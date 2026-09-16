"""doc 235 Stages 2-5 — the pre-registered cross-regime audit. Mode A (frozen-2026) + Mode B (CPCV
refit), bootstrap CIs, Bonferroni, decision rules. Reads data/research/exit_labels_cross_regime.parquet.
NO dial may differ from the doc-235 pre-registration block. NO live change.
"""
from __future__ import annotations
import re, itertools, duckdb, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier as GBM
from sklearn.metrics import roc_auc_score

CORPUS="data/research/exit_labels_cross_regime.parquet"
DA="data/polygon_warehouse/day_aggs/**/*.parquet"
MICRO=["minute_idx","ret_session","ret_5m","ret_15m","ret_30m","vwap_dist","high_dist","low_dist",
       "range_pos","rvol_cum","vol_accel_5m","realized_vol_15m","up_min_frac_15m"]
ENTRY=3
# LOCKED dials (doc-235 pre-reg)
CAP, SLIP, PFLOOR = 0.40, 2.0, 1.0
def cost_bucket(px): return 0.040 if px<1 else 0.025 if px<3 else 0.015 if px<10 else 0.008 if px<30 else 0.004
def gbm(): return GBM(max_iter=250,learning_rate=0.05,max_depth=4,l2_regularization=1.0)
def boot_ci(x,n=10000,seed=11):
    x=np.asarray(x,float)
    if len(x)<3: return (np.nan,np.nan,np.nan)
    rng=np.random.default_rng(seed); idx=rng.integers(0,len(x),size=(n,len(x))); s=x[idx].mean(axis=1)
    return np.percentile(s,2.5),np.percentile(s,50),np.percentile(s,97.5)

def reduce_daytable(df):
    """per ticker-day -> early_p, hold_30m, hold_eod, base_close, adv20, year, month."""
    rows=[]
    for (tk,d),g in df.groupby(["ticker","session_date"]):
        g=g.sort_values("minute_idx").reset_index(drop=True)
        if len(g)<=ENTRY+6: continue
        rel=1+g.ret_session.to_numpy();
        if rel[ENTRY]<=0: continue
        rows.append(dict(ticker=tk,session_date=d,year=int(g.year.iloc[0]),month=d[:7],
            early_p=float(g.P.to_numpy()[:3].mean()),
            hold_30m=rel[ENTRY+6]/rel[ENTRY]-1, hold_eod=rel[-1]/rel[ENTRY]-1,
            base_close=float(g.base_close.iloc[0]), adv20=float(g.adv20.iloc[0]),
            is_warrant=bool(re.match(r"^[A-Z]{3,4}W$",tk))))
    return pd.DataFrame(rows)

def day_edge(dt, N, advfloor, hold):
    pool=dt[(dt.base_close>=PFLOOR)&(dt.adv20>=advfloor)&(~dt.is_warrant)].copy()
    pool["_net"]=np.minimum(pool[hold],CAP)-pool.base_close.map(cost_bucket)*SLIP
    e=[]
    for d,g in pool.groupby("session_date"):
        if len(g)<N: continue
        e.append(g.nlargest(N,"early_p")._net.mean()-g._net.mean())
    return np.array(e)

def edge_line(tag, dt, N, advfloor, hold):
    e=day_edge(dt,N,advfloor,hold); lo,m,hi=boot_ci(e)
    sig = (lo>0 or hi<0)
    print(f"  {tag:<42} n_days={len(e):>3} edge {e.mean()*100:+.2f}% CI[{lo*100:+.2f},{hi*100:+.2f}] {'*EXCL0*' if sig else 'incl0'}")
    return dict(tag=tag,n=len(e),edge=float(e.mean()),lo=lo,hi=hi,sig=bool(sig))

def run_mode(name, dt):
    print(f"\n################## {name} ##################")
    res={}
    print("PRIMARY ENDPOINT — pooled 2024+2025, ADV>=$1M, 30-min:")
    pooled=dt[dt.year.isin([2024,2025])]
    res["pool_top1"]=edge_line("pooled top-1 @ADV$1M @30m",pooled,1,1e6,"hold_30m")
    res["pool_top2"]=edge_line("pooled top-2 @ADV$1M @30m",pooled,2,1e6,"hold_30m")
    print("per-year (SURVIVES needs both excl 0), top-2 @ADV$1M @30m:")
    res["y24"]=edge_line("2024 top-2 @ADV$1M @30m",dt[dt.year==2024],2,1e6,"hold_30m")
    res["y25"]=edge_line("2025 top-2 @ADV$1M @30m",dt[dt.year==2025],2,1e6,"hold_30m")
    res["y26"]=edge_line("2026 top-2 @ADV$1M @30m (in-sample ref)",dt[dt.year==2026],2,1e6,"hold_30m")
    print("SECONDARY — EOD horizon (pooled):")
    edge_line("pooled top-2 @ADV$1M @EOD",pooled,2,1e6,"hold_eod")
    print("ADV sweep (pooled top-2 @30m; gate floor is $1M so sweep is upward):")
    for af in [1e6,5e6,10e6,25e6]: edge_line(f"ADV>=${af/1e6:.0f}M",pooled,2,af,"hold_30m")
    print("concentration sweep (pooled @ADV$1M @30m):")
    for n in [1,2,4]: edge_line(f"top-{n}",pooled,n,1e6,"hold_30m")
    print("per-month (top-2 @ADV$1M @30m) — fraction of months with positive edge:")
    months=[];
    for mth,g in pooled.groupby("month"):
        e=day_edge(g,2,1e6,"hold_30m")
        if len(e)>=3: months.append((mth,e.mean(),boot_ci(e)))
    pos=sum(1 for _,m,_ in months if m>0)
    for mth,m,(lo,_,hi) in months: print(f"    {mth}: edge {m*100:+.2f}% CI[{lo*100:+.2f},{hi*100:+.2f}] {'*' if (lo>0 or hi<0) else ''}")
    res["months_pos_frac"]=pos/max(len(months),1); res["n_months"]=len(months)
    print(f"  -> {pos}/{len(months)} months positive ({res['months_pos_frac']*100:.0f}%)")
    return res

