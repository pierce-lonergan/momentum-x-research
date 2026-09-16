"""
D195: Continuous Arena Auto-Tuning.

Runs automatically after each trading session to:
1. Ingest new trading data into arena datasets via AutoLabeler
2. Re-run selection arena with expanded data
3. Detect parameter drift from optimal
4. Generate tuning recommendations

Usage (via CLI):
    python scripts/post_session_tuner.py --date 2026-04-06
    python scripts/post_session_tuner.py --weekly
    python scripts/post_session_tuner.py --alerts
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger(__name__)


# =============================================================================
# Data contracts
# =============================================================================

@dataclass
class DriftAlert:
    """A single parameter drift detection result."""

    parameter: str
    current_value: float
    optimal_value: float
    drift_pct: float
    severity: str           # "info", "warning", "critical"
    recommendation: str
    evidence: str           # What data supports this


@dataclass
class TuningReport:
    """Full post-session tuning report."""

    session_date: date
    scenarios_added: int
    total_scenarios: int
    drift_alerts: list[DriftAlert]
    selection_arena_score: float    # F2 score; -1 if unavailable
    faller_accuracy: float          # fraction correctly predicted; -1 if unavailable
    recommended_changes: list[dict]
    report_text: str


# =============================================================================
# AutoTuner
# =============================================================================

class AutoTuner:
    """
    Post-session parameter drift detector and tuning advisor.

    Mirrors production constants from run_meta_simulation.py so that drift
    can be measured against what is actually deployed.
    """

    # ── Production parameters (mirrored from run_meta_simulation.py) ────────
    REJECT_THRESHOLD = 0.60
    REDUCE_HALF_THRESHOLD = 0.50
    REDUCE_PARTIAL_THRESHOLD = 0.30
    STOP_THRESHOLD_PCT = 0.03       # 3% initial stop on longs
    SHORT_STOP_PCT = 0.35           # 35% stop on shorts (D161)
    SHORT_FALLER_MIN = 0.65         # Min faller score to qualify for short
    SHORT_DOLVOL_MIN = 500_000      # Min dollar volume for short (D161)

    # ── Drift thresholds ────────────────────────────────────────────────────
    _MIN_SCENARIOS_FOR_DRIFT = 10   # Need at least this many to flag drift
    _INFO_DRIFT_PCT = 5.0
    _WARNING_DRIFT_PCT = 15.0
    _CRITICAL_DRIFT_PCT = 25.0

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)
        self._scenarios_cache: list[dict] | None = None

    # =========================================================================
    # Public API
    # =========================================================================

    def run_post_session(self, session_date: date | None = None) -> TuningReport:
        """Run complete post-session tuning pipeline."""
        if session_date is None:
            session_date = date.today()

        logger.info("[AutoTuner] Starting post-session pipeline for %s", session_date)

        new_scenarios = self._ingest_session(session_date)
        logger.info("[AutoTuner] Ingested %d new scenarios", new_scenarios)

        selection_results = self._run_selection_arena()
        faller_results = self._check_faller_accuracy()
        drift_alerts = self._detect_drift(faller_results)

        return self._generate_report(
            session_date=session_date,
            new_scenarios=new_scenarios,
            selection_results=selection_results,
            faller_results=faller_results,
            drift_alerts=drift_alerts,
        )

    def run_weekly_optimization(self) -> TuningReport:
        """Run full weekly optimization across all arenas."""
        logger.info("[AutoTuner] Running weekly optimization")
        faller_results = self._check_faller_accuracy()
        drift_alerts = self._detect_drift(faller_results)

        sweep_alerts = self._run_weekly_sweep()
        existing_params = {a.parameter for a in drift_alerts}
        for alert in sweep_alerts:
            if alert.parameter not in existing_params:
                drift_alerts.append(alert)

        selection_results = self._run_selection_arena()
        return self._generate_report(
            session_date=date.today(),
            new_scenarios=0,
            selection_results=selection_results,
            faller_results=faller_results,
            drift_alerts=drift_alerts,
        )

    def get_active_alerts(self) -> list[DriftAlert]:
        """Return only warning/critical alerts. Useful for --alerts mode."""
        faller_results = self._check_faller_accuracy()
        all_alerts = self._detect_drift(faller_results)
        return [a for a in all_alerts if a.severity in ("warning", "critical")]

    def save_report(self, report: TuningReport, output_dir: str | None = None) -> Path:
        """Save a TuningReport as both text and JSON."""
        if output_dir is None:
            output_dir = str(self._data_dir / "tuning_reports")
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        date_str = report.session_date.isoformat()

        text_path = out / f"tuning_{date_str}.txt"
        text_path.write_text(report.report_text, encoding="utf-8")

        json_path = out / f"tuning_{date_str}.json"
        json_path.write_text(
            json.dumps(self._report_to_dict(report), indent=2),
            encoding="utf-8",
        )

        logger.info("[AutoTuner] Report saved: %s", text_path)
        return text_path

    # =========================================================================
    # Step 1: Ingest session data
    # =========================================================================

    def _ingest_session(self, session_date: date) -> int:
        """Add today's trades to the labeled dataset via AutoLabeler."""
        try:
            from src.llm_arena.auto_labeler import AutoLabeler
        except ImportError:
            logger.warning("[AutoTuner] AutoLabeler not available — skipping ingest")
            return 0

        labeler = AutoLabeler(str(self._data_dir))
        try:
            all_scenarios = labeler.run_full_pipeline()
        except Exception as exc:
            logger.warning("[AutoTuner] AutoLabeler pipeline failed: %s", exc)
            return 0

        date_str = session_date.isoformat()
        today_scenarios = [
            s for s in all_scenarios
            if self._scenario_date_str(s) == date_str
        ]

        dataset_path = self._data_dir / "labeled_scenarios.jsonl"
        existing_ids = self._load_existing_ids(dataset_path)

        new_count = 0
        with open(dataset_path, "a", encoding="utf-8") as f:
            for scenario in today_scenarios:
                sid = getattr(scenario, "scenario_id", "")
                if sid and sid not in existing_ids:
                    f.write(json.dumps(self._scenario_to_dict(scenario)) + "\n")
                    existing_ids.add(sid)
                    new_count += 1

        # Invalidate cache since we wrote new data
        self._scenarios_cache = None
        return new_count

    def _scenario_date_str(self, scenario) -> str:
        d = getattr(scenario, "date", None)
        if d is None:
            return ""
        return d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]

    def _load_existing_ids(self, path: Path) -> set[str]:
        ids: set[str] = set()
        if not path.exists():
            return ids
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ids.add(json.loads(line).get("scenario_id", ""))
                except json.JSONDecodeError:
                    pass
        return ids

    def _scenario_to_dict(self, scenario) -> dict:
        """Convert a LabeledScenario to a JSON-serializable dict."""
        FIELDS = [
            "scenario_id", "ticker", "gap_pct", "rvol", "open_price",
            "close_price", "high_price", "low_price", "dollar_volume",
            "max_gain_pct", "max_drawdown_pct", "close_pct",
            "was_traded", "trade_pnl", "trade_direction",
            "actual_faller_score", "outcome", "catalyst_type",
            "correct_signal", "label_confidence", "label_source",
        ]
        result: dict = {}
        for attr in FIELDS:
            val = getattr(scenario, attr, None)
            if val is None:
                continue
            if hasattr(val, "value"):       # enum
                val = val.value
            elif hasattr(val, "isoformat"):  # date/datetime
                val = val.isoformat()
            result[attr] = val

        d = getattr(scenario, "date", None)
        if d is not None:
            result["date"] = d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]

        return result

    # =========================================================================
    # Step 2: Selection arena
    # =========================================================================

    def _run_selection_arena(self) -> dict:
        """Re-run selection arena with current data. Returns F2 score + raw output."""
        script = _ROOT / "scripts" / "run_selection_arena.py"
        if not script.exists():
            return {"f2_score": -1.0, "error": "script not found"}

        try:
            result = subprocess.run(
                [
                    sys.executable, str(script),
                    "--all-dates", "--profiles", "current", "--no-day-detail",
                ],
                capture_output=True, text=True, timeout=120, cwd=str(_ROOT),
            )
            if result.returncode == 0:
                return {
                    "f2_score": self._parse_f2_from_output(result.stdout),
                    "output": result.stdout,
                }
            logger.warning(
                "[AutoTuner] Selection arena non-zero exit: %s", result.stderr[:200]
            )
            return {"f2_score": -1.0, "error": result.stderr[:200]}
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.warning("[AutoTuner] Selection arena unavailable: %s", exc)
            return {"f2_score": -1.0, "error": str(exc)}

    def _parse_f2_from_output(self, output: str) -> float:
        """Extract F2 score from selection arena text output."""
        import re
        for line in output.splitlines():
            if "f2" in line.lower() or "f-score" in line.lower():
                matches = re.findall(r"\d+\.\d+", line)
                if matches:
                    try:
                        return float(matches[-1])
                    except ValueError:
                        pass
        return -1.0

    # =========================================================================
    # Step 3: Faller accuracy
    # =========================================================================

    def _check_faller_accuracy(self) -> dict:
        """
        Check how accurately the faller score predicts actual outcomes.

        For each scenario with both a faller score and a known outcome:
          - High faller (>= REJECT_THRESHOLD) should → FADER
          - Low faller (< REJECT_THRESHOLD) should → RUNNER
        """
        scenarios = self._load_labeled_scenarios()
        evaluated = [
            s for s in scenarios
            if s.get("actual_faller_score") is not None
            and s.get("outcome") is not None
        ]

        if not evaluated:
            return {
                "n_evaluated": 0,
                "correct": 0,
                "accuracy": -1.0,
                "fader_precision": -1.0,
                "runner_recall": -1.0,
                "high_faller_total": 0,
                "high_faller_faders": 0,
                "low_faller_total": 0,
                "optimal_threshold": self.REJECT_THRESHOLD,
            }

        correct = 0
        high_faller_faders = 0
        high_faller_total = 0
        low_faller_runners = 0
        low_faller_total = 0

        for s in evaluated:
            fs = float(s["actual_faller_score"])
            outcome = s["outcome"]
            is_high = fs >= self.REJECT_THRESHOLD
            is_fader = outcome == "fader"
            is_runner = outcome == "runner"

            if is_high:
                high_faller_total += 1
                if is_fader:
                    high_faller_faders += 1
                    correct += 1
            else:
                low_faller_total += 1
                if is_runner:
                    low_faller_runners += 1
                    correct += 1

        n = len(evaluated)
        return {
            "n_evaluated": n,
            "correct": correct,
            "accuracy": correct / n,
            "fader_precision": (
                high_faller_faders / high_faller_total if high_faller_total > 0 else -1.0
            ),
            "runner_recall": (
                low_faller_runners / low_faller_total if low_faller_total > 0 else -1.0
            ),
            "high_faller_total": high_faller_total,
            "high_faller_faders": high_faller_faders,
            "low_faller_total": low_faller_total,
            "optimal_threshold": self._find_optimal_faller_threshold(evaluated),
        }

    def _find_optimal_faller_threshold(self, evaluated: list[dict]) -> float:
        """
        Grid-search the faller threshold that maximises accuracy on labeled data.
        Searches 0.40 – 0.80 in 0.05 steps.
        """
        if not evaluated:
            return self.REJECT_THRESHOLD

        best_threshold = self.REJECT_THRESHOLD
        best_accuracy = -1.0

        for threshold_int in range(40, 85, 5):
            threshold = threshold_int / 100.0
            correct = sum(
                1 for s in evaluated
                if (
                    (float(s["actual_faller_score"]) >= threshold and s["outcome"] == "fader")
                    or (float(s["actual_faller_score"]) < threshold and s["outcome"] == "runner")
                )
            )
            acc = correct / len(evaluated)
            if acc > best_accuracy:
                best_accuracy = acc
                best_threshold = threshold

        return best_threshold

    # =========================================================================
    # Step 4: Drift detection
    # =========================================================================

    def _detect_drift(self, faller_results: dict) -> list[DriftAlert]:
        """Check all parameters for drift from optimal."""
        alerts: list[DriftAlert] = []

        alert = self._check_faller_threshold_drift(faller_results)
        if alert:
            alerts.append(alert)

        alert = self._check_short_accuracy()
        if alert:
            alerts.append(alert)

        alert = self._check_stop_drift()
        if alert:
            alerts.append(alert)

        alert = self._check_data_coverage()
        if alert:
            alerts.append(alert)

        return alerts

    def _check_parameter(
        self,
        name: str,
        current: float,
        optimal: float | None,
        threshold_pct: float = 10.0,
        evidence: str = "",
    ) -> Optional[DriftAlert]:
        """Check a single parameter for drift. Returns None if within tolerance."""
        if optimal is None:
            return None
        drift = abs(current - optimal) / max(abs(optimal), 0.001) * 100
        if drift <= threshold_pct:
            return None
        severity = (
            "critical" if drift > self._CRITICAL_DRIFT_PCT
            else "warning" if drift > self._WARNING_DRIFT_PCT
            else "info"
        )
        return DriftAlert(
            parameter=name,
            current_value=current,
            optimal_value=optimal,
            drift_pct=drift,
            severity=severity,
            recommendation=f"Consider updating {name} from {current:.4g} to {optimal:.4g}",
            evidence=evidence or f"Drift of {drift:.1f}% detected",
        )

    def _check_faller_threshold_drift(self, faller_results: dict) -> Optional[DriftAlert]:
        """Check if the faller rejection threshold has drifted from optimal."""
        n = faller_results.get("n_evaluated", 0)
        if n < self._MIN_SCENARIOS_FOR_DRIFT:
            return None

        optimal = faller_results.get("optimal_threshold", self.REJECT_THRESHOLD)
        current = self.REJECT_THRESHOLD
        accuracy = faller_results.get("accuracy", -1.0)
        fader_prec = faller_results.get("fader_precision", -1.0)

        evidence = (
            f"Evaluated {n} labeled scenarios. "
            f"Accuracy at {current:.0%}: {accuracy:.1%}. "
            f"Fader precision: {fader_prec:.1%}. "
            f"Optimal threshold from grid search: {optimal:.0%}."
        )

        alert = self._check_parameter(
            name="faller_reject_threshold",
            current=current,
            optimal=optimal,
            threshold_pct=5.0,
            evidence=evidence,
        )
        if alert:
            alert.recommendation = (
                f"Consider updating REJECT_THRESHOLD from {current:.0%} to {optimal:.0%} "
                f"in run_meta_simulation.py and src/execution/faller_detection.py"
            )
        return alert

    def _check_short_accuracy(self) -> Optional[DriftAlert]:
        """
        Check whether high-faller, liquid candidates are actually fading.
        Target: >= 70% of short candidates should be actual faders.
        """
        scenarios = self._load_labeled_scenarios()
        short_candidates = [
            s for s in scenarios
            if s.get("actual_faller_score", 0) >= self.SHORT_FALLER_MIN
            and s.get("dollar_volume", 0) >= self.SHORT_DOLVOL_MIN
            and s.get("outcome") is not None
        ]

        if len(short_candidates) < 5:
            return None

        faders = sum(1 for s in short_candidates if s.get("outcome") == "fader")
        fader_rate = faders / len(short_candidates)

        if fader_rate >= 0.70:
            return None

        drift_pct = (0.70 - fader_rate) / 0.70 * 100
        severity = "critical" if fader_rate < 0.50 else "warning"
        new_min = round(min(0.80, self.SHORT_FALLER_MIN + 0.05), 2)

        return DriftAlert(
            parameter="short_faller_min",
            current_value=self.SHORT_FALLER_MIN,
            optimal_value=new_min,
            drift_pct=drift_pct,
            severity=severity,
            recommendation=(
                f"Short fader rate is only {fader_rate:.1%} (target >=70%). "
                f"Raise SHORT_FALLER_MIN from {self.SHORT_FALLER_MIN:.0%} to {new_min:.0%}."
            ),
            evidence=(
                f"{len(short_candidates)} short candidates (faller>={self.SHORT_FALLER_MIN:.0%}, "
                f"dolvol>=${self.SHORT_DOLVOL_MIN/1e6:.1f}M) evaluated; "
                f"{faders} were actual faders ({fader_rate:.1%})."
            ),
        )

    def _check_stop_drift(self) -> Optional[DriftAlert]:
        """
        Compare the configured 3% stop against actual realized drawdowns on losses.
        Flags if there is a >20% gap between them.
        """
        scenarios = self._load_labeled_scenarios()
        losses = [
            s for s in scenarios
            if s.get("was_traded")
            and s.get("trade_pnl") is not None
            and s["trade_pnl"] < -50          # Meaningful loss (not a tiny scratch)
            and s.get("max_drawdown_pct") is not None
        ]

        if len(losses) < 5:
            return None

        avg_dd = sum(abs(s["max_drawdown_pct"]) for s in losses) / len(losses)

        if abs(avg_dd - self.STOP_THRESHOLD_PCT) < 0.01:
            return None

        drift_pct = abs(avg_dd - self.STOP_THRESHOLD_PCT) / self.STOP_THRESHOLD_PCT * 100
        if drift_pct < 20.0:
            return None

        direction = "too tight" if avg_dd > self.STOP_THRESHOLD_PCT else "too wide"
        severity = "critical" if drift_pct > 50 else "warning"

        return DriftAlert(
            parameter="initial_stop_pct",
            current_value=self.STOP_THRESHOLD_PCT,
            optimal_value=round(avg_dd, 2),
            drift_pct=drift_pct,
            severity=severity,
            recommendation=(
                f"Stop distance appears {direction}. "
                f"Average realized drawdown on losses: {avg_dd:.1%}. "
                f"Consider adjusting from {self.STOP_THRESHOLD_PCT:.0%} to ~{avg_dd:.1%}."
            ),
            evidence=(
                f"{len(losses)} stopped-out trades analyzed. "
                f"Avg max drawdown: {avg_dd:.1%} vs configured stop {self.STOP_THRESHOLD_PCT:.0%}."
            ),
        )

    def _check_data_coverage(self) -> Optional[DriftAlert]:
        """
        Alert if the labeled dataset is too small for reliable drift detection.
        Minimum target: 50 scenarios, 30 with known outcome.
        """
        scenarios = self._load_labeled_scenarios()
        n = len(scenarios)
        with_outcome = sum(1 for s in scenarios if s.get("outcome"))
        with_faller = sum(1 for s in scenarios if s.get("actual_faller_score") is not None)

        if n >= 50 and with_outcome >= 30:
            return None

        drift_pct = max(0.0, (50 - n) / 50 * 100)
        severity = "warning" if n >= 20 else "info"

        return DriftAlert(
            parameter="dataset_size",
            current_value=float(n),
            optimal_value=50.0,
            drift_pct=drift_pct,
            severity=severity,
            recommendation=(
                f"Only {n} labeled scenarios ({with_outcome} with outcome, "
                f"{with_faller} with faller score). "
                f"Drift detection needs >=50 scenarios for reliability. "
                f"Accumulate data over the next ~{max(0, 50 - n)} sessions."
            ),
            evidence=(
                f"Dataset: {n} total, {with_outcome} with outcome, {with_faller} with faller score."
            ),
        )

    # =========================================================================
    # Step 5: Report generation
    # =========================================================================

    def _generate_report(
        self,
        session_date: date,
        new_scenarios: int,
        selection_results: dict,
        faller_results: dict,
        drift_alerts: list[DriftAlert],
    ) -> TuningReport:
        total_scenarios = len(self._load_labeled_scenarios())
        f2_score = selection_results.get("f2_score", -1.0)
        faller_accuracy = faller_results.get("accuracy", -1.0)

        recommended_changes = [
            {
                "parameter": a.parameter,
                "current": a.current_value,
                "recommended": a.optimal_value,
                "severity": a.severity,
                "action": a.recommendation,
            }
            for a in drift_alerts
            if a.severity in ("warning", "critical")
        ]

        report_text = self._format_report(
            session_date=session_date,
            new_scenarios=new_scenarios,
            total_scenarios=total_scenarios,
            f2_score=f2_score,
            faller_results=faller_results,
            drift_alerts=drift_alerts,
        )

        return TuningReport(
            session_date=session_date,
            scenarios_added=new_scenarios,
            total_scenarios=total_scenarios,
            drift_alerts=drift_alerts,
            selection_arena_score=f2_score,
            faller_accuracy=faller_accuracy,
            recommended_changes=recommended_changes,
            report_text=report_text,
        )

    def _format_report(
        self,
        session_date: date,
        new_scenarios: int,
        total_scenarios: int,
        f2_score: float,
        faller_results: dict,
        drift_alerts: list[DriftAlert],
    ) -> str:
        SEP = "=" * 60
        lines = [
            SEP,
            f"  D195 POST-SESSION TUNING REPORT — {session_date}",
            SEP,
            "",
            f"  Dataset:    +{new_scenarios} new scenarios  (total: {total_scenarios})",
        ]

        if f2_score >= 0:
            lines.append(f"  Arena F2:   {f2_score:.3f}")
        else:
            lines.append("  Arena F2:   N/A (selection arena unavailable)")

        faller_acc = faller_results.get("accuracy", -1.0)
        n_eval = faller_results.get("n_evaluated", 0)
        if faller_acc >= 0 and n_eval > 0:
            lines.append(f"  Faller Acc: {faller_acc:.1%}  ({n_eval} evaluated)")
            prec = faller_results.get("fader_precision", -1.0)
            recall = faller_results.get("runner_recall", -1.0)
            if prec >= 0:
                lines.append(f"  Fader Prec: {prec:.1%}   Runner Recall: {recall:.1%}")
            opt = faller_results.get("optimal_threshold", self.REJECT_THRESHOLD)
            lines.append(
                f"  Opt thresh: {opt:.0%}  (current: {self.REJECT_THRESHOLD:.0%})"
            )
        else:
            lines.append("  Faller Acc: N/A (insufficient labeled data)")

        lines.append("")

        _SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
        sorted_alerts = sorted(drift_alerts, key=lambda a: _SEVERITY_ORDER[a.severity])

        if not sorted_alerts:
            lines.append("  DRIFT STATUS: No drift detected — all parameters nominal.")
        else:
            lines.append(f"  DRIFT ALERTS ({len(sorted_alerts)}):")
            for alert in sorted_alerts:
                icon = {"critical": "[!!!]", "warning": "[!]  ", "info": "[i]  "}[alert.severity]
                lines.append(
                    f"    {icon} {alert.parameter}: "
                    f"{alert.current_value:.4g} → {alert.optimal_value:.4g} "
                    f"({alert.drift_pct:.1f}% drift)"
                )
                # Wrap recommendation at 70 chars
                rec = alert.recommendation
                while len(rec) > 68:
                    cut = rec[:68].rfind(" ")
                    if cut < 0:
                        cut = 68
                    lines.append(f"           {rec[:cut]}")
                    rec = rec[cut:].lstrip()
                if rec:
                    lines.append(f"           {rec}")

        lines += ["", SEP]
        return "\n".join(lines)

    # =========================================================================
    # Weekly sweep helper
    # =========================================================================

    def _run_weekly_sweep(self) -> list[DriftAlert]:
        """Run short_param_sweep.py and extract any parameter change signals."""
        script = _ROOT / "scripts" / "short_param_sweep.py"
        if not script.exists():
            return []
        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True, text=True, timeout=60, cwd=str(_ROOT),
            )
            if result.returncode != 0:
                logger.warning("[AutoTuner] Short param sweep error: %s", result.stderr[:100])
        except Exception as exc:
            logger.warning("[AutoTuner] Weekly sweep failed: %s", exc)
        # Sweep is currently a static analysis report; no programmatic recommendations yet.
        return []

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _load_labeled_scenarios(self) -> list[dict]:
        """Load all labeled scenarios from the JSONL dataset (cached)."""
        if self._scenarios_cache is not None:
            return self._scenarios_cache

        path = self._data_dir / "labeled_scenarios.jsonl"
        scenarios: list[dict] = []
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        scenarios.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

        self._scenarios_cache = scenarios
        return scenarios

    def _report_to_dict(self, report: TuningReport) -> dict:
        return {
            "session_date": report.session_date.isoformat(),
            "scenarios_added": report.scenarios_added,
            "total_scenarios": report.total_scenarios,
            "selection_arena_score": report.selection_arena_score,
            "faller_accuracy": report.faller_accuracy,
            "drift_alerts": [
                {
                    "parameter": a.parameter,
                    "current_value": a.current_value,
                    "optimal_value": a.optimal_value,
                    "drift_pct": a.drift_pct,
                    "severity": a.severity,
                    "recommendation": a.recommendation,
                    "evidence": a.evidence,
                }
                for a in report.drift_alerts
            ],
            "recommended_changes": report.recommended_changes,
        }
