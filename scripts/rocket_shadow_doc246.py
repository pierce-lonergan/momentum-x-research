"""doc 246 — forward PAPER SHADOW of the 2026/current-regime rocket signal. NO capital, NO live change.

doc 245 closed BET#3 as a cross-regime-robust edge: the ex-ante (9:50) microstructure+tape rocket selector
is regime-dependent (real in 2026, absent 2024, breakeven 2025). The ONE honest continuation (Pierce's call):
since *now* IS the 2026 regime and the leave-one-regime-out test showed a model trained on PAST years still
ranks 2026 rockets (+10.2% top-5%), freeze a model on all data through now and **shadow it forward** — score
the live gapper watchlist each day, log the top-5%/top-1 rocket-slice, and track its realized OOS return in
real time. Explicitly a REGIME BET that will silently fail if the regime shifts; the shadow tells us truthfully.

Features are computed EXACTLY as the frozen corpus (reuses build_cross_regime_corpus.label_day for MICRO and
build_xregime_tick_features_doc245.compute_tick_feats for TICK -> no train/serve skew). A day with
session_date > model cutoff is FORWARD-OOS (counts toward the track record); <= cutoff is in-sample validation.

Usage:
  python scripts/rocket_shadow_doc246.py --train            # freeze GBM(MICRO+TICK) on the xregime corpus
  python scripts/rocket_shadow_doc246.py --run 2026-06-02   # score one day, append to the shadow log
  python scripts/rocket_shadow_doc246.py --run-since 2026-06-02   # score every trading day from a date to now
  python scripts/rocket_shadow_doc246.py --report           # summarize the accumulated OOS track record
"""
from __future__ import annotations
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd, duckdb, joblib
from sklearn.ensemble import HistGradientBoostingClassifier as GBM
from build_cross_regime_corpus import label_day
from build_xregime_tick_features_doc245 import compute_tick_feats, get_trades, MICRO, TICK, ENTRY, R

DA="data/polygon_warehouse/day_aggs/**/*.parquet"
MN="data/polygon_warehouse/minute_aggs/**/*.parquet"
CORPUS="data/research/rocket_tick_features_xregime.parquet"
MODEL="data/research/rocket_shadow_model.pkl"
LOG="data/research/rocket_shadow_log.jsonl"

def train_and_freeze():
    df=duckdb.connect().execute(f"SELECT * FROM read_parquet('{CORPUS}')").df()
    for c in MICRO+TICK: df[c]=df[c].astype(float).replace([np.inf,-np.inf],np.nan).fillna(0)
    m=GBM(max_iter=300,learning_rate=0.05,max_depth=4,l2_regularization=1.0,class_weight="balanced").fit(df[MICRO+TICK],df.rocket)
    cutoff=str(df.session_date.max())
    joblib.dump({"model":m,"feats":MICRO+TICK,"cutoff":cutoff,"n":int(len(df)),"rockets":int(df.rocket.sum())},MODEL)
    print(f"froze model -> {MODEL} | n={len(df)} rockets={int(df.rocket.sum())} cutoff={cutoff} (forward = session_date > cutoff)")

def gate_candidates(con, date):
    return con.execute(f"""
      WITH b AS (SELECT ticker, ts_et::DATE d, open, close,
                  lag(close) OVER (PARTITION BY ticker ORDER BY ts_et) pc,
                  avg(volume*close) OVER (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) adv20
                 FROM read_parquet('{DA}'))
      SELECT ticker, open AS base_close, adv20 FROM b
      WHERE d=DATE '{date}' AND pc>0 AND open/pc-1>=0.08 AND open BETWEEN 0.50 AND 20.0 AND adv20>=1e6""").df()

def micro_and_label(bars):
    """One ticker-day RTH bars -> the 12 minute MICRO features at the 9:50 entry + realized eod/rocket.
    Mirrors build_xregime_tick_features_doc245.corpus() exactly (label_day -> ENTRY=3 -> minute_idx 20)."""
    rr=label_day(bars)
    if not rr: return None
    g=pd.DataFrame(rr).sort_values("minute_idx").reset_index(drop=True)
    if len(g)<=ENTRY+6 or (1+g.ret_session.iloc[ENTRY])<=0: return None
    rel=1+g.ret_session.to_numpy(); eod=rel[-1]/rel[ENTRY]-1
    runhi=rel[ENTRY:].max()/rel[ENTRY]
    sustain=(rel[-1]-rel[ENTRY])/(runhi*rel[ENTRY]-rel[ENTRY]) if runhi>1+1e-6 else 0
    row=g.iloc[ENTRY][MICRO[:-2]].to_dict()  # 12 minute-derived (base_close, adv20 added by caller)
    row["eod"]=float(eod); row["rocket"]=int(eod>=R and sustain>=0.5)
    return row

