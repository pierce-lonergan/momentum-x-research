"""
Tests for NYSE market calendar.

Verifies holiday detection, weekend detection, early close days,
and the pre-flight check_market_open() function.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.scheduling.market_calendar import (
    check_market_open,
    get_early_close_time,
    get_holiday_name,
    is_early_close,
    is_market_holiday,
    is_trading_day,
    is_weekend,
)


class TestIsWeekend:
    def test_saturday(self):
        assert is_weekend(date(2026, 3, 7)) is True  # Saturday

    def test_sunday(self):
        assert is_weekend(date(2026, 3, 8)) is True  # Sunday

    def test_monday(self):
        assert is_weekend(date(2026, 3, 9)) is False

    def test_friday(self):
        assert is_weekend(date(2026, 3, 6)) is False


class TestIsMarketHoliday:
    def test_christmas_2026(self):
        assert is_market_holiday(date(2026, 12, 25)) is True

    def test_new_years_2026(self):
        assert is_market_holiday(date(2026, 1, 1)) is True

    def test_mlk_day_2026(self):
        assert is_market_holiday(date(2026, 1, 19)) is True

    def test_presidents_day_2026(self):
        assert is_market_holiday(date(2026, 2, 16)) is True

    def test_good_friday_2026(self):
        assert is_market_holiday(date(2026, 4, 3)) is True

    def test_memorial_day_2026(self):
        assert is_market_holiday(date(2026, 5, 25)) is True

    def test_juneteenth_2026(self):
        assert is_market_holiday(date(2026, 6, 19)) is True

    def test_independence_day_observed_2026(self):
        # July 4, 2026 is Saturday, so observed on Friday July 3
        assert is_market_holiday(date(2026, 7, 3)) is True

    def test_labor_day_2026(self):
        assert is_market_holiday(date(2026, 9, 7)) is True

    def test_thanksgiving_2026(self):
        assert is_market_holiday(date(2026, 11, 26)) is True

    def test_normal_trading_day(self):
        assert is_market_holiday(date(2026, 3, 11)) is False  # Wednesday

    def test_unknown_year_returns_false(self):
        # Year 2030 not in our calendar — defaults to not-a-holiday
        assert is_market_holiday(date(2030, 12, 25)) is False


class TestIsEarlyClose:
    def test_day_after_thanksgiving_2026(self):
        assert is_early_close(date(2026, 11, 27)) is True

    def test_christmas_eve_2026(self):
        assert is_early_close(date(2026, 12, 24)) is True

    def test_normal_day(self):
        assert is_early_close(date(2026, 3, 11)) is False

    def test_early_close_time(self):
        t = get_early_close_time()
        assert t.hour == 13
        assert t.minute == 0


class TestIsTradingDay:
    def test_normal_weekday(self):
        assert is_trading_day(date(2026, 3, 11)) is True  # Wednesday

    def test_weekend(self):
        assert is_trading_day(date(2026, 3, 7)) is False  # Saturday

    def test_holiday(self):
        assert is_trading_day(date(2026, 12, 25)) is False  # Christmas

    def test_early_close_is_still_trading(self):
        # Early close days ARE trading days (just shorter)
        assert is_trading_day(date(2026, 11, 27)) is True


class TestCheckMarketOpen:
    def test_normal_day_returns_true(self):
        should_run, reason = check_market_open(date(2026, 3, 11))
        assert should_run is True
        assert "Normal" in reason

    def test_weekend_returns_false(self):
        should_run, reason = check_market_open(date(2026, 3, 7))
        assert should_run is False
        assert "Weekend" in reason

    def test_holiday_returns_false(self):
        should_run, reason = check_market_open(date(2026, 12, 25))
        assert should_run is False
        assert "holiday" in reason

    def test_early_close_returns_true_with_warning(self):
        should_run, reason = check_market_open(date(2026, 11, 27))
        assert should_run is True
        assert "Early close" in reason
        assert "1:00 PM" in reason


class TestGetHolidayName:
    def test_christmas(self):
        assert get_holiday_name(date(2026, 12, 25)) == "Christmas"

    def test_new_years(self):
        assert get_holiday_name(date(2026, 1, 1)) == "New Year's Day"

    def test_good_friday(self):
        assert get_holiday_name(date(2026, 4, 3)) == "Good Friday"

    def test_memorial_day(self):
        assert get_holiday_name(date(2026, 5, 25)) == "Memorial Day"

    def test_labor_day(self):
        assert get_holiday_name(date(2026, 9, 7)) == "Labor Day"

    def test_non_holiday_returns_none(self):
        assert get_holiday_name(date(2026, 3, 11)) is None


class TestAllHolidaysAreWeekdays:
    """Verify that all holidays in the calendar fall on weekdays."""

    @pytest.mark.parametrize("year", [2025, 2026, 2027])
    def test_holidays_are_weekdays(self, year):
        from src.scheduling.market_calendar import _NYSE_HOLIDAYS

        holidays = _NYSE_HOLIDAYS.get(year, [])
        for h in holidays:
            assert h.weekday() < 5, (
                f"Holiday {h} falls on {h.strftime('%A')} — "
                "NYSE holidays should be on weekdays (observed dates)"
            )
