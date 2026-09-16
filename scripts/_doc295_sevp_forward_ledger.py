"""DOC 295 — SEVP FORWARD SHADOW-LEDGER (pre-built, blind-safe). Tracks only FUTURE earnings events, so it
can be wired today without touching the blind backtest gate. If SEVP later passes its frozen backtest, this
instrument is already collecting the n>=60 forward sample the verdict map requires before any Pierce-gated
paper step; if SEVP dies, this ledger is retired unread.

Certification bar (copied verbatim from the frozen doc-294 verdict map + the doc-292 ledger idiom):
forward n >= 60 events, event-blocked bootstrap CI95 of mean net (at the frozen 5% cost line) excluding 0,
median net > 0. Rows are marked at the collector's EOD option prices T-1/T+1 — zero capital, no options
approval. FORWARD_START = 2026-07-14 (first session after build). Self-repairing: --append scores any
past-due unscored event whose legs exist in the price store.
"""
from __future__ import annotations
import argparse, json
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
LEDGER = _ROOT / "data" / "reports" / "sevp_forward_ledger.jsonl"
EVENTS = _ROOT / "data" / "research" / "doc294" / "earnings_events.jsonl"
FORWARD_START = "2026-07-14"
N_MIN = 60
COST = 0.05
SEED = 295


def _load(p):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def append_due():
    import importlib.util
    spec = importlib.util.spec_from_file_location("P", str(_ROOT / "scripts" / "_doc294_sevp_pipeline.py"))
    P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)
    prices = P._load_prices()
    done = {(r["ticker"], r["ts"]) for r in _load(LEDGER)}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = []
    for ev in _load(EVENTS):
        if ev["date"] < FORWARD_START or ev["date"] >= today:
            continue                      # forward-only, and the T+1 leg must be in the past
        if (ev["ticker"], ev["ts"]) in done:
            continue
        legs = P._event_legs(ev, prices)
        if not legs:
            continue                      # legs not collected yet — self-repairs on a later run
        p0 = legs["legs"]["C"][0] + legs["legs"]["P"][0]
        p1 = legs["legs"]["C"][1] + legs["legs"]["P"][1]
        if p0 <= 0:
            continue
        row = {"ticker": ev["ticker"], "ts": ev["ts"], "date": ev["date"], "t0": legs["t0"], "t1": legs["t1"],
               "gross": round((p0 - p1) / p0, 4), "net_5pct": round((p0 - p1) / p0 - COST, 4),
               "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        out.append(row)
    return out


def status():
    import numpy as np
    rows = _load(LEDGER)
    out = {"n_forward_events": len(rows), "n_min": N_MIN, "forward_start": FORWARD_START}
    if len(rows) < 8:
        out["status"] = "PENDING-COLLECTION"
        return out
    v = np.array([r["net_5pct"] for r in rows])
    rng = np.random.default_rng(SEED)
    boots = [rng.choice(v, len(v), replace=True).mean() for _ in range(5000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    out.update(mean_net=round(float(v.mean()), 4), median_net=round(float(np.median(v)), 4),
               ci95=[round(float(lo), 4), round(float(hi), 4)])
    out["status"] = ("CONFIRMED (forward)" if len(rows) >= N_MIN and lo > 0 and np.median(v) > 0
                     else "PENDING-COLLECTION")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--append", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.append:
        rows = append_due()
        for r in rows:
            print(f"sevp-forward {r['ticker']} {r['date']}: gross {r['gross']:+.4f} net {r['net_5pct']:+.4f} [appended]")
        if not rows:
            print("sevp-forward: nothing due/coverable yet")
    print(json.dumps(status(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
