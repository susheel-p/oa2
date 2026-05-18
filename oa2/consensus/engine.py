"""Consensus engine — heteroscedastic GLS aggregation of debater opinions.

Algorithm
---------
Each debater produces a signed score o_i ∈ [−1, 1] (direction × conviction).
We model these as noisy observations of the true consensus direction c:

    o_i = c + ε_i,   Cov(ε) = Σ

The noise covariance is built from two sources:

  * Correlation structure (C):  how much pairs of debaters co-move.
    Source: EWMA live estimates (Phase A3) when warm; hardcoded priors otherwise.

  * Per-debater noise variance (σ_i²):  how certain each debater is.
    σ_i = max(NOISE_FLOOR, 1 − |o_i|).
    A debater who says BULLISH×0.95 has σ = 0.05 (low noise, high precision).
    A NEUTRAL debater always has σ = 1.0 (no signal, minimal influence).

Full covariance:  Σ_ij = C_ij · σ_i · σ_j   (heteroscedastic correlation model)
Ridge:            Σ_r   = Σ + λI               (prevents singular matrix; λ = 1e-3)
Precision:        Ω     = Σ_r⁻¹                (numpy.linalg.inv)

GLS estimator (BLUE):
    ĉ = (1ᵀ Ω 1)⁻¹ · 1ᵀ Ω o = Σ w_i o_i
where w_i = (Ω·1)_i / (1ᵀ·Ω·1)  — row sums of Ω, normalised to sum to 1.

N_eff = 1 / Σ w_i²  (Herfindahl ESS; equals N when all weights are equal,
                      falls as correlation or conviction concentration increases)

Phase A3:  If OA2_FLAG_EWMA_CORR=1 and the EWMA tracker has ≥ 20 observations
           per debater, live EWMA correlations replace the hardcoded priors.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from oa2.consensus.state import Consensus, Direction
from oa2.debaters.base import DebaterOpinion

_RIDGE_LAMBDA = 1e-3   # ridge regularisation added to diagonal of Σ before inversion
_NOISE_FLOOR = 0.05    # minimum σ_i; prevents zero-variance debaters collapsing Ω


class ConsensusEngine:
    """Aggregates debater opinions into a single consensus direction via GLS.

    High-conviction debaters receive higher GLS weights (lower noise → higher
    precision).  Correlated debater pairs are down-weighted: if two debaters
    are highly correlated their combined vote counts as less than two independent
    sources — exactly what the inverse of a correlation matrix expresses.

    Correlation source (Phase A3):
    - OA2_FLAG_EWMA_CORR=1 (default True) + tracker warm → live EWMA correlations.
    - Otherwise → hardcoded _fixed_correlation() priors.
    """

    def __init__(
        self,
        regime: str | None = None,
        prior_weights: dict[str, float] | None = None,
        covariance_tracker=None,
    ):
        """Initialise the consensus engine.

        Args:
            regime:           Optional regime string for regime-aware weighting.
            prior_weights:    Optional bandit prior weights (Phase 4).
            covariance_tracker: Optional EWMACovariance instance (Phase A3).
                When None, loads from disk if OA2_FLAG_EWMA_CORR enabled.
        """
        self.regime = regime
        self.prior_weights = prior_weights
        self._covariance_tracker = covariance_tracker

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def aggregate(self, opinions: list[DebaterOpinion]) -> Consensus:
        """Aggregate debater opinions into a single consensus.

        Args:
            opinions: list of DebaterOpinion objects (typically 5–6).

        Returns:
            Consensus with direction, score ∈ [0,1], n_eff, p_bull, and weights.
        """
        if not opinions:
            return self._neutral_consensus()

        # Step 1: convert opinions to signed scores o_i ∈ [−1, 1]
        opinion_vectors = self._vectorize_opinions(opinions)

        # Step 2: build NxN correlation matrix (EWMA or fixed priors)
        corr_matrix = self._compute_correlation_matrix(opinion_vectors)

        # Step 3: compute GLS weights via proper matrix inversion
        weights = self._compute_gls_weights(opinion_vectors, corr_matrix)

        # Step 4 (Phase 4): blend with bandit prior weights when available
        if self.prior_weights:
            blended = {
                name: weights[name] * self.prior_weights.get(name, 1.0)
                for name in weights
            }
            total = sum(blended.values())
            weights = (
                {k: v / total for k, v in blended.items()}
                if total > 1e-10
                else {op.debater_name: 1.0 / len(opinions) for op in opinions}
            )

        # Step 5: weighted consensus score ĉ ∈ [−1, 1]
        raw_score = sum(weights[name] * opinion_vectors[name] for name in opinion_vectors)

        # Step 6: effective sample size N_eff = 1 / Σ w_i²
        n_eff = self._compute_n_eff(weights)

        # Step 7: direction threshold (±0.10 dead-band for NEUTRAL)
        if raw_score > 0.10:
            direction = Direction.BULLISH
        elif raw_score < -0.10:
            direction = Direction.BEARISH
        else:
            direction = Direction.NEUTRAL

        # Step 8: calibrated p_bull
        p_bull = self._calibrate_probability(raw_score, n_eff)

        # Step 9: normalise raw score to [0, 1] for the score field
        normalised_score = (raw_score + 1.0) / 2.0

        return Consensus(
            direction=direction,
            score=normalised_score,
            n_eff=n_eff,
            p_bull=p_bull,
            weights=weights,
            corr_matrix=corr_matrix if self.regime else None,
        )

    # ------------------------------------------------------------------
    # Step 1 — vectorisation
    # ------------------------------------------------------------------

    @staticmethod
    def _vectorize_opinions(opinions: list[DebaterOpinion]) -> dict[str, float]:
        """Convert DebaterOpinion objects to signed numeric scores.

        BULLISH × conviction  →  +conviction
        BEARISH × conviction  →  −conviction
        NEUTRAL               →   0.0  (conviction ignored — no directional signal)
        """
        vectors: dict[str, float] = {}
        for op in opinions:
            if op.direction == "BULLISH":
                vectors[op.debater_name] = op.conviction
            elif op.direction == "BEARISH":
                vectors[op.debater_name] = -op.conviction
            else:
                vectors[op.debater_name] = 0.0
        return vectors

    # ------------------------------------------------------------------
    # Step 2 — correlation matrix
    # ------------------------------------------------------------------

    def _get_covariance_tracker(self):
        """Lazy-load the EWMA covariance tracker once per engine instance."""
        if self._covariance_tracker is not None:
            return self._covariance_tracker

        from oa2.core import feature_flags
        if not feature_flags.EWMA_CORR_ENABLED:
            return None

        try:
            from oa2.consensus.covariance import EWMACovariance, default_covariance_path
            self._covariance_tracker = EWMACovariance.load(default_covariance_path())
        except Exception:
            self._covariance_tracker = None

        return self._covariance_tracker

    def _compute_correlation_matrix(
        self, opinion_vectors: dict[str, float]
    ) -> dict[str, dict[str, float]]:
        """Build the NxN correlation matrix C with diagonal 1.

        Phase A3: uses live EWMA estimates when the tracker is warm for both
        debaters; falls back to hardcoded priors otherwise.
        """
        names = list(opinion_vectors.keys())
        tracker = self._get_covariance_tracker()

        if tracker is not None:
            tracker.update_batch(opinion_vectors)

        corr: dict[str, dict[str, float]] = {}
        for n1 in names:
            corr[n1] = {}
            for n2 in names:
                if n1 == n2:
                    corr[n1][n2] = 1.0
                elif tracker is not None and tracker.is_warm([n1, n2]):
                    corr[n1][n2] = tracker.correlation(n1, n2)
                else:
                    corr[n1][n2] = ConsensusEngine._fixed_correlation(n1, n2)

        return corr

    @staticmethod
    def _fixed_correlation(name1: str, name2: str) -> float:
        """Hardcoded prior correlation between known debater pairs.

        Used when EWMA tracker has insufficient observations (< 20 per debater).
        These are domain priors, not empirically validated values.
        Phase A3 replaces them with live EWMA estimates as data accumulates.
        """
        pairs = {
            frozenset(["directional", "income"]): 0.40,
            frozenset(["directional", "volatility"]): 0.15,
            frozenset(["directional", "flow"]): 0.10,
            frozenset(["directional", "sentiment"]): 0.25,
            frozenset(["income", "volatility"]): 0.35,
            frozenset(["income", "flow"]): 0.10,
            frozenset(["income", "sentiment"]): 0.20,
            frozenset(["volatility", "flow"]): 0.05,
            frozenset(["volatility", "sentiment"]): 0.15,
            frozenset(["flow", "sentiment"]): 0.10,
        }
        return pairs.get(frozenset([name1, name2]), 0.20)

    # ------------------------------------------------------------------
    # Step 3 — GLS precision matrix and weights
    # ------------------------------------------------------------------

    @staticmethod
    def _build_precision_matrix(
        opinion_vectors: dict[str, float],
        corr_matrix: dict[str, dict[str, float]],
    ) -> tuple[np.ndarray, list[str]]:
        """Build and invert the heteroscedastic GLS covariance matrix.

        Per-debater noise:
            σ_i = max(NOISE_FLOOR, 1 − |o_i|)

        A debater saying BULLISH×0.95 has σ = 0.05 (very certain).
        A NEUTRAL debater always has σ = 1.0 (no directional signal).

        Full covariance:  Σ_ij = C_ij · σ_i · σ_j
        Ridge-stabilised: Σ_r = Σ + λI
        Precision:        Ω   = Σ_r⁻¹   (numpy.linalg.inv — exact for N ≤ ~30)

        The ridge penalty λ = 1e-3 is negligible relative to the covariance
        entries (which are O(σ²) with σ ≥ 0.05) but prevents singular matrices
        when all debaters are perfectly correlated or have zero variance.

        Returns:
            Ω (ndarray, shape N×N) and the ordered list of debater names.
        """
        names = list(opinion_vectors.keys())
        n = len(names)

        sigma = np.array(
            [max(_NOISE_FLOOR, 1.0 - abs(opinion_vectors[name])) for name in names],
            dtype=float,
        )

        C = np.array(
            [[corr_matrix[n1][n2] for n2 in names] for n1 in names],
            dtype=float,
        )

        Sigma = C * np.outer(sigma, sigma)
        Sigma_ridge = Sigma + _RIDGE_LAMBDA * np.eye(n)
        Omega = np.linalg.inv(Sigma_ridge)

        return Omega, names

    @staticmethod
    def _compute_gls_weights(
        opinion_vectors: dict[str, float],
        corr_matrix: dict[str, dict[str, float]],
    ) -> dict[str, float]:
        """Compute GLS weights: w_i ∝ (Ω·1)_i.

        The BLUE (Best Linear Unbiased Estimator) weights are proportional to
        the row sums of the precision matrix Ω.  They depend only on the
        covariance structure, not on the current opinions.

        Weights are clipped to ≥ 0 (small negatives can arise numerically from
        near-zero convictions) then normalised to sum to 1.
        """
        Omega, names = ConsensusEngine._build_precision_matrix(
            opinion_vectors, corr_matrix
        )

        ones = np.ones(len(names))
        row_sums = Omega @ ones          # (Ω·1) — precision-weighted importance per debater
        row_sums = np.maximum(row_sums, 0.0)

        total = row_sums.sum()
        if total < 1e-10:
            weights_arr = np.ones(len(names)) / len(names)
        else:
            weights_arr = row_sums / total

        return {name: float(w) for name, w in zip(names, weights_arr)}

    # ------------------------------------------------------------------
    # Derived statistics
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_n_eff(weights: dict[str, float]) -> float:
        """Effective sample size: N_eff = 1 / Σ w_i²   (Herfindahl ESS).

        Equal weights of 1/N → N_eff = N  (full independence).
        One dominant weight of 1 → N_eff = 1.
        High correlation or conviction concentration both reduce N_eff.
        """
        sum_w2 = sum(w * w for w in weights.values())
        return 1.0 / sum_w2 if sum_w2 > 1e-10 else float(len(weights))

    @staticmethod
    def _calibrate_probability(raw_score: float, n_eff: float) -> float:
        """Calibrate probability of bullish from raw consensus score and N_eff.

        p_bull = sigmoid(raw_score × n_eff × 2)

        N_eff amplifies the signal: N independent agreements are more
        informative than N correlated ones.
        """
        x = raw_score * n_eff * 2.0
        if x > 100:
            return 1.0
        if x < -100:
            return 0.0
        return 1.0 / (1.0 + math.exp(-x))

    @staticmethod
    def _neutral_consensus() -> Consensus:
        """Return a neutral consensus when no opinions are provided."""
        return Consensus(
            direction=Direction.NEUTRAL,
            score=0.5,
            n_eff=1.0,
            p_bull=0.5,
            weights={},
        )
