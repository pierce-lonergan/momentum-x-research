"""Extract authoritative exit fills/PnLs per prod trade.

Block A.1 of the H2 prod-mirror exit semantics fix. See plan doc 58
and `docs/replay_diffs/2026-04-28_error_decomposition.md` (H2 issue).

Source priority (most-authoritative first):
  1. D76 CLOSED log line: "D76 CLOSED: <ticker> qty=<N> @ $<exit_px>
     (entry=$<E>, pnl=$<P>) [<DIR>]" — has exit price directly.
  2. Bridge attribution close: "<ticker>: Closed with attribution →
     PnL=$<P> (<qty> shares), MFCS=..." — has realized PnL + qty;
     entry_px comes from extract_prod_qty_truth.py's output, so we
     back out exit_px = entry_px + (pnl / qty).
  3. Tranche fill events: "TRANCHE T<i> FILLED: <ticker> @ $<px> ...
     Remaining: <r>" — useful for OGN-class multi-tranche exits;
     captured but not used as primary source (the bridge attribution
     line aggregates them into a final realized PnL).
  4. trade_results.jsonl prod_pnl as ultimate fallback — computed
     synthetic exit_px from prod_qty + prod_entry_avg + prod_pnl.

The "prod-mirror" approach: arena does NOT model tranche limits or
exit-policy mechanics — it READS what prod actually realized. This
is deliberately tautological for the "did the rig wire correctly"
question and is the right scope for Block A. Forward-looking sweeps
(Block E) require MODELED exits and won't use this truth source.

Output:
    data/replay/prod_exit_truth.parquet
    Columns: ticker, session_date, entry_ts, prod_qty, prod_pnl,
             prod_entry_avg_px, prod_exit_avg_px, exit_path, source,
             notes
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

logger = logging.getLogger("extract_exit_truth")

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = REPO_ROOT / "logs"
TRADE_RESULTS = REPO_ROOT / "data" / "trade_results.jsonl"
QTY_TRUTH = REPO_ROOT / "data" / "replay" / "prod_qty_truth.parquet"
DEFAULT_OUT = REPO_ROOT / "data" / "replay" / "prod_exit_truth.parquet"


@dataclass
class ExitTruth:
    ticker: str
    session_date: str
    entry_ts: str
    prod_qty: int
    prod_pnl: float
    prod_entry_avg_px: float
    prod_exit_avg_px: float
    exit_path: str           # d76_closed | bridge_attribution | tranche_aggregate | computed_from_pnl
    source: str
    notes: str = ""


# ── Log extractors ────────────────────────────────────────────────


# "D76 CLOSED: LIDR qty=5264 @ $2.15 (entry=$2.42, pnl=$-1447.60) [LONG]"
_D76_RE = re.compile(
    r'(\d{2}:\d{2}:\d{2}).*D76 CLOSED:\s+(\w+)\s+qty=(\d+)\s+@\s+\$([\d.]+)\s+\(entry=\$([\d.]+),\s+pnl=\$(-?[\d.]+)\)',
)

# "SBLX: Closed with attribution → PnL=$-157.75 (3490 shares), MFCS=..."
# (rendering: arrow appears as varied unicode in different encodings)
_BRIDGE_ATTR_RE = re.compile(
    r'(\d{2}:\d{2}:\d{2})\s.*?(\w+):\s+Closed\s+with\s+attribution\s+\S+\s+PnL=\$(-?[\d.]+)\s+\((\d+)\s+shares\)',
)


def scan_log_for_exits(log_path: Path) -> list[dict]:
    """Return all exit events found in a momentum log."""
    if not log_path.exists():
        return []
    text = log_path.read_text(encoding="utf-8", errors="replace")
    events: list[dict] = []
    for line in text.split("\n"):
        m = _D76_RE.search(line)
        if m:
            events.append({
                "kind": "d76_closed",
                "clock_et": m.group(1),
                "ticker": m.group(2),
                "qty": int(m.group(3)),
                "exit_px": float(m.group(4)),
                "entry_px": float(m.group(5)),
                "pnl": float(m.group(6)),
            })
            continue
        m = _BRIDGE_ATTR_RE.search(line)
        if m:
            events.append({
                "kind": "bridge_attribution",
                "clock_et": m.group(1),
                "ticker": m.group(2),
                "pnl": float(m.group(3)),
                "qty": int(m.group(4)),
            })
    return events


# ── Resolve one trade ─────────────────────────────────────────────


def _clock_diff_sec(a: str, b: str) -> int:
    ah, am, as_ = map(int, a.split(":"))
    bh, bm, bs = map(int, b.split(":"))
    return abs((ah - bh) * 3600 + (am - bm) * 60 + (as_ - bs))


def resolve_exit(*, ticker: str, session_date: str, entry_ts: datetime,
                 exit_ts: datetime, prod_pnl: float,
                 qty_truth_lookup: dict) -> ExitTruth | None:
    """Resolve exit for one trade. exit_ts is from trade_results.jsonl."""
    from zoneinfo import ZoneInfo
    log = LOGS_DIR / f"momentum_{session_date}.log"
    events = scan_log_for_exits(log)
    if not events:
        return _fallback_from_qty_truth(
            ticker=ticker, session_date=session_date, entry_ts=entry_ts,
            prod_pnl=prod_pnl, qty_truth_lookup=qty_truth_lookup,
            notes="no exit events in log",
        )

    exit_et = exit_ts.astimezone(ZoneInfo("America/New_York"))
    exit_clock = exit_et.strftime("%H:%M:%S")

    # Filter to this ticker, sorted by proximity to recorded exit
    candidates = [e for e in events if e["ticker"] == ticker]
    if not candidates:
        return _fallback_from_qty_truth(
            ticker=ticker, session_date=session_date, entry_ts=entry_ts,
            prod_pnl=prod_pnl, qty_truth_lookup=qty_truth_lookup,
            notes="ticker not found in exit events",
        )
    candidates.sort(key=lambda e: _clock_diff_sec(e["clock_et"], exit_clock))
    best = candidates[0]
    if _clock_diff_sec(best["clock_et"], exit_clock) > 600:  # 10 min window
        return _fallback_from_qty_truth(
            ticker=ticker, session_date=session_date, entry_ts=entry_ts,
            prod_pnl=prod_pnl, qty_truth_lookup=qty_truth_lookup,
            notes=f"closest exit event {best['clock_et']} >10 min from {exit_clock}",
        )

    # Pull entry_px from qty_truth if we have it
    qt = qty_truth_lookup.get((ticker, session_date, entry_ts.isoformat()))
    entry_px_from_truth = qt["prod_entry_avg_px"] if qt else 0.0

    if best["kind"] == "d76_closed":
        return ExitTruth(
            ticker=ticker, session_date=session_date,
            entry_ts=entry_ts.isoformat(),
            prod_qty=best["qty"], prod_pnl=best["pnl"],
            prod_entry_avg_px=best["entry_px"],
            prod_exit_avg_px=best["exit_px"],
            exit_path="d76_closed", source="d76_closed_log",
            notes=f"clock_et={best['clock_et']}",
        )

    # bridge_attribution: back out exit_px from PnL + qty + entry_px
    qty = best["qty"]
    pnl = best["pnl"]
    if entry_px_from_truth > 0 and qty > 0:
        exit_px = entry_px_from_truth + (pnl / qty)
        return ExitTruth(
            ticker=ticker, session_date=session_date,
            entry_ts=entry_ts.isoformat(),
            prod_qty=qty, prod_pnl=pnl,
            prod_entry_avg_px=entry_px_from_truth,
            prod_exit_avg_px=round(exit_px, 4),
            exit_path="bridge_attribution",
            source="bridge_attribution_log",
            notes=f"clock_et={best['clock_et']}; exit_px back-out from pnl/qty",
        )

    # No entry_px to back out from
    return _fallback_from_qty_truth(
        ticker=ticker, session_date=session_date, entry_ts=entry_ts,
        prod_pnl=prod_pnl, qty_truth_lookup=qty_truth_lookup,
        notes=f"bridge_attribution found but no entry_px in qty truth (clock_et={best['clock_et']})",
    )


def _fallback_from_qty_truth(*, ticker: str, session_date: str,
                              entry_ts: datetime, prod_pnl: float,
                              qty_truth_lookup: dict, notes: str) -> ExitTruth | None:
    """Synthesize exit truth from qty_truth + trade_results pnl when no
    explicit exit log line is found."""
    qt = qty_truth_lookup.get((ticker, session_date, entry_ts.isoformat()))
    if qt is None:
        return None
    qty = qt["prod_qty"]
    entry_px = qt["prod_entry_avg_px"]
    if qty <= 0 or entry_px <= 0:
        return None
    exit_px = entry_px + (prod_pnl / qty)
    return ExitTruth(
        ticker=ticker, session_date=session_date,
        entry_ts=entry_ts.isoformat(),
        prod_qty=qty, prod_pnl=prod_pnl,
        prod_entry_avg_px=entry_px,
        prod_exit_avg_px=round(exit_px, 4),
        exit_path="computed_from_pnl",
        source="trade_results_pnl_fallback",
        notes=notes,
    )


# ── Main ──────────────────────────────────────────────────────────


def load_qty_truth() -> dict:
    if not QTY_TRUTH.exists():
        return {}
    try:
        import pandas as pd
        df = pd.read_parquet(QTY_TRUTH)
        return {
            (r["ticker"], r["session_date"], r["entry_ts"]): {
                "prod_qty": int(r["prod_qty"]),
                "prod_entry_avg_px": float(r["prod_entry_avg_px"]),
            }
            for _, r in df.iterrows()
        }
    except Exception:
        return {}


def load_in_scope_trades() -> list[dict]:
    out = []
    with TRADE_RESULTS.open("r", encoding="utf-8") as f:
        for line in f:
            j = json.loads(line)
            entry = datetime.fromisoformat(j["entry_time"])
            exit_ = datetime.fromisoformat(j["exit_time"])
            if entry.date() < exit_.date():
                continue
            et_cutoff = datetime.fromisoformat("2000-01-01T13:00:00+00:00").time()
            if entry.astimezone(timezone.utc).time() < et_cutoff:
                continue
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

    qty_truth_lookup = load_qty_truth()
    logger.info("loaded %d qty truth rows", len(qty_truth_lookup))

    trades = load_in_scope_trades()
    logger.info("resolving exit truth for %d in-scope trades", len(trades))

    truths: list[ExitTruth] = []
    misses: list[dict] = []
    for t in trades:
        result = resolve_exit(
            ticker=t["ticker"], session_date=t["session_date"],
            entry_ts=t["entry_time"], exit_ts=t["exit_time"],
            prod_pnl=t["pnl"], qty_truth_lookup=qty_truth_lookup,
        )
        if result is None:
            misses.append(t)
            logger.warning(
                "no exit truth source for %s %s @ %s",
                t["ticker"], t["session_date"], t["entry_time"].isoformat(),
            )
            continue
        truths.append(result)
        logger.info(
            "  %s %s exit_px=$%.4f path=%s source=%s",
            result.ticker, result.session_date,
            result.prod_exit_avg_px, result.exit_path, result.source,
        )

    if not truths:
        logger.error("zero exit truths resolved — nothing to write")
        return 1

    try:
        import pandas as pd
    except ImportError:
        logger.error("pandas required")
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([asdict(t) for t in truths])
    df.to_parquet(args.output, index=False)
    logger.info("wrote %d exit truths to %s (%d misses)",
                len(truths), args.output, len(misses))
    return 0


if __name__ == "__main__":
    sys.exit(main())
