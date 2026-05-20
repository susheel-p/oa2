# Execution Summary — Signal Quality Improvement Plan

**Status:** Ready for implementation  
**Timeline:** Tonight + Tomorrow + Monday validation  
**Owner:** You  
**Success gate:** 3-5 approved trades on Monday 9:35 AM scan (vs. 0/22 currently)

---

## The Problem (One Sentence)

**GLS consensus is barely better than random (Brier=0.24881) because all debaters have ~50% accuracy → Kelly gate rejects all 22 tickers due to p_bull=0.541 < min_edge threshold.**

---

## Root Cause Chain

```
Debater accuracy ≈ 50% (baseline, no signal)
  ↓
Raw p_bull ≈ 0.54-0.57 (barely bullish)
  ↓
Calibrator slope a = 0.0998 (flattened, p_bull barely moves)
  ↓
Kelly gate: min_edge = 0.52 (rejects p_bull < 0.52)
  ↓
Mean p_bull = 0.574 (passes gate barely)
  ↓
But on 22-ticker scan: Some tickers p_bull < 0.52 after calibration adjustment
  ↓
Result: 0/22 approved trades
```

**Solution:** Improve debater accuracy to 55-60% → push mean p_bull to 0.56+ → Kelly passes reliably

---

## Critical Findings (Executive Summary)

### Q1: GLS Weights
- **Directional dominates** (65-70% weight) — consensus depends almost entirely on directional accuracy
- **Flow abstains too often** (conviction=0.0) — needs stronger moomoo signal integration
- **Sentiment near max noise** (conviction=0.2) → weight is minimal

**Action:** Improve directional + sentiment signal quality

### Q2: Brier Score & Calibrator
- **Brier = 0.24881** (post-calibration) — equals random guessing
- **Platt slope a = 0.0998** — dangerously weak, says "your signal is worthless"
- **Root cause:** Debater accuracy ~50% makes p_bull non-predictive

**Action:** Increase debater accuracy from 50% to 55%+ will flatten Brier curve naturally

### Q3: Kelly Sensitivity
- Increasing directional conviction 0.48 → 0.60 shifts p_bull by only +0.015-0.025
- **Insufficient alone** — need both directional + sentiment improvements
- **Real bottleneck:** Kelly min_edge=0.52, we need p_bull ≥ 0.55 consistently

**Action:** Combine P1.1 (directional) + P2.1 (sentiment) to hit 0.56+ mean p_bull

### Q4: Sentiment Simplification
- ✅ **DO NOT eliminate sentiment** — flow and sentiment are orthogonal signals
- ✅ **Simplify to IV-skew + earnings calendar** — harder to game than news/reddit
- Conviction range should be [0.25, 0.60] (honest about signal quality, not [0.35, 0.90])

**Action:** Rewrite sentiment.py to use options market structure signals

---

## Implementation Roadmap (3 Phases)

### PHASE 1: Tonight — Directional Improvements (P1.1 + P1.2)

**P1.1 — Add RSI/MACD/ATR Momentum (File: tradingbot/debaters/directional.py)**

Add new signal group (Group E) for momentum oscillators:
```python
def _group_e_vote(rsi: float, macd_histogram: float, price: float, ma20: float) -> int:
    """Momentum signals (RSI, MACD, ATR breakout)."""
    bull, bear = 0, 0
    
    if rsi < 30:
        bull += 1  # Oversold = reversal bullish
    elif rsi > 70:
        bear += 1  # Overbought = reversal bearish
    
    if macd_histogram > 0:
        bull += 1  # Positive momentum
    elif macd_histogram < 0:
        bear += 1
    
    # (ATR breakout logic already in Group A, don't double-count)
    
    return 1 if bull > bear else (-1 if bear > bull else 0)
```

Update conviction formula to include Group E:
- Base: 0.40
- Per group in agreement (A,B,C,D,E): +0.12 each
- Cross-group consensus (all 5 agree): ×1.15
- Cap at 0.75 (down from 0.90 — more honest)

**Validation:** Backtest, measure directional accuracy (target ≥55%)

**P1.2 — Session Weighting + Regime Volatility (File: tradingbot/debaters/directional.py)**

Wire session overlay into conviction scaling:
```python
# After computing base conviction:
session = context.get("session")  # From regime classifier
volatility_regime = context.get("vol_state")  # VOL_COMPRESSION, NORMAL, VOL_EXPANSION, CRISIS

if session == "OPEN":
    session_scalar = 1.2  # 9:30-10:00 ET: volatile, trending
elif session == "MORNING":
    session_scalar = 1.0  # 10:00-12:00: normal
elif session == "MIDDAY":
    session_scalar = 0.7  # 12:00-14:00: consolidation, reduce conviction
elif session == "POWER_HOUR":
    session_scalar = 1.1  # Gamma effects, option expiry
else:
    session_scalar = 1.0

# In high-VIX regimes, reduce session bias (markets are choppier, less reliable)
if volatility_regime in ("VOL_EXPANSION", "CRISIS"):
    session_scalar = session_scalar * 0.85

conviction = conviction * session_scalar
conviction = max(0.2, min(0.75, conviction))  # Keep within bounds
```

