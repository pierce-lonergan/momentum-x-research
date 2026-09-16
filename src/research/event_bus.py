"""doc 272 C2 — the canonical READ-ONLY event view over production's durable streams (DORMANT-C).

This module unifies the streams the live system already writes into ONE typed,
time-ordered iterator of `Event` records keyed on the correlation spine
(ticker / broker order_id / broker execution_id). It is a VIEW: it writes
nothing, holds no locks, and is never imported by production code.

Sources unified (per session date, all under <data_root>):

  kind="verdict_trace"  source="verdict_trace"   ops/verdict_trace_<date>.jsonl
      VLL lines: {"ts", "stage", "ticker", "reason", "mfcs", "path", ...}
      payload = the full line dict.

  kind="fill"           source="raw_fills"       ops/raw_fills_<date>.jsonl
      Durable websocket capture: {"captured_ts_utc", "raw": {"stream", "data"}}.
      Only raw.data.event in ("fill", "partial_fill") rows become events; the
      stream's "listening"/heartbeat rows are skipped. Synthetic self-test rows
      (AAPL/TSLA with null raw.data.timestamp) are excluded, mirroring
      scripts/ledger_shadow_recon.py. The view does NOT dedupe on execution_id
      — dedupe is consumer policy (see ledger_journal_drift).
      payload = {"event", "symbol", "side", "qty", "price", "order_status",
                 "position_qty", "timestamp", "captured_ts_utc"}.

  kind="ledger"         source="ledger_shadow"   ops/ledger_shadow.db (if present)
      sqlite `events` table (doc 270 B1 shadow ledger), rows whose
      substr(wall_ts,1,10) == date, in seq order. Opened read-only (mode=ro).
      payload = {"seq", "event_type", "side", "qty", "price", "fees"}.

  kind="incident"       source="incidents"       ops/incidents_<date>.jsonl
      Incident-bus lines: {"id", "kind", "severity", "ticker", "ts_utc",
      "session_date", "context", ...}. payload = the full line dict.

  kind="journal"        source="journal"         journals/journal_<date>_*.jsonl
      Verdict rows from ALL matching files in sorted (start-time) order. The
      live journal re-appends a row per trade_id at exit time, so a trade_id
      can appear more than once — last-write-wins is consumer policy. Rows are
      projected to _JOURNAL_FIELDS (the raw rows embed multi-KB agent blobs).

Schema/versioning: SCHEMA_VERSION below; Event is additive-only — new kinds or
payload keys may appear, existing ones never change meaning within a version.

Tolerance contract: `events()` NEVER raises for malformed lines, missing files,
or unreadable databases — bad lines are skipped, missing sources contribute
zero events. Timestamps are parsed best-effort (RFC3339 with 'Z' and
nanosecond fractions normalized); rows with unparseable ts get ts=None and
sort to the epoch (front) with stable per-source file order preserved.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from dataclasses import dataclass, field
from glob import glob
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1

#: Every kind this view can emit.
KINDS = ("verdict_trace", "fill", "ledger", "incident", "journal")

#: Injected websocket self-test symbols (null-ts rows) — excluded from kind="fill".
SYNTHETIC_SYMBOLS = frozenset({"AAPL", "TSLA"})

#: Projection of journal verdict rows into Event.payload (raw rows are huge).
_JOURNAL_FIELDS = (
    "trade_id", "session_date", "phase", "action", "mfcs", "confidence",
    "entry_price", "fill_price", "fill_qty", "exit_price", "exit_reason",
    "realized_pnl", "order_id", "stop_order_id", "rejection_reason",
    "direction", "hold_duration_minutes", "position_size_pct", "timestamp",
)

_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
_FRAC_RE = re.compile(r"\.(\d{6})\d+")  # truncate >6-digit (nanosecond) fractions


def default_data_root() -> Path:
    """<repo_root>/data, resolved from this file's location (src/research/...)."""
    return Path(__file__).resolve().parents[2] / "data"


@dataclass(frozen=True)
class Event:
    """One row of the unified view. Correlation spine: ticker/order_id/execution_id."""

    ts: dt.datetime | None          # tz-aware UTC, or None if unparseable
    source: str                     # which durable stream (see module docstring)
    kind: str                       # one of KINDS
    ticker: str | None
    order_id: str | None            # broker order id, where the stream carries one
    execution_id: str | None        # broker execution id (fills/ledger only)
    payload: dict[str, Any] = field(default_factory=dict)


# ── tolerant primitives ──────────────────────────────────────────────────────

