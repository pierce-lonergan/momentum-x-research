"""
D95 Crash Recovery Integration Tests
=====================================

These tests verify that the system can survive crashes and restarts
without losing critical state. Every test simulates a specific crash
scenario and verifies that recovery produces correct behavior.

Scenarios tested:
  1. Stop-out cooldown survives restart
  2. EOD close flag survives restart (prevents triple-fire)
  3. Phase 0 flag survives restart (prevents re-run)
  4. Position recovery from session state + broker
  5. Stale session state (wrong date) rejected
  6. Corrupt session state handled gracefully
  7. Full crash-during-Phase-3 scenario

Run: pytest tests/integration/test_crash_recovery.py -v
"""
from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.execution.session_state import (
    PositionState,
    SessionState,
    SessionStateManager,
)


# ═══════════════════════════════════════════════════════════════════
# TEST SUITE 1: Stopped-Out Tickers Persistence
# ═══════════════════════════════════════════════════════════════════


class TestStoppedOutPersistence:
    """
    D95: _stopped_out_tickers must survive process restart.
    Without this, Bug 3 (D94b) fix is useless on restart.
    """

    def test_stopped_out_tickers_roundtrip(self, tmp_path: Path):
        """Stop-out cooldown set survives save → load cycle."""
        mgr = SessionStateManager(state_dir=tmp_path)

        # Simulate 3 tickers getting stopped out
        mgr.add_stopped_out_ticker("CANF")
        mgr.add_stopped_out_ticker("ASNS")
        mgr.add_stopped_out_ticker("MOBX")
        mgr.save()

        # New manager instance (simulates process restart)
        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()

        assert state is not None, "Session state must load successfully"
        restored = mgr2.get_stopped_out_tickers()
        assert restored == {"CANF", "ASNS", "MOBX"}, (
            f"Expected {{CANF, ASNS, MOBX}}, got {restored}"
        )

    def test_stopped_out_no_duplicates(self, tmp_path: Path):
        """Adding same ticker twice doesn't create duplicates."""
        mgr = SessionStateManager(state_dir=tmp_path)

        mgr.add_stopped_out_ticker("CANF")
        mgr.add_stopped_out_ticker("CANF")
        mgr.add_stopped_out_ticker("CANF")
        mgr.save()

        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()
        assert state is not None
        assert state.stopped_out_tickers.count("CANF") == 1, "Should not have duplicate entries"

    def test_stopped_out_empty_on_fresh_start(self, tmp_path: Path):
        """Fresh start (no state file) has empty cooldown."""
        mgr = SessionStateManager(state_dir=tmp_path)
        state = mgr.load()

        assert state is None, "No state file should return None"
        assert mgr.get_stopped_out_tickers() == set(), "Fresh start must have empty cooldown"

    def test_stopped_out_cleared_on_reset(self, tmp_path: Path):
        """Daily reset clears stopped-out tickers."""
        mgr = SessionStateManager(state_dir=tmp_path)

        mgr.add_stopped_out_ticker("CANF")
        mgr.save()

        # New day reset
        mgr.reset()
        mgr.save()

        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()
        assert state is not None
        assert mgr2.get_stopped_out_tickers() == set(), "Reset must clear cooldown"


# ═══════════════════════════════════════════════════════════════════
# TEST SUITE 2: EOD Close Flag Persistence
# ═══════════════════════════════════════════════════════════════════


class TestEODFlagPersistence:
    """
    D95: eod_close_completed must survive restart.
    Without this, restart at 15:56 re-fires EOD close (sells ghost positions).
    """

    def test_eod_close_flag_roundtrip(self, tmp_path: Path):
        """EOD close flag survives save → load cycle."""
        mgr = SessionStateManager(state_dir=tmp_path)

        mgr.set_eod_close_completed(True)
        mgr.save()

        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()

        assert state is not None
        assert state.eod_close_completed is True, "EOD close flag must survive restart"

    def test_eod_close_false_by_default(self, tmp_path: Path):
        """EOD close starts as False on fresh session."""
        mgr = SessionStateManager(state_dir=tmp_path)
        mgr.save()

        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()

        assert state is not None
        assert state.eod_close_completed is False, "EOD close must default to False"

    def test_eod_close_cleared_on_reset(self, tmp_path: Path):
        """Daily reset clears EOD close flag."""
        mgr = SessionStateManager(state_dir=tmp_path)
        mgr.set_eod_close_completed(True)
        mgr.save()

        mgr.reset()
        mgr.save()

        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()
        assert state is not None
        assert state.eod_close_completed is False, "Reset must clear EOD flag"


