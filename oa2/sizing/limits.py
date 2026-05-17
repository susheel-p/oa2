"""Book-level Greeks hard caps — Phase B2.

Tracks the running Greeks of all open positions and enforces hard caps on
aggregate exposure. Any proposed trade that would breach a cap is rejected
before execution, regardless of consensus direction or Kelly sizing.

Caps (defaults, configurable):
    net_delta:              ±0.30 × account_size in dollar-delta terms
    net_vega:               ±50 dollars per 1% IV move
    net_theta:              single-day theta <= 2% of account (absolute)
    single_underlying_pct:  max 25% of net_vega or net_delta in one name

Why hard caps instead of soft penalties:
    Options books can blow up quickly when Greeks are additive across positions.
    A soft penalty would allow the sizing engine to approve many correlated trades
    until the cumulative exposure is dangerous. Hard caps are structurally simpler
    to reason about and audit.

Usage:
    book = GreeksBook(account_size=50_000)
    book.add_position("TSLA_trade_001", delta=0.35, vega=12.0, theta=-18.0, underlying="TSLA")

    result = book.check_proposed(delta=0.20, vega=8.0, theta=-12.0, underlying="TSLA")
    if not result.approved:
        print(result.reject_reason)
    else:
        book.add_position(...)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Default caps (conservative defaults for a $50k account scale)
_DEFAULT_DELTA_LIMIT = 0.30      # fraction of account in dollar-delta
_DEFAULT_VEGA_LIMIT = 50.0       # dollars per 1% IV move
_DEFAULT_THETA_LIMIT = 0.02      # fraction of account in daily theta (absolute)
_DEFAULT_SINGLE_NAME_PCT = 0.25  # max fraction of vega or delta in one underlying


@dataclass
class PositionGreeks:
    """Greeks for a single open position."""
    trade_id: str
    underlying: str
    delta: float       # dollar delta (positive = long, negative = short)
    vega: float        # dollars per 1% IV move (positive = long vega)
    theta: float       # daily theta (negative = time decay cost)
    contracts: int = 1


@dataclass
class LimitCheckResult:
    """Result of a Greek limit check."""
    approved: bool
    reject_reason: str | None
    current_delta: float
    current_vega: float
    current_theta: float
    proposed_delta: float
    proposed_vega: float
    proposed_theta: float
    delta_after: float
    vega_after: float
    theta_after: float
    delta_headroom: float        # dollars of delta remaining before cap
    vega_headroom: float         # dollars of vega remaining before cap
    theta_headroom: float        # dollars of theta remaining before cap


class GreeksBook:
    """Registry of open position Greeks with hard cap enforcement.

    All Greek values are in dollar terms:
        delta:  dollars moved per $1 move in underlying
        vega:   dollars moved per 1% IV move
        theta:  dollars gained/lost per calendar day (usually negative)

    The book is not persistent — it is rebuilt from the position registry
    on each session start. Positions are keyed by trade_id.
    """

    def __init__(
        self,
        account_size: float,
        delta_limit: float = _DEFAULT_DELTA_LIMIT,
        vega_limit: float = _DEFAULT_VEGA_LIMIT,
        theta_limit: float = _DEFAULT_THETA_LIMIT,
        single_name_pct: float = _DEFAULT_SINGLE_NAME_PCT,
    ):
        self.account_size = account_size
        self.delta_limit = delta_limit
        self.vega_limit = vega_limit
        self.theta_limit = theta_limit
        self.single_name_pct = single_name_pct
        self._positions: dict[str, PositionGreeks] = {}

    # ------------------------------------------------------------------
    # Book management
    # ------------------------------------------------------------------

    def add_position(
        self,
        trade_id: str,
        underlying: str,
        delta: float,
        vega: float,
        theta: float,
        contracts: int = 1,
    ) -> None:
        """Register a new open position in the book."""
        self._positions[trade_id] = PositionGreeks(
            trade_id=trade_id,
            underlying=underlying,
            delta=delta,
            vega=vega,
            theta=theta,
            contracts=contracts,
        )

    def remove_position(self, trade_id: str) -> None:
        """Remove a closed position from the book."""
        self._positions.pop(trade_id, None)

    def clear(self) -> None:
        """Remove all positions (e.g., EOD reset)."""
        self._positions.clear()

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    @property
    def net_delta(self) -> float:
        """Sum of dollar-delta across all open positions."""
        return sum(p.delta for p in self._positions.values())

    @property
    def net_vega(self) -> float:
        """Sum of dollar-vega across all open positions."""
        return sum(p.vega for p in self._positions.values())

    @property
    def net_theta(self) -> float:
        """Sum of daily theta across all open positions."""
        return sum(p.theta for p in self._positions.values())

    def underlying_delta(self, underlying: str) -> float:
        """Dollar-delta exposure in a single underlying name."""
        return sum(p.delta for p in self._positions.values() if p.underlying == underlying)

    def underlying_vega(self, underlying: str) -> float:
        """Dollar-vega exposure in a single underlying name."""
        return sum(p.vega for p in self._positions.values() if p.underlying == underlying)

    def position_count(self) -> int:
        return len(self._positions)

    def summary(self) -> dict[str, Any]:
        """Book-level summary for logging and diagnostics."""
        return {
            "account_size": self.account_size,
            "position_count": len(self._positions),
            "net_delta": round(self.net_delta, 2),
            "net_vega": round(self.net_vega, 2),
            "net_theta": round(self.net_theta, 2),
            "delta_limit_dollars": round(self._delta_cap_dollars(), 2),
            "vega_limit_dollars": round(self._vega_cap_dollars(), 2),
            "theta_limit_dollars": round(self._theta_cap_dollars(), 2),
        }

    # ------------------------------------------------------------------
    # Cap helpers
    # ------------------------------------------------------------------

    def _delta_cap_dollars(self) -> float:
        return self.account_size * self.delta_limit

    def _vega_cap_dollars(self) -> float:
        return self.vega_limit

    def _theta_cap_dollars(self) -> float:
        return self.account_size * self.theta_limit

    def _single_name_delta_cap(self) -> float:
        return self._delta_cap_dollars() * self.single_name_pct

    def _single_name_vega_cap(self) -> float:
        return self._vega_cap_dollars() * self.single_name_pct

    # ------------------------------------------------------------------
    # Limit check
    # ------------------------------------------------------------------

    def check_proposed(
        self,
        delta: float,
        vega: float,
        theta: float,
        underlying: str,
    ) -> LimitCheckResult:
        """Check whether a proposed trade would breach any hard cap.

        Args:
            delta: dollar-delta of the proposed new position.
            vega:  dollar-vega of the proposed new position.
            theta: daily theta of the proposed new position.
            underlying: ticker of the underlying (for single-name limits).

        Returns:
            LimitCheckResult — approved=True if all caps pass.
        """
        current_delta = self.net_delta
        current_vega = self.net_vega
        current_theta = self.net_theta

        delta_after = current_delta + delta
        vega_after = current_vega + vega
        theta_after = current_theta + theta

        delta_cap = self._delta_cap_dollars()
        vega_cap = self._vega_cap_dollars()
        theta_cap = self._theta_cap_dollars()

        # 1. Net delta cap
        if abs(delta_after) > delta_cap:
            return LimitCheckResult(
                approved=False,
                reject_reason=(
                    f"Net delta breach: {delta_after:+.2f} exceeds cap ±{delta_cap:.2f} "
                    f"({self.delta_limit*100:.0f}% of ${self.account_size:,.0f})"
                ),
                current_delta=current_delta,
                current_vega=current_vega,
                current_theta=current_theta,
                proposed_delta=delta,
                proposed_vega=vega,
                proposed_theta=theta,
                delta_after=delta_after,
                vega_after=vega_after,
                theta_after=theta_after,
                delta_headroom=delta_cap - abs(current_delta),
                vega_headroom=vega_cap - abs(current_vega),
                theta_headroom=theta_cap - abs(current_theta),
            )

        # 2. Net vega cap
        if abs(vega_after) > vega_cap:
            return LimitCheckResult(
                approved=False,
                reject_reason=(
                    f"Net vega breach: {vega_after:+.2f} exceeds cap ±{vega_cap:.2f}"
                ),
                current_delta=current_delta,
                current_vega=current_vega,
                current_theta=current_theta,
                proposed_delta=delta,
                proposed_vega=vega,
                proposed_theta=theta,
                delta_after=delta_after,
                vega_after=vega_after,
                theta_after=theta_after,
                delta_headroom=delta_cap - abs(current_delta),
                vega_headroom=vega_cap - abs(current_vega),
                theta_headroom=theta_cap - abs(current_theta),
            )

        # 3. Net theta cap (daily decay burden)
        if abs(theta_after) > theta_cap:
            return LimitCheckResult(
                approved=False,
                reject_reason=(
                    f"Net theta breach: {theta_after:+.2f} exceeds cap ±{theta_cap:.2f} "
                    f"({self.theta_limit*100:.0f}% of account per day)"
                ),
                current_delta=current_delta,
                current_vega=current_vega,
                current_theta=current_theta,
                proposed_delta=delta,
                proposed_vega=vega,
                proposed_theta=theta,
                delta_after=delta_after,
                vega_after=vega_after,
                theta_after=theta_after,
                delta_headroom=delta_cap - abs(current_delta),
                vega_headroom=vega_cap - abs(current_vega),
                theta_headroom=theta_cap - abs(current_theta),
            )

        # 4. Single-name delta concentration
        name_delta_after = self.underlying_delta(underlying) + delta
        name_delta_cap = self._single_name_delta_cap()
        if abs(name_delta_after) > name_delta_cap:
            return LimitCheckResult(
                approved=False,
                reject_reason=(
                    f"Single-name delta concentration breach in {underlying}: "
                    f"{name_delta_after:+.2f} exceeds {name_delta_cap:.2f} "
                    f"({self.single_name_pct*100:.0f}% of delta cap)"
                ),
                current_delta=current_delta,
                current_vega=current_vega,
                current_theta=current_theta,
                proposed_delta=delta,
                proposed_vega=vega,
                proposed_theta=theta,
                delta_after=delta_after,
                vega_after=vega_after,
                theta_after=theta_after,
                delta_headroom=delta_cap - abs(current_delta),
                vega_headroom=vega_cap - abs(current_vega),
                theta_headroom=theta_cap - abs(current_theta),
            )

        # 5. Single-name vega concentration
        name_vega_after = self.underlying_vega(underlying) + vega
        name_vega_cap = self._single_name_vega_cap()
        if abs(name_vega_after) > name_vega_cap:
            return LimitCheckResult(
                approved=False,
                reject_reason=(
                    f"Single-name vega concentration breach in {underlying}: "
                    f"{name_vega_after:+.2f} exceeds {name_vega_cap:.2f} "
                    f"({self.single_name_pct*100:.0f}% of vega cap)"
                ),
                current_delta=current_delta,
                current_vega=current_vega,
                current_theta=current_theta,
                proposed_delta=delta,
                proposed_vega=vega,
                proposed_theta=theta,
                delta_after=delta_after,
                vega_after=vega_after,
                theta_after=theta_after,
                delta_headroom=delta_cap - abs(current_delta),
                vega_headroom=vega_cap - abs(current_vega),
                theta_headroom=theta_cap - abs(current_theta),
            )

        # All checks passed
        return LimitCheckResult(
            approved=True,
            reject_reason=None,
            current_delta=current_delta,
            current_vega=current_vega,
            current_theta=current_theta,
            proposed_delta=delta,
            proposed_vega=vega,
            proposed_theta=theta,
            delta_after=delta_after,
            vega_after=vega_after,
            theta_after=theta_after,
            delta_headroom=round(delta_cap - abs(delta_after), 2),
            vega_headroom=round(vega_cap - abs(vega_after), 2),
            theta_headroom=round(theta_cap - abs(theta_after), 2),
        )

    def scale_to_fit(
        self,
        delta: float,
        vega: float,
        theta: float,
        underlying: str,
        contracts_requested: int,
    ) -> int:
        """Reduce contract count to the largest number that fits within all caps.

        Returns 0 if even 1 contract breaches a cap.
        Returns contracts_requested if all fit without scaling.
        Used when Kelly says N contracts but the book is near a limit.
        """
        if contracts_requested <= 0:
            return 0

        delta_per = delta / max(contracts_requested, 1)
        vega_per = vega / max(contracts_requested, 1)
        theta_per = theta / max(contracts_requested, 1)

        for n in range(contracts_requested, 0, -1):
            result = self.check_proposed(
                delta=delta_per * n,
                vega=vega_per * n,
                theta=theta_per * n,
                underlying=underlying,
            )
            if result.approved:
                return n

        return 0
