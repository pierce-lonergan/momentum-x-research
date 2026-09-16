"""Sample queries against the Polygon Parquet warehouse.

Demonstrates 4 queries that prove the warehouse is operational:
  Q1: Single-ticker history (predicate pushdown — should be <100ms)
  Q2: Top-50 movers for a specific day across the entire US universe
  Q3: Cross-sectional breadth — what % of the universe gapped >30% per day
  Q4: Recurrent winners — tickers most often in the top-100 movers
"""
from __future__ import annotations
import sys, time
from pathlib import Path
import duckdb

REPO = Path(__file__).resolve().parents[1]
WAREHOUSE = REPO / "data" / "polygon_warehouse"


def section(title):
    print(f"\n{'='*70}\n{title}\n{'='*70}")


def main():
    day_aggs_glob = str(WAREHOUSE / "day_aggs" / "**" / "*.parquet").replace("\\", "/")
    print(f"Querying: {day_aggs_glob}")
    con = duckdb.connect()

    # Q1: Single-ticker history
    section("Q1 — AAPL last 30 trading days (predicate pushdown)")
    t0 = time.perf_counter()
    df = con.sql(f"""
        SELECT ts_et, open, high, low, close, volume
        FROM read_parquet('{day_aggs_glob}', hive_partitioning=true)
        WHERE ticker = 'AAPL'
        ORDER BY ts_et DESC LIMIT 10
    """).fetchdf()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(df.to_string(index=False))
    print(f"  -> {elapsed_ms:.1f}ms")

    # Q2: Top-50 movers on the most recent date
    section("Q2 — Top-50 movers on the latest date")
    t0 = time.perf_counter()
    df = con.sql(f"""
        WITH d AS (SELECT MAX(CAST(ts_et AS DATE)) AS max_d FROM read_parquet('{day_aggs_glob}', hive_partitioning=true))
        SELECT ticker,
               ROUND(open, 2) AS open,
               ROUND(close, 2) AS close,
               ROUND((close - open) / open * 100, 2) AS pct_intraday,
               CAST(volume AS BIGINT) AS volume,
               transactions
        FROM read_parquet('{day_aggs_glob}', hive_partitioning=true), d
        WHERE CAST(ts_et AS DATE) = d.max_d
          AND open > 0.5 AND open < 50
          AND volume > 100000
        ORDER BY pct_intraday DESC
        LIMIT 25
    """).fetchdf()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(df.to_string(index=False))
    print(f"  -> {elapsed_ms:.1f}ms")

    # Q3: Daily breadth — share of universe with >30% intraday gain
    section("Q3 — Daily breadth: pct of universe with >30% intraday move")
    t0 = time.perf_counter()
    df = con.sql(f"""
        SELECT CAST(ts_et AS DATE) AS d,
               COUNT(*) AS n_total,
               SUM(CASE WHEN open>0 AND (close - open)/open >= 0.30 THEN 1 ELSE 0 END) AS n_30pct_up,
               SUM(CASE WHEN open>0 AND (close - open)/open <= -0.30 THEN 1 ELSE 0 END) AS n_30pct_down,
               ROUND(100.0 * SUM(CASE WHEN open>0 AND (close - open)/open >= 0.30 THEN 1 ELSE 0 END) / COUNT(*), 3) AS pct_huge_up
        FROM read_parquet('{day_aggs_glob}', hive_partitioning=true)
        WHERE volume > 50000
        GROUP BY 1
        ORDER BY 1 DESC
        LIMIT 10
    """).fetchdf()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(df.to_string(index=False))
    print(f"  -> {elapsed_ms:.1f}ms")

    # Q4: Tickers most often in top-100 movers (recurrent winners)
    section("Q4 — Tickers most often in top-100 daily movers (last 30 days)")
    t0 = time.perf_counter()
    df = con.sql(f"""
        WITH ranked AS (
            SELECT ticker, ts_et,
                   (close - open) / open AS pct,
                   ROW_NUMBER() OVER (PARTITION BY CAST(ts_et AS DATE)
                                       ORDER BY (close - open) / open DESC) AS rn
            FROM read_parquet('{day_aggs_glob}', hive_partitioning=true)
            WHERE open > 0.5 AND open < 50 AND volume > 100000
        )
        SELECT ticker,
               COUNT(*) AS n_appearances_in_top100,
               ROUND(AVG(pct) * 100, 2) AS avg_pct_when_listed,
               ROUND(MAX(pct) * 100, 2) AS max_pct
        FROM ranked WHERE rn <= 100
        GROUP BY 1 HAVING COUNT(*) >= 3
        ORDER BY n_appearances_in_top100 DESC
        LIMIT 25
    """).fetchdf()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(df.to_string(index=False))
    print(f"  -> {elapsed_ms:.1f}ms")

    # Summary
    section("WAREHOUSE STATS")
    df = con.sql(f"""
        SELECT COUNT(*) AS total_rows,
               COUNT(DISTINCT ticker) AS unique_tickers,
               MIN(CAST(ts_et AS DATE)) AS first_date,
               MAX(CAST(ts_et AS DATE)) AS last_date
        FROM read_parquet('{day_aggs_glob}', hive_partitioning=true)
    """).fetchdf()
    print(df.to_string(index=False))


if __name__ == "__main__":
    sys.exit(main() or 0)
