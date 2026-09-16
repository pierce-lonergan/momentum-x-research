"""v6 Phase 1 — microstructure pack structural-invariant tests.

These tests pin properties that MUST hold by construction (not by
empirical fit):

  - tick_rule_sign: +1/-1/0 only; carry-forward on equality
  - VPIN ∈ [0, 1] for any non-empty signed flow
  - VPIN = 1 for perfectly one-directional flow
  - VPIN = 0 for perfectly balanced flow
  - OFI ∈ [-1, +1]
  - Kyle's λ ≥ 0 always (we clip negative)
  - Hawkes Fano ≥ 0
  - Amihud illiquidity ≥ 0
  - ISO sweep count uses code 14 (not 15 — that was the v2 builder bug)

These run on synthetic trades — no warehouse access required. A
separate smoke test (`scripts/v6_microstructure_pack.py` __main__ block,
or notebook) validates on real Polygon data.
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from v6_microstructure_pack import (  # noqa: E402
    tick_rule_sign,
    compute_vpin,
    compute_ofi_window,
    compute_kyle_lambda,
    compute_hawkes_fano,
    compute_amihud_illiquidity,
    compute_iso_sweep_count,
    compute_v6_microstructure_pack,
    ISO_CONDITION_CODE,
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def make_trades(prices, sizes, conditions=None,
                start="2026-04-01 13:30:00",  # 09:30 ET
                interval_sec=10) -> pd.DataFrame:
    """Build a synthetic trades DataFrame in the same shape as Polygon trades_v1.

    Timestamps are evenly spaced starting at `start` UTC (which is 09:30 ET
    during EDT). Returns ts_et tz-aware UTC, matching the parquet schema.
    """
    n = len(prices)
    assert len(sizes) == n
    if conditions is None:
        conditions = [""] * n
    ts = pd.date_range(start=start, periods=n, freq=f"{interval_sec}s", tz="UTC")
    return pd.DataFrame({
        "price": np.array(prices, dtype="float64"),
        "size": np.array(sizes, dtype="float64"),
        "conditions": conditions,
        "ts_et": ts,
    })


# ──────────────────────────────────────────────────────────────────────
# 1. tick_rule_sign
# ──────────────────────────────────────────────────────────────────────


def test_tick_rule_basic_up_down():
    """Strictly increasing prices → all +1; strictly decreasing → all -1.
    First trade is unsignable, defaults to 0."""
    s = tick_rule_sign(pd.Series([1.0, 2.0, 3.0, 4.0]))
    assert s.tolist() == [0.0, 1.0, 1.0, 1.0]
    s = tick_rule_sign(pd.Series([10.0, 9.0, 8.0, 7.0]))
    assert s.tolist() == [0.0, -1.0, -1.0, -1.0]


def test_tick_rule_equality_carries_forward():
    """Lee-Ready carry-forward: when price doesn't change, sign inherits prior."""
    # Pattern: [_, +, =, =, -, =, +, =]
    s = tick_rule_sign(pd.Series([10.0, 11.0, 11.0, 11.0, 10.0, 10.0, 11.0, 11.0]))
    assert s.tolist() == [0.0, 1.0, 1.0, 1.0, -1.0, -1.0, 1.0, 1.0]


def test_tick_rule_initial_equality_stays_zero():
    """Trades that start with all-equal prices have no carry source → all 0
    until first move."""
    s = tick_rule_sign(pd.Series([5.0, 5.0, 5.0, 6.0, 6.0]))
    assert s.tolist() == [0.0, 0.0, 0.0, 1.0, 1.0]


def test_tick_rule_empty():
    s = tick_rule_sign(pd.Series([], dtype="float64"))
    assert len(s) == 0


# ──────────────────────────────────────────────────────────────────────
# 2. VPIN
# ──────────────────────────────────────────────────────────────────────


def test_vpin_perfectly_one_sided_flow_is_one():
    """Strictly rising prices → all trades signed +1 → buckets are 100% buy
    → VPIN = 1.0."""
    prices = list(range(1, 201))  # 200 strictly increasing
    sizes = [100] * 200
    df = make_trades(prices, sizes)
    v = compute_vpin(df, n_buckets=10)
    assert abs(v - 1.0) < 1e-9, f"expected VPIN=1.0 for monotone-up flow, got {v}"


def test_vpin_perfectly_balanced_flow_is_low():
    """Alternating up/down with equal sizes → each bucket has B≈S → VPIN ≈ 0.

    NOTE: tick rule on alternating gives perfect alternation +1/-1/+1/-1...
    With even bucket counts, B and S in each bucket should equal."""
    # 200 trades alternating +1/-1 in price, equal size
    prices = []
    p = 100.0
    for i in range(200):
        p += 0.01 if i % 2 == 0 else -0.01
        prices.append(p)
    sizes = [100] * 200
    df = make_trades(prices, sizes)
    v = compute_vpin(df, n_buckets=10)
    assert v < 0.05, f"expected VPIN ≈ 0 for balanced flow, got {v}"


