"""Build v6 microstructure pack (VPIN + OFI + Kyle's λ + Hawkes-proxy + Amihud)
across the aftermath_strat key universe.

Strategy:
  1. Load aftermath keys (~20K rows after standard filter)
  2. Group keys by (year, month) for partition pruning
  3. For each (year, month) chunk:
       For each (ticker, d0) key:
         Read trades_v1_parquet/year=YYYY/month=MM/day=DD/ticker=TKR/data_0.parquet
         Run compute_v6_microstructure_pack
         Append result row to in-memory list
  4. Concat + write to data/polygon_warehouse/derived/microstructure_v6_pack.parquet

This is NOT joined SQL — it's per-file Python. The trade-off: lower peak
memory (one ticker-day in RAM at a time) vs higher per-file overhead
(thousands of small parquet reads).

Per the v6 Phase 0 hygiene rule: this script BUILDS features but does
NOT validate them or wire them into a model. Validation happens in
ml_v6_phase_0_validation.py after a v3+v6_pack model is trained on a
HELD-OUT fold.

USAGE:
    python scripts/build_v6_microstructure_pack.py
    python scripts/build_v6_microstructure_pack.py --month 2026-04   # single month
    python scripts/build_v6_microstructure_pack.py --limit 100       # smoke test
"""
from __future__ import annotations
import argparse
import sys
import time
import traceback
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from v6_microstructure_pack import compute_v6_microstructure_pack  # noqa: E402

DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
TRADES_PARQUET = REPO / "data" / "polygon_warehouse" / "trades_v1_parquet"
OUT_PATH = DERIVED / "microstructure_v6_pack.parquet"

TRADE_COLS = ["price", "size", "conditions", "exchange", "tape", "trf_id", "ts_et"]


def section(t: str) -> None:
    # Stick to ASCII — Windows cp1252 console chokes on unicode arrows.
    print(f"\n{'='*72}\n{t}\n{'='*72}", flush=True)


_ARROW = "->"


def load_keys() -> pd.DataFrame:
    """Load aftermath keys with the same filter the v2 builder uses."""
    p = DERIVED / "aftermath_strat.parquet"
    df = pq.read_table(p, columns=["ticker", "d0", "ca_flag", "open",
                                   "volume", "ret_t5"]).to_pandas()
    df = df[(df["ca_flag"] == "clean")
            & (df["ret_t5"].notna())
            & (df["open"].between(0.5, 50))
            & (df["volume"] > 100_000)].copy()
    df["d0"] = pd.to_datetime(df["d0"])
    df["year"] = df["d0"].dt.year
    df["month"] = df["d0"].dt.month
    df["day"] = df["d0"].dt.day
    return df[["ticker", "d0", "year", "month", "day"]].drop_duplicates().reset_index(drop=True)


def trade_path_for(ticker: str, year: int, month: int, day: int) -> Path:
    return (TRADES_PARQUET
            / f"year={year}"
            / f"month={month:02d}"
            / f"day={day:02d}"
            / f"ticker={ticker}"
            / "data_0.parquet")


def find_trade_file_with_offset(ticker: str, target_date: pd.Timestamp,
                                 day_offset: int = 0,
                                 search_days: int = 7) -> Path | None:
    """Resolve the actual trades file for (ticker, target_date + day_offset).

    Handles weekends/holidays/missing-data: walks back up to `search_days`
    calendar days from the offset target until it finds an existing file.

    day_offset = 0 -> use target_date (default, original v6 behavior)
    day_offset = -1 -> use prior trading day (Path A: lookahead-safe d-1 features)
    day_offset = -2 -> two trading days back, etc.
    """
    # Apply offset; for negative offsets, we need to walk back |day_offset|
    # business days then continue searching for the next available file.
    if day_offset == 0:
        candidate = target_date
        fp = trade_path_for(ticker, candidate.year, candidate.month, candidate.day)
        return fp if fp.exists() else None
    # For day_offset < 0: walk back business days
    candidate = target_date
    bdays_back_needed = abs(day_offset)
    while bdays_back_needed > 0:
        candidate -= pd.Timedelta(days=1)
        # Skip weekends
        while candidate.dayofweek >= 5:
            candidate -= pd.Timedelta(days=1)
        bdays_back_needed -= 1
    # Now search up to `search_days` calendar days back from `candidate`
    # for an actually-existing file (handles holidays + missing partitions).
    for delta in range(search_days):
        c = candidate - pd.Timedelta(days=delta)
        fp = trade_path_for(ticker, c.year, c.month, c.day)
        if fp.exists():
            return fp
    return None


