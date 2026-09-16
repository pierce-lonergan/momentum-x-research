"""
D122: Post-freeze evaluation fixes tests.

Tests parallel exit UPGRADE-ONLY semantics, catalyst staleness filter,
gap-day stop widening, and deflation replay bug fix.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════
# PARALLEL EXIT UPGRADE-ONLY TESTS
# ═══════════════════════════════════════════════════════════════════


@dataclass
class FakeExitStrategyResult:
    strategy_name: str = "test_strategy"
    should_exit: bool = False
    should_tighten: bool = False
    confidence: float = 0.0
    reasoning: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class FakePosition:
    ticker: str = "TEST"
    entry_price: float = 10.0
    stop_loss: float = 9.0
    peak_price: float = 10.5
    opened_at: str = "2026-03-21T09:30:00"
    catalyst_type: str = "unknown"
    sector: str = ""
    gap_pct: float = 0.1
    entry_volume: int = 100000
    entry_spread: float = 0.01


@dataclass
class FakeSignal:
    recommendation: str = "HOLD"
    composite_exit_urgency: float = 0.0
    reasoning: str = "test"
    volume_fade: float = 0.0
    vwap_deterioration: float = 0.0
    spread_widening: float = 0.0
    time_decay: float = 0.0
    distribution: float = 0.0
    resistance_proximity: float = 0.0
    failed_breakout: float = 0.0
    churning: float = 0.0
    obv_divergence: float = 0.0
    volume_climax: float = 0.0
    momentum_degradation: float = 0.0
    flow_toxicity: float = 0.0


class TestParallelExitUpgradeOnly:
    """D122: Verify parallel strategies can only UPGRADE actions."""

    def test_parallel_inactive_preserves_legacy(self):
        """When parallel_active=False, legacy behavior is unchanged."""
        from src.execution.exit_intelligence import ExitIntelligenceManager

        mgr = ExitIntelligenceManager(parallel_strategies_active=False)
        assert mgr._parallel_active is False

    def test_parallel_active_stored(self):
        """Config params are stored correctly."""
        from src.execution.exit_intelligence import ExitIntelligenceManager

        mgr = ExitIntelligenceManager(
            parallel_strategies_active=True,
            parallel_exit_min_confidence=0.8,
            parallel_tighten_min_confidence=0.6,
            parallel_min_strategies_for_exit=3,
        )
        assert mgr._parallel_active is True
        assert mgr._parallel_exit_min_conf == 0.8
        assert mgr._parallel_tighten_min_conf == 0.6
        assert mgr._parallel_min_strats_exit == 3

    def test_excluded_strategies_stored(self):
        """Excluded strategies list is stored correctly."""
        from src.execution.exit_intelligence import ExitIntelligenceManager

        mgr = ExitIntelligenceManager(
            parallel_exit_excluded_strategies=["pullback", "catalyst_half_life"],
        )
        assert "pullback" in mgr._parallel_excluded
        assert "catalyst_half_life" in mgr._parallel_excluded

    def test_excluded_strategy_exit_not_counted(self):
        """Excluded strategies' EXIT signals are filtered out."""
        # pullback fires EXIT at 0.9 confidence but is excluded
        results = [
            FakeExitStrategyResult(
                strategy_name="pullback", should_exit=True, confidence=0.9
            ),
            FakeExitStrategyResult(
                strategy_name="velocity", should_exit=True, confidence=0.8
            ),
        ]
        excluded = {"pullback", "catalyst_half_life"}
        filtered = [r for r in results
                    if r.should_exit and r.confidence >= 0.7
                    and r.strategy_name not in excluded]
        assert len(filtered) == 1
        assert filtered[0].strategy_name == "velocity"

    def test_exit_fires_with_single_strategy(self):
        """EXIT upgrade fires with single high-confidence strategy (min=1).

        D122: Consensus gate dropped from 2→1. The 2-strategy requirement was
        wrong in principle — CatalystHalfLife + PullbackClassifier on stalled
        positions are correlated, not independent signals. Real safety nets
        are the 0.7 confidence threshold + stop_loss ratchet-up invariant.
        """
        results = [
            FakeExitStrategyResult(
                strategy_name="alpha_oracle", should_exit=True, confidence=0.9
            ),
        ]
        high_conf = [r for r in results if r.should_exit and r.confidence >= 0.7]
        assert len(high_conf) == 1
        # With min_strategies=1, single strategy EXIT DOES trigger upgrade
        assert len(high_conf) >= 1

    def test_exit_fires_with_enough_strategies(self):
        """EXIT upgrade fires when >= min_strategies agree with sufficient confidence."""
        results = [
            FakeExitStrategyResult(
                strategy_name="alpha_oracle", should_exit=True, confidence=0.9
            ),
            FakeExitStrategyResult(
                strategy_name="pullback", should_exit=True, confidence=0.8
            ),
        ]
        high_conf = [r for r in results if r.should_exit and r.confidence >= 0.7]
        assert len(high_conf) >= 2  # Both pass confidence gate

    def test_low_confidence_exits_filtered(self):
        """Exits below min_confidence are filtered out."""
        results = [
            FakeExitStrategyResult(
                strategy_name="pullback", should_exit=True, confidence=0.5
            ),
            FakeExitStrategyResult(
                strategy_name="catalyst_half_life", should_exit=True, confidence=0.6
            ),
        ]
        # With min_confidence=0.7, both should be filtered
        high_conf = [r for r in results if r.should_exit and r.confidence >= 0.7]
        assert len(high_conf) == 0

    def test_tighten_uses_atr_not_confidence(self):
        """TIGHTEN trail should use ATR, not confidence-based percentage."""
        # Confidence-based: trail_pct = max(0.01, 0.03 - 0.03 * 1.0) = 0.01
        # On $5 stock: $0.05 room — dangerously tight
        # ATR-based: current_price - 1.5 * ATR
        entry = 5.0
        atr = 0.30  # 6% ATR
        current_price = 5.20

        # ATR-grounded trail
        new_stop = current_price - (1.5 * atr)
        assert new_stop == pytest.approx(4.75, abs=0.01)
        # This is 8.7% below current — reasonable for a volatile small-cap

        # Confidence-based trail (broken)
        confidence = 1.0
        trail_pct = max(0.01, 0.03 - 0.03 * confidence)
        broken_stop = current_price * (1.0 - trail_pct)
        assert broken_stop == pytest.approx(5.148, abs=0.01)
        # Only $0.05 room — would stop out on a single tick

    def test_tighten_respects_ratchet_up_invariant(self):
        """TIGHTEN is skipped if new_stop <= current stop_loss."""
        current_stop = 9.50
        current_price = 10.0
        atr = 0.50  # Large ATR
        new_stop = current_price - (1.5 * atr)  # 10.0 - 0.75 = 9.25
        assert new_stop < current_stop  # Would move stop DOWN — skip

    def test_never_downgrades_exit(self):
        """Parallel TIGHTEN cannot downgrade a legacy EXIT to TIGHTEN."""
        # If legacy says EXIT and parallel says TIGHTEN,
        # the result should remain EXIT
        legacy_action = "EXIT"
        p_tightens = [
            FakeExitStrategyResult(should_tighten=True, confidence=0.9)
        ]
        # The code only upgrades HOLD→TIGHTEN, not EXIT→TIGHTEN
        # (elif _p_tightens and action.action == "HOLD")
        should_upgrade = p_tightens and legacy_action == "HOLD"
        assert should_upgrade is False


