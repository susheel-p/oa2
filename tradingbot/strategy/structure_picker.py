"""Structure picker — pick option spread strikes from a live chain.

For a given directional consensus (BULLISH / BEARISH) and conviction
(p_bull), find a debit vertical spread (or outright long) whose
max_profit / max_loss ratio is Kelly-viable.

Kelly math (binary bet):
    f* = (edge x (odds + 1) - 1) / odds
For Kelly to be positive at win-probability p, need odds > (1-p)/p.
At p=0.55 -> odds > 0.818, at p=0.60 -> odds > 0.667, at p=0.65 -> 0.538.

The picker computes `required_odds` from p_bull and picks the narrowest
spread that satisfies it while staying liquid (bid > 0, OI >= MIN_OI,
spread/mid < MAX_SPREAD_PCT).

V1 supported structures:
    LONG_CALL                  (outright)
    LONG_PUT                   (outright)
    VERTICAL_CALL_SPREAD       (debit bull-call)
    VERTICAL_PUT_SPREAD        (debit bear-put)

Chain format (per cache/<TICKER>_<DATE>.json):
    chain = {
        "calls": [{"strike", "bid", "ask", "open_interest", "iv", "delta", ...}, ...],
        "puts":  [...],
        "atm_strike": float,
    }
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any


# -----------------------------------------------------------------------------
# Tunables
# -----------------------------------------------------------------------------

MIN_OPEN_INTEREST = 50          # liquidity floor per leg
MAX_SPREAD_PCT = 0.30           # (ask-bid)/mid threshold; reject wider
SAFETY_MARGIN = 0.05            # add to required_odds (cushion for slippage)
DEFAULT_DTE = 30                # placeholder until expiry per-leg is wired
SPREAD_WIDTHS_PCT = [0.01, 0.02, 0.03, 0.04, 0.05, 0.07, 0.10]  # try in order

# Outright long fallbacks need higher conviction to justify their fat tails
OUTRIGHT_MIN_P_BULL = 0.62


# -----------------------------------------------------------------------------
# Result type
# -----------------------------------------------------------------------------

@dataclass
class StructurePick:
    """Output of the structure picker."""
    structure_type: str
    long_strike: float
    short_strike: float | None
    expiry: str | None
    dte: int
    max_profit: float           # per contract, dollars
    max_loss: float             # per contract, dollars (positive)
    breakeven: float
    odds: float                 # max_profit / max_loss
    delta_per_contract: float
    vega_per_contract: float
    theta_per_contract: float
    debit_per_contract: float   # premium paid
    reason: str
    diagnostics: dict[str, Any] = field(default_factory=dict)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _required_odds(p_bull: float, direction: str) -> float:
    """Min odds needed for Kelly>0 at the given win probability + safety."""
    p_win = p_bull if direction == "BULLISH" else (1.0 - p_bull)
    if p_win <= 0:
        return float("inf")
    return (1.0 - p_win) / p_win + SAFETY_MARGIN


def _is_liquid(opt: dict) -> bool:
    bid = float(opt.get("bid", 0.0) or 0.0)
    ask = float(opt.get("ask", 0.0) or 0.0)
    oi = float(opt.get("open_interest", 0.0) or 0.0)
    if bid <= 0 or ask <= 0 or oi < MIN_OPEN_INTEREST:
        return False
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return False
    return (ask - bid) / mid <= MAX_SPREAD_PCT


def _mid(opt: dict) -> float:
    return (float(opt.get("bid", 0.0)) + float(opt.get("ask", 0.0))) / 2.0


def _approx_delta(opt: dict, spot: float, is_call: bool) -> float:
    """Use chain delta if non-zero; otherwise fall back to moneyness-based estimate."""
    d = float(opt.get("delta", 0.0) or 0.0)
    if abs(d) > 1e-6:
        return d
    # Crude estimate: ATM = 0.5, scaled by (strike vs spot)
    strike = float(opt.get("strike", spot))
    if spot <= 0:
        return 0.5 if is_call else -0.5
    moneyness = (spot - strike) / spot  # >0 = ITM call / OTM put
    base = 0.5 + 1.5 * moneyness
    base = max(0.02, min(0.98, base))
    return base if is_call else -(1.0 - base)


def _find_atm(legs: list[dict], spot: float) -> dict | None:
    """Nearest-strike liquid contract to spot."""
    candidates = [o for o in legs if _is_liquid(o)]
    if not candidates:
        return None
    return min(candidates, key=lambda o: abs(float(o["strike"]) - spot))


def _find_strike_at_offset(
    legs: list[dict],
    target_strike: float,
    require_liquid: bool = True,
) -> dict | None:
    """Closest leg to target_strike that satisfies liquidity."""
    pool = [o for o in legs if (_is_liquid(o) if require_liquid else True)]
    if not pool:
        return None
    return min(pool, key=lambda o: abs(float(o["strike"]) - target_strike))


# -----------------------------------------------------------------------------
# Vertical debit spread picker
# -----------------------------------------------------------------------------

def _pick_debit_vertical(
    legs: list[dict],
    spot: float,
    is_bullish: bool,
    required_odds: float,
    expiry: str | None = None,
    dte: int = DEFAULT_DTE,
) -> StructurePick | None:
    """Build a debit vertical (long lower / short higher for call; mirrored for put).

    Iterates widths from narrowest to widest; returns first viable.
    """
    long_leg = _find_atm(legs, spot)
    if long_leg is None:
        return None

    long_strike = float(long_leg["strike"])
    long_ask = float(long_leg["ask"])

    for width_pct in SPREAD_WIDTHS_PCT:
        width = max(1.0, round(spot * width_pct, 0))
        target_short_strike = long_strike + width if is_bullish else long_strike - width
        short_leg = _find_strike_at_offset(legs, target_short_strike, require_liquid=True)
        if short_leg is None:
            continue
        short_strike = float(short_leg["strike"])
        # Width could differ from target if no exact strike — use real width
        real_width = abs(short_strike - long_strike)
        if real_width <= 0:
            continue
        short_bid = float(short_leg["bid"])

        debit = max(long_ask - short_bid, 0.01)
        # Cap debit at width (can't pay more than max payoff)
        if debit >= real_width:
            continue
        max_profit_pts = real_width - debit
        max_loss_pts = debit
        odds = max_profit_pts / max_loss_pts

        if odds < required_odds:
            continue

        # Multiply by 100 for $ per contract
        max_profit = max_profit_pts * 100.0
        max_loss = max_loss_pts * 100.0

        # Greeks: delta is approx (long delta - short delta), vega/theta similar
        is_call = is_bullish
        delta_long = _approx_delta(long_leg, spot, is_call)
        delta_short = _approx_delta(short_leg, spot, is_call)
        spread_delta = (delta_long - delta_short) * 100.0
        spread_vega = (float(long_leg.get("vega", 0.0)) - float(short_leg.get("vega", 0.0))) * 100.0
        spread_theta = (float(long_leg.get("theta", 0.0)) - float(short_leg.get("theta", 0.0))) * 100.0

        structure = "vertical_call_spread" if is_bullish else "vertical_put_spread"
        # Breakeven: long strike + debit (bull-call), long strike - debit (bear-put)
        breakeven = long_strike + debit if is_bullish else long_strike - debit

        return StructurePick(
            structure_type=structure,
            long_strike=long_strike,
            short_strike=short_strike,
            expiry=expiry,
            dte=dte,
            max_profit=round(max_profit, 2),
            max_loss=round(max_loss, 2),
            breakeven=round(breakeven, 2),
            odds=round(odds, 3),
            delta_per_contract=round(spread_delta, 2),
            vega_per_contract=round(spread_vega, 2),
            theta_per_contract=round(spread_theta, 2),
            debit_per_contract=round(debit * 100.0, 2),
            reason=f"{'bull-call' if is_bullish else 'bear-put'} debit spread "
                   f"L{long_strike:g}/S{short_strike:g}, width={real_width:g}, odds={odds:.2f}",
            diagnostics={
                "width": real_width,
                "debit": round(debit, 3),
                "required_odds": round(required_odds, 3),
                "long_ask": long_ask,
                "short_bid": short_bid,
            },
        )

    return None


# -----------------------------------------------------------------------------
# Outright long fallback (only when conviction is high enough)
# -----------------------------------------------------------------------------

def _pick_outright(
    legs: list[dict],
    spot: float,
    is_bullish: bool,
    p_bull: float,
    expiry: str | None = None,
    dte: int = DEFAULT_DTE,
) -> StructurePick | None:
    """Pick a near-the-money long option as a fallback when no vertical is viable."""
    if (p_bull if is_bullish else 1.0 - p_bull) < OUTRIGHT_MIN_P_BULL:
        return None

    leg = _find_atm(legs, spot)
    if leg is None:
        return None
    ask = float(leg["ask"])
    if ask <= 0:
        return None
    strike = float(leg["strike"])

    # For outright long: max_loss = premium paid; max_profit capped at 2x premium for sizing
    # (real max profit is unbounded; using 2x is a conservative Kelly proxy).
    max_loss = ask * 100.0
    max_profit = max_loss * 2.0
    odds = 2.0
    is_call = is_bullish
    delta = _approx_delta(leg, spot, is_call) * 100.0
    vega = float(leg.get("vega", 0.0)) * 100.0
    theta = float(leg.get("theta", 0.0)) * 100.0
    breakeven = strike + ask if is_bullish else strike - ask
    structure = "long_call" if is_bullish else "long_put"

    return StructurePick(
        structure_type=structure,
        long_strike=strike,
        short_strike=None,
        expiry=expiry,
        dte=dte,
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        breakeven=round(breakeven, 2),
        odds=round(odds, 3),
        delta_per_contract=round(delta, 2),
        vega_per_contract=round(vega, 2),
        theta_per_contract=round(theta, 2),
        debit_per_contract=round(ask * 100.0, 2),
        reason=f"outright {'long-call' if is_bullish else 'long-put'} @ {strike:g}, premium={ask:.2f}",
        diagnostics={"premium": ask, "p_bull": p_bull},
    )


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def pick_structure(
    chain: dict[str, Any] | None,
    spot: float,
    direction: str,
    p_bull: float,
    iv_rank: float | None = None,
    expiry: str | None = None,
) -> StructurePick | None:
    """Pick an options structure that maximises Kelly viability.

    Args:
        chain: dict with 'calls' and 'puts' lists (each leg has strike/bid/ask/oi/iv/delta).
        spot:  current underlying price.
        direction: "BULLISH" | "BEARISH" | "NEUTRAL".
        p_bull: calibrated probability of bullish from consensus.
        iv_rank: 0..100, optional — currently unused (reserved for structure choice).
        expiry: ISO date string (YYYY-MM-DD) of the selected expiration; used to compute real DTE.

    Returns:
        StructurePick or None if no liquid + Kelly-viable structure exists.
    """
    if direction not in ("BULLISH", "BEARISH"):
        return None
    if not chain:
        return None

    legs = chain.get("calls" if direction == "BULLISH" else "puts", [])
    if not legs:
        return None

    is_bullish = direction == "BULLISH"
    required_odds = _required_odds(p_bull, direction)

    # Compute real DTE from expiry date if provided
    dte = DEFAULT_DTE
    if expiry:
        try:
            exp_date = datetime.date.fromisoformat(expiry)
            dte = max(0, (exp_date - datetime.date.today()).days)
        except ValueError:
            pass

    # 1) Try debit vertical first (defined risk + favorable odds for our conviction range)
    pick = _pick_debit_vertical(legs, spot, is_bullish, required_odds, expiry=expiry, dte=dte)
    if pick is not None:
        return pick

    # 2) Outright long only when conviction is high enough to justify fat-tail risk
    return _pick_outright(legs, spot, is_bullish, p_bull, expiry=expiry, dte=dte)
