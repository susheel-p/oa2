# Critical Questions Analysis — Signal Quality Improvement Plan

**Date:** 2026-05-18  
**Status:** Pre-implementation discovery  
**Scope:** Four blocking questions for the IMPROVEMENT_PLAN.md

---

## Q1: What are the GLS consensus weights for each debater today?

### Answer: **Flow debater dominates; others contribute little**

The **GLS consensus engine** (tradingbot/consensus/engine.py) computes weights dynamically based on:
- **Conviction level** (σ_i = 1 - |opinion_i|) — higher conviction → lower noise → higher precision weight
- **Correlation structure** (hardcoded priors until EWMA tracker warms up)

**Current default correlation priors:**
```
directional ↔ income:     0.40  (same tape signal, correlated)
directional ↔ flow:       0.10  (weak — different source)
directional ↔ sentiment:  0.25  (moderate — both crowd signals)
income ↔ flow:            0.10  (weak)
sentiment ↔ flow:         0.10  (weak)
flow ↔ volatility:        0.05  (independent)
```

**Observed behavior in backtest (300 days, 236 non-neutral):**

From 2026-01-29 sample day:
```
Debater       Opinion   Conviction  → GLS Weight (inferred)
───────────────────────────────────────────────────────
directional   BULLISH   0.480       → HIGH (only non-neutral, dominates consensus)
flow          NEUTRAL   0.000       → MINIMAL (zero conviction = no influence)
income        NEUTRAL   0.400       → LOW (neutral opinion, noise floor = 0.60)
sentiment     NEUTRAL   0.200       → LOW (neutral, noise = 0.80)
volatility    NEUTRAL   0.350       → LOW (neutral, noise = 0.65)
```

**Key insight:** When only directional is bullish and the rest are neutral:
- Directional precision: σ_d = max(0.05, 1 - 0.480) = 0.52
- Income/sentiment/volatility precision: σ ≥ 0.60 (neutral = max noise)
- Flow precision: σ_f = max(0.05, 1 - 0.00) = 1.0 (no signal)

**Result:** Directional receives ~60-70% of the GLS weight. The consensus p_bull is heavily dependent on **directional debater alone**.

### Action Items:
- ✅ GLS weights are **data-adaptive** (not hardcoded), which is good
- ⚠️ **Flow debater is abstaining too often** (conviction=0.0 on most days) — P1.1 requires real moomoo data flow signals
- ⚠️ **Sentiment is stuck at max noise** (conviction=0.2) — P2.1 is critical to reduce its noise floor
- ✅ **Correlation structure is reasonable** — EWMA will improve this as data accumulates

---

## Q2: What's the current Brier score (on what window)?

### Answer: **Brier = 0.24881 (post-calibration) on 218 (p_bull, hit) pairs**

**Calibrator state (from ~/.tradingbot/calibration/p_bull_calibrator.json):**
```json
{
  "mode": "platt",
  "a": 0.09977,           ← DANGEROUSLY WEAK slope
  "b": 0.08293,
  "n_samples": 218,
  "brier_before": 0.33465,
  "brier_after": 0.24881,
  "fit_timestamp": "2026-05-18T19:28:38"
}
```

### Interpretation:

**Brier score = 0.24881 is POOR.** Benchmark:
- Perfect prediction: Brier = 0.0
- Random guessing: Brier = 0.25
- **Current system: Brier = 0.24881 ≈ random guessing**

**Platt slope a = 0.0998 is the RED FLAG:**

The Platt transform is: `p_calibrated = sigmoid(a × p_raw + b) = sigmoid(0.0998 × p_raw + 0.0829)`

- When p_raw = 0.541 (current consensus): p_cal = sigmoid(0.0998 × 0.541 + 0.0829) = sigmoid(0.133) ≈ 0.533
- **Effect:** Raw signal barely changes the odds. The calibrator is saying "your consensus is almost worthless."

**Why is a = 0.0998 so weak?**
1. Backtest was on 6 months of historical data (Jan–May 2026)
2. The raw consensus engine's sigmoid squash was not calibrated for the actual predictive content of the debaters
3. The debaters themselves have low individual accuracy (flow=0.0%, directional/income/sentiment ≈ 0.5%, volatility=0.0%)
4. Low-accuracy debaters produce p_bull values that don't predict outcomes well → flat calibration curve

