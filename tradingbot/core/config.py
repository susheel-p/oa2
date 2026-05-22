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

# Exit engine: trailing stop configuration
TRAILING_STOP_PCT: float = float(os.getenv("TRAILING_STOP_PCT", "0.10"))