"""doc278 SCOPED fragility measurement (panel-fixed). Corrected ADV basis = TAPE-FAITHFUL minute-rolled daily
dollar-volume for ALL 2026 (Q1 hole + post-hole = one consistent basis, validated within ~2-10% of the raw tape),
with min_periods=20 (NO partial/NULL bridging). Contaminated ADV = the same formula on day_aggs-only (starts 03-23,
so pre-late-April windows bridge/short). For the immune-flagged hole-adjacent corpus ticker-days:
 - RETROACTIVE-EJECTION (pass->fail): corrected adv20 < $1M among days that passed at collection (unidirectional,
   since the corpus is 100% post-gate).
 - COVERAGE-flip (adv20 NULL/short in contaminated -> computable in corrected) reported SEPARATELY (not fragility).
 - FRAGILITY INDEX: distribution of corrected adv20 vs the $1M threshold (near-threshold band populated?).
Read-only. Corrected basis is minute-rolled (tape-faithful) NOT day_aggs (the panel-verified erratic proxy)."""
import os
import duckdb
import numpy as np

WH = "data/polygon_warehouse"
OUT = "data/research/doc278"
con = duckdb.connect()
con.execute("SET threads=4")

# --- 1. tape-faithful minute-rolled daily dollar-volume for ALL 2026 ---
mg = f"{WH}/minute_aggs/year=2026/**/*.parquet".replace("\\", "/")
print("rolling all-2026 minute -> daily dollar-volume (tape-faithful basis)...")
con.execute(f"""
CREATE TEMP TABLE dd AS
SELECT ticker, make_date(year::BIGINT, month::BIGINT, day::BIGINT) d,
       sum(volume*close) dvol, sum(volume) vol, arg_max(close, window_start) dclose
FROM read_parquet('{mg}', union_by_name=true)
GROUP BY ticker, year, month, day
""")
r = con.execute("SELECT count(*), count(DISTINCT d) FROM dd").fetchone()
print(f"  daily bars: {r[0]:,} ticker-days over {r[1]} sessions")

# corrected adv20 (min_periods=20) on the full tape-faithful basis
con.execute("""
CREATE TEMP TABLE corr AS
SELECT ticker, d,
  CASE WHEN count(*) OVER w = 20 THEN avg(dvol) OVER w END adv20_corr,
  (d - lag(d,20) OVER (PARTITION BY ticker ORDER BY d)) win_span_corr
FROM dd
WINDOW w AS (PARTITION BY ticker ORDER BY d ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)
""")

# contaminated adv20 on day_aggs-only, BOTH strict (min_periods=20 -> NULL) AND partial (as the LIVE immune
# system computed it: DuckDB avg over 1-20 available rows, no min_periods -> the value the gate actually used)
da = f"{WH}/day_aggs/year=2026/**/*.parquet".replace("\\", "/")
con.execute(f"""
CREATE TEMP TABLE cont AS
WITH b AS (SELECT ticker, ts_et::DATE d, volume*close dvol FROM read_parquet('{da}', union_by_name=true))
SELECT ticker, d,
  CASE WHEN count(*) OVER w = 20 THEN avg(dvol) OVER w END adv20_cont,
  avg(dvol) OVER w AS adv20_cont_partial,   -- what the live gate used (partial windows trusted)
  count(*) OVER w AS n_win_cont,
  (d - lag(d,20) OVER (PARTITION BY ticker ORDER BY d)) win_span_cont
FROM b
WINDOW w AS (PARTITION BY ticker ORDER BY d ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)
""")

# --- 2. flagged hole-adjacent corpus ticker-days = corpus days whose contaminated window bridged/short ---
corpus = "data/research/exit_labels_cross_regime.parquet"
ccols = con.execute(f"SELECT * FROM read_parquet('{corpus}') LIMIT 0").df().columns.tolist()
tk = next((c for c in ccols if c.lower() in ("ticker", "symbol")), "ticker")
sd = next((c for c in ccols if c.lower() in ("session_date", "date", "d")), None)
con.execute(f"""CREATE TEMP TABLE corp AS
  SELECT DISTINCT {tk} ticker, CAST({sd} AS DATE) d FROM read_parquet('{corpus}')
  WHERE CAST({sd} AS VARCHAR) >= '2026-03-23'""")

