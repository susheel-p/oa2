"""Persistence layer for bandit posteriors."""

from __future__ import annotations

import json
from pathlib import Path

from oa2.core.config import oa2_home
from oa2.performance.bandit import BetaPosterior


def bandit_path(path: Path | None = None) -> Path:
    """Get path to bandit posteriors file."""
    if path:
        return path
    base = oa2_home() / "bandit"
    base.mkdir(parents=True, exist_ok=True)
    return base / "posteriors.json"


def save_posteriors(posteriors: dict[tuple[str, int], BetaPosterior], path: Path | None = None) -> None:
    """Serialize posteriors to JSON file.

    Tuple keys (debater_name, regime_id) are serialized as "debater_name:regime_id".
    """
    path = bandit_path(path)
    serialized = {}
    for (debater_name, regime_id), posterior in posteriors.items():
        key = f"{debater_name}:{regime_id}"
        serialized[key] = {"alpha": posterior.alpha, "beta": posterior.beta}

    with open(path, "w") as f:
        json.dump(serialized, f, indent=2)


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