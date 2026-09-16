"""MoMTrans v4 hyperparameter sweep (RESEARCH ONLY).

Optuna-driven Bayesian search over MoMTrans architecture + training
hyperparameters. Each trial runs a full 16-fold WF training; the
objective is the MoMTrans-binary-head $-PNL on the WF set (computed via
the same Aggressive-Kelly logic the verdict script uses).

Designed to be left running for a week; trials can be checkpointed and
resumed via Optuna's RDB storage.

USAGE
  --n-trials 32        : trial budget (default 32 = ~32 GPU-hours)
  --hours 24           : max wall-clock (Optuna-side timeout)
  --storage URL        : Optuna RDB storage (default: sqlite under data/models)
  --study-name NAME    : study name (default: momtrans_v4_sweep)
  --resume             : continue an existing study

OUTPUT
  data/models/momtrans_sweep.db          : Optuna RDB
  data/models/momtrans_sweep_summary.json: best trial + history
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


def evaluate_pnl_from_predictions() -> tuple[float, float]:
    """Run the verdict script to compute MoMTrans binary-head $-PNL +
    worst-tier-regression vs the production baseline. Returns (pnl, regression)."""
    import importlib
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ml_v4_momtrans_verdict", SCRIPTS / "ml_v4_momtrans_verdict.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Direct call: load the predictions parquet, evaluate baseline + binary,
    # return total_pnl and worst regression.
    import duckdb
    import pandas as pd
    DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
    momtrans = pd.read_parquet(MODELS / "momtrans_v4_predictions.parquet")
    momtrans["d0"] = pd.to_datetime(momtrans["d0"])
    prod = pd.read_parquet(DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet")
    prod["d0"] = pd.to_datetime(prod["d0"])
    con = duckdb.connect()
    con.sql(f"""CREATE TABLE i AS SELECT *,
        AVG(magnetization) OVER (ORDER BY d ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)
        AS mag_5d FROM read_parquet('{(DERIVED / "ising_daily.parquet").as_posix()}')""")
    mag_df = con.sql("""SELECT d AS d0, mag_5d,
        CASE WHEN mag_5d < -0.05 THEN 'LO' WHEN mag_5d > 0.05 THEN 'HI' ELSE 'MID' END
        AS mag_label FROM i""").df()
    mag_df["d0"] = pd.to_datetime(mag_df["d0"])
    momtrans = momtrans.merge(mag_df, on="d0", how="left").dropna(subset=["mag_label"])
    prod = prod.merge(mag_df, on="d0", how="left").dropna(subset=["mag_label"])
    baseline = mod.evaluate_baseline(prod)
    result = mod.evaluate_momtrans_binary(momtrans)
    delta = result["TOTAL"]["pnl"] - baseline["TOTAL"]["pnl"]
    # worst regression
    max_regression = 0.0
    for t in ("ELITE", "HIGH", "VETOED", "BROAD"):
        if baseline[t]["pnl"] > 0:
            pct = (result[t]["pnl"] - baseline[t]["pnl"]) / baseline[t]["pnl"]
            if pct < max_regression:
                max_regression = pct
    return delta, max_regression


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-trials", type=int, default=32)
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--storage", type=str, default=None)
    parser.add_argument("--study-name", type=str, default="momtrans_v4_sweep")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--epochs", type=int, default=100,
                        help="Per-fold epoch budget (default 100; lower = faster trials)")
    args = parser.parse_args()

    import optuna

    storage = args.storage or f"sqlite:///{(MODELS / 'momtrans_sweep.db').as_posix()}"
    section(f"MoMTrans v4 sweep — {args.n_trials} trials over {args.hours}h")
    print(f"  storage: {storage}")
    print(f"  study:   {args.study_name}")

    study = optuna.create_study(
        study_name=args.study_name,
        storage=storage,
        direction="maximize",
        load_if_exists=args.resume,
        sampler=optuna.samplers.TPESampler(seed=42),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=4, n_warmup_steps=0),
    )

    def objective(trial: optuna.Trial) -> float:
        d_model = trial.suggest_categorical("d_model", [32, 64, 128])
        n_layers = trial.suggest_int("n_layers", 2, 6)
        dropout = trial.suggest_float("dropout", 0.05, 0.30, step=0.05)
        lr_max = trial.suggest_float("lr_max", 1e-4, 1e-3, log=True)
        batch_size = trial.suggest_categorical("batch_size", [64, 128, 256])

        cmd = [
            sys.executable, "-X", "utf8",
            str(SCRIPTS / "ml_v4_momtrans_train.py"),
            "--full-wf",
            "--epochs", str(args.epochs),
            "--batch-size", str(batch_size),
            "--d-model", str(d_model),
            "--n-layers", str(n_layers),
            "--dropout", f"{dropout:.4f}",
            "--lr-max", f"{lr_max:.6f}",
            "--patience", "10",
            "--precision", "fp32",
        ]
        print(f"\n  Trial {trial.number}: {trial.params}")
        t0 = time.perf_counter()
        rc = subprocess.call(cmd)
        elapsed = time.perf_counter() - t0
        print(f"  Trial {trial.number} subprocess rc={rc}, elapsed={elapsed:.0f}s")
        if rc != 0:
            return -1e9  # Failed trial
        delta, regression = evaluate_pnl_from_predictions()
        # Penalize trials that break the -30% gate so the optimizer learns to
        # avoid them (without hard-pruning the search space)
        if regression < -0.30:
            penalty = (regression + 0.30) * 5_000  # negative
            score = delta + penalty
        else:
            score = delta
        print(f"  Trial {trial.number} score=${score:+,.2f} (delta=${delta:+,.2f}, "
              f"regression={regression*100:+.1f}%)")
        return score

    timeout_s = int(args.hours * 3600) if args.hours > 0 else None
    study.optimize(objective, n_trials=args.n_trials, timeout=timeout_s,
                    catch=(Exception,))

    section("Sweep complete")
    print(f"  best value: ${study.best_value:+,.2f}")
    print(f"  best params: {study.best_params}")
    print(f"  trials: {len(study.trials)}")

    summary = {
        "n_trials": len(study.trials),
        "best_value": study.best_value,
        "best_params": study.best_params,
        "history": [
            {"number": t.number, "value": t.value, "params": t.params,
             "state": t.state.name}
            for t in study.trials
        ],
    }
    out = MODELS / "momtrans_sweep_summary.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n  Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