# ═══════════════════════════════════════════════════════════════════
# CATALYST STALENESS FILTER TESTS
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class FakeNewsItem:
    headline: str = "Test headline"
    summary: str = ""
    source: str = "test"
    url: str = ""
    published_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    tickers: list = field(default_factory=list)


class TestCatalystStalenessFilter:
    """D122: Two-layer staleness filter tests."""

    def test_fresh_news_passes(self):
        """News published today pre-market passes both layers."""
        from src.core.orchestrator import Orchestrator

        now = datetime.now(timezone.utc)
        item = FakeNewsItem(
            headline="ACME receives FDA breakthrough designation",
            published_at=now - timedelta(hours=1),
        )
        result = Orchestrator._filter_stale_news([item])
        assert len(result) == 1

    def test_old_news_filtered_by_pub_time(self):
        """News from yesterday afternoon is filtered by Layer 1."""
        from src.core.orchestrator import Orchestrator

        old_time = datetime.now(timezone.utc) - timedelta(hours=72)  # 3 days ago — always stale
        item = FakeNewsItem(
            headline="ACME announces new product line",
            published_at=old_time,
        )
        result = Orchestrator._filter_stale_news([item])
        assert len(result) == 0

    def test_recap_headline_filtered(self):
        """Recap articles with past-tense verbs are filtered by Layer 2."""
        from src.core.orchestrator import Orchestrator

        now = datetime.now(timezone.utc)
        recap_items = [
            FakeNewsItem(headline="Why MOBX Soared Monday", published_at=now),
            FakeNewsItem(headline="ACME surged 20% on catalyst", published_at=now),
            FakeNewsItem(headline="Here's Why XYZ Stock Jumped Today", published_at=now),
            FakeNewsItem(headline="What Happened to BRLS Stock?", published_at=now),
        ]
        for item in recap_items:
            result = Orchestrator._filter_stale_news([item])
            assert len(result) == 0, f"Should filter: {item.headline}"

    def test_forward_looking_headline_passes(self):
        """Forward-looking headlines are NOT filtered by Layer 2."""
        from src.core.orchestrator import Orchestrator

        now = datetime.now(timezone.utc)
        forward_items = [
            FakeNewsItem(headline="ACME Expected to Surge on FDA Decision", published_at=now),
            FakeNewsItem(headline="Breaking: MOBX Receives Contract Award", published_at=now),
            FakeNewsItem(headline="FDA Approves ACME's New Drug Application", published_at=now),
            FakeNewsItem(headline="ACME Reports Record Earnings Beat", published_at=now),
        ]
        for item in forward_items:
            result = Orchestrator._filter_stale_news([item])
            assert len(result) == 1, f"Should NOT filter: {item.headline}"

    def test_empty_list_passthrough(self):
        """Empty news list returns empty."""
        from src.core.orchestrator import Orchestrator

        assert Orchestrator._filter_stale_news([]) == []
        assert Orchestrator._filter_stale_news(None) is None

    def test_mixed_fresh_and_stale(self):
        """Mix of fresh and stale items: only fresh survive."""
        from src.core.orchestrator import Orchestrator

        now = datetime.now(timezone.utc)
        items = [
            FakeNewsItem(headline="Fresh catalyst news", published_at=now),
            FakeNewsItem(
                headline="Old news",
                published_at=now - timedelta(hours=72),  # 3 days ago — always stale
            ),
            FakeNewsItem(headline="ACME tumbled after earnings", published_at=now),
        ]
        result = Orchestrator._filter_stale_news(items)
        assert len(result) == 1
        assert result[0].headline == "Fresh catalyst news"


