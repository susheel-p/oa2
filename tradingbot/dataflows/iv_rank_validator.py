"""IV Rank validation and normalization — ensures 0-100 scale with quality checks."""

import logging
import warnings
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


def validate_and_normalize_iv_rank(
    iv_rank: float,
    context_source: str = "unknown",
    ticker: str = "UNKNOWN"
) -> float:
    """Validate and normalize IV rank to 0-100 scale.

    Args:
        iv_rank: Raw IV rank value (may be 0-1, 0-100, or invalid)
        context_source: Where this value came from (yfinance, moomoo, etc.)
        ticker: Ticker symbol for logging

    Returns:
        IV rank normalized to 0-100 scale, clamped [0, 100]

    Raises:
        ValueError: If IV rank cannot be reasonably interpreted
    """

    if iv_rank is None:
        logger.warning(f"[{ticker}] IV rank is None; defaulting to 50.0 (median)")
        return 50.0

    if not isinstance(iv_rank, (int, float)):
        logger.warning(f"[{ticker}] IV rank is not numeric ({type(iv_rank)}); defaulting to 50.0")
        return 50.0

    # Detect and auto-correct 0-1 scale (normalized) values
    if 0.0 <= iv_rank <= 1.0:
        # This is already 0-1 scale; convert to 0-100
        iv_rank_normalized = iv_rank * 100.0
        logger.debug(f"[{ticker}] Auto-corrected IV rank from 0-1 scale ({iv_rank:.4f}) to 0-100 scale ({iv_rank_normalized:.0f})")
        return iv_rank_normalized

    # Detect impossible values (> 100 or < 0) — likely 0-1 scale that wasn't converted
    if 1.0 < iv_rank <= 10.0:
        # Likely 0-10 scale (rare) or 0-1 scale × 10 (possible error)
        iv_rank_corrected = iv_rank * 10.0
        if iv_rank_corrected > 100:
            logger.warning(f"[{ticker}] IV rank {iv_rank:.2f} looks like 0-10 scale; converted to {iv_rank_corrected:.0f}. If wrong, please report.")
            warnings.warn(f"IV rank interpretation ambiguous for {ticker}: {iv_rank}")
            return min(100.0, iv_rank_corrected)

    # Detect large values (> 100) — definitely wrong
    if iv_rank > 100.0:
        # Check if it looks like a 0-1 scale value multiplied by 100 twice (e.g., 4.86 * 100 = 486)
        if iv_rank > 500:
            # Likely: 0-1 scale (0.486) was multiplied by 100 → 48.6, then × 10 again → 486
            iv_rank_corrected = iv_rank / 10.0
            logger.warning(
                f"[{ticker}] IV rank {iv_rank:.0f} is > 500; likely 0-1 scale × 10. "
                f"Correcting to {iv_rank_corrected:.0f}%"
            )
            warnings.warn(f"IV rank {iv_rank} on {ticker} — potential 0-1 scale error, converted to {iv_rank_corrected}")
            return min(100.0, iv_rank_corrected)
        else:
            # Clamp to 100
            logger.warning(
                f"[{ticker}] IV rank {iv_rank:.0f}% exceeds 100; clamping to 100.0 "
                f"(source: {context_source})"
            )
            warnings.warn(f"IV rank {iv_rank}% on {ticker} — clamped to 100")
            return 100.0

    if iv_rank < 0.0:
        logger.warning(f"[{ticker}] IV rank {iv_rank:.2f} is negative; clamping to 0.0")
        warnings.warn(f"IV rank {iv_rank} on {ticker} — negative, clamped to 0")
        return 0.0

    # Valid 0-100 range
    return float(iv_rank)


def validate_market_context(context: Dict[str, Any], ticker: str = "UNKNOWN") -> Dict[str, Any]:
    """Validate all IV-related fields in market context.

    Args:
        context: Market context dict with iv_rank, iv_percentile, etc.
        ticker: Ticker symbol for logging

    Returns:
        Updated context with validated IV rank values
    """

    # Validate iv_rank
    if "iv_rank" in context:
        context["iv_rank"] = validate_and_normalize_iv_rank(
            context["iv_rank"],
            context_source="market_context",
            ticker=ticker
        )
    else:
        context["iv_rank"] = 50.0
        logger.debug(f"[{ticker}] Missing iv_rank; defaulting to 50.0")

    # Validate iv_percentile (should also be 0-100)
    if "iv_percentile" in context:
        iv_percentile = context["iv_percentile"]
        if isinstance(iv_percentile, float) and 0.0 <= iv_percentile <= 1.0:
            # Auto-correct if it's 0-1 scale
            context["iv_percentile"] = iv_percentile * 100.0
            logger.debug(f"[{ticker}] Auto-corrected iv_percentile from 0-1 to 0-100")
        elif isinstance(iv_percentile, (int, float)):
            context["iv_percentile"] = max(0.0, min(100.0, iv_percentile))
    else:
        context["iv_percentile"] = 50.0
        logger.debug(f"[{ticker}] Missing iv_percentile; defaulting to 50.0")

    # Validate realized_vol (should be % in 0-100+ range typically)
    if "realized_vol" in context:
        rv = context["realized_vol"]
        if isinstance(rv, float) and 0.0 <= rv <= 1.0:
            # Likely 0-1 scale; convert to percentage
            context["realized_vol"] = rv * 100.0
            logger.debug(f"[{ticker}] Auto-corrected realized_vol from 0-1 to 0-100 scale")

    return context
