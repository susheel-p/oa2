"""Diagnose why moomoo returns no options chain data for specific tickers.

Run inside the container:
    python scripts/diagnose_options_chain.py [ticker ...] [--expiry YYYY-MM-DD]

Default tickers: IWM NVDA TSLA AAPL AMZN AMD XLK XLV XLI SLV TLT
Default expiry:  nearest Friday

Each step is logged so you can see exactly where the chain fetch breaks down:
  1. get_option_chain() — does moomoo return any rows?
  2. code extraction     — do the rows have valid "code" values?
  3. get_market_snapshot() — do snapshots come back for those codes?
  4. field population   — are bid/ask/IV/delta/OI non-zero?
  5. has_real_data check — does at least one contract pass?
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta

# ── Logging — show DEBUG so every [chain:X] line is visible ──────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("diagnose_options_chain")

# ── Import moomoo directly so we can inspect raw responses ───────────────────
try:
    import moomoo as ft
    from moomoo import OpenQuoteContext, RET_OK
except ImportError:
    logger.error("moomoo SDK not installed — cannot run diagnostics")
    sys.exit(1)

import os


def _next_friday(from_date: date | None = None) -> str:
    d = from_date or date.today()
    days_ahead = 4 - d.weekday()  # Friday=4
    if days_ahead <= 0:
        days_ahead += 7
    return (d + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def get_ctx() -> OpenQuoteContext:
    host = os.getenv("MOOMOO_OPEND_HOST", os.getenv("MOOMOO_HOST", "127.0.0.1"))
    port = int(os.getenv("MOOMOO_OPEND_PORT", os.getenv("MOOMOO_PORT", "11111")))
    logger.info("Connecting to moomoo OpenD at %s:%d", host, port)
    ctx = OpenQuoteContext(host=host, port=port)
    return ctx


def diagnose_ticker(ctx: OpenQuoteContext, ticker: str, expiry: str) -> None:
    code = f"US.{ticker.upper()}"
    logger.info("=" * 60)
    logger.info("TICKER: %s  EXPIRY: %s", ticker, expiry)
    logger.info("=" * 60)

    # ── Step 1: get_option_chain ──────────────────────────────────────────────
    ret, data = ctx.get_option_chain(
        code,
        index_option_type=ft.IndexOptionType.NORMAL,
        start=expiry,
        end=expiry,
        option_type=ft.OptionType.ALL,
    )
    logger.info("Step 1 get_option_chain: ret=%s", ret)

    if ret != RET_OK:
        logger.error("  FAIL: get_option_chain returned error ret=%s for %s expiry=%s", ret, ticker, expiry)
        return

    if data is None or data.empty:
        logger.error("  FAIL: get_option_chain returned empty dataframe for %s expiry=%s", ticker, expiry)
        return

    logger.info("  OK: %d rows, columns=%s", len(data), list(data.columns))
    logger.info("  Sample rows:\n%s", data.head(3).to_string())

    # ── Step 2: code extraction ───────────────────────────────────────────────
    meta: dict[str, dict] = {}
    for _, row in data.iterrows():
        c = str(row.get("code", ""))
        if c:
            meta[c] = {
                "strike": float(row.get("strike_price", 0)),
                "option_type": str(row.get("option_type", "")).upper(),
            }

    logger.info("Step 2 code extraction: %d codes extracted", len(meta))
    if not meta:
        logger.error("  FAIL: no codes in 'code' column. Check column names above.")
        return

    sample_codes = list(meta.keys())[:5]
    logger.info("  Sample codes: %s", sample_codes)

    # ── Step 3: get_market_snapshot ───────────────────────────────────────────
    all_codes = list(meta.keys())
    snap_rows: dict[str, dict] = {}
    batch_size = 100
    for i in range(0, len(all_codes), batch_size):
        batch = all_codes[i : i + batch_size]
        s_ret, s_data = ctx.get_market_snapshot(batch)
        logger.info("Step 3 snapshot batch %d-%d: ret=%s rows=%s",
                    i, i + len(batch), s_ret,
                    len(s_data) if s_data is not None and not s_data.empty else 0)

        if s_ret != RET_OK:
            logger.error("  FAIL: snapshot batch %d-%d returned error ret=%s", i, i + len(batch), s_ret)
            continue

        if s_data is None or s_data.empty:
            logger.error("  FAIL: snapshot batch %d-%d returned empty dataframe", i, i + len(batch))
            continue

        if i == 0:
            logger.info("  Snapshot columns: %s", list(s_data.columns))
            logger.info("  Sample snapshot row:\n%s", s_data.head(1).to_string())

        for _, srow in s_data.iterrows():
            snap_rows[str(srow.get("code", ""))] = srow.to_dict()

    logger.info("Step 3 result: %d / %d codes have snapshots", len(snap_rows), len(all_codes))

    if not snap_rows:
        logger.error("  FAIL: zero snapshots returned for all %d codes", len(all_codes))
        return

    # ── Step 4: field population ──────────────────────────────────────────────
    option_fields = [
        "bid_price", "ask_price", "bid_vol", "ask_vol", "volume",
        "option_open_interest", "option_net_open_interest",
        "option_implied_volatility", "option_delta",
        "option_gamma", "option_theta", "option_vega",
    ]

    logger.info("Step 4 field population (first 3 snapshots):")
    for code_key in list(snap_rows.keys())[:3]:
        snap = snap_rows[code_key]
        vals = {f: snap.get(f) for f in option_fields}
        logger.info("  %s: %s", code_key, vals)

    # Check how many contracts have at least one non-zero pricing field
    def _f(val: object) -> float:
        try:
            return float(val) if val not in (None, "", "N/A") else 0.0
        except (TypeError, ValueError):
            return 0.0

    has_any_data = 0
    for c in snap_rows.values():
        if (
            _f(c.get("option_open_interest")) > 0
            or _f(c.get("option_net_open_interest")) > 0
            or _f(c.get("bid_price")) > 0
            or _f(c.get("option_implied_volatility")) > 0
            or _f(c.get("option_delta")) != 0
        ):
            has_any_data += 1

    logger.info("Step 4 result: %d / %d snapshots have at least one pricing field non-zero",
                has_any_data, len(snap_rows))

    # ── Step 5: has_real_data verdict ────────────────────────────────────────
    if has_any_data == 0:
        logger.error(
            "VERDICT: moomoo returns chain metadata but ALL Greeks/OI/bid are zero "
            "for %s expiry=%s. Possible causes:\n"
            "  - Expiry not yet actively traded (too far out or non-standard)\n"
            "  - Market closed / pre-market and moomoo hasn't populated snapshots\n"
            "  - Ticker requires a different index_option_type (e.g., ETF vs equity)\n"
            "  - moomoo subscription tier doesn't include options data for this symbol",
            ticker, expiry,
        )
    else:
        logger.info("VERDICT: chain fetch would SUCCEED for %s expiry=%s (%d contracts with data)",
                    ticker, expiry, has_any_data)


def main() -> None:
    default_tickers = ["IWM", "NVDA", "TSLA", "AAPL", "AMZN", "AMD", "XLK", "XLV", "XLI", "SLV", "TLT"]

    parser = argparse.ArgumentParser(description="Diagnose moomoo options chain fetch failures")
    parser.add_argument("tickers", nargs="*", default=default_tickers, help="Tickers to test")
    parser.add_argument("--expiry", default=_next_friday(), help="Expiry date YYYY-MM-DD (default: next Friday)")
    args = parser.parse_args()

    logger.info("Testing %d tickers for expiry %s", len(args.tickers), args.expiry)
    ctx = get_ctx()

    try:
        for ticker in args.tickers:
            try:
                diagnose_ticker(ctx, ticker, args.expiry)
            except Exception as exc:
                logger.exception("Unhandled error for %s: %s", ticker, exc)
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
