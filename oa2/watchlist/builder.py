"""oa2 watchlist — 22-ticker fixed universe.

Curated, hardcoded list. No daily promotion, no Fisher, no Tier-2 churn.
Expansion happens through `oa2/watchlist/promote.py` (later phase) with
gated 30-day shadow mode evaluation.

Composition:
  4 indices         — dealer signal applies (SPY/QQQ/IWM only)
  4 macro ETFs      — dealer abstains
  6 sector ETFs     — dealer abstains
  8 mega-caps       — dealer abstains; 2-day earnings blackout enforced
"""

from __future__ import annotations

INDICES: list[str] = ["SPY", "QQQ", "IWM", "DIA"]

MACRO_ETFS: list[str] = ["GLD", "SLV", "TLT", "USO"]

SECTOR_ETFS: list[str] = ["XLF", "XLE", "XLK", "XLV", "XLI", "XLY"]

MEGA_CAPS: list[str] = [
    "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "AMD",
]

WATCHLIST: list[str] = INDICES + MACRO_ETFS + SECTOR_ETFS + MEGA_CAPS

# Tickers where the Dealer Positioning agent emits a real opinion.
# Everywhere else it abstains (free OI data too sparse to be trustworthy).
DEALER_SIGNAL_TICKERS: set[str] = {"SPY", "QQQ", "IWM"}

# Mega-caps have quarterly earnings. Hard-block new entries within N trading
# days of earnings until the Event Risk agent (Phase 8) provides finer control.
EARNINGS_BLACKOUT_DAYS: int = 2
EARNINGS_BLACKOUT_TICKERS: set[str] = set(MEGA_CAPS)


def is_dealer_signal_ticker(ticker: str) -> bool:
    return ticker.upper() in DEALER_SIGNAL_TICKERS


def requires_earnings_blackout(ticker: str) -> bool:
    return ticker.upper() in EARNINGS_BLACKOUT_TICKERS


def watchlist_summary() -> dict:
    return {
        "total": len(WATCHLIST),
        "indices": INDICES,
        "macro": MACRO_ETFS,
        "sectors": SECTOR_ETFS,
        "mega_caps": MEGA_CAPS,
        "dealer_signal": sorted(DEALER_SIGNAL_TICKERS),
        "earnings_blackout_days": EARNINGS_BLACKOUT_DAYS,
    }