# ═══════════════════════════════════════════════════════════════════
# TEST SUITE 3: Phase 0 Flag Persistence
# ═══════════════════════════════════════════════════════════════════


class TestPhase0FlagPersistence:
    """
    D95: phase0_completed must survive restart.
    Without this, Phase 0 research re-runs wasting 5+ minutes and API calls.
    """

    def test_phase0_flag_roundtrip(self, tmp_path: Path):
        """Phase 0 flag survives save → load cycle."""
        mgr = SessionStateManager(state_dir=tmp_path)

        mgr.set_phase0_completed(True)
        mgr.save()

        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()

        assert state is not None
        assert state.phase0_completed is True, "Phase 0 flag must survive restart"

    def test_phase0_false_by_default(self, tmp_path: Path):
        """Phase 0 starts as False on fresh session."""
        mgr = SessionStateManager(state_dir=tmp_path)
        mgr.save()

        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()
        assert state is not None
        assert state.phase0_completed is False


# ═══════════════════════════════════════════════════════════════════
# TEST SUITE 4: Position State Recovery
# ═══════════════════════════════════════════════════════════════════


class TestPositionStateRecovery:
    """
    Verify position metadata (targets, tranches, stops) survives restart.
    """

    def test_position_full_roundtrip(self, tmp_path: Path):
        """All position fields survive save → load cycle."""
        mgr = SessionStateManager(state_dir=tmp_path)

        mgr.update_position(
            "JZXN",
            qty=500,
            entry_price=5.0,
            signal_price=4.95,
            stop_loss=4.725,
            target_prices=[5.25, 5.50, 6.00],
            tranches_filled=1,
            remaining_qty=333,
            realized_pnl=42.50,
            entry_order_id="ent-001",
            stop_order_id="stop-001",
            tranche_order_ids=["t1-001", "t2-001"],
            trailing_stop_active=True,
            peak_price=5.35,
        )
        mgr.save()

        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()
        assert state is not None

        pos = state.positions["JZXN"]
        assert pos.qty == 500
        assert pos.entry_price == 5.0
        assert pos.signal_price == 4.95
        assert pos.stop_loss == 4.725
        assert pos.target_prices == [5.25, 5.50, 6.00]
        assert pos.tranches_filled == 1
        assert pos.remaining_qty == 333
        assert pos.realized_pnl == 42.50
        assert pos.entry_order_id == "ent-001"
        assert pos.stop_order_id == "stop-001"
        assert pos.tranche_order_ids == ["t1-001", "t2-001"]
        assert pos.trailing_stop_active is True
        assert pos.peak_price == 5.35

    def test_multiple_positions_roundtrip(self, tmp_path: Path):
        """Multiple positions all survive save → load cycle."""
        mgr = SessionStateManager(state_dir=tmp_path)

        for ticker in ["JZXN", "AIFF", "MOBX"]:
            mgr.update_position(
                ticker,
                qty=100,
                entry_price=10.0,
                stop_loss=9.45,
                target_prices=[10.50, 11.00, 12.00],
            )
        mgr.save()

        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()
        assert state is not None
        assert set(state.positions.keys()) == {"JZXN", "AIFF", "MOBX"}

    def test_position_removal_persists(self, tmp_path: Path):
        """Removing a position persists across restarts."""
        mgr = SessionStateManager(state_dir=tmp_path)

        mgr.update_position("CANF", qty=100, entry_price=10.0)
        mgr.update_position("JZXN", qty=200, entry_price=5.0)
        mgr.save()

        # Position closed
        mgr.remove_position("CANF")
        mgr.save()

        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()
        assert state is not None
        assert "CANF" not in state.positions, "Removed position must not reappear"
        assert "JZXN" in state.positions, "Other positions must survive"

    def test_partial_update_preserves_other_fields(self, tmp_path: Path):
        """Updating one field doesn't wipe others."""
        mgr = SessionStateManager(state_dir=tmp_path)

        mgr.update_position(
            "TEST",
            qty=500,
            entry_price=10.0,
            stop_loss=9.45,
            target_prices=[10.50, 11.00, 12.00],
            tranches_filled=0,
        )
        mgr.save()

        # Partial update: only stop_loss and tranches_filled
        mgr.update_position("TEST", stop_loss=9.60, tranches_filled=1)
        mgr.save()

        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()
        assert state is not None

        pos = state.positions["TEST"]
        assert pos.stop_loss == 9.60, "Updated field must reflect new value"
        assert pos.tranches_filled == 1, "Updated field must reflect new value"
        assert pos.qty == 500, "Non-updated field must be preserved"
        assert pos.entry_price == 10.0, "Non-updated field must be preserved"
        assert pos.target_prices == [10.50, 11.00, 12.00], "Non-updated field must be preserved"


