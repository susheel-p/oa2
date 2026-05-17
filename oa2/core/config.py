"""oa2 runtime configuration — env loading, paths, log setup."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def oa2_home() -> Path:
    """Root for runtime data (logs, journal, bandit store, shadow logs)."""
    base = os.getenv("OA2_HOME")
    if base:
        path = Path(base)
    else:
        path = Path.home() / ".oa2"
    path.mkdir(parents=True, exist_ok=True)
    return path


def shadow_log_dir() -> Path:
    p = oa2_home() / "shadow_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def scan_log_dir() -> Path:
    p = oa2_home() / "scan_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def journal_dir() -> Path:
    p = oa2_home() / "journal"
    p.mkdir(parents=True, exist_ok=True)
    return p


def bandit_store_path() -> Path:
    return oa2_home() / "bandit_store.json"


def covariance_store_path() -> Path:
    return oa2_home() / "debater_covariance.json"


# Broker selection (moomoo only for v2; placeholder for future)
BROKER: str = os.getenv("BROKER", "moomoo").lower()

# Log level
LOG_LEVEL: str = os.getenv("OA2_LOG_LEVEL", "INFO").upper()