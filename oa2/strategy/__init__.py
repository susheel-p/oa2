"""Strategy module — selects option structures + applies quality gates."""

from oa2.strategy.quality_gates import (
    check_quality_gates,
    ticker_conviction_multiplier,
)
from oa2.strategy.structure_picker import StructurePick, pick_structure

__all__ = [
    "StructurePick",
    "pick_structure",
    "check_quality_gates",
    "ticker_conviction_multiplier",
]
