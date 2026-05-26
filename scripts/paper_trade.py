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
    TRADINGBOT_FLAG_DEBATERS=1
    TRADINGBOT_FLAG_REGIME=1
    TRADINGBOT_FLAG_CONSENSUS=1
    TRADINGBOT_FLAG_DEALER=1
    TRADINGBOT_FLAG_BANDIT=1
    TRADINGBOT_FLAG_SIZING=1
    TRADINGBOT_FLAG_EXIT=1
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

from dotenv import load_dotenv
load_dotenv()

try:
    import telegram_notify
except Exception as e:
    print(f"WARNING: Failed to import telegram_notify: {e}")
    import traceback
    traceback.print_exc()
    telegram_notify = None  # type: ignore

# ── Force all feature flags ON before any oa2 import ──────────────────────────
for _flag in (
    "TRADINGBOT_FLAG_DEBATERS",
    "TRADINGBOT_FLAG_REGIME",
    "TRADINGBOT_FLAG_CONSENSUS",
    "TRADINGBOT_FLAG_DEALER",
    "TRADINGBOT_FLAG_BANDIT",
    "TRADINGBOT_FLAG_SIZING",
    "TRADINGBOT_FLAG_EXIT",
):
    os.environ.setdefault(_flag, "1")

# ── oa2 imports ───────────────────────────────────────────────────────────────
from tradingbot.execution.monitor import PositionMonitor, OpenPosition, Leg
from tradingbot.graph.pipeline import run as pipeline_run
from tradingbot.learning.knowledge_base import KnowledgeBase, default_kb_path
from tradingbot.sizing.limits import GreeksBook
from tradingbot.watchlist.builder import WATCHLIST

ET = ZoneInfo("America/New_York")


def _get_base_dir() -> Path:
    """Get base directory, respecting TRADINGBOT_HOME override."""
    env_home = os.getenv("TRADINGBOT_HOME")
    if env_home:
        return Path(env_home)
    return Path(__file__).parent.parent


LOG_DIR = _get_base_dir() / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


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


def _print_rag_status() -> None:
    """Print RAG learning status at startup."""
    kb_path = default_kb_path()
    try:
        kb = KnowledgeBase.load(kb_path)
        if not kb.tickers and not kb.posteriors:
            _log("RAG Learning: KB exists but empty (run backtest --bandit to seed)")
            return

        n_tickers = len(kb.tickers)
        n_posteriors = sum(len(v) for v in kb.posteriors.values())
        last_updated = kb.last_updated[-10:] if kb.last_updated else "unknown"

        _log(f"RAG Learning: ENABLED")
        _log(f"  KB last updated: {last_updated}")
        _log(f"  Tickers tracked: {n_tickers}")
        _log(f"  Outcomes used: {kb.n_outcomes_used}")
        _log(f"  Thompson posteriors: {n_posteriors}")

        # Count blacklisted tickers
        blacklisted = sum(
            1 for stats in kb.tickers.values()
            if stats.hit_rate < 0.43 and stats.dollar_weighted_win_rate < 0.45
        )
        if blacklisted > 0:
            _log(f"  ⚠️  {blacklisted} ticker(s) blacklisted (hit_rate < 43%)")

    except Exception as e:
        _log(f"RAG Learning: DISABLED (KB load error: {str(e)[:50]})")


def _log_execution(record: dict) -> None:
    """Append one execution event to logs/executions_{date}.jsonl. Never raises."""
    path = LOG_DIR / f"executions_{_today_str()}.jsonl"
    try:
        with open(path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
            f.flush()
    except Exception as e:
        _log(f"[EXEC LOG] write failed: {e}")


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
        import os
        from tradingbot.execution.moomoo_broker import MoomooBroker
        host = os.getenv("MOOMOO_OPEND_HOST", "127.0.0.1")
        port = int(os.getenv("MOOMOO_OPEND_PORT", 11111))
        _BROKER = MoomooBroker(host=host, port=port)
    return _BROKER


def _submit_to_broker(ticker: str, decision: dict, structure_pick: dict) -> list[dict]:
    """Build LegSpecs from a sized_approved decision + structure pick and submit.

    Returns a list of fill dicts (one per leg) for logging. Never raises.
    """
    import datetime as _dt
    import uuid
    from tradingbot.execution.broker import LegSpec

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
                "fill_time": fill.fill_time,
                "error": fill.error,
            })
        except Exception as e:
            fills.append({"leg": i, "cid": cid, "error": f"submit raised: {e}"})
    return fills


