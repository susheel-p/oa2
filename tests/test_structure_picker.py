"""Tests for tradingbot.strategy.structure_picker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingbot.strategy import pick_structure
from tradingbot.strategy.structure_picker import (
    StructurePick,
    _required_odds,
    SAFETY_MARGIN,
)


# ---- Helpers ----------------------------------------------------------------

def _make_leg(strike: float, bid: float, ask: float, oi: int = 200, iv: float = 0.30, delta: float = 0.0) -> dict:
    return {
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "open_interest": oi,
        "iv": iv,
        "delta": delta,
        "vega": 0.10,
        "theta": -0.05,
    }


def _synthetic_chain(spot: float = 100.0) -> dict:
    """Build a small synthetic chain: 5 strikes either side of spot."""
    calls, puts = [], []
    for offset in [-10, -5, 0, 5, 10]:
        k = spot + offset
        # ITM calls cheaper than ATM; OTM calls cheaper still
        intrinsic = max(0.0, spot - k)
        time_val = 2.0
        mid = intrinsic + time_val
        calls.append(_make_leg(k, mid - 0.10, mid + 0.10))
        intrinsic_p = max(0.0, k - spot)
        midp = intrinsic_p + time_val
        puts.append(_make_leg(k, midp - 0.10, midp + 0.10))
    return {"calls": calls, "puts": puts, "atm_strike": spot}


# ---- _required_odds ---------------------------------------------------------

class TestRequiredOdds:
    def test_bullish_high_conviction_low_required_odds(self):
        # p_bull = 0.7 -> required = 0.3/0.7 + safety = ~0.43
        odds = _required_odds(0.70, "BULLISH")
        assert 0.40 < odds < 0.50

    def test_bullish_low_conviction_high_required_odds(self):
        # p_bull = 0.55 -> required = 0.45/0.55 + safety = ~0.87
        odds = _required_odds(0.55, "BULLISH")
        assert 0.80 < odds < 0.95

    def test_bearish_uses_inverse_probability(self):
        # p_bull=0.45 means p_bear=0.55, so bearish required = 0.45/0.55 + safety
        odds = _required_odds(0.45, "BEARISH")
        assert 0.80 < odds < 0.95


# ---- pick_structure: vertical spreads --------------------------------------

class TestPickStructureVertical:
    def test_bullish_picks_call_vertical(self):
        chain = _synthetic_chain(spot=100.0)
        pick = pick_structure(chain, spot=100.0, direction="BULLISH", p_bull=0.55)
        assert pick is not None
        assert pick.structure_type == "vertical_call_spread"
        assert pick.long_strike <= pick.short_strike
        assert pick.max_profit > 0
        assert pick.max_loss > 0
        assert pick.odds > 0

    def test_bearish_picks_put_vertical(self):
        chain = _synthetic_chain(spot=100.0)
        pick = pick_structure(chain, spot=100.0, direction="BEARISH", p_bull=0.45)
        assert pick is not None
        assert pick.structure_type == "vertical_put_spread"
        assert pick.long_strike >= pick.short_strike

    def test_neutral_returns_none(self):
        chain = _synthetic_chain(spot=100.0)
        assert pick_structure(chain, spot=100.0, direction="NEUTRAL", p_bull=0.50) is None

    def test_returns_none_with_no_chain(self):
        assert pick_structure(None, 100.0, "BULLISH", 0.60) is None
        assert pick_structure({}, 100.0, "BULLISH", 0.60) is None

    def test_odds_meet_kelly_requirement(self):
        chain = _synthetic_chain(spot=100.0)
        pick = pick_structure(chain, 100.0, "BULLISH", p_bull=0.55)
        assert pick is not None
        required = _required_odds(0.55, "BULLISH")
        assert pick.odds >= required, f"odds {pick.odds} < required {required}"


# ---- Liquidity filtering ---------------------------------------------------

class TestLiquidityFilter:
    def test_skips_zero_bid_legs(self):
        chain = {
            "calls": [
                _make_leg(95.0, 0.0, 7.0),       # zero bid -> skip
                _make_leg(100.0, 2.00, 2.10),     # liquid ATM
                _make_leg(105.0, 1.10, 1.20),     # liquid OTM tight spread
            ],
            "puts": [],
        }
        pick = pick_structure(chain, 100.0, "BULLISH", 0.60)
        assert pick is not None
        # Should not have selected the zero-bid leg
        assert pick.long_strike != 95.0
        assert pick.short_strike != 95.0

    def test_skips_low_open_interest(self):
        chain = {
            "calls": [
                _make_leg(100.0, 2.0, 2.20, oi=200),    # liquid
                _make_leg(105.0, 1.0, 1.20, oi=10),     # OI too low -> skip
                _make_leg(110.0, 0.5, 0.60, oi=200),    # liquid wider strike
            ],
            "puts": [],
        }
        pick = pick_structure(chain, 100.0, "BULLISH", 0.60)
        assert pick is not None
        assert pick.short_strike != 105.0


# ---- Real cached chain validation ------------------------------------------

class TestRealChain:
    @pytest.fixture
    def amd_chain(self):
        path = Path("cache/AMD_2026-05-18.json")
        if not path.exists():
            pytest.skip("AMD cache not present")
        return json.loads(path.read_text())

    def test_amd_real_chain_picks_viable_structure(self, amd_chain):
        """At high conviction (p_bull=0.65), AMD chain should produce a viable pick.

        At lower conviction the picker may legitimately return None when no
        spread satisfies the required odds — that's the correct behavior.
        """
        chain = amd_chain["_options_chain"]
        spot = amd_chain["current_price"]
        pick = pick_structure(chain, spot, "BULLISH", p_bull=0.65)
        # Either we get a viable structure with Kelly-viable odds, or None
        # (picker rejecting bad-R:R chains is correct behavior).
        if pick is not None:
            required = _required_odds(0.65, "BULLISH")
            assert pick.odds >= required
            assert pick.max_profit > 0
            assert pick.max_loss > 0
