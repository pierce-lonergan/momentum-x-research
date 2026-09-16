"""doc 257 - RocketScalp-RL "the Monster" v2, Phase 1. NO capital, NO live change.

Per the architecture deep-research synthesis, the two highest-lift / near-zero-overfit changes first:
  (A) FORCE-TO-TRADE: replace lower-tail CVaR (averse -> abstains) with a configurable SPECTRAL distortion;
      --risk-mode wang/cvar_high/dualpow are tail-SEEKING (chase the rocket right-tail). DECISION-TIME ONLY;
      the IQN learning target stays undistorted (the "Pitfall of Optimism" correctness detail). 0 new params.
  (B) FiLM candidacy conditioning: g(c)->(gamma,beta) modulates the encoder, c = the doc-255/253 candidacy
      vector + the doc-242/248 aggregated tick microstructure (tick_ofi, large_print_ratio, ...). gamma=1,beta=0
      init (identity), so it's a monotone-safe A/B vs the bare encoder. This is "use ALL the data we collected."
  + state-augmentation (running realized P&L) for episode-coherent risk; + a VECTORIZED rollout (batched across
    episodes) so training is fast (the v1 per-step Python rollout was the bottleneck).
Run --risk-mode cvar_low for the conservative (v1-style) arm and --risk-mode wang for the aggressive monster ->
the clean A/B, both walk-forward (train 2024+2025 -> held-out 2026), net-of-cost vs the baselines.
"""
from __future__ import annotations
import os, sys, pickle, argparse, numpy as np, torch, torch.nn as nn, torch.nn.functional as Fn
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb
from rocketscalp_rl_doc256 import run_policy, vwap_meanrev, FEATS
DEV='cuda' if torch.cuda.is_available() else 'cpu'
W=16; NF=len(FEATS); ACTS=torch.tensor([-1.,0.,1.]); NA=3; Dh=96; NTAU=16; K=4; GAMMA=0.999; NSTEP=5; COST=30/1e4
ENR="data/research/_rocketscalp_episodes_enriched.pkl"; CORPUS="data/research/rocket_tick_features_xregime.parquet"
CANDF=["gap","micro_float","log_shares","recent_reverse_split","rsplit_days","prior_runner","log_mcap","days_listed","sic_health","sic_tech"]  # drop foreign(0%)+coiled(leaky)
TICKF=["tick_ofi","tick_ofi_late","large_print_ratio","mean_trade_size","trade_intensity","tick_vwap_dist","odd_lot_ratio","pm_dollarvol"]
DC=len(CANDF)+len(TICKF)

def load():
    eps=pickle.load(open(ENR,'rb'))
    tk=duckdb.connect().execute(f"SELECT ticker,session_date,{','.join(TICKF)} FROM read_parquet('{CORPUS}')").df()
    tk['pm_dollarvol']=np.log1p(tk['pm_dollarvol']); tkidx={(r.ticker,r.session_date):r for r in tk.itertuples()}
    out=[]
    for e in eps:
        c=e['cand']; t=tkidx.get((e['ticker'],e['date']))
        cv=[c.get(f,0.0) for f in CANDF]+[float(getattr(t,f,0.0)) if t is not None else 0.0 for f in TICKF]
        e['c']=np.nan_to_num(np.array(cv,np.float32),nan=0.0,posinf=0.0,neginf=0.0)
        out.append(e)
    # normalize c on TRAIN regime only
    tr=np.stack([e['c'] for e in out if e['year'] in (2024,2025)])
    mu=tr.mean(0); sd=tr.std(0)+1e-6
    for e in out: e['c']=(e['c']-mu)/sd
    return out

class Encoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.tcn=nn.Sequential(nn.Conv1d(NF,48,3,padding=1,dilation=1),nn.GELU(),
                               nn.Conv1d(48,48,3,padding=2,dilation=2),nn.GELU(),
                               nn.Conv1d(48,48,3,padding=4,dilation=4),nn.GELU())
        self.film=nn.Sequential(nn.Linear(DC,64),nn.GELU(),nn.Linear(64,2*48))   # FiLM: c -> (gamma,beta)
        self.head=nn.Sequential(nn.Linear(48+DC+3,Dh),nn.GELU(),nn.Linear(Dh,Dh),nn.GELU())
        self.film[-1].weight.data.zero_(); self.film[-1].bias.data.zero_()       # init gamma=1,beta=0
    def forward(self, win, scal, c):            # win[B,W,NF], scal[B,3]=(pos,tod,run_pnl), c[B,DC]
        x=self.tcn(win.transpose(1,2)).mean(-1)                     # [B,48]
        gb=self.film(c); g,b=gb[:,:48],gb[:,48:]; x=(1+g)*x+b       # FiLM modulation
        return self.head(torch.cat([x,c,scal],-1))

class IQN(nn.Module):
    def __init__(self):
        super().__init__(); self.enc=Encoder(); self.phi=nn.Linear(64,Dh)
        self.out=nn.Sequential(nn.Linear(Dh,Dh),nn.GELU(),nn.Linear(Dh,NA))
        self.register_buffer('ar',torch.arange(1,65,dtype=torch.float32)*np.pi)
    def forward(self, win, scal, c, taus):
        h=self.enc(win,scal,c); cos=torch.cos(taus.unsqueeze(-1)*self.ar)
        return self.out(h.unsqueeze(1)*Fn.gelu(self.phi(cos)))      # [B,NTAU,NA]

def spectral(z, mode, eta):                     # z[B,NTAU,NA] -> [B,NA]
    zs,_=torch.sort(z,1); N=zs.shape[1]; u=(torch.arange(N,device=z.device)+0.5)/N; a=0.25
    if mode=='cvar_low':  w=(u<=a).float()
    elif mode=='cvar_high': w=(u>=1-a).float()
    elif mode=='wang':
        nd=torch.distributions.Normal(0.,1.); W_=nd.cdf(nd.icdf(u)+eta); w=torch.diff(W_,prepend=W_.new_zeros(1)); w=w.clamp(min=0)
    elif mode=='dualpow': nu=1+abs(eta); w=nu*(u**(nu-1))
    else: w=torch.ones(N,device=z.device)
    w=(w/w.sum()).view(1,N,1); return (zs*w).sum(1)

class Agent:
    def __init__(self,mode,eta,lr=3e-4):
        self.mode=mode; self.eta=eta
        self.nets=[IQN().to(DEV) for _ in range(K)]; self.tgts=[IQN().to(DEV) for _ in range(K)]; self.pri=[IQN().to(DEV) for _ in range(K)]
        for p in self.pri:
            for q in p.parameters(): q.requires_grad_(False)
        for n,t in zip(self.nets,self.tgts): t.load_state_dict(n.state_dict())
        self.opt=torch.optim.Adam([p for n in self.nets for p in n.parameters()],lr=lr)
    def Q(self,nets,win,scal,c,taus,ki): return nets[ki](win,scal,c,taus)+0.3*self.pri[ki](win,scal,c,taus)
    @torch.no_grad()
    def act(self,win,scal,c,explore=0.0):
        taus=torch.rand(win.shape[0],NTAU,device=DEV)
        z=torch.stack([self.Q(self.nets,win,scal,c,taus,ki) for ki in range(K)],0).mean(0)
        a=spectral(z,self.mode,self.eta).argmax(-1)
        if explore>0:
            r=torch.randint(0,NA,(win.shape[0],),device=DEV); a=torch.where(torch.rand(win.shape[0],device=DEV)<explore,r,a)
        return a
    def update(self,b):
        win,scal,c,act,ret,nwin,nscal,nc,done=b; B=win.shape[0]
        taus=torch.rand(B,NTAU,device=DEV); pt=torch.rand(B,NTAU,device=DEV)
        with torch.no_grad():
            nz=torch.stack([self.Q(self.tgts,nwin,nscal,nc,pt,ki) for ki in range(K)],0).mean(0)
            na=spectral(nz,self.mode,self.eta).argmax(-1)
            tgt=ret.unsqueeze(1)+(GAMMA**NSTEP)*(1-done).unsqueeze(1)*nz.gather(2,na.view(B,1,1).expand(B,NTAU,1)).squeeze(-1)
        loss=0
        for ki in range(K):
            z=self.Q(self.nets,win,scal,c,taus,ki).gather(2,act.view(B,1,1).expand(B,NTAU,1)).squeeze(-1)
            d=tgt.unsqueeze(1)-z.unsqueeze(2); hub=torch.where(d.abs()<1,0.5*d**2,d.abs()-0.5)
            loss=loss+(torch.abs(taus.unsqueeze(1)-(d.detach()<0).float())*hub).mean()
        self.opt.zero_grad(); loss.backward()
        torch.nn.utils.clip_grad_norm_([p for n in self.nets for p in n.parameters()],10.); self.opt.step()
        return float(loss)
    def soft(self,tau=0.01):
        for n,t in zip(self.nets,self.tgts):
            for p,q in zip(n.parameters(),t.parameters()): q.data.mul_(1-tau).add_(tau*p.data)

