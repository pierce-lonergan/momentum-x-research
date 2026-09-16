"""Build microstructure features from Polygon trades_v1 (Phase 3).

This script READS the trade tape once it's been downloaded by
scripts/polygon_trades_v1_pull.py and produces per-(ticker, d0) features
that feed the v4 ensemble.

Per Compass artifact 2 §A.1, A.2:
  - SWEEP_BURST_RATE: count of intermarket-sweep trades (condition=15) per
    minute. Institution choosing speed over price-improvement; high-conviction
    continuation signal in microcap gap-ups.
  - DARK_POOL_PCT: (sum size where exchange=4 FINRA TRF) / total size.
    Dark prints aren't in real-time tape; high % = institutional positioning.
  - LARGE_PRINT_PCT: (sum size where size >= 10000) / total size. Block trades.
  - ODD_LOT_PCT: (count where condition=38, size <100) / count. Retail noise.
  - TRUE_VWAP: VWAP excluding conditions 6, 7, 13 (off-tape prints).

For each (ticker, d0) in aftermath_strat, computes:
  ticker, d0,
  sweep_burst_rate_first30,    -- # ISO trades in 9:30-10:00 ET
  sweep_burst_rate_full,        -- # ISO trades full RTH
  dark_pool_pct_first30,
  dark_pool_pct_full,
  large_print_pct_first30,
  odd_lot_pct_first30,
  true_vwap_first30,
  iso_to_dark_ratio,            -- compound: institutional aggression / accumulation
  print_size_p90,               -- 90th percentile trade size (skew indicator)

INPUT:
  data/polygon_warehouse/trades_v1_parquet/year=YYYY/month=MM/day=DD/ticker=TKR/*.parquet

OUTPUT:
  data/polygon_warehouse/derived/microstructure_features.parquet

USAGE:
  # First, download trades_v1 (if not done already):
  python scripts/polygon_trades_v1_pull.py --start 2024-01-01 --end 2026-04-30

  # Then build features:
  python scripts/build_microstructure_features.py
"""
from __future__ import annotations
import sys
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
TRADES_PARQUET = REPO / "data" / "polygon_warehouse" / "trades_v1_parquet"
TRADES_GLOB = (TRADES_PARQUET / "**" / "*.parquet").as_posix()


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def main():
    section("STEP 1 - Verify trades_v1 parquet exists")
    if not TRADES_PARQUET.exists() or not list(TRADES_PARQUET.rglob("*.parquet")):
        print(f"  ERROR: {TRADES_PARQUET} missing or empty.")
        print(f"  Run first: python scripts/polygon_trades_v1_pull.py --start <YYYY-MM-DD>")
        print(f"             (requires POLYGON_S3_KEY / POLYGON_S3_SECRET env vars)")
        return 1

    n_files = len(list(TRADES_PARQUET.rglob("*.parquet")))
    print(f"  found {n_files:,} parquet files")

    section("STEP 2 - Load aftermath keys")
    aftermath = (DERIVED / "aftermath_strat.parquet").as_posix()
    con = duckdb.connect()
    con.sql("SET memory_limit='8GB'")
    con.sql("SET threads=8")
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")
    con.sql(f"""
        CREATE OR REPLACE TABLE keys AS
        SELECT ticker, d0
        FROM read_parquet('{aftermath}')
        WHERE ca_flag = 'clean' AND ret_t5 IS NOT NULL
          AND open BETWEEN 0.5 AND 50 AND volume > 100000
    """)
    n_keys = con.sql("SELECT COUNT(*) FROM keys").fetchone()[0]
    print(f"  {n_keys:,} (ticker, d0) keys to enrich")

    section("STEP 3 - Build per-(ticker, d0) microstructure features")
    # Note: condition codes are stored as comma-separated strings in raw csv;
    # after conversion they may be a list or string. We'll handle both.
    # For 'first30' we use sip_timestamp range 9:30-10:00 ET; for 'full' all of RTH.
    print("  computing first-30-min features...")
    con.sql(f"""
        CREATE OR REPLACE TABLE micro_first30 AS
        SELECT
            t.ticker,
            CAST(t.ts_et AS DATE) AS d0,
            -- ISO sweep count: condition=15 (Intermarket Sweep)
            SUM(CASE WHEN list_contains(string_split(CAST(t.conditions AS VARCHAR), ','), '15')
                     THEN 1 ELSE 0 END) AS sweep_burst_count_first30,
            -- Dark-pool %: exchange = 4 (FINRA TRF)
            SUM(CASE WHEN t.exchange = 4 THEN t.size ELSE 0 END) * 1.0 /
              NULLIF(SUM(t.size), 0) AS dark_pool_pct_first30,
            -- Large-print %: size >= 10000 shares
            SUM(CASE WHEN t.size >= 10000 THEN t.size ELSE 0 END) * 1.0 /
              NULLIF(SUM(t.size), 0) AS large_print_pct_first30,
            -- Odd-lot count %: size < 100 shares
            COUNT(*) FILTER (WHERE t.size < 100) * 1.0 /
              NULLIF(COUNT(*), 0) AS odd_lot_pct_first30,
            -- True VWAP: exclude conditions 6 (cash sale), 7 (avg price), 13 (sold OOS)
            SUM(CASE WHEN NOT list_contains(string_split(CAST(t.conditions AS VARCHAR), ','), '6')
                       AND NOT list_contains(string_split(CAST(t.conditions AS VARCHAR), ','), '7')
                       AND NOT list_contains(string_split(CAST(t.conditions AS VARCHAR), ','), '13')
                     THEN t.price * t.size ELSE 0 END) /
              NULLIF(SUM(CASE WHEN NOT list_contains(string_split(CAST(t.conditions AS VARCHAR), ','), '6')
                                AND NOT list_contains(string_split(CAST(t.conditions AS VARCHAR), ','), '7')
                                AND NOT list_contains(string_split(CAST(t.conditions AS VARCHAR), ','), '13')
                              THEN t.size ELSE 0 END), 0) AS true_vwap_first30,
            -- 90th percentile trade size (large-trade skew)
            QUANTILE_CONT(t.size, 0.90) AS print_size_p90_first30,
            COUNT(*) AS n_trades_first30
        FROM read_parquet('{TRADES_GLOB}', hive_partitioning=true) t
        INNER JOIN keys k
            ON t.ticker = k.ticker AND CAST(t.ts_et AS DATE) = k.d0
        -- First 30 RTH min: 9:30-10:00 ET only
        WHERE (EXTRACT(hour FROM t.ts_et) = 9 AND EXTRACT(minute FROM t.ts_et) >= 30)
           OR (EXTRACT(hour FROM t.ts_et) = 10 AND EXTRACT(minute FROM t.ts_et) = 0)
        GROUP BY t.ticker, CAST(t.ts_et AS DATE)
    """)

    print("  computing full-RTH features...")
    con.sql(f"""
        CREATE OR REPLACE TABLE micro_full AS
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
        FROM read_parquet('{TRADES_GLOB}', hive_partitioning=true) t
        INNER JOIN keys k
            ON t.ticker = k.ticker AND CAST(t.ts_et AS DATE) = k.d0
        WHERE EXTRACT(hour FROM t.ts_et) BETWEEN 9 AND 15
          AND NOT (EXTRACT(hour FROM t.ts_et) = 9 AND EXTRACT(minute FROM t.ts_et) < 30)
        GROUP BY t.ticker, CAST(t.ts_et AS DATE)
    """)

    section("STEP 4 - Join + derive compound features")
    out = con.sql("""
        SELECT
            f.ticker, f.d0,
            f.sweep_burst_count_first30,
            f.sweep_burst_count_first30 * 1.0 / 30.0 AS sweep_burst_rate_first30,
            f.dark_pool_pct_first30,
            f.large_print_pct_first30,
            f.odd_lot_pct_first30,
            f.true_vwap_first30,
            f.print_size_p90_first30,
            f.n_trades_first30,
            COALESCE(g.sweep_burst_count_full, 0) AS sweep_burst_count_full,
            COALESCE(g.dark_pool_pct_full, 0)     AS dark_pool_pct_full,
            COALESCE(g.large_print_pct_full, 0)   AS large_print_pct_full,
            COALESCE(g.n_trades_full, 0)           AS n_trades_full,
            -- Compound: ISO bursts per dark-pool fraction (institutional aggression / accumulation)
            f.sweep_burst_count_first30 * 1.0 /
                NULLIF(GREATEST(f.dark_pool_pct_first30, 0.001), 0) AS iso_to_dark_ratio
        FROM micro_first30 f
        LEFT JOIN micro_full g ON f.ticker = g.ticker AND f.d0 = g.d0
    """).df()

    print(f"  built {len(out):,} (ticker, d0) feature rows")
    print(f"  coverage: {len(out)/n_keys*100:.1f}% of aftermath keys")

    section("STEP 5 - Persist")
    out_path = DERIVED / "microstructure_features.parquet"
    out.to_parquet(out_path, compression="zstd")
    print(f"  Wrote {out_path}")
    sz_mb = out_path.stat().st_size / 1e6
    print(f"  size: {sz_mb:.2f} MB")

    section("Sample rows")
    print(out.head(5).to_string())

    return 0


if __name__ == "__main__":
    sys.exit(main())
