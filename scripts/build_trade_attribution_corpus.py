"""Backfill a trade-attribution corpus from prod logs (Bug AO MVP).

Block C of the Block 4.4 unblock work. See plan doc 58 §7 and
docs/research-log/63_bug_ao_orchestrator_hook.md.

CONTEXT — why backfill, not a forward-going hook:
  The brief's stop condition: "if hook write-path requires invasive
  changes (>100 LOC across multiple modules), STOP. Ship a minimum
  scope (e.g. hook only at submission, terminal fields backfilled
  from logs at session-end)."

  The existing TradeContextRow is frozen+extra=forbid; adding fields
  for catalyst_type/news_signal/exit_codepath would require a v2
  schema migration + writer changes + 2-3 emit-site changes.
  Estimated ~150 LOC across 4 modules — over the stop threshold.

  This script ships the SIDECAR-BACKFILL approach: a new
  trade_attribution_corpus parquet, joinable to trade_context by
  order_id where the order_id exists, otherwise keyed by
  (date, ticker, entry_ts). All data extracted from existing log
  lines. Forward-going hook (writing this sidecar live during prod)
  deferred to next session.

OUTPUT:
    data/instrumentation/trade_attribution/session_date=<date>/attribution.parquet
    Schema:
      session_date           — YYYY-MM-DD
      ticker                 — UPPERCASE
      entry_ts               — ISO 8601 UTC
      order_id               — Alpaca order ID (when extractable)
      prod_qty               — actual fill qty
      prod_entry_avg_px      — actual fill price
      prod_exit_avg_px       — actual exit price (D76/bridge/computed)
      prod_pnl               — realized P&L (from trade_results.jsonl)
      catalyst_type          — earnings_beat / fda / unknown / etc.
      news_signal            — STRONG_BULL/BULL/NEUTRAL/STRONG_BEAR/BEAR/NO_SIGNAL
      news_conf              — [0.0, 1.0]
      exit_codepath          — d146_bar1 / d245_smart / tranche / d76_close / unknown
      data_completeness      — full / partial / minimal
      backfill_source        — logs_v0.1

This is the data Block 4.4 needs to (a) size trades correctly across
86 sessions and (b) stratify outcomes by catalyst classification.
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

logger = logging.getLogger("build_trade_attribution")

REPO_ROOT = Path(__file__).resolve().parent.parent
TRADE_RESULTS = REPO_ROOT / "data" / "trade_results.jsonl"
LOGS_DIR = REPO_ROOT / "logs"
QTY_TRUTH = REPO_ROOT / "data" / "replay" / "prod_qty_truth.parquet"
EXIT_TRUTH = REPO_ROOT / "data" / "replay" / "prod_exit_truth.parquet"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "instrumentation" / "trade_attribution"


@dataclass
class AttributionRow:
    session_date: str
    ticker: str
    entry_ts: str
    order_id: str
    prod_qty: int
    prod_entry_avg_px: float
    prod_exit_avg_px: float
    prod_pnl: float
    catalyst_type: str
    news_signal: str
    news_conf: float
    exit_codepath: str
    data_completeness: str        # full | partial | minimal
    backfill_source: str = "logs_v0.1"


# ── Log scrapers ──────────────────────────────────────────────────


_RE_ENS = re.compile(
    r'(\d{2}:\d{2}:\d{2}).*?D202 ENSEMBLE news_agent (\w+):.*?(NEUTRAL|STRONG_BULL|STRONG_BEAR|BULL|BEAR).*?conf=([\d\.]+)',
)
_RE_BAR1 = re.compile(r'D146 BAR-?1 EXIT:\s+(\w+)')
_RE_D245 = re.compile(r'D245 SMART_EXIT_REJECTED\s+(\w+)')
_RE_D76 = re.compile(r'D76 CLOSED:\s+(\w+)')
_RE_TRANCHE = re.compile(r'TRANCHE T\d+ FILLED:\s+(\w+)')
_RE_D215_ORDER = re.compile(
    r'D85 SUBMITTED:\s+(\w+).*?oid=(\S+)',
)


def _scrape_news_signal_at_entry(text: str, ticker: str, entry_clock_et: str) -> tuple[str, float]:
    """Return (signal, conf) for the most-recent ensemble call BEFORE entry_clock_et."""
    sigs: list[tuple[str, str, float]] = []
    for line in text.split("\n"):
        if "news_agent" not in line or ticker not in line:
            continue
        m = _RE_ENS.search(line)
        if m and m.group(2) == ticker:
            sigs.append((m.group(1), m.group(3), float(m.group(4))))
    at_or_before = [s for s in sigs if s[0] <= entry_clock_et]
    if at_or_before:
        return (at_or_before[-1][1], at_or_before[-1][2])
    if sigs:
        return (sigs[0][1], sigs[0][2])
    return ("NO_SIGNAL", 0.0)


def _scrape_exit_codepath(text: str, ticker: str) -> str:
    """Determine which exit code path fired for this ticker. Returns
    the FIRST matching path found in the log (chronological order)."""
    candidates: list[tuple[str, str]] = []  # (clock, codepath)
    for line in text.split("\n"):
        m = _RE_BAR1.search(line)
        if m and m.group(1) == ticker:
            cm = re.search(r'(\d{2}:\d{2}:\d{2})', line)
            if cm:
                candidates.append((cm.group(1), "d146_bar1"))
            continue
        m = _RE_TRANCHE.search(line)
        if m and m.group(1) == ticker:
            cm = re.search(r'(\d{2}:\d{2}:\d{2})', line)
            if cm:
                candidates.append((cm.group(1), "tranche"))
            continue
        m = _RE_D76.search(line)
        if m and m.group(1) == ticker:
            cm = re.search(r'(\d{2}:\d{2}:\d{2})', line)
            if cm:
                candidates.append((cm.group(1), "d76_close"))
            continue
        m = _RE_D245.search(line)
        if m and m.group(1) == ticker:
            cm = re.search(r'(\d{2}:\d{2}:\d{2})', line)
            if cm:
                candidates.append((cm.group(1), "d245_smart"))
    if not candidates:
        return "unknown"
    candidates.sort()
    return candidates[0][1]  # first (chronologically) wins


def _scrape_order_id(text: str, ticker: str, entry_clock_et: str) -> str:
    """Pull D85 SUBMITTED's oid for a ticker around entry_clock_et."""
    candidates: list[tuple[str, str]] = []
    for line in text.split("\n"):
        m = _RE_D215_ORDER.search(line)
        if not m or m.group(1) != ticker:
            continue
        cm = re.search(r'(\d{2}:\d{2}:\d{2})', line)
        if cm:
            candidates.append((cm.group(1), m.group(2)))
    if not candidates:
        return ""
    # Pick the entry closest to entry_clock_et
    def _diff(a: str, b: str) -> int:
        ah, am, as_ = map(int, a.split(":"))
        bh, bm, bs = map(int, b.split(":"))
        return abs((ah - bh) * 3600 + (am - bm) * 60 + (as_ - bs))
    return min(candidates, key=lambda c: _diff(c[0], entry_clock_et))[1]


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


