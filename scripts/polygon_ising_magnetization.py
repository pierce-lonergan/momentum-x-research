"""Ising magnetization regime detector (doc 95 §9 wild idea #9).

Per the physics analogy: each ticker is a 'spin' — +1 if rallying that day,
-1 if declining. Daily magnetization = mean spin = market breadth.

Phase transitions in magnetization correspond to regime shifts. Doc 89
buried H3 ('short the morning ripper') as 'regime-dependent and dying'
based on 88 days. Doc 99 re-ran on 576 days and found H3 is CYCLIC
(18 of 28 months positive, 10 negative).

Question: does Ising magnetization PREDICT which months are positive
for H3? If yes, we have a regime overlay for live deployment.

Two computations:
  1. Daily magnetization: (n_up - n_down) / (n_up + n_down) over the
     whole tradable universe (filtered to price 0.5-50, vol >100k).
  2. High-mover breadth: count of ≥30% intraday movers per day.

Outputs:
  data/polygon_warehouse/derived/ising_daily.parquet
  data/polygon_warehouse/derived/ising_h3_overlay.parquet (joins to H3 trades)
  Plot summary printed to console.
"""
from __future__ import annotations
import sys
from pathlib import Path
import duckdb

REPO = Path(__file__).resolve().parents[1]
WAREHOUSE = REPO / "data" / "polygon_warehouse"
DERIVED = WAREHOUSE / "derived"
DERIVED.mkdir(parents=True, exist_ok=True)


