"""Enhanced logging for oa2 — plain English with configurable detail level.

Usage:
    logger = PipelineLogger(detail_logging=True, ticker="SPY")
    logger.log_stage("L0", "Fetching market data...")
    logger.log_detail("Moomoo snapshot returned", {"bid": 450.25, "ask": 450.26})
    logger.log_signal("Flow debater", "abstained", {"data_quality": "absent"})

Log files:
    - Daily rolling logs: ~/.tradingbot/logs/pipeline.YYYY-MM-DD.log
    - Archived logs (30+ days): ~/.tradingbot/logs/archive/
"""

import logging
import logging.handlers
import os
from typing import Any
from datetime import datetime
from pathlib import Path

from tradingbot.core.config import tradingbot_home


def _setup_rotating_file_handler(logger_obj: logging.Logger) -> None:
    """Configure daily rolling file handler with archiving."""
    logs_dir = tradingbot_home() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Daily rotating file handler (keeps 30 days)
    log_file = logs_dir / "pipeline.log"
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",  # Rotate at midnight
        interval=1,       # Every day
        backupCount=30,   # Keep 30 days of logs
        utc=False
    )

    # Use ISO date format in filename (e.g., pipeline.2026-05-22.log)
    file_handler.namer = lambda name: _archive_namer(name, logs_dir)
    file_handler.rotator = _archive_rotator

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    logger_obj.addHandler(file_handler)


def _archive_namer(default_name: str, logs_dir: Path) -> str:
    """Convert log filename to include date and place old logs in archive."""
    # default_name is like "pipeline.log.2026-05-21"
    base = Path(default_name)
    date_part = base.name.split(".")[-1]  # Extract date
    archive_dir = logs_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    return str(archive_dir / f"pipeline.{date_part}.log")


def _archive_rotator(source: str, dest: str) -> None:
    """Move rotated log to archive directory."""
    Path(source).rename(dest)


class PipelineLogger:
    """Plain-English pipeline logger with configurable detail level."""

    _initialized = False  # Class-level flag to prevent duplicate handler setup

    def __init__(self, detail_logging: bool = False, ticker: str = ""):
        self.detail_logging = detail_logging
        self.ticker = ticker
        self.logger = logging.getLogger("tradingbot.pipeline")

        # Configure handlers only once per process
        if not PipelineLogger._initialized:
            # Clear any existing handlers first
            self.logger.handlers.clear()

            # Console handler (stdout)
            console_handler = logging.StreamHandler()
            console_formatter = logging.Formatter(
                "[%(asctime)s] %(levelname)s | %(message)s",
                datefmt="%H:%M:%S"
            )
            console_handler.setFormatter(console_formatter)
            self.logger.addHandler(console_handler)

            # File handler with daily rotation
            _setup_rotating_file_handler(self.logger)

            self.logger.setLevel(logging.INFO)
            PipelineLogger._initialized = True

    def _format_msg(self, msg: str, stage: str = "") -> str:
        """Format message with ticker and stage context."""
        prefix = f"{self.ticker}" if self.ticker else ""
        if stage:
            prefix = f"{prefix} {stage}".strip()
        if prefix:
            return f"[{prefix}] {msg}"
        return msg

    def log_stage(self, stage_id: str, description: str):
        """Log a pipeline stage entry."""
        msg = self._format_msg(f"→ {description}", stage_id)
        self.logger.info(msg)

    def log_detail(self, title: str, data: dict[str, Any] | None = None, stage: str = ""):
        """Log detailed info (only when detail_logging=True)."""
        if not self.detail_logging:
            return

        if data:
            items = [f"{k}={v}" for k, v in data.items()]
            msg = self._format_msg(f"  {title}: {', '.join(items)}", stage)
        else:
            msg = self._format_msg(f"  {title}", stage)
        self.logger.debug(msg)

    def log_signal(self, debater: str, status: str, data: dict[str, Any] | None = None):
        """Log a debater signal (e.g., 'Flow debater voted BULLISH with conviction 0.65')."""
        if data:
            items = [f"{k}={v}" for k, v in data.items()]
            msg = f"  {debater}: {status} ({', '.join(items)})"
        else:
            msg = f"  {debater}: {status}"
        self.logger.info(self._format_msg(msg))

    def log_consensus(self, direction: str, p_bull: float, n_eff: float, weights: dict[str, float] | None = None):
        """Log consensus result."""
        msg = f"Consensus: {direction} @ p_bull={p_bull:.3f}, n_eff={n_eff:.2f}"
        self.logger.info(self._format_msg(msg))

        if self.detail_logging and weights:
            sorted_weights = sorted(weights.items(), key=lambda x: x[1], reverse=True)
            weight_str = ", ".join([f"{k}={v:.3f}" for k, v in sorted_weights])
            self.logger.debug(self._format_msg(f"  Debater weights: {weight_str}"))

    def log_sizing(self, status: str, reason: str | None = None, data: dict[str, Any] | None = None):
        """Log sizing decision."""
        if reason:
            msg = f"Sizing: {status} — {reason}"
        else:
            msg = f"Sizing: {status}"

        if data:
            items = [f"{k}={v}" for k, v in data.items()]
            msg += f" ({', '.join(items)})"

        self.logger.info(self._format_msg(msg))

    def log_warning(self, title: str, details: str | None = None):
        """Log a warning or anomaly."""
        if details:
            msg = f"⚠ {title}: {details}"
        else:
            msg = f"⚠ {title}"
        self.logger.warning(self._format_msg(msg))

    def log_error(self, title: str, details: str | None = None):
        """Log an error."""
        if details:
            msg = f"✗ {title}: {details}"
        else:
            msg = f"✗ {title}"
        self.logger.error(self._format_msg(msg))


def get_detail_logging_enabled() -> bool:
    """Check if detail logging is enabled via OA2_DETAIL_LOGGING env var."""
    return os.getenv("OA2_DETAIL_LOGGING", "").lower() in ("true", "1", "yes")


def cleanup_old_logs(days: int = 60) -> int:
    """Delete archived logs older than N days. Returns count deleted."""
    archive_dir = tradingbot_home() / "logs" / "archive"
    if not archive_dir.exists():
        return 0

    cutoff_ts = datetime.now().timestamp() - (days * 86400)
    deleted = 0

    for log_file in archive_dir.glob("pipeline.*.log"):
        try:
            mtime = log_file.stat().st_mtime
            if mtime < cutoff_ts:
                log_file.unlink()
                deleted += 1
        except (OSError, AttributeError):
            pass

    return deleted
