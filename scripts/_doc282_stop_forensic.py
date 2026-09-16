"""DOC 282 — the VANISHED-STOP class forensic (doc 281 Stage-2a, the decider).
Root-cause hypothesis (verified on PLSM 6/24): the OTO stop leg is priced off the INTENDED entry (e.g. phase-1 1.5%
below limit); when the marketable buy fills BELOW intended (favorable slippage), the stop lands AT/ABOVE the fill ->
the broker rejects/kills the stop leg -> position runs unprotected (D313 'unhedged' within seconds, D231
RECON_HARD_BLOCK) until an emergency stop or a manual/EOD exit.

This forensic parses every session log and quantifies the CLASS-WIDE damage:
 for each OTO submission: intended entry, submitted stop, actual fill, D231-blocked?, D313 events, realized exit
 (fill + pnl/qty from trade_results joined on ticker+session). Counterfactual = exit at the effective working stop
 (submitted stop if < fill, else the last intended stop below fill [D106/D100], else fill*(1-1.5%)).
 EXCESS LOSS = cf_pnl - realized_pnl where the trade lost MORE than a working stop would have allowed.
GATE A (pre-registered doc 281 §4): class-wide excess >= ~$8K AND root cause real -> the stop-path fix ships
(the ONE close-path code change). Read-only."""
from __future__ import annotations
import glob, json, re
from collections import defaultdict

OTO = re.compile(r"(\w+): Submitting OTO order \(buy limit \+ stop sell\) — qty=(\d+), entry=([\d.]+), stop=([\d.]+)")
FILL = re.compile(r"D217: (\w+) order \w+ reached terminal status=filled filled_qty=(\d+) filled_avg_price=([\d.]+)")
OPENED = re.compile(r"(\w+): Position opened — qty=(\d+), entry=\$([\d.]+), stop=\$([\d.]+)")
D231 = re.compile(r"D231 RECON_HARD_BLOCK STOP (\w+):")
D313E = re.compile(r"D313 EMERGENCY_STOP_SUBMITTED (\w+)")
D100S = re.compile(r"(\w+) D100: ATR stop=\$([\d.]+)")
D106S = re.compile(r"(\w+) D106 .*?stop=\$([\d.]+)")


def parse_session(path, date):
    txt = open(path, encoding="utf-8", errors="replace").read()
    otos = {}      # ticker -> (qty, intended_entry, submitted_stop)  (first OTO per ticker)
    fills = {}     # ticker -> fill_px
    blocked = set(m.group(1) for m in D231.finditer(txt))
    emer = set(m.group(1) for m in D313E.finditer(txt))
    intended = defaultdict(list)   # ticker -> [candidate intended stops]
    for m in D100S.finditer(txt):
        intended[m.group(1)].append(float(m.group(2)))
    for m in D106S.finditer(txt):
        intended[m.group(1)].append(float(m.group(2)))
    for m in OTO.finditer(txt):
        t = m.group(1)
        if t not in otos:
            otos[t] = (int(m.group(2)), float(m.group(3)), float(m.group(4)))
    for m in FILL.finditer(txt):
        fills.setdefault(m.group(1), float(m.group(3)))
    for m in OPENED.finditer(txt):
        fills.setdefault(m.group(1), float(m.group(3)))
    out = []
    for t, (qty, ientry, sstop) in otos.items():
        f = fills.get(t)
        if f is None:
            continue
        out.append({"date": date, "ticker": t, "qty": qty, "intended_entry": ientry,
                    "submitted_stop": sstop, "fill": f,
                    "stop_at_or_above_fill": sstop >= f - 1e-9,
                    "d231_blocked": t in blocked, "d313_emergency": t in emer,
                    "intended_stops": sorted(intended.get(t, []))})
    return out


def main():
    trades = [json.loads(l) for l in open("data/trade_results.jsonl", encoding="utf-8") if l.strip()]
    tidx = defaultdict(list)
    for r in trades:
        if not r.get("infrastructure_contaminated"):
            tidx[(r["ticker"], r["session_date"])].append(r)
    rows = []
    for p in sorted(glob.glob("logs/momentum_2026-*.log")):
        date = p.split("momentum_")[-1][:10]
        try:
            rows.extend(parse_session(p, date))
        except Exception as e:
            print(f"  parse fail {date}: {e}")
    print(f"parsed {len(rows)} OTO-filled entries across {len(set(r['date'] for r in rows))} sessions")
    affected = [r for r in rows if r["stop_at_or_above_fill"] or r["d231_blocked"]]
    print(f"CLASS (stop>=fill OR D231-blocked): {len(affected)} entries "
          f"({sum(1 for r in affected if r['stop_at_or_above_fill'])} stop>=fill, "
          f"{sum(1 for r in affected if r['d231_blocked'])} d231-blocked, "
          f"{sum(1 for r in affected if r['d313_emergency'])} got an emergency stop)")
    total_excess = 0.0
    matched = 0
    detail = []
    for r in affected:
        cands = tidx.get((r["ticker"], r["date"]), [])
        if not cands:
            continue
        tr = max(cands, key=lambda x: abs(x.get("pnl", 0)))
        pnl = tr["pnl"]
        qty, fill = r["qty"], r["fill"]
        exit_px = fill + pnl / qty if qty else fill
        # effective working stop: submitted if below fill, else best intended below fill, else 1.5% below fill
        eff = r["submitted_stop"] if r["submitted_stop"] < fill else None
        if eff is None:
            below = [s for s in r["intended_stops"] if s < fill]
            eff = max(below) if below else fill * 0.985
        cf_pnl = qty * (eff - fill)          # bounded loss if the stop had worked
        excess = cf_pnl - pnl if pnl < cf_pnl else 0.0
        matched += 1
        total_excess += excess
        detail.append({**r, "realized_pnl": round(pnl, 0), "exit_px": round(exit_px, 3),
                       "eff_stop": round(eff, 3), "cf_pnl": round(cf_pnl, 0), "excess": round(excess, 0)})
    detail.sort(key=lambda d: -d["excess"])
    print(f"\nmatched to realized trades: {matched}")
    print(f"{'date':>11} {'tkr':>6} {'qty':>6} {'fill':>7} {'sub_stop':>8} {'s>=f':>5} {'d231':>5} {'realized$':>10} {'cf_stop$':>9} {'EXCESS$':>9}")
    for d in detail:
        print(f"{d['date']:>11} {d['ticker']:>6} {d['qty']:>6} {d['fill']:>7.3f} {d['submitted_stop']:>8.3f} "
              f"{str(d['stop_at_or_above_fill'])[:1]:>5} {str(d['d231_blocked'])[:1]:>5} {d['realized_pnl']:>+10,.0f} {d['cf_pnl']:>+9,.0f} {d['excess']:>+9,.0f}")
    print(f"\n=== CLASS-WIDE EXCESS LOSS (realized worse than a working stop): ${total_excess:,.0f} ===")
    print(f"GATE A (>= $8,000 AND real root cause): {'PASS -> the stop-path fix ships' if total_excess >= 8000 else 'FAIL -> no code ships'}")
    json.dump({"class_excess": total_excess, "n_class": len(affected), "n_matched": matched, "detail": detail},
              open("data/research/doc282_stop_class.json", "w"), indent=1)
    print("wrote data/research/doc282_stop_class.json")


if __name__ == "__main__":
    main()
