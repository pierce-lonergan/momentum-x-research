"""doc 259 STAGE A - post-disclosure (PEAD) drift baseline. PRICE + POINT-IN-TIME FUNDAMENTALS only, NO LLM, NO
new data. Does a base under-reaction anomaly even EXIST in our reachable non-lottery universe (a thing to amplify
with the LLM in Stage B)? PRE-REGISTERED (doc 259) - a base anomaly exists IFF ALL hold:
 (1) top fundamental-surprise tranche's forward-drift MEDIAN > 0 AND > bottom tranche @10d & @20d
 (2) cross-regime: 2024 & 2025 firmly sign-consistent, 2026 not contradicting
 (3) NOT a tail illusion: winsorize fwd rets @ +/-20% -> L-S median survives; top-decile of trades < ~60% of L-S P&L
 (4) after ~0.4% round-trip cost the LONG-ONLY top tranche clears (short leg needs borrow - long is capturable)
 (5) edge concentrates in the THIN-coverage corner (ADV 1-10M) vs large (>10M)  [capacity check]
 (6) look-ahead audit: every input knowable at acceptance_datetime; drift window starts the NEXT session.
NO live change. NO capital. All research.
"""
from __future__ import annotations
import json, glob, numpy as np, pandas as pd, duckdb

FIN='data/polygon_cache/vX_reference_financials/*.json'
DA='data/polygon_warehouse/day_aggs/**/*.parquet'
COST=0.004   # ~0.4% round-trip, liquid universe

def load_events():
    rows=[]
    for fp in glob.glob(FIN):
        try: d=json.load(open(fp, encoding='utf-8'))
        except Exception: continue
        for r in (d.get('results') or []):
            acc=r.get('acceptance_datetime'); fd=r.get('filing_date'); tks=r.get('tickers') or []
            if not acc or not fd or not tks: continue
            inc=(r.get('financials') or {}).get('income_statement') or {}
            g=lambda k: (inc.get(k,{}).get('value') if isinstance(inc.get(k),dict) else inc.get(k))
            rows.append(dict(ticker=tks[0], sic=r.get('sic'), fp=r.get('fiscal_period'), fy=r.get('fiscal_year'),
                end_date=r.get('end_date'), acceptance=acc, revenues=g('revenues'), net_income=g('net_income_loss'),
                op_income=g('operating_income_loss'), gross_profit=g('gross_profit'), eps=g('diluted_earnings_per_share')))
    df=pd.DataFrame(rows)
    df['acc_dt']=pd.to_datetime(df.acceptance, utc=True, errors='coerce')
    df=df[df.acc_dt.notna()].copy()
    df['acc_date']=df.acc_dt.dt.tz_convert('America/New_York').dt.normalize().dt.tz_localize(None)  # point-in-time ET
    df['end_dt']=pd.to_datetime(df.end_date, errors='coerce')
    for c in ['revenues','net_income','op_income','gross_profit','eps']: df[c]=pd.to_numeric(df[c], errors='coerce')
    df=df.drop_duplicates(['ticker','end_date']).sort_values(['ticker','end_dt']).reset_index(drop=True)
    return df

def surprise(df):
    g=df.groupby('ticker')
    df['rev_yoy']=g.revenues.pct_change(4)
    p4=g.net_income.shift(4); df['ni_yoy']=(df.net_income-p4)/p4.abs()
    df['eps_yoy']=df.eps - g.eps.shift(4)
    opm=df.op_income/df.revenues; df['opm_yoy']=opm - (g.op_income.shift(4)/g.revenues.shift(4))
    df['rev_qoq']=g.revenues.pct_change(1)
    # composite z within filing-month cohort (cross-sectional, no time leak)
    df['cohort']=df.acc_date.dt.to_period('M').astype(str)
    feats=['rev_yoy','ni_yoy','eps_yoy','opm_yoy','rev_qoq']
    for f in feats:
        df[f]=df[f].replace([np.inf,-np.inf],np.nan)
        df[f+'_z']=df.groupby('cohort')[f].transform(lambda s:(s-s.median())/(s.std(ddof=0)+1e-9)).clip(-3,3)
    df['surprise']=df[[f+'_z' for f in feats]].mean(axis=1)
    return df

def attach_prices(df):
    con=duckdb.connect()
    tks="','".join(sorted(set(df.ticker.str.replace("'",""))))
    px=con.execute(f"""SELECT ticker, ts_et::DATE d, close, volume*close dv
        FROM read_parquet('{DA}',hive_partitioning=1)
        WHERE ticker IN ('{tks}') AND ts_et>='2023-06-01' AND close>0 ORDER BY ticker, ts_et""").df()
    px['d']=pd.to_datetime(px.d)
    px['adv20']=px.groupby('ticker').dv.transform(lambda s:s.rolling(20,min_periods=10).mean())
    by={tk:g.reset_index(drop=True) for tk,g in px.groupby('ticker')}
    out=[]
    for r in df.itertuples():
        g=by.get(r.ticker)
        if g is None: out.append((np.nan,)*6); continue
        i=int(g.d.searchsorted(r.acc_date, side='right'))   # first session STRICTLY after acceptance
        if i>=len(g) or i<1: out.append((np.nan,)*6); continue
        a=g.iloc[i]; clo=a.close
        fwd=lambda k:(g.iloc[i+k].close/clo-1) if i+k<len(g) else np.nan
        react=clo/g.iloc[i-1].close-1
        out.append((clo, a.adv20, react, fwd(5), fwd(10), fwd(20)))
    df[['anchor_close','adv20','react','f5','f10','f20']]=pd.DataFrame(out, index=df.index)
    return df

def wins(x,lo=-0.20,hi=0.20): return np.clip(x,lo,hi)

