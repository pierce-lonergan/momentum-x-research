"""v6 Phase 1 — microstructure feature pack (VPIN + OFI + Kyle's λ + Hawkes-proxy + Amihud).

Per the v6 roadmap (`docs/research-log/132 § 3` + `133`), the highest-ROI path
forward is DATA, not architecture. M.md research identifies these 5
microstructure scalars per (ticker, d0) as the +0.05–0.10 Spearman
expected-lift pack:

  1. VPIN (Easley, López de Prado, O'Hara — RFS 2012)
     Bucket-volume buy/sell imbalance, time-averaged over equal-volume
     buckets. Captures informed-trader concentration.

  2. OFI_first30 (Cont, Kukanov, Stoikov — JFE 2014)
     Signed volume sum over the first 30 min of RTH. Direct measure of
     opening-window directional aggression.

  3. Kyle's λ (Kyle 1985)
     Slope of |return| on |signed dollar volume| in 5-min bars. Higher
     means smaller trades move the price more — fragile liquidity.

  4. Hawkes-proxy Fano factor (Bauwens-Hautsch 2009; Embrechts-Liniger-Lin 2011)
     Variance/mean of inter-trade intervals. >1 means clustered arrivals —
     proxy for Hawkes self-excitation without fitting the full process.

  5. Amihud illiquidity (Amihud 2002)
     Average over 5-min bars of |return| / dollar_volume × 1e6.
     Cross-section-stable illiquidity proxy.

We use the **tick rule** (Lee-Ready 1991, downgraded fallback) for trade
signing because Polygon `quotes_v1` NBBO is not in this warehouse —
tick-rule signing has ≈75% agreement with Lee-Ready in modern liquid
microcaps per Holden-Jacobsen 2014.

NO FEATURE FROM THIS PACK IS WIRED INTO PRODUCTION YET. Per Phase 0
hygiene, every addition must pass:
  - Numerai feature-neutralization (residual ρ improvement vs sector/cap)
  - Deflated Sharpe (with N_trials = however many configs we tried)
  - CPCV stability (frac of fold-subsets with positive Sharpe)

This module is the COMPUTATION layer. Validation belongs in
`scripts/ml_v6_phase_0_validation.py` after we have a model trained on
v3_features ⊕ v6_microstructure_pack.
"""
from __future__ import annotations
from typing import Optional

import numpy as np
import pandas as pd


# Polygon trade condition codes (subset we care about):
#   12 = Form-T (extended hours)
#   13 = Cross / Out of Sequence
#   14 = Intermarket Sweep Order (ISO) — aggressive liquidity-taker
#   15 = Average Price Trade (NOT what build_microstructure_features_v2.py
#        was trying to detect — that's a known bug in the v2 builder)
#   37 = Odd Lot Trade
#   06 = Cash-Settled (T+0)
#   07 = Next-Day Settlement (T+1)
ISO_CONDITION_CODE = "14"


# ──────────────────────────────────────────────────────────────────────
# Trade-signing primitives
# ──────────────────────────────────────────────────────────────────────


def tick_rule_sign(prices: pd.Series) -> pd.Series:
    """Lee-Ready tick rule (downgraded — no NBBO).

    +1 if price > prior trade
    -1 if price < prior trade
    Carry-forward sign on equality.
    Initial trade = 0 (cannot be signed, will be excluded from buy/sell counts).

    Returns a pd.Series of {-1, 0, +1} aligned with `prices`.
    """
    if len(prices) == 0:
        return pd.Series([], dtype="float64")
    diffs = prices.diff()
    # Direct sign on the first pass (NaN for index 0)
    sign = np.sign(diffs).astype("float64")
    # Carry-forward on equality (sign==0): replace 0→NaN, ffill, then fill
    # initial NaNs (index 0 + any leading equalities) with 0.
    sign = sign.replace(0.0, np.nan)
    sign = sign.ffill()
    return sign.fillna(0.0)


# ──────────────────────────────────────────────────────────────────────
# 1. VPIN (Easley-LdP-O'Hara RFS 2012)
# ──────────────────────────────────────────────────────────────────────


