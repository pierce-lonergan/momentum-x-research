"""doc 254 - COILED-CATALYST lead (doc-253 B4) computable backtest from day_aggs. NO live change.

doc 253 found AIXI: a real catalyst absorbed on HUGE volume with the price FLAT, then +515% six days later.
Tests the PURE PRICE SIGNATURE (no news layer): does an anomalous-volume + flat-price day ("news absorbed,
not yet priced") predict an outsized FORWARD move (within 10 trading days), cross-regime? If yes, it's a cheap
deployable predictor; if no, the 'verifiable catalyst' qualifier (the agent/news layer) is doing the work.
"""
from __future__ import annotations
import numpy as np, pandas as pd, duckdb
DA="data/polygon_warehouse/day_aggs/**/*.parquet"

def main():
    con=duckdb.connect()
    print("computing coiled signal + forward 10d move from day_aggs...")
    df=con.execute(f"""
      WITH b AS (
        SELECT ticker, ts_et::DATE d, EXTRACT(year FROM ts_et) yr, open, high, low, close, volume,
          lag(close) OVER w pc1,
          avg(volume)       OVER (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) avol20,
          avg(volume*close) OVER (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) adv20,
          max(high)         OVER (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 1 FOLLOWING AND 10 FOLLOWING) fwd_hi10,
          max(close)        OVER (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 1 FOLLOWING AND 10 FOLLOWING) fwd_close10,
          count(*)          OVER (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 1 FOLLOWING AND 10 FOLLOWING) fwd_n
        FROM read_parquet('{DA}', hive_partitioning=1) WINDOW w AS (PARTITION BY ticker ORDER BY ts_et))
      SELECT yr, close, volume/avol20 rvol, abs(close/open-1) flat_intraday, abs(close/pc1-1) flat_cc,
             fwd_hi10/close-1 fwd_max_hi, fwd_close10/close-1 fwd_max_close
      FROM b
      WHERE pc1>0 AND open>0 AND avol20>0 AND adv20>=5e5 AND close BETWEEN 0.5 AND 20 AND fwd_n>=8
    """).df()
    df=df[df.yr.isin([2024,2025,2026])].copy()
    print(f"tradeable stock-days: {len(df):,}\n")
    base_rocket=(df.fwd_max_hi>=0.30).mean()
    print(f"BASE forward-10d: P(+30% pop) {base_rocket*100:.1f}% | mean fwd-max-high {df.fwd_max_hi.mean()*100:+.1f}%\n")

    print("=== COILED SIGNAL (rvol>=K AND flat price) -> forward 10d outcome, per regime ===")
    print(f"  {'rvol>=':>7}{'flat<=':>8}{'n':>8}{'P(+30% pop)':>13}{'lift':>6}{'mean fwdMaxHi':>14}  by-regime P(+30%) 24/25/26")
    for K in [5,10,20]:
        for F in [0.05,0.10]:
            s=df[(df.rvol>=K)&(df.flat_intraday<=F)&(df.flat_cc<=F)]
            if len(s)<50: continue
            pr=(s.fwd_max_hi>=0.30).mean(); lift=pr/base_rocket
            by={y:(s[s.yr==y].fwd_max_hi>=0.30).mean()*100 for y in [2024,2025,2026]}
            print(f"  {K:>7}{F:>8.2f}{len(s):>8}{pr*100:>12.1f}%{lift:>6.1f}{s.fwd_max_hi.mean()*100:>+13.1f}%   {by[2024]:.0f}/{by[2025]:.0f}/{by[2026]:.0f}%")
    print("\n  (fwd_max_hi = best high over next 10 trading days vs today's close; +30% pop ~ a forward rocket.)")
    print("  LEAD is real if the coiled signal LIFTS forward-pop probability materially over base, in ALL regimes.")
    print("  Caveat: fwd-max-HIGH is an upper bound (you can't capture the exact high); + small-cap exit slippage.")

if __name__=="__main__": main()
