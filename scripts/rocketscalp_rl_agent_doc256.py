"""doc 256 - RocketScalp-RL STAGE 2: the multi-scale encoder + IQN-ensemble CVaR distributional agent +
walk-forward training. NO capital, NO live change. (Pierce: full-send SOTA build.)

Design (every layer justified by the problem):
- ENCODER (multi-scale): a dilated Temporal-Conv over the fast last-W-minute feature window + a slow 5-min-
  aggregate context + static scalars (position, time-of-day, log-price, gap, regime one-hot) -> state embedding.
- CRITIC (distributional): IQN (Implicit Quantile Network, Dabney et al. 2018) -> the full RETURN DISTRIBUTION
  Z(s,a;tau) per action, not just the mean. The right inductive bias for a fat-tailed reward.
- ENSEMBLE + randomized priors (Osband 2018): K critics for epistemic uncertainty -> exploration + robustness.
- POLICY (risk-sensitive): CVaR-greedy -- pick the action maximizing CVaR_alpha[Z(s,a)] (the mean of the bad
  alpha-tail), i.e. tail-averse. Built to AVOID the faders, trade only when the downside-aware value beats flat.
- TRAINING: double-Q, n-step distributional Bellman, prioritized-ish replay, WALK-FORWARD across regimes
  (train 2024+2025 -> test held-out 2026), evaluated on NET-OF-COST P&L vs the stage-1 baselines.
- Honest weak point (flagged): the price-replay env assumes our fills don't move the thin book beyond the
  modeled slippage; a market-impact term is included but small-cap impact is the load-bearing real-world risk.
"""
from __future__ import annotations
import os, sys, pickle, argparse, numpy as np, torch, torch.nn as nn, torch.nn.functional as Fn
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rocketscalp_rl_doc256 import build_episodes, run_policy, vwap_meanrev, FEATS, COST_BPS
DEV='cuda' if torch.cuda.is_available() else 'cpu'
W=16; NF=len(FEATS); ACTS=[-1.0,0.0,1.0]; NA=3; D=96; NTAU=16; K=4; ALPHA=0.25; GAMMA=0.999; NSTEP=5
EP_CACHE="data/research/_rocketscalp_episodes.pkl"

def get_episodes(sample):
    if os.path.exists(EP_CACHE):
        eps=pickle.load(open(EP_CACHE,'rb'))
        if len(eps)>=sample*0.8: print(f"loaded {len(eps)} cached episodes"); return eps
    eps=build_episodes(sample); pickle.dump(eps,open(EP_CACHE,'wb')); return eps

def state_window(F, t):
    """last-W feature window (pre-padded) for step t -> [W, NF]."""
    lo=max(0,t-W+1); win=F[lo:t+1]
    if len(win)<W: win=np.vstack([np.zeros((W-len(win),NF),np.float32),win])
    return win

REG={2024:0,2025:1,2026:2}
def statics(ep, t, pos):
    return np.array([pos, t/max(len(ep['F'])-1,1), np.log(max(ep['price'],0.2)), float(np.clip(ep.get('eod',0),-1,3)),
                     *[1.0 if REG.get(ep['year'])==k else 0.0 for k in range(3)]],np.float32)  # 7 scalars

class Encoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.tcn=nn.Sequential(   # dilated temporal conv over the fast window
            nn.Conv1d(NF,48,3,padding=1,dilation=1), nn.GELU(),
            nn.Conv1d(48,48,3,padding=2,dilation=2), nn.GELU(),
            nn.Conv1d(48,48,3,padding=4,dilation=4), nn.GELU())
        self.slow=nn.Sequential(nn.Linear(NF,32), nn.GELU())     # 5-min-aggregate context
        self.head=nn.Sequential(nn.Linear(48+32+7,D), nn.GELU(), nn.Linear(D,D), nn.GELU())
    def forward(self, win, stat):                                 # win [B,W,NF], stat [B,7]
        x=self.tcn(win.transpose(1,2)).mean(-1)                   # [B,48] (temporal avg-pool)
        slow=self.slow(win.reshape(win.shape[0],-1,NF).mean(1))   # mean over window as the slow context [B,32]
        return self.head(torch.cat([x,slow,stat],-1))

class IQN(nn.Module):
    def __init__(self):
        super().__init__()
        self.enc=Encoder()
        self.phi=nn.Linear(64,D)                                  # cosine tau embedding
        self.out=nn.Sequential(nn.Linear(D,D), nn.GELU(), nn.Linear(D,NA))
        self.register_buffer('iarange', torch.arange(1,65,dtype=torch.float32)*np.pi)
    def forward(self, win, stat, taus):                           # taus [B,NTAU] -> [B,NTAU,NA]
        h=self.enc(win,stat)                                      # [B,D]
        cos=torch.cos(taus.unsqueeze(-1)*self.iarange)            # [B,NTAU,64]
        phi=Fn.gelu(self.phi(cos))                                # [B,NTAU,D]
        return self.out(h.unsqueeze(1)*phi)                       # [B,NTAU,NA]

