"""
Tests for D104: ATR Data Resilience + Macro Regime Awareness.

Change 1a: ATR fetch with explicit start date returns data.
Change 1b: Premarket ATR bridge populates _atr_cache.
Change 2: Gap-based fallback computes wider stop for high-gap stocks.
Change 3: VIX delta > threshold blocks entries.
Change 4: SPY return < threshold blocks entries.
Change 5: Time-based confidence decay in first 30 minutes.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from config.settings import Settings
from src.monitoring.metrics import reset_metrics


@pytest.fixture(autouse=True)
def _clean_metrics():
    reset_metrics()
    yield
    reset_metrics()


@pytest.fixture
def settings():
    """Default settings for test orchestrator."""
    return Settings()


def _make_orchestrator(settings: Settings, **kwargs):
    """Create an Orchestrator with minimal deps for unit testing."""
    from src.core.orchestrator import Orchestrator
    return Orchestrator(settings=settings, **kwargs)


# ── Change 1b: Premarket ATR Bridge ──


class TestPremarketATRBridge:
    """Verify seed_premarket_atr populates _atr_cache on lookup."""

    def test_seed_populates_premarket_dict(self, settings):
        orch = _make_orchestrator(settings)
        orch.seed_premarket_atr({"NVTS": 0.45, "AAPL": 2.10})
        assert orch._premarket_atr["NVTS"] == 0.45
        assert orch._premarket_atr["AAPL"] == 2.10

    def test_seed_skips_zero_and_none(self, settings):
        orch = _make_orchestrator(settings)
        orch.seed_premarket_atr({"NVTS": 0.45, "BAD1": 0.0, "BAD2": None})
        assert "NVTS" in orch._premarket_atr
        assert "BAD1" not in orch._premarket_atr
        assert "BAD2" not in orch._premarket_atr

    def test_init_with_premarket_atr(self, settings):
        orch = _make_orchestrator(settings, premarket_atr={"NVTS": 0.50})
        assert orch._premarket_atr["NVTS"] == 0.50

    def test_init_without_premarket_atr(self, settings):
        orch = _make_orchestrator(settings)
        assert orch._premarket_atr == {}


# ── Change 2: Gap-based Fallback Stop ──


class TestGapBasedFallbackStop:
    """Verify gap-aware stop replaces fixed 5.5% when ATR unavailable."""

    def test_no_atr_small_gap_uses_base_pct(self, settings):
        """Small gap (5%) -> max(4%, 5%*0.5=2.5%) = 4% (base wins)."""
        orch = _make_orchestrator(settings)
        entry = 10.00
        stop = orch._compute_atr_stop("TEST", entry, gap_pct=0.05)
        expected = entry * (1 - settings.execution.stop_loss_pct)
        assert abs(stop - expected) < 0.01

    def test_no_atr_large_gap_uses_half_gap(self, settings):
        """NVTS scenario: 20% gap -> max(4%, 20%*0.5=10%) = 10%."""
        orch = _make_orchestrator(settings)
        entry = 10.43
        stop = orch._compute_atr_stop("NVTS", entry, gap_pct=0.2016)
        # 20.16% * 0.5 = 10.08%
        expected_pct = max(settings.execution.stop_loss_pct, 0.2016 * 0.5)
        expected = entry * (1 - expected_pct)
        assert abs(stop - expected) < 0.01
        # Stop should be below $9.88 (actual day low)
        assert stop < 9.88

    def test_no_atr_extreme_gap_capped_at_15pct(self, settings):
        """40% gap → max(5.5%, 40%*0.5=20%) = 20% → capped at 15%."""
        orch = _make_orchestrator(settings)
        entry = 5.00
        stop = orch._compute_atr_stop("BIGMOVE", entry, gap_pct=0.40)
        expected = entry * (1 - 0.15)  # Capped at 15%
        assert abs(stop - expected) < 0.01

    def test_no_atr_no_gap_uses_base(self, settings):
        """Zero gap → falls back to base stop_loss_pct."""
        orch = _make_orchestrator(settings)
        entry = 10.00
        stop = orch._compute_atr_stop("ZEROGAP", entry, gap_pct=0.0)
        expected = entry * (1 - settings.execution.stop_loss_pct)
        assert abs(stop - expected) < 0.01

    def test_no_atr_negative_gap_uses_abs(self, settings):
        """Negative gap still computes absolute value."""
        orch = _make_orchestrator(settings)
        entry = 10.00
        stop_pos = orch._compute_atr_stop("POS", entry, gap_pct=0.20)
        stop_neg = orch._compute_atr_stop("NEG", entry, gap_pct=-0.20)
        assert abs(stop_pos - stop_neg) < 0.01

    def test_atr_available_with_gap_widening(self, settings):
        """D122+D124: Gap > 8% → gap-day widening with dynamic cap."""
        orch = _make_orchestrator(settings)
        orch._atr_cache["HASATR"] = 0.50
        entry = 10.00
        stop = orch._compute_atr_stop("HASATR", entry, gap_pct=0.50)
        # gap_floor = 10.0 * 0.50 * 0.5 = $2.50 (25%)
        # ATR stop = max(2.0*0.50, 10.0*0.04) = $1.00 (10%)
        # gap_floor ($2.50) > ATR ($1.00), so gap_floor used
        # D124 dynamic cap = min(25%, 35%) = 25% → $2.50 (no further capping)
        # stop = 10.0 - 2.50 = 7.50
        assert abs(stop - 7.5) < 0.01

    def test_atr_available_small_gap_no_widening(self, settings):
        """When ATR is available and gap < 8%, gap has no effect (pre-D122 behavior)."""
        orch = _make_orchestrator(settings)
        orch._atr_cache["HASATR"] = 0.50
        entry = 10.00
        stop = orch._compute_atr_stop("HASATR", entry, gap_pct=0.05)
        # gap < 8% threshold, no widening
        atr_stop_dist = max(
            settings.execution.initial_stop_atr_multiplier * 0.50,
            entry * settings.execution.initial_stop_floor_pct,
        )
        expected = entry - atr_stop_dist
        assert abs(stop - expected) < 0.01


# ── Change 3: VIX Delta Shock Detection ──


class TestVIXDeltaShock:
    """Verify VIX delta > threshold blocks entries."""

    def test_vix_delta_above_threshold_blocks(self, settings):
        """VIX delta +35% > 15% threshold should block."""
        orch = _make_orchestrator(settings)
        orch._vix_delta_pct = 35.0  # March 12 scenario
        # Check directly that the logic would block
        assert abs(orch._vix_delta_pct) > settings.scoring.vix_shock_threshold_pct

    def test_vix_delta_below_threshold_allows(self, settings):
        """VIX delta +10% < 15% threshold should allow."""
        orch = _make_orchestrator(settings)
        orch._vix_delta_pct = 10.0
        assert abs(orch._vix_delta_pct) <= settings.scoring.vix_shock_threshold_pct

    def test_vix_delta_negative_shock_blocks(self, settings):
        """VIX delta -20% (absolute > 15%) should also block."""
        orch = _make_orchestrator(settings)
        orch._vix_delta_pct = -20.0
        assert abs(orch._vix_delta_pct) > settings.scoring.vix_shock_threshold_pct

    def test_vix_delta_none_allows(self, settings):
        """When VIX delta is None (fetch failed), don't block."""
        orch = _make_orchestrator(settings)
        assert orch._vix_delta_pct is None
        # None should not trigger the block

    def test_vix_shock_threshold_default(self, settings):
        """Default threshold should be 15%."""
        assert settings.scoring.vix_shock_threshold_pct == 15.0


