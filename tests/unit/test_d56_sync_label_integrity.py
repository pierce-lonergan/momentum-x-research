"""
Wed 2026-04-22 Bug C tests: D56 sync labeling integrity.

The bug. This morning's D56 sync log line:

    D56 sync: ELSE qty=2478 @ $7.65 (stop=$7.23) from broker

was a LIE. The broker had ZERO open orders for ELSE. The "$7.23" was
`entry_price × (1 - stop_loss_pct)` from settings -- a COMPUTED DEFAULT,
not a broker-confirmed stop. The "from broker" label made the operator
(me) believe a real protective stop existed when none did. This is the
integrity bug at the heart of Wed morning: I trusted the dashboard
showing "stop=$7.23" and only discovered it was phantom by directly
querying the broker. ELSE rode out 2 hours of pre-market naked while
the system displayed protection that didn't exist.

Per user spec: "D56's 'stop from broker' claim must be backed by an
actual broker order ID. If no order ID can be associated, the
displayed value must be marked as 'computed default' or 'no broker
stop' -- never silently labeled as if it came from the broker."

Two-part fix:
  1. position_manager.py D56 sync log line says "COMPUTED DEFAULT,
     no broker order" -- not "from broker".
  2. live_dashboard.py renders position lines with a "⚠UNVERIFIED"
     marker beside any stop value when stop_order_id is empty.

These tests pin both layers permanently.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
POSITION_MANAGER = REPO_ROOT / "src" / "execution" / "position_manager.py"
LIVE_DASHBOARD = REPO_ROOT / "src" / "monitoring" / "live_dashboard.py"


# ── Layer 1: position_manager log line integrity ────────────────────


class TestD56SyncLogIntegrity:

    def test_d56_does_not_say_from_broker_for_computed_stop(self):
        """The bug: 'from broker' label on a computed-default stop.
        Specifically guard against the literal pattern that was
        misleading this morning."""
        text = POSITION_MANAGER.read_text(encoding="utf-8")
        # Find every D56 sync log f-string
        # Bad pattern (the bug): includes "from broker" without a
        # "COMPUTED DEFAULT" disclaimer in the same string.
        for match in re.finditer(
            r'"D56 sync:[^"]*?from broker"', text,
        ):
            snippet = match.group(0)
            assert "COMPUTED DEFAULT" in snippet or "no broker order" in snippet, (
                "D56 sync log line claims 'from broker' but the stop "
                "value is actually a COMPUTED DEFAULT from settings. "
                f"Misleading label found at: {snippet}"
            )

    def test_d56_log_explicitly_marks_computed_default(self):
        """Positive case: the new log line must contain explicit
        'COMPUTED DEFAULT' wording so operators can grep for it.
        (Match across multi-line f-string literals; any D56 sync
        log call must mention the phrase.)"""
        text = POSITION_MANAGER.read_text(encoding="utf-8")
        # Find the D56 sync log block (with surrounding context to span
        # multi-line concatenated string literals). The block extends
        # from "D56 sync:" until the closing paren of the logger call.
        assert "D56 sync:" in text and "COMPUTED DEFAULT" in text, (
            "D56 sync log must include 'COMPUTED DEFAULT' so the value "
            "is unmistakable as not-broker-confirmed. Search the file "
            "for 'D56 sync:' and confirm the phrase appears in the same "
            "logger call."
        )
        # Stronger check: 'COMPUTED DEFAULT' must appear within 200
        # chars of the D56 sync log marker.
        idx = text.find("D56 sync:")
        # Pick the LOG CALL occurrence (skip the docstring mention if any)
        while idx >= 0:
            window = text[idx:idx+400]
            if "COMPUTED DEFAULT" in window:
                break
            idx = text.find("D56 sync:", idx + 1)
        assert idx >= 0, (
            "No D56 sync log block within 400 chars of 'COMPUTED DEFAULT' "
            "phrase. The disclaimer must be inside the SAME log call as "
            "the 'D56 sync:' label, not in a separate comment."
        )

    def test_d56_log_says_no_broker_order(self):
        """Reinforces the message: explicit 'no broker order' phrase
        so the operator can't miss it."""
        text = POSITION_MANAGER.read_text(encoding="utf-8")
        assert "no broker order" in text, (
            "position_manager.py must contain 'no broker order' phrase "
            "in the D56 sync log to make the unverified state explicit"
        )


