"""D295 (2026-05-13) — D91 next-open close must record_close in journal.

Pin the production reconciliation gap from 2026-05-13:
- broker showed +$41,083.22 across 70 tickers, all marked
  ``journal=MISSING`` (D222 mid-session WARNING).
- root cause: ``bridge._close_overnight_position`` (the D91 next-open
  carry-over close) closed positions without calling
  ``TradeJournal.record_close()``.
- the same hole was patched in BAR-1 (D146) on 2026-04-08 (Bug #13),
  but D278's ``t1_next_open`` path bypassed the journal record because
  D91 lives in ``src/execution/bridge.py`` (not the post-fill handler).

Post-D295: when ``trade_journal`` is passed, STEP 4 of
``_close_overnight_position`` records the close (best-effort, never
fatal) so EOD reconciliation (D222 / D238) stops flagging
``journal=MISSING``.

Backwards-compat: omitting ``trade_journal`` preserves the original
3-step behavior — the D91/D279 unit tests still pass.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── Helpers ─────────────────────────────────────────────────────────


def _make_journal_with_open_buy(ticker: str, entry_price: float):
    """Build a stub trade_journal whose `_entries` dict has one open BUY
    entry for `ticker`. Records every record_close() call on a list."""
    entry = MagicMock()
    entry.ticker = ticker
    entry.action = "BUY"
    entry.entry_price = entry_price

    journal = MagicMock()
    journal._entries = {"trade-1": entry}

    journal.recorded_closes = []  # accumulator for assertions

    def _record_close(trade_id, **kwargs):
        journal.recorded_closes.append({"trade_id": trade_id, **kwargs})

    journal.record_close = MagicMock(side_effect=_record_close)
    return journal


def _make_overnight_pm(ticker: str, qty: int, entry_price: float,
                       stop_oid: str = "stop-1"):
    """PositionManager stub matching test_d91_overnight_close.py's pattern."""
    pos = MagicMock()
    pos.ticker = ticker
    pos.remaining_qty = qty
    pos.entry_price = entry_price
    pos.stop_order_id = stop_oid

    pm = MagicMock()
    pm.has_position = MagicMock(return_value=True)
    pm.get_position = MagicMock(return_value=pos)
    pm.remove_position = MagicMock()
    return pm


def _make_client_with_snapshot(ticker: str, last_price: float):
    client = MagicMock()
    client.cancel_order = AsyncMock(return_value={"id": "stop-1"})
    client.close_position = AsyncMock(return_value={"id": "sell-1"})
    client.get_snapshots = AsyncMock(
        return_value={ticker: {"last_price": last_price}}
    )
    return client


# ── Headline regression: production failure mode ────────────────────


@pytest.mark.asyncio
async def test_d91_close_records_journal_when_journal_provided():
    """The D91 close must call journal.record_close() when trade_journal
    is provided. This is the binary fix for the 2026-05-13 D222/D238
    journal=MISSING reconciliation gap."""
    from src.execution.bridge import _close_overnight_position

    ticker = "ELSE"
    pm = _make_overnight_pm(ticker, qty=100, entry_price=10.00)
    client = _make_client_with_snapshot(ticker, last_price=10.50)
    journal = _make_journal_with_open_buy(ticker, entry_price=10.00)

    ok = await _close_overnight_position(
        client=client, position_manager=pm, ticker=ticker,
        trade_journal=journal,
    )
    assert ok is True, "D91 close should report success"
    assert len(journal.recorded_closes) == 1, (
        "Exactly one record_close() expected for an open BUY position"
    )
    rc = journal.recorded_closes[0]
    assert rc["trade_id"] == "trade-1"
    assert rc["exit_reason"] == "NEXT_OPEN"
    # PnL: ($10.50 exit - $10.00 entry) × 100 qty = +$50
    assert rc["realized_pnl"] == pytest.approx(50.0, abs=0.01)
    assert rc["exit_price"] == pytest.approx(10.50, abs=0.01)


# ── Backwards-compat: original 3-step path still works ──────────────


@pytest.mark.asyncio
async def test_d91_close_without_journal_kwarg_is_unchanged():
    """Existing callers that don't pass trade_journal must continue to
    work (TestD91CloseRoutineSteps + D279 tests)."""
    from src.execution.bridge import _close_overnight_position

    ticker = "ELSE"
    pm = _make_overnight_pm(ticker, qty=100, entry_price=10.00)
    client = _make_client_with_snapshot(ticker, last_price=10.50)

    ok = await _close_overnight_position(
        client=client, position_manager=pm, ticker=ticker,
    )
    assert ok is True, "D91 close should still succeed without journal kwarg"
    # Snapshot should NOT have been called when no journal (avoid noise)
    assert client.get_snapshots.await_count == 0


