"""Search for the feature combinations that meaningfully separate
continuers from faders in the aftermath catalog.

Single-feature analysis (continuer_vs_fader_features.py) showed continuer
rate is ~21% FLAT across all features. Fader rate varies. So the alpha
must be in INTERACTIONS.

This script:
  1. Builds 2D contingency tables for top feature pairs.
  2. Computes per-(ticker) historical continuer rate to use as Bayesian prior.
  3. Identifies the top combinations by Sharpe-ratio-of-T+5-returns.
  4. Outputs a "selection cookbook" rule set.

Outputs:
  data/polygon_warehouse/derived/continuer_alpha_combinations.json
  data/polygon_warehouse/derived/per_ticker_continuer_prior.parquet
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
import duckdb

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"


def section(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def main():
    strat_path = str(DERIVED / "aftermath_strat.parquet").replace("\\", "/")
    con = duckdb.connect()
    # Reload + enrich with prior_7d_count and appearance_idx
    con.sql(f"""
        CREATE OR REPLACE TABLE strat_raw AS
        SELECT * FROM read_parquet('{strat_path}')
        WHERE stratum_t5 != 'no_t5_data'
    """)
    con.sql("""
        CREATE OR REPLACE TABLE strat_enriched AS
        SELECT *,
               ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY d0) AS appearance_idx,
               COUNT(*) OVER (PARTITION BY ticker
                               ORDER BY d0
                               RANGE BETWEEN INTERVAL 7 DAY PRECEDING
                                       AND INTERVAL 1 DAY PRECEDING) AS prior_7d_count
        FROM strat_raw
    """)
    con.sql(f"""
        CREATE OR REPLACE TABLE strat AS
        SELECT *,
               EXTRACT(dow FROM d0) AS dow,
               -- buckets
               CASE WHEN dvol_d0 < 1e6 THEN 'A:lt1M'
                    WHEN dvol_d0 < 5e6 THEN 'B:1to5M'
                    WHEN dvol_d0 < 25e6 THEN 'C:5to25M'
                    WHEN dvol_d0 < 100e6 THEN 'D:25to100M'
                    ELSE 'E:gt100M' END AS dvol_bucket,
               CASE WHEN intraday_pct < 0.40 THEN 'A:30-40'
                    WHEN intraday_pct < 0.60 THEN 'B:40-60'
                    WHEN intraday_pct < 1.00 THEN 'C:60-100'
                    WHEN intraday_pct < 2.00 THEN 'D:100-200'
                    ELSE 'E:200plus' END AS intra_bucket,
               CASE WHEN ret_open_close_d0 < 0 THEN 'A:neg_close'
                    WHEN ret_open_close_d0 < 0.10 THEN 'B:0-10'
                    WHEN ret_open_close_d0 < 0.30 THEN 'C:10-30'
                    WHEN ret_open_close_d0 < 0.60 THEN 'D:30-60'
                    ELSE 'E:60plus' END AS oc_bucket,
               CASE WHEN open < 1 THEN 'A:lt1'
                    WHEN open < 2 THEN 'B:1-2'
                    WHEN open < 5 THEN 'C:2-5'
                    WHEN open < 10 THEN 'D:5-10'
                    ELSE 'E:gt10' END AS price_bucket,
               COALESCE(prior_7d_count, 0) AS prior_7d
        FROM strat_enriched
    """)

    section("2D CONTINGENCY: dvol_bucket x intra_bucket")
    print(f"  {'dvol':<10} {'intra':<10} {'n':>5}  {'%cont':>6}  {'%fade':>6}  {'avg_t5':>8}  {'sharpe':>7}")
    rows = con.sql("""
        SELECT dvol_bucket, intra_bucket, COUNT(*) n,
               ROUND(100.0*SUM(CASE WHEN stratum_t5='continuer' THEN 1 ELSE 0 END)/COUNT(*), 1) pc,
               ROUND(100.0*SUM(CASE WHEN stratum_t5='fader' THEN 1 ELSE 0 END)/COUNT(*), 1) pf,
               ROUND(AVG(ret_t5)*100, 2) avg_pct,
               ROUND(AVG(ret_t5) / NULLIF(STDDEV(ret_t5), 0), 3) sharpe
        FROM strat
        GROUP BY 1, 2
        HAVING COUNT(*) >= 50
        ORDER BY avg_pct DESC LIMIT 25
    """).fetchall()
    for r in rows:
        print(f"  {r[0]:<10} {r[1]:<10} {r[2]:>5,}  {r[3]:>5.1f}%  {r[4]:>5.1f}%  {r[5]:>+7.2f}%  {(r[6] if r[6] is not None else 0):>+6.3f}")

    section("2D CONTINGENCY: dvol_bucket x prior_7d")
    print(f"  {'dvol':<10} {'prior_7d':<8} {'n':>5}  {'%cont':>6}  {'%fade':>6}  {'avg_t5':>8}  {'sharpe':>7}")
    rows = con.sql("""
        SELECT dvol_bucket, prior_7d, COUNT(*) n,
               ROUND(100.0*SUM(CASE WHEN stratum_t5='continuer' THEN 1 ELSE 0 END)/COUNT(*), 1) pc,
               ROUND(100.0*SUM(CASE WHEN stratum_t5='fader' THEN 1 ELSE 0 END)/COUNT(*), 1) pf,
               ROUND(AVG(ret_t5)*100, 2) avg_pct,
               ROUND(AVG(ret_t5) / NULLIF(STDDEV(ret_t5), 0), 3) sharpe
        FROM strat
        GROUP BY 1, 2
        HAVING COUNT(*) >= 50
        ORDER BY avg_pct DESC LIMIT 25
    """).fetchall()
    for r in rows:
        print(f"  {r[0]:<10} {r[1]:<8} {r[2]:>5,}  {r[3]:>5.1f}%  {r[4]:>5.1f}%  {r[5]:>+7.2f}%  {(r[6] if r[6] is not None else 0):>+6.3f}")

    section("3D CONTINGENCY: dvol_bucket x intra_bucket x prior_7d (top 30 by avg_t5)")
    print(f"  {'dvol':<10} {'intra':<10} {'p7d':<4} {'n':>4}  {'%cont':>6}  {'%fade':>6}  {'avg_t5':>8}")
    rows = con.sql("""
        SELECT dvol_bucket, intra_bucket, prior_7d, COUNT(*) n,
               ROUND(100.0*SUM(CASE WHEN stratum_t5='continuer' THEN 1 ELSE 0 END)/COUNT(*), 1) pc,
               ROUND(100.0*SUM(CASE WHEN stratum_t5='fader' THEN 1 ELSE 0 END)/COUNT(*), 1) pf,
               ROUND(AVG(ret_t5)*100, 2) avg_pct
        FROM strat
        GROUP BY 1, 2, 3
        HAVING COUNT(*) >= 30
        ORDER BY avg_pct DESC LIMIT 20
    """).fetchall()
    for r in rows:
        print(f"  {r[0]:<10} {r[1]:<10} {r[2]:<4} {r[3]:>4,}  {r[4]:>5.1f}%  {r[5]:>5.1f}%  {r[6]:>+7.2f}%")

    section("BOTTOM 20: WORST combinations (avoid these)")
    rows = con.sql("""
        SELECT dvol_bucket, intra_bucket, prior_7d, COUNT(*) n,
               ROUND(100.0*SUM(CASE WHEN stratum_t5='continuer' THEN 1 ELSE 0 END)/COUNT(*), 1) pc,
               ROUND(100.0*SUM(CASE WHEN stratum_t5='fader' THEN 1 ELSE 0 END)/COUNT(*), 1) pf,
               ROUND(AVG(ret_t5)*100, 2) avg_pct
        FROM strat
        GROUP BY 1, 2, 3
        HAVING COUNT(*) >= 30
        ORDER BY avg_pct ASC LIMIT 20
    """).fetchall()
    print(f"  {'dvol':<10} {'intra':<10} {'p7d':<4} {'n':>4}  {'%cont':>6}  {'%fade':>6}  {'avg_t5':>8}")
    for r in rows:
        print(f"  {r[0]:<10} {r[1]:<10} {r[2]:<4} {r[3]:>4,}  {r[4]:>5.1f}%  {r[5]:>5.1f}%  {r[6]:>+7.2f}%")

    section("PER-TICKER CONTINUER RATE (Bayesian prior)")
    # Compute beta-binomial smoothed continuer rate per ticker
    # alpha_prior = 21 (overall continuer rate * 100), beta_prior = 79
    con.sql("""
        CREATE OR REPLACE TABLE per_ticker_prior AS
        WITH stats AS (
            SELECT ticker,
                   COUNT(*) AS n_appearances,
                   SUM(CASE WHEN stratum_t5='continuer' THEN 1 ELSE 0 END) AS n_continuer,
                   SUM(CASE WHEN stratum_t5='fader' THEN 1 ELSE 0 END) AS n_fader,
                   AVG(ret_t5) AS avg_ret_t5,
                   AVG(intraday_pct) AS avg_intra,
                   AVG(dvol_d0) AS avg_dvol
            FROM strat
            GROUP BY 1
        )
        SELECT ticker, n_appearances, n_continuer, n_fader,
               -- Beta-binomial smoothed rate: (alpha + n_cont) / (alpha + beta + n)
               ROUND((20.78 + n_continuer * 100.0) / (100 + n_appearances), 2)
                   AS smoothed_continuer_rate,
               ROUND(avg_ret_t5*100, 2) AS avg_ret_t5_pct,
               ROUND(avg_intra*100, 2) AS avg_intra_pct,
               ROUND(avg_dvol/1e6, 2) AS avg_dvol_M
        FROM stats
        ORDER BY smoothed_continuer_rate DESC
    """)
    n_tickers = con.sql("SELECT COUNT(*) FROM per_ticker_prior").fetchone()[0]
    print(f"  built per-ticker prior for {n_tickers} unique tickers")

    print("\n  TOP 25 tickers by smoothed_continuer_rate (n>=3 appearances):")
    print(f"  {'ticker':<8} {'n':>4} {'cont':>4} {'fade':>4} {'rate':>6} {'avg_t5':>8} {'avg_intra':>9} {'avg_dvol_M':>10}")
    rows = con.sql("""
        SELECT ticker, n_appearances, n_continuer, n_fader,
               smoothed_continuer_rate, avg_ret_t5_pct, avg_intra_pct, avg_dvol_M
        FROM per_ticker_prior
        WHERE n_appearances >= 3
        ORDER BY smoothed_continuer_rate DESC LIMIT 25
    """).fetchall()
    for r in rows:
        print(f"  {r[0]:<8} {r[1]:>4} {r[2]:>4} {r[3]:>4} {r[4]:>5.1f}% {r[5]:>+7.2f}% {r[6]:>+8.2f}% {r[7]:>9.2f}M")

    print("\n  BOTTOM 25 by smoothed_continuer_rate (n>=5 - the chronic faders):")
    print(f"  {'ticker':<8} {'n':>4} {'cont':>4} {'fade':>4} {'rate':>6} {'avg_t5':>8} {'avg_intra':>9} {'avg_dvol_M':>10}")
    rows = con.sql("""
        SELECT ticker, n_appearances, n_continuer, n_fader,
               smoothed_continuer_rate, avg_ret_t5_pct, avg_intra_pct, avg_dvol_M
        FROM per_ticker_prior
        WHERE n_appearances >= 5
        ORDER BY smoothed_continuer_rate ASC LIMIT 25
    """).fetchall()
    for r in rows:
        print(f"  {r[0]:<8} {r[1]:>4} {r[2]:>4} {r[3]:>4} {r[4]:>5.1f}% {r[5]:>+7.2f}% {r[6]:>+8.2f}% {r[7]:>9.2f}M")

    # Persist
    out_prior = DERIVED / "per_ticker_continuer_prior.parquet"
    con.sql(f"COPY (SELECT * FROM per_ticker_prior) TO '{out_prior}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    print(f"\n  Wrote {out_prior} ({out_prior.stat().st_size/1e3:.1f} KB, {n_tickers} tickers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
