"""Build the aftermath catalog for the 20,795 high-mover rows.

The +30% intraday is an ENTRY filter, not an outcome. To know which rows
are tradeable opportunities we need T+1, T+5, T+20 close-to-close returns.

This script:
  1. Loads minute_aggs warehouse, computes per-(ticker, date) daily close.
  2. Joins forward via window LEAD(close, N).
  3. Joins back to high_movers_catalog.
  4. Computes T+1, T+5, T+20 returns from the catalog day's close.
  5. Cross-references reference/splits to flag corporate-action contamination.
  6. Stratifies into continuers (T+5 > +10%) / faders (T+5 < -10%) / choppers.
  7. Outputs feature distributions per stratum.

Output: data/polygon_warehouse/derived/aftermath_catalog.parquet
        data/polygon_warehouse/derived/aftermath_summary.json
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

import duckdb
import polars as pl

REPO = Path(__file__).resolve().parents[1]
WAREHOUSE = REPO / "data" / "polygon_warehouse"
DERIVED = WAREHOUSE / "derived"
DERIVED.mkdir(parents=True, exist_ok=True)


def section(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def main():
    minute_glob = str(WAREHOUSE / "minute_aggs" / "**" / "*.parquet").replace("\\", "/")
    catalog_path = str(DERIVED / "high_movers_catalog.parquet").replace("\\", "/")

    if not Path(catalog_path).exists():
        print("ERROR: high_movers_catalog.parquet not found. Run polygon_high_mover_catalog.py first.")
        return 1

    con = duckdb.connect()

    section("STEP 1 — Build per-(ticker, date) daily close from minute_aggs")
    t0 = time.perf_counter()
    con.sql(f"""
        CREATE OR REPLACE TABLE daily AS
        SELECT ticker,
               CAST(ts_et AS DATE) AS d,
               FIRST(open ORDER BY ts_et) AS open,
               MAX(high) AS high,
               MIN(low) AS low,
               LAST(close ORDER BY ts_et) AS close,
               SUM(volume) AS volume,
               SUM(transactions) AS transactions
        FROM read_parquet('{minute_glob}', hive_partitioning=true)
        GROUP BY 1, 2
    """)
    n_daily = con.sql("SELECT COUNT(*) FROM daily").fetchone()[0]
    print(f"  Built daily: {n_daily:,} rows in {time.perf_counter()-t0:.1f}s")

    section("STEP 2 — Add forward-looking close via window LEAD")
    t0 = time.perf_counter()
    con.sql("""
        CREATE OR REPLACE TABLE daily_with_fwd AS
        SELECT *,
               LEAD(close, 1)  OVER (PARTITION BY ticker ORDER BY d) AS close_t1,
               LEAD(close, 5)  OVER (PARTITION BY ticker ORDER BY d) AS close_t5,
               LEAD(close, 20) OVER (PARTITION BY ticker ORDER BY d) AS close_t20,
               LEAD(d,     1)  OVER (PARTITION BY ticker ORDER BY d) AS d_t1,
               LEAD(d,     5)  OVER (PARTITION BY ticker ORDER BY d) AS d_t5,
               LEAD(d,    20)  OVER (PARTITION BY ticker ORDER BY d) AS d_t20,
               LEAD(volume, 1) OVER (PARTITION BY ticker ORDER BY d) AS volume_t1
        FROM daily
    """)
    print(f"  Forward-looking joins computed in {time.perf_counter()-t0:.1f}s")

    section("STEP 3 - Join catalog <- daily_with_fwd, compute returns")
    t0 = time.perf_counter()
    con.sql(f"""
        CREATE OR REPLACE TABLE aftermath AS
        SELECT c.date AS d0, c.ticker, c.open, c.high, c.low, c.close AS close_t0,
               c.volume, c.intraday_pct,
               d.close_t1, d.close_t5, d.close_t20,
               d.d_t1, d.d_t5, d.d_t20,
               d.volume_t1,
               -- Same-day open-to-close
               (c.close - c.open) / c.open AS ret_open_close_d0,
               -- Forward returns from d0 close
               CASE WHEN d.close_t1 IS NOT NULL AND c.close > 0
                    THEN (d.close_t1 - c.close) / c.close END AS ret_t1,
               CASE WHEN d.close_t5 IS NOT NULL AND c.close > 0
                    THEN (d.close_t5 - c.close) / c.close END AS ret_t5,
               CASE WHEN d.close_t20 IS NOT NULL AND c.close > 0
                    THEN (d.close_t20 - c.close) / c.close END AS ret_t20,
               -- Volume decay (same-day vs t+1)
               CASE WHEN c.volume > 0 AND d.volume_t1 IS NOT NULL
                    THEN d.volume_t1 / c.volume END AS volume_ratio_t1,
               -- Dollar volume on d0
               c.volume * (c.high + c.low + c.close) / 3 AS dvol_d0
        FROM read_parquet('{catalog_path}') c
        LEFT JOIN daily_with_fwd d
          ON c.ticker = d.ticker AND c.date = d.d
    """)
    n_after = con.sql("SELECT COUNT(*) FROM aftermath").fetchone()[0]
    print(f"  Built aftermath: {n_after:,} rows in {time.perf_counter()-t0:.1f}s")

    section("STEP 4 — Flag corporate-action contamination heuristics")
    t0 = time.perf_counter()
    # Heuristic CA flag: >50% jump in ret_t1 OR ret_t1 ≤ -50% AND volume_ratio_t1 < 0.3
    # Plus catastrophic intraday > 500% (likely reverse-split day).
    con.sql("""
        CREATE OR REPLACE TABLE aftermath_flagged AS
        SELECT *,
               CASE
                 WHEN intraday_pct >= 5.0 THEN 'extreme_>500pct'
                 WHEN ABS(ret_t1) >= 0.50 AND volume_ratio_t1 < 0.3 THEN 'likely_split_or_MA'
                 WHEN intraday_pct >= 3.0 THEN 'extreme_300_500pct'
                 ELSE 'clean'
               END AS ca_flag
        FROM aftermath
    """)
    print(f"  Heuristic CA flag in {time.perf_counter()-t0:.1f}s")
    print("  Distribution:")
    for row in con.sql("SELECT ca_flag, COUNT(*) AS n FROM aftermath_flagged GROUP BY 1 ORDER BY 2 DESC").fetchall():
        print(f"    {row[0]:25s}  {row[1]:>6,}")

    section("STEP 5 — Stratify (clean rows only): continuers / faders / choppers")
    t0 = time.perf_counter()
    con.sql("""
        CREATE OR REPLACE TABLE aftermath_strat AS
        SELECT *,
               CASE
                 WHEN ret_t5 IS NULL THEN 'no_t5_data'
                 WHEN ret_t5 >= 0.10 THEN 'continuer'
                 WHEN ret_t5 <= -0.10 THEN 'fader'
                 ELSE 'chopper'
               END AS stratum_t5,
               CASE
                 WHEN ret_t1 IS NULL THEN 'no_t1_data'
                 WHEN ret_t1 >= 0.05 THEN 'continuer'
                 WHEN ret_t1 <= -0.05 THEN 'fader'
                 ELSE 'chopper'
               END AS stratum_t1
        FROM aftermath_flagged
        WHERE ca_flag = 'clean'
    """)
    n_clean = con.sql("SELECT COUNT(*) FROM aftermath_strat").fetchone()[0]
    print(f"  Clean rows (ca_flag='clean'): {n_clean:,}")

    print("\n  T+1 stratum distribution:")
    for row in con.sql("""
        SELECT stratum_t1, COUNT(*) n,
               ROUND(AVG(ret_t1)*100, 2) avg_pct,
               ROUND(MEDIAN(ret_t1)*100, 2) med_pct
        FROM aftermath_strat
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchall():
        avg = row[2] if row[2] is not None else 0.0
        med = row[3] if row[3] is not None else 0.0
        print(f"    {row[0]:15s}  n={row[1]:>6,}  avg={avg:>+6.2f}%  med={med:>+6.2f}%")

    print("\n  T+5 stratum distribution (THE BIG ONE):")
    for row in con.sql("""
        SELECT stratum_t5, COUNT(*) n,
               ROUND(AVG(ret_t5)*100, 2) avg_pct,
               ROUND(MEDIAN(ret_t5)*100, 2) med_pct
        FROM aftermath_strat
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchall():
        avg = row[2] if row[2] is not None else 0.0
        med = row[3] if row[3] is not None else 0.0
        print(f"    {row[0]:15s}  n={row[1]:>6,}  avg={avg:>+6.2f}%  med={med:>+6.2f}%")

    print("\n  T+20 stratum distribution:")
    for row in con.sql("""
        SELECT
          CASE WHEN ret_t20 IS NULL THEN 'no_t20'
               WHEN ret_t20 >= 0.20 THEN 'continuer'
               WHEN ret_t20 <= -0.20 THEN 'fader'
               ELSE 'chopper' END AS s,
          COUNT(*) n,
          ROUND(AVG(ret_t20)*100, 2) avg_pct,
          ROUND(MEDIAN(ret_t20)*100, 2) med_pct
        FROM aftermath_strat
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchall():
        avg = row[2] if row[2] is not None else 0.0
        med = row[3] if row[3] is not None else 0.0
        print(f"    {row[0]:15s}  n={row[1]:>6,}  avg={avg:>+6.2f}%  med={med:>+6.2f}%")

    section("STEP 6 — Headline numbers")
    t0 = time.perf_counter()
    overall = con.sql("""
        SELECT COUNT(*) n,
               COUNT(ret_t1) n_t1, COUNT(ret_t5) n_t5, COUNT(ret_t20) n_t20,
               ROUND(AVG(ret_t1)*100, 3) avg_t1,
               ROUND(AVG(ret_t5)*100, 3) avg_t5,
               ROUND(AVG(ret_t20)*100, 3) avg_t20,
               ROUND(MEDIAN(ret_t1)*100, 3) med_t1,
               ROUND(MEDIAN(ret_t5)*100, 3) med_t5,
               ROUND(MEDIAN(ret_t20)*100, 3) med_t20,
               ROUND(SUM(CASE WHEN ret_t5 >= 0.10 THEN 1 ELSE 0 END) * 100.0 / COUNT(ret_t5), 2) pct_continuer_t5,
               ROUND(SUM(CASE WHEN ret_t5 <= -0.10 THEN 1 ELSE 0 END) * 100.0 / COUNT(ret_t5), 2) pct_fader_t5
        FROM aftermath_strat
    """).fetchone()
    cols = ["n", "n_t1", "n_t5", "n_t20",
            "avg_t1", "avg_t5", "avg_t20",
            "med_t1", "med_t5", "med_t20",
            "pct_continuer_t5", "pct_fader_t5"]
    headline = dict(zip(cols, overall))
    for k, v in headline.items():
        print(f"  {k:20s}  {v}")

    # Persist
    section("STEP 7 — Persist")
    out_aftermath = DERIVED / "aftermath_catalog.parquet"
    con.sql(f"COPY (SELECT * FROM aftermath_flagged) TO '{out_aftermath}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    print(f"  Wrote {out_aftermath} ({out_aftermath.stat().st_size/1e6:.2f} MB)")

    out_strat = DERIVED / "aftermath_strat.parquet"
    con.sql(f"COPY (SELECT * FROM aftermath_strat) TO '{out_strat}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    print(f"  Wrote {out_strat} ({out_strat.stat().st_size/1e6:.2f} MB)")

    summary = {
        "n_catalog_rows": n_after,
        "n_clean_rows": n_clean,
        "ca_flag_distribution": {row[0]: row[1] for row in con.sql(
            "SELECT ca_flag, COUNT(*) FROM aftermath_flagged GROUP BY 1").fetchall()},
        "headline": headline,
        "elapsed_total_s": time.perf_counter() - t0,
    }
    (DERIVED / "aftermath_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"  Wrote {DERIVED / 'aftermath_summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
