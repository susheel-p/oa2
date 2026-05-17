"""Gamma Exposure (GEX) computation for dealer positioning intelligence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class GEXResult:
    """Gamma exposure analysis result."""
    net_gex: float          # aggregate dealer gamma exposure ($)
    gamma_flip: float | None  # price where GEX changes sign
    is_positive_gex: bool   # True = dealers long gamma (dampen moves)
    price_vs_flip: str      # "above" | "below" | "unknown"
    strikes_analyzed: int   # number of strikes with data


def compute_gex(chain: dict[str, Any], spot_price: float) -> GEXResult:
    """Compute Gamma Exposure (GEX) from options chain.

    Net GEX = Σ(call_gamma × call_OI - put_gamma × put_OI) × spot_price² × 100

    Positive GEX: dealers long gamma (range-bound, dampen moves)
    Negative GEX: dealers short gamma (momentum-driven, amplify moves)

    Args:
        chain: dict with keys "calls" and "puts", each a list of
               {"strike": float, "gamma": float, "open_interest": int}
        spot_price: current price for gamma flip detection

    Returns:
        GEXResult with net exposure, flip level, and position relative to flip
    """
    calls = chain.get("calls", [])
    puts = chain.get("puts", [])

    if not calls or not puts:
        return GEXResult(
            net_gex=0.0,
            gamma_flip=None,
            is_positive_gex=False,
            price_vs_flip="unknown",
            strikes_analyzed=0,
        )

    # Compute net GEX per strike
    cumulative_gex = 0.0
    gex_by_strike = {}
    strikes_analyzed = 0

    for strike_data in calls:
        strike = strike_data.get("strike", 0.0)
        gamma = strike_data.get("gamma", 0.0)
        oi = strike_data.get("open_interest", 0)

        if strike and gamma and oi:
            gex = gamma * oi * (spot_price ** 2) * 100
            gex_by_strike[strike] = gex_by_strike.get(strike, 0.0) + gex
            strikes_analyzed += 1

    for strike_data in puts:
        strike = strike_data.get("strike", 0.0)
        gamma = strike_data.get("gamma", 0.0)
        oi = strike_data.get("open_interest", 0)

        if strike and gamma and oi:
            gex = -(gamma * oi * (spot_price ** 2) * 100)  # puts subtract from net GEX
            gex_by_strike[strike] = gex_by_strike.get(strike, 0.0) + gex
            strikes_analyzed += 1

    # Compute net GEX
    net_gex = sum(gex_by_strike.values())

    # Find gamma flip level (where GEX changes sign)
    sorted_strikes = sorted(gex_by_strike.keys())
    gamma_flip = None
    for i in range(len(sorted_strikes) - 1):
        strike1 = sorted_strikes[i]
        strike2 = sorted_strikes[i + 1]
        gex1 = gex_by_strike[strike1]
        gex2 = gex_by_strike[strike2]

        if gex1 * gex2 < 0:  # sign change detected
            # Linear interpolation for flip level
            gamma_flip = strike1 + (strike2 - strike1) * abs(gex1) / (abs(gex1) + abs(gex2))
            break

    # Determine position relative to gamma flip
    price_vs_flip = "unknown"
    if gamma_flip is not None:
        if spot_price > gamma_flip:
            price_vs_flip = "above"
        else:
            price_vs_flip = "below"

    return GEXResult(
        net_gex=net_gex,
        gamma_flip=gamma_flip,
        is_positive_gex=net_gex > 0,
        price_vs_flip=price_vs_flip,
        strikes_analyzed=strikes_analyzed,
    )