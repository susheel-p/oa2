"""Clock injection + Greeks re-mark from live chain."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from tradingbot.core.clock import ManualClock, SystemClock
from tradingbot.execution.exit import ExitEngine, ExitReason, ExitUrgency
from tradingbot.execution.monitor import Leg, OpenPosition, PositionMonitor
from tradingbot.sizing.limits import GreeksBook


ET = ZoneInfo("America/New_York")


def _pos(trade_id: str = "t1", **overrides) -> OpenPosition:
    base = dict(
        trade_id=trade_id,
        ticker="SPY",
        underlying="SPY",
        structure="VERTICAL_CALL_SPREAD",
        direction="BULLISH",
        entry_price=450.0,
        entry_premium=1.0,
        entry_time=0.0,
        entry_regime=3,
        entry_dte=30,
        contracts=2,
        max_profit_per_contract=100.0,
        max_loss_per_contract=200.0,
        delta=0.0,
        vega=0.0,
        theta=0.0,
        current_dte=30,
    )
    base.update(overrides)
    return OpenPosition(**base)


# =============================================================================
# Clock
# =============================================================================

def test_system_clock_returns_aware_et_datetime():
    now = SystemClock().now_et()
    assert now.tzinfo is not None


def test_manual_clock_set_and_advance():
    start = dt.datetime(2025, 6, 1, 9, 30, tzinfo=ET)
    c = ManualClock(start)
    assert c.now_et() == start
    c.advance(3600)
    assert c.now_et() == start + dt.timedelta(hours=1)
    c.set(dt.datetime(2025, 6, 2, 9, 30, tzinfo=ET))
    assert c.now_et().day == 2


def test_manual_clock_rejects_naive_datetime():
    with pytest.raises(ValueError):
        ManualClock(dt.datetime(2025, 6, 1, 9, 30))


# =============================================================================
# Hard EOD uses injected clock (not OS wall-clock)
# =============================================================================

def test_hard_eod_fires_at_replay_time_not_wall_clock():
    # Replay clock parked at 3:56 ET — intraday position must force-close
    # regardless of what time it is on the host running the test.
    clock = ManualClock(dt.datetime(2025, 6, 3, 15, 56, tzinfo=ET))
    engine = ExitEngine(clock=clock)
    pos = _pos(structure="LONG_GAMMA_SCALP", entry_dte=0, current_dte=0)
    decision = engine.evaluate(pos)
    assert decision.should_exit
    assert decision.reason == ExitReason.HARD_EOD_CUTOFF
    assert decision.urgency == ExitUrgency.IMMEDIATE


def test_hard_eod_does_not_fire_before_cutoff_under_replay():
    clock = ManualClock(dt.datetime(2025, 6, 3, 12, 0, tzinfo=ET))
    engine = ExitEngine(clock=clock)
    pos = _pos(structure="LONG_GAMMA_SCALP", entry_dte=0, current_dte=0)
    decision = engine.evaluate(pos)
    assert not decision.should_exit


def test_time_stop_uses_replay_clock_for_age():
    # Entry 10 days ago of replay time; threshold 7.
    now = dt.datetime(2025, 6, 11, 10, 0, tzinfo=ET)
    entry = now - dt.timedelta(days=10)
    clock = ManualClock(now)
    engine = ExitEngine(time_stop_days=7, clock=clock)
    pos = _pos(entry_time=entry.timestamp(), current_dte=15)
    decision = engine.evaluate(pos)
    assert decision.needs_review
    assert decision.reason == ExitReason.TIME_STOP


# =============================================================================
# Greeks re-mark from chain
# =============================================================================

def test_remark_greeks_recomputes_position_and_updates_dte():
    today = dt.datetime(2025, 6, 1, 10, 0, tzinfo=ET)
    clock = ManualClock(today)
    monitor = PositionMonitor(clock=clock)

    expiry = dt.date(2025, 6, 21)  # 20 days out
    # Bull call spread: long 450C / short 460C, 2 structures
    pos = _pos(
        trade_id="bcs1",
        contracts=2,
        legs=[
            Leg("SPY", expiry, 450.0, "C", side=+1, contracts=1),
            Leg("SPY", expiry, 460.0, "C", side=-1, contracts=1),
        ],
    )
    monitor.add(pos)

    quotes = {
        (450.0, "C"): {"delta": 0.55, "vega": 0.20, "theta": -0.05},
        (460.0, "C"): {"delta": 0.35, "vega": 0.18, "theta": -0.04},
    }

    def chain(_under, _exp, strike, right):
        return quotes[(strike, right)]

    skipped = monitor.remark_greeks(chain)

    assert skipped == []
    # delta = (0.55 - 0.35) * 1 * 2 contracts = 0.40
    assert pos.delta == pytest.approx(0.40)
    assert pos.vega == pytest.approx((0.20 - 0.18) * 2)
    assert pos.theta == pytest.approx((-0.05 - (-0.04)) * 2)
    assert pos.current_dte == 20
    assert pos.last_checked == clock.now()


def test_remark_skips_position_when_chain_missing_leg():
    today = dt.datetime(2025, 6, 1, tzinfo=ET)
    monitor = PositionMonitor(clock=ManualClock(today))
    expiry = dt.date(2025, 6, 21)
    pos = _pos(
        trade_id="bcs1",
        legs=[
            Leg("SPY", expiry, 450.0, "C", side=+1),
            Leg("SPY", expiry, 460.0, "C", side=-1),
        ],
        delta=99.0,  # stale snapshot we want to detect was NOT overwritten
    )
    monitor.add(pos)

    def chain(_u, _e, strike, _r):
        return {"delta": 0.5, "vega": 0.1, "theta": 0.0} if strike == 450.0 else None

    skipped = monitor.remark_greeks(chain)
    assert skipped == ["bcs1"]
    assert pos.delta == 99.0   # untouched on partial-chain failure


def test_remark_skips_legacy_positions_without_legs():
    monitor = PositionMonitor(clock=ManualClock(dt.datetime(2025, 6, 1, tzinfo=ET)))
    monitor.add(_pos("legacy"))
    skipped = monitor.remark_greeks(lambda *a, **k: None)
    assert skipped == ["legacy"]


# =============================================================================
# GreeksBook.rebuild_from picks up the remarked values
# =============================================================================

def test_book_rebuild_from_picks_up_remarked_greeks():
    today = dt.datetime(2025, 6, 1, tzinfo=ET)
    monitor = PositionMonitor(clock=ManualClock(today))
    expiry = dt.date(2025, 6, 21)
    pos = _pos(
        trade_id="bcs1",
        contracts=2,
        legs=[
            Leg("SPY", expiry, 450.0, "C", side=+1),
            Leg("SPY", expiry, 460.0, "C", side=-1),
        ],
    )
    monitor.add(pos)

    def chain(_u, _e, strike, _r):
        return {"delta": 0.5 if strike == 450 else 0.3, "vega": 0.1, "theta": -0.01}

    monitor.remark_greeks(chain)

    book = GreeksBook(account_size=50_000)
    # Pre-populate with stale entry to ensure rebuild really clears.
    book.add_position("stale", underlying="QQQ", delta=10.0, vega=10.0, theta=-1.0)

    book.rebuild_from(monitor.all_positions())

    assert book.position_count() == 1
    assert book.net_delta == pytest.approx(pos.delta)
    assert book.net_vega == pytest.approx(pos.vega)
