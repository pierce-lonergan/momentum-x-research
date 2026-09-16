"""MOMENTUM-X Tests: doc 297 — the negative-stop / naked-position defect.

Node ID: tests.unit.test_doc297_stop_clamp
Graph Link: tested_by -> agent.technical

DEFECT (measured, not hypothesized): DeterministicTechnicalAgent derived its stop as
`current_price - 1.5 * ATR(14)` with no floor. On this universe (low-float gap-ups, frequent
bad bars) ATR-14 can exceed the price itself, so the proposed stop went NEGATIVE. Live decision
journals show 10 occurrences between 2026-02-10 and 2026-07-28:

    MUU  2026-07-20  entry 29.5101 -> stop -171.9207
    JLHL 2026-07-09  entry  5.5400 -> stop   -5.0972
    EHGO 2026-07-01  entry  2.1500 -> stop   -0.0999   (this one reached order_status='filled')
    LGHL 2026-07-28  entry  0.9895 -> stop   -0.0432

A negative stop cannot be placed at the broker, so the bracket fails to attach and the position
rides unprotected — the doc-281 "vanished stop" class, at its source.

These tests fail on the pre-doc-297 code and pass after the clamp.
"""
from __future__ import annotations

import pytest

from src.agents.deterministic_technical import DeterministicTechnicalAgent
from src.core.models import TechnicalSignal


def _bars_pathological_atr(price: float, n: int = 30) -> list[dict]:
    """Bars whose true range dwarfs the price — the shape that drove ATR-14 above price.

    Each bar spans roughly [0.02*price, 3*price], which is what a corrupted/mixed-scale bar or a
    violent low-float gap sequence looks like to ATR.
    """
    bars = []
    for i in range(n):
        hi = price * 3.0
        lo = price * 0.02
        bars.append({
            "timestamp": f"2026-07-20T13:{30 + i:02d}:00Z",
            "o": price, "h": hi, "l": lo, "c": price, "v": 1_000_000 + i,
        })
    return bars


@pytest.fixture
def agent() -> DeterministicTechnicalAgent:
    return DeterministicTechnicalAgent()


# The four live failures, replayed at their real entry prices.
LIVE_CASES = [
    pytest.param("MUU", 29.5101, id="MUU-2026-07-20"),
    pytest.param("JLHL", 5.5400, id="JLHL-2026-07-09"),
    pytest.param("EHGO", 2.1500, id="EHGO-2026-07-01-filled"),
    pytest.param("LGHL", 0.9895, id="LGHL-2026-07-28"),
]


class TestStopIsAlwaysPlaceable:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("ticker,price", LIVE_CASES)
    async def test_stop_is_strictly_positive(self, agent, ticker, price):
        """A proposed stop must be placeable at a broker: strictly greater than zero."""
        result = await agent.analyze(
            ticker=ticker,
            current_price=price,
            rvol=8.0,
            gap_pct=0.35,
            vwap=price * 0.98,
            price_data={"5min": _bars_pathological_atr(price)},
            indicators={},
        )
        assert isinstance(result, TechnicalSignal)
        if result.stop_loss_level is not None:
            assert result.stop_loss_level > 0, (
                f"{ticker}: proposed stop {result.stop_loss_level} is not placeable; "
                "the position would ride naked"
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ticker,price", LIVE_CASES)
    async def test_stop_stays_inside_the_legal_band(self, agent, ticker, price):
        """A corrupted ATR must be clamped, not honoured: the stop stays within 35% of entry."""
        result = await agent.analyze(
            ticker=ticker,
            current_price=price,
            rvol=8.0,
            gap_pct=0.35,
            vwap=price * 0.98,
            price_data={"5min": _bars_pathological_atr(price)},
            indicators={},
        )
        if result.stop_loss_level is not None:
            floor = price * (1.0 - 0.35)
            assert result.stop_loss_level >= floor - 1e-9, (
                f"{ticker}: stop {result.stop_loss_level} is below the 35% floor {floor}"
            )
            assert result.stop_loss_level < price, (
                f"{ticker}: a long stop must sit below entry; got {result.stop_loss_level} >= {price}"
            )

    @pytest.mark.asyncio
    async def test_clamp_is_flagged_loudly(self, agent):
        """When the clamp binds, the corrupted ATR must surface as a red flag, not pass silently."""
        result = await agent.analyze(
            ticker="MUU",
            current_price=29.5101,
            rvol=8.0,
            gap_pct=0.35,
            vwap=29.0,
            price_data={"5min": _bars_pathological_atr(29.5101)},
            indicators={},
        )
        flags = " ".join(getattr(result, "flags", []) or [])
        assert "ATR" in flags and "clamp" in flags.lower(), (
            f"corrupted ATR was clamped silently; flags={getattr(result, 'flags', None)}"
        )


class TestHealthyAtrIsUntouched:
    @pytest.mark.asyncio
    async def test_normal_atr_still_sets_an_atr_based_stop(self, agent):
        """The clamp must not disturb the normal path: a sane ATR keeps its 1.5R stop."""
        price = 5.00
        bars = [
            {
                "timestamp": f"2026-07-20T13:{30 + i:02d}:00Z",
                "o": price, "h": price * 1.01, "l": price * 0.99, "c": price, "v": 100_000,
            }
            for i in range(30)
        ]
        result = await agent.analyze(
            ticker="NORMAL",
            current_price=price,
            rvol=3.0,
            gap_pct=0.05,
            vwap=price * 0.99,
            price_data={"5min": bars},
            indicators={},
        )
        if result.stop_loss_level is not None:
            # ~1% bar ranges => ATR well under the band; stop should sit just below entry,
            # nowhere near the 35% floor.
            assert result.stop_loss_level > price * 0.90, (
                f"healthy ATR produced an over-wide stop {result.stop_loss_level} for entry {price}"
            )
            assert result.stop_loss_level < price
