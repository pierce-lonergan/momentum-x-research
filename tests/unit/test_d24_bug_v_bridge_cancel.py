"""
Fri 2026-04-24 Bug V regression tests.

Background. Yesterday (D23) the bridge submitted 5 OTO orders. 4 of
them did NOT reach terminal in the 6-poll budget (12s). The bridge
correctly REJECTED them per the Bug D fix, dropping internal tracking.
But the broker accepted the underlying day-only OTOs, filled them
later in the session, and the OTO stop legs absorbed losses we never
saw. Net equity drop yesterday: -$978.50.

Root cause. `bridge.execute_verdict` rejection branch when terminal
filled_qty <= 0 after max polls: drops internal tracking but does NOT
cancel the order at the broker. Day-tif OTO continues to live, may
fill later, becomes a ghost.

Fix. Add `await self._client.cancel_order(order_result.order_id)` in
the rejection branch BEFORE returning None. Wrap in try/except so
cancel failures don't block the bridge's return path. Emit:
  D237 BRIDGE_CANCEL INFO  on success
  D237 BRIDGE_CANCEL WARN  on cancel failure

T1 — happy path: status=new + filled_qty=0 after max polls →
                 client.cancel_order awaited with the order_id →
                 awaited BEFORE return None
T2 — no rejection path: terminal status=filled, fill_price>0 →
                 cancel NOT called
T3 — cancel itself raises: rejection branch + cancel raises →
                 D237 WARN fires + bridge returns None gracefully
                 (cancel failure must not break the bridge)
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_verdict(ticker, entry, stop, qty=100, target=None):
    """Build a complete TradeVerdict-shape mock with all fields the
    bridge.execute_verdict path reads. Avoids MagicMock-vs-int comparison
    errors in the bridge's pre-execute checks."""
    v = MagicMock()
    v.ticker = ticker
    v.action = "BUY"
    v.entry_price = float(entry)
    v.stop_loss = float(stop)
    v.target_prices = target or [float(entry) * 1.05]
    v.position_size_pct = 0.05      # 5% sizing — non-zero so bridge proceeds
    v.mfcs = 0.50
    v.float_shares = 5_000_000
    v.gap_pct = 0.30
    v.rvol = 5.0
    v.kelly_tier = 1
    v.direction = "long"
    v.catalyst_profile = None
    return v


def _make_scored(ticker):
    """Minimal ScoredCandidate-shape mock so bridge.ManagedPosition
    construction (which references _cand from scored.candidate) doesn't
    crash with UnboundLocalError on `scored=None` happy paths."""
    s = MagicMock()
    s.candidate = MagicMock()
    s.candidate.catalyst_type = "unknown"
    s.candidate.gap_pct = 0.30
    s.candidate.rvol = 5.0
    s.candidate.prior_gap_count = 0
    s.candidate.is_day2_runner = False
    s.agent_signals = []
    return s


# ── T1 — happy path: cancel awaited before return None ─────────────


