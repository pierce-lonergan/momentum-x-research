"""Tests for regime classification and drift detection."""

from __future__ import annotations

import pytest

from arena.regime import (
    classify_regime,
    detect_parameter_drift,
    DayRegime,
    DriftAlert,
)


class TestRegimeClassification:
    def test_low_vol_trend(self):
        assert classify_regime(spy_return=0.5, vixy_return=1.0) == "LOW_VOL_TREND"

    def test_elevated_vol_from_spy(self):
        assert classify_regime(spy_return=-0.8, vixy_return=2.0) == "ELEVATED_VOL"

    def test_elevated_vol_from_vixy(self):
        assert classify_regime(spy_return=0.0, vixy_return=8.0) == "ELEVATED_VOL"

    def test_crisis_from_spy(self):
        assert classify_regime(spy_return=-2.0, vixy_return=0.0) == "CRISIS"

    def test_crisis_from_vixy(self):
        assert classify_regime(spy_return=0.0, vixy_return=20.0) == "CRISIS"

    def test_boundary_negative_half_pct(self):
        # -0.5% is exactly at boundary (< -0.5 triggers ELEVATED, == -0.5 is LOW_VOL)
        assert classify_regime(spy_return=-0.5, vixy_return=0.0) == "LOW_VOL_TREND"
        assert classify_regime(spy_return=-0.51, vixy_return=0.0) == "ELEVATED_VOL"

    def test_boundary_positive(self):
        assert classify_regime(spy_return=0.0, vixy_return=0.0) == "LOW_VOL_TREND"


class TestDriftDetection:
    def test_no_drift_similar_performance(self):
        training = [{"pnl": 0.5}] * 10 + [{"pnl": -0.3}] * 5
        recent = [{"pnl": 0.4}] * 5 + [{"pnl": -0.2}] * 3
        alerts = detect_parameter_drift(training, recent)
        # No alerts should fire
        assert not any(a.is_alert for a in alerts)

    def test_drift_detected_on_degradation(self):
        training = [{"pnl": 1.0}] * 15 + [{"pnl": -0.2}] * 5
        recent = [{"pnl": -0.5}] * 8  # All losses
        alerts = detect_parameter_drift(training, recent)
        # At least one alert should fire
        pf_alert = [a for a in alerts if a.metric == "profit_factor"]
        assert len(pf_alert) == 1
        assert pf_alert[0].recent_value < pf_alert[0].training_value

    def test_insufficient_data_no_alerts(self):
        alerts = detect_parameter_drift([{"pnl": 1.0}], [{"pnl": -1.0}])
        assert len(alerts) == 0

    def test_win_rate_drift(self):
        training = [{"pnl": 1.0}] * 8 + [{"pnl": -0.5}] * 2  # 80% WR
        recent = [{"pnl": 1.0}] * 2 + [{"pnl": -0.5}] * 8    # 20% WR
        alerts = detect_parameter_drift(training, recent)
        wr_alert = [a for a in alerts if a.metric == "win_rate"]
        assert len(wr_alert) == 1
        assert wr_alert[0].training_value > wr_alert[0].recent_value