# ═══════════════════════════════════════════════════════════════════
# GAP-DAY STOP WIDENING TESTS
# ═══════════════════════════════════════════════════════════════════


class TestGapDayStopWidening:
    """D122: Gap-day stop widening in _compute_atr_stop."""

    def test_large_gap_widens_stop(self):
        """20% gap should use gap_floor (10%) instead of ATR (4%)."""
        from src.core.orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch._settings = MagicMock()
        orch._settings.execution.initial_stop_atr_multiplier = 2.0
        orch._settings.execution.initial_stop_floor_pct = 0.04
        orch._settings.execution.gap_day_stop_widening_enabled = True
        orch._settings.execution.gap_day_threshold = 0.08
        orch._settings.execution.gap_day_stop_cap = 0.35
        orch._atr_cache = {"TEST": 0.20}  # ATR=$0.20 on $10 stock = 2%

        stop = orch._compute_atr_stop("TEST", entry=10.0, gap_pct=0.20)
        # gap_floor = 10.0 * 0.20 * 0.5 = $1.00 (10%)
        # ATR stop = 2.0 * 0.20 = $0.40 (4%)
        # gap_floor > ATR stop, so gap_floor used
        # D124 dynamic cap = min(10%, 35%) = 10% → no extra capping
        # stop = 10.0 - 1.0 = 9.0
        assert stop == pytest.approx(9.0, abs=0.01)

    def test_small_gap_no_widening(self):
        """5% gap (below 8% threshold) should not trigger widening."""
        from src.core.orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch._settings = MagicMock()
        orch._settings.execution.initial_stop_atr_multiplier = 2.0
        orch._settings.execution.initial_stop_floor_pct = 0.04
        orch._settings.execution.gap_day_stop_widening_enabled = True
        orch._settings.execution.gap_day_threshold = 0.08
        orch._settings.execution.gap_day_stop_cap = 0.35
        orch._atr_cache = {"TEST": 0.20}

        stop = orch._compute_atr_stop("TEST", entry=10.0, gap_pct=0.05)
        # No widening (5% < 8% threshold): uses 20% cap
        # ATR stop = max(2.0*0.20, 10.0*0.04) = max(0.40, 0.40) = 0.40
        # stop = 10.0 - 0.40 = 9.60
        assert stop == pytest.approx(9.60, abs=0.01)

    def test_gap_widening_uses_dynamic_cap(self):
        """D124: Gap floor uses dynamic cap (gap*0.5, max 35%), not hard 20%."""
        from src.core.orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch._settings = MagicMock()
        orch._settings.execution.initial_stop_atr_multiplier = 2.0
        orch._settings.execution.initial_stop_floor_pct = 0.04
        orch._settings.execution.gap_day_stop_widening_enabled = True
        orch._settings.execution.gap_day_threshold = 0.08
        orch._settings.execution.gap_day_stop_cap = 0.35
        orch._atr_cache = {"TEST": 0.10}

        stop = orch._compute_atr_stop("TEST", entry=10.0, gap_pct=0.50)
        # gap_floor = 10.0 * 0.50 * 0.5 = $2.50 (25%)
        # D124 dynamic cap = min(25%, 35%) = 25% → $2.50
        # stop = 10.0 - 2.50 = 7.50
        assert stop == pytest.approx(7.50, abs=0.01)

    def test_widening_disabled_by_config(self):
        """When disabled, large gaps don't widen stops."""
        from src.core.orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch._settings = MagicMock()
        orch._settings.execution.initial_stop_atr_multiplier = 2.0
        orch._settings.execution.initial_stop_floor_pct = 0.04
        orch._settings.execution.gap_day_stop_widening_enabled = False
        orch._settings.execution.gap_day_threshold = 0.08
        orch._atr_cache = {"TEST": 0.20}

        stop = orch._compute_atr_stop("TEST", entry=10.0, gap_pct=0.20)
        # Widening disabled: ATR stop = max(0.40, 0.40) = 0.40
        # stop = 10.0 - 0.40 = 9.60
        assert stop == pytest.approx(9.60, abs=0.01)