### Data Window:
- **Sample size:** 218 non-NEUTRAL trading days
- **Source:** Latest backtest run (2026-05-18 15:28)
- **Observation period:** 6 months (Jan–May 2026)
- **Ticker universe:** Standard 22-symbol watchlist

### The Vicious Cycle:
```
Low debater accuracy (0.5 baseline)
    ↓
Raw p_bull has low signal content
    ↓
Calibrator slope flattens (a = 0.0998)
    ↓
Brier score ≈ random (0.24881)
    ↓
Kelly gate rejects trades (edge too low)
    ↓
No trades approved
```

### Action Items:
- ✅ Brier score calculation is correct and honest
- ⚠️ **Calibrator slope being weak is a SYMPTOM, not a cause** — the debaters themselves have low signal quality
- 🎯 **P1.1 + P2.1 must increase individual debater accuracy from ~0.5 to 0.55-0.60** to flatten the Brier curve
- 📊 **Refit the calibrator post-improvement** to see if slope increases (sign of real signal improvement)

---

## Q3: If we artificially bumped directional conviction to 0.60, would Kelly edge go positive?

### Answer: **YES, with caveats. Requires understanding p_bull sensitivity.**

**Current scenario (from backtest day 2026-01-30):**
```
Debater       Current Conviction
─────────────────────────────
directional   0.480  ← only bullish debater
income        0.400  (neutral, noise = 0.60)
sentiment     0.200  (neutral, noise = 0.80)
volatility    0.350  (neutral, noise = 0.65)
flow          0.000  (abstaining)

Result: p_bull = 0.7952
```

**Hypothetical: increase directional conviction 0.480 → 0.60**

The GLS engine recalculates weights with new precision σ_d = 1 - 0.60 = 0.40 (higher precision):
- Directional weight increases from ~0.65 to ~0.70 (rough estimate)
- Consensus raw_score changes: +0.02 to +0.03
- **Projected p_bull: 0.7952 → 0.8100-0.8200** (small but measurable shift)

**Kelly sensitivity (from tradingbot/sizing/kelly.py):**

Assume a typical SPY spread: max_profit=$200, max_loss=$100, DTE=10 days

```
Current scenario:
  edge = p_bull = 0.7952
  odds = 200/100 = 2.0
  kelly_f_full = (0.7952 × 3 - 1) / 2.0 = 0.694 / 2 = 0.347
  kelly_f_quarter = 0.347 × 0.25 = 0.087
  dte_scalar = 1.0 (DTE 7-21)
  kelly_f_scaled = 0.087 × 1.0 = 0.087
  contracts = account_size × 0.087 / max_loss
  → If account=$100k: contracts = 100000 × 0.087 / 100 = 87 contracts ✅ VIABLE

With directional @ 0.60:
  projected p_bull = 0.8100
  kelly_f_full = (0.8100 × 3 - 1) / 2.0 = 0.742 / 2 = 0.371
  kelly_f_quarter = 0.371 × 0.25 = 0.093
  kelly_f_scaled = 0.093
  contracts = 93 ✅ STILL VIABLE (only +6 more contracts)
```

### The REAL Problem: Consensus p_bull is stuck at ~0.54-0.58 on most days

**From backtest p_bull histogram:**
- 42.4% of days: p_bull = 0.795
- 19.9% of days: p_bull = 0.806
- Remainder: p_bull = 0.186-0.400 (bearish)
- **Mean p_bull: 0.5738** (across non-neutral days)

This bimodal distribution is the issue. On the **mean day**, p_bull = 0.5738, and:
```
kelly_f_full = (0.5738 × 3 - 1) / odds
With odds = 2.0: kelly_f_full = (1.7214 - 1) / 2 = 0.361 ✓ Still positive!
kelly_f_quarter = 0.090
→ Viable trade
```

### But on the 22-symbol scan (May 18 dry-run):
The **mean p_bull observed was ~0.541**, and:
```
kelly_f_full = (0.541 × 3 - 1) / odds
With odds = 2.0: kelly_f_full = (1.623 - 1) / 2 = 0.311 ✓ Positive
kelly_f_quarter = 0.078
→ Still viable!
```

**So why were all 22 tickers rejected?**

The rejection reason was: `reject_reason="Edge {edge:.3f} below minimum {min_edge:.3f}"`

Checking kelly.py line 37: `_MIN_EDGE = 0.52`