# =============================================================================
# Exit-only mode (intraday monitoring) and Broker close helpers
# =============================================================================

def _close_position_on_broker(pos: OpenPosition) -> list[dict]:
    """Submit opposite orders to the broker to close the position. Never raises."""
    from tradingbot.execution.broker import LegSpec
    
    fills: list[dict] = []
    try:
        broker = _broker()
    except Exception as e:
        return [{"error": f"broker init failed: {e}"}]

    close_id = f"close-{pos.trade_id[:8]}"
    for i, leg in enumerate(pos.legs):
        cid = f"{close_id}-{i}"
        # Reverse side to close:
        close_side = -leg.side
        close_leg = LegSpec(
            underlying=leg.underlying,
            expiry=leg.expiry,
            strike=leg.strike,
            right=leg.right,
            side=close_side,
            contracts=leg.contracts * pos.contracts,
            limit_price=None, # Market order to close
        )
        try:
            fill = broker.submit_leg(cid, close_leg)
            fills.append({
                "leg": i, "cid": cid, "side": close_side, "strike": leg.strike,
                "right": leg.right, "qty": close_leg.contracts,
                "leg_id": fill.leg_id, "status": fill.status.value,
                "filled_qty": fill.filled_qty, "avg_fill_price": fill.avg_fill_price,
                "fill_time": fill.fill_time,
                "error": fill.error,
            })
        except Exception as e:
            fills.append({"leg": i, "cid": cid, "error": f"submit close raised: {e}"})
    return fills


def _process_exit_alerts(
    alerts: list[dict],
    monitor: PositionMonitor,
    book: GreeksBook | None,
    dry_run: bool,
) -> None:
    """Submit offsetting close orders for exit alerts, and remove them from monitor/book."""
    for alert in alerts:
        if alert.get("should_exit"):
            trade_id = alert["trade_id"]
            pos = monitor.get(trade_id)
            if pos:
                monitor.lock_for_exit(trade_id)
                ticker = pos.ticker
                _log(f"Executing EXIT for {ticker} ({trade_id}): {alert['reason']}")
                fills: list[dict] = []
                if not dry_run and os.getenv("OA2_SUBMIT_ORDERS", "1") != "0":
                    fills = _close_position_on_broker(pos)
                    alert["broker_fills"] = fills
                    for f in fills:
                        if f.get("error"):
                            _log(f"  [BROKER EXIT] ERR leg{f.get('leg','?')}: {f['error']}")
                        else:
                            _log(
                                f"  [BROKER EXIT] leg{f['leg']} {('BUY' if f['side']>0 else 'SELL')} "
                                f"{f['qty']}x {ticker} {f['strike']}{f['right']} "
                                f"-> {f['status']} (oid={f['leg_id']})"
                            )

                # Log execution event (EXIT)
                _log_execution({
                    "ts": _ts(),
                    "event": "EXIT",
                    "trigger": "dry_run" if dry_run else "exit_only",
                    "trade_id": trade_id,
                    "ticker": ticker,
                    "direction": None,
                    "structure": None,
                    "contracts": len(pos.legs),
                    "dry_run": dry_run,
                    "legs": fills,
                    "exit_reason": alert.get("reason"),
                    "exit_urgency": alert.get("urgency"),
                    "current_pnl": alert.get("current_pnl"),
                })

                # Notify via Telegram
                if telegram_notify is not None:
                    try:
                        success = telegram_notify.notify_exit(ticker, alert)
                        if success:
                            _log(f"  [TG] Exit alert sent via Telegram")
                        else:
                            _log(f"  [TG] Exit alert failed (API returned False)")
                    except Exception as e:
                        _log(f"  [TG] exit notify failed: {e}")

                monitor.remove(trade_id)
                if book is not None:
                    book.remove_position(trade_id)


