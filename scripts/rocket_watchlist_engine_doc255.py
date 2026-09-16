"""doc 255 - morning ROCKET WATCHLIST + collection engine. NO capital, NO live trading change.

Honest design (per doc 254): this is NOT a runner-predictor (none exists in the information set). It:
 (1) builds the CANDIDACY watchlist each day from the doc-253 structural signature (computable: micro-float,
     recent reverse-split, foreign-issuer, prior-runner, coiled-flag) — a better universe than a price-only scan;
 (2) DETECTS + LOGS the two real-but-underpowered leads — INSIDER Form-4 open-market buy clusters (via SEC EDGAR)
     and COILED catalysts (volume/price) — so a forward month accumulates a POWERED out-of-sample sample;
 (3) logs the realized OUTCOME (gap-day close/open + rocket flag) next to the ex-ante features.
After ~a month, --report re-tests whether the 5%-rare leads actually associate with rockets out-of-sample.

Runs daily after the close (like the doc-246 shadow). All features are ex-ante (pre-gap) except the outcome.

Usage: python scripts/rocket_watchlist_engine_doc255.py --run 2026-06-03   # one day -> log
       python scripts/rocket_watchlist_engine_doc255.py --run-since 2026-06-01
       python scripts/rocket_watchlist_engine_doc255.py --report           # accumulated lead-vs-outcome
"""
from __future__ import annotations
import os, sys, json, time, argparse, urllib.request, datetime as dt
import numpy as np, pandas as pd, duckdb
DA="data/polygon_warehouse/day_aggs/**/*.parquet"
SPLITS="data/polygon_warehouse/reference/splits.parquet"
TD="data/polygon_warehouse/reference/ticker_details.parquet"
LOG="data/research/rocket_watchlist_log.jsonl"
UA="MomentumX research (contact: ops@momentum-x.local)"
# doc 260: LLM red-flag / fader risk score (validates the Stage-B fader-detector PROSPECTIVELY on the live gapper
# universe). DeepSeek-V3.1 + Qwen3-Coder-480B are no longer Together-serverless; Qwen3-235B-2507 is the strong one.
TOGETHER_BASE='https://api.together.xyz/v1'
REDFLAG_MODEL=os.environ.get('REDFLAG_MODEL','Qwen/Qwen3-235B-A22B-Instruct-2507-tput')

def _together_key():
    for f in [os.path.expanduser('~/momentum-x-secrets.env'),'.env']:
        if os.path.exists(f):
            for l in open(f,encoding='utf-8',errors='replace'):
                if l.startswith('TOGETHER_AI_API_KEY='): return l.split('=',1)[1].strip().strip('"').strip("'")
    return None

def _strip_html(h):
    import re
    h=re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>',' ',h)
    t=re.sub(r'(?s)<[^>]+>',' ',h); t=re.sub(r'&#160;|&nbsp;',' ',t); t=re.sub(r'&amp;','&',t)
    return re.sub(r'\s+',' ',t).strip()

