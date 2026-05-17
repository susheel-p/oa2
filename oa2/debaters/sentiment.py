"""Sentiment debater — crowd/news sentiment perspective (quant-only, v2)."""

from __future__ import annotations

from typing import Any

from oa2.debaters.base import DebaterBase, DebaterOpinion, Direction


_BULLISH_STRUCTURES = {
    "LONG_CALL",
    "LONG_STRADDLE",
    "LONG_STRANGLE",
    "LONG_GAMMA_SCALP",
    "VERTICAL_CALL_SPREAD",
    "DIAGONAL_SPREAD",
}

_BEARISH_STRUCTURES = {
    "LONG_PUT",
    "VERTICAL_PUT_SPREAD",
}

_NEUTRAL_STRUCTURES = {
    "IRON_CONDOR",
    "SHORT_PREMIUM_FADE",
    "CALENDAR_CALL",
    "CALENDAR_PUT",
}


class SentimentDebater(DebaterBase):
    """Argues based on crowd/news sentiment alignment with proposed trade.

    Signals:
      - Composite sentiment score (−1.0 to +1.0)
      - Mention count (strength of signal)
      - Source diversity (Reddit, StockTwits, moomoo, yfinance)

    Conviction: 0.45 + |score| × 0.40, discounted 0.75× if mention_count < 10.
    Baseline on timeout: 0.20 (neutral, not zero).
    """

    def __init__(self):
        super().__init__("sentiment")

    def debate(self, context: dict[str, Any]) -> DebaterOpinion:
        """Assess sentiment alignment with proposed trade direction."""
        ticker = context.get("ticker", "UNKNOWN")

        # Extract sentiment data (pre-fetched in context; real-time fetch deferred to async layer)
        sentiment_data = context.get("sentiment_snapshot")

        # If no sentiment data available, return neutral baseline
        if not sentiment_data:
            return DebaterOpinion(
                debater_name=self.name,
                direction=Direction.NEUTRAL,
                conviction=0.20,
                reasoning="No sentiment data available.",
                signals_used={"error": "missing_sentiment_snapshot"},
            )

        # Extract sentiment metrics
        composite_score = 0.0
        mention_count = 0
        data_sources = []

        if isinstance(sentiment_data, dict):
            composite_score = sentiment_data.get("composite_score", 0.0)
            mention_count = sentiment_data.get("mention_count", 0)
            data_sources = sentiment_data.get("data_sources", [])
        else:
            composite_score = getattr(sentiment_data, "composite_score", 0.0)
            mention_count = getattr(sentiment_data, "mention_count", 0)
            data_sources = getattr(sentiment_data, "data_sources", [])

        # Extract proposed structure
        strategy = context.get("strategy")
        proposed_structure = None
        if strategy:
            if isinstance(strategy, dict):
                proposed_structure = strategy.get("selected_structure")
            else:
                proposed_structure = getattr(strategy, "selected_structure", None)

        # Compute base conviction
        conviction_base = 0.45 + abs(composite_score) * 0.40
        if mention_count < 10:
            conviction_base = max(conviction_base * 0.85, 0.25)
        conviction_base = min(conviction_base, 0.90)

        # Determine direction and alignment
        if composite_score > 0.30:
            sentiment_direction = Direction.BULLISH
            if proposed_structure in _BULLISH_STRUCTURES:
                conviction = conviction_base
                reasoning = f"Crowd bullish ({composite_score:+.2f}). {proposed_structure} aligns with sentiment."
            elif proposed_structure in _BEARISH_STRUCTURES:
                conviction = conviction_base * 0.75
                reasoning = f"Crowd bullish but {proposed_structure} is bearish. Sentiment mismatch."
            else:
                conviction = 0.40
                reasoning = f"Crowd bullish. {proposed_structure or 'Trade'} is neutral-directional."

        elif composite_score < -0.30:
            sentiment_direction = Direction.BEARISH
            if proposed_structure in _BEARISH_STRUCTURES:
                conviction = conviction_base
                reasoning = f"Crowd bearish ({composite_score:+.2f}). {proposed_structure} aligns with sentiment."
            elif proposed_structure in _BULLISH_STRUCTURES:
                conviction = conviction_base * 0.75
                reasoning = f"Crowd bearish but {proposed_structure} is bullish. Sentiment mismatch."
            else:
                conviction = 0.40
                reasoning = f"Crowd bearish. {proposed_structure or 'Trade'} is neutral-directional."

        else:
            sentiment_direction = Direction.NEUTRAL
            conviction = 0.35
            reasoning = "Crowd sentiment is neutral; no directional bias."

        signals = {
            "composite_score": composite_score,
            "mention_count": mention_count,
            "data_sources": data_sources,
            "proposed_structure": proposed_structure,
            "sentiment_direction": sentiment_direction.value,
            "sources_count": len(data_sources),
        }

        return DebaterOpinion(
            debater_name=self.name,
            direction=sentiment_direction,
            conviction=round(conviction, 3),
            reasoning=reasoning,
            signals_used=signals,
        )