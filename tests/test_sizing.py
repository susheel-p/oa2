"""Tests for Phase B sizing engine: Kelly, Greeks limits, CVaR."""

from __future__ import annotations

import pytest

from oa2.sizing.kelly import (
    KellyResult,
    compute_kelly,
    size_from_consensus,
    dte_scalar,
    _MIN_EDGE,
)
from oa2.sizing.limits import GreeksBook, LimitCheckResult
from oa2.sizing.cvar import CVaRChecker, CVaRResult


# =============================================================================
# Kelly sizer (B1 + B4)
# =============================================================================

class TestDTEScalar:
    def test_extreme_short_dte(self):
        assert dte_scalar(0) == 0.50
        assert dte_scalar(1) == 0.50
        assert dte_scalar(2) == 0.50

    def test_short_dte(self):
        assert dte_scalar(3) == 0.75
        assert dte_scalar(6) == 0.75

    def test_standard_dte(self):
        assert dte_scalar(7) == 1.00
        assert dte_scalar(21) == 1.00
        assert dte_scalar(45) == 1.00

    def test_long_dte(self):
        assert dte_scalar(46) == 0.75
        assert dte_scalar(90) == 0.75


class TestComputeKelly:
    def test_clear_edge_produces_contracts(self):
        result = compute_kelly(
            edge=0.60, max_profit=200.0, max_loss=100.0, dte=21, account_size=50_000
        )
        assert result.viable
        assert result.contracts >= 1

    def test_no_edge_returns_zero_contracts(self):
        result = compute_kelly(
            edge=0.50, max_profit=200.0, max_loss=100.0, dte=21, account_size=50_000
        )
        assert not result.viable
        assert result.contracts == 0
        assert result.reject_reason is not None

    def test_below_min_edge_rejects(self):
        result = compute_kelly(
            edge=_MIN_EDGE - 0.01, max_profit=200.0, max_loss=100.0, dte=21, account_size=50_000
        )
        assert not result.viable

    def test_zero_max_loss_rejects(self):
        result = compute_kelly(
            edge=0.65, max_profit=200.0, max_loss=0.0, dte=21, account_size=50_000
        )
        assert not result.viable
        assert "max_loss" in result.reject_reason

    def test_zero_max_profit_rejects(self):
        result = compute_kelly(
            edge=0.65, max_profit=0.0, max_loss=100.0, dte=21, account_size=50_000
        )
        assert not result.viable
        assert "max_profit" in result.reject_reason

    def test_dte_scaling_short_reduces_size(self):
        result_standard = compute_kelly(
            edge=0.65, max_profit=200.0, max_loss=100.0, dte=21, account_size=50_000
        )
        result_short = compute_kelly(
            edge=0.65, max_profit=200.0, max_loss=100.0, dte=2, account_size=50_000
        )
        assert result_short.contracts <= result_standard.contracts
        assert result_short.dte_scalar == 0.50

    def test_dte_scaling_long_reduces_size(self):
        result_standard = compute_kelly(
            edge=0.65, max_profit=200.0, max_loss=100.0, dte=21, account_size=50_000
        )
        result_long = compute_kelly(
            edge=0.65, max_profit=200.0, max_loss=100.0, dte=60, account_size=50_000
        )
        assert result_long.contracts <= result_standard.contracts
        assert result_long.dte_scalar == 0.75

    def test_kelly_fraction_reduces_size(self):
        result_25 = compute_kelly(
            edge=0.65, max_profit=200.0, max_loss=100.0, dte=21, account_size=50_000,
            kelly_fraction=0.25,
        )
        result_50 = compute_kelly(
            edge=0.65, max_profit=200.0, max_loss=100.0, dte=21, account_size=50_000,
            kelly_fraction=0.50,
        )
        assert result_50.contracts >= result_25.contracts

    def test_max_dollars_at_risk_consistent(self):
        result = compute_kelly(
            edge=0.65, max_profit=200.0, max_loss=100.0, dte=21, account_size=50_000
        )
        assert abs(result.max_dollars_at_risk - result.contracts * 100.0) < 0.01

    def test_result_fields_present(self):
        result = compute_kelly(
            edge=0.65, max_profit=200.0, max_loss=100.0, dte=21, account_size=50_000
        )
        assert isinstance(result, KellyResult)
        assert result.edge == 0.65
        assert result.odds == pytest.approx(2.0, rel=0.01)
        assert result.account_size == 50_000

    def test_negative_kelly_fraction_rejects(self):
        # Low odds + moderate edge can give negative Kelly (EV < 0)
        result = compute_kelly(
            edge=0.53, max_profit=50.0, max_loss=200.0, dte=21, account_size=50_000
        )
        assert not result.viable
        assert result.reject_reason is not None


