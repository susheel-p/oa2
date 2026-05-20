"""Rollback learning to a previous state.

Usage:
    python scripts/rollback_learning.py --list
    python scripts/rollback_learning.py --show YYYY-MM-DD_HHMMSS
    python scripts/rollback_learning.py --restore YYYY-MM-DD_HHMMSS
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from tradingbot.learning.versioning import (
    diff_kb, diff_bandit, kb_path, bandit_path, list_versions,
    restore_kb, restore_bandit, learning_events_path, _version_dir
)
from tradingbot.core.config import tradingbot_home

ET = ZoneInfo("America/New_York")


def cmd_list():
    """List all versions with summary stats."""
    versions = list_versions()
    if not versions:
        print("No learning events recorded yet.")
        return

    print("\n" + "=" * 100)
    print(f"{'VERSION':<25} {'DATE':<12} {'CHANGES':<10} {'WIN_RATE':<12} {'AVG_PNL':<12}")
    print("=" * 100)

    for v in versions:
        version_id = v["version_id"]
        ts_str = v["ts"][:10] if v["ts"] else "unknown"
        n_changes = v["n_changes"]
        win_rate = f"{v['win_rate']:.1%}" if v.get("win_rate") else "—"
        avg_pnl = f"${v['avg_pnl']:+.0f}" if v.get("avg_pnl") else "—"

        marker = " (latest)" if version_id == versions[0]["version_id"] else ""
        print(f"{version_id:<25} {ts_str:<12} {n_changes:<10} {win_rate:<12} {avg_pnl:<12}{marker}")

    print("=" * 100 + "\n")


def cmd_show(version_id: str):
    """Show detailed changes for a version."""
    versions = list_versions()
    matching = [v for v in versions if v["version_id"] == version_id]

    if not matching:
        print(f"Version {version_id} not found.")
        return

    # Load the event record from learning_events.jsonl
    events_path = learning_events_path()
    if not events_path.exists():
        print("No learning events recorded.")
        return

    event = None
    with open(events_path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                if e.get("post_kb_version_id") == version_id:
                    event = e
                    break
            except json.JSONDecodeError:
                pass

    if not event:
        print(f"Event record for {version_id} not found.")
        return

    print(f"\n{'='*80}")
    print(f"Learning Event: {version_id}")
    print(f"Timestamp: {event.get('ts', '?')}")
    print(f"Outcomes used (60-day window): {event.get('n_outcomes_used', '?')}")
    print(f"{'='*80}\n")

    kb_diff = event.get("kb_diff", {})
    bandit_diff = event.get("bandit_diff", {})

    # KB changes
    if kb_diff.get("ticker_multiplier_changes"):
        print("Ticker Multiplier Changes:")
        for change in kb_diff["ticker_multiplier_changes"]:
            ticker = change["ticker"]
            old = change["old"]
            new = change["new"]
            delta = change["delta"]
            hit_rate = change["hit_rate"]
            print(f"  {ticker:<6} {old:.3f} → {new:.3f} (Δ {delta:+.3f}, hit_rate={hit_rate:.1%})")
        print()

    if kb_diff.get("blacklist_changes"):
        print("Blacklist Changes:")
        for change in kb_diff["blacklist_changes"]:
            ticker = change["ticker"]
            action = change["action"]
            hit_rate = change.get("hit_rate", 0)
            print(f"  {ticker}: {action.upper()} (hit_rate={hit_rate:.1%})")
        print()

    if kb_diff.get("debater_changes"):
        print("Debater Changes:")
        for change in kb_diff["debater_changes"]:
            debater = change["debater"]
            old_hit = change["old_hit_rate"]
            new_hit = change["new_hit_rate"]
            n_votes = change.get("n_votes", 0)
            print(f"  {debater:<15} {old_hit:.1%} → {new_hit:.1%} ({n_votes} votes)")
        print()

    # Bandit changes (top 5)
    if bandit_diff.get("changes"):
        changes = bandit_diff["changes"][:5]
        print(f"Bandit Changes ({len(bandit_diff['changes'])} total, showing first 5):")
        for change in changes:
            key = change["key"]
            old_alpha = change["old_alpha"]
            new_alpha = change["new_alpha"]
            alpha_delta = change["alpha_delta"]
            print(f"  {key:<20} α: {old_alpha:.1f} → {new_alpha:.1f} (Δ {alpha_delta:+.1f})")
        print()

    # Performance snapshot
    perf = event.get("perf_snapshot", {})
    if perf:
        print("Performance Snapshot (at time of learning):")
        print(f"  30-day win rate: {perf.get('last_30d_win_rate', 0):.1%}")
        print(f"  30-day avg P&L:  ${perf.get('last_30d_avg_pnl', 0):+.0f}")
        print(f"  30-day n_trades: {perf.get('last_30d_n_trades', 0)}")
        print()

    print(f"{'='*80}\n")


def cmd_restore(version_id: str):
    """Restore KB + bandit to a specific version."""
    versions = list_versions()
    matching = [v for v in versions if v["version_id"] == version_id]

    if not matching:
        print(f"Version {version_id} not found.")
        return

    # Confirm
    print(f"\nAbout to restore to version {version_id}")
    cmd_show(version_id)
    response = input("Restore this version? (yes/no) ").strip().lower()

    if response not in ("yes", "y"):
        print("Cancelled.")
        return

    # Restore
    print(f"\nRestoring KB from {version_id}...", end=" ")
    if restore_kb(version_id):
        print("✓")
    else:
        print("✗ FAILED")
        return

    print(f"Restoring bandit posteriors from {version_id}...", end=" ")
    if restore_bandit(version_id):
        print("✓")
    else:
        print("✗ FAILED")
        return

    # Log the rollback event
    event = {
        "event_id": f"rollback_{version_id}",
        "ts": __import__("datetime").datetime.now(ET).isoformat(),
        "event_type": "rollback",
        "restored_to_version_id": version_id,
    }
    events_path = learning_events_path()
    with open(events_path, "a") as f:
        f.write(json.dumps(event) + "\n")

    print(f"\nRollback complete. Learning event logged.")
    print("Run 'python scripts/daily_learn.py' when ready to re-learn from this point.\n")


def main():
    parser = argparse.ArgumentParser(description="Rollback learning to a previous state")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("list", help="List all versions")
    subparsers.add_parser("show", help="Show changes for a version").add_argument("version_id")
    subparsers.add_parser("restore", help="Restore to a version").add_argument("version_id")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list()
    elif args.command == "show":
        cmd_show(args.version_id)
    elif args.command == "restore":
        cmd_restore(args.version_id)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
