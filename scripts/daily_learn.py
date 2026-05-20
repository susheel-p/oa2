"""Daily learner — Phase 2 of the RAG learning loop.

Run nightly after eod_outcomes.py. Reads outcomes_history.jsonl + the latest
backtest result, builds a fresh KnowledgeBase, writes it to
~/.tradingbot/knowledge_base.json (atomic), and emits a human-readable insights
report to reports/<date>/insights.md.

Sources merged (in priority order):
  1. ~/.tradingbot/outcomes/outcomes_history.jsonl  (live + paper-trade outcomes)
  2. ~/.tradingbot/backtest/results_*.json (latest)  (bootstrap when live data is thin)

Usage:
    python scripts/daily_learn.py              # update KB + write today's report
    python scripts/daily_learn.py --dry-run    # print, do not write
    python scripts/daily_learn.py --window 30  # narrow rolling window
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingbot.core.config import tradingbot_home
from tradingbot.learning.knowledge_base import (
    DEFAULT_WINDOW_DAYS,
    KnowledgeBase,
    build_from_backtest,
    build_from_outcomes,
    default_kb_path,
)
from oa2.learning.versioning import (
    snapshot_kb, snapshot_bandit, diff_kb, diff_bandit,
    learning_events_path, kb_path, bandit_path,
)


REPORTS_DIR = Path(os.getenv("REPORTS_DIR", "reports"))


# ----- Data loading ---------------------------------------------------------

def _load_outcomes_history() -> list[dict]:
    p = tradingbot_home() / "outcomes" / "outcomes_history.jsonl"
    if not p.exists():
        return []
    records = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _latest_backtest_path() -> Path | None:
    bdir = tradingbot_home() / "backtest"
    results = sorted(bdir.glob("results_*.json"))
    return results[-1] if results else None


# ----- Static blacklist auto-sync -------------------------------------------

def _derive_static_blacklist(kb: KnowledgeBase) -> tuple[list[str], dict[str, float]]:
    """From a KB, derive the static blacklist + quality-score dict.

    The blacklist UNIONS:
      - KB-driven: tickers meeting KB.is_blacklisted() criteria (n>=30, hit<43%, $win<45%)
      - Code fallback: tickers in oa2.strategy.quality_gates._FALLBACK_BLACKLIST
        (safety net so a KB rebuild can never silently drop hard-earned bans)

    Quality scores combine KB multipliers (when n>=20) with the code fallback
    for tickers the KB hasn't seen.

    Returns:
        (sorted blacklist tickers, full ticker->multiplier dict)
    """
    from tradingbot.learning.knowledge_base import (
        _ticker_blacklisted, _ticker_multiplier, MIN_OBS_FOR_MULT,
    )
    from tradingbot.strategy.quality_gates import _FALLBACK_BLACKLIST, _FALLBACK_QUALITY_SCORE

    blacklist: set[str] = set()
    scores: dict[str, float] = {}

    # Start with code-fallback values (safety net)
    blacklist.update(_FALLBACK_BLACKLIST)
    scores.update(_FALLBACK_QUALITY_SCORE)

    # Overlay KB findings (KB wins on tickers with enough trades)
    for ticker, stats in kb.tickers.items():
        t = ticker.upper()
        if _ticker_blacklisted(stats):
            blacklist.add(t)
            scores[t] = 0.0
        elif stats.n_trades >= MIN_OBS_FOR_MULT:
            scores[t] = round(_ticker_multiplier(stats), 4)
            # If KB now disagrees with fallback (says ticker is OK), we still
            # keep it in blacklist union -- code fallback is a "ratchet". To
            # remove a ticker from the static fallback, edit quality_gates.py.

    # Blacklisted tickers always score 0 regardless of KB multiplier
    for t in blacklist:
        scores[t] = 0.0

    return sorted(blacklist), scores


def _write_static_blacklist(kb: KnowledgeBase, dry_run: bool) -> Path:
    """Write ~/.tradingbot/static_blacklist.json from the current KB."""
    from tradingbot.strategy.quality_gates import _static_blacklist_path

    tickers, scores = _derive_static_blacklist(kb)
    payload = {
        "schema_version": 1,
        "last_synced": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "daily_learn.py auto-sync from KB",
        "n_tickers_evaluated": len(kb.tickers),
        "tickers": tickers,
        "quality_scores": scores,
    }
    path = _static_blacklist_path()
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write (.tmp + rename)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
    return path


# ----- Merge logic ----------------------------------------------------------

def _merge_kbs(primary: KnowledgeBase, secondary: KnowledgeBase) -> KnowledgeBase:
    """Merge stats from secondary INTO primary. Primary wins on conflicts."""
    for ticker, s in secondary.tickers.items():
        if ticker not in primary.tickers:
            primary.tickers[ticker] = s
        else:
            t = primary.tickers[ticker]
            t.n_trades += s.n_trades
            t.hits += s.hits
            t.total_pnl += s.total_pnl
            t.sum_pnl_pct += s.sum_pnl_pct
            t.n_dollar_winners += s.n_dollar_winners
            t.rolling_30d_n += s.rolling_30d_n
            t.rolling_30d_hits += s.rolling_30d_hits
    for regime, r in secondary.regimes.items():
        if regime not in primary.regimes:
            primary.regimes[regime] = r
        else:
            p = primary.regimes[regime]
            p.n_trades += r.n_trades
            p.hits += r.hits
            p.total_pnl += r.total_pnl
    primary.n_outcomes_used += secondary.n_outcomes_used
    return primary


# ----- Report writer --------------------------------------------------------

def _report_markdown(kb: KnowledgeBase) -> str:
    today = datetime.date.today().isoformat()
    lines: list[str] = []
    lines.append(f"# Daily Trading Insights — {today}")
    lines.append("")
    lines.append(f"**KB last updated:** {kb.last_updated}")
    lines.append(f"**Rolling window:** {kb.window_days} days")
    lines.append(f"**Outcomes used:** {kb.n_outcomes_used}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Ticker leaderboard
    lines.append("## Ticker Performance (sorted by hit rate)")
    lines.append("")
    lines.append("| Ticker | Hit Rate | Avg PnL % | $-Win Rate | N | Multiplier | Blacklist |")
    lines.append("|--------|----------|-----------|------------|---|------------|-----------|")
    rows = []
    for t, s in kb.tickers.items():
        from tradingbot.learning.knowledge_base import _ticker_multiplier, _ticker_blacklisted
        rows.append((
            t, s.hit_rate, s.avg_pnl_pct, s.dollar_weighted_win_rate,
            s.n_trades, _ticker_multiplier(s), _ticker_blacklisted(s),
        ))
    rows.sort(key=lambda r: -r[1])
    for t, hr, ap, dw, n, mult, bl in rows:
        bl_marker = "**YES**" if bl else "-"
        lines.append(f"| {t} | {hr:.1%} | {ap:+.2%} | {dw:.1%} | {n} | {mult:.2f}x | {bl_marker} |")
    lines.append("")

    # Regime breakdown
    lines.append("## Regime Performance")
    lines.append("")
    lines.append("| Regime | Hit Rate | Avg PnL | N | Multiplier |")
    lines.append("|--------|----------|---------|---|------------|")
    from tradingbot.learning.knowledge_base import _regime_multiplier
    reg_rows = sorted(kb.regimes.items(), key=lambda r: -r[1].hit_rate)
    for regime, s in reg_rows:
        lines.append(f"| {regime} | {s.hit_rate:.1%} | ${s.avg_pnl:+,.2f} | {s.n_trades} | {_regime_multiplier(s):.2f}x |")
    lines.append("")

    # Recommendations
    lines.append("## Recommendations (auto-derived)")
    lines.append("")
    from tradingbot.learning.knowledge_base import (
        _ticker_blacklisted, BLACKLIST_HIT_RATE, MIN_OBS_FOR_BLACKLIST,
    )
    blocked = [(t, s) for t, s in kb.tickers.items() if _ticker_blacklisted(s)]
    boosted = [(t, s) for t, s in kb.tickers.items()
               if s.n_trades >= 20 and s.hit_rate >= 0.55]
    underwatch = [(t, s) for t, s in kb.tickers.items()
                  if 20 <= s.n_trades < MIN_OBS_FOR_BLACKLIST
                  and s.hit_rate < BLACKLIST_HIT_RATE]

    if blocked:
        lines.append("**Blacklisted (auto-blocked at sizing gate):**")
        for t, s in blocked:
            lines.append(f"- {t}: {s.hit_rate:.1%} hit-rate over {s.n_trades} trades")
        lines.append("")
    if boosted:
        lines.append("**Boosted (multiplier > 1.05):**")
        for t, s in sorted(boosted, key=lambda r: -r[1].hit_rate):
            from tradingbot.learning.knowledge_base import _ticker_multiplier
            lines.append(f"- {t}: {s.hit_rate:.1%} hit-rate, mult={_ticker_multiplier(s):.2f}x")
        lines.append("")
    if underwatch:
        lines.append("**Watch list (needs more trades before blacklist):**")
        for t, s in underwatch:
            lines.append(f"- {t}: {s.hit_rate:.1%} over {s.n_trades} trades (need {MIN_OBS_FOR_BLACKLIST}+)")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*Auto-generated by `scripts/daily_learn.py` from outcomes history + latest backtest.*")
    return "\n".join(lines)


def _write_report(content: str, dry_run: bool) -> Path:
    today = datetime.date.today().isoformat()
    day_dir = REPORTS_DIR / today
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / "insights.md"
    if not dry_run:
        path.write_text(content, encoding="utf-8")
    return path


# ----- Performance snapshot helper -----------------------------------------------

def _compute_perf_snapshot(outcomes: list[dict], days: int = 30) -> dict:
    """Compute win rate, avg P&L, and count for last N days."""
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).timestamp()
    recent = []
    for outcome in outcomes:
        try:
            ts = datetime.datetime.fromisoformat(outcome.get("resolution_date", "1970-01-01")).timestamp()
            if ts >= cutoff:
                recent.append(outcome)
        except Exception:
            pass

    if not recent:
        return {
            "last_30d_win_rate": 0.5,
            "last_30d_avg_pnl": 0.0,
            "last_30d_n_trades": 0,
        }

    hits = sum(1 for o in recent if o.get("direction_hit", False))
    total_pnl = sum(o.get("total_pnl_dollars", 0.0) for o in recent)
    win_rate = hits / len(recent) if recent else 0.5

    return {
        "last_30d_win_rate": round(win_rate, 4),
        "last_30d_avg_pnl": round(total_pnl / len(recent) if recent else 0.0, 2),
        "last_30d_n_trades": len(recent),
    }


# ----- Main -----------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="oa2 daily learner (Phase 2)")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW_DAYS,
                        help=f"Rolling window in days (default {DEFAULT_WINDOW_DAYS})")
    parser.add_argument("--dry-run", action="store_true", help="Print only, do not write")
    parser.add_argument("--no-backtest", action="store_true",
                        help="Skip bootstrap from backtest results")
    args = parser.parse_args()

    print(f"=== oa2 daily learner ({datetime.date.today()}) ===\n")

    # Build KB from live outcomes
    outcomes = _load_outcomes_history()
    print(f"Loaded {len(outcomes)} outcome records from history.")
    kb_live = build_from_outcomes(outcomes, window_days=args.window)
    print(f"  -> KB from live: {len(kb_live.tickers)} tickers, "
          f"{kb_live.n_outcomes_used} outcomes used (window={args.window}d)")

    # Optionally bootstrap from latest backtest
    if not args.no_backtest:
        bpath = _latest_backtest_path()
        if bpath:
            print(f"Bootstrapping from backtest: {bpath.name}")
            kb_bt = build_from_backtest(bpath, window_days=args.window)
            print(f"  -> KB from backtest: {len(kb_bt.tickers)} tickers, "
                  f"{kb_bt.n_outcomes_used} non-neutral days")
            kb = _merge_kbs(kb_live, kb_bt)
        else:
            kb = kb_live
    else:
        kb = kb_live

    print(f"\nFinal KB: {len(kb.tickers)} tickers, {len(kb.regimes)} regimes, "
          f"{kb.n_outcomes_used} total observations.")

    # === Versioning: snapshot before write ===
    pre_kb_version_id = ""
    pre_bandit_version_id = ""
    if not args.dry_run:
        try:
            pre_kb_version_id = snapshot_kb("before_nightly_learn")
            pre_bandit_version_id = snapshot_bandit("before_nightly_learn")
            print(f"Snapshots created: KB {pre_kb_version_id}, Bandit {pre_bandit_version_id}")
        except Exception as e:
            print(f"Warning: snapshot failed: {e}")

    # Save KB
    kb_path = default_kb_path()
    if not args.dry_run:
        kb.save(kb_path)
        print(f"KB written: {kb_path}")
    else:
        print(f"(dry-run: would write to {kb_path})")

    # Auto-sync the static blacklist + quality scores so pipeline picks up
    # KB-derived values on next process start (no code change needed).
    bl_path = _write_static_blacklist(kb, dry_run=args.dry_run)
    bl_tickers, _ = _derive_static_blacklist(kb)
    if not args.dry_run:
        print(f"Static blacklist synced: {bl_path} ({len(bl_tickers)} tickers blacklisted)")
        if bl_tickers:
            print(f"  Blacklisted: {', '.join(bl_tickers)}")
        # Reload in-process (helps when daily_learn is imported, harmless otherwise)
        try:
            from tradingbot.strategy.quality_gates import reload_blacklist_from_disk
            reload_blacklist_from_disk()
        except Exception:
            pass
    else:
        print(f"(dry-run: would write blacklist to {bl_path})")
        if bl_tickers:
            print(f"  Would blacklist: {', '.join(bl_tickers)}")

    # === Versioning: snapshot after write and compute diffs ===
    post_kb_version_id = ""
    post_bandit_version_id = ""
    kb_diff_dict = {}
    bandit_diff_dict = {}
    if not args.dry_run:
        try:
            post_kb_version_id = snapshot_kb("after_nightly_learn")
            post_bandit_version_id = snapshot_bandit("after_nightly_learn")

            # Compute diffs
            old_kb_path = kb_path().parent / "kb_versions" / f"kb_{pre_kb_version_id}.json"
            new_kb_path = kb_path()
            if old_kb_path.exists():
                kb_diff = diff_kb(old_kb_path, new_kb_path)
                kb_diff_dict = kb_diff.to_dict()

            old_bandit_path = bandit_path().parent.parent / "bandit_versions" / f"posteriors_{pre_bandit_version_id}.json"
            new_bandit_path = bandit_path()
            if old_bandit_path.exists() and new_bandit_path.exists():
                bandit_diff = diff_bandit(old_bandit_path, new_bandit_path)
                bandit_diff_dict = bandit_diff.to_dict()

            print(f"Post-snapshots created: KB {post_kb_version_id}, Bandit {post_bandit_version_id}")
            if kb_diff_dict.get("n_tickers_affected", 0) > 0:
                print(f"  KB diffs: {kb_diff_dict['n_tickers_affected']} ticker multipliers, "
                      f"{len(kb_diff_dict.get('blacklist_changes', []))} blacklist changes")
        except Exception as e:
            print(f"Warning: post-snapshot or diff failed: {e}")

    # === Log learning event ===
    if not args.dry_run and (pre_kb_version_id or post_kb_version_id):
        try:
            perf_snapshot = _compute_perf_snapshot(outcomes, days=30)
            event = {
                "event_id": f"learn_{post_kb_version_id or 'unknown'}",
                "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                "event_type": "daily_learn",
                "n_outcomes_used": kb.n_outcomes_used,
                "window_days": args.window,
                "pre_kb_version_id": pre_kb_version_id,
                "post_kb_version_id": post_kb_version_id,
                "pre_bandit_version_id": pre_bandit_version_id,
                "post_bandit_version_id": post_bandit_version_id,
                "kb_diff": kb_diff_dict,
                "bandit_diff": bandit_diff_dict,
                "perf_snapshot": perf_snapshot,
            }
            events_path = learning_events_path()
            with open(events_path, "a") as f:
                f.write(json.dumps(event) + "\n")
            print(f"Learning event logged: {events_path}")
        except Exception as e:
            print(f"Warning: learning event logging failed: {e}")

    # Generate report
    report = _report_markdown(kb)
    report_path = _write_report(report, args.dry_run)
    if not args.dry_run:
        print(f"Insights report written: {report_path}")
    else:
        print(f"(dry-run: would write report to {report_path})")
        print("\n--- REPORT PREVIEW (first 40 lines) ---")
        for line in report.splitlines()[:40]:
            print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
