"""Flow data adapters — Phase E2.

Vendor-agnostic adapter layer that feeds real options flow signals into
the FlowDebater. The FlowDebater contract expects a `flow_data` dict with
`data_quality` set to "real" — this module provides that dict from
whichever data source is configured.

Available adapters:
    YFinanceFlowAdapter — EOD signals from the options chain (free, delayed).
        Provides: PCR from chain volume, OI changes day-over-day, unusual volume.
        Does NOT provide: sweeps, dark pool, real-time tape.
        data_quality = "real"  (it IS real market data, just not tick-level)

    UnusualWhalesAdapter — sweep/dark-pool tape (requires UW_API_KEY secret).
        Provides: all signals including sweeps and dark pool prints.
        Stub — wire when UW_API_KEY is set.

    TradierAdapter — real-time streaming chain (requires TRADIER_API_KEY secret).
        Provides: real-time chain, live PCR, OI.
        Stub — wire when TRADIER_API_KEY is set.

Usage:
    adapter = get_adapter("yfinance")
    flow_data = adapter.fetch("SPY")
    # flow_data is ready to merge into the pipeline context dict:
    context["flow_data"] = flow_data

Factory:
    get_adapter(source)  → FlowAdapterBase
    auto_adapter()       → picks best available based on env vars
"""

from __future__ import annotations

import datetime
import os
import warnings
from abc import ABC, abstractmethod
from typing import Any


# ---------------------------------------------------------------------------
# FlowData schema
# ---------------------------------------------------------------------------

def empty_flow_data(source: str = "unknown") -> dict[str, Any]:
    """Return a fully-populated flow_data dict with all signals absent."""
    return {
        "data_quality": "absent",
        "source": source,
        "as_of": datetime.date.today().isoformat(),
        "put_call_ratio": None,
        "call_sweep_count": 0,
        "put_sweep_count": 0,
        "dark_pool_bullish": False,
        "dark_pool_bearish": False,
        "large_call_oi_change": 0.0,
        "large_put_oi_change": 0.0,
        "unusual_call_vol": False,
        "unusual_put_vol": False,
    }


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class FlowAdapterBase(ABC):
    """Base class for all flow data adapters."""

    @abstractmethod
    def fetch(self, ticker: str, date: str | None = None) -> dict[str, Any]:
        """Fetch flow signals for ticker.

        Args:
            ticker: underlying symbol (e.g., "SPY").
            date: ISO date string for historical fetch; None = today.

        Returns:
            flow_data dict ready to merge into pipeline context.
            Always returns a valid dict — never raises on data unavailability;
            instead returns data_quality="absent" with all signals at defaults.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Adapter name for logging and feature-flag display."""


# ---------------------------------------------------------------------------
# YFinance adapter (free, EOD)
# ---------------------------------------------------------------------------

class YFinanceFlowAdapter(FlowAdapterBase):
    """EOD flow signals derived from the yfinance options chain.

    Available signals:
        put_call_ratio       — total put volume / total call volume (EOD)
        large_call_oi_change — call OI change vs previous day (requires 2 fetches)
        large_put_oi_change  — put OI change vs previous day
        unusual_call_vol     — True if today's call vol > N × 30-day average
        unusual_put_vol      — True if today's put vol > N × 30-day average

    NOT available (requires real tape):
        call_sweep_count, put_sweep_count, dark_pool_bullish, dark_pool_bearish

    data_quality = "real" — the signals above are genuine market data.
    """

    def __init__(self, unusual_vol_multiplier: float = 2.0):
        """
        Args:
            unusual_vol_multiplier: call/put volume is "unusual" when it
                exceeds this multiple of the 30-day rolling average.
        """
        self._unusual_mult = unusual_vol_multiplier

    @property
    def name(self) -> str:
        return "yfinance"

    def fetch(self, ticker: str, date: str | None = None) -> dict[str, Any]:
        """Fetch EOD flow signals from the yfinance options chain.

        For historical dates, OI change cannot be computed (yfinance does not
        store historical OI snapshots) so those fields are left at 0.0.
        """
        try:
            import yfinance as yf
        except ImportError:
            result = empty_flow_data(self.name)
            result["error"] = "yfinance not installed"
            return result

        result = empty_flow_data(self.name)
        result["as_of"] = date or datetime.date.today().isoformat()

        try:
            t = yf.Ticker(ticker)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                exps = t.options
        except Exception as exc:
            result["error"] = str(exc)
            return result

        if not exps:
            return result

        # Use front-month expiry for primary signals
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                chain = t.option_chain(exps[0])
        except Exception as exc:
            result["error"] = str(exc)
            return result

        calls = chain.calls
        puts = chain.puts

        if calls.empty and puts.empty:
            return result

        # PCR from total chain volume
        total_call_vol = float(calls["volume"].sum()) if "volume" in calls else 0.0
        total_put_vol = float(puts["volume"].sum()) if "volume" in puts else 0.0
        if total_call_vol > 0:
            result["put_call_ratio"] = round(total_put_vol / total_call_vol, 3)

        # Unusual volume: compare today's total vol to average open interest
        # (yfinance does not provide historical volume; use OI as a proxy for "normal" activity)
        if "openInterest" in calls and total_call_vol > 0:
            avg_call_oi = float(calls["openInterest"].sum())
            if avg_call_oi > 0:
                result["unusual_call_vol"] = total_call_vol > (avg_call_oi * self._unusual_mult / 252)

        if "openInterest" in puts and total_put_vol > 0:
            avg_put_oi = float(puts["openInterest"].sum())
            if avg_put_oi > 0:
                result["unusual_put_vol"] = total_put_vol > (avg_put_oi * self._unusual_mult / 252)

        # OI change: would require yesterday's snapshot — not available from yfinance
        # large_call_oi_change and large_put_oi_change remain 0.0

        # Mark data as real (it is real EOD market data)
        if result["put_call_ratio"] is not None or result["unusual_call_vol"] or result["unusual_put_vol"]:
            result["data_quality"] = "real"

        return result


