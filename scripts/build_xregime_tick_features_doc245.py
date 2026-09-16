"""doc 245 (BET#3) — CROSS-REGIME tick rocket test. Extend the doc-242 TICK tape features to the FULL
2024-2026 candidate universe (via targeted /v3/trades API, NOT a 1 TB flat-file pull) and run the
PRE-REGISTERED money test: does the tape flip the top-slice realized return POSITIVE in ALL 3 years?

doc 241: MICRO-only ranks rockets (AUC .65/.72/.83) but the top slice LOSES in 2024 & 2025 (positive only
2026). doc 242: adding the TICK tape lifts AUC +0.05-0.07 — but WITHIN-WINDOW only (2025-08..2026-04,
34-73 rockets). THIS test: ~217 rockets across 3 regimes; the gate is realized $$, not AUC.

Method: candidates + MICRO + rocket label are 100% local (exit_labels_cross_regime.parquet, identical gate
all years). Only the early-session TAPE is fetched — per candidate ticker-day, premarket(04:00-09:30) +
09:30-09:50 trades via /v3/trades. In-window candidates REUSE the stored doc-242 features; out-window are
pulled via API. BOTH compute the 11 features through the SAME compute_tick_feats() -> no method confound
(validated by --sanity). NO live change.

PRE-REGISTERED DECISION (committed before results, see --test):
  MICRO+TICK is a real cross-regime rocket edge IFF, at the PRIMARY operating slice top-5% (~1 name/day):
    (1) mean EOD forward return > 0 in ALL of 2024, 2025, 2026, AND
    (2) lift > 1 in all 3 years, AND
    (3) it RESCUES the two currently-negative regimes (2024, 2025 mean return goes from <0 to >0).
  Secondary: median not strongly negative; bootstrap 95% CI on per-year top-slice mean reported (not a
  hard gate given fat tails / small n, but a one-winner-driven positive is flagged).
  If MICRO+TICK is positive ONLY in 2026 (like MICRO-only), the tape does NOT generalize -> rocket tail
  is ex-ante random cross-regime and BET#3 closes.

Usage:
  python scripts/build_xregime_tick_features_doc245.py --sanity 50      # API-vs-local equivalence
  python scripts/build_xregime_tick_features_doc245.py --workers 10     # full pull (resumable)
  python scripts/build_xregime_tick_features_doc245.py --test           # pre-registered money test
"""
from __future__ import annotations
import argparse, os, time, json, urllib.request, concurrent.futures as cf
import numpy as np, pandas as pd, duckdb
from datetime import datetime
from zoneinfo import ZoneInfo
from sklearn.ensemble import HistGradientBoostingClassifier as GBM
from sklearn.metrics import roc_auc_score

ET=ZoneInfo("America/New_York"); UTC=ZoneInfo("UTC")
XREG="data/research/exit_labels_cross_regime.parquet"
LOCAL_TICK="data/research/rocket_tick_features.parquet"   # doc-242 in-window stored features
TRADES_PQ="data/polygon_warehouse/trades_v1_parquet"      # local tape (in-window, for --sanity)
OUT="data/research/rocket_tick_features_xregime.parquet"
CKPT="data/research/_xregime_tick_ckpt.jsonl"
ENTRY=3; R=0.30
MICRO=["ret_session","ret_5m","ret_15m","ret_30m","vwap_dist","high_dist","low_dist","range_pos",
       "rvol_cum","vol_accel_5m","realized_vol_15m","up_min_frac_15m","base_close","adv20"]
TICK=["pm_vol","pm_dollarvol","early_vol","early_trades","tick_ofi","tick_ofi_late","large_print_ratio",
      "mean_trade_size","trade_intensity","tick_vwap_dist","odd_lot_ratio"]

_KEY=None
def key():
    global _KEY
    if _KEY: return _KEY
    for l in open(os.path.expanduser('~/momentum-x-secrets.env'),encoding='utf-8',errors='replace'):
        l=l.rstrip('\r\n')
        if l.startswith('POLYGON_API_KEY=') and not l.lstrip().startswith('#'):
            _KEY=l.split('=',1)[1].strip().strip('"')
    return _KEY

def utc_ns(d, hh, mm):
    dt=datetime(int(d[:4]),int(d[5:7]),int(d[8:10]),hh,mm,tzinfo=ET).astimezone(UTC)
    return int(dt.timestamp()*1e9)

