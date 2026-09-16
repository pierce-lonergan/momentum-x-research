"""Tests for the LLM Arena Agent Harness (Component 2).

Node ID: tests.unit.test_llm_arena_harness
Graph Link: tested_by → src.llm_arena.harness

Tests:
  1.  test_agent_config_creation
  2.  test_prepare_input_news_agent
  3.  test_prepare_input_fundamental
  4.  test_prepare_input_manipulation
  5.  test_run_replay_single
  6.  test_run_replay_extracts_direction
  7.  test_run_replay_extracts_confidence
  8.  test_run_replay_extracts_catalyst
  9.  test_run_replay_handles_missing_signals
  10. test_run_replay_batch
  11. test_run_replay_latency_zero
  12. test_run_replay_parse_success
  13. test_save_and_load_results
  14. test_result_metadata
  15. test_unknown_model_returns_error_result
  16. test_multiple_agent_types
  17. test_run_result_serialization
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timezone

import pytest

from src.llm_arena.harness import AgentConfig, AgentHarness, AgentRunResult
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


def _make_scenario(
    ticker: str = "BRLS",
    scenario_date: date = date(2026, 2, 10),
    actual_signals: dict | None = None,
) -> LabeledScenario:
    if actual_signals is None:
        actual_signals = {
            "news_agent": {
                "signal": "BULL",
                "confidence": 0.72,
                "reasoning": "Strong FDA approval catalyst.",
                "catalyst_type": "FDA_APPROVAL",
                "model_id": "mistralai/Mixtral-8x7B-Instruct-v0.1",
            }
        }
    return LabeledScenario(
        ticker=ticker,
        date=scenario_date,
        scenario_id=f"{ticker}_{scenario_date}",
        gap_pct=0.45,
        rvol=4.2,
        dollar_volume=8_500_000.0,
        open_price=15.50,
        prev_close=10.00,
        premarket_headlines=["FDA approves BRLS drug", "Strong clinical trial results"],
        sec_filings=["8-K"],
        catalyst_type=CatalystType.FDA,
        outcome=StockOutcome.RUNNER,
        max_gain_pct=0.35,
        correct_signal=CorrectSignal.BULL,
        was_traded=True,
        trade_direction="long",
        trade_pnl=1_250.0,
        actual_agent_signals=actual_signals,
        label_confidence=LabelConfidence.AUTO_HIGH,
        label_source="auto_journal",
    )


@pytest.fixture
def scenario():
    return _make_scenario()


@pytest.fixture
def data_dir(tmp_path):
    d = str(tmp_path / "llm_arena")
    os.makedirs(d, exist_ok=True)
    return d


@pytest.fixture
def harness(data_dir):
    return AgentHarness(data_dir)


@pytest.fixture
def news_config():
    return AgentConfig(agent_type="news", model_id="mixtral-8x7b")


# ---------------------------------------------------------------------------
# 1. AgentConfig creation
# ---------------------------------------------------------------------------


class TestAgentConfigCreation:
    def test_agent_config_creation(self):
        cfg = AgentConfig(agent_type="news", model_id="mixtral-8x7b")
        assert cfg.agent_type == "news"
        assert cfg.model_id == "mixtral-8x7b"
        assert cfg.timeout_seconds == 25.0
        assert cfg.temperature == 0.0
        assert cfg.max_tokens == 1024
        assert cfg.extra_params == {}

    def test_agent_config_custom_params(self):
        cfg = AgentConfig(
            agent_type="fundamental",
            model_id="qwen3-235b",
            timeout_seconds=15.0,
            temperature=0.1,
            max_tokens=2048,
            extra_params={"top_p": 0.9},
        )
        assert cfg.timeout_seconds == 15.0
        assert cfg.temperature == 0.1
        assert cfg.max_tokens == 2048
        assert cfg.extra_params["top_p"] == 0.9

    def test_agent_config_roundtrip(self):
        cfg = AgentConfig(
            agent_type="manipulation",
            model_id="qwen3-480b",
            prompt_template="Custom prompt {ticker}",
        )
        d = cfg.to_dict()
        cfg2 = AgentConfig.from_dict(d)
        assert cfg2.agent_type == cfg.agent_type
        assert cfg2.model_id == cfg.model_id
        assert cfg2.prompt_template == cfg.prompt_template


# ---------------------------------------------------------------------------
# 2. prepare_input — news agent
# ---------------------------------------------------------------------------


class TestPrepareInputNews:
    def test_prepare_input_news_agent(self, harness, scenario):
        inp = harness.prepare_input(scenario, "news")
        assert inp["ticker"] == "BRLS"
        assert isinstance(inp["news_items"], list)
        assert len(inp["news_items"]) == 2
        # Each headline should be wrapped in a dict
        first = inp["news_items"][0]
        assert "headline" in first
        assert "FDA" in first["headline"]

    def test_prepare_input_news_accepts_full_id(self, harness, scenario):
        inp = harness.prepare_input(scenario, "news_agent")
        assert inp["ticker"] == "BRLS"

    def test_prepare_input_news_dict_headlines_passthrough(self, harness):
        s = _make_scenario()
        s.premarket_headlines = [
            {"headline": "FDA clears drug", "source": "Reuters", "published_at": "2026-02-10", "summary": ""}
        ]
        inp = harness.prepare_input(s, "news")
        assert inp["news_items"][0]["source"] == "Reuters"


# ---------------------------------------------------------------------------
# 3. prepare_input — fundamental agent
# ---------------------------------------------------------------------------


class TestPrepareInputFundamental:
    def test_prepare_input_fundamental(self, harness, scenario):
        inp = harness.prepare_input(scenario, "fundamental")
        assert inp["ticker"] == "BRLS"
        assert "float_shares" in inp
        assert "recent_filings" in inp

    def test_prepare_input_fundamental_sec_filings_mapped(self, harness):
        s = _make_scenario()
        s.sec_filings = ["S-3", "424B5"]
        inp = harness.prepare_input(s, "fundamental")
        forms = [f["form"] for f in inp["recent_filings"]]
        assert "S-3" in forms
        assert "424B5" in forms


# ---------------------------------------------------------------------------
# 4. prepare_input — manipulation classifier
# ---------------------------------------------------------------------------


class TestPrepareInputManipulation:
    def test_prepare_input_manipulation(self, harness, scenario):
        inp = harness.prepare_input(scenario, "manipulation")
        assert inp["ticker"] == "BRLS"
        assert inp["gap_pct"] == pytest.approx(0.45)
        assert inp["rvol"] == pytest.approx(4.2)
        assert "filing_summary" in inp
        assert isinstance(inp["news_items"], list)

    def test_prepare_input_manipulation_detects_424b5(self, harness):
        s = _make_scenario()
        s.sec_filings = ["424B5", "8-K"]
        inp = harness.prepare_input(s, "manipulation")
        assert inp["filing_summary"]["has_424b5_same_day"] is True

    def test_prepare_input_unknown_agent_raises(self, harness, scenario):
        with pytest.raises(ValueError, match="Unknown agent_type"):
            harness.prepare_input(scenario, "nonexistent_agent_xyz")


# ---------------------------------------------------------------------------
# 5. run_single — replay mode returns stored signals
# ---------------------------------------------------------------------------


class TestRunReplaySingle:
    def test_run_replay_single(self, harness, scenario, news_config):
        result = harness.run_single(scenario, news_config, mode="replay")
        assert isinstance(result, AgentRunResult)
        assert result.scenario_id == "BRLS_2026-02-10"

    def test_run_replay_extracts_direction(self, harness, scenario, news_config):
        result = harness.run_single(scenario, news_config, mode="replay")
        assert result.signal_direction == "BULL"

    def test_run_replay_extracts_confidence(self, harness, scenario, news_config):
        result = harness.run_single(scenario, news_config, mode="replay")
        assert result.signal_confidence == pytest.approx(0.72)

    def test_run_replay_extracts_catalyst(self, harness, scenario, news_config):
        result = harness.run_single(scenario, news_config, mode="replay")
        assert result.catalyst_type == "FDA_APPROVAL"

    def test_run_replay_latency_zero(self, harness, scenario, news_config):
        result = harness.run_single(scenario, news_config, mode="replay")
        assert result.latency_ms == pytest.approx(0.0)

    def test_run_replay_parse_success(self, harness, scenario, news_config):
        result = harness.run_single(scenario, news_config, mode="replay")
        assert result.parse_success is True


# ---------------------------------------------------------------------------
# 9. Graceful handling when no signals stored
# ---------------------------------------------------------------------------


class TestRunReplayMissingSignals:
    def test_run_replay_handles_missing_signals(self, harness, news_config):
        s = _make_scenario(actual_signals={})
        result = harness.run_single(s, news_config, mode="replay")
        assert result.parse_success is False
        assert result.signal_direction is None
        assert result.error is not None

    def test_run_replay_handles_wrong_agent_type(self, harness):
        s = _make_scenario(actual_signals={"news_agent": {"signal": "BULL", "confidence": 0.6}})
        cfg = AgentConfig(agent_type="technical", model_id="qwen3-480b")
        result = harness.run_single(s, cfg, mode="replay")
        assert result.parse_success is False

    def test_run_replay_handles_none_signal_value(self, harness, news_config):
        s = _make_scenario(actual_signals={"news_agent": {"signal": None, "confidence": 0.5}})
        result = harness.run_single(s, news_config, mode="replay")
        assert result.parse_success is False


# ---------------------------------------------------------------------------
# 10. Batch replay
# ---------------------------------------------------------------------------


class TestRunReplayBatch:
    def test_run_replay_batch(self, harness, news_config):
        scenarios = [
            _make_scenario("BRLS", date(2026, 2, 10)),
            _make_scenario("GTBP", date(2026, 2, 11), actual_signals={
                "news_agent": {"signal": "STRONG_BULL", "confidence": 0.85, "reasoning": "M&A confirmed"}
            }),
            _make_scenario("SIGA", date(2026, 2, 12), actual_signals={
                "news_agent": {"signal": "NEUTRAL", "confidence": 0.50, "reasoning": "No catalyst"}
            }),
        ]
        results = harness.run_batch(scenarios, news_config, mode="replay")
        assert len(results) == 3
        directions = {r.signal_direction for r in results}
        assert "BULL" in directions
        assert "STRONG_BULL" in directions
        assert "NEUTRAL" in directions

    def test_run_replay_batch_accumulates_in_harness(self, harness, news_config):
        scenarios = [_make_scenario("A", date(2026, 1, i + 1)) for i in range(5)]
        harness.run_batch(scenarios, news_config, mode="replay")
        assert len(harness.results()) == 5


# ---------------------------------------------------------------------------
# 13. Save and load results
# ---------------------------------------------------------------------------


class TestSaveAndLoadResults:
    def test_save_and_load_results(self, harness, scenario, news_config):
        harness.run_single(scenario, news_config, mode="replay")
        path = harness.save_results("test_experiment")
        assert os.path.exists(path)
        assert path.endswith("test_experiment.json")

        harness2 = AgentHarness(harness._data_dir)
        loaded = harness2.load_results("test_experiment")
        assert len(loaded) == 1
        assert loaded[0].scenario_id == "BRLS_2026-02-10"
        assert loaded[0].signal_direction == "BULL"

    def test_result_metadata(self, harness, scenario, news_config):
        harness.run_single(scenario, news_config, mode="replay")
        path = harness.save_results("metadata_test")
        with open(path) as fh:
            payload = json.load(fh)
        assert payload["experiment_name"] == "metadata_test"
        assert "saved_at" in payload
        assert payload["result_count"] == 1
        assert payload["scenario_count"] == 1

    def test_load_nonexistent_raises(self, harness):
        with pytest.raises(FileNotFoundError):
            harness.load_results("does_not_exist")


# ---------------------------------------------------------------------------
# 15. Live mode — unknown model gracefully returns an error result
# ---------------------------------------------------------------------------


class TestLiveModeUnknownModel:
    """Live mode is now implemented. Unknown model_id returns an error result
    instead of raising (avoids crashing the whole batch on one bad config)."""

    def test_unknown_model_returns_error_result(self, harness, scenario):
        """Requesting a model not in MODEL_REGISTRY returns a failed AgentRunResult."""
        cfg = AgentConfig(agent_type="news", model_id="nonexistent-model-xyz")
        result = harness.run_single(scenario, cfg, mode="live")
        assert result.parse_success is False
        assert result.timed_out is False
        assert "nonexistent-model-xyz" in (result.error or "")

    def test_unknown_model_batch_returns_error_results(self, harness):
        """Batch live run with unknown model returns one error result per scenario."""
        cfg = AgentConfig(agent_type="news", model_id="nonexistent-model-xyz")
        scenarios = [_make_scenario("AAA"), _make_scenario("BBB")]
        results = harness.run_batch(scenarios, cfg, mode="live", rate_limit_delay=0)
        assert len(results) == 2
        assert all(not r.parse_success for r in results)


# ---------------------------------------------------------------------------
# 16. Multiple agent types from same scenario
# ---------------------------------------------------------------------------


class TestMultipleAgentTypes:
    def test_multiple_agent_types(self, harness):
        multi_signals = {
            "news_agent": {
                "signal": "BULL",
                "confidence": 0.70,
                "reasoning": "FDA deal.",
                "catalyst_type": "FDA_APPROVAL",
            },
            "fundamental_agent": {
                "signal": "BULL",
                "confidence": 0.65,
                "reasoning": "Low float squeeze candidate.",
            },
            "manipulation_classifier": {
                "signal": "NEUTRAL",
                "confidence": 0.55,
                "reasoning": "Organic momentum.",
            },
        }
        s = _make_scenario(actual_signals=multi_signals)

        news_cfg = AgentConfig(agent_type="news", model_id="mixtral")
        fund_cfg = AgentConfig(agent_type="fundamental", model_id="qwen3")
        manip_cfg = AgentConfig(agent_type="manipulation", model_id="qwen3")

        r_news = harness.run_single(s, news_cfg, mode="replay")
        r_fund = harness.run_single(s, fund_cfg, mode="replay")
        r_manip = harness.run_single(s, manip_cfg, mode="replay")

        assert r_news.signal_direction == "BULL"
        assert r_news.catalyst_type == "FDA_APPROVAL"
        assert r_fund.signal_direction == "BULL"
        assert r_manip.signal_direction == "NEUTRAL"

    def test_run_replay_all_agents_in_scenario(self, harness):
        multi_signals = {
            "news_agent": {"signal": "BULL", "confidence": 0.70},
            "technical_agent": {"signal": "NEUTRAL", "confidence": 0.50},
        }
        s = _make_scenario(actual_signals=multi_signals)
        results = harness.run_replay([s])
        assert len(results) == 2
        agent_types = {r.agent_config.agent_type for r in results}
        assert "news_agent" in agent_types
        assert "technical_agent" in agent_types


# ---------------------------------------------------------------------------
# 17. AgentRunResult serialization roundtrip
# ---------------------------------------------------------------------------


class TestRunResultSerialization:
    def test_run_result_serialization(self, harness, scenario, news_config):
        result = harness.run_single(scenario, news_config, mode="replay")
        d = result.to_dict()

        # Verify structure
        assert d["scenario_id"] == "BRLS_2026-02-10"
        assert d["signal_direction"] == "BULL"
        assert d["signal_confidence"] == pytest.approx(0.72)
        assert d["catalyst_type"] == "FDA_APPROVAL"
        assert d["latency_ms"] == pytest.approx(0.0)
        assert d["parse_success"] is True
        assert "agent_config" in d
        assert d["agent_config"]["agent_type"] == "news"

        # Roundtrip
        result2 = AgentRunResult.from_dict(d)
        assert result2.scenario_id == result.scenario_id
        assert result2.signal_direction == result.signal_direction
        assert result2.signal_confidence == pytest.approx(result.signal_confidence)
        assert result2.catalyst_type == result.catalyst_type
        assert result2.parse_success == result.parse_success

    def test_run_result_timestamp_preserved(self, harness, scenario, news_config):
        result = harness.run_single(scenario, news_config, mode="replay")
        assert result.timestamp is not None
        d = result.to_dict()
        result2 = AgentRunResult.from_dict(d)
        assert result2.timestamp is not None

    def test_run_result_error_serialization(self, harness, news_config):
        s = _make_scenario(actual_signals={})
        result = harness.run_single(s, news_config, mode="replay")
        d = result.to_dict()
        assert d["parse_success"] is False
        result2 = AgentRunResult.from_dict(d)
        assert result2.parse_success is False
        assert result2.error is not None
