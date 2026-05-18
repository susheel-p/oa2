# Signal Quality Improvement Plan

**Current State (May 18, 2026):**
- Flow debater: ✅ BULLISH @ 0.595 (strong, real moomoo data)
- Directional debater: ❌ 0.48-0.50 avg conviction, ~50% accuracy (weak, mixed direction)
- Sentiment debater: ❌ 0.2-0.35 conviction, ~50% accuracy (silent, no real signal)
- Consensus p_bull: 0.574 mean (non-neutral days), bottleneck at min_edge=0.52
- Calibrator: a=0.0998 (dangerously weak slope), Brier=0.24881 (≈ random guessing)
- **Result:** 0/22 trades approved (all rejected on Kelly min_edge gate)

**Root Cause:** Debater accuracy is ~50% baseline → raw p_bull not predictive → calibrator flattens → Kelly gate (min_edge=0.52) becomes binding constraint

---

## Critical Findings (Before Implementation)

### 1. GLS Consensus Weights Analysis
The GLS engine computes weights dynamically based on conviction (precision) and correlation:
- **Directional dominates:** When only directional is bullish, it receives ~65-70% of consensus weight
- **Flow abstains:** On most backtest days, flow conviction=0.0 → minimal weight contribution
- **Sentiment stuck at noise:** Conviction=0.2-0.35 → noise floor σ≥0.65 → minimal influence
- **Implication:** Consensus p_bull currently depends almost entirely on directional debater accuracy (~50%)

### 2. Calibrator Brier Score & Platt Slope
Current calibrator state (as of 2026-05-18 19:28):
```
Mode:           platt
Samples:        218 (p_bull, hit) pairs from 6-month backtest
Platt slope (a): 0.0998  ← DANGEROUSLY WEAK (should be 0.5+)
Platt intercept: 0.0829
Brier before:   0.33465
Brier after:    0.24881  ← EQUALS RANDOM GUESSING (0.25)
```

