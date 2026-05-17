"""Gamma Exposure (GEX) computation for dealer positioning intelligence.

Phase D4: Extended GEXResult with call_wall, put_wall, and max_pain.
These are computed from the same options chain data already used for GEX
and require no additional data source.

    call_wall:  highest open-interest call strike — acts as magnetic resistance
    put_wall:   highest open-interest put strike  — acts as magnetic support
    max_pain:   strike where total aggregate option value is minimised
                (the price at which the most options expire worthless)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class GEXResult:
    """Gamma exposure analysis result (Phase D4: extended with walls + max pain)."""
    net_gex: float              # aggregate dealer gamma exposure ($)
    gamma_flip: float | None    # price where GEX changes sign
    is_positive_gex: bool       # True = dealers long gamma (dampen moves)
    price_vs_flip: str          # "above" | "below" | "unknown"
    strikes_analyzed: int       # number of strikes with data
    # Phase D4 additions (None when chain data insufficient)
    call_wall: float | None = None   # strike with highest call OI (resistance)
    put_wall: float | None = None    # strike with highest put OI (support)
    max_pain: float | None = None    # strike minimising total option value


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

    # --- Phase D4: call wall, put wall, max pain ---
    call_wall = _compute_call_wall(calls)
    put_wall = _compute_put_wall(puts)
    max_pain = _compute_max_pain(calls, puts)

    return GEXResult(
        net_gex=net_gex,
        gamma_flip=gamma_flip,
        is_positive_gex=net_gex > 0,
        price_vs_flip=price_vs_flip,
        strikes_analyzed=strikes_analyzed,
        call_wall=call_wall,
        put_wall=put_wall,
        max_pain=max_pain,
    )


def _compute_call_wall(calls: list[dict]) -> float | None:
    """Return the call strike with the highest open interest (magnetic resistance).

    The call wall is the single strike where the most call contracts are open.
    Dealers are short gamma above this level, creating a ceiling effect.
    """
    if not calls:
        return None
    best = max(calls, key=lambda c: c.get("open_interest", 0))
    strike = best.get("strike")
    return float(strike) if strike else None


def _compute_put_wall(puts: list[dict]) -> float | None:
    """Return the put strike with the highest open interest (magnetic support).

    The put wall is the single strike where the most put contracts are open.
    Dealers are short gamma below this level, creating a floor effect.
    """
    if not puts:
        return None
    best = max(puts, key=lambda p: p.get("open_interest", 0))
    strike = best.get("strike")
    return float(strike) if strike else None


def _compute_max_pain(calls: list[dict], puts: list[dict]) -> float | None:
    """Return the max pain strike — the price where aggregate option value is minimised.

    At max pain, the most options (by dollar value) expire worthless, which is
    the best outcome for net option sellers (i.e., market makers collectively).
    The computation: for each candidate strike S, compute the total value of
    all calls with strike < S and all puts with strike > S that are in-the-money,
    then sum. The strike minimising this total is max pain.

    This is a first-order approximation (uses OI × intrinsic value; ignores
    time value and greeks). Sufficient for a price-magnet estimate.
    """
    if not calls or not puts:
        return None

    # Build strike universe from both sides
    all_strikes: set[float] = set()
    for c in calls:
        if c.get("strike"):
            all_strikes.add(float(c["strike"]))
    for p in puts:
        if p.get("strike"):
            all_strikes.add(float(p["strike"]))

    if not all_strikes:
        return None

    # Index OI by strike for fast lookup
    call_oi: dict[float, int] = {}
    for c in calls:
        s = c.get("strike")
        if s:
            call_oi[float(s)] = call_oi.get(float(s), 0) + int(c.get("open_interest", 0))

    put_oi: dict[float, int] = {}
    for p in puts:
        s = p.get("strike")
        if s:
            put_oi[float(s)] = put_oi.get(float(s), 0) + int(p.get("open_interest", 0))

    # For each candidate expiry price (= each strike), compute total pain
    best_strike = None
    best_pain = float("inf")

    for candidate in sorted(all_strikes):
        pain = 0.0
        # Calls that are ITM (strike < candidate): each contract worth (candidate - strike)
        for strike, oi in call_oi.items():
            if strike < candidate:
                pain += (candidate - strike) * oi
        # Puts that are ITM (strike > candidate): each contract worth (strike - candidate)
        for strike, oi in put_oi.items():
            if strike > candidate:
                pain += (strike - candidate) * oi

        if pain < best_pain:
            best_pain = pain
            best_strike = candidate

    return float(best_strike) if best_strike is not None else None