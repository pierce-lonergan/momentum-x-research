"""doc 260 - STAGE B de-risk probe. The cheap, decisive test of the doc-259 thesis: does an LLM reading the SOFT
TEXT of the earnings disclosure predict forward drift BETTER than the numbers (which Stage A showed are flat/
reversing)? Point-in-time: fetch the earnings 8-K from EDGAR (filed AT the announcement), give the LLM ONLY that
text, have it predict the next-10-day drift, and measure whether its score separates forward returns - cross-
regime, median, AND vs the numeric baselines (reaction, fundamental surprise) on the SAME events.

PRE-REGISTERED: soft text adds edge IFF the LLM-score top tranche beats its bottom tranche (MEDIAN f10 & f20),
cross-regime (2024 & 2025), AND beats the numeric-sort L-S on the same sample, AND survives winsorize. If the
LLM just re-derives the price reaction (high corr with react, no extra separation) -> text adds nothing -> the
earnings-drift form of the info game is closed. NO live change. NO capital. Resumable (caches events/text/scores).
"""
from __future__ import annotations
import os, json, glob, time, re, asyncio
import numpy as np, pandas as pd, duckdb
import urllib.request

FIN='data/polygon_cache/vX_reference_financials/*.json'
DA='data/polygon_warehouse/day_aggs/**/*.parquet'
RES='data/research'; EVENTS_PQ=f'{RES}/stage_b_events.parquet'; EDGAR_DIR=f'{RES}/edgar_8k_cache'
SEED=int(os.environ.get('SBSEED','7'))
SCORED_PQ=f'{RES}/stage_b_scored.parquet' if SEED==7 else f'{RES}/stage_b_scored_s{SEED}.parquet'
UA='MomentumX-Research research@momentumx.example'   # EDGAR requires a UA w/ contact
N_PER_REGIME=int(os.environ.get('SBN','200'))
# DeepSeek-V3.1 + Qwen3-Coder-480B are no longer Together-serverless (settings.py defaults are stale / LiteLLM-resolved).
# Qwen3-235B-A22B-Instruct-2507 is the strongest serverless instruct model available now (clean JSON, strong judgment).
MODEL=os.environ.get('SBMODEL','Qwen/Qwen3-235B-A22B-Instruct-2507-tput'); BASE='https://api.together.xyz/v1'

# ---------- events (reuse Stage-A v2 detection, carry CIK) ----------
def build_events():
    if os.path.exists(EVENTS_PQ):
        return pd.read_parquet(EVENTS_PQ)
    print("building earnings-event panel (one-time, cached)...")
    rows=[]
    for fp in glob.glob(FIN):
        try: d=json.load(open(fp, encoding='utf-8'))
        except Exception: continue
        for r in (d.get('results') or []):
            tks=r.get('tickers') or []; ed=r.get('end_date'); cik=r.get('cik')
            if not tks or not ed or not cik: continue
            inc=(r.get('financials') or {}).get('income_statement') or {}
            g=lambda k:(inc.get(k,{}).get('value') if isinstance(inc.get(k),dict) else inc.get(k))
            rows.append(dict(ticker=tks[0], cik=str(cik), end_date=ed, revenues=g('revenues'),
                net_income=g('net_income_loss'), op_income=g('operating_income_loss'), eps=g('diluted_earnings_per_share')))
    df=pd.DataFrame(rows); df['end_dt']=pd.to_datetime(df.end_date, errors='coerce')
    for c in ['revenues','net_income','op_income','eps']: df[c]=pd.to_numeric(df[c],errors='coerce')
    df=df[df.end_dt.notna()].drop_duplicates(['ticker','end_date']).sort_values(['ticker','end_dt']).reset_index(drop=True)
    gg=df.groupby('ticker')
    df['rev_yoy']=gg.revenues.pct_change(4,fill_method=None); p4=gg.net_income.shift(4)
    df['ni_yoy']=(df.net_income-p4)/p4.abs(); df['eps_yoy']=df.eps-gg.eps.shift(4)
    df['opm_yoy']=(df.op_income/df.revenues)-(gg.op_income.shift(4)/gg.revenues.shift(4))
    df['rev_qoq']=gg.revenues.pct_change(1,fill_method=None)
    # earnings-day detection + forward returns (same as v2)
    con=duckdb.connect(); tks="','".join(sorted(set(df.ticker.str.replace("'",""))))
    px=con.execute(f"""SELECT ticker, ts_et::DATE d, close, volume*close dv FROM read_parquet('{DA}',hive_partitioning=1)
        WHERE ticker IN ('{tks}') AND ts_et>='2023-06-01' AND close>0 ORDER BY ticker, ts_et""").df()
    px['d']=pd.to_datetime(px.d); px['adv20']=px.groupby('ticker').dv.transform(lambda s:s.rolling(20,min_periods=10).mean())
    px['basevol']=px.groupby('ticker').dv.transform(lambda s:s.shift(1).rolling(20,min_periods=10).median())
    by={tk:g.reset_index(drop=True) for tk,g in px.groupby('ticker')}
    out=[]
    for r in df.itertuples():
        g=by.get(r.ticker)
        if g is None: out.append((np.nan,)*7); continue
        w=g[(g.d>r.end_dt+pd.Timedelta(days=10))&(g.d<=r.end_dt+pd.Timedelta(days=80))]
        if len(w)<5: out.append((np.nan,)*7); continue
        i=int(w.dv.idxmax())
        if i<1 or i+20>=len(g): out.append((np.nan,)*7); continue
        a=g.iloc[i]
        if not (a.basevol and a.dv>=2.5*a.basevol): out.append((np.nan,)*7); continue
        clo=a.close; out.append((a.d, clo, a.adv20, clo/g.iloc[i-1].close-1, g.iloc[i+5].close/clo-1, g.iloc[i+10].close/clo-1, g.iloc[i+20].close/clo-1))
    df[['ann_date','anchor_close','adv20','react','f5','f10','f20']]=pd.DataFrame(out,index=df.index)
    df['ann_date']=pd.to_datetime(df.ann_date,errors='coerce'); df['yr']=df.ann_date.dt.year
    df['cohort']=df.ann_date.dt.to_period('M').astype(str)
    for f in ['rev_yoy','ni_yoy','eps_yoy','opm_yoy','rev_qoq']:
        df[f]=df[f].replace([np.inf,-np.inf],np.nan)
        df[f+'_z']=df.groupby('cohort')[f].transform(lambda s:(s-s.median())/(s.std(ddof=0)+1e-9)).clip(-3,3)
    df['surprise']=df[[f+'_z' for f in ['rev_yoy','ni_yoy','eps_yoy','opm_yoy','rev_qoq']]].mean(axis=1)
    os.makedirs(RES,exist_ok=True); df.to_parquet(EVENTS_PQ)
    return df

