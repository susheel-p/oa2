"""Exit rules engine — Phase C2.

Evaluates seven exit rules in priority order for each open position.
Rules are evaluated against the current market context and position state.
The first rule that fires wins — priority order is by urgency.

Priority order:
    1. STOP_LOSS        — current loss >= stop threshold (immediate)
    2. TRAILING_STOP    — P&L drops trailing_stop_pct from peak, floored at entry (immediate)
    3. DTE_EMERGENCY    — any position DTE <= 5 (immediate, avoid assignment / illiquidity)
    4. HARD_EOD_CUTOFF  — 3:55 PM ET for intraday positions (immediate)
    5. FRIDAY_SWEEP     — Friday close of all sub-30-DTE positions (execute, avoid weekend gap)
    6. PROFIT_TARGET    — gain >= 50% of max_profit for short premium (execute)
    7. TIME_STOP        — position held > time_stop_days from entry (evaluate)
    8. REGIME_FLIP      — current regime != entry regime and consensus flipped (evaluate)

Urgency levels:
    IMMEDIATE   — close NOW, market order if needed
    EXECUTE     — close at next liquid fill (limit order ok)
    EVALUATE    — human or next-scan review recommended (not forced close)

Usage:
    engine = ExitEngine()
    decision = engine.evaluate(position, context)
    if decision.should_exit:
        print(decision.reason, decision.urgency)
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from tradingbot.core.clock import Clock, SystemClock
from tradingbot.core.config import (
    TRAILING_STOP_PCT,
    STOP_LOSS_PCT,
    DTE_EMERGENCY_SHORT,
    DTE_EMERGENCY_LONG,
    FRIDAY_SWEEP_DTE,
    FRIDAY_SWEEP_HOUR,
    PROFIT_TARGET_SHORT,
    PROFIT_TARGET_LONG,
)
from tradingbot.execution.monitor import OpenPosition


ET = ZoneInfo("America/New_York")

# Short-premium structures — have a short leg that risks assignment
_SHORT_PREMIUM_STRUCTURES = {
    "IRON_CONDOR", "SHORT_PREMIUM_FADE", "VERTICAL_CALL_SPREAD",
    "VERTICAL_PUT_SPREAD", "CALENDAR_CALL", "CALENDAR_PUT", "DIAGONAL_SPREAD",
}

# Configurable thresholds (all sourced from env via config.py)
_STOP_LOSS_PCT = STOP_LOSS_PCT          # default 0.75 — close at 75% of max loss
_TRAILING_STOP_PCT = TRAILING_STOP_PCT  # default 0.10 — 10% drop from peak
_PROFIT_TARGET_SHORT_PCT = PROFIT_TARGET_SHORT  # default 0.50 — 50% of credit
_PROFIT_TARGET_LONG_PCT = PROFIT_TARGET_LONG    # default 1.00 — double the premium
_DTE_EMERGENCY_SHORT = DTE_EMERGENCY_SHORT      # default 7 — short-premium exit DTE
_DTE_EMERGENCY_LONG = DTE_EMERGENCY_LONG        # default 5 — long structure exit DTE
_DEFAULT_TIME_STOP_DAYS = 21
_EOD_CUTOFF_HOUR = 15
_EOD_CUTOFF_MINUTE = 55
_FRIDAY_SWEEP_DTE = FRIDAY_SWEEP_DTE    # default 21 — sweep sub-3-week positions
_FRIDAY_SWEEP_HOUR = FRIDAY_SWEEP_HOUR  # default 14 — 2:00 PM ET


class ExitReason(Enum):
    EXPIRED = "expired"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    PROFIT_TARGET = "profit_target"
    DTE_EMERGENCY = "dte_emergency"
    FRIDAY_SWEEP = "friday_sweep"
    TIME_STOP = "time_stop"
    HARD_EOD_CUTOFF = "hard_eod_cutoff"
    REGIME_FLIP = "regime_flip"


class ExitUrgency(Enum):
    IMMEDIATE = "immediate"   # close NOW, market order acceptable
    EXECUTE = "execute"       # close at next liquid fill
    EVALUATE = "evaluate"     # flag for human or next-scan review


@dataclass
class ExitDecision:
    """Result of exit rule evaluation for one position.

    Two distinct outcomes a rule can produce:
      - should_exit=True   force-close the position now (urgency IMMEDIATE or EXECUTE)
      - needs_review=True  flag for human / next-scan review (urgency EVALUATE)
    A rule that fires sets exactly one of these — never both, never neither.
    When no rule fires, both are False and reason/urgency are None.
    """
    trade_id: str
    should_exit: bool
    reason: ExitReason | None
    urgency: ExitUrgency | None
    detail: str
    current_pnl: float
    current_dte: int
    needs_review: bool = False

    @property
    def is_immediate(self) -> bool:
        return self.urgency == ExitUrgency.IMMEDIATE

    @property
    def fired(self) -> bool:
        """True if any rule fired (close OR review)."""
        return self.should_exit or self.needs_review


class ExitEngine:
    """Evaluates exit rules in priority order for open positions.

    All thresholds default to env-configured values (see tradingbot/core/config.py)
    and can be overridden per-instance for testing.

    Args:
        stop_loss_pct: close when loss >= this fraction of max_loss (default 0.75).
        profit_target_short_pct: close short-premium at this fraction of max_profit (default 0.50).
        profit_target_long_pct: close long structures at this gain multiple (default 1.00 = 2×).
        dte_emergency_short: DTE threshold for short-premium structures (default 7).
        dte_emergency_long: DTE threshold for long structures (default 5).
        time_stop_days: flag for review after this many days (default 21).
    """

    def __init__(
        self,
        stop_loss_pct: float = _STOP_LOSS_PCT,
        profit_target_short_pct: float = _PROFIT_TARGET_SHORT_PCT,
        profit_target_long_pct: float = _PROFIT_TARGET_LONG_PCT,
        dte_emergency_short: int = _DTE_EMERGENCY_SHORT,
        dte_emergency_long: int = _DTE_EMERGENCY_LONG,
        time_stop_days: int = _DEFAULT_TIME_STOP_DAYS,
        clock: Clock | None = None,
    ):
        self.stop_loss_pct = stop_loss_pct
        self.profit_target_short_pct = profit_target_short_pct
        self.profit_target_long_pct = profit_target_long_pct
        self.dte_emergency_short = dte_emergency_short
        self.dte_emergency_long = dte_emergency_long
        self.time_stop_days = time_stop_days
        self.clock: Clock = clock or SystemClock()

    def evaluate(self, position: OpenPosition, context: dict[str, Any] | None = None) -> ExitDecision:
        """Evaluate all exit rules for one position in priority order.

        Args:
            position: the open position to evaluate.
            context: current market context (for regime flip check).

        Returns:
            ExitDecision — should_exit=True if any rule fires.
        """
        context = context or {}

        # Rule 0: Expired — options past their expiry date, remove from book immediately
        decision = self._check_expired(position)
        if decision.fired:
            return decision

        # Rule 1: Stop loss (highest priority — prevents blowup)
        decision = self._check_stop_loss(position)
        if decision.fired:
            return decision

        # Rule 2: Trailing stop (dynamic stop that reacts to reversals from peak)
        decision = self._check_trailing_stop(position)
        if decision.fired:
            return decision

        # Rule 3: DTE emergency (avoid assignment on short legs)
        decision = self._check_dte_emergency(position)
        if decision.fired:
            return decision

        # Rule 4: Hard EOD cutoff (intraday positions only)
        decision = self._check_hard_eod(position, context)
        if decision.fired:
            return decision

        # Rule 5: Friday sweep — close all sub-30-DTE positions on Fridays
        decision = self._check_friday_sweep(position)
        if decision.fired:
            return decision

        # Rule 6: Profit target
        decision = self._check_profit_target(position)
        if decision.fired:
            return decision

        # Rule 8: Time stop
        decision = self._check_time_stop(position)
        if decision.fired:
            return decision

        # Rule 9: Regime flip
        decision = self._check_regime_flip(position, context)
        if decision.fired:
            return decision

        # No rule fired
        return ExitDecision(
            trade_id=position.trade_id,
            should_exit=False,
            reason=None,
            urgency=None,
            detail="No exit condition met.",
            current_pnl=position.current_pnl,
            current_dte=position.current_dte,
        )

    def evaluate_all(
        self, positions: list[OpenPosition], context: dict[str, Any] | None = None
    ) -> list[ExitDecision]:
        """Evaluate all positions. Returns list of ExitDecision (one per position)."""
        return [self.evaluate(p, context) for p in positions]

    def exits_required(
        self, positions: list[OpenPosition], context: dict[str, Any] | None = None
    ) -> list[ExitDecision]:
        """Return only positions that must be force-closed (should_exit=True)."""
        return [d for d in self.evaluate_all(positions, context) if d.should_exit]

    def reviews_required(
        self, positions: list[OpenPosition], context: dict[str, Any] | None = None
    ) -> list[ExitDecision]:
        """Return only positions flagged for human / next-scan review."""
        return [d for d in self.evaluate_all(positions, context) if d.needs_review]

    # ------------------------------------------------------------------
    # Individual rules
    # ------------------------------------------------------------------

    def _check_expired(self, pos: OpenPosition) -> ExitDecision:
        """Rule 0: Position options have passed expiry (DTE <= 0). Remove from book."""
        if pos.current_dte <= 0:
            return ExitDecision(
                trade_id=pos.trade_id,
                should_exit=True,
                reason=ExitReason.EXPIRED,
                urgency=ExitUrgency.IMMEDIATE,
                detail=(
                    f"Options expired: DTE {pos.current_dte} — "
                    f"removing {pos.structure} from book (P&L {pos.current_pnl:+.2f})."
                ),
                current_pnl=pos.current_pnl,
                current_dte=pos.current_dte,
            )
        return self._no_exit(pos)

    def _check_stop_loss(self, pos: OpenPosition) -> ExitDecision:
        """Rule 1: Close when cumulative loss >= stop_loss_pct × max_loss.

        Trailing stop also applies from entry (not just after a peak), so even a
        position that never goes green gets cut if it drifts down past the threshold.
        """
        stop_threshold = -pos.max_loss * self.stop_loss_pct

        if pos.current_pnl <= stop_threshold:
            return ExitDecision(
                trade_id=pos.trade_id,
                should_exit=True,
                reason=ExitReason.STOP_LOSS,
                urgency=ExitUrgency.IMMEDIATE,
                detail=(
                    f"Stop loss hit: P&L {pos.current_pnl:+.2f} "
                    f"<= threshold {stop_threshold:+.2f} "
                    f"({self.stop_loss_pct*100:.0f}% of max_loss {pos.max_loss:.2f})"
                ),
                current_pnl=pos.current_pnl,
                current_dte=pos.current_dte,
            )
        return self._no_exit(pos)

    def _check_trailing_stop(self, pos: OpenPosition) -> ExitDecision:
        """Rule 2: Trailing stop — close if P&L drops trailing_stop_pct from peak,
        with floor at trailing_stop_pct loss from entry."""
        if pos.trailing_stop_pct <= 0:
            return self._no_exit(pos)

        threshold = pos.trailing_stop_threshold
        if pos.current_pnl <= threshold:
            return ExitDecision(
                trade_id=pos.trade_id,
                should_exit=True,
                reason=ExitReason.TRAILING_STOP,
                urgency=ExitUrgency.IMMEDIATE,
                detail=(
                    f"Trailing stop hit: P&L {pos.current_pnl:+.2f} "
                    f"<= stop level {threshold:+.2f} "
                    f"(peak {pos.peak_pnl:+.2f}, trail {pos.trailing_stop_pct*100:.0f}%)"
                ),
                current_pnl=pos.current_pnl,
                current_dte=pos.current_dte,
            )
        return self._no_exit(pos)

    def _check_dte_emergency(self, pos: OpenPosition) -> ExitDecision:
        """Rule 3: Structure-aware DTE emergency exit.

        Short-premium structures (IC, verticals, calendars) exit at 7 DTE —
        the full last week — to avoid assignment risk and gamma spikes.
        Long structures exit at 5 DTE when liquidity dries up and spreads widen.
        """
        is_short_premium = pos.structure in _SHORT_PREMIUM_STRUCTURES
        threshold = self.dte_emergency_short if is_short_premium else self.dte_emergency_long
        reason_text = "assignment / gamma risk" if is_short_premium else "illiquidity / theta crush"

        if pos.current_dte <= threshold:
            return ExitDecision(
                trade_id=pos.trade_id,
                should_exit=True,
                reason=ExitReason.DTE_EMERGENCY,
                urgency=ExitUrgency.IMMEDIATE,
                detail=(
                    f"DTE emergency: {pos.current_dte} DTE <= {threshold} "
                    f"on {pos.structure} — closing to avoid {reason_text}."
                ),
                current_pnl=pos.current_pnl,
                current_dte=pos.current_dte,
            )
        return self._no_exit(pos)

    def _check_hard_eod(self, pos: OpenPosition, context: dict[str, Any]) -> ExitDecision:
        """Rule 4: Force-close intraday positions at 3:55 PM ET."""
        is_intraday = pos.structure in {
            "LONG_GAMMA_SCALP", "SHORT_PREMIUM_FADE",
        } or pos.entry_dte <= 1

        if not is_intraday:
            return self._no_exit(pos)

        now_et = self.clock.now_et()
        cutoff_reached = (
            now_et.hour > _EOD_CUTOFF_HOUR or
            (now_et.hour == _EOD_CUTOFF_HOUR and now_et.minute >= _EOD_CUTOFF_MINUTE)
        )

        if cutoff_reached:
            return ExitDecision(
                trade_id=pos.trade_id,
                should_exit=True,
                reason=ExitReason.HARD_EOD_CUTOFF,
                urgency=ExitUrgency.IMMEDIATE,
                detail=(
                    f"Hard EOD cutoff: {now_et.strftime('%H:%M')} ET >= 15:55. "
                    f"Intraday position ({pos.structure}) force-closed."
                ),
                current_pnl=pos.current_pnl,
                current_dte=pos.current_dte,
            )
        return self._no_exit(pos)

    def _check_friday_sweep(self, pos: OpenPosition) -> ExitDecision:
        """Rule 5: On Fridays at or after 2:00 PM ET, close positions with DTE <= 30.

        Positions with > 30 DTE have enough time value to carry safely over the
        weekend. Positions at or under 30 DTE are swept at 2pm so there is still
        liquidity to get a clean fill before the close.
        """
        now_et = self.clock.now_et()
        if now_et.weekday() != 4:  # 4 = Friday
            return self._no_exit(pos)

        if now_et.hour < _FRIDAY_SWEEP_HOUR:
            return self._no_exit(pos)

        if pos.current_dte <= _FRIDAY_SWEEP_DTE:
            return ExitDecision(
                trade_id=pos.trade_id,
                should_exit=True,
                reason=ExitReason.FRIDAY_SWEEP,
                urgency=ExitUrgency.EXECUTE,
                detail=(
                    f"Friday sweep: {pos.current_dte} DTE <= {_FRIDAY_SWEEP_DTE} — "
                    f"closing at {now_et.strftime('%H:%M')} ET before weekend."
                ),
                current_pnl=pos.current_pnl,
                current_dte=pos.current_dte,
            )
        return self._no_exit(pos)

    def _check_profit_target(self, pos: OpenPosition) -> ExitDecision:
        """Rule 7: Structure-aware profit target.

        Short-premium (IC, verticals): close at 50% of max credit received.
        Long structures (long calls/puts): let winners run — close at 100% gain
        (i.e. the position has doubled the premium paid).
        """
        if pos.max_profit <= 0:
            return self._no_exit(pos)

        is_short_premium = pos.structure in _SHORT_PREMIUM_STRUCTURES
        pct = self.profit_target_short_pct if is_short_premium else self.profit_target_long_pct
        target = pos.max_profit * pct

        if pos.current_pnl >= target:
            label = "50% of credit" if is_short_premium else "100% gain (2×)"
            return ExitDecision(
                trade_id=pos.trade_id,
                should_exit=True,
                reason=ExitReason.PROFIT_TARGET,
                urgency=ExitUrgency.EXECUTE,
                detail=(
                    f"Profit target reached: P&L {pos.current_pnl:+.2f} "
                    f">= {label} — target {target:.2f} on {pos.structure}."
                ),
                current_pnl=pos.current_pnl,
                current_dte=pos.current_dte,
            )
        return self._no_exit(pos)

    def _check_time_stop(self, pos: OpenPosition) -> ExitDecision:
        """Rule 6: Flag for review after time_stop_days from entry."""
        age_days = pos.age_days_against(self.clock)
        if age_days >= self.time_stop_days:
            return ExitDecision(
                trade_id=pos.trade_id,
                should_exit=False,
                needs_review=True,
                reason=ExitReason.TIME_STOP,
                urgency=ExitUrgency.EVALUATE,
                detail=(
                    f"Time stop: position held {age_days:.1f} days "
                    f">= {self.time_stop_days} day limit. Review for close."
                ),
                current_pnl=pos.current_pnl,
                current_dte=pos.current_dte,
            )
        return self._no_exit(pos)

    def _check_regime_flip(self, pos: OpenPosition, context: dict[str, Any]) -> ExitDecision:
        """Rule 7: Flag if current regime differs from entry regime.

        Only evaluates if current_regime and consensus direction are in context.
        When the regime has flipped AND the consensus direction also flipped,
        the position's edge thesis is invalidated — flag for evaluation.
        """
        current_regime = context.get("regime_id")
        consensus_direction = context.get("consensus_direction")

        if current_regime is None:
            return self._no_exit(pos)

        regime_flipped = current_regime != pos.entry_regime

        if not regime_flipped:
            return self._no_exit(pos)

        # Regime flipped — check if consensus direction also flipped against the trade
        direction_conflict = False
        if consensus_direction:
            if pos.direction == "BULLISH" and consensus_direction == "BEARISH":
                direction_conflict = True
            elif pos.direction == "BEARISH" and consensus_direction == "BULLISH":
                direction_conflict = True

        if direction_conflict:
            return ExitDecision(
                trade_id=pos.trade_id,
                should_exit=True,
                needs_review=False,
                reason=ExitReason.REGIME_FLIP,
                urgency=ExitUrgency.EXECUTE,
                detail=(
                    f"Regime flip + direction conflict: entry regime {pos.entry_regime} → "
                    f"current {current_regime}. Consensus ({consensus_direction}) "
                    f"opposes trade direction ({pos.direction}) — closing."
                ),
                current_pnl=pos.current_pnl,
                current_dte=pos.current_dte,
            )

        # Regime flipped but direction not conflicting — soft flag only
        if regime_flipped:
            return ExitDecision(
                trade_id=pos.trade_id,
                should_exit=False,
                needs_review=True,
                reason=ExitReason.REGIME_FLIP,
                urgency=ExitUrgency.EVALUATE,
                detail=(
                    f"Regime flip: entry regime {pos.entry_regime} → "
                    f"current {current_regime}. Monitor closely — edge thesis may have shifted."
                ),
                current_pnl=pos.current_pnl,
                current_dte=pos.current_dte,
            )

        return self._no_exit(pos)

    @staticmethod
    def _no_exit(pos: OpenPosition) -> ExitDecision:
        return ExitDecision(
            trade_id=pos.trade_id,
            should_exit=False,
            reason=None,
            urgency=None,
            detail="",
            current_pnl=pos.current_pnl,
            current_dte=pos.current_dte,
        )
