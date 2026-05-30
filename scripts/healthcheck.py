#!/usr/bin/env python3
"""Pre-flight health check for trading bot system readiness.

Validates: OpenD connectivity, config completeness, calibrator state, backtest freshness,
feature flag consistency, and position monitor state.

Usage:
    python scripts/healthcheck.py              # Run all checks
    python scripts/healthcheck.py --fast       # Skip test suite
    python scripts/healthcheck.py --verbose    # Show detailed output
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

# Import trading bot config
from tradingbot.core.config import tradingbot_home
from tradingbot.core.feature_flags import all_flags
from tradingbot.consensus.calibration import Calibrator, default_calibrator_path


def check_opend_reachable(host: str = "127.0.0.1", port: int = 11111) -> tuple[bool, str]:
    """Check if OpenD is reachable on localhost:11111."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        if result == 0:
            return True, "OpenD reachable on 127.0.0.1:11111"
        else:
            return False, "OpenD not running — open moomoo desktop app and enable OpenD"
    except Exception as e:
        return False, f"OpenD reachability check failed: {e}"


def check_env_keys() -> tuple[bool | None, str]:
    """Check that required env keys exist and are non-empty."""
    env_file = Path(".env")
    required_keys = ["MOOMOO_USERNAME", "MOOMOO_PASSWORD", "MOOMOO_ACCOUNT_ID"]

    if not env_file.exists():
        return None, ".env missing (operator may be using OS env)"

    try:
        with open(env_file) as f:
            env_content = f.read()

        missing = []
        for key in required_keys:
            # Simple check: key=value where value is non-empty
            found = False
            for line in env_content.split("\n"):
                if line.startswith(key + "="):
                    value = line.split("=", 1)[1].strip()
                    if value and not value.startswith("#"):
                        found = True
                        break
            if not found:
                missing.append(key)

        if missing:
            return False, f"Missing .env keys: {', '.join(missing)}"
        else:
            return True, "All required .env keys present"
    except Exception as e:
        return False, f".env validation failed: {e}"


def check_tradingbot_home_writable() -> tuple[bool, str]:
    """Check that TRADINGBOT_HOME is writable."""
    try:
        home = tradingbot_home()
        test_file = home / ".health_check_test"
        test_file.write_text("test")
        test_file.unlink()
        return True, f"TRADINGBOT_HOME writable: {home}"
    except Exception as e:
        return False, f"TRADINGBOT_HOME not writable: {e}"


def check_calibrator_state() -> tuple[bool | None, str]:
    """Check calibrator existence, mode, and sample count."""
    try:
        cal_path = default_calibrator_path()
        if not cal_path.exists():
            return False, f"Calibrator file missing: {cal_path}"

        cal = Calibrator.load(cal_path)
        # fit_timestamp is ISO string; parse it
        from datetime import datetime as dt_class
        fit_time = dt_class.fromisoformat(cal.state.fit_timestamp.replace('Z', '+00:00')).timestamp()
        age_hours = (time.time() - fit_time) / 3600

        msg = (
            f"mode={cal.state.mode}, n_samples={cal.state.n_samples}, "
            f"age={age_hours:.1f}h, brier={cal.state.brier_after:.3f}"
        )

        if cal.state.mode == "identity" and cal.state.n_samples < 50:
            return None, f"Calibrator: {msg} (untrained)"
        elif cal.state.mode == "identity":
            return None, f"Calibrator: {msg} (identity mode)"
        elif cal.state.n_samples < 50:
            return None, f"Calibrator: {msg} (low sample count)"
        elif age_hours > 168:  # > 7 days
            return None, f"Calibrator: {msg} (older than 7 days)"
        else:
            return True, f"Calibrator: {msg}"
    except Exception as e:
        return False, f"Calibrator state check failed: {e}"


def check_bandit_store() -> tuple[bool | None, str]:
    """Check bandit store existence and freshness."""
    try:
        home = tradingbot_home()
        bandit_path = home / "bandit" / "bandit_state.json"

        if not bandit_path.exists():
            return None, f"Bandit store missing: {bandit_path}"

        age_hours = (time.time() - bandit_path.stat().st_mtime) / 3600
        if age_hours > 168:  # > 7 days
            return None, f"Bandit store stale: {age_hours:.1f}h old"
        else:
            return True, f"Bandit store fresh: {age_hours:.1f}h old"
    except Exception as e:
        return False, f"Bandit store check failed: {e}"


def check_backtest_freshness() -> tuple[bool | None, str]:
    """Check backtest results freshness."""
    try:
        home = tradingbot_home()
        backtest_dir = home / "backtest"

        if not backtest_dir.exists():
            return None, "Backtest directory missing"

        result_files = list(backtest_dir.glob("results_*.json"))
        if not result_files:
            return False, "No backtest results found — run /oa2-recalibrate"

        newest = max(result_files, key=lambda f: f.stat().st_mtime)
        age_hours = (time.time() - newest.stat().st_mtime) / 3600

        if age_hours > 720:  # > 30 days
            return False, f"Backtest results too old: {age_hours:.1f}h (run /oa2-recalibrate)"
        elif age_hours > 168:  # > 7 days
            return None, f"Backtest results stale: {age_hours:.1f}h old"
        else:
            return True, f"Backtest results fresh: {age_hours:.1f}h old"
    except Exception as e:
        return False, f"Backtest freshness check failed: {e}"


