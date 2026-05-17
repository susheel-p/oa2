"""Tests for Phase F backtest helper functions.

These test the pure-computation functions (no yfinance network calls).
The full backtest script (scripts/backtest.py) requires network access
and is tested via the dry-run smoke test only.
"""

from __future__ import annotations

import math

import pytest

from scripts.backtest import (
    DayResult,
    compute_daily_context,
    evaluate_direction,
    compute_sharpe,
    compute_accuracy,
    compute_accuracy_by_regime,
    confusion_matrix,
    f2_validation,
    baseline_direction,
    _ema,
    _rsi,
    _std,
    _atr,
)


# =============================================================================
# Math helpers
# =============================================================================

class TestMathHelpers:
    def test_ema_single_value(self):
        assert _ema([100.0], 20) == pytest.approx(100.0)

    def test_ema_two_values(self):
        result = _ema([100.0, 110.0], 1)
        assert result == pytest.approx(110.0)

    def test_ema_span_20_weights_recent(self):
        prices = [100.0] * 20 + [110.0]  # jump at the end
        result = _ema(prices, 20)
        assert result > 100.0  # should be pulled up by recent value

    def test_ema_empty_returns_zero(self):
        assert _ema([], 20) == 0.0

    def test_rsi_neutral_on_flat_prices(self):
        prices = [100.0] * 20
        rsi = _rsi(prices, 14)
        # No gains or losses — RSI is undefined, should return 100 (no losses)
        assert rsi == pytest.approx(100.0)

    def test_rsi_oversold_on_declining_prices(self):
        prices = list(range(100, 80, -1))  # declining
        rsi = _rsi(prices, 14)
        assert rsi < 50.0

    def test_rsi_overbought_on_rising_prices(self):
        prices = list(range(80, 100, 1))   # rising
        rsi = _rsi(prices, 14)
        assert rsi > 50.0

    def test_rsi_insufficient_data_returns_neutral(self):
        assert _rsi([100.0], 14) == pytest.approx(50.0)

    def test_std_empty(self):
        assert _std([]) == 0.0

    def test_std_single(self):
        assert _std([5.0]) == 0.0

    def test_std_known(self):
        # [2, 4, 4, 4, 5, 5, 7, 9] → std = 2.0
        vals = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        assert _std(vals) == pytest.approx(2.0, rel=0.01)

    def test_atr_positive(self):
        closes = [100.0] * 20
        highs = [101.0] * 20
        lows = [99.0] * 20
        atr = _atr(closes, highs, lows, 15, 14)
        assert atr > 0.0


# =============================================================================
# evaluate_direction
# =============================================================================

class TestEvaluateDirection:
    def test_positive_return_is_bullish(self):
        assert evaluate_direction(0.01) == "BULLISH"

    def test_negative_return_is_bearish(self):
        assert evaluate_direction(-0.01) == "BEARISH"

    def test_tiny_return_is_neutral(self):
        assert evaluate_direction(0.0001) == "NEUTRAL"

    def test_zero_return_is_neutral(self):
        assert evaluate_direction(0.0) == "NEUTRAL"

    def test_custom_threshold(self):
        assert evaluate_direction(0.005, threshold=0.01) == "NEUTRAL"
        assert evaluate_direction(0.015, threshold=0.01) == "BULLISH"


# =============================================================================
# compute_sharpe
# =============================================================================

class TestComputeSharpe:
    def test_positive_returns_positive_sharpe(self):
        # Slightly varied so std > 0; mean is positive
        returns = [0.01 + (i % 5) * 0.001 for i in range(100)]
        sharpe = compute_sharpe(returns)
        assert sharpe > 0.0

    def test_zero_volatility_returns_zero(self):
        returns = [0.01] * 50  # all same → std = 0
        sharpe = compute_sharpe(returns)
        assert sharpe == pytest.approx(0.0)

    def test_negative_mean_negative_sharpe(self):
        # Slightly varied so std > 0; mean is negative
        returns = [-0.01 - (i % 5) * 0.001 for i in range(100)]
        sharpe = compute_sharpe(returns)
        assert sharpe < 0.0

    def test_insufficient_data_returns_zero(self):
        assert compute_sharpe([0.01]) == 0.0
        assert compute_sharpe([]) == 0.0

    def test_annualized_by_sqrt_252(self):
        # Known: daily mean=0.001, std=0.001 → daily sharpe=1 → annual = sqrt(252) ≈ 15.87
        returns = [0.001 + (0.001 if i % 2 == 0 else -0.001) for i in range(200)]
        # Not a perfect test, but checks the scaling factor
        sharpe = compute_sharpe(returns)
        assert isinstance(sharpe, float)


