"""D281 (2026-05-05) — Cohort-specialized cascade behavioral regression guards.

Pin the s109 + s124 + s125 architectural progression. The naive cohort
cascade (each specialist >= 0.30) UNDERPERFORMED baseline by $3,967 (the
verdict step would have HOLD_BASELINE'd it). The grid-searched UNION
strategy (baseline-rule OR specialist-at-tier-threshold) delivers
+$1,104 vs production with thresholds (ELITE 0.40, HIGH 0.40,
VETOED 0.40, BROAD 0.50).

This test suite locks in the UNION cascade behavior so tomorrow's
production deploy has a regression guard:

  1. Backward compat: when MX_TIERED_LEARNING is OFF (no specialists),
     the original tier waterfall is unchanged.
  2. ELITE specialist alone CAN trigger ELITE tier even when v3t < 0.60
     (the headline lift mechanism).
  3. HIGH specialist alone CAN trigger HIGH tier when v3t < 0.50.
  4. Specialist below threshold does NOT change the tier.
  5. Specialists do NOT bypass the magnetization (HI|MID) gate.
  6. Tier cascade order is preserved: ELITE > HIGH > VETOED > BROAD.
  7. UNION reason string makes the trigger source visible to operators.

The test infrastructure (_enable_tier_specialists) primes the
per-call specialist-proba cache directly so we can drive the tier-
assignment logic without loading actual specialist .pkl files.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from test_meta_scorer_inference import _mk_scorer, _enable_tier_specialists  # noqa: E402


# ── 1. Backward compat: tiered learning OFF ──────────────────────────


def test_no_specialists_loaded_means_baseline_waterfall_unchanged():
    """When MX_TIERED_LEARNING is OFF, no specialists are loaded; the tier
    waterfall behaves exactly as it did pre-D281."""
    s = _mk_scorer(v3t_proba=0.45, mag_5d=0.0)  # mid-mag, sub-HIGH
    # No _enable_tier_specialists call → tier_specialists empty
    tier, reason = s.assign_tier(v3t_proba=0.45, tcn_proba=None,
                                    intraday_pct=0.30)
    # Without specialists, v3t=0.45 in MID-mag is BROAD (>=0.30 only)
    assert tier == "BROAD"
    assert "[D281 cascade]" not in reason


# ── 2. ELITE specialist alone triggers ELITE (the headline lift) ────


def test_elite_specialist_triggers_elite_when_baseline_fails():
    """Pin the +$1,468 ELITE-tier lift mechanism. v3t < 0.60 (so baseline
    rule does NOT match), but ELITE specialist >= 0.40 (default threshold)
    in HI/MID mag → tier = ELITE."""
    s = _mk_scorer(v3t_proba=0.45, mag_5d=0.0)
    _enable_tier_specialists(s, elite_p=0.42)  # specialist > 0.40 threshold
    tier, reason = s.assign_tier(v3t_proba=0.45, tcn_proba=None,
                                    intraday_pct=0.30)
    assert tier == "ELITE"
    assert "specialist_ELITE" in reason
    assert "[D281 cascade]" in reason


def test_elite_specialist_below_threshold_does_not_trigger():
    """Defensive: specialist below the configured threshold must NOT trigger
    the tier. Otherwise we'd over-produce ELITE picks and lose the precision."""
    s = _mk_scorer(v3t_proba=0.45, mag_5d=0.0)
    _enable_tier_specialists(s, elite_p=0.39)  # just below 0.40 default
    tier, _ = s.assign_tier(v3t_proba=0.45, tcn_proba=None,
                                intraday_pct=0.30)
    # Should fall through to BROAD (v3t=0.45 >= 0.30 in MID mag)
    assert tier != "ELITE"


def test_elite_specialist_blocked_in_lo_mag():
    """Specialists do NOT bypass the magnetization gate. ELITE requires
    mag IN (HI, MID) regardless of which signal triggered it."""
    s = _mk_scorer(v3t_proba=0.45, mag_5d=-0.10)  # LO mag
    _enable_tier_specialists(s, elite_p=0.95)  # very high specialist
    tier, _ = s.assign_tier(v3t_proba=0.45, tcn_proba=None,
                                intraday_pct=0.30)
    assert tier == "SKIP", "LO mag must block all tiers including specialist-driven"


