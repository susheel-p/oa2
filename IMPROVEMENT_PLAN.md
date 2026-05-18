# Signal Quality Improvement Plan

**Current State (May 18, 2026):**
- Flow debater: ✅ BULLISH @ 0.595 (strong, real moomoo data)
- Directional debater: ❌ 0.39-0.48 conviction (weak, mixed direction)
- Sentiment debater: ❌ 0.2 conviction (virtually silent)
- Consensus p_bull: ~0.541 (need 0.55+ for Kelly approval)
- **Result:** 0/22 trades approved (all rejected on Kelly edge gate)

---

## Priority 1: Strengthen Directional Debater (High Impact)

**Current issue:** Conviction only 0.39-0.48, not decisive enough.

### P1.1 — Add real-time technical momentum signals
**File:** `oa2/debaters/directional.py`

Replace current EMA-crossover logic with:
- **RSI oversold/overbought** (RSI < 30 = bullish, > 70 = bearish) with conviction scaling
- **MACD trend** (histogram positive = bullish, negative = bearish)
- **ATR breakout** (price > 20-day high = bullish momentum)
- **Volume surge** (volume > 2× MA = confirms direction)

**Expected impact:** Conviction should increase to 0.65-0.75 on trending days.

---

### P1.2 — Add intraday session context
**File:** `oa2/regime/classifier.py` (session overlay already exists)

Wire session state into directional debater:
- **Market open (9:30-10:00 ET):** Usually volatile, higher conviction bias
- **Morning (10:00-12:00):** Trend-following bias
- **Midday (12:00-14:00):** Lower conviction (consolidation)
- **Power hour (15:00-16:00):** Option expiry gamma effects

**Expected impact:** Conviction adjusted 0.6x to 1.3x based on session, removes midday false signals.

---

### P1.3 — Add relative strength vs macro
**File:** `oa2/debaters/directional.py` (new)

Fetch SPY/QQQ trend and scale single-name conviction:
- If SPY is up 2%+ today and ticker is up 1%: increase conviction
- If SPY is down 1% but ticker is up 1%: **strong relative** → conviction boost
- If ticker and SPY diverging sharply: possible reversal signal

**Expected impact:** Conviction 0.6-0.75 on cross-asset relative strength plays.

---

## Priority 2: Revive Sentiment Debater (Medium Impact)

**Current issue:** Conviction stuck at 0.2 (max), always NEUTRAL.

### P2.1 — Wire real sentiment sources
**File:** `oa2/debaters/sentiment.py`

Currently using moomoo news + reddit (both weak). Add:
- **StockTwits sentiment score** (bullish/bearish ratio per ticker)
- **Options flow extremes** (call/put ratio > 1.5 or < 0.6 = high conviction)
- **IV skew** (put-skew > 5% above call-skew = bearish institutional hedge)

**Expected impact:** Conviction 0.4-0.6 when real sentiment data present.

---

### P2.2 — Replace naive averaging with directional weighting
**File:** `oa2/debaters/sentiment.py`

Current: All sources weighted equally → neutral result.
New: Weight sources by reliability + current direction:
```
if stocktwits_bullish_ratio > 0.65:
    conviction = 0.65 * (bullish_ratio - 0.5) × 2  # Scale from 0.5-1.0 → 0.65 max
else if options_call_ratio > 1.8:
    conviction = 0.55 × (call_ratio - 1.0) × 0.5   # Less aggressive than twits
else:
    conviction = 0.2 (default neutral)
```

**Expected impact:** Conviction 0.4-0.65 when any source shows real conviction.

---

### P2.3 — Add earnings surprise context
**File:** `oa2/debaters/sentiment.py` (new)

Next 5 trading days: earnings calendar check
- **Pre-earnings:** Reduce conviction (vol crush risk)
- **Post-earnings (same day):** Sentiment highly correlated, boost it
- **Earnings next week:** Normal weighting

**Expected impact:** Avoid false signals around earnings, boost signal 2-3 days after.

---

## Priority 3: Calibrator Refit (Quick Win)

**Current:** Platt slope a=0.10 (signal barely informative).

### P3.1 — Refit on recent 20-day window
**File:** `scripts/fit_calibrator.py`

Current: Uses 6-month backtest. Try:
- Last 20 trading days only (more recent market regime)
- Filter out earnings day signals (high noise)
- Separate by regime (maybe calibration differs for vol-high vs vol-normal)

**Expected impact:** Platt slope a=0.15-0.20 (modest improvement, honest assessment).

---

## Priority 4: Income & Volatility (Lower Priority)

**Current:** Both NEUTRAL @ 0.35-0.4, not moving needle.

### P4.1 — Income debater: Add collar/spread scoring
Instead of just "IV is expensive/cheap", score how good the specific trade structure is:
- Long call in cheap IV: approval
- Short call in cheap IV: rejection (bad risk/reward)
- Iron condor in expensive IV: strong approval

### P4.2 — Volatility debater: Add volatility term structure
- Upward slope (VIX > VIX3M): sell premium bias
- Inverted (VIX < VIX3M): buy volatility bias
- Add VVIX breadth (cluster volatility across names)

---

## Implementation Roadmap

| Priority | Task | Effort | Impact | Owner | Timeline |
|----------|------|--------|--------|-------|----------|
| 1.1 | RSI + MACD + ATR technical momentum | Medium | High | You | Today |
| 1.2 | Session context weighting | Small | Medium | You | Today |
| 1.3 | Relative strength to SPY/QQQ | Medium | High | You | Tomorrow |
| 2.1 | StockTwits + IV skew integration | Medium | Medium | You | Tomorrow |
| 2.2 | Directional weighting in sentiment | Small | Medium | You | Tomorrow |
| 2.3 | Earnings calendar filtering | Small | Low | You | Later |
| 3.1 | Recalibrate on 20-day window | Small | Low | Auto | Monday |
| 4.1 | Income trade-structure scoring | Large | Low | Later | Later |
| 4.2 | Volatility term structure | Large | Low | Later | Later |

---

## Success Metrics

| Metric | Current | Target | Gate |
|--------|---------|--------|------|
| Directional conviction | 0.39-0.48 | 0.60-0.75 | P1.1 + P1.2 |
| Sentiment conviction | 0.2 | 0.40-0.60 | P2.1 + P2.2 |
| Consensus p_bull | 0.541 | 0.55-0.60 | P1+P2 combined |
| Kelly edge gate | -0.148 | +0.10-0.20 | Pass sizing gate |
| Approved trades per scan | 0/22 | 3-5/22 | Monday cutover |

---

## Next Steps

1. **Today/Tonight:** Implement P1.1 (RSI+MACD) and P1.2 (session weighting)
2. **Tomorrow morning:** Implement P2.1 (StockTwits) and P1.3 (relative strength)
3. **Monday 9:35 AM:** Run full scan with improved debaters, verify ≥3 approved trades
4. **Monday cutover:** If ≥5 approvals/scan, deploy to live paper trading
