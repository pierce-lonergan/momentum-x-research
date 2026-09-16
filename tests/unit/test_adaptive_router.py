"""
D112: Tests for Adaptive Compute Router — three-tier evaluation depth routing.

Tests verify:
1. Tier 1 INSTANT_REJECT for all structural violation types
2. Tier 2 DETERMINISTIC_ONLY for high-confidence deterministic cases
3. Tier 3 FULL_PIPELINE for genuinely ambiguous candidates
4. Pump pattern detection with catalyst override
5. Edge cases (missing data, disabled router)
6. Session stats and logging
"""

import pytest
from datetime import datetime, timezone

from config.settings import RouterConfig
from src.core.adaptive_router import (
    AdaptiveComputeRouter,
    EvalTier,
    RouterDecision,
)
from src.core.models import CandidateStock


# ─── Fixtures ───────────────────────────────────────────────────────────

def _make_candidate(**overrides) -> CandidateStock:
    """Build a CandidateStock with sensible defaults, overriding as needed."""
    defaults = dict(
        ticker="TEST",
        company_name="Test Corp",
        current_price=8.50,
        previous_close=7.00,
        gap_pct=0.10,  # 10% gap
        gap_classification="SIGNIFICANT",
        rvol=5.0,
        premarket_volume=500_000,
        float_shares=5_000_000,
        market_cap=42_500_000.0,
        scan_timestamp=datetime.now(timezone.utc),
        scan_phase="MARKET_OPEN",
    )
    defaults.update(overrides)
    return CandidateStock(**defaults)


def _make_router(**config_overrides) -> AdaptiveComputeRouter:
    """Build a router with default config, overriding as needed."""
    config = RouterConfig(**config_overrides)
    return AdaptiveComputeRouter(config=config, max_entry_spread_pct=0.01)


# ─── Tier 1: INSTANT_REJECT Tests ──────────────────────────────────────

class TestTier1InstantReject:
    """Structural violations that should be instantly rejected."""

    def test_rvol_below_minimum(self):
        # D162: instant_reject_min_rvol lowered 1.5→1.0; use 0.8 to stay below new floor
        router = _make_router()
        candidate = _make_candidate(rvol=0.8)
        decision = router.classify(candidate)
        assert decision.tier == EvalTier.INSTANT_REJECT
        assert "RVOL" in decision.reason

    def test_price_below_floor(self):
        router = _make_router()
        candidate = _make_candidate(current_price=0.25)
        decision = router.classify(candidate)
        assert decision.tier == EvalTier.INSTANT_REJECT
        assert "price" in decision.reason

    def test_float_too_large(self):
        # D220: cap raised from 200M to 2B. 3B is above the new cap.
        # When deterministic_mfcs is None, the escape hatch does not apply.
        # D272 (2026-05-12 test fix): the prior version used float_shares=3B,
        # which triggers D272 FLOAT_IMPLAUSIBLE drop (implied float ~5M from
        # market_cap/price → ratio 600x > 100x). After the drop, the
        # float_too_large check no longer fires. Use 2.5B float WITH a
        # market_cap/price that supports plausibility check (implied
        # float must be within 100x of stated to avoid the drop).
        # Test fix: bump market_cap so implied float ~ stated float.
        router = _make_router()
        # market_cap=$5B, current_price=$2 → implied=2.5B matches stated 2.5B
        candidate = _make_candidate(
            float_shares=2_500_000_000,
            market_cap=5_000_000_000.0,
            current_price=2.0,
        )
        decision = router.classify(candidate)
        assert decision.tier == EvalTier.INSTANT_REJECT
        assert "float" in decision.reason

    def test_gap_too_small(self):
        router = _make_router()
        candidate = _make_candidate(gap_pct=0.02)
        decision = router.classify(candidate)
        assert decision.tier == EvalTier.INSTANT_REJECT
        assert "gap" in decision.reason

    def test_same_day_424b5_filing(self):
        router = _make_router()
        candidate = _make_candidate()
        sec_filings = {"has_424b5_same_day": True}
        decision = router.classify(candidate, sec_filings=sec_filings)
        assert decision.tier == EvalTier.INSTANT_REJECT
        assert "424B5" in decision.reason

    def test_corporate_action_flag(self):
        router = _make_router()
        candidate = _make_candidate(corporate_action_flag="REVERSE_SPLIT")
        decision = router.classify(candidate)
        assert decision.tier == EvalTier.INSTANT_REJECT
        assert "corporate action" in decision.reason
        assert "REVERSE_SPLIT" in decision.reason


# ─── Tier 1: Pump Pattern Detection ────────────────────────────────────

