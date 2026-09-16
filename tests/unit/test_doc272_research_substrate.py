"""doc 272 C2 — tests for the concurrent-experiment research substrate.

Covers: event_bus parsing of a synthetic fixture data dir (all five durable
streams, malformed lines, synthetic-fill exclusion, time ordering), registry
fan-out exception isolation (poison experiment cannot break siblings), and the
ledger_journal_drift reference experiment computing a known drift.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import pytest

from src.research.event_bus import Event, events
from src.research.experiment import (
    REGISTRY,
    Experiment,
    _load_builtin_experiments,
    run_experiments,
)
from src.research.experiments.ledger_journal_drift import LedgerJournalDrift

DATE = "2026-01-15"
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


# ── fixture data dir ─────────────────────────────────────────────────────────

def _fill_line(captured: str, *, event: str, symbol: str, side: str, qty: str,
               price: str, order_id: str, execution_id: str | None,
               timestamp: str | None) -> str:
    data: dict = {
        "event": event,
        "order": {"id": order_id, "symbol": symbol, "side": side,
                  "filled_qty": qty, "filled_avg_price": price, "status": "filled"},
    }
    if execution_id is not None:
        data["execution_id"] = execution_id
        data["qty"] = qty
        data["price"] = price
    if timestamp is not None:
        data["timestamp"] = timestamp
    return json.dumps({"captured_ts_utc": captured,
                       "raw": {"stream": "trade_updates", "data": data}})


@pytest.fixture()
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    ops = root / "ops"
    journals = root / "journals"
    ops.mkdir(parents=True)
    journals.mkdir(parents=True)

    # verdict trace: 2 valid rows (one with null ts) + 1 malformed line
    (ops / f"verdict_trace_{DATE}.jsonl").write_text(
        json.dumps({"ts": f"{DATE}T13:31:48+00:00", "stage": "BLOCKED_CATALYST_GATE",
                    "ticker": "AAA", "reason": "catalyst_type=NONE", "mfcs": 0.2,
                    "path": "MAIN"}) + "\n"
        + "this is not json {{{\n"
        + json.dumps({"ts": None, "stage": "SMOKE", "ticker": "BBB", "reason": ""}) + "\n",
        encoding="utf-8")

    # raw fills: heartbeat + synthetic AAPL (null ts) + buy + duplicate xid +
    # nanosecond-ts partial sell + orphan sell + malformed line
    buy = _fill_line(f"{DATE}T14:00:00.000000Z", event="fill", symbol="TST",
                     side="buy", qty="100", price="1.00", order_id="o1",
                     execution_id="x1", timestamp=f"{DATE}T14:00:00.100000Z")
    (ops / f"raw_fills_{DATE}.jsonl").write_text(
        json.dumps({"captured_ts_utc": f"{DATE}T08:30:00.000000Z",
                    "raw": {"stream": "listening", "data": {"streams": ["trade_updates"]}}}) + "\n"
        + _fill_line(f"{DATE}T12:33:35.000000Z", event="fill", symbol="AAPL",
                     side="buy", qty="100", price="150.25", order_id="order-001",
                     execution_id=None, timestamp=None) + "\n"
        + buy + "\n"
        + buy + "\n"  # duplicate execution_id: bus yields it, experiment dedupes
        + _fill_line(f"{DATE}T15:00:00.000000Z", event="partial_fill", symbol="TST",
                     side="sell", qty="100", price="1.50", order_id="o2",
                     execution_id="x2", timestamp=f"{DATE}T15:00:00.546479033Z") + "\n"
        + _fill_line(f"{DATE}T15:30:00.000000Z", event="fill", symbol="ZZZ",
                     side="sell", qty="50", price="2.00", order_id="o3",
                     execution_id="x3", timestamp=f"{DATE}T15:30:00.000001Z") + "\n"
        + "{broken\n",
        encoding="utf-8")

    # incidents: 1 row
    (ops / f"incidents_{DATE}.jsonl").write_text(
        json.dumps({"id": f"{DATE}-1", "kind": "NAKED_POSITION_RISK",
                    "severity": "CRITICAL", "ticker": "TST",
                    "ts_utc": f"{DATE}T12:23:17Z", "session_date": DATE,
                    "context": {"reason": "test"}}) + "\n",
        encoding="utf-8")

    # journal: entry row (no realized) + two exit re-appends (last-write-wins 40.0)
    rows = [
        {"trade_id": "t1-TST", "ticker": "TST", "timestamp": f"{DATE}T13:31:00+00:00",
         "action": "BUY", "mfcs": 0.5, "order_id": "o1",
         "input_data": {"big": "blob"}, "agent_signals": [{"x": 1}]},
        {"trade_id": "t1-TST", "ticker": "TST", "timestamp": f"{DATE}T13:31:00+00:00",
         "action": "BUY", "mfcs": 0.5, "order_id": "o1", "realized_pnl": 35.0},
        {"trade_id": "t1-TST", "ticker": "TST", "timestamp": f"{DATE}T13:31:00+00:00",
         "action": "BUY", "mfcs": 0.5, "order_id": "o1", "realized_pnl": 40.0,
         "exit_reason": "STOP_FILL"},
    ]
    (journals / f"journal_{DATE}_093000.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\nnot json either\n",
        encoding="utf-8")

    # shadow ledger db: one row on DATE, one on another date (filtered out)
    conn = sqlite3.connect(ops / "ledger_shadow.db")
    conn.execute(
        "CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, wall_ts TEXT NOT NULL,"
        " event_type TEXT NOT NULL, ticker TEXT NOT NULL, side TEXT, broker_order_id TEXT,"
        " broker_event_id TEXT UNIQUE, qty INTEGER DEFAULT 0, price REAL DEFAULT 0.0,"
        " fees REAL DEFAULT 0.0, payload TEXT)")
    conn.execute(
        "INSERT INTO events (wall_ts, event_type, ticker, side, broker_order_id,"
        " broker_event_id, qty, price) VALUES (?,?,?,?,?,?,?,?)",
        (f"{DATE}T14:00:00.123456789Z", "FILL_COMPLETE", "TST", "buy", "o1", "x1", 100, 1.0))
    conn.execute(
        "INSERT INTO events (wall_ts, event_type, ticker, side, broker_order_id,"
        " broker_event_id, qty, price) VALUES (?,?,?,?,?,?,?,?)",
        ("2026-01-16T14:00:00Z", "FILL_COMPLETE", "OTH", "buy", "o9", "x9", 10, 5.0))
    conn.commit()
    conn.close()
    return root


# ── event_bus ────────────────────────────────────────────────────────────────

class TestEventBus:
    def test_unifies_all_five_streams(self, data_root: Path):
        evs = list(events(DATE, data_root=data_root))
        by_kind: dict[str, list[Event]] = {}
        for e in evs:
            by_kind.setdefault(e.kind, []).append(e)
        assert set(by_kind) == {"verdict_trace", "fill", "ledger", "incident", "journal"}
        assert len(by_kind["verdict_trace"]) == 2   # malformed line skipped
        assert len(by_kind["fill"]) == 4            # heartbeat/synthetic/broken skipped
        assert len(by_kind["incident"]) == 1
        assert len(by_kind["journal"]) == 3         # garbage line skipped
        assert len(by_kind["ledger"]) == 1          # other-date row filtered out
        assert len(evs) == 11

    def test_synthetic_null_ts_fills_excluded(self, data_root: Path):
        tickers = {e.ticker for e in events(DATE, data_root=data_root, kinds={"fill"})}
        assert "AAPL" not in tickers
        assert tickers == {"TST", "ZZZ"}

    def test_time_ordered_with_correlation_spine(self, data_root: Path):
        evs = list(events(DATE, data_root=data_root))
        stamps = [e.ts or _EPOCH for e in evs]
        assert stamps == sorted(stamps)
        assert evs[0].ts is None                    # null-ts verdict row sorts first
        sell = next(e for e in evs if e.kind == "fill" and e.execution_id == "x2")
        assert sell.ts == dt.datetime(2026, 1, 15, 15, 0, 0, 546479,
                                      tzinfo=dt.timezone.utc)  # 9-digit nanos truncated
        assert sell.order_id == "o2" and sell.payload["side"] == "sell"
        assert sell.payload["qty"] == 100.0 and sell.payload["price"] == 1.50
        ledger = next(e for e in evs if e.kind == "ledger")
        assert (ledger.ticker, ledger.order_id, ledger.execution_id) == ("TST", "o1", "x1")

    def test_journal_payload_is_projected(self, data_root: Path):
        rows = list(events(DATE, data_root=data_root, kinds={"journal"}))
        assert all("input_data" not in e.payload and "agent_signals" not in e.payload
                   for e in rows)
        assert rows[-1].payload["realized_pnl"] == 40.0
        assert rows[0].order_id == "o1"

    def test_missing_root_and_garbage_never_raise(self, tmp_path: Path):
        assert list(events(DATE, data_root=tmp_path / "does_not_exist")) == []
        bad = tmp_path / "bad" / "ops"
        bad.mkdir(parents=True)
        (bad / f"verdict_trace_{DATE}.jsonl").write_text("{{{\n[1,2]\n", encoding="utf-8")
        (bad / "ledger_shadow.db").write_text("not a sqlite database", encoding="utf-8")
        assert list(events(DATE, data_root=tmp_path / "bad")) == []


# ── registry fan-out isolation ───────────────────────────────────────────────

class _Poison(Experiment):
    name = "poison"
    consumes = {"fill"}

    def on_event(self, e: Event) -> None:
        raise RuntimeError("boom")

    def metrics(self) -> dict:
        return {"should_be_replaced_by_error": True}


class _BadInit(Experiment):
    name = "bad_init"
    consumes = {"fill"}

    def __init__(self) -> None:
        raise ValueError("nope")

    def on_event(self, e: Event) -> None:  # pragma: no cover
        pass

    def metrics(self) -> dict:  # pragma: no cover
        return {}


class _Counter(Experiment):
    name = "counter"
    consumes = {"*"}

    def __init__(self) -> None:
        self.n = 0

    def on_event(self, e: Event) -> None:
        self.n += 1

    def metrics(self) -> dict:
        return {"n": self.n}


class TestRegistryFanout:
    def test_poison_and_bad_init_cannot_break_siblings(self, data_root: Path):
        res = run_experiments(DATE, data_root=data_root,
                              registry=[_BadInit, _Poison, _Counter], write=False)
        assert res["bad_init"]["metrics"]["error"].startswith("init-error")
        assert res["poison"]["metrics"]["error"].startswith("on_event-error")
        assert res["counter"]["metrics"]["n"] == 11  # full fixture stream delivered

    def test_builtin_registry_contains_reference(self):
        _load_builtin_experiments()
        names = set()
        for factory in REGISTRY:
            try:
                names.add(factory().name)
            except Exception:
                pass
        assert "ledger_journal_drift" in names

    def test_metrics_writer_appends_jsonl(self, data_root: Path, tmp_path: Path):
        out_root = tmp_path / "out"
        run_experiments(DATE, data_root=data_root,
                        registry=[LedgerJournalDrift], write=True, out_root=out_root)
        path = out_root / "research" / "experiments" / "ledger_journal_drift" / f"metrics_{DATE}.jsonl"
        record = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert record["schema_version"] == 1
        assert record["date"] == DATE
        assert record["metrics"]["drift_total"] == 10.0
        assert record["promote_gate"] == "informational"
        assert record["kill_gate"] is None
        # the read side stays read-only: nothing written under data_root
        assert not (data_root / "research").exists()


# ── reference experiment: known drift on fixture data ────────────────────────

class TestLedgerJournalDrift:
    def test_known_drift(self, data_root: Path):
        res = run_experiments(DATE, data_root=data_root,
                              registry=[LedgerJournalDrift], write=False)
        m = res["ledger_journal_drift"]["metrics"]
        # ledger fold: buy 100@1.00 (dup xid ignored), sell 100@1.50 -> TST +50.00;
        # ZZZ orphan sell books 0. journal last-write-wins: t1-TST -> +40.00.
        assert m["n_fills"] == 3
        assert m["per_ticker_drift"] == {"TST": 10.0, "ZZZ": 0.0}
        assert m["drift_total"] == 10.0
        assert m["worst_ticker"] == "TST"

    def test_empty_day_is_zero(self, tmp_path: Path):
        res = run_experiments(DATE, data_root=tmp_path / "empty",
                              registry=[LedgerJournalDrift], write=False)
        m = res["ledger_journal_drift"]["metrics"]
        assert m == {"drift_total": 0.0, "worst_ticker": None, "n_fills": 0,
                     "per_ticker_drift": {}}
