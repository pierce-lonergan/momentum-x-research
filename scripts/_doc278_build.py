"""doc278: BUILD the day_aggs Q1 rebuild (parallel artifact, read-only source) + reconcile it.
Rolls minute_aggs Q1 (2026-01..03) -> daily bars in the day_aggs schema. Writes
data/research/doc278/day_aggs_q1_rebuild.parquet (NEVER touches the live warehouse).
Also reconciles dollar-volume (volume*close, the immune-system ADV basis) on a post-hole date."""
import os
import duckdb

WH = "data/polygon_warehouse"
OUT = "data/research/doc278"
os.makedirs(OUT, exist_ok=True)
con = duckdb.connect()
con.execute("SET threads=4")

# --- 1. roll minute Q1 -> daily, using the partition (year,month,day) as the session date ---
# open=first-by-time, close=last-by-time, high=max, low=min, volume=sum, transactions=sum
mgQ1 = f"{WH}/minute_aggs/year=2026/month=0[123]/**/*.parquet".replace("\\", "/")
print("rolling minute Q1 -> daily ...")
con.execute(f"""
CREATE TEMP TABLE q1 AS
SELECT ticker, year, month, day,
       make_date(year::BIGINT, month::BIGINT, day::BIGINT) AS session_date,
       arg_min(open, window_start)  AS open,
       max(high)                    AS high,
       min(low)                     AS low,
       arg_max(close, window_start) AS close,
       sum(volume)                  AS volume,
       sum(transactions)            AS transactions,
       count(*)                     AS n_min
FROM read_parquet('{mgQ1}', union_by_name=true)
GROUP BY ticker, year, month, day
""")
r = con.execute("SELECT count(*), count(DISTINCT ticker), count(DISTINCT session_date), min(session_date), max(session_date) FROM q1").fetchone()
print(f"  Q1 rebuild: {r[0]:,} ticker-days, {r[1]:,} tickers, {r[2]} sessions, {r[3]}..{r[4]}")

# --- 2. keep only the HOLE (before 2026-03-23, the day_aggs start) ---
con.execute("CREATE TEMP TABLE hole AS SELECT * FROM q1 WHERE session_date < DATE '2026-03-23'")
r = con.execute("SELECT count(*), count(DISTINCT session_date), min(session_date), max(session_date) FROM hole").fetchone()
print(f"  hole fill (< 2026-03-23): {r[0]:,} ticker-days, {r[1]} sessions, {r[2]}..{r[3]}")

# write the parallel artifact
con.execute(f"COPY (SELECT ticker, session_date, open, high, low, close, volume, transactions, n_min FROM hole) "
            f"TO '{OUT}/day_aggs_q1_rebuild.parquet' (FORMAT PARQUET)")
print(f"  wrote {OUT}/day_aggs_q1_rebuild.parquet (PARALLEL artifact; live warehouse untouched)")

# --- 3. reconcile dollar-volume on 2026-03-24 (day_aggs has it): rolled full-day vs existing ---
da = f"{WH}/day_aggs/year=2026/**/*.parquet".replace("\\", "/")
recon = con.execute(f"""
WITH roll AS (SELECT ticker, sum(volume) v, arg_max(close, window_start) c
              FROM read_parquet('{mgQ1}', union_by_name=true) WHERE month=3 AND day=24 GROUP BY ticker),
     ex AS (SELECT ticker, volume v, close c FROM read_parquet('{da}', union_by_name=true) WHERE month=3 AND day=24)
SELECT count(*) n,
  sum(CASE WHEN abs(roll.v*roll.c - ex.v*ex.c) <= 0.05*(ex.v*ex.c)+1 THEN 1 ELSE 0 END) dv_5pct,
  sum(CASE WHEN ex.v*ex.c >= 1e6 THEN 1 ELSE 0 END) liquid_ex,
  sum(CASE WHEN ex.v*ex.c >= 1e6 AND abs(roll.v*roll.c - ex.v*ex.c) <= 0.05*(ex.v*ex.c)+1 THEN 1 ELSE 0 END) liquid_dv_5pct
FROM roll JOIN ex USING (ticker)
""").fetchone()
print(f"\nRECONCILE dollar-volume (volume*close) 2026-03-24: n={recon[0]:,}")
print(f"  dollar-vol within 5%: {recon[1]:,} ({100*recon[1]/recon[0]:.1f}%)")
print(f"  liquid (>=$1M) names: {recon[2]:,}; of those dollar-vol within 5%: {recon[3]:,} ({100*recon[3]/max(recon[2],1):.1f}%)")
con.close()
