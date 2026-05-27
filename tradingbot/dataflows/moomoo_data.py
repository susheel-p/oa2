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

from tradingbot.dataflows.iv_rank_validator import validate_and_normalize_iv_rank

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


def _compute_expiry_dates(as_of: datetime | None = None) -> list[str]:
    """Compute a list of 5 option expiration dates for flow analysis.

    Returns expirations representing: front-week, near-term, mid-term, longer.
    Dates are ISO format strings (YYYY-MM-DD).

    Args:
        as_of: Compute from this date. Defaults to today.

    Returns:
        List of 5 ISO date strings, sorted ascending.
    """
    base = as_of or datetime.now()
    expirations = []

    # 1. Next Friday (front-week / weekly)
    days_ahead = 4 - base.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    next_fri = base + timedelta(days=days_ahead)
    expirations.append(next_fri.strftime("%Y-%m-%d"))

    # 2. Friday after next (weekly)
    two_fri = next_fri + timedelta(days=7)
    expirations.append(two_fri.strftime("%Y-%m-%d"))

    # 3. Third Friday (monthly / near-term monthly)
    three_fri = next_fri + timedelta(days=14)
    expirations.append(three_fri.strftime("%Y-%m-%d"))

    # 4. 6-week (mid-term) — snap to next Friday so yfinance can find the expiry
    six_week = base + timedelta(days=42)
    six_week_fri_offset = (4 - six_week.weekday()) % 7
    expirations.append((six_week + timedelta(days=six_week_fri_offset)).strftime("%Y-%m-%d"))

    # 5. 10-week (longer-dated) — snap to next Friday so yfinance can find the expiry
    ten_week = base + timedelta(days=70)
    ten_week_fri_offset = (4 - ten_week.weekday()) % 7
    expirations.append((ten_week + timedelta(days=ten_week_fri_offset)).strftime("%Y-%m-%d"))

    return sorted(expirations)


def _recommend_expiry(chains_by_expiry: Dict[str, Dict[str, Any]]) -> str | None:
    """Recommend which expiration to use based on options flow analysis.

    Uses classify_expiry_flow() to find the dominant DTE bucket and maps it
    to the appropriate expiration date.

    Strategy:
      - front_week dominant → use nearest expiry
      - near_term dominant → use 2nd nearest expiry
      - mid_term dominant → use 3rd nearest expiry
      - longer dominant → use furthest expiry

    Args:
        chains_by_expiry: {expiry_str: {"calls": [...], "puts": [...]}}

    Returns:
        ISO date string of recommended expiration, or None if empty.
    """
    from tradingbot.dataflows.expiry_flow import classify_expiry_flow, ExpiryBucket

    # Never enter an option expiring in the current calendar week (Mon–Sun).
    # Buying same-week expiry means holding into expiration with no room to exit.
    today = datetime.now().date()
    days_until_sunday = (6 - today.weekday()) % 7
    end_of_current_week = today + timedelta(days=days_until_sunday if days_until_sunday > 0 else 7)

    if not chains_by_expiry:
        # Fallback: first Friday strictly after end_of_current_week
        days_ahead = 4 - today.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        candidate = today + timedelta(days=days_ahead)
        if candidate <= end_of_current_week:
            candidate += timedelta(days=7)
        return candidate.strftime("%Y-%m-%d")

    # Analyze flow across all expirations
    flow_profile = classify_expiry_flow(chains_by_expiry)

    # Filter out same-week and already-expired dates before bucket selection.
    candidate_expiries = [
        e for e in sorted(chains_by_expiry.keys())
        if datetime.strptime(e, "%Y-%m-%d").date() > end_of_current_week
    ]

    if not candidate_expiries:
        return None

    # Map dominant bucket to expiration index
    dominant = flow_profile.dominant_bucket

    if dominant == ExpiryBucket.FRONT_WEEK.value:
        return candidate_expiries[0]
    elif dominant == ExpiryBucket.NEAR_TERM.value:
        return candidate_expiries[1] if len(candidate_expiries) >= 2 else candidate_expiries[0]
    elif dominant == ExpiryBucket.MID_TERM.value:
        return candidate_expiries[2] if len(candidate_expiries) >= 3 else candidate_expiries[-1]
    elif dominant == ExpiryBucket.LONGER.value:
        return candidate_expiries[-1]

    return candidate_expiries[0]


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
            host = os.getenv("MOOMOO_OPEND_HOST", "127.0.0.1")
            port = int(os.getenv("MOOMOO_OPEND_PORT", 11111))
            _quote_ctx = ft.OpenQuoteContext(host=host, port=port)
        except Exception as e:
            host = os.getenv("MOOMOO_OPEND_HOST", "127.0.0.1")
            port = os.getenv("MOOMOO_OPEND_PORT", 11111)
            raise Exception(f"Failed to connect to OpenD server at {host}:{port}. Ensure moomoo app is running and OpenD is enabled. Error: {e}")
        import atexit
        atexit.register(close_quote_context)
    return _quote_ctx


