"""doc278 FRAGILITY MEASUREMENT: recompute the doc-235 universe gate under CONTAMINATED (day_aggs only)
vs REBUILT (day_aggs + Q1 rebuild) ADV, over the research corpus ticker-days. Headline = the decision-FLIP rate,
with (a) NULL->computable vs computed-wrong->computed-right split, (b) flip direction, (c) inert-vs-fragile
(Goes 2023: of non-flips, how many fail the gate on price/gap REGARDLESS of adv20). Holds the dollar-volume formula
FIXED (volume*close) so the ONLY difference is Q1 window completeness. Read-only."""
import os
import duckdb

WH = "data/polygon_warehouse"
OUT = "data/research/doc278"
con = duckdb.connect()
con.execute("SET threads=4")

da = f"{WH}/day_aggs/year=2026/**/*.parquet".replace("\\", "/")
rebuild = f"{OUT}/day_aggs_q1_rebuild.parquet"
corpus = "data/research/exit_labels_cross_regime.parquet"

# unified daily bars (ticker, session_date, close, volume) for CONTAMINATED and REBUILT
con.execute(f"""
CREATE TEMP TABLE contam AS
  SELECT ticker, ts_et::DATE AS d, close, volume FROM read_parquet('{da}', union_by_name=true);
CREATE TEMP TABLE rebuilt AS
  SELECT ticker, ts_et::DATE AS d, close, volume FROM read_parquet('{da}', union_by_name=true)
  UNION ALL
  SELECT ticker, session_date AS d, close, volume FROM read_parquet('{rebuild}');
""")


def audit(tbl):
    return f"""
      WITH b AS (
        SELECT ticker, d, close, volume,
          lag(close) OVER (PARTITION BY ticker ORDER BY d) pc,
          avg(volume*close) OVER (PARTITION BY ticker ORDER BY d ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) adv20,
          (d - lag(d, 20) OVER (PARTITION BY ticker ORDER BY d)) win_span,
          open  -- placeholder; open not needed, gate uses open/pc but corpus has its own open at decision
        FROM (SELECT *, close AS open FROM {tbl})
      )
      SELECT ticker, d AS session_date, adv20, win_span, pc,
             (pc IS NULL OR adv20 IS NULL OR adv20 < 1e6) AS adv_or_pc_fail,
             (adv20 IS NULL) AS adv_null,
             (adv20 IS NOT NULL AND adv20 < 1e6) AS adv_below,
             (win_span IS NULL OR win_span > 40) AS window_bridged
      FROM b"""


con.execute(f"CREATE TEMP TABLE aC AS {audit('contam')}")
con.execute(f"CREATE TEMP TABLE aR AS {audit('rebuilt')}")

# corpus ticker-days
ccols = con.execute(f"SELECT * FROM read_parquet('{corpus}') LIMIT 0").df().columns.tolist()
tk = next((c for c in ccols if c.lower() in ("ticker", "symbol")), "ticker")
sd = next((c for c in ccols if c.lower() in ("session_date", "date", "d")), None)
print("corpus cols:", ccols[:8], "| ticker=", tk, "date=", sd)
con.execute(f"CREATE TEMP TABLE corp AS SELECT DISTINCT {tk} ticker, CAST({sd} AS DATE) session_date "
            f"FROM read_parquet('{corpus}') WHERE CAST({sd} AS VARCHAR) >= '2026-03-23'")

res = con.execute("""
SELECT c.ticker, c.session_date,
       aC.adv20 advC, aR.adv20 advR, aC.win_span wsC, aR.win_span wsR,
       aC.adv_or_pc_fail failC, aR.adv_or_pc_fail failR,
       aC.adv_null nullC, aR.adv_null nullR, aC.window_bridged bridgedC
FROM corp c
LEFT JOIN aC ON aC.ticker=c.ticker AND aC.session_date=c.session_date
LEFT JOIN aR ON aR.ticker=c.ticker AND aR.session_date=c.session_date
""").df()

n = len(res)
# affected = corpus rows the immune system would flag (window bridged in contaminated, or adv null)
affected = res[(res.bridgedC == True) | (res.nullC == True)]
print(f"\ncorpus ticker-days (>=2026-03-23) matched: {n:,}")
print(f"affected (contaminated window bridged or adv NULL): {len(affected):,} ({100*len(affected)/max(n,1):.1f}%)")

# FLIP: gate adv/pc component flips between contaminated and rebuilt (only where both computable enough)
both = res.dropna(subset=["failC", "failR"])
flips = both[both.failC != both.failR]
print(f"\n=== DECISION-FLIP (adv/prev-close gate component) ===")
print(f"  evaluable ticker-days (both sides non-null gate): {len(both):,}")
print(f"  FLIPS: {len(flips):,} ({100*len(flips)/max(len(both),1):.2f}%)")
if len(flips):
    f2p = flips[(flips.failC == True) & (flips.failR == False)]
    p2f = flips[(flips.failC == False) & (flips.failR == True)]
    print(f"    fail->pass (contam rejected, rebuilt admits): {len(f2p):,}")
    print(f"    pass->fail (contam admitted, rebuilt rejects): {len(p2f):,}")

# NULL->computable vs computed-wrong->right
nowC = res[(res.nullC == True) & (res.nullR == False)]
print(f"\n  adv NULL in contaminated -> computable in rebuilt: {len(nowC):,} (were UNCOMPUTABLE, not mis-computed)")

# ADV error on affected where both non-null
aff2 = affected.dropna(subset=["advC", "advR"])
aff2 = aff2[aff2.advR > 0]
if len(aff2):
    import numpy as np
    err = (np.abs(aff2.advC - aff2.advR) / aff2.advR)
    print(f"\n=== ADV ERROR on affected (both computable), n={len(aff2):,} ===")
    print(f"  median rel err={err.median():.2f}  p90={err.quantile(0.9):.2f}  max={err.max():.1f}  >2x: {int((err>2).sum())}")

res.to_parquet(f"{OUT}/fragility_rows.parquet")
print(f"\nwrote {OUT}/fragility_rows.parquet")
con.close()
