"""doc 249 - the DATA levers (doc 246/248 register). Free-float is HARD-BLOCKED (Finnhub exposes no
free-float, only current shareOutstanding = doc 243's already-negative quantity + a point-in-time/staleness
problem; no historical free-float source). So this tests the one remaining lever: CATALYST/NEWS CONTENT.

No LLM needed: Polygon /v2/reference/news ships per-article SENTIMENT (insights), and keyword tagging of
titles gives catalyst-TYPE. Coverage is low (~24% of candidates have any pre-9:50 news - most rockets are
no-news squeezes), so the realistic prior is weak; this is the honest one-shot test. Features per candidate
(news in [session_date-3d .. session_date 13:50Z], i.e. prior-day AH + overnight + premarket, strictly ex-ante):
  has_news, news_count, sent_pos_frac, sent_neg_frac, sent_net, and keyword flags
  kw_fda / kw_offering(dilution) / kw_ma / kw_earnings / kw_contract.
Then add to the 25 and run the LORO top-5% realized-return money gate vs the GBM. NO live change.

Usage: python scripts/build_news_features_doc249.py            # fetch + build (resumable, parallel)
       python scripts/build_news_features_doc249.py --test     # LORO money test (25 vs 25+news)
"""
from __future__ import annotations
import os, json, re, time, argparse, urllib.request, datetime as dt, concurrent.futures as cf
import numpy as np, pandas as pd, duckdb
from sklearn.ensemble import HistGradientBoostingClassifier as GBM
from sklearn.metrics import roc_auc_score

CORPUS="data/research/rocket_tick_features_xregime.parquet"
OUT="data/research/rocket_news_features.parquet"
CKPT="data/research/_news_feat_ckpt.jsonl"
MICRO=["ret_session","ret_5m","ret_15m","ret_30m","vwap_dist","high_dist","low_dist","range_pos",
       "rvol_cum","vol_accel_5m","realized_vol_15m","up_min_frac_15m","base_close","adv20"]
TICK=["pm_vol","pm_dollarvol","early_vol","early_trades","tick_ofi","tick_ofi_late","large_print_ratio",
      "mean_trade_size","trade_intensity","tick_vwap_dist","odd_lot_ratio"]
NEWS=["has_news","news_count","sent_pos_frac","sent_neg_frac","sent_net","kw_fda","kw_offering","kw_ma","kw_earnings","kw_contract"]
CTX=MICRO+TICK
_K={"kw_fda":r"fda|approv|phase|clinical|trial|\bnda\b|pdufa|topline|endpoint",
    "kw_offering":r"offering|priced|registered direct|\batm\b|warrant|dilut|\bpipe\b|reverse split|shelf",
    "kw_ma":r"merger|acquir|acquisition|buyout|takeover|strategic review",
    "kw_earnings":r"earnings|revenue|results|quarter|guidance|profit|loss per share",
    "kw_contract":r"contract|award|partnership|agreement|collaboration|deal|order|grant"}
_KEY=None
def key():
    global _KEY
    if _KEY: return _KEY
    for l in open(os.path.expanduser('~/momentum-x-secrets.env'),encoding='utf-8',errors='replace'):
        if l.startswith('POLYGON_API_KEY='): _KEY=l.split('=',1)[1].strip().strip('"')
    return _KEY

def news_feats(tk, d):
    d0=(dt.date.fromisoformat(d)-dt.timedelta(days=3)).isoformat()
    url=(f"https://api.polygon.io/v2/reference/news?ticker={tk}&published_utc.gte={d0}T00:00:00Z"
         f"&published_utc.lte={d}T13:50:00Z&order=desc&limit=50&apiKey={key()}")
    res=None
    for _ in range(3):
        try: res=json.load(urllib.request.urlopen(url,timeout=25)).get("results",[]); break
        except Exception: time.sleep(0.5)
    if res is None: return None
    n=len(res); pos=neg=0; txt=""
    for a in res:
        txt+=" "+(a.get("title","")+" "+a.get("description",""))
        for i in a.get("insights",[]):
            if i.get("ticker")==tk:
                if i.get("sentiment")=="positive": pos+=1
                elif i.get("sentiment")=="negative": neg+=1
    tl=txt.lower(); tot=max(pos+neg,1)
    f=dict(has_news=float(n>0), news_count=float(n),
           sent_pos_frac=pos/tot, sent_neg_frac=neg/tot, sent_net=(pos-neg)/tot)
    for k,pat in _K.items(): f[k]=float(bool(re.search(pat,tl))) if n>0 else 0.0
    return f

