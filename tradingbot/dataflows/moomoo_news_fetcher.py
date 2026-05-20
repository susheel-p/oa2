"""moomoo News fetcher using moomoo SDK.

Pulls recent news for ticker from moomoo OpenNews API via OpenD server.
Scores headlines using same keyword approach as alpaca_news_fetcher.
Gracefully degrades if OpenD unavailable or API fails.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def fetch_moomoo_news(ticker: str, limit: int = 20) -> dict:
    """Fetch news and sentiment for ticker from moomoo News API.

    Args:
        ticker: Stock ticker (e.g. "AAPL")
        limit: Max articles to fetch

    Returns:
        {
            "score": float (-1 to +1),
            "article_count": int,
            "catalyst_tags": list[str],
        }
        Returns empty dict on error (OpenD unavailable, API fail, etc).
    """
    try:
        import moomoo as ft
        from moomoo import OpenNewsContext
    except ImportError:
        logger.debug("moomoo SDK not installed — skipping moomoo news")
        return {}

    try:
        # Connect to OpenD server (same as quote context)
        news_ctx = ft.OpenNewsContext(host="127.0.0.1", port=11111)
    except Exception as e:
        logger.debug(f"Failed to connect to OpenNews context: {e}")
        return {}

    try:
        # Fetch news for ticker
        ret_code, articles = news_ctx.get_news(ticker.upper(), limit=limit)

        if ret_code != 0:
            logger.debug(f"moomoo news fetch failed with code {ret_code}")
            return {}

        if not articles:
            return {"score": 0.0, "article_count": 0, "catalyst_tags": []}

        # Score headlines using same keywords as alpaca_news_fetcher.py
        bull_count = 0
        bear_count = 0
        catalyst_tags = set()

        bullish_words = {
            "beat", "bullish", "gain", "jump", "profit", "raise", "rally",
            "strong", "surge", "upgrade", "approval", "buyback", "growth",
        }
        bearish_words = {
            "bearish", "cut", "decline", "downgrade", "fall", "loss", "miss",
            "warn", "weak", "investigation", "lawsuit", "recall", "crash",
        }
        catalyst_patterns = [
            ("earn", "earnings"),
            ("acqui", "m_and_a"),
            ("fda", "fda"),
            ("fed", "macro"),
            ("upgrade", "analyst"),
            ("dividend", "capital_return"),
        ]

        for article in articles:
            # moomoo article structure: {title, content, source, time, ...}
            headline = ""
            if hasattr(article, "title"):
                headline = article.title or ""
            elif isinstance(article, dict):
                headline = article.get("title", "")

            headline = headline.lower()

            # Sentiment
            bull = sum(1 for w in bullish_words if w in headline)
            bear = sum(1 for w in bearish_words if w in headline)
            if bull > bear:
                bull_count += 1
            elif bear > bull:
                bear_count += 1

            # Catalysts
            for pattern, tag in catalyst_patterns:
                if pattern in headline:
                    catalyst_tags.add(tag)

        total = bull_count + bear_count
        if total == 0:
            score = 0.0
        else:
            score = (bull_count - bear_count) / total

        return {
            "score": score,
            "article_count": len(articles),
            "catalyst_tags": list(catalyst_tags),
        }

    except Exception as e:
        logger.debug(f"moomoo news fetch failed: {e}")
        return {}
