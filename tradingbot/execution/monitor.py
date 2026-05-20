"""Position monitor — Phase C1.

Maintains a registry of all open options positions and their current
mark-to-market status. This is the data store the exit engine reads.

Each position records the entry state (regime, Greeks, P&L targets) so
the exit engine can evaluate drift, regime flips, and stop levels without
querying the broker on every tick.

Persistence: the monitor is in-memory only. On restart it must be rebuilt
from the broker's open-positions list (Phase E wiring). The data model is
intentionally broker-agnostic.

Usage:
    monitor = PositionMonitor()
    monitor.add(position)
    monitor.mark_to_market("trade_001", current_price=450.25, current_pnl=-45.00)
    due = monitor.positions_due_for_exit_check(max_age_seconds=300)
"""

from __future__ import annotations

import datetime as _dt
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from tradingbot.core.clock import Clock, SystemClock


@dataclass
class Leg:
    """A single option leg of a multi-leg position.

    Stored on OpenPosition so the book can re-mark Greeks from the live chain
    each cycle instead of relying on the entry-time snapshot.

    `side` is +1 for long, -1 for short. `contracts` is the per-leg multiplier
    (typically 1; vertical / IC structures use 1 per leg with the position-level
    `contracts` scaling the whole structure).
    """
    underlying: str
    expiry: _dt.date
    strike: float
    right: str         # "C" or "P"
    side: int          # +1 long, -1 short
    contracts: int = 1


ChainProvider = Callable[[str, _dt.date, float, str], dict[str, float] | None]
"""(underlying, expiry, strike, right) -> {delta, vega, theta, price} or None."""


