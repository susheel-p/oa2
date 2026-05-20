"""Strategy module — selects option structures + applies quality gates."""

from tradingbot.strategy.quality_gates import (
    check_quality_gates,
    ticker_conviction_multiplier,
)
from tradingbot.strategy.structure_picker import StructurePick, pick_structure

__all__ = [
    "StructurePick",
    "pick_structure",
    "check_quality_gates",
    "ticker_conviction_multiplier",
]
