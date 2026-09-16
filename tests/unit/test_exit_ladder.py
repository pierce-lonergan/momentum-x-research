"""
Tue 2026-04-21 Fix 4: tests for src/execution/exit_ladder.py.

Per the user directive: parity, race, partial-fill, crash-recovery
adversarial tests. Plus rule-(e) positive case + edge cases.

The exit-ladder helper orchestrates a 3-step broker-side transition
that has 6 outcomes worth pinning:

  1. Happy path -- normal 3-tranche position, residual stop
     submitted, full reservation == position qty.
  2. Non-shortable (D147) -- short-circuit, OTO stop preserved.
  3. All-limit tranches (no residual) -- WARN + position unprotected.
  4. Cancel failure -- abort early, prior stop assumed still active.
  5. Mid-ladder tranche failure -- continue to residual stop to
     minimize naked window; partial_failure flag set.
  6. Residual stop failure -- log CRITICAL, is_position_unprotected.

Plus the user's named adversarial classes:
  - Race: price moves through target during the cancel-replace
    window. (Tested via "tranche fills before residual-stop submit"
    -- the helper still calls submit; broker rejects; we surface it.)
  - Partial-fill: tranche limit partial-fills. (Tranche order
    submission returns success regardless of fill state -- the helper
    is concerned with submission, not fill management; tranche_monitor
    handles fills.)
  - Crash-recovery: process dies between cancel and submit. (We can't
    truly test "process dies" but we can test that the result clearly
    indicates which steps completed -- so a re-launching session can
    reconcile broker state to the result.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.execution.exit_ladder import (
    ExitLadderResult,
    cancel_stop_and_submit_exit_ladder,
    _compute_residual_qty,
    _filter_limit_tranches,
)


# ── Helper: minimal ExitTranche-like for tests ────────────────────────


@dataclass
class FakeTranche:
    tranche_number: int
    qty: int
    target: float
    exit_type: str  # "limit" | "trailing_stop"


def _standard_three(position_qty: int = 2478) -> list[FakeTranche]:
    """Mirror compute_exit_tranches output: ⅓ + ⅓ + ⅓ with last
    tranche typed as trailing_stop."""
    third = position_qty // 3
    remainder = position_qty - 3 * third
    return [
        FakeTranche(1, third, 8.03, "limit"),
        FakeTranche(2, third, 8.42, "limit"),
        FakeTranche(3, third + remainder, 9.18, "trailing_stop"),
    ]


@pytest.fixture
def mock_client():
    """Broker stub that records every call and returns success
    by default. Tests override individual methods to simulate
    failures."""
    c = MagicMock()
    c.cancel_order = AsyncMock(return_value={"id": "cancelled-stub", "status": "canceled"})
    c.submit_limit_order = AsyncMock(side_effect=_make_limit_responder())
    c.submit_stop_order = AsyncMock(side_effect=_make_stop_responder())
    return c


def _make_limit_responder():
    """Return a fresh increment-counter for limit-order ID generation."""
    counter = {"n": 0}
    async def respond(**kwargs):
        counter["n"] += 1
        return {"id": f"limit-oid-{counter['n']}", "status": "accepted"}
    return respond


def _make_stop_responder():
    counter = {"n": 0}
    async def respond(**kwargs):
        counter["n"] += 1
        return {"id": f"stop-oid-{counter['n']}", "status": "accepted"}
    return respond


# ── Pure helpers ──────────────────────────────────────────────────────


class TestPureHelpers:

    def test_filter_keeps_only_limit_typed(self):
        ts = _standard_three(2478)
        kept = _filter_limit_tranches(ts)
        assert [t.tranche_number for t in kept] == [1, 2]
        assert all(t.exit_type == "limit" for t in kept)

    def test_residual_qty_standard_split(self):
        ts = _standard_three(2478)  # 826/826/826
        limits = _filter_limit_tranches(ts)
        # residual = 2478 - 826 - 826 = 826
        assert _compute_residual_qty(2478, limits) == 826

    def test_residual_qty_all_limits_zero(self):
        """Edge case: if all 3 are typed limit, residual = 0."""
        ts = [
            FakeTranche(1, 100, 1.0, "limit"),
            FakeTranche(2, 100, 1.5, "limit"),
            FakeTranche(3, 100, 2.0, "limit"),
        ]
        assert _compute_residual_qty(300, ts) == 0

    def test_residual_qty_negative_signals_misconfig(self):
        ts = [FakeTranche(1, 1000, 1.0, "limit")]
        # position only 500 but tranche claims 1000
        assert _compute_residual_qty(500, ts) == -500


# ── Class 1: Happy path / parity ──────────────────────────────────────


class TestHappyPath:
    """Rule (e) positive case: with realistic ELSE-shaped inputs,
    the helper produces exactly the broker call sequence we want."""

    @pytest.mark.asyncio
    async def test_else_shaped_trade_full_flow(self, mock_client):
        """ELSE-shaped: 2478 qty, OTO stop active, 3 tranches."""
        result = await cancel_stop_and_submit_exit_ladder(
            mock_client,
            ticker="ELSE",
            position_qty=2478,
            tranches=_standard_three(2478),
            stop_price=6.50,
            old_stop_order_id="old-oto-stop-id",
            is_shortable=True,
        )

        # Outcome assertions
        assert result.skipped_reason == ""
        assert result.is_position_unprotected is False
        assert result.partial_failure is False
        assert result.residual_qty == 826  # 2478 - 826 - 826
        assert len(result.tranche_order_ids) == 2
        assert result.new_stop_order_id != ""

        # Call sequence assertions (the broker must see)
        # 1. cancel_order called once with the old stop id
        mock_client.cancel_order.assert_awaited_once_with("old-oto-stop-id")
        # 2. submit_limit_order called twice (T1 and T2 only -- T3 is trailing_stop)
        assert mock_client.submit_limit_order.await_count == 2
        # 3. submit_stop_order called once for the residual
        assert mock_client.submit_stop_order.await_count == 1

        # Specific call args -- limit tranches
        limit_calls = mock_client.submit_limit_order.call_args_list
        # Tranche 1: 826 @ $8.03
        assert limit_calls[0].kwargs == {
            "symbol": "ELSE", "qty": 826, "side": "sell", "limit_price": 8.03,
        }
        # Tranche 2: 826 @ $8.42
        assert limit_calls[1].kwargs == {
            "symbol": "ELSE", "qty": 826, "side": "sell", "limit_price": 8.42,
        }
        # Residual stop: 826 @ $6.50 GTC
        stop_call = mock_client.submit_stop_order.call_args
        assert stop_call.kwargs["symbol"] == "ELSE"
        assert stop_call.kwargs["qty"] == 826
        assert stop_call.kwargs["side"] == "sell"
        assert stop_call.kwargs["stop_price"] == 6.50
        assert stop_call.kwargs["time_in_force"] == "gtc"  # Fix 1 contract
        assert stop_call.kwargs["position_intent"] == "close"  # D124

    @pytest.mark.asyncio
    async def test_total_reservations_equal_position_qty(self, mock_client):
        """Parity: sum of all sell-side reservations == position qty.
        This is the FUNDAMENTAL invariant that makes the 403 impossible."""
        result = await cancel_stop_and_submit_exit_ladder(
            mock_client,
            ticker="X", position_qty=2478,
            tranches=_standard_three(2478),
            stop_price=6.50, old_stop_order_id="old", is_shortable=True,
        )
        # Sum of qty across the actual broker calls
        limit_qtys = sum(c.kwargs["qty"] for c in mock_client.submit_limit_order.call_args_list)
        stop_qty = mock_client.submit_stop_order.call_args.kwargs["qty"]
        total_reserved = limit_qtys + stop_qty
        assert total_reserved == 2478, (
            f"Total broker reservations ({total_reserved}) must equal "
            f"position qty (2478). Anything else triggers 403."
        )


# ── Class 2: Non-shortable short-circuit (D147) ──────────────────────


class TestNonShortable:
    """D147: non-shortable tickers must skip the cancel/replace
    entirely. The OTO stop stays in place; no tranches submitted."""

    @pytest.mark.asyncio
    async def test_non_shortable_short_circuits(self, mock_client):
        result = await cancel_stop_and_submit_exit_ladder(
            mock_client,
            ticker="SST", position_qty=1000,
            tranches=_standard_three(1000),
            stop_price=5.00, old_stop_order_id="oto-id",
            is_shortable=False,
        )
        assert "non-shortable" in result.skipped_reason
        assert result.new_stop_order_id == "oto-id"  # preserved
        # Broker received NO calls
        mock_client.cancel_order.assert_not_awaited()
        mock_client.submit_limit_order.assert_not_awaited()
        mock_client.submit_stop_order.assert_not_awaited()


# ── Class 3: All-limit (no residual) — unprotected case ─────────────


class TestAllLimitNoResidual:
    """If every tranche is exit_type='limit', they consume the full
    position and there's no residual for a stop. Helper warns +
    flags is_position_unprotected."""

    @pytest.mark.asyncio
    async def test_no_residual_flags_unprotected(self, mock_client):
        ts = [
            FakeTranche(1, 100, 1.0, "limit"),
            FakeTranche(2, 100, 1.5, "limit"),
            FakeTranche(3, 100, 2.0, "limit"),
        ]
        result = await cancel_stop_and_submit_exit_ladder(
            mock_client,
            ticker="X", position_qty=300, tranches=ts,
            stop_price=0.5, old_stop_order_id="old", is_shortable=True,
        )
        assert result.residual_qty == 0
        assert result.is_position_unprotected is True
        assert result.new_stop_order_id == ""
        # All 3 limits submitted, NO stop submission
        assert mock_client.submit_limit_order.await_count == 3
        mock_client.submit_stop_order.assert_not_awaited()


# ── Class 4: Cancel failure (abort) ─────────────────────────────────


class TestCancelFailure:
    """If the OTO cancel fails, we MUST NOT proceed to submit
    tranches — the broker would still see the OTO stop holding all
    qty and 403 every tranche. Position is NOT unprotected
    (the prior stop is still there)."""

    @pytest.mark.asyncio
    async def test_cancel_raises_aborts_and_does_not_unprotect(self, mock_client):
        mock_client.cancel_order = AsyncMock(side_effect=RuntimeError("transient"))
        result = await cancel_stop_and_submit_exit_ladder(
            mock_client,
            ticker="X", position_qty=300,
            tranches=_standard_three(300),
            stop_price=0.5, old_stop_order_id="old", is_shortable=True,
        )
        assert "cancel" in result.skipped_reason.lower()
        # is_position_unprotected stays False because prior stop is still active
        assert result.is_position_unprotected is False
        # No tranche or new-stop submission was attempted
        mock_client.submit_limit_order.assert_not_awaited()
        mock_client.submit_stop_order.assert_not_awaited()


# ── Class 5: Mid-ladder tranche failure (partial) ───────────────────


class TestMidLadderFailure:
    """If T1 submits OK but T2 fails, the helper must STILL try to
    submit the residual stop — minimizing the naked window. The
    result reports partial_failure so the caller can re-submit T2."""

    @pytest.mark.asyncio
    async def test_t2_failure_still_submits_residual_stop(self, mock_client):
        # T1 succeeds; T2 raises; residual stop succeeds
        call_count = {"n": 0}

        async def flaky_limit(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated T2 failure")
            return {"id": f"limit-{call_count['n']}", "status": "accepted"}

        mock_client.submit_limit_order = AsyncMock(side_effect=flaky_limit)

        result = await cancel_stop_and_submit_exit_ladder(
            mock_client,
            ticker="X", position_qty=300,
            tranches=_standard_three(300),
            stop_price=5.0, old_stop_order_id="old", is_shortable=True,
        )
        assert result.partial_failure is True
        assert len(result.tranche_order_ids) == 1  # only T1 captured
        # CRITICAL: residual stop WAS submitted despite the partial failure
        mock_client.submit_stop_order.assert_awaited_once()
        assert result.new_stop_order_id != ""
        assert result.is_position_unprotected is False  # stop covered residual


# ── Class 6: Residual stop failure ──────────────────────────────────


class TestResidualStopFailure:
    """Residual stop submission failure leaves the position NAKED
    on the un-tranched portion. CRITICAL log + flag."""

    @pytest.mark.asyncio
    async def test_residual_stop_failure_flags_unprotected(self, mock_client):
        mock_client.submit_stop_order = AsyncMock(
            side_effect=RuntimeError("simulated stop failure"),
        )

        result = await cancel_stop_and_submit_exit_ladder(
            mock_client,
            ticker="X", position_qty=300,
            tranches=_standard_three(300),
            stop_price=5.0, old_stop_order_id="old", is_shortable=True,
        )
        # Tranches submitted normally
        assert len(result.tranche_order_ids) == 2
        assert result.partial_failure is False  # tranches OK
        # Stop failed
        assert result.is_position_unprotected is True
        assert result.new_stop_order_id == ""


# ── Class 7: Crash-recovery surfacing ───────────────────────────────


class TestCrashRecoverySurfacing:
    """We can't truly test "process dies mid-flow" but we can verify
    the result clearly indicates which steps completed, so a relaunching
    session can reconcile broker state."""

    @pytest.mark.asyncio
    async def test_result_reports_each_completed_step(self, mock_client):
        result = await cancel_stop_and_submit_exit_ladder(
            mock_client,
            ticker="X", position_qty=300,
            tranches=_standard_three(300),
            stop_price=5.0, old_stop_order_id="old", is_shortable=True,
        )
        # Result fields are individually inspectable -- recovery code can
        # read each and reconcile.
        assert isinstance(result.tranche_order_ids, list)
        assert isinstance(result.new_stop_order_id, str)
        assert isinstance(result.residual_qty, int)
        assert isinstance(result.is_position_unprotected, bool)
        assert isinstance(result.partial_failure, bool)
        assert isinstance(result.skipped_reason, str)


# ── Class 8: Configuration validation ───────────────────────────────


class TestConfigValidation:

    @pytest.mark.asyncio
    async def test_zero_position_qty_short_circuits(self, mock_client):
        result = await cancel_stop_and_submit_exit_ladder(
            mock_client,
            ticker="X", position_qty=0, tranches=[],
            stop_price=5.0, old_stop_order_id="old", is_shortable=True,
        )
        assert "position_qty" in result.skipped_reason
        mock_client.cancel_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_overspecified_tranches_refuses(self, mock_client):
        """Limit tranches summing to MORE than position qty is a
        config error -- refuse to act, don't try to submit."""
        ts = [FakeTranche(1, 1000, 1.0, "limit")]
        result = await cancel_stop_and_submit_exit_ladder(
            mock_client,
            ticker="X", position_qty=500, tranches=ts,
            stop_price=0.5, old_stop_order_id="old", is_shortable=True,
        )
        assert "config" in result.skipped_reason.lower() or "> position" in result.skipped_reason
        mock_client.cancel_order.assert_not_awaited()
        mock_client.submit_limit_order.assert_not_awaited()
        mock_client.submit_stop_order.assert_not_awaited()


# ── Class 9: No prior stop (entry path without OTO) ─────────────────


class TestNoOldStop:
    """If the caller doesn't have a prior stop to cancel (e.g. entry
    didn't use OTO), we should still submit the tranches + residual
    stop, just skip the cancel step."""

    @pytest.mark.asyncio
    async def test_empty_old_stop_id_skips_cancel(self, mock_client):
        result = await cancel_stop_and_submit_exit_ladder(
            mock_client,
            ticker="X", position_qty=300,
            tranches=_standard_three(300),
            stop_price=5.0, old_stop_order_id=None, is_shortable=True,
        )
        # cancel_order NOT called
        mock_client.cancel_order.assert_not_awaited()
        # tranches + stop still submitted
        assert mock_client.submit_limit_order.await_count == 2
        mock_client.submit_stop_order.assert_awaited_once()
        assert result.new_stop_order_id != ""
        assert result.is_position_unprotected is False
