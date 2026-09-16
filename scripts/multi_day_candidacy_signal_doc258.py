"""doc 258 - THE SLOW GAME, first brick. NO live change. Does the one real signal we found (the candidacy /
dilution-distribution signature) pay on a MULTI-DAY hold, where the intraday spread can't eat it? Cross-regime.

Setup: gap-up candidacy universe (gap>=8%, $0.5-20, ADV>=$1M) from day_aggs. Enter at the GAP-DAY CLOSE (signal
known then), hold k days, measure forward close->close return. The forensics found: dilution = "the rally is sold
into" + exhausted prior-runners fade. Computable bearish-structure proxies: RECENT REVERSE SPLIT (the dilution-
engineered shells), PRIOR-RUNNER (exhausted), MICRO-FLOAT. Test whether these predict the forward multi-day
return (a shortable fade), and whether FRESH candidacy names hold up. Long-short, cross-regime, cost-aware.

PRE-REGISTERED: a real slow-horizon edge IFF the long(fresh)/short(dilution-exhausted) spread is positive in ALL
3 regimes AND CI-separated, NET of a ~0.6% round-trip cost (borrow on the short side flagged separately).
"""
from __future__ import annotations
import numpy as np, pandas as pd, duckdb
DA="data/polygon_warehouse/day_aggs/**/*.parquet"; SPLITS="data/polygon_warehouse/reference/splits.parquet"; TD="data/polygon_warehouse/reference/ticker_details.parquet"
RT_COST=0.006   # ~0.6% round-trip (small-cap), one round trip over the whole multi-day hold

