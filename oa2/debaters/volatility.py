"""Volatility debater — long-vega / vol-expansion perspective (quant-only, v2)."""

from __future__ import annotations

from typing import Any

from oa2.debaters.base import DebaterBase, DebaterOpinion, Direction, TradeQuality


_LONG_VEGA = {
    "LONG_CALL",
    "LONG_PUT",
    "LONG_STRADDLE",
    "LONG_STRANGLE",
    "LONG_GAMMA_SCALP",
}

_SHORT_VEGA = {
    "IRON_CONDOR",
    "SHORT_PREMIUM_FADE",
    "VERTICAL_CALL_SPREAD",
    "VERTICAL_PUT_SPREAD",
    "CALENDAR_CALL",
    "CALENDAR_PUT",
    "DIAGONAL_SPREAD",
}


class VolatilityDebater(DebaterBase):
    """Argues from volatility expansion / long-vega perspective.

    Signals:
      - Vol expansion: RV > IV, low IV rank, low VIX, skew extreme, backwardation, high VVIX
      - Vol compression: RV < IV, high IV rank, elevated VIX, contango
      - Conviction scales with signal count: 0.45 + signals × 0.12

    Penalty: 0.70 if position vega-sign misaligned with regime.
    """

    def __init__(self):
        super().__init__("volatility")

    def debate(self, context: dict[str, Any]) -> DebaterOpinion:
        """Assess vol expansion vs. compression; align position vega."""
        ticker = context.get("ticker", "UNKNOWN")

        # Extract regime
        vol_regime = context.get("vol_regime")
        iv_rank = 0.50
        rv_iv = 1.0
        skew_extreme = False
        term_upward = True
        if vol_regime:
            if isinstance(vol_regime, dict):
                iv_rank = vol_regime.get("iv_rank", 0.50)
                rv_iv = vol_regime.get("rv_iv_ratio", 1.0)
                skew_extreme = vol_regime.get("skew_extreme", False)
                term_upward = vol_regime.get("term_upward", True)
            else:
                iv_rank = getattr(vol_regime, "iv_rank", 0.50)
                rv_iv = getattr(vol_regime, "rv_iv_ratio", 1.0)
                skew_extreme = getattr(vol_regime, "skew_extreme", False)
                term_upward = getattr(vol_regime, "term_upward", True)

        # Extract chain
        chain = context.get("chain_snapshot")
        vega = 0.0
        gamma = 0.0
        theta = 0.0
        if chain:
            if isinstance(chain, dict):
                vega = chain.get("vega", 0.0)
                gamma = chain.get("gamma", 0.0)
                theta = chain.get("theta", 0.0)
            else:
                vega = getattr(chain, "vega", 0.0)
                gamma = getattr(chain, "gamma", 0.0)
                theta = getattr(chain, "theta", 0.0)

        # Extract market context
        market_vix = context.get("market_vix", 20.0)
        vvix = context.get("vvix", 80.0)

        # Extract strategy
        strategy = context.get("strategy")
        proposed_structure = None
        if strategy:
            if isinstance(strategy, dict):
                proposed_structure = strategy.get("selected_structure")
            else:
                proposed_structure = getattr(strategy, "selected_structure", None)

        trade_is_long_vega = proposed_structure in _LONG_VEGA
        trade_is_short_vega = proposed_structure in _SHORT_VEGA

        # Count vol expansion signals
        vol_expansion_signals = sum([
            rv_iv > 1.10,      # Realized > implied
            iv_rank < 0.35,    # IV cheap
            market_vix < 16,   # Systemic complacency
            skew_extreme,      # Hedging demand
            not term_upward,   # Backwardation = near-term fear
            vvix > 100,        # Vol-of-vol elevated
        ])

        # Count vol compression signals
        vol_compression_signals = sum([
            rv_iv < 0.80,      # Realized < implied
            iv_rank > 0.70,    # IV expensive
            market_vix > 25,   # Elevated VIX precedes compression
            term_upward,       # Contango = normal
        ])

        # Volatility debater is a trade-quality voter on vega alignment with
        # vol regime. It has no view on underlying *price* direction —
        # `direction` is always NEUTRAL; the real vote is `trade_quality`.
        direction = Direction.NEUTRAL

        if trade_is_long_vega and vol_expansion_signals >= 2:
            # Long vega in expansion regime — ideal
            conviction = 0.45 + vol_expansion_signals * 0.12
            trade_quality = TradeQuality.APPROVE
            reasoning = f"Vol expansion signals ({vol_expansion_signals}/6) support long vega. RV/IV {rv_iv:.2f}, IV rank {iv_rank*100:.0f}%."

        elif trade_is_short_vega and vol_expansion_signals >= 2:
            # Short vega in expansion regime — dangerous
            conviction = 0.70
            trade_quality = TradeQuality.REJECT
            reasoning = f"Vol expansion signals ({vol_expansion_signals}/6) are elevated. Selling vega here is risky."

        elif trade_is_short_vega and vol_compression_signals >= 3:
            # Short vega in compression regime — acceptable
            conviction = 0.55 + vol_compression_signals * 0.05
            trade_quality = TradeQuality.APPROVE
            reasoning = f"Vol compression signals ({vol_compression_signals}/4) support premium selling. RV/IV {rv_iv:.2f}."

        elif trade_is_long_vega and vol_compression_signals >= 3:
            # Long vega in compression regime — bad
            conviction = 0.65
            trade_quality = TradeQuality.REJECT
            reasoning = f"Vol compression regime ({vol_compression_signals}/4) opposes long vega. IV rank {iv_rank*100:.0f}% is expensive."

        else:
            # Mixed signals
            conviction = 0.35
            trade_quality = TradeQuality.ABSTAIN
            reasoning = f"Vol signals mixed ({vol_expansion_signals} expansion vs {vol_compression_signals} compression)."

        conviction = min(max(conviction, 0.0), 0.95)

        signals = {
            "iv_rank": iv_rank,
            "rv_iv_ratio": rv_iv,
            "market_vix": market_vix,
            "vvix": vvix,
            "skew_extreme": skew_extreme,
            "term_upward": term_upward,
            "vol_expansion_signals": vol_expansion_signals,
            "vol_compression_signals": vol_compression_signals,
            "vega": vega,
            "gamma": gamma,
            "theta": theta,
            "trade_is_long_vega": trade_is_long_vega,
            "trade_is_short_vega": trade_is_short_vega,
        }

        return DebaterOpinion(
            debater_name=self.name,
            direction=direction,
            conviction=round(conviction, 3),
            reasoning=reasoning,
            signals_used=signals,
            trade_quality=trade_quality,
        )