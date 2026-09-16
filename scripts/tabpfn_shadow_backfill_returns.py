"""TabPFN shadow backfill — fill `realized_ret_t5` in stale shadow parquets.

Per doc 163 § Gate 3: live-mode shadow runner writes today's d0 with
`realized_ret_t5 = NaN` because forward 5d returns aren't available yet.
This script walks the shadow output directory and fills in the realized
return for any row where:

  1. `realized_ret_t5` is NaN, AND
  2. `d0 + 7 calendar days < today` (5 trading days + weekend buffer),
     so the corresponding `ret_t5` should now be present in
     `aftermath_strat.parquet`.

Idempotent: rows already filled are left untouched. Rows where the
aftermath lookup itself returns NULL (ticker dropped, ca_flag != clean,
etc.) stay NaN — this script only ever overwrites NaN with a non-NaN
value.

USAGE:
  python scripts/tabpfn_shadow_backfill_returns.py
    [--dry-run]           don't write, just report what would change
    [--shadow-dir PATH]   override default data/.../tabpfn_shadow/
    [--days-buffer N]     calendar-day grace before assuming labeled
                          (default 7 = 5 trading days + weekend slack)

Designed to be run after every `daily_data_ingest.ps1` so newly-labeled
aftermath rows propagate into older shadow files within ~1 trading day.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
SHADOW_DIR = DERIVED / "tabpfn_shadow"
AFTERMATH = DERIVED / "aftermath_strat.parquet"


def section(t: str) -> None:
    print(f"\n{'=' * 72}\n{t}\n{'=' * 72}", flush=True)


def fetch_realized_returns(
    keys: pd.DataFrame,
    aftermath_path: Path = AFTERMATH,
) -> pd.DataFrame:
    """Look up realized ret_t5 for (ticker, d0) pairs from aftermath_strat.

    `keys` must have columns ``ticker`` and ``d0`` (timestamp). Returns a
    frame with the same key columns plus ``ret_t5`` (float, may be NaN
    when the row is absent from aftermath_strat or has NULL ret_t5).
    """
    if not aftermath_path.exists():
        raise FileNotFoundError(f"aftermath_strat not found: {aftermath_path}")
    if keys.empty:
        return keys.assign(ret_t5=np.array([], dtype=float))

    # Normalize d0 to date so the join behaves consistently regardless of
    # whether the parquet stored it as DATE or TIMESTAMP. We cast both
    # sides to DATE in the SQL.
    keys_norm = keys.copy()
    keys_norm["d0"] = pd.to_datetime(keys_norm["d0"]).dt.date

    con = duckdb.connect()
    con.register("keys", keys_norm)
    out = con.sql(
        f"""
        SELECT k.ticker, k.d0, a.ret_t5
        FROM keys k
        LEFT JOIN read_parquet('{aftermath_path.as_posix()}') a
          ON a.ticker = k.ticker
         AND CAST(a.d0 AS DATE) = k.d0
        """
    ).df()
    out["d0"] = pd.to_datetime(out["d0"])
    return out


def backfill_file(
    parquet_path: Path,
    today: pd.Timestamp,
    days_buffer: int,
    dry_run: bool,
    aftermath_path: Path = AFTERMATH,
) -> tuple[int, int, int]:
    """Backfill realized_ret_t5 in one shadow parquet.

    Returns (n_rows_total, n_eligible, n_filled).
      - n_rows_total: total rows in the file.
      - n_eligible: rows that are NaN AND old enough (d0 + buffer < today).
      - n_filled: of the eligible, how many got a non-NaN value from
        aftermath_strat (rest stay NaN, e.g. ticker dropped after CA).
    """
    df = pd.read_parquet(parquet_path)
    if "realized_ret_t5" not in df.columns or "d0" not in df.columns:
        return len(df), 0, 0
    n_total = len(df)

    df["d0"] = pd.to_datetime(df["d0"])
    cutoff = today - pd.Timedelta(days=days_buffer)
    eligible_mask = df["realized_ret_t5"].isna() & (df["d0"] < cutoff)
    n_eligible = int(eligible_mask.sum())
    if n_eligible == 0:
        return n_total, 0, 0

    keys = df.loc[eligible_mask, ["ticker", "d0"]].drop_duplicates()
    looked_up = fetch_realized_returns(keys, aftermath_path=aftermath_path)
    # Build (ticker, d0_date) -> ret_t5 map
    looked_up["d0_date"] = pd.to_datetime(looked_up["d0"]).dt.date
    lookup_map = dict(
        zip(zip(looked_up["ticker"], looked_up["d0_date"]),
            looked_up["ret_t5"].values)
    )

    elig_idx = df.index[eligible_mask]
    new_vals = []
    for idx in elig_idx:
        key = (df.at[idx, "ticker"],
               pd.Timestamp(df.at[idx, "d0"]).date())
        new_vals.append(lookup_map.get(key, np.nan))
    new_vals = np.array(new_vals, dtype=float)
    n_filled = int(np.isfinite(new_vals).sum())

    if n_filled == 0:
        return n_total, n_eligible, 0

    if not dry_run:
        df.loc[elig_idx, "realized_ret_t5"] = new_vals
        # Atomic write: tmp file then rename.
        tmp = parquet_path.with_suffix(parquet_path.suffix + ".tmp")
        df.to_parquet(tmp, compression="zstd")
        tmp.replace(parquet_path)

    return n_total, n_eligible, n_filled


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shadow-dir", type=str, default=str(SHADOW_DIR),
                    help="Directory of shadow parquets to backfill.")
    ap.add_argument("--days-buffer", type=int, default=7,
                    help="Calendar-day grace before assuming label exists "
                         "(5 trading days + weekend = 7).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report only; don't modify any parquet.")
    args = ap.parse_args()

    shadow_dir = Path(args.shadow_dir)
    if not shadow_dir.exists():
        print(f"shadow dir does not exist: {shadow_dir}", file=sys.stderr)
        return 1

    today = pd.Timestamp.today().normalize()
    files = sorted(shadow_dir.glob("*.parquet"))

    section(f"backfill scan -- {len(files)} file(s) under {shadow_dir}")
    print(f"  today={today.date()}  buffer={args.days_buffer}d  "
          f"dry_run={args.dry_run}")
    if not files:
        print("  no shadow parquets; nothing to do")
        return 0

    grand_total = grand_elig = grand_filled = 0
    files_touched = 0
    for fp in files:
        try:
            n_total, n_elig, n_filled = backfill_file(
                fp, today=today,
                days_buffer=args.days_buffer,
                dry_run=args.dry_run,
            )
        except Exception as e:
            print(f"  {fp.name}: ERROR -- {e}")
            continue
        grand_total += n_total
        grand_elig += n_elig
        grand_filled += n_filled
        if n_elig:
            files_touched += int(n_filled > 0)
            verb = "would fill" if args.dry_run else "filled"
            print(f"  {fp.name}: {n_filled}/{n_elig} {verb} "
                  f"(of {n_total} total rows)")

    section("summary")
    print(f"  files scanned:      {len(files)}")
    print(f"  files touched:      {files_touched}"
          f"{' (dry-run)' if args.dry_run else ''}")
    print(f"  rows examined:      {grand_total}")
    print(f"  rows eligible:      {grand_elig}")
    print(f"  rows filled:        {grand_filled}")
    if grand_elig and not grand_filled:
        print("  NOTE: all eligible rows had NULL ret_t5 in aftermath_strat "
              "(e.g. ticker dropped, ca_flag != clean). They remain NaN.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
