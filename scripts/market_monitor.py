"""oa2 market monitor daemon.

Fully automated trading system with scheduled operations:
  - 8:00 AM:  Premarket scan (45-min window using premarket prices)
  - 8:45 AM:  Premarket report (today's signal overview)
  - 9:00 AM:  Full-scan (debaters → consensus → sizing → approved positions, MUST finish by 9:30)
  - 9:30 AM–4:00 PM: Entry-only + exit-only every minute (ready with approved positions)
  - 4:15 PM:  Postmarket report (day's results & analysis)

Runs continuously as a daemon (auto-restart on crash, runs across midnight).

Usage:
    python scripts/market_monitor.py                # daemon mode (continuous)
    python scripts/market_monitor.py --dry-run      # test mode, no file writes
    python scripts/market_monitor.py --once         # single cycle (testing)

Daemon Setup (Windows Task Scheduler):
    schtasks /create /tn "oa2-market-monitor" /tr "python C:\\path\\to\\oa2-new\\scripts\\market_monitor.py" /sc onstart /ru SYSTEM

Daemon Setup (Linux/Mac cron):
    @reboot cd /path/to/oa2-new && nohup python scripts/market_monitor.py > logs/daemon.log 2>&1 &
"""

from __future__ import annotations

import argparse
import datetime
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    from scripts import telegram_notify
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts import telegram_notify

