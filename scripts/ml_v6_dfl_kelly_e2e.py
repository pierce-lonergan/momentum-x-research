"""Doc 151 — Frontier 2: Decision-Focused End-to-End Learning (DFL).

Per compass research roadmap (Frontier 2):
  Replace MSE prediction loss with differentiable Sharpe/Kelly utility
  that backpropagates through the portfolio construction operator.
  Costa-Iyengar 2023 / Donti-Amos-Kolter 2017 / Wilder 2019 lineage.

THE PROBLEM DFL SOLVES:
  Current stack: predict(x) -> score -> tier -> Kelly fraction -> impact -> $-PnL
  Training: minimize MSE(predict(x), ret_t5)
  Deployment: maximize log(1 + Kelly_fraction * (ret_t5 - Bouchaud_impact))

  Training objective != deployment objective. The model optimizes for
  Spearman of returns, not for portfolio P&L after costs. DFL aligns
  them by training the scorer to maximize realized utility directly.

ARCHITECTURE:
  Small MLP scorer s(x) -> probability of "trade this" (sigmoid output)
  Kelly fraction = clip(probability * adaptive_kelly_cap, 0, max_kelly)
  Position = bankroll * Kelly_fraction
  Participation = position / dvol_d0 (clipped per D285: max 5%)
  Impact = 2 * Y * sqrt(participation) * sigma_d
  Net return = ret_t5 - impact (when we trade)
  Utility = log(1 + Kelly_fraction * net_return)
  Loss = -mean(utility) over training fold

DIFFERENTIABILITY:
  Everything is smooth except clip(participation, 0, 0.05) which has
  zero gradient when binding. We use a soft cap via sigmoid-rescaling
  so gradients flow even at the cap boundary.

PRE-COMMITTED VERDICTS (locked before running):
  PASS: DFL Bouchaud-adj Sharpe @ $1M AUM >= 1.2x v3 XGBoost baseline
        AND noise-floor permutation p < 0.05 (10 trials)
        AND recent-subset Sharpe >= v3 baseline
  -> Ship as DFL shadow scorer (D292), parallel to D286 TabPFN shadow

  PARTIAL: 1-2 of 3 pass
  -> Document promising, file follow-up with architecture search

  FAIL: 0 of 3 pass
  -> Document negative, close DFL thread

USAGE:
  python scripts/ml_v6_dfl_kelly_e2e.py
"""
from __future__ import annotations
import json
import math
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
# DFL model: small MLP scorer
# ──────────────────────────────────────────────────────────────────────


