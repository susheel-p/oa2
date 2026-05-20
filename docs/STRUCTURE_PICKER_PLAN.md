# Structure Picker — Architectural Gap & Plan

**Date:** 2026-05-18  
**Trigger:** P1+P2 signal improvements unlocked 13 BULLISH signals on 22-ticker scan, but 0 approved. Root cause: hardcoded R:R defaults rejected by Kelly.

---

## The Bug (Single Line)

[pipeline.py:388-389](tradingbot/graph/pipeline.py#L388-L389) — `max_profit` and `max_loss` default to **$200 / $300 (R:R = 0.667)** because no module computes them from real chain data.

```python
max_profit = float(md.get("max_profit", 200.0))
max_loss = float(md.get("max_loss", 300.0))
```

Kelly's gate math:
```
At p_bull = 0.554, edge = 0.554
kelly_f = (edge × (odds + 1) - 1) / odds
        = (0.554 × 1.667 - 1) / 0.667
        = -0.077 / 0.667
        = -0.116    ❌ NEGATIVE
```

To pass at p_bull=0.554, need **odds > 0.805** (max_profit > 80% of max_loss). Default gives 0.667 → guaranteed rejection.

---

## Why This Wasn't Caught Earlier

| Layer | Status | Notes |
|-------|--------|-------|
| Data source (chain) | ✅ Has data | `cache/AMD_2026-05-18.json` has `_options_chain` with 220 calls, 168 puts, real bid/ask/IV/Greeks |
| Chain fetcher (cache.py) | ✅ Works | Caches full chain per ticker per date |
| Structure picker module | ❌ **MISSING** | No code translates chain → (strike1, strike2, max_profit, max_loss) |
| Debaters reference `selected_structure` | ✅ Consume | But nothing populates it |
| Pipeline sizing gate | ⚠️ Stubbed | Uses 200/300 defaults silently |
| Kelly sizing | ✅ Correct | Math is right, just fed wrong inputs |

Before P1+P2 fixed signal quality, all rejections came from "edge below min_edge" — the structure picker bug was masked. Now that signals are stronger, the masked bug is the binding constraint.

---

## What a Structure Picker Should Do

Given:
- Consensus direction (BULLISH / BEARISH)
- Consensus conviction (p_bull)
- Current price, IV rank, regime
- Real option chain (calls/puts with bid/ask/IV/Greeks)

Output:
- Selected structure (LONG_CALL, VERTICAL_CALL_SPREAD, etc.)
- Strike(s) chosen
- Expiry chosen
- **max_profit_per_contract** (real $)
- **max_loss_per_contract** (real $)
- Greeks per contract (delta, gamma, theta, vega)

---

## Proposed Module: `tradingbot/strategy/structure_picker.py`

### Design Principles

1. **Match structure to regime + conviction**
   - High conviction (p_bull > 0.65) + bullish + cheap IV → LONG_CALL (big R:R)
   - Moderate conviction (0.55-0.65) + bullish + normal IV → VERTICAL_CALL_SPREAD (controlled risk)
   - High conviction + expensive IV → DEBIT_SPREAD or DIAGONAL (limit vega risk)
   - Bearish equivalent for puts

2. **Target Kelly-viable R:R**
   - At p_bull=0.55: need odds > 0.82 → choose narrower spreads
   - At p_bull=0.65: need odds > 0.54 → wider spreads OK
   - **Width selection algorithm:** pick spread width that yields max_profit ≥ 1.0 × max_loss when possible

3. **Liquidity filter**
   - Skip strikes with bid=0 or spread > 30% of mid
   - Prefer open_interest > 100 strikes

4. **DTE selection**
   - 21-45 DTE default (matches Kelly sweet spot in current code)
   - Closer for high-conviction directional plays (7-14 DTE)
   - Further for income/volatility plays (45+)

### API Sketch

```python
@dataclass
class StructurePick:
    structure_type: str        # "VERTICAL_CALL_SPREAD", etc.
    long_strike: float
    short_strike: float | None  # None for outright
    expiry: str
    dte: int
    max_profit: float
    max_loss: float
    breakeven: float
    odds: float                 # = max_profit / max_loss
    delta_per_contract: float
    vega_per_contract: float
    theta_per_contract: float
    confidence: float           # picker's confidence in selection


def pick_structure(
    chain: dict,                # cached _options_chain
    spot: float,
    direction: str,             # "BULLISH" | "BEARISH"
    p_bull: float,
    iv_rank: float,
    target_dte_min: int = 21,
    target_dte_max: int = 45,
    min_odds: float = 0.85,
) -> StructurePick | None:
    """Select an options structure that maximizes Kelly viability.
    
    Returns None if no liquid + Kelly-viable structure exists.
    """
```

### Selection Algorithm (V1 — keep it simple)

```
1. Filter chain to liquid contracts (bid > 0, spread/mid < 30%, OI > 50)
2. Find expiry in [target_dte_min, target_dte_max]
3. Compute required_odds = (1 - p_bull) / p_bull + safety_margin (e.g., 0.05)
4. For BULLISH:
   a. ATM long call strike = closest strike <= spot
   b. Try spread widths [5, 10, 15, 20] dollars (or 1%, 2%, 3%, 4% of spot)
   c. For each width:
      - long = ATM, short = ATM + width
      - max_profit = width - (long_ask - short_bid)   [debit paid]
      - max_loss   = long_ask - short_bid              [debit paid]
      - odds       = max_profit / max_loss
      - if odds >= required_odds: SELECT
   d. If none viable, fall back to LONG_CALL outright (max_loss = ask × 100, max_profit = unbounded → cap at 2× ATM)
5. For BEARISH: mirror with puts
6. Return StructurePick or None
```

---

## Integration Plan

### Phase 1: Build picker module (2-3 hrs)
- `tradingbot/strategy/__init__.py`
- `tradingbot/strategy/structure_picker.py` with `pick_structure()`
- Unit tests using cached chain data (AMD, SPY, QQQ)

### Phase 2: Wire into pipeline L3 (30 min)
- Insert L3 step in [pipeline.py](tradingbot/graph/pipeline.py) before sizing
- Populate `ctx.market_data["max_profit"]`, `max_loss`, `delta_per_contract`, etc.
- Populate `ctx.strategy.selected_structure` for debaters

### Phase 3: Validate on 22-ticker scan (15 min)
- Run `python scripts/paper_trade.py --full-scan --dry-run`
- Target: ≥3 approved trades
- Check: rejection reasons shift from "Kelly negative" to other gates (Greeks, CVaR) or pass entirely

### Phase 4: Backtest validation (30 min)
- Update `scripts/backtest.py compute_daily_context()` to call picker
- Re-run backtest, measure: approval rate, expected EV, Sharpe vs old defaults
- Refit calibrator if accuracy changes meaningfully

---

## Risk Analysis

| Risk | Mitigation |
|------|-----------|
| Picker selects illiquid strikes (slippage) | Hard filter: bid > 0, OI > 50, spread/mid < 30% |
| Picker fails to find Kelly-viable structure for low p_bull (0.52-0.55) | Return None → pipeline rejects cleanly with reason "no viable structure" |
| Bullish bias in picker (debit spreads can't go wider than chain allows) | Try multiple widths; if all fail, fall back to outright LONG_CALL |
| Adds latency to scan (each ticker calls picker) | Chain is already cached; picker is pure compute → negligible |
| Backtest doesn't have chain data | Add a synthetic R:R estimator based on IV rank for backtest mode |

---

## Success Metrics

| Metric | Current | Target | Gate |
|--------|---------|--------|------|
| 22-ticker scan approvals | 0/22 | **3-5/22** | Live scan validation |
| Mean odds (R:R) on selected structures | 0.667 (hardcoded) | **0.85-1.20** | Picker output |
| Kelly negative rejections | 13/22 | **0/22** | When p_bull ≥ 0.55 and viable structure exists |
| Backtest Sharpe (v2) | +0.348 | **≥+0.50** | Better trade selection |
| Time to picker decision | n/a | **<50ms per ticker** | Performance |

---

## What This Unblocks

Once the structure picker is in place:
1. ✅ Real Kelly sizing on real spreads → realistic position counts
2. ✅ Greeks book limits gate becomes meaningful (currently hardcoded greeks too)
3. ✅ CVaR stress test runs on actual structures (not stubbed P&L)
4. ✅ Exit engine works on real positions with real targets
5. ✅ Paper trading cutover possible (was blocked by lack of executable trades)

---

## Recommendation: Build It Tonight

This is the **highest-leverage missing piece**. The signal improvements from P1+P2 are wasted without it. Estimated 3-4 hours total including tests + validation.

**Proposed sequence:**
1. Build `structure_picker.py` (~150 lines, includes liquidity filter + spread selection)
2. Add `test_structure_picker.py` (~80 lines, 6-8 cases using cached chain)
3. Wire into pipeline at L3 (before sizing)
4. Run 22-ticker scan, validate ≥3 approvals
5. Commit + push, then run validation backtest

Want me to start with the picker module?
