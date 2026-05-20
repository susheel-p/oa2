"""Tests for oa2.learning.outcomes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingbot.learning.outcomes import (
    TradeOutcome,
    resolve_outcomes_from_log,
    simulate_spread_pnl,
    _next_trading_day,
)


class TestSimulateSpreadPnl:
    def test_bullish_win_capped_at_max_profit(self):
        # Big rally -> capped at max_profit
        pnl, pct = simulate_spread_pnl(
            direction="BULLISH",
            long_strike=100.0, short_strike=105.0,
            max_profit=300.0, max_loss=200.0,
            entry_price=100.0, resolution_price=150.0,
        )
        assert pnl == 300.0
        assert pct == 1.5  # 300 / 200

    def test_bullish_loss_capped_at_max_loss(self):
        pnl, pct = simulate_spread_pnl(
            direction="BULLISH",
            long_strike=100.0, short_strike=105.0,
            max_profit=300.0, max_loss=200.0,
            entry_price=100.0, resolution_price=50.0,
        )
        assert pnl == -200.0
        assert pct == -1.0

    def test_bearish_win_on_drop(self):
        pnl, pct = simulate_spread_pnl(
            direction="BEARISH",
            long_strike=100.0, short_strike=95.0,
            max_profit=300.0, max_loss=200.0,
            entry_price=100.0, resolution_price=98.0,
            spread_delta=0.30,
        )
        # delta 0.30 * -2 move * -1 direction = +0.60 per share = +$60
        assert pnl == 60.0
        assert pct == 0.30

    def test_bullish_small_gain(self):
        pnl, pct = simulate_spread_pnl(
            direction="BULLISH",
            long_strike=100.0, short_strike=105.0,
            max_profit=300.0, max_loss=200.0,
            entry_price=100.0, resolution_price=101.0,
        )
        # 0.30 * 1.0 = 0.30/share = $30/contract
        assert pnl == 30.0
        assert pct == 0.15


class TestNextTradingDay:
    def test_friday_skips_weekend(self):
        # 2026-05-15 is Friday -> next trading day = Monday 2026-05-18
        assert _next_trading_day("2026-05-15") == "2026-05-18"

    def test_monday(self):
        # 2026-05-18 Mon -> Tue 2026-05-19
        assert _next_trading_day("2026-05-18") == "2026-05-19"


class TestResolveOutcomesFromLog:
    @pytest.fixture
    def synthetic_log(self, tmp_path):
        """Create a fake decision log with one approved + one rejected trade."""
        log = tmp_path / "paper_trade_2026-05-18.jsonl"
        approved = {
            "ticker": "SPY",
            "ts": "2026-05-18T09:35:00-0400",
            "status": "sized_approved",
            "structure_pick": {
                "structure": "vertical_call_spread",
                "long_strike": 739.0,
                "short_strike": 746.0,
                "max_profit": 398.0,
                "max_loss": 302.0,
                "odds": 1.318,
            },
            "sizing": {
                "approved": True,
                "contracts": 7,
                "max_dollars_at_risk": 2114.0,
                "max_profit_dollars": 2786.0,
                "kelly": {"kelly_f": 0.047},
            },
            "consensus": {
                "direction": "BULLISH",
                "p_bull": 0.55,
                "weights": {"directional": 0.55, "sentiment": 0.10},
            },
            "regime": {"vol_state": "normal", "trend_state": "neutral"},
        }
        rejected = {
            "ticker": "SLV",
            "ts": "2026-05-18T09:35:00-0400",
            "status": "sized_rejected",
        }
        with open(log, "w") as f:
            f.write(json.dumps(approved) + "\n")
            f.write(json.dumps(rejected) + "\n")
        return log

    def test_resolves_only_approved(self, synthetic_log):
        # Stub price fetcher: entry $740, resolution $743 (small rally)
        def fake_fetcher(ticker, date):
            return {"2026-05-18": 740.0, "2026-05-19": 743.0}.get(date)

        outcomes = resolve_outcomes_from_log(synthetic_log, price_fetcher=fake_fetcher)
        assert len(outcomes) == 1
        o = outcomes[0]
        assert o.ticker == "SPY"
        assert o.direction == "BULLISH"
        assert o.direction_hit is True  # price moved up
        assert o.entry_price == 740.0
        assert o.resolution_price == 743.0

    def test_outcome_pnl_consistent(self, synthetic_log):
        def fake_fetcher(ticker, date):
            return {"2026-05-18": 740.0, "2026-05-19": 743.0}.get(date)

        outcomes = resolve_outcomes_from_log(synthetic_log, price_fetcher=fake_fetcher)
        o = outcomes[0]
        # 0.30 * 3.0 move * 100 = $90/contract; *7 contracts = $630
        assert o.pnl_proxy_dollars == 90.0
        assert o.total_pnl_dollars == 630.0
        assert 0.29 < o.pnl_pct_of_max_loss < 0.31

    def test_handles_missing_prices(self, synthetic_log):
        def empty_fetcher(ticker, date):
            return None

        outcomes = resolve_outcomes_from_log(synthetic_log, price_fetcher=empty_fetcher)
        assert outcomes == []

    def test_skips_neutral_direction(self, tmp_path):
        log = tmp_path / "p.jsonl"
        record = {
            "ticker": "SPY",
            "ts": "2026-05-18T09:35:00-0400",
            "status": "sized_approved",
            "structure_pick": {"long_strike": 100.0, "short_strike": 105.0,
                              "max_profit": 300.0, "max_loss": 200.0, "structure": "x"},
            "sizing": {"contracts": 1, "max_dollars_at_risk": 200.0, "max_profit_dollars": 300.0,
                       "kelly": {"kelly_f": 0.05}},
            "consensus": {"direction": "NEUTRAL", "p_bull": 0.5, "weights": {}},
            "regime": {"vol_state": "normal", "trend_state": "neutral"},
        }
        with open(log, "w") as f:
            f.write(json.dumps(record) + "\n")
        outcomes = resolve_outcomes_from_log(log, price_fetcher=lambda t, d: 100.0)
        assert outcomes == []