def compute_vpin(trades_df: pd.DataFrame, n_buckets: int = 50,
                 size_col: str = "size", price_col: str = "price") -> float:
    """Volume-Synchronized Probability of Informed Trading.

    Method:
      1. Sign each trade via tick rule
      2. Form n_buckets equal-volume buckets (cumulative-volume bins)
      3. For each bucket: imb = |B - S| / (B + S) where B,S are signed volumes
      4. Return mean(imb) across buckets

    Edge cases:
      - Empty trades_df → returns NaN
      - <n_buckets total volume → returns NaN (insufficient data)
      - All trades same price (no signable trades) → returns 0.0
    """
    if len(trades_df) == 0:
        return float("nan")
    df = trades_df.copy()
    if df[size_col].sum() <= 0:
        return float("nan")
    sign = tick_rule_sign(df[price_col])
    df["_signed_size"] = sign.values * df[size_col].values
    bucket_size = df[size_col].sum() / n_buckets
    if bucket_size <= 0:
        return float("nan")
    df["_cum_vol"] = df[size_col].cumsum()
    df["_bucket"] = (df["_cum_vol"] / bucket_size).clip(upper=n_buckets - 1).astype(int)
    grouped = df.groupby("_bucket", observed=True)
    buy_vol = grouped["_signed_size"].apply(lambda s: s[s > 0].sum())
    sell_vol = grouped["_signed_size"].apply(lambda s: -s[s < 0].sum())
    total = buy_vol + sell_vol
    imb = (buy_vol - sell_vol).abs() / total.where(total > 0, np.nan)
    # Buckets with all-zero signed volume (e.g. all carry-forward zero starts)
    # become NaN; mean of remaining is the VPIN.
    return float(imb.mean())  # nan if all buckets are nan


# ──────────────────────────────────────────────────────────────────────
# 2. Order Flow Imbalance (Cont-Kukanov-Stoikov JFE 2014, simplified)
# ──────────────────────────────────────────────────────────────────────


def compute_ofi_window(trades_df: pd.DataFrame, ts_col: str = "ts_et",
                       start_min: int = 0, end_min: int = 30,
                       size_col: str = "size",
                       price_col: str = "price") -> float:
    """Signed-volume imbalance over [09:30+start_min, 09:30+end_min) ET.

    Returns dimensionless ratio: (B - S) / (B + S) over the window.
    Positive = net buy pressure; negative = net sell pressure.

    Edge cases:
      - No trades in window → returns NaN
      - All same price → returns 0.0
    """
    if len(trades_df) == 0:
        return float("nan")
    ts = pd.to_datetime(trades_df[ts_col], utc=True)
    ts_et = ts.dt.tz_convert("US/Eastern")
    open_minutes = (ts_et.dt.hour * 60 + ts_et.dt.minute) - (9 * 60 + 30)
    mask = (open_minutes >= start_min) & (open_minutes < end_min)
    sub = trades_df[mask]
    if len(sub) == 0:
        return float("nan")
    sign = tick_rule_sign(sub[price_col])
    signed = sign.values * sub[size_col].values
    buy = signed[signed > 0].sum()
    sell = -signed[signed < 0].sum()
    total = buy + sell
    if total <= 0:
        return float("nan")
    return float((buy - sell) / total)


# ──────────────────────────────────────────────────────────────────────
# 3. Kyle's λ (Kyle 1985, 5-min bar regression form)
# ──────────────────────────────────────────────────────────────────────


