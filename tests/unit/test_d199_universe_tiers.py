"""D199: Unit tests for tiered stock universe classifier.

Tests the UniverseClassifier and TierConfig logic for both MOMENTUM
(existing low-float strategy) and CATALYST (new mid-cap strategy) tiers.
"""

import pytest

from src.data.universe_tiers import (
    TIER_CONFIGS,
    TierConfig,
    UniverseClassifier,
    UniverseTier,
    universe_classifier,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def classifier() -> UniverseClassifier:
    return UniverseClassifier()


# ── Classification tests ──────────────────────────────────────────────────────

class TestClassifyMomentum:
    """Low-float gap-up → MOMENTUM tier."""

    def test_classify_momentum(self, classifier):
        """Classic low-float setup: cheap price, small mcap, explosive gap."""
        tier = classifier.classify(
            ticker="ARTL",
            price=2.50,
            market_cap=150_000_000,   # $150M — small cap
            gap_pct=0.45,             # 45% gap
            rvol=8.0,                 # 8x volume
            dollar_volume=3_000_000,  # $3M
        )
        assert tier == UniverseTier.MOMENTUM

    def test_classify_momentum_unknown_mcap(self, classifier):
        """Unknown market cap → falls back to MOMENTUM if other criteria pass."""
        tier = classifier.classify(
            ticker="XYZ",
            price=3.00,
            market_cap=None,          # Not available
            gap_pct=0.25,
            rvol=5.0,
            dollar_volume=4_000_000,
        )
        assert tier == UniverseTier.MOMENTUM

    def test_momentum_needs_high_gap(self, classifier):
        """3% gap is NOT enough for MOMENTUM tier (needs 5%+)."""
        tier = classifier.classify(
            ticker="LOWGAP",
            price=3.00,
            market_cap=200_000_000,   # Small cap — not $1B for CATALYST
            gap_pct=0.03,             # Only 3% — below 5% MOMENTUM floor
            rvol=5.0,
            dollar_volume=3_000_000,
        )
        assert tier == UniverseTier.UNKNOWN


class TestClassifyCatalyst:
    """Mid-cap earnings/FDA/contract gap → CATALYST tier."""

    def test_classify_catalyst(self, classifier):
        """Textbook mid-cap catalyst: $5B mcap, 5% earnings gap."""
        tier = classifier.classify(
            ticker="BIOG",
            price=45.0,
            market_cap=5_000_000_000,   # $5B
            gap_pct=0.05,               # 5% earnings gap
            rvol=2.0,                   # 2x volume
            dollar_volume=25_000_000,   # $25M
        )
        assert tier == UniverseTier.CATALYST

    def test_catalyst_lower_gap(self, classifier):
        """3% gap is acceptable for CATALYST tier (mid-caps don't gap 100%)."""
        tier = classifier.classify(
            ticker="MIDCO",
            price=30.0,
            market_cap=2_000_000_000,   # $2B
            gap_pct=0.03,               # 3% — just above CATALYST floor
            rvol=1.5,
            dollar_volume=15_000_000,
        )
        assert tier == UniverseTier.CATALYST

    def test_catalyst_high_price(self, classifier):
        """Mid-cap stock priced above $50 (above MOMENTUM ceiling) → CATALYST."""
        tier = classifier.classify(
            ticker="HIGHPX",
            price=120.0,               # Above $50 MOMENTUM ceiling
            market_cap=8_000_000_000,   # $8B
            gap_pct=0.04,
            rvol=1.8,
            dollar_volume=30_000_000,
        )
        assert tier == UniverseTier.CATALYST

    def test_catalyst_preferred_over_momentum_on_overlap(self, classifier):
        """Stock meeting both tiers' criteria → CATALYST wins (checked first)."""
        # Price $10, mcap $2B, gap 8%, rvol 3x, dolvol $12M — meets BOTH
        tier = classifier.classify(
            ticker="OVERLAP",
            price=10.0,
            market_cap=2_000_000_000,  # $2B — qualifies for CATALYST
            gap_pct=0.08,
            rvol=3.0,
            dollar_volume=12_000_000,  # Meets both $2M momentum and $10M catalyst
        )
        assert tier == UniverseTier.CATALYST


class TestClassifyUnknown:
    """Stocks that don't fit either active tier → UNKNOWN."""

    def test_classify_unknown(self, classifier):
        """Tiny dollar volume, low gap, below any tier criteria."""
        tier = classifier.classify(
            ticker="JUNK",
            price=2.00,
            market_cap=50_000_000,
            gap_pct=0.01,         # 1% — below all floors
            rvol=1.0,
            dollar_volume=500_000,  # Only $500K
        )
        assert tier == UniverseTier.UNKNOWN

    def test_classify_unknown_large_cap(self, classifier):
        """Large-cap stocks ($50B+) fall into UNKNOWN — LARGE_CAP tier not active."""
        tier = classifier.classify(
            ticker="AAPL",
            price=180.0,
            market_cap=2_800_000_000_000,  # $2.8T
            gap_pct=0.03,
            rvol=1.5,
            dollar_volume=1_000_000_000,
        )
        # LARGE_CAP tier criteria: mcap > $50B — but classify() doesn't check it
        # Large-cap with mcap > $50B fails CATALYST ceiling ($50B) and MOMENTUM
        # (price > $50). So it should return UNKNOWN.
        assert tier == UniverseTier.UNKNOWN


# ── Tier-specific criteria tests ─────────────────────────────────────────────

class TestTierCriteriaBoundaries:

    def test_catalyst_min_gap_boundary(self, classifier):
        """Exactly at 3% gap passes CATALYST; below fails."""
        # At boundary
        at_boundary = classifier.classify(
            "TEST", 50.0, 3_000_000_000, 0.03, 1.5, 15_000_000
        )
        assert at_boundary == UniverseTier.CATALYST

        # Just below
        below = classifier.classify(
            "TEST", 50.0, 3_000_000_000, 0.029, 1.5, 15_000_000
        )
        assert below == UniverseTier.UNKNOWN

    def test_market_cap_boundary(self, classifier):
        """$1B boundary: below → MOMENTUM path; at/above → CATALYST."""
        # Just below $1B — can't be CATALYST
        tier_below = classifier.classify(
            "SMALLCAP", 8.0, 999_999_999, 0.06, 2.0, 11_000_000
        )
        # Passes MOMENTUM (price $8 ok, gap 6% ok, rvol 2x ok, dolvol $11M > $2M)
        assert tier_below == UniverseTier.MOMENTUM

        # At exactly $1B — CATALYST
        tier_at = classifier.classify(
            "MIDCAP", 8.0, 1_000_000_000, 0.06, 2.0, 11_000_000
        )
        assert tier_at == UniverseTier.CATALYST

    def test_dollar_volume_requirement_catalyst(self, classifier):
        """CATALYST requires $10M dolvol; $9.9M is not enough."""
        tier_pass = classifier.classify(
            "DOLVOL", 40.0, 2_000_000_000, 0.04, 1.6, 10_000_000
        )
        assert tier_pass == UniverseTier.CATALYST

        tier_fail = classifier.classify(
            "DOLVOL", 40.0, 2_000_000_000, 0.04, 1.6, 9_999_999
        )
        # Fails CATALYST ($10M floor), also fails MOMENTUM (price $40 ok but mcap $2B > $1B cap)
        assert tier_fail == UniverseTier.UNKNOWN


# ── TierConfig parameter tests ────────────────────────────────────────────────

class TestTierConfigParams:

    def test_tier_config_params(self):
        """CATALYST has tighter stop loss than MOMENTUM."""
        catalyst = TIER_CONFIGS[UniverseTier.CATALYST]
        momentum = TIER_CONFIGS[UniverseTier.MOMENTUM]
        assert catalyst.stop_distance_pct < momentum.stop_distance_pct

    def test_catalyst_no_shorting(self):
        """Short selling is disabled for CATALYST tier."""
        catalyst = TIER_CONFIGS[UniverseTier.CATALYST]
        assert catalyst.short_selling_enabled is False

    def test_momentum_shorting_enabled(self):
        """Short selling is enabled for MOMENTUM tier (D161)."""
        momentum = TIER_CONFIGS[UniverseTier.MOMENTUM]
        assert momentum.short_selling_enabled is True

    def test_catalyst_shorter_observation(self):
        """CATALYST has a shorter observation window than MOMENTUM."""
        catalyst = TIER_CONFIGS[UniverseTier.CATALYST]
        momentum = TIER_CONFIGS[UniverseTier.MOMENTUM]
        assert catalyst.observation_minutes < momentum.observation_minutes
        assert catalyst.observation_minutes == 5.0
        assert momentum.observation_minutes == 15.0

    def test_catalyst_higher_faller_threshold(self):
        """CATALYST is more permissive on faller score (0.70 vs 0.60)."""
        catalyst = TIER_CONFIGS[UniverseTier.CATALYST]
        momentum = TIER_CONFIGS[UniverseTier.MOMENTUM]
        assert catalyst.faller_reject_threshold > momentum.faller_reject_threshold
        assert catalyst.faller_reject_threshold == 0.70
        assert momentum.faller_reject_threshold == 0.60


# ── Override params tests ─────────────────────────────────────────────────────

class TestOverrideParams:

    def test_override_params_catalyst(self, classifier):
        """CATALYST overrides return correct parameter values."""
        overrides = classifier.should_override_params(UniverseTier.CATALYST)
        assert overrides["faller_reject_threshold"] == 0.70
        assert overrides["short_selling_enabled"] is False
        assert overrides["observation_minutes"] == 5.0
        assert overrides["stop_distance_pct"] == 0.08

    def test_override_params_momentum(self, classifier):
        """MOMENTUM overrides match global defaults."""
        overrides = classifier.should_override_params(UniverseTier.MOMENTUM)
        assert overrides["faller_reject_threshold"] == 0.60
        assert overrides["short_selling_enabled"] is True
        assert overrides["observation_minutes"] == 15.0

    def test_override_params_unknown_falls_back(self, classifier):
        """UNKNOWN tier falls back to MOMENTUM config."""
        overrides = classifier.should_override_params(UniverseTier.UNKNOWN)
        momentum_overrides = classifier.should_override_params(UniverseTier.MOMENTUM)
        assert overrides == momentum_overrides

    def test_override_params_all_keys_present(self, classifier):
        """Both tiers return all expected override keys."""
        expected_keys = {
            "faller_reject_threshold",
            "stop_distance_pct",
            "trailing_activation_pct",
            "short_selling_enabled",
            "observation_minutes",
            "max_positions",
        }
        for tier in (UniverseTier.MOMENTUM, UniverseTier.CATALYST):
            overrides = classifier.should_override_params(tier)
            assert set(overrides.keys()) == expected_keys, f"Missing keys for {tier}"


# ── Module-level singleton ─────────────────────────────────────────────────────

def test_module_singleton_works():
    """The module-level singleton classifies correctly."""
    tier = universe_classifier.classify(
        "NVDA", 500.0, 1_200_000_000_000,  # $1.2T — too large for CATALYST
        0.04, 2.0, 100_000_000
    )
    # $1.2T > $50B CATALYST ceiling → UNKNOWN (LARGE_CAP not active)
    assert tier == UniverseTier.UNKNOWN
