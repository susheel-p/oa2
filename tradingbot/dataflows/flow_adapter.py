"""Flow data adapters — Phase E2.

Vendor-agnostic adapter layer that feeds real options flow signals into
the FlowDebater. Adding a new vendor takes three steps:

    1. Write a class that extends FlowAdapterBase and implements fetch().
    2. Decorate it with @register_adapter("name", env_key="MY_API_KEY", priority=N).
    3. Done — get_adapter("name") and auto_adapter() pick it up automatically.

No changes to the factory, feature flags, or FlowDebater are needed.

-----------------------------------------------------------------------
Available adapters (registered below):

  yfinance        — free, EOD. PCR + unusual volume. Always available.
  moomoo          — real-time chain via local OpenD gateway (MOOMOO_HOST).
  unusual_whales  — real sweeps + dark pool tape (UW_API_KEY).
  options_whale   — real sweeps, flow alerts (OPTIONS_WHALE_KEY).
  tradier         — real-time chain, tick-level volume (TRADIER_API_KEY).

-----------------------------------------------------------------------
Priority ordering (auto_adapter picks highest-priority configured source):

  unusual_whales  40  ← best quality: sweeps + dark pool
  options_whale   35
  tradier         25
  moomoo          20  ← real-time chain, already in the project's stack
  yfinance         0  ← always available; fallback

-----------------------------------------------------------------------
Usage:

    # Explicit:
    adapter = get_adapter("moomoo")
    flow_data = adapter.fetch("SPY")
    context["flow_data"] = flow_data          # pass to pipeline

    # Automatic (picks best available from env vars):
    adapter = auto_adapter()

    # Force a specific source via env var (overrides priority order):
    OA2_FLOW_SOURCE=moomoo  python run.py

    # List what is currently configured:
    for name, info in list_adapters().items():
        print(name, info["configured"], info["priority"])

-----------------------------------------------------------------------
Contract (FlowData):

    Every adapter must return a FlowData instance (call FlowData.to_dict()
    to get the plain dict that FlowDebater reads from context["flow_data"]).

    Required fields: data_quality, source, as_of.
    When data is unavailable: return FlowData.absent(source) — never raise.

-----------------------------------------------------------------------
FlowDebater signals expected:

    flow_data["data_quality"]          "real" | "derived" | "absent"
    flow_data["put_call_ratio"]        float | None
    flow_data["call_sweep_count"]      int
    flow_data["put_sweep_count"]       int
    flow_data["dark_pool_bullish"]     bool
    flow_data["dark_pool_bearish"]     bool
    flow_data["large_call_oi_change"]  float  (fractional, e.g. 0.15 = 15%)
    flow_data["large_put_oi_change"]   float
    flow_data["unusual_call_vol"]      bool
    flow_data["unusual_put_vol"]       bool
"""

from __future__ import annotations

import datetime
import os
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any


# ---------------------------------------------------------------------------
# FlowData — typed contract
# ---------------------------------------------------------------------------

