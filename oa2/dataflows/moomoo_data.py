"""Unified moomoo data fetcher — replaces yfinance, Alpaca, Tradier.

Single source of truth for:
  - Options chains + IV/Greeks
  - Price bars (OHLCV) for technicals
  - Real-time quotes
  - IV rank calculations
  - Technical indicators (EMA, RSI, VWAP, ATR)
  - Volume/volatility analysis

Install: pip install futu-openapi ta pandas numpy
"""

from __future__ import annotations

import os
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import warnings
import logging

from oa2.dataflows.iv_rank_validator import validate_and_normalize_iv_rank

logger = logging.getLogger(__name__)

try:
    import moomoo as ft
    from moomoo import OpenQuoteContext
    MOOMOO_AVAILABLE = True
    RET_OK = 0  # moomoo returns 0 on success
except ImportError:
    MOOMOO_AVAILABLE = False
    RET_OK = None

try:
    import pandas as pd
    import numpy as np
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

try:
    import talib
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False


def _creds() -> tuple[str, str, str]:
    """Get moomoo credentials from env."""
    username = os.getenv("MOOMOO_USERNAME", "")
    password = os.getenv("MOOMOO_PASSWORD", "")
    account_id = os.getenv("MOOMOO_ACCOUNT_ID", "")
    if not username or not password or not account_id:
        raise ValueError("MOOMOO_USERNAME, MOOMOO_PASSWORD, MOOMOO_ACCOUNT_ID required in .env")
    return username, password, account_id


# ── Quote Context (market data) ────────────────────────────────────────────

_quote_ctx = None
_subscribed_codes = set()

def _get_quote_context():
    """Lazy-load quote context (requires OpenD running on localhost:11111)."""
    global _quote_ctx
    if _quote_ctx is None:
        if not MOOMOO_AVAILABLE:
            raise ImportError("moomoo SDK not installed. Run: pip install moomoo-api")
        try:
            _quote_ctx = ft.OpenQuoteContext(host="127.0.0.1", port=11111)
        except Exception as e:
            raise Exception(f"Failed to connect to OpenD server at 127.0.0.1:11111. Ensure moomoo app is running and OpenD is enabled. Error: {e}")
    return _quote_ctx


def _ensure_subscribed(code: str, sub_types: list):
    """Subscribe to data streams if not already subscribed."""
    global _subscribed_codes
    ctx = _get_quote_context()
    try:
        # Create a subscription key for this code+subtypes combo
        sub_key = (code, tuple(sub_types))
        if sub_key not in _subscribed_codes:
            ret = ctx.subscribe([code], sub_types)
            if ret[0] == 0:  # subscribe returns (ret_code, None)
                _subscribed_codes.add(sub_key)
            else:
                warnings.warn(f"Failed to subscribe {code}: {ret[1]}")
    except Exception as e:
        warnings.warn(f"Subscription error for {code}: {e}")


# ── Price Bars (OHLCV) ─────────────────────────────────────────────────────

def fetch_bars(
    ticker: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    period: str = "1d",  # 1d only (moomoo K_DAY)
    limit: int = 250,
) -> pd.DataFrame:
    """Fetch price bars from moomoo.

    Args:
        ticker: Stock ticker (e.g., 'AAPL')
        start_date: ISO date (e.g., '2026-01-01') - filtered after fetch
        end_date: ISO date (e.g., '2026-05-15') - filtered after fetch
        period: Bar period (only '1d' supported for daily)
        limit: Max bars to fetch (default 250)

    Returns:
        DataFrame with columns: [timestamp, open, high, low, close, volume]
    """
    if not PANDAS_AVAILABLE:
        raise ImportError("pandas required. Run: pip install pandas")

    try:
        ctx = _get_quote_context()
        code = f"US.{ticker.upper()}"

        # Subscribe to K_DAY stream
        _ensure_subscribed(code, [ft.SubType.K_DAY])

        # Fetch bars using get_cur_kline (takes count as integer)
        ret, data = ctx.get_cur_kline(code, limit)

        if ret != RET_OK:
            warnings.warn(f"moomoo bars fetch failed for {ticker}: ret={ret}")
            return pd.DataFrame()

        if data.empty:
            warnings.warn(f"No bar data returned for {ticker}")
            return pd.DataFrame()

        # Normalize column names (moomoo returns: time_key, open, high, low, close, volume, ...)
        data = data.copy()
        if "time_key" in data.columns:
            data.rename(columns={"time_key": "timestamp"}, inplace=True)
        data["timestamp"] = pd.to_datetime(data["timestamp"])
        data = data[["timestamp", "open", "high", "low", "close", "volume"]]

        # Filter by date range if provided
        if start_date:
            start = pd.to_datetime(start_date)
            data = data[data["timestamp"] >= start]
        if end_date:
            end = pd.to_datetime(end_date)
            data = data[data["timestamp"] <= end]

        return data.reset_index(drop=True)

    except Exception as e:
        warnings.warn(f"Error fetching bars for {ticker}: {e}")
        return pd.DataFrame()


