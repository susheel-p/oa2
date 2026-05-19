"""Outcome resolver — convert decision logs into wins/losses + P&L proxies.

Phase 1 of the RAG learning loop.

Reads `logs/paper_trade_<date>.jsonl` and resolves each APPROVED trade by:
  1. Fetching entry price (close on decision date)
  2. Fetching T+1 close (next trading day)
  3. Computing:
     - direction_hit: T+1 close moved in predicted direction
     - pnl_proxy_dollars: estimated P&L on the spread using a delta-based model
     - pnl_pct_of_max_loss: signed magnitude (+1.0 = max win, -1.0 = max loss)

The P&L proxy uses a simplified day-1 model:
    pnl_per_contract = direction_sign * price_move * ATM_SPREAD_DELTA * 100
    pnl_capped       = clip(pnl, -max_loss, +max_profit)

This is realistic for 30-DTE ATM debit verticals held overnight. For longer
holding periods we'd want full Black-Scholes repricing; for the daily learning
loop the simple proxy is adequate and consistent.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# Spread-delta approximation for an ATM debit vertical (~ 30 DTE).
# Empirical: long ATM call has delta ~0.55, short OTM call has delta ~0.30,
# so spread delta ~ 0.25-0.35. Use 0.30 as a calibrated midpoint.
ATM_SPREAD_DELTA = 0.30


@dataclass
class TradeOutcome:
    """One resolved trade — direction hit + P&L proxy."""
    trade_id: str
    ticker: str
    decision_date: str          # ISO date of the decision
    resolution_date: str        # ISO date of T+1 close used to resolve
    direction: str              # BULLISH | BEARISH
    structure: str              # vertical_call_spread, long_call, etc.
    long_strike: float
    short_strike: float | None
    max_profit: float           # per contract dollars
    max_loss: float             # per contract dollars (positive)
    contracts: int
    entry_price: float          # underlying close on decision date
    resolution_price: float     # underlying close T+1
    # Outcomes:
    direction_hit: bool
    pnl_proxy_dollars: float    # signed; per-contract
    pnl_pct_of_max_loss: float  # signed; +1.0 = max win, -1.0 = max loss
    total_pnl_dollars: float    # pnl_per_contract * contracts
    # Context for the learner:
    p_bull_at_entry: float
    regime_label: str
    debater_convictions: dict[str, float] = field(default_factory=dict)
    debater_directions: dict[str, str] = field(default_factory=dict)
    # Diagnostics:
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def simulate_spread_pnl(
    direction: str,
    long_strike: float,
    short_strike: float | None,
    max_profit: float,
    max_loss: float,
    entry_price: float,
    resolution_price: float,
    spread_delta: float = ATM_SPREAD_DELTA,
) -> tuple[float, float]:
    """Estimate one-day P&L for a debit vertical / outright long.

    Returns:
        (pnl_per_contract_dollars, pnl_pct_of_max_loss)

    Model:
        pnl_per_contract = direction_sign * (resolution - entry) * spread_delta * 100
        pnl_clipped      = clip(pnl, -max_loss, +max_profit)
        pnl_pct          = pnl_clipped / max_loss   (signed, +1.0 = max win)
    """
    direction_sign = 1.0 if direction == "BULLISH" else -1.0
    price_move = resolution_price - entry_price
    raw_pnl = direction_sign * price_move * spread_delta * 100.0
    # Cap at structure-defined max profit / max loss
    clipped = max(-max_loss, min(max_profit, raw_pnl))
    pnl_pct = clipped / max_loss if max_loss > 0 else 0.0
    return round(clipped, 2), round(pnl_pct, 4)


def _next_trading_day(date_str: str) -> str:
    """Return the next weekday after the given ISO date (skips weekends, not holidays)."""
    d = datetime.date.fromisoformat(date_str)
    d = d + datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d = d + datetime.timedelta(days=1)
    return d.isoformat()


def _fetch_close_yf(ticker: str, date_str: str) -> float | None:
    """Fetch the underlying close on the given date via yfinance.

    Uses a 5-day window ending on date_str and takes the last available close.
    Returns None if fetch fails or no data.
    """
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        end = datetime.date.fromisoformat(date_str) + datetime.timedelta(days=1)
        start = end - datetime.timedelta(days=10)
        hist = yf.download(
            ticker,
            start=start.isoformat(),
            end=end.isoformat(),
            progress=False,
            auto_adjust=True,
        )
        if hist is None or hist.empty:
            return None
        closes = hist["Close"]
        # Closes can be a Series or DataFrame depending on yfinance version
        if hasattr(closes, "columns"):
            closes = closes[ticker] if ticker in closes.columns else closes.iloc[:, 0]
        closes = closes.dropna()
        if closes.empty:
            return None
        # Find the close on or before date_str
        target = datetime.date.fromisoformat(date_str)
        for idx in reversed(closes.index):
            if idx.date() <= target:
                return float(closes.loc[idx])
        # Fallback: last close in window
        return float(closes.iloc[-1])
    except Exception:
        return None


def _extract_trade_id(record: dict, idx: int) -> str:
    """Synthesize a stable trade_id from the log record."""
    ticker = record.get("ticker", "UNK")
    ts = record.get("ts", "").split("T")[0]
    return f"{ticker}_{ts}_{idx}"


def resolve_outcomes_from_log(
    log_path: Path,
    price_fetcher=None,
) -> list[TradeOutcome]:
    """Read a paper_trade_<date>.jsonl and resolve all APPROVED trades.

    Args:
        log_path: path to a JSONL decision log.
        price_fetcher: callable (ticker, date) -> close | None. Defaults to
                       yfinance-backed `_fetch_close_yf`. Tests can inject a
                       deterministic stub.

    Returns:
        list of TradeOutcome (only for APPROVED trades that resolved cleanly).
    """
    if price_fetcher is None:
        price_fetcher = _fetch_close_yf

    outcomes: list[TradeOutcome] = []

    with open(log_path) as f:
        for idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            if record.get("status") != "sized_approved":
                continue

            ticker = record.get("ticker")
            decision_date = (record.get("ts") or "").split("T")[0]
            if not ticker or not decision_date:
                continue

            structure_pick = record.get("structure_pick") or {}
            sizing = record.get("sizing") or {}
            consensus = record.get("consensus") or {}
            regime = record.get("regime") or {}

            direction = consensus.get("direction")
            if direction not in ("BULLISH", "BEARISH"):
                continue

            long_strike = structure_pick.get("long_strike")
            short_strike = structure_pick.get("short_strike")
            max_profit = structure_pick.get("max_profit") or sizing.get("max_profit_dollars", 0.0)
            max_loss = sizing.get("max_dollars_at_risk") or structure_pick.get("max_loss", 0.0)
            if max_loss is None or max_loss <= 0 or max_profit is None or max_profit <= 0:
                continue

            contracts = int(sizing.get("contracts", 0) or 0)
            if contracts <= 0:
                continue

            # Fetch entry and resolution closes
            entry_price = price_fetcher(ticker, decision_date)
            resolution_date = _next_trading_day(decision_date)
            resolution_price = price_fetcher(ticker, resolution_date)

            if entry_price is None or resolution_price is None or entry_price <= 0:
                continue

            # Direction hit on T+1
            price_move = resolution_price - entry_price
            if direction == "BULLISH":
                direction_hit = price_move > 0
            else:
                direction_hit = price_move < 0

            # Per-contract P&L, then total
            # NOTE: max_profit/max_loss in structure_pick are per-contract dollars,
            # but sizing.max_dollars_at_risk is total. Detect and normalize.
            mp_per = max_profit
            ml_per = sizing.get("max_dollars_at_risk", 0.0) / contracts if contracts else max_loss
            # Prefer structure_pick values when present (per-contract)
            if structure_pick.get("max_loss"):
                ml_per = structure_pick["max_loss"]

            pnl_per_contract, pnl_pct = simulate_spread_pnl(
                direction=direction,
                long_strike=long_strike,
                short_strike=short_strike,
                max_profit=mp_per,
                max_loss=ml_per,
                entry_price=entry_price,
                resolution_price=resolution_price,
            )
            total_pnl = round(pnl_per_contract * contracts, 2)

            # Best-effort debater context (rebuild from consensus weights only — we
            # don't have per-debater opinions in the rolled-up log line).
            debater_convictions = consensus.get("weights") or {}

            regime_label = (
                f"{regime.get('vol_state', 'unknown')}_{regime.get('trend_state', 'unknown')}".lower()
            )

            outcome = TradeOutcome(
                trade_id=_extract_trade_id(record, idx),
                ticker=ticker,
                decision_date=decision_date,
                resolution_date=resolution_date,
                direction=direction,
                structure=structure_pick.get("structure", "unknown"),
                long_strike=long_strike,
                short_strike=short_strike,
                max_profit=mp_per,
                max_loss=ml_per,
                contracts=contracts,
                entry_price=round(entry_price, 4),
                resolution_price=round(resolution_price, 4),
                direction_hit=direction_hit,
                pnl_proxy_dollars=pnl_per_contract,
                pnl_pct_of_max_loss=pnl_pct,
                total_pnl_dollars=total_pnl,
                p_bull_at_entry=consensus.get("p_bull", 0.5),
                regime_label=regime_label,
                debater_convictions=debater_convictions,
                debater_directions={},
                diagnostics={
                    "kelly_f": sizing.get("kelly", {}).get("kelly_f"),
                    "odds": structure_pick.get("odds"),
                    "calibrator_mode": consensus.get("calibrator_mode"),
                },
            )
            outcomes.append(outcome)

    return outcomes
