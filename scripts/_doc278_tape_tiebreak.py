"""doc278 TAPE TIE-BREAKER: the panel says minute-rolled volume runs ~10-34% below day_aggs and calls the
rebuild unfit (assuming day_aggs is truth). But the contamination premise is that day_aggs ADV is INFLATED.
The raw consolidated tape (trades_v1) is the arbiter. For a sample of tickers:
(A) POST-hole (2026-03-24, day_aggs HAS it): tape dollar-vol vs day_aggs vs minute -> who is right?
(B) HOLE (2026-02-17, day_aggs LACKS it): minute vs tape -> is the minute rebuild faithful for the hole?
Derive ET session from raw sip_timestamp (memory: ts_et is UTC-mislabeled). Read-only, ticker-filtered for speed."""
import duckdb

WH = "data/polygon_warehouse"
con = duckdb.connect()
con.execute("SET threads=4")

SAMPLE = ['GLXG', 'SOXS', 'PAVS', 'NVDA', 'ADTX', 'TZA', 'AAPL', 'TSLA', 'RGTI', 'SMCI']
tklist = "'" + "','".join(SAMPLE) + "'"


def tape_dollar_vol(month, day, date_lit):
    """sum(price*size) for regular session (09:30-16:00 ET) from raw tape, ET from sip_timestamp."""
    tv = f"{WH}/trades_v1_parquet/year=2026/month={month}/**/*.parquet".replace("\\", "/")
    q = f"""
      SELECT ticker, sum(price*size) AS dvol, count(*) n
      FROM read_parquet('{tv}', union_by_name=true)
      WHERE ticker IN ({tklist})
        AND (sip_timestamp/1e9) IS NOT NULL
        AND CAST(to_timestamp(sip_timestamp/1e9) AT TIME ZONE 'America/New_York' AS DATE) = DATE '{date_lit}'
        AND EXTRACT(hour FROM (to_timestamp(sip_timestamp/1e9) AT TIME ZONE 'America/New_York')) BETWEEN 9 AND 15
      GROUP BY ticker"""
    return {r[0]: (r[1], r[2]) for r in con.execute(q).fetchall()}


def minute_dollar_vol(month, day):
    mg = f"{WH}/minute_aggs/year=2026/month={month:02d}/**/*.parquet".replace("\\", "/")
    q = f"""SELECT ticker, sum(volume*close) dvol, sum(volume) v FROM read_parquet('{mg}',union_by_name=true)
            WHERE ticker IN ({tklist}) AND day={day} GROUP BY ticker"""
    return {r[0]: (r[1], r[2]) for r in con.execute(q).fetchall()}


def day_dollar_vol(month, day):
    da = f"{WH}/day_aggs/year=2026/**/*.parquet".replace("\\", "/")
    q = f"""SELECT ticker, volume*close dvol, volume v FROM read_parquet('{da}',union_by_name=true)
            WHERE ticker IN ({tklist}) AND month={month} AND day={day}"""
    return {r[0]: (r[1], r[2]) for r in con.execute(q).fetchall()}


print("=== (A) POST-HOLE 2026-03-24: tape (consolidated RTH) vs day_aggs vs minute ===")
print(f"{'ticker':8} {'tape_$vol':>14} {'day_aggs_$vol':>14} {'minute_$vol':>14}  {'day/tape':>8} {'min/tape':>8}")
tp = tape_dollar_vol("03", 24, "2026-03-24")
dm = day_dollar_vol(3, 24)
mm = minute_dollar_vol(3, 24)
for tk in SAMPLE:
    t = tp.get(tk, (None,))[0]
    d = dm.get(tk, (None,))[0]
    m = mm.get(tk, (None,))[0]
    if t and d and m:
        print(f"{tk:8} {t:14,.0f} {d:14,.0f} {m:14,.0f}  {d/t:8.2f} {m/t:8.2f}")
    else:
        print(f"{tk:8} tape={t} day={d} min={m}")

print("\n=== (B) HOLE 2026-02-17: minute vs tape (day_aggs absent) — is the rebuild faithful for the hole? ===")
print(f"{'ticker':8} {'tape_$vol':>14} {'minute_$vol':>14}  {'min/tape':>8}")
tp2 = tape_dollar_vol("02", 17, "2026-02-17")
mm2 = minute_dollar_vol(2, 17)
for tk in SAMPLE:
    t = tp2.get(tk, (None,))[0]
    m = mm2.get(tk, (None,))[0]
    if t and m:
        print(f"{tk:8} {t:14,.0f} {m:14,.0f}  {m/t:8.2f}")
    else:
        print(f"{tk:8} tape={t} min={m}")
con.close()
