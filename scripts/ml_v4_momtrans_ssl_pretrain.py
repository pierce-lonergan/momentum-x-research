"""Self-supervised pre-training for the MoMTrans tabular encoder.

Per docs/research-log/127_momtrans_v4_ablations.md Phase 2: pre-train the
TabTransformer encoder via masked feature reconstruction (BERT/MAE-style)
on UNLABELED rows. The hypothesis: 20k labeled rows is small for a
transformer; SSL pre-training compresses the input distribution into
the encoder's hidden state, then the small supervised dataset just
fine-tunes the heads.

Method:
  1. Take ALL aftermath_strat rows (20k+, including those with no y_reg)
  2. For each row, randomly mask 30% of tabular features
  3. Train an encoder + reconstruction head to predict the original
     masked features (MSE loss on continuous, CE on categorical)
  4. Save encoder state_dict to data/models/momtrans_v4_ssl_encoder.pt
  5. Downstream: ml_v4_momtrans_train.py --load-ssl-encoder loads
     these weights as the starting point for supervised training

USAGE
  python scripts/ml_v4_momtrans_ssl_pretrain.py --epochs 200 --d-model 64

OUTPUT (gitignored)
  data/models/momtrans_v4_ssl_encoder.pt
  data/models/momtrans_v4_ssl_summary.json
"""
from __future__ import annotations
import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"