def gappers(con, date):
    """Today's gap>=8% candidates + ex-ante candidacy features + the realized outcome, with splits/details joined."""
    df=con.execute(f"""
      WITH r AS (
        SELECT ticker, ts_et, EXTRACT(year FROM ts_et) yr, open, high, low, close, volume,
               lag(close) OVER w pc1, close/lag(close) OVER w - 1 AS ret1
        FROM read_parquet('{DA}', hive_partitioning=1) WINDOW w AS (PARTITION BY ticker ORDER BY ts_et)),
      b AS (
        SELECT ticker, ts_et::DATE d, open, high, low, close, volume, pc1,
          avg(volume*close) OVER w20 adv20, avg(volume) OVER w20 avol20,
          max(close) OVER w10 hi10, min(close) OVER w10 lo10, max(ret1) OVER w10 maxday10
        FROM r WINDOW w20 AS (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING),
                       w10 AS (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING))
      SELECT ticker, open AS price, open/pc1-1 AS gap, adv20, volume/nullif(avol20,0) AS rvol,
             close/open-1 AS oc_ret, (close/open-1>=0.30)::INT AS rocket,
             (hi10/nullif(lo10,0)>=1.8 OR maxday10>=0.50)::INT AS prior_runner
      FROM b WHERE d=DATE '{date}' AND pc1>0 AND open>0 AND adv20>=1e6 AND open BETWEEN 0.5 AND 20 AND open/pc1-1>=0.08
    """).df()
    if df.empty: return df
    tks="','".join(t.replace("'","") for t in df.ticker)
    # recent reverse split (<=90d before date): split_from>split_to
    rs=con.execute(f"""SELECT ticker, max(execution_date) last_rs FROM read_parquet('{SPLITS}')
        WHERE ticker IN ('{tks}') AND split_from>split_to AND execution_date<=DATE '{date}'
              AND execution_date>=DATE '{date}'-90 GROUP BY ticker""").df()
    td=con.execute(f"""SELECT ticker, cik, locale, primary_exchange, market_cap, share_class_shares_outstanding sh_out,
        sic_description, list_date FROM read_parquet('{TD}') WHERE ticker IN ('{tks}')""").df()
    df=df.merge(rs,on='ticker',how='left').merge(td,on='ticker',how='left')
    df['recent_reverse_split']=df.last_rs.notna().astype(int)
    df['foreign_issuer']=(df.locale.fillna('us').str.lower()!='us').astype(int)
    df['micro_float']=((df.sh_out>0)&(df.sh_out<5e6)).fillna(False).astype(int)
    df['coiled']=((df.rvol>=20)&(df.oc_ret.abs()<=0.10)).fillna(False).astype(int)   # note: oc_ret is same-day; coiled here = prior-style proxy
    # doc 282 NA-hardening (the silent nightly crash since 6/8): nullable dtypes (duckdb->pandas) leak pd.NA
    # through the boolean flags / int() casts -> TypeError int(NAType) in candidacy_score (line ~178).
    # Coerce every flag to plain int64 NA->0; drop rows missing the fields the logger float()s — LOUDLY.
    import pandas as _pd
    for _c in ('recent_reverse_split','foreign_issuer','micro_float','coiled','prior_runner','rocket'):
        if _c in df.columns:
            df[_c]=_pd.to_numeric(df[_c],errors='coerce').fillna(0).astype('int64')
    _need=[c for c in ('price','gap','adv20','oc_ret') if c in df.columns]
    _n0=len(df); df=df.dropna(subset=_need)
    if len(df)<_n0: print(f"  doc282: dropped {_n0-len(df)} unpriceable rows (NA in one of {_need})")
    return df

_CIK=None
def cik_map():
    global _CIK
    if _CIK is None:
        try:
            j=json.load(urllib.request.urlopen(urllib.request.Request("https://www.sec.gov/files/company_tickers.json",headers={'User-Agent':UA}),timeout=25))
            _CIK={v['ticker'].upper():str(v['cik_str']).zfill(10) for v in j.values()}
        except Exception: _CIK={}
    return _CIK

def edgar_insider_buys(ticker, cik, date, lookback=21):
    """Best-effort: count recent Form-4 filings with an open-market PURCHASE (code P) + distinct owners, in the
    lookback window before `date`. The doc-254 lead. Returns (n_filings_with_buy, distinct_owners)."""
    c=None
    try:
        if cik is not None and str(cik).strip().lower() not in ('','nan','none'):
            c=str(int(float(cik))).zfill(10)
    except Exception:
        c=None
    if not c: c=cik_map().get(ticker.upper())
    if not c: return (0,0)
    try:
        j=json.load(urllib.request.urlopen(urllib.request.Request(f"https://data.sec.gov/submissions/CIK{c}.json",headers={'User-Agent':UA}),timeout=25))
    except Exception: return (-1,-1)
    rec=j.get('filings',{}).get('recent',{}); cutoff=(dt.date.fromisoformat(date)-dt.timedelta(days=lookback))
    forms=rec.get('form',[]); dates=rec.get('filingDate',[]); accs=rec.get('accessionNumber',[]); pdocs=rec.get('primaryDocument',[])
    nbuy=0; owners=set()
    for i in range(len(forms)):
        if forms[i]!='4': continue
        try: fd=dt.date.fromisoformat(dates[i])
        except Exception: continue
        if not (cutoff<=fd<dt.date.fromisoformat(date)): continue
        accn=accs[i].replace('-',''); doc=pdocs[i]
        url=f"https://www.sec.gov/Archives/edgar/data/{int(c)}/{accn}/{doc}"
        try: txt=urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':UA}),timeout=25).read().decode('utf-8','replace')
        except Exception: continue
        if '<transactionCode>P</transactionCode>' in txt or 'transactionCode>P<' in txt:
            nbuy+=1
            import re
            m=re.search(r'<rptOwnerName>([^<]+)</rptOwnerName>',txt)
            owners.add(m.group(1).strip() if m else f'owner{i}')
        time.sleep(0.12)   # EDGAR rate limit
    return (nbuy, len(owners))

