"""doc 222 (B1 Phase 1-3): the event-sourced ledger — make phantom P&L structurally impossible.

P&L and position state become a PURE FOLD over an append-only event log. Only broker-confirmed
FILL events (carrying a broker_event_id) book P&L. The wrapper's succeeded=True is a HINT, not
a truth — it can emit ORDER_* events, never a FILL.

Phase 1 — event schema (tight, append-only, idempotent):
  ORDER_SUBMITTED  local intent      — never books
  ORDER_ACKED      broker accepted   — never books
  ORDER_REJECTED   lifecycle close   — books nothing
  ORDER_CANCELLED  lifecycle close   — books nothing
  FILL_PARTIAL     books the slice   — REQUIRES broker_event_id
  FILL_COMPLETE    books remainder   — REQUIRES broker_event_id
  RECON_DELTA      drift observation — never books (triggers a CRITICAL upstream)
THE rule: no event books P&L without a broker_event_id. That single invariant is most of B1.

Phase 2 — persistent append-only log: SQLite, crash-safe, dedup by PK on broker_event_id,
fsync on every fill-class event.

Phase 3 — position & realized P&L as a PURE FOLD over the log (no caches that can diverge).
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DB = _ROOT / "data" / "ops" / "ledger.db"

# event types
ORDER_SUBMITTED = "ORDER_SUBMITTED"
ORDER_ACKED = "ORDER_ACKED"
ORDER_REJECTED = "ORDER_REJECTED"
ORDER_CANCELLED = "ORDER_CANCELLED"
FILL_PARTIAL = "FILL_PARTIAL"
FILL_COMPLETE = "FILL_COMPLETE"
RECON_DELTA = "RECON_DELTA"

EVENT_TYPES = frozenset({ORDER_SUBMITTED, ORDER_ACKED, ORDER_REJECTED, ORDER_CANCELLED,
                         FILL_PARTIAL, FILL_COMPLETE, RECON_DELTA})
# ONLY these book P&L — and ONLY with a broker_event_id (enforced on append).
FILL_TYPES = frozenset({FILL_PARTIAL, FILL_COMPLETE})


@dataclass(frozen=True)
class Position:
    qty: int = 0               # signed: + long, - short
    avg_entry: float = 0.0     # avg entry of the OPEN qty
    realized_pnl: float = 0.0  # cumulative realized P&L for this ticker


_ZERO = Position()


class Ledger:
    """Append-only event log + pure-fold position/P&L. The bank ledger for execution truth."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = str(db_path or _DEFAULT_DB)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, isolation_level=None)  # autocommit; we fsync explicitly
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=FULL")  # crash-safe; fill events must survive kill -9
        return c

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    wall_ts TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    side TEXT,
                    broker_order_id TEXT,
                    broker_event_id TEXT UNIQUE,   -- dedup key; NULL allowed for non-fill events
                    qty INTEGER DEFAULT 0,
                    price REAL DEFAULT 0.0,
                    fees REAL DEFAULT 0.0,
                    payload TEXT
                )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_ticker_seq ON events(ticker, seq)")

    def append(self, *, event_type: str, ticker: str, wall_ts: str,
               side: str | None = None, broker_order_id: str | None = None,
               broker_event_id: str | None = None, qty: int = 0, price: float = 0.0,
               fees: float = 0.0, payload: str | None = None) -> str:
        """Append one event (idempotent on broker_event_id). Returns 'appended'|'duplicate'|
        'rejected:<reason>'. NEVER raises into trading.

        THE invariant: a FILL_* event MUST carry a broker_event_id (no fill books P&L without
        broker confirmation). A duplicate broker_event_id is a no-op (stream-reconnect safe)."""
        try:
            if event_type not in EVENT_TYPES:
                return f"rejected:unknown_event_type:{event_type}"
            if event_type in FILL_TYPES and not broker_event_id:
                # the structural rule: no fill books without a broker confirmation id
                return "rejected:fill_without_broker_event_id"
            with self._conn() as c:
                if broker_event_id:
                    cur = c.execute("SELECT 1 FROM events WHERE broker_event_id=?",
                                    (broker_event_id,))
                    if cur.fetchone():
                        return "duplicate"   # idempotent: replaying a fill is a no-op
                c.execute(
                    "INSERT INTO events (wall_ts,event_type,ticker,side,broker_order_id,"
                    "broker_event_id,qty,price,fees,payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (wall_ts, event_type, ticker, side, broker_order_id, broker_event_id,
                     int(qty), float(price), float(fees), payload))
                if event_type in FILL_TYPES:
                    c.execute("PRAGMA wal_checkpoint(FULL)")  # durably persist the fill
            return "appended"
        except sqlite3.IntegrityError:
            return "duplicate"   # UNIQUE race -> still idempotent
        except Exception as e:  # noqa: BLE001
            logger.warning("ledger.append failed (%s %s): %s", event_type, ticker, e)
            return f"rejected:error:{str(e)[:80]}"

    # ── Phase 3: pure fold ──
    def _events_for(self, ticker: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT event_type,side,qty,price,fees FROM events WHERE ticker=? "
                "AND event_type IN (?,?) ORDER BY seq",
                (ticker, FILL_PARTIAL, FILL_COMPLETE)).fetchall()
        return [{"event_type": r[0], "side": r[1], "qty": r[2], "price": r[3], "fees": r[4]}
                for r in rows]

    def position(self, ticker: str) -> Position:
        """position(ticker) = fold(fill_events_for(ticker)). Pure; no cache. A buy adds to the
        open qty (avg-cost); a sell realizes P&L against the avg entry and reduces qty."""
        qty = 0
        avg = 0.0
        realized = 0.0
        for e in self._events_for(ticker):
            f_qty = int(e["qty"])
            f_price = float(e["price"])
            fees = float(e["fees"])
            is_buy = (e["side"] == "buy")
            if is_buy:
                # adding to (or opening) a long: weighted-avg entry
                new_qty = qty + f_qty
                if new_qty != 0:
                    avg = (avg * qty + f_price * f_qty) / new_qty if (qty >= 0) else f_price
                qty = new_qty
                realized -= fees
            else:  # sell: realize against avg entry for the closed qty
                realized += (f_price - avg) * f_qty - fees
                qty -= f_qty
                if qty == 0:
                    avg = 0.0
        return Position(qty=qty, avg_entry=round(avg, 6), realized_pnl=round(realized, 4))

    def realized_pnl(self, ticker: str) -> float:
        return self.position(ticker).realized_pnl

    def all_tickers(self) -> list[str]:
        with self._conn() as c:
            return [r[0] for r in c.execute(
                "SELECT DISTINCT ticker FROM events ORDER BY ticker").fetchall()]

    def total_realized_pnl(self) -> float:
        return round(sum(self.realized_pnl(t) for t in self.all_tickers()), 4)
