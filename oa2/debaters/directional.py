"""Directional debater — tape/momentum perspective (quant-only, v2).

Phase D5: Additive conviction fix — signals are now grouped by independent
data source. Previously, all signals were counted as independent votes, but
VWAP, EMA, and prior-close are all derived from the same price series and
are therefore highly correlated. Counting them separately inflated conviction.

Signal groups (one vote per group — max conviction from group):
    Group A (price momentum):  VWAP position, price vs prior close
    Group B (EMA structure):   EMA-20 vs EMA-50 crossover, price vs EMA-20
    Group C (oscillator):      RSI
    Group D (structure):       multi-timeframe alignment

Conviction formula:
    base = 0.40
    Per group in agreement: +0.12
    Cross-group consensus (all 4 agree): ×1.15 multiplier
    Capped at 0.90

Misaligned trade penalty: ×0.75 if proposed structure contradicts tape direction.
"""

from __future__ import annotations

from typing import Any

from oa2.debaters.base import DebaterBase, DebaterOpinion, Direction


_BULLISH_STRUCTURES = {
    "VERTICAL_CALL_SPREAD", "LONG_CALL", "DIAGONAL_SPREAD", "CALENDAR_CALL",
}
_BEARISH_STRUCTURES = {
    "VERTICAL_PUT_SPREAD", "LONG_PUT", "CALENDAR_PUT",
}


def _group_a_vote(price: float, vwap: float, prior_close: float, atr: float) -> int:
    """Group A: price momentum signals (VWAP + prior close).

    Returns: +1 (bullish), -1 (bearish), 0 (neutral/split).
    """
    bull, bear = 0, 0

    if vwap > 0:
        if price > vwap:
            bull += 1
        elif price < vwap:
            bear += 1

    if prior_close > 0 and atr > 0:
        change = (price - prior_close) / prior_close
        threshold = atr / prior_close / 2
        if change > threshold:
            bull += 1
        elif change < -threshold:
            bear += 1

    if bull > bear:
        return 1
    if bear > bull:
        return -1
    return 0


def _group_b_vote(price: float, ema_20: float, ema_50: float) -> int:
    """Group B: EMA structure (EMA crossover + price vs EMA-20).

    Returns: +1 (bullish), -1 (bearish), 0 (neutral/split).
    """
    bull, bear = 0, 0

    if ema_20 > 0 and ema_50 > 0:
        if ema_20 > ema_50:
            bull += 1
        elif ema_20 < ema_50:
            bear += 1

    if ema_20 > 0:
        if price > ema_20:
            bull += 1
        elif price < ema_20:
            bear += 1

    if bull > bear:
        return 1
    if bear > bull:
        return -1
    return 0


def _group_c_vote(rsi: float) -> int:
    """Group C: RSI oscillator.

    Returns: +1 (oversold = bullish signal), -1 (overbought = bearish), 0 (neutral).
    """
    if rsi < 30:
        return 1    # oversold → mean reversion bullish
    if rsi > 70:
        return -1   # overbought → mean reversion bearish
    return 0


def _group_d_vote(mtf_alignment: float) -> int:
    """Group D: multi-timeframe structure alignment.

    Returns: +1 (bullish alignment), -1 (bearish), 0 (neutral).
    """
    if mtf_alignment > 0.3:
        return 1
    if mtf_alignment < -0.3:
        return -1
    return 0