**Validation:** Backtest, check no regression in midday (12-14:00 ET should have lower hit rate)

**Timeline:** 2-3 hours including backtest validation

**Gate:** Directional accuracy ≥55% + no midday regression

---

### PHASE 2: Tomorrow Morning — Sentiment Rewrite (P2.1)

**P2.1 — IV-Skew Sentiment Debater (File: tradingbot/debaters/sentiment.py)**

Complete rewrite (replace current implementation):
```python
def debate(self, context):
    ticker = context.get("ticker")
    
    # Check earnings calendar
    earnings_data = context.get("earnings_snapshot")  # New: pre-fetch this
    days_to_earnings = earnings_data.get("days_to_earnings", 30) if earnings_data else 30
    
    if days_to_earnings <= 1:
        # Pre-earnings: vol crush risk, reduce conviction
        return DebaterOpinion(
            debater_name=self.name,
            direction=Direction.NEUTRAL,
            conviction=0.20,
            reasoning="Earnings within 1 day — avoid pre-announcement vol crush risk",
            signals_used={"days_to_earnings": days_to_earnings}
        )
    elif days_to_earnings <= 5:
        # Next few days: wait for event clarity
        return DebaterOpinion(
            debater_name=self.name,
            direction=Direction.NEUTRAL,
            conviction=0.25,
            reasoning=f"Earnings in {days_to_earnings} days — awaiting catalyst clarity",
            signals_used={"days_to_earnings": days_to_earnings}
        )
    
    # Earnings clear: use options market structure
    options_data = context.get("options_snapshot")
    
    iv_put_skew = options_data.get("iv_put_skew_pct", 0)  # Put-side IV vs call-side IV
    call_put_ratio = options_data.get("call_put_ratio", 1.0)
    
    if iv_put_skew > 5.0:  # Institutions hedging downside
        return DebaterOpinion(
            debater_name=self.name,
            direction=Direction.BEARISH,
            conviction=0.55,
            reasoning=f"IV put-skew elevated {iv_put_skew:.1f}% — institutional hedging",
            signals_used={"iv_put_skew": iv_put_skew, "signal": "put_hedge"}
        )
    elif iv_put_skew < -5.0:  # Call buying spike
        return DebaterOpinion(
            debater_name=self.name,
            direction=Direction.BULLISH,
            conviction=0.45,
            reasoning=f"IV call-skew elevated {-iv_put_skew:.1f}% — call buying",
            signals_used={"iv_put_skew": iv_put_skew, "signal": "call_spike"}
        )
    elif call_put_ratio > 1.8:  # Call heavy (weaker signal)
        return DebaterOpinion(
            debater_name=self.name,
            direction=Direction.BULLISH,
            conviction=0.30,
            reasoning=f"Call/put ratio {call_put_ratio:.2f} — retail call buying (weak signal)",
            signals_used={"call_put_ratio": call_put_ratio, "signal": "call_ratio_high"}
        )
    elif call_put_ratio < 0.6:  # Put heavy
        return DebaterOpinion(
            debater_name=self.name,
            direction=Direction.BEARISH,
            conviction=0.30,
            reasoning=f"Call/put ratio {call_put_ratio:.2f} — put buying (weak signal)",
            signals_used={"call_put_ratio": call_put_ratio, "signal": "put_ratio_high"}
        )
    else:
        # Ambiguous options market
        return DebaterOpinion(
            debater_name=self.name,
            direction=Direction.NEUTRAL,
            conviction=0.25,
            reasoning="Options market neutral — no clear skew or ratio signal",
            signals_used={"iv_put_skew": iv_put_skew, "call_put_ratio": call_put_ratio}
        )
```

**Data sources needed:**
- IV skew: `moomoo_data.get_iv_skew(ticker)` or yfinance equivalent
- Earnings calendar: yfinance `.earnings_dates` or free API
- Call/put ratio: Already available from moomoo snapshot

**Validation:** Backtest, measure sentiment accuracy (target ≥52% hit rate)

**Timeline:** 2 hours including backtest

**Gate:** Sentiment accuracy ≥52%

---

### PHASE 3: Tomorrow Afternoon — Calibration Refit (P3.1)

**P3.1 — Refit Calibrator on Improved Signals**

Run after P1.1 + P1.2 + P2.1 are complete and backtested:
```bash
python scripts/fit_calibrator.py
```

This will:
1. Load latest backtest results
2. Extract (p_bull, hit) pairs
3. Fit new Platt slope a (expect 0.15-0.20, up from 0.0998)
4. Calculate new Brier score (expect <0.24, down from 0.24881)

**Expected outcome:**
- a increases 30-50% (from 0.0998 to 0.13-0.15): calibrator now more informative
- Brier improves 5-10% (from 0.24881 to 0.235-0.245): signal quality visible

**Timeline:** 15 min (automated)

---

### PHASE 4: Monday Morning — Validation & Cutover

**Monday 9:35 AM — Shadow Scan Validation**

Run full 22-symbol scan with P1+P2+P3 improvements:
```bash
python scripts/market_monitor.py --dry-run --shadow
```

