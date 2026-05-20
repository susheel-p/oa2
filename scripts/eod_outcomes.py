"""End-of-day outcome resolver — Phase 1 of the RAG learning loop.

Reads decision logs from `logs/paper_trade_<date>.jsonl`, resolves each
APPROVED trade against T+1 close prices via yfinance, and appends results
to `~/.tradingbot/outcomes/outcomes_history.jsonl`.

Usage:
    python scripts/eod_outcomes.py                    # today's outcomes
    python scripts/eod_outcomes.py --date 2026-05-18  # specific date
    python scripts/eod_outcomes.py --backfill         # all logs found
    python scripts/eod_outcomes.py --dry-run          # print, don't write
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

# Allow running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingbot.core.config import tradingbot_home
from tradingbot.learning.outcomes import TradeOutcome, resolve_outcomes_from_log


LOG_DIR = Path("logs")


def _outcomes_dir() -> Path:
    d = tradingbot_home() / "outcomes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _today_str() -> str:
    return datetime.date.today().isoformat()


def _find_log_files(date: str | None, backfill: bool) -> list[Path]:
    if backfill:
        return sorted(LOG_DIR.glob("paper_trade_*.jsonl"))
    target_date = date or _today_str()
    p = LOG_DIR / f"paper_trade_{target_date}.jsonl"
    return [p] if p.exists() else []


def _append_outcomes(outcomes: list[TradeOutcome], dry_run: bool) -> tuple[Path, Path]:
    """Append outcomes to both a per-date file and the rolling history file."""
    out_dir = _outcomes_dir()
    history_path = out_dir / "outcomes_history.jsonl"

    by_date: dict[str, list[TradeOutcome]] = {}
    for o in outcomes:
        by_date.setdefault(o.decision_date, []).append(o)

    daily_path = None
    if not dry_run:
        for date, items in by_date.items():
            daily_path = out_dir / f"outcomes_{date}.jsonl"
            with open(daily_path, "w") as f:
                for o in items:
                    f.write(json.dumps(o.to_dict()) + "\n")
        # Append to rolling history (deduplicate by trade_id if file exists)
        existing_ids = set()
        if history_path.exists():
            with open(history_path) as f:
                for line in f:
                    try:
                        existing_ids.add(json.loads(line).get("trade_id"))
                    except json.JSONDecodeError:
                        continue
        with open(history_path, "a") as f:
            for o in outcomes:
                if o.trade_id not in existing_ids:
                    f.write(json.dumps(o.to_dict()) + "\n")
                    existing_ids.add(o.trade_id)

    return daily_path, history_path


def _print_summary(outcomes: list[TradeOutcome]) -> None:
    if not outcomes:
        print("No APPROVED trades resolved.")
        return

    n = len(outcomes)
    hits = sum(1 for o in outcomes if o.direction_hit)
    total_pnl = sum(o.total_pnl_dollars for o in outcomes)
    avg_pnl_per_trade = total_pnl / n
    by_ticker = {}
    for o in outcomes:
        by_ticker.setdefault(o.ticker, []).append(o)

    print(f"\n{'=' * 60}")
    print(f"OUTCOMES SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total APPROVED trades resolved: {n}")
    print(f"Direction hits: {hits}/{n} ({hits/n:.1%})")
    print(f"Total P&L proxy: ${total_pnl:+,.2f}")
    print(f"Avg P&L per trade: ${avg_pnl_per_trade:+,.2f}")

    print(f"\nPer-ticker breakdown:")
    rows = []
    for ticker, items in sorted(by_ticker.items()):
        t_hits = sum(1 for o in items if o.direction_hit)
        t_pnl = sum(o.total_pnl_dollars for o in items)
        rows.append((ticker, t_hits, len(items), t_pnl))
    rows.sort(key=lambda r: -r[3])  # by P&L desc
    for ticker, h, t, pnl in rows:
        marker = "+" if pnl > 0 else ("-" if pnl < 0 else " ")
        print(f"  {marker} {ticker:6s} {h}/{t} ({h/t:.0%}) ${pnl:+,.2f}")

    # Best / worst
    best = max(outcomes, key=lambda o: o.total_pnl_dollars)
    worst = min(outcomes, key=lambda o: o.total_pnl_dollars)
    print(f"\nBest:  {best.ticker} ({best.direction}) ${best.total_pnl_dollars:+,.2f}")
    print(f"Worst: {worst.ticker} ({worst.direction}) ${worst.total_pnl_dollars:+,.2f}")
    print(f"{'=' * 60}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="oa2 EOD outcome resolver (Phase 1)")
    parser.add_argument("--date", type=str, default=None, help="Date to resolve (default: today)")
    parser.add_argument("--backfill", action="store_true", help="Process all log files")
    parser.add_argument("--dry-run", action="store_true", help="Print only, do not write")
    args = parser.parse_args()

    log_files = _find_log_files(args.date, args.backfill)
    if not log_files:
        target = args.date or _today_str()
        print(f"No log file found for {target}. Tried: logs/paper_trade_{target}.jsonl")
        return 1

    print(f"Resolving outcomes from {len(log_files)} log file(s) ...")
    all_outcomes: list[TradeOutcome] = []
    for path in log_files:
        print(f"  Processing {path.name} ...")
        outcomes = resolve_outcomes_from_log(path)
        print(f"    -> resolved {len(outcomes)} APPROVED trade(s)")
        all_outcomes.extend(outcomes)

    _print_summary(all_outcomes)

    if not args.dry_run and all_outcomes:
        daily, history = _append_outcomes(all_outcomes, dry_run=False)
        print(f"Wrote daily file: {daily}")
        print(f"Appended to history: {history}")
    elif args.dry_run:
        print("(dry-run: nothing written)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
