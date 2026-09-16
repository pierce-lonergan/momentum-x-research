"""Polygon trades_v1 flat-file downloader (Phase 3 trade tape pull).

Per Compass artifact 2 §A.1: Polygon trades_v1 is the highest-impact unlock
on the $199/mo Stocks Advanced subscription. Tick-level data with full
condition codes enables:
  - Sweep / ISO-burst detection (cited as high-conviction continuation
    signal for microcap gap-ups; arbitrageurs send ISOs to chase price)
  - Dark-pool / FINRA TRF print isolation (filter exchange == 4)
  - True VWAP that excludes off-tape prints (condition codes 6, 7, 13)
  - Print-size distribution: large prints vs odd lots = institutional vs retail
  - Sub-second timing precision (nanosecond timestamps in UTC)

Volume estimate (per Compass artifact §A.1):
  - ~700 MB-1 GB compressed CSV per trading day, full universe
  - ~300-500 GB total for 600 trading days
  - At sustained 50 MB/s download, ~1.5-3 hours full historical pull
  - At 16 parallel workers, achievable on a residential connection

This script:
  1. Reads POLYGON_S3_KEY / POLYGON_S3_SECRET from env (separate from REST key)
  2. Lists missing files vs local manifest
  3. Downloads in parallel (16 workers default)
  4. Verifies via the .csv.gz integrity (header row present, gzip valid)
  5. Optionally converts to ZSTD Parquet partitioned by (year, month, day, ticker)

USAGE:
    # Dry run: list what's missing
    python scripts/polygon_trades_v1_pull.py --dry-run --start 2024-01-01 --end 2026-04-30

    # Single day to validate setup
    python scripts/polygon_trades_v1_pull.py --start 2024-03-04 --end 2024-03-04

    # Full historical pull (background, takes hours)
    python scripts/polygon_trades_v1_pull.py --start 2024-01-01 --end 2026-04-30 --workers 16

    # After download: convert all to partitioned Parquet
    python scripts/polygon_trades_v1_pull.py --convert-only

OUTPUT:
    data/polygon_warehouse/trades_v1/year=YYYY/month=MM/day=DD.csv.gz   (raw)
    data/polygon_warehouse/trades_v1_parquet/year=YYYY/month=MM/day=DD/ticker=XXX/*.parquet  (after --convert)
"""
from __future__ import annotations
import argparse
import concurrent.futures as cf
import gzip
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RAW_DIR = REPO / "data" / "polygon_warehouse" / "trades_v1"
PARQUET_DIR = REPO / "data" / "polygon_warehouse" / "trades_v1_parquet"


def _load_env_dotfile() -> None:
    """Best-effort load of .env into os.environ (without overwriting set vars)."""
    env_path = REPO / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.split("#", 1)[0].strip()
            if k and not os.environ.get(k):
                os.environ[k] = v
    except Exception as e:
        print(f"WARN: _load_env_dotfile failed: {e}")


_load_env_dotfile()

S3_BUCKET = "flatfiles"
# Polygon -> Massive rebrand (Oct 2025): files.polygon.io still works but
# files.massive.com is the canonical endpoint. Override via POLYGON_S3_ENDPOINT.
S3_ENDPOINT = os.environ.get("POLYGON_S3_ENDPOINT", "https://files.massive.com")
S3_PREFIX = "us_stocks_sip/trades_v1"


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        if cur.weekday() < 5:  # Mon-Fri only (Polygon publishes only trading days)
            yield cur
        cur += timedelta(days=1)


def s3_key_for(d: date) -> str:
    return f"{S3_PREFIX}/{d.year}/{d.month:02d}/{d.isoformat()}.csv.gz"


def local_path_for(d: date) -> Path:
    return RAW_DIR / f"year={d.year}" / f"month={d.month:02d}" / f"day={d.day:02d}.csv.gz"