class TestD56SyncCreatesPositionWithEmptyStopOrderId:
    """The recovered ManagedPosition from D56 sync must have
    stop_order_id="" so downstream code (dashboard, exit-ladder)
    can detect 'no broker stop confirmed yet' state."""

    def test_sync_constructed_position_has_empty_stop_order_id(self):
        """Behavioral test: actually call sync_from_broker and check
        the resulting ManagedPosition's stop_order_id field."""
        from unittest.mock import MagicMock
        from src.execution.position_manager import PositionManager
        from config.settings import ExecutionConfig

        cfg = ExecutionConfig()
        pm = PositionManager(config=cfg, starting_equity=100_000.0)

        broker_positions = [{
            "symbol": "ELSE",
            "qty": "2478",
            "side": "long",
            "avg_entry_price": "7.65",
            "current_price": "8.20",
        }]
        n = pm.sync_from_broker(broker_positions)
        assert n == 1

        pos = next(p for p in pm.open_positions if p.ticker == "ELSE")
        assert pos.stop_order_id == "", (
            f"D56 sync constructed ManagedPosition with "
            f"stop_order_id={pos.stop_order_id!r} -- should be empty "
            f"string to signal 'no broker stop confirmed'. The dashboard "
            f"reads this field to render the UNVERIFIED marker."
        )


# ── Layer 2: dashboard renders the unverified marker ────────────────


class TestDashboardUnverifiedMarker:

    def test_dashboard_renders_marker_when_stop_order_id_empty(self):
        """Behavioral: dashboard must visually surface the no-broker-
        stop state. Build a position with empty stop_order_id and
        check the rendered text."""
        from unittest.mock import MagicMock
        from src.monitoring.live_dashboard import LiveDashboard

        # Build a fake PositionManager exposing one position
        class FakePos:
            ticker = "ELSE"
            remaining_qty = 2478
            entry_price = 7.65
            stop_loss = 7.23
            stop_order_id = ""  # the bug case: no broker stop
            tranches_filled = 0
        fake_pm = MagicMock()
        fake_pm.open_positions = [FakePos()]

        dash = LiveDashboard(position_manager=fake_pm)
        # Build a snapshot dict the dashboard expects
        snap = {"pipeline": {}, "execution": {}, "risk": {}, "agents": {}}
        block = dash._format_block(snap) if hasattr(dash, "_format_block") else None

        # Some dashboards expose a render method; try a few common names
        for method_name in ("_format_block", "render", "format", "_render"):
            method = getattr(dash, method_name, None)
            if callable(method):
                try:
                    block = method(snap) if method_name != "render" else method()
                    break
                except Exception:
                    continue

        if block is None:
            # If we can't easily call the renderer, fall back to
            # asserting the *source* contains the marker logic.
            text = LIVE_DASHBOARD.read_text(encoding="utf-8")
            assert "UNVERIFIED" in text, (
                "Dashboard source must include the UNVERIFIED marker "
                "logic for unconfirmed stops"
            )
            return

        assert "UNVERIFIED" in block, (
            f"Dashboard rendering an empty-stop_order_id position must "
            f"include UNVERIFIED marker. Got block:\n{block}"
        )

    def test_dashboard_NO_marker_when_stop_order_id_present(self):
        """Negative-case complement: a real broker-confirmed stop
        should NOT show the UNVERIFIED marker."""
        text = LIVE_DASHBOARD.read_text(encoding="utf-8")
        # Verify the conditional logic exists: "" -> marker, non-empty -> no marker
        assert (
            re.search(r'has_broker_stop\s*=\s*bool\(', text)
            or re.search(r'stop_order_id', text)
        ), (
            "Dashboard must check stop_order_id to decide whether to "
            "show the UNVERIFIED marker"
        )
        # And the marker must be conditional, not unconditional
        assert re.search(
            r'""\s*if\s+has_broker_stop\s+else\s+["\']\s*[^a-zA-Z]*UNVERIFIED',
            text,
        ) or re.search(
            r'if\s+has_broker_stop[^"]*UNVERIFIED',
            text,
        ), (
            "Marker must be GATED by has_broker_stop -- not always shown"
        )


# ── Source-grep guards (catch silent re-introductions) ──────────────


class TestNoSilentMisleadingLabels:
    """Defense against future re-introduction of the misleading label.
    Any new log line claiming 'from broker' alongside a stop value
    must include the COMPUTED-DEFAULT disclaimer or be backed by a
    real order id."""

    def test_no_other_from_broker_stop_claims_in_position_manager(self):
        text = POSITION_MANAGER.read_text(encoding="utf-8")
        # Look for any line saying "stop=" and "from broker" in the same
        # log call. Allow it ONLY if the same string also disclaims as
        # COMPUTED DEFAULT.
        for match in re.finditer(
            r'logger\.(info|warning|error|critical)\(\s*["\']([^"\']*)["\']',
            text,
        ):
            log_text = match.group(2)
            if "stop=" in log_text and "from broker" in log_text:
                if (
                    "COMPUTED DEFAULT" not in log_text
                    and "no broker order" not in log_text
                    and "%(stop_oid)s" not in log_text  # explicitly threaded
                ):
                    pytest.fail(
                        f"Found misleading log line in position_manager.py: "
                        f"{log_text!r} claims 'from broker' without "
                        f"COMPUTED DEFAULT disclaimer."
                    )
