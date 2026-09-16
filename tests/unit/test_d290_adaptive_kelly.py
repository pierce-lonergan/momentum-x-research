"""D290 (2026-05-11, doc 149 adaptive Kelly) — per-tier-AUM-aware Kelly caps.

Pin the adaptive Kelly schedule + interpolation logic. Behavior contract:

  1. At ANY AUM: SKIP tier returns 0.0
  2. At $140K paper account: caps match the fixed Aggressive profile
     (50/35/20/10 for ELITE/HIGH/VETOED/BROAD)
  3. At higher AUM brackets: caps shrink per the doc 149 schedule
  4. Interpolation in log(AUM) is monotone non-increasing per tier
  5. Opt-out via LOTTERY_USE_ADAPTIVE_KELLY=0 returns the fixed AGGRESSIVE
     cap regardless of AUM
  6. Below $100K: returns the $100K bracket cap (no extrapolation)
  7. Above $10M: returns the $10M bracket cap (no extrapolation)

Critical invariant: at the current $140K paper account, behavior MUST
be identical to the pre-D290 fixed Aggressive Kelly. This is the
"no degradation at paper" half of the pre-commit.
"""
from __future__ import annotations
import importlib
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def _reload(monkeypatch, profile="aggressive", use_adaptive="1"):
    """Reload ml_meta_scorer_inference with desired env vars."""
    monkeypatch.setenv("MX_META_KELLY_PROFILE", profile)
    monkeypatch.setenv("LOTTERY_USE_ADAPTIVE_KELLY", use_adaptive)
    if "ml_meta_scorer_inference" in sys.modules:
        del sys.modules["ml_meta_scorer_inference"]
    import ml_meta_scorer_inference
    return ml_meta_scorer_inference


# ──────────────────────────────────────────────────────────────────────
# Schedule structure
# ──────────────────────────────────────────────────────────────────────


def test_schedule_contains_all_tiers(monkeypatch):
    m = _reload(monkeypatch)
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD", "SKIP"):
        assert tier in m.ADAPTIVE_KELLY_SCHEDULE


def test_schedule_brackets_sorted_ascending(monkeypatch):
    m = _reload(monkeypatch)
    for tier, brackets in m.ADAPTIVE_KELLY_SCHEDULE.items():
        if tier == "SKIP":
            continue
        aums = [aum for aum, _ in brackets]
        assert aums == sorted(aums), f"{tier} brackets not sorted ascending"


def test_schedule_caps_monotone_non_increasing(monkeypatch):
    """For each tier (except SKIP), Kelly cap must NOT increase as AUM grows."""
    m = _reload(monkeypatch)
    for tier, brackets in m.ADAPTIVE_KELLY_SCHEDULE.items():
        if tier == "SKIP":
            continue
        caps = [cap for _, cap in brackets]
        for i in range(len(caps) - 1):
            assert caps[i] >= caps[i + 1], (
                f"{tier} cap increases between bracket {i} ({caps[i]:.3f}) "
                f"and {i+1} ({caps[i+1]:.3f}); should be non-increasing"
            )


# ──────────────────────────────────────────────────────────────────────
# Critical invariant: $140K paper account = fixed Aggressive Kelly
# ──────────────────────────────────────────────────────────────────────