def make_s3_client():
    """Lazy import boto3 so the script can be loaded without the dependency."""
    try:
        import boto3
        import botocore
    except ImportError:
        raise SystemExit(
            "boto3 not installed. Run: pip install boto3\n"
            "(Also requires POLYGON_S3_KEY and POLYGON_S3_SECRET env vars from\n"
            " polygon.io/dashboard/flat-files — these are SEPARATE from the REST key.)"
        )
    key = os.environ.get("POLYGON_S3_KEY")
    secret = os.environ.get("POLYGON_S3_SECRET")
    if not key or not secret:
        raise SystemExit(
            "POLYGON_S3_KEY / POLYGON_S3_SECRET env vars not set.\n"
            "Get them at: https://polygon.io/dashboard/flat-files"
        )
    sess = boto3.session.Session(aws_access_key_id=key, aws_secret_access_key=secret)
    cfg = botocore.client.Config(signature_version="s3v4", max_pool_connections=64)
    return sess.client("s3", endpoint_url=S3_ENDPOINT, config=cfg)


def fetch_one(d: date, dry_run: bool = False) -> tuple[date, str, int]:
    """Download a single day. Returns (date, status, size_bytes)."""
    out = local_path_for(d)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 1024:
        # Probably already downloaded; verify by checking gzip header
        try:
            with gzip.open(out, "rb") as f:
                header_line = f.readline()
                if b"ticker" in header_line:
                    return (d, "skip-already-exists", out.stat().st_size)
        except Exception as e:
            print(f"WARN: gzip header check failed for {out}, will re-download: {e}")

    if dry_run:
        return (d, "would-download", 0)

    s3 = make_s3_client()
    key = s3_key_for(d)
    try:
        s3.download_file(S3_BUCKET, key, str(out))
        sz = out.stat().st_size
        return (d, "ok", sz)
    except Exception as e:
        return (d, f"error: {str(e)[:200]}", 0)


