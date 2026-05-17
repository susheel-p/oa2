"""Regime state enums and data classes."""

from dataclasses import dataclass
from enum import Enum


class VolState(Enum):
    """Volatility state: 4 buckets based on IV rank and vol metrics."""
    VOL_COMPRESSION = "vol_comp"      # iv_rank < 0.35, rv_iv < 0.80
    NORMAL = "normal"                 # iv_rank 0.35-0.65
    VOL_EXPANSION = "vol_exp"         # iv_rank > 0.65
    CRISIS = "crisis"                 # rv_iv > 1.20 or vix > 35 or iv spike


class TrendState(Enum):
    """Trend state: 2 buckets based on 20d return slope."""
    TRENDING = "trending"             # slope > 0.3% (bullish trend)
    MEAN_REVERTING = "mean_revert"    # slope < -0.3% (bearish/reverting)
    NEUTRAL_TREND = "neutral"         # slope between -0.3% and 0.3%


@dataclass
class RegimeClassification:
    """Regime classification result."""
    regime_id: int                     # 0-7: (vol_state × trend_state)
    vol_state: VolState
    trend_state: TrendState
    confidence: float                  # [0, 1] — how confident is this classification?
    posterior: dict[str, float]        # posterior probability over all 8 regimes

    # Raw signals for debugging
    iv_rank: float
    rv_iv_ratio: float
    price_slope_20d: float
    vix: float | None = None

    def __str__(self) -> str:
        return f"Regime {self.regime_id} ({self.vol_state.value}×{self.trend_state.value}, conf={self.confidence:.2f})"


# Regime ID mapping (8 regimes: 3 vol_states × 3 trend_states with VOL_EXP + NEUTRAL_TREND merged)
# Regimes 0-7 cover most combinations; regimes with 2+ signals weight heavily
REGIME_MAP = {
    # VOL_COMPRESSION (regimes 0-2)
    (VolState.VOL_COMPRESSION, TrendState.TRENDING): 0,
    (VolState.VOL_COMPRESSION, TrendState.MEAN_REVERTING): 1,
    (VolState.VOL_COMPRESSION, TrendState.NEUTRAL_TREND): 2,
    # NORMAL (regimes 3-5)
    (VolState.NORMAL, TrendState.TRENDING): 3,
    (VolState.NORMAL, TrendState.MEAN_REVERTING): 4,
    (VolState.NORMAL, TrendState.NEUTRAL_TREND): 5,
    # VOL_EXPANSION (regimes 6-7, but 6 covers both TREND and NEUTRAL_TREND)
    (VolState.VOL_EXPANSION, TrendState.TRENDING): 6,
    (VolState.VOL_EXPANSION, TrendState.MEAN_REVERTING): 7,
    (VolState.VOL_EXPANSION, TrendState.NEUTRAL_TREND): 6,  # collapse to regime 6
    # CRISIS (maps to 6-7 depending on trend)
    (VolState.CRISIS, TrendState.TRENDING): 6,
    (VolState.CRISIS, TrendState.MEAN_REVERTING): 7,
    (VolState.CRISIS, TrendState.NEUTRAL_TREND): 6,  # default to expansion+trend
}


def get_regime_id(vol_state: VolState, trend_state: TrendState) -> int:
    """Map (vol, trend) pair to regime_id [0-7]."""
    return REGIME_MAP.get((vol_state, trend_state), 5)  # default to normal+neutral