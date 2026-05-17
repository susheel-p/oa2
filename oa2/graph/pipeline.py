"""oa2 pipeline — plain Python orchestration of the 9-layer architecture.

Phase 0 status: skeleton only. `run()` raises NotImplementedError until
Phase 1 ports the debaters. Each phase fills in one stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from oa2.core import feature_flags
from oa2.debaters.runner import DebaterEnsemble


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


def run(ticker: str, as_of: str | None = None, context_dict: dict[str, Any] | None = None) -> PipelineContext:
    """End-to-end pipeline for one ticker.

    Layers (each gated by a feature flag):
      L0  fetch market data
      L1  regime classification (Phase 2)
      L2  context agents (Phase 5)
      L3  timeframe routing
      L4  debater ensemble (Phase 1)
      L5  consensus engine (Phase 3)
      L6  sizing
      L7  portfolio orchestration
      L8  execution / journal write

    Args:
        ticker: stock/ETF symbol
        as_of: optional date for backtesting
        context_dict: optional dict with pre-populated market data, regime, etc.
                      used for backtesting; real-time uses default stubs
    """
    ctx = PipelineContext(ticker=ticker, as_of=as_of)

    # L0 — market data (stub if not provided)
    if context_dict:
        ctx.market_data = context_dict.copy()
    else:
        ctx.market_data = {"ticker": ticker, "stub": True}

    # L1 — regime
    if feature_flags.REGIME_CLASSIFIER_ENABLED:
        raise NotImplementedError("Phase 2: regime classifier not yet built")

    # L2 — context agents
    if feature_flags.DEALER_AGENT_ENABLED:
        raise NotImplementedError("Phase 5: dealer agent not yet built")

    # L4 — debaters (Phase 1)
    if feature_flags.DEBATERS_ENABLED:
        ensemble = DebaterEnsemble()
        ctx.debater_opinions = ensemble.run(ctx.market_data, log_to_disk=True)
        ctx.attribution["debater_ensemble"] = ensemble.opinions_summary(ctx.debater_opinions)
    else:
        # Phase 0: placeholder
        ctx.debater_opinions = []

    # L5 — consensus
    if feature_flags.CONSENSUS_ENGINE_ENABLED:
        raise NotImplementedError("Phase 3: consensus engine not yet built")

    # Phase 0/1: no decision yet
    ctx.decision = {
        "status": "debaters_only" if ctx.debater_opinions else "scaffold_only",
        "ticker": ticker,
        "opinion_count": len(ctx.debater_opinions),
    }
    return ctx