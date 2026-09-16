"""doc 241 — ROCKET DETECTION (the reframe): can we predict the extreme right tail EX-ANTE?

New objective (Pierce): don't trade the homogeneous mean — identify the ~1/day-to-week ROCKET (explodes
+stays) and trade ONLY those. This is a RARE-EVENT PRECISION problem, not a mean problem. Gating question:
does the rocket have an ex-ante (9:50) signature with cross-regime precision/lift, and does trading ONLY
the top-scored slice flip the realized return positive in ALL 3 years? Same rigor: CPCV OOS, cross-regime.

rocket = EOD-from-entry(9:50) >= R AND sustained (close >= 50% of run-high). Features = microstructure at
the entry decision point (ex-ante, 9:50). NO live change.
"""
from __future__ import annotations
import duckdb, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier as GBM
from sklearn.metrics import roc_auc_score

FEATS=["ret_session","ret_5m","ret_15m","ret_30m","vwap_dist","high_dist","low_dist","range_pos",
       "rvol_cum","vol_accel_5m","realized_vol_15m","up_min_frac_15m","base_close","adv20"]
ENTRY=3; R=0.30

def build():
    df=duckdb.connect().execute("SELECT * FROM read_parquet('data/research/exit_labels_cross_regime.parquet')").df()
    rows=[]
    for (tk,d),g in df.groupby(["ticker","session_date"]):
        g=g.sort_values("minute_idx").reset_index(drop=True)
        if len(g)<=ENTRY+6 or (1+g.ret_session.iloc[ENTRY])<=0: continue
        rel=1+g.ret_session.to_numpy(); eod=rel[-1]/rel[ENTRY]-1
        runhi=rel[ENTRY:].max()/rel[ENTRY]
        sustain=(rel[-1]-rel[ENTRY])/(runhi*rel[ENTRY]-rel[ENTRY]) if runhi>1+1e-6 else 0
        row=g.iloc[ENTRY][["ret_session","ret_5m","ret_15m","ret_30m","vwap_dist","high_dist","low_dist",
                           "range_pos","rvol_cum","vol_accel_5m","realized_vol_15m","up_min_frac_15m"]].to_dict()
        row.update(base_close=float(g.base_close.iloc[0]), adv20=float(g.adv20.iloc[0]),
                   year=int(g.year.iloc[0]), session_date=d, eod=eod, rocket=int(eod>=R and sustain>=0.5))
        rows.append(row)
    dt=pd.DataFrame(rows)
    for c in FEATS: dt[c]=dt[c].astype(float).replace([np.inf,-np.inf],np.nan).fillna(0)
    return dt

def main():
    dt=build()
    print(f"n={len(dt)} | rocket base rate {dt.rocket.mean()*100:.2f}% | by year {dt.groupby('year').rocket.mean().round(4).to_dict()}")
    udays=sorted(dt.session_date.unique()); grp={x:i for i,gg in enumerate(np.array_split(udays,8)) for x in gg}
    dt["_g"]=dt.session_date.map(grp); dt["score"]=np.nan
    for gi in range(8):
        tr=dt[dt._g!=gi]; te=dt.index[dt._g==gi]
        if len(te) and tr.rocket.nunique()>1:
            dt.loc[te,"score"]=GBM(max_iter=300,learning_rate=0.05,max_depth=4,l2_regularization=1.0,
                                   class_weight="balanced").fit(tr[FEATS],tr.rocket).predict_proba(dt.loc[te,FEATS])[:,1]
    dt=dt.dropna(subset=["score"])

    print(f"\n=== AUC(rocket) — can we RANK rockets ex-ante? (CPCV OOS) ===")
    for y in [2024,2025,2026,"POOL"]:
        s=dt if y=="POOL" else dt[dt.year==y]
        if s.rocket.nunique()>1: print(f"  {str(y):6}: AUC {roc_auc_score(s.rocket,s.score):.3f}  (base rate {s.rocket.mean()*100:.1f}%)")

    print(f"\n=== PRECISION/LIFT + realized FORWARD return of the TOP-scored slice (the money test) ===")
    print(f"  {'slice':>8}{'yr':>6}{'n':>5}{'rocket%':>9}{'lift':>6}{'mean EOD ret':>14}{'median':>9}")
    for topf in [0.01,0.02,0.05,0.10]:
        for y in [2024,2025,2026]:
            s=dt[dt.year==y]
            thr=s.score.quantile(1-topf); top=s[s.score>=thr]
            base=s.rocket.mean()
            prec=top.rocket.mean(); lift=prec/base if base>0 else float('nan')
            print(f"  {'top%d%%'%(topf*100):>8}{y:>6}{len(top):>5}{prec*100:>8.0f}%{lift:>6.1f}{top.eod.mean()*100:>+13.1f}%{top.eod.median()*100:>+8.1f}%")
        print()
    print("  VERDICT hint: ex-ante rocket signal is REAL+tradeable iff the top-slice mean EOD return is")
    print("  strongly POSITIVE in ALL 3 years AND lift>1. If the top slice is ~base-rate / ~0-return -> tail is ex-ante random.")

if __name__=="__main__": main()
