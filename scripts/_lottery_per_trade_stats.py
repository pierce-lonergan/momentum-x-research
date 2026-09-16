"""Per-trade stats for the 5 lottery variants from
backtest_lottery_full_universe.py. Avoids the daily-portfolio compounding
inflation by reporting per-trade statistics directly.
"""
import duckdb
from pathlib import Path
con = duckdb.connect()
con.sql("SET memory_limit='6GB'")
con.sql(f"SET temp_directory='{Path(__file__).resolve().parent.parent.as_posix()}/.duckdb_tmp'")

con.sql("""
    CREATE TABLE universe AS
    WITH base AS (
        SELECT * FROM read_parquet('data/polygon_warehouse/derived/aftermath_strat.parquet')
    ),
    enriched AS (
        SELECT *,
               ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY d0) AS appearance_idx,
               COUNT(*) OVER (PARTITION BY ticker
                               ORDER BY d0
                               RANGE BETWEEN INTERVAL 7 DAY PRECEDING
                                       AND INTERVAL 1 DAY PRECEDING) AS prior_7d_count
        FROM base
        WHERE ca_flag = 'clean' AND ret_t5 IS NOT NULL
          AND open BETWEEN 0.5 AND 50 AND volume > 100000
    )
    SELECT e.*, p.smoothed_continuer_rate, p.n_appearances AS p_n
    FROM enriched e
    LEFT JOIN read_parquet('data/polygon_warehouse/derived/per_ticker_continuer_prior.parquet') p
      ON e.ticker = p.ticker
""")

variants = [
    ("V1 baseline (every catalog row)", "TRUE"),
    ("V2 fresh + low-dvol + mod-intra",
     "COALESCE(prior_7d_count, 0) = 0 AND dvol_d0 < 5e6 AND intraday_pct BETWEEN 0.30 AND 0.60"),
    ("V3 chronic-fader gate (cont>=5% OR no history) + p7d<=1",
     "(smoothed_continuer_rate IS NULL OR smoothed_continuer_rate >= 5.0) "
     "AND COALESCE(prior_7d_count, 0) <= 1 "
     "AND dvol_d0 BETWEEN 1e5 AND 100e6 "
     "AND intraday_pct BETWEEN 0.30 AND 1.00"),
    ("V4 high-continuer tickers only (rate>=7%, n>=5, fresh)",
     "smoothed_continuer_rate >= 7.0 AND p_n >= 5 AND COALESCE(prior_7d_count, 0) = 0"),
    ("V5 truly-fresh (no catalog history at all)",
     "smoothed_continuer_rate IS NULL "
     "AND dvol_d0 BETWEEN 1e5 AND 100e6 "
     "AND intraday_pct BETWEEN 0.30 AND 1.50"),
    ("V6 V3 + intraday_pct in [0.30, 0.60] only",
     "(smoothed_continuer_rate IS NULL OR smoothed_continuer_rate >= 5.0) "
     "AND COALESCE(prior_7d_count, 0) = 0 "
     "AND dvol_d0 BETWEEN 1e5 AND 50e6 "
     "AND intraday_pct BETWEEN 0.30 AND 0.60"),
    ("V7 V3 + same-day open-close in [+10%, +30%]",
     "(smoothed_continuer_rate IS NULL OR smoothed_continuer_rate >= 5.0) "
     "AND COALESCE(prior_7d_count, 0) <= 1 "
     "AND dvol_d0 BETWEEN 1e5 AND 100e6 "
     "AND intraday_pct BETWEEN 0.30 AND 1.00 "
     "AND ret_open_close_d0 BETWEEN 0.10 AND 0.30"),
]

print(f"\n{'='*90}")
print(f"  {'variant':<55} {'n_tr':>6} {'avg_t5':>9} {'med_t5':>9} {'win%':>6} {'sharpe':>7}")
print(f"{'='*90}")
for label, where in variants:
    rows = con.sql(f"""
        SELECT COUNT(*) AS n,
               AVG(ret_t5) AS avg_t5,
               MEDIAN(ret_t5) AS med_t5,
               STDDEV(ret_t5) AS std_t5,
               1.0 * SUM(CASE WHEN ret_t5 > 0 THEN 1 ELSE 0 END) / COUNT(*) AS win,
               1.0 * SUM(CASE WHEN ret_t5 >= 0.10 THEN 1 ELSE 0 END) / COUNT(*) AS cont_rate,
               1.0 * SUM(CASE WHEN ret_t5 <= -0.10 THEN 1 ELSE 0 END) / COUNT(*) AS fade_rate
        FROM universe WHERE {where}
    """).fetchone()
    n, avg, med, std, win, cr, fr = rows
    if n == 0:
        print(f"  {label:<55} {n:>6}  (no rows)")
        continue
    sharpe = (avg / std * (252 / 5) ** 0.5) if std and std > 0 else 0  # 5-day return scaled to annual
    print(f"  {label:<55} {n:>6,} {avg*100:>+7.2f}% {med*100:>+7.2f}% {win*100:>5.1f}% {sharpe:>+6.2f}")

# Plus continuer/fader rates per variant
print()
print(f"  {'variant':<55} {'cont%':>6} {'fade%':>6}")
print(f"{'-'*90}")
for label, where in variants:
    rows = con.sql(f"""
        SELECT 1.0 * SUM(CASE WHEN ret_t5 >= 0.10 THEN 1 ELSE 0 END) / COUNT(*) AS cr,
               1.0 * SUM(CASE WHEN ret_t5 <= -0.10 THEN 1 ELSE 0 END) / COUNT(*) AS fr
        FROM universe WHERE {where}
    """).fetchone()
    if rows[0] is None: continue
    print(f"  {label:<55} {rows[0]*100:>5.1f}% {rows[1]*100:>5.1f}%")