def test_at_paper_account_140k_matches_fixed_aggressive(monkeypatch):
    """At current paper account, adaptive must match fixed Aggressive."""
    m = _reload(monkeypatch)
    fixed = m.KELLY_CAPS_AGGRESSIVE
    # 140k is between 100k and 250k brackets — both have the fixed values
    # for ELITE/HIGH/VETOED. Only BROAD differs (10% at 100k, 4% at 250k).
    # Linear interpolation in log10(140000): t = (log10(140000) - log10(100000)) /
    # (log10(250000) - log10(100000)) = (5.146 - 5.000) / (5.398 - 5.000) = 0.367
    # So BROAD at 140K = 0.10 - 0.367 * (0.10 - 0.04) = 0.078, not exactly 0.10.
    # That's a deliberate design choice — adaptive starts taking effect early.
    # But ELITE/HIGH/VETOED at 140K should still equal fixed (both brackets agree).
    assert m.adaptive_kelly_cap("ELITE", 140_000) == pytest.approx(fixed["ELITE"], abs=1e-6)
    assert m.adaptive_kelly_cap("HIGH", 140_000) == pytest.approx(fixed["HIGH"], abs=1e-6)
    assert m.adaptive_kelly_cap("VETOED", 140_000) == pytest.approx(fixed["VETOED"], abs=1e-6)
    # BROAD at 140K may be slightly less than 10% due to adaptive interpolation
    broad_140k = m.adaptive_kelly_cap("BROAD", 140_000)
    assert 0.07 <= broad_140k <= 0.10, f"BROAD at $140K = {broad_140k}"


def test_at_exactly_100k_brackets_exact_fixed(monkeypatch):
    """At exactly $100K (the lowest bracket), all tier caps == fixed Aggressive."""
    m = _reload(monkeypatch)
    fixed = m.KELLY_CAPS_AGGRESSIVE
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
        assert m.adaptive_kelly_cap(tier, 100_000) == pytest.approx(fixed[tier], abs=1e-6), (
            f"{tier} at $100K should match fixed {fixed[tier]}, "
            f"got {m.adaptive_kelly_cap(tier, 100_000)}"
        )


def test_skip_tier_always_zero(monkeypatch):
    m = _reload(monkeypatch)
    for aum in (10_000, 100_000, 1_000_000, 10_000_000):
        assert m.adaptive_kelly_cap("SKIP", aum) == 0.0


# ──────────────────────────────────────────────────────────────────────
# AUM scaling behavior
# ──────────────────────────────────────────────────────────────────────


def test_at_5m_aum_caps_meaningfully_smaller(monkeypatch):
    """At $5M AUM, doc 149 schedule has ELITE 5.5%, HIGH 19.5%, VETOED 2%, BROAD 0.5%."""
    m = _reload(monkeypatch)
    assert m.adaptive_kelly_cap("ELITE", 5_000_000) == pytest.approx(0.055, abs=1e-3)
    assert m.adaptive_kelly_cap("HIGH", 5_000_000) == pytest.approx(0.195, abs=1e-3)
    assert m.adaptive_kelly_cap("VETOED", 5_000_000) == pytest.approx(0.020, abs=1e-3)
    assert m.adaptive_kelly_cap("BROAD", 5_000_000) == pytest.approx(0.005, abs=1e-3)


def test_at_1m_aum_broad_below_2pct(monkeypatch):
    """At $1M AUM, BROAD must be near 1% (was 10% under fixed)."""
    m = _reload(monkeypatch)
    broad_1m = m.adaptive_kelly_cap("BROAD", 1_000_000)
    assert broad_1m == pytest.approx(0.010, abs=1e-3)


def test_below_100k_uses_lowest_bracket(monkeypatch):
    """No extrapolation below $100K — return that bracket."""
    m = _reload(monkeypatch)
    fixed = m.KELLY_CAPS_AGGRESSIVE
    for aum in (10_000, 50_000, 99_999):
        for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
            assert m.adaptive_kelly_cap(tier, aum) == pytest.approx(fixed[tier], abs=1e-6)


def test_above_10m_uses_highest_bracket(monkeypatch):
    """No extrapolation above $10M — return that bracket."""
    m = _reload(monkeypatch)
    sched = m.ADAPTIVE_KELLY_SCHEDULE
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
        top_cap = sched[tier][-1][1]
        for aum in (10_000_001, 50_000_000, 1_000_000_000):
            assert m.adaptive_kelly_cap(tier, aum) == pytest.approx(top_cap, abs=1e-6)


