#!/usr/bin/env python3
"""Diagnose why exit rules aren't firing for current positions.

Usage:
    python scripts/diagnose_exits.py
"""

import sys
import os
import datetime
import json
import time
from zoneinfo import ZoneInfo
from pathlib import Path

os.environ.setdefault("TRADINGBOT_FLAG_EXIT", "1")

from dotenv import load_dotenv
load_dotenv()

from tradingbot.execution.exit import ExitEngine, ExitReason, ExitUrgency
from tradingbot.execution.monitor import PositionMonitor, OpenPosition
from tradingbot.execution.moomoo_broker import MoomooBroker

ET = ZoneInfo("America/New_York")

def now_et():
    return datetime.datetime.now(ET)

def load_positions_from_broker():
    """Query the broker for live positions."""
    print("[*] Querying broker for live positions...")
    try:
        broker = MoomooBroker()
        positions = broker.query_positions()
        if not positions:
            print("    [!] No positions found on broker")
            return []
        print(f"    [+] Found {len(positions)} position(s)")
        return positions
    except Exception as e:
        print(f"    [!] Broker query failed: {e}")
        return []

def build_position_table(broker_positions):
    """Build diagnostic table showing position data."""
    rows = []

    for bp in broker_positions:
        rows.append({
            "Ticker": bp.underlying,
            "Strike": f"${bp.strike}",
            "Right": bp.right,
            "Qty": bp.quantity,
            "Expiry": bp.expiry.strftime("%Y-%m-%d"),
            "Entry Price": f"${bp.avg_price:.2f}",
            "Current Price": f"${bp.current_price:.2f}",
            "Current P&L": f"${bp.pnl:+.2f}",
            "Days to Expiry": (bp.expiry - now_et().date()).days,
        })

    return rows

def evaluate_exit_rules(broker_position):
    """Create a synthetic OpenPosition from broker data and evaluate all exit rules."""
    from tradingbot.execution.monitor import Leg

    trade_id = f"broker-{broker_position.underlying}-{broker_position.strike}-{broker_position.right}"

    # Build the position object
    pos = OpenPosition(
        trade_id=trade_id,
        ticker=broker_position.underlying,
        underlying=broker_position.underlying,
        structure="synthetic",
        direction="LONG" if broker_position.quantity > 0 else "SHORT",
        entry_price=broker_position.avg_price,
        entry_premium=broker_position.avg_price,
        entry_time=time.time(),
        entry_regime=0,
        entry_dte=30,  # Assume 30 DTE for demo
        contracts=abs(broker_position.quantity),
        max_profit_per_contract=0.0,  # <-- ISSUE: This is 0!
        max_loss_per_contract=0.0,    # <-- ISSUE: This is 0!
        current_pnl=broker_position.pnl,
        current_underlying_price=broker_position.current_price,
        current_dte=(broker_position.expiry - now_et().date()).days,
        peak_pnl=max(0.0, broker_position.pnl),
        legs=[Leg(
            underlying=broker_position.underlying,
            expiry=broker_position.expiry,
            strike=broker_position.strike,
            right=broker_position.right,
            side=1 if broker_position.quantity > 0 else -1,
            contracts=abs(broker_position.quantity),
        )],
    )

    # Evaluate all rules
    engine = ExitEngine()
    decision = engine.evaluate(pos, {})

    return pos, decision

def main():
    print("\n" + "="*80)
    print("EXIT RULES DIAGNOSTIC")
    print("="*80)

    # Load positions
    broker_positions = load_positions_from_broker()
    if not broker_positions:
        print("\n[!] No positions to diagnose. Nothing to do.")
        return

    # Display position summary table
    print("\n[CURRENT POSITIONS]")
    print("-" * 80)
    rows = build_position_table(broker_positions)
    if rows:
        headers = list(rows[0].keys())
        print("  " + " | ".join(f"{h:15}" for h in headers))
        print("  " + "-" * 110)
        for row in rows:
            print("  " + " | ".join(f"{str(row[h]):15}" for h in headers))
    else:
        print("  No positions")

    # Evaluate exit rules for each position
    print("\n[EXIT RULE EVALUATION]")
    print("-" * 80)

    for bp in broker_positions:
        pos, decision = evaluate_exit_rules(bp)

        print(f"\n{bp.underlying} {bp.right} ${bp.strike} (qty={bp.quantity})")
        print(f"  Current P&L:       ${pos.current_pnl:+.2f}")
        print(f"  Current DTE:       {pos.current_dte}")
        print(f"  Max Profit/Contract: ${pos.max_profit_per_contract:.2f}")
        print(f"  Max Loss/Contract:   ${pos.max_loss_per_contract:.2f}")
        print(f"  Peak P&L:          ${pos.peak_pnl:+.2f}")

        # Show rule checks
        print(f"  ")
        print(f"  Exit Rule Results:")
        print(f"    Fired:            {decision.fired}")
        print(f"    Should Exit:      {decision.should_exit}")
        print(f"    Needs Review:     {decision.needs_review}")
        if decision.reason:
            print(f"    Reason:           {decision.reason.value}")
        if decision.urgency:
            print(f"    Urgency:          {decision.urgency.value}")
        print(f"    Detail:           {decision.detail}")

        # Analysis
        print(f"  ")
        print(f"  Analysis:")
        if pos.max_loss_per_contract == 0.0 and pos.max_profit_per_contract == 0.0:
            print(f"    ❌ MAX_PROFIT and MAX_LOSS both 0 - synthetic position from broker")
            print(f"       → Stop loss rule will never fire (threshold = 0)")
            print(f"       → Profit target rule will never fire (target = 0)")
            print(f"       → These values are NOT SET when loading from broker")

        if pos.current_dte <= 0:
            print(f"    ⚠️  DTE <= 0 - position should be expired (EXPIRED rule should fire)")
        elif pos.current_dte <= 5:
            print(f"    ⚠️  DTE low ({pos.current_dte}) - DTE_EMERGENCY rule may fire")

        if pos.current_pnl < 0 and abs(pos.current_pnl) > abs(pos.max_loss * 0.75 if pos.max_loss > 0 else 1e9):
            print(f"    ⚠️  Loss is severe - STOP_LOSS rule should fire if max_loss was set")

        print()

    # Summary
    print("\n[ROOT CAUSE ANALYSIS]")
    print("-" * 80)
    print("""
When positions are loaded from the broker (_load_live_positions), they are
created with:
    max_profit_per_contract = 0.0
    max_loss_per_contract = 0.0

This breaks the exit rules because:

1. STOP_LOSS rule:
   - Checks: current_pnl <= -max_loss * stop_loss_pct
   - With max_loss=0: threshold = 0, so only fires on negative P&L
   - Should fire when loss exceeds a % of max_loss, but max_loss unknown

2. PROFIT_TARGET rule:
   - Checks: current_pnl >= max_profit * target_pct
   - With max_profit=0: target = 0, so only fires on positive P&L
   - Should fire at specific profit target, but target unknown

3. P&L REMARKING in _run_exit_only:
   - Uses: approx_pnl = delta * price_move * contracts
   - With delta=0 (from broker): approx_pnl = 0 always
   - P&L never updates, stays at initial value

SOLUTION:
  → Need to reconstruct max_profit and max_loss from broker position data
  → Or fetch live chain data to calculate delta/greeks for proper P&L remarking
  → Or store the original max_profit/max_loss in position metadata at entry time
""")

if __name__ == "__main__":
    main()
