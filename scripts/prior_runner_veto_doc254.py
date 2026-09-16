"""doc 254 - validate the FADE-VETO on the UNFILTERED gapper universe (addresses the discriminator's #1 caveat:
the fader set was curated, so the prior-runner inversion could be a selection artifact). NO live change.

The discriminator's one robust finding: faders disproportionately gap as EXHAUSTED PRIOR-RUNNERS (already had a
big run). Test it computationally on ALL gap>=8% stock-days (no curation): do prior-runner gappers ROCKET LESS
and FADE MORE than fresh gappers, cross-regime? If yes -> a real, deployable de-selection filter. If no -> the
inversion was a curation artifact.

prior-runner (exhausted) proxy from day_aggs: trailing-10d max/min close ratio >= 1.8 (the stock already ran
>=80% in the last 10 sessions) OR any single trailing-10d day >= +50%. rocket proxy: close/open >= +30%
(closed up 30%+ from the open = a sustained intraday rocket). fade: open->close return.
"""
from __future__ import annotations
import numpy as np, pandas as pd, duckdb
DA="data/polygon_warehouse/day_aggs/**/*.parquet"

def main():
    con=duckdb.connect()
    print("computing gappers + prior-runner flag + outcomes from day_aggs...")
    df=con.execute(f"""
      WITH r AS (
        SELECT ticker, ts_et, EXTRACT(year FROM ts_et) yr, open, high, low, close, volume,
               lag(close) OVER w pc1, close/lag(close) OVER w - 1 AS ret1
        FROM read_parquet('{DA}', hive_partitioning=1) WINDOW w AS (PARTITION BY ticker ORDER BY ts_et)),
      b AS (
        SELECT yr, open, close, pc1,
          avg(volume*close) OVER w20 adv20,
          max(close) OVER w10 hi10, min(close) OVER w10 lo10, max(ret1) OVER w10 maxday10
        FROM r
        WINDOW w20 AS (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING),
               w10 AS (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING))
      SELECT yr, open/pc1-1 gap, close/open-1 oc_ret, (close/open-1>=0.30)::INT rocket,
             (hi10/nullif(lo10,0)>=1.8 OR maxday10>=0.50)::INT prior_runner
      FROM b
      WHERE pc1>0 AND open>0 AND adv20>=5e5 AND close BETWEEN 0.5 AND 20 AND open/pc1-1>=0.08 AND lo10>0
    """).df()
    df=df[df.yr.isin([2024,2025,2026])].copy()
    print(f"gap>=8% stock-days: {len(df):,} | prior-runner share {df.prior_runner.mean()*100:.0f}%\n")

    def boot(x,reps=2000,seed=7):
        x=x[~np.isnan(x)]; rng=np.random.default_rng(seed)
        m=[x[rng.integers(0,len(x),len(x))].mean() for _ in range(reps)]
        return np.percentile(m,2.5),np.percentile(m,97.5)

    print("=== EXHAUSTED PRIOR-RUNNER vs FRESH gapper: rocket-rate (close>=+30%) + open->close, per regime ===")
    print(f"  {'group':>14}{'n':>8}{'rocket%':>9}{'meanOC':>9}{'  by-regime rocket% 24/25/26':>30}{'  OC 24/25/26':>22}")
    for lab,sub in [("FRESH",df[df.prior_runner==0]),("PRIOR-RUNNER",df[df.prior_runner==1])]:
        rk=sub.rocket.mean(); oc=sub.oc_ret.mean()
        rby={y:sub[sub.yr==y].rocket.mean()*100 for y in [2024,2025,2026]}
        oby={y:sub[sub.yr==y].oc_ret.mean()*100 for y in [2024,2025,2026]}
        print(f"  {lab:>14}{len(sub):>8}{rk*100:>8.1f}%{oc*100:>+8.1f}%   {rby[2024]:.1f}/{rby[2025]:.1f}/{rby[2026]:.1f}%   {oby[2024]:+.1f}/{oby[2025]:+.1f}/{oby[2026]:+.1f}%")
    # the veto's value: does AVOIDING prior-runners improve the gapper basket?
    fresh=df[df.prior_runner==0].oc_ret.to_numpy(); pr=df[df.prior_runner==1].oc_ret.to_numpy()
    lo,hi=boot(fresh-fresh.mean()*0+ (fresh)) if False else (None,None)
    d=df[df.prior_runner==0].oc_ret.mean()-df[df.prior_runner==1].oc_ret.mean()
    # paired-ish: bootstrap the difference in means
    rng=np.random.default_rng(7); diffs=[fresh[rng.integers(0,len(fresh),len(fresh))].mean()-pr[rng.integers(0,len(pr),len(pr))].mean() for _ in range(3000)]
    print(f"\n  FRESH minus PRIOR-RUNNER open->close: {d*100:+.2f}pp  95%CI[{np.percentile(diffs,2.5)*100:+.2f},{np.percentile(diffs,97.5)*100:+.2f}]")
    print(f"  rocket-rate FRESH {df[df.prior_runner==0].rocket.mean()*100:.1f}% vs PRIOR-RUNNER {df[df.prior_runner==1].rocket.mean()*100:.1f}%")
    print("\n  VETO VALIDATED if prior-runners ROCKET LESS and FADE MORE than fresh gappers, consistently cross-regime.")
    print("  (Computable, ex-ante, pre-open. A de-selection filter for the long watchlist.)")

if __name__=="__main__": main()