# ── Defensive: snapshot failure must not break the close ─────────────


@pytest.mark.asyncio
async def test_d91_close_proceeds_when_snapshot_fails():
    """If get_snapshots raises, the close still succeeds and journal
    falls back to entry_price (zero pnl) so D238 stops complaining."""
    from src.execution.bridge import _close_overnight_position

    ticker = "ELSE"
    pm = _make_overnight_pm(ticker, qty=100, entry_price=10.00)
    client = _make_client_with_snapshot(ticker, last_price=10.50)
    client.get_snapshots = AsyncMock(side_effect=RuntimeError("snapshot down"))
    journal = _make_journal_with_open_buy(ticker, entry_price=10.00)

    ok = await _close_overnight_position(
        client=client, position_manager=pm, ticker=ticker,
        trade_journal=journal,
    )
    assert ok is True, "Close must succeed even if snapshot lookup fails"
    assert len(journal.recorded_closes) == 1
    # Fallback: exit = entry → realized_pnl = 0
    rc = journal.recorded_closes[0]
    assert rc["realized_pnl"] == pytest.approx(0.0, abs=0.01)


# ── Defensive: journal exception must not break the close ────────────


@pytest.mark.asyncio
async def test_d91_close_succeeds_when_record_close_raises(caplog):
    """If record_close() itself raises, log a warning but still report
    True — broker close already happened."""
    import logging
    from src.execution.bridge import _close_overnight_position

    ticker = "ELSE"
    pm = _make_overnight_pm(ticker, qty=100, entry_price=10.00)
    client = _make_client_with_snapshot(ticker, last_price=10.50)
    journal = _make_journal_with_open_buy(ticker, entry_price=10.00)
    journal.record_close = MagicMock(side_effect=RuntimeError("journal corrupt"))

    with caplog.at_level(logging.WARNING, logger="src.execution.bridge"):
        ok = await _close_overnight_position(
            client=client, position_manager=pm, ticker=ticker,
            trade_journal=journal,
        )
    assert ok is True, "Broker close already done; advisory journal failure must not flip ok"
    # Warning must be emitted so operators see the journal hole
    warns = [r.message for r in caplog.records if "STEP 4" in r.message]
    assert any("raised" in w for w in warns), (
        f"expected STEP 4 warning about record_close raising; got {warns}"
    )


# ── Edge: no matching BUY entry in journal logs a warning ────────────


@pytest.mark.asyncio
async def test_d91_close_warns_when_no_journal_match(caplog):
    """If the position is closed but has no open BUY entry in the
    journal (e.g. process restart lost in-memory journal state), warn
    so D238 still has visibility into the gap."""
    import logging
    from src.execution.bridge import _close_overnight_position

    ticker = "ELSE"
    pm = _make_overnight_pm(ticker, qty=100, entry_price=10.00)
    client = _make_client_with_snapshot(ticker, last_price=10.50)
    journal = _make_journal_with_open_buy("DIFFERENT_TICKER", entry_price=5.00)

    with caplog.at_level(logging.WARNING, logger="src.execution.bridge"):
        ok = await _close_overnight_position(
            client=client, position_manager=pm, ticker=ticker,
            trade_journal=journal,
        )
    assert ok is True
    assert len(journal.recorded_closes) == 0, "no matching entry → no record"
    warns = [r.message for r in caplog.records if "no open BUY" in r.message]
    assert warns, "expected warning about missing journal entry"


# ── Edge: zero entry_price doesn't divide-by-zero ────────────────────


@pytest.mark.asyncio
async def test_d91_close_handles_zero_entry_price():
    """A zero entry_price (data corruption / restart edge case) must not
    crash; pnl should default to 0."""
    from src.execution.bridge import _close_overnight_position

    ticker = "ELSE"
    pm = _make_overnight_pm(ticker, qty=100, entry_price=0.0)
    client = _make_client_with_snapshot(ticker, last_price=10.50)
    journal = _make_journal_with_open_buy(ticker, entry_price=0.0)

    ok = await _close_overnight_position(
        client=client, position_manager=pm, ticker=ticker,
        trade_journal=journal,
    )
    assert ok is True
    assert len(journal.recorded_closes) == 1
    assert journal.recorded_closes[0]["realized_pnl"] == 0.0
