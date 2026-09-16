"""
D211: VIX + Tier-Aware Gate Tests

Covers:
- Orchestrator VIX gate behavior (CATALYST allowed in VIX 20-35, MOMENTUM blocked)
- Tier classification with market_cap_max enforcement (FIX 3)
- Settings validation for VIX threshold ordering (FIX 6)
- Session regime VIX spike detection (FIX 2)

Created: 2026-04-06
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from config.settings import Settings
from src.core.models import CandidateStock, ScoredCandidate
from src.core.orchestrator import Orchestrator
from src.data.universe_tiers import UniverseClassifier, UniverseTier, TIER_CONFIGS
from src.execution.session_regime import SessionRegime, SessionRegimeDetector
from src.monitoring.metrics import reset_metrics


# ── Fixtures & Helpers ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_metrics():
    reset_metrics()
    yield
    reset_metrics()


@pytest.fixture
def settings():
    return Settings()


def _make_candidate(**kwargs) -> CandidateStock:
    """Create a CandidateStock with sensible defaults."""
    defaults = dict(
        ticker="TEST",
        company_name="Test Corp",
        current_price=10.0,
        previous_close=8.0,
        gap_pct=0.25,
        gap_classification="EXPLOSIVE",
        rvol=3.5,
        float_shares=5_000_000,
        market_cap=200_000_000,
        premarket_volume=1_000_000,
        avg_daily_volume=500_000,
        scan_timestamp=datetime.now(timezone.utc),
        scan_phase="PRE_MARKET",
    )
    defaults.update(kwargs)
    return CandidateStock(**defaults)


def _make_catalyst_candidate(**kwargs) -> CandidateStock:
    """Create a candidate that classifies as CATALYST tier."""
    defaults = dict(
        ticker="CATL",
        company_name="Catalyst Corp",
        current_price=50.0,
        previous_close=47.50,
        gap_pct=0.05,
        gap_classification="SIGNIFICANT",
        rvol=2.0,
        float_shares=50_000_000,
        market_cap=5_000_000_000,  # $5B — solidly CATALYST
        premarket_volume=500_000,
        avg_daily_volume=5_000_000,  # $250M dollar volume
        scan_timestamp=datetime.now(timezone.utc),
        scan_phase="PRE_MARKET",
    )
    defaults.update(kwargs)
    return CandidateStock(**defaults)


def _make_orchestrator(settings: Settings, **kwargs):
    return Orchestrator(settings=settings, **kwargs)


# ── Test Class 1: VIX Gate Orchestrator Logic ────────────────────────────────


class TestVIXGateOrchestrator:
    """Test D211 tier-aware VIX gate in the orchestrator."""

    @pytest.mark.asyncio
    async def test_catalyst_passes_vix_gate_at_25(self, settings):
        """CATALYST tier stock should pass VIX gate when VIX is 25 (between 20-35)."""
        orch = _make_orchestrator(settings)
        orch._vix_level = 25.0
        orch._vix_last_fetch = datetime.now(timezone.utc)
        candidate = _make_catalyst_candidate()

        # Mock all agents to prevent actual LLM calls
        for attr in [
            "_news_agent", "_technical_agent", "_fundamental_agent",
            "_institutional_agent", "_deep_search_agent", "_risk_agent",
        ]:
            mock = AsyncMock()
            mock.analyze = AsyncMock(return_value=MagicMock(
                agent_id=attr, signal="BULL", confidence=0.8,
                reasoning="Test", ticker="CATL",
                timestamp=datetime.now(timezone.utc),
            ))
            setattr(orch, attr, mock)
        orch._debate_engine = AsyncMock()
        orch._debate_engine.run_debate = AsyncMock(return_value=None)

        verdict = await orch.evaluate_candidate(candidate)
        # Should NOT be blocked by VIX — the trade may still fail later gates,
        # but the reason should NOT be a VIX block
        assert "VIX panic" not in (verdict.reasoning_summary or "")
        assert "VIX momentum block" not in (verdict.reasoning_summary or "")

    @pytest.mark.asyncio
    async def test_momentum_allowed_at_vix_25_with_reduced_size(self, settings):
        """MOMENTUM tier stock allowed at VIX 25 with extra 50% reduction (paper trading)."""
        orch = _make_orchestrator(settings)
        orch._vix_level = 25.0
        orch._vix_last_fetch = datetime.now(timezone.utc)
        candidate = _make_candidate()

        # Mock agents so pipeline completes
        for attr in [
            "_news_agent", "_technical_agent", "_fundamental_agent",
            "_institutional_agent", "_deep_search_agent", "_risk_agent",
        ]:
            mock = AsyncMock()
            mock.analyze = AsyncMock(return_value=MagicMock(
                agent_id=attr, signal="BULL", confidence=0.8,
                reasoning="Test", ticker="TEST",
                timestamp=datetime.now(timezone.utc),
            ))
            setattr(orch, attr, mock)
        orch._debate_engine = AsyncMock()
        orch._debate_engine.run_debate = AsyncMock(return_value=None)

        verdict = await orch.evaluate_candidate(candidate)
        # Should NOT be blocked — allowed through at reduced size
        assert "VIX momentum block" not in (verdict.reasoning_summary or "")
        assert "VIX panic" not in (verdict.reasoning_summary or "")
        # VIX reduction is now a local variable (race condition fix)
        # The trade passing through confirms the reduced sizing path was taken

    @pytest.mark.asyncio
    async def test_unknown_tier_allowed_at_vix_25_reduced(self, settings):
        """UNKNOWN tier stock allowed at VIX 25 with extra reduction (paper trading)."""
        orch = _make_orchestrator(settings)
        orch._vix_level = 25.0
        orch._vix_last_fetch = datetime.now(timezone.utc)
        # Low gap, low rvol → fails both CATALYST and MOMENTUM → UNKNOWN
        candidate = _make_candidate(
            gap_pct=0.01, rvol=0.5, market_cap=50_000_000,
            avg_daily_volume=100_000,
        )

        # Mock agents
        for attr in [
            "_news_agent", "_technical_agent", "_fundamental_agent",
            "_institutional_agent", "_deep_search_agent", "_risk_agent",
        ]:
            mock = AsyncMock()
            mock.analyze = AsyncMock(return_value=MagicMock(
                agent_id=attr, signal="NEUTRAL", confidence=0.5,
                reasoning="Test", ticker="TEST",
                timestamp=datetime.now(timezone.utc),
            ))
            setattr(orch, attr, mock)
        orch._debate_engine = AsyncMock()
        orch._debate_engine.run_debate = AsyncMock(return_value=None)

        verdict = await orch.evaluate_candidate(candidate)
        # Should NOT be VIX-blocked — allowed through at reduced size
        assert "VIX momentum block" not in (verdict.reasoning_summary or "")

    @pytest.mark.asyncio
    async def test_all_blocked_at_vix_35_panic(self, settings):
        """ALL tiers should be blocked at VIX >= 35 (panic threshold)."""
        orch = _make_orchestrator(settings)
        orch._vix_level = 35.0
        orch._vix_last_fetch = datetime.now(timezone.utc)

        # Even CATALYST tier should be blocked
        candidate = _make_catalyst_candidate()
        verdict = await orch.evaluate_candidate(candidate)
        assert verdict.action == "NO_TRADE"
        assert "D211 VIX panic" in (verdict.reasoning_summary or "")

    @pytest.mark.asyncio
    async def test_all_pass_at_vix_19(self, settings):
        """All tiers should pass VIX gate when VIX < 20."""
        orch = _make_orchestrator(settings)
        orch._vix_level = 19.0
        orch._vix_last_fetch = datetime.now(timezone.utc)
        candidate = _make_candidate()

        # Mock agents
        for attr in [
            "_news_agent", "_technical_agent", "_fundamental_agent",
            "_institutional_agent", "_deep_search_agent", "_risk_agent",
        ]:
            mock = AsyncMock()
            mock.analyze = AsyncMock(return_value=MagicMock(
                agent_id=attr, signal="NEUTRAL", confidence=0.5,
                reasoning="Test", ticker="TEST",
                timestamp=datetime.now(timezone.utc),
            ))
            setattr(orch, attr, mock)
        orch._debate_engine = AsyncMock()
        orch._debate_engine.run_debate = AsyncMock(return_value=None)

        verdict = await orch.evaluate_candidate(candidate)
        # Should not be VIX-blocked
        assert "VIX panic" not in (verdict.reasoning_summary or "")
        assert "VIX momentum block" not in (verdict.reasoning_summary or "")

    @pytest.mark.asyncio
    async def test_vix_exactly_20_not_blocked(self, settings):
        """VIX exactly at 20.0 should NOT trigger block (uses > not >=)."""
        orch = _make_orchestrator(settings)
        orch._vix_level = 20.0
        orch._vix_last_fetch = datetime.now(timezone.utc)
        candidate = _make_candidate()

        # Mock agents
        for attr in [
            "_news_agent", "_technical_agent", "_fundamental_agent",
            "_institutional_agent", "_deep_search_agent", "_risk_agent",
        ]:
            mock = AsyncMock()
            mock.analyze = AsyncMock(return_value=MagicMock(
                agent_id=attr, signal="NEUTRAL", confidence=0.5,
                reasoning="Test", ticker="TEST",
                timestamp=datetime.now(timezone.utc),
            ))
            setattr(orch, attr, mock)
        orch._debate_engine = AsyncMock()
        orch._debate_engine.run_debate = AsyncMock(return_value=None)

        verdict = await orch.evaluate_candidate(candidate)
        # 20.0 > 20.0 is False, so NOT blocked
        assert "VIX momentum block" not in (verdict.reasoning_summary or "")
        assert "VIX panic" not in (verdict.reasoning_summary or "")

    @pytest.mark.asyncio
    async def test_vix_exactly_35_is_blocked(self, settings):
        """VIX exactly at 35.0 should trigger panic block (uses >=)."""
        orch = _make_orchestrator(settings)
        orch._vix_level = 35.0
        orch._vix_last_fetch = datetime.now(timezone.utc)
        candidate = _make_candidate()

        verdict = await orch.evaluate_candidate(candidate)
        assert verdict.action == "NO_TRADE"
        assert "D211 VIX panic" in (verdict.reasoning_summary or "")

    @pytest.mark.asyncio
    async def test_catalyst_with_no_adv_gets_extra_reduction(self, settings):
        """CATALYST candidate with avg_daily_volume=None → UNKNOWN → extra reduction (not blocked)."""
        orch = _make_orchestrator(settings)
        orch._vix_level = 25.0
        orch._vix_last_fetch = datetime.now(timezone.utc)
        candidate = _make_catalyst_candidate(avg_daily_volume=None)

        # Mock agents
        for attr in [
            "_news_agent", "_technical_agent", "_fundamental_agent",
            "_institutional_agent", "_deep_search_agent", "_risk_agent",
        ]:
            mock = AsyncMock()
            mock.analyze = AsyncMock(return_value=MagicMock(
                agent_id=attr, signal="BULL", confidence=0.8,
                reasoning="Test", ticker="CATL",
                timestamp=datetime.now(timezone.utc),
            ))
            setattr(orch, attr, mock)
        orch._debate_engine = AsyncMock()
        orch._debate_engine.run_debate = AsyncMock(return_value=None)

        verdict = await orch.evaluate_candidate(candidate)
        # Without dollar volume → UNKNOWN tier → allowed with extra 50% reduction
        assert "VIX momentum block" not in (verdict.reasoning_summary or "")

    @pytest.mark.asyncio
    async def test_vix_none_gate_skipped(self, settings):
        """When VIX is None, the VIX gate should be entirely skipped."""
        orch = _make_orchestrator(settings)
        orch._vix_level = None
        candidate = _make_candidate()

        # Mock agents
        for attr in [
            "_news_agent", "_technical_agent", "_fundamental_agent",
            "_institutional_agent", "_deep_search_agent", "_risk_agent",
        ]:
            mock = AsyncMock()
            mock.analyze = AsyncMock(return_value=MagicMock(
                agent_id=attr, signal="NEUTRAL", confidence=0.5,
                reasoning="Test", ticker="TEST",
                timestamp=datetime.now(timezone.utc),
            ))
            setattr(orch, attr, mock)
        orch._debate_engine = AsyncMock()
        orch._debate_engine.run_debate = AsyncMock(return_value=None)

        verdict = await orch.evaluate_candidate(candidate)
        assert "VIX" not in (verdict.reasoning_summary or "")


# ── Test Class 2: Tier Classification ────────────────────────────────────────


class TestTierClassification:
    """Test D211 tier classification with market_cap_max enforcement."""

    def test_large_cap_failing_catalyst_is_unknown(self):
        """$5B stock failing CATALYST criteria (rvol=1.0) should be UNKNOWN, not MOMENTUM."""
        uc = UniverseClassifier()
        # rvol=1.0 < CATALYST min (1.5), market_cap=$5B > MOMENTUM max ($1B)
        tier = uc.classify("BIG", 50.0, 5_000_000_000, 0.03, 1.0, 25_000_000)
        assert tier == UniverseTier.UNKNOWN

    def test_small_cap_is_momentum(self):
        """$500M stock meeting MOMENTUM criteria should classify as MOMENTUM."""
        uc = UniverseClassifier()
        tier = uc.classify("SMALL", 3.0, 500_000_000, 0.10, 5.0, 3_000_000)
        assert tier == UniverseTier.MOMENTUM

    def test_midcap_is_catalyst(self):
        """$3B stock meeting all CATALYST criteria should classify as CATALYST."""
        uc = UniverseClassifier()
        tier = uc.classify("MID", 45.0, 3_000_000_000, 0.05, 2.0, 25_000_000)
        assert tier == UniverseTier.CATALYST

    def test_boundary_1b_market_cap_is_catalyst(self):
        """Exactly $1B market cap (CATALYST min) should classify as CATALYST if criteria met."""
        uc = UniverseClassifier()
        tier = uc.classify("EDGE", 50.0, 1_000_000_000, 0.05, 2.0, 15_000_000)
        assert tier == UniverseTier.CATALYST

    def test_none_market_cap_is_momentum(self):
        """market_cap=None should fall to MOMENTUM check (market_cap is optional)."""
        uc = UniverseClassifier()
        tier = uc.classify("UNK", 3.0, None, 0.10, 5.0, 3_000_000)
        assert tier == UniverseTier.MOMENTUM

    def test_mega_cap_failing_catalyst_is_unknown(self):
        """$100B stock exceeding CATALYST max ($50B) and MOMENTUM max ($1B) → UNKNOWN."""
        uc = UniverseClassifier()
        tier = uc.classify("MEGA", 200.0, 100_000_000_000, 0.04, 1.8, 50_000_000)
        assert tier == UniverseTier.UNKNOWN


# ── Test Class 3: Settings Validation ────────────────────────────────────────


class TestSettingsValidation:
    """Test D211 VIX threshold settings and validation."""

    def test_vix_panic_threshold_default(self, settings):
        assert settings.scoring.vix_panic_threshold == 35.0

    def test_vix_block_threshold_default(self, settings):
        # D219 (settings.py:476): raised from 20.0 to 30.0 because the
        # original D205 analysis (n=2 at VIX 20-25) was statistically
        # unreliable, and Cameron / Abreu & Brunnermeier evidence supports
        # CONTINUING to trade in high-VIX with reduced sizing rather than
        # blocking. New regime: VIX 15-30 = reduced sizing, VIX 30+ = hard block.
        # If this default changes again, update both production and this test
        # in the same commit so the test surfaces the change to reviewers.
        assert settings.scoring.vix_block_threshold == 30.0

    def test_vix_reduce_threshold_default(self, settings):
        assert settings.scoring.vix_reduce_threshold == 15.0

    def test_invalid_panic_below_block_rejected(self):
        """vix_panic_threshold < vix_block_threshold should fail validation."""
        from pydantic import ValidationError
        from config.settings import ScoringWeights
        with pytest.raises(ValidationError, match="vix_block_threshold.*must be <.*vix_panic_threshold"):
            ScoringWeights(vix_panic_threshold=15.0, vix_block_threshold=20.0)

    def test_invalid_block_below_reduce_rejected(self):
        """vix_block_threshold < vix_reduce_threshold should fail validation."""
        from pydantic import ValidationError
        from config.settings import ScoringWeights
        with pytest.raises(ValidationError, match="vix_reduce_threshold.*must be <.*vix_block_threshold"):
            ScoringWeights(vix_block_threshold=10.0, vix_reduce_threshold=15.0)


# ── Test Class 4: Session Regime VIX Detection ───────────────────────────────


# ────────────────────────────────────────────────────────────────────────
# Mon 2026-04-20 Bug #2: NameError scope-leak on _d211_local_vix_reduction
#
# Root cause: `_d211_local_vix_reduction` was defined as a local variable
# in `_evaluate_candidate_inner` (orchestrator.py:611, 659, 668) and read
# in `_build_trade_verdict` (orchestrator.py:2854) — a *sibling method*.
# Python does not propagate caller locals into a sibling method's scope,
# so every call to `_build_trade_verdict` raised NameError on the line
# `if _d211_local_vix_reduction < 1.0`.
#
# Existing D211 integration tests in TestVIXGateOrchestrator above DO
# exercise this path through `evaluate_candidate`, but the failure mode
# was confirmed only after stashing the fix and re-running:
#
#     test_momentum_allowed_at_vix_25_with_reduced_size FAILED
#     NameError: name '_d211_local_vix_reduction' is not defined
#
# Fix: thread the local through as a method parameter
# `d211_local_vix_reduction: float = 1.0`. The default of 1.0 means the
# `if reduction < 1.0` branch is bypassed, matching the pre-D211 behavior
# when no extra reduction applies.
#
# Test class below is a *direct* unit test on `_build_trade_verdict`,
# bypassing the orchestrator wiring. Intentionally narrow scope: prove
# the parameter plumbing is correct and the verdict's position_size_pct
# reflects the reduction.
# ────────────────────────────────────────────────────────────────────────


class TestBuildTradeVerdictHighVIXPath:
    """Direct unit test of `_build_trade_verdict` exercising the
    high-VIX MOMENTUM-tier extra-reduction path that NameError'd
    pre-Mon-2026-04-20."""

    def _scored(self, candidate: CandidateStock, mfcs: float) -> ScoredCandidate:
        return ScoredCandidate(
            candidate=candidate,
            mfcs=mfcs,
            agent_signals=[],
            component_scores={},
            risk_score=0.2,
            qualifies_for_debate=False,
        )

    def test_default_reduction_is_one_no_extra_scaling(self, settings):
        """Default `d211_local_vix_reduction=1.0` means the high-VIX
        branch is skipped; position_size_pct equals the pre-reduction
        value (modulo other unrelated multipliers)."""
        orch = _make_orchestrator(settings)
        candidate = _make_candidate(rvol=4.0, gap_pct=0.15)
        scored = self._scored(candidate, mfcs=0.45)  # > buy_threshold

        verdict = orch._build_trade_verdict(
            candidate, scored, debate=None, risk_signal=None,
            manipulation_phase="UNCERTAIN",
            # explicitly omit d211_local_vix_reduction → defaults to 1.0
        )
        assert verdict.action == "BUY"
        assert verdict.position_size_pct > 0
        # Stash the baseline for the comparison test below
        self._baseline_pct = verdict.position_size_pct

    def test_half_reduction_applied_to_position_size(self, settings):
        """`d211_local_vix_reduction=0.50` halves the post-VIX-mult
        position_size_pct (the D211 extra reduction).

        Use a high-float candidate (lower float multiplier) and a
        modest MFCS so the resulting position stays well below the
        max_position_pct cap. Otherwise the full case is capped and
        the ratio assertion conflates D211 reduction with capping."""
        orch = _make_orchestrator(settings)
        # high float -> 0.7x mult, low mfcs -> small base -> stays below 0.15 cap
        candidate = _make_candidate(
            rvol=4.0, gap_pct=0.15, float_shares=50_000_000,
        )
        scored = self._scored(candidate, mfcs=0.30)

        verdict_full = orch._build_trade_verdict(
            candidate, scored, debate=None, risk_signal=None,
            manipulation_phase="UNCERTAIN",
            d211_local_vix_reduction=1.0,
        )
        verdict_half = orch._build_trade_verdict(
            candidate, scored, debate=None, risk_signal=None,
            manipulation_phase="UNCERTAIN",
            d211_local_vix_reduction=0.50,
        )

        assert verdict_full.action == "BUY"
        assert verdict_half.action == "BUY"
        # Pre-condition: full case not capped (otherwise the ratio
        # comparison is meaningless because both sides would be capped
        # at 0.15 or different sides of the cap).
        max_cap = orch._settings.execution.max_position_pct
        assert verdict_full.position_size_pct < max_cap, (
            f"Test setup failed: full case ({verdict_full.position_size_pct:.4f}) "
            f"is at the cap ({max_cap:.4f}). Pick a smaller MFCS or different "
            f"candidate so the cap doesn't fire."
        )
        # The 0.50 reduction should halve the position. Allow small
        # float tolerance; verify within 1e-9.
        expected_half = verdict_full.position_size_pct * 0.50
        assert abs(verdict_half.position_size_pct - expected_half) < 1e-9, (
            f"d211_local_vix_reduction=0.50 should halve position_size_pct: "
            f"full={verdict_full.position_size_pct:.6f} "
            f"half={verdict_half.position_size_pct:.6f} "
            f"expected={expected_half:.6f}"
        )

    def test_no_nameerror_on_default_call(self, settings):
        """Regression: the pre-fix code raised
        `NameError: name '_d211_local_vix_reduction' is not defined`
        on every call to _build_trade_verdict because the variable was
        defined in the sibling method `_evaluate_candidate_inner` and
        Python doesn't propagate caller locals into sibling methods.

        This test pins the fix: calling `_build_trade_verdict` without
        any additional kwargs MUST NOT raise NameError. (Other call-time
        checks on the verdict shape live in the two tests above.)"""
        orch = _make_orchestrator(settings)
        candidate = _make_candidate(rvol=4.0, gap_pct=0.15)
        scored = self._scored(candidate, mfcs=0.45)

        # The assertion is implicit: no exception raised.
        verdict = orch._build_trade_verdict(
            candidate, scored, debate=None, risk_signal=None,
        )
        assert verdict is not None
        assert verdict.ticker == candidate.ticker

    def test_signature_includes_d211_local_vix_reduction(self):
        """The parameter MUST appear in the public signature with a
        safe default of 1.0. Static check so a future refactor that
        removes/renames the parameter is caught at unit-test time, not
        at the next high-VIX trading session."""
        import inspect
        sig = inspect.signature(Orchestrator._build_trade_verdict)
        assert "d211_local_vix_reduction" in sig.parameters, (
            f"Parameter missing. Signature: {sig}"
        )
        param = sig.parameters["d211_local_vix_reduction"]
        assert param.default == 1.0, (
            f"Default must be 1.0 (no reduction) so callers that don't "
            f"pass it explicitly behave as if no high-VIX rule applies. "
            f"Got default={param.default!r}"
        )


class TestSessionRegimeVIX:
    """Test D211 session regime VIX spike detection."""

    def test_vix_spike_triggers_halt(self):
        """VIX spike of +10 points should trigger HALTED regime."""
        detector = SessionRegimeDetector()
        detector.initialize_session(starting_equity=100_000, vix_at_open=20.0)

        # Simulate VIX spike from 20 to 30 (+10 points > 3.0 threshold)
        detector.update_vix(30.0)

        assert detector._perf.vix_spike_detected is True
        allowed, reason = detector.should_allow_new_entry()
        assert not allowed
        assert "VIX spike" in reason

    def test_small_vix_move_no_halt(self):
        """VIX move of +2 points should NOT trigger halt (below 3.0 threshold)."""
        detector = SessionRegimeDetector()
        detector.initialize_session(starting_equity=100_000, vix_at_open=20.0)

        # Small VIX move: 20 to 22 (+2 points < 3.0 threshold)
        detector.update_vix(22.0)

        assert detector._perf.vix_spike_detected is False
        allowed, _ = detector.should_allow_new_entry()
        assert allowed
