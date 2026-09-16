"""doc 244 (BET#3) — TRUE OFI from L2 quotes (Lee-Ready) via the Polygon quotes API. No disk needed.

doc 242 used TICK-RULE OFI (a proxy). The doc-187-validated signal is true book-imbalance OFI: trades
signed by the prevailing NBBO (Lee-Ready), plus quoted spread + depth-at-touch ("real bid wall"). Pull
9:30-9:50 NBBO via API for a sample (all rockets + controls), align to local trades, compute true OFI +
quote features, and test: does TRUE OFI beat tick-rule OFI on rocket discrimination? NO live change.
"""
from __future__ import annotations
import os, time, json, urllib.request, numpy as np, pandas as pd, duckdb
from datetime import datetime
from zoneinfo import ZoneInfo
from sklearn.ensemble import HistGradientBoostingClassifier as GBM
from sklearn.metrics import roc_auc_score

ET=ZoneInfo("America/New_York"); UTC=ZoneInfo("UTC")
TRADES="data/polygon_warehouse/trades_v1_parquet"
MICRO=["ret_session","ret_5m","ret_15m","ret_30m","vwap_dist","high_dist","low_dist","range_pos","rvol_cum","vol_accel_5m","realized_vol_15m","up_min_frac_15m","base_close","adv20"]
TICK=["pm_vol","pm_dollarvol","early_vol","early_trades","tick_ofi","tick_ofi_late","large_print_ratio","mean_trade_size","trade_intensity","tick_vwap_dist","odd_lot_ratio"]
TRUE=["true_ofi","true_ofi_late","quoted_spread","depth_imb","depth_imb_late","quote_intensity"]
KEY=None
def key():
    global KEY
    if KEY: return KEY
    for l in open(os.path.expanduser('~/momentum-x-secrets.env'),encoding='utf-8',errors='replace'):
        l=l.rstrip('\r\n')
        if l.startswith('POLYGON_API_KEY=') and not l.lstrip().startswith('#'): KEY=l.split('=',1)[1].strip().strip('"')
    return KEY

def utc_ns(d, hh, mm):
    dt=datetime(int(d[:4]),int(d[5:7]),int(d[8:10]),hh,mm,tzinfo=ET).astimezone(UTC)
    return int(dt.timestamp()*1e9)

def pull_quotes(tk,d):
    g0=utc_ns(d,9,30); g1=utc_ns(d,9,50)
    url=f"https://api.polygon.io/v3/quotes/{tk}?timestamp.gte={g0}&timestamp.lt={g1}&order=asc&limit=50000&apiKey={key()}"
    out=[]
    for _ in range(6):
        for attempt in range(4):
            try: dd=json.load(urllib.request.urlopen(url,timeout=40)); break
            except Exception: time.sleep(1.0)
        else: return None
        out+=dd.get('results',[])
        nu=dd.get('next_url')
        if not nu: break
        url=nu+f"&apiKey={key()}"
        time.sleep(0.15)
    if not out: return None
    q=pd.DataFrame(out)[['sip_timestamp','bid_price','ask_price','bid_size','ask_size']].dropna()
    q=q[(q.bid_price>0)&(q.ask_price>=q.bid_price)]
    return q if len(q)>=5 else None

def local_trades(tk,d):
    p=f"{TRADES}/year={d[:4]}/month={d[5:7]}/day={d[8:10]}/ticker={tk}/data_0.parquet"
    if not os.path.exists(p): return None
    g0=utc_ns(d,9,30); g1=utc_ns(d,9,50)
    try:
        t=duckdb.connect().execute(f"SELECT sip_timestamp, price, size FROM read_parquet('{p}') WHERE price>0 AND size>0 AND sip_timestamp>={g0} AND sip_timestamp<{g1} ORDER BY sip_timestamp").df()
    except Exception: return None
    return t if len(t)>=5 else None

def features(tk,d):
    q=pull_quotes(tk,d); t=local_trades(tk,d)
    if q is None or t is None: return None
    q=q.sort_values('sip_timestamp'); q['mid']=(q.bid_price+q.ask_price)/2
    m=pd.merge_asof(t.sort_values('sip_timestamp'),q[['sip_timestamp','mid','bid_price','ask_price']],on='sip_timestamp',direction='backward').dropna(subset=['mid'])
    if len(m)<5: return None
    sign=np.where(m.price>m.mid,1.0,np.where(m.price<m.mid,-1.0,0.0))
    for i in range(1,len(sign)):
        if sign[i]==0: sign[i]=sign[i-1]
    sz=m['size'].to_numpy().astype(float); tot=sz.sum()
    true_ofi=float((sign*sz).sum()/tot) if tot>0 else 0.0
    half=len(m)//2; lsz=sz[half:]; lsg=sign[half:]
    spread=float(((q.ask_price-q.bid_price)/q.mid).mean())
    di=float(((q.bid_size-q.ask_size)/(q.bid_size+q.ask_size).clip(lower=1)).mean())
    qh=len(q)//2; di_late=float(((q.bid_size.iloc[qh:]-q.ask_size.iloc[qh:])/(q.bid_size.iloc[qh:]+q.ask_size.iloc[qh:]).clip(lower=1)).mean())
    return dict(true_ofi=true_ofi, true_ofi_late=float((lsg*lsz).sum()/max(lsz.sum(),1)),
                quoted_spread=spread, depth_imb=di, depth_imb_late=di_late, quote_intensity=float(len(q)/20.0))