# ── Real-time Quotes ───────────────────────────────────────────────────────

def fetch_quote(ticker: str) -> Dict[str, float]:
    """Fetch current quote for a ticker.

    Returns dict with keys:
        last_price, bid, ask, bid_size, ask_size, volume, day_open, day_high, day_low
    """
    try:
        ctx = _get_quote_context()
        code = f"US.{ticker.upper()}"

        # Subscribe to QUOTE stream
        _ensure_subscribed(code, [ft.SubType.QUOTE])

        # Use get_market_snapshot (has bid/ask data)
        ret, data = ctx.get_market_snapshot([code])
        if ret != RET_OK or data is None or (hasattr(data, 'empty') and data.empty):
            return {}

        quote = data.iloc[0]
        return {
            "last_price": float(quote.get("last_price", 0)),
            "bid": float(quote.get("bid_price", 0)),
            "ask": float(quote.get("ask_price", 0)),
            "bid_size": float(quote.get("bid_vol", 0)),
            "ask_size": float(quote.get("ask_vol", 0)),
            "volume": float(quote.get("volume", 0)),
            "day_open": float(quote.get("open_price", 0)),
            "day_high": float(quote.get("high_price", 0)),
            "day_low": float(quote.get("low_price", 0)),
        }

    except Exception as e:
        warnings.warn(f"Error fetching quote for {ticker}: {e}")
        return {}


# ── Technical Indicators ───────────────────────────────────────────────────

def compute_technicals(df: pd.DataFrame) -> pd.DataFrame:
    """Add technical indicators to price bars DataFrame.

    Adds columns: ema_20, ema_50, ema_200, rsi_14, atr_14, vwap, realized_vol
    """
    if df.empty:
        return df

    df = df.copy()

    # EMA (using ta-lib if available, else simple pandas)
    if TA_AVAILABLE:
        df["ema_20"] = talib.EMA(df["close"], timeperiod=20)
        df["ema_50"] = talib.EMA(df["close"], timeperiod=50)
        df["ema_200"] = talib.EMA(df["close"], timeperiod=200)
        df["rsi_14"] = talib.RSI(df["close"], timeperiod=14)
        df["atr_14"] = talib.ATR(df["high"], df["low"], df["close"], timeperiod=14)
    else:
        df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
        df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()
        df["rsi_14"] = _calculate_rsi(df["close"], 14)
        df["atr_14"] = _calculate_atr(df["high"], df["low"], df["close"], 14)

    # VWAP (Volume-Weighted Average Price)
    df["vwap"] = _calculate_vwap(df["high"], df["low"], df["close"], df["volume"])

    # Realized volatility (20-day rolling standard deviation of returns)
    log_returns = np.log(df["close"] / df["close"].shift(1))
    df["realized_vol"] = log_returns.rolling(window=20).std() * np.sqrt(252)  # Annualized

    return df


def _calculate_rsi(prices, period=14):
    """Calculate RSI indicator."""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def _calculate_atr(high, low, close, period=14):
    """Calculate Average True Range."""
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr


def _calculate_vwap(high, low, close, volume):
    """Calculate Volume-Weighted Average Price."""
    tp = (high + low + close) / 3
    vwap = (tp * volume).rolling(window=20).sum() / volume.rolling(window=20).sum()
    return vwap


# ── Options Chains ─────────────────────────────────────────────────────────

