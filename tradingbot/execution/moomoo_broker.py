"""Moomoo implementation of the Broker protocol.

Maps the per-leg `LegSpec` / `LegFill` model onto moomoo's trade SDK
(`OpenSecTradeContext.place_order` / `modify_order` / `cancel_order` /
`order_list_query`). Not unit-tested (requires OpenD + a live account);
integration-tested via `scripts/moomoo_order_smoke.py`.

Idempotency: moomoo's `place_order` does not accept a client order id, so
we maintain a local map `client_order_id -> broker order id`. Re-submitting
the same client id returns the cached broker order without a second
network call.

Connection lifetime: the trade context is lazy and kept alive at module
level; long-running pipelines reuse one TCP connection to OpenD. On the
first SDK exception we tear it down so the next call reconnects.
"""

from __future__ import annotations

import os
import logging
import threading
from typing import Any

from tradingbot.execution.broker import LegFill, LegSpec, LegStatus

logger = logging.getLogger(__name__)

try:
    import moomoo as ft
    from moomoo import OpenSecTradeContext, TrdMarket, SecurityFirm, Currency
    MOOMOO_AVAILABLE = True
except ImportError:
    MOOMOO_AVAILABLE = False
    ft = None  # type: ignore


_STATUS_MAP: dict[Any, LegStatus] = {}  # populated lazily after import


def _init_status_map() -> None:
    """Map moomoo OrderStatus enums to our LegStatus. Done lazily so this
    file is importable without the SDK installed (e.g. for type checking)."""
    if _STATUS_MAP or not MOOMOO_AVAILABLE:
        return
    s = ft.OrderStatus
    _STATUS_MAP.update({
        s.NONE: LegStatus.PENDING,
        s.UNSUBMITTED: LegStatus.PENDING,
        s.WAITING_SUBMIT: LegStatus.PENDING,
        s.SUBMITTING: LegStatus.PENDING,
        s.SUBMITTED: LegStatus.WORKING,
        s.FILLED_PART: LegStatus.PARTIAL,
        s.FILLED_ALL: LegStatus.FILLED,
        s.CANCELLING_PART: LegStatus.PARTIAL,
        s.CANCELLING_ALL: LegStatus.WORKING,
        s.CANCELLED_PART: LegStatus.PARTIAL,
        s.CANCELLED_ALL: LegStatus.CANCELLED,
        s.FAILED: LegStatus.REJECTED,
        s.DISABLED: LegStatus.REJECTED,
        s.DELETED: LegStatus.CANCELLED,
        s.SUBMIT_FAILED: LegStatus.REJECTED,
        s.TIMEOUT: LegStatus.FAILED,
    })


def _format_option_code(spec: LegSpec, strike_override: float | None = None) -> str:
    """Moomoo US options code: e.g. US.AAPL230616C150000

    US. prefix is required by moomoo trade context.
    YYMMDD + C/P + strike*1000 (no zero-padding).
    """
    yymmdd = spec.expiry.strftime("%y%m%d")
    strike = strike_override if strike_override is not None else spec.strike
    strike_int = int(round(strike * 1000))
    return f"US.{spec.underlying.upper()}{yymmdd}{spec.right}{strike_int}"


# In-process cache: (underlying, expiry_iso, right) -> sorted list of valid strikes
_STRIKE_CACHE: dict[tuple[str, str, str], list[float]] = {}
# Max distance (in dollars) to snap a requested strike onto a real chain strike.
# Beyond this we reject rather than trade something materially different.
STRIKE_SNAP_MAX_DOLLARS = 2.5


