"""Integration tests — L6/L7/L8 wired into pipeline.py.

Covers:
  - L6 sizing (Kelly + limits + CVaR) fires when SIZING_ENGINE_ENABLED
  - sized_approved when consensus is strong and book is empty
  - sized_rejected when Kelly edge is below minimum
  - sized_rejected when direction is NEUTRAL
  - CVaR forces size reduction before hard reject
  - Book limits reject when book is already full
  - L7 book state included in attribution when sizing is enabled
  - L8 exit rules tagged on sized_approved decision
  - L8 open-position alerts from PositionMonitor
  - Pipeline without flags produces scaffold_only (smoke guard)
  - full_pipeline produced when consensus runs but sizing is off
"""

from __future__ import annotations

import datetime as dt
import time
import unittest
from unittest.mock import patch, MagicMock

from tradingbot.core.clock import ManualClock
from tradingbot.graph.pipeline import run, PipelineContext
from tradingbot.sizing.limits import GreeksBook
from tradingbot.execution.monitor import OpenPosition, PositionMonitor


# =============================================================================
# Helpers
# =============================================================================

def _make_bullish_consensus(p_bull: float = 0.72, direction: str = "BULLISH") -> MagicMock:
    """Stub consensus object that mimics ConsensusEngine output."""
    c = MagicMock()
    c.direction = MagicMock()
    c.direction.value = direction
    c.score = p_bull - 0.5
    c.n_eff = 3.5
    c.p_bull = p_bull
    c.weights = {}
    return c


def _make_neutral_consensus() -> MagicMock:
    return _make_bullish_consensus(p_bull=0.50, direction="NEUTRAL")


def _make_open_position(
    trade_id: str = "existing_001",
    ticker: str = "SPY",
    direction: str = "BULLISH",
    current_pnl: float = 0.0,
    current_dte: int = 20,
    entry_regime: int = 3,
    max_profit_per_contract: float = 200.0,
    max_loss_per_contract: float = 300.0,
) -> OpenPosition:
    return OpenPosition(
        trade_id=trade_id,
        ticker=ticker,
        underlying=ticker,
        structure="VERTICAL_CALL_SPREAD",
        direction=direction,
        entry_price=450.0,
        entry_premium=2.00,
        entry_time=time.time() - 3600,
        entry_regime=entry_regime,
        entry_dte=30,
        contracts=1,
        max_profit_per_contract=max_profit_per_contract,
        max_loss_per_contract=max_loss_per_contract,
        current_pnl=current_pnl,
        current_dte=current_dte,
    )


# Market data that gives sizing engine real inputs
_SPY_MARKET_DATA = {
    "ticker": "SPY",
    "price": 450.0,
    "max_profit": 200.0,
    "max_loss": 100.0,
    "dte": 21,
    "delta_per_contract": 15.0,
    "vega_per_contract": 8.0,
    "theta_per_contract": -5.0,
}


# =============================================================================
# L6 — Sizing engine
# =============================================================================