@dataclass
class OpenPosition:
    """Full state of one open options position.

    Greeks are in dollar terms:
        delta:  dollars moved per $1 move in underlying
        vega:   dollars moved per 1% IV move
        theta:  daily theta (negative = time-decay cost)

    P&L targets are set at trade approval time by the sizing engine.
    """
    trade_id: str
    ticker: str
    underlying: str
    structure: str                  # e.g. "IRON_CONDOR", "VERTICAL_CALL_SPREAD"
    direction: str                  # "BULLISH" | "BEARISH" | "NEUTRAL"

    # Entry snapshot
    entry_price: float              # underlying price at entry
    entry_premium: float            # net premium paid/received per contract (dollars)
    entry_time: float               # unix timestamp
    entry_regime: int               # regime_id [0-7] at entry
    entry_dte: int                  # DTE at entry

    # Risk parameters (set by sizing engine at approval)
    contracts: int
    max_profit_per_contract: float  # max achievable profit per contract
    max_loss_per_contract: float    # max loss per contract (positive number)
    stop_loss_pct: float = 1.00     # close when loss >= stop_loss_pct × max_loss
    profit_target_pct: float = 0.50 # close when gain >= profit_target_pct × max_profit

    # Greeks at entry (dollar terms)
    delta: float = 0.0
    vega: float = 0.0
    theta: float = 0.0

    # Live state (updated by mark_to_market)
    current_pnl: float = 0.0        # cumulative P&L in dollars (negative = loss)
    current_underlying_price: float = 0.0
    current_dte: int = 0
    last_checked: float = field(default_factory=time.time)

    # Legs are optional; structures registered before the legs refactor (and
    # most existing tests) leave this empty and rely on entry-time Greeks.
    legs: list[Leg] = field(default_factory=list)

    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def max_profit(self) -> float:
        return self.max_profit_per_contract * self.contracts

    @property
    def max_loss(self) -> float:
        return self.max_loss_per_contract * self.contracts

    @property
    def stop_loss_dollars(self) -> float:
        return -self.max_loss * self.stop_loss_pct

    @property
    def profit_target_dollars(self) -> float:
        return self.max_profit * self.profit_target_pct

    @property
    def pnl_pct_of_max_profit(self) -> float:
        if self.max_profit <= 0:
            return 0.0
        return self.current_pnl / self.max_profit

    @property
    def pnl_pct_of_max_loss(self) -> float:
        if self.max_loss <= 0:
            return 0.0
        return self.current_pnl / (-self.max_loss)

    @property
    def age_seconds(self) -> float:
        """Wall-clock age. Prefer `age_seconds_against(clock)` in pipelines /
        backtests so replay time, not OS time, drives age."""
        return time.time() - self.entry_time

    @property
    def age_days(self) -> float:
        return self.age_seconds / 86400.0

    def age_seconds_against(self, clock: Clock) -> float:
        return clock.now() - self.entry_time

    def age_days_against(self, clock: Clock) -> float:
        return self.age_seconds_against(clock) / 86400.0

    def summary(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "ticker": self.ticker,
            "structure": self.structure,
            "direction": self.direction,
            "contracts": self.contracts,
            "current_pnl": round(self.current_pnl, 2),
            "current_dte": self.current_dte,
            "pnl_pct_of_max_profit": round(self.pnl_pct_of_max_profit, 3),
            "stop_loss_dollars": round(self.stop_loss_dollars, 2),
            "profit_target_dollars": round(self.profit_target_dollars, 2),
            "entry_regime": self.entry_regime,
            "age_days": round(self.age_days, 1),
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "trade_id": self.trade_id,
            "ticker": self.ticker,
            "underlying": self.underlying,
            "structure": self.structure,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "entry_premium": self.entry_premium,
            "entry_time": self.entry_time,
            "entry_regime": self.entry_regime,
            "entry_dte": self.entry_dte,
            "contracts": self.contracts,
            "max_profit_per_contract": self.max_profit_per_contract,
            "max_loss_per_contract": self.max_loss_per_contract,
            "stop_loss_pct": self.stop_loss_pct,
            "profit_target_pct": self.profit_target_pct,
            "delta": self.delta,
            "vega": self.vega,
            "theta": self.theta,
            "current_pnl": self.current_pnl,
            "current_underlying_price": self.current_underlying_price,
            "current_dte": self.current_dte,
            "legs": [
                {
                    "underlying": l.underlying,
                    "expiry": l.expiry.isoformat(),
                    "strike": l.strike,
                    "right": l.right,
                    "side": l.side,
                    "contracts": l.contracts,
                }
                for l in self.legs
            ],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OpenPosition":
        """Deserialize from JSON-compatible dict."""
        legs = [
            Leg(
                underlying=l["underlying"],
                expiry=_dt.date.fromisoformat(l["expiry"]),
                strike=l["strike"],
                right=l["right"],
                side=l["side"],
                contracts=l["contracts"],
            )
            for l in d.get("legs", [])
        ]
        return cls(
            trade_id=d["trade_id"],
            ticker=d["ticker"],
            underlying=d["underlying"],
            structure=d["structure"],
            direction=d["direction"],
            entry_price=d["entry_price"],
            entry_premium=d["entry_premium"],
            entry_time=d["entry_time"],
            entry_regime=d["entry_regime"],
            entry_dte=d["entry_dte"],
            contracts=d["contracts"],
            max_profit_per_contract=d["max_profit_per_contract"],
            max_loss_per_contract=d["max_loss_per_contract"],
            stop_loss_pct=d.get("stop_loss_pct", 1.0),
            profit_target_pct=d.get("profit_target_pct", 0.5),
            delta=d.get("delta", 0.0),
            vega=d.get("vega", 0.0),
            theta=d.get("theta", 0.0),
            current_pnl=d.get("current_pnl", 0.0),
            current_underlying_price=d.get("current_underlying_price", 0.0),
            current_dte=d.get("current_dte", 0),
            legs=legs,
        )


class PositionMonitor:
    """In-memory registry of open positions.

    Thread-safety: not thread-safe by design — the pipeline is synchronous.
    If concurrent access is needed, wrap with a lock at the pipeline level.
    """

    def __init__(self, clock: Clock | None = None):
        self._positions: dict[str, OpenPosition] = {}
        self._clock: Clock = clock or SystemClock()

    @property
    def clock(self) -> Clock:
        return self._clock

    # ------------------------------------------------------------------
    # Registry management
    # ------------------------------------------------------------------

    def add(self, position: OpenPosition) -> None:
        """Register a new open position."""
        self._positions[position.trade_id] = position

    def remove(self, trade_id: str) -> OpenPosition | None:
        """Remove and return a closed position. Returns None if not found."""
        return self._positions.pop(trade_id, None)

    def get(self, trade_id: str) -> OpenPosition | None:
        """Return a position by trade_id, or None."""
        return self._positions.get(trade_id)

    def clear(self) -> None:
        """Remove all positions (end-of-day reset)."""
        self._positions.clear()

    # ------------------------------------------------------------------
    # Mark-to-market
    # ------------------------------------------------------------------

    def mark_to_market(
        self,
        trade_id: str,
        current_pnl: float,
        current_underlying_price: float | None = None,
        current_dte: int | None = None,
    ) -> OpenPosition | None:
        """Update P&L and live state for one position.

        Args:
            trade_id: identifies the position.
            current_pnl: cumulative P&L in dollars (negative = loss).
            current_underlying_price: current underlying spot price.
            current_dte: updated DTE (decrements each day).

        Returns:
            Updated OpenPosition, or None if not found.
        """
        pos = self._positions.get(trade_id)
        if pos is None:
            return None

        pos.current_pnl = current_pnl
        pos.last_checked = self._clock.now()
        if current_underlying_price is not None:
            pos.current_underlying_price = current_underlying_price
        if current_dte is not None:
            pos.current_dte = current_dte

        return pos

    def mark_all(self, updates: dict[str, dict]) -> None:
        """Batch mark-to-market.

        Args:
            updates: {trade_id: {"current_pnl": float, "current_dte": int, ...}}
        """
        for trade_id, data in updates.items():
            self.mark_to_market(
                trade_id=trade_id,
                current_pnl=data.get("current_pnl", 0.0),
                current_underlying_price=data.get("current_underlying_price"),
                current_dte=data.get("current_dte"),
            )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def all_positions(self) -> list[OpenPosition]:
        """Return all open positions (copy of values list)."""
        return list(self._positions.values())

    def positions_for(self, underlying: str) -> list[OpenPosition]:
        """Return all positions for a given underlying ticker."""
        return [p for p in self._positions.values() if p.underlying == underlying]

    def position_count(self) -> int:
        return len(self._positions)

    def positions_due_for_exit_check(self, max_age_seconds: float = 300.0) -> list[OpenPosition]:
        """Return positions not checked within max_age_seconds."""
        cutoff = self._clock.now() - max_age_seconds
        return [p for p in self._positions.values() if p.last_checked < cutoff]

    def positions_near_expiry(self, dte_threshold: int = 2) -> list[OpenPosition]:
        """Return positions with current_dte <= threshold."""
        return [p for p in self._positions.values() if p.current_dte <= dte_threshold]

    def net_pnl(self) -> float:
        """Sum of current P&L across all open positions."""
        return sum(p.current_pnl for p in self._positions.values())

    # ------------------------------------------------------------------
    # Mark-to-market against a live options chain
    # ------------------------------------------------------------------

    def remark_greeks(self, chain_provider: ChainProvider) -> list[str]:
        """Recompute each position's delta/vega/theta from the current chain.

        For every position with legs registered, look up per-contract greeks for
        each leg, apply side (+1 long / -1 short) and per-leg/structure
        contracts, and sum into position-level dollar Greeks. Also refreshes
        `current_dte` from the clock-relative days to nearest leg expiry, and
        `last_checked`.

        Positions without legs (legacy / synthetic test positions) are left
        untouched and reported in the returned skipped-list.

        Args:
            chain_provider: callable (underlying, expiry, strike, right) ->
                {"delta": float, "vega": float, "theta": float, "price": float}
                or None if the contract is missing from the chain snapshot.

        Returns:
            List of trade_ids that could not be fully re-marked (no legs, or
            at least one leg missing from the chain). The caller may choose to
            force-exit, alert, or keep the stale snapshot.
        """
        today_et = self._clock.now_et().date()
        skipped: list[str] = []

        for pos in self._positions.values():
            if not pos.legs:
                skipped.append(pos.trade_id)
                continue

            d_sum = v_sum = t_sum = 0.0
            missing = False
            min_dte: int | None = None

            for leg in pos.legs:
                quote = chain_provider(leg.underlying, leg.expiry, leg.strike, leg.right)
                if quote is None:
                    missing = True
                    break
                mult = leg.side * leg.contracts * pos.contracts
                d_sum += quote.get("delta", 0.0) * mult
                v_sum += quote.get("vega", 0.0) * mult
                t_sum += quote.get("theta", 0.0) * mult
                dte = (leg.expiry - today_et).days
                min_dte = dte if min_dte is None else min(min_dte, dte)

            if missing:
                skipped.append(pos.trade_id)
                continue

            pos.delta = d_sum
            pos.vega = v_sum
            pos.theta = t_sum
            if min_dte is not None:
                pos.current_dte = min_dte
            pos.last_checked = self._clock.now()

        return skipped

    def book_summary(self) -> dict[str, Any]:
        """Aggregate book summary for logging."""
        positions = self.all_positions()
        return {
            "open_positions": len(positions),
            "net_pnl": round(self.net_pnl(), 2),
            "positions": [p.summary() for p in positions],
        }

    def save(self, path: str) -> None:
        """Persist all open positions to JSON file."""
        import json
        from pathlib import Path
        data = [p.to_dict() for p in self._positions.values()]
        Path(path).write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: str, clock: Clock | None = None) -> "PositionMonitor":
        """Load open positions from JSON file into a new monitor."""
        import json
        from pathlib import Path
        monitor = cls(clock=clock)
        if Path(path).exists():
            data = json.loads(Path(path).read_text())
            for d in data:
                monitor.add(OpenPosition.from_dict(d))
        return monitor