def _one(args):
    tk,d=args
    try: return (tk,d,news_feats(tk,d))
    except Exception: return (tk,d,None)

def build(workers):
    dt_=duckdb.connect().execute(f"SELECT * FROM read_parquet('{CORPUS}')").df()
    done={}
    if os.path.exists(CKPT):
        for l in open(CKPT,encoding="utf-8"):
            try: o=json.loads(l); done[(o["ticker"],o["session_date"])]=o["f"]
            except Exception: pass
    todo=[(r.ticker,r.session_date) for r in dt_.itertuples() if (r.ticker,r.session_date) not in done]
    print(f"corpus {len(dt_)} | checkpoint {len(done)} | to fetch {len(todo)}")
    ck=open(CKPT,"a",encoding="utf-8"); n=0; t0=time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for tk,d,f in ex.map(_one,todo):
            done[(tk,d)]=f; n+=1; ck.write(json.dumps({"ticker":tk,"session_date":d,"f":f})+"\n")
            if n%1000==0: ck.flush(); r=n/(time.time()-t0); print(f"  {n}/{len(todo)} ({r:.1f}/s, ETA {((len(todo)-n)/max(r,1e-9))/60:.0f}m)")
    ck.close()
    feats=[done.get((r.ticker,r.session_date)) or {c:0.0 for c in NEWS} for r in dt_.itertuples()]
    out=pd.concat([dt_.reset_index(drop=True),pd.DataFrame(feats)],axis=1)
    cov=(out.has_news>0).mean()
    print(f"news coverage {cov*100:.1f}% | rocket cov {(out[out.rocket==1].has_news>0).mean()*100:.0f}% vs fader {(out[out.rocket==0].has_news>0).mean()*100:.0f}%")
    out.to_parquet(OUT,index=False); print(f"wrote {OUT} ({len(out)} rows)")

def topslice(te,f=0.05):
    t=te[te.score>=te.score.quantile(1-f)]; return float(t.eod.mean())
def run_test():
    d=duckdb.connect().execute(f"SELECT * FROM read_parquet('{OUT}')").df()
    for c in CTX+NEWS: d[c]=d[c].astype(float).replace([np.inf,-np.inf],np.nan).fillna(0.0)
    print("=== LORO top-5% realized EOD return: GBM(25) vs GBM(25+news) ===")
    print(f"  {'regime':>7}{'base GBM':>10}{'+news':>9}{'Δ':>8}{'base AUC':>10}{'+news AUC':>11}")
    def loro(feat):
        out={}
        for ty in [2024,2025,2026]:
            tr=d[d.year!=ty]; te=d[d.year==ty].copy()
            te["score"]=GBM(max_iter=300,learning_rate=0.05,max_depth=4,l2_regularization=1.0,class_weight="balanced").fit(tr[feat],tr.rocket).predict_proba(te[feat])[:,1]
            out[ty]=te
        return out
    base=loro(CTX); plus=loro(CTX+NEWS)
    for ty in [2024,2025,2026]:
        b=topslice(base[ty]); p=topslice(plus[ty])
        ba=roc_auc_score(base[ty].rocket,base[ty].score); pa=roc_auc_score(plus[ty].rocket,plus[ty].score)
        print(f"  {ty:>7}{b*100:>+9.1f}%{p*100:>+8.1f}%{(p-b)*100:>+7.1f}%{ba:>10.3f}{pa:>11.3f}")
    g2=pd.concat([base[2024],base[2025]]); p2=pd.concat([plus[2024],plus[2025]])
    print(f"\n  POOLED 2024+2025: GBM {topslice(g2)*100:+.1f}% | +news {topslice(p2)*100:+.1f}%")
    print("  -> news adds signal ONLY if it lifts the 2024+2025 top-slice positive/CI-separated over GBM(25). Prior is weak (24% coverage).")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--workers",type=int,default=12); ap.add_argument("--test",action="store_true")
    a=ap.parse_args()
    if a.test: run_test()
    else: build(a.workers)

if __name__=="__main__": main()
