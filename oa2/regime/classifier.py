"""Regime classifier — 8-bucket vol × trend state machine."""

from __future__ import annotations

from typing import Any

from oa2.regime.state import RegimeClassification, TrendState, VolState, get_regime_id


class RegimeClassifier:
    """Classifies market into 8 regimes: VolState × TrendState.

    Simple rule-based state machine:
    - VolState: 4 buckets (COMPRESSION, NORMAL, EXPANSION, CRISIS)
    - TrendState: 3 buckets (TRENDING, MEAN_REVERT, NEUTRAL)
    → 8 active regimes (crisis rolls into expansion)

    Inputs: IV rank, RV/IV, 20d price slope, optional VIX
    Output: RegimeClassification with regime_id [0-7], confidence, posterior
    """

    def classify(self, context: dict[str, Any]) -> RegimeClassification:
        """Classify regime from market context.

        Args:
            context: dict with vol_regime + current_price + prior_close + historical prices

        Returns:
            RegimeClassification with regime_id, states, confidence, posterior
        """
        # Extract vol regime signals
        vol_regime = context.get("vol_regime", {})
        iv_rank = vol_regime.get("iv_rank", 0.50)
        rv_iv_ratio = vol_regime.get("rv_iv_ratio", 1.0)
        vix = vol_regime.get("vix", 20.0)

        # Compute price slope (20d trend)
        price_slope = self._compute_price_slope(context)

        # Classify vol state
        vol_state = self._classify_vol_state(iv_rank, rv_iv_ratio, vix)

        # Classify trend state
        trend_state = self._classify_trend_state(price_slope)

        # Compute confidence (higher when signals are extreme)
        confidence = self._compute_confidence(iv_rank, rv_iv_ratio, price_slope, vix)

        # Compute posterior over all 8 regimes
        posterior = self._compute_posterior(vol_state, trend_state, iv_rank, rv_iv_ratio, price_slope)

        # Get regime_id
        regime_id = get_regime_id(vol_state, trend_state)

        return RegimeClassification(
            regime_id=regime_id,
            vol_state=vol_state,
            trend_state=trend_state,
            confidence=confidence,
            posterior=posterior,
            iv_rank=iv_rank,
            rv_iv_ratio=rv_iv_ratio,
            price_slope_20d=price_slope,
            vix=vix,
        )

    @staticmethod
    def _compute_price_slope(context: dict[str, Any]) -> float:
        """Compute 20-day price slope (% change, annualized direction).

        Returns slope as decimal (0.005 = 0.5% uptrend).
        If insufficient data, returns 0 (neutral).
        """
        current_price = context.get("current_price", None)
        prices_20d = context.get("prices_20d", [])

        if not current_price or len(prices_20d) < 2:
            return 0.0

        # Linear regression approximation: (recent - old) / num_days / old_price
        old_price = prices_20d[0]
        if old_price <= 0:
            return 0.0

        slope = (current_price - old_price) / len(prices_20d) / old_price
        return slope

    @staticmethod
    def _classify_vol_state(iv_rank: float, rv_iv_ratio: float, vix: float) -> VolState:
        """Classify volatility state into 4 buckets.

        Crisis: rv_iv > 1.20 or vix > 35 (overrides other states)
        Vol Expansion: iv_rank > 0.65
        Normal: iv_rank 0.35-0.65
        Vol Compression: iv_rank < 0.35
        """
        # Crisis check (overrides others)
        if rv_iv_ratio > 1.20 or vix > 35.0:
            return VolState.CRISIS

        # Vol state tiers
        if iv_rank > 0.65:
            return VolState.VOL_EXPANSION
        elif iv_rank < 0.35:
            return VolState.VOL_COMPRESSION
        else:
            return VolState.NORMAL

    @staticmethod
    def _classify_trend_state(price_slope: float) -> TrendState:
        """Classify trend state into 3 buckets.

        Trending: slope > 0.3% (strong uptrend)
        Mean Reverting: slope < -0.3% (strong downtrend)
        Neutral: between
        """
        if price_slope > 0.003:
            return TrendState.TRENDING
        elif price_slope < -0.003:
            return TrendState.MEAN_REVERTING
        else:
            return TrendState.NEUTRAL_TREND

    @staticmethod
    def _compute_confidence(
        iv_rank: float, rv_iv_ratio: float, price_slope: float, vix: float
    ) -> float:
        """Compute confidence in regime classification [0, 1].

        Higher when signals are extreme (away from neutral):
        - IV rank extremes (< 0.20 or > 0.80)
        - RV/IV extremes (< 0.70 or > 1.50)
        - Strong price slope (> 0.5% or < -0.5%)
        - High VIX (> 25 or < 12)
        """
        confidence = 0.5  # baseline

        # IV rank distance from neutral 0.50
        iv_distance = abs(iv_rank - 0.50)
        confidence += iv_distance * 0.3

        # RV/IV distance from 1.0
        rv_distance = abs(rv_iv_ratio - 1.0)
        confidence += min(rv_distance * 0.2, 0.20)

        # Price slope magnitude
        slope_magnitude = abs(price_slope)
        confidence += min(slope_magnitude * 10, 0.20)

        # VIX distance from neutral ~20
        vix_distance = abs(vix - 20.0) / 20.0
        confidence += min(vix_distance * 0.15, 0.15)

        return min(confidence, 1.0)

    @staticmethod
    def _compute_posterior(
        vol_state: VolState, trend_state: TrendState, iv_rank: float, rv_iv_ratio: float, price_slope: float
    ) -> dict[str, float]:
        """Compute posterior probability over all 8 regimes.

        Soft assignment based on distance to regime centers.
        Primary regime gets ~0.4-0.6, neighbors split remainder.
        """
        posterior = {}

        # Regime center points (iv_rank, rv_iv, slope)
        centers = {
            "VOL_COMP_TREND": (0.20, 0.80, 0.005),
            "VOL_COMP_REVERT": (0.20, 0.80, -0.005),
            "VOL_COMP_NEUTRAL": (0.20, 0.80, 0.0),
            "NORMAL_TREND": (0.50, 1.0, 0.005),
            "NORMAL_REVERT": (0.50, 1.0, -0.005),
            "NORMAL_NEUTRAL": (0.50, 1.0, 0.0),
            "VOL_EXP_TREND": (0.80, 1.20, 0.005),
            "VOL_EXP_REVERT": (0.80, 1.20, -0.005),
        }

        # Compute distance to each center, convert to probability
        distances = {}
        for name, (iv_c, rv_c, slope_c) in centers.items():
            iv_err = (iv_rank - iv_c) ** 2
            rv_err = (rv_iv_ratio - rv_c) ** 2
            slope_err = (price_slope - slope_c) ** 2
            distances[name] = (iv_err + rv_err + slope_err * 100) ** 0.5

        # Softmax: convert distances to probabilities
        min_dist = min(distances.values())
        max_dist = max(distances.values())
        range_dist = max_dist - min_dist + 1e-6

        total_weight = 0.0
        for name in centers:
            # Normalize distance to [0, 1], invert to probability
            norm_dist = (distances[name] - min_dist) / range_dist
            weight = (1.0 - norm_dist) ** 2  # quadratic penalty for distance
            distances[name] = weight
            total_weight += weight

        # Convert to probabilities
        for name in centers:
            posterior[name] = distances[name] / total_weight

        return posterior