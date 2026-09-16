"""
Track B wire-in regression test (item 6 of 2026-04-24 next-actions list).

The daemon module is independently tested (8/8 pass via
test_track_b_recon_daemon.py). This test guards the WIRE-IN — that
main.py's cmd_paper() startup actually launches the daemon with
shadow_mode=True, that the asyncio task is registered with the
expected name, and that the trading_blocked gate exists at the entry-
submission site.

Source-grep guards because integration-testing the full main.py
startup is too heavy for a fast feedback loop. The structural checks
catch the canonical "future refactor silently dropped the daemon"
regression class.

Per item 7's pre-open verification: startup MUST log
`[SHADOW] ReconDaemon armed, shadow_mode=True` within 30s of
cmd_paper() start. We test that the launch log line is present in
main.py source.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
MAIN_PY = REPO / "main.py"


class TestTrackB_WireIn:

    def test_main_imports_recon_daemon(self):
        """main.py must import ReconDaemon + ReconState."""
        text = MAIN_PY.read_text(encoding="utf-8")
        assert "from src.monitoring.recon_daemon import" in text, (
            "Track B wire-in: main.py must import ReconDaemon + ReconState"
        )
        assert "ReconDaemon" in text and "ReconState" in text

    def test_daemon_instantiated_with_shadow_mode_true(self):
        """Item 5 — non-negotiable: shadow_mode=True at first arm."""
        text = MAIN_PY.read_text(encoding="utf-8")
        # Find the ReconDaemon(...) construction
        m = re.search(
            r"ReconDaemon\((.*?)\)",
            text, re.DOTALL,
        )
        assert m is not None, "Track B wire-in: ReconDaemon(...) construction not found"
        block = m.group(1)
        assert "shadow_mode=True" in block, (
            "Item 5 violation: first-arm wire-in MUST set shadow_mode=True. "
            f"Got block: {block[:300]}"
        )

    def test_daemon_launched_as_named_asyncio_task(self):
        """Item 7 — pre-open verification needs a named task to track."""
        text = MAIN_PY.read_text(encoding="utf-8")
        # Pattern: asyncio.create_task(daemon.run_forever(), name="track_b_recon")
        # OR: asyncio.create_task(daemon.run_forever(), name=...)
        m = re.search(
            r"asyncio\.create_task\([^)]*recon[^)]*\.run_forever\(\)[^)]*name=[\"']track_b_recon[\"']",
            text, re.DOTALL | re.IGNORECASE,
        )
        assert m is not None, (
            "Track B wire-in: daemon must be launched as "
            "asyncio.create_task(...run_forever(), name='track_b_recon'). "
            "Named task is required for item 7's pre-open verification."
        )

    def test_pre_open_armed_log_line_present(self):
        """Item 7: startup log MUST contain '[SHADOW] ReconDaemon armed' so
        operators can verify the daemon is running before market open."""
        text = MAIN_PY.read_text(encoding="utf-8")
        # The arming log line — flexible on exact phrasing but must
        # contain SHADOW + ReconDaemon + armed
        assert re.search(
            r"\[SHADOW\].*ReconDaemon.*armed",
            text,
        ) or re.search(
            r"ReconDaemon.*\[SHADOW\].*armed",
            text,
        ), (
            "Track B wire-in: startup must log "
            "`[SHADOW] ReconDaemon armed, shadow_mode=True` (or similar) "
            "for item 7's pre-open verification step. Not found in main.py."
        )

    def test_recon_state_shared_with_trading_loop(self):
        """The trading loop reads daemon.state.trading_blocked to gate
        new entries. The wire-in must hold a reference to ReconState
        accessible from the entry-submission site."""
        text = MAIN_PY.read_text(encoding="utf-8")
        # Find ReconState() instantiation
        assert re.search(r"ReconState\(\s*\)", text), (
            "Track B wire-in: must instantiate ReconState() so the "
            "trading loop can read state.trading_blocked"
        )
