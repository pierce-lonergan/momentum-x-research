"""doc 265 - ENTRY-MECHANISM A/B REPLAY. The doc-264 finding: the dip-limit entry adversely selects (6/8: the 5
fills were the faders, the 7 unfilled were the rockets +274/+121/+76/+60%...). This replay prices that trade-off
on EVERY actual journal BUY decision since May, on real minute bars (Polygon REST, ET derived from raw UTC ms --
never warehouse ts_et), identical exits both arms, costs included.

ARM A (incumbent): dip-limit at the journal's entry_price; fills iff a later bar's low touches it; entry AT the
  limit with ZERO slippage (generous to the incumbent = hostile to the hypothesis).
ARM B (challenger): marketable at the decision minute's close +50bps slippage; always fills.
Both arms: flat at the 15:55 bar close -30bps. Secondary panel: hard intraday stops (none/-10/-15/-20%) filled
  at stop -50bps (hostile).

PRE-REGISTERED decision rule (stated before running): B replaces A iff (i) B total-$ > A total-$ at fixed $5k/
ticket AND ahead on the majority of session days, (ii) B stays ahead after winsorizing returns at +/-30%
(tail-illusion check), (iii) the per-day loss profile doesn't break the many-tickets LLN sizing argument (report
worst day both arms). NOTE: in a tail-game the MEDIAN may favor A (it enters cheaper when it fills) while the
MEAN favors B (it holds the runners) -- with ~20 tickets/day the mean*n is what the account eats; BOTH reported.
CALIBRATION: simulated Arm-A fills on 2026-06-08 must match the broker's actual 5-filled/14-unfilled split.
READ-ONLY research. No trading.
"""
from __future__ import annotations
import os, json, glob, time, datetime as dt, urllib.request
import numpy as np

ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CKPT=os.path.join(ROOT,'data','research','_entry_ab_minute_ckpt.jsonl')
NOTIONAL=5000.0; SLIP_B=0.005; EXIT_COST=0.003; STOP_SLIP=0.005

def pkey():
    for f in [os.path.expanduser('~/momentum-x-secrets.env'), os.path.join(ROOT,'.env')]:
        if os.path.exists(f):
            for l in open(f,encoding='utf-8',errors='replace'):
                if l.startswith('POLYGON_API_KEY='): return l.split('=',1)[1].strip().strip('"').strip("'")
    return None
KEY=pkey()

def decisions():
    """First BUY decision per (date,ticker) from the journals, with limit px + submit time (ET)."""
    out={}
    for f in sorted(glob.glob(os.path.join(ROOT,'data','journals','journal_2026-0[56]-*.jsonl'))):
        date=os.path.basename(f)[8:18]
        for l in open(f,encoding='utf-8',errors='replace'):
            try: e=json.loads(l)
            except Exception: continue
            if e.get('action')!='BUY': continue
            tk=e.get('ticker'); px=e.get('entry_price')
            ts=e.get('order_submitted_at') or e.get('timestamp')
            if not tk or not px or not ts: continue
            try:
                t=dt.datetime.fromisoformat(str(ts).replace('Z','+00:00'))
                if t.tzinfo: t=t.astimezone(dt.timezone.utc).replace(tzinfo=None)
                tet=t-dt.timedelta(hours=4)   # EDT constant (May-Jun window)
            except Exception: continue
            k=(date,tk)
            if k not in out or tet<out[k][1]: out[k]=(float(px),tet)
    rows=[dict(date=d,ticker=t,limit=v[0],tsub=v[1]) for (d,t),v in out.items()]
    rows=[r for r in rows if r['tsub'].time()<=dt.time(15,30) and r['tsub'].time()>=dt.time(4,0)]
    return sorted(rows,key=lambda r:(r['date'],r['ticker']))

_CK={}
def load_ckpt():
    if os.path.exists(CKPT):
        for l in open(CKPT,encoding='utf-8'):
            try: o=json.loads(l); _CK[(o['d'],o['t'])]=o['bars']
            except Exception: pass
