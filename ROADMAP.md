# oa2 Production Roadmap

## Production Readiness Assessment

All phases A–F complete. System is ready for supervised paper trading.

**Hard gate status:**
1. Phase A complete: honest debaters, live EWMA correlation, warm bandit posteriors
2. Phase B complete: sizing engine — Kelly + Greeks caps + CVaR pass before every trade
3. Phase C complete: exit engine running on all open positions
4. Phase F complete: v2 Sharpe >= v1 baseline on 90-day backtest window

**Remaining gate:** 2 weeks of shadow mode with no sizing/exit rule breaches before
enabling unsupervised paper trading.

---

## Phase A — Signal Integrity [COMPLETE]

Goal: make sure every signal the system emits is honest and statistically grounded.

### A1 — Flow Debater: Honest Abstention [DONE]

**Fix:** `oa2/debaters/flow.py` — added `_has_real_flow_data()` guard requiring
`flow_data["data_quality"] == "real"`. Returns `conviction=0.0, direction=NEUTRAL`
when no real tape data available. No more PCR derived from chain delta.

### A2 — Bandit Warm-Start from Historical Replay [DONE]

**Fix:** `scripts/bandit_warmstart.py` — full 6-month yfinance replay, scores
next-day direction hits per (debater, regime), updates Beta posteriors.
Usage: `python scripts/bandit_warmstart.py [--months 6] [--dry-run] [--verbose]`

### A3 — EWMA Correlation Matrix [DONE]

**Fix:** `oa2/consensus/covariance.py` — rolling EWMA (λ=0.94, min 20 obs).
`oa2/consensus/engine.py` — `_compute_correlation_matrix()` now instance method,
uses live EWMA when tracker is warm, falls back to `_fixed_correlation()`.
Feature flag: `OA2_FLAG_EWMA_CORR` (default on).

---

## Phase B — Sizing Engine (oa2/sizing/) [COMPLETE]

Gate: required before any paper or live trading begins. No trade executes without sizing.

### B1 — Fractional Kelly per Trade

Formula: `f* = (edge × odds - (1 - edge)) / odds × kelly_fraction`
where:
- `edge` = consensus p_bull (or 1 - p_bull for bearish) from consensus engine
- `odds` = max_profit / max_loss from ChainSnapshot
- `kelly_fraction` = 0.25 (quarter-Kelly; conservative for options)

Output: contract count recommendation, bounded by B2 hard caps.

**File:** `oa2/sizing/kelly.py`

### B2 — Book-Level Greeks Hard Caps

Track running book Greeks across all open positions:
- `max_net_delta`: ±0.30 of account (e.g., ±$300 per $1000 account)
- `max_net_vega`: ±$50/1% IV move
- `max_net_theta`: no single-day theta > 2% of account
- `max_single_underlying_pct`: no more than 25% of vega/delta in one name

Any proposed trade that would breach a cap is hard-rejected by the pipeline, regardless
of consensus direction or conviction.

**File:** `oa2/sizing/limits.py`

### B3 — CVaR Scenario Check

Five stress scenarios before trade approval:
1. Underlying -3% intraday
2. Underlying -5% intraday
3. VIX +10 points (IV spike)
4. VIX +20 points (crisis spike)
5. Correlation spike: all positions move adversely simultaneously

If any scenario produces a P&L breach > configured threshold (default: 5% of account),
the trade is rejected or size is reduced to fit within the CVaR budget.

**File:** `oa2/sizing/cvar.py`

### B4 — DTE-Aware Sizing

DTE is a first-class variable in sizing:
- DTE 0-2: long-gamma only; size at 50% of Kelly (gamma risk)
- DTE 3-6: normal Kelly; tight stop rules
- DTE 7-21: full Kelly; standard rules
- DTE 22-45: full Kelly; appropriate for premium selling
- DTE 46+: reduce to 75% Kelly (calendar/diagonal plays; more uncertain P&L path)

Short positions approaching DTE < 2: mandatory size reduction and exit evaluation.

**File:** `oa2/sizing/kelly.py` (DTE parameter)

---

## Phase C — Exit Engine (oa2/execution/) [COMPLETE]

Gate: required for unattended paper trading. Without exits, every open position
is a risk that grows over time.

### C1 — Position Monitor

