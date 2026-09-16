"""doc 292: unit tests for the RV forward shadow-ledger status logic and the vendor-agnostic IV ingestion
(schema validation + idempotent upsert + both vendor-shape loaders, synthetic fixtures only)."""
import importlib.util
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, str(_ROOT / "scripts" / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── rv forward ledger: certification-status logic against the frozen bar ─────

@pytest.fixture()
def ledger_mod(tmp_path, monkeypatch):
    m = _load("_doc292_rv_forward_ledger")
    monkeypatch.setattr(m, "LEDGER", tmp_path / "rv_forward_ledger.jsonl")
    return m


def _rows(m, n, improvement=0.05, start_forward=True, n_names=140):
    for i in range(n):
        d = f"2026-08-{(i % 28) + 1:02d}" if start_forward else f"2026-06-{(i % 28) + 1:02d}"
        # unique-ify dates beyond a month by year-bumping (status logic doesn't dedupe dates)
        m.write_row({"date": f"{d}-{i}", "n_names": n_names,
                     "qlike_har": 0.20, "qlike_challenger": 0.20 * (1 - improvement),
                     "improvement_pct": 100 * improvement, "retro": not start_forward,
                     "computed_at": "2026-08-01T00:00:00+00:00"})


def test_empty_ledger_is_pending(ledger_mod):
    assert ledger_mod.status()["status"] == "PENDING-COLLECTION"


def test_retro_rows_never_certify(ledger_mod):
    _rows(ledger_mod, 100, improvement=0.10, start_forward=False)
    s = ledger_mod.status()
    assert s["n_forward_sessions"] == 0 and s["status"] == "PENDING-COLLECTION"


def test_below_n_min_is_pending_even_if_strong(ledger_mod):
    _rows(ledger_mod, 30, improvement=0.10)
    assert ledger_mod.status()["status"] == "PENDING-COLLECTION"


def test_confirms_at_bar(ledger_mod):
    _rows(ledger_mod, 80, improvement=0.05)
    s = ledger_mod.status()
    assert s["status"] == "CONFIRMED (forward)" and s["pooled_improvement_pct"] > 2.0


def test_sub_mmi_improvement_never_confirms(ledger_mod):
    _rows(ledger_mod, 80, improvement=0.01)   # 1% < the frozen 2% MMI
    assert ledger_mod.status()["status"] == "PENDING-COLLECTION"


def test_thin_sessions_excluded(ledger_mod):
    _rows(ledger_mod, 80, improvement=0.10, n_names=10)   # <30 names never counts
    assert ledger_mod.status()["n_forward_sessions"] == 0


# ── IV ingestion: schema + upsert + loaders on synthetic fixtures ─────────────

@pytest.fixture()
def iv_mod(tmp_path, monkeypatch):
    m = _load("_doc292_iv_ingest")
    monkeypatch.setattr(m, "STORE", tmp_path / "iv_eod.jsonl")
    return m


def test_validation_rejects_garbage(iv_mod):
    rep = iv_mod.upsert([
        {"date": "07/01/2026", "ticker": "AAPL", "tenor_days": 30, "iv": 0.3, "kind": "cm",
         "source": "x", "asof": "t"},                                # bad date format
        {"date": "2026-07-01", "ticker": "", "tenor_days": 30, "iv": 0.3, "kind": "cm",
         "source": "x", "asof": "t"},                                # missing ticker
        {"date": "2026-07-01", "ticker": "AAPL", "tenor_days": 30, "iv": 35.0, "kind": "cm",
         "source": "x", "asof": "t"},                                # percent-vs-decimal mixup caught
    ])
    assert rep["invalid"] == 3 and rep["inserted"] == 0


def test_upsert_idempotent(iv_mod):
    row = {"date": "2026-07-01", "ticker": "AAPL", "tenor_days": 30, "iv": 0.31, "kind": "cm",
           "source": "test", "asof": "t"}
    r1 = iv_mod.upsert([row])
    r2 = iv_mod.upsert([dict(row, iv=0.33)])
    assert r1["inserted"] == 1 and r2["replaced"] == 1 and r2["inserted"] == 0
    store = iv_mod._read_store()
    assert len(store) == 1 and store[0]["iv"] == 0.33


def test_polygon_shape_loader(iv_mod, tmp_path):
    fx = tmp_path / "poly.json"
    fx.write_text(json.dumps({"results": [
        {"underlying_ticker": "AAPL", "date": "2026-07-01", "implied_volatility": 0.29, "dte": 30},
        {"underlying_ticker": "MSFT", "date": "2026-07-01", "implied_volatility": 0.22,
         "expiration": "2026-07-31"},
    ]}), encoding="utf-8")
    rep = iv_mod.load_polygon_options_aggs(fx)
    assert rep["inserted"] == 2 and rep["invalid"] == 0
    tenors = {r["ticker"]: r["tenor_days"] for r in iv_mod._read_store()}
    assert tenors["AAPL"] == 30 and tenors["MSFT"] == 30


def test_orats_shape_loader_percent_autodetect(iv_mod, tmp_path):
    fx = tmp_path / "orats.csv"
    fx.write_text("tradeDate,ticker,iv30d,iv60d\n07/01/2026,AAPL,29.5,31.2\n2026-07-02,MSFT,0.21,\n",
                  encoding="utf-8")
    rep = iv_mod.load_orats_csv(fx)
    assert rep["inserted"] == 3 and rep["invalid"] == 0
    store = {(r["ticker"], r["tenor_days"]): r["iv"] for r in iv_mod._read_store()}
    assert abs(store[("AAPL", 30)] - 0.295) < 1e-9      # percent auto-converted
    assert abs(store[("MSFT", 30)] - 0.21) < 1e-9       # decimal passed through