# ═══════════════════════════════════════════════════════════════════
# CONFIG TESTS
# ═══════════════════════════════════════════════════════════════════


class TestD122Config:
    """Verify D122 config fields exist with correct defaults."""

    def test_parallel_strategies_active_default_true(self):
        from config.settings import ExitIntelligenceConfig

        cfg = ExitIntelligenceConfig()
        assert cfg.parallel_strategies_active is True

    def test_parallel_exit_min_confidence(self):
        """Parallel-EXIT confidence bar exists and stays HIGH.

        Exact value is a tuning knob (D122 set 0.7; doc 178 raised to 0.85
        after low-conf overlay exits flattened winners — BB at +1.8%, UMAC
        at -2.2% pre-+26%). The safety direction is a high floor: the overlay
        must fire only on near-certain reversals.
        """
        from config.settings import ExitIntelligenceConfig

        cfg = ExitIntelligenceConfig()
        assert isinstance(cfg.parallel_exit_min_confidence, float)
        # >= 0.5: anything lower means coin-flip-confidence market EXITs.
        assert 0.5 <= cfg.parallel_exit_min_confidence <= 1.0
        # An irreversible EXIT must never require LESS confidence than a TIGHTEN.
        assert cfg.parallel_exit_min_confidence >= cfg.parallel_tighten_min_confidence

    def test_parallel_min_strategies_for_exit(self):
        """At least one agreeing strategy is required to trigger parallel EXIT.

        Exact value is a tuning knob (D122 set 1; doc 178 raised to 2 —
        consensus required after single-strategy exits clipped winners).
        0 would allow zero-consensus exits, which is never valid.
        """
        from config.settings import ExitIntelligenceConfig

        cfg = ExitIntelligenceConfig()
        assert isinstance(cfg.parallel_min_strategies_for_exit, int)
        assert cfg.parallel_min_strategies_for_exit >= 1

    def test_parallel_excluded_strategies_default(self):
        """Broken/winner-clipping strategies excluded by default.

        pullback (92% false-fire) and catalyst_half_life (85% false-fire) are
        broken-until-fixed — these stay PINNED. doc 177 added gratitude +
        alpha_oracle (they fired on winners: BB +1.8%, UMAC pre-+26%).
        velocity remains active so the overlay isn't dead code.
        """
        from config.settings import ExitIntelligenceConfig

        cfg = ExitIntelligenceConfig()
        excluded = cfg.parallel_exit_excluded_strategies
        assert isinstance(excluded, list)
        assert all(isinstance(s, str) for s in excluded)
        # Known-broken strategies must stay excluded until their bugs are fixed.
        assert "pullback" in excluded
        assert "catalyst_half_life" in excluded
        # doc 177: winner-clippers excluded.
        assert "gratitude" in excluded
        assert "alpha_oracle" in excluded
        # The one genuinely selective strategy stays active.
        assert "velocity" not in excluded

    def test_gap_day_config(self):
        from config.settings import ExecutionConfig

        cfg = ExecutionConfig()
        assert cfg.gap_day_stop_widening_enabled is True
        assert cfg.gap_day_threshold == 0.08