class DirectionalDebater(DebaterBase):
    """Argues from trend/momentum perspective.

    Signal groups (Phase D5 — one vote per independent data source):
        Group A: Price momentum  (VWAP position, price vs prior close)
        Group B: EMA structure   (EMA crossover, price vs EMA-20)
        Group C: RSI oscillator  (single independent signal)
        Group D: MTF structure   (multi-timeframe alignment)

    Conviction: 0.40 base + 0.12 per agreeing group, ×1.15 if all 4 agree.
    Misalignment penalty: ×0.75 if proposed trade opposes tape direction.
    """

    def __init__(self):
        super().__init__("directional")

    def debate(self, context: dict[str, Any]) -> DebaterOpinion:
        """Assess directional alignment between tape and proposed trade."""
        price = context.get("current_price", 0.0)
        vwap = context.get("vwap", price)
        ema_20 = context.get("ema_20", price)
        ema_50 = context.get("ema_50", price)
        rsi = context.get("rsi", 50.0)
        atr = context.get("atr", 0.0)
        prior_close = context.get("prior_close", price)

        setup = context.get("setup")
        mtf_alignment = 0.0
        if setup and isinstance(setup, dict):
            mtf_alignment = setup.get("multi_timeframe_alignment", 0.0)
        elif hasattr(setup, "multi_timeframe_alignment"):
            mtf_alignment = setup.multi_timeframe_alignment

        # --- Phase D5: grouped signal voting ---
        group_votes = {
            "A_momentum": _group_a_vote(price, vwap, prior_close, atr),
            "B_ema": _group_b_vote(price, ema_20, ema_50),
            "C_rsi": _group_c_vote(rsi),
            "D_mtf": _group_d_vote(mtf_alignment),
        }

        bull_groups = sum(1 for v in group_votes.values() if v == 1)
        bear_groups = sum(1 for v in group_votes.values() if v == -1)
        neutral_groups = sum(1 for v in group_votes.values() if v == 0)
        n_groups = len(group_votes)

        # Determine tape direction from group majority
        if bull_groups > bear_groups:
            tape_direction = Direction.BULLISH
            agreeing_groups = bull_groups
        elif bear_groups > bull_groups:
            tape_direction = Direction.BEARISH
            agreeing_groups = bear_groups
        else:
            tape_direction = Direction.NEUTRAL
            agreeing_groups = 0

        # Conviction: base + per-agreeing-group bonus
        tape_conviction = 0.40 + agreeing_groups * 0.12

        # Cross-group consensus multiplier: all 4 non-neutral groups agree
        active_groups = bull_groups + bear_groups
        if active_groups == n_groups and (bull_groups == n_groups or bear_groups == n_groups):
            tape_conviction *= 1.15   # unanimous across all independent sources

        tape_conviction = min(tape_conviction, 0.90)

        # --- Trade alignment check ---
        strategy = context.get("strategy")
        proposed_structure = None
        if strategy:
            if isinstance(strategy, dict):
                proposed_structure = strategy.get("selected_structure")
            else:
                proposed_structure = getattr(strategy, "selected_structure", None)

        trade_aligned = (
            (tape_direction == Direction.BULLISH and proposed_structure in _BULLISH_STRUCTURES) or
            (tape_direction == Direction.BEARISH and proposed_structure in _BEARISH_STRUCTURES) or
            (tape_direction == Direction.NEUTRAL and proposed_structure not in (_BULLISH_STRUCTURES | _BEARISH_STRUCTURES))
        )

        if trade_aligned:
            conviction = tape_conviction
            reasoning = (
                f"Tape is {tape_direction.value}: {bull_groups} bull / {bear_groups} bear / "
                f"{neutral_groups} neutral groups. Trade aligns with trend."
            )
        else:
            conviction = tape_conviction * 0.75
            reasoning = (
                f"Tape is {tape_direction.value} but proposed trade is misaligned. "
                f"Directional conflict reduces conviction."
            )

        signals = {
            "price": price,
            "vwap": vwap,
            "ema_20": ema_20,
            "ema_50": ema_50,
            "rsi": rsi,
            "group_votes": group_votes,
            "bull_groups": bull_groups,
            "bear_groups": bear_groups,
            "neutral_groups": neutral_groups,
            "tape_direction": tape_direction.value,
            "tape_conviction": tape_conviction,
            "trade_aligned": trade_aligned,
        }

        return DebaterOpinion(
            debater_name=self.name,
            direction=tape_direction,
            conviction=round(conviction, 3),
            reasoning=reasoning,
            signals_used=signals,
        )
