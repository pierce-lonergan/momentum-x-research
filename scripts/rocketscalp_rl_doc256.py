"""doc 256 - RocketScalp-RL: a cost-realistic, risk-sensitive, distributional deep-RL intraday trader on the
candidacy watchlist. NO capital, NO live trading change. (Pierce: full-send the most layered SOTA build.)

STAGE 1 (this file): the foundation that makes the whole thing respectable rather than a flashy toy —
  (1) EPISODE pipeline: candidacy ticker-days' 1-min RTH paths (minute_aggs) -> per-minute ex-ante state features
      (no look-ahead): momentum (1/5/15m), VWAP-distance, range-position, RVOL, realized-vol, dist-from-hi/lo,
      time-of-day. + static context (gap, price, adv, year/regime).
  (2) ENV: a cost-realistic price-replay simulator. Action = target position in {-1,0,+1}. Stepping from t->t+1
      the agent earns position * ret_{t->t+1} and PAYS cost = |Δposition| * (half_spread + slippage + impact).
      The spread is the killer (honest prior); charging it in the reward is what makes any result trustworthy.
  (3) BASELINES: long-only, flat, random, and a simple VWAP mean-reversion scalper -> net-of-cost P&L per regime,
      the bar the RL agent must beat in STAGE 2 (the multi-scale encoder + IQN-ensemble CVaR distributional agent).

Cost model (per unit |Δposition|, one-way): COST_BPS bps. For $0.5-20 small-caps a realistic one-way cross is
~20-50 bps; default 30 bps (a round trip flat->long->flat pays ~60 bps). Tunable; this is the load-bearing knob.
"""
from __future__ import annotations
import argparse, numpy as np, pandas as pd, duckdb
from zoneinfo import ZoneInfo
ET=ZoneInfo("America/New_York")
MN="data/polygon_warehouse/minute_aggs/**/*.parquet"
CORPUS="data/research/rocket_tick_features_xregime.parquet"   # candidacy ticker-days (gap>=8% gappers) + labels
COST_BPS=30.0          # one-way cost per unit position change, in bps of notional
FEATS=["ret1","ret5","ret15","vwap_dist","range_pos","rvol_cum","rvol_5","rlzvol_15","hi_dist","lo_dist","tod"]

def episode_features(g):
    """g: one ticker-day's RTH 1-min bars (sorted). Return per-minute ex-ante feature matrix + the forward 1-min
    return used by the env (ret_{t->t+1}). All features use only info <= t (no look-ahead)."""
    g=g.sort_values("ts_et").reset_index(drop=True); n=len(g)
    if n<30: return None
    o=g.open.to_numpy(float); h=g.high.to_numpy(float); l=g.low.to_numpy(float); c=g.close.to_numpy(float); v=g.volume.to_numpy(float)
    typ=(h+l+c)/3.0; cv=np.cumsum(v); vwap=np.where(cv>0,np.cumsum(typ*v)/np.maximum(cv,1e-9),c)
    r1=np.zeros(n); r1[1:]=c[1:]/np.maximum(c[:-1],1e-9)-1
    def lag_ret(k):
        x=np.zeros(n); x[k:]=c[k:]/np.maximum(c[:-k],1e-9)-1; return x
    rhi=np.maximum.accumulate(h); rlo=np.minimum.accumulate(l)
    cvol_avg=cv/np.maximum(np.arange(1,n+1),1)
    rlz=np.zeros(n)
    for t in range(n): rlz[t]=np.std(r1[max(0,t-15):t+1]) if t>2 else 0.0
    F=np.stack([
        lag_ret(1), lag_ret(5), lag_ret(15),
        c/np.maximum(vwap,1e-9)-1,
        (c-rlo)/np.maximum(rhi-rlo,1e-9),
        cv/np.maximum(cvol_avg*np.arange(1,n+1),1e-9)/np.maximum(np.arange(1,n+1),1),   # cumulative RVOL-ish
        v/np.maximum(pd.Series(v).rolling(5,min_periods=1).mean().to_numpy(),1e-9),     # 5-min RVOL
        rlz,
        c/np.maximum(rhi,1e-9)-1,
        c/np.maximum(rlo,1e-9)-1,
        np.linspace(0,1,n),
    ],axis=1).astype(np.float32)
    fwd=np.zeros(n,np.float32); fwd[:-1]=(c[1:]/np.maximum(c[:-1],1e-9)-1)   # ret_{t->t+1}
    return np.nan_to_num(F,nan=0.0,posinf=0.0,neginf=0.0), fwd, float(c[0])

