"""doc 259 STAGE A v2 - STRONGEST cheap form. The v1 anchor (10-Q filing date) LAGS the 8-K earnings announcement
by days-to-weeks, so the PEAD drift is often already spent by then -> not a clean test. v2 DETECTS the true
earnings-announcement day as the max-dollar-volume volume-SPIKE day in [quarter_end+10, +80], anchors there, and
measures (a) classic price-PEAD (sort by announcement-day reaction) and (b) fundamental-surprise drift from the
earnings day. Same pre-registered gates, but the VERDICT now correctly tests the LONG-SHORT (top>bottom
cross-regime @10d AND @20d, median, winsorized) - not just top>0 (which is only beta). NO LLM, NO new data.
"""
from __future__ import annotations
import json, glob, numpy as np, pandas as pd, duckdb

FIN='data/polygon_cache/vX_reference_financials/*.json'
DA='data/polygon_warehouse/day_aggs/**/*.parquet'
COST=0.004

def load_events():
    rows=[]
    for fp in glob.glob(FIN):
        try: d=json.load(open(fp, encoding='utf-8'))
        except Exception: continue
        for r in (d.get('results') or []):
            tks=r.get('tickers') or []; ed=r.get('end_date')
            if not tks or not ed: continue
            inc=(r.get('financials') or {}).get('income_statement') or {}
            g=lambda k:(inc.get(k,{}).get('value') if isinstance(inc.get(k),dict) else inc.get(k))
            rows.append(dict(ticker=tks[0], end_date=ed, revenues=g('revenues'), net_income=g('net_income_loss'),
                op_income=g('operating_income_loss'), eps=g('diluted_earnings_per_share')))
    df=pd.DataFrame(rows); df['end_dt']=pd.to_datetime(df.end_date, errors='coerce')
    for c in ['revenues','net_income','op_income','eps']: df[c]=pd.to_numeric(df[c],errors='coerce')
    df=df[df.end_dt.notna()].drop_duplicates(['ticker','end_date']).sort_values(['ticker','end_dt']).reset_index(drop=True)
    g=df.groupby('ticker')
    df['rev_yoy']=g.revenues.pct_change(4, fill_method=None)
    p4=g.net_income.shift(4); df['ni_yoy']=(df.net_income-p4)/p4.abs()
    df['eps_yoy']=df.eps-g.eps.shift(4)
    df['opm_yoy']=(df.op_income/df.revenues)-(g.op_income.shift(4)/g.revenues.shift(4))
    df['rev_qoq']=g.revenues.pct_change(1, fill_method=None)
    return df

def detect_and_attach(df):
    con=duckdb.connect()
    tks="','".join(sorted(set(df.ticker.str.replace("'",""))))
    px=con.execute(f"""SELECT ticker, ts_et::DATE d, close, volume*close dv
        FROM read_parquet('{DA}',hive_partitioning=1)
        WHERE ticker IN ('{tks}') AND ts_et>='2023-06-01' AND close>0 ORDER BY ticker, ts_et""").df()
    px['d']=pd.to_datetime(px.d)
    px['adv20']=px.groupby('ticker').dv.transform(lambda s:s.rolling(20,min_periods=10).mean())
    px['basevol']=px.groupby('ticker').dv.transform(lambda s:s.shift(1).rolling(20,min_periods=10).median())
    by={tk:g.reset_index(drop=True) for tk,g in px.groupby('ticker')}
    out=[]
    for r in df.itertuples():
        g=by.get(r.ticker)
        if g is None: out.append((np.nan,)*7); continue
        lo=r.end_dt+pd.Timedelta(days=10); hi=r.end_dt+pd.Timedelta(days=80)
        w=g[(g.d>lo)&(g.d<=hi)]
        if len(w)<5: out.append((np.nan,)*7); continue
        ei=w.dv.idxmax()                      # earnings day = max dollar-volume day in the window
        i=int(ei)
        if i<1 or i+20>=len(g): out.append((np.nan,)*7); continue
        ann=g.iloc[i]
        if not (ann.basevol and ann.dv>=2.5*ann.basevol): out.append((np.nan,)*7); continue  # require a real vol spike
        clo=ann.close; react=clo/g.iloc[i-1].close-1
        fwd=lambda k:g.iloc[i+k].close/clo-1
        out.append((ann.d, clo, ann.adv20, react, fwd(5), fwd(10), fwd(20)))
    df[['ann_date','anchor_close','adv20','react','f5','f10','f20']]=pd.DataFrame(out, index=df.index)
    df['ann_date']=pd.to_datetime(df.ann_date, errors='coerce')
    return df

def composite(df):
    df['cohort']=df.ann_date.dt.to_period('M').astype(str)
    feats=['rev_yoy','ni_yoy','eps_yoy','opm_yoy','rev_qoq']
    for f in feats:
        df[f]=df[f].replace([np.inf,-np.inf],np.nan)
        df[f+'_z']=df.groupby('cohort')[f].transform(lambda s:(s-s.median())/(s.std(ddof=0)+1e-9)).clip(-3,3)
    df['surprise']=df[[f+'_z' for f in feats]].mean(axis=1)
    return df

