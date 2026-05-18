"""oa2 premarket and postmarket report generator.

Generates human-readable Obsidian-compatible markdown reports for daily trading analysis.

Usage:
    python scripts/report.py --premarket [--date YYYY-MM-DD]       # 8:30 AM before market open
    python scripts/report.py --postmarket [--date YYYY-MM-DD]      # 4:15 PM after market close
    python scripts/report.py --trade-doc TRADE_ID [--date YYYY-MM-DD]

Reports are written to reports/ directory with Obsidian [[wikilinks]].

Environment:
    OA2_HOME — override base directory (default: parent of scripts/)
    REPORTS_DIR — override reports output directory (default: reports/)
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import traceback
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yfinance as yf

ET = ZoneInfo("America/New_York")


def _now_et() -> datetime.datetime:
    return datetime.datetime.now(ET)


def _today_str() -> str:
    return _now_et().strftime("%Y-%m-%d")


def _log(msg: str) -> None:
    ts = _now_et().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _log_error(msg: str) -> None:
    ts = _now_et().strftime("%H:%M:%S")
    print(f"[{ts}] ERROR: {msg}", flush=True, file=sys.stderr)


def _get_base_dir() -> Path:
    import os
    env_home = os.getenv("OA2_HOME")
    if env_home:
        return Path(env_home)
    return Path(__file__).parent.parent


def _get_log_dir() -> Path:
    return _get_base_dir() / "logs"


def _get_reports_dir() -> Path:
    import os
    env_reports = os.getenv("REPORTS_DIR")
    if env_reports:
        return Path(env_reports)
    return _get_base_dir() / "reports"


def _load_jsonl(path: Path) -> list[dict]:
    """Load a .jsonl file (one JSON object per line)."""
    if not path.exists():
        return []
    records = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except Exception as e:
        _log_error(f"Failed to load {path}: {e}")
    return records


def _load_json(path: Path) -> dict | list | None:
    """Load a single JSON file."""
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        _log_error(f"Failed to load {path}: {e}")
        return None


def _write_report(path: Path, content: str) -> None:
    """Write report file with proper directory creation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    _log(f"Wrote {path}")


def _get_yesterday_str(date_str: str | None = None) -> str:
    """Return YYYY-MM-DD string for yesterday."""
    if date_str:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ET)
    else:
        dt = _now_et()
    yesterday = (dt - datetime.timedelta(days=1)).date()
    return yesterday.strftime("%Y-%m-%d")


def _fetch_price(ticker: str) -> float | None:
    """Fetch current price via moomoo (preferred) or yfinance (fallback)."""
    try:
        from oa2.dataflows.moomoo_data import fetch_quote
        quote = fetch_quote(ticker)
        if quote and quote.get("last_price"):
            return float(quote["last_price"])
    except Exception as e:
        _log(f"moomoo fetch failed for {ticker}, trying yfinance: {e}")

    try:
        data = yf.Ticker(ticker)
        info = data.fast_info
        if info and hasattr(info, "last_price") and info.last_price:
            return float(info.last_price)
        hist = data.history(period="1d", prepost=True)
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        _log_error(f"yfinance fallback also failed for {ticker}: {e}")
    return None


def _fmt_pct(value: float) -> str:
    """Format percentage with 2 decimals."""
    return f"{value:.1f}%"


def _fmt_price(value: float) -> str:
    """Format price with 2 decimals."""
    return f"${value:.2f}"


def _fmt_dollar(value: float) -> str:
    """Format dollar amount."""
    return f"${value:,.2f}"


def _compute_scenario_price(current_price: float, pct_move: float) -> float:
    """Compute price at given percentage move."""
    return current_price * (1 + pct_move / 100)


# =============================================================================
# PREMARKET REPORT
# =============================================================================

