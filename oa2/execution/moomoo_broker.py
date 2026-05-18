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

from oa2.execution.broker import LegFill, LegSpec, LegStatus

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


def _format_option_code(spec: LegSpec) -> str:
    """Moomoo US options code: e.g. US.AAPL230616C00150000

    YYMMDD + C/P + strike*1000 zero-padded to 8 digits.
    """
    yymmdd = spec.expiry.strftime("%y%m%d")
    strike_int = int(round(spec.strike * 1000))
    return f"US.{spec.underlying.upper()}{yymmdd}{spec.right}{strike_int:08d}"


class MoomooBroker:
    """Broker protocol implementation for moomoo OpenAPI.

    The trading password is required for any non-read action; this class
    unlocks the trade context on first use and re-unlocks if the SDK reports
    the session expired.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 11111):
        if not MOOMOO_AVAILABLE:
            raise ImportError("moomoo SDK not installed. Run: pip install moomoo-api")
        _init_status_map()

        self._host = host
        self._port = port
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

        code = _format_option_code(leg)
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

    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_fill(row: Any, leg_id: str) -> LegFill:
        moomoo_status = row.get("order_status")
        status = _STATUS_MAP.get(moomoo_status, LegStatus.WORKING)
        return LegFill(
            leg_id=leg_id,
            status=status,
            filled_qty=int(row.get("dealt_qty", 0) or 0),
            avg_fill_price=float(row.get("dealt_avg_price", 0) or 0.0),
        )
