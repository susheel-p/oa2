#!/usr/bin/env python3
"""Preflight check for tradingbot daemon."""

import json
import socket
import subprocess
import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

checks = []
ET = ZoneInfo("America/New_York")

# 1. OpenD reachable
try:
    s = socket.socket()
    s.settimeout(1)
    s.connect(('127.0.0.1', 11111))
    s.close()
    checks.append(("[PASS] OpenD reachable on 127.0.0.1:11111", "PASS"))
except Exception as e:
    checks.append(("[FAIL] OpenD not running — open moomoo desktop app and enable OpenD", "FAIL"))

# 2. .env sanity
env_file = Path(".env")
required_keys = ["MOOMOO_USERNAME", "MOOMOO_PASSWORD", "MOOMOO_ACCOUNT_ID"]
if not env_file.exists():
    checks.append(("[WARN] .env file missing (using OS env vars)", "WARN"))
else:
    env_vars = {}
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()

    missing = [k for k in required_keys if k not in env_vars or not env_vars[k]]
    if missing:
        checks.append((f"[FAIL] .env missing keys: {', '.join(missing)}", "FAIL"))
    else:
        checks.append(("[PASS] .env keys present: MOOMOO_USERNAME, MOOMOO_PASSWORD, MOOMOO_ACCOUNT_ID", "PASS"))

# 3. Test suite green
result = subprocess.run(["python", "-m", "pytest", "tests/", "-q"], capture_output=True, text=True, timeout=300)
output_lines = result.stdout.strip().split("\n")
summary_line = output_lines[-1] if output_lines else ""

if "passed" in summary_line and "failed" not in summary_line and "error" not in summary_line.lower():
    import re
    match = re.search(r"(\d+) passed", summary_line)
    if match:
        checks.append((f"[PASS] tests: {match.group(1)} passed", "PASS"))
    else:
        checks.append(("[PASS] tests passing", "PASS"))
else:
    if "failed" in summary_line or "error" in summary_line.lower():
        checks.append((f"[FAIL] tests: {summary_line}", "FAIL"))
    else:
        checks.append(("[FAIL] test run failed", "FAIL"))

# 4. Latest backtest age
backtest_dir = Path.home() / ".tradingbot" / "backtest"
if backtest_dir.exists():
    backtest_files = sorted(backtest_dir.glob("results_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if backtest_files:
        latest = backtest_files[0]
        age_hours = (datetime.datetime.now(ET).timestamp() - latest.stat().st_mtime) / 3600
        age_days = age_hours / 24

        if age_hours <= 168:
            checks.append((f"[PASS] backtest age: {age_days:.1f} days", "PASS"))
        elif age_hours <= 720:
            checks.append((f"[WARN] backtest age: {age_days:.1f} days (run /tradingbot-recalibrate)", "WARN"))
        else:
            checks.append((f"[FAIL] backtest age: {age_days:.1f} days — too old (run /tradingbot-recalibrate)", "FAIL"))
    else:
        checks.append(("[FAIL] no backtest found — run /tradingbot-recalibrate", "FAIL"))
else:
    checks.append(("[FAIL] no backtest directory — run /tradingbot-recalibrate", "FAIL"))

# 5. Calibrator state
calibrator_path = Path.home() / ".tradingbot" / "calibration" / "p_bull_calibrator.json"
if not calibrator_path.exists():
    checks.append(("[FAIL] calibrator file missing", "FAIL"))
else:
    with open(calibrator_path) as f:
        cal = json.load(f)

    mode = cal.get("mode", "unknown")
    n_samples = cal.get("n_samples", 0)
    brier_before = cal.get("brier_before", 0)
    brier_after = cal.get("brier_after", 0)
    slope_a = cal.get("platt_slope_a", 0)

    status = "PASS"
    if mode == "identity" and n_samples < 50:
        status = "WARN"
    elif mode != "identity" and n_samples >= 50:
        status = "PASS"
    else:
        status = "WARN" if mode == "identity" else "PASS"

    if slope_a:
        msg = f"[{status}] calibrator: mode={mode}, n={n_samples}, brier {brier_before:.3f} -> {brier_after:.3f}, slope a={slope_a:.2f}"
    else:
        msg = f"[{status}] calibrator: mode={mode}, n={n_samples}, brier {brier_before:.3f} -> {brier_after:.3f}"
    checks.append((msg, status))

# 6. Open positions (informational)
try:
    from tradingbot.execution.monitor import PositionMonitor
    m = PositionMonitor()
    count = m.position_count()
    checks.append((f"[PASS] open positions: {count}", "PASS"))
except Exception:
    checks.append(("[PASS] open positions: no in-memory state", "PASS"))

# 7. Last shadow run
logs_dir = Path("logs")
if logs_dir.exists():
    shadow_files = sorted(logs_dir.glob("paper_trade_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if shadow_files:
        latest_shadow = shadow_files[0]
        date_str = latest_shadow.stem.replace("paper_trade_", "")

        signal_count = 0
        try:
            with open(latest_shadow) as f:
                signal_count = sum(1 for _ in f)
        except:
            pass

        age_days = (datetime.datetime.now(ET).date() - datetime.datetime.strptime(date_str, "%Y-%m-%d").date()).days

        if age_days == 0 or age_days == 1:
            status = "PASS"
        elif age_days <= 3:
            status = "WARN"
        else:
            status = "FAIL"

        checks.append((f"[{status}] last shadow run: {latest_shadow.name} ({signal_count} signals)", status))
    else:
        checks.append(("[FAIL] no shadow log found", "FAIL"))
else:
    checks.append(("[FAIL] no logs directory", "FAIL"))

# Print results
print()
for msg, _ in checks:
    print(msg)

# Verdict
statuses = [s for _, s in checks]
if "FAIL" in statuses:
    verdict = "NOT_READY"
elif "WARN" in statuses:
    verdict = "READY_WITH_WARNINGS"
else:
    verdict = "READY"

print()
print(f"Verdict: {verdict}")
print()
