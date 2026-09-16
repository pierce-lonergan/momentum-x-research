"""Tests for scenario generator."""

from __future__ import annotations

import pytest

from arena.scenario import ScenarioConfig, ScenarioGenerator


@pytest.fixture
def generator():
    return ScenarioGenerator()


class TestGapAndGo:
    def test_generates_390_bars(self, generator):
        config = ScenarioConfig(name="test", base_pattern="gap_and_go", entry_price=5.0, gap_pct=0.20)
        bars, prev_daily = generator.generate(config)
        assert len(bars) == 390

    def test_prev_daily_computed(self, generator):
        config = ScenarioConfig(name="test", entry_price=6.0, gap_pct=0.20)
        bars, prev_daily = generator.generate(config)
        assert prev_daily["c"] == 5.0  # 6.0 / 1.20 = 5.0

    def test_deterministic_with_seed(self, generator):
        config = ScenarioConfig(name="test", seed=42)
        bars1, _ = generator.generate(config)
        bars2, _ = generator.generate(config)
        assert bars1[0].close == bars2[0].close
        assert bars1[100].close == bars2[100].close

    def test_different_seeds_different_results(self, generator):
        bars1, _ = generator.generate(ScenarioConfig(name="a", seed=1))
        bars2, _ = generator.generate(ScenarioConfig(name="b", seed=2))
        assert bars1[50].close != bars2[50].close

    def test_volume_u_shaped(self, generator):
        config = ScenarioConfig(name="test", rvol=5.0)
        bars, _ = generator.generate(config)
        # Opening volume should be higher than midday
        open_vol = sum(b.volume for b in bars[:15])
        midday_vol = sum(b.volume for b in bars[180:195])
        assert open_vol > midday_vol


class TestGapAndFade:
    def test_fade_pattern(self, generator):
        config = ScenarioConfig(name="fade", base_pattern="gap_and_fade", entry_price=5.0, gap_pct=0.20, seed=42)
        bars, _ = generator.generate(config)
        # Price should generally decline from open
        assert bars[30].close < bars[0].close


class TestInjections:
    def test_flash_crash(self, generator):
        config = ScenarioConfig(
            name="crash", entry_price=10.0, gap_pct=0.10, seed=42,
            inject_at_minute=45, injection_type="flash_crash",
            injection_params={"drop_pct": 0.12, "recovery_bars": 5},
        )
        bars, _ = generator.generate(config)

        # Bar at minute 45 should have a dramatic low
        pre_crash = bars[44].close
        crash_bar = bars[45]
        assert crash_bar.low < pre_crash * 0.90  # At least 10% drop

    def test_trading_halt(self, generator):
        config = ScenarioConfig(
            name="halt", entry_price=8.0, gap_pct=0.15, seed=42,
            inject_at_minute=15, injection_type="trading_halt",
            injection_params={"duration_bars": 5, "resume_gap_pct": -0.05},
        )
        bars, _ = generator.generate(config)

        # Halt bars should have zero volume
        for i in range(15, 20):
            assert bars[i].volume == 0

        # Resume bar should have high volume
        assert bars[20].volume > bars[25].volume

    def test_squeeze(self, generator):
        config = ScenarioConfig(
            name="squeeze", entry_price=5.0, gap_pct=0.20, seed=42,
            inject_at_minute=30, injection_type="squeeze",
            injection_params={"acceleration": 0.005, "duration_bars": 20, "volume_multiplier": 5},
        )
        bars, _ = generator.generate(config)

        # Squeeze bars should have elevated volume
        squeeze_vol = sum(b.volume for b in bars[30:50])
        normal_vol = sum(b.volume for b in bars[60:80])
        assert squeeze_vol > normal_vol * 2  # Volume surge during squeeze