def wins(x,b=0.20): return np.clip(x,-b,b)

def report(df, col, label):
    print(f"\n========== sort by {label} ({col}) | drift from EARNINGS DAY ==========")
    print(f"  {'regime':>7}{'topMd f10':>11}{'botMd f10':>11}{'L-S f10':>10}{'topMd f20':>11}{'botMd f20':>11}{'L-S f20':>10}{'topWin':>8}{'n/grp':>7}")
    R={}
    for y in [2024,2025,2026,'ALL']:
        s=df if y=='ALL' else df[df.yr==y]
        s=s[s[col].notna()&s.f10.notna()&s.f20.notna()]
        if len(s)<60: print(f"  {str(y):>7}{'(thin)':>11}"); continue
        q=s[col].quantile([1/3,2/3]).values; top=s[s[col]>=q[1]]; bot=s[s[col]<=q[0]]
        m=lambda a,c:np.median(a[c])*100
        ls10=m(top,'f10')-m(bot,'f10'); ls20=m(top,'f20')-m(bot,'f20')
        print(f"  {str(y):>7}{m(top,'f10'):>+10.2f}%{m(bot,'f10'):>+10.2f}%{ls10:>+9.2f}%{m(top,'f20'):>+10.2f}%{m(bot,'f20'):>+10.2f}%{ls20:>+9.2f}%{(top.f10>0).mean()*100:>7.0f}%{min(len(top),len(bot)):>7}")
        R[y]=dict(top=top,bot=bot,ls10=ls10,ls20=ls20)
    return R

def verdict(R, label):
    print(f"\n  --- PRE-REGISTERED GATE on {label} (L-S top>bottom, MEDIAN, winsorized, cross-regime) ---")
    g1_10=all(R.get(y,{}).get('ls10',-1)>0 for y in [2024,2025])      # firmly 2024&2025
    g1_20=all(R.get(y,{}).get('ls20',-1)>0 for y in [2024,2025])
    # winsorized L-S
    wins_ok=True
    for y in [2024,2025]:
        if y not in R: wins_ok=False; continue
        t=wins(R[y]['top'].f10.dropna().to_numpy()); b=wins(R[y]['bot'].f10.dropna().to_numpy())
        if np.median(t)-np.median(b)<=0: wins_ok=False
    print(f"  gate1 L-S med f10>0 in 2024&2025: {'PASS' if g1_10 else 'FAIL'} | L-S med f20>0 in 2024&2025: {'PASS' if g1_20 else 'FAIL'} | winsorized L-S f10>0: {'PASS' if wins_ok else 'FAIL'}")
    return g1_10 and g1_20 and wins_ok

def main():
    df=composite(detect_and_attach(load_events()))
    df['yr']=df.ann_date.dt.year
    u=df[(df.anchor_close.between(2,500))&(df.adv20>=1e6)&df.f20.notna()].copy()
    print(f"events w/ DETECTED earnings-day vol-spike + tradable: {len(u):,} | tickers {u.ticker.nunique():,}")
    print(f"  per-regime n: "+" ".join(f"{y}={int((u.yr==y).sum())}" for y in [2024,2025,2026]))
    # sanity: ann_date should be days-weeks after quarter end
    lag=(u.ann_date-u.end_dt).dt.days; print(f"  earnings-day lag after quarter-end: median {lag.median():.0f}d (typical 30-50d => detection sane)")

    Rr=report(u,'react','announcement REACTION (classic price PEAD)')
    okr=verdict(Rr,'price-PEAD (reaction sort)')
    uf=u[u.surprise.notna()].copy()
    Rf=report(uf,'surprise','FUNDAMENTAL surprise composite')
    okf=verdict(Rf,'fundamental-surprise sort')

    print("\n  --- CAPACITY CHECK: L-S med f10 (reaction sort) THIN (ADV 1-10M) vs LARGE (>10M) ---")
    for tag,mask in [('THIN 1-10M',u.adv20<1e7),('LARGE >10M',u.adv20>=1e7)]:
        s=u[mask]; cells=[]
        for y in [2024,2025,2026]:
            ss=s[(s.yr==y)&s.react.notna()]
            if len(ss)<60: cells.append(f"{y}=thin"); continue
            q=ss.react.quantile([1/3,2/3]).values
            cells.append(f"{y}={np.median(ss[ss.react>=q[1]].f10)*100-np.median(ss[ss.react<=q[0]].f10)*100:+.2f}%")
        print(f"    {tag:>12} (n={len(s):>5}): "+"  ".join(cells))

    print(f"\n=== STAGE A v2 VERDICT ===  price-PEAD: {'BASE ANOMALY EXISTS' if okr else 'FLAT/regime-flips (no base)'} | fundamental: {'BASE EXISTS' if okf else 'FLAT/regime-flips (no base)'}")
    print("  (If BOTH flat: the numeric base is arbitraged in our universe too; the LLM-text layer would have to create 100% of the edge - a high bar, decide explicitly.)")

if __name__=='__main__': main()
