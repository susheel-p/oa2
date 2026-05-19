"""Daemon liveness watchdog — monitors heartbeat and alerts if stale.

Runs as a scheduled task (cron/Task Scheduler every 5 min) or as a loop.
Sends Telegram alerts if the daemon heartbeat is stale for > WATCHDOG_STALE_SECONDS.

Setup:
    1. Ensure market_monitor.py is set up to run as a daemon.
    2. Add env vars to .env:
         WATCHDOG_STALE_SECONDS=300      # 5 min
         WATCHDOG_MAX_ALERTS=5           # cap
    3. Schedule this script to run every 5 min:
       - Linux crontab: */5 * * * * cd /path/to/oa2-new && python scripts/watchdog.py
       - Windows Task Scheduler: Run scripts\\watchdog.py every 5 minutes
    4. Or run as a loop: python scripts/watchdog.py --loop

The watchdog maintains state in logs/watchdog_state.json to avoid spam:
- Tracks alert count and last alert time
- Resets when heartbeat recovers
- Caps at WATCHDOG_MAX_ALERTS before silencing
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


OA2_HOME = Path(os.getenv("OA2_HOME", Path(__file__).parent.parent))
HEARTBEAT_FILE = OA2_HOME / "logs" / "daemon_heartbeat.txt"
STATE_FILE = OA2_HOME / "logs" / "watchdog_state.json"

STALE_SECONDS = int(os.getenv("WATCHDOG_STALE_SECONDS", "300"))
MAX_ALERTS = int(os.getenv("WATCHDOG_MAX_ALERTS", "5"))
LOOP_INTERVAL = int(os.getenv("WATCHDOG_INTERVAL_SECONDS", "300"))


def _load_state() -> dict:
    """Load watchdog state. Defaults to clean slate."""
    if not STATE_FILE.exists():
        return {"alerts_sent": 0, "last_alert_time": None, "last_heartbeat_time": None}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"alerts_sent": 0, "last_alert_time": None, "last_heartbeat_time": None}


def _save_state(state: dict) -> None:
    """Save watchdog state."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"Warning: could not save watchdog state: {e}")


def _get_heartbeat_age() -> float | None:
    """Get age of heartbeat file in seconds, or None if missing."""
    if not HEARTBEAT_FILE.exists():
        return None
    try:
        mtime = HEARTBEAT_FILE.stat().st_mtime
        return time.time() - mtime
    except Exception:
        return None


def check_daemon() -> bool:
    """
    Check daemon liveness. Send alert if stale.
    Returns True if daemon is healthy, False if stale and alert sent (or should send).
    """
    state = _load_state()
    age = _get_heartbeat_age()

    # Daemon is healthy — reset state
    if age is not None and age <= STALE_SECONDS:
        if state.get("alerts_sent", 0) > 0:
            print(f"[OK] Daemon recovered (heartbeat age {age:.0f}s)")
        state = {"alerts_sent": 0, "last_alert_time": None, "last_heartbeat_time": time.time()}
        _save_state(state)
        return True

    # Daemon is stale
    alerts_sent = state.get("alerts_sent", 0)
    if alerts_sent >= MAX_ALERTS:
        print(f"[WARN] Daemon stale (age {age:.0f}s) — max alerts ({MAX_ALERTS}) reached, silencing")
        return False

    # Send alert
    if age is None:
        msg = (
            "[ALERT] oa2 daemon not started yet\n"
            f"Heartbeat file missing: {HEARTBEAT_FILE}\n"
            f"Start daemon: python scripts/market_monitor.py"
        )
    else:
        msg = (
            f"[ALERT] oa2 daemon stale (no update for {age:.0f}s)\n"
            f"Expected update every 60s during market hours\n"
            f"Check: tail -f logs/daemon.log\n"
            f"Restart: python scripts/market_monitor.py"
        )

    if telegram_notify.send(msg):
        print(f"[OK] Alert {alerts_sent + 1}/{MAX_ALERTS} sent: daemon stale (age {age}s)")
    else:
        print(f"[WARN] Telegram alert failed, but daemon is stale (age {age}s)")

    state["alerts_sent"] = alerts_sent + 1
    state["last_alert_time"] = datetime.now().isoformat()
    state["last_heartbeat_time"] = time.time() if age is not None else None
    _save_state(state)
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
        print(f"Watchdog loop started (interval {LOOP_INTERVAL}s, stale threshold {STALE_SECONDS}s)")
        while True:
            check_daemon()
            time.sleep(LOOP_INTERVAL)
    else:
        check_daemon()


if __name__ == "__main__":
    main()
