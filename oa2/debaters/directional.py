"""Directional debater — tape/momentum perspective (quant-only, v2).

Phase D5 + P1.1: signals grouped by independent data source.

Signal groups (one vote per group — independent data sources):
    Group A (price momentum):     VWAP position, price vs prior close
    Group B (EMA structure):      EMA-20 vs EMA-50 crossover, price vs EMA-20
    Group C (oscillator):         RSI
    Group D (structure):          multi-timeframe alignment
    Group E (momentum confirm):   MACD histogram + volume surge + 20d breakout

Conviction formula:
    base = 0.40
    Per group in agreement: +0.10  (5 groups now, was 0.12 for 4)
    Cross-group consensus (all 5 agree): x1.15 multiplier
    Capped at 0.80

Misaligned trade penalty: x0.75 if proposed structure contradicts tape direction.
"""

from __future__ import annotations

from typing import Any

from oa2.debaters.base import DebaterBase, DebaterOpinion, Direction


def _ema_from_series(prices: list[float], span: int) -> float:
    """Compute EMA from a price series. Returns last value, or last price if too short."""
    if not prices:
        return 0.0
    if len(prices) < span:
        return prices[-1]
    alpha = 2.0 / (span + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = alpha * p + (1 - alpha) * ema
    return ema


def _macd_histogram(prices: list[float]) -> float:
    """MACD histogram = (EMA-12 - EMA-26) - signal-line (EMA-9 of MACD).

    Simplified: returns (EMA-12 - EMA-26) when only 20 bars available.
    Positive = bullish momentum, negative = bearish.
    """
    if len(prices) < 12:
        return 0.0
    ema_fast = _ema_from_series(prices, 12)
    ema_slow = _ema_from_series(prices, 26) if len(prices) >= 26 else _ema_from_series(prices, min(len(prices), 20))
    return ema_fast - ema_slow


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


def _group_c_vote(rsi: float, ema_20: float = 0.0, ema_50: float = 0.0) -> int:
    """Group C: RSI oscillator, gated by EMA trend (P0 bearish-asymmetry fix).

    Prior version returned bearish on RSI>70 unconditionally, which produced
    systematic false bearish signals in trending markets (uptrend with high
    RSI is continuation, not exhaustion). Now requires opposite-trend gate:
      - Oversold (RSI<30) bullish only when EMA20 >= EMA50 (uptrend dip)
      - Overbought (RSI>70) bearish only when EMA20 <= EMA50 (downtrend rip)

    Returns: +1 (bullish), -1 (bearish), 0 (neutral).
    """
    trend_up = ema_20 > 0 and ema_50 > 0 and ema_20 >= ema_50
    trend_down = ema_20 > 0 and ema_50 > 0 and ema_20 <= ema_50
    if rsi < 30 and trend_up:
        return 1    # oversold dip in uptrend -> buy the dip
    if rsi > 70 and trend_down:
        return -1   # overbought rip in downtrend -> fade the rally
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


def _group_e_vote(
    prices_20d: list[float],
    price: float,
    volume: float,
    avg_volume: float,
) -> tuple[int, dict[str, float]]:
    """Group E: momentum confirmation (MACD + volume surge + 20d breakout).

    Three independent confirmation signals that, together, indicate strong
    directional momentum vs. mean-reverting chop:
        - MACD histogram (trend momentum)
        - Volume surge (vol > 1.5x 20d avg)
        - 20-day breakout (price > prior 20d high, or < prior 20d low)

    Returns:
        (vote, signals) where vote is +1/-1/0 and signals is a diagnostics dict.
    """
    bull, bear = 0, 0
    signals = {"macd_hist": 0.0, "volume_ratio": 0.0, "breakout": 0}

    # 1) MACD histogram from prices_20d
    if prices_20d and len(prices_20d) >= 12:
        hist = _macd_histogram(prices_20d)
        signals["macd_hist"] = hist
        # Threshold: 0.1% of price to avoid noise
        threshold = price * 0.001
        if hist > threshold:
            bull += 1
        elif hist < -threshold:
            bear += 1

    # 2) Volume surge (only confirms when also trending in price)
    if avg_volume > 0 and volume > 0:
        vol_ratio = volume / avg_volume
        signals["volume_ratio"] = vol_ratio
        if vol_ratio > 1.5 and prices_20d and len(prices_20d) >= 2:
            # Volume surge confirms direction of recent price move
            recent_change = prices_20d[-1] - prices_20d[-2]
            if recent_change > 0:
                bull += 1
            elif recent_change < 0:
                bear += 1

    # 3) 20-day breakout
    if prices_20d and len(prices_20d) >= 5:
        prior = prices_20d[:-1] if len(prices_20d) > 1 else prices_20d
        if prior:
            prior_high = max(prior)
            prior_low = min(prior)
            if price > prior_high:
                bull += 1
                signals["breakout"] = 1
            elif price < prior_low:
                bear += 1
                signals["breakout"] = -1

    if bull > bear:
        return 1, signals
    if bear > bull:
        return -1, signals
    return 0, signals


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
        volume = context.get("volume", 0.0)
        avg_volume = context.get("avg_volume", 0.0)
        prices_20d = context.get("prices_20d", []) or []

        setup = context.get("setup")
        mtf_alignment = 0.0
        if setup and isinstance(setup, dict):
            mtf_alignment = setup.get("multi_timeframe_alignment", 0.0)
        elif hasattr(setup, "multi_timeframe_alignment"):
            mtf_alignment = setup.multi_timeframe_alignment

        # --- Phase D5 + P1.1: grouped signal voting (5 groups) ---
        e_vote, e_signals = _group_e_vote(prices_20d, price, volume, avg_volume)
        group_votes = {
            "A_momentum": _group_a_vote(price, vwap, prior_close, atr),
            "B_ema": _group_b_vote(price, ema_20, ema_50),
            "C_rsi": _group_c_vote(rsi, ema_20, ema_50),
            "D_mtf": _group_d_vote(mtf_alignment),
            "E_momentum_confirm": e_vote,
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

        # P1.1: 5 groups now; per-group bonus reduced from 0.12 -> 0.10 to keep cap honest
        tape_conviction = 0.40 + agreeing_groups * 0.10

        # Cross-group consensus multiplier: all 5 non-neutral groups agree
        active_groups = bull_groups + bear_groups
        if active_groups == n_groups and (bull_groups == n_groups or bear_groups == n_groups):
            tape_conviction *= 1.15   # unanimous across all independent sources

        # P1.1: cap reduced from 0.90 -> 0.80 (more honest about signal quality)
        tape_conviction = min(tape_conviction, 0.80)

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
            "group_e_signals": e_signals,
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
