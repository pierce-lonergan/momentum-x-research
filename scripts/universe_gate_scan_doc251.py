"""doc 251 - UNIVERSE GATE SCAN (the "different game" search, part 1). NO live change.

doc 250 proved the $0.50-$20 low-float gapper basket is zero-EV at the open. Is that specific to that corner,
or is the open efficient ACROSS universes? Fast, comprehensive first cut from day_aggs ALONE: for every
gap-up stock-day (superset), compute the OPEN->CLOSE return (close/open-1, no minute join needed), slice by a
grid of gates (gap x price x ADV x direction), and find ANY corner whose CROSS-REGIME basket mean is positive
(long) or negative enough to short (the fade). Any positive corner gets drilled with 9:50 minute precision next.
Caveat: open->close approximates the 9:50 entry (skips the first 20m); a broad scan to locate +EV corners.
"""
from __future__ import annotations
import numpy as np, pandas as pd, duckdb
DA="data/polygon_warehouse/day_aggs/**/*.parquet"

def main():
    con=duckdb.connect()
    print("computing per-stock-day gap/adv/return from day_aggs (all gap-ups >=3%)...")
    df=con.execute(f"""
      WITH b AS (
        SELECT ticker, ts_et::DATE d, EXTRACT(year FROM ts_et) yr, open, high, low, close, volume,
               lag(close) OVER (PARTITION BY ticker ORDER BY ts_et) pc,
               avg(volume*close) OVER (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) adv20
        FROM read_parquet('{DA}', hive_partitioning=1))
      SELECT ticker, d, yr, open, close, adv20, open/pc-1 AS gap, close/open-1 AS oc_ret
      FROM b WHERE pc>0 AND open>0 AND open/pc-1>=0.03 AND open BETWEEN 0.30 AND 500 AND adv20>=5e5
    """).df()
    df=df[df.yr.isin([2024,2025,2026])]
    print(f"superset gap-up stock-days: {len(df):,} ({df.d.min()}..{df.d.max()})\n")

    gap_buckets=[("3-5%",0.03,0.05),("5-8%",0.05,0.08),("8-15%",0.08,0.15),("15-30%",0.15,0.30),("30%+",0.30,9)]
    px_buckets=[("$0.5-2",0.5,2),("$2-5",2,5),("$5-20",5,20),("$20-50",20,50),("$50-200",50,200)]
    adv_buckets=[("ADV1-10M",1e6,1e7),("ADV10M+",1e7,1e12)]

    def boot(x,reps=1500,seed=7):
        x=x[~np.isnan(x)]
        if len(x)<10: return (np.nan,np.nan)
        rng=np.random.default_rng(seed); m=[x[rng.integers(0,len(x),len(x))].mean() for _ in range(reps)]
        return np.percentile(m,2.5),np.percentile(m,97.5)

    print("=== OPEN->CLOSE basket mean return by gate (long; a strongly NEGATIVE cell = short-the-fade candidate) ===")
    print(f"  {'gap':>7}{'price':>9}{'adv':>10}{'n':>7}{'2024':>9}{'2025':>9}{'2026':>9}{'POOL24+25 (CI)':>22}")
    hits=[]
    for gn,glo,ghi in gap_buckets:
        for pn,plo,phi in px_buckets:
            for an,alo,ahi in adv_buckets:
                s=df[(df.gap>=glo)&(df.gap<ghi)&(df.open>=plo)&(df.open<phi)&(df.adv20>=alo)&(df.adv20<ahi)]
                if len(s)<100: continue
                r={y:s[s.yr==y].oc_ret.mean() for y in [2024,2025,2026]}
                pool=s[s.yr.isin([2024,2025])].oc_ret.to_numpy(); lo,hi=boot(pool); pm=np.nanmean(pool)
                flag=""
                if lo>0: flag=" <== LONG +EV (CI>0)"; hits.append((gn,pn,an,"LONG",pm,lo,hi,len(s)))
                if hi<-0.01: flag=" <== SHORT candidate (CI<-1%)"; hits.append((gn,pn,an,"SHORT",pm,lo,hi,len(s)))
                print(f"  {gn:>7}{pn:>9}{an:>10}{len(s):>7}{r[2024]*100:>+8.1f}%{r[2025]*100:>+8.1f}%{r[2026]*100:>+8.1f}%"
                      f"{('%+.1f%% [%+.1f,%+.1f]'%(pm*100,lo*100,hi*100)):>22}{flag}")
    print(f"\n=== {len(hits)} corner(s) with a CI-separated edge (pooled 2024+2025) ===")
    for h in sorted(hits,key=lambda x:-abs(x[4])):
        print(f"  {h[3]:>5} {h[0]}/{h[1]}/{h[2]}: pooled {h[4]*100:+.1f}% CI[{h[5]*100:+.1f},{h[6]*100:+.1f}] n={h[7]}")
    if not hits: print("  NONE. The open is efficient across every gate -> open-entry has no edge in any corner (long or short).")
    print("\n  NOTE: open->close ~approximates the 9:50 entry; SHORT cells ignore borrow/HTB cost (steep on small-caps).")
    print("  Any LONG +EV corner -> drill with 9:50 minute precision + costs next.")

if __name__=="__main__": main()