@dataclass
class FlowData:
    """Typed representation of all signals the FlowDebater can consume.

    Create via FlowData.absent(source) and then set only the fields your
    vendor actually provides. Call to_dict() to pass to the pipeline.
    """
    source: str
    as_of: str = field(default_factory=lambda: datetime.date.today().isoformat())
    data_quality: str = "absent"       # "real" | "derived" | "absent"

    # Core flow signals
    put_call_ratio: float | None = None
    call_sweep_count: int = 0
    put_sweep_count: int = 0
    dark_pool_bullish: bool = False
    dark_pool_bearish: bool = False
    large_call_oi_change: float = 0.0  # fractional: 0.15 = +15%
    large_put_oi_change: float = 0.0
    unusual_call_vol: bool = False
    unusual_put_vol: bool = False

    # Optional diagnostic / error field (does not affect FlowDebater)
    error: str | None = None

    # ----------------------------------------------------------------
    # Class-level helpers
    # ----------------------------------------------------------------

    @classmethod
    def absent(cls, source: str) -> "FlowData":
        """Return an all-absent FlowData with correct defaults.

        Use this as the starting point inside every adapter's fetch() so
        you only override the fields your vendor actually provides.
        """
        return cls(source=source, data_quality="absent")

    def mark_real(self) -> "FlowData":
        """Mark data_quality='real' if at least one meaningful signal is set.

        Leaves data_quality unchanged if no real signals are present.
        """
        has_signal = (
            self.put_call_ratio is not None
            or self.call_sweep_count > 0
            or self.put_sweep_count > 0
            or self.dark_pool_bullish
            or self.dark_pool_bearish
            or abs(self.large_call_oi_change) > 0.0
            or abs(self.large_put_oi_change) > 0.0
            or self.unusual_call_vol
            or self.unusual_put_vol
        )
        if has_signal:
            self.data_quality = "real"
        return self

    def to_dict(self) -> dict[str, Any]:
        """Convert to plain dict compatible with FlowDebater / pipeline context.

        Excludes the 'error' key when error is None to keep the dict clean.
        """
        d = asdict(self)
        if d["error"] is None:
            del d["error"]
        return d

    # ----------------------------------------------------------------
    # Validation
    # ----------------------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of validation warnings (empty = all good).

        Does NOT raise — call validate() to surface issues in tests or
        adapter health checks.
        """
        issues = []
        if self.data_quality not in ("real", "derived", "absent"):
            issues.append(f"data_quality must be 'real'|'derived'|'absent', got {self.data_quality!r}")
        if self.put_call_ratio is not None and not (0.0 <= self.put_call_ratio <= 20.0):
            issues.append(f"put_call_ratio {self.put_call_ratio} looks out of range [0, 20]")
        if self.call_sweep_count < 0:
            issues.append("call_sweep_count must be >= 0")
        if self.put_sweep_count < 0:
            issues.append("put_sweep_count must be >= 0")
        if not (-1.0 <= self.large_call_oi_change <= 50.0):
            issues.append(f"large_call_oi_change {self.large_call_oi_change} looks out of range")
        if not (-1.0 <= self.large_put_oi_change <= 50.0):
            issues.append(f"large_put_oi_change {self.large_put_oi_change} looks out of range")
        return issues


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@dataclass
class _AdapterRegistration:
    cls: type           # the adapter class
    env_key: str | None # env var that signals this adapter is configured
    priority: int       # higher = preferred by auto_adapter()


_ADAPTER_REGISTRY: dict[str, _AdapterRegistration] = {}


def register_adapter(name: str, env_key: str | None = None, priority: int = 0):
    """Class decorator to register a flow adapter.

    Args:
        name:     Unique adapter key (used in get_adapter() and OA2_FLOW_SOURCE).
        env_key:  Environment variable that must be non-empty for this adapter
                  to be considered "configured" by auto_adapter().
                  Pass None for adapters that need no credentials (e.g. yfinance).
        priority: Higher number = preferred by auto_adapter() when multiple
                  adapters are configured. yfinance is 0 (lowest fallback).

    Example:
        @register_adapter("my_vendor", env_key="MY_VENDOR_KEY", priority=30)
        class MyVendorAdapter(FlowAdapterBase):
            ...
    """
    def decorator(cls: type) -> type:
        _ADAPTER_REGISTRY[name] = _AdapterRegistration(cls=cls, env_key=env_key, priority=priority)
        return cls
    return decorator


def list_adapters() -> dict[str, dict[str, Any]]:
    """Return info about all registered adapters and their configuration status.

    Useful for health checks and logging at startup.

    Returns:
        {
            "yfinance": {"configured": True, "priority": 0, "env_key": None},
            "moomoo":   {"configured": False, "priority": 20, "env_key": "MOOMOO_HOST"},
            ...
        }
    """
    result = {}
    for name, reg in sorted(_ADAPTER_REGISTRY.items(), key=lambda x: -x[1].priority):
        configured = reg.env_key is None or bool(os.getenv(reg.env_key))
        result[name] = {
            "configured": configured,
            "priority": reg.priority,
            "env_key": reg.env_key,
        }
    return result


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class FlowAdapterBase(ABC):
    """Base class for all flow data adapters.

    Subclass this, implement fetch() and name, then decorate with
    @register_adapter("my_name", ...) — that's all that's required.
    """

    @abstractmethod
    def fetch(self, ticker: str, date: str | None = None) -> dict[str, Any]:
        """Fetch flow signals for ticker.

        Args:
            ticker: underlying symbol (e.g., "SPY").
            date:   ISO date string (YYYY-MM-DD) for historical fetch;
                    None = today's data.

        Returns:
            Plain dict (call FlowData(...).to_dict() at the end).
            Contract: NEVER raise on data unavailability. Instead return
            FlowData.absent(self.name).to_dict() with an "error" key set.
            The FlowDebater handles absent/low-quality data via abstention.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short adapter name matching the registry key."""


# ---------------------------------------------------------------------------
# YFinance adapter — free, EOD, always available
# ---------------------------------------------------------------------------

@register_adapter("yfinance", env_key=None, priority=0)
class YFinanceFlowAdapter(FlowAdapterBase):
    """EOD flow signals from the yfinance options chain.

    Provides (real EOD market data, not fabricated):
        put_call_ratio   — total put vol / call vol from front-month chain
        unusual_call_vol — True when call vol > unusual_vol_multiplier × OI/252
        unusual_put_vol  — True when put vol > unusual_vol_multiplier × OI/252

    Does NOT provide (requires real tape):
        call_sweep_count, put_sweep_count, dark_pool_bullish/bearish,
        large_call_oi_change, large_put_oi_change

    No credentials needed. Marks data_quality="real" when signals are present.
    Fallback of last resort — always succeeds.
    """

    def __init__(self, unusual_vol_multiplier: float = 2.0):
        self._unusual_mult = unusual_vol_multiplier

    @property
    def name(self) -> str:
        return "yfinance"

    def fetch(self, ticker: str, date: str | None = None) -> dict[str, Any]:
        try:
            import yfinance as yf
        except ImportError:
            fd = FlowData.absent(self.name)
            fd.error = "yfinance not installed"
            return fd.to_dict()

        fd = FlowData.absent(self.name)
        fd.as_of = date or datetime.date.today().isoformat()

        try:
            t = yf.Ticker(ticker)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                exps = t.options
        except Exception as exc:
            fd.error = str(exc)
            return fd.to_dict()

        if not exps:
            return fd.to_dict()

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                chain = t.option_chain(exps[0])
        except Exception as exc:
            fd.error = str(exc)
            return fd.to_dict()

        calls = chain.calls
        puts = chain.puts

        if calls.empty and puts.empty:
            return fd.to_dict()

        total_call_vol = float(calls["volume"].sum()) if "volume" in calls.columns else 0.0
        total_put_vol = float(puts["volume"].sum()) if "volume" in puts.columns else 0.0

        if total_call_vol > 0:
            fd.put_call_ratio = round(total_put_vol / total_call_vol, 3)

        if "openInterest" in calls.columns and total_call_vol > 0:
            avg_call_oi = float(calls["openInterest"].sum())
            if avg_call_oi > 0:
                fd.unusual_call_vol = total_call_vol > (avg_call_oi * self._unusual_mult / 252)

        if "openInterest" in puts.columns and total_put_vol > 0:
            avg_put_oi = float(puts["openInterest"].sum())
            if avg_put_oi > 0:
                fd.unusual_put_vol = total_put_vol > (avg_put_oi * self._unusual_mult / 252)

        fd.mark_real()
        return fd.to_dict()


# ---------------------------------------------------------------------------
# Moomoo adapter — real-time chain via local OpenD gateway
# ---------------------------------------------------------------------------

@register_adapter("moomoo", env_key="MOOMOO_HOST", priority=20)
class MoomooFlowAdapter(FlowAdapterBase):
    """Real-time options chain via Moomoo's local OpenD gateway.

    Moomoo/Futu uses a local desktop gateway process (OpenD) rather than
    a remote API — the env var is MOOMOO_HOST (default: 127.0.0.1) plus
    MOOMOO_PORT (default: 11111). No external API key required.

    Provides (when OpenD is running):
        put_call_ratio       — real-time PCR from the live chain
        large_call_oi_change — day-over-day OI delta (moomoo tracks historical OI)
        large_put_oi_change
        unusual_call_vol     — True when vol > threshold
        unusual_put_vol

    Does NOT provide:
        call_sweep_count, put_sweep_count  (no sweep-detection in Moomoo API)
        dark_pool_bullish/bearish          (Moomoo does not surface dark pool)

    Setup:
        1. Install Moomoo OpenD desktop app
        2. Set MOOMOO_HOST=127.0.0.1 in Replit Secrets (or leave default)
        3. Set OA2_FLOW_SOURCE=moomoo to force this adapter
           OR leave unset — auto_adapter() will prefer moomoo over yfinance
           whenever MOOMOO_HOST is set.

    Wire-up (when moomoo-api package is installed):
        pip install moomoo-api
        from moomoo import OpenSecTradeContext, TrdMarket
        The full API reference: https://openapi.moomoo.com/moomoo-api-doc/
    """

    def __init__(self):
        self._host = os.getenv("MOOMOO_HOST", "127.0.0.1")
        self._port = int(os.getenv("MOOMOO_PORT", "11111"))

    @property
    def name(self) -> str:
        return "moomoo"

    def is_configured(self) -> bool:
        return bool(os.getenv("MOOMOO_HOST"))

    def fetch(self, ticker: str, date: str | None = None) -> dict[str, Any]:
        fd = FlowData.absent(self.name)
        fd.as_of = date or datetime.date.today().isoformat()

        if not self.is_configured():
            fd.error = (
                "MOOMOO_HOST not set — Moomoo adapter inactive. "
                "Set MOOMOO_HOST=127.0.0.1 (and start OpenD) via Replit Secrets."
            )
            return fd.to_dict()

        try:
            from tradingbot.dataflows.moomoo_data import fetch_market_snapshot
        except ImportError:
            fd.error = "moomoo_data module not found"
            return fd.to_dict()

        try:
            snapshot = fetch_market_snapshot(ticker)

            # Check for errors in snapshot
            if "error" in snapshot:
                fd.error = snapshot.get("error")
                return fd.to_dict()

            # Extract flow analysis from snapshot
            flow_analysis = snapshot.get("flow_analysis", {})
            depth_analysis = snapshot.get("depth_analysis", {})

            # DEBUG: Log what we got
            import logging as _logging
            _log = _logging.getLogger("tradingbot.flow_adapter")
            _log.warning(f"[{ticker}] Flow analysis: quality={flow_analysis.get('data_quality')}, pcr={flow_analysis.get('put_call_ratio')}, call_conc={flow_analysis.get('call_vol_concentration'):.1f}%, put_conc={flow_analysis.get('put_vol_concentration'):.1f}%")

            # Populate FlowData from moomoo analysis
            if flow_analysis.get("data_quality") in ("real", "partial"):
                fd.put_call_ratio = flow_analysis.get("put_call_ratio")

                # Detect unusual volume concentration (>65% in top 3 strikes = concentrated/unusual)
                call_conc = flow_analysis.get("call_vol_concentration", 0)
                put_conc = flow_analysis.get("put_vol_concentration", 0)
                fd.unusual_call_vol = call_conc > 65
                fd.unusual_put_vol = put_conc > 65

                # Count aggressive buys as proxy for sweeps
                fd.call_sweep_count = flow_analysis.get("aggressive_call_buying", 0)
                fd.put_sweep_count = flow_analysis.get("aggressive_put_buying", 0)

                # Gamma concentration >60% in top 3 indicates positioning/risk concentration
                call_gamma_conc = flow_analysis.get("call_gamma_concentration", 0)
                put_gamma_conc = flow_analysis.get("put_gamma_concentration", 0)

                # Use gamma concentration as a proxy for institutional activity
                # (high gamma = more dealer hedging activity)
                if call_gamma_conc > 60:
                    fd.unusual_call_vol = True
                if put_gamma_conc > 60:
                    fd.unusual_put_vol = True

                _log.info(f"[{ticker}] FlowData populated: pcr={fd.put_call_ratio}, sweeps=({fd.call_sweep_count},{fd.put_sweep_count}), unusual=({fd.unusual_call_vol},{fd.unusual_put_vol})")

            # Depth analysis for order flow imbalance
            if depth_analysis.get("data_quality") == "real":
                imbalance = depth_analysis.get("bid_ask_imbalance", 0)
                # Strong imbalance > 0.2 indicates heavy flow direction
                if imbalance > 0.20:
                    fd.dark_pool_bullish = True  # More buy pressure
                elif imbalance < -0.20:
                    fd.dark_pool_bearish = True  # More sell pressure

            fd.mark_real()
            return fd.to_dict()

        except Exception as e:
            fd.error = f"Moomoo fetch failed: {str(e)}"
            return fd.to_dict()


# ---------------------------------------------------------------------------
# Unusual Whales adapter — sweeps + dark pool
# ---------------------------------------------------------------------------

@register_adapter("unusual_whales", env_key="UW_API_KEY", priority=40)
class UnusualWhalesAdapter(FlowAdapterBase):
    """Unusual Whales API — real-time sweeps and dark pool tape.

    Provides all FlowDebater signals:
        put_call_ratio       — tape PCR (not derived from chain)
        call_sweep_count     — aggressive institutional call sweeps
        put_sweep_count
        dark_pool_bullish    — large dark pool prints on offer side
        dark_pool_bearish
        large_call_oi_change — UW tracks historical OI snapshots
        large_put_oi_change
        unusual_call_vol     — from UW's proprietary flow score
        unusual_put_vol

    Setup:
        1. Subscribe at https://unusualwhales.com  (~$50/mo Individual plan)
        2. Set UW_API_KEY via Replit Secrets
        3. auto_adapter() will prefer this over all other sources automatically

    Wire-up (when UW_API_KEY is set):
        GET https://api.unusualwhales.com/api/stock/{ticker}/options-flow
        Authorization: Bearer {UW_API_KEY}
        → parse "data" array: type, sentiment, strike, expiry, volume, premium
    """

    def __init__(self):
        self._api_key = os.getenv("UW_API_KEY")

    @property
    def name(self) -> str:
        return "unusual_whales"

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def fetch(self, ticker: str, date: str | None = None) -> dict[str, Any]:
        fd = FlowData.absent(self.name)

        if not self.is_configured():
            fd.error = "UW_API_KEY not set — Unusual Whales adapter inactive. Set via Replit Secrets."
            return fd.to_dict()

        # TODO: implement when UW_API_KEY is available.
        #
        # import requests
        # endpoint = f"https://api.unusualwhales.com/api/stock/{ticker}/options-flow"
        # headers = {"Authorization": f"Bearer {self._api_key}"}
        # params = {"date": date} if date else {}
        # resp = requests.get(endpoint, headers=headers, params=params, timeout=10)
        # resp.raise_for_status()
        # flows = resp.json().get("data", [])
        #
        # call_sweeps = sum(1 for f in flows if f["type"] == "CALL" and f["sentiment"] == "BULLISH")
        # put_sweeps  = sum(1 for f in flows if f["type"] == "PUT"  and f["sentiment"] == "BEARISH")
        # fd.call_sweep_count = call_sweeps
        # fd.put_sweep_count  = put_sweeps
        # fd.mark_real()
        fd.error = "Unusual Whales API wiring not yet implemented — see flow_adapter.py TODO."
        return fd.to_dict()


# ---------------------------------------------------------------------------
# OptionsWhale adapter — flow alerts + sweeps
# ---------------------------------------------------------------------------

@register_adapter("options_whale", env_key="OPTIONS_WHALE_KEY", priority=35)
class OptionsWhaleAdapter(FlowAdapterBase):
    """OptionsWhale.com API — options flow alerts and sweep detection.

    Provides:
        call_sweep_count, put_sweep_count — sweep alerts from their feed
        put_call_ratio                    — from aggregated flow data
        unusual_call_vol, unusual_put_vol — from flow-score signals

    Setup:
        1. Subscribe at https://optionswhale.com
        2. Set OPTIONS_WHALE_KEY via Replit Secrets
        3. auto_adapter() will prefer this over moomoo and yfinance

    Wire-up (when OPTIONS_WHALE_KEY is set):
        GET https://api.optionswhale.com/v1/flow?ticker={ticker}
        X-API-Key: {OPTIONS_WHALE_KEY}
        → parse flow alerts into sweep counts, PCR
    """

    def __init__(self):
        self._api_key = os.getenv("OPTIONS_WHALE_KEY")

    @property
    def name(self) -> str:
        return "options_whale"

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def fetch(self, ticker: str, date: str | None = None) -> dict[str, Any]:
        fd = FlowData.absent(self.name)

        if not self.is_configured():
            fd.error = "OPTIONS_WHALE_KEY not set — OptionsWhale adapter inactive. Set via Replit Secrets."
            return fd.to_dict()

        # TODO: implement when OPTIONS_WHALE_KEY is available.
        #
        # import requests
        # endpoint = f"https://api.optionswhale.com/v1/flow"
        # headers = {"X-API-Key": self._api_key}
        # params = {"ticker": ticker}
        # if date: params["date"] = date
        # resp = requests.get(endpoint, headers=headers, params=params, timeout=10)
        # resp.raise_for_status()
        # data = resp.json()
        # fd.call_sweep_count = data.get("call_sweeps", 0)
        # fd.put_sweep_count  = data.get("put_sweeps", 0)
        # fd.mark_real()
        fd.error = "OptionsWhale API wiring not yet implemented — see flow_adapter.py TODO."
        return fd.to_dict()


# ---------------------------------------------------------------------------
# Tradier adapter — real-time chain
# ---------------------------------------------------------------------------

@register_adapter("tradier", env_key="TRADIER_API_KEY", priority=25)
class TradierAdapter(FlowAdapterBase):
    """Tradier brokerage API — real-time options chain.

    Provides:
        put_call_ratio       — real-time PCR from live chain volume
        large_call_oi_change — day-over-day OI delta
        large_put_oi_change
        unusual_call_vol, unusual_put_vol

    Does NOT provide:
        call_sweep_count, put_sweep_count (no sweep detection in Tradier chain API)
        dark_pool_bullish/bearish

    Setup:
        1. Open a Tradier account at https://tradier.com  (~$10/mo)
        2. Set TRADIER_API_KEY via Replit Secrets
        3. Set TRADIER_SANDBOX=0 in Replit Secrets to use production endpoint

    Wire-up (when TRADIER_API_KEY is set):
        GET /v1/markets/options/chains?symbol={ticker}&expiration={exp}&greeks=true
        Authorization: Bearer {TRADIER_API_KEY}
    """

    def __init__(self):
        self._api_key = os.getenv("TRADIER_API_KEY")
        self._sandbox = os.getenv("TRADIER_SANDBOX", "1") == "1"

    @property
    def name(self) -> str:
        return "tradier"

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def fetch(self, ticker: str, date: str | None = None) -> dict[str, Any]:
        fd = FlowData.absent(self.name)

        if not self.is_configured():
            fd.error = "TRADIER_API_KEY not set — Tradier adapter inactive. Set via Replit Secrets."
            return fd.to_dict()

        # TODO: implement when TRADIER_API_KEY is available.
        #
        # import requests
        # base = "https://sandbox.tradier.com" if self._sandbox else "https://api.tradier.com"
        # headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        # exps_resp = requests.get(f"{base}/v1/markets/options/expirations",
        #     headers=headers, params={"symbol": ticker}, timeout=10)
        # exps = exps_resp.json()["expirations"]["date"]
        # chain_resp = requests.get(f"{base}/v1/markets/options/chains",
        #     headers=headers, params={"symbol": ticker, "expiration": exps[0]}, timeout=10)
        # options = chain_resp.json()["options"]["option"]
        # call_vol = sum(o["volume"] for o in options if o["option_type"] == "call")
        # put_vol  = sum(o["volume"] for o in options if o["option_type"] == "put")
        # if call_vol > 0:
        #     fd.put_call_ratio = round(put_vol / call_vol, 3)
        # fd.mark_real()
        fd.error = "Tradier API wiring not yet implemented — see flow_adapter.py TODO."
        return fd.to_dict()


# ---------------------------------------------------------------------------
# Factory (reads from registry — no hardcoded adapter list)
# ---------------------------------------------------------------------------

def get_adapter(source: str) -> FlowAdapterBase:
    """Return a flow adapter instance by registry name.

    Args:
        source: adapter name as registered (e.g. "yfinance", "moomoo",
                "unusual_whales", "options_whale", "tradier", or any name
                registered via @register_adapter).

    Raises:
        ValueError if the name is not in the registry.
    """
    reg = _ADAPTER_REGISTRY.get(source)
    if reg is None:
        known = sorted(_ADAPTER_REGISTRY.keys())
        raise ValueError(f"Unknown flow adapter: {source!r}. Registered: {known}")
    return reg.cls()


def auto_adapter() -> FlowAdapterBase:
    """Return the best available adapter, respecting configuration.

    Selection logic (in order):
        1. If OA2_FLOW_SOURCE is set, use that adapter explicitly.
           Raises ValueError if the named adapter is not registered.
        2. Otherwise, find the registered adapter with the highest priority
           whose env_key is set (or whose env_key is None, i.e. always available).
           In practice this means:
               unusual_whales (40) if UW_API_KEY is set
               options_whale  (35) if OPTIONS_WHALE_KEY is set
               tradier        (25) if TRADIER_API_KEY is set
               moomoo         (20) if MOOMOO_HOST is set
               yfinance        (0) always

    To force a specific source regardless of keys:
        OA2_FLOW_SOURCE=moomoo  (set via Replit Secrets or shell export)
    """
    explicit = os.getenv("OA2_FLOW_SOURCE")
    if explicit:
        return get_adapter(explicit)

    best_name = "yfinance"
    best_priority = -1

    for name, reg in _ADAPTER_REGISTRY.items():
        is_configured = reg.env_key is None or bool(os.getenv(reg.env_key))
        if is_configured and reg.priority > best_priority:
            best_priority = reg.priority
            best_name = name

    return get_adapter(best_name)
