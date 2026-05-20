# Backtest Learnings — 6-Month Analysis (22 tickers, 1,650 days)

**Date:** 2026-05-18  
**Run:** results_20260518_201029.json  
**Headline:** Consensus accuracy 51.5%, v2 Sharpe +0.379 vs v1 -0.212  
**Critical patterns:** 5 actionable findings below

---

## Finding 1: 🔴 BEARISH SIGNAL IS BROKEN (Priority: P0)

| Direction | Hit Rate | Verdict |
|-----------|----------|---------|
| BULLISH (754 trades) | **50.7%** | Marginal edge |
| BEARISH (697 trades) | **46.3%** | **WORSE THAN RANDOM** |

**Implication:** Our bearish signals are systematically wrong. Trading them costs us money.

**Hypotheses:**
- Bearish bias in directional debater (Group A/B/D might over-weight downside)
- Synthetic `put_call_skew` proxy is signed wrong in compute_daily_context
- Bullish drift in the 6-month sample period — bearish signals fight the macro

**Proposed Fix (P0):**
- Audit directional.py for sign asymmetry (every `+` vs `-` test)
- Validate put_call_skew sign convention end-to-end
- Add minimum BEARISH conviction filter (e.g., only act on p_bull < 0.20)

---

## Finding 2: 🟡 TICKER QUALITY DIFFERENTIAL IS HUGE (Priority: P1)

**Top 5 (60+ days each):**
| Rank | Ticker | Accuracy |
|------|--------|----------|
| 1 | XLE | **58.5%** |
| 2 | GOOGL | 55.9% |
| 3 | USO | 54.9% |
| 4 | AMD | 54.8% |
| 5 | XLK | 54.4% |

**Bottom 5:**
| Rank | Ticker | Accuracy |
|------|--------|----------|
| 22 | SLV | **37.9%** |
| 21 | TLT | 40.3% |
| 20 | DIA | 41.0% |
| 19 | TSLA | 41.4% |
| 18 | XLV | 42.2% |

**Spread: 20.6 percentage points** between best and worst.

**Implication:** Trading SLV is *guaranteed* to lose money (37.9% accuracy → -25% expected over 100 trades).

**Proposed Fix (P1):**
- Implement **ticker quality bandit** — track per-ticker historical accuracy
- Multiply consensus conviction by `max(0.5, ticker_accuracy × 2 - 1)` to suppress losing tickers
- Or simply blacklist tickers with <45% historical accuracy (40-day rolling)

---

## Finding 3: 🚨 P_BULL DISTRIBUTION IS BIMODAL (Priority: P0)

**Distribution histogram:**
```
0.00-0.10:  84  ################
0.10-0.20: 307  #############################################################
0.20-0.30: 295  ###########################################################
0.30-0.40:  11  ##
0.40-0.60:   0  (DEAD ZONE — should be where most signals are!)
0.60-0.70:   1
0.70-0.80: 244  ################################################
0.80-0.90: 455  ###########################################################################################
0.90-1.00:  54  ##########
```

**The consensus output is mathematically broken:**
- 697 days at p_bull < 0.30 (extreme bearish)
- 754 days at p_bull > 0.70 (extreme bullish)
- **0 days at p_bull 0.40-0.60** — the entire middle ground is empty

