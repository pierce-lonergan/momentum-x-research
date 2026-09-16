"""doc 252 - PRE-GAP ANTICIPATION (the "different game", part 2). NO live change.

The open is efficient BECAUSE the gap already happened (docs 250/251). The untested, higher-ceiling angle:
predict WHICH stocks gap up >=8% TOMORROW from prior-day price/volume action, position at today's close, and
CAPTURE the overnight gap (close_t -> open_{t+1}). A genuinely different problem: cross-sectional daily rare-
event prediction + overnight capture (not intraday reaction). Honest prior is low (gaps are mostly news-driven),
but it's the last untested lever and the capture is large (+8%+) if predictable.

Universe: tradeable (close $1-$500, trailing-20d $ADV >= $1M). Features (all <= close of day t): ret1/5/20,
rvol, range_pos, dollar-vol surge, dist-from-20d-high, today's gap, price. Label: open_{t+1}/close_t-1 >= 8%.
Money test (LORO, cross-regime): top-slice by predicted P(gap) -> mean OVERNIGHT return (close_t->open_{t+1}),
per regime. Wins iff the top-slice overnight return is positive cross-regime AND beats just-buy-everything.
"""
from __future__ import annotations
import numpy as np, pandas as pd, duckdb
from sklearn.ensemble import HistGradientBoostingClassifier as GBM
from sklearn.metrics import roc_auc_score
DA="data/polygon_warehouse/day_aggs/**/*.parquet"
FEATS=["ret1","ret5","ret20","rvol","range_pos","dvol_surge","dist_hh20","gap_today","price"]

def main():
    con=duckdb.connect()
    print("building cross-sectional daily dataset (features<=close_t, label=gap_up>=8% at open_t+1)...")
    df=con.execute(f"""
      WITH b AS (
        SELECT ticker, ts_et::DATE d, EXTRACT(year FROM ts_et) yr, open, high, low, close, volume,
          lag(close)   OVER w pc1, lag(close,5) OVER w pc5, lag(close,20) OVER w pc20,
          avg(volume)      OVER (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) av20,
          avg(volume*close)OVER (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) adv20,
          max(high)        OVER (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) hh20,
          lead(open)  OVER w nxo
        FROM read_parquet('{DA}', hive_partitioning=1)
        WINDOW w AS (PARTITION BY ticker ORDER BY ts_et))
      SELECT yr,
        close/pc1-1 ret1, close/pc5-1 ret5, close/pc20-1 ret20,
        volume/av20 rvol, (close-low)/nullif(high-low,0) range_pos,
        (volume*close)/adv20 dvol_surge, close/hh20-1 dist_hh20, open/pc1-1 gap_today, close price,
        nxo/close-1 AS overnight_ret, (nxo/close-1>=0.08)::INT AS gap8
      FROM b
      WHERE pc20>0 AND av20>0 AND hh20>0 AND high>low AND close BETWEEN 1 AND 500 AND adv20>=1e6 AND nxo IS NOT NULL
    """).df()
    df=df[df.yr.isin([2024,2025,2026])].copy()
    for c in FEATS: df[c]=df[c].astype(float).replace([np.inf,-np.inf],np.nan).fillna(0.0)
    base=df.gap8.mean()
    print(f"stock-days {len(df):,} | gap>=8% base rate {base*100:.2f}% | by year {df.groupby('yr').gap8.agg(['size','mean']).round(4).to_dict()}\n")

    print("=== LORO: predict next-day gap; trade top-slice at close_t, capture overnight (close_t->open_t+1) ===")
    print(f"  {'regime':>7}{'AUC':>7}{'allOVN':>9}" + "".join(f"{('top%.1f%%'%(f*100)):>11}" for f in [0.005,0.01,0.02]))
    held={}
    for ty in [2024,2025,2026]:
        tr=df[df.yr!=ty]; te=df[df.yr==ty].copy()
        te["score"]=GBM(max_iter=300,learning_rate=0.05,max_depth=4,l2_regularization=1.0,class_weight="balanced").fit(tr[FEATS],tr.gap8).predict_proba(te[FEATS])[:,1]
        held[ty]=te
        auc=roc_auc_score(te.gap8,te.score); allovn=te.overnight_ret.mean()
        cells=[]
        for f in [0.005,0.01,0.02]:
            top=te[te.score>=te.score.quantile(1-f)]
            cells.append(f"{top.overnight_ret.mean()*100:>+10.2f}%")
        print(f"  {ty:>7}{auc:>7.3f}{allovn*100:>+8.2f}%"+"".join(cells))
    # pooled 2024+2025 top-0.5% overnight return + CI + gap hit-rate
    p=pd.concat([held[2024],held[2025]]); thr=p.score.quantile(0.995); top=p[p.score>=thr]
    rng=np.random.default_rng(7); m=[top.overnight_ret.to_numpy()[rng.integers(0,len(top),len(top))].mean() for _ in range(3000)]
    lo,hi=np.percentile(m,2.5),np.percentile(m,97.5)
    print(f"\n  POOLED 2024+2025 top-0.5%: overnight mean {top.overnight_ret.mean()*100:+.2f}% 95%CI[{lo*100:+.2f},{hi*100:+.2f}] | "
          f"gap>=8% hit-rate {top.gap8.mean()*100:.1f}% (base {base*100:.2f}%) | n/day~{len(top)/ (p.shape[0]/df.yr.isin([2024,2025]).sum()) :.0f}")
    print("  WIN iff top-slice overnight is POSITIVE cross-regime AND CI-separated >0 (beats holding nothing). Overnight")
    print("  holding has gap-DOWN risk + open slippage; a small gross edge likely dies net. Honest prior: low.")

if __name__=="__main__": main()