**Success criteria:**
- ✅ 3-5 approved trades (vs. 0/22 currently) → **Proceed to live paper trading**
- ⚠️ 1-2 approved trades → **Debug consensus p_bull distribution, may need P1.3**
- ❌ 0 approved trades → **Root cause analysis, revert and investigate Kelly gate**

**Expected p_bull distribution (post-improvement):**
```
Mean p_bull (non-neutral): 0.56-0.57 (up from 0.541)
Min p_bull: 0.52-0.53 (just above Kelly gate)
Max p_bull: 0.81-0.83 (when directional strongly bullish)
```

**If <3 approvals:**
- Check if P1.3 (relative strength) needed immediately
- Or investigate: Are some tickers still hitting calibration squash?
- Option: Soft-lift min_edge from 0.52 to 0.50 for one more scan

---

## Work Breakdown (Time Estimates)

| Task | Time | Total | Start |
|------|------|-------|-------|
| **P1.1: RSI/MACD/ATR** | 1.5 hr | 1.5 | Tonight 7pm |
| Backtest + validation | 1 hr | 2.5 | Tonight 8:30pm |
| **P1.2: Session weighting** | 1 hr | 3.5 | Tonight 9:30pm |
| Backtest + validation | 1 hr | 4.5 | Tonight 10:30pm |
| **COMMIT & SLEEP** | - | - | Tonight 11:30pm |
| **P2.1: IV-skew sentiment** | 2 hrs | 2 | Tomorrow 9am |
| Backtest + validation | 1 hr | 3 | Tomorrow 11am |
| **P3.1: Refit calibrator** | 0.25 hr | 3.25 | Tomorrow 12pm |
| **P1.3: Relative strength** | 2 hrs | 5.25 | Tomorrow 2pm (if time) |
| Buffer + debug | 1 hr | 6.25 | - |
| **Monday: Full validation** | 0.5 hr | 0.5 | Monday 9:35am |

**Total:** ~6.5 hours of work across tonight + tomorrow + Monday morning

---

## Decision Trees

### If P1.1 backtest shows directional accuracy < 55%:
1. Check: Are RSI/MACD signals noisy on backtest period?
2. Try: Add volume confirmation (vol > 2× MA) to reduce false signals
3. Fallback: Keep RSI/MACD but reduce Group E weight (0.10 instead of 0.12 per signal)

### If P2.1 sentiment accuracy < 52%:
1. Check: Is earnings filter too aggressive (killing signal ±5 days)?
2. Try: Widen earnings window to ±7 days
3. Fallback: Revert to simple IV-skew only (no earnings, no call/put ratio)

### If Monday shows <3 approvals:
1. Check: Did P1+P2 improvements actually increase mean p_bull to 0.56+?
2. Try: Implement P1.3 (relative strength) immediately for second scan
3. Fallback: Soft-lift Kelly min_edge from 0.52 to 0.50 for one-off validation

---

## Success Definition

**Monday 9:35 AM cutover proceeds if:**
- ✅ P1.1 + P1.2 backtest shows directional accuracy ≥55%
- ✅ P2.1 backtest shows sentiment accuracy ≥52%
- ✅ Consensus p_bull mean ≥0.56 on non-neutral days
- ✅ Shadow scan produces ≥3 approved trades
- ✅ Calibrator Platt slope a increases to ≥0.13

**If any gate fails:** Debug, iterate on failed component, retry Monday afternoon scan.

---

## Key Risks & Mitigations

| Risk | Probability | Mitigation |
|------|-------------|-----------|
| P1.1 (RSI/MACD) adds noise, hurts accuracy | Medium | Use volume confirmation + narrow conviction range |
| P2.1 IV-skew data unreliable | Low | Fallback to simple IV-skew only, drop earnings filter |
| P3.1 recalibration still shows flat slope | Low | Indicates debater accuracy still ~50%; extend P1+P2 |
| Still <3 approvals Monday | Medium | P1.3 (relative strength) as emergency 2nd phase |
| Kelly min_edge too tight | Low | Can soft-lift to 0.50 for validation, but fix symptoms not gate |

---

## Success Celebration Criteria

**Tonight:** P1.1 + P1.2 implemented and validated (directional acc ≥55%)  
**Tomorrow 12pm:** P2.1 implemented, P3.1 refit complete, Brier delta measurable  
**Monday 10am:** 3+ approved trades on scan, paper trading cutover authorized  
**Monday EOD:** First live trades executed under improved debater consensus

---

## Rollback & Backout Plan

**If at any point accuracy regresses:**
1. Revert changes to feature branch
2. Restore previous calibrator state from git history
3. Re-run validation on working baseline
4. Debug isolated component before re-introducing

All changes are committed to feature branches before Monday cutover, so rollback is trivial.

---

## Questions Before Starting?

Review [CRITICAL_ANALYSIS.md](CRITICAL_ANALYSIS.md) for detailed Q&A on:
1. GLS weights and consensus mechanism
2. Brier score interpretation
3. Kelly sensitivity analysis
4. Sentiment debater design rationale

**Ready to start tonight?** All context is in this document and IMPROVEMENT_PLAN.md.
