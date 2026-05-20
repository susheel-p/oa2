"""Broker protocol for multi-leg options orders.

The order state machine in `orders.py` talks to brokers through this
protocol. Production wires `MoomooBroker` (see `moomoo_broker.py`); tests
use a fake implementation that lets us drive partial-fill recovery paths
deterministically.

Each leg is submitted as an independent single-leg order. We do NOT rely
on broker-side spread / combo order types because:

  1. Not every broker accepts every structure (moomoo's combo support is
     limited for US options).
  2. Combo orders hide the per-leg fill state we need to react to partial
     fills.

The tradeoff is execution risk between legs. The state machine mitigates
this with aggressive recovery (re-route remaining legs to marketable
prices, or unwind already-filled legs) rather than hoping for symmetric
fills.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class LegStatus(str, Enum):
    """Lifecycle for a single broker leg.

    The state machine treats PARTIAL as an actionable signal — a partial
    fill on one leg of a spread means the structure is broken and must
    be either completed or unwound.
    """
    PENDING = "PENDING"        # not yet submitted to broker
    WORKING = "WORKING"        # live at broker, zero fills
    PARTIAL = "PARTIAL"        # some contracts filled
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"      # broker rejected (bad symbol, no margin, etc.)
    FAILED = "FAILED"          # network / SDK error; state unknown


@dataclass
class LegSpec:
    """Static parameters describing one leg to be submitted."""
    underlying: str
    expiry: _dt.date
    strike: float
    right: str                 # "C" or "P"
    side: int                  # +1 buy-to-open / -1 sell-to-open
    contracts: int
    limit_price: float | None = None   # None = market


@dataclass
class LegFill:
    """Snapshot of broker-reported fill state for one leg."""
    leg_id: str                # broker order id
    status: LegStatus
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    fill_time: str | None = None  # ISO timestamp from broker dealt_time (if available)
    error: str | None = None


class Broker(Protocol):
    """Minimal broker surface the order state machine relies on.

    Implementations MUST be idempotent w.r.t. `client_order_id`: re-submitting
    the same client id after a network blip must not create a duplicate order.
    """

    def submit_leg(self, client_order_id: str, leg: LegSpec) -> LegFill:
        """Send one leg. Returns initial fill snapshot (usually WORKING)."""
        ...

    def cancel_leg(self, leg_id: str) -> LegFill:
        """Cancel a working/partial leg. Returns post-cancel snapshot."""
        ...

    def get_leg(self, leg_id: str) -> LegFill:
        """Poll current state of one leg."""
        ...

    def replace_leg(self, leg_id: str, new_limit_price: float) -> LegFill:
        """Modify limit price on a working/partial leg (used by recovery)."""
        ...