def fetch_options_chain(
    ticker: str,
    expiration: str,
    strike_range: Optional[tuple[float, float]] = None,
) -> Dict[str, Any]:
    """Fetch options chain from native moomoo API (falls back to yfinance).

    Uses moomoo's get_option_chain() for full Greeks data.
    Falls back to yfinance only if moomoo is unavailable.

    Args:
        ticker: Stock ticker (e.g., 'AAPL')
        expiration: ISO date (e.g., '2026-06-20')
        strike_range: (min_strike, max_strike) to filter

    Returns:
        {
            'calls': [{'strike': ..., 'bid': ..., 'ask': ..., 'iv': ..., 'delta': ..., ...}],
            'puts': [same structure],
            'atm_strike': float,
        }
    """
    try:
        # Try moomoo first
        if MOOMOO_AVAILABLE:
            ctx = _get_quote_context()
            code = f"US.{ticker.upper()}"

            # Subscribe to options data
            _ensure_subscribed(code, [ft.SubType.QUOTE])

            # Fetch options chain with correct parameters
            ret, data = ctx.get_option_chain(
                code,
                index_option_type=ft.IndexOptionType.NORMAL,
                start=expiration,
                end=expiration,
                option_type=ft.OptionType.ALL
            )

            if ret == RET_OK and data is not None and not (hasattr(data, 'empty') and data.empty):
                # Get current price from moomoo
                quote = fetch_quote(ticker)
                current_price = quote.get("last_price", 0)

                calls = []
                puts = []

                for _, row in data.iterrows():
                    strike = float(row.get("strike_price", 0))
                    if strike_range:
                        min_s, max_s = strike_range
                        if strike < min_s or strike > max_s:
                            continue

                    # Normalize IV (handle both decimal and percentage formats)
                    iv = float(row.get("implied_volatility", 0))
                    if iv > 1.0:  # Percentage format
                        iv = iv / 100.0

                    contract = {
                        "strike": strike,
                        "bid": float(row.get("bid_price", 0)),
                        "ask": float(row.get("ask_price", 0)),
                        "bid_size": float(row.get("bid_vol", 0)),
                        "ask_size": float(row.get("ask_vol", 0)),
                        "volume": float(row.get("vol", 0)),
                        "open_interest": float(row.get("net_open_interest", 0)),
                        "iv": iv,
                        "delta": float(row.get("delta", 0)),
                        "gamma": float(row.get("gamma", 0)),
                        "theta": float(row.get("theta", 0)),
                        "vega": float(row.get("vega", 0)),
                    }

                    option_type = str(row.get("option_type", "")).upper()
                    if option_type == "CALL":
                        calls.append(contract)
                    elif option_type == "PUT":
                        puts.append(contract)

                if calls or puts:
                    # Check if we have actual data (not all zeros)
                    # If moomoo returned structure but no prices, fall through to yfinance
                    has_real_data = any(
                        c.get("bid", 0) > 0 or c.get("iv", 0) > 0 or c.get("delta", 0) != 0
                        for c in calls
                    ) or any(
                        p.get("bid", 0) > 0 or p.get("iv", 0) > 0 or p.get("delta", 0) != 0
                        for p in puts
                    )

                    if has_real_data:
                        return {
                            "calls": sorted(calls, key=lambda x: x["strike"]),
                            "puts": sorted(puts, key=lambda x: x["strike"]),
                            "atm_strike": current_price,
                        }

        # Fallback to yfinance if moomoo unavailable or returned empty
        try:
            import yfinance as yf
        except ImportError:
            warnings.warn("yfinance not installed. Cannot fetch options chain.")
            return {"calls": [], "puts": [], "atm_strike": 0}

        ticker_obj = yf.Ticker(ticker)
        try:
            opts = ticker_obj.option_chain(expiration)
        except Exception as e:
            warnings.warn(f"yfinance options fetch failed for {ticker}: {e}")
            return {"calls": [], "puts": [], "atm_strike": 0}

        calls_df = opts.calls
        puts_df = opts.puts

        # Get current price from moomoo if possible
        quote = fetch_quote(ticker)
        current_price = quote.get("last_price", 0)

        calls = []
        puts = []

        for _, row in calls_df.iterrows():
            strike = float(row["strike"])
            if strike_range:
                min_s, max_s = strike_range
                if strike < min_s or strike > max_s:
                    continue

            # Normalize IV
            iv = float(row.get("impliedVolatility", 0))
            if iv > 1.0:
                iv = iv / 100.0

            contract = {
                "strike": strike,
                "bid": float(row.get("bid", 0)),
                "ask": float(row.get("ask", 0)),
                "bid_size": float(row.get("bid_size", 0)),
                "ask_size": float(row.get("ask_size", 0)),
                "volume": float(row.get("volume", 0)),
                "open_interest": float(row.get("openInterest", 0)),
                "iv": iv,
                "delta": float(row.get("delta", 0)),
                "gamma": float(row.get("gamma", 0)),
                "theta": float(row.get("theta", 0)),
                "vega": float(row.get("vega", 0)),
            }
            calls.append(contract)

        for _, row in puts_df.iterrows():
            strike = float(row["strike"])
            if strike_range:
                min_s, max_s = strike_range
                if strike < min_s or strike > max_s:
                    continue

            # Normalize IV
            iv = float(row.get("impliedVolatility", 0))
            if iv > 1.0:
                iv = iv / 100.0

            contract = {
                "strike": strike,
                "bid": float(row.get("bid", 0)),
                "ask": float(row.get("ask", 0)),
                "bid_size": float(row.get("bid_size", 0)),
                "ask_size": float(row.get("ask_size", 0)),
                "volume": float(row.get("volume", 0)),
                "open_interest": float(row.get("openInterest", 0)),
                "iv": iv,
                "delta": float(row.get("delta", 0)),
                "gamma": float(row.get("gamma", 0)),
                "theta": float(row.get("theta", 0)),
                "vega": float(row.get("vega", 0)),
            }
            puts.append(contract)

        return {
            "calls": sorted(calls, key=lambda x: x["strike"]),
            "puts": sorted(puts, key=lambda x: x["strike"]),
            "atm_strike": current_price,
        }

    except Exception as e:
        warnings.warn(f"Error fetching options chain for {ticker}: {e}")
        return {"calls": [], "puts": [], "atm_strike": 0}


