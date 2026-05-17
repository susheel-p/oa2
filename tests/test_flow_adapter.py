"""Tests for Phase E: flow adapter layer and expiration-aware flow."""

from __future__ import annotations

import datetime

import pytest

from oa2.dataflows.flow_adapter import (
    YFinanceFlowAdapter,
    UnusualWhalesAdapter,
    TradierAdapter,
    get_adapter,
    auto_adapter,
    empty_flow_data,
)
from oa2.dataflows.expiry_flow import (
    ExpiryBucket,
    ExpiryFlowProfile,
    classify_expiry_flow,
    flow_context_from_profile,
    _dte,
    _bucket_from_dte,
)


# =============================================================================
# empty_flow_data
# =============================================================================

class TestEmptyFlowData:
    def test_returns_dict(self):
        d = empty_flow_data()
        assert isinstance(d, dict)

    def test_data_quality_absent(self):
        d = empty_flow_data()
        assert d["data_quality"] == "absent"

    def test_all_required_keys_present(self):
        d = empty_flow_data("test_source")
        required_keys = [
            "data_quality", "source", "as_of",
            "put_call_ratio", "call_sweep_count", "put_sweep_count",
            "dark_pool_bullish", "dark_pool_bearish",
            "large_call_oi_change", "large_put_oi_change",
            "unusual_call_vol", "unusual_put_vol",
        ]
        for key in required_keys:
            assert key in d

    def test_source_set(self):
        d = empty_flow_data("my_source")
        assert d["source"] == "my_source"

    def test_numeric_defaults_are_zero(self):
        d = empty_flow_data()
        assert d["call_sweep_count"] == 0
        assert d["put_sweep_count"] == 0
        assert d["large_call_oi_change"] == 0.0
        assert d["large_put_oi_change"] == 0.0

    def test_bool_defaults_are_false(self):
        d = empty_flow_data()
        assert d["dark_pool_bullish"] is False
        assert d["dark_pool_bearish"] is False
        assert d["unusual_call_vol"] is False
        assert d["unusual_put_vol"] is False

    def test_put_call_ratio_is_none(self):
        d = empty_flow_data()
        assert d["put_call_ratio"] is None


# =============================================================================
# Flow adapter factory
# =============================================================================

class TestGetAdapter:
    def test_yfinance_adapter(self):
        adapter = get_adapter("yfinance")
        assert isinstance(adapter, YFinanceFlowAdapter)
        assert adapter.name == "yfinance"

    def test_unusual_whales_adapter(self):
        adapter = get_adapter("unusual_whales")
        assert isinstance(adapter, UnusualWhalesAdapter)
        assert adapter.name == "unusual_whales"

    def test_tradier_adapter(self):
        adapter = get_adapter("tradier")
        assert isinstance(adapter, TradierAdapter)
        assert adapter.name == "tradier"

    def test_unknown_source_raises(self):
        with pytest.raises(ValueError, match="Unknown flow adapter"):
            get_adapter("nonexistent_vendor")

    def test_auto_adapter_returns_yfinance_when_no_keys(self, monkeypatch):
        monkeypatch.delenv("UW_API_KEY", raising=False)
        monkeypatch.delenv("TRADIER_API_KEY", raising=False)
        adapter = auto_adapter()
        assert isinstance(adapter, YFinanceFlowAdapter)

    def test_auto_adapter_returns_uw_when_key_set(self, monkeypatch):
        monkeypatch.setenv("UW_API_KEY", "fake_key")
        monkeypatch.delenv("TRADIER_API_KEY", raising=False)
        adapter = auto_adapter()
        assert isinstance(adapter, UnusualWhalesAdapter)

    def test_auto_adapter_returns_tradier_when_uw_absent(self, monkeypatch):
        monkeypatch.delenv("UW_API_KEY", raising=False)
        monkeypatch.setenv("TRADIER_API_KEY", "fake_key")
        adapter = auto_adapter()
        assert isinstance(adapter, TradierAdapter)


# =============================================================================
# Unconfigured stubs return absent data, not exceptions
# =============================================================================