def run_one_key(row: pd.Series, day_offset: int = 0) -> dict | None:
    """Compute v6 pack for one (ticker, d0) with optional day_offset.

    day_offset = 0 -> features computed on d0 (state descriptor; lookahead vs open)
    day_offset = -1 -> features computed on d-1 (prior trading day, lookahead-safe)
    """
    if day_offset == 0:
        fp = trade_path_for(row["ticker"], row["year"], row["month"], row["day"])
        if not fp.exists():
            return None
    else:
        fp = find_trade_file_with_offset(row["ticker"], row["d0"], day_offset)
        if fp is None:
            return None
    try:
        df = pq.ParquetFile(str(fp)).read(columns=TRADE_COLS).to_pandas()
    except Exception as e:
        # corrupt parquet, mismatched schema, etc — skip
        print(f"  read error {row['ticker']} {row['d0'].date()}: {e}", flush=True)
        return None
    if len(df) == 0:
        return None
    pack = compute_v6_microstructure_pack(df)
    pack["ticker"] = row["ticker"]
    pack["d0"] = row["d0"]  # the EVENT date stays as d0 for join key consistency
    pack["n_trades_d0"] = len(df)
    pack["source_day_offset"] = day_offset
    return pack


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", type=str, default=None,
                    help="restrict to YYYY-MM, e.g. 2026-04 (default: all)")
    ap.add_argument("--limit", type=int, default=None,
                    help="limit to first N keys (smoke test)")
    ap.add_argument("--out", type=str, default=str(OUT_PATH),
                    help="output parquet path")
    ap.add_argument("--day-offset", type=int, default=0,
                    help="0 = features on d0 (default, lookahead vs open); "
                         "-1 = features on d-1 (Path A, lookahead-safe)")
    args = ap.parse_args()
    out = Path(args.out)
    if args.day_offset != 0:
        # Auto-suffix the output path if user didn't override
        if args.out == str(OUT_PATH):
            out = OUT_PATH.parent / f"microstructure_v6_pack_dminus{abs(args.day_offset)}.parquet"
        print(f"  day_offset = {args.day_offset}  -> writing to {out.name}")

    section("STEP 1 — load aftermath keys")
    keys = load_keys()
    print(f"  total keys: {len(keys):,}")
    print(f"  d0 range: {keys['d0'].min().date()} -> {keys['d0'].max().date()}")
    print(f"  unique tickers: {keys['ticker'].nunique():,}")

    if args.month:
        y, m = args.month.split("-")
        keys = keys[(keys["year"] == int(y)) & (keys["month"] == int(m))].copy()
        print(f"  -> restricted to {args.month}: {len(keys):,} keys")
    if args.limit:
        keys = keys.head(args.limit).copy()
        print(f"  -> limited to first {args.limit}: {len(keys):,} keys")

    section("STEP 2 — compute pack per key (one trades file per (ticker, d0))")
    rows: list[dict] = []
    skipped = 0
    t_start = time.time()
    for i, row in keys.iterrows():
        result = run_one_key(row, day_offset=args.day_offset)
        if result is None:
            skipped += 1
        else:
            rows.append(result)
        if (len(rows) + skipped) % 100 == 0 and (len(rows) + skipped) > 0:
            elapsed = time.time() - t_start
            done = len(rows) + skipped
            rate = done / elapsed
            eta = (len(keys) - done) / rate if rate > 0 else float("nan")
            print(f"  {done:,}/{len(keys):,}  ok={len(rows):,}  skip={skipped:,}  "
                  f"rate={rate:.1f}/s  ETA={eta/60:.1f}min", flush=True)

    section("STEP 3 — concat + persist")
    if not rows:
        print("  no rows produced!")
        return 1
    final = pd.DataFrame(rows)
    cols = ["ticker", "d0", "n_trades_d0",
            "vpin_d0", "ofi_first30_d0", "kyle_lambda_d0",
            "hawkes_fano_d0", "amihud_illiq_d0", "iso_sweep_count_d0"]
    final = final[cols]
    print(f"  rows: {len(final):,}")
    print(f"  unique tickers: {final['ticker'].nunique():,}")
    print(f"  d0 range: {final['d0'].min().date()} -> {final['d0'].max().date()}")
    print()
    print("  feature stats (non-null only):")
    print(final[["vpin_d0", "ofi_first30_d0", "kyle_lambda_d0",
                 "hawkes_fano_d0", "amihud_illiq_d0",
                 "iso_sweep_count_d0"]].describe().T[
                     ["count", "mean", "std", "min", "50%", "max"]])

    out.parent.mkdir(parents=True, exist_ok=True)
    final.to_parquet(out, compression="zstd")
    print(f"\n  wrote {out} ({out.stat().st_size/1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
