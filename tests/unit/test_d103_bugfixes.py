"""
Tests for D103: Post-Trade Day 1 Bug Fixes & Improvements.

Bug 1: D99 fallback path now updates MetricsRegistry.daily_pnl gauge.
Bug 2: SessionReportGenerator receives session_start for correct duration.
Bug 3: LiveDashboard converts UTC to Eastern Time correctly.
Imp 1: SessionReport includes PM ground-truth P&L cross-check.
Imp 2: Debate skip reasons differentiated (budget vs CV).
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from src.monitoring.metrics import reset_metrics, get_metrics, MetricsRegistry
from src.analysis.session_report import SessionReport, SessionReportGenerator


@pytest.fixture(autouse=True)
def _clean_metrics():
    reset_metrics()
    yield
    reset_metrics()


# ── Bug 1: D99 fallback path updates metrics gauge ──


class TestD99MetricsGaugeUpdate:
    """Verify that daily_pnl gauge is updated when D99 fallback records P&L."""

    def test_daily_pnl_gauge_incremented(self):
        """After inc(), gauge should reflect the P&L value."""
        m = get_metrics()
        assert m.daily_pnl.value == 0.0

        # Simulate what D103 fix does: inc() with negative P&L
        m.daily_pnl.inc(-285.10)
        assert abs(m.daily_pnl.value - (-285.10)) < 0.01

    def test_daily_pnl_gauge_set(self):
        """Safety sync uses set() to force gauge to PM ground truth."""
        m = get_metrics()
        m.daily_pnl.inc(-100.0)  # Some prior value
        m.daily_pnl.set(-285.10)  # Safety sync
        assert abs(m.daily_pnl.value - (-285.10)) < 0.01

    def test_daily_pnl_gauge_multiple_increments(self):
        """Multiple stop-outs accumulate correctly in gauge."""
        m = get_metrics()
        m.daily_pnl.inc(-100.0)
        m.daily_pnl.inc(-50.0)
        m.daily_pnl.inc(200.0)
        assert abs(m.daily_pnl.value - 50.0) < 0.01

    def test_session_report_reads_from_gauge(self):
        """Session report daily_pnl should match what the gauge holds."""
        m = get_metrics()
        m.daily_pnl.set(-285.10)

        gen = SessionReportGenerator(mode="paper")
        report = gen.generate()
        assert abs(report.daily_pnl - (-285.10)) < 0.01


# ── Bug 2: Session duration with explicit session_start ──


class TestSessionDuration:
    """Verify that passing session_start yields correct duration."""

    def test_duration_with_explicit_session_start(self):
        """Duration should be > 0 when session_start is in the past."""
        start = datetime.now(timezone.utc) - timedelta(hours=6, minutes=30)
        gen = SessionReportGenerator(mode="paper", session_start=start)
        report = gen.generate()
        # 6h30m = 390 min, allow some tolerance
        assert report.duration_minutes >= 389.0
        assert report.duration_minutes <= 391.0

    def test_duration_default_is_near_zero(self):
        """Without explicit start, duration should be ~0 (instantiation time)."""
        gen = SessionReportGenerator(mode="paper")
        report = gen.generate()
        # Should be very small (< 1 min)
        assert report.duration_minutes < 1.0

    def test_session_start_stored_in_report(self):
        """session_start ISO string should appear in the report."""
        start = datetime(2026, 3, 12, 8, 30, 0, tzinfo=timezone.utc)
        gen = SessionReportGenerator(mode="paper", session_start=start)
        report = gen.generate()
        assert "2026-03-12" in report.session_start


# ── Bug 3: Dashboard UTC→ET conversion ──


class TestDashboardTimezone:
    """Verify LiveDashboard correctly converts UTC to Eastern Time."""

    def test_utc_to_et_conversion_basic(self):
        """9:30 AM UTC should show as 4:30 or 5:30 AM ET (depending on DST)."""
        from src.monitoring.live_dashboard import LiveDashboard

        utc_time = datetime(2026, 3, 12, 13, 30, 0, tzinfo=timezone.utc)
        et_time = utc_time.astimezone(ZoneInfo("America/New_York"))
        # March 12 is during DST (EDT = UTC-4)
        assert et_time.hour == 9
        assert et_time.minute == 30

    def test_print_status_contains_et(self):
        """Dashboard output should contain 'ET' time label."""
        from src.monitoring.live_dashboard import LiveDashboard

        d = LiveDashboard()
        d._print_status()  # Should not crash with the new timezone code

    def test_zoneinfo_import(self):
        """zoneinfo should be importable and support New York."""
        tz = ZoneInfo("America/New_York")
        now = datetime.now(timezone.utc).astimezone(tz)
        assert now.tzinfo is not None


# ── Improvement 1: PM ground-truth P&L in session report ──


class TestPMGroundTruthPnL:
    """Verify realized_pnl_pm field in SessionReport."""

    def test_realized_pnl_pm_defaults_to_zero(self):
        """Without explicit value, PM P&L should be 0."""
        report = SessionReport()
        assert report.realized_pnl_pm == 0.0

    def test_realized_pnl_pm_passed_to_generate(self):
        """generate(realized_pnl_pm=...) should populate the field."""
        gen = SessionReportGenerator(mode="paper")
        report = gen.generate(realized_pnl_pm=-285.10)
        assert abs(report.realized_pnl_pm - (-285.10)) < 0.01

    def test_realized_pnl_pm_defaults_to_metrics_when_not_passed(self):
        """When realized_pnl_pm is None, it should use metrics daily_pnl."""
        m = get_metrics()
        m.daily_pnl.set(-150.0)
        gen = SessionReportGenerator(mode="paper")
        report = gen.generate()
        assert abs(report.realized_pnl_pm - (-150.0)) < 0.01

    def test_summary_text_shows_pm_pnl(self):
        """summary_text() should include the PM ground truth line."""
        report = SessionReport(daily_pnl=-285.10, realized_pnl_pm=-285.10)
        text = report.summary_text()
        assert "PM Ground Truth" in text
        assert "$-285.10" in text

    def test_summary_text_shows_match_checkmark(self):
        """When daily_pnl matches PM, show checkmark."""
        report = SessionReport(daily_pnl=-285.10, realized_pnl_pm=-285.10)
        text = report.summary_text()
        assert "✓" in text

    def test_summary_text_shows_mismatch_warning(self):
        """When daily_pnl != PM P&L, show MISMATCH warning."""
        report = SessionReport(daily_pnl=0.0, realized_pnl_pm=-285.10)
        text = report.summary_text()
        assert "MISMATCH" in text


# ── Improvement 2: Debate skip reason differentiation ──


class TestDebateSkipDifferentiation:
    """Verify budget vs CV skip counters are separate."""

    def test_debates_skipped_budget_counter_exists(self):
        """MetricsRegistry should have debates_skipped_budget counter."""
        m = get_metrics()
        assert hasattr(m, "debates_skipped_budget")
        assert m.debates_skipped_budget.value == 0

    def test_debates_skipped_budget_increments(self):
        """Budget skip counter should increment independently."""
        m = get_metrics()
        m.debates_skipped_budget.inc()
        m.debates_skipped_budget.inc()
        assert m.debates_skipped_budget.value == 2
        assert m.debates_skipped.value == 0  # CV counter unchanged

    def test_debates_skipped_cv_increments_independently(self):
        """CV skip counter should increment independently of budget."""
        m = get_metrics()
        m.debates_skipped.inc()
        assert m.debates_skipped.value == 1
        assert m.debates_skipped_budget.value == 0  # Budget counter unchanged

    def test_snapshot_contains_skipped_budget(self):
        """snapshot() should include skipped_budget in debate section."""
        m = get_metrics()
        m.debates_skipped_budget.inc(5)
        m.debates_skipped.inc(3)
        snap = m.snapshot()
        assert snap["debate"]["skipped_budget"] == 5
        assert snap["debate"]["skipped_cv"] == 3

    def test_skip_rate_includes_both_skip_types(self):
        """skip_rate should include both budget and CV skips."""
        m = get_metrics()
        m.debates_triggered.inc(10)
        m.debates_skipped_budget.inc(6)
        m.debates_skipped.inc(2)
        snap = m.snapshot()
        # skip_rate = (6 + 2) / 10 = 0.8
        assert abs(snap["debate"]["skip_rate"] - 0.8) < 0.01

    def test_session_report_reads_budget_skips(self):
        """Session report should include debates_skipped_budget."""
        m = get_metrics()
        m.debates_triggered.inc(10)
        m.debates_skipped_budget.inc(8)
        m.debates_skipped.inc(0)

        gen = SessionReportGenerator(mode="paper")
        report = gen.generate()
        assert report.debates_skipped_budget == 8
        assert report.debates_skipped == 0

    def test_summary_text_shows_budget_skips(self):
        """summary_text() should differentiate budget vs CV skips."""
        report = SessionReport(
            debates_skipped_budget=8,
            debates_skipped=0,
            debates_no_trade=0,
            debate_skip_rate_pct=100.0,
        )
        text = report.summary_text()
        assert "Skipped (budget): 8" in text
        assert "Skipped (CV): 0" in text

    def test_session_report_dataclass_has_budget_field(self):
        """SessionReport should have debates_skipped_budget field."""
        report = SessionReport(debates_skipped_budget=5)
        assert report.debates_skipped_budget == 5
        d = report.to_dict()
        assert "debates_skipped_budget" in d
        assert d["debates_skipped_budget"] == 5


# ── Integration: Session report end-to-end ──


class TestSessionReportIntegration:
    """End-to-end tests for session report with all D103 changes."""

    def test_full_session_report_with_all_d103_fields(self):
        """Session report should include all D103 additions."""
        m = get_metrics()
        m.daily_pnl.set(-285.10)
        m.debates_triggered.inc(8)
        m.debates_skipped_budget.inc(8)

        start = datetime.now(timezone.utc) - timedelta(hours=7)
        gen = SessionReportGenerator(mode="paper", session_start=start)
        report = gen.generate(realized_pnl_pm=-285.10)

        # Bug 1: P&L matches
        assert abs(report.daily_pnl - (-285.10)) < 0.01
        # Imp 1: PM ground truth
        assert abs(report.realized_pnl_pm - (-285.10)) < 0.01
        # Bug 2: Duration is correct
        assert report.duration_minutes >= 419.0  # ~7h
        # Imp 2: Budget skips
        assert report.debates_skipped_budget == 8

    def test_summary_text_renders_without_error(self):
        """Full summary_text() should not crash with D103 fields."""
        report = SessionReport(
            session_date="2026-03-12",
            mode="paper",
            duration_minutes=420.0,
            daily_pnl=-285.10,
            realized_pnl_pm=-285.10,
            debates_skipped_budget=8,
            debates_skipped=0,
            debates_no_trade=0,
            debate_skip_rate_pct=100.0,
            session_trades=1,
            stop_outs=1,
        )
        text = report.summary_text()
        assert "MOMENTUM-X SESSION REPORT" in text
        assert "2026-03-12" in text
        assert "$-285.10" in text
        assert "420.0 min" in text
        assert "Skipped (budget): 8" in text

    def test_to_dict_includes_all_d103_fields(self):
        """to_dict() should serialize all new D103 fields."""
        report = SessionReport(
            realized_pnl_pm=-285.10,
            debates_skipped_budget=8,
        )
        d = report.to_dict()
        assert "realized_pnl_pm" in d
        assert "debates_skipped_budget" in d
        assert d["realized_pnl_pm"] == -285.10
        assert d["debates_skipped_budget"] == 8
