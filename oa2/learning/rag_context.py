"""RAG context — inject knowledge base into pipeline at decision time.

Phase 3 of the RAG learning loop.

Replaces the hardcoded TICKER_BLACKLIST and TICKER_QUALITY_SCORE in
oa2/strategy/quality_gates.py with dynamic lookups against
~/.oa2/knowledge_base.json. Falls back to the hardcoded values when no KB
exists or for tickers the KB hasn't seen yet.

Cache strategy: the KB is loaded once per process and cached. Call
`reset_rag_cache()` between tests or to force a fresh read.

Soft-mode design (per RAG_LEARNING_PLAN: agression level A):
  - KB-driven blacklist: only blocks when both hit_rate AND $-win-rate
    are below threshold (built into KB.is_blacklisted)
  - Multipliers: capped at [0.5, 1.3] so KB can never zero-out a ticker
    or amplify risk beyond 30%
  - Pipeline still runs the static quality_gates as a fallback when KB
    is missing or too thin
"""

from __future__ import annotations

from pathlib import Path

from oa2.learning.knowledge_base import KnowledgeBase, default_kb_path


_CACHED_KB: KnowledgeBase | None = None
_CACHED_PATH: Path | None = None


def get_rag_context(path: Path | None = None) -> KnowledgeBase:
    """Return the cached KB, loading on first access.

    Args:
        path: optional explicit KB path. Defaults to `default_kb_path()`.

    Returns:
        KnowledgeBase (may be empty if no file exists — callers should treat
        empty KB as "use static fallbacks").
    """
    global _CACHED_KB, _CACHED_PATH
    if path is None:
        path = default_kb_path()
    if _CACHED_KB is None or _CACHED_PATH != path:
        _CACHED_KB = KnowledgeBase.load(path)
        _CACHED_PATH = path
    return _CACHED_KB


def reset_rag_cache() -> None:
    """Clear the cached KB (for tests or forced refresh)."""
    global _CACHED_KB, _CACHED_PATH
    _CACHED_KB = None
    _CACHED_PATH = None


def kb_is_available() -> bool:
    """Return True if a non-empty KB exists on disk."""
    kb = get_rag_context()
    return bool(kb.tickers)
