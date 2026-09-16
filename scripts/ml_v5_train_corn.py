"""MoMTrans v5 — single-trunk, CORN-headed, Spearman-listwise-trained model.

The Tier S enhancement bundle from docs/research-log/131_momtrans_v5_enhancement_roadmap.md:

  S1. CORN ordinal head — one model with K-1 chained conditional
      probabilities, replacing v4's 4 independent BCE specialists. Borrows
      ELITE statistical strength from BROAD's much larger positive class.

  S2. Spearman soft-rank loss — composite loss = α·CORN_BCE + (1-α)·SpearmanRank
      with per-day groupings (the strategy trades top-K daily picks).

  S3. Muon + AdamW hybrid + EMA weights — Muon for 2D matrix params,
      AdamW for biases/LayerNorm. EMA decay=0.999 for distribution-shift
      robustness. Per Yandex 2026 benchmark.

  S4. Mixup augmentation — α=0.4 in feature space + linear-interpolation
      of CORN ordinal labels. Calibrated for the imbalanced ELITE-tail.

Architecture is the SAME tabular-only TabTransformer trunk as v4 (per
the doc 127 ablation result: sequence branch was harmful). The change is
the HEAD, the LOSS, and the OPTIMIZER.

USAGE
  python scripts/ml_v5_train_corn.py --full-wf
  python scripts/ml_v5_train_corn.py --smoke
  python scripts/ml_v5_train_corn.py --full-wf --epochs 150 --spearman-weight 0.5

OUTPUT (gitignored under data/models)
  data/models/momtrans_v5_corn.pt                 — final production model
  data/models/momtrans_v5_corn_predictions.parquet — 16-fold WF predictions
  data/models/momtrans_v5_corn_summary.json       — config + metrics
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
MODELS.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO / "scripts"))
from ml_continuer_v2_ensemble import (  # type: ignore
    load_data, engineer_features, walk_forward_cv,
)
from ml_v5_components import (  # type: ignore
    CORNHead, corn_loss, corn_loss_with_mixup,
    soft_rank, per_day_spearman_loss,
    Muon, split_params_for_muon, EMAWeights,
    mixup_features_and_ordinal_labels,
)


# ── Ordinal label encoding (4 thresholds matching v4 specialist set) ──
ORDINAL_THRESHOLDS = [0.10, 0.15, 0.25, 0.40]  # BROAD, VETOED, HIGH, ELITE
N_ORDINAL_LEVELS = len(ORDINAL_THRESHOLDS)  # K = 4
N_CONDITIONAL = N_ORDINAL_LEVELS  # K conditional probs (one per threshold)
TIER_NAMES = ["BROAD", "VETOED", "HIGH", "ELITE"]


def encode_ordinal(ret_t5: np.ndarray) -> np.ndarray:
    """Convert continuous ret_t5 to ordinal label in [0, K]:
      0 = below all thresholds (SKIP)
      1 = >=0.10 (BROAD positive)
      2 = >=0.15 (VETOED positive)
      3 = >=0.25 (HIGH positive)
      4 = >=0.40 (ELITE positive)
    """
    y = np.zeros(len(ret_t5), dtype=np.int64)
    for thr in ORDINAL_THRESHOLDS:
        y += (ret_t5 >= thr).astype(np.int64)
    return y


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


# ── v5 model: same TabTransformer trunk as v4, different head ────────


class MoMTransV5(nn.Module):
    """v5 = TabTransformer trunk (tabular-only per doc 127 ablation winner)
    + CORN head outputting K conditional probabilities."""
    def __init__(self, n_tab_features: int, d_model: int = 64,
                  n_layers: int = 4, dropout: float = 0.10):
        super().__init__()
        # Tabular branch (mirrors v4 exactly so SSL encoder weights transfer)
        self.tab_proj = nn.Linear(1, d_model)
        self.tab_pos = nn.Parameter(torch.randn(1, n_tab_features, d_model) * 0.02)
        self.tab_pool_q = nn.Parameter(torch.randn(1, 1, d_model) * 0.02)
        tab_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=4, dim_feedforward=d_model*2,
            dropout=dropout, batch_first=True, activation="gelu",
        )
        self.tab_enc = nn.TransformerEncoder(tab_layer, num_layers=n_layers)

        # CORN head replaces v4's 4 independent specialists
        self.shared = nn.Sequential(
            nn.Linear(d_model, d_model),
            nn.GELU(),
            nn.Dropout(dropout),
        )
        self.corn = CORNHead(in_features=d_model, n_thresholds=N_CONDITIONAL)

    def encode(self, tab: torch.Tensor) -> torch.Tensor:
        B, F = tab.shape
        tok = self.tab_proj(tab.unsqueeze(-1)) + self.tab_pos
        cls = self.tab_pool_q.expand(B, -1, -1)
        seq = torch.cat([cls, tok], dim=1)
        h = self.tab_enc(seq)
        return h[:, 0, :]  # CLS pool

    def forward(self, tab: torch.Tensor) -> torch.Tensor:
        """Returns conditional logits, shape (B, K)."""
        embed = self.shared(self.encode(tab))
        return self.corn(embed)

    def cumulative_probs(self, tab: torch.Tensor) -> torch.Tensor:
        """Returns unconditional P(R≥t_k) at each level, shape (B, K)."""
        return self.corn.cumulative_probs(self.forward(tab))


def load_ssl_encoder_into(model: MoMTransV5, ssl_pt_path: Path) -> int:
    """Warm-start v5's tabular encoder from v4's SSL pre-trained weights.
    Returns number of tensors transferred."""
    if not ssl_pt_path.exists():
        return 0
    blob = torch.load(ssl_pt_path, map_location="cpu", weights_only=False)
    encoder_state = blob["encoder_state"]
    own_state = model.state_dict()
    n_loaded = 0
    for k, v in encoder_state.items():
        if k in own_state and own_state[k].shape == v.shape:
            own_state[k].copy_(v)
            n_loaded += 1
    return n_loaded


def train_one_fold(fold_i: int, X: np.ndarray, y_ret_t5: np.ndarray,
                     dates: pd.Series, tr_mask, te_mask, args, device):
    """Train one v5 model on a fold. Returns predictions + best state_dict."""
    # Build train/test tensors
    Xtr = torch.from_numpy(X[tr_mask].astype(np.float32))
    ytr_ret = y_ret_t5[tr_mask]
    ytr_ord = encode_ordinal(ytr_ret)
    ytr = torch.from_numpy(ytr_ord)
    dates_tr = pd.to_datetime(dates[tr_mask])
    # Day-id integer for per-day Spearman grouping
    day_id_map = {d: i for i, d in enumerate(dates_tr.unique())}
    day_ids_tr = torch.tensor([day_id_map[d] for d in dates_tr], dtype=torch.long)

    Xte = torch.from_numpy(X[te_mask].astype(np.float32)).to(device)
    yte_ret = y_ret_t5[te_mask]

    # Model
    model = MoMTransV5(n_tab_features=X.shape[1], d_model=args.d_model,
                          n_layers=args.n_layers, dropout=args.dropout).to(device)
    if args.load_ssl_encoder:
        n = load_ssl_encoder_into(model, Path(args.load_ssl_encoder))
        if fold_i == 0:
            print(f"    SSL warm-start: loaded {n} encoder tensors")

    # S3. Hybrid Muon (matrix) + AdamW (1D) optimizer
    matrix_p, scalar_p = split_params_for_muon(model)
    opt_muon = Muon(matrix_p, lr=args.muon_lr, momentum=0.95, weight_decay=1e-4)
    opt_adamw = torch.optim.AdamW(scalar_p, lr=args.adamw_lr, weight_decay=1e-4)
    # Use ceiling so we don't undershoot when n_train % batch_size != 0
    steps_per_epoch = max(1, math.ceil(len(Xtr) / args.batch_size))
    n_steps = steps_per_epoch * args.epochs
    sched_muon = torch.optim.lr_scheduler.OneCycleLR(
        opt_muon, max_lr=args.muon_lr, total_steps=n_steps, pct_start=0.10,
    )
    sched_adamw = torch.optim.lr_scheduler.OneCycleLR(
        opt_adamw, max_lr=args.adamw_lr, total_steps=n_steps, pct_start=0.10,
    )

    # S3 cont. EMA weights
    ema = EMAWeights(model, decay=0.999) if args.use_ema else None

    # Training loop
    best_loss = float("inf")
    best_state = None
    bad_epochs = 0
    n_train = len(Xtr)
    for epoch in range(args.epochs):
        model.train()
        # Shuffle and iterate
        perm = torch.randperm(n_train)
        epoch_losses = []
        for i in range(0, n_train, args.batch_size):
            idx = perm[i:i + args.batch_size]
            x_b = Xtr[idx].to(device, non_blocking=True)
            y_b = ytr[idx].to(device, non_blocking=True)
            day_b = day_ids_tr[idx].to(device, non_blocking=True)

            # S4. Mixup augmentation
            if args.mixup_alpha > 0:
                x_mix, y_a, y_b_mix, lam = mixup_features_and_ordinal_labels(
                    x_b, y_b, alpha=args.mixup_alpha,
                )
            else:
                x_mix, y_a, y_b_mix, lam = x_b, y_b, y_b, 1.0

            opt_muon.zero_grad()
            opt_adamw.zero_grad()

            # Forward (CORN conditional logits)
            cond_logits = model(x_mix)

            # S1. CORN ordinal loss (with Mixup label interpolation)
            l_corn = corn_loss_with_mixup(cond_logits, y_a, y_b_mix, lam,
                                              n_thresholds=N_CONDITIONAL)

            # S2. Spearman soft-rank loss (per-day grouped). Use BROAD-level
            # cumulative probability as the "score" being ranked.
            cum_probs = torch.sigmoid(cond_logits)  # (B, K)
            score = cum_probs[:, 0]  # cumulative prob at first level = BROAD
            # Target ranking signal: continuous ret_t5 (clipped) for THIS batch
            yreg_b = torch.from_numpy(
                np.clip(y_ret_t5[tr_mask][idx.cpu().numpy()], -0.5, 1.0)
            ).to(device).float()
            l_spearman = per_day_spearman_loss(score, yreg_b, day_b, tau=0.1)

            loss = (1.0 - args.spearman_weight) * l_corn + args.spearman_weight * l_spearman
            if not torch.isfinite(loss):
                continue
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt_muon.step()
            opt_adamw.step()
            sched_muon.step()
            sched_adamw.step()
            epoch_losses.append(loss.item())
            if ema is not None:
                ema.update(model)

        if not epoch_losses:
            print(f"    fold {fold_i:2d} epoch {epoch:3d} ALL BATCHES NAN — abort")
            break
        avg = float(np.mean(epoch_losses))
        if epoch % 5 == 0 or epoch == args.epochs - 1:
            print(f"    fold {fold_i:2d} epoch {epoch:3d} train_loss={avg:.4f}")
        if avg < best_loss - 1e-4:
            best_loss = avg
            bad_epochs = 0
            best_state = {k: v.detach().cpu().clone()
                            for k, v in model.state_dict().items()}
        else:
            bad_epochs += 1
            if bad_epochs >= args.patience:
                print(f"    fold {fold_i:2d} early stop at epoch {epoch}")
                break

    # Restore best, then optionally swap in EMA for final inference
    if best_state is not None:
        model.load_state_dict(best_state)
    if ema is not None:
        ema.apply_to(model)

    model.eval()
    with torch.no_grad():
        cond_logits = model(Xte).float().cpu()
        # Cumulative probs = cumprod(sigmoid(logits)) — guarantees rank consistency
        cum_probs = torch.cumprod(torch.sigmoid(cond_logits), dim=1)

    return {
        "cond_logits": cond_logits.numpy(),
        "cum_probs": cum_probs.numpy(),  # (n_test, K) — P(R>=t_k)
        "best_state": best_state,
        "y_ret_t5_test": yte_ret,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true",
                        help="1 fold, 5 epochs, batch=32 (~2 min)")
    parser.add_argument("--full-wf", action="store_true",
                        help="16-fold WF, 100 epochs/fold (~30 min)")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--d-model", type=int, default=64)
    parser.add_argument("--n-layers", type=int, default=4)
    parser.add_argument("--dropout", type=float, default=0.10)
    parser.add_argument("--muon-lr", type=float, default=0.02,
                        help="Muon (matrix) optimizer learning rate")
    parser.add_argument("--adamw-lr", type=float, default=3e-4,
                        help="AdamW (1D-param) optimizer learning rate")
    parser.add_argument("--patience", type=int, default=15)
    parser.add_argument("--mixup-alpha", type=float, default=0.4,
                        help="Mixup α (0 = disabled)")
    parser.add_argument("--spearman-weight", type=float, default=0.30,
                        help="Loss weight on Spearman term (rest is CORN BCE)")
    parser.add_argument("--use-ema", action="store_true", default=True)
    parser.add_argument("--no-ema", dest="use_ema", action="store_false")
    parser.add_argument("--load-ssl-encoder", type=str,
                        default=str(MODELS / "momtrans_v4_ssl_encoder.pt"))
    parser.add_argument("--out-suffix", type=str, default="_corn")
    parser.add_argument("--device", type=str, default="auto")
    args = parser.parse_args()

    if args.smoke:
        args.epochs = args.epochs or 5
        args.batch_size = 32
    elif args.full_wf:
        args.epochs = args.epochs or 100
    else:
        args.epochs = args.epochs or 30

    device = torch.device(args.device if args.device != "auto"
                            else ("cuda" if torch.cuda.is_available() else "cpu"))
    print(f"\nMoMTrans v5 trainer — device={device}")
    print(f"  config: d_model={args.d_model} n_layers={args.n_layers} "
          f"dropout={args.dropout} epochs={args.epochs} batch={args.batch_size}")
    print(f"  Tier-S stack: CORN + Spearman({args.spearman_weight}) + "
          f"Muon({args.muon_lr}) + EMA({args.use_ema}) + "
          f"Mixup(α={args.mixup_alpha})")

    section("STEP 1 — Load + engineer 54 v3 features")
    df = load_data(include_paths=True)
    X_df = engineer_features(df).replace([np.inf, -np.inf], 0).fillna(0)
    y_ret_t5 = df["ret_t5"].clip(-0.50, 1.00).values
    dates = df["d0"]
    X = X_df.values.astype(np.float32)
    means = X.mean(axis=0, keepdims=True)
    stds = X.std(axis=0, keepdims=True) + 1e-6
    X = (X - means) / stds
    print(f"  X: {X.shape}, y_ret_t5 mean: {y_ret_t5.mean()*100:+.2f}%")
    y_ord = encode_ordinal(y_ret_t5)
    print(f"  ordinal label distribution:")
    for k in range(N_ORDINAL_LEVELS + 1):
        n = int((y_ord == k).sum())
        tier = "SKIP" if k == 0 else TIER_NAMES[k-1]
        print(f"    level {k} ({tier:<6}): n={n:>5,} ({100*n/len(y_ord):.2f}%)")

    section("STEP 2 — Walk-forward training")
    folds = list(walk_forward_cv(dates, train_window_days=365, test_window_days=30))
    if args.smoke:
        folds = folds[:1]
    print(f"  n_folds: {len(folds)}")

    all_preds = []
    fold_summaries = []
    t_start = time.perf_counter()
    for fold_i, (tr_mask, te_mask) in enumerate(folds):
        if te_mask.sum() < 30:
            continue
        print(f"\n  ── FOLD {fold_i+1}/{len(folds)} ──")
        result = train_one_fold(fold_i, X, y_ret_t5, dates, tr_mask, te_mask,
                                  args, device)
        # Compute Spearman ρ for this fold
        broad_score = result["cum_probs"][:, 0]  # P(R>=0.10)
        elite_score = result["cum_probs"][:, -1]  # P(R>=0.40)
        rho_broad = float(np.corrcoef(
            np.argsort(np.argsort(broad_score)),
            np.argsort(np.argsort(result["y_ret_t5_test"])),
        )[0, 1])

        pred_df = pd.DataFrame({
            "fold": fold_i,
            "d0": pd.to_datetime(df.loc[te_mask, "d0"]).values,
            "ticker": df.loc[te_mask, "ticker"].values,
            "y_cls": (result["y_ret_t5_test"] >= 0.10).astype(int),
            "y_reg": result["y_ret_t5_test"],
            "p_broad":  result["cum_probs"][:, 0],
            "p_vetoed": result["cum_probs"][:, 1],
            "p_high":   result["cum_probs"][:, 2],
            "p_elite":  result["cum_probs"][:, 3],
            # Compatibility with downstream comparison scripts:
            "prob_binary": result["cum_probs"][:, 0],
            "conformal_width": np.full(int(te_mask.sum()), 0.5, dtype=np.float32),
        })
        all_preds.append(pred_df)
        fold_summaries.append({
            "fold": fold_i,
            "n_test": int(te_mask.sum()),
            "rho_broad": rho_broad,
            "elite_p_max": float(elite_score.max()),
            "elite_p_mean": float(elite_score.mean()),
        })

    elapsed = time.perf_counter() - t_start
    print(f"\n  WF done in {elapsed:.1f}s ({elapsed/max(1, len(folds)):.1f}s/fold)")

    section("STEP 3 — Aggregate + persist predictions")
    all_df = pd.concat(all_preds, ignore_index=True)
    out_pred = MODELS / f"momtrans_v5{args.out_suffix}_predictions.parquet"
    all_df.to_parquet(out_pred, compression="zstd")
    print(f"  -> {out_pred}  ({len(all_df):,} rows)")

    # Aggregate Spearman across all folds
    rho_all = float(np.corrcoef(
        np.argsort(np.argsort(all_df["p_broad"].values)),
        np.argsort(np.argsort(all_df["y_reg"].values)),
    )[0, 1])
    rho_elite = float(np.corrcoef(
        np.argsort(np.argsort(all_df["p_elite"].values)),
        np.argsort(np.argsort(all_df["y_reg"].values)),
    )[0, 1])
    print(f"\n  Aggregate Spearman ρ:")
    print(f"    p_broad vs y_reg: {rho_all:+.4f}  (v4 baseline 0.141, prod v3 0.159)")
    print(f"    p_elite vs y_reg: {rho_elite:+.4f}")

    # Top-K rank discrimination (matches comparison script outputs)
    sorted_df = all_df.sort_values("p_broad", ascending=False).reset_index(drop=True)
    n = len(sorted_df)
    print(f"\n  Top-K rank discrimination (using p_broad as score):")
    for k_pct in (1, 2, 5, 10):
        k = max(int(n * k_pct / 100), 1)
        top = sorted_df.head(k)
        avg = float(top["y_reg"].mean() * 100)
        win = float((top["y_reg"] > 0).mean() * 100)
        print(f"    top {k_pct:>2}% (n={k:>5,}): avg={avg:>+6.2f}%, win={win:>5.1f}%")

    section("STEP 4 — Persist final production model (full data minus 5d holdout)")
    if args.full_wf:
        cutoff = pd.to_datetime(dates).max() - timedelta(days=5)
        final_mask = (pd.to_datetime(dates) < cutoff).values
        Xf = X[final_mask]
        yreg_f = y_ret_t5[final_mask]
        dates_f = dates[final_mask]
        # Train one final model on the full filtered set
        fake_te = np.zeros(len(yreg_f), dtype=bool); fake_te[-1] = True
        fake_tr = ~fake_te
        result = train_one_fold(999, Xf, yreg_f, dates_f, fake_tr, fake_te, args, device)
        out_pt = MODELS / f"momtrans_v5{args.out_suffix}.pt"
        torch.save({
            "model_state": result["best_state"],
            "config": {
                "d_model": args.d_model, "n_layers": args.n_layers,
                "dropout": args.dropout, "n_features": X.shape[1],
                "ordinal_thresholds": ORDINAL_THRESHOLDS,
                "n_conditional": N_CONDITIONAL,
                "spearman_weight": args.spearman_weight,
                "mixup_alpha": args.mixup_alpha,
                "use_ema": args.use_ema,
                "muon_lr": args.muon_lr,
                "adamw_lr": args.adamw_lr,
                "version": "v5_corn",
            },
            "feature_columns": list(X_df.columns),
            "feature_means": means.flatten().tolist(),
            "feature_stds": stds.flatten().tolist(),
            "training_rows": int(final_mask.sum()),
            "trained_at": pd.Timestamp.utcnow().isoformat(),
        }, out_pt)
        print(f"  -> {out_pt}")

    summary = {
        "config": vars(args),
        "device": str(device),
        "n_features": X.shape[1],
        "n_folds": len(folds),
        "elapsed_s": elapsed,
        "fold_summaries": fold_summaries,
        "agg_spearman_p_broad": rho_all,
        "agg_spearman_p_elite": rho_elite,
    }
    out_sum = MODELS / f"momtrans_v5{args.out_suffix}_summary.json"
    out_sum.write_text(json.dumps(summary, indent=2, default=str))
    print(f"  -> {out_sum}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
