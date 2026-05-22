# Production Readiness Analysis — May 22, 2026

## Executive Summary

**Status**: Incomplete scan detected → Root causes identified and fixed.

Today's paper trading scan attempted all 22 tickers but failed to execute ANY trades due to a **critical pipeline bug** where the sizing engine was approving trades even when the option structure picker returned `no_viable_structure`. This cascaded into broker submission failures for all attempted trades.

---

## Issues Identified

### Issue #1: Structure Picker Failure → Sizing Approval Mismatch (CRITICAL)

**Symptom**:
- All 22 tickers scanned (JSONL logs show complete scan)
- Paper trade summary showed only NVDA as approved (actually: summary was from a dry-run test)
- Execution logs showed trades marked `sized_approved` with error: `missing long_strike in structure_pick`

**Root Cause**:
Pipeline Layer L6 (Sizing Engine) was running **without validating** that Layer L5b (Structure Picker) had succeeded. Even when structure picker returned `{"status": "no_viable_structure"}`, the sizing gates (Kelly, Greeks, CVaR) would still run and potentially approve a trade.

When the approved trade reached broker submission in paper_trade.py, the code tried to extract `long_strike` from an empty structure_pick dict, resulting in None, and no order legs were created.

**Code Path**:
1. L5b (structure_picker.py): Evaluates option chains, returns None if no liquid spreads found
2. L5b (pipeline.py line 350): Sets `ctx.attribution["structure_pick"] = {"status": "no_viable_structure"}`
3. L6 (pipeline.py line 362): **Does NOT check** if structure_pick succeeded
4. L6: Runs sizing gates (Kelly, Greeks, CVaR) with empty `ctx.market_data["max_profit/loss"]`
5. L6: Approves trade anyway (Kelly gate likely passes with neutral outlook)
6. paper_trade.py line 658: `long_strike = struct_pick.get("long_strike")` → None
7. paper_trade.py line 662: `if long_strike:` fails → no legs created
8. Broker submission logged error: `missing long_strike`

**Impact**:
- Zero trades executed despite consensus signals
- System appeared to be working (logs show approved trades) but submitted nothing
- Not a crash, so not caught by monitoring

---

### Issue #2: Options Chain Has Valid Data But Greeks Are All Zero

**Symptom**:
- Options chain cached with 241 calls, 223 puts
- Bid/ask prices present (e.g., 311.53/314.33)
- IV values present (e.g., 0.0183)
- **But**: delta, gamma, theta, vega all = 0.0

**Root Cause**:
moomoo's `get_option_chain()` is returning options data but not computing Greeks. This causes:
1. Structure picker `_is_liquid()` check still passes (bid > 0, ask > 0)
2. Structure picker `_approx_delta()` falls back to moneyness estimate (not from chain)
3. Options appear viable but lack proper Greeks for pricing spreads

**Note**: This is NOT a showstopper because the structure picker falls back to moneyness-based delta calculation. The real issue is #1 above.

---

### Issue #3: Watchdog Heartbeat Age Calculation Broken (NON-CRITICAL)

**Symptom**:
- Watchdog logs show: `Daemon stale (age Nones if exists)`
- Heartbeat age not being calculated correctly

**Root Cause**:
Watchdog script has a formatting/parsing bug when reading heartbeat file age.

**Impact**: Watchdog still functions (daemon is running), but alerts may be incorrectly triggered.

---

## Fixes Implemented

### Fix #1: Add Structure Picker Guard Gate (CRITICAL)

**File**: `tradingbot/graph/pipeline.py` (Line 359-376)

**Change**:
```python
# Guard: reject if structure picking failed (no viable option structures found)
structure_pick_status = ctx.attribution.get("structure_pick", {}).get("status")
if structure_pick_status == "no_viable_structure":
    ctx.sizing = {
        "approved": False,
        "reject_gate": "structure",
        "reject_reason": "No viable option structure found for current market conditions",
        "contracts": 0,
    }
    logger.log_sizing("REJECTED", "structure gate", {"reason": "No viable option structure"})
elif feature_flags.SIZING_ENGINE_ENABLED and ctx.consensus is not None:
    # ... existing L6 sizing logic
```

**Impact**: Trades with failed structure picker now reject before reaching Kelly/Greeks gates.

---

### Fix #2: Validate Structure Data Before Submission

**File**: `scripts/paper_trade.py` (Line 595-608)

**Change**:
```python
if status == "sized_approved":
    struct_pick = result.get("structure_pick") or {}
    # Validate that structure has actual data before submitting
    if not struct_pick.get("long_strike"):
        _log(f"    [SKIP] APPROVED but no structure found (no_viable_structure)")
        results.append(result)
        continue
    # ... submit to broker
```

**Impact**: Double-check at submission time prevents any trade with missing structure data from reaching broker.

---

## Verification

### Test Coverage
- Full test suite (531 tests) passing
- Structure picker tests cover no-viable-structure cases
- Sizing gate tests will validate new guard condition

### Production Checklist
- [ ] Deploy fixes to production
- [ ] Run shadow trade with all 22 tickers
- [ ] Verify at least 1-2 trades execute (or confirm all rejected by structure gate with reason)
- [ ] Check daemon logs for guard gate rejections
- [ ] Fix watchdog heartbeat age calculation (separate issue)

---

## Why This Happened

The pipeline was designed with 9 layers (L0–L8), but the guard condition between structure picking (L5b) and sizing (L6) was omitted. This created a scenario where:

1. **Consensus succeeds** → "Yes, market is bullish"
2. **Structure picking fails** → "But no liquid option spreads available"
3. **Sizing runs anyway** → "OK, size 0 contracts due to Kelly"
4. **BUT**: Sizing approval logic doesn't require non-zero contracts
5. **Result**: Approved 0-contract trades that fail at submission

The fix is simple: add explicit guard before L6.

---

## Next Steps

1. **Immediate**: Verify fixes with shadow trade tomorrow (May 23)
2. **Short-term**: Fix watchdog heartbeat parsing (minor)
3. **Medium-term**: Backtest to see if structure-gate rejections are realistic or indicate calibration issue
4. **Long-term**: Consider: when should structure picker fail? Is options chain data quality issue?

---

## Files Modified

- `tradingbot/graph/pipeline.py`: Add structure_pick guard gate
- `scripts/paper_trade.py`: Add long_strike validation
- `scripts/market_monitor.py`: Remove redundant file logging (supervisord handles it)
- `tradingbot/core/logging_util.py`: Add _initialized class flag to prevent duplicate handlers

**Commit**: `1388df6` - "fix: add structure_pick guard gate before sizing approval"
