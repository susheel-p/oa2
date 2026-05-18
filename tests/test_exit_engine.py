"""Tests for Phase C: position monitor, exit rules, and roll logic."""

from __future__ import annotations

import time

import pytest

from oa2.execution.monitor import OpenPosition, PositionMonitor
from oa2.execution.exit import ExitEngine, ExitReason, ExitUrgency
from oa2.execution.roll import RollEngine


# =============================================================================
# Helpers
# =============================================================================

def make_position(
    trade_id: str = "t001",
    ticker: str = "SPY",
    structure: str = "IRON_CONDOR",
    direction: str = "NEUTRAL",
    entry_dte: int = 30,
    current_dte: int = 20,
    max_profit_per_contract: float = 200.0,
    max_loss_per_contract: float = 300.0,
    contracts: int = 1,
    current_pnl: float = 0.0,
    entry_time: float | None = None,
    entry_regime: int = 5,
    stop_loss_pct: float = 1.0,
    profit_target_pct: float = 0.5,
    **kwargs,
) -> OpenPosition:
    return OpenPosition(
        trade_id=trade_id,
        ticker=ticker,
        underlying=ticker,
        structure=structure,
        direction=direction,
        entry_price=450.0,
        entry_premium=2.00,
        entry_time=entry_time or (time.time() - 3600),
        entry_regime=entry_regime,
        entry_dte=entry_dte,
        contracts=contracts,
        max_profit_per_contract=max_profit_per_contract,
        max_loss_per_contract=max_loss_per_contract,
        current_pnl=current_pnl,
        current_dte=current_dte,
        stop_loss_pct=stop_loss_pct,
        profit_target_pct=profit_target_pct,
        **kwargs,
    )


# =============================================================================
# Position Monitor (C1)
# =============================================================================

class TestPositionMonitor:
    def test_add_and_get(self):
        monitor = PositionMonitor()
        pos = make_position("t1")
        monitor.add(pos)
        assert monitor.get("t1") is pos

    def test_remove_returns_position(self):
        monitor = PositionMonitor()
        pos = make_position("t1")
        monitor.add(pos)
        removed = monitor.remove("t1")
        assert removed is pos
        assert monitor.get("t1") is None

    def test_remove_missing_returns_none(self):
        monitor = PositionMonitor()
        assert monitor.remove("nonexistent") is None

    def test_clear_empties_all_positions(self):
        monitor = PositionMonitor()
        monitor.add(make_position("t1"))
        monitor.add(make_position("t2"))
        monitor.clear()
        assert monitor.position_count() == 0

    def test_mark_to_market_updates_pnl(self):
        monitor = PositionMonitor()
        pos = make_position("t1")
        monitor.add(pos)
        monitor.mark_to_market("t1", current_pnl=-50.0, current_underlying_price=448.0, current_dte=19)
        updated = monitor.get("t1")
        assert updated.current_pnl == -50.0
        assert updated.current_underlying_price == 448.0
        assert updated.current_dte == 19

    def test_mark_to_market_missing_returns_none(self):
        monitor = PositionMonitor()
        result = monitor.mark_to_market("missing", current_pnl=-10.0)
        assert result is None

    def test_positions_for_filters_by_underlying(self):
        monitor = PositionMonitor()
        monitor.add(make_position("t1", ticker="SPY"))
        monitor.add(make_position("t2", ticker="QQQ"))
        monitor.add(make_position("t3", ticker="SPY"))
        spy_positions = monitor.positions_for("SPY")
        assert len(spy_positions) == 2
        assert all(p.underlying == "SPY" for p in spy_positions)

    def test_positions_near_expiry_filters_by_dte(self):
        monitor = PositionMonitor()
        monitor.add(make_position("t1", current_dte=1))
        monitor.add(make_position("t2", current_dte=15))
        monitor.add(make_position("t3", current_dte=2))
        near = monitor.positions_near_expiry(dte_threshold=2)
        assert len(near) == 2

    def test_net_pnl_sums_all_positions(self):
        monitor = PositionMonitor()
        monitor.add(make_position("t1", current_pnl=100.0))
        monitor.add(make_position("t2", current_pnl=-30.0))
        assert monitor.net_pnl() == pytest.approx(70.0)

    def test_book_summary_has_required_keys(self):
        monitor = PositionMonitor()
        monitor.add(make_position("t1"))
        summary = monitor.book_summary()
        assert "open_positions" in summary
        assert "net_pnl" in summary
        assert "positions" in summary

    def test_max_profit_and_loss_computed_correctly(self):
        pos = make_position(contracts=3, max_profit_per_contract=100.0, max_loss_per_contract=200.0)
        assert pos.max_profit == pytest.approx(300.0)
        assert pos.max_loss == pytest.approx(600.0)

    def test_pnl_pct_of_max_profit(self):
        pos = make_position(
            contracts=1, max_profit_per_contract=200.0, max_loss_per_contract=300.0,
            current_pnl=100.0,
        )
        assert pos.pnl_pct_of_max_profit == pytest.approx(0.5)

    def test_stop_loss_dollars_negative(self):
        pos = make_position(max_loss_per_contract=200.0, stop_loss_pct=1.0)
        assert pos.stop_loss_dollars == pytest.approx(-200.0)

    def test_profit_target_dollars(self):
        pos = make_position(max_profit_per_contract=200.0, profit_target_pct=0.5)
        assert pos.profit_target_dollars == pytest.approx(100.0)


