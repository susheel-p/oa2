"""Directional debater — tape/momentum perspective (quant-only, v2)."""

from __future__ import annotations

from typing import Any

from oa2.debaters.base import DebaterBase, DebaterOpinion, Direction


class DirectionalDebater(DebaterBase):
    """Argues from trend/momentum perspective.

    Signals:
      - Price vs VWAP (primary)
      - EMA 20 vs 50 (primary)
      - Multi-timeframe alignment (primary)
      - VWAP distance (secondary)
      - RSI extremes (secondary)
      - Momentum from prior close (secondary)

    Conviction: 0.40 + (signal_diff × 0.12), capped 0.90
    Penalty: 0.75× if proposed trade is directionally misaligned with tape.
    """

    def __init__(self):
        super().__init__("directional")

    def debate(self, context: dict[str, Any]) -> DebaterOpinion:
        """Assess directional alignment between tape and proposed trade."""
        ticker = context.get("ticker", "UNKNOWN")
        price = context.get("current_price", 0.0)
        vwap = context.get("vwap", price)
        ema_20 = context.get("ema_20", price)
        ema_50 = context.get("ema_50", price)
        rsi = context.get("rsi", 50.0)
        atr = context.get("atr", 0.0)
        prior_close = context.get("prior_close", price)

        # Setup/strategy are optional; work with what we have
        setup = context.get("setup")
        strategy = context.get("strategy")
        mtf_alignment = 0.0
        if setup and isinstance(setup, dict):
            mtf_alignment = setup.get("multi_timeframe_alignment", 0.0)
        elif hasattr(setup, "multi_timeframe_alignment"):
            mtf_alignment = setup.multi_timeframe_alignment

        # Count bullish vs bearish signals
        bullish_signals = sum([
            price > vwap,
            price > ema_20,
            ema_20 > ema_50,
            mtf_alignment > 0.3,
        ])
        bearish_signals = sum([
            price < vwap,
            price < ema_20,
            ema_20 < ema_50,
            mtf_alignment < -0.3,
        ])

        # Secondary signals
        if vwap > 0:
            vwap_distance_pct = abs(price - vwap) / vwap
            if price > vwap and vwap_distance_pct > 0.02:
                bullish_signals += 1
            elif price < vwap and vwap_distance_pct > 0.02:
                bearish_signals += 1

        if rsi < 30:
            bullish_signals += 1
        elif rsi > 70:
            bearish_signals += 1

        if prior_close > 0 and atr > 0:
            price_change = (price - prior_close) / prior_close
            atr_pct = atr / prior_close
            if price_change > atr_pct / 2:
                bullish_signals += 1
            elif price_change < -atr_pct / 2:
                bearish_signals += 1

        # Determine tape direction
        if bullish_signals > bearish_signals:
            tape_direction = Direction.BULLISH
            tape_conviction = 0.40 + (bullish_signals - bearish_signals) * 0.12
        elif bearish_signals > bullish_signals:
            tape_direction = Direction.BEARISH
            tape_conviction = 0.40 + (bearish_signals - bullish_signals) * 0.12
        else:
            tape_direction = Direction.NEUTRAL
            tape_conviction = 0.30

        tape_conviction = min(tape_conviction, 0.90)

        # Check if proposed trade aligns with tape
        proposed_structure = None
        if strategy:
            if isinstance(strategy, dict):
                proposed_structure = strategy.get("selected_structure")
            else:
                proposed_structure = getattr(strategy, "selected_structure", None)

        bullish_structures = {
            "VERTICAL_CALL_SPREAD", "LONG_CALL", "DIAGONAL_SPREAD", "CALENDAR_CALL",
        }
        bearish_structures = {
            "VERTICAL_PUT_SPREAD", "LONG_PUT", "CALENDAR_PUT",
        }

        trade_aligned = (
            (tape_direction == Direction.BULLISH and proposed_structure in bullish_structures) or
            (tape_direction == Direction.BEARISH and proposed_structure in bearish_structures) or
            (tape_direction == Direction.NEUTRAL and proposed_structure not in (bullish_structures | bearish_structures))
        )

        # Assess conviction
        if trade_aligned:
            conviction = tape_conviction
            reasoning = f"Tape is {tape_direction.value} ({bullish_signals} bullish vs {bearish_signals} bearish signals). Trade aligns with trend."
        else:
            conviction = tape_conviction * 0.75
            reasoning = f"Tape is {tape_direction.value} but proposed trade is misaligned. Directional conflict reduces conviction."

        signals = {
            "price": price,
            "vwap": vwap,
            "ema_20": ema_20,
            "ema_50": ema_50,
            "rsi": rsi,
            "bullish_signals": bullish_signals,
            "bearish_signals": bearish_signals,
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