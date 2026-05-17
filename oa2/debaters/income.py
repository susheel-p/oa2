"""Income debater — premium-selling / theta perspective (quant-only, v2)."""

from __future__ import annotations

from typing import Any

from oa2.debaters.base import DebaterBase, DebaterOpinion, Direction


_PREMIUM_SELLERS = {
    "IRON_CONDOR",
    "SHORT_PREMIUM_FADE",
    "VERTICAL_CALL_SPREAD",
    "VERTICAL_PUT_SPREAD",
    "CALENDAR_CALL",
    "CALENDAR_PUT",
    "DIAGONAL_SPREAD",
}

_PREMIUM_BUYERS = {
    "LONG_CALL",
    "LONG_PUT",
    "LONG_STRADDLE",
    "LONG_STRANGLE",
    "LONG_GAMMA_SCALP",
}


class IncomeDebater(DebaterBase):
    """Argues from premium-selling / theta-collection perspective.

    Signals:
      - IV rank (0-1 scale: >0.60 = expensive, <0.35 = cheap)
      - RV/IV ratio (>1.10 = realized vol exceeds implied)
      - Theta per contract
      - Vega exposure

    Conviction ranges 0.30–0.95 based on IV extremes and position type.
    """

    def __init__(self):
        super().__init__("income")

    def debate(self, context: dict[str, Any]) -> DebaterOpinion:
        """Assess premium-selling edge: is IV rich or poor relative to RV?"""
        ticker = context.get("ticker", "UNKNOWN")

        # Extract regime data
        vol_regime = context.get("vol_regime")
        iv_rank = 0.50
        rv_iv = 1.0
        if vol_regime:
            if isinstance(vol_regime, dict):
                iv_rank = vol_regime.get("iv_rank", 0.50)
                rv_iv = vol_regime.get("rv_iv_ratio", 1.0)
            else:
                iv_rank = getattr(vol_regime, "iv_rank", 0.50)
                rv_iv = getattr(vol_regime, "rv_iv_ratio", 1.0)

        # Extract chain data
        chain = context.get("chain_snapshot")
        theta = 0.0
        vega = 0.0
        if chain:
            if isinstance(chain, dict):
                theta = chain.get("theta", 0.0)
                vega = chain.get("vega", 0.0)
            else:
                theta = getattr(chain, "theta", 0.0)
                vega = getattr(chain, "vega", 0.0)

        # Extract strategy
        strategy = context.get("strategy")
        proposed_structure = None
        if strategy:
            if isinstance(strategy, dict):
                proposed_structure = strategy.get("selected_structure")
            else:
                proposed_structure = getattr(strategy, "selected_structure", None)

        # Classify trade
        trade_sells_premium = proposed_structure in _PREMIUM_SELLERS
        trade_buys_premium = proposed_structure in _PREMIUM_BUYERS
        iv_is_expensive = iv_rank > 0.60
        iv_is_cheap = iv_rank < 0.35
        rv_exceeds_iv = rv_iv > 1.10

        # Decision logic
        if trade_sells_premium and iv_is_expensive and not rv_exceeds_iv:
            # Ideal: selling rich premium in quiet vol regime
            conviction = 0.65 + (iv_rank - 0.60) * 0.5
            direction = Direction.BULLISH
            reasoning = f"IV Rank {iv_rank*100:.0f}% is expensive. Selling premium in quiet regime is ideal. {proposed_structure} earns theta."

        elif trade_buys_premium and iv_is_expensive:
            # Worst: buying expensive premium
            conviction = 0.70
            direction = Direction.BEARISH
            reasoning = f"IV Rank {iv_rank*100:.0f}% is expensive. Buying premium at high IV rank means overpaying for decay."

        elif trade_sells_premium and rv_exceeds_iv:
            # Dangerous: selling when actual moves exceed implied
            conviction = 0.65
            direction = Direction.BEARISH
            reasoning = f"RV/IV {rv_iv:.2f} — realized moves exceeding implied. Selling premium here is risky."

        elif trade_buys_premium and iv_is_cheap and rv_exceeds_iv:
            # Favorable for vol buyer; income trader admits it
            conviction = 0.30
            direction = Direction.BULLISH
            reasoning = f"IV Rank {iv_rank*100:.0f}% is cheap. Buying premium is justified if RV stays elevated."

        else:
            # Moderate / mixed
            conviction = 0.40
            direction = Direction.NEUTRAL
            reasoning = f"IV Rank {iv_rank*100:.0f}%, RV/IV {rv_iv:.2f} — no strong income edge."

        conviction = min(max(conviction, 0.30), 0.95)

        signals = {
            "iv_rank": iv_rank,
            "rv_iv_ratio": rv_iv,
            "iv_is_expensive": iv_is_expensive,
            "iv_is_cheap": iv_is_cheap,
            "rv_exceeds_iv": rv_exceeds_iv,
            "theta": theta,
            "vega": vega,
            "trade_sells_premium": trade_sells_premium,
            "trade_buys_premium": trade_buys_premium,
        }

        return DebaterOpinion(
            debater_name=self.name,
            direction=direction,
            conviction=round(conviction, 3),
            reasoning=reasoning,
            signals_used=signals,
        )