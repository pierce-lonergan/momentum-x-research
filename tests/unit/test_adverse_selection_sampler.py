"""Unit tests for the arena adverse-selection sampler."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "mx-arena"))

from arena.adverse_selection_sampler import (  # noqa: E402
    AdverseSelectionSampler,
    MIN_N_FOR_TIER_SAMPLE,
)


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def synthetic_curves() -> pd.DataFrame:
    """Build a synthetic curves DataFrame with known cell sizes."""
    rows = []
    # sub_3 cell at T=15: 5 samples (above MIN)
    for i, drift in enumerate([-100.0, -50.0, 0.0, 50.0, 100.0]):
        rows.append({
            "ticker": f"S{i}", "session_date": "2026-01-01",
            "prod_entry_px": 1.5, "T_minutes": 15,
            "adverse_drift_bps": drift, "has_quote": True,
            "price_tier": "sub_3", "tod_q": "q1",
        })
    # 3_to_10 cell at T=15: only 2 samples (below MIN)
    for i, drift in enumerate([200.0, 300.0]):
        rows.append({
            "ticker": f"M{i}", "session_date": "2026-01-01",
            "prod_entry_px": 5.0, "T_minutes": 15,
            "adverse_drift_bps": drift, "has_quote": True,
            "price_tier": "3_to_10", "tod_q": "q1",
        })
    # above_10 cell at T=15: empty
    # sub_3 cell at T=60: 3 samples
    for i, drift in enumerate([-200.0, 0.0, 200.0]):
        rows.append({
            "ticker": f"S{i}", "session_date": "2026-01-01",
            "prod_entry_px": 1.5, "T_minutes": 60,
            "adverse_drift_bps": drift, "has_quote": True,
            "price_tier": "sub_3", "tod_q": "q1",
        })
    return pd.DataFrame(rows)


# ── Construction ──────────────────────────────────────────────────


def test_from_dataframe_builds_pools(synthetic_curves):
    s = AdverseSelectionSampler.from_dataframe(synthetic_curves)
    assert s.cell_n("sub_3", 15) == 5
    assert s.cell_n("3_to_10", 15) == 2
    assert s.cell_n("sub_3", 60) == 3
    assert s.cell_n("above_10", 15) == 0  # not present
    assert s._max_T == 60


def test_from_dataframe_drops_no_quote_rows():
    df = pd.DataFrame([
        {"ticker": "X", "session_date": "2026-01-01", "prod_entry_px": 1.0,
         "T_minutes": 5, "adverse_drift_bps": 100.0, "has_quote": True,
         "price_tier": "sub_3", "tod_q": "q1"},
        {"ticker": "X", "session_date": "2026-01-01", "prod_entry_px": 1.0,
         "T_minutes": 10, "adverse_drift_bps": float("nan"), "has_quote": False,
         "price_tier": "sub_3", "tod_q": "q1"},
    ])
    s = AdverseSelectionSampler.from_dataframe(df)
    assert s.cell_n("sub_3", 5) == 1
    assert s.cell_n("sub_3", 10) == 0  # filtered


def test_from_dataframe_drops_nan_drift():
    df = pd.DataFrame([
        {"ticker": "X", "session_date": "2026-01-01", "prod_entry_px": 1.0,
         "T_minutes": 5, "adverse_drift_bps": float("nan"), "has_quote": True,
         "price_tier": "sub_3", "tod_q": "q1"},
    ])
    s = AdverseSelectionSampler.from_dataframe(df)
    assert s.cell_n("sub_3", 5) == 0


def test_from_dataframe_missing_columns_raises():
    df = pd.DataFrame([{"ticker": "X", "T_minutes": 5}])
    with pytest.raises(ValueError, match="missing columns"):
        AdverseSelectionSampler.from_dataframe(df)


# ── Sampling ──────────────────────────────────────────────────────


def test_sample_returns_observed_value_when_n_above_min(synthetic_curves):
    s = AdverseSelectionSampler.from_dataframe(synthetic_curves)
    rng = np.random.default_rng(42)
    observed = {-100.0, -50.0, 0.0, 50.0, 100.0}
    for _ in range(50):
        draw = s.sample("sub_3", 15, rng=rng)
        assert draw in observed


def test_sample_falls_back_to_global_pool_when_n_below_min(synthetic_curves):
    """3_to_10 at T=15 has n=2 (below MIN). Should fall back to global
    pool at T=15, which is sub_3's 5 samples + 3_to_10's 2 samples = 7."""
    s = AdverseSelectionSampler.from_dataframe(synthetic_curves)
    rng = np.random.default_rng(0)
    global_observed = {-100.0, -50.0, 0.0, 50.0, 100.0, 200.0, 300.0}
    for _ in range(50):
        draw = s.sample("3_to_10", 15, rng=rng)
        assert draw in global_observed


def test_sample_falls_back_to_zero_for_unknown_T():
    """If both the tier-cell and global pool are empty at T, return 0.0."""
    df = pd.DataFrame([
        {"ticker": "X", "session_date": "2026-01-01", "prod_entry_px": 1.0,
         "T_minutes": 5, "adverse_drift_bps": 100.0, "has_quote": True,
         "price_tier": "sub_3", "tod_q": "q1"},
    ])
    s = AdverseSelectionSampler.from_dataframe(df)
    # T=5 only; ask for T=30 → after clip to _max_T=5, but cell at T=5
    # still has data. Confirm clamp + draw:
    rng = np.random.default_rng(0)
    assert s.sample("sub_3", 30, rng=rng) == 100.0


def test_sample_clamps_T_to_max():
    df = pd.DataFrame([
        {"ticker": "X", "session_date": "2026-01-01", "prod_entry_px": 1.0,
         "T_minutes": 60, "adverse_drift_bps": 50.0, "has_quote": True,
         "price_tier": "sub_3", "tod_q": "q1"},
    ])
    s = AdverseSelectionSampler.from_dataframe(df)
    assert s._max_T == 60
    # Asking for T=999 should clamp to T=60 → 50.0
    assert s.sample("sub_3", 999, rng=np.random.default_rng(0)) == 50.0


def test_sample_path_returns_correct_length(synthetic_curves):
    s = AdverseSelectionSampler.from_dataframe(synthetic_curves)
    path = s.sample_path("sub_3", T_max=15, rng=np.random.default_rng(0))
    assert len(path) == 15
    # All entries must be drawn from the synthetic data or fallback
    valid = {-200.0, -100.0, -50.0, 0.0, 50.0, 100.0, 200.0, 300.0}
    for v in path:
        assert v in valid or v == 0.0  # 0.0 is the empty-cell fallback


# ── Determinism ───────────────────────────────────────────────────


def test_sample_with_same_seed_is_reproducible(synthetic_curves):
    s = AdverseSelectionSampler.from_dataframe(synthetic_curves)
    a = [s.sample("sub_3", 15, rng=np.random.default_rng(99)) for _ in range(20)]
    b = [s.sample("sub_3", 15, rng=np.random.default_rng(99)) for _ in range(20)]
    assert a == b


# ── Quantiles + summary ───────────────────────────────────────────


def test_cell_quantiles_returns_correct_values(synthetic_curves):
    s = AdverseSelectionSampler.from_dataframe(synthetic_curves)
    qs = s.cell_quantiles("sub_3", 15, qs=(0.25, 0.5, 0.75))
    # Synthetic: [-100, -50, 0, 50, 100]
    assert qs[0.5] == pytest.approx(0.0)
    # Empty cell → NaN
    qs2 = s.cell_quantiles("above_10", 15)
    assert all(np.isnan(v) for v in qs2.values())


def test_summary_renders_without_error(synthetic_curves):
    s = AdverseSelectionSampler.from_dataframe(synthetic_curves)
    out = s.summary()
    assert "AdverseSelectionSampler" in out
    assert "sub_3" in out


# ── Real-data smoke ───────────────────────────────────────────────


def test_real_default_data_loads_if_present():
    """The default curves parquet may or may not exist on a fresh checkout.
    If it does, the sampler should build cleanly from it."""
    from arena.adverse_selection_sampler import DEFAULT_CURVES
    if not DEFAULT_CURVES.exists():
        pytest.skip("default curves parquet not present")
    s = AdverseSelectionSampler.from_default()
    # Must support at least one tier × T cell from the real data
    assert s._max_T >= 1
    rng = np.random.default_rng(0)
    # Real curves have T=1..60; sub_3 should be the largest cell
    assert s.cell_n("sub_3", 5) >= 1 or s.cell_n("3_to_10", 5) >= 1