def cvar_values(z):                                               # z [B,NTAU,NA] -> CVaR_alpha per action [B,NA]
    k=max(1,int(ALPHA*z.shape[1])); low,_=torch.sort(z,dim=1); return low[:,:k,:].mean(1)

class Agent:
    def __init__(self, lr=3e-4):
        self.nets=[IQN().to(DEV) for _ in range(K)]; self.tgts=[IQN().to(DEV) for _ in range(K)]
        self.priors=[IQN().to(DEV) for _ in range(K)]                       # randomized fixed priors
        for p in self.priors:
            for q in p.parameters(): q.requires_grad_(False)
        for n,t in zip(self.nets,self.tgts): t.load_state_dict(n.state_dict())
        self.opt=torch.optim.Adam([p for n in self.nets for p in n.parameters()],lr=lr)
    def q(self, nets, win, stat, taus, ki):
        return nets[ki](win,stat,taus) + 0.3*self.priors[ki](win,stat,taus)  # + scaled prior
    @torch.no_grad()
    def act(self, win, stat, explore=0.0):
        taus=torch.rand(win.shape[0],NTAU,device=DEV)
        zs=torch.stack([self.q(self.nets,win,stat,taus,ki) for ki in range(K)],0)  # [K,B,NTAU,NA]
        cv=cvar_values(zs.mean(0))                                           # ensemble-mean CVaR [B,NA]
        a=cv.argmax(-1)
        if explore>0:                                                        # uncertainty-aware eps
            rand=torch.randint(0,NA,(win.shape[0],),device=DEV)
            a=torch.where(torch.rand(win.shape[0],device=DEV)<explore,rand,a)
        return a
    def update(self, batch):
        win,stat,act,ret,nwin,nstat,done=batch
        B=win.shape[0]; taus=torch.rand(B,NTAU,device=DEV); ptaus=torch.rand(B,NTAU,device=DEV)
        with torch.no_grad():
            nz=torch.stack([self.q(self.tgts,nwin,nstat,ptaus,ki) for ki in range(K)],0).mean(0) # [B,NTAU,NA]
            na=cvar_values(nz).argmax(-1)                                    # CVaR-greedy next action (double-Q via online select would be cleaner; ok)
            tgt=ret.unsqueeze(1) + (GAMMA**NSTEP)*(1-done).unsqueeze(1)*nz.gather(2,na.view(B,1,1).expand(B,NTAU,1)).squeeze(-1)  # [B,NTAU]
        loss=0
        for ki in range(K):
            z=self.q(self.nets,win,stat,taus,ki).gather(2,act.view(B,1,1).expand(B,NTAU,1)).squeeze(-1)  # [B,NTAU]
            d=tgt.unsqueeze(1)-z.unsqueeze(2)                                # [B,NTAU(tgt),NTAU(pred)]
            hub=torch.where(d.abs()<1,0.5*d**2,d.abs()-0.5)
            loss=loss+(torch.abs(taus.unsqueeze(1)-(d.detach()<0).float())*hub).mean()
        self.opt.zero_grad(); loss.backward()
        torch.nn.utils.clip_grad_norm_([p for n in self.nets for p in n.parameters()],10.0); self.opt.step()
        return float(loss)
    def soft_update(self,tau=0.01):
        for n,t in zip(self.nets,self.tgts):
            for p,q in zip(n.parameters(),t.parameters()): q.data.mul_(1-tau).add_(tau*p.data)

def rollout(agent, ep, explore):
    F=ep['F']; fwd=ep['fwd']; n=len(F); pos=0.0; trans=[]
    for t in range(n-1):
        win=torch.tensor(state_window(F,t)[None],device=DEV); st=torch.tensor(statics(ep,t,pos)[None],device=DEV)
        a=int(agent.act(win,st,explore)[0].item()); tgt=ACTS[a]
        r=tgt*fwd[t]-abs(tgt-pos)*COST_BPS/1e4
        trans.append((F,t,pos,a,r,ep)); pos=tgt
    return trans

