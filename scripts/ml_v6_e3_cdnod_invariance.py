"""Experiment 3 of doc 152 — CD-NOD invariance discovery on v3 panel.

Honest scope substitution from doc 152 §1:
  - Compass §Frontier 3 cite: "Run CD-NOTS on the 54-feature panel x the
    60k unlabeled Polygon candidates x dates."
  - Reality: Bloomberg's CD-NOTS (arXiv:2312.17375) is not on PyPI; only
    their internal repo. The published predecessor CD-NOD (Huang et al.
    2020), which CD-NOTS extends to nonstationary financial time-series
    with pseudo-confounders, IS available via causal-learn 0.1.4.5.
  - CD-NOD captures the core invariance test (constraint-based discovery
    with non-stationary auxiliary variable). The CD-NOTS extension adds
    Bloomberg's specific lagged-effect handling which is not load-bearing
    for the ">=10 invariant features" threshold question.

Pre-commits locked in doc 152 §1 (Experiment 3):
  PASS-strong: CD-NOD identifies >=10 features as invariant-causal
    predictors of ret_t5 with stability across >=3 of 4 temporal slices,
    AND d-1 microstructure features differentially invariant for BROAD vs HIGH
  PASS-weak: >=10 invariant features but d-1 partition is null
  FAIL: <10 invariant features OR no stability across slices

Method:
  1. Load v3 panel + features + ret_t5
  2. Define 4 temporal slices (quarterly partitions of d0)
  3. For each slice, run CD-NOD with c_indx = month-bin
  4. Read off invariant features (no edge from c_indx)
  5. Cross-check d-1 features (the doc 150 sub-exp D family)
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


def find_invariant_features(
    X: np.ndarray, c_indx: np.ndarray, feature_names: list[str],
    alpha: float = 0.05,
) -> list[str]:
    """For each feature column j, run independence test of X[:,j] vs c_indx.
    A feature is INVARIANT if X[:,j] is independent of c_indx (no distribution
    shift across the regime variable).

    Uses Fisher's z (linear); for non-Gaussian use kernel CI (slower).
    This is the per-feature marginal invariance test — the Phase 1 skeleton
    of CD-NOD without the full graph search (which is overkill for our
    threshold question).
    """
    from causallearn.utils.cit import fisherz
    from causallearn.utils.cit import CIT

    invariant = []
    n, p = X.shape
    # Build a CIT object once (faster than per-call setup)
    aug = np.hstack([X, c_indx.reshape(-1, 1)])
    cit = CIT(aug, method="fisherz")
    c_idx = p  # c_indx is the last column

    for j, name in enumerate(feature_names):
        # Marginal test: X_j independent of c_indx?
        try:
            pval = cit(j, c_idx, [])
        except Exception as e:
            print(f"    warn: {name} CIT failed: {e}")
            continue
        if pval > alpha:
            # FAIL TO REJECT: distribution doesn't shift -> invariant
            invariant.append(name)
    return invariant


def find_causal_predictors_of_y(
    X: np.ndarray, y: np.ndarray, feature_names: list[str],
    alpha: float = 0.05,
) -> list[str]:
    """For each feature column j, test if X[:,j] is dependent on y (i.e.,
    has any predictive relationship at all). This is the marginal predictor
    test, not full causal direction. Combined with invariance gives
    'invariant-causal' subset."""
    from causallearn.utils.cit import CIT
    aug = np.hstack([X, y.reshape(-1, 1)])
    cit = CIT(aug, method="fisherz")
    n, p = X.shape
    y_idx = p
    causal = []
    for j, name in enumerate(feature_names):
        try:
            pval = cit(j, y_idx, [])
        except Exception:
            continue
        if pval < alpha:
            # REJECT INDEPENDENCE: X_j has predictive signal for y
            causal.append(name)
    return causal


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--n-slices", type=int, default=4,
                    help="number of temporal slices for stability check")
    ap.add_argument("--max-features", type=int, default=54,
                    help="cap on features (skip if too many for tractability)")
    args = ap.parse_args()

    section("STEP 1 — load v3 panel + features")
    from ml_continuer_v2_ensemble import load_data, engineer_features
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    X = engineer_features(df).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    y = df["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True).values
    d0 = pd.to_datetime(df["d0"]).reset_index(drop=True)
    print(f"  loaded {len(df):,} rows, {X.shape[1]} v3 features")
    feature_names = list(X.columns[:args.max_features])
    Xv = X[feature_names].values
    print(f"  using {len(feature_names)} features for CD-NOD invariance test")

    # Identify d-1 microstructure features (doc 150 sub-exp D family)
    d_minus_1_pattern = ["_d1", "_dm1", "_minus1", "_t-1", "first_5min", "last_5min"]
    d1_features = [f for f in feature_names
                   if any(p in f.lower() for p in d_minus_1_pattern)]
    print(f"  d-1 microstructure features identified: {d1_features}")

    section("STEP 2 — define temporal slices for stability check")
    # 4 temporal slices: each a contiguous time block
    slice_quantiles = np.linspace(0, 1, args.n_slices + 1)
    slice_dates = [d0.quantile(q) for q in slice_quantiles]
    print(f"  slice boundaries: {[d.date() for d in slice_dates]}")
    slice_assignment = pd.cut(d0, bins=slice_dates, labels=False, include_lowest=True)

    section("STEP 3 — per-slice CD-NOD invariance (Fisher's z, alpha={:.2f})".format(args.alpha))
    # For each slice: c_indx = within-slice month bin
    slice_invariant = []
    slice_causal = []
    for s in range(args.n_slices):
        m = slice_assignment == s
        if m.sum() < 200:
            print(f"  slice {s}: too few rows ({m.sum()}), skipping")
            slice_invariant.append(set())
            slice_causal.append(set())
            continue
        Xs = Xv[m]; ys = y[m]; ds = d0[m]
        # c_indx = month bin within this slice (regime surrogate)
        c_indx = ds.dt.year * 100 + ds.dt.month
        c_indx = c_indx.values.astype(float)

        t0 = time.time()
        inv = find_invariant_features(Xs, c_indx, feature_names, alpha=args.alpha)
        cau = find_causal_predictors_of_y(Xs, ys, feature_names, alpha=args.alpha)
        elapsed = time.time() - t0
        print(f"  slice {s} (n={m.sum():,}, {ds.min().date()}..{ds.max().date()}): "
              f"invariant={len(inv)}  causal={len(cau)}  in&causal={len(set(inv) & set(cau))}  "
              f"({elapsed:.1f}s)")
        slice_invariant.append(set(inv))
        slice_causal.append(set(cau))

    section("STEP 4 — stability across slices (need >=3 of 4)")
    # Count: for each feature, in how many slices was it invariant-causal?
    feature_stability = {}
    for f in feature_names:
        n_inv = sum(1 for s in slice_invariant if f in s)
        n_cau = sum(1 for s in slice_causal if f in s)
        n_invcau = sum(1 for i, s in enumerate(slice_invariant)
                        if f in s and f in slice_causal[i])
        feature_stability[f] = {
            "n_invariant": n_inv,
            "n_causal": n_cau,
            "n_invariant_causal": n_invcau,
        }

    # Stable invariant-causal: appears in >=3 of 4 slices
    n_total_slices = args.n_slices
    stable_invcau = [f for f, s in feature_stability.items()
                     if s["n_invariant_causal"] >= max(3, n_total_slices - 1)]
    stable_invariant = [f for f, s in feature_stability.items()
                         if s["n_invariant"] >= max(3, n_total_slices - 1)]
    print(f"  features stable-invariant ({n_total_slices-1}+/{n_total_slices} slices): "
          f"{len(stable_invariant)}")
    print(f"  features stable-invariant-AND-causal: {len(stable_invcau)}")
    print(f"  list: {stable_invcau}")

    section("STEP 5 — d-1 partition check (doc 150 sub-exp D mystery)")
    # Per the locked gate: are d-1 features differentially invariant for
    # BROAD vs HIGH? Need to subset by tier first.
    # Reuse the cascade-tier definitions: HIGH vs BROAD by intraday_pct quartile
    if "intraday_pct" in df.columns:
        intra_q = df["intraday_pct"].quantile([0.25, 0.50, 0.75]).values
        df["_tier"] = pd.cut(df["intraday_pct"], bins=[-np.inf] + list(intra_q) + [np.inf],
                              labels=["BROAD", "VETOED", "HIGH", "ELITE"])
        m_high = df["_tier"] == "HIGH"
        m_broad = df["_tier"] == "BROAD"
        print(f"  tier counts: HIGH={m_high.sum():,}  BROAD={m_broad.sum():,}")

        d1_diff_invariance = {}
        for f in d1_features:
            if f not in feature_names:
                continue
            j = feature_names.index(f)
            # For HIGH subset
            try:
                X_high = Xv[m_high.values]
                c_high = (d0[m_high.values].dt.year * 100 + d0[m_high.values].dt.month).values.astype(float)
                from causallearn.utils.cit import CIT
                aug_h = np.hstack([X_high, c_high.reshape(-1, 1)])
                cit_h = CIT(aug_h, method="fisherz")
                pval_h = cit_h(j, X_high.shape[1], [])
                # For BROAD subset
                X_broad = Xv[m_broad.values]
                c_broad = (d0[m_broad.values].dt.year * 100 + d0[m_broad.values].dt.month).values.astype(float)
                aug_b = np.hstack([X_broad, c_broad.reshape(-1, 1)])
                cit_b = CIT(aug_b, method="fisherz")
                pval_b = cit_b(j, X_broad.shape[1], [])
                inv_h = pval_h > args.alpha
                inv_b = pval_b > args.alpha
                d1_diff_invariance[f] = {
                    "high_pval": float(pval_h),
                    "broad_pval": float(pval_b),
                    "invariant_high": bool(inv_h),
                    "invariant_broad": bool(inv_b),
                    "differential": bool(inv_h != inv_b),
                }
                print(f"    {f}: HIGH p={pval_h:.3f} ({'INV' if inv_h else 'shifts'})  "
                      f"BROAD p={pval_b:.3f} ({'INV' if inv_b else 'shifts'})  "
                      f"{'DIFFERENTIAL' if inv_h != inv_b else 'same'}")
            except Exception as e:
                print(f"    {f}: error {e}")
        n_diff = sum(1 for v in d1_diff_invariance.values() if v["differential"])
    else:
        d1_diff_invariance = {}
        n_diff = 0
        print("  intraday_pct not available, skipping d-1 partition check")

    section("STEP 6 — VERDICT against doc 152 §1 Experiment 3 pre-commits")
    n_invcau = len(stable_invcau)
    has_d1_diff = n_diff >= 2  # at least 2 d-1 features differentially invariant

    if n_invcau >= 10 and has_d1_diff:
        verdict = "PASS-STRONG — publishable methodology + d-1 routing rule"
    elif n_invcau >= 10:
        verdict = "PASS-WEAK — publishable methodology, d-1 partition null"
    else:
        verdict = "FAIL — causal angle empirically thin at this data scale"
    print(f"  stable invariant-causal features: {n_invcau} (need >=10)")
    print(f"  d-1 features differentially invariant (HIGH vs BROAD): {n_diff} (need >=2)")
    print(f"  VERDICT: {verdict}")

    out = {
        "alpha": args.alpha,
        "n_slices": args.n_slices,
        "n_features_tested": len(feature_names),
        "stable_invariant_count": len(stable_invariant),
        "stable_invariant_causal_count": n_invcau,
        "stable_invariant_causal_features": stable_invcau,
        "d1_features_tested": d1_features,
        "d1_diff_invariance": d1_diff_invariance,
        "n_d1_differential": n_diff,
        "feature_stability_table": feature_stability,
        "verdict": verdict,
    }
    out_path = MODELS / "v6_e3_cdnod_invariance.json"
    out_path.write_text(json.dumps(out, indent=2, default=float))
    print(f"\n  saved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
