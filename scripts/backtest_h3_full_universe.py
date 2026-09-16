"""Re-run doc 89 H3 (short-the-morning-ripper) on full Polygon universe.

doc 89 H3: top-K of first-30-min momentum (filtered to first_30 ≥ 30%),
short at the 10:00 ET bar's open, target -20% / stop +10%.

We approximate this from minute_aggs without true tick-level execution:
  - "first_30_max" = MAX(high) over the first 30 RTH bars
  - "10am entry price" = the 31st RTH bar's open
  - For each (date), pick top-K by first_30_max where first_30_max >= 30%
  - Compute short P&L using minute bars from entry forward:
      target hit if any subsequent low <= entry * 0.80
      stop hit if any subsequent high >= entry * 1.10
      otherwise close-to-entry pct at EOD

doc 89 sample (88 days): H3 top-1 min30 was +2.82%/trade, win 56.7%, +168% compound
                           - but DIED in second half of sample
With 576 days we test if the regime-dependence was just sample size.
"""
from __future__ import annotations
import sys
from pathlib import Path
import duckdb

REPO = Path(__file__).resolve().parents[1]
WAREHOUSE = REPO / "data" / "polygon_warehouse"


def section(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def main():
    minute_glob = str(WAREHOUSE / "minute_aggs" / "**" / "*.parquet").replace("\\", "/")
    con = duckdb.connect()

    section("STEP 1 - Define RTH view (lazy; predicate-pushdown)")
    con.sql(f"""
        CREATE OR REPLACE VIEW rth AS
        SELECT ticker, ts_et, open, high, low, close, volume,
               CAST(ts_et AS DATE) AS d
        FROM read_parquet('{minute_glob}', hive_partitioning=true)
        WHERE EXTRACT(hour FROM ts_et) BETWEEN 9 AND 15
          AND NOT (EXTRACT(hour FROM ts_et) = 9 AND EXTRACT(minute FROM ts_et) < 30)
    """)
    # Tune memory limit and temp directory (Windows .tmp default doesn't exist)
    con.sql("SET memory_limit='6GB'")
    con.sql("SET threads=8")
    tmp_dir = (REPO / ".duckdb_tmp").as_posix()
    con.sql(f"SET temp_directory='{tmp_dir}'")
    print(f"  RTH view defined; memory_limit=6GB; threads=8; temp_dir={tmp_dir}")

    section("STEP 2 - Compute per-(ticker, date) first-30 max + 10am entry price")
    con.sql("""
        CREATE OR REPLACE TABLE first30 AS
        WITH numbered AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker, d ORDER BY ts_et) AS bar_idx
            FROM rth
        ),
        f30 AS (
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
        )
        SELECT *,
               (first30_high - rth_open) / rth_open AS first30_max_ret
        FROM f30
        WHERE rth_open IS NOT NULL AND entry_open IS NOT NULL
          AND rth_open > 0
    """)
    n_setups = con.sql("SELECT COUNT(*) FROM first30").fetchone()[0]
    print(f"  Built first30: {n_setups:,} (ticker, date) setups")

    section("STEP 3 - Filter to short candidates (first_30 >= 30%)")
    n_filt = con.sql("""
        SELECT COUNT(*) FROM first30
        WHERE first30_max_ret >= 0.30
          AND total_vol > 100000
          AND rth_open BETWEEN 0.5 AND 50
    """).fetchone()[0]
    print(f"  Eligible short setups: {n_filt:,}")

    section("STEP 4 - Take top-1 per day by first_30")
    con.sql("""
        CREATE OR REPLACE TABLE picks AS
        WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY d ORDER BY first30_max_ret DESC) AS rk
            FROM first30
            WHERE first30_max_ret >= 0.30
              AND total_vol > 100000
              AND rth_open BETWEEN 0.5 AND 50
        )
        SELECT * FROM ranked WHERE rk = 1
    """)
    n_picks = con.sql("SELECT COUNT(*) FROM picks").fetchone()[0]
    print(f"  Top-1 picks per day: {n_picks:,}")

    section("STEP 5 - Simulate short trade per pick (target -20%, stop +10%)")
    # For each pick: scan bars after entry_ts on same day
    # Determine first hit of target ($entry * 0.80) or stop ($entry * 1.10)
    # If neither: close pct = (entry - last_close) / entry
    con.sql("""
        CREATE OR REPLACE TABLE trades AS
        WITH joined AS (
            SELECT p.ticker, p.d, p.entry_ts, p.entry_open AS entry_px,
                   r.ts_et, r.high, r.low, r.close
            FROM picks p
            JOIN rth r ON p.ticker = r.ticker AND p.d = r.d
            WHERE r.ts_et >= p.entry_ts
        ),
        first_target AS (
            SELECT ticker, d, MIN(ts_et) AS target_ts, ANY_VALUE(entry_px) AS entry_px
            FROM joined
            WHERE low <= entry_px * 0.80
            GROUP BY 1, 2
        ),
        first_stop AS (
            SELECT ticker, d, MIN(ts_et) AS stop_ts, ANY_VALUE(entry_px) AS entry_px
            FROM joined
            WHERE high >= entry_px * 1.10
            GROUP BY 1, 2
        ),
        eod AS (
            SELECT ticker, d, LAST(close ORDER BY ts_et) AS eod_close,
                   ANY_VALUE(entry_px) AS entry_px
            FROM joined
            GROUP BY 1, 2
        )
        SELECT e.ticker, e.d, e.entry_px, e.eod_close,
               t.target_ts, s.stop_ts,
               CASE
                 WHEN s.stop_ts IS NOT NULL AND
                      (t.target_ts IS NULL OR s.stop_ts <= t.target_ts) THEN 'stop'
                 WHEN t.target_ts IS NOT NULL THEN 'target'
                 ELSE 'eod'
               END AS exit_reason,
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

    section("STEP 6 - HEADLINE H3 RESULTS (full 576-day universe)")
    rows = con.sql("""
        SELECT COUNT(*) n,
               ROUND(AVG(pnl)*100, 3) avg_pct,
               ROUND(MEDIAN(pnl)*100, 3) med_pct,
               ROUND(STDDEV(pnl)*100, 3) std_pct,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) n_wins,
               ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 2) win_rate,
               SUM(CASE WHEN exit_reason='target' THEN 1 ELSE 0 END) n_target,
               SUM(CASE WHEN exit_reason='stop'   THEN 1 ELSE 0 END) n_stop,
               SUM(CASE WHEN exit_reason='eod'    THEN 1 ELSE 0 END) n_eod
        FROM trades
    """).fetchone()
    print(f"  n trades:          {rows[0]:,}")
    print(f"  avg per-trade:     {rows[1]:>+7.3f}%")
    print(f"  median per-trade:  {rows[2]:>+7.3f}%")
    print(f"  stddev per-trade:  {rows[3]:>7.3f}%")
    print(f"  win rate:          {rows[5]:>5.2f}%")
    print(f"  target hits:       {rows[6]:,}")
    print(f"  stop hits:         {rows[7]:,}")
    print(f"  eod exits:         {rows[8]:,}")

    section("STEP 7 - PER-MONTH (regime stability check)")
    print(f"  {'month':<8} {'n':>4} {'avg':>8} {'win':>6} {'cmpd':>9}")
    rows = con.sql("""
        SELECT date_trunc('month', d) AS month, COUNT(*) n,
               ROUND(AVG(pnl)*100, 2) avg_pct,
               ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) win,
               ROUND((EXP(SUM(LN(1 + pnl))) - 1) * 100, 2) cmpd
        FROM trades
        GROUP BY 1 ORDER BY 1 DESC LIMIT 30
    """).fetchall()
    for r in rows:
        print(f"  {str(r[0])[:7]:<8} {r[1]:>4,} {r[2]:>+7.2f}% {r[3]:>5.1f}% {r[4]:>+8.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
