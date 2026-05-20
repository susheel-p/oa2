"""Phase 1 smoke test — debaters imported, can run, opinions tracked."""

import pytest

from tradingbot.debaters.directional import DirectionalDebater
from tradingbot.debaters.income import IncomeDebater
from tradingbot.debaters.volatility import VolatilityDebater
from tradingbot.debaters.flow import FlowDebater
from tradingbot.debaters.sentiment import SentimentDebater
from tradingbot.debaters.runner import DebaterEnsemble
from tradingbot.graph.pipeline import run
from tradingbot.core import feature_flags


def test_debaters_import():
    """All 5 debaters import cleanly."""
    assert DirectionalDebater()
    assert IncomeDebater()
    assert VolatilityDebater()
    assert FlowDebater()
    assert SentimentDebater()


def test_ensemble_instantiate():
    """DebaterEnsemble initializes with all 5 debaters."""
    ensemble = DebaterEnsemble()
    assert len(ensemble.debaters) == 5
    assert set(ensemble.debaters.keys()) == {
        "directional", "income", "volatility", "flow", "sentiment"
    }


def test_debater_with_minimal_context():
    """Each debater can run on a minimal context and returns an opinion."""
    context = {
        "ticker": "SPY",
        "current_price": 520.0,
        "vwap": 519.0,
        "ema_20": 521.0,
        "ema_50": 518.0,
        "rsi": 55.0,
        "atr": 2.0,
        "prior_close": 519.0,
        "vol_regime": {
            "iv_rank": 0.60,
            "rv_iv_ratio": 1.0,
            "skew_extreme": False,
            "term_upward": True,
        },
        "chain_snapshot": {
            "theta": -0.05,
            "vega": 0.02,
            "delta": 0.5,
            "gamma": 0.001,
        },
        "strategy": {
            "selected_structure": "VERTICAL_CALL_SPREAD",
        },
    }

    debaters = [
        DirectionalDebater(),
        IncomeDebater(),
        VolatilityDebater(),
        FlowDebater(),
        SentimentDebater(),
    ]

    for debater in debaters:
        opinion = debater.debate(context)
        assert opinion.debater_name == debater.name
        assert opinion.direction in ["BULLISH", "BEARISH", "NEUTRAL"]
        assert 0.0 <= opinion.conviction <= 1.0
        assert isinstance(opinion.reasoning, str)
        assert isinstance(opinion.signals_used, dict)


def test_ensemble_run():
    """Ensemble runs all debaters and returns list of opinions."""
    context = {
        "ticker": "QQQ",
        "current_price": 380.0,
        "vwap": 379.0,
        "ema_20": 381.0,
        "ema_50": 378.0,
        "rsi": 50.0,
        "atr": 1.5,
        "prior_close": 379.5,
        "vol_regime": {
            "iv_rank": 0.50,
            "rv_iv_ratio": 0.95,
            "skew_extreme": False,
            "term_upward": True,
        },
        "chain_snapshot": {
            "theta": -0.03,
            "vega": 0.015,
            "delta": 0.5,
            "gamma": 0.0012,
        },
        "strategy": {
            "selected_structure": "LONG_CALL",
        },
    }

    ensemble = DebaterEnsemble()
    opinions = ensemble.run(context, log_to_disk=False)  # Don't log to disk in test

    assert len(opinions) == 5
    assert all(op.debater_name in ensemble.debaters for op in opinions)
    assert all(0.0 <= op.conviction <= 1.0 for op in opinions)


def test_ensemble_opinion_summary():
    """Ensemble can summarize opinions."""
    context = {
        "ticker": "IWM",
        "current_price": 200.0,
        "vwap": 200.0,
        "ema_20": 199.5,
        "ema_50": 199.0,
        "rsi": 50.0,
        "atr": 1.0,
        "prior_close": 199.8,
        "vol_regime": {"iv_rank": 0.45, "rv_iv_ratio": 1.05},
        "chain_snapshot": {"theta": -0.02, "vega": 0.01, "delta": 0.5, "gamma": 0.001},
        "strategy": {"selected_structure": "VERTICAL_PUT_SPREAD"},
    }

    ensemble = DebaterEnsemble()
    opinions = ensemble.run(context, log_to_disk=False)
    summary = ensemble.opinions_summary(opinions)

    assert summary["count"] == 5
    assert len(summary["debaters"]) == 5
    for debater_summary in summary["debaters"]:
        assert "name" in debater_summary
        assert "direction" in debater_summary
        assert "conviction" in debater_summary


def test_pipeline_with_debaters_enabled():
    """Pipeline runs debaters when flag is enabled (shadow mode)."""
    # Temporarily enable debaters flag
    import os
    os.environ["TRADINGBOT_FLAG_DEBATERS"] = "1"

    # Re-import to pick up new flag
    import importlib
    import tradingbot.core.feature_flags
    importlib.reload(oa2.core.feature_flags)

    context = {
        "ticker": "DIA",
        "current_price": 380.0,
        "vwap": 379.0,
        "ema_20": 380.5,
        "ema_50": 378.0,
        "rsi": 55.0,
        "atr": 1.0,
        "prior_close": 379.5,
        "vol_regime": {"iv_rank": 0.55, "rv_iv_ratio": 0.98},
        "chain_snapshot": {"theta": -0.04, "vega": 0.02, "delta": 0.5, "gamma": 0.001},
        "strategy": {"selected_structure": "IRON_CONDOR"},
    }

    result = run("DIA", context_dict=context)

    assert result.ticker == "DIA"
    assert result.decision["status"] == "debaters_only"
    assert result.decision["opinion_count"] == 5
    assert len(result.debater_opinions) == 5

    # Clean up
    del os.environ["TRADINGBOT_FLAG_DEBATERS"]
    importlib.reload(oa2.core.feature_flags)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])