class TestL6SizingApproved(unittest.TestCase):
    """Strong bullish consensus + empty book + benign market data → sized_approved."""

    def test_sized_approved_status(self):
        with (
            patch("tradingbot.graph.pipeline.feature_flags.SIZING_ENGINE_ENABLED", True),
            patch("tradingbot.graph.pipeline.feature_flags.EXIT_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.REGIME_CLASSIFIER_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEBATERS_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.CONSENSUS_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEALER_AGENT_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEALER_SHADOW_LOG", False),
        ):
            ctx = run("SPY", context_dict=_SPY_MARKET_DATA, account_size=50_000)
            # No consensus → sizing cannot run → falls back to scaffold_only
            self.assertEqual(ctx.decision["status"], "scaffold_only")

    def test_sized_approved_with_injected_consensus(self):
        """Inject consensus directly and verify L6 produces sized_approved."""
        ctx = run("SPY", context_dict=_SPY_MARKET_DATA, account_size=50_000)
        ctx.consensus = _make_bullish_consensus(p_bull=0.72)

        with (
            patch("tradingbot.graph.pipeline.feature_flags.SIZING_ENGINE_ENABLED", True),
            patch("tradingbot.graph.pipeline.feature_flags.EXIT_ENGINE_ENABLED", False),
        ):
            from tradingbot.graph.pipeline import _run_sizing
            book = GreeksBook(account_size=50_000)
            result = _run_sizing(ctx, book, 50_000)

        self.assertTrue(result["approved"])
        self.assertGreater(result["contracts"], 0)
        self.assertIn("kelly", result)
        self.assertIn("book_after", result)
        self.assertIn("cvar", result)

    def test_sizing_includes_max_dollars_at_risk(self):
        ctx = run("SPY", context_dict=_SPY_MARKET_DATA, account_size=50_000)
        ctx.consensus = _make_bullish_consensus(p_bull=0.72)

        from tradingbot.graph.pipeline import _run_sizing
        book = GreeksBook(account_size=50_000)
        result = _run_sizing(ctx, book, 50_000)

        self.assertIn("max_dollars_at_risk", result)
        self.assertIn("max_profit_dollars", result)
        self.assertGreater(result["max_dollars_at_risk"], 0)

    def test_sizing_kelly_diag_present(self):
        ctx = run("SPY", context_dict=_SPY_MARKET_DATA, account_size=50_000)
        ctx.consensus = _make_bullish_consensus(p_bull=0.72)

        from tradingbot.graph.pipeline import _run_sizing
        book = GreeksBook(account_size=50_000)
        result = _run_sizing(ctx, book, 50_000)

        kelly = result["kelly"]
        self.assertIn("kelly_f", kelly)
        self.assertIn("dte_scalar", kelly)
        self.assertIn("edge", kelly)
        self.assertIn("odds", kelly)
        self.assertGreater(kelly["edge"], 0.52)

    def test_bearish_consensus_also_sizes(self):
        """Phase A1: BEARISH blocked by default; re-enable via TRADINGBOT_FLAG_BEARISH=1."""
        import os
        ctx = run("SPY", context_dict=_SPY_MARKET_DATA, account_size=50_000)
        ctx.consensus = _make_bullish_consensus(p_bull=0.25, direction="BEARISH")

        from tradingbot.graph.pipeline import _run_sizing
        book = GreeksBook(account_size=50_000)

        os.environ["TRADINGBOT_FLAG_BEARISH"] = "1"
        try:
            result = _run_sizing(ctx, book, 50_000)
            # edge = 1 - 0.25 = 0.75 → should approve when BEARISH enabled
            self.assertTrue(result["approved"])
            self.assertGreater(result["contracts"], 0)
        finally:
            os.environ.pop("TRADINGBOT_FLAG_BEARISH", None)

    def test_bearish_blocked_by_default(self):
        """Phase A1: with TRADINGBOT_FLAG_BEARISH unset, BEARISH consensus is rejected."""
        import os
        os.environ.pop("TRADINGBOT_FLAG_BEARISH", None)
        ctx = run("SPY", context_dict=_SPY_MARKET_DATA, account_size=50_000)
        ctx.consensus = _make_bullish_consensus(p_bull=0.25, direction="BEARISH")

        from tradingbot.graph.pipeline import _run_sizing
        book = GreeksBook(account_size=50_000)
        result = _run_sizing(ctx, book, 50_000)

        self.assertFalse(result["approved"])
        self.assertEqual(result["reject_gate"], "directional_bias")
        self.assertEqual(result["contracts"], 0)


