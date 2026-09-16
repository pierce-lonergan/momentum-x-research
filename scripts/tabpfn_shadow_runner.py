"""TabPFN shadow-mode runner — logs predictions WITHOUT affecting production trading.

Per doc 144 § Exp #4 pre-commit: skip Phase 1 paper-trade observation,
build TabPFN shadow-mode wrapper for next launcher commit.

DESIGN:
  Shadow mode runs ALONGSIDE production (not inside MetaScorer). After
  the daily picks fire (or before, doesn't matter — this is read-only
  forensics), this script:
    1. Reads the day's candidate set (from lottery_picks_YYYY-MM-DD.json
       or by re-running the same candidate-discovery query)
    2. Loads v3 base features (same as MetaScorer would)
    3. Fits TabPFN on the most-recent 120-day training window
       (matches doc 138 / doc 143 WF setup; train window ends "today")
    4. Predicts on today's candidates with TabPFN
    5. Writes data/.../tabpfn_shadow/<YYYY-MM-DD>.parquet with columns:
         ticker, d0, v3_proba, tabpfn_pred, tabpfn_quintile_per_day,
         realized_ret_t5_5d_later (filled in retroactively)

CRITICAL: this script does NOT touch MetaScorer or trading. It's
purely observational. The only failure mode is "no shadow data
written" which doesn't impact production.

Two-week shadow data → analyze whether defensive overlay (skip v3
trades where TabPFN below top-quintile per day) reproduces the
+5.87pp lift from doc 144 Exp #3 in live data. If yes: enable
LOTTERY_TABPFN_DEFENSIVE_OVERLAY=1 in a follow-up launcher commit.

USAGE:
  TABPFN_TOKEN=<token> TABPFN_NO_BROWSER=1 \\
    python scripts/tabpfn_shadow_runner.py

  # Optionally specify a date (default: today):
  python scripts/tabpfn_shadow_runner.py --date 2026-05-08

  # Backfill shadow predictions retroactively from existing pick logs:
  python scripts/tabpfn_shadow_runner.py --backfill-from-logs
"""
from __future__ import annotations
import argparse
import gc
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
SHADOW_DIR = DERIVED / "tabpfn_shadow"
LOGS_DIR = REPO / "logs"

SHADOW_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


# D293a (2026-05-12, doc 155 verdict): REVERTED to single-seed.
# Doc 155's D293.6 calibrated verification: Gate B PASS (rho_B_agg=0.895),
# Gate C PASS (Fisher-z partial correlation one-sided p=0.31), Gate D FAIL
# (only 2/7 dates have bootstrap CI lower bound >= -0.05 — not because the
# ensemble is bad, but because at N=24-55 picks per day the bootstrap CI
# is symmetric ±0.13 around an essentially-zero point delta, which dips
# below -0.05 by symmetric chance). Per Rule 7 (no post-hoc threshold
# movement), REVERT enforced. Filed D293.8 to re-test the ensemble once
# shadow data accumulates ~200+ picks per "test pool" (estimated ~6-8
# weeks of shadow accumulation).
#
# What's KEPT from D293: explicit n_estimators=2 (compass §Topic 7
# production-safety fix; independent of ensemble research). tabpfn v7.1.1's
# default n_estimators causes 75min/seed on RTX 5070 (observed in doc 152
# E1 first-run before kill); n_estimators=2 keeps inference at 1-2 sec/seed.
N_ESTIMATORS = 2


def fit_predict_tabpfn(X_train: np.ndarray, y_train: np.ndarray,
                       X_test: np.ndarray, max_train: int = 2500,
                       seed: int = 42,
                       n_estimators: int = N_ESTIMATORS) -> np.ndarray:
    """Fit TabPFN on (X_train, y_train), predict on X_test. Returns pred array.

    D293a: single-seed only (ensemble reverted per doc 155 verdict).
    Explicit n_estimators=2 retained as production-safety fix per compass
    research §Topic 7.

    Subsamples to max_train rows if X_train is larger (TabPFN's memory
    sweet spot is ~10K context but 2500 is safe on RTX 5070 against the
    OOM patterns we hit in doc 143).
    """
    import torch
    from tabpfn import TabPFNRegressor

    if len(X_train) > max_train:
        rng = np.random.default_rng(seed)
        idx = rng.choice(len(X_train), max_train, replace=False)
        X_train = X_train[idx]
        y_train = y_train[idx]

    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        gc.collect()

    model = TabPFNRegressor(device="cuda", random_state=seed,
                             n_estimators=n_estimators)
    model.fit(X_train, y_train)
    pred = model.predict(X_test)

    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        gc.collect()
    return pred


