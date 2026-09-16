"""Tests for the LLM Arena Scoring & Metrics Engine (Component 3).

Node ID: tests.unit.test_llm_arena_scoring
Graph Link: tested_by → src.llm_arena.scoring

Tests:
  1.  test_direction_accuracy_all_correct
  2.  test_direction_accuracy_all_wrong
  3.  test_direction_accuracy_mixed
  4.  test_catalyst_accuracy_exact_match
  5.  test_catalyst_accuracy_no_match
  6.  test_confusion_matrix_true_positive
  7.  test_confusion_matrix_false_positive
  8.  test_confusion_matrix_true_negative
  9.  test_confusion_matrix_false_negative
  10. test_precision_recall_f1
  11. test_calibration_perfect
  12. test_calibration_overconfident
  13. test_operational_timeout_rate
  14. test_operational_parse_success
  15. test_operational_latency_percentiles
  16. test_financial_signal_value
  17. test_financial_avoided_losses
  18. test_accuracy_by_catalyst_type
  19. test_accuracy_by_outcome
  20. test_compare_scorecards
  21. test_scorecard_with_empty_results
  22. test_scorecard_serialization
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.llm_arena.harness import AgentConfig, AgentRunResult
from src.llm_arena.models import (
    CatalystType,
    CorrectSignal,
    LabelConfidence,
    LabeledScenario,
    StockOutcome,
)
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
    open_price: float | None = 10.0,
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
        open_price=open_price,
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
    catalyst_type: str | None = "fda",
    parse_success: bool = True,
    timed_out: bool = False,
    latency_ms: float = 1000.0,
    tokens_input: int = 500,
    tokens_output: int = 100,
    agent_type: str = "news",
    model_id: str = "haiku",
) -> AgentRunResult:
    return AgentRunResult(
        scenario_id=scenario_id,
        agent_config=_make_config(agent_type=agent_type, model_id=model_id),
        signal_direction=direction,
        signal_confidence=confidence,
        catalyst_type=catalyst_type,
        parse_success=parse_success,
        timed_out=timed_out,
        latency_ms=latency_ms,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        timestamp=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# 1. test_direction_accuracy_all_correct
# ---------------------------------------------------------------------------


def test_direction_accuracy_all_correct():
    """All results correctly predict direction → accuracy = 1.0."""
    scenarios = [
        _make_scenario("A", outcome=StockOutcome.RUNNER, correct_signal=CorrectSignal.BULL, scenario_id="A_2026-01-15"),
        _make_scenario("B", outcome=StockOutcome.FADER, correct_signal=CorrectSignal.BEAR, scenario_id="B_2026-01-15"),
    ]
    results = [
        _make_result(scenario_id="A_2026-01-15", direction="BULL"),
        _make_result(scenario_id="B_2026-01-15", direction="BEAR"),
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    assert sc.classification.direction_accuracy == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 2. test_direction_accuracy_all_wrong
# ---------------------------------------------------------------------------


def test_direction_accuracy_all_wrong():
    """All results predict wrong direction → accuracy = 0.0."""
    scenarios = [
        _make_scenario("A", outcome=StockOutcome.RUNNER, correct_signal=CorrectSignal.BULL, scenario_id="A_2026-01-15"),
        _make_scenario("B", outcome=StockOutcome.FADER, correct_signal=CorrectSignal.BEAR, scenario_id="B_2026-01-15"),
    ]
    results = [
        _make_result(scenario_id="A_2026-01-15", direction="BEAR"),
        _make_result(scenario_id="B_2026-01-15", direction="BULL"),
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    assert sc.classification.direction_accuracy == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 3. test_direction_accuracy_mixed
# ---------------------------------------------------------------------------


def test_direction_accuracy_mixed():
    """Mixed results → accuracy is fraction correct."""
    scenarios = [
        _make_scenario("A", correct_signal=CorrectSignal.BULL, scenario_id="A_2026-01-15"),
        _make_scenario("B", correct_signal=CorrectSignal.BEAR, scenario_id="B_2026-01-15"),
        _make_scenario("C", correct_signal=CorrectSignal.BULL, scenario_id="C_2026-01-15"),
        _make_scenario("D", correct_signal=CorrectSignal.NEUTRAL, scenario_id="D_2026-01-15"),
    ]
    results = [
        _make_result(scenario_id="A_2026-01-15", direction="BULL"),    # correct
        _make_result(scenario_id="B_2026-01-15", direction="BULL"),    # wrong
        _make_result(scenario_id="C_2026-01-15", direction="BEAR"),    # wrong
        _make_result(scenario_id="D_2026-01-15", direction="NEUTRAL"), # correct
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    assert sc.classification.direction_accuracy == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# 4. test_catalyst_accuracy_exact_match
# ---------------------------------------------------------------------------


def test_catalyst_accuracy_exact_match():
    """Catalyst type matches exactly → catalyst_accuracy = 1.0."""
    scenarios = [
        _make_scenario("A", catalyst_type=CatalystType.FDA, scenario_id="A_2026-01-15"),
    ]
    results = [
        _make_result(scenario_id="A_2026-01-15", catalyst_type="fda"),
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    assert sc.classification.catalyst_accuracy == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 5. test_catalyst_accuracy_no_match
# ---------------------------------------------------------------------------


def test_catalyst_accuracy_no_match():
    """Catalyst type mismatches → catalyst_accuracy = 0.0."""
    scenarios = [
        _make_scenario("A", catalyst_type=CatalystType.FDA, scenario_id="A_2026-01-15"),
    ]
    results = [
        _make_result(scenario_id="A_2026-01-15", catalyst_type="earnings"),
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    assert sc.classification.catalyst_accuracy == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 6. test_confusion_matrix_true_positive
# ---------------------------------------------------------------------------


def test_confusion_matrix_true_positive():
    """BULL call on RUNNER scenario → TP."""
    scenarios = [
        _make_scenario("A", outcome=StockOutcome.RUNNER, scenario_id="A_2026-01-15"),
    ]
    results = [
        _make_result(scenario_id="A_2026-01-15", direction="BULL"),
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    assert sc.classification.true_positives == 1
    assert sc.classification.false_positives == 0
    assert sc.classification.false_negatives == 0
    assert sc.classification.true_negatives == 0


# ---------------------------------------------------------------------------
# 7. test_confusion_matrix_false_positive
# ---------------------------------------------------------------------------


def test_confusion_matrix_false_positive():
    """BULL call on FADER scenario → FP."""
    scenarios = [
        _make_scenario("A", outcome=StockOutcome.FADER, correct_signal=CorrectSignal.BEAR, scenario_id="A_2026-01-15"),
    ]
    results = [
        _make_result(scenario_id="A_2026-01-15", direction="BULL"),
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    assert sc.classification.false_positives == 1
    assert sc.classification.true_positives == 0
    assert sc.classification.true_negatives == 0
    assert sc.classification.false_negatives == 0


# ---------------------------------------------------------------------------
# 8. test_confusion_matrix_true_negative
# ---------------------------------------------------------------------------


def test_confusion_matrix_true_negative():
    """BEAR call on FADER scenario → TN."""
    scenarios = [
        _make_scenario("A", outcome=StockOutcome.FADER, correct_signal=CorrectSignal.BEAR, scenario_id="A_2026-01-15"),
    ]
    results = [
        _make_result(scenario_id="A_2026-01-15", direction="BEAR"),
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    assert sc.classification.true_negatives == 1
    assert sc.classification.true_positives == 0
    assert sc.classification.false_positives == 0
    assert sc.classification.false_negatives == 0


# ---------------------------------------------------------------------------
# 9. test_confusion_matrix_false_negative
# ---------------------------------------------------------------------------


def test_confusion_matrix_false_negative():
    """BEAR call on RUNNER scenario → FN."""
    scenarios = [
        _make_scenario("A", outcome=StockOutcome.RUNNER, correct_signal=CorrectSignal.BULL, scenario_id="A_2026-01-15"),
    ]
    results = [
        _make_result(scenario_id="A_2026-01-15", direction="BEAR"),
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    assert sc.classification.false_negatives == 1
    assert sc.classification.true_positives == 0
    assert sc.classification.false_positives == 0
    assert sc.classification.true_negatives == 0


# ---------------------------------------------------------------------------
# 10. test_precision_recall_f1
# ---------------------------------------------------------------------------


def test_precision_recall_f1():
    """Check precision, recall, and F1 with known TP/FP/FN counts."""
    # TP=2, FP=1, FN=1, TN=1
    # precision = 2/(2+1) = 0.667
    # recall    = 2/(2+1) = 0.667
    # f1        = 2*0.667*0.667/(0.667+0.667) = 0.667
    scenarios = [
        _make_scenario("A", outcome=StockOutcome.RUNNER, correct_signal=CorrectSignal.BULL, scenario_id="A_2026-01-15"),
        _make_scenario("B", outcome=StockOutcome.RUNNER, correct_signal=CorrectSignal.BULL, scenario_id="B_2026-01-15"),
        _make_scenario("C", outcome=StockOutcome.FADER, correct_signal=CorrectSignal.BEAR, scenario_id="C_2026-01-15"),
        _make_scenario("D", outcome=StockOutcome.FADER, correct_signal=CorrectSignal.BEAR, scenario_id="D_2026-01-15"),
        _make_scenario("E", outcome=StockOutcome.RUNNER, correct_signal=CorrectSignal.BULL, scenario_id="E_2026-01-15"),
    ]
    results = [
        _make_result(scenario_id="A_2026-01-15", direction="BULL"),   # TP
        _make_result(scenario_id="B_2026-01-15", direction="BULL"),   # TP
        _make_result(scenario_id="C_2026-01-15", direction="BULL"),   # FP
        _make_result(scenario_id="D_2026-01-15", direction="BEAR"),   # TN
        _make_result(scenario_id="E_2026-01-15", direction="BEAR"),   # FN
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    c = sc.classification
    assert c.true_positives == 2
    assert c.false_positives == 1
    assert c.true_negatives == 1
    assert c.false_negatives == 1
    assert c.precision == pytest.approx(2 / 3, rel=1e-3)
    assert c.recall == pytest.approx(2 / 3, rel=1e-3)
    assert c.f1_score == pytest.approx(2 / 3, rel=1e-3)


# ---------------------------------------------------------------------------
# 11. test_calibration_perfect
# ---------------------------------------------------------------------------


def test_calibration_perfect():
    """When confidence matches actual accuracy per bin, calibration_error is low."""
    # Make 10 scenarios: all runners, agent calls BULL with confidence=0.9 → all correct
    # In the 0.8-1.0 bin: avg_conf = 0.9, actual accuracy = 1.0 → error = 0.1
    scenarios = [
        _make_scenario(
            str(i),
            outcome=StockOutcome.RUNNER,
            correct_signal=CorrectSignal.BULL,
            scenario_id=f"S{i}_2026-01-15",
        )
        for i in range(10)
    ]
    results = [
        _make_result(scenario_id=f"S{i}_2026-01-15", direction="BULL", confidence=0.9)
        for i in range(10)
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    # All in one bin: |0.9 - 1.0| = 0.1
    assert sc.classification.calibration_error == pytest.approx(0.1, abs=1e-9)


# ---------------------------------------------------------------------------
# 12. test_calibration_overconfident
# ---------------------------------------------------------------------------


def test_calibration_overconfident():
    """Overconfidence rate > 0 when agents are systematically overconfident."""
    # 5 scenarios: all faders, but agent calls BULL with confidence=0.9 → all wrong
    # In the 0.8-1.0 bin: avg_conf=0.9, actual_acc=0.0 → each prediction overconfident
    scenarios = [
        _make_scenario(
            str(i),
            outcome=StockOutcome.FADER,
            correct_signal=CorrectSignal.BEAR,
            scenario_id=f"F{i}_2026-01-15",
        )
        for i in range(5)
    ]
    results = [
        _make_result(scenario_id=f"F{i}_2026-01-15", direction="BULL", confidence=0.9)
        for i in range(5)
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    # All 5 predictions have conf=0.9 > actual_acc=0.0 in their bin → overconfidence_rate=1.0
    assert sc.classification.overconfidence_rate == pytest.approx(1.0)
    assert sc.classification.calibration_error > 0.0


# ---------------------------------------------------------------------------
# 13. test_operational_timeout_rate
# ---------------------------------------------------------------------------


def test_operational_timeout_rate():
    """Timeout rate is calculated correctly."""
    scenarios = [_make_scenario(str(i), scenario_id=f"S{i}_2026-01-15") for i in range(4)]
    results = [
        _make_result(scenario_id="S0_2026-01-15", timed_out=True, parse_success=False),
        _make_result(scenario_id="S1_2026-01-15", timed_out=False, parse_success=True),
        _make_result(scenario_id="S2_2026-01-15", timed_out=False, parse_success=True),
        _make_result(scenario_id="S3_2026-01-15", timed_out=True, parse_success=False),
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    assert sc.operational.timeout_rate == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 14. test_operational_parse_success
# ---------------------------------------------------------------------------


def test_operational_parse_success():
    """Parse success rate is calculated correctly."""
    scenarios = [_make_scenario(str(i), scenario_id=f"S{i}_2026-01-15") for i in range(5)]
    results = [
        _make_result(scenario_id="S0_2026-01-15", parse_success=True),
        _make_result(scenario_id="S1_2026-01-15", parse_success=True),
        _make_result(scenario_id="S2_2026-01-15", parse_success=True),
        _make_result(scenario_id="S3_2026-01-15", parse_success=True),
        _make_result(scenario_id="S4_2026-01-15", parse_success=False),
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    assert sc.operational.parse_success_rate == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# 15. test_operational_latency_percentiles
# ---------------------------------------------------------------------------


def test_operational_latency_percentiles():
    """Latency percentiles are computed correctly from sorted data."""
    # 10 results with latencies 100ms to 1000ms in steps of 100
    scenarios = [_make_scenario(str(i), scenario_id=f"S{i}_2026-01-15") for i in range(10)]
    results = [
        _make_result(scenario_id=f"S{i}_2026-01-15", latency_ms=float((i + 1) * 100))
        for i in range(10)
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    # Sorted: [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
    # median = 550.0 (average of 500 and 600 via statistics.median)
    assert sc.operational.median_latency_ms == pytest.approx(550.0)
    # p95: idx = int(10 * 95 / 100) = 9 → latencies[9] = 1000
    assert sc.operational.p95_latency_ms == pytest.approx(1000.0)
    # p99: idx = int(10 * 99 / 100) = 9 → latencies[9] = 1000
    assert sc.operational.p99_latency_ms == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# 16. test_financial_signal_value
# ---------------------------------------------------------------------------


def test_financial_signal_value():
    """signal_value reflects captured gains minus FP penalties minus FN penalties."""
    # TP: BULL on RUNNER with trade_pnl=500 → captured_gains += 500
    # FP: BULL on FADER with trade_pnl=200 → fp_penalty += 200 * 1.5 = 300
    # Net signal_value = 500 - 300 = 200
    scenarios = [
        _make_scenario("A", outcome=StockOutcome.RUNNER, correct_signal=CorrectSignal.BULL,
                       trade_pnl=500.0, scenario_id="A_2026-01-15"),
        _make_scenario("B", outcome=StockOutcome.FADER, correct_signal=CorrectSignal.BEAR,
                       trade_pnl=200.0, scenario_id="B_2026-01-15"),
    ]
    results = [
        _make_result(scenario_id="A_2026-01-15", direction="BULL"),  # TP
        _make_result(scenario_id="B_2026-01-15", direction="BULL"),  # FP
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    assert sc.financial.captured_gains == pytest.approx(500.0)
    assert sc.financial.signal_value == pytest.approx(500.0 - 300.0)


# ---------------------------------------------------------------------------
# 17. test_financial_avoided_losses
# ---------------------------------------------------------------------------


def test_financial_avoided_losses():
    """avoided_losses accumulates for correctly identified faders."""
    # TN: BEAR on FADER with trade_pnl=300 → avoided_losses += 300
    scenarios = [
        _make_scenario("A", outcome=StockOutcome.FADER, correct_signal=CorrectSignal.BEAR,
                       trade_pnl=300.0, max_drawdown_pct=-0.15, scenario_id="A_2026-01-15"),
    ]
    results = [
        _make_result(scenario_id="A_2026-01-15", direction="BEAR"),  # TN
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    assert sc.financial.avoided_losses == pytest.approx(300.0)
    assert sc.financial.captured_gains == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 18. test_accuracy_by_catalyst_type
# ---------------------------------------------------------------------------


def test_accuracy_by_catalyst_type():
    """accuracy_by_catalyst groups and averages direction accuracy per catalyst."""
    scenarios = [
        _make_scenario("A", catalyst_type=CatalystType.FDA, correct_signal=CorrectSignal.BULL,
                       scenario_id="A_2026-01-15"),
        _make_scenario("B", catalyst_type=CatalystType.FDA, correct_signal=CorrectSignal.BULL,
                       scenario_id="B_2026-01-15"),
        _make_scenario("C", catalyst_type=CatalystType.EARNINGS, correct_signal=CorrectSignal.BEAR,
                       scenario_id="C_2026-01-15"),
    ]
    results = [
        _make_result(scenario_id="A_2026-01-15", direction="BULL"),   # correct (FDA)
        _make_result(scenario_id="B_2026-01-15", direction="BEAR"),   # wrong (FDA)
        _make_result(scenario_id="C_2026-01-15", direction="BEAR"),   # correct (EARNINGS)
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    assert sc.accuracy_by_catalyst["fda"] == pytest.approx(0.5)
    assert sc.accuracy_by_catalyst["earnings"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 19. test_accuracy_by_outcome
# ---------------------------------------------------------------------------


def test_accuracy_by_outcome():
    """accuracy_by_outcome groups and averages direction accuracy per outcome type."""
    scenarios = [
        _make_scenario("A", outcome=StockOutcome.RUNNER, correct_signal=CorrectSignal.BULL,
                       scenario_id="A_2026-01-15"),
        _make_scenario("B", outcome=StockOutcome.FADER, correct_signal=CorrectSignal.BEAR,
                       scenario_id="B_2026-01-15"),
        _make_scenario("C", outcome=StockOutcome.FADER, correct_signal=CorrectSignal.BEAR,
                       scenario_id="C_2026-01-15"),
    ]
    results = [
        _make_result(scenario_id="A_2026-01-15", direction="BULL"),   # correct (runner)
        _make_result(scenario_id="B_2026-01-15", direction="BEAR"),   # correct (fader)
        _make_result(scenario_id="C_2026-01-15", direction="BULL"),   # wrong (fader)
    ]
    calc = MetricsCalculator()
    sc = calc.score_results(results, scenarios)
    assert sc.accuracy_by_outcome["runner"] == pytest.approx(1.0)
    assert sc.accuracy_by_outcome["fader"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 20. test_compare_scorecards
# ---------------------------------------------------------------------------


def test_compare_scorecards():
    """compare_scorecards returns correct deltas between two scorecards."""
    calc = MetricsCalculator()

    # Baseline: 1 correct out of 2
    scenarios_b = [
        _make_scenario("A", correct_signal=CorrectSignal.BULL, scenario_id="A_2026-01-15"),
        _make_scenario("B", correct_signal=CorrectSignal.BULL, scenario_id="B_2026-01-15"),
    ]
    results_b = [
        _make_result(scenario_id="A_2026-01-15", direction="BULL"),
        _make_result(scenario_id="B_2026-01-15", direction="BEAR"),
    ]
    baseline_sc = calc.score_results(results_b, scenarios_b)

    # Variant: 2 correct out of 2
    scenarios_v = [
        _make_scenario("A", correct_signal=CorrectSignal.BULL, scenario_id="A_2026-01-15"),
        _make_scenario("B", correct_signal=CorrectSignal.BULL, scenario_id="B_2026-01-15"),
    ]
    results_v = [
        _make_result(scenario_id="A_2026-01-15", direction="BULL"),
        _make_result(scenario_id="B_2026-01-15", direction="BULL"),
    ]
    variant_sc = calc.score_results(results_v, scenarios_v)

    comparison = calc.compare_scorecards(baseline_sc, variant_sc)

    # Direction accuracy improved from 0.5 to 1.0 → delta = +0.5
    assert comparison["direction_accuracy_delta"] == pytest.approx(0.5)
    assert "summary" in comparison
    assert isinstance(comparison["summary"], str)


# ---------------------------------------------------------------------------
# 21. test_scorecard_with_empty_results
# ---------------------------------------------------------------------------


def test_scorecard_with_empty_results():
    """Empty results list produces a valid scorecard with zero metrics."""
    calc = MetricsCalculator()
    sc = calc.score_results([], [])

    assert sc.scenario_count == 0
    assert sc.classification.direction_accuracy == 0.0
    assert sc.classification.true_positives == 0
    assert sc.operational.timeout_rate == 0.0
    assert sc.financial.signal_value == 0.0
    assert sc.accuracy_by_catalyst == {}
    assert sc.accuracy_by_outcome == {}


# ---------------------------------------------------------------------------
# 22. test_scorecard_serialization
# ---------------------------------------------------------------------------


def test_scorecard_serialization():
    """AgentScorecard round-trips through to_dict/from_dict correctly."""
    scenarios = [
        _make_scenario("A", outcome=StockOutcome.RUNNER, correct_signal=CorrectSignal.BULL,
                       scenario_id="A_2026-01-15"),
        _make_scenario("B", outcome=StockOutcome.FADER, correct_signal=CorrectSignal.BEAR,
                       scenario_id="B_2026-01-15"),
    ]
    results = [
        _make_result(scenario_id="A_2026-01-15", direction="BULL", confidence=0.85),
        _make_result(scenario_id="B_2026-01-15", direction="BEAR", confidence=0.65),
    ]
    calc = MetricsCalculator()
    original = calc.score_results(results, scenarios)

    # Serialize → deserialize
    d = original.to_dict()
    restored = AgentScorecard.from_dict(d)

    assert restored.agent_type == original.agent_type
    assert restored.scenario_count == original.scenario_count
    assert restored.classification.direction_accuracy == pytest.approx(original.classification.direction_accuracy)
    assert restored.classification.true_positives == original.classification.true_positives
    assert restored.classification.false_positives == original.classification.false_positives
    assert restored.operational.parse_success_rate == pytest.approx(original.operational.parse_success_rate)
    assert restored.financial.signal_value == pytest.approx(original.financial.signal_value)
    assert restored.accuracy_by_catalyst == original.accuracy_by_catalyst
    assert restored.accuracy_by_outcome == original.accuracy_by_outcome
