"""Weekly performance analyzer — buckets backtest by ISO week.

Reads the latest backtest JSON and produces:
  - Per-week accuracy + Sharpe + cumulative P&L proxy
  - Per-ticker rolling accuracy over the window
  - Regime drift week-over-week
  - Anomaly detection (best / worst weeks)
  - RAG learning insights (which week, ticker, regime, conviction combos worked)

Usage:
    python scripts/analyze_weekly.py                          # latest backtest
    python scripts/analyze_weekly.py --results <path>         # specific
    python scripts/analyze_weekly.py --output reports/<name>.md
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingbot.core.config import tradingbot_home


def _latest_backtest() -> Path:
    bdir = tradingbot_home() / "backtest"
    files = sorted(bdir.glob("results_*.json"))
    if not files:
        raise FileNotFoundError("No backtest results found")
    return files[-1]


def _iso_week(date_str: str) -> str:
    d = datetime.date.fromisoformat(date_str)
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 5:
        return 0.0
    mu = statistics.mean(returns)
    sd = statistics.pstdev(returns)
    return (mu / sd * math.sqrt(252)) if sd > 0 else 0.0


def _per_week_stats(days: list[dict]) -> dict[str, dict]:
    """Bucket days by ISO week, compute accuracy + Sharpe + cumulative return."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for d in days:
        buckets[_iso_week(d["date"])].append(d)

    out: dict[str, dict] = {}
    for week, items in sorted(buckets.items()):
        nn = [d for d in items if d["consensus_direction"] != "NEUTRAL"]
        hits = sum(1 for d in nn if d["outcome"] == d["consensus_direction"])
        n = len(nn)
        # Directional returns: +next_day_return when bullish, -next_day_return when bearish, 0 when neutral
        rets = []
        for d in items:
            r = d.get("next_day_return", 0.0) or 0.0
            if d["consensus_direction"] == "BULLISH":
                rets.append(r)
            elif d["consensus_direction"] == "BEARISH":
                rets.append(-r)
            else:
                rets.append(0.0)
        cumulative = sum(rets)
        bull = sum(1 for d in nn if d["consensus_direction"] == "BULLISH")
        bear = sum(1 for d in nn if d["consensus_direction"] == "BEARISH")
        out[week] = {
            "n_days": len(items),
            "n_active": n,
            "n_bullish": bull,
            "n_bearish": bear,
            "hits": hits,
            "accuracy": hits / n if n else 0.0,
            "cumulative_return": round(cumulative, 4),
            "avg_daily_return": round(cumulative / max(len(items), 1), 5),
            "sharpe": round(_sharpe(rets), 3),
        }
    return out


def _per_ticker_stats(days: list[dict]) -> list[dict]:
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for d in days:
        by_ticker[d["ticker"]].append(d)
    rows = []
    for ticker, items in by_ticker.items():
        nn = [d for d in items if d["consensus_direction"] != "NEUTRAL"]
        hits = sum(1 for d in nn if d["outcome"] == d["consensus_direction"])
        rets = []
        for d in items:
            r = d.get("next_day_return", 0.0) or 0.0
            if d["consensus_direction"] == "BULLISH":
                rets.append(r)
            elif d["consensus_direction"] == "BEARISH":
                rets.append(-r)
            else:
                rets.append(0.0)
        rows.append({
            "ticker": ticker,
            "n_days": len(items),
            "n_active": len(nn),
            "accuracy": hits / len(nn) if nn else 0.0,
            "cumulative_return": round(sum(rets), 4),
            "sharpe": round(_sharpe(rets), 3),
        })
    rows.sort(key=lambda r: -r["sharpe"])
    return rows


def _direction_bias(days: list[dict]) -> dict:
    """Compute bullish/bearish hit rates and bias."""
    nn = [d for d in days if d["consensus_direction"] != "NEUTRAL"]
    bull = [d for d in nn if d["consensus_direction"] == "BULLISH"]
    bear = [d for d in nn if d["consensus_direction"] == "BEARISH"]
    bull_hits = sum(1 for d in bull if d["outcome"] == "BULLISH")
    bear_hits = sum(1 for d in bear if d["outcome"] == "BEARISH")
    return {
        "bullish_n": len(bull),
        "bullish_hits": bull_hits,
        "bullish_accuracy": bull_hits / len(bull) if bull else 0.0,
        "bearish_n": len(bear),
        "bearish_hits": bear_hits,
        "bearish_accuracy": bear_hits / len(bear) if bear else 0.0,
    }


def _regime_stats(days: list[dict]) -> list[dict]:
    by_regime: dict[str, list[dict]] = defaultdict(list)
    for d in days:
        if d["consensus_direction"] == "NEUTRAL":
            continue
        by_regime[d.get("regime_label", "?")].append(d)
    rows = []
    for reg, items in by_regime.items():
        hits = sum(1 for d in items if d["outcome"] == d["consensus_direction"])
        rows.append({
            "regime": reg,
            "n": len(items),
            "accuracy": hits / len(items),
        })
    rows.sort(key=lambda r: -r["accuracy"])
    return rows