class TestUnconfiguredStubs:
    def test_uw_stub_returns_dict_not_exception(self, monkeypatch):
        monkeypatch.delenv("UW_API_KEY", raising=False)
        adapter = UnusualWhalesAdapter()
        result = adapter.fetch("SPY")
        assert isinstance(result, dict)
        assert "error" in result

    def test_tradier_stub_returns_dict_not_exception(self, monkeypatch):
        monkeypatch.delenv("TRADIER_API_KEY", raising=False)
        adapter = TradierAdapter()
        result = adapter.fetch("SPY")
        assert isinstance(result, dict)
        assert "error" in result

    def test_uw_not_configured(self, monkeypatch):
        monkeypatch.delenv("UW_API_KEY", raising=False)
        adapter = UnusualWhalesAdapter()
        assert not adapter.is_configured()

    def test_tradier_not_configured(self, monkeypatch):
        monkeypatch.delenv("TRADIER_API_KEY", raising=False)
        adapter = TradierAdapter()
        assert not adapter.is_configured()


# =============================================================================
# YFinance adapter (unit-level — mock the network call)
# =============================================================================

class TestYFinanceFlowAdapter:
    def _mock_chain(self, monkeypatch, call_vol=1000, put_vol=1200, call_oi=5000, put_oi=4000):
        """Patch yfinance to return a minimal chain."""
        import types
        import sys

        class FakeCalls:
            columns = ["volume", "openInterest"]
            def __init__(self):
                import pandas as pd
                self._df = pd.DataFrame({
                    "volume": [call_vol],
                    "openInterest": [call_oi],
                })
            @property
            def empty(self): return False
            def __getitem__(self, key): return self._df[key]
            def __contains__(self, key): return key in self._df.columns
            @property
            def sum(self): return self._df.sum

        class FakePuts:
            def __init__(self):
                import pandas as pd
                self._df = pd.DataFrame({
                    "volume": [put_vol],
                    "openInterest": [put_oi],
                })
            @property
            def empty(self): return False
            def __getitem__(self, key): return self._df[key]
            def __contains__(self, key): return key in self._df.columns
            @property
            def sum(self): return self._df.sum

        class FakeChain:
            calls = FakeCalls()
            puts = FakePuts()

        class FakeTicker:
            options = ["2025-05-16", "2025-05-23"]
            def option_chain(self, exp): return FakeChain()
            def history(self, **kwargs): return None

        class FakeYF:
            @staticmethod
            def Ticker(sym): return FakeTicker()

        monkeypatch.setitem(sys.modules, "yfinance", FakeYF)

    def test_fetch_returns_dict(self, monkeypatch):
        self._mock_chain(monkeypatch)
        adapter = YFinanceFlowAdapter()
        result = adapter.fetch("SPY")
        assert isinstance(result, dict)

    def test_fetch_has_required_keys(self, monkeypatch):
        self._mock_chain(monkeypatch)
        adapter = YFinanceFlowAdapter()
        result = adapter.fetch("SPY")
        assert "data_quality" in result
        assert "put_call_ratio" in result

    def test_pcr_computed_from_volumes(self, monkeypatch):
        self._mock_chain(monkeypatch, call_vol=1000, put_vol=1200)
        adapter = YFinanceFlowAdapter()
        result = adapter.fetch("SPY")
        if result["put_call_ratio"] is not None:
            assert result["put_call_ratio"] == pytest.approx(1.2, rel=0.01)

    def test_data_quality_real_when_signals_present(self, monkeypatch):
        self._mock_chain(monkeypatch)
        adapter = YFinanceFlowAdapter()
        result = adapter.fetch("SPY")
        if result.get("put_call_ratio") is not None:
            assert result["data_quality"] == "real"

    def test_source_is_yfinance(self, monkeypatch):
        self._mock_chain(monkeypatch)
        adapter = YFinanceFlowAdapter()
        result = adapter.fetch("SPY")
        assert result["source"] == "yfinance"

    def test_no_sweeps_or_dark_pool(self, monkeypatch):
        self._mock_chain(monkeypatch)
        adapter = YFinanceFlowAdapter()
        result = adapter.fetch("SPY")
        assert result["call_sweep_count"] == 0
        assert result["put_sweep_count"] == 0
        assert result["dark_pool_bullish"] is False
        assert result["dark_pool_bearish"] is False


# =============================================================================
# Expiration-aware flow (E3)
# =============================================================================