def build_episodes(sample=3000, seed=1):
    con=duckdb.connect()
    cand=con.execute(f"SELECT ticker, session_date, year, base_close, adv20, eod FROM read_parquet('{CORPUS}')").df()
    cand=cand.sample(min(sample,len(cand)),random_state=seed)
    eps=[]
    for ym,grp in cand.assign(ym=cand.session_date.str.slice(0,7)).groupby("ym"):
        yr=int(ym[:4]); tks=",".join("'"+t.replace("'","")+"'" for t in grp.ticker.unique())
        m0,m1=grp.session_date.min(),grp.session_date.max()
        bars=con.execute(f"""SELECT ticker, ts_et, open, high, low, close, volume FROM read_parquet('{MN}', hive_partitioning=1)
            WHERE year={yr} AND ts_et::DATE BETWEEN DATE '{m0}' AND DATE '{m1}' AND ticker IN ({tks})""").df()
        if bars.empty: continue
        bars["ts_et"]=pd.to_datetime(bars.ts_et,utc=True).dt.tz_convert(ET); bars["d"]=bars.ts_et.dt.strftime("%Y-%m-%d")
        mn=bars.ts_et.dt.hour*60+bars.ts_et.dt.minute; bars=bars[(mn>=570)&(mn<960)]
        keys={(r.ticker,r.session_date):(r.year,r.eod) for r in grp.itertuples()}
        for (tk,d),gb in bars.groupby(["ticker","d"]):
            if (tk,d) not in keys: continue
            r=episode_features(gb)
            if r is None: continue
            F,fwd,p0=r; eps.append(dict(ticker=tk,date=d,year=keys[(tk,d)][0],eod=keys[(tk,d)][1],F=F,fwd=fwd,price=p0))
        print(f"  {ym}: {len(grp)} cand -> {len(eps)} episodes built")
    print(f"\nbuilt {len(eps)} episodes | by year {pd.Series([e['year'] for e in eps]).value_counts().to_dict()}")
    return eps

# ---- cost-realistic env (vectorized over an episode for the baselines) ----
def run_policy(ep, policy_fn, cost_bps=COST_BPS):
    """policy_fn(t, F, pos) -> target position in {-1,0,1}. Returns net-of-cost episode return (sum of step pnl)."""
    F=ep["F"]; fwd=ep["fwd"]; n=len(F); pos=0.0; pnl=0.0; turns=0.0; cost=cost_bps/1e4
    for t in range(n-1):
        tgt=policy_fn(t,F,pos)
        turns+=abs(tgt-pos); pnl-=abs(tgt-pos)*cost      # pay cost on the change
        pnl+=tgt*fwd[t]                                    # earn the next-bar return at the new position
        pos=tgt
    pnl-=abs(0.0-pos)*cost                                 # flatten at the close
    return pnl, turns

def vwap_meanrev(t,F,pos):
    vd=F[t,3]                                              # vwap_dist
    if vd<-0.03: return 1.0                                # >3% below VWAP -> buy the dip
    if vd>+0.03: return 0.0                                # >3% above -> flat (don't fight)
    return pos

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--sample",type=int,default=3000); ap.add_argument("--cost-bps",type=float,default=COST_BPS)
    a=ap.parse_args()
    eps=build_episodes(a.sample)
    import collections
    print(f"\n=== BASELINE net-of-cost episode returns (cost {a.cost_bps:.0f}bps/turn), mean per regime ===")
    print(f"  {'policy':>16}" + "".join(f"{y:>10}" for y in [2024,2025,2026]) + f"{'ALL':>10}{'turns':>8}")
    pols={"long-only":lambda t,F,p:1.0,"flat":lambda t,F,p:0.0,
          "random":lambda t,F,p:float(np.random.default_rng(t).integers(-1,2)),
          "vwap-meanrev":vwap_meanrev}
    for name,fn in pols.items():
        by=collections.defaultdict(list); allr=[]; tot_turns=[]
        for e in eps:
            r,tn=run_policy(e,fn,a.cost_bps); by[e["year"]].append(r); allr.append(r); tot_turns.append(tn)
        cells="".join(f"{np.mean(by[y])*100:>+9.2f}%" for y in [2024,2025,2026])
        print(f"  {name:>16}{cells}{np.mean(allr)*100:>+9.2f}%{np.mean(tot_turns):>8.1f}")
    print("\n  These are the bars STAGE 2 (IQN-ensemble CVaR agent) must beat NET of cost, cross-regime.")
    print("  Honest read: if even the mean-rev scalper is deeply negative after 30bps/turn, the spread is the wall")
    print("  (as predicted) and the RL must find genuine intraday timing structure to overcome it.")

if __name__=="__main__": main()
