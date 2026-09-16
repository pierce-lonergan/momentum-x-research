"""doc278: confirm the day_aggs Q1 rebuild is feasible from minute_aggs (read-only). Checks:
(1) day_aggs 2026 schema + exact missing session dates (the hole);
(2) minute_aggs 2026-Q1 schema, date coverage, ticker density — can it reconstruct day_aggs?
Uses duckdb with tight per-partition globs to stay fast."""
import duckdb
import os

WH = "data/polygon_warehouse"
con = duckdb.connect()
con.execute("SET threads=4")


def cols(glob):
    return con.execute(f"SELECT * FROM read_parquet('{glob}', union_by_name=true) LIMIT 0").df().columns.tolist()


# --- day_aggs ---
da = f"{WH}/day_aggs/year=2026/**/*.parquet".replace("\\", "/")
dcols = cols(da)
print("day_aggs 2026 cols:", dcols)
datec = next((c for c in dcols if c.lower() in ("date", "session_date", "day", "t", "window_start")), dcols[0])
r = con.execute(f"SELECT min({datec}), max({datec}), count(DISTINCT {datec}) FROM read_parquet('{da}')").fetchone()
print(f"day_aggs 2026 {datec}: min={r[0]} max={r[1]} distinct_sessions={r[2]}")
tickc = next((c for c in dcols if c.lower() in ("ticker", "symbol", "t", "sym")), None)
if tickc:
    rr = con.execute(f"SELECT count(DISTINCT {tickc}) FROM read_parquet('{da}')").fetchone()
    print(f"day_aggs 2026 distinct {tickc}: {rr[0]}")

# --- minute_aggs Q1 ---
print("\n--- minute_aggs 2026 Q1 ---")
for m in ["01", "02", "03"]:
    mg = f"{WH}/minute_aggs/year=2026/month={m}/**/*.parquet".replace("\\", "/")
    try:
        mcols = cols(mg)
        tsc = next((c for c in mcols if c.lower() in ("window_start", "t", "timestamp", "ts", "sip_timestamp", "start")), None)
        tkc = next((c for c in mcols if c.lower() in ("ticker", "symbol", "sym", "t")), None)
        if m == "01":
            print("minute_aggs cols:", mcols)
        # count rows + distinct days + distinct tickers (cheap aggregates)
        q = f"SELECT count(*) FROM read_parquet('{mg}', union_by_name=true)"
        n = con.execute(q).fetchone()[0]
        print(f"  month={m}: rows={n:,} tsc={tsc} tkc={tkc}")
    except Exception as e:
        print(f"  month={m}: ERR {str(e)[:120]}")
con.close()
