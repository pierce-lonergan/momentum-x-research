"""Tests for the LLM Arena Experiment Engine (Component 4).

Node ID: tests.unit.test_llm_arena_experiment
Graph Link: tested_by → src.llm_arena.experiment, src.llm_arena.experiments_library

Tests:
  1.  test_experiment_config_creation
  2.  test_experiment_config_with_filters
  3.  test_select_scenarios_all
  4.  test_select_scenarios_filtered
  5.  test_select_scenarios_min_check
  6.  test_run_experiment_replay
  7.  test_experiment_result_has_scorecards
  8.  test_experiment_result_has_comparisons
  9.  test_bootstrap_significance_identical
  10. test_bootstrap_significance_different
  11. test_bootstrap_confidence_interval
  12. test_recommendation_variant_wins
  13. test_recommendation_baseline_wins
  14. test_recommendation_not_significant
  15. test_save_and_load_experiment
  16. test_list_experiments
  17. test_experiment_library_replay_validation
  18. test_experiment_library_model_comparison
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timezone

import pytest

from src.llm_arena.dataset import DatasetManager
from src.llm_arena.experiment import (
    ExperimentConfig,
    ExperimentEngine,
    ExperimentResult,
    SignificanceResult,
    format_experiment_result,
)
from src.llm_arena.experiments_library import (
    create_model_comparison_experiment,
    create_replay_validation_experiment,
    get_experiment,
    list_available_experiments,
)
from src.llm_arena.harness import AgentConfig, AgentHarness, AgentRunResult
from src.llm_arena.models import (
    CatalystType,
    CorrectSignal,
    LabelConfidence,
    LabeledScenario,
    StockOutcome,
)
from src.llm_arena.scoring import MetricsCalculator


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_scenario(
    ticker: str,
    outcome: StockOutcome = StockOutcome.RUNNER,
    correct_signal: CorrectSignal = CorrectSignal.BULL,
    catalyst: CatalystType = CatalystType.EARNINGS,
    date_val: date = date(2024, 1, 15),
    agent_signals: dict = None,
) -> LabeledScenario:
    return LabeledScenario(
        ticker=ticker,
        date=date_val,
        scenario_id=f"{ticker}_{date_val}",
        gap_pct=0.25,
        rvol=5.0,
        dollar_volume=2_000_000,
        open_price=10.0,
        prev_close=8.0,
        catalyst_type=catalyst,
        outcome=outcome,
        correct_signal=correct_signal,
        max_gain_pct=0.30,
        max_drawdown_pct=-0.05,
        label_confidence=LabelConfidence.AUTO_HIGH,
        actual_agent_signals=agent_signals or {},
    )


def _make_result(
    scenario_id: str,
    agent_type: str,
    direction: str,
    parse_ok: bool = True,
) -> AgentRunResult:
    return AgentRunResult(
        scenario_id=scenario_id,
        agent_config=AgentConfig(agent_type=agent_type, model_id="test"),
        signal_direction=direction,
        signal_confidence=0.8,
        parse_success=parse_ok,
        latency_ms=100.0,
    )


def _make_dataset(scenarios: list[LabeledScenario], tmp_dir: str) -> DatasetManager:
    mgr = DatasetManager(tmp_dir)
    for s in scenarios:
        mgr.add_scenario(s)
    return mgr


def _make_engine(
    scenarios: list[LabeledScenario], tmp_dir: str
) -> tuple[ExperimentEngine, DatasetManager]:
    mgr = _make_dataset(scenarios, os.path.join(tmp_dir, "dataset"))
    harness = AgentHarness(os.path.join(tmp_dir, "harness"))
    scorer = MetricsCalculator()
    exp_dir = os.path.join(tmp_dir, "experiments")
    engine = ExperimentEngine(mgr, harness, scorer, exp_dir)
    return engine, mgr


def _runner_scenarios_with_news(n: int = 30) -> list[LabeledScenario]:
    """Generate n scenarios with news_agent signal correctly set to BULL on RUNNER."""
    scenarios = []
    for i in range(n):
        d = date(2024, 1, i + 1) if i < 28 else date(2024, 2, i - 27)
        ticker = f"TICK{i:03d}"
        signals = {
            "news_agent": {
                "signal": "BULL",
                "confidence": 0.85,
                "reasoning": "Positive catalyst",
            }
        }
        s = _make_scenario(
            ticker=ticker,
            outcome=StockOutcome.RUNNER,
            correct_signal=CorrectSignal.BULL,
            date_val=d,
            agent_signals=signals,
        )
        scenarios.append(s)
    return scenarios


def _mixed_scenarios_with_agents(n: int = 40) -> list[LabeledScenario]:
    """40 scenarios: half RUNNER (BULL), half FADER (BEAR).
    news_agent is correct on all; fundamental_agent is random (correct on half).
    """
    scenarios = []
    for i in range(n):
        d = date(2024, 1, 1 + (i % 28))
        ticker = f"MX{i:03d}"
        is_runner = i % 2 == 0
        outcome = StockOutcome.RUNNER if is_runner else StockOutcome.FADER
        correct = CorrectSignal.BULL if is_runner else CorrectSignal.BEAR
        news_dir = "BULL" if is_runner else "BEAR"  # news_agent always correct
        # fundamental_agent: correct only on even i/2 (25% accuracy ≈ worse)
        fund_dir = "BULL" if i % 4 == 0 else "BEAR"

        signals = {
            "news_agent": {"signal": news_dir, "confidence": 0.8},
            "fundamental_agent": {"signal": fund_dir, "confidence": 0.6},
        }
        s = _make_scenario(
            ticker=ticker,
            outcome=outcome,
            correct_signal=correct,
            date_val=d,
            agent_signals=signals,
        )
        scenarios.append(s)
    return scenarios


# ---------------------------------------------------------------------------
# 1. test_experiment_config_creation
# ---------------------------------------------------------------------------


def test_experiment_config_creation():
    config = ExperimentConfig(
        name="test_exp",
        description="A test experiment",
        baseline=AgentConfig(agent_type="news", model_id="replay"),
        variants=[AgentConfig(agent_type="fundamental", model_id="replay")],
    )
    assert config.name == "test_exp"
    assert config.baseline.agent_type == "news"
    assert len(config.variants) == 1
    assert config.mode == "replay"
    assert config.confidence_level == 0.95
    assert config.bootstrap_iterations == 1000


# ---------------------------------------------------------------------------
# 2. test_experiment_config_with_filters
# ---------------------------------------------------------------------------


def test_experiment_config_with_filters():
    config = ExperimentConfig(
        name="filtered",
        description="Filtered experiment",
        baseline=AgentConfig(agent_type="news", model_id="replay"),
        variants=[],
        scenario_filters={"outcome": "runner"},
        min_scenarios=5,
    )
    assert config.scenario_filters["outcome"] == "runner"
    assert config.min_scenarios == 5


# ---------------------------------------------------------------------------
# 3. test_select_scenarios_all
# ---------------------------------------------------------------------------


def test_select_scenarios_all():
    with tempfile.TemporaryDirectory() as tmp:
        scenarios = _mixed_scenarios_with_agents(40)
        engine, _ = _make_engine(scenarios, tmp)

        config = ExperimentConfig(
            name="x",
            description="",
            baseline=AgentConfig(agent_type="news", model_id="replay"),
            variants=[],
            scenario_filters={},
            min_scenarios=5,
        )
        selected = engine._select_scenarios(config)
        assert len(selected) == 40


# ---------------------------------------------------------------------------
# 4. test_select_scenarios_filtered
# ---------------------------------------------------------------------------


def test_select_scenarios_filtered():
    with tempfile.TemporaryDirectory() as tmp:
        scenarios = _mixed_scenarios_with_agents(40)
        engine, _ = _make_engine(scenarios, tmp)

        config = ExperimentConfig(
            name="x",
            description="",
            baseline=AgentConfig(agent_type="news", model_id="replay"),
            variants=[],
            scenario_filters={"outcome": "runner"},
            min_scenarios=5,
        )
        selected = engine._select_scenarios(config)
        assert len(selected) == 20
        assert all(s.outcome == StockOutcome.RUNNER for s in selected)


# ---------------------------------------------------------------------------
# 5. test_select_scenarios_min_check
# ---------------------------------------------------------------------------


def test_select_scenarios_min_check():
    with tempfile.TemporaryDirectory() as tmp:
        scenarios = _mixed_scenarios_with_agents(10)
        engine, _ = _make_engine(scenarios, tmp)

        config = ExperimentConfig(
            name="x",
            description="",
            baseline=AgentConfig(agent_type="news", model_id="replay"),
            variants=[],
            scenario_filters={},
            min_scenarios=50,  # More than available
        )
        with pytest.raises(ValueError, match="Insufficient scenarios"):
            engine._select_scenarios(config)


# ---------------------------------------------------------------------------
# 6. test_run_experiment_replay
# ---------------------------------------------------------------------------


def test_run_experiment_replay():
    with tempfile.TemporaryDirectory() as tmp:
        scenarios = _mixed_scenarios_with_agents(40)
        engine, _ = _make_engine(scenarios, tmp)

        config = ExperimentConfig(
            name="replay_test",
            description="Replay test",
            baseline=AgentConfig(agent_type="news", model_id="replay"),
            variants=[AgentConfig(agent_type="fundamental", model_id="replay")],
            scenario_filters={},
            bootstrap_iterations=100,
            min_scenarios=5,
            mode="replay",
        )
        result = engine.run_experiment(config)
        assert isinstance(result, ExperimentResult)
        assert result.scenario_count == 40
        assert result.run_duration_seconds >= 0
        assert result.completed_at is not None


# ---------------------------------------------------------------------------
# 7. test_experiment_result_has_scorecards
# ---------------------------------------------------------------------------


def test_experiment_result_has_scorecards():
    with tempfile.TemporaryDirectory() as tmp:
        scenarios = _mixed_scenarios_with_agents(40)
        engine, _ = _make_engine(scenarios, tmp)

        config = ExperimentConfig(
            name="scorecard_test",
            description="",
            baseline=AgentConfig(agent_type="news", model_id="replay"),
            variants=[AgentConfig(agent_type="fundamental", model_id="replay")],
            scenario_filters={},
            bootstrap_iterations=50,
            min_scenarios=5,
            mode="replay",
        )
        result = engine.run_experiment(config)

        # Baseline scorecard
        assert result.baseline_scorecard is not None
        assert result.baseline_scorecard.scenario_count > 0

        # Variant scorecards
        assert len(result.variant_scorecards) == 1
        assert result.variant_scorecards[0].scenario_count > 0


# ---------------------------------------------------------------------------
# 8. test_experiment_result_has_comparisons
# ---------------------------------------------------------------------------


def test_experiment_result_has_comparisons():
    with tempfile.TemporaryDirectory() as tmp:
        scenarios = _mixed_scenarios_with_agents(40)
        engine, _ = _make_engine(scenarios, tmp)

        config = ExperimentConfig(
            name="comparison_test",
            description="",
            baseline=AgentConfig(agent_type="news", model_id="replay"),
            variants=[AgentConfig(agent_type="fundamental", model_id="replay")],
            scenario_filters={},
            bootstrap_iterations=50,
            min_scenarios=5,
            mode="replay",
        )
        result = engine.run_experiment(config)

        assert len(result.comparisons) == 1
        cmp = result.comparisons[0]
        assert "direction_accuracy_delta" in cmp
        assert "summary" in cmp


# ---------------------------------------------------------------------------
# 9. test_bootstrap_significance_identical
# ---------------------------------------------------------------------------


def test_bootstrap_significance_identical():
    """Identical baseline and variant results -> delta is 0, not significant."""
    with tempfile.TemporaryDirectory() as tmp:
        n = 40
        scenarios = _runner_scenarios_with_news(n)
        engine, _ = _make_engine(scenarios, tmp)
        scenario_map = {s.scenario_id: s for s in scenarios}

        # Both configs produce identical BULL results
        results = [
            _make_result(s.scenario_id, "news", "BULL") for s in scenarios
        ]

        sig = engine._compute_significance(
            baseline_results=results,
            variant_results=results,
            scenarios=scenario_map,
            bootstrap_iterations=500,
        )
        # When delta is exactly 0 the variant never strictly beats baseline
        assert sig.significant is False
        assert abs(sig.delta) < 1e-9


# ---------------------------------------------------------------------------
# 10. test_bootstrap_significance_different
# ---------------------------------------------------------------------------


def test_bootstrap_significance_different():
    """Variant clearly better → significant (p < 0.05)."""
    with tempfile.TemporaryDirectory() as tmp:
        n = 40
        scenarios = _runner_scenarios_with_news(n)
        engine, _ = _make_engine(scenarios, tmp)
        scenario_map = {s.scenario_id: s for s in scenarios}

        # Baseline: always wrong (BEAR on RUNNER scenarios)
        baseline = [
            _make_result(s.scenario_id, "news", "BEAR") for s in scenarios
        ]
        # Variant: always correct (BULL on RUNNER scenarios)
        variant = [
            AgentRunResult(
                scenario_id=s.scenario_id,
                agent_config=AgentConfig(agent_type="variant", model_id="test"),
                signal_direction="BULL",
                parse_success=True,
            )
            for s in scenarios
        ]

        sig = engine._compute_significance(
            baseline_results=baseline,
            variant_results=variant,
            scenarios=scenario_map,
            bootstrap_iterations=500,
        )
        assert sig.significant is True, f"Expected significant, p={sig.p_value}"
        assert sig.p_value < 0.05
        assert sig.delta > 0


# ---------------------------------------------------------------------------
# 11. test_bootstrap_confidence_interval
# ---------------------------------------------------------------------------


def test_bootstrap_confidence_interval():
    """CI should contain the observed delta."""
    with tempfile.TemporaryDirectory() as tmp:
        scenarios = _mixed_scenarios_with_agents(40)
        engine, _ = _make_engine(scenarios, tmp)
        scenario_map = {s.scenario_id: s for s in scenarios}

        baseline = [
            _make_result(s.scenario_id, "news", "BEAR") for s in scenarios
        ]
        variant = [
            AgentRunResult(
                scenario_id=s.scenario_id,
                agent_config=AgentConfig(agent_type="v", model_id="test"),
                signal_direction="BULL",
                parse_success=True,
            )
            for s in scenarios
        ]

        sig = engine._compute_significance(
            baseline_results=baseline,
            variant_results=variant,
            scenarios=scenario_map,
            bootstrap_iterations=500,
            confidence_level=0.95,
        )
        lo, hi = sig.confidence_interval
        assert lo <= sig.delta <= hi, (
            f"Observed delta {sig.delta:.3f} not in CI [{lo:.3f}, {hi:.3f}]"
        )


# ---------------------------------------------------------------------------
# 12. test_recommendation_variant_wins
# ---------------------------------------------------------------------------


def test_recommendation_variant_wins():
    """Variant with clearly better accuracy → recommended as winner."""
    with tempfile.TemporaryDirectory() as tmp:
        scenarios = _runner_scenarios_with_news(30)
        engine, _ = _make_engine(scenarios, tmp)

        config = ExperimentConfig(
            name="winner_test",
            description="",
            baseline=AgentConfig(agent_type="news", model_id="replay"),
            variants=[AgentConfig(agent_type="fundamental", model_id="replay")],
            scenario_filters={},
            bootstrap_iterations=200,
            min_scenarios=5,
            confidence_level=0.95,
            mode="replay",
        )

        # Inject scenarios where fundamental is better: override actual_agent_signals
        # so that fundamental_agent is always correct, news_agent is always wrong.
        for s in engine._dataset.all():
            s.actual_agent_signals = {
                "news_agent": {"signal": "BEAR"},       # wrong
                "fundamental_agent": {"signal": "BULL"},  # correct (runner)
            }
        result = engine.run_experiment(config)
        # fundamental_agent should win
        assert result.winner == "fundamental"


# ---------------------------------------------------------------------------
# 13. test_recommendation_baseline_wins
# ---------------------------------------------------------------------------


def test_recommendation_baseline_wins():
    """Variant clearly worse than baseline → baseline recommended."""
    with tempfile.TemporaryDirectory() as tmp:
        scenarios = _runner_scenarios_with_news(30)
        engine, _ = _make_engine(scenarios, tmp)

        config = ExperimentConfig(
            name="baseline_wins",
            description="",
            baseline=AgentConfig(agent_type="news", model_id="replay"),
            variants=[AgentConfig(agent_type="fundamental", model_id="replay")],
            scenario_filters={},
            bootstrap_iterations=200,
            min_scenarios=5,
            confidence_level=0.95,
            mode="replay",
        )

        # baseline correct, variant wrong
        for s in engine._dataset.all():
            s.actual_agent_signals = {
                "news_agent": {"signal": "BULL"},         # correct
                "fundamental_agent": {"signal": "BEAR"},  # wrong
            }
        result = engine.run_experiment(config)
        assert result.winner == "baseline"


# ---------------------------------------------------------------------------
# 14. test_recommendation_not_significant
# ---------------------------------------------------------------------------


def test_recommendation_not_significant():
    """No clear winner → baseline recommended (conservative)."""
    with tempfile.TemporaryDirectory() as tmp:
        scenarios = _runner_scenarios_with_news(30)
        engine, _ = _make_engine(scenarios, tmp)

        config = ExperimentConfig(
            name="no_winner",
            description="",
            baseline=AgentConfig(agent_type="news", model_id="replay"),
            variants=[AgentConfig(agent_type="fundamental", model_id="replay")],
            scenario_filters={},
            bootstrap_iterations=200,
            min_scenarios=5,
            confidence_level=0.95,
            mode="replay",
        )

        # Both exactly equal
        for s in engine._dataset.all():
            s.actual_agent_signals = {
                "news_agent": {"signal": "BULL"},
                "fundamental_agent": {"signal": "BULL"},  # identical to baseline
            }
        result = engine.run_experiment(config)
        assert result.winner == "baseline"
        assert "conservative" in result.recommendation.lower()


# ---------------------------------------------------------------------------
# 15. test_save_and_load_experiment
# ---------------------------------------------------------------------------


def test_save_and_load_experiment():
    with tempfile.TemporaryDirectory() as tmp:
        scenarios = _mixed_scenarios_with_agents(40)
        engine, _ = _make_engine(scenarios, tmp)

        config = ExperimentConfig(
            name="persist_test",
            description="Test persistence",
            baseline=AgentConfig(agent_type="news", model_id="replay"),
            variants=[AgentConfig(agent_type="fundamental", model_id="replay")],
            scenario_filters={},
            bootstrap_iterations=50,
            min_scenarios=5,
            mode="replay",
        )
        result = engine.run_experiment(config)
        path = engine.save_experiment(result)

        assert os.path.exists(path)

        loaded = engine.load_experiment("persist_test")
        assert loaded.config.name == result.config.name
        assert loaded.winner == result.winner
        assert loaded.scenario_count == result.scenario_count
        assert len(loaded.significance) == len(result.significance)


# ---------------------------------------------------------------------------
# 16. test_list_experiments
# ---------------------------------------------------------------------------


def test_list_experiments():
    with tempfile.TemporaryDirectory() as tmp:
        scenarios = _mixed_scenarios_with_agents(40)
        engine, _ = _make_engine(scenarios, tmp)

        config = ExperimentConfig(
            name="list_test",
            description="For listing",
            baseline=AgentConfig(agent_type="news", model_id="replay"),
            variants=[AgentConfig(agent_type="fundamental", model_id="replay")],
            scenario_filters={},
            bootstrap_iterations=50,
            min_scenarios=5,
            mode="replay",
        )
        result = engine.run_experiment(config)
        engine.save_experiment(result)

        listings = engine.list_experiments()
        assert len(listings) == 1
        listing = listings[0]
        assert listing["name"] == "list_test"
        assert "winner" in listing
        assert "scenario_count" in listing
        assert listing["scenario_count"] == 40


# ---------------------------------------------------------------------------
# 17. test_experiment_library_replay_validation
# ---------------------------------------------------------------------------


def test_experiment_library_replay_validation():
    config = create_replay_validation_experiment()
    assert config.name == "replay_validation"
    assert config.mode == "replay"
    assert config.baseline.agent_type == "news"
    assert len(config.variants) >= 1
    assert config.min_scenarios <= 20
    assert config.bootstrap_iterations > 0
    assert 0.0 < config.confidence_level < 1.0


# ---------------------------------------------------------------------------
# 18. test_experiment_library_model_comparison
# ---------------------------------------------------------------------------


def test_experiment_library_model_comparison():
    config = create_model_comparison_experiment()
    assert config.name == "model_comparison"
    assert config.mode == "live"
    assert config.baseline.agent_type == "news"
    assert len(config.variants) >= 1
    # All variants should also be news agent type
    for v in config.variants:
        assert v.agent_type == "news"


# ---------------------------------------------------------------------------
# Bonus: test get_experiment and list_available_experiments
# ---------------------------------------------------------------------------


def test_get_experiment_known():
    config = get_experiment("replay_validation")
    assert config.name == "replay_validation"


def test_get_experiment_unknown():
    with pytest.raises(KeyError, match="Unknown experiment"):
        get_experiment("does_not_exist")


def test_list_available_experiments():
    items = list_available_experiments()
    names = [i["name"] for i in items]
    assert "replay_validation" in names
    assert "model_comparison" in names
    assert "prompt_engineering" in names
    for item in items:
        assert "name" in item
        assert "mode" in item
        assert "baseline" in item
        assert "variants" in item


def test_format_experiment_result_runs():
    """format_experiment_result returns a non-empty string."""
    with tempfile.TemporaryDirectory() as tmp:
        scenarios = _mixed_scenarios_with_agents(40)
        engine, _ = _make_engine(scenarios, tmp)

        config = ExperimentConfig(
            name="fmt_test",
            description="Format test",
            baseline=AgentConfig(agent_type="news", model_id="replay"),
            variants=[AgentConfig(agent_type="fundamental", model_id="replay")],
            scenario_filters={},
            bootstrap_iterations=50,
            min_scenarios=5,
            mode="replay",
        )
        result = engine.run_experiment(config)
        output = format_experiment_result(result)
        assert "EXPERIMENT" in output
        assert "WINNER" in output
        assert "RECOMMENDATION" in output
        assert "SIGNIFICANCE" in output


def test_significance_result_serialization():
    sig = SignificanceResult(
        variant_name="foo",
        metric="direction_accuracy",
        baseline_mean=0.6,
        variant_mean=0.75,
        delta=0.15,
        delta_pct=0.25,
        p_value=0.02,
        significant=True,
        confidence_interval=(0.05, 0.25),
        effect_size=0.42,
    )
    d = sig.to_dict()
    sig2 = SignificanceResult.from_dict(d)
    assert sig2.variant_name == sig.variant_name
    assert sig2.significant == sig.significant
    assert sig2.confidence_interval == sig.confidence_interval


def test_experiment_config_serialization():
    config = ExperimentConfig(
        name="serial_test",
        description="Serialization test",
        baseline=AgentConfig(agent_type="news", model_id="replay"),
        variants=[AgentConfig(agent_type="fundamental", model_id="replay")],
        scenario_filters={"outcome": "runner"},
    )
    d = config.to_dict()
    config2 = ExperimentConfig.from_dict(d)
    assert config2.name == config.name
    assert config2.scenario_filters == {"outcome": "runner"}
    assert config2.baseline.agent_type == "news"
    assert len(config2.variants) == 1
