"""EWMA covariance tracker for debater opinion correlations.

Phase A3: Replace the hardcoded _fixed_correlation() pairs in the consensus
engine with a rolling EWMA estimate computed from the debater opinion log.

Why this matters:
  The GLS consensus engine weights debaters by the inverse of their correlation
  matrix. If two debaters are highly correlated, their combined vote counts as
  one, not two. Hardcoded correlations (directional/income = 0.40 etc.) are
  assumptions that were never empirically validated. In trending regimes
  directional and flow may be 0.85 correlated; in a pinned gamma-flat market
  they may be near zero or negative.

EWMA parameters:
  lambda_decay = 0.94   — standard RiskMetrics decay (half-life ~12 observations)
  min_observations = 20 — fall back to flat 0.20 when insufficient history

Usage:
  tracker = EWMACovariance()
  tracker.update("directional", 0.75)   # add a BULLISH×0.75 observation
  tracker.update("income", -0.50)       # add a BEARISH×0.50 observation
  ...
  corr = tracker.correlation_matrix(["directional", "income", "volatility"])

  # Load from log file (most common usage)
  tracker = EWMACovariance.from_log()
  corr = tracker.correlation_matrix(names)
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


_LAMBDA = 0.94          # EWMA decay factor
_MIN_OBS = 20           # minimum observations before trusting the estimate
_DEFAULT_CORR = 0.20    # fallback off-diagonal correlation when data sparse


class EWMACovariance:
    """Rolling EWMA covariance tracker over debater signed scores.

    Maintains per-debater EWMA mean and variance, plus all pairwise EWMA
    covariances. Each new observation is a signed score: +conviction for
    BULLISH, -conviction for BEARISH, 0 for NEUTRAL.

    The tracker is intentionally lightweight: no numpy dependency, pure Python.
    For a 6-debater system a full covariance update is ~36 multiplications.
    """

    def __init__(self, lambda_decay: float = _LAMBDA):
        self._lambda = lambda_decay
        self._n: dict[str, int] = {}            # observation count per debater
        self._mean: dict[str, float] = {}       # EWMA mean per debater
        self._var: dict[str, float] = {}        # EWMA variance per debater
        self._cov: dict[frozenset, float] = {}  # EWMA covariance per pair
        self._last: dict[str, float] = {}       # last signed score per debater (for pairs)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def update(self, debater_name: str, signed_score: float) -> None:
        """Incorporate a new signed score observation for one debater.

        signed_score: +conviction for BULLISH, -conviction for BEARISH, 0 for NEUTRAL.
        Prefer update_batch() when multiple debaters report from the same scan —
        single-debater updates cannot form cross-covariance terms for that scan.
        """
        self.update_batch({debater_name: signed_score})

    def update_batch(self, opinions: dict[str, float]) -> None:
        """Update all debaters from one scan's opinions atomically.

        Computes deviations against pre-update means for ALL debaters first,
        then commits new means/vars/covs. This makes the result independent of
        the iteration order of `opinions`, which the prior implementation was
        not (A's mean was updated before B's cross-cov used it).

        opinions: {debater_name: signed_score}
        """
        lam = self._lambda

        # Snapshot pre-update means so deviations are consistent across pairs.
        pre_means = {name: self._mean.get(name, score) for name, score in opinions.items()}
        deviations = {name: score - pre_means[name] for name, score in opinions.items()}

        # Update per-debater mean / variance / count
        for name, score in opinions.items():
            if name not in self._mean:
                self._mean[name] = score
                self._var[name] = 0.0
                self._n[name] = 1
                self._last[name] = score
                continue
            dev = deviations[name]
            self._mean[name] = lam * pre_means[name] + (1 - lam) * score
            self._var[name] = lam * self._var[name] + (1 - lam) * dev * dev
            self._n[name] += 1
            self._last[name] = score

        # Update pairwise covariances using the pre-update deviations.
        names = list(opinions.keys())
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                # Only contribute a co-deviation if BOTH had prior history;
                # otherwise pre_means[x] == score and dev is 0 anyway.
                key = frozenset([a, b])
                old_cov = self._cov.get(key, 0.0)
                self._cov[key] = lam * old_cov + (1 - lam) * deviations[a] * deviations[b]

    def correlation(self, name1: str, name2: str) -> float:
        """Pearson correlation estimate between two debaters.

        Returns _DEFAULT_CORR when insufficient data for either debater.
        Always returns 1.0 for same debater.
        """
        if name1 == name2:
            return 1.0

        n1 = self._n.get(name1, 0)
        n2 = self._n.get(name2, 0)
        if n1 < _MIN_OBS or n2 < _MIN_OBS:
            return _DEFAULT_CORR

        var1 = self._var.get(name1, 0.0)
        var2 = self._var.get(name2, 0.0)
        if var1 <= 1e-10 or var2 <= 1e-10:
            return _DEFAULT_CORR

        key = frozenset([name1, name2])
        cov = self._cov.get(key, 0.0)
        corr = cov / math.sqrt(var1 * var2)

        # Clip to [-1, 1] with a small epsilon to avoid numerical issues
        return max(-0.99, min(0.99, corr))

    def correlation_matrix(self, names: list[str]) -> dict[str, dict[str, float]]:
        """Full correlation matrix for a set of debater names.

        Returns NxN dict. Diagonal is always 1.0.
        Off-diagonal entries fall back to _DEFAULT_CORR when data sparse.
        """
        matrix = {}
        for n1 in names:
            matrix[n1] = {}
            for n2 in names:
                matrix[n1][n2] = self.correlation(n1, n2)
        return matrix

    def observation_counts(self) -> dict[str, int]:
        """Return number of observations per debater (for diagnostics)."""
        return dict(self._n)

    def is_warm(self, names: list[str]) -> bool:
        """True if all named debaters have >= _MIN_OBS observations."""
        return all(self._n.get(n, 0) >= _MIN_OBS for n in names)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise state to a plain dict."""
        return {
            "lambda": self._lambda,
            "n": self._n,
            "mean": self._mean,
            "var": self._var,
            "cov": {
                f"{sorted(k)[0]}:{sorted(k)[1]}": v
                for k, v in self._cov.items()
            },
            "last": self._last,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EWMACovariance:
        """Deserialise state from a plain dict."""
        tracker = cls(lambda_decay=data.get("lambda", _LAMBDA))
        tracker._n = {k: int(v) for k, v in data.get("n", {}).items()}
        tracker._mean = dict(data.get("mean", {}))
        tracker._var = dict(data.get("var", {}))
        tracker._last = dict(data.get("last", {}))
        for key_str, val in data.get("cov", {}).items():
            parts = key_str.split(":", 1)
            if len(parts) == 2:
                tracker._cov[frozenset(parts)] = float(val)
        return tracker

    def save(self, path: Path) -> None:
        """Save state to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> EWMACovariance:
        """Load state from JSON file. Returns fresh tracker if file missing."""
        if not path.exists():
            return cls()
        with open(path) as f:
            return cls.from_dict(json.load(f))

    # ------------------------------------------------------------------
    # Log replay
    # ------------------------------------------------------------------

    @classmethod
    def from_log(cls, log_path: Path | None = None) -> EWMACovariance:
        """Build EWMA tracker by replaying the debater opinion log.

        Reads opinions.jsonl, groups by (ticker, timestamp-minute) to
        reconstruct per-scan batches, and updates the tracker in chronological
        order. Opinions without a resolved outcome are still used for
        correlation estimation (direction × conviction regardless of hit).

        Falls back to empty tracker if log is missing or empty.
        """
        from tradingbot.learning.debater_logger import debater_log_path

        path = log_path or debater_log_path()
        if not path.exists():
            return cls()

        tracker = cls()
        # Group opinions by (ts-minute, ticker) to reconstruct scan batches
        batches: dict[str, dict[str, float]] = {}

        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = entry.get("ts", "")[:16]   # truncate to minute
                ticker = entry.get("ticker", "")
                batch_key = f"{ts}|{ticker}"

                direction = entry.get("direction", "NEUTRAL")
                conviction = float(entry.get("conviction", 0.0))

                if direction == "BULLISH":
                    signed_score = conviction
                elif direction == "BEARISH":
                    signed_score = -conviction
                else:
                    signed_score = 0.0

                if batch_key not in batches:
                    batches[batch_key] = {}
                batches[batch_key][entry.get("debater_name", "")] = signed_score

        # Replay in chronological order (dict insertion order, Python 3.7+)
        for batch_key in sorted(batches):
            tracker.update_batch(batches[batch_key])

        return tracker


def default_covariance_path() -> Path:
    """Default path for persisted EWMA covariance state."""
    from tradingbot.core.config import tradingbot_home
    base = tradingbot_home() / "covariance"
    base.mkdir(parents=True, exist_ok=True)
    return base / "ewma_covariance.json"