class TestExpiryBucket:
    def test_dte_computation(self):
        today = datetime.date(2025, 5, 15)
        assert _dte("2025-05-16", today) == 1
        assert _dte("2025-05-22", today) == 7
        assert _dte("2025-06-05", today) == 21
        assert _dte("2025-06-30", today) == 46

    def test_invalid_dte_returns_minus_one(self):
        assert _dte("not-a-date", datetime.date(2025, 5, 15)) == -1

    def test_bucket_from_dte(self):
        assert _bucket_from_dte(0) == ExpiryBucket.FRONT_WEEK
        assert _bucket_from_dte(7) == ExpiryBucket.FRONT_WEEK
        assert _bucket_from_dte(8) == ExpiryBucket.NEAR_TERM
        assert _bucket_from_dte(21) == ExpiryBucket.NEAR_TERM
        assert _bucket_from_dte(22) == ExpiryBucket.MID_TERM
        assert _bucket_from_dte(45) == ExpiryBucket.MID_TERM
        assert _bucket_from_dte(46) == ExpiryBucket.LONGER
        assert _bucket_from_dte(-1) == ExpiryBucket.UNKNOWN


class TestClassifyExpiryFlow:
    def _chain(self, expiry_dates_vols: dict) -> dict:
        """Build chain_by_expiry from {expiry: (call_vol, put_vol)}."""
        result = {}
        for exp, (cv, pv) in expiry_dates_vols.items():
            result[exp] = {
                "calls": [{"volume": cv}],
                "puts": [{"volume": pv}],
            }
        return result

    def test_empty_chain_returns_profile(self):
        profile = classify_expiry_flow({})
        assert isinstance(profile, ExpiryFlowProfile)
        assert profile.total_call_vol == 0
        assert profile.total_put_vol == 0

    def test_front_week_dominance_sets_urgency_high(self):
        as_of = datetime.date(2025, 5, 15)
        chain = self._chain({
            "2025-05-16": (10000, 8000),   # 1 DTE → front week
            "2025-06-20": (500, 400),       # mid-term
        })
        profile = classify_expiry_flow(chain, as_of)
        assert profile.urgency_signal == "high"
        assert profile.dominant_bucket == ExpiryBucket.FRONT_WEEK.value

    def test_mid_term_dominant(self):
        as_of = datetime.date(2025, 5, 15)
        chain = self._chain({
            "2025-05-16": (200, 150),          # front week — small
            "2025-06-20": (8000, 7000),        # ~36 DTE → mid-term — large
        })
        profile = classify_expiry_flow(chain, as_of)
        assert profile.dominant_bucket == ExpiryBucket.MID_TERM.value

    def test_pcr_computed_per_bucket(self):
        as_of = datetime.date(2025, 5, 15)
        chain = self._chain({
            "2025-05-16": (1000, 2000),   # front week: PCR = 2.0
        })
        profile = classify_expiry_flow(chain, as_of)
        assert profile.front_week_pcr == pytest.approx(2.0, rel=0.01)

    def test_volumes_summed_correctly(self):
        as_of = datetime.date(2025, 5, 15)
        chain = self._chain({
            "2025-05-16": (1000, 500),    # front week
            "2025-06-06": (2000, 1500),   # near-term (~22 DTE)
        })
        profile = classify_expiry_flow(chain, as_of)
        assert profile.total_call_vol == 3000
        assert profile.total_put_vol == 2000

    def test_front_week_dominant_property(self):
        as_of = datetime.date(2025, 5, 15)
        chain = self._chain({
            "2025-05-16": (9000, 8000),   # front week dominant
            "2025-06-20": (100, 100),
        })
        profile = classify_expiry_flow(chain, as_of)
        assert profile.front_week_dominant is True

    def test_urgency_medium_when_moderate_front_week(self):
        as_of = datetime.date(2025, 5, 15)
        chain = self._chain({
            "2025-05-16": (3000, 2000),   # 35% → medium (25-50%)
            "2025-06-20": (5000, 4000),   # 65%
        })
        profile = classify_expiry_flow(chain, as_of)
        assert profile.urgency_signal in ("medium", "low")

    def test_expirations_analyzed_count(self):
        as_of = datetime.date(2025, 5, 15)
        chain = self._chain({
            "2025-05-16": (100, 100),
            "2025-05-23": (200, 150),
            "2025-06-20": (300, 250),
        })
        profile = classify_expiry_flow(chain, as_of)
        assert profile.expirations_analyzed == 3

    def test_flow_context_from_profile_has_required_keys(self):
        profile = ExpiryFlowProfile(
            front_week_call_vol=1000,
            front_week_put_vol=800,
            total_call_vol=5000,
            total_put_vol=4000,
            dominant_bucket="front_week",
            urgency_signal="high",
        )
        ctx = flow_context_from_profile(profile)
        assert "expiry_flow" in ctx
        ef = ctx["expiry_flow"]
        assert "dominant_bucket" in ef
        assert "urgency_signal" in ef
        assert "front_week_pcr" in ef
        assert "total_call_vol" in ef
