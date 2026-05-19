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
"""

from __future__ import annotations

import json
import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

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
    """Build a compact Markdown summary of an approved trade."""
    decision = result.get("decision") or {}
    sizing = result.get("sizing") or {}
    pick = result.get("structure_pick") or {}

    p_bull = decision.get("p_bull")
    direction = decision.get("direction", "?")
    contracts = sizing.get("contracts", 0)
    kelly = sizing.get("kelly_fraction")
    structure = pick.get("structure", "?")
    short_k = pick.get("short_strike")
    long_k = pick.get("long_strike")
    expiry = pick.get("expiry", "?")

    lines = [
        f"oa2 APPROVED -- {ticker} {direction}",
        f"Structure: {structure}  {short_k}/{long_k}  exp {expiry}",
        f"Size: {contracts} contract(s)"
        + (f"  Kelly={kelly:.2%}" if isinstance(kelly, (int, float)) else ""),
    ]
    if isinstance(p_bull, (int, float)):
        lines.append(f"p_bull = {p_bull:.3f}")
    if fills:
        ok = sum(1 for f in fills if not f.get("error"))
        err = len(fills) - ok
        lines.append(f"Broker: {ok} filled, {err} errored")
    return "\n".join(lines)


def notify_trade(ticker: str, result: dict, fills: list[dict] | None = None) -> bool:
    return send(format_trade(ticker, result, fills))


def notify_summary(summary: dict) -> bool:
    text = (
        "oa2 run complete\n"
        f"Approved: {summary.get('approved_count', 0)}\n"
        f"Rejected: {summary.get('rejected_count', 0)}\n"
        f"Errors:   {summary.get('error_count', 0)}\n"
        f"Exit alerts: {summary.get('exit_alert_count', 0)}"
    )
    return send(text)


if __name__ == "__main__":
    import sys
    msg = " ".join(sys.argv[1:]) or "oa2 telegram_notify test ping"
    print("sent" if send(msg) else "FAILED (check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
