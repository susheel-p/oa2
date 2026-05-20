"""oa2 premarket and postmarket report generator.

Generates human-readable Obsidian-compatible markdown reports for daily trading analysis.

Usage:
    python scripts/report.py --premarket [--date YYYY-MM-DD]       # 8:30 AM before market open
    python scripts/report.py --postmarket [--date YYYY-MM-DD]      # 4:15 PM after market close
    python scripts/report.py --trade-doc TRADE_ID [--date YYYY-MM-DD]

Reports are written to reports/ directory with Obsidian [[wikilinks]].

Environment:
    TRADINGBOT_HOME — override base directory (default: parent of scripts/)
    REPORTS_DIR — override reports output directory (default: reports/)
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
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
    env_home = os.getenv("TRADINGBOT_HOME")
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


def _load_executions(log_dir: Path, date_str: str) -> tuple[list[dict], list[dict]]:
    """Load executions_{date}.jsonl, split into (entry_records, exit_records)."""
    all_recs = _load_jsonl(log_dir / f"executions_{date_str}.jsonl")
    return (
        [r for r in all_recs if r.get("event") == "ENTRY"],
        [r for r in all_recs if r.get("event") == "EXIT"],
    )


def _get_day_reports_dir(date_str: str, reports_dir: Path) -> Path:
    """Get the reports directory for a specific date."""
    return reports_dir / date_str


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
        from tradingbot.dataflows.moomoo_data import fetch_quote
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

    # Load executions to cross-reference with approvals
    entry_execs, _ = _load_executions(log_dir, scan_date)
    entry_exec_tickers = {e.get("ticker"): e for e in entry_execs}

    # Execution status cross-reference
    lines.append("## Execution Status of Yesterday's Approvals")
    lines.append("")
    if entry_execs or approved:
        lines.append("| Ticker | Approved | Executed | Fill Status |")
        lines.append("|--------|----------|----------|-------------|")
        for rec in approved:
            ticker = rec.get("ticker", "?")
            exec_rec = entry_exec_tickers.get(ticker)
            if exec_rec:
                legs = exec_rec.get("legs", [])
                leg_status = ", ".join(
                    f"leg{l.get('leg')}={l.get('status', '?')}"
                    for l in legs if not l.get("error")
                ) or "error"
                lines.append(f"| {ticker} | YES | YES | {leg_status} |")
            else:
                lines.append(f"| {ticker} | YES | NO | (not submitted) |")
        lines.append("")
    else:
        lines.append("No execution data available.")
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
    lines.append("[[postmarket]] (after market close)")
    lines.append("")

    report_date = date_str if date_str else _today_str()
    day_dir = _get_day_reports_dir(report_date, reports_dir)
    report_path = day_dir / "premarket.md"
    _write_report(report_path, "\n".join(lines))


# =============================================================================
# POSTMARKET REPORT
# =============================================================================

def generate_postmarket(date_str: str | None = None, log_dir: Path | None = None, reports_dir: Path | None = None) -> None:
    """Generate postmarket report for date (default: today) with 4-section audit trail."""
    if log_dir is None:
        log_dir = _get_log_dir()
    if reports_dir is None:
        reports_dir = _get_reports_dir()

    report_date = date_str or _today_str()
    log_file = log_dir / f"paper_trade_{report_date}.jsonl"
    summary_file = log_dir / f"summary_{report_date}.json"
    positions_file = log_dir / f"positions_{report_date}.json"

    _log(f"Generating postmarket report for {report_date}")

    if not log_file.exists():
        _log_error(f"No scan log found: {log_file}")
        return

    scan_records = _load_jsonl(log_file)
    summary_data = _load_json(summary_file) or {}
    positions_data = _load_json(positions_file) or []
    entry_execs, exit_execs = _load_executions(log_dir, report_date)

    approved_recs = [r for r in scan_records if r.get("status") == "sized_approved"]
    rejected_recs = [r for r in scan_records if r.get("status") == "sized_rejected"]

    lines = []
    lines.append(f"# Postmarket Report — {report_date}")
    lines.append("")
    lines.append(f"Generated: {_now_et().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    lines.append("")

    # Section 1: Signal Scan Summary
    lines.append("## Signal Scan")
    lines.append("")
    lines.append(f"- Tickers scanned: {summary_data.get('tickers_scanned', len(scan_records))}")
    lines.append(f"- Approved for trading: {summary_data.get('approved_count', len(approved_recs))}")
    lines.append(f"- Rejected: {summary_data.get('rejected_count', len(rejected_recs))}")
    lines.append("")

    # Section 2: Executed Trades
    lines.append("## Executed Trades")
    lines.append("")
    if entry_execs:
        lines.append("| Time | Ticker | Direction | Structure | Contracts | Leg Status | Trigger |")
        lines.append("|------|--------|-----------|-----------|-----------|-----------|---------|")
        for exec_rec in entry_execs:
            ts = exec_rec.get("ts", "?")[:16]
            ticker = exec_rec.get("ticker", "?")
            direction = exec_rec.get("direction", "?")
            structure = exec_rec.get("structure", "?")
            contracts = exec_rec.get("contracts", 0)
            legs = exec_rec.get("legs", [])
            leg_summary = ", ".join(
                f"leg{l.get('leg')}={l.get('status', '?')}@{l.get('avg_fill_price', 0):.2f}"
                for l in legs if not l.get("error")
            ) or "error"
            trigger = exec_rec.get("trigger", "?")
            lines.append(f"| {ts} | {ticker} | {direction} | {structure} | {contracts} | {leg_summary} | {trigger} |")
        lines.append("")
    elif positions_data:
        lines.append("*No execution log found; showing position snapshot.*")
        lines.append("")
        lines.append("| Ticker | Direction | Structure | Contracts |")
        lines.append("|--------|-----------|-----------|-----------|")
        for pos in positions_data:
            if not isinstance(pos, dict):
                continue
            lines.append(f"| {pos.get('ticker', '?')} | {pos.get('direction', '?')} | {pos.get('structure', '?')} | {pos.get('contracts', 0)} |")
        lines.append("")
    else:
        lines.append("No executed trades today.")
        lines.append("")

    # Section 3: Exit Events
    lines.append("## Exit Events")
    lines.append("")
    if exit_execs:
        lines.append("| Time | Ticker | Reason | Urgency | P&L |")
        lines.append("|------|--------|--------|---------|-----|")
        for exit_rec in exit_execs:
            ts = exit_rec.get("ts", "?")[:16]
            ticker = exit_rec.get("ticker", "?")
            reason = exit_rec.get("exit_reason", "?")
            urgency = exit_rec.get("exit_urgency", "?")
            pnl = exit_rec.get("current_pnl", 0)
            lines.append(f"| {ts} | {ticker} | {reason} | {urgency} | {_fmt_dollar(pnl)} |")
        lines.append("")
    else:
        lines.append("No exit events today.")
        lines.append("")

    # Section 4: Live Broker Positions
    lines.append("## Live Broker Positions")
    lines.append("")
    try:
        from tradingbot.dataflows.moomoo_data import fetch_account_positions
        live_positions = fetch_account_positions()
    except Exception:
        live_positions = []

    if live_positions:
        lines.append("| Symbol | Qty | Cost | Market Value | P&L | Today P&L |")
        lines.append("|--------|-----|------|--------------|-----|-----------|")
        for pos in live_positions:
            code = pos.get("code", "?")
            qty = pos.get("qty", 0)
            cost = pos.get("cost_price", 0)
            market_val = pos.get("market_val", 0)
            pl_val = pos.get("pl_val", 0)
            today_pl = pos.get("today_pl_val", 0)
            lines.append(f"| {code} | {qty} | {_fmt_price(cost)} | {_fmt_dollar(market_val)} | {_fmt_dollar(pl_val)} | {_fmt_dollar(today_pl)} |")
        lines.append("")
    else:
        lines.append("No open positions in broker account (or OpenD not reachable).")
        lines.append("")

    # Watch list
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
    lines.append("[[premarket]] (pre-market setup)")
    lines.append("")

    day_dir = _get_day_reports_dir(report_date, reports_dir)
    report_path = day_dir / "postmarket.md"
    _write_report(report_path, "\n".join(lines))

    _write_daily_summary_html(
        day_dir=day_dir,
        report_date=report_date,
        scan_records=scan_records,
        summary_data=summary_data,
        positions_data=positions_data,
        exit_alerts=[],  # Updated to use executions instead, but keep for compat
        approved_recs=approved_recs,
        rejected_recs=rejected_recs,
    )


def _write_daily_summary_html(
    day_dir: Path,
    report_date: str,
    scan_records: list,
    summary_data: dict,
    positions_data: list,
    exit_alerts: list,
    approved_recs: list,
    rejected_recs: list,
) -> None:
    """Render clean executive summary with KPIs, trades table, and links to full reports."""
    import html as _html

    n_scanned = summary_data.get("tickers_scanned", len(scan_records))
    n_approved = summary_data.get("approved_count", len(approved_recs))
    n_rejected = summary_data.get("rejected_count", len(rejected_recs))
    n_exits = summary_data.get("exit_alert_count", len(exit_alerts))

    # Load executions for executed trades table
    log_dir = _get_log_dir()
    entry_execs, exit_execs = _load_executions(log_dir, report_date)

    open_positions = [p for p in positions_data if isinstance(p, dict)]

    html_parts: list[str] = []
    html_parts.append("<!doctype html><html><head><meta charset='utf-8'>")
    html_parts.append(f"<title>Daily Summary — {report_date}</title>")
    html_parts.append(
        "<style>"
        "body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
        "max-width:900px;margin:2rem auto;padding:0 1rem;color:#222;line-height:1.6}"
        "h1{border-bottom:3px solid #333;padding-bottom:.5rem;margin-bottom:1.5rem}"
        "h2{margin-top:2rem;margin-bottom:1rem;color:#1a4480;font-size:1.1rem;border-left:4px solid #1a4480;padding-left:.8rem}"
        ".kpis{display:flex;gap:1rem;flex-wrap:wrap;margin:1.5rem 0;margin-bottom:2.5rem}"
        ".kpi{background:#eef3fb;padding:.8rem 1.2rem;border-radius:6px;text-align:center;min-width:120px}"
        ".kpi b{display:block;font-size:1.6rem;color:#1a4480;margin-bottom:.3rem}"
        ".kpi span{font-size:.85rem;color:#666}"
        "table{width:100%;border-collapse:collapse;margin:1rem 0;font-size:.95rem}"
        "th,td{padding:.6rem;text-align:left;border-bottom:1px solid #ddd}"
        "th{background:#f0f0f0;font-weight:600;color:#333}"
        "tr:hover{background:#f9f9f9}"
        ".summary-text{margin:1rem 0;line-height:1.7;color:#444}"
        ".no-data{color:#999;font-style:italic;padding:1rem}"
        ".links{margin-top:2.5rem;padding-top:1.5rem;border-top:1px solid #ddd;font-size:.95rem}"
        ".links a{color:#1a4480;text-decoration:none;margin-right:1.5rem;font-weight:500}"
        ".links a:hover{text-decoration:underline}"
        "</style></head><body>"
    )

    html_parts.append(f"<h1>Trading Summary — {_html.escape(report_date)}</h1>")
    html_parts.append(
        f"<p style='color:#666;margin-bottom:1.5rem'>"
        f"<em>Generated {_html.escape(_now_et().strftime('%Y-%m-%d %H:%M:%S %Z'))}</em></p>"
    )

    # KPIs
    html_parts.append("<div class='kpis'>")
    for label, val in [
        ("Scanned", n_scanned),
        ("Approved", n_approved),
        ("Rejected", n_rejected),
        ("Executed", len(entry_execs)),
        ("Open", len(open_positions)),
    ]:
        html_parts.append(f"<div class='kpi'><b>{val}</b><span>{_html.escape(label)}</span></div>")
    html_parts.append("</div>")

    # Premarket Plan
    html_parts.append("<h2>📋 Premarket Plan</h2>")
    html_parts.append(f"<div class='summary-text'>Identified {n_approved} trades ready to execute. {n_rejected} were rejected by sizing gates.</div>")

    # Executed Trades
    html_parts.append("<h2>✅ Executed Trades</h2>")
    if entry_execs:
        html_parts.append(
            "<table>"
            "<tr><th>Ticker</th><th>Direction</th><th>Structure</th><th>Contracts</th><th>Entry Price</th><th>Status</th></tr>"
        )
        for exec_rec in entry_execs:
            ticker = exec_rec.get("ticker", "?")
            direction = exec_rec.get("direction", "?")
            structure = exec_rec.get("structure", "?")
            contracts = exec_rec.get("contracts", 0)
            legs = exec_rec.get("legs", [])
            avg_fill = next((l.get("avg_fill_price", 0) for l in legs if not l.get("error")), 0)
            leg_status = ", ".join(
                f"leg{l.get('leg')}={l.get('status', '?')}"
                for l in legs if not l.get("error")
            ) or "error"
            html_parts.append(
                f"<tr><td><strong>{_html.escape(ticker)}</strong></td><td>{_html.escape(direction)}</td>"
                f"<td>{_html.escape(structure)}</td><td>{contracts}</td>"
                f"<td>{_fmt_price(avg_fill)}</td><td>{_html.escape(leg_status)}</td></tr>"
            )
        html_parts.append("</table>")
    else:
        html_parts.append("<div class='no-data'>No trades executed today.</div>")

    # Open Positions
    html_parts.append("<h2>📊 Open Positions</h2>")
    if open_positions:
        html_parts.append(
            "<table>"
            "<tr><th>Ticker</th><th>Direction</th><th>Contracts</th><th>Entry Price</th><th>Current P&L</th></tr>"
        )
        for pos in open_positions:
            ticker = pos.get("ticker", "?")
            direction = pos.get("direction", "?")
            contracts = pos.get("contracts", 0)
            entry_price = pos.get("entry_price", 0)
            pnl = pos.get("current_pnl", 0)
            html_parts.append(
                f"<tr><td><strong>{_html.escape(ticker)}</strong></td><td>{_html.escape(direction)}</td>"
                f"<td>{contracts}</td><td>{_fmt_price(entry_price)}</td><td><strong>{_fmt_dollar(pnl)}</strong></td></tr>"
            )
        html_parts.append("</table>")
    else:
        html_parts.append("<div class='no-data'>No open positions.</div>")

    # Postmarket Summary
    html_parts.append("<h2>📈 Postmarket Summary</h2>")
    summary_lines = []
    if entry_execs:
        summary_lines.append(f"{len(entry_execs)} trade(s) executed")
    if open_positions:
        summary_lines.append(f"{len(open_positions)} position(s) open")
    if n_exits:
        summary_lines.append(f"{n_exits} exit alert(s) fired")

    if summary_lines:
        summary = " · ".join(summary_lines)
        html_parts.append(f"<div class='summary-text'>{summary}.</div>")
    else:
        html_parts.append("<div class='summary-text'>No trading activity today.</div>")

    # Top rejection reasons (brief)
    if rejected_recs:
        top_reasons: dict[str, int] = {}
        for r in rejected_recs:
            reason = (r.get("decision") or {}).get("sizing_reject_reason", "Unknown")
            top_reasons[reason] = top_reasons.get(reason, 0) + 1
        top = sorted(top_reasons.items(), key=lambda kv: -kv[1])[:2]
        if top:
            reason_str = "; ".join(f"{r} ({n})" for r, n in top)
            html_parts.append(
                f"<div class='summary-text' style='color:#666;font-size:.95rem'>"
                f"<strong>Why {n_rejected} were rejected:</strong> {reason_str}</div>"
            )

    # Links to full reports
    html_parts.append("<div class='links'>")
    html_parts.append("<strong>📎 Full Reports</strong><br><br>")
    html_parts.append("<a href='premarket.md'>Premarket Setup (detailed plan)</a><br>")
    html_parts.append("<a href='postmarket.md'>Postmarket Detail (full breakdown)</a>")
    html_parts.append("</div>")

    html_parts.append("</body></html>")

    out_path = day_dir / "summary.html"
    out_path.write_text("".join(html_parts), encoding="utf-8")
    _log(f"Wrote {out_path}")


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

    # Broker fills (entry)
    entry_execs, exit_execs = _load_executions(log_dir, doc_date)
    entry_rec = next((e for e in entry_execs if e.get("trade_id") == trade_id), None)
    if entry_rec:
        lines.append("## Broker Fills (Entry)")
        lines.append("")
        legs = entry_rec.get("legs", [])
        if legs:
            lines.append("| Leg | Side | Strike | Right | Qty | Status | Fill Price | Fill Time |")
            lines.append("|-----|------|--------|-------|-----|--------|-----------|-----------|")
            for leg in legs:
                if not leg.get("error"):
                    leg_num = leg.get("leg", "?")
                    side = "BUY" if leg.get("side", 0) > 0 else "SELL"
                    strike = leg.get("strike", "?")
                    right = leg.get("right", "?")
                    qty = leg.get("qty", 0)
                    status = leg.get("status", "?")
                    fill_price = leg.get("avg_fill_price", 0)
                    fill_time = leg.get("fill_time", "N/A") or "N/A"
                    lines.append(f"| {leg_num} | {side} | {strike} | {right} | {qty} | {status} | {_fmt_price(fill_price)} | {fill_time} |")
            lines.append("")

    # Broker fills (exit) if exists
    exit_rec = next((e for e in exit_execs if e.get("trade_id") == trade_id), None)
    if exit_rec:
        lines.append("## Broker Fills (Exit)")
        lines.append("")
        lines.append(f"Closed: {exit_rec.get('exit_reason', '?')} (Urgency: {exit_rec.get('exit_urgency', '?')})")
        lines.append(f"Realized P&L: {_fmt_dollar(exit_rec.get('current_pnl', 0))}")
        lines.append("")
        legs = exit_rec.get("legs", [])
        if legs:
            lines.append("| Leg | Side | Strike | Right | Qty | Status | Fill Price | Fill Time |")
            lines.append("|-----|------|--------|-------|-----|--------|-----------|-----------|")
            for leg in legs:
                if not leg.get("error"):
                    leg_num = leg.get("leg", "?")
                    side = "BUY" if leg.get("side", 0) > 0 else "SELL"
                    strike = leg.get("strike", "?")
                    right = leg.get("right", "?")
                    qty = leg.get("qty", 0)
                    status = leg.get("status", "?")
                    fill_price = leg.get("avg_fill_price", 0)
                    fill_time = leg.get("fill_time", "N/A") or "N/A"
                    lines.append(f"| {leg_num} | {side} | {strike} | {right} | {qty} | {status} | {_fmt_price(fill_price)} | {fill_time} |")
            lines.append("")

    lines.append("## How It Resolved")
    lines.append("")
    lines.append("*[To be filled in at market close]*")
    lines.append("")

    lines.append("## Links")
    lines.append("")
    lines.append("[[../premarket]] | [[../postmarket]]")
    lines.append("")

    day_dir = _get_day_reports_dir(doc_date, reports_dir)
    report_path = day_dir / "trades" / f"{ticker}-{trade_id[:4]}.md"
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
    try:
        main()
    finally:
        try:
            from tradingbot.dataflows.moomoo_data import close_quote_context
            close_quote_context()
        except Exception:
            pass
        # Moomoo SDK leaves non-daemon background threads alive; force exit.
        os._exit(0)
