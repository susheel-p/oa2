---
description: Summarize a shadow-trade day's signals from logs/. Optional date arg (YYYY-MM-DD); defaults to most recent log.
argument-hint: "[YYYY-MM-DD]"
---

You are the operator's morning review of yesterday's shadow run. Read-only; no scans, no fits, no commits.

## Steps

1. **Resolve the target date**
   - If `$ARGUMENTS` is a date string `YYYY-MM-DD`, use it.
   - Otherwise pick the newest `logs/paper_trade_*.jsonl` by mtime and extract its date.
   - If no file matches, stop with: `No shadow-trade logs found. Run /oa2-shadow first.`

2. **Load both files for that date**
   - `logs/paper_trade_<date>.jsonl` — one JSON object per line, per ticker
   - `logs/summary_<date>.json` — daily summary (may not exist if the runner failed late)

   Parse defensively: skip malformed lines, never crash.

3. **Report — be specific, not generic**

   ```
   ── oa2 shadow review: <date> ───────────────────────────────────
   signals logged:           <count>
   tickers covered:          <list, comma-separated>
   pipeline statuses:
     scaffold_only:          <n>
     debaters_only:          <n>
     full_pipeline:          <n>  (consensus reached, sizing not triggered)
     sized_approved:         <n>  ← these would have been live trades
     sized_rejected:         <n>

   approved trade summary:   (one line per approval, max 10)
     <ticker> <direction> <contracts>x  p_bull=<cal> kelly=<f>  $risk=<n>

   rejections by gate:
     kelly:                  <n>  (most common reason: <reason summary>)
     book_limits:            <n>
     scenario_stress:        <n>
     mc_cvar:                <n>

   calibrator at run time:
     mode=<m>, n=<n>, slope a=<a>, brier=<b>

   regime distribution observed:
     <regime_label>: <n>      (up to 8 lines)

   open-position exit alerts: <count>
     <ticker> <trade_id> <reason> <urgency>     (per alert)

   anomalies / things to look at:
     <list any of: pipeline errors, fetch failures, calibrator identity-mode
      after refit, all-rejected days, regime confidence < 0.3 majority, etc.>
   ────────────────────────────────────────────────────────────────
   ```

4. **Anomaly detection** — flag these explicitly in the bottom section:
   - Any record with a `fetch_error` field set
   - Any sized_approved trade where `kelly.edge < 0.55` (Kelly fired with thin margin)
   - Any day where rejections > approvals + 10x (whole run was non-productive)
   - Any open-position exit alert with urgency=IMMEDIATE that wasn't closed

## Notes
- Pure read. Do not modify any file.
- If both files are missing for the requested date, suggest the next-nearest date that does have logs.
- Truncate the "approved trade summary" to 10 entries — if there are more, end with `... (N more, see logs/paper_trade_<date>.jsonl)`.