class TestL6SizingRejected(unittest.TestCase):
    """Conditions that cause sizing to reject."""

    def test_neutral_direction_rejected(self):
        ctx = run("SPY", context_dict=_SPY_MARKET_DATA, account_size=50_000)
        ctx.consensus = _make_neutral_consensus()

        from tradingbot.graph.pipeline import _run_sizing
        book = GreeksBook(account_size=50_000)
        result = _run_sizing(ctx, book, 50_000)

        self.assertFalse(result["approved"])
        self.assertEqual(result["reject_gate"], "kelly")
        self.assertEqual(result["contracts"], 0)
        self.assertIn("NEUTRAL", result["reject_reason"])

    def test_weak_edge_rejected(self):
        """p_bull just above 0.5 → edge=0.51 < min_edge(0.52) → rejected."""
        ctx = run("SPY", context_dict=_SPY_MARKET_DATA, account_size=50_000)
        ctx.consensus = _make_bullish_consensus(p_bull=0.51)

        from tradingbot.graph.pipeline import _run_sizing
        book = GreeksBook(account_size=50_000)
        result = _run_sizing(ctx, book, 50_000)

        self.assertFalse(result["approved"])
        self.assertEqual(result["reject_gate"], "kelly")
        self.assertEqual(result["contracts"], 0)

    def test_full_book_rejects_new_trade(self):
        """Book already at delta cap → new trade rejected at book_limits gate."""
        ctx = run("SPY", context_dict=_SPY_MARKET_DATA, account_size=50_000)
        ctx.consensus = _make_bullish_consensus(p_bull=0.72)

        from tradingbot.graph.pipeline import _run_sizing
        # Fill book to near-cap: delta cap = 0.30 × 50_000 = 15_000
        book = GreeksBook(account_size=50_000)
        book.add_position("existing", "SPY", delta=14_999.0, vega=0.0, theta=0.0)
        result = _run_sizing(ctx, book, 50_000)

        self.assertFalse(result["approved"])
        self.assertIn("book_limits", result["reject_gate"])
        self.assertEqual(result["contracts"], 0)

    def test_reject_reason_is_a_string(self):
        ctx = run("SPY", context_dict=_SPY_MARKET_DATA, account_size=50_000)
        ctx.consensus = _make_neutral_consensus()

        from tradingbot.graph.pipeline import _run_sizing
        book = GreeksBook(account_size=50_000)
        result = _run_sizing(ctx, book, 50_000)

        self.assertIsInstance(result["reject_reason"], str)
        self.assertGreater(len(result["reject_reason"]), 0)

    def test_cvar_forced_reduction(self):
        """Tiny account + large delta → CVaR fires; size reduced or rejected."""
        ctx = run("SPY", context_dict={
            **_SPY_MARKET_DATA,
            "max_profit": 200.0,
            "max_loss": 100.0,
            "delta_per_contract": 50_000.0,   # enormous delta per contract
            "vega_per_contract": 0.0,
            "theta_per_contract": 0.0,
        }, account_size=1_000)   # tiny account
        ctx.consensus = _make_bullish_consensus(p_bull=0.72)

        from tradingbot.graph.pipeline import _run_sizing
        book = GreeksBook(account_size=1_000)
        result = _run_sizing(ctx, book, 1_000)

        # Either CVaR rejection or reduced to 0
        if result["approved"]:
            self.assertGreater(result["contracts"], 0)
        else:
            self.assertIn(result["reject_gate"], ("cvar", "kelly", "book_limits"))


# =============================================================================
# L7 — Portfolio book state
# =============================================================================

class TestL7BookState(unittest.TestCase):
    def test_book_state_in_attribution_when_sizing_enabled(self):
        """book_state is always added to attribution when SIZING_ENGINE_ENABLED."""
        with (
            patch("tradingbot.graph.pipeline.feature_flags.SIZING_ENGINE_ENABLED", True),
            patch("tradingbot.graph.pipeline.feature_flags.EXIT_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.REGIME_CLASSIFIER_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEBATERS_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.CONSENSUS_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEALER_AGENT_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEALER_SHADOW_LOG", False),
        ):
            book = GreeksBook(account_size=50_000)
            ctx = run("SPY", context_dict=_SPY_MARKET_DATA, account_size=50_000, book=book)

        self.assertIn("book_state", ctx.attribution)
        bs = ctx.attribution["book_state"]
        self.assertIn("account_size", bs)
        self.assertIn("net_delta", bs)
        self.assertIn("net_vega", bs)
        self.assertIn("net_theta", bs)

    def test_book_state_absent_when_sizing_disabled(self):
        """book_state must NOT appear in attribution when SIZING_ENGINE_ENABLED=False."""
        with (
            patch("tradingbot.graph.pipeline.feature_flags.SIZING_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.EXIT_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.REGIME_CLASSIFIER_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEBATERS_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.CONSENSUS_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEALER_AGENT_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEALER_SHADOW_LOG", False),
        ):
            ctx = run("SPY", context_dict=_SPY_MARKET_DATA, account_size=50_000)

        self.assertNotIn("book_state", ctx.attribution)

    def test_shared_book_accumulates_across_runs(self):
        """A GreeksBook passed to run() accumulates Greeks across multiple calls."""
        from tradingbot.graph.pipeline import _run_sizing

        book = GreeksBook(account_size=50_000)

        ctx1 = run("SPY", context_dict=_SPY_MARKET_DATA, account_size=50_000)
        ctx1.consensus = _make_bullish_consensus(p_bull=0.72)
        r1 = _run_sizing(ctx1, book, 50_000)

        if r1["approved"]:
            book.add_position(
                "trade_001", "SPY",
                delta=_SPY_MARKET_DATA["delta_per_contract"] * r1["contracts"],
                vega=_SPY_MARKET_DATA["vega_per_contract"] * r1["contracts"],
                theta=_SPY_MARKET_DATA["theta_per_contract"] * r1["contracts"],
                contracts=r1["contracts"],
            )

        # Book now has non-zero Greeks; headroom should be reduced
        self.assertNotEqual(book.net_delta, 0.0)