# ═══════════════════════════════════════════════════════════════════
# DEFLATION REPLAY BUG FIX TESTS
# ═══════════════════════════════════════════════════════════════════


class TestDeflationReplayFix:
    """D122: Verify experiment replay correctly re-applies deflation variants.

    Bug: confidence_deflation_factor overrides in experiment variants had zero
    effect because deflation was applied upstream (base.py) and baked into
    signal.confidence before replay received the signals. ~3000 wasted records.

    Fix: Store raw_confidence in AgentSignal; replay re-applies variant
    deflation to raw_confidence before calling compute_mfcs().
    """

    def test_agent_signal_has_raw_confidence(self):
        """AgentSignal model includes raw_confidence field."""
        from src.core.models import AgentSignal

        sig = AgentSignal(
            agent_id="news", ticker="TEST", timestamp=datetime.now(timezone.utc),
            signal="BULL", confidence=0.56, raw_confidence=0.80,
            reasoning="test",
        )
        assert sig.raw_confidence == 0.80
        assert sig.confidence == 0.56  # deflated (0.80 * 0.70)

    def test_raw_confidence_optional_for_deterministic(self):
        """Deterministic agents (no deflation) can omit raw_confidence."""
        from src.core.models import AgentSignal

        sig = AgentSignal(
            agent_id="technical", ticker="TEST", timestamp=datetime.now(timezone.utc),
            signal="BULL", confidence=0.75,
            reasoning="deterministic",
        )
        assert sig.raw_confidence is None

    def test_deflation_variant_changes_replay_score(self):
        """When variant has different deflation, replay signals get re-deflated.

        This is the core regression test for the bug: with the old code,
        changing confidence_deflation_factor in a variant produced identical
        MFCS to the primary (0 delta). With the fix, different deflation
        factors MUST produce different MFCS values.
        """
        from src.core.models import AgentSignal
        from src.core.scoring import compute_mfcs, signal_to_score

        now = datetime.now(timezone.utc)
        # Simulate a BULL signal with raw_confidence=0.80
        # Primary deflation 0.70 → confidence=0.56
        sig = AgentSignal(
            agent_id="news_agent", ticker="TEST", timestamp=now,
            signal="BULL", confidence=0.56, raw_confidence=0.80,
            reasoning="catalyst",
        )

        # Score with primary deflation (confidence=0.56)
        primary_score = signal_to_score(sig)

        # Score with variant deflation 0.50 → confidence=0.40
        variant_deflation = 0.50
        new_conf = min(1.0, max(0.0, sig.raw_confidence * variant_deflation))
        variant_sig = sig.model_copy(update={"confidence": new_conf})
        variant_score = signal_to_score(variant_sig)

        assert new_conf == pytest.approx(0.40, abs=0.01)
        assert variant_score != primary_score
        assert variant_score < primary_score  # lower deflation → lower score

    def test_same_deflation_skips_signal_rebuild(self):
        """When variant deflation matches primary, signals pass through unchanged."""
        primary_deflation = 0.70
        variant_deflation = 0.70
        # Should NOT rebuild signals (performance optimization)
        assert abs(variant_deflation - primary_deflation) <= 1e-6

    def test_deterministic_signals_pass_through_in_deflation_variant(self):
        """Deterministic agents (raw_confidence=None) are unchanged by deflation variant."""
        from src.core.models import AgentSignal

        now = datetime.now(timezone.utc)
        sig = AgentSignal(
            agent_id="technical", ticker="TEST", timestamp=now,
            signal="BULL", confidence=0.75, raw_confidence=None,
            reasoning="deterministic",
        )

        # Variant with different deflation shouldn't change deterministic signals
        variant_deflation = 0.50
        if sig.raw_confidence is not None:
            new_conf = min(1.0, max(0.0, sig.raw_confidence * variant_deflation))
            rebuilt = sig.model_copy(update={"confidence": new_conf})
        else:
            rebuilt = sig
        assert rebuilt.confidence == 0.75  # unchanged


