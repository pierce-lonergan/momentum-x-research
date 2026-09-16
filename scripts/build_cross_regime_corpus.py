"""doc 235 — build the 2024-2026 cross-regime labeled corpus (gate-eligible gappers).

Identical ex-ante gate applied to ALL years (no tuning): gap>=8%, open in [$0.50,$20], trailing-20d
ADV>=$1M. Microstructure-only features (13, all minute-derived) so the frozen model transfers fairly
across regimes — gap_pct/rvol/mfcs (live-eval-only) are DROPPED. Per-decision-point rows (every 5 min),
triple-barrier 30-min continuation label (+5%/-5%), no look-ahead. Output:
data/research/exit_labels_cross_regime.parquet
"""
from __future__ import annotations
import duckdb, numpy as np, pandas as pd

DA="data/polygon_warehouse/day_aggs/**/*.parquet"
MN="data/polygon_warehouse/minute_aggs/**/*.parquet"
OUT="data/research/exit_labels_cross_regime.parquet"
MICRO=["minute_idx","ret_session","ret_5m","ret_15m","ret_30m","vwap_dist","high_dist","low_dist",
       "range_pos","rvol_cum","vol_accel_5m","realized_vol_15m","up_min_frac_15m"]
CAD,HOR,UP,DN=5,30,0.05,0.05

def label_day(g):
    g=g.sort_values("ts_et").reset_index(drop=True); n=len(g)
    if n<CAD+HOR+5: return []
    px=g.close.to_numpy(float); hi=g.high.to_numpy(float); lo=g.low.to_numpy(float); vol=g.volume.to_numpy(float)
    op=float(g.open.iloc[0]) or px[0]
    typ=(hi+lo+px)/3.0; cv=np.cumsum(vol); vwap=np.where(cv>0,np.cumsum(typ*vol)/np.maximum(cv,1e-9),px)
    rhi=np.maximum.accumulate(hi); rlo=np.minimum.accumulate(lo); r1=np.zeros(n); r1[1:]=px[1:]/np.maximum(px[:-1],1e-9)-1
    out=[]
    for t in range(CAD,n-HOR,CAD):
        p=px[t]
        if p<=0: continue
        w5,w15,w30=max(0,t-5),max(0,t-15),max(0,t-30)
        row={"minute_idx":t,"ret_session":p/op-1,"ret_5m":p/max(px[w5],1e-9)-1,"ret_15m":p/max(px[w15],1e-9)-1,
             "ret_30m":p/max(px[w30],1e-9)-1,"vwap_dist":p/max(vwap[t],1e-9)-1,"high_dist":p/max(rhi[t],1e-9)-1,
             "low_dist":p/max(rlo[t],1e-9)-1,"range_pos":(p-rlo[t])/max(rhi[t]-rlo[t],1e-9),
             "rvol_cum":cv[t]/max(cv[min(t,30)]/max(min(t,30),1),1e-9),
             "vol_accel_5m":vol[w5:t].sum()/max(vol[max(0,t-10):w5].sum(),1e-9),
             "realized_vol_15m":float(np.std(r1[w15:t+1])) if t-w15>2 else 0.0,
             "up_min_frac_15m":float((r1[w15:t+1]>0).mean()) if t>w15 else 0.5}
        fh=hi[t+1:t+1+HOR]; fl=lo[t+1:t+1+HOR]
        up_h=np.where(fh>=p*(1+UP))[0]; dn_h=np.where(fl<=p*(1-DN))[0]
        tu=up_h[0] if len(up_h) else 10**9; td=dn_h[0] if len(dn_h) else 10**9
        row["continued"]=int(tu<td)
        out.append(row)
    return out

def main():
    con=duckdb.connect()
    print("computing eligible pool (gate, all years)...")
    elig=con.execute(f"""
      WITH b AS (SELECT ticker, ts_et::DATE d, EXTRACT(year FROM ts_et) yr, open, close, volume,
                  lag(close) OVER (PARTITION BY ticker ORDER BY ts_et) pc,
                  avg(volume*close) OVER (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) adv20
                 FROM read_parquet('{DA}'))
      SELECT ticker, d, yr, open AS price_level, adv20 FROM b
      WHERE pc>0 AND open/pc-1>=0.08 AND open BETWEEN 0.50 AND 20.0 AND adv20>=1e6""").df()
    elig["d"]=pd.to_datetime(elig.d); elig["ym"]=elig.d.dt.strftime("%Y-%m"); elig["dstr"]=elig.d.dt.strftime("%Y-%m-%d")
    ctx={(r.ticker,r.dstr):(r.price_level,r.adv20,int(r.yr)) for r in elig.itertuples()}
    print(f"  eligible ticker-days: {len(elig)} across {elig.ym.nunique()} months")
    allrows=[]
    for ym,grp in elig.groupby("ym"):
        yr=int(ym[:4]); tks=grp.ticker.unique().tolist(); dates=set(grp.dstr)
        tklist=",".join("'"+t.replace("'","")+"'" for t in tks)
        m0=grp.d.min().strftime("%Y-%m-%d"); m1=grp.d.max().strftime("%Y-%m-%d")
        bars=con.execute(f"""SELECT ticker, ts_et, open, high, low, close, volume
            FROM read_parquet('{MN}', hive_partitioning=1)
            WHERE year={yr} AND ts_et::DATE BETWEEN DATE '{m0}' AND DATE '{m1}' AND ticker IN ({tklist})""").df()
        if bars.empty: continue
        bars["ts_et"]=pd.to_datetime(bars.ts_et,utc=True).dt.tz_convert("America/New_York")
        bars["dstr"]=bars.ts_et.dt.strftime("%Y-%m-%d")
        mins=bars.ts_et.dt.hour*60+bars.ts_et.dt.minute
        bars=bars[(mins>=570)&(mins<960)]
        cnt=0
        for (tk,d),g in bars.groupby(["ticker","dstr"]):
            if (tk,d) not in ctx: continue
            rr=label_day(g)
            if not rr: continue
            pl,adv,y=ctx[(tk,d)]
            for row in rr:
                row["ticker"]=tk; row["session_date"]=d; row["year"]=y; row["base_close"]=pl; row["adv20"]=adv
            allrows.extend(rr); cnt+=1
        print(f"  {ym}: {len(bars):,} RTH bars -> {cnt} ticker-days labeled (cum rows {len(allrows):,})")
    df=pd.DataFrame(allrows)
    df.to_parquet(OUT,index=False)
    print(f"\nWROTE {OUT}: {len(df):,} rows, {df.groupby(['ticker','session_date']).ngroups} ticker-days")
    print(df.groupby('year').agg(rows=('continued','size'), tdays=('session_date','nunique'),
                                 contrate=('continued','mean')))

if __name__=="__main__": main()
