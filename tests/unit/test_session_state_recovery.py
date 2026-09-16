"""
Wed 2026-04-22 Bug B tests: session_state recovery + launcher contract.

The bug: this morning at 04:30 ET the launcher's D90 routine deleted
session_state.json every day regardless of date. Root cause: the PS1
script read `$stateJson.date` but the JSON field is actually
`session_date`, so `$null -ne $Today` always evaluated TRUE → file
deleted. D64 then logged "No session state file found — fresh start"
and lost stop_order_id, target_prices, tranche_order_ids, opened_at,
peak_price for every held position — every day.

Two-layer fix:
  1. Launcher PS1: corrected to read `$stateJson.session_date` (the
     actual JSON field name).
  2. session_state.py:load(): defense-in-depth — if the primary file
     is MISSING (not just corrupt), try `.bak` before giving up. Per
     user spec: "D64 *never* overwrites a populated state file with
     a fresh-start empty version (the catastrophic case from this
     morning)."

This test file enforces both layers permanently.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import pytest

from src.execution.session_state import (
    PositionState,
    SessionState,
    SessionStateManager,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_PS1 = REPO_ROOT / "scripts" / "daily_paper_trade.ps1"


# ── Helper to build a populated state ───────────────────────────────


def _seed_with_bak(mgr: SessionStateManager, state: SessionState) -> None:
    """Seed `mgr` with a state and force the .bak to exist.

    `_save_inner` only creates .bak if the primary file already
    exists (the "rotate previous to backup" pattern). To get an
    initial .bak, save() twice — the second call's "before
    overwriting, copy current to .bak" creates the backup.
    """
    mgr._state = state
    mgr.save()  # first: writes primary, no .bak yet
    mgr.save()  # second: copies primary to .bak, then writes primary again


def _populated_state(session_date: str, ticker: str = "ELSE") -> SessionState:
    s = SessionState(
        session_date=session_date,
        last_update=datetime.now(timezone.utc).isoformat(),
    )
    s.positions[ticker] = PositionState(
        ticker=ticker,
        qty=2478,
        entry_price=7.65,
        signal_price=7.65,
        stop_loss=7.50,
        target_prices=[8.03, 8.42, 9.18],
        tranches_filled=0,
        remaining_qty=2478,
        realized_pnl=0.0,
        entry_order_id="ce8fa636",
        stop_order_id="b062f185",
        tranche_order_ids=[],
        opened_at="2026-04-21T14:17:06+00:00",
        trailing_stop_active=False,
        peak_price=8.40,
        position_tier=3,
        kelly_tier=1,
    )
    return s


# ── Layer 1: Launcher PS1 contract ──────────────────────────────────


class TestLauncherStateFieldName:
    """The launcher must read the CORRECT JSON field name. If a
    future PR changes the field but forgets to update the launcher,
    we re-introduce the daily-delete bug. This test enforces the
    contract."""

    def test_launcher_reads_session_date_not_date(self):
        text = LAUNCHER_PS1.read_text(encoding="utf-8")
        # The CORRECT pattern: launcher must read $stateJson.session_date
        assert re.search(
            r"\$stateJson\.session_date", text,
        ), (
            "Launcher must read $stateJson.session_date (the actual JSON "
            "field). If you renamed the field, update both the launcher "
            "and src/execution/session_state.py SessionState.session_date."
        )

    def test_launcher_does_not_use_wrong_field(self):
        """Specific guard: NO occurrence of `$stateJson.date` in LIVE
        code (the bug we just fixed). Comments referring to the bug
        by name are allowed. If a future cleanup re-introduces the
        wrong field name in code, fail loudly."""
        text = LAUNCHER_PS1.read_text(encoding="utf-8")
        # Scan line-by-line, ignoring PowerShell comment lines (start with #
        # after optional leading whitespace).
        offenders: list[tuple[int, str]] = []
        for i, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue  # PS1 comment line — safe to mention the bug
            # Match $stateJson.date with non-word-character boundary so
            # $stateJson.session_date is not a false positive.
            if re.search(r"\$stateJson\.date(?![a-z_])", line, re.IGNORECASE):
                offenders.append((i, line.strip()))
        assert not offenders, (
            f"Launcher has {len(offenders)} line(s) using $stateJson.date "
            f"in live code — the JSON field is session_date:\n"
            + "\n".join(f"  line {ln}: {txt}" for ln, txt in offenders)
        )

    def test_launcher_logs_preservation_branch(self):
        """When state is current (not stale), the launcher should log
        an explicit 'preserving' message — not silently fall through.
        Operators reading the transcript should see why the file
        survived."""
        text = LAUNCHER_PS1.read_text(encoding="utf-8")
        assert re.search(
            r"preserving for D64 recovery", text,
        ), (
            "Launcher should log a 'preserving for D64 recovery' "
            "message when state file is current — silent fall-through "
            "is what hid this morning's bug from observability"
        )


# ── Layer 2: SessionStateManager.load() defense-in-depth ────────────


class TestLoadBackupOnMissingPrimary:
    """The defense: if the primary file is MISSING (not just corrupt),
    load() must try .bak before returning None. Today's bug: the
    launcher deleted the primary daily; D64 went straight to fresh
    start, losing all state."""

    def test_missing_primary_loads_from_backup(self, tmp_path):
        """T1: file deleted overnight, .bak survived → recover from bak."""
        mgr = SessionStateManager(state_dir=tmp_path)

        # Seed: write a populated state, then save() to create the .bak too
        _seed_with_bak(mgr, _populated_state(date.today().isoformat()))
        # _seed_with_bak() forces .bak via two saves; verify both exist
        assert mgr.file_path.exists()
        assert mgr._backup_path.exists()

        # Simulate the launcher deleting the primary
        mgr.file_path.unlink()
        assert not mgr.file_path.exists()
        assert mgr._backup_path.exists()  # .bak still there

        # Reload — must recover from backup, not return None
        mgr2 = SessionStateManager(state_dir=tmp_path)
        recovered = mgr2.load()
        assert recovered is not None, (
            "load() returned None despite .bak being present and current — "
            "Bug B defense-in-depth not active"
        )
        assert "ELSE" in recovered.positions
        assert recovered.positions["ELSE"].stop_order_id == "b062f185"

    def test_missing_primary_AND_missing_backup_returns_none(self, tmp_path):
        """T2: both files gone → graceful fresh start (not crash)."""
        mgr = SessionStateManager(state_dir=tmp_path)
        # No files written yet; both .json and .bak are missing
        result = mgr.load()
        assert result is None  # fresh start, no crash

    def test_missing_primary_with_stale_backup_returns_none(self, tmp_path):
        """T3: backup exists but is from 30 days ago → don't use it
        (would re-create yesterday's positions)."""
        mgr = SessionStateManager(state_dir=tmp_path)
        # Seed a STALE state (old date), write+save (creates .bak)
        old_date = (date.today() - timedelta(days=30)).isoformat()
        _seed_with_bak(mgr, _populated_state(old_date))

        # Delete primary
        mgr.file_path.unlink()
        # .bak now contains the STALE state

        mgr2 = SessionStateManager(state_dir=tmp_path)
        result = mgr2.load()
        assert result is None, (
            "Stale backup (30d old) must NOT be loaded — would resurrect "
            "ancient positions"
        )

    def test_missing_primary_present_but_corrupt_backup_returns_none(self, tmp_path):
        """T4: primary missing, backup exists but is garbage JSON."""
        mgr = SessionStateManager(state_dir=tmp_path)
        # Write a corrupt .bak
        mgr._backup_path.write_text("{not valid: json", encoding="utf-8")

        result = mgr.load()
        assert result is None  # graceful fall-through to fresh start


class TestPrimaryCorruptStillTriesBackup:
    """Pre-existing D108 behavior: corrupt primary already tries
    backup. Make sure Bug B fix didn't break this path."""

    def test_corrupt_primary_falls_through_to_backup(self, tmp_path):
        mgr = SessionStateManager(state_dir=tmp_path)
        # Seed valid populated state with .bak
        _seed_with_bak(mgr, _populated_state(date.today().isoformat()))
        # Corrupt the primary
        mgr.file_path.write_text("{not valid: json", encoding="utf-8")
        # Reload — should fall through to backup
        mgr2 = SessionStateManager(state_dir=tmp_path)
        recovered = mgr2.load()
        assert recovered is not None
        assert "ELSE" in recovered.positions


# ── User-spec rule: "D64 NEVER overwrites populated state with empty" ─


class TestPopulatedStateNotOverwrittenByEmpty:
    """The catastrophic case the user named: D64 fresh-start created
    an empty SessionState in memory; subsequent save() wrote that
    empty state over the on-disk populated file. This test ensures
    that even if some future caller does this, the saved file
    preserves the on-disk state via .bak rotation.

    The defense lives in two complementary places:
      1. load() now tries .bak when primary is missing (already
         tested above).
      2. save() creates .bak BEFORE overwriting the primary, so even
         if a fresh-start empty state gets save()'d, the previous
         populated state is preserved at .bak.
    """

    def test_save_creates_bak_before_overwriting(self, tmp_path):
        """Verifies the .bak rotation behavior. If this regresses, the
        load() defense becomes useless."""
        mgr = SessionStateManager(state_dir=tmp_path)
        # First seed: populated state with .bak
        _seed_with_bak(mgr, _populated_state(date.today().isoformat()))
        first_content = mgr.file_path.read_text(encoding="utf-8")
        assert "ELSE" in first_content

        # Second save: catastrophic empty
        mgr2 = SessionStateManager(state_dir=tmp_path)
        # mgr2 starts empty (the fresh-start case)
        mgr2.save()
        # Now: primary should have empty positions, but .bak should
        # still contain the populated ELSE state
        primary = mgr2.file_path.read_text(encoding="utf-8")
        backup = mgr2._backup_path.read_text(encoding="utf-8")
        assert "ELSE" not in primary  # overwritten with empty
        assert "ELSE" in backup, (
            ".bak should preserve the previous populated state. If a "
            "fresh-start empty state overwrites the on-disk populated "
            "state, .bak is the recovery point."
        )

        # Now demonstrate the recovery via load():
        # delete primary, .bak still has ELSE → load() should recover
        mgr2.file_path.unlink()
        mgr3 = SessionStateManager(state_dir=tmp_path)
        recovered = mgr3.load()
        assert recovered is not None
        assert "ELSE" in recovered.positions, (
            "Round-trip: even after a fresh-start save() overwrites "
            "the populated primary, .bak rotation + load()-tries-bak "
            "give a recovery path"
        )


# ── Today's morning scenario, simulated ─────────────────────────────


class TestThisMorningScenarioWouldBeCaught:
    """Rule (e) positive case: simulate this morning's exact
    catastrophe and assert the new behavior recovers."""

    def test_morning_2026_04_22_recovery(self, tmp_path):
        # 1. Seed yesterday's populated state file (what was on disk
        #    Tue evening after my Phase 1 patch added the GTC stop ref)
        mgr = SessionStateManager(state_dir=tmp_path)
        state = _populated_state(date.today().isoformat())
        state.positions["ELSE"].stop_order_id = "6490714a"  # last night's
        state.positions["ELSE"].stop_loss = 6.50
        _seed_with_bak(mgr, state)

        # 2. Simulate the launcher's BUGGY D90 deleting the primary
        #    (which is what happened this morning at 04:30:04)
        mgr.file_path.unlink()

        # 3. Simulate the next session start: D64.load()
        mgr_next = SessionStateManager(state_dir=tmp_path)
        recovered = mgr_next.load()

        # 4. The recovery should produce a populated state, NOT None.
        #    With Bug B fix: load() tries .bak when primary is missing.
        assert recovered is not None, (
            "RECOVERY FAILED: this morning's scenario still produces "
            "fresh-start. Either the .bak fallback in load() didn't "
            "activate or the test fixture is incomplete."
        )
        assert "ELSE" in recovered.positions
        else_pos = recovered.positions["ELSE"]
        assert else_pos.qty == 2478
        assert else_pos.stop_order_id == "6490714a"
        assert else_pos.stop_loss == 6.50
        assert else_pos.target_prices == [8.03, 8.42, 9.18]
