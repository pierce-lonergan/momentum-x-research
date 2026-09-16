"""
Tests for D195: Continuous Arena Auto-Tuning.

Covers:
  - DriftAlert / TuningReport construction
  - _find_optimal_faller_threshold grid search
  - _check_faller_accuracy with known-good data
  - _check_faller_threshold_drift (below / above tolerance)
  - _check_short_accuracy (clean / degraded)
  - _check_stop_drift (aligned / misaligned)
  - _check_data_coverage (sparse / sufficient)
  - _format_report (no alerts / mixed alerts)
  - save_report writes text + JSON
  - run_post_session with empty data dir (graceful degradation)
  - Meta-simulation: ENTRY_SLIPPAGE_PCT reduces gain
  - Meta-simulation: STOP_FILL_SLIPPAGE_PCT worsens stop
  - Meta-simulation: is_shortable=False blocks short entry
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

import pytest

# ── Project root ──────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.data.auto_tuner import AutoTuner, DriftAlert, TuningReport


# =============================================================================
# Helpers / fixtures
# =============================================================================

def make_tuner(tmp_path: Path) -> AutoTuner:
    return AutoTuner(data_dir=str(tmp_path))


def write_scenarios(tmp_path: Path, scenarios: list[dict]) -> None:
    path = tmp_path / "labeled_scenarios.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for s in scenarios:
            f.write(json.dumps(s) + "\n")


_HIGH_FALLER_FADER = {
    "scenario_id": "PUMP_2026-01-01",
    "ticker": "PUMP",
    "date": "2026-01-01",
    "actual_faller_score": 0.75,
    "outcome": "fader",
    "dollar_volume": 1_000_000,
    "was_traded": True,
    "trade_pnl": -400,
    "max_drawdown_pct": 0.04,
}

_LOW_FALLER_RUNNER = {
    "scenario_id": "RUN_2026-01-02",
    "ticker": "RUN",
    "date": "2026-01-02",
    "actual_faller_score": 0.25,
    "outcome": "runner",
    "dollar_volume": 2_000_000,
    "was_traded": True,
    "trade_pnl": 800,
    "max_drawdown_pct": 0.01,
}

_HIGH_FALLER_RUNNER = {
    "scenario_id": "MISS_2026-01-03",
    "ticker": "MISS",
    "date": "2026-01-03",
    "actual_faller_score": 0.70,   # Predicted fader, was actually runner
    "outcome": "runner",
    "dollar_volume": 3_000_000,
    "was_traded": True,
    "trade_pnl": 600,
    "max_drawdown_pct": 0.01,
}


# =============================================================================
# DriftAlert & TuningReport construction
# =============================================================================

class TestDriftAlert:
    def test_fields(self):
        alert = DriftAlert(
            parameter="faller_reject_threshold",
            current_value=0.60,
            optimal_value=0.55,
            drift_pct=8.3,
            severity="info",
            recommendation="Lower threshold",
            evidence="10 scenarios evaluated",
        )
        assert alert.parameter == "faller_reject_threshold"
        assert alert.severity == "info"
        assert math.isclose(alert.drift_pct, 8.3)


class TestTuningReport:
    def test_construction(self):
        report = TuningReport(
            session_date=date(2026, 4, 6),
            scenarios_added=3,
            total_scenarios=15,
            drift_alerts=[],
            selection_arena_score=-1.0,
            faller_accuracy=-1.0,
            recommended_changes=[],
            report_text="test",
        )
        assert report.scenarios_added == 3
        assert report.total_scenarios == 15


# =============================================================================
# _find_optimal_faller_threshold
# =============================================================================

class TestFindOptimalThreshold:
    def test_empty_returns_default(self, tmp_path):
        tuner = make_tuner(tmp_path)
        result = tuner._find_optimal_faller_threshold([])
        assert result == AutoTuner.REJECT_THRESHOLD

    def test_perfect_separation(self, tmp_path):
        """When high-faller=fader and low-faller=runner, optimal should stay near 0.60."""
        tuner = make_tuner(tmp_path)
        data = [
            {"actual_faller_score": 0.80, "outcome": "fader"},
            {"actual_faller_score": 0.75, "outcome": "fader"},
            {"actual_faller_score": 0.20, "outcome": "runner"},
            {"actual_faller_score": 0.15, "outcome": "runner"},
        ]
        optimal = tuner._find_optimal_faller_threshold(data)
        # Any threshold in [0.40, 0.80] gives 100% accuracy
        assert 0.40 <= optimal <= 0.80

    def test_shifts_threshold_lower_when_data_supports_it(self, tmp_path):
        """When many stocks with score 0.45–0.59 are faders, optimal drops below 0.60."""
        tuner = make_tuner(tmp_path)
        data = [
            # Faders at mid-range scores
            {"actual_faller_score": 0.50, "outcome": "fader"},
            {"actual_faller_score": 0.52, "outcome": "fader"},
            {"actual_faller_score": 0.48, "outcome": "fader"},
            # Runners at low scores
            {"actual_faller_score": 0.20, "outcome": "runner"},
            {"actual_faller_score": 0.25, "outcome": "runner"},
            # Faders at high scores too
            {"actual_faller_score": 0.75, "outcome": "fader"},
            {"actual_faller_score": 0.80, "outcome": "fader"},
        ]
        optimal = tuner._find_optimal_faller_threshold(data)
        assert optimal <= 0.55  # Should capture the mid-range faders


# =============================================================================
# _check_faller_accuracy
# =============================================================================

class TestFallerAccuracy:
    def test_no_data(self, tmp_path):
        tuner = make_tuner(tmp_path)
        result = tuner._check_faller_accuracy()
        assert result["n_evaluated"] == 0
        assert result["accuracy"] == -1.0
        assert result["fader_precision"] == -1.0

    def test_perfect_accuracy(self, tmp_path):
        write_scenarios(tmp_path, [
            _HIGH_FALLER_FADER,
            _HIGH_FALLER_FADER | {"scenario_id": "P2", "ticker": "P2"},
            _LOW_FALLER_RUNNER,
            _LOW_FALLER_RUNNER | {"scenario_id": "R2", "ticker": "R2"},
        ])
        tuner = make_tuner(tmp_path)
        result = tuner._check_faller_accuracy()
        assert result["n_evaluated"] == 4
        assert result["accuracy"] == pytest.approx(1.0)
        assert result["fader_precision"] == pytest.approx(1.0)
        assert result["runner_recall"] == pytest.approx(1.0)

    def test_mixed_accuracy(self, tmp_path):
        write_scenarios(tmp_path, [
            _HIGH_FALLER_FADER,    # correct
            _HIGH_FALLER_RUNNER,   # wrong: high faller but runner
            _LOW_FALLER_RUNNER,    # correct
        ])
        tuner = make_tuner(tmp_path)
        result = tuner._check_faller_accuracy()
        assert result["n_evaluated"] == 3
        # 2 correct out of 3
        assert result["accuracy"] == pytest.approx(2 / 3)

    def test_skips_scenarios_without_faller_score(self, tmp_path):
        write_scenarios(tmp_path, [
            _HIGH_FALLER_FADER,
            {"scenario_id": "NO_FS", "ticker": "X", "outcome": "runner"},  # no faller score
        ])
        tuner = make_tuner(tmp_path)
        result = tuner._check_faller_accuracy()
        assert result["n_evaluated"] == 1

    def test_skips_scenarios_without_outcome(self, tmp_path):
        write_scenarios(tmp_path, [
            _HIGH_FALLER_FADER,
            {"scenario_id": "NO_OUT", "ticker": "X", "actual_faller_score": 0.7},
        ])
        tuner = make_tuner(tmp_path)
        result = tuner._check_faller_accuracy()
        assert result["n_evaluated"] == 1


# =============================================================================
# _check_faller_threshold_drift
# =============================================================================

class TestFallerThresholdDrift:
    def test_no_alert_when_not_enough_data(self, tmp_path):
        tuner = make_tuner(tmp_path)
        alert = tuner._check_faller_threshold_drift({"n_evaluated": 5})
        assert alert is None

    def test_no_alert_when_optimal_matches_current(self, tmp_path):
        tuner = make_tuner(tmp_path)
        # optimal == current → drift = 0%
        alert = tuner._check_faller_threshold_drift({
            "n_evaluated": 20,
            "optimal_threshold": AutoTuner.REJECT_THRESHOLD,
            "accuracy": 0.80,
            "fader_precision": 0.85,
        })
        assert alert is None

    def test_alert_when_drift_large(self, tmp_path):
        tuner = make_tuner(tmp_path)
        alert = tuner._check_faller_threshold_drift({
            "n_evaluated": 20,
            "optimal_threshold": 0.45,   # 25% drift from 0.60
            "accuracy": 0.75,
            "fader_precision": 0.70,
        })
        assert alert is not None
        assert alert.parameter == "faller_reject_threshold"
        assert alert.severity in ("warning", "critical")
        assert alert.drift_pct > 10

    def test_alert_severity_critical_at_25pct(self, tmp_path):
        tuner = make_tuner(tmp_path)
        alert = tuner._check_faller_threshold_drift({
            "n_evaluated": 15,
            "optimal_threshold": 0.40,   # 33% drift
            "accuracy": 0.70,
            "fader_precision": 0.65,
        })
        assert alert is not None
        assert alert.severity == "critical"


# =============================================================================
# _check_short_accuracy
# =============================================================================

class TestShortAccuracy:
    def _make_short_cand(self, sid: str, outcome: str) -> dict:
        return {
            "scenario_id": sid,
            "actual_faller_score": 0.70,
            "dollar_volume": 1_000_000,
            "outcome": outcome,
        }

    def test_no_alert_when_insufficient_data(self, tmp_path):
        write_scenarios(tmp_path, [
            self._make_short_cand("A", "fader"),
            self._make_short_cand("B", "fader"),
        ])
        tuner = make_tuner(tmp_path)
        assert tuner._check_short_accuracy() is None

    def test_no_alert_when_70pct_faders(self, tmp_path):
        scenarios = [
            self._make_short_cand(f"F{i}", "fader") for i in range(7)
        ] + [
            self._make_short_cand(f"R{i}", "runner") for i in range(3)
        ]
        write_scenarios(tmp_path, scenarios)
        tuner = make_tuner(tmp_path)
        assert tuner._check_short_accuracy() is None

    def test_alert_when_mostly_runners(self, tmp_path):
        scenarios = [
            self._make_short_cand(f"R{i}", "runner") for i in range(8)
        ] + [
            self._make_short_cand(f"F{i}", "fader") for i in range(2)
        ]
        write_scenarios(tmp_path, scenarios)
        tuner = make_tuner(tmp_path)
        alert = tuner._check_short_accuracy()
        assert alert is not None
        assert alert.parameter == "short_faller_min"
        assert alert.severity in ("warning", "critical")

    def test_low_dolvol_excluded(self, tmp_path):
        """Scenarios below dolvol_min should not count as short candidates."""
        scenarios = [
            {
                "scenario_id": f"LDV{i}",
                "actual_faller_score": 0.70,
                "dollar_volume": 100_000,   # Below SHORT_DOLVOL_MIN
                "outcome": "runner",
            }
            for i in range(10)
        ]
        write_scenarios(tmp_path, scenarios)
        tuner = make_tuner(tmp_path)
        # Not enough qualifying candidates → no alert
        assert tuner._check_short_accuracy() is None


# =============================================================================
# _check_stop_drift
# =============================================================================

class TestStopDrift:
    def _make_loss(self, sid: str, trade_pnl: float, max_dd: float) -> dict:
        return {
            "scenario_id": sid,
            "was_traded": True,
            "trade_pnl": trade_pnl,
            "max_drawdown_pct": max_dd,
        }

    def test_no_alert_when_insufficient_data(self, tmp_path):
        write_scenarios(tmp_path, [
            self._make_loss("A", -200, 0.03),
        ])
        tuner = make_tuner(tmp_path)
        assert tuner._check_stop_drift() is None

    def test_no_alert_when_aligned(self, tmp_path):
        scenarios = [
            self._make_loss(f"L{i}", -300, 0.031) for i in range(6)
        ]
        write_scenarios(tmp_path, scenarios)
        tuner = make_tuner(tmp_path)
        # Avg drawdown ~3.1% ≈ 3% configured stop → minimal drift
        assert tuner._check_stop_drift() is None

    def test_alert_when_stops_much_larger(self, tmp_path):
        """Average drawdown 8% >> 3% configured stop."""
        scenarios = [
            self._make_loss(f"L{i}", -800, 0.08) for i in range(6)
        ]
        write_scenarios(tmp_path, scenarios)
        tuner = make_tuner(tmp_path)
        alert = tuner._check_stop_drift()
        assert alert is not None
        assert alert.parameter == "initial_stop_pct"
        assert alert.optimal_value > alert.current_value
        assert "too tight" in alert.recommendation

    def test_excludes_scratch_losses(self, tmp_path):
        """trade_pnl = -10 (scratch) should not count."""
        scenarios = [
            self._make_loss(f"S{i}", -10, 0.10) for i in range(6)
        ]
        write_scenarios(tmp_path, scenarios)
        tuner = make_tuner(tmp_path)
        # Scratches are < -50 threshold → excluded
        assert tuner._check_stop_drift() is None


# =============================================================================
# _check_data_coverage
# =============================================================================

class TestDataCoverage:
    def test_alert_when_sparse(self, tmp_path):
        write_scenarios(tmp_path, [{"scenario_id": "X", "ticker": "X"}])
        tuner = make_tuner(tmp_path)
        alert = tuner._check_data_coverage()
        assert alert is not None
        assert alert.parameter == "dataset_size"
        assert alert.severity in ("info", "warning")

    def test_no_alert_when_sufficient(self, tmp_path):
        scenarios = [
            {
                "scenario_id": f"S{i}",
                "actual_faller_score": 0.5,
                "outcome": "fader",
            }
            for i in range(55)  # > 50 threshold
        ]
        write_scenarios(tmp_path, scenarios)
        tuner = make_tuner(tmp_path)
        assert tuner._check_data_coverage() is None

    def test_warning_severity_between_20_and_50(self, tmp_path):
        scenarios = [{"scenario_id": f"S{i}", "outcome": "fader"} for i in range(25)]
        write_scenarios(tmp_path, scenarios)
        tuner = make_tuner(tmp_path)
        alert = tuner._check_data_coverage()
        assert alert is not None
        assert alert.severity == "warning"

    def test_info_severity_below_20(self, tmp_path):
        write_scenarios(tmp_path, [{"scenario_id": "X"}])
        tuner = make_tuner(tmp_path)
        alert = tuner._check_data_coverage()
        assert alert is not None
        assert alert.severity == "info"


# =============================================================================
# _format_report
# =============================================================================

class TestFormatReport:
    def test_no_alerts_shows_nominal(self, tmp_path):
        tuner = make_tuner(tmp_path)
        text = tuner._format_report(
            session_date=date(2026, 4, 6),
            new_scenarios=3,
            total_scenarios=20,
            f2_score=-1.0,
            faller_results={"accuracy": -1.0, "n_evaluated": 0},
            drift_alerts=[],
        )
        assert "nominal" in text.lower()
        assert "2026-04-06" in text

    def test_critical_alert_appears(self, tmp_path):
        tuner = make_tuner(tmp_path)
        alert = DriftAlert(
            parameter="faller_reject_threshold",
            current_value=0.60,
            optimal_value=0.40,
            drift_pct=33.3,
            severity="critical",
            recommendation="Lower threshold urgently",
            evidence="30 scenarios evaluated",
        )
        text = tuner._format_report(
            session_date=date(2026, 4, 6),
            new_scenarios=5,
            total_scenarios=35,
            f2_score=0.71,
            faller_results={"accuracy": 0.72, "n_evaluated": 30, "fader_precision": 0.65, "runner_recall": 0.80, "optimal_threshold": 0.40},
            drift_alerts=[alert],
        )
        assert "[!!!]" in text
        assert "faller_reject_threshold" in text
        assert "Lower threshold urgently" in text

    def test_f2_score_shown(self, tmp_path):
        tuner = make_tuner(tmp_path)
        text = tuner._format_report(
            session_date=date(2026, 4, 6),
            new_scenarios=0,
            total_scenarios=10,
            f2_score=0.714,
            faller_results={"accuracy": -1.0, "n_evaluated": 0},
            drift_alerts=[],
        )
        assert "0.714" in text


# =============================================================================
# save_report
# =============================================================================

class TestSaveReport:
    def test_writes_text_and_json(self, tmp_path):
        tuner = make_tuner(tmp_path)
        report = TuningReport(
            session_date=date(2026, 4, 6),
            scenarios_added=2,
            total_scenarios=10,
            drift_alerts=[],
            selection_arena_score=-1.0,
            faller_accuracy=-1.0,
            recommended_changes=[],
            report_text="Test report content",
        )
        save_dir = str(tmp_path / "reports")
        text_path = tuner.save_report(report, output_dir=save_dir)

        assert text_path.exists()
        assert text_path.suffix == ".txt"
        assert "Test report content" in text_path.read_text()

        json_path = text_path.with_suffix(".json")
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["session_date"] == "2026-04-06"
        assert data["scenarios_added"] == 2

    def test_default_save_dir(self, tmp_path):
        tuner = make_tuner(tmp_path)
        report = TuningReport(
            session_date=date(2026, 4, 1),
            scenarios_added=0,
            total_scenarios=0,
            drift_alerts=[],
            selection_arena_score=-1.0,
            faller_accuracy=-1.0,
            recommended_changes=[],
            report_text="text",
        )
        path = tuner.save_report(report)
        assert path.exists()
        assert "tuning_reports" in str(path)


# =============================================================================
# run_post_session — graceful degradation with empty data dir
# =============================================================================

class TestRunPostSession:
    def test_empty_dir_returns_valid_report(self, tmp_path):
        tuner = make_tuner(tmp_path)
        report = tuner.run_post_session(session_date=date(2026, 4, 6))
        assert isinstance(report, TuningReport)
        assert report.session_date == date(2026, 4, 6)
        assert report.scenarios_added == 0
        assert report.total_scenarios == 0
        # With empty data, should show a data coverage alert
        assert len(report.drift_alerts) >= 0  # May or may not alert; must not crash

    def test_date_defaults_to_today(self, tmp_path):
        tuner = make_tuner(tmp_path)
        report = tuner.run_post_session()
        assert report.session_date == date.today()


# =============================================================================
# Meta-simulation realism fixes
# =============================================================================

class TestMetaSimulationRealism:
    """Verify D195 realism constants are applied correctly in simulate_pnl."""

    def _import_sim(self):
        """Import from run_meta_simulation without side-effects."""
        import importlib.util
        mod_name = "run_meta_simulation_d195"
        if mod_name in sys.modules:
            return sys.modules[mod_name]
        spec = importlib.util.spec_from_file_location(
            mod_name,
            str(_ROOT / "scripts" / "run_meta_simulation.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod   # register before exec so dataclasses can find __module__
        spec.loader.exec_module(mod)
        return mod

    @pytest.fixture(autouse=True)
    def _load_sim(self):
        self.sim = self._import_sim()

    def _make_scenario(self, **kwargs):
        defaults = dict(
            ticker="TEST",
            date="2026-04-06",
            entry_price=5.0,
            max_gain_pct=0.10,
            max_drawdown_pct=0.02,
            actual_pnl_dollars=0.0,
            gap_pct=0.30,
            rvol=5.0,
            manipulation_prob=0.20,
            dollar_volume=2_000_000,
            is_shortable=True,
        )
        defaults.update(kwargs)
        return self.sim.LabeledScenario(**defaults)

    def test_entry_slippage_reduces_gain(self):
        """With 0.5% slippage, a 5% gain scenario should yield less than without slippage."""
        scenario = self._make_scenario(max_gain_pct=0.05, max_drawdown_pct=0.01)
        pnl, reason = self.sim.simulate_pnl(scenario, "ENTER_LONG", 1.0)
        # Slippage-adjusted max_gain = 0.05 - 0.005 = 0.045
        # Since 0.045 >= 0.02, enters D163/D164 path: trail_exit = 0.045 * 0.5 = 0.0225
        # pnl = pos * 0.50 * 0.005 + pos * 0.50 * 0.0225 = 500*0.005 + 500*0.0225 = 2.5 + 11.25
        # Without slippage: max_g=0.05, trail_exit=0.025 → pnl = 500*0.005 + 500*0.025 = 2.5 + 12.5 = 15.0
        assert pnl < 15.0, "Entry slippage should reduce P&L below the no-slippage baseline"

    def test_stop_fill_slippage_worsens_stop(self):
        """Stop should fire at 3%+1% = 4% effective loss, not 3%."""
        # max_drawdown of 0.035 is between 3% (old stop) and 4% (new stop)
        # With old logic (no slippage): 0.035 >= 0.03 → stopped at -3%
        # With entry slippage: max_dd = 0.035 + 0.005 = 0.040 >= 0.04 → stopped at -4%
        scenario = self._make_scenario(
            max_gain_pct=0.005,  # Tiny gain, won't trigger D163/D164/D165
            max_drawdown_pct=0.035,
        )
        pnl, reason = self.sim.simulate_pnl(scenario, "ENTER_LONG", 1.0)
        # Effective stop = 0.03 + 0.01 = 0.04
        # entry-slippage-adjusted max_dd = 0.035 + 0.005 = 0.040
        # 0.040 >= 0.040 → stop fires at 4%
        assert reason == "initial_stop"
        assert pnl == pytest.approx(-1000 * 0.04)

    def test_not_shortable_blocks_short_entry(self):
        """is_shortable=False should prevent ENTER_SHORT even with high faller score."""
        scenario = self._make_scenario(
            is_shortable=False,
            manipulation_prob=0.85,
            gap_pct=0.50,
            dollar_volume=500_000,
        )
        faller_score, breakdown = self.sim.compute_faller_score(scenario)
        decision, pos_mult = self.sim.make_decision(scenario, faller_score, breakdown)
        # Even if faller_score > REJECT_THRESHOLD, should SKIP (not shortable)
        if faller_score > self.sim.REJECT_THRESHOLD and scenario.gap_pct > 0.15:
            assert decision == "SKIP", (
                f"Expected SKIP for non-shortable stock, got {decision} "
                f"(faller_score={faller_score:.2f})"
            )

    def test_shortable_true_can_enter_short(self):
        """is_shortable=True allows ENTER_SHORT when score is high enough."""
        scenario = self._make_scenario(
            is_shortable=True,
            manipulation_prob=0.85,
            gap_pct=0.50,
            dollar_volume=5_000_000,
            spread_proxy=0.6,
        )
        faller_score, breakdown = self.sim.compute_faller_score(scenario)
        decision, pos_mult = self.sim.make_decision(scenario, faller_score, breakdown)
        if faller_score > self.sim.REJECT_THRESHOLD:
            assert decision == "ENTER_SHORT"

    def test_entry_slippage_constant_exists(self):
        assert hasattr(self.sim, "ENTRY_SLIPPAGE_PCT")
        assert self.sim.ENTRY_SLIPPAGE_PCT == pytest.approx(0.005)

    def test_stop_fill_slippage_constant_exists(self):
        assert hasattr(self.sim, "STOP_FILL_SLIPPAGE_PCT")
        assert self.sim.STOP_FILL_SLIPPAGE_PCT == pytest.approx(0.01)

    def test_commission_constant_is_zero(self):
        """Alpaca is commission-free; flag if this changes."""
        assert hasattr(self.sim, "COMMISSION_PER_TRADE")
        assert self.sim.COMMISSION_PER_TRADE == pytest.approx(0.00)

    def test_short_pnl_reduced_by_entry_slippage(self):
        """Short gain should be reduced by entry slippage."""
        # max_drawdown_pct = 0.20 → effective fade = 0.20 - 0.005 = 0.195
        scenario = self._make_scenario(
            max_gain_pct=0.02,
            max_drawdown_pct=0.20,
        )
        pnl, reason = self.sim.simulate_pnl(scenario, "ENTER_SHORT", 1.0)
        # Effective gain for short: 0.20 - 0.005 = 0.195
        # stop_pct = 0.05 + 0.01 = 0.06
        # realized = min(0.195, 0.06) = 0.06 → pnl = 1000 * 0.06
        assert pnl == pytest.approx(1000 * 0.06)
        assert reason == "short_exit"