def sample_events(df):
    u=df[(df.anchor_close.between(2,500))&(df.adv20>=1e6)&df.f20.notna()&df.react.notna()&df.surprise.notna()&df.yr.isin([2024,2025])].copy()
    u['_key']=u.ticker+'|'+u.end_date.astype(str)
    if SEED!=7:  # OUT-OF-SAMPLE: exclude the seed-7 selection so the replication is genuinely non-overlapping
        prior=u.groupby('yr',group_keys=False).apply(lambda g:g.sample(min(N_PER_REGIME,len(g)),random_state=7))
        u=u[~u._key.isin(set(prior._key))]
    s=u.groupby('yr',group_keys=False).apply(lambda g:g.sample(min(N_PER_REGIME,len(g)),random_state=SEED))
    return s.reset_index(drop=True)

# ---------- EDGAR point-in-time 8-K fetch ----------
def _get(url, is_json=False, tries=3):
    for k in range(tries):
        try:
            req=urllib.request.Request(url, headers={'User-Agent':UA,'Accept-Encoding':'gzip, deflate'})
            with urllib.request.urlopen(req, timeout=25) as r:
                raw=r.read()
                if r.info().get('Content-Encoding')=='gzip':
                    import gzip; raw=gzip.decompress(raw)
                txt=raw.decode('utf-8','replace')
                return json.loads(txt) if is_json else txt
        except Exception:
            time.sleep(0.5*(k+1))
    return None

_SUBM={}
def submissions(cik):
    c=str(int(float(cik))).zfill(10)
    if c in _SUBM: return _SUBM[c]
    d=_get(f"https://data.sec.gov/submissions/CIK{c}.json", is_json=True); time.sleep(0.12)
    _SUBM[c]=d; return d

def strip_html(html):
    html=re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>',' ',html)
    t=re.sub(r'(?s)<[^>]+>',' ',html)
    t=re.sub(r'&#160;|&nbsp;|&#xa0;',' ',t); t=re.sub(r'&amp;','&',t); t=re.sub(r'&#39;|&rsquo;|&lsquo;',"'",t)
    return re.sub(r'\s+',' ',t).strip()