Module that maintains a registry of open positions and their current mark-to-market.
Polls at configurable intervals (default: every 5 minutes during market hours).
For each position: current P&L vs target, DTE, current regime, and current consensus.

**File:** `oa2/execution/monitor.py`

### C2 — Exit Rules Engine

Rules evaluated in priority order for each open position:

1. **Hard stop**: current loss >= max_loss_dollars → market order close, log ExitReason.STOP_LOSS
2. **Profit target**: current gain >= 50% of max_profit for short premium → close, PROFIT_TARGET
3. **DTE emergency**: DTE < 2 on any short leg → close, TIME_STOP (avoid assignment)
4. **Time stop**: position held > time_stop_days from entry → evaluate close, TIME_STOP
5. **Hard EOD**: 3:55 PM ET → force close all intraday positions, HARD_EOD_CUTOFF
6. **Regime flip**: current regime ≠ entry regime → re-run consensus; if direction flips → close

**File:** `oa2/execution/exit.py`

### C3 — Roll Logic

When a short position is: profitable (> 25% max profit), DTE < 14, and regime unchanged:
evaluate rolling to the next expiration vs closing. Roll if:
- Next expiry has sufficient premium to justify transaction costs
- Rolling does not increase Greek exposure beyond limits
- Regime forecast supports continued premium collection

**File:** `oa2/execution/roll.py`

---

## Phase D — Regime Enhancement [COMPLETE]

Goal: improve the quality of regime classification and add missing intraday context.

### D1 — Session-State Overlay

Current regime (vol × trend) is a daily signal. Add intraday session tagging:
- OPEN: 9:30-10:00 ET (high vol, wide spreads, flow meaningful)
- MORNING: 10:00-12:00 ET (direction establishes)
- MIDDAY: 12:00-14:00 ET (low vol, narrow ranges, theta harvest)
- AFTERNOON: 14:00-15:30 ET (institutional positioning)
- POWER_HOUR: 15:30-16:00 ET (directional follow-through, closing flows)

Debater weights adjust by session: flow and GEX matter more at OPEN;
technical matters more in AFTERNOON; income/theta harvesting in MIDDAY.

**File:** `oa2/regime/session.py`

### D2 — Leading Crisis Signal

Current crisis trigger (VIX > 35 or RV/IV > 1.20) is backward-looking.
By the time VIX hits 35, the vol spike is priced.

Leading indicators to add:
- VIX3M/VIX ratio < 1.05: term structure flattening (near-term fear rising)
- VVIX > 110: vol-of-vol elevated (options on options expensive = hedging demand)
- Both conditions simultaneously → early CRISIS signal, shift regime one step earlier

**File:** `oa2/regime/classifier.py` (add _leading_crisis_check method)

### D3 — Cross-Asset Macro Signals

TLT, HYG, DXY as regime context:
- TLT falling + DXY rising: risk-off flight to dollar (bearish equities)
- TLT rising + DXY falling: risk-on (bullish equities, watch for reflation)
- HYG/JNK spread widening: credit stress precedes equity vol by 1-3 days
- USO spike > 3%: energy shock, bearish XLY, XLF; bullish XLE

These are NOT separate debaters. They are additional inputs to the regime classifier
that shift vol_state or flag CAUTION/HALT on the MacroSignal.

**File:** `oa2/regime/classifier.py` (add cross_asset_context parameter)

### D4 — Max Pain / Call-Put Walls from GEX

The existing `compute_gex()` function has all the data. Extend it to compute:
- `call_wall`: highest open interest call strike (magnetic ceiling)
- `put_wall`: highest open interest put strike (magnetic support)
- `max_pain`: strike where total option value (calls + puts) is minimized (pinning target)

These populate `Setup.resistance_level` (= call wall) and `Setup.support_level` (= put wall).
Max pain is stored in context for intraday target setting.

**File:** `oa2/dealer/gex.py` (extend GEXResult)

### D5 — Fix Additive Conviction Scoring

Current: directional debater counts RSI + VWAP + EMA signals as independent votes.
Problem: these are all derived from the same price series — highly correlated.

Fix: group signals by independent data source:
- Group A (price momentum): VWAP position, EMA crossover, price vs prior close
- Group B (oscillator): RSI, stochastic
- Group C (structure): multi-timeframe alignment