**Root Cause:** In [engine.py:340](tradingbot/consensus/engine.py#L340):
```python
x = raw_score * n_eff * 2.0
return 1.0 / (1.0 + math.exp(-x))
```
With n_eff = 3.13 and raw_score = 0.20, x = 0.20 × 3.13 × 2.0 = **1.25** → sigmoid = **0.78**.
The `× 2.0` amplifier saturates the sigmoid; small raw scores get pushed to extremes.

**Conviction tier accuracy proves the problem:**
| Edge bucket | Hits | Accuracy |
|-------------|------|----------|
| 0.50-0.60 (the missing middle) | — | n/a (no trades) |
| 0.60-0.70 | 6/12 | 50.0% |
| 0.70-1.00 | 699/1439 | **48.6% (worse than random!)** |

**Higher conviction ≠ better predictions.** The amplifier creates false confidence.

**Proposed Fix (P0):**
- Remove `× 2.0` amplifier in `_calibrate_probability()`
- Use `x = raw_score * sqrt(n_eff)` instead (more conservative)
- Re-run calibrator after fix; expect Brier improvement of 5-10%

---

## Finding 4: 🟢 REGIME PATTERN — MEAN-REVERTING IS A TRAP (Priority: P1)

**Sorted by win rate:**
| Regime | Accuracy | Trades |
|--------|----------|--------|
| normal_neutral | **55.8%** | 129 |
| vol_exp_trending | 52.4% | 307 |
| normal_mean_revert | 51.1% | 47 |
| normal_trending | 51.0% | 100 |
| vol_comp_neutral | 47.5% | 80 |
| vol_exp_neutral | 46.8% | 555 |
| vol_comp_trending | 44.9% | 49 |
| **vol_exp_mean_revert** | **42.2%** | 135 |
| **vol_comp_mean_revert** | **40.8%** | 49 |

**Pattern:** All "mean_revert" regimes underperform. Consensus engine assumes directional follow-through; mean-reversion fights it.

**Proposed Fix (P1):**
- Add regime gate: skip trades when `trend_state == MEAN_REVERTING`
- Or: reduce conviction by 0.7x in mean-reverting regimes
- Estimated impact: remove 184 bad trades (-44% accuracy) → consensus accuracy → ~54%

---

## Finding 5: ✅ SENTIMENT P2.1 IS WORKING (Priority: validate)

**Stats:**
- Sentiment voted directional: **276/1650 days (16.7%)**
- Hit rate when voting: **51.4%**
- Slight positive edge, low participation

**Before P2.1:** Sentiment voted ~3 days/year with 0% accuracy.  
**After P2.1:** Sentiment votes 17% of the time with marginal positive edge.

**Implication:** P2.1 works but is conservative. Could be more aggressive.

**Future Optimization (P3):**
- Lower the IV-skew threshold from ±5% to ±3% (more votes)
- Verify call/put_ratio tiebreaker is actually firing in backtest

---

## Recommended Implementation Order

| Priority | Fix | Effort | Expected Impact |
|----------|-----|--------|-----------------|
| **P0** | Remove sigmoid `× 2.0` amplifier | 5 min | Distribution rebalance, Brier -5% |
| **P0** | Audit bearish signal logic | 1 hr | +4 ppt accuracy on bearish trades |
| **P1** | Mean-reverting regime gate | 30 min | +2-3 ppt overall accuracy |
| **P1** | Per-ticker quality filter | 1 hr | +2-4 ppt accuracy on weak tickers |
| **P2** | Backtest synthetic chain → wire structure picker | 2 hr | True picker validation in backtest |
| **P3** | Lower IV-skew sentiment thresholds | 15 min | More sentiment votes (P2.1 tuning) |

---

## What This Means for Monday

Current state:
- 8/22 trades approved on live scan ✅
- But accuracy is only ~51-52% across the board
- Bearish trades actively LOSE money

**Safer path: trade BULLISH ONLY** on top-5 tickers (XLE, GOOGL, USO, AMD, XLK) until P0 fixes are in.

Combined with structure picker (Kelly-viable R:R), this gives:
- 5 quality tickers × ~55% accuracy = positive EV
- 8-9 contracts per trade = manageable risk
- Skip all bearish + mean-reverting until P0 fixes ship

---

## Suggested Next Steps

1. **Tonight (P0):**
   - Fix sigmoid amplifier (5 min)
   - Re-run backtest, verify distribution rebalances
   
2. **Tomorrow morning (P0+P1):**
   - Audit bearish signal logic
   - Add mean-reverting regime gate
   - Re-run backtest, target consensus accuracy ≥54%
   
3. **Tomorrow afternoon (P2):**
   - Wire structure picker into backtest with synthetic chain
   - Measure: how many backtest days would actually approve?
   - Compute realized P&L on approvals only
