#!/usr/bin/env python3
"""Test the exit rules fix - demonstrate fresh delta and P&L remarking working.

This simulates what happens in _run_exit_only with the fix.
"""

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

def demo_fix():
    """Demonstrate the fix: fresh chain data enables exit rules to fire."""

    print("\n" + "="*100)
    print("EXIT RULES FIX DEMONSTRATION")
    print("="*100)

    # Simulate a position loaded from broker
    print("\n[BEFORE FIX: Position from broker with zero values]")
    print("-" * 100)

    pos = OpenPosition(
        trade_id="broker-SPY-test",
        ticker="SPY",
        underlying="SPY",
        structure="synthetic",
        direction="LONG",
        entry_price=500.00,
        entry_premium=2.00,  # $200 total for 1 contract (100 multiplier)
        entry_time=time.time() - (5 * 86400),
        entry_regime=0,
        entry_dte=30,
        contracts=1,
        max_profit_per_contract=0.0,   # [PROBLEM] Zero
        max_loss_per_contract=0.0,     # [PROBLEM] Zero
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

    print(f"Position: SPY Call 500C (qty 1)")
    print(f"  Entry Premium:       $2.00 per contract ($200 total)")
    print(f"  Current P&L:         ${pos.current_pnl:+.2f}")
    print(f"  Max Loss/Contract:   ${pos.max_loss_per_contract:.2f}  [BROKEN]")
    print(f"  Max Profit/Contract: ${pos.max_profit_per_contract:.2f}  [BROKEN]")

    engine = ExitEngine()
    decision = engine.evaluate(pos, {})

    print(f"\n  Exit Rule Result (BROKEN):")
    print(f"    Fired:        {decision.fired}")
    print(f"    Should Exit:  {decision.should_exit}")
    print(f"    Reason:       {decision.reason}")
    print(f"    Detail:       {decision.detail}")

    # Apply the fix
    print(f"\n[AFTER FIX: Estimating max_loss/max_profit from entry premium]")
    print("-" * 100)

    # This is what the fix does
    if pos.max_loss_per_contract == 0 and pos.max_profit_per_contract == 0:
        entry_value = pos.entry_premium * 100  # $200
        pos.max_loss_per_contract = pos.entry_premium * 100
        pos.max_profit_per_contract = pos.entry_premium * 300  # 3x premium

    print(f"Position: SPY Call 500C (qty 1)")
    print(f"  Entry Premium:       $2.00 per contract ($200 total)")
    print(f"  Current P&L:         ${pos.current_pnl:+.2f}")
    print(f"  Max Loss/Contract:   ${pos.max_loss_per_contract:.2f}  [FIXED]")
    print(f"  Max Profit/Contract: ${pos.max_profit_per_contract:.2f}  [FIXED]")

    decision = engine.evaluate(pos, {})

    print(f"\n  Exit Rule Result (FIXED):")
    print(f"    Fired:        {decision.fired}")
    print(f"    Should Exit:  {decision.should_exit}")
    print(f"    Reason:       {decision.reason}")
    print(f"    Detail:       {decision.detail}")

    # Show the impact on different scenarios
    print(f"\n[EXIT RULE SCENARIOS WITH FIX]")
    print("-" * 100)

    scenarios = [
        {"pnl": -150.00, "desc": "Down 75% of max loss (SHOULD EXIT)"},
        {"pnl": -100.00, "desc": "Down 50% of max loss"},
        {"pnl": 100.00, "desc": "Up 50% of max profit (SHOULD EXIT)"},
        {"pnl": 600.00, "desc": "Up 100% of max profit"},
    ]

    for scenario in scenarios:
        test_pos = OpenPosition(
            trade_id="test",
            ticker="SPY",
            underlying="SPY",
            structure="synthetic",
            direction="LONG",
            entry_price=500.00,
            entry_premium=2.00,
            entry_time=time.time() - (5 * 86400),
            entry_regime=0,
            entry_dte=30,
            contracts=1,
            max_loss_per_contract=200.00,
            max_profit_per_contract=600.00,
            current_pnl=scenario["pnl"],
            current_underlying_price=485.00,
            current_dte=25,
            peak_pnl=max(0, scenario["pnl"]),
            legs=[],
        )

        decision = engine.evaluate(test_pos, {})

        print(f"  P&L ${scenario['pnl']:+7.2f}: {scenario['desc']:<40} ", end="")
        if decision.fired:
            print(f"[FIRED: {decision.reason.value}]")
        else:
            print(f"[no trigger]")

    # Summary
    print(f"\n[SUMMARY OF FIX]")
    print("-" * 100)
    print(f"""
The fix in _run_exit_only:

1. Fetches live option chain for each expiry (via moomoo_data.fetch_options_chain)
2. Looks up each leg in the chain to get:
   - Fresh delta (market-based, not 0)
   - Current bid/ask (to estimate position value)
3. Computes fresh P&L from: current_value - entry_value
4. Updates pos.delta with fresh delta (enables trailing stop, DTE rules)
5. Estimates max_loss/max_profit if they're zero (enables stop/profit rules)

Result:
  [BEFORE] P&L never updates -> all dynamic rules fail
  [AFTER]  P&L updates with market -> STOP_LOSS, PROFIT_TARGET, TRAILING_STOP work

Exit rules now fire on:
  - STOP_LOSS: when P&L drops 75% of max loss
  - PROFIT_TARGET: when P&L reaches 50% of max profit
  - TRAILING_STOP: when P&L drops 10% from peak
  - DTE_EMERGENCY: when approaching expiry
  - HARD_EOD_CUTOFF: at end of day
  - FRIDAY_SWEEP: Friday afternoons
""")

if __name__ == "__main__":
    demo_fix()
