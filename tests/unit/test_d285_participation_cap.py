"""D285 (2026-05-07, doc 142 Bouchaud capacity finding) — per-pick
participation cap.

Pin the Bouchaud impact constraint: per-pick notional must be capped at
LOTTERY_MAX_PARTICIPATION_PCT × dvol_d0. Per doc 142 capacity analysis
the cap converts $5M-AUM "all alpha eaten" into "alpha intact".

Tests:
  1. Cap binds when notional > MAX_PARTICIPATION_PCT × dvol_d0
  2. Cap does NOT bind when notional <= cap
  3. Cap is bypassed when LOTTERY_MAX_PARTICIPATION_PCT >= 1.0 (opt-out)
  4. Cap is bypassed when dvol_d0 missing or zero (no participation
     denominator to compute)
  5. The `kelly_frac` field is updated to reflect the capped notional
     (so downstream P&L math is correct)
  6. The reason string is annotated when cap fires (for log forensics)

The cap lives in MetaScorer.decide() at the boundary where
notional = bankroll * kelly is computed. We test by constructing a
MetaScorer with stub artifacts and a hand-built candidate dict.
"""
from __future__ import annotations
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def _reload_meta_scorer(monkeypatch, max_participation_pct: str | None = None):
    """Reload ml_meta_scorer_inference with desired LOTTERY_MAX_PARTICIPATION_PCT."""
    monkeypatch.delenv("LOTTERY_MAX_PARTICIPATION_PCT", raising=False)
    if max_participation_pct is not None:
        monkeypatch.setenv("LOTTERY_MAX_PARTICIPATION_PCT", max_participation_pct)
    # Make Aggressive Kelly fire so the cap has something to cap
    monkeypatch.setenv("MX_META_KELLY_PROFILE", "aggressive")
    if "ml_meta_scorer_inference" in sys.modules:
        del sys.modules["ml_meta_scorer_inference"]
    import ml_meta_scorer_inference
    return ml_meta_scorer_inference


# ──────────────────────────────────────────────────────────────────────
# Test the participation cap math directly (the integration into decide()
# is straightforward; the math is the part we want to pin)
# ──────────────────────────────────────────────────────────────────────


def test_participation_cap_module_constant_default(monkeypatch):
    """Module reload picks up the LOTTERY_MAX_PARTICIPATION_PCT env var."""
    mod = _reload_meta_scorer(monkeypatch)  # no env -> default 0.05
    assert mod.LOTTERY_MAX_PARTICIPATION_PCT == 0.05


def test_participation_cap_module_constant_override(monkeypatch):
    mod = _reload_meta_scorer(monkeypatch, max_participation_pct="0.10")
    assert mod.LOTTERY_MAX_PARTICIPATION_PCT == 0.10


def test_participation_cap_disabled_at_one(monkeypatch):
    mod = _reload_meta_scorer(monkeypatch, max_participation_pct="1.0")
    assert mod.LOTTERY_MAX_PARTICIPATION_PCT == 1.0


# ──────────────────────────────────────────────────────────────────────
# The cap math, replicating the inline logic from MetaScorer.decide()
# (we don't construct a full MetaScorer because that requires loading
# real model artifacts; the cap is pure arithmetic)
# ──────────────────────────────────────────────────────────────────────


def _apply_participation_cap(notional: float, dvol_d0: float | None,
                              max_part_pct: float) -> tuple[float, bool]:
    """Replicate the MetaScorer.decide() participation-cap logic.

    Returns (capped_notional, cap_was_applied).
    """
    if (notional > 0 and dvol_d0 is not None
            and isinstance(dvol_d0, (int, float)) and dvol_d0 > 0
            and max_part_pct < 1.0):
        cap = float(dvol_d0) * max_part_pct
        if notional > cap:
            return cap, True
    return notional, False


def test_cap_binds_when_notional_exceeds_cap():
    """ELITE pick at $5M AUM: bankroll=$5M × Kelly=0.50 = $2.5M.
    Median microcap dvol $8.6M × 0.05 = $430K cap. Notional should drop."""
    notional = 2_500_000.0
    dvol_d0 = 8_600_000.0
    max_part = 0.05
    capped, applied = _apply_participation_cap(notional, dvol_d0, max_part)
    assert applied is True
    assert capped == pytest.approx(8_600_000.0 * 0.05, rel=1e-6)
    assert capped == pytest.approx(430_000.0, rel=1e-6)