def close_quote_context() -> None:
    """Close the moomoo quote context so background threads exit on process shutdown."""
    global _quote_ctx, _subscribed_codes
    if _quote_ctx is not None:
        try:
            _quote_ctx.close()
        except Exception:
            pass
        _quote_ctx = None
        _subscribed_codes = set()


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

            logger.debug("[chain:%s] get_option_chain ret=%s expiry=%s", ticker, ret, expiration)

            if ret != RET_OK:
                logger.warning("[chain:%s] get_option_chain FAILED ret=%s expiry=%s", ticker, ret, expiration)
            elif data is None or (hasattr(data, 'empty') and data.empty):
                logger.warning("[chain:%s] get_option_chain returned EMPTY dataframe expiry=%s", ticker, expiration)
            else:
                # get_option_chain returns metadata only (code, strike_price, option_type).
                # Fetch live bid/ask/Greeks via get_market_snapshot on the option codes.
                quote = fetch_quote(ticker)
                current_price = quote.get("last_price", 0)

                # Build strike/type lookup from chain metadata
                meta = {}
                for _, row in data.iterrows():
                    c = str(row.get("code", ""))
                    if c:
                        meta[c] = {
                            "strike": float(row.get("strike_price", 0)),
                            "option_type": str(row.get("option_type", "")).upper(),
                        }

                logger.debug("[chain:%s] chain metadata: %d codes (cols=%s)", ticker, len(meta), list(data.columns))

                if not meta:
                    logger.warning("[chain:%s] no option codes extracted from chain rows; raw sample: %s",
                                   ticker, data.head(2).to_dict("records") if not data.empty else "empty")
                else:
                    # Fetch snapshots in batches of 100 (moomoo per-call limit)
                    all_codes = list(meta.keys())
                    snap_rows: dict[str, Any] = {}
                    batch_size = 100
                    for i in range(0, len(all_codes), batch_size):
                        batch = all_codes[i : i + batch_size]
                        s_ret, s_data = ctx.get_market_snapshot(batch)
                        logger.debug("[chain:%s] snapshot batch %d-%d ret=%s rows=%s",
                                     ticker, i, i + len(batch), s_ret,
                                     len(s_data) if s_data is not None and not s_data.empty else 0)
                        if s_ret == RET_OK and s_data is not None and not s_data.empty:
                            for _, srow in s_data.iterrows():
                                snap_rows[str(srow.get("code", ""))] = srow.to_dict()
                        else:
                            logger.warning("[chain:%s] snapshot batch %d-%d FAILED ret=%s sample_codes=%s",
                                           ticker, i, i + len(batch), s_ret, batch[:3])

                    logger.debug("[chain:%s] snapshots returned: %d / %d codes populated",
                                 ticker, len(snap_rows), len(all_codes))

                    calls = []
                    puts = []
                    skipped_no_snap = 0

                    for c, m in meta.items():
                        strike = m["strike"]
                        if strike_range:
                            min_s, max_s = strike_range
                            if strike < min_s or strike > max_s:
                                continue

                        snap = snap_rows.get(c, {})
                        if not snap:
                            skipped_no_snap += 1

                        def _f(val, default=0.0):
                            try:
                                return float(val) if val not in (None, "", "N/A") else default
                            except (TypeError, ValueError):
                                return default

                        iv = _f(snap.get("option_implied_volatility"))
                        if iv > 1.0:
                            iv = iv / 100.0

                        contract = {
                            "strike": strike,
                            "bid": _f(snap.get("bid_price")),
                            "ask": _f(snap.get("ask_price")),
                            "bid_size": _f(snap.get("bid_vol")),
                            "ask_size": _f(snap.get("ask_vol")),
                            "volume": _f(snap.get("volume")),
                            "open_interest": _f(snap.get("option_open_interest")) or _f(snap.get("option_net_open_interest")),
                            "iv": iv,
                            "delta": _f(snap.get("option_delta")),
                            "gamma": _f(snap.get("option_gamma")),
                            "theta": _f(snap.get("option_theta")),
                            "vega": _f(snap.get("option_vega")),
                        }

                        if m["option_type"] == "CALL":
                            calls.append(contract)
                        elif m["option_type"] == "PUT":
                            puts.append(contract)

                    logger.debug("[chain:%s] contracts built: calls=%d puts=%d skipped_no_snap=%d",
                                 ticker, len(calls), len(puts), skipped_no_snap)

                    if not calls and not puts:
                        logger.warning("[chain:%s] zero contracts after building; meta=%d snap_rows=%d strike_range=%s",
                                       ticker, len(meta), len(snap_rows), strike_range)
                    else:
                        has_real_data = any(
                            c.get("open_interest", 0) > 0 or c.get("bid", 0) > 0
                            or c.get("iv", 0) > 0 or c.get("delta", 0) != 0
                            for c in calls
                        ) or any(
                            p.get("open_interest", 0) > 0 or p.get("bid", 0) > 0
                            or p.get("iv", 0) > 0 or p.get("delta", 0) != 0
                            for p in puts
                        )

                        if not has_real_data:
                            sample = (calls[:2] if calls else []) + (puts[:2] if puts else [])
                            logger.warning("[chain:%s] has_real_data=False — all greeks/OI/bid are zero. sample=%s",
                                           ticker, sample)
                        else:
                            return {
                                "calls": sorted(calls, key=lambda x: x["strike"]),
                                "puts": sorted(puts, key=lambda x: x["strike"]),
                                "atm_strike": current_price,
                            }

        # No yfinance fallback — moomoo is our data source
        if MOOMOO_AVAILABLE:
            logger.error("[chain:%s] moomoo returned no usable chain for expiry=%s — returning empty. "
                         "Check DEBUG logs above for the exact failure point.", ticker, expiration)
            return {"calls": [], "puts": [], "atm_strike": 0}

        logger.error("[chain:%s] moomoo SDK not available — cannot fetch options chain", ticker)
        return {"calls": [], "puts": [], "atm_strike": 0}

    except Exception as e:
        logger.exception("[chain:%s] unexpected error fetching options chain expiry=%s: %s", ticker, expiration, e)
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

