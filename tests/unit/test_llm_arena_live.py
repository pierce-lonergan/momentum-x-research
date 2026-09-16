"""Tests for LLM Arena live mode — Component 3.

All tests use mocking so no real API calls are made.

Node ID: tests.unit.test_llm_arena_live
Graph Link: tested_by → src.llm_arena.harness (live mode)

Tests:
  1.  test_live_single_success          — mock API response → AgentRunResult populated
  2.  test_live_single_timeout          — mock timeout → timed_out=True
  3.  test_live_single_api_error        — mock API error → parse_success=False, error set
  4.  test_live_single_parse_failure    — mock bad JSON → parse_success=False
  5.  test_live_single_unknown_signal   — mock unrecognised signal → parse_success=False
  6.  test_live_batch_sequential        — mock multiple calls → one result per scenario
  7.  test_live_batch_rate_limit_delay  — verify delay is applied between calls
  8.  test_cost_calculation             — verify cost computed from token counts
  9.  test_model_registry_accessible    — all registry entries have required keys
  10. test_model_registry_known_models  — experiment models present in registry
  11. test_parse_live_response_bull      — parse BULL response correctly
  12. test_parse_live_response_neutral   — parse NEUTRAL response correctly
  13. test_calculate_cost_zero_tokens    — zero tokens → zero cost
  14. test_run_single_live_stores_result — live result appended to harness._results
  15. test_unsupported_agent_type       — non-news agent type returns error result
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm_arena.harness import (
    MODEL_REGISTRY,
    AgentConfig,
    AgentHarness,
    AgentRunResult,
    _calculate_cost,
    _parse_live_response,
    _SimpleNewsItem,
)
from src.llm_arena.models import (
    CatalystType,
    CorrectSignal,
    LabelConfidence,
    LabeledScenario,
    StockOutcome,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_scenario(ticker: str = "AAPL", scenario_date: date = date(2026, 2, 1)) -> LabeledScenario:
    return LabeledScenario(
        scenario_id=f"{ticker}_{scenario_date}",
        ticker=ticker,
        date=scenario_date,
        gap_pct=0.12,
        rvol=4.5,
        open_price=25.0,
        close_price=28.0,
        max_gain_pct=0.15,
        max_drawdown_pct=-0.03,
        dollar_volume=5_000_000,
        premarket_headlines=[
            {
                "headline": f"{ticker} Receives FDA Approval for Drug XYZ",
                "source": "businesswire",
                "published_at": "2026-02-01T07:30:00+00:00",
                "summary": "The FDA approved Drug XYZ for treatment of condition ABC.",
            }
        ],
        sec_filings=[],
        correct_signal=CorrectSignal.BULL,
        outcome=StockOutcome.RUNNER,
        catalyst_type=CatalystType.FDA,
        label_confidence=LabelConfidence.VERIFIED,
        actual_agent_signals={},
    )


def _mock_litellm_response(
    signal: str = "BULL",
    confidence: float = 0.80,
    catalyst_type: str = "FDA_APPROVAL",
    tokens_input: int = 500,
    tokens_output: int = 150,
) -> MagicMock:
    """Build a mock litellm response object."""
    content = json.dumps({
        "signal": signal,
        "confidence": confidence,
        "catalyst_type": catalyst_type,
        "catalyst_specificity": "CONFIRMED",
        "sentiment_score": 0.8,
        "key_reasoning": "Specific FDA approval with named drug.",
        "red_flags": [],
        "source_citations": [],
    })
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = content
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = tokens_input
    mock_resp.usage.completion_tokens = tokens_output
    return mock_resp


@pytest.fixture
def scenario():
    return _make_scenario()


@pytest.fixture
def news_config():
    return AgentConfig(
        agent_type="news",
        model_id="qwen3-235b",
        temperature=0.0,
        max_tokens=1024,
        timeout_seconds=25.0,
    )


@pytest.fixture
def harness():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield AgentHarness(tmpdir)


# ---------------------------------------------------------------------------
# 1. test_live_single_success
# ---------------------------------------------------------------------------


class TestLiveSingleSuccess:
    def test_live_single_success(self, harness, scenario, news_config):
        """Mock API response → AgentRunResult populated with correct fields."""
        mock_resp = _mock_litellm_response(
            signal="BULL",
            confidence=0.80,
            catalyst_type="FDA_APPROVAL",
            tokens_input=500,
            tokens_output=150,
        )

        with patch("src.llm_arena.harness._build_live_prompt") as mock_prompt, \
             patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_prompt.return_value = ("system", "user")
            mock_llm.return_value = mock_resp

            result = harness.run_single(scenario, news_config, mode="live")

        assert result.parse_success is True
        assert result.signal_direction == "BULL"
        assert result.signal_confidence == pytest.approx(0.80)
        assert result.catalyst_type == "FDA_APPROVAL"
        assert result.timed_out is False
        assert result.latency_ms > 0
        assert result.tokens_input == 500
        assert result.tokens_output == 150
        assert result.cost_usd > 0
        assert result.scenario_id == scenario.scenario_id
        assert result.error is None


# ---------------------------------------------------------------------------
# 2. test_live_single_timeout
# ---------------------------------------------------------------------------


class TestLiveSingleTimeout:
    def test_live_single_timeout(self, harness, scenario, news_config):
        """asyncio.TimeoutError → timed_out=True, parse_success=False."""

        async def slow_call(*args, **kwargs):
            raise asyncio.TimeoutError()

        with patch("src.llm_arena.harness._build_live_prompt") as mock_prompt, \
             patch("litellm.acompletion", side_effect=asyncio.TimeoutError):
            mock_prompt.return_value = ("system", "user")
            result = harness.run_single(scenario, news_config, mode="live")

        assert result.timed_out is True
        assert result.parse_success is False
        assert result.error is not None
        assert "25" in result.error  # timeout_seconds in message


# ---------------------------------------------------------------------------
# 3. test_live_single_api_error
# ---------------------------------------------------------------------------


class TestLiveSingleApiError:
    def test_live_single_api_error(self, harness, scenario, news_config):
        """Generic API exception → parse_success=False, error contains message."""
        with patch("src.llm_arena.harness._build_live_prompt") as mock_prompt, \
             patch("litellm.acompletion", side_effect=Exception("Connection refused")):
            mock_prompt.return_value = ("system", "user")
            result = harness.run_single(scenario, news_config, mode="live")

        assert result.parse_success is False
        assert result.timed_out is False
        assert "Connection refused" in (result.error or "")


# ---------------------------------------------------------------------------
# 4. test_live_single_parse_failure
# ---------------------------------------------------------------------------


class TestLiveSingleParseFailure:
    def test_live_single_parse_failure(self, harness, scenario, news_config):
        """Non-JSON API response → parse_success=False."""
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "not valid json at all"
        mock_resp.usage = None

        with patch("src.llm_arena.harness._build_live_prompt") as mock_prompt, \
             patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_prompt.return_value = ("system", "user")
            mock_llm.return_value = mock_resp
            result = harness.run_single(scenario, news_config, mode="live")

        assert result.parse_success is False
        assert "JSON" in (result.error or "")


# ---------------------------------------------------------------------------
# 5. test_live_single_unknown_signal
# ---------------------------------------------------------------------------


class TestLiveSingleUnknownSignal:
    def test_live_single_unknown_signal(self, harness, scenario, news_config):
        """Response with unrecognised signal value → parse_success=False."""
        bad_content = json.dumps({"signal": "MAYBE", "confidence": 0.5})
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = bad_content
        mock_resp.usage = None

        with patch("src.llm_arena.harness._build_live_prompt") as mock_prompt, \
             patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_prompt.return_value = ("system", "user")
            mock_llm.return_value = mock_resp
            result = harness.run_single(scenario, news_config, mode="live")

        assert result.parse_success is False
        assert "MAYBE" in (result.error or "")


# ---------------------------------------------------------------------------
# 6. test_live_batch_sequential
# ---------------------------------------------------------------------------


class TestLiveBatchSequential:
    def test_live_batch_sequential(self, harness, news_config):
        """Batch live run returns one result per scenario."""
        scenarios = [_make_scenario("AAA"), _make_scenario("BBB"), _make_scenario("CCC")]
        call_count = 0

        def side_effect_factory():
            async def mock_completion(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                return _mock_litellm_response()
            return mock_completion

        with patch("src.llm_arena.harness._build_live_prompt", return_value=("sys", "usr")), \
             patch("litellm.acompletion", side_effect=side_effect_factory()):
            results = harness.run_batch(
                scenarios, news_config, mode="live", rate_limit_delay=0
            )

        assert len(results) == 3
        assert call_count == 3
        assert all(r.parse_success for r in results)
        tickers = {r.scenario_id.split("_")[0] for r in results}
        assert tickers == {"AAA", "BBB", "CCC"}


# ---------------------------------------------------------------------------
# 7. test_live_batch_rate_limit_delay
# ---------------------------------------------------------------------------


class TestLiveBatchRateLimitDelay:
    def test_rate_limit_delay_called(self, harness, news_config):
        """time.sleep is called between calls (N-1 times for N scenarios)."""
        scenarios = [_make_scenario("X1"), _make_scenario("X2"), _make_scenario("X3")]
        sleep_calls = []

        original_sleep = __import__("time").sleep

        def counting_sleep(seconds):
            sleep_calls.append(seconds)

        with patch("src.llm_arena.harness._build_live_prompt", return_value=("sys", "usr")), \
             patch("litellm.acompletion", new_callable=AsyncMock, return_value=_mock_litellm_response()), \
             patch("src.llm_arena.harness.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            mock_time.sleep.side_effect = counting_sleep
            harness.run_batch(
                scenarios, news_config, mode="live", rate_limit_delay=0.5
            )

        # sleep called N-1 times (between calls, not after the last)
        assert mock_time.sleep.call_count == 2
        assert all(c == 0.5 for c in [args[0] for args, _ in mock_time.sleep.call_args_list])


# ---------------------------------------------------------------------------
# 8. test_cost_calculation
# ---------------------------------------------------------------------------


class TestCostCalculation:
    def test_cost_calculated_from_tokens(self, harness, scenario, news_config):
        """cost_usd is computed from token counts and model pricing."""
        model_info = MODEL_REGISTRY["qwen3-235b"]
        expected_cost = _calculate_cost(model_info, tokens_input=500, tokens_output=150)

        mock_resp = _mock_litellm_response(tokens_input=500, tokens_output=150)

        with patch("src.llm_arena.harness._build_live_prompt", return_value=("s", "u")), \
             patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp):
            result = harness.run_single(scenario, news_config, mode="live")

        assert result.cost_usd == pytest.approx(expected_cost, abs=1e-9)

    def test_cost_formula(self):
        model_info = {
            "cost_per_1k_input": 0.001,
            "cost_per_1k_output": 0.002,
        }
        # 1000 input + 500 output = 0.001 + 0.001 = 0.002
        assert _calculate_cost(model_info, 1000, 500) == pytest.approx(0.002)

    def test_cost_zero_tokens(self):
        model_info = {"cost_per_1k_input": 0.001, "cost_per_1k_output": 0.002}
        assert _calculate_cost(model_info, 0, 0) == 0.0


# ---------------------------------------------------------------------------
# 9. test_model_registry_accessible
# ---------------------------------------------------------------------------


class TestModelRegistryAccessible:
    def test_all_registry_entries_have_required_keys(self):
        required_keys = {"api_model_id", "cost_per_1k_input", "cost_per_1k_output", "provider"}
        for model_id, info in MODEL_REGISTRY.items():
            missing = required_keys - set(info)
            assert not missing, f"Model '{model_id}' missing keys: {missing}"

    def test_registry_is_non_empty(self):
        assert len(MODEL_REGISTRY) >= 3

    def test_all_api_model_ids_non_empty(self):
        for model_id, info in MODEL_REGISTRY.items():
            assert info["api_model_id"], f"Empty api_model_id for '{model_id}'"


# ---------------------------------------------------------------------------
# 10. test_model_registry_known_models
# ---------------------------------------------------------------------------


class TestModelRegistryKnownModels:
    """Verify that models used in experiments_library are present."""

    def test_mixtral_in_registry(self):
        assert "mixtral-8x7b" in MODEL_REGISTRY

    def test_qwen3_235b_in_registry(self):
        assert "qwen3-235b" in MODEL_REGISTRY

    def test_claude_haiku_in_registry(self):
        assert "claude-haiku-4-5-20251001" in MODEL_REGISTRY


# ---------------------------------------------------------------------------
# 11. test_parse_live_response_bull
# ---------------------------------------------------------------------------


class TestParseLiveResponseBull:
    def test_parse_bull_response(self):
        scenario = _make_scenario()
        config = AgentConfig(agent_type="news", model_id="qwen3-235b")
        raw_content = json.dumps({
            "signal": "STRONG_BULL",
            "confidence": 0.90,
            "catalyst_type": "FDA_APPROVAL",
            "key_reasoning": "Clear FDA approval.",
        })
        now = datetime.now(timezone.utc)

        result = _parse_live_response(
            scenario=scenario,
            config=config,
            raw_content=raw_content,
            latency_ms=1500.0,
            tokens_input=400,
            tokens_output=120,
            cost_usd=0.0001,
            now=now,
        )

        assert result.parse_success is True
        assert result.signal_direction == "STRONG_BULL"
        assert result.signal_confidence == pytest.approx(0.90)
        assert result.catalyst_type == "FDA_APPROVAL"
        assert result.latency_ms == pytest.approx(1500.0)
        assert result.tokens_input == 400
        assert result.tokens_output == 120
        assert result.cost_usd == pytest.approx(0.0001)
        assert result.error is None


# ---------------------------------------------------------------------------
# 12. test_parse_live_response_neutral
# ---------------------------------------------------------------------------


class TestParseLiveResponseNeutral:
    def test_parse_neutral_response(self):
        scenario = _make_scenario()
        config = AgentConfig(agent_type="news", model_id="qwen3-235b")
        raw_content = json.dumps({
            "signal": "NEUTRAL",
            "confidence": 0.40,
            "catalyst_type": "NONE",
            "key_reasoning": "No specific catalyst found.",
        })
        now = datetime.now(timezone.utc)

        result = _parse_live_response(
            scenario=scenario,
            config=config,
            raw_content=raw_content,
            latency_ms=800.0,
            tokens_input=300,
            tokens_output=80,
            cost_usd=0.00005,
            now=now,
        )

        assert result.parse_success is True
        assert result.signal_direction == "NEUTRAL"
        assert result.error is None


# ---------------------------------------------------------------------------
# 13. test_calculate_cost_zero_tokens (covered in TestCostCalculation)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 14. test_run_single_live_stores_result
# ---------------------------------------------------------------------------


class TestRunSingleLiveStoresResult:
    def test_result_appended_to_harness(self, harness, scenario, news_config):
        """Successful live call appends result to harness internal list."""
        assert len(harness.results()) == 0

        with patch("src.llm_arena.harness._build_live_prompt", return_value=("s", "u")), \
             patch("litellm.acompletion", new_callable=AsyncMock, return_value=_mock_litellm_response()):
            harness.run_single(scenario, news_config, mode="live")

        assert len(harness.results()) == 1
        assert harness.results()[0].scenario_id == scenario.scenario_id


# ---------------------------------------------------------------------------
# 15. test_unsupported_agent_type
# ---------------------------------------------------------------------------


class TestUnsupportedAgentType:
    def test_unsupported_agent_type_returns_error(self, harness, scenario):
        """Agent types other than 'news' return an error result (not raise)."""
        cfg = AgentConfig(agent_type="fundamental", model_id="qwen3-235b")
        result = harness.run_single(scenario, cfg, mode="live")
        assert result.parse_success is False
        assert result.error is not None
        assert "fundamental" in result.error
