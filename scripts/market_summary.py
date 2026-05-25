"""Generate daily 8am market summary: economic calendar, sentiment, technicals, news, volatility."""

from __future__ import annotations

import datetime
import os
from zoneinfo import ZoneInfo

import yfinance as yf
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

ET = ZoneInfo("America/New_York")
FINNHUB_API_KEY = os.getenv("FINHUB_API_KEY", "").strip()


def get_market_summary() -> str:
    """Build crispy market summary with 5 key points as bullet points.

    Returns formatted string suitable for Telegram with structured bullets.
    """
    now = datetime.datetime.now(ET)
    date_str = now.strftime("%a, %b %d")

    # Fetch market data
    spy = yf.Ticker("SPY")
    qqq = yf.Ticker("QQQ")
    vix = yf.Ticker("^VIX")

    try:
        # Get last close prices for technicals
        spy_hist = spy.history(period="5d")
        qqq_hist = qqq.history(period="5d")
        vix_hist = vix.history(period="5d")

        spy_close = spy_hist["Close"].iloc[-1] if len(spy_hist) > 0 else 0
        qqq_close = qqq_hist["Close"].iloc[-1] if len(qqq_hist) > 0 else 0
        spy_sma20 = spy_hist["Close"].tail(20).mean() if len(spy_hist) >= 20 else 0
        qqq_sma20 = qqq_hist["Close"].tail(20).mean() if len(qqq_hist) >= 20 else 0

        vix_close = vix_hist["Close"].iloc[-1] if len(vix_hist) > 0 else 0

        # Build points
        economic = _get_economic_calendar(now)
        sentiment = _get_sentiment(vix_close)
        technicals = _get_technicals(spy_close, spy_sma20, qqq_close, qqq_sma20)
        news = _get_news_summary(now)
        volatility = _get_volatility_outlook(vix_close)

        lines = [
            f"🌅 Market Summary — {date_str}",
            f"📊 Economic: {economic}",
            f"📈 Sentiment: {sentiment}",
            f"🎯 Technicals: {technicals}",
            f"📰 News: {news}",
            f"⚡ Volatility: {volatility}",
        ]
        return "\n".join(lines)

    except Exception as e:
        return f"🌅 Market Summary — {date_str}\n⚠️ Unavailable: {e}"


def _get_economic_calendar(now: datetime.datetime) -> str:
    """Economic data releases for the day (one-liner)."""
    # TODO: Wire to actual economic calendar API (e.g., Finnhub, FRED)
    # For now, return placeholder; user can hardcode major dates
    day_of_week = now.strftime("%A")

    # Placeholder mapping of major economic events
    major_events = {
        "Monday": "No major econ data",
        "Tuesday": "CPI/PPI watch",
        "Wednesday": "Fed speakers, initial jobless claims",
        "Thursday": "Jobless claims, retail sales",
        "Friday": "Jobs report, consumer sentiment",
    }
    return major_events.get(day_of_week, "Check economic calendar")


def _get_sentiment(vix_close: float) -> str:
    """Market sentiment based on VIX level."""
    if vix_close < 12:
        return f"Calm (VIX {vix_close:.1f})"
    elif vix_close < 16:
        return f"Neutral (VIX {vix_close:.1f})"
    elif vix_close < 20:
        return f"Elevated (VIX {vix_close:.1f})"
    else:
        return f"Stressed (VIX {vix_close:.1f})"


def _get_technicals(spy: float, spy_sma20: float, qqq: float, qqq_sma20: float) -> str:
    """SPY/QQQ position vs 20-day MA."""
    spy_status = "above" if spy > spy_sma20 else "below"
    qqq_status = "above" if qqq > qqq_sma20 else "below"
    return f"SPY {spy_status} SMA20, QQQ {qqq_status} SMA20"


def _get_news_summary(now: datetime.datetime) -> str:
    """Top market news from Finnhub (real headlines that can move markets)."""
    if not FINNHUB_API_KEY:
        return "Market news (API key not configured)"

    try:
        # Fetch general market news from Finnhub
        url = f"https://finnhub.io/api/v1/news?category=general&minId=0&token={FINNHUB_API_KEY}"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        if not data or not isinstance(data, list) or len(data) == 0:
            return "Market news (no recent headlines)"

        lines = ["Top market movers:"]
        # Get top 3 headlines
        for i, item in enumerate(data[:3], 1):
            headline = item.get("headline", "").strip()
            source = item.get("source", "").upper()
            if headline:
                # Truncate long headlines to ~60 chars
                if len(headline) > 60:
                    headline = headline[:57] + "..."
                lines.append(f"  {i}. {headline} ({source})")

        return "\n".join(lines) if len(lines) > 1 else "Market news (fetch failed)"

    except Exception as e:
        return f"Market news (error: {type(e).__name__})"


def _get_volatility_outlook(vix_close: float) -> str:
    """Expected IV regime and trading implications for the day."""
    if vix_close < 14:
        lines = [
            "Low IV regime — Spreads compressed",
            "  • Good for defined-risk spreads (ICs, verticals)",
            "  • Avoid naked short premium, wide stops",
            "  • Expect mean-reversion trades to perform",
        ]
    elif vix_close < 18:
        lines = [
            "Normal IV — Balanced risk/reward",
            "  • Both directional and premium collection viable",
            "  • Watch for surprise catalysts to spike IV",
            "  • Standard position sizing applies",
        ]
    else:
        lines = [
            "High IV — Elevated volatility expected",
            "  • Favor directional trades over premium sales",
            "  • Watch for volatility crush on event passes",
            "  • Wider moves likely, use strict stops",
        ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(get_market_summary())
