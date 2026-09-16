"""doc 250 - ASYMMETRY-HARVEST backtest (selection -> execution pivot). NO live change.

Selection is closed (doc 249). New question: if you CAN'T pick the rocket but the oracle tail is +40%, can a
BROAD gapper basket + a sharply ASYMMETRIC exit (cut the -7/-35% faders fast, ride the rare +40% rockets) flip
the basket EXPECTANCY positive cross-regime? doc 235 showed the universe MEAN (symmetric hold) buys variance not
expectancy; the asymmetric EXIT is the untested lever. Simulate real exit policies on the REAL intraday minute
paths (09:50 entry -> 16:00), equal-weight across ALL candidates, per regime. The per-regime result is also the
regime-timing signal (#2: which regimes are positive).

Entry = 09:50 bar close. Exits simulated bar-by-bar (intrabar stop on low, trail on running high), else EOD close.
Source = local minute_aggs (no API). Decision = does any asymmetric policy make 2024 AND 2025 basket-positive?
"""
from __future__ import annotations
import numpy as np, pandas as pd, duckdb
from zoneinfo import ZoneInfo
ET=ZoneInfo("America/New_York")
MN="data/polygon_warehouse/minute_aggs/**/*.parquet"
CORPUS="data/research/rocket_tick_features_xregime.parquet"

# exit policies: name -> (stop_pct, trail_pct)  (None = not used). EOD hold has neither.
POLICIES={
 "EOD hold":        (None, None),
 "stop -5%":        (0.05, None),
 "stop -8%":        (0.08, None),
 "trail 15%":       (None, 0.15),
 "trail 25%":       (None, 0.25),
 "ASYM -5%/trail20":(0.05, 0.20),   # cut faders fast, ride rockets wide
 "ASYM -8%/trail30":(0.08, 0.30),
}

def sim_exit(entry, highs, lows, closes, stop_pct, trail_pct):
    """bar-by-bar from just after 09:50 to EOD. Returns realized return (exit/entry-1)."""
    run_hi=entry
    for h,l,c in zip(highs,lows,closes):
        run_hi=max(run_hi,h)
        if stop_pct is not None and l<=entry*(1-stop_pct):      # intrabar stop on the low
            return -stop_pct
        if trail_pct is not None and l<=run_hi*(1-trail_pct):   # trail off the running high (checked on low)
            return run_hi*(1-trail_pct)/entry-1
    return closes[-1]/entry-1 if len(closes) else 0.0           # EOD close

def main():
    con=duckdb.connect()
    cand=con.execute(f"SELECT ticker,session_date,year,rocket,eod FROM read_parquet('{CORPUS}')").df()
    cand["ym"]=cand.session_date.str.slice(0,7)
    paths={}   # (ticker,date) -> (entry, highs[], lows[], closes[])
    for ym,grp in cand.groupby("ym"):
        yr=int(ym[:4]); tks=grp.ticker.unique().tolist()
        tklist=",".join("'"+t.replace("'","")+"'" for t in tks)
        m0=grp.session_date.min(); m1=grp.session_date.max()
        bars=con.execute(f"""SELECT ticker, ts_et, open, high, low, close FROM read_parquet('{MN}', hive_partitioning=1)
            WHERE year={yr} AND ts_et::DATE BETWEEN DATE '{m0}' AND DATE '{m1}' AND ticker IN ({tklist})""").df()
        if bars.empty: continue
        bars["ts_et"]=pd.to_datetime(bars.ts_et,utc=True).dt.tz_convert(ET)
        bars["d"]=bars.ts_et.dt.strftime("%Y-%m-%d"); mn=bars.ts_et.dt.hour*60+bars.ts_et.dt.minute
        bars=bars[(mn>=590)&(mn<960)]           # 09:50 -> 16:00
        keys=set(map(tuple,grp[["ticker","session_date"]].values))
        for (tk,d),g in bars.groupby(["ticker","d"]):
            if (tk,d) not in keys: continue
            g=g.sort_values("ts_et")
            entry=float(g.close.iloc[0])         # 09:50 bar close
            if entry<=0 or len(g)<2: continue
            paths[(tk,d)]=(entry, g.high.to_numpy(float)[1:], g.low.to_numpy(float)[1:], g.close.to_numpy(float)[1:])
        print(f"  {ym}: {len(grp)} candidates, paths so far {len(paths)}")
    print(f"\nintraday paths built for {len(paths)}/{len(cand)} candidates\n")

    # simulate every policy x candidate; assemble per-regime equal-weight basket mean return
    rows=[]
    for r in cand.itertuples():
        p=paths.get((r.ticker,r.session_date))
        rec={"year":r.year,"rocket":r.rocket,"eod":r.eod}
        for name,(sp,tp) in POLICIES.items():
            if p is None: rec[name]=r.eod if name=="EOD hold" else np.nan
            else: rec[name]=sim_exit(p[0],p[1],p[2],p[3],sp,tp)
        rows.append(rec)
    df=pd.DataFrame(rows)

    def boot_ci(x,reps=2000,seed=7):
        x=x[~np.isnan(x)];
        if len(x)<5: return (np.nan,np.nan)
        rng=np.random.default_rng(seed); m=[x[rng.integers(0,len(x),len(x))].mean() for _ in range(reps)]
        return np.percentile(m,2.5),np.percentile(m,97.5)

    print("=== BROAD-BASKET mean realized return per regime, by exit policy (equal-weight ALL gappers) ===")
    print(f"  {'policy':>18}" + "".join(f"{y:>11}" for y in [2024,2025,2026]) + f"{'POOL24+25':>14}")
    for name in POLICIES:
        cells=[]
        for y in [2024,2025,2026]:
            x=df[df.year==y][name].to_numpy(float); cells.append(f"{np.nanmean(x)*100:>+10.2f}%")
        x2=df[df.year.isin([2024,2025])][name].to_numpy(float); lo,hi=boot_ci(x2)
        cells.append(f"{np.nanmean(x2)*100:>+6.2f}%[{lo*100:+.1f},{hi*100:+.1f}]")
        print(f"  {name:>18}" + "".join(cells[:3]) + f"  {cells[3]}")
    # win-rate + tail check on the asymmetric policy
    a=df["ASYM -5%/trail20"]
    print(f"\n  ASYM -5%/trail20: win-rate {(a>0).mean()*100:.0f}% | median {a.median()*100:+.1f}% | mean {a.mean()*100:+.2f}% | p95 {a.quantile(.95)*100:+.0f}% | rocket-mean {a[df.rocket==1].mean()*100:+.0f}%")
    print("\n  DECISION: a policy WINS if its POOL24+25 basket mean is positive AND CI-separated from 0 (harvests the")
    print("  tail selection-free). If even the best asymmetric exit is <=0 cross-regime, the universe is not")
    print("  profitably tradeable at the open and the per-regime row IS the regime-timing signal (#2).")

if __name__=="__main__": main()
