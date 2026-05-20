"""Learning audit trail and versioning.

Archives KB and bandit snapshots before each overwrite, computes diffs,
and enables rollback to any past state.

Paths:
  ~/.oa2/kb_versions/kb_<YYYY-MM-DD_HHMMSS>.json
  ~/.oa2/bandit_versions/posteriors_<YYYY-MM-DD_HHMMSS>.json
  ~/.oa2/learning_events.jsonl
"""

from __future__ import annotations

import datetime
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from oa2.core.config import oa2_home


@dataclass
class KBDiff:
    """Machine-readable KB changes."""
    ticker_multiplier_changes: list[dict] = None
    blacklist_changes: list[dict] = None
    regime_changes: list[dict] = None
    debater_changes: list[dict] = None
    regime_blocks_added: list[str] = None
    regime_blocks_removed: list[str] = None
    n_tickers_affected: int = 0
    n_debaters_affected: int = 0

    def __post_init__(self):
        self.ticker_multiplier_changes = self.ticker_multiplier_changes or []
        self.blacklist_changes = self.blacklist_changes or []
        self.regime_changes = self.regime_changes or []
        self.debater_changes = self.debater_changes or []
        self.regime_blocks_added = self.regime_blocks_added or []
        self.regime_blocks_removed = self.regime_blocks_removed or []
        self.n_tickers_affected = len(self.ticker_multiplier_changes)
        self.n_debaters_affected = len(self.debater_changes)

    def to_dict(self) -> dict:
        return {
            "ticker_multiplier_changes": self.ticker_multiplier_changes,
            "blacklist_changes": self.blacklist_changes,
            "regime_changes": self.regime_changes,
            "debater_changes": self.debater_changes,
            "regime_blocks_added": self.regime_blocks_added,
            "regime_blocks_removed": self.regime_blocks_removed,
            "n_tickers_affected": self.n_tickers_affected,
            "n_debaters_affected": self.n_debaters_affected,
        }


@dataclass
class BanditDiff:
    """Machine-readable bandit changes."""
    changes: list[dict] = None
    old_expected: dict = None
    new_expected: dict = None
    n_arms_changed: int = 0

    def __post_init__(self):
        self.changes = self.changes or []
        self.old_expected = self.old_expected or {}
        self.new_expected = self.new_expected or {}
        self.n_arms_changed = len(self.changes)

    def to_dict(self) -> dict:
        return {
            "changes": self.changes,
            "n_arms_changed": self.n_arms_changed,
        }


def _version_dir(subdir: str) -> Path:
    """Get or create a version archive subdirectory."""
    base = oa2_home()
    d = base / subdir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now_str() -> str:
    """ISO-format timestamp usable in filenames."""
    return datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")


def kb_path() -> Path:
    """Current KB path."""
    return oa2_home() / "knowledge_base.json"


def bandit_path() -> Path:
    """Current bandit posteriors path."""
    return oa2_home() / "bandit" / "posteriors.json"


def learning_events_path() -> Path:
    """Append-only event log."""
    return oa2_home() / "learning_events.jsonl"


def snapshot_kb(reason: str = "daily_learn") -> str:
    """Archive the current KB. Returns version_id (timestamp)."""
    src = kb_path()
    if not src.exists():
        return ""

    version_id = _now_str()
    dst = _version_dir("kb_versions") / f"kb_{version_id}.json"

    with open(src) as f_in:
        data = json.load(f_in)
    with open(dst, "w") as f_out:
        json.dump(data, f_out, indent=2)

    return version_id


def snapshot_bandit(reason: str = "daily_learn") -> str:
    """Archive the current bandit posteriors. Returns version_id."""
    src = bandit_path()
    if not src.exists():
        return ""

    version_id = _now_str()
    dst = _version_dir("bandit_versions") / f"posteriors_{version_id}.json"

    with open(src) as f_in:
        data = json.load(f_in)
    with open(dst, "w") as f_out:
        json.dump(data, f_out, indent=2)

    return version_id


