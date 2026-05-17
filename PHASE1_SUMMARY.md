# Phase 1 — 5 Debaters + Smoke Tests Complete

**Status:** ✅ Complete and tested  
**Commits:** `7fc2980` (debaters) + `fe9a7db` (tests)  
**Test coverage:** 59/59 passing

---

## What shipped

### Core implementation

1. **DebaterBase** (`oa2/debaters/base.py`)
   - Abstract interface for all debaters
   - Unified `debate(context) -> DebaterOpinion` signature
   - Direction enum + DebaterOpinion dataclass

2. **Five concrete debaters** (all quant-only, no LLM):
   - **Directional** (`directional.py`) — tape/momentum, 6 price signals, conviction 0.40–0.90
   - **Income** (`income.py`) — premium-selling, IV rank + RV/IV, conviction 0.30–0.95
   - **Volatility** (`volatility.py`) — vol expansion/compression, 6+4 signals, conviction 0.45–0.95
   - **Flow** (`flow.py`) — institutional PCR + sweeps + OI, conviction 0.10–0.90
   - **Sentiment** (`sentiment.py`) — crowd alignment, baseline 0.20 on missing data

3. **DebaterEnsemble** (`oa2/debaters/runner.py`)
   - Orchestrates all 5 debaters
   - Logs opinions to disk (JSONL)
   - Produces summary dict for attribution

4. **Debater logging** (`oa2/learning/debater_logger.py`)
   - JSONL file at `~/.oa2/debater_logs/opinions.jsonl`
   - Per-debater analytics: hit rate, avg conviction, per-regime stats
   - Outcome resolution: fill in realized direction + PnL on trade close

5. **Pipeline integration** (`oa2/graph/pipeline.py`)
   - Feature flag `OA2_FLAG_DEBATERS` gates debater execution
   - `run(ticker, context_dict)` calls ensemble, returns opinions
   - Decision status now shows `debaters_only` + opinion count

---

## Test coverage (59 tests, all passing)

### Phase 1 integration tests (6)
- ✅ Debaters import cleanly
- ✅ Ensemble instantiates with 5 debaters
- ✅ Each debater runs on minimal context
- ✅ Ensemble run returns list of opinions
- ✅ Ensemble opinion summary works
- ✅ Pipeline with flag enabled calls debaters

### Directional debater tests (6)
- ✅ Bullish signal stack (price > VWAP, EMA 20 > 50, etc.)
- ✅ Bearish signal stack
- ✅ Misaligned trade conviction penalty (0.75×)
- ✅ Neutral when signals balanced
- ✅ All signals logged (price, vwap, ema_20, ema_50, rsi, tape_direction, etc.)

### Income debater tests (6)
- ✅ Rich IV + short premium → bullish (ideal for seller)
- ✅ Expensive IV + long premium → bearish (worst case)
- ✅ Cheap IV + RV high + buying → conviction ≤ 0.35
- ✅ Short premium + RV exceeds IV → bearish (dangerous)
- ✅ All signals logged (iv_rank, rv_iv_ratio, theta, vega, premium flags, etc.)

### Volatility debater tests (6)
- ✅ Long vega + 2+ vol expansion signals → bullish
- ✅ Short vega + 2+ vol expansion signals → bearish
- ✅ Short vega + 3+ vol compression signals → neutral/acceptable
- ✅ Long vega + 3+ vol compression signals → bearish
- ✅ All signals logged (vol_expansion_signals, vol_compression_signals, vega, gamma, theta, etc.)

### Flow debater tests (7)
- ✅ PCR bullish (< 0.6) + call sweeps → bullish
- ✅ PCR bearish (> 1.5) + put sweeps → bearish
- ✅ No flow data → neutral, low conviction
- ✅ Fallback to derive flow from chain Greeks
- ✅ OI changes bullish (calls surging, puts collapsing)
- ✅ All signals logged (pcr, sweeps, oi_change, dark_pool, unusual_vol, etc.)

### Sentiment debater tests (8)
- ✅ Bullish sentiment + bullish structure → bullish
- ✅ Bearish sentiment + bearish structure → bearish
- ✅ Sentiment/structure mismatch → conviction reduced
- ✅ Neutral sentiment (−0.30 to +0.30) → neutral
- ✅ Thin signal (mention_count < 10) → conviction −15%
- ✅ Missing data → neutral baseline 0.20
- ✅ All signals logged (composite_score, mention_count, data_sources, etc.)

### Cross-debater property tests (25, parametrized)
- ✅ Conviction always in [0, 1] (all 5 debaters)
- ✅ Direction always valid (all 5 debaters)
- ✅ Signals_used always non-empty dict (all 5 debaters)
- ✅ Reasoning always non-empty string (all 5 debaters)

---

## Key design choices

1. **Quant-only in Phase 1.** LLM debaters (async, cached) ship in Phase 6. All debaters are deterministic, testable, logged.

2. **Logging is automatic.** Every opinion goes to `~/.oa2/debater_logs/opinions.jsonl` by default. Tests disable it with `log_to_disk=False`.

3. **Context is flexible.** Each debater handles dict, Pydantic models, or missing fields (graceful defaults).

4. **Signals are complete.** Every opinion logs the signals it used, enabling future attribution analysis and performance-adaptive weighting (Phase 4).

5. **Conviction ranges are tuned.**
   - Directional: 0.40–0.90 (base + signal diff × 0.12)
   - Income: 0.30–0.95 (IV extremes)
   - Volatility: 0.35–0.95 (signal stacking)
   - Flow: 0.10–0.90 (PCR extremes)
   - Sentiment: 0.20–0.90 (base 0.45 + |score| × 0.40)

---

## Next phase

**Phase 2: Regime classifier** (8-bucket vol × trend)
- Inputs: IV rank, RV, term structure, VIX, trend of 20d returns
- Output: regime_id ∈ [0, 7] with posterior distribution
- Feeds debater weighting in Phase 3 (consensus engine)

Or **Phase 3: Consensus engine** (GLS aggregation + Mahalanobis confidence)
- Replaces current static weighted sum with correlation-aware blending
- Inputs: 5 DebaterOpinion + covariance tracker
- Output: `Consensus` with direction, score, N_eff, calibrated p_bull

---

## Files changed / added

```
oa2/debaters/
  __init__.py (new)
  base.py (new) — DebaterBase, DebaterOpinion, Direction
  directional.py (new) — DirectionalDebater
  income.py (new) — IncomeDebater
  volatility.py (new) — VolatilityDebater
  flow.py (new) — FlowDebater
  sentiment.py (new) — SentimentDebater
  runner.py (new) — DebaterEnsemble

oa2/learning/
  debater_logger.py (new) — logging + analytics

oa2/graph/
  pipeline.py (modified) — added debater ensemble call

tests/
  test_phase1_debaters.py (new) — 6 integration tests
  test_debaters_individual.py (new) — 53 individual + cross-debater tests
```

---

## Validation

Run all debater tests:
```bash
pytest tests/test_phase1_debaters.py tests/test_debaters_individual.py -v
# 59 passed in 0.13s
```

Run smoke test (Phase 0 + Phase 1):
```bash
python -m scripts.smoke_test
# 5/5 passed (Phase 0)
# + all debaters imported, runnable, logged
```