def run_day(date, art=None, con=None):
    art=art or joblib.load(MODEL); m=art["model"]; feats=art["feats"]; cutoff=art["cutoff"]
    con=con or duckdb.connect()
    cand=gate_candidates(con,date)
    if cand.empty: print(f"{date}: 0 gapper candidates (gate)"); return
    yr=int(date[:4]); tickers=",".join("'"+t.replace("'","")+"'" for t in cand.ticker)
    bars=con.execute(f"""SELECT ticker, ts_et, open, high, low, close, volume FROM read_parquet('{MN}', hive_partitioning=1)
        WHERE year={yr} AND ts_et::DATE = DATE '{date}' AND ticker IN ({tickers})""").df()
    if bars.empty: print(f"{date}: {len(cand)} candidates but no minute bars yet (data not ingested?)"); return
    bars["ts_et"]=pd.to_datetime(bars.ts_et,utc=True).dt.tz_convert("America/New_York")
    mins=bars.ts_et.dt.hour*60+bars.ts_et.dt.minute; bars=bars[(mins>=570)&(mins<960)]
    rows=[]
    for tk,g in bars.groupby("ticker"):
        ml=micro_and_label(g.reset_index(drop=True))
        if ml is None: continue
        tf=compute_tick_feats(get_trades(tk,date))
        if tf is None: continue
        c=cand[cand.ticker==tk].iloc[0]
        rows.append({**ml,**tf,"ticker":tk,"base_close":float(c.base_close),"adv20":float(c.adv20)})
    if not rows: print(f"{date}: 0 scorable candidates (no micro+tick)"); return
    X=pd.DataFrame(rows)
    for col in feats: X[col]=X[col].astype(float).replace([np.inf,-np.inf],np.nan).fillna(0)
    X["score"]=m.predict_proba(X[feats])[:,1]
    X=X.sort_values("score",ascending=False).reset_index(drop=True)
    X["is_top5"]=(X.score>=X.score.quantile(0.95)).astype(int); X["is_top1"]=0
    if len(X): X.loc[0,"is_top1"]=1
    forward=date>cutoff
    # de-dup: drop any existing log rows for this date, then append
    if os.path.exists(LOG):
        keep=[l for l in open(LOG,encoding="utf-8") if json.loads(l).get("date")!=date]
        open(LOG,"w",encoding="utf-8").writelines(keep)
    with open(LOG,"a",encoding="utf-8") as f:
        for r in X.itertuples():
            f.write(json.dumps({"date":date,"ticker":r.ticker,"score":round(float(r.score),4),
                "eod":round(float(r.eod),4),"rocket":int(r.rocket),"is_top5":int(r.is_top5),
                "is_top1":int(r.is_top1),"forward":int(forward)})+"\n")
    top=X[X.is_top5==1]
    tag="FORWARD-OOS" if forward else "in-sample"
    print(f"{date} [{tag}]: {len(X)} candidates -> {len(top)} top-5% | rockets-today {int(X.rocket.sum())}")
    print(f"  picks: "+", ".join(f"{r.ticker}(p{r.score:.2f},eod{r.eod*100:+.0f}%{'*ROCKET' if r.rocket else ''})" for r in top.head(6).itertuples()))
    print(f"  top-1 {X.iloc[0].ticker} p{X.iloc[0].score:.2f} eod{X.iloc[0].eod*100:+.1f}% | top-5% mean eod {top.eod.mean()*100:+.1f}% (median {top.eod.median()*100:+.1f}%)")

def run_since(start):
    art=joblib.load(MODEL); con=duckdb.connect()
    days=con.execute(f"SELECT DISTINCT ts_et::DATE d FROM read_parquet('{DA}') WHERE ts_et::DATE >= DATE '{start}' ORDER BY d").df()
    for d in days.d: run_day(str(d)[:10], art, con)   # YYYY-MM-DD only (str(date) can carry ' 00:00:00')

def run_catchup():
    """Daily entrypoint: re-score from a couple days before the last logged date (de-dup makes re-runs safe;
    the small look-back retro-fills days whose warehouse data arrived late) through whatever is now ingested."""
    from datetime import datetime, timedelta
    start=None
    if os.path.exists(LOG):
        ds=[json.loads(l)["date"] for l in open(LOG,encoding="utf-8") if l.strip()]
        if ds: start=(datetime.strptime(max(ds),"%Y-%m-%d")-timedelta(days=2)).strftime("%Y-%m-%d")
    if start is None: start=joblib.load(MODEL)["cutoff"]
    print(f"catchup since {start}")
    run_since(start)

def report():
    if not os.path.exists(LOG): print("no shadow log yet — run --run/--run-since first"); return
    df=pd.DataFrame([json.loads(l) for l in open(LOG,encoding="utf-8")])
    if df.empty: print("empty log"); return
    print(f"shadow log: {df.date.nunique()} days, {len(df)} candidate-rows, {df.forward.sum()} forward-OOS rows")
    print(f"  {'scope':>10}{'days':>6}{'cand':>7}{'top5%n':>8}{'t5_meanEOD':>12}{'t5_med':>9}{'t5_rocket%':>11}{'top1_meanEOD':>13}{'top1_hit%':>10}")
    for scope in ["FORWARD","all"]:
        d=df[df.forward==1] if scope=="FORWARD" else df
        if d.empty: print(f"  {scope:>10}   (none yet)"); continue
        top=d[d.is_top5==1]; t1=d[d.is_top1==1]
        print(f"  {scope:>10}{d.date.nunique():>6}{len(d):>7}{len(top):>8}{top.eod.mean()*100:>+11.1f}%{top.eod.median()*100:>+8.1f}%"
              f"{top.rocket.mean()*100:>10.0f}%{t1.eod.mean()*100:>+12.1f}%{(t1.eod>0).mean()*100:>9.0f}%")
    print("  (FORWARD = the honest out-of-sample track record; 'all' includes in-sample validation days.)")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--train",action="store_true")
    ap.add_argument("--run",type=str,default="")
    ap.add_argument("--run-since",type=str,default="")
    ap.add_argument("--catchup",action="store_true")
    ap.add_argument("--report",action="store_true")
    a=ap.parse_args()
    if a.train: train_and_freeze()
    elif a.run: run_day(a.run)
    elif a.run_since: run_since(a.run_since)
    elif a.catchup: run_catchup()
    elif a.report: report()
    else: ap.print_help()

if __name__=="__main__": main()