def fetch_8k_text(cik, ann_date):
    """Find the 8-K filed nearest the announcement day; return its press-release/primary text (<=9000 chars)."""
    cint=int(float(cik)); cache=f"{EDGAR_DIR}/{cint}_{ann_date.date()}.txt"
    if os.path.exists(cache):
        t=open(cache,encoding='utf-8').read(); return t if t!='__NONE__' else None
    os.makedirs(EDGAR_DIR,exist_ok=True)
    sub=submissions(cik); res=None
    try:
        rec=sub['filings']['recent']; forms=rec['form']; fdates=rec['filingDate']; accs=rec['accessionNumber']; prim=rec['primaryDocument']
        best=None; bestgap=99
        for j,form in enumerate(forms):
            if form!='8-K': continue
            fd=pd.Timestamp(fdates[j]); gap=abs((fd-ann_date).days)
            if gap<=5 and gap<bestgap: bestgap=gap; best=(accs[j].replace('-',''), prim[j])
        if best:
            acc,pdoc=best
            idx=_get(f"https://www.sec.gov/Archives/edgar/data/{cint}/{acc}/index.json", is_json=True); time.sleep(0.12)
            doc=pdoc
            if idx:
                items=idx.get('directory',{}).get('item',[])
                ex99=[it['name'] for it in items if re.search(r'ex.?99',it['name'],re.I) and it['name'].lower().endswith(('.htm','.html','.txt'))]
                if ex99: doc=ex99[0]
            html=_get(f"https://www.sec.gov/Archives/edgar/data/{cint}/{acc}/{doc}"); time.sleep(0.12)
            if html: res=strip_html(html)[:9000]
    except Exception: res=None
    open(cache,'w',encoding='utf-8').write(res if res else '__NONE__')
    return res

# ---------- LLM scoring ----------
PROMPT=("You are a buy-side equity analyst. Below is the point-in-time earnings disclosure (SEC 8-K / press release) "
 "for {tk}, released {dt}. Based ONLY on this text - the reported results, guidance, tone, segment detail and any "
 "red/green flags an attentive analyst would weigh - predict this stock's price DRIFT over the NEXT 10 TRADING DAYS "
 "relative to the broad market. Think like you are forecasting post-earnings continuation vs fade. Respond with ONLY "
 "a compact JSON object, no prose: {{\"drift\":\"STRONG_UP|UP|FLAT|DOWN|STRONG_DOWN\",\"conviction\":0.0-1.0,"
 "\"reason\":\"<=12 words\"}}.\n\n--- DISCLOSURE ---\n{txt}")
DMAP={'STRONG_UP':2,'UP':1,'FLAT':0,'DOWN':-1,'STRONG_DOWN':-2}

async def score_all(events, key):
    from openai import AsyncOpenAI
    client=AsyncOpenAI(base_url=BASE, api_key=key, max_retries=0)
    sem=asyncio.Semaphore(6)
    async def one(ev):
        txt=ev.get('text')
        if not txt or len(txt)<300: return None
        async with sem:
            for _ in range(2):
                try:
                    r=await asyncio.wait_for(client.chat.completions.create(model=MODEL, temperature=0.1, max_tokens=120,
                        messages=[{'role':'user','content':PROMPT.format(tk=ev['ticker'],dt=str(ev['ann_date'])[:10],txt=txt)}]), timeout=40)
                    c=r.choices[0].message.content
                    m=re.search(r'\{.*\}', c, re.S)
                    if not m: continue
                    o=json.loads(m.group(0)); d=str(o.get('drift','FLAT')).upper()
                    return dict(idx=ev['idx'], llm_drift=d, llm_conv=float(o.get('conviction',0.5)), llm_reason=str(o.get('reason',''))[:80])
                except Exception: await asyncio.sleep(0.6)
        return None
    return [x for x in await asyncio.gather(*[one(e) for e in events]) if x]

# ---------- analysis ----------
def wins(x,b=0.20): return np.clip(x,-b,b)
def tranche(df,col):
    out={}
    for y in [2024,2025,'ALL']:
        s=df if y=='ALL' else df[df.yr==y]
        s=s[s[col].notna()&s.f10.notna()&s.f20.notna()]
        if len(s)<30: out[y]=None; continue
        q=s[col].quantile([1/3,2/3]).values; top=s[s[col]>=q[1]]; bot=s[s[col]<=q[0]]
        m=lambda a,c:np.median(a[c])*100
        out[y]=dict(t10=m(top,'f10'),b10=m(bot,'f10'),ls10=m(top,'f10')-m(bot,'f10'),
                    t20=m(top,'f20'),b20=m(bot,'f20'),ls20=m(top,'f20')-m(bot,'f20'),
                    win=(top.f10>0).mean()*100,n=min(len(top),len(bot)),top=top,bot=bot)
    return out