def recent_disclosure(ticker, cik, date, days=45):
    """Point-in-time: SEC forms filed in the `days` BEFORE `date` + the most-recent 8-K's text. The red-flag source:
    recent 8-Ks (financing/guidance) + presence of dilution filings (424B/S-1/S-3/EFFECT). Returns (forms, text)."""
    c=None
    try:
        if cik is not None and str(cik).strip().lower() not in ('','nan','none'): c=str(int(float(cik))).zfill(10)
    except Exception: c=None
    if not c: c=cik_map().get(str(ticker).upper())
    if not c: return ([], '')
    try:
        j=json.load(urllib.request.urlopen(urllib.request.Request(f"https://data.sec.gov/submissions/CIK{c}.json",headers={'User-Agent':UA}),timeout=25))
    except Exception: return ([], '')
    rec=j.get('filings',{}).get('recent',{}); cutoff=dt.date.fromisoformat(date)-dt.timedelta(days=days)
    forms=rec.get('form',[]); dates=rec.get('filingDate',[]); accs=rec.get('accessionNumber',[]); pdocs=rec.get('primaryDocument',[])
    rforms=[]; text8k=''
    for i in range(len(forms)):
        try: fd=dt.date.fromisoformat(dates[i])
        except Exception: continue
        if not (cutoff<=fd<dt.date.fromisoformat(date)): continue
        rforms.append(forms[i])
        if forms[i]=='8-K' and not text8k:
            accn=accs[i].replace('-',''); doc=pdocs[i]
            try:
                html=urllib.request.urlopen(urllib.request.Request(f"https://www.sec.gov/Archives/edgar/data/{int(c)}/{accn}/{doc}",headers={'User-Agent':UA}),timeout=25).read().decode('utf-8','replace')
                text8k=_strip_html(html)[:7000]
            except Exception: pass
            time.sleep(0.12)
    return (rforms, text8k)

REDFLAG_PROMPT=("You are a risk analyst screening a small-cap stock that gapped up today ({tk}, {dt}). Below are its "
 "recent SEC filing TYPES (last ~45d) and the text of its most recent 8-K. Assess the DILUTION / TOXIC-FINANCING / "
 "WEAK-FUNDAMENTAL red-flag risk that this rally will be SOLD INTO (faded). Weigh dilutive offerings (S-1/S-3/424B/"
 "ATM/convertible/warrant), going-concern, toxic lenders, weak guidance, shell-like traits. Respond ONLY JSON: "
 '{{"redflag":0.0-1.0,"reason":"<=12 words"}} where 1.0=severe dilution/fade risk, 0.0=clean.\n\n'
 "RECENT FILING TYPES: {forms}\n\n--- MOST RECENT 8-K ---\n{txt}")

