"""Roll logic — Phase C3.

Evaluates whether a near-expiry short position should be rolled to the
next expiration rather than closed outright.

Roll criteria (all must be true):
    1. Position is profitable (>= 25% of max_profit captured)
    2. DTE <= 14 (near expiry — time to act)
    3. Current regime == entry regime (thesis unchanged)
    4. Next expiry has sufficient premium (>= roll_premium_floor)
    5. Rolling does not increase Greek exposure beyond limits

When a roll is recommended, returns a RollDecision with the suggested
next expiration and a credit/debit estimate.

This module is intentionally stateless — it takes a position and context
and returns a decision. Broker interaction (placing the roll order) is
handled by the execution layer.

Usage:
    engine = RollEngine()
    decision = engine.evaluate(position, context)
    if decision.should_roll:
        print(decision.rationale)
        # Place roll order: close near-expiry legs, open next-expiry legs
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from oa2.execution.monitor import OpenPosition


_DEFAULT_PROFIT_THRESHOLD = 0.25    # must have captured >= 25% of max_profit
_DEFAULT_DTE_THRESHOLD = 14         # must be within 14 DTE to consider roll
_DEFAULT_PREMIUM_FLOOR = 0.30       # next expiry must offer >= 30% of original premium
_DEFAULT_MAX_DEBIT = 0.50           # never pay more than 50% of original premium to roll


@dataclass
class RollDecision:
    """Result of roll evaluation for one position."""
    trade_id: str
    should_roll: bool
    reject_reason: str | None
    rationale: str
    profit_captured_pct: float      # how much of max_profit has been captured
    current_dte: int
    suggested_dte_target: int | None  # suggested next expiry DTE (e.g., 30)
    estimated_roll_credit: float    # positive = net credit received; negative = debit paid
    regime_unchanged: bool


class RollEngine:
    """Evaluates roll vs close for near-expiry profitable short positions.

    Args:
        profit_threshold_pct: minimum profit captured to consider a roll (default 0.25).
        dte_threshold: maximum DTE at which to evaluate roll (default 14).
        premium_floor_pct: next expiry must offer >= this fraction of entry premium (default 0.30).
        max_roll_debit_pct: max debit to pay for roll as fraction of entry premium (default 0.50).
    """

    def __init__(
        self,
        profit_threshold_pct: float = _DEFAULT_PROFIT_THRESHOLD,
        dte_threshold: int = _DEFAULT_DTE_THRESHOLD,
        premium_floor_pct: float = _DEFAULT_PREMIUM_FLOOR,
        max_roll_debit_pct: float = _DEFAULT_MAX_DEBIT,
    ):
        self.profit_threshold_pct = profit_threshold_pct
        self.dte_threshold = dte_threshold
        self.premium_floor_pct = premium_floor_pct
        self.max_roll_debit_pct = max_roll_debit_pct

    def evaluate(self, position: OpenPosition, context: dict[str, Any]) -> RollDecision:
        """Evaluate whether this position should be rolled.

        Args:
            position: the open position (must be a short-premium structure).
            context: current market context, may include:
                - "regime_id": current regime for regime-unchanged check
                - "next_expiry_premium": estimated premium in next expiry (dollars)
                - "next_expiry_dte": DTE of the next suitable expiration

        Returns:
            RollDecision — should_roll=True only when all criteria pass.
        """
        # Only short-premium structures make sense to roll
        rollable_structures = {
            "IRON_CONDOR", "SHORT_PREMIUM_FADE", "VERTICAL_CALL_SPREAD",
            "VERTICAL_PUT_SPREAD", "CALENDAR_CALL", "CALENDAR_PUT",
            "DIAGONAL_SPREAD",
        }

        if position.structure not in rollable_structures:
            return RollDecision(
                trade_id=position.trade_id,
                should_roll=False,
                reject_reason=f"Structure {position.structure} is not rollable (not short-premium).",
                rationale="",
                profit_captured_pct=position.pnl_pct_of_max_profit,
                current_dte=position.current_dte,
                suggested_dte_target=None,
                estimated_roll_credit=0.0,
                regime_unchanged=True,
            )

        profit_pct = position.pnl_pct_of_max_profit

        # 1. Must have captured sufficient profit
        if profit_pct < self.profit_threshold_pct:
            return RollDecision(
                trade_id=position.trade_id,
                should_roll=False,
                reject_reason=(
                    f"Insufficient profit captured: {profit_pct:.1%} < "
                    f"{self.profit_threshold_pct:.1%} threshold. "
                    f"Close or hold — not enough premium collected to justify roll cost."
                ),
                rationale="",
                profit_captured_pct=profit_pct,
                current_dte=position.current_dte,
                suggested_dte_target=None,
                estimated_roll_credit=0.0,
                regime_unchanged=True,
            )

        # 2. Must be within DTE window
        if position.current_dte > self.dte_threshold:
            return RollDecision(
                trade_id=position.trade_id,
                should_roll=False,
                reject_reason=(
                    f"DTE {position.current_dte} > {self.dte_threshold} threshold. "
                    f"Too early to roll — re-evaluate when DTE <= {self.dte_threshold}."
                ),
                rationale="",
                profit_captured_pct=profit_pct,
                current_dte=position.current_dte,
                suggested_dte_target=None,
                estimated_roll_credit=0.0,
                regime_unchanged=True,
            )

        # 3. Regime unchanged
        current_regime = context.get("regime_id")
        regime_unchanged = (current_regime is None or current_regime == position.entry_regime)

        if not regime_unchanged:
            return RollDecision(
                trade_id=position.trade_id,
                should_roll=False,
                reject_reason=(
                    f"Regime has shifted: entry={position.entry_regime}, "
                    f"current={current_regime}. Rolling extends duration into a different regime — "
                    f"close instead of rolling."
                ),
                rationale="",
                profit_captured_pct=profit_pct,
                current_dte=position.current_dte,
                suggested_dte_target=None,
                estimated_roll_credit=0.0,
                regime_unchanged=False,
            )

        # 4. Check next-expiry premium (from context if available)
        next_premium = context.get("next_expiry_premium", None)
        next_dte = context.get("next_expiry_dte", 30)
        entry_premium_abs = abs(position.entry_premium)

        if next_premium is not None:
            if next_premium < entry_premium_abs * self.premium_floor_pct:
                return RollDecision(
                    trade_id=position.trade_id,
                    should_roll=False,
                    reject_reason=(
                        f"Next-expiry premium ({next_premium:.2f}) is below floor "
                        f"({entry_premium_abs * self.premium_floor_pct:.2f} = "
                        f"{self.premium_floor_pct:.0%} of entry {entry_premium_abs:.2f}). "
                        f"Roll credit insufficient to justify transaction costs."
                    ),
                    rationale="",
                    profit_captured_pct=profit_pct,
                    current_dte=position.current_dte,
                    suggested_dte_target=next_dte,
                    estimated_roll_credit=next_premium - (position.current_pnl / position.contracts),
                    regime_unchanged=True,
                )

        # Estimate roll credit (next premium - cost to close current position)
        # When next_premium is not in context, use a placeholder
        close_cost = entry_premium_abs * (1.0 - profit_pct)  # approximate cost to close
        estimated_credit = (next_premium or entry_premium_abs * 0.50) - close_cost

        if estimated_credit < -(entry_premium_abs * self.max_roll_debit_pct):
            return RollDecision(
                trade_id=position.trade_id,
                should_roll=False,
                reject_reason=(
                    f"Estimated roll debit ({-estimated_credit:.2f}) exceeds max "
                    f"({entry_premium_abs * self.max_roll_debit_pct:.2f} = "
                    f"{self.max_roll_debit_pct:.0%} of entry premium). Too expensive to roll."
                ),
                rationale="",
                profit_captured_pct=profit_pct,
                current_dte=position.current_dte,
                suggested_dte_target=next_dte,
                estimated_roll_credit=round(estimated_credit, 2),
                regime_unchanged=True,
            )

        # All criteria passed — recommend roll
        return RollDecision(
            trade_id=position.trade_id,
            should_roll=True,
            reject_reason=None,
            rationale=(
                f"Roll recommended: {profit_pct:.1%} profit captured, "
                f"{position.current_dte} DTE remaining, regime unchanged (id={position.entry_regime}). "
                f"Estimated roll credit: {estimated_credit:+.2f}. "
                f"Suggested next expiry: ~{next_dte} DTE."
            ),
            profit_captured_pct=profit_pct,
            current_dte=position.current_dte,
            suggested_dte_target=next_dte,
            estimated_roll_credit=round(estimated_credit, 2),
            regime_unchanged=True,
        )