# ---- corpus: doc-241 build() VERBATIM (+ ticker carried for the API pull) ----
def corpus():
    df=duckdb.connect().execute(f"SELECT * FROM read_parquet('{XREG}')").df()
    rows=[]
    for (tk,d),g in df.groupby(["ticker","session_date"]):
        g=g.sort_values("minute_idx").reset_index(drop=True)
        if len(g)<=ENTRY+6 or (1+g.ret_session.iloc[ENTRY])<=0: continue
        rel=1+g.ret_session.to_numpy(); eod=rel[-1]/rel[ENTRY]-1
        runhi=rel[ENTRY:].max()/rel[ENTRY]
        sustain=(rel[-1]-rel[ENTRY])/(runhi*rel[ENTRY]-rel[ENTRY]) if runhi>1+1e-6 else 0
        row=g.iloc[ENTRY][["ret_session","ret_5m","ret_15m","ret_30m","vwap_dist","high_dist","low_dist",
                           "range_pos","rvol_cum","vol_accel_5m","realized_vol_15m","up_min_frac_15m"]].to_dict()
        row.update(ticker=tk, base_close=float(g.base_close.iloc[0]), adv20=float(g.adv20.iloc[0]),
                   year=int(g.year.iloc[0]), session_date=d, eod=eod, rocket=int(eod>=R and sustain>=0.5))
        rows.append(row)
    dt=pd.DataFrame(rows)
    for c in MICRO: dt[c]=dt[c].astype(float).replace([np.inf,-np.inf],np.nan).fillna(0)
    return dt

# ---- the 11 TICK features: ONE function, fed by EITHER local parquet OR API (no method drift) ----
def compute_tick_feats(t):
    """t: DataFrame with columns ts_et (tz-aware ET), price, size. Verbatim doc-242 tick_feats logic."""
    if t is None or len(t)<5: return None
    t=t.sort_values("ts_et")
    mins=t.ts_et.dt.hour*60+t.ts_et.dt.minute
    pm=t[(mins>=240)&(mins<570)]; early=t[(mins>=570)&(mins<590)]   # premarket; 9:30-9:50
    if len(early)<3: return None
    px=early["price"].to_numpy(); sz=early["size"].to_numpy().astype(float)
    sign=np.sign(np.diff(px,prepend=px[0]))
    for i in range(1,len(sign)):
        if sign[i]==0: sign[i]=sign[i-1]
    tot=sz.sum(); ofi=float((sign*sz).sum()/tot) if tot>0 else 0.0
    half=len(early)//2; lsz=sz[half:]; lsg=sign[half:]
    ofi_late=float((lsg*lsz).sum()/max(lsz.sum(),1)) if lsz.sum()>0 else 0.0
    vwap=float((px*sz).sum()/tot) if tot>0 else px[-1]
    return dict(pm_vol=float(pm["size"].sum()), pm_dollarvol=float((pm.price*pm["size"]).sum()),
                early_vol=float(tot), early_trades=float(len(early)),
                tick_ofi=ofi, tick_ofi_late=ofi_late,
                large_print_ratio=float(sz[sz>=5000].sum()/tot) if tot>0 else 0.0,
                mean_trade_size=float(sz.mean()), trade_intensity=float(len(early)/20.0),
                tick_vwap_dist=float(px[-1]/vwap-1), odd_lot_ratio=float((sz<100).sum()/len(sz)))

def _pull_window(tk, d, hm0, hm1, max_pages):
    g0=utc_ns(d,hm0[0],hm0[1]); g1=utc_ns(d,hm1[0],hm1[1])
    url=(f"https://api.polygon.io/v3/trades/{tk}?timestamp.gte={g0}&timestamp.lt={g1}"
         f"&order=asc&limit=50000&apiKey={key()}")
    out=[]
    for _ in range(max_pages):
        dd=None
        for _a in range(4):
            try: dd=json.load(urllib.request.urlopen(url,timeout=40)); break
            except Exception: time.sleep(1.0)
        if dd is None: break
        out+=dd.get("results",[])
        nu=dd.get("next_url")
        if not nu: break
        url=nu+f"&apiKey={key()}"; time.sleep(0.04)
    return out

def api_trades(tk, d):
    """Pull premarket(04:00-09:30, capped) + early(09:30-09:50, full) separately so the EARLY signal is
    never truncated by heavy premarket. Returns combined df(ts_et,price,size) for compute_tick_feats."""
    early=_pull_window(tk,d,(9,30),(9,50),max_pages=8)
    pm=_pull_window(tk,d,(4,0),(9,30),max_pages=20)
    rows=pm+early
    if len(rows)<5: return None
    q=pd.DataFrame(rows)
    if not {"sip_timestamp","price","size"}.issubset(q.columns): return None
    q=q[["sip_timestamp","price","size"]].dropna()
    q=q[(q.price>0)&(q["size"]>0)]
    if len(q)<5: return None
    q["ts_et"]=pd.to_datetime(q["sip_timestamp"],unit="ns",utc=True).dt.tz_convert("America/New_York")
    return q

