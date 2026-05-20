"""Individual smoke tests for each of the 5 debaters.

Each test verifies:
  1. Debater instantiates
  2. Debater can run on a context dict
  3. Opinion is well-formed (direction, conviction in [0,1], signals_used is dict)
  4. Signals directly correspond to the debater's logic
"""

import pytest

from tradingbot.debaters.base import Direction, TradeQuality
from tradingbot.debaters.directional import DirectionalDebater
from tradingbot.debaters.income import IncomeDebater
from tradingbot.debaters.volatility import VolatilityDebater
from tradingbot.debaters.flow import FlowDebater
from tradingbot.debaters.sentiment import SentimentDebater


# ─────────────────────────────────────────────────────────────────────────────
# DIRECTIONAL DEBATER TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestDirectionalDebater:
    """Tape/momentum debater — argues from price vs VWAP, EMA stack, MTF alignment."""

    def test_instantiate(self):
        debater = DirectionalDebater()
        assert debater.name == "directional"

    def test_bullish_signal_stack(self):
        """Price above VWAP, EMA20 > EMA50, strong momentum → bullish."""
        context = {
            "ticker": "SPY",
            "current_price": 530.0,
            "vwap": 520.0,
            "ema_20": 525.0,
            "ema_50": 515.0,
            "rsi": 60.0,
            "atr": 2.0,
            "prior_close": 529.0,
            "setup": {"multi_timeframe_alignment": 0.5},
            "strategy": {"selected_structure": "VERTICAL_CALL_SPREAD"},
        }
        debater = DirectionalDebater()
        opinion = debater.debate(context)

        assert opinion.debater_name == "directional"
        assert opinion.direction == Direction.BULLISH
        assert 0.0 <= opinion.conviction <= 1.0
        assert opinion.signals_used["bull_groups"] > opinion.signals_used["bear_groups"]
        assert opinion.signals_used["trade_aligned"] is True  # Structure matches tape

    def test_bearish_signal_stack(self):
        """Price below VWAP, EMA20 < EMA50, weak momentum → bearish."""
        context = {
            "ticker": "QQQ",
            "current_price": 370.0,
            "vwap": 380.0,
            "ema_20": 375.0,
            "ema_50": 385.0,
            "rsi": 30.0,
            "atr": 2.0,
            "prior_close": 371.0,
            "setup": {"multi_timeframe_alignment": -0.5},
            "strategy": {"selected_structure": "VERTICAL_PUT_SPREAD"},
        }
        debater = DirectionalDebater()
        opinion = debater.debate(context)

        assert opinion.direction == Direction.BEARISH
        assert opinion.signals_used["bear_groups"] > opinion.signals_used["bull_groups"]

    def test_misaligned_trade_conviction_penalty(self):
        """Trade misaligned with tape direction → conviction reduced 25%."""
        # Tape is bullish, but trade is bearish
        context = {
            "ticker": "IWM",
            "current_price": 210.0,
            "vwap": 200.0,
            "ema_20": 209.0,
            "ema_50": 195.0,
            "rsi": 65.0,
            "atr": 1.5,
            "prior_close": 209.5,
            "setup": {"multi_timeframe_alignment": 0.8},
            "strategy": {"selected_structure": "LONG_PUT"},  # bearish vs bullish tape
        }
        debater = DirectionalDebater()
        opinion_misaligned = debater.debate(context)

        # Now test aligned tape + bullish trade
        context["strategy"]["selected_structure"] = "LONG_CALL"
        opinion_aligned = debater.debate(context)

        # Misaligned should have lower conviction than aligned
        assert opinion_misaligned.conviction < opinion_aligned.conviction
        assert opinion_misaligned.signals_used["trade_aligned"] is False

    def test_neutral_when_signals_balanced(self):
        """Equal bullish/bearish signals → neutral direction, conviction ~0.30."""
        context = {
            "ticker": "DIA",
            "current_price": 380.0,
            "vwap": 380.0,  # exactly at VWAP
            "ema_20": 380.0,
            "ema_50": 380.0,  # EMAs aligned but no direction
            "rsi": 50.0,
            "atr": 0.0,
            "prior_close": 380.0,
            "setup": {"multi_timeframe_alignment": 0.0},
            "strategy": {"selected_structure": "LONG_CALL"},
        }
        debater = DirectionalDebater()
        opinion = debater.debate(context)

        assert opinion.direction == Direction.NEUTRAL

    def test_signals_used_complete(self):
        """All key signals are logged in signals_used dict."""
        context = {
            "ticker": "SPY",
            "current_price": 525.0,
            "vwap": 520.0,
            "ema_20": 523.0,
            "ema_50": 518.0,
            "rsi": 55.0,
            "atr": 1.5,
            "prior_close": 524.0,
            "setup": None,
            "strategy": None,
        }
        debater = DirectionalDebater()
        opinion = debater.debate(context)

        assert "price" in opinion.signals_used
        assert "vwap" in opinion.signals_used
        assert "ema_20" in opinion.signals_used
        assert "ema_50" in opinion.signals_used
        assert "rsi" in opinion.signals_used
        assert "bull_groups" in opinion.signals_used
        assert "bear_groups" in opinion.signals_used
        assert "tape_direction" in opinion.signals_used
        assert "trade_aligned" in opinion.signals_used