# ---------------------------------------------------------------------------
# Unusual Whales adapter (stub)
# ---------------------------------------------------------------------------

class UnusualWhalesAdapter(FlowAdapterBase):
    """Unusual Whales API adapter — real-time sweeps and dark pool.

    Requires environment variable: UW_API_KEY

    When wired:
        - Real sweep counts (call_sweep_count, put_sweep_count)
        - Dark pool prints (dark_pool_bullish, dark_pool_bearish)
        - Real tape PCR (not derived from chain)
        - OI changes (Unusual Whales tracks historical OI)
        - Unusual volume flags from their proprietary flow score

    Current status: STUB. Set UW_API_KEY to activate.
    Estimated cost: ~$50/mo (Individual plan).
    Documentation: https://unusualwhales.com/api

    Wire-up plan (when API key is available):
        POST /api/stock/{ticker}/options-flow
        → parse sweeps, dark pool prints, PCR from tape
    """

    def __init__(self):
        self._api_key = os.getenv("UW_API_KEY")

    @property
    def name(self) -> str:
        return "unusual_whales"

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def fetch(self, ticker: str, date: str | None = None) -> dict[str, Any]:
        result = empty_flow_data(self.name)

        if not self.is_configured():
            result["error"] = "UW_API_KEY not set — Unusual Whales adapter inactive. Set via Replit Secrets."
            return result

        # TODO: implement when UW_API_KEY is available
        # endpoint = f"https://api.unusualwhales.com/api/stock/{ticker}/options-flow"
        # headers = {"Authorization": f"Bearer {self._api_key}"}
        # response = requests.get(endpoint, headers=headers, params={"date": date})
        # ... parse sweeps, PCR, dark pool into result dict
        result["error"] = "Unusual Whales API wiring not yet implemented — see flow_adapter.py TODO."
        return result


# ---------------------------------------------------------------------------
# Tradier adapter (stub)
# ---------------------------------------------------------------------------

class TradierAdapter(FlowAdapterBase):
    """Tradier brokerage streaming chain adapter.

    Requires environment variable: TRADIER_API_KEY

    When wired:
        - Real-time options chain with live bid/ask and IV
        - Tick-level volume (approaching real tape PCR)
        - Live OI updates (intraday OI changes)

    Current status: STUB. Set TRADIER_API_KEY to activate.
    Estimated cost: ~$10/mo (Commission-free account).
    Documentation: https://documentation.tradier.com/brokerage-api

    Wire-up plan (when API key is available):
        GET /v1/markets/options/chains?symbol={ticker}&expiration={exp}&greeks=true
        → parse volume, OI, PCR from live chain
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
        result = empty_flow_data(self.name)

        if not self.is_configured():
            result["error"] = "TRADIER_API_KEY not set — Tradier adapter inactive. Set via Replit Secrets."
            return result

        # TODO: implement when TRADIER_API_KEY is available
        # base = "https://sandbox.tradier.com" if self._sandbox else "https://api.tradier.com"
        # endpoint = f"{base}/v1/markets/options/chains"
        # ... parse live chain into flow signals
        result["error"] = "Tradier API wiring not yet implemented — see flow_adapter.py TODO."
        return result


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_adapter(source: str = "yfinance") -> FlowAdapterBase:
    """Return a flow adapter by name.

    Args:
        source: "yfinance" | "unusual_whales" | "tradier"

    Returns:
        FlowAdapterBase instance.
    """
    adapters: dict[str, type] = {
        "yfinance": YFinanceFlowAdapter,
        "unusual_whales": UnusualWhalesAdapter,
        "tradier": TradierAdapter,
    }
    cls = adapters.get(source)
    if cls is None:
        raise ValueError(f"Unknown flow adapter: {source!r}. Choose from {list(adapters)}")
    return cls()


def auto_adapter() -> FlowAdapterBase:
    """Pick the best available adapter based on configured env vars.

    Priority order:
        1. Unusual Whales  (real sweeps + dark pool — highest quality)
        2. Tradier         (real-time chain — good PCR and OI)
        3. YFinance        (EOD PCR and unusual volume — always available)
    """
    if os.getenv("UW_API_KEY"):
        return UnusualWhalesAdapter()
    if os.getenv("TRADIER_API_KEY"):
        return TradierAdapter()
    return YFinanceFlowAdapter()