# ── Options Flow Analysis (new) ───────────────────────────────────────────

def analyze_options_flow(chains_by_expiry_or_single: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze options volume and Greeks for flow signals.

    Accepts both single chain dict and multi-expiry dict for backwards compatibility.

    Args:
        chains_by_expiry_or_single:
            - If has keys 'calls'/'puts': single chain (backwards compat)
            - If has date keys (e.g., '2026-06-20'): multi-expiry dict

    Returns:
        {
            'put_call_ratio': float,           # Put vol / Call vol
            'call_vol_concentration': float,   # % of call vol in top 3 strikes
            'put_vol_concentration': float,    # % of put vol in top 3 strikes
            'aggressive_call_buying': int,     # Calls where ask_size << volume
            'aggressive_put_buying': int,      # Puts where ask_size << volume
            'call_gamma_concentration': float, # % of gamma in top 3 calls
            'put_gamma_concentration': float,  # % of gamma in top 3 puts
            'data_quality': 'real' | 'partial' | 'absent'
        }
    """
    input_dict = chains_by_expiry_or_single
    all_calls = []
    all_puts = []

    # Detect if single chain or multi-expiry
    if 'calls' in input_dict or 'puts' in input_dict:
        # Single chain (backwards compatible path)
        all_calls = input_dict.get('calls', [])
        all_puts = input_dict.get('puts', [])
    else:
        # Multi-expiry: aggregate across all expirations
        for expiry_str, chain in input_dict.items():
            if isinstance(chain, dict):
                all_calls.extend(chain.get('calls', []))
                all_puts.extend(chain.get('puts', []))

    if not all_calls or not all_puts:
        return {'data_quality': 'absent'}

    # PCR from volume
    total_call_vol = sum(c.get('volume', 0) for c in all_calls)
    total_put_vol = sum(p.get('volume', 0) for p in all_puts)

    if total_call_vol == 0 and total_put_vol == 0:
        return {'data_quality': 'absent'}

    pcr = total_put_vol / total_call_vol if total_call_vol > 0 else 0.0

    # Volume concentration (top 3 strikes)
    top_calls = sorted(all_calls, key=lambda x: x.get('volume', 0), reverse=True)[:3]
    top_puts = sorted(all_puts, key=lambda x: x.get('volume', 0), reverse=True)[:3]

    call_top_vol = sum(c.get('volume', 0) for c in top_calls)
    put_top_vol = sum(p.get('volume', 0) for p in top_puts)

    call_concentration = (call_top_vol / total_call_vol * 100) if total_call_vol > 0 else 0
    put_concentration = (put_top_vol / total_put_vol * 100) if total_put_vol > 0 else 0

    # Aggressive buys (volume >> ask_size, typical of sweeps)
    agg_calls = sum(1 for c in all_calls
                    if c.get('volume', 0) > 5 and c.get('ask_size', 0) < c.get('volume', 0) / 3)
    agg_puts = sum(1 for p in all_puts
                   if p.get('volume', 0) > 5 and p.get('ask_size', 0) < p.get('volume', 0) / 3)

    # Gamma concentration (top 3)
    call_gammas = [abs(c.get('gamma', 0)) for c in all_calls]
    put_gammas = [abs(p.get('gamma', 0)) for p in all_puts]

    total_call_gamma = sum(call_gammas)
    total_put_gamma = sum(put_gammas)

    top_call_gamma = sum(sorted(call_gammas, reverse=True)[:3])
    top_put_gamma = sum(sorted(put_gammas, reverse=True)[:3])

    call_gamma_conc = (top_call_gamma / total_call_gamma * 100) if total_call_gamma > 0 else 0
    put_gamma_conc = (top_put_gamma / total_put_gamma * 100) if total_put_gamma > 0 else 0

    return {
        'put_call_ratio': pcr,
        'call_vol_concentration': call_concentration,
        'put_vol_concentration': put_concentration,
        'aggressive_call_buying': agg_calls,
        'aggressive_put_buying': agg_puts,
        'call_gamma_concentration': call_gamma_conc,
        'put_gamma_concentration': put_gamma_conc,
        'data_quality': 'real' if total_call_vol > 0 and total_put_vol > 0 else 'partial'
    }


def analyze_market_depth(ticker: str) -> Dict[str, Any]:
    """Analyze order flow imbalance from bid/ask levels.

    Returns:
        {
            'bid_ask_imbalance': float,      # (bid_size - ask_size) / (bid_size + ask_size)
            'spread_pct': float,              # (ask - bid) / mid
            'order_flow_signal': 'bullish' | 'bearish' | 'neutral',
            'data_quality': 'real' | 'absent'
        }
    """
    quote = fetch_quote(ticker)

    if not quote or quote.get('bid', 0) == 0 or quote.get('ask', 0) == 0:
        return {'data_quality': 'absent'}

    bid_size = quote.get('bid_size', 0)
    ask_size = quote.get('ask_size', 0)
    bid = quote.get('bid', 0)
    ask = quote.get('ask', 0)

    if bid_size + ask_size == 0:
        return {'data_quality': 'absent'}

    # Order flow imbalance: positive = more buy pressure
    imbalance = (bid_size - ask_size) / (bid_size + ask_size)

    # Spread as % of mid
    mid = (bid + ask) / 2
    spread_pct = (ask - bid) / mid if mid > 0 else 0

    # Imbalance interpretation
    if imbalance > 0.15:
        signal = 'bullish'
    elif imbalance < -0.15:
        signal = 'bearish'
    else:
        signal = 'neutral'

    return {
        'bid_ask_imbalance': imbalance,
        'spread_pct': spread_pct,
        'order_flow_signal': signal,
        'data_quality': 'real'
    }


def fetch_market_snapshot(ticker: str) -> Dict[str, Any]:
    """Fetch everything: quote, bars, technicals, options chain, IV rank, flow analysis.

    Single call to get all data needed for trade decision.
    """
    try:
        # Price & quote
        quote = fetch_quote(ticker)
        bars = fetch_bars(ticker, limit=252)
        technicals = compute_technicals(bars) if not bars.empty else bars

        # Get latest candle data
        latest = technicals.iloc[-1].to_dict() if not technicals.empty else {}

        # Fetch multiple option expirations for flow analysis
        expiries = _compute_expiry_dates()
        chains_by_expiry = {}
        for exp in expiries:
            try:
                chain = fetch_options_chain(ticker, exp)
                if chain.get("calls") or chain.get("puts"):
                    chains_by_expiry[exp] = chain
            except Exception as e:
                warnings.warn(f"Failed to fetch {ticker} chain for {exp}: {e}")

        # For backwards compatibility: use first available chain as default
        default_chain = None
        if chains_by_expiry:
            first_exp = min(chains_by_expiry.keys())
            default_chain = chains_by_expiry[first_exp]
            default_chain["expiry"] = first_exp

        # IV rank (with validation to catch 0-1 scale errors)
        iv_rank = calculate_iv_rank(ticker)
        iv_rank = validate_and_normalize_iv_rank(iv_rank, context_source="moomoo", ticker=ticker)

        # Flow analysis across all expirations
        flow_analysis = analyze_options_flow(chains_by_expiry) if chains_by_expiry else {"data_quality": "absent"}
        depth_analysis = analyze_market_depth(ticker)

        # Recommend best expiration based on flow
        recommended_expiry = _recommend_expiry(chains_by_expiry) if chains_by_expiry else None

        return {
            "ticker": ticker,
            "quote": quote,
            "latest_candle": latest,
            "bars": technicals,
            "chains_by_expiry": chains_by_expiry,
            "recommended_expiry": recommended_expiry,
            "options_chain": default_chain,
            "iv_rank": iv_rank,
            "flow_analysis": flow_analysis,
            "depth_analysis": depth_analysis,
            "timestamp": datetime.now().isoformat(),
        }

    except Exception as e:
        warnings.warn(f"Error fetching market snapshot for {ticker}: {e}")
        return {"ticker": ticker, "error": str(e)}


def fetch_account_positions(trd_env_str: str | None = None, acc_id: int = 0) -> list[dict]:
    """Fetch live open positions from moomoo account via position_list_query().

    Opens its own OpenSecTradeContext, calls position_list_query(), closes immediately.
    Never raises; returns empty list on any failure.

    Args:
        trd_env_str: "SIMULATE" (default) or "REAL" — reads OA2_TRADE_ENV if None
        acc_id: moomoo account ID — reads MOOMOO_ACCOUNT_ID from env if 0

    Returns:
        List of position dicts with: code, stock_name, qty, cost_price, market_val,
        pl_val, pl_ratio, today_pl_val, currency, position_side
    """
    if not MOOMOO_AVAILABLE:
        logger.warning("moomoo SDK not installed; fetch_account_positions returning []")
        return []

    if trd_env_str is None:
        trd_env_str = os.getenv("OA2_TRADE_ENV", "SIMULATE")
    if acc_id == 0:
        try:
            acc_id = int(os.getenv("MOOMOO_ACCOUNT_ID", "0"))
        except (ValueError, TypeError):
            acc_id = 0

    try:
        from moomoo import OpenSecTradeContext, TrdMarket, SecurityFirm
    except ImportError:
        logger.warning("OpenSecTradeContext import failed; fetch_account_positions returning []")
        return []

    trd_env = ft.TrdEnv.SIMULATE if trd_env_str == "SIMULATE" else ft.TrdEnv.REAL
    ctx = None
    try:
        ctx = OpenSecTradeContext(
            filter_trdmarket=TrdMarket.US,
            host=os.getenv("MOOMOO_OPEND_HOST", "127.0.0.1"),
            port=int(os.getenv("MOOMOO_OPEND_PORT", "11111")),
            security_firm=SecurityFirm.FUTUINC,
        )
        ret, df = ctx.position_list_query(
            trd_env=trd_env,
            acc_id=acc_id,
        )
        if ret != 0 or df is None or (hasattr(df, "empty") and df.empty):
            logger.warning(f"position_list_query returned ret={ret}; returning []")
            return []

        positions = []
        for _, row in df.iterrows():
            positions.append({
                "code": str(row.get("code", "")),
                "stock_name": str(row.get("stock_name", "")),
                "qty": int(row.get("qty", 0) or 0),
                "can_sell_qty": int(row.get("can_sell_qty", 0) or 0),
                "cost_price": float(row.get("cost_price", 0) or 0.0),
                "market_val": float(row.get("market_val", 0) or 0.0),
                "pl_val": float(row.get("pl_val", 0) or 0.0),
                "pl_ratio": float(row.get("pl_ratio", 0) or 0.0),
                "today_pl_val": float(row.get("today_pl_val", 0) or 0.0),
                "currency": str(row.get("currency", "USD")),
                "position_side": str(row.get("position_side", "")),
            })
        return positions

    except Exception as exc:
        logger.warning(f"fetch_account_positions failed: {exc}")
        return []
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