def assign_per_day_quintile(preds: np.ndarray, dates: pd.Series) -> np.ndarray:
    """For each pick, return its TabPFN per-day quintile rank (0..4).
    Quintile 4 = top quintile; quintile 0 = bottom.

    With pct=True, ranks are in (0, 1]. To map to 5 buckets where the
    lowest rank goes to bucket 0 and the highest to bucket 4, use
    `floor(rank * 5 - epsilon)` and clip into [0, 4]. For 5 picks with
    ranks {0.2, 0.4, 0.6, 0.8, 1.0} this gives {0, 1, 2, 3, 4}.
    """
    df = pd.DataFrame({"pred": preds, "d0": pd.to_datetime(dates)})
    out = pd.Series(np.zeros(len(df), dtype=int), index=df.index)
    for _, g in df.groupby("d0"):
        if len(g) < 5:
            out.loc[g.index] = 4  # too few to bucket; treat as top
            continue
        ranks = g["pred"].rank(pct=True, method="first")
        # Map (0, 1] -> [0, 4]: subtract a tiny epsilon then * 5, floor, clip
        quintile = np.floor((ranks - 1e-9) * 5).clip(0, 4).astype(int)
        out.loc[g.index] = quintile.values
    return out.values


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=str, default=None,
                    help="Date to score (YYYY-MM-DD). Default: today.")
    ap.add_argument("--backfill-from-logs", action="store_true",
                    help="Backfill shadow preds for ALL dates with picks in logs/.")
    ap.add_argument("--train-days", type=int, default=120,
                    help="Training window length (matches doc 143 setup).")
    # 2026-05-12 doc 163: live mode supports scoring today's d0 (which has
    # NULL ret_t5 at scoring time). Realized returns are filled in 5+ days
    # later by tabpfn_shadow_backfill_returns.py.
    ap.add_argument("--mode", choices=["historical", "live"], default="historical",
                    help="historical: score historical labeled date (existing). "
                         "live: score today's d0 (unlabeled at scoring time); "
                         "realized returns filled in by backfill script later.")
    args = ap.parse_args()

    section(f"STEP 1 -- load v3 base data (mode={args.mode})")
    from ml_continuer_v2_ensemble import load_data, engineer_features
    # Doc 163 Gate 1: include unlabeled rows in live mode so target_date
    # (today) actually appears in the loaded panel. y will be NaN for
    # unlabeled rows; the train mask filters them out before fit.
    df = load_data(include_paths=True, include_unlabeled=(args.mode == "live"))
    df["d0"] = pd.to_datetime(df["d0"])
    print(f"  loaded {len(df):,} rows, d0 range "
          f"{df['d0'].min().date()} -> {df['d0'].max().date()}")
    X = engineer_features(df).replace([np.inf, -np.inf], 0).fillna(0).reset_index(drop=True)
    # In live mode, y can be NaN (unlabeled rows). Keep raw values; train
    # mask below excludes NaN before TabPFN.fit().
    y = df["ret_t5"].clip(-0.50, 1.00).reset_index(drop=True)
    d0 = pd.to_datetime(df["d0"]).reset_index(drop=True)
    df_keyed = df.reset_index()[["index", "ticker", "d0"]].rename(columns={"index": "_idx"})
    print(f"  X: {X.shape}  y: {y.shape}  unlabeled rows: {y.isna().sum()}")

    # Determine target dates.
    # 2026-05-13 (doc 163 followup): in --mode live the default behavior
    # is now "score the most-recent d0 actually present in the dataset"
    # rather than "score today". Polygon's day_aggs flat file for $TODAY
    # isn't published until ~04:00 ET the next morning, so when this
    # script is invoked at 17:30 ET (post-market-close ingest), the
    # catalog's max(d0) is yesterday and `--date $TODAY` silently
    # produces 0 picks. Using max(d0) means we always score the latest
    # date the catalog actually contains.
    if args.backfill_from_logs:
        # All dates in df where we have realized return data
        target_dates = sorted(df["d0"].dt.date.unique())
        print(f"  backfill mode: {len(target_dates)} unique dates in dataset")
    elif args.date:
        target_dates = [pd.to_datetime(args.date).date()]
    elif args.mode == "live":
        # Doc 163 followup: pick the most-recent d0 in the catalog.
        target_dates = [df["d0"].max().date()]
        print(f"  live mode: defaulting target_date to max(d0) in catalog "
              f"= {target_dates[0]}")
    else:
        target_dates = [pd.Timestamp.today().date()]

    section(f"STEP 2 -- score TabPFN for {len(target_dates)} target date(s)")
    written = 0
    skipped_no_data = 0  # informational: target date has 0 rows in catalog
    skipped_other = 0    # actionable: file exists, too-few train rows, error
    for target_date in target_dates:
        target_ts = pd.Timestamp(target_date)
        # Train window: [target - train_days, target). Doc 163: in live
        # mode, also exclude unlabeled rows from the train window (y NaN
        # would crash TabPFN.fit). Test mask in live mode INCLUDES the
        # unlabeled target_date rows -- those are the predict targets.
        train_mask = (
            (d0 >= target_ts - pd.Timedelta(days=args.train_days))
            & (d0 < target_ts)
            & (y.notna())  # exclude any unlabeled rows from training
        )
        test_mask = d0 == target_ts
        n_test = int(test_mask.sum())
        n_train = int(train_mask.sum())
        if n_test == 0:
            print(f"  {target_date}: 0 rows in catalog for this d0 — skip "
                  f"(catalog may not yet include today's data)")
            skipped_no_data += 1
            continue
        if n_train < 100:
            print(f"  {target_date}: too few train rows ({n_train}); skip")
            skipped_other += 1
            continue

        out_path = SHADOW_DIR / f"{target_date}.parquet"
        if out_path.exists() and not args.backfill_from_logs:
            print(f"  {target_date}: already exists at {out_path.name}, skip")
            skipped_no_data += 1  # idempotent re-run, not a real failure
            continue

        # Doc 163: in live mode, the test rows have NaN y. That's fine -
        # we don't pass y_test to TabPFN, only X_test for prediction.
        t0 = time.time()
        try:
            tabpfn_pred = fit_predict_tabpfn(
                X.loc[train_mask].values, y.loc[train_mask].values,
                X.loc[test_mask].values,
            )
        except Exception as e:
            print(f"  {target_date}: TabPFN error: {e}")
            skipped_other += 1
            continue

        # D293a (doc 155 REVERT): single-seed schema restored. Ensemble
        # variance columns (tabpfn_pred_std, n_seeds_used) removed since
        # single-seed has no ensemble variance to report.
        # Doc 163: added scoring_mode column for forensics; in live mode
        # realized_ret_t5 is NaN at write time (filled in by backfill).
        test_idx = np.where(test_mask)[0]
        rec = pd.DataFrame({
            "ticker": df.loc[test_idx, "ticker"].values,
            "d0": d0.loc[test_idx].values,
            "tabpfn_pred": tabpfn_pred,
            "n_train_rows_used": min(n_train, 2500),
        })
        rec["tabpfn_quintile_per_day"] = assign_per_day_quintile(
            rec["tabpfn_pred"].values, rec["d0"])
        # Realized return: in historical mode this is the labeled value;
        # in live mode it's NaN (filled in by tabpfn_shadow_backfill_returns
        # ~5 trading days later when ret_t5 becomes available).
        rec["realized_ret_t5"] = y.loc[test_idx].values
        rec["scoring_mode"] = args.mode  # forensics: how was this row scored?
        rec["shadow_run_at_utc"] = pd.Timestamp.utcnow()

        rec.to_parquet(out_path, compression="zstd")
        written += 1
        print(f"  {target_date}: wrote {len(rec)} shadow preds "
              f"({time.time()-t0:.1f}s)  -> {out_path.name}")

    section("STEP 3 -- summary")
    print(f"  wrote:           {written} files")
    print(f"  skipped (no_data): {skipped_no_data}  "
          f"(target d0 not yet in catalog OR file already exists)")
    print(f"  skipped (other):   {skipped_other}  "
          f"(too few train rows OR TabPFN error)")
    print(f"  output dir: {SHADOW_DIR}")
    # Doc 163 followup: return 0 when the only "failure" was no-data
    # (cron-friendly: the launcher should NOT mark the step as FAILED
    # when the shadow runner correctly skipped because today's d0 isn't
    # yet in the catalog). Return 1 only on actionable errors.
    if skipped_other > 0 and written == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
