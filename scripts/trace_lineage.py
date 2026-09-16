"""doc 272 B1 - THE CORRELATION SPINE, provable: given a ticker+date (or --execution-id), print the full
lineage in one command: journal verdict rows -> VLL lifecycle events -> order ids -> broker executions
(raw_fills) -> ledger-shadow events. The HWH forensic (doc 270) took an hour; this makes it a 10-second query.
READ-ONLY. Usage: python scripts/trace_lineage.py --ticker HWH --date 2026-06-10
"""
from __future__ import annotations
import os, json, glob, argparse, sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def P(*a): return os.path.join(ROOT, *a)

def jload(line):
    try: return json.loads(line)
    except Exception: return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True); ap.add_argument("--date", required=True)
    ap.add_argument("--execution-id", default="")
    a = ap.parse_args(); tk, d = a.ticker.upper(), a.date

    print(f"==== LINEAGE {tk} {d} " + "=" * 40)
    # 1. journal verdict/booking rows
    js = sorted(glob.glob(P("data", "journals", f"journal_{d}_*.jsonl")))
    orders = set()
    if js:
        print("-- JOURNAL (verdicts + bookings) --")
        for l in open(js[-1], encoding="utf-8", errors="replace"):
            e = jload(l)
            if not e or e.get("ticker") != tk: continue
            if e.get("action") or e.get("realized_pnl") is not None:
                oid = str(e.get("order_id") or "")
                if oid: orders.add(oid)
                print(f"  tid={str(e.get('trade_id'))[:14]} act={e.get('action')} mfcs={e.get('mfcs')} "
                      f"oid={oid[:8] or '-'} entry={e.get('entry_price')} exit={e.get('exit_price')} "
                      f"pnl={e.get('realized_pnl')} reason={e.get('exit_reason') or e.get('rejection_reason') or ''}")
    # 2. VLL lifecycle
    vf = P("data", "ops", f"verdict_trace_{d}.jsonl")
    if os.path.exists(vf):
        print("-- VLL (lifecycle) --")
        for l in open(vf, encoding="utf-8", errors="replace"):
            e = jload(l)
            if e and e.get("ticker") == tk:
                print(f"  {e.get('ts','')[11:19]} {e.get('stage')} reason={e.get('reason','')[:60]}")
    # 3. broker executions
    rf = P("data", "ops", f"raw_fills_{d}.jsonl")
    if os.path.exists(rf):
        print("-- BROKER EXECUTIONS (raw_fills) --")
        for l in open(rf, encoding="utf-8", errors="replace"):
            e = jload(l)
            raw = (e or {}).get("raw", {}).get("data", {}) if isinstance((e or {}).get("raw"), dict) else {}
            o = raw.get("order", {}) or {}
            if o.get("symbol") != tk: continue
            if a.execution_id and str(raw.get("execution_id")) != a.execution_id: continue
            if raw.get("event") in ("fill", "partial_fill"):
                oid = str(o.get("id") or "")
                orders.add(oid)
                print(f"  {str(raw.get('timestamp'))[11:19]} {raw.get('event'):>12} {o.get('side'):>4} "
                      f"qty={raw.get('qty')} px={raw.get('price')} cum={o.get('filled_qty')}@{o.get('filled_avg_price')} "
                      f"oid={oid[:8]} exec={str(raw.get('execution_id'))[:10]} pos_after={raw.get('position_qty')}")
    # 4. ledger-shadow events
    db = P("data", "ops", "ledger_shadow.db")
    if os.path.exists(db):
        try:
            c = sqlite3.connect(db)
            rows = c.execute("SELECT wall_ts,event_type,side,qty,price,broker_event_id FROM events "
                             "WHERE ticker=? AND substr(wall_ts,1,10)=? ORDER BY seq", (tk, d)).fetchall()
            c.close()
            if rows:
                print("-- LEDGER SHADOW (event-sourced book) --")
                for r in rows:
                    print(f"  {str(r[0])[11:19]} {r[1]:>13} {str(r[2]):>4} qty={r[3]} px={r[4]} exec={str(r[5])[:10]}")
        except Exception as e:
            print(f"  (ledger read failed: {str(e)[:60]})")
    print(f"-- SPINE: {len(orders)} distinct order id(s): {', '.join(sorted(x[:8] for x in orders if x)) or '-'}")

if __name__ == "__main__":
    main()