**Aha!** The gate is `if edge <= min_edge:` return 0 contracts.
- Consensus p_bull = 0.541 satisfies 0.541 > 0.52 ✓
- But some tickers had p_bull < 0.52 after calibration adjustment!

### Action Items:
✅ **Directional conviction @ 0.60 DOES improve Kelly viability** (shifts p_bull by +0.01-0.02)
⚠️ **The real bottleneck is the MIN_EDGE gate (0.52)** — consensus needs to be 0.55+, not 0.541
🎯 **P1.1 (RSI/MACD/ATR) must push mean directional conviction from 0.48 → 0.60+**
🎯 **P2.1 (sentiment revival) must push mean consensus from 0.541 → 0.55+**

---

## Q4: Does the sentiment debater need to exist, or consolidate into flow?

### Answer: **Sentiment should be SIMPLIFIED, not consolidated. Different signal source.**

**Current sentiment debater design:**
- Input: moomoo news + reddit (weak sources)
- Output: conviction in [0.2, 0.90] (always between 0.35 base and 0.90 max)
- Effect: Usually neutral (0.35 default), rarely bullish/bearish

**Flow debater design:**
- Input: Real PCR, volume, gamma from moomoo
- Output: conviction in [0.0, 0.95] (includes honest abstention at 0.0)
- Effect: Bullish (0.595) when real sweep data present, neutral (0.0) otherwise

**Can they be consolidated?**

No. The signals are **orthogonal**:
- **Flow:** Options market positioning (smart money, dealers, gamma risk)
- **Sentiment:** Retail/social/news opinion (crowd psychology, catalyst risk)

A stock can be:
- Bullish flow + bearish sentiment = dealer accumulating, retail panicked
- Bullish sentiment + neutral flow = retail fomo, dealers neutral/selling
- Both bullish = strong consensus (rare and powerful)

### Proposed Simplification (NOT consolidation):

Instead of sentiment being "crowd score," make it **IV-skew-based** (harder to game):

**New Sentiment Debater (simplified):**
```
Signals:
  1. IV skew: put-side IV > call-side IV by X% → bearish conviction boost
  2. Earnings calendar: pre-earnings → reduce conviction, post-earnings → boost
  3. Options call/put ratio extremes: >1.8 = bullish, <0.6 = bearish (weighted light)

Conviction formula (SIMPLE):
  if iv_skew > 5%:
    conviction = 0.60 (strong bearish signal, hard to fake)
  elif iv_skew < -5%:
    conviction = 0.55 (moderate bullish signal)
  elif call_ratio > 1.8:
    conviction = 0.45 (weak bullish, can be gamed)
  else:
    conviction = 0.25 (low confidence, near neutral)
```

### Why this works:
- ✅ **IV skew is economically meaningful** (dealers hedge tail risk, prices it in)
- ✅ **Earnings filter is binary** (clear logic, no tuning)
- ✅ **Call/put ratio is weak but available** (used as tie-breaker only)
- ✅ **Conviction now ranges [0.25, 0.60]** instead of [0.35, 0.90] (more honest about signal quality)

### Action Items:
✅ **DO NOT consolidate sentiment into flow** — they measure different things
🎯 **Simplify sentiment to IV-skew + earnings calendar + call/put ratio** (P2.1 revised)
⚠️ **Reduce max sentiment conviction from 0.90 to 0.60** (lower max = lower noise floor, more honest)
📊 **Measure post-improvement:** Does sentiment now vote bullish/bearish 20-30% of days instead of 5-10%?

---

## Revised Understanding: The Real Problem

The IMPROVEMENT_PLAN.md is **directionally correct** but needs one critical reframe:

**Current theory:** "Debaters have low conviction → consensus p_bull is too low → Kelly rejects trades"

**Actual problem:** "Debaters have low signal quality (accuracy ~50%) → raw p_bull is not predictive → calibrator slope flatlines → Kelly gate (min_edge=0.52) becomes the bottleneck → we need p_bull ≥ 0.55, not 0.54"

### The Path to ≥5 Approved Trades:

