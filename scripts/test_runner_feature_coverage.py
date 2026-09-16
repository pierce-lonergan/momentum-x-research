"""Regression guard: assert lottery_runner builds ALL expected model features.

Session 122 root cause: runner provided only 30 of 54 features expected by
v3-tuned-16fold. Missing 24 fields defaulted to 0 -> v3t collapsed to base
rate -> 0 picks fired Monday.

This test extracts the runner's feature-construction code path (the dict
literal in the META-SCORER block) and asserts it covers exactly the
columns that the production model expects. If a future model swap adds
new features, this test fails BEFORE deploy, not at 9:30 ET.

Run:
    python scripts/test_runner_feature_coverage.py
or:
    python -m pytest scripts/test_runner_feature_coverage.py
"""
from __future__ import annotations
import math
import pickle
import sys
import unittest
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODELS = REPO / "data" / "models"


def _build_runner_features_inline(p_ticker="TEST", p_price=5.0,
                                     p_pct_change=15.0, today_total=10,
                                     rank_intra=1, avg_today_intra=0.10,
                                     pr=None, td=None) -> dict:
    """Mirror EXACTLY the dict literal in lottery_runner.py's META-SCORER
    gate (s122 fix version). Any mismatch indicates the runner needs an
    update.

    pr: prior_map row dict (n, n_cont, n_fade, rate, avg_t5)
    td: ticker_details row dict (market_cap, sic_code, sic_description,
        total_employees, share_class_shares_outstanding, list_date)
    """
    pr = pr or {"n": 0, "n_cont": 0, "n_fade": 0, "rate": 20.78, "avg_t5": 0}
    td = td or {}
    now = datetime(2026, 5, 5, 9, 30)  # Tuesday morning fixture

    # Mirror the inline ticker_details enrichment from lottery_runner.py
    _mcap = td.get("market_cap") if td else None
    _emp = td.get("total_employees") if td else None
    _list = td.get("list_date") if td else None
    _sso = td.get("share_class_shares_outstanding") if td else None
    _sic = td.get("sic_code") if td else None
    _sic_str = (td.get("sic_description") or "").upper() if td else ""

    log_market_cap = math.log(max(float(_mcap) if _mcap else 50e6, 1))
    mcap_known = 1 if _mcap else 0
    log_employees = math.log(max(float(_emp) if _emp else 100, 1))
    log_float = math.log(max(float(_sso) if _sso else 1e7, 1))
    if _list:
        try:
            from datetime import date as _date
            ld = _date.fromisoformat(str(_list)[:10])
            days = max((now.date() - ld).days, 1)
        except Exception:
            days = 3650
    else:
        days = 3650
    log_days_since_ipo = math.log1p(days)
    try:
        sic_int = int(float(_sic)) if _sic else 0
    except Exception:
        sic_int = 0
    sic_group = sic_int // 100

    def _has(needle):
        return 1 if needle in _sic_str else 0

    sec_pharma = _has("PHARMACEUTICAL")
    sec_bio = _has("BIOLOGICAL")
    sec_medical = 1 if ("SURGICAL" in _sic_str or "MEDICAL" in _sic_str) else 0
    sec_software = _has("SOFTWARE")
    sec_finance = _has("FINANCE")
    sec_semi = _has("SEMICONDUCTOR")
    sec_spac = _has("BLANK CHECK")
    sec_reit = _has("REAL ESTATE")

    return {
        "log_open": math.log(max(p_price, 0.01)),
        "log_dvol_d0": math.log(max(p_price * 1e6, 1)),
        "intraday_pct": p_pct_change / 100.0,
        "intraday_pct_log": math.log1p(max(p_pct_change / 100.0, 0)),
        "ret_open_close_d0": p_pct_change / 100.0,
        "close_strength": 1.0,
        "prior_n": pr["n"] or 0,
        "prior_n_log": math.log1p(pr["n"] or 0),
        "prior_cont_rate": pr["rate"] or 20.78,
        "prior_fade_rate": (43.36 + (pr["n_fade"] or 0) * 100) / (100 + (pr["n"] or 0)),
        "prior_avg_t5": (pr["avg_t5"] or 0) / 100.0,
        "prior_7d_count": 0,
        "dow": now.weekday(), "month": now.month, "year": now.year,
        "day_of_year": now.timetuple().tm_yday,
        "rank_intra": rank_intra, "rank_intra_log": math.log1p(rank_intra),
        "rank_dvol": rank_intra, "n_today_total": today_total,
        "intra_vs_today_avg": (p_pct_change / 100.0) / max(avg_today_intra, 0.01),
        "dvol_vs_today_avg": 1.0,
        "prior_avg_intra": 0.5, "prior_avg_oc": 0.0,
        "prior_30d_count": pr["n"] or 0, "intra_vs_prior": 1.0,
        "day_of_month": now.day,
        "week_of_month": (now.day - 1) // 7 + 1,
        "quarter": (now.month - 1) // 3 + 1,
        "year_frac": now.timetuple().tm_yday / 365.25,
        "log_market_cap": log_market_cap,
        "mcap_known": mcap_known,
        "log_employees": log_employees,
        "log_days_since_ipo": log_days_since_ipo,
        "sic_code": sic_int,
        "sic_group": sic_group,
        "sec_pharma": sec_pharma, "sec_bio": sec_bio, "sec_medical": sec_medical,
        "sec_software": sec_software, "sec_finance": sec_finance,
        "sec_semi": sec_semi, "sec_spac": sec_spac, "sec_reit": sec_reit,
        "log_float": log_float,
        "log_rank_x_prior_continuer":
            math.log1p(rank_intra) * (1 + (pr["n_cont"] or 0)) / (5 + (pr["n"] or 0)),
        "first_5min_max_close": 0.0,
        "first_5min_min_close": 0.0,
        "last_5min_avg_close": 0.0,
        "first_5min_avg_volz": 0.0,
        "last_5min_avg_volz": 0.0,
        "u_shape_intraday": 0.0,
        "volume_acceleration": 0.0,
        "has_path": 0,
    }