def show(name,T):
    print(f"\n  {name:>26} | "+"  ".join(f"{y}: LS10 {T[y]['ls10']:+.2f}% LS20 {T[y]['ls20']:+.2f}% (n{T[y]['n']})" if T.get(y) else f"{y}: thin" for y in [2024,2025,'ALL']))

def main():
    key=None
    for f in [os.path.expanduser('~/momentum-x-secrets.env'),'.env']:
        if os.path.exists(f):
            for l in open(f,encoding='utf-8',errors='replace'):
                if l.startswith('TOGETHER_AI_API_KEY='): key=l.split('=',1)[1].strip().strip('"').strip("'"); break
        if key: break
    if not key: print("FAIL: no TOGETHER_AI_API_KEY"); return
    s=sample_events(build_events())
    print(f"sampled {len(s)} events (2024={int((s.yr==2024).sum())}, 2025={int((s.yr==2025).sum())})")
    print("fetching point-in-time 8-K text from EDGAR (cached)...")
    texts=[]; nt=0
    for r in s.itertuples():
        t=fetch_8k_text(r.cik, r.ann_date); texts.append(t)
        if t: nt+=1
    s['text']=texts; s=s[s.text.notna()].copy()
    print(f"  8-K text found for {nt}/{len(texts)} events; usable {len(s)}")
    if os.path.exists(SCORED_PQ):
        sc=pd.read_parquet(SCORED_PQ)
    else:
        evs=[dict(idx=int(i),ticker=r.ticker,ann_date=r.ann_date,text=r.text) for i,r in s.iterrows()]
        print(f"scoring {len(evs)} disclosures with {MODEL} (async, conc 6)...")
        recs=asyncio.run(score_all(evs,key)); sc=pd.DataFrame(recs)
        if len(sc): sc.to_parquet(SCORED_PQ)
    if not len(sc): print("FAIL: 0 events scored (check model availability / API)"); return
    print(f"  LLM scored {len(sc)} events")
    s=s.merge(sc,left_index=True,right_on='idx',how='inner')
    s['llm_score']=s.llm_drift.map(DMAP).astype(float)*s.llm_conv
    print(f"  merged {len(s)} | drift dist: "+", ".join(f"{k}={int((s.llm_drift==k).sum())}" for k in ['STRONG_UP','UP','FLAT','DOWN','STRONG_DOWN']))
    print(f"  corr(llm_score, react)={s.llm_score.corr(s.react):+.2f}  corr(llm_score, surprise)={s.llm_score.corr(s.surprise):+.2f}  (high react-corr => LLM just re-derives the price move)")

    Tllm=tranche(s,'llm_score'); Tr=tranche(s,'react'); Tf=tranche(s,'surprise')
    print("\n=== L-S MEDIAN drift by sort key (top tercile minus bottom tercile) ===")
    show('LLM text score',Tllm); show('announcement reaction',Tr); show('fundamental surprise',Tf)

    print("\n  --- PRE-REGISTERED GATE (LLM beats its own bottom + beats the numeric baselines, cross-regime, winsorized) ---")
    g_self=all(Tllm.get(y) and Tllm[y]['ls10']>0 and Tllm[y]['ls20']>0 for y in [2024,2025])
    g_vs=all(Tllm.get(y) and Tr.get(y) and Tllm[y]['ls10']>Tr[y]['ls10'] for y in [2024,2025])
    wok=True
    for y in [2024,2025]:
        if not Tllm.get(y): wok=False; continue
        if np.median(wins(Tllm[y]['top'].f10))-np.median(wins(Tllm[y]['bot'].f10))<=0: wok=False
    # directional accuracy: predicted-up vs predicted-down forward sign
    up=s[s.llm_score>0.3]; dn=s[s.llm_score<-0.3]
    da=f"up-pred f10 med {np.median(up.f10)*100:+.2f}% (n{len(up)}) vs down-pred {np.median(dn.f10)*100:+.2f}% (n{len(dn)})" if len(up)>10 and len(dn)>10 else "thin"
    print(f"  gate self (LS10&LS20>0 cross-regime): {'PASS' if g_self else 'FAIL'} | gate beats-reaction: {'PASS' if g_vs else 'FAIL'} | winsor: {'PASS' if wok else 'FAIL'}")
    print(f"  directional: {da}")
    print(f"\n=== STAGE B VERDICT === {'SOFT TEXT ADDS EDGE -> scale up + Stage C shadow' if (g_self and g_vs and wok) else 'SOFT TEXT ADDS NOTHING -> earnings-drift info-game CLOSED (LLM cannot beat the flat/reversing numeric base)'}")

if __name__=='__main__': main()