def convert_to_parquet(d: date) -> tuple[date, str, int]:
    """Convert a raw CSV.gz day file to partitioned ZSTD Parquet via DuckDB."""
    raw = local_path_for(d)
    if not raw.exists():
        return (d, "missing-raw", 0)
    out_root = PARQUET_DIR / f"year={d.year}" / f"month={d.month:02d}" / f"day={d.day:02d}"
    out_root.mkdir(parents=True, exist_ok=True)
    try:
        import duckdb
        con = duckdb.connect()
        con.sql("SET memory_limit='12GB'")
        con.sql("SET threads=8")
        con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")
        con.sql("SET preserve_insertion_order=false")
        # Parquet schema (per Compass artifact 2 §A.1 schema):
        # ticker, conditions, correction, exchange, id, participant_timestamp,
        # price, sequence_number, sip_timestamp, size, tape, trf_id, trf_timestamp
        # NOTE: Windows filesystem is case-insensitive. If two ticker symbols
        # exist with different case (e.g. "BCPC" and "BCpC"), DuckDB's
        # PARTITION_BY tries to write both to the same dir → file lock.
        # Force UPPER on ticker so all case variants merge into one partition.
        con.sql(f"""
            COPY (
                SELECT * EXCLUDE (ticker),
                       UPPER(ticker) AS ticker,
                       to_timestamp(sip_timestamp/1e9) AT TIME ZONE 'UTC'
                         AT TIME ZONE 'America/New_York' AS ts_et
                FROM read_csv_auto('{raw.as_posix()}',
                                    compression='gzip',
                                    sample_size=10000)
            )
            TO '{out_root.as_posix()}'
            (FORMAT PARQUET, PARTITION_BY (ticker), COMPRESSION ZSTD,
             OVERWRITE_OR_IGNORE)
        """)
        # Sum partition sizes
        sz = sum(p.stat().st_size for p in out_root.rglob("*.parquet"))
        return (d, "ok", sz)
    except Exception as e:
        return (d, f"error: {str(e)[:300]}", 0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="2024-01-01",
                        help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None,
                        help="End date (YYYY-MM-DD); default = yesterday")
    parser.add_argument("--workers", type=int, default=16,
                        help="Parallel download workers (Polygon recommends 8-32)")
    parser.add_argument("--dry-run", action="store_true",
                        help="List missing files; don't download")
    parser.add_argument("--convert-only", action="store_true",
                        help="Skip downloads; only convert existing CSV.gz to Parquet")
    parser.add_argument("--no-convert", action="store_true",
                        help="Download only; skip Parquet conversion")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_str = args.end or (datetime.now().date() - timedelta(days=1)).isoformat()
    end = datetime.strptime(end_str, "%Y-%m-%d").date()
    days = list(daterange(start, end))

    section(f"PHASE 3 TRADE TAPE PULL  start={start}  end={end}  trading_days={len(days)}")
    print(f"  workers={args.workers}  dry_run={args.dry_run}  convert_only={args.convert_only}")
    print(f"  raw dest:     {RAW_DIR}")
    print(f"  parquet dest: {PARQUET_DIR}")
    print(f"  S3 endpoint:  {S3_ENDPOINT}/{S3_BUCKET}/{S3_PREFIX}/")
    expected_gb = len(days) * 0.85  # ~850 MB/day average
    print(f"  expected raw download volume: ~{expected_gb:.0f} GB ({len(days)} days x ~850 MB)")

    if not args.convert_only:
        section("STEP 1 - Download")
        if args.dry_run:
            missing = [d for d in days if not local_path_for(d).exists()]
            print(f"  {len(missing)}/{len(days)} files missing locally:")
            for d in missing[:10]:
                print(f"    would download {s3_key_for(d)}")
            if len(missing) > 10:
                print(f"    ... and {len(missing) - 10} more")
            return 0

        ok_count = 0
        skip_count = 0
        err_count = 0
        total_bytes = 0
        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(fetch_one, d, False): d for d in days}
            for f in cf.as_completed(futures):
                d, status, sz = f.result()
                total_bytes += sz
                if status == "ok":
                    ok_count += 1
                elif "skip" in status:
                    skip_count += 1
                else:
                    err_count += 1
                    print(f"  ERROR {d}: {status}")
        print(f"\n  downloaded={ok_count}  skipped={skip_count}  errors={err_count}  "
              f"total={total_bytes/1e9:.2f} GB")

    if not args.no_convert and not args.dry_run:
        section("STEP 2 - Convert CSV.gz -> partitioned ZSTD Parquet")
        # Process serially (DuckDB conversion is CPU/IO bound; parallelism
        # benefits less and risks memory blowups on 1GB files)
        ok = 0
        err = 0
        total_pq = 0
        for d in days:
            if not local_path_for(d).exists():
                continue
            d, status, sz = convert_to_parquet(d)
            total_pq += sz
            if status == "ok":
                ok += 1
                if ok % 10 == 0:
                    print(f"  converted {ok}/{len(days)}  total parquet={total_pq/1e9:.2f} GB")
            else:
                err += 1
                print(f"  CONVERT-ERROR {d}: {status}")
        print(f"\n  converted={ok}  errors={err}  total parquet={total_pq/1e9:.2f} GB")

    section("DONE")
    print(f"  next steps:")
    print(f"    1. Build microstructure features from trades_v1_parquet:")
    print(f"       - sweep_burst_rate (count of conditions=15 per minute)")
    print(f"       - dark_pool_pct (sum size where exchange=4 / total size)")
    print(f"       - large_print_pct (sum size where size >= 10000 / total)")
    print(f"       - true_vwap (vwap excluding conditions 6, 7, 13)")
    print(f"    2. Add features to v2 ensemble; rerun walk-forward")
    print(f"    3. Compare v2-with-microstructure vs v2-baseline (+9.30% MID-mag)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
