"""Tests for the LLM Arena Report Generator (Component 5).

Node ID: tests.unit.test_llm_arena_report
Graph Link: tested_by → src.llm_arena.report

Tests:
  1.  test_scorecard_report_contains_accuracy
  2.  test_scorecard_report_contains_confusion_matrix
  3.  test_scorecard_report_contains_operational
  4.  test_scorecard_report_contains_financial
  5.  test_experiment_report_contains_winner
  6.  test_experiment_report_contains_significance
  7.  test_experiment_report_contains_recommendation
  8.  test_comparison_table_format
  9.  test_confusion_matrix_display
  10. test_calibration_chart
  11. test_significance_stars
  12. test_format_pct_positive
  13. test_format_pct_negative
  14. test_save_markdown
  15. test_dataset_quality_report
  16. test_report_handles_empty_results
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timezone

import pytest

from src.llm_arena.experiment import ExperimentConfig, ExperimentResult, SignificanceResult
from src.llm_arena.harness import AgentConfig, AgentRunResult
from src.llm_arena.models import (
    CatalystType,
    CorrectSignal,
    LabelConfidence,
    LabeledScenario,
    StockOutcome,
)
from src.llm_arena.report import ReportGenerator
from src.llm_arena.scoring import (
    AgentScorecard,
    ClassificationMetrics,
    FinancialMetrics,
    MetricsCalculator,
    OperationalMetrics,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_config(agent_type: str = "news", model_id: str = "haiku") -> AgentConfig:
    return AgentConfig(agent_type=agent_type, model_id=model_id)


def _make_scenario(
    ticker: str = "TEST",
    outcome: StockOutcome = StockOutcome.RUNNER,
    correct_signal: CorrectSignal = CorrectSignal.BULL,
    catalyst_type: CatalystType = CatalystType.FDA,
    trade_pnl: float | None = 500.0,
    max_gain_pct: float | None = 0.25,
    max_drawdown_pct: float | None = -0.10,
    scenario_id: str | None = None,
) -> LabeledScenario:
    d = date(2026, 1, 15)
    sid = scenario_id or f"{ticker}_{d}"
    return LabeledScenario(
        ticker=ticker,
        date=d,
        scenario_id=sid,
        gap_pct=0.30,
        rvol=5.0,
        dollar_volume=2_000_000.0,
        open_price=10.0,
        prev_close=8.0,
        catalyst_type=catalyst_type,
        outcome=outcome,
        max_gain_pct=max_gain_pct,
        max_drawdown_pct=max_drawdown_pct,
        trade_pnl=trade_pnl,
        correct_signal=correct_signal,
        label_confidence=LabelConfidence.AUTO_HIGH,
    )


def _make_result(
    scenario_id: str = "TEST_2026-01-15",
    direction: str = "BULL",
    confidence: float | None = 0.75,
    parse_success: bool = True,
    timed_out: bool = False,
    latency_ms: float = 1000.0,
    agent_type: str = "news",
    model_id: str = "haiku",
) -> AgentRunResult:
    return AgentRunResult(
        scenario_id=scenario_id,
        agent_config=_make_config(agent_type=agent_type, model_id=model_id),
        signal_direction=direction,
        signal_confidence=confidence,
        parse_success=parse_success,
        timed_out=timed_out,
        latency_ms=latency_ms,
        tokens_input=500,
        tokens_output=100,
        timestamp=datetime.now(timezone.utc),
    )


def _make_classification_metrics(
    direction_accuracy: float = 0.55,
    catalyst_accuracy: float = 0.40,
    tp: int = 10,
    fp: int = 5,
    tn: int = 8,
    fn: int = 3,
    precision: float = 0.667,
    recall: float = 0.769,
    f1: float = 0.714,
    cal_error: float = 0.08,
    overconf: float = 0.35,
    acc_runners: float = 0.769,
    acc_faders: float = 0.615,
) -> ClassificationMetrics:
    return ClassificationMetrics(
        direction_accuracy=direction_accuracy,
        catalyst_accuracy=catalyst_accuracy,
        true_positives=tp,
        true_negatives=tn,
        false_positives=fp,
        false_negatives=fn,
        precision=precision,
        recall=recall,
        f1_score=f1,
        calibration_error=cal_error,
        overconfidence_rate=overconf,
        accuracy_on_runners=acc_runners,
        accuracy_on_faders=acc_faders,
    )


def _make_operational_metrics(
    timeout_rate: float = 0.02,
    parse_success_rate: float = 0.98,
    median_latency_ms: float = 1200.0,
    p95_latency_ms: float = 2500.0,
    p99_latency_ms: float = 4000.0,
) -> OperationalMetrics:
    return OperationalMetrics(
        timeout_rate=timeout_rate,
        parse_success_rate=parse_success_rate,
        median_latency_ms=median_latency_ms,
        p95_latency_ms=p95_latency_ms,
        p99_latency_ms=p99_latency_ms,
        total_tokens_input=50000,
        total_tokens_output=10000,
        avg_tokens_per_call=600.0,
    )


def _make_financial_metrics(
    signal_value: float = 12500.0,
    cost_per_signal: float = 0.0012,
    roi: float = 42.5,
    avoided_losses: float = 4000.0,
    captured_gains: float = 8500.0,
) -> FinancialMetrics:
    return FinancialMetrics(
        signal_value=signal_value,
        cost_per_signal=cost_per_signal,
        roi=roi,
        avoided_losses=avoided_losses,
        captured_gains=captured_gains,
    )


def _make_scorecard(
    agent_type: str = "news",
    model_id: str = "haiku",
    scenario_count: int = 26,
    direction_accuracy: float = 0.55,
) -> AgentScorecard:
    return AgentScorecard(
        agent_type=agent_type,
        config={"agent_type": agent_type, "model_id": model_id},
        scenario_count=scenario_count,
        classification=_make_classification_metrics(direction_accuracy=direction_accuracy),
        operational=_make_operational_metrics(),
        financial=_make_financial_metrics(),
        accuracy_by_catalyst={
            "fda": 0.70,
            "earnings": 0.45,
            "pharma_deal": 0.30,
        },
        accuracy_by_outcome={
            "runner": 0.769,
            "fader": 0.615,
        },
    )


def _make_significance(
    variant_name: str = "fundamental",
    baseline_mean: float = 0.306,
    variant_mean: float = 0.552,
    delta: float = 0.246,
    p_value: float = 0.0001,
    effect_size: float = 0.51,
    significant: bool = True,
) -> SignificanceResult:
    return SignificanceResult(
        variant_name=variant_name,
        metric="direction_accuracy",
        baseline_mean=baseline_mean,
        variant_mean=variant_mean,
        delta=delta,
        delta_pct=delta / baseline_mean if baseline_mean > 0 else 0.0,
        p_value=p_value,
        significant=significant,
        confidence_interval=(0.15, 0.35),
        effect_size=effect_size,
    )


def _make_experiment_result(
    winner: str = "fundamental",
    significant: bool = True,
    p_value: float = 0.0001,
) -> ExperimentResult:
    baseline_cfg = _make_config("news", "haiku")
    variant_cfg = _make_config("fundamental", "sonnet")
    exp_config = ExperimentConfig(
        name="replay_validation",
        description="Compare fundamental vs news agent on replay data",
        baseline=baseline_cfg,
        variants=[variant_cfg],
        mode="replay",
        bootstrap_iterations=1000,
        confidence_level=0.95,
    )
    baseline_sc = _make_scorecard("news", "haiku", direction_accuracy=0.306)
    variant_sc = _make_scorecard("fundamental", "sonnet", direction_accuracy=0.552)
    sig = _make_significance(
        variant_name="fundamental",
        baseline_mean=0.306,
        variant_mean=0.552,
        delta=0.246,
        p_value=p_value,
        significant=significant,
    )

    if winner == "fundamental" and significant:
        recommendation = (
            "Variant 'fundamental' wins over baseline 'news':\n"
            "  direction_accuracy: 30.6% -> 55.2% (+24.6%, p=0.000)\n"
            "  95% CI of delta: [+15.0%, +35.0%]\n"
            "  Effect size (Cohen's d): 0.510"
        )
    else:
        winner = "baseline"
        recommendation = (
            "No variant achieved statistical significance at 95% confidence level "
            "(alpha=0.05). Baseline 'news' recommended (conservative default)."
        )

    return ExperimentResult(
        config=exp_config,
        baseline_scorecard=baseline_sc,
        variant_scorecards=[variant_sc],
        comparisons=[{
            "direction_accuracy_delta": 0.246,
            "f1_delta": 0.05,
            "precision_delta": 0.03,
            "recall_delta": 0.04,
            "signal_value_delta": 5000.0,
            "roi_delta": 10.0,
            "parse_success_delta": 0.01,
            "timeout_delta": -0.01,
            "calibration_error_delta": -0.02,
            "overconfidence_rate_delta": -0.05,
            "summary": "Direction: +24.6%  F1: +0.050  Signal Value: +$5,000  ROI: +10.0x",
        }],
        significance=[sig],
        winner=winner,
        recommendation=recommendation,
        scenario_count=509,
        run_duration_seconds=2.3,
        completed_at=datetime(2026, 4, 2, 14, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# 1. test_scorecard_report_contains_accuracy
# ---------------------------------------------------------------------------


def test_scorecard_report_contains_accuracy():
    """Scorecard report shows direction accuracy."""
    gen = ReportGenerator()
    sc = _make_scorecard(direction_accuracy=0.552)
    report = gen.generate_scorecard_report(sc)
    assert "55.2%" in report
    assert "Direction Accuracy" in report


# ---------------------------------------------------------------------------
# 2. test_scorecard_report_contains_confusion_matrix
# ---------------------------------------------------------------------------


def test_scorecard_report_contains_confusion_matrix():
    """Scorecard report includes TP/FP/TN/FN values."""
    gen = ReportGenerator()
    sc = _make_scorecard()
    report = gen.generate_scorecard_report(sc)
    assert "TP=" in report
    assert "FP=" in report
    assert "TN=" in report
    assert "FN=" in report
    assert "Confusion Matrix" in report


# ---------------------------------------------------------------------------
# 3. test_scorecard_report_contains_operational
# ---------------------------------------------------------------------------


def test_scorecard_report_contains_operational():
    """Scorecard report shows latency and timeout data."""
    gen = ReportGenerator()
    sc = _make_scorecard()
    report = gen.generate_scorecard_report(sc)
    assert "Median Latency" in report
    assert "Timeout Rate" in report
    assert "Parse Success" in report


# ---------------------------------------------------------------------------
# 4. test_scorecard_report_contains_financial
# ---------------------------------------------------------------------------


def test_scorecard_report_contains_financial():
    """Scorecard report shows signal value and ROI."""
    gen = ReportGenerator()
    sc = _make_scorecard()
    report = gen.generate_scorecard_report(sc)
    assert "Signal Value" in report
    assert "ROI" in report
    assert "Captured Gains" in report
    assert "Avoided Losses" in report


# ---------------------------------------------------------------------------
# 5. test_experiment_report_contains_winner
# ---------------------------------------------------------------------------


def test_experiment_report_contains_winner():
    """Experiment report clearly states the winner."""
    gen = ReportGenerator()
    result = _make_experiment_result(winner="fundamental")
    report = gen.generate_experiment_report(result)
    assert "WINNER" in report
    assert "fundamental" in report.upper() or "FUNDAMENTAL" in report


# ---------------------------------------------------------------------------
# 6. test_experiment_report_contains_significance
# ---------------------------------------------------------------------------


def test_experiment_report_contains_significance():
    """Experiment report shows p-values and confidence intervals."""
    gen = ReportGenerator()
    result = _make_experiment_result()
    report = gen.generate_experiment_report(result)
    assert "p-value" in report or "p_value" in report.lower()
    assert "95% CI" in report
    assert "Cohen" in report


# ---------------------------------------------------------------------------
# 7. test_experiment_report_contains_recommendation
# ---------------------------------------------------------------------------


def test_experiment_report_contains_recommendation():
    """Experiment report contains actionable recommendation section."""
    gen = ReportGenerator()
    result = _make_experiment_result()
    report = gen.generate_experiment_report(result)
    assert "RECOMMENDATION" in report
    # Should reference the winner or baseline decision
    assert "fundamental" in report or "news" in report


# ---------------------------------------------------------------------------
# 8. test_comparison_table_format
# ---------------------------------------------------------------------------


def test_comparison_table_format():
    """Comparison table has aligned columns with deltas shown."""
    gen = ReportGenerator()
    baseline = _make_scorecard("news", "haiku", direction_accuracy=0.306)
    variant = _make_scorecard("fundamental", "sonnet", direction_accuracy=0.552)
    sig = _make_significance()
    table = gen.generate_comparison_table(baseline, variant, sig)

    # Columns present
    assert "Baseline" in table
    assert "Variant" in table
    assert "Delta" in table
    assert "Sig?" in table

    # Key metrics shown
    assert "Direction Accuracy" in table
    assert "F1 Score" in table
    assert "Signal Value" in table
    assert "Timeout Rate" in table


# ---------------------------------------------------------------------------
# 9. test_confusion_matrix_display
# ---------------------------------------------------------------------------


def test_confusion_matrix_display():
    """Confusion matrix shows all four cells in correct positions."""
    gen = ReportGenerator()
    metrics = _make_classification_metrics(tp=10, fp=5, tn=8, fn=3)
    display = gen.generate_confusion_matrix_display(metrics)

    assert "TP=10" in display
    assert "FP=5" in display
    assert "TN=8" in display
    assert "FN=3" in display
    # Runners should have TP and FN, Faders should have FP and TN
    lines = display.strip().split("\n")
    runner_line = next(l for l in lines if "RUNNER" in l)
    fader_line = next(l for l in lines if "FADER" in l)
    assert "TP=10" in runner_line
    assert "FN=3" in runner_line
    assert "FP=5" in fader_line
    assert "TN=8" in fader_line


# ---------------------------------------------------------------------------
# 10. test_calibration_chart
# ---------------------------------------------------------------------------


def test_calibration_chart():
    """Calibration chart shows calibration error and overconfidence rate."""
    gen = ReportGenerator()
    sc = _make_scorecard()
    chart = gen.generate_calibration_chart(sc)
    assert "Calibration Error" in chart
    assert "Overconfidence Rate" in chart
    # Should classify quality
    assert any(word in chart for word in ["calibrated", "miscalibrated"])


# ---------------------------------------------------------------------------
# 11. test_significance_stars
# ---------------------------------------------------------------------------


def test_significance_stars():
    """*** for p<0.001, ** for p<0.01, * for p<0.05, ns otherwise."""
    gen = ReportGenerator()
    assert gen.format_significance(0.0001) == "***"
    assert gen.format_significance(0.0009) == "***"
    assert gen.format_significance(0.001) == "** "
    assert gen.format_significance(0.005) == "** "
    assert gen.format_significance(0.01) == "*  "
    assert gen.format_significance(0.049) == "*  "
    assert gen.format_significance(0.05) == "ns "
    assert gen.format_significance(0.10) == "ns "
    assert gen.format_significance(1.0) == "ns "


# ---------------------------------------------------------------------------
# 12. test_format_pct_positive
# ---------------------------------------------------------------------------


def test_format_pct_positive():
    """Positive percentage formatted with + sign."""
    gen = ReportGenerator()
    result = gen.format_pct(0.246)
    assert result == "+24.6%"


# ---------------------------------------------------------------------------
# 13. test_format_pct_negative
# ---------------------------------------------------------------------------


def test_format_pct_negative():
    """Negative percentage formatted with - sign."""
    gen = ReportGenerator()
    result = gen.format_pct(-0.14)
    assert result == "-14.0%"


# ---------------------------------------------------------------------------
# 14. test_save_markdown
# ---------------------------------------------------------------------------


def test_save_markdown():
    """save_markdown creates file with correct content wrapped in code block."""
    gen = ReportGenerator()
    report_text = "=== TEST REPORT ===\nSome content here."

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, "reports", "test_report.md")
        gen.save_markdown(report_text, filepath)

        assert os.path.exists(filepath)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        assert "```" in content
        assert report_text in content


# ---------------------------------------------------------------------------
# 15. test_dataset_quality_report
# ---------------------------------------------------------------------------


def test_dataset_quality_report():
    """Dataset quality report shows distributions and gaps."""
    gen = ReportGenerator()
    stats = {
        "total": 509,
        "traded": 200,
        "trade_wins": 120,
        "trade_win_rate": 0.60,
        "by_outcome": {
            "runner": 180,
            "fader": 200,
            "mixed": 80,
            "flat": 49,
        },
        "by_catalyst_type": {
            "pharma_deal": 150,
            "earnings": 120,
            "fda": 80,
            "contract": 40,
            "unknown": 30,
            "sec_filing": 5,  # sparse — should appear in gaps
        },
        "by_label_confidence": {
            "auto_high": 300,
            "auto_medium": 150,
            "auto_low": 40,
            "unlabeled": 19,
        },
    }
    report = gen.generate_dataset_quality_report(stats)

    assert "509" in report
    assert "OUTCOME DISTRIBUTION" in report
    assert "CATALYST TYPE DISTRIBUTION" in report
    assert "LABEL CONFIDENCE" in report
    # sec_filing has only 5 — should be flagged
    assert "sec_filing" in report or "GAPS" in report


# ---------------------------------------------------------------------------
# 16. test_report_handles_empty_results
# ---------------------------------------------------------------------------


def test_report_handles_empty_results():
    """Report generator handles scorecard with zero scenarios gracefully."""
    gen = ReportGenerator()
    # Build scorecard via MetricsCalculator with empty results
    calc = MetricsCalculator()
    empty_sc = calc.score_results([], [])

    # Should not raise
    report = gen.generate_scorecard_report(empty_sc)
    assert len(report) > 0
    assert "0.0%" in report or "0%" in report  # direction accuracy = 0


# ---------------------------------------------------------------------------
# Bonus: test_experiment_report_baseline_wins
# ---------------------------------------------------------------------------


def test_experiment_report_baseline_wins():
    """When no variant wins, report says baseline recommended."""
    gen = ReportGenerator()
    result = _make_experiment_result(winner="baseline", significant=False, p_value=0.25)
    report = gen.generate_experiment_report(result)
    assert "baseline" in report.lower()
    # Should mention no significance
    assert "significance" in report.lower() or "recommended" in report.lower()