def load_exit_truth() -> dict:
    if not EXIT_TRUTH.exists():
        return {}
    try:
        import pandas as pd
        df = pd.read_parquet(EXIT_TRUTH)
        return {
            (r["ticker"], r["session_date"], r["entry_ts"]): {
                "prod_exit_avg_px": float(r["prod_exit_avg_px"]),
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
            out.append({
                "ticker": j["ticker"],
                "session_date": j.get("session_date", ""),
                "entry_time": entry,
                "exit_time": exit_,
                "pnl": float(j["pnl"]),
                "is_carry": entry.date() < exit_.date(),
            })
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
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from zoneinfo import ZoneInfo
    qty_truth = load_qty_truth()
    exit_truth = load_exit_truth()
    trades = load_in_scope_trades()
    logger.info(
        "building attribution corpus: %d trades | %d qty truths | %d exit truths",
        len(trades), len(qty_truth), len(exit_truth),
    )

    # Scan logs ONCE per session
    log_cache: dict[str, str] = {}

    rows_by_session: dict[str, list[AttributionRow]] = {}
    for t in trades:
        sd = t["session_date"]
        if sd not in log_cache:
            log_path = LOGS_DIR / f"momentum_{sd}.log"
            log_cache[sd] = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        text = log_cache[sd]

        entry_et = t["entry_time"].astimezone(ZoneInfo("America/New_York"))
        entry_clock = entry_et.strftime("%H:%M:%S")

        key = (t["ticker"], sd, t["entry_time"].isoformat())
        qt = qty_truth.get(key, {})
        et = exit_truth.get(key, {})

        sig, conf = _scrape_news_signal_at_entry(text, t["ticker"], entry_clock)
        codepath = _scrape_exit_codepath(text, t["ticker"])
        oid = _scrape_order_id(text, t["ticker"], entry_clock)

        # Catalyst type heuristic (MVP): conservative classification
        # from news signal — STRONG_BULL → likely earnings/M&A;
        # BULL → likely sector/news; NEUTRAL → no clear catalyst.
        # When the news_agent capture corpus is enabled (next session),
        # this gets replaced with the actual classification.
        if sig == "STRONG_BULL":
            catalyst = "high_conviction"
        elif sig == "BULL":
            catalyst = "moderate_conviction"
        elif sig in {"BEAR", "STRONG_BEAR"}:
            catalyst = "bearish"
        else:
            catalyst = "unknown"

        # Data completeness
        has_qty = bool(qt)
        has_exit = bool(et)
        has_signal = sig != "NO_SIGNAL"
        if has_qty and has_exit and has_signal:
            completeness = "full"
        elif has_qty or has_exit:
            completeness = "partial"
        else:
            completeness = "minimal"

        row = AttributionRow(
            session_date=sd,
            ticker=t["ticker"],
            entry_ts=t["entry_time"].isoformat(),
            order_id=oid,
            prod_qty=qt.get("prod_qty", 0),
            prod_entry_avg_px=qt.get("prod_entry_avg_px", 0.0),
            prod_exit_avg_px=et.get("prod_exit_avg_px", 0.0),
            prod_pnl=t["pnl"],
            catalyst_type=catalyst,
            news_signal=sig,
            news_conf=conf,
            exit_codepath=codepath,
            data_completeness=completeness,
        )
        rows_by_session.setdefault(sd, []).append(row)

    # Write per-session parquets
    try:
        import pandas as pd
    except ImportError:
        logger.error("pandas required")
        return 1

    n_total = 0
    n_full = 0
    for sd, rows in rows_by_session.items():
        out_dir = args.output_dir / f"session_date={sd}"
        out_dir.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame([asdict(r) for r in rows])
        target = out_dir / "attribution.parquet"
        df.to_parquet(target, index=False)
        n_full += sum(1 for r in rows if r.data_completeness == "full")
        n_total += len(rows)
        logger.info("  %s: %d rows → %s", sd, len(rows), target)

    logger.info(
        "DONE: %d total | %d full | %d partial+minimal",
        n_total, n_full, n_total - n_full,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
