"""Tests for the multi-leg order state machine.

Uses a fake broker so partial-fill recovery paths are deterministic. The
moomoo adapter itself is integration-tested separately (requires OpenD).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from oa2.core.clock import ManualClock
from oa2.execution.broker import LegFill, LegSpec, LegStatus
from oa2.execution.orders import (
    MultiLegOrderEngine,
    OrderStatus,
    build_order,
)


# ---------------------------------------------------------------------------
# Fake broker
# ---------------------------------------------------------------------------

@dataclass
class _FakeOrder:
    leg_id: str
    spec: LegSpec
    status: LegStatus = LegStatus.WORKING
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    limit_price: float | None = None


class FakeBroker:
    """In-memory broker that lets tests script per-leg outcomes.

    Tests configure `on_submit` / `on_replace` / `on_get` callbacks keyed by
    a leg's client_order_id to drive specific fill scenarios.
    """
    def __init__(self):
        self.orders: dict[str, _FakeOrder] = {}     # leg_id -> order
        self.by_cid: dict[str, str] = {}            # client_order_id -> leg_id
        self._next_id = 0
        self.submit_fail_for: set[str] = set()      # cids that should reject on submit
        self.submit_raise_for: set[str] = set()     # cids that should raise on submit
        self.fills_after_submit: dict[str, int] = {}  # cid -> qty immediately filled
        self.fills_after_replace: dict[str, int] = {}  # cid -> qty filled after first replace

    def _new_id(self) -> str:
        self._next_id += 1
        return f"BRK{self._next_id:04d}"

    def submit_leg(self, client_order_id: str, leg: LegSpec) -> LegFill:
        if client_order_id in self.submit_raise_for:
            raise RuntimeError("simulated network failure")
        if client_order_id in self.submit_fail_for:
            return LegFill(leg_id="", status=LegStatus.REJECTED, error="simulated reject")

        leg_id = self._new_id()
        # Market orders (limit_price=None) auto-fill — this mirrors broker behavior
        # and is essential for unwind legs which the state machine submits as market.
        if leg.limit_price is None:
            qty = leg.contracts
        else:
            qty = self.fills_after_submit.get(client_order_id, 0)
        status = LegStatus.FILLED if qty >= leg.contracts else (
            LegStatus.PARTIAL if qty > 0 else LegStatus.WORKING
        )
        self.orders[leg_id] = _FakeOrder(
            leg_id=leg_id, spec=leg, status=status,
            filled_qty=qty, avg_fill_price=leg.limit_price or 1.0,
            limit_price=leg.limit_price,
        )
        self.by_cid[client_order_id] = leg_id
        return LegFill(leg_id=leg_id, status=status, filled_qty=qty,
                       avg_fill_price=leg.limit_price or 1.0)

    def cancel_leg(self, leg_id: str) -> LegFill:
        o = self.orders[leg_id]
        if o.filled_qty >= o.spec.contracts:
            o.status = LegStatus.FILLED
        else:
            o.status = LegStatus.CANCELLED
        return self._snapshot(o)

    def get_leg(self, leg_id: str) -> LegFill:
        return self._snapshot(self.orders[leg_id])

    def replace_leg(self, leg_id: str, new_limit_price: float) -> LegFill:
        o = self.orders[leg_id]
        o.limit_price = new_limit_price
        # Find cid for this leg
        cid = next((c for c, lid in self.by_cid.items() if lid == leg_id), None)
        if cid and cid in self.fills_after_replace:
            extra = self.fills_after_replace.pop(cid)
            o.filled_qty = min(o.spec.contracts, o.filled_qty + extra)
            o.status = LegStatus.FILLED if o.filled_qty >= o.spec.contracts else LegStatus.PARTIAL
        return self._snapshot(o)

    @staticmethod
    def _snapshot(o: _FakeOrder) -> LegFill:
        return LegFill(leg_id=o.leg_id, status=o.status,
                       filled_qty=o.filled_qty, avg_fill_price=o.avg_fill_price)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vertical_call_legs(contracts: int = 1) -> list[LegSpec]:
    exp = _dt.date(2026, 6, 19)
    return [
        LegSpec("AAPL", exp, 200.0, "C", +1, contracts, limit_price=2.50),
        LegSpec("AAPL", exp, 210.0, "C", -1, contracts, limit_price=1.00),
    ]


# ---------------------------------------------------------------------------
# Golden path
# ---------------------------------------------------------------------------

def test_all_legs_fill_immediately_reaches_FULLY_FILLED():
    broker = FakeBroker()
    clock = ManualClock(1_000_000.0)
    engine = MultiLegOrderEngine(broker, clock=clock)
    order = build_order("VCS", _vertical_call_legs(), clock=clock)

    # Both legs fill on submit
    for leg in order.legs:
        broker.fills_after_submit[leg.client_order_id] = leg.spec.contracts

    engine.submit(order)
    engine.step(order)

    assert order.status == OrderStatus.FULLY_FILLED
    assert order.is_terminal()


# ---------------------------------------------------------------------------
# Partial fill -> reprice -> complete
# ---------------------------------------------------------------------------

def test_partial_fill_repriced_then_completes():
    broker = FakeBroker()
    clock = ManualClock(1_000_000.0)
    engine = MultiLegOrderEngine(
        broker, clock=clock,
    )
    legs = _vertical_call_legs(contracts=10)
    order = build_order("VCS", legs, reprice_after_seconds=20.0,
                        max_total_seconds=300.0, clock=clock)

    # Leg 0 partially fills (3/10), leg 1 fills fully
    broker.fills_after_submit[order.legs[0].client_order_id] = 3
    broker.fills_after_submit[order.legs[1].client_order_id] = 10
    # After reprice, leg 0 fills the remaining 7
    broker.fills_after_replace[order.legs[0].client_order_id] = 7

    engine.submit(order)
    assert order.status == OrderStatus.WORKING

    # Initial step before reprice window -> PARTIAL, no replace yet
    engine.step(order)
    assert order.status == OrderStatus.PARTIAL
    assert order.legs[0].reprice_count == 0

    # Advance past reprice window
    clock.advance(25.0)
    engine.step(order)

    # Leg 0 should have been repriced and fully filled
    assert order.legs[0].reprice_count == 1
    assert order.legs[0].fill.filled_qty == 10

    engine.step(order)
    assert order.status == OrderStatus.FULLY_FILLED


# ---------------------------------------------------------------------------
# Reject on submit -> unwind any filled legs
# ---------------------------------------------------------------------------

def test_submit_reject_unwinds_already_filled_legs():
    broker = FakeBroker()
    clock = ManualClock(1_000_000.0)
    engine = MultiLegOrderEngine(broker, clock=clock)
    legs = _vertical_call_legs(contracts=5)
    order = build_order("VCS", legs, clock=clock)

    # Leg 0 fills, leg 1 rejects
    broker.fills_after_submit[order.legs[0].client_order_id] = 5
    broker.submit_fail_for.add(order.legs[1].client_order_id)

    engine.submit(order)

    # Should have begun unwinding
    assert order.status in (OrderStatus.UNWINDING, OrderStatus.UNWOUND, OrderStatus.FAILED)
    assert order.failure_reason is not None

    # Unwind leg for leg 0 should have been submitted (it was filled)
    assert order.legs[0].unwind_leg_id is not None
    # Reverse order: opposite side, market price
    reverse_order = broker.orders[order.legs[0].unwind_leg_id]
    assert reverse_order.spec.side == -order.legs[0].spec.side
    assert reverse_order.spec.contracts == 5

    # Drive to terminal
    for _ in range(3):
        if order.is_terminal():
            break
        engine.step(order)

    assert order.status == OrderStatus.UNWOUND


# ---------------------------------------------------------------------------
# Stuck order beyond max_total_seconds -> unwind
# ---------------------------------------------------------------------------

def test_timeout_with_partial_fill_unwinds():
    broker = FakeBroker()
    clock = ManualClock(1_000_000.0)
    engine = MultiLegOrderEngine(broker, clock=clock)
    legs = _vertical_call_legs(contracts=4)
    order = build_order(
        "VCS", legs,
        reprice_after_seconds=10.0,
        max_reprices=1,
        max_total_seconds=30.0,
        clock=clock,
    )

    # Leg 0 partial (2/4), leg 1 working. Replaces don't help.
    broker.fills_after_submit[order.legs[0].client_order_id] = 2

    engine.submit(order)
    clock.advance(15.0)
    engine.step(order)   # first reprice burst
    clock.advance(20.0)
    engine.step(order)   # should now exceed max_total_seconds and unwind

    for _ in range(3):
        if order.is_terminal():
            break
        engine.step(order)

    assert order.status == OrderStatus.UNWOUND
    # Leg 0 had 2 filled -> should have an unwind leg of qty 2 reversing the side
    assert order.legs[0].unwind_leg_id is not None
    rev = broker.orders[order.legs[0].unwind_leg_id]
    assert rev.spec.contracts == 2
    assert rev.spec.side == -order.legs[0].spec.side


# ---------------------------------------------------------------------------
# Exception during submit -> FAILED handling
# ---------------------------------------------------------------------------

def test_submit_exception_unwinds_safely():
    broker = FakeBroker()
    clock = ManualClock(1_000_000.0)
    engine = MultiLegOrderEngine(broker, clock=clock)
    legs = _vertical_call_legs(contracts=2)
    order = build_order("VCS", legs, clock=clock)

    broker.fills_after_submit[order.legs[0].client_order_id] = 2
    broker.submit_raise_for.add(order.legs[1].client_order_id)

    engine.submit(order)
    for _ in range(3):
        if order.is_terminal():
            break
        engine.step(order)

    # The filled leg should be reversed; final state should be UNWOUND
    assert order.status == OrderStatus.UNWOUND
    assert order.legs[0].unwind_leg_id is not None


# ---------------------------------------------------------------------------
# Idempotency: re-submitting after partial network success
# ---------------------------------------------------------------------------

def test_build_order_assigns_unique_client_order_ids():
    legs = _vertical_call_legs(contracts=1)
    o1 = build_order("VCS", legs)
    o2 = build_order("VCS", legs)
    cids = {l.client_order_id for l in o1.legs} | {l.client_order_id for l in o2.legs}
    assert len(cids) == 4  # all unique
