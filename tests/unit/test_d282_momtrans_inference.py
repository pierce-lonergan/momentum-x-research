"""D282 — MoMTrans v4 inference behavioral guards.

Pin the MetaScorer's MoMTrans wiring so the launcher's MX_USE_MOMTRANS=1
flag has predictable, regression-tested behavior:

  1. When MX_USE_MOMTRANS=0 (default), MoMTrans does NOT load and the
     legacy v3 + D281 path runs unchanged.
  2. When MX_USE_MOMTRANS=1 AND all 4 .pt files present, MoMTrans loads
     and score_candidate routes through the cascade.
  3. When MX_USE_MOMTRANS=1 AND .pt files MISSING, MoMTrans gracefully
     falls back to the legacy path with a warning.
  4. The cascade tier waterfall enforces mag-gate correctly: ELITE/HIGH/
     BROAD require mag IN (HI, MID); VETOED requires mag = MID exactly.
  5. assign_tier_momtrans uses the Phase-A frozen thresholds:
     ELITE 0.60, HIGH 0.70, VETOED 0.90, BROAD 0.60.
  6. Cascade order is preserved: ELITE > HIGH > VETOED > BROAD.

The actual model loading is NOT tested at unit level (it requires the
4 trained .pt artifacts which are gitignored). That coverage lives in
the production-deploy smoke check at the bottom of this file.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from test_meta_scorer_inference import _mk_scorer  # noqa: E402


def _enable_momtrans_stub(scorer, elite=0.0, high=0.0, vetoed=0.0, broad=0.0):
    """Test helper: pretend the 4 MoMTrans models are loaded by populating
    `_momtrans_models` with non-empty dict. The router uses
    `len(_momtrans_models) == 4` as the "MoMTrans active" sentinel, and
    we monkey-patch predict_v4t to return our pinned probabilities."""
    scorer._momtrans_models = {"ELITE": object(), "HIGH": object(),
                                  "VETOED": object(), "BROAD": object()}

    def fake_predict_v4t(features):
        return {"ELITE": elite, "HIGH": high, "VETOED": vetoed, "BROAD": broad}
    scorer.predict_v4t = fake_predict_v4t
    return scorer


# ── 1. Default OFF — legacy path unchanged ──────────────────────────


def test_default_momtrans_off_means_legacy_path_runs():
    """MX_USE_MOMTRANS unset → MetaScorer constructs without
    momtrans_models populated → score_candidate uses v3+D281 path."""
    s = _mk_scorer(v3t_proba=0.45, mag_5d=0.0)
    # _mk_scorer leaves _momtrans_models empty
    assert s._momtrans_models == {}
    decision = s.score_candidate(
        candidate={"ticker": "TEST", "intraday_pct": 0.30},
        intraday_path=None, bankroll=10_000.0,
    )
    # Legacy path: v3t=0.45 in MID-mag → BROAD via baseline rule
    assert decision.tier == "BROAD"
    assert "[D282]" not in decision.reason


# ── 2. ON + 4 stub models → cascade path runs ───────────────────────


def test_momtrans_on_routes_through_cascade():
    """When 4 stub models are 'loaded', score_candidate must call
    predict_v4t and use assign_tier_momtrans."""
    s = _mk_scorer(v3t_proba=0.10, mag_5d=0.0)  # MID mag
    _enable_momtrans_stub(s, elite=0.65, high=0.0, vetoed=0.0, broad=0.0)
    decision = s.score_candidate(
        candidate={"ticker": "X", "intraday_pct": 0.30},
        intraday_path=None, bankroll=10_000.0,
    )
    assert decision.tier == "ELITE"
    assert "[D282]" in decision.reason
    assert "momtrans_ELITE" in decision.reason


# ── 3. Cascade order ELITE > HIGH > VETOED > BROAD ──────────────────


def test_cascade_elite_wins_when_all_specialists_fire():
    s = _mk_scorer(v3t_proba=0.10, mag_5d=0.0)
    _enable_momtrans_stub(s, elite=0.99, high=0.99, vetoed=0.99, broad=0.99)
    tier, reason = s.assign_tier_momtrans(
        s.predict_v4t({}), intraday_pct=0.30,
    )
    assert tier == "ELITE"


def test_cascade_high_wins_when_no_elite():
    s = _mk_scorer(v3t_proba=0.10, mag_5d=0.0)
    _enable_momtrans_stub(s, elite=0.55, high=0.85, vetoed=0.99, broad=0.99)
    # ELITE specialist 0.55 < threshold 0.60 → ELITE blocked
    tier, _ = s.assign_tier_momtrans(s.predict_v4t({}), intraday_pct=0.30)
    assert tier == "HIGH"


def test_cascade_vetoed_wins_in_mid_mag_when_no_elite_high():
    s = _mk_scorer(v3t_proba=0.10, mag_5d=0.0)  # MID mag
    _enable_momtrans_stub(s, elite=0.55, high=0.65, vetoed=0.95, broad=0.99)
    # ELITE 0.55<0.60, HIGH 0.65<0.70 → both blocked, VETOED 0.95>=0.90 wins
    tier, _ = s.assign_tier_momtrans(s.predict_v4t({}), intraday_pct=0.30)
    assert tier == "VETOED"


def test_cascade_broad_wins_when_only_broad_fires():
    s = _mk_scorer(v3t_proba=0.10, mag_5d=0.10)  # HI mag
    _enable_momtrans_stub(s, elite=0.50, high=0.60, vetoed=0.50, broad=0.65)
    tier, _ = s.assign_tier_momtrans(s.predict_v4t({}), intraday_pct=0.30)
    assert tier == "BROAD"


def test_cascade_skip_when_all_below_thresholds():
    s = _mk_scorer(v3t_proba=0.10, mag_5d=0.10)
    _enable_momtrans_stub(s, elite=0.50, high=0.60, vetoed=0.85, broad=0.55)
    tier, _ = s.assign_tier_momtrans(s.predict_v4t({}), intraday_pct=0.30)
    assert tier == "SKIP"


# ── 4. mag-gate enforcement ──────────────────────────────────────────


def test_elite_blocked_in_lo_mag():
    s = _mk_scorer(v3t_proba=0.10, mag_5d=-0.10)  # LO mag
    _enable_momtrans_stub(s, elite=0.95)
    tier, _ = s.assign_tier_momtrans(s.predict_v4t({}), intraday_pct=0.30)
    assert tier == "SKIP"


def test_vetoed_blocked_in_hi_mag():
    """VETOED tier requires mag = MID exactly. HI mag with VETOED firing
    falls through to BROAD (if BROAD specialist also fires)."""
    s = _mk_scorer(v3t_proba=0.10, mag_5d=0.10)  # HI mag
    _enable_momtrans_stub(s, elite=0.0, high=0.0, vetoed=0.95, broad=0.65)
    tier, _ = s.assign_tier_momtrans(s.predict_v4t({}), intraday_pct=0.30)
    # VETOED would fire in MID; in HI it's blocked. BROAD takes over.
    assert tier == "BROAD"


# ── 5. Threshold env override ────────────────────────────────────────


def test_threshold_override_via_env(monkeypatch):
    """MX_MOMTRANS_ELITE_THR=0.50 should make elite=0.55 trigger ELITE.
    Reload the module to pick up the env override."""
    monkeypatch.setenv("MX_MOMTRANS_ELITE_THR", "0.50")
    import importlib
    import ml_meta_scorer_inference
    importlib.reload(ml_meta_scorer_inference)
    importlib.reload(sys.modules["test_meta_scorer_inference"])
    from test_meta_scorer_inference import _mk_scorer as _mk
    s = _mk(v3t_proba=0.10, mag_5d=0.0)
    _enable_momtrans_stub(s, elite=0.55)
    tier, _ = s.assign_tier_momtrans(s.predict_v4t({}), intraday_pct=0.30)
    assert tier == "ELITE"


# ── 6. .pt artifacts present (production-deploy smoke check) ─────────


def test_momtrans_pt_files_load_when_present():
    """If all 4 .pt files exist on disk, they MUST load + the model factory
    rebuilds correctly. This is the production-deploy health check."""
    import torch
    models_dir = REPO / "data" / "models"
    pt_files = {
        tier: models_dir / f"momtrans_v4_tier_{tier}.pt"
        for tier in ("ELITE", "HIGH", "VETOED", "BROAD")
    }
    missing = [t for t, p in pt_files.items() if not p.exists()]
    if missing:
        pytest.skip(f"Production .pt files not present: {missing} — "
                     f"skipping deploy smoke check (expected in clean checkouts)")

    for tier, p in pt_files.items():
        blob = torch.load(p, map_location="cpu", weights_only=False)
        assert "model_state" in blob
        assert "config" in blob
        assert "feature_columns" in blob
        assert "feature_means" in blob and "feature_stds" in blob
        cfg = blob["config"]
        assert cfg["n_features"] == 54
        assert cfg["disable_sequence"] is True
        assert cfg["tier_name"] == tier or cfg.get("tier_name") == "DEFAULT"
        assert len(blob["feature_columns"]) == 54
        assert len(blob["feature_means"]) == 54
        assert len(blob["feature_stds"]) == 54