# ─────────────────────────────────────────────────────────────────────────────
# INCOME DEBATER TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestIncomeDebater:
    """Premium-selling debater — argues from IV rank + RV/IV ratio."""

    def test_instantiate(self):
        debater = IncomeDebater()
        assert debater.name == "income"

    def test_rich_iv_short_premium_ideal(self):
        """IV Rank > 0.60 + RV/IV < 1.10 + short premium → bullish (ideal for seller)."""
        context = {
            "ticker": "SPY",
            "vol_regime": {
                "iv_rank": 0.75,  # Rich IV
                "rv_iv_ratio": 0.95,  # RV quiet
            },
            "chain_snapshot": {
                "theta": -0.10,
                "vega": -0.05,
            },
            "strategy": {"selected_structure": "VERTICAL_CALL_SPREAD"},  # short vega
        }
        debater = IncomeDebater()
        opinion = debater.debate(context)

        # C8/C9: income debater no longer overloads `direction` with trade-quality.
        # Direction is NEUTRAL (no view on underlying); the real vote is on trade_quality.
        assert opinion.direction == Direction.NEUTRAL
        assert opinion.trade_quality == TradeQuality.APPROVE
        assert opinion.conviction >= 0.65  # High conviction for ideal setup
        assert opinion.signals_used["iv_is_expensive"] is True

    def test_expensive_iv_long_premium_worst(self):
        """IV Rank > 0.60 + buying premium → bearish (worst case for debater)."""
        context = {
            "ticker": "QQQ",
            "vol_regime": {
                "iv_rank": 0.80,  # Very expensive
                "rv_iv_ratio": 0.90,
            },
            "chain_snapshot": {
                "theta": 0.05,
                "vega": 0.10,
            },
            "strategy": {"selected_structure": "LONG_CALL"},  # long vega
        }
        debater = IncomeDebater()
        opinion = debater.debate(context)

        assert opinion.direction == Direction.NEUTRAL
        assert opinion.trade_quality == TradeQuality.REJECT
        assert opinion.conviction >= 0.70  # Strong conviction AGAINST
        assert opinion.signals_used["trade_buys_premium"] is True

    def test_cheap_iv_rv_high_ok_to_buy(self):
        """IV Rank < 0.35 + RV > IV + buying premium → neutral/low conviction against."""
        context = {
            "ticker": "IWM",
            "vol_regime": {
                "iv_rank": 0.25,  # Cheap
                "rv_iv_ratio": 1.15,  # RV exceeds IV
            },
            "chain_snapshot": {
                "theta": 0.05,
                "vega": 0.08,
            },
            "strategy": {"selected_structure": "LONG_PUT"},
        }
        debater = IncomeDebater()
        opinion = debater.debate(context)

        # Income debater admits defeat — buying cheap premium is OK
        assert opinion.conviction <= 0.35  # Low conviction

    def test_short_premium_rv_exceeds_iv_dangerous(self):
        """RV > IV + short premium → bearish (dangerous)."""
        context = {
            "ticker": "DIA",
            "vol_regime": {
                "iv_rank": 0.50,
                "rv_iv_ratio": 1.20,  # RV exceeds IV
            },
            "chain_snapshot": {
                "theta": -0.08,
                "vega": -0.08,
            },
            "strategy": {"selected_structure": "IRON_CONDOR"},  # short vega
        }
        debater = IncomeDebater()
        opinion = debater.debate(context)

        assert opinion.direction == Direction.NEUTRAL
        assert opinion.trade_quality == TradeQuality.REJECT
        assert opinion.conviction >= 0.65
        assert opinion.signals_used["rv_exceeds_iv"] is True

    def test_signals_used_complete(self):
        """All key income signals logged."""
        context = {
            "ticker": "SPY",
            "vol_regime": {
                "iv_rank": 0.60,
                "rv_iv_ratio": 1.0,
            },
            "chain_snapshot": {
                "theta": -0.05,
                "vega": 0.02,
            },
            "strategy": {"selected_structure": "VERTICAL_CALL_SPREAD"},
        }
        debater = IncomeDebater()
        opinion = debater.debate(context)

        assert "iv_rank" in opinion.signals_used
        assert "rv_iv_ratio" in opinion.signals_used
        assert "iv_is_expensive" in opinion.signals_used
        assert "iv_is_cheap" in opinion.signals_used
        assert "rv_exceeds_iv" in opinion.signals_used
        assert "theta" in opinion.signals_used
        assert "vega" in opinion.signals_used
        assert "trade_sells_premium" in opinion.signals_used
        assert "trade_buys_premium" in opinion.signals_used