def _run_entry_only(account_size: float, tickers: list[str], dry_run: bool) -> None:
    """Entry-only mode: scan for new positions, skip tickers with open positions.

    Guards against entry/exit conflicts:
    - Skip any ticker with an open position (unless locked for exit)
    - Lock prevents entry from adding to positions being closed
    """
    from tradingbot.execution.monitor import PositionMonitor

    today = _today_str()
    positions_path = LOG_DIR / f"positions_{today}.json"

    # Carry-over logic: load from previous day if today's doesn't exist yet
    if not positions_path.exists():
        positions_files = sorted(LOG_DIR.glob("positions_*.json"))
        positions_files = [p for p in positions_files if p.name != f"positions_{today}.json"]
        if positions_files:
            latest_file = positions_files[-1]
            _log(f"Carrying over open positions from previous session: {latest_file}")
            monitor = PositionMonitor.load(latest_file)
        else:
            _log("No previous open positions found. Starting fresh.")
            monitor = PositionMonitor()
        monitor.save(positions_path)
    else:
        monitor = PositionMonitor.load(positions_path)

    # Filter tickers: skip those with open positions (entry guards)
    eligible_tickers = [t for t in tickers if not monitor.has_position_for(t)]
    skipped_tickers = [t for t in tickers if monitor.has_position_for(t)]

    if skipped_tickers:
        _log(f"Skipping {len(skipped_tickers)} ticker(s) with open position(s): {', '.join(skipped_tickers)}")

    if not eligible_tickers:
        _log("All tickers have open positions. No entry opportunities.")
        return

    _print_rag_status()
    _log(f"Running entry scan for {len(eligible_tickers)} eligible ticker(s) ...")

    # Run full pipeline on eligible tickers
    book = GreeksBook(account_size=account_size)
    for pos in monitor.all_positions():
        book.add_position(
            trade_id=pos.trade_id,
            underlying=pos.ticker,
            delta=pos.delta,
            vega=pos.vega,
            theta=pos.theta,
            contracts=pos.contracts,
        )

    results: list[dict] = []
    log_path = LOG_DIR / f"paper_trade_{today}.jsonl"

    with open(log_path, "a") as log_file:
        for ticker in eligible_tickers:
            try:
                result = _scan_ticker(ticker, book, monitor, account_size)
                results.append(result)
            except Exception as e:
                _log(f"  ERROR scanning {ticker}: {e}")
                results.append({
                    "ticker": ticker,
                    "ts": _ts(),
                    "status": "error",
                    "error": str(e),
                })

            # Log to JSONL
            log_file.write(json.dumps(result) + "\n")
            log_file.flush()

    # Save updated positions
    monitor.save(positions_path)

    # Summary
    approved_count = sum(1 for r in results if r.get("status") == "approved")
    rejected_count = sum(1 for r in results if r.get("status") == "rejected")
    _log(f"Entry scan done: {approved_count} approved, {rejected_count} rejected")


def _run_exit_only(account_size: float, dry_run: bool) -> None:
    """Load today's positions, fetch fresh quotes, run exit engine, log alerts."""
    import yfinance as yf

    from tradingbot.execution.exit import ExitEngine
    from tradingbot.execution.monitor import PositionMonitor

    today = _today_str()
    positions_path = LOG_DIR / f"positions_{today}.json"

    # Carry-over logic: load from previous day if today's doesn't exist yet
    if not positions_path.exists():
        positions_files = sorted(LOG_DIR.glob("positions_*.json"))
        positions_files = [p for p in positions_files if p.name != f"positions_{today}.json"]
        if positions_files:
            latest_file = positions_files[-1]
            _log(f"Carrying over open positions from previous session: {latest_file}")
            monitor = PositionMonitor.load(latest_file)
        else:
            _log("No open positions file found.")
            return
        # Save immediately so subsequent checks find it
        monitor.save(positions_path)
    else:
        monitor = PositionMonitor.load(positions_path)

    positions = monitor.all_positions()

    if not positions:
        _log("No open positions to monitor.")
        return

    _print_rag_status()
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

    # Process and execute exits if any fired
    fired_exits = [a for a in alerts if a.get("should_exit")]
    if fired_exits:
        _process_exit_alerts(fired_exits, monitor, None, dry_run)
        monitor.save(positions_path)

    # Log alerts locally
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
        # Store pricing/greeks parameters for monitor registration
        if ctx.market_data:
            result["entry_price"] = float(ctx.market_data.get("current_price") or ctx.market_data.get("price") or 0.0)
            result["entry_premium"] = float(ctx.market_data.get("max_loss") or 0.0)
            result["delta_per_contract"] = float(ctx.market_data.get("delta_per_contract", 0.0))
            result["vega_per_contract"] = float(ctx.market_data.get("vega_per_contract", 0.0))
            result["theta_per_contract"] = float(ctx.market_data.get("theta_per_contract", 0.0))
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
# Premarket scan (8:00 AM — live premarket prices, no broker submission)
# =============================================================================