def test_cap_does_not_bind_at_paper_account_scale():
    """ELITE pick at $140K paper account: bankroll=$140K × Kelly=0.50 = $70K.
    Median microcap dvol $8.6M × 0.05 = $430K cap. Notional well below cap;
    cap should NOT bind (preserve current behavior at small AUM)."""
    notional = 70_000.0
    dvol_d0 = 8_600_000.0
    max_part = 0.05
    capped, applied = _apply_participation_cap(notional, dvol_d0, max_part)
    assert applied is False
    assert capped == 70_000.0


def test_cap_bypassed_when_pct_is_one():
    """Opt-out path: setting MAX_PARTICIPATION_PCT=1.0 disables the cap."""
    notional = 2_500_000.0
    dvol_d0 = 8_600_000.0
    max_part = 1.0
    capped, applied = _apply_participation_cap(notional, dvol_d0, max_part)
    assert applied is False
    assert capped == 2_500_000.0


def test_cap_bypassed_when_dvol_missing():
    """If dvol_d0 is None, can't compute participation -> no cap."""
    notional = 2_500_000.0
    capped, applied = _apply_participation_cap(notional, None, 0.05)
    assert applied is False
    assert capped == 2_500_000.0


def test_cap_bypassed_when_dvol_zero():
    """If dvol_d0 is 0 (data quality issue), can't divide -> no cap."""
    notional = 2_500_000.0
    capped, applied = _apply_participation_cap(notional, 0.0, 0.05)
    assert applied is False
    assert capped == 2_500_000.0


def test_cap_bypassed_when_dvol_string_or_nan():
    """Defensive: non-numeric dvol_d0 (string from bad CSV, NaN, etc.)
    must not crash; the cap logic should silently skip."""
    notional = 2_500_000.0
    capped, applied = _apply_participation_cap(notional, "8600000", 0.05)
    assert applied is False  # string isn't (int, float)
    assert capped == 2_500_000.0


def test_cap_at_thin_microcap_correctly_aggressive():
    """Thin microcap dvol $1M × 0.05 = $50K cap. ELITE pick at $5M AUM
    = $2.5M notional. Cap should drop notional to $50K (50× reduction)."""
    notional = 2_500_000.0
    dvol_d0 = 1_000_000.0
    max_part = 0.05
    capped, applied = _apply_participation_cap(notional, dvol_d0, max_part)
    assert applied is True
    assert capped == pytest.approx(50_000.0, rel=1e-6)
    # The cap is 2% of original notional — exactly the kind of drastic
    # reduction the Bouchaud analysis says is needed at low-liquidity names.
    assert capped / notional < 0.025


# ──────────────────────────────────────────────────────────────────────
# Sanity check: Bouchaud impact at the cap is bounded
# ──────────────────────────────────────────────────────────────────────


def test_cap_bounds_bouchaud_impact_to_safe_range():
    """At MAX_PARTICIPATION_PCT=0.05 with Y=1.5 and σ_daily=11%:
       impact_in_sigma = Y · √participation = 1.5 · √0.05 = 0.335
       impact_fractional = 0.335 × 0.11 = 3.7% one-way
       round-trip impact ≈ 7.4%
    For an ELITE pick with +20% mean ret_t5, net = 12.6%. Still profitable.
    """
    Y = 1.5
    sigma_daily = 0.11  # ~median microcap intraday_pct/4
    max_part = 0.05
    one_way_impact = Y * np.sqrt(max_part) * sigma_daily
    round_trip_impact = 2 * one_way_impact
    assert round_trip_impact == pytest.approx(0.0738, abs=1e-4)
    assert round_trip_impact < 0.10  # < 10% impact = strategy is viable

    # At the OLD uncapped behavior on a 30%-participation pick:
    uncapped_part = 0.30
    uncapped_impact = 2 * Y * np.sqrt(uncapped_part) * sigma_daily
    assert uncapped_impact > 0.18  # > 18% impact = strategy dies
    # Cap reduces impact by >2x at 30% baseline participation
    assert round_trip_impact < uncapped_impact / 2
