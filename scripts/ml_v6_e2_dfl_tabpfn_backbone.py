"""Experiment 2 of doc 152 — DFL-on-TabPFN-backbone (the real DFL).

Per doc 151 §6 filed plan, with broken-baseline fix from doc 151 §3:
  - Backbone: TabPFN's frozen forward pass (load from saved RECOVERED preds)
  - Head: 2-layer MLP (TabPFN_proba, top-k v3 features) -> conviction
  - Loss: differentiable Bouchaud-Kelly utility, U = E[log(1 + f * net_return)]
  - Comparator: TabPFN-with-uniform-Kelly (NOT rank-normalized XGB,
    which produced doc 151's phantom 14x lift artifact)

Pre-commits locked in doc 152 §1 (Experiment 2):
  Gate 1: Sharpe ratio (DFL : TabPFN-uniform) >= 1.2x on $1M-AUM Bouchaud-adj
  Gate 2: Permutation noise floor (5 perms) p < 0.05
  Gate 3: Recent-subset Sharpe-ratio >= TabPFN-uniform recent-subset Sharpe
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from scipy.stats import spearmanr

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


# ------- Bouchaud-Kelly differentiable utility (compass §Frontier 2) -------

def differentiable_kelly_utility(
    conviction: torch.Tensor,        # in [0, 1]
    ret: torch.Tensor,                # realized 5d return per pick
    dvol_d0: torch.Tensor,            # day's dollar volume per pick ($)
    sigma_d: torch.Tensor,            # day's vol proxy per pick (return units)
    aum: float = 1_000_000.0,         # $1M Bouchaud-relevant scale
    max_kelly: float = 0.50,          # Aggressive ELITE cap
    max_part: float = 0.05,           # MAX_PARTICIPATION_PCT (D285)
    Y: float = 1.5,                   # Bouchaud constant for microcap
    eps: float = 1e-6,
) -> torch.Tensor:
    """U = mean( log(1 + f * net_ret) ),  net_ret = ret - 2*Y*sqrt(participation)*sigma_d"""
    f = conviction * max_kelly
    position_dollars = aum * f
    raw_part = position_dollars / dvol_d0.clamp(min=1.0)
    # Soft-cap participation at max_part via tanh (doc 151's pattern, kept)
    part_capped = max_part * torch.tanh(raw_part / max_part)
    impact = 2.0 * Y * torch.sqrt(part_capped + eps) * sigma_d
    net_ret = ret - impact
    inner = (1.0 + f * net_ret).clamp(min=0.01)
    return torch.log(inner).mean()


def realized_pnl_uniform_kelly(
    proba: np.ndarray,
    ret: np.ndarray,
    dvol_d0: np.ndarray,
    sigma_d: np.ndarray,
    top_decile_thresh: float,
    aum: float = 1_000_000.0,
    uniform_kelly: float = 0.35,      # HIGH-tier Kelly per doc 149 schedule
    max_part: float = 0.05,
    Y: float = 1.5,
) -> np.ndarray:
    """Per-pick $-PnL under uniform-Kelly baseline: bet uniform_kelly on
    every top-decile pick (proba >= threshold), $0 otherwise."""
    pnl = np.zeros_like(ret, dtype=float)
    take = proba >= top_decile_thresh
    if not take.any():
        return pnl
    pos = aum * uniform_kelly
    raw_part = pos / np.maximum(dvol_d0[take], 1.0)
    part_capped = np.minimum(raw_part, max_part)
    impact = 2.0 * Y * np.sqrt(part_capped) * sigma_d[take]
    net_ret = ret[take] - impact
    pnl[take] = pos * net_ret
    return pnl


def realized_pnl_dfl(
    conviction: np.ndarray,
    ret: np.ndarray,
    dvol_d0: np.ndarray,
    sigma_d: np.ndarray,
    aum: float = 1_000_000.0,
    max_kelly: float = 0.50,
    max_part: float = 0.05,
    Y: float = 1.5,
) -> np.ndarray:
    f = conviction * max_kelly
    pos = aum * f
    raw_part = pos / np.maximum(dvol_d0, 1.0)
    part_capped = np.minimum(raw_part, max_part)
    impact = 2.0 * Y * np.sqrt(part_capped) * sigma_d
    net_ret = ret - impact
    return pos * net_ret


# ----------------------------- thin DFL head ------------------------------

class DFLHead(nn.Module):
    def __init__(self, n_features: int, hidden: int = 32):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_features, hidden),
            nn.GELU(),
            nn.Linear(hidden, hidden),
            nn.GELU(),
            nn.Linear(hidden, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


def train_head(
    Xtr: np.ndarray, ret_tr: np.ndarray, dvol_tr: np.ndarray, sigma_tr: np.ndarray,
    n_features: int, lr: float = 3e-3, epochs: int = 400,
    aum: float = 1_000_000.0, device: str = "cpu", seed: int = 42,
) -> DFLHead:
    torch.manual_seed(seed)
    head = DFLHead(n_features).to(device)
    opt = torch.optim.Adam(head.parameters(), lr=lr, weight_decay=1e-4)
    Xt = torch.tensor(Xtr, dtype=torch.float32, device=device)
    rt = torch.tensor(ret_tr, dtype=torch.float32, device=device)
    dt = torch.tensor(dvol_tr, dtype=torch.float32, device=device)
    st = torch.tensor(sigma_tr, dtype=torch.float32, device=device)
    for ep in range(epochs):
        opt.zero_grad()
        c = head(Xt)
        u = differentiable_kelly_utility(c, rt, dt, st, aum=aum)
        loss = -u
        loss.backward()
        opt.step()
    return head


# -------------------------- per-fold DFL training -------------------------

def run_dfl_per_fold(
    df: pd.DataFrame,                 # full enriched panel
    feat_cols: list[str],             # top-k v3 feature columns to use
    tabpfn_preds: pd.DataFrame,       # OOS TabPFN preds with d0, ticker, y_pred
    fold_starts: pd.DatetimeIndex,
    train_days: int = 120, test_days: int = 15,
    aum: float = 1_000_000.0,
    seed: int = 42,
) -> pd.DataFrame:
    """For each fold: train DFL head on training window, score test window."""
    out = []
    df = df.copy()
    df["d0"] = pd.to_datetime(df["d0"])
    tabpfn_preds = tabpfn_preds.copy()
    tabpfn_preds["d0"] = pd.to_datetime(tabpfn_preds["d0"])

    # Merge: TabPFN proba + v3 features per (ticker, d0)
    if "ticker" in tabpfn_preds.columns:
        merged = tabpfn_preds.merge(
            df[["ticker", "d0", "ret_t5", "dvol_d0"] + feat_cols],
            on=["ticker", "d0"], how="inner",
        )
    else:
        # Fallback: align by _idx
        merged = tabpfn_preds.merge(
            df.reset_index().rename(columns={"index": "_idx"})[
                ["_idx", "ret_t5", "dvol_d0"] + feat_cols
            ],
            on="_idx", how="inner",
        )

    # Per-day vol proxy (rolling 5d std of ret_t5 per ticker)
    if "sigma_d" not in merged.columns:
        # Cheap proxy: |ret_t5|, scaled (better proxies require intraday data)
        merged["sigma_d"] = merged["ret_t5"].abs().clip(0.005, 0.50)

    head_input_cols = ["y_pred"] + feat_cols
    n_features = len(head_input_cols)

    for fi, fs in enumerate(fold_starts):
        tr = (merged["d0"] >= fs - pd.Timedelta(days=train_days)) & (merged["d0"] < fs)
        te = (merged["d0"] >= fs) & (merged["d0"] < fs + pd.Timedelta(days=test_days))
        if tr.sum() < 100 or te.sum() < 5:
            continue

        Xtr = merged.loc[tr, head_input_cols].values.astype(np.float32)
        Xte = merged.loc[te, head_input_cols].values.astype(np.float32)
        # Normalize per-column on training stats (no leakage)
        mu, sd = Xtr.mean(axis=0), Xtr.std(axis=0) + 1e-6
        Xtr_n = (Xtr - mu) / sd
        Xte_n = (Xte - mu) / sd

        ret_tr = merged.loc[tr, "ret_t5"].values.astype(np.float32)
        dvol_tr = merged.loc[tr, "dvol_d0"].fillna(1e6).values.astype(np.float32)
        sigma_tr = merged.loc[tr, "sigma_d"].values.astype(np.float32)

        # Train head
        head = train_head(Xtr_n, ret_tr, dvol_tr, sigma_tr,
                          n_features=n_features, aum=aum, seed=seed + fi)

        # Score test
        with torch.no_grad():
            Xte_t = torch.tensor(Xte_n, dtype=torch.float32)
            conviction = head(Xte_t).cpu().numpy()

        ret_te = merged.loc[te, "ret_t5"].values
        dvol_te = merged.loc[te, "dvol_d0"].fillna(1e6).values
        sigma_te = merged.loc[te, "sigma_d"].values
        proba_te = merged.loc[te, "y_pred"].values

        out.append(pd.DataFrame({
            "fold": fi, "d0": merged.loc[te, "d0"].values,
            "ticker": merged.loc[te, "ticker"].values if "ticker" in merged.columns else "",
            "tabpfn_proba": proba_te,
            "dfl_conviction": conviction,
            "ret_t5": ret_te,
            "dvol_d0": dvol_te,
            "sigma_d": sigma_te,
        }))

    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


# ----------------------------- Sharpe metrics -----------------------------

def daily_pnl(pnl: np.ndarray, d0: np.ndarray) -> pd.Series:
    s = pd.Series(pnl, index=pd.to_datetime(d0))
    return s.groupby(s.index).sum()


def sharpe_annualized(daily: pd.Series) -> float:
    if daily.std() <= 0 or len(daily) < 5:
        return 0.0
    return float(daily.mean() / daily.std() * np.sqrt(252))


# ---------------------------------- main ----------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-permutations", type=int, default=5,
                    help="noise-floor permutations for gate 2")
    ap.add_argument("--top-k-features", type=int, default=10,
                    help="how many v3 features to feed the head alongside TabPFN proba")
    ap.add_argument("--aum", type=float, default=1_000_000.0)
    ap.add_argument("--epochs", type=int, default=400)
    args = ap.parse_args()

    section("STEP 1 — load v3 panel + TabPFN saved preds (RECOVERED)")
    from ml_continuer_v2_ensemble import load_data, engineer_features
    df = load_data(include_paths=True)
    df["d0"] = pd.to_datetime(df["d0"])
    X = engineer_features(df).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    print(f"  panel: {len(df):,} rows, {len(X.columns)} v3 features")

    tabpfn_path = DERIVED / "ml_v6_item5_tabpfn_preds_RECOVERED.parquet"
    if not tabpfn_path.exists():
        # Fall back to non-recovered (with the doc 145 caveat)
        tabpfn_path = DERIVED / "ml_v6_item5_tabpfn_preds.parquet"
    tp = pd.read_parquet(tabpfn_path)
    print(f"  TabPFN preds: {len(tp):,} rows from {tabpfn_path.name}")

    # Top-k v3 features by simple |corr| with ret_t5 on training-style window
    target = df["ret_t5"].fillna(0).values
    feat_imp = []
    for c in X.columns:
        v = X[c].values
        if v.std() > 0:
            feat_imp.append((c, abs(float(np.corrcoef(v, target)[0, 1]))))
    feat_imp.sort(key=lambda r: -r[1])
    top_feats = [c for c, _ in feat_imp[:args.top_k_features]]
    print(f"  top-{args.top_k_features} features: {top_feats}")

    # Attach top features to df for merge
    for c in top_feats:
        df[c] = X[c].values

    # Re-derive the same fold scheme as ml_v6_item5_tabpfn_proper.py
    n_folds = 12
    train_days = 120
    test_days = 15
    fold_starts = pd.date_range(
        df["d0"].min() + pd.Timedelta(days=train_days),
        df["d0"].max() - pd.Timedelta(days=test_days), periods=n_folds,
    )
    print(f"  WF: {n_folds} folds, train={train_days}d, test={test_days}d")

    section("STEP 2 — train DFL head per fold (TabPFN frozen, head learns Kelly)")
    t0 = time.time()
    dfl_oos = run_dfl_per_fold(df, top_feats, tp, fold_starts,
                                train_days, test_days, aum=args.aum)
    print(f"  done in {time.time()-t0:.0f}s, n_oos = {len(dfl_oos):,}")

    if len(dfl_oos) < 50:
        print("  ERROR: insufficient OOS rows for evaluation, abort")
        return 1

    section("STEP 3 — compute DFL vs TabPFN-uniform-Kelly Sharpe")

    # Top-decile threshold from TabPFN proba on full OOS
    top_thresh = float(np.quantile(dfl_oos["tabpfn_proba"].values, 0.90))
    print(f"  TabPFN top-decile threshold: {top_thresh:+.4f}")

    pnl_uniform = realized_pnl_uniform_kelly(
        dfl_oos["tabpfn_proba"].values, dfl_oos["ret_t5"].values,
        dfl_oos["dvol_d0"].values, dfl_oos["sigma_d"].values,
        top_decile_thresh=top_thresh, aum=args.aum,
    )
    pnl_dfl = realized_pnl_dfl(
        dfl_oos["dfl_conviction"].values, dfl_oos["ret_t5"].values,
        dfl_oos["dvol_d0"].values, dfl_oos["sigma_d"].values, aum=args.aum,
    )

    daily_uni = daily_pnl(pnl_uniform, dfl_oos["d0"].values)
    daily_dfl = daily_pnl(pnl_dfl,     dfl_oos["d0"].values)
    sharpe_uni = sharpe_annualized(daily_uni)
    sharpe_dfl = sharpe_annualized(daily_dfl)
    sharpe_ratio = sharpe_dfl / sharpe_uni if sharpe_uni > 0 else float("nan")

    print(f"  TabPFN-uniform Kelly:  Sharpe = {sharpe_uni:+.3f}  total $ = {pnl_uniform.sum():+,.0f}")
    print(f"  DFL-on-TabPFN backbone: Sharpe = {sharpe_dfl:+.3f}  total $ = {pnl_dfl.sum():+,.0f}")
    print(f"  Ratio (DFL : uniform): {sharpe_ratio:+.3f}x")

    section("STEP 4 — recent-subset (folds 8-11) Sharpe (gate 3)")
    recent_mask = dfl_oos["fold"] >= 8
    recent_sub = dfl_oos[recent_mask].copy()
    if len(recent_sub) < 20:
        print("  WARNING: insufficient recent rows, gate 3 inconclusive")
        recent_uni = recent_dfl = float("nan")
    else:
        pu = realized_pnl_uniform_kelly(
            recent_sub["tabpfn_proba"].values, recent_sub["ret_t5"].values,
            recent_sub["dvol_d0"].values, recent_sub["sigma_d"].values,
            top_decile_thresh=top_thresh, aum=args.aum,
        )
        pd_ = realized_pnl_dfl(
            recent_sub["dfl_conviction"].values, recent_sub["ret_t5"].values,
            recent_sub["dvol_d0"].values, recent_sub["sigma_d"].values, aum=args.aum,
        )
        recent_uni = sharpe_annualized(daily_pnl(pu, recent_sub["d0"].values))
        recent_dfl = sharpe_annualized(daily_pnl(pd_, recent_sub["d0"].values))
        print(f"  recent uniform: Sharpe = {recent_uni:+.3f}  ($ {pu.sum():+,.0f})")
        print(f"  recent DFL:     Sharpe = {recent_dfl:+.3f}  ($ {pd_.sum():+,.0f})")

    section("STEP 5 — noise-floor permutation (gate 2)")
    null_ratios = []
    for perm in range(args.n_permutations):
        rng = np.random.default_rng(101 + perm)
        # Shuffle the conviction column ONLY (within fold) to test gate independence
        shuffled = dfl_oos.copy()
        for fi in shuffled["fold"].unique():
            m = shuffled["fold"] == fi
            shuffled.loc[m, "dfl_conviction"] = rng.permutation(shuffled.loc[m, "dfl_conviction"].values)
        p_perm = realized_pnl_dfl(
            shuffled["dfl_conviction"].values, shuffled["ret_t5"].values,
            shuffled["dvol_d0"].values, shuffled["sigma_d"].values, aum=args.aum,
        )
        sh_perm = sharpe_annualized(daily_pnl(p_perm, shuffled["d0"].values))
        null_ratios.append(sh_perm / sharpe_uni if sharpe_uni > 0 else 0.0)
        print(f"  perm {perm+1}/{args.n_permutations}: shuffled-DFL Sharpe = {sh_perm:+.3f}  (ratio {null_ratios[-1]:+.3f}x)")

    null_arr = np.array(null_ratios)
    p_value = float((null_arr >= sharpe_ratio).mean()) if not np.isnan(sharpe_ratio) else 1.0
    print(f"  null mean ratio: {null_arr.mean():+.3f}x   max: {null_arr.max():+.3f}x")
    print(f"  REAL ratio: {sharpe_ratio:+.3f}x   p-value (one-sided): {p_value:.3f}")

    section("STEP 6 — VERDICT against doc 152 §1 Experiment 2 pre-commits")
    g1 = (not np.isnan(sharpe_ratio)) and sharpe_ratio >= 1.2
    g2 = p_value < 0.05
    g3 = (not np.isnan(recent_dfl)) and (not np.isnan(recent_uni)) and recent_dfl >= recent_uni
    print(f"  Gate 1 (Sharpe ratio >= 1.2x):     {'PASS' if g1 else 'FAIL'}  ({sharpe_ratio:+.3f}x)")
    print(f"  Gate 2 (perm noise floor p<0.05):  {'PASS' if g2 else 'FAIL'}  (p={p_value:.3f})")
    print(f"  Gate 3 (recent DFL >= uniform):    {'PASS' if g3 else 'FAIL'}  ({recent_dfl:+.3f} vs {recent_uni:+.3f})")
    n_pass = sum([g1, g2, g3])
    if n_pass == 3:
        verdict = "SHIP D292 (DFL shadow scorer)"
    elif n_pass >= 1:
        verdict = "PROMISING-BUT-INCOMPLETE — investigate failing gate(s)"
    else:
        verdict = "DFL FAMILY CLOSED (all RTX-5070 variants exhausted)"
    print(f"\n  Composite: {n_pass}/3 PASS -> VERDICT: {verdict}")

    out = {
        "n_oos": int(len(dfl_oos)),
        "n_recent": int(recent_mask.sum()),
        "top_decile_thresh": top_thresh,
        "sharpe_uniform": sharpe_uni,
        "sharpe_dfl": sharpe_dfl,
        "sharpe_ratio": sharpe_ratio,
        "recent_sharpe_uniform": recent_uni,
        "recent_sharpe_dfl": recent_dfl,
        "p_value": p_value,
        "null_ratio_mean": float(null_arr.mean()),
        "null_ratio_max": float(null_arr.max()),
        "gate_1": g1, "gate_2": g2, "gate_3": g3,
        "n_pass": n_pass,
        "verdict": verdict,
        "top_features": top_feats,
        "aum": args.aum,
    }
    out_path = MODELS / "v6_e2_dfl_tabpfn_backbone.json"
    out_path.write_text(json.dumps(out, indent=2, default=float))
    print(f"\n  saved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