def main():
    df=duckdb.connect().execute("SELECT * FROM read_parquet('data/research/rocket_tick_features.parquet')").df()
    df["lab"]=(df.eod>=0.20).astype(int)
    rng=np.random.default_rng(1)
    rk=df[df.lab==1]; ctrl=df[df.lab==0].sample(min(260,(df.lab==0).sum()),random_state=1)
    samp=pd.concat([rk,ctrl]).reset_index(drop=True)
    print(f"sample: {len(samp)} ticker-days ({rk.shape[0]} rockets eod>=20% + {ctrl.shape[0]} controls)")
    feats=[]; miss=0
    for i,r in enumerate(samp.itertuples()):
        f=features(r.ticker,r.session_date)
        if f is None: miss+=1; f={k:np.nan for k in TRUE}
        feats.append(f)
        if (i+1)%50==0: print(f"  ...{i+1}/{len(samp)} ({miss} missing)")
    s=pd.concat([samp.reset_index(drop=True),pd.DataFrame(feats)],axis=1)
    s=s.dropna(subset=["true_ofi"]).reset_index(drop=True)
    print(f"true-OFI coverage: {len(s)}/{len(samp)} ({miss} missing quotes/trades)")
    for c in MICRO+TICK+TRUE: s[c]=s[c].astype(float).replace([np.inf,-np.inf],np.nan).fillna(0)
    s.to_parquet("data/research/rocket_true_ofi.parquet",index=False)
    # sanity: rocket vs non-rocket true_ofi / depth
    print(f"  rocket true_ofi {s[s.lab==1].true_ofi.median():+.3f} vs non {s[s.lab==0].true_ofi.median():+.3f} | "
          f"depth_imb {s[s.lab==1].depth_imb.median():+.3f} vs {s[s.lab==0].depth_imb.median():+.3f} | "
          f"tick_ofi {s[s.lab==1].tick_ofi.median():+.3f} vs {s[s.lab==0].tick_ofi.median():+.3f}")
    ud=sorted(s.session_date.unique()); grp={x:i for i,g in enumerate(np.array_split(ud,5)) for x in g}; s["_g"]=s.session_date.map(grp)
    def oos(feat):
        x=s.copy(); x["p"]=np.nan
        for gi in range(5):
            tr=x[x._g!=gi]; te=x.index[x._g==gi]
            if len(te) and tr.lab.nunique()>1: x.loc[te,"p"]=GBM(max_iter=300,learning_rate=0.05,max_depth=4,l2_regularization=1.0,class_weight="balanced").fit(tr[feat],tr.lab).predict_proba(x.loc[te,feat])[:,1]
        return x.dropna(subset=["p"]).p.to_numpy()
    y=s.lab.to_numpy()
    base=oos(MICRO+TICK); plus=oos(MICRO+TICK+TRUE); tonly=oos(TRUE)
    rng=np.random.default_rng(7); d=[]
    for _ in range(3000):
        idx=rng.integers(0,len(y),len(y))
        if 2<y[idx].sum()<len(idx)-2: d.append(roc_auc_score(y[idx],plus[idx])-roc_auc_score(y[idx],base[idx]))
    d=np.array(d)
    print(f"\n=== does TRUE OFI (L2) beat tick-rule? (oversampled rocket-discrimination, CPCV OOS) ===")
    print(f"  micro+tick(tick-rule) AUC {roc_auc_score(y,base):.3f}")
    print(f"  micro+tick+TRUE-OFI   AUC {roc_auc_score(y,plus):.3f}  | lift {d.mean():+.3f} CI[{np.percentile(d,2.5):+.3f},{np.percentile(d,97.5):+.3f}] {'SIG' if np.percentile(d,2.5)>0 else 'ns'}")
    print(f"  TRUE-OFI-only         AUC {roc_auc_score(y,tonly):.3f}")
    print("  (oversampled rockets for power; AUC-LIFT is the comparison-valid metric. WIN if true-OFI lifts AUC over tick-rule.)")

if __name__=="__main__": main()