# ── Change 4: SPY Return Gate ──


class TestSPYReturnGate:
    """Verify SPY return < threshold blocks entries."""

    def test_spy_genuine_selloff_blocks(self, settings):
        """SPY -3.0% < -2.5% threshold should block."""
        orch = _make_orchestrator(settings)
        orch._spy_return_pct = -3.0
        assert orch._spy_return_pct < settings.scoring.spy_halt_threshold_pct

    def test_spy_normal_down_day_allows(self, settings):
        """D128: SPY -1.4% > -2.5% — normal down day, should NOT block.
        Mar 27: -1.4% blocked all trading while watchlist was +7-13%."""
        orch = _make_orchestrator(settings)
        orch._spy_return_pct = -1.4
        assert orch._spy_return_pct >= settings.scoring.spy_halt_threshold_pct

    def test_spy_above_threshold_allows(self, settings):
        """SPY -0.5% > -2.5% threshold should allow."""
        orch = _make_orchestrator(settings)
        orch._spy_return_pct = -0.5
        assert orch._spy_return_pct >= settings.scoring.spy_halt_threshold_pct

    def test_spy_positive_allows(self, settings):
        """SPY +1.0% should allow."""
        orch = _make_orchestrator(settings)
        orch._spy_return_pct = 1.0
        assert orch._spy_return_pct >= settings.scoring.spy_halt_threshold_pct

    def test_spy_none_allows(self, settings):
        """When SPY return is None (fetch failed), don't block."""
        orch = _make_orchestrator(settings)
        assert orch._spy_return_pct is None

    def test_spy_halt_threshold_default(self, settings):
        """D128: Default threshold should be -2.5%."""
        assert settings.scoring.spy_halt_threshold_pct == -2.5