def _fetch_valid_strikes(quote_ctx: Any, underlying: str, expiry: Any, right: str) -> list[float]:
    """Return moomoo's actual strike list for (underlying, expiry, right). Cached."""
    expiry_iso = expiry.strftime("%Y-%m-%d") if hasattr(expiry, "strftime") else str(expiry)
    key = (underlying.upper(), expiry_iso, right.upper())
    if key in _STRIKE_CACHE:
        return _STRIKE_CACHE[key]

    code = f"US.{underlying.upper()}"
    opt_type = ft.OptionType.CALL if right.upper() == "C" else ft.OptionType.PUT
    try:
        ret, data = quote_ctx.get_option_chain(
            code,
            index_option_type=ft.IndexOptionType.NORMAL,
            start=expiry_iso,
            end=expiry_iso,
            option_type=opt_type,
        )
    except Exception as exc:
        logger.warning("get_option_chain raised for %s %s %s: %s", underlying, expiry_iso, right, exc)
        return []

    if ret != 0 or data is None or getattr(data, "empty", True):
        return []

    try:
        strikes = sorted({float(s) for s in data["strike_price"].tolist() if s is not None})
    except Exception as exc:
        logger.warning("Could not parse strikes for %s %s %s: %s", underlying, expiry_iso, right, exc)
        return []

    _STRIKE_CACHE[key] = strikes
    return strikes


def _snap_strike(quote_ctx: Any, spec: LegSpec) -> tuple[float | None, str | None]:
    """Snap spec.strike to nearest valid moomoo strike.

    Returns (snapped_strike, None) on success, or (None, reason) if no strike
    is within STRIKE_SNAP_MAX_DOLLARS or the chain is empty.
    """
    strikes = _fetch_valid_strikes(quote_ctx, spec.underlying, spec.expiry, spec.right)
    if not strikes:
        # Chain unavailable (after-hours, weekend, broker error) — pass through
        # rather than block. The order may still reject downstream.
        return spec.strike, None
    nearest = min(strikes, key=lambda s: abs(s - spec.strike))
    if abs(nearest - spec.strike) > STRIKE_SNAP_MAX_DOLLARS:
        return None, (
            f"strike {spec.strike:g} has no moomoo listing within "
            f"${STRIKE_SNAP_MAX_DOLLARS:.2f} (nearest {nearest:g})"
        )
    return nearest, None


