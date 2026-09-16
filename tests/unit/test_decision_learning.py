"""
D221 Phase F: tests for src/monitoring/decision_learning.

Two layers:
  1. Pure-helper tests (no I/O, no webhook): consensus computation,
     trust-the-gate logic, paper1 footer rendering, cascade warning.
  2. Adversarial sweep on every public function: tolerates None/wrong-type
     inputs without raising. Posts are mocked via webhook URL = "" so
     no network is touched.

The bug-sweep template applies. Per the Sunday-night discipline: every
external-input boundary gets isinstance guards, every numeric coercion
goes through safe-coercion, every kwargs.get() with caller-might-pass-None
gets explicit None handling.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.monitoring import decision_learning as dl


# ── Pure helpers ─────────────────────────────────────────────────────


class TestConsensusStrength:
    def test_empty_returns_none(self):
        assert dl._consensus_strength_from_signals([]) is None

    def test_single_signal_returns_none(self):
        sigs = [{"signal": "BULL"}]
        assert dl._consensus_strength_from_signals(sigs) is None

    def test_perfect_agreement_high_consensus(self):
        sigs = [{"signal": "BULL"}] * 5
        c = dl._consensus_strength_from_signals(sigs)
        assert c == 1.0  # std=0, 1 - 0/2 = 1

    def test_max_disagreement_low_consensus(self):
        sigs = [{"signal": "STRONG_BULL"}, {"signal": "STRONG_BEAR"}]
        c = dl._consensus_strength_from_signals(sigs)
        # std = 2.0, normalized = 2.0/2 = 1, consensus = 0
        assert c == 0.0

    def test_handles_dict_and_object_mix(self):
        class FakeSig:
            def __init__(self, s):
                self.signal = s
        sigs = [{"signal": "BULL"}, FakeSig("BULL")]
        c = dl._consensus_strength_from_signals(sigs)
        assert c == 1.0

    def test_unknown_signal_skipped(self):
        sigs = [{"signal": "BULL"}, {"signal": "ALIEN_SIGNAL"}, {"signal": "BULL"}]
        c = dl._consensus_strength_from_signals(sigs)
        # 2 BULLs -> perfect agreement among recognized
        assert c == 1.0

    def test_none_signals_handled(self):
        # Caller passed None where a list was expected
        assert dl._consensus_strength_from_signals(None) is None  # type: ignore[arg-type]


class TestTrustTheGate:
    @pytest.mark.parametrize("code", [
        "HARD_VETO_DILUTION", "HARD_VETO_BANKRUPTCY", "HARD_VETO_SPREAD",
        "D198_REGIME_HALT", "D150_MAX_POSITIONS", "D150_CIRCUIT_BREAKER",
        "D56_DUPLICATE", "D85_FAST_PATH", "EXECUTED",
    ])
    def test_known_trusted_codes(self, code):
        assert dl.is_trusted_gate(code) is True

    def test_substring_match(self):
        # Real rejection_reason often includes context
        assert dl.is_trusted_gate(
            "HARD_VETO_DILUTION: 424B5 filed 2 days ago"
        ) is True

    def test_lowercase_match(self):
        # Defensive: case-insensitive
        assert dl.is_trusted_gate("hard_veto_dilution") is True

    def test_untrusted_code(self):
        assert dl.is_trusted_gate("D204_NEWS_CONFIDENCE") is False
        assert dl.is_trusted_gate("D200_NO_CATALYST") is False

    def test_none_safe(self):
        assert dl.is_trusted_gate(None) is False

    def test_non_string_safe(self):
        assert dl.is_trusted_gate(12345) is False  # type: ignore[arg-type]
        assert dl.is_trusted_gate({"x": 1}) is False  # type: ignore[arg-type]

    def test_empty_string_untrusted(self):
        assert dl.is_trusted_gate("") is False


class TestCascadeWarning:
    def test_low_consensus_no_warning(self):
        assert dl._format_cascade_warning(0.3) is None

    def test_high_consensus_warning(self):
        w = dl._format_cascade_warning(0.85)
        assert w is not None
        assert "-0.86" in w
        assert "contra-predictive" in w

    def test_threshold_boundary(self):
        # 0.7 exactly = warning fires
        assert dl._format_cascade_warning(0.7) is not None
        assert dl._format_cascade_warning(0.69) is None

    def test_none_no_warning(self):
        assert dl._format_cascade_warning(None) is None


class TestPaper1Footer:
    def test_empty_returns_empty(self):
        assert dl._paper1_footer({}) == ""

    def test_filters_none(self):
        out = dl._paper1_footer({"a": 1.0, "b": None, "c": 2.0})
        assert "a" in out and "c" in out and '"b"' not in out

    def test_filters_nan(self):
        out = dl._paper1_footer({"x": float("nan"), "y": 1.0})
        assert "y" in out and '"x"' not in out

    def test_filters_inf(self):
        out = dl._paper1_footer({"x": float("inf"), "y": 1.0})
        assert "y" in out and '"x"' not in out

    def test_renders_as_json_block(self):
        out = dl._paper1_footer({"k": 1.234567890})
        assert "```json" in out
        # Rounded to 6 digits
        assert "1.234568" in out

    def test_handles_strings_and_bools(self):
        out = dl._paper1_footer({"name": "test", "ok": True, "n": 42})
        assert '"name": "test"' in out
        assert '"ok": true' in out


# ── Module-level config ──────────────────────────────────────────────


class TestModuleConfig:
    def test_disabled_when_url_unset(self, monkeypatch):
        monkeypatch.setattr(dl, "_WEBHOOK_URL", "")
        assert dl.is_enabled() is False

    def test_enabled_when_url_set(self, monkeypatch):
        monkeypatch.setattr(dl, "_WEBHOOK_URL", "https://example/webhook")
        assert dl.is_enabled() is True

    def test_threshold_default(self):
        # _DISAGREE_THRESHOLD initialized at import; verify it's a float in
        # plausible range
        assert 0.0 <= dl.disagree_threshold() <= 1.0


# ── Adversarial sweep on async post functions (no network) ──────────


class TestPostFunctionsNoCrash:
    """Every public post_X must not raise on bad inputs.

    Webhook URL is forced empty so no network IO happens; just exercise
    the formatting/logic paths.
    """

    @pytest.fixture(autouse=True)
    def disable_webhook(self, monkeypatch):
        monkeypatch.setattr(dl, "_WEBHOOK_URL", "")

    @pytest.mark.asyncio
    async def test_trade_open_minimal(self):
        # Minimum required args
        await dl.post_trade_open_enriched(
            ticker="X", qty=100, entry_price=5.0, stop_loss=4.5,
            mfcs=0.5, path="FAST_PATH",
        )

    @pytest.mark.asyncio
    async def test_trade_open_with_none_optionals(self):
        # All optionals = None
        await dl.post_trade_open_enriched(
            ticker="X", qty=100, entry_price=5.0, stop_loss=4.5,
            mfcs=None, path="UNKNOWN",
            agent_signals=None, catalyst_type=None, kelly_tier=None,
            distribution_cache=None,
        )

    @pytest.mark.asyncio
    async def test_trade_open_with_malformed_signals(self):
        bad_signals = [
            {"agent_id": "x"},  # missing signal/confidence
            None,  # whole entry None
            "not a dict",  # wrong type
            {"agent_id": None, "signal": None, "confidence": "not a number"},
        ]
        await dl.post_trade_open_enriched(
            ticker="X", qty=100, entry_price=5.0, stop_loss=4.5,
            mfcs=0.5, path="FAST_PATH",
            agent_signals=bad_signals,
        )

    @pytest.mark.asyncio
    async def test_trade_close_minimal(self):
        await dl.post_trade_close_enriched(
            ticker="X", qty=100, entry_price=5.0, exit_price=5.10,
            pnl=10.0, exit_reason="time_exit", hold_minutes=15.0,
            mfcs_at_entry=0.5,
        )

    @pytest.mark.asyncio
    async def test_trade_close_loss_with_no_attribution(self):
        await dl.post_trade_close_enriched(
            ticker="X", qty=100, entry_price=5.0, exit_price=4.90,
            pnl=-10.0, exit_reason="stop_loss", hold_minutes=2.0,
            mfcs_at_entry=None,
            agent_component_scores=None,
            shapley_attribution=None,
        )

    @pytest.mark.asyncio
    async def test_trade_close_with_malformed_attribution(self):
        await dl.post_trade_close_enriched(
            ticker="X", qty=100, entry_price=5.0, exit_price=5.0,
            pnl=0.0, exit_reason="time_exit", hold_minutes=10.0,
            mfcs_at_entry=0.5,
            shapley_attribution={"cat": None, "tech": float("nan"), "news": 0.05},
        )

    @pytest.mark.asyncio
    async def test_trade_close_zero_qty(self):
        # Edge case: divide-by-zero in pnl_pct
        await dl.post_trade_close_enriched(
            ticker="X", qty=0, entry_price=5.0, exit_price=5.0,
            pnl=0.0, exit_reason="weird", hold_minutes=None,
            mfcs_at_entry=None,
        )

    @pytest.mark.asyncio
    async def test_disagree_below_threshold_returns_false(self):
        result = await dl.post_disagree_shadow_buy(
            ticker="X", composite_score=0.3, production_decision="NO_TRADE",
            rejection_code="D200_NO_CATALYST",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_disagree_trusted_gate_returns_false(self):
        result = await dl.post_disagree_shadow_buy(
            ticker="X", composite_score=0.99, production_decision="NO_TRADE",
            rejection_code="HARD_VETO_DILUTION",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_disagree_above_threshold_attempts_send(self, monkeypatch):
        # Need webhook URL to bypass the is_enabled() short-circuit AND mock
        # the actual post so no network IO happens. With both, the function
        # exercises its full filter logic and returns True.
        async def fake_post(payload):
            pass
        monkeypatch.setattr(dl, "_WEBHOOK_URL", "https://fake")
        monkeypatch.setattr(dl, "_post_async", fake_post)
        result = await dl.post_disagree_shadow_buy(
            ticker="X", composite_score=0.7, production_decision="NO_TRADE",
            rejection_code="D200_NO_CATALYST",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_disagree_with_none_rejection(self, monkeypatch):
        async def fake_post(payload):
            pass
        monkeypatch.setattr(dl, "_WEBHOOK_URL", "https://fake")
        monkeypatch.setattr(dl, "_post_async", fake_post)
        result = await dl.post_disagree_shadow_buy(
            ticker="X", composite_score=0.7, production_decision="NO_TRADE",
            rejection_code=None,  # None isn't a trusted code
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_disagree_returns_false_when_webhook_unset(self):
        # Default fixture state: webhook empty -> immediate False
        result = await dl.post_disagree_shadow_buy(
            ticker="X", composite_score=0.99, production_decision="NO_TRADE",
            rejection_code="D200_NO_CATALYST",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_session_summary_zero_trades(self):
        await dl.post_session_summary_enriched(
            trades_count=0, pnl=0.0, wins=0, losses=0,
        )

    @pytest.mark.asyncio
    async def test_session_summary_positive(self):
        await dl.post_session_summary_enriched(
            trades_count=5, pnl=234.50, wins=3, losses=2,
        )


# ── End-to-end format check on a realistic scenario ──────────────────


class TestEndToEndFormatting:
    """Snapshot-style checks on what the message LOOKS like, mocking the
    actual HTTP call but capturing the payload."""

    @pytest.mark.asyncio
    async def test_trade_open_payload_has_all_sections(self, monkeypatch):
        captured = {}

        async def fake_post(payload):
            captured["payload"] = payload

        monkeypatch.setattr(dl, "_WEBHOOK_URL", "https://fake")
        monkeypatch.setattr(dl, "_post_async", fake_post)

        await dl.post_trade_open_enriched(
            ticker="ACME", qty=200, entry_price=5.50, stop_loss=5.20,
            mfcs=0.62, path="PHASE2_BUY",
            agent_signals=[
                {"agent_id": "news_agent", "signal": "BULL", "confidence": 0.85},
                {"agent_id": "fundamental_agent", "signal": "BULL", "confidence": 0.7},
                {"agent_id": "technical_agent", "signal": "BULL", "confidence": 0.6},
                {"agent_id": "institutional_agent", "signal": "BULL", "confidence": 0.55},
                {"agent_id": "deep_search_agent", "signal": "BULL", "confidence": 0.7},
            ],
            catalyst_type="FDA_APPROVAL", kelly_tier=3,
        )

        assert "payload" in captured
        embed = captured["payload"]["embeds"][0]
        # Basic structure
        assert "ACME" in embed["title"]
        descr = embed["description"]
        assert "MFCS:** 0.620" in descr
        assert "FDA_APPROVAL" in descr
        assert "Kelly tier 3" in descr
        # All 5 agents listed
        for agent in ("news_agent", "fundamental_agent", "technical_agent",
                      "institutional_agent", "deep_search_agent"):
            assert agent in descr
        # Cascade warning fires (5 BULLs = perfect consensus)
        assert "CASCADE CONSENSUS" in descr
        assert "-0.86" in descr  # the publishable claim, surfaced
        # Paper 1 footer present
        assert "```json" in descr

    @pytest.mark.asyncio
    async def test_trade_close_payload_has_shapley(self, monkeypatch):
        captured = {}

        async def fake_post(payload):
            captured["payload"] = payload

        monkeypatch.setattr(dl, "_WEBHOOK_URL", "https://fake")
        monkeypatch.setattr(dl, "_post_async", fake_post)

        await dl.post_trade_close_enriched(
            ticker="ACME", qty=200, entry_price=5.50, exit_price=5.85,
            pnl=70.0, exit_reason="target_hit", hold_minutes=18.5,
            mfcs_at_entry=0.62,
            shapley_attribution={
                "catalyst_news": 0.024,
                "technical": 0.011,
                "volume_rvol": 0.008,
                "institutional": -0.003,
                "risk": 0.0,
            },
            debate_triggered=True, risk_score=0.4,
            catalyst_type="FDA_APPROVAL",
        )

        assert "payload" in captured
        embed = captured["payload"]["embeds"][0]
        assert "WIN" in embed["title"]
        descr = embed["description"]
        assert "Shapley attribution" in descr
        assert "catalyst_news" in descr
        assert "+0.024" in descr or "0.024" in descr  # top contributor
        assert "debate_triggered" in descr
        assert "```json" in descr

    @pytest.mark.asyncio
    async def test_disagree_payload_includes_thesis_link(self, monkeypatch):
        captured = {}

        async def fake_post(payload):
            captured["payload"] = payload

        monkeypatch.setattr(dl, "_WEBHOOK_URL", "https://fake")
        monkeypatch.setattr(dl, "_post_async", fake_post)

        result = await dl.post_disagree_shadow_buy(
            ticker="ACME",
            composite_score=0.72,
            production_decision="NO_TRADE",
            rejection_code="D200_NO_CATALYST",
            mfcs=0.45,
            agent_signals=[
                {"agent_id": "news_agent", "signal": "NEUTRAL"},
                {"agent_id": "fundamental_agent", "signal": "NEUTRAL"},
            ],
        )
        assert result is True
        assert "payload" in captured
        embed = captured["payload"]["embeds"][0]
        assert "DISAGREE" in embed["title"]
        descr = embed["description"]
        assert "0.72" in descr  # composite score
        assert "v2 cascade-anti-selection thesis" in descr
        assert "```json" in descr
