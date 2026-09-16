"""doc278: diagnose the minute-vs-day_aggs divergence. Three hypotheses: ADJUSTMENT (split-adjusted vs raw),
COVERAGE (minute incomplete), DATA-QUALITY (day_aggs values corrupt). Read-only."""
import duckdb

WH = "data/polygon_warehouse"
con = duckdb.connect()
con.execute("SET threads=4")
mg = f"{WH}/minute_aggs/year=2026/month=03/**/*.parquet".replace("\\", "/")
da = f"{WH}/day_aggs/year=2026/**/*.parquet".replace("\\", "/")

print("=== (1) minute completeness for mismatch tickers on 2026-03-24 (n minutes, time span, vol) ===")
for tk in ["ADTX", "SOXS", "PAVS", "GLXG"]:
    r = con.execute(f"""
      SELECT count(*) n, min(window_start) mn, max(window_start) mx, sum(volume) v,
             min(low) lo, max(high) hi
      FROM read_parquet('{mg}', union_by_name=true) WHERE ticker='{tk}' AND day=24
    """).fetchone()
    if r and r[0]:
        span_h = (r[2] - r[1]) / 3.6e12 if r[1] else 0
        print(f"  {tk}: n_min={r[0]} span={span_h:.1f}h vol={r[3]:,.0f} px[{r[4]},{r[5]}]")
    else:
        print(f"  {tk}: NO minute rows on 03-24")

print("\n=== (2) day_aggs values for the same tickers 2026-03-24 (transactions tells real activity) ===")
for tk in ["ADTX", "SOXS", "PAVS", "GLXG"]:
    r = con.execute(f"SELECT open,high,low,close,volume,transactions FROM read_parquet('{da}',union_by_name=true) WHERE ticker='{tk}' AND day=24").fetchone()
    print(f"  {tk}: {r}")

print("\n=== (3) adjustment check: is existing.close = rolled.close / k for a consistent k? ===")
# recompute rolled for these and show ratio
for tk in ["ADTX", "SOXS", "PAVS"]:
    rr = con.execute(f"SELECT arg_min(open,window_start),arg_max(close,window_start),sum(volume) FROM read_parquet('{mg}',union_by_name=true) WHERE ticker='{tk}' AND day=24").fetchone()
    ee = con.execute(f"SELECT open,close,volume FROM read_parquet('{da}',union_by_name=true) WHERE ticker='{tk}' AND day=24").fetchone()
    if rr and ee and rr[1] and ee[1]:
        print(f"  {tk}: close ratio r/e={rr[1]/ee[1]:.2f}  vol ratio e/r={ee[2]/rr[2]:.1f}  (open r={rr[0]} e={ee[0]})")

print("\n=== (4) does trades_v1 (raw tape) cover 2026-03-24 as a tie-breaker? ===")
import os
tv = f"{WH}/trades_v1_parquet/year=2026/month=03"
print("  trades_v1 2026-03 exists:", os.path.isdir(tv))
if os.path.isdir(tv):
    tvg = f"{tv}/**/*.parquet".replace("\\", "/")
    try:
        cols = con.execute(f"SELECT * FROM read_parquet('{tvg}',union_by_name=true) LIMIT 0").df().columns.tolist()
        print("  trades cols:", cols[:10])
    except Exception as e:
        print("  trades read err:", str(e)[:100])

print("\n=== (5) universe overlap: how many day_aggs tickers on 03-24 have ANY minute rows that day ===")
r = con.execute(f"""
  SELECT
    (SELECT count(DISTINCT ticker) FROM read_parquet('{da}',union_by_name=true) WHERE day=24) AS day_tickers,
    (SELECT count(DISTINCT ticker) FROM read_parquet('{mg}',union_by_name=true) WHERE day=24) AS min_tickers
""").fetchone()
print(f"  day_aggs tickers={r[0]:,}  minute tickers={r[1]:,}  ratio={r[1]/r[0]:.2f}")
con.close()
