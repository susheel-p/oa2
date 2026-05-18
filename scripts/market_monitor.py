"""oa2 market monitor daemon.

Runs full-scan and exit-only on schedule:
  - 9:35 AM: Full-scan (debaters → consensus → sizing → save positions)
  - Every 1 min (9:30-4:00 PM): Exit-only (load positions → check exits → log alerts)

Designed to run continuously as a daemon/service.

Usage:
    python scripts/market_monitor.py                # run until market close
    python scripts/market_monitor.py --dry-run      # test mode, no file writes
    python scripts/market_monitor.py --once         # run once and exit (for testing)
"""

from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _now_et() -> datetime.datetime:
    return datetime.datetime.now(ET)


def _log(msg: str) -> None:
    ts = _now_et().strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"[{ts}] {msg}", flush=True)


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


def _run_command(cmd: list[str], label: str) -> bool:
    """Run a shell command and log the result."""
    try:
        _log(f"[{label}] Starting ...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            _log(f"[{label}] SUCCESS")
            return True
        else:
            _log(f"[{label}] FAILED (exit code {result.returncode})")
            if result.stderr:
                _log(f"[{label}] stderr: {result.stderr[:200]}")
            return False
    except subprocess.TimeoutExpired:
        _log(f"[{label}] TIMEOUT (5 min)")
        return False
    except Exception as exc:
        _log(f"[{label}] ERROR: {exc}")
        return False


class MarketMonitor:
    """Daemon that schedules full-scan and exit-only operations."""

    def __init__(self, dry_run: bool = False, once: bool = False):
        self.dry_run = dry_run
        self.once = once
        self.full_scan_done_today = False
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
            f"(target 09:35:00)"
        )

        cmd = self._build_cmd("full-scan")
        if _run_command(cmd, "FULL-SCAN"):
            with self.lock:
                self.full_scan_done_today = True
                _log("Full-scan flagged as done for today")

    def _run_exit_only(self) -> None:
        """Run exit-only if market is open."""
        if not _is_market_open():
            return

        cmd = self._build_cmd("exit-only")
        _run_command(cmd, "EXIT-ONLY")

    def _reset_daily_flags(self) -> None:
        """Reset daily flags at midnight."""
        with self.lock:
            self.full_scan_done_today = False

    def _schedule_loop(self) -> None:
        """Main scheduling loop."""
        _log("Market monitor started")

        last_daily_reset = _now_et().date()

        while not self.stop_event.is_set():
            now = _now_et()

            # Reset daily flags at midnight
            if now.date() > last_daily_reset:
                _log("Daily reset (midnight passed)")
                self._reset_daily_flags()
                last_daily_reset = now.date()

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
            _log("Shutting down...")
        except Exception as exc:
            _log(f"Unexpected error: {exc}")
            raise

        _log("Market monitor stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="oa2 market monitor daemon")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test mode: log to console, do not write files",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one cycle and exit (for testing)",
    )
    args = parser.parse_args()

    monitor = MarketMonitor(dry_run=args.dry_run, once=args.once)
    monitor.run()


if __name__ == "__main__":
    main()
