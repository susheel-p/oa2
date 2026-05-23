# Implementation Summary: Intelligent Option Expiration Selection

**Status:** ✅ Complete  
**Date:** 2026-05-22  
**Commit:** `0b48476` — feat: implement intelligent (flow-driven) option expiration selection

---

## Overview

Successfully replaced hardcoded weekly-only option selection with **data-driven expiration choice** based on smart money options flow. The system now analyzes 5 different expirations and recommends the one with the dominant smart money activity.

---

## What Changed

### Phase 1: Multi-Expiration Data Fetching ✅

**File:** `tradingbot/dataflows/moomoo_data.py`

#### New Function: `_compute_expiry_dates(as_of: datetime | None)`
- Generates 5 option expirations for analysis
- Returns: next Friday, 2nd Friday, 3rd Friday, 6-week, 10-week (ISO format)
- Example output: `['2026-05-29', '2026-06-05', '2026-06-12', '2026-07-03', '2026-07-31']`
- Replaces hardcoded "days_ahead = 4 - today.weekday()" logic

#### Modified: `fetch_market_snapshot(ticker: str)`
- **Before:** Fetched single chain for next Friday only
- **After:** Fetches 4-5 expirations, stores in `chains_by_expiry` dict
- Gracefully handles per-expiry failures (try/except on each fetch)
- Maintains backwards compatibility with `options_chain` (default chain)
- Returns:
  ```python
  {
    "chains_by_expiry": {
      "2026-05-29": {"calls": [...], "puts": [...]},
      "2026-06-05": {...},
      ...
    },
    "recommended_expiry": "2026-06-05",  # Computed in Phase 2
    "options_chain": {...},  # Default (nearest) for backwards compat
    ...
  }
  ```

#### Enhanced: `analyze_options_flow(chains_or_dict)`
- Now accepts **both** single chain (old) and multi-expiry dict (new)
- Auto-detects input type by checking for 'calls'/'puts' keys
- Aggregates volume across all expirations if multi-expiry
- Returns same structure (PCR, concentration, urgency)

### Phase 2: Expiry Recommendation Engine ✅

**File:** `tradingbot/dataflows/moomoo_data.py`

#### New Function: `_recommend_expiry(chains_by_expiry: Dict)`
- Uses `classify_expiry_flow()` to analyze dominant DTE bucket
- Maps buckets to expiration dates:
  - **front_week** (0-7 DTE) → nearest expiry (index 0)
  - **near_term** (8-21 DTE) → 2nd nearest (index 1)
  - **mid_term** (22-45 DTE) → 3rd nearest (index 2)
  - **longer** (46+ DTE) → furthest (index 4)
- Fallback: Returns next Friday if empty chains or unknown bucket
- Returns: ISO date string or None

#### Example Flow Analysis
If chains show:
- Front week: 10% volume
- **Near term: 60% volume** ← dominant
- Mid term: 20% volume
- Longer: 10% volume

Then: `_recommend_expiry()` returns the **2nd Friday expiration** (8-21 DTE range)

#### Integrated into `fetch_market_snapshot()`
- Calls `_recommend_expiry(chains_by_expiry)` before returning
- Adds `recommended_expiry` to snapshot dict
- Used by pipeline L0

### Phase 3: Pipeline Integration ✅

**File:** `tradingbot/graph/pipeline.py`

#### L0 — Market Data Layer
Added expiry extraction logic (after line 150):
```python
# Extract recommended expiration from snapshot
recommended_expiry = ctx.market_data.get("recommended_expiry")
if recommended_expiry:
    ctx.market_data["_recommended_expiry"] = recommended_expiry
    logger.log_detail("Expiry recommendation", {"recommended": recommended_expiry})
else:
    logger.log_detail("No expiry recommendation available", {})
```

**Result:** `ctx.market_data["_recommended_expiry"]` now available to all downstream layers

#### L5b — Structure Picker
Modified chain selection logic (around line 325):
```python
# Before: Used only _options_chain (hardcoded Friday)
chain = ctx.market_data.get("_options_chain")

# After: Uses recommended expiry if available, falls back to default
if recommended_expiry and recommended_expiry in chains_by_expiry:
    chain = chains_by_expiry[recommended_expiry]
    logger.log_detail("Using recommended expiry for structure pick", {...})
else:
    chain = ctx.market_data.get("_options_chain")
    logger.log_detail("Using default options chain", {})
```

**Result:** `pick_structure()` receives the recommended chain, not hardcoded Friday

#### Decision Record
Added two fields to `_build_decision()`:
```python
"recommended_expiry": ctx.market_data.get("_recommended_expiry"),
"chains_analyzed": len(ctx.market_data.get("chains_by_expiry", {})),
```

**Result:** Decision output shows which expiry was chosen and how many chains were analyzed

---

## Key Design Decisions

✅ **Don't modify `pick_structure()` signature**
- Already used elsewhere; stays as-is
- Now receives better chain (recommended vs hardcoded)

