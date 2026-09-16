"""doc 257 - enrich the RocketScalp-RL episodes with the STATIC FORENSIC CANDIDACY FEATURES (the data we
collected via deep research), so the monster (v2) can fuse them into the RL state. NO live change.

Per episode (ticker, date) joins from the warehouse: gap, micro-float (ticker_details shares-out), recent
reverse-split (splits<=90d), foreign-issuer (locale), prior-runner + coiled (day_aggs trailing-10d), log
market-cap, days-since-listing, SIC sector code. Caches the enriched episode list for the v2 builder.
"""
from __future__ import annotations
import os, pickle, numpy as np, pandas as pd, duckdb
DA="data/polygon_warehouse/day_aggs/**/*.parquet"
SPLITS="data/polygon_warehouse/reference/splits.parquet"
TD="data/polygon_warehouse/reference/ticker_details.parquet"
SRC="data/research/_rocketscalp_episodes.pkl"; OUT="data/research/_rocketscalp_episodes_enriched.pkl"
# the static candidacy feature vector appended to the RL state (order fixed):
CAND_FEATS=["gap","micro_float","log_shares","recent_reverse_split","rsplit_days","foreign_issuer",
            "prior_runner","coiled","log_mcap","days_listed","sic_health","sic_tech"]

def main():
    eps=pickle.load(open(SRC,'rb'))
    keys=sorted({(e['ticker'],e['date']) for e in eps})
    print(f"{len(eps)} episodes | {len(keys)} unique ticker-days -> computing candidacy features")
    con=duckdb.connect()
    tks="','".join(sorted({k[0].replace("'","") for k in keys}))
    # day_aggs: gap + prior-runner + coiled per ticker-day (trailing-10d windows)
    da=con.execute(f"""
      WITH r AS (SELECT ticker, ts_et, open, high, low, close, volume,
                   lag(close) OVER w pc1, close/lag(close) OVER w -1 ret1
                 FROM read_parquet('{DA}',hive_partitioning=1) WHERE ticker IN ('{tks}')
                 WINDOW w AS (PARTITION BY ticker ORDER BY ts_et)),
      b AS (SELECT ticker, ts_et::DATE d, open, close, pc1, volume,
              avg(volume) OVER w20 avol20, max(close) OVER w10 hi10, min(close) OVER w10 lo10, max(ret1) OVER w10 mx10
            FROM r WINDOW w20 AS (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING),
                          w10 AS (PARTITION BY ticker ORDER BY ts_et ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING))
      SELECT ticker, d::VARCHAR date, open/nullif(pc1,0)-1 gap, volume/nullif(avol20,0) rvol,
             (hi10/nullif(lo10,0)>=1.8 OR mx10>=0.50)::INT prior_runner,
             (volume/nullif(avol20,0)>=20 AND abs(close/open-1)<=0.10)::INT coiled
      FROM b""").df()
    td=con.execute(f"""SELECT ticker, locale, market_cap, share_class_shares_outstanding sh_out, sic_code, list_date
        FROM read_parquet('{TD}') WHERE ticker IN ('{tks}')""").df()
    rs=con.execute(f"""SELECT ticker, execution_date FROM read_parquet('{SPLITS}')
        WHERE ticker IN ('{tks}') AND split_from>split_to""").df()
    rs['execution_date']=pd.to_datetime(rs.execution_date)
    da_idx={(r.ticker,r.date):r for r in da.itertuples()}
    td_idx={r.ticker:r for r in td.itertuples()}
    rs_by={}; [rs_by.setdefault(r.ticker,[]).append(r.execution_date) for r in rs.itertuples()]
    n_ok=0
    for e in eps:
        tk,d=e['ticker'],e['date']; dd=pd.to_datetime(d)
        a=da_idx.get((tk,d)); t=td_idx.get(tk)
        sh=float(getattr(t,'sh_out',np.nan)) if t is not None else np.nan
        mc=float(getattr(t,'market_cap',np.nan)) if t is not None else np.nan
        loc=str(getattr(t,'locale','us')) if t is not None else 'us'
        sic=str(getattr(t,'sic_code','')) if t is not None else ''
        ld=getattr(t,'list_date',None) if t is not None else None
        rsplits=[x for x in rs_by.get(tk,[]) if x<=dd and x>=dd-pd.Timedelta(days=90)]
        days_listed=(dd-pd.to_datetime(ld)).days if ld is not None and str(ld) not in ('','nan','None') else 3650
        _f=lambda x,dft=0.0: (float(x) if pd.notna(x) else dft)
        e['cand']=dict(
            gap=_f(getattr(a,'gap',0.0)) if a is not None else 0.0,
            micro_float=float(0<sh<5e6) if sh==sh else 0.0,
            log_shares=float(np.log(max(sh,1e5))) if sh==sh and sh>0 else 16.0,
            recent_reverse_split=float(len(rsplits)>0),
            rsplit_days=float(min((dd-max(rsplits)).days,90)/90.0) if rsplits else 1.0,
            foreign_issuer=float(loc.lower()!='us'),
            prior_runner=_f(getattr(a,'prior_runner',0)) if a is not None else 0.0,
            coiled=_f(getattr(a,'coiled',0)) if a is not None else 0.0,
            log_mcap=float(np.log(max(mc,1e6))) if mc==mc and mc>0 else 18.0,
            days_listed=float(min(max(days_listed,0),3650)/3650.0),
            sic_health=float(sic.startswith('283') or sic.startswith('80') or sic.startswith('38')),  # pharma/health/instruments
            sic_tech=float(sic.startswith('737') or sic.startswith('357') or sic.startswith('367')),   # software/computers/electronics
        )
        n_ok+= a is not None
    pickle.dump(eps,open(OUT,'wb'))
    # quick sanity: candidacy-feature prevalence + rocket association
    df=pd.DataFrame([{**e['cand'],'eod':e['eod'],'year':e['year']} for e in eps])
    print(f"enriched {len(eps)} episodes ({n_ok} matched day_aggs) -> {OUT}")
    print("candidacy-feature prevalence:")
    for f in ['micro_float','recent_reverse_split','foreign_issuer','prior_runner','coiled']:
        print(f"  {f:>22}: {df[f].mean()*100:.0f}%")
    print(f"  feature vector dim appended to RL state: {len(CAND_FEATS)}")

if __name__=="__main__": main()