def bars(date,tk):
    """ET minute bars [(et_dt, o,h,l,c)] via Polygon REST; ET derived from raw UTC ms (-4h EDT)."""
    if (date,tk) in _CK: return [(dt.datetime.fromisoformat(b[0]),*b[1:]) for b in _CK[(date,tk)]]
    u=f"https://api.polygon.io/v2/aggs/ticker/{tk}/range/1/minute/{date}/{date}?adjusted=true&sort=asc&limit=50000&apiKey={KEY}"
    out=[]
    for k in range(3):
        try:
            d=json.loads(urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'mx'}),timeout=25).read())
            for b in d.get('results') or []:
                et=dt.datetime.utcfromtimestamp(b['t']/1000)-dt.timedelta(hours=4)
                out.append((et,b['o'],b['h'],b['l'],b['c']))
            break
        except Exception: time.sleep(0.5*(k+1))
    _CK[(date,tk)]=[(b[0].isoformat(),*b[1:]) for b in out]
    with open(CKPT,'a',encoding='utf-8') as f:
        f.write(json.dumps({'d':date,'t':tk,'bars':_CK[(date,tk)]})+'\n')
    time.sleep(0.05)
    return out

def replay(r):
    bs=[b for b in bars(r['date'],r['ticker']) if dt.time(9,30)<=b[0].time()<=dt.time(15,55)]
    if len(bs)<10: return None
    sub=max(r['tsub'], dt.datetime.fromisoformat(r['date']+'T09:30:00'))
    after=[b for b in bs if b[0]>=sub]
    if not after: return None
    eod=bs[-1][4]*(1-EXIT_COST)
    res=dict(date=r['date'],ticker=r['ticker'])
    # ARM B: marketable at decision bar close +slip
    eb=after[0][4]*(1+SLIP_B)
    res['B_entry']=eb; res['B_ret']=eod/eb-1
    # ARM A: dip-limit; fill iff any low<=limit from the decision bar on
    fa=None
    for b in after:
        if b[3]<=r['limit']: fa=b; break
    if fa is not None:
        ea=min(r['limit'], fa[4]) if fa is after[0] else r['limit']
        res['A_entry']=ea; res['A_ret']=eod/ea-1; res['A_fill_t']=fa[0].time().isoformat()[:5]
    else:
        res['A_entry']=None; res['A_ret']=None
    # stop panel (intraday hard stops, both arms)
    def stopped(entry, start_i, s):
        lvl=entry*(1-s)
        for b in after[start_i+1:]:
            if b[3]<=lvl: return (lvl*(1-STOP_SLIP))/entry-1
        return None
    res['stops']={}
    for s in (0.10,0.15,0.20):
        sb=stopped(eb,0,s); res['stops'][f'B{int(s*100)}']=sb if sb is not None else res['B_ret']
        if fa is not None:
            ia=after.index(fa); sa=stopped(res['A_entry'],ia,s)
            res['stops'][f'A{int(s*100)}']=sa if sa is not None else res['A_ret']
    return res

