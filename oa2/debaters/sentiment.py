"""Sentiment debater — options market structure perspective (P2.1 rewrite).

Phase P2.1: Replaced weak news/reddit sources with options market signals
that are harder to game:

Primary signal: IV put-call skew (from vol_regime.put_call_skew)
    - put_skew > +0.05 (5%+): institutions hedging downside -> BEARISH conviction 0.55
    - put_skew < -0.05: call buying spike -> BULLISH conviction 0.45 (weaker, can be FOMO)

Secondary signal: Earnings calendar (if days_to_earnings <= 5)
    - Within 1 day: NEUTRAL @ 0.20 (pre-earnings vol crush risk)
    - Within 5 days: NEUTRAL @ 0.25 (awaiting catalyst)

Tertiary signal: Call/put ratio extremes (tiebreaker only, capped at 0.30)
    - > 1.8: BULLISH @ 0.30 (retail call buying, weak)
    - < 0.6: BEARISH @ 0.30 (put buying, weak)

Fallback: legacy sentiment_snapshot (composite_score) when no vol_regime data.

Conviction range: [0.20, 0.55] — honest reflection of signal quality.
"""

from __future__ import annotations

from typing import Any

from oa2.debaters.base import DebaterBase, DebaterOpinion, Direction


_IV_SKEW_BEARISH_THRESHOLD = 0.05   # put-IV > call-IV by 5%+
_IV_SKEW_BULLISH_THRESHOLD = -0.05  # call-IV > put-IV by 5%+
_CP_RATIO_BULLISH = 1.8
_CP_RATIO_BEARISH = 0.6


def _extract_iv_skew(vol_regime: Any) -> float | None:
    """Extract put_call_skew from vol_regime (dict or pydantic model). None if absent."""
    if vol_regime is None:
        return None
    if isinstance(vol_regime, dict):
        return vol_regime.get("put_call_skew")
    return getattr(vol_regime, "put_call_skew", None)


def _extract_days_to_earnings(context: dict[str, Any]) -> int:
    """Pull days_to_earnings from earnings_snapshot or context. Defaults to 30 (clear)."""
    earnings = context.get("earnings_snapshot") or {}
    if isinstance(earnings, dict):
        d = earnings.get("days_to_earnings")
    else:
        d = getattr(earnings, "days_to_earnings", None)
    if d is None:
        d = context.get("days_to_earnings", 30)
    try:
        return int(d)
    except (TypeError, ValueError):
        return 30


def _extract_call_put_ratio(context: dict[str, Any]) -> float | None:
    """Pull call/put ratio from flow_snapshot or sentiment_snapshot."""
    flow = context.get("flow_snapshot") or {}
    if isinstance(flow, dict):
        cpr = flow.get("call_put_ratio")
    else:
        cpr = getattr(flow, "call_put_ratio", None)
    if cpr is None:
        sent = context.get("sentiment_snapshot") or {}
        if isinstance(sent, dict):
            cpr = sent.get("call_put_ratio")
        else:
            cpr = getattr(sent, "call_put_ratio", None)
    return cpr


