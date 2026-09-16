"""Polygon Flat Files (S3) bulk downloader.

Per docs/research/polygon_compass_playbook.md §A.1, the single biggest
unlock is the S3-compatible flat-file corpus at https://files.polygon.io.

Datasets pulled (us_stocks_sip/):
  day_aggs_v1     ~3 MB/day      ~4 MB/yr            (cheapest, do first)
  minute_aggs_v1  ~50-80 MB/day  ~30-50 GB total    (workhorse)
  trades_v1       ~500MB-1GB/day ~300-500 GB total  (forensics)
  quotes_v1       ~1-2 GB/day    ~600 GB-1 TB total (NBBO history)

USAGE:
    # Sanity check creds
    python scripts/polygon_flatfile_pull.py --check
    # Download last 30 days of day_aggs (smoke test, ~100 MB)
    python scripts/polygon_flatfile_pull.py --dataset day_aggs_v1 --days 30
    # Download last 600 days of minute_aggs (~50 GB; backgroundable)
    python scripts/polygon_flatfile_pull.py --dataset minute_aggs_v1 --days 600
    # Download specific date range
    python scripts/polygon_flatfile_pull.py --dataset minute_aggs_v1 --start 2024-01-01 --end 2024-12-31

ENVIRONMENT:
    POLYGON_S3_KEY      Polygon dashboard → Flat Files → Access Key ID
    POLYGON_S3_SECRET   Polygon dashboard → Flat Files → Secret Access Key
    (these are SEPARATE from POLYGON_API_KEY)

OUTPUT layout:
    data/polygon_flatfiles/
        day_aggs_v1/{YYYY}/{YYYY-MM-DD}.csv.gz
        minute_aggs_v1/{YYYY}/{YYYY-MM-DD}.csv.gz
        ...

RESUMABILITY: skips files that already exist with non-zero size.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import logging
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import boto3
import botocore.client
import botocore.exceptions

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "data" / "polygon_flatfiles"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Per Compass Caveat #1: Polygon → Massive rebrand (2025-10-30).
# files.polygon.io continues to work but new dashboard issues files.massive.com.
ENDPOINT_URL = os.environ.get("POLYGON_S3_ENDPOINT", "https://files.massive.com")
BUCKET = os.environ.get("POLYGON_S3_BUCKET", "flatfiles")
DATASETS = {
    "day_aggs_v1":    {"prefix": "us_stocks_sip/day_aggs_v1",    "approx_mb_per_day": 0.25},
    "minute_aggs_v1": {"prefix": "us_stocks_sip/minute_aggs_v1", "approx_mb_per_day": 65},
    "trades_v1":      {"prefix": "us_stocks_sip/trades_v1",      "approx_mb_per_day": 700},
    "quotes_v1":      {"prefix": "us_stocks_sip/quotes_v1",      "approx_mb_per_day": 1500},
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("polygon_s3")


def make_s3_client():
    key = os.environ.get("POLYGON_S3_KEY", "").strip()
    secret = os.environ.get("POLYGON_S3_SECRET", "").strip()
    if not key or not secret:
        raise RuntimeError(
            "POLYGON_S3_KEY / POLYGON_S3_SECRET not set in environment.\n"
            "  Get them from polygon.io/dashboard → Flat Files → Access Keys.\n"
            "  These are SEPARATE from your POLYGON_API_KEY."
        )
    session = boto3.session.Session(
        aws_access_key_id=key,
        aws_secret_access_key=secret,
    )
    return session.client(
        "s3",
        endpoint_url=ENDPOINT_URL,
        config=botocore.client.Config(
            signature_version="s3v4",
            max_pool_connections=64,
            retries={"max_attempts": 5, "mode": "adaptive"},
        ),
    )


def trading_days_back(n: int, end: date | None = None) -> list[date]:
    """Approximate: skip Sat/Sun. We'll rely on Polygon's 404 for actual
    holidays (cheap to skip)."""
    end = end or date.today() - timedelta(days=1)
    out: list[date] = []
    cur = end
    while len(out) < n:
        if cur.weekday() < 5:  # Mon-Fri
            out.append(cur)
        cur -= timedelta(days=1)
    out.reverse()
    return out


def trading_days_range(start: date, end: date) -> list[date]:
    out: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def fetch_one(s3, dataset: str, d: date) -> tuple[date, str, int]:
    """Download one (dataset, date) → local file. Returns (date, status, bytes).

    status ∈ {"ok", "skipped", "404", "error: ..."}
    """
    info = DATASETS[dataset]
    key = f"{info['prefix']}/{d.year}/{d.month:02d}/{d.isoformat()}.csv.gz"
    out_path = OUT_DIR / dataset / str(d.year) / f"{d.isoformat()}.csv.gz"
    if out_path.exists() and out_path.stat().st_size > 0:
        return d, "skipped", out_path.stat().st_size
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".csv.gz.tmp")
    try:
        s3.download_file(BUCKET, key, str(tmp_path))
        sz = tmp_path.stat().st_size
        if sz == 0:
            tmp_path.unlink(missing_ok=True)
            return d, "error: zero bytes", 0
        os.replace(tmp_path, out_path)
        return d, "ok", sz
    except botocore.exceptions.ClientError as e:
        tmp_path.unlink(missing_ok=True)
        code = e.response.get("Error", {}).get("Code", "?")
        if code in ("NoSuchKey", "404"):
            return d, "404", 0
        return d, f"error: {code}", 0
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        return d, f"error: {e}", 0


def check_credentials():
    log.info("Checking POLYGON_S3 credentials ...")
    try:
        s3 = make_s3_client()
    except RuntimeError as e:
        log.error(str(e))
        return 1
    try:
        # Try a HEAD on a known-good key (yesterday's day_aggs)
        d = trading_days_back(1)[0]
        key = f"us_stocks_sip/day_aggs_v1/{d.year}/{d.month:02d}/{d.isoformat()}.csv.gz"
        s3.head_object(Bucket=BUCKET, Key=key)
        log.info("OK — credentials valid, sample key %s exists", key)
        return 0
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "?")
        if code == "403":
            log.error("403 Forbidden — credentials are likely invalid or "
                      "your subscription doesn't include flat files.")
        elif code == "NoSuchKey":
            log.warning("NoSuchKey for sample key — try a more recent date.")
        else:
            log.error("Auth check failed: %s", e)
        return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Verify credentials and exit")
    parser.add_argument("--dataset", choices=list(DATASETS.keys()),
                         default="day_aggs_v1")
    parser.add_argument("--days", type=int, default=None,
                         help="Pull last N trading days")
    parser.add_argument("--start", type=str, default=None, help="YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="YYYY-MM-DD")
    parser.add_argument("--workers", type=int, default=16,
                         help="Parallel download workers (Compass: 16-32 sweet spot)")
    parser.add_argument("--dry-run", action="store_true",
                         help="Just list what would be downloaded")
    args = parser.parse_args()

    if args.check:
        return check_credentials()

    # Build date list
    if args.days:
        dates = trading_days_back(args.days)
    elif args.start and args.end:
        dates = trading_days_range(
            date.fromisoformat(args.start), date.fromisoformat(args.end),
        )
    else:
        dates = trading_days_back(60)  # default

    info = DATASETS[args.dataset]
    est_mb = len(dates) * info["approx_mb_per_day"]
    log.info("Dataset:      %s", args.dataset)
    log.info("Date range:   %s → %s", dates[0], dates[-1])
    log.info("N days:       %d trading days", len(dates))
    log.info("Est. size:    ~%.1f MB (%.2f GB)", est_mb, est_mb / 1024)
    log.info("Workers:      %d", args.workers)
    log.info("Output dir:   %s", OUT_DIR / args.dataset)

    if args.dry_run:
        log.info("[DRY-RUN] not downloading.")
        return 0

    if est_mb > 5000:
        log.warning("LARGE DOWNLOAD (%.2f GB) — proceeding in 5s ...", est_mb / 1024)
        time.sleep(5)

    s3 = make_s3_client()
    t0 = time.time()
    counts = {"ok": 0, "skipped": 0, "404": 0, "error": 0}
    bytes_total = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(fetch_one, s3, args.dataset, d): d for d in dates}
        for i, fut in enumerate(cf.as_completed(futures), 1):
            d, status, sz = fut.result()
            if status == "ok":
                counts["ok"] += 1; bytes_total += sz
            elif status == "skipped":
                counts["skipped"] += 1; bytes_total += sz
            elif status == "404":
                counts["404"] += 1
            else:
                counts["error"] += 1
                log.warning("  %s → %s", d, status)
            if i % 25 == 0 or i == len(dates):
                rate_mb_s = bytes_total / (time.time() - t0) / 1e6
                pct = i / len(dates) * 100
                log.info("Progress: %d/%d (%.0f%%) | ok=%d skip=%d 404=%d err=%d | "
                         "%.1f MB total | %.1f MB/s avg",
                         i, len(dates), pct, counts["ok"], counts["skipped"],
                         counts["404"], counts["error"],
                         bytes_total / 1e6, rate_mb_s)

    elapsed = time.time() - t0
    log.info("DONE in %.1fs", elapsed)
    log.info("  ok=%d skipped=%d 404=%d error=%d",
             counts["ok"], counts["skipped"], counts["404"], counts["error"])
    log.info("  Total: %.1f MB (%.2f GB)", bytes_total / 1e6, bytes_total / 1e9)
    return 0 if counts["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
