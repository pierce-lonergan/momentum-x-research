"""doc 260 - HOSTILE BATTERY on the Stage-B LLM-text signal that PASSED the initial median gate. Same discipline
that killed 6 false-positives this session: try as hard as possible to KILL it. Reuses cached events+text+scores
(no re-fetch/re-LLM). Answers: (1) is the L-S median CI-separated from 0 (small n~55)? (2) does it survive
winsorize @ f10 AND f20? (3) THE capturability gate (doc-259 #4): does the LONG-ONLY top tranche clear 0 after
cost, or is the whole edge the (borrow-constrained) SHORT leg? (4) universe-relative framing. (5) quintile vs
tercile robustness. Then the decisive call on whether to run an OUT-OF-SAMPLE replication (fresh sample, seed 11).
"""
from __future__ import annotations
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from stage_b_text_probe_doc260 import build_events, sample_events, fetch_8k_text, DMAP, SCORED_PQ
COST=0.004

def reconstruct():
    s=sample_events(build_events())
    s['text']=[fetch_8k_text(r.cik, r.ann_date) for r in s.itertuples()]
    s=s[s.text.notna()].copy()
    sc=pd.read_parquet(SCORED_PQ)
    s=s.merge(sc,left_index=True,right_on='idx',how='inner')
    s['llm_score']=s.llm_drift.map(DMAP).astype(float)*s.llm_conv
    return s

def boot_med(x, reps=4000, seed=7):
    x=np.asarray(x); x=x[~np.isnan(x)]
    if len(x)<8: return (np.nan,np.nan,np.nan)
    rng=np.random.default_rng(seed); meds=[np.median(x[rng.integers(0,len(x),len(x))]) for _ in range(reps)]
    return np.median(x)*100, np.percentile(meds,2.5)*100, np.percentile(meds,97.5)*100

def boot_ls(top,bot,col,reps=4000,seed=7):
    a=top[col].dropna().to_numpy(); b=bot[col].dropna().to_numpy()
    rng=np.random.default_rng(seed); d=[np.median(a[rng.integers(0,len(a),len(a))])-np.median(b[rng.integers(0,len(b),len(b))]) for _ in range(reps)]
    return (np.median(a)-np.median(b))*100, np.percentile(d,2.5)*100, np.percentile(d,97.5)*100

def wins(x,b=0.20): return np.clip(np.asarray(x),-b,b)

def main():
    s=reconstruct()
    print(f"reconstructed {len(s)} scored events | 2024={int((s.yr==2024).sum())} 2025={int((s.yr==2025).sum())}")
    print(f"  UNIVERSE baseline median: f10 {np.median(s.f10)*100:+.2f}%  f20 {np.median(s.f20)*100:+.2f}%  (these post-earnings names mildly fade)")

    for qname,(lo,hi) in [('TERCILE',(1/3,2/3)),('QUINTILE',(0.2,0.8))]:
        print(f"\n===== {qname} split by llm_score =====")
        for y in [2024,2025,'ALL']:
            d=s if y=='ALL' else s[s.yr==y]
            ql,qh=d.llm_score.quantile([lo,hi]).values
            top=d[d.llm_score>=qh]; bot=d[d.llm_score<=ql]
            ls10,l10,h10=boot_ls(top,bot,'f10'); ls20,l20,h20=boot_ls(top,bot,'f20')
            # winsorized L-S
            w10=(np.median(wins(top.f10))-np.median(wins(bot.f10)))*100
            w20=(np.median(wins(top.f20))-np.median(wins(bot.f20)))*100
            print(f"  {str(y):>4} (nT{len(top)}/nB{len(bot)}): L-S f10 {ls10:+.2f}% CI[{l10:+.1f},{h10:+.1f}] (wins {w10:+.2f}) | L-S f20 {ls20:+.2f}% CI[{l20:+.1f},{h20:+.1f}] (wins {w20:+.2f})")

    print("\n===== CAPTURABILITY GATE (doc-259 #4): is the LONG leg real, or is it all the (borrow-constrained) SHORT? =====")
    ql,qh=s.llm_score.quantile([1/3,2/3]).values
    for y in [2024,2025,'ALL']:
        d=s if y=='ALL' else s[s.yr==y]
        top=d[d.llm_score>=qh]; bot=d[d.llm_score<=ql]
        tm,tl,th=boot_med(top.f10); bm,bl,bh=boot_med(bot.f10)
        # long-only after cost; relative to universe
        uni=np.median(d.f10)*100
        print(f"  {str(y):>4}: LONG top med f10 {tm:+.2f}% CI[{tl:+.1f},{th:+.1f}] (after -{COST*100:.1f}% cost: {tm-COST*100:+.2f}%, vs universe {uni:+.2f}%)  ||  SHORT bot med f10 {bm:+.2f}% CI[{bl:+.1f},{bh:+.1f}]")
    longclears=boot_med(s[s.llm_score>=qh].f10)[1]>COST*100   # lower CI of long > cost
    print(f"  --> LONG-ONLY clears cost (lower-CI > {COST*100:.1f}%)? {'YES' if longclears else 'NO - edge is short-side / uncapturable long'}")

    print("\n===== universe-relative directional skill =====")
    uni10=np.median(s.f10)
    up=s[s.llm_score>0.3]; dn=s[s.llm_score<-0.3]
    print(f"  up-pred(n{len(up)}) f10 med {np.median(up.f10)*100:+.2f}%  vs  down-pred(n{len(dn)}) {np.median(dn.f10)*100:+.2f}%  vs  universe {uni10*100:+.2f}%")
    print(f"  --> longs beat universe by {(np.median(up.f10)-uni10)*100:+.2f}pp; shorts beat universe by {(uni10-np.median(dn.f10))*100:+.2f}pp")

    print("\n===== VERDICT =====")
    ls_all,lo_all,_=boot_ls(s[s.llm_score>=qh],s[s.llm_score<=ql],'f10')
    sep = lo_all>0
    print(f"  L-S f10 ALL = {ls_all:+.2f}% CI lower {lo_all:+.2f}% -> {'CI-separated from 0' if sep else 'NOT CI-separated'}")
    print(f"  Long-only capturable: {'YES' if longclears else 'NO (short-constrained)'}")
    print("  NEXT: if L-S CI-separated AND median-robust -> run OUT-OF-SAMPLE replication (fresh sample seed=11) = the decisive test vs lucky-sample.")

if __name__=='__main__': main()