**Why a=0.0998 is critical:** This means the calibrator is saying "your p_bull signal is almost worthless." Even if consensus says 0.55, calibrator squashes it back to 0.53 (minimal change). The weak slope is a symptom of low debater accuracy (Brier ≈ random means p_bull doesn't predict outcomes).

### 3. Kelly Edge Sensitivity Analysis
Tested: "If directional conviction goes 0.48 → 0.60, does Kelly edge flip positive?"

**Calculation:**
```
Current (directional @ 0.48):
  Consensus p_bull: 0.7952 (when directional alone is bullish)
  Kelly f* = (0.7952 × 3 - 1) / 2.0 = 0.347 (positive)
  
Projected (directional @ 0.60):
  Consensus p_bull: 0.8100-0.8200 (shift +0.015 to +0.025)
  Kelly f* = (0.8100 × 3 - 1) / 2.0 = 0.371 (still positive, only +7% more)
```

**Key insight:** Directional conviction improvement shifts p_bull by ~1-2%, which is **insufficient alone**. The real bottleneck is the Kelly min_edge=0.52 gate. We need:
- Mean consensus p_bull ≥ 0.55 (not 0.541) to reliably pass Kelly on most tickers
- This requires BOTH directional + sentiment improvements working together

### 4. Sentiment Debater — Simplify, Don't Eliminate
Current sentiment sources (news + reddit) are weak and noisy. Options:
1. ❌ **Eliminate sentiment:** No, flow + directional + IV signals are orthogonal. Sentiment adds real information.
2. ✅ **Simplify sentiment to IV-skew primary:** IV skew is harder to game, empirically meaningful, available real-time.

**Proposed sentiment redesign:**
- Primary: IV put-skew > call-skew by >5% → conviction boost (economic signal)
- Secondary: Earnings calendar filter (binary: pre-earnings reduce, post-earnings boost)
- Tertiary: Call/put ratio extremes only as tiebreaker (can be gamed)
- **Conviction range:** [0.25, 0.60] (more honest than current [0.35, 0.90])

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

## Priority 2: Simplify Sentiment Debater to IV-Skew Primary (Medium Impact)

**Current issue:** Conviction stuck at 0.2-0.35 (near neutral), accuracy ~50%.  
**Root cause:** News + reddit sources are weak and noisy. Sentiment needs a harder signal source.

### P2.1 — Replace sentiment sources with IV-skew + earnings calendar
**File:** `oa2/debaters/sentiment.py`

Rewrite sentiment debater to focus on options market structure (harder to game):

```python
# New sentiment logic:
if earnings_within_5_days:
    if earnings_within_1_day:
        conviction = 0.20  # Pre-earnings vol crush risk — reduce conviction
    else:
        conviction = 0.25  # Next few days — neutral, wait for event
else:
    # Earnings clear — use options market signals
    if iv_put_skew - iv_call_skew > 5.0:  # Institutions hedging downside
        direction = BEARISH
        conviction = 0.55  # Strong bearish signal, hard to fake
    elif iv_call_skew - iv_put_skew > 5.0:  # Call buying spike
        direction = BULLISH
        conviction = 0.45  # Weaker bullish, can be retail fomo
    elif call_put_ratio > 1.8:  # Call heavy
        direction = BULLISH
        conviction = 0.30  # Weak signal, use only as tiebreaker
    elif call_put_ratio < 0.6:  # Put heavy
        direction = BEARISH
        conviction = 0.30  # Weak signal, use only as tiebreaker
    else:
        direction = NEUTRAL
        conviction = 0.25  # Ambiguous options market
```

**Why this works:**
- ✅ **IV skew is economically meaningful** (dealers hedge tail risk, prices it in)
- ✅ **Earnings filter is binary** (clear logic, no tuning)
- ✅ **Call/put ratio is weak but available** (used only as tiebreaker, max 0.30 conviction)
- ✅ **Conviction now honest:** [0.20, 0.55] range (vs. [0.35, 0.90] before)

**Data sources:**
- IV skew: yfinance (free tier has historical, needs real-time integration)
- Earnings calendar: yfinance or free alternative
- Call/put ratio: moomoo data we already fetch

**Expected impact:** 
- Accuracy should improve to ≥52% (from ~50%)
- Conviction distribution: 20-30% bullish/bearish days (vs. 5% currently)
- Mean conviction: 0.35-0.40 (higher signal participation)

---

### P2.2 — Validation: Post-improvement sentiment accuracy
After P2.1 implementation, backtest to measure:
- Does sentiment accuracy improve to ≥52%?
- Does sentiment vote bullish/bearish ≥20% of days?
- Does consensus p_bull now include sentiment weight meaningfully?

**Gate:** If sentiment accuracy <52%, revert and try alternative source (StockTwits or options flow).

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

## Phased Implementation Roadmap

| Priority | Task | Effort | Blocker? | Impact | Timeline | Validation |
|----------|------|--------|----------|--------|----------|-----------|
| **1.1** | RSI + MACD + ATR momentum | 1-2 hrs | YES | High | Today 11pm | Directional acc ≥55% |
| **1.2** | Session weighting + regime scaling | 1 hr | YES | Medium | Today 11pm | No midday regression |
| 1.3 | Relative strength to SPY/QQQ | 2 hrs | NO | Medium | Tomorrow | Mean p_bull trend |
| **2.1** | IV-skew sentiment rewrite | 2 hrs | YES | Medium | Tomorrow 9am | Sentiment acc ≥52% |
| 2.2 | Earnings calendar integration | 1 hr | Medium | Low | Tomorrow | Toggle on/off |
| **3.1** | Refit calibrator post-improvements | 15 min | Auto | High | Tomorrow 11am | Brier delta ≥5% |
| 4.1 | Income trade-structure scoring | 4 hrs | NO | Low | Next week | Complex, defer |
| 4.2 | Volatility term structure | 4 hrs | NO | Low | Next week | Complex, defer |

**Legend:**  
- **Blocker?** = Required for Monday cutover (YES/NO)
- **Impact** = Signal quality or approval rate impact
- **Validation** = Backtest gate before proceeding

---

## Success Metrics

| Metric | Current | Target | Validation Gate |
|--------|---------|--------|------|
| Directional accuracy | ~50% | 55-60% | Backtest after P1.1 |
| Directional conviction | 0.48 avg | 0.60+ avg | Backtest distribution |
| Sentiment accuracy | ~50% | 52%+ | Backtest after P2.1 |
| Sentiment conviction | 0.20 avg | 0.35-0.40 avg | Backtest, 20%+ bullish/bearish days |
| Consensus accuracy | 50% | 54-56% | Backtest after P1+P2 |
| **Mean p_bull (non-neutral)** | **0.5738** | **0.56+** | **Must clear Kelly min_edge=0.52** |
| Calibrator Platt slope (a) | 0.0998 | 0.15-0.20 | Refit after improvements |
| Calibrator Brier score | 0.24881 | <0.24 | Indicates signal quality improved |
| Approved trades per scan | 0/22 | **3-5/22** | Monday 9:35 AM validation |

---

## Implementation Roadmap (Detailed)

### Phase 1 — Directional Signal Improvement (Tonight)

**P1.1: Add RSI/MACD/ATR momentum with volume confirmation**
- File: `oa2/debaters/directional.py` (add _group_e_vote function)
- Signals:
  - RSI oversold/overbought (RSI < 30 = +1, > 70 = -1, else 0)
  - MACD trend (histogram positive/negative)
  - ATR breakout (price > 20-day high = +1)
  - Volume surge (vol > 2× MA = confirms direction)
- Conviction formula: base 0.40 + 0.12 per signal group in agreement, ×1.15 if all 4 agree, capped at 0.75 (down from 0.90)
- Backtest after: Measure directional accuracy delta, expect ≥55%
- **Gate:** Directional accuracy must improve to ≥55% or revert

**P1.2: Session weighting with regime volatility scaling**
- File: `oa2/debaters/directional.py` (wire session overlay into conviction)
- Session bias: 9:30-10:00 ET = 1.2× (volatile, trending), 12:00-14:00 ET = 0.7× (consolidation)
- Volatility scaling: In high-VIX regimes (>30), reduce session bias (markets choppier)
- Backtest after: Check conviction distribution by session, ensure no accuracy regression in midday
- **Gate:** No accuracy loss in midday consolidation hours

### Phase 2 — Sentiment Simplification (Tomorrow)

**P2.1: Replace sentiment with IV-skew + earnings calendar (code above)**
- File: `oa2/debaters/sentiment.py` (complete rewrite, ~50 lines)
- Data sources: yfinance (IV skew), free earnings calendar, moomoo (call/put ratio)
- Backtest after: Measure sentiment accuracy delta, expect ≥52%
- **Gate:** Sentiment accuracy must improve to ≥52%

**P2.2: Post-improvement consensus analysis**
- Run backtest with both P1 and P2 complete
- Measure: Consensus p_bull distribution (mean, std)
- Check: Does mean p_bull ≥ 0.56 on non-neutral days?
- Check: Does consensus accuracy ≥54%?
- **Gate:** If mean p_bull < 0.555, debug and iterate

### Phase 3 — Calibration Refit (After P1+P2)

**P3.1: Refit calibrator and measure Brier improvement**
- Run: `python scripts/fit_calibrator.py`
- Measure Brier delta: (0.24881 - new_brier) / 0.24881 (expect ≥5% improvement)
- Measure Platt slope: (new_a - 0.0998) / 0.0998 (expect ≥30% improvement, a → 0.13+)
- **Gate:** If Brier doesn't improve 5%+, debater accuracy is still too low; return to P1

### Phase 4 — Validation & Cutover (Monday)

**Monday 9:35 AM: Full shadow scan with improved debaters**
- Run 22-symbol scan with P1+P2+P3 complete
- Target: ≥3-5 approved trades (vs. 0/22 currently)
- Success: Deploy to live paper trading
- Fallback <3 approvals: Debug consensus p_bull distribution, may need P1.3 (relative strength) immediately

---

## Next Steps (Order Matters)

1. **Today/Tonight (this evening):** Implement P1.1 (RSI/MACD/ATR) and P1.2 (session weighting)
   - Commit changes to feature branch, run backtest
   - Validate: directional accuracy ≥55%, no session regression
   
2. **Tomorrow morning:** Implement P2.1 (IV-skew sentiment) and run full backtest
   - Validate: sentiment accuracy ≥52%, mean p_bull ≥0.56
   - Refit calibrator (P3.1), measure Brier delta
   
3. **Tomorrow afternoon:** P1.3 if time permits (relative strength vs SPY/QQQ)
   - Adds macro context weighting to directional debater
   
4. **Monday 9:35 AM:** Deploy P1+P2+P3, run shadow scan
   - Target: 3-5 approvals → paper trading cutover
   - If <3: Debug mean p_bull, investigate Kelly gate tuning
