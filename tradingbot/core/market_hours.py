"""US market hours and holiday management.

Computes market holidays dynamically from pandas calendar to avoid hardcoding.
Provides market open/close times and intelligent sleep calculations.
"""

from __future__ import annotations

import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.holiday import (
    USFederalHolidayCalendar,
    AbstractHolidayCalendar,
)

ET = ZoneInfo("America/New_York")

MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 0


@lru_cache(maxsize=1)
def _get_holiday_calendar() -> AbstractHolidayCalendar:
    """Get US Federal Holiday calendar (cached)."""
    return USFederalHolidayCalendar()


def get_market_holidays(year: int | None = None) -> pd.DatetimeIndex:
    """Get all US market holidays for a given year.

    Args:
        year: Year to fetch holidays for. If None, uses current year.

    Returns:
        DatetimeIndex of market holidays (days when market is closed).
    """
    if year is None:
        year = datetime.datetime.now(ET).year

    cal = _get_holiday_calendar()
    start = f"{year}-01-01"
    end = f"{year}-12-31"
    return cal.holidays(start=start, end=end)


def is_market_holiday(dt: datetime.datetime | None = None) -> bool:
    """Check if a date is a market holiday.

    Args:
        dt: Date to check (ET). If None, uses today.

    Returns:
        True if the date is a US market holiday, False otherwise.
    """
    if dt is None:
        dt = datetime.datetime.now(ET)

    date = dt.date()
    holidays = get_market_holidays(date.year)
    return pd.Timestamp(date) in holidays


def is_market_day(dt: datetime.datetime | None = None) -> bool:
    """Check if a date is a market trading day.

    Market is closed on:
    - Weekends (Saturday/Sunday)
    - US Federal Holidays

    Args:
        dt: Date to check (ET). If None, uses now.

    Returns:
        True if the market is (would be) open, False if closed.
    """
    if dt is None:
        dt = datetime.datetime.now(ET)

    # Weekdays are 0-4 (Mon-Fri)
    if dt.weekday() >= 5:
        return False

    return not is_market_holiday(dt)


def is_market_open(dt: datetime.datetime | None = None) -> bool:
    """Check if market is currently open.

    Market hours: 9:30 AM - 4:00 PM ET on market trading days.

    Args:
        dt: Time to check (ET). If None, uses now.

    Returns:
        True if market is open, False otherwise.
    """
    if dt is None:
        dt = datetime.datetime.now(ET)

    if not is_market_day(dt):
        return False

    market_open = dt.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    market_close = dt.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    return market_open <= dt < market_close


def time_until_market_open(dt: datetime.datetime | None = None) -> float:
    """Calculate seconds until next market open.

    Args:
        dt: Current time (ET). If None, uses now.

    Returns:
        Seconds until next market open (9:30 AM ET on next market day).
    """
    if dt is None:
        dt = datetime.datetime.now(ET)

    # Check if market is already open
    if is_market_open(dt):
        return 0.0

    # Find next market day and calculate open time
    target = dt.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)

    # If target is before now, move to next day
    if target <= dt:
        target += datetime.timedelta(days=1)

    # Skip to next market day if target day is a holiday/weekend
    while not is_market_day(target):
        target += datetime.timedelta(days=1)
        target = target.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)

    return (target - dt).total_seconds()


def time_until_market_close(dt: datetime.datetime | None = None) -> float:
    """Calculate seconds until market close today (if open).

    Args:
        dt: Current time (ET). If None, uses now.

    Returns:
        Seconds until market close (4:00 PM ET), or 0 if market not open today.
    """
    if dt is None:
        dt = datetime.datetime.now(ET)

    if not is_market_open(dt):
        return 0.0

    market_close = dt.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    return max(0.0, (market_close - dt).total_seconds())


def time_until_next_event(target_hour: int, target_minute: int, dt: datetime.datetime | None = None) -> float:
    """Calculate seconds until next occurrence of a specific time.

    Args:
        target_hour: Hour (0-23) in ET.
        target_minute: Minute (0-59) in ET.
        dt: Current time (ET). If None, uses now.

    Returns:
        Seconds until next occurrence of target time.
    """
    if dt is None:
        dt = datetime.datetime.now(ET)

    target = dt.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

    # If target time has passed today, aim for tomorrow
    if dt >= target:
        target += datetime.timedelta(days=1)

    return (target - dt).total_seconds()