# ═══════════════════════════════════════════════════════════════════
# TEST SUITE 5: Stale State Rejection
# ═══════════════════════════════════════════════════════════════════


class TestStaleStateRejection:
    """
    Session state from yesterday must be rejected to prevent
    carrying overnight positions into a new session.
    """

    def test_stale_state_returns_none(self, tmp_path: Path):
        """State from yesterday is rejected."""
        mgr = SessionStateManager(state_dir=tmp_path)

        # Manually create a stale state file
        stale_state = {
            "version": 1,
            "session_date": "2025-01-01",  # Very old date
            "last_update": "2025-01-01T20:00:00+00:00",
            "daily_realized_pnl": 500.0,
            "positions": {
                "OLD": {
                    "ticker": "OLD",
                    "qty": 100,
                    "entry_price": 10.0,
                    "signal_price": 10.0,
                    "stop_loss": 9.45,
                    "target_prices": [10.50],
                    "tranches_filled": 0,
                    "remaining_qty": 100,
                    "realized_pnl": 0.0,
                    "entry_order_id": "",
                    "stop_order_id": "",
                    "tranche_order_ids": [],
                    "opened_at": "",
                    "trailing_stop_active": False,
                    "peak_price": 0.0,
                }
            },
            "stopped_out_tickers": ["OLD_STOP"],
            "eod_close_completed": True,
            "phase0_completed": True,
        }
        (tmp_path / "session_state.json").write_text(json.dumps(stale_state))

        state = mgr.load()
        assert state is None, "Stale state must be rejected (returns None)"

    def test_today_state_accepted(self, tmp_path: Path):
        """State from today is accepted."""
        mgr = SessionStateManager(state_dir=tmp_path)

        today_state = {
            "version": 1,
            "session_date": date.today().isoformat(),
            "last_update": datetime.now(timezone.utc).isoformat(),
            "daily_realized_pnl": 0.0,
            "positions": {},
            "stopped_out_tickers": ["TEST"],
            "eod_close_completed": False,
            "phase0_completed": True,
        }
        (tmp_path / "session_state.json").write_text(json.dumps(today_state))

        state = mgr.load()
        assert state is not None, "Today's state must be accepted"
        assert state.stopped_out_tickers == ["TEST"]
        assert state.phase0_completed is True


# ═══════════════════════════════════════════════════════════════════
# TEST SUITE 6: Corrupt State Handling
# ═══════════════════════════════════════════════════════════════════


class TestCorruptStateHandling:
    """
    Corrupt or malformed state files must be handled gracefully.
    """

    def test_corrupt_json_returns_none(self, tmp_path: Path):
        """Corrupt JSON file returns None (fresh start)."""
        mgr = SessionStateManager(state_dir=tmp_path)
        (tmp_path / "session_state.json").write_text("{not valid json!!!")

        state = mgr.load()
        assert state is None, "Corrupt JSON must return None (fresh start)"

    def test_empty_file_returns_none(self, tmp_path: Path):
        """Empty file returns None."""
        mgr = SessionStateManager(state_dir=tmp_path)
        (tmp_path / "session_state.json").write_text("")

        state = mgr.load()
        assert state is None, "Empty file must return None"

    def test_missing_fields_have_defaults(self, tmp_path: Path):
        """State with missing new fields (D95) still loads with defaults."""
        mgr = SessionStateManager(state_dir=tmp_path)

        # Simulate old-format state file (pre-D95, no stopped_out/eod/phase0)
        old_state = {
            "version": 1,
            "session_date": date.today().isoformat(),
            "last_update": datetime.now(timezone.utc).isoformat(),
            "daily_realized_pnl": 100.0,
            "positions": {},
            # Note: no stopped_out_tickers, eod_close_completed, phase0_completed
        }
        (tmp_path / "session_state.json").write_text(json.dumps(old_state))

        state = mgr.load()
        assert state is not None, "Old-format state must load (backward compatible)"
        assert state.stopped_out_tickers == [], "Missing field → empty list"
        assert state.eod_close_completed is False, "Missing field → False"
        assert state.phase0_completed is False, "Missing field → False"

    def test_atomic_write_no_corrupt_on_crash(self, tmp_path: Path):
        """Verify .tmp file doesn't persist after successful save."""
        mgr = SessionStateManager(state_dir=tmp_path)
        mgr.update_position("TEST", qty=100)
        mgr.save()

        tmp_file = tmp_path / "session_state.tmp"
        assert not tmp_file.exists(), ".tmp file must not exist after successful save"
        assert (tmp_path / "session_state.json").exists(), "Main file must exist"


