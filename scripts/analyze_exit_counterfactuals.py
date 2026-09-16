"""For each broker-truth closed trade, compute counterfactual P&L
under alternative exit policies, plus MFE / MAE analysis.

The goal: identify whether the strategy's EXIT POLICY is the bug,
not the entry policy. If many trades had significant favorable
excursion (MFE) that wasn't captured, exits are too early. If
many had bad adverse excursion (MAE) that the actual exit didn't
respect, stops are too loose / late.

Counterfactual exit policies tested per trade:
  - exit at +60s (BAR-1 default)
  - exit at +5min
  - exit at +15min
  - exit at +30min
  - exit at end-of-session (EOD)
  - exit at next-day open (T+1)
  - exit at +5-day close (T+5; CRCA-class)

Plus MFE / MAE relative to entry price within the hold window.

Output: docs/oos/2025-11-to-2026-04_exit_counterfactual.md
        data/broker_truth/exit_counterfactuals.parquet
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("exit_counterfactuals")

REPO_ROOT = Path(__file__).resolve().parent.parent
ARENA_HISTORICAL = REPO_ROOT / "mx-arena" / "data" / "historical"
DEFAULT_INPUT = REPO_ROOT / "data" / "broker_truth" / "closed_trades.parquet"
DEFAULT_OUT_DOC = REPO_ROOT / "docs" / "oos" / "2025-11-to-2026-04_exit_counterfactual.md"
DEFAULT_OUT_DATA = REPO_ROOT / "data" / "broker_truth" / "exit_counterfactuals.parquet"

sys.path.insert(0, str(REPO_ROOT / "mx-arena"))


@dataclass
class CounterfactualRow:
    symbol: str
    entry_ts: str
    exit_ts: str
    entry_price: float
    actual_exit_price: float
    qty: int
    actual_pnl: float
    is_carry: bool
    hold_seconds: int
    # Counterfactuals — exit price + P&L
    px_t60s: float = 0.0
    pnl_t60s: float = 0.0
    px_t5min: float = 0.0
    pnl_t5min: float = 0.0
    px_t15min: float = 0.0
    pnl_t15min: float = 0.0
    px_t30min: float = 0.0
    pnl_t30min: float = 0.0
    px_eod: float = 0.0
    pnl_eod: float = 0.0
    px_t1: float = 0.0
    pnl_t1: float = 0.0
    px_t5: float = 0.0
    pnl_t5: float = 0.0
    # MFE / MAE within actual hold window
    mfe_dollars: float = 0.0      # Max favorable excursion ($)
    mae_dollars: float = 0.0      # Max adverse excursion ($, signed: negative)
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    bars_loaded: bool = False     # diagnostic


def _load_session_bars(*, ticker: str, session_date: str):
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
    return DataEngine(clock=clock, historical_dir=str(ARENA_HISTORICAL))._load_parquet_bars(ticker, session_date)


def _bar_at_or_after(bars_map: dict, ts: datetime):
    target_iso = ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    for _idx, b in bars_map.items():
        if b.timestamp >= target_iso:
            return b
    return list(bars_map.values())[-1]


def compute_counterfactuals(*, trades: list[dict]) -> list[CounterfactualRow]:
    """For each broker trade, look up bars on entry date + later dates
    to compute counterfactual exit P&L."""
    rows: list[CounterfactualRow] = []
    bars_cache: dict[tuple[str, str], dict] = {}  # (ticker, date) -> bars map
    n_no_bars = 0

    for t in trades:
        sym = t["symbol"]
        entry_dt = datetime.fromisoformat(t["entry_ts"].replace("Z", "+00:00"))
        exit_dt = datetime.fromisoformat(t["exit_ts"].replace("Z", "+00:00"))
        entry_session = entry_dt.astimezone(timezone.utc).date().isoformat()
        entry_px = float(t["entry_price"])
        actual_exit_px = float(t["exit_price"])
        qty = int(t["entry_qty"])

        row = CounterfactualRow(
            symbol=sym, entry_ts=t["entry_ts"], exit_ts=t["exit_ts"],
            entry_price=entry_px, actual_exit_price=actual_exit_px,
            qty=qty, actual_pnl=float(t["realized_pnl"]),
            is_carry=bool(t.get("is_carry", False)),
            hold_seconds=int(t.get("hold_seconds", 0)),
        )

        # Load entry-day bars
        cache_key = (sym, entry_session)
        if cache_key not in bars_cache:
            bars_cache[cache_key] = _load_session_bars(ticker=sym, session_date=entry_session)
        entry_bars = bars_cache.get(cache_key)
        if entry_bars is None:
            n_no_bars += 1
            rows.append(row)
            continue
        row.bars_loaded = True

        # Counterfactual exits within entry session
        for hold_sec, attr_px, attr_pnl in [
            (60, "px_t60s", "pnl_t60s"),
            (300, "px_t5min", "pnl_t5min"),
            (900, "px_t15min", "pnl_t15min"),
            (1800, "px_t30min", "pnl_t30min"),
        ]:
            target_ts = entry_dt + timedelta(seconds=hold_sec)
            bar = _bar_at_or_after(entry_bars, target_ts)
            if bar:
                px = float(bar.open)
                setattr(row, attr_px, px)
                setattr(row, attr_pnl, round((px - entry_px) * qty, 2))

        # EOD same session
        eod_bar = list(entry_bars.values())[-1]
        if eod_bar:
            row.px_eod = float(eod_bar.close)
            row.pnl_eod = round((row.px_eod - entry_px) * qty, 2)

        # T+1 (next session's open) — try +1 to +5 days for next available bar parquet
        t1_done = False
        t5_done = False
        for days_ahead in range(1, 8):
            future_date = (entry_dt + timedelta(days=days_ahead)).astimezone(timezone.utc).date().isoformat()
            future_key = (sym, future_date)
            if future_key not in bars_cache:
                bars_cache[future_key] = _load_session_bars(ticker=sym, session_date=future_date)
            future_bars = bars_cache.get(future_key)
            if future_bars is None:
                continue
            first_bar = list(future_bars.values())[0]
            if not t1_done:
                row.px_t1 = float(first_bar.open)
                row.pnl_t1 = round((row.px_t1 - entry_px) * qty, 2)
                t1_done = True
            # T+5: roughly 5 trading days after entry (using calendar days as proxy)
            if days_ahead >= 5 and not t5_done:
                # Use last bar of that session as T+5 close
                t5_bar = list(future_bars.values())[-1]
                row.px_t5 = float(t5_bar.close)
                row.pnl_t5 = round((row.px_t5 - entry_px) * qty, 2)
                t5_done = True
                break

        # MFE / MAE within actual hold window (entry session bars only — multi-day MFE
        # would require iterating multiple sessions; for MVP use entry session)
        max_high = entry_px
        min_low = entry_px
        for bar in entry_bars.values():
            try:
                bts = datetime.fromisoformat(bar.timestamp.replace("Z", "+00:00"))
            except Exception:
                continue
            if bts >= entry_dt and bts <= exit_dt:
                max_high = max(max_high, float(bar.high))
                min_low = min(min_low, float(bar.low))
        row.mfe_dollars = round((max_high - entry_px) * qty, 2)
        row.mae_dollars = round((min_low - entry_px) * qty, 2)
        row.mfe_pct = round((max_high - entry_px) / entry_px * 100, 2) if entry_px > 0 else 0
        row.mae_pct = round((min_low - entry_px) / entry_px * 100, 2) if entry_px > 0 else 0

        rows.append(row)

    logger.info("computed counterfactuals for %d trades; %d had no bars", len(rows), n_no_bars)
    return rows


def render_doc(*, rows: list[CounterfactualRow]) -> str:
    lines = []
    lines.append("# Exit Counterfactual Analysis — 280 Broker Trades")
    lines.append("")
    lines.append("**Generated:** by `scripts/analyze_exit_counterfactuals.py` against `data/broker_truth/closed_trades.parquet` + `mx-arena/data/historical/{ticker}/{date}.parquet` bars.")
    lines.append("")
    lines.append("**Goal:** for each broker trade, compute what the P&L WOULD have been under alternative exit policies (T+60s, T+5min, T+15min, T+30min, EOD, T+1, T+5). Plus MFE / MAE within the actual hold window. Identifies whether the strategy's EXITS are the bug, not the ENTRIES.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Summary aggregates
    valid = [r for r in rows if r.bars_loaded]
    if not valid:
        lines.append("**No trades had loadable bars. Cannot compute counterfactuals.**")
        return "\n".join(lines)

    lines.append(f"## §1 — Sample size")
    lines.append(f"- Total broker trades: {len(rows)}")
    lines.append(f"- With loadable bars (counterfactuals computed): {len(valid)}")
    lines.append(f"- Without bars (skipped): {len(rows) - len(valid)}")
    lines.append("")

    # Aggregate P&L per exit policy
    actual = sum(r.actual_pnl for r in valid)
    policies = [
        ("ACTUAL", actual),
        ("T+60s", sum(r.pnl_t60s for r in valid if r.px_t60s > 0)),
        ("T+5min", sum(r.pnl_t5min for r in valid if r.px_t5min > 0)),
        ("T+15min", sum(r.pnl_t15min for r in valid if r.px_t15min > 0)),
        ("T+30min", sum(r.pnl_t30min for r in valid if r.px_t30min > 0)),
        ("EOD", sum(r.pnl_eod for r in valid if r.px_eod > 0)),
        ("T+1 open", sum(r.pnl_t1 for r in valid if r.px_t1 > 0)),
        ("T+5 close", sum(r.pnl_t5 for r in valid if r.px_t5 > 0)),
    ]
    lines.append("## §2 — Aggregate P&L by counterfactual exit policy")
    lines.append("")
    lines.append("| Exit policy | Σ P&L (across valid trades) | vs ACTUAL |")
    lines.append("|---|---:|---:|")
    for name, pnl in policies:
        delta = pnl - actual if name != "ACTUAL" else 0
        lines.append(f"| {name} | ${pnl:+,.2f} | ${delta:+,.2f} |")
    lines.append("")

    # MFE / MAE distribution
    mfes_pct = [r.mfe_pct for r in valid if r.entry_price > 0]
    maes_pct = [r.mae_pct for r in valid if r.entry_price > 0]
    if mfes_pct:
        mfe_median = statistics.median(mfes_pct)
        mfe_p75 = sorted(mfes_pct)[int(len(mfes_pct) * 0.75)]
        mfe_p90 = sorted(mfes_pct)[int(len(mfes_pct) * 0.9)]
        mae_median = statistics.median(maes_pct)
        mae_p25 = sorted(maes_pct)[int(len(maes_pct) * 0.25)]
        mae_p10 = sorted(maes_pct)[int(len(maes_pct) * 0.1)]
        lines.append("## §3 — MFE / MAE distribution (within actual hold window)")
        lines.append("")
        lines.append(f"- **Max Favorable Excursion (% from entry)**: median {mfe_median:+.2f}%, p75 {mfe_p75:+.2f}%, p90 {mfe_p90:+.2f}%")
        lines.append(f"- **Max Adverse Excursion (% from entry)**: median {mae_median:+.2f}%, p25 {mae_p25:+.2f}%, p10 {mae_p10:+.2f}%")
        lines.append("")
        # Capture ratio: how much of MFE did the actual exit capture?
        capture_ratios = []
        for r in valid:
            if r.entry_price > 0 and r.mfe_dollars > 1:
                actual_capture = r.actual_pnl / r.mfe_dollars
                capture_ratios.append(actual_capture)
        if capture_ratios:
            cr_median = statistics.median(capture_ratios)
            lines.append(f"- **MFE capture ratio (actual P&L / MFE $)**: median {cr_median*100:+.1f}% — i.e., median trade captured {cr_median*100:.0f}% of its peak favorable excursion")
            cr_neg = sum(1 for c in capture_ratios if c < 0)
            cr_low = sum(1 for c in capture_ratios if 0 <= c < 0.25)
            cr_mid = sum(1 for c in capture_ratios if 0.25 <= c < 0.75)
            cr_high = sum(1 for c in capture_ratios if c >= 0.75)
            lines.append(f"- Capture distribution: {cr_neg} trades NEGATIVE (had MFE but exited at loss), {cr_low} captured <25%, {cr_mid} captured 25-75%, {cr_high} captured >75%")
        lines.append("")

    # Win rate per policy
    lines.append("## §4 — Win rate per exit policy (n=trades P&L > 0)")
    lines.append("")
    lines.append("| Exit policy | Wins | Total | Win-rate |")
    lines.append("|---|---:|---:|---:|")
    for name, attr in [("ACTUAL", "actual_pnl"), ("T+60s", "pnl_t60s"),
                       ("T+5min", "pnl_t5min"), ("T+15min", "pnl_t15min"),
                       ("T+30min", "pnl_t30min"), ("EOD", "pnl_eod"),
                       ("T+1 open", "pnl_t1"), ("T+5 close", "pnl_t5")]:
        # For non-actual policies, only count trades where the counterfactual price was loaded
        valid_for_policy = [r for r in valid if attr == "actual_pnl" or getattr(r, attr.replace("pnl", "px"), 0) > 0]
        if not valid_for_policy:
            lines.append(f"| {name} | n/a | n/a | n/a |")
            continue
        wins = sum(1 for r in valid_for_policy if getattr(r, attr) > 0)
        wr = wins / len(valid_for_policy) * 100
        lines.append(f"| {name} | {wins} | {len(valid_for_policy)} | {wr:.1f}% |")
    lines.append("")

    # Top "missed gains" — trades where MFE >> actual P&L
    lines.append("## §5 — Top 10 missed gains (MFE - actual_pnl, biggest gaps)")
    lines.append("")
    lines.append("Trades where the price reached significant favorable excursion during hold but actual exit didn't capture it.")
    lines.append("")
    missed = sorted(valid, key=lambda r: -(r.mfe_dollars - r.actual_pnl))[:10]
    lines.append("| Ticker | Date | Entry | Actual exit | MFE $ | Actual P&L | Missed |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for r in missed:
        sd = r.entry_ts[:10] if r.entry_ts else "?"
        lines.append(
            f"| {r.symbol} | {sd} | ${r.entry_price:.4f} | ${r.actual_exit_price:.4f} | "
            f"${r.mfe_dollars:+,.2f} | ${r.actual_pnl:+,.2f} | "
            f"${r.mfe_dollars - r.actual_pnl:+,.2f} |"
        )
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    p.add_argument("--output-doc", type=Path, default=DEFAULT_OUT_DOC)
    p.add_argument("--output-data", type=Path, default=DEFAULT_OUT_DATA)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    import pandas as pd
    df = pd.read_parquet(args.input)
    trades = df.to_dict("records")
    logger.info("loaded %d closed trades", len(trades))

    rows = compute_counterfactuals(trades=trades)

    args.output_data.parent.mkdir(parents=True, exist_ok=True)
    out_df = pd.DataFrame([asdict(r) for r in rows])
    out_df.to_parquet(args.output_data, index=False)
    logger.info("wrote %s (%d rows)", args.output_data, len(rows))

    md = render_doc(rows=rows)
    args.output_doc.parent.mkdir(parents=True, exist_ok=True)
    args.output_doc.write_text(md, encoding="utf-8")
    logger.info("wrote %s", args.output_doc)

    return 0


if __name__ == "__main__":
    sys.exit(main())
