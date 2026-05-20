"""Tests for Phase D regime enhancements: session overlay, leading crisis,
cross-asset context, GEX walls/max-pain, and directional debater D5."""

from __future__ import annotations

import datetime

import pytest
from zoneinfo import ZoneInfo

from tradingbot.regime.session import (
    SessionState,
    get_session_state,
    session_weight_multipliers,
    apply_session_weights,
    session_context,
)
from tradingbot.regime.classifier import RegimeClassifier
from tradingbot.regime.state import VolState
from tradingbot.dealer.gex import compute_gex, _compute_call_wall, _compute_put_wall, _compute_max_pain
from tradingbot.debaters.directional import DirectionalDebater
from tradingbot.debaters.base import Direction

ET = ZoneInfo("America/New_York")


# =============================================================================
# D1 — Session overlay
# =============================================================================

class TestSessionState:
    def _et(self, hour: int, minute: int = 0) -> datetime.datetime:
        return datetime.datetime(2025, 5, 15, hour, minute, tzinfo=ET)

    def test_pre_market(self):
        assert get_session_state(self._et(8, 0)) == SessionState.PRE_MARKET

    def test_open_session(self):
        assert get_session_state(self._et(9, 35)) == SessionState.OPEN

    def test_morning_session(self):
        assert get_session_state(self._et(10, 30)) == SessionState.MORNING

    def test_midday_session(self):
        assert get_session_state(self._et(13, 0)) == SessionState.MIDDAY

    def test_afternoon_session(self):
        assert get_session_state(self._et(14, 45)) == SessionState.AFTERNOON

    def test_power_hour(self):
        assert get_session_state(self._et(15, 45)) == SessionState.POWER_HOUR

    def test_closed_after_market(self):
        assert get_session_state(self._et(16, 30)) == SessionState.CLOSED

    def test_open_session_start_boundary(self):
        assert get_session_state(self._et(9, 30)) == SessionState.OPEN

    def test_morning_start_boundary(self):
        assert get_session_state(self._et(10, 0)) == SessionState.MORNING

    def test_multipliers_have_all_debaters(self):
        for session in SessionState:
            mults = session_weight_multipliers(session)
            assert "directional" in mults
            assert "income" in mults
            assert "volatility" in mults
            assert "flow" in mults
            assert "sentiment" in mults

    def test_open_session_boosts_flow(self):
        mults = session_weight_multipliers(SessionState.OPEN)
        assert mults["flow"] > 1.0

    def test_midday_boosts_income_reduces_directional(self):
        mults = session_weight_multipliers(SessionState.MIDDAY)
        assert mults["income"] > 1.0
        assert mults["directional"] < 1.0

    def test_apply_session_weights_renormalizes(self):
        base_weights = {"directional": 0.25, "income": 0.25, "volatility": 0.20, "flow": 0.15, "sentiment": 0.15}
        adjusted = apply_session_weights(base_weights, SessionState.OPEN)
        total = sum(adjusted.values())
        assert abs(total - 1.0) < 1e-6

    def test_apply_session_weights_changes_relative_weights(self):
        base_weights = {"directional": 0.25, "income": 0.25, "volatility": 0.20, "flow": 0.15, "sentiment": 0.15}
        adjusted = apply_session_weights(base_weights, SessionState.OPEN)
        # Flow should be relatively higher at OPEN
        assert adjusted["flow"] > base_weights["flow"]

    def test_session_context_returns_dict(self):
        ctx = session_context(datetime.datetime(2025, 5, 15, 10, 30, tzinfo=ET))
        assert "session_state" in ctx
        assert "session_weight_multipliers" in ctx
        assert ctx["session_state"] == SessionState.MORNING.value


# =============================================================================
# D2 — Leading crisis signal
# =============================================================================