# ═══════════════════════════════════════════════════════════════════
# TEST SUITE 7: Full Crash-During-Phase-3 Scenario
# ═══════════════════════════════════════════════════════════════════


class TestCrashScenarios:
    """
    End-to-end crash scenarios that verify the complete
    save → crash → reload → resume flow.
    """

    def test_crash_during_phase3_with_stopped_out(self, tmp_path: Path):
        """
        Simulate March 4 scenario:
        1. Buy CANF and JZXN in Phase 2
        2. CANF stops out → added to cooldown
        3. Process crashes
        4. Process restarts
        5. CANF must still be in cooldown
        6. JZXN must still be in positions
        """
        # === Session 1: Before crash ===
        mgr1 = SessionStateManager(state_dir=tmp_path)

        # Phase 2: Bought CANF and JZXN
        mgr1.update_position(
            "CANF", qty=500, entry_price=10.40, stop_loss=9.83,
            target_prices=[10.92, 11.44, 12.48],
        )
        mgr1.update_position(
            "JZXN", qty=1000, entry_price=5.00, stop_loss=4.73,
            target_prices=[5.25, 5.50, 6.00],
        )
        mgr1.save()

        # Phase 3: CANF stops out
        mgr1.add_stopped_out_ticker("CANF")
        mgr1.remove_position("CANF")
        mgr1.save()

        # === CRASH HERE ===

        # === Session 2: After restart ===
        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()

        assert state is not None, "State must load after crash"

        # CANF in cooldown
        restored_cooldown = mgr2.get_stopped_out_tickers()
        assert "CANF" in restored_cooldown, "CANF must still be in stop-out cooldown"
        assert "JZXN" not in restored_cooldown, "JZXN was not stopped out"

        # JZXN still has position
        assert "JZXN" in state.positions, "JZXN position must survive crash"
        assert "CANF" not in state.positions, "CANF was removed before crash"

        # JZXN position data intact
        jzxn = state.positions["JZXN"]
        assert jzxn.entry_price == 5.00
        assert jzxn.stop_loss == 4.73
        assert jzxn.target_prices == [5.25, 5.50, 6.00]

    def test_crash_after_eod_close(self, tmp_path: Path):
        """
        Simulate crash at 15:56 (after EOD close at 15:55):
        1. EOD close fires → all positions sold
        2. eod_close_completed = True persisted
        3. Process crashes
        4. Process restarts at 15:57
        5. Must NOT re-fire EOD close
        """
        # === Session 1: EOD close fires ===
        mgr1 = SessionStateManager(state_dir=tmp_path)

        # Had positions, now all closed
        mgr1.update_position("MOBX", qty=200, entry_price=8.0)
        mgr1.save()

        mgr1.remove_position("MOBX")
        mgr1.set_eod_close_completed(True)
        mgr1.save()

        # === CRASH at 15:56 ===

        # === Session 2: Restart at 15:57 ===
        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()

        assert state is not None
        assert state.eod_close_completed is True, (
            "eod_close_completed must be True — prevents re-fire at 15:57"
        )
        assert len(state.positions) == 0, "All positions were closed before crash"

    def test_crash_during_multiple_stopouts(self, tmp_path: Path):
        """
        Multiple tickers stop out in rapid succession, crash mid-way.
        All stop-outs before crash must be preserved.
        """
        mgr = SessionStateManager(state_dir=tmp_path)

        # Buy 5 positions
        for ticker in ["A", "B", "C", "D", "E"]:
            mgr.update_position(ticker, qty=100, entry_price=10.0)
        mgr.save()

        # A, B, C stop out
        for ticker in ["A", "B", "C"]:
            mgr.add_stopped_out_ticker(ticker)
            mgr.remove_position(ticker)
            mgr.save()  # Each save is atomic

        # === CRASH before D stops out ===

        # Restart
        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()
        assert state is not None

        cooldown = mgr2.get_stopped_out_tickers()
        assert "A" in cooldown
        assert "B" in cooldown
        assert "C" in cooldown
        assert "D" not in cooldown, "D didn't stop out before crash"
        assert "E" not in cooldown, "E didn't stop out before crash"

        # D and E still have positions
        assert "D" in state.positions
        assert "E" in state.positions

    def test_daily_pnl_survives_crash(self, tmp_path: Path):
        """Daily realized P&L (for circuit breaker) survives restart."""
        mgr = SessionStateManager(state_dir=tmp_path)

        mgr.update_daily_pnl(-3500.0)
        mgr.save()

        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()
        assert state is not None
        assert state.daily_realized_pnl == -3500.0, (
            "Daily P&L must survive for circuit breaker accuracy"
        )

    def test_combined_state_integrity(self, tmp_path: Path):
        """
        Full combined state: positions + cooldown + flags + P&L.
        Everything must survive together.
        """
        mgr = SessionStateManager(state_dir=tmp_path)

        # Build complex state
        mgr.update_position("JZXN", qty=1000, entry_price=5.0, stop_loss=4.73,
                            target_prices=[5.25, 5.50, 6.00], tranches_filled=1,
                            remaining_qty=666)
        mgr.update_position("AIFF", qty=500, entry_price=3.0, stop_loss=2.84,
                            target_prices=[3.15, 3.30, 3.60])
        mgr.add_stopped_out_ticker("CANF")
        mgr.add_stopped_out_ticker("ASNS")
        mgr.add_stopped_out_ticker("MOBX")
        mgr.set_phase0_completed(True)
        mgr.set_eod_close_completed(False)
        mgr.update_daily_pnl(-1200.0)
        mgr.save()

        # Full restart
        mgr2 = SessionStateManager(state_dir=tmp_path)
        state = mgr2.load()
        assert state is not None

        # Verify everything
        assert set(state.positions.keys()) == {"JZXN", "AIFF"}
        assert state.positions["JZXN"].tranches_filled == 1
        assert state.positions["JZXN"].remaining_qty == 666
        assert set(state.stopped_out_tickers) == {"CANF", "ASNS", "MOBX"}
        assert state.phase0_completed is True
        assert state.eod_close_completed is False
        assert state.daily_realized_pnl == -1200.0


