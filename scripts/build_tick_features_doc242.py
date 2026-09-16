"""doc 242 (BET#3) — ex-ante TICK microstructure features for rocket detection + the discrimination test.

trades_v1_parquet is partitioned year/month/day/ticker -> read ONE ticker-day's trades via its exact path
(no full scan). Trades-only (no quotes) -> OFI via TICK-RULE (uptick=buy/downtick=sell). Ex-ante window =
premarket(04:00-09:30) + 9:30-9:50 (entry at 9:50). Coverage 2025-08..2026-04 (within-window test only).

THE TEST: does adding tick features to the 5-min-OHLCV microstructure improve rocket-vs-fader
discrimination? Metric = rocket AUC + top-slice precision/realized-return (the homogeneity that the
OHLCV-only model couldn't break in 2024-25). NO live change.

Usage: python scripts/build_tick_features_doc242.py [--sample N]
"""
from __future__ import annotations
import argparse, glob, os, numpy as np, pandas as pd, duckdb
from sklearn.ensemble import HistGradientBoostingClassifier as GBM
from sklearn.metrics import roc_auc_score

TRADES="data/polygon_warehouse/trades_v1_parquet"
ENTRY=3; R=0.30
MICRO=["ret_session","ret_5m","ret_15m","ret_30m","vwap_dist","high_dist","low_dist","range_pos",
       "rvol_cum","vol_accel_5m","realized_vol_15m","up_min_frac_15m","base_close","adv20"]
TICK=["pm_vol","pm_dollarvol","early_vol","early_trades","tick_ofi","tick_ofi_late","large_print_ratio",
      "mean_trade_size","trade_intensity","tick_vwap_dist","odd_lot_ratio"]

def corpus():
    df=duckdb.connect().execute("SELECT * FROM read_parquet('data/research/exit_labels_cross_regime.parquet')").df()
    rows=[]
    for (tk,d),g in df.groupby(["ticker","session_date"]):
        g=g.sort_values("minute_idx").reset_index(drop=True)
        if len(g)<=ENTRY+6 or (1+g.ret_session.iloc[ENTRY])<=0: continue
        rel=1+g.ret_session.to_numpy(); eod=rel[-1]/rel[ENTRY]-1
        runhi=rel[ENTRY:].max()/rel[ENTRY]; sustain=(rel[-1]-rel[ENTRY])/(runhi*rel[ENTRY]-rel[ENTRY]) if runhi>1+1e-6 else 0
        row=g.iloc[ENTRY][MICRO[:-2]].to_dict()
        row.update(ticker=tk,session_date=d,base_close=float(g.base_close.iloc[0]),adv20=float(g.adv20.iloc[0]),
                   year=int(g.year.iloc[0]),eod=eod,rocket=int(eod>=R and sustain>=0.5))
        rows.append(row)
    dt=pd.DataFrame(rows)
    # tick coverage window: 2025-08 .. 2026-04
    dt=dt[(dt.session_date>="2025-08-01")&(dt.session_date<="2026-04-30")].copy()
    return dt

