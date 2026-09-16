"""Block A.1 audit: residuals between bar.open at entry minute and
prod's actual fill price, broken out by entry path.

Per the brief: "the audit IS the evidence for the fix; commit it
before writing code." This script computes the structural residuals
across every replayable entry in the truth corpus and surfaces
which trades and which entry paths are affected by >100 bps.

Output:
    data/audits/limit_vs_baropen_residuals.parquet
        Columns: ticker, session_date, entry_ts, entry_path,
                 prod_fill_px, bar_open_px, bar_high_px, bar_low_px,
                 residual_bps, residual_dollar, prod_qty, structural

Entry-path classification (from log scan):
    FAST_PATH_OTO    — D85 SUBMITTED line present
    RESCAN_LIMIT     — D217 filled_avg_price line present (no D85)
    UNKNOWN          — neither (probably market entries from other paths)

`structural=True` if |residual_bps| > 100 (the threshold for
limit-vs-bar-open mismatches that need the limit-aware fill fix).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("audit_limit")

REPO_ROOT = Path(__file__).resolve().parent.parent
QTY_TRUTH = REPO_ROOT / "data" / "replay" / "prod_qty_truth.parquet"
LOGS_DIR = REPO_ROOT / "logs"
ARENA_HISTORICAL = REPO_ROOT / "mx-arena" / "data" / "historical"
DEFAULT_OUT = REPO_ROOT / "data" / "audits" / "limit_vs_baropen_residuals.parquet"

sys.path.insert(0, str(REPO_ROOT / "mx-arena"))

# Entry-path detection patterns
_RE_D85 = re.compile(r"D85 SUBMITTED:\s+(\w+)")
_RE_D217 = re.compile(r"D217:\s+(\w+)\s+order\s+\w+\s+reached\s+terminal\s+status=filled")


@dataclass
class ResidualRow:
    ticker: str
    session_date: str
    entry_ts: str
    entry_path: str           # FAST_PATH_OTO | RESCAN_LIMIT | UNKNOWN
    prod_fill_px: float
    bar_open_px: float
    bar_high_px: float
    bar_low_px: float
    residual_bps: float
    residual_dollar_per_share: float
    prod_qty: int
    structural: bool          # |residual_bps| > 100


def detect_entry_path(*, ticker: str, session_date: str) -> str:
    log = LOGS_DIR / f"momentum_{session_date}.log"
    if not log.exists():
        return "UNKNOWN"
    text = log.read_text(encoding="utf-8", errors="replace")
    if any(m == ticker for m in _RE_D85.findall(text)):
        return "FAST_PATH_OTO"
    if any(m == ticker for m in _RE_D217.findall(text)):
        return "RESCAN_LIMIT"
    return "UNKNOWN"


def load_bar_at_entry(*, ticker: str, session_date: str, entry_ts: datetime):
    from arena.data_engine import DataEngine
    from arena.clock import SimClock, ClockMode
    target = ARENA_HISTORICAL / ticker / f"{session_date}.parquet"
    if not target.exists():
        return None
    clock = SimClock(
        start=datetime.fromisoformat(f"{session_date}T13:30:00+00:00"),
        end=datetime.fromisoformat(f"{session_date}T20:00:00+00:00"),
        mode=ClockMode.REPLAY,
    )
    bars = DataEngine(clock=clock, historical_dir=str(ARENA_HISTORICAL))._load_parquet_bars(ticker, session_date)
    if not bars:
        return None
    target_iso = entry_ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    last = None
    for _i, b in bars.items():
        if b.timestamp > target_iso:
            break
        last = b
    return last


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    import pandas as pd
    qty_df = pd.read_parquet(QTY_TRUTH)
    logger.info("auditing %d truth-corpus entries", len(qty_df))

    rows: list[ResidualRow] = []
    for _, r in qty_df.iterrows():
        ticker = r["ticker"]
        sd = r["session_date"]
        entry_iso = r["entry_ts"]
        entry_dt = datetime.fromisoformat(entry_iso)
        prod_fill = float(r["prod_entry_avg_px"])
        bar = load_bar_at_entry(ticker=ticker, session_date=sd, entry_ts=entry_dt)
        if bar is None:
            logger.warning("skip %s/%s: no bar at entry", ticker, sd)
            continue
        bar_open = float(bar.open)
        bar_high = float(bar.high)
        bar_low = float(bar.low)
        if bar_open <= 0:
            continue
        residual_bps = (prod_fill - bar_open) / bar_open * 1e4
        path = detect_entry_path(ticker=ticker, session_date=sd)
        rows.append(ResidualRow(
            ticker=ticker, session_date=sd, entry_ts=entry_iso,
            entry_path=path,
            prod_fill_px=prod_fill, bar_open_px=bar_open,
            bar_high_px=bar_high, bar_low_px=bar_low,
            residual_bps=round(residual_bps, 2),
            residual_dollar_per_share=round(prod_fill - bar_open, 4),
            prod_qty=int(r["prod_qty"]),
            structural=abs(residual_bps) > 100.0,
        ))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([asdict(x) for x in rows])
    df.to_parquet(args.output, index=False)
    logger.info("wrote %s (n=%d rows)", args.output, len(df))

    # Summary
    print("\n=== Residual audit summary ===")
    print(df[["ticker","session_date","entry_path","prod_fill_px","bar_open_px","bar_high_px","bar_low_px","residual_bps","structural"]].to_string())
    print()
    by_path = df.groupby("entry_path").agg(
        n=("ticker", "count"),
        n_structural=("structural", "sum"),
        mean_abs_bps=("residual_bps", lambda s: s.abs().mean()),
        median_abs_bps=("residual_bps", lambda s: s.abs().median()),
    )
    print("By entry path:")
    print(by_path.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