# =============================================================================
# Exit rules engine (C2)
# =============================================================================

class TestExitEngine:
    def _engine(self, **kwargs) -> ExitEngine:
        return ExitEngine(**kwargs)

    def test_no_rule_fires_on_neutral_position(self):
        engine = self._engine()
        pos = make_position(current_pnl=10.0, current_dte=20)
        decision = engine.evaluate(pos)
        assert not decision.should_exit

    # Rule 1: Stop loss
    def test_stop_loss_fires_at_full_loss(self):
        engine = self._engine(stop_loss_pct=1.0)
        # max_loss=300 × 1 contract; loss = -300 = threshold
        pos = make_position(max_loss_per_contract=300.0, current_pnl=-300.0)
        decision = engine.evaluate(pos)
        assert decision.should_exit
        assert decision.reason == ExitReason.STOP_LOSS
        assert decision.urgency == ExitUrgency.IMMEDIATE

    def test_stop_loss_fires_below_threshold(self):
        engine = self._engine(stop_loss_pct=0.75)
        # threshold = -300 × 0.75 = -225
        pos = make_position(max_loss_per_contract=300.0, current_pnl=-250.0)
        decision = engine.evaluate(pos)
        assert decision.should_exit
        assert decision.reason == ExitReason.STOP_LOSS

    def test_stop_loss_does_not_fire_below_threshold(self):
        engine = self._engine(stop_loss_pct=1.0)
        pos = make_position(max_loss_per_contract=300.0, current_pnl=-100.0)
        decision = engine.evaluate(pos)
        assert not decision.should_exit

    # Rule 2: DTE emergency
    def test_dte_emergency_fires_for_short_structures(self):
        engine = self._engine(dte_emergency_threshold=2)
        pos = make_position(structure="IRON_CONDOR", current_dte=1)
        decision = engine.evaluate(pos)
        assert decision.should_exit
        assert decision.reason == ExitReason.DTE_EMERGENCY
        assert decision.urgency == ExitUrgency.IMMEDIATE

    def test_dte_emergency_fires_at_threshold(self):
        engine = self._engine(dte_emergency_threshold=2)
        pos = make_position(structure="VERTICAL_CALL_SPREAD", current_dte=2)
        decision = engine.evaluate(pos)
        assert decision.should_exit
        assert decision.reason == ExitReason.DTE_EMERGENCY

    def test_dte_emergency_does_not_fire_for_long_only(self):
        engine = self._engine(dte_emergency_threshold=2)
        pos = make_position(structure="LONG_CALL", current_dte=1)
        decision = engine.evaluate(pos)
        # LONG_CALL is not in short-leg set — should not fire DTE emergency
        assert decision.reason != ExitReason.DTE_EMERGENCY

    def test_dte_emergency_does_not_fire_above_threshold(self):
        engine = self._engine(dte_emergency_threshold=2)
        pos = make_position(structure="IRON_CONDOR", current_dte=5)
        decision = engine.evaluate(pos)
        assert not decision.should_exit

    # Rule 4: Profit target
    def test_profit_target_fires_at_50_pct(self):
        engine = self._engine(profit_target_pct=0.5)
        # max_profit = 200; target = 100; current_pnl = 110 >= 100
        pos = make_position(max_profit_per_contract=200.0, current_pnl=110.0, current_dte=15)
        decision = engine.evaluate(pos)
        assert decision.should_exit
        assert decision.reason == ExitReason.PROFIT_TARGET
        assert decision.urgency == ExitUrgency.EXECUTE

    def test_profit_target_does_not_fire_below_threshold(self):
        engine = self._engine(profit_target_pct=0.5)
        pos = make_position(max_profit_per_contract=200.0, current_pnl=80.0, current_dte=15)
        decision = engine.evaluate(pos)
        assert not decision.should_exit

    # Rule 5: Time stop
    def test_time_stop_fires_after_threshold(self):
        engine = self._engine(time_stop_days=7)
        # Entry 10 days ago
        old_entry = time.time() - (10 * 86400)
        pos = make_position(entry_time=old_entry, current_dte=15)
        decision = engine.evaluate(pos)
        assert not decision.should_exit
        assert decision.needs_review
        assert decision.reason == ExitReason.TIME_STOP
        assert decision.urgency == ExitUrgency.EVALUATE

    def test_time_stop_does_not_fire_before_threshold(self):
        engine = self._engine(time_stop_days=21)
        recent_entry = time.time() - (5 * 86400)
        pos = make_position(entry_time=recent_entry, current_dte=15)
        decision = engine.evaluate(pos)
        assert decision.reason != ExitReason.TIME_STOP

    # Rule 6: Regime flip
    def test_regime_flip_fires_when_regime_changes_and_direction_conflicts(self):
        engine = self._engine()
        pos = make_position(direction="BULLISH", entry_regime=3, current_dte=15)
        context = {"regime_id": 7, "consensus_direction": "BEARISH"}
        decision = engine.evaluate(pos, context)
        assert not decision.should_exit
        assert decision.needs_review
        assert decision.reason == ExitReason.REGIME_FLIP
        assert decision.urgency == ExitUrgency.EVALUATE

    def test_regime_flip_does_not_fire_when_regime_same(self):
        engine = self._engine()
        pos = make_position(direction="BULLISH", entry_regime=3, current_dte=15)
        context = {"regime_id": 3, "consensus_direction": "BEARISH"}
        decision = engine.evaluate(pos, context)
        assert not decision.should_exit

    def test_no_context_no_regime_flip(self):
        engine = self._engine()
        pos = make_position(direction="BULLISH", entry_regime=3, current_dte=15)
        decision = engine.evaluate(pos, context=None)
        assert not decision.should_exit

    # Priority ordering
    def test_stop_loss_beats_profit_target(self):
        engine = self._engine()
        # Both stop-loss and profit-target conditions are met — stop-loss wins
        pos = make_position(
            max_profit_per_contract=200.0,
            max_loss_per_contract=50.0,
            current_pnl=-60.0,  # breaches stop-loss
            current_dte=15,
        )
        decision = engine.evaluate(pos)
        assert decision.reason == ExitReason.STOP_LOSS

    def test_evaluate_all_returns_one_per_position(self):
        engine = self._engine()
        positions = [make_position(f"t{i}", current_dte=15) for i in range(3)]
        decisions = engine.evaluate_all(positions)
        assert len(decisions) == 3

    def test_exits_required_filters_to_should_exit_only(self):
        engine = self._engine(stop_loss_pct=1.0)
        positions = [
            make_position("t1", max_loss_per_contract=200.0, current_pnl=-210.0),  # stop hit
            make_position("t2", current_pnl=10.0, current_dte=15),                  # no exit
        ]
        exits = engine.exits_required(positions)
        assert len(exits) == 1
        assert exits[0].trade_id == "t1"


