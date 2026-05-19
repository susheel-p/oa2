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

    Args:
        ticker: ticker symbol.
        regime_label: regime classifier output (e.g., 'vol_exp_mean_revert').

    Returns:
        (True, None) if all gates pass; (False, reason) if any gate blocks.
    """
    if ticker.upper() in TICKER_BLACKLIST:
        return False, f"Ticker {ticker} on quality blacklist (historical accuracy < 43%)"

    # Match either abbreviated ("vol_comp_mean_revert") or full
    # ("vol_compression_mean_revert") form by suffix check.
    if regime_label and "mean_revert" in regime_label.lower():
        return False, f"Mean-reverting regime ({regime_label}) -- consensus accuracy < 45%"

    return True, None


def ticker_conviction_multiplier(ticker: str) -> float:
    """Per-ticker conviction multiplier for non-blacklisted symbols.

    Defaults to 1.0 for unknown tickers (neutral). Blacklisted tickers
    should be filtered by `check_quality_gates` before this is consulted.
    """
    return TICKER_QUALITY_SCORE.get(ticker.upper(), 1.0)
