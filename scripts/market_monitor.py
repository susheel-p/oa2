"""oa2 market monitor daemon.

Fully automated trading system with scheduled operations:
  - 8:30 AM:  Premarket report (today's plan based on yesterday's signals)
  - 9:35 AM:  Full-scan (debaters → consensus → sizing → save positions)
  - 9:30 AM–4:00 PM: Exit-only every minute (monitor & close positions)
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
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

REPORTS_DIR = Path(os.getenv("REPORTS_DIR", "reports"))


def _now_et() -> datetime.datetime:
    return datetime.datetime.now(ET)


def _get_daemon_log_path() -> Path:
    """Get daemon log file path."""
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    return log_dir / "daemon.log"


def _log(msg: str, log_file: Path | None = None) -> None:
    """Log message to console and optionally to daemon log file."""
    ts = _now_et().strftime("%Y-%m-%dT%H:%M:%S%z")
    output = f"[{ts}] {msg}"
    print(output, flush=True)

    if log_file:
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(output + "\n")
        except Exception:
            pass  # Silently fail if log file unavailable


def _is_market_day() -> bool:
    """Return False on weekends."""
    return _now_et().weekday() < 5


def _is_market_open() -> bool:
    """Return True if 9:30 AM - 4:00 PM ET on a market day."""
    if not _is_market_day():
        return False
    now = _now_et()
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now < market_close


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
    """Run a shell command and log the result."""
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
        return False
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - t_start
        _log(f"[{label}] TIMEOUT after {elapsed:.1f}s (limit {timeout_s}s)", log_file)
        _log(f"[{label}] stdout tail:\n{_tail(exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout)}", log_file)
        _log(f"[{label}] stderr tail:\n{_tail(exc.stderr.decode() if isinstance(exc.stderr, bytes) else exc.stderr)}", log_file)
        return False
    except Exception as exc:
        elapsed = time.monotonic() - t_start
        _log(f"[{label}] ERROR after {elapsed:.1f}s: {exc!r}", log_file)
        _log(f"[{label}] traceback:\n{traceback.format_exc()}", log_file)
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
        self.premarket_done_today = False
        self.postmarket_done_today = False
        self.learning_loop_done_today = False
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
            f"(target 09:35:00)",
            self.log_file
        )

        cmd = self._build_cmd("full-scan")
        if _run_command(cmd, "FULL-SCAN", self.log_file):
            with self.lock:
                self.full_scan_done_today = True
                _log("Full-scan flagged as done for today", self.log_file)

    def _run_exit_only(self) -> None:
        """Run exit-only if market is open."""
        if not _is_market_open():
            return

        cmd = self._build_cmd("exit-only")
        _run_command(cmd, "EXIT-ONLY", self.log_file)

    def _run_premarket_report(self) -> None:
        """Generate premarket report at 8:30 AM."""
        with self.lock:
            if self.premarket_done_today:
                return

        now = _now_et()
        _log(f"Premarket report trigger at {now.strftime('%H:%M:%S')} (target 08:30:00)", self.log_file)

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

    def _reset_daily_flags(self) -> None:
        """Reset daily flags at midnight."""
        with self.lock:
            self.full_scan_done_today = False
            self.premarket_done_today = False
            self.postmarket_done_today = False
            self.learning_loop_done_today = False

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
        if not self.once and _is_market_day():
            now = _now_et()
            today = now.date()
            premarket_target = now.replace(hour=8, minute=30, second=0, microsecond=0)
            postmarket_target = now.replace(hour=16, minute=15, second=0, microsecond=0)
            learning_target = now.replace(hour=17, minute=0, second=0, microsecond=0)

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

            # Check for premarket report at 8:30 AM
            if (
                now.hour == 8
                and now.minute == 30
                and _is_market_day()
            ):
                self._run_premarket_report()

            # Check for full-scan at 9:35 AM
            if (
                now.hour == 9
                and now.minute == 35
                and _is_market_day()
            ):
                self._run_full_scan()

            # Run exit-only every minute during market hours
            if _is_market_open():
                self._run_exit_only()

            # Check for postmarket report at 4:15 PM
            if (
                now.hour == 16
                and now.minute == 15
                and _is_market_day()
            ):
                self._run_postmarket_report()

            # Check for learning loop at 5:00 PM (17:00)
            if (
                now.hour == 17
                and now.minute == 0
                and _is_market_day()
            ):
                self._run_learning_loop()

            # Write heartbeat for watchdog monitoring (before checking self.once)
            try:
                heartbeat_file.write_text(str(time.time()))
            except Exception:
                pass  # Silently fail if heartbeat write unavailable

            if self.once:
                break

            # Sleep until next minute boundary
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

    monitor = MarketMonitor(dry_run=args.dry_run, once=args.once, daemon_mode=daemon_mode, scan_on_start=args.scan_on_start)

    if daemon_mode:
        log_file = _get_daemon_log_path()
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
  * 8:30 AM:  Premarket report (today's trading plan)
  * 9:35 AM:  Full scan (enter trades)
  * Every 1 min (9:30 AM–4:00 PM): Monitor exits
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
