"""ITEM 4 — CORN ordinal head on v3 features in isolation.

Per user critique on doc 138:
  "Run CORN in isolation, on v3 features only, under Phase 0 hygiene,
  and see what it does."

The v5 program (doc 132 negative) bundled CORN with Muon optimizer + EMA
weights + Mixup augmentation + Spearman soft-rank loss. The bundle
underperformed v3 (Spearman 0.041 vs v4's 0.141). The user's pushback:
that's a bundle-effect confound; CORN itself might be the right v5 item
and was thrown out with the rest.

This script tests CORN ALONE on v3 features:
  - Vanilla MLP backbone (3 layers: 54 -> 256 -> 128 -> 64)
  - CORN head with 4 ordinal thresholds (matches BROAD/VETOED/HIGH/ELITE)
  - Vanilla AdamW optimizer, no Muon/EMA/Mixup
  - 12-fold WF same as Item 1 CPU control
  - Metric: P(R>=0.10) as ranking score; per-tier P@30 from cumulative probs

Decision criterion (relative to v3 XGBoost CPU baseline):
  - Spearman: does CORN beat v3 XGBoost +0.179 (Item 5b baseline)?
  - Per-tier P@30: does CORN improve specifically on ELITE / HIGH where the
    v3 binary-classifier cascade has the worst data starvation?
  - Importantly: does CORN beat XGBoost regression on the SAME (CPU-deterministic) folds?

Composes orthogonally with Item 5b (TabICL): if TabICL says model-class
ceiling, CORN tests whether the answer involves training-objective change
or only foundation-model change. Both could be true.

USAGE:
  python scripts/ml_v6_item4_corn_isolation.py
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from scipy.stats import spearmanr

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


# ──────────────────────────────────────────────────────────────────────
# CORN-only model (vanilla MLP backbone, no Muon/EMA/Mixup)
# ──────────────────────────────────────────────────────────────────────


class CORNModelV3(nn.Module):
    """3-layer MLP + CORN head. No fancy optimization. Apples-to-apples
    with v3 XGBoost's level of training simplicity."""
    def __init__(self, in_features: int, n_thresholds: int = 4, hidden: int = 256):
        super().__init__()
        self.backbone = nn.Sequential(
            nn.Linear(in_features, hidden),
            nn.LayerNorm(hidden),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(hidden, hidden // 2),
            nn.LayerNorm(hidden // 2),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(hidden // 2, hidden // 4),
            nn.GELU(),
        )
        self.corn_head = nn.Linear(hidden // 4, n_thresholds)
        self.n_thresholds = n_thresholds

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.backbone(x)
        cond_logits = self.corn_head(h)  # (B, K)
        return cond_logits

    def cumulative_probs(self, conditional_logits: torch.Tensor) -> torch.Tensor:
        cond_probs = torch.sigmoid(conditional_logits)
        return torch.cumprod(cond_probs, dim=1)  # (B, K)


def corn_loss(conditional_logits: torch.Tensor, y_ordinal: torch.Tensor,
                n_thresholds: int) -> torch.Tensor:
    levels = torch.arange(n_thresholds, device=y_ordinal.device).unsqueeze(0)
    in_scope = (y_ordinal.unsqueeze(1) >= levels).float()
    target = (y_ordinal.unsqueeze(1) >= levels + 1).float()
    bce = F.binary_cross_entropy_with_logits(
        conditional_logits, target, reduction="none",
    )
    masked = bce * in_scope
    return masked.sum() / in_scope.sum().clamp(min=1.0)


def encode_ordinal(y: pd.Series, thresholds=(0.10, 0.15, 0.25, 0.40)) -> np.ndarray:
    """Convert ret_t5 to ordinal level in [0, len(thresholds)]."""
    arr = np.zeros(len(y), dtype=np.int64)
    for k, thr in enumerate(thresholds, start=1):
        arr[(y >= thr).values] = k
    return arr


# ──────────────────────────────────────────────────────────────────────
# Training loop (one fold)
# ──────────────────────────────────────────────────────────────────────


def train_one_fold(Xtr: np.ndarray, ytr_ord: np.ndarray,
                    Xte: np.ndarray, yte: np.ndarray,
                    n_epochs: int = 60, batch_size: int = 256, lr: float = 1e-3,
                    seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    """Train CORN model on one fold; return cumulative_probs on test set."""
    torch.manual_seed(seed)
    np.random.seed(seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    in_dim = Xtr.shape[1]
    model = CORNModelV3(in_dim, n_thresholds=4).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=n_epochs)
    # Standardize per-fold (train statistics)
    mu = Xtr.mean(axis=0); sd = Xtr.std(axis=0).clip(min=1e-6)
    Xtr_n = ((Xtr - mu) / sd).astype("float32")
    Xte_n = ((Xte - mu) / sd).astype("float32")
    Xtr_t = torch.from_numpy(Xtr_n).to(device)
    Xte_t = torch.from_numpy(Xte_n).to(device)
    ytr_t = torch.from_numpy(ytr_ord.astype("int64")).to(device)
    n = len(Xtr_t)

    model.train()
    for epoch in range(n_epochs):
        perm = torch.randperm(n, device=device)
        for i in range(0, n, batch_size):
            idx = perm[i:i + batch_size]
            cond_logits = model(Xtr_t[idx])
            loss = corn_loss(cond_logits, ytr_t[idx], n_thresholds=4)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
        sched.step()

    model.eval()
    with torch.no_grad():
        cond_logits = model(Xte_t)
        cum_probs = model.cumulative_probs(cond_logits)  # (B, 4)
    return cum_probs.cpu().numpy(), cond_logits.cpu().numpy()


# ──────────────────────────────────────────────────────────────────────
# Helpers (neutralization, exposures) — mirror Item 5b
# ──────────────────────────────────────────────────────────────────────


def neutralized_rho(y_pred: np.ndarray, y_true: np.ndarray,
                    exposures: pd.DataFrame, proportion: float = 1.0) -> float:
    F_ = exposures.values.astype(float)
    F_ = F_ - F_.mean(axis=0)
    F_pinv = np.linalg.pinv(F_)
    p = y_pred.astype(float)
    p_proj = F_ @ (F_pinv @ p)
    p_neut = p - proportion * p_proj
    if p_neut.std() > 0:
        p_neut = p_neut / p_neut.std()
    return float(spearmanr(y_true, p_neut).statistic)


def build_exposures(df: pd.DataFrame) -> pd.DataFrame:
    n = len(df)
    exp = pd.DataFrame({
        "log_market_cap": np.log1p(df.get("market_cap", pd.Series(np.zeros(n))).fillna(0)),
        "log_dvol_d0":    np.log1p(df.get("dvol_d0", pd.Series(np.zeros(n))).fillna(0)),
        "prior_avg_t5":   df.get("prior_avg_t5", pd.Series(np.zeros(n))).fillna(0),
        "intraday_pct":   df.get("intraday_pct", pd.Series(np.zeros(n))).fillna(0),
    })
    if "sic_description" in df.columns:
        sic = df["sic_description"].fillna("").str.lower()
        for sec, key in [("pharma", "pharm"), ("bio", "bio"),
                          ("medical", "medic"), ("software", "software"),
                          ("finance", "financ"), ("semi", "semicond"),
                          ("spac", "spac"), ("reit", "reit")]:
            exp[f"sector_{sec}"] = sic.str.contains(key).astype(float)
    return exp


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────


def main() -> int:
    section("STEP 1 — load v3 base data")
    from ml_continuer_v2_ensemble import load_data, engineer_features
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    print(f"  loaded {len(df):,} rows")
    X = engineer_features(df).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    y_reg = df["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True)
    d0 = pd.to_datetime(df["d0"]).reset_index(drop=True)
    y_ord = encode_ordinal(y_reg)  # 0..4 ordinal level
    print(f"  X: {X.shape}  y_reg: {y_reg.shape}  ordinal levels: {np.bincount(y_ord)}")

    section("STEP 2 — 12-fold WF: CORN MLP, vanilla AdamW, no Muon/EMA/Mixup")
    n_folds = 12
    train_days = 120
    test_days = 15
    d_min, d_max = d0.min(), d0.max()
    fold_starts = pd.date_range(
        d_min + pd.Timedelta(days=train_days),
        d_max - pd.Timedelta(days=test_days), periods=n_folds,
    )
    rows = []
    for fold_i, fs in enumerate(fold_starts):
        tr = (d0 >= fs - pd.Timedelta(days=train_days)) & (d0 < fs)
        te = (d0 >= fs) & (d0 < fs + pd.Timedelta(days=test_days))
        if tr.sum() < 100 or te.sum() < 5: continue
        Xtr, Xte = X.loc[tr].values, X.loc[te].values
        yord_tr = y_ord[tr.values]
        yreg_te = y_reg.loc[te].values
        t0 = time.time()
        try:
            cum_probs, cond_logits = train_one_fold(Xtr, yord_tr, Xte, yreg_te,
                                                     n_epochs=60, lr=1e-3, seed=42 + fold_i)
        except Exception as e:
            print(f"  fold {fold_i:>2}: error {e}")
            continue
        # Use P(R >= 0.10) — the broadest cumulative prob — as ranking score
        rank_score = cum_probs[:, 0]
        rho = float(spearmanr(yreg_te, rank_score).statistic)
        elapsed = time.time() - t0
        rows.append(pd.DataFrame({
            "fold": fold_i, "d0": d0.loc[te].values,
            "y_true": yreg_te,
            "p_broad": cum_probs[:, 0],
            "p_vetoed": cum_probs[:, 1],
            "p_high": cum_probs[:, 2],
            "p_elite": cum_probs[:, 3],
            "y_pred": rank_score,  # alias for downstream
            "_idx": np.where(te)[0],
        }))
        print(f"  fold {fold_i:>2}: tr={tr.sum():>5}  te={te.sum():>4}  "
              f"Spearman={rho:+.3f}  ({elapsed:.0f}s)", flush=True)

    if not rows:
        print("  no successful folds — abort")
        return 1

    section("STEP 3 — overall metrics + per-tier P@30")
    preds = pd.concat(rows, ignore_index=True)
    rho_overall = float(spearmanr(preds["y_true"], preds["y_pred"]).statistic)
    print(f"  CORN OOS rows: {len(preds):,}")
    print(f"  CORN overall Spearman: {rho_overall:+.4f}")

    exp = build_exposures(df)
    exp_aligned = exp.iloc[preds["_idx"].values].reset_index(drop=True)
    rho_neut = neutralized_rho(preds["y_pred"].values, preds["y_true"].values,
                                exp_aligned, 1.0)
    print(f"  CORN neutralized rho:  {rho_neut:+.4f}")
    print()

    # Per-tier P@30 from CORN's per-tier prob columns (no separate classifier needed!)
    # This is what makes CORN structurally elegant for cascade tasks.
    print("  CORN per-tier P@30 (from cumulative_probs columns):")
    print(f"  {'tier':<8} {'thr':>5} {'pos%':>6} {'P@30 from CORN':>18}")
    print("  " + "-" * 55)
    TIERS_THR = {"BROAD": 0.10, "VETOED": 0.15, "HIGH": 0.25, "ELITE": 0.40}
    TIER_COLS = {"BROAD": "p_broad", "VETOED": "p_vetoed", "HIGH": "p_high", "ELITE": "p_elite"}
    tier_p30 = {}
    for tier, thr in TIERS_THR.items():
        col = TIER_COLS[tier]
        # Per-fold P@30
        p30s = []
        pos_rates = []
        for fold_i in preds["fold"].unique():
            sub = preds[preds["fold"] == fold_i]
            y_bin = (sub["y_true"] >= thr).astype(int)
            pos_rates.append(y_bin.mean())
            k = min(30, len(sub))
            top_idx = np.argsort(sub[col].values)[-k:]
            p30s.append(y_bin.values[top_idx].mean())
        tier_p30[tier] = float(np.mean(p30s))
        print(f"  {tier:<8} {thr:>5.2f} {np.mean(pos_rates)*100:>5.1f}% {tier_p30[tier]:>18.4f}")

    section("STEP 4 — head-to-head vs v3 XGBoost CPU baseline (same folds)")
    import xgboost as xgb
    XGB_PARAMS = {
        "objective": "reg:squarederror", "n_estimators": 600, "max_depth": 5,
        "learning_rate": 0.03, "subsample": 0.8, "colsample_bytree": 0.6,
        "min_child_weight": 5, "reg_alpha": 0.5, "reg_lambda": 1.0,
        "tree_method": "hist", "device": "cpu", "n_jobs": 1, "random_state": 42,
    }
    PARAMS_BIN = dict(XGB_PARAMS)
    PARAMS_BIN["objective"] = "binary:logistic"
    PARAMS_BIN["eval_metric"] = "logloss"

    xgb_rows = []
    xgb_tier_p30 = {}
    for fold_i, fs in enumerate(fold_starts):
        tr = (d0 >= fs - pd.Timedelta(days=train_days)) & (d0 < fs)
        te = (d0 >= fs) & (d0 < fs + pd.Timedelta(days=test_days))
        if tr.sum() < 100 or te.sum() < 5: continue
        # XGBoost regression (for Spearman comparison)
        m = xgb.XGBRegressor(**XGB_PARAMS)
        m.fit(X.loc[tr], y_reg.loc[tr], verbose=False)
        pred = m.predict(X.loc[te])
        xgb_rows.append(pd.DataFrame({
            "fold": fold_i, "d0": d0.loc[te].values,
            "y_true": y_reg.loc[te].values, "y_pred": pred,
            "_idx": np.where(te)[0],
        }))

    # Per-tier XGBoost P@30 (4 separate binary classifiers)
    for tier, thr in TIERS_THR.items():
        y_bin = (y_reg >= thr).astype(int)
        p30s = []
        for fold_i, fs in enumerate(fold_starts):
            tr = (d0 >= fs - pd.Timedelta(days=train_days)) & (d0 < fs)
            te = (d0 >= fs) & (d0 < fs + pd.Timedelta(days=test_days))
            if tr.sum() < 100 or te.sum() < 30 or y_bin.loc[tr].sum() < 5: continue
            m = xgb.XGBClassifier(**PARAMS_BIN)
            m.fit(X.loc[tr], y_bin.loc[tr], verbose=False)
            p = m.predict_proba(X.loc[te])[:, 1]
            k = min(30, te.sum())
            top_idx = np.argsort(p)[-k:]
            p30s.append(y_bin.loc[te].values[top_idx].mean())
        xgb_tier_p30[tier] = float(np.mean(p30s))

    xgb_preds = pd.concat(xgb_rows, ignore_index=True)
    xgb_rho = float(spearmanr(xgb_preds["y_true"], xgb_preds["y_pred"]).statistic)
    xgb_exp = exp.iloc[xgb_preds["_idx"].values].reset_index(drop=True)
    xgb_neut = neutralized_rho(xgb_preds["y_pred"].values, xgb_preds["y_true"].values,
                                xgb_exp, 1.0)
    print(f"  v3 XGBoost overall Spearman: {xgb_rho:+.4f}")
    print(f"  v3 XGBoost neutralized rho:  {xgb_neut:+.4f}")
    print()

    print("  HEAD-TO-HEAD (CORN vs v3 XGBoost on same CPU folds):")
    print(f"    Spearman      CORN={rho_overall:+.4f}  XGB={xgb_rho:+.4f}  delta={rho_overall-xgb_rho:+.4f}")
    print(f"    Neutralized   CORN={rho_neut:+.4f}  XGB={xgb_neut:+.4f}  delta={rho_neut-xgb_neut:+.4f}")
    print(f"  Per-tier P@30:")
    print(f"  {'tier':<8} {'CORN':>8} {'XGB':>8} {'delta':>10}")
    for tier in TIERS_THR:
        c = tier_p30[tier]; x = xgb_tier_p30[tier]
        print(f"  {tier:<8} {c:>8.4f} {x:>8.4f} {c-x:>+10.4f}")

    section("STEP 5 — verdict")
    spear_lift = rho_overall - xgb_rho
    neut_lift = rho_neut - xgb_neut
    elite_lift = tier_p30["ELITE"] - xgb_tier_p30["ELITE"]
    print(f"  CORN Spearman lift over v3 XGBoost: {spear_lift:+.4f}")
    print(f"  CORN ELITE P@30 lift over v3 XGB:   {elite_lift:+.4f}")
    if spear_lift > 0.01 or elite_lift > 0.02:
        print(f"  -> CORN ALONE adds value beyond v3 XGBoost.")
        print(f"  -> The v5 'CORN bundle failed' result was a bundle-effect confound.")
        print(f"  -> The user's prediction was correct: throwing CORN out was wrong.")
    else:
        print(f"  -> CORN alone does NOT meaningfully beat v3 XGBoost.")
        print(f"  -> Training-objective change (regression->ordinal) is not the bottleneck.")
        print(f"  -> The v5 negative result reflected CORN's actual contribution, not bundle effects.")

    out = {
        "corn_spearman_overall": rho_overall,
        "corn_neutralized_rho": rho_neut,
        "corn_per_tier_p30": tier_p30,
        "v3_xgb_spearman_overall": xgb_rho,
        "v3_xgb_neutralized_rho": xgb_neut,
        "v3_xgb_per_tier_p30": xgb_tier_p30,
        "spearman_lift": spear_lift,
        "neutralized_lift": neut_lift,
        "elite_p30_lift": elite_lift,
    }
    out_path = MODELS / "v6_item4_corn_isolation.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {out_path}")
    preds.to_parquet(DERIVED / "ml_v6_item4_corn_preds.parquet", compression="zstd")
    return 0


if __name__ == "__main__":
    sys.exit(main())