def _run_premarket_scan(account_size: float, tickers: list[str] | None, dry_run: bool) -> None:
    """Run full debater/consensus/sizing pipeline using premarket prices.

    Saves results to paper_trade_{today}_premarket.jsonl and
    summary_{today}_premarket.json. Never submits broker orders — this is
    a read-only signal scan so the 8:30 AM report has fresh data.
    """
    tickers = tickers if tickers else WATCHLIST
    today = _today_str()

    _log("=" * 60)
    _log(f"oa2 premarket scan — {today}")
    _log(f"Tickers: {len(tickers)}  |  Account: ${account_size:,.0f}")
    _log(f"Dry run: {dry_run}  |  Broker submission: DISABLED (premarket)")
    _log("=" * 60)

    book = GreeksBook(account_size=account_size)
    monitor = PositionMonitor()

    # Carry over open positions so Greeks caps are respected
    positions_files = sorted(LOG_DIR.glob("positions_*.json"))
    if positions_files:
        monitor = PositionMonitor.load(positions_files[-1])
        for pos in monitor.all_positions():
            book.add_position(
                trade_id=pos.trade_id,
                underlying=pos.ticker,
                delta=pos.delta,
                vega=pos.vega,
                theta=pos.theta,
                contracts=pos.contracts,
            )

    results: list[dict] = []
    log_path = LOG_DIR / f"paper_trade_{today}_premarket.jsonl"

    with open(log_path, "w") as log_file:
        for ticker in tickers:
            _log(f"  Scanning {ticker} ...")
            result = _scan_ticker(ticker, book, monitor, account_size)
            results.append(result)
            log_file.write(json.dumps(result, default=str) + "\n")
            log_file.flush()

            status = result["status"]
            if status == "sized_approved":
                _log(f"    [PM] APPROVED (no broker order — premarket scan only)")
            elif status == "sized_rejected":
                reason = (result.get("decision") or {}).get("sizing_reject_reason", "?")
                _log(f"    [PM] REJECTED: {reason}")
            else:
                _log(f"    [PM] {status}")

    summary = _build_summary(results, account_size, book)
    summary["scan_type"] = "premarket"
    summary_path = LOG_DIR / f"summary_{today}_premarket.json"
    if not dry_run:
        summary_path.write_text(json.dumps(summary, indent=2, default=str))

    approved = [r for r in results if r.get("status") == "sized_approved"]
    rejected = [r for r in results if r.get("status") == "sized_rejected"]
    _log(f"Premarket scan done: {len(approved)} approved, {len(rejected)} rejected → {log_path}")

    if telegram_notify:
        try:
            msg = (
                f"Premarket scan complete — {today}\n"
                f"Scanned: {len(results)} | Approved: {len(approved)} | Rejected: {len(rejected)}"
            )
            telegram_notify.send(msg)
        except Exception:
            pass


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="oa2 automated paper trading runner")
    parser.add_argument("--dry-run", action="store_true",
                        help="Test mode: log to console, do not write files")
    parser.add_argument("--full-scan", action="store_true",
                        help="Run full entry scan (default if no --exit-only)")
    parser.add_argument("--premarket-scan", action="store_true",
                        help="Run premarket scan at 8:00 AM using live premarket prices; saves to paper_trade_{date}_premarket.jsonl; never submits broker orders")
    parser.add_argument("--exit-only", action="store_true",
                        help="Check exits on open positions only; no debaters/consensus")
    parser.add_argument("--entry-only", action="store_true",
                        help="Run entry scan only (debaters → consensus → sizing); skip tickers with open positions")
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

    # Dispatch: exit-only mode, entry-only mode, premarket-scan mode, or full-scan mode
    if args.exit_only:
        _run_exit_only(account_size=args.account_size, dry_run=args.dry_run)
        return

    if args.entry_only:
        tickers = args.tickers if args.tickers else WATCHLIST
        _run_entry_only(account_size=args.account_size, tickers=tickers, dry_run=args.dry_run)
        return

    if args.premarket_scan:
        _run_premarket_scan(account_size=args.account_size, tickers=args.tickers, dry_run=args.dry_run)
        return

    tickers = args.tickers if args.tickers else WATCHLIST
    account_size = args.account_size
    today = _today_str()

    _log("=" * 60)
    _log(f"oa2 paper trading run — {today}")
    _log(f"Tickers: {len(tickers)}  |  Account: ${account_size:,.0f}")
    _log(f"Dry run: {args.dry_run}")
    _log("=" * 60)

    _print_rag_status()

    # Shared book and monitor across all tickers
    book = GreeksBook(account_size=account_size)
    
    # Load open positions from previous day (carry-over logic)
    positions_path = LOG_DIR / f"positions_{today}.json"
    if not positions_path.exists():
        positions_files = sorted(LOG_DIR.glob("positions_*.json"))
        positions_files = [p for p in positions_files if p.name != f"positions_{today}.json"]
        if positions_files:
            latest_file = positions_files[-1]
            _log(f"Carrying over open positions from previous session: {latest_file}")
            monitor = PositionMonitor.load(latest_file)
            for pos in monitor.all_positions():
                book.add_position(
                    trade_id=pos.trade_id,
                    underlying=pos.ticker,
                    delta=pos.delta,
                    vega=pos.vega,
                    theta=pos.theta,
                    contracts=pos.contracts,
                )
        else:
            _log("No previous open positions found. Starting fresh.")
            monitor = PositionMonitor()
    else:
        monitor = PositionMonitor.load(positions_path)
        for pos in monitor.all_positions():
            book.add_position(
                trade_id=pos.trade_id,
                underlying=pos.ticker,
                delta=pos.delta,
                vega=pos.vega,
                theta=pos.theta,
                contracts=pos.contracts,
            )

    # ── Scan all tickers ──────────────────────────────────────────────────────
    results: list[dict] = []
    log_path = LOG_DIR / f"paper_trade_{today}.jsonl"
    MAX_TICKER_TIME = 300  # 5 min per ticker max before timeout

    with open(log_path, "a") as log_file:
        for ticker in tickers:
            _log(f"  Scanning {ticker} ...")
            start_time = time.time()
            try:
                result = _scan_ticker(ticker, book, monitor, account_size)
                elapsed = time.time() - start_time
                if elapsed > MAX_TICKER_TIME:
                    result = {
                        "ticker": ticker,
                        "ts": _ts(),
                        "status": "error",
                        "error": f"Ticker scan timed out after {elapsed:.1f}s (> {MAX_TICKER_TIME}s)",
                        "duration_ms": round(elapsed * 1000)
                    }
                results.append(result)
            except Exception as e:
                results.append({
                    "ticker": ticker,
                    "ts": _ts(),
                    "status": "error",
                    "error": str(e),
                    "duration_ms": round((time.time() - start_time) * 1000)
                })

            status = result["status"]
            contracts = (result.get("sizing") or {}).get("contracts", 0)
            n_exits = len(result.get("exit_alerts", []))

            if status == "sized_approved":
                struct_pick = result.get("structure_pick") or {}
                # Validate that structure has actual data before submitting
                if not struct_pick.get("long_strike"):
                    _log(f"    [SKIP] APPROVED but no structure found (no_viable_structure)")
                    results.append(result)
                    continue

                _log(f"    [OK] APPROVED -- {contracts} contract(s)")
                fills: list[dict] = []
                if not args.dry_run and os.getenv("OA2_SUBMIT_ORDERS", "1") != "0":
                    fills = _submit_to_broker(
                        ticker=ticker,
                        decision=result.get("decision") or {},
                        structure_pick=struct_pick,
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

                # Construct and register the OpenPosition in monitor and book
                decision = result.get("decision") or {}
                struct_pick = result.get("structure_pick") or {}
                exit_rules = decision.get("exit_rules") or {}
                regime = result.get("regime") or {}

                trade_id = exit_rules.get("trade_id") or uuid.uuid4().hex[:12]

                # Log execution event (ENTRY)
                _log_execution({
                    "ts": _ts(),
                    "event": "ENTRY",
                    "trigger": "dry_run" if args.dry_run else "full_scan",
                    "trade_id": trade_id,
                    "ticker": ticker,
                    "direction": decision.get("direction"),
                    "structure": struct_pick.get("structure"),
                    "contracts": contracts,
                    "dry_run": args.dry_run,
                    "legs": fills,
                    "exit_reason": None,
                    "exit_urgency": None,
                    "current_pnl": None,
                })

                # Expiry string parsing
                import datetime as _dt
                import uuid
                expiry_str = struct_pick.get("expiry")
                expiry = None
                if expiry_str:
                    try:
                        expiry = _dt.date.fromisoformat(expiry_str)
                    except Exception:
                        pass
                if expiry is None:
                    today_date = _now_et().date()
                    days_ahead = (4 - today_date.weekday()) % 7 or 7
                    expiry = today_date + _dt.timedelta(days=days_ahead)

                # Build Legs
                is_call = "call" in (struct_pick.get("structure") or "").lower()
                right = "C" if is_call else "P"
                long_strike = struct_pick.get("long_strike")
                short_strike = struct_pick.get("short_strike")

                legs = []
                if long_strike:
                    legs.append(Leg(
                        underlying=ticker,
                        expiry=expiry,
                        strike=float(long_strike),
                        right=right,
                        side=+1,
                        contracts=1
                    ))
                if short_strike:
                    legs.append(Leg(
                        underlying=ticker,
                        expiry=expiry,
                        strike=float(short_strike),
                        right=right,
                        side=-1,
                        contracts=1
                    ))

                entry_price = float(result.get("entry_price") or 0.0)
                entry_premium = float(result.get("entry_premium") or 0.0)
                entry_regime = int(regime.get("regime_id") or 0)
                entry_dte = int(struct_pick.get("dte") or 30)
                # Read max_loss/max_profit from exit_rules (set by sizing engine), not struct_pick
                max_loss_dollars = float(exit_rules.get("max_loss_dollars") or 0.0)
                max_profit_dollars = float(exit_rules.get("max_profit_dollars") or 0.0)
                max_profit_per = max_profit_dollars / contracts if contracts > 0 else 0.0
                max_loss_per = max_loss_dollars / contracts if contracts > 0 else 0.0

                delta = float(result.get("delta_per_contract", 0.0)) * contracts
                vega = float(result.get("vega_per_contract", 0.0)) * contracts
                theta = float(result.get("theta_per_contract", 0.0)) * contracts

                pos = OpenPosition(
                    trade_id=trade_id,
                    ticker=ticker,
                    underlying=ticker,
                    structure=struct_pick.get("structure") or "VERTICAL_CALL_SPREAD",
                    direction=decision.get("direction") or "BULLISH",
                    entry_price=entry_price,
                    entry_premium=entry_premium,
                    entry_time=time.time(),
                    entry_regime=entry_regime,
                    entry_dte=entry_dte,
                    contracts=contracts,
                    max_profit_per_contract=max_profit_per,
                    max_loss_per_contract=max_loss_per,
                    stop_loss_pct=float(exit_rules.get("stop_loss_pct", 1.0)),
                    profit_target_pct=float(exit_rules.get("profit_target_pct", 0.5)),
                    delta=delta,
                    vega=vega,
                    theta=theta,
                    current_pnl=0.0,
                    current_underlying_price=entry_price,
                    current_dte=entry_dte,
                    legs=legs
                )
                monitor.add(pos)
                book.add_position(
                    trade_id=trade_id,
                    underlying=ticker,
                    delta=delta,
                    vega=vega,
                    theta=theta,
                    contracts=contracts,
                )
                _log(f"    [MONITOR] Registered new open position {trade_id} for {ticker}")

                if telegram_notify is not None:
                    try:
                        success = telegram_notify.notify_trade(ticker, result, fills or None)
                        if success:
                            _log(f"      [TG] Alert sent via Telegram")
                        else:
                            _log(f"      [TG] Alert failed (API returned False)")
                    except Exception as e:
                        _log(f"      [TG] notify failed: {e}")
                        import traceback
                        _log(f"      [TG] traceback: {traceback.format_exc()}")
                else:
                    _log(f"      [TG] telegram_notify module not loaded")

            elif status == "sized_rejected":
                reason = (result.get("sizing") or {}).get("reject_reason", "")
                _log(f"    [X] REJECTED -- {reason[:60]}")
            elif status == "error":
                _log(f"    [X] ERROR")
            else:
                _log(f"    -- {status}")

            if n_exits:
                _log(f"    [!] {n_exits} exit alert(s) for open positions")
                exits_to_process = [a for a in result.get("exit_alerts", []) if a.get("should_exit")]
                if exits_to_process:
                    _process_exit_alerts(exits_to_process, monitor, book, args.dry_run)

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
            success = telegram_notify.notify_summary(summary)
            if success:
                _log(f"[TG] Summary alert sent via Telegram")
            else:
                _log(f"[TG] Summary alert failed (API returned False)")
        except Exception as e:
            _log(f"[TG] summary notify failed: {e}")
            import traceback
            _log(f"[TG] traceback: {traceback.format_exc()}")
    else:
        _log(f"[TG] telegram_notify module not loaded for summary")

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