def main():
    if not KEY: print("no POLYGON_API_KEY"); return
    load_ckpt()
    ds=decisions()
    print(f"journal BUY decisions (deduped first-per-ticker-day, 04:00-15:30 ET): {len(ds)} across {len(set(r['date'] for r in ds))} sessions")
    R=[x for x in (replay(r) for r in ds) if x]
    print(f"replayable (minute bars found): {len(R)}")
    A=[x for x in R if x['A_ret'] is not None]
    print(f"\nARM A (dip-limit) fill rate: {len(A)}/{len(R)} = {len(A)/len(R)*100:.0f}%")
    # calibration vs broker 6/8
    cal=[x for x in R if x['date']=='2026-06-08']
    calf={x['ticker'] for x in cal if x['A_ret'] is not None}
    print(f"  CALIBRATION 6/8: sim-A fills = {sorted(calf)} (broker actual: ABAT, AIM, GMHS, OCC, TNGX)")
    a=np.array([x['A_ret'] for x in A]); b=np.array([x['B_ret'] for x in R])
    w=lambda x: np.clip(x,-0.30,0.30)
    print(f"\n=== HEADLINE (fixed ${NOTIONAL:,.0f}/ticket; A pays no entry slip, B pays {SLIP_B*100:.1f}%; both -{EXIT_COST*100:.1f}% at exit) ===")
    print(f"  {'':>14}{'n':>5}{'mean':>9}{'median':>9}{'win%':>7}{'winsor mean':>12}{'TOTAL $':>11}")
    print(f"  {'A dip-limit':>14}{len(a):>5}{np.mean(a)*100:>+8.2f}%{np.median(a)*100:>+8.2f}%{(a>0).mean()*100:>6.0f}%{np.mean(w(a))*100:>+11.2f}%{np.sum(a)*NOTIONAL:>+11,.0f}")
    print(f"  {'B marketable':>14}{len(b):>5}{np.mean(b)*100:>+8.2f}%{np.median(b)*100:>+8.2f}%{(b>0).mean()*100:>6.0f}%{np.mean(w(b))*100:>+11.2f}%{np.sum(b)*NOTIONAL:>+11,.0f}")
    # B on the names A MISSED (the adverse-selection quantum)
    miss=np.array([x['B_ret'] for x in R if x['A_ret'] is None])
    if len(miss): print(f"  {'B on A-missed':>14}{len(miss):>5}{np.mean(miss)*100:>+8.2f}%{np.median(miss)*100:>+8.2f}%{(miss>0).mean()*100:>6.0f}%{np.mean(w(miss))*100:>+11.2f}%{np.sum(miss)*NOTIONAL:>+11,.0f}")
    print("\n=== PER-DAY TOTAL $ (consistency; B must lead the majority of days) ===")
    days=sorted(set(x['date'] for x in R)); bwins=0
    for d in days:
        ta=sum(x['A_ret'] for x in R if x['date']==d and x['A_ret'] is not None)*NOTIONAL
        tb=sum(x['B_ret'] for x in R if x['date']==d)*NOTIONAL
        na=sum(1 for x in R if x['date']==d and x['A_ret'] is not None); nb=sum(1 for x in R if x['date']==d)
        bwins+=tb>ta
        print(f"  {d}: A {ta:>+9,.0f} (n{na:>2})   B {tb:>+9,.0f} (n{nb:>2})   {'B' if tb>ta else 'A'}")
    print(f"  B leads {bwins}/{len(days)} days")
    print("\n=== STOP PANEL (total $; intraday hard stop then EOD) ===")
    print(f"  {'stop':>8}{'A total':>12}{'B total':>12}")
    for s in (10,15,20):
        sa=sum(x['stops'].get(f'A{s}',0) for x in A)*NOTIONAL
        sb=sum(x['stops'][f'B{s}'] for x in R)*NOTIONAL
        print(f"  {('-%d%%'%s):>8}{sa:>+12,.0f}{sb:>+12,.0f}")
    print(f"  {'none':>8}{np.sum(a)*NOTIONAL:>+12,.0f}{np.sum(b)*NOTIONAL:>+12,.0f}")
    print("\n  worst single day: A "+f"{min(sum(x['A_ret'] for x in R if x['date']==d and x['A_ret'] is not None) for d in days)*NOTIONAL:+,.0f}"
          +"  B "+f"{min(sum(x['B_ret'] for x in R if x['date']==d) for d in days)*NOTIONAL:+,.0f}")
    print("\n  PRE-REGISTERED RULE: B replaces A iff (i) B total > A total AND B leads majority of days,")
    print("  (ii) B stays ahead under winsor +/-30%, (iii) worst-day profile sane. Read the tables.")

if __name__=='__main__': main()
