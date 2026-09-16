"""doc 266 PHASE 1 - BROKER-TRUTH per-arm P&L for the live D310.T2 arena (wide_stop vs tight_stop/phase1).
Journal P&L is sparse + phantom-poisoned (doc 263), so this reads ONLY broker fills (Alpaca activities) and
builds FIFO round-trips per symbol; each round-trip is attributed to the arm tagged in stop_decisions for its
ENTRY date. PRIMARY metric = round-trip RETURN on entry cost (the wide arm runs 0.5x qty by design, so raw $
understates it 2x). PRE-REGISTERED GATE (stated before running): flip MOMENTUM_T2_WIDE_PCT=1.0 UNLESS the live
read materially contradicts the n=306 replay + n=14 shadow -- i.e. UNLESS tight beats wide by >2pp mean return
AND wide's median is also worse. READ-ONLY.
"""
from __future__ import annotations
import os, json, glob, urllib.request, urllib.parse, datetime as dt
from zoneinfo import ZoneInfo
import numpy as np
ET=ZoneInfo("America/New_York"); ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def keys():
    k=s=None
    for f in [os.path.expanduser('~/momentum-x-secrets.env'), os.path.join(ROOT,'.env')]:
        if os.path.exists(f):
            for l in open(f,encoding='utf-8',errors='replace'):
                if l.startswith('ALPACA_API_KEY='): k=l.split('=',1)[1].strip().strip('"').strip("'")
                if l.startswith('ALPACA_SECRET_KEY='): s=l.split('=',1)[1].strip().strip('"').strip("'")
        if k and s: break
    return k,s
K,S=keys()

def fills(after,until):
    out=[];tok=None
    for _ in range(60):
        q=dict(activity_types='FILL',page_size=100,after=after,until=until)
        if tok: q['page_token']=tok
        u='https://paper-api.alpaca.markets/v2/account/activities?'+urllib.parse.urlencode(q)
        b=json.loads(urllib.request.urlopen(urllib.request.Request(u,headers={'APCA-API-KEY-ID':K,'APCA-API-SECRET-KEY':S,'User-Agent':'mx'}),timeout=30).read())
        if not b: break
        out+=b; tok=b[-1]['id']
        if len(b)<100: break
    return out

def main():
    # arm tags from stop_decisions (the executor logs one row per stop submission with the strategy)
    arm={}
    for f in sorted(glob.glob(os.path.join(ROOT,'data','shadow_stops','stop_decisions_2026-*.jsonl'))):
        fdate=os.path.basename(f)[15:25]   # the per-day file name IS the session date (rows carry None)
        for l in open(f,encoding='utf-8'):
            try: o=json.loads(l)
            except Exception: continue
            s=o.get('symbol'); st=str(o.get('submitted_strategy') or '')
            if s and st: arm[(fdate,s)]='wide' if 'wide' in st else 'tight'
    wn=sum(1 for v in arm.values() if v=='wide')
    print(f"arena arm tags: {len(arm)} entries ({wn} wide / {len(arm)-wn} tight) from stop_decisions")

    fs=fills('2026-05-19T00:00:00Z','2026-06-10T23:59:59Z')
    print(f"broker fills pulled: {len(fs)} (5/19 -> now)")
    bysym={}
    for a in sorted(fs,key=lambda a:a['transaction_time']):
        bysym.setdefault(a['symbol'],[]).append(a)

    rts=[]
    for sym,acts in bysym.items():
        pos=0.0; cost=0.0; proceeds=0.0; entry_date=None; skip_orphan=False
        for a in acts:
            side=a['side']; q=float(a['qty']); px=float(a['price'])
            t=dt.datetime.fromisoformat(a['transaction_time'].replace('Z','+00:00')).astimezone(ET)
            if pos==0:
                if side!='buy': skip_orphan=True; continue   # orphan close of pre-window position
                cost=0.0; proceeds=0.0; entry_date=t.date().isoformat(); skip_orphan=False
            if skip_orphan: continue
            if side=='buy': pos+=q; cost+=q*px
            elif side=='sell': pos-=q; proceeds+=q*px
            else: skip_orphan=True; continue                  # shorts: T2 is long-only
            if pos<=1e-9 and cost>0:
                pnl=proceeds-cost
                rts.append(dict(sym=sym,entry=entry_date,close=t.date().isoformat(),cost=cost,pnl=pnl,ret=pnl/cost,
                                arm=arm.get((entry_date,sym))))
                pos=0.0; cost=0.0; proceeds=0.0; entry_date=None
    tagged=[r for r in rts if r['arm']]
    print(f"FIFO round-trips in window: {len(rts)} | arm-tagged: {len(tagged)}")

    print("\n=== BROKER-TRUTH per-arm (PRIMARY = return on entry cost; wide is 0.5x-sized by design) ===")
    print(f"  {'arm':>6}{'n':>4}{'total $':>11}{'mean ret':>10}{'med ret':>9}{'win%':>6}{'best':>8}{'worst':>8}")
    res={}
    for a in ('wide','tight'):
        v=[r for r in tagged if r['arm']==a]
        if not v: print(f"  {a:>6}   0"); continue
        rr=np.array([r['ret'] for r in v]); pp=np.array([r['pnl'] for r in v])
        res[a]=dict(mean=rr.mean(),med=np.median(rr))
        print(f"  {a:>6}{len(v):>4}{pp.sum():>+11,.0f}{rr.mean()*100:>+9.2f}%{np.median(rr)*100:>+8.2f}%{(rr>0).mean()*100:>5.0f}%{rr.max()*100:>+7.1f}%{rr.min()*100:>+7.1f}%")
    print("\n  wide round-trips (eyeball trust):")
    for r in sorted([r for r in tagged if r['arm']=='wide'],key=lambda r:r['entry']):
        print(f"    {r['entry']} {r['sym']:<6} cost ${r['cost']:>8,.0f}  pnl ${r['pnl']:>+8,.0f}  ret {r['ret']*100:>+6.1f}%  closed {r['close']}")
    print("\n  tight round-trips:")
    for r in sorted([r for r in tagged if r['arm']=='tight'],key=lambda r:r['entry']):
        print(f"    {r['entry']} {r['sym']:<6} cost ${r['cost']:>8,.0f}  pnl ${r['pnl']:>+8,.0f}  ret {r['ret']*100:>+6.1f}%  closed {r['close']}")

    print("\n=== PRE-REGISTERED GATE ===")
    if 'wide' in res and 'tight' in res:
        contra = (res['tight']['mean']-res['wide']['mean']>0.02) and (res['tight']['med']>res['wide']['med'])
        print(f"  tight-mean - wide-mean = {(res['tight']['mean']-res['wide']['mean'])*100:+.2f}pp | "
              f"contradiction (tight>wide by >2pp mean AND median): {'YES -> DO NOT FLIP, investigate' if contra else 'NO -> FLIP CLEARED (concordant w/ n=306 replay + n=14 shadow)'}")
    else:
        print("  insufficient tagged round-trips on one arm -> gate falls back to replay+shadow concordance; report honestly.")

if __name__=='__main__': main()
