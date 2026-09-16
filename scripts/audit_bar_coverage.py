"""Audit bar-recording coverage against the trade tape.

Block B.3 of the Block 4.4 unblock work. Compares
data/bar_recordings/<date>/*.json filenames against the deduped
trade ticker set from data/trade_results.jsonl. Surfaces any trade
whose bars are missing or partial — prevents silent recurrence of
the D121 dynamic-subscription gap (doc 61).

Output: data/audits/bar_coverage_status.parquet — one row per
deduped trade with status ∈ {present, missing, partial, backfilled}.

Usage:
    python scripts/audit_bar_coverage.py
    python scripts/audit_bar_coverage.py --since 2026-04-22 --until 2026-04-28
    python scripts/audit_bar_coverage.py --fail-on-missing  # exit 1 if any missing
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("audit_bar_coverage")

REPO_ROOT = Path(__file__).resolve().parent.parent
TRADE_RESULTS = REPO_ROOT / "data" / "trade_results.jsonl"
BAR_RECORDINGS = REPO_ROOT / "data" / "bar_recordings"
DEFAULT_OUT = REPO_ROOT / "data" / "audits" / "bar_coverage_status.parquet"

# Minimum bars to count as "present" (a 6.5-hour session = 390 bars;
# we accept >=200 as present, >=50 as partial).
PRESENT_THRESHOLD = 200
PARTIAL_THRESHOLD = 50


@dataclass
class CoverageRow:
    session_date: str
    ticker: str
    entry_ts: str
    is_carry: bool
    bar_file: str           # relative path
    bars_count: int
    source: str             # "live" | "backfill_alpaca_v0.1" | "missing"
    status: str             # present | partial | missing | backfilled


def _bar_file_status(*, ticker: str, date: str) -> tuple[str, int, str]:
    """Returns (file_rel, bars_count, source)."""
    p = BAR_RECORDINGS / date / f"{ticker.upper()}.json"
    if not p.exists():
        return ("", 0, "missing")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return (str(p.relative_to(REPO_ROOT)), 0, "corrupt")
    bars = data.get("bars") or []
    source = data.get("source", "live")
    return (str(p.relative_to(REPO_ROOT)), len(bars), source)


def load_trades(*, since: str | None, until: str | None) -> list[dict]:
    out = []
    with TRADE_RESULTS.open("r", encoding="utf-8") as f:
        for line in f:
            j = json.loads(line)
            sd = j.get("session_date", "")
            if since and sd < since:
                continue
            if until and sd > until:
                continue
            entry = datetime.fromisoformat(j["entry_time"])
            exit_ = datetime.fromisoformat(j["exit_time"])
            out.append({
                "ticker": j["ticker"],
                "session_date": sd,
                "entry_time": entry,
                "exit_time": exit_,
                "is_carry": entry.date() < exit_.date(),
            })
    # Dedupe by (ticker, entry_time)
    seen: dict[tuple, dict] = {}
    for t in out:
        key = (t["ticker"], t["entry_time"].isoformat())
        if t["is_carry"]:
            existing = seen.get(key)
            if existing is None or t["exit_time"] < existing["exit_time"]:
                seen[key] = t
        else:
            seen[key] = t
    return sorted(seen.values(), key=lambda r: r["entry_time"])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--output", type=Path, default=DEFAULT_OUT)
    p.add_argument("--fail-on-missing", action="store_true",
                   help="Exit 1 if any non-carry trade has missing bars")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    trades = load_trades(since=args.since, until=args.until)
    logger.info("auditing coverage for %d deduped trades", len(trades))

    rows: list[CoverageRow] = []
    for t in trades:
        file_rel, bars_count, source = _bar_file_status(
            ticker=t["ticker"], date=t["session_date"],
        )
        if source == "missing":
            status = "missing"
        elif source == "backfill_alpaca_v0.1":
            status = "backfilled"
        elif bars_count >= PRESENT_THRESHOLD:
            status = "present"
        elif bars_count >= PARTIAL_THRESHOLD:
            status = "partial"
        else:
            status = "missing"

        rows.append(CoverageRow(
            session_date=t["session_date"],
            ticker=t["ticker"],
            entry_ts=t["entry_time"].isoformat(),
            is_carry=t["is_carry"],
            bar_file=file_rel,
            bars_count=bars_count,
            source=source,
            status=status,
        ))

    # Print summary
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    logger.info("Coverage summary: %s", counts)

    # Per-row report
    logger.info("Per-trade detail:")
    for r in rows:
        logger.info(
            "  %s %s carry=%s status=%-11s bars=%4d source=%s",
            r.session_date, r.ticker, r.is_carry, r.status, r.bars_count, r.source,
        )

    # Write parquet
    try:
        import pandas as pd
        args.output.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame([asdict(r) for r in rows])
        df.to_parquet(args.output, index=False)
        logger.info("wrote %s (n=%d rows)", args.output, len(df))
    except ImportError:
        logger.warning("pandas not available — skipping parquet write")

    if args.fail_on_missing:
        non_carry_missing = [r for r in rows if not r.is_carry and r.status == "missing"]
        if non_carry_missing:
            logger.error(
                "FAIL: %d non-carry trades missing bars — run "
                "scripts/backfill_historical_bars.py to close gaps",
                len(non_carry_missing),
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