def pad_batch(eps_chunk):
    L=max(len(e['F']) for e in eps_chunk); B=len(eps_chunk)
    F=np.zeros((B,L,NF),np.float32); fwd=np.zeros((B,L),np.float32); msk=np.zeros((B,L),np.float32); C=np.zeros((B,DC),np.float32)
    for i,e in enumerate(eps_chunk):
        n=len(e['F']); F[i,:n]=e['F']; fwd[i,:n]=e['fwd']; msk[i,:n]=1; C[i]=e['c']
    return (torch.tensor(F,device=DEV),torch.tensor(fwd,device=DEV),torch.tensor(msk,device=DEV),torch.tensor(C,device=DEV),L)

def windows(F,t):                               # F[B,L,NF], step t -> [B,W,NF] (left-pad)
    lo=max(0,t-W+1); w=F[:,lo:t+1]
    if w.shape[1]<W: w=torch.cat([torch.zeros(w.shape[0],W-w.shape[1],NF,device=w.device),w],1)
    return w

@torch.no_grad()
def vroll(agent, eps_chunk, explore, collect):
    F,fwd,msk,C,L=pad_batch(eps_chunk); B=len(eps_chunk)
    pos=torch.zeros(B,device=DEV); pnl=torch.zeros(B,device=DEV); tr=[]
    for t in range(L-1):
        scal=torch.stack([pos,torch.full((B,),t/max(L-1,1),device=DEV),pnl.clamp(-1,3)],1)
        a=agent.act(windows(F,t),scal,C,explore); tgt=ACTS.to(DEV)[a]
        r=(tgt*fwd[:,t]-(tgt-pos).abs()*COST)*msk[:,t]
        pnl=pnl+r
        if collect: tr.append((t,pos.clone(),a.clone(),r.clone(),scal.clone()))
        pos=tgt
    pnl=pnl-pos.abs()*COST
    return pnl, msk, F, C, fwd, tr

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--risk-mode',default='wang'); ap.add_argument('--eta',type=float,default=0.75)
    ap.add_argument('--epochs',type=int,default=4); ap.add_argument('--smoke',action='store_true'); a=ap.parse_args()
    eps=load(); tr=[e for e in eps if e['year'] in (2024,2025)]; te=[e for e in eps if e['year']==2026]
    if a.smoke: tr=tr[:120]; te=te[:80]; a.epochs=1
    print(f"MODE={a.risk_mode} eta={a.eta} | train {len(tr)} test {len(te)} | DC={DC} | device {DEV}")
    agent=Agent(a.risk_mode,a.eta); buf=[]; rng=np.random.default_rng(0); CH=256
    def turnover(eps_chunk):                     # eval: greedy, return (net per-year, mean turns)
        import collections; by=collections.defaultdict(list); tn=[]
        for s in range(0,len(eps_chunk),CH):
            chunk=eps_chunk[s:s+CH]; pnl,msk,F,C,fwd,_=vroll(agent,chunk,0.0,False)
            for i,e in enumerate(chunk): by[e['year']].append(float(pnl[i]))
        return {y:float(np.mean(v)) for y,v in by.items()}
    for ep_i in range(a.epochs):
        rng.shuffle(tr); losses=[]; explore=max(0.05,0.4*(1-ep_i/max(a.epochs-1,1)))
        for s in range(0,min(len(tr),1500),CH):
            chunk=tr[s:s+CH]; pnl,msk,F,C,fwd,trans=vroll(agent,chunk,explore,True)
            # build transitions with n-step returns from the realized reward sequence
            rmat=torch.stack([x[3] for x in trans],1)                  # [B, L-1] realized rewards
            for ti,(t,pos,a_,r,scal) in enumerate(trans):
                tn=min(ti+NSTEP,len(trans)-1)
                disc=torch.tensor([GAMMA**k for k in range(min(NSTEP,len(trans)-ti))],device=DEV)
                nret=(rmat[:,ti:ti+len(disc)]*disc).sum(1)
                nt=trans[tn]; nscal=nt[4]
                done=torch.full((len(chunk),),1.0 if tn>=len(trans)-1 else 0.0,device=DEV)
                buf.append((windows(F,t).cpu(),scal.cpu(),C.cpu(),a_.cpu(),nret.cpu(),windows(F,trans[tn][0]).cpu(),nscal.cpu(),C.cpu(),done.cpu()))
            if len(buf)>4000: buf=buf[-4000:]
            if len(buf)>50:
                for _ in range(8):
                    idx=rng.integers(0,len(buf),1); b=buf[idx[0]]
                    # sub-sample rows within the stored batch to size 256
                    nb=b[0].shape[0]; ri=torch.randint(0,nb,(min(256,nb),))
                    batch=tuple(x[ri].to(DEV) for x in b)
                    losses.append(agent.update(batch)); agent.soft()
        tre=turnover(tr[:300]); tee=turnover(te)
        print(f"  [epoch {ep_i}] loss {np.mean(losses[-50:]) if losses else 0:.3f} | train { {k:round(v*100,2) for k,v in tre.items()} } | TEST-2026 { {k:round(v*100,2) for k,v in tee.items()} }")
    # final A/B vs baselines on held-out 2026
    fin=turnover(te); base_long=np.mean([run_policy(e,lambda t,F,p:1.0)[0] for e in te]); base_mr=np.mean([run_policy(e,vwap_meanrev)[0] for e in te])
    # turnover of the trained agent
    pnls=[]; turns=[]
    for s in range(0,len(te),CH):
        chunk=te[s:s+CH]; pnl,msk,F,C,fwd,trans=vroll(agent,chunk,0.0,True)
        acts=torch.stack([x[2].float() for x in trans],1); poss=torch.stack([x[1] for x in trans],1)
        turns.append(float((torch.diff(torch.cat([torch.zeros(len(chunk),1,device=DEV),acts],1),dim=1).abs().sum(1)).mean()))
    print(f"\n=== HELD-OUT 2026 net-of-cost: MONSTER[{a.risk_mode}] {fin.get(2026,0)*100:+.2f}% | long {base_long*100:+.2f}% | vwap-mr {base_mr*100:+.2f}% | flat 0.00% | avg turns {np.mean(turns):.1f} ===")
    print("  A/B: run --risk-mode cvar_low (conservative, should abstain ~flat) vs --risk-mode wang (tail-seeking,")
    print("  should TRADE more = higher turns). Honest: tail-seeking buys the right-tail optionality; whether it")
    print("  beats flat NET of the 30bps spread on held-out 2026 is the test. ~flat = the respectable null.")

if __name__=="__main__": main()
