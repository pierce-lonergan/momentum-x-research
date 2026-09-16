"""
MOMENTUM-X Tests: D118 Entry Catalyst Profiler

Tests for CatalystProfile model, exit strategy overrides,
and integration wiring.
"""

import pytest

from src.core.models import CatalystProfile, TradeVerdict
from src.execution.position_manager import ManagedPosition
from src.execution.exit_strategies import (
    CatalystHalfLifeStrategy,
    GratitudeExitStrategy,
    AlphaDecayOracle,
    ParallelExitEngine,
)


# ── Model Tests ──────────────────────────────────────────────────


class TestCatalystProfileModel:
    """Verify CatalystProfile construction and validation."""

    def test_default_profile(self):
        """Defaults to SHORT_LIVED with 20-min half-life."""
        p = CatalystProfile(ticker="TEST")
        assert p.durability == "SHORT_LIVED"
        assert p.recommended_half_life_minutes == 20
        assert p.recommended_atr_multiplier == 2.0
        assert p.recommended_risk_scale == 1.0
        assert p.recommended_gratitude_decay == 0.05

    def test_permanent_revaluation(self):
        """FDA approval → PERMANENT_REVALUATION with long half-life."""
        p = CatalystProfile(
            ticker="ANNA",
            catalyst_type="fda_approval",
            durability="PERMANENT_REVALUATION",
            confidence=0.9,
            recommended_half_life_minutes=180,
            recommended_atr_multiplier=3.0,
            recommended_risk_scale=1.5,
            recommended_gratitude_decay=0.02,
        )
        assert p.durability == "PERMANENT_REVALUATION"
        assert p.recommended_half_life_minutes == 180
        assert p.recommended_atr_multiplier == 3.0
        assert p.recommended_risk_scale == 1.5
        assert p.recommended_gratitude_decay == 0.02

    def test_ephemeral_profile(self):
        """Social pump → EPHEMERAL with short half-life."""
        p = CatalystProfile(
            ticker="PUMP",
            catalyst_type="social_media_promo",
            durability="EPHEMERAL",
            confidence=0.7,
            recommended_half_life_minutes=8,
            recommended_atr_multiplier=1.5,
            recommended_risk_scale=0.5,
            recommended_gratitude_decay=0.10,
        )
        assert p.durability == "EPHEMERAL"
        assert p.recommended_half_life_minutes == 8
        assert p.recommended_gratitude_decay == 0.10

    def test_invalid_durability_sanitized(self):
        """Unknown durability string sanitized to SHORT_LIVED."""
        p = CatalystProfile(ticker="TEST", durability="NONSENSE")
        assert p.durability == "SHORT_LIVED"

    def test_risk_scale_clamped(self):
        """Risk scale clamped to [0.25, 2.0]."""
        with pytest.raises(Exception):
            CatalystProfile(ticker="TEST", recommended_risk_scale=3.0)
        with pytest.raises(Exception):
            CatalystProfile(ticker="TEST", recommended_risk_scale=0.1)

    def test_profile_on_verdict(self):
        """CatalystProfile attaches to TradeVerdict."""
        profile = CatalystProfile(
            ticker="TEST",
            catalyst_type="earnings_beat",
            durability="MULTI_HOUR",
            recommended_half_life_minutes=120,
        )
        verdict = TradeVerdict(
            ticker="TEST",
            action="BUY",
            confidence=0.8,
            mfcs=0.5,
            entry_price=5.0,
            stop_loss=4.5,
            target_prices=[5.5, 6.0],
            position_size_pct=0.10,
            catalyst_profile=profile,
        )
        assert verdict.catalyst_profile is not None
        assert verdict.catalyst_profile.durability == "MULTI_HOUR"
        assert verdict.catalyst_profile.recommended_half_life_minutes == 120


# ── ManagedPosition Tests ────────────────────────────────────────


class TestProfileOnManagedPosition:
    """Verify position fields populated from profile."""

    def test_default_position_fields(self):
        """Default position has standard catalyst params."""
        pos = ManagedPosition(
            ticker="TEST", qty=100, entry_price=5.0, signal_price=5.0,
            stop_loss=4.5, target_prices=[5.5],
        )
        assert pos.catalyst_half_life_minutes == 20
        assert pos.catalyst_gratitude_decay == 0.05

    def test_custom_position_fields(self):
        """Position with profiler overrides."""
        pos = ManagedPosition(
            ticker="TEST", qty=100, entry_price=5.0, signal_price=5.0,
            stop_loss=4.5, target_prices=[5.5],
            catalyst_half_life_minutes=180,
            catalyst_gratitude_decay=0.02,
        )
        assert pos.catalyst_half_life_minutes == 180
        assert pos.catalyst_gratitude_decay == 0.02


# ── Exit Strategy Override Tests ─────────────────────────────────


