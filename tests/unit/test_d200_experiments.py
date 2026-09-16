"""D200: Tests for experiment components (catalyst gate, regime, etc.)."""

import pytest
from src.arena.regime_analyzer import RegimeAnalyzer, RegimeBucket, RegimeReport


class TestRegimeBucket:
    def test_empty(self):
        b = RegimeBucket("test")
        assert b.win_rate == 0.0
        assert b.avg_return_pct == 0.0
        assert not b.positive_ev

    def test_positive_ev(self):
        b = RegimeBucket("good", total=10, wins=7, total_return_pct=0.15)
        assert b.win_rate == 0.7
        assert b.avg_return_pct == pytest.approx(0.015)
        assert b.positive_ev

    def test_negative_ev(self):
        b = RegimeBucket("bad", total=10, wins=3, total_return_pct=-0.10)
        assert b.win_rate == 0.3
        assert not b.positive_ev

    def test_needs_min_samples(self):
        b = RegimeBucket("small", total=3, wins=3, total_return_pct=0.10)
        assert not b.positive_ev  # n < 5


class TestRegimeAnalyzer:
    def test_empty_scenarios(self):
        analyzer = RegimeAnalyzer()
        report = analyzer.analyze()
        assert report.total_scenarios == 0

    def test_analyze_with_data(self):
        analyzer = RegimeAnalyzer()
        analyzer._scenarios = [
            {"ticker": "A", "date": "2026-01-06", "outcome": "WIN",
             "intraday_return": 0.05, "gap_pct": 0.15},
            {"ticker": "B", "date": "2026-01-06", "outcome": "LOSS",
             "intraday_return": -0.03, "gap_pct": 0.30},
            {"ticker": "C", "date": "2026-01-07", "outcome": "WIN",
             "intraday_return": 0.08, "gap_pct": 0.60},
        ]
        report = analyzer.analyze()
        assert report.total_scenarios == 3
        # Day of week should have entries
        assert any(b.total > 0 for b in report.by_day_of_week.values())

    def test_gap_size_partitioning(self):
        analyzer = RegimeAnalyzer()
        analyzer._scenarios = [
            {"ticker": "SMALL", "date": "2026-01-06", "outcome": "WIN",
             "intraday_return": 0.05, "gap_pct": 0.10},
            {"ticker": "BIG", "date": "2026-01-06", "outcome": "LOSS",
             "intraday_return": -0.03, "gap_pct": 0.55},
        ]
        report = analyzer.analyze()
        assert report.by_gap_size["Gap 5-20%"].total == 1
        assert report.by_gap_size["Gap 50-100%"].total == 1

    def test_format_report_no_crash(self):
        analyzer = RegimeAnalyzer()
        report = analyzer.analyze()
        text = analyzer.format_report(report)
        assert "REGIME-CONDITIONAL" in text


class TestCatalystGateConfig:
    def test_default_enabled(self):
        """D201: require_catalyst defaults to True (enabled in production)."""
        from config.settings import UniverseConfig
        config = UniverseConfig()
        assert config.require_catalyst is True

    def test_disable_via_env(self, monkeypatch):
        """Can be disabled via environment variable."""
        monkeypatch.setenv("UNIVERSE_REQUIRE_CATALYST", "false")
        from config.settings import UniverseConfig
        config = UniverseConfig()
        assert config.require_catalyst is False