def section(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def main():
    day_glob = str(WAREHOUSE / "day_aggs" / "**" / "*.parquet").replace("\\", "/")
    minute_glob = str(WAREHOUSE / "minute_aggs" / "**" / "*.parquet").replace("\\", "/")
    catalog_path = str(DERIVED / "high_movers_catalog.parquet").replace("\\", "/")
    h3_runner_path = REPO / "logs" / "h3_full_universe.log"

    con = duckdb.connect()
    con.sql("SET memory_limit='6GB'")
    con.sql("SET threads=8")
    tmp_dir = (REPO / ".duckdb_tmp").as_posix()
    con.sql(f"SET temp_directory='{tmp_dir}'")

    section("STEP 1 - Daily Ising magnetization from minute_aggs")
    # We have minute_aggs; roll up to daily per ticker, then compute breadth
    print("  Computing per-(ticker, date) daily summary ...")
    con.sql(f"""
        CREATE OR REPLACE TABLE per_day AS
        SELECT ticker,
               CAST(ts_et AS DATE) AS d,
               FIRST(open ORDER BY ts_et) AS open,
               LAST(close ORDER BY ts_et) AS close,
               SUM(volume) AS volume
        FROM read_parquet('{minute_glob}', hive_partitioning=true)
        GROUP BY 1, 2
    """)
    n_per_day = con.sql("SELECT COUNT(*) FROM per_day").fetchone()[0]
    print(f"  per_day: {n_per_day:,} rows")

    section("STEP 2 - Compute daily Ising magnetization")
    con.sql("""
        CREATE OR REPLACE TABLE ising_daily AS
        SELECT d,
               COUNT(*) AS n_total,
               -- Spin: +1 if up >0%, -1 if down >0%
               SUM(CASE WHEN open > 0 AND close > open THEN 1 ELSE 0 END) AS n_up,
               SUM(CASE WHEN open > 0 AND close < open THEN 1 ELSE 0 END) AS n_down,
               -- Magnetization: (up - down) / total in [-1, 1]
               1.0 * (SUM(CASE WHEN open > 0 AND close > open THEN 1 ELSE 0 END) -
                      SUM(CASE WHEN open > 0 AND close < open THEN 1 ELSE 0 END)) /
                      NULLIF(COUNT(*), 0) AS magnetization,
               -- High-mover breadth: count with intraday >=+30%
               SUM(CASE WHEN open > 0 AND (close - open)/open >= 0.30 THEN 1 ELSE 0 END) AS n_huge_up,
               SUM(CASE WHEN open > 0 AND (close - open)/open <= -0.30 THEN 1 ELSE 0 END) AS n_huge_down,
               -- Volatility proxy: avg abs return
               AVG(CASE WHEN open > 0 THEN ABS(close - open)/open END) AS avg_abs_ret
        FROM per_day
        WHERE volume > 50000
          AND open BETWEEN 0.5 AND 1000
        GROUP BY 1
        ORDER BY 1
    """)
    n_dates = con.sql("SELECT COUNT(*) FROM ising_daily").fetchone()[0]
    print(f"  ising_daily: {n_dates} dates")

    print("\n  Magnetization stats:")
    rows = con.sql("""
        SELECT
          COUNT(*) AS n,
          ROUND(AVG(magnetization), 4) AS avg_mag,
          ROUND(STDDEV(magnetization), 4) AS std_mag,
          ROUND(MIN(magnetization), 4) AS min_mag,
          ROUND(MAX(magnetization), 4) AS max_mag,
          AVG(n_huge_up) AS avg_huge_up,
          AVG(n_huge_down) AS avg_huge_down
        FROM ising_daily
    """).fetchone()
    print(f"  n={rows[0]} avg={rows[1]:+.4f} std={rows[2]:.4f} min={rows[3]:+.4f} max={rows[4]:+.4f}")
    print(f"  avg_huge_up/day={rows[5]:.1f}  avg_huge_down/day={rows[6]:.1f}")

    section("STEP 3 - Monthly aggregate magnetization")
    print(f"  {'month':<8} {'n_d':>4}  {'avg_mag':>10}  {'avg_huge_up':>12}  {'avg_huge_down':>14}")
    rows = con.sql("""
        SELECT date_trunc('month', d) AS month,
               COUNT(*) AS n_d,
               ROUND(AVG(magnetization), 4) AS avg_mag,
               ROUND(AVG(n_huge_up), 1) AS avg_up,
               ROUND(AVG(n_huge_down), 1) AS avg_down
        FROM ising_daily
        GROUP BY 1 ORDER BY 1 DESC LIMIT 30
    """).fetchall()
    for r in rows:
        print(f"  {str(r[0])[:7]:<8} {r[1]:>4}  {r[2]:>+9.4f}  {r[3]:>11.1f}  {r[4]:>13.1f}")

    # Persist
    out_ising = DERIVED / "ising_daily.parquet"
    con.sql(f"COPY (SELECT * FROM ising_daily) TO '{out_ising}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    print(f"\n  Wrote {out_ising} ({out_ising.stat().st_size/1e3:.1f} KB)")

    section("STEP 4 - Cross-reference with H3 monthly performance")
    # Build H3 monthly P&L from the backtest log we already have
    # Easier approach: re-run the H3 picks query and join
    print("  Re-computing H3 picks for cross-ref ...")
    con.sql(f"""
        CREATE OR REPLACE VIEW rth AS
        SELECT ticker, ts_et, open, high, low, close, volume,
               CAST(ts_et AS DATE) AS d
        FROM read_parquet('{minute_glob}', hive_partitioning=true)
        WHERE EXTRACT(hour FROM ts_et) BETWEEN 9 AND 15
          AND NOT (EXTRACT(hour FROM ts_et) = 9 AND EXTRACT(minute FROM ts_et) < 30)
    """)
    con.sql("""
        CREATE OR REPLACE TABLE first30 AS
        WITH numbered AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker, d ORDER BY ts_et) AS bar_idx
            FROM rth
        )
        SELECT ticker, d,
               MAX(CASE WHEN bar_idx <= 30 THEN high END) AS first30_high,
               FIRST(open ORDER BY ts_et)
                   FILTER (WHERE bar_idx = 1) AS rth_open,
               FIRST(open ORDER BY ts_et)
                   FILTER (WHERE bar_idx = 31) AS entry_open,
               FIRST(ts_et ORDER BY ts_et)
                   FILTER (WHERE bar_idx = 31) AS entry_ts,
               SUM(volume) AS total_vol
        FROM numbered
        GROUP BY 1, 2
    """)
    con.sql("""
        CREATE OR REPLACE TABLE h3_picks AS
        WITH ranked AS (
            SELECT *, (first30_high - rth_open)/rth_open AS first30_max_ret,
                   ROW_NUMBER() OVER (PARTITION BY d ORDER BY (first30_high - rth_open)/rth_open DESC) AS rk
            FROM first30
            WHERE rth_open > 0 AND first30_high IS NOT NULL
              AND (first30_high - rth_open)/rth_open >= 0.30
              AND total_vol > 100000
              AND rth_open BETWEEN 0.5 AND 50
        )
        SELECT * FROM ranked WHERE rk = 1
    """)
    n_picks = con.sql("SELECT COUNT(*) FROM h3_picks").fetchone()[0]
    print(f"  H3 picks: {n_picks}")

    print("  Joining each pick to its trade outcome ...")
    con.sql("""
        CREATE OR REPLACE TABLE h3_trades AS
        WITH joined AS (
            SELECT p.ticker, p.d, p.entry_ts, p.entry_open AS entry_px,
                   r.ts_et, r.high, r.low, r.close
            FROM h3_picks p
            JOIN rth r ON p.ticker = r.ticker AND p.d = r.d
            WHERE r.ts_et >= p.entry_ts
        ),
        first_target AS (
            SELECT ticker, d, MIN(ts_et) AS target_ts, ANY_VALUE(entry_px) AS entry_px
            FROM joined WHERE low <= entry_px * 0.80
            GROUP BY 1, 2
        ),
        first_stop AS (
            SELECT ticker, d, MIN(ts_et) AS stop_ts, ANY_VALUE(entry_px) AS entry_px
            FROM joined WHERE high >= entry_px * 1.10
            GROUP BY 1, 2
        ),
        eod AS (
            SELECT ticker, d, LAST(close ORDER BY ts_et) AS eod_close,
                   ANY_VALUE(entry_px) AS entry_px
            FROM joined GROUP BY 1, 2
        )
        SELECT e.d, e.ticker, e.entry_px, e.eod_close,
               t.target_ts, s.stop_ts,
               CASE
                 WHEN s.stop_ts IS NOT NULL AND
                      (t.target_ts IS NULL OR s.stop_ts <= t.target_ts) THEN -0.10
                 WHEN t.target_ts IS NOT NULL THEN 0.20
                 ELSE (e.entry_px - e.eod_close) / e.entry_px
               END AS pnl
        FROM eod e
        LEFT JOIN first_target t ON e.ticker=t.ticker AND e.d=t.d
        LEFT JOIN first_stop s   ON e.ticker=s.ticker AND e.d=s.d
    """)

    section("STEP 5 - H3 P&L vs Ising magnetization regime")
    print("  Joining H3 trades to ising_daily by date ...")
    con.sql("""
        CREATE OR REPLACE TABLE h3_x_ising AS
        SELECT t.d, t.ticker, t.pnl,
               i.magnetization, i.n_huge_up, i.n_huge_down, i.avg_abs_ret,
               -- 5-day rolling avg magnetization (regime context)
               AVG(i.magnetization) OVER (ORDER BY t.d ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS mag_5d
        FROM h3_trades t JOIN ising_daily i ON t.d = i.d
        ORDER BY t.d
    """)

    print("\n  H3 P&L by Ising magnetization tercile (full sample):")
    print(f"  {'mag_bucket':<14} {'n':>4}  {'avg_pnl':>9}  {'win':>6}  {'avg_mag':>8}")
    rows = con.sql("""
        WITH tercile AS (
            SELECT *,
                   NTILE(3) OVER (ORDER BY magnetization) AS mag_tile
            FROM h3_x_ising
        )
        SELECT CASE mag_tile WHEN 1 THEN 'A:lo (-)'
                              WHEN 2 THEN 'B:mid'
                              WHEN 3 THEN 'C:hi (+)' END AS bucket,
               COUNT(*) n,
               ROUND(AVG(pnl)*100, 2) avg_pct,
               ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) win_pct,
               ROUND(AVG(magnetization), 4) avg_mag
        FROM tercile GROUP BY 1 ORDER BY 1
    """).fetchall()
    for r in rows:
        print(f"  {r[0]:<14} {r[1]:>4} {r[2]:>+8.2f}% {r[3]:>5.1f}% {r[4]:>+8.4f}")

    print("\n  H3 P&L by 5-day rolling avg magnetization tercile:")
    rows = con.sql("""
        WITH tercile AS (
            SELECT *,
                   NTILE(3) OVER (ORDER BY mag_5d) AS mag_tile
            FROM h3_x_ising
            WHERE mag_5d IS NOT NULL
        )
        SELECT CASE mag_tile WHEN 1 THEN 'A:lo (bearish)'
                              WHEN 2 THEN 'B:mid'
                              WHEN 3 THEN 'C:hi (bullish)' END AS bucket,
               COUNT(*) n,
               ROUND(AVG(pnl)*100, 2) avg_pct,
               ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) win_pct,
               ROUND(AVG(mag_5d), 4) avg_mag
        FROM tercile GROUP BY 1 ORDER BY 1
    """).fetchall()
    print(f"  {'mag_bucket':<18} {'n':>4}  {'avg_pnl':>9}  {'win':>6}  {'avg_mag':>9}")
    for r in rows:
        print(f"  {r[0]:<18} {r[1]:>4} {r[2]:>+8.2f}% {r[3]:>5.1f}% {r[4]:>+8.4f}")

    print("\n  H3 P&L by n_huge_up tercile (breadth):")
    rows = con.sql("""
        WITH tercile AS (
            SELECT *,
                   NTILE(3) OVER (ORDER BY n_huge_up) AS bin
            FROM h3_x_ising
        )
        SELECT CASE bin WHEN 1 THEN 'A:lo_breadth (calm)'
                         WHEN 2 THEN 'B:mid_breadth'
                         WHEN 3 THEN 'C:hi_breadth (pump-rich)' END AS bucket,
               COUNT(*) n,
               ROUND(AVG(pnl)*100, 2) avg_pct,
               ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) win_pct,
               ROUND(AVG(n_huge_up), 1) avg_huge_up
        FROM tercile GROUP BY 1 ORDER BY 1
    """).fetchall()
    print(f"  {'bucket':<28} {'n':>4}  {'avg_pnl':>9}  {'win':>6}  {'avg_huge_up':>12}")
    for r in rows:
        print(f"  {r[0]:<28} {r[1]:>4} {r[2]:>+8.2f}% {r[3]:>5.1f}% {r[4]:>11.1f}")

    out_overlay = DERIVED / "ising_h3_overlay.parquet"
    con.sql(f"COPY (SELECT * FROM h3_x_ising) TO '{out_overlay}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    print(f"\n  Wrote {out_overlay} ({out_overlay.stat().st_size/1e3:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
