"""
Tests for D33: Defensive Literal field validators on signal models.

Node ID: tests.unit.test_model_validators
Validates that _sanitize_literal() and @field_validator decorators correctly
handle LLM output noise (whitespace, casing, invalid values) on all Literal
fields across all Pydantic signal models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import get_args

import pytest

from src.core.models import (
    AgentSignal,
    CatalystSpecificity,
    CatalystType,
    DebateResult,
    NewsSignal,
    PatternType,
    PositionSize,
    RiskSignal,
    RiskVerdict,
    SignalDirection,
    TechnicalSignal,
    TimeHorizon,
    TradeAction,
    TradeVerdict,
    _sanitize_literal,
)


# ── Helper: common kwargs for building signals ──────────────────────

_AGENT_KWARGS = dict(
    agent_id="test_agent",
    ticker="TEST",
    timestamp=datetime.now(timezone.utc),
    signal="NEUTRAL",
    confidence=0.5,
    reasoning="test reasoning",
)

_VERDICT_KWARGS = dict(
    ticker="TEST",
    entry_price=10.0,
    stop_loss=9.0,
    target_prices=[12.0, 14.0],
    position_size_pct=0.025,
    confidence=0.7,
    mfcs=0.5,
)


# ── _sanitize_literal() unit tests ──────────────────────────────────


class TestSanitizeLiteral:
    """Test the core sanitization function directly."""

    OPTS = ("CONFIRMED", "RUMORED", "SPECULATIVE")

    def test_exact_match_passthrough(self):
        assert _sanitize_literal("CONFIRMED", self.OPTS, "SPECULATIVE") == "CONFIRMED"

    def test_whitespace_stripped(self):
        assert _sanitize_literal("  CONFIRMED  ", self.OPTS, "SPECULATIVE") == "CONFIRMED"

    def test_single_space_defaults(self):
        """THE bug: DeepSeek R1 returns ' ' for catalyst_specificity."""
        assert _sanitize_literal(" ", self.OPTS, "SPECULATIVE") == "SPECULATIVE"

    def test_empty_string_defaults(self):
        assert _sanitize_literal("", self.OPTS, "SPECULATIVE") == "SPECULATIVE"

    def test_lowercase_accepted(self):
        assert _sanitize_literal("confirmed", self.OPTS, "SPECULATIVE") == "CONFIRMED"

    def test_mixed_case_accepted(self):
        assert _sanitize_literal("Confirmed", self.OPTS, "SPECULATIVE") == "CONFIRMED"

    def test_partial_prefix_match(self):
        """'CONF' → 'CONFIRMED' via prefix match."""
        assert _sanitize_literal("CONF", self.OPTS, "SPECULATIVE") == "CONFIRMED"

    def test_invalid_value_defaults(self):
        assert _sanitize_literal("BANANA", self.OPTS, "SPECULATIVE") == "SPECULATIVE"

    def test_none_input_defaults(self):
        assert _sanitize_literal(None, self.OPTS, "SPECULATIVE") == "SPECULATIVE"

    def test_integer_input_defaults(self):
        assert _sanitize_literal(42, self.OPTS, "SPECULATIVE") == "SPECULATIVE"

    def test_bool_input_defaults(self):
        """Booleans are not strings — should fall back to default."""
        assert _sanitize_literal(True, self.OPTS, "SPECULATIVE") == "SPECULATIVE"


# ── AgentSignal.signal validator ─────────────────────────────────────


class TestAgentSignalValidator:
    """D33: AgentSignal.signal field validation."""

    # AgentSignal tests need kwargs WITHOUT signal (since we pass it explicitly)
    _BASE = dict(
        agent_id="test_agent",
        ticker="TEST",
        timestamp=datetime.now(timezone.utc),
        confidence=0.5,
        reasoning="test reasoning",
    )

    def test_valid_signal_passthrough(self):
        sig = AgentSignal(**self._BASE, signal="STRONG_BULL")
        assert sig.signal == "STRONG_BULL"

    def test_lowercase_signal(self):
        sig = AgentSignal(**self._BASE, signal="bull")
        assert sig.signal == "BULL"

    def test_whitespace_signal(self):
        sig = AgentSignal(**self._BASE, signal="  BEAR  ")
        assert sig.signal == "BEAR"

    def test_single_space_signal_defaults_neutral(self):
        sig = AgentSignal(**self._BASE, signal=" ")
        assert sig.signal == "NEUTRAL"

    def test_empty_signal_defaults_neutral(self):
        sig = AgentSignal(**self._BASE, signal="")
        assert sig.signal == "NEUTRAL"

    def test_invalid_signal_defaults_neutral(self):
        sig = AgentSignal(**self._BASE, signal="MEGA_BULL")
        assert sig.signal == "NEUTRAL"

    def test_all_valid_signals_accepted(self):
        for direction in get_args(SignalDirection):
            sig = AgentSignal(**self._BASE, signal=direction)
            assert sig.signal == direction


# ── NewsSignal validator tests ───────────────────────────────────────


class TestNewsSignalValidators:
    """D33: NewsSignal catalyst_type and catalyst_specificity validation."""

    def test_catalyst_type_valid(self):
        sig = NewsSignal(**_AGENT_KWARGS, catalyst_type="EARNINGS_BEAT")
        assert sig.catalyst_type == "EARNINGS_BEAT"

    def test_catalyst_type_lowercase(self):
        sig = NewsSignal(**_AGENT_KWARGS, catalyst_type="fda_approval")
        assert sig.catalyst_type == "FDA_APPROVAL"

    def test_catalyst_type_whitespace(self):
        sig = NewsSignal(**_AGENT_KWARGS, catalyst_type="  M_AND_A  ")
        assert sig.catalyst_type == "M_AND_A"

    def test_catalyst_type_invalid_defaults_none(self):
        sig = NewsSignal(**_AGENT_KWARGS, catalyst_type="MOONSHOT")
        assert sig.catalyst_type == "NONE"

    def test_catalyst_specificity_the_bug(self):
        """THE actual bug: DeepSeek R1 returns ' ' for catalyst_specificity."""
        sig = NewsSignal(**_AGENT_KWARGS, catalyst_specificity=" ")
        assert sig.catalyst_specificity == "SPECULATIVE"

    def test_catalyst_specificity_lowercase(self):
        sig = NewsSignal(**_AGENT_KWARGS, catalyst_specificity="confirmed")
        assert sig.catalyst_specificity == "CONFIRMED"

    def test_catalyst_specificity_whitespace(self):
        sig = NewsSignal(**_AGENT_KWARGS, catalyst_specificity="  RUMORED  ")
        assert sig.catalyst_specificity == "RUMORED"

    def test_all_catalyst_types_accepted(self):
        for ct in get_args(CatalystType):
            sig = NewsSignal(**_AGENT_KWARGS, catalyst_type=ct)
            assert sig.catalyst_type == ct

    def test_all_catalyst_specificities_accepted(self):
        for cs in get_args(CatalystSpecificity):
            sig = NewsSignal(**_AGENT_KWARGS, catalyst_specificity=cs)
            assert sig.catalyst_specificity == cs


# ── TechnicalSignal validator tests ──────────────────────────────────


class TestTechnicalSignalValidator:
    """D33: TechnicalSignal.pattern_identified validation."""

    def test_valid_pattern(self):
        sig = TechnicalSignal(**_AGENT_KWARGS, pattern_identified="BULL_FLAG")
        assert sig.pattern_identified == "BULL_FLAG"

    def test_lowercase_pattern(self):
        sig = TechnicalSignal(**_AGENT_KWARGS, pattern_identified="cup_handle")
        assert sig.pattern_identified == "CUP_HANDLE"

    def test_whitespace_pattern(self):
        sig = TechnicalSignal(**_AGENT_KWARGS, pattern_identified="  BB_SQUEEZE  ")
        assert sig.pattern_identified == "BB_SQUEEZE"

    def test_invalid_pattern_defaults_none(self):
        sig = TechnicalSignal(**_AGENT_KWARGS, pattern_identified="HEAD_SHOULDERS")
        assert sig.pattern_identified == "NONE"

    def test_all_patterns_accepted(self):
        for pt in get_args(PatternType):
            sig = TechnicalSignal(**_AGENT_KWARGS, pattern_identified=pt)
            assert sig.pattern_identified == pt


# ── RiskSignal validator tests ───────────────────────────────────────


class TestRiskSignalValidators:
    """D33: RiskSignal.risk_verdict and position_size_recommendation validation."""

    def test_valid_risk_verdict(self):
        sig = RiskSignal(**_AGENT_KWARGS, risk_verdict="APPROVE")
        assert sig.risk_verdict == "APPROVE"

    def test_risk_verdict_lowercase(self):
        sig = RiskSignal(**_AGENT_KWARGS, risk_verdict="veto")
        assert sig.risk_verdict == "VETO"

    def test_risk_verdict_whitespace(self):
        sig = RiskSignal(**_AGENT_KWARGS, risk_verdict="  CAUTION  ")
        assert sig.risk_verdict == "CAUTION"

    def test_risk_verdict_invalid_defaults_caution(self):
        sig = RiskSignal(**_AGENT_KWARGS, risk_verdict="MAYBE")
        assert sig.risk_verdict == "CAUTION"

    def test_position_recommendation_valid(self):
        sig = RiskSignal(
            **_AGENT_KWARGS, position_size_recommendation="FULL",
        )
        assert sig.position_size_recommendation == "FULL"

    def test_position_recommendation_lowercase(self):
        sig = RiskSignal(
            **_AGENT_KWARGS, position_size_recommendation="half",
        )
        assert sig.position_size_recommendation == "HALF"

    def test_position_recommendation_invalid_defaults_none(self):
        sig = RiskSignal(
            **_AGENT_KWARGS, position_size_recommendation="MAXIMUM",
        )
        assert sig.position_size_recommendation == "NONE"

    def test_all_risk_verdicts_accepted(self):
        for rv in get_args(RiskVerdict):
            sig = RiskSignal(**_AGENT_KWARGS, risk_verdict=rv)
            assert sig.risk_verdict == rv

    def test_all_position_sizes_accepted(self):
        for ps in get_args(PositionSize):
            sig = RiskSignal(
                **_AGENT_KWARGS, position_size_recommendation=ps,
            )
            assert sig.position_size_recommendation == ps


# ── DebateResult validator tests ─────────────────────────────────────


class TestDebateResultValidators:
    """D33: DebateResult verdict, position_size, time_horizon validation."""

    _BASE = dict(
        ticker="TEST",
        confidence=0.7,
        bull_strength=0.8,
        bear_strength=0.2,
        debate_divergence=0.6,
    )

    def test_verdict_valid(self):
        r = DebateResult(**self._BASE, verdict="BUY")
        assert r.verdict == "BUY"

    def test_verdict_lowercase(self):
        r = DebateResult(**self._BASE, verdict="strong_buy")
        assert r.verdict == "STRONG_BUY"

    def test_verdict_whitespace(self):
        r = DebateResult(**self._BASE, verdict="  NO_TRADE  ")
        assert r.verdict == "NO_TRADE"

    def test_verdict_invalid_defaults_no_trade(self):
        r = DebateResult(**self._BASE, verdict="SUPER_BUY")
        assert r.verdict == "NO_TRADE"

    def test_position_size_lowercase(self):
        r = DebateResult(**self._BASE, verdict="BUY", position_size="quarter")
        assert r.position_size == "QUARTER"

    def test_time_horizon_lowercase(self):
        r = DebateResult(**self._BASE, verdict="BUY", time_horizon="overnight")
        assert r.time_horizon == "OVERNIGHT"

    def test_time_horizon_invalid_defaults_intraday(self):
        r = DebateResult(**self._BASE, verdict="BUY", time_horizon="WEEKLY")
        assert r.time_horizon == "INTRADAY"


# ── TradeVerdict validator tests ─────────────────────────────────────


class TestTradeVerdictValidators:
    """D33: TradeVerdict.action and time_horizon validation."""

    def test_action_valid(self):
        v = TradeVerdict(**_VERDICT_KWARGS, action="BUY")
        assert v.action == "BUY"

    def test_action_lowercase(self):
        v = TradeVerdict(**_VERDICT_KWARGS, action="hold")
        assert v.action == "HOLD"

    def test_action_whitespace(self):
        v = TradeVerdict(**_VERDICT_KWARGS, action="  STRONG_BUY  ")
        assert v.action == "STRONG_BUY"

    def test_action_invalid_defaults_no_trade(self):
        v = TradeVerdict(**_VERDICT_KWARGS, action="YOLO")
        assert v.action == "NO_TRADE"

    def test_time_horizon_valid(self):
        v = TradeVerdict(**_VERDICT_KWARGS, action="BUY", time_horizon="MULTI_DAY")
        assert v.time_horizon == "MULTI_DAY"

    def test_time_horizon_lowercase(self):
        v = TradeVerdict(**_VERDICT_KWARGS, action="BUY", time_horizon="intraday")
        assert v.time_horizon == "INTRADAY"

    def test_all_actions_accepted(self):
        for a in get_args(TradeAction):
            v = TradeVerdict(**_VERDICT_KWARGS, action=a)
            assert v.action == a

    def test_all_time_horizons_accepted(self):
        for th in get_args(TimeHorizon):
            v = TradeVerdict(**_VERDICT_KWARGS, action="BUY", time_horizon=th)
            assert v.time_horizon == th
