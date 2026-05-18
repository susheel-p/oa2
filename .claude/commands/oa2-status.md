---
description: Preflight check — is oa2 ready to run today? OpenD, .env, backtest age, calibrator state, open positions, last shadow run.
---

You are the operator's preflight check before any oa2 run. Produce a one-screen, scannable readout. No fluff, no recommendations the operator didn't ask for.

Run each check in sequence, capture its result, and report **PASS / WARN / FAIL** with the reason. At the end, print one overall verdict: `READY` (all PASS), `READY_WITH_WARNINGS` (any WARN, no FAIL), or `NOT_READY` (any FAIL).

## Checks

### 1. moomoo OpenD reachable
PowerShell on Windows; use `Test-NetConnection -ComputerName 127.0.0.1 -Port 11111 -InformationLevel Quiet`. On other shells, use `python -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1',11111)); s.close()"` and treat non-zero exit as FAIL.

- **PASS** if the port is open.
- **FAIL** if not. Reason: "OpenD not running — open moomoo desktop app and enable OpenD."

### 2. .env sanity
Read `.env` (if present). Check that these keys exist and are non-empty (do NOT print the values):
`MOOMOO_USERNAME`, `MOOMOO_PASSWORD`, `MOOMOO_ACCOUNT_ID`.

- **PASS** if all three present.
- **FAIL** if any missing — name the missing keys.
- **WARN** if `.env` is missing entirely (operator may be using OS env).

### 3. Test suite green
Run `python -m pytest tests/ -q` and parse the final summary line.

- **PASS** if `N passed` with no failures.
- **FAIL** if any failures or errors — print the failing test names.

### 4. Latest backtest age
Locate `~/.oa2/backtest/results_*.json` (newest by mtime). Compute age in hours.

- **PASS** if age ≤ 168 hours (one week).
- **WARN** if 168 < age ≤ 720 (older than a week, younger than a month).
- **FAIL** if no backtest found or age > 720 hours. Suggest `/oa2-recalibrate`.

### 5. Calibrator state
Load `~/.oa2/calibration/p_bull_calibrator.json` and report:
- `mode` (identity / platt / isotonic)
- `n_samples`
- `brier_before` → `brier_after`
- Platt `a` slope (if applicable)

- **PASS** if `mode != identity` and `n_samples >= 50`.
- **WARN** if identity mode with samples < 50 (legitimately untrained).
- **FAIL** if the file is missing.

### 6. Open positions
Use `python -c "from oa2.execution.monitor import PositionMonitor; m = PositionMonitor(); print(m.position_count())"` — if a persisted state file exists, load it first; otherwise just report "no in-memory state".

Report the count and the list of underlying tickers.

- Always **PASS** (informational only).

### 7. Last shadow run
Locate the newest `logs/paper_trade_*.jsonl`. Report the date and number of signals logged.

- **PASS** if a run exists from today or the most recent trading day.
- **WARN** if the most recent run is older than 3 calendar days.
- **FAIL** if no shadow log exists.

## Output format

Print one line per check, then the verdict. Example:

```
[PASS] OpenD reachable on 127.0.0.1:11111
[PASS] .env keys present: MOOMOO_USERNAME, MOOMOO_PASSWORD, MOOMOO_ACCOUNT_ID
[PASS] tests: 471 passed
[WARN] backtest age: 8.3 days (run /oa2-recalibrate)
[PASS] calibrator: mode=platt, n=218, brier 0.335 -> 0.249, slope a=0.10
[PASS] open positions: 0
[PASS] last shadow run: logs/paper_trade_2026-05-18.jsonl (22 signals)

Verdict: READY_WITH_WARNINGS
```

Do not run paper_trade.py or any other write action. This is read-only.
