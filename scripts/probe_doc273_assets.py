"""doc 273 phase-1 probe: corpus size + trades-parquet timestamp semantics (read-only)."""
import duckdb, glob

con = duckdb.connect()
PANEL = "data/research/exit_labels_cross_regime.parquet"

df = con.execute(f"""SELECT count(*) n, count(DISTINCT ticker||session_date) td,
    count(DISTINCT session_date) ndays, min(session_date) lo, max(session_date) hi
    FROM read_parquet('{PANEL}')""").df()
print("PANEL:", df.to_dict("records"))
cols = con.execute(f"SELECT * FROM read_parquet('{PANEL}') LIMIT 1").df().columns.tolist()
print("PANEL COLS:", cols)
inw = con.execute(f"""SELECT count(DISTINCT ticker||session_date) td, count(DISTINCT session_date) ndays
    FROM read_parquet('{PANEL}') WHERE session_date BETWEEN '2025-08-01' AND '2026-04-30'""").df()
print("IN-WINDOW ticker-days:", inw.to_dict("records"))
perday = con.execute(f"""SELECT session_date, count(DISTINCT ticker) k FROM read_parquet('{PANEL}')
    WHERE session_date BETWEEN '2025-08-01' AND '2026-04-30' GROUP BY 1""").df()
print("names/day: median", perday.k.median(), "p10", perday.k.quantile(.1), "p90", perday.k.quantile(.9))

parts = glob.glob("data/polygon_warehouse/trades_v1_parquet/year=2025/month=09/day=*/ticker=*/data_0.parquet")
print("sample partitions found:", len(parts))
p = parts[0].replace("\\", "/")
print("probing:", p)
sch = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{p}')").df()
print(sch[["column_name", "column_type"]].to_string())
t = con.execute(f"SELECT * FROM read_parquet('{p}') ORDER BY ts_et LIMIT 3").df()
print(t.to_string())
hrs = con.execute(f"SELECT extract(hour FROM ts_et) h, count(*) c FROM read_parquet('{p}') GROUP BY 1 ORDER BY 1").df()
print("ts_et hour histogram:", dict(zip(hrs.h.astype(int), hrs.c.astype(int))))
# if a raw sip timestamp column exists, compare its ET-derived hour to ts_et
for cand in ("sip_timestamp", "participant_timestamp", "ts", "t"):
    if cand in sch.column_name.values:
        cmp = con.execute(f"""SELECT extract(hour FROM ts_et) h_et,
            extract(hour FROM (to_timestamp({cand}/1e9) AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York')) h_sip,
            count(*) c FROM read_parquet('{p}') GROUP BY 1,2 ORDER BY 3 DESC LIMIT 5""").df()
        print(f"{cand} vs ts_et hour crosstab:\n", cmp.to_string())