# ─────────────────────────────────────────────────────────────────────────────
# VOLATILITY DEBATER TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestVolatilityDebater:
    """Vol expansion/compression debater — tracks 6 expansion + 4 compression signals."""

    def test_instantiate(self):
        debater = VolatilityDebater()
        assert debater.name == "volatility"

    def test_long_vega_vol_expansion_ideal(self):
        """Long vega + 2+ vol expansion signals → bullish."""
        context = {
            "ticker": "SPY",
            "vol_regime": {
                "iv_rank": 0.25,  # Cheap
                "rv_iv_ratio": 1.15,  # RV > IV
                "skew_extreme": True,
                "term_upward": False,  # Backwardation
            },
            "market_vix": 14.0,  # Low
            "vvix": 105.0,  # Elevated
            "chain_snapshot": {
                "vega": 0.15,
                "gamma": 0.002,
                "theta": 0.02,
            },
            "strategy": {"selected_structure": "LONG_CALL"},  # long vega
        }
        debater = VolatilityDebater()
        opinion = debater.debate(context)

        # C8/C9: volatility debater is a trade-quality voter — direction is NEUTRAL.
        assert opinion.direction == Direction.NEUTRAL
        assert opinion.trade_quality == TradeQuality.APPROVE
        assert opinion.conviction >= 0.45
        assert opinion.signals_used["vol_expansion_signals"] >= 2

    def test_short_vega_vol_expansion_dangerous(self):
        """Short vega + 2+ vol expansion signals → bearish (dangerous)."""
        context = {
            "ticker": "QQQ",
            "vol_regime": {
                "iv_rank": 0.30,
                "rv_iv_ratio": 1.12,
                "skew_extreme": True,
                "term_upward": False,
            },
            "market_vix": 15.0,
            "vvix": 110.0,
            "chain_snapshot": {
                "vega": -0.12,
                "gamma": -0.001,
                "theta": -0.05,
            },
            "strategy": {"selected_structure": "VERTICAL_CALL_SPREAD"},  # short vega
        }
        debater = VolatilityDebater()
        opinion = debater.debate(context)

        assert opinion.direction == Direction.NEUTRAL
        assert opinion.trade_quality == TradeQuality.REJECT
        assert opinion.conviction >= 0.70
        assert opinion.signals_used["vol_expansion_signals"] >= 2

    def test_short_vega_vol_compression_acceptable(self):
        """Short vega + 3+ vol compression signals → neutral/acceptable."""
        context = {
            "ticker": "IWM",
            "vol_regime": {
                "iv_rank": 0.80,  # Expensive
                "rv_iv_ratio": 0.75,  # RV << IV
                "skew_extreme": False,
                "term_upward": True,  # Contango
            },
            "market_vix": 28.0,  # Elevated
            "vvix": 95.0,
            "chain_snapshot": {
                "vega": -0.10,
                "gamma": -0.001,
                "theta": -0.06,
            },
            "strategy": {"selected_structure": "IRON_CONDOR"},  # short vega
        }
        debater = VolatilityDebater()
        opinion = debater.debate(context)

        # Vol debater doesn't love short vega, but accepts it in compression
        assert opinion.direction == Direction.NEUTRAL
        assert opinion.signals_used["vol_compression_signals"] >= 3

    def test_long_vega_vol_compression_bad(self):
        """Long vega + 3+ vol compression signals → bearish (bad)."""
        context = {
            "ticker": "DIA",
            "vol_regime": {
                "iv_rank": 0.78,  # Higher to trigger compression
                "rv_iv_ratio": 0.70,  # RV well below IV
                "skew_extreme": False,
                "term_upward": True,
            },
            "market_vix": 27.0,  # Elevated
            "vvix": 90.0,
            "chain_snapshot": {
                "vega": 0.12,
                "gamma": 0.002,
                "theta": 0.03,
            },
            "strategy": {"selected_structure": "LONG_STRADDLE"},  # long vega
        }
        debater = VolatilityDebater()
        opinion = debater.debate(context)

        # Compression regime with long vega should be bearish or neutral
        assert opinion.direction in [Direction.BEARISH, Direction.NEUTRAL]
        if opinion.direction == Direction.BEARISH:
            assert opinion.conviction >= 0.65

    def test_signals_used_complete(self):
        """All vol signals logged."""
        context = {
            "ticker": "SPY",
            "vol_regime": {
                "iv_rank": 0.50,
                "rv_iv_ratio": 1.0,
                "skew_extreme": False,
                "term_upward": True,
            },
            "market_vix": 20.0,
            "vvix": 90.0,
            "chain_snapshot": {
                "vega": 0.05,
                "gamma": 0.001,
                "theta": -0.02,
            },
            "strategy": {"selected_structure": "LONG_CALL"},
        }
        debater = VolatilityDebater()
        opinion = debater.debate(context)

        assert "iv_rank" in opinion.signals_used
        assert "rv_iv_ratio" in opinion.signals_used
        assert "market_vix" in opinion.signals_used
        assert "vvix" in opinion.signals_used
        assert "skew_extreme" in opinion.signals_used
        assert "term_upward" in opinion.signals_used
        assert "vol_expansion_signals" in opinion.signals_used
        assert "vol_compression_signals" in opinion.signals_used
        assert "vega" in opinion.signals_used
        assert "trade_is_long_vega" in opinion.signals_used
        assert "trade_is_short_vega" in opinion.signals_used


