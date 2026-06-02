# Exit Rules Reference - Complete Decision Matrix

## Quick Reference: When Each Rule Fires

| Priority | Rule | Condition | Urgency | Action |
|---|---|---|---|---|
| 0 | **EXPIRED** | `current_dte <= 0` | IMMEDIATE | Remove from book |
| 1 | **STOP_LOSS** | `P&L <= -max_loss * 75%` | IMMEDIATE | Close now (market order) |
| 2 | **TRAILING_STOP** | `P&L drops 10% from peak, floor at -max_loss*10%` | IMMEDIATE | Close now (market order) |
| 3 | **DTE_EMERGENCY** | Short-premium: `DTE <= 7` / Long: `DTE <= 5` | IMMEDIATE | Avoid assignment/gamma |
| 4 | **HARD_EOD_CUTOFF** | Intraday & `time >= 3:55 PM ET` | IMMEDIATE | Close at EOD |
| 5 | **FRIDAY_SWEEP** | Friday & `time >= 2:00 PM ET` & `DTE <= 30` | EXECUTE | Close with limit order |
| 6 | **PROFIT_TARGET** | Short-premium: `P&L >= 50% of max_profit` / Long: `P&L >= 100% of max_profit` | EXECUTE | Close with limit |
| 7 | **TIME_STOP** | `age >= 21 days` | EVALUATE | Flag for human review |
| 8 | **REGIME_FLIP** | `regime changed && consensus flipped against trade` | EXECUTE/EVALUATE | Close or review |

## Detailed Rule Definitions

