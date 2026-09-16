"""doc 234 — hostile adversarial audit of the doc-233 NEGATIVE, with CIs everywhere.

Doc 233 killed the conviction edge with POINT estimates and a single arbitrary random draw, but never
put a CI on the negative. With n=38 sessions, "random beats signal" may itself be within noise. This
applies doc-233's own rigor TO doc 233. 8 experiments, every claim a CI or sweep. NO live change.

Shared setup: per (ticker, session_date) — OOS early_p (purged K-fold), hold_ret at EOD and at the
model's TRAINED 30-min horizon, base_close (price bucket), trailing-20d ADV (no look-ahead
tradeability), month, and p at bars {1,3,5,10} + ret_15m@entry for the leakage audit. Entry = decision
point #3 (~9:50), AFTER the early_p window (no look-ahead). Equal-weight top-N (selection is the test,
not weighting).
"""
from __future__ import annotations
import duckdb, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier as GBM

LABELS = "data/research/exit_labels.parquet"
MULTIDAY = "data/research/multiday_hold_dataset.parquet"
DAYAGG = "data/polygon_warehouse/day_aggs/**/*.parquet"
FEATS = ["minute_idx","ret_session","ret_5m","ret_15m","ret_30m","vwap_dist","high_dist","low_dist",
         "range_pos","rvol_cum","vol_accel_5m","realized_vol_15m","up_min_frac_15m","gap_pct","rvol_entry","mfcs"]
ENTRY = 3; H30 = ENTRY + 6   # 30 min = 6 bars at 5-min cadence
RNG = np.random.default_rng(7)

def cost_bucket(px):
    return 0.040 if px<1 else 0.025 if px<3 else 0.015 if px<10 else 0.008 if px<30 else 0.004

def build():
    df = duckdb.connect().execute(f"SELECT * FROM read_parquet('{LABELS}')").df().dropna(subset=["continued"]).copy()
    for c in FEATS: df[c]=df[c].astype(float).replace([np.inf,-np.inf],np.nan).fillna(0)
    days=sorted(df.session_date.unique()); grp={d:gi for gi,g in enumerate(np.array_split(days,6)) for d in g}
    df["_g"]=df.session_date.map(grp); df["p"]=np.nan
    for gi in range(6):
        tr=df[df._g!=gi]; te=df.index[df._g==gi]
        if len(te) and tr.continued.nunique()>1:
            m=GBM(max_iter=250,learning_rate=0.05,max_depth=4,l2_regularization=1.0).fit(tr[FEATS],tr.continued)
            df.loc[te,"p"]=m.predict_proba(df.loc[te,FEATS])[:,1]
    df=df.dropna(subset=["p"])
    rows=[]
    for (tk,d),g in df.groupby(["ticker","session_date"]):
        g=g.sort_values("minute_idx").reset_index(drop=True); rel=1+g.ret_session.to_numpy(); p=g.p.to_numpy()
        if len(rel)<=H30 or rel[ENTRY]<=0: continue
        pb=lambda i: float(p[i]) if i<len(p) else np.nan
        rows.append(dict(ticker=tk,session_date=d,early_p=float(p[:3].mean()),
            hold_eod=rel[-1]/rel[ENTRY]-1, hold_30m=rel[H30]/rel[ENTRY]-1,
            mfcs=float(g.mfcs.iloc[0] or 0), ret15_entry=float(g.ret_15m.iloc[ENTRY]),
            p1=pb(1),p3=pb(3),p5=pb(5),p10=pb(10)))
    dt=pd.DataFrame(rows)
    md=duckdb.connect().execute(f"SELECT ticker,session_date,base_close FROM read_parquet('{MULTIDAY}')").df()
    dt=dt.merge(md,on=["ticker","session_date"],how="left"); dt["base_close"]=dt.base_close.fillna(5.0)
    dt["is_warrant"]=dt.ticker.str.match(r"^[A-Z]{3,4}W$"); dt["month"]=dt.session_date.str[:7]
    # trailing-20d ADV ($-vol) from day_aggs — no look-ahead
    tks=",".join("'"+t+"'" for t in dt.ticker.unique())
    da=duckdb.connect().execute(f"SELECT ticker, ts_et::DATE d, volume*close dvol FROM read_parquet('{DAYAGG}') WHERE ticker IN ({tks})").df()
    da["d"]=pd.to_datetime(da.d); da=da.sort_values(["ticker","d"])
    da["adv20"]=da.groupby("ticker").dvol.transform(lambda s: s.shift(1).rolling(20,min_periods=5).mean())
    da["session_date"]=da.d.dt.strftime("%Y-%m-%d")
    dt=dt.merge(da[["ticker","session_date","adv20"]],on=["ticker","session_date"],how="left")
    dt["adv20"]=dt.adv20.fillna(0.0)
    return dt

def net(dt, cap, slip, hold="hold_eod"):
    r=np.minimum(dt[hold].to_numpy(), cap)
    c=dt.base_close.map(cost_bucket).to_numpy()*slip
    return r-c

