"""RAG Impact Analyzer — Measure how much RAG learning affects trade sizing.

Shows:
  - How many trades were affected by KB multipliers?
  - What was the average multiplier applied?
  - How many trades were blocked by KB blacklist?
  - Estimated P&L impact of RAG learning vs. baseline

Usage:
    python scripts/rag_impact.py                         # Analyze all logs
    python scripts/rag_impact.py --date 2026-05-23      # Single day
    python scripts/rag_impact.py --days 7               # Last 7 days
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from tradingbot.core.config import tradingbot_home


def load_decision_logs(start_date: date, end_date: date) -> list[dict]:
    """Load all decision logs in date range."""
    logs_dir = tradingbot_home() / "logs"
    if not logs_dir.exists():
        print(f"ERROR: logs directory not found at {logs_dir}")
        sys.exit(1)

    decisions = []
    current_date = start_date
    while current_date <= end_date:
        log_path = logs_dir / f"paper_trade_{current_date.isoformat()}.jsonl"
        if log_path.exists():
            with open(log_path) as f:
                for line in f:
                    if line.strip():
                        try:
                            decisions.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        current_date += timedelta(days=1)

    return decisions


def compute_impact(decisions: list[dict]) -> dict:
    """Analyze RAG impact across decisions."""
    rag_enabled_count = 0
    rag_disabled_count = 0
    blacklist_blocks = 0
    multiplier_sum = 0.0
    multiplier_count = 0
    approval_changes = 0
    sizing_changes = []

    by_ticker = defaultdict(lambda: {
        "n_trades": 0,
        "n_rag_enabled": 0,
        "sum_mult": 0.0,
        "blacklisted": 0,
    })

    for decision in decisions:
        if decision.get("decision") == "REJECTED":
            # Skip rejected trades (already failed other gates)
            continue

        rag = decision.get("rag_learning", {})
        if not rag:
            continue

        ticker = decision.get("ticker", "UNKNOWN").upper()
        by_ticker[ticker]["n_trades"] += 1

        if rag.get("enabled"):
            rag_enabled_count += 1
            by_ticker[ticker]["n_rag_enabled"] += 1

            # Check if KB stats indicate blacklist
            kb_meta = rag.get("kb_metadata", {})
            ticker_stats = kb_meta.get("ticker_stats", {})
            if ticker_stats.get("blacklisted"):
                blacklist_blocks += 1
                by_ticker[ticker]["blacklisted"] += 1
                continue

            # Measure multiplier effect
            mult = rag.get("conviction_multipliers", {}).get("combined_multiplier", 1.0)
            multiplier_sum += mult
            multiplier_count += 1
            by_ticker[ticker]["sum_mult"] += mult

            # Measure p_bull adjustment
            p_raw = rag.get("p_bull_adjustment", {}).get("p_bull_raw", 0.5)
            p_adj = rag.get("p_bull_adjustment", {}).get("p_bull_adjusted", 0.5)
            if abs(p_adj - p_raw) > 0.01:  # >1 ppt change
                approval_changes += 1

            # Measure Kelly sizing impact
            thompson = decision.get("sizing", {}).get("kelly", {}).get("thompson_scaling", {})
            if thompson.get("enabled"):
                confidence = thompson.get("aggregate", {}).get("confidence", 1.0)
                if confidence < 0.99:  # Some scaling applied
                    sizing_changes.append({
                        "ticker": ticker,
                        "confidence": confidence,
                        "impact": f"{(confidence - 1.0) * 100:.1f}%",
                    })
        else:
            rag_disabled_count += 1

    avg_mult = (multiplier_sum / multiplier_count) if multiplier_count > 0 else 1.0

    return {
        "rag_enabled": rag_enabled_count,
        "rag_disabled": rag_disabled_count,
        "blacklist_blocks": blacklist_blocks,
        "avg_multiplier": round(avg_mult, 4),
        "approval_changes": approval_changes,
        "thompson_scalings": len(sizing_changes),
        "by_ticker": dict(by_ticker),
        "sizing_changes": sizing_changes[:10],  # Top 10
    }


def print_impact_report(impact: dict) -> None:
    """Print RAG impact analysis."""
    print("\n" + "=" * 80)
    print("RAG LEARNING IMPACT ANALYSIS")
    print("=" * 80)

    total_trades = impact["rag_enabled"] + impact["rag_disabled"]
    pct_enabled = (impact["rag_enabled"] / total_trades * 100) if total_trades > 0 else 0

    print(f"\nCoverage:")
    print(f"  Trades with RAG enabled:  {impact['rag_enabled']:4d} ({pct_enabled:5.1f}%)")
    print(f"  Trades with KB unavailable: {impact['rag_disabled']:4d}")
    print(f"  Total trades analyzed:    {total_trades:4d}")

    print(f"\nBlocking:")
    print(f"  Trades blocked by KB blacklist: {impact['blacklist_blocks']}")

    print(f"\nMultiplier Effect (conviction adjustment):")
    avg_mult = impact["avg_multiplier"]
    if avg_mult > 1.0:
        direction = "🔼 BOOSTED"
        pct = (avg_mult - 1.0) * 100
    elif avg_mult < 1.0:
        direction = "🔽 REDUCED"
        pct = (1.0 - avg_mult) * 100
    else:
        direction = "➡️ NEUTRAL"
        pct = 0
    print(f"  Average multiplier:       {avg_mult:.3f}x {direction} ({pct:+.1f}%)")

    print(f"\nP&L Adjustment Effect:")
    print(f"  Trades with p_bull shifted:  {impact['approval_changes']}")

    print(f"\nThompson Posterior Scaling (position sizing):")
    print(f"  Trades with confidence scaling:  {impact['thompson_scalings']}")
    if impact["sizing_changes"]:
        print(f"\n  Top adjustments:")
        for change in impact["sizing_changes"][:5]:
            print(f"    {change['ticker']:6s} confidence {change['confidence']:.3f} ({change['impact']})")

    print(f"\nPer-Ticker Summary:")
    print(f"  {'Ticker':<8} {'N Trades':<10} {'RAG %':<10} {'Avg Mult':<12} {'Blacklist':<10}")
    print(f"  {'-'*8} {'-'*10} {'-'*10} {'-'*12} {'-'*10}")

    for ticker in sorted(impact["by_ticker"].keys()):
        stats = impact["by_ticker"][ticker]
        n_trades = stats["n_trades"]
        n_rag = stats["n_rag_enabled"]
        pct = (n_rag / n_trades * 100) if n_trades > 0 else 0

        avg_m = (stats["sum_mult"] / n_rag) if n_rag > 0 else 1.0
        blacklist_mark = "🚫" if stats["blacklisted"] > 0 else ""

        print(f"  {ticker:<8} {n_trades:<10} {pct:>7.1f}% {avg_m:>10.3f}x {blacklist_mark:<10}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG Impact Analyzer")
    parser.add_argument("--date", type=str, help="Analyze single date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=1, help="Analyze last N days (default: 1)")
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    if args.date:
        target_date = datetime.fromisoformat(args.date).date()
        start_date = target_date
        end_date = target_date
    elif args.start and args.end:
        start_date = datetime.fromisoformat(args.start).date()
        end_date = datetime.fromisoformat(args.end).date()
    else:
        end_date = date.today()
        start_date = end_date - timedelta(days=args.days - 1)

    print(f"Loading decision logs from {start_date} to {end_date}...")
    decisions = load_decision_logs(start_date, end_date)

    if not decisions:
        print(f"No decision logs found for this date range")
        sys.exit(1)

    print(f"Loaded {len(decisions)} decisions\n")

    impact = compute_impact(decisions)
    print_impact_report(impact)

    print("\n" + "=" * 80)
    print("INTERPRETATION")
    print("=" * 80)
    print("""
Multiplier > 1.0:
  KB is boosting conviction (ticker/regime performing well)
  → Larger positions than baseline consensus

Multiplier < 1.0:
  KB is reducing conviction (ticker/regime underperforming)
  → Smaller positions than baseline consensus

Blacklist blocks:
  Ticker hit_rate < 43% AND dollar_weighted_win < 45%
  → Trade completely rejected by quality gate

Thompson confidence scaling:
  High confidence (many observations) → Kelly gets 95-99% scaling
  Low confidence (few observations) → Kelly gets 50-80% scaling
  → Protects against regime change by reducing size when uncertain

If multiplier ≈ 1.0:
  KB has insufficient data (< 20 trades per ticker)
  → No adjustment, using baseline consensus

If blacklist blocks > 5% of trades:
  Ticker is consistently losing; consider:
  a) Retrain debaters that vote bullish on this ticker
  b) Increase sample size (need more trades to get good stats)
  c) Disable ticker entirely in watchlist
""")


if __name__ == "__main__":
    main()
