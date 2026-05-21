"""Tests for tradingbot.learning.knowledge_base."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingbot.learning.knowledge_base import (
    KnowledgeBase,
    TickerStats,
    RegimeStats,
    build_from_outcomes,
    _ticker_multiplier,
    _ticker_blacklisted,
    MIN_OBS_FOR_MULT,
    MIN_OBS_FOR_BLACKLIST,
    MIN_MULT, MAX_MULT,
)


class TestTickerMultiplier:
    def test_below_min_obs_returns_one(self):
        s = TickerStats(n_trades=10, hits=4)
        assert _ticker_multiplier(s) == 1.0

    def test_strong_ticker_boosted(self):
        s = TickerStats(n_trades=50, hits=30)  # 60% hit rate
        mult = _ticker_multiplier(s)
        assert mult > 1.0
        assert mult <= MAX_MULT

    def test_weak_ticker_suppressed(self):
        s = TickerStats(n_trades=50, hits=20)  # 40% hit rate
        mult = _ticker_multiplier(s)
        assert mult < 1.0
        assert mult >= MIN_MULT


class TestTickerBlacklist:
    def test_not_blacklisted_under_min_obs(self):
        s = TickerStats(n_trades=20, hits=5, n_dollar_winners=4)  # 25% hit, 20% win
        assert not _ticker_blacklisted(s)

    def test_blacklisted_when_both_low(self):
        s = TickerStats(n_trades=50, hits=20, n_dollar_winners=20)  # 40% hit, 40% win
        assert _ticker_blacklisted(s)

    def test_not_blacklisted_when_dollar_winners_high(self):
        # Low hit-rate but good $-weighted wins -> NOT blacklisted
        s = TickerStats(n_trades=50, hits=20, n_dollar_winners=30)  # 40% hit, 60% win
        assert not _ticker_blacklisted(s)


class TestBuildFromOutcomes:
    def test_aggregates_by_ticker(self):
        import datetime
        today = datetime.date.today().isoformat()
        outcomes = [
            {"ticker": "SPY", "direction_hit": True,  "total_pnl_dollars": 100, "pnl_pct_of_max_loss": 0.5, "regime_label": "normal_neutral", "structure": "vertical_call_spread", "max_loss": 200, "decision_date": today},
            {"ticker": "SPY", "direction_hit": False, "total_pnl_dollars": -50, "pnl_pct_of_max_loss": -0.25, "regime_label": "normal_neutral", "structure": "vertical_call_spread", "max_loss": 200, "decision_date": today},
            {"ticker": "QQQ", "direction_hit": True,  "total_pnl_dollars": 200, "pnl_pct_of_max_loss": 0.6,  "regime_label": "normal_trending", "structure": "vertical_call_spread", "max_loss": 300, "decision_date": today},
        ]
        kb = build_from_outcomes(outcomes)
        assert kb.n_outcomes_used == 3
        assert kb.tickers["SPY"].n_trades == 2
        assert kb.tickers["SPY"].hits == 1
        assert kb.tickers["SPY"].hit_rate == 0.5
        assert kb.tickers["QQQ"].hit_rate == 1.0
        assert kb.regimes["normal_neutral"].n_trades == 2

    def test_filters_out_old_outcomes(self):
        outcomes = [
            {"ticker": "SPY", "direction_hit": True, "total_pnl_dollars": 100,
             "pnl_pct_of_max_loss": 0.5, "regime_label": "x", "structure": "y",
             "max_loss": 200, "decision_date": "2020-01-01"},
        ]
        kb = build_from_outcomes(outcomes, window_days=60)
        assert kb.n_outcomes_used == 0


class TestKBPersistence:
    def test_save_and_load_roundtrip(self, tmp_path):
        kb = KnowledgeBase()
        kb.last_updated = "2026-05-19T22:00:00-04:00"
        kb.tickers["SPY"] = TickerStats(n_trades=10, hits=6, total_pnl=500.0)
        kb.regimes["normal_neutral"] = RegimeStats(n_trades=10, hits=6, total_pnl=500.0)

        path = tmp_path / "kb.json"
        kb.save(path)

        assert path.exists()
        loaded = KnowledgeBase.load(path)
        assert loaded.tickers["SPY"].n_trades == 10
        assert loaded.tickers["SPY"].hits == 6
        assert loaded.regimes["normal_neutral"].hits == 6
        assert loaded.last_updated == kb.last_updated

    def test_load_missing_file_returns_empty(self, tmp_path):
        kb = KnowledgeBase.load(tmp_path / "nonexistent.json")
        assert kb.tickers == {}
        assert kb.regimes == {}

    def test_atomic_write(self, tmp_path):
        """Save uses .tmp and rename — no partial files left on disk."""
        kb = KnowledgeBase()
        path = tmp_path / "kb.json"
        kb.save(path)
        leftovers = list(tmp_path.glob(".kb_*.tmp"))
        assert leftovers == []


class TestPublicAPI:
    def test_ticker_multiplier_default(self):
        kb = KnowledgeBase()
        kb.tickers["XLE"] = TickerStats(n_trades=50, hits=30)
        assert kb.ticker_multiplier("XLE") > 1.0
        # Unknown ticker -> 1.0
        assert kb.ticker_multiplier("UNKNOWN") == 1.0
        # Case insensitive
        assert kb.ticker_multiplier("xle") == kb.ticker_multiplier("XLE")

    def test_blacklist_lookup(self):
        kb = KnowledgeBase()
        kb.tickers["SLV"] = TickerStats(n_trades=60, hits=22, n_dollar_winners=25)
        assert kb.is_blacklisted("SLV")
        assert not kb.is_blacklisted("XLE")
