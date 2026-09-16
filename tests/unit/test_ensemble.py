"""Unit tests for the multi-model ensemble voter and experiment.

All tests use mocked LLM responses — no real API calls are made.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm_arena.ensemble import (
    EnsembleConfig,
    EnsembleResult,
    EnsembleVoter,
    ModelResponse,
    _normalize_signal,
)
from src.llm_arena.ensemble_experiment import (
    EnsembleExperiment,
    EnsembleExperimentResult,
    format_ensemble_result,
    _dir_correct_ensemble,
)
from src.llm_arena.harness import AgentConfig, AgentRunResult
from src.llm_arena.models import (
    CorrectSignal,
    LabeledScenario,
    StockOutcome,
    CatalystType,
    LabelConfidence,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_scenario(
    ticker: str = "TEST",
    outcome: StockOutcome = StockOutcome.RUNNER,
    correct_signal: CorrectSignal = CorrectSignal.BULL,
    catalyst_type: CatalystType = CatalystType.EARNINGS,
) -> LabeledScenario:
    return LabeledScenario(
        ticker=ticker,
        date=date(2026, 1, 15),
        scenario_id=f"{ticker}_2026-01-15",
        gap_pct=25.0,
        rvol=8.5,
        dollar_volume=5_000_000.0,
        open_price=10.0,
        premarket_headlines=[{"headline": f"{ticker} beats earnings", "source": "reuters"}],
        outcome=outcome,
        correct_signal=correct_signal,
        catalyst_type=catalyst_type,
        label_confidence=LabelConfidence.AUTO_HIGH,
    )


def _make_run_result(
    scenario: LabeledScenario,
    model_id: str,
    signal: str,
    confidence: float = 0.8,
    catalyst_type: str = "earnings",
    parse_success: bool = True,
) -> AgentRunResult:
    return AgentRunResult(
        scenario_id=scenario.scenario_id,
        agent_config=AgentConfig(agent_type="news", model_id=model_id),
        signal_direction=signal,
        signal_confidence=confidence,
        catalyst_type=catalyst_type,
        latency_ms=500.0,
        tokens_input=400,
        tokens_output=200,
        cost_usd=0.001,
        timed_out=False,
        parse_success=parse_success,
        timestamp=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# _normalize_signal
# ---------------------------------------------------------------------------


class TestNormalizeSignal:
    def test_bull_variants(self):
        assert _normalize_signal("BULL") == "BULL"
        assert _normalize_signal("STRONG_BULL") == "BULL"

    def test_bear_variants(self):
        assert _normalize_signal("BEAR") == "BEAR"
        assert _normalize_signal("STRONG_BEAR") == "BEAR"

    def test_neutral(self):
        assert _normalize_signal("NEUTRAL") == "NEUTRAL"

    def test_empty_defaults_neutral(self):
        assert _normalize_signal("") == "NEUTRAL"

    def test_case_insensitive(self):
        assert _normalize_signal("bull") == "BULL"


# ---------------------------------------------------------------------------
# EnsembleVoter._majority_vote
# ---------------------------------------------------------------------------


class TestMajorityVote:
    def _voter(self, tie_break: str = "neutral") -> EnsembleVoter:
        config = EnsembleConfig(
            models=["m1", "m2", "m3"],
            tie_break=tie_break,
        )
        return EnsembleVoter(config, data_dir="data/llm_arena")

    def _resp(self, signal: str, conf: float = 0.8) -> ModelResponse:
        return ModelResponse(
            model_id="test",
            signal=signal,
            confidence=conf,
            catalyst_type="earnings",
            reasoning="",
            latency_ms=100.0,
            success=True,
        )

    def test_unanimous_bull(self):
        voter = self._voter()
        responses = [self._resp("BULL"), self._resp("BULL"), self._resp("BULL")]
        signal, conf, agreed, ratio = voter._majority_vote(responses)
        assert signal == "BULL"
        assert agreed == 3
        assert ratio == pytest.approx(1.0)

    def test_two_to_one_bull(self):
        voter = self._voter()
        responses = [self._resp("BULL"), self._resp("BULL"), self._resp("BEAR")]
        signal, conf, agreed, ratio = voter._majority_vote(responses)
        assert signal == "BULL"
        assert agreed == 2
        assert ratio == pytest.approx(2 / 3, abs=0.001)

    def test_strong_bull_normalizes_to_bull(self):
        voter = self._voter()
        responses = [
            self._resp("STRONG_BULL"),
            self._resp("BULL"),
            self._resp("BEAR"),
        ]
        signal, conf, agreed, ratio = voter._majority_vote(responses)
        assert signal == "BULL"
        assert agreed == 2

    def test_true_tie_returns_tie_break(self):
        voter = self._voter(tie_break="neutral")
        responses = [self._resp("BULL"), self._resp("BEAR"), self._resp("NEUTRAL")]
        signal, _, _, _ = voter._majority_vote(responses)
        assert signal == "NEUTRAL"

    def test_not_enough_successful_responses(self):
        voter = self._voter()
        # Only 1 successful response (require_min_responses=2)
        failed = ModelResponse(
            model_id="m1", signal="NEUTRAL", confidence=0.5,
            catalyst_type="unknown", reasoning="", latency_ms=0.0,
            success=False, error="timeout",
        )
        success = self._resp("BULL")
        signal, conf, agreed, ratio = voter._majority_vote([failed, success, failed])
        assert signal == "NEUTRAL"   # tie_break
        assert agreed == 0
        assert ratio == 0.0

    def test_confidence_averaged_over_agreeing_models(self):
        voter = self._voter()
        responses = [
            self._resp("BULL", conf=0.9),
            self._resp("BULL", conf=0.7),
            self._resp("BEAR", conf=0.8),
        ]
        signal, conf, agreed, ratio = voter._majority_vote(responses)
        assert signal == "BULL"
        assert conf == pytest.approx(0.8, abs=0.01)  # avg(0.9, 0.7)


# ---------------------------------------------------------------------------
# EnsembleVoter._most_common_catalyst
# ---------------------------------------------------------------------------


class TestMostCommonCatalyst:
    def _voter(self) -> EnsembleVoter:
        config = EnsembleConfig(models=["m1", "m2", "m3"])
        return EnsembleVoter(config, data_dir="data/llm_arena")

    def _resp(self, signal: str, catalyst: str) -> ModelResponse:
        return ModelResponse(
            model_id="test", signal=signal, confidence=0.8,
            catalyst_type=catalyst, reasoning="", latency_ms=100.0, success=True,
        )

    def test_most_common_among_agreeing(self):
        voter = self._voter()
        responses = [
            self._resp("BULL", "earnings"),
            self._resp("BULL", "earnings"),
            self._resp("BEAR", "contract"),
        ]
        catalyst = voter._most_common_catalyst(responses, "BULL")
        assert catalyst == "earnings"

    def test_no_successful_responses_returns_unknown(self):
        voter = self._voter()
        failed = ModelResponse(
            model_id="m1", signal="BULL", confidence=0.5, catalyst_type="earnings",
            reasoning="", latency_ms=0.0, success=False,
        )
        catalyst = voter._most_common_catalyst([failed], "BULL")
        assert catalyst == "unknown"


# ---------------------------------------------------------------------------
# EnsembleVoter.evaluate (mocked async)
# ---------------------------------------------------------------------------


class TestEnsembleVoterEvaluate:
    """Tests for EnsembleVoter.evaluate() using mocked harness calls."""

    def _patch_harness(self, mock_results: list[AgentRunResult]):
        """Return a context manager that patches _run_live_single_async on the class.

        Each call pops the next result from the list in order (asyncio.gather
        calls them in list order, but they're all awaited together so the mock
        just needs to return each result for each invocation).
        """
        results_iter = iter(mock_results)

        async def _fake_run(self_harness, scenario, cfg):
            return next(results_iter)

        return patch(
            "src.llm_arena.harness.AgentHarness._run_live_single_async",
            new=_fake_run,
        )

    @pytest.mark.asyncio
    async def test_evaluate_majority_bull(self):
        scenario = _make_scenario()
        config = EnsembleConfig(models=["m1", "m2", "m3"])
        voter = EnsembleVoter(config, data_dir="data/llm_arena")

        mock_results = [
            _make_run_result(scenario, "m1", "BULL"),
            _make_run_result(scenario, "m2", "BULL"),
            _make_run_result(scenario, "m3", "BEAR"),
        ]

        with self._patch_harness(mock_results):
            result = await voter.evaluate(scenario)

        assert result.voted_signal == "BULL"
        assert result.models_responded == 3
        assert result.models_agreed == 2
        assert result.agreement_ratio == pytest.approx(2 / 3, abs=0.001)
        assert result.dissenting_model == "m3"
        assert result.dissenting_signal == "BEAR"

    @pytest.mark.asyncio
    async def test_evaluate_unanimous_bear(self):
        scenario = _make_scenario(
            outcome=StockOutcome.FADER,
            correct_signal=CorrectSignal.BEAR,
        )
        config = EnsembleConfig(models=["m1", "m2", "m3"])
        voter = EnsembleVoter(config, data_dir="data/llm_arena")

        mock_results = [
            _make_run_result(scenario, "m1", "BEAR"),
            _make_run_result(scenario, "m2", "BEAR"),
            _make_run_result(scenario, "m3", "BEAR"),
        ]

        with self._patch_harness(mock_results):
            result = await voter.evaluate(scenario)

        assert result.voted_signal == "BEAR"
        assert result.models_agreed == 3
        assert result.agreement_ratio == pytest.approx(1.0)
        assert result.dissenting_model == ""

    @pytest.mark.asyncio
    async def test_evaluate_timeout_fallback(self):
        """If fewer than require_min_responses succeed, falls back to tie_break."""
        scenario = _make_scenario()
        config = EnsembleConfig(models=["m1", "m2", "m3"], require_min_responses=2)
        voter = EnsembleVoter(config, data_dir="data/llm_arena")

        mock_results = [
            _make_run_result(scenario, "m1", "NEUTRAL", parse_success=False),
            _make_run_result(scenario, "m2", "NEUTRAL", parse_success=False),
            _make_run_result(scenario, "m3", "BULL"),
        ]

        with self._patch_harness(mock_results):
            result = await voter.evaluate(scenario)

        # Only 1 success < require_min_responses=2 → tie_break
        assert result.voted_signal == "NEUTRAL"
        assert result.models_responded == 1


# ---------------------------------------------------------------------------
# EnsembleResult serialisation round-trip
# ---------------------------------------------------------------------------


class TestEnsembleResultSerde:
    def _make_result(self) -> EnsembleResult:
        return EnsembleResult(
            scenario_id="TEST_2026-01-15",
            responses=[
                ModelResponse(
                    model_id="m1",
                    signal="BULL",
                    confidence=0.85,
                    catalyst_type="earnings",
                    reasoning="Beat EPS",
                    latency_ms=450.0,
                    success=True,
                ),
                ModelResponse(
                    model_id="m2",
                    signal="BULL",
                    confidence=0.75,
                    catalyst_type="earnings",
                    reasoning="Revenue up",
                    latency_ms=600.0,
                    success=True,
                ),
                ModelResponse(
                    model_id="m3",
                    signal="BEAR",
                    confidence=0.6,
                    catalyst_type="unknown",
                    reasoning="Guidance cut",
                    latency_ms=380.0,
                    success=True,
                ),
            ],
            voted_signal="BULL",
            voted_confidence=0.80,
            voted_catalyst_type="earnings",
            agreement_ratio=2 / 3,
            models_responded=3,
            models_agreed=2,
            total_latency_ms=620.0,
            total_cost=0.003,
            dissenting_model="m3",
            dissenting_signal="BEAR",
        )

    def test_round_trip(self):
        original = self._make_result()
        restored = EnsembleResult.from_dict(original.to_dict())
        assert restored.scenario_id == original.scenario_id
        assert restored.voted_signal == original.voted_signal
        assert restored.agreement_ratio == original.agreement_ratio
        assert restored.dissenting_model == original.dissenting_model
        assert len(restored.responses) == 3
        assert restored.responses[0].model_id == "m1"
        assert restored.responses[2].signal == "BEAR"


# ---------------------------------------------------------------------------
# _dir_correct_ensemble
# ---------------------------------------------------------------------------


class TestDirCorrectEnsemble:
    def _result(self, signal: str) -> EnsembleResult:
        return EnsembleResult(
            scenario_id="X",
            responses=[],
            voted_signal=signal,
            voted_confidence=0.8,
            voted_catalyst_type="earnings",
            agreement_ratio=1.0,
            models_responded=3,
            models_agreed=3,
            total_latency_ms=500.0,
            total_cost=0.003,
        )

    def test_bull_on_runner_correct(self):
        scenario = _make_scenario(
            outcome=StockOutcome.RUNNER, correct_signal=CorrectSignal.BULL
        )
        result = self._result("BULL")
        assert _dir_correct_ensemble(result, scenario) is True

    def test_bull_on_fader_incorrect(self):
        scenario = _make_scenario(
            outcome=StockOutcome.FADER, correct_signal=CorrectSignal.BEAR
        )
        result = self._result("BULL")
        assert _dir_correct_ensemble(result, scenario) is False

    def test_strong_bull_on_runner_correct(self):
        scenario = _make_scenario(
            outcome=StockOutcome.RUNNER, correct_signal=CorrectSignal.STRONG_BULL
        )
        result = self._result("BULL")
        assert _dir_correct_ensemble(result, scenario) is True

    def test_neutral_on_neutral_correct(self):
        scenario = _make_scenario(
            outcome=StockOutcome.FLAT, correct_signal=CorrectSignal.NEUTRAL
        )
        result = self._result("NEUTRAL")
        assert _dir_correct_ensemble(result, scenario) is True


# ---------------------------------------------------------------------------
# format_ensemble_result (smoke test — just ensure no exceptions)
# ---------------------------------------------------------------------------


class TestFormatEnsembleResult:
    def _make_scorecard_mock(self, model_id: str = "m1"):
        from src.llm_arena.scoring import (
            AgentScorecard,
            ClassificationMetrics,
            OperationalMetrics,
            FinancialMetrics,
        )

        c = ClassificationMetrics(
            direction_accuracy=0.65,
            catalyst_accuracy=0.55,
            true_positives=10,
            true_negatives=15,
            false_positives=5,
            false_negatives=8,
            precision=0.667,
            recall=0.556,
            f1_score=0.606,
            calibration_error=0.08,
            overconfidence_rate=0.4,
            accuracy_on_runners=0.70,
            accuracy_on_faders=0.60,
        )
        o = OperationalMetrics(
            timeout_rate=0.02,
            parse_success_rate=0.98,
            median_latency_ms=450.0,
            p95_latency_ms=900.0,
            p99_latency_ms=1200.0,
            total_tokens_input=20000,
            total_tokens_output=10000,
            avg_tokens_per_call=600.0,
        )
        f = FinancialMetrics(
            signal_value=1500.0,
            cost_per_signal=0.0008,
            roi=375.0,
            avoided_losses=800.0,
            captured_gains=700.0,
        )
        return AgentScorecard(
            agent_type=model_id,
            config={"model_id": model_id},
            scenario_count=50,
            classification=c,
            operational=o,
            financial=f,
            accuracy_by_catalyst={"earnings": 0.7, "contract": 0.5},
            accuracy_by_outcome={"runner": 0.7, "fader": 0.6},
        )

    def test_format_no_exception(self):
        models = ["qwen3-235b", "llama-3.3-70b", "mixtral-8x7b"]
        scorecard = self._make_scorecard_mock("ensemble")
        individual = {m: self._make_scorecard_mock(m) for m in models}

        result = EnsembleExperimentResult(
            name="ensemble_v1",
            models=models,
            scenario_count=50,
            individual_scorecards=individual,
            ensemble_scorecard=scorecard,
            comparisons={m: {"direction_accuracy_delta": 0.05, "f1_delta": 0.02} for m in models},
            mean_agreement_ratio=0.78,
            unanimous_agreement_pct=0.55,
            ensemble_results=[],
            best_individual_model="qwen3-235b",
            ensemble_vs_best_delta=0.04,
            ensemble_vs_best_p_value=0.038,
            ensemble_vs_best_significant=True,
            run_duration_seconds=142.5,
            total_cost_usd=0.045,
            completed_at=datetime.now(timezone.utc),
        )

        report = format_ensemble_result(result)
        assert "ENSEMBLE EXPERIMENT" in report
        assert "ensemble_v1" in report
        assert "qwen3-235b" in report
        assert "MAJORITY VOTE" in report
        assert "STATISTICAL SIGNIFICANCE" in report
        assert "YES" in report  # significant