def compute_kyle_lambda(trades_df: pd.DataFrame, ts_col: str = "ts_et",
                        window_min: int = 5,
                        size_col: str = "size",
                        price_col: str = "price") -> float:
    """Price-impact coefficient λ from |Δlog-price| = λ · |signed dollar volume|.

    Aggregates trades into `window_min`-minute bars over RTH (09:30–16:00 ET),
    computes per-bar:
      - |bar_return|  = |log(close/open)|
      - |signed_dvol| = |Σ sign·price·size|

    OLS regression of |bar_return| on |signed_dvol|. Returns slope (in
    units of 1/dollar). Higher = more fragile liquidity (small order
    moves price).

    Returns max(λ, 0.0) — negative slopes are noise from sparse bars.
    """
    if len(trades_df) < window_min * 4:  # need ≥4 trades per 5-min bar minimum
        return float("nan")
    ts = pd.to_datetime(trades_df[ts_col], utc=True).dt.tz_convert("US/Eastern")
    open_minutes = (ts.dt.hour * 60 + ts.dt.minute) - (9 * 60 + 30)
    rth_mask = (open_minutes >= 0) & (open_minutes < (16 - 9) * 60 - 30)
    sub = trades_df[rth_mask].copy()
    if len(sub) < window_min * 4:
        return float("nan")
    sub["_bar"] = (open_minutes[rth_mask].values // window_min).astype(int)
    sign = tick_rule_sign(sub[price_col])
    sub["_signed_dvol"] = sign.values * sub[price_col].values * sub[size_col].values
    sub["_log_p"] = np.log(sub[price_col].clip(lower=1e-9))
    grp = sub.groupby("_bar", observed=True)
    bar = pd.DataFrame({
        "open_log_p": grp["_log_p"].first(),
        "close_log_p": grp["_log_p"].last(),
        "signed_dvol": grp["_signed_dvol"].sum(),
    })
    bar["abs_ret"] = (bar["close_log_p"] - bar["open_log_p"]).abs()
    bar["abs_dvol"] = bar["signed_dvol"].abs()
    bar = bar[(bar["abs_dvol"] > 0)]
    if len(bar) < 3:
        return float("nan")
    # OLS slope: λ = Σ(x·y) / Σ(x²)  where x = abs_dvol, y = abs_ret
    x = bar["abs_dvol"].values
    y = bar["abs_ret"].values
    denom = float((x * x).sum())
    if denom <= 0:
        return float("nan")
    slope = float((x * y).sum() / denom)
    return max(slope, 0.0)


# ──────────────────────────────────────────────────────────────────────
# 4. Hawkes-proxy Fano factor (clustering of trade arrivals)
# ──────────────────────────────────────────────────────────────────────


def compute_hawkes_fano(trades_df: pd.DataFrame, ts_col: str = "ts_et",
                        window_sec: float = 60.0) -> float:
    """Variance/mean of trade-count per fixed time window over RTH.

    Fano factor F:
      - F = 1: Poisson (memoryless arrivals)
      - F > 1: clustered (over-dispersed; Hawkes-like self-excitation)
      - F < 1: regular (under-dispersed; refractory)

    For microcap gap-up days we expect F ≫ 1 during news-driven bursts.

    Edge cases:
      - <2 windows of trade data → NaN
      - Mean=0 (no trades in any window) → NaN
    """
    if len(trades_df) < 4:
        return float("nan")
    ts = pd.to_datetime(trades_df[ts_col], utc=True).dt.tz_convert("US/Eastern")
    open_minutes = (ts.dt.hour * 60 + ts.dt.minute) - (9 * 60 + 30)
    rth_mask = (open_minutes >= 0) & (open_minutes < (16 - 9) * 60 - 30)
    sub_ts = ts[rth_mask]
    if len(sub_ts) < 4:
        return float("nan")
    # Bucket into fixed-second windows
    sec_since_open = (sub_ts - sub_ts.iloc[0]).dt.total_seconds()
    bucket = (sec_since_open // window_sec).astype(int)
    counts = bucket.value_counts()
    if len(counts) < 2:
        return float("nan")
    mean = float(counts.mean())
    var = float(counts.var(ddof=1))
    if mean <= 0:
        return float("nan")
    return var / mean


# ──────────────────────────────────────────────────────────────────────
# 5. Amihud illiquidity (Amihud 2002) — averaged over 5-min bars
# ──────────────────────────────────────────────────────────────────────


def compute_amihud_illiquidity(trades_df: pd.DataFrame,
                               ts_col: str = "ts_et",
                               window_min: int = 5,
                               size_col: str = "size",
                               price_col: str = "price",
                               scale: float = 1e6) -> float:
    """Mean over 5-min bars of |bar_return| / bar_dollar_volume × scale.

    Higher = less liquid (small dollar volume produces large returns).
    Multiplied by `scale` (default 1e6) to bring values into a workable
    O(1) range for tabular models.
    """
    if len(trades_df) < window_min * 4:
        return float("nan")
    ts = pd.to_datetime(trades_df[ts_col], utc=True).dt.tz_convert("US/Eastern")
    open_minutes = (ts.dt.hour * 60 + ts.dt.minute) - (9 * 60 + 30)
    rth_mask = (open_minutes >= 0) & (open_minutes < (16 - 9) * 60 - 30)
    sub = trades_df[rth_mask].copy()
    if len(sub) < window_min * 4:
        return float("nan")
    sub["_bar"] = (open_minutes[rth_mask].values // window_min).astype(int)
    sub["_dvol"] = sub[price_col].values * sub[size_col].values
    sub["_log_p"] = np.log(sub[price_col].clip(lower=1e-9))
    grp = sub.groupby("_bar", observed=True)
    bar = pd.DataFrame({
        "open_log_p": grp["_log_p"].first(),
        "close_log_p": grp["_log_p"].last(),
        "dvol": grp["_dvol"].sum(),
    })
    bar["abs_ret"] = (bar["close_log_p"] - bar["open_log_p"]).abs()
    bar = bar[bar["dvol"] > 0]
    if len(bar) == 0:
        return float("nan")
    illiq_per_bar = bar["abs_ret"] / bar["dvol"]
    return float(illiq_per_bar.mean() * scale)


# ──────────────────────────────────────────────────────────────────────
# Sweep / dark-pool / odd-lot fixes
# ──────────────────────────────────────────────────────────────────────


def _condition_contains(conditions: pd.Series, code: str) -> pd.Series:
    """Return boolean Series where conditions string contains the given code.

    Conditions in Polygon trades are comma-separated strings like '12,37'.
    Match is exact code-token (so '14' matches '14' and '12,14' but not '141').
    """
    if conditions.dtype == object:
        # Convert NaN to empty string then split
        c = conditions.fillna("")
        return c.apply(lambda s: code in str(s).split(",") if s else False)
    return pd.Series([False] * len(conditions), index=conditions.index)


def compute_iso_sweep_count(trades_df: pd.DataFrame,
                            conditions_col: str = "conditions") -> int:
    """Count of Intermarket Sweep Orders (condition code 14).

    Fixes the bug in `build_microstructure_features_v2.py` which used
    code '15' (Average Price Trade), returning all zeros.
    """
    if conditions_col not in trades_df.columns:
        return 0
    return int(_condition_contains(trades_df[conditions_col], ISO_CONDITION_CODE).sum())


# ──────────────────────────────────────────────────────────────────────
# Per-day pack: run all features at once
# ──────────────────────────────────────────────────────────────────────


def compute_v6_microstructure_pack(trades_df: pd.DataFrame,
                                   ts_col: str = "ts_et",
                                   price_col: str = "price",
                                   size_col: str = "size",
                                   conditions_col: str = "conditions") -> dict:
    """Compute all 6 v6 features for one (ticker, d0) trades DataFrame.

    Returns a dict with keys:
      - vpin_d0
      - ofi_first30_d0
      - kyle_lambda_d0
      - hawkes_fano_d0
      - amihud_illiq_d0
      - iso_sweep_count_d0   (fix for v2 builder bug)

    Caller is responsible for filtering trades_df to RTH and a single
    (ticker, date) before calling. This function does NOT cross-validate
    the input — passing multi-day data will silently produce wrong VPIN
    (cumulative volume crosses day boundary).
    """
    df = trades_df.sort_values(ts_col).reset_index(drop=True)
    return {
        "vpin_d0":            compute_vpin(df, size_col=size_col, price_col=price_col),
        "ofi_first30_d0":     compute_ofi_window(df, ts_col=ts_col,
                                                 start_min=0, end_min=30,
                                                 size_col=size_col, price_col=price_col),
        "kyle_lambda_d0":     compute_kyle_lambda(df, ts_col=ts_col,
                                                  size_col=size_col, price_col=price_col),
        "hawkes_fano_d0":     compute_hawkes_fano(df, ts_col=ts_col),
        "amihud_illiq_d0":    compute_amihud_illiquidity(df, ts_col=ts_col,
                                                          size_col=size_col, price_col=price_col),
        "iso_sweep_count_d0": compute_iso_sweep_count(df, conditions_col=conditions_col),
    }
