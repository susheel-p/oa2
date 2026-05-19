"""Tests for oa2.learning.rag_context + KB-aware quality_gates."""

from __future__ import annotations

import pytest

from oa2.learning.knowledge_base import KnowledgeBase, TickerStats
from oa2.learning.rag_context import (
    get_rag_context,
    reset_rag_cache,
    kb_is_available,
)
from oa2.strategy.quality_gates import (
    check_quality_gates,
    ticker_conviction_multiplier,
    regime_conviction_multiplier,
)


@pytest.fixture
def kb_with_data(tmp_path, monkeypatch):
    """Write a KB with a blacklisted ticker + boosted ticker, point cache at it."""
    kb = KnowledgeBase()
    # Blacklisted: low hit + low $ win, enough trades
    kb.tickers["BAD"] = TickerStats(n_trades=50, hits=20, n_dollar_winners=20)
    # Boosted: high hit rate, enough trades
    kb.tickers["GOOD"] = TickerStats(n_trades=50, hits=33, n_dollar_winners=35)
    path = tmp_path / "kb.json"
    kb.save(path)

    # Redirect cache to this KB
    reset_rag_cache()
    monkeypatch.setattr(
        "oa2.learning.rag_context.default_kb_path",
        lambda: path,
    )
    reset_rag_cache()
    yield kb
    reset_rag_cache()


class TestRagContextLoading:
    def test_kb_available(self, kb_with_data):
        assert kb_is_available()

    def test_returns_loaded_kb(self, kb_with_data):
        kb = get_rag_context()
        assert "BAD" in kb.tickers
        assert "GOOD" in kb.tickers

    def test_cache_reuses_loaded_kb(self, kb_with_data):
        kb1 = get_rag_context()
        kb2 = get_rag_context()
        assert kb1 is kb2  # same object due to caching


class TestQualityGatesKBIntegration:
    def test_kb_blacklist_blocks(self, kb_with_data):
        passed, reason = check_quality_gates("BAD", "normal_neutral")
        assert not passed
        assert "blacklisted by KB" in reason

    def test_kb_unknown_ticker_passes(self, kb_with_data):
        passed, _ = check_quality_gates("UNKNOWN", "normal_neutral")
        assert passed

    def test_mean_revert_still_blocked(self, kb_with_data):
        passed, reason = check_quality_gates("GOOD", "vol_exp_mean_revert")
        assert not passed
        assert "mean-revert" in reason.lower() or "Mean-reverting" in reason

    def test_good_ticker_in_good_regime_passes(self, kb_with_data):
        passed, _ = check_quality_gates("GOOD", "normal_trending")
        assert passed


class TestKBMultipliers:
    def test_good_ticker_boosted(self, kb_with_data):
        mult = ticker_conviction_multiplier("GOOD")
        assert mult > 1.0

    def test_bad_ticker_suppressed(self, kb_with_data):
        mult = ticker_conviction_multiplier("BAD")
        assert mult < 1.0

    def test_unknown_ticker_neutral(self, kb_with_data):
        mult = ticker_conviction_multiplier("UNKNOWN")
        assert mult == 1.0


class TestEmptyKBFallback:
    def test_no_kb_uses_static_blacklist(self, tmp_path, monkeypatch):
        """When no KB file exists, static TICKER_BLACKLIST still applies."""
        reset_rag_cache()
        monkeypatch.setattr(
            "oa2.learning.rag_context.default_kb_path",
            lambda: tmp_path / "nonexistent.json",
        )
        reset_rag_cache()
        # SLV is in the static blacklist
        passed, reason = check_quality_gates("SLV", "normal_neutral")
        assert not passed
        assert "static quality blacklist" in reason
        reset_rag_cache()