class TestSizeFromConsensus:
    def test_bullish_direction_uses_p_bull(self):
        result = size_from_consensus(
            p_bull=0.70, direction="BULLISH",
            max_profit=200.0, max_loss=100.0, dte=21, account_size=50_000,
        )
        assert result.edge == pytest.approx(0.70)

    def test_bearish_direction_uses_1_minus_p_bull(self):
        result = size_from_consensus(
            p_bull=0.30, direction="BEARISH",
            max_profit=200.0, max_loss=100.0, dte=21, account_size=50_000,
        )
        assert result.edge == pytest.approx(0.70)

    def test_neutral_always_zero(self):
        result = size_from_consensus(
            p_bull=0.75, direction="NEUTRAL",
            max_profit=200.0, max_loss=100.0, dte=21, account_size=50_000,
        )
        assert not result.viable
        assert "NEUTRAL" in result.reject_reason

    def test_bullish_low_p_bull_rejects(self):
        result = size_from_consensus(
            p_bull=0.51, direction="BULLISH",
            max_profit=200.0, max_loss=100.0, dte=21, account_size=50_000,
        )
        assert not result.viable

    def test_bearish_high_p_bull_rejects(self):
        # p_bull=0.90 → bearish edge=0.10 → below min_edge
        result = size_from_consensus(
            p_bull=0.90, direction="BEARISH",
            max_profit=200.0, max_loss=100.0, dte=21, account_size=50_000,
        )
        assert not result.viable


# =============================================================================
# Greeks hard caps (B2)
# =============================================================================