class TestPumpPatternDetection:
    """Sub-$3 stocks gapping >30% — pump pattern handling."""

    def test_pump_pattern_no_catalyst_full_pipeline(self):
        """D116: Gap>30% + price<$3 + no catalyst → FULL_PIPELINE (agents evaluate)."""
        router = _make_router()
        candidate = _make_candidate(current_price=2.50, gap_pct=0.35)
        decision = router.classify(candidate, news_items=[])
        assert decision.tier == EvalTier.FULL_PIPELINE
        assert "pump pattern" in decision.reason

    def test_pump_pattern_with_strong_catalyst_full_pipeline(self):
        """Gap>30% + price<$3 + FDA catalyst → FULL_PIPELINE with catalyst note."""
        router = _make_router()
        candidate = _make_candidate(current_price=2.50, gap_pct=0.35)
        news = [{"headline": "FDA Approval Granted for New Drug", "summary": ""}]
        decision = router.classify(candidate, news_items=news)
        assert decision.tier == EvalTier.FULL_PIPELINE
        assert "strong catalyst" in decision.reason

    def test_pump_pattern_catalyst_override_disabled(self):
        """D116: Even with override disabled, pump → FULL_PIPELINE (no blanket reject)."""
        router = _make_router(pump_catalyst_override=False)
        candidate = _make_candidate(current_price=2.50, gap_pct=0.35)
        news = [{"headline": "FDA Approval Granted", "summary": ""}]
        decision = router.classify(candidate, news_items=news)
        assert decision.tier == EvalTier.FULL_PIPELINE
        assert "strong catalyst" not in decision.reason

    def test_pump_pattern_weak_catalyst_still_full_pipeline(self):
        """D116: News without strong catalyst keywords → still FULL_PIPELINE."""
        router = _make_router()
        candidate = _make_candidate(current_price=2.50, gap_pct=0.35)
        news = [{"headline": "Stock is moving up today", "summary": "Momentum play"}]
        decision = router.classify(candidate, news_items=news)
        assert decision.tier == EvalTier.FULL_PIPELINE

    def test_pump_threshold_boundary_below(self):
        """Gap at exactly pump threshold but price above → not pump pattern."""
        router = _make_router()
        candidate = _make_candidate(current_price=5.00, gap_pct=0.35)
        decision = router.classify(candidate)
        # Price > $3 → not a pump pattern, should proceed to Tier 2/3
        assert decision.tier != EvalTier.INSTANT_REJECT or "pump" not in decision.reason


# ─── Tier 2: DETERMINISTIC_ONLY Tests ──────────────────────────────────

class TestTier2DeterministicOnly:
    """High-confidence deterministic cases that skip LLM."""

    def test_strong_deterministic_mfcs(self):
        """MFCS > strong_pass threshold → DETERMINISTIC_ONLY."""
        router = _make_router()
        candidate = _make_candidate()
        decision = router.classify(candidate, deterministic_mfcs=0.45)
        assert decision.tier == EvalTier.DETERMINISTIC_ONLY
        assert "strong deterministic" in decision.reason
        assert decision.deterministic_mfcs == 0.45

    def test_weak_deterministic_mfcs(self):
        """MFCS < clear_reject threshold → DETERMINISTIC_ONLY."""
        router = _make_router()
        candidate = _make_candidate()
        decision = router.classify(candidate, deterministic_mfcs=0.02)
        assert decision.tier == EvalTier.DETERMINISTIC_ONLY
        assert "weak deterministic" in decision.reason
        assert decision.deterministic_mfcs == 0.02

    def test_borderline_mfcs_full_pipeline(self):
        """MFCS in ambiguity band (0.05–0.40) → FULL_PIPELINE."""
        router = _make_router()
        candidate = _make_candidate()
        decision = router.classify(candidate, deterministic_mfcs=0.20)
        assert decision.tier == EvalTier.FULL_PIPELINE

    def test_no_deterministic_mfcs_skips_tier2(self):
        """When deterministic_mfcs is None, Tier 2 checks are skipped."""
        router = _make_router()
        candidate = _make_candidate()
        decision = router.classify(candidate, deterministic_mfcs=None)
        assert decision.tier == EvalTier.FULL_PIPELINE


# ─── Tier 3: FULL_PIPELINE Tests ───────────────────────────────────────

class TestTier3FullPipeline:
    """Genuinely ambiguous candidates that need full LLM evaluation."""

    def test_healthy_candidate_full_pipeline(self):
        """A candidate that passes all Tier 1/2 checks → FULL_PIPELINE."""
        router = _make_router()
        candidate = _make_candidate(
            current_price=8.50,
            gap_pct=0.10,
            rvol=5.0,
            float_shares=5_000_000,
        )
        decision = router.classify(candidate)
        assert decision.tier == EvalTier.FULL_PIPELINE
        assert "borderline" in decision.reason

    def test_candidate_just_above_all_thresholds(self):
        """Candidate barely passing all Tier 1 checks → FULL_PIPELINE."""
        router = _make_router()
        candidate = _make_candidate(
            current_price=0.55,  # Just above $0.50
            gap_pct=0.035,       # Just above 3%
            rvol=1.6,            # Just above 1.5
            float_shares=190_000_000,  # Just below 200M
        )
        decision = router.classify(candidate)
        assert decision.tier == EvalTier.FULL_PIPELINE


