#!/usr/bin/env python3
"""
2-week paper trading validation harness.

Week 1: Shadow mode — log signals, no real trades
Week 2: Paper trades at 25% Kelly size — validate fills & P&L

Usage:
  python scripts/validate_paper_trading.py --mode shadow --days 5
  python scripts/validate_paper_trading.py --mode paper --size 0.25 --days 10
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any

# Import backtest tracker for comparison
sys.path.insert(0, str(Path(__file__).parent.parent))


def load_backtest_baseline() -> dict[str, Any]:
    """Load latest backtest results for comparison."""
    results_dir = Path.home() / ".tradingbot" / "backtest"
    results_files = sorted(results_dir.glob("results_*.json"))
    if not results_files:
        return {}

    latest = results_files[-1]
    with open(latest) as f:
        data = json.load(f)
    return data.get("metrics", {})


def print_shadow_checklist(day: int) -> None:
    """Print daily shadow mode validation checklist."""
    print(f"\n{'='*70}")
    print(f"SHADOW MODE - DAY {day} CHECKLIST")
    print(f"{'='*70}\n")

    print("SIGNAL VALIDATION:")
    print("  [ ] Run:  python scripts/paper_trade.py --shadow")
    print("  [ ] Check logs: tail -f ~/.tradingbot/logs/paper_*.log")
    print("  [ ] Verify consensus direction generated (BULLISH/BEARISH/NEUTRAL)")
    print("  [ ] Confirm income debater votes (APPROVE/REJECT/ABSTAIN)")
    print("  [ ] Verify flow debater conviction in [0.3, 0.85]")
    print()

    print("METRICS TO RECORD:")
    print("  ✓ # of signals generated today")
    print("  ✓ % BULLISH vs BEARISH consensus")
    print("  ✓ Income debater: % APPROVE vs ABSTAIN")
    print("  ✓ Flow debater: avg conviction")
    print("  ✓ Directional debater: regime accuracy (if trend day)")
    print()

    print("DATA COLLECTION:")
    print(f"  [ ] Save to: ~/.tradingbot/validation/shadow_day{day}.json")
    print("  [ ] Include: date, signals, debater votes, p_bull distribution")
    print()


def print_paper_checklist(day: int, week: int) -> None:
    """Print daily paper trading validation checklist."""
    print(f"\n{'='*70}")
    print(f"PAPER TRADING - WEEK {week}, DAY {day} CHECKLIST")
    print(f"{'='*70}\n")

    print("ENTRY VALIDATION:")
    print("  [ ] Run:  python scripts/paper_trade.py --entry-only")
    print("  [ ] Verify moomoo connection (OpenD on localhost:11111)")
    print("  [ ] Check Kelly sizing output")
    print("  [ ] Confirm position size ≤ 1% of account")
    print()

    print("TRADE MONITORING:")
    print("  [ ] Log all fills: ticker, side, qty, entry_price, ts")
    print("  [ ] Record slippage: (fill_price - signal_price) / signal_price")
    print("  [ ] Track P&L intraday (vs backtest expected return)")
    print("  [ ] Monitor exit triggers (50% profit, stop loss, EOD)")
    print()

    print("METRICS TO RECORD:")
    print("  ✓ # trades entered today")
    print("  ✓ Avg entry slippage (vs ±2% assumption)")
    print("  ✓ Realized P&L on closed positions")
    print("  ✓ Win rate so far (% winning trades)")
    print("  ✓ Profit target hit rate (% closed via 50% target)")
    print("  ✓ Greeks exposure (delta, vega, theta)")
    print()

    print("RISK CHECKS:")
    print("  [ ] Daily P&L < -2% of account? → STOP ENTRIES")
    print("  [ ] Greeks within caps? (delta ±0.30, vega ±0.25)")
    print("  [ ] Consensus accuracy ≥ 50% (vs 53.7% backtest)?")
    print()


def print_end_of_week_summary(week: int, metrics: dict[str, Any]) -> None:
    """Print end-of-week summary and decision point."""
    print(f"\n{'='*70}")
    print(f"END OF WEEK {week} - DECISION POINT")
    print(f"{'='*70}\n")

    if week == 1:
        print("SHADOW MODE COMPLETION CRITERIA:")
        print(f"  [{'✓' if metrics.get('consensus_accuracy', 0) >= 0.50 else '✗'}] Consensus accuracy ≥ 50% (backtest: 53.7%)")
        print(f"  [{'✓' if metrics.get('income_votes_pct', 0) > 0.20 else '✗'}] Income debater: ≥ 20% non-ABSTAIN votes")
        print(f"  [{'✓' if metrics.get('signal_count', 0) >= 5 else '✗'}] Generated ≥ 5 valid signals")
        print(f"  [{'✓' if metrics.get('no_errors', False) else '✗'}] No regime classifier crashes")
        print()

        all_pass = (
            metrics.get('consensus_accuracy', 0) >= 0.50 and
            metrics.get('income_votes_pct', 0) > 0.20 and
            metrics.get('signal_count', 0) >= 5 and
            metrics.get('no_errors', False)
        )

        if all_pass:
            print("✅ READY FOR PAPER TRADING")
            print("   → Proceed to Week 2 at 25% Kelly size")
        else:
            print("⚠️  MARGINAL - INVESTIGATE")
            print("   → Check income debater logic (too many ABSTAIN votes?)")
            print("   → Verify regime classifier on live data")
            print("   → Consider extending shadow mode 1 more week")

    else:  # week 2
        print("PAPER TRADING COMPLETION CRITERIA:")
        print(f"  [{'✓' if metrics.get('win_rate', 0) >= 0.45 else '✗'}] Win rate ≥ 45% (backtest: 56%)")
        print(f"  [{'✓' if metrics.get('total_pnl', 0) >= 0 else '✗'}] Total P&L ≥ 0% (positive overall)")
        print(f"  [{'✓' if metrics.get('avg_slippage', 0) <= 0.025 else '✗'}] Avg slippage ≤ 2.5% (backtest: ±2%)")
        print(f"  [{'✓' if metrics.get('trade_count', 0) >= 10 else '✗'}] ≥ 10 trades executed")
        print()

        all_pass = (
            metrics.get('win_rate', 0) >= 0.45 and
            metrics.get('total_pnl', 0) >= 0 and
            metrics.get('avg_slippage', 0) <= 0.025 and
            metrics.get('trade_count', 0) >= 10
        )

        if all_pass:
            print("✅ READY FOR LIVE TRADING")
            print("   → Proceed to 50% Kelly size on real account")
            print("   → Start with $5K–$10K allocation")
        elif metrics.get('total_pnl', 0) >= -0.01 and metrics.get('win_rate', 0) >= 0.40:
            print("⚠️  MARGINAL - CONTINUE WITH CAUTION")
            print("   → P&L near breakeven (slippage impact confirmed)")
            print("   → Stay at 25% size for 1 more week")
            print("   → Tighten profit target to 40% (vs 50% backtest)")
        else:
            print("❌ DO NOT PROCEED")
            print("   → Win rate < 40% OR negative P&L")
            print("   → Audit debater logic / regime detection")
            print("   → Return to shadow mode, increase sample size")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="2-week paper trading validation")
    parser.add_argument("--mode", choices=["shadow", "paper"], default="shadow",
                        help="shadow (Week 1) or paper (Week 2)")
    parser.add_argument("--days", type=int, default=5, help="Days to track")
    parser.add_argument("--size", type=float, default=0.25, help="Kelly fraction (paper only)")
    parser.add_argument("--week", type=int, default=1, help="Which week (1 or 2)")
    args = parser.parse_args()

    # Create validation directory
    val_dir = Path.home() / ".tradingbot" / "validation"
    val_dir.mkdir(parents=True, exist_ok=True)

    # Load backtest baseline
    baseline = load_backtest_baseline()
    print(f"\nBacktest Baseline Loaded:")
    print(f"  Consensus accuracy: {baseline.get('consensus_accuracy', 0):.1%}")
    print(f"  Win rate (MC): {baseline.get('mc_mean_win_rate', 0):.1%}")
    print(f"  Sharpe (MC): {baseline.get('mc_sharpe', 0):+.3f}")

    if args.mode == "shadow":
        print(f"\n{'='*70}")
        print(f"WEEK 1: SHADOW MODE (Days 1–{args.days})")
        print(f"{'='*70}")
        print("\nRun live signals with NO real trades. Validate:")
        print("  • Consensus accuracy matches backtest (≥50%)")
        print("  • Income debater voting (not stuck in ABSTAIN)")
        print("  • Regime detection on live data")
        print("  • No crashes or errors")

        for day in range(1, args.days + 1):
            print_shadow_checklist(day)

            if day == args.days:
                print("END OF WEEK 1 → Run: python scripts/validate_paper_trading.py --mode paper --week 2")

    else:  # paper
        print(f"\n{'='*70}")
        print(f"WEEK {args.week}: PAPER TRADING (Days 1–{args.days})")
        print(f"{'='*70}")
        print(f"\nRun with REAL moomoo paper account at {args.size:.0%} Kelly size. Validate:")
        print("  • Fills and slippage vs ±2% backtest assumption")
        print("  • Win rate ≥ 45% (vs 56% backtest)")
        print("  • P&L breakeven after slippage")
        print("  • Profit target hit rate vs backtest")

        for day in range(1, args.days + 1):
            print_paper_checklist(day, args.week)

            if day == args.days:
                print("\n" + "="*70)
                print("READY TO ANALYZE? Run:")
                print("  python scripts/validate_paper_trading.py --analyze")
                print("="*70)

    print(f"\n📋 Save results to: {val_dir}/week{args.week}_summary.json")
    print("   Include: daily signal counts, win_rate, slippage, P&L\n")


if __name__ == "__main__":
    main()
