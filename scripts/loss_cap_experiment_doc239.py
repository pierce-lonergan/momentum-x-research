"""doc 239 (c2) — loss-tail cap experiment. Does a hard stop improve EXPECTANCY (not just variance)?

PRE-REGISTRATION (inline, before results):
- Hypothesis: some hard-stop level S, applied to the gapper universe (doc-235 corpus, entry = decision
  pt #3 ~9:50, hold to EOD), IMPROVES the realized MEAN (not merely reduces variance) by cutting
  catastrophic NON-RECOVERING losers (the LIDR/ASNS/CANF class).
- Test: sweep S in {none,3,5,8,10,15,20}%. Stopped return = -S if the post-entry intraday close-path
  breaches entry*(1-S), else hold-to-horizon. Per year (2024/2025/2026): mean/median/WL/P5. Bootstrap
  paired-day CI on mean-diff (stopped - no-stop).
- SURVIVES: some S improves the MEAN with CI-excl-0-positive mean-diff in ALL 3 years (Bonferroni across S).
- FAILS: no S improves the mean cross-regime (stop is variance-only or hurts the mean).
- Also report a slippage variant (stop fills at -S - 0.5%) since stops slip on thin names.
NO live change.
"""
from __future__ import annotations
import numpy as np, pandas as pd, duckdb
CORPUS="data/research/exit_labels_cross_regime.parquet"
ENTRY=3
def boot_diff_ci(a,b,n=10000,seed=11):  # mean(b)-mean(a), paired by index (same ticker-days)
    d=np.asarray(b)-np.asarray(a); rng=np.random.default_rng(seed)
    idx=rng.integers(0,len(d),size=(n,len(d))); s=d[idx].mean(axis=1)
    return np.percentile(s,2.5),np.percentile(s,97.5)

def reduce_paths():
    df=duckdb.connect().execute(f"SELECT ticker,session_date,year,minute_idx,ret_session FROM read_parquet('{CORPUS}')").df()
    rows=[]
    for (tk,d),g in df.groupby(["ticker","session_date"]):
        g=g.sort_values("minute_idx"); rel=1+g.ret_session.to_numpy()
        if len(rel)<=ENTRY+6 or rel[ENTRY]<=0: continue
        rows.append((int(g.year.iloc[0]), rel/rel[ENTRY]))  # normalized to entry=1.0
    return rows

def stopped_ret(reln, S, horizon_end, slip=0.0):
    """reln normalized to entry=1.0; stop at 1-S; hold to horizon_end. Post-entry path = reln[1:horizon_end]."""
    path=reln[1:horizon_end]
    if len(path)==0: return 0.0
    if path.min() <= (1-S):
        return -(S+slip)                      # stopped (fill at level minus slippage)
    return reln[horizon_end-1]-1.0            # hold to horizon

def run(rows, horizon_end_fn, label, slip=0.0):
    print(f"\n### {label} ###")
    Ss=[None,0.03,0.05,0.08,0.10,0.15,0.20]
    for S in Ss:
        line=f"  stop {'none' if S is None else f'{S*100:.0f}%':>5}: "
        ok_years=[]
        for y in [2024,2025,2026]:
            yr=[r for r in rows if r[0]==y]
            ns=np.array([r[1][horizon_end_fn(len(r[1]))-1]-1.0 for r in yr])  # no-stop: hold-to-horizon
            if S is None:
                st=ns
            else:
                st=np.array([stopped_ret(r[1],S,horizon_end_fn(len(r[1])),slip) for r in yr])
            mean=st.mean(); med=np.median(st);
            w=st[st>0]; l=st[st<0]; wl=(w.mean()/abs(l.mean())) if len(w) and len(l) else float('nan')
            if S is None:
                line+=f"{y}:m{mean*100:+.2f}%/WL{wl:.2f} "
            else:
                lo,hi=boot_diff_ci(ns,st); sig=(lo>0)
                ok_years.append(sig)
                line+=f"{y}:m{mean*100:+.2f}%/WL{wl:.2f}/dCI[{lo*100:+.1f},{hi*100:+.1f}]{'*' if sig else ''} "
        if S is not None and all(ok_years): line+=" <== improves mean ALL 3yrs"
        print(line)

def main():
    rows=reduce_paths()
    print(f"ticker-days: {len(rows)} | by year: {pd.Series([r[0] for r in rows]).value_counts().to_dict()}")
    run(rows, lambda n: n, "EOD horizon (hold to last decision pt) — PRIMARY", slip=0.0)
    run(rows, lambda n: min(ENTRY+7,n), "30-min horizon (trained) — secondary", slip=0.0)
    run(rows, lambda n: n, "EOD horizon WITH 0.5% stop slippage (thin-name realism)", slip=0.005)
    print("\nDECISION: SURVIVES iff some S improves the MEAN (paired mean-diff CI excl 0 positive) in ALL 3 years.")

if __name__=="__main__": main()
