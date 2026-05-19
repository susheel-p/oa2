"""Quality gates — pre-Kelly filters derived from backtest analysis.

P1 fixes shipped 2026-05-18:
    - Mean-reverting regime gate: backtest showed mean-revert regimes had
      40-45% accuracy vs 50-55% in trending/neutral. Block those trades.
    - Ticker quality filter: bottom-5 tickers (37-42% accuracy over 6mo)
      consistently lose money; suppress them.

Update TICKER_BLACKLIST and TICKER_QUALITY_SCORE periodically from a
rolling-window backtest. Current values are from results_20260518_201029
(6-month sample, 22 tickers, 1,650 days).
"""

from __future__ import annotations

from typing import Any


# Tickers with <43% accuracy over the 6-month sample — net negative EV.
TICKER_BLACKLIST: set[str] = {"SLV", "TLT", "DIA", "TSLA", "XLV"}

# Per-ticker quality multiplier (1.0 = no change; 0.0 = block).
# Derived from rolling accuracy; multiplied with consensus conviction.
TICKER_QUALITY_SCORE: dict[str, float] = {
    "XLE": 1.10, "GOOGL": 1.08, "USO": 1.07, "AMD": 1.07, "XLK": 1.06,
    "AMZN": 1.02, "XLY": 1.00, "NVDA": 1.00, "MSFT": 1.00, "QQQ": 1.00,
    "XLI": 1.00, "SPY": 0.97, "META": 0.95, "AAPL": 0.95, "IWM": 0.94,
    "XLF": 0.93, "GLD": 0.88,
    # Blacklisted (set to 0 to make the suppression explicit):
    "XLV": 0.0, "TSLA": 0.0, "DIA": 0.0, "TLT": 0.0, "SLV": 0.0,
}

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
