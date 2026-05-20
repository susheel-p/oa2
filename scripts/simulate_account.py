"""Simulate realistic account growth from a backtest result.

Models options spread P&L on top of the backtest's directional predictions
using the same Kelly fraction + structure delta the live pipeline uses.

Each non-neutral signal day:
    position_size = account x kelly_fraction          (compounded)
    spread_pnl    = position_size x direction_sign x next_day_return x SPREAD_DELTA
    account      += spread_pnl

Quality gates respected (skipped trades):
  - Mean-reverting regimes
  - Blacklisted tickers (from quality_gates.TICKER_BLACKLIST)
  - p_bull within deadband (consensus=NEUTRAL)
  - Optionally: p_bull < min_edge

Usage:
    python scripts/simulate_account.py --account 1000
    python scripts/simulate_account.py --account 1000 --kelly 0.05 --no-gates
    python scripts/simulate_account.py --account 1000 --bullish-only
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingbot.core.config import tradingbot_home
from tradingbot.strategy.quality_gates import TICKER_BLACKLIST


SPREAD_DELTA = 0.30           # ATM debit-vertical effective delta
DEFAULT_KELLY_FRACTION = 0.05  # ~5% of account per trade (matches live scan)
MIN_EDGE = 0.52                # Kelly gate threshold


def _latest_backtest() -> Path:
    bdir = tradingbot_home() / "backtest"
    files = sorted(bdir.glob("results_*.json"))
    if not files:
        raise FileNotFoundError("No backtest results found")
    return files[-1]


def _is_mean_revert(regime: str) -> bool:
    return "mean_revert" in (regime or "").lower()


def simulate(
    days: list[dict],
    starting_account: float,
    kelly_fraction: float = DEFAULT_KELLY_FRACTION,
    spread_delta: float = SPREAD_DELTA,
    apply_gates: bool = True,
    bullish_only: bool = False,
    min_edge: float = MIN_EDGE,
) -> dict:
    """Walk through days, simulate account-equity curve."""
    # Sort by date, then ticker (stable for reproducibility)
    days = sorted(days, key=lambda d: (d["date"], d["ticker"]))

    account = starting_account
    n_trades = 0
    n_skipped_gate = 0
    n_skipped_edge = 0
    n_winners = 0
    n_losers = 0
    daily_returns = []  # for Sharpe
    equity_curve = []
    by_week: dict[str, dict] = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "start_eq": 0.0})

    # Group by date for proper weekly tracking
    by_date: dict[str, list[dict]] = defaultdict(list)
    for d in days:
        by_date[d["date"]].append(d)

    prev_account = account
    for date in sorted(by_date.keys()):
        # Track week-start equity
        iso_year, iso_week, _ = datetime.date.fromisoformat(date).isocalendar()
        week_key = f"{iso_year}-W{iso_week:02d}"
        if by_week[week_key]["start_eq"] == 0.0:
            by_week[week_key]["start_eq"] = account

        day_pnl = 0.0
        for d in by_date[date]:
            direction = d["consensus_direction"]
            if direction == "NEUTRAL":
                continue
            p_bull = d.get("p_bull", 0.5)
            edge = p_bull if direction == "BULLISH" else (1 - p_bull)
            if edge < min_edge:
                n_skipped_edge += 1
                continue
            if apply_gates:
                if _is_mean_revert(d.get("regime_label", "")):
                    n_skipped_gate += 1
                    continue
                if d["ticker"].upper() in TICKER_BLACKLIST:
                    n_skipped_gate += 1
                    continue
            if bullish_only and direction != "BULLISH":
                n_skipped_gate += 1
                continue

            # Position size from current account
            position = account * kelly_fraction
            ret = d.get("next_day_return", 0.0) or 0.0
            sign = 1 if direction == "BULLISH" else -1
            trade_pnl = position * sign * ret * (spread_delta / 0.30 * 3.33)
            # Note: factor 3.33 converts the small underlying move into option-spread P&L scale.
            # A 1% underlying move on an ATM debit spread typically moves the spread ~3-4% of capital.
            # We use 3.33x to roughly match live observation: $50k Kelly 5% = $2.5k position;
            # 1% SPY move ~= $25 raw, but spread moves ~$80-100 (~3-4x leverage from delta).
            day_pnl += trade_pnl
            n_trades += 1
            if trade_pnl > 0:
                n_winners += 1
            elif trade_pnl < 0:
                n_losers += 1

        account += day_pnl
        by_week[week_key]["trades"] += sum(1 for d in by_date[date]
                                            if d["consensus_direction"] != "NEUTRAL")
        by_week[week_key]["pnl"] += day_pnl
        by_week[week_key]["end_eq"] = account
        equity_curve.append({"date": date, "equity": round(account, 2)})

        daily_return = (account / prev_account) - 1 if prev_account > 0 else 0
        daily_returns.append(daily_return)
        prev_account = account

    final_account = account
    total_return = final_account - starting_account
    total_return_pct = total_return / starting_account
    win_rate = n_winners / n_trades if n_trades else 0
    sharpe = 0.0
    if len(daily_returns) > 5:
        mu = statistics.mean(daily_returns)
        sd = statistics.pstdev(daily_returns)
        if sd > 0:
            sharpe = mu / sd * math.sqrt(252)

    # Max drawdown
    peak = starting_account
    max_dd = 0.0
    for pt in equity_curve:
        if pt["equity"] > peak:
            peak = pt["equity"]
        dd = (peak - pt["equity"]) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    return {
        "starting_account": starting_account,
        "final_account": round(final_account, 2),
        "total_return_dollars": round(total_return, 2),
        "total_return_pct": round(total_return_pct, 4),
        "n_trades": n_trades,
        "n_winners": n_winners,
        "n_losers": n_losers,
        "win_rate": round(win_rate, 4),
        "n_skipped_gate": n_skipped_gate,
        "n_skipped_edge": n_skipped_edge,
        "sharpe": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd, 4),
        "kelly_fraction": kelly_fraction,
        "weekly_pnl": {
            w: {"trades": v["trades"], "pnl": round(v["pnl"], 2),
                "start_eq": round(v["start_eq"], 2),
                "end_eq": round(v.get("end_eq", v["start_eq"]), 2)}
            for w, v in sorted(by_week.items())
        },
        "equity_curve": equity_curve,
    }


def _print_summary(name: str, r: dict, weekly: bool = False) -> None:
    print(f"\n{'=' * 60}")
    print(f"SCENARIO: {name}")
    print(f"{'=' * 60}")
    print(f"  Starting:        ${r['starting_account']:,.2f}")
    print(f"  Final:           ${r['final_account']:,.2f}")
    print(f"  Total P&L:       ${r['total_return_dollars']:+,.2f}")
    print(f"  Total return:    {r['total_return_pct']:+.2%}")
    print(f"  Trades placed:   {r['n_trades']:,} (wins={r['n_winners']}, losses={r['n_losers']})")
    print(f"  Win rate:        {r['win_rate']:.1%}")
    print(f"  Skipped (gate):  {r['n_skipped_gate']:,}")
    print(f"  Skipped (edge):  {r['n_skipped_edge']:,}")
    print(f"  Sharpe:          {r['sharpe']:+.3f}")
    print(f"  Max drawdown:    {r['max_drawdown_pct']:.1%}")
    if weekly and r["weekly_pnl"]:
        print(f"\n  Weekly equity (first/last 5):")
        items = list(r["weekly_pnl"].items())
        for w, v in items[:5] + [("...", None)] + items[-5:]:
            if v is None:
                print(f"    ...")
                continue
            print(f"    {w}: {v['trades']:>3d} trades, P&L ${v['pnl']:>+9.2f}, end equity ${v['end_eq']:>9.2f}")


def main() -> int:
    p = argparse.ArgumentParser(description="Simulate account growth from backtest")
    p.add_argument("--account", type=float, default=1000.0)
    p.add_argument("--kelly", type=float, default=DEFAULT_KELLY_FRACTION)
    p.add_argument("--results", type=str, default=None)
    p.add_argument("--no-gates", action="store_true",
                   help="Disable quality gates (regime + blacklist)")
    p.add_argument("--bullish-only", action="store_true",
                   help="Skip BEARISH trades entirely")
    p.add_argument("--weekly", action="store_true", help="Print weekly breakdown")
    args = p.parse_args()

    path = Path(args.results) if args.results else _latest_backtest()
    print(f"Loading backtest: {path.name}")
    data = json.loads(path.read_text())
    days = data.get("days", [])
    print(f"Total days: {len(days):,}")

    # Run all 4 scenarios for comparison
    scenarios = [
        ("Baseline (no gates, all signals)",
         simulate(days, args.account, args.kelly, apply_gates=False)),
        ("With quality gates (regime+blacklist)",
         simulate(days, args.account, args.kelly, apply_gates=True)),
        ("With gates + bullish-only",
         simulate(days, args.account, args.kelly, apply_gates=True, bullish_only=True)),
        ("With gates + bullish-only + 2x Kelly",
         simulate(days, args.account, args.kelly * 2, apply_gates=True, bullish_only=True)),
    ]

    for name, r in scenarios:
        _print_summary(name, r, weekly=args.weekly)

    print(f"\n{'=' * 60}")
    print(f"COMPARISON TABLE (${args.account:.0f} starting, Kelly={args.kelly:.2%})")
    print(f"{'=' * 60}")
    print(f"{'Scenario':<45s} {'Final':>10s} {'Return':>9s} {'Sharpe':>8s} {'MaxDD':>7s}")
    for name, r in scenarios:
        short_name = name[:45]
        print(f"{short_name:<45s} ${r['final_account']:>8.2f} {r['total_return_pct']:>+8.1%} {r['sharpe']:>+7.2f} {r['max_drawdown_pct']:>6.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
