"""doc 248 - Deep-Sets MEAN-POOL screen over the RAW opening-trade set (the cheap raw-tape gate).

KILL CRITERION 2a (doc 247): if a learned per-trade transform + masked mean-pool over the un-aggregated
09:30-09:50 trades, fused with the 25 context features, shows NO 2024+2025 top-slice lift over the GBM under
leave-one-regime-out, then the attention (Set Transformer) version won't either -> kill before the full run.
This is the one input Phase 0 did not test (Phase 0 used the aggregates; this uses the raw tape).

Per-trade channels (per-day-normalized, scale-free -> no cross-regime leakage): signed_size_frac,
log_price_rel_vwap, dt_frac, is_block(>=5000), is_odd_lot(<100), frac_elapsed. Empty/thin tape -> empty set
(masked) -> model degrades to context-only. Context = the 25 features (rank-Gaussian per train fold).
Decision metric = top-5% realized EOD-from-9:50 return per regime, paired vs GBM, day-block bootstrap. NO live change.
"""
from __future__ import annotations
import os, glob, numpy as np, pandas as pd, duckdb, torch, torch.nn as nn
from sklearn.ensemble import HistGradientBoostingClassifier as GBM
from sklearn.preprocessing import QuantileTransformer
from sklearn.metrics import roc_auc_score

CORPUS="data/research/rocket_tick_features_xregime.parquet"
TRADES="data/research/rocket_early_trades/*.parquet"
MICRO=["ret_session","ret_5m","ret_15m","ret_30m","vwap_dist","high_dist","low_dist","range_pos",
       "rvol_cum","vol_accel_5m","realized_vol_15m","up_min_frac_15m","base_close","adv20"]
TICK=["pm_vol","pm_dollarvol","early_vol","early_trades","tick_ofi","tick_ofi_late","large_print_ratio",
      "mean_trade_size","trade_intensity","tick_vwap_dist","odd_lot_ratio"]
CTX=MICRO+TICK; NMAX=1024; NCH=6; TOPF=0.05
DEV='cuda' if torch.cuda.is_available() else 'cpu'

def per_trade_channels(secs,px,sz):
    o=np.argsort(secs,kind="stable"); secs,px,sz=secs[o],px[o],sz[o]
    sign=np.sign(np.diff(px,prepend=px[0]))
    for i in range(1,len(sign)):
        if sign[i]==0: sign[i]=sign[i-1]
    tot=max(sz.sum(),1e-9); cv=np.cumsum(px*sz)/np.maximum(np.cumsum(sz),1e-9)
    ch=np.stack([sign*sz/tot, np.log(np.maximum(px,1e-9)/np.maximum(cv,1e-9)),
                 np.diff(secs,prepend=secs[0])/1200.0, (sz>=5000).astype(np.float32),
                 (sz<100).astype(np.float32), secs/1200.0],axis=1).astype(np.float32)
    if len(ch)>NMAX:                       # keep all blocks + importance-sample the rest
        blk=np.where(sz>=5000)[0]; rest=np.where(sz<5000)[0]
        keep=np.concatenate([blk, np.random.default_rng(0).choice(rest,max(NMAX-len(blk),0),replace=False)]) if len(blk)<NMAX else blk[:NMAX]
        ch=ch[np.sort(keep)]
    return ch

def build():
    dt=duckdb.connect().execute(f"SELECT * FROM read_parquet('{CORPUS}')").df()
    for c in CTX: dt[c]=dt[c].astype(float).replace([np.inf,-np.inf],np.nan).fillna(0.0)
    sets={}                                  # iterate parquet PARTS (~1GB each) to bound memory vs a 120M-row load
    for pf in sorted(glob.glob(TRADES)):
        tr=duckdb.connect().execute(f"SELECT ticker,session_date,secs,price,size FROM read_parquet('{pf}')").df()
        for (tk,d),g in tr.groupby(["ticker","session_date"],sort=False):
            sets[(tk,d)]=per_trade_channels(g.secs.to_numpy(np.float32),g.price.to_numpy(np.float32),g["size"].to_numpy(np.float32))
        del tr
    M=len(dt); X=np.zeros((M,NMAX,NCH),np.float32); mask=np.zeros((M,NMAX),np.float32); have=0
    for i,r in enumerate(dt.itertuples()):
        ch=sets.get((r.ticker,r.session_date))
        if ch is not None and len(ch)>=3:
            n=min(len(ch),NMAX); X[i,:n]=ch[:n]; mask[i,:n]=1.0; have+=1
    print(f"corpus {M} | rockets {int(dt.rocket.sum())} | tape sets attached {have} ({have/M*100:.1f}%) | device {DEV}")
    return dt, X, mask

class DeepSets(nn.Module):
    def __init__(self):
        super().__init__()
        self.phi=nn.Sequential(nn.Linear(NCH,32),nn.ReLU(),nn.Linear(32,32),nn.ReLU())
        self.ctx=nn.Sequential(nn.Linear(len(CTX),32),nn.ReLU(),nn.Dropout(0.3))
        self.head=nn.Sequential(nn.Linear(64,32),nn.ReLU(),nn.Dropout(0.4),nn.Linear(32,1))
    def forward(self,X,mask,ctx):
        h=self.phi(X)*mask.unsqueeze(-1)
        emb=h.sum(1)/mask.sum(1,keepdim=True).clamp(min=1.0)     # masked mean-pool (empty set -> 0)
        return self.head(torch.cat([emb,self.ctx(ctx)],-1)).squeeze(-1)

