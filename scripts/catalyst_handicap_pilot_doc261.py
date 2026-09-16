"""doc 261 - TRACK 2 PILOT: catalyst/binary-event handicapping feasibility. The real event-trading edge is not
predicting the binary outcome (near-random) but identifying where the OPTIONS-IMPLIED move is MISPRICED vs the
realized move (sell rich premium / buy cheap premium) - and whether an LLM reading the setup can pick the
mispriced ones. Step 1 (this pilot): does the data pipeline even work for OUR universe? Small-cap biotech binary
events (|gap|>=30% on a trial/FDA day) - do they have TRADEABLE options, and is implied systematically rich vs
realized? If small biotechs have no liquid options, the implied-probability edge is untestable for them (a finding).

Pipeline: detect biotech binary-event days (day_aggs + ticker_details SIC) -> for each, the PRE-EVENT ATM straddle
implied move (Polygon historical options aggregates, day before, nearest expiry after the event) vs the realized
move. Reports feasibility (how many events have usable options) + implied-vs-realized (the premium-richness read).
NO capital. NO live change.
"""
from __future__ import annotations
import os, json, time, urllib.request, urllib.parse
import numpy as np, pandas as pd, duckdb
DA="data/polygon_warehouse/day_aggs/**/*.parquet"; TD="data/polygon_warehouse/reference/ticker_details.parquet"

def pkey():
    for f in [os.path.expanduser('~/momentum-x-secrets.env'),'.env']:
        if os.path.exists(f):
            for l in open(f,encoding='utf-8',errors='replace'):
                if l.startswith('POLYGON_API_KEY='): return l.split('=',1)[1].strip().strip('"').strip("'")
    return None
KEY=pkey()

def get(url, tries=3):
    for k in range(tries):
        try:
            sep='&' if '?' in url else '?'
            with urllib.request.urlopen(urllib.request.Request(url+f"{sep}apiKey={KEY}",headers={'User-Agent':'mx'}),timeout=20) as r:
                return json.loads(r.read())
        except Exception: time.sleep(0.4*(k+1))
    return None

def events(n=40):
    con=duckdb.connect()
    df=con.execute(f"""
      WITH r AS (SELECT ticker, ts_et, open, close, volume, lag(close) OVER w pc
                 FROM read_parquet('{DA}',hive_partitioning=1) WINDOW w AS (PARTITION BY ticker ORDER BY ts_et)),
      b AS (SELECT ticker, ts_et::DATE d, open, close, pc, open/pc-1 gap,
              avg(volume*close) OVER w20 adv20 FROM r
            WINDOW w20 AS (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING))
      SELECT ticker, d::VARCHAR d, pc s0, open, gap, adv20 FROM b
      WHERE pc>=2 AND adv20>=1e6 AND abs(open/pc-1)>=0.30 AND EXTRACT(year FROM d) IN (2024,2025)
    """).df()
    bio=con.execute(f"""SELECT ticker FROM read_parquet('{TD}') WHERE
        lower(sic_description) LIKE '%pharma%' OR lower(sic_description) LIKE '%biolog%'
        OR lower(sic_description) LIKE '%medicinal%' OR lower(sic_description) LIKE '%in vitro%'""").df()
    df=df[df.ticker.isin(set(bio.ticker))].copy()
    df['gap']=pd.to_numeric(df.gap,errors='coerce')
    df=df.sort_values('d').drop_duplicates(['ticker','d'])
    # spread the sample across time + tickers
    return df.sample(min(n,len(df)),random_state=7).reset_index(drop=True), len(df)

def prior_trading_day(con, ticker, date):
    r=con.execute(f"""SELECT max(ts_et::DATE) FROM read_parquet('{DA}',hive_partitioning=1)
        WHERE ticker='{ticker}' AND ts_et::DATE < DATE '{date}'""").fetchone()
    return str(r[0])[:10] if r and r[0] else None

