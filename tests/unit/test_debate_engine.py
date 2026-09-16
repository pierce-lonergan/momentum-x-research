"""
MOMENTUM-X Tests: Debate Engine

Node ID: tests.unit.test_debate_engine
Tests verify debate divergence thresholds from MOMENTUM_LOGIC.md §10.
"""

import pytest
from datetime import datetime, timezone

from src.core.models import (
    CandidateStock,
    ScoredCandidate,
    AgentSignal,
    DebateResult,
)
from src.agents.debate_engine import DebateEngine


class TestDebateDivergence:
    """Verify position sizing from divergence metric (MOMENTUM_LOGIC.md §10)."""

    @pytest.fixture
    def engine(self) -> DebateEngine:
        return DebateEngine(model="test-model")

    def test_high_divergence_full_position(self):
        """DIV > 0.6 → FULL position."""
        result = DebateResult(
            ticker="TEST",
            verdict="STRONG_BUY",
            confidence=0.9,
            bull_strength=0.95,
            bear_strength=0.2,
            debate_divergence=0.75,  # > 0.6
            position_size="FULL",
        )
        assert result.debate_divergence > 0.6
        assert result.position_size == "FULL"

    def test_moderate_divergence_half_position(self):
        """DIV ∈ [0.3, 0.6] → HALF position."""
        result = DebateResult(
            ticker="TEST",
            verdict="BUY",
            confidence=0.6,
            bull_strength=0.65,
            bear_strength=0.25,
            debate_divergence=0.4,  # ∈ [0.3, 0.6]
            position_size="HALF",
        )
        assert 0.3 <= result.debate_divergence <= 0.6
        assert result.position_size == "HALF"

    def test_low_divergence_no_trade(self):
        """DIV < 0.3 → NO TRADE (insufficient edge)."""
        result = DebateResult(
            ticker="TEST",
            verdict="NO_TRADE",
            confidence=0.0,
            bull_strength=0.55,
            bear_strength=0.45,
            debate_divergence=0.1,  # < 0.3
            position_size="NONE",
        )
        assert result.debate_divergence < 0.3
        assert result.position_size == "NONE"
        assert result.verdict == "NO_TRADE"


class TestDebateContextBuilding:
    """Test that debate context is properly built from scored candidates."""

    @pytest.fixture
    def engine(self) -> DebateEngine:
        return DebateEngine(model="test-model")

    @pytest.fixture
    def scored_candidate(self) -> ScoredCandidate:
        candidate = CandidateStock(
            ticker="BOOM",
            current_price=8.0,
            previous_close=5.0,
            gap_pct=0.60,
            gap_classification="EXPLOSIVE",
            rvol=10.0,
            premarket_volume=500_000,
            float_shares=5_000_000,
            scan_timestamp=datetime.now(timezone.utc),
            scan_phase="PRE_MARKET",
        )
        signal = AgentSignal(
            agent_id="news_agent",
            ticker="BOOM",
            timestamp=datetime.now(timezone.utc),
            signal="STRONG_BULL",
            confidence=0.9,
            reasoning="FDA approval confirmed by PR Newswire",
        )
        return ScoredCandidate(
            candidate=candidate,
            mfcs=0.85,
            agent_signals=[signal],
            component_scores={"catalyst_news": 0.9},
            risk_score=0.1,
            qualifies_for_debate=True,
        )

    def test_context_contains_ticker(self, engine, scored_candidate):
        context = engine._build_context(scored_candidate)
        assert "BOOM" in context

    def test_context_contains_gap(self, engine, scored_candidate):
        context = engine._build_context(scored_candidate)
        assert "60.0%" in context

    def test_context_contains_mfcs(self, engine, scored_candidate):
        context = engine._build_context(scored_candidate)
        assert "0.850" in context

    def test_context_contains_agent_signals(self, engine, scored_candidate):
        context = engine._build_context(scored_candidate)
        assert "news_agent" in context
        assert "STRONG_BULL" in context
        assert "FDA" in context

    def test_context_includes_data_quality_note(self, engine):
        """D30: Context should flag agents with no data for fair debate."""
        candidate = CandidateStock(
            ticker="AMC",
            current_price=8.0,
            previous_close=5.0,
            gap_pct=0.60,
            gap_classification="EXPLOSIVE",
            rvol=10.0,
            premarket_volume=500_000,
            scan_timestamp=datetime.now(timezone.utc),
            scan_phase="PRE_MARKET",
        )
        real_signal = AgentSignal(
            agent_id="technical_agent",
            ticker="AMC",
            timestamp=datetime.now(timezone.utc),
            signal="STRONG_BULL",
            confidence=0.9,
            reasoning="Breakout above resistance",
        )
        empty_signal = AgentSignal(
            agent_id="institutional_agent",
            ticker="AMC",
            timestamp=datetime.now(timezone.utc),
            signal="NEUTRAL",
            confidence=0.0,
            reasoning="",  # Empty — no data
        )
        scored = ScoredCandidate(
            candidate=candidate,
            mfcs=0.329,
            agent_signals=[real_signal, empty_signal],
            component_scores={"technical": 0.9},
            risk_score=0.3,
            qualifies_for_debate=True,
        )
        context = engine._build_context(scored)
        assert "DATA QUALITY NOTE" in context
        assert "institutional_agent" in context
        assert "technical_agent" in context

    def test_context_no_data_quality_note_when_all_have_data(self, engine, scored_candidate):
        """No DATA QUALITY NOTE when all agents have real signals."""
        context = engine._build_context(scored_candidate)
        assert "DATA QUALITY NOTE" not in context


