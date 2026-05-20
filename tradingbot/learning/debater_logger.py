"""Debater opinion tracking and logging — structured, per-debater analytics.

Every opinion is logged to JSON with:
  - Timestamp, ticker, debater name
  - Direction, conviction, signals used
  - Later (on trade resolution): actual direction, hit/loss, pnl attribution

Used for:
  1. Shadow-mode debugging (compare debater opinions across phases)
  2. Performance analytics (which debaters hit most in which regimes)
  3. Bandit training (per-debater, per-regime Thompson posteriors)
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from tradingbot.core.config import tradingbot_home
from tradingbot.debaters.base import DebaterOpinion


logger = logging.getLogger(__name__)


@dataclass
class DebaterLog:
    """Single debater opinion logged to disk."""
    ts: str                    # ISO 8601
    ticker: str
    debater_name: str
    direction: str             # BULLISH, BEARISH, NEUTRAL
    conviction: float
    reasoning: str
    signals_used: dict[str, Any]
    trade_id: str | None = None  # filled in later on trade creation
    outcome_ts: str | None = None  # filled in later on resolution
    realized_direction: str | None = None
    pnl: float | None = None
    hit: bool | None = None


def debater_log_path() -> Path:
    """Path to debater opinion log."""
    base = tradingbot_home() / "debater_logs"
    base.mkdir(parents=True, exist_ok=True)
    return base / "opinions.jsonl"


def log_opinion(opinion: DebaterOpinion, ticker: str, trade_id: str | None = None) -> None:
    """Append a debater opinion to the log."""
    log_entry = DebaterLog(
        ts=datetime.now(tz=None).isoformat(),
        ticker=ticker,
        debater_name=opinion.debater_name,
        direction=opinion.direction.value,
        conviction=opinion.conviction,
        reasoning=opinion.reasoning,
        signals_used=opinion.signals_used,
        trade_id=trade_id,
    )
    _append_jsonl(debater_log_path(), asdict(log_entry))


def log_outcome(trade_id: str, ticker: str, realized_direction: str, pnl: float, hit: bool) -> None:
    """Append outcome to all debater opinions in this trade."""
    # Read the log, find all opinions for this trade_id, update them
    path = debater_log_path()
    if not path.exists():
        return

    entries = []
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("trade_id") == trade_id:
                entry["outcome_ts"] = datetime.now(tz=None).isoformat()
                entry["realized_direction"] = realized_direction
                entry["pnl"] = pnl
                entry["hit"] = hit
            entries.append(entry)

    # Write back
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _append_jsonl(path: Path, obj: dict) -> None:
    """Append one JSON object (no dict serialization needed) to a JSONL file."""
    with open(path, "a") as f:
        f.write(json.dumps(obj) + "\n")


def debater_stats(regime: int | None = None, days: int | None = None) -> dict[str, Any]:
    """Compute per-debater hit rates, conviction stats.

    Args:
        regime: filter to a specific regime bucket (0..7)
        days: filter to recent N days

    Returns:
        dict with per-debater analytics
    """
    path = debater_log_path()
    if not path.exists():
        return {}

    debaters = {}
    cutoff = None
    if days:
        cutoff = datetime.now(tz=None).timestamp() - (days * 86400)

    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)

            # Filter by timestamp
            if cutoff:
                ts = datetime.fromisoformat(entry["ts"]).timestamp()
                if ts < cutoff:
                    continue

            debater = entry["debater_name"]
            if debater not in debaters:
                debaters[debater] = {
                    "count": 0,
                    "hits": 0,
                    "conviction_sum": 0.0,
                    "avg_conviction": 0.0,
                }

            debaters[debater]["count"] += 1
            debaters[debater]["conviction_sum"] += entry["conviction"]

            if entry.get("hit") is True:
                debaters[debater]["hits"] += 1

    # Compute averages
    for debater in debaters:
        if debaters[debater]["count"] > 0:
            debaters[debater]["avg_conviction"] = (
                debaters[debater]["conviction_sum"] / debaters[debater]["count"]
            )
            debaters[debater]["hit_rate"] = (
                debaters[debater]["hits"] / debaters[debater]["count"]
            )

    return debaters