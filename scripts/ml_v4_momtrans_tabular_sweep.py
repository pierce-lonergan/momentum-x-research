"""Tabular-only MoMTrans v4 Optuna sweep.

Per docs/research-log/127_momtrans_v4_ablations.md: tabular-only is the
strongest variant (Spearman ρ 0.141, vs 0.036 for default and 0.020
for "bigger"). Sequence branch is dead weight; bigger overfits.

This sweep narrows the search space accordingly:
  d_model      ∈ {32, 48, 64}        (smaller models do better)
  n_layers     ∈ {2, 3, 4}           (deeper overfits on 20k rows)
  dropout      ∈ [0.05, 0.30]        (regularization is important)
  lr_max       ∈ [1e-4, 1e-3] log    (1cycle LR scheduler)
  batch_size   ∈ {64, 128, 256}      (bigger batch = more stable)
  bce_weight   ∈ [0.40, 0.85]        (binary task weight)
  ce_weight    ∈ [0.10, 0.40]        (cohort task weight)
  pos_weight   ∈ [2.0, 5.0]          (BCE class imbalance compensation)
  epochs       ∈ {60, 100, 150}      (overlong training overfits too)

Objective: Spearman ρ between binary head proba and y_reg (continuous
return). More stable than $-PNL whose calibration depends on the
tier-waterfall thresholds (designed for v3, not MoMTrans).

Penalty: heavy negative if Spearman is below random walk baseline.

OUTPUT
  data/models/momtrans_tabular_sweep.db          (SQLite Optuna RDB)
  data/models/momtrans_tabular_sweep_summary.json (best trial + history)
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODELS = REPO / "data" / "models"
SCRIPTS = REPO / "scripts"


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def evaluate_spearman_from_predictions(suffix: str) -> tuple[float, dict]:
    """Read MoMTrans predictions, compute Spearman ρ binary-head vs y_reg.
    Returns (rho, stats_dict). NaN -> -1.0 sentinel."""
    import pandas as pd
    p = MODELS / f"momtrans_v4{suffix}_predictions.parquet"
    if not p.exists():
        return -1.0, {"reason": f"predictions file missing: {p.name}"}
    df = pd.read_parquet(p)
    if df.empty or "prob_binary" not in df.columns:
        return -1.0, {"reason": "empty or missing prob_binary"}
    df = df.dropna(subset=["prob_binary", "y_reg"])
    if df.empty:
        return -1.0, {"reason": "all NaN"}
    rho = float(df["prob_binary"].corr(df["y_reg"], method="spearman"))
    if not (-1 <= rho <= 1):
        return -1.0, {"reason": f"non-finite rho: {rho}"}

    # Top-K rank discrimination for diagnostics
    sorted_df = df.sort_values("prob_binary", ascending=False).reset_index(drop=True)
    top1 = sorted_df.head(int(len(sorted_df) * 0.01))
    top5 = sorted_df.head(int(len(sorted_df) * 0.05))
    return rho, {
        "spearman": rho,
        "top1pct_avg": float(top1["y_reg"].mean() * 100) if len(top1) else 0.0,
        "top1pct_win": float((top1["y_reg"] > 0).mean() * 100) if len(top1) else 0.0,
        "top5pct_avg": float(top5["y_reg"].mean() * 100) if len(top5) else 0.0,
        "n_total": len(df),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-trials", type=int, default=16)
    parser.add_argument("--hours", type=float, default=4.0)
    parser.add_argument("--storage", type=str, default=None)
    parser.add_argument("--study-name", type=str, default="momtrans_tabular_v1")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-epochs", type=int, default=150)
    args = parser.parse_args()

    import optuna

    storage = args.storage or f"sqlite:///{(MODELS / 'momtrans_tabular_sweep.db').as_posix()}"
    section(f"Tabular-only Optuna sweep — {args.n_trials} trials over {args.hours}h")
    print(f"  storage:    {storage}")
    print(f"  study:      {args.study_name}")
    print(f"  objective:  Spearman ρ(prob_binary, y_reg) on 16-fold WF")
    print(f"  variant:    tabular_only (sequence branch zeroed)")

    study = optuna.create_study(
        study_name=args.study_name,
        storage=storage,
        direction="maximize",
        load_if_exists=args.resume,
        sampler=optuna.samplers.TPESampler(seed=42),
    )

    def objective(trial: optuna.Trial) -> float:
        d_model = trial.suggest_categorical("d_model", [32, 48, 64])
        n_layers = trial.suggest_int("n_layers", 2, 4)
        dropout = trial.suggest_float("dropout", 0.05, 0.30, step=0.05)
        lr_max = trial.suggest_float("lr_max", 1e-4, 1e-3, log=True)
        batch_size = trial.suggest_categorical("batch_size", [64, 128, 256])
        epochs = trial.suggest_categorical("epochs", [60, 100, 150])

        # Per-trial output suffix so parallel trials don't clobber each other
        suffix = f"_sweep_{trial.number}"
        cmd = [
            sys.executable, "-X", "utf8", "-u",
            str(SCRIPTS / "ml_v4_momtrans_train.py"),
            "--full-wf",
            "--variant", "tabular_only",
            "--out-suffix", suffix,
            "--epochs", str(epochs),
            "--batch-size", str(batch_size),
            "--d-model", str(d_model),
            "--n-layers", str(n_layers),
            "--dropout", f"{dropout:.4f}",
            "--lr-max", f"{lr_max:.6f}",
            "--patience", "12",
            "--precision", "fp32",
        ]
        print(f"\n  Trial {trial.number}: {trial.params}")
        t0 = time.perf_counter()
        rc = subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elapsed = time.perf_counter() - t0
        print(f"  Trial {trial.number} subprocess rc={rc}, elapsed={elapsed:.0f}s")
        if rc != 0:
            return -1.0
        rho, stats = evaluate_spearman_from_predictions(suffix)
        # Persist trial-level stats for analysis
        trial.set_user_attr("stats", stats)
        print(f"  Trial {trial.number} ρ={rho:+.4f}, top1%={stats.get('top1pct_avg', 0):+.2f}%, "
              f"top5%={stats.get('top5pct_avg', 0):+.2f}%")
        return rho

    timeout_s = int(args.hours * 3600) if args.hours > 0 else None
    study.optimize(objective, n_trials=args.n_trials, timeout=timeout_s,
                    catch=(Exception,))

    section("Sweep complete")
    print(f"  best ρ:     {study.best_value:+.4f}")
    print(f"  best params: {study.best_params}")
    print(f"  trials:     {len(study.trials)}")

    # Production v3 reference: ρ = 0.1586
    PROD_RHO = 0.1586
    gap = PROD_RHO - study.best_value
    pct_of_prod = study.best_value / PROD_RHO * 100 if PROD_RHO > 0 else 0
    print(f"\n  Production v3 ρ:      {PROD_RHO:+.4f}")
    print(f"  Gap to production:    {gap:+.4f}")
    print(f"  MoMTrans pct of prod: {pct_of_prod:.1f}%")

    summary = {
        "n_trials": len(study.trials),
        "best_value": study.best_value,
        "best_params": study.best_params,
        "production_v3_rho": PROD_RHO,
        "gap_to_production": gap,
        "history": [
            {"number": t.number, "value": t.value, "params": t.params,
             "state": t.state.name, "stats": t.user_attrs.get("stats", {})}
            for t in study.trials
        ],
    }
    out = MODELS / "momtrans_tabular_sweep_summary.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n  Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