def diff_kb(old_path: Path, new_path: Path) -> KBDiff:
    """Compute structured diff between two KB snapshots."""
    diff = KBDiff()

    try:
        with open(old_path) as f:
            old_kb = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        old_kb = {}

    try:
        with open(new_path) as f:
            new_kb = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        new_kb = {}

    # Ticker multiplier changes
    old_tickers = old_kb.get("tickers", {})
    new_tickers = new_kb.get("tickers", {})
    all_tickers = set(old_tickers.keys()) | set(new_tickers.keys())
    for ticker in sorted(all_tickers):
        old_mult = old_tickers.get(ticker, {}).get("conviction_multiplier", 1.0)
        new_mult = new_tickers.get(ticker, {}).get("conviction_multiplier", 1.0)
        if abs(old_mult - new_mult) > 0.001:
            diff.ticker_multiplier_changes.append({
                "ticker": ticker,
                "old": round(old_mult, 3),
                "new": round(new_mult, 3),
                "delta": round(new_mult - old_mult, 3),
                "hit_rate": round(new_tickers.get(ticker, {}).get("hit_rate", 0.5), 3),
                "n_trades": new_tickers.get(ticker, {}).get("n_trades", 0),
            })

    # Blacklist changes
    old_blacklist = {t for t, v in old_tickers.items() if v.get("blacklisted", False)}
    new_blacklist = {t for t, v in new_tickers.items() if v.get("blacklisted", False)}
    for ticker in sorted(new_blacklist - old_blacklist):
        diff.blacklist_changes.append({
            "ticker": ticker,
            "action": "added",
            "hit_rate": round(new_tickers[ticker].get("hit_rate", 0.0), 3),
            "n_trades": new_tickers[ticker].get("n_trades", 0),
        })
    for ticker in sorted(old_blacklist - new_blacklist):
        diff.blacklist_changes.append({
            "ticker": ticker,
            "action": "removed",
            "hit_rate": round(new_tickers.get(ticker, {}).get("hit_rate", 0.0), 3),
            "n_trades": new_tickers.get(ticker, {}).get("n_trades", 0),
        })

    # Regime changes
    old_regimes = old_kb.get("regimes", {})
    new_regimes = new_kb.get("regimes", {})
    all_regimes = set(old_regimes.keys()) | set(new_regimes.keys())
    for regime in sorted(all_regimes):
        old_mult = old_regimes.get(regime, {}).get("conviction_multiplier", 1.0)
        new_mult = new_regimes.get(regime, {}).get("conviction_multiplier", 1.0)
        if abs(old_mult - new_mult) > 0.001:
            diff.regime_changes.append({
                "regime": regime,
                "old": round(old_mult, 3),
                "new": round(new_mult, 3),
                "hit_rate": round(new_regimes.get(regime, {}).get("hit_rate", 0.5), 3),
            })

    # Debater changes
    old_debaters = old_kb.get("debaters", {})
    new_debaters = new_kb.get("debaters", {})
    all_debaters = set(old_debaters.keys()) | set(new_debaters.keys())
    for debater in sorted(all_debaters):
        old_hit = old_debaters.get(debater, {}).get("hit_rate", 0.5)
        new_hit = new_debaters.get(debater, {}).get("hit_rate", 0.5)
        if abs(old_hit - new_hit) > 0.01:
            diff.debater_changes.append({
                "debater": debater,
                "old_hit_rate": round(old_hit, 3),
                "new_hit_rate": round(new_hit, 3),
                "n_votes": new_debaters.get(debater, {}).get("n_votes", 0),
            })

    diff.n_tickers_affected = len(diff.ticker_multiplier_changes)
    diff.n_debaters_affected = len(diff.debater_changes)

    return diff


def diff_bandit(old_path: Path, new_path: Path) -> BanditDiff:
    """Compute structured diff between two bandit snapshots."""
    diff = BanditDiff()

    try:
        with open(old_path) as f:
            old_bandit = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        old_bandit = {}

    try:
        with open(new_path) as f:
            new_bandit = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        new_bandit = {}

    all_keys = set(old_bandit.keys()) | set(new_bandit.keys())
    for key in sorted(all_keys):
        old_post = old_bandit.get(key, {"alpha": 1.0, "beta": 1.0})
        new_post = new_bandit.get(key, {"alpha": 1.0, "beta": 1.0})

        old_alpha = old_post.get("alpha", 1.0)
        old_beta = old_post.get("beta", 1.0)
        new_alpha = new_post.get("alpha", 1.0)
        new_beta = new_post.get("beta", 1.0)

        if abs(old_alpha - new_alpha) > 0.01 or abs(old_beta - new_beta) > 0.01:
            old_exp = old_alpha / (old_alpha + old_beta) if (old_alpha + old_beta) > 0 else 0.5
            new_exp = new_alpha / (new_alpha + new_beta) if (new_alpha + new_beta) > 0 else 0.5

            diff.old_expected[key] = round(old_exp, 3)
            diff.new_expected[key] = round(new_exp, 3)

            diff.changes.append({
                "key": key,
                "old_alpha": round(old_alpha, 2),
                "old_beta": round(old_beta, 2),
                "new_alpha": round(new_alpha, 2),
                "new_beta": round(new_beta, 2),
                "alpha_delta": round(new_alpha - old_alpha, 2),
                "beta_delta": round(new_beta - old_beta, 2),
            })

    diff.n_arms_changed = len(diff.changes)
    return diff


def restore_kb(version_id: str) -> bool:
    """Restore KB from a version snapshot. Returns success."""
    src = _version_dir("kb_versions") / f"kb_{version_id}.json"
    dst = kb_path()

    if not src.exists():
        return False

    with open(src) as f:
        data = json.load(f)

    # Atomic write
    import tempfile
    import os
    with tempfile.NamedTemporaryFile(mode="w", dir=dst.parent, delete=False) as tmp:
        json.dump(data, tmp, indent=2)
        tmp_path = tmp.name

    try:
        os.replace(tmp_path, dst)
        return True
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return False


def restore_bandit(version_id: str) -> bool:
    """Restore bandit posteriors from a version snapshot. Returns success."""
    src = _version_dir("bandit_versions") / f"posteriors_{version_id}.json"
    dst = bandit_path()

    if not src.exists():
        return False

    with open(src) as f:
        data = json.load(f)

    # Atomic write
    import tempfile
    import os
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=dst.parent, delete=False) as tmp:
        json.dump(data, tmp, indent=2)
        tmp_path = tmp.name

    try:
        os.replace(tmp_path, dst)
        return True
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return False


def list_versions() -> list[dict]:
    """List all KB versions with metadata."""
    events_path = learning_events_path()
    if not events_path.exists():
        return []

    versions = []
    with open(events_path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if event.get("event_type") == "daily_learn":
                    version_id = event.get("post_kb_version_id", "")
                    versions.append({
                        "version_id": version_id,
                        "ts": event.get("ts", ""),
                        "n_outcomes_used": event.get("n_outcomes_used", 0),
                        "n_changes": (
                            len(event.get("kb_diff", {}).get("ticker_multiplier_changes", []))
                            + len(event.get("kb_diff", {}).get("blacklist_changes", []))
                        ),
                        "win_rate": event.get("perf_snapshot", {}).get("last_30d_win_rate", 0.5),
                        "avg_pnl": event.get("perf_snapshot", {}).get("last_30d_avg_pnl", 0.0),
                    })
            except json.JSONDecodeError:
                pass

    return sorted(versions, key=lambda x: x["ts"], reverse=True)
