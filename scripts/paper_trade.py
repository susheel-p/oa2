"""oa2 automated paper trading runner.

Two modes:
  --full-scan   Run debaters → consensus → sizing → save positions (daily at 9:35 AM)
  --exit-only   Load positions → fetch quotes → check exits → log locally (every 1 min)

All logs stay local (logs/ directory). No GitHub push.

Usage:
    python scripts/paper_trade.py --full-scan               # daily entry scan
    python scripts/paper_trade.py --exit-only               # intraday exit checks
    python scripts/paper_trade.py --full-scan --dry-run     # test full-scan
    python scripts/paper_trade.py --exit-only --dry-run     # test exit monitor
    python scripts/paper_trade.py --tickers SPY QQQ         # scan subset only

Environment variables (all default ON for paper trading):
    OA2_FLAG_DEBATERS=1
    OA2_FLAG_REGIME=1
    OA2_FLAG_CONSENSUS=1
    OA2_FLAG_DEALER=1
    OA2_FLAG_BANDIT=1
    OA2_FLAG_SIZING=1
    OA2_FLAG_EXIT=1
    OA2_ACCOUNT_SIZE=50000      (override account size, default $50,000)
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from scripts import telegram_notify
except Exception:
    telegram_notify = None  # type: ignore

# ── Force all feature flags ON before any oa2 import ──────────────────────────
for _flag in (
    "OA2_FLAG_DEBATERS",
    "OA2_FLAG_REGIME",
    "OA2_FLAG_CONSENSUS",
    "OA2_FLAG_DEALER",
    "OA2_FLAG_BANDIT",
    "OA2_FLAG_SIZING",
    "OA2_FLAG_EXIT",
):
    os.environ.setdefault(_flag, "1")

# ── oa2 imports ───────────────────────────────────────────────────────────────
from oa2.execution.monitor import PositionMonitor
from oa2.graph.pipeline import run as pipeline_run
from oa2.sizing.limits import GreeksBook
from oa2.watchlist.builder import WATCHLIST

ET = ZoneInfo("America/New_York")
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


# =============================================================================
# Helpers
# =============================================================================

def _now_et() -> datetime.datetime:
    return datetime.datetime.now(ET)


def _today_str() -> str:
    return _now_et().strftime("%Y-%m-%d")


def _ts() -> str:
    return _now_et().strftime("%Y-%m-%dT%H:%M:%S%z")


def _log(msg: str) -> None:
    print(f"[{_ts()}] {msg}", flush=True)


def _is_market_day() -> bool:
    """Return False on weekends. Does not check public holidays."""
    return _now_et().weekday() < 5


def _is_market_open() -> bool:
    """Cash session 09:30–16:00 ET, weekdays. Ignores holidays."""
    now = _now_et()
    if now.weekday() >= 5:
        return False
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_t <= now <= close_t


# ── Broker submission (moomoo paper / SIMULATE) ──────────────────────────────
_BROKER = None  # lazy singleton


def _broker():
    global _BROKER
    if _BROKER is None:
        from oa2.execution.moomoo_broker import MoomooBroker
        _BROKER = MoomooBroker()
    return _BROKER


def _submit_to_broker(ticker: str, decision: dict, structure_pick: dict) -> list[dict]:
    """Build LegSpecs from a sized_approved decision + structure pick and submit.

    Returns a list of fill dicts (one per leg) for logging. Never raises.
    """
    import datetime as _dt
    import uuid
    from oa2.execution.broker import LegSpec

    contracts = int(decision.get("contracts") or 0)
    if contracts <= 0:
        return [{"error": "no contracts approved"}]

    structure = (structure_pick.get("structure") or "").lower()
    long_strike = structure_pick.get("long_strike")
    short_strike = structure_pick.get("short_strike")
    expiry_str = structure_pick.get("expiry")
    if not long_strike:
        return [{"error": f"missing long_strike in structure_pick: {structure_pick}"}]

    if expiry_str:
        try:
            expiry = _dt.date.fromisoformat(expiry_str)
        except Exception as e:
            return [{"error": f"bad expiry {expiry_str!r}: {e}"}]
    else:
        today = _now_et().date()
        days_ahead = (4 - today.weekday()) % 7 or 7
        expiry = today + _dt.timedelta(days=days_ahead)

    is_call = "call" in structure
    right = "C" if is_call else "P"

    legs: list[LegSpec] = [
        LegSpec(
            underlying=ticker, expiry=expiry, strike=float(long_strike),
            right=right, side=+1, contracts=contracts, limit_price=None,
        )
    ]
    if short_strike:
        legs.append(LegSpec(
            underlying=ticker, expiry=expiry, strike=float(short_strike),
            right=right, side=-1, contracts=contracts, limit_price=None,
        ))

    fills: list[dict] = []
    trade_id = decision.get("exit_rules", {}).get("trade_id") or uuid.uuid4().hex[:12]
    try:
        broker = _broker()
    except Exception as e:
        return [{"error": f"broker init failed: {e}"}]

    for i, leg in enumerate(legs):
        cid = f"{trade_id}-{i}"
        try:
            fill = broker.submit_leg(cid, leg)
            fills.append({
                "leg": i, "cid": cid, "side": leg.side, "strike": leg.strike,
                "right": leg.right, "qty": leg.contracts,
                "leg_id": fill.leg_id, "status": fill.status.value,
                "filled_qty": fill.filled_qty, "avg_fill_price": fill.avg_fill_price,
                "error": fill.error,
            })
        except Exception as e:
            fills.append({"leg": i, "cid": cid, "error": f"submit raised: {e}"})
    return fills


# =============================================================================
# Exit-only mode (intraday monitoring)
# =============================================================================

def _run_exit_only(account_size: float, dry_run: bool) -> None:
    """Load today's positions, fetch fresh quotes, run exit engine, log alerts."""
    import yfinance as yf

    from oa2.execution.exit import ExitEngine
    from oa2.execution.monitor import PositionMonitor

    today = _today_str()
    positions_path = LOG_DIR / f"positions_{today}.json"

    if not positions_path.exists():
        _log("No open positions file found — run --full-scan first.")
        return

    monitor = PositionMonitor.load(positions_path)
    positions = list(monitor.open_positions.values()) if monitor.open_positions else []

    if not positions:
        _log("No open positions to monitor.")
        return

    _log(f"Checking {len(positions)} open position(s) ...")

    # Fetch fresh quotes via yfinance (single batch call)
    tickers = list({p.underlying for p in positions})
    quotes = {}
    try:
        data = yf.download(tickers, period="1d", progress=False)
        for t in tickers:
            try:
                if len(tickers) == 1:
                    quotes[t] = float(data["Close"].dropna().iloc[-1])
                else:
                    quotes[t] = float(data["Close"][t].dropna().iloc[-1])
            except Exception:
                pass
    except Exception as exc:
        _log(f"Quote fetch failed: {exc}")

    engine = ExitEngine()
    alerts = []
    now_et = _now_et()
    context = {"current_time": now_et}

    for pos in positions:
        fresh_price = quotes.get(pos.underlying)
        if fresh_price:
            # Simple PnL re-mark: delta approximation
            price_move = fresh_price - pos.entry_price
            approx_pnl = pos.delta * price_move * pos.contracts
            monitor.mark_to_market(
                pos.trade_id,
                current_pnl=approx_pnl,
                current_underlying_price=fresh_price,
                current_dte=pos.current_dte,
            )
            pos = monitor.get(pos.trade_id)  # get updated object

        decision = engine.evaluate(pos, context)
        if decision.fired:
            alert = {
                "ts": _ts(),
                "trade_id": pos.trade_id,
                "ticker": pos.ticker,
                "should_exit": decision.should_exit,
                "needs_review": decision.needs_review,
                "reason": decision.reason.value if decision.reason else None,
                "urgency": decision.urgency.value if decision.urgency else None,
                "detail": decision.detail,
                "current_pnl": decision.current_pnl,
                "current_dte": decision.current_dte,
            }
            alerts.append(alert)
            urgency = (decision.urgency.value if decision.urgency else "").upper()
            _log(
                f"  EXIT ALERT [{urgency}] {pos.ticker} {pos.trade_id}: "
                f"{decision.detail}"
            )

    # Log alerts locally (don't push to GitHub — too frequent, too expensive)
    alerts_path = LOG_DIR / f"exit_alerts_{today}.jsonl"
    with open(alerts_path, "a") as f:
        for alert in alerts:
            f.write(json.dumps(alert) + "\n")
            f.flush()

    _log(f"Exit check done: {len(alerts)} alert(s) written to {alerts_path}")