def check_feature_flag_consistency() -> tuple[bool, str]:
    """Check feature flag consistency (e.g., BANDIT requires REGIME)."""
    try:
        flags = all_flags()
        issues = []

        if flags.get("TRADINGBOT_FLAG_BANDIT") and not flags.get("TRADINGBOT_FLAG_REGIME"):
            issues.append("BANDIT enabled but REGIME disabled (BANDIT depends on REGIME)")

        if issues:
            return None, "Feature flag issues: " + "; ".join(issues)
        else:
            return True, "Feature flags consistent"
    except Exception as e:
        return False, f"Feature flag check failed: {e}"


def check_position_monitor_state() -> tuple[bool, str]:
    """Check PositionMonitor persisted state."""
    try:
        home = tradingbot_home()
        scan_log_dir = home / "logs"

        if not scan_log_dir.exists():
            return True, "Position log directory not yet created (normal)"

        position_files = list(scan_log_dir.glob("positions_*.json"))
        if not position_files:
            return True, "No position state files yet (normal)"

        newest = max(position_files, key=lambda f: f.stat().st_mtime)
        age_hours = (time.time() - newest.stat().st_mtime) / 3600

        try:
            with open(newest) as f:
                positions = json.load(f)
            count = len(positions) if isinstance(positions, list) else 0
            return True, f"Latest position file: {newest.name}, {count} positions, age={age_hours:.1f}h"
        except Exception as e:
            return False, f"Position file corrupted: {newest.name}: {e}"
    except Exception as e:
        return False, f"Position monitor state check failed: {e}"


def check_test_suite(fast: bool = False) -> tuple[bool | None, str]:
    """Run test suite if not --fast mode."""
    if fast:
        return None, "Test suite check skipped (--fast mode)"

    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/", "-q", "--tb=no"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        # Parse output for pass/fail counts
        lines = result.stdout.strip().split("\n")
        summary = lines[-1] if lines else ""

        if result.returncode == 0:
            return True, f"Test suite: {summary}"
        else:
            return False, f"Test suite failed: {summary}"
    except subprocess.TimeoutExpired:
        return False, "Test suite check timed out (120s)"
    except Exception as e:
        return None, f"Test suite check unavailable: {e}"


def run_all_checks(verbose: bool = False, fast: bool = False) -> dict[str, any]:
    """Run all health checks and return results."""
    checks = [
        ("OpenD reachable", check_opend_reachable),
        (".env keys", check_env_keys),
        ("TRADINGBOT_HOME writable", check_tradingbot_home_writable),
        ("Calibrator state", check_calibrator_state),
        ("Bandit store", check_bandit_store),
        ("Backtest freshness", check_backtest_freshness),
        ("Feature flag consistency", check_feature_flag_consistency),
        ("Position monitor state", check_position_monitor_state),
        ("Test suite", lambda: check_test_suite(fast=fast)),
    ]

    results = {}
    for name, check_fn in checks:
        try:
            result = check_fn()
            if isinstance(result, tuple) and len(result) == 2:
                passed, msg = result
            else:
                passed, msg = False, "Invalid check result"
        except Exception as e:
            passed, msg = False, f"Check exception: {e}"

        # Normalize result to PASS/WARN/FAIL
        if passed is True:
            status = "PASS"
        elif passed is False:
            status = "FAIL"
        else:  # None
            status = "WARN"

        results[name] = {"status": status, "message": msg}

        # Print immediately
        print(f"[{status:4s}] {name}: {msg}")

    return results


def compute_verdict(results: dict) -> str:
    """Compute final verdict based on results."""
    statuses = {r["status"] for r in results.values()}

    if "FAIL" in statuses:
        return "NOT_READY"
    elif "WARN" in statuses:
        return "READY_WITH_WARNINGS"
    else:
        return "READY"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Trading bot health check")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--fast", action="store_true", help="Skip test suite")
    args = parser.parse_args()

    print("=" * 70)
    print("oa2 healthcheck — " + datetime.now().isoformat())
    print("=" * 70)
    print()

    results = run_all_checks(verbose=args.verbose, fast=args.fast)

    print()
    print("=" * 70)
    verdict = compute_verdict(results)
    print(f"Verdict: {verdict}")
    print("=" * 70)

    # Exit with status code
    if verdict == "NOT_READY":
        sys.exit(1)
    elif verdict == "READY_WITH_WARNINGS":
        sys.exit(0)
    else:
        sys.exit(0)