def local_trades(tk, d):
    """In-window local tape -> df(ts_et,price,size). CRITICAL: derive ET from RAW sip_timestamp, NOT the
    stored ts_et column (which is UTC-mislabeled-as-ET — the warehouse conversion bug that broke doc242/244:
    stored ts_et = correct ET + UTC offset, so mins[570,590) read ~5:30am premarket, not the 9:30-9:50 open)."""
    p=f"{TRADES_PQ}/year={d[:4]}/month={d[5:7]}/day={d[8:10]}/ticker={tk}/data_0.parquet"
    if not os.path.exists(p): return None
    try:
        t=duckdb.connect().execute(
            f"SELECT sip_timestamp, price, size FROM read_parquet('{p}') WHERE price>0 AND size>0").df()
    except Exception: return None
    if len(t)<5: return None
    t["ts_et"]=pd.to_datetime(t["sip_timestamp"],unit="ns",utc=True).dt.tz_convert("America/New_York")
    return t

def get_trades(tk, d):
    """Uniform tape source: local parquet (fast, in-window) else API (out-window). Both derive ET from
    sip_timestamp -> identical method, no confound. Validated equal on the AAL diagnostic (38,256 trades both).
    NOTE: explicit None check, NOT `local or api` (bool(DataFrame) raises ValueError)."""
    t=local_trades(tk, d)
    return t if t is not None else api_trades(tk, d)

# ---------------------------------------------------------------------------
def run_sanity(n):
    dt=corpus()
    loc=duckdb.connect().execute(f"SELECT ticker,session_date FROM read_parquet('{LOCAL_TICK}')").df()
    inwin=dt.merge(loc,on=["ticker","session_date"]).sample(min(n,len(loc)),random_state=1)
    stored=duckdb.connect().execute(f"SELECT * FROM read_parquet('{LOCAL_TICK}')").df().set_index(["ticker","session_date"])
    print(f"SANITY: API vs local stored doc-242 features on {len(inwin)} in-window ticker-days")
    api_rows=[]; key_rows=[]
    for r in inwin.itertuples():
        f=compute_tick_feats(api_trades(r.ticker,r.session_date))
        if f is None: continue
        api_rows.append(f); key_rows.append((r.ticker,r.session_date))
    A=pd.DataFrame(api_rows,index=pd.MultiIndex.from_tuples(key_rows,names=["ticker","session_date"]))
    S=stored.loc[A.index]
    print(f"  matched {len(A)} ticker-days")
    print(f"  {'feature':<18}{'corr':>8}{'med_rel_diff':>13}")
    for c in TICK:
        a=A[c].to_numpy(); s=S[c].astype(float).to_numpy()
        corr=np.corrcoef(a,s)[0,1] if np.std(a)>0 and np.std(s)>0 else float('nan')
        rel=np.median(np.abs(a-s)/(np.abs(s)+1e-9))
        print(f"  {c:<18}{corr:>8.3f}{rel:>13.3f}")
    print("  -> equivalent if corr~1 and med_rel_diff small (<~0.05) on the signed/ratio features.")

def _pull_one(args):
    tk,d=args
    try: return (tk,d,compute_tick_feats(get_trades(tk,d)))
    except Exception: return (tk,d,None)

def run_full(workers, limit=0):
    dt=corpus()
    if limit:  # smoke test: stratified across years (mix of local in-window + API out-window)
        dt=pd.concat([dt[dt.year==y].head(limit) for y in sorted(dt.year.unique())]).reset_index(drop=True)
        print(f"SMOKE limit={limit}: {len(dt)} candidates across years {sorted(dt.year.unique())}")
    print(f"corpus: {len(dt)} candidates | rockets {int(dt.rocket.sum())} ({dt.rocket.mean()*100:.2f}%) | "
          f"by year {dt.groupby('year').rocket.agg(['size','sum']).to_dict()}")
    # ALL candidates via get_trades (local-sip in-window + API out-window). The doc-242 stored features are
    # DISCARDED (UTC-mislabeled-as-ET bug). Checkpoint-resumable.
    done={}
    if os.path.exists(CKPT):
        for line in open(CKPT,encoding="utf-8"):
            try:
                o=json.loads(line); done[(o["ticker"],o["session_date"])]=o["feats"]
            except Exception: pass
    todo=[(r.ticker,r.session_date) for r in dt.itertuples() if (r.ticker,r.session_date) not in done]
    print(f"  checkpoint: {len(done)} done | to fetch: {len(todo)} (local-sip in-window + API out-window)")
    ck=open(CKPT,"a",encoding="utf-8"); pulled=0; t0=time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for tk,d,f in ex.map(_pull_one,todo):
            done[(tk,d)]=f; pulled+=1
            ck.write(json.dumps({"ticker":tk,"session_date":d,"feats":f})+"\n")
            if pulled%200==0:
                ck.flush(); rate=pulled/(time.time()-t0)
                print(f"  ...fetched {pulled}/{len(todo)} ({rate:.1f}/s, ETA {((len(todo)-pulled)/max(rate,1e-9))/60:.0f}m)")
    ck.close()
    # assemble feature frame
    feats=[done.get((r.ticker,r.session_date)) or {c:np.nan for c in TICK} for r in dt.itertuples()]
    tdf=pd.concat([dt.reset_index(drop=True),pd.DataFrame(feats)],axis=1)
    cov=len(tdf)-tdf.tick_ofi.isna().sum()
    print(f"TICK coverage: {cov}/{len(tdf)} ({cov/len(tdf)*100:.1f}%) | by year "
          f"{tdf.assign(has=tdf.tick_ofi.notna()).groupby('year').has.mean().round(3).to_dict()}")
    tdf=tdf.dropna(subset=["tick_ofi"]).reset_index(drop=True)
    for c in MICRO+TICK: tdf[c]=tdf[c].astype(float).replace([np.inf,-np.inf],np.nan).fillna(0)
    tdf.to_parquet(OUT,index=False)
    print(f"wrote {OUT} ({len(tdf)} rows, rockets {int(tdf.rocket.sum())})")