def test_vpin_in_unit_interval():
    """For any random flow, VPIN must be in [0, 1]."""
    rng = np.random.default_rng(7)
    prices = 10 + rng.standard_normal(500).cumsum() * 0.05
    sizes = rng.integers(50, 500, 500).astype(float)
    df = make_trades(list(prices), list(sizes))
    v = compute_vpin(df, n_buckets=20)
    assert 0.0 <= v <= 1.0, f"VPIN out of [0,1]: {v}"


def test_vpin_empty_returns_nan():
    df = make_trades([], [])
    v = compute_vpin(df, n_buckets=10)
    assert np.isnan(v)


# ──────────────────────────────────────────────────────────────────────
# 3. OFI
# ──────────────────────────────────────────────────────────────────────


def test_ofi_window_purely_buying_returns_one():
    """All trades in window are buys → OFI = +1."""
    prices = list(range(1, 51))  # 50 monotone-up trades
    sizes = [100] * 50
    # 10s spacing × 50 trades = 500s = 8.3 min → all in first 30 min
    df = make_trades(prices, sizes)
    o = compute_ofi_window(df, start_min=0, end_min=30)
    assert abs(o - 1.0) < 1e-9, f"expected OFI=+1, got {o}"


def test_ofi_window_purely_selling_returns_negative_one():
    prices = list(range(50, 0, -1))  # monotone-down
    sizes = [100] * 50
    df = make_trades(prices, sizes)
    o = compute_ofi_window(df, start_min=0, end_min=30)
    assert abs(o - (-1.0)) < 1e-9, f"expected OFI=-1, got {o}"


def test_ofi_window_in_bounds():
    rng = np.random.default_rng(13)
    prices = 5 + rng.standard_normal(300).cumsum() * 0.02
    sizes = rng.integers(10, 200, 300).astype(float)
    df = make_trades(list(prices), list(sizes))
    o = compute_ofi_window(df, start_min=0, end_min=30)
    assert -1.0 - 1e-9 <= o <= 1.0 + 1e-9, f"OFI out of [-1,1]: {o}"


def test_ofi_window_outside_returns_nan():
    """Trades only after 11:00 ET should give NaN for window [0,30) of RTH."""
    # 11:00 ET = 15:00 UTC during EDT
    prices = [5.0] * 10
    sizes = [100] * 10
    df = make_trades(prices, sizes, start="2026-04-01 15:00:00")
    o = compute_ofi_window(df, start_min=0, end_min=30)
    assert np.isnan(o)


# ──────────────────────────────────────────────────────────────────────
# 4. Kyle's λ
# ──────────────────────────────────────────────────────────────────────


def test_kyle_lambda_nonnegative():
    """We clip negative slopes to 0; result is always ≥ 0."""
    rng = np.random.default_rng(21)
    prices = 10 + rng.standard_normal(500).cumsum() * 0.01
    sizes = rng.integers(10, 1000, 500).astype(float)
    df = make_trades(list(prices), list(sizes), interval_sec=2)
    # Spread over ~17 min so we get a few 5-min bars
    lam = compute_kyle_lambda(df)
    assert not np.isnan(lam), "expected finite λ on 500 trades"
    assert lam >= 0.0, f"λ should be clipped ≥ 0, got {lam}"


def test_kyle_lambda_higher_for_thinner_book():
    """Doubling the size in each bar with the same return should HALVE λ
    (since λ = ret / dvol). Sanity: if dvol→2·dvol and ret unchanged,
    λ→λ/2. With OLS regression the proportion may shift but should DROP."""
    rng = np.random.default_rng(33)
    n = 500
    base_prices = 10 + rng.standard_normal(n).cumsum() * 0.01
    sizes_thin = np.full(n, 100.0)
    sizes_thick = np.full(n, 10000.0)
    df_thin = make_trades(list(base_prices), list(sizes_thin), interval_sec=2)
    df_thick = make_trades(list(base_prices), list(sizes_thick), interval_sec=2)
    lam_thin = compute_kyle_lambda(df_thin)
    lam_thick = compute_kyle_lambda(df_thick)
    assert lam_thin > lam_thick, (
        f"thin-book λ should exceed thick-book λ, got {lam_thin} ≤ {lam_thick}"
    )


def test_kyle_lambda_too_few_trades_returns_nan():
    df = make_trades([1.0, 2.0, 3.0], [100, 100, 100])
    lam = compute_kyle_lambda(df)
    assert np.isnan(lam)


# ──────────────────────────────────────────────────────────────────────
# 5. Hawkes-proxy Fano
# ──────────────────────────────────────────────────────────────────────


def test_hawkes_fano_nonnegative():
    rng = np.random.default_rng(44)
    prices = [10.0] * 200
    sizes = rng.integers(50, 500, 200).astype(float)
    df = make_trades(prices, sizes, interval_sec=3)
    f = compute_hawkes_fano(df, window_sec=60.0)
    assert f >= 0.0


