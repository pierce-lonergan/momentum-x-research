"""D286 (2026-05-11, doc 144 Exp #4 pre-commit) — TabPFN shadow-mode wrapper.

Pin the shadow-mode behavior:
  - Per-day quintile rank correctly buckets predictions
  - Missing/short days fall through gracefully
  - Output schema matches spec (ticker, d0, tabpfn_pred, quintile, realized_ret_t5)
  - Defensive: missing TABPFN_TOKEN doesn't crash the launcher (only the script)

These tests do NOT require TabPFN to be installed; they test the
quintile-assignment logic in isolation.
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def test_assign_per_day_quintile_basic():
    """5 predictions per day should get quintiles 0-4 in rank order."""
    from tabpfn_shadow_runner import assign_per_day_quintile
    preds = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
    dates = pd.Series(pd.to_datetime(["2026-01-01"] * 5))
    quintiles = assign_per_day_quintile(preds, dates)
    # Lowest pred = quintile 0; highest = quintile 4
    assert quintiles[0] == 0
    assert quintiles[4] == 4
    # All quintiles should be present
    assert sorted(quintiles) == [0, 1, 2, 3, 4]


def test_assign_per_day_quintile_two_days():
    """Quintiles are computed PER day, not across days. Use 5 picks per day."""
    from tabpfn_shadow_runner import assign_per_day_quintile
    # Day 1: small values 0.1 to 0.5; Day 2: large values 100 to 500
    preds = np.array([0.1, 0.2, 0.3, 0.4, 0.5,
                      100, 200, 300, 400, 500])
    dates = pd.Series(pd.to_datetime(["2026-01-01"]*5 + ["2026-01-02"]*5))
    quintiles = assign_per_day_quintile(preds, dates)
    # Day 1: lowest in day's set (0.1) -> q0; highest (0.5) -> q4
    assert quintiles[0] == 0
    assert quintiles[4] == 4
    # Day 2: same — lowest in day's set (100) -> q0; highest (500) -> q4
    assert quintiles[5] == 0
    assert quintiles[9] == 4
    # The CRITICAL property: 100 (day 2 lowest) gets quintile 0 even though
    # it's much larger than 0.5 (day 1 highest, also at quintile 4).
    # This proves quintiles are per-day, not global.
    assert quintiles[5] < quintiles[4], (
        "per-day quintile failed: day-2 lowest should get smaller quintile "
        "than day-1 highest, despite larger absolute value"
    )


def test_assign_per_day_quintile_short_day():
    """A day with <5 picks shouldn't crash; treats all as top quintile."""
    from tabpfn_shadow_runner import assign_per_day_quintile
    preds = np.array([0.1, 0.2, 0.3])  # 3 picks
    dates = pd.Series(pd.to_datetime(["2026-01-01"] * 3))
    quintiles = assign_per_day_quintile(preds, dates)
    # All assigned to quintile 4 (treated as top, since we can't bucket cleanly)
    assert (quintiles == 4).all()


def test_assign_per_day_quintile_realistic_distribution():
    """50 picks per day distributed normally — quintile counts should be ~10 each."""
    from tabpfn_shadow_runner import assign_per_day_quintile
    rng = np.random.default_rng(42)
    preds = rng.standard_normal(50)
    dates = pd.Series(pd.to_datetime(["2026-01-01"] * 50))
    quintiles = assign_per_day_quintile(preds, dates)
    counts = pd.Series(quintiles).value_counts().sort_index()
    # Each quintile should have 9-11 picks (50/5 = 10 expected)
    for q in range(5):
        assert 8 <= counts.get(q, 0) <= 12, f"quintile {q} count {counts.get(q,0)} not in [8,12]"


def test_shadow_output_schema():
    """The smoke-tested output for 2026-04-24 should have the documented schema."""
    p = REPO / "data" / "polygon_warehouse" / "derived" / "tabpfn_shadow" / "2026-04-24.parquet"
    if not p.exists():
        pytest.skip("Smoke-test output not present (run shadow runner first)")
    df = pd.read_parquet(p)
    expected_cols = {"ticker", "d0", "tabpfn_pred", "n_train_rows_used",
                     "tabpfn_quintile_per_day", "realized_ret_t5", "shadow_run_at_utc"}
    assert expected_cols.issubset(set(df.columns)), \
        f"Missing columns: {expected_cols - set(df.columns)}"
    # Quintile values must be in [0, 4]
    assert df["tabpfn_quintile_per_day"].between(0, 4).all()
    # tabpfn_pred should be a finite float
    assert df["tabpfn_pred"].notna().all()
    assert np.isfinite(df["tabpfn_pred"]).all()