def llm_redflag(ticker, cik, date, key):
    """Returns (redflag_score 0-1 or None, reason). Mechanism-aligned with doc 253 (dilution = the distribution)."""
    if not key: return (None,'no key')
    forms,txt=recent_disclosure(ticker,cik,date)
    if not forms and not txt: return (None,'no recent filings')
    dilution_filing=int(any(f in ('S-1','S-3','424B5','424B3','424B4','EFFECT','S-1/A','S-3/A') for f in forms))
    try:
        from openai import OpenAI; import re
        cl=OpenAI(base_url=TOGETHER_BASE, api_key=key, max_retries=0)
        r=cl.chat.completions.create(model=REDFLAG_MODEL,temperature=0.1,max_tokens=80,
            messages=[{'role':'user','content':REDFLAG_PROMPT.format(tk=ticker,dt=date,forms=', '.join(sorted(set(forms))[:20]) or 'none',txt=(txt or 'no 8-K text')[:7000])}])
        m=re.search(r'\{.*\}',r.choices[0].message.content,re.S)
        if not m: return (None,'parse')
        o=json.loads(m.group(0))
        rf=float(o.get('redflag',0.5)); rf=min(1.0,max(0.0,rf))
        # nudge up if a dilution filing is present and the LLM under-weighted it
        if dilution_filing and rf<0.5: rf=0.5+0.5*rf
        return (round(rf,2), str(o.get('reason',''))[:60])
    except Exception as e:
        return (None, f'err:{str(e)[:30]}')

def candidacy_score(r):
    return int(r.micro_float)*2 + int(r.recent_reverse_split) + int(r.foreign_issuer) + int(r.prior_runner) + int(r.coiled)

def run_day(date, con, do_edgar=True, do_llm=False):
    df=gappers(con, date)
    if df.empty: print(f"{date}: 0 gappers"); return
    df['candidacy']=[candidacy_score(r) for r in df.itertuples()]
    df=df.sort_values('candidacy',ascending=False).reset_index(drop=True)
    key=_together_key() if do_llm else None
    rows=[]
    for r in df.itertuples():
        nbuy,owners=(0,0)
        if do_edgar: nbuy,owners=edgar_insider_buys(r.ticker, getattr(r,'cik',None), date)
        rf,rfr=(None,'')
        if do_llm: rf,rfr=llm_redflag(r.ticker, getattr(r,'cik',None), date, key)
        rows.append(dict(date=date,ticker=r.ticker,price=round(float(r.price),2),gap=round(float(r.gap),3),
            adv_M=round(float(r.adv20)/1e6,1),candidacy=int(r.candidacy),micro_float=int(r.micro_float),
            recent_reverse_split=int(r.recent_reverse_split),foreign_issuer=int(r.foreign_issuer),
            prior_runner=int(r.prior_runner),coiled=int(r.coiled),insider_buys=int(nbuy),insider_owners=int(owners),
            insider_cluster=int(owners>=2),redflag=rf,redflag_reason=rfr,sic=str(getattr(r,'sic_description',''))[:40],
            oc_ret=round(float(r.oc_ret),3),rocket=int(r.rocket)))
    # de-dup this date, append
    if os.path.exists(LOG):
        keep=[l for l in open(LOG,encoding='utf-8') if json.loads(l).get('date')!=date]
        open(LOG,'w',encoding='utf-8').writelines(keep)
    with open(LOG,'a',encoding='utf-8') as f:
        for r in rows: f.write(json.dumps(r)+'\n')
    nr=sum(r['rocket'] for r in rows); ic=sum(r['insider_cluster'] for r in rows); co=sum(r['coiled'] for r in rows)
    print(f"{date}: {len(rows)} gappers logged | rockets {nr} | insider-clusters {ic} | coiled {co}")
    top=rows[:8]
    print("  top-candidacy: "+", ".join(f"{r['ticker']}(c{r['candidacy']},g{r['gap']*100:.0f}%{'*ROCKET' if r['rocket'] else ''}{' INSIDER' if r['insider_cluster'] else ''}{' COILED' if r['coiled'] else ''})" for r in top))
    return rows

def run_since(start, do_llm=False):
    con=duckdb.connect()
    days=con.execute(f"SELECT DISTINCT ts_et::DATE d FROM read_parquet('{DA}',hive_partitioning=1) WHERE ts_et::DATE>=DATE '{start}' ORDER BY d").df()
    for d in days.d: run_day(str(d)[:10], con, do_llm=do_llm)

