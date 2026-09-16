"""Re-run doc 89 H1 (long late-day) on full Polygon universe.

Doc 89 H1 finding: ONLY entry at 13:00 ET top-3 worked (+0.45%/trade,
+50% compound on 88 days). 15 of 16 (entry × top-K) configs lost money.
This was the suspicious "narrow edge" finding.

Re-test on 576 days × full universe:
  - Per day, rank candidates by first_30_max_return
  - Pick top-K (sweep K=1,2,3,5)
  - Enter LONG at the bar at HH:MM (sweep entry times)
  - Exit at +20% / -10% / 15:55 ET time-stop
  - Measure per-trade P&L

If H1 was real edge, the optimal entry should be stable across 576 days
and at multiple K. If it was noise, no config should be cleanly positive.
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
    con.sql("SET memory_limit='6GB'")
    con.sql("SET threads=8")
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")

    section("STEP 1 - RTH view + per-(ticker, date) bar index")
    con.sql(f"""
        CREATE OR REPLACE VIEW rth AS
        SELECT ticker, ts_et, open, high, low, close, volume,
               CAST(ts_et AS DATE) AS d
        FROM read_parquet('{minute_glob}', hive_partitioning=true)
        WHERE EXTRACT(hour FROM ts_et) BETWEEN 9 AND 15
          AND NOT (EXTRACT(hour FROM ts_et) = 9 AND EXTRACT(minute FROM ts_et) < 30)
    """)
    print("  rth view defined")

    section("STEP 2 - Build first-30-min picks (top-K candidates per day)")
    con.sql("""
        CREATE OR REPLACE TABLE picks AS
        WITH numbered AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker, d ORDER BY ts_et) AS bar_idx
            FROM rth
        ),
        per_day AS (
            SELECT ticker, d,
                   MAX(CASE WHEN bar_idx <= 30 THEN high END) AS first30_high,
                   FIRST(open ORDER BY ts_et)
                       FILTER (WHERE bar_idx = 1) AS rth_open,
                   SUM(volume) AS total_vol
            FROM numbered
            GROUP BY 1, 2
        ),
        scored AS (
            SELECT ticker, d, first30_high, rth_open, total_vol,
                   (first30_high - rth_open) / rth_open AS first30_max_ret,
                   ROW_NUMBER() OVER (PARTITION BY d
                                       ORDER BY (first30_high - rth_open) / rth_open DESC) AS rk
            FROM per_day
            WHERE rth_open > 0
              AND first30_high IS NOT NULL
              AND total_vol > 100000
              AND rth_open BETWEEN 0.5 AND 50
        )
        SELECT * FROM scored WHERE rk <= 5
    """)
    n_picks = con.sql("SELECT COUNT(*) FROM picks").fetchone()[0]
    print(f"  picks (top-5/day): {n_picks:,}")

    section("STEP 3 - Sweep entry times × top-K, simulate +20%/-10%/EOD")
    print(f"  {'entry_time':<6} {'K':>2} {'n':>5} {'avg_t':>8} {'med_t':>8} {'win%':>6} {'tgt%':>6} {'stp%':>6}")

    # Time options: 09:30 (open), 10:30, 11:30, 12:30, 13:00, 13:30, 14:00
    times = [(9, 30), (10, 30), (11, 30), (12, 30), (13, 0), (13, 30), (14, 0), (15, 0)]

    for hh, mm in times:
        for k in [1, 2, 3, 5]:
            # For each pick where rk<=k:
            # - entry_open = open of first bar at >= HH:MM
            # - target = entry * 1.20, stop = entry * 0.90
            # - simulate to find first hit or 15:55 close
            con.sql(f"""
                CREATE OR REPLACE TABLE sim AS
                WITH p AS (SELECT * FROM picks WHERE rk <= {k}),
                entry_bar AS (
                    SELECT p.ticker, p.d,
                           MIN(r.ts_et) AS entry_ts
                    FROM p JOIN rth r ON p.ticker = r.ticker AND p.d = r.d
                    WHERE EXTRACT(hour FROM r.ts_et) > {hh}
                       OR (EXTRACT(hour FROM r.ts_et) = {hh}
                           AND EXTRACT(minute FROM r.ts_et) >= {mm})
                    GROUP BY 1, 2
                ),
                with_entry AS (
                    SELECT p.ticker, p.d, eb.entry_ts, r.open AS entry_px
                    FROM p JOIN entry_bar eb ON p.ticker = eb.ticker AND p.d = eb.d
                          JOIN rth r ON p.ticker = r.ticker AND p.d = r.d AND r.ts_et = eb.entry_ts
                ),
                joined AS (
                    SELECT we.ticker, we.d, we.entry_ts, we.entry_px,
                           r.ts_et, r.high, r.low, r.close
                    FROM with_entry we JOIN rth r
                      ON we.ticker = r.ticker AND we.d = r.d
                     AND r.ts_et >= we.entry_ts
                ),
                first_target AS (
                    SELECT ticker, d, MIN(ts_et) AS target_ts
                    FROM joined WHERE high >= entry_px * 1.20
                    GROUP BY 1, 2
                ),
                first_stop AS (
                    SELECT ticker, d, MIN(ts_et) AS stop_ts
                    FROM joined WHERE low <= entry_px * 0.90
                    GROUP BY 1, 2
                ),
                eod AS (
                    SELECT ticker, d, LAST(close ORDER BY ts_et) AS eod_close,
                           ANY_VALUE(entry_px) AS entry_px
                    FROM joined GROUP BY 1, 2
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
                         ELSE (e.eod_close - e.entry_px) / e.entry_px
                       END AS pnl
                FROM eod e
                LEFT JOIN first_target t ON e.ticker=t.ticker AND e.d=t.d
                LEFT JOIN first_stop s   ON e.ticker=s.ticker AND e.d=s.d
            """)
            row = con.sql("""
                SELECT COUNT(*),
                       AVG(pnl),
                       MEDIAN(pnl),
                       1.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*),
                       1.0 * SUM(CASE WHEN exit_reason='target' THEN 1 ELSE 0 END) / COUNT(*),
                       1.0 * SUM(CASE WHEN exit_reason='stop'   THEN 1 ELSE 0 END) / COUNT(*)
                FROM sim
            """).fetchone()
            n, avg, med, win, tgt, stp = row
            if n == 0:
                continue
            label = f"{hh:02d}{mm:02d}"
            print(f"  {label:<6} {k:>2} {n:>5,} {avg*100:>+7.2f}% {med*100:>+7.2f}% {win*100:>5.1f}% {tgt*100:>5.1f}% {stp*100:>5.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
