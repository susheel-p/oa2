"""Per-ticker news fetcher and sentiment scorer.

Fetches headlines from yfinance (free, no API key), dedupes within a session,
scores importance and sentiment using keyword rules (no model needed at this
scale), and returns a structured list ready to merge into the market context.

Output shape (appended to context dict by cache.py):
  news_items:      list[dict]   — scored headlines, newest first
  sentiment_score: float        — composite in [-1.0, +1.0]
  sentiment_label: str          — "bullish" | "bearish" | "neutral"
  news_fetched_at: str          — ISO timestamp

Each news_item dict:
  {
    "title":       str,
    "publisher":   str,
    "published":   str,          # ISO timestamp
    "url":         str,
    "importance":  float,        # 0–1 (1=highest)
    "sentiment":   float,        # -1 to +1
    "catalyst_tag": str | None,  # "earnings" | "m_and_a" | "fda" | "macro" | None
  }
"""

from __future__ import annotations

import datetime
import hashlib
import re
from typing import Optional


# ── Importance keywords — higher weight = higher signal-to-noise ──────────────

_HIGH_IMPORTANCE: list[str] = [
    "earnings", "revenue", "eps", "guidance", "beat", "miss", "profit",
    "acquisition", "merger", "buyout", "deal", "takeover",
    "fda", "approval", "trial", "clinical",
    "fed", "fomc", "rate", "inflation", "cpi", "ppi",
    "layoff", "job", "recall", "investigation", "lawsuit", "sec",
    "dividend", "buyback", "split",
    "upgrade", "downgrade", "price target",
]

_LOW_IMPORTANCE: list[str] = [
    "analyst", "could", "might", "may", "predicts", "forecasts",
    "opinion", "why", "how", "what", "best", "top", "must-read",
    "here's what", "here is", "watch", "look at", "roundup",
]

# ── Sentiment keywords ────────────────────────────────────────────────────────

_BULLISH_WORDS: list[str] = [
    "beat", "beats", "surpass", "record", "rally", "soar", "surge", "jump",
    "rise", "gain", "profit", "outperform", "upgrade", "raise", "bullish",
    "strong", "positive", "growth", "expand", "win", "approval", "buyback",
    "dividend increase",
]

_BEARISH_WORDS: list[str] = [
    "miss", "misses", "fall", "drop", "plunge", "slide", "sink", "tumble",
    "decline", "loss", "warn", "cut", "downgrade", "lower", "bearish",
    "weak", "negative", "shrink", "recall", "investigation", "lawsuit",
    "layoff", "delay", "fail", "reject",
]

# ── Catalyst tag patterns ─────────────────────────────────────────────────────

_CATALYST_PATTERNS: list[tuple[str, str]] = [
    (r"earn|eps|revenue|quarter|guidance|profit", "earnings"),
    (r"acqui|merger|buyout|takeover|deal", "m_and_a"),
    (r"fda|approval|trial|clinical|drug", "fda"),
    (r"fed|fomc|rate|inflation|cpi|ppi|macro|economy", "macro"),
    (r"upgrade|downgrade|price target|initiat", "analyst"),
    (r"buyback|repurchase|dividend|split", "capital_return"),
    (r"recall|investigation|lawsuit|sec|probe", "legal"),
    (r"layoff|restructur|job cut", "restructuring"),
]


# ── Scoring helpers ───────────────────────────────────────────────────────────

def _importance_score(title: str) -> float:
    """Score 0–1: fraction of high-importance keywords found minus noise penalty."""
    title_lower = title.lower()
    hits = sum(1 for kw in _HIGH_IMPORTANCE if kw in title_lower)
    noise = sum(1 for kw in _LOW_IMPORTANCE if kw in title_lower)
    raw = min(hits, 3) / 3.0 - noise * 0.1
    return round(max(0.0, min(1.0, raw)), 3)


def _sentiment_score(title: str) -> float:
    """Score -1 to +1 based on keyword polarity."""
    title_lower = title.lower()
    bull = sum(1 for w in _BULLISH_WORDS if w in title_lower)
    bear = sum(1 for w in _BEARISH_WORDS if w in title_lower)
    total = bull + bear
    if total == 0:
        return 0.0
    return round((bull - bear) / total, 3)


def _catalyst_tag(title: str) -> Optional[str]:
    """Return the first matching catalyst category, or None."""
    title_lower = title.lower()
    for pattern, tag in _CATALYST_PATTERNS:
        if re.search(pattern, title_lower):
            return tag
    return None


