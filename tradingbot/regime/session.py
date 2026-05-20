"""Session-state overlay — Phase D1.

Adds intraday session tagging on top of the existing vol × trend regime.
The session determines which debaters carry the most signal weight at
each point in the trading day.

Session buckets (all times Eastern):
    PRE_MARKET:  before 09:30 ET
    OPEN:        09:30 – 10:00 ET  — high vol, wide spreads, flow most meaningful
    MORNING:     10:00 – 12:00 ET  — directional trend establishes
    MIDDAY:      12:00 – 14:00 ET  — low vol, narrow ranges, theta harvest optimal
    AFTERNOON:   14:00 – 15:30 ET  — institutional positioning and rebalancing
    POWER_HOUR:  15:30 – 16:00 ET  — directional follow-through, closing flows
    CLOSED:      after 16:00 ET / before 09:30 ET

Session-specific debater weight multipliers are applied AFTER the base
GLS weights from the consensus engine. They are fractional adjustments
(1.0 = no change; 1.3 = 30% boost; 0.7 = 30% reduction).

These multipliers are based on empirical market microstructure:
    OPEN:        flow and GEX dominate (institutional activity, gap reactions)
    MORNING:     directional (trend establishing) and regime signals matter most
    MIDDAY:      income/theta collecting is optimal; directional is noisy
    AFTERNOON:   directional and flow (institutional rebalancing flows)
    POWER_HOUR:  directional and flow again; gamma risk elevated
    PRE/CLOSED:  regime and volatility signals most stable; flow is irrelevant

Usage:
    session = get_session_state()               # uses datetime.now(ET)
    session = get_session_state(some_datetime)  # or pass explicit time
    multipliers = session_weight_multipliers(session)
    # Apply to GLS weights before consensus aggregation
"""

from __future__ import annotations

import datetime
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")


class SessionState(Enum):
    """Intraday session buckets (Eastern time)."""
    PRE_MARKET = "pre_market"    # before 09:30
    OPEN = "open"                # 09:30 – 10:00
    MORNING = "morning"          # 10:00 – 12:00
    MIDDAY = "midday"            # 12:00 – 14:00
    AFTERNOON = "afternoon"      # 14:00 – 15:30
    POWER_HOUR = "power_hour"    # 15:30 – 16:00
    CLOSED = "closed"            # after 16:00


# Session boundaries as (hour, minute) tuples in ET
_BOUNDARIES = [
    ((9, 30), SessionState.OPEN),
    ((10, 0), SessionState.MORNING),
    ((12, 0), SessionState.MIDDAY),
    ((14, 0), SessionState.AFTERNOON),
    ((15, 30), SessionState.POWER_HOUR),
    ((16, 0), SessionState.CLOSED),
]


def get_session_state(dt: datetime.datetime | None = None) -> SessionState:
    """Return the current (or given) session state.

    Args:
        dt: datetime to classify. If None, uses datetime.now(ET).
            If the datetime is timezone-naive it is assumed to be ET.

    Returns:
        SessionState enum value.
    """
    if dt is None:
        dt = datetime.datetime.now(tz=ET)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)

    time_et = (dt.hour, dt.minute)

    if time_et < (9, 30):
        return SessionState.PRE_MARKET

    for boundary, state in _BOUNDARIES:
        if time_et < boundary:
            # We are in the session that started at the previous boundary
            break
        current_state = state
    else:
        current_state = SessionState.CLOSED

    return current_state


def session_weight_multipliers(session: SessionState) -> dict[str, float]:
    """Return per-debater weight multipliers for the given session.

    Multipliers are applied to GLS weights from the consensus engine.
    1.0 = no change.  Values above 1.0 boost; below 1.0 reduce.

    Debater names must match the keys used in DebaterEnsemble.debaters.
    """
    multipliers: dict[SessionState, dict[str, float]] = {
        SessionState.PRE_MARKET: {
            "directional": 0.80,   # price action not yet meaningful
            "income": 1.00,        # premium levels stable overnight
            "volatility": 1.20,    # overnight IV moves matter
            "flow": 0.50,          # no tape flow pre-market
            "sentiment": 1.00,
        },
        SessionState.OPEN: {
            "directional": 0.90,   # gap fills / reversals — noisy
            "income": 0.90,        # spreads wide at open
            "volatility": 1.10,    # vol often spikes at open
            "flow": 1.40,          # sweeps and dark pool prints most meaningful
            "sentiment": 1.00,
        },
        SessionState.MORNING: {
            "directional": 1.30,   # trend establishes in morning session
            "income": 1.00,
            "volatility": 1.00,
            "flow": 1.10,          # institutional flows still active
            "sentiment": 0.90,     # retail sentiment lags price action
        },
        SessionState.MIDDAY: {
            "directional": 0.70,   # mean-reverting, low conviction
            "income": 1.30,        # best time for theta harvesting
            "volatility": 1.20,    # vol signals stable in midday
            "flow": 0.70,          # institutional flow reduced midday
            "sentiment": 1.10,     # retail sentiment (Twitter/Reddit) peaks midday
        },
        SessionState.AFTERNOON: {
            "directional": 1.20,   # institutional rebalancing = directional flows
            "income": 1.00,
            "volatility": 0.90,
            "flow": 1.20,          # institutional positioning flows pick up
            "sentiment": 0.80,
        },
        SessionState.POWER_HOUR: {
            "directional": 1.30,   # strong directional follow-through common
            "income": 0.80,        # gamma risk elevated — avoid new short premium
            "volatility": 1.10,
            "flow": 1.30,          # closing institutional flows
            "sentiment": 0.80,
        },
        SessionState.CLOSED: {
            "directional": 0.80,
            "income": 1.00,
            "volatility": 1.10,    # after-hours vol announcements
            "flow": 0.30,          # no tape flow after market
            "sentiment": 1.20,     # after-hours news / earnings reactions
        },
    }

    return multipliers.get(session, {name: 1.0 for name in ["directional", "income", "volatility", "flow", "sentiment"]})


def apply_session_weights(
    gls_weights: dict[str, float],
    session: SessionState,
) -> dict[str, float]:
    """Apply session multipliers to GLS weights and renormalize.

    Args:
        gls_weights: {debater_name: weight} from ConsensusEngine (sums to 1.0).
        session: current session state.

    Returns:
        Renormalized weights with session adjustments applied.
    """
    multipliers = session_weight_multipliers(session)
    adjusted = {
        name: weight * multipliers.get(name, 1.0)
        for name, weight in gls_weights.items()
    }
    total = sum(adjusted.values())
    if total <= 0:
        return gls_weights  # fallback: return original
    return {name: w / total for name, w in adjusted.items()}


def session_context(dt: datetime.datetime | None = None) -> dict[str, Any]:
    """Return a context dict with session state and multipliers.

    Intended to be merged into the pipeline context dict.
    """
    session = get_session_state(dt)
    return {
        "session_state": session.value,
        "session_weight_multipliers": session_weight_multipliers(session),
    }