def _conviction_calibration(days: list[dict]) -> list[dict]:
    """Bucket non-neutral days by p_bull, compute hit rate."""
    buckets = [
        (0.10, 0.20), (0.20, 0.30), (0.30, 0.40), (0.40, 0.50),
        (0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 1.01),
    ]
    rows = []
    nn = [d for d in days if d["consensus_direction"] != "NEUTRAL"]
    for lo, hi in buckets:
        items = [d for d in nn if lo <= d["p_bull"] < hi]
        if not items:
            continue
        hits = sum(1 for d in items if d["outcome"] == d["consensus_direction"])
        rows.append({
            "bucket": f"{lo:.2f}-{hi:.2f}",
            "n": len(items),
            "accuracy": hits / len(items),
        })
    return rows


def _markdown_report(weekly: dict, by_ticker: list, by_regime: list,
                     bias: dict, calib: list, meta: dict) -> str:
    L = []
    L.append("# 1-Year Weekly Backtest Analysis")
    L.append("")
    L.append(f"**Period:** {meta['months']} months | **Tickers:** {len(meta['tickers'])}")
    L.append(f"**Total days:** {meta['total_days']:,} | **Non-neutral:** {meta['n_active']:,}")
    L.append(f"**v2 Sharpe:** {meta['v2_sharpe']:+.3f} | **v1 Sharpe:** {meta['v1_sharpe']:+.3f}")
    L.append(f"**Improvement:** {meta['improvement']:+.3f}")
    L.append("")
    L.append("---")
    L.append("")

    # === Direction bias headline ===
    L.append("## Direction Bias (the big finding)")
    L.append("")
    L.append("| Direction | N | Accuracy | Verdict |")
    L.append("|-----------|---|----------|---------|")
    bv = "Marginal edge" if bias["bullish_accuracy"] > 0.51 else ("Random" if 0.49 <= bias["bullish_accuracy"] <= 0.51 else "**LOSING**")
    bv2 = "Marginal edge" if bias["bearish_accuracy"] > 0.51 else ("Random" if 0.49 <= bias["bearish_accuracy"] <= 0.51 else "**LOSING**")
    L.append(f"| BULLISH | {bias['bullish_n']:,} | {bias['bullish_accuracy']:.1%} | {bv} |")
    L.append(f"| BEARISH | {bias['bearish_n']:,} | {bias['bearish_accuracy']:.1%} | {bv2} |")
    L.append("")

    # === Weekly trends ===
    L.append("## Week-by-Week Performance")
    L.append("")
    L.append("| Week | Active | Bull/Bear | Hit Rate | Cum Return | Sharpe |")
    L.append("|------|--------|-----------|----------|------------|--------|")
    for week, s in weekly.items():
        L.append(
            f"| {week} | {s['n_active']:3d} | {s['n_bullish']:>3d}/{s['n_bearish']:<3d} | "
            f"{s['accuracy']:.1%} | {s['cumulative_return']:+.2%} | {s['sharpe']:+.2f} |"
        )
    L.append("")

    # Best/worst weeks
    weeks_by_sharpe = sorted(weekly.items(), key=lambda kv: -kv[1]["sharpe"])
    L.append("### Top 5 weeks (by Sharpe)")
    L.append("")
    for week, s in weeks_by_sharpe[:5]:
        L.append(f"- **{week}**: Sharpe {s['sharpe']:+.2f}, acc {s['accuracy']:.1%}, return {s['cumulative_return']:+.2%}")
    L.append("")
    L.append("### Bottom 5 weeks (by Sharpe)")
    L.append("")
    for week, s in weeks_by_sharpe[-5:]:
        L.append(f"- **{week}**: Sharpe {s['sharpe']:+.2f}, acc {s['accuracy']:.1%}, return {s['cumulative_return']:+.2%}")
    L.append("")

    # === Per-ticker ===
    L.append("## Per-Ticker Performance (sorted by Sharpe)")
    L.append("")
    L.append("| Ticker | Active | Accuracy | Cum Return | Sharpe | RAG Action |")
    L.append("|--------|--------|----------|------------|--------|------------|")
    for r in by_ticker:
        # RAG action interpretation
        if r["accuracy"] < 0.43:
            action = "**BLACKLIST**"
        elif r["accuracy"] < 0.47:
            action = "Watch (mult <1.0)"
        elif r["accuracy"] >= 0.55:
            action = "Boost (mult >1.0)"
        else:
            action = "Neutral"
        L.append(
            f"| {r['ticker']:6s} | {r['n_active']:3d} | {r['accuracy']:.1%} | "
            f"{r['cumulative_return']:+.2%} | {r['sharpe']:+.2f} | {action} |"
        )
    L.append("")

    # === Per-regime ===
    L.append("## Per-Regime Accuracy")
    L.append("")
    L.append("| Regime | N | Accuracy | RAG Multiplier |")
    L.append("|--------|---|----------|----------------|")
    for r in by_regime:
        mult = 0.6 + (r["accuracy"] - 0.45) * 4.0
        mult = max(0.5, min(1.3, mult))
        if r["accuracy"] < 0.45:
            mult_str = f"{mult:.2f}x **(reduce)**"
        elif r["accuracy"] > 0.55:
            mult_str = f"{mult:.2f}x (boost)"
        else:
            mult_str = f"{mult:.2f}x"
        L.append(f"| {r['regime']} | {r['n']:,} | {r['accuracy']:.1%} | {mult_str} |")
    L.append("")

    # === Conviction calibration ===
    L.append("## Conviction Calibration (does higher p_bull predict better?)")
    L.append("")
    L.append("| p_bull Bucket | N | Accuracy | Verdict |")
    L.append("|---------------|---|----------|---------|")
    for r in calib:
        v = "Good" if r["accuracy"] >= 0.55 else ("OK" if r["accuracy"] >= 0.50 else "Poor")
        L.append(f"| {r['bucket']} | {r['n']:,} | {r['accuracy']:.1%} | {v} |")
    L.append("")

    # === Learnings summary ===
    L.append("## Key Learnings (auto-extracted)")
    L.append("")
    # 1. Direction bias
    if bias["bullish_accuracy"] - bias["bearish_accuracy"] > 0.05:
        L.append(f"1. **BULLISH signals are {(bias['bullish_accuracy'] - bias['bearish_accuracy']) * 100:.1f}pp more accurate than BEARISH.** "
                 f"Consider tightening BEARISH min_edge or biasing toward BULLISH-only strategy.")
    # 2. Best regime
    best_reg = by_regime[0]
    worst_reg = by_regime[-1]
    L.append(f"2. **Best regime:** {best_reg['regime']} ({best_reg['accuracy']:.1%} on {best_reg['n']} trades). "
             f"**Worst:** {worst_reg['regime']} ({worst_reg['accuracy']:.1%} on {worst_reg['n']} trades).")
    # 3. Top tickers
    top3 = [r['ticker'] for r in by_ticker[:3]]
    bot3 = [r['ticker'] for r in by_ticker[-3:]]
    L.append(f"3. **Top tickers (by Sharpe):** {', '.join(top3)}. **Worst:** {', '.join(bot3)}.")
    # 4. Calibration
    high_conv = [r for r in calib if r["bucket"].startswith("0.7") or r["bucket"].startswith("0.8")]
    if high_conv and statistics.mean(r["accuracy"] for r in high_conv) < 0.55:
        L.append("4. **High-conviction predictions (p_bull > 0.7) are NOT more accurate than chance.** "
                 "Calibrator slope is too aggressive; consider lowering sigmoid amplifier further or re-running calibration.")
    # 5. Best/worst weeks
    if weeks_by_sharpe:
        best_w, best_s = weeks_by_sharpe[0]
        worst_w, worst_s = weeks_by_sharpe[-1]
        L.append(f"5. **Best week:** {best_w} ({best_s['sharpe']:+.2f} Sharpe). "
                 f"**Worst:** {worst_w} ({worst_s['sharpe']:+.2f} Sharpe). "
                 f"Wide spread suggests timing-dependent edge.")
    L.append("")
    L.append("---")
    L.append("")
    L.append("*Auto-generated by `scripts/analyze_weekly.py`*")
    return "\n".join(L)


