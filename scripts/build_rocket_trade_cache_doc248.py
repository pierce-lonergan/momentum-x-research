"""doc 248 - cache RAW early-window (09:30-09:50) trades per gapper candidate, for the Deep-Sets raw-tape screen.

doc 245 discarded the raw trades (kept only the 11 aggregates doc 245 then falsified). The raw-tape bet
(Pierce: "take the raw-tape shot") needs the un-aggregated trades back. Re-fetch via the SAME tz-correct
get_trades (local-sip in-window + API out-window; ET from raw sip_timestamp), keep the early-window trades,
cache them so both the cheap mean-pool screen AND the later full Set Transformer reuse them. Resumable, parallel.

Output: data/research/rocket_early_trades/part_*.parquet  [ticker, session_date, secs, price, size]
        (secs = seconds since 09:30; the 09:30-09:50 window so secs in [0,1200))
"""
from __future__ import annotations
import sys, os, json, time, argparse, concurrent.futures as cf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd, duckdb
from build_xregime_tick_features_doc245 import corpus, get_trades

OUT_DIR="data/research/rocket_early_trades"
CKPT="data/research/_trade_cache_ckpt.jsonl"

def early_trades(tk, d):
    df=get_trades(tk, d)               # premarket+early, ts_et/price/size, ET from sip_timestamp
    if df is None: return None
    mins=df.ts_et.dt.hour*60+df.ts_et.dt.minute
    e=df[(mins>=570)&(mins<590)]       # 09:30-09:50
    if len(e)<3: return None
    secs=((e.ts_et.dt.hour*3600+e.ts_et.dt.minute*60+e.ts_et.dt.second+e.ts_et.dt.microsecond/1e6)-570*60).to_numpy(np.float32)
    return pd.DataFrame({"ticker":tk,"session_date":d,"secs":secs,
                         "price":e.price.to_numpy(np.float32),"size":e["size"].to_numpy(np.float32)})

def _one(args):
    tk,d=args
    try: return (tk,d,early_trades(tk,d))
    except Exception: return (tk,d,None)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--workers",type=int,default=12); a=ap.parse_args()
    os.makedirs(OUT_DIR,exist_ok=True)
    dt=corpus()
    cand=[(r.ticker,r.session_date) for r in dt.itertuples()]
    done=set()
    if os.path.exists(CKPT):
        for l in open(CKPT,encoding="utf-8"):
            try: o=json.loads(l); done.add((o["ticker"],o["session_date"]))
            except Exception: pass
    todo=[c for c in cand if c not in done]
    part=len([f for f in os.listdir(OUT_DIR) if f.endswith(".parquet")])
    print(f"candidates {len(cand)} | already done {len(done)} | to fetch {len(todo)}")
    ck=open(CKPT,"a",encoding="utf-8"); buf=[]; n=0; t0=time.time()
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for tk,d,df in ex.map(_one,todo):
            n+=1; ck.write(json.dumps({"ticker":tk,"session_date":d,"ok":df is not None})+"\n")
            if df is not None: buf.append(df)
            if n%2000==0:
                if buf: pd.concat(buf,ignore_index=True).to_parquet(f"{OUT_DIR}/part_{part:04d}.parquet",index=False); part+=1; buf=[]
                ck.flush(); r=n/(time.time()-t0)
                print(f"  {n}/{len(todo)} ({r:.1f}/s, ETA {((len(todo)-n)/max(r,1e-9))/60:.0f}m)")
    if buf: pd.concat(buf,ignore_index=True).to_parquet(f"{OUT_DIR}/part_{part:04d}.parquet",index=False)
    ck.close()
    tot=duckdb.connect().execute(f"SELECT count(*), count(distinct ticker||session_date) FROM read_parquet('{OUT_DIR}/*.parquet')").fetchone()
    print(f"cached {tot[0]:,} early trades across {tot[1]:,} ticker-days -> {OUT_DIR}/")

if __name__=="__main__": main()
