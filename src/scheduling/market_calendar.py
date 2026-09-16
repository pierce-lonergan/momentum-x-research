"""
MOMENTUM-X NYSE Market Calendar

### ARCHITECTURAL CONTEXT
Prevents the system from running on market holidays. The PowerShell launcher
already skips weekends, but has no holiday awareness — it runs on Christmas,
Good Friday, MLK Day, etc., wasting 13 hours trying to trade a closed market.

### DESIGN DECISIONS
- Static holiday list (NYSE publishes schedule annually in advance)
- Covers 2025-2027 — extend annually
- Early close days (day before holidays) are included as half-days
- Pure Python, no external dependencies
- check_market_open() is the primary entry point for pre-flight validation
"""

from __future__ import annotations

import logging
from datetime import date, time

logger = logging.getLogger(__name__)


# NYSE Full-Day Closures
# Source: https://www.nyse.com/markets/hours-calendars
# Format: (month, day) for fixed holidays, or explicit dates for floating ones
_NYSE_HOLIDAYS: dict[int, list[date]] = {
    2025: [
        date(2025, 1, 1),    # New Year's Day
        date(2025, 1, 20),   # MLK Day
        date(2025, 2, 17),   # Presidents' Day
        date(2025, 4, 18),   # Good Friday
        date(2025, 5, 26),   # Memorial Day
        date(2025, 6, 19),   # Juneteenth
        date(2025, 7, 4),    # Independence Day
        date(2025, 9, 1),    # Labor Day
        date(2025, 11, 27),  # Thanksgiving
        date(2025, 12, 25),  # Christmas
    ],
    2026: [
        date(2026, 1, 1),    # New Year's Day
        date(2026, 1, 19),   # MLK Day
        date(2026, 2, 16),   # Presidents' Day
        date(2026, 4, 3),    # Good Friday
        date(2026, 5, 25),   # Memorial Day
        date(2026, 6, 19),   # Juneteenth
        date(2026, 7, 3),    # Independence Day (observed, 7/4 = Saturday)
        date(2026, 9, 7),    # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
    ],
    2027: [
        date(2027, 1, 1),    # New Year's Day
        date(2027, 1, 18),   # MLK Day
        date(2027, 2, 15),   # Presidents' Day
        date(2027, 3, 26),   # Good Friday
        date(2027, 5, 31),   # Memorial Day
        date(2027, 6, 18),   # Juneteenth (observed, 6/19 = Saturday)
        date(2027, 7, 5),    # Independence Day (observed, 7/4 = Sunday)
        date(2027, 9, 6),    # Labor Day
        date(2027, 11, 25),  # Thanksgiving
        date(2027, 12, 24),  # Christmas (observed, 12/25 = Saturday)
    ],
}

# NYSE Early Close Days (1:00 PM ET close instead of 4:00 PM)
_NYSE_EARLY_CLOSE: dict[int, list[date]] = {
    2025: [
        date(2025, 7, 3),    # Day before Independence Day
        date(2025, 11, 28),  # Day after Thanksgiving
        date(2025, 12, 24),  # Christmas Eve
    ],
    2026: [
        date(2026, 11, 27),  # Day after Thanksgiving
        date(2026, 12, 24),  # Christmas Eve
    ],
    2027: [
        date(2027, 11, 26),  # Day after Thanksgiving
    ],
}


def is_market_holiday(d: date | None = None) -> bool:
    """Check if the given date is an NYSE holiday (full closure).

    Args:
        d: Date to check. Defaults to today.

    Returns:
        True if market is closed for holiday.
    """
    if d is None:
        d = date.today()

    holidays = _NYSE_HOLIDAYS.get(d.year, [])
    return d in holidays


def is_early_close(d: date | None = None) -> bool:
    """Check if the given date is an NYSE early close day (1:00 PM ET).

    Args:
        d: Date to check. Defaults to today.

    Returns:
        True if market closes early (1:00 PM ET).
    """
    if d is None:
        d = date.today()

    early_days = _NYSE_EARLY_CLOSE.get(d.year, [])
    return d in early_days


def get_early_close_time() -> time:
    """Return the early close time (1:00 PM ET)."""
    return time(13, 0)


def is_weekend(d: date | None = None) -> bool:
    """Check if the given date is a weekend."""
    if d is None:
        d = date.today()
    return d.weekday() >= 5  # 5=Saturday, 6=Sunday


def is_trading_day(d: date | None = None) -> bool:
    """Check if the given date is a valid trading day.

    A trading day is a weekday that is not an NYSE holiday.

    Args:
        d: Date to check. Defaults to today.

    Returns:
        True if the market is open for trading.
    """
    if d is None:
        d = date.today()

    if is_weekend(d):
        return False

    if is_market_holiday(d):
        return False

    return True


def check_market_open(d: date | None = None) -> tuple[bool, str]:
    """Pre-flight check: should we run the trading session today?

    Returns:
        (should_run, reason_string)
    """
    if d is None:
        d = date.today()

    if is_weekend(d):
        return False, f"Weekend: {d.strftime('%A')} {d}"

    if is_market_holiday(d):
        return False, f"NYSE holiday: {d}"

    if is_early_close(d):
        logger.info("Early close day: %s — market closes at 1:00 PM ET", d)
        return True, f"Early close day: {d} — closes 1:00 PM ET"

    return True, f"Normal trading day: {d}"


def get_holiday_name(d: date) -> str | None:
    """Get a human-readable name for a holiday date, if known."""
    _names = {
        (1, 1): "New Year's Day",
        (1, 18): "MLK Day", (1, 19): "MLK Day", (1, 20): "MLK Day",
        (2, 15): "Presidents' Day", (2, 16): "Presidents' Day",
        (2, 17): "Presidents' Day",
        (6, 18): "Juneteenth (observed)", (6, 19): "Juneteenth",
        (7, 3): "Independence Day (observed)", (7, 4): "Independence Day",
        (7, 5): "Independence Day (observed)",
        (11, 25): "Thanksgiving", (11, 26): "Thanksgiving",
        (11, 27): "Thanksgiving",
        (12, 24): "Christmas (observed)", (12, 25): "Christmas",
    }
    # Check fixed-date names
    name = _names.get((d.month, d.day))
    if name:
        return name
    # Good Friday, Memorial Day, Labor Day are floating — check if in holiday list
    holidays = _NYSE_HOLIDAYS.get(d.year, [])
    if d in holidays:
        if d.month == 3 or d.month == 4:
            return "Good Friday"
        if d.month == 5 and d.day >= 25:
            return "Memorial Day"
        if d.month == 9 and d.day <= 7:
            return "Labor Day"
    return None
