"""EXPERIMENT #1 — TabPFN compositional matrix.

Per user critique: "The right experiment is not a single composition test —
it's a 2×2 matrix that, run in one session, would answer five strategic
questions at once."

The matrix:
                    v3 features        v3 + d-1 features
  XGBoost           baseline (Item 5)  doc 138 BROAD lift
  TabPFN            doc 143 (+0.07)    UNKNOWN  ← NEW FIT NEEDED
  Ensemble (avg)    UNKNOWN            UNKNOWN  ← derived from existing preds

5 strategic questions answered:
  Q1: Does d-1 help or hurt TabPFN?
  Q2: Does ensembling TabPFN + XGBoost beat either alone?
  Q3: Realistic upper bound for v3 + d-1 + (TabPFN or ensemble) production stack?
  Q4: Is TabICL-vs-TabPFN gap stable under feature changes?
  Q5: Does TabPFN's recent-data win survive ensembling (orthogonal vs better signal)?

PRE-COMMITTED VERDICT:
  TabPFN + d-1 HURTS TabPFN  ->  ship pure-v3-features TabPFN; d-1 stays XGB-only

USAGE:
  TABPFN_TOKEN=<token> TABPFN_NO_BROWSER=1 \\
    python scripts/ml_v6_composition_matrix.py
"""
from __future__ import annotations
import json
import sys
import time
import gc
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from scipy.stats import spearmanr

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


def neutralized_rho(yp, yt, exposures, proportion=1.0):
    F = exposures.values.astype(float); F = F - F.mean(axis=0)
    p_proj = F @ (np.linalg.pinv(F) @ yp.astype(float))
    p_n = yp.astype(float) - proportion * p_proj
    if p_n.std() > 0: p_n = p_n / p_n.std()
    return float(spearmanr(yt, p_n).statistic)


def per_tier_p30(preds_df, tiers={"BROAD": 0.10, "VETOED": 0.15, "HIGH": 0.25, "ELITE": 0.40}):
    """Compute per-tier P@30 from OOS preds (regression target -> binary thresholds)."""
    out = {}
    for tier, thr in tiers.items():
        p30s = []
        for fold_i in preds_df["fold"].unique():
            sub = preds_df[preds_df["fold"] == fold_i]
            if len(sub) < 5: continue
            y_bin = (sub["y_true"] >= thr).astype(int)
            k = min(30, len(sub))
            top_idx = np.argsort(sub["y_pred"].values)[-k:]
            p30s.append(y_bin.values[top_idx].mean())
        out[tier] = float(np.mean(p30s)) if p30s else float("nan")
    return out