def train_score(Xtr,mtr,ctr,ytr, Xte,mte,cte, seeds=5, epochs=40):
    pw=torch.tensor([(ytr==0).sum()/max((ytr==1).sum(),1)],dtype=torch.float32,device=DEV)
    Xtr_,mtr_,ctr_,ytr_=[torch.tensor(a,device=DEV) for a in (Xtr,mtr,ctr,ytr.astype(np.float32))]
    Xte_,mte_,cte_=[torch.tensor(a,device=DEV) for a in (Xte,mte,cte)]
    preds=[]
    for s in range(seeds):
        torch.manual_seed(s); m=DeepSets().to(DEV)
        opt=torch.optim.AdamW(m.parameters(),lr=3e-4,weight_decay=1e-2)
        lossf=nn.BCEWithLogitsLoss(pos_weight=pw)
        n=len(ytr_);
        for ep in range(epochs):
            m.train(); perm=torch.randperm(n,device=DEV)
            for b in range(0,n,512):
                idx=perm[b:b+512]; opt.zero_grad()
                out=m(Xtr_[idx],mtr_[idx],ctr_[idx]); lossf(out,ytr_[idx]).backward(); opt.step()
        m.eval()
        with torch.no_grad(): preds.append(torch.sigmoid(m(Xte_,mte_,cte_)).cpu().numpy())
    return np.mean(preds,axis=0)

def topslice(te):
    t=te[te.score>=te.score.quantile(1-TOPF)]; return float(t.eod.mean()),float(t.eod.median()),float(t.rocket.mean()),len(t)

def paired_ci(g_gbm,g_ds,reps=2000,seed=7):
    # day-block bootstrap on (DeepSets - GBM) top-5% mean, pooled 2024+2025
    days=g_gbm.session_date.unique(); rng=np.random.default_rng(seed); d=[]
    bg={x:gg for x,gg in g_gbm.groupby("session_date")}; bd={x:gg for x,gg in g_ds.groupby("session_date")}
    for _ in range(reps):
        ds=rng.choice(days,len(days),replace=True)
        sg=pd.concat([bg[x] for x in ds]); sd=pd.concat([bd[x] for x in ds])
        tg=sg[sg.score>=sg.score.quantile(1-TOPF)].eod.mean(); td=sd[sd.score>=sd.score.quantile(1-TOPF)].eod.mean()
        d.append(td-tg)
    return float(np.percentile(d,2.5)),float(np.percentile(d,97.5))

def main():
    dt,X,mask=build()
    print("\n=== LEAVE-ONE-REGIME-OUT: DeepSets(raw tape + ctx) vs GBM(ctx) | top-5% mean EOD ===")
    print(f"  {'regime':>7}{'base%':>7}{'GBM':>9}{'GBM_AUC':>9}{'DeepSets':>10}{'DS_AUC':>8}{'DS-GBM':>9}")
    gbm_held={}; ds_held={}
    for ty in [2024,2025,2026]:
        trm=dt.year!=ty; tem=dt.year==ty
        # GBM on ctx
        qt=QuantileTransformer(output_distribution="normal",n_quantiles=min(1000,trm.sum()),random_state=0).fit(dt.loc[trm,CTX])
        g=dt[tem].copy()
        g["score"]=GBM(max_iter=300,learning_rate=0.05,max_depth=4,l2_regularization=1.0,class_weight="balanced").fit(
            dt.loc[trm,CTX],dt.loc[trm,"rocket"]).predict_proba(dt.loc[tem,CTX])[:,1]
        # DeepSets on raw tape + ctx (ctx rank-Gaussian on train)
        ctr=qt.transform(dt.loc[trm,CTX]).astype(np.float32); cte=qt.transform(dt.loc[tem,CTX]).astype(np.float32)
        d=dt[tem].copy()
        d["score"]=train_score(X[trm.values],mask[trm.values],ctr,dt.loc[trm,"rocket"].to_numpy(),
                               X[tem.values],mask[tem.values],cte)
        ga=roc_auc_score(g.rocket,g.score); da=roc_auc_score(d.rocket,d.score)
        gm=topslice(g)[0]; dm=topslice(d)[0]
        gbm_held[ty]=g; ds_held[ty]=d
        print(f"  {ty:>7}{g.rocket.mean()*100:>6.1f}%{gm*100:>+8.1f}%{ga:>9.3f}{dm*100:>+9.1f}%{da:>8.3f}{(dm-gm)*100:>+8.1f}%")
    # pooled 2024+2025 paired test (the rescue tier)
    g2=pd.concat([gbm_held[2024],gbm_held[2025]]); d2=pd.concat([ds_held[2024],ds_held[2025]])
    gm2=g2[g2.score>=g2.score.quantile(1-TOPF)].eod.mean(); dm2=d2[d2.score>=d2.score.quantile(1-TOPF)].eod.mean()
    lo,hi=paired_ci(g2,d2)
    print(f"\n  POOLED 2024+2025: GBM {gm2*100:+.1f}% | DeepSets {dm2*100:+.1f}% | paired DS-GBM {(dm2-gm2)*100:+.1f}% 95%CI [{lo*100:+.1f},{hi*100:+.1f}]")
    print("\n=== DECISION ===")
    print("  KILL (raw tape adds nothing) if DeepSets does NOT beat GBM on the pooled 2024+2025 block (paired lower-CI <= 0).")
    print("  ESCALATE to the full Set Transformer ONLY if DeepSets shows a CI-separated positive 2024+2025 lift.")

if __name__=="__main__": main()
