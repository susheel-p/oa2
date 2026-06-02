"""oa2 pipeline — plain Python orchestration of the 9-layer architecture.

Phases wired:
- Phase 1:   5 debaters + JSONL logging
- Phase 2:   Regime classifier (8-bucket vol × trend)
- Phase 3:   Consensus engine (GLS aggregation)
- Phase 4:   Thompson sampling bandit (adaptive debater weighting)
- Phase 5:   Dealer agent (institutional positioning via GEX)
- Phase B:   Sizing engine — Kelly (B1/B4) + Greeks limits (B2) + CVaR (B3)
- Phase C:   Exit engine — exit rule tagging + open-position exit alerts
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from tradingbot.consensus.calibration import Calibrator, default_calibrator_path
from tradingbot.consensus.engine import ConsensusEngine
from tradingbot.core import feature_flags
from tradingbot.core.clock import Clock, SystemClock
from tradingbot.core.logging_util import PipelineLogger, get_detail_logging_enabled
from tradingbot.dealer.agent import DealerAgent
from tradingbot.debaters.runner import DebaterEnsemble
from tradingbot.execution.exit import ExitEngine
from tradingbot.execution.monitor import ChainProvider, PositionMonitor
from tradingbot.learning.knowledge_base import default_kb_path, KnowledgeBase
from tradingbot.performance.bandit import BanditEngine
from tradingbot.regime.classifier import RegimeClassifier
from tradingbot.regime.session import get_session_state, session_weight_multipliers
from tradingbot.sizing.cvar import CVaRChecker
from tradingbot.sizing.kelly import size_from_consensus
from tradingbot.sizing.limits import GreeksBook
from tradingbot.sizing.mc_cvar import MonteCarloCVaR

_DEFAULT_ACCOUNT_SIZE = float(os.getenv("ACCOUNT_SIZE", "50000"))


# =============================================================================
# Pipeline context
# =============================================================================

@dataclass
class PipelineContext:
    """Mutable bag passed between layers. Each stage populates its slice."""
    ticker: str
    as_of: str | None = None
    market_data: dict[str, Any] = field(default_factory=dict)
    regime: Any | None = None
    context_agents: dict[str, Any] = field(default_factory=dict)
    debater_opinions: list[Any] = field(default_factory=list)
    consensus: Any | None = None
    sizing: dict[str, Any] | None = None
    exit_rules: dict[str, Any] | None = None
    open_position_exits: list[dict[str, Any]] = field(default_factory=list)
    decision: dict[str, Any] | None = None
    attribution: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Main entry point
# =============================================================================

def run(
    ticker: str,
    as_of: str | None = None,
    context_dict: dict[str, Any] | None = None,
    account_size: float = _DEFAULT_ACCOUNT_SIZE,
    book: GreeksBook | None = None,
    monitor: PositionMonitor | None = None,
    clock: Clock | None = None,
    chain_provider: ChainProvider | None = None,
    calibrator: Calibrator | None = None,
    premarket_mode: bool = False,
) -> PipelineContext:
    """End-to-end pipeline for one ticker.

    Layers (each gated by a feature flag):
      L0  fetch market data
      L1  regime classification             (TRADINGBOT_FLAG_REGIME)
      L2  context agents / dealer           (TRADINGBOT_FLAG_DEALER)
      L3  timeframe routing                 (implicit in pipeline order)
      L4  debater ensemble                  (TRADINGBOT_FLAG_DEBATERS)
      L5  consensus engine + bandit         (TRADINGBOT_FLAG_CONSENSUS, TRADINGBOT_FLAG_BANDIT)
      L6  sizing: Kelly + Greeks limits + CVaR  (TRADINGBOT_FLAG_SIZING)
      L7  portfolio: book Greeks state      (TRADINGBOT_FLAG_SIZING)
      L8  exit engine: tag + open-position alerts  (TRADINGBOT_FLAG_EXIT)

    Args:
        ticker:       Stock/ETF symbol.
        as_of:        Optional date string for backtesting.
        context_dict: Optional pre-populated market data (backtest / unit tests).
                      Real-time mode uses a stub dict.
        account_size: Total account equity in dollars (default $50,000).
        book:         GreeksBook tracking open-position Greeks across the full
                      book.  If None, a fresh empty book is created (no existing
                      exposure — safe for single-run calls).
        monitor:      PositionMonitor tracking open positions.  When provided,
                      the exit engine is run against all open positions for this
                      ticker and alerts are included in the decision.

    Returns:
        PipelineContext with decision, sizing, exit_rules, attribution, and
        (optionally) open_position_exits populated.
    """
    ctx = PipelineContext(ticker=ticker, as_of=as_of)

    # Initialize logger
    detail_logging = get_detail_logging_enabled()
    logger = PipelineLogger(detail_logging=detail_logging, ticker=ticker)
    logger.log_stage("L0", f"Pipeline starting (account=${account_size:,.0f})")

    if book is None:
        book = GreeksBook(account_size=account_size)
    if clock is None:
        clock = SystemClock()
    if calibrator is None:
        try:
            cal_path = default_calibrator_path()
            calibrator = Calibrator.load(cal_path)
        except Exception as e:
            logger.log_warning(
                f"Failed to load calibrator from {cal_path}: {e}",
                "Falling back to identity mode"
            )
            calibrator = Calibrator()

    # Warn if calibrator is uncalibrated or has low sample count
    if calibrator.state.mode == "identity":
        logger.log_warning(
            "Calibrator in IDENTITY mode (uncalibrated)",
            "Using raw debater probabilities. Run scripts/fit_calibrator.py or scripts/daily_learn.py to train."
        )
    elif calibrator.state.n_samples < 50:
        logger.log_warning(
            "Calibrator has low sample count",
            f"Only {calibrator.state.n_samples} samples (min 50). Mode={calibrator.state.mode}. Accuracy may be degraded."
        )

    # ------------------------------------------------------------------
    # L0 — market data
    # ------------------------------------------------------------------
    # C13 fix: when no context_dict is injected (live mode), actually fetch
    # via the dataflows cache instead of running with a stub. The cache
    # internally gates moomoo to live dates and falls back to yfinance for
    # historical replay (see dataflows/cache.py C1 gating).
    if context_dict:
        ctx.market_data = context_dict.copy()
        logger.log_detail("Using injected context (backtest/test mode)", {})
    else:
        try:
            import datetime as _dt
            from tradingbot.dataflows.cache import fetch_with_cache
            fetch_date = as_of or _dt.date.today().isoformat()
            logger.log_detail("Fetching live market data", {"date": fetch_date})
            ctx.market_data = fetch_with_cache(ticker, fetch_date)
            quote = ctx.market_data.get("quote", {})
            logger.log_detail("Market snapshot received", {
                "price": quote.get("last_price"),
                "bid": quote.get("bid"),
                "ask": quote.get("ask"),
            })
        except Exception as e:
            import warnings as _warnings
            _warnings.warn(f"L0 fetch failed for {ticker} ({e}); pipeline running with stub context")
            logger.log_warning("L0 fetch failed", str(e))
            ctx.market_data = {"ticker": ticker, "stub": True, "fetch_error": str(e)}

    # Extract recommended expiration from snapshot
    recommended_expiry = ctx.market_data.get("recommended_expiry")
    if recommended_expiry:
        ctx.market_data["_recommended_expiry"] = recommended_expiry
        logger.log_detail("Expiry recommendation", {"recommended": recommended_expiry})
    else:
        logger.log_detail("No expiry recommendation available", {})

    # ------------------------------------------------------------------
    # L0b — fetch flow data for flow debater (new)
    # ------------------------------------------------------------------
    # Wire moomoo or other adapters to populate real flow signals for FlowDebater.
    # If unavailable, flow_data will be "absent" and FlowDebater abstains cleanly.
    try:
        from tradingbot.dataflows.flow_adapter import auto_adapter
        flow_adapter = auto_adapter()
        logger.log_detail("Fetching flow data", {"adapter": flow_adapter.name})
        flow_data = flow_adapter.fetch(ticker, date=as_of)
        ctx.market_data["flow_data"] = flow_data

        data_quality = flow_data.get("data_quality", "absent")
        logger.log_detail("Flow data received", {
            "quality": data_quality,
            "pcr": flow_data.get("put_call_ratio"),
            "unusual_call_vol": flow_data.get("unusual_call_vol"),
            "unusual_put_vol": flow_data.get("unusual_put_vol"),
        })
    except Exception as e:
        import warnings as _warnings
        _warnings.warn(f"L0b flow data fetch failed: {e}; FlowDebater will abstain")
        logger.log_warning("L0b flow data fetch failed", str(e))
        ctx.market_data["flow_data"] = {"data_quality": "absent"}

    # ------------------------------------------------------------------
    # L1 — regime classifier
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # L2 — dealer agent (context + optional 6th debater)
    # ------------------------------------------------------------------
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
        else:
            ctx.attribution["dealer_shadow"] = {
                "direction": dealer_opinion.direction.value,
                "conviction": dealer_opinion.conviction,
                "signals": dealer_opinion.signals_used,
            }

    # ------------------------------------------------------------------
    # L4 — debater ensemble
    # ------------------------------------------------------------------
    if feature_flags.DEBATERS_ENABLED:
        logger.log_stage("L4", "Running debater ensemble (6 debaters)")
        ensemble = DebaterEnsemble()
        ctx.debater_opinions = ensemble.run(ctx.market_data, log_to_disk=True)
        if feature_flags.DEALER_AGENT_ENABLED and "dealer_opinion" in ctx.context_agents:
            ctx.debater_opinions.append(ctx.context_agents["dealer_opinion"])
        ctx.attribution["debater_ensemble"] = ensemble.opinions_summary(ctx.debater_opinions)

        # Log each debater's opinion
        for opinion in ctx.debater_opinions:
            abstained = opinion.conviction == 0.0
            if abstained:
                logger.log_signal(opinion.debater_name, "ABSTAINED", {})
            else:
                logger.log_signal(opinion.debater_name, f"{opinion.direction.value}", {
                    "conviction": round(opinion.conviction, 3),
                })
    else:
        ctx.debater_opinions = []

    # ------------------------------------------------------------------
    # L5 — consensus engine + bandit weights
    # ------------------------------------------------------------------
    if feature_flags.CONSENSUS_ENGINE_ENABLED and ctx.debater_opinions:
        logger.log_stage("L5", "Aggregating consensus (GLS engine)")
        regime_id = ctx.regime.regime_id if ctx.regime else None

        bandit_weights = None
        if feature_flags.BANDIT_ENABLED and regime_id is not None:
            bandit = BanditEngine.load()
            debater_names = [op.debater_name for op in ctx.debater_opinions]
            bandit_weights = bandit.get_regime_weights(
                debater_names, regime_id,
                use_mean=feature_flags.BANDIT_USE_POSTERIOR_MEAN,
            )
            ctx.attribution["bandit_weights"] = bandit_weights
            logger.log_detail("Bandit weights applied", {}, "L5")

        # P1.2: session-state weight multipliers (intraday context).
        # Pipeline clock is the source of truth.
        try:
            session_dt = clock.now_et()
        except Exception:
            session_dt = None
        session_state = get_session_state(session_dt)
        session_mults = session_weight_multipliers(session_state)
        ctx.attribution["session_state"] = session_state.value
        ctx.attribution["session_multipliers"] = session_mults

        # Combine bandit + session multipliers into prior_weights for the engine.
        prior_weights = None
        if bandit_weights or session_mults:
            debater_names = [op.debater_name for op in ctx.debater_opinions]
            prior_weights = {}
            for name in debater_names:
                b = (bandit_weights or {}).get(name, 1.0)
                s = session_mults.get(name, 1.0)
                prior_weights[name] = b * s

        consensus_engine = ConsensusEngine(regime=regime_id, prior_weights=prior_weights)
        ctx.consensus = consensus_engine.aggregate(ctx.debater_opinions)

        # P0#2: calibrate p_bull before it reaches Kelly. Identity mode when
        # untrained — see oa2/consensus/calibration.py.
        raw_p_bull = ctx.consensus.p_bull
        calibrated_p_bull = calibrator.transform(raw_p_bull)
        ctx.consensus.p_bull = calibrated_p_bull

        logger.log_consensus(
            ctx.consensus.direction.value,
            calibrated_p_bull,
            ctx.consensus.n_eff,
            ctx.consensus.weights
        )
        logger.log_detail("Calibration applied", {
            "raw_p_bull": round(raw_p_bull, 3),
            "calibrated_p_bull": round(calibrated_p_bull, 3),
            "calibrator_mode": calibrator.state.mode,
        }, "L5")

        ctx.attribution["consensus"] = {
            "direction": ctx.consensus.direction.value,
            "score": ctx.consensus.score,
            "n_eff": ctx.consensus.n_eff,
            "p_bull": calibrated_p_bull,
            "p_bull_raw": raw_p_bull,
            "calibrator_mode": calibrator.state.mode,
            "calibrator_n_samples": calibrator.state.n_samples,
            "weights": ctx.consensus.weights,
        }
    else:
        ctx.consensus = None

    # ------------------------------------------------------------------
    # L5b — structure picker: pick real strikes from chain, compute R:R
    # Skipped in premarket_mode — options chains have no live bid/ask before open
    # ------------------------------------------------------------------
    if premarket_mode:
        ctx.decision = _build_decision(ctx, ticker)
        return ctx

    if ctx.consensus is not None and ctx.consensus.direction.value in ("BULLISH", "BEARISH"):
        try:
            from tradingbot.strategy import pick_structure

            # Get chain for recommended expiry, fall back to default chain
            recommended_expiry = ctx.market_data.get("_recommended_expiry")
            chains_by_expiry = ctx.market_data.get("chains_by_expiry", {})

            if recommended_expiry and recommended_expiry in chains_by_expiry:
                chain = chains_by_expiry[recommended_expiry]
                logger.log_detail("Using recommended expiry for structure pick", {
                    "expiry": recommended_expiry
                })
            else:
                chain = ctx.market_data.get("_options_chain")
                logger.log_detail("Using default options chain (no expiry recommendation)", {})

            spot = float(ctx.market_data.get("current_price") or ctx.market_data.get("price") or 0.0)
            iv_rank = ctx.market_data.get("iv_rank")
            pick = pick_structure(
                chain=chain,
                spot=spot,
                direction=ctx.consensus.direction.value,
                p_bull=ctx.consensus.p_bull,
                iv_rank=iv_rank,
                expiry=recommended_expiry,
            )
            if pick is not None:
                ctx.market_data["max_profit"] = pick.max_profit
                ctx.market_data["max_loss"] = pick.max_loss
                ctx.market_data["dte"] = pick.dte
                ctx.market_data["delta_per_contract"] = pick.delta_per_contract
                ctx.market_data["vega_per_contract"] = pick.vega_per_contract
                ctx.market_data["theta_per_contract"] = pick.theta_per_contract
                ctx.market_data["selected_structure"] = pick.structure_type
                ctx.attribution["structure_pick"] = {
                    "structure": pick.structure_type,
                    "long_strike": pick.long_strike,
                    "short_strike": pick.short_strike,
                    "expiry": pick.expiry or (chain.get("expiry") if isinstance(chain, dict) else None),
                    "max_profit": pick.max_profit,
                    "max_loss": pick.max_loss,
                    "odds": pick.odds,
                    "breakeven": pick.breakeven,
                    "reason": pick.reason,
                }
                logger.log_detail("Structure picked", {
                    "structure": pick.structure_type,
                    "odds": pick.odds,
                    "max_profit": pick.max_profit,
                    "max_loss": pick.max_loss,
                }, "L5b")
            else:
                ctx.attribution["structure_pick"] = {"status": "no_viable_structure"}
                logger.log_detail("No viable structure found", {
                    "p_bull": ctx.consensus.p_bull,
                    "direction": ctx.consensus.direction.value,
                }, "L5b")
        except Exception as e:
            ctx.attribution["structure_pick"] = {"status": "no_viable_structure", "error": str(e)}
            logger.log_warning("Structure picker failed", str(e))

    # Fallback: ensure structure_pick is set if consensus exists but direction is unsupported
    if ctx.consensus is not None and "structure_pick" not in ctx.attribution:
        ctx.attribution["structure_pick"] = {"status": "no_viable_structure"}

    # ------------------------------------------------------------------
    # L6 — sizing engine (Kelly + book limits + CVaR)
    # ------------------------------------------------------------------
    # Guard: reject if structure picking failed (no viable option structures found)
    structure_pick_status = ctx.attribution.get("structure_pick", {}).get("status")
    if structure_pick_status == "no_viable_structure":
        ctx.sizing = {
            "approved": False,
            "reject_gate": "structure",
            "reject_reason": "No viable option structure found for current market conditions",
            "contracts": 0,
        }
        logger.log_sizing("REJECTED", "structure gate", {"reason": "No viable option structure"})
    elif feature_flags.SIZING_ENGINE_ENABLED and ctx.consensus is not None:
        logger.log_stage("L6", "Running sizing gates (Kelly → Greeks → Scenario → MC CVaR)")
        ctx.sizing = _run_sizing(ctx, book, account_size)
        ctx.attribution["sizing"] = ctx.sizing

        if ctx.sizing.get("approved"):
            logger.log_sizing("APPROVED", data={
                "contracts": ctx.sizing.get("contracts"),
                "kelly_f": ctx.sizing.get("kelly", {}).get("kelly_f"),
                "risk_dollars": ctx.sizing.get("max_dollars_at_risk"),
            })
        else:
            reason = ctx.sizing.get("reject_reason", "unknown")
            gate = ctx.sizing.get("reject_gate", "unknown")
            logger.log_sizing("REJECTED", f"Gate {gate}", {"reason": reason})

    # ------------------------------------------------------------------
    # L7 — portfolio: book state summary
    # ------------------------------------------------------------------
    if feature_flags.SIZING_ENGINE_ENABLED:
        ctx.attribution["book_state"] = book.summary()

    # ------------------------------------------------------------------
    # L8 — exit engine: tag trade + check open positions
    # ------------------------------------------------------------------
    if feature_flags.EXIT_ENGINE_ENABLED:
        if ctx.sizing and ctx.sizing.get("approved"):
            ctx.exit_rules = _build_exit_rules(ctx)
            ctx.attribution["exit_rules"] = ctx.exit_rules

        if monitor is not None:
            # P0#4: re-mark Greeks against the live chain before exit checks,
            # so stop / DTE / regime-flip rules read fresh exposures, not
            # entry-time snapshots.
            if chain_provider is not None:
                try:
                    skipped = monitor.remark_greeks(chain_provider)
                    if skipped:
                        ctx.attribution["remark_skipped_trade_ids"] = skipped
                except Exception as e:
                    ctx.attribution["remark_error"] = str(e)
            alerts = _check_open_positions(ctx, monitor, clock=clock)
            ctx.open_position_exits = alerts
            if alerts:
                ctx.attribution["open_position_exit_alerts"] = alerts

    # ------------------------------------------------------------------
    # Assemble decision
    # ------------------------------------------------------------------
    ctx.decision = _build_decision(ctx, ticker)
    return ctx


# =============================================================================
# L6 helper — sizing
# =============================================================================

def _run_sizing(
    ctx: PipelineContext,
    book: GreeksBook,
    account_size: float,
) -> dict[str, Any]:
    """Run the three-gate sizing pipeline for the proposed trade.

    Reads trade parameters from market_data; falls back to sensible stub
    defaults when running without real chain data.

    Gates (all must pass):
        B1/B4  Kelly — positive edge, DTE-aware scaling
        B2     GreeksBook — no hard-cap breach after proposed trade
        B3     CVaR — no stress scenario exceeds 5% of account

    Returns a dict describing the outcome and diagnostics for attribution.
    """
    md = ctx.market_data
    consensus = ctx.consensus

    price = float(md.get("price", 100.0))
    max_profit = float(md.get("max_profit", 200.0))
    max_loss = float(md.get("max_loss", 300.0))
    dte = int(md.get("dte", 30))
    delta_per = float(md.get("delta_per_contract", 15.0))
    vega_per = float(md.get("vega_per_contract", 8.0))
    theta_per = float(md.get("theta_per_contract", -5.0))

    direction = consensus.direction.value
    p_bull = consensus.p_bull

    # --- Phase A1: directional bias filter ---
    # 12-month backtest (4510 days): BULLISH 49.2% vs BEARISH 42.0% accuracy.
    # Bearish signals are systematically losing (1,254 trades, -$160 simulated on $1k).
    # Block BEARISH entirely until B-phase root cause is fixed.
    BEARISH_ENABLED = bool(int(os.getenv("TRADINGBOT_FLAG_BEARISH", "0")))
    if direction == "BEARISH" and not BEARISH_ENABLED:
        return {
            "approved": False,
            "reject_gate": "directional_bias",
            "reject_reason": "BEARISH signals disabled (42% historical accuracy, set TRADINGBOT_FLAG_BEARISH=1 to re-enable)",
            "contracts": 0,
        }

    # --- Gate B0: quality gates (KB-driven ticker blacklist + mean-revert regime) ---
    from tradingbot.strategy.quality_gates import (
        check_quality_gates,
        ticker_conviction_multiplier,
        regime_conviction_multiplier,
    )
    regime_label = None
    if ctx.regime is not None:
        try:
            regime_label = f"{ctx.regime.vol_state.value}_{ctx.regime.trend_state.value}".lower()
        except Exception:
            regime_label = None
    q_passed, q_reason = check_quality_gates(ctx.ticker, regime_label)
    if not q_passed:
        return {
            "approved": False,
            "reject_gate": "quality",
            "reject_reason": q_reason,
            "contracts": 0,
        }

    # Phase 3 RAG: combined ticker + regime multiplier (KB-driven when available).
    # Both capped to [0.5, 1.3] inside KB; final product also clipped for safety.
    t_mult = ticker_conviction_multiplier(ctx.ticker)
    r_mult = regime_conviction_multiplier(regime_label)
    q_mult = max(0.40, min(1.50, t_mult * r_mult))
    if direction == "BULLISH":
        p_bull_adj = 0.5 + (p_bull - 0.5) * q_mult
    elif direction == "BEARISH":
        p_bull_adj = 0.5 - (0.5 - p_bull) * q_mult
    else:
        p_bull_adj = p_bull
    p_bull_adj = max(0.0, min(1.0, p_bull_adj))

    # Load KB for attribution logging
    kb_available = False
    kb_metadata = {}
    try:
        kb = KnowledgeBase.load(default_kb_path())
        kb_available = True
        kb_metadata = {
            "last_updated": kb.last_updated,
            "window_days": kb.window_days,
            "n_outcomes_used": kb.n_outcomes_used,
        }
        # Get ticker stats for attribution
        ticker_stats = kb.tickers.get(ctx.ticker.upper())
        if ticker_stats:
            kb_metadata["ticker_stats"] = {
                "n_trades": ticker_stats.n_trades,
                "hit_rate": round(ticker_stats.hit_rate, 4),
                "dollar_weighted_win_rate": round(ticker_stats.dollar_weighted_win_rate, 4),
                "blacklisted": ticker_stats.hit_rate < 0.43 and ticker_stats.dollar_weighted_win_rate < 0.45,
            }
        # Get regime stats for attribution
        if regime_label:
            regime_stats = kb.regimes.get(regime_label)
            if regime_stats:
                kb_metadata["regime_stats"] = {
                    "n_trades": regime_stats.n_trades,
                    "hit_rate": round(regime_stats.hit_rate, 4),
                }
    except Exception:
        kb = None

    # Log RAG attribution so operators can see what KB applied.
    ctx.attribution["rag_learning"] = {
        "enabled": kb_available,
        "kb_metadata": kb_metadata,
        "conviction_multipliers": {
            "ticker_multiplier": round(t_mult, 3),
            "regime_multiplier": round(r_mult, 3),
            "combined_multiplier": round(q_mult, 3),
        },
        "p_bull_adjustment": {
            "p_bull_raw": round(p_bull, 4),
            "p_bull_adjusted": round(p_bull_adj, 4),
            "adjustment_factor": round(q_mult, 3),
        },
    }
    p_bull = p_bull_adj

    # --- Gate B1/B4: Kelly with Thompson posterior scaling ---
    # Load Thompson posteriors from KB if available (or use previously loaded KB)
    posterior_mean = None
    posterior_std = None
    thompson_attribution = {
        "enabled": False,
        "reason": "No KB available",
        "posteriors": {},
    }
    try:
        if kb is None:
            kb = KnowledgeBase.load(default_kb_path())

        if kb and kb.posteriors:
            regime_id = ctx.regime.regime_id if ctx.regime else 5  # default to normal+neutral

            # Get average posterior across all debaters for this regime
            all_posteriors = []
            debater_posts = {}
            for debater_name in ["directional", "income", "volatility", "options_flow", "sentiment"]:
                post = kb.get_posterior(debater_name, regime_id)
                if post.alpha > 1.0 or post.beta > 1.0:  # Has observations beyond prior
                    all_posteriors.append(post)
                    debater_posts[debater_name] = {
                        "alpha": round(post.alpha, 1),
                        "beta": round(post.beta, 1),
                        "mean": round(post.mean, 4),
                        "std": round(post.std, 4),
                    }

            if all_posteriors:
                # Compute mean of all posterior means and average std
                posterior_mean = sum(p.mean for p in all_posteriors) / len(all_posteriors)
                posterior_std = sum(p.std for p in all_posteriors) / len(all_posteriors)
                thompson_attribution = {
                    "enabled": True,
                    "reason": f"Thompson posteriors loaded for regime {regime_id}",
                    "regime_id": regime_id,
                    "posteriors": debater_posts,
                    "aggregate": {
                        "mean": round(posterior_mean, 4),
                        "std": round(posterior_std, 4),
                        "confidence": round(1.0 / (1.0 + posterior_std), 4),
                    },
                }
            else:
                thompson_attribution["reason"] = f"No posteriors with observations for regime {regime_id}"
        else:
            thompson_attribution["reason"] = "KB loaded but no posteriors present"
    except Exception as e:
        thompson_attribution["reason"] = f"KB load error: {str(e)}"

    kelly = size_from_consensus(
        p_bull=p_bull,
        direction=direction,
        max_profit=max_profit,
        max_loss=max_loss,
        dte=dte,
        account_size=account_size,
        posterior_mean=posterior_mean,
        posterior_std=posterior_std,
    )

    kelly_diag = {
        "kelly_f": kelly.kelly_f,
        "dte_scalar": kelly.dte_scalar,
        "edge": kelly.edge,
        "odds": kelly.odds,
        "kelly_contracts": kelly.contracts,
        "thompson_scaling": thompson_attribution,
    }

    if not kelly.viable:
        return {
            "approved": False,
            "reject_gate": "kelly",
            "reject_reason": kelly.reject_reason,
            "contracts": 0,
            "kelly": kelly_diag,
        }

    # --- Gate B2: book Greeks limits ---
    # Scale down if the full Kelly count would breach caps, then hard-check.
    final_contracts = book.scale_to_fit(
        delta=delta_per * kelly.contracts,
        vega=vega_per * kelly.contracts,
        theta=theta_per * kelly.contracts,
        underlying=ctx.ticker,
        contracts_requested=kelly.contracts,
    )

    if final_contracts == 0:
        return {
            "approved": False,
            "reject_gate": "book_limits",
            "reject_reason": (
                f"Book limits prevent any contracts for {ctx.ticker} at current exposure."
            ),
            "contracts": 0,
            "kelly": kelly_diag,
        }

    limit_check = book.check_proposed(
        delta=delta_per * final_contracts,
        vega=vega_per * final_contracts,
        theta=theta_per * final_contracts,
        underlying=ctx.ticker,
    )

    if not limit_check.approved:
        return {
            "approved": False,
            "reject_gate": "book_limits",
            "reject_reason": limit_check.reject_reason,
            "contracts": 0,
            "kelly": kelly_diag,
        }

    # --- Gate B3: CVaR stress check ---
    cvar_checker = CVaRChecker(account_size=account_size)
    cvar_result = cvar_checker.check(
        delta=delta_per,
        vega=vega_per,
        price=price,
        contracts=final_contracts,
        book_delta=book.net_delta,
        book_vega=book.net_vega,
    )

    cvar_diag = {
        "worst_scenario": cvar_result.worst_scenario,
        "worst_pnl": round(cvar_result.worst_pnl, 2),
        "budget_dollars": round(cvar_result.budget_dollars, 2),
    }

    if not cvar_result.approved:
        # Try scaling down to fit within CVaR budget before hard reject
        reduced = cvar_checker.max_contracts_within_budget(
            delta=delta_per,
            vega=vega_per,
            price=price,
            requested=final_contracts,
            book_delta=book.net_delta,
            book_vega=book.net_vega,
        )

        if reduced == 0:
            return {
                "approved": False,
                "reject_gate": "cvar",
                "reject_reason": cvar_result.reject_reason,
                "contracts": 0,
                "kelly": kelly_diag,
                "cvar": cvar_diag,
            }

        # CVaR reduced the count — re-verify book limits with smaller size
        limit_recheck = book.check_proposed(
            delta=delta_per * reduced,
            vega=vega_per * reduced,
            theta=theta_per * reduced,
            underlying=ctx.ticker,
        )
        if not limit_recheck.approved:
            return {
                "approved": False,
                "reject_gate": "book_limits_after_cvar_reduction",
                "reject_reason": limit_recheck.reject_reason,
                "contracts": 0,
                "kelly": kelly_diag,
                "cvar": cvar_diag,
            }

        final_contracts = reduced
        # Re-fetch limit_check for headroom reporting at the reduced size
        limit_check = limit_recheck

    # --- Gate B3b: Monte Carlo CVaR (P0#3) ---
    # The scenario stress above is a coarse pre-check. Real CVaR is computed
    # here via N-path MC with fat-tail returns and IV shocks.
    gamma_per = float(md.get("gamma_per_contract", 0.0))
    mc = MonteCarloCVaR(account_size=account_size, rng_seed=0)
    mc_result = mc.check(
        delta=delta_per, gamma=gamma_per, vega=vega_per, theta=theta_per,
        price=price, contracts=final_contracts,
        book_delta=book.net_delta, book_vega=book.net_vega,
    )
    mc_diag = {
        "mode": mc_result.mode,
        "n_paths": mc_result.n_paths,
        "var_loss": mc_result.var_loss,
        "cvar_loss": mc_result.cvar_loss,
        "worst_pnl": mc_result.worst_pnl,
        "budget_dollars": mc_result.budget_dollars,
    }
    if not mc_result.approved:
        reduced = mc.max_contracts_within_budget(
            delta=delta_per, gamma=gamma_per, vega=vega_per, theta=theta_per,
            price=price, requested=final_contracts,
            book_delta=book.net_delta, book_vega=book.net_vega,
        )
        if reduced == 0:
            return {
                "approved": False,
                "reject_gate": "mc_cvar",
                "reject_reason": mc_result.reject_reason,
                "contracts": 0,
                "kelly": kelly_diag,
                "cvar": cvar_diag,
                "mc_cvar": mc_diag,
            }
        # Re-verify book limits at the reduced size
        limit_recheck = book.check_proposed(
            delta=delta_per * reduced,
            vega=vega_per * reduced,
            theta=theta_per * reduced,
            underlying=ctx.ticker,
        )
        if not limit_recheck.approved:
            return {
                "approved": False,
                "reject_gate": "book_limits_after_mc_cvar_reduction",
                "reject_reason": limit_recheck.reject_reason,
                "contracts": 0,
                "kelly": kelly_diag,
                "cvar": cvar_diag,
                "mc_cvar": mc_diag,
            }
        final_contracts = reduced
        limit_check = limit_recheck

    # All four gates passed
    kelly_diag["final_contracts"] = final_contracts

    return {
        "approved": True,
        "contracts": final_contracts,
        "max_dollars_at_risk": round(final_contracts * max_loss, 2),
        "max_profit_dollars": round(final_contracts * max_profit, 2),
        "kelly": kelly_diag,
        "book_after": {
            "delta_after": round(limit_check.delta_after, 2),
            "vega_after": round(limit_check.vega_after, 2),
            "theta_after": round(limit_check.theta_after, 2),
            "delta_headroom": round(limit_check.delta_headroom, 2),
            "vega_headroom": round(limit_check.vega_headroom, 2),
            "theta_headroom": round(limit_check.theta_headroom, 2),
        },
        "cvar": cvar_diag,
        "mc_cvar": mc_diag,
    }


# =============================================================================
# L8 helpers — exit engine
# =============================================================================

def _build_exit_rules(ctx: PipelineContext) -> dict[str, Any]:
    """Build exit parameters for a newly approved trade.

    These are attached to the OpenPosition when it is registered in the
    PositionMonitor after execution.  The ExitEngine reads them on every
    evaluation tick.
    """
    md = ctx.market_data
    contracts = ctx.sizing["contracts"]
    max_loss = float(md.get("max_loss", 300.0))
    max_profit = float(md.get("max_profit", 200.0))
    dte = int(md.get("dte", 30))

    return {
        "trade_id": str(uuid.uuid4()),
        "stop_loss_pct": 1.00,
        "profit_target_pct": 0.50,
        "dte_emergency_threshold": 2,
        "time_stop_days": min(dte, 21),
        "max_loss_dollars": round(contracts * max_loss, 2),
        "max_profit_dollars": round(contracts * max_profit, 2),
        "structure": md.get("structure", "VERTICAL_CALL_SPREAD"),
    }


def _check_open_positions(
    ctx: PipelineContext,
    monitor: PositionMonitor,
    clock: Clock | None = None,
) -> list[dict[str, Any]]:
    """Run exit engine against all open positions for this ticker.

    Called on every pipeline run so that open positions are evaluated against
    the freshly computed regime and consensus before a new trade is considered.
    Only positions where should_exit=True are returned.
    """
    positions = monitor.positions_for(ctx.ticker)
    if not positions:
        return []

    exit_context: dict[str, Any] = {}
    if ctx.regime is not None:
        exit_context["regime_id"] = ctx.regime.regime_id
    if ctx.consensus is not None:
        exit_context["consensus_direction"] = ctx.consensus.direction.value

    engine = ExitEngine(clock=clock)
    decisions = engine.exits_required(positions, context=exit_context)

    return [
        {
            "trade_id": d.trade_id,
            "should_exit": d.should_exit,
            "reason": d.reason.value if d.reason else None,
            "urgency": d.urgency.value if d.urgency else None,
            "detail": d.detail,
            "current_pnl": d.current_pnl,
            "current_dte": d.current_dte,
        }
        for d in decisions
    ]


# =============================================================================
# Decision assembler
# =============================================================================

def _build_decision(ctx: PipelineContext, ticker: str) -> dict[str, Any]:
    """Assemble the final decision dict from pipeline context."""
    if ctx.sizing is not None:
        status = "sized_approved" if ctx.sizing["approved"] else "sized_rejected"
    elif ctx.consensus is not None:
        status = "full_pipeline"
    elif ctx.debater_opinions:
        status = "debaters_only"
    else:
        status = "scaffold_only"

    decision: dict[str, Any] = {
        "status": status,
        "ticker": ticker,
        "regime_id": ctx.regime.regime_id if ctx.regime else None,
        "opinion_count": len(ctx.debater_opinions),
        "direction": ctx.consensus.direction.value if ctx.consensus else None,
        "consensus_score": ctx.consensus.score if ctx.consensus else None,
        "p_bull": ctx.consensus.p_bull if ctx.consensus else None,
        "recommended_expiry": ctx.market_data.get("_recommended_expiry"),
        "chains_analyzed": len(ctx.market_data.get("chains_by_expiry", {})),
    }

    if ctx.sizing is not None:
        decision["contracts"] = ctx.sizing.get("contracts", 0)
        decision["max_dollars_at_risk"] = ctx.sizing.get("max_dollars_at_risk")
        if not ctx.sizing["approved"]:
            decision["sizing_reject_reason"] = ctx.sizing.get("reject_reason")
            decision["sizing_reject_gate"] = ctx.sizing.get("reject_gate")

    if ctx.exit_rules is not None:
        decision["exit_rules"] = ctx.exit_rules

    if ctx.open_position_exits:
        decision["open_position_exit_alerts"] = ctx.open_position_exits

    return decision
