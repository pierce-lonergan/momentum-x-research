"""DOC 292 WORKSTREAM C — vendor-agnostic EOD-IV ingestion (Stage-3 day-one drop-in).

Built against SYNTHETIC FIXTURES ONLY: no live endpoints, no keys, no trial signups. The day Pierce buys
data, one loader call converts the vendor file into the canonical store and Stage 3 runs the same session.

Canonical schema (data/iv_store/iv_eod.parquet — or .jsonl fallback):
  date (YYYY-MM-DD) | ticker | tenor_days (int, constant-maturity) | iv (annualized decimal, e.g. 0.35)
  | kind ('atm' | 'cm' | 'index') | source (str) | asof (ISO ts the row was ingested)

Loaders for the two likeliest vendor shapes:
  load_polygon_options_aggs(path)  — Polygon-style flat rows: {ticker, date/window_start, implied_volatility(0-1), expiration/dte, ...}
  load_orats_csv(path)             — ORATS/DataShop-style CSV: tradeDate, ticker, iv30d/iv60d/... (percent or decimal auto-detected)
Both normalize into the canonical schema, validate, and are idempotent (date+ticker+tenor+kind upsert).
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
STORE = _ROOT / "data" / "iv_store" / "iv_eod.jsonl"

CANON_FIELDS = ["date", "ticker", "tenor_days", "iv", "kind", "source", "asof"]


def _validate(row: dict) -> list[str]:
    errs = []
    d = str(row.get("date", ""))
    if len(d) != 10 or d[4] != "-" or d[7] != "-":
        errs.append(f"bad date {d!r}")
    if not row.get("ticker"):
        errs.append("missing ticker")
    try:
        t = int(row.get("tenor_days"))
        if not (1 <= t <= 730):
            errs.append(f"tenor out of range {t}")
    except Exception:
        errs.append("bad tenor_days")
    try:
        iv = float(row.get("iv"))
        if not (0.001 <= iv <= 5.0):     # annualized decimal; 500% cap catches percent-vs-decimal mixups
            errs.append(f"iv out of plausible range {iv}")
    except Exception:
        errs.append("bad iv")
    if row.get("kind") not in ("atm", "cm", "index"):
        errs.append(f"bad kind {row.get('kind')!r}")
    return errs


def _read_store():
    rows = []
    if STORE.exists():
        for line in STORE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def upsert(rows: list[dict]) -> dict:
    """validate + idempotent upsert on (date,ticker,tenor_days,kind). Returns a report."""
    report = {"in": len(rows), "invalid": 0, "inserted": 0, "replaced": 0, "errors": []}
    valid = []
    for r in rows:
        errs = _validate(r)
        if errs:
            report["invalid"] += 1
            if len(report["errors"]) < 10:
                report["errors"].append({"row": {k: r.get(k) for k in CANON_FIELDS[:5]}, "errs": errs})
        else:
            valid.append({k: r[k] for k in CANON_FIELDS})
    existing = _read_store()
    key = lambda r: (r["date"], r["ticker"], int(r["tenor_days"]), r["kind"])
    have = {key(r): i for i, r in enumerate(existing)}
    for r in valid:
        k = key(r)
        if k in have:
            existing[have[k]] = r
            report["replaced"] += 1
        else:
            existing.append(r)
            report["inserted"] += 1
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text("\n".join(json.dumps(r) for r in
                               sorted(existing, key=lambda r: (r["date"], r["ticker"], r["tenor_days"])))
                     + ("\n" if existing else ""), encoding="utf-8")
    return report


def _asof():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_polygon_options_aggs(path: str | Path, source: str = "polygon") -> dict:
    """Polygon-shaped JSONL/JSON: rows with underlying_ticker/ticker, date or window_start,
    implied_volatility (decimal), dte or expiration-derived tenor."""
    p = Path(path)
    raw = []
    text = p.read_text(encoding="utf-8")
    try:
        obj = json.loads(text)
        raw = obj if isinstance(obj, list) else obj.get("results", [])
    except json.JSONDecodeError:
        raw = [json.loads(l) for l in text.splitlines() if l.strip()]
    rows = []
    for r in raw:
        tk = r.get("underlying_ticker") or r.get("ticker")
        d = r.get("date") or (str(r.get("window_start", ""))[:10])
        iv = r.get("implied_volatility") or r.get("iv")
        tenor = r.get("dte") or r.get("tenor_days")
        if tenor is None and r.get("expiration") and d:
            try:
                tenor = (datetime.fromisoformat(str(r["expiration"])) - datetime.fromisoformat(str(d))).days
            except Exception:
                tenor = None
        rows.append({"date": d, "ticker": tk, "tenor_days": tenor, "iv": iv,
                     "kind": r.get("kind", "atm"), "source": source, "asof": _asof()})
    return upsert(rows)


def load_orats_csv(path: str | Path, source: str = "orats") -> dict:
    """ORATS/DataShop-shaped CSV: tradeDate,ticker,iv30d[,iv60d,...] — percent or decimal auto-detected."""
    import csv
    p = Path(path)
    rows = []
    with open(p, newline="", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            rec = {k.strip(): v for k, v in rec.items()}
            d = rec.get("tradeDate") or rec.get("date") or ""
            if "/" in d:  # MM/DD/YYYY -> ISO
                try:
                    d = datetime.strptime(d, "%m/%d/%Y").strftime("%Y-%m-%d")
                except Exception:
                    pass
            tk = rec.get("ticker") or rec.get("symbol")
            for col, tenor in [("iv30d", 30), ("iv10d", 10), ("iv60d", 60), ("iv90d", 90)]:
                if rec.get(col) not in (None, ""):
                    iv = float(rec[col])
                    if iv > 3.0:          # percent-quoted -> decimal
                        iv = iv / 100.0
                    rows.append({"date": d, "ticker": tk, "tenor_days": tenor, "iv": iv,
                                 "kind": "cm", "source": source, "asof": _asof()})
    return upsert(rows)


if __name__ == "__main__":
    print(json.dumps({"store": str(STORE), "rows": len(_read_store())}, indent=2))
