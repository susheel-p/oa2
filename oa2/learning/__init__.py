"""Learning loop — outcome tracking, knowledge base, RAG context."""

from oa2.learning.outcomes import (
    TradeOutcome,
    resolve_outcomes_from_log,
    simulate_spread_pnl,
)

__all__ = [
    "TradeOutcome",
    "resolve_outcomes_from_log",
    "simulate_spread_pnl",
]
