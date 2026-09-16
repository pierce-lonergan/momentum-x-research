"""Build per-(ticker, d0) intraday-path parquet for TCN training.

For each row in aftermath_strat (the labeled gap-up universe), extract the
first 30 RTH minutes (09:30-10:00 ET) from minute_aggs and emit as a flat
30-bar path: 30 * 6 = 180 features per sample.

Why 30 RTH minutes?
  - Compass artifact §B.1: minute layer wants 30 min - 4 hr context
  - Lou/Polk/Skouras 2019: intraday fade is the dominant pattern; the first
    30 minutes is where the gap either holds or breaks
  - Small enough to fit in memory after flattening (20K * 180 * 8 = 30 MB)

Output:
  data/polygon_warehouse/derived/intraday_paths_30min.parquet

Schema:
  d0           DATE
  ticker       VARCHAR
  bar_idx      INT (0-29, 30 bars)
  open, high, low, close   DOUBLE  (normalized: pct change from RTH open)
  volume       DOUBLE  (normalized: log-bar-volume / log-day-volume_d-1)
  transactions DOUBLE  (raw count)

Run:
  python scripts/build_intraday_paths.py [--limit-tickers N]
"""
from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MINUTE_GLOB = (REPO / "data" / "polygon_warehouse" / "minute_aggs" / "**" / "*.parquet").as_posix()
AFTERMATH = (DERIVED / "aftermath_strat.parquet").as_posix()


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-tickers", type=int, default=0,
                        help="If >0, sample this many (ticker, d0) rows for a fast smoke test")
    parser.add_argument("--bars", type=int, default=30,
                        help="Number of RTH minute bars per path (default 30)")
    args = parser.parse_args()

    section("STEP 1 - Setup")
    con = duckdb.connect()
    con.sql("SET memory_limit='8GB'")
    con.sql("SET threads=8")
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")
    print(f"  minute_aggs glob: {MINUTE_GLOB}")
    print(f"  aftermath:        {AFTERMATH}")

    section("STEP 2 - Load labeled (ticker, d0) keys")
    limit_clause = f"LIMIT {args.limit_tickers}" if args.limit_tickers > 0 else ""
    keys = con.sql(f"""
        SELECT ticker, d0
        FROM read_parquet('{AFTERMATH}')
        WHERE ca_flag = 'clean' AND ret_t5 IS NOT NULL
          AND open BETWEEN 0.5 AND 50 AND volume > 100000
        ORDER BY d0
        {limit_clause}
    """).df()
    print(f"  {len(keys):,} (ticker, d0) keys to extract")

    section("STEP 3 - Extract first-30-minute RTH paths")
    t0 = time.perf_counter()
    con.sql(f"""
        CREATE OR REPLACE TABLE keys AS
        SELECT * FROM read_parquet('{AFTERMATH}')
        WHERE ca_flag = 'clean' AND ret_t5 IS NOT NULL
          AND open BETWEEN 0.5 AND 50 AND volume > 100000
        {limit_clause}
    """)
    # Pull RTH minute bars 09:30-09:30+args.bars-min for each (ticker, d0)
    # Use bar index ROW_NUMBER over (ticker, d) ORDER BY ts_et
    # Limit to first args.bars bars per (ticker, d)
    con.sql(f"""
        CREATE OR REPLACE TABLE rth_bars AS
        WITH ma AS (
            SELECT ticker, ts_et, open, high, low, close, volume, transactions,
                   CAST(ts_et AS DATE) AS d
            FROM read_parquet('{MINUTE_GLOB}', hive_partitioning=true)
            WHERE EXTRACT(hour FROM ts_et) = 9
              AND EXTRACT(minute FROM ts_et) >= 30
              OR (EXTRACT(hour FROM ts_et) = 10
                  AND EXTRACT(minute FROM ts_et) < 30)
        ),
        joined AS (
            SELECT m.ticker, m.d, m.ts_et, m.open, m.high, m.low, m.close,
                   m.volume, m.transactions,
                   ROW_NUMBER() OVER (PARTITION BY m.ticker, m.d ORDER BY m.ts_et) - 1 AS bar_idx
            FROM ma m
            INNER JOIN keys k ON m.ticker = k.ticker AND m.d = k.d0
        )
        SELECT * FROM joined WHERE bar_idx < {args.bars}
    """)
    n_bars = con.sql("SELECT COUNT(*) FROM rth_bars").fetchone()[0]
    n_uniq = con.sql("SELECT COUNT(DISTINCT (ticker, d)) FROM rth_bars").fetchone()[0]
    print(f"  extracted {n_bars:,} bars across {n_uniq:,} (ticker, d) keys "
          f"in {time.perf_counter()-t0:.1f}s")
    print(f"  coverage: {n_uniq/len(keys)*100:.1f}% of requested keys "
          f"({len(keys)-n_uniq:,} missing — likely halted/early-close)")

    section("STEP 4 - Normalize to RTH-open-relative + per-day volume profile")
    # Normalize price to (price / RTH_open) - 1; volume to log-volume-z-score within day
    con.sql("""
        CREATE OR REPLACE TABLE normalized AS
        WITH agg AS (
            SELECT ticker, d,
                   FIRST(open ORDER BY bar_idx) AS rth_open,
                   STDDEV(volume) AS std_vol,
                   AVG(volume) AS mean_vol
            FROM rth_bars
            GROUP BY ticker, d
        )
        SELECT b.ticker, b.d AS d0, b.bar_idx,
               (b.open - a.rth_open) / a.rth_open AS open_rel,
               (b.high - a.rth_open) / a.rth_open AS high_rel,
               (b.low  - a.rth_open) / a.rth_open AS low_rel,
               (b.close - a.rth_open) / a.rth_open AS close_rel,
               (b.volume - a.mean_vol) / NULLIF(a.std_vol, 0) AS vol_z,
               LN(b.transactions + 1) AS log_trans
        FROM rth_bars b
        JOIN agg a ON b.ticker = a.ticker AND b.d = a.d
    """)
    print(f"  normalized: {con.sql('SELECT COUNT(*) FROM normalized').fetchone()[0]:,} bars")

    section("STEP 5 - Persist parquet")
    out = DERIVED / "intraday_paths_30min.parquet"
    con.sql(f"""
        COPY normalized TO '{out.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    print(f"  Wrote {out}")
    sz_mb = out.stat().st_size / 1e6
    print(f"  size: {sz_mb:.1f} MB")

    return 0


if __name__ == "__main__":
    sys.exit(main())
