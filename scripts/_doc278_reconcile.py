"""doc278 STEP 1: reconcile the minute->daily aggregation against EXISTING day_aggs on a post-03-23 date,
BEFORE trusting it on the Q1 hole (the design panel's "does the enabler silently corrupt what it enables").
Tests whether grouping minute_aggs by (ticker, year, month, day) with open=first/high=max/low=min/close=last/
volume=sum/transactions=sum reproduces the existing day_aggs bars. Read-only."""
import duckdb

WH = "data/polygon_warehouse"
con = duckdb.connect()
con.execute("SET threads=4")

DATE = ("2026", "03", "24")  # a session day_aggs HAS
y, m, d = DATE
mg = f"{WH}/minute_aggs/year={y}/month={m}/**/*.parquet".replace("\\", "/")
da = f"{WH}/day_aggs/year={y}/**/*.parquet".replace("\\", "/")

# roll minute -> day for that date, ordering open/close by window_start
con.execute(f"""
CREATE TEMP TABLE rolled AS
SELECT ticker,
       arg_min(open, window_start)  AS open,
       max(high)                    AS high,
       min(low)                     AS low,
       arg_max(close, window_start) AS close,
       sum(volume)                  AS volume,
       sum(transactions)            AS transactions,
       count(*)                     AS n_min
FROM read_parquet('{mg}', union_by_name=true)
WHERE day = {int(d)}
GROUP BY ticker
""")

con.execute(f"""
CREATE TEMP TABLE existing AS
SELECT ticker, open, high, low, close, volume, transactions
FROM read_parquet('{da}', union_by_name=true)
WHERE month = {int(m)} AND day = {int(d)}
""")

n_roll = con.execute("SELECT count(*) FROM rolled").fetchone()[0]
n_exist = con.execute("SELECT count(*) FROM existing").fetchone()[0]
print(f"reconcile {y}-{m}-{d}: rolled tickers={n_roll:,}  existing day_aggs tickers={n_exist:,}")

# join and compare
comp = con.execute("""
SELECT
  count(*) AS matched_tickers,
  sum(CASE WHEN abs(r.close - e.close) <= 0.005*abs(e.close)+1e-6 THEN 1 ELSE 0 END) AS close_match,
  sum(CASE WHEN r.volume = e.volume THEN 1 ELSE 0 END) AS vol_exact,
  sum(CASE WHEN abs(r.volume - e.volume) <= 0.01*e.volume+1 THEN 1 ELSE 0 END) AS vol_1pct,
  sum(CASE WHEN abs(r.high - e.high) <= 0.005*abs(e.high)+1e-6 THEN 1 ELSE 0 END) AS high_match,
  sum(CASE WHEN abs(r.low - e.low) <= 0.005*abs(e.low)+1e-6 THEN 1 ELSE 0 END) AS low_match,
  sum(CASE WHEN abs(r.open - e.open) <= 0.005*abs(e.open)+1e-6 THEN 1 ELSE 0 END) AS open_match
FROM rolled r JOIN existing e USING (ticker)
""").fetchone()
mt = comp[0]
print(f"  joined (in both): {mt:,}")
print(f"  close within 0.5%: {comp[1]:,} ({100*comp[1]/mt:.1f}%)")
print(f"  volume exact:      {comp[2]:,} ({100*comp[2]/mt:.1f}%)")
print(f"  volume within 1%:  {comp[3]:,} ({100*comp[3]/mt:.1f}%)")
print(f"  high within 0.5%:  {comp[4]:,} ({100*comp[4]/mt:.1f}%)")
print(f"  low  within 0.5%:  {comp[5]:,} ({100*comp[5]/mt:.1f}%)")
print(f"  open within 0.5%:  {comp[6]:,} ({100*comp[6]/mt:.1f}%)")

# show a few mismatches (likely pre/post-market inclusion differences)
print("\n  sample close mismatches (>0.5%):")
mm = con.execute("""
SELECT r.ticker, r.open, e.open, r.close, e.close, r.volume, e.volume
FROM rolled r JOIN existing e USING (ticker)
WHERE abs(r.close - e.close) > 0.005*abs(e.close)+1e-6
ORDER BY e.volume DESC LIMIT 6
""").fetchall()
for row in mm:
    print(f"    {row[0]}: open r={row[1]}/e={row[2]} close r={row[3]}/e={row[4]} vol r={row[5]}/e={row[6]}")
con.close()