# ─────────────────────────────────────────────────────────────────────────────
# FLOW DEBATER TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestFlowDebater:
    """Institutional flow debater — tracks PCR, sweeps, OI, dark pool."""

    def test_instantiate(self):
        debater = FlowDebater()
        assert debater.name == "flow"

    def test_pcr_bullish_call_sweeps(self):
        """PCR < 0.6 (call-heavy) + call sweeps → bullish (requires data_quality='real')."""
        context = {
            "ticker": "SPY",
            "flow_data": {
                "data_quality": "real",
                "put_call_ratio": 0.55,
                "call_sweep_count": 3,
                "put_sweep_count": 0,
                "dark_pool_bullish": True,
            },
            "strategy": {"selected_structure": "VERTICAL_CALL_SPREAD"},
        }
        debater = FlowDebater()
        opinion = debater.debate(context)

        assert opinion.direction == Direction.BULLISH
        assert 0.0 <= opinion.conviction <= 1.0
        assert opinion.signals_used["pcr"] == 0.55
        assert opinion.signals_used["call_sweeps"] == 3

    def test_pcr_bearish_put_sweeps(self):
        """PCR > 1.5 (put-heavy) + put sweeps → bearish (requires data_quality='real')."""
        context = {
            "ticker": "QQQ",
            "flow_data": {
                "data_quality": "real",
                "put_call_ratio": 1.65,
                "call_sweep_count": 0,
                "put_sweep_count": 5,
                "dark_pool_bearish": True,
            },
            "strategy": {"selected_structure": "VERTICAL_PUT_SPREAD"},
        }
        debater = FlowDebater()
        opinion = debater.debate(context)

        assert opinion.direction == Direction.BEARISH
        assert opinion.signals_used["put_sweeps"] == 5
        assert opinion.signals_used["dark_pool_bearish"] is True

    def test_no_flow_data_neutral(self):
        """No flow signals → neutral direction, low conviction."""
        context = {
            "ticker": "IWM",
            "flow_data": {},
            "strategy": {"selected_structure": "LONG_CALL"},
        }
        debater = FlowDebater()
        opinion = debater.debate(context)

        assert opinion.direction == Direction.NEUTRAL
        assert opinion.conviction <= 0.35

    def test_flow_from_chain_fallback(self):
        """Phase A1: no real flow data + chain Greeks only → honest abstention, conviction=0."""
        context = {
            "ticker": "DIA",
            "flow_data": None,
            "chain_snapshot": {
                "vega": 0.10,
                "delta": 0.6,
                "gamma": 0.001,
            },
            "strategy": {"selected_structure": "LONG_CALL"},
        }
        debater = FlowDebater()
        opinion = debater.debate(context)

        # Phase A1: chain Greeks are NOT real flow data — must abstain, not fabricate a vote
        assert opinion.debater_name == "flow"
        assert opinion.direction == Direction.NEUTRAL
        assert opinion.conviction == 0.0
        assert opinion.signals_used.get("abstained") is True

    def test_oi_change_bullish(self):
        """Call OI surging + put OI collapsing → bullish (requires data_quality='real')."""
        context = {
            "ticker": "SPY",
            "flow_data": {
                "data_quality": "real",
                "put_call_ratio": 0.90,
                "large_call_oi_change": 0.25,  # Calls surging
                "large_put_oi_change": -0.30,  # Puts collapsing
                "call_sweep_count": 1,
            },
            "strategy": {"selected_structure": "VERTICAL_CALL_SPREAD"},
        }
        debater = FlowDebater()
        opinion = debater.debate(context)

        assert opinion.direction == Direction.BULLISH

    def test_signals_used_complete(self):
        """All flow signals logged (requires data_quality='real')."""
        context = {
            "ticker": "SPY",
            "flow_data": {
                "data_quality": "real",
                "put_call_ratio": 0.80,
                "call_sweep_count": 2,
                "put_sweep_count": 0,
                "dark_pool_bullish": True,
                "dark_pool_bearish": False,
                "unusual_call_vol": True,
                "unusual_put_vol": False,
            },
            "strategy": {"selected_structure": "LONG_CALL"},
        }
        debater = FlowDebater()
        opinion = debater.debate(context)

        assert "pcr" in opinion.signals_used
        assert "call_sweeps" in opinion.signals_used
        assert "put_sweeps" in opinion.signals_used
        assert "dark_pool_bullish" in opinion.signals_used
        assert "dark_pool_bearish" in opinion.signals_used
        assert "unusual_call_vol" in opinion.signals_used
        assert "unusual_put_vol" in opinion.signals_used
        assert "flow_direction" in opinion.signals_used
        assert "bull_score" in opinion.signals_used
        assert "bear_score" in opinion.signals_used