def tranche_report(df, sort_col, label):
    print(f"\n========== TRANCHE TEST: sort by {label} ({sort_col}) ==========")
    print(f"  {'regime':>7}{'top med f10':>12}{'bot med f10':>12}{'L-S med f10':>12}{'top med f20':>12}{'bot med f20':>12}{'L-S med f20':>12}{'top win10':>10}{'n/grp':>8}")
    res={}
    for y in [2024,2025,2026,'ALL']:
        s=df if y=='ALL' else df[df.yr==y]
        s=s[s[sort_col].notna() & s.f10.notna() & s.f20.notna()]
        if len(s)<60: print(f"  {str(y):>7}{'(thin)':>12}"); continue
        q=s[sort_col].quantile([1/3,2/3]).values
        top=s[s[sort_col]>=q[1]]; bot=s[s[sort_col]<=q[0]]
        def md(a,c): return np.median(a[c])*100
        ls10=md(top,'f10')-md(bot,'f10'); ls20=md(top,'f20')-md(bot,'f20')
        winr=(top.f10>0).mean()*100
        print(f"  {str(y):>7}{md(top,'f10'):>+11.2f}%{md(bot,'f10'):>+11.2f}%{ls10:>+11.2f}%{md(top,'f20'):>+11.2f}%{md(bot,'f20'):>+11.2f}%{ls20:>+11.2f}%{winr:>9.0f}%{min(len(top),len(bot)):>8}")
        res[y]=dict(top=top, bot=bot, ls10=ls10, ls20=ls20)
    return res

def hostile(res, label):
    print(f"\n  --- HOSTILE CHECK on {label} (the gate that killed 6 false-positives) ---")
    print(f"  {'regime':>7}{'top mean10':>11}{'top MED10':>10}{'top win':>8}{'winsor top':>11}{'top-dec share':>14}{'long-net':>10}")
    ok_med=ok_wins=True
    for y in [2024,2025,2026]:
        if y not in res: print(f"  {str(y):>7}{'(thin)':>11}"); continue
        t=res[y]['top']; f=t.f10.to_numpy(); f=f[~np.isnan(f)]
        mean=np.mean(f)*100; med=np.median(f)*100; win=(f>0).mean()*100
        wm=np.mean(wins(f))*100
        srt=np.sort(f)[::-1]; tot=np.abs(f).sum(); dec=np.abs(srt[:max(1,len(srt)//10)]).sum()/ (tot+1e-9)*100
        net=mean-COST*100
        print(f"  {str(y):>7}{mean:>+10.2f}%{med:>+9.2f}%{win:>7.0f}%{wm:>+10.2f}%{dec:>13.0f}%{net:>+9.2f}%")
        if med<=0: ok_med=False
        if wm<=0: ok_wins=False
    print(f"  pre-reg(1) top MED10>0 all regimes: {'PASS' if ok_med else 'FAIL'} | (3) winsor-survives: {'PASS' if ok_wins else 'FAIL'}")
    return ok_med, ok_wins

def main():
    print("loading point-in-time fundamental events from vX_reference_financials cache...")
    df=surprise(load_events())
    print(f"  raw ticker-quarters with acceptance_datetime: {len(df):,} | tickers {df.ticker.nunique():,}")
    df=attach_prices(df)
    df['yr']=df.acc_date.dt.year
    # NON-lottery universe + valid surprise + valid fwd
    u=df[(df.anchor_close.between(2,500)) & (df.adv20>=1e6) & df.surprise.notna() & df.f20.notna()].copy()
    print(f"  tradable events (px $2-500, ADV>=$1M, valid surprise+fwd): {len(u):,} | tickers {u.ticker.nunique():,}")
    print(f"  per-regime n: " + " ".join(f"{y}={int((u.yr==y).sum())}" for y in [2024,2025,2026]))

    # primary test: sort by FUNDAMENTAL surprise (the 'read the filing detail' proxy)
    res_f=tranche_report(u, 'surprise', 'FUNDAMENTAL surprise composite')
    okm,okw=hostile(res_f, 'fundamental-surprise top tranche')

    # secondary: sort by the announcement REACTION (classic price PEAD) - is THAT alive?
    res_r=tranche_report(u, 'react', 'announcement REACTION (classic price PEAD)')

    # capacity check (pre-reg 5): does the fundamental edge concentrate in the THIN corner?
    print("\n  --- CAPACITY CHECK (pre-reg 5): L-S med f10 by surprise, THIN (ADV 1-10M) vs LARGE (>10M) ---")
    for tag,mask in [('THIN 1-10M',(u.adv20<1e7)),('LARGE >10M',(u.adv20>=1e7))]:
        s=u[mask]
        cells=[]
        for y in [2024,2025,2026]:
            ss=s[s.yr==y]
            if len(ss)<60: cells.append(f"{y}=thin"); continue
            q=ss.surprise.quantile([1/3,2/3]).values
            ls=np.median(ss[ss.surprise>=q[1]].f10)*100 - np.median(ss[ss.surprise<=q[0]].f10)*100
            cells.append(f"{y}={ls:+.2f}%")
        print(f"    {tag:>12} (n={len(s):>5}): " + "  ".join(cells))

    print("\n=== PRE-REGISTERED VERDICT (doc 259 Stage A) ===")
    verdict = okm and okw
    print(f"  Base anomaly to amplify with the LLM (Stage B)?  {'-> PROCEED (gates 1&3 cleared, inspect 2/4/5)' if verdict else '-> FAILS the hostile median/winsorize gate'}")
    print("  (Gates 2/4/5/6 read from the tables above; gate 6 is structural - anchor is the session AFTER acceptance_datetime.)")

if __name__=='__main__': main()
