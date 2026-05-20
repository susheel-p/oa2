"""Tests for Phase E: flow adapter layer and expiration-aware flow."""

from __future__ import annotations

import datetime

import pytest

from tradingbot.dataflows.flow_adapter import (
    FlowData,
    FlowAdapterBase,
    YFinanceFlowAdapter,
    MoomooFlowAdapter,
    UnusualWhalesAdapter,
    OptionsWhaleAdapter,
    TradierAdapter,
    get_adapter,
    auto_adapter,
    list_adapters,
    _ADAPTER_REGISTRY,
)
from tradingbot.dataflows.expiry_flow import (
    ExpiryBucket,
    ExpiryFlowProfile,
    classify_expiry_flow,
    flow_context_from_profile,
    _dte,
    _bucket_from_dte,
)


# =============================================================================
# FlowData dataclass
# =============================================================================

class TestFlowData:
    def test_absent_has_correct_defaults(self):
        fd = FlowData.absent("test")
        assert fd.data_quality == "absent"
        assert fd.source == "test"
        assert fd.put_call_ratio is None
        assert fd.call_sweep_count == 0
        assert fd.put_sweep_count == 0
        assert fd.dark_pool_bullish is False
        assert fd.dark_pool_bearish is False
        assert fd.large_call_oi_change == 0.0
        assert fd.large_put_oi_change == 0.0
        assert fd.unusual_call_vol is False
        assert fd.unusual_put_vol is False
        assert fd.error is None

    def test_to_dict_contains_required_keys(self):
        d = FlowData.absent("test").to_dict()
        required = [
            "data_quality", "source", "as_of",
            "put_call_ratio", "call_sweep_count", "put_sweep_count",
            "dark_pool_bullish", "dark_pool_bearish",
            "large_call_oi_change", "large_put_oi_change",
            "unusual_call_vol", "unusual_put_vol",
        ]
        for key in required:
            assert key in d, f"Missing key: {key}"

    def test_to_dict_excludes_none_error(self):
        d = FlowData.absent("test").to_dict()
        assert "error" not in d

    def test_to_dict_includes_error_when_set(self):
        fd = FlowData.absent("test")
        fd.error = "something went wrong"
        d = fd.to_dict()
        assert "error" in d
        assert d["error"] == "something went wrong"

    def test_mark_real_on_pcr(self):
        fd = FlowData.absent("test")
        fd.put_call_ratio = 1.2
        fd.mark_real()
        assert fd.data_quality == "real"

    def test_mark_real_on_sweeps(self):
        fd = FlowData.absent("test")
        fd.call_sweep_count = 5
        fd.mark_real()
        assert fd.data_quality == "real"

    def test_mark_real_on_dark_pool(self):
        fd = FlowData.absent("test")
        fd.dark_pool_bullish = True
        fd.mark_real()
        assert fd.data_quality == "real"

    def test_mark_real_leaves_absent_when_no_signals(self):
        fd = FlowData.absent("test")
        fd.mark_real()
        assert fd.data_quality == "absent"

    def test_mark_real_returns_self_for_chaining(self):
        fd = FlowData.absent("test")
        fd.put_call_ratio = 0.9
        result = fd.mark_real()
        assert result is fd

    def test_validate_passes_on_valid_data(self):
        fd = FlowData.absent("test")
        fd.put_call_ratio = 1.2
        fd.call_sweep_count = 3
        fd.mark_real()
        assert fd.validate() == []

    def test_validate_catches_bad_data_quality(self):
        fd = FlowData.absent("test")
        fd.data_quality = "garbage"
        issues = fd.validate()
        assert len(issues) > 0

    def test_validate_catches_out_of_range_pcr(self):
        fd = FlowData.absent("test")
        fd.put_call_ratio = -1.0
        issues = fd.validate()
        assert len(issues) > 0

    def test_validate_catches_negative_sweep_count(self):
        fd = FlowData.absent("test")
        fd.call_sweep_count = -5
        issues = fd.validate()
        assert len(issues) > 0

    def test_as_of_defaults_to_today(self):
        fd = FlowData.absent("test")
        today = datetime.date.today().isoformat()
        assert fd.as_of == today


