"""File-based cache for market context — keyed by (ticker, date).

Saves one JSON file per (ticker, date) pair under ~/.tradingbot/cache/.
TTL: same-day data is reused; stale entries are refreshed automatically.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Optional


def _cache_dir() -> Path:
    base = Path(os.getenv("TRADINGBOT_HOME", Path.home() / ".tradingbot"))
    d = base / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(ticker: str, date: str) -> Path:
    safe = ticker.upper().replace("^", "_").replace("/", "_")
    return _cache_dir() / f"{safe}_{date}.json"


def load(ticker: str, date: str) -> Optional[dict]:
    """Return cached market context for (ticker, date), or None if missing/stale.

    TTL: Historical dates cached indefinitely; today's data refreshed every 15 minutes.
    """
    path = _cache_path(ticker, date)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        cached_date = data.get("_cached_date", "")
        cached_time = data.get("_cached_time", "")
        today = datetime.date.today().isoformat()

        # Historical dates: cache never expires
        if date < today:
            return data

        # Today's data: refresh every 15 minutes
        if cached_date == today and cached_time:
            try:
                cached_dt = datetime.datetime.fromisoformat(cached_time)
                elapsed = (datetime.datetime.utcnow() - cached_dt).total_seconds()
                if elapsed < 900:  # 15 minutes
                    return data
            except (ValueError, TypeError):
                pass

        return None
    except Exception:
        return None


def save(ticker: str, date: str, context: dict) -> None:
    """Persist market context dict to cache with timestamp for TTL tracking."""
    path = _cache_path(ticker, date)
    payload = dict(context)
    payload["_cached_date"] = datetime.date.today().isoformat()
    payload["_cached_time"] = datetime.datetime.utcnow().isoformat()
    path.write_text(json.dumps(payload, default=str))


def fetch_with_cache(ticker: str, date: str) -> dict:
    """Return market context for (ticker, date), using cache when possible.

    Single unified source: moomoo (replaces Alpaca, yfinance, Tradier).
    Provides: price bars, quotes, IV rank, options chains, technicals.
    """
    cached = load(ticker, date)
    if cached is not None:
        cached.pop("_cached_date", None)
        cached.pop("_cached_time", None)
        return cached

    import os
    import warnings

    # C1 fix: moomoo's fetch_market_snapshot() has no `date` parameter — it
    # always returns the live snapshot. Using it for a historical `date`
    # silently scores backtests against current-market data. Gate it: only
    # call moomoo when the requested date is today.
    today_iso = datetime.date.today().isoformat()
    is_live_date = (date == today_iso)

    # ── Data fetch: try moomoo (live only), fallback to yfinance for history ──
    ctx = None
    if is_live_date:
        try:
            from tradingbot.dataflows.moomoo_data import fetch_market_snapshot
            snapshot = fetch_market_snapshot(ticker)

            if snapshot and "error" not in snapshot:
                quote = snapshot.get("quote", {})
                latest = snapshot.get("latest_candle", {})
                chain = snapshot.get("options_chain", {})

                ctx = {
                    "ticker": ticker,
                    "date": date,
                    "current_price": quote.get("last_price", 0.0),
                    "open_price": latest.get("open", 0.0),
                    "high_price": latest.get("high", 0.0),
                    "low_price": latest.get("low", 0.0),
                    "volume": quote.get("volume", 0),
                    "bid": quote.get("bid", 0.0),
                    "ask": quote.get("ask", 0.0),
                    "bid_size": quote.get("bid_size", 0),
                    "ask_size": quote.get("ask_size", 0),
                    "ema_20": latest.get("ema_20", 0.0),
                    "ema_50": latest.get("ema_50", 0.0),
                    "ema_200": latest.get("ema_200", 0.0),
                    "rsi_14": latest.get("rsi_14", 0.0),
                    "atr_14": latest.get("atr_14", 0.0),
                    "vwap": latest.get("vwap", 0.0),
                    "realized_vol": latest.get("realized_vol", 0.0),
                    "iv_rank": snapshot.get("iv_rank", 50.0),
                    "iv_percentile": snapshot.get("iv_percentile", 50.0),
                    "calls_count": len(chain.get("calls", [])),
                    "puts_count": len(chain.get("puts", [])),
                    "atm_strike": chain.get("atm_strike", 0.0),
                    "_options_chain": chain,
                    "_market_snapshot": snapshot,
                    "data_source": "moomoo",
                    "fetched_at": datetime.datetime.utcnow().isoformat(),
                }

                ctx["term_structure"] = {
                    "front_month_iv": 0.0,
                    "back_month_iv": 0.0,
                }
                ctx["skew_put_iv"] = 0.0
                ctx["skew_call_iv"] = 0.0

        except Exception as e:
            warnings.warn(f"moomoo data fetch failed ({e}); using yfinance fallback")
            ctx = None

    # ── Fallback to yfinance if moomoo unavailable ─────────────────────────────
    if ctx is None:
        try:
            from tradingbot.dataflows.yfinance_options import fetch_market_context
            ctx = fetch_market_context(ticker, date)
            ctx["data_source"] = "yfinance (moomoo fallback)"
        except Exception as e2:
            warnings.warn(f"yfinance fallback also failed ({e2}); returning minimal context")
            ctx = {
                "ticker": ticker,
                "date": date,
                "error": str(e2),
                "data_source": "error",
            }

    # ── News + sentiment overlay (independent of data source) ─────────────────
    try:
        from tradingbot.dataflows.news import fetch_news, composite_sentiment
        news_items = fetch_news(ticker, max_items=20, min_importance=0.0)
        sentiment = composite_sentiment(news_items)
        ctx["news_items"] = news_items
        ctx["sentiment_score"] = sentiment["score"]
        ctx["sentiment_label"] = sentiment["label"]
        ctx["sentiment_detail"] = sentiment
        ctx["news_fetched_at"] = __import__("datetime").datetime.utcnow().isoformat()
    except Exception as e:
        warnings.warn(f"News overlay failed ({e}); proceeding without news data")
        ctx.setdefault("news_items", [])
        ctx.setdefault("sentiment_score", 0.0)
        ctx.setdefault("sentiment_label", "neutral")

    save(ticker, date, ctx)
    return ctx