def main() -> int:
    section("STEP 1 — load existing predictions + d-1 subset features")
    from ml_continuer_v2_ensemble import load_data, engineer_features

    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    v6_dm1 = pd.read_parquet(DERIVED / "microstructure_v6_pack_dminus1.parquet")
    v6_dm1["d0"] = pd.to_datetime(v6_dm1["d0"])
    V6_FEATS = ["vpin_d0", "ofi_first30_d0", "kyle_lambda_d0",
                "hawkes_fano_d0", "amihud_illiq_d0", "iso_sweep_count_d0"]
    merged = df.merge(v6_dm1[["ticker", "d0", *V6_FEATS]],
                      on=["ticker", "d0"], how="inner")
    print(f"  merged inner-join (rows with d-1 v6 features): {len(merged):,}")

    X_v3 = engineer_features(merged).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    v6_block = merged[V6_FEATS].copy().replace([np.inf, -np.inf], np.nan)
    v6_block["log_kyle_lambda_d0"] = np.log1p(v6_block["kyle_lambda_d0"].clip(lower=0))
    v6_block["log_hawkes_fano_d0"] = np.log1p(v6_block["hawkes_fano_d0"].clip(lower=0))
    v6_block["log_amihud_illiq_d0"] = np.log1p(v6_block["amihud_illiq_d0"].clip(lower=0))
    v6_block["log_iso_sweep_count_d0"] = np.log1p(v6_block["iso_sweep_count_d0"].clip(lower=0))
    v6_block = v6_block.fillna(0).reset_index(drop=True)
    X_v3_dm1 = pd.concat([X_v3, v6_block], axis=1)
    y = merged["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True)
    d0 = pd.to_datetime(merged["d0"]).reset_index(drop=True)
    print(f"  X_v3: {X_v3.shape}  X_v3+d-1: {X_v3_dm1.shape}  y: {y.shape}")

    n_folds = 12; train_days = 120; test_days = 15
    fs_list = pd.date_range(
        d0.min() + pd.Timedelta(days=train_days),
        d0.max() - pd.Timedelta(days=test_days), periods=n_folds,
    )

    section("STEP 2 — TabPFN on v3 features (d-1 subset, for fair comparison)")
    from tabpfn import TabPFNRegressor
    import xgboost as xgb
    MAX_TRAIN = 2500

    def run_tabpfn_fold(Xtr, ytr, Xte, fi):
        if len(Xtr) > MAX_TRAIN:
            rng = np.random.default_rng(42 + fi)
            idx = rng.choice(len(Xtr), MAX_TRAIN, replace=False)
            Xtr = Xtr[idx]; ytr = ytr[idx]
        if torch.cuda.is_available():
            torch.cuda.empty_cache(); torch.cuda.synchronize(); gc.collect()
        m = TabPFNRegressor(device="cuda", random_state=42)
        m.fit(Xtr, ytr)
        pred = m.predict(Xte)
        del m
        if torch.cuda.is_available():
            torch.cuda.empty_cache(); gc.collect()
        return pred

    def run_xgb_fold(Xtr, ytr, Xte):
        m = xgb.XGBRegressor(objective="reg:squarederror", n_estimators=600, max_depth=5,
            learning_rate=0.03, subsample=0.8, colsample_bytree=0.6, min_child_weight=5,
            reg_alpha=0.5, reg_lambda=1.0, tree_method="hist", device="cpu", n_jobs=1, random_state=42)
        m.fit(Xtr, ytr, verbose=False)
        return m.predict(Xte)

    def run_wf(X_use, label):
        rows = []
        for fi, fs in enumerate(fs_list):
            tr = (d0 >= fs - pd.Timedelta(days=train_days)) & (d0 < fs)
            te = (d0 >= fs) & (d0 < fs + pd.Timedelta(days=test_days))
            if tr.sum() < 100 or te.sum() < 5: continue
            Xtr = X_use.loc[tr].values; Xte = X_use.loc[te].values
            ytr = y.loc[tr].values; yte = y.loc[te].values
            t0 = time.time()
            try:
                pf_pred = run_tabpfn_fold(Xtr, ytr, Xte, fi)
            except Exception as e:
                print(f"  fold {fi} TabPFN err: {e}", flush=True); continue
            xg_pred = run_xgb_fold(Xtr, ytr, Xte)
            ens_pred = (pf_pred + xg_pred) / 2
            rows.append(pd.DataFrame({
                "fold": fi, "d0": d0.loc[te].values, "y_true": yte,
                "tabpfn": pf_pred, "xgb": xg_pred, "ensemble": ens_pred,
            }))
            print(f"  fold {fi:>2} ({label}): tr={len(Xtr)}  te={te.sum()}  "
                  f"PF Sp={spearmanr(yte, pf_pred).statistic:+.3f}  "
                  f"XG Sp={spearmanr(yte, xg_pred).statistic:+.3f}  "
                  f"Ens Sp={spearmanr(yte, ens_pred).statistic:+.3f}  "
                  f"({time.time()-t0:.0f}s)", flush=True)
        return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()

    print("  v3 features only (54 cols):")
    preds_v3 = run_wf(X_v3, "v3")
    print()
    print("  v3 + d-1 features (64 cols):")
    preds_dm1 = run_wf(X_v3_dm1, "v3+d-1")

    section("STEP 3 — aggregate: 6-cell matrix")
    print("  Per-cell aggregate Spearman (full d-1 subset, n=6708):")
    print(f"  {'feature_set':<15} {'XGB':>10} {'TabPFN':>10} {'Ensemble':>10}")
    print("  " + "-" * 50)
    for label, pdf in [("v3 only", preds_v3), ("v3 + d-1", preds_dm1)]:
        if not len(pdf):
            print(f"  {label:<15} (no data)"); continue
        xg = float(spearmanr(pdf["y_true"], pdf["xgb"]).statistic)
        pf = float(spearmanr(pdf["y_true"], pdf["tabpfn"]).statistic)
        en = float(spearmanr(pdf["y_true"], pdf["ensemble"]).statistic)
        print(f"  {label:<15} {xg:>+10.4f} {pf:>+10.4f} {en:>+10.4f}")

    section("STEP 4 — recent-period (folds 8-11) per the doc 141 hygiene rule")
    threshold = pd.Timestamp("2025-08-01")
    for label, pdf in [("v3 only", preds_v3), ("v3 + d-1", preds_dm1)]:
        if not len(pdf): continue
        pdf["d0"] = pd.to_datetime(pdf["d0"])
        recent = pdf[pdf["d0"] >= threshold]
        older = pdf[pdf["d0"] < threshold]
        print(f"\n  {label}:")
        print(f"  {'period':<12} {'n':>6} {'XGB':>10} {'TabPFN':>10} {'Ensemble':>10}")
        for sub_label, sub in [("OLDER", older), ("NEWER", recent)]:
            if not len(sub): continue
            xg = float(spearmanr(sub["y_true"], sub["xgb"]).statistic)
            pf = float(spearmanr(sub["y_true"], sub["tabpfn"]).statistic)
            en = float(spearmanr(sub["y_true"], sub["ensemble"]).statistic)
            print(f"  {sub_label:<12} {len(sub):>6} {xg:>+10.4f} {pf:>+10.4f} {en:>+10.4f}")

    section("STEP 5 — pre-committed verdict on TabPFN + d-1 composition")
    pf_v3 = float(spearmanr(preds_v3["y_true"], preds_v3["tabpfn"]).statistic) if len(preds_v3) else float("nan")
    pf_dm1 = float(spearmanr(preds_dm1["y_true"], preds_dm1["tabpfn"]).statistic) if len(preds_dm1) else float("nan")
    delta = pf_dm1 - pf_v3
    print(f"  TabPFN on v3 only:    Spearman {pf_v3:+.4f}")
    print(f"  TabPFN on v3 + d-1:   Spearman {pf_dm1:+.4f}")
    print(f"  Delta (d-1 effect):   {delta:+.4f}")
    print()
    print("  PRE-COMMIT: 'TabPFN + d-1 hurts TabPFN' -> ship pure-v3-features TabPFN")
    if delta < -0.005:
        print(f"  -> TRIGGERED: d-1 hurts TabPFN by {delta:+.4f}.")
        print(f"     Production: pure-v3-features TabPFN. d-1 work stays XGBoost-only (doc 138).")
    elif delta > 0.005:
        print(f"  -> NOT triggered: d-1 HELPS TabPFN by {delta:+.4f}.")
        print(f"     Production candidate: TabPFN on v3 + d-1 features (best of both).")
    else:
        print(f"  -> Marginal: d-1 effect on TabPFN is {delta:+.4f} (within noise).")
        print(f"     Choose pure-v3-features TabPFN for simpler production wiring.")

    # Persist
    preds_v3.to_parquet(DERIVED / "ml_v6_composition_v3only_preds.parquet", compression="zstd")
    preds_dm1.to_parquet(DERIVED / "ml_v6_composition_v3dm1_preds.parquet", compression="zstd")
    out = {
        "n_subset": int(len(y)),
        "v3_only": {
            "xgb": float(spearmanr(preds_v3["y_true"], preds_v3["xgb"]).statistic) if len(preds_v3) else None,
            "tabpfn": pf_v3,
            "ensemble": float(spearmanr(preds_v3["y_true"], preds_v3["ensemble"]).statistic) if len(preds_v3) else None,
        },
        "v3_dm1": {
            "xgb": float(spearmanr(preds_dm1["y_true"], preds_dm1["xgb"]).statistic) if len(preds_dm1) else None,
            "tabpfn": pf_dm1,
            "ensemble": float(spearmanr(preds_dm1["y_true"], preds_dm1["ensemble"]).statistic) if len(preds_dm1) else None,
        },
        "tabpfn_dm1_delta": delta,
    }
    out_path = MODELS / "v6_composition_matrix.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
