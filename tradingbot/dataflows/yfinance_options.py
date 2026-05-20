"""yfinance market data fetcher — price, vol, IV, skew, term structure."""

from __future__ import annotations

import datetime
import warnings
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
from tradingbot.dataflows.iv_rank_validator import validate_and_normalize_iv_rank


def fetch_market_context(
    ticker: str,
    analysis_date: Optional[str] = None,
) -> dict:
    """Return a dict that can be unpacked directly into OptionsState.

    Fetches:
    - OHLCV + VWAP proxy + EMAs from daily history
    - VIX and VVIX
    - Front/back-month ATM IV from the live options chain
    - Put/call skew (5% OTM)
    - Realized vol (30-day HV annualized)
    - IV rank vs 52-week HV range (yfinance has no historical IV, so HV is the proxy)
    """
    t = yf.Ticker(ticker)

    # ── 1. Price history (1 year + a bit for EMA warmup) ──────────────────────
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        hist = t.history(period="400d", auto_adjust=True)

    if hist.empty:
        raise ValueError(f"yfinance returned no price history for {ticker}")

    # Use the last row as "today" (or look up analysis_date row if provided)
    if analysis_date:
        target = pd.Timestamp(analysis_date).tz_localize(None)
        # find the last row on or before target
        hist.index = hist.index.tz_localize(None) if hist.index.tz else hist.index
        hist = hist[hist.index <= target]
        if hist.empty:
            raise ValueError(f"No data for {ticker} on or before {analysis_date}")

    today_row = hist.iloc[-1]
    prev_row = hist.iloc[-2] if len(hist) >= 2 else today_row

    current_price = float(today_row["Close"])
    open_price = float(today_row["Open"])
    high = float(today_row["High"])
    low = float(today_row["Low"])
    volume = float(today_row["Volume"])
    prev_close = float(prev_row["Close"])

    # VWAP proxy: (H+L+C)/3 (true VWAP requires intraday ticks)
    vwap = (high + low + current_price) / 3.0

    # ── 2. EMAs (daily 8/21/50/200) ───────────────────────────────────────────
    closes = hist["Close"]
    ema_8   = float(closes.ewm(span=8,   adjust=False).mean().iloc[-1])
    ema_20  = float(closes.ewm(span=20,  adjust=False).mean().iloc[-1])
    ema_21  = float(closes.ewm(span=21,  adjust=False).mean().iloc[-1])
    ema_50  = float(closes.ewm(span=50,  adjust=False).mean().iloc[-1])
    ema_200 = float(closes.ewm(span=200, adjust=False).mean().iloc[-1]) if len(closes) >= 200 else ema_50
    avg_volume = float(hist["Volume"].rolling(20).mean().iloc[-1])

    # Daily ATR(14) — used to normalize EMA distances
    high_low   = hist["High"] - hist["Low"]
    high_close = (hist["High"] - closes.shift()).abs()
    low_close  = (hist["Low"]  - closes.shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr_14 = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else float(high_low.mean())

    # ── 2b. Intraday EMAs (15m: 8/21/50) for S/R map ─────────────────────────
    ema15_8, ema15_21, ema15_50 = ema_8, ema_21, ema_50  # safe fallbacks
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            intraday = t.history(period="5d", interval="15m", auto_adjust=True)
        if not intraday.empty and len(intraday) >= 8:
            c15 = intraday["Close"]
            ema15_8  = float(c15.ewm(span=8,  adjust=False).mean().iloc[-1])
            ema15_21 = float(c15.ewm(span=21, adjust=False).mean().iloc[-1])
            ema15_50 = float(c15.ewm(span=50, adjust=False).mean().iloc[-1]) if len(c15) >= 50 else ema15_21
    except Exception:
        pass

    # ── 3. Realized vol (30-day HV, annualized) ───────────────────────────────
    log_returns = np.log(closes / closes.shift(1)).dropna()
    rv_30 = float(log_returns.iloc[-30:].std() * np.sqrt(252)) if len(log_returns) >= 30 else 0.20

    # ── 4. IV rank via 52-week HV range ───────────────────────────────────────
    # We use 252-day rolling 30-day HV as a proxy for historical IV levels.
    rolling_hv = log_returns.rolling(30).std() * np.sqrt(252)
    rolling_hv = rolling_hv.dropna()
    if len(rolling_hv) >= 50:
        hv_min = float(rolling_hv.min())
        hv_max = float(rolling_hv.max())
        hv_range = hv_max - hv_min
        if hv_range > 0.001:
            iv_rank = float((rv_30 - hv_min) / hv_range * 100)
        else:
            iv_rank = 50.0
        iv_rank = max(0.0, min(100.0, iv_rank))
        iv_percentile = float((rolling_hv <= rv_30).mean() * 100)
    else:
        iv_rank = 50.0
        iv_percentile = 50.0

    # ── 5. VIX and VVIX ───────────────────────────────────────────────────────
    market_vix = _fetch_index_close("^VIX", fallback=20.0)
    vvix = _fetch_index_close("^VVIX", fallback=market_vix * 5)

    # ── 6. Options chain — front + back month ATM IV, skew ────────────────────
    # For historical backtests, use ML-predicted IV; for live, fetch real chain
    is_historical_backtest = analysis_date and analysis_date < datetime.date.today().isoformat()

    if is_historical_backtest:
        # Backtest mode: use ML model to predict IV from HV
        from tradingbot.dataflows.iv_predictor import predict_iv, is_model_available

        if is_model_available():
            # Calculate HV features for model input (0-1 scale)
            hv_5d = float(log_returns.iloc[-5:].std() * np.sqrt(252)) if len(log_returns) >= 5 else rv_30
            hv_20d = float(log_returns.iloc[-20:].std() * np.sqrt(252)) if len(log_returns) >= 20 else rv_30
            hv_60d = float(log_returns.iloc[-60:].std() * np.sqrt(252)) if len(log_returns) >= 60 else rv_30

            # Predict IV from HV features
            predicted_iv = predict_iv(hv_5d, hv_20d, hv_60d)
            front_month_iv = predicted_iv * 100  # Convert to percentage scale
            back_month_iv = predicted_iv * 100 * 1.08  # Back month slightly higher
            skew_put_iv = predicted_iv * 100 * 1.10
            skew_call_iv = predicted_iv * 100 * 0.95
        else:
            # Fallback: use HV-based IV (HV * 1.15 premium)
            front_month_iv = rv_30 * 100 * 1.15
            back_month_iv = rv_30 * 100 * 1.25
            skew_put_iv = rv_30 * 100 * 1.30
            skew_call_iv = rv_30 * 100 * 1.05
    else:
        # Live mode: fetch real options chain
        front_month_iv, back_month_iv, skew_put_iv, skew_call_iv = _fetch_options_iv(
            t, current_price
        )

    term_structure = {
        "front_month_iv": front_month_iv,
        "back_month_iv": back_month_iv,
    }

    # ── 7. IV rank override: if we got real chain IV, use percentile method ───
    if front_month_iv > 0 and len(rolling_hv) >= 50:
        # Normalize front_month_iv to decimal form [0, 1] if yfinance returned percentage
        front_iv_normalized = front_month_iv / 100.0 if front_month_iv > 1.0 else front_month_iv
        # Use percentile approach (robust when IV is outside HV range): "what % of HV values <= current IV"
        iv_percentile = float((rolling_hv <= front_iv_normalized).mean() * 100)
        iv_rank = iv_percentile  # IV rank = percentile of HV distribution
        # Log when IV is unusually low (below min HV, suggesting calm market or data stale)
        if front_iv_normalized < hv_min:
            import logging
            logging.debug(
                f"[{ticker}] IV below HV min: front_iv={front_month_iv:.4f} < hv_min={hv_min:.4f} "
                f"(iv_rank={iv_rank:.0f}%) — market expecting calmer trading ahead"
            )

    # Validate IV rank before returning (catches 0-1 scale errors, negative values, etc.)
    iv_rank = validate_and_normalize_iv_rank(iv_rank, context_source="yfinance", ticker=ticker)
    iv_percentile = max(0.0, min(100.0, iv_percentile))

    return {
        "ticker": ticker,
        "analysis_date": analysis_date or datetime.date.today().isoformat(),
        "current_price": current_price,
        "open_price": open_price,
        "high": high,
        "low": low,
        "vwap": vwap,
        "prev_close": prev_close,
        "ema_8": ema_8,
        "ema_20": ema_20,
        "ema_21": ema_21,
        "ema_50": ema_50,
        "ema_200": ema_200,
        "ema15_8": ema15_8,
        "ema15_21": ema15_21,
        "ema15_50": ema15_50,
        "atr_14": atr_14,
        "volume": volume,
        "avg_volume": avg_volume,
        "gamma_flip_level": None,
        "iv_rank": iv_rank,
        "iv_percentile": iv_percentile,
        "term_structure": term_structure,
        "skew_put_iv": skew_put_iv,
        "skew_call_iv": skew_call_iv,
        "realized_vol": rv_30,
        "market_vix": market_vix,
        "vvix": vvix,
        "front_month_iv": front_month_iv,
    }


# ── Helpers ────────────────────────────────────────────────────────────────

def _fetch_index_close(symbol: str, fallback: float) -> float:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            data = yf.Ticker(symbol).history(period="5d", auto_adjust=True)
        if data.empty:
            return fallback
        return float(data["Close"].iloc[-1])
    except Exception:
        return fallback


def _fetch_options_iv(
    ticker_obj: yf.Ticker,
    spot: float,
) -> tuple[float, float, float, float]:
    """Return (front_iv, back_iv, put_skew_iv, call_skew_iv) from the chain.

    Falls back gracefully on any error — yfinance options data is unreliable
    for some tickers (ETFs, indices, non-optionable).
    """
    try:
        exps = ticker_obj.options
    except Exception:
        return _iv_fallback(spot)

    if not exps:
        return _iv_fallback(spot)

    front_iv = _atm_iv(ticker_obj, exps[0], spot)
    back_iv = _atm_iv(ticker_obj, exps[1], spot) if len(exps) > 1 else front_iv

    # Skew: 5% OTM put vs 5% OTM call from front month
    put_iv, call_iv = _skew_iv(ticker_obj, exps[0], spot)

    return front_iv, back_iv, put_iv, call_iv


def _atm_iv(ticker_obj: yf.Ticker, expiry: str, spot: float) -> float:
    """Get ATM implied vol from the nearest-to-money call in this expiry."""
    try:
        chain = ticker_obj.option_chain(expiry)
        calls = chain.calls[["strike", "impliedVolatility"]].dropna()
        calls = calls[calls["impliedVolatility"] > 0]
        if calls.empty:
            return 0.25
        idx = (calls["strike"] - spot).abs().idxmin()
        iv = float(calls.loc[idx, "impliedVolatility"])
        # Normalize: yfinance sometimes returns percentages (2.78) vs decimals (0.0278)
        if iv > 1.0:
            iv = iv / 100.0
        return iv
    except Exception:
        return 0.25


def _skew_iv(ticker_obj: yf.Ticker, expiry: str, spot: float) -> tuple[float, float]:
    """Return (put_iv_5pct_otm, call_iv_5pct_otm)."""
    try:
        chain = ticker_obj.option_chain(expiry)
        put_target = spot * 0.95
        call_target = spot * 1.05

        puts = chain.puts[["strike", "impliedVolatility"]].dropna()
        puts = puts[puts["impliedVolatility"] > 0]
        calls = chain.calls[["strike", "impliedVolatility"]].dropna()
        calls = calls[calls["impliedVolatility"] > 0]

        put_iv = 0.30
        call_iv = 0.20
        if not puts.empty:
            idx = (puts["strike"] - put_target).abs().idxmin()
            put_iv = float(puts.loc[idx, "impliedVolatility"])
            if put_iv > 1.0:
                put_iv = put_iv / 100.0
        if not calls.empty:
            idx = (calls["strike"] - call_target).abs().idxmin()
            call_iv = float(calls.loc[idx, "impliedVolatility"])
            if call_iv > 1.0:
                call_iv = call_iv / 100.0

        return put_iv, call_iv
    except Exception:
        return 0.30, 0.20


def _iv_fallback(spot: float) -> tuple[float, float, float, float]:
    """Return sensible fallback IV values when options data is unavailable."""
    return 0.25, 0.27, 0.30, 0.22
