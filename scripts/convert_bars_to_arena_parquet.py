"""Convert recorded bar JSON files into the parquet shape mx-arena consumes.

Production records bars as:
    data/bar_recordings/{date}/{TICKER}.json
where each file is:
    {"ticker": "LIDR", "date": "2026-04-28",
     "bars": [{"timestamp": "...Z", "open": ..., "high": ..., "low": ...,
               "close": ..., "volume": ..., "vwap": ...}, ...]}

Arena's data_engine._load_parquet_bars searches:
    {historical_dir}/{symbol}/{date}.parquet
or
    {historical_dir}/{symbol}_{date}.parquet
with rows containing either short keys (t/o/h/l/c/v/vw/n) or long keys
(timestamp/open/high/low/close/volume/vwap/trade_count). We emit short
keys (canonical Polygon-flavor) since several arena tests use them.

Usage:
    # convert one date
    python scripts/convert_bars_to_arena_parquet.py --date 2026-04-28

    # convert a range
    python scripts/convert_bars_to_arena_parquet.py --since 2026-04-01

    # dry run (count files but don't write)
    python scripts/convert_bars_to_arena_parquet.py --date 2026-04-28 --dry-run

Idempotent: rewriting an existing parquet is safe (overwrite). The
converter validates input shape and skips malformed files with a
warning rather than crashing the batch.

Discipline (per docs/research-log/58_arena_prod_parity_plan.md §3):
  - One-way conversion (recordings are source of truth; parquet is
    derived). Round-trip tests pin OHLCV+timestamp identity.
  - Default output dir is mx-arena/data/historical/ — arena's
    expected location. Override with --out for one-off testing.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("convert_bars")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_DIR = REPO_ROOT / "data" / "bar_recordings"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "mx-arena" / "data" / "historical"

# Mapping from production long keys → arena short keys (Polygon-style)
LONG_TO_SHORT = {
    "timestamp": "t",
    "open": "o",
    "high": "h",
    "low": "l",
    "close": "c",
    "volume": "v",
    "vwap": "vw",
    "trade_count": "n",
}


def iter_session_dates(input_dir: Path) -> Iterator[str]:
    """Yield date strings (YYYY-MM-DD) for which we have recordings."""
    if not input_dir.exists():
        return
    for child in sorted(input_dir.iterdir()):
        if child.is_dir() and len(child.name) == 10 and child.name[4] == "-":
            yield child.name


def iter_recording_files(input_dir: Path, date: str) -> Iterator[Path]:
    """Yield paths of {TICKER}.json files for a given date."""
    day_dir = input_dir / date
    if not day_dir.exists():
        return
    for path in sorted(day_dir.glob("*.json")):
        yield path


def load_recording(path: Path) -> tuple[str, str, list[dict]] | None:
    """Parse a recording file. Returns (ticker, date, bars) or None on error."""
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("skip %s: %s", path, e)
        return None

    ticker = data.get("ticker")
    date = data.get("date")
    bars = data.get("bars")
    if not ticker or not date or not isinstance(bars, list):
        logger.warning(
            "skip %s: malformed (ticker=%r date=%r bars=%s)",
            path, ticker, date, type(bars).__name__,
        )
        return None
    return ticker.upper(), date, bars


def normalize_bar(raw: dict) -> dict | None:
    """Convert a recorded bar (long keys) into arena-shape (short keys).

    Returns None if a required field is missing/unparseable. We are strict
    here: a missing OHLCV field is a data-quality issue worth surfacing,
    not silently filling with zeros (which would break downstream stats).
    """
    out: dict = {}
    for long_key, short_key in LONG_TO_SHORT.items():
        if long_key not in raw:
            if long_key == "trade_count":
                out[short_key] = 0  # optional; default 0
                continue
            if long_key == "vwap":
                # VWAP is computable from OHLC fallback; default to close
                out[short_key] = float(raw.get("close", 0)) or 0.0
                continue
            return None
        try:
            if long_key == "timestamp":
                out[short_key] = str(raw[long_key])
            elif long_key == "volume" or long_key == "trade_count":
                out[short_key] = int(raw[long_key])
            else:
                out[short_key] = float(raw[long_key])
        except (TypeError, ValueError):
            return None
    return out


def write_parquet(
    *, ticker: str, date: str, bars: list[dict], out_dir: Path,
) -> Path:
    """Write bars to {out_dir}/{ticker}/{date}.parquet."""
    try:
        import pandas as pd
    except ImportError as e:
        raise SystemExit(
            "pandas is required for parquet output. "
            "Install: pip install pandas pyarrow"
        ) from e

    target_dir = out_dir / ticker
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{date}.parquet"

    df = pd.DataFrame(bars)
    # Sort by timestamp to enforce chronological order (arena also sorts;
    # doing it once here avoids per-load cost).
    df = df.sort_values("t").reset_index(drop=True)
    df.to_parquet(target, index=False)
    return target


def convert_one_date(
    *, date: str, input_dir: Path, out_dir: Path, dry_run: bool,
) -> tuple[int, int]:
    """Convert all tickers for one date. Returns (converted, skipped)."""
    converted = 0
    skipped = 0
    for path in iter_recording_files(input_dir, date):
        rec = load_recording(path)
        if rec is None:
            skipped += 1
            continue
        ticker, recorded_date, bars = rec
        if recorded_date != date:
            logger.warning(
                "skip %s: filename date %s != recorded date %s",
                path, date, recorded_date,
            )
            skipped += 1
            continue
        normalized: list[dict] = []
        bad_bars = 0
        for raw in bars:
            n = normalize_bar(raw)
            if n is None:
                bad_bars += 1
                continue
            normalized.append(n)
        if bad_bars:
            logger.warning(
                "%s/%s: dropped %d malformed bars (kept %d)",
                ticker, date, bad_bars, len(normalized),
            )
        if not normalized:
            logger.warning("%s/%s: zero usable bars after normalize — skip", ticker, date)
            skipped += 1
            continue
        if dry_run:
            logger.info(
                "[dry-run] would write %s/%s.parquet (n=%d bars)",
                ticker, date, len(normalized),
            )
        else:
            target = write_parquet(
                ticker=ticker, date=date, bars=normalized, out_dir=out_dir,
            )
            logger.info("wrote %s (n=%d bars)", target, len(normalized))
        converted += 1
    return converted, skipped


def parse_date(s: str) -> str:
    datetime.strptime(s, "%Y-%m-%d")  # validate
    return s


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", type=parse_date, help="Convert a single session date.")
    p.add_argument("--since", type=parse_date,
                   help="Convert all session dates from this date forward.")
    p.add_argument("--all", action="store_true",
                   help="Convert every available session date.")
    p.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR,
                   help=f"Bar recording root (default: {DEFAULT_INPUT_DIR})")
    p.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help=f"Arena historical dir (default: {DEFAULT_OUTPUT_DIR})")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be written; don't write.")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    if not (args.date or args.since or args.all):
        p.error("specify one of: --date, --since, --all")

    if args.date:
        dates = [args.date]
    elif args.since:
        all_dates = list(iter_session_dates(args.input_dir))
        dates = [d for d in all_dates if d >= args.since]
    else:
        dates = list(iter_session_dates(args.input_dir))

    if not dates:
        logger.error("no session dates to convert under %s", args.input_dir)
        return 1

    total_converted = 0
    total_skipped = 0
    for date in dates:
        converted, skipped = convert_one_date(
            date=date,
            input_dir=args.input_dir,
            out_dir=args.out,
            dry_run=args.dry_run,
        )
        total_converted += converted
        total_skipped += skipped
        logger.info(
            "%s: converted=%d skipped=%d", date, converted, skipped,
        )

    logger.info(
        "DONE: %d session-date(s), %d files converted, %d skipped",
        len(dates), total_converted, total_skipped,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