res = con.execute("""
SELECT c.ticker, c.d, co.adv20_cont, co.adv20_cont_partial, co.n_win_cont, co.win_span_cont, cr.adv20_corr, cr.win_span_corr
FROM corp c
LEFT JOIN cont co ON co.ticker=c.ticker AND co.d=c.d
LEFT JOIN corr cr ON cr.ticker=c.ticker AND cr.d=c.d
""").df()

n = len(res)
# affected = contaminated window bridged (>40d) OR contaminated adv20 short/NULL (insufficient history)
affected = res[(res.win_span_cont.isna()) | (res.win_span_cont > 40) | (res.adv20_cont.isna())]
print(f"\ncorpus ticker-days (>=03-23): {n:,}; affected by hole (contaminated window bridged/short/NULL): {len(affected):,} ({100*len(affected)/n:.1f}%)")

# COVERAGE-flip: contaminated adv20 NULL (uncomputable) but corrected computable
cov = affected[(affected.adv20_cont.isna()) & (affected.adv20_corr.notna())]
print(f"\nCOVERAGE-flip (contaminated adv20 NULL -> corrected computable; NOT fragility): {len(cov):,}")

# VALUE-flip / RETROACTIVE-EJECTION: both computable, corrected < $1M (gate would now reject)
val = affected[(affected.adv20_cont.notna()) & (affected.adv20_corr.notna())]
ejected = val[val.adv20_corr < 1e6]
print(f"\nVALUE-comparable affected days (both adv20 computable): {len(val):,}")
print(f"RETROACTIVE-EJECTION (corrected adv20 < $1M, was admitted): {len(ejected):,} ({100*len(ejected)/max(len(val),1):.2f}% of value-comparable)")
# direction sanity: how many were also <$1M contaminated (already-marginal)
if len(val):
    both_low = val[(val.adv20_cont < 1e6)]
    print(f"  (of value-comparable, contaminated<$1M: {len(both_low):,})")

# FRAGILITY INDEX: corrected adv20 vs $1M threshold
if len(val):
    a = val.adv20_corr.values
    band = ((a >= 0.7e6) & (a <= 1.5e6)).sum()
    print(f"\nFRAGILITY INDEX (corrected adv20 relative to $1M gate), n={len(val):,}:")
    print(f"  median=${np.median(a)/1e6:.2f}M  p10=${np.percentile(a,10)/1e6:.2f}M  p90=${np.percentile(a,90)/1e6:.1f}M")
    print(f"  near-threshold band [$0.7M,$1.5M]: {band:,} ({100*band/len(val):.1f}%)  ->  <$1M: {(a<1e6).sum():,}  >=$2M: {(a>=2e6).sum():,}")

# ADV ERROR: corrected vs contaminated on value-comparable
if len(val):
    err = np.abs(val.adv20_cont.values - val.adv20_corr.values) / np.maximum(val.adv20_corr.values, 1)
    print(f"\nADV-ERROR |contaminated-corrected|/corrected (value-comparable): median={np.median(err):.2f} p90={np.percentile(err,90):.2f} max={err.max():.1f} >2x={int((err>2).sum())}")
# --- LIVE-SYSTEM retroactive-ejection: partial-window contaminated (what the gate USED) vs corrected full-20 ---
live = affected[(affected.adv20_cont_partial.notna()) & (affected.adv20_corr.notna())]
adm = live[live.adv20_cont_partial >= 1e6]   # partial-window ADV admitted them (>=$1M)
ejL = adm[adm.adv20_corr < 1e6]
print(f"\n=== LIVE-SYSTEM view (partial-window contaminated = what the gate actually used) ===")
print(f"affected days with a partial-window contaminated ADV AND corrected full-20: {len(live):,}")
print(f"  admitted by partial-window (>=$1M): {len(adm):,}")
print(f"  RETROACTIVE-EJECTION (corrected full-20 < $1M): {len(ejL):,} ({100*len(ejL)/max(len(adm),1):.2f}% of admitted)")
if len(adm):
    infl = (adm.adv20_cont_partial.values / np.maximum(adm.adv20_corr.values, 1))
    print(f"  partial/corrected inflation ratio k: median={np.median(infl):.2f} p90={np.percentile(infl,90):.1f} max={infl.max():.1f} (GLXG-class >5x: {int((infl>5).sum())})")
res.to_parquet(f"{OUT}/fragility2_rows.parquet")
print(f"\nwrote {OUT}/fragility2_rows.parquet")
con.close()