# ── 3. HIGH specialist triggers HIGH ──────────────────────────────────


def test_high_specialist_triggers_high_when_v3t_sub_baseline():
    """v3t = 0.40 (below baseline HIGH 0.50) but HIGH specialist >= 0.40
    in HI/MID mag → tier = HIGH."""
    s = _mk_scorer(v3t_proba=0.40, mag_5d=0.0)
    _enable_tier_specialists(s, high_p=0.50)
    tier, reason = s.assign_tier(v3t_proba=0.40, tcn_proba=None,
                                    intraday_pct=0.30)
    assert tier == "HIGH"
    assert "specialist_HIGH" in reason


def test_baseline_high_takes_precedence_over_specialist():
    """When BOTH baseline AND specialist match, the baseline reason wins
    (it's listed first in the OR). The ASSIGNED TIER is the same; this is
    a documentation-clarity test to keep operator log lines stable."""
    s = _mk_scorer(v3t_proba=0.55, mag_5d=0.0)  # baseline HIGH (>=0.50)
    _enable_tier_specialists(s, high_p=0.99)
    tier, reason = s.assign_tier(v3t_proba=0.55, tcn_proba=None,
                                    intraday_pct=0.30)
    assert tier == "HIGH"
    assert "v3t>=0.50" in reason  # baseline reason wins
    assert "[D281 cascade]" not in reason


# ── 4. VETOED specialist (rule-D path) ───────────────────────────────


def test_vetoed_specialist_in_mid_mag(monkeypatch):
    """VETOED specialist >= 0.40 in MID mag → tier = VETOED, even when
    baseline rule D's intraday_pct < p25 condition is not met."""
    s = _mk_scorer(v3t_proba=0.25, mag_5d=0.0)  # below baseline 0.30
    _enable_tier_specialists(s, vetoed_p=0.45)
    tier, _ = s.assign_tier(v3t_proba=0.25, tcn_proba=None,
                                intraday_pct=0.50)  # above p25 → baseline blocked
    assert tier == "VETOED"


def test_vetoed_specialist_blocked_in_hi_mag():
    """VETOED tier requires mag == MID exactly; HI mag falls through to BROAD."""
    s = _mk_scorer(v3t_proba=0.45, mag_5d=0.10)  # HI mag
    _enable_tier_specialists(s, vetoed_p=0.99, broad_p=0.99)
    tier, _ = s.assign_tier(v3t_proba=0.45, tcn_proba=None,
                                intraday_pct=0.30)
    # HI mag + v3t>=0.30 → BROAD via baseline; or BROAD specialist if baseline failed
    assert tier in ("BROAD",)


# ── 5. BROAD specialist (highest threshold 0.50) ─────────────────────


def test_broad_specialist_triggers_broad_when_v3t_below_threshold():
    """v3t < 0.30 (below baseline) but BROAD specialist >= 0.50 in HI/MID
    mag → tier = BROAD."""
    s = _mk_scorer(v3t_proba=0.20, mag_5d=0.10)
    _enable_tier_specialists(s, broad_p=0.55)
    tier, reason = s.assign_tier(v3t_proba=0.20, tcn_proba=None,
                                    intraday_pct=0.30)
    assert tier == "BROAD"
    assert "specialist_BROAD" in reason


def test_broad_specialist_below_threshold_skips():
    """Defensive: BROAD specialist below 0.50 default threshold → SKIP,
    not BROAD. The threshold IS the precision gate."""
    s = _mk_scorer(v3t_proba=0.15, mag_5d=0.10)
    _enable_tier_specialists(s, broad_p=0.45)  # below 0.50
    tier, _ = s.assign_tier(v3t_proba=0.15, tcn_proba=None,
                                intraday_pct=0.30)
    assert tier == "SKIP"


# ── 6. Cascade-order preservation ────────────────────────────────────


