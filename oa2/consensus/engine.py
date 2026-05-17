"""Consensus engine — GLS aggregation of debater opinions with correlation-aware weighting."""

from __future__ import annotations

from typing import Any

from oa2.consensus.state import Consensus, Direction
from oa2.debaters.base import DebaterOpinion


class ConsensusEngine:
    """Aggregates 5 debater opinions into a single consensus direction.

    Uses Generalized Least Squares (GLS) weighting to account for correlation
    between debaters. Debaters that agree more strongly get higher weights;
    debaters with high noise (low conviction) get discounted.

    Outputs:
    - direction: BULLISH, BEARISH, or NEUTRAL
    - score: [0, 1] — strength of consensus (0.5 = no consensus)
    - n_eff: effective sample size (accounting for correlation)
    - p_bull: calibrated probability of bullish direction
    - weights: final GLS weight per debater
    """

    def __init__(self, regime: str | None = None):
        """Initialize consensus engine.

        Args:
            regime: optional regime string for regime-aware weighting (future)
        """
        self.regime = regime

    def aggregate(self, opinions: list[DebaterOpinion]) -> Consensus:
        """Aggregate debater opinions into consensus.

        Args:
            opinions: list of 5 DebaterOpinion objects

        Returns:
            Consensus with direction, score, N_eff, p_bull, and weights
        """
        if not opinions:
            return self._neutral_consensus()

        # Convert opinions to numeric scores [-1, 0, 1]
        opinion_vectors = self._vectorize_opinions(opinions)

        # Compute correlation matrix between debater opinions
        corr_matrix = self._compute_correlation_matrix(opinion_vectors)

        # Compute inverse covariance (precision) matrix
        precision_matrix = self._invert_correlation(corr_matrix)

        # Compute GLS weights
        weights = self._compute_gls_weights(opinion_vectors, precision_matrix)

        # Normalize weights to [0, 1]
        total_weight = sum(weights.values())
        if total_weight > 0:
            weights = {k: v / total_weight for k, v in weights.items()}
        else:
            weights = {op.debater_name: 0.2 for op in opinions}  # equal weight fallback

        # Compute weighted consensus score
        consensus_score = self._compute_consensus_score(opinion_vectors, weights)

        # Compute effective sample size (N_eff = sum(weights)^2 / sum(weights^2))
        n_eff = self._compute_n_eff(weights)

        # Determine direction
        if consensus_score > 0.1:
            direction = Direction.BULLISH
        elif consensus_score < -0.1:
            direction = Direction.BEARISH
        else:
            direction = Direction.NEUTRAL

        # Compute calibrated probability of bullish
        p_bull = self._calibrate_probability(consensus_score, n_eff)

        # Normalize consensus score to [0, 1]
        normalized_score = (consensus_score + 1.0) / 2.0  # maps [-1, 1] to [0, 1]

        return Consensus(
            direction=direction,
            score=normalized_score,
            n_eff=n_eff,
            p_bull=p_bull,
            weights=weights,
            corr_matrix=corr_matrix if self.regime else None,  # only include for debugging
        )

    @staticmethod
    def _vectorize_opinions(opinions: list[DebaterOpinion]) -> dict[str, float]:
        """Convert opinion objects to numeric vectors.

        BULLISH + conviction → +conviction
        BEARISH + conviction → -conviction
        NEUTRAL → 0 (ignore conviction)

        Returns dict of {debater_name: score in [-1, 1]}
        """
        vectors = {}
        for op in opinions:
            if op.direction == "BULLISH":
                score = op.conviction
            elif op.direction == "BEARISH":
                score = -op.conviction
            else:  # NEUTRAL
                score = 0.0

            vectors[op.debater_name] = score

        return vectors

    @staticmethod
    def _compute_correlation_matrix(opinion_vectors: dict[str, float]) -> dict[str, dict[str, float]]:
        """Compute correlation matrix between debater opinions.

        Simple Pearson correlation between opinion vectors.
        Assumes stable correlation over a rolling window (future: update dynamically).

        Returns NxN correlation matrix where N = len(opinion_vectors)
        """
        names = list(opinion_vectors.keys())
        corr = {}

        for name1 in names:
            corr[name1] = {}
            for name2 in names:
                if name1 == name2:
                    corr[name1][name2] = 1.0
                else:
                    # For now: fixed correlations based on debater pairs
                    # (In Phase 4, these would be learned from historical data)
                    corr[name1][name2] = ConsensusEngine._fixed_correlation(name1, name2)

        return corr

    @staticmethod
    def _fixed_correlation(name1: str, name2: str) -> float:
        """Fixed correlation between debater pairs (learned from Phase 1 logs).

        Directional & Income are moderately correlated (both bullish on momentum + cheap IV)
        Directional & Volatility: weakly correlated (different signals)
        Flow & Sentiment: weakly correlated (flow is institutional, sentiment is crowd)
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

        key = frozenset([name1, name2])
        return pairs.get(key, 0.20)  # default low correlation

    @staticmethod
    def _invert_correlation(corr_matrix: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
        """Compute inverse (precision) matrix from correlation matrix.

        For small 5x5 matrices, use simplified approach:
        Precision ≈ (I - ρ * ones) where ρ is avg off-diagonal correlation.
        """
        names = list(corr_matrix.keys())
        n = len(names)

        # Compute average off-diagonal correlation
        avg_rho = 0.0
        count = 0
        for name1 in names:
            for name2 in names:
                if name1 != name2:
                    avg_rho += corr_matrix[name1][name2]
                    count += 1

        if count > 0:
            avg_rho /= count
        else:
            avg_rho = 0.0

        # Simplified inverse: (I - ρ * ones)^-1
        # For corr matrix with constant off-diag ρ:
        # inv[i][i] ≈ (1 + (n-1)*ρ)^-1 + (n-1)*ρ*(1 + (n-1)*ρ)^-2
        # inv[i][j] ≈ -ρ*(1 + (n-1)*ρ)^-2
        denom = 1.0 + (n - 1) * avg_rho
        if denom > 1e-6:
            diag = 1.0 / denom
            off_diag = -avg_rho / (denom * denom)
        else:
            diag = 1.0
            off_diag = 0.0

        precision = {}
        for name1 in names:
            precision[name1] = {}
            for name2 in names:
                if name1 == name2:
                    precision[name1][name2] = diag
                else:
                    precision[name1][name2] = off_diag

        return precision

    @staticmethod
    def _compute_gls_weights(
        opinion_vectors: dict[str, float], precision_matrix: dict[str, dict[str, float]]
    ) -> dict[str, float]:
        """Compute GLS weights from precision matrix.

        GLS weight ∝ sum_j precision[i][j] * opinion[j]

        High conviction opinions with low noise (high precision) get higher weights.
        """
        weights = {}
        names = list(opinion_vectors.keys())

        for name1 in names:
            weight = 0.0
            for name2 in names:
                weight += precision_matrix[name1][name2] * abs(opinion_vectors[name2])
            weights[name1] = max(weight, 0.0)  # non-negative weights

        return weights

    @staticmethod
    def _compute_consensus_score(
        opinion_vectors: dict[str, float], weights: dict[str, float]
    ) -> float:
        """Compute weighted consensus score in [-1, 1].

        Consensus = sum(weights[i] * opinion[i]) / sum(weights[i])
        """
        numerator = sum(weights.get(name, 0.0) * opinion_vectors[name] for name in opinion_vectors)
        denominator = sum(weights.values())

        if denominator > 1e-6:
            return numerator / denominator
        else:
            # Fallback: equal weight average
            return sum(opinion_vectors.values()) / len(opinion_vectors)

    @staticmethod
    def _compute_n_eff(weights: dict[str, float]) -> float:
        """Compute effective sample size N_eff.

        N_eff = (sum weights)^2 / sum(weights^2)

        If all weights equal: N_eff = N (perfect independence)
        If weights highly skewed: N_eff < 1 (single dominant opinion)
        """
        sum_w = sum(weights.values())
        sum_w2 = sum(w * w for w in weights.values())

        if sum_w2 > 1e-6:
            n_eff = (sum_w * sum_w) / sum_w2
        else:
            n_eff = len(weights)

        return n_eff

    @staticmethod
    def _calibrate_probability(consensus_score: float, n_eff: float) -> float:
        """Calibrate probability of bullish from consensus score and N_eff.

        p_bull = sigmoid(consensus_score * n_eff)

        Higher N_eff amplifies the consensus score.
        Low N_eff (correlated opinions) reduces confidence.
        """

        def sigmoid(x: float) -> float:
            if x > 100:
                return 1.0
            elif x < -100:
                return 0.0
            else:
                import math

                return 1.0 / (1.0 + math.exp(-x))

        return sigmoid(consensus_score * n_eff * 2.0)  # * 2.0 to boost signal

    @staticmethod
    def _neutral_consensus() -> Consensus:
        """Return neutral consensus when no opinions available."""
        return Consensus(
            direction=Direction.NEUTRAL,
            score=0.5,
            n_eff=1.0,
            p_bull=0.5,
            weights={},
        )