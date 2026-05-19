"""Thompson sampling bandit for per-debater, per-regime performance tracking."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oa2.core.config import oa2_home
from oa2.learning.debater_logger import debater_log_path


@dataclass
class BetaPosterior:
    """Beta(alpha, beta) posterior for Bayesian hit-rate estimation."""
    alpha: float = 1.0  # wins + 1
    beta: float = 1.0   # losses + 1

    def mean(self) -> float:
        """Posterior mean: E[p] = alpha / (alpha + beta)."""
        return self.alpha / (self.alpha + self.beta)

    def sample(self) -> float:
        """Thompson sample from Beta(alpha, beta). Returns value in [0, 1]."""
        return random.betavariate(self.alpha, self.beta)


class BanditEngine:
    """Thompson sampling bandit with 48 arms: 6 debaters × 8 regimes."""

    def __init__(self):
        # key: (debater_name, regime_id) → BetaPosterior
        self._posteriors: dict[tuple[str, int], BetaPosterior] = {}

    def get_weight(self, debater_name: str, regime_id: int, *, use_mean: bool = True) -> float:
        """Get weight (posterior mean or Thompson sample) for (debater, regime)."""
        key = (debater_name, regime_id)
        posterior = self._posteriors.get(key, BetaPosterior())
        return posterior.mean() if use_mean else posterior.sample()

    def get_regime_weights(
        self, debater_names: list[str], regime_id: int, *, use_mean: bool = True
    ) -> dict[str, float]:
        """Get normalized weights for all debaters in a specific regime.

        Returns weights that sum to 1.0.
        """
        raw = {name: self.get_weight(name, regime_id, use_mean=use_mean) for name in debater_names}
        total = sum(raw.values()) or 1.0
        return {name: weight / total for name, weight in raw.items()}

    def update(self, debater_name: str, regime_id: int, *, hit: bool, decay: float = 1.0) -> None:
        """Update Beta posterior for (debater, regime) after trade outcome with optional decay."""
        key = (debater_name, regime_id)
        if key not in self._posteriors:
            self._posteriors[key] = BetaPosterior()
        
        # Apply exponential decay to the current posterior parameters above the prior
        if decay < 1.0:
            self._posteriors[key].alpha = (self._posteriors[key].alpha - 1.0) * decay + 1.0
            self._posteriors[key].beta = (self._posteriors[key].beta - 1.0) * decay + 1.0

        if hit:
            self._posteriors[key].alpha += 1.0
        else:
            self._posteriors[key].beta += 1.0

    def update_from_trade(self, trade_id: str, regime_id: int) -> None:
        """Read debater_logger JSONL, extract outcomes for trade_id, update posteriors."""
        path = debater_log_path()
        if not path.exists():
            return

        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                entry = json.loads(line)
                if entry.get("trade_id") == trade_id and entry.get("hit") is not None:
                    self.update(entry["debater_name"], regime_id, hit=entry["hit"])

    @classmethod
    def load(cls, path: Path | None = None) -> BanditEngine:
        """Load bandit engine from JSON file. Returns empty engine if file missing."""
        from oa2.performance.storage import load_posteriors
        engine = cls()
        engine._posteriors = load_posteriors(path)
        return engine

    def save(self, path: Path | None = None) -> None:
        """Save bandit engine posteriors to JSON file."""
        from oa2.performance.storage import save_posteriors
        save_posteriors(self._posteriors, path)