class TestBugV_CancelOnRejection:

    @pytest.mark.asyncio
    async def test_rejection_calls_cancel_order_with_correct_oid(self, caplog):
        """When _poll_for_terminal_fill returns a non-terminal partial
        with filled_qty=0, the rejection branch must call
        client.cancel_order(order_id) before returning None."""
        from src.execution.bridge import ExecutionBridge

        # Build a verdict + executor mock that submits successfully but
        # whose returned OrderResult has status="new" (not terminal).
        verdict = _make_verdict("AUUD", 6.00, 5.40, target=[6.30, 6.60, 6.90])

        executor_result = MagicMock()
        executor_result.order_id = "auud-test-1"
        executor_result.status = "new"            # not terminal
        executor_result.ticker = "AUUD"
        executor_result.qty = 1978
        executor_result.fill_price = 0.0
        executor_result.signal_price = 6.00
        executor_result.submitted_price = 6.00
        executor_result.stop_order_id = ""

        executor = MagicMock()
        executor.execute = AsyncMock(return_value=executor_result)

        # Client mock: get_orders always returns the order in status=new
        # with filled_qty=0. The poll loop will exhaust 6 attempts and
        # return that partial; the bridge must then reject.
        client = MagicMock()
        client.get_orders = AsyncMock(return_value=[{
            "id": "auud-test-1", "status": "new",
            "filled_qty": "0", "filled_avg_price": None,
        }])
        client.cancel_order = AsyncMock(return_value={"id": "auud-test-1", "status": "canceled"})

        pm = MagicMock()
        pm.can_enter_new_position = MagicMock(return_value=(True, ""))
        pm._scored_cache = {"AUUD": MagicMock()}

        bridge = ExecutionBridge(
            executor=executor,
            position_manager=pm,
            alpaca_client=client,
        )

        with caplog.at_level(logging.INFO, logger="src.execution.bridge"):
            result = await bridge.execute_verdict(
                verdict, scored=None, entry_snapshot=None,
            )

        # Rejection: bridge returned None
        assert result is None, (
            "Bridge must return None when poll budget exhausts with "
            "filled_qty=0 (Bug V fix preserves Bug D behavior)"
        )

        # Bug V: cancel_order MUST have been called with the right oid
        client.cancel_order.assert_awaited_once_with("auud-test-1")

        # D237 INFO log line must have fired with success disposition
        d237_logs = [
            r for r in caplog.records
            if "D237" in r.message and "BRIDGE_CANCEL" in r.message
        ]
        assert len(d237_logs) >= 1, (
            f"Expected D237 BRIDGE_CANCEL log; got: {[r.message for r in caplog.records]}"
        )
        # Must reference the ticker so operators can grep per-position
        assert "AUUD" in d237_logs[0].message

    @pytest.mark.asyncio
    async def test_rejection_cancels_BEFORE_return_None(self):
        """The cancel must complete (or fail) BEFORE the bridge releases
        its rejection. Otherwise the OTO can fill in the window between
        rejection and cancel."""
        from src.execution.bridge import ExecutionBridge

        # Track call order via a side-effect list
        call_order: list[str] = []

        async def cancel_side_effect(oid):
            call_order.append("cancel_order")
            return {"id": oid, "status": "canceled"}

        verdict = _make_verdict("TZOO", 11.68, 9.00, target=[12.0])

        executor_result = MagicMock()
        executor_result.order_id = "tzoo-test-1"
        executor_result.status = "new"
        executor_result.ticker = "TZOO"
        executor_result.qty = 869
        executor_result.fill_price = 0.0
        executor_result.signal_price = 11.68
        executor_result.submitted_price = 11.68
        executor_result.stop_order_id = ""

        executor = MagicMock()
        executor.execute = AsyncMock(return_value=executor_result)

        client = MagicMock()
        client.get_orders = AsyncMock(return_value=[{
            "id": "tzoo-test-1", "status": "new",
            "filled_qty": "0", "filled_avg_price": None,
        }])
        client.cancel_order = AsyncMock(side_effect=cancel_side_effect)

        pm = MagicMock()
        pm.can_enter_new_position = MagicMock(return_value=(True, ""))
        pm._scored_cache = {"TZOO": MagicMock()}
        pm.add_position = MagicMock(side_effect=lambda *a, **kw: call_order.append("add_position"))

        bridge = ExecutionBridge(
            executor=executor,
            position_manager=pm,
            alpaca_client=client,
        )

        result = await bridge.execute_verdict(
            verdict, scored=None, entry_snapshot=None,
        )

        # Bridge rejected
        assert result is None
        # cancel_order WAS called
        assert "cancel_order" in call_order
        # add_position was NOT called (we rejected, no position to track)
        assert "add_position" not in call_order


# ── T2 — happy fill: no cancel ─────────────────────────────────────


