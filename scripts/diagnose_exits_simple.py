#!/usr/bin/env python3
"""Diagnose why exit rules aren't firing - using synthetic positions.

This shows the PROBLEM with how positions are loaded from broker.
"""

import sys
import os
import datetime
import time
from zoneinfo import ZoneInfo

os.environ.setdefault("TRADINGBOT_FLAG_EXIT", "1")

from dotenv import load_dotenv
load_dotenv()

from tradingbot.execution.exit import ExitEngine
from tradingbot.execution.monitor import OpenPosition, Leg

ET = ZoneInfo("America/New_York")

def now_et():
    return datetime.datetime.now(ET)

def demo_exit_rules():
    """Show why exit rules don't fire when loaded from broker."""

    print("\n" + "="*100)
    print("EXIT RULES DIAGNOSTIC - WHY RULES DON'T FIRE")
    print("="*100)

    # Create two positions: one as the broker loads it, one correctly configured

    print("\n[SCENARIO 1: Position loaded from broker (BROKEN)]")
    print("-" * 100)

    broken_pos = OpenPosition(
        trade_id="broker-SPY-synthetic",
        ticker="SPY",
        underlying="SPY",
        structure="synthetic",
        direction="LONG",
        entry_price=500.00,
        entry_premium=500.00,
        entry_time=time.time() - (5 * 86400),  # 5 days ago
        entry_regime=0,
        entry_dte=30,
        contracts=1,
        max_profit_per_contract=0.0,  # <-- PROBLEM: 0!
        max_loss_per_contract=0.0,    # <-- PROBLEM: 0!
        current_pnl=-150.00,  # Position is down $150
        current_underlying_price=485.00,
        current_dte=25,
        peak_pnl=50.00,
        legs=[Leg(
            underlying="SPY",
            expiry=(now_et().date() + datetime.timedelta(days=25)),
            strike=500.0,
            right="C",
            side=1,
            contracts=1,
        )],
    )

    print(f"Position: SPY Call $500 (qty 1)")
    print(f"  Entry:              $500.00")
    print(f"  Current Price:      ${broken_pos.current_underlying_price}")
    print(f"  Current P&L:        ${broken_pos.current_pnl:+.2f}  [WARNING] DOWN $150!")
    print(f"  Max Profit/Contract: ${broken_pos.max_profit_per_contract:.2f}  [PROBLEM] ZERO")
    print(f"  Max Loss/Contract:   ${broken_pos.max_loss_per_contract:.2f}  [PROBLEM] ZERO")
    print(f"  Peak P&L:           ${broken_pos.peak_pnl:+.2f}")
    print(f"  DTE:                {broken_pos.current_dte}")

    engine = ExitEngine()
    decision = engine.evaluate(broken_pos, {})

    print(f"\n  Exit Decision:")
    print(f"    Fired:          {decision.fired}")
    print(f"    Should Exit:    {decision.should_exit}")
    print(f"    Reason:         {decision.reason}")
    print(f"    Detail:         {decision.detail}")

    print(f"\n  [PROBLEM] PROBLEM ANALYSIS:")
    print(f"    - STOP_LOSS rule: Checks if P&L <= -max_loss*0.75")
    print(f"      With max_loss=0: checks if -150 <= 0 (TRUE, but threshold is meaningless!)")
    print(f"      Actually fires because P&L < 0, not because it exceeded risk threshold")
    print(f"    - Real P&L range [-150, +150] but rules expect specific max_loss/max_profit")
    print(f"    - Can't determine when to exit on profit target (what's max profit?)")
    print(f"    - Can't determine when to exit on stop loss (what's max loss?)")

    # Now show the correct way
    print("\n[SCENARIO 2: Position with correct max_profit/max_loss (FIXED)]")
    print("-" * 100)

    fixed_pos = OpenPosition(
        trade_id="broker-SPY-fixed",
        ticker="SPY",
        underlying="SPY",
        structure="long_call",
        direction="BULLISH",
        entry_price=500.00,
        entry_premium=500.00,
        entry_time=time.time() - (5 * 86400),
        entry_regime=0,
        entry_dte=30,
        contracts=1,
        max_profit_per_contract=2000.00,   # Max profit: $2.00 * 100 = $200
        max_loss_per_contract=200.00,      # Max loss: premium paid = $2.00 * 100 = $200
        current_pnl=-150.00,
        current_underlying_price=485.00,
        current_dte=25,
        peak_pnl=50.00,
        legs=[Leg(
            underlying="SPY",
            expiry=(now_et().date() + datetime.timedelta(days=25)),
            strike=500.0,
            right="C",
            side=1,
            contracts=1,
        )],
    )

    print(f"Position: SPY Call $500 (qty 1)")
    print(f"  Entry:              $500.00")
    print(f"  Current Price:      ${fixed_pos.current_underlying_price}")
    print(f"  Current P&L:        ${fixed_pos.current_pnl:+.2f}  (lost $150)")
    print(f"  Max Profit/Contract: ${fixed_pos.max_profit_per_contract:.2f}  [OK]")
    print(f"  Max Loss/Contract:   ${fixed_pos.max_loss_per_contract:.2f}  [OK]")
    print(f"  Peak P&L:           ${fixed_pos.peak_pnl:+.2f}")
    print(f"  DTE:                {fixed_pos.current_dte}")

    decision = engine.evaluate(fixed_pos, {})

    print(f"\n  Exit Decision:")
    print(f"    Fired:          {decision.fired}")
    print(f"    Should Exit:    {decision.should_exit}")
    print(f"    Reason:         {decision.reason}")
    print(f"    Detail:         {decision.detail}")

    print(f"\n  [FIXED] FIXED ANALYSIS:")
    print(f"    - STOP_LOSS rule: P&L -150 >= threshold -150 (75% of 200)")
    print(f"      Rule correctly fires because loss equals 75% of max loss")
    print(f"    - PROFIT_TARGET rule: Would fire when P&L >= 50% of max_profit = $100")
    print(f"    - Rules now have proper thresholds based on risk/reward")

    # Show P&L remarking issue
    print("\n[SCENARIO 3: P&L Remarking Problem in _run_exit_only]")
    print("-" * 100)

    print(f"Current code in _run_exit_only (line 562-564):")
    print(f"  price_move = fresh_price - pos.entry_price")
    print(f"  approx_pnl = pos.delta * price_move * pos.contracts")
    print(f"\nWith positions from broker:")
    print(f"  price_move = $485 - $500 = -$15")
    print(f"  pos.delta = 0.0  (from broker, not calculated!)")
    print(f"  approx_pnl = 0.0 * -15 * 1 = $0.00  [BROKEN]")
    print(f"\nResult:")
    print(f"  - current_pnl stays at initial value, never updates")
    print(f"  - Exit rules always see the SAME P&L, never see fresh marks")
    print(f"  - Trailing stop can't work (relies on peak_pnl changes)")
    print(f"\nSolution options:")
    print(f"  A) Fetch live option chain -> compute fresh delta/theta -> update P&L")
    print(f"  B) Store original structure/parameters -> reconstruct max_profit/max_loss")
    print(f"  C) Use bid/ask spread to estimate position value instead of delta")

    # Show which rules would fire
    print("\n[EXIT RULES PRIORITY ORDER]")
    print("-" * 100)

    print(f"""
With BROKEN position (max_loss=0, max_profit=0, current_pnl=-$150):
  1. EXPIRED (DTE<=0):            [NO] (DTE=25)
  2. STOP_LOSS (loss>=75%):       [?] Fires on ANY negative P&L (threshold=0)
  3. TRAILING_STOP (drop from peak): [?] Confusing (peak=$50, current=-$150)
  4. DTE_EMERGENCY (<=7):         [NO] (DTE=25)
  5. HARD_EOD_CUTOFF (3:55 PM):  Depends on time
  6. FRIDAY_SWEEP:                 Depends on day/time
  7. PROFIT_TARGET (50% max):      [NO] Never (max_profit=0)
  8. TIME_STOP (>21 days):        [NO] (age=5 days)
  9. REGIME_FLIP:                  Depends on context

With FIXED position (max_loss=$200, max_profit=$2000, current_pnl=-$150):
  1. EXPIRED:                      [NO]
  2. STOP_LOSS:                    [YES] (-150 <= -150)
  3. ... (rest never checked, first rule wins)
""")

    # Summary table
    print("\n[ROOT CAUSE SUMMARY]")
    print("-" * 100)

    table_data = [
        ["Problem", "Current Code", "Impact"],
        ["=" * 40, "=" * 40, "=" * 40],
        ["max_loss=0 in broker pos", "_load_live_positions() sets to 0.0", "STOP_LOSS rule broken"],
        ["max_profit=0 in broker pos", "_load_live_positions() sets to 0.0", "PROFIT_TARGET rule broken"],
        ["delta=0 in broker pos", "_load_live_positions() sets to 0.0", "P&L never updates in exit-only"],
        ["entry_dte=0 in broker pos", "_load_live_positions() sets to 0.0", "DTE_EMERGENCY can't fire initially"],
        ["", "", ""],
        ["Solution 1", "Reconstruct from structure", "Need to store structure_id at entry"],
        ["Solution 2", "Live chain data", "Fetch fresh greeks each exit check"],
        ["Solution 3", "Store at entry time", "Save max_loss/max_profit with position"],
    ]

    for row in table_data:
        if row[0] == "=" * 40:
            print("-" * 120)
        else:
            print(f"  {row[0]:<40} | {row[1]:<40} | {row[2]:<40}")

if __name__ == "__main__":
    demo_exit_rules()
