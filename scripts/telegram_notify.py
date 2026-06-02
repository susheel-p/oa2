"""Telegram notifier for oa2 paper trading.

Sends approved-trade alerts to a Telegram chat via the Bot API. Silent no-op
when credentials are missing so paper_trade.py can call it unconditionally.

Setup:
    1. Talk to @BotFather on Telegram, /newbot, save the token.
    2. Message your new bot once, then visit
       https://api.telegram.org/bot<TOKEN>/getUpdates to grab your chat_id.
    3. Add to .env:
         TELEGRAM_BOT_TOKEN=123456:ABC...
         TELEGRAM_CHAT_ID=987654321

Templates:
    Messages use templates from scripts/telegram_templates.yml for consistency.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    import yaml
except ImportError:
    yaml = None

import requests


_API = "https://api.telegram.org/bot{token}/sendMessage"


def _enabled() -> tuple[str, str] | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return None
    return token, chat_id


def send(text: str) -> bool:
    """Send a plain message. Returns True on success, False on any failure."""
    creds = _enabled()
    if creds is None:
        return False
    token, chat_id = creds
    try:
        resp = requests.post(
            _API.format(token=token),
            data={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": "true",
            },
            timeout=5,
        )
        return resp.ok
    except Exception:
        return False


def format_trade(
    ticker: str,
    result: dict,
    fills: list[dict] | None = None,
) -> str:
    """Build a compact summary of an approved trade with meaningful context."""
    decision = result.get("decision") or {}
    sizing = result.get("sizing") or {}
    pick = result.get("structure_pick") or {}

    direction = decision.get("direction", "?")
    p_bull = decision.get("p_bull")
    contracts = sizing.get("contracts", 0)
    max_risk = sizing.get("max_dollars_at_risk", 0)
    entry_premium = result.get("entry_premium")

    # Extract Kelly from sizing.kelly if kelly_fraction not available
    kelly = sizing.get("kelly_fraction")
    if not isinstance(kelly, (int, float)) and isinstance(sizing.get("kelly"), dict):
        kelly = sizing["kelly"].get("kelly_f")

    regime_id = decision.get("regime_id", "?")
    structure = pick.get("structure", "?")
    short_k = pick.get("short_strike")
    long_k = pick.get("long_strike")
    expiry = pick.get("expiry", "?")
    recommended_expiry = decision.get("recommended_expiry")

    # Calculate DTE if we have expiry date
    dte = "?"
    if recommended_expiry:
        try:
            from datetime import datetime
            exp_date = datetime.strptime(recommended_expiry, "%Y-%m-%d")
            today = datetime.now()
            dte = (exp_date - today).days
        except:
            dte = "?"

    lines = [
        f"✅ Trade Approved: {ticker} {direction}",
        "",
    ]

    # Build structure line with safe formatting
    if short_k is not None and long_k is not None:
        lines.append(f"Structure: {structure} {short_k}/{long_k}")
    else:
        lines.append(f"Structure: {structure}")

    if isinstance(dte, int):
        lines.append(f"Expiry: {dte} DTE ({recommended_expiry})")
    else:
        lines.append(f"Expiry: {recommended_expiry}")

    lines.append(f"Size: {contracts} contract(s)")

    # Premium and risk
    if isinstance(entry_premium, (int, float)) and entry_premium > 0:
        total_premium = entry_premium * 100 * contracts
        lines.append(f"Entry Premium: ${total_premium:,.2f}")

    if isinstance(max_risk, (int, float)) and max_risk > 0:
        lines.append(f"Max Risk: ${max_risk:,.2f}")

    if isinstance(kelly, (int, float)):
        lines.append(f"Kelly: {kelly:.1%}")

    # Conviction and context
    if isinstance(p_bull, (int, float)):
        lines.append(f"Conviction: p_bull={p_bull:.1%}")

    if regime_id != "?":
        lines.append(f"Regime: R{regime_id}")

    if fills:
        ok = sum(1 for f in fills if not f.get("error"))
        err = len(fills) - ok
        if err > 0:
            lines.append(f"Broker: {ok} filled, {err} errored")
        else:
            lines.append(f"Broker: {ok} filled ✅")

    return "\n".join(lines)


def notify_trade(ticker: str, result: dict, fills: list[dict] | None = None) -> bool:
    return send(format_trade(ticker, result, fills))


def format_exit(
    ticker: str,
    alert: dict,
) -> str:
    """Build a compact summary of an exit alert."""
    reason = alert.get("reason", "?")
    urgency = alert.get("urgency", "?").upper() if alert.get("urgency") else "?"
    pnl = alert.get("current_pnl", 0)
    trade_id = alert.get("trade_id", "?")

    lines = [
        f"Position EXIT: {ticker} [{trade_id}]",
        f"Reason: {reason}  Urgency: {urgency}",
        f"P&L: ${pnl:+.2f}",
    ]
    return "\n".join(lines)


def notify_exit(ticker: str, alert: dict) -> bool:
    return send(format_exit(ticker, alert))


def notify_scan_summary(summary: dict) -> bool:
    """Send daily market scan summary after full-scan completes (9:45 AM)."""
    approved_count = summary.get('approved_count', 0)
    rejected_count = summary.get('rejected_count', 0)
    account_size = summary.get('account_size', 0)
    tickers_scanned = summary.get('tickers_scanned', 0)

    # Count bullish/bearish from ticker breakdown if available
    bullish_count = 0
    bearish_count = 0
    neutral_count = 0
    ticker_breakdown = summary.get('ticker_breakdown', {})
    bullish_count = ticker_breakdown.get('bullish', 0)
    bearish_count = ticker_breakdown.get('bearish', 0)
    neutral_count = ticker_breakdown.get('neutral', 0)

    lines = [
        f"📊 Market Scan — {summary.get('date', '?')}",
        f"Scanned: {tickers_scanned} | Bull: {bullish_count} | Bear: {bearish_count} | Neutral: {neutral_count}",
        "",
        f"Approved Trades: {approved_count}",
        f"Rejected: {rejected_count}",
        "",
        f"Account Size: ${account_size:,.0f}",
    ]

    # Add top approved trades if available
    approved_trades = summary.get('approved_trades', [])
    if approved_trades:
        lines.append("\nTop Approved:")
        for trade in approved_trades[:3]:
            ticker = trade.get('ticker', '?')
            direction = trade.get('direction', '?')
            structure = trade.get('structure', '?')
            dte = trade.get('dte', '?')
            contracts = trade.get('contracts', 0)
            lines.append(f"  • {ticker}: {structure} {dte} DTE, {contracts} contracts")

    text = "\n".join(lines)
    return send(text)


def notify_premarket_scan(summary: dict) -> bool:
    """Send premarket market scan summary at 8:00 AM."""
    bullish_count = summary.get('bullish_count', 0)
    neutral_count = summary.get('neutral_count', 0)
    scanned_count = summary.get('tickers_scanned', 0)
    regime = summary.get('regime_summary', 'Unknown')
    vix = summary.get('vix_level', '?')
    movers = summary.get('premarket_movers', '')

    lines = [
        f"🌅 Premarket Market Scan — {summary.get('date', '?')}",
        f"Scanned: {scanned_count} | Bull: {bullish_count} | Neutral: {neutral_count}",
        "",
        f"Regime: {regime}",
        f"VIX: {vix}",
    ]

    if movers:
        lines.append(f"Premarket Movers: {movers}")

    lines.append("\nStatus: Ready for 9:00 AM full-scan")

    text = "\n".join(lines)
    return send(text)


def notify_summary(summary: dict) -> bool:
    """Legacy: send trading run summary (4:15 PM daily summary)."""
    approved_count = summary.get('approved_count', 0)
    rejected_count = summary.get('rejected_count', 0)
    error_count = summary.get('error_count', 0)
    exit_alert_count = summary.get('exit_alert_count', 0)
    account_size = summary.get('account_size', 0)

    lines = [
        "📈 Daily Summary",
        "",
        f"Trades Executed:",
        f"  Approved: {approved_count} | Rejected: {rejected_count} | Errors: {error_count}",
        f"  Exit alerts: {exit_alert_count}",
        "",
        f"Account Size: ${account_size:,.0f}",
    ]

    # Add book state summary
    book_state = summary.get("book_state", {})
    if book_state:
        position_count = book_state.get('position_count', 0)
        net_delta = book_state.get('net_delta', 0)
        net_theta = book_state.get('net_theta', 0)
        lines.append("")
        lines.append(f"Book State:")
        lines.append(f"  Open Positions: {position_count}")
        lines.append(f"  Net Delta: {net_delta:+.2f}")
        lines.append(f"  Net Theta: {net_theta:+.2f}/day")

    # Add open positions summary
    positions = summary.get("open_positions", [])
    if positions:
        lines.append("\nOpen Positions:")
        for pos in positions:
            ticker = pos.get("ticker", "?")
            contracts = pos.get("contracts", 0)
            pnl = pos.get("current_pnl", 0)
            dte = pos.get("current_dte", 0)
            structure = pos.get("structure", "?")
            pnl_pct = pos.get("current_pnl_pct", 0)
            lines.append(f"  • {ticker}: {contracts}c, P&L ${pnl:+.2f} ({pnl_pct:+.1%}), {dte} DTE ({structure})")
    else:
        lines.append("\nOpen Positions: None")

    text = "\n".join(lines)
    return send(text)


def notify_market_summary(market_summary: str) -> bool:
    """Send 8am daily market summary with economic calendar, sentiment, technicals, news, volatility."""
    return send(market_summary)


def notify_system_health(
    daemon_status: str,
    signals_generated: int,
    heartbeat_age_seconds: int | None = None,
    error_count: int = 0,
    position_count: int = 0,
) -> bool:
    """Send 8am system health report with detailed status."""
    from datetime import datetime
    date_str = datetime.now().strftime("%Y-%m-%d")

    # Determine status emoji and health indicator
    if daemon_status.lower() == "running" and (heartbeat_age_seconds is None or heartbeat_age_seconds < 300):
        status_emoji = "✅"
        heartbeat_status = "HEALTHY"
    elif heartbeat_age_seconds and heartbeat_age_seconds > 900:
        status_emoji = "❌"
        heartbeat_status = "STALE"
    else:
        status_emoji = "⚠️"
        heartbeat_status = "AGING"

    lines = [
        f"⚙️ System Health Report — {date_str}",
        "",
        f"Daemon: {daemon_status} {status_emoji}",
        f"Heartbeat: {heartbeat_status}",
    ]

    if heartbeat_age_seconds is not None:
        lines.append(f"  (updated {heartbeat_age_seconds}s ago)")

    lines.append(f"Signals Generated: {signals_generated}")
    lines.append(f"Recent Errors: {error_count}")
    lines.append(f"Positions Open: {position_count}")

    if error_count == 0 and daemon_status.lower() == "running" and heartbeat_status == "HEALTHY":
        lines.append("\n✅ All systems nominal. Ready for trading.")
    elif heartbeat_age_seconds and heartbeat_age_seconds > 600:
        lines.append(f"\n⚠️ Heartbeat aging - daemon may be hanging")
        lines.append("Check market_monitor logs for blocked operations")

    text = "\n".join(lines)
    return send(text)


def notify_system_issues(
    is_stale: bool,
    error_count: int,
    last_activity: str | None = None,
    heartbeat_age_seconds: int | None = None,
    errors_detail: list[str] | None = None,
) -> bool:
    """Send critical noon watchdog alert if there are system issues."""
    if not is_stale and error_count == 0:
        return True  # No issues, don't send alert

    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    alert_lines = [
        "🚨 CRITICAL: System Issues Detected — " + now_str,
        "",
        "Issues:",
    ]

    if is_stale and heartbeat_age_seconds:
        alert_lines.append(f"  ❌ Daemon stale (no heartbeat for {heartbeat_age_seconds}s)")

    if error_count > 0:
        alert_lines.append(f"  ⚠️ {error_count} pipeline error(s) detected")
        if errors_detail:
            for err in errors_detail[:3]:  # Show first 3 errors
                alert_lines.append(f"     - {err}")

    if last_activity:
        alert_lines.append(f"\nLast Known Activity: {last_activity}")

    alert_lines.extend([
        "",
        "Immediate Actions:",
        "1. Restart daemon: docker-compose restart tradingbot-daemon",
        "2. Check logs: tail -f logs/daemon.log",
        "3. Verify broker connectivity",
        "",
        "⚠️ Manual Entry/Exit Required: YES (positions may be unmonitored)",
    ])

    return send("\n".join(alert_lines))


def notify_critical_error(
    component: str,
    error_msg: str,
    affected: str | list[str] | None = None,
    severity: str = "CRITICAL",
    recovery_steps: list[str] | None = None,
) -> bool:
    """Send immediate critical error alert."""
    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"⚠️ {severity}: {component} Error — {now_str}",
        "",
        f"Error: {error_msg}",
    ]

    if affected:
        lines.append("")
        lines.append("Impact:")
        if isinstance(affected, list):
            for item in affected:
                lines.append(f"  • {item}")
        else:
            lines.append(f"  • {affected}")

    if recovery_steps:
        lines.append("")
        lines.append("Recovery Steps:")
        for i, step in enumerate(recovery_steps, 1):
            lines.append(f"{i}. {step}")
    else:
        lines.append("")
        lines.append("Recovery Steps:")
        lines.append("1. Check logs for root cause")
        lines.append("2. Verify component connectivity")
        lines.append("3. Restart daemon if necessary")

    lines.append("")
    lines.append("Urgency: IMMEDIATE — Manual review required")

    return send("\n".join(lines))


if __name__ == "__main__":
    import sys
    msg = " ".join(sys.argv[1:]) or "Test message from trading system"
    print("sent" if send(msg) else "FAILED (check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