def eligible(dt, pricefloor, advfloor, drop_warrants):
    m=(dt.base_close>=pricefloor)&(dt.adv20>=advfloor)
    if drop_warrants: m&=~dt.is_warrant
    return dt[m]

def boot_ci(x, stat=np.mean, n=10000, seed=11):
    x=np.asarray(x); rng=np.random.default_rng(seed)
    if len(x)<2: return (np.nan,np.nan,np.nan)
    idx=rng.integers(0,len(x),size=(n,len(x))); s=stat(x[idx],axis=1)
    return np.percentile(s,2.5),np.percentile(s,50),np.percentile(s,97.5)

def day_edge(dt, N, cap, slip, pf, af, dw, hold="hold_eod"):
    """per-day: signal-top-N mean-net MINUS eligible-pool mean-net (expected random). Returns array over days."""
    pool=eligible(dt,pf,af,dw).copy(); pool["_net"]=net(pool,cap,slip,hold)
    e=[]
    for d,g in pool.groupby("session_date"):
        if len(g)<N: continue
        sig=g.nlargest(N,"early_p")._net.mean(); rnd=g._net.mean()
        e.append(sig-rnd)
    return np.array(e)

def seeded_random_total(dt, N, cap, slip, pf, af, dw, hold, nseed=1000):
    pool=eligible(dt,pf,af,dw).copy(); pool["_net"]=net(pool,cap,slip,hold)
    byday=[g for _,g in pool.groupby("session_date") if len(g)>=N]
    sig_daily=np.array([g.nlargest(N,"early_p")._net.mean() for g in byday])
    sig_tot=np.prod(1+sig_daily)-1
    rng=np.random.default_rng(3); tots=[]; means=[]; sharpes=[]
    nets=[g._net.to_numpy() for g in byday]
    for _ in range(nseed):
        dr=np.array([a[rng.integers(0,len(a),N)].mean() for a in nets])
        tots.append(np.prod(1+dr)-1); means.append(dr.mean())
        sharpes.append(dr.mean()/dr.std()*np.sqrt(252) if dr.std()>0 else 0)
    return sig_tot, sig_daily, np.array(tots), np.array(means), np.array(sharpes)

# ============================ RUN ============================
dt=build()
print(f"=== daytable: {len(dt)} ticker-days, {dt.session_date.nunique()} sessions, {dt.is_warrant.sum()} warrants ===")
DOC233=dict(cap=0.40,slip=2.0,pf=1.0,af=0.0,dw=True)  # doc-233's exact dials (the verdict under test)
N=8

print("\n########## EXP 1: BOOTSTRAP CI ON SIGNAL-vs-RANDOM (doc-233 dials, EOD) ##########")
edge=day_edge(dt,N,DOC233["cap"],DOC233["slip"],DOC233["pf"],DOC233["af"],DOC233["dw"],"hold_eod")
lo,md_,hi=boot_ci(edge); print(f"per-day edge (signal - pool-mean): n_days={len(edge)} mean {edge.mean()*100:+.2f}%  95% CI [{lo*100:+.2f}%, {hi*100:+.2f}%]")
print(f"  -> CI {'EXCLUDES 0 (real)' if (lo>0 or hi<0) else 'INCLUDES 0 -> signal vs random is WITHIN NOISE (not a finding)'}")
sig_tot,sd,tots,means,sharpes=seeded_random_total(dt,N,DOC233["cap"],DOC233["slip"],DOC233["pf"],DOC233["af"],DOC233["dw"],"hold_eod")
print(f"  signal total {sig_tot*100:+.0f}% | random total 1000-seed P5/50/95: {np.percentile(tots,5)*100:+.0f}% / {np.percentile(tots,50)*100:+.0f}% / {np.percentile(tots,95)*100:+.0f}%")
print(f"  signal Sharpe {sd.mean()/sd.std()*np.sqrt(252):.2f} | random Sharpe P5/50/95: {np.percentile(sharpes,5):.2f}/{np.percentile(sharpes,50):.2f}/{np.percentile(sharpes,95):.2f}")
print(f"  signal pctile within random total-return dist: {(tots<sig_tot).mean()*100:.0f}%")

print("\n########## EXP 2: HORIZON 2x2 (30-min trained vs EOD) — per-day edge CIs ##########")
for hold in ["hold_30m","hold_eod"]:
    e=day_edge(dt,N,DOC233["cap"],DOC233["slip"],DOC233["pf"],DOC233["af"],DOC233["dw"],hold)
    lo,m2,hi=boot_ci(e); print(f"  {hold:>9}: edge {e.mean()*100:+.2f}%  CI [{lo*100:+.2f}%,{hi*100:+.2f}%]  {'**EXCLUDES 0**' if (lo>0 or hi<0) else 'incl 0'}")

print("\n########## EXP 3: SENSITIVITY SWEEPS (per-day edge CI vs each dial, EOD) ##########")
print(" upside cap:")
for cap in [0.2,0.4,0.6,0.8,1.0,2.0,9.9]:
    e=day_edge(dt,N,cap,DOC233["slip"],DOC233["pf"],DOC233["af"],DOC233["dw"]); lo,_,hi=boot_ci(e)
    print(f"   cap +{cap*100:>4.0f}%: edge {e.mean()*100:+.2f}% CI[{lo*100:+.2f},{hi*100:+.2f}] {'*' if (lo>0 or hi<0) else ''}")
