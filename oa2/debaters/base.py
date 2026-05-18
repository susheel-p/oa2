"""DebaterBase — abstract interface for all debaters (v2).

Each debater receives a context dict with market/setup/chain data and
returns a structured DebaterOpinion. Uniform interface for consensus engine.

All debaters are quant-only in v2 until Phase 6 (LLM debaters run async,
cached by regime×setup×ticker, not on every tick).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Direction(str, Enum):
    """Directional view on the *underlying* (BULL/BEAR/NEUTRAL).

    This is strictly a view on price direction — not a vote on whether the
    proposed trade is a good idea. See `TradeQuality` for that channel.
    """
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class TradeQuality(str, Enum):
    """Debater's verdict on the *proposed trade structure*.

    Separated from `Direction` so that a vote like "selling premium here is
    ideal" (which is a trade-quality APPROVE, not a directional BULLISH)
    does not pollute directional consensus.
    """
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ABSTAIN = "ABSTAIN"


@dataclass
class DebaterOpinion:
    """Structured output from each debater.

    Two independent channels:
      - `direction`: view on underlying price direction (consumed by GLS direction consensus)
      - `trade_quality`: verdict on the proposed structure (consumed separately, not by GLS)

    A debater that has no directional view (income/volatility judging trade
    quality only) MUST emit `direction=NEUTRAL` and put its real vote on
    `trade_quality`. Conviction applies to whichever channel is non-neutral.
    """
    debater_name: str
    direction: Direction
    conviction: float
    reasoning: str
    signals_used: dict[str, Any]
    trade_quality: TradeQuality = TradeQuality.ABSTAIN

    def signed_score(self) -> float:
        """Return conviction scaled by direction: +conviction if BULLISH, -conviction if BEARISH, 0 if NEUTRAL."""
        if self.direction == Direction.BULLISH:
            return self.conviction
        elif self.direction == Direction.BEARISH:
            return -self.conviction
        else:
            return 0.0


class DebaterBase(ABC):
    """Abstract base class for all debaters."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def debate(self, context: dict[str, Any]) -> DebaterOpinion:
        """Produce an opinion on a trade proposal.

        Args:
            context: dict with keys like:
                - ticker: str
                - current_price: float
                - vol_regime: dict or object with regime state
                - setup: dict or object with setup metadata
                - strategy: dict or object with proposed structure
                - chain_snapshot: dict or object with option greeks
                - technical signals: vwap, ema_20, ema_50, rsi, atr, etc.

        Returns:
            DebaterOpinion with direction, conviction, reasoning, signals_used.
        """
        pass