class DFLScorer(nn.Module):
    """3-layer MLP that outputs a "trade conviction" in [0, 1].
    Multiplied by the per-tier-AUM Kelly cap to get the actual Kelly fraction."""
    def __init__(self, in_features: int, hidden: int = 128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_features, hidden),
            nn.LayerNorm(hidden),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(hidden, hidden // 2),
            nn.LayerNorm(hidden // 2),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(hidden // 2, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Sigmoid output -> "trade conviction" in [0, 1]
        return torch.sigmoid(self.net(x).squeeze(-1))


# ──────────────────────────────────────────────────────────────────────
# Differentiable Kelly + Bouchaud impact + log-utility
# ──────────────────────────────────────────────────────────────────────


def differentiable_dfl_loss(scores: torch.Tensor,
                              ret: torch.Tensor,
                              dvol: torch.Tensor,
                              sigma_d: torch.Tensor,
                              aum: float = 1_000_000.0,
                              max_kelly: float = 0.50,
                              max_participation: float = 0.05,
                              Y: float = 1.5,
                              eps: float = 1e-6) -> torch.Tensor:
    """Negative mean log-utility (so we minimize it).

    scores ∈ [0, 1]: trade conviction from MLP
    ret: realized ret_t5 (pre-cost)
    dvol: $ daily volume
    sigma_d: daily volatility (~ intraday_pct/4)

    Kelly fraction = scores * max_kelly  (i.e. score=1 -> max_kelly position)
    Position $ = aum * kelly
    Participation = position / dvol  (capped at max_participation via soft sigmoid)
    Impact = 2 * Y * sqrt(participation) * sigma_d
    Net return = ret - impact
    Utility = log(1 + kelly * net_return), clipped to be safe

    We use a SOFT cap on participation via sigmoid rescaling so gradients
    flow through the cap boundary.
    """
    kelly = scores * max_kelly  # ∈ [0, max_kelly]
    position = aum * kelly  # $ amount

    # Raw participation
    raw_part = position / (dvol.clamp(min=1.0))
    # Soft cap at max_participation: smooth ceiling via sigmoid
    # part_capped = max_participation * tanh(raw_part / max_participation)
    # Use tanh which smoothly saturates at max_participation
    part_capped = max_participation * torch.tanh(raw_part / max_participation)

    impact = 2.0 * Y * torch.sqrt(part_capped + eps) * sigma_d
    net_ret = ret - impact

    # Utility: log(1 + kelly * net_ret), with safety clipping inside log
    # To avoid log of negative when net_ret * kelly < -1 (catastrophic loss)
    inner = 1.0 + kelly * net_ret
    inner = inner.clamp(min=0.01)  # cap loss at 99% (one-trade ruin)
    utility = torch.log(inner)

    # We want to MAXIMIZE utility, so loss = -mean(utility)
    return -utility.mean()


# ──────────────────────────────────────────────────────────────────────
# Train + evaluate one fold
# ──────────────────────────────────────────────────────────────────────


def train_dfl_fold(X_tr, ret_tr, dvol_tr, sigma_tr,
                    X_te, ret_te, dvol_te, sigma_te,
                    n_epochs: int = 80, batch_size: int = 256,
                    lr: float = 1e-3, weight_decay: float = 1e-3,
                    aum: float = 1_000_000.0, max_kelly: float = 0.50,
                    seed: int = 42, device: str = "cuda") -> tuple[np.ndarray, dict]:
    """Train one DFL fold; return per-test-row scores + diagnostics."""
    torch.manual_seed(seed)
    np.random.seed(seed)

    in_dim = X_tr.shape[1]
    model = DFLScorer(in_dim).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=n_epochs)

    # Standardize features per fold
    mu = X_tr.mean(axis=0); sd = X_tr.std(axis=0).clip(min=1e-6)
    Xtr_n = (X_tr - mu) / sd
    Xte_n = (X_te - mu) / sd

    Xtr_t = torch.from_numpy(Xtr_n.astype("float32")).to(device)
    Xte_t = torch.from_numpy(Xte_n.astype("float32")).to(device)
    ret_tr_t = torch.from_numpy(ret_tr.astype("float32")).to(device)
    dvol_tr_t = torch.from_numpy(dvol_tr.astype("float32")).to(device)
    sigma_tr_t = torch.from_numpy(sigma_tr.astype("float32")).to(device)

    n = len(Xtr_t)
    losses = []
    model.train()
    for epoch in range(n_epochs):
        perm = torch.randperm(n, device=device)
        epoch_losses = []
        for i in range(0, n, batch_size):
            idx = perm[i:i + batch_size]
            scores = model(Xtr_t[idx])
            loss = differentiable_dfl_loss(
                scores, ret_tr_t[idx], dvol_tr_t[idx], sigma_tr_t[idx],
                aum=aum, max_kelly=max_kelly,
            )
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            epoch_losses.append(loss.item())
        sched.step()
        losses.append(np.mean(epoch_losses))

    model.eval()
    with torch.no_grad():
        scores_te = model(Xte_t).cpu().numpy()
    diagnostics = {"final_loss": float(losses[-1]),
                   "min_loss": float(min(losses)),
                   "n_epochs": n_epochs}
    return scores_te, diagnostics


# ──────────────────────────────────────────────────────────────────────
# Bouchaud-adjusted Sharpe given scores + sizing
# ──────────────────────────────────────────────────────────────────────


def bouchaud_adjusted_sharpe(scores: np.ndarray, ret: np.ndarray,
                              dvol: np.ndarray, sigma_d: np.ndarray,
                              d0: pd.Series, aum: float = 1_000_000.0,
                              max_kelly: float = 0.50, max_part: float = 0.05,
                              Y: float = 1.5) -> dict:
    """Compute daily P&L with Bouchaud impact, return Sharpe."""
    kelly = np.clip(scores * max_kelly, 0, max_kelly)
    position = aum * kelly
    raw_part = position / np.clip(dvol, 1, None)
    part = np.minimum(raw_part, max_part)
    impact = 2.0 * Y * np.sqrt(np.clip(part, 0, None)) * sigma_d
    net = ret - impact
    pnl = position * net  # $ per pick
    df = pd.DataFrame({"d0": pd.to_datetime(d0).values, "pnl": pnl})
    daily = df.groupby("d0")["pnl"].sum().dropna().values
    if len(daily) < 5:
        return {"error": "T<5"}
    mean = daily.mean(); std = daily.std(ddof=1)
    return {
        "T_days": int(len(daily)),
        "mean_daily_pnl": float(mean),
        "std_daily_pnl": float(std),
        "sharpe_ann": float(mean / std * math.sqrt(252)) if std > 0 else 0.0,
        "total_pnl": float(daily.sum()),
        "n_picks_with_position": int((position > 0).sum()),
    }


# ──────────────────────────────────────────────────────────────────────
# Driver
# ──────────────────────────────────────────────────────────────────────


def main() -> int:
    section("LOAD")
    from ml_continuer_v2_ensemble import load_data, engineer_features
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    X = engineer_features(df).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    ret = df["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True).values
    dvol = df["dvol_d0"].clip(lower=1.0).reset_index(drop=True).values
    intraday = df["intraday_pct"].clip(0.001, 2.0).reset_index(drop=True).values
    sigma_d = intraday / 4.0  # daily vol approx
    d0 = pd.to_datetime(df["d0"]).reset_index(drop=True)
    print(f"  X: {X.shape}  ret: {ret.shape}  dvol mean: ${dvol.mean()/1e6:.2f}M  sigma_d mean: {sigma_d.mean()*100:.1f}%")

    n_folds = 12; train_days = 120; test_days = 15
    fs_list = pd.date_range(d0.min() + pd.Timedelta(days=train_days),
                              d0.max() - pd.Timedelta(days=test_days),
                              periods=n_folds)
    print(f"  WF: {n_folds} folds, train={train_days}d, test={test_days}d")

    # ─────────────────────────────────────────────────────────────────
    section("STEP 1 — train DFL on 12-fold WF")
    AUM = 1_000_000.0
    MAX_KELLY = 0.50
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"  device: {device}, AUM: ${AUM/1e6:.1f}M, max_kelly: {MAX_KELLY*100:.0f}%")

    dfl_rows = []
    X_arr = X.values.astype("float64")
    for fi, fs in enumerate(fs_list):
        tr = (d0 >= fs - pd.Timedelta(days=train_days)) & (d0 < fs)
        te = (d0 >= fs) & (d0 < fs + pd.Timedelta(days=test_days))
        if tr.sum() < 100 or te.sum() < 5:
            continue
        t0 = time.time()
        scores_te, diag = train_dfl_fold(
            X_arr[tr.values], ret[tr.values], dvol[tr.values], sigma_d[tr.values],
            X_arr[te.values], ret[te.values], dvol[te.values], sigma_d[te.values],
            aum=AUM, max_kelly=MAX_KELLY, seed=42 + fi, device=device,
        )
        # Compute fold Bouchaud-adj Sharpe
        s = bouchaud_adjusted_sharpe(
            scores_te, ret[te.values], dvol[te.values], sigma_d[te.values],
            d0[te.values], aum=AUM, max_kelly=MAX_KELLY,
        )
        dfl_rows.append(pd.DataFrame({
            "fold": fi, "d0": d0[te.values].values,
            "y_true": ret[te.values], "score": scores_te,
            "_idx": np.where(te.values)[0],
        }))
        print(f"  fold {fi:>2}: tr={tr.sum():>5}  te={te.sum():>4}  "
              f"loss={diag['final_loss']:+.4f}  fold_Sharpe={s.get('sharpe_ann','--'):.2f}  ({time.time()-t0:.0f}s)",
              flush=True)
    dfl_preds = pd.concat(dfl_rows, ignore_index=True) if dfl_rows else pd.DataFrame()
    print(f"  DFL OOS rows: {len(dfl_preds):,}")

    # Aggregate Bouchaud-adj Sharpe across all OOS
    all_idx = dfl_preds["_idx"].values
    dfl_sharpe = bouchaud_adjusted_sharpe(
        dfl_preds["score"].values, ret[all_idx], dvol[all_idx], sigma_d[all_idx],
        d0[all_idx], aum=AUM, max_kelly=MAX_KELLY,
    )
    print(f"\n  DFL aggregate: T={dfl_sharpe['T_days']}  "
          f"mean ${dfl_sharpe['mean_daily_pnl']:,.0f}  std ${dfl_sharpe['std_daily_pnl']:,.0f}  "
          f"Sharpe_ann {dfl_sharpe['sharpe_ann']:+.2f}  total ${dfl_sharpe['total_pnl']:,.0f}")

    # ─────────────────────────────────────────────────────────────────
    section("STEP 2 — v3 XGBoost baseline (CPU-deterministic) on same folds")
    import xgboost as xgb
    XGB_PARAMS = {"objective": "reg:squarederror", "n_estimators": 600,
        "max_depth": 5, "learning_rate": 0.03, "subsample": 0.8,
        "colsample_bytree": 0.6, "min_child_weight": 5, "reg_alpha": 0.5,
        "reg_lambda": 1.0, "tree_method": "hist", "device": "cpu",
        "n_jobs": 1, "random_state": 42}

    xgb_rows = []
    for fi, fs in enumerate(fs_list):
        tr = (d0 >= fs - pd.Timedelta(days=train_days)) & (d0 < fs)
        te = (d0 >= fs) & (d0 < fs + pd.Timedelta(days=test_days))
        if tr.sum() < 100 or te.sum() < 5: continue
        m = xgb.XGBRegressor(**XGB_PARAMS)
        m.fit(X.loc[tr.values], pd.Series(ret[tr.values]), verbose=False)
        pred = m.predict(X.loc[te.values])
        # Map XGBoost regression output to a [0,1] sigmoid-like score
        # via per-fold rank-based normalization (so it's comparable to DFL's [0,1] scores)
        rank = pd.Series(pred).rank(pct=True).values  # 0..1 per fold
        xgb_rows.append(pd.DataFrame({
            "fold": fi, "d0": d0[te.values].values,
            "y_true": ret[te.values], "score": rank,
            "_idx": np.where(te.values)[0],
        }))
    xgb_preds = pd.concat(xgb_rows, ignore_index=True)
    xgb_idx = xgb_preds["_idx"].values
    xgb_sharpe = bouchaud_adjusted_sharpe(
        xgb_preds["score"].values, ret[xgb_idx], dvol[xgb_idx], sigma_d[xgb_idx],
        d0[xgb_idx], aum=AUM, max_kelly=MAX_KELLY,
    )
    print(f"  v3 XGBoost (rank-normalized) aggregate: T={xgb_sharpe['T_days']}  "
          f"Sharpe_ann {xgb_sharpe['sharpe_ann']:+.2f}  total ${xgb_sharpe['total_pnl']:,.0f}")

    # ─────────────────────────────────────────────────────────────────
    section("STEP 3 — head-to-head + recent-subset check (doc 141 hygiene)")
    threshold_recent = pd.Timestamp("2025-08-01")
    dfl_preds["d0"] = pd.to_datetime(dfl_preds["d0"])
    xgb_preds["d0"] = pd.to_datetime(xgb_preds["d0"])
    dfl_recent = dfl_preds[dfl_preds["d0"] >= threshold_recent]
    xgb_recent = xgb_preds[xgb_preds["d0"] >= threshold_recent]

    if len(dfl_recent) >= 20 and len(xgb_recent) >= 20:
        dfl_r_idx = dfl_recent["_idx"].values
        xgb_r_idx = xgb_recent["_idx"].values
        dfl_recent_sharpe = bouchaud_adjusted_sharpe(
            dfl_recent["score"].values, ret[dfl_r_idx], dvol[dfl_r_idx],
            sigma_d[dfl_r_idx], d0[dfl_r_idx], aum=AUM, max_kelly=MAX_KELLY,
        )
        xgb_recent_sharpe = bouchaud_adjusted_sharpe(
            xgb_recent["score"].values, ret[xgb_r_idx], dvol[xgb_r_idx],
            sigma_d[xgb_r_idx], d0[xgb_r_idx], aum=AUM, max_kelly=MAX_KELLY,
        )
        print(f"  RECENT subset (Aug 2025+):")
        print(f"    DFL recent: Sharpe {dfl_recent_sharpe['sharpe_ann']:+.2f}  total ${dfl_recent_sharpe['total_pnl']:,.0f}")
        print(f"    XGB recent: Sharpe {xgb_recent_sharpe['sharpe_ann']:+.2f}  total ${xgb_recent_sharpe['total_pnl']:,.0f}")
    else:
        print(f"  Recent subset too small; skipping recent comparison")
        dfl_recent_sharpe = {"error": "n<20"}; xgb_recent_sharpe = {"error": "n<20"}

    print()
    print(f"  HEAD-TO-HEAD (Bouchaud-adjusted Sharpe @ ${AUM/1e6:.0f}M AUM):")
    print(f"    DFL:        {dfl_sharpe['sharpe_ann']:+.2f}")
    print(f"    v3 XGBoost: {xgb_sharpe['sharpe_ann']:+.2f}")
    if xgb_sharpe['sharpe_ann'] > 0:
        ratio = dfl_sharpe['sharpe_ann'] / xgb_sharpe['sharpe_ann']
        print(f"    ratio DFL/XGB: {ratio:.3f}x")
    else:
        ratio = float("inf") if dfl_sharpe['sharpe_ann'] > 0 else 0
        print(f"    ratio: undefined (XGB Sharpe non-positive)")

    # ─────────────────────────────────────────────────────────────────
    section("STEP 4 — noise-floor permutation (10 trials, shuffle features)")
    print("  Pre-commit: real DFL Sharpe lift over null > 95th percentile")
    print()
    rng = np.random.default_rng(42)
    null_dfl_sharpes = []
    null_xgb_sharpes = []
    for perm_i in range(10):
        t0 = time.time()
        # Shuffle each feature column across rows
        X_shuf = X.copy()
        for col in X_shuf.columns:
            X_shuf[col] = rng.permutation(X_shuf[col].values)
        X_shuf_arr = X_shuf.values.astype("float64")
        # Quick re-train DFL + XGB on shuffled features (1-2 folds for speed)
        # Use just folds 0, 5, 11 for noise-floor speed
        null_dfl_pnl = []
        null_xgb_pnl = []
        for fi in [0, 5, 11]:
            fs = fs_list[fi]
            tr = (d0 >= fs - pd.Timedelta(days=train_days)) & (d0 < fs)
            te = (d0 >= fs) & (d0 < fs + pd.Timedelta(days=test_days))
            if tr.sum() < 100 or te.sum() < 5: continue
            try:
                scores_null, _ = train_dfl_fold(
                    X_shuf_arr[tr.values], ret[tr.values], dvol[tr.values], sigma_d[tr.values],
                    X_shuf_arr[te.values], ret[te.values], dvol[te.values], sigma_d[te.values],
                    aum=AUM, max_kelly=MAX_KELLY, n_epochs=40, seed=42 + perm_i * 100 + fi,
                    device=device,
                )
                null_dfl_pnl.append((scores_null, ret[te.values], dvol[te.values],
                                       sigma_d[te.values], d0[te.values]))
                # Also XGB on shuffled
                m = xgb.XGBRegressor(**XGB_PARAMS)
                m.fit(X_shuf.loc[tr.values], pd.Series(ret[tr.values]), verbose=False)
                xgb_null_pred = m.predict(X_shuf.loc[te.values])
                xgb_null_score = pd.Series(xgb_null_pred).rank(pct=True).values
                null_xgb_pnl.append((xgb_null_score, ret[te.values], dvol[te.values],
                                       sigma_d[te.values], d0[te.values]))
            except Exception as e:
                continue
        if null_dfl_pnl:
            scores_concat = np.concatenate([x[0] for x in null_dfl_pnl])
            ret_concat = np.concatenate([x[1] for x in null_dfl_pnl])
            dvol_concat = np.concatenate([x[2] for x in null_dfl_pnl])
            sigma_concat = np.concatenate([x[3] for x in null_dfl_pnl])
            d0_concat = pd.concat([pd.Series(x[4]) for x in null_dfl_pnl]).values
            null_dfl_s = bouchaud_adjusted_sharpe(
                scores_concat, ret_concat, dvol_concat, sigma_concat, pd.Series(d0_concat),
                aum=AUM, max_kelly=MAX_KELLY,
            )
            null_dfl_sharpes.append(null_dfl_s.get('sharpe_ann', 0))

            scores_concat = np.concatenate([x[0] for x in null_xgb_pnl])
            null_xgb_s = bouchaud_adjusted_sharpe(
                scores_concat, ret_concat, dvol_concat, sigma_concat, pd.Series(d0_concat),
                aum=AUM, max_kelly=MAX_KELLY,
            )
            null_xgb_sharpes.append(null_xgb_s.get('sharpe_ann', 0))
            print(f"  perm[{perm_i+1:>2}/10]: DFL_null={null_dfl_sharpes[-1]:+.2f}  "
                  f"XGB_null={null_xgb_sharpes[-1]:+.2f}  ({time.time()-t0:.0f}s)", flush=True)

    if null_dfl_sharpes:
        null_arr = np.array(null_dfl_sharpes)
        # For 3-fold null Sharpe vs 12-fold real Sharpe, scale appropriately
        # Quick approximation: use proportion of null exceeding real
        p_value_dfl = float((null_arr >= dfl_sharpe['sharpe_ann']).mean())
        print()
        print(f"  REAL DFL Sharpe:        {dfl_sharpe['sharpe_ann']:+.2f}")
        print(f"  NULL distribution: mean={null_arr.mean():+.2f}  std={null_arr.std():.2f}  p95={np.percentile(null_arr, 95):+.2f}")
        print(f"  p-value (1-sided, vs DFL's noise floor): {p_value_dfl:.3f}")
        # Also p-value of (DFL_real - XGB_real) lift vs (DFL_null - XGB_null) distribution
        null_lifts = null_arr - np.array(null_xgb_sharpes)
        real_lift = dfl_sharpe['sharpe_ann'] - xgb_sharpe['sharpe_ann']
        p_value_lift = float((null_lifts >= real_lift).mean())
        print(f"  Real DFL-XGB Sharpe lift: {real_lift:+.2f}")
        print(f"  Null DFL-XGB lift mean: {null_lifts.mean():+.2f}, p95: {np.percentile(null_lifts, 95):+.2f}")
        print(f"  p-value (DFL-XGB lift outside null): {p_value_lift:.3f}")
    else:
        print("  no permutations succeeded")
        p_value_dfl = float("nan"); p_value_lift = float("nan")

    # ─────────────────────────────────────────────────────────────────
    section("STEP 5 — pre-committed verdict")
    pass_ratio = (xgb_sharpe['sharpe_ann'] > 0
                  and dfl_sharpe['sharpe_ann'] / xgb_sharpe['sharpe_ann'] >= 1.2)
    pass_noise_floor = (not math.isnan(p_value_lift)) and p_value_lift < 0.05
    pass_recent = (dfl_recent_sharpe.get('sharpe_ann', -999) >=
                   xgb_recent_sharpe.get('sharpe_ann', 999))
    n_pass = int(pass_ratio) + int(pass_noise_floor) + int(pass_recent)
    print(f"  PRE-COMMIT GATES:")
    print(f"    1. DFL Sharpe / XGB Sharpe >= 1.2x  : {'PASS' if pass_ratio else 'FAIL'}")
    print(f"       (DFL {dfl_sharpe['sharpe_ann']:+.2f} / XGB {xgb_sharpe['sharpe_ann']:+.2f})")
    print(f"    2. Noise-floor p < 0.05              : {'PASS' if pass_noise_floor else 'FAIL'}")
    print(f"       (p_value = {p_value_lift:.3f})")
    print(f"    3. DFL recent >= XGB recent          : {'PASS' if pass_recent else 'FAIL'}")
    print(f"       (DFL recent {dfl_recent_sharpe.get('sharpe_ann','--')} vs XGB recent {xgb_recent_sharpe.get('sharpe_ann','--')})")
    print()
    print(f"  Total: {n_pass} of 3 gates PASS")
    if n_pass == 3:
        print(f"  -> SHIP DFL as candidate scorer (D292 shadow mode)")
    elif n_pass >= 1:
        print(f"  -> PARTIAL: document promising; file architecture-search follow-up")
    else:
        print(f"  -> NO LIFT: document negative; close DFL thread")

    out = {
        "AUM": AUM, "max_kelly": MAX_KELLY,
        "dfl_full": dfl_sharpe,
        "xgb_full": xgb_sharpe,
        "dfl_recent": dfl_recent_sharpe,
        "xgb_recent": xgb_recent_sharpe,
        "noise_floor_dfl": [float(s) for s in null_dfl_sharpes],
        "noise_floor_xgb": [float(s) for s in null_xgb_sharpes],
        "p_value_dfl": float(p_value_dfl) if not math.isnan(p_value_dfl) else None,
        "p_value_lift": float(p_value_lift) if not math.isnan(p_value_lift) else None,
        "pass_ratio": pass_ratio,
        "pass_noise_floor": pass_noise_floor,
        "pass_recent": pass_recent,
        "n_pass": n_pass,
    }
    out_path = MODELS / "v6_dfl_kelly_e2e.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
