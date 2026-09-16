"""doc 184: tests for the intraday-continuer feature/label contract.

The contract must be deterministic and identical at train + inference time (the #1
way ML shadows go wrong is train/serve feature skew).
"""
from __future__ import annotations

import math

from src.analysis import intraday_continuer as ic


def _row(**kw):
    base = {
        "gap_pct": 0.85, "rvol": 96.0, "mfcs": 0.6, "score": 0.67,
        "minutes_since_open": 5.0, "decision_price": 0.51,
        "float_shares": 6.3e6, "market_cap": 7e6, "gate": "D160_FALLER",
    }
    base.update(kw)
    return base


def test_extract_features_mapping():
    f = ic.extract_features(_row())
    assert set(f.keys()) == set(ic.FEATURE_NAMES)
    assert f["gap_pct"] == 0.85
    assert abs(f["rvol_log"] - math.log1p(96.0)) < 1e-9
    assert f["faller_score"] == 0.67
    assert f["is_d170"] == 0.0
    assert ic.extract_features(_row(gate="D170_ENTRY_DELAY"))["is_d170"] == 1.0
    # log transforms positive, finite
    assert f["log_float"] > 0 and f["log_mcap"] > 0


def test_extract_features_missing_defaults_zero():
    f = ic.extract_features({"ticker": "X", "gate": "D160_FALLER"})
    assert f["gap_pct"] == 0.0
    assert f["rvol_log"] == 0.0
    assert f["log_float"] == 0.0  # missing float -> 0, not -inf


def test_label_from_outcome():
    # continued: MFE >= run threshold and not deeply red
    assert ic.label_from_outcome({"filled": True, "mfe_pct": 0.12, "return_eod": 0.05}) == 1
    # faded: tiny MFE
    assert ic.label_from_outcome({"filled": True, "mfe_pct": 0.02, "return_eod": -0.08}) == 0
    # big MFE but fully reversed deep red -> not a capturable continuation
    assert ic.label_from_outcome({"filled": True, "mfe_pct": 0.20, "return_eod": -0.30}) == 0
    # unlabelable
    assert ic.label_from_outcome(None) is None
    assert ic.label_from_outcome({"filled": False}) is None
    assert ic.label_from_outcome({"filled": True, "mfe_pct": None}) is None


def test_features_vector_order():
    f = ic.extract_features(_row())
    v = ic.features_vector(f)
    assert len(v) == len(ic.FEATURE_NAMES)
    assert v[0] == f["gap_pct"]  # order matches FEATURE_NAMES


def test_load_missing_artifact_returns_none(tmp_path):
    assert ic.IntradayContinuer.load(tmp_path / "nope.pkl") is None
