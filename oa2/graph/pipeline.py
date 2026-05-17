"""oa2 pipeline — plain Python orchestration of the 9-layer architecture.

Phases completed:
- Phase 1: 5 debaters + JSONL logging
- Phase 2: Regime classifier (8-bucket vol × trend)
- Phase 3: Consensus engine (GLS aggregation)
- Phase 4: Thompson sampling bandit (adaptive debater weighting)
- Phase 5: Dealer agent (institutional positioning via GEX)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from oa2.consensus.engine import ConsensusEngine
from oa2.core import feature_flags
from oa2.debaters.runner import DebaterEnsemble
from oa2.regime.classifier import RegimeClassifier
from oa2.performance.bandit import BanditEngine
from oa2.dealer.agent import DealerAgent


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

    # L1 — regime classifier (Phase 2)
    if feature_flags.REGIME_CLASSIFIER_ENABLED:
        classifier = RegimeClassifier()
        ctx.regime = classifier.classify(ctx.market_data)
        ctx.attribution["regime"] = {
            "regime_id": ctx.regime.regime_id,
            "vol_state": ctx.regime.vol_state.value,
            "trend_state": ctx.regime.trend_state.value,
            "confidence": ctx.regime.confidence,
        }
    else:
        ctx.regime = None

    # L2 — dealer agent (Phase 5)
    if feature_flags.DEALER_AGENT_ENABLED or feature_flags.DEALER_SHADOW_LOG:
        dealer = DealerAgent()
        dealer_opinion = dealer.debate(ctx.market_data)
        if feature_flags.DEALER_AGENT_ENABLED:
            ctx.context_agents["dealer_opinion"] = dealer_opinion
            ctx.attribution["dealer"] = {
                "direction": dealer_opinion.direction.value,
                "conviction": dealer_opinion.conviction,
                "signals": dealer_opinion.signals_used,
            }
        else:  # shadow mode only
            ctx.attribution["dealer_shadow"] = {
                "direction": dealer_opinion.direction.value,
                "conviction": dealer_opinion.conviction,
                "signals": dealer_opinion.signals_used,
            }

    # L4 — debaters (Phase 1 + Phase 5 dealer agent)
    if feature_flags.DEBATERS_ENABLED:
        ensemble = DebaterEnsemble()
        ctx.debater_opinions = ensemble.run(ctx.market_data, log_to_disk=True)
        # Inject dealer opinion if Phase 5 enabled
        if feature_flags.DEALER_AGENT_ENABLED and "dealer_opinion" in ctx.context_agents:
            ctx.debater_opinions.append(ctx.context_agents["dealer_opinion"])
        ctx.attribution["debater_ensemble"] = ensemble.opinions_summary(ctx.debater_opinions)
    else:
        ctx.debater_opinions = []

    # L5 — consensus engine (Phase 3 + Phase 4 bandit weights)
    if feature_flags.CONSENSUS_ENGINE_ENABLED and ctx.debater_opinions:
        regime_id = ctx.regime.regime_id if ctx.regime else None

        # Phase 4: load bandit prior weights
        bandit_weights = None
        if feature_flags.BANDIT_ENABLED and regime_id is not None:
            bandit = BanditEngine.load()
            debater_names = [op.debater_name for op in ctx.debater_opinions]
            bandit_weights = bandit.get_regime_weights(
                debater_names, regime_id, use_mean=feature_flags.BANDIT_USE_POSTERIOR_MEAN
            )
            ctx.attribution["bandit_weights"] = bandit_weights

        consensus_engine = ConsensusEngine(regime=regime_id, prior_weights=bandit_weights)
        ctx.consensus = consensus_engine.aggregate(ctx.debater_opinions)
        ctx.attribution["consensus"] = {
            "direction": ctx.consensus.direction.value,
            "score": ctx.consensus.score,
            "n_eff": ctx.consensus.n_eff,
            "p_bull": ctx.consensus.p_bull,
            "weights": ctx.consensus.weights,
        }
    else:
        ctx.consensus = None

    # Determine decision status and output
    if ctx.consensus:
        status = "full_pipeline"
        direction = ctx.consensus.direction.value
        consensus_score = ctx.consensus.score
    elif ctx.debater_opinions:
        status = "debaters_only"
        direction = None
        consensus_score = None
    else:
        status = "scaffold_only"
        direction = None
        consensus_score = None

    ctx.decision = {
        "status": status,
        "ticker": ticker,
        "regime_id": ctx.regime.regime_id if ctx.regime else None,
        "opinion_count": len(ctx.debater_opinions),
        "direction": direction,
        "consensus_score": consensus_score,
    }
    return ctx