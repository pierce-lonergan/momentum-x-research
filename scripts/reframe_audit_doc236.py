"""doc 236 — pre-registered (b1) variance-predictor + (b2) direction-agnostic tail-filter reframes.
doc-235 corpus, Mode-B CPCV-refit OOS preds only. No tuning. Bonferroni. NO live change.
"""
from __future__ import annotations
import numpy as np, pandas as pd, duckdb
from sklearn.ensemble import HistGradientBoostingClassifier as GBM
from scipy.stats import spearmanr

CORPUS="data/research/exit_labels_cross_regime.parquet"
MICRO=["minute_idx","ret_session","ret_5m","ret_15m","ret_30m","vwap_dist","high_dist","low_dist",
       "range_pos","rvol_cum","vol_accel_5m","realized_vol_15m","up_min_frac_15m"]
ENTRY=3
def gbm(): return GBM(max_iter=250,learning_rate=0.05,max_depth=4,l2_regularization=1.0)
def bootci(x,fn=np.mean,n=4000,seed=11,pcts=(2.5,97.5)):
    x=np.asarray(x,float)
    if len(x)<5: return (np.nan,np.nan)
    rng=np.random.default_rng(seed); idx=rng.integers(0,len(x),size=(n,len(x))); s=fn(x[idx],axis=1)
    return np.percentile(s,pcts[0]),np.percentile(s,pcts[1])