# ── IV Rank / Percentile ───────────────────────────────────────────────────

def calculate_iv_rank(ticker: str, days_back: int = 252) -> float:
    """Calculate IV rank (percentile of current IV vs 1-year history).

    IV Rank = (Current IV - Min IV) / (Max IV - Min IV) * 100
    """
    try:
        # Fetch daily bars (1 year)
        end_date = datetime.now().isoformat()
        start_date = (datetime.now() - timedelta(days=days_back)).isoformat()

        df = fetch_bars(ticker, start_date=start_date, end_date=end_date, period="1d", limit=252)

        if df.empty or len(df) < 20:
            return 50.0  # Default neutral if insufficient data

        df = compute_technicals(df)

        # For now, use realized volatility as IV proxy
        # In production, would fetch actual option IV from chain
        realized_vols = df["realized_vol"].dropna()

        if len(realized_vols) < 2:
            return 50.0

        current_vol = realized_vols.iloc[-1]
        min_vol = realized_vols.min()
        max_vol = realized_vols.max()

        if max_vol == min_vol:
            return 50.0

        iv_rank = ((current_vol - min_vol) / (max_vol - min_vol)) * 100
        return max(0, min(100, iv_rank))  # Clamp to 0-100

    except Exception as e:
        warnings.warn(f"Error calculating IV rank for {ticker}: {e}")
        return 50.0


# ── Unified Data Package ───────────────────────────────────────────────────

def fetch_market_snapshot(ticker: str) -> Dict[str, Any]:
    """Fetch everything: quote, bars, technicals, options chain, IV rank.

    Single call to get all data needed for trade decision.
    """
    try:
        # Price & quote
        quote = fetch_quote(ticker)
        bars = fetch_bars(ticker, limit=252)
        technicals = compute_technicals(bars) if not bars.empty else bars

        # Get latest candle data
        latest = technicals.iloc[-1].to_dict() if not technicals.empty else {}

        # Options chain (front month)
        today = datetime.now()
        # Find next Friday (standard options expiration)
        days_ahead = 4 - today.weekday()  # Friday is 4
        if days_ahead <= 0:
            days_ahead += 7
        next_friday = today + timedelta(days=days_ahead)
        exp_date = next_friday.strftime("%Y-%m-%d")

        chain = fetch_options_chain(ticker, exp_date)

        # IV rank (with validation to catch 0-1 scale errors)
        iv_rank = calculate_iv_rank(ticker)
        iv_rank = validate_and_normalize_iv_rank(iv_rank, context_source="moomoo", ticker=ticker)

        return {
            "ticker": ticker,
            "quote": quote,
            "latest_candle": latest,
            "bars": technicals,
            "options_chain": chain,
            "iv_rank": iv_rank,
            "timestamp": datetime.now().isoformat(),
        }

    except Exception as e:
        warnings.warn(f"Error fetching market snapshot for {ticker}: {e}")
        return {"ticker": ticker, "error": str(e)}