✅ **Backwards compatible**
- Old code still works: snapshot has `options_chain` (default)
- Single-chain input to `analyze_options_flow()` still works
- Pipeline falls back to default chain if recommendation missing

✅ **Fallback to next Friday**
- If no chains available or unknown dominant bucket
- Safe default behavior

✅ **Observable decision-making**
- Logs "Expiry recommendation" at L0
- Logs which expiry used in L5b ("Using recommended expiry" vs "Using default")
- Decision dict includes both `recommended_expiry` and `chains_analyzed` for auditing

✅ **No new dependencies**
- Uses existing `datetime`, `timedelta` (already imported)
- Uses existing `classify_expiry_flow()` from expiry_flow.py
- No additional packages required

---

## Testing Results

### Unit Tests ✅
```
Test 1: _compute_expiry_dates()
  Generated 5 expiration dates
  Dates: ['2026-05-29', '2026-06-05', '2026-06-12', '2026-07-03', '2026-07-31']
  Sorted: True
  All ISO format: True

Test 2: _recommend_expiry()
  Recommended expiry: 2026-06-05
  In available expirations: True

Test 3: _recommend_expiry() fallback
  Fallback (no chains): 2026-05-29
  Returns ISO date: True

All unit tests passed!
```

### Syntax Validation ✅
- `tradingbot/dataflows/moomoo_data.py`: syntax check passed
- `tradingbot/graph/pipeline.py`: syntax check passed

### Integration Notes
- Full smoke test deferred (requires OpenD moomoo server running)
- Unit tests verify core logic
- Backwards compatibility maintained

---

## How It Works in Practice

### Scenario 1: Call-Heavy Smart Money in Near-Term Expirations
```
Daily trade for QQQ:
  L0 fetches: [May29, Jun05, Jun12, Jul03, Jul31] option chains
  Flow analysis shows: Jun05 has 65% of total call volume
  dominant_bucket: near_term (8-21 DTE)
  _recommend_expiry() returns: 2026-06-05

  L5b Structure Picker:
    Uses QQQ Jun05 chain (not May29)
    Finds higher conviction spread with better odds
    Trade recommended for Jun05 expiration instead of hardcoded weekly

  Decision logged:
    "recommended_expiry": "2026-06-05"
    "chains_analyzed": 5
```

### Scenario 2: Monthly Dominance (Earnings, Event)
```
Daily trade for MSFT around earnings:
  L0 fetches 5 expirations
  Flow shows: Jun20 (3rd Friday / monthly) has 55% of volume
  dominant_bucket: mid_term (22-45 DTE)
  _recommend_expiry() returns: 2026-06-20

  System trades Jun20 instead of May29, aligning with institutional positioning
```

### Scenario 3: No Clear Dominance
```
Daily trade for XYZ with low flow:
  L0 fetches 5 expirations
  Chains mostly empty or low volume
  dominant_bucket: unknown
  _recommend_expiry() returns: 2026-05-29 (fallback to nearest Friday)

  System gracefully defaults to weekly option
```

---

## Impact

| Aspect | Before | After |
|--------|--------|-------|
| **Expiration Selection** | Hardcoded next Friday | Data-driven (dominant flow bucket) |
| **Expirations Analyzed** | 1 (only weekly) | 5 (weekly + 6w + 10w) |
| **Smart Money Alignment** | No; trades off cycle | Yes; trades where volume is |
| **Flexibility** | Fixed for all stocks | Per-stock, per-day based on flow |
| **Backwards Compat** | N/A | Full; old code still works |
| **Observability** | Chain choice invisible | Logged and visible in decision |

---

## What's Next

1. **Live Testing:** Run against real market data with OpenD running
2. **Flow Attribution:** Use decision `recommended_expiry` field to analyze trade correlation with expiry flow
3. **A/B Testing:** Compare historical P&L using weeklies vs recommended expirations
4. **Refinement:** If needed, adjust bucket-to-expiry mapping or add tie-breaking logic

---

## Files Changed

- **tradingbot/dataflows/moomoo_data.py**
  - Added: `_compute_expiry_dates()`
  - Added: `_recommend_expiry()`
  - Modified: `fetch_market_snapshot()`
  - Enhanced: `analyze_options_flow()`

- **tradingbot/graph/pipeline.py**
  - L0: Added expiry extraction logic
  - L5b: Modified chain selection to use recommended expiry
  - Decision: Added `recommended_expiry` and `chains_analyzed` fields

- **plans/intelligent-expiry-selection.md** (reference implementation plan)

---

## Rollback Plan (if needed)

If Phase 3 integration causes issues:
```bash
# Revert to default chain-only behavior
git revert 0b48476
```

The changes are self-contained and easily reverted. Core pipeline logic unchanged.

---

## Commit Message

```
feat: implement intelligent (flow-driven) option expiration selection

Implements 3-phase enhancement to replace hardcoded weekly-only option
selection with data-driven expiration choice based on smart money flow.
[Full details in commit body]
```

Commit hash: `0b48476`

---

**Implementation by:** Claude Code  
**Date Completed:** 2026-05-22 21:40 UTC
