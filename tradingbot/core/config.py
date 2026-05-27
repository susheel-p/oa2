"""oa2 runtime configuration — env loading, paths, log setup."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def tradingbot_home() -> Path:
    """Root for runtime data (logs, journal, bandit store, shadow logs)."""
    base = os.getenv("TRADINGBOT_HOME")
    if base:
        path = Path(base)
    else:
        path = Path.home() / ".tradingbot"
    path.mkdir(parents=True, exist_ok=True)
    return path


def shadow_log_dir() -> Path:
    p = tradingbot_home() / "shadow_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def scan_log_dir() -> Path:
    p = tradingbot_home() / "scan_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def journal_dir() -> Path:
    p = tradingbot_home() / "journal"
    p.mkdir(parents=True, exist_ok=True)
    return p


def bandit_store_path() -> Path:
    return tradingbot_home() / "bandit_store.json"


def covariance_store_path() -> Path:
    return tradingbot_home() / "debater_covariance.json"


# Broker selection (moomoo only for v2; placeholder for future)
BROKER: str = os.getenv("BROKER", "moomoo").lower()

# Log level
LOG_LEVEL: str = os.getenv("OA2_LOG_LEVEL", "INFO").upper()

# Exit engine configuration
TRAILING_STOP_PCT: float = float(os.getenv("TRAILING_STOP_PCT", "0.10"))

# Stop loss: fraction of max_loss before closing (0.75 = close at 75% of max loss)
STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "0.75"))

# DTE emergency thresholds — structure-aware
# Short-premium structures (IC, verticals, calendars) exit earlier to avoid assignment
DTE_EMERGENCY_SHORT: int = int(os.getenv("DTE_EMERGENCY_SHORT", "7"))
# Long structures (long calls/puts) exit at 5 DTE
DTE_EMERGENCY_LONG: int = int(os.getenv("DTE_EMERGENCY_LONG", "5"))

# Friday sweep: close positions with DTE <= this on Friday afternoons
FRIDAY_SWEEP_DTE: int = int(os.getenv("FRIDAY_SWEEP_DTE", "21"))
FRIDAY_SWEEP_HOUR: int = int(os.getenv("FRIDAY_SWEEP_HOUR", "14"))

# Profit target: fraction of max_profit to close at
# Short premium (IC, verticals): close at 50% of credit received
PROFIT_TARGET_SHORT: float = float(os.getenv("PROFIT_TARGET_SHORT", "0.50"))
# Long structures: close at 100% gain (double the premium paid)
PROFIT_TARGET_LONG: float = float(os.getenv("PROFIT_TARGET_LONG", "1.00"))