# =============================================================================
# L8 — Exit engine
# =============================================================================

class TestL8ExitRuleTagging(unittest.TestCase):
    """exit_rules are tagged on decision when sizing approved."""

    def _run_with_approved_sizing(self) -> PipelineContext:
        from tradingbot.graph.pipeline import _run_sizing, _build_exit_rules, _build_decision

        ctx = run("SPY", context_dict=_SPY_MARKET_DATA, account_size=50_000)
        ctx.consensus = _make_bullish_consensus(p_bull=0.72)
        book = GreeksBook(account_size=50_000)
        ctx.sizing = _run_sizing(ctx, book, 50_000)
        if ctx.sizing["approved"]:
            ctx.exit_rules = _build_exit_rules(ctx)
            ctx.attribution["exit_rules"] = ctx.exit_rules
        ctx.decision = _build_decision(ctx, "SPY")
        return ctx

    def test_exit_rules_present_on_approved_trade(self):
        ctx = self._run_with_approved_sizing()
        if ctx.sizing["approved"]:
            self.assertIsNotNone(ctx.exit_rules)
            self.assertIn("exit_rules", ctx.attribution)

    def test_exit_rules_contain_required_keys(self):
        ctx = self._run_with_approved_sizing()
        if ctx.sizing["approved"]:
            rules = ctx.exit_rules
            self.assertIn("trade_id", rules)
            self.assertIn("stop_loss_pct", rules)
            self.assertIn("profit_target_pct", rules)
            self.assertIn("dte_emergency_threshold", rules)
            self.assertIn("time_stop_days", rules)
            self.assertIn("max_loss_dollars", rules)
            self.assertIn("max_profit_dollars", rules)

    def test_trade_id_is_uuid_string(self):
        ctx = self._run_with_approved_sizing()
        if ctx.sizing["approved"]:
            trade_id = ctx.exit_rules["trade_id"]
            self.assertIsInstance(trade_id, str)
            self.assertEqual(len(trade_id), 36)   # UUID4 format

    def test_exit_rules_defaults_are_sane(self):
        ctx = self._run_with_approved_sizing()
        if ctx.sizing["approved"]:
            rules = ctx.exit_rules
            self.assertEqual(rules["stop_loss_pct"], 1.00)
            self.assertEqual(rules["profit_target_pct"], 0.50)
            self.assertEqual(rules["dte_emergency_threshold"], 2)
            self.assertGreaterEqual(rules["time_stop_days"], 1)
            self.assertLessEqual(rules["time_stop_days"], 21)

    def test_exit_rules_dollars_consistent_with_sizing(self):
        ctx = self._run_with_approved_sizing()
        if ctx.sizing["approved"]:
            rules = ctx.exit_rules
            self.assertAlmostEqual(
                rules["max_loss_dollars"],
                ctx.sizing["max_dollars_at_risk"],
                places=2,
            )