def _parse_ts(value: Any) -> dt.datetime | None:
    """Best-effort RFC3339 -> aware-UTC datetime. Never raises."""
    if not isinstance(value, str) or not value:
        return None
    try:
        s = _FRAC_RE.sub(lambda m: "." + m.group(1), value.strip())
        if s.endswith(("Z", "z")):
            s = s[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(s)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return None


def _iter_jsonl(path: Path) -> Iterator[dict]:
    """Yield dict rows from a jsonl file; skip malformed/non-dict lines. Never raises."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def _first_positive_float(*candidates: Any) -> float | None:
    for c in candidates:
        try:
            v = float(c)
        except (TypeError, ValueError):
            continue
        if v > 0:
            return v
    return None


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value)
    return s if s else None


# ── per-source builders ──────────────────────────────────────────────────────

def _verdict_events(data_root: Path, date: str) -> list[Event]:
    out = []
    for row in _iter_jsonl(data_root / "ops" / f"verdict_trace_{date}.jsonl"):
        out.append(Event(
            ts=_parse_ts(row.get("ts")), source="verdict_trace", kind="verdict_trace",
            ticker=_opt_str(row.get("ticker")), order_id=None, execution_id=None,
            payload=row,
        ))
    return out


def _fill_events(data_root: Path, date: str) -> list[Event]:
    out = []
    for row in _iter_jsonl(data_root / "ops" / f"raw_fills_{date}.jsonl"):
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        if data.get("event") not in ("fill", "partial_fill"):
            continue
        order = data.get("order") if isinstance(data.get("order"), dict) else {}
        symbol = order.get("symbol")
        if not symbol:
            continue
        if symbol in SYNTHETIC_SYMBOLS and not data.get("timestamp"):
            continue  # injected self-test rows (doc 270 prerequisite note)
        out.append(Event(
            ts=_parse_ts(data.get("timestamp")) or _parse_ts(row.get("captured_ts_utc")),
            source="raw_fills", kind="fill",
            ticker=str(symbol),
            order_id=_opt_str(order.get("id")),
            execution_id=_opt_str(data.get("execution_id")),
            payload={
                "event": data.get("event"),
                "symbol": symbol,
                "side": order.get("side"),
                "qty": _first_positive_float(data.get("qty"), order.get("filled_qty")),
                "price": _first_positive_float(data.get("price"), order.get("filled_avg_price")),
                "order_status": order.get("status"),
                "position_qty": data.get("position_qty"),
                "timestamp": data.get("timestamp"),
                "captured_ts_utc": row.get("captured_ts_utc"),
            },
        ))
    return out


def _ledger_events(data_root: Path, date: str) -> list[Event]:
    db = data_root / "ops" / "ledger_shadow.db"
    if not db.exists():
        return []
    out = []
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT seq, wall_ts, event_type, ticker, side, broker_order_id, "
                "broker_event_id, qty, price, fees FROM events "
                "WHERE substr(wall_ts,1,10) = ? ORDER BY seq", (date,)).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    for seq, wall_ts, event_type, ticker, side, boid, beid, qty, price, fees in rows:
        out.append(Event(
            ts=_parse_ts(wall_ts), source="ledger_shadow", kind="ledger",
            ticker=_opt_str(ticker), order_id=_opt_str(boid), execution_id=_opt_str(beid),
            payload={"seq": seq, "event_type": event_type, "side": side,
                     "qty": qty, "price": price, "fees": fees},
        ))
    return out


def _incident_events(data_root: Path, date: str) -> list[Event]:
    out = []
    for row in _iter_jsonl(data_root / "ops" / f"incidents_{date}.jsonl"):
        out.append(Event(
            ts=_parse_ts(row.get("ts_utc")), source="incidents", kind="incident",
            ticker=_opt_str(row.get("ticker")), order_id=None, execution_id=None,
            payload=row,
        ))
    return out


def _journal_events(data_root: Path, date: str) -> list[Event]:
    out = []
    pattern = (data_root / "journals" / f"journal_{date}_*.jsonl").as_posix()
    try:
        files = sorted(glob(pattern))
    except Exception:
        files = []
    for path in files:
        for row in _iter_jsonl(Path(path)):
            if "ticker" not in row:
                continue  # not a verdict row
            payload = {k: row.get(k) for k in _JOURNAL_FIELDS if k in row}
            out.append(Event(
                ts=_parse_ts(row.get("timestamp")), source="journal", kind="journal",
                ticker=_opt_str(row.get("ticker")),
                order_id=_opt_str(row.get("order_id")), execution_id=None,
                payload=payload,
            ))
    return out


_BUILDERS = (_verdict_events, _fill_events, _ledger_events, _incident_events, _journal_events)


# ── public API ───────────────────────────────────────────────────────────────

def events(date: str, data_root: str | Path | None = None,
           kinds: set[str] | None = None) -> Iterator[Event]:
    """Unified, time-ordered, read-only event iterator for one session date.

    Args:
        date: session date, "YYYY-MM-DD".
        data_root: override the data directory (default: <repo_root>/data).
            Reads only — nothing under data_root is ever written by this module.
        kinds: optional filter, subset of KINDS.

    Ordering: ascending parsed ts (None ts sorts to the epoch, i.e. first);
    ties preserve per-source file order (sort is stable, sources collected in
    the fixed order verdict_trace, raw_fills, ledger_shadow, incidents,
    journal). Never raises; missing/corrupt sources contribute zero events.
    """
    root = Path(data_root) if data_root is not None else default_data_root()
    collected: list[Event] = []
    for builder in _BUILDERS:
        try:
            collected.extend(builder(root, str(date)))
        except Exception:
            continue  # tolerance contract: one bad source never kills the view
    if kinds is not None:
        collected = [e for e in collected if e.kind in kinds]
    yield from sorted(collected, key=lambda e: e.ts or _EPOCH)