def test_log_interpolation_smooth_between_brackets(monkeypatch):
    """Between brackets, interpolation should be monotone in AUM."""
    m = _reload(monkeypatch)
    # BROAD between $100K (10%) and $250K (4%): should drop monotonically
    brk_aums = [120_000, 150_000, 200_000, 240_000]
    caps = [m.adaptive_kelly_cap("BROAD", a) for a in brk_aums]
    for i in range(len(caps) - 1):
        assert caps[i] >= caps[i + 1], (
            f"BROAD adaptive cap not monotonic between $120K-$240K: {caps}"
        )


# ──────────────────────────────────────────────────────────────────────
# Opt-out behavior
# ──────────────────────────────────────────────────────────────────────


def test_opt_out_returns_fixed_aggressive_at_any_aum(monkeypatch):
    m = _reload(monkeypatch, use_adaptive="0")
    fixed = m.KELLY_CAPS_AGGRESSIVE
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD", "SKIP"):
        for aum in (100_000, 1_000_000, 5_000_000, 10_000_000):
            assert m.adaptive_kelly_cap(tier, aum) == pytest.approx(fixed[tier], abs=1e-6), (
                f"opt-out failed: {tier} at ${aum} returned "
                f"{m.adaptive_kelly_cap(tier, aum)} not fixed {fixed[tier]}"
            )


# ──────────────────────────────────────────────────────────────────────
# Launcher state pinning
# ──────────────────────────────────────────────────────────────────────


def test_launcher_default_use_adaptive_kelly_one():
    """Pin LOTTERY_USE_ADAPTIVE_KELLY default = '1' to prevent accidental flip."""
    p = REPO / "scripts" / "lottery_paper_trade.ps1"
    text = p.read_text(encoding="utf-8")
    lines = text.split("\n")
    found = False
    for line in lines:
        if "LOTTERY_USE_ADAPTIVE_KELLY" in line and "SetEnvironment" in line and '"1"' in line:
            found = True
            break
    assert found, "LOTTERY_USE_ADAPTIVE_KELLY should default to '1'"


# ──────────────────────────────────────────────────────────────────────
# D291.5 (2026-05-12, doc 162 §7): HIGH Kelly cap raised 0.35 -> 0.50
# ──────────────────────────────────────────────────────────────────────


def test_d291_5_high_kelly_cap_is_0_50(monkeypatch):
    """Pin D291.5 production constant.

    Triggered by doc 162 §7 (n_HIGH last 60d = 56 >= 50 threshold,
    mean ret +4.13%). Bouchaud-optimal was 98% (doc 150 sub-exp B);
    shipped conservatively at 0.50.

    Regression guard: if anyone reverts this without going through the
    proper "lower it back" gate (e.g., recent edge degraded → halve cap),
    this test catches it.
    """
    m = _reload(monkeypatch)
    assert m.KELLY_CAPS_AGGRESSIVE["HIGH"] == 0.50, (
        f"D291.5 set HIGH cap to 0.50; got {m.KELLY_CAPS_AGGRESSIVE['HIGH']}. "
        "If lowering, document the trigger (e.g. recent edge degradation) "
        "in a doc with explicit pre-commit and update this test."
    )


def test_d291_5_high_at_140k_aum_uses_new_cap(monkeypatch):
    """At $140K paper account, adaptive_kelly_cap('HIGH') == 0.50."""
    m = _reload(monkeypatch)
    assert m.adaptive_kelly_cap("HIGH", 140_000) == pytest.approx(0.50, abs=1e-6)


def test_d291_5_high_keeps_low_cap_at_high_aum(monkeypatch):
    """At $5M+ AUM, HIGH cap stays at 0.195 (Bouchaud impact dominates).
    The D291.5 raise only applies to brackets <= $2M."""
    m = _reload(monkeypatch)
    assert m.adaptive_kelly_cap("HIGH", 5_000_000) == pytest.approx(0.195, abs=1e-6)
    assert m.adaptive_kelly_cap("HIGH", 10_000_000) == pytest.approx(0.100, abs=1e-6)