# =============================================================================
# Single-ticker scan
# =============================================================================

def _scan_ticker(
    ticker: str,
    book: GreeksBook,
    monitor: PositionMonitor,
    account_size: float,
) -> dict:
    """Run the full pipeline for one ticker. Never raises — errors are caught."""
    start = time.time()
    result: dict = {
        "ticker": ticker,
        "ts": _ts(),
        "status": "error",
        "error": None,
        "decision": None,
        "sizing": None,
        "exit_alerts": [],
        "duration_ms": 0,
    }
    try:
        ctx = pipeline_run(
            ticker=ticker,
            account_size=account_size,
            book=book,
            monitor=monitor,
        )
        result["status"] = ctx.decision.get("status", "unknown") if ctx.decision else "unknown"
        result["decision"] = ctx.decision
        result["sizing"] = ctx.sizing
        result["exit_alerts"] = ctx.open_position_exits or []
        result["regime"] = (
            ctx.attribution.get("regime", {}) if ctx.attribution else {}
        )
        result["consensus"] = (
            ctx.attribution.get("consensus", {}) if ctx.attribution else {}
        )
        result["structure_pick"] = (
            ctx.attribution.get("structure_pick", {}) if ctx.attribution else {}
        )
    except Exception as exc:
        result["error"] = traceback.format_exc()
        _log(f"  ERROR scanning {ticker}: {exc}")
    finally:
        result["duration_ms"] = round((time.time() - start) * 1000)
    return result


