"""Phase 7 tests for src.model_arena — catalog, runner, metrics.

Network calls are mocked. Real-API smoke is performed via the CLI separately.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.model_arena.metrics import (
    compute_metrics,
    confusion_matrix,
    rank_by_cost_per_correct,
)
from src.model_arena.models import ModelEntry, available_models, load_catalog
from src.model_arena.runner import ModelResponse, run_task
from src.model_arena.tasks import (
    CatalystClassificationTask,
    Task,
    TaskExample,
    default_classification_parse,
    load_task,
)


# ── Catalog tests ────────────────────────────────────────────────────────


class TestCatalog:

    def test_catalog_loads(self):
        catalog = load_catalog()
        assert len(catalog) > 0, "catalog should have at least one entry"
        keys = {m.key for m in catalog}
        # Spot check expected entries
        assert "claude-haiku-4-5" in keys
        assert "qwen-3-235b" in keys
        assert "deepseek-v3-1" in keys

    def test_each_entry_has_required_fields(self):
        for m in load_catalog():
            assert m.key
            assert m.provider in ("anthropic", "together", "openai")
            assert m.api_model_id
            assert m.input_price_per_mtok > 0
            assert m.output_price_per_mtok > 0
            assert m.context_window > 0

    def test_cost_calculation(self):
        m = ModelEntry(
            key="test", provider="together",
            api_model_id="dummy",
            input_price_per_mtok=1.0,
            output_price_per_mtok=2.0,
            context_window=32000,
        )
        # 1M input + 1M output should cost $1 + $2 = $3
        assert m.cost_for(1_000_000, 1_000_000) == pytest.approx(3.0)
        assert m.cost_for(0, 0) == 0.0

    def test_has_api_key_with_alternate_env_names(self, monkeypatch):
        """The project's .env uses TOGETHER_AI_API_KEY; we accept it too."""
        monkeypatch.delenv("TOGETHER_API_KEY", raising=False)
        monkeypatch.delenv("TOGETHER_AI_API_KEY", raising=False)
        m = ModelEntry(
            key="t", provider="together", api_model_id="x",
            input_price_per_mtok=0.1, output_price_per_mtok=0.1,
            context_window=1000,
        )
        assert m.has_api_key() is False
        monkeypatch.setenv("TOGETHER_AI_API_KEY", "sk-test")
        assert m.has_api_key() is True

    def test_available_models_filters_no_key(self, monkeypatch):
        monkeypatch.delenv("TOGETHER_API_KEY", raising=False)
        monkeypatch.delenv("TOGETHER_AI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        ms = available_models(require_key=True)
        assert ms == [], "all models should be filtered when no API keys set"


# ── Task / parser tests ──────────────────────────────────────────────────


class TestTasks:

    def test_catalyst_task_loads_with_examples(self):
        task = CatalystClassificationTask.build()
        assert task.name == "catalyst_classification"
        assert len(task.examples) >= 40, "expect >=40 hand-built examples"
        assert "FDA" in task.valid_outputs
        assert "OFFERING" in task.valid_outputs

    def test_load_task_unknown_raises(self):
        with pytest.raises(ValueError, match="no task registered"):
            load_task("nonexistent")

    def test_render_user_prompt(self):
        task = CatalystClassificationTask.build()
        ex = task.examples[0]
        prompt = task.render_user_prompt(ex)
        assert ex.inputs["ticker"] in prompt
        assert "Headline:" in prompt

    def test_default_parser_substring_match(self):
        valid = ("FDA", "EARNINGS", "M_AND_A")
        assert default_classification_parse("The catalyst is FDA approval", valid) == "FDA"
        assert default_classification_parse("EARNINGS were strong", valid) == "EARNINGS"

    def test_default_parser_json(self):
        valid = ("FDA", "NONE")
        assert default_classification_parse('{"label": "FDA"}', valid) == "FDA"
        assert default_classification_parse('{"label": "fda"}', valid) == "FDA"

    def test_default_parser_no_match(self):
        valid = ("FDA", "EARNINGS")
        assert default_classification_parse("Nothing relevant here", valid) is None
        assert default_classification_parse("", valid) is None

    def test_default_parser_longest_match(self):
        # M_AND_A contains 'M' and 'A' but should match as a whole word
        valid = ("M", "M_AND_A")
        # Both could match; longest wins
        assert default_classification_parse("This is M_AND_A activity", valid) == "M_AND_A"


# ── Metrics tests ────────────────────────────────────────────────────────


class TestMetrics:

    def _mk_response(self, model_key="m1", correct=True, error=None,
                     truth="A", prediction="A", cost=0.001, latency=100.0):
        return ModelResponse(
            sample_id="x", model_key=model_key,
            prompt_tokens=100, completion_tokens=20,
            latency_ms=latency, cost_usd=cost,
            raw_response="{}", prediction=prediction, truth=truth,
            correct=correct, error=error,
        )

    def test_compute_metrics_basic(self):
        responses = [
            self._mk_response(correct=True, cost=0.01),
            self._mk_response(correct=True, cost=0.01),
            self._mk_response(correct=False, prediction="B", cost=0.01),
        ]
        m = compute_metrics(responses)
        assert len(m) == 1
        row = m[0]
        assert row["n"] == 3
        assert row["n_correct"] == 2
        assert row["accuracy"] == pytest.approx(2/3)
        assert row["total_cost_usd"] == pytest.approx(0.03)

    def test_cost_per_correct(self):
        responses = [
            self._mk_response(correct=True, cost=0.10),
            self._mk_response(correct=True, cost=0.20),
        ]
        m = compute_metrics(responses)
        assert m[0]["cost_per_correct_usd"] == pytest.approx(0.30 / 2)

    def test_cost_per_correct_inf_when_zero(self):
        responses = [self._mk_response(correct=False, prediction="B", cost=0.01)]
        m = compute_metrics(responses)
        assert m[0]["cost_per_correct_usd"] == float("inf")

    def test_error_rate(self):
        responses = [
            self._mk_response(error=None),
            self._mk_response(error="timeout"),
            self._mk_response(error="oops"),
        ]
        m = compute_metrics(responses)
        assert m[0]["error_rate"] == pytest.approx(2/3)

    def test_rank_by_cost_per_correct(self):
        m = [
            {"model_key": "expensive", "cost_per_correct_usd": 1.00, "accuracy": 0.9},
            {"model_key": "cheap_smart", "cost_per_correct_usd": 0.10, "accuracy": 0.8},
            {"model_key": "broken", "cost_per_correct_usd": float("inf"), "accuracy": 0.0},
        ]
        ranked = rank_by_cost_per_correct(m)
        assert ranked[0]["model_key"] == "cheap_smart"
        assert ranked[-1]["model_key"] == "broken"

    def test_macro_f1_multi_class(self):
        # Perfect classifier on 2 classes
        responses = [
            self._mk_response(truth="A", prediction="A", correct=True),
            self._mk_response(truth="A", prediction="A", correct=True),
            self._mk_response(truth="B", prediction="B", correct=True),
            self._mk_response(truth="B", prediction="B", correct=True),
        ]
        m = compute_metrics(responses)
        assert m[0]["macro_f1"] == pytest.approx(1.0)


# ── Runner tests (mocked litellm) ────────────────────────────────────────


class TestRunner:

    @pytest.mark.asyncio
    async def test_run_task_handles_timeout_gracefully(self):
        """Verify timeout produces an ERROR response, not a crash."""
        import asyncio as _asyncio

        async def slow_completion(*args, **kwargs):
            await _asyncio.sleep(10)
            return MagicMock()

        with patch("litellm.acompletion", side_effect=slow_completion):
            task = CatalystClassificationTask.build()
            model = ModelEntry(
                key="t", provider="together", api_model_id="dummy",
                input_price_per_mtok=0.1, output_price_per_mtok=0.1,
                context_window=1000, enabled=True, verified=False,
            )
            responses = await run_task(task, [model], n_samples=1, timeout_s=0.05, max_retries=0)
            assert len(responses) == 1
            assert responses[0].error and "timeout" in responses[0].error.lower()
            assert responses[0].prediction is None

    @pytest.mark.asyncio
    async def test_run_task_handles_exception_gracefully(self):
        async def failing_completion(*args, **kwargs):
            raise RuntimeError("provider down")

        with patch("litellm.acompletion", side_effect=failing_completion):
            task = CatalystClassificationTask.build()
            model = ModelEntry(
                key="t", provider="together", api_model_id="dummy",
                input_price_per_mtok=0.1, output_price_per_mtok=0.1,
                context_window=1000, enabled=True, verified=False,
            )
            responses = await run_task(task, [model], n_samples=1, timeout_s=5.0, max_retries=0)
            assert len(responses) == 1
            assert responses[0].error and "RuntimeError" in responses[0].error

    @pytest.mark.asyncio
    async def test_run_task_records_correct_response(self):
        """Mock a successful response and verify parser extracts correctly."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"label": "FDA"}'))]
        mock_response.usage = MagicMock(prompt_tokens=50, completion_tokens=10)

        async def good_completion(*args, **kwargs):
            return mock_response

        with patch("litellm.acompletion", side_effect=good_completion):
            task = CatalystClassificationTask.build()
            # Use first FDA example to verify correct
            fda_ex = next(e for e in task.examples if e.expected_output == "FDA")
            single_task = Task(
                name=task.name, system_prompt=task.system_prompt,
                user_prompt_template=task.user_prompt_template,
                examples=[fda_ex],
                valid_outputs=task.valid_outputs,
                parse_fn_name=task.parse_fn_name,
            )
            model = ModelEntry(
                key="t", provider="together", api_model_id="dummy",
                input_price_per_mtok=0.1, output_price_per_mtok=0.1,
                context_window=1000, enabled=True, verified=False,
            )
            responses = await run_task(single_task, [model], n_samples=1, timeout_s=5.0)
            assert len(responses) == 1
            r = responses[0]
            assert r.error is None
            assert r.prediction == "FDA"
            assert r.correct is True
            assert r.cost_usd > 0
