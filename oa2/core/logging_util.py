"""Enhanced logging for oa2 — plain English with configurable detail level.

Usage:
    logger = PipelineLogger(detail_logging=True, ticker="SPY")
    logger.log_stage("L0", "Fetching market data...")
    logger.log_detail("Moomoo snapshot returned", {"bid": 450.25, "ask": 450.26})
    logger.log_signal("Flow debater", "abstained", {"data_quality": "absent"})
"""

import logging
import os
from typing import Any
from datetime import datetime


class PipelineLogger:
    """Plain-English pipeline logger with configurable detail level."""

    def __init__(self, detail_logging: bool = False, ticker: str = ""):
        self.detail_logging = detail_logging
        self.ticker = ticker
        self.logger = logging.getLogger("oa2.pipeline")

        # Configure handler if not already done
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "[%(asctime)s] %(levelname)s | %(message)s",
                datefmt="%H:%M:%S"
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

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