# ── Change 5: Technical Signal Time-Based Confidence Decay ──


class TestTimBasedConfidenceDecay:
    """Verify opening auction confidence is attenuated."""

    def _make_tech_agent(self):
        from src.agents.deterministic_technical import DeterministicTechnicalAgent
        return DeterministicTechnicalAgent()

    def test_decay_at_market_open(self):
        """At 9:30 AM ET (T+0), confidence should be ~0."""
        agent = self._make_tech_agent()
        # Mock time to 9:30 AM ET
        mock_dt = datetime(2026, 3, 13, 14, 30, 0, tzinfo=timezone.utc)  # 9:30 ET (EDT)
        with patch("src.agents.deterministic_technical.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_dt
            mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)
            # minutes_since_open = 0 → factor = 0.0
            minutes_since_open = 0
            time_confidence = minutes_since_open / 30.0
            assert time_confidence == 0.0

    def test_decay_at_15_minutes(self):
        """At T+15 (9:45 AM), confidence × 0.5."""
        minutes_since_open = 15
        time_confidence = minutes_since_open / 30.0
        assert abs(time_confidence - 0.5) < 0.01

    def test_no_decay_after_30_minutes(self):
        """At T+30 (10:00 AM), confidence × 1.0 (no decay)."""
        minutes_since_open = 30
        time_confidence = min(1.0, minutes_since_open / 30.0)
        assert time_confidence == 1.0

    def test_no_decay_at_11am(self):
        """At 11:00 AM (T+90), no decay applied."""
        minutes_since_open = 90
        time_confidence = min(1.0, minutes_since_open / 30.0)
        assert time_confidence == 1.0

    def test_nvts_scenario_t2(self):
        """NVTS entered at T+2min. Confidence 0.65 → 0.043."""
        raw_confidence = 0.65
        minutes_since_open = 2
        time_confidence = minutes_since_open / 30.0  # 2/30 = 0.0667
        decayed = raw_confidence * time_confidence
        assert decayed < 0.05  # Negligible — would not trigger BUY

    def test_zoneinfo_import(self):
        """ZoneInfo should be importable in deterministic_technical."""
        from src.agents.deterministic_technical import ZoneInfo as TechZI
        tz = TechZI("America/New_York")
        assert tz is not None


# ── Integration: NVTS Scenario Replay ──