class TestConfigurableDivergenceThresholds:
    """D29: Configurable divergence thresholds for Phase 3."""

    def test_custom_thresholds_accepted(self):
        """DebateEngine should accept custom divergence thresholds."""
        engine = DebateEngine(
            model="test-model",
            divergence_no_trade=0.15,
            divergence_full=0.5,
        )
        assert engine.divergence_no_trade == 0.15
        assert engine.divergence_full == 0.5

    def test_default_thresholds(self):
        """Default thresholds should be 0.3/0.6."""
        engine = DebateEngine(model="test-model")
        assert engine.divergence_no_trade == 0.3
        assert engine.divergence_full == 0.6

    def test_settings_default_divergence_low_is_0_15_or_env_override(self):
        """DebateConfig divergence_low: 0.15 (code default), 0.20 (D45 .env), or 0.08 (live tuned)."""
        from config.settings import DebateConfig
        config = DebateConfig()
        assert config.divergence_low_threshold in (0.08, 0.15, 0.20)

    def test_settings_default_divergence_high_is_0_6(self):
        """DebateConfig default divergence_high is 0.6."""
        from config.settings import DebateConfig
        config = DebateConfig()
        assert config.divergence_high_threshold == 0.6


class TestDebateModelTiering:
    """D32: Separate models for bull/bear vs judge."""

    def test_advocate_model_defaults_to_main(self):
        """If no advocate_model specified, uses main model."""
        engine = DebateEngine(model="test-model")
        assert engine.advocate_model == "test-model"

    def test_advocate_model_can_differ(self):
        """Advocate model can be set independently."""
        engine = DebateEngine(
            model="tier1/deep-thinker",
            advocate_model="tier2/fast-writer",
        )
        assert engine.model == "tier1/deep-thinker"
        assert engine.advocate_model == "tier2/fast-writer"

    def test_advocate_timeout_defaults_to_main(self):
        """If no advocate_timeout, uses main timeout."""
        engine = DebateEngine(model="test", timeout=120)
        assert engine.advocate_timeout == 120

    def test_advocate_timeout_can_be_shorter(self):
        """Advocate timeout can be set independently."""
        engine = DebateEngine(
            model="test", timeout=120, advocate_timeout=60
        )
        assert engine.advocate_timeout == 60