def main():
    con=duckdb.connect()
    print("building gap-up candidacy universe + forward multi-day returns + signals from day_aggs...")
    df=con.execute(f"""
      WITH r AS (SELECT ticker, ts_et, EXTRACT(year FROM ts_et) yr, open, high, low, close, volume,
                   lag(close) OVER w pc1, close/lag(close) OVER w -1 ret1
                 FROM read_parquet('{DA}',hive_partitioning=1) WINDOW w AS (PARTITION BY ticker ORDER BY ts_et)),
      b AS (SELECT ticker, ts_et::DATE d, yr, open, close, pc1,
              avg(volume*close) OVER w20 adv20, max(close) OVER w10 hi10, min(close) OVER w10 lo10, max(ret1) OVER w10 mx10,
              lead(close,1) OVER w c1, lead(close,3) OVER w c3, lead(close,5) OVER w c5, lead(close,10) OVER w c10, lead(close,20) OVER w c20
            FROM r WINDOW w AS (PARTITION BY ticker ORDER BY ts_et),
                          w20 AS (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING),
                          w10 AS (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING))
      SELECT yr, ticker, d::VARCHAR d, open price, open/pc1-1 gap,
             (hi10/nullif(lo10,0)>=1.8 OR mx10>=0.50)::INT prior_runner,
             c1/close-1 f1, c3/close-1 f3, c5/close-1 f5, c10/close-1 f10, c20/close-1 f20
      FROM b WHERE pc1>0 AND open>0 AND adv20>=1e6 AND open BETWEEN 0.5 AND 20 AND open/pc1-1>=0.08 AND lo10>0 AND c20 IS NOT NULL
    """).df()
    df=df[df.yr.isin([2024,2025,2026])].copy()
    df['prior_runner']=pd.to_numeric(df.prior_runner,errors='coerce').fillna(0).astype(int)
    for h in ['f1','f3','f5','f10','f20']: df[h]=pd.to_numeric(df[h],errors='coerce')
    # signal joins: recent reverse split (<=90d) + micro-float
    tks="','".join(sorted(set(df.ticker.str.replace("'",""))))
    rs=con.execute(f"SELECT DISTINCT ticker, execution_date FROM read_parquet('{SPLITS}') WHERE ticker IN ('{tks}') AND split_from>split_to").df()
    rs['execution_date']=pd.to_datetime(rs.execution_date); rsby={}; [rsby.setdefault(r.ticker,[]).append(r.execution_date) for r in rs.itertuples()]
    td=con.execute(f"SELECT ticker, share_class_shares_outstanding sh FROM read_parquet('{TD}') WHERE ticker IN ('{tks}')").df()
    shmap={r.ticker:r.sh for r in td.itertuples()}
    df['dd']=pd.to_datetime(df.d)
    df['recent_rsplit']=[int(any((x<=dd)&(x>=dd-pd.Timedelta(days=90)) for x in rsby.get(tk,[]))) for tk,dd in zip(df.ticker,df.dd)]
    df['micro_float']=[int(0<shmap.get(tk,np.nan)<5e6) if shmap.get(tk,np.nan)==shmap.get(tk,np.nan) else 0 for tk in df.ticker]
    df['bearish']=((df.recent_rsplit==1)|(df.prior_runner==1)).astype(int)   # dilution-engineered OR exhausted
    print(f"gap-up candidacy stock-days: {len(df):,} | bearish-signature {df.bearish.mean()*100:.0f}% (rsplit {df.recent_rsplit.mean()*100:.0f}%, prior-runner {df.prior_runner.mean()*100:.0f}%)\n")

    def boot(x,reps=2000,seed=7):
        x=x[~np.isnan(x)];
        if len(x)<10: return (np.nan,np.nan)
        rng=np.random.default_rng(seed); m=[x[rng.integers(0,len(x),len(x))].mean() for _ in range(reps)]
        return np.percentile(m,2.5),np.percentile(m,97.5)

    print("=== FORWARD multi-day mean return (close->close), FRESH vs BEARISH-signature, per regime ===")
    print(f"  {'horizon':>8}{'grp':>9}" + "".join(f"{y:>10}" for y in [2024,2025,2026]) + f"{'ALL':>10}")
    for h in ['f1','f3','f5','f10','f20']:
        for grp,sub in [("FRESH",df[df.bearish==0]),("BEARISH",df[df.bearish==1])]:
            cells="".join(f"{sub[sub.yr==y][h].mean()*100:>+9.2f}%" for y in [2024,2025,2026])
            print(f"  {h:>8}{grp:>9}{cells}{sub[h].mean()*100:>+9.2f}%")
    print("\n=== LONG-SHORT: long FRESH, short BEARISH, held 10 days, NET of ~0.6% round-trip (per leg), cross-regime ===")
    print(f"  {'regime':>8}{'long f10':>10}{'short f10':>11}{'L-S gross':>11}{'L-S net':>10}{'95% CI (net)':>18}")
    for y in [2024,2025,2026,'ALL']:
        s=df if y=='ALL' else df[df.yr==y]
        lo=s[s.bearish==0].f10.to_numpy(); sh=s[s.bearish==1].f10.to_numpy()
        lmean=np.nanmean(lo); smean=np.nanmean(sh); gross=lmean-smean; net=gross-2*RT_COST
        # bootstrap the L-S net diff
        rng=np.random.default_rng(7); d=[lo[rng.integers(0,len(lo),len(lo))].mean()-sh[rng.integers(0,len(sh),len(sh))].mean()-2*RT_COST for _ in range(3000)]
        print(f"  {str(y):>8}{lmean*100:>+9.2f}%{smean*100:>+10.2f}%{gross*100:>+10.2f}%{net*100:>+9.2f}%{('[%+.2f,%+.2f]'%(np.percentile(d,2.5)*100,np.percentile(d,97.5)*100)):>18}")
    print("\n  PRE-REGISTERED: real slow-horizon edge IFF L-S net is POSITIVE in ALL 3 regimes AND CI excludes 0.")
    print("  Caveat: the SHORT leg needs borrow (HTB on these names) - net shown is pre-borrow; long-only fade-avoid is the capturable part.")

if __name__=="__main__": main()
