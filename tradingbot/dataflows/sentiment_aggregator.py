"""Sentiment aggregator: combines Reddit, moomoo News, Finnhub news.

Fetches sentiment from 3 sources in parallel, computes weighted composite,
returns SentimentSnapshot. Caches results for 15 minutes.
(StockTwits blocked all API access; disabled.)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import requests

from tradingbot.core.schemas import SentimentSnapshot
from tradingbot.dataflows.reddit_fetcher import fetch_reddit_sentiment
from tradingbot.dataflows.stocktwits_fetcher import fetch_stocktwits_sentiment
from tradingbot.dataflows.moomoo_news_fetcher import fetch_moomoo_news
from tradingbot.dataflows.news import composite_sentiment  # yfinance scorer

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logger = logging.getLogger(__name__)

FINNHUB_API_KEY = os.getenv("FINHUB_API_KEY", "").strip()

# Cache: {f"{ticker}_{timestamp_bucket}": SentimentSnapshot}
_SENTIMENT_CACHE = {}
_CACHE_TTL_MINUTES = 15

# Bullish and bearish keywords for news sentiment scoring
_BULLISH_KEYWORDS = {
    "beat", "upgrade", "surge", "rally", "gain", "outperform", "strong", "bullish",
    "upside", "positive", "growth", "expansion", "record", "milestone", "recovery",
    "approval", "partnership", "acquisition", "deal", "success", "breakthrough"
}
_BEARISH_KEYWORDS = {
    "miss", "downgrade", "plunge", "crash", "loss", "underperform", "weak", "bearish",
    "downside", "negative", "decline", "contraction", "worst", "warning", "halt",
    "decline", "lawsuit", "investigation", "recall", "bankruptcy", "risk"
}


async def fetch_finnhub_news_sentiment(ticker: str) -> tuple[float, list[str]]:
    """Fetch news from Finnhub and score sentiment (-1 to +1).

    Returns:
        Tuple of (sentiment_score, catalyst_tags)
    """
    if not FINNHUB_API_KEY:
        return 0.0, []

    try:
        # Company news endpoint requires date range (YYYY-MM-DD format)
        now = datetime.utcnow()
        to_date = now.strftime("%Y-%m-%d")
        from_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")

        url = f"https://finnhub.io/api/v1/company-news?symbol={ticker}&from={from_date}&to={to_date}&limit=20&token={FINNHUB_API_KEY}"
        resp = await asyncio.to_thread(requests.get, url, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        if not isinstance(data, list) or len(data) == 0:
            return 0.0, []

        # Score headlines with keyword matching
        bullish_count = 0
        bearish_count = 0
        catalysts = []

        for item in data[:10]:  # Check top 10 headlines
            headline = (item.get("headline", "") + " " + item.get("summary", "")).lower()

            # Count keyword matches
            bull_matches = sum(1 for kw in _BULLISH_KEYWORDS if kw in headline)
            bear_matches = sum(1 for kw in _BEARISH_KEYWORDS if kw in headline)

            if bull_matches > bear_matches:
                bullish_count += bull_matches
            elif bear_matches > bull_matches:
                bearish_count += bear_matches

            # Extract catalyst tags
            if bull_matches > 0 or bear_matches > 0:
                source = item.get("source", "").upper()
                headline_short = item.get("headline", "")[:50]
                if headline_short and source:
                    catalysts.append(f"{source}: {headline_short}")

        # Convert to -1 to +1 score
        total = bullish_count + bearish_count
        if total == 0:
            return 0.0, catalysts[:3]

        score = (bullish_count - bearish_count) / (total + 1)
        return float(score), catalysts[:3]

    except Exception as e:
        logger.debug(f"Finnhub news sentiment fetch failed for {ticker}: {e}")
        return 0.0, []


async def fetch_sentiment(
    ticker: str,
    current_price: Optional[float] = None,
    force_refresh: bool = False,
) -> SentimentSnapshot:
    """Fetch aggregated sentiment for ticker from all sources.

    Args:
        ticker: Stock ticker (e.g. "AAPL")
        current_price: Optional price for cache context
        force_refresh: Skip cache if True

    Returns:
        SentimentSnapshot with composite score and source breakdown.
    """
    # Check cache
    now = datetime.utcnow()
    cache_key = f"{ticker}_{now.hour}_{now.minute // 15}"

    if not force_refresh and cache_key in _SENTIMENT_CACHE:
        cached = _SENTIMENT_CACHE[cache_key]
        age = (now - cached.fetched_at).total_seconds()
        if age < _CACHE_TTL_MINUTES * 60:
            logger.debug(f"Using cached sentiment for {ticker}")
            return cached

    # Fetch all sources in parallel
    try:
        # Parallel fetch: Reddit, moomoo, Finnhub
        # Note: StockTwits blocked all API access (403 on all endpoints)
        reddit_result = await asyncio.to_thread(fetch_reddit_sentiment, ticker, 50)
        moomoo_result = await asyncio.to_thread(fetch_moomoo_news, ticker, 20)
        yf_score, yf_catalyst_tags = await fetch_finnhub_news_sentiment(ticker)
        stocktwits_result = {}  # StockTwits API blocked

        # Extract values with defaults
        reddit_bull_pct = reddit_result.get("bull_pct", 0.5)
        stocktwits_bull_pct = stocktwits_result.get("bull_pct", 0.5)
        moomoo_score = moomoo_result.get("score", 0.0)
        reddit_mentions = reddit_result.get("mention_count", 0)
        stocktwits_mentions = stocktwits_result.get("message_count", 0)
        moomoo_catalysts = moomoo_result.get("catalyst_tags", [])

        # Merge catalysts from Finnhub and moomoo
        all_catalysts = list(yf_catalyst_tags) + moomoo_catalysts

        # Sources that returned data
        data_sources = []
        if reddit_mentions > 0:
            data_sources.append("reddit")
        if stocktwits_mentions > 0:
            data_sources.append("stocktwits")
        if moomoo_result:
            data_sources.append("moomoo")
        if yf_score != 0.0:
            data_sources.append("yfinance")

        # Composite score (weighted average of available sources)
        # Weights: Finnhub (news sentiment) + moomoo (news catalysts) + Reddit (retail)
        # NOTE: StockTwits blocked all API access (403 on all endpoints), reassigned weight to Finnhub
        # yfinance/finnhub=0.35 (news-driven sentiment), moomoo=0.30 (news catalysts),
        # reddit=0.35 (broader retail sentiment)
        weights = {
            "yfinance": 0.35,  # Increased from 0.15 (StockTwits blocked)
            "moomoo": 0.30,
            "reddit": 0.35,
            "stocktwits": 0.0,  # Blocked
        }

        available_weight = sum(weights[s] for s in data_sources)
        if available_weight == 0:
            available_weight = 1.0  # All missing

        # Normalize scores to -1 to +1
        scores = {
            "yfinance": yf_score,
            "moomoo": moomoo_score,
            "reddit": 2 * reddit_bull_pct - 1,  # Convert 0-1 to -1 to 1
            "stocktwits": 2 * stocktwits_bull_pct - 1,
        }

        composite_score = 0.0
        if available_weight > 0:
            for source in data_sources:
                composite_score += (weights[source] / available_weight) * scores[source]

        # Composite label
        if composite_score > 0.6:
            label = "STRONGLY_BULLISH"
        elif composite_score > 0.2:
            label = "BULLISH"
        elif composite_score < -0.6:
            label = "STRONGLY_BEARISH"
        elif composite_score < -0.2:
            label = "BEARISH"
        else:
            label = "NEUTRAL"

        # Create snapshot
        snapshot = SentimentSnapshot(
            ticker=ticker,
            yfinance_score=yf_score,
            alpaca_score=moomoo_score,  # Schema field reused for moomoo news score
            reddit_bull_pct=reddit_bull_pct,
            stocktwits_bull_pct=stocktwits_bull_pct,
            composite_score=round(composite_score, 3),
            composite_label=label,
            mention_count=reddit_mentions + stocktwits_mentions,
            catalyst_tags=all_catalysts,
            data_sources=data_sources,
            fetched_at=now,
        )

        # Cache it
        _SENTIMENT_CACHE[cache_key] = snapshot

        # Clean old cache entries
        cutoff = now - timedelta(minutes=_CACHE_TTL_MINUTES * 2)
        stale_keys = [
            k for k, v in _SENTIMENT_CACHE.items()
            if v.fetched_at < cutoff
        ]
        for k in stale_keys:
            del _SENTIMENT_CACHE[k]

        return snapshot

    except Exception as e:
        logger.warning(f"Sentiment aggregation failed for {ticker}: {e}")
        # Return neutral snapshot on total failure
        return SentimentSnapshot(
            ticker=ticker,
            yfinance_score=0.0,
            alpaca_score=0.0,  # Schema field reused for moomoo news score
            reddit_bull_pct=0.5,
            stocktwits_bull_pct=0.5,
            composite_score=0.0,
            composite_label="NEUTRAL",
            mention_count=0,
            catalyst_tags=[],
            data_sources=[],
            fetched_at=now,
        )