# ═══════════════════════════════════════════════════════════════════
# TEST SUITE 8: Session State JSON Format
# ═══════════════════════════════════════════════════════════════════


class TestSessionStateJSON:
    """
    Verify the JSON format is correct and backward-compatible.
    """

    def test_json_has_all_d95_fields(self, tmp_path: Path):
        """Saved JSON must include the D95 session-level fields."""
        mgr = SessionStateManager(state_dir=tmp_path)
        mgr.add_stopped_out_ticker("TEST")
        mgr.set_eod_close_completed(True)
        mgr.set_phase0_completed(True)
        mgr.save()

        raw = json.loads((tmp_path / "session_state.json").read_text())

        assert "stopped_out_tickers" in raw, "JSON must include stopped_out_tickers"
        assert "eod_close_completed" in raw, "JSON must include eod_close_completed"
        assert "phase0_completed" in raw, "JSON must include phase0_completed"
        assert raw["stopped_out_tickers"] == ["TEST"]
        assert raw["eod_close_completed"] is True
        assert raw["phase0_completed"] is True

    def test_json_version_is_1(self, tmp_path: Path):
        """Version field is always 1."""
        mgr = SessionStateManager(state_dir=tmp_path)
        mgr.save()

        raw = json.loads((tmp_path / "session_state.json").read_text())
        assert raw["version"] == 1

    def test_json_session_date_matches_today(self, tmp_path: Path):
        """Session date in JSON must match today."""
        mgr = SessionStateManager(state_dir=tmp_path)
        mgr.save()

        raw = json.loads((tmp_path / "session_state.json").read_text())
        assert raw["session_date"] == date.today().isoformat()
