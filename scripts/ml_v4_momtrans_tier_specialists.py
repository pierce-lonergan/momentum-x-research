"""Tier-specific tabular MoMTrans models — echoes D281 cohort cascade.

Per docs/research-log/127_momtrans_v4_ablations.md Phase 3: D281 already
proved that 4 cohort-specialized models (each on a different ret_t5
threshold) beat a single-objective model in production. This script
applies the same pattern but with the MoMTrans tabular-only architecture
as the backbone instead of the v3 stacked ensemble.

For each tier T in {ELITE, HIGH, VETOED, BROAD}, train a separate
MoMTrans tabular-only model with that tier's binary y-label
(ret_t5 >= threshold[T]). The 4 models share architecture but specialize
via the y-label.

USAGE
  python scripts/ml_v4_momtrans_tier_specialists.py \\
      --epochs 100 --d-model 64 --n-layers 4

OUTPUT (gitignored)
  data/models/momtrans_v4_tier_{ELITE,HIGH,VETOED,BROAD}_predictions.parquet
  data/models/momtrans_v4_tier_specialists_summary.json
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
MODELS = REPO / "data" / "models"

TIERS = {
    "BROAD":  0.10,
    "VETOED": 0.15,
    "HIGH":   0.25,
    "ELITE":  0.40,
}


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--d-model", type=int, default=64)
    parser.add_argument("--n-layers", type=int, default=4)
    parser.add_argument("--dropout", type=float, default=0.10)
    parser.add_argument("--lr-max", type=float, default=3e-4)
    parser.add_argument("--patience", type=int, default=15)
    parser.add_argument("--load-ssl-encoder", type=str, default=None,
                        help="Path to SSL pre-trained encoder for warm-start")
    args = parser.parse_args()

    section("D281-style tier specialists with MoMTrans tabular backbone")
    print(f"  config: d_model={args.d_model} n_layers={args.n_layers} "
          f"dropout={args.dropout} epochs={args.epochs}")
    print(f"  4 tiers: {TIERS}")
    if args.load_ssl_encoder:
        print(f"  SSL warm-start from: {args.load_ssl_encoder}")

    # Each tier runs as a separate subprocess. The trainer's --variant flag
    # only handles 4 variants (default/tabular_only/cls_only/bigger), so we
    # use environment variables to pass the per-tier ret_t5 threshold to a
    # patched trainer entrypoint. Simpler: do this inline via a child script
    # that reuses train_one_fold.

    summaries = {}
    t_start = time.perf_counter()
    for tier_name, threshold in TIERS.items():
        section(f"Training {tier_name} specialist (ret_t5 >= {threshold:.2f})")
        suffix = f"_tier_{tier_name}"
        cmd = [
            sys.executable, "-X", "utf8", "-u",
            str(SCRIPTS / "ml_v4_momtrans_train.py"),
            "--full-wf",
            "--variant", "tabular_only",
            "--out-suffix", suffix,
            "--epochs", str(args.epochs),
            "--batch-size", str(args.batch_size),
            "--d-model", str(args.d_model),
            "--n-layers", str(args.n_layers),
            "--dropout", f"{args.dropout:.4f}",
            "--lr-max", f"{args.lr_max:.6f}",
            "--patience", str(args.patience),
            "--precision", "fp32",
        ]
        if args.load_ssl_encoder:
            cmd += ["--load-ssl-encoder", args.load_ssl_encoder]
        # Pass tier threshold via env var; the trainer reads it to override y_cls
        env = None
        import os
        env = dict(os.environ)
        env["MX_MOMTRANS_TIER_THRESHOLD"] = f"{threshold:.4f}"
        env["MX_MOMTRANS_TIER_NAME"] = tier_name

        t0 = time.perf_counter()
        rc = subprocess.call(cmd, env=env)
        elapsed = time.perf_counter() - t0
        print(f"  {tier_name} subprocess rc={rc}, elapsed={elapsed:.0f}s")
        if rc != 0:
            summaries[tier_name] = {"error": f"subprocess failed rc={rc}"}
            continue

        # Read predictions, compute Spearman ρ + top-K rank-discrimination
        import pandas as pd
        p = MODELS / f"momtrans_v4{suffix}_predictions.parquet"
        if not p.exists():
            summaries[tier_name] = {"error": f"predictions file missing: {p.name}"}
            continue
        df = pd.read_parquet(p).dropna(subset=["prob_binary", "y_reg"])
        rho = float(df["prob_binary"].corr(df["y_reg"], method="spearman"))
        sorted_df = df.sort_values("prob_binary", ascending=False).reset_index(drop=True)
        top1 = sorted_df.head(int(len(sorted_df) * 0.01))
        summaries[tier_name] = {
            "threshold": threshold,
            "spearman": rho,
            "top1pct_avg_pct": float(top1["y_reg"].mean() * 100) if len(top1) else 0.0,
            "top1pct_win_pct": float((top1["y_reg"] > 0).mean() * 100) if len(top1) else 0.0,
            "elapsed_s": elapsed,
        }
        print(f"  {tier_name} ρ={rho:+.4f}, top-1% avg="
              f"{summaries[tier_name]['top1pct_avg_pct']:+.2f}%")

    section("Tier specialists summary")
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
        s = summaries.get(tier, {})
        if "error" in s:
            print(f"  {tier:<8} ERROR: {s['error']}")
        else:
            print(f"  {tier:<8} ρ={s['spearman']:+.4f}  top-1% avg={s['top1pct_avg_pct']:+.2f}%  "
                  f"win={s['top1pct_win_pct']:.1f}%")

    out = MODELS / "momtrans_v4_tier_specialists_summary.json"
    out.write_text(json.dumps({
        "tiers": TIERS,
        "config": vars(args),
        "summaries": summaries,
        "total_elapsed_s": time.perf_counter() - t_start,
    }, indent=2))
    print(f"\n  Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
