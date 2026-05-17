"""Expiration-aware flow classification — Phase E3.

Smart money targeting is expiration-specific:
    Front-week sweeps (0-7 DTE)  → high urgency, directional bets
    Near-term (8-21 DTE)         → near-term positioning
    Mid-term (22-45 DTE)         → strategic positioning, premium selling
    Longer-dated (46+ DTE)       → LEAPS, hedges, long-duration plays

The same PCR signal has different meanings depending on which DTE bucket
it comes from. A sweep in 3-DTE calls is very different from a 45-DTE
call accumulation — one is an event bet, the other is directional
positioning.

Usage:
    profile = classify_expiry_flow(chain_by_expiry, spot_price=450.0)
    if profile.urgency_signal == "high":
        # Something big is happening in the front week
        ...
    print(profile.dominant_bucket)   # "near_term", "mid_term", etc.
    print(profile.front_week_pcr)    # 0.0 if no front-week activity
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ExpiryBucket(Enum):
    """DTE-based expiration bucket."""
    FRONT_WEEK = "front_week"    # 0-7 DTE
    NEAR_TERM = "near_term"      # 8-21 DTE
    MID_TERM = "mid_term"        # 22-45 DTE
    LONGER = "longer"            # 46+ DTE
    UNKNOWN = "unknown"          # could not compute DTE


def _dte(expiry_str: str, as_of: datetime.date | None = None) -> int:
    """Compute DTE from expiry string (YYYY-MM-DD) to as_of date."""
    try:
        exp = datetime.date.fromisoformat(expiry_str)
        base = as_of or datetime.date.today()
        return max((exp - base).days, 0)
    except (ValueError, TypeError):
        return -1


def _bucket_from_dte(dte: int) -> ExpiryBucket:
    if dte < 0:
        return ExpiryBucket.UNKNOWN
    if dte <= 7:
        return ExpiryBucket.FRONT_WEEK
    if dte <= 21:
        return ExpiryBucket.NEAR_TERM
    if dte <= 45:
        return ExpiryBucket.MID_TERM
    return ExpiryBucket.LONGER


@dataclass
class ExpiryFlowProfile:
    """Per-bucket call/put volume and derived urgency signal.

    All volume figures are raw option contract counts.
    PCR fields are 0.0 when no activity in that bucket.
    """
    # Raw volumes by bucket
    front_week_call_vol: int = 0
    front_week_put_vol: int = 0
    near_term_call_vol: int = 0
    near_term_put_vol: int = 0
    mid_term_call_vol: int = 0
    mid_term_put_vol: int = 0
    longer_call_vol: int = 0
    longer_put_vol: int = 0

    # Derived signals
    dominant_bucket: str = ExpiryBucket.UNKNOWN.value
    urgency_signal: str = "low"    # "high" | "medium" | "low"
    front_week_pcr: float = 0.0    # put/call ratio for front-week bucket
    near_term_pcr: float = 0.0
    mid_term_pcr: float = 0.0
    longer_pcr: float = 0.0

    # Metadata
    expirations_analyzed: int = 0
    total_call_vol: int = 0
    total_put_vol: int = 0

    @property
    def front_week_dominant(self) -> bool:
        """True when front-week volume is > 50% of all activity."""
        total = self.total_call_vol + self.total_put_vol
        fw = self.front_week_call_vol + self.front_week_put_vol
        return total > 0 and fw / total > 0.50

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "dominant_bucket": self.dominant_bucket,
            "urgency_signal": self.urgency_signal,
            "front_week_pcr": self.front_week_pcr,
            "near_term_pcr": self.near_term_pcr,
            "mid_term_pcr": self.mid_term_pcr,
            "total_call_vol": self.total_call_vol,
            "total_put_vol": self.total_put_vol,
            "front_week_dominant": self.front_week_dominant,
        }


def classify_expiry_flow(
    chain_by_expiry: dict[str, dict[str, Any]],
    as_of: datetime.date | None = None,
) -> ExpiryFlowProfile:
    """Classify options flow by expiration bucket.

    Args:
        chain_by_expiry: {expiry_str: {"calls": [...], "puts": [...]}}
            where each entry is a list of contracts with at least
            {"volume": int} (and optionally "open_interest": int).
        as_of: date to compute DTE from. Defaults to today.

    Returns:
        ExpiryFlowProfile with per-bucket volumes and urgency signals.

    Example input:
        {
            "2025-05-16": {"calls": [{"volume": 500}, ...], "puts": [...]},
            "2025-05-23": {"calls": [...], "puts": [...]},
        }
    """
    profile = ExpiryFlowProfile()
    bucket_vols: dict[ExpiryBucket, dict[str, int]] = {
        b: {"call": 0, "put": 0} for b in ExpiryBucket
    }

    for expiry_str, sides in chain_by_expiry.items():
        dte = _dte(expiry_str, as_of)
        bucket = _bucket_from_dte(dte)
        profile.expirations_analyzed += 1

        calls = sides.get("calls", [])
        puts = sides.get("puts", [])

        call_vol = sum(int(c.get("volume", 0) or 0) for c in calls)
        put_vol = sum(int(p.get("volume", 0) or 0) for p in puts)

        bucket_vols[bucket]["call"] += call_vol
        bucket_vols[bucket]["put"] += put_vol

    # Populate profile from bucket totals
    fw = bucket_vols[ExpiryBucket.FRONT_WEEK]
    nt = bucket_vols[ExpiryBucket.NEAR_TERM]
    mt = bucket_vols[ExpiryBucket.MID_TERM]
    lg = bucket_vols[ExpiryBucket.LONGER]

    profile.front_week_call_vol = fw["call"]
    profile.front_week_put_vol = fw["put"]
    profile.near_term_call_vol = nt["call"]
    profile.near_term_put_vol = nt["put"]
    profile.mid_term_call_vol = mt["call"]
    profile.mid_term_put_vol = mt["put"]
    profile.longer_call_vol = lg["call"]
    profile.longer_put_vol = lg["put"]

    profile.total_call_vol = sum(d["call"] for d in bucket_vols.values())
    profile.total_put_vol = sum(d["put"] for d in bucket_vols.values())

    # PCR per bucket
    def _pcr(b: dict[str, int]) -> float:
        return round(b["put"] / b["call"], 3) if b["call"] > 0 else 0.0

    profile.front_week_pcr = _pcr(fw)
    profile.near_term_pcr = _pcr(nt)
    profile.mid_term_pcr = _pcr(mt)
    profile.longer_pcr = _pcr(lg)

    # Dominant bucket: which has the most total volume
    vols = {
        ExpiryBucket.FRONT_WEEK: fw["call"] + fw["put"],
        ExpiryBucket.NEAR_TERM: nt["call"] + nt["put"],
        ExpiryBucket.MID_TERM: mt["call"] + mt["put"],
        ExpiryBucket.LONGER: lg["call"] + lg["put"],
    }
    if any(v > 0 for v in vols.values()):
        dominant = max(vols, key=vols.get)
        profile.dominant_bucket = dominant.value
    else:
        profile.dominant_bucket = ExpiryBucket.UNKNOWN.value

    # Urgency: front-week > 50% of total activity → high urgency
    total_vol = profile.total_call_vol + profile.total_put_vol
    fw_vol = fw["call"] + fw["put"]
    if total_vol > 0:
        fw_pct = fw_vol / total_vol
        if fw_pct > 0.50:
            profile.urgency_signal = "high"
        elif fw_pct > 0.25:
            profile.urgency_signal = "medium"
        else:
            profile.urgency_signal = "low"

    return profile


def flow_context_from_profile(profile: ExpiryFlowProfile) -> dict[str, Any]:
    """Convert an ExpiryFlowProfile into a context dict for the pipeline.

    Merges with the existing context dict (alongside flow_data).
    """
    return {
        "expiry_flow": {
            "dominant_bucket": profile.dominant_bucket,
            "urgency_signal": profile.urgency_signal,
            "front_week_pcr": profile.front_week_pcr,
            "near_term_pcr": profile.near_term_pcr,
            "mid_term_pcr": profile.mid_term_pcr,
            "longer_pcr": profile.longer_pcr,
            "front_week_dominant": profile.front_week_dominant,
            "total_call_vol": profile.total_call_vol,
            "total_put_vol": profile.total_put_vol,
        }
    }