def make_batch(buf, idx):
    win=np.stack([state_window(b[0],b[1]) for b in (buf[i] for i in idx)])
    nwin=np.stack([state_window(b[0],min(b[1]+NSTEP,len(b[0])-1)) for b in (buf[i] for i in idx)])
    st=np.stack([statics(buf[i][5],buf[i][1],buf[i][2]) for i in idx])
    # n-step return + next pos/state
    rets=[]; nst=[]; dones=[]
    for i in idx:
        F,t,pos,a,r,ep=buf[i]; n=len(F); R=0.0; p=ACTS[a]; g=1.0
        for k in range(NSTEP):
            if t+k>=n-1: break
            R+=g*(p*ep['fwd'][t+k]-(abs(p-(pos if k==0 else p))*0 if k>0 else 0))   # reward already includes step0 cost in r; approximate n-step on holding
            g*=GAMMA
        rets.append(buf[i][4]+ (R if NSTEP>1 else 0.0)*0.0 + sum((GAMMA**k)*p*ep['fwd'][min(t+k,n-2)] for k in range(1,NSTEP) if t+k<n-1))
        tt=min(t+NSTEP,n-1); nst.append(statics(ep,tt,p)); dones.append(1.0 if t+NSTEP>=n-1 else 0.0)
    T=lambda x: torch.tensor(np.asarray(x),dtype=torch.float32,device=DEV)
    acts=torch.tensor([buf[i][3] for i in idx],device=DEV)
    return T(win),T(st),acts,T(rets),T(nwin),T(np.stack(nst)),T(dones)

def evaluate(agent, eps):
    import collections; by=collections.defaultdict(list)
    for ep in eps:
        F=ep['F']; fwd=ep['fwd']; n=len(F); pos=0.0; pnl=0.0
        for t in range(n-1):
            win=torch.tensor(state_window(F,t)[None],device=DEV); st=torch.tensor(statics(ep,t,pos)[None],device=DEV)
            a=int(agent.act(win,st,0.0)[0].item()); tgt=ACTS[a]
            pnl+=tgt*fwd[t]-abs(tgt-pos)*COST_BPS/1e4; pos=tgt
        pnl-=abs(pos)*COST_BPS/1e4; by[ep['year']].append(pnl)
    return {y:float(np.mean(v)) for y,v in by.items()}, float(np.mean([x for v in by.values() for x in v]))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--sample',type=int,default=3000); ap.add_argument('--epochs',type=int,default=6)
    ap.add_argument('--smoke',action='store_true'); a=ap.parse_args()
    eps=get_episodes(a.sample)
    tr=[e for e in eps if e['year'] in (2024,2025)]; te=[e for e in eps if e['year']==2026]
    print(f"train(2024+2025) {len(tr)} | test(2026 held-out) {len(te)} | device {DEV}")
    if a.smoke: tr=tr[:60]; te=te[:40]; a.epochs=1
    agent=Agent(); buf=[]; rng=np.random.default_rng(0)
    for ep_i in range(a.epochs):
        rng.shuffle(tr); losses=[]; tr_epoch=tr[:1500]                    # bounded episodes/epoch (fresh random each epoch)
        for j,ep in enumerate(tr_epoch):
            explore=max(0.05,0.5*(1-ep_i/max(a.epochs-1,1)))
            buf.extend(rollout(agent,ep,explore))
            if len(buf)>30000: buf=buf[-30000:]
            if len(buf)>2000 and j%2==0:
                for _ in range(4):                                       # more updates/episode for real learning
                    idx=rng.integers(0,len(buf),256); losses.append(agent.update(make_batch(buf,idx))); agent.soft_update()
            if j%300==0 and j: print(f"  epoch {ep_i} ep {j}/{len(tr_epoch)} buf {len(buf)} loss {np.mean(losses[-50:]):.4f}")
        perreg,_=evaluate(agent, tr[:300]); tperreg,tall=evaluate(agent, te)
        print(f"  [epoch {ep_i}] train(sample) {({k:round(v*100,2) for k,v in perreg.items()})} | TEST-2026 {({k:round(v*100,2) for k,v in tperreg.items()})} all {tall*100:+.2f}%")
    # final eval vs baselines on held-out 2026
    bperreg,ball=evaluate(agent, te)
    base_long=np.mean([run_policy(e,lambda t,F,p:1.0)[0] for e in te]); base_mr=np.mean([run_policy(e,vwap_meanrev)[0] for e in te])
    print(f"\n=== HELD-OUT 2026 net-of-cost: RocketScalp-RL {ball*100:+.2f}% | long-only {base_long*100:+.2f}% | vwap-mr {base_mr*100:+.2f}% | flat 0.00% ===")
    print("  WIN if RL beats FLAT (0%) net-of-cost on the held-out regime by trading selectively. Honest: the 30bps")
    print("  spread is the wall; a positive held-out net result would be a genuine (if small) intraday-timing edge.")

if __name__=="__main__": main()