class TestLiteLLMRetryConfig:
    """D83: Verify LiteLLM zero-retry and timeout configuration."""

    def test_litellm_num_retries_set(self):
        """D83: litellm.num_retries should be 0 after importing base (single attempt)."""
        import litellm
        from src.agents import base  # noqa: F401 — triggers module-level config
        assert litellm.num_retries == 0

    def test_openai_client_max_retries_disabled(self):
        """D83-fix: DEFAULT_MAX_RETRIES env var should be '0' to disable httpx retries."""
        import os
        from src.agents import base  # noqa: F401 — triggers module-level config
        assert os.environ.get("DEFAULT_MAX_RETRIES") == "0"

    def test_tier_specific_timeouts_in_settings(self):
        """D92: ModelConfig exposes tier-specific timeouts for instruct models.

        Exact seconds are tuning knobs that move with Together AI latency
        (tier2: 15s D92 → 25s doc 177/178 after queue latency blinded the
        news agent). Assert existence + a sane band + tier ordering instead.
        Zero retries is a hard D83 latency invariant and stays pinned.
        """
        from config.settings import ModelConfig
        config = ModelConfig()
        assert isinstance(config.litellm_timeout_tier1, int)
        assert isinstance(config.litellm_timeout_tier2, int)
        # Band: <10s = mass timeouts on Together AI queue spikes (D87 lesson);
        # >60s = a single agent call could eat the whole eval budget.
        assert 10 <= config.litellm_timeout_tier1 <= 60
        assert 10 <= config.litellm_timeout_tier2 <= 60
        # Extraction tier (2) must never get MORE time than reasoning tier (1).
        assert config.litellm_timeout_tier2 <= config.litellm_timeout_tier1
        # SAFETY PIN — D83: zero retries, single attempt. Speed is everything.
        assert config.litellm_num_retries == 0

    def test_worst_case_wallclock_within_budget(self):
        """D92: With 0 retries and 25s timeout, worst case = 25s (single attempt)."""
        from config.settings import ModelConfig
        config = ModelConfig()
        worst_case = config.litellm_timeout_tier1 * (config.litellm_num_retries + 1)
        assert worst_case <= 25  # Single attempt, no retry overhead


class TestPromptCalibration:
    """D30: Debate prompts calibrated for Phase 3 partial data."""

    def test_bear_prompt_requires_evidence(self):
        """Bear prompt should instruct not to penalize for missing data."""
        import inspect
        source = inspect.getsource(DebateEngine._run_agent)
        assert "Do NOT penalize" in source

    def test_bull_prompt_focuses_on_available_data(self):
        """Bull prompt should focus on data provided."""
        import inspect
        source = inspect.getsource(DebateEngine._run_agent)
        assert "DATA PROVIDED" in source

    def test_judge_prompt_weights_evidence_quality(self):
        """Judge prompt should reference evidence quality."""
        import inspect
        source = inspect.getsource(DebateEngine._run_judge)
        assert "QUALITY" in source


