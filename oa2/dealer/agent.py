"""Dealer Agent — 6th debater that injects institutional positioning context via GEX."""

from __future__ import annotations

from typing import Any

from oa2.dealer.gex import compute_gex
from oa2.debaters.base import DebaterBase, DebaterOpinion, Direction


class DealerAgent(DebaterBase):
    """Phase 5: Institutional positioning debater using Gamma Exposure (GEX).

    Interprets dealer gamma positioning:
    - Positive GEX (long gamma): dealers dampen moves (range-bound) → NEUTRAL
    - Negative GEX (short gamma) + above flip: dealers losing on rallies → BULLISH (momentum)
    - Negative GEX (short gamma) + below flip: dealers losing on drops → BEARISH (momentum)
    """

    def __init__(self):
        super().__init__("dealer")

    def debate(self, context: dict[str, Any]) -> DebaterOpinion:
        """Generate opinion from GEX analysis.

        Args:
            context: market data dict with "options_chain" and "current_price"

        Returns:
            DebaterOpinion with direction and conviction based on dealer positioning
        """
        chain = context.get("options_chain")
        spot_price = context.get("current_price", 0.0)

        if not chain or not spot_price:
            return DebaterOpinion(
                debater_name="dealer",
                direction=Direction.NEUTRAL,
                conviction=0.0,
                reasoning="No options chain available",
                signals_used={},
            )

        gex = compute_gex(chain, spot_price)
        direction, conviction, reasoning = self._interpret(gex)

        return DebaterOpinion(
            debater_name="dealer",
            direction=direction,
            conviction=conviction,
            reasoning=reasoning,
            signals_used={
                "gex": gex.net_gex,
                "gamma_flip": gex.gamma_flip,
                "gex_positive": gex.is_positive_gex,
                "price_vs_flip": gex.price_vs_flip,
            },
        )

    @staticmethod
    def _interpret(gex_result) -> tuple[str, float, str]:
        """Interpret GEX result into direction and conviction.

        Returns:
            (direction, conviction, reasoning)
        """
        gex = gex_result.net_gex
        is_positive = gex_result.is_positive_gex
        price_vs_flip = gex_result.price_vs_flip
        flip = gex_result.gamma_flip

        # Threshold for "significant" GEX (in millions of dollars)
        GEX_THRESHOLD = 500e6

        # Case 1: Positive GEX (dealers long gamma) → NEUTRAL
        if is_positive:
            abs_gex = abs(gex)
            conviction = min(abs_gex / GEX_THRESHOLD, 0.85)
            reasoning = f"Dealers long gamma (${gex/1e6:.0f}M) → dampen moves, range-bound"
            return Direction.NEUTRAL.value, conviction, reasoning

        # Case 2: Negative GEX (dealers short gamma) — direction depends on flip
        abs_gex = abs(gex)
        conviction = min(abs_gex / GEX_THRESHOLD, 0.85)

        if price_vs_flip == "above" and flip is not None:
            # Above flip: dealers short gamma get hurt on up moves
            reasoning = f"Dealers short gamma (${gex/1e6:.0f}M), price ${price_vs_flip} flip (${flip:.0f}) → bullish momentum"
            return Direction.BULLISH.value, conviction, reasoning

        elif price_vs_flip == "below" and flip is not None:
            # Below flip: dealers short gamma get hurt on down moves
            reasoning = f"Dealers short gamma (${gex/1e6:.0f}M), price ${price_vs_flip} flip (${flip:.0f}) → bearish momentum"
            return Direction.BEARISH.value, conviction, reasoning

        else:
            # No clear flip or unknown position
            reasoning = "Unclear dealer positioning (no gamma flip)"
            return Direction.NEUTRAL.value, 0.3, reasoning