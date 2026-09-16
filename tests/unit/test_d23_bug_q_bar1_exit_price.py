"""
Thu 2026-04-23 EOD Bug Q regression tests.

Background. XNDU's BAR-1 EXIT today reported `Arena_actual=+$0.00
(entry=$30.7400 exit=$30.7400 qty=395)`. Exact-zero P&L on a 395-share
microcap position is suspicious — possible but not the modal outcome.

Root cause at main.py:4356-4358:

    _bar1_snap = _d78_snapshots.get(pos.ticker, {})
    _bar1_exit_px = float(
        _bar1_snap.get("last_price", 0) or pos.entry_price
    )

When `_d78_snapshots[ticker]` is empty OR `last_price` is missing/zero,
the formula falls back to `pos.entry_price` SILENTLY. The result:
`exit_px == entry_px` → `Arena_actual = $0.00` regardless of actual
market outcome. This silently masks every BAR-1 exit's realised
performance whenever the snapshot is stale.

This violates state-mutation reversibility rule (f) — the system
reports what it WANTS to be true (zero P&L) not what IS true.

Fix:
1. New helper `_resolve_bar1_exit_price(snap, client, ticker, fallback)`
   that tries snapshot → broker NBBO mid → emits D228 warning →
   fallback. Never silent.
2. main.py call site uses the helper instead of the bare `or` fallback.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestBugQ_Bar1ExitPriceResolver:
    """The new helper resolves the BAR-1 exit price with explicit
    fallback chain and a labeled warning when the snapshot path fails."""

    @pytest.mark.asyncio
    async def test_uses_snapshot_when_present(self):
        from src.execution.bar1_exit_logging import resolve_bar1_exit_price

        snap = {"last_price": 31.50}
        client = MagicMock()
        client.get_latest_quote = AsyncMock()
        px, source = await resolve_bar1_exit_price(
            snap=snap, client=client, ticker="XNDU", fallback_price=30.74,
        )
        assert px == 31.50
        assert source == "snapshot"
        # Must NOT have hit the broker if snapshot was good
        client.get_latest_quote.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_back_to_broker_quote_when_snapshot_empty(self):
        from src.execution.bar1_exit_logging import resolve_bar1_exit_price

        snap = {}  # empty snapshot
        client = MagicMock()
        client.get_latest_quote = AsyncMock(return_value={
            "bp": 31.20, "ap": 31.30,  # bid/ask
        })
        px, source = await resolve_bar1_exit_price(
            snap=snap, client=client, ticker="XNDU", fallback_price=30.74,
        )
        # Should be the NBBO mid: (31.20 + 31.30) / 2 = 31.25
        assert px == pytest.approx(31.25, abs=0.001)
        assert source == "broker_nbbo_mid"

    @pytest.mark.asyncio
    async def test_emits_d228_warning_when_both_fail(self, caplog):
        """When snapshot is empty AND broker quote fails, emit a
        D228 BAR1_EXIT_PX_DEGRADED warning before falling back to
        entry_price. The warning is the difference between silent
        fallback (the bug) and explicit acknowledgment (the fix)."""
        from src.execution.bar1_exit_logging import resolve_bar1_exit_price

        snap = {}
        client = MagicMock()
        client.get_latest_quote = AsyncMock(side_effect=RuntimeError("API down"))
        with caplog.at_level(logging.WARNING, logger="src.execution.bar1_exit_logging"):
            px, source = await resolve_bar1_exit_price(
                snap=snap, client=client, ticker="XNDU",
                fallback_price=30.74,
            )
        assert px == 30.74  # fell back to entry
        assert source == "fallback_entry_price"
        # The labeled warning MUST fire
        warnings = [r for r in caplog.records if "D228 BAR1_EXIT_PX_DEGRADED" in r.message]
        assert len(warnings) == 1, (
            f"Bug Q regression: when both snapshot and broker quote fail, "
            f"a D228 warning MUST fire so operators can see the silent "
            f"fallback. Got: {[r.message for r in caplog.records]}"
        )
        # Warning must mention the ticker so operators can grep
        assert "XNDU" in warnings[0].message

    @pytest.mark.asyncio
    async def test_handles_zero_last_price_in_snapshot(self):
        """Snapshot present but last_price is 0 (stale tick) — must
        treat as missing and fall through to broker, not return 0."""
        from src.execution.bar1_exit_logging import resolve_bar1_exit_price

        snap = {"last_price": 0.0}
        client = MagicMock()
        client.get_latest_quote = AsyncMock(return_value={
            "bp": 31.00, "ap": 31.10,
        })
        px, source = await resolve_bar1_exit_price(
            snap=snap, client=client, ticker="XNDU", fallback_price=30.74,
        )
        assert px == pytest.approx(31.05, abs=0.001)
        assert source == "broker_nbbo_mid"

    @pytest.mark.asyncio
    async def test_xndu_today_replay_would_have_flagged_silently_zero(self):
        """Replay XNDU's exact situation: snapshot empty, BAR-1 fires.
        Today's bug returned exit_px = entry_price silently → +$0.00
        Arena_actual. The fix MUST either (a) get a real broker quote
        OR (b) emit D228 warning. Never silent equality."""
        from src.execution.bar1_exit_logging import resolve_bar1_exit_price

        snap = {}  # XNDU's actual situation today (no _d78 snapshot)
        client = MagicMock()
        # Simulated: broker returns a real quote near entry but not
        # exactly equal to entry — this is the realistic case that
        # the bug masked today.
        client.get_latest_quote = AsyncMock(return_value={
            "bp": 30.70, "ap": 30.78,
        })
        px, source = await resolve_bar1_exit_price(
            snap=snap, client=client, ticker="XNDU", fallback_price=30.74,
        )
        # The realistic exit price would be ~30.74 (mid of 30.70/30.78)
        # — which happens to be ≈ entry. But the SOURCE must be
        # "broker_nbbo_mid", not silent fallback.
        assert source == "broker_nbbo_mid", (
            "Bug Q regression: XNDU's snapshot-empty case must hit the "
            "broker quote path, not silently fall back to entry_price."
        )