class MoomooBroker:
    """Broker protocol implementation for moomoo OpenAPI.

    The trading password is required for any non-read action; this class
    unlocks the trade context on first use and re-unlocks if the SDK reports
    the session expired.
    """

    def __init__(self, host: str | None = None, port: int | None = None):
        if not MOOMOO_AVAILABLE:
            raise ImportError("moomoo SDK not installed. Run: pip install moomoo-api")
        _init_status_map()

        self._host = host or os.getenv("MOOMOO_OPEND_HOST", "127.0.0.1")
        self._port = port or int(os.getenv("MOOMOO_OPEND_PORT", 11111))
        self._trd_ctx: Any = None
        self._lock = threading.Lock()
        self._cid_to_oid: dict[str, str] = {}
        self._trd_env = ft.TrdEnv.SIMULATE if os.getenv("OA2_TRADE_ENV", "SIMULATE") == "SIMULATE" else ft.TrdEnv.REAL
        self._acc_id = int(os.getenv("MOOMOO_ACCOUNT_ID", "0"))
        self._unlocked = False

    # ------------------------------------------------------------------

    def _ctx(self) -> Any:
        with self._lock:
            if self._trd_ctx is None:
                self._trd_ctx = OpenSecTradeContext(
                    filter_trdmarket=TrdMarket.US,
                    host=self._host,
                    port=self._port,
                    security_firm=SecurityFirm.FUTUINC,
                )
            if not self._unlocked and self._trd_env == ft.TrdEnv.REAL:
                pwd = os.getenv("MOOMOO_TRADE_PASSWORD", "")
                if not pwd:
                    raise RuntimeError("MOOMOO_TRADE_PASSWORD required for REAL env")
                ret, _ = self._trd_ctx.unlock_trade(pwd)
                if ret != 0:
                    raise RuntimeError(f"moomoo unlock_trade failed: {ret}")
                self._unlocked = True
        return self._trd_ctx

    def _reset(self) -> None:
        with self._lock:
            try:
                if self._trd_ctx is not None:
                    self._trd_ctx.close()
            except Exception:
                pass
            self._trd_ctx = None
            self._unlocked = False

    # ------------------------------------------------------------------
    # Broker protocol
    # ------------------------------------------------------------------

    def submit_leg(self, client_order_id: str, leg: LegSpec) -> LegFill:
        if client_order_id in self._cid_to_oid:
            return self.get_leg(self._cid_to_oid[client_order_id])

        try:
            from tradingbot.dataflows.moomoo_data import _get_quote_context
            snapped, reason = _snap_strike(_get_quote_context(), leg)
        except Exception as exc:
            logger.warning("strike snap failed for %s: %s", leg.underlying, exc)
            snapped, reason = leg.strike, None
        if snapped is None:
            return LegFill(leg_id="", status=LegStatus.REJECTED, error=f"strike snap: {reason}")
        if snapped != leg.strike:
            logger.info(
                "snapped %s %s %s strike %.4f -> %.4f",
                leg.underlying, leg.expiry, leg.right, leg.strike, snapped,
            )
        code = _format_option_code(leg, strike_override=snapped)
        trd_side = ft.TrdSide.BUY if leg.side > 0 else ft.TrdSide.SELL
        order_type = ft.OrderType.NORMAL if leg.limit_price is not None else ft.OrderType.MARKET
        price = leg.limit_price if leg.limit_price is not None else 0.0

        try:
            ret, data = self._ctx().place_order(
                price=price,
                qty=leg.contracts,
                code=code,
                trd_side=trd_side,
                order_type=order_type,
                trd_env=self._trd_env,
                acc_id=self._acc_id,
            )
            import time
            time.sleep(2.1)
        except Exception as e:
            self._reset()
            return LegFill(leg_id="", status=LegStatus.FAILED, error=f"place_order exception: {e}")

        if ret != 0 or data is None or data.empty:
            return LegFill(leg_id="", status=LegStatus.REJECTED, error=f"place_order ret={ret}: {data}")

        row = data.iloc[0]
        oid = str(row.get("order_id", ""))
        self._cid_to_oid[client_order_id] = oid
        return self._row_to_fill(row, oid)

    def cancel_leg(self, leg_id: str) -> LegFill:
        try:
            ret, _ = self._ctx().modify_order(
                modify_order_op=ft.ModifyOrderOp.CANCEL,
                order_id=leg_id,
                qty=0, price=0,
                trd_env=self._trd_env, acc_id=self._acc_id,
            )
        except Exception as e:
            self._reset()
            return LegFill(leg_id=leg_id, status=LegStatus.FAILED, error=f"cancel exception: {e}")
        if ret != 0:
            return LegFill(leg_id=leg_id, status=LegStatus.FAILED, error=f"cancel ret={ret}")
        return self.get_leg(leg_id)

    def replace_leg(self, leg_id: str, new_limit_price: float) -> LegFill:
        try:
            current = self.get_leg(leg_id)
            remaining = max(0, current.filled_qty)  # placeholder; recomputed below
            # We need the original qty to replace; moomoo modify expects total qty.
            # Pull from order_list_query.
            ret, df = self._ctx().order_list_query(
                order_id=leg_id, trd_env=self._trd_env, acc_id=self._acc_id,
            )
            if ret != 0 or df is None or df.empty:
                return LegFill(leg_id=leg_id, status=LegStatus.FAILED,
                               error=f"replace order_list_query ret={ret}")
            total_qty = int(df.iloc[0]["qty"])

            ret, _ = self._ctx().modify_order(
                modify_order_op=ft.ModifyOrderOp.NORMAL,
                order_id=leg_id,
                qty=total_qty,
                price=new_limit_price,
                trd_env=self._trd_env, acc_id=self._acc_id,
            )
        except Exception as e:
            self._reset()
            return LegFill(leg_id=leg_id, status=LegStatus.FAILED, error=f"replace exception: {e}")
        if ret != 0:
            return LegFill(leg_id=leg_id, status=LegStatus.FAILED, error=f"replace ret={ret}")
        return self.get_leg(leg_id)

    def get_leg(self, leg_id: str) -> LegFill:
        try:
            ret, df = self._ctx().order_list_query(
                order_id=leg_id, trd_env=self._trd_env, acc_id=self._acc_id,
            )
        except Exception as e:
            self._reset()
            return LegFill(leg_id=leg_id, status=LegStatus.FAILED, error=f"query exception: {e}")
        if ret != 0 or df is None or df.empty:
            return LegFill(leg_id=leg_id, status=LegStatus.FAILED, error=f"query ret={ret}")
        return self._row_to_fill(df.iloc[0], leg_id)

    def query_positions(self) -> list[Any]:  # Returns list[Position]
        """Query all open option positions from broker.

        Returns a list of Position objects representing holdings.
        Returns empty list on error (no positions or query failure).
        """
        try:
            ret, df = self._ctx().position_list_query(
                trd_env=self._trd_env,
                acc_id=self._acc_id,
            )
            if ret != 0 or df is None or df.empty:
                logger.warning(f"position_list_query ret={ret}")
                return []

            from tradingbot.execution.broker import Position
            positions: list[Position] = []

            for _, row in df.iterrows():
                try:
                    code = str(row.get("code", "")).strip()
                    if not code.startswith("US."):
                        continue  # Skip non-US options

                    # Parse moomoo option code: US.SPY260522C735000
                    # Format: US.[UNDERLYING][YYMMDD][C/P][STRIKE*1000]
                    code_no_prefix = code[3:]  # Remove "US."
                    if len(code_no_prefix) < 10:
                        continue

                    # Extract components
                    underlying = ""
                    for i, ch in enumerate(code_no_prefix):
                        if ch.isdigit():
                            underlying = code_no_prefix[:i]
                            break
                    if not underlying:
                        continue

                    rest = code_no_prefix[len(underlying):]
                    if len(rest) < 7:
                        continue

                    yymmdd = rest[:6]
                    right = rest[6]
                    strike_int = int(rest[7:])
                    strike = strike_int / 1000.0

                    # Parse date
                    import datetime as _dt
                    yy = int(yymmdd[:2])
                    mm = int(yymmdd[2:4])
                    dd = int(yymmdd[4:6])
                    year = 2000 + yy if yy <= 50 else 1900 + yy
                    expiry = _dt.date(year, mm, dd)

                    # Get position details
                    quantity = int(row.get("qty", 0) or 0)
                    if row.get("position_side") == "Short":
                        quantity = -quantity

                    # Handle N/A and None values from moomoo
                    def _safe_float(val, default=0.0):
                        if val is None or val == "" or str(val).strip().upper() == "N/A":
                            return default
                        try:
                            return float(val)
                        except (ValueError, TypeError):
                            return default

                    avg_price = _safe_float(row.get("cost_price"), 0.0)
                    current_price = _safe_float(row.get("market_price"), 0.0)
                    pnl = _safe_float(row.get("unrealized_pl"), 0.0)

                    pos = Position(
                        underlying=underlying,
                        expiry=expiry,
                        strike=strike,
                        right=right,
                        quantity=quantity,
                        avg_price=avg_price,
                        current_price=current_price,
                        pnl=pnl,
                    )
                    positions.append(pos)
                except Exception as e:
                    logger.warning(f"Failed to parse position row: {e}")
                    continue

            return positions

        except Exception as e:
            self._reset()
            logger.error(f"position_list_query exception: {e}")
            return []

    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_fill(row: Any, leg_id: str) -> LegFill:
        moomoo_status = row.get("order_status")
        status = _STATUS_MAP.get(moomoo_status, LegStatus.WORKING)

        raw_time = row.get("dealt_time")
        fill_time: str | None = None
        if raw_time and str(raw_time).strip() not in ("", "N/A", "nan"):
            fill_time = str(raw_time).strip()

        return LegFill(
            leg_id=leg_id,
            status=status,
            filled_qty=int(row.get("dealt_qty", 0) or 0),
            avg_fill_price=float(row.get("dealt_avg_price", 0) or 0.0),
            fill_time=fill_time,
        )
