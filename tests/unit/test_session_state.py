"""
MOMENTUM-X Tests: Session State Persistence

Node ID: tests.unit.test_session_state
Graph Link: tested_by → execution.session_state

Tests D64 session state save/load/recovery functionality.
"""

from __future__ import annotations

import json
import pytest
from datetime import date, datetime, timezone
from pathlib import Path

from src.execution.session_state import (
    PositionState,
    SessionState,
    SessionStateManager,
)


class TestPositionState:
    """Test PositionState serialization."""

    def test_roundtrip(self):
        pos = PositionState(
            ticker="AAPL",
            qty=99,
            entry_price=150.25,
            signal_price=150.10,
            stop_loss=147.50,
            target_prices=[153.0, 156.0, 160.0],
            tranches_filled=1,
            remaining_qty=66,
            realized_pnl=120.0,
            entry_order_id="abc-123",
            stop_order_id="def-456",
            tranche_order_ids=["t1", "t2", "t3"],
            opened_at="2026-02-10T09:35:00+00:00",
        )
        d = pos.to_dict()
        restored = PositionState.from_dict(d)
        assert restored.ticker == "AAPL"
        assert restored.qty == 99
        assert restored.entry_price == 150.25
        assert restored.tranches_filled == 1
        assert restored.remaining_qty == 66
        assert restored.stop_order_id == "def-456"
        assert len(restored.tranche_order_ids) == 3

    def test_from_dict_defaults(self):
        pos = PositionState.from_dict({"ticker": "TSLA"})
        assert pos.ticker == "TSLA"
        assert pos.qty == 0
        assert pos.tranches_filled == 0
        assert pos.tranche_order_ids == []


class TestSessionState:
    """Test SessionState serialization and staleness."""

    def test_roundtrip(self):
        state = SessionState(
            session_date=date.today().isoformat(),
            daily_realized_pnl=-500.0,
            positions={
                "AAPL": PositionState(ticker="AAPL", qty=100),
            },
        )
        d = state.to_dict()
        restored = SessionState.from_dict(d)
        assert restored.session_date == date.today().isoformat()
        assert restored.daily_realized_pnl == -500.0
        assert "AAPL" in restored.positions
        assert restored.positions["AAPL"].qty == 100

    def test_stale_yesterday(self):
        state = SessionState(session_date="2020-01-01")
        assert state.is_stale()

    def test_not_stale_today(self):
        state = SessionState(session_date=date.today().isoformat())
        assert not state.is_stale()

    def test_stale_empty_date(self):
        state = SessionState(session_date="")
        assert state.is_stale()

    def test_stale_invalid_date(self):
        state = SessionState(session_date="not-a-date")
        assert state.is_stale()


