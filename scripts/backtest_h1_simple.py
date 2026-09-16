"""H1 (long late-day) simplified using the aftermath catalog.

The full H1 sweep (entry-time × top-K) hung on per-config sim queries.
Simpler approach: use the aftermath_strat catalog which already has
intraday_pct (selection signal) and ret_open_close_d0 (the d0 return).

H1 rule: top-K per day by first-30-min momentum, enter at HH:MM ET, exit
at +20% / -10% / 15:55 ET.

Approximation: for each (date, ticker) in the catalog, the realized
ret_open_close_d0 is a proxy for "what happened intraday." For the
top-K-per-day picks we measure their avg ret_open_close_d0 as a *bound*
on H1 P&L — this assumes "enter at open, exit at close" which is what
H1 does without target/stop.

Then we compare K=1, 2, 3, 5 to see if narrow vs wide selection wins.
"""
from __future__ import annotations
import sys
from pathlib import Path
import duckdb

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"


def section(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def main():
    con = duckdb.connect()
    con.sql("SET memory_limit='6GB'")
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")

    aftermath = str(DERIVED / "aftermath_strat.parquet").replace("\\", "/")

    # Build picks: rank catalog rows per day by intraday_pct (proxy for first-30 momentum)
    # The catalog is already filtered to >= 30% intraday, dvol thresholds etc.
    section("STEP 1 - Rank per day by intraday_pct (proxy for first-30 momentum)")
    con.sql(f"""
        CREATE OR REPLACE TABLE picks AS
        WITH base AS (
            SELECT * FROM read_parquet('{aftermath}')
            WHERE ca_flag = 'clean' AND ret_open_close_d0 IS NOT NULL
              AND open BETWEEN 0.5 AND 50 AND volume > 100000
        )
        SELECT *,
               ROW_NUMBER() OVER (PARTITION BY d0 ORDER BY intraday_pct DESC) AS rk
        FROM base
    """)
    n = con.sql("SELECT COUNT(*) FROM picks").fetchone()[0]
    print(f"  picks: {n:,} (catalog rows with rank-per-day)")

    section("STEP 2 - H1 baseline by top-K (no entry-time constraint)")
    print(f"  {'K':>2} {'n':>6}  {'avg_d0':>9}  {'med_d0':>9}  {'win%':>6}  {'+20%hit':>8}  {'-10%hit':>8}")
    for k in [1, 2, 3, 5, 10]:
        rows = con.sql(f"""
            SELECT COUNT(*) AS n,
                   AVG(ret_open_close_d0) AS avg_d0,
                   MEDIAN(ret_open_close_d0) AS med_d0,
                   1.0 * SUM(CASE WHEN ret_open_close_d0 > 0 THEN 1 ELSE 0 END) / COUNT(*) AS win,
                   1.0 * SUM(CASE WHEN ret_open_close_d0 >= 0.20 THEN 1 ELSE 0 END) / COUNT(*) AS up20,
                   1.0 * SUM(CASE WHEN ret_open_close_d0 <= -0.10 THEN 1 ELSE 0 END) / COUNT(*) AS dn10
            FROM picks WHERE rk <= {k}
        """).fetchone()
        n, avg, med, win, up20, dn10 = rows
        print(f"  {k:>2} {n:>6,}  {avg*100:>+8.2f}%  {med*100:>+8.2f}%  {win*100:>5.1f}%  {up20*100:>7.1f}%  {dn10*100:>7.1f}%")

    section("STEP 3 - H1 + chronic-fader gate")
    print(f"  {'K':>2} {'n':>6}  {'avg_d0':>9}  {'win%':>6}  {'+20%hit':>8}")
    # Need to re-join to per_ticker_continuer_prior
    prior = str(DERIVED / "per_ticker_continuer_prior.parquet").replace("\\", "/")
    con.sql(f"""
        CREATE OR REPLACE TABLE picks_pri AS
        SELECT p.*, pp.smoothed_continuer_rate, pp.n_appearances AS p_n
        FROM picks p LEFT JOIN read_parquet('{prior}') pp ON p.ticker = pp.ticker
    """)
    for k in [1, 2, 3, 5, 10]:
        rows = con.sql(f"""
            WITH gated AS (
                SELECT *,
                       ROW_NUMBER() OVER (PARTITION BY d0 ORDER BY intraday_pct DESC) AS rk_gated
                FROM picks_pri
                WHERE (smoothed_continuer_rate IS NULL OR smoothed_continuer_rate >= 5.0)
            )
            SELECT COUNT(*) AS n,
                   AVG(ret_open_close_d0) AS avg_d0,
                   1.0 * SUM(CASE WHEN ret_open_close_d0 > 0 THEN 1 ELSE 0 END) / COUNT(*) AS win,
                   1.0 * SUM(CASE WHEN ret_open_close_d0 >= 0.20 THEN 1 ELSE 0 END) / COUNT(*) AS up20
            FROM gated WHERE rk_gated <= {k}
        """).fetchone()
        n, avg, win, up20 = rows
        if n == 0: continue
        print(f"  {k:>2} {n:>6,}  {avg*100:>+8.2f}%  {win*100:>5.1f}%  {up20*100:>7.1f}%")

    section("STEP 4 - Per-K MONTHLY stability (top-3 picks by intraday)")
    rows = con.sql("""
        SELECT date_trunc('month', d0) AS month,
               COUNT(*) AS n,
               ROUND(AVG(ret_open_close_d0)*100, 2) AS avg_pct,
               ROUND(100.0 * SUM(CASE WHEN ret_open_close_d0 > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win
        FROM picks WHERE rk <= 3
        GROUP BY 1 ORDER BY 1 DESC LIMIT 28
    """).fetchall()
    print(f"  {'month':<8} {'n':>4} {'avg':>8} {'win':>6}")
    for r in rows:
        print(f"  {str(r[0])[:7]:<8} {r[1]:>4} {r[2]:>+7.2f}% {r[3]:>5.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
