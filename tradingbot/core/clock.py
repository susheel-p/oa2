"""Clock abstraction for deterministic time in backtests and live runs.

Wall-clock calls (`time.time()`, `datetime.now()`) scattered through the
codebase make backtests infidel — a hard EOD cutoff that reads the OS clock
will fire at 3:55pm of the day the backtest is RUN, not the day being
replayed. Likewise position age, last-checked timestamps, and DTE rollover
all need to advance with replay time.

Every component that needs "now" should accept a `Clock` instance and never
call `time.time()` or `datetime.now()` directly.

Implementations:
    SystemClock — production. Delegates to time.time() / datetime.now(tz=ET).
    ManualClock — tests + backtests. Caller advances time explicitly.
"""

from __future__ import annotations

import datetime as _dt
import time as _time
from typing import Protocol
from zoneinfo import ZoneInfo


_ET = ZoneInfo("America/New_York")


class Clock(Protocol):
    """Minimal time interface used across tradingbot."""

    def now(self) -> float:
        """Unix timestamp (seconds since epoch). Equivalent of time.time()."""
        ...

    def now_et(self) -> _dt.datetime:
        """Timezone-aware datetime in America/New_York."""
        ...


class SystemClock:
    """Production clock. Wraps the real OS time sources."""

    def now(self) -> float:
        return _time.time()

    def now_et(self) -> _dt.datetime:
        return _dt.datetime.now(tz=_ET)


class ManualClock:
    """Test / backtest clock. Time only advances when the caller asks.

    Construct with either a unix timestamp or an aware datetime; either form
    is accepted by `set()` and `advance()`.
    """

    def __init__(self, initial: float | _dt.datetime):
        self._t: float = self._coerce(initial)

    @staticmethod
    def _coerce(value: float | _dt.datetime) -> float:
        if isinstance(value, _dt.datetime):
            if value.tzinfo is None:
                raise ValueError("ManualClock requires timezone-aware datetimes")
            return value.timestamp()
        return float(value)

    def now(self) -> float:
        return self._t

    def now_et(self) -> _dt.datetime:
        return _dt.datetime.fromtimestamp(self._t, tz=_ET)

    def set(self, value: float | _dt.datetime) -> None:
        self._t = self._coerce(value)

    def advance(self, seconds: float) -> None:
        self._t += float(seconds)