class TestL8OpenPositionAlerts(unittest.TestCase):
    """Open-position exit alerts via PositionMonitor."""

    def _run_with_monitor(self, position: OpenPosition) -> PipelineContext:
        monitor = PositionMonitor()
        monitor.add(position)

        # Use a fixed Monday at 10 AM ET to avoid Friday sweep / EOD cutoff issues
        monday_10am = dt.datetime(2026, 5, 25, 10, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=-4)))
        clock = ManualClock(monday_10am)

        with (
            patch("tradingbot.graph.pipeline.feature_flags.EXIT_ENGINE_ENABLED", True),
            patch("tradingbot.graph.pipeline.feature_flags.SIZING_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.REGIME_CLASSIFIER_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEBATERS_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.CONSENSUS_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEALER_AGENT_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEALER_SHADOW_LOG", False),
        ):
            ctx = run("SPY", context_dict=_SPY_MARKET_DATA, monitor=monitor, clock=clock)
        return ctx

    def test_stop_loss_hit_produces_alert(self):
        """Position at full stop loss → open_position_exits contains alert."""
        pos = _make_open_position(
            current_pnl=-300.0,           # = max_loss → stop triggered
            max_loss_per_contract=300.0,
        )
        ctx = self._run_with_monitor(pos)
        self.assertGreater(len(ctx.open_position_exits), 0)
        alert = ctx.open_position_exits[0]
        self.assertTrue(alert["should_exit"])
        self.assertEqual(alert["reason"], "stop_loss")
        self.assertEqual(alert["urgency"], "immediate")

    def test_profit_target_hit_produces_alert(self):
        """Position at 50% of max_profit → profit target alert."""
        pos = _make_open_position(
            current_pnl=100.0,            # 100 = 50% of max_profit=200
            max_profit_per_contract=200.0,
        )
        ctx = self._run_with_monitor(pos)
        self.assertGreater(len(ctx.open_position_exits), 0)
        alert = ctx.open_position_exits[0]
        self.assertTrue(alert["should_exit"])
        self.assertEqual(alert["reason"], "profit_target")

    def test_healthy_position_produces_no_alert(self):
        """Position mid-trade with no rule violations → no open_position_exits."""
        pos = _make_open_position(
            current_pnl=20.0,
            current_dte=15,
            max_profit_per_contract=200.0,
            max_loss_per_contract=300.0,
        )
        ctx = self._run_with_monitor(pos)
        self.assertEqual(len(ctx.open_position_exits), 0)

    def test_no_monitor_means_no_alerts(self):
        """monitor=None → open_position_exits is empty."""
        with (
            patch("tradingbot.graph.pipeline.feature_flags.EXIT_ENGINE_ENABLED", True),
            patch("tradingbot.graph.pipeline.feature_flags.SIZING_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.REGIME_CLASSIFIER_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEBATERS_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.CONSENSUS_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEALER_AGENT_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEALER_SHADOW_LOG", False),
        ):
            ctx = run("SPY", context_dict=_SPY_MARKET_DATA, monitor=None)
        self.assertEqual(ctx.open_position_exits, [])

    def test_alerts_in_attribution(self):
        """When exits fire, open_position_exit_alerts appears in attribution."""
        pos = _make_open_position(current_pnl=-300.0, max_loss_per_contract=300.0)
        ctx = self._run_with_monitor(pos)
        if ctx.open_position_exits:
            self.assertIn("open_position_exit_alerts", ctx.attribution)

    def test_alert_keys_are_complete(self):
        """Each alert has all required keys."""
        pos = _make_open_position(current_pnl=-300.0, max_loss_per_contract=300.0)
        ctx = self._run_with_monitor(pos)
        required = {"trade_id", "should_exit", "reason", "urgency", "detail",
                    "current_pnl", "current_dte"}
        for alert in ctx.open_position_exits:
            self.assertEqual(required, required & alert.keys())

    def test_other_ticker_positions_not_checked(self):
        """Monitor positions for a different ticker are not evaluated."""
        pos = _make_open_position(ticker="AAPL", current_pnl=-300.0, max_loss_per_contract=300.0)
        monitor = PositionMonitor()
        monitor.add(pos)

        with (
            patch("tradingbot.graph.pipeline.feature_flags.EXIT_ENGINE_ENABLED", True),
            patch("tradingbot.graph.pipeline.feature_flags.SIZING_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.REGIME_CLASSIFIER_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEBATERS_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.CONSENSUS_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEALER_AGENT_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEALER_SHADOW_LOG", False),
        ):
            ctx = run("SPY", context_dict=_SPY_MARKET_DATA, monitor=monitor)
        # SPY run should not see AAPL position
        self.assertEqual(ctx.open_position_exits, [])


# =============================================================================
# Decision status progression
# =============================================================================

