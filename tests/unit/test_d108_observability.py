"""
Tests for D108 WS2: Observability Improvements.

Covers:
  - Metric snapshots to disk with rotation
  - Post-session notification via webhook
  - Reasoning cap increase (500→1500)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.monitoring.metrics import MetricsRegistry
from src.analysis.session_report import SessionReport, SessionReportGenerator


# ── Metric Snapshot Tests ──────────────────────────────────────


class TestMetricSnapshots:
    """D108: Tests for periodic metric snapshots to disk."""

    def test_snapshot_writes_valid_json(self, tmp_path):
        """save_snapshot creates a valid JSON file."""
        registry = MetricsRegistry()
        registry.scan_iterations.inc(5)
        registry.daily_pnl.set(-150.0)

        registry.save_snapshot(tmp_path)

        files = list(tmp_path.glob("metrics_*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["pipeline"]["scan_iterations"] == 5
        assert data["risk"]["daily_pnl"] == -150.0

    def test_snapshot_rotation_keeps_max(self, tmp_path):
        """Writing more than _MAX_SNAPSHOTS deletes oldest files."""
        registry = MetricsRegistry()
        registry._MAX_SNAPSHOTS = 5  # Override for testing

        # Pre-create 7 old snapshot files
        for i in range(7):
            f = tmp_path / f"metrics_20260301T{i:06d}.json"
            f.write_text("{}")

        # Write one more via save_snapshot (total = 8 files)
        registry.save_snapshot(tmp_path)

        files = list(tmp_path.glob("metrics_*.json"))
        assert len(files) == 5  # Only last 5 kept


# ── Session Notification Tests ─────────────────────────────────


class TestSessionNotification:
    """D108: Tests for post-session webhook notification."""

    def _make_report(self) -> SessionReport:
        return SessionReport(
            session_date="2026-03-13",
            session_start="2026-03-13T08:30:00+00:00",
            session_end="2026-03-13T20:00:00+00:00",
            duration_minutes=690,
            mode="paper",
            evaluations_total=8,
            session_trades=2,
            daily_pnl=-85.50,
            win_count=1,
            loss_count=1,
            stop_outs=0,
        )

    @patch("src.analysis.session_report.urllib.request.urlopen")
    def test_notification_sends_post(self, mock_urlopen):
        """Notification sends POST with expected fields."""
        mock_urlopen.return_value.__enter__ = MagicMock()
        mock_urlopen.return_value.__exit__ = MagicMock()

        gen = SessionReportGenerator()
        report = self._make_report()
        gen.send_notification(report, "https://example.com/notify")

        mock_urlopen.assert_called_once()
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        body = json.loads(req.data)
        assert body["date"] == "2026-03-13"
        assert body["pnl"] == -85.5
        assert body["trades"] == 2
        assert body["wins"] == 1

    @patch("src.analysis.session_report.urllib.request.urlopen")
    def test_notification_failure_nonfatal(self, mock_urlopen):
        """urlopen raising an exception does not propagate."""
        mock_urlopen.side_effect = ConnectionError("webhook down")

        gen = SessionReportGenerator()
        report = self._make_report()
        # Should not raise
        gen.send_notification(report, "https://bad.example.com/notify")


# ── Reasoning Cap Tests ────────────────────────────────────────


class TestReasoningCap:
    """D108: Test that reasoning cap increased from 500 to 1500."""

    def test_reasoning_not_truncated_at_1200(self):
        """A 1200-char reasoning string should survive (was truncated at 500)."""
        from src.analysis.trade_journal import TradeJournal

        long_reasoning = "A" * 1200

        # Create a mock signal object
        sig = MagicMock()
        sig.agent_id = "test_agent"
        sig.signal = "BULL"
        sig.confidence = 0.8
        sig.reasoning = long_reasoning
        sig.key_data = {}
        sig.sources_used = []
        sig.model_id = "test-model"
        sig.latency_ms = 100.0

        journal = TradeJournal()
        entry = journal.create_entry(
            trade_id="test-001",
            candidate=MagicMock(ticker="TEST", current_price=10.0),
            phase="MARKET_OPEN",
        )
        journal.record_agent_signals(entry, [sig])

        # Check the recorded reasoning length
        assert len(entry.agent_signals[0].reasoning) == 1200