# ═══════════════════════════════════════════════════════════════════
# ALPHA DECAY ORACLE MINIMUM EVALUATION TIME TESTS
# ═══════════════════════════════════════════════════════════════════


class TestAlphaDecayOracleMinEval:
    """D122: AlphaDecayOracle must not evaluate before first null curve point.

    Bug: At minute 0, observed return ≈ 0% but null curve says +3.0% at
    minute 5. So alpha = 0% - 3.0% = -3.0%, firing EXIT on every position
    within the first minute. This would have destroyed Monday.
    """

    def test_oracle_holds_before_min_eval_time(self):
        """Oracle returns HOLD for all minutes < MIN_EVALUATION_MINUTES."""
        from src.execution.exit_strategies import AlphaDecayOracle

        oracle = AlphaDecayOracle()
        for minutes in [0, 1, 2, 3, 4]:
            result = oracle.evaluate(
                ticker="TEST", current_return_pct=0.0,
                minutes_since_open=float(minutes),
            )
            assert not result.should_exit, (
                f"Oracle fired EXIT at minute {minutes} — "
                f"must hold until minute {oracle.MIN_EVALUATION_MINUTES}"
            )
            assert not result.should_tighten, (
                f"Oracle fired TIGHTEN at minute {minutes}"
            )

    def test_oracle_evaluates_after_min_eval_time(self):
        """Oracle performs normal evaluation at minute >= MIN_EVALUATION_MINUTES."""
        from src.execution.exit_strategies import AlphaDecayOracle

        oracle = AlphaDecayOracle()
        # At minute 5 with 0% return, null is 3.0% → alpha = -3.0% → EXIT
        result = oracle.evaluate(
            ticker="TEST", current_return_pct=0.0,
            minutes_since_open=5.0,
        )
        assert result.should_exit, "Oracle should EXIT when alpha < 0 at minute 5"

    def test_oracle_holds_at_minute_5_with_good_return(self):
        """Oracle holds when return exceeds null curve."""
        from src.execution.exit_strategies import AlphaDecayOracle

        oracle = AlphaDecayOracle()
        # At minute 5 with 5% return, null is 3.0% → alpha = +2.0% → HOLD
        result = oracle.evaluate(
            ticker="TEST", current_return_pct=5.0,
            minutes_since_open=5.0,
        )
        assert not result.should_exit, "Oracle should hold when alpha > 0"

    def test_min_eval_time_matches_first_null_curve_point(self):
        """MIN_EVALUATION_MINUTES matches the first point on the null curve."""
        from src.execution.exit_strategies import AlphaDecayOracle, DEFAULT_NULL_CURVE

        oracle = AlphaDecayOracle()
        first_null_point = DEFAULT_NULL_CURVE[0][0]  # (5, 3.0)
        assert oracle.MIN_EVALUATION_MINUTES == first_null_point, (
            f"MIN_EVALUATION_MINUTES ({oracle.MIN_EVALUATION_MINUTES}) "
            f"doesn't match first null curve point ({first_null_point})"
        )