def tick_feats(tk, d):
    y,m,dd=d[:4],d[5:7],d[8:10]
    path=f"{TRADES}/year={y}/month={m}/day={dd}/ticker={tk}/data_0.parquet"
    if not os.path.exists(path): return None
    try:
        t=duckdb.connect().execute(f"""SELECT ts_et, price, size, conditions FROM read_parquet('{path}')
            WHERE price>0 AND size>0 ORDER BY ts_et""").df()
    except Exception: return None
    if len(t)<5: return None
    mins=t.ts_et.dt.hour*60+t.ts_et.dt.minute
    pm=t[(mins>=240)&(mins<570)]; early=t[(mins>=570)&(mins<590)]   # premarket; 9:30-9:50
    if len(early)<3: return None
    px=early['price'].to_numpy(); sz=early['size'].to_numpy().astype(float)
    sign=np.sign(np.diff(px,prepend=px[0]));
    for i in range(1,len(sign)):
        if sign[i]==0: sign[i]=sign[i-1]    # tick-rule carry-forward
    tot=sz.sum(); ofi=float((sign*sz).sum()/tot) if tot>0 else 0.0
    half=len(early)//2
    late_sz=sz[half:]; late_sign=sign[half:]; ofi_late=float((late_sign*late_sz).sum()/max(late_sz.sum(),1)) if late_sz.sum()>0 else 0.0
    vwap=float((px*sz).sum()/tot) if tot>0 else px[-1]
    odd=float((sz<100).sum()/len(sz))
    return dict(pm_vol=float(pm['size'].sum()), pm_dollarvol=float((pm.price*pm['size']).sum()),
                early_vol=float(tot), early_trades=float(len(early)),
                tick_ofi=ofi, tick_ofi_late=ofi_late,
                large_print_ratio=float(sz[sz>=5000].sum()/tot) if tot>0 else 0.0,
                mean_trade_size=float(sz.mean()), trade_intensity=float(len(early)/20.0),
                tick_vwap_dist=float(px[-1]/vwap-1), odd_lot_ratio=odd)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--sample",type=int,default=0); a=ap.parse_args()
    dt=corpus()
    print(f"tick-covered gapper ticker-days (2025-08..2026-04): {len(dt)} | rocket rate {dt.rocket.mean()*100:.2f}% | by year {dt.groupby('year').rocket.agg(['size','mean']).round(3).to_dict()}")
    if a.sample: dt=dt.sample(min(a.sample,len(dt)),random_state=1).reset_index(drop=True)
    feats=[]; miss=0
    for i,r in enumerate(dt.itertuples()):
        tf=tick_feats(r.ticker,r.session_date)
        if tf is None: miss+=1; tf={k:np.nan for k in TICK}
        feats.append(tf)
        if (i+1)%500==0: print(f"  ...{i+1}/{len(dt)} ({miss} missing ticks)")
    tdf=pd.concat([dt.reset_index(drop=True),pd.DataFrame(feats)],axis=1)
    print(f"tick-feature coverage: {len(tdf)-tdf.tick_ofi.isna().sum()}/{len(tdf)} have ticks ({miss} missing partitions)")
    tdf=tdf.dropna(subset=["tick_ofi"]).reset_index(drop=True)
    for c in MICRO+TICK: tdf[c]=tdf[c].astype(float).replace([np.inf,-np.inf],np.nan).fillna(0)
    os.makedirs("data/research",exist_ok=True); tdf.to_parquet("data/research/rocket_tick_features.parquet",index=False)
    print(f"wrote data/research/rocket_tick_features.parquet ({len(tdf)} rows)")

    # ---- discrimination test: micro-only vs micro+tick (CPCV within-window) ----
    udays=sorted(tdf.session_date.unique()); grp={x:i for i,gg in enumerate(np.array_split(udays,6)) for x in gg}
    tdf["_g"]=tdf.session_date.map(grp)
    def cv_auc(feat):
        s=tdf.copy(); s["p"]=np.nan
        for gi in range(6):
            tr=s[s._g!=gi]; te=s.index[s._g==gi]
            if len(te) and tr.rocket.nunique()>1:
                s.loc[te,"p"]=GBM(max_iter=300,learning_rate=0.05,max_depth=4,l2_regularization=1.0,class_weight="balanced").fit(tr[feat],tr.rocket).predict_proba(s.loc[te,feat])[:,1]
        s=s.dropna(subset=["p"]); return s
    print("\n=== ROCKET discrimination: micro-only vs micro+TICK (CPCV within 2025-08..2026-04) ===")
    for name,feat in [("micro-only",MICRO),("micro+TICK",MICRO+TICK),("TICK-only",TICK)]:
        s=cv_auc(feat); auc=roc_auc_score(s.rocket,s.p)
        # top-slice precision + realized return
        line=f"  {name:12} AUC {auc:.3f} | "
        for topf in [0.02,0.05]:
            thr=s.p.quantile(1-topf); top=s[s.p>=thr]
            line+=f"top{int(topf*100)}%: prec {top.rocket.mean()*100:.0f}% ret {top.eod.mean()*100:+.1f}%/med {top.eod.median()*100:+.1f}%  "
        print(line)
    print("\n  WIN if micro+TICK lifts AUC and (esp.) the top-slice precision/return materially over micro-only.")
    print("  Coverage caveat: 2025-08..2026-04 only -> within-window; full cross-regime needs 2024/2025-H1 ticks.")

if __name__=="__main__": main()