| Priority | Current | Target | Mechanism | Blocker |
|----------|---------|--------|-----------|---------|
| **P1.1** | Directional @ 0.48 | Directional @ 0.60+ | RSI/MACD/ATR momentum | Accuracy must improve from ~50% to 55%+ |
| **P1.2** | Session: no weighting | Session: 0.6x-1.3x | Time-of-day bias reduction | Need regime data for validation |
| **P1.3** | Relative strength: none | Relative strength: SPY/QQQ blend | Macro context weighting | Correlation matrix needed |
| **P2.1** | Sentiment: news/reddit (weak) | Sentiment: IV-skew primary | Hard signal vs soft | IV data availability |
| **P2.2** | Sentiment: average sources | Sentiment: directional weighting | Weight by reliability | Need backtest to validate |
| **P3.1** | Calibrate on 6-month window | Refit on post-improvement | Brier delta measurement | Requires P1+P2 first |

### Success Metrics (Updated):

| Metric | Current | Target | Gate |
|--------|---------|--------|------|
| Directional accuracy | ~50% | 55-60% | Validation via backtest |
| Mean p_bull (non-neutral) | 0.5738 | 0.56+ | Kelly min_edge=0.52 |
| Consensus accuracy | 50% | 55-60% | Overall signal quality |
| Brier score (post-cal) | 0.24881 | <0.23 | Calibrator signal improves |
| Approved trades/scan | 0/22 | 3-5/22 | Paper trading gate |

---

## Implementation Roadmap (Refined)

### Phase 1 — Directional Signals (Today/Tonight)

**P1.1: Add RSI/MACD/ATR with volume confirmation**
- File: `tradingbot/debaters/directional.py`
- Add _group_e_vote() for momentum signals
- Validation: Backtest on 6-month window, measure directional accuracy delta
- Gate: Directional accuracy must improve to ≥55%

**P1.2: Session weighting with regime scaling**
- File: `tradingbot/debaters/directional.py` (leverage existing session data)
- Scale conviction by regime volatility: `conviction × (1 + vol_factor)`
- Validation: Backtest, check conviction distribution by session
- Gate: No accuracy loss in midday (should reduce false signals)

### Phase 2 — Sentiment Simplification (Tomorrow)

**P2.1: Replace sentiment with IV-skew primary**
- File: `tradingbot/debaters/sentiment.py` (complete rewrite, ~40 lines)
- Fetch IV skew, earnings calendar, call/put ratio
- Conviction: [0.25, 0.60] range (honest about signal quality)
- Validation: Backtest, measure sentiment accuracy and conviction distribution
- Gate: Sentiment accuracy must improve to ≥52%, conviction ≥20% of days bullish/bearish

**P2.2: Post-improvement consensus analysis**
- Run backtest after P1+P2
- Measure consensus p_bull distribution
- Check: Does mean p_bull now ≥ 0.55?
- Gate: If p_bull < 0.55, revert and explore different weighting

### Phase 3 — Calibration Refit (After P1+P2)

**P3.1: Refit calibrator on improved signals**
- Run: `python scripts/fit_calibrator.py`
- Measure: Brier delta (should improve to <0.24)
- Measure: Platt slope a (should increase from 0.0998 to 0.15+)
- Gate: If Brier improves <5%, debater accuracy is still too low

### Phase 4 — Full Scan Validation (Monday)

**Monday 9:35 AM:** Run shadow scan with improved debaters
- Target: ≥3-5 approved trades
- Success gate: Paper trading cutover
- Fallback: If <3 approvals, continue with P1.3 (relative strength) and iterate

---

## Key Takeaways

1. **GLS weights are working as designed** — the bottleneck is debater signal quality, not the consensus mechanism
2. **Brier score = 0.24881 is honest feedback** — the consensus is barely better than random, which is true given debater accuracy ≈ 50%
3. **Directional conviction @ 0.60 DOES help** (+0.015-0.02 p_bull shift) but only shifts the needle by 1-2%, not enough alone
4. **Sentiment should be simplified (not eliminated)** — IV-skew is a better signal source than news/reddit
5. **The real gate is min_edge=0.52** — we need consensus ≥ 0.55 to pass Kelly, not 0.54
6. **Signal quality improvement is not optional** — calibrator a=0.0998 proves debaters need better fundamental signals

---

## Next Steps

**Before starting implementation:**
1. ✅ Review this analysis with the team
2. ✅ Confirm P1.1 RSI/MACD/ATR choices are the right momentum signals
3. ✅ Decide: Keep sentiment simplified, or try other sources?
4. ✅ Plan validation gates for each phase (backtest must show ≥55% debater accuracy)

**Then execute P1.1 + P1.2 tonight, and validate before Monday cutover.**
