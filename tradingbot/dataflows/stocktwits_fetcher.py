"""StockTwits sentiment fetcher using public REST API.

Pulls recent messages mentioning ticker from StockTwits.
Counts bull/bear labeled messages (no auth needed for public endpoint).
Gracefully degrades on network error or timeout.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def fetch_stocktwits_sentiment(
    ticker: str, limit: int = 30, timeout: float = 5.0
) -> dict:
    """Fetch sentiment for ticker from StockTwits public API.

    Args:
        ticker: Stock ticker (e.g. "AAPL")
        limit: Max messages to fetch
        timeout: Request timeout in seconds

    Returns:
        {
            "bull_pct": float (0-1),
            "bear_pct": float (0-1),
            "message_count": int,
            "composite_score": float (-1 to +1),
        }
        Returns empty dict on error (network fail, timeout, invalid ticker, etc).
    """
    try:
        import aiohttp
    except ImportError:
        logger.debug("aiohttp not installed — skipping StockTwits sentiment")
        return {}

    url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker.upper()}.json"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    logger.debug(f"StockTwits API returned {resp.status} for {ticker}")
                    return {}

                data = await resp.json()

        messages = data.get("messages", [])
        if not messages:
            return {
                "bull_pct": 0.5,
                "bear_pct": 0.5,
                "message_count": 0,
                "composite_score": 0.0,
            }

        bull_count = 0
        bear_count = 0

        for msg in messages[:limit]:
            sentiment = msg.get("sentiment")
            if sentiment == "Bullish":
                bull_count += 1
            elif sentiment == "Bearish":
                bear_count += 1

        total = bull_count + bear_count
        if total == 0:
            return {
                "bull_pct": 0.5,
                "bear_pct": 0.5,
                "message_count": len(messages),
                "composite_score": 0.0,
            }

        bull_pct = bull_count / total
        bear_pct = bear_count / total
        composite = bull_pct - bear_pct

        return {
            "bull_pct": bull_pct,
            "bear_pct": bear_pct,
            "message_count": len(messages),
            "composite_score": composite,
        }

    except asyncio.TimeoutError:
        logger.debug(f"StockTwits request timeout for {ticker}")
        return {}
    except Exception as e:
        logger.debug(f"StockTwits fetch failed for {ticker}: {e}")
        return {}