class TestGreeksBook:
    def _book(self, account_size=50_000) -> GreeksBook:
        return GreeksBook(account_size=account_size)

    def test_empty_book_passes_any_small_trade(self):
        book = self._book()
        result = book.check_proposed(delta=5.0, vega=3.0, theta=-10.0, underlying="SPY")
        assert result.approved

    def test_delta_cap_breach_rejected(self):
        book = self._book(account_size=10_000)
        # cap = 10_000 × 0.30 = 3_000
        book.add_position("t1", "SPY", delta=2_500, vega=10.0, theta=-5.0)
        result = book.check_proposed(delta=600.0, vega=5.0, theta=-5.0, underlying="QQQ")
        assert not result.approved
        assert "delta" in result.reject_reason.lower()

    def test_vega_cap_breach_rejected(self):
        book = self._book()
        book.add_position("t1", "SPY", delta=10.0, vega=40.0, theta=-5.0)
        result = book.check_proposed(delta=5.0, vega=15.0, theta=-5.0, underlying="QQQ")
        assert not result.approved
        assert "vega" in result.reject_reason.lower()

    def test_theta_cap_breach_rejected(self):
        book = self._book(account_size=10_000)
        # theta cap = 10_000 × 0.02 = 200
        book.add_position("t1", "SPY", delta=0.0, vega=0.0, theta=-180.0)
        result = book.check_proposed(delta=0.0, vega=0.0, theta=-30.0, underlying="QQQ")
        assert not result.approved
        assert "theta" in result.reject_reason.lower()

    def test_single_name_delta_concentration_rejected(self):
        book = self._book(account_size=10_000)
        # delta cap = 3_000; single-name cap = 25% × 3_000 = 750
        book.add_position("t1", "TSLA", delta=600.0, vega=5.0, theta=-5.0)
        result = book.check_proposed(delta=200.0, vega=5.0, theta=-5.0, underlying="TSLA")
        assert not result.approved
        assert "TSLA" in result.reject_reason
        assert "delta" in result.reject_reason.lower()

    def test_single_name_vega_concentration_rejected(self):
        book = self._book()
        # vega cap = 50; single-name cap = 25% × 50 = 12.5
        book.add_position("t1", "AAPL", delta=5.0, vega=10.0, theta=-5.0)
        result = book.check_proposed(delta=5.0, vega=5.0, theta=-5.0, underlying="AAPL")
        assert not result.approved
        assert "AAPL" in result.reject_reason
        assert "vega" in result.reject_reason.lower()

    def test_position_added_and_reflected_in_net_greeks(self):
        book = self._book()
        book.add_position("t1", "SPY", delta=20.0, vega=5.0, theta=-8.0)
        assert book.net_delta == pytest.approx(20.0)
        assert book.net_vega == pytest.approx(5.0)
        assert book.net_theta == pytest.approx(-8.0)

    def test_remove_position_updates_net_greeks(self):
        book = self._book()
        book.add_position("t1", "SPY", delta=20.0, vega=5.0, theta=-8.0)
        book.add_position("t2", "QQQ", delta=10.0, vega=3.0, theta=-4.0)
        book.remove_position("t1")
        assert book.net_delta == pytest.approx(10.0)

    def test_clear_empties_book(self):
        book = self._book()
        book.add_position("t1", "SPY", delta=20.0, vega=5.0, theta=-8.0)
        book.clear()
        assert book.position_count() == 0
        assert book.net_delta == 0.0

    def test_approved_result_has_headroom(self):
        book = self._book()
        result = book.check_proposed(delta=5.0, vega=3.0, theta=-10.0, underlying="SPY")
        assert result.approved
        assert result.delta_headroom > 0
        assert result.vega_headroom > 0

    def test_summary_returns_all_keys(self):
        book = self._book()
        summary = book.summary()
        assert "net_delta" in summary
        assert "net_vega" in summary
        assert "net_theta" in summary
        assert "account_size" in summary

    def test_scale_to_fit_reduces_contracts(self):
        book = self._book(account_size=10_000)
        # delta cap = 3_000; each contract has delta=1_000
        result = book.scale_to_fit(
            delta=1_000.0, vega=5.0, theta=-5.0,
            underlying="SPY", contracts_requested=5,
        )
        # 5 contracts = 5_000 delta → breach; should return 2 or 3
        assert 0 < result < 5

    def test_scale_to_fit_returns_zero_when_even_one_breaches(self):
        book = self._book(account_size=1_000)
        # delta cap = 300; a single contract has delta=500
        result = book.scale_to_fit(
            delta=500.0, vega=0.0, theta=0.0,
            underlying="SPY", contracts_requested=3,
        )
        assert result == 0

    def test_scale_to_fit_returns_requested_when_all_fit(self):
        book = self._book(account_size=100_000)
        result = book.scale_to_fit(
            delta=1.0, vega=0.1, theta=-0.1,
            underlying="SPY", contracts_requested=5,
        )
        assert result == 5


# =============================================================================
# CVaR stress check (B3)
# =============================================================================