def test_hawkes_fano_constant_rate_near_one():
    """Perfectly evenly-spaced trades should have Fano factor ≈ 0
    (variance of count is 0 since every window has exactly the same count)."""
    prices = [10.0] * 200
    sizes = [100] * 200
    # 200 trades × 3s = 600s = 10 windows of 60s, each with exactly 20 trades
    df = make_trades(prices, sizes, interval_sec=3)
    f = compute_hawkes_fano(df, window_sec=60.0)
    assert f < 0.05, f"evenly-spaced trades should have Fano≈0, got {f}"


def test_hawkes_fano_clustered_is_high():
    """Bursty trades (long pause then a flurry) should have Fano > 1."""
    # 50 trades in first 5s, then 50 trades in last 5s of a 60s window
    n = 50
    ts1 = pd.date_range("2026-04-01 13:30:00", periods=n, freq="100ms", tz="UTC")
    ts2 = pd.date_range("2026-04-01 13:31:00", periods=n, freq="100ms", tz="UTC")
    # then sparse trades in between
    ts3 = pd.date_range("2026-04-01 13:32:00", periods=10, freq="60s", tz="UTC")
    ts = ts1.union(ts2).union(ts3)
    df = pd.DataFrame({
        "price": np.full(len(ts), 10.0),
        "size":  np.full(len(ts), 100.0),
        "conditions": [""] * len(ts),
        "ts_et": ts,
    })
    f = compute_hawkes_fano(df, window_sec=60.0)
    assert f > 1.0, f"clustered flow should have Fano > 1, got {f}"


# ──────────────────────────────────────────────────────────────────────
# 6. Amihud illiquidity
# ──────────────────────────────────────────────────────────────────────


def test_amihud_nonnegative():
    rng = np.random.default_rng(55)
    prices = 10 + rng.standard_normal(500).cumsum() * 0.01
    sizes = rng.integers(50, 1000, 500).astype(float)
    df = make_trades(list(prices), list(sizes), interval_sec=2)
    a = compute_amihud_illiquidity(df)
    assert a >= 0.0


def test_amihud_higher_for_lower_dollar_volume():
    """Same returns, lower dvol → higher illiquidity."""
    rng = np.random.default_rng(66)
    prices = 10 + rng.standard_normal(500).cumsum() * 0.01
    big_sizes = np.full(500, 10000.0)
    small_sizes = np.full(500, 100.0)
    df_big = make_trades(list(prices), list(big_sizes), interval_sec=2)
    df_small = make_trades(list(prices), list(small_sizes), interval_sec=2)
    a_big = compute_amihud_illiquidity(df_big)
    a_small = compute_amihud_illiquidity(df_small)
    assert a_small > a_big, (
        f"smaller dvol should be more illiquid: {a_small} ≤ {a_big}"
    )


# ──────────────────────────────────────────────────────────────────────
# 7. ISO sweep count (the v2 builder bug fix)
# ──────────────────────────────────────────────────────────────────────


def test_iso_sweep_uses_condition_14_not_15():
    """The v2 builder used '15' (Average Price Trade) which is why all
    sweep_burst columns were zero. Polygon ISO is condition 14."""
    assert ISO_CONDITION_CODE == "14"
    df = make_trades(
        [1.0, 2.0, 3.0, 4.0, 5.0],
        [100] * 5,
        conditions=["14", "12,14", "12", "14,37", "37"],
    )
    assert compute_iso_sweep_count(df) == 3


def test_iso_sweep_count_token_match_not_substring():
    """Code '14' must not match '141' or '214'. We split on comma."""
    df = make_trades(
        [1.0, 2.0, 3.0],
        [100] * 3,
        conditions=["141", "214", "14"],  # only the last is a real ISO
    )
    assert compute_iso_sweep_count(df) == 1


def test_iso_sweep_count_handles_missing_column():
    df = make_trades([1.0, 2.0], [100, 100])
    df = df.drop(columns=["conditions"])
    assert compute_iso_sweep_count(df) == 0


# ──────────────────────────────────────────────────────────────────────
# 8. Full pack runs end-to-end
# ──────────────────────────────────────────────────────────────────────


def test_pack_returns_all_expected_keys():
    rng = np.random.default_rng(77)
    n = 600
    prices = 10 + rng.standard_normal(n).cumsum() * 0.01
    sizes = rng.integers(50, 1000, n).astype(float)
    conds = ["14" if i % 50 == 0 else "" for i in range(n)]
    df = make_trades(list(prices), list(sizes), conditions=conds, interval_sec=2)
    pack = compute_v6_microstructure_pack(df)
    expected = {"vpin_d0", "ofi_first30_d0", "kyle_lambda_d0",
                "hawkes_fano_d0", "amihud_illiq_d0", "iso_sweep_count_d0"}
    assert set(pack.keys()) == expected
    # vpin in [0,1], ofi in [-1,1]
    assert 0.0 <= pack["vpin_d0"] <= 1.0
    assert -1.0 <= pack["ofi_first30_d0"] <= 1.0
    assert pack["kyle_lambda_d0"] >= 0.0
    assert pack["hawkes_fano_d0"] >= 0.0
    assert pack["amihud_illiq_d0"] >= 0.0
    assert pack["iso_sweep_count_d0"] == n // 50  # every 50th = 12 sweeps