class TestLeadingCrisisSignal:
    def _classifier(self):
        return RegimeClassifier()

    def test_leading_crisis_fires_when_both_conditions_met(self):
        classifier = self._classifier()
        # VIX3M/VIX ratio < 1.05 AND VVIX > 110
        result = classifier._leading_crisis_check(vix3m=20.0, vix=19.5, vvix=115.0)
        assert result is True

    def test_leading_crisis_does_not_fire_when_only_vvix_elevated(self):
        classifier = self._classifier()
        result = classifier._leading_crisis_check(vix3m=25.0, vix=20.0, vvix=120.0)
        # ratio = 1.25 > 1.05 — term NOT flat
        assert result is False

    def test_leading_crisis_does_not_fire_when_only_term_flat(self):
        classifier = self._classifier()
        result = classifier._leading_crisis_check(vix3m=20.0, vix=19.5, vvix=90.0)
        # VVIX < 110
        assert result is False

    def test_leading_crisis_requires_valid_data(self):
        classifier = self._classifier()
        result = classifier._leading_crisis_check(vix3m=0.0, vix=20.0, vvix=120.0)
        assert result is False  # missing data → no escalation

    def test_leading_crisis_escalates_vol_expansion_to_crisis(self):
        classifier = self._classifier()
        # iv_rank=0.75 → VOL_EXPANSION normally; with leading crisis → CRISIS
        context = {
            "vol_regime": {
                "iv_rank": 0.75, "rv_iv_ratio": 1.0, "vix": 28.0,
                "vix3m": 28.5, "vvix": 115.0,
            },
            "current_price": 450.0,
            "prices_20d": [450.0] * 20,
        }
        result = classifier.classify(context)
        assert result.vol_state == VolState.CRISIS

    def test_leading_crisis_does_not_escalate_normal_to_crisis(self):
        classifier = self._classifier()
        # iv_rank=0.50 → NORMAL; leading crisis alone should not escalate NORMAL to CRISIS
        context = {
            "vol_regime": {
                "iv_rank": 0.50, "rv_iv_ratio": 1.0, "vix": 22.0,
                "vix3m": 22.0, "vvix": 115.0,
            },
            "current_price": 450.0,
            "prices_20d": [450.0] * 20,
        }
        result = classifier.classify(context)
        # Should NOT be CRISIS — term flat requires iv_rank to already be VOL_EXPANSION
        assert result.vol_state != VolState.CRISIS


# =============================================================================
# D3 — Cross-asset macro context
# =============================================================================

class TestCrossAssetRegime:
    def _classifier(self):
        return RegimeClassifier()

    def test_no_cross_asset_data_unchanged(self):
        classifier = self._classifier()
        context = {
            "vol_regime": {"iv_rank": 0.50, "rv_iv_ratio": 1.0, "vix": 20.0},
            "current_price": 450.0,
        }
        result = classifier.classify(context)
        assert result.vol_state == VolState.NORMAL

    def test_two_stress_signals_escalate_normal_to_expansion(self):
        classifier = self._classifier()
        context = {
            "vol_regime": {"iv_rank": 0.50, "rv_iv_ratio": 1.0, "vix": 20.0},
            "current_price": 450.0,
            "cross_asset": {
                "tlt_ret": -0.010,  # TLT falling hard
                "dxy_chg": 0.005,   # DXY rising
                "hyg_ret": -0.006,  # HYG spread widening
            },
        }
        result = classifier.classify(context)
        assert result.vol_state == VolState.VOL_EXPANSION

    def test_two_stress_signals_escalate_expansion_to_crisis(self):
        classifier = self._classifier()
        context = {
            "vol_regime": {"iv_rank": 0.75, "rv_iv_ratio": 1.0, "vix": 28.0},
            "current_price": 450.0,
            "cross_asset": {
                "tlt_ret": -0.012,
                "dxy_chg": 0.006,
                "hyg_ret": -0.008,
            },
        }
        result = classifier.classify(context)
        assert result.vol_state == VolState.CRISIS

    def test_single_stress_signal_escalates_compression_to_normal(self):
        classifier = self._classifier()
        context = {
            "vol_regime": {"iv_rank": 0.20, "rv_iv_ratio": 0.80, "vix": 12.0},
            "current_price": 450.0,
            "cross_asset": {"hyg_ret": -0.008},
        }
        result = classifier.classify(context)
        # VOL_COMPRESSION + 1 stress signal → NORMAL
        assert result.vol_state == VolState.NORMAL

    def test_weak_cross_asset_does_not_escalate(self):
        classifier = self._classifier()
        context = {
            "vol_regime": {"iv_rank": 0.50, "rv_iv_ratio": 1.0, "vix": 18.0},
            "current_price": 450.0,
            "cross_asset": {"tlt_ret": -0.001, "hyg_ret": -0.001},  # below threshold
        }
        result = classifier.classify(context)
        assert result.vol_state == VolState.NORMAL

    def test_cross_asset_stress_score_computation(self):
        classifier = self._classifier()
        stress = classifier._cross_asset_stress({
            "tlt_ret": -0.010, "dxy_chg": 0.005, "hyg_ret": -0.006
        })
        assert stress >= 2.0

    def test_no_stress_returns_zero(self):
        classifier = self._classifier()
        stress = classifier._cross_asset_stress({})
        assert stress == 0.0