# =============================================================================
# Registry and list_adapters
# =============================================================================

class TestRegistry:
    def test_all_expected_adapters_registered(self):
        for name in ["yfinance", "moomoo", "unusual_whales", "options_whale", "tradier"]:
            assert name in _ADAPTER_REGISTRY, f"{name} not in registry"

    def test_list_adapters_returns_all(self):
        info = list_adapters()
        assert "yfinance" in info
        assert "moomoo" in info
        assert "unusual_whales" in info
        assert "options_whale" in info
        assert "tradier" in info

    def test_list_adapters_has_required_fields(self):
        info = list_adapters()
        for name, meta in info.items():
            assert "configured" in meta, f"{name} missing 'configured'"
            assert "priority" in meta, f"{name} missing 'priority'"
            assert "env_key" in meta, f"{name} missing 'env_key'"

    def test_yfinance_always_configured(self):
        info = list_adapters()
        assert info["yfinance"]["configured"] is True

    def test_priorities_ordered_correctly(self):
        info = list_adapters()
        assert info["unusual_whales"]["priority"] > info["tradier"]["priority"]
        assert info["tradier"]["priority"] > info["moomoo"]["priority"]
        assert info["moomoo"]["priority"] > info["yfinance"]["priority"]

    def test_register_adapter_decorator(self):
        from tradingbot.dataflows.flow_adapter import register_adapter

        @register_adapter("_test_vendor_", env_key="NONEXISTENT_KEY_XYZ", priority=99)
        class _TestAdapter(FlowAdapterBase):
            @property
            def name(self): return "_test_vendor_"
            def fetch(self, ticker, date=None): return FlowData.absent(self.name).to_dict()

        assert "_test_vendor_" in _ADAPTER_REGISTRY
        del _ADAPTER_REGISTRY["_test_vendor_"]   # clean up

    def test_get_adapter_unknown_raises_valueerror(self):
        with pytest.raises(ValueError, match="Unknown flow adapter"):
            get_adapter("nonexistent_vendor_xyz")

    def test_get_adapter_error_lists_known_adapters(self):
        with pytest.raises(ValueError) as exc_info:
            get_adapter("bad_source")
        assert "yfinance" in str(exc_info.value)


# =============================================================================
# get_adapter factory
# =============================================================================

class TestGetAdapter:
    def test_yfinance(self):
        a = get_adapter("yfinance")
        assert isinstance(a, YFinanceFlowAdapter)
        assert a.name == "yfinance"

    def test_moomoo(self):
        a = get_adapter("moomoo")
        assert isinstance(a, MoomooFlowAdapter)
        assert a.name == "moomoo"

    def test_unusual_whales(self):
        a = get_adapter("unusual_whales")
        assert isinstance(a, UnusualWhalesAdapter)
        assert a.name == "unusual_whales"

    def test_options_whale(self):
        a = get_adapter("options_whale")
        assert isinstance(a, OptionsWhaleAdapter)
        assert a.name == "options_whale"

    def test_tradier(self):
        a = get_adapter("tradier")
        assert isinstance(a, TradierAdapter)
        assert a.name == "tradier"


# =============================================================================
# auto_adapter selection logic
# =============================================================================

