---
description: Re-run the backtest and refit the p_bull calibrator. Shows the Brier delta and Platt slope so you can see whether signal quality is improving.
argument-hint: "[months] [tickers...]"
---

You are refreshing oa2's offline learning artefacts. This is the weekly maintenance command — it does not place trades and does not depend on OpenD.

## Steps

1. **Parse args**
   - `$ARGUMENTS` may be empty, just `months`, or `months tickers...`.
   - Defaults: months=6, tickers=`SPY QQQ IWM DIA`.

2. **Capture the pre-fit calibrator state** (so we can show the delta)
   Read `~/.oa2/calibration/p_bull_calibrator.json` if it exists. Save: mode, n_samples, brier_after, platt slope `a`. If the file is missing, this is the first fit — note that and continue.

3. **Run the backtest**
   ```powershell
   $env:PYTHONIOENCODING="utf-8"
   $env:PYTHONPATH="."
   python scripts/backtest.py --months <months> --tickers <tickers>
   ```
   Stream output. The script writes `~/.oa2/backtest/results_<timestamp>.json`.

   If the backtest exits non-zero or no new `results_*.json` is created, abort and report the last 20 stderr lines.

4. **Refit the calibrator**
   ```powershell
   $env:PYTHONIOENCODING="utf-8"
   $env:PYTHONPATH="."
   python scripts/fit_calibrator.py
   ```
   The script reads the newest backtest result and writes `~/.oa2/calibration/p_bull_calibrator.json`.

5. **Report the delta**
   Print a one-screen comparison:

   ```
   ── oa2 recalibration ───────────────────────────────────────────
   backtest:            months=<m>, tickers=<list>, days=<n>
   v2 Sharpe:           <new>  (was <prev if known>)
   baseline Sharpe:     <new>  (was <prev if known>)
   consensus accuracy:  <new%> on non-NEUTRAL days

   calibrator (before → after):
     mode:              <prev> → <new>
     n_samples:         <prev> → <new>
     brier:             <prev_after> → <new_after>
     Platt slope a:     <prev_a> → <new_a>

   signal quality verdict:
     IMPROVING   if new slope a > prev slope a + 0.05
     STAGNANT    if |Δa| <= 0.05
     DEGRADING   if new slope a < prev slope a - 0.05
   ────────────────────────────────────────────────────────────────
   ```

   - If Platt slope is still below 0.30 after this fit, print a one-line note:
     `Signal still weak — Kelly will reject most trades through min-edge gate.`

## Notes
- Read-only on the codebase; only writes are `~/.oa2/backtest/results_*.json` and `~/.oa2/calibration/p_bull_calibrator.json`.
- Do NOT push to git from this skill. The operator can commit if they want to capture the new artefact path.
- Both scripts are network-bound (yfinance). Allow up to 10 minutes for the full SPY/QQQ/IWM/DIA × 6-month run.