# ─── Router Control Tests ──────────────────────────────────────────────

class TestRouterControl:
    """Router enable/disable and configuration."""

    def test_router_disabled_always_full_pipeline(self):
        """When router is disabled, all candidates go to FULL_PIPELINE."""
        router = _make_router(enabled=False)
        # This candidate would normally be INSTANT_REJECT (RVOL too low)
        candidate = _make_candidate(rvol=0.5)
        decision = router.classify(candidate)
        assert decision.tier == EvalTier.FULL_PIPELINE
        assert "router disabled" in decision.reason

    def test_missing_float_shares_not_rejected(self):
        """Candidate with None float_shares passes float check safely."""
        router = _make_router()
        candidate = _make_candidate(float_shares=None)
        decision = router.classify(candidate)
        # Should not be rejected for float (None means unknown, not large)
        assert decision.tier == EvalTier.FULL_PIPELINE

    def test_no_sec_filings_skips_424b5_check(self):
        """When sec_filings is None, 424B5 check is safely skipped."""
        router = _make_router()
        candidate = _make_candidate()
        decision = router.classify(candidate, sec_filings=None)
        # Should proceed normally
        assert decision.tier == EvalTier.FULL_PIPELINE


# ─── Metadata and Logging Tests ────────────────────────────────────────

class TestRouterMetadata:
    """Verify logging, stats, and decision metadata."""

    def test_latency_saved_estimate(self):
        """Tier 1 decisions report ~25s latency savings."""
        router = _make_router()
        candidate = _make_candidate(rvol=0.5)
        decision = router.classify(candidate)
        assert decision.latency_saved_estimate_ms == 25_000

    def test_full_pipeline_zero_latency_saved(self):
        """Tier 3 decisions report 0 latency savings."""
        router = _make_router()
        candidate = _make_candidate()
        decision = router.classify(candidate)
        assert decision.latency_saved_estimate_ms == 0

    def test_classification_time_populated(self):
        """classification_time_ms is populated (non-negative)."""
        router = _make_router()
        candidate = _make_candidate()
        decision = router.classify(candidate)
        assert decision.classification_time_ms >= 0.0

    def test_session_stats_tracking(self):
        """Session stats correctly track tier distribution."""
        router = _make_router()
        # 2 instant rejects
        router.classify(_make_candidate(rvol=0.5))
        router.classify(_make_candidate(current_price=0.10))
        # 1 full pipeline
        router.classify(_make_candidate())

        stats = router.get_session_stats()
        assert stats["total_routed"] == 3
        assert stats["instant_reject"] == 2
        assert stats["full_pipeline"] == 1
        assert stats["pct_saved"] == pytest.approx(66.7, abs=0.1)

    def test_to_log_dict_structure(self):
        """Log dict has the expected structure for trade journal."""
        router = _make_router()
        candidate = _make_candidate(rvol=0.5)
        decision = router.classify(candidate)
        log = router.to_log_dict(decision, "TEST")

        assert "router_decision" in log
        rd = log["router_decision"]
        assert rd["ticker"] == "TEST"
        assert rd["tier"] == "instant_reject"
        assert "reason" in rd
        assert "timestamp" in rd
        assert "latency_saved_estimate_ms" in rd
        assert "classification_time_ms" in rd

    def test_catalyst_detection_with_object_news(self):
        """_has_strong_catalyst works with object-style news items."""
        router = _make_router()

        class FakeNewsItem:
            headline = "Earnings beat expectations"
            summary = "Q4 revenue exceeded estimates"

        assert router._has_strong_catalyst([FakeNewsItem()])

    def test_catalyst_detection_no_match(self):
        """_has_strong_catalyst returns False when no strong keywords."""
        router = _make_router()
        news = [{"headline": "Stock moves higher", "summary": "Momentum continues"}]
        assert not router._has_strong_catalyst(news)

    def test_catalyst_detection_empty_news(self):
        """_has_strong_catalyst returns False for empty/None news."""
        router = _make_router()
        assert not router._has_strong_catalyst(None)
        assert not router._has_strong_catalyst([])


# ─── Tier Priority Tests ───────────────────────────────────────────────

class TestTierPriority:
    """Verify that Tier 1 checks take priority over Tier 2."""

    def test_tier1_overrides_strong_deterministic(self):
        """Even with strong deterministic MFCS, a Tier 1 violation rejects."""
        router = _make_router()
        candidate = _make_candidate(rvol=0.5)  # Tier 1 reject
        decision = router.classify(candidate, deterministic_mfcs=0.50)
        assert decision.tier == EvalTier.INSTANT_REJECT

    def test_424b5_overrides_deterministic(self):
        """424B5 filing rejects even with strong deterministic MFCS."""
        router = _make_router()
        candidate = _make_candidate()
        decision = router.classify(
            candidate,
            sec_filings={"has_424b5_same_day": True},
            deterministic_mfcs=0.50,
        )
        assert decision.tier == EvalTier.INSTANT_REJECT
