"""Regime classifier — 8-bucket vol × trend state machine.

Phase D2: Leading crisis signal added — detects early vol stress via:
    - VIX3M/VIX ratio < 1.05 (term-structure flattening)
    - VVIX > 110 (vol-of-vol elevated, hedging demand)
When both fire simultaneously, crisis is flagged one step early —
before the lagging VIX > 35 threshold would trigger.

Phase D3: Cross-asset macro context — optional inputs from:
    - TLT (bonds): falling + DXY rising = risk-off flight to dollar
    - HYG return: credit spread widening precedes equity vol 1-3 days
    - USO return: energy shock → sector-specific effects
These modify vol_state only when signals are strongly directional
(> threshold); otherwise the base iv_rank / rv_iv logic applies.
"""

from __future__ import annotations

from typing import Any

from tradingbot.regime.state import RegimeClassification, TrendState, VolState, get_regime_id

# D2: Leading crisis thresholds
_VIX_TERM_FLAT_THRESHOLD = 1.05   # VIX3M/VIX < this → term structure flattening
_VVIX_THRESHOLD = 110.0            # VVIX above this → elevated vol-of-vol

# D3: Cross-asset thresholds
_TLT_BEARISH_PCT = -0.005          # TLT daily return below this → risk-off signal
_HYG_BEARISH_PCT = -0.003          # HYG daily return below this → credit stress
_USO_SPIKE_PCT = 0.03              # USO return above this → energy shock