try:
    from tradingbot.core.market_hours import (
        is_market_day,
        is_market_open,
        time_until_market_open,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from tradingbot.core.market_hours import (
        is_market_day,
        is_market_open,
        time_until_market_open,
    )

ET = ZoneInfo("America/New_York")

REPORTS_DIR = Path(os.getenv("REPORTS_DIR", "reports"))

_lock_socket: socket.socket | None = None


def _acquire_instance_lock(port: int = 18500) -> bool:
    """Acquire a socket bind lock on localhost to prevent duplicate daemons."""
    global _lock_socket
    try:
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform != "win32":
            _lock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _lock_socket.bind(("127.0.0.1", port))
        _lock_socket.listen(1)
        return True
    except socket.error:
        _lock_socket = None
        return False


def _now_et() -> datetime.datetime:
    return datetime.datetime.now(ET)


def _get_daemon_log_path() -> Path:
    """Get daemon log file path."""
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    return log_dir / "daemon.log"


def _log(msg: str, log_file: Path | None = None) -> None:
    """Log message to console. supervisord captures stdout to daemon.log automatically."""
    ts = _now_et().strftime("%Y-%m-%dT%H:%M:%S%z")
    output = f"[{ts}] {msg}"
    print(output, flush=True)
    # Note: log_file parameter unused since supervisord redirects stdout to daemon.log


def _update_heartbeat(heartbeat_file: Path) -> bool:
    """Update heartbeat file. Returns True on success."""
    try:
        heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        heartbeat_file.write_text(str(time.time()), encoding="utf-8")
        return True
    except Exception as e:
        _log(f"Warning: heartbeat update failed: {e}")
        return False


def _alert_telegram(label: str, msg: str) -> None:
    """Send alert to Telegram. Logs but doesn't block on failure."""
    try:
        if telegram_notify.send(f"[{label}] {msg}"):
            _log(f"[TELEGRAM] Alert sent: {label}")
        else:
            _log(f"[TELEGRAM] Send failed (credentials missing?): {label}")
    except Exception as e:
        _log(f"[TELEGRAM] Send error: {e}")




def _time_until_next_event(target_hour: int, target_minute: int) -> float:
    """Return seconds until next occurrence of target time (in ET)."""
    now = _now_et()
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

    # If target time has passed today, aim for tomorrow
    if now >= target:
        target += datetime.timedelta(days=1)

    return (target - now).total_seconds()


_TIMEOUTS = {
    "FULL-SCAN": 1800,           # 30 min — debaters + chain fetch for 22 tickers
    "PREMARKET-SCAN": 2700,      # 45 min — same pipeline, runs 8:00-8:45 AM with buffer
    "PREMARKET-REPORT": 600,     # 10 min
    "POSTMARKET-REPORT": 600,    # 10 min
    "EXIT-ONLY": 120,            # 2 min — must fit inside the 60s scheduler tick
    "EOD-OUTCOMES": 600,         # 10 min
    "DAILY-LEARN": 600,          # 10 min
}


def _tail(text: str | None, n_chars: int = 2000) -> str:
    if not text:
        return "<empty>"
    text = text.rstrip()
    if len(text) <= n_chars:
        return text
    return "...<truncated>...\n" + text[-n_chars:]


def _run_command(cmd: list[str], label: str, log_file: Path | None = None) -> bool:
    """Run a shell command and log the result. Send Telegram alert on critical failures."""
    timeout_s = _TIMEOUTS.get(label, 300)
    t_start = time.monotonic()
    _log(f"[{label}] Starting (timeout {timeout_s}s) cmd={cmd}", log_file)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        elapsed = time.monotonic() - t_start
        if result.returncode == 0:
            _log(f"[{label}] SUCCESS in {elapsed:.1f}s", log_file)
            return True
        _log(f"[{label}] FAILED exit={result.returncode} in {elapsed:.1f}s", log_file)
        _log(f"[{label}] stdout tail:\n{_tail(result.stdout)}", log_file)
        _log(f"[{label}] stderr tail:\n{_tail(result.stderr)}", log_file)
        # Alert on critical operation failures
        if label in ("FULL-SCAN", "POSTMARKET-REPORT"):
            _alert_telegram(label, f"Operation failed (exit {result.returncode})\nCheck logs/daemon.log")
        return False
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - t_start
        _log(f"[{label}] TIMEOUT after {elapsed:.1f}s (limit {timeout_s}s)", log_file)
        _log(f"[{label}] stdout tail:\n{_tail(exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout)}", log_file)
        _log(f"[{label}] stderr tail:\n{_tail(exc.stderr.decode() if isinstance(exc.stderr, bytes) else exc.stderr)}", log_file)
        # Alert on critical timeouts
        if label in ("FULL-SCAN", "POSTMARKET-REPORT"):
            _alert_telegram(label, f"Operation TIMEOUT after {elapsed:.0f}s (limit {timeout_s}s)\nDaemon may be hung or resource-constrained")
        return False
    except Exception as exc:
        elapsed = time.monotonic() - t_start
        _log(f"[{label}] ERROR after {elapsed:.1f}s: {exc!r}", log_file)
        _log(f"[{label}] traceback:\n{traceback.format_exc()}", log_file)
        # Alert on critical errors
        if label in ("FULL-SCAN", "POSTMARKET-REPORT"):
            _alert_telegram(label, f"Operation ERROR after {elapsed:.0f}s: {exc!r}")
        return False


class MarketMonitor:
    """Daemon that schedules full-scan, exit-only, and report generation."""

    def __init__(self, dry_run: bool = False, once: bool = False, daemon_mode: bool = False, scan_on_start: bool = False):
        self.dry_run = dry_run
        self.once = once
        self.daemon_mode = daemon_mode
        self.scan_on_start = scan_on_start
        self.log_file = _get_daemon_log_path() if daemon_mode else None
        self.full_scan_done_today = False
        self.premarket_scan_done_today = False
        self.premarket_done_today = False
        self.postmarket_done_today = False
        self.learning_loop_done_today = False
        self.weekly_analysis_done_today = False
        self.stop_event = threading.Event()
        self.lock = threading.Lock()

    def _build_cmd(self, mode: str) -> list[str]:
        """Build the paper_trade.py command."""
        cmd = [sys.executable, "scripts/paper_trade.py", f"--{mode}"]
        if self.dry_run:
            cmd.append("--dry-run")
        return cmd

    def _run_full_scan(self) -> None:
        """Run full-scan if we haven't already today."""
        with self.lock:
            if self.full_scan_done_today:
                return

        now = _now_et()
        _log(
            f"Full-scan trigger at {now.strftime('%H:%M:%S')} "
            f"(target 09:45:00)",
            self.log_file
        )

        cmd = self._build_cmd("full-scan")
        if _run_command(cmd, "FULL-SCAN", self.log_file):
            with self.lock:
                self.full_scan_done_today = True
                _log("Full-scan flagged as done for today", self.log_file)

    def _run_exit_only(self) -> None:
        """Run exit-only if market is open."""
        if not is_market_open():
            return

        cmd = self._build_cmd("exit-only")
        _run_command(cmd, "EXIT-ONLY", self.log_file)

    def _run_entry_only(self) -> None:
        """Run entry-only if market is open (scan for new positions)."""
        if not is_market_open():
            return

        cmd = self._build_cmd("entry-only")
        _run_command(cmd, "ENTRY-ONLY", self.log_file)

    def _run_premarket_scan(self) -> None:
        """Run premarket signal scan at 8:00 AM using live premarket prices."""
        with self.lock:
            if self.premarket_scan_done_today:
                return

        now = _now_et()
        _log(f"Premarket scan trigger at {now.strftime('%H:%M:%S')} (target 08:30:00)")

        cmd = [sys.executable, "scripts/paper_trade.py", "--premarket-scan"]
        if self.dry_run:
            cmd.append("--dry-run")
        if _run_command(cmd, "PREMARKET-SCAN"):
            with self.lock:
                self.premarket_scan_done_today = True
                _log("Premarket scan flagged as done for today")

    def _run_premarket_report(self) -> None:
        """Generate premarket report at 8:30 AM."""
        with self.lock:
            if self.premarket_done_today:
                return

        now = _now_et()
        _log(f"Premarket report trigger at {now.strftime('%H:%M:%S')} (target 09:00:00)", self.log_file)

        cmd = [sys.executable, "scripts/report.py", "--premarket"]
        if _run_command(cmd, "PREMARKET-REPORT", self.log_file):
            with self.lock:
                self.premarket_done_today = True
                _log("Premarket report generated", self.log_file)

    def _run_postmarket_report(self) -> None:
        """Generate postmarket report at 4:15 PM."""
        with self.lock:
            if self.postmarket_done_today:
                return

        now = _now_et()
        _log(f"Postmarket report trigger at {now.strftime('%H:%M:%S')} (target 16:15:00)", self.log_file)

        cmd = [sys.executable, "scripts/report.py", "--postmarket"]
        if _run_command(cmd, "POSTMARKET-REPORT", self.log_file):
            with self.lock:
                self.postmarket_done_today = True
                _log("Postmarket report generated", self.log_file)

    def _run_learning_loop(self) -> None:
        """Run EOD outcomes resolver and daily learner (updates RAG KnowledgeBase + Blacklist)."""
        with self.lock:
            if self.learning_loop_done_today:
                return

        now = _now_et()
        _log(f"Nightly learning loop trigger at {now.strftime('%H:%M:%S')} (target 17:00:00)", self.log_file)

        # 1. Run outcomes resolver
        cmd_outcomes = [sys.executable, "scripts/eod_outcomes.py"]
        if self.dry_run:
            cmd_outcomes.append("--dry-run")
        success_outcomes = _run_command(cmd_outcomes, "EOD-OUTCOMES", self.log_file)

        # 2. Run daily learn
        cmd_learn = [sys.executable, "scripts/daily_learn.py"]
        if self.dry_run:
            cmd_learn.append("--dry-run")
        success_learn = _run_command(cmd_learn, "DAILY-LEARN", self.log_file)

        if success_outcomes and success_learn:
            with self.lock:
                self.learning_loop_done_today = True
                _log("Nightly learning loop completed successfully", self.log_file)

    def _run_weekly_analysis(self) -> None:
        """Run weekly backtest analysis on Sunday at 5:30 PM (after daily learn)."""
        with self.lock:
            if self.weekly_analysis_done_today:
                return

        now = _now_et()
        _log(f"Weekly analysis trigger at {now.strftime('%H:%M:%S')} (Sunday 17:30)", self.log_file)

        cmd = [sys.executable, "scripts/analyze_weekly.py"]
        if _run_command(cmd, "WEEKLY-ANALYSIS", self.log_file):
            with self.lock:
                self.weekly_analysis_done_today = True
                _log("Weekly analysis completed", self.log_file)

    def _reset_daily_flags(self) -> None:
        """Reset daily flags at midnight."""
        with self.lock:
            self.full_scan_done_today = False
            self.premarket_scan_done_today = False
            self.premarket_done_today = False
            self.postmarket_done_today = False
            self.learning_loop_done_today = False
            self.weekly_analysis_done_today = False

    def _schedule_loop(self) -> None:
        """Main scheduling loop."""
        _log("Market monitor started", self.log_file)

        # Setup heartbeat file for watchdog monitoring
        heartbeat_file = Path(__file__).parent.parent / "logs" / "daemon_heartbeat.txt"
        heartbeat_file.parent.mkdir(parents=True, exist_ok=True)

        last_daily_reset = _now_et().date()

        if self.scan_on_start and not self.once:
            _log("Scan-on-start: running full-scan immediately", self.log_file)
            self._run_full_scan()

        # Catch-up: if daemon started after a scheduled report/learning time on a market
        # day, run the missed process once instead of waiting until tomorrow.
        if not self.once and is_market_day():
            now = _now_et()
            today = now.date()
            premarket_scan_target = now.replace(hour=8, minute=0, second=0, microsecond=0)
            premarket_target = now.replace(hour=8, minute=45, second=0, microsecond=0)
            fullscan_target = now.replace(hour=9, minute=0, second=0, microsecond=0)
            postmarket_target = now.replace(hour=16, minute=15, second=0, microsecond=0)
            learning_target = now.replace(hour=17, minute=0, second=0, microsecond=0)

            log_dir = Path(__file__).parent.parent / "logs"
            premarket_scan_path = log_dir / f"paper_trade_{today.isoformat()}_premarket.jsonl"
            if now >= premarket_scan_target and not premarket_scan_path.exists():
                _log("Catch-up: premarket scan missed; running now")
                self._run_premarket_scan()

            premarket_path = REPORTS_DIR / today.isoformat() / "premarket.md"
            if now >= premarket_target and not premarket_path.exists():
                _log("Catch-up: premarket report missed; running now", self.log_file)
                self._run_premarket_report()

            postmarket_path = REPORTS_DIR / today.isoformat() / "postmarket.md"
            if now >= postmarket_target and not postmarket_path.exists():
                _log("Catch-up: postmarket report missed; running now", self.log_file)
                self._run_postmarket_report()

            insights_path = REPORTS_DIR / today.isoformat() / "insights.md"
            if now >= learning_target and not insights_path.exists():
                _log("Catch-up: learning loop missed; running now", self.log_file)
                self._run_learning_loop()

        while not self.stop_event.is_set():
            now = _now_et()

            # Reset daily flags at midnight
            if now.date() > last_daily_reset:
                _log("Daily reset (midnight passed)", self.log_file)
                self._reset_daily_flags()
                last_daily_reset = now.date()

            # Catch-up for missed scheduled events on market days (if we wake up past the target time)
            if is_market_day(now):
                # Catch-up: premarket scan (target 8:00 AM) — run if past 8:00 and before premarket report (8:45)
                if not self.premarket_scan_done_today and now.hour == 8 and now.minute >= 0:
                    _log("Catch-up: running premarket scan (missed 8:00 AM window)", self.log_file)
                    self._run_premarket_scan()
                elif not self.premarket_scan_done_today and now.hour == 8 and now.minute < 45:
                    _log("Catch-up: running premarket scan (missed 8:00 AM window)", self.log_file)
                    self._run_premarket_scan()

                # Catch-up: premarket report (target 8:45 AM) — run if past 8:45 and before full-scan (9:45)
                if not self.premarket_done_today and now.hour == 8 and now.minute >= 45:
                    _log("Catch-up: running premarket report (missed 8:45 AM window)", self.log_file)
                    self._run_premarket_report()
                elif not self.premarket_done_today and now.hour == 9 and now.minute < 45:
                    _log("Catch-up: running premarket report (missed 8:45 AM window)", self.log_file)
                    self._run_premarket_report()

                # Catch-up: full-scan (target 9:45 AM) — run if past 9:45 and before market close
                if not self.full_scan_done_today and (now.hour > 9 or (now.hour == 9 and now.minute >= 45)) and now.hour < 16:
                    _log("Catch-up: running full-scan (missed 9:45 AM window)", self.log_file)
                    self._run_full_scan()

                # Catch-up: postmarket report (target 4:15 PM) — run if past 4:15 PM and before learning loop (5:00)
                if not self.postmarket_done_today and now.hour >= 16 and now.hour < 17:
                    if now.hour == 16 and now.minute >= 15:
                        _log("Catch-up: running postmarket report (missed 4:15 PM window)", self.log_file)
                        self._run_postmarket_report()

                # Catch-up: learning loop (target 5:00 PM) — run if past 5:00 PM and before midnight
                if not self.learning_loop_done_today and now.hour >= 17 and now.hour < 23:
                    _log("Catch-up: running learning loop (missed 5:00 PM window)", self.log_file)
                    self._run_learning_loop()

            # Check for weekly analysis at 5:30 PM on Sunday (before other weekly checks)
            if (
                now.weekday() == 6  # Sunday
                and now.hour >= 17
                and now.minute >= 30
                and not self.weekly_analysis_done_today
            ):
                _log("Running weekly analysis (Sunday 5:30 PM)", self.log_file)
                self._run_weekly_analysis()

            # Exact time matching (as backup for systems that can check at precise times)
            # Run premarket scan at 8:00 AM (fresh signals using live premarket prices, 45-min window)
            if (
                now.hour == 8
                and now.minute == 0
                and is_market_day(now)
            ):
                self._run_premarket_scan()

            # Generate premarket report at 8:45 AM (reads premarket scan results)
            if (
                now.hour == 8
                and now.minute == 45
                and is_market_day(now)
            ):
                self._run_premarket_report()

            # Full-scan at 9:45 AM (live options chains available after open)
            if (
                now.hour == 9
                and now.minute == 45
                and is_market_day(now)
            ):
                self._run_full_scan()

            # Run exit-only and entry-only every minute during market hours
            if is_market_open(now):
                self._run_exit_only()
                self._run_entry_only()

            # Check for postmarket report at 4:15 PM
            if (
                now.hour == 16
                and now.minute == 15
                and is_market_day(now)
            ):
                self._run_postmarket_report()

            # Check for learning loop at 5:00 PM (17:00)
            if (
                now.hour == 17
                and now.minute == 0
                and is_market_day(now)
            ):
                self._run_learning_loop()

            # Check for weekly analysis at 5:30 PM on Sunday
            if (
                now.weekday() == 6  # Sunday
                and now.hour == 17
                and now.minute == 30
            ):
                self._run_weekly_analysis()

            # Write heartbeat for watchdog monitoring (before checking self.once)
            _update_heartbeat(heartbeat_file)

            if self.once:
                break

            # Smart sleep: if market is closed, sleep longer until market opens
            if not is_market_open(now) and not is_market_day(now):
                # Market is closed today (weekend/holiday) — sleep until next market open
                sleep_until_open = time_until_market_open(now)
                _log(f"Market closed (holiday/weekend). Sleeping {sleep_until_open:.0f}s until next open.", self.log_file)
                sleep_time = min(sleep_until_open, 3600)  # Cap at 1 hour to avoid missing recovery signals
            elif not is_market_open(now):
                # Market will open today — sleep until market opens
                sleep_until_open = time_until_market_open(now)
                _log(f"Market closed for now. Sleeping {sleep_until_open:.0f}s until market opens.", self.log_file)
                sleep_time = min(sleep_until_open, 3600)
            else:
                # Market is open — sleep until next minute boundary
                sleep_time = 60 - now.second
                if sleep_time <= 0:
                    sleep_time = 60

            try:
                self.stop_event.wait(sleep_time)
            except KeyboardInterrupt:
                _log("Interrupted by user")
                break

    def run(self) -> None:
        """Run the monitor daemon."""
        try:
            self._schedule_loop()
        except KeyboardInterrupt:
            _log("Shutting down...", self.log_file)
        except Exception as exc:
            _log(f"Unexpected error: {exc}", self.log_file)
            if self.daemon_mode:
                _log(f"Exception details: {traceback.format_exc()}", self.log_file)
            raise

        _log("Market monitor stopped", self.log_file)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="oa2 market monitor daemon — fully automated trading",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
AUTOMATED SCHEDULING (select one):

Windows Task Scheduler:
  schtasks /create /tn "oa2-market-monitor" \\
    /tr "python C:\\path\\to\\oa2-new\\scripts\\market_monitor.py" \\
    /sc onstart /ru SYSTEM

Linux/Mac cron (@reboot):
  @reboot cd /path/to/oa2-new && nohup python scripts/market_monitor.py > logs/daemon.log 2>&1 &

Manual daemon (foreground):
  python scripts/market_monitor.py

Test mode (single cycle):
  python scripts/market_monitor.py --once

Test with no file writes:
  python scripts/market_monitor.py --dry-run

Check daemon logs:
  tail -f logs/daemon.log
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test mode: no file writes, full console logging",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one cycle and exit (for testing)",
    )
    parser.add_argument(
        "--scan-on-start",
        action="store_true",
        help="Run a full-scan immediately on startup, then continue scheduling",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Print setup instructions for Windows Task Scheduler",
    )
    args = parser.parse_args()

    if args.setup:
        _print_setup_instructions()
        return

    # Detect daemon mode (no --once and not --dry-run)
    daemon_mode = not args.once and not args.dry_run

    if daemon_mode:
        lock_port = int(os.getenv("OA2_MONITOR_LOCK_PORT", "18500"))
        log_file = _get_daemon_log_path()
        if not _acquire_instance_lock(lock_port):
            _log(f"FATAL: Another instance of market_monitor is already running (locked on port {lock_port}). Exiting.", log_file)
            sys.exit(1)

    monitor = MarketMonitor(dry_run=args.dry_run, once=args.once, daemon_mode=daemon_mode, scan_on_start=args.scan_on_start)

    if daemon_mode:
        _log(f"Starting daemon mode (logs: {log_file})")

    try:
        monitor.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        _log(f"Fatal error: {e}")
        if daemon_mode:
            log_file = _get_daemon_log_path()
            _log(f"Full traceback:\n{traceback.format_exc()}", log_file)
        raise