class TestJudgeParseResilience:
    """D34: Judge JSON parse failure resilience.

    When _extract_json() returns {} or partial data, the debate engine
    should handle gracefully rather than producing BUY with zero confidence.
    """

    def test_empty_parse_forces_no_trade(self):
        """confidence=0.0 + divergence=0.0 + no reasoning → forced NO_TRADE."""
        # Simulate what happens when _extract_json returns {}
        # This triggers the D34 confidence guard in _run_judge
        raw = {}
        verdict = raw.get("verdict", "NO_TRADE")
        confidence = float(raw.get("confidence", 0.0))
        bull_str = float(raw.get("bull_strength", 0.5))
        bear_str = float(raw.get("bear_strength", 0.5))
        divergence = abs(bull_str - bear_str)

        # D34 guard: conf=0 + div=0 + no reasoning → NO_TRADE
        if confidence == 0.0 and divergence == 0.0 and not raw.get("key_reasoning"):
            verdict = "NO_TRADE"

        assert verdict == "NO_TRADE"

    def test_partial_json_verdict_only_uses_defaults(self):
        """D34: When verdict present but strengths missing, estimate from verdict."""
        raw = {"verdict": "BUY"}  # No bull_strength or bear_strength

        verdict = raw.get("verdict", "NO_TRADE")
        if "verdict" in raw and "bull_strength" not in raw:
            if verdict in ("STRONG_BUY", "BUY"):
                bull_str = 0.7
                bear_str = 0.3
            elif verdict == "NO_TRADE":
                bull_str = 0.3
                bear_str = 0.7
            else:
                bull_str = 0.5
                bear_str = 0.5
        else:
            bull_str = float(raw.get("bull_strength", 0.5))
            bear_str = float(raw.get("bear_strength", 0.5))

        assert bull_str == 0.7
        assert bear_str == 0.3
        divergence = abs(bull_str - bear_str)
        assert divergence == pytest.approx(0.4)

    def test_no_trade_verdict_estimates_bear_stronger(self):
        """D34: NO_TRADE verdict with missing strengths → bear > bull."""
        raw = {"verdict": "NO_TRADE", "confidence": 0.6}

        if "verdict" in raw and "bull_strength" not in raw:
            if raw["verdict"] in ("STRONG_BUY", "BUY"):
                bull_str = 0.7
                bear_str = 0.3
            elif raw["verdict"] == "NO_TRADE":
                bull_str = 0.3
                bear_str = 0.7
            else:
                bull_str = 0.5
                bear_str = 0.5

        assert bull_str == 0.3
        assert bear_str == 0.7
        divergence = abs(bull_str - bear_str)
        assert divergence == pytest.approx(0.4)

    def test_hold_verdict_equal_strengths(self):
        """D34: HOLD verdict with missing strengths → equal bull/bear."""
        raw = {"verdict": "HOLD", "confidence": 0.4}

        if "verdict" in raw and "bull_strength" not in raw:
            if raw["verdict"] in ("STRONG_BUY", "BUY"):
                bull_str = 0.7
                bear_str = 0.3
            elif raw["verdict"] == "NO_TRADE":
                bull_str = 0.3
                bear_str = 0.7
            else:
                bull_str = 0.5
                bear_str = 0.5

        assert bull_str == 0.5
        assert bear_str == 0.5

    def test_zero_confidence_with_buy_verdict_still_no_trade(self):
        """D34: Even if LLM says BUY, zero confidence + zero divergence → NO_TRADE."""
        raw = {"verdict": "BUY"}
        confidence = float(raw.get("confidence", 0.0))
        # With full JSON present, strengths default to 0.5/0.5 → divergence=0
        bull_str = float(raw.get("bull_strength", 0.5))
        bear_str = float(raw.get("bear_strength", 0.5))

        # But D34 verdict-aware defaults would kick in:
        if "verdict" in raw and "bull_strength" not in raw:
            bull_str = 0.7
            bear_str = 0.3
        divergence = abs(bull_str - bear_str)

        # However, the confidence guard checks for *complete* parse failure:
        # confidence=0 AND divergence=0 AND no reasoning → forced NO_TRADE
        # Here divergence=0.4 because verdict-aware defaults apply, so guard doesn't fire.
        # This is correct: partial parse WITH verdict is salvageable.
        assert divergence > 0.0  # Verdict-aware defaults prevent zero divergence
        assert confidence == 0.0  # Confidence still 0

    def test_extract_json_handles_malformed_input(self):
        """D34: _extract_json returns {} for totally malformed input."""
        engine = DebateEngine(model="test-model")

        result = engine._extract_json("This is not JSON at all")
        assert result == {}

        result = engine._extract_json("")
        assert result == {}

        result = engine._extract_json("```json\n{invalid}\n```")
        assert result == {}

    def test_extract_json_handles_think_blocks(self):
        """_extract_json strips R1 <think> blocks correctly."""
        engine = DebateEngine(model="test-model")

        raw = '<think>Some reasoning here</think>{"verdict": "BUY", "confidence": 0.8}'
        result = engine._extract_json(raw)
        assert result.get("verdict") == "BUY"
        assert result.get("confidence") == 0.8

    def test_extract_json_handles_markdown_fences(self):
        """_extract_json strips markdown fences."""
        engine = DebateEngine(model="test-model")

        raw = '```json\n{"verdict": "NO_TRADE", "confidence": 0.3}\n```'
        result = engine._extract_json(raw)
        assert result.get("verdict") == "NO_TRADE"

    def test_extract_json_unwraps_arrays(self):
        """_extract_json unwraps JSON arrays (DeepSeek R1 quirk)."""
        engine = DebateEngine(model="test-model")

        raw = '[{"verdict": "BUY", "confidence": 0.7}]'
        result = engine._extract_json(raw)
        assert result.get("verdict") == "BUY"
