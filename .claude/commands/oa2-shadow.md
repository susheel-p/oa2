---
description: Run today's shadow scan and summarize the signals. Arguments are passed as tickers (optional).
argument-hint: "[ticker ticker ...]"
---

You are running today's oa2 shadow scan. **No real orders are placed** — `paper_trade.py --dry-run` only logs signals and exit alerts.

## Steps

1. **Preflight (lightweight)**
   Confirm `python -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1',11111))"` exits 0. If not, abort with:
   `OpenD is not reachable. Start the moomoo desktop app, enable OpenD, then re-run /oa2-shadow.`

2. **Run the scan**
   ```powershell
   $env:PYTHONIOENCODING="utf-8"
   python scripts/paper_trade.py --dry-run $ARGUMENTS
   ```
   - If `$ARGUMENTS` is empty, omit it and the runner uses the full 22-ticker watchlist.
   - If non-empty, it becomes `--tickers SPY QQQ ...`.

   Stream output to the operator. The runner already writes:
   - `logs/paper_trade_<YYYY-MM-DD>.jsonl` — per-ticker signal records
   - `logs/summary_<YYYY-MM-DD>.json` — daily summary

3. **Summarize the run** (this is the value-add of the skill)

   After the scan completes, read the day's `logs/summary_*.json` and the JSONL, then print a structured readout:

   ```
   ── oa2 shadow run summary ──────────────────────────────────────
   date:                2026-05-18
   tickers scanned:     22  (or the subset you passed)
   signals logged:      <count>

   pipeline status counts:
     scaffold_only:     <n>
     debaters_only:     <n>
     full_pipeline:     <n>
     sized_approved:    <n>    ← these would be live trades
     sized_rejected:    <n>

   rejection breakdown (by gate):
     kelly:             <n>
     book_limits:       <n>
     cvar:              <n>
     mc_cvar:           <n>

   open-position exit alerts: <n>
     <if any, list trade_id / reason / urgency>

   calibrator on this run: mode=<m>, slope=<a>
   ────────────────────────────────────────────────────────────────
   ```

4. **Do not** push to GitHub. The `--dry-run` flag already disables that.

## Failure handling
- If the runner exits non-zero: print the last 20 lines of its stderr and stop. Do NOT retry.
- If `logs/summary_<today>.json` is missing after a successful run: warn but still complete; the runner may have failed silently after the scan.

## Notes
- Skill is read-mostly. The only writes are the runner's own JSONL/JSON log files.
- Run this once per day, ideally between 9:30–9:45 AM ET so the directional debaters get the open's worth of bar data.