def implied_move(ticker, event_date, s0, con):
    """Pre-event ATM straddle implied move: day-before close of the nearest-expiry-after-event ATM call+put."""
    pday=prior_trading_day(con, ticker, event_date)
    if not pday: return None
    exp_hi=(np.datetime64(event_date)+np.timedelta64(45,'D')).astype(str)
    j=get(f"https://api.polygon.io/v3/reference/options/contracts?underlying_ticker={ticker}&expiration_date.gte={event_date}&expiration_date.lte={exp_hi}&expired=true&limit=1000")
    if not j or not j.get('results'): return dict(prior=pday, has_opt=0)
    cs=pd.DataFrame(j['results'])
    cs['strike']=pd.to_numeric(cs.strike_price,errors='coerce')
    exps=sorted(e for e in cs.expiration_date.unique() if e>=event_date)
    if not exps: return dict(prior=pday, has_opt=0)
    exp=exps[0]; near=cs[cs.expiration_date==exp].copy()
    near['dist']=(near.strike-s0).abs(); k=near.sort_values('dist').iloc[0].strike
    call=near[(near.contract_type=='call')&(near.strike==k)]; put=near[(near.contract_type=='put')&(near.strike==k)]
    if call.empty or put.empty: return dict(prior=pday, has_opt=1, straddle=None)
    def opx(sym):
        a=get(f"https://api.polygon.io/v2/aggs/ticker/{sym}/range/1/day/{pday}/{pday}")
        if a and a.get('results'): return a['results'][0].get('c')
        return None
    cpx=opx(call.iloc[0].ticker); ppx=opx(put.iloc[0].ticker)
    if cpx is None or ppx is None: return dict(prior=pday, has_opt=1, straddle=None, strike=float(k), exp=exp)
    return dict(prior=pday, has_opt=1, straddle=cpx+ppx, strike=float(k), exp=exp, impl=(cpx+ppx)/s0)

def main():
    if not KEY: print("no polygon key"); return
    con=duckdb.connect()
    ev,total=events(40)
    print(f"biotech binary-event days (|gap|>=30%, prev>=$2, ADV>=$1M, 2024-25): {total} total; sampling {len(ev)}")
    rows=[]
    for r in ev.itertuples():
        s0=float(r.s0); realized=abs(float(r.gap))
        im=implied_move(r.ticker, r.d, s0, con)
        if im is None: continue
        rows.append(dict(ticker=r.ticker,date=r.d,s0=round(s0,2),realized=round(realized,3),
            has_opt=im.get('has_opt',0),straddle=im.get('straddle'),impl=im.get('impl')))
        time.sleep(0.15)
    d=pd.DataFrame(rows)
    nopt=int(d.has_opt.sum()); npx=int(d.impl.notna().sum())
    print(f"\n=== FEASIBILITY ===")
    print(f"  events checked: {len(d)} | with listed options: {nopt} ({nopt/max(len(d),1)*100:.0f}%) | with a tradeable pre-event straddle price: {npx} ({npx/max(len(d),1)*100:.0f}%)")
    if npx<8:
        print("  --> SMALL-CAP BIOTECH BINARY EVENTS LARGELY LACK TRADEABLE OPTIONS -> the implied-probability edge is UNTESTABLE for this universe (a real finding). The handicapping game would need a more liquid event universe.")
    dd=d[d.impl.notna()].copy()
    if len(dd):
        dd['rich']=dd.impl-dd.realized   # implied minus realized; +ve = options were RICH (overpriced) = sell-premium edge
        print(f"\n=== IMPLIED vs REALIZED (n={len(dd)} with usable straddle) ===")
        print(f"  implied move  : mean {dd.impl.mean()*100:.1f}%  median {dd.impl.median()*100:.1f}%")
        print(f"  realized move : mean {dd.realized.mean()*100:.1f}%  median {dd.realized.median()*100:.1f}%")
        print(f"  RICHNESS (impl-real): mean {dd.rich.mean()*100:+.1f}pp  median {dd.rich.median()*100:+.1f}pp  | options-rich frac {(dd.rich>0).mean()*100:.0f}%")
        print(f"  (consistently +richness => a sell-premium edge into these events; the LLM-handicap test would then pick WHICH to sell.)")
        print("\n  sample:")
        for r in dd.head(12).itertuples():
            print(f"    {r.ticker:<6} {r.date}: implied {r.impl*100:>5.1f}%  realized {r.realized*100:>5.1f}%  richness {(r.impl-r.realized)*100:>+5.1f}pp")

if __name__=='__main__': main()
