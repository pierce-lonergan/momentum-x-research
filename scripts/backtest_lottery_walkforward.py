"""WALK-FORWARD test of the chronic-fader gate.

CRITICAL: the per-ticker continuer prior was built from the same 576 days
the in-sample backtest evaluates. Selecting tickers with high historical
continuer rate is partly circular — they earned the rate by having
continuer outcomes IN this data.

This script does the honest test:
  At each date d, compute per-ticker continuer rate using ONLY rows
  with d0 < d. Then apply the V3/V4 filter using THIS d-bound rate.
  Measure ret_t5 on the trade.

Three modes:
  WF-A: Rolling 90d lookback prior (sliding window)
  WF-B: Expanding-window prior (everything-up-to-d)
  WF-C: Train/Test 60/40 split (V3 filter built on first 60% applied to last 40%)

Outputs: data/polygon_warehouse/derived/lottery_walkforward_summary.json
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import duckdb

REPO = Path(__file__).resolve().parents[1]
WAREHOUSE = REPO / "data" / "polygon_warehouse"
DERIVED = WAREHOUSE / "derived"


def section(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def main():
    aftermath = str(DERIVED / "aftermath_strat.parquet").replace("\\", "/")

    con = duckdb.connect()
    con.sql("SET memory_limit='6GB'")
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")

    section("STEP 1 - Load + enrich catalog")
    con.sql(f"""
        CREATE OR REPLACE TABLE base AS
        SELECT *,
               ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY d0) AS appearance_idx,
               COUNT(*) OVER (PARTITION BY ticker
                               ORDER BY d0
                               RANGE BETWEEN INTERVAL 7 DAY PRECEDING
                                       AND INTERVAL 1 DAY PRECEDING) AS prior_7d_count
        FROM read_parquet('{aftermath}')
        WHERE ca_flag = 'clean' AND ret_t5 IS NOT NULL
          AND open BETWEEN 0.5 AND 50 AND volume > 100000
    """)
    n = con.sql("SELECT COUNT(*) FROM base").fetchone()[0]
    print(f"  base: {n:,} rows")

    section("STEP 2 - WF-A: Build EXPANDING-window per-ticker prior")
    # For each row, compute prior from STRICTLY PREVIOUS rows for same ticker
    print("  Computing expanding-window prior at each row ...")
    con.sql("""
        CREATE OR REPLACE TABLE expanding_prior AS
        SELECT *,
               -- Number of prior appearances of this ticker before d0
               COUNT(*) OVER (PARTITION BY ticker ORDER BY d0
                               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_n,
               -- Number of prior continuer outcomes
               SUM(CASE WHEN ret_t5 >= 0.10 THEN 1 ELSE 0 END) OVER (
                   PARTITION BY ticker ORDER BY d0
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_cont,
               -- Number of prior fader outcomes
               SUM(CASE WHEN ret_t5 <= -0.10 THEN 1 ELSE 0 END) OVER (
                   PARTITION BY ticker ORDER BY d0
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_fade
        FROM base
    """)
    # Smoothed continuer rate (beta-binomial: alpha=21, beta=79 prior)
    con.sql("""
        CREATE OR REPLACE TABLE wf_expand AS
        SELECT *,
               -- Beta-binomial smoothed: (a + cont) / (a + b + n)
               1.0 * (20.78 + prior_cont * 100.0) / (100 + prior_n) AS wf_cont_rate
        FROM expanding_prior
    """)
    print("  expanding_prior built")

    section("STEP 3 - WF-A apply gate: cont_rate>=5% OR no history (n=0)")
    print(f"  {'variant':<60} {'n':>6} {'avg_t5':>9} {'med_t5':>9} {'win%':>6} {'cont%':>6} {'fade%':>6}")
    variants = [
        ("V3-WF: gate (rate>=5 OR n=0) + p7d<=1, dvol [1e5-1e8], intra [.3-1]",
         "(prior_n = 0 OR wf_cont_rate >= 5.0) "
         "AND COALESCE(prior_7d_count, 0) <= 1 "
         "AND dvol_d0 BETWEEN 1e5 AND 100e6 "
         "AND intraday_pct BETWEEN 0.30 AND 1.00"),
        ("V4-WF: high-continuer (rate>=7, n>=5, fresh)",
         "wf_cont_rate >= 7.0 AND prior_n >= 5 AND COALESCE(prior_7d_count, 0) = 0"),
        ("V5-WF: truly-fresh (n=0)",
         "prior_n = 0 "
         "AND dvol_d0 BETWEEN 1e5 AND 100e6 "
         "AND intraday_pct BETWEEN 0.30 AND 1.50"),
        ("V7-WF: V3-WF + ret_open_close [+10, +30]%",
         "(prior_n = 0 OR wf_cont_rate >= 5.0) "
         "AND COALESCE(prior_7d_count, 0) <= 1 "
         "AND dvol_d0 BETWEEN 1e5 AND 100e6 "
         "AND intraday_pct BETWEEN 0.30 AND 1.00 "
         "AND ret_open_close_d0 BETWEEN 0.10 AND 0.30"),
        ("BASELINE-WF: every clean row (no filter)", "TRUE"),
    ]
    out = {}
    for label, where in variants:
        rows = con.sql(f"""
            SELECT COUNT(*) AS n,
                   AVG(ret_t5) AS avg_t5,
                   MEDIAN(ret_t5) AS med_t5,
                   STDDEV(ret_t5) AS std_t5,
                   1.0 * SUM(CASE WHEN ret_t5 > 0 THEN 1 ELSE 0 END) / COUNT(*) AS win,
                   1.0 * SUM(CASE WHEN ret_t5 >= 0.10 THEN 1 ELSE 0 END) / COUNT(*) AS cr,
                   1.0 * SUM(CASE WHEN ret_t5 <= -0.10 THEN 1 ELSE 0 END) / COUNT(*) AS fr
            FROM wf_expand WHERE {where}
        """).fetchone()
        n, avg, med, std, win, cr, fr = rows
        if n == 0:
            print(f"  {label:<60} {n:>6}  (no rows)")
            continue
        sharpe = (avg / std * (252/5) ** 0.5) if std and std > 0 else 0
        print(f"  {label[:60]:<60} {n:>6,} {avg*100:>+7.2f}% {med*100:>+7.2f}% {win*100:>5.1f}% {cr*100:>5.1f}% {fr*100:>5.1f}%")
        out[label] = {"n": n, "avg_t5": avg, "med_t5": med,
                       "win_rate": win, "continuer_rate": cr, "fader_rate": fr,
                       "sharpe": sharpe}

    section("STEP 4 - WF-A monthly stability of V3-WF")
    print(f"  {'month':<8} {'n':>4} {'avg_t5':>9} {'cont%':>6} {'fade%':>6}")
    rows = con.sql("""
        SELECT date_trunc('month', d0) AS month,
               COUNT(*) AS n,
               ROUND(AVG(ret_t5)*100, 2) AS avg_pct,
               ROUND(100.0 * SUM(CASE WHEN ret_t5 >= 0.10 THEN 1 ELSE 0 END) / COUNT(*), 1) AS cr,
               ROUND(100.0 * SUM(CASE WHEN ret_t5 <= -0.10 THEN 1 ELSE 0 END) / COUNT(*), 1) AS fr
        FROM wf_expand
        WHERE (prior_n = 0 OR wf_cont_rate >= 5.0)
          AND COALESCE(prior_7d_count, 0) <= 1
          AND dvol_d0 BETWEEN 1e5 AND 100e6
          AND intraday_pct BETWEEN 0.30 AND 1.00
        GROUP BY 1 ORDER BY 1 DESC LIMIT 28
    """).fetchall()
    for r in rows:
        print(f"  {str(r[0])[:7]:<8} {r[1]:>4} {r[2]:>+7.2f}% {r[3]:>5.1f}% {r[4]:>5.1f}%")

    section("STEP 5 - WF-B: Train/Test 60/40 split")
    # Find the date that splits 60/40
    cutoff = con.sql("""
        SELECT QUANTILE_CONT(d0, 0.60) FROM base
    """).fetchone()[0]
    print(f"  Train period: ... -> {cutoff}")
    print(f"  Test period:  {cutoff} -> ...")
    train_n = con.sql(f"SELECT COUNT(*) FROM base WHERE d0 < '{cutoff}'").fetchone()[0]
    test_n = con.sql(f"SELECT COUNT(*) FROM base WHERE d0 >= '{cutoff}'").fetchone()[0]
    print(f"  Train rows: {train_n:,}  Test rows: {test_n:,}")

    # Build prior on TRAIN only
    con.sql(f"""
        CREATE OR REPLACE TABLE train_prior AS
        SELECT ticker,
               COUNT(*) AS n_app,
               SUM(CASE WHEN ret_t5 >= 0.10 THEN 1 ELSE 0 END) AS n_cont,
               SUM(CASE WHEN ret_t5 <= -0.10 THEN 1 ELSE 0 END) AS n_fade,
               AVG(ret_t5) AS avg_t5,
               1.0 * (20.78 + SUM(CASE WHEN ret_t5 >= 0.10 THEN 1 ELSE 0 END) * 100.0)
                   / (100 + COUNT(*)) AS smoothed_rate
        FROM base WHERE d0 < '{cutoff}'
        GROUP BY 1
    """)

    print("\n  V3 (TRAIN-built filter) applied to TEST only:")
    rows = con.sql(f"""
        WITH test AS (
            SELECT b.*, t.smoothed_rate, t.n_app
            FROM base b LEFT JOIN train_prior t ON b.ticker = t.ticker
            WHERE b.d0 >= '{cutoff}'
        )
        SELECT COUNT(*) AS n,
               ROUND(AVG(ret_t5)*100, 2) AS avg_t5,
               ROUND(MEDIAN(ret_t5)*100, 2) AS med_t5,
               ROUND(100.0 * SUM(CASE WHEN ret_t5 > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win,
               ROUND(100.0 * SUM(CASE WHEN ret_t5 >= 0.10 THEN 1 ELSE 0 END) / COUNT(*), 1) AS cr,
               ROUND(100.0 * SUM(CASE WHEN ret_t5 <= -0.10 THEN 1 ELSE 0 END) / COUNT(*), 1) AS fr
        FROM test
        WHERE (n_app IS NULL OR smoothed_rate >= 5.0)
          AND COALESCE(prior_7d_count, 0) <= 1
          AND dvol_d0 BETWEEN 1e5 AND 100e6
          AND intraday_pct BETWEEN 0.30 AND 1.00
    """).fetchone()
    print(f"  {'n':<10} {rows[0]:>8,}")
    print(f"  {'avg_t5':<10} {rows[1]:>+7.2f}%")
    print(f"  {'med_t5':<10} {rows[2]:>+7.2f}%")
    print(f"  {'win':<10} {rows[3]:>7.1f}%")
    print(f"  {'cont rate':<10} {rows[4]:>7.1f}%")
    print(f"  {'fade rate':<10} {rows[5]:>7.1f}%")
    out["train_test_split"] = {"cutoff": str(cutoff),
                                 "n": rows[0], "avg_t5": rows[1], "med_t5": rows[2],
                                 "win": rows[3], "cont_rate": rows[4], "fade_rate": rows[5]}

    print("\n  Compare to TRAIN period (in-sample):")
    rows_train = con.sql(f"""
        WITH train AS (
            SELECT b.*, t.smoothed_rate, t.n_app
            FROM base b LEFT JOIN train_prior t ON b.ticker = t.ticker
            WHERE b.d0 < '{cutoff}'
        )
        SELECT COUNT(*) AS n,
               ROUND(AVG(ret_t5)*100, 2) AS avg_t5,
               ROUND(100.0 * SUM(CASE WHEN ret_t5 > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win,
               ROUND(100.0 * SUM(CASE WHEN ret_t5 >= 0.10 THEN 1 ELSE 0 END) / COUNT(*), 1) AS cr
        FROM train
        WHERE (n_app IS NULL OR smoothed_rate >= 5.0)
          AND COALESCE(prior_7d_count, 0) <= 1
          AND dvol_d0 BETWEEN 1e5 AND 100e6
          AND intraday_pct BETWEEN 0.30 AND 1.00
    """).fetchone()
    print(f"  {'TRAIN n=':<12} {rows_train[0]:>8,}  avg={rows_train[1]:>+5.2f}%  win={rows_train[2]:>5.1f}%  cont={rows_train[3]:>5.1f}%")
    print(f"  {'TEST  n=':<12} {rows[0]:>8,}  avg={rows[1]:>+5.2f}%  win={rows[3]:>5.1f}%  cont={rows[4]:>5.1f}%")
    if rows_train[1] is not None and rows[1] is not None:
        print(f"  {'TEST/TRAIN edge ratio':<24} {rows[1]/rows_train[1] if rows_train[1]!=0 else float('inf'):>+6.2f}")
        print("  (1.0 = no decay; 0.5 = 50% decay; <0 = sign flip)")

    out_path = DERIVED / "lottery_walkforward_summary.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
