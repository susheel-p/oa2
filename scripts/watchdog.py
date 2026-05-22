"""Daemon liveness watchdog — monitors heartbeat and alerts if stale.

Runs as a scheduled task (cron/Task Scheduler every 5 min) or as a loop.
Sends Telegram alerts if the daemon heartbeat is stale for > WATCHDOG_STALE_SECONDS.

Setup:
    1. Ensure market_monitor.py is set up to run as a daemon.
    2. Add env vars to .env:
         WATCHDOG_STALE_SECONDS=300      # 5 min
         WATCHDOG_MAX_ALERTS=5           # cap
         WATCHDOG_HEALTH_INTERVAL=3600   # send "all clear" every N successful checks (0=disabled)
    3. Schedule this script to run every 5 min:
       - Linux crontab: */5 * * * * cd /path/to/oa2-new && python scripts/watchdog.py
       - Windows Task Scheduler: Run scripts\\watchdog.py every 5 minutes
    4. Or run as a loop: python scripts/watchdog.py --loop

The watchdog maintains state in logs/watchdog_state.json to avoid spam:
- Tracks alert count and last alert time
- Resets when heartbeat recovers
- Caps at WATCHDOG_MAX_ALERTS before silencing
- Sends recovery notifications when daemon comes back online
- Writes own heartbeat for outer monitoring
- Sends periodic "all clear" confirmations (if enabled)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Add parent to path so we can import scripts.telegram_notify
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts import telegram_notify


TRADINGBOT_HOME = Path(os.getenv("TRADINGBOT_HOME", Path(__file__).parent.parent))
HEARTBEAT_FILE = TRADINGBOT_HOME / "logs" / "daemon_heartbeat.txt"
WATCHDOG_HEARTBEAT_FILE = TRADINGBOT_HOME / "logs" / "watchdog_heartbeat.txt"
STATE_FILE = TRADINGBOT_HOME / "logs" / "watchdog_state.json"
LOG_FILE = TRADINGBOT_HOME / "logs" / "watchdog.log"

STALE_SECONDS = int(os.getenv("WATCHDOG_STALE_SECONDS", "300"))
MAX_ALERTS = int(os.getenv("WATCHDOG_MAX_ALERTS", "5"))
LOOP_INTERVAL = int(os.getenv("WATCHDOG_INTERVAL_SECONDS", "300"))
HEALTH_INTERVAL = int(os.getenv("WATCHDOG_HEALTH_INTERVAL", "3600"))


def _log(msg: str) -> None:
    """Log to both stdout and file."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat()
    log_line = f"[{ts}] {msg}"
    print(log_line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(log_line + "\n")
    except Exception as e:
        print(f"Warning: could not write to {LOG_FILE}: {e}")


def _load_state() -> dict:
    """Load watchdog state. Defaults to clean slate."""
    if not STATE_FILE.exists():
        return {
            "alerts_sent": 0,
            "last_alert_time": None,
            "last_heartbeat_time": None,
            "last_health_check": None,
            "last_8am_alert": None,
            "last_noon_check": None,
            "was_stale": False,
        }
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception as e:
        _log(f"Warning: watchdog state corrupted, resetting: {e}")
        return {
            "alerts_sent": 0,
            "last_alert_time": None,
            "last_heartbeat_time": None,
            "last_health_check": None,
            "last_8am_alert": None,
            "last_noon_check": None,
            "was_stale": False,
        }


def _save_state(state: dict) -> None:
    """Save watchdog state."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"Warning: could not save watchdog state: {e}")


def _write_watchdog_heartbeat() -> None:
    """Write watchdog's own heartbeat for outer monitoring."""
    WATCHDOG_HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(WATCHDOG_HEARTBEAT_FILE, "w") as f:
            f.write(str(time.time()))
    except Exception as e:
        _log(f"Warning: could not write watchdog heartbeat: {e}")


def _get_heartbeat_age() -> float | None:
    """Get age of heartbeat file in seconds, or None if missing."""
    if not HEARTBEAT_FILE.exists():
        return None
    try:
        mtime = HEARTBEAT_FILE.stat().st_mtime
        return time.time() - mtime
    except Exception:
        return None


def _count_signals_generated() -> int:
    """Count signals generated from today's daemon log."""
    daemon_log = TRADINGBOT_HOME / "logs" / "daemon.log"
    if not daemon_log.exists():
        return 0
    try:
        with open(daemon_log) as f:
            content = f.read()
            return content.count("signal") + content.count("Signal") + content.count("SIGNAL")
    except Exception:
        return 0


def _check_for_errors() -> int:
    """Count errors in today's daemon log."""
    daemon_log = TRADINGBOT_HOME / "logs" / "daemon.log"
    if not daemon_log.exists():
        return 0
    try:
        with open(daemon_log) as f:
            content = f.read()
            return content.count("ERROR") + content.count("Error") + content.count("error")
    except Exception:
        return 0


def _should_send_8am_alert() -> bool:
    """Check if we should send 8am health report (once per day)."""
    state = _load_state()
    last_8am = state.get("last_8am_alert")
    now = datetime.now()

    # Check if we're near 8am (within 1 hour) and haven't sent yet today
    if now.hour == 8:
        if last_8am is None or not last_8am.startswith(now.strftime("%Y-%m-%d")):
            return True
    return False


def _should_send_noon_check() -> bool:
    """Check if we should send noon issue check (once per day)."""
    state = _load_state()
    last_noon = state.get("last_noon_check")
    now = datetime.now()

    # Check if we're near noon (hour 12, within 1 hour) and haven't sent yet today
    if now.hour == 12:
        if last_noon is None or not last_noon.startswith(now.strftime("%Y-%m-%d")):
            return True
    return False


def _send_8am_alert(state: dict) -> None:
    """Send 8am system health report."""
    age = _get_heartbeat_age()
    daemon_status = "running" if age is not None and age <= STALE_SECONDS else "stale"
    signals = _count_signals_generated()

    if telegram_notify.notify_system_health(
        daemon_status=daemon_status,
        signals_generated=signals,
        heartbeat_age_seconds=int(age) if age is not None else None,
    ):
        state["last_8am_alert"] = datetime.now().isoformat()
        _log("[OK] 8am health alert sent")
    else:
        _log("[WARN] 8am health alert failed to send")


def _send_noon_check(state: dict) -> None:
    """Send noon system issue check."""
    age = _get_heartbeat_age()
    is_stale = age is None or age > STALE_SECONDS
    error_count = _check_for_errors()
    last_activity = datetime.fromtimestamp(time.time() - (age or 0)).isoformat() if age else None

    if telegram_notify.notify_system_issues(
        is_stale=is_stale,
        error_count=error_count,
        last_activity=last_activity,
    ):
        state["last_noon_check"] = datetime.now().isoformat()
        if not is_stale and error_count == 0:
            _log("[OK] Noon check: all clear")
        else:
            _log("[OK] Noon alert sent")
    else:
        _log("[WARN] Noon check failed to send")


def check_daemon() -> bool:
    """
    Check daemon liveness. Send alerts if stale, recovery if it comes back.
    Returns True if daemon is healthy, False if stale.
    """
    state = _load_state()
    age = _get_heartbeat_age()
    now = time.time()

    # Check for 8am health alert
    if _should_send_8am_alert():
        _send_8am_alert(state)

    # Check for noon issue check
    if _should_send_noon_check():
        _send_noon_check(state)

    # Write watchdog's own heartbeat
    _write_watchdog_heartbeat()

    # Daemon is healthy
    if age is not None and age <= STALE_SECONDS:
        was_stale = state.get("was_stale", False)
        alerts_sent = state.get("alerts_sent", 0)

        # Send recovery notification if daemon just came back
        if was_stale and alerts_sent > 0:
            recovery_msg = (
                f"[RECOVERED] Daemon is healthy again\n"
                f"Heartbeat age: {age:.0f}s\n"
                f"Sent {alerts_sent} alerts before recovery"
            )
            if telegram_notify.send(recovery_msg):
                _log(f"[OK] Recovery notification sent (was {alerts_sent} alerts)")
            else:
                _log(f"[WARN] Recovery notification failed to send")

        # Send periodic health confirmation if enabled
        last_health = state.get("last_health_check")
        if HEALTH_INTERVAL > 0 and (last_health is None or now - last_health >= HEALTH_INTERVAL):
            health_msg = (
                "✅ Daemon operating normally\n"
                f"Heartbeat age: {age:.0f}s\n"
                f"Watchdog: {LOOP_INTERVAL}s interval"
            )
            if telegram_notify.send(health_msg):
                state["last_health_check"] = now
                _log(f"[OK] Health confirmation sent")
            else:
                _log(f"[WARN] Health confirmation failed to send")

        # Reset stale state, preserve 8am/noon alert times
        state = {
            "alerts_sent": 0,
            "last_alert_time": None,
            "last_heartbeat_time": now,
            "last_health_check": state.get("last_health_check"),
            "last_8am_alert": state.get("last_8am_alert"),
            "last_noon_check": state.get("last_noon_check"),
            "was_stale": False,
        }
        _save_state(state)
        return True

    # Daemon is stale or missing
    alerts_sent = state.get("alerts_sent", 0)

    # Check if we've already hit the alert cap
    if alerts_sent >= MAX_ALERTS:
        _log(f"[WARN] Daemon stale (age {age}s if exists) — max alerts ({MAX_ALERTS}) reached, silencing")
        state["was_stale"] = True
        _save_state(state)
        return False

    # Compose alert message with clear severity indicators
    if age is None:
        msg = (
            "🚨 ALERT: Daemon not started yet\n"
            f"Heartbeat file missing: {HEARTBEAT_FILE}\n\n"
            f"Action: Start the daemon now\n"
            f"python scripts/market_monitor.py"
        )
    else:
        msg = (
            f"🚨 ALERT: Daemon is STALE (no heartbeat for {age:.0f}s)\n"
            f"Expected update every 60s during market hours\n"
            f"Stale threshold: {STALE_SECONDS}s\n\n"
            f"Immediate Actions:\n"
            f"1. Check logs: tail -f logs/daemon.log\n"
            f"2. Restart: python scripts/market_monitor.py\n"
            f"3. Verify: no duplicate daemon instances running"
        )

    # Send alert — only increment counter if send succeeds
    if telegram_notify.send(msg):
        alerts_sent_new = alerts_sent + 1
        _log(f"[OK] Alert {alerts_sent_new}/{MAX_ALERTS} sent: daemon stale (age {age}s if exists)")
        state["alerts_sent"] = alerts_sent_new
        state["last_alert_time"] = datetime.now().isoformat()
        state["last_heartbeat_time"] = time.time() if age is not None else None
        state["was_stale"] = True
        # Preserve 8am and noon alert times
        state.setdefault("last_8am_alert", state.get("last_8am_alert"))
        state.setdefault("last_noon_check", state.get("last_noon_check"))
        _save_state(state)
    else:
        _log(f"[WARN] Telegram send failed (alert not counted); daemon is stale (age {age}s if exists)")
        # Don't increment counter or update state — retry next cycle

    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor daemon heartbeat and alert if stale"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help=f"Run as loop instead of one-shot (checks every {LOOP_INTERVAL}s)",
    )
    args = parser.parse_args()

    if args.loop:
        _log(
            f"Watchdog loop started (interval {LOOP_INTERVAL}s, "
            f"stale threshold {STALE_SECONDS}s, max alerts {MAX_ALERTS})"
        )
        while True:
            try:
                check_daemon()
            except Exception as e:
                _log(f"[ERROR] Watchdog check failed: {e}")
            time.sleep(LOOP_INTERVAL)
    else:
        check_daemon()


if __name__ == "__main__":
    main()