print(" slippage mult:")
for s in [1.0,1.5,2.0,3.0,5.0]:
    e=day_edge(dt,N,DOC233["cap"],s,DOC233["pf"],DOC233["af"],DOC233["dw"]); lo,_,hi=boot_ci(e)
    print(f"   slip {s:.1f}x: edge {e.mean()*100:+.2f}% CI[{lo*100:+.2f},{hi*100:+.2f}] {'*' if (lo>0 or hi<0) else ''}")
print(" $-volume floor (trailing-20d ADV) [NEW]:")
for af in [0,0.5e6,1e6,5e6,10e6,25e6]:
    e=day_edge(dt,N,DOC233["cap"],DOC233["slip"],DOC233["pf"],af,DOC233["dw"]); lo,_,hi=boot_ci(e)
    print(f"   ADV>=${af/1e6:>4.1f}M: edge {e.mean()*100:+.2f}% CI[{lo*100:+.2f},{hi*100:+.2f}] n_days={len(e)} {'*' if (lo>0 or hi<0) else ''}")

print("\n########## EXP 4: RANDOM-POOL AUDIT (does the eval-pool selection carry the edge?) ##########")
pool=eligible(dt,1.0,0,True).copy(); pool["_net"]=net(pool,0.4,2.0,"hold_eod")
da=duckdb.connect().execute(f"SELECT (close/open-1) r, open FROM read_parquet('{DAYAGG}') WHERE year=2026 AND open BETWEEN 1 AND 50").df()
print(f"  eval-pool mean intraday net (realistic): {pool._net.mean()*100:+.2f}%  median {pool._net.median()*100:+.2f}%")
print(f"  TRUE-NULL broad universe (all sub-$50, day open->close, n={len(da):,}): mean {da.r.mean()*100:+.2f}%  median {da.r.median()*100:+.2f}%")
print(f"  -> if eval-pool >> broad universe, the SCANNER's gap+RVOL selection is the edge, not P(continue)")

print("\n########## EXP 5: PER-MONTH (signal-vs-random edge CI within each regime) ##########")
for mth,g in dt.groupby("month"):
    e=day_edge(g,N,DOC233["cap"],DOC233["slip"],DOC233["pf"],DOC233["af"],DOC233["dw"])
    if len(e)<3: print(f"   {mth}: n_days={len(e)} (too few)"); continue
    lo,_,hi=boot_ci(e); print(f"   {mth}: n_days={len(e)} edge {e.mean()*100:+.2f}% CI[{lo*100:+.2f},{hi*100:+.2f}] {'*EXCL0*' if (lo>0 or hi<0) else ''}")

print("\n########## EXP 6: CONCENTRATION (top-N, per-day edge CI, EOD) ##########")
for n in [1,2,4,8,16]:
    e=day_edge(dt,n,DOC233["cap"],DOC233["slip"],DOC233["pf"],DOC233["af"],DOC233["dw"]); lo,_,hi=boot_ci(e)
    print(f"   top-{n:>2}: edge {e.mean()*100:+.2f}% CI[{lo*100:+.2f},{hi*100:+.2f}] n_days={len(e)} {'*EXCL0*' if (lo>0 or hi<0) else ''}")

print("\n########## EXP 7: PAIRED WILCOXON (per-day signal-mean vs pool-mean, EOD) ##########")
from scipy.stats import wilcoxon
e=day_edge(dt,N,DOC233["cap"],DOC233["slip"],DOC233["pf"],DOC233["af"],DOC233["dw"])
try:
    st,pv=wilcoxon(e);
    print(f"  n_days={len(e)} median edge {np.median(e)*100:+.2f}%  Wilcoxon p={pv:.3f}  effect(median/IQR)={np.median(e)/(np.percentile(e,75)-np.percentile(e,25)+1e-9):.2f}")
    print(f"  -> {'SIGNIFICANT (p<0.05)' if pv<0.05 else 'NOT significant -> signal indistinguishable from pool'}")
except Exception as ex: print("  wilcoxon err",ex)

print("\n########## EXP 8: LEAKAGE / TAUTOLOGY AUDIT on bar-3 P(continue) ##########")
c=dt[["early_p","ret15_entry"]].dropna()
print(f"  corr(early_p, prior-15min return @entry) = {c.early_p.corr(c.ret15_entry):.3f}  (high => P(continue) ~ lagged momentum)")
st=dt[["p1","p3","p5","p10"]].dropna()
print(f"  P(continue) autocorr across bars: corr(p1,p3)={st.p1.corr(st.p3):.2f} corr(p3,p5)={st.p3.corr(st.p5):.2f} corr(p3,p10)={st.p3.corr(st.p10):.2f}")
print(f"  mean |p3-p1|={ (st.p3-st.p1).abs().mean():.3f}  mean|p10-p3|={(st.p10-st.p3).abs().mean():.3f}  (stability)")