class SentimentDebater(DebaterBase):
    """Argues based on options market structure (IV skew + earnings + call/put ratio).

    Signal priority (strongest first):
        1. IV put-call skew (institutional positioning, hardest to game)
        2. Earnings calendar (binary risk-off filter)
        3. Call/put ratio extremes (tiebreaker, weak signal)

    Honest conviction range: [0.20, 0.55].
    """

    def __init__(self):
        super().__init__("sentiment")

    def debate(self, context: dict[str, Any]) -> DebaterOpinion:
        ticker = context.get("ticker", "UNKNOWN")

        # ---- Filter 1: Earnings calendar ----
        days_to_earnings = _extract_days_to_earnings(context)
        if days_to_earnings <= 1:
            return DebaterOpinion(
                debater_name=self.name,
                direction=Direction.NEUTRAL,
                conviction=0.20,
                reasoning=f"Earnings in {days_to_earnings}d -- avoid pre-announcement vol crush.",
                signals_used={"days_to_earnings": days_to_earnings, "signal": "earnings_blackout"},
            )
        if days_to_earnings <= 5:
            return DebaterOpinion(
                debater_name=self.name,
                direction=Direction.NEUTRAL,
                conviction=0.25,
                reasoning=f"Earnings in {days_to_earnings}d -- awaiting catalyst clarity.",
                signals_used={"days_to_earnings": days_to_earnings, "signal": "earnings_window"},
            )

        # ---- Primary signal: IV put-call skew ----
        vol_regime = context.get("vol_regime")
        iv_skew = _extract_iv_skew(vol_regime)

        if iv_skew is not None:
            if iv_skew >= _IV_SKEW_BEARISH_THRESHOLD:
                return DebaterOpinion(
                    debater_name=self.name,
                    direction=Direction.BEARISH,
                    conviction=0.55,
                    reasoning=f"IV put-skew {iv_skew:+.3f} -- institutional downside hedging.",
                    signals_used={
                        "put_call_skew": iv_skew,
                        "signal": "iv_put_skew_elevated",
                        "days_to_earnings": days_to_earnings,
                    },
                )
            if iv_skew <= _IV_SKEW_BULLISH_THRESHOLD:
                return DebaterOpinion(
                    debater_name=self.name,
                    direction=Direction.BULLISH,
                    conviction=0.45,
                    reasoning=f"IV call-skew {iv_skew:+.3f} -- call demand spike.",
                    signals_used={
                        "put_call_skew": iv_skew,
                        "signal": "iv_call_skew_elevated",
                        "days_to_earnings": days_to_earnings,
                    },
                )

        # ---- Tertiary: call/put ratio extremes (tiebreaker only) ----
        cpr = _extract_call_put_ratio(context)
        if cpr is not None:
            if cpr > _CP_RATIO_BULLISH:
                return DebaterOpinion(
                    debater_name=self.name,
                    direction=Direction.BULLISH,
                    conviction=0.30,
                    reasoning=f"Call/put ratio {cpr:.2f} -- weak retail bullish signal.",
                    signals_used={"call_put_ratio": cpr, "signal": "cpr_bullish", "iv_skew": iv_skew},
                )
            if cpr < _CP_RATIO_BEARISH:
                return DebaterOpinion(
                    debater_name=self.name,
                    direction=Direction.BEARISH,
                    conviction=0.30,
                    reasoning=f"Call/put ratio {cpr:.2f} -- weak put-buying signal.",
                    signals_used={"call_put_ratio": cpr, "signal": "cpr_bearish", "iv_skew": iv_skew},
                )

        # ---- Fallback: legacy sentiment snapshot ----
        sentiment_data = context.get("sentiment_snapshot")
        if sentiment_data:
            if isinstance(sentiment_data, dict):
                composite = sentiment_data.get("composite_score", 0.0)
            else:
                composite = getattr(sentiment_data, "composite_score", 0.0)
            if composite > 0.30:
                return DebaterOpinion(
                    debater_name=self.name,
                    direction=Direction.BULLISH,
                    conviction=0.30,
                    reasoning=f"Fallback sentiment composite {composite:+.2f}.",
                    signals_used={"composite_score": composite, "signal": "legacy_sentiment"},
                )
            if composite < -0.30:
                return DebaterOpinion(
                    debater_name=self.name,
                    direction=Direction.BEARISH,
                    conviction=0.30,
                    reasoning=f"Fallback sentiment composite {composite:+.2f}.",
                    signals_used={"composite_score": composite, "signal": "legacy_sentiment"},
                )

        # ---- No actionable signal ----
        return DebaterOpinion(
            debater_name=self.name,
            direction=Direction.NEUTRAL,
            conviction=0.25,
            reasoning="No actionable options market signal (skew flat, CPR neutral).",
            signals_used={
                "iv_skew": iv_skew,
                "call_put_ratio": cpr if 'cpr' in locals() else None,
                "days_to_earnings": days_to_earnings,
            },
        )
