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

import uuid
from dataclasses import dataclass, field
from typing import Any

from oa2.consensus.calibration import Calibrator, default_calibrator_path
from oa2.consensus.engine import ConsensusEngine
from oa2.core import feature_flags
from oa2.core.clock import Clock, SystemClock
from oa2.core.logging_util import PipelineLogger, get_detail_logging_enabled
from oa2.dealer.agent import DealerAgent
from oa2.debaters.runner import DebaterEnsemble
from oa2.execution.exit import ExitEngine
from oa2.execution.monitor import ChainProvider, PositionMonitor
from oa2.performance.bandit import BanditEngine
from oa2.regime.classifier import RegimeClassifier
from oa2.sizing.cvar import CVaRChecker
from oa2.sizing.kelly import size_from_consensus
from oa2.sizing.limits import GreeksBook
from oa2.sizing.mc_cvar import MonteCarloCVaR

_DEFAULT_ACCOUNT_SIZE = 50_000.0


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
) -> PipelineContext:
    """End-to-end pipeline for one ticker.

    Layers (each gated by a feature flag):
      L0  fetch market data
      L1  regime classification             (OA2_FLAG_REGIME)
      L2  context agents / dealer           (OA2_FLAG_DEALER)
      L3  timeframe routing                 (implicit in pipeline order)
      L4  debater ensemble                  (OA2_FLAG_DEBATERS)
      L5  consensus engine + bandit         (OA2_FLAG_CONSENSUS, OA2_FLAG_BANDIT)
      L6  sizing: Kelly + Greeks limits + CVaR  (OA2_FLAG_SIZING)
      L7  portfolio: book Greeks state      (OA2_FLAG_SIZING)
      L8  exit engine: tag + open-position alerts  (OA2_FLAG_EXIT)

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
            calibrator = Calibrator.load(default_calibrator_path())
        except Exception:
            calibrator = Calibrator()

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
            from oa2.dataflows.cache import fetch_with_cache
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

    # ------------------------------------------------------------------
    # L0b — fetch flow data for flow debater (new)
    # ------------------------------------------------------------------
    # Wire moomoo or other adapters to populate real flow signals for FlowDebater.
    # If unavailable, flow_data will be "absent" and FlowDebater abstains cleanly.
    try:
        from oa2.dataflows.flow_adapter import auto_adapter
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

        consensus_engine = ConsensusEngine(regime=regime_id, prior_weights=bandit_weights)
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
    # L6 — sizing engine (Kelly + book limits + CVaR)
    # ------------------------------------------------------------------
    if feature_flags.SIZING_ENGINE_ENABLED and ctx.consensus is not None:
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

    # --- Gate B1/B4: Kelly ---
    kelly = size_from_consensus(
        p_bull=p_bull,
        direction=direction,
        max_profit=max_profit,
        max_loss=max_loss,
        dte=dte,
        account_size=account_size,
    )

    kelly_diag = {
        "kelly_f": kelly.kelly_f,
        "dte_scalar": kelly.dte_scalar,
        "edge": kelly.edge,
        "odds": kelly.odds,
        "kelly_contracts": kelly.contracts,
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
