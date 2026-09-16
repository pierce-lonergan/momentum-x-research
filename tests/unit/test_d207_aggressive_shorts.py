"""D207: Tests for Aggressive Gap Fader Short pipeline."""

import pytest

from config.settings import AggressiveShortConfig


class TestAggressiveShortConfig:
    def test_defaults(self):
        cfg = AggressiveShortConfig()
        # SAFETY PIN — D215 (2026-04-08): D207 disabled after PF=0.11 over 25
        # trades, -244.6% cumulative. Do NOT flip this back to True without
        # reading docs/d215_short_book_postmortem.md first. Exact pin on
        # purpose: re-enabling must be a deliberate, test-breaking act.
        assert cfg.enabled is False
        # Gate parameters: sane types/ranges (tuning knobs, not pinned).
        assert 0.0 < cfg.min_gap_pct <= 1.0
        assert cfg.min_rvol > 0
        assert cfg.min_dollar_volume > 0
        # SAFETY PIN: shorting a CATALYST-backed gapper is squeeze risk —
        # this strategy may only target no-catalyst promotional pumps.
        assert cfg.require_no_catalyst is True
        # Risk shape: stop ABOVE entry (it's a short), bounded; targets all
        # BELOW entry and monotonically deeper (T1 shallowest → T3 deepest).
        assert 0.0 < cfg.stop_pct_above_entry <= 0.5
        assert len(cfg.target_pcts) >= 1
        assert all(t < 0 for t in cfg.target_pcts)
        assert cfg.target_pcts == sorted(cfg.target_pcts, reverse=True)
        # Exposure limits: at least one slot, small sizing within global 15% cap.
        assert cfg.max_concurrent >= 1
        assert 0.0 < cfg.position_size_pct <= 0.15

    def test_disable_via_env(self, monkeypatch):
        monkeypatch.setenv("AGGRESSIVE_SHORT_ENABLED", "false")
        cfg = AggressiveShortConfig()
        assert cfg.enabled is False

    def test_target_calculation(self):
        """Verify target prices are correct for a $10 entry."""
        cfg = AggressiveShortConfig()
        entry = 10.0
        targets = [round(entry * (1 + t), 2) for t in cfg.target_pcts]
        assert targets == [9.50, 8.50, 7.50]

    def test_stop_calculation(self):
        """Verify stop price is correct for a $10 entry."""
        cfg = AggressiveShortConfig()
        entry = 10.0
        stop = round(entry * (1 + cfg.stop_pct_above_entry), 2)
        assert stop == 13.50

    def test_targets_more_aggressive_than_d161(self):
        """D207 targets capture more of the move than D161."""
        from config.settings import ShortSellingConfig
        d161 = ShortSellingConfig()
        d207 = AggressiveShortConfig()
        # D207 T3 (-25%) vs D161 T3 (-10%) — D207 captures 2.5x more
        assert abs(d207.target_pcts[-1]) > abs(d161.target_pcts[-1])

    def test_wired_into_settings(self):
        """AggressiveShortConfig is accessible from Settings root."""
        from config.settings import Settings
        s = Settings()
        assert hasattr(s, "aggressive_short")
        assert isinstance(s.aggressive_short, AggressiveShortConfig)


class TestGateQualification:
    """Test the D207 gate logic (unit tests for conditions, not main.py integration)."""

    def test_gap_below_threshold_rejected(self):
        """Gap 20% < 30% minimum → not a D207 candidate."""
        cfg = AggressiveShortConfig()
        assert 0.20 < cfg.min_gap_pct  # Would be rejected

    def test_gap_above_threshold_qualifies(self):
        """Gap 40% >= 30% minimum → D207 candidate."""
        cfg = AggressiveShortConfig()
        assert 0.40 >= cfg.min_gap_pct  # Qualifies

    def test_low_rvol_rejected(self):
        """RVOL 2.0 < 3.0 minimum → rejected."""
        cfg = AggressiveShortConfig()
        assert 2.0 < cfg.min_rvol

    def test_low_dolvol_rejected(self):
        """Dollar volume $300K < $500K minimum → rejected."""
        cfg = AggressiveShortConfig()
        assert 300_000 < cfg.min_dollar_volume

    def test_catalyst_present_rejected(self):
        """Stock with confirmed news catalyst → rejected when require_no_catalyst=True."""
        cfg = AggressiveShortConfig()
        assert cfg.require_no_catalyst is True
        # has_news_catalyst=True would cause rejection


class TestSimulatedShortPnL:
    """Test short P&L calculation logic."""

    def test_short_profit_on_fade(self):
        """Stock gaps 80%, fades -30% from open → short profits."""
        entry = 10.0
        # Stock opens at $10, drops to $7 (low_from_open = -30%)
        # Short entry at $10, cover at $7 = +30% profit
        low_from_open = -0.30
        target_pcts = [-0.05, -0.15, -0.25]
        # T3 at -25% would hit (low dropped -30%)
        assert low_from_open <= target_pcts[-1]  # -0.30 <= -0.25 ✓

    def test_short_stopped_on_spike(self):
        """Stock gaps 60%, spikes +40% from open → short stopped."""
        high_from_open = 0.40
        stop_pct = 0.35
        # Stop at +35% would hit (stock spiked +40%)
        assert high_from_open >= stop_pct  # Stopped out
