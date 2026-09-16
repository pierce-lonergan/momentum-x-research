"""Re-run doc 90 lottery on the full Polygon universe (576 days × ~36 movers/day).

Doc 90 lottery: buy a unit at RTH open in every fresh watchlist ticker,
trail 15%, force-close at EOD. The 88-day backtest showed +51.9% compound
on the watchlist subset.

This re-run:
  - Universe = all (date, ticker) where intraday >=30% AND price 0.5-50
    AND volume >100k (the same gate as the high-mover catalog)
  - Per-day picks = top-K by EOD intraday_pct (proxy for what would have
    been on the watchlist)
  - Apply continuer-prior gate (chronic-fader filter from doc 99)
  - Compute T+5 portfolio return (since we don't have intraday tick data
    for every ticker, we approximate exit at EOD = first available metric)

Three lottery variants:
  V1: every catalog row, no filter, equal-weight
  V2: filtered to fresh + low-dvol (best 3D bucket from doc 99 §2.3)
  V3: filtered to per-ticker continuer rate >= 5% OR no history (chronic-fader gate)

Outputs:
  data/polygon_warehouse/derived/lottery_full_universe_summary.json
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
import duckdb

REPO = Path(__file__).resolve().parents[1]
WAREHOUSE = REPO / "data" / "polygon_warehouse"
DERIVED = WAREHOUSE / "derived"


def section(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def main():
    aftermath = str(DERIVED / "aftermath_strat.parquet").replace("\\", "/")
    prior = str(DERIVED / "per_ticker_continuer_prior.parquet").replace("\\", "/")

    con = duckdb.connect()
    con.sql("SET memory_limit='6GB'")
    tmp_dir = (REPO / ".duckdb_tmp").as_posix()
    con.sql(f"SET temp_directory='{tmp_dir}'")

    # Build a working table joining aftermath + prior + appearance_idx + prior_7d
    section("STEP 1 - Build enriched universe")
    con.sql(f"""
        CREATE OR REPLACE TABLE universe AS
        WITH base AS (
            SELECT * FROM read_parquet('{aftermath}')
        ),
        enriched AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY d0) AS appearance_idx,
                   COUNT(*) OVER (PARTITION BY ticker
                                   ORDER BY d0
                                   RANGE BETWEEN INTERVAL 7 DAY PRECEDING
                                           AND INTERVAL 1 DAY PRECEDING) AS prior_7d_count
            FROM base
            WHERE ca_flag = 'clean'
              AND ret_t5 IS NOT NULL
              AND open BETWEEN 0.5 AND 50
              AND volume > 100000
        )
        SELECT e.*, p.smoothed_continuer_rate, p.n_appearances AS p_n,
               p.avg_ret_t5_pct AS p_avg_ret_t5
        FROM enriched e
        LEFT JOIN read_parquet('{prior}') p ON e.ticker = p.ticker
    """)
    n = con.sql("SELECT COUNT(*) FROM universe").fetchone()[0]
    print(f"  Universe: {n:,} rows")

    section("STEP 2 - V1: BASELINE (no filter, equal-weight everything)")
    print(f"  {'metric':<25} {'value':>12}")
    rows = con.sql("""
        WITH per_day AS (
            SELECT d0, AVG(ret_t5) AS daily_ret_t5
            FROM universe GROUP BY 1
        )
        SELECT COUNT(*) AS n_days,
               COUNT(*) FILTER (WHERE daily_ret_t5 > 0) AS win_days,
               ROUND(100.0 * COUNT(*) FILTER (WHERE daily_ret_t5 > 0) / COUNT(*), 1) AS win_pct,
               ROUND(AVG(daily_ret_t5)*100, 3) AS daily_avg_pct,
               ROUND(STDDEV(daily_ret_t5)*100, 3) AS daily_std_pct,
               ROUND((EXP(SUM(LN(1 + daily_ret_t5))) - 1) * 100, 2) AS compound_pct
        FROM per_day
    """).fetchone()
    print(f"  {'n_days':<25} {rows[0]:>12}")
    print(f"  {'win_days':<25} {rows[1]:>12} ({rows[2]}%)")
    print(f"  {'daily avg':<25} {rows[3]:>+11.3f}%")
    print(f"  {'daily std':<25} {rows[4]:>11.3f}%")
    print(f"  {'compound (full sample)':<25} {rows[5]:>+11.2f}%")
    if rows[3] is not None and rows[4] is not None and rows[4] > 0:
        sharpe = rows[3] / rows[4] * (252 ** 0.5)
        print(f"  {'sharpe (annualized)':<25} {sharpe:>+11.2f}")
    v1 = {"n_days": rows[0], "win_pct": rows[2], "daily_avg": rows[3],
          "daily_std": rows[4], "compound": rows[5]}

    section("STEP 3 - V2: BEST 3D BUCKET (fresh + low-dvol + moderate intra)")
    rows = con.sql("""
        WITH picks AS (
            SELECT * FROM universe
            WHERE COALESCE(prior_7d_count, 0) = 0
              AND dvol_d0 < 5e6
              AND intraday_pct BETWEEN 0.30 AND 0.60
        ),
        per_day AS (
            SELECT d0, AVG(ret_t5) AS daily_ret_t5, COUNT(*) AS n_picks
            FROM picks GROUP BY 1
        )
        SELECT COUNT(*) AS n_days,
               SUM(n_picks) AS n_trades,
               COUNT(*) FILTER (WHERE daily_ret_t5 > 0) AS win_days,
               ROUND(100.0 * COUNT(*) FILTER (WHERE daily_ret_t5 > 0) / COUNT(*), 1) AS win_pct,
               ROUND(AVG(daily_ret_t5)*100, 3) AS daily_avg_pct,
               ROUND(STDDEV(daily_ret_t5)*100, 3) AS daily_std_pct,
               ROUND((EXP(SUM(LN(1 + daily_ret_t5))) - 1) * 100, 2) AS compound_pct,
               ROUND(AVG(n_picks), 2) AS avg_picks_per_day
        FROM per_day
    """).fetchone()
    print(f"  {'n_days':<25} {rows[0]:>12}")
    print(f"  {'n_trades':<25} {rows[1]:>12}")
    print(f"  {'avg picks/day':<25} {rows[7]:>11.2f}")
    print(f"  {'win_days':<25} {rows[2]:>12} ({rows[3]}%)")
    print(f"  {'daily avg':<25} {rows[4]:>+11.3f}%")
    print(f"  {'daily std':<25} {rows[5]:>11.3f}%")
    print(f"  {'compound (full sample)':<25} {rows[6]:>+11.2f}%")
    if rows[4] is not None and rows[5] is not None and rows[5] > 0:
        sharpe = rows[4] / rows[5] * (252 ** 0.5)
        print(f"  {'sharpe (annualized)':<25} {sharpe:>+11.2f}")
    v2 = {"n_days": rows[0], "n_trades": rows[1], "avg_picks": rows[7],
          "win_pct": rows[3], "daily_avg": rows[4], "daily_std": rows[5],
          "compound": rows[6]}

    section("STEP 4 - V3: CHRONIC-FADER GATE (continuer rate >=5% OR no history)")
    rows = con.sql("""
        WITH picks AS (
            SELECT * FROM universe
            WHERE (smoothed_continuer_rate IS NULL OR smoothed_continuer_rate >= 5.0)
              AND COALESCE(prior_7d_count, 0) <= 1
              AND dvol_d0 BETWEEN 1e5 AND 100e6
              AND intraday_pct BETWEEN 0.30 AND 1.00
        ),
        per_day AS (
            SELECT d0, AVG(ret_t5) AS daily_ret_t5, COUNT(*) AS n_picks
            FROM picks GROUP BY 1
        )
        SELECT COUNT(*) AS n_days,
               SUM(n_picks) AS n_trades,
               COUNT(*) FILTER (WHERE daily_ret_t5 > 0) AS win_days,
               ROUND(100.0 * COUNT(*) FILTER (WHERE daily_ret_t5 > 0) / COUNT(*), 1) AS win_pct,
               ROUND(AVG(daily_ret_t5)*100, 3) AS daily_avg_pct,
               ROUND(STDDEV(daily_ret_t5)*100, 3) AS daily_std_pct,
               ROUND((EXP(SUM(LN(1 + daily_ret_t5))) - 1) * 100, 2) AS compound_pct,
               ROUND(AVG(n_picks), 2) AS avg_picks_per_day
        FROM per_day
    """).fetchone()
    print(f"  {'n_days':<25} {rows[0]:>12}")
    print(f"  {'n_trades':<25} {rows[1]:>12}")
    print(f"  {'avg picks/day':<25} {rows[7]:>11.2f}")
    print(f"  {'win_days':<25} {rows[2]:>12} ({rows[3]}%)")
    print(f"  {'daily avg':<25} {rows[4]:>+11.3f}%")
    print(f"  {'daily std':<25} {rows[5]:>11.3f}%")
    print(f"  {'compound (full sample)':<25} {rows[6]:>+11.2f}%")
    if rows[4] is not None and rows[5] is not None and rows[5] > 0:
        sharpe = rows[4] / rows[5] * (252 ** 0.5)
        print(f"  {'sharpe (annualized)':<25} {sharpe:>+11.2f}")
    v3 = {"n_days": rows[0], "n_trades": rows[1], "avg_picks": rows[7],
          "win_pct": rows[3], "daily_avg": rows[4], "daily_std": rows[5],
          "compound": rows[6]}

    section("STEP 5 - V4: HIGH-CONTINUER TICKERS ONLY (rate >= 7% min n=5)")
    rows = con.sql("""
        WITH picks AS (
            SELECT * FROM universe
            WHERE smoothed_continuer_rate >= 7.0
              AND p_n >= 5
              AND COALESCE(prior_7d_count, 0) = 0
        ),
        per_day AS (
            SELECT d0, AVG(ret_t5) AS daily_ret_t5, COUNT(*) AS n_picks
            FROM picks GROUP BY 1
        )
        SELECT COUNT(*) AS n_days,
               SUM(n_picks) AS n_trades,
               ROUND(AVG(n_picks), 2) AS avg_picks_per_day,
               COUNT(*) FILTER (WHERE daily_ret_t5 > 0) AS win_days,
               ROUND(100.0 * COUNT(*) FILTER (WHERE daily_ret_t5 > 0) / COUNT(*), 1) AS win_pct,
               ROUND(AVG(daily_ret_t5)*100, 3) AS daily_avg_pct,
               ROUND(STDDEV(daily_ret_t5)*100, 3) AS daily_std_pct,
               ROUND((EXP(SUM(LN(1 + daily_ret_t5))) - 1) * 100, 2) AS compound_pct
        FROM per_day
    """).fetchone()
    print(f"  {'n_days':<25} {rows[0]:>12}")
    print(f"  {'n_trades':<25} {rows[1]:>12}")
    print(f"  {'avg picks/day':<25} {rows[2]:>11.2f}")
    print(f"  {'win_days':<25} {rows[3]:>12} ({rows[4]}%)")
    print(f"  {'daily avg':<25} {rows[5]:>+11.3f}%")
    print(f"  {'daily std':<25} {rows[6]:>11.3f}%")
    print(f"  {'compound':<25} {rows[7]:>+11.2f}%")
    if rows[5] is not None and rows[6] is not None and rows[6] > 0:
        sharpe = rows[5] / rows[6] * (252 ** 0.5)
        print(f"  {'sharpe':<25} {sharpe:>+11.2f}")
    v4 = {"n_days": rows[0], "n_trades": rows[1], "avg_picks": rows[2],
          "win_pct": rows[4], "daily_avg": rows[5], "daily_std": rows[6],
          "compound": rows[7]}

    section("STEP 6 - V5: NEW-TICKER LOTTERY (no catalog history; truly fresh)")
    rows = con.sql("""
        WITH picks AS (
            SELECT * FROM universe
            WHERE smoothed_continuer_rate IS NULL
              AND dvol_d0 BETWEEN 1e5 AND 100e6
              AND intraday_pct BETWEEN 0.30 AND 1.50
        ),
        per_day AS (
            SELECT d0, AVG(ret_t5) AS daily_ret_t5, COUNT(*) AS n_picks
            FROM picks GROUP BY 1
        )
        SELECT COUNT(*) AS n_days,
               SUM(n_picks) AS n_trades,
               ROUND(AVG(n_picks), 2) AS avg_picks_per_day,
               COUNT(*) FILTER (WHERE daily_ret_t5 > 0) AS win_days,
               ROUND(100.0 * COUNT(*) FILTER (WHERE daily_ret_t5 > 0) / COUNT(*), 1) AS win_pct,
               ROUND(AVG(daily_ret_t5)*100, 3) AS daily_avg_pct,
               ROUND(STDDEV(daily_ret_t5)*100, 3) AS daily_std_pct,
               ROUND((EXP(SUM(LN(1 + daily_ret_t5))) - 1) * 100, 2) AS compound_pct
        FROM per_day
    """).fetchone()
    print(f"  {'n_days':<25} {rows[0]:>12}")
    print(f"  {'n_trades':<25} {rows[1]:>12}")
    print(f"  {'avg picks/day':<25} {rows[2]:>11.2f}")
    print(f"  {'win_days':<25} {rows[3]:>12} ({rows[4]}%)")
    print(f"  {'daily avg':<25} {rows[5]:>+11.3f}%")
    print(f"  {'daily std':<25} {rows[6]:>11.3f}%")
    print(f"  {'compound':<25} {rows[7]:>+11.2f}%")
    if rows[5] is not None and rows[6] is not None and rows[6] > 0:
        sharpe = rows[5] / rows[6] * (252 ** 0.5)
        print(f"  {'sharpe':<25} {sharpe:>+11.2f}")
    v5 = {"n_days": rows[0], "n_trades": rows[1], "avg_picks": rows[2],
          "win_pct": rows[4], "daily_avg": rows[5], "daily_std": rows[6],
          "compound": rows[7]}

    section("STEP 7 - SUMMARY COMPARISON")
    print(f"  {'variant':<32} {'n_trades':>9}  {'avg/d':>8}  {'sharpe':>8}  {'compound':>10}")
    for label, v in [("V1 baseline (every catalog)", v1),
                       ("V2 fresh+low-dvol+mod-intra", v2),
                       ("V3 chronic-fader gate", v3),
                       ("V4 high-continuer tickers", v4),
                       ("V5 truly-fresh tickers", v5)]:
        n_t = v.get("n_trades", v.get("n_days"))
        avg = v.get("daily_avg", 0)
        std = v.get("daily_std") or 1
        sharpe = (avg / std * (252 ** 0.5)) if std and std > 0 else 0
        cmpd = v.get("compound", 0)
        print(f"  {label:<32} {n_t or 0:>9,}  {avg:>+7.3f}%  {sharpe:>+7.2f}  {cmpd:>+9.2f}%")

    out = {"v1": v1, "v2": v2, "v3": v3, "v4": v4, "v5": v5}
    (DERIVED / "lottery_full_universe_summary.json").write_text(
        json.dumps(out, indent=2, default=str))
    print(f"\n  Wrote {DERIVED / 'lottery_full_universe_summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