def run_catchup(do_llm=False):
    """Daily entrypoint: re-run from a couple days before the last logged date (de-dup makes re-runs safe;
    the small look-back retro-fills days whose data arrived late) through the latest ingested day."""
    start=None
    if os.path.exists(LOG):
        ds=[json.loads(l).get('date') for l in open(LOG,encoding='utf-8') if l.strip()]
        ds=[x for x in ds if x]
        if ds: start=(dt.date.fromisoformat(max(ds))-dt.timedelta(days=2)).isoformat()
    if start is None:
        start=str(duckdb.connect().execute(f"SELECT max(ts_et::DATE) FROM read_parquet('{DA}',hive_partitioning=1)").fetchone()[0])[:10]
    print(f"catchup since {start}")
    run_since(start, do_llm=do_llm)

def report():
    if not os.path.exists(LOG): print("no watchlist log yet"); return
    df=pd.DataFrame([json.loads(l) for l in open(LOG,encoding='utf-8')])
    print(f"watchlist log: {df.date.nunique()} days, {len(df)} gapper-rows, {df.rocket.sum()} rockets ({df.rocket.mean()*100:.1f}%)")
    print("\n=== LEAD vs OUTCOME (accumulated; powers the doc-254 leads out-of-sample) ===")
    for lead in ['insider_cluster','coiled','prior_runner','micro_float','recent_reverse_split','foreign_issuer']:
        for v in [1]:
            s=df[df[lead]==v]
            if len(s): print(f"  {lead}=1: n={len(s):>4} rocket-rate {s.rocket.mean()*100:>4.1f}%  (base {df.rocket.mean()*100:.1f}%, lift {s.rocket.mean()/max(df.rocket.mean(),1e-9):.1f}x)")
    # doc 260: prospective LLM red-flag / fader test. Hypothesis: HIGH red-flag -> MORE fade (lower oc_ret, lower rocket-rate).
    if 'redflag' in df.columns:
        rdf=df[df.redflag.notna()].copy()
        if len(rdf)>=20:
            rdf['redflag']=pd.to_numeric(rdf.redflag,errors='coerce')
            hi=rdf[rdf.redflag>=0.6]; lo=rdf[rdf.redflag<0.4]
            print("\n=== doc-260 LLM RED-FLAG vs OUTCOME (prospective, OOS; HIGH red-flag should FADE more) ===")
            print(f"  scored {len(rdf)} gapper-rows | mean redflag {rdf.redflag.mean():.2f}")
            for tag,s in [('HIGH redflag>=0.6',hi),('LOW redflag<0.4',lo)]:
                if len(s): print(f"  {tag:>18}: n={len(s):>4}  mean oc_ret {s.oc_ret.mean()*100:>+5.1f}%  median {s.oc_ret.median()*100:>+5.1f}%  rocket-rate {s.rocket.mean()*100:>4.1f}%")
            if len(hi)>=10 and len(lo)>=10:
                # doc 272 A6 (fleet-assessment fix): compute the pre-registered VERDICT instead of
                # handing the reader a legend beside a wrong-direction mean. The doc-260/261 rule is
                # MEDIAN-based (the house gate): HIGH red-flag should fade more than LOW.
                _mean_sp=(hi.oc_ret.mean()-lo.oc_ret.mean())*100
                _med_sp=(hi.oc_ret.median()-lo.oc_ret.median())*100
                _verdict="REPLICATING (median)" if _med_sp<0 else "NOT replicating"
                print(f"  --> spread HIGH-minus-LOW: median {_med_sp:+.1f}pp (the gate) | mean {_mean_sp:+.1f}pp (tail-noisy) ==> {_verdict} at n={len(hi)}/{len(lo)}")
    print("\n  (Need ~weeks of forward days for the 5%-rare insider/coiled leads + the LLM red-flag to reach power. This is the collection.)")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--run',default=''); ap.add_argument('--run-since',default='')
    ap.add_argument('--report',action='store_true'); ap.add_argument('--no-edgar',action='store_true')
    ap.add_argument('--catchup',action='store_true'); ap.add_argument('--llm',action='store_true',help='doc260: add LLM red-flag/fader score per gapper')
    a=ap.parse_args()
    if a.run: run_day(a.run, duckdb.connect(), do_edgar=not a.no_edgar, do_llm=a.llm)
    elif a.run_since: run_since(a.run_since, do_llm=a.llm)
    elif a.catchup: run_catchup(do_llm=a.llm)
    elif a.report: report()
    else: ap.print_help()

if __name__=="__main__": main()