class TestHalfLifeOverride:
    """CatalystHalfLife uses position-level half_life_override."""

    def test_static_table_default(self):
        """Without override, uses static table."""
        strat = CatalystHalfLifeStrategy()
        result = strat.evaluate(
            minutes_held=25, velocity_per_min=0.001,
            catalyst_type="unknown", manipulation_phase="ORGANIC_MOMENTUM",
        )
        # Default unknown = 20 min, exit at 1.5x = 30 min
        # 25 min < 30 min exit deadline → tighten (> 0.8 × 20 = 16 min)
        assert result.should_tighten
        assert not result.should_exit

    def test_override_extends_half_life(self):
        """With 180-min override, 25 min is well within IGNITION."""
        strat = CatalystHalfLifeStrategy()
        result = strat.evaluate(
            minutes_held=25, velocity_per_min=0.001,
            catalyst_type="unknown", manipulation_phase="ORGANIC_MOMENTUM",
            half_life_override=180.0,
        )
        # 180 min half-life: tighten at 144 min, exit at 270 min
        # 25 min is well before either → no signal
        assert not result.should_tighten
        assert not result.should_exit

    def test_override_shortens_half_life(self):
        """With 8-min override, 15 min triggers exit."""
        strat = CatalystHalfLifeStrategy()
        result = strat.evaluate(
            minutes_held=15, velocity_per_min=-0.001,
            catalyst_type="unknown", manipulation_phase="ORGANIC_MOMENTUM",
            half_life_override=8.0,
        )
        # 8 min half-life: exit at 12 min, 15 > 12 AND velocity < 0 → exit
        assert result.should_exit


class TestGratitudeDecayOverride:
    """GratitudeExit uses position-level decay_per_min."""

    def test_default_decay(self):
        """Without override, uses class constant 0.05."""
        strat = GratitudeExitStrategy()
        # entry=5.0, stop=4.5, 1R=$0.50
        # At 30 min with default 0.05 decay: threshold = max(0.75, 3.0 - 0.05*30) = max(0.75, 1.5) = 1.5R
        # Price=5.80 → unrealized = (5.80-5.0)/0.50 = 1.6R ≥ 1.5R → tighten
        result = strat.evaluate(5.0, 4.5, 5.80, 30.0)
        assert result.should_tighten

    def test_slow_decay_holds_longer(self):
        """With 0.02 decay (PERMANENT_REVALUATION), threshold stays higher."""
        strat = GratitudeExitStrategy()
        # At 30 min with 0.02 decay: threshold = max(0.75, 3.0 - 0.02*30) = max(0.75, 2.4) = 2.4R
        # Price=5.80 → unrealized = 1.6R < 2.4R → no signal (hold for bigger R)
        result = strat.evaluate(5.0, 4.5, 5.80, 30.0, decay_per_min=0.02)
        assert not result.should_tighten
        assert not result.should_exit

    def test_fast_decay_exits_earlier(self):
        """With 0.10 decay (EPHEMERAL), threshold drops to floor faster."""
        strat = GratitudeExitStrategy()
        # At 30 min with 0.10 decay: threshold = max(0.75, 3.0 - 0.10*30) = max(0.75, 0.0) = 0.75R
        # Price=5.80 → unrealized = 1.6R ≥ 0.75R × 1.5 = 1.125R → EXIT
        result = strat.evaluate(5.0, 4.5, 5.80, 30.0, decay_per_min=0.10)
        assert result.should_exit


class TestAlphaOracleDurabilityShift:
    """AlphaDecayOracle shifts null curve by durability."""

    def test_no_shift_default(self):
        """Without shift, uses raw minutes_since_open."""
        oracle = AlphaDecayOracle()
        # At 30 min, null return = 0.5%. If observed return = 0.3% → alpha = -0.2% → exit
        result = oracle.evaluate("TEST", 0.3, 30.0)
        assert result.should_exit

    def test_positive_shift_delays_exit(self):
        """Positive shift (PERMANENT) → looks up earlier time → higher null → more alpha."""
        oracle = AlphaDecayOracle()
        # Shift +16 min: effective lookup = 30 - 16 = 14 min
        # At 14 min, null return ≈ 1.6%. observed 0.3% → alpha = -1.3% → still exit
        # But with bigger observed return...
        # At 14 min, null return ≈ 1.6%. observed 2.0% → alpha = +0.4% → thin alpha tighten
        result = oracle.evaluate("TEST2", 2.0, 30.0, durability_shift_minutes=16.0)
        # At 30 min without shift: null = 0.5%, alpha = 1.5% → no signal
        # At 14 min with shift: null ≈ 1.6%, alpha ≈ 0.4% → tighten (thin alpha)
        assert result.should_tighten

    def test_negative_shift_accelerates_exit(self):
        """Negative shift (EPHEMERAL) → looks up later time → lower null → less alpha."""
        oracle = AlphaDecayOracle()
        # At 20 min without shift: null = 1.0%. observed 1.2% → alpha = 0.2% → tighten
        result_no_shift = oracle.evaluate("TEST3", 1.2, 20.0)
        assert result_no_shift.should_tighten  # thin alpha

        # Shift -3 min: effective = 20 + 3 = 23 min → null ≈ 0.75%
        # alpha = 1.2 - 0.75 = 0.45% → still thin alpha tighten
        result_shifted = oracle.evaluate("TEST4", 1.2, 20.0, durability_shift_minutes=-3.0)
        assert result_shifted.should_tighten


