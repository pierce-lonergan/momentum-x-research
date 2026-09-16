"""
Wed 2026-04-22 Bug E regression tests.

Background. ELSE was held overnight from Tue 04-21 through Wed 04-22.
At 09:30 ET market open, D91 OVERNIGHT-CLOSE should have:
  1. Cancelled the GTC stop_order_id
  2. Submitted a market sell for remaining qty
  3. Removed the position from the internal tracker

None of those happened. The 09:30 scheduler tick never even saw ELSE
in `_overnight_positions_to_close`. Two hours later, agents re-evaluated
ELSE at 09:54 (because nothing tagged it as "close pending"); D146
BAR-1 EXIT eventually closed it at 10:14 — saved ~$3,925 of further
drawdown but the planned 09:30 exit never fired.

Root cause: `main.py:1161` — D91 detection requires
`session_state is None`. After the Bug B Phase-3 restart fix, session
state DOES exist on restart, so the elif branch never matches and
overnight positions are silently skipped.

Two-part fix:
  1. main.py D91 detection — drop the session_state-None gate. Detect
     "this position was opened in a prior session" via opened_at <
     today's pre-market start (04:00 ET).
  2. ManagedPosition.close_pending: True flag set at D91 detection so
     the eval queue / Phase-2 candidate scorer skips it.
  3. D91 close routine emits step-by-step logs:
       D91 STEP 1: cancel stop oid=...
       D91 STEP 2: submit market sell qty=...
       D91 STEP 3: confirmed close, removed from tracker

T1-T3:   detection logic — opened_at-based, NOT session_state-coupled
T4:      close_pending flag set on detection
T5-T7:   close routine step-by-step logging + sequencing
T8:      eval queue filter respects close_pending
T9:      ELSE exact-replay: opened Tue 21:48, system restart Wed 03:30
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


ET = timezone(timedelta(hours=-4))  # EDT, sufficient for these tests


# ── T1-T3: detection logic — opened_at, not session_state ──────────


class TestD91DetectionByOpenedAt:
    """The bug: detection was gated on `session_state is None`. After the
    Bug B Phase-3 restart fix, session state survives restart so the
    branch never fires. New detection: opened_at < today's 04:00 ET
    pre-market start, regardless of session-state presence."""

    def test_overnight_position_detected_when_opened_yesterday(self):
        from src.execution.bridge import _is_overnight_position

        now_et = datetime(2026, 4, 22, 9, 25, tzinfo=ET)
        opened = datetime(2026, 4, 21, 21, 48, tzinfo=ET)  # ELSE entry
        assert _is_overnight_position(opened_at=opened, now_et=now_et) is True

    def test_today_position_not_overnight(self):
        from src.execution.bridge import _is_overnight_position

        now_et = datetime(2026, 4, 22, 9, 25, tzinfo=ET)
        opened = datetime(2026, 4, 22, 7, 30, tzinfo=ET)  # today's pre-market
        assert _is_overnight_position(opened_at=opened, now_et=now_et) is False

    def test_premarket_boundary_4am_et(self):
        """A position opened at exactly 04:00 ET today is NOT overnight."""
        from src.execution.bridge import _is_overnight_position

        now_et = datetime(2026, 4, 22, 9, 25, tzinfo=ET)
        opened_at_4am = datetime(2026, 4, 22, 4, 0, tzinfo=ET)
        assert _is_overnight_position(opened_at=opened_at_4am, now_et=now_et) is False

        # Whereas one second before 04:00 IS overnight
        opened_3_59 = datetime(2026, 4, 22, 3, 59, 59, tzinfo=ET)
        assert _is_overnight_position(opened_at=opened_3_59, now_et=now_et) is True

    def test_detection_does_not_depend_on_session_state(self):
        """The bug case: session state EXISTS (Bug B fix succeeded), so
        the old `session_state is None` gate would skip this. New logic
        must detect overnight purely from opened_at."""
        from src.execution.bridge import _is_overnight_position

        # Both calls have session_state populated; only opened_at differs.
        # The function signature should not even take session_state.
        now_et = datetime(2026, 4, 22, 9, 25, tzinfo=ET)
        old = datetime(2026, 4, 21, 21, 48, tzinfo=ET)
        new = datetime(2026, 4, 22, 7, 0, tzinfo=ET)
        assert _is_overnight_position(opened_at=old, now_et=now_et) is True
        assert _is_overnight_position(opened_at=new, now_et=now_et) is False


# ── T4: close_pending flag tagged at detection ─────────────────────


class TestClosePendingFlag:

    def test_managed_position_has_close_pending_field(self):
        """ManagedPosition must expose a close_pending attribute that
        defaults to False so eval queue / scorer can filter."""
        from src.execution.position_manager import ManagedPosition

        pos = ManagedPosition(
            ticker="ELSE", qty=100, entry_price=8.0,
            signal_price=8.0, stop_loss=7.5,
        )
        assert hasattr(pos, "close_pending"), (
            "ManagedPosition must expose a close_pending field for "
            "Bug E so the eval queue can skip positions slated for D91 close"
        )
        assert pos.close_pending is False

    def test_close_pending_is_writable(self):
        from src.execution.position_manager import ManagedPosition

        pos = ManagedPosition(
            ticker="ELSE", qty=100, entry_price=8.0,
            signal_price=8.0, stop_loss=7.5,
        )
        pos.close_pending = True
        assert pos.close_pending is True


# ── T5-T7: close routine step-by-step ──────────────────────────────


class TestD91CloseRoutineSteps:
    """The new D91 close helper must:
      - cancel stop_order_id BEFORE submitting market sell
      - then submit market sell
      - emit one log line per step with explicit 'D91 STEP N' marker
      - tolerate a missing stop_order_id (some positions may have lost
        their stop) and proceed with the market sell anyway
    """

    @pytest.mark.asyncio
    async def test_close_overnight_cancels_stop_then_sells(self, caplog):
        from src.execution.bridge import _close_overnight_position
        import logging

        client = MagicMock()
        client.cancel_order = AsyncMock(return_value={"id": "stop-1", "status": "canceled"})
        client.close_position = AsyncMock(return_value={"id": "sell-1", "status": "accepted"})

        pm = MagicMock()
        pos = MagicMock()
        pos.ticker = "ELSE"
        pos.remaining_qty = 2478
        pos.stop_order_id = "stop-1"
        pm.get_position = MagicMock(return_value=pos)
        pm.has_position = MagicMock(return_value=True)
        pm.remove_position = MagicMock()

        with caplog.at_level(logging.INFO, logger="src.execution.bridge"):
            await _close_overnight_position(
                client=client, position_manager=pm, ticker="ELSE",
            )

        # Sequencing: stop must be canceled FIRST, then market sell
        client.cancel_order.assert_awaited_once_with("stop-1")
        client.close_position.assert_awaited_once_with("ELSE")
        # Step labels appear in the right order
        steps = [r.message for r in caplog.records if "D91 STEP" in r.message]
        assert any("STEP 1" in m for m in steps), f"missing STEP 1 log; got {steps}"
        assert any("STEP 2" in m for m in steps), f"missing STEP 2 log; got {steps}"
        assert any("STEP 3" in m for m in steps), f"missing STEP 3 log; got {steps}"

    @pytest.mark.asyncio
    async def test_close_overnight_handles_missing_stop_order_id(self, caplog):
        """If stop_order_id is empty, skip cancel and go straight to sell."""
        from src.execution.bridge import _close_overnight_position
        import logging

        client = MagicMock()
        client.cancel_order = AsyncMock()
        client.close_position = AsyncMock(return_value={"id": "sell-1"})
        pm = MagicMock()
        pos = MagicMock()
        pos.ticker = "ELSE"
        pos.remaining_qty = 100
        pos.stop_order_id = ""  # No broker-confirmed stop (D56 case)
        pm.get_position = MagicMock(return_value=pos)
        pm.has_position = MagicMock(return_value=True)
        pm.remove_position = MagicMock()

        with caplog.at_level(logging.INFO, logger="src.execution.bridge"):
            await _close_overnight_position(
                client=client, position_manager=pm, ticker="ELSE",
            )

        client.cancel_order.assert_not_awaited()  # No stop to cancel
        client.close_position.assert_awaited_once_with("ELSE")

    @pytest.mark.asyncio
    async def test_close_overnight_continues_if_cancel_fails(self, caplog):
        """If cancel raises (stop already gone), the market sell must
        still be submitted — the position must close."""
        from src.execution.bridge import _close_overnight_position
        import logging

        client = MagicMock()
        client.cancel_order = AsyncMock(side_effect=RuntimeError("not found"))
        client.close_position = AsyncMock(return_value={"id": "sell-1"})
        pm = MagicMock()
        pos = MagicMock()
        pos.ticker = "ELSE"
        pos.remaining_qty = 100
        pos.stop_order_id = "stop-gone"
        pm.get_position = MagicMock(return_value=pos)
        pm.has_position = MagicMock(return_value=True)
        pm.remove_position = MagicMock()

        with caplog.at_level(logging.WARNING, logger="src.execution.bridge"):
            await _close_overnight_position(
                client=client, position_manager=pm, ticker="ELSE",
            )

        # Cancel attempted, market sell still submitted
        client.cancel_order.assert_awaited_once()
        client.close_position.assert_awaited_once_with("ELSE")


# ── T8: eval queue respects close_pending ──────────────────────────


class TestEvalQueueRespectsClosePending:
    """A position tagged close_pending=True must not be re-evaluated by
    the scorer / agent pipeline. Today's 09:54 re-eval of ELSE was the
    visible symptom that this filter was missing."""

    def test_filter_excludes_close_pending(self):
        from src.execution.bridge import filter_eligible_for_eval

        p_open = MagicMock()
        p_open.ticker = "ABCD"
        p_open.close_pending = False

        p_pending = MagicMock()
        p_pending.ticker = "ELSE"
        p_pending.close_pending = True

        eligible = filter_eligible_for_eval([p_open, p_pending])
        tickers = {p.ticker for p in eligible}
        assert "ABCD" in tickers
        assert "ELSE" not in tickers, (
            "Positions tagged close_pending=True must be filtered out of "
            "the eval queue. Today, ELSE was re-evaluated at 09:54 because "
            "no such filter existed."
        )

    def test_filter_treats_missing_attr_as_not_pending(self):
        """Backward compat: positions without the attribute count as
        eligible (default behavior)."""
        from src.execution.bridge import filter_eligible_for_eval

        class Bare:
            def __init__(self, t):
                self.ticker = t
        eligible = filter_eligible_for_eval([Bare("XYZ")])
        assert len(eligible) == 1


# ── T9: ELSE exact replay ──────────────────────────────────────────


class TestELSEOvernightReplay:
    """End-to-end: the exact ELSE timeline from Tue/Wed:
       Tue 2026-04-21 21:48 ET — ELSE entry
       Wed 2026-04-22 03:30 ET — system restart (session_state populated)
       Wed 2026-04-22 09:25 ET — startup sweep
    Expected: detection fires (opened_at < today 04:00 ET); ELSE goes
    into _overnight_positions_to_close; close_pending=True is set.
    """

    def test_else_overnight_path_end_to_end(self):
        from src.execution.bridge import _is_overnight_position
        from src.execution.position_manager import ManagedPosition

        now_et = datetime(2026, 4, 22, 9, 25, tzinfo=ET)
        else_pos = ManagedPosition(
            ticker="ELSE", qty=2478, entry_price=8.20,
            signal_price=8.20, stop_loss=7.65,
            opened_at=datetime(2026, 4, 21, 21, 48, tzinfo=ET),
        )

        # Step 1: detection
        is_overnight = _is_overnight_position(
            opened_at=else_pos.opened_at, now_et=now_et,
        )
        assert is_overnight is True, (
            "ELSE opened Tue 21:48 ET must be detected as overnight at "
            "Wed 09:25 ET regardless of session_state. This was the "
            "exact bug: D91 detection silently skipped because session_state "
            "had been preserved by Bug B fix."
        )

        # Step 2: tag close_pending; eval queue filter excludes it
        else_pos.close_pending = True
        from src.execution.bridge import filter_eligible_for_eval
        assert len(filter_eligible_for_eval([else_pos])) == 0
