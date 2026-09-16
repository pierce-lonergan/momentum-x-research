"""Unit tests for ml_meta_scorer_inference.

Validates the meta-scorer tier waterfall, Kelly sizing, and end-to-end
score_candidate() against the session 109 production tier definitions.

Run:
    python scripts/test_meta_scorer_inference.py
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from ml_meta_scorer_inference import (  # noqa: E402
    KELLY_CAPS, TIER_EW_EL_PCT,
    classify_mag, MetaScorer, MetaDecision,
)


class _StubBaseLearner:
    """Mimics sklearn predict_proba shape."""
    def __init__(self, p: float):
        self.p = p
    def predict_proba(self, X):
        n = len(X)
        return np.column_stack([np.full(n, 1 - self.p), np.full(n, self.p)])


class _StubMeta:
    """Returns the avg of the input columns (each input is a learner's P(1))."""
    def predict_proba(self, P):
        avg = P.mean(axis=1)
        return np.column_stack([1 - avg, avg])


def _mk_scorer(v3t_proba: float, mag_5d: float, with_tcn: bool = True,
                feat_cols=None) -> MetaScorer:
    feat_cols = feat_cols or ["log_open", "intraday_pct"]
    artifacts = {
        "feature_columns": feat_cols,
        "conformal_threshold": 0.20,
        "base_learners": {
            "xgb": _StubBaseLearner(v3t_proba),
            "lgbm": _StubBaseLearner(v3t_proba),
        },
        "meta_learner": _StubMeta(),
    }
    tcn_state = None
    if with_tcn:
        # Real TCN state; not used in tests that override predict_tcn
        tcn_state = {
            "state_dict": {},
            "n_features": 6, "channels": (16,), "kernel_size": 3,
            "dropout": 0.2, "seq_len": 30,
        }
    # Bypass _build_tcn_from_state by injecting None and stubbing predict_tcn
    scorer = MetaScorer.__new__(MetaScorer)
    scorer.v3t = artifacts
    scorer.feature_columns = feat_cols
    scorer.conformal_threshold = 0.20
    scorer.tcn_state = tcn_state
    scorer.tcn_model = "stub" if with_tcn else None
    # s125 hybrid ELITE: tests don't use it; set to None
    scorer.v3_default = None
    # D281 tiered cascade: tests opt-in via _enable_tier_specialists() helper
    scorer.tier_specialists = {}
    scorer._last_specialist_probas = {}
    # D282 MoMTrans: tests opt out by leaving these empty (legacy v3 path runs)
    scorer.momtrans_artifacts = {}
    scorer._momtrans_models = {}
    scorer._momtrans_feature_columns = []
    scorer._momtrans_means = []
    scorer._momtrans_stds = []
    scorer.mag_5d = mag_5d
    scorer.mag_label = classify_mag(mag_5d)
    return scorer


def _enable_tier_specialists(scorer: MetaScorer,
                                elite_p: float = 0.0, high_p: float = 0.0,
                                vetoed_p: float = 0.0, broad_p: float = 0.0
                                ) -> MetaScorer:
    """Test helper: prime the per-call specialist-proba cache so assign_tier
    sees the values predict_v3t would normally compute. Use to exercise the
    D281 UNION cascade without going through actual specialist models."""
    scorer.tier_specialists = {
        "ELITE": {}, "HIGH": {}, "VETOED": {}, "BROAD": {},
    }
    scorer._last_specialist_probas = {
        "ELITE": elite_p, "HIGH": high_p,
        "VETOED": vetoed_p, "BROAD": broad_p,
    }
    return scorer


class TestClassifyMag(unittest.TestCase):
    def test_hi(self):
        self.assertEqual(classify_mag(0.10), "HI")
        self.assertEqual(classify_mag(0.05001), "HI")

    def test_mid(self):
        self.assertEqual(classify_mag(0.0), "MID")
        self.assertEqual(classify_mag(0.05), "MID")
        self.assertEqual(classify_mag(-0.05), "MID")

    def test_lo(self):
        self.assertEqual(classify_mag(-0.10), "LO")
        self.assertEqual(classify_mag(-0.05001), "LO")


class TestAssignTier(unittest.TestCase):
    def test_elite_in_hi_mag(self):
        s = _mk_scorer(v3t_proba=0.65, mag_5d=0.10)
        tier, reason = s.assign_tier(0.65, tcn_proba=0.5)
        self.assertEqual(tier, "ELITE")

    def test_elite_in_mid_mag(self):
        s = _mk_scorer(v3t_proba=0.65, mag_5d=0.0)
        tier, _ = s.assign_tier(0.65, tcn_proba=0.5)
        self.assertEqual(tier, "ELITE")

    def test_elite_blocked_in_lo_mag(self):
        s = _mk_scorer(v3t_proba=0.65, mag_5d=-0.10)
        tier, _ = s.assign_tier(0.65, tcn_proba=0.5)
        # Falls through to SKIP because mag is LO
        self.assertEqual(tier, "SKIP")

    def test_high_in_mid_mag(self):
        s = _mk_scorer(v3t_proba=0.55, mag_5d=0.0)
        tier, _ = s.assign_tier(0.55, tcn_proba=0.5)
        self.assertEqual(tier, "HIGH")

    def test_vetoed_requires_mid_mag_and_low_tcn(self):
        s = _mk_scorer(v3t_proba=0.35, mag_5d=0.0)
        tier, reason = s.assign_tier(0.35, tcn_proba=0.20)
        self.assertEqual(tier, "VETOED")
        self.assertIn("mag=MID", reason)

    def test_vetoed_blocked_in_hi_mag(self):
        s = _mk_scorer(v3t_proba=0.35, mag_5d=0.10)
        tier, _ = s.assign_tier(0.35, tcn_proba=0.20)
        # mag=HI hits BROAD before VETOED check
        self.assertEqual(tier, "BROAD")

    def test_vetoed_blocked_when_tcn_high(self):
        s = _mk_scorer(v3t_proba=0.35, mag_5d=0.0)
        tier, _ = s.assign_tier(0.35, tcn_proba=0.50)
        # Falls through to BROAD (mag=MID, but TCN not low)
        self.assertEqual(tier, "BROAD")

    def test_broad_in_hi_mag(self):
        s = _mk_scorer(v3t_proba=0.35, mag_5d=0.10)
        tier, _ = s.assign_tier(0.35, tcn_proba=None)
        self.assertEqual(tier, "BROAD")

    def test_skip_below_threshold(self):
        s = _mk_scorer(v3t_proba=0.20, mag_5d=0.10)
        tier, _ = s.assign_tier(0.20, tcn_proba=None)
        self.assertEqual(tier, "SKIP")

    def test_skip_lo_mag_even_high_v3t(self):
        s = _mk_scorer(v3t_proba=0.55, mag_5d=-0.10)
        tier, _ = s.assign_tier(0.55, tcn_proba=None)
        self.assertEqual(tier, "SKIP")


class TestVetoedRuleD(unittest.TestCase):
    """VETOED rule D (s115 ablation): v3t>=0.30 AND mag=MID AND intraday_pct < p25.

    Activated by MX_VETOED_RULE=D env. Mirrors rule E test patterns.
    """
    def setUp(self):
        # Patch the module-level VETOED_RULE to "D" for these tests.
        # Use INTRA_P25_THRESHOLD = 0.3608 (s115 frozen value).
        import importlib
        import ml_meta_scorer_inference as msi
        self._orig_rule = msi.VETOED_RULE
        self._orig_thr = msi.INTRA_P25_THRESHOLD
        msi.VETOED_RULE = "D"
        msi.INTRA_P25_THRESHOLD = 0.3608

    def tearDown(self):
        import ml_meta_scorer_inference as msi
        msi.VETOED_RULE = self._orig_rule
        msi.INTRA_P25_THRESHOLD = self._orig_thr

    def test_d_fires_when_intra_below_p25(self):
        s = _mk_scorer(v3t_proba=0.35, mag_5d=0.0)
        tier, reason = s.assign_tier(0.35, tcn_proba=None,
                                       intraday_pct=0.20)  # 0.20 < 0.3608
        self.assertEqual(tier, "VETOED")
        self.assertIn("[rule D]", reason)
        self.assertIn("intraday_pct", reason)

    def test_d_blocked_when_intra_above_p25(self):
        s = _mk_scorer(v3t_proba=0.35, mag_5d=0.0)
        tier, _ = s.assign_tier(0.35, tcn_proba=None,
                                  intraday_pct=0.50)  # 0.50 > 0.3608
        # Falls through to BROAD
        self.assertEqual(tier, "BROAD")

    def test_d_blocked_in_hi_mag_even_low_intra(self):
        s = _mk_scorer(v3t_proba=0.35, mag_5d=0.10)
        tier, _ = s.assign_tier(0.35, tcn_proba=None,
                                  intraday_pct=0.20)
        # mag=HI hits BROAD before VETOED check
        self.assertEqual(tier, "BROAD")

    def test_d_skipped_when_intra_missing(self):
        s = _mk_scorer(v3t_proba=0.35, mag_5d=0.0)
        tier, _ = s.assign_tier(0.35, tcn_proba=None, intraday_pct=None)
        # Falls through to BROAD when intraday_pct unavailable
        self.assertEqual(tier, "BROAD")

    def test_d_ignores_tcn(self):
        # Under rule D, even if TCN is high, VETOED still fires on intra<p25
        s = _mk_scorer(v3t_proba=0.35, mag_5d=0.0)
        tier, _ = s.assign_tier(0.35, tcn_proba=0.99,  # high TCN — irrelevant
                                  intraday_pct=0.20)
        self.assertEqual(tier, "VETOED")


class TestComputeKelly(unittest.TestCase):
    def test_skip_returns_zero(self):
        s = _mk_scorer(v3t_proba=0.10, mag_5d=0.0)
        self.assertEqual(s.compute_kelly("SKIP", 0.10, 0.5), 0.0)

    def test_elite_caps_at_5pct(self):
        s = _mk_scorer(v3t_proba=0.95, mag_5d=0.10)
        # Very high p + tight conformal width → kelly should cap at 0.05
        self.assertEqual(s.compute_kelly("ELITE", 0.95, 0.0), KELLY_CAPS["ELITE"])

    def test_high_caps_at_3pct(self):
        s = _mk_scorer(v3t_proba=0.95, mag_5d=0.10)
        self.assertEqual(s.compute_kelly("HIGH", 0.95, 0.0), KELLY_CAPS["HIGH"])

    def test_kelly_zero_at_breakeven_p(self):
        # At the breakeven probability p* = 1/(b+1), Kelly = 0
        s = _mk_scorer(v3t_proba=0.5, mag_5d=0.0)
        ew, el = TIER_EW_EL_PCT["BROAD"]
        b = abs(ew / el)
        breakeven_p = 1.0 / (b + 1)
        kelly = s.compute_kelly("BROAD", breakeven_p, 0.5)
        self.assertAlmostEqual(kelly, 0.0, places=4)

    def test_kelly_modulated_by_conformal_width(self):
        # Tighter width (smaller) should give larger Kelly.
        # The production caps are very tight relative to natural Kelly, so we
        # patch KELLY_CAPS to a large value to verify the formula's modulation.
        s = _mk_scorer(v3t_proba=0.55, mag_5d=0.0)
        with patch("ml_meta_scorer_inference.KELLY_CAPS", {**KELLY_CAPS, "HIGH": 1.0}):
            k_tight = s.compute_kelly("HIGH", 0.55, 0.1)
            k_wide = s.compute_kelly("HIGH", 0.55, 0.9)
        self.assertGreater(k_tight, k_wide)


class TestScoreCandidate(unittest.TestCase):
    def test_skip_candidate_has_zero_notional(self):
        s = _mk_scorer(v3t_proba=0.10, mag_5d=0.0)
        decision = s.score_candidate(
            candidate={"ticker": "X", "log_open": 0, "intraday_pct": 0},
            intraday_path=None, bankroll=10_000.0,
        )
        self.assertEqual(decision.tier, "SKIP")
        self.assertEqual(decision.kelly_frac, 0.0)
        self.assertEqual(decision.notional_usd, 0.0)

    def test_high_conviction_pick_sized_at_cap(self):
        s = _mk_scorer(v3t_proba=0.95, mag_5d=0.0)
        # Override predict_v3t to avoid sklearn stub edge case
        s.predict_v3t = lambda f: (0.95, 0.05)
        s.predict_tcn = lambda p: None
        decision = s.score_candidate(
            candidate={"ticker": "Y"}, intraday_path=None, bankroll=10_000.0,
        )
        self.assertEqual(decision.tier, "ELITE")
        # 5% cap × $10k = $500
        self.assertEqual(decision.notional_usd, 500.0)
        self.assertEqual(decision.mag_label, "MID")

    def test_vetoed_path_with_low_tcn(self):
        s = _mk_scorer(v3t_proba=0.40, mag_5d=0.0)
        s.predict_v3t = lambda f: (0.40, 0.50)
        s.predict_tcn = lambda p: 0.20  # TCN-veto active
        decision = s.score_candidate(
            candidate={"ticker": "Z"},
            intraday_path=np.zeros((6, 30), dtype=np.float32),
            bankroll=10_000.0,
        )
        self.assertEqual(decision.tier, "VETOED")
        self.assertEqual(decision.tcn_proba, 0.20)
        # Notional should be > 0 but ≤ 2% × $10k = $200
        self.assertGreater(decision.notional_usd, 0.0)
        self.assertLessEqual(decision.notional_usd, 200.0)

    def test_returns_metadecision_dataclass(self):
        s = _mk_scorer(v3t_proba=0.10, mag_5d=0.0)
        decision = s.score_candidate({"ticker": "T"}, None, 10_000.0)
        self.assertIsInstance(decision, MetaDecision)


class TestKellyCapsConsistency(unittest.TestCase):
    """Sanity: caps must mirror scripts/ml_meta_scorer.py."""
    def test_all_tiers_have_caps(self):
        for tier in ("ELITE", "HIGH", "VETOED", "BROAD", "SKIP"):
            self.assertIn(tier, KELLY_CAPS)

    def test_caps_are_monotonic(self):
        self.assertGreater(KELLY_CAPS["ELITE"], KELLY_CAPS["HIGH"])
        self.assertGreater(KELLY_CAPS["HIGH"], KELLY_CAPS["VETOED"])
        self.assertGreater(KELLY_CAPS["VETOED"], KELLY_CAPS["BROAD"])
        self.assertGreater(KELLY_CAPS["BROAD"], 0.0)
        self.assertEqual(KELLY_CAPS["SKIP"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