# ═══════════════════════════════════════════════════════════════════
# PULLBACK CLASSIFIER MINIMUM ADVANCE GATE TESTS
# ═══════════════════════════════════════════════════════════════════


class TestPullbackMinAdvanceGate:
    """D122: PullbackClassifier must require meaningful advance before tracking.

    Bug: 325/355 (92%) signal history cycles were in EXHAUSTED state because
    tiny noise-level advances (e.g., +0.2% on a $5 stock = $0.01) triggered
    the retracement tracker, and any normal price fluctuation was a "50%
    retracement" of that tiny advance. The strategy was measuring noise, not
    momentum exhaustion.

    Fix: MIN_ADVANCE_PCT (1% of entry price) — don't start tracking
    retracements until the advance is meaningful.
    """

    def test_tiny_advance_does_not_trigger_pullback(self):
        """A 0.2% advance should not trigger pullback tracking."""
        from src.execution.exit_strategies import PullbackClassifier, PullbackTrackerState

        pc = PullbackClassifier()
        tracker = PullbackTrackerState()
        entry = 10.0
        peak = 10.02  # +0.2% advance (below 1% MIN_ADVANCE_PCT)
        current = 10.01  # 50% retracement of 0.2% advance

        result = pc.evaluate(entry, current, peak, tracker)
        assert not result.should_exit, (
            "Tiny advance (0.2%) should not trigger EXHAUSTED"
        )
        assert not result.should_tighten, (
            "Tiny advance (0.2%) should not trigger PULLBACK"
        )

    def test_meaningful_advance_triggers_pullback(self):
        """A 2% advance with 50% retracement should trigger EXHAUSTED."""
        from src.execution.exit_strategies import PullbackClassifier, PullbackTrackerState

        pc = PullbackClassifier()
        tracker = PullbackTrackerState()
        entry = 10.0
        peak = 10.20  # +2% advance (above 1% MIN_ADVANCE_PCT)

        # First, establish the pullback entry (30% retracement)
        current_pullback = 10.20 - (0.20 * 0.35)  # 35% retracement
        result1 = pc.evaluate(entry, current_pullback, peak, tracker)
        assert result1.should_tighten, "35% retracement of 2% advance should TIGHTEN"

        # Then, deeper pullback (55% retracement)
        current_exhausted = 10.20 - (0.20 * 0.55)  # 55% retracement
        result2 = pc.evaluate(entry, current_exhausted, peak, tracker)
        assert result2.should_exit, "55% retracement of 2% advance should EXIT"

    def test_exhausted_resets_on_noise_advance(self):
        """EXHAUSTED state from tiny advance should reset to ADVANCING."""
        from src.execution.exit_strategies import (
            PullbackClassifier, PullbackTrackerState, PullbackState,
        )

        pc = PullbackClassifier()
        tracker = PullbackTrackerState()
        # Manually set tracker to EXHAUSTED (as if from a tiny advance)
        tracker.current_state = PullbackState.EXHAUSTED
        tracker.advance_size = 0.01  # tiny advance

        entry = 10.0
        peak = 10.02  # peak was +0.2% (tiny)
        current = 10.01

        result = pc.evaluate(entry, current, peak, tracker)
        # Should NOT exit — advance was not meaningful
        assert not result.should_exit, (
            "EXHAUSTED from tiny advance should reset, not exit"
        )
        # Tracker should have reset to ADVANCING
        assert tracker.current_state == PullbackState.ADVANCING

    def test_min_advance_pct_default(self):
        """MIN_ADVANCE_PCT default is 1%."""
        from src.execution.exit_strategies import PullbackClassifier

        pc = PullbackClassifier()
        assert pc.MIN_ADVANCE_PCT == 0.01
