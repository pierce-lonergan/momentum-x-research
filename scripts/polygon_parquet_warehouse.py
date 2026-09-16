"""Convert Polygon flat files (CSV.gz) → Hive-partitioned Parquet warehouse.

Per docs/research/polygon_compass_playbook.md §A.1: CSV.gz is fine for
ingest, miserable for repeated queries. Convert to Hive-partitioned
Parquet by (year, ticker) for minute_aggs, with ZSTD compression and
~256 MB row groups (DuckDB sweet spot 100 MB - 10 GB; Polars OOMs on
monolithic 140 GB files but handles partitions cleanly).

Resulting layout:
    data/polygon_warehouse/
        day_aggs/year=2024/data.parquet
        minute_aggs/year=2024/month=03/data.parquet
        minute_aggs/year=2024/month=04/data.parquet
        ...

Query pattern (see scripts/polygon_query_demo.py):
    duckdb.sql('''
        SELECT ticker, ts_et, open, high, low, close, volume
        FROM read_parquet('data/polygon_warehouse/minute_aggs/**/*.parquet',
                          hive_partitioning=true)
        WHERE ticker = 'AAPL' AND year = 2024
    ''')

Critical timestamp handling per Compass §A.1:
  - flat-file `window_start` is **nanoseconds in UTC**
  - We add `ts_utc` (datetime[ns, UTC]) and `ts_et` (datetime[ns, ET])
  - Anything that uses naive UTC for session detection is 4-5 hours off

USAGE:
    # Convert one dataset
    python scripts/polygon_parquet_warehouse.py --dataset day_aggs_v1
    python scripts/polygon_parquet_warehouse.py --dataset minute_aggs_v1
    # Convert one specific year
    python scripts/polygon_parquet_warehouse.py --dataset minute_aggs_v1 --year 2024
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

REPO = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO / "data" / "polygon_flatfiles"
DST_ROOT = REPO / "data" / "polygon_warehouse"
DST_ROOT.mkdir(parents=True, exist_ok=True)

ET = ZoneInfo("America/New_York")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("warehouse")

# Schema knowledge per Compass §A.1
AGG_COLS = ["ticker", "volume", "open", "close", "high", "low",
             "window_start", "transactions"]
TRADE_COLS = ["ticker", "conditions", "correction", "exchange", "id",
               "participant_timestamp", "price", "sequence_number",
               "sip_timestamp", "size", "tape", "trf_id", "trf_timestamp"]
QUOTE_COLS = ["ticker", "ask_exchange", "ask_price", "ask_size",
               "bid_exchange", "bid_price", "bid_size", "conditions",
               "indicators", "participant_timestamp", "sequence_number",
               "sip_timestamp", "tape"]


def normalize_aggs(df: pl.DataFrame) -> pl.DataFrame:
    """Add ts_utc + ts_et + year + month columns to an aggs frame."""
    df = df.with_columns([
        pl.from_epoch("window_start", time_unit="ns").alias("ts_utc"),
    ])
    df = df.with_columns([
        pl.col("ts_utc").dt.convert_time_zone("America/New_York").alias("ts_et"),
    ])
    df = df.with_columns([
        pl.col("ts_et").dt.year().alias("year"),
        pl.col("ts_et").dt.month().alias("month"),
        pl.col("ts_et").dt.day().alias("day"),
    ])
    return df


def convert_aggs_dataset(dataset: str, year_filter: int | None = None) -> int:
    """Convert all CSV.gz files for an aggs dataset → partitioned Parquet."""
    src_root = SRC_ROOT / dataset
    if not src_root.exists():
        log.error("Source dir does not exist: %s", src_root)
        return 1
    out_kind = "day_aggs" if "day" in dataset else "minute_aggs"
    files = sorted(src_root.rglob("*.csv.gz"))
    if year_filter is not None:
        files = [f for f in files if f.parent.name == str(year_filter)]
    if not files:
        log.warning("No source files found.")
        return 0
    log.info("Converting %d files from %s -> %s/", len(files), dataset, out_kind)

    # Group files by (year, month) for minute_aggs; by year for day_aggs
    if out_kind == "minute_aggs":
        # process per-month
        by_month: dict[tuple[int, int], list[Path]] = {}
        for f in files:
            year = int(f.parent.name)
            month = int(f.stem.split("-")[1])
            by_month.setdefault((year, month), []).append(f)
        for (yr, mo), fs in sorted(by_month.items()):
            out_dir = DST_ROOT / "minute_aggs" / f"year={yr}" / f"month={mo:02d}"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / "data.parquet"
            if out_file.exists():
                log.info("  %d-%02d: SKIP (already converted)", yr, mo)
                continue
            log.info("  %d-%02d: reading %d files ...", yr, mo, len(fs))
            frames = []
            for f in fs:
                try:
                    df = pl.read_csv(f, has_header=True, schema_overrides={
                        "ticker": pl.Utf8,
                        "volume": pl.Float64,  # some ETFs report fractional
                        "open": pl.Float64, "close": pl.Float64,
                        "high": pl.Float64, "low": pl.Float64,
                        "window_start": pl.Int64,
                        "transactions": pl.Int64,
                    }, infer_schema_length=10000)
                    frames.append(df)
                except Exception as e:
                    log.warning("    %s: %s", f.name, e)
            if not frames:
                continue
            big = pl.concat(frames, how="vertical_relaxed")
            big = normalize_aggs(big)
            big.write_parquet(
                out_file,
                compression="zstd",
                row_group_size=200_000,
                statistics=True,
            )
            log.info("    wrote %s (%d rows, %.1f MB)", out_file,
                     len(big), out_file.stat().st_size / 1e6)
    else:  # day_aggs
        by_year: dict[int, list[Path]] = {}
        for f in files:
            year = int(f.parent.name)
            by_year.setdefault(year, []).append(f)
        for yr, fs in sorted(by_year.items()):
            out_dir = DST_ROOT / "day_aggs" / f"year={yr}"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / "data.parquet"
            if out_file.exists():
                log.info("  %d: SKIP (already converted)", yr)
                continue
            log.info("  %d: reading %d files ...", yr, len(fs))
            frames = []
            for f in fs:
                try:
                    df = pl.read_csv(f, has_header=True, schema_overrides={
                        "ticker": pl.Utf8,
                        "volume": pl.Float64,  # some ETFs report fractional
                        "open": pl.Float64, "close": pl.Float64,
                        "high": pl.Float64, "low": pl.Float64,
                        "window_start": pl.Int64,
                        "transactions": pl.Int64,
                    }, infer_schema_length=10000)
                    frames.append(df)
                except Exception as e:
                    log.warning("    %s: %s", f.name, e)
            if not frames: continue
            big = pl.concat(frames, how="vertical_relaxed")
            big = normalize_aggs(big)
            big.write_parquet(
                out_file, compression="zstd",
                row_group_size=200_000, statistics=True,
            )
            log.info("    wrote %s (%d rows, %.1f MB)", out_file,
                     len(big), out_file.stat().st_size / 1e6)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True,
                         choices=["day_aggs_v1", "minute_aggs_v1"])
    parser.add_argument("--year", type=int, default=None,
                         help="Limit conversion to one year")
    args = parser.parse_args()
    return convert_aggs_dataset(args.dataset, year_filter=args.year)


if __name__ == "__main__":
    sys.exit(main())
