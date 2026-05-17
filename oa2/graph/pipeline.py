"""oa2 pipeline — plain Python orchestration of the 9-layer architecture.

Phase 0 status: skeleton only. `run()` raises NotImplementedError until
Phase 1 ports the debaters. Each phase fills in one stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from oa2.core import feature_flags


@dataclass
class PipelineContext:
    """Mutable bag passed between layers. Each stage populates its slice."""
    ticker: str
    as_of: str | None = None
    market_data: dict[str, Any] = field(default_factory=dict)
    regime: dict[str, Any] | None = None
    context_agents: dict[str, Any] = field(default_factory=dict)
    debater_opinions: list[Any] = field(default_factory=list)
    consensus: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    attribution: dict[str, Any] = field(default_factory=dict)


def run(ticker: str, as_of: str | None = None) -> PipelineContext:
    """End-to-end pipeline for one ticker.

    Layers (each gated by a feature flag; Phase 0 has none enabled):
      L0  fetch market data
      L1  regime classification
      L2  context agents (dealer, macro, event, exec)
      L3  timeframe routing
      L4  debater ensemble
      L5  consensus engine
      L6  sizing
      L7  portfolio orchestration
      L8  execution / journal write
    """
    ctx = PipelineContext(ticker=ticker, as_of=as_of)

    # L0 — market data (always on; just no-op stub for Phase 0)
    ctx.market_data = {"ticker": ticker, "stub": True}

    # L1 — regime
    if feature_flags.REGIME_CLASSIFIER_ENABLED:
        raise NotImplementedError("Phase 2: regime classifier not yet built")

    # L2 — context agents
    if feature_flags.DEALER_AGENT_ENABLED:
        raise NotImplementedError("Phase 5: dealer agent not yet built")

    # L4 — debaters
    if feature_flags.DEBATERS_ENABLED:
        raise NotImplementedError("Phase 1: debaters not yet ported")

    # L5 — consensus
    if feature_flags.CONSENSUS_ENGINE_ENABLED:
        raise NotImplementedError("Phase 3: consensus engine not yet built")

    # Phase 0: nothing decided
    ctx.decision = {"status": "phase0_scaffold_only", "ticker": ticker}
    return ctx