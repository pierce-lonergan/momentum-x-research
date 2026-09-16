"""doc 252 - HOSTILE verification of the pre-gap anticipation positive (+1.0% CI[+0.58,+1.51] cross-regime).
A surprising positive after a long string of negatives gets the same adversarial rigor as the negatives.
Three kill-tests: (A) drop the gap/momentum autocorrelation features (is it more than 'gappers keep gapping'?);
(B) restrict to LIQUID names ADV>=$10M (are the open prints executable, or phantom?); (C) distribution +
realistic open-slippage haircut (outlier-driven? gap-down tail? survives net?). NO live change.
"""
from __future__ import annotations
import numpy as np, pandas as pd, duckdb
from sklearn.ensemble import HistGradientBoostingClassifier as GBM
from sklearn.metrics import roc_auc_score
DA="data/polygon_warehouse/day_aggs/**/*.parquet"
ALL=["ret1","ret5","ret20","rvol","range_pos","dvol_surge","dist_hh20","gap_today","price"]
NOMOM=["ret5","ret20","rvol","range_pos","dvol_surge","dist_hh20","price"]   # drop gap_today + ret1

def build():
    return duckdb.connect().execute(f"""
      WITH b AS (
        SELECT ticker, ts_et::DATE d, EXTRACT(year FROM ts_et) yr, open, high, low, close, volume,
          lag(close) OVER w pc1, lag(close,5) OVER w pc5, lag(close,20) OVER w pc20,
          avg(volume) OVER (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) av20,
          avg(volume*close) OVER (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) adv20,
          max(high) OVER (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) hh20,
          lead(open) OVER w nxo
        FROM read_parquet('{DA}', hive_partitioning=1)
        WINDOW w AS (PARTITION BY ticker ORDER BY ts_et))
      SELECT yr, adv20,
        close/pc1-1 ret1, close/pc5-1 ret5, close/pc20-1 ret20, volume/av20 rvol,
        (close-low)/nullif(high-low,0) range_pos, (volume*close)/adv20 dvol_surge,
        close/hh20-1 dist_hh20, open/pc1-1 gap_today, close price,
        nxo/close-1 AS ovn, (nxo/close-1>=0.08)::INT AS gap8
      FROM b WHERE pc20>0 AND av20>0 AND hh20>0 AND high>low AND close BETWEEN 1 AND 500 AND adv20>=1e6 AND nxo IS NOT NULL
    """).df()

def loro_top(df, feats, frac=0.005):
    df=df.copy()
    for c in feats: df[c]=df[c].astype(float).replace([np.inf,-np.inf],np.nan).fillna(0.0)
    held={}
    for ty in [2024,2025,2026]:
        tr=df[df.yr!=ty]; te=df[df.yr==ty].copy()
        te["s"]=GBM(max_iter=300,learning_rate=0.05,max_depth=4,l2_regularization=1.0,class_weight="balanced").fit(tr[feats],tr.gap8).predict_proba(te[feats])[:,1]
        held[ty]=te
    p=pd.concat([held[2024],held[2025]]); top=p[p.s>=p.s.quantile(1-frac)]
    rng=np.random.default_rng(7); m=[top.ovn.to_numpy()[rng.integers(0,len(top),len(top))].mean() for _ in range(3000)]
    perreg={ty:held[ty][held[ty].s>=held[ty].s.quantile(1-frac)].ovn.mean() for ty in [2024,2025,2026]}
    return top, (np.percentile(m,2.5),np.percentile(m,97.5)), perreg

def main():
    df=build()
    print(f"stock-days {len(df):,} | gap8 base {df.gap8.mean()*100:.2f}%\n")
    def report(name, sub, feats):
        top,ci,pr=loro_top(sub,feats)
        ov=top.ovn.to_numpy()
        slip=0.005   # ~0.5% one-way open slippage haircut (conservative for an open-print exit)
        print(f"  {name:<22} n_top {len(top):>6} | per-regime 24/25/26 {pr[2024]*100:+.2f}/{pr[2025]*100:+.2f}/{pr[2026]*100:+.2f}%")
        print(f"  {'':<22} pooled mean {ov.mean()*100:+.2f}% CI[{ci[0]*100:+.2f},{ci[1]*100:+.2f}] | median {np.median(ov)*100:+.2f}% "
              f"| win {np.mean(ov>0)*100:.0f}% | p5 {np.percentile(ov,5)*100:+.1f}% | net(-slip) {(ov.mean()-slip)*100:+.2f}%")
    print("=== (A) FULL features (headline) ===");            report("full",        df, ALL)
    print("=== (A) NO gap/momentum (gap_today,ret1 dropped) ==="); report("no-momentum", df, NOMOM)
    print("=== (B) LIQUID only ADV>=$10M (executable opens) ==="); report("liquid ADV>=10M", df[df.adv20>=1e7], ALL)
    print("=== (B) LIQUID + no-momentum ===");                 report("liquid+no-mom", df[df.adv20>=1e7], NOMOM)
    print("\n  VERDICT: REAL lead iff the edge survives (a) dropping gap/momentum, (b) liquid-only, (c) net of ~0.5% slip,")
    print("  with a tolerable p5 downside. If it collapses under any -> it was autocorrelation/phantom-open/outliers.")

if __name__=="__main__": main()