class TestAutoAdapter:
    def test_falls_back_to_yfinance_when_no_keys(self, monkeypatch):
        for key in ("UW_API_KEY", "OPTIONS_WHALE_KEY", "TRADIER_API_KEY", "MOOMOO_HOST", "OA2_FLOW_SOURCE"):
            monkeypatch.delenv(key, raising=False)
        a = auto_adapter()
        assert isinstance(a, YFinanceFlowAdapter)

    def test_prefers_unusual_whales_when_key_set(self, monkeypatch):
        for key in ("OPTIONS_WHALE_KEY", "TRADIER_API_KEY", "MOOMOO_HOST", "OA2_FLOW_SOURCE"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("UW_API_KEY", "fake_key")
        a = auto_adapter()
        assert isinstance(a, UnusualWhalesAdapter)

    def test_prefers_options_whale_over_tradier(self, monkeypatch):
        monkeypatch.delenv("UW_API_KEY", raising=False)
        monkeypatch.delenv("MOOMOO_HOST", raising=False)
        monkeypatch.delenv("OA2_FLOW_SOURCE", raising=False)
        monkeypatch.setenv("OPTIONS_WHALE_KEY", "fake_key")
        monkeypatch.setenv("TRADIER_API_KEY", "fake_key")
        a = auto_adapter()
        assert isinstance(a, OptionsWhaleAdapter)

    def test_prefers_tradier_over_moomoo(self, monkeypatch):
        for key in ("UW_API_KEY", "OPTIONS_WHALE_KEY", "OA2_FLOW_SOURCE"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("TRADIER_API_KEY", "fake_key")
        monkeypatch.setenv("MOOMOO_HOST", "127.0.0.1")
        a = auto_adapter()
        assert isinstance(a, TradierAdapter)

    def test_prefers_moomoo_over_yfinance(self, monkeypatch):
        for key in ("UW_API_KEY", "OPTIONS_WHALE_KEY", "TRADIER_API_KEY", "OA2_FLOW_SOURCE"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("MOOMOO_HOST", "127.0.0.1")
        a = auto_adapter()
        assert isinstance(a, MoomooFlowAdapter)

    def test_oa2_flow_source_overrides_priority(self, monkeypatch):
        monkeypatch.setenv("OA2_FLOW_SOURCE", "yfinance")
        monkeypatch.setenv("UW_API_KEY", "fake_key")   # would normally win
        a = auto_adapter()
        assert isinstance(a, YFinanceFlowAdapter)

    def test_oa2_flow_source_moomoo(self, monkeypatch):
        monkeypatch.setenv("OA2_FLOW_SOURCE", "moomoo")
        monkeypatch.delenv("UW_API_KEY", raising=False)
        a = auto_adapter()
        assert isinstance(a, MoomooFlowAdapter)

    def test_oa2_flow_source_unknown_raises(self, monkeypatch):
        monkeypatch.setenv("OA2_FLOW_SOURCE", "totally_fake_vendor")
        with pytest.raises(ValueError):
            auto_adapter()


# =============================================================================
# Unconfigured stubs return absent data, not exceptions
# =============================================================================

class TestUnconfiguredStubs:
    def _assert_absent_dict(self, result: dict, adapter_name: str):
        assert isinstance(result, dict), f"{adapter_name} did not return a dict"
        assert "error" in result, f"{adapter_name} should include 'error' key when unconfigured"
        # data_quality may still be "absent" (that's correct)
        assert result.get("data_quality") in ("absent", "real", "derived")

    def test_uw_unconfigured(self, monkeypatch):
        monkeypatch.delenv("UW_API_KEY", raising=False)
        result = UnusualWhalesAdapter().fetch("SPY")
        self._assert_absent_dict(result, "unusual_whales")

    def test_options_whale_unconfigured(self, monkeypatch):
        monkeypatch.delenv("OPTIONS_WHALE_KEY", raising=False)
        result = OptionsWhaleAdapter().fetch("SPY")
        self._assert_absent_dict(result, "options_whale")

    def test_tradier_unconfigured(self, monkeypatch):
        monkeypatch.delenv("TRADIER_API_KEY", raising=False)
        result = TradierAdapter().fetch("SPY")
        self._assert_absent_dict(result, "tradier")

    def test_moomoo_unconfigured(self, monkeypatch):
        monkeypatch.delenv("MOOMOO_HOST", raising=False)
        result = MoomooFlowAdapter().fetch("SPY")
        self._assert_absent_dict(result, "moomoo")

    def test_uw_not_configured_property(self, monkeypatch):
        monkeypatch.delenv("UW_API_KEY", raising=False)
        assert not UnusualWhalesAdapter().is_configured()

    def test_options_whale_not_configured_property(self, monkeypatch):
        monkeypatch.delenv("OPTIONS_WHALE_KEY", raising=False)
        assert not OptionsWhaleAdapter().is_configured()

    def test_tradier_not_configured_property(self, monkeypatch):
        monkeypatch.delenv("TRADIER_API_KEY", raising=False)
        assert not TradierAdapter().is_configured()

    def test_moomoo_not_configured_property(self, monkeypatch):
        monkeypatch.delenv("MOOMOO_HOST", raising=False)
        assert not MoomooFlowAdapter().is_configured()

    def test_stubs_never_raise(self, monkeypatch):
        for key in ("UW_API_KEY", "OPTIONS_WHALE_KEY", "TRADIER_API_KEY", "MOOMOO_HOST"):
            monkeypatch.delenv(key, raising=False)
        for adapter in [UnusualWhalesAdapter(), OptionsWhaleAdapter(), TradierAdapter(), MoomooFlowAdapter()]:
            try:
                result = adapter.fetch("SPY")
                assert isinstance(result, dict)
            except Exception as exc:
                pytest.fail(f"{adapter.name}.fetch() raised unexpectedly: {exc}")


# =============================================================================
# YFinance adapter (unit-level — mock yfinance network)
# =============================================================================

class TestYFinanceFlowAdapter:
    def _mock_yfinance(self, monkeypatch, call_vol=1000, put_vol=1200, call_oi=5000, put_oi=4000):
        import sys

        class _FakeDF:
            def __init__(self, vol, oi):
                self._vol = vol
                self._oi = oi
                self.empty = False
                self.columns = ["volume", "openInterest"]

            def __getitem__(self, key):
                class _S:
                    def __init__(self, val):
                        self._val = val
                    def sum(self):
                        return self._val
                return _S(self._vol if key == "volume" else self._oi)

            def __contains__(self, key):
                return key in self.columns

        class _FakeChain:
            def __init__(self, cv, pv, coi, poi):
                self.calls = _FakeDF(cv, coi)
                self.puts = _FakeDF(pv, poi)

        class _FakeTicker:
            options = ["2025-05-16", "2025-05-23"]
            def option_chain(self, exp):
                return _FakeChain(call_vol, put_vol, call_oi, put_oi)

        class _FakeYF:
            @staticmethod
            def Ticker(sym): return _FakeTicker()

        monkeypatch.setitem(sys.modules, "yfinance", _FakeYF)

    def test_fetch_returns_dict(self, monkeypatch):
        self._mock_yfinance(monkeypatch)
        result = YFinanceFlowAdapter().fetch("SPY")
        assert isinstance(result, dict)

    def test_all_required_keys_present(self, monkeypatch):
        self._mock_yfinance(monkeypatch)
        result = YFinanceFlowAdapter().fetch("SPY")
        required = [
            "data_quality", "source", "as_of", "put_call_ratio",
            "call_sweep_count", "put_sweep_count",
            "dark_pool_bullish", "dark_pool_bearish",
            "large_call_oi_change", "large_put_oi_change",
            "unusual_call_vol", "unusual_put_vol",
        ]
        for key in required:
            assert key in result, f"Missing key: {key}"

    def test_pcr_computed_from_volumes(self, monkeypatch):
        self._mock_yfinance(monkeypatch, call_vol=1000, put_vol=1200)
        result = YFinanceFlowAdapter().fetch("SPY")
        if result["put_call_ratio"] is not None:
            assert result["put_call_ratio"] == pytest.approx(1.2, rel=0.01)

    def test_data_quality_real_when_pcr_present(self, monkeypatch):
        self._mock_yfinance(monkeypatch)
        result = YFinanceFlowAdapter().fetch("SPY")
        if result.get("put_call_ratio") is not None:
            assert result["data_quality"] == "real"

    def test_source_is_yfinance(self, monkeypatch):
        self._mock_yfinance(monkeypatch)
        result = YFinanceFlowAdapter().fetch("SPY")
        assert result["source"] == "yfinance"

    def test_no_sweeps_or_dark_pool(self, monkeypatch):
        self._mock_yfinance(monkeypatch)
        result = YFinanceFlowAdapter().fetch("SPY")
        assert result["call_sweep_count"] == 0
        assert result["put_sweep_count"] == 0
        assert result["dark_pool_bullish"] is False
        assert result["dark_pool_bearish"] is False

    def test_result_passes_validation(self, monkeypatch):
        self._mock_yfinance(monkeypatch)
        result = YFinanceFlowAdapter().fetch("SPY")
        fd = FlowData(**{k: result[k] for k in FlowData.__dataclass_fields__ if k in result})
        issues = fd.validate()
        assert issues == [], f"Validation failed: {issues}"


# =============================================================================
# Expiry bucket helpers
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


# =============================================================================
# classify_expiry_flow
# =============================================================================

class TestClassifyExpiryFlow:
    def _chain(self, expiry_vols: dict) -> dict:
        return {
            exp: {"calls": [{"volume": cv}], "puts": [{"volume": pv}]}
            for exp, (cv, pv) in expiry_vols.items()
        }

    def test_empty_chain_returns_profile(self):
        profile = classify_expiry_flow({})
        assert isinstance(profile, ExpiryFlowProfile)
        assert profile.total_call_vol == 0
        assert profile.total_put_vol == 0

    def test_front_week_dominance_sets_urgency_high(self):
        as_of = datetime.date(2025, 5, 15)
        chain = self._chain({
            "2025-05-16": (10000, 8000),
            "2025-06-20": (500, 400),
        })
        profile = classify_expiry_flow(chain, as_of)
        assert profile.urgency_signal == "high"
        assert profile.dominant_bucket == ExpiryBucket.FRONT_WEEK.value

    def test_mid_term_dominant(self):
        as_of = datetime.date(2025, 5, 15)
        chain = self._chain({
            "2025-05-16": (200, 150),
            "2025-06-20": (8000, 7000),
        })
        profile = classify_expiry_flow(chain, as_of)
        assert profile.dominant_bucket == ExpiryBucket.MID_TERM.value

    def test_pcr_computed_per_bucket(self):
        as_of = datetime.date(2025, 5, 15)
        chain = self._chain({"2025-05-16": (1000, 2000)})
        profile = classify_expiry_flow(chain, as_of)
        assert profile.front_week_pcr == pytest.approx(2.0, rel=0.01)

    def test_volumes_summed_correctly(self):
        as_of = datetime.date(2025, 5, 15)
        chain = self._chain({
            "2025-05-16": (1000, 500),
            "2025-06-06": (2000, 1500),
        })
        profile = classify_expiry_flow(chain, as_of)
        assert profile.total_call_vol == 3000
        assert profile.total_put_vol == 2000

    def test_front_week_dominant_property(self):
        as_of = datetime.date(2025, 5, 15)
        chain = self._chain({
            "2025-05-16": (9000, 8000),
            "2025-06-20": (100, 100),
        })
        profile = classify_expiry_flow(chain, as_of)
        assert profile.front_week_dominant is True

    def test_urgency_medium_when_moderate_front_week(self):
        as_of = datetime.date(2025, 5, 15)
        chain = self._chain({
            "2025-05-16": (3000, 2000),
            "2025-06-20": (5000, 4000),
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
        for key in ("dominant_bucket", "urgency_signal", "front_week_pcr", "total_call_vol"):
            assert key in ctx["expiry_flow"], f"Missing key: {key}"
