"""Tests for paper_trade.py exit execution and position carry-over logic."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Load paper_trade module dynamically
orig_env = dict(os.environ)
ROOT_DIR = Path(__file__).resolve().parent.parent
paper_trade_path = ROOT_DIR / "scripts" / "paper_trade.py"
spec = importlib.util.spec_from_file_location("paper_trade", paper_trade_path)
paper_trade = importlib.util.module_from_spec(spec)
sys.modules["paper_trade"] = paper_trade
spec.loader.exec_module(paper_trade)

# Restore environment
for k in list(os.environ.keys()):
    if k not in orig_env:
        try:
            del os.environ[k]
        except Exception:
            pass
for k, v in orig_env.items():
    try:
        os.environ[k] = v
    except Exception:
        pass

from tradingbot.execution.monitor import Leg, OpenPosition, PositionMonitor
from tradingbot.sizing.limits import GreeksBook


def _pos(trade_id: str = "t1", ticker: str = "SPY", **overrides) -> OpenPosition:
    expiry = dt.date.today() + dt.timedelta(days=10)
    base = dict(
        trade_id=trade_id,
        ticker=ticker,
        underlying=ticker,
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
        delta=0.4,
        vega=0.1,
        theta=-0.05,
        current_dte=30,
        legs=[
            Leg(ticker, expiry, 450.0, "C", side=+1, contracts=1),
            Leg(ticker, expiry, 460.0, "C", side=-1, contracts=1),
        ]
    )
    base.update(overrides)
    return OpenPosition(**base)


@patch("paper_trade._broker")
def test_close_position_on_broker(mock_broker_func):
    mock_broker = MagicMock()
    mock_fill = MagicMock()
    mock_fill.leg_id = "oid-123"
    mock_fill.status.value = "filled"
    mock_fill.filled_qty = 2
    mock_fill.avg_fill_price = 0.5
    mock_fill.error = None
    mock_broker.submit_leg.return_value = mock_fill
    mock_broker_func.return_value = mock_broker

    pos = _pos()
    fills = paper_trade._close_position_on_broker(pos)

    assert len(fills) == 2
    assert fills[0]["status"] == "filled"
    assert fills[0]["leg_id"] == "oid-123"
    # The first leg originally has side=+1, so the close leg should have side=-1
    assert fills[0]["side"] == -1
    # The second leg originally has side=-1, so the close leg should have side=+1
    assert fills[1]["side"] == 1
    assert mock_broker.submit_leg.call_count == 2


@patch("paper_trade._close_position_on_broker")
def test_process_exit_alerts(mock_close_broker):
    mock_close_broker.return_value = [{
        "leg": 0,
        "status": "filled",
        "leg_id": "oid-123",
        "side": -1,
        "qty": 2,
        "strike": 450.0,
        "right": "C"
    }]

    monitor = PositionMonitor()
    pos = _pos("trade-xyz", "AAPL")
    monitor.add(pos)

    book = GreeksBook(account_size=50000)
    book.add_position("trade-xyz", "AAPL", delta=0.8, vega=0.2, theta=-0.1, contracts=2)

    alerts = [
        {
            "trade_id": "trade-xyz",
            "should_exit": True,
            "reason": "PROFIT_TARGET",
        }
    ]

    with patch.dict(os.environ, {"OA2_SUBMIT_ORDERS": "1"}):
        paper_trade._process_exit_alerts(alerts, monitor, book, dry_run=False)

    assert monitor.get("trade-xyz") is None
    assert book.position_count() == 0
    assert mock_close_broker.call_count == 1


def test_process_exit_alerts_dry_run():
    monitor = PositionMonitor()
    pos = _pos("trade-xyz", "AAPL")
    monitor.add(pos)

    book = GreeksBook(account_size=50000)
    book.add_position("trade-xyz", "AAPL", delta=0.8, vega=0.2, theta=-0.1, contracts=2)

    alerts = [
        {
            "trade_id": "trade-xyz",
            "should_exit": True,
            "reason": "PROFIT_TARGET",
        }
    ]

    with patch("paper_trade._close_position_on_broker") as mock_close:
        paper_trade._process_exit_alerts(alerts, monitor, book, dry_run=True)
        assert mock_close.call_count == 0

    assert monitor.get("trade-xyz") is None
    assert book.position_count() == 0
