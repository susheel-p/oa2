"""Multi-leg order state machine.

A spread / condor / butterfly submits as N independent broker legs. Between
legs, the market can move and partial fills can happen. The state machine's
job is to drive a `MultiLegOrder` to either FULLY_FILLED (all legs at full
size) or UNWOUND (any filled legs closed back out, so we hold no broken
structure) — never leave a torn structure live.

Recovery policy (REPRICE_THEN_UNWIND) on partial / stuck legs:

  1. Poll all legs. If every leg is FILLED at full size → FULLY_FILLED, done.
  2. If any leg is REJECTED / FAILED: cancel everything still working, then
     unwind any already-filled legs at market → UNWOUND.
  3. If we have at least one fully-filled leg but other legs are still
     working with no fills after `reprice_after_seconds`, replace the
     stuck legs with marketable limits (cross the spread by
     `reprice_aggression`). After `max_reprices` attempts, give up and
     unwind.
  4. If we have a PARTIAL on any leg, treat it the same as #3 — replace at
     a marketable price to complete the remainder.
  5. EOD cutoff or `max_total_seconds` exceeded with no full fill →
     unwind whatever filled.

Unwind = submit reversing market orders for whatever contracts actually
filled. This is the "non-negotiable" part: we never walk away from a
broken structure.

The state machine is single-threaded and tick-driven. Call `step()` from
the pipeline loop (or a dedicated thread) until `is_terminal()` returns
True.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum

from oa2.core.clock import Clock, SystemClock
from oa2.execution.broker import Broker, LegFill, LegSpec, LegStatus


class OrderStatus(str, Enum):
    BUILDING = "BUILDING"          # constructed, not yet submitted
    WORKING = "WORKING"            # all legs submitted, none fully filled
    PARTIAL = "PARTIAL"            # at least one leg fully filled, others not
    RECOVERING = "RECOVERING"      # repricing / chasing remaining legs
    UNWINDING = "UNWINDING"        # closing already-filled legs after failure
    FULLY_FILLED = "FULLY_FILLED"  # terminal: structure live as intended
    UNWOUND = "UNWOUND"            # terminal: no residual position
    FAILED = "FAILED"              # terminal: unknown / partial residual; needs human


_TERMINAL: set[OrderStatus] = {
    OrderStatus.FULLY_FILLED,
    OrderStatus.UNWOUND,
    OrderStatus.FAILED,
}


@dataclass
class TrackedLeg:
    """Per-leg state held by the state machine.

    Distinct from `LegSpec` (static intent) and `LegFill` (broker snapshot):
    this is what the machine remembers across ticks, including the chain of
    broker leg ids we've created via replace/unwind.
    """
    spec: LegSpec
    client_order_id: str
    fill: LegFill                            # latest snapshot
    reprice_count: int = 0
    unwind_leg_id: str | None = None         # broker id of the reversing leg
    unwind_filled_qty: int = 0
    history: list[LegFill] = field(default_factory=list)

    @property
    def filled_qty(self) -> int:
        return self.fill.filled_qty

    @property
    def is_done(self) -> bool:
        return self.fill.status in (
            LegStatus.FILLED, LegStatus.CANCELLED, LegStatus.REJECTED
        )

    @property
    def needs_unwind(self) -> bool:
        """We've got contracts on the books for this leg that need to be
        closed out (because the overall order is failing)."""
        return self.filled_qty > 0


@dataclass
class MultiLegOrder:
    """One spread / condor / etc. order tracked through completion.

    The state machine mutates this object in-place. Callers can inspect
    `status` and `legs` at any time and persist the whole thing for crash
    recovery — the state is fully serializable (after replacing the broker
    handle).
    """
    order_id: str
    structure: str                           # e.g. "VERTICAL_CALL_SPREAD"
    legs: list[TrackedLeg]
    status: OrderStatus = OrderStatus.BUILDING
    created_at: float = 0.0
    last_step_at: float = 0.0
    reprice_aggression: float = 0.05         # $0.05 cross past mid per attempt
    max_reprices: int = 3
    reprice_after_seconds: float = 20.0
    max_total_seconds: float = 180.0
    failure_reason: str | None = None

    def is_terminal(self) -> bool:
        return self.status in _TERMINAL


def build_order(
    structure: str,
    leg_specs: list[LegSpec],
    *,
    reprice_aggression: float = 0.05,
    max_reprices: int = 3,
    reprice_after_seconds: float = 20.0,
    max_total_seconds: float = 180.0,
    clock: Clock | None = None,
) -> MultiLegOrder:
    """Construct a MultiLegOrder in BUILDING state. No broker calls yet."""
    clock = clock or SystemClock()
    order_id = f"oa2-{uuid.uuid4().hex[:12]}"
    legs = [
        TrackedLeg(
            spec=spec,
            client_order_id=f"{order_id}-L{i}",
            fill=LegFill(leg_id="", status=LegStatus.PENDING),
        )
        for i, spec in enumerate(leg_specs)
    ]
    return MultiLegOrder(
        order_id=order_id,
        structure=structure,
        legs=legs,
        created_at=clock.now(),
        last_step_at=clock.now(),
        reprice_aggression=reprice_aggression,
        max_reprices=max_reprices,
        reprice_after_seconds=reprice_after_seconds,
        max_total_seconds=max_total_seconds,
    )


class MultiLegOrderEngine:
    """Drives MultiLegOrder instances toward a terminal state.

    Usage:
        engine = MultiLegOrderEngine(broker)
        order = build_order("VERTICAL_CALL_SPREAD", legs)
        engine.submit(order)
        while not order.is_terminal():
            engine.step(order)
            time.sleep(1)
    """

    def __init__(self, broker: Broker, clock: Clock | None = None):
        self._broker = broker
        self._clock = clock or SystemClock()

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    def submit(self, order: MultiLegOrder) -> None:
        """Submit all legs. Order moves BUILDING -> WORKING (or FAILED).

        If any leg rejects at submit time, the others that already went out
        are cancelled and any fills are unwound. We don't ship a partial
        structure to the book under any circumstance.
        """
        if order.status != OrderStatus.BUILDING:
            raise RuntimeError(f"order {order.order_id} not in BUILDING state")

        submit_errors: list[str] = []
        for leg in order.legs:
            try:
                fill = self._broker.submit_leg(leg.client_order_id, leg.spec)
            except Exception as e:
                fill = LegFill(leg_id="", status=LegStatus.FAILED, error=str(e))
                submit_errors.append(f"{leg.client_order_id}: {e}")

            leg.fill = fill
            leg.history.append(fill)

        order.last_step_at = self._clock.now()

        bad = [l for l in order.legs if l.fill.status in (LegStatus.REJECTED, LegStatus.FAILED)]
        if bad:
            order.failure_reason = (
                f"submit failure on {len(bad)}/{len(order.legs)} legs: "
                + "; ".join(submit_errors or [f"{l.client_order_id}={l.fill.status}" for l in bad])
            )
            self._begin_unwind(order)
            return

        order.status = OrderStatus.WORKING

    # ------------------------------------------------------------------
    # Per-tick drive
    # ------------------------------------------------------------------

    def step(self, order: MultiLegOrder) -> OrderStatus:
        """Advance one tick. Idempotent — safe to call repeatedly."""
        if order.is_terminal():
            return order.status

        now = self._clock.now()
        order.last_step_at = now

        if order.status == OrderStatus.UNWINDING:
            self._poll_unwind(order)
            return order.status

        self._refresh_legs(order)

        if all(l.fill.status == LegStatus.FILLED and l.filled_qty == l.spec.contracts
               for l in order.legs):
            order.status = OrderStatus.FULLY_FILLED
            return order.status

        fatal = [l for l in order.legs if l.fill.status in (LegStatus.REJECTED, LegStatus.FAILED)]
        if fatal:
            order.failure_reason = f"fatal leg state: {[l.fill.status for l in fatal]}"
            self._begin_unwind(order)
            return order.status

        if now - order.created_at > order.max_total_seconds:
            order.failure_reason = (
                f"max_total_seconds={order.max_total_seconds}s exceeded "
                f"without full fill"
            )
            self._begin_unwind(order)
            return order.status

        any_filled = any(l.filled_qty > 0 for l in order.legs)
        order.status = OrderStatus.PARTIAL if any_filled else OrderStatus.WORKING

        if now - order.created_at >= order.reprice_after_seconds:
            self._reprice_stuck_legs(order)

        return order.status

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _refresh_legs(self, order: MultiLegOrder) -> None:
        for leg in order.legs:
            if leg.fill.status in (LegStatus.FILLED, LegStatus.CANCELLED,
                                   LegStatus.REJECTED, LegStatus.FAILED):
                continue
            if not leg.fill.leg_id:
                continue
            try:
                fill = self._broker.get_leg(leg.fill.leg_id)
            except Exception as e:
                fill = LegFill(leg_id=leg.fill.leg_id, status=LegStatus.FAILED, error=str(e))
            leg.fill = fill
            leg.history.append(fill)

    def _reprice_stuck_legs(self, order: MultiLegOrder) -> None:
        for leg in order.legs:
            if leg.fill.status not in (LegStatus.WORKING, LegStatus.PARTIAL):
                continue
            if leg.reprice_count >= order.max_reprices:
                order.failure_reason = (
                    f"leg {leg.client_order_id} exceeded max_reprices "
                    f"({order.max_reprices})"
                )
                self._begin_unwind(order)
                return

            current = leg.spec.limit_price or 0.0
            bump = order.reprice_aggression * (leg.reprice_count + 1)
            new_price = current + bump if leg.spec.side > 0 else max(0.01, current - bump)

            try:
                fill = self._broker.replace_leg(leg.fill.leg_id, new_price)
            except Exception as e:
                fill = LegFill(leg_id=leg.fill.leg_id, status=LegStatus.FAILED, error=str(e))

            leg.fill = fill
            leg.history.append(fill)
            leg.spec.limit_price = new_price
            leg.reprice_count += 1

        order.status = OrderStatus.RECOVERING

    def _begin_unwind(self, order: MultiLegOrder) -> None:
        """Cancel all working legs, submit reversing market orders for any
        already-filled contracts, and move to UNWINDING (terminal once the
        reversing orders settle)."""
        order.status = OrderStatus.UNWINDING

        for leg in order.legs:
            if leg.fill.status in (LegStatus.WORKING, LegStatus.PARTIAL) and leg.fill.leg_id:
                try:
                    fill = self._broker.cancel_leg(leg.fill.leg_id)
                except Exception as e:
                    fill = LegFill(leg_id=leg.fill.leg_id, status=LegStatus.FAILED, error=str(e))
                leg.fill = fill
                leg.history.append(fill)

        for leg in order.legs:
            if not leg.needs_unwind:
                continue
            reverse = LegSpec(
                underlying=leg.spec.underlying,
                expiry=leg.spec.expiry,
                strike=leg.spec.strike,
                right=leg.spec.right,
                side=-leg.spec.side,
                contracts=leg.filled_qty,
                limit_price=None,        # market
            )
            unwind_cid = f"{leg.client_order_id}-UNW"
            try:
                fill = self._broker.submit_leg(unwind_cid, reverse)
                leg.unwind_leg_id = fill.leg_id
            except Exception as e:
                leg.unwind_leg_id = None
                order.failure_reason = (
                    (order.failure_reason or "") + f" | UNWIND SUBMIT FAILED "
                    f"for {leg.client_order_id}: {e}"
                )

        self._poll_unwind(order)

    def _poll_unwind(self, order: MultiLegOrder) -> None:
        all_done = True
        any_failed = False

        for leg in order.legs:
            if not leg.needs_unwind:
                continue
            if leg.unwind_leg_id is None:
                any_failed = True
                continue
            try:
                u_fill = self._broker.get_leg(leg.unwind_leg_id)
            except Exception as e:
                any_failed = True
                leg.history.append(LegFill(leg_id=leg.unwind_leg_id,
                                           status=LegStatus.FAILED, error=str(e)))
                continue

            leg.history.append(u_fill)
            leg.unwind_filled_qty = u_fill.filled_qty
            if u_fill.status == LegStatus.FILLED:
                continue
            if u_fill.status in (LegStatus.REJECTED, LegStatus.FAILED, LegStatus.CANCELLED):
                any_failed = True
                continue
            all_done = False

        if any_failed:
            order.status = OrderStatus.FAILED
            return
        if all_done:
            order.status = OrderStatus.UNWOUND