def _print_setup_instructions() -> None:
    """Print setup instructions for daemon auto-start."""
    import platform
    from pathlib import Path

    print("""
========================================================================
  oa2 MARKET MONITOR — DAEMON SETUP INSTRUCTIONS
========================================================================

This system runs FULLY AUTOMATED:
  * 8:00 AM:  Premarket scan (signal analysis)
  * 8:45 AM:  Premarket report (today's signal overview)
  * 9:00 AM:  Full scan (approved trades READY before market opens)
  * 9:30 AM:  Market opens — Entry-only + exit-only every minute
  * 4:15 PM:  Postmarket report (day's results)

Once started, the daemon runs continuously across midnight.

========================================================================

SETUP INSTRUCTIONS:

""")

    if platform.system() == "Windows":
        script_path = Path(__file__).resolve()
        daemon_log = Path(__file__).parent.parent / "logs" / "daemon.log"
        print(f"""
WINDOWS - Task Scheduler (Recommended):

1. Open Task Scheduler (press Win+R, type "taskschd.msc")

2. Click "Create Basic Task..." on the right panel

3. Fill in:
   Name: "oa2-market-monitor"
   Description: "Automated trading system for oa2"

4. Trigger: Select "At startup" and click Next

5. Action: Select "Start a program" and click Next

6. Program/script:
   python

7. Arguments:
   {script_path}

8. Click "Finish"

9. Right-click the task -> Properties -> Check "Run whether user is logged in or not"

10. Restart your computer, daemon will start automatically

========================================================================

VERIFY DAEMON IS RUNNING:

Check logs:
  powershell -Command "Get-Content '{daemon_log}' -Tail 20 -Wait"

Kill daemon (if needed):
  tasklist | findstr python
  taskkill /pid <PID> /f

========================================================================
""")
    else:
        script_path = Path(__file__).resolve()
        base_dir = script_path.parent.parent
        daemon_log = base_dir / "logs" / "daemon.log"
        print(f"""
LINUX/MAC - Cron (@reboot):

1. Open crontab:
   crontab -e

2. Add this line (all one line):
   @reboot cd {base_dir} && nohup python scripts/market_monitor.py > logs/daemon.log 2>&1 &

3. Save and exit

4. Restart computer, daemon will start automatically

========================================================================

VERIFY DAEMON IS RUNNING:

Check logs:
  tail -f {daemon_log}

Manual start (testing):
  nohup python {script_path} > {daemon_log} 2>&1 &

Kill daemon:
  pkill -f "python.*market_monitor.py"

========================================================================
""")


if __name__ == "__main__":
    main()
