"""doc 264 - ACCOUNT FORENSICS: where did $100K -> $216K actually come from? Decompose the live paper equity
curve with the SAME hostile metrics we apply to backtests: median daily return, daily win rate, top-N-days share
of the total gain, biggest single days (artifact candidates: Alpaca paper reverse-split credits, phantom MTM),
and per-ticker concentration of realized P&L. READ-ONLY (GET endpoints only). No trading.
"""
from __future__ import annotations
import os, json, datetime as dt, urllib.request, urllib.parse
import numpy as np

def keys():
    k=s=None
    for f in [os.path.expanduser('~/momentum-x-secrets.env'), '.env']:
        if os.path.exists(f):
            for l in open(f, encoding='utf-8', errors='replace'):
                if l.startswith('ALPACA_API_KEY='): k=l.split('=',1)[1].strip().strip('"').strip("'")
                if l.startswith('ALPACA_SECRET_KEY='): s=l.split('=',1)[1].strip().strip('"').strip("'")
        if k and s: break
    return k,s
K,S=keys(); BASE='https://paper-api.alpaca.markets'

def get(path, params=None):
    u=BASE+path+('?'+urllib.parse.urlencode(params) if params else '')
    req=urllib.request.Request(u, headers={'APCA-API-KEY-ID':K,'APCA-API-SECRET-KEY':S,'User-Agent':'mx-forensics'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def main():
    # ---- 1. equity curve ----
    ph=get('/v2/account/portfolio/history', dict(period='12M', timeframe='1D'))
    eq=np.array([e for e in ph['equity'] if e], dtype=float)
    ts=[dt.datetime.fromtimestamp(t).date() for t,e in zip(ph['timestamp'],ph['equity']) if e]
    print(f"equity curve: {ts[0]} -> {ts[-1]}  ({len(eq)} days)   ${eq[0]:,.0f} -> ${eq[-1]:,.0f}  ({(eq[-1]/eq[0]-1)*100:+.1f}%)")
    dr=eq[1:]/eq[:-1]-1
    dd=[(ts[i+1], dr[i], eq[i+1]-eq[i]) for i in range(len(dr))]
    total_gain=eq[-1]-eq[0]
    print(f"\n=== HOSTILE METRICS ON THE LIVE CURVE (the same gates every backtest faced) ===")
    print(f"  median daily return : {np.median(dr)*100:+.3f}%")
    print(f"  daily win rate      : {(dr>0).mean()*100:.0f}%  ({int((dr>0).sum())}/{len(dr)})")
    print(f"  mean daily          : {np.mean(dr)*100:+.3f}%")
    big=sorted(dd, key=lambda x:-x[2])[:8]
    top5=sum(x[2] for x in big[:5])
    print(f"  top-5 days $ share  : ${top5:,.0f} of ${total_gain:,.0f} total gain = {top5/total_gain*100:.0f}%")
    top10=sum(x[2] for x in sorted(dd,key=lambda x:-x[2])[:10])
    print(f"  top-10 days $ share : {top10/total_gain*100:.0f}%")
    print(f"  worst 5 days        : "+", ".join(f"{d} {r*100:+.1f}% (${g:,.0f})" for d,r,g in sorted(dd,key=lambda x:x[2])[:5]))
    print(f"\n  biggest UP days (artifact candidates -- check vs fills below):")
    for d,r,g in big: print(f"    {d}  {r*100:+6.1f}%  ${g:>+10,.0f}")

    # ---- 2. non-trade activities (splits, corp actions, journal entries) ----
    print(f"\n=== NON-TRADE ACTIVITIES (Alpaca paper artifacts: splits/SSP/CSD etc) ===")
    try:
        acts=get('/v2/account/activities', dict(page_size=100))
        nontrade=[a for a in acts if a.get('activity_type')!='FILL']
        if not nontrade: print("  (none in latest page)")
        for a in nontrade[:20]:
            print(f"  {str(a.get('date') or a.get('transaction_time'))[:10]}  {a.get('activity_type'):>6}  {a.get('symbol','')!s:<6} qty={a.get('qty','')} px={a.get('price','')} net={a.get('net_amount','')}")
    except Exception as e:
        print(f"  activities fetch failed: {str(e)[:80]}")

    # ---- 3. per-ticker realized P&L concentration (from FILL activities, FIFO-ish net) ----
    print(f"\n=== PER-TICKER REALIZED P&L (cash flow per symbol from fills; + = net cash extracted) ===")
    fills=[]; ptoken=None
    for _ in range(40):
        p=dict(activity_types='FILL', page_size=100)
        if ptoken: p['page_token']=ptoken
        batch=get('/v2/account/activities', p)
        if not batch: break
        fills+=batch; ptoken=batch[-1].get('id')
        if len(batch)<100: break
    flow={}; pos={}
    for a in fills:
        sym=a['symbol']; q=float(a['qty']); px=float(a['price'])
        sgn=-1 if a['side']=='buy' else 1
        flow[sym]=flow.get(sym,0)+sgn*q*px
        pos[sym]=pos.get(sym,0)+(q if a['side']=='buy' else -q)
    # only count tickers that are FLAT (closed round trips) for honest realized
    closed={s:v for s,v in flow.items() if abs(pos.get(s,0))<1e-6}
    tot=sum(closed.values())
    srt=sorted(closed.items(), key=lambda x:-x[1])
    print(f"  fills pulled: {len(fills)} | closed tickers: {len(closed)} | net realized (closed only): ${tot:,.0f}")
    print(f"  TOP 10 winners: "+", ".join(f"{s} ${v:,.0f}" for s,v in srt[:10]))
    print(f"  TOP 10 losers : "+", ".join(f"{s} ${v:,.0f}" for s,v in srt[-10:]))
    if tot>0:
        top10s=sum(v for _,v in srt[:10])
        print(f"  top-10-ticker share of net realized: {top10s/tot*100:.0f}%  (lottery check)")
    wins=[v for v in closed.values() if v>0]; losses=[v for v in closed.values() if v<=0]
    print(f"  ticker win rate: {len(wins)}/{len(closed)} ({len(wins)/max(len(closed),1)*100:.0f}%) | median ticker P&L: ${np.median(list(closed.values())):,.0f}")

if __name__=='__main__': main()