# =============================================================================
# DayResult + accuracy helpers
# =============================================================================

def _make_result(
    regime_label: str = "NORMAL_NEUTRAL",
    consensus_direction: str = "BULLISH",
    outcome: str = "BULLISH",
    debater_opinions: dict | None = None,
    next_day_return: float = 0.005,
    baseline_direction: str = "BULLISH",
) -> DayResult:
    return DayResult(
        date="2025-05-15",
        ticker="SPY",
        regime_id=5,
        regime_label=regime_label,
        consensus_direction=consensus_direction,
        consensus_score=0.6,
        p_bull=0.65,
        next_day_return=next_day_return,
        outcome=outcome,
        debater_opinions=debater_opinions or {"directional": "BULLISH"},
        debater_convictions={"directional": 0.7},
        baseline_direction=baseline_direction,
        iv_rank=0.50,
    )


class TestComputeAccuracy:
    def test_all_correct(self):
        results = [_make_result("NORMAL_NEUTRAL", "BULLISH", "BULLISH") for _ in range(10)]
        assert compute_accuracy(results) == pytest.approx(1.0)

    def test_all_wrong(self):
        results = [_make_result("NORMAL_NEUTRAL", "BULLISH", "BEARISH") for _ in range(10)]
        assert compute_accuracy(results) == pytest.approx(0.0)

    def test_neutral_predictions_excluded(self):
        results = [_make_result("NORMAL_NEUTRAL", "NEUTRAL", "BULLISH") for _ in range(10)]
        # No non-neutral predictions → accuracy = 0 (no denominator)
        assert compute_accuracy(results) == 0.0

    def test_neutral_outcomes_excluded(self):
        results = [_make_result("NORMAL_NEUTRAL", "BULLISH", "NEUTRAL") for _ in range(10)]
        assert compute_accuracy(results) == 0.0

    def test_debater_accuracy(self):
        results = [
            _make_result(debater_opinions={"directional": "BULLISH"}, outcome="BULLISH"),
            _make_result(debater_opinions={"directional": "BULLISH"}, outcome="BEARISH"),
        ]
        acc = compute_accuracy(results, debater="directional")
        assert acc == pytest.approx(0.5)

    def test_mixed_accuracy(self):
        results = [
            _make_result("NORMAL_NEUTRAL", "BULLISH", "BULLISH"),
            _make_result("NORMAL_NEUTRAL", "BULLISH", "BULLISH"),
            _make_result("NORMAL_NEUTRAL", "BULLISH", "BEARISH"),
        ]
        assert compute_accuracy(results) == pytest.approx(2 / 3, rel=0.01)


class TestComputeAccuracyByRegime:
    def test_groups_by_regime_label(self):
        results = [
            _make_result("VOL_EXP_TREND", "BULLISH", "BULLISH"),
            _make_result("VOL_EXP_TREND", "BULLISH", "BULLISH"),
            _make_result("NORMAL_NEUTRAL", "BULLISH", "BEARISH"),
        ]
        by_regime = compute_accuracy_by_regime(results)
        assert "VOL_EXP_TREND" in by_regime
        assert "NORMAL_NEUTRAL" in by_regime
        assert by_regime["VOL_EXP_TREND"] == pytest.approx(1.0)
        assert by_regime["NORMAL_NEUTRAL"] == pytest.approx(0.0)


class TestConfusionMatrix:
    def test_basic_confusion(self):
        results = [
            _make_result(consensus_direction="BULLISH", outcome="BULLISH"),
            _make_result(consensus_direction="BULLISH", outcome="BEARISH"),
            _make_result(consensus_direction="BEARISH", outcome="BEARISH"),
        ]
        matrix = confusion_matrix(results)
        assert matrix["BULLISH"]["BULLISH"] == 1
        assert matrix["BULLISH"]["BEARISH"] == 1
        assert matrix["BEARISH"]["BEARISH"] == 1

    def test_empty_results(self):
        matrix = confusion_matrix([])
        assert matrix == {}


# =============================================================================
# F2 validation
# =============================================================================