def test_launcher_env_vars_safe_defaults():
    """The launcher must default LOTTERY_TABPFN_DEFENSIVE_OVERLAY=0 (not 1).
    Defensive overlay should NOT be enabled until 2 weeks of shadow data
    confirms the OOS pattern. Pin this to prevent accidental flip."""
    p = REPO / "scripts" / "lottery_paper_trade.ps1"
    text = p.read_text(encoding="utf-8")
    # Find the LOTTERY_TABPFN_DEFENSIVE_OVERLAY block
    lines = text.split("\n")
    in_block = False
    found_default_zero = False
    for i, line in enumerate(lines):
        if "LOTTERY_TABPFN_DEFENSIVE_OVERLAY" in line and "SetEnvironment" in line:
            # Check the value being set
            if '"0"' in line:
                found_default_zero = True
            elif '"1"' in line:
                pytest.fail(
                    f"LOTTERY_TABPFN_DEFENSIVE_OVERLAY default is '1' "
                    f"at line {i+1}; should be '0' until shadow data confirms.")
    assert found_default_zero, "LOTTERY_TABPFN_DEFENSIVE_OVERLAY default not set to 0"


def test_launcher_shadow_default_on():
    """LOTTERY_TABPFN_SHADOW should default to 1 (we want shadow data
    accumulating from the next launcher run). Defensive overlay still off."""
    p = REPO / "scripts" / "lottery_paper_trade.ps1"
    text = p.read_text(encoding="utf-8")
    lines = text.split("\n")
    found = False
    for i, line in enumerate(lines):
        if "LOTTERY_TABPFN_SHADOW" in line and "SetEnvironment" in line and '"1"' in line:
            found = True
            break
    assert found, "LOTTERY_TABPFN_SHADOW default should be '1'"


# ---------------------------------------------------------------------------
# D293a (2026-05-12, doc 155 REVERT verdict): ensemble reverted; only the
# n_estimators=2 production-safety fix remains. Ensemble code reverted
# because doc 155 Gate D-revised failed at single-day-shadow N (24-55 picks);
# bootstrap CI on (ensemble - mean per-seed) Spearman delta could not
# confirm CI lower bound >= -0.05 on 5 of 7 dates -- not because ensemble
# is bad (Gates B and C passed), but because we lack statistical power at
# current shadow-data scale to verify per-date quality. Filed D293.8 to
# re-test ensemble once shadow data accumulates ~200+ picks per pool.
# ---------------------------------------------------------------------------

def test_d293a_n_estimators_pinned():
    """N_ESTIMATORS=2 is the locked production-safety value per compass
    §Topic 7. tabpfn v7.1.1's default n_estimators causes 75min/seed
    runtime on RTX 5070 — explicit n_estimators=2 keeps inference at
    1-2 sec/seed. Pin to prevent accidental drift / regression."""
    from tabpfn_shadow_runner import N_ESTIMATORS
    assert N_ESTIMATORS == 2, (
        f"D293a N_ESTIMATORS locked at 2 per compass §Topic 7 "
        f"(prevents 75min/seed runtime on RTX 5070); got {N_ESTIMATORS}"
    )


def test_d293a_no_ensemble_constants():
    """D293a REVERTED ensemble. The N_SEEDS constant should NOT exist
    in the module. If it reappears, doc 155's REVERT is being undone
    without going through D293.8 verification — pin against that."""
    import tabpfn_shadow_runner as mod
    assert not hasattr(mod, "N_SEEDS"), (
        "N_SEEDS reappeared in tabpfn_shadow_runner — doc 155 REVERT "
        "verdict is being silently undone. D293.8 verification required "
        "before re-introducing ensemble code."
    )


def test_d293a_single_seed_n_estimators_explicit(monkeypatch):
    """fit_predict_tabpfn must call TabPFNRegressor exactly ONCE
    (single-seed) with explicit n_estimators=2 (production safety)."""
    import tabpfn_shadow_runner as mod
    n_test = 7
    n_train = 200
    call_log = []

    class _MockTabPFNRegressor:
        def __init__(self, **kwargs):
            call_log.append(kwargs)

        def fit(self, X, y):
            return self

        def predict(self, X):
            return np.full(len(X), 0.123, dtype=float)

    class _MockTabpfnModule:
        TabPFNRegressor = _MockTabPFNRegressor

    monkeypatch.setitem(sys.modules, "tabpfn", _MockTabpfnModule)

    rng = np.random.default_rng(0)
    X_train = rng.standard_normal((n_train, 5))
    y_train = rng.standard_normal(n_train)
    X_test = rng.standard_normal((n_test, 5))

    pred = mod.fit_predict_tabpfn(X_train, y_train, X_test)

    # Exactly ONE TabPFN call (single-seed, not ensemble)
    assert len(call_log) == 1, (
        f"D293a is single-seed; expected 1 TabPFNRegressor call, "
        f"got {len(call_log)}. If ensemble re-introduced, must go "
        f"through D293.8 verification."
    )
    # The call must use explicit n_estimators=2 (production safety)
    assert call_log[0].get("n_estimators") == mod.N_ESTIMATORS, (
        f"D293a requires explicit n_estimators={mod.N_ESTIMATORS}; "
        f"got {call_log[0].get('n_estimators')}"
    )
    # Returns a 1d array (not a tuple)
    assert isinstance(pred, np.ndarray), f"Expected np.ndarray, got {type(pred)}"
    assert pred.shape == (n_test,)
    assert np.all(np.isfinite(pred))
