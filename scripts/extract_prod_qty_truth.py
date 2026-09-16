"""Extract authoritative production qty + entry-price per trade.

Block B.1 of the rig forensics. Per docs/replay_diffs/
2026-04-28_error_decomposition.md, the dominant arena ↔ prod
divergence is qty multiplier error (5.7-16.7× too big), not fill-price.

Source priority (most-authoritative first):
  1. data/instrumentation/trade_context/session_date=<date>/orders.parquet
     — Bug AO orchestrator-hook coverage. 4/28 has it; 4/24 and 4/27
     do not.
  2. logs/momentum_<date>.log D217 fill events:
     "order <oid> reached terminal status=filled filled_qty=<N>
      filled_avg_price=<P>"
  3. logs/momentum_<date>.log tranche fill events for partial-fill
     trades (e.g. OGN 4/27 had T1+T2 tranche limits BEFORE BAR-1):
     "TRANCHE T<i> FILLED: <ticker> @ $<P>"

Output:
    data/replay/prod_qty_truth.parquet
    Columns: ticker, session_date, entry_ts (ISO), prod_qty,
             prod_entry_avg_px, source, notes

Used by scripts/arena_replay_session.py to size arena's replayed
trades correctly. Falls back to tier-1 sizing default ONLY when no
authoritative source exists.

Discipline: this script never WRITES to trade_results.jsonl
(production data should not be retroactively rewritten). It produces
a separate side-table that the replay rig joins against.
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

logger = logging.getLogger("extract_qty_truth")

REPO_ROOT = Path(__file__).resolve().parent.parent
TRADE_CONTEXT_DIR = REPO_ROOT / "data" / "instrumentation" / "trade_context"
LOGS_DIR = REPO_ROOT / "logs"
TRADE_RESULTS = REPO_ROOT / "data" / "trade_results.jsonl"
DEFAULT_OUT = REPO_ROOT / "data" / "replay" / "prod_qty_truth.parquet"
POLYGON_TICK_ROOT = REPO_ROOT / "data" / "polygon_backfill" / "tick_data"


def _polygon_day_range(ticker: str, session_date: str) -> tuple[float, float] | None:
    """Return (min_px, max_px) of all Polygon trades for (ticker, date),
    or None if tick data isn't available (then we can't sanity-check).

    Bug AS / Bug AT-1 defense (2026-04-29): used to flag truth-corpus
    rows whose recorded `prod_entry_avg_px` is OUTSIDE the day's actual
    price range — meaning the price was never traded.
    """
    p = POLYGON_TICK_ROOT / session_date / f"{ticker}_trades.parquet"
    if not p.exists():
        return None
    try:
        import pandas as pd
        df = pd.read_parquet(p, columns=["price"])
    except Exception:
        return None
    if df.empty:
        return None
    return (float(df["price"].min()), float(df["price"].max()))


@dataclass
class QtyTruth:
    ticker: str
    session_date: str
    entry_ts: str
    prod_qty: int
    prod_entry_avg_px: float
    source: str           # trade_context | d217_log | tranche_log | tier1_default
    notes: str = ""
    # Bug AS / Bug AT-1 (2026-04-29): Polygon sanity check. When True,
    # this row's `prod_entry_avg_px` is OUTSIDE the day's price range per
    # Polygon SIP ticks (i.e. that price never traded). Most likely cause:
    # FAST_PATH recorder logged the LIMIT price as the fill price for an
    # order that was never actually filled (Bug AT-1, see docs/research-log/80).
    # Calibration / replay should EXCLUDE rows with this flag set.
    data_quality_outlier: bool = False
    # Bug AU resolution (2026-04-30, doc 85, PROMPT_08 §6.1): side-aware
    # corpus schema. Broker is canonical for side + qty when broker_truth
    # is available. Journal values preserved as forensic fields.
    side: str = "long_legacy"          # "long" | "short" | "long_legacy"
    broker_qty: int = 0                 # 0 if broker_truth missing
    journal_qty: int = 0                # forensic — what the journal recorded
    qty_drift_pct: float = float("nan") # NaN if broker_truth missing
    n_legs: int = 0                     # broker fill events on the matched side
    data_integrity_flag: str = ""       # "" | side_mismatch_AU | qty_mismatch_AU
                                        #    | side_ambiguous_AU | missing_broker_truth
    # Canonical values: broker wins for qty + side when available;
    # journal otherwise. Downstream consumers should use these.
    canonical_qty: int = 0
    canonical_entry_avg_px: float = 0.0


# ── Source 1: trade_context parquet ────────────────────────────────


def lookup_in_trade_context(*, ticker: str, session_date: str, entry_ts: datetime) -> QtyTruth | None:
    pq = TRADE_CONTEXT_DIR / f"session_date={session_date}" / "orders.parquet"
    if not pq.exists():
        return None
    try:
        import pandas as pd
        df = pd.read_parquet(pq)
    except Exception as e:
        logger.warning("could not read %s: %s", pq, e)
        return None

    # Match: ticker + side=buy + filled + submit_ts within ±5 min of entry_ts
    target = entry_ts.astimezone(timezone.utc)
    candidates = df[
        (df["ticker"] == ticker)
        & (df["side"] == "buy")
        & (df["terminal_status"] == "filled")
    ].copy()
    if candidates.empty:
        return None

    candidates["submit_dt"] = pd.to_datetime(candidates["submit_ts"], utc=True)
    candidates["delta_sec"] = (candidates["submit_dt"] - target).abs().dt.total_seconds()
    nearest = candidates.loc[candidates["delta_sec"].idxmin()]
    if nearest["delta_sec"] > 300:  # 5-min window
        return None

    return QtyTruth(
        ticker=ticker,
        session_date=session_date,
        entry_ts=entry_ts.isoformat(),
        prod_qty=int(nearest["terminal_filled_qty"]),
        prod_entry_avg_px=float(nearest["requested_px"]),  # closest to actual fill
        source="trade_context",
        notes=f"submit_ts={nearest['submit_ts']} delta={nearest['delta_sec']:.0f}s",
    )


# ── Source 2: D217 fill log ────────────────────────────────────────


_D217_FILL_RE = re.compile(
    r'(\d{2}:\d{2}:\d{2})\s.*D217:\s*(\w+)\s+order\s+\w+\s+reached\s+terminal\s+status=filled\s+filled_qty=(\d+)\s+filled_avg_price=([\d.]+)',
)

# D215 EXECUTION RECORDED is the FAST_PATH equivalent — OTO fills don't
# go through D217 polling, so OGN-class trades are only visible here.
# Format: "D215 EXECUTION RECORDED: BUY OGN long qty=999 fill=$11.25 ..."
_D215_FILL_RE = re.compile(
    r'(\d{2}:\d{2}:\d{2})\s.*D215\s+EXECUTION RECORDED:\s+BUY\s+(\w+)\s+\S+\s+qty=(\d+)\s+fill=\$([\d.]+)',
)


def lookup_in_d217_log(*, ticker: str, session_date: str, entry_ts: datetime) -> QtyTruth | None:
    log = LOGS_DIR / f"momentum_{session_date}.log"
    if not log.exists():
        return None
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    from zoneinfo import ZoneInfo
    entry_et = entry_ts.astimezone(ZoneInfo("America/New_York"))
    entry_clock = entry_et.strftime("%H:%M:%S")

    # Find all D217 OR D215 fills for this ticker within ±5 min
    candidates: list[tuple[str, int, float, str]] = []
    for line in text.split("\n"):
        m = _D217_FILL_RE.search(line)
        if m and m.group(2) == ticker:
            candidates.append((m.group(1), int(m.group(3)), float(m.group(4)), "d217"))
            continue
        m = _D215_FILL_RE.search(line)
        if m and m.group(2) == ticker:
            candidates.append((m.group(1), int(m.group(3)), float(m.group(4)), "d215"))

    if not candidates:
        return None

    # Pick the fill closest to entry_clock
    def _clock_diff_sec(a: str, b: str) -> int:
        ah, am, as_ = map(int, a.split(":"))
        bh, bm, bs = map(int, b.split(":"))
        return abs((ah - bh) * 3600 + (am - bm) * 60 + (as_ - bs))

    best = min(candidates, key=lambda c: _clock_diff_sec(c[0], entry_clock))
    if _clock_diff_sec(best[0], entry_clock) > 600:  # 10-min window — D215 may log slightly off
        return None

    return QtyTruth(
        ticker=ticker,
        session_date=session_date,
        entry_ts=entry_ts.isoformat(),
        prod_qty=best[1],
        prod_entry_avg_px=best[2],
        source=f"{best[3]}_log",
        notes=f"fill_clock_et={best[0]}",
    )


# ── Main: enumerate trades and build truth table ───────────────────


def load_trades_to_resolve() -> list[dict]:
    """All non-carry, non-pre-market trades we need qty for."""
    out = []
    with TRADE_RESULTS.open("r", encoding="utf-8") as f:
        for line in f:
            j = json.loads(line)
            entry = datetime.fromisoformat(j["entry_time"])
            exit_ = datetime.fromisoformat(j["exit_time"])
            if entry.date() < exit_.date():
                continue  # carry — out of scope
            et_cutoff = datetime.fromisoformat("2000-01-01T13:00:00+00:00").time()
            if entry.astimezone(timezone.utc).time() < et_cutoff:
                continue  # pre-market snapshot
            out.append({
                "ticker": j["ticker"],
                "session_date": j.get("session_date", ""),
                "entry_time": entry,
                "exit_time": exit_,
                "pnl": float(j["pnl"]),
            })
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    trades = load_trades_to_resolve()
    logger.info("resolving qty truth for %d trades", len(trades))

    truths: list[QtyTruth] = []
    misses: list[dict] = []
    for t in trades:
        result = lookup_in_trade_context(
            ticker=t["ticker"], session_date=t["session_date"], entry_ts=t["entry_time"],
        )
        if result is None:
            result = lookup_in_d217_log(
                ticker=t["ticker"], session_date=t["session_date"], entry_ts=t["entry_time"],
            )
        if result is None:
            misses.append(t)
            logger.warning(
                "no qty truth source for %s %s @ %s",
                t["ticker"], t["session_date"], t["entry_time"].isoformat(),
            )
            continue
        # Polygon sanity check (Bug AS defense). If the recorded fill
        # price is outside the day's actual trade range, flag as
        # data_quality_outlier so downstream calibration/replay excludes it.
        rng = _polygon_day_range(result.ticker, result.session_date)
        if rng is not None:
            day_min, day_max = rng
            if not (day_min - 1e-9 <= result.prod_entry_avg_px <= day_max + 1e-9):
                result.data_quality_outlier = True
                if result.notes:
                    result.notes += " | "
                result.notes += (
                    f"BUG_AS: recorded ${result.prod_entry_avg_px} OUTSIDE "
                    f"polygon day range [${day_min:.4f},${day_max:.4f}] - "
                    f"likely Bug AT-1 (FAST_PATH limit-as-fill)"
                )
                logger.warning(
                    "  %s %s data_quality_outlier=True (recorded $%.4f outside "
                    "day range [$%.4f, $%.4f])",
                    result.ticker, result.session_date,
                    result.prod_entry_avg_px, day_min, day_max,
                )

        # Bug AU resolution (2026-04-30, doc 85, PROMPT_08 Block 3+5):
        # Reconcile against broker_truth/activities.parquet.
        # Broker is canonical for side + qty when available; journal
        # preserved as forensic. Adds: side, broker_qty, journal_qty,
        # qty_drift_pct, n_legs, data_integrity_flag, canonical_qty,
        # canonical_entry_avg_px.
        try:
            from src.analysis.corpus_reconciliation import (
                reconcile_one_row, load_broker_fills_for,
            )
            global _broker_activities_cache
            if "_broker_activities_cache" not in globals():
                bt_path = REPO_ROOT / "data" / "broker_truth" / "activities.parquet"
                if bt_path.exists():
                    import pandas as pd
                    _broker_activities_cache = pd.read_parquet(bt_path)
                else:
                    _broker_activities_cache = None
            if _broker_activities_cache is not None:
                fills = load_broker_fills_for(
                    _broker_activities_cache, result.ticker, result.session_date,
                )
                rec = reconcile_one_row(
                    ticker=result.ticker, session_date=result.session_date,
                    journal_qty=result.prod_qty,
                    journal_entry_avg_px=result.prod_entry_avg_px,
                    broker_fills=fills,
                )
                result.side = rec.side
                result.broker_qty = rec.broker_qty
                result.journal_qty = rec.journal_qty
                import math as _math
                result.qty_drift_pct = (
                    rec.qty_drift_pct if not _math.isnan(rec.qty_drift_pct) else float("nan")
                )
                result.n_legs = rec.n_legs
                result.data_integrity_flag = rec.data_integrity_flag or ""
                result.canonical_qty = rec.canonical_qty
                result.canonical_entry_avg_px = rec.canonical_entry_avg_px
            else:
                result.journal_qty = result.prod_qty
                result.canonical_qty = result.prod_qty
                result.canonical_entry_avg_px = result.prod_entry_avg_px
                result.data_integrity_flag = "missing_broker_truth"
        except Exception as e:
            logger.warning("Bug AU reconciliation failed for %s: %s", result.ticker, e)
            # Fall back: preserve original journal values.
            result.journal_qty = result.prod_qty
            result.canonical_qty = result.prod_qty
            result.canonical_entry_avg_px = result.prod_entry_avg_px

        truths.append(result)
        flag_marker = " ⚠ DQ_OUTLIER" if result.data_quality_outlier else ""
        au_marker = (
            f" 🚨 {result.data_integrity_flag}"
            if result.data_integrity_flag and result.data_integrity_flag != "missing_broker_truth"
            else ""
        )
        logger.info(
            "  %s %s qty=%d @ $%.4f side=%s broker_qty=%d source=%s%s%s",
            result.ticker, result.session_date,
            result.canonical_qty, result.canonical_entry_avg_px,
            result.side, result.broker_qty, result.source,
            flag_marker, au_marker,
        )

    if not truths:
        logger.error("zero qty truths resolved — nothing to write")
        return 1

    try:
        import pandas as pd
    except ImportError:
        logger.error("pandas required")
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([asdict(t) for t in truths])
    df.to_parquet(args.output, index=False)
    logger.info("wrote %d truths to %s (%d misses)", len(truths), args.output, len(misses))
    return 0


if __name__ == "__main__":
    sys.exit(main())
