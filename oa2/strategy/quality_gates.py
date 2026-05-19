"""Quality gates — pre-Kelly filters derived from backtest analysis.

Auto-sync architecture (added 2026-05-19):
    The blacklist and quality scores are kept at ~/.oa2/static_blacklist.json
    and refreshed nightly by `scripts/daily_learn.py` from the latest KB.
    The constants below are emergency fallbacks for when the data file is
    missing or corrupt -- they are NOT the source of truth.

    To force a refresh:
        python scripts/daily_learn.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# ---- Code-level fallback (used only when data file is missing) -------------
# Refreshed from 12mo backtest results_20260519_003232. The data file at
# ~/.oa2/static_blacklist.json is the source of truth; these are the seed
# values shipped with source.

_FALLBACK_BLACKLIST: set[str] = {"META", "XLI", "XLF", "TLT", "XLV"}

_FALLBACK_QUALITY_SCORE: dict[str, float] = {
    "GOOGL": 1.20, "AAPL": 1.15, "AMD": 1.10, "SLV": 1.08, "MSFT": 1.05,
    "GLD": 1.03, "XLK": 1.02, "QQQ": 1.00,
    "USO": 0.98, "XLE": 0.95, "SPY": 0.90,
    "TSLA": 0.85, "DIA": 0.85, "NVDA": 0.85, "XLY": 0.80,
    "IWM": 0.75, "AMZN": 0.75,
    "META": 0.0, "XLI": 0.0, "XLF": 0.0, "TLT": 0.0, "XLV": 0.0,
}


def _static_blacklist_path() -> Path:
    """Disk location for the auto-synced static blacklist."""
    from oa2.core.config import oa2_home
    return oa2_home() / "static_blacklist.json"


def _load_blacklist_data() -> tuple[set[str], dict[str, float]]:
    """Load the auto-synced blacklist + quality scores from disk.

    Falls back to the code-level constants on missing file, schema mismatch,
    or JSON parse error. Never raises.
    """
    path = _static_blacklist_path()
    if not path.exists():
        return set(_FALLBACK_BLACKLIST), dict(_FALLBACK_QUALITY_SCORE)
    try:
        data = json.loads(path.read_text())
        if int(data.get("schema_version", 0)) != 1:
            return set(_FALLBACK_BLACKLIST), dict(_FALLBACK_QUALITY_SCORE)
        tickers = {t.upper() for t in (data.get("tickers") or [])}
        scores = {k.upper(): float(v) for k, v in (data.get("quality_scores") or {}).items()}
        # Sanity: blacklisted tickers must score 0
        for t in tickers:
            scores[t] = 0.0
        return tickers, scores
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return set(_FALLBACK_BLACKLIST), dict(_FALLBACK_QUALITY_SCORE)


# ---- Public constants (loaded at import time, refreshed at module reload) ---
TICKER_BLACKLIST, TICKER_QUALITY_SCORE = _load_blacklist_data()


def reload_blacklist_from_disk() -> None:
    """Re-read the data file. Call after `daily_learn.py` writes the file."""
    global TICKER_BLACKLIST, TICKER_QUALITY_SCORE
    TICKER_BLACKLIST, TICKER_QUALITY_SCORE = _load_blacklist_data()

# Regimes where consensus has historically been below random.
MEAN_REVERT_REGIMES = {
    "vol_comp_mean_revert",
    "vol_exp_mean_revert",
    "normal_mean_revert",
}


def check_quality_gates(
    ticker: str,
    regime_label: str | None,
) -> tuple[bool, str | None]:
    """Return (passed, reject_reason) for pre-Kelly quality filters.

    Phase 3: prefers KnowledgeBase verdict when available. Falls back to
    the hardcoded TICKER_BLACKLIST + mean-revert filter for unknown tickers
    or when KB is empty.
    """
    # 1) KB-driven decision (preferred when available)
    try:
        from oa2.learning.rag_context import get_rag_context
        kb = get_rag_context()
        if kb.tickers and kb.is_blacklisted(ticker):
            stats = kb.tickers[ticker.upper()]
            return (
                False,
                f"Ticker {ticker} blacklisted by KB "
                f"(hit_rate={stats.hit_rate:.1%}, $win_rate={stats.dollar_weighted_win_rate:.1%}, n={stats.n_trades})"
            )
    except Exception:
        kb = None  # KB lookup failed; fall through to static rules

    # 2) Static blacklist fallback (covers tickers the KB hasn't seen)
    if ticker.upper() in TICKER_BLACKLIST:
        if kb is None or ticker.upper() not in (kb.tickers if kb else {}):
            return False, f"Ticker {ticker} on static quality blacklist (historical accuracy < 43%)"

    # 3) Mean-reverting regime gate (still static; KB regime mult only soft-suppresses)
    if regime_label and "mean_revert" in regime_label.lower():
        return False, f"Mean-reverting regime ({regime_label}) -- consensus accuracy < 45%"

    return True, None


def ticker_conviction_multiplier(ticker: str) -> float:
    """Per-ticker conviction multiplier.

    Prefers KnowledgeBase value when the ticker has met MIN_OBS_FOR_MULT;
    otherwise falls back to the hardcoded TICKER_QUALITY_SCORE; otherwise 1.0.
    """
    try:
        from oa2.learning.rag_context import get_rag_context
        kb = get_rag_context()
        if kb.tickers:
            stats = kb.tickers.get(ticker.upper())
            if stats and stats.n_trades >= 20:  # MIN_OBS_FOR_MULT
                return kb.ticker_multiplier(ticker)
    except Exception:
        pass
    return TICKER_QUALITY_SCORE.get(ticker.upper(), 1.0)


def regime_conviction_multiplier(regime_label: str | None) -> float:
    """KB-driven regime multiplier (soft suppression for weak regimes).

    Used in addition to the static mean-revert hard-block in check_quality_gates.
    Returns 1.0 when KB has no data for this regime.
    """
    if not regime_label:
        return 1.0
    try:
        from oa2.learning.rag_context import get_rag_context
        kb = get_rag_context()
        if kb.regimes:
            return kb.regime_multiplier(regime_label)
    except Exception:
        pass
    return 1.0