def test_elite_specialist_takes_precedence_over_high_specialist():
    """When both ELITE and HIGH specialists fire, ELITE wins (cascade order)."""
    s = _mk_scorer(v3t_proba=0.30, mag_5d=0.0)
    _enable_tier_specialists(s, elite_p=0.45, high_p=0.99)
    tier, _ = s.assign_tier(v3t_proba=0.30, tcn_proba=None,
                                intraday_pct=0.30)
    assert tier == "ELITE"


def test_high_specialist_takes_precedence_over_vetoed_specialist():
    """In MID mag with both HIGH+VETOED specialists firing, HIGH wins."""
    s = _mk_scorer(v3t_proba=0.20, mag_5d=0.0)  # MID mag
    _enable_tier_specialists(s, high_p=0.45, vetoed_p=0.99)
    tier, _ = s.assign_tier(v3t_proba=0.20, tcn_proba=None,
                                intraday_pct=0.30)
    assert tier == "HIGH"


def test_vetoed_specialist_takes_precedence_over_broad_specialist():
    """In MID mag with VETOED+BROAD firing, VETOED wins."""
    s = _mk_scorer(v3t_proba=0.20, mag_5d=0.0)
    _enable_tier_specialists(s, vetoed_p=0.45, broad_p=0.99)
    tier, _ = s.assign_tier(v3t_proba=0.20, tcn_proba=None,
                                intraday_pct=0.30)
    assert tier == "VETOED"


# ── 7. SKIP path with diagnostic info ────────────────────────────────


def test_skip_includes_specialist_probas_in_reason_when_loaded():
    """When tiered learning is on but no tier matched, the SKIP reason
    must include the specialist proba dict for diagnostic visibility."""
    s = _mk_scorer(v3t_proba=0.10, mag_5d=0.10)
    _enable_tier_specialists(s, elite_p=0.05, high_p=0.10,
                                  vetoed_p=0.05, broad_p=0.10)
    tier, reason = s.assign_tier(v3t_proba=0.10, tcn_proba=None,
                                      intraday_pct=0.30)
    assert tier == "SKIP"
    assert "specialists=" in reason


# ── 8. End-to-end: specialist threshold env-var override ─────────────


def test_specialist_threshold_env_var_override(monkeypatch):
    """MX_TIER_ELITE_THR=0.30 should make elite_p=0.31 trigger ELITE.
    Reload the module to pick up the new env var."""
    monkeypatch.setenv("MX_TIER_ELITE_THR", "0.30")
    import importlib
    import ml_meta_scorer_inference
    importlib.reload(ml_meta_scorer_inference)
    # Re-import after reload
    from test_meta_scorer_inference import _mk_scorer as _mk2
    from test_meta_scorer_inference import _enable_tier_specialists as _en2
    importlib.reload(sys.modules["test_meta_scorer_inference"])
    from test_meta_scorer_inference import _mk_scorer as _mk3
    from test_meta_scorer_inference import _enable_tier_specialists as _en3
    s = _mk3(v3t_proba=0.45, mag_5d=0.0)
    _en3(s, elite_p=0.31)  # would NOT trigger at default 0.40, WOULD at 0.30
    tier, _ = s.assign_tier(v3t_proba=0.45, tcn_proba=None,
                                intraday_pct=0.30)
    assert tier == "ELITE"


# ── 9. Loaded-from-disk artifact integrity ───────────────────────────


def test_specialists_artifact_files_exist_and_load():
    """The 4 specialist .pkl files must be present + loadable. Failure
    here means the trainer didn't run or the launcher would skip the
    cascade silently. This is the production-deployment health check."""
    import pickle
    models_dir = REPO / "data" / "models"
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
        p = models_dir / f"continuer_v2_v3_tier_{tier}.pkl"
        assert p.exists(), (
            f"D281 specialist {p.name} missing — run "
            f"scripts/ml_v3_tier_specialists_train.py to regenerate"
        )
        with open(p, "rb") as f:
            d = pickle.load(f)
        assert d.get("tier_name") == tier, (
            f"{p.name} tier_name={d.get('tier_name')!r}, expected {tier!r}"
        )
        assert "base_learners" in d
        assert "meta_learner" in d
        assert "feature_columns" in d
        assert len(d["feature_columns"]) == 54, (
            f"{tier} specialist trained on {len(d['feature_columns'])} features, "
            f"expected 54 (v3 feature set)"
        )