# =============================================================================
# Roll logic (C3)
# =============================================================================

class TestRollEngine:
    def _engine(self, **kwargs) -> RollEngine:
        return RollEngine(**kwargs)

    def _profitable_near_expiry_position(self, **kwargs) -> OpenPosition:
        defaults = dict(
            structure="IRON_CONDOR",
            current_dte=10,
            max_profit_per_contract=200.0,
            max_loss_per_contract=300.0,
            current_pnl=80.0,  # 80/200 = 40% captured > 25% threshold
            entry_regime=5,
        )
        defaults.update(kwargs)
        return make_position(**defaults)

    def test_roll_recommended_when_all_criteria_pass(self):
        engine = self._engine(profit_threshold_pct=0.25, dte_threshold=14)
        pos = self._profitable_near_expiry_position()
        context = {"regime_id": 5, "next_expiry_premium": 1.50, "next_expiry_dte": 30}
        decision = engine.evaluate(pos, context)
        assert decision.should_roll
        assert decision.reject_reason is None
        assert decision.rationale

    def test_roll_rejected_when_insufficient_profit(self):
        engine = self._engine(profit_threshold_pct=0.25)
        pos = make_position(
            structure="IRON_CONDOR", current_dte=10,
            max_profit_per_contract=200.0, current_pnl=20.0,  # 10% < 25%
        )
        context = {"regime_id": 5}
        decision = engine.evaluate(pos, context)
        assert not decision.should_roll
        assert "Insufficient profit" in decision.reject_reason

    def test_roll_rejected_when_dte_too_high(self):
        engine = self._engine(dte_threshold=14)
        pos = make_position(
            structure="IRON_CONDOR", current_dte=20,  # > 14 threshold
            max_profit_per_contract=200.0, current_pnl=80.0,
        )
        decision = engine.evaluate(pos, {})
        assert not decision.should_roll
        assert "DTE" in decision.reject_reason

    def test_roll_rejected_when_regime_changed(self):
        engine = self._engine()
        pos = self._profitable_near_expiry_position(entry_regime=3)
        context = {"regime_id": 7}  # different regime
        decision = engine.evaluate(pos, context)
        assert not decision.should_roll
        assert "Regime" in decision.reject_reason
        assert not decision.regime_unchanged

    def test_roll_rejected_for_non_rollable_structure(self):
        engine = self._engine()
        pos = make_position(
            structure="LONG_CALL",  # not a short-premium structure
            current_dte=10, current_pnl=80.0,
        )
        decision = engine.evaluate(pos, {"regime_id": 5})
        assert not decision.should_roll
        assert "not rollable" in decision.reject_reason

    def test_roll_rejected_when_next_premium_below_floor(self):
        engine = self._engine(premium_floor_pct=0.30)
        pos = self._profitable_near_expiry_position()
        # entry_premium=2.00; floor = 2.00 × 0.30 = 0.60; next_premium = 0.10 < 0.60
        context = {"regime_id": 5, "next_expiry_premium": 0.10}
        decision = engine.evaluate(pos, context)
        assert not decision.should_roll
        assert "floor" in decision.reject_reason

    def test_decision_has_profit_captured_pct(self):
        engine = self._engine()
        pos = self._profitable_near_expiry_position()
        context = {"regime_id": 5, "next_expiry_dte": 30}
        decision = engine.evaluate(pos, context)
        assert 0.0 <= decision.profit_captured_pct <= 1.0

    def test_decision_has_suggested_dte_target(self):
        engine = self._engine()
        pos = self._profitable_near_expiry_position()
        context = {"regime_id": 5, "next_expiry_dte": 35}
        decision = engine.evaluate(pos, context)
        if decision.should_roll:
            assert decision.suggested_dte_target == 35

    def test_estimated_roll_credit_is_float(self):
        engine = self._engine()
        pos = self._profitable_near_expiry_position()
        context = {"regime_id": 5, "next_expiry_premium": 1.50, "next_expiry_dte": 30}
        decision = engine.evaluate(pos, context)
        assert isinstance(decision.estimated_roll_credit, float)
