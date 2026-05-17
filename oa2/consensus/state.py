"""Consensus data classes and types."""

from dataclasses import dataclass
from enum import Enum


class Direction(Enum):
    """Trade direction: bullish, bearish, or neutral."""
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass
class Consensus:
    """Consensus output from aggregating debater opinions.

    Uses GLS weighting to account for correlation between debaters.
    Outputs a single direction, confidence score, effective sample size, and calibrated probability.
    """

    direction: Direction                    # BULLISH, BEARISH, NEUTRAL
    score: float                           # [0, 1] — directional strength (0.5 = neutral)
    n_eff: float                           # Effective sample size (5.0 if all independent, < 5 if correlated)
    p_bull: float                          # Calibrated probability of bullish direction
    weights: dict[str, float]              # Final GLS weights per debater
    corr_matrix: dict[str, dict[str, float]] | None = None  # Correlation matrix (debugging)

    def __str__(self) -> str:
        return f"{self.direction.value} (score={self.score:.2f}, N_eff={self.n_eff:.1f}, p_bull={self.p_bull:.2f})"