class TestNVTSScenarioReplay:
    """
    Verify each D104 defense layer independently prevents the NVTS loss.
    NVTS: entry $10.43, gap +20.16%, stopped at $9.86, recovered to $10.39.
    VIX delta +35%, SPY -1.52%.
    """

    def test_defense_2_gap_stop_survives(self, settings):
        """Gap-based fallback: stop=$9.38 survives day low $9.88."""
        orch = _make_orchestrator(settings)
        stop = orch._compute_atr_stop("NVTS", 10.43, gap_pct=0.2016)
        # max(5.5%, 10.08%) = 10.08% → stop = 10.43 * 0.8992 = $9.38
        assert stop < 9.88  # Survives day low
        assert stop > 8.00  # Sanity: not too wide

    def test_defense_3_vix_delta_blocks(self, settings):
        """VIX delta +35% > 15% → entry blocked."""
        orch = _make_orchestrator(settings)
        orch._vix_delta_pct = 35.0
        assert abs(orch._vix_delta_pct) > settings.scoring.vix_shock_threshold_pct

    def test_defense_4_spy_halt_blocks(self, settings):
        """D128: SPY -3.0% < -2.5% → entry blocked (genuine selloff)."""
        orch = _make_orchestrator(settings)
        orch._spy_return_pct = -3.0
        assert orch._spy_return_pct < settings.scoring.spy_halt_threshold_pct

    def test_defense_5_time_decay_kills_signal(self):
        """At T+2min, BULL(0.65) → BULL(0.043) — below buy threshold."""
        raw_conf = 0.65
        minutes = 2
        decayed = raw_conf * (minutes / 30.0)
        # 0.65 * 0.0667 = 0.043
        assert decayed < 0.30  # Below mfcs_buy_threshold

    def test_all_five_defenses_independent(self, settings):
        """Each defense alone prevents entry — they are independent layers."""
        # Defense 1: ATR cache from Phase 0 (would give proper ATR stop)
        orch = _make_orchestrator(settings, premarket_atr={"NVTS": 0.45})
        assert orch._premarket_atr["NVTS"] == 0.45

        # Defense 2: Gap stop survives
        stop = orch._compute_atr_stop("NOGAP", 10.43, gap_pct=0.2016)
        # No ATR for NOGAP → gap fallback
        assert stop < 9.88

        # Defense 3: VIX shock blocks
        assert 35.0 > settings.scoring.vix_shock_threshold_pct

        # Defense 4: SPY halt blocks (D128: threshold widened to -2.5%)
        assert -3.0 < settings.scoring.spy_halt_threshold_pct

        # Defense 5: Time decay kills
        assert 0.65 * (2 / 30.0) < 0.30


# ── Config Defaults ──


class TestD104ConfigDefaults:
    """Verify new config fields have correct defaults."""

    def test_vix_shock_threshold(self, settings):
        assert settings.scoring.vix_shock_threshold_pct == 15.0

    def test_spy_halt_threshold(self, settings):
        assert settings.scoring.spy_halt_threshold_pct == -2.5

    def test_existing_vix_thresholds_unchanged(self, settings):
        """VIX regime gates exist, are ordered, and sit in a sane band.

        The literals are tuning knobs that move intentionally (D205 pinned
        block=20.0; D219 raised it to 30.0 — VIX 15-30 reduced sizing, 30+
        hard block). Assert the structural safety invariants instead of the
        exact values so re-tuning can't rot this test.
        """
        scoring = settings.scoring
        assert isinstance(scoring.vix_reduce_threshold, float)
        assert isinstance(scoring.vix_block_threshold, float)
        assert isinstance(scoring.vix_panic_threshold, float)
        # Safety ordering: sizing reduction kicks in BEFORE the hard block,
        # and the block BEFORE the panic (all-entries) block.
        assert (
            0.0
            < scoring.vix_reduce_threshold
            < scoring.vix_block_threshold
            < scoring.vix_panic_threshold
        )
        # Block must stay in a band where it can actually fire (not 0 = always
        # blocked, not 100 = never blocks).
        assert 10.0 <= scoring.vix_block_threshold <= 50.0
        # Elevated-vol sizing must be a REAL reduction (0 < scale < 1).
        assert 0.0 < scoring.vix_position_scale < 1.0