Vote = max conviction from each group. Cross-group agreement multiplies conviction.
Same fix applies to flow debater (PCR, sweep counts, OI changes are partially correlated).

**File:** `oa2/debaters/directional.py`, `oa2/debaters/flow.py`

---

## Phase E — Real Flow Data [COMPLETE]

Decision point: select a data vendor for real-time options tape.

### E1 — Data Source Evaluation

| Source | Cost | Coverage | Quality |
|---|---|---|---|
| Unusual Whales API | ~$50/mo | Sweeps, dark pool, PCR | High |
| Tradier streaming | ~$10/mo | Real-time chain, tick | High |
| Moomoo OpenD | Already integrated | Options chain, EOD OI | Medium |
| yfinance | Free | EOD chain, 15-min delayed | Low |

Recommendation: Tradier for real-time chain data + Unusual Whales for sweep/dark pool.
Current yfinance suffices for daily regime detection but not intraday flow.

### E2 — Wire FlowDebater to Real Sweeps

Once data source chosen:
- Feed real PCR (from tape, not derived from delta) into flow_data["put_call_ratio"]
- Feed real sweep counts: flow_data["call_sweep_count"], flow_data["put_sweep_count"]
- Feed dark pool: flow_data["dark_pool_bullish"], flow_data["dark_pool_bearish"]
- FlowDebater conviction scale is already correct; only the inputs are wrong

**File:** `oa2/dataflows/flow_adapter.py` (new)

### E3 — Expiration-Aware Flow

Track which expiration smart money is targeting:
- Sweeps in front-week options: high urgency, directional
- Sweeps in longer-dated options (30-60 DTE): positioning, less urgent
- OI accumulation in specific strikes = price targets, not just direction

---

## Phase F — Backtesting Harness [COMPLETE]

### F1 — Historical Replay Framework

`scripts/backtest.py`: replay 6-12 months of daily data through full pipeline.
- yfinance daily OHLCV + EOD options snapshot
- Synthetic regime classification from historical VIX + price data
- Run all debaters; record opinions vs next-day outcome (direction of close-to-close)
- Output: per-debater accuracy by regime, confusion matrix, Sharpe by regime

### F2 — Bandit Validation

Before warm-starting, validate the posteriors make economic sense:
- Check that income debater performs better in high-IV regimes
- Check that directional debater performs better in trending regimes
- Check that flow debater (when real data available) outperforms random in all regimes

### F3 — A/B vs v1

OA2_FLAG_AB_V1 flag exists but is unconnected. Wire it:
- On each scan, record v2 consensus direction and v1 decision
- Compare: win rate, avg P&L, Sharpe, max drawdown over same period
- Paper cutover when v2 >= v1 on 90-day window

### F4 — Paper Cutover Gate

Hard requirements before cutover:
1. Phase A complete: honest debaters, live correlation, warm bandit [DONE]
2. Phase B complete: sizing engine passing all scenario checks
3. Phase C complete: exit engine running on all open positions
4. Phase F3: v2 Sharpe >= v1 Sharpe on 90-day replay
5. 2 weeks of shadow mode with no sizing/exit rule breaches

---

## Build Order and Timeline

```
Week 1-2:   Phase A (signal integrity — prerequisite for everything)  [COMPLETE]
Week 3-4:   Phase B (sizing engine — gating item for paper trading)   [COMPLETE]
Week 5-6:   Phase C (exit engine — gating item for unattended running)[COMPLETE]
Week 7:     Phase D (regime enhancement — improves quality, not gating)[COMPLETE]
Week 8+:    Phase E (real flow data — adapter layer complete)          [COMPLETE]
Ongoing:    Phase F (backtesting, A/B vs v1)                          [COMPLETE]

Paper trade cutover: after A + B + C complete and F shows parity with v1
```

---

## Known Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Flow data cost > budget | Medium | Start with moomoo built-in; add Unusual Whales gradually |
| Bandit warm-start overfits to training window | Medium | Use out-of-sample validation period; cap posterior updates |
| Sizing engine rejects all trades (too conservative) | Low | Tune Kelly fraction and cap thresholds against backtest |
| Exit engine forces premature closes | Medium | A/B test exit rules against hold-to-expiry baseline |
| Regime classifier misclassifies transitions | High (always) | Session overlay + posterior distribution smooths boundaries |