sys.path.insert(0, str(REPO / "scripts"))
from ml_continuer_v2_ensemble import load_data, engineer_features  # type: ignore


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def build_ssl_model(n_features: int, d_model: int = 64,
                       n_layers: int = 4, dropout: float = 0.10):
    """TabTransformer encoder + reconstruction head. The encoder is the
    SAME architecture as the supervised model's TabularBranch, so its
    weights can be transferred directly via state_dict."""
    import torch
    import torch.nn as nn

    class _TabSSL(nn.Module):
        def __init__(self):
            super().__init__()
            # Encoder (matches build_model's tab branch)
            self.tab_proj = nn.Linear(1, d_model)
            self.tab_pos = nn.Parameter(torch.randn(1, n_features, d_model) * 0.02)
            self.tab_pool_q = nn.Parameter(torch.randn(1, 1, d_model) * 0.02)
            tab_layer = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=4, dim_feedforward=d_model*2,
                dropout=dropout, batch_first=True, activation="gelu",
            )
            self.tab_enc = nn.TransformerEncoder(tab_layer, num_layers=n_layers)
            # MASK token — substituted at masked positions
            self.mask_token = nn.Parameter(torch.randn(1, 1, d_model) * 0.02)
            # Reconstruction head: per-position scalar output
            self.recon_head = nn.Sequential(
                nn.Linear(d_model, d_model),
                nn.GELU(),
                nn.Linear(d_model, 1),
            )

        def encode(self, x: torch.Tensor, mask: torch.Tensor):
            """x: (B, F), mask: (B, F) bool — True at masked positions."""
            B, F = x.shape
            tok = self.tab_proj(x.unsqueeze(-1))  # (B, F, D)
            # Substitute mask token at masked positions
            mask_expanded = mask.unsqueeze(-1)  # (B, F, 1)
            tok = torch.where(mask_expanded, self.mask_token.expand_as(tok), tok)
            tok = tok + self.tab_pos
            cls = self.tab_pool_q.expand(B, -1, -1)
            seq = torch.cat([cls, tok], dim=1)
            h = self.tab_enc(seq)
            return h  # (B, F+1, D)  [0]=CLS, [1:]=feature tokens

        def forward(self, x: torch.Tensor, mask: torch.Tensor):
            h = self.encode(x, mask)
            # Recon all positions; we only score the masked ones in loss
            recon = self.recon_head(h[:, 1:, :]).squeeze(-1)  # (B, F)
            return recon

    return _TabSSL()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--d-model", type=int, default=64)
    parser.add_argument("--n-layers", type=int, default=4)
    parser.add_argument("--dropout", type=float, default=0.10)
    parser.add_argument("--lr-max", type=float, default=3e-4)
    parser.add_argument("--mask-pct", type=float, default=0.30)
    parser.add_argument("--device", type=str, default="auto")
    args = parser.parse_args()

    import torch
    device = torch.device(args.device if args.device != "auto"
                            else ("cuda" if torch.cuda.is_available() else "cpu"))
    print(f"\nMoMTrans SSL pre-training — device={device}")
    print(f"  config: d_model={args.d_model} n_layers={args.n_layers} "
          f"dropout={args.dropout} mask_pct={args.mask_pct} epochs={args.epochs}")

    section("STEP 1 — Load + engineer 54-feature tabular set")
    t0 = time.perf_counter()
    df = load_data(include_paths=True)
    X_df = engineer_features(df).replace([np.inf, -np.inf], 0).fillna(0)
    print(f"  loaded {len(X_df):,} rows in {time.perf_counter()-t0:.1f}s")

    X = X_df.values.astype(np.float32)
    means = X.mean(axis=0, keepdims=True)
    stds = X.std(axis=0, keepdims=True) + 1e-6
    X = (X - means) / stds

    section("STEP 2 — Build SSL model")
    model = build_ssl_model(n_features=X.shape[1], d_model=args.d_model,
                              n_layers=args.n_layers, dropout=args.dropout).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"  model params: {n_params:,}")

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr_max, weight_decay=1e-4)
    n_iter_per_epoch = int(np.ceil(len(X) / args.batch_size))
    scheduler = torch.optim.lr_scheduler.OneCycleLR(
        opt, max_lr=args.lr_max,
        total_steps=args.epochs * max(1, n_iter_per_epoch),
        pct_start=0.05,
    )

    section("STEP 3 — Pre-train (masked feature reconstruction)")
    X_t = torch.from_numpy(X)
    rng = np.random.RandomState(42)
    losses_history = []
    t_start = time.perf_counter()
    for epoch in range(args.epochs):
        perm = rng.permutation(len(X))
        epoch_losses = []
        for batch_start in range(0, len(X), args.batch_size):
            idx = perm[batch_start:batch_start + args.batch_size]
            x = X_t[idx].to(device, non_blocking=True)
            B, F = x.shape
            # Random mask: each feature is independently masked with prob=mask_pct
            mask = (torch.rand(B, F, device=device) < args.mask_pct)
            opt.zero_grad()
            recon = model(x, mask)
            # MSE loss on masked positions only
            sq_err = (recon - x) ** 2
            loss = (sq_err * mask.float()).sum() / mask.float().sum().clamp(min=1)
            if not torch.isfinite(loss):
                continue
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            scheduler.step()
            epoch_losses.append(loss.item())
        if not epoch_losses:
            print(f"    epoch {epoch:3d} ALL BATCHES NAN — abort")
            break
        avg = float(np.mean(epoch_losses))
        losses_history.append(avg)
        if epoch % 10 == 0 or epoch == args.epochs - 1:
            print(f"    epoch {epoch:3d} mse_loss={avg:.4f}")

    elapsed = time.perf_counter() - t_start
    final_loss = losses_history[-1] if losses_history else float("nan")
    print(f"\n  Pre-training done in {elapsed:.1f}s, final loss = {final_loss:.4f}")

    section("STEP 4 — Persist encoder (NOT the recon head)")
    # Save ONLY the encoder pieces that match the supervised model's tab branch.
    encoder_state = {
        "tab_proj.weight":   model.tab_proj.weight.detach().cpu(),
        "tab_proj.bias":     model.tab_proj.bias.detach().cpu(),
        "tab_pos":           model.tab_pos.detach().cpu(),
        "tab_pool_q":        model.tab_pool_q.detach().cpu(),
    }
    # tab_enc is a TransformerEncoder; capture its full state
    for k, v in model.tab_enc.state_dict().items():
        encoder_state[f"tab_enc.{k}"] = v.detach().cpu()

    out_pt = MODELS / "momtrans_v4_ssl_encoder.pt"
    torch.save({
        "encoder_state": encoder_state,
        "config": {
            "d_model": args.d_model, "n_layers": args.n_layers,
            "dropout": args.dropout, "n_features": X.shape[1],
            "mask_pct": args.mask_pct, "epochs": args.epochs,
        },
        "feature_means": means.flatten().tolist(),
        "feature_stds": stds.flatten().tolist(),
    }, out_pt)
    print(f"  -> {out_pt}")

    summary = {
        "n_rows": len(X),
        "n_features": X.shape[1],
        "n_params": n_params,
        "epochs": args.epochs,
        "elapsed_s": elapsed,
        "final_loss": final_loss,
        "loss_history": losses_history,
        "config": {
            "d_model": args.d_model, "n_layers": args.n_layers,
            "dropout": args.dropout, "mask_pct": args.mask_pct,
            "lr_max": args.lr_max, "batch_size": args.batch_size,
        },
    }
    out_sum = MODELS / "momtrans_v4_ssl_summary.json"
    out_sum.write_text(json.dumps(summary, indent=2))
    print(f"  -> {out_sum}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
