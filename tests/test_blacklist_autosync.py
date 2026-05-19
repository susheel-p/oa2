"""Tests for the auto-sync between KB and static_blacklist.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oa2.learning.knowledge_base import KnowledgeBase, TickerStats
from oa2.strategy import quality_gates as qg


@pytest.fixture
def isolated_disk(tmp_path, monkeypatch):
    """Point quality_gates at a tmp_path so we never touch the real file."""
    bl_path = tmp_path / "static_blacklist.json"
    monkeypatch.setattr(qg, "_static_blacklist_path", lambda: bl_path)
    qg.reload_blacklist_from_disk()
    yield bl_path
    # Restore to defaults after test
    qg.reload_blacklist_from_disk()


class TestStaticBlacklistFile:
    def test_missing_file_uses_fallback(self, isolated_disk):
        """No file -> fallback constants are used."""
        assert not isolated_disk.exists()
        # Fallback should be the hardcoded set
        assert qg.TICKER_BLACKLIST == qg._FALLBACK_BLACKLIST

    def test_loads_blacklist_from_file(self, isolated_disk):
        payload = {
            "schema_version": 1,
            "last_synced": "2026-05-19T10:00:00",
            "source": "test",
            "tickers": ["FOO", "BAR"],
            "quality_scores": {"FOO": 0.0, "BAR": 0.0, "GOOGL": 1.2},
        }
        isolated_disk.write_text(json.dumps(payload))
        qg.reload_blacklist_from_disk()
        assert qg.TICKER_BLACKLIST == {"FOO", "BAR"}
        assert qg.TICKER_QUALITY_SCORE["GOOGL"] == 1.2

    def test_corrupt_json_falls_back(self, isolated_disk):
        isolated_disk.write_text("{not valid json")
        qg.reload_blacklist_from_disk()
        assert qg.TICKER_BLACKLIST == qg._FALLBACK_BLACKLIST

    def test_wrong_schema_version_falls_back(self, isolated_disk):
        isolated_disk.write_text(json.dumps({"schema_version": 999, "tickers": ["X"]}))
        qg.reload_blacklist_from_disk()
        assert qg.TICKER_BLACKLIST == qg._FALLBACK_BLACKLIST

    def test_blacklisted_ticker_forced_to_zero_score(self, isolated_disk):
        """Even if data file gives blacklisted ticker a non-zero score, it's overridden."""
        payload = {
            "schema_version": 1,
            "tickers": ["FOO"],
            "quality_scores": {"FOO": 1.5},  # absurd, should be overridden
        }
        isolated_disk.write_text(json.dumps(payload))
        qg.reload_blacklist_from_disk()
        assert qg.TICKER_QUALITY_SCORE["FOO"] == 0.0


class TestDailyLearnAutoSync:
    def test_derive_writes_correct_set(self, tmp_path, monkeypatch):
        """daily_learn._derive_static_blacklist matches KB blacklist verdict."""
        # Stub the path so we don't touch real disk
        bl_path = tmp_path / "static_blacklist.json"
        monkeypatch.setattr(qg, "_static_blacklist_path", lambda: bl_path)
        qg.reload_blacklist_from_disk()

        kb = KnowledgeBase()
        # Bad ticker -> blacklisted
        kb.tickers["BAD"] = TickerStats(n_trades=50, hits=20, n_dollar_winners=20)
        # Good ticker -> high score, not blacklisted
        kb.tickers["GOOD"] = TickerStats(n_trades=50, hits=33, n_dollar_winners=35)

        # Import after path patch so the helper sees our mocked path
        import importlib
        import scripts.daily_learn as dl
        importlib.reload(dl)

        tickers, scores = dl._derive_static_blacklist(kb)
        assert "BAD" in tickers
        assert "GOOD" not in tickers
        assert scores["BAD"] == 0.0
        assert scores["GOOD"] > 1.0

        # Round-trip: write then reload via quality_gates
        dl._write_static_blacklist(kb, dry_run=False)
        qg.reload_blacklist_from_disk()
        assert "BAD" in qg.TICKER_BLACKLIST
        assert qg.TICKER_QUALITY_SCORE["GOOD"] > 1.0
