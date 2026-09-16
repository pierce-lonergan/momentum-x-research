"""
Tests for D108 WS0: Safety-Critical Hardening.

Covers:
  - Stop resubmission retry loop with exponential backoff
  - State file backup rotation + fallback load
  - State diff logging
  - Post-merge sanity assertions
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.execution.stop_resubmitter import StopResubmitter
from src.execution.session_state import SessionStateManager, SessionState, PositionState
from src.execution.position_manager import PositionManager, ManagedPosition


# ── Stop Resubmission Retry Tests ──────────────────────────────


class TestStopResubmitRetry:
    """D108: Tests for retry loop with exponential backoff."""

    @pytest.mark.asyncio
    async def test_retries_on_failure_then_succeeds(self):
        """Mock client fails twice then succeeds — 3 attempts, final success."""
        client = AsyncMock()
        client.cancel_order = AsyncMock(return_value={"status": "canceled"})

        # Fail twice, succeed on third
        call_count = {"n": 0}

        async def flaky_submit(**kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise ConnectionError(f"Attempt {call_count['n']} failed")
            return {"id": "new-stop-ok"}

        client.submit_stop_order = flaky_submit

        resubmitter = StopResubmitter(client=client)
        resubmitter.register_stop("TEST", order_id="old-stop", stop_price=10.0, qty=100)

        # Override backoff for fast testing
        StopResubmitter._BACKOFF_BASE_S = 0.01

        result = await resubmitter.resubmit("TEST", new_stop_price=11.0)

        assert result.success is True
        assert result.new_order_id == "new-stop-ok"
        assert call_count["n"] == 3

        # Restore
        StopResubmitter._BACKOFF_BASE_S = 1.0

    @pytest.mark.asyncio
    async def test_exhausts_retries(self):
        """Mock client fails all 3 times — failure with CRITICAL."""
        client = AsyncMock()
        client.cancel_order = AsyncMock(return_value={"status": "canceled"})
        client.submit_stop_order = AsyncMock(side_effect=ConnectionError("broker down"))

        resubmitter = StopResubmitter(client=client)
        resubmitter.register_stop("FAIL", order_id="old-stop", stop_price=10.0, qty=50)

        StopResubmitter._BACKOFF_BASE_S = 0.01

        result = await resubmitter.resubmit("FAIL", new_stop_price=11.0)

        assert result.success is False
        assert "CRITICAL" in result.error
        assert "3 retries" in result.error
        # D150: 3 retries + 1 emergency fallback attempt = 4 calls
        assert client.submit_stop_order.call_count == 4

        StopResubmitter._BACKOFF_BASE_S = 1.0


# ── State File Backup Tests ────────────────────────────────────


class TestStateBackup:
    """D108: Tests for .bak file rotation and fallback load."""

    def test_backup_created_on_save(self, tmp_path):
        """Save twice — .bak exists with first save's content."""
        mgr = SessionStateManager(state_dir=tmp_path)

        # First save — no backup yet (no prior file)
        mgr.update_position("AAPL", qty=100, entry_price=150.0)
        mgr.save()
        assert mgr.file_path.exists()
        first_content = mgr.file_path.read_text()

        # Second save — backup should now contain first save
        mgr.update_position("AAPL", stop_loss=145.0)
        mgr.save()

        bak_path = Path(str(mgr.file_path) + ".bak")
        assert bak_path.exists()
        bak_content = bak_path.read_text()
        bak_data = json.loads(bak_content)
        # Backup should have AAPL but without stop_loss update
        assert "AAPL" in bak_data["positions"]
        assert bak_data["positions"]["AAPL"]["stop_loss"] == 0.0

    def test_backup_fallback_on_corrupt_primary(self, tmp_path):
        """Corrupt primary + valid .bak → loads from backup."""
        mgr = SessionStateManager(state_dir=tmp_path)
        mgr.update_position("MSFT", qty=50, entry_price=300.0)
        mgr.save()

        # Save again so .bak is created
        mgr.update_position("MSFT", stop_loss=290.0)
        mgr.save()

        # Now corrupt the primary file
        mgr.file_path.write_text("{INVALID JSON!!!", encoding="utf-8")

        # Load should fall back to .bak
        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()

        assert state is not None
        assert "MSFT" in state.positions


# ── State Diff Logging Tests ───────────────────────────────────


class TestStateDiffLogging:
    """D108: Tests for state diff logging on save."""

    def test_diff_logged_on_change(self, tmp_path, caplog):
        """Changing a field logs the diff at DEBUG level."""
        mgr = SessionStateManager(state_dir=tmp_path)
        mgr.update_position("XYZ", qty=100, entry_price=10.0)
        mgr.save()  # First save — no diff (empty _last_saved_dict)

        with caplog.at_level(logging.DEBUG):
            mgr.update_daily_pnl(-250.0)
            mgr.save()

        assert any("D108 state change" in msg for msg in caplog.messages)


# ── Post-Merge Sanity Assertions ───────────────────────────────


class TestPostMergeValidation:
    """D108: Tests for _validate_recovered_positions()."""

    def _make_pm(self) -> PositionManager:
        from config.settings import ExecutionConfig
        return PositionManager(config=ExecutionConfig(), starting_equity=100_000.0)

    def test_clamps_negative_remaining_qty(self, caplog):
        """Position with remaining_qty=-1 → clamped to 0."""
        pm = self._make_pm()
        pos = ManagedPosition(
            ticker="BAD", qty=100, entry_price=10.0,
            signal_price=10.0, stop_loss=9.0,
            remaining_qty=-5,
        )
        pm._positions["BAD"] = pos

        with caplog.at_level(logging.WARNING):
            pm._validate_recovered_positions()

        assert pos.remaining_qty == 0
        assert any("remaining_qty=-5 < 0" in msg for msg in caplog.messages)

    def test_warns_stop_above_entry(self, caplog):
        """Position with stop > entry → WARNING logged (not auto-corrected)."""
        pm = self._make_pm()
        pos = ManagedPosition(
            ticker="HIGH", qty=50, entry_price=10.0,
            signal_price=10.0, stop_loss=12.0,
            target_prices=[11.0, 12.0, 13.0],
        )
        pm._positions["HIGH"] = pos

        with caplog.at_level(logging.WARNING):
            pm._validate_recovered_positions()

        # Should warn but not change stop
        assert pos.stop_loss == 12.0
        assert any("stop_loss=$12.00 >= entry_price=$10.00" in msg for msg in caplog.messages)

    def test_clamps_tranches_out_of_range(self):
        """tranches_filled > 3 → clamped to 3."""
        pm = self._make_pm()
        pos = ManagedPosition(
            ticker="OVER", qty=100, entry_price=10.0,
            signal_price=10.0, stop_loss=9.0,
            tranches_filled=5,
            target_prices=[11.0, 12.0, 13.0],
        )
        pm._positions["OVER"] = pos
        pm._validate_recovered_positions()
        assert pos.tranches_filled == 3