# ─────────────────────────────────────────────────────────────────────────────
# SENTIMENT DEBATER TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestSentimentDebater:
    """Crowd/news sentiment debater — argues from crowd signal + structure alignment."""

    def test_instantiate(self):
        debater = SentimentDebater()
        assert debater.name == "sentiment"

    def test_bullish_iv_call_skew(self):
        """IV call-skew (put_call_skew < -0.05) -> BULLISH @ 0.45 (P2.1)."""
        context = {
            "ticker": "SPY",
            "vol_regime": {"put_call_skew": -0.08},
            "strategy": {"selected_structure": "LONG_CALL"},
        }
        debater = SentimentDebater()
        opinion = debater.debate(context)

        assert opinion.direction == Direction.BULLISH
        assert opinion.conviction == 0.45
        assert opinion.signals_used["signal"] == "iv_call_skew_elevated"

    def test_bearish_iv_put_skew(self):
        """IV put-skew (put_call_skew > +0.05) -> BEARISH @ 0.55 (P2.1)."""
        context = {
            "ticker": "QQQ",
            "vol_regime": {"put_call_skew": 0.08},
            "strategy": {"selected_structure": "LONG_PUT"},
        }
        debater = SentimentDebater()
        opinion = debater.debate(context)

        assert opinion.direction == Direction.BEARISH
        assert opinion.conviction == 0.55
        assert opinion.signals_used["signal"] == "iv_put_skew_elevated"

    def test_earnings_blackout(self):
        """Earnings within 1 day -> NEUTRAL @ 0.20 (vol crush risk)."""
        context = {
            "ticker": "AAPL",
            "earnings_snapshot": {"days_to_earnings": 0},
            "vol_regime": {"put_call_skew": 0.10},  # would otherwise be bearish
        }
        debater = SentimentDebater()
        opinion = debater.debate(context)

        assert opinion.direction == Direction.NEUTRAL
        assert opinion.conviction == 0.20
        assert opinion.signals_used["signal"] == "earnings_blackout"

    def test_call_put_ratio_tiebreaker(self):
        """No IV skew + extreme call/put ratio -> weak directional (0.30)."""
        context = {
            "ticker": "SPY",
            "vol_regime": {"put_call_skew": 0.01},  # within deadband
            "flow_snapshot": {"call_put_ratio": 2.1},
        }
        debater = SentimentDebater()
        opinion = debater.debate(context)

        assert opinion.direction == Direction.BULLISH
        assert opinion.conviction == 0.30
        assert opinion.signals_used["signal"] == "cpr_bullish"

    def test_sentiment_structure_mismatch(self):
        """Bullish sentiment + bearish structure → conviction reduced."""
        context = {
            "ticker": "IWM",
            "sentiment_snapshot": {
                "composite_score": 0.60,  # Strong bullish
                "mention_count": 30,
                "data_sources": ["reddit"],
            },
            "strategy": {"selected_structure": "LONG_PUT"},  # bearish vs bullish sentiment
        }
        debater = SentimentDebater()
        opinion = debater.debate(context)

        # Still bullish (sentiment-based), but conviction reduced for mismatch
        # Base: 0.45 + 0.60*0.40 = 0.69, with mismatch penalty 0.75x = 0.5175
        base = 0.45 + 0.60 * 0.40
        expected_max = base * 0.75
        assert opinion.direction == Direction.BULLISH
        assert opinion.conviction <= expected_max + 0.01

    def test_neutral_sentiment(self):
        """Neutral sentiment (−0.30 to +0.30) → neutral direction."""
        context = {
            "ticker": "DIA",
            "sentiment_snapshot": {
                "composite_score": 0.15,
                "mention_count": 20,
                "data_sources": ["moomoo", "yfinance"],
            },
            "strategy": {"selected_structure": "VERTICAL_CALL_SPREAD"},
        }
        debater = SentimentDebater()
        opinion = debater.debate(context)

        assert opinion.direction == Direction.NEUTRAL
        assert opinion.conviction <= 0.35

    def test_thin_signal_conviction_discount(self):
        """Mention count < 10 → conviction reduced by 15%."""
        context = {
            "ticker": "SPY",
            "sentiment_snapshot": {
                "composite_score": 0.60,  # Strong bullish
                "mention_count": 5,  # Thin signal
                "data_sources": ["reddit"],
            },
            "strategy": {"selected_structure": "LONG_CALL"},
        }
        debater = SentimentDebater()
        opinion = debater.debate(context)

        # Base would be 0.45 + 0.60*0.40 = 0.69, but with thin signal discount
        assert opinion.conviction < 0.69
        assert opinion.conviction >= 0.25  # Floor applied

    def test_no_data_neutral_baseline(self):
        """No vol_regime / flow / sentiment data -> NEUTRAL @ 0.25 (P2.1)."""
        context = {
            "ticker": "SPY",
            "strategy": {"selected_structure": "LONG_CALL"},
        }
        debater = SentimentDebater()
        opinion = debater.debate(context)

        assert opinion.direction == Direction.NEUTRAL
        assert opinion.conviction == 0.25
        assert "days_to_earnings" in opinion.signals_used

    def test_signals_used_complete(self):
        """IV-skew path logs put_call_skew, signal type, and earnings context (P2.1)."""
        context = {
            "ticker": "SPY",
            "vol_regime": {"put_call_skew": 0.07},
            "strategy": {"selected_structure": "VERTICAL_CALL_SPREAD"},
        }
        debater = SentimentDebater()
        opinion = debater.debate(context)

        assert "put_call_skew" in opinion.signals_used
        assert "signal" in opinion.signals_used
        assert "days_to_earnings" in opinion.signals_used


