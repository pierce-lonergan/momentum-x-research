"""doc 247 Phase 0 - the FREE, no-GPU decision diagnostic. Retires or escalates the architecture bet.

Question: is the rocket right-tail ex-ante SEPARABLE from the 25 features cross-regime, or is the
regime-dependence a DATA truth no architecture fixes? Method = leave-one-regime-out (train on the other two
regimes, score the fully-unseen held-out regime), comparing:
  - GBM (class-balanced HistGBM)            : the deployed baseline; CAN memorize the 30 2026 rockets.
  - TabPFN v2 (frozen in-context Bayesian)  : the OVERFIT-PROOF REFEREE; weights never train -> cannot memorize.
  - kNN rocket-purity                       : pure separability probe (local rocket density in feature space).
  - order-flow proxy                        : large_print_ratio high & odd_lot_ratio low & tick_ofi_late>0.
  - ORACLE                                  : top-slice if we could cheat (the ceiling).
Binding readout per regime = top-5% mean realized EOD-from-9:50 return (+ day-block bootstrap CI on GBM/TabPFN).

DECISION (pre-registered):
  * If TabPFN's 2024 & 2025 top-slice is MATERIALLY LESS NEGATIVE than the GBM's -> the GBM's 2026 win was
    partly capacity-overfit and a calibrated edge survives -> ESCALATE (Phase 1 loss work, then RocketSetNet).
  * If TabPFN ~= GBM (equally negative) AND kNN separability ~ base-rate in 2024/2025 -> rockets and faders are
    interleaved in this space; capacity was never the constraint -> DATA TRUTH -> pivot to the data levers
    (free-float, news content). No encoder can separate what is not separable from these inputs.
NO live change.
"""
from __future__ import annotations
import numpy as np, pandas as pd, duckdb
from sklearn.ensemble import HistGradientBoostingClassifier as GBM
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import QuantileTransformer
from sklearn.metrics import roc_auc_score

CORPUS="data/research/rocket_tick_features_xregime.parquet"
MICRO=["ret_session","ret_5m","ret_15m","ret_30m","vwap_dist","high_dist","low_dist","range_pos",
       "rvol_cum","vol_accel_5m","realized_vol_15m","up_min_frac_15m","base_close","adv20"]
TICK=["pm_vol","pm_dollarvol","early_vol","early_trades","tick_ofi","tick_ofi_late","large_print_ratio",
      "mean_trade_size","trade_intensity","tick_vwap_dist","odd_lot_ratio"]
FEATS=MICRO+TICK
TOPF=0.05

def load():
    dt=duckdb.connect().execute(f"SELECT * FROM read_parquet('{CORPUS}')").df()
    for c in FEATS: dt[c]=dt[c].astype(float).replace([np.inf,-np.inf],np.nan).fillna(0.0)
    return dt

def topslice(te):
    thr=te.score.quantile(1-TOPF); top=te[te.score>=thr]
    return dict(n=len(top), mean=float(top.eod.mean()), median=float(top.eod.median()),
                prec=float(top.rocket.mean()))

def dayblock_ci(te, reps=2000, seed=7):
    """day-block bootstrap CI on the top-5% mean EOD (resample whole session_dates)."""
    days=te.session_date.unique(); rng=np.random.default_rng(seed); ms=[]
    by={d:g for d,g in te.groupby("session_date")}
    for _ in range(reps):
        samp=pd.concat([by[d] for d in rng.choice(days,len(days),replace=True)])
        thr=samp.score.quantile(1-TOPF); top=samp[samp.score>=thr]
        if len(top): ms.append(top.eod.mean())
    return (float(np.percentile(ms,2.5)), float(np.percentile(ms,97.5))) if ms else (float('nan'),float('nan'))

# ---- scorers: each takes scaled Xtr,ytr,Xte -> probability/score on Xte ----
def gbm_score(Xtr,ytr,Xte):
    return GBM(max_iter=300,learning_rate=0.05,max_depth=4,l2_regularization=1.0,
               class_weight="balanced").fit(Xtr,ytr).predict_proba(Xte)[:,1]

def knn_score(Xtr,ytr,Xte,k=75):
    nn=NearestNeighbors(n_neighbors=k).fit(Xtr); _,idx=nn.kneighbors(Xte)
    return ytr[idx].mean(axis=1)   # local rocket density = separability probe

_TABPFN=None
def tabpfn_score(Xtr,ytr,Xte):
    global _TABPFN
    if _TABPFN is None:
        from tabpfn import TabPFNClassifier
        _TABPFN=TabPFNClassifier
    # cap in-context training to 10k (keep ALL rockets, subsample faders) - TabPFN v2 limit
    if len(Xtr)>10000:
        pos=np.where(ytr==1)[0]; neg=np.where(ytr==0)[0]
        neg=np.random.default_rng(0).choice(neg,10000-len(pos),replace=False)
        sel=np.concatenate([pos,neg]); Xtr,ytr=Xtr[sel],ytr[sel]
    for kw in (dict(ignore_pretraining_limits=True),{}):
        try:
            clf=_TABPFN(**kw); clf.fit(Xtr,ytr); return clf.predict_proba(Xte)[:,1]
        except TypeError: continue
    raise RuntimeError("TabPFNClassifier construction failed")

