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
    """Trade direction opinion."""
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass
class DebaterOpinion:
    """Structured output from each debater.

    Attributes:
        debater_name: e.g., "directional", "income", "volatility", "flow", "sentiment", "dealer"
        direction: BULLISH, BEARISH, or NEUTRAL
        conviction: float in [0, 1], magnitude of confidence in direction
        reasoning: short string explaining the opinion
        signals_used: dict of {signal_name: signal_value} for attribution
    """
    debater_name: str
    direction: Direction
    conviction: float
    reasoning: str
    signals_used: dict[str, Any]

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