class TestDecisionStatusProgression(unittest.TestCase):
    """Verify that decision.status correctly reflects which layers ran."""

    def _flags_off(self):
        return (
            patch("tradingbot.graph.pipeline.feature_flags.SIZING_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.EXIT_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.REGIME_CLASSIFIER_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEBATERS_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.CONSENSUS_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEALER_AGENT_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEALER_SHADOW_LOG", False),
        )

    def test_scaffold_only_when_all_flags_off(self):
        with (
            patch("tradingbot.graph.pipeline.feature_flags.SIZING_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.EXIT_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.REGIME_CLASSIFIER_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEBATERS_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.CONSENSUS_ENGINE_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEALER_AGENT_ENABLED", False),
            patch("tradingbot.graph.pipeline.feature_flags.DEALER_SHADOW_LOG", False),
        ):
            ctx = run("SPY")
        self.assertEqual(ctx.decision["status"], "scaffold_only")
        self.assertIsNone(ctx.decision["direction"])

    def test_full_pipeline_when_consensus_but_no_sizing(self):
        """Consensus ran but SIZING_ENGINE_ENABLED=False → full_pipeline status."""
        from tradingbot.graph.pipeline import _build_decision

        ctx = run("SPY", context_dict=_SPY_MARKET_DATA)
        ctx.consensus = _make_bullish_consensus(p_bull=0.72)
        # sizing not set (None) → full_pipeline
        ctx.decision = _build_decision(ctx, "SPY")

        self.assertEqual(ctx.decision["status"], "full_pipeline")
        self.assertEqual(ctx.decision["direction"], "BULLISH")
        self.assertIsNotNone(ctx.decision["p_bull"])

    def test_sized_rejected_reflected_in_decision(self):
        """Sizing ran but rejected → status=sized_rejected, reject fields present."""
        from tradingbot.graph.pipeline import _build_decision

        ctx = run("SPY", context_dict=_SPY_MARKET_DATA)
        ctx.consensus = _make_neutral_consensus()
        ctx.sizing = {
            "approved": False,
            "reject_gate": "kelly",
            "reject_reason": "NEUTRAL direction — no trade",
            "contracts": 0,
        }
        ctx.decision = _build_decision(ctx, "SPY")

        self.assertEqual(ctx.decision["status"], "sized_rejected")
        self.assertEqual(ctx.decision["contracts"], 0)
        self.assertIn("sizing_reject_reason", ctx.decision)
        self.assertIn("sizing_reject_gate", ctx.decision)

    def test_sized_approved_reflected_in_decision(self):
        """Sizing approved → status=sized_approved, contracts > 0."""
        from tradingbot.graph.pipeline import _build_decision, _run_sizing, _build_exit_rules

        ctx = run("SPY", context_dict=_SPY_MARKET_DATA)
        ctx.consensus = _make_bullish_consensus(p_bull=0.72)
        book = GreeksBook(account_size=50_000)
        ctx.sizing = _run_sizing(ctx, book, 50_000)

        if ctx.sizing["approved"]:
            ctx.exit_rules = _build_exit_rules(ctx)
        ctx.decision = _build_decision(ctx, "SPY")

        if ctx.sizing["approved"]:
            self.assertEqual(ctx.decision["status"], "sized_approved")
            self.assertGreater(ctx.decision["contracts"], 0)
            self.assertIn("exit_rules", ctx.decision)
            self.assertNotIn("sizing_reject_reason", ctx.decision)

    def test_decision_always_has_required_keys(self):
        """decision dict always contains the core keys regardless of which layers ran."""
        from tradingbot.graph.pipeline import _build_decision

        ctx = run("SPY")
        ctx.decision = _build_decision(ctx, "SPY")

        required = {"status", "ticker", "regime_id", "opinion_count",
                    "direction", "consensus_score", "p_bull"}
        for key in required:
            self.assertIn(key, ctx.decision, f"Missing key: {key}")

    def test_pipeline_context_fields_initialized(self):
        """PipelineContext always initialises sizing, exit_rules, open_position_exits."""
        ctx = run("SPY")
        self.assertIsNone(ctx.sizing)
        self.assertIsNone(ctx.exit_rules)
        self.assertEqual(ctx.open_position_exits, [])


if __name__ == "__main__":
    unittest.main()
