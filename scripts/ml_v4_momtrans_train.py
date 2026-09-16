"""MoMTrans v4 — multi-task tabular+sequence transformer (RESEARCH ONLY).

Architecture per docs/research-log/125_momtrans_v4_design.md:
  TabularBranch (TabTransformer)  +  SequenceBranch (Time Transformer)
  +  RegimeBranch (Ising mag)     →  CrossAttention fusion
  →  Multi-task heads (binary + 4-class cohort + quantile + magnitude)
  →  Joint loss (BCE + CE + QuantileLoss + Huber)

USAGE
  --smoke        : 1 fold, 5 epochs, batch_size=32 (validate end-to-end ~2min)
  --full-wf      : 16-fold WF, 100 epochs/fold w/ early stopping (~4-6 hr)
  --epochs N     : override per-fold epoch budget
  --batch-size N : override batch size (default 128 for full-wf)
  --d-model N    : transformer hidden dim (default 64)
  --n-layers N   : transformer encoder layers (default 4)
  --dropout F    : dropout rate (default 0.10)
  --device cuda  : force device (default: auto-detect)

OUTPUT (all gitignored under data/models)
  data/models/momtrans_v4.pt                 : best fold-aggregated state
  data/models/momtrans_v4_predictions.parquet: per-row OOS predictions
  data/models/momtrans_v4_summary.json       : per-fold metrics + config

NOT a production loader. The lottery launcher does NOT load this model.
The verdict script (ml_v4_momtrans_verdict.py) compares its predictions
against v3-tuned-16f baseline using the same SHIP gate as D281.
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

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
MODELS.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO / "scripts"))
from ml_continuer_v2_ensemble import (  # type: ignore
    load_data, engineer_features, walk_forward_cv,
)


# ── Cohort assignment for multi-class head ───────────────────────────
COHORT_BOUNDS = {
    "ELITE":   0.40,   # ret_t5 >= 0.40
    "HIGH":    0.25,   # 0.25 <= ret_t5 < 0.40
    "VETOED":  0.15,   # 0.15 <= ret_t5 < 0.25
    "BROAD":   0.10,   # 0.10 <= ret_t5 < 0.15
    # SKIP: ret_t5 < 0.10 (label = 0 in cohort head)
}
COHORT_TO_IDX = {"SKIP": 0, "BROAD": 1, "VETOED": 2, "HIGH": 3, "ELITE": 4}
N_COHORT = len(COHORT_TO_IDX)


def assign_cohort(ret_t5: float) -> int:
    if ret_t5 >= COHORT_BOUNDS["ELITE"]:  return COHORT_TO_IDX["ELITE"]
    if ret_t5 >= COHORT_BOUNDS["HIGH"]:   return COHORT_TO_IDX["HIGH"]
    if ret_t5 >= COHORT_BOUNDS["VETOED"]: return COHORT_TO_IDX["VETOED"]
    if ret_t5 >= COHORT_BOUNDS["BROAD"]:  return COHORT_TO_IDX["BROAD"]
    return COHORT_TO_IDX["SKIP"]


# ── Sequence loader: pads/truncates each (ticker, d0) to fixed-length ─

SEQ_LEN = 30      # target minute-bar count
SEQ_FEAT_COLS = ["open_rel", "high_rel", "low_rel", "close_rel",
                   "vol_z", "log_trans"]


def build_sequence_lookup() -> dict:
    """Pre-load intraday paths into a {(ticker, d0): np.ndarray(SEQ_LEN, 6)} dict.
    Pads with zeros + a binary mask channel later if needed; for now we just
    pad/truncate to SEQ_LEN."""
    paths_path = DERIVED / "intraday_paths_30min.parquet"
    df = pd.read_parquet(paths_path)
    df["d0"] = pd.to_datetime(df["d0"])
    df = df.sort_values(["ticker", "d0", "bar_idx"])

    # Group into per-(ticker, d0) arrays
    lookup: dict = {}
    for (tkr, d), grp in df.groupby(["ticker", "d0"], sort=False):
        arr = grp[SEQ_FEAT_COLS].values.astype(np.float32)
        n = arr.shape[0]
        if n >= SEQ_LEN:
            arr = arr[:SEQ_LEN]
        else:
            # Right-pad with zeros
            pad = np.zeros((SEQ_LEN - n, len(SEQ_FEAT_COLS)), dtype=np.float32)
            arr = np.concatenate([arr, pad], axis=0)
        lookup[(tkr, d)] = arr
    return lookup


def lookup_sequences(tickers: list, dates: pd.Series,
                       seq_lookup: dict) -> np.ndarray:
    """Returns (B, SEQ_LEN, 6). Missing keys get all-zero rows (has_path=0
    is already a tabular feature, so the model can learn to ignore zero-paths)."""
    out = np.zeros((len(tickers), SEQ_LEN, len(SEQ_FEAT_COLS)), dtype=np.float32)
    dates_pd = pd.to_datetime(dates)
    for i, (t, d) in enumerate(zip(tickers, dates_pd)):
        # d0 in lookup is a Timestamp; in sequences they may be date()
        key = (t, d)
        if key in seq_lookup:
            out[i] = seq_lookup[key]
        else:
            # Try converting to plain date
            key2 = (t, d.date() if hasattr(d, "date") else d)
            if key2 in seq_lookup:
                out[i] = seq_lookup[key2]
    return out


# ── Model ────────────────────────────────────────────────────────────


def build_model(n_tab_features: int, d_model: int = 64,
                  n_layers: int = 4, dropout: float = 0.10,
                  disable_sequence: bool = False):
    """MoMTrans architecture. Returns torch.nn.Module.

    When `disable_sequence=True`, the sequence branch is zeroed out at
    forward time (still constructed for state-dict compatibility, but its
    output is multiplied by 0 before fusion). Used by the tabular-only
    ablation to test whether the time-transformer adds signal at all.
    """
    import torch
    import torch.nn as nn

    class _SinusoidalPosEnc(nn.Module):
        def __init__(self, d_model: int, max_len: int = 64):
            super().__init__()
            pe = torch.zeros(max_len, d_model)
            pos = torch.arange(0, max_len).unsqueeze(1).float()
            div = torch.exp(torch.arange(0, d_model, 2).float() *
                              -(math.log(10000.0) / d_model))
            pe[:, 0::2] = torch.sin(pos * div)
            pe[:, 1::2] = torch.cos(pos * div)
            self.register_buffer("pe", pe.unsqueeze(0))

        def forward(self, x):  # (B, L, D)
            return x + self.pe[:, :x.size(1), :]

    _disable_seq = bool(disable_sequence)

    class MoMTrans(nn.Module):
        def __init__(self):
            super().__init__()
            self.disable_sequence = _disable_seq
            # Tabular branch
            self.tab_proj = nn.Linear(1, d_model)  # per-feature scalar -> d_model
            self.tab_pos = nn.Parameter(torch.randn(1, n_tab_features, d_model) * 0.02)
            tab_layer = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=4, dim_feedforward=d_model*2,
                dropout=dropout, batch_first=True, activation="gelu",
            )
            self.tab_enc = nn.TransformerEncoder(tab_layer, num_layers=n_layers)
            self.tab_pool_q = nn.Parameter(torch.randn(1, 1, d_model) * 0.02)

            # Sequence branch
            self.seq_proj = nn.Linear(len(SEQ_FEAT_COLS), d_model)
            self.seq_posenc = _SinusoidalPosEnc(d_model, max_len=SEQ_LEN + 4)
            self.seq_cls = nn.Parameter(torch.randn(1, 1, d_model) * 0.02)
            seq_layer = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=4, dim_feedforward=d_model*2,
                dropout=dropout, batch_first=True, activation="gelu",
            )
            self.seq_enc = nn.TransformerEncoder(seq_layer, num_layers=n_layers)

            # Regime embedding (HI=0, MID=1, LO=2)
            self.regime_emb = nn.Embedding(3, d_model)

            # Cross-attention fusion: 3 modalities each as 1 token
            fuse_layer = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=4, dim_feedforward=d_model*2,
                dropout=dropout, batch_first=True, activation="gelu",
            )
            self.fuse_enc = nn.TransformerEncoder(fuse_layer, num_layers=2)

            # Shared MLP
            self.shared = nn.Sequential(
                nn.Linear(d_model * 3, d_model * 2),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(d_model * 2, d_model),
            )

            # Heads
            self.head_binary = nn.Linear(d_model, 1)
            self.head_cohort = nn.Linear(d_model, N_COHORT)
            self.head_quantile = nn.Linear(d_model, 3)  # q10, q50, q90
            self.head_magnitude = nn.Linear(d_model, 1)

        def forward(self, tab: "torch.Tensor", seq: "torch.Tensor",
                     regime: "torch.Tensor"):
            B = tab.size(0)
            # Tabular: each scalar -> embedding, add learned positional, encode
            tab_e = self.tab_proj(tab.unsqueeze(-1)) + self.tab_pos
            tab_q = self.tab_pool_q.expand(B, -1, -1)
            tab_seq = torch.cat([tab_q, tab_e], dim=1)
            tab_h = self.tab_enc(tab_seq)
            tab_pooled = tab_h[:, 0, :]  # CLS token

            # Sequence: minute-bar features -> encode -> CLS pool.
            # When disable_sequence=True, zero the contribution so the
            # fusion sees only tabular + regime (tabular-only ablation).
            if self.disable_sequence:
                seq_pooled = torch.zeros(B, self.seq_cls.size(-1),
                                            device=tab.device, dtype=tab.dtype)
            else:
                seq_e = self.seq_proj(seq)
                seq_e = self.seq_posenc(seq_e)
                cls_tok = self.seq_cls.expand(B, -1, -1)
                seq_in = torch.cat([cls_tok, seq_e], dim=1)
                seq_h = self.seq_enc(seq_in)
                seq_pooled = seq_h[:, 0, :]

            # Regime embedding
            reg_pooled = self.regime_emb(regime)

            # Fusion: 3 modalities as tokens
            fuse_in = torch.stack([tab_pooled, seq_pooled, reg_pooled], dim=1)
            fuse_h = self.fuse_enc(fuse_in)
            fused = fuse_h.flatten(1)  # (B, 3 * d_model)

            shared = self.shared(fused)

            return {
                "binary":     self.head_binary(shared).squeeze(-1),
                "cohort":     self.head_cohort(shared),
                "quantile":   self.head_quantile(shared),
                "magnitude":  self.head_magnitude(shared).squeeze(-1),
            }

    return MoMTrans()


# ── Losses ───────────────────────────────────────────────────────────


def quantile_loss(pred, target, quantiles=(0.1, 0.5, 0.9)):
    """Pinball loss over multiple quantiles. pred: (B, K). target: (B,)."""
    import torch
    losses = []
    for i, q in enumerate(quantiles):
        diff = target - pred[:, i]
        losses.append(torch.maximum(q * diff, (q - 1) * diff))
    return torch.stack(losses, dim=1).mean()


def joint_loss(out, y_binary, y_cohort, y_continuous,
                 weights=(0.55, 0.25, 0.10, 0.10),
                 binary_pos_weight: float = 4.0,
                 cohort_class_weights=None):
    """v2 task-weight balance — classification heads get 80% of gradient
    because v1 collapsed (model learned to always predict "no"). Empirically
    on a 21%-positive dataset, BCE without pos_weight floors prob at ~0.20.

    binary_pos_weight ≈ n_neg / n_pos (4.0 = 1:0.25 imbalance) makes the
    model take positives seriously. cohort_class_weights similarly upweights
    the rare ELITE class (~7% of training rows).
    """
    import torch
    import torch.nn.functional as F
    pos_w = torch.tensor(binary_pos_weight, device=out["binary"].device,
                          dtype=out["binary"].dtype)
    L_bin = F.binary_cross_entropy_with_logits(
        out["binary"], y_binary.float(), pos_weight=pos_w
    )
    L_coh = F.cross_entropy(out["cohort"], y_cohort, weight=cohort_class_weights)
    L_qnt = quantile_loss(out["quantile"], y_continuous)
    L_mag = F.huber_loss(out["magnitude"], y_continuous, delta=0.10)
    total = (weights[0] * L_bin + weights[1] * L_coh
             + weights[2] * L_qnt + weights[3] * L_mag)
    return total, {"bce": L_bin.item(), "ce": L_coh.item(),
                    "quantile": L_qnt.item(), "huber": L_mag.item(),
                    "total": total.item()}


# ── Trainer ──────────────────────────────────────────────────────────


def train_one_fold(fold_i, X_tab, X_seq, regime, y_reg, dates, tr_mask, te_mask,
                     args, device):
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    Xtr_tab = torch.from_numpy(X_tab[tr_mask].astype(np.float32))
    Xtr_seq = torch.from_numpy(X_seq[tr_mask])
    reg_tr = torch.from_numpy(regime[tr_mask].astype(np.int64))
    yreg_tr = torch.from_numpy(y_reg[tr_mask].astype(np.float32))

    Xte_tab = torch.from_numpy(X_tab[te_mask].astype(np.float32)).to(device)
    Xte_seq = torch.from_numpy(X_seq[te_mask]).to(device)
    reg_te = torch.from_numpy(regime[te_mask].astype(np.int64)).to(device)

    # Tier-specialist override: the train script reads MX_MOMTRANS_TIER_THRESHOLD
    # to redefine y_cls. Used by ml_v4_momtrans_tier_specialists.py to train 4
    # cohort-specific tabular models (echoing D281's cohort cascade).
    import os as _os
    _tier_threshold = float(_os.environ.get("MX_MOMTRANS_TIER_THRESHOLD", "0.10"))
    y_bin = (yreg_tr >= _tier_threshold).float()
    y_coh = torch.tensor([assign_cohort(r) for r in yreg_tr.numpy()], dtype=torch.long)

    # v2 balanced cross-entropy: inverse-frequency weights for the 5 cohort
    # classes (SKIP/BROAD/VETOED/HIGH/ELITE). Without weighting the model
    # collapses to always-SKIP because SKIP is ~80% of rows. Capped at 5x
    # to avoid extreme gradient noise on tiny ELITE class.
    coh_counts = torch.bincount(y_coh, minlength=N_COHORT).clamp(min=1).float()
    coh_weights_t = (coh_counts.sum() / (N_COHORT * coh_counts)).clamp(max=5.0).to(device)

    train_ds = TensorDataset(Xtr_tab, Xtr_seq, reg_tr, yreg_tr, y_bin, y_coh)
    train_dl = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True,
                            drop_last=False)

    model = build_model(n_tab_features=X_tab.shape[1],
                          d_model=args.d_model, n_layers=args.n_layers,
                          dropout=args.dropout,
                          disable_sequence=args._disable_sequence).to(device)
    # Warm-start tabular encoder from SSL pre-training (Phase 2)
    if getattr(args, "_ssl_encoder_state", None) is not None:
        own_state = model.state_dict()
        loaded_keys = []
        for k, v in args._ssl_encoder_state.items():
            if k in own_state and own_state[k].shape == v.shape:
                own_state[k].copy_(v.to(own_state[k].device))
                loaded_keys.append(k)
        print(f"    fold {fold_i:2d} SSL warm-start: loaded {len(loaded_keys)} encoder tensors")
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr_max, weight_decay=1e-4)
    n_total_steps = args.epochs * max(1, len(train_dl))
    scheduler = torch.optim.lr_scheduler.OneCycleLR(
        opt, max_lr=args.lr_max, total_steps=n_total_steps, pct_start=0.1,
    )
    # Precision selection: default fp32 for numerical safety on this dataset
    use_amp = args.precision in ("bf16", "fp16") and device.type == "cuda"
    scaler_dtype = {"bf16": torch.bfloat16, "fp16": torch.float16}.get(
        args.precision, torch.float32
    )

    best_val = float("inf")
    best_state = None
    patience = args.patience
    bad_epochs = 0
    for epoch in range(args.epochs):
        model.train()
        losses = []
        for batch in train_dl:
            tab_b, seq_b, reg_b, ycontin_b, ybin_b, ycoh_b = [
                t.to(device, non_blocking=True) for t in batch
            ]
            opt.zero_grad()
            if use_amp:
                with torch.autocast(device_type=device.type, dtype=scaler_dtype):
                    out = model(tab_b, seq_b, reg_b)
                    loss, metrics = joint_loss(
                        out, ybin_b, ycoh_b, ycontin_b,
                        weights=args._task_weights,
                        cohort_class_weights=coh_weights_t,
                    )
            else:
                out = model(tab_b, seq_b, reg_b)
                loss, metrics = joint_loss(out, ybin_b, ycoh_b, ycontin_b,
                                              cohort_class_weights=coh_weights_t)
            if not torch.isfinite(loss):
                # Skip the bad batch instead of corrupting weights with NaN grads.
                # Surfaces in the per-epoch print as a lower-than-expected loss
                # if it happens often.
                continue
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            scheduler.step()
            losses.append(metrics["total"])
        if not losses:
            print(f"    fold {fold_i:2d} epoch {epoch:3d} ALL BATCHES NAN — abort")
            break

        train_avg = float(np.mean(losses))
        if epoch % 5 == 0 or epoch == args.epochs - 1:
            print(f"    fold {fold_i:2d} epoch {epoch:3d} train_loss={train_avg:.4f}")

        # Early stopping on training loss plateau
        if train_avg < best_val - 1e-4:
            best_val = train_avg
            bad_epochs = 0
            best_state = {k: v.detach().cpu().clone()
                            for k, v in model.state_dict().items()}
        else:
            bad_epochs += 1
            if bad_epochs >= patience:
                print(f"    fold {fold_i:2d} early stop at epoch {epoch}")
                break

    if best_state is not None:
        model.load_state_dict(best_state)

    # Final test predictions (always FP32 for stable numerical output)
    model.eval()
    with torch.no_grad():
        out_te = model(Xte_tab, Xte_seq, reg_te)
        prob_binary = torch.sigmoid(out_te["binary"]).float().cpu().numpy()
        cohort_logits = out_te["cohort"].float().cpu().numpy()
        cohort_pred = np.argmax(cohort_logits, axis=1)
        quantiles = out_te["quantile"].float().cpu().numpy()
        magnitude = out_te["magnitude"].float().cpu().numpy()
    return {
        "model_state": best_state,
        "prob_binary": prob_binary,
        "cohort_pred": cohort_pred,
        "cohort_logits": cohort_logits,
        "quantiles": quantiles,
        "magnitude": magnitude,
        "test_indices": np.where(te_mask)[0],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true",
                        help="Single fold, 5 epochs, batch=32 (~2 min)")
    parser.add_argument("--full-wf", action="store_true",
                        help="16-fold WF, 100 epochs/fold (~4-6 hr)")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--d-model", type=int, default=64)
    parser.add_argument("--n-layers", type=int, default=4)
    parser.add_argument("--dropout", type=float, default=0.10)
    parser.add_argument("--lr-max", type=float, default=3e-4)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--device", type=str, default="auto")
    parser.add_argument("--precision", type=str, default="fp32",
                        choices=["fp32", "bf16", "fp16"],
                        help="Mixed-precision mode (default fp32 for stability)")
    parser.add_argument("--variant", type=str, default="default",
                        choices=["default", "tabular_only", "cls_only", "bigger"],
                        help="Ablation variant. tabular_only zeros sequence "
                             "branch; cls_only sets quantile+magnitude weights "
                             "to 0; bigger overrides d_model=128 + n_layers=6.")
    parser.add_argument("--out-suffix", type=str, default="",
                        help="Suffix for output filenames (e.g. '_tabular_only')")
    parser.add_argument("--load-ssl-encoder", type=str, default=None,
                        help="Path to data/models/momtrans_v4_ssl_encoder.pt — "
                             "warm-start the tabular encoder from SSL pre-training")
    args = parser.parse_args()

    if args.smoke:
        args.epochs = args.epochs or 5
        args.batch_size = 32
    elif args.full_wf:
        args.epochs = args.epochs or 100
    else:
        args.epochs = args.epochs or 30

    # Variant overrides — applied after the smoke/full-wf defaults so the
    # bigger variant inherits the right epoch budget by default.
    args._task_weights = (0.55, 0.25, 0.10, 0.10)  # default: cls-heavy
    args._disable_sequence = False
    if args.variant == "tabular_only":
        args._disable_sequence = True
    elif args.variant == "cls_only":
        args._task_weights = (0.70, 0.30, 0.0, 0.0)
    elif args.variant == "bigger":
        args.d_model = max(args.d_model, 128)
        args.n_layers = max(args.n_layers, 6)
    print(f"\n  variant: {args.variant}  task_weights={args._task_weights}  "
          f"disable_sequence={args._disable_sequence}")

    # Optional SSL encoder warm-start
    args._ssl_encoder_state = None
    if args.load_ssl_encoder:
        import torch as _torch
        ssl_path = Path(args.load_ssl_encoder)
        if not ssl_path.exists():
            print(f"  WARN: --load-ssl-encoder {ssl_path} missing — cold-start instead")
        else:
            ssl_blob = _torch.load(ssl_path, map_location="cpu", weights_only=False)
            args._ssl_encoder_state = ssl_blob["encoder_state"]
            print(f"  SSL warm-start loaded from {ssl_path.name} "
                  f"({len(args._ssl_encoder_state)} tensors)")

    import torch
    device = torch.device(args.device if args.device != "auto"
                            else ("cuda" if torch.cuda.is_available() else "cpu"))
    print(f"\nMoMTrans v4 trainer — device={device}")
    print(f"  config: d_model={args.d_model} n_layers={args.n_layers} "
          f"dropout={args.dropout} epochs={args.epochs} batch={args.batch_size}")

    print("\n[STEP 1] Load + engineer 54-feature tabular set")
    df = load_data(include_paths=True)
    X_tab_df = engineer_features(df).replace([np.inf, -np.inf], 0).fillna(0)
    y_reg = df["ret_t5"].clip(-0.50, 1.00).values
    dates = df["d0"]

    # Standardize tabular features (z-score) — transformer wants normalized inputs
    X_tab = X_tab_df.values.astype(np.float32)
    means = X_tab.mean(axis=0, keepdims=True)
    stds = X_tab.std(axis=0, keepdims=True) + 1e-6
    X_tab = (X_tab - means) / stds
    print(f"  tabular: {X_tab.shape}, n_features={X_tab.shape[1]}")

    print("\n[STEP 2] Build sequence lookup from intraday_paths_30min")
    t0 = time.perf_counter()
    seq_lookup = build_sequence_lookup()
    print(f"  loaded {len(seq_lookup):,} sequences in {time.perf_counter()-t0:.1f}s")

    print("\n[STEP 3] Build aligned sequence array")
    X_seq = lookup_sequences(df["ticker"].tolist(), dates, seq_lookup)
    has_seq = X_seq.sum(axis=(1, 2)) != 0
    print(f"  sequence: {X_seq.shape}, has_path coverage: {has_seq.mean()*100:.2f}%")

    print("\n[STEP 4] Compute regime indices (HI=0, MID=1, LO=2) from Ising")
    # Reuse Ising mag_5d join
    import duckdb
    con = duckdb.connect()
    con.sql(f"""CREATE TABLE i AS SELECT *,
        AVG(magnetization) OVER (ORDER BY d ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)
        AS mag_5d FROM read_parquet('{(DERIVED / "ising_daily.parquet").as_posix()}')""")
    mag_df = con.sql("""SELECT d AS d0,
        CASE WHEN mag_5d < -0.05 THEN 2 WHEN mag_5d > 0.05 THEN 0 ELSE 1 END AS regime_idx
        FROM i""").df()
    mag_df["d0"] = pd.to_datetime(mag_df["d0"])
    df_with_reg = df[["d0"]].copy()
    df_with_reg["d0"] = pd.to_datetime(df_with_reg["d0"])
    regime = df_with_reg.merge(mag_df, on="d0", how="left")["regime_idx"].fillna(1).astype(np.int64).values
    print(f"  regime distribution: HI={np.sum(regime==0)}, MID={np.sum(regime==1)}, LO={np.sum(regime==2)}")

    print("\n[STEP 5] Walk-forward training")
    folds = list(walk_forward_cv(dates, train_window_days=365, test_window_days=30))
    if args.smoke:
        folds = folds[:1]
        print(f"  SMOKE: single fold only (skipping {len(folds)-1} folds)")
    print(f"  n_folds: {len(folds)}")

    all_predictions = []
    fold_summaries = []
    t_start = time.perf_counter()
    for fold_i, (tr_mask, te_mask) in enumerate(folds):
        print(f"\n  ── FOLD {fold_i+1}/{len(folds)} ──")
        result = train_one_fold(fold_i, X_tab, X_seq, regime, y_reg, dates,
                                  tr_mask, te_mask, args, device)
        # Aggregate predictions
        pred_df = pd.DataFrame({
            "fold": fold_i,
            "d0": pd.to_datetime(df.loc[te_mask, "d0"]).values,
            "ticker": df.loc[te_mask, "ticker"].values,
            "y_cls": (y_reg[te_mask] >= 0.10).astype(int),
            "y_reg": y_reg[te_mask],
            "prob_binary": result["prob_binary"],
            "cohort_pred": result["cohort_pred"],
            "q10": result["quantiles"][:, 0],
            "q50": result["quantiles"][:, 1],
            "q90": result["quantiles"][:, 2],
            "magnitude_pred": result["magnitude"],
        })
        all_predictions.append(pred_df)
        fold_summaries.append({
            "fold": fold_i,
            "n_test": int(te_mask.sum()),
            "binary_p30_n": int((result["prob_binary"] >= 0.30).sum()),
            "binary_p30_avg": float(y_reg[te_mask][result["prob_binary"] >= 0.30].mean())
                                if (result["prob_binary"] >= 0.30).any() else 0.0,
            "elite_pred_n": int((result["cohort_pred"] == COHORT_TO_IDX["ELITE"]).sum()),
        })

    elapsed = time.perf_counter() - t_start
    print(f"\n[STEP 6] Aggregate ({elapsed:.1f}s total, {elapsed/len(folds):.1f}s/fold)")
    all_preds = pd.concat(all_predictions, ignore_index=True)
    out_preds = MODELS / f"momtrans_v4{args.out_suffix}_predictions.parquet"
    all_preds.to_parquet(out_preds, compression="zstd")
    print(f"  -> {out_preds}  ({len(all_preds):,} rows)")

    summary = {
        "config": {
            "d_model": args.d_model, "n_layers": args.n_layers,
            "dropout": args.dropout, "epochs": args.epochs,
            "batch_size": args.batch_size, "lr_max": args.lr_max,
            "smoke": args.smoke, "full_wf": args.full_wf,
            "variant": args.variant,
            "task_weights": list(args._task_weights),
            "disable_sequence": args._disable_sequence,
        },
        "device": str(device),
        "n_features_tabular": X_tab.shape[1],
        "seq_len": SEQ_LEN, "seq_features": len(SEQ_FEAT_COLS),
        "n_folds": len(folds),
        "elapsed_s": elapsed,
        "fold_summaries": fold_summaries,
    }
    out_sum = MODELS / f"momtrans_v4{args.out_suffix}_summary.json"
    out_sum.write_text(json.dumps(summary, indent=2))
    print(f"  -> {out_sum}")

    # ── STEP 7: Persist final production model ────────────────────────
    # Train ONE more model on the full data minus a 5-day holdout (mirrors
    # the v3 production training pattern in ml_continuer_v2_ensemble.py).
    # This is the artifact MetaScorer.load_default() will load when
    # MX_USE_MOMTRANS=1. Without this step, the WF predictions parquet
    # is the only output and there's no model to call at inference time.
    if args.full_wf:
        print(f"\n[STEP 7] Train + persist final production model")
        cutoff = pd.to_datetime(dates).max() - timedelta(days=5)
        final_mask = (pd.to_datetime(dates) < cutoff).values
        Xf_tab = X_tab[final_mask]
        Xf_seq = X_seq[final_mask]
        regf = regime[final_mask]
        yreg_f = y_reg[final_mask]
        # Reuse train_one_fold by faking a 100% train mask + tiny test
        fake_te = np.zeros(len(yreg_f), dtype=bool)
        fake_te[-1] = True  # need >=1 row for the eval batch
        fake_tr = ~fake_te
        # Patch: train_one_fold expects df.loc[te_mask, "ret_t5"]; build a tiny
        # df slice that matches the structure
        df_final = df[final_mask].reset_index(drop=True)
        result = train_one_fold(
            999, Xf_tab, Xf_seq, regf, yreg_f, df_final["d0"],
            fake_tr, fake_te, args, device,
        )
        final_pt = MODELS / f"momtrans_v4{args.out_suffix}.pt"
        # Tier threshold (env override or default 0.10) for downstream cascade
        _tier_threshold = float(os.environ.get("MX_MOMTRANS_TIER_THRESHOLD", "0.10"))
        torch.save({
            "model_state": result["model_state"],
            "config": {
                "d_model": args.d_model,
                "n_layers": args.n_layers,
                "dropout": args.dropout,
                "n_features": X_tab.shape[1],
                "seq_len": SEQ_LEN,
                "seq_features": len(SEQ_FEAT_COLS),
                "task_weights": list(args._task_weights),
                "disable_sequence": args._disable_sequence,
                "tier_threshold": _tier_threshold,
                "tier_name": os.environ.get("MX_MOMTRANS_TIER_NAME", "DEFAULT"),
            },
            "feature_columns": list(X_tab_df.columns),
            "feature_means": means.flatten().tolist(),
            "feature_stds": stds.flatten().tolist(),
            "training_rows": int(final_mask.sum()),
            "trained_at": pd.Timestamp.utcnow().isoformat(),
        }, final_pt)
        print(f"  -> {final_pt}")

    # Quick aggregate print
    if all_preds.empty:
        print("\n  (no predictions to aggregate)")
        return 0
    print(f"\n  AGGREGATE BINARY HEAD @ p>=0.30:")
    mask = all_preds["prob_binary"] >= 0.30
    print(f"    n={int(mask.sum()):,}  avg_ret_t5={all_preds.loc[mask, 'y_reg'].mean()*100:+.2f}%  "
          f"win={(all_preds.loc[mask, 'y_reg'] > 0).mean()*100:.1f}%")
    print(f"\n  COHORT HEAD ELITE predictions:")
    elite_mask = all_preds["cohort_pred"] == COHORT_TO_IDX["ELITE"]
    if elite_mask.sum() > 0:
        print(f"    n={int(elite_mask.sum()):,}  avg_ret_t5="
              f"{all_preds.loc[elite_mask, 'y_reg'].mean()*100:+.2f}%  "
              f"win={(all_preds.loc[elite_mask, 'y_reg'] > 0).mean()*100:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