def main():
    df=duckdb.connect().execute(f"SELECT * FROM read_parquet('{CORPUS}')").df()
    for c in MICRO: df[c]=df[c].astype(float).replace([np.inf,-np.inf],np.nan).fillna(0)
    df=df.dropna(subset=["continued"])
    print(f"corpus: {len(df):,} rows | ticker-days {df.groupby(['ticker','session_date']).ngroups} | by year:")
    print(df.groupby('year').agg(rows=('continued','size'),contrate=('continued','mean')))

    # 2026 micro-only CPCV AUC (confirm feature reduction didn't break it)
    d26=df[df.year==2026].copy()
    days=sorted(d26.session_date.unique()); grp={x:i for i,g in enumerate(np.array_split(days,6)) for x in g}
    d26["_g"]=d26.session_date.map(grp); d26["P"]=np.nan; aucs=[]
    for gi in range(6):
        tr=d26[d26._g!=gi]; te=d26.index[d26._g==gi]
        if len(te) and tr.continued.nunique()>1:
            m=gbm().fit(tr[MICRO],tr.continued); pr=m.predict_proba(d26.loc[te,MICRO])[:,1]
            d26.loc[te,"P"]=pr; aucs.append(roc_auc_score(d26.loc[te,"continued"],pr))
    print(f"\n[check] 2026 micro-only CPCV AUC mean {np.mean(aucs):.3f} +/- {np.std(aucs):.3f}  (doc-234 full-feature was 0.768)")

    # ===== MODE A: frozen-2026 model -> score 2024+2025 =====
    mA=gbm().fit(df[df.year==2026][MICRO], df[df.year==2026]["continued"])
    dfA=df.copy(); dfA["P"]=mA.predict_proba(dfA[MICRO])[:,1]
    dtA=reduce_daytable(dfA)
    resA=run_mode("MODE A — FROZEN 2026 model scored out-of-period", dtA)

    # ===== MODE B: CPCV refit across 2024-2026 =====
    dfB=df.copy(); days=sorted(dfB.session_date.unique()); grp={x:i for i,g in enumerate(np.array_split(days,8)) for x in g}
    dfB["_g"]=dfB.session_date.map(grp); dfB["P"]=np.nan
    for gi in range(8):
        tr=dfB[dfB._g!=gi]; te=dfB.index[dfB._g==gi]
        if len(te) and tr.continued.nunique()>1:
            dfB.loc[te,"P"]=gbm().fit(tr[MICRO],tr.continued).predict_proba(dfB.loc[te,MICRO])[:,1]
    dtB=reduce_daytable(dfB.dropna(subset=["P"]))
    resB=run_mode("MODE B — CPCV refit across 2024-2026 (OOS)", dtB)

    # ===== broad-universe true null (does the GATE itself beat the market?) =====
    print("\n################## TRUE-NULL: gate vs broad market (intraday open->close) ##################")
    bn=duckdb.connect().execute(f"SELECT avg(close/open-1) m, median(close/open-1) md, count(*) n FROM read_parquet('{DA}') WHERE year IN (2024,2025) AND open BETWEEN 0.50 AND 20").fetchone()
    print(f"  broad sub-$20 universe open->close: mean {bn[0]*100:+.2f}% median {bn[1]*100:+.2f}% (n={bn[2]:,})")

    # ===== Bonferroni + DECISION =====
    print("\n################## VERDICT (Bonferroni-corrected on the primary family) ##################")
    # primary family = {ModeA,ModeB} x {top1,top2} pooled = 4 tests at 95% -> Bonferroni alpha 0.05/4
    # CI at 95% excludes 0 ~ p<0.05; Bonferroni-equiv requires the 98.75% CI to exclude 0. Recompute primary at 98.75%.
    def ci9875(dt,N):
        e=day_edge(dt,N,1e6,"hold_30m"); x=np.asarray(e); rng=np.random.default_rng(11)
        idx=rng.integers(0,len(x),size=(20000,len(x))); s=x[idx].mean(axis=1)
        return np.percentile(s,0.625),np.percentile(s,99.375),e.mean()
    for nm,dt2 in [("A",dtA),("B",dtB)]:
        for N in [1,2]:
            lo,hi,m=ci9875(dt2[dt2.year.isin([2024,2025])],N)
            print(f"  Mode {nm} pooled top-{N}: edge {m*100:+.2f}% Bonferroni-98.75%CI[{lo*100:+.2f},{hi*100:+.2f}] {'*EXCL0*' if (lo>0 or hi<0) else 'incl0'}")
    def verdict(res):
        prim=res["pool_top2"]["sig"] or res["pool_top1"]["sig"]
        both_years=res["y24"]["sig"] and res["y25"]["sig"]
        dir_ok=res["pool_top2"]["edge"]>0
        if prim and both_years and dir_ok: return "SURVIVES"
        if (not res["pool_top2"]["sig"] and not res["pool_top1"]["sig"]) or res["pool_top2"]["edge"]<0 or res["months_pos_frac"]<0.30: return "SEASONAL/ARTIFACT"
        return "AMBIGUOUS"
    print(f"\n  MODE A decision: {verdict(resA)}")
    print(f"  MODE B decision: {verdict(resB)}")

if __name__=="__main__": main()