def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly backtest analyzer")
    parser.add_argument("--results", type=str, default=None)
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    path = Path(args.results) if args.results else _latest_backtest()
    print(f"Loading: {path.name}")
    data = json.loads(path.read_text())
    days = data.get("days", [])

    print(f"Total days: {len(days)}")
    n_active = sum(1 for d in days if d["consensus_direction"] != "NEUTRAL")
    print(f"Non-neutral: {n_active}")

    weekly = _per_week_stats(days)
    by_ticker = _per_ticker_stats(days)
    by_regime = _regime_stats(days)
    bias = _direction_bias(days)
    calib = _conviction_calibration(days)

    meta = {
        "months": data.get("metrics", {}).get("months_back", 12),
        "tickers": data.get("metrics", {}).get("tickers", []),
        "total_days": len(days),
        "n_active": n_active,
        "v2_sharpe": data.get("metrics", {}).get("v2_sharpe", 0.0),
        "v1_sharpe": data.get("metrics", {}).get("baseline_sharpe", 0.0),
        "improvement": data.get("metrics", {}).get("sharpe_improvement", 0.0),
    }

    report = _markdown_report(weekly, by_ticker, by_regime, bias, calib, meta)

    if args.output:
        out_path = Path(args.output)
    else:
        reports_dir = Path(os.getenv("REPORTS_DIR", "reports"))
        out_path = reports_dir / datetime.date.today().isoformat() / "weekly_analysis.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    print(f"\nReport written: {out_path}")
    print(f"  ({len(weekly)} weeks, {len(by_ticker)} tickers, {len(by_regime)} regimes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
