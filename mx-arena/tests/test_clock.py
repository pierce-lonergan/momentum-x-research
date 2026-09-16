"""Tests for SimClock."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone

import pytest

from arena.clock import ClockMode, SimClock


@pytest.fixture
def clock():
    return SimClock(
        start=datetime(2026, 3, 25, 13, 30, tzinfo=timezone.utc),  # 9:30 ET
        end=datetime(2026, 3, 25, 20, 0, tzinfo=timezone.utc),     # 4:00 PM ET
    )


class TestClockBasics:
    def test_initial_time(self, clock):
        assert clock.now == datetime(2026, 3, 25, 13, 30, tzinfo=timezone.utc)

    def test_done_false_initially(self, clock):
        assert not clock.done

    def test_tick_count_starts_at_zero(self, clock):
        assert clock.tick_count == 0

    @pytest.mark.asyncio
    async def test_tick_advances_time(self, clock):
        await clock.tick()
        assert clock.now == datetime(2026, 3, 25, 13, 31, tzinfo=timezone.utc)
        assert clock.tick_count == 1

    @pytest.mark.asyncio
    async def test_tick_custom_delta(self, clock):
        await clock.tick(timedelta(minutes=5))
        assert clock.now == datetime(2026, 3, 25, 13, 35, tzinfo=timezone.utc)


class TestMarketHours:
    @pytest.mark.asyncio
    async def test_is_market_open_at_930(self, clock):
        assert clock.is_market_open

    @pytest.mark.asyncio
    async def test_is_premarket_at_8am(self):
        clock = SimClock(
            start=datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc),  # 8:00 ET
            end=datetime(2026, 3, 25, 20, 0, tzinfo=timezone.utc),
        )
        assert clock.is_premarket
        assert not clock.is_market_open

    @pytest.mark.asyncio
    async def test_market_closed_after_4pm(self):
        clock = SimClock(
            start=datetime(2026, 3, 25, 20, 1, tzinfo=timezone.utc),  # 4:01 PM ET
            end=datetime(2026, 3, 25, 21, 0, tzinfo=timezone.utc),
        )
        assert not clock.is_market_open


class TestClockSubscribers:
    @pytest.mark.asyncio
    async def test_sync_subscriber_called(self, clock):
        called = []
        clock.subscribe(lambda ts: called.append(ts))
        await clock.tick()
        assert len(called) == 1

    @pytest.mark.asyncio
    async def test_async_subscriber_called(self, clock):
        called = []

        async def cb(ts):
            called.append(ts)

        clock.subscribe_async(cb)
        await clock.tick()
        assert len(called) == 1


class TestClockRun:
    @pytest.mark.asyncio
    async def test_run_completes(self):
        clock = SimClock(
            start=datetime(2026, 3, 25, 13, 30, tzinfo=timezone.utc),
            end=datetime(2026, 3, 25, 13, 35, tzinfo=timezone.utc),  # 5 min
            mode=ClockMode.REPLAY,
        )
        await clock.run()
        assert clock.done
        assert clock.tick_count == 5

    @pytest.mark.asyncio
    async def test_run_calls_subscribers(self):
        clock = SimClock(
            start=datetime(2026, 3, 25, 13, 30, tzinfo=timezone.utc),
            end=datetime(2026, 3, 25, 13, 33, tzinfo=timezone.utc),  # 3 min
            mode=ClockMode.REPLAY,
        )
        tick_times = []
        clock.subscribe(lambda ts: tick_times.append(ts))
        await clock.run()
        assert len(tick_times) == 3


class TestNextOpenClose:
    def test_next_close_during_market(self, clock):
        nc = clock.next_close
        assert nc.hour == 20  # 4 PM ET = 20:00 UTC

    def test_next_open_after_close(self):
        clock = SimClock(
            start=datetime(2026, 3, 25, 21, 0, tzinfo=timezone.utc),  # 5 PM ET
            end=datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc),
        )
        no = clock.next_open
        assert no.day == 26  # Next day


class TestClockReset:
    @pytest.mark.asyncio
    async def test_reset_clears_state(self, clock):
        await clock.tick()
        await clock.tick()
        assert clock.tick_count == 2

        new_start = datetime(2026, 3, 26, 13, 30, tzinfo=timezone.utc)
        new_end = datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc)
        clock.reset(new_start, new_end)

        assert clock.now == new_start
        assert clock.tick_count == 0
        assert not clock.done
