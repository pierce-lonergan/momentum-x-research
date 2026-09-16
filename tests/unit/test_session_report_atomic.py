"""
D218 Item 4: Atomic session report write tests.

Verifies the .tmp + os.replace() pattern prevents half-written reports.
"""

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def temp_report_dir():
    d = tempfile.mkdtemp(prefix="report_test_")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def _make_mock_report():
    """Create a minimal SessionReport-like object for testing."""
    report = MagicMock()
    report.session_date = "2026-04-11"
    report.session_end = "2026-04-11T16:00:00+00:00"
    report.to_dict.return_value = {
        "session_date": "2026-04-11",
        "session_end": "2026-04-11T16:00:00+00:00",
        "daily_pnl": 42.50,
        "session_trades": 3,
        "mode": "paper",
    }
    return report


class TestSessionReportAtomicWrite:

    def test_successful_write_produces_valid_json(self, temp_report_dir):
        from src.analysis.session_report import SessionReportGenerator
        gen = SessionReportGenerator.__new__(SessionReportGenerator)
        gen._report_dir = temp_report_dir
        gen._mode = "paper"

        report = _make_mock_report()
        path = gen.save(report)

        assert path.exists()
        data = json.loads(path.read_text())
        assert data["daily_pnl"] == 42.50
        assert data["session_trades"] == 3

    def test_serialization_error_leaves_no_file(self, temp_report_dir):
        from src.analysis.session_report import SessionReportGenerator
        gen = SessionReportGenerator.__new__(SessionReportGenerator)
        gen._report_dir = temp_report_dir

        report = _make_mock_report()
        # Make serialization fail
        report.to_dict.side_effect = RuntimeError("serialize boom")

        with pytest.raises(RuntimeError, match="serialize boom"):
            gen.save(report)

        # No report file should exist
        reports = list(temp_report_dir.glob("session_*.json"))
        assert len(reports) == 0
        # No tmp file should remain
        tmps = list(temp_report_dir.glob("*.tmp"))
        assert len(tmps) == 0

    def test_replace_error_leaves_previous_file_intact(self, temp_report_dir):
        from src.analysis.session_report import SessionReportGenerator
        gen = SessionReportGenerator.__new__(SessionReportGenerator)
        gen._report_dir = temp_report_dir

        # Create an existing report
        report = _make_mock_report()
        path = gen.save(report)
        original_content = path.read_text()

        # Now try to save a new report that fails during os.replace
        report2 = _make_mock_report()
        report2.to_dict.return_value = {"daily_pnl": 999.99, "session_trades": 10,
                                         "session_date": "2026-04-11",
                                         "session_end": "2026-04-11T16:00:00+00:00",
                                         "mode": "paper"}

        with patch("os.replace", side_effect=OSError("replace failed")):
            with pytest.raises(OSError, match="replace failed"):
                gen.save(report2)

        # Original file should still have the old content
        assert path.read_text() == original_content

    def test_bak_file_created_when_previous_exists(self, temp_report_dir):
        from src.analysis.session_report import SessionReportGenerator
        gen = SessionReportGenerator.__new__(SessionReportGenerator)
        gen._report_dir = temp_report_dir

        report = _make_mock_report()
        path = gen.save(report)

        # Save again — should create .bak
        report2 = _make_mock_report()
        report2.to_dict.return_value = {"daily_pnl": 100.0, "session_trades": 5,
                                         "session_date": "2026-04-11",
                                         "session_end": "2026-04-11T16:00:00+00:00",
                                         "mode": "paper"}
        gen.save(report2)

        bak = path.with_suffix(".bak")
        assert bak.exists()
        bak_data = json.loads(bak.read_text())
        assert bak_data["daily_pnl"] == 42.50  # Original data

    def test_bak_failure_does_not_prevent_write(self, temp_report_dir):
        from src.analysis.session_report import SessionReportGenerator
        gen = SessionReportGenerator.__new__(SessionReportGenerator)
        gen._report_dir = temp_report_dir

        # Create initial report
        report = _make_mock_report()
        path = gen.save(report)

        # Save again with shutil.copy2 failing
        report2 = _make_mock_report()
        report2.to_dict.return_value = {"daily_pnl": 200.0, "session_trades": 7,
                                         "session_date": "2026-04-11",
                                         "session_end": "2026-04-11T16:00:00+00:00",
                                         "mode": "paper"}

        with patch("shutil.copy2", side_effect=OSError("bak failed")):
            result = gen.save(report2)

        # New report should still be written despite backup failure
        assert result.exists()
        data = json.loads(result.read_text())
        assert data["daily_pnl"] == 200.0