class TestCVaRChecker:
    def _checker(self, account_size=50_000, budget_pct=0.05) -> CVaRChecker:
        return CVaRChecker(account_size=account_size, budget_pct=budget_pct)

    def test_small_trade_passes_all_scenarios(self):
        checker = self._checker()
        result = checker.check(
            delta=10.0, vega=3.0, price=450.0, contracts=1
        )
        assert result.approved
        assert result.reject_reason is None

    def test_large_long_delta_fails_underlying_down_scenarios(self):
        checker = self._checker(account_size=10_000, budget_pct=0.05)
        # budget = $500; delta=1000 × 1 contract × 5% move = -$500 × 450 × 0.05 ... let me compute
        # dPL = delta × price × ds% = 200 × 100 × (-0.05) = -1000 → breaches $500 budget
        result = checker.check(
            delta=200.0, vega=5.0, price=100.0, contracts=1
        )
        assert not result.approved
        assert "underlying" in result.reject_reason.lower() or "vix" in result.reject_reason.lower() or "spike" in result.reject_reason.lower()

    def test_large_vega_fails_iv_spike_scenarios(self):
        checker = self._checker(account_size=10_000, budget_pct=0.05)
        # budget = $500; vega=100 per 1% × 10 pts VIX → -$1000 (short vega)
        result = checker.check(
            delta=0.0, vega=-60.0, price=450.0, contracts=1
        )
        assert not result.approved

    def test_five_scenarios_always_returned(self):
        checker = self._checker()
        result = checker.check(delta=5.0, vega=2.0, price=450.0, contracts=1)
        assert len(result.scenarios) == 5

    def test_scenarios_have_required_fields(self):
        checker = self._checker()
        result = checker.check(delta=5.0, vega=2.0, price=450.0, contracts=1)
        for s in result.scenarios:
            assert s.name
            assert s.label
            assert isinstance(s.pnl_total, float)
            assert isinstance(s.breaches_budget, bool)

    def test_budget_dollars_consistent_with_account(self):
        checker = self._checker(account_size=50_000, budget_pct=0.05)
        result = checker.check(delta=5.0, vega=2.0, price=450.0, contracts=1)
        assert result.budget_dollars == pytest.approx(2500.0)

    def test_book_exposure_added_to_scenario_pnl(self):
        checker = self._checker(account_size=10_000, budget_pct=0.05)
        # Without book: small trade passes
        result_no_book = checker.check(delta=10.0, vega=0.0, price=100.0, contracts=1)
        # With large adverse book: should fail
        result_with_book = checker.check(
            delta=10.0, vega=0.0, price=100.0, contracts=1,
            book_delta=300.0, book_vega=0.0,
        )
        # Large book_delta should make scenario worse
        if result_no_book.approved:
            # The book_delta adds adverse P&L
            total_no_book = result_no_book.worst_pnl
            total_with_book = result_with_book.worst_pnl
            assert total_with_book <= total_no_book

    def test_max_contracts_within_budget(self):
        checker = self._checker(account_size=50_000, budget_pct=0.05)
        n = checker.max_contracts_within_budget(
            delta=50.0, vega=5.0, price=450.0, requested=10
        )
        assert 0 <= n <= 10

    def test_max_contracts_zero_when_even_one_fails(self):
        checker = self._checker(account_size=1_000, budget_pct=0.01)
        # budget = $10; even 1 contract with delta=100 and -5% move = $500 loss
        n = checker.max_contracts_within_budget(
            delta=100.0, vega=0.0, price=100.0, requested=5
        )
        assert n == 0

    def test_worst_scenario_name_populated_on_failure(self):
        checker = self._checker(account_size=5_000, budget_pct=0.02)
        result = checker.check(delta=200.0, vega=10.0, price=100.0, contracts=1)
        if not result.approved:
            assert result.worst_scenario is not None

    def test_correlation_spike_scenario_present(self):
        checker = self._checker()
        result = checker.check(delta=5.0, vega=2.0, price=450.0, contracts=1)
        names = [s.name for s in result.scenarios]
        assert "correlation_spike" in names

    def test_viable_property_mirrors_approved(self):
        checker = self._checker()
        result = checker.check(delta=5.0, vega=2.0, price=450.0, contracts=1)
        assert result.viable == result.approved
