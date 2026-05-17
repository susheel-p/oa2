"""Options flow debater — institutional/smart-money perspective (quant-only, v2)."""

from __future__ import annotations

from typing import Any

from oa2.debaters.base import DebaterBase, DebaterOpinion, Direction


def _interpret_put_call_ratio(pcr: float) -> tuple[str, Direction]:
    """Interpret PCR into direction + confidence."""
    if pcr > 1.5:
        return f"PCR {pcr:.2f} — heavy put buying", Direction.BEARISH
    elif pcr > 1.1:
        return f"PCR {pcr:.2f} — elevated put buying", Direction.BEARISH
    elif pcr < 0.6:
        return f"PCR {pcr:.2f} — heavy call buying", Direction.BULLISH
    elif pcr < 0.85:
        return f"PCR {pcr:.2f} — call-heavy flow", Direction.BULLISH
    else:
        return f"PCR {pcr:.2f} — balanced flow", Direction.NEUTRAL


class FlowDebater(DebaterBase):
    """Argues from institutional / smart-money options flow.

    Signals:
      - Put/call ratio (PCR)
      - Call vs put sweeps (aggressive institutional buys)
      - OI changes (positioning trends)
      - Unusual volume

    Conviction: linear model on bull_ratio, capped [0.10, 0.90].
    """

    def __init__(self):
        super().__init__("flow")

    def debate(self, context: dict[str, Any]) -> DebaterOpinion:
        """Assess institutional flow alignment with proposed trade."""
        ticker = context.get("ticker", "UNKNOWN")

        # Extract flow data (from context or derive from chain)
        flow_data = context.get("flow_data") or {}
        chain = context.get("chain_snapshot")

        # If no explicit flow_data, try to derive from chain Greeks
        if not flow_data and chain:
            flow_data = self._flow_from_chain(chain)

        # Parse flow signals
        bull_score = 0
        bear_score = 0
        signals = []

        # PCR interpretation
        pcr = flow_data.get("put_call_ratio")
        if pcr is not None:
            label, d = _interpret_put_call_ratio(pcr)
            signals.append(label)
            if d == Direction.BULLISH:
                bull_score += 2
            elif d == Direction.BEARISH:
                bear_score += 2

        # Sweeps
        call_sweeps = flow_data.get("call_sweep_count", 0)
        put_sweeps = flow_data.get("put_sweep_count", 0)
        if call_sweeps > 0 or put_sweeps > 0:
            signals.append(f"Sweeps: {call_sweeps} call / {put_sweeps} put")
            bull_score += call_sweeps
            bear_score += put_sweeps

        # Dark pool
        if flow_data.get("dark_pool_bullish"):
            signals.append("Dark pool bullish")
            bull_score += 2
        if flow_data.get("dark_pool_bearish"):
            signals.append("Dark pool bearish")
            bear_score += 2

        # OI changes
        call_oi_chg = flow_data.get("large_call_oi_change", 0.0)
        put_oi_chg = flow_data.get("large_put_oi_change", 0.0)
        if abs(call_oi_chg) > 0.15:
            direction = "surging" if call_oi_chg > 0 else "collapsing"
            signals.append(f"Call OI {direction} {call_oi_chg*100:+.0f}%")
            bull_score += 1 if call_oi_chg > 0 else -1
        if abs(put_oi_chg) > 0.15:
            direction = "surging" if put_oi_chg > 0 else "collapsing"
            signals.append(f"Put OI {direction} {put_oi_chg*100:+.0f}%")
            bear_score += 1 if put_oi_chg > 0 else -1

        # Unusual volume
        if flow_data.get("unusual_call_vol"):
            signals.append("Unusual call volume")
            bull_score += 1
        if flow_data.get("unusual_put_vol"):
            signals.append("Unusual put volume")
            bear_score += 1

        # Derive direction + conviction
        total = bull_score + bear_score
        if total == 0:
            flow_direction = Direction.NEUTRAL
            flow_conviction = 0.20
        else:
            bull_ratio = bull_score / max(total, 1)
            if bull_ratio > 0.6:
                flow_direction = Direction.BULLISH
                flow_conviction = min(0.50 + (bull_ratio - 0.5) * 0.80, 0.90)
            elif bull_ratio < 0.4:
                flow_direction = Direction.BEARISH
                flow_conviction = max(0.50 - (0.5 - bull_ratio) * 0.80, 0.10)
            else:
                flow_direction = Direction.NEUTRAL
                flow_conviction = 0.35

        # Check if proposed trade aligns with flow
        strategy = context.get("strategy")
        proposed_structure = None
        if strategy:
            if isinstance(strategy, dict):
                proposed_structure = strategy.get("selected_structure")
            else:
                proposed_structure = getattr(strategy, "selected_structure", None)

        bullish_structures = {
            "LONG_CALL", "VERTICAL_CALL_SPREAD", "DIAGONAL_SPREAD", "CALENDAR_CALL",
        }
        bearish_structures = {
            "LONG_PUT", "VERTICAL_PUT_SPREAD", "CALENDAR_PUT",
        }
        neutral_structures = {
            "IRON_CONDOR", "SHORT_PREMIUM_FADE", "LONG_STRADDLE", "LONG_STRANGLE", "LONG_GAMMA_SCALP",
        }

        trade_is_bullish = proposed_structure in bullish_structures
        trade_is_bearish = proposed_structure in bearish_structures
        trade_is_neutral = proposed_structure in neutral_structures

        flow_aligned = (
            (flow_direction == Direction.BULLISH and trade_is_bullish) or
            (flow_direction == Direction.BEARISH and trade_is_bearish) or
            (flow_direction == Direction.NEUTRAL and trade_is_neutral)
        )

        # Assess conviction
        if flow_direction == Direction.NEUTRAL or not signals:
            conviction = flow_conviction
            reasoning = "Flow is neutral or no strong signal detected."
        elif flow_aligned:
            conviction = flow_conviction
            reasoning = f"Institutional flow ({flow_direction.value}) aligns with proposed {proposed_structure or 'trade'}."
        else:
            conviction = flow_conviction * 0.85
            reasoning = f"Flow is {flow_direction.value} but trade is misaligned. Contradiction reduces conviction."

        signals_dict = {
            "pcr": flow_data.get("put_call_ratio"),
            "call_sweeps": call_sweeps,
            "put_sweeps": put_sweeps,
            "call_oi_change": call_oi_chg,
            "put_oi_change": put_oi_chg,
            "dark_pool_bullish": flow_data.get("dark_pool_bullish", False),
            "dark_pool_bearish": flow_data.get("dark_pool_bearish", False),
            "unusual_call_vol": flow_data.get("unusual_call_vol", False),
            "unusual_put_vol": flow_data.get("unusual_put_vol", False),
            "bull_score": bull_score,
            "bear_score": bear_score,
            "flow_direction": flow_direction.value,
            "flow_signals": signals,
        }

        return DebaterOpinion(
            debater_name=self.name,
            direction=flow_direction,
            conviction=round(conviction, 3),
            reasoning=reasoning,
            signals_used=signals_dict,
        )

    @staticmethod
    def _flow_from_chain(chain: dict | Any) -> dict:
        """Derive minimal flow proxy from chain Greeks when no dedicated feed."""
        flow = {}
        if not chain:
            return flow

        vega = 0.0
        delta = 0.5
        if isinstance(chain, dict):
            vega = chain.get("vega", 0.0)
            delta = chain.get("delta", 0.5)
        else:
            vega = getattr(chain, "vega", 0.0)
            delta = getattr(chain, "delta", 0.5)

        if vega > 0.05:
            flow["unusual_call_vol"] = True
        elif vega < -0.05:
            flow["unusual_put_vol"] = True

        if delta > 0.55:
            flow["put_call_ratio"] = 0.7
        elif delta < 0.45:
            flow["put_call_ratio"] = 1.3

        return flow