class TestParallelEnginePassthrough:
    """ParallelExitEngine passes D118 params to individual strategies."""

    def test_default_params_no_change(self):
        """Without D118 params, backward compatible."""
        engine = ParallelExitEngine()
        bars = [{"c": 5.0}, {"c": 5.01}, {"c": 5.02}]
        results = engine.evaluate_all(
            ticker="TEST", entry_price=5.0, stop_loss=4.5,
            current_price=5.10, peak_price=5.10, minutes_held=10.0,
            entry_volume=100000, bars=bars,
        )
        assert len(results) == 6
        # All strategy names present
        names = {r.strategy_name for r in results}
        assert "velocity" in names
        assert "gratitude" in names
        assert "catalyst_half_life" in names
        assert "alpha_oracle" in names

    def test_d118_params_passed_through(self):
        """D118 position-level params change strategy behavior."""
        engine = ParallelExitEngine()
        bars = [{"c": 5.0}, {"c": 5.01}, {"c": 5.02}]

        # With 180-min half-life override: 25 min should NOT tighten
        results = engine.evaluate_all(
            ticker="TEST2", entry_price=5.0, stop_loss=4.5,
            current_price=5.10, peak_price=5.10, minutes_held=25.0,
            entry_volume=100000, bars=bars,
            catalyst_half_life_minutes=180.0,
        )
        half_life_result = [r for r in results if r.strategy_name == "catalyst_half_life"][0]
        # 180-min half-life: tighten at 144, exit at 270 → no signal at 25 min
        assert not half_life_result.should_tighten
        assert not half_life_result.should_exit


class TestShadowModeNoOverride:
    """When profiler is enabled but not active, static table used."""

    def test_half_life_none_uses_table(self):
        """half_life_override=None falls back to table lookup."""
        strat = CatalystHalfLifeStrategy()
        # fda_approval has 180-min half-life in static table
        result = strat.evaluate(
            minutes_held=25, velocity_per_min=0.001,
            catalyst_type="fda_approval", manipulation_phase="ORGANIC_MOMENTUM",
            half_life_override=None,
        )
        # 180 min half-life from table → no signal at 25 min
        assert not result.should_tighten
        assert not result.should_exit

    def test_gratitude_none_uses_default(self):
        """decay_per_min=None falls back to class constant."""
        strat = GratitudeExitStrategy()
        result_default = strat.evaluate(5.0, 4.5, 5.80, 30.0)
        result_none = strat.evaluate(5.0, 4.5, 5.80, 30.0, decay_per_min=None)
        assert result_default.should_tighten == result_none.should_tighten
        assert result_default.should_exit == result_none.should_exit


class TestPromotionalEarlyOverridesProfile:
    """PROMOTIONAL_EARLY still halves the half-life even with strong profile."""

    def test_promo_early_multiplier_applied(self):
        """PROMOTIONAL_EARLY × 0.6 multiplier applied to override."""
        strat = CatalystHalfLifeStrategy()
        # Without PROMOTIONAL_EARLY, 180-min half-life → no signal at 100 min
        result_organic = strat.evaluate(
            minutes_held=100, velocity_per_min=0.001,
            catalyst_type="fda_approval", manipulation_phase="ORGANIC_MOMENTUM",
        )
        assert not result_organic.should_tighten

        # With PROMOTIONAL_EARLY and NO override: fda 180 × 0.6 = 108 → tighten at 86.4
        result_promo = strat.evaluate(
            minutes_held=100, velocity_per_min=0.001,
            catalyst_type="fda_approval", manipulation_phase="PROMOTIONAL_EARLY",
        )
        assert result_promo.should_tighten

    def test_override_bypasses_promo_multiplier(self):
        """With half_life_override, PROMOTIONAL_EARLY multiplier NOT applied
        (the profiler already accounts for manipulation phase)."""
        strat = CatalystHalfLifeStrategy()
        # Override = 180, PROMOTIONAL_EARLY — override bypasses table + multiplier
        result = strat.evaluate(
            minutes_held=100, velocity_per_min=0.001,
            catalyst_type="fda_approval", manipulation_phase="PROMOTIONAL_EARLY",
            half_life_override=180.0,
        )
        # Override = 180 → tighten at 144, exit at 270. 100 < 144 → no signal
        assert not result.should_tighten


class TestUnknownCatalystDefaultsShortLived:
    """When news agent returns 'NONE', profiler defaults to SHORT_LIVED."""

    def test_unknown_catalyst_profile(self):
        """Unknown catalyst type gets SHORT_LIVED defaults."""
        p = CatalystProfile(ticker="TEST", catalyst_type="unknown")
        assert p.durability == "SHORT_LIVED"
        assert p.recommended_half_life_minutes == 20