class RegimeClassifier:
    """Classifies market into 8 regimes: VolState × TrendState.

    Inputs:
        IV rank, RV/IV, 20d price slope, optional VIX (base signals)
        VIX3M, VVIX (Phase D2 — leading crisis)
        TLT, HYG, USO daily returns, DXY change (Phase D3 — cross-asset)
    Output:
        RegimeClassification with regime_id [0-7], confidence, posterior
    """

    def classify(self, context: dict[str, Any]) -> RegimeClassification:
        """Classify regime from market context.

        Args:
            context: dict with:
                vol_regime: {iv_rank, rv_iv_ratio, vix, vvix (D2), vix3m (D2)}
                current_price, prices_20d (trend)
                cross_asset: {tlt_ret, hyg_ret, uso_ret, dxy_chg} (D3, optional)

        Returns:
            RegimeClassification with regime_id, states, confidence, posterior
        """
        vol_regime = context.get("vol_regime", {})
        iv_rank = vol_regime.get("iv_rank", 0.50)
        rv_iv_ratio = vol_regime.get("rv_iv_ratio", 1.0)
        vix = vol_regime.get("vix", 20.0)

        # Phase D2: leading crisis check
        vvix = vol_regime.get("vvix", 0.0)
        vix3m = vol_regime.get("vix3m", 0.0)
        leading_crisis = self._leading_crisis_check(vix3m, vix, vvix)

        # Phase D3: cross-asset stress modifier
        cross_asset = context.get("cross_asset", {})
        cross_asset_stress = self._cross_asset_stress(cross_asset)

        # Compute price slope (20d trend)
        price_slope = self._compute_price_slope(context)

        # Classify vol state — leading crisis and cross-asset can escalate it
        vol_state = self._classify_vol_state(
            iv_rank, rv_iv_ratio, vix,
            leading_crisis=leading_crisis,
            cross_asset_stress=cross_asset_stress,
        )

        # Classify trend state
        trend_state = self._classify_trend_state(price_slope)

        # Compute confidence
        confidence = self._compute_confidence(iv_rank, rv_iv_ratio, price_slope, vix)

        # Compute posterior over all 8 regimes
        posterior = self._compute_posterior(vol_state, trend_state, iv_rank, rv_iv_ratio, price_slope)

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
    def _leading_crisis_check(vix3m: float, vix: float, vvix: float) -> bool:
        """Phase D2: detect early crisis before VIX hits the lagging 35 threshold.

        Fires when BOTH conditions are true:
            1. VIX3M/VIX ratio < 1.05 (term structure flattening — near-term fear > medium-term)
            2. VVIX > 110 (vol-of-vol elevated — hedging demand surging)

        When only one fires, it is informational but not enough to escalate regime.
        Returns True only when both conditions are simultaneously met.
        """
        if vix3m <= 0 or vix <= 0 or vvix <= 0:
            return False   # missing data — do not escalate
        term_ratio = vix3m / vix
        term_flat = term_ratio < _VIX_TERM_FLAT_THRESHOLD
        vvix_elevated = vvix > _VVIX_THRESHOLD
        return term_flat and vvix_elevated

    @staticmethod
    def _cross_asset_stress(cross_asset: dict[str, float]) -> float:
        """Phase D3: compute a stress score from cross-asset signals.

        Inputs (all optional, defaults to no stress if missing):
            tlt_ret:  TLT daily return (bonds)
            hyg_ret:  HYG daily return (high-yield credit)
            uso_ret:  USO daily return (crude oil)
            dxy_chg:  DXY daily change (dollar index)

        Returns a stress score [0.0, 3.0]:
            0.0 = no cross-asset stress
            1.0 = one stress signal
            2.0 = two stress signals (moderate — escalate to VOL_EXPANSION if NORMAL)
            3.0 = three or more signals (severe — escalate to CRISIS if already VOL_EXPANSION)
        """
        stress = 0.0

        tlt_ret = cross_asset.get("tlt_ret", 0.0)
        hyg_ret = cross_asset.get("hyg_ret", 0.0)
        uso_ret = cross_asset.get("uso_ret", 0.0)
        dxy_chg = cross_asset.get("dxy_chg", 0.0)

        # TLT falling + DXY rising → risk-off (flight to dollar, not bonds)
        if tlt_ret < _TLT_BEARISH_PCT and dxy_chg > 0.002:
            stress += 1.0

        # HYG spread widening (negative return) → credit stress, leading equity vol
        if hyg_ret < _HYG_BEARISH_PCT:
            stress += 1.0

        # USO spike → energy shock (separate channel, affects different sectors)
        if abs(uso_ret) > _USO_SPIKE_PCT:
            stress += 0.5   # energy shock is a sector effect, not broad market crisis

        return stress

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
    def _classify_vol_state(
        iv_rank: float,
        rv_iv_ratio: float,
        vix: float,
        leading_crisis: bool = False,
        cross_asset_stress: float = 0.0,
    ) -> VolState:
        """Classify volatility state into 4 buckets.

        Priority order (highest to lowest):
            1. Lagging crisis: rv_iv > 1.20 or vix > 35 (hard threshold)
            2. Leading crisis: D2 signals (term flat + vvix) when already VOL_EXPANSION
            3. Cross-asset stress: D3 severity escalates vol state one level
            4. Standard iv_rank tiers

        The leading crisis check (D2) escalates VOL_EXPANSION → CRISIS when
        cross_asset_stress >= 2.0 or leading_crisis is True. This catches
        developing crises before the lagging VIX > 35 threshold fires.
        """
        # Hard lagging crisis threshold (original logic — unchanged)
        if rv_iv_ratio > 1.20 or vix > 35.0:
            return VolState.CRISIS

        # Determine base state from iv_rank
        if iv_rank > 0.65:
            base_state = VolState.VOL_EXPANSION
        elif iv_rank < 0.35:
            base_state = VolState.VOL_COMPRESSION
        else:
            base_state = VolState.NORMAL

        # Phase D2: leading crisis — escalate VOL_EXPANSION → CRISIS
        if leading_crisis and base_state == VolState.VOL_EXPANSION:
            return VolState.CRISIS

        # Phase D3: cross-asset stress escalation
        if cross_asset_stress >= 2.0:
            if base_state == VolState.VOL_EXPANSION:
                return VolState.CRISIS
            elif base_state == VolState.NORMAL:
                return VolState.VOL_EXPANSION
            # VOL_COMPRESSION with severe stress → NORMAL
            elif base_state == VolState.VOL_COMPRESSION:
                return VolState.NORMAL
        elif cross_asset_stress >= 1.0:
            # One stress signal: escalate one tier
            if base_state == VolState.VOL_COMPRESSION:
                return VolState.NORMAL
            elif base_state == VolState.NORMAL:
                return VolState.VOL_EXPANSION

        return base_state

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