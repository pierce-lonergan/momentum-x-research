"""Dilated TCN on intraday minute paths -> P(continuer | path).

Per Compass artifact 2 §B.1: minute layer of the temporal hierarchy wants
either a Dilated TCN or Mamba/S4. Mamba is less battle-tested in finance,
TCN is well-understood, both have linear time complexity in sequence length.

This is a TCN. Architecture:
  Input:  (B, 6, 30)  - 6 features (open_rel, high_rel, low_rel, close_rel,
                                     vol_z, log_trans), 30 bars
  Block 1: Conv1d(6, 16, k=3, d=1)  + BN + ReLU + Dropout(0.2)
  Block 2: Conv1d(16, 32, k=3, d=2) + BN + ReLU + Dropout(0.2)
  Block 3: Conv1d(32, 32, k=3, d=4) + BN + ReLU + Dropout(0.2)
  Block 4: Conv1d(32, 16, k=3, d=8) + BN + ReLU + Dropout(0.2)
  GlobalAvgPool -> Linear(16, 1) -> sigmoid
  BCE loss on continuer label (ret_t5 >= 0.10)

Walk-forward: same 16 folds as v2 (365d train / 30d test).
For each fold: train 5 epochs, batch=128, Adam lr=3e-4.

Output:
  data/models/tcn_intraday.pt
  data/polygon_warehouse/derived/tcn_intraday_walkforward_predictions.parquet
  Prints per-fold and aggregate metrics; comparison vs v2 broad gate.

Run:
  python scripts/ml_tcn_intraday.py [--epochs N] [--batch N] [--lr X]
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
MODELS.mkdir(parents=True, exist_ok=True)

# Fixed: 30 bars, 6 features
SEQ_LEN = 30
N_FEATURES = 6
FEATURE_COLS = ["open_rel", "high_rel", "low_rel", "close_rel", "vol_z", "log_trans"]


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def load_paths_and_labels() -> pd.DataFrame:
    """Pivot intraday_paths_30min into (ticker, d0) -> (30, 6) tensor.

    Joins to aftermath_strat for the y_cls / y_reg labels.
    """
    paths_p = (DERIVED / "intraday_paths_30min.parquet").as_posix()
    after_p = (DERIVED / "aftermath_strat.parquet").as_posix()
    con = duckdb.connect()
    con.sql("SET memory_limit='6GB'")
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")

    df = con.sql(f"""
        WITH paths AS (
            SELECT * FROM read_parquet('{paths_p}')
        ),
        labels AS (
            SELECT ticker, d0, ret_t5
            FROM read_parquet('{after_p}')
            WHERE ca_flag = 'clean' AND ret_t5 IS NOT NULL
              AND open BETWEEN 0.5 AND 50 AND volume > 100000
        )
        SELECT p.*, l.ret_t5
        FROM paths p
        JOIN labels l ON p.ticker = l.ticker AND p.d0 = l.d0
        ORDER BY p.d0, p.ticker, p.bar_idx
    """).df()
    df["d0"] = pd.to_datetime(df["d0"])
    return df


def pivot_to_tensors(df: pd.DataFrame):
    """Convert long-format paths into (N, 6, 30) tensor + (N,) labels.

    Pads (ticker, d0) groups shorter than 30 bars with zeros.
    """
    keys = df[["ticker", "d0"]].drop_duplicates().sort_values(["d0", "ticker"]).reset_index(drop=True)
    n = len(keys)
    X = np.zeros((n, N_FEATURES, SEQ_LEN), dtype=np.float32)
    y = np.zeros(n, dtype=np.float32)
    y_reg = np.zeros(n, dtype=np.float32)
    dates = pd.array([pd.Timestamp(0)] * n, dtype="datetime64[ns]")
    tickers = np.array([""] * n, dtype=object)

    key_to_idx = {(t, d): i for i, (t, d) in enumerate(zip(keys["ticker"], keys["d0"]))}
    for (t, d), g in df.groupby(["ticker", "d0"], sort=False):
        idx = key_to_idx[(t, d)]
        bar_indices = g["bar_idx"].values.astype(int)
        for f_i, fname in enumerate(FEATURE_COLS):
            vals = g[fname].fillna(0).astype(float).values
            for bi, v in zip(bar_indices, vals):
                if 0 <= bi < SEQ_LEN:
                    X[idx, f_i, bi] = v
        ret = float(g["ret_t5"].iloc[0])
        y[idx] = 1.0 if ret >= 0.10 else 0.0
        y_reg[idx] = ret
        dates[idx] = d
        tickers[idx] = t

    # Replace inf/nan
    X = np.nan_to_num(X, nan=0.0, posinf=10.0, neginf=-10.0)
    return X, y, y_reg, dates, tickers


class DilatedTCN(nn.Module):
    """4-block dilated TCN for sequence classification."""
    def __init__(self, in_channels: int = N_FEATURES, channels=(16, 32, 32, 16),
                 kernel_size: int = 3, dropout: float = 0.2):
        super().__init__()
        layers = []
        prev_c = in_channels
        for i, c in enumerate(channels):
            d = 2 ** i
            pad = (kernel_size - 1) * d  # causal-ish but symmetric is fine for offline
            layers += [
                nn.Conv1d(prev_c, c, kernel_size=kernel_size, dilation=d, padding=pad // 2),
                nn.BatchNorm1d(c),
                nn.ReLU(),
                nn.Dropout(dropout),
            ]
            prev_c = c
        self.body = nn.Sequential(*layers)
        self.head = nn.Linear(prev_c, 1)

    def forward(self, x):
        # x: (B, C, T)
        h = self.body(x)
        # global average pool over time
        h = h.mean(dim=2)
        return self.head(h).squeeze(-1)  # logits


class FocalLoss(nn.Module):
    """Binary focal loss for imbalanced classes (Lin et al. 2017).

    L = -alpha_t * (1 - p_t)^gamma * log(p_t)
      where p_t = sigmoid(logit) if y=1 else 1 - sigmoid(logit)
            alpha_t = alpha if y=1 else (1 - alpha)

    For our 20.74% positive rate in continuer prediction:
      gamma=2.0 down-weights easy negatives (most rows) so the model
        focuses on hard positives (the actual continuers).
      alpha=0.75 up-weights the minority class.
    """
    def __init__(self, alpha: float = 0.75, gamma: float = 2.0):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma

    def forward(self, logits, targets):
        # BCE with logits (numerically stable)
        bce = F.binary_cross_entropy_with_logits(logits, targets, reduction="none")
        p = torch.sigmoid(logits)
        p_t = p * targets + (1 - p) * (1 - targets)
        alpha_t = self.alpha * targets + (1 - self.alpha) * (1 - targets)
        focal = alpha_t * (1 - p_t) ** self.gamma * bce
        return focal.mean()


def train_one_fold(model, Xtr, ytr, Xte, yte, epochs: int, batch: int,
                    lr: float, device: str, verbose: bool = False,
                    use_focal: bool = False, focal_alpha: float = 0.75,
                    focal_gamma: float = 2.0,
                    early_stop_patience: int = 0,
                    val_frac: float = 0.15,
                    rng_seed: int = 42) -> np.ndarray:
    """Train one WF fold with optional val-AUC early stopping.

    early_stop_patience: 0 disables; >0 enables holdout val + best-AUC restore
    val_frac: fraction of training data held out as val for early stopping
    """
    # Optional: stratified-by-class split for early stopping
    if early_stop_patience > 0 and val_frac > 0 and len(Xtr) > 200:
        rng = np.random.RandomState(rng_seed)
        idx = rng.permutation(len(Xtr))
        n_val = max(50, int(len(Xtr) * val_frac))
        val_idx = idx[:n_val]
        tr_idx = idx[n_val:]
        Xv, yv = Xtr[val_idx], ytr[val_idx]
        Xtr_use, ytr_use = Xtr[tr_idx], ytr[tr_idx]
    else:
        Xv = yv = None
        Xtr_use, ytr_use = Xtr, ytr

    train_ds = TensorDataset(torch.from_numpy(Xtr_use), torch.from_numpy(ytr_use))
    train_loader = DataLoader(train_ds, batch_size=batch, shuffle=True, num_workers=0)
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    crit = FocalLoss(alpha=focal_alpha, gamma=focal_gamma) if use_focal else nn.BCEWithLogitsLoss()
    model.to(device)

    best_val_auc = -1.0
    best_state = None
    epochs_since_improve = 0
    for ep in range(epochs):
        model.train()
        tot_loss = 0.0
        n = 0
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            opt.zero_grad()
            logits = model(xb)
            loss = crit(logits, yb)
            loss.backward()
            opt.step()
            tot_loss += loss.item() * len(xb)
            n += len(xb)
        # Validation step for early stopping
        if Xv is not None:
            model.eval()
            with torch.no_grad():
                v_logits = model(torch.from_numpy(Xv).to(device)).cpu().numpy()
            v_probs = 1.0 / (1.0 + np.exp(-v_logits))
            try:
                from sklearn.metrics import roc_auc_score
                v_auc = float(roc_auc_score(yv, v_probs))
            except Exception:
                v_auc = -1.0
            if verbose:
                print(f"    epoch {ep+1}/{epochs}  loss={tot_loss/n:.4f}  val_auc={v_auc:.4f}")
            if v_auc > best_val_auc + 1e-4:
                best_val_auc = v_auc
                best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
                epochs_since_improve = 0
            else:
                epochs_since_improve += 1
                if epochs_since_improve >= early_stop_patience:
                    if verbose:
                        print(f"    EARLY STOP at epoch {ep+1} (best val_auc={best_val_auc:.4f})")
                    break
        else:
            if verbose:
                print(f"    epoch {ep+1}/{epochs}  loss={tot_loss/n:.4f}")

    # Restore best-val state if early stopping was active
    if best_state is not None:
        model.load_state_dict(best_state)

    # Predict on test
    model.eval()
    with torch.no_grad():
        Xte_t = torch.from_numpy(Xte).to(device)
        logits = model(Xte_t).cpu().numpy()
    return 1.0 / (1.0 + np.exp(-logits))


def walk_forward_cv(dates, train_window_days=365, test_window_days=30):
    dates = pd.Series(dates)
    start = dates.min() + timedelta(days=train_window_days)
    end = dates.max()
    cur = start
    while cur < end:
        tr_mask = (dates >= cur - timedelta(days=train_window_days)) & (dates < cur)
        te_mask = (dates >= cur) & (dates < cur + timedelta(days=test_window_days))
        if tr_mask.sum() >= 100 and te_mask.sum() >= 30:
            yield tr_mask.values, te_mask.values
        cur += timedelta(days=test_window_days)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch", type=int, default=128)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--use-focal", action="store_true",
                        help="Use focal loss instead of BCE (better calibration on imbalanced data)")
    parser.add_argument("--focal-alpha", type=float, default=0.75)
    parser.add_argument("--focal-gamma", type=float, default=2.0)
    parser.add_argument("--early-stop-patience", type=int, default=0,
                        help="Patience epochs for val-AUC early stopping (0 = off)")
    parser.add_argument("--val-frac", type=float, default=0.15,
                        help="Holdout fraction for early-stopping val set")
    parser.add_argument("--out-suffix", type=str, default="",
                        help="Suffix for output artifacts (e.g. '_focal')")
    args = parser.parse_args()

    torch.manual_seed(42)
    np.random.seed(42)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    section("STEP 1 - Load + pivot intraday paths")
    t0 = time.perf_counter()
    df = load_paths_and_labels()
    print(f"  loaded {len(df):,} bars in {time.perf_counter()-t0:.1f}s")
    t1 = time.perf_counter()
    X, y, y_reg, dates, tickers = pivot_to_tensors(df)
    print(f"  pivoted to ({X.shape}) tensor in {time.perf_counter()-t1:.1f}s")
    print(f"  positive rate: {y.mean()*100:.2f}%")
    print(f"  date range: {pd.Series(dates).min()} -> {pd.Series(dates).max()}")
    print(f"  device: {device}")

    section("STEP 2 - Walk-forward TCN training")
    folds = list(walk_forward_cv(dates))
    print(f"  n folds: {len(folds)}")

    fold_results = []
    all_preds = []
    for fold_i, (tr_mask, te_mask) in enumerate(folds):
        Xtr, ytr = X[tr_mask], y[tr_mask]
        Xte, yte = X[te_mask], y[te_mask]
        if len(Xte) < 30 or len(Xtr) < 200:
            continue
        t0 = time.perf_counter()
        model = DilatedTCN(dropout=args.dropout)
        probs = train_one_fold(model, Xtr, ytr, Xte, yte,
                                args.epochs, args.batch, args.lr, device,
                                verbose=(fold_i == 0),
                                use_focal=args.use_focal,
                                focal_alpha=args.focal_alpha,
                                focal_gamma=args.focal_gamma,
                                early_stop_patience=args.early_stop_patience,
                                val_frac=args.val_frac,
                                rng_seed=42 + fold_i)
        elapsed = time.perf_counter() - t0

        # Evaluate
        y_reg_te = pd.Series(y_reg[te_mask]).clip(-0.5, 1.0)
        def stat(mask):
            n = int(mask.sum())
            if n == 0: return (0, 0.0, 0.0)
            return (n, float(y_reg_te[mask].mean()), float((y_reg_te[mask] > 0).mean()))

        n_30, avg_30, win_30 = stat(probs >= 0.30)
        n_50, avg_50, win_50 = stat(probs >= 0.50)
        n_60, avg_60, win_60 = stat(probs >= 0.60)
        # Calibration: actual continue rate vs. mean predicted P
        actual_cr = float(yte.mean())

        fold_results.append({
            "fold": fold_i,
            "test_start": str(pd.Series(dates[te_mask]).min().date()),
            "n_test": len(Xte),
            "actual_cr": actual_cr,
            "mean_pred": float(probs.mean()),
            "tcn_30_n": n_30, "tcn_30_avg": avg_30, "tcn_30_win": win_30,
            "tcn_50_n": n_50, "tcn_50_avg": avg_50, "tcn_50_win": win_50,
            "tcn_60_n": n_60, "tcn_60_avg": avg_60, "tcn_60_win": win_60,
            "elapsed_s": elapsed,
        })
        per_row = pd.DataFrame({
            "fold": fold_i,
            "d0": dates[te_mask],
            "ticker": tickers[te_mask],
            "y_cls": yte,
            "y_reg": y_reg[te_mask],
            "tcn_proba": probs,
        })
        all_preds.append(per_row)
        print(f"  fold {fold_i:>2}  n_test={len(Xte):>4}  "
              f"P>=0.30 n={n_30:>3} avg={avg_30*100:>+6.2f}%  "
              f"P>=0.50 n={n_50:>3} avg={avg_50*100:>+6.2f}%  "
              f"({elapsed:.1f}s)")

    section("STEP 3 - Aggregate")
    fr = pd.DataFrame(fold_results)
    if len(fr) == 0:
        print("  no folds completed!")
        return 1
    fr = fr[fr["n_test"] >= 30]
    print(f"  n folds with >=30 test rows: {len(fr)}")

    def agg(name, n_col, avg_col, win_col):
        total_n = fr[n_col].sum()
        if total_n == 0: return f"{name}: no samples"
        weighted_avg = (fr[n_col] * fr[avg_col]).sum() / total_n
        weighted_win = (fr[n_col] * fr[win_col]).sum() / total_n
        return (f"{name:<25} n={total_n:>5,}  weighted_avg={weighted_avg*100:>+6.2f}%  "
                f"weighted_win={weighted_win*100:>5.1f}%")

    print()
    print(agg("TCN P>=0.30", "tcn_30_n", "tcn_30_avg", "tcn_30_win"))
    print(agg("TCN P>=0.50", "tcn_50_n", "tcn_50_avg", "tcn_50_win"))
    print(agg("TCN P>=0.60", "tcn_60_n", "tcn_60_avg", "tcn_60_win"))

    section("STEP 4 - Compare vs v2 ensemble (intersection)")
    v2_path = DERIVED / "ml_v2_walkforward_predictions.parquet"
    if v2_path.exists() and all_preds:
        v2 = pd.read_parquet(v2_path)
        v2["d0"] = pd.to_datetime(v2["d0"])
        tcn_all = pd.concat(all_preds, ignore_index=True)
        tcn_all["d0"] = pd.to_datetime(tcn_all["d0"])
        merged = tcn_all.merge(v2[["d0", "ticker", "prob_continuer"]],
                                on=["d0", "ticker"], how="inner")
        print(f"  merged: {len(merged):,} rows (TCN intersect v2)")

        # Both / TCN-only / v2-only / neither at high threshold
        both = merged[(merged["tcn_proba"] >= 0.30) & (merged["prob_continuer"] >= 0.30)]
        tcn_only = merged[(merged["tcn_proba"] >= 0.30) & (merged["prob_continuer"] < 0.30)]
        v2_only = merged[(merged["tcn_proba"] < 0.30) & (merged["prob_continuer"] >= 0.30)]
        neither = merged[(merged["tcn_proba"] < 0.30) & (merged["prob_continuer"] < 0.30)]
        for label, g in [("BOTH (long)", both), ("TCN-only (long)", tcn_only),
                          ("v2-only (long)", v2_only), ("NEITHER (skip)", neither)]:
            if len(g) == 0: continue
            avg = g["y_reg"].clip(-0.5, 1.0).mean() * 100
            win = (g["y_reg"] > 0).mean() * 100
            print(f"  {label:<20} n={len(g):>5,}  avg={avg:>+6.2f}%  win={win:>5.1f}%")

    section("STEP 5 - Persist WF preds + final model trained on all data")
    if all_preds:
        out = DERIVED / f"tcn_intraday_walkforward_predictions{args.out_suffix}.parquet"
        pd.concat(all_preds, ignore_index=True).to_parquet(out, compression="zstd")
        print(f"  Wrote {out}")

    # Final model: train on ALL labeled data for live inference.
    # The WF folds give the OOS evaluation; the final model is for production.
    print("  Training final model on full dataset for live inference...")
    final_model = DilatedTCN(dropout=args.dropout)
    final_model.to(device)
    final_model.train()
    train_ds = TensorDataset(torch.from_numpy(X), torch.from_numpy(y))
    train_loader = DataLoader(train_ds, batch_size=args.batch, shuffle=True, num_workers=0)
    opt = torch.optim.Adam(final_model.parameters(), lr=args.lr, weight_decay=1e-5)
    crit = (FocalLoss(alpha=args.focal_alpha, gamma=args.focal_gamma)
            if args.use_focal else nn.BCEWithLogitsLoss())
    for ep in range(args.epochs):
        tot, n_b = 0.0, 0
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            opt.zero_grad()
            loss = crit(final_model(xb), yb)
            loss.backward()
            opt.step()
            tot += loss.item() * len(xb)
            n_b += len(xb)
        print(f"    final ep {ep+1}/{args.epochs}  loss={tot/n_b:.4f}")
    pt_path = MODELS / f"tcn_intraday{args.out_suffix}.pt"
    torch.save({
        "state_dict": final_model.state_dict(),
        "seq_len": SEQ_LEN,
        "n_features": N_FEATURES,
        "feature_cols": FEATURE_COLS,
        "channels": (16, 32, 32, 16),
        "kernel_size": 3,
        "dropout": args.dropout,
        "trained_on_n": int(len(X)),
        "epochs": args.epochs,
    }, pt_path)
    print(f"  Wrote {pt_path}")

    summary = {
        "n_folds": len(fr),
        "epochs": args.epochs,
        "batch": args.batch,
        "lr": args.lr,
        "dropout": args.dropout,
        "device": device,
        "model": "DilatedTCN(channels=[16,32,32,16], k=3, d=[1,2,4,8])",
        "weighted": {
            "tcn_30": {
                "n": int(fr["tcn_30_n"].sum()),
                "avg_pct": float((fr["tcn_30_n"] * fr["tcn_30_avg"]).sum() /
                                  max(fr["tcn_30_n"].sum(), 1) * 100),
            },
            "tcn_50": {
                "n": int(fr["tcn_50_n"].sum()),
                "avg_pct": float((fr["tcn_50_n"] * fr["tcn_50_avg"]).sum() /
                                  max(fr["tcn_50_n"].sum(), 1) * 100),
            },
        },
    }
    s_path = MODELS / "tcn_intraday_summary.json"
    s_path.write_text(json.dumps(summary, indent=2))
    print(f"  Wrote {s_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