class TestBugV_NoCancelOnSuccess:

    @pytest.mark.asyncio
    async def test_successful_terminal_fill_does_not_cancel(self):
        """When the order reaches terminal status=filled with non-zero
        fill_price, the bridge must NOT call cancel_order. The order
        is in the desired state."""
        from src.execution.bridge import ExecutionBridge

        verdict = _make_verdict("XNDU", 32.93, 25.72, target=[34.58, 36.22, 39.52])

        executor_result = MagicMock()
        executor_result.order_id = "xndu-test-1"
        executor_result.status = "filled"
        executor_result.ticker = "XNDU"
        executor_result.qty = 395
        executor_result.fill_price = 30.74
        executor_result.signal_price = 32.93
        executor_result.submitted_price = 32.93
        executor_result.stop_order_id = "stop-1"
        executor_result.actual_stop_price = 32.44

        executor = MagicMock()
        executor.execute = AsyncMock(return_value=executor_result)

        # Skip the poll loop entirely (status already terminal)
        client = MagicMock()
        client.cancel_order = AsyncMock()

        pm = MagicMock()
        pm.can_enter_new_position = MagicMock(return_value=(True, ""))
        pm._scored_cache = {"XNDU": MagicMock()}
        pm.add_position = MagicMock()
        pm.open_positions = []

        bridge = ExecutionBridge(
            executor=executor,
            position_manager=pm,
            alpaca_client=client,
        )

        # Note: this path may exercise the poll loop briefly; that's
        # OK because TERMINAL_SUCCESS_STATES will short-circuit.
        # Pass a minimal scored candidate so ManagedPosition's _cand
        # reference doesn't crash (unrelated pre-existing path bug).
        result = await bridge.execute_verdict(
            verdict, scored=_make_scored("XNDU"), entry_snapshot=None,
        )

        # Bridge accepted the position
        assert result is not None or pm.add_position.called
        # cancel_order MUST NOT have been called
        client.cancel_order.assert_not_awaited()


# ── T3 — cancel itself fails: D237 WARN, still return None ─────────


class TestBugV_CancelFailureGraceful:

    @pytest.mark.asyncio
    async def test_cancel_raises_emits_warning_and_still_returns_none(self, caplog):
        """If client.cancel_order raises (broker outage, order already
        canceled at broker, etc.), the bridge must:
          - emit D237 WARN with the failure reason
          - still return None (rejection completes; no ghost tracking)

        The motivation: cancel failure means the broker MIGHT still fill
        the order. Operator must see the warning and intervene manually.
        Better to surface the failure loudly than to silently double-
        cancel or block the bridge."""
        from src.execution.bridge import ExecutionBridge

        verdict = _make_verdict("CPIX", 4.41, 3.50, target=[4.6])

        executor_result = MagicMock()
        executor_result.order_id = "cpix-test-1"
        executor_result.status = "new"
        executor_result.ticker = "CPIX"
        executor_result.qty = 2887
        executor_result.fill_price = 0.0
        executor_result.signal_price = 4.41
        executor_result.submitted_price = 4.41
        executor_result.stop_order_id = ""

        executor = MagicMock()
        executor.execute = AsyncMock(return_value=executor_result)

        client = MagicMock()
        client.get_orders = AsyncMock(return_value=[{
            "id": "cpix-test-1", "status": "new",
            "filled_qty": "0", "filled_avg_price": None,
        }])
        client.cancel_order = AsyncMock(side_effect=RuntimeError("broker API outage"))

        pm = MagicMock()
        pm.can_enter_new_position = MagicMock(return_value=(True, ""))
        pm._scored_cache = {"CPIX": MagicMock()}

        bridge = ExecutionBridge(
            executor=executor,
            position_manager=pm,
            alpaca_client=client,
        )

        with caplog.at_level(logging.WARNING, logger="src.execution.bridge"):
            result = await bridge.execute_verdict(
                verdict, scored=None, entry_snapshot=None,
            )

        # Bridge MUST still return None (cancel failure does not block)
        assert result is None, (
            "Bug V regression: cancel failure must not prevent the "
            "bridge from completing its rejection. The order may still "
            "fill at the broker, but blocking the bridge is worse."
        )

        # D237 WARN must have fired with the cancel-failure context
        d237_warn = [
            r for r in caplog.records
            if "D237" in r.message
            and r.levelname in ("WARNING", "ERROR")
        ]
        assert len(d237_warn) >= 1, (
            f"Bug V regression: cancel failure must emit a D237 "
            f"WARN (NOT silent). Got: {[(r.levelname, r.message) for r in caplog.records]}"
        )
        # Message must mention the broker failure cause for operator triage
        assert "broker API outage" in d237_warn[0].message or "outage" in d237_warn[0].message.lower()
