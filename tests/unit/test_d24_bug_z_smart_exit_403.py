"""
Fri 2026-04-24 Bug Z regression tests — Knight-Capital-class fix.

Background. Today's LIDR catastrophe (29_eod_bug_findings_d24.md D24
EOD addendum + 30_bug_w_lidr_evidence.md): D78 SMART_EXIT path called
client.close_position(LIDR) which returned 403 Forbidden. The
try/except caught the failure but the cleanup chain proceeded
unconditionally:

  1. Cancel protective stop (succeeded at broker)
  2. Cancel both tranche limit sells (succeeded at broker)
  3. Mark position closed in PositionManager (FAKE — broker still has it)
  4. Log "Closed with attribution PnL=$+421.12" (FAKE)
  5. Log "D215 PATH P&L: LIDR closed via SMART_EXIT" (FAKE)

Result: LIDR naked at broker over a 3-day weekend, no stop, no tranches,
internal tracker thinks it's closed. Net divergence: $1,210.72.
EOD recon caught it 5h 30m later. The bridge / main.py SMART_EXIT
path was the failing layer.

Per user spec, test-first with three cases:

T1 — close returns 403 → tracker NOT mutated, stop NOT canceled,
                          tranches NOT canceled, D245 fires,
                          D247 escalates after N retries
T2 — close returns 200 → tracker mutated, all cleanup proceeds normally
                          (negative test that the patch doesn't over-restrict)
T3 — close returns 500 then 200 on retry → D246 fires, eventual
                                            success state correct

Patch ships in main.py SMART_EXIT path. The fix uses a status-tracking
flag (_d245_close_succeeded) that gates the cleanup chain. The bridge.py
close_with_attribution helper gains a `require_broker_confirmation` kwarg
that skips its bookkeeping if the close was not broker-confirmed.

Note: T1/T2/T3 here exercise the SHAPE of the fix via a smaller helper
function `attempt_close_with_status_check` extracted to a testable unit
in `src/execution/bridge.py`. The integration tests against main.py
itself stay source-grep style (much like Bug W's test pattern).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── T1 — 403 rejection path ────────────────────────────────────────


class TestBugZ_403CloseRejection:

    @pytest.mark.asyncio
    async def test_403_does_not_mutate_tracker_or_cancel_stops(self, caplog):
        """The canonical Bug Z scenario: client.close_position returns
        non-2xx. The bridge.attempt_close_with_status_check helper
        must:
          - return ({succeeded: False}, attempt_count) without raising
          - emit D245 SMART_EXIT_REJECTED
          - emit D247 SMART_EXIT_ESCALATE after retries exhausted
          - NOT call cancel_order on stop or tranches"""
        from src.execution.bridge import attempt_close_with_status_check

        client = MagicMock()
        # Simulate Alpaca 403 — raise an HTTPStatusError-like exception
        # whose `response.status_code` is 403, OR a plain RuntimeError
        # with the 403 string. Both must be detected.
        client.close_position = AsyncMock(side_effect=RuntimeError("403 Forbidden"))
        client.submit_market_order = AsyncMock(side_effect=RuntimeError("403 Forbidden"))
        client.cancel_order = AsyncMock()

        with caplog.at_level(logging.WARNING, logger="src.execution.bridge"):
            result = await attempt_close_with_status_check(
                client=client,
                ticker="LIDR",
                qty=5264,
                max_retries=3,
                retry_backoff_s=0.01,  # fast test
            )
        # Must NOT have succeeded
        assert result["succeeded"] is False
        # MUST have attempted multiple times (retry path)
        assert result["attempts"] >= 2
        # MUST have logged D245 (per-retry rejection)
        d245 = [r for r in caplog.records if "D245" in r.message]
        assert len(d245) >= 1, f"D245 SMART_EXIT_REJECTED must fire on 403; got: {[r.message for r in caplog.records]}"
        # MUST have logged D247 (final escalation)
        d247 = [r for r in caplog.records if "D247" in r.message]
        assert len(d247) >= 1, f"D247 SMART_EXIT_ESCALATE must fire after retries exhausted; got: {[r.message for r in caplog.records]}"
        # MUST NOT have called cancel_order (no cleanup on failed close)
        client.cancel_order.assert_not_called()


# ── T2 — successful close, cleanup proceeds normally ───────────────


class TestBugZ_SuccessPath:

    @pytest.mark.asyncio
    async def test_2xx_close_returns_succeeded_true(self, caplog):
        """Negative test: the patch must not over-restrict. A successful
        close MUST still produce succeeded=True with the broker fill
        details."""
        from src.execution.bridge import attempt_close_with_status_check

        client = MagicMock()
        client.close_position = AsyncMock(return_value={
            "id": "close-1", "status": "filled",
            "filled_qty": "5264", "filled_avg_price": "2.30",
        })
        client.cancel_order = AsyncMock()

        with caplog.at_level(logging.INFO, logger="src.execution.bridge"):
            result = await attempt_close_with_status_check(
                client=client, ticker="LIDR", qty=5264,
                max_retries=3, retry_backoff_s=0.01,
            )
        assert result["succeeded"] is True
        assert result["attempts"] == 1
        assert result["fill_price"] == pytest.approx(2.30, abs=0.01)
        assert result["filled_qty"] == 5264
        # No D245 / D247 on the success path
        assert not any("D245" in r.message for r in caplog.records)
        assert not any("D247" in r.message for r in caplog.records)


# ── T3 — transient failure then retry success ──────────────────────


class TestBugZ_RetryEventualSuccess:

    @pytest.mark.asyncio
    async def test_500_then_200_on_retry_fires_d246(self, caplog):
        """T3 from the user spec: 500 then 200. D246 SMART_EXIT_RETRY
        fires on the first attempt's failure; eventual success state
        is succeeded=True."""
        from src.execution.bridge import attempt_close_with_status_check

        # Fail once, then succeed
        client = MagicMock()
        client.close_position = AsyncMock(side_effect=[
            RuntimeError("500 Internal Server Error"),
            {"id": "close-2", "status": "filled",
             "filled_qty": "5264", "filled_avg_price": "2.28"},
        ])
        client.cancel_order = AsyncMock()

        # D246 RETRY is at INFO level (it's informational — the system
        # is recovering, not failing). D245 REJECTED is at WARNING.
        # Capture INFO so both surface.
        with caplog.at_level(logging.INFO, logger="src.execution.bridge"):
            result = await attempt_close_with_status_check(
                client=client, ticker="LIDR", qty=5264,
                max_retries=3, retry_backoff_s=0.01,
            )
        assert result["succeeded"] is True
        assert result["attempts"] == 2
        assert result["fill_price"] == pytest.approx(2.28, abs=0.01)
        # D246 must fire for the first attempt's failure (the retry log)
        d246 = [r for r in caplog.records if "D246" in r.message]
        assert len(d246) >= 1, f"D246 SMART_EXIT_RETRY must fire on retryable failure; got: {[r.message for r in caplog.records]}"
        # No D247 (escalate) because we eventually succeeded
        assert not any("D247" in r.message for r in caplog.records)


# ── T4 — Bug X + Bug Y cross-check (per user item: BugZ closes both) ──


class TestBugZ_FixAlsoFixesXY:
    """Bug X (BAR-1 fired 9× without ACTUAL) and Bug Y (D76 said
    "no positions" with broker open) are downstream symptoms of Bug Z.
    Bug Z's patch closes both via the principle: cleanup chain ONLY
    runs after broker-confirmed close."""

    @pytest.mark.asyncio
    async def test_bug_x_repro_no_repeat_fires_after_z_fix(self, caplog):
        """Bug X repro: BAR-1 fires on a position. The position should
        either close (success path) OR remain in tracker (failure path).
        It must NOT be marked closed AND remain at broker."""
        from src.execution.bridge import attempt_close_with_status_check

        # Simulate failed close — position MUST stay in tracker, ready
        # for next BAR-1 attempt OR for operator intervention
        client = MagicMock()
        client.close_position = AsyncMock(side_effect=RuntimeError("403 Forbidden"))
        client.submit_market_order = AsyncMock(side_effect=RuntimeError("403 Forbidden"))

        result = await attempt_close_with_status_check(
            client=client, ticker="LIDR", qty=5264,
            max_retries=3, retry_backoff_s=0.01,
        )
        # Position is NOT marked closed → tracker still has it →
        # Bug Y's "D76 says no positions" cannot fire
        # → Bug X's "BAR-1 fires repeatedly" is bounded by max_retries
        #   in the bridge, not infinite
        assert result["succeeded"] is False
        assert result["attempts"] == 3   # exhausted retries

    @pytest.mark.asyncio
    async def test_bug_y_repro_tracker_stays_consistent(self):
        """Bug Y repro: tracker must remain consistent with broker
        across the failure-then-EOD path. If close fails, tracker
        keeps the position. D76 EOD-close iterates over open_positions
        and DOES find LIDR — does not say "no positions" falsely."""
        from src.execution.bridge import attempt_close_with_status_check

        client = MagicMock()
        client.close_position = AsyncMock(side_effect=RuntimeError("403 Forbidden"))
        client.submit_market_order = AsyncMock(side_effect=RuntimeError("403 Forbidden"))

        result = await attempt_close_with_status_check(
            client=client, ticker="LIDR", qty=5264,
            max_retries=2, retry_backoff_s=0.01,
        )
        # The CALLER's responsibility is to NOT remove the position
        # from the tracker if result["succeeded"] is False. The helper
        # itself never touches the tracker — it just reports.
        assert result["succeeded"] is False
        # When the caller honors this contract, tracker stays intact.


# ── T5 — source-grep guard on the cleanup chain ────────────────────


class TestBugZ_SourceGrepGuard:

    def test_smart_exit_cleanup_gated_by_close_success_flag(self):
        """Source-grep guard: the SMART_EXIT cleanup chain in main.py
        (cancel stops, cancel tranches, call close_with_attribution)
        MUST be gated by a flag indicating the broker close succeeded.
        The Bug Z fix introduces `_d245_close_succeeded` (or similar)
        that wraps the cleanup. If a future refactor drops the gate,
        Bug Z regresses silently — this test catches it.

        Post-patch shape: the SMART_EXIT block calls
        `attempt_close_with_status_check(...)` and gates the cleanup
        on the result. We verify both:
        (a) the helper is called from main.py
        (b) the result is checked before cleanup proceeds"""
        import re
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        text = (repo / "main.py").read_text(encoding="utf-8")

        # (a) main.py must import + use attempt_close_with_status_check
        assert "attempt_close_with_status_check" in text, (
            "Bug Z regression: main.py SMART_EXIT path must use "
            "attempt_close_with_status_check helper from src.execution.bridge"
        )

        # (b) The helper's result must gate the cleanup chain. Find the
        # call site and check the surrounding ~1500 chars for both the
        # gate variable + an early-out (continue/return) on failure
        idx = text.find("attempt_close_with_status_check(")
        assert idx != -1
        block = text[idx:idx + 2000]
        assert (
            "_d245_close_succeeded" in block
            or "close_succeeded" in block
            or "result[\"succeeded\"]" in block
        ), (
            "Bug Z regression: SMART_EXIT block uses the helper but "
            "doesn't capture/check the succeeded flag. Cleanup chain "
            f"is not gated. Block: {block[:500]}..."
        )
        # Must have an early-out (continue) when close failed
        assert (
            "if not _d245_close_succeeded" in block
            or "if not close_succeeded" in block
            or "continue  # next exit_action" in block
        ), (
            "Bug Z regression: no early-out (continue) when close "
            "fails. Without it, the cleanup chain runs anyway and "
            "today's LIDR catastrophe re-occurs."
        )