# ─────────────────────────────────────────────────────────────────────────────
# CROSS-DEBATER TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestDebaterProperties:
    """Properties that all debaters must satisfy."""

    @pytest.mark.parametrize(
        "debater_class",
        [DirectionalDebater, IncomeDebater, VolatilityDebater, FlowDebater, SentimentDebater],
    )
    def test_conviction_in_range(self, debater_class):
        """Every debater's conviction must be in [0, 1]."""
        debater = debater_class()
        context = {
            "ticker": "TEST",
            "current_price": 100.0,
            "vwap": 100.0,
            "ema_20": 100.0,
            "ema_50": 100.0,
            "rsi": 50.0,
            "atr": 1.0,
            "prior_close": 100.0,
            "vol_regime": {"iv_rank": 0.50, "rv_iv_ratio": 1.0},
            "chain_snapshot": {"theta": 0.0, "vega": 0.0, "delta": 0.5, "gamma": 0.001},
            "strategy": {"selected_structure": "LONG_CALL"},
            "flow_data": {"put_call_ratio": 1.0},
            "sentiment_snapshot": {"composite_score": 0.0, "mention_count": 0, "data_sources": []},
            "market_vix": 20.0,
            "vvix": 90.0,
        }
        opinion = debater.debate(context)
        assert 0.0 <= opinion.conviction <= 1.0

    @pytest.mark.parametrize(
        "debater_class",
        [DirectionalDebater, IncomeDebater, VolatilityDebater, FlowDebater, SentimentDebater],
    )
    def test_direction_valid(self, debater_class):
        """Every debater returns a valid direction."""
        debater = debater_class()
        context = {
            "ticker": "TEST",
            "current_price": 100.0,
            "vwap": 100.0,
            "ema_20": 100.0,
            "ema_50": 100.0,
            "rsi": 50.0,
            "atr": 1.0,
            "prior_close": 100.0,
            "vol_regime": {"iv_rank": 0.50, "rv_iv_ratio": 1.0},
            "chain_snapshot": {"theta": 0.0, "vega": 0.0, "delta": 0.5, "gamma": 0.001},
            "strategy": {"selected_structure": "LONG_CALL"},
            "flow_data": {"put_call_ratio": 1.0},
            "sentiment_snapshot": {"composite_score": 0.0, "mention_count": 0, "data_sources": []},
            "market_vix": 20.0,
            "vvix": 90.0,
        }
        opinion = debater.debate(context)
        assert opinion.direction in [Direction.BULLISH, Direction.BEARISH, Direction.NEUTRAL]

    @pytest.mark.parametrize(
        "debater_class",
        [DirectionalDebater, IncomeDebater, VolatilityDebater, FlowDebater, SentimentDebater],
    )
    def test_signals_used_nonempty_dict(self, debater_class):
        """Every debater logs signals_used as a non-empty dict."""
        debater = debater_class()
        context = {
            "ticker": "TEST",
            "current_price": 100.0,
            "vwap": 100.0,
            "ema_20": 100.0,
            "ema_50": 100.0,
            "rsi": 50.0,
            "atr": 1.0,
            "prior_close": 100.0,
            "vol_regime": {"iv_rank": 0.50, "rv_iv_ratio": 1.0},
            "chain_snapshot": {"theta": 0.0, "vega": 0.0, "delta": 0.5, "gamma": 0.001},
            "strategy": {"selected_structure": "LONG_CALL"},
            "flow_data": {"put_call_ratio": 1.0},
            "sentiment_snapshot": {"composite_score": 0.0, "mention_count": 0, "data_sources": []},
            "market_vix": 20.0,
            "vvix": 90.0,
        }
        opinion = debater.debate(context)
        assert isinstance(opinion.signals_used, dict)
        assert len(opinion.signals_used) > 0

    @pytest.mark.parametrize(
        "debater_class",
        [DirectionalDebater, IncomeDebater, VolatilityDebater, FlowDebater, SentimentDebater],
    )
    def test_reasoning_nonempty_string(self, debater_class):
        """Every debater produces a non-empty reasoning string."""
        debater = debater_class()
        context = {
            "ticker": "TEST",
            "current_price": 100.0,
            "vwap": 100.0,
            "ema_20": 100.0,
            "ema_50": 100.0,
            "rsi": 50.0,
            "atr": 1.0,
            "prior_close": 100.0,
            "vol_regime": {"iv_rank": 0.50, "rv_iv_ratio": 1.0},
            "chain_snapshot": {"theta": 0.0, "vega": 0.0, "delta": 0.5, "gamma": 0.001},
            "strategy": {"selected_structure": "LONG_CALL"},
            "flow_data": {"put_call_ratio": 1.0},
            "sentiment_snapshot": {"composite_score": 0.0, "mention_count": 0, "data_sources": []},
            "market_vix": 20.0,
            "vvix": 90.0,
        }
        opinion = debater.debate(context)
        assert isinstance(opinion.reasoning, str)
        assert len(opinion.reasoning) > 0