# =============================================================================
# Daily summary
# =============================================================================

def _build_summary(results: list[dict], account_size: float, book: GreeksBook) -> dict:
    approved = [r for r in results if r.get("status") == "sized_approved"]
    rejected = [r for r in results if r.get("status") == "sized_rejected"]
    errors   = [r for r in results if r.get("status") == "error"]
    exit_alerts = [a for r in results for a in r.get("exit_alerts", [])]

    return {
        "date": _today_str(),
        "run_ts": _ts(),
        "account_size": account_size,
        "tickers_scanned": len(results),
        "approved_count": len(approved),
        "rejected_count": len(rejected),
        "error_count": len(errors),
        "exit_alert_count": len(exit_alerts),
        "approved_tickers": [r["ticker"] for r in approved],
        "exit_alerts": exit_alerts,
        "book_state": book.summary(),
        "errors": [{"ticker": r["ticker"], "error": r["error"]} for r in errors],
    }


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="oa2 automated paper trading runner")
    parser.add_argument("--dry-run", action="store_true",
                        help="Test mode: log to console, do not write files")
    parser.add_argument("--full-scan", action="store_true",
                        help="Run full entry scan (default if no --exit-only)")
    parser.add_argument("--exit-only", action="store_true",
                        help="Check exits on open positions only; no debaters/consensus")
    parser.add_argument("--tickers", nargs="*", default=None,
                        help="Override ticker list (default: all 22)")
    parser.add_argument("--account-size", type=float,
                        default=float(os.environ.get("OA2_ACCOUNT_SIZE", "50000")),
                        help="Account size in dollars (default $50,000)")
    parser.add_argument("--skip-weekend", action="store_true",
                        help="Exit silently on weekends")
    args = parser.parse_args()

    # Weekend guard
    if args.skip_weekend and not _is_market_day():
        _log("Weekend — skipping run.")
        sys.exit(0)

    # Dispatch: exit-only mode or full-scan mode
    if args.exit_only:
        _run_exit_only(account_size=args.account_size, dry_run=args.dry_run)
        return

    tickers = args.tickers if args.tickers else WATCHLIST
    account_size = args.account_size
    today = _today_str()

    _log("=" * 60)
    _log(f"oa2 paper trading run — {today}")
    _log(f"Tickers: {len(tickers)}  |  Account: ${account_size:,.0f}")
    _log(f"Dry run: {args.dry_run}")
    _log("=" * 60)

    # Shared book and monitor across all tickers
    book = GreeksBook(account_size=account_size)
    monitor = PositionMonitor()

    # ── Scan all tickers ──────────────────────────────────────────────────────
    results: list[dict] = []
    log_path = LOG_DIR / f"paper_trade_{today}.jsonl"

    with open(log_path, "a") as log_file:
        for ticker in tickers:
            _log(f"  Scanning {ticker} ...")
            result = _scan_ticker(ticker, book, monitor, account_size)
            results.append(result)

            status = result["status"]
            contracts = (result.get("sizing") or {}).get("contracts", 0)
            n_exits = len(result.get("exit_alerts", []))

            if status == "sized_approved":
                _log(f"    [OK] APPROVED -- {contracts} contract(s)")
                fills: list[dict] = []
                if not args.dry_run and os.getenv("OA2_SUBMIT_ORDERS", "1") != "0":
                    fills = _submit_to_broker(
                        ticker=ticker,
                        decision=result.get("decision") or {},
                        structure_pick=result.get("structure_pick") or {},
                    )
                    result["broker_fills"] = fills
                    for f in fills:
                        if f.get("error"):
                            _log(f"      [BROKER] ERR leg{f.get('leg','?')}: {f['error']}")
                        else:
                            _log(
                                f"      [BROKER] leg{f['leg']} {('BUY' if f['side']>0 else 'SELL')} "
                                f"{f['qty']}x {ticker} {f['strike']}{f['right']} "
                                f"-> {f['status']} (oid={f['leg_id']})"
                            )
                if telegram_notify is not None:
                    try:
                        telegram_notify.notify_trade(ticker, result, fills or None)
                    except Exception as e:
                        _log(f"      [TG] notify failed: {e}")

            elif status == "sized_rejected":
                reason = (result.get("sizing") or {}).get("reject_reason", "")
                _log(f"    [X] REJECTED -- {reason[:60]}")
            elif status == "error":
                _log(f"    [X] ERROR")
            else:
                _log(f"    -- {status}")

            if n_exits:
                _log(f"    [!] {n_exits} exit alert(s) for open positions")

            # Write result to log AFTER any broker submission so fills are persisted
            log_file.write(json.dumps(result, default=str) + "\n")
            log_file.flush()

    # ── Save open positions for exit monitor ──────────────────────────────────
    positions_path = LOG_DIR / f"positions_{today}.json"
    monitor.save(positions_path)

    # ── Build and write daily summary ─────────────────────────────────────────
    summary = _build_summary(results, account_size, book)
    summary_path = LOG_DIR / f"summary_{today}.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    if telegram_notify is not None:
        try:
            telegram_notify.notify_summary(summary)
        except Exception as e:
            _log(f"[TG] summary notify failed: {e}")

    _log("-" * 60)
    _log(f"Run complete: {summary['approved_count']} approved, "
         f"{summary['rejected_count']} rejected, "
         f"{summary['error_count']} errors, "
         f"{summary['exit_alert_count']} exit alerts")
    _log(f"Logs: {log_path}")
    _log(f"Summary: {summary_path}")

    _log("=" * 60)

    # Exit non-zero if any ticker errored, so the caller/scheduler knows
    if summary["error_count"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