def loro(dt, scorer, scale=True):
    rows={}
    for ty in [2024,2025,2026]:
        tr=dt[dt.year!=ty]; te=dt[dt.year==ty].copy()
        if scale:
            qt=QuantileTransformer(output_distribution="normal",n_quantiles=min(1000,len(tr)),random_state=0).fit(tr[FEATS])
            Xtr,Xte=qt.transform(tr[FEATS]),qt.transform(te[FEATS])
        else:
            Xtr,Xte=tr[FEATS].to_numpy(),te[FEATS].to_numpy()
        te["score"]=scorer(Xtr,tr.rocket.to_numpy(),Xte)
        rows[ty]=te
    return rows

def main():
    dt=load()
    print(f"corpus n={len(dt)} | rockets {int(dt.rocket.sum())} | by year {dt.groupby('year').rocket.agg(['size','sum']).to_dict()}\n")

    # order-flow proxy score (z-combo on the existing aggregates)
    z=lambda s:(s-s.mean())/(s.std()+1e-9)
    dt["ofproxy"]=z(dt.large_print_ratio)-z(dt.odd_lot_ratio)+z(dt.tick_ofi_late)

    print("=== LEAVE-ONE-REGIME-OUT: top-5% mean realized EOD return (train on other 2 regimes) ===")
    print(f"  {'regime':>7}{'base%':>7}{'GBM':>9}{'GBM 95%CI':>16}{'TabPFN':>9}{'TabPFN 95%CI':>16}{'kNN':>8}{'kNN_AUC':>9}{'OFprox':>8}{'ORACLE':>8}")
    gbm_r=loro(dt,gbm_score); tab_r=loro(dt,tabpfn_score)
    knn_r=loro(dt,knn_score)
    summary={}
    for ty in [2024,2025,2026]:
        te_g=gbm_r[ty]; te_t=tab_r[ty]; te_k=knn_r[ty]
        base=te_g.rocket.mean()
        g=topslice(te_g); gci=dayblock_ci(te_g)
        t=topslice(te_t); tci=dayblock_ci(te_t)
        k=topslice(te_k); kauc=roc_auc_score(te_k.rocket,te_k.score) if te_k.rocket.nunique()>1 else float('nan')
        # order-flow proxy + oracle on the same held-out frame
        of=te_g.copy(); of["score"]=dt.loc[of.index,"ofproxy"]; ofs=topslice(of)
        orc=te_g.copy(); orc["score"]=orc.eod; ors=topslice(orc)
        summary[ty]=dict(base=base,gbm=g,gci=gci,tab=t,tci=tci,knn=k,kauc=kauc,of=ofs,oracle=ors)
        print(f"  {ty:>7}{base*100:>6.1f}%{g['mean']*100:>+8.1f}%{('[%+.1f,%+.1f]'%(gci[0]*100,gci[1]*100)):>16}"
              f"{t['mean']*100:>+8.1f}%{('[%+.1f,%+.1f]'%(tci[0]*100,tci[1]*100)):>16}"
              f"{k['mean']*100:>+7.1f}%{kauc:>9.3f}{ofs['mean']*100:>+7.1f}%{ors['mean']*100:>+7.1f}%")

    print("\n=== DECISION READOUT ===")
    for ty in [2024,2025]:
        s=summary[ty]; d=(s['tab']['mean']-s['gbm']['mean'])*100
        verdict=("TabPFN LESS negative -> capacity-overfit existed" if d>1.0 else
                 "TabPFN ~= GBM -> data truth (capacity not the wall)" if abs(d)<=1.0 else
                 "TabPFN MORE negative than GBM")
        print(f"  {ty}: GBM {s['gbm']['mean']*100:+.1f}% vs TabPFN {s['tab']['mean']*100:+.1f}% (Δ {d:+.1f}pp) | kNN_AUC {s['kauc']:.3f} | {verdict}")
    sep24=summary[2024]['kauc']; sep25=summary[2025]['kauc']
    print(f"\n  SEPARABILITY (kNN_AUC, 0.5=interleaved/random): 2024 {sep24:.3f} | 2025 {sep25:.3f} | 2026 {summary[2026]['kauc']:.3f}")
    print("  -> if 2024/2025 kNN_AUC ~0.5 AND TabPFN ~= GBM (both negative): DATA TRUTH, pivot to free-float/news.")
    print("  -> if TabPFN materially less negative OR kNN_AUC >> 0.5 in 2024/2025: surviving signal, ESCALATE.")

if __name__=="__main__": main()
