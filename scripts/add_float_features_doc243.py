"""doc 243 (BET#3 cont.) — add FLOAT / float-rotation features (the classic rocket signal) + re-test.

Low-float + high early rotation is the canonical low-float-rocket setup. ticker_details has
share_class_shares_outstanding + weighted_shares_outstanding + market_cap. Float features (ex-ante,
computable from data on hand): log_shares (float-proxy size), log_mcap, premarket-vol/shares (premarket
rotation = the doc-187 premarket-vol/float signal), early-vol/shares (early rotation). Test whether
float ADDS rocket-discrimination on top of micro+tick. NO live change.
"""
from __future__ import annotations
import duckdb, numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier as GBM
from sklearn.metrics import roc_auc_score

MICRO=["ret_session","ret_5m","ret_15m","ret_30m","vwap_dist","high_dist","low_dist","range_pos","rvol_cum","vol_accel_5m","realized_vol_15m","up_min_frac_15m","base_close","adv20"]
TICK=["pm_vol","pm_dollarvol","early_vol","early_trades","tick_ofi","tick_ofi_late","large_print_ratio","mean_trade_size","trade_intensity","tick_vwap_dist","odd_lot_ratio"]
FLOAT=["log_shares","log_mcap","pm_vol_per_share","early_vol_per_share","turnover_ratio"]

def main():
    con=duckdb.connect()
    df=con.execute("SELECT * FROM read_parquet('data/research/rocket_tick_features.parquet')").df()
    td=con.execute("SELECT ticker, share_class_shares_outstanding sh1, weighted_shares_outstanding sh2, market_cap mcap FROM read_parquet('data/polygon_warehouse/reference/ticker_details.parquet')").df()
    td["shares"]=td.sh1.fillna(td.sh2)
    df=df.merge(td[["ticker","shares","mcap"]],on="ticker",how="left")
    cov=df.shares.notna().mean()
    print(f"n={len(df)} | float coverage: {cov*100:.0f}% have shares-outstanding | median shares {df.shares.median():,.0f}")
    # float features
    df["log_shares"]=np.log10(df.shares.clip(lower=1e5)).fillna(np.log10(df.shares.median() if df.shares.notna().any() else 1e7))
    df["log_mcap"]=np.log10(df.mcap.clip(lower=1e5)).fillna(7.0)
    df["pm_vol_per_share"]=(df.pm_vol/df.shares).replace([np.inf,-np.inf],np.nan).fillna(0)
    df["early_vol_per_share"]=(df.early_vol/df.shares).replace([np.inf,-np.inf],np.nan).fillna(0)
    df["turnover_ratio"]=((df.pm_vol+df.early_vol)/df.shares).replace([np.inf,-np.inf],np.nan).fillna(0)
    for c in MICRO+TICK+FLOAT: df[c]=df[c].astype(float).replace([np.inf,-np.inf],np.nan).fillna(0)

    # rocket-by-shares sanity: do rockets have smaller float?
    for R in [0.20,0.30]:
        lab=(df.eod>=R)
        print(f"  R>={R}: rocket median shares {df[lab].shares.median():,.0f} vs non-rocket {df[~lab].shares.median():,.0f} | "
              f"rocket median pm_vol/share {df[lab].pm_vol_per_share.median():.3f} vs {df[~lab].pm_vol_per_share.median():.3f}")

    ud=sorted(df.session_date.unique()); grp={x:i for i,g in enumerate(np.array_split(ud,6)) for x in g}; df["_g"]=df.session_date.map(grp)
    def oos(feat,lab):
        s=df.copy(); s["p"]=np.nan
        for gi in range(6):
            tr=s[s._g!=gi]; te=s.index[s._g==gi]
            if len(te) and tr[lab].nunique()>1:
                s.loc[te,"p"]=GBM(max_iter=300,learning_rate=0.05,max_depth=4,l2_regularization=1.0,class_weight="balanced").fit(tr[feat],tr[lab]).predict_proba(s.loc[te,feat])[:,1]
        return s.dropna(subset=["p"]).p.to_numpy()
    def bootdiff(y,pa,pb,n=3000):
        rng=np.random.default_rng(7); d=[]
        for _ in range(n):
            idx=rng.integers(0,len(y),len(y))
            if 2<y[idx].sum()<len(idx)-2: d.append(roc_auc_score(y[idx],pb[idx])-roc_auc_score(y[idx],pa[idx]))
        d=np.array(d); return d.mean(),np.percentile(d,2.5),np.percentile(d,97.5)
    print("\n=== does FLOAT add discrimination on top of micro+tick? (CPCV OOS, AUC + bootstrap lift) ===")
    for R in [0.20,0.30]:
        df["lab"]=(df.eod>=R).astype(int); y=df["lab"].to_numpy()
        pmt=oos(MICRO+TICK,"lab"); pall=oos(MICRO+TICK+FLOAT,"lab"); pf=oos(FLOAT,"lab")
        m,lo,hi=bootdiff(y[:len(pmt)],pmt,pall)
        print(f"  R>={R} (rockets={int(y.sum())}): micro+tick {roc_auc_score(y,pmt):.3f} -> +FLOAT {roc_auc_score(y,pall):.3f} | "
              f"FLOAT-only {roc_auc_score(y,pf):.3f} | float lift {m:+.3f} CI[{lo:+.3f},{hi:+.3f}] {'SIG' if lo>0 else 'ns'}")
        # top-slice precision/return with all features
        s=df.copy(); s["p"]=pall if len(pall)==len(s) else np.nan
        if s.p.notna().all():
            thr=s.p.quantile(0.98); top=s[s.p>=thr]
            print(f"       all-feature top-2%: prec {top['lab'].mean()*100:.0f}% ret {top.eod.mean()*100:+.1f}%/med {top.eod.median()*100:+.1f}%")
    print("\n  (float = shares-outstanding proxy; true free-float usually smaller. ex-ante, on-hand.)")

if __name__=="__main__": main()