class TestSessionStateManager:
    """Test SessionStateManager persistence."""

    @pytest.fixture
    def state_dir(self, tmp_path: Path) -> Path:
        return tmp_path / "data"

    @pytest.fixture
    def mgr(self, state_dir: Path) -> SessionStateManager:
        return SessionStateManager(state_dir=state_dir)

    def test_save_and_load_roundtrip(self, mgr: SessionStateManager):
        mgr.update_position(
            ticker="BOOM",
            qty=500,
            entry_price=8.50,
            stop_loss=7.90,
            target_prices=[9.35, 10.20, 11.05],
            tranches_filled=1,
            stop_order_id="stop-001",
            tranche_order_ids=["t1", "t2", "t3"],
        )
        mgr.update_daily_pnl(-1200.0)
        mgr.save()

        # Create a new manager pointing to same dir
        mgr2 = SessionStateManager(state_dir=mgr.file_path.parent)
        loaded = mgr2.load()

        assert loaded is not None
        assert loaded.daily_realized_pnl == -1200.0
        assert "BOOM" in loaded.positions
        boom = loaded.positions["BOOM"]
        assert boom.qty == 500
        assert boom.entry_price == 8.50
        assert boom.tranches_filled == 1
        assert boom.stop_order_id == "stop-001"
        assert boom.tranche_order_ids == ["t1", "t2", "t3"]
        assert boom.target_prices == [9.35, 10.20, 11.05]

    def test_missing_file_returns_none(self, mgr: SessionStateManager):
        result = mgr.load()
        assert result is None

    def test_corrupt_json_returns_none(self, mgr: SessionStateManager):
        mgr.file_path.parent.mkdir(parents=True, exist_ok=True)
        mgr.file_path.write_text("{{invalid json}", encoding="utf-8")
        result = mgr.load()
        assert result is None

    def test_stale_state_returns_none(self, mgr: SessionStateManager):
        stale = SessionState(
            session_date="2020-01-01",
            daily_realized_pnl=100.0,
        )
        mgr.file_path.parent.mkdir(parents=True, exist_ok=True)
        mgr.file_path.write_text(
            json.dumps(stale.to_dict()), encoding="utf-8",
        )
        result = mgr.load()
        assert result is None

    def test_update_position_partial(self, mgr: SessionStateManager):
        mgr.update_position(
            ticker="AAPL",
            qty=100,
            entry_price=150.0,
            stop_loss=145.0,
        )
        assert mgr.state.positions["AAPL"].qty == 100
        assert mgr.state.positions["AAPL"].stop_loss == 145.0

        # Partial update
        mgr.update_position(ticker="AAPL", tranches_filled=1, stop_loss=150.0)
        assert mgr.state.positions["AAPL"].qty == 100  # Unchanged
        assert mgr.state.positions["AAPL"].tranches_filled == 1  # Updated
        assert mgr.state.positions["AAPL"].stop_loss == 150.0  # Updated

    def test_remove_position(self, mgr: SessionStateManager):
        mgr.update_position(ticker="AAPL", qty=100)
        assert "AAPL" in mgr.state.positions

        mgr.remove_position("AAPL")
        assert "AAPL" not in mgr.state.positions

    def test_remove_nonexistent_position(self, mgr: SessionStateManager):
        # Should not raise
        mgr.remove_position("DOESNOTEXIST")

    def test_daily_pnl_persisted(self, mgr: SessionStateManager):
        mgr.update_daily_pnl(-3500.0)
        mgr.save()

        mgr2 = SessionStateManager(state_dir=mgr.file_path.parent)
        loaded = mgr2.load()
        assert loaded is not None
        assert loaded.daily_realized_pnl == -3500.0

    def test_atomic_write_creates_no_tmp_file(self, mgr: SessionStateManager):
        mgr.update_position(ticker="AAPL", qty=100)
        mgr.save()

        # .tmp file should not exist after save
        tmp_path = mgr.file_path.with_suffix(".tmp")
        assert not tmp_path.exists()
        # Main file should exist
        assert mgr.file_path.exists()

    def test_set_tranche_order_ids(self, mgr: SessionStateManager):
        mgr.update_position(ticker="BOOM", qty=300)
        mgr.set_tranche_order_ids("BOOM", ["t1", "t2", "t3"])
        assert mgr.state.positions["BOOM"].tranche_order_ids == ["t1", "t2", "t3"]

    def test_set_tranche_order_ids_creates_position(self, mgr: SessionStateManager):
        mgr.set_tranche_order_ids("NEW", ["t1"])
        assert "NEW" in mgr.state.positions
        assert mgr.state.positions["NEW"].tranche_order_ids == ["t1"]

    def test_reset(self, mgr: SessionStateManager):
        mgr.update_position(ticker="AAPL", qty=100)
        mgr.update_daily_pnl(-500.0)
        assert len(mgr.state.positions) == 1

        mgr.reset()
        assert len(mgr.state.positions) == 0
        assert mgr.state.daily_realized_pnl == 0.0
        assert mgr.state.session_date == date.today().isoformat()

    def test_multiple_positions(self, mgr: SessionStateManager):
        mgr.update_position(ticker="AAPL", qty=100, entry_price=150.0)
        mgr.update_position(ticker="TSLA", qty=50, entry_price=200.0)
        mgr.update_position(ticker="BOOM", qty=500, entry_price=8.50)
        mgr.save()

        mgr2 = SessionStateManager(state_dir=mgr.file_path.parent)
        loaded = mgr2.load()
        assert loaded is not None
        assert len(loaded.positions) == 3
        assert loaded.positions["AAPL"].qty == 100
        assert loaded.positions["TSLA"].qty == 50
        assert loaded.positions["BOOM"].qty == 500