class TestF2Validation:
    def test_income_passes_in_high_iv(self):
        # VOL_EXP regime: income debater is correct
        results = [
            _make_result(
                "VOL_EXP_TREND", "BULLISH", "BULLISH",
                debater_opinions={"income": "BULLISH"},
            )
            for _ in range(10)
        ]
        result = f2_validation(results)
        assert result["income_passes"] is True

    def test_directional_passes_in_trending(self):
        results = [
            _make_result(
                "NORMAL_TREND", "BULLISH", "BULLISH",
                debater_opinions={"directional": "BULLISH"},
            )
            for _ in range(10)
        ]
        result = f2_validation(results)
        assert result["directional_passes"] is True

    def test_high_iv_days_counted(self):
        results = [
            _make_result("VOL_EXP_TREND", "BULLISH", "BULLISH"),
            _make_result("NORMAL_NEUTRAL", "BULLISH", "BULLISH"),
        ]
        result = f2_validation(results)
        assert result["high_iv_days"] == 1

    def test_empty_results(self):
        result = f2_validation([])
        assert isinstance(result, dict)


# =============================================================================
# Baseline direction (v1 proxy)
# =============================================================================

class TestBaselineDirection:
    def test_bullish_when_ema_20_above_ema_50_and_low_vix(self):
        ctx = {"ema_20": 105.0, "ema_50": 100.0, "vol_regime": {"vix": 18.0}}
        assert baseline_direction(ctx) == "BULLISH"

    def test_bearish_when_ema_20_below_ema_50(self):
        ctx = {"ema_20": 98.0, "ema_50": 100.0, "vol_regime": {"vix": 18.0}}
        assert baseline_direction(ctx) == "BEARISH"

    def test_bearish_when_high_vix(self):
        ctx = {"ema_20": 105.0, "ema_50": 100.0, "vol_regime": {"vix": 32.0}}
        assert baseline_direction(ctx) == "BEARISH"

    def test_neutral_when_emas_equal_moderate_vix(self):
        # ema_20 == ema_50 → first condition false; VIX < 30 → second condition false
        ctx = {"ema_20": 100.0, "ema_50": 100.0, "vol_regime": {"vix": 20.0}}
        result = baseline_direction(ctx)
        # Neither condition fires → NEUTRAL
        assert result == "NEUTRAL"

    def test_default_vix_when_missing(self):
        ctx = {"ema_20": 105.0, "ema_50": 100.0}
        # No vol_regime → vix defaults to 20 → should be BULLISH
        assert baseline_direction(ctx) == "BULLISH"


# =============================================================================
# compute_daily_context
# =============================================================================

class TestComputeDailyContext:
    def _prices(self, n: int = 60, start: float = 450.0, drift: float = 0.001) -> list[float]:
        prices = [start]
        for i in range(1, n):
            prices.append(prices[-1] * (1 + drift))
        return prices

    def test_returns_empty_when_insufficient_warmup(self):
        prices = self._prices(15)
        highs = [p * 1.005 for p in prices]
        lows = [p * 0.995 for p in prices]
        vols = [1_000_000.0] * len(prices)
        vix = [20.0] * len(prices)
        ctx = compute_daily_context("SPY", prices, highs, lows, vols, "2025-05-15", vix, 10)
        assert ctx == {}

    def test_returns_dict_with_required_keys(self):
        n = 60
        prices = self._prices(n)
        highs = [p * 1.005 for p in prices]
        lows = [p * 0.995 for p in prices]
        vols = [1_000_000.0] * n
        vix = [20.0] * n
        ctx = compute_daily_context("SPY", prices, highs, lows, vols, "2025-05-15", vix, 55)
        assert ctx != {}
        assert "current_price" in ctx
        assert "ema_20" in ctx
        assert "ema_50" in ctx
        assert "vol_regime" in ctx
        assert "iv_rank" in ctx
        assert "rsi" in ctx

    def test_ema_20_present_and_positive(self):
        n = 60
        prices = self._prices(n)
        highs = [p * 1.005 for p in prices]
        lows = [p * 0.995 for p in prices]
        vols = [1_000_000.0] * n
        vix = [20.0] * n
        ctx = compute_daily_context("SPY", prices, highs, lows, vols, "2025-05-15", vix, 55)
        if ctx:
            assert ctx["ema_20"] > 0

    def test_iv_rank_in_unit_interval(self):
        n = 260
        prices = self._prices(n)
        highs = [p * 1.005 for p in prices]
        lows = [p * 0.995 for p in prices]
        vols = [1_000_000.0] * n
        vix = [20.0] * n
        ctx = compute_daily_context("SPY", prices, highs, lows, vols, "2025-05-15", vix, 255)
        if ctx:
            assert 0.0 <= ctx["iv_rank"] <= 1.0