def _dedup_key(title: str) -> str:
    """Stable hash for deduplication — same article from two publishers = same key."""
    clean = re.sub(r"[^a-z0-9 ]", "", title.lower())
    words = clean.split()[:8]  # first 8 words capture the headline topic
    return hashlib.md5(" ".join(words).encode()).hexdigest()


# ── Main fetch function ───────────────────────────────────────────────────────

def fetch_news(ticker: str, max_items: int = 20, min_importance: float = 0.0) -> list[dict]:
    """Fetch, score, and dedupe recent headlines for ticker.

    Args:
        ticker:         Ticker symbol.
        max_items:      Maximum headlines to return (after dedup + filter).
        min_importance: Drop items below this importance threshold (0–1).

    Returns:
        List of news_item dicts, newest first.
    """
    import yfinance as yf
    import warnings

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            raw = yf.Ticker(ticker).news or []
    except Exception:
        return []

    seen: set[str] = set()
    items: list[dict] = []

    for article in raw:
        # yfinance ≥0.2.55 nests content under a 'content' key
        content = article.get("content") or article
        title = content.get("title") or article.get("title") or ""
        if not title:
            continue

        dk = _dedup_key(title)
        if dk in seen:
            continue
        seen.add(dk)

        importance = _importance_score(title)
        if importance < min_importance:
            continue

        sentiment = _sentiment_score(title)
        tag = _catalyst_tag(title)

        # Try multiple timestamp fields across API versions
        pub_ts = article.get("providerPublishTime") or 0
        pub_str = content.get("pubDate") or ""
        if pub_str:
            try:
                published = datetime.datetime.fromisoformat(
                    pub_str.replace("Z", "+00:00")
                ).isoformat()
            except Exception:
                published = datetime.datetime.now(datetime.timezone.utc).isoformat()
        elif pub_ts:
            published = datetime.datetime.fromtimestamp(
                pub_ts, tz=datetime.timezone.utc
            ).isoformat()
        else:
            published = datetime.datetime.now(datetime.timezone.utc).isoformat()

        provider = content.get("provider", {})
        publisher = (
            provider.get("displayName")
            or article.get("publisher")
            or ""
        )
        canonical = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
        url = canonical.get("url") or article.get("link") or ""

        items.append({
            "title": title,
            "publisher": publisher,
            "published": published,
            "url": url,
            "importance": importance,
            "sentiment": sentiment,
            "catalyst_tag": tag,
        })

        if len(items) >= max_items:
            break

    # Sort newest first
    items.sort(key=lambda x: x["published"], reverse=True)
    return items


# ── Composite sentiment ───────────────────────────────────────────────────────

def composite_sentiment(
    news_items: list[dict],
    options_flow_score: float = 0.0,
    tape_score: float = 0.0,
    w_news: float = 0.4,
    w_flow: float = 0.35,
    w_tape: float = 0.25,
    recency_halflife_hours: float = 4.0,
) -> dict:
    """Weighted composite of news, options flow, and tape sentiment.

    News items are time-decayed with exponential decay (halflife = recency_halflife_hours).
    Returns:
      {
        "score":  float  in [-1.0, +1.0],
        "label":  str    "bullish" | "bearish" | "neutral",
        "news_component":   float,
        "flow_component":   float,
        "tape_component":   float,
        "n_news_used":      int,
      }
    """
    now = datetime.datetime.now(datetime.timezone.utc)

    weighted_sum = 0.0
    weight_total = 0.0

    for item in news_items:
        if item["importance"] < 0.1:
            continue
        try:
            pub = datetime.datetime.fromisoformat(item["published"])
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=datetime.timezone.utc)
            age_hours = (now - pub).total_seconds() / 3600.0
        except Exception:
            age_hours = 24.0

        decay = 0.5 ** (age_hours / recency_halflife_hours)
        w = item["importance"] * decay
        weighted_sum += item["sentiment"] * w
        weight_total += w

    news_score = round(weighted_sum / weight_total, 3) if weight_total > 0 else 0.0

    composite = round(
        w_news * news_score
        + w_flow * options_flow_score
        + w_tape * tape_score,
        3,
    )
    composite = max(-1.0, min(1.0, composite))

    if composite >= 0.2:
        label = "bullish"
    elif composite <= -0.2:
        label = "bearish"
    else:
        label = "neutral"

    return {
        "score": composite,
        "label": label,
        "news_component": news_score,
        "flow_component": options_flow_score,
        "tape_component": tape_score,
        "n_news_used": sum(1 for i in news_items if i["importance"] >= 0.1),
    }