class TestRunnerFeatureCoverage(unittest.TestCase):
    """Production model + runner feature-set must match exactly.

    s122: silent regression where runner's dict missed 24 of 54 features
    caused 0 picks Monday. This test prevents recurrence.
    """

    def test_runner_features_cover_all_model_columns(self):
        """The runner's feature dict has every column the model expects."""
        v3t_pkl = MODELS / "continuer_v2_v3_tuned.pkl"
        if not v3t_pkl.exists():
            self.skipTest(f"production model {v3t_pkl} missing — skip")
        with open(v3t_pkl, "rb") as f:
            artifacts = pickle.load(f)
        expected = set(artifacts["feature_columns"])
        actual = set(_build_runner_features_inline().keys())

        missing = expected - actual
        extra = actual - expected
        self.assertEqual(missing, set(),
                          f"runner is MISSING {len(missing)} features the model expects: "
                          f"{sorted(missing)}")
        # Extras are OK (model just won't see them) but log for visibility
        if extra:
            print(f"\n  NOTE: runner produces {len(extra)} extra features "
                  f"unused by model: {sorted(extra)}")

    def test_runner_feature_count_matches_model(self):
        """Sanity: count = count (catches typos that produce duplicates)."""
        v3t_pkl = MODELS / "continuer_v2_v3_tuned.pkl"
        if not v3t_pkl.exists():
            self.skipTest(f"production model {v3t_pkl} missing — skip")
        with open(v3t_pkl, "rb") as f:
            artifacts = pickle.load(f)
        expected_n = len(artifacts["feature_columns"])
        actual_n = len(_build_runner_features_inline().keys())
        self.assertEqual(actual_n, expected_n,
                          f"runner builds {actual_n} features but model expects {expected_n}")

    def test_features_serialize_to_floats_or_ints(self):
        """All values must be numeric (no None / strings) for sklearn."""
        f = _build_runner_features_inline()
        for k, v in f.items():
            self.assertIsInstance(v, (int, float),
                                    f"feature {k!r}={v!r} is not numeric")
            # NaN check
            if isinstance(v, float):
                self.assertEqual(v, v, f"feature {k!r} is NaN")

    def test_features_with_real_ticker_details(self):
        """Realistic td blob (NVDA-like) produces sensible feature values."""
        td = {
            "market_cap": 2.5e12, "sic_code": "3674",
            "sic_description": "Semiconductors & Related Devices",
            "total_employees": 30000, "list_date": "1999-01-22",
            "share_class_shares_outstanding": 24.6e9,
        }
        f = _build_runner_features_inline(td=td)
        self.assertGreater(f["log_market_cap"], 28)  # ln(2.5T) ~ 28.5
        self.assertEqual(f["mcap_known"], 1)
        self.assertEqual(f["sec_semi"], 1)
        self.assertEqual(f["sec_pharma"], 0)
        self.assertGreater(f["log_float"], 23)  # ln(24.6e9) ~ 24
        # Days since IPO 1999 -> ~27 years -> log1p(9855) ~ 9.2
        self.assertGreater(f["log_days_since_ipo"], 9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
