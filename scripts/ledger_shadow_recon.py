"""doc 270 C2 - B1 LEDGER SHADOW feeder + three-way recon (DORMANT-C). Replays the durable websocket
fill stream (data/ops/raw_fills_<date>.jsonl) into the doc-222 event-sourced Ledger (a SHADOW db at
data/ops/ledger_shadow.db - never the production path), then emits the nightly drift line:
LEDGER vs JOURNAL vs BROKER. No cutover, no O1-O6 rewire; the >=5-session shadow clock starts 2026-06-10.

Prerequisite (verified 2026-06-10): execution_id present on 168/168 real fill events this week; the only
misses are injected AAPL/TSLA self-test rows (null ts), excluded here. Ledger dedupes on execution_id ->
re-runs and stream reconnects are no-ops.

Usage: python scripts/ledger_shadow_recon.py [--date YYYY-MM-DD]
"""
from __future__ import annotations
import os, sys, json, glob, argparse, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src.ops.ledger import Ledger, FILL_PARTIAL, FILL_COMPLETE  # noqa: E402

SHADOW_DB = os.path.join(ROOT, "data", "ops", "ledger_shadow.db")
SYNTH = {"AAPL", "TSLA"}


def ingest(date: str) -> dict:
    led = Ledger(SHADOW_DB)
    path = os.path.join(ROOT, "data", "ops", f"raw_fills_{date}.jsonl")
    stats = {"appended": 0, "duplicate": 0, "rejected": 0, "skipped": 0}
    if not os.path.exists(path):
        return stats
    for l in open(path, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(l)
        except Exception:
            continue
        raw = e.get("raw", {}).get("data", {}) if isinstance(e.get("raw"), dict) else {}
        if raw.get("event") not in ("fill", "partial_fill"):
            continue
        order = raw.get("order", {}) or {}
        sym, xid = order.get("symbol"), raw.get("execution_id")
        if not sym or sym in SYNTH or not xid:
            stats["skipped"] += 1
            continue
        try:
            qty = int(float(raw.get("qty") or 0)) or int(float(order.get("filled_qty") or 0))
            px = float(raw.get("price") or order.get("filled_avg_price") or 0)
        except (TypeError, ValueError):
            stats["skipped"] += 1
            continue
        if qty <= 0 or px <= 0:
            stats["skipped"] += 1
            continue
        r = led.append(
            event_type=FILL_COMPLETE if raw.get("event") == "fill" else FILL_PARTIAL,
            ticker=sym, wall_ts=str(raw.get("timestamp") or f"{date}T00:00:00Z"),
            side=order.get("side"), broker_order_id=str(order.get("id") or ""),
            broker_event_id=str(xid), qty=qty, price=px,
        )
        key = r if r in stats else ("rejected" if r.startswith("rejected") else "appended")
        stats[key] = stats.get(key, 0) + 1
    return stats


def _fold_realized_through(end_date: str):
    """Avg-entry fold over ALL events with wall_ts date <= end_date (the doc-222 pure-fold semantics,
    applied across days so carried positions and intraday re-buys book correctly). Returns
    (cumulative_realized, {sym: open_qty}, n_events_total)."""
    import sqlite3
    if not os.path.exists(SHADOW_DB):
        return None, {}, 0
    c = sqlite3.connect(SHADOW_DB)
    rows = c.execute(
        "SELECT ticker, side, qty, price FROM events WHERE event_type IN (?,?) "
        "AND substr(wall_ts,1,10) <= ? ORDER BY seq",
        (FILL_PARTIAL, FILL_COMPLETE, end_date)).fetchall()
    c.close()
    pos, avg, realized = {}, {}, 0.0
    for sym, side, qty, px in rows:
        q, p0, a0 = float(qty), pos.get(sym, 0.0), avg.get(sym, 0.0)
        if side == "buy":
            pos[sym] = p0 + q
            avg[sym] = (a0 * p0 + px * q) / (p0 + q) if (p0 + q) > 0 else px
        else:  # sell against avg entry; sells beyond tracked qty book 0 (orphan close of pre-stream position)
            booked = min(q, p0)
            realized += (px - a0) * booked
            pos[sym] = p0 - booked
    return round(realized, 2), {s: q for s, q in pos.items() if abs(q) >= 1e-6}, len(rows)


def ledger_realized(date: str):
    """Realized for `date` = fold-through(date) - fold-through(prior trading day with events)."""
    cum, open_pos, n_total = _fold_realized_through(date)
    if cum is None:
        return None, [], 0
    prev = (dt.date.fromisoformat(date) - dt.timedelta(days=1))
    cum_prev = 0.0
    for _ in range(7):  # walk back over weekends/holidays
        c_prev, _, n_prev = _fold_realized_through(prev.isoformat())
        if n_prev > 0 or _ == 6:
            cum_prev = c_prev or 0.0
            break
        prev -= dt.timedelta(days=1)
    return round(cum - cum_prev, 2), sorted(open_pos), n_total


def journal_realized(date: str):
    js = sorted(glob.glob(os.path.join(ROOT, "data", "journals", f"journal_{date}_*.jsonl")))
    if not js:
        return None
    per = {}
    for l in open(js[-1], encoding="utf-8", errors="replace"):
        try:
            e = json.loads(l)
        except Exception:
            continue
        if e.get("realized_pnl") is not None and e.get("trade_id"):
            per[e["trade_id"]] = float(e["realized_pnl"])
    return round(sum(per.values()), 2) if per else 0.0


def broker_realized(date: str):
    f = os.path.join(ROOT, "data", "reports", f"eod_{date}.json")
    if not os.path.exists(f):
        return None
    try:
        return json.load(open(f, encoding="utf-8")).get("sections", {}).get(
            "eod_failsafes", {}).get("broker_truth_recon", {}).get("broker_total_pnl")
    except Exception:
        return None


def recon_line(date: str) -> str:
    st = ingest(date)
    L, open_syms, n = ledger_realized(date)
    j, b = journal_realized(date), broker_realized(date)
    def fd(a, x):
        return f"{a - x:+.2f}" if (a is not None and x is not None) else "n/a"
    return (f"LEDGER-SHADOW {date}: ledger {('$%+.2f' % L) if L is not None else 'n/a'} "
            f"({n} fills, +{st['appended']} new/{st['duplicate']} dup; open: {','.join(open_syms) or 'none'}) | "
            f"journal {('$%+.2f' % j) if j is not None else 'n/a'} | broker {('$%+.2f' % b) if b is not None else 'n/a'} | "
            f"drift L-J {fd(L, j)}  L-B {fd(L, b)}  J-B {fd(j, b)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    a = ap.parse_args()
    print(recon_line(a.date))
