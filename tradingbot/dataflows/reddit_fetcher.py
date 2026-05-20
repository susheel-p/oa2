"""Reddit sentiment fetcher using PRAW.

Pulls recent posts/comments from r/wallstreetbets, r/stocks, r/options mentioning the ticker.
Scores bullish vs bearish sentiment using keyword matching (same approach as news.py).
Gracefully degrades if PRAW not installed or credentials missing.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── Sentiment keywords ────────────────────────────────────────────────────────

_BULLISH_WORDS = {
    "beat", "bullish", "call", "diamond", "go long", "long", "pump", "rally",
    "strong", "surge", "tendies", "to the moon", "upside", "yolo",
}

_BEARISH_WORDS = {
    "bearish", "crash", "dead", "dump", "fail", "fear", "put", "rekt",
    "sell", "short", "tank", "weakness", "worst",
}


def fetch_reddit_sentiment(ticker: str, limit: int = 50) -> dict:
    """Fetch sentiment for ticker from Reddit (r/wallstreetbets, r/stocks, r/options).

    Args:
        ticker: Stock ticker (e.g. "AAPL")
        limit: Max posts per subreddit to scan

    Returns:
        {
            "bull_score": float (0-1),
            "bear_score": float (0-1),
            "mention_count": int,
            "bull_pct": float (0-1),
        }
        Returns empty dict on error (missing deps, no credentials, network fail, etc).
    """
    try:
        import praw
    except ImportError:
        logger.debug("PRAW not installed — skipping Reddit sentiment")
        return {}

    # Load credentials from env
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT", "optionsagents/1.0")

    if not client_id or not client_secret:
        logger.debug("REDDIT_CLIENT_ID/SECRET not set — skipping Reddit sentiment")
        return {}

    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
        )

        subreddits = ["wallstreetbets", "stocks", "options"]
        ticker_lower = ticker.lower()
        ticker_upper = ticker.upper()

        bull_count = 0
        bear_count = 0
        mention_count = 0

        for subreddit_name in subreddits:
            try:
                subreddit = reddit.subreddit(subreddit_name)
                for post in subreddit.hot(limit=limit):
                    text = (post.title + " " + post.selftext).lower()
                    if ticker_lower not in text and ticker_upper not in text:
                        continue

                    mention_count += 1

                    # Count sentiment signals in this post
                    post_bull = sum(1 for word in _BULLISH_WORDS if word in text)
                    post_bear = sum(1 for word in _BEARISH_WORDS if word in text)

                    if post_bull > post_bear:
                        bull_count += 1
                    elif post_bear > post_bull:
                        bear_count += 1

            except Exception as e:
                logger.debug(f"Reddit {subreddit_name} fetch failed: {e}")
                continue

        if mention_count == 0:
            return {
                "bull_score": 0.0,
                "bear_score": 0.0,
                "mention_count": 0,
                "bull_pct": 0.5,  # Neutral if no mentions
            }

        bull_pct = bull_count / mention_count
        bear_pct = bear_count / mention_count

        # Normalize to -1 to +1 scale
        composite = bull_pct - bear_pct  # Ranges from -1 to +1

        return {
            "bull_score": (1 + composite) / 2,  # Convert to 0-1
            "bear_score": (1 - composite) / 2,  # Convert to 0-1
            "mention_count": mention_count,
            "bull_pct": bull_pct,
        }

    except Exception as e:
        logger.warning(f"Reddit fetch failed: {e}")
        return {}
