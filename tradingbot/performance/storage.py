"""Persistence layer for bandit posteriors."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from tradingbot.core.config import tradingbot_home
from tradingbot.performance.bandit import BetaPosterior


def bandit_path(path: Path | None = None) -> Path:
    """Get path to bandit posteriors file."""
    if path:
        return path
    base = tradingbot_home() / "bandit"
    base.mkdir(parents=True, exist_ok=True)
    return base / "posteriors.json"


def save_posteriors(posteriors: dict[tuple[str, int], BetaPosterior], path: Path | None = None) -> None:
    """Serialize posteriors to JSON file atomically.

    Tuple keys (debater_name, regime_id) are serialized as "debater_name:regime_id".
    Snapshots before overwrite to bandit_versions/ for audit trail.
    """
    path = bandit_path(path)

    # Snapshot the current posteriors before overwriting
    if path.exists():
        try:
            from oa2.learning.versioning import snapshot_bandit
            snapshot_bandit("save_posteriors")
        except Exception:
            pass

    serialized = {}
    for (debater_name, regime_id), posterior in posteriors.items():
        key = f"{debater_name}:{regime_id}"
        serialized[key] = {"alpha": posterior.alpha, "beta": posterior.beta}

    # Atomic write: write to temp file, then rename
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as tmp:
        json.dump(serialized, tmp, indent=2)
        tmp_path = tmp.name

    try:
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def load_posteriors(path: Path | None = None) -> dict[tuple[str, int], BetaPosterior]:
    """Deserialize posteriors from JSON file.

    Returns empty dict if file doesn't exist.
    """
    path = bandit_path(path)
    if not path.exists():
        return {}

    with open(path) as f:
        serialized = json.load(f)

    posteriors = {}
    for key, data in serialized.items():
        if ":" not in key:
            continue
        debater_name, regime_str = key.rsplit(":", 1)
        try:
            regime_id = int(regime_str)
            posteriors[(debater_name, regime_id)] = BetaPosterior(
                alpha=data.get("alpha", 1.0),
                beta=data.get("beta", 1.0),
            )
        except (ValueError, KeyError):
            continue

    return posteriors