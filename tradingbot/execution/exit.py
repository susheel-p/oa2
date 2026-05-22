"""Exit rules engine — Phase C2.

Evaluates six exit rules in priority order for each open position.
Rules are evaluated against the current market context and position state.
The first rule that fires wins — priority order is by urgency.

Priority order:
    1. STOP_LOSS        — current loss >= stop threshold (immediate)
    2. TRAILING_STOP    — P&L drops trailing_stop_pct from peak, floored at entry (immediate)
    3. DTE_EMERGENCY    — short leg DTE < 2 (immediate, avoid assignment)
    4. HARD_EOD_CUTOFF  — 3:55 PM ET for intraday positions (immediate)
    5. PROFIT_TARGET    — gain >= 50% of max_profit for short premium (execute)
    6. TIME_STOP        — position held > time_stop_days from entry (evaluate)
    7. REGIME_FLIP      — current regime != entry regime and consensus flipped (evaluate)

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
from tradingbot.core.config import TRAILING_STOP_PCT
from tradingbot.execution.monitor import OpenPosition


ET = ZoneInfo("America/New_York")

# Configurable thresholds
_STOP_LOSS_PCT = 1.00           # close when loss >= 100% of max_loss
_TRAILING_STOP_PCT = TRAILING_STOP_PCT  # close if P&L drops X% from peak (configured via TRAILING_STOP_PCT env var)
_PROFIT_TARGET_PCT = 0.50       # close when gain >= 50% of max_profit (short premium)
_DTE_EMERGENCY_THRESHOLD = 2    # close any short leg with DTE <= this
_DEFAULT_TIME_STOP_DAYS = 21    # close long positions after this many days
_EOD_CUTOFF_HOUR = 15           # 3:00 PM ET
_EOD_CUTOFF_MINUTE = 55         # 3:55 PM ET


class ExitReason(Enum):
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    PROFIT_TARGET = "profit_target"
    DTE_EMERGENCY = "dte_emergency"
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

    Args:
        stop_loss_pct: close when loss >= this fraction of max_loss (default 1.0 = full stop).
        profit_target_pct: close when gain >= this fraction of max_profit (default 0.50).
        dte_emergency_threshold: close short legs at or below this DTE (default 2).
        time_stop_days: close long positions after this many days (default 21).
    """

    def __init__(
        self,
        stop_loss_pct: float = _STOP_LOSS_PCT,
        profit_target_pct: float = _PROFIT_TARGET_PCT,
        dte_emergency_threshold: int = _DTE_EMERGENCY_THRESHOLD,
        time_stop_days: int = _DEFAULT_TIME_STOP_DAYS,
        clock: Clock | None = None,
    ):
        self.stop_loss_pct = stop_loss_pct
        self.profit_target_pct = profit_target_pct
        self.dte_emergency_threshold = dte_emergency_threshold
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

        # Rule 5: Profit target
        decision = self._check_profit_target(position)
        if decision.fired:
            return decision

        # Rule 6: Time stop
        decision = self._check_time_stop(position)
        if decision.fired:
            return decision

        # Rule 7: Regime flip
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

    def _check_stop_loss(self, pos: OpenPosition) -> ExitDecision:
        """Rule 1: Close when cumulative loss >= stop_loss_pct × max_loss."""
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
        """Rule 2: Close any position with DTE <= threshold to avoid assignment."""
        has_short_leg = pos.structure in {
            "IRON_CONDOR", "SHORT_PREMIUM_FADE", "VERTICAL_CALL_SPREAD",
            "VERTICAL_PUT_SPREAD", "CALENDAR_CALL", "CALENDAR_PUT",
            "DIAGONAL_SPREAD",
        }

        if has_short_leg and pos.current_dte <= self.dte_emergency_threshold:
            return ExitDecision(
                trade_id=pos.trade_id,
                should_exit=True,
                reason=ExitReason.DTE_EMERGENCY,
                urgency=ExitUrgency.IMMEDIATE,
                detail=(
                    f"DTE emergency: {pos.current_dte} DTE <= {self.dte_emergency_threshold} "
                    f"on {pos.structure} — closing to avoid assignment risk."
                ),
                current_pnl=pos.current_pnl,
                current_dte=pos.current_dte,
            )
        return self._no_exit(pos)

    def _check_hard_eod(self, pos: OpenPosition, context: dict[str, Any]) -> ExitDecision:
        """Rule 3: Force-close intraday positions at 3:55 PM ET."""
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

    def _check_profit_target(self, pos: OpenPosition) -> ExitDecision:
        """Rule 4: Close when gain >= profit_target_pct × max_profit."""
        if pos.max_profit <= 0:
            return self._no_exit(pos)

        target = pos.max_profit * self.profit_target_pct

        # Short premium structures: profit = premium received → positive P&L is profit
        # Long structures: profit = premium appreciation
        if pos.current_pnl >= target:
            return ExitDecision(
                trade_id=pos.trade_id,
                should_exit=True,
                reason=ExitReason.PROFIT_TARGET,
                urgency=ExitUrgency.EXECUTE,
                detail=(
                    f"Profit target reached: P&L {pos.current_pnl:+.2f} "
                    f">= {self.profit_target_pct*100:.0f}% of max_profit {pos.max_profit:.2f} "
                    f"(target {target:.2f})"
                ),
                current_pnl=pos.current_pnl,
                current_dte=pos.current_dte,
            )
        return self._no_exit(pos)

    def _check_time_stop(self, pos: OpenPosition) -> ExitDecision:
        """Rule 5: Flag for review after time_stop_days from entry."""
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
        """Rule 6: Flag if current regime differs from entry regime.

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
                should_exit=False,
                needs_review=True,
                reason=ExitReason.REGIME_FLIP,
                urgency=ExitUrgency.EVALUATE,
                detail=(
                    f"Regime flip: entry regime {pos.entry_regime} → "
                    f"current {current_regime}. Consensus direction "
                    f"({consensus_direction}) conflicts with trade direction "
                    f"({pos.direction}). Consider closing."
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
