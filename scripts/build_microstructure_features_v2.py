"""Faster per-month chunked microstructure feature builder.

The single-shot SQL in build_microstructure_features.py becomes intractable
on 64+ days of trades_v1 (~115 GB parquet). DuckDB's hive partition pruning
breaks down with `CAST(t.ts_et AS DATE) = k.d0` join because the cast
defeats predicate pushdown.

This version:
  1. Reads aftermath_strat keys once into memory (small, ~20K rows)
  2. For each (year, month) in trades_v1_parquet:
     a. Filter keys to those falling in this month
     b. Read only that month's parquet partitions (hive prune by year/month)
     c. Run the aggregation on that smaller chunk
     d. Append results
  3. Concat all monthly results into final parquet

This way DuckDB only scans relevant ticker/day partitions and never holds
the full 115 GB in memory at once.

OUTPUT:
  data/polygon_warehouse/derived/microstructure_features.parquet (overwrite)
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

import duckdb
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
TRADES_PARQUET = REPO / "data" / "polygon_warehouse" / "trades_v1_parquet"


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def build_for_month(con, month_glob: str, keys_for_month: pd.DataFrame) -> pd.DataFrame:
    """Build microstructure features for one (year, month) chunk."""
    con.sql("""
        CREATE OR REPLACE TABLE keys_month AS
        SELECT * FROM keys_month_pd
    """)
    return con.sql(f"""
        WITH first30 AS (
            SELECT
                t.ticker,
                CAST(t.ts_et AS DATE) AS d0,
                SUM(CASE WHEN list_contains(string_split(CAST(t.conditions AS VARCHAR), ','), '15')
                         THEN 1 ELSE 0 END) AS sweep_burst_count_first30,
                SUM(CASE WHEN t.exchange = 4 THEN t.size ELSE 0 END) * 1.0 /
                  NULLIF(SUM(t.size), 0) AS dark_pool_pct_first30,
                SUM(CASE WHEN t.size >= 10000 THEN t.size ELSE 0 END) * 1.0 /
                  NULLIF(SUM(t.size), 0) AS large_print_pct_first30,
                COUNT(*) FILTER (WHERE t.size < 100) * 1.0 /
                  NULLIF(COUNT(*), 0) AS odd_lot_pct_first30,
                SUM(CASE WHEN NOT list_contains(string_split(CAST(t.conditions AS VARCHAR), ','), '6')
                          AND NOT list_contains(string_split(CAST(t.conditions AS VARCHAR), ','), '7')
                          AND NOT list_contains(string_split(CAST(t.conditions AS VARCHAR), ','), '13')
                         THEN t.price * t.size ELSE 0 END) /
                  NULLIF(SUM(CASE WHEN NOT list_contains(string_split(CAST(t.conditions AS VARCHAR), ','), '6')
                                    AND NOT list_contains(string_split(CAST(t.conditions AS VARCHAR), ','), '7')
                                    AND NOT list_contains(string_split(CAST(t.conditions AS VARCHAR), ','), '13')
                                  THEN t.size ELSE 0 END), 0) AS true_vwap_first30,
                QUANTILE_CONT(t.size, 0.90) AS print_size_p90_first30,
                COUNT(*) AS n_trades_first30
            FROM read_parquet('{month_glob}', hive_partitioning=true) t
            INNER JOIN keys_month k
                ON t.ticker = k.ticker AND CAST(t.ts_et AS DATE) = k.d0
            WHERE (EXTRACT(hour FROM t.ts_et) = 9 AND EXTRACT(minute FROM t.ts_et) >= 30)
               OR (EXTRACT(hour FROM t.ts_et) = 10 AND EXTRACT(minute FROM t.ts_et) = 0)
            GROUP BY t.ticker, CAST(t.ts_et AS DATE)
        ),
        full_rth AS (
            SELECT
                t.ticker,
                CAST(t.ts_et AS DATE) AS d0,
                SUM(CASE WHEN list_contains(string_split(CAST(t.conditions AS VARCHAR), ','), '15')
                         THEN 1 ELSE 0 END) AS sweep_burst_count_full,
                SUM(CASE WHEN t.exchange = 4 THEN t.size ELSE 0 END) * 1.0 /
                  NULLIF(SUM(t.size), 0) AS dark_pool_pct_full,
                SUM(CASE WHEN t.size >= 10000 THEN t.size ELSE 0 END) * 1.0 /
                  NULLIF(SUM(t.size), 0) AS large_print_pct_full,
                COUNT(*) AS n_trades_full
            FROM read_parquet('{month_glob}', hive_partitioning=true) t
            INNER JOIN keys_month k
                ON t.ticker = k.ticker AND CAST(t.ts_et AS DATE) = k.d0
            WHERE EXTRACT(hour FROM t.ts_et) BETWEEN 9 AND 15
              AND NOT (EXTRACT(hour FROM t.ts_et) = 9 AND EXTRACT(minute FROM t.ts_et) < 30)
            GROUP BY t.ticker, CAST(t.ts_et AS DATE)
        )
        SELECT f.*,
               COALESCE(g.sweep_burst_count_full, 0) AS sweep_burst_count_full,
               COALESCE(g.dark_pool_pct_full, 0)     AS dark_pool_pct_full,
               COALESCE(g.large_print_pct_full, 0)   AS large_print_pct_full,
               COALESCE(g.n_trades_full, 0)           AS n_trades_full,
               f.sweep_burst_count_first30 * 1.0 / 30.0 AS sweep_burst_rate_first30,
               f.sweep_burst_count_first30 * 1.0 /
                   NULLIF(GREATEST(f.dark_pool_pct_first30, 0.001), 0) AS iso_to_dark_ratio
        FROM first30 f
        LEFT JOIN full_rth g ON f.ticker = g.ticker AND f.d0 = g.d0
    """).df()


def main():
    section("STEP 1 - Load aftermath keys")
    aftermath = (DERIVED / "aftermath_strat.parquet").as_posix()
    con = duckdb.connect()
    con.sql("SET memory_limit='10GB'")
    con.sql("SET threads=8")
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")
    keys = con.sql(f"""
        SELECT ticker, CAST(d0 AS DATE) AS d0
        FROM read_parquet('{aftermath}')
        WHERE ca_flag = 'clean' AND ret_t5 IS NOT NULL
          AND open BETWEEN 0.5 AND 50 AND volume > 100000
    """).df()
    keys["d0"] = pd.to_datetime(keys["d0"])
    keys["year"] = keys["d0"].dt.year
    keys["month"] = keys["d0"].dt.month
    print(f"  {len(keys):,} keys; range: {keys['d0'].min().date()} -> {keys['d0'].max().date()}")

    section("STEP 2 - Discover available trades_v1 (year, month) partitions")
    available = []
    for year_dir in sorted(TRADES_PARQUET.glob("year=*")):
        year = int(year_dir.name.split("=")[1])
        for month_dir in sorted(year_dir.glob("month=*")):
            month = int(month_dir.name.split("=")[1])
            available.append((year, month, str(year_dir / month_dir.name)))
    print(f"  {len(available)} (year, month) partitions: {[(y, m) for y, m, _ in available]}")

    section("STEP 3 - Build features per (year, month)")
    all_results: list[pd.DataFrame] = []
    for year, month, dir_path in available:
        keys_month = keys[(keys["year"] == year) & (keys["month"] == month)].copy()
        if len(keys_month) == 0:
            print(f"  ({year}, {month}): no aftermath keys — skip")
            continue
        keys_month_kept = keys_month[["ticker", "d0"]].drop_duplicates().reset_index(drop=True)
        # Register the keys for this month
        con.register("keys_month_pd", keys_month_kept)
        month_glob = f"{dir_path}/**/*.parquet"
        t0 = time.time()
        try:
            df_month = build_for_month(con, month_glob, keys_month_kept)
        except Exception as e:
            print(f"  ({year}, {month}): ERROR {e}")
            continue
        elapsed = time.time() - t0
        print(f"  ({year}, {month}): {len(df_month):>3} rows in {elapsed:>5.1f}s "
              f"(of {len(keys_month_kept):>3} keys)")
        all_results.append(df_month)
        con.unregister("keys_month_pd")

    section("STEP 4 - Concat + persist")
    if not all_results:
        print("  no results to write!")
        return 1
    final = pd.concat(all_results, ignore_index=True)
    print(f"  total rows: {len(final):,}")
    print(f"  unique d0 dates: {final['d0'].nunique()}")
    print(f"  unique tickers:  {final['ticker'].nunique()}")
    print(f"  d0 range: {final['d0'].min()} -> {final['d0'].max()}")

    out = DERIVED / "microstructure_features.parquet"
    final.to_parquet(out, compression="zstd")
    print(f"  Wrote {out} ({out.stat().st_size/1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
