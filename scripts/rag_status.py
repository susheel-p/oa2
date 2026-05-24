"""RAG Learning Status Report — Shows what learning is currently applied to the system.

Usage:
    python scripts/rag_status.py                    # Full KB status
    python scripts/rag_status.py --ticker SPY      # Status for one ticker
    python scripts/rag_status.py --regime 3        # Status for one regime (0-7)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from tabulate import tabulate

from tradingbot.learning.knowledge_base import KnowledgeBase, default_kb_path
from tradingbot.regime.state import VolState, TrendState, get_regime_id


def regime_name(vol_state: str, trend_state: str) -> str:
    """Format regime name from vol and trend states."""
    return f"{vol_state.upper()}_{trend_state.upper()}"


def print_kb_summary(kb: KnowledgeBase) -> None:
    """Print high-level KB status."""
    print("\n" + "=" * 80)
    print("KNOWLEDGE BASE STATUS")
    print("=" * 80)
    print(f"Last Updated:     {kb.last_updated}")
    print(f"Window:           {kb.window_days} days")
    print(f"Outcomes Used:    {kb.n_outcomes_used} trades")
    print(f"Tickers Tracked:  {len(kb.tickers)}")
    print(f"Regimes Tracked:  {len(kb.regimes)}")
    print(f"Debaters Tracked: {len(kb.debaters)}")
    print(f"Posteriors Saved: {sum(len(v) for v in kb.posteriors.values())} posterior(s)")


def print_ticker_analysis(kb: KnowledgeBase, ticker: str | None = None) -> None:
    """Print per-ticker accuracy and blacklist status."""
    print("\n" + "=" * 80)
    print("TICKER PERFORMANCE & BLACKLIST STATUS")
    print("=" * 80)

    tickers = [ticker.upper()] if ticker else sorted(kb.tickers.keys())
    rows = []

    for t in tickers:
        stats = kb.tickers.get(t)
        if not stats:
            continue

        is_blacklisted = stats.hit_rate < 0.43 and stats.dollar_weighted_win_rate < 0.45
        mult = 0.6 + (stats.hit_rate - 0.43) * 4.0 if stats.hit_rate > 0.43 else 0.6
        mult = max(0.50, min(1.30, mult))

        rows.append([
            t,
            stats.n_trades,
            f"{stats.hit_rate:.1%}",
            f"{stats.dollar_weighted_win_rate:.1%}",
            f"{stats.avg_pnl_pct:.2%}",
            f"{mult:.2f}x",
            "🚫 BLOCKED" if is_blacklisted else "✓ OK",
        ])

    if rows:
        headers = ["Ticker", "N Trades", "Hit Rate", "Dollar Win %", "Avg P&L %", "Multiplier", "Status"]
        print(tabulate(rows, headers=headers, tablefmt="grid"))
    else:
        print("(No ticker data available)")


def print_regime_analysis(kb: KnowledgeBase, regime_id: int | None = None) -> None:
    """Print per-regime accuracy and multiplier."""
    print("\n" + "=" * 80)
    print("REGIME PERFORMANCE & MULTIPLIERS")
    print("=" * 80)

    regimes = {k: v for k, v in kb.regimes.items()}
    if regime_id is not None:
        # Filter to specific regime
        pass

    rows = []
    for regime_label, stats in sorted(regimes.items()):
        mult = 0.6 + (stats.hit_rate - 0.45) * 4.0 if stats.hit_rate >= 0.45 else 0.5
        mult = max(0.50, min(1.30, mult))

        rows.append([
            regime_label,
            stats.n_trades,
            f"{stats.hit_rate:.1%}",
            f"{stats.avg_pnl:.2%}",
            f"{mult:.2f}x",
        ])

    if rows:
        headers = ["Regime", "N Trades", "Hit Rate", "Avg P&L", "Multiplier"]
        print(tabulate(rows, headers=headers, tablefmt="grid"))
    else:
        print("(No regime data available)")


def print_thompson_posteriors(kb: KnowledgeBase) -> None:
    """Print Thompson posteriors per debater per regime."""
    print("\n" + "=" * 80)
    print("THOMPSON SAMPLING POSTERIORS")
    print("=" * 80)

    if not kb.posteriors or not any(kb.posteriors.values()):
        print("(No Thompson posteriors available — run backtest with --bandit to seed)")
        return

    for debater_name in sorted(kb.posteriors.keys()):
        regimes_dict = kb.posteriors[debater_name]
        if not regimes_dict:
            continue

        print(f"\n{debater_name.upper()}:")
        rows = []
        for regime_id in sorted(regimes_dict.keys()):
            post = regimes_dict[regime_id]
            n_obs = post.alpha + post.beta - 2  # observations beyond Beta(1,1) prior

            rows.append([
                regime_id,
                f"{n_obs:.0f}",
                f"{post.alpha:.1f}",
                f"{post.beta:.1f}",
                f"{post.mean:.3f}",
                f"{post.std:.4f}",
                f"{1.0 / (1.0 + post.std):.3f}",
            ])

        headers = ["Regime", "N Obs", "Alpha", "Beta", "Mean", "Std", "Confidence"]
        print(tabulate(rows, headers=headers, tablefmt="grid"))


def print_debater_calibration(kb: KnowledgeBase) -> None:
    """Print per-debater conviction bucket calibration."""
    print("\n" + "=" * 80)
    print("DEBATER CONVICTION CALIBRATION (by conviction bucket)")
    print("=" * 80)

    for debater_name in sorted(kb.debaters.keys()):
        stats = kb.debaters[debater_name]
        if not stats.by_conviction or stats.n_votes == 0:
            continue

        print(f"\n{debater_name.upper()} — {stats.n_votes} total votes, {stats.hit_rate:.1%} overall hit rate")
        rows = []
        for bucket in sorted(stats.by_conviction.keys()):
            bucket_stats = stats.by_conviction[bucket]
            n = bucket_stats.get("n", 0)
            hits = bucket_stats.get("hits", 0)
            hit_rate = hits / n if n > 0 else 0.5

            rows.append([
                bucket,
                n,
                f"{hit_rate:.1%}",
                "📈 GOOD" if hit_rate >= 0.52 else "📉 POOR" if hit_rate < 0.48 else "➡️ OK",
            ])

        if rows:
            headers = ["Conviction Bucket", "N Votes", "Hit Rate", "Calibration"]
            print(tabulate(rows, headers=headers, tablefmt="grid"))


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG Learning Status Report")
    parser.add_argument("--ticker", type=str, help="Filter to specific ticker")
    parser.add_argument("--regime", type=int, help="Filter to specific regime ID (0-7)")
    parser.add_argument("--posteriors-only", action="store_true", help="Show only Thompson posteriors")
    parser.add_argument("--no-posteriors", action="store_true", help="Skip Thompson posteriors section")
    args = parser.parse_args()

    kb_path = default_kb_path()
    if not kb_path.exists():
        print(f"ERROR: Knowledge base not found at {kb_path}")
        print("(Run backtest or daily_learn.py to generate KB)")
        sys.exit(1)

    try:
        kb = KnowledgeBase.load(kb_path)
    except Exception as e:
        print(f"ERROR: Failed to load KB: {e}")
        sys.exit(1)

    if args.posteriors_only:
        print_thompson_posteriors(kb)
    else:
        print_kb_summary(kb)
        print_ticker_analysis(kb, args.ticker)
        print_regime_analysis(kb, args.regime)
        print_debater_calibration(kb)
        if not args.no_posteriors:
            print_thompson_posteriors(kb)

    print("\n" + "=" * 80)
    print("INTERPRETATION GUIDE")
    print("=" * 80)
    print("""
Conviction Multiplier:
  - 0.50–1.00: Underperforming, reduce position size
  - 1.00–1.30: Outperforming, increase position size
  - 0.0 (blacklist): Blocked entirely

Thompson Posteriors:
  - Alpha/Beta: Bayesian counts of wins/losses (+1 prior)
  - Mean: Expected win rate for this debater in this regime
  - Std: Uncertainty (lower = more confident)
  - Confidence: 1/(1+std) used to scale Kelly sizing

Debater Calibration:
  - GOOD: Hit rate >= 52% (above baseline)
  - OK: 48%–52% (around baseline)
  - POOR: < 48% (below baseline, debater is miscalibrated)
""")


if __name__ == "__main__":
    main()
