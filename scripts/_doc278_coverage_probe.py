"""doc278 feasibility probe: does raw 2026-Q1 data (minute/trade level) sit on disk to aggregate into
day_aggs, or is the rebuild blocked on a Polygon fetch? This ONE fact decides build-vs-chase.
Read-only. Reports date coverage per warehouse table for 2026."""
import glob
import os

WH = "data/polygon_warehouse"


def probe_parquet_dir(name):
    d = os.path.join(WH, name)
    if not os.path.isdir(d):
        print(f"  {name}: DIR ABSENT ({d})")
        return
    # find parquet files, look at 2026 partitions
    files = glob.glob(os.path.join(d, "**", "*.parquet"), recursive=True)
    y2026 = [f for f in files if "2026" in f or "year=2026" in f]
    print(f"  {name}: {len(files)} parquet files total, {len(y2026)} touching 2026")
    for f in sorted(y2026)[:6]:
        print(f"    {f.replace(WH+os.sep,'')}  ({os.path.getsize(f)//1024} KB)")
    return files


print("=== warehouse dirs ===")
for x in sorted(os.listdir(WH)) if os.path.isdir(WH) else []:
    p = os.path.join(WH, x)
    kind = "dir" if os.path.isdir(p) else "file"
    print(f"  {x} ({kind})")

print("\n=== 2026 coverage by table ===")
for t in ["day_aggs", "minute_aggs", "trades_v1_parquet", "trades_v1", "quotes_v1", "derived", "reference"]:
    probe_parquet_dir(t)

# try duckdb for precise date ranges
print("\n=== precise date ranges (duckdb if available) ===")
try:
    import duckdb
    con = duckdb.connect()
    for t, datecol in [("day_aggs", None), ("minute_aggs", None), ("trades_v1_parquet", None)]:
        d = os.path.join(WH, t)
        if not os.path.isdir(d):
            continue
        globp = os.path.join(d, "**", "*.parquet").replace("\\", "/")
        try:
            cols = con.execute(f"SELECT * FROM read_parquet('{globp}', union_by_name=true) LIMIT 0").df().columns.tolist()
            dc = next((c for c in cols if c.lower() in ("date", "session_date", "day", "ts", "window_start", "t")), None)
            print(f"  {t}: cols={cols[:8]}{'...' if len(cols)>8 else ''}")
            if dc:
                r = con.execute(f"SELECT min({dc}), max({dc}), count(*) FROM read_parquet('{globp}', union_by_name=true) "
                                f"WHERE CAST({dc} AS VARCHAR) LIKE '2026%'").fetchone()
                print(f"    2026 {dc}: min={r[0]} max={r[1]} rows={r[2]}")
        except Exception as e:
            print(f"  {t}: duckdb err {str(e)[:100]}")
except ImportError:
    print("  duckdb not available; relying on file listing above")