# ---------------------------------------------------------------------------
def _cpcv_scores(dt, feat, folds=8):
    udays=sorted(dt.session_date.unique()); grp={x:i for i,gg in enumerate(np.array_split(udays,folds)) for x in gg}
    dt=dt.copy(); dt["_g"]=dt.session_date.map(grp); dt["score"]=np.nan
    for gi in range(folds):
        tr=dt[dt._g!=gi]; te=dt.index[dt._g==gi]
        if len(te) and tr.rocket.nunique()>1:
            dt.loc[te,"score"]=GBM(max_iter=300,learning_rate=0.05,max_depth=4,l2_regularization=1.0,
                                   class_weight="balanced").fit(tr[feat],tr.rocket).predict_proba(dt.loc[te,feat])[:,1]
    return dt.dropna(subset=["score"])

def _boot_ci(x, n=3000, seed=7):
    if len(x)<3: return (float('nan'),float('nan'))
    rng=np.random.default_rng(seed); m=[x[rng.integers(0,len(x),len(x))].mean() for _ in range(n)]
    return (float(np.percentile(m,2.5)), float(np.percentile(m,97.5)))

def run_test():
    dt=duckdb.connect().execute(f"SELECT * FROM read_parquet('{OUT}')").df()
    print(f"corpus n={len(dt)} | rockets {int(dt.rocket.sum())} ({dt.rocket.mean()*100:.2f}%) | "
          f"by year {dt.groupby('year').rocket.agg(['size','sum']).to_dict()}\n")
    print("PRE-REGISTERED: MICRO+TICK wins IFF top-5% mean EOD ret >0 in ALL 2024/2025/2026, lift>1 all,")
    print("and it RESCUES 2024 & 2025 (both currently <0 under MICRO-only).\n")
    for name,feat in [("MICRO-only",MICRO),("MICRO+TICK",MICRO+TICK),("TICK-only",TICK)]:
        s=_cpcv_scores(dt,feat)
        pool_auc=roc_auc_score(s.rocket,s.score)
        print(f"=== {name}  (POOL AUC {pool_auc:.3f}) ===")
        print(f"  {'yr':>6}{'AUC':>7}{'top5% n':>9}{'rocket%':>9}{'lift':>6}{'meanEOD':>9}{'  95% CI':>16}{'median':>9}")
        for y in [2024,2025,2026]:
            sy=s[s.year==y]
            if sy.rocket.nunique()<2: continue
            auc=roc_auc_score(sy.rocket,sy.score); base=sy.rocket.mean()
            thr=sy.score.quantile(0.95); top=sy[sy.score>=thr]
            lo,hi=_boot_ci(top.eod.to_numpy())
            print(f"  {y:>6}{auc:>7.3f}{len(top):>9}{top.rocket.mean()*100:>8.0f}%{top.rocket.mean()/base if base>0 else float('nan'):>6.1f}"
                  f"{top.eod.mean()*100:>+8.1f}%{('[%+.1f,%+.1f]'%(lo*100,hi*100)):>16}{top.eod.median()*100:>+8.1f}%")
        print()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--sanity",type=int,default=0)
    ap.add_argument("--workers",type=int,default=10)
    ap.add_argument("--limit",type=int,default=0)
    ap.add_argument("--test",action="store_true")
    a=ap.parse_args()
    if a.sanity: run_sanity(a.sanity)
    elif a.test: run_test()
    else: run_full(a.workers, a.limit)

if __name__=="__main__": main()