### Rule 0: EXPIRED
**Fires when:** Options have passed their expiration date  
**Condition:** `current_dte <= 0`  
**Action:** Remove from book immediately (no execution, already expired)  
**Code:** [exit.py:243-258](file://c:\Users\pamed\Susheel\oa2-new\tradingbot\execution\exit.py#L243-L258)

**Example:**
```
DTE = 0 (today is expiration) or DTE = -1 (past expiration)
-> FIRE IMMEDIATELY (remove from book)
```

---

### Rule 1: STOP_LOSS
**Fires when:** Cumulative loss reaches 75% of maximum risk  
**Condition:** `current_pnl <= -max_loss * 0.75`  
**Urgency:** IMMEDIATE (market order acceptable)  
**Code:** [exit.py:260-282](file://c:\Users\pamed\Susheel\oa2-new\tradingbot\execution\exit.py#L260-L282)

**Calculation:**
```
max_loss = max_loss_per_contract * contracts
stop_threshold = -max_loss * 0.75

Example:
  max_loss_per_contract = $200
  contracts = 1
  max_loss = $200
  stop_threshold = -$150

Position is down -$151:
  -$151 <= -$150 ✓ → FIRE
```

**Applies to:** ALL position types (short premium, long, spreads, etc.)

---

### Rule 2: TRAILING_STOP
**Fires when:** Profit retraces more than 10% from the highest peak  
**Condition:** `current_pnl <= trailing_stop_threshold`  
**Threshold Calculation:**
- If never profitable (peak_pnl <= 0):
  ```
  threshold = -max_loss * 0.10  # 10% loss floor
  ```
- If profitable (peak_pnl > 0):
  ```
  threshold = max(peak_pnl * (1 - 0.10), -max_loss * 0.10)
  # Use peak as anchor, but never go below 10% loss floor
  ```

**Urgency:** IMMEDIATE (market order)  
**Code:** [exit.py:284-305](file://c:\Users\pamed\Susheel\oa2-new\tradingbot\execution\exit.py#L284-L305)

**Examples:**
```
Scenario A: Never made profit
  peak_pnl = 0
  max_loss = $200
  threshold = -$20
  current_pnl = -$25 -> FIRE (broke floor)

Scenario B: Was up $100, now down $50
  peak_pnl = $100
  current_pnl = -$50
  threshold = max($100 * 0.9, -$20) = max($90, -$20) = $90
  -$50 <= $90 ✓ -> FIRE (dropped too far from peak)

Scenario C: Was up $100, now up $85
  peak_pnl = $100
  current_pnl = $85
  threshold = max($100 * 0.9, -$20) = $90
  $85 <= $90 ✓ -> FIRE (10% pullback from peak)
```

**Key insight:** Trailing stop anchors to the peak and lets winners run until they give back 10%

---

### Rule 3: DTE_EMERGENCY
**Fires when:** Days to expiration reaches critical threshold  
**Conditions:**
- Short-premium structures (IC, verticals, calendars): `DTE <= 7`
  - Reason: Avoid assignment risk on short legs, gamma explosion
- Long structures (long calls/puts, debit spreads): `DTE <= 5`
  - Reason: Avoid liquidity crisis, theta crush accelerates

**Urgency:** IMMEDIATE  
**Code:** [exit.py:307-331](file://c:\Users\pamed\Susheel\oa2-new\tradingbot\execution\exit.py#L307-L331)

**Structure Classification:**
```
Short-premium structures (use 7-DTE threshold):
  - IRON_CONDOR
  - SHORT_PREMIUM_FADE
  - VERTICAL_CALL_SPREAD
  - VERTICAL_PUT_SPREAD
  - CALENDAR_CALL
  - CALENDAR_PUT
  - DIAGONAL_SPREAD

All others are treated as long (use 5-DTE threshold)
```

**Example:**
```
IRON_CONDOR with DTE = 7 -> FIRE IMMEDIATELY
Long call with DTE = 5 -> FIRE IMMEDIATELY
Long call with DTE = 6 -> No trigger (waits for DTE = 5)
```

---

### Rule 4: HARD_EOD_CUTOFF
**Fires when:** Market approaches close (3:55 PM ET)  
**Applies to:** Intraday positions ONLY  
**Intraday Definition:**
- Structure is `LONG_GAMMA_SCALP` OR `SHORT_PREMIUM_FADE`
- OR Entry DTE was 0 or 1 (0-DTE or 1-DTE strategies)

**Urgency:** IMMEDIATE  
**Code:** [exit.py:333-361](file://c:\Users\pamed\Susheel\oa2-new\tradingbot\execution\exit.py#L333-L361)

**Examples:**
```
12:30 PM ET, LONG_GAMMA_SCALP:
  Time < 3:55 PM -> No trigger, keep position

3:55 PM ET, LONG_GAMMA_SCALP:
  Time >= 3:55 PM -> FIRE (EOD cutoff)

3:56 PM ET, 0-DTE entry, any structure:
  Is intraday, time >= 3:55 PM -> FIRE

3:30 PM ET, multi-week IRON_CONDOR:
  Not intraday (entry_dte > 1) -> No trigger
```

---

### Rule 5: FRIDAY_SWEEP
**Fires when:** End of week liquidity cleanup for near-term positions  
**Conditions:**
- Day is Friday (weekday == 4)
- Time is 2:00 PM ET or later
- Current DTE <= 30

**Urgency:** EXECUTE (limit order ok, don't hold to final bell)  
**Code:** [exit.py:363-390](file://c:\Users\pamed\Susheel\oa2-new\tradingbot\execution\exit.py#L363-L390)

**Rationale:** Positions with < 30 DTE have enough time value to carry through weekend gap, but should be closed while market is liquid

**Examples:**
```
Thursday 2:00 PM, 30-DTE:
  Not Friday -> No trigger

Friday 1:59 PM, 30-DTE:
  Time < 2:00 PM -> No trigger

Friday 2:00 PM, 30-DTE:
  Friday && time >= 2:00 PM && DTE <= 30 -> FIRE

Friday 2:00 PM, 31-DTE:
  DTE > 30 -> No trigger (carry position into next week)

Friday 2:00 PM, 5-DTE:
  All conditions met -> FIRE (definitely close before weekend)
```

---

### Rule 6: PROFIT_TARGET
**Fires when:** Position reaches profit objectives  
**Thresholds:** Structure-dependent

**Short-premium (IC, verticals, calendars):**
- Target: `50% of max credit received`
- Example: Sold credit for $200 → Close when P&L reaches $100

**Long structures (long calls/puts, debit spreads):**
- Target: `100% gain` (2x the debit paid)
- Example: Paid $100 debit → Close when P&L reaches $100

**Urgency:** EXECUTE (limit order acceptable)  
**Code:** [exit.py:392-420](file://c:\Users\pamed\Susheel\oa2-new\tradingbot\execution\exit.py#L392-L420)

**Calculation:**
```
Short-premium (IC):
  max_profit = $300 credit
  target = max_profit * 0.50 = $150
  current_pnl >= $150 -> FIRE

Long call:
  max_profit = $2000
  target = max_profit * 1.00 = $2000 (double the debit)
  current_pnl >= $2000 -> FIRE (gained 100%)
```

---

### Rule 7: TIME_STOP
**Fires when:** Position has been held beyond risk tolerance window  
**Condition:** `age >= 21 days`  
**Urgency:** EVALUATE (flag for human review, not auto-close)  
**Action:** Log alert for trader to review  
**Code:** [exit.py:422-439](file://c:\Users\pamed\Susheel\oa2-new\tradingbot\execution\exit.py#L422-L439)

**Purpose:** Prevents positions from becoming "forever holders" with stale assumptions

**Example:**
```
Entry: May 12
Review date: June 2 (21 days later)
-> ALERT: "Position held 21.0 days >= 21 day limit. Review for close."
```

---

### Rule 8: REGIME_FLIP
**Fires when:** Market regime shifts invalidate trade thesis  
**Requires:** Context with `regime_id` and `consensus_direction`  
**Conditions:**

1. **Soft alert (needs_review=True):**
   - Regime changed (entry_regime != current_regime)
   - Consensus direction is still compatible with trade
   - Action: Flag for human review

2. **Hard close (should_exit=True):**
   - Regime changed (entry_regime != current_regime)
   - Consensus direction OPPOSES trade:
     - Trade is BULLISH but consensus is BEARISH
     - Trade is BEARISH but consensus is BULLISH
   - Action: Close at EXECUTE urgency

**Urgency:** EXECUTE (if consensus conflict) or EVALUATE (if regime-only)  
**Code:** [exit.py:441-499](file://c:\Users\pamed\Susheel\oa2-new\tradingbot\execution\exit.py#L441-L499)

**Examples:**
```
Entry regime: LOW_IV (regime_id=0)
Trade direction: BULLISH

Current regime: HIGH_VOL (regime_id=4) [FLIPPED]
Consensus: BULLISH [ALIGNED]
-> Alert only (regime changed but still bullish)

Current regime: HIGH_VOL (regime_id=4) [FLIPPED]
Consensus: BEARISH [CONFLICT]
-> FIRE (close position - bearish when we're bullish)
```

---

## Priority Order (First Rule Wins)

The exit engine evaluates rules in this **strict priority** order. The first rule that fires is executed; remaining rules are skipped:

1. ✓ EXPIRED - already expired, remove from book
2. ✓ STOP_LOSS - loss threshold hit, protect capital
3. ✓ TRAILING_STOP - profit retracement, lock in gains
4. ✓ DTE_EMERGENCY - assignment risk, gamma explosion
5. ✓ HARD_EOD_CUTOFF - end of day for intraday positions
6. ✓ FRIDAY_SWEEP - Friday close for near-term positions
7. ✓ PROFIT_TARGET - profit objective reached
8. ⚠️ TIME_STOP - long-held position review
9. ⚠️ REGIME_FLIP - market thesis invalidated

**Note:** Only first matching rule is returned. If STOP_LOSS fires, PROFIT_TARGET is never evaluated.

---

## Decision Output

Each exit evaluation returns an `ExitDecision` with:

```python
@dataclass
class ExitDecision:
    trade_id: str              # Position identifier
    should_exit: bool          # True = execute close order
    reason: ExitReason | None  # Which rule fired (or None)
    urgency: ExitUrgency | None # IMMEDIATE / EXECUTE / EVALUATE
    detail: str                # Human-readable explanation
    current_pnl: float         # Mark-to-market P&L
    current_dte: int           # Days remaining
    needs_review: bool = False # True = flag for human (TIME_STOP, REGIME_FLIP soft)
```

**Urgency Levels:**
- **IMMEDIATE:** Close NOW, market order if needed (STOP_LOSS, TRAILING_STOP, DTE_EMERGENCY, EOD, REGIME conflict)
- **EXECUTE:** Close at next good fill, limit order ok (FRIDAY_SWEEP, PROFIT_TARGET, REGIME conflict)
- **EVALUATE:** Flag for human review, don't auto-close (TIME_STOP, REGIME soft flip)

---

## Testing Each Rule

```bash
# Run comprehensive exit rule tests
python -m pytest tests/test_execution.py::test_exit_engine -v

# Run individual rule demos
python scripts/test_exit_fix.py
```

## Configuration

Override defaults in `tradingbot/core/config.py`:

```python
STOP_LOSS_PCT = 0.75              # 75% of max loss
TRAILING_STOP_PCT = 0.10          # 10% retracement
DTE_EMERGENCY_SHORT = 7           # 7-DTE for short-premium
DTE_EMERGENCY_LONG = 5            # 5-DTE for long
PROFIT_TARGET_SHORT = 0.50        # 50% of credit
PROFIT_TARGET_LONG = 1.00         # 100% gain (2x debit)
FRIDAY_SWEEP_DTE = 30             # Sweep sub-30-DTE
FRIDAY_SWEEP_HOUR = 14            # 2:00 PM ET
```

---

## Performance Notes

- **Evaluation time:** ~1-5ms per position (no external calls)
- **Chain fetch time:** ~500ms per expiry (moomoo API with timeout)
- **Total exit check cycle:** ~1-2s for 10 positions with fresh chain data
- **Optimal frequency:** Run exit checks every 60s in live trading