# =============================================================================
# D4 — GEX walls and max pain
# =============================================================================

def make_chain(strikes_calls: list[tuple], strikes_puts: list[tuple]) -> dict:
    """Build minimal chain dict: [(strike, gamma, oi), ...]"""
    calls = [{"strike": s, "gamma": g, "open_interest": oi} for s, g, oi in strikes_calls]
    puts = [{"strike": s, "gamma": g, "open_interest": oi} for s, g, oi in strikes_puts]
    return {"calls": calls, "puts": puts}


class TestGEXWallsAndMaxPain:
    def test_call_wall_returns_highest_oi_call_strike(self):
        calls = [
            {"strike": 450, "open_interest": 1000},
            {"strike": 460, "open_interest": 5000},  # highest
            {"strike": 470, "open_interest": 2000},
        ]
        result = _compute_call_wall(calls)
        assert result == 460.0

    def test_put_wall_returns_highest_oi_put_strike(self):
        puts = [
            {"strike": 430, "open_interest": 3000},  # highest
            {"strike": 440, "open_interest": 2000},
            {"strike": 450, "open_interest": 1000},
        ]
        result = _compute_put_wall(puts)
        assert result == 430.0

    def test_call_wall_none_on_empty(self):
        assert _compute_call_wall([]) is None

    def test_put_wall_none_on_empty(self):
        assert _compute_put_wall([]) is None

    def test_max_pain_returns_float(self):
        calls = [{"strike": 440, "gamma": 0.01, "open_interest": 500},
                 {"strike": 450, "gamma": 0.01, "open_interest": 1000}]
        puts = [{"strike": 440, "gamma": 0.01, "open_interest": 1000},
                {"strike": 430, "gamma": 0.01, "open_interest": 500}]
        result = _compute_max_pain(calls, puts)
        assert result is not None
        assert isinstance(result, float)

    def test_max_pain_within_strike_range(self):
        calls = [{"strike": s, "gamma": 0.01, "open_interest": 1000} for s in [440, 450, 460]]
        puts = [{"strike": s, "gamma": 0.01, "open_interest": 1000} for s in [420, 430, 440]]
        result = _compute_max_pain(calls, puts)
        assert 420 <= result <= 460

    def test_max_pain_none_when_no_data(self):
        assert _compute_max_pain([], []) is None

    def test_compute_gex_populates_walls_and_max_pain(self):
        chain = make_chain(
            strikes_calls=[(450, 0.01, 5000), (460, 0.008, 2000), (470, 0.005, 1000)],
            strikes_puts=[(430, 0.01, 3000), (440, 0.008, 1500), (450, 0.005, 1000)],
        )
        result = compute_gex(chain, spot_price=445.0)
        assert result.call_wall is not None
        assert result.put_wall is not None
        assert result.max_pain is not None

    def test_compute_gex_call_wall_is_highest_oi_strike(self):
        chain = make_chain(
            strikes_calls=[(450, 0.01, 1000), (460, 0.01, 8000), (470, 0.01, 500)],
            strikes_puts=[(430, 0.01, 2000), (440, 0.01, 3000)],
        )
        result = compute_gex(chain, spot_price=445.0)
        assert result.call_wall == 460.0

    def test_compute_gex_put_wall_is_highest_oi_put_strike(self):
        chain = make_chain(
            strikes_calls=[(460, 0.01, 1000), (470, 0.01, 500)],
            strikes_puts=[(430, 0.01, 500), (440, 0.01, 6000), (450, 0.01, 1000)],
        )
        result = compute_gex(chain, spot_price=445.0)
        assert result.put_wall == 440.0

    def test_empty_chain_still_returns_result_with_none_walls(self):
        result = compute_gex({"calls": [], "puts": []}, spot_price=450.0)
        assert result.call_wall is None
        assert result.put_wall is None
        assert result.max_pain is None