def main():
    df=duckdb.connect().execute(f"SELECT * FROM read_parquet('{CORPUS}')").df()
    for c in MICRO: df[c]=df[c].astype(float).replace([np.inf,-np.inf],np.nan).fillna(0)
    df=df.dropna(subset=["continued"])
    # Mode-B CPCV-refit OOS preds (8-fold day-grouped, same as doc 235)
    days=sorted(df.session_date.unique()); grp={x:i for i,g in enumerate(np.array_split(days,8)) for x in g}
    df["_g"]=df.session_date.map(grp); df["P"]=np.nan
    for gi in range(8):
        tr=df[df._g!=gi]; te=df.index[df._g==gi]
        if len(te) and tr.continued.nunique()>1:
            df.loc[te,"P"]=gbm().fit(tr[MICRO],tr.continued).predict_proba(df.loc[te,MICRO])[:,1]
    df=df.dropna(subset=["P"])
    # per ticker-day reduce
    rows=[]
    for (tk,d),g in df.groupby(["ticker","session_date"]):
        g=g.sort_values("minute_idx").reset_index(drop=True)
        if len(g)<=ENTRY+6: continue
        rel=1+g.ret_session.to_numpy()
        if rel[ENTRY]<=0: continue
        win=rel[ENTRY:ENTRY+7]
        ep=float(g.P.to_numpy()[:3].mean())
        h30=rel[ENTRY+6]/rel[ENTRY]-1
        rows.append(dict(year=int(g.year.iloc[0]), early_p=ep, dev=abs(ep-0.5),
                         hold_30m=h30, abs30=abs(h30), range30=(win.max()-win.min())/rel[ENTRY]))
    dt=pd.DataFrame(rows)
    print(f"ticker-days: {len(dt)} | by year: {dt.year.value_counts().to_dict()}\n")

    # ===================== (b1) VARIANCE PREDICTOR =====================
    print("########## REFRAME b1 — |P-0.5| predicts realized 30-min volatility ##########")
    print("Spearman(|P-0.5|, abs 30-min return) per year + pooled (Bonferroni alpha 0.05/4=0.0125):")
    b1_pass=True
    for lab,sub in [("2024",dt[dt.year==2024]),("2025",dt[dt.year==2025]),("2026",dt[dt.year==2026]),("POOLED",dt)]:
        rho,p=spearmanr(sub.dev,sub.abs30)
        sig = p<0.0125 and rho>0
        b1_pass &= sig
        print(f"  {lab:>7}: n={len(sub):>5}  Spearman rho {rho:+.3f}  p={p:.4f}  {'*sig+pos*' if sig else 'FAIL'}")
    print("  |P-0.5| quintile -> mean abs 30-min return (monotonic? pooled):")
    dt["q"]=pd.qcut(dt.dev,5,labels=[1,2,3,4,5],duplicates="drop")
    for q,g in dt.groupby("q",observed=True):
        lo,hi=bootci(g.abs30.values)
        print(f"    Q{q}: |P-0.5| med {g.dev.median():.3f}  abs30 mean {g.abs30.mean()*100:.2f}% CI[{lo*100:.2f},{hi*100:.2f}]")
    print(f"  b1 VERDICT: {'SURVIVES' if b1_pass else 'FAILS'} (needs sig+positive Spearman in ALL of 2024,2025,2026,pooled)\n")

    # ===================== (b2) DIRECTION-AGNOSTIC TAIL FILTER =====================
    print("########## REFRAME b2 — does any P-threshold cut the P5 loss tail across ALL 3 years ##########")
    yrs=[2024,2025,2026]; thresholds=[0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9]
    n_family=len(thresholds)*2*len(yrs)
    a_bonf=0.05/n_family
    pcts=(a_bonf/2*100, 100-a_bonf/2*100)  # Bonferroni-corrected CI for P5-improvement
    survivors=[]
    def p5(x): return np.percentile(x,5)
    for t in thresholds:
        for direction in ["above","below"]:
            ok_all=True; detail=[]
            for y in yrs:
                sub=dt[dt.year==y]; allr=sub.hold_30m.values
                kept = sub[sub.early_p>=t].hold_30m.values if direction=="above" else sub[sub.early_p<t].hold_30m.values
                if len(kept)<30: ok_all=False; detail.append(f"{y}:n{len(kept)}"); continue
                p5_imp = np.percentile(kept,5)-np.percentile(allr,5)   # >0 = tail less negative
                mean_lost = allr.mean()-kept.mean()
                # bootstrap CI on p5_improvement (paired-ish: resample kept)
                rng=np.random.default_rng(7); idx=rng.integers(0,len(kept),size=(2000,len(kept)))
                bs=np.percentile(kept[idx],5,axis=1)-np.percentile(allr,5)
                lo,hi=np.percentile(bs,pcts[0]),np.percentile(bs,pcts[1])
                cond = (lo>0) and (p5_imp>mean_lost)   # sig tail cut AND cut > mean sacrificed
                ok_all &= cond
                detail.append(f"{y}:P5imp{p5_imp*100:+.1f}%[{lo*100:+.1f},{hi*100:+.1f}]meanLost{mean_lost*100:+.1f}{'OK' if cond else 'x'}")
            tag=f"thr{t} keep-{direction}"
            mark = "  <== SURVIVES ALL 3" if ok_all else ""
            if ok_all: survivors.append(tag)
            # only print the promising ones (any year sig) to keep output readable
            if ok_all or any("OK" in d for d in detail):
                print(f"  {tag:<18} " + " | ".join(detail) + mark)
    print(f"\n  b2 family size {n_family}, Bonferroni alpha {a_bonf:.5f} (CI {pcts[0]:.2f}-{pcts[1]:.2f} pct)")
    print(f"  b2 VERDICT: {'SURVIVES — '+str(survivors) if survivors else 'FAILS (no threshold cuts the tail across all 3 years)'}")

    print("\n########## DECISION ##########")
    print(f"  b1 (variance-predictor): {'SURVIVES' if b1_pass else 'FAILS'}")
    print(f"  b2 (tail-filter): {'SURVIVES' if survivors else 'FAILS'}")
    print(f"  => {'BET#1 CLOSES FULLY (both fail)' if (not b1_pass and not survivors) else 'a reframe survived -> promote per doc-236 rules'}")

if __name__=="__main__": main()