def generate_premarket(date_str: str | None = None, log_dir: Path | None = None, reports_dir: Path | None = None) -> None:
    """Generate premarket report for date (default: yesterday's scan for today's trading)."""
    if log_dir is None:
        log_dir = _get_log_dir()
    if reports_dir is None:
        reports_dir = _get_reports_dir()

    if date_str:
        scan_date = date_str
        report_label = f"for {date_str}"
    else:
        scan_date = _get_yesterday_str()
        report_label = f"(scanning yesterday's signals)"

    log_file = log_dir / f"paper_trade_{scan_date}.jsonl"
    positions_file = log_dir / f"positions_{scan_date}.json"

    _log(f"Generating premarket report {report_label}")

    if not log_file.exists():
        _log_error(f"No scan log found: {log_file}")
        return

    scan_records = _load_jsonl(log_file)
    if not scan_records:
        _log_error(f"No records in {log_file}")
        return

    positions_data = _load_json(positions_file) or []
    open_positions = {p["trade_id"]: p for p in positions_data if isinstance(p, dict)}

    approved = [r for r in scan_records if r.get("status") == "sized_approved"]
    rejected = [r for r in scan_records if r.get("status") == "sized_rejected"]

    _log(f"Found {len(approved)} approved trades, {len(rejected)} rejected")

    lines = []
    lines.append(f"# Premarket Report — {scan_date}")
    lines.append("")
    lines.append(f"Generated: {_now_et().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    lines.append("")
    lines.append(f"Based on yesterday's scan ({scan_date}). Prices are premarket.")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Tickers scanned: {len(scan_records)}")
    lines.append(f"- Approved for trading: {len(approved)}")
    lines.append(f"- Rejected: {len(rejected)}")
    lines.append(f"- Currently open positions: {len(open_positions)}")
    lines.append("")

    if open_positions:
        lines.append("## Open Positions from Yesterday")
        lines.append("")
        for trade_id, pos in open_positions.items():
            ticker = pos.get("ticker", "?")
            direction = pos.get("direction", "?")
            entry_price = pos.get("entry_price", 0)
            current_price = _fetch_price(ticker)
            pnl = pos.get("current_pnl", 0)
            pnl_pct = (pnl / pos.get("max_loss_per_contract", 1)) * 100 if pos.get("max_loss_per_contract") else 0
            lines.append(f"**{ticker}** ({direction}): Entry {_fmt_price(entry_price)}, Current {_fmt_price(current_price) if current_price else 'N/A'}, P&L {_fmt_dollar(pnl)}")
        lines.append("")

    lines.append("## Approved Trades — Entry Setup")
    lines.append("")
    for rec in approved:
        ticker = rec.get("ticker", "?")
        decision = rec.get("decision", {})
        consensus = rec.get("consensus", {})
        sizing = rec.get("sizing", {})
        regime = rec.get("regime", {})
        kelly = sizing.get("kelly", {})

        direction = decision.get("direction", "?")
        p_bull = consensus.get("p_bull", 0)
        score = decision.get("consensus_score", 0)
        contracts = decision.get("contracts", 0)
        max_risk = decision.get("max_dollars_at_risk", 0)

        current_price = _fetch_price(ticker)
        if current_price is None:
            continue

        lines.append(f"### {ticker} — {direction} (p_bull={_fmt_pct(p_bull*100)}, score={_fmt_pct(score*100)})")
        lines.append("")

        vol_state = regime.get("vol_state", "?")
        trend_state = regime.get("trend_state", "?")
        lines.append(f"**Regime:** {vol_state} volatility, {trend_state} trend")
        lines.append("")

        weights = consensus.get("weights", {})
        weight_str = ", ".join(f"{k}={_fmt_pct(v*100)}" for k, v in sorted(weights.items(), key=lambda x: -x[1])[:3])
        lines.append(f"**Top Debaters:** {weight_str}")
        lines.append("")

        lines.append("#### Scenario Analysis")
        lines.append("")
        lines.append("| Scenario | Price | Action |")
        lines.append("|----------|-------|--------|")

        scenarios = [
            ("+2%", 0.02),
            ("+1%", 0.01),
            ("-1%", -0.01),
            ("-2%", -0.02),
        ]
        for label, pct in scenarios:
            price = _compute_scenario_price(current_price, pct * 100)
            if label.startswith("+"):
                action = "Entry → Profit target watch"
            else:
                action = "Hold or reassess"
            lines.append(f"| {label} | {_fmt_price(price)} | {action} |")

        lines.append("")
        lines.append(f"**Max Risk:** {_fmt_dollar(max_risk)} | **Contracts:** {contracts}")
        lines.append("")

        raw_p = consensus.get("p_bull_raw", p_bull)
        if raw_p != p_bull:
            lines.append(f"*Calibrator applied: raw {_fmt_pct(raw_p*100)} → {_fmt_pct(p_bull*100)} (Platt scaling)*")
            lines.append("")

        edge = kelly.get("edge", 0)
        kelly_f = kelly.get("kelly_f", 0)
        lines.append(f"**Kelly Math:** edge={_fmt_pct(edge*100)}, kelly_f={_fmt_pct(kelly_f*100)} → {contracts} contracts")
        lines.append("")

    if rejected:
        lines.append("## Rejected Trades")
        lines.append("")
        for rec in rejected:
            ticker = rec.get("ticker", "?")
            decision = rec.get("decision", {})
            direction = decision.get("direction", "?")
            reason = decision.get("sizing_reject_reason", "Unknown")
            lines.append(f"- **{ticker}** ({direction}): {reason}")
        lines.append("")

    lines.append("## Links")
    lines.append("")
    lines.append(f"[[{scan_date}-postmarket]] (after market close)")
    lines.append("")

    report_path = reports_dir / f"{scan_date}-premarket.md"
    _write_report(report_path, "\n".join(lines))


# =============================================================================
# POSTMARKET REPORT
# =============================================================================

def generate_postmarket(date_str: str | None = None, log_dir: Path | None = None, reports_dir: Path | None = None) -> None:
    """Generate postmarket report for date (default: today)."""
    if log_dir is None:
        log_dir = _get_log_dir()
    if reports_dir is None:
        reports_dir = _get_reports_dir()

    report_date = date_str or _today_str()
    log_file = log_dir / f"paper_trade_{report_date}.jsonl"
    summary_file = log_dir / f"summary_{report_date}.json"
    positions_file = log_dir / f"positions_{report_date}.json"
    exits_file = log_dir / f"exit_alerts_{report_date}.jsonl"

    _log(f"Generating postmarket report for {report_date}")

    if not log_file.exists():
        _log_error(f"No scan log found: {log_file}")
        return

    scan_records = _load_jsonl(log_file)
    summary_data = _load_json(summary_file) or {}
    positions_data = _load_json(positions_file) or []
    exit_alerts = _load_jsonl(exits_file)

    approved_recs = [r for r in scan_records if r.get("status") == "sized_approved"]
    rejected_recs = [r for r in scan_records if r.get("status") == "sized_rejected"]

    lines = []
    lines.append(f"# Postmarket Report — {report_date}")
    lines.append("")
    lines.append(f"Generated: {_now_et().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    lines.append("")

    lines.append("## Day Summary")
    lines.append("")
    lines.append(f"- Scanned: {summary_data.get('tickers_scanned', len(scan_records))} tickers")
    lines.append(f"- Approved: {summary_data.get('approved_count', len(approved_recs))} trades")
    lines.append(f"- Rejected: {summary_data.get('rejected_count', len(rejected_recs))}")
    lines.append(f"- Exit alerts: {summary_data.get('exit_alert_count', len(exit_alerts))}")
    lines.append("")

    if positions_data:
        lines.append("## Trades Entered")
        lines.append("")
        lines.append("| Ticker | Direction | Structure | Entry Price | Contracts | Max Risk |")
        lines.append("|--------|-----------|-----------|-------------|-----------|----------|")
        for pos in positions_data:
            if not isinstance(pos, dict):
                continue
            ticker = pos.get("ticker", "?")
            direction = pos.get("direction", "?")
            structure = pos.get("structure", "?")
            entry_price = pos.get("entry_price", 0)
            contracts = pos.get("contracts", 0)
            max_loss = pos.get("max_loss_per_contract", 0) * contracts
            lines.append(f"| {ticker} | {direction} | {structure} | {_fmt_price(entry_price)} | {contracts} | {_fmt_dollar(max_loss)} |")
        lines.append("")

    if exit_alerts:
        lines.append("## Exit Events")
        lines.append("")
        lines.append("| Time | Ticker | Reason | P&L |")
        lines.append("|------|--------|--------|-----|")
        for alert in exit_alerts:
            ts = alert.get("ts", "?")[:16]
            ticker = alert.get("ticker", "?")
            reason = alert.get("reason", "?")
            pnl = alert.get("current_pnl", 0)
            lines.append(f"| {ts} | {ticker} | {reason} | {_fmt_dollar(pnl)} |")
        lines.append("")

    if rejected_recs:
        lines.append("## Watch List — Almost Traded")
        lines.append("")
        lines.append("These tickers had good signals but were rejected for sizing reasons:")
        lines.append("")
        for rec in rejected_recs:
            ticker = rec.get("ticker", "?")
            decision = rec.get("decision", {})
            direction = decision.get("direction", "?")
            reason = decision.get("sizing_reject_reason", "Unknown")
            p_bull = decision.get("p_bull", 0)
            lines.append(f"- **{ticker}** ({direction}, p_bull={_fmt_pct(p_bull*100)}): {reason}")
        lines.append("")

    lines.append("## Links")
    lines.append("")
    lines.append(f"[[{report_date}-premarket]] (pre-market setup)")
    lines.append("")

    report_path = reports_dir / f"{report_date}-postmarket.md"
    _write_report(report_path, "\n".join(lines))


# =============================================================================
# TRADE DOCUMENTATION
# =============================================================================

def generate_trade_doc(trade_id: str, date_str: str | None = None, log_dir: Path | None = None, reports_dir: Path | None = None) -> None:
    """Generate detailed Obsidian note for a single trade."""
    if log_dir is None:
        log_dir = _get_log_dir()
    if reports_dir is None:
        reports_dir = _get_reports_dir()

    doc_date = date_str or _today_str()
    log_file = log_dir / f"paper_trade_{doc_date}.jsonl"
    positions_file = log_dir / f"positions_{doc_date}.json"

    _log(f"Generating trade doc for {trade_id}")

    positions_data = _load_json(positions_file) or []
    position = None
    for p in positions_data:
        if isinstance(p, dict) and p.get("trade_id") == trade_id:
            position = p
            break

    if not position:
        _log_error(f"Trade {trade_id} not found in {positions_file}")
        return

    scan_records = _load_jsonl(log_file)
    scan_rec = None
    for r in scan_records:
        if r.get("ticker") == position.get("ticker") and r.get("status") == "sized_approved":
            scan_rec = r
            break

    lines = []
    lines.append("---")
    lines.append(f"tags: [trade, {position.get('direction', '?')}, {position.get('ticker', '?')}, {doc_date}]")
    lines.append(f"date: {doc_date}")
    lines.append(f"ticker: {position.get('ticker', '?')}")
    lines.append(f"trade_id: {trade_id}")
    lines.append(f"direction: {position.get('direction', '?')}")
    lines.append("status: open")
    lines.append("---")
    lines.append("")

    ticker = position.get("ticker", "?")
    direction = position.get("direction", "?")
    structure = position.get("structure", "?")

    lines.append(f"# Trade: {ticker} {direction} — {doc_date}")
    lines.append("")

    lines.append("## What We Did")
    lines.append("")
    entry_price = position.get("entry_price", 0)
    contracts = position.get("contracts", 0)
    lines.append(f"At {doc_date} market open, the system entered a {direction} position on {ticker} at {_fmt_price(entry_price)} ({contracts} contracts).")
    lines.append(f"Structure: {structure}")
    lines.append("")

    if scan_rec:
        consensus = scan_rec.get("consensus", {})
        decision = scan_rec.get("decision", {})
        weights = consensus.get("weights", {})
        top_debater = max(weights.items(), key=lambda x: x[1]) if weights else ("?", 0)
        lines.append(f"We entered because {len(consensus.get('weights', {}))} debaters agreed: {top_debater[0].title()} was strongest ({_fmt_pct(top_debater[1]*100)}).")
        lines.append("")

    lines.append("## Why We Did It")
    lines.append("")
    if scan_rec:
        consensus = scan_rec.get("consensus", {})
        regime = scan_rec.get("regime", {})
        decision = scan_rec.get("decision", {})
        sizing = scan_rec.get("sizing", {})

        p_bull = consensus.get("p_bull", 0)
        score = decision.get("consensus_score", 0)
        lines.append(f"- **Direction:** {direction} (consensus score {_fmt_pct(score*100)}, calibrated p_bull {_fmt_pct(p_bull*100)})")

        vol_state = regime.get("vol_state", "?")
        trend_state = regime.get("trend_state", "?")
        lines.append(f"- **Market Regime:** {vol_state} volatility, {trend_state} trend")

        weights = consensus.get("weights", {})
        major_votes = sorted(weights.items(), key=lambda x: -x[1])[:3]
        voted_str = ", ".join(f"{k.title()} ({_fmt_pct(v*100)})" for k, v in major_votes)
        lines.append(f"- **Debaters that voted:** {voted_str}")

        raw_p = consensus.get("p_bull_raw", p_bull)
        if raw_p != p_bull:
            lines.append(f"- **Calibration:** Raw probability {_fmt_pct(raw_p*100)} scaled to {_fmt_pct(p_bull*100)} by Platt scaling")

    lines.append("")

    lines.append("## The Math")
    lines.append("")
    if scan_rec:
        sizing = scan_rec.get("sizing", {})
        kelly = sizing.get("kelly", {})
        kelly_f = kelly.get("kelly_f", 0)
        edge = kelly.get("edge", 0)
        decision = scan_rec.get("decision", {})
        max_risk = decision.get("max_dollars_at_risk", 0)

        lines.append(f"- **Kelly Fraction:** {_fmt_pct(kelly_f*100)} of bankroll")
        lines.append(f"- **Edge (expected return):** {_fmt_pct(edge*100)}")
        lines.append(f"- **Max Risk:** {_fmt_dollar(max_risk)}")

    max_profit = position.get("max_profit_per_contract", 0) * contracts
    max_loss = position.get("max_loss_per_contract", 0) * contracts
    profit_target_pct = position.get("profit_target_pct", 0.5)
    lines.append(f"- **Profit Target:** {_fmt_pct(profit_target_pct*100)} of max profit = {_fmt_dollar(max_profit * profit_target_pct)}")
    lines.append(f"- **Max Loss:** {_fmt_dollar(max_loss)}")
    lines.append("")

    lines.append("## Scenario Analysis")
    lines.append("")
    current_price = position.get("entry_price", 0)
    lines.append("| If underlying moves... | Result |")
    lines.append("|---|---|")
    for pct_move, result in [(2, "Profit target zone"), (1, "In profit"), (-1, "Near break-even"), (-2, "Approaching stop")]:
        price = _compute_scenario_price(current_price, pct_move)
        lines.append(f"| +{pct_move}% to {_fmt_price(price)} | {result} |")
    lines.append("")

    lines.append("## How It Resolved")
    lines.append("")
    lines.append("*[To be filled in at market close]*")
    lines.append("")

    lines.append("## Links")
    lines.append("")
    lines.append(f"[[{doc_date}-premarket]] | [[{doc_date}-postmarket]]")
    lines.append("")

    report_path = reports_dir / "trades" / f"{doc_date}-{ticker}-{trade_id[:4]}.md"
    _write_report(report_path, "\n".join(lines))


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Generate oa2 premarket and postmarket reports",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/report.py --premarket                    # Generate today's premarket (uses yesterday's scan)
  python scripts/report.py --premarket --date 2026-05-18  # Generate premarket for specific date
  python scripts/report.py --postmarket                   # Generate today's postmarket
  python scripts/report.py --trade-doc TRADE_001          # Generate detail page for one trade
        """,
    )

    parser.add_argument("--premarket", action="store_true", help="Generate premarket report")
    parser.add_argument("--postmarket", action="store_true", help="Generate postmarket report")
    parser.add_argument("--trade-doc", type=str, help="Generate trade documentation for TRADE_ID")
    parser.add_argument("--date", type=str, help="Date (YYYY-MM-DD) to generate for")

    args = parser.parse_args()

    try:
        reports_dir = _get_reports_dir()
        log_dir = _get_log_dir()

        if args.premarket:
            generate_premarket(date_str=args.date, log_dir=log_dir, reports_dir=reports_dir)
        elif args.postmarket:
            generate_postmarket(date_str=args.date, log_dir=log_dir, reports_dir=reports_dir)
        elif args.trade_doc:
            generate_trade_doc(args.trade_doc, date_str=args.date, log_dir=log_dir, reports_dir=reports_dir)
        else:
            parser.print_help()

    except Exception as e:
        _log_error(f"Report generation failed: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