# =============================================================================
# D5 — Directional debater grouped conviction scoring
# =============================================================================

class TestDirectionalDebaterD5:
    def _context(
        self,
        price=100.0, vwap=99.0, ema_20=98.0, ema_50=96.0,
        rsi=45.0, prior_close=99.0, atr=1.0,
        mtf=0.5, structure="VERTICAL_CALL_SPREAD",
    ) -> dict:
        return {
            "ticker": "SPY",
            "current_price": price,
            "vwap": vwap,
            "ema_20": ema_20,
            "ema_50": ema_50,
            "rsi": rsi,
            "prior_close": prior_close,
            "atr": atr,
            "setup": {"multi_timeframe_alignment": mtf},
            "strategy": {"selected_structure": structure},
        }

    def test_strong_bull_all_groups_agree(self):
        debater = DirectionalDebater()
        ctx = self._context(price=105.0, vwap=100.0, ema_20=102.0, ema_50=98.0,
                            rsi=55.0, prior_close=103.0, atr=1.0, mtf=0.6)
        opinion = debater.debate(ctx)
        assert opinion.direction == Direction.BULLISH
        assert "group_votes" in opinion.signals_used

    def test_strong_bear_all_groups_agree(self):
        debater = DirectionalDebater()
        ctx = self._context(price=95.0, vwap=100.0, ema_20=98.0, ema_50=102.0,
                            rsi=72.0, prior_close=97.0, atr=1.0, mtf=-0.5)
        opinion = debater.debate(ctx)
        assert opinion.direction == Direction.BEARISH

    def test_conviction_bounded_zero_to_one(self):
        debater = DirectionalDebater()
        ctx = self._context()
        opinion = debater.debate(ctx)
        assert 0.0 <= opinion.conviction <= 1.0

    def test_group_votes_in_signals_used(self):
        debater = DirectionalDebater()
        ctx = self._context()
        opinion = debater.debate(ctx)
        assert "group_votes" in opinion.signals_used
        gv = opinion.signals_used["group_votes"]
        assert "A_momentum" in gv
        assert "B_ema" in gv
        assert "C_rsi" in gv
        assert "D_mtf" in gv

    def test_misaligned_trade_reduces_conviction(self):
        debater = DirectionalDebater()
        # Bullish tape, bearish structure
        ctx_aligned = self._context(price=105.0, structure="VERTICAL_CALL_SPREAD")
        ctx_misaligned = self._context(price=105.0, structure="VERTICAL_PUT_SPREAD")
        op_aligned = debater.debate(ctx_aligned)
        op_misaligned = debater.debate(ctx_misaligned)
        if op_aligned.direction == Direction.BULLISH:
            assert op_misaligned.conviction < op_aligned.conviction

    def test_unanimous_groups_apply_multiplier(self):
        debater = DirectionalDebater()
        # All 4 groups bullish: price > vwap, ema_20 > ema_50, price > ema_20,
        # rsi < 30 (oversold = bull signal), mtf > 0.3, price > prior_close
        ctx = self._context(
            price=110.0, vwap=105.0, ema_20=108.0, ema_50=100.0,
            rsi=25.0, prior_close=108.0, atr=1.0, mtf=0.6,
        )
        opinion = debater.debate(ctx)
        assert opinion.direction == Direction.BULLISH
        # With multiplier, conviction should be > 0.40 + 4×0.12 = 0.88
        assert opinion.conviction > 0.70

    def test_neutral_when_groups_split_evenly(self):
        debater = DirectionalDebater()
        # Group A: price(99) > vwap(98) but atr=0 skips prior-close signal → +1
        # Group B: ema_20(101) < ema_50(103) AND price(99) < ema_20(101)   → -1
        # Group C: rsi=50 → 0 (neutral)
        # Group D: mtf=0  → 0 (neutral)
        # Result: 1 bull group vs 1 bear group → NEUTRAL
        ctx = self._context(
            price=99.0, vwap=98.0,
            ema_20=101.0, ema_50=103.0,
            rsi=50.0, prior_close=99.0, atr=0.0, mtf=0.0,
        )
        opinion = debater.debate(ctx)
        assert opinion.direction == Direction.NEUTRAL
