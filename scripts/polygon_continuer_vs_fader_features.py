"""Feature analysis: what distinguishes T+5 CONTINUERS from FADERS?

For the 20,415 clean high-mover rows, compute per-stratum distributions of:
  - starting price (open)
  - dollar volume on entry day
  - intraday max % (entry-day intensity)
  - same-day open-to-close return
  - recurrence count (how many times this ticker hit catalog before)
  - day-of-week
  - prior-week count of same-ticker catalog hits

Output: data/polygon_warehouse/derived/continuer_vs_fader_features.json
        Plus a printed comparison table.

Goal: identify the top 3-5 features where continuers and faders cleanly separate.
Those become the lottery's selection prior.
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


def fmt_dist(stats: dict) -> str:
    return (f"n={stats['n']:>6,}  "
            f"med={stats['med']:>+9.2f}  "
            f"p25={stats['p25']:>+9.2f}  "
            f"p75={stats['p75']:>+9.2f}  "
            f"avg={stats['avg']:>+9.2f}")


def per_stratum_dist(con, feature_expr: str, label: str) -> dict:
    rows = con.sql(f"""
        SELECT stratum_t5,
               COUNT(*) AS n,
               MEDIAN({feature_expr}) AS med,
               QUANTILE_CONT({feature_expr}, 0.25) AS p25,
               QUANTILE_CONT({feature_expr}, 0.75) AS p75,
               AVG({feature_expr}) AS avg
        FROM aftermath_strat
        WHERE stratum_t5 IN ('continuer', 'fader', 'chopper')
          AND {feature_expr} IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    out = {}
    for r in rows:
        out[r[0]] = {"n": r[1], "med": r[2], "p25": r[3], "p75": r[4], "avg": r[5]}
    print(f"\n  {label}")
    for stratum in ["continuer", "chopper", "fader"]:
        if stratum in out:
            print(f"    {stratum:10s}  {fmt_dist(out[stratum])}")
    return out


def main():
    strat_path = str(DERIVED / "aftermath_strat.parquet").replace("\\", "/")
    if not Path(strat_path).exists():
        print("ERROR: aftermath_strat.parquet missing. Run polygon_aftermath_catalog.py first.")
        return 1

    con = duckdb.connect()
    con.sql(f"CREATE OR REPLACE TABLE aftermath_strat AS SELECT * FROM read_parquet('{strat_path}')")

    # Add recurrence + day-of-week features
    section("ENRICHING with recurrence + DOW features")
    con.sql("""
        CREATE OR REPLACE TABLE aftermath_strat AS
        WITH ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY d0) AS appearance_idx,
                   COUNT(*) OVER (PARTITION BY ticker
                                   ORDER BY d0 RANGE BETWEEN INTERVAL 7 DAY PRECEDING
                                                       AND INTERVAL 1 DAY PRECEDING) AS prior_7d_count,
                   EXTRACT(dow FROM d0) AS day_of_week
            FROM aftermath_strat
        )
        SELECT * FROM ranked
    """)
    print("  done")

    section("FEATURE DISTRIBUTIONS per T+5 stratum (continuer / chopper / fader)")
    summary = {}

    summary["entry_price"]    = per_stratum_dist(con, "open",            "Starting price (open) [$]")
    summary["dollar_vol"]     = per_stratum_dist(con, "dvol_d0 / 1e6",   "Dollar volume entry-day [M$]")
    summary["intraday_pct"]   = per_stratum_dist(con, "intraday_pct*100", "Intraday max % (entry day)")
    summary["open_close_d0"]  = per_stratum_dist(con, "ret_open_close_d0*100", "Same-day open-close % (where catalog row 'closed')")
    summary["volume_ratio_t1"] = per_stratum_dist(con, "volume_ratio_t1", "Vol ratio t+1 / t0 (decay)")
    summary["appearance_idx"] = per_stratum_dist(con, "appearance_idx",  "Ticker's nth appearance in catalog (1 = first time)")
    summary["prior_7d_count"] = per_stratum_dist(con, "prior_7d_count",  "# times same ticker pumped in last 7 days")
    summary["day_of_week"]    = per_stratum_dist(con, "day_of_week",     "Day of week (0=Sun .. 6=Sat)")

    # Discretized contingency: for each feature, look at continuer rate by bucket
    section("DISCRETIZED: continuer rate by feature bucket")

    print("\n  By open price bucket:")
    rows = con.sql("""
        SELECT
          CASE
            WHEN open < 1 THEN 'A:0-1'
            WHEN open < 2 THEN 'B:1-2'
            WHEN open < 5 THEN 'C:2-5'
            WHEN open < 10 THEN 'D:5-10'
            WHEN open < 20 THEN 'E:10-20'
            ELSE             'F:20+'
          END AS bucket,
          COUNT(*) n,
          SUM(CASE WHEN stratum_t5 = 'continuer' THEN 1 ELSE 0 END) n_cont,
          SUM(CASE WHEN stratum_t5 = 'fader' THEN 1 ELSE 0 END) n_fade,
          ROUND(100.0 * SUM(CASE WHEN stratum_t5 = 'continuer' THEN 1 ELSE 0 END) / COUNT(*), 1) pct_cont,
          ROUND(100.0 * SUM(CASE WHEN stratum_t5 = 'fader' THEN 1 ELSE 0 END) / COUNT(*), 1) pct_fade,
          ROUND(AVG(ret_t5)*100, 2) avg_t5
        FROM aftermath_strat WHERE stratum_t5 != 'no_t5_data'
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    print(f"    {'bucket':<10} {'n':>6}  {'%cont':>6}  {'%fade':>6}  {'avg_t5':>8}")
    for r in rows:
        print(f"    {r[0]:<10} {r[1]:>6,}  {r[4]:>5.1f}%  {r[5]:>5.1f}%  {r[6]:>+7.2f}%")

    print("\n  By dollar volume bucket (entry day, $M):")
    rows = con.sql("""
        SELECT
          CASE
            WHEN dvol_d0 < 1e6 THEN 'A:<$1M'
            WHEN dvol_d0 < 5e6 THEN 'B:$1-5M'
            WHEN dvol_d0 < 25e6 THEN 'C:$5-25M'
            WHEN dvol_d0 < 100e6 THEN 'D:$25-100M'
            WHEN dvol_d0 < 500e6 THEN 'E:$100-500M'
            ELSE                    'F:$500M+'
          END AS bucket,
          COUNT(*) n,
          ROUND(100.0 * SUM(CASE WHEN stratum_t5 = 'continuer' THEN 1 ELSE 0 END) / COUNT(*), 1) pct_cont,
          ROUND(100.0 * SUM(CASE WHEN stratum_t5 = 'fader' THEN 1 ELSE 0 END) / COUNT(*), 1) pct_fade,
          ROUND(AVG(ret_t5)*100, 2) avg_t5
        FROM aftermath_strat WHERE stratum_t5 != 'no_t5_data'
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    print(f"    {'bucket':<14} {'n':>6}  {'%cont':>6}  {'%fade':>6}  {'avg_t5':>8}")
    for r in rows:
        print(f"    {r[0]:<14} {r[1]:>6,}  {r[2]:>5.1f}%  {r[3]:>5.1f}%  {r[4]:>+7.2f}%")

    print("\n  By intraday_pct bucket (how violent the move):")
    rows = con.sql("""
        SELECT
          CASE
            WHEN intraday_pct < 0.40 THEN 'A:30-40%'
            WHEN intraday_pct < 0.60 THEN 'B:40-60%'
            WHEN intraday_pct < 1.00 THEN 'C:60-100%'
            WHEN intraday_pct < 2.00 THEN 'D:100-200%'
            ELSE                         'E:200%+'
          END AS bucket,
          COUNT(*) n,
          ROUND(100.0 * SUM(CASE WHEN stratum_t5 = 'continuer' THEN 1 ELSE 0 END) / COUNT(*), 1) pct_cont,
          ROUND(100.0 * SUM(CASE WHEN stratum_t5 = 'fader' THEN 1 ELSE 0 END) / COUNT(*), 1) pct_fade,
          ROUND(AVG(ret_t5)*100, 2) avg_t5
        FROM aftermath_strat WHERE stratum_t5 != 'no_t5_data'
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    print(f"    {'bucket':<12} {'n':>6}  {'%cont':>6}  {'%fade':>6}  {'avg_t5':>8}")
    for r in rows:
        print(f"    {r[0]:<12} {r[1]:>6,}  {r[2]:>5.1f}%  {r[3]:>5.1f}%  {r[4]:>+7.2f}%")

    print("\n  By same-day OPEN-CLOSE strength (was the move sustained?):")
    rows = con.sql("""
        SELECT
          CASE
            WHEN ret_open_close_d0 < 0 THEN 'A:negative_close'
            WHEN ret_open_close_d0 < 0.10 THEN 'B:0-10%_close'
            WHEN ret_open_close_d0 < 0.30 THEN 'C:10-30%_close'
            WHEN ret_open_close_d0 < 0.60 THEN 'D:30-60%_close'
            ELSE                            'E:60%+_close'
          END AS bucket,
          COUNT(*) n,
          ROUND(100.0 * SUM(CASE WHEN stratum_t5 = 'continuer' THEN 1 ELSE 0 END) / COUNT(*), 1) pct_cont,
          ROUND(100.0 * SUM(CASE WHEN stratum_t5 = 'fader' THEN 1 ELSE 0 END) / COUNT(*), 1) pct_fade,
          ROUND(AVG(ret_t5)*100, 2) avg_t5
        FROM aftermath_strat WHERE stratum_t5 != 'no_t5_data'
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    print(f"    {'bucket':<18} {'n':>6}  {'%cont':>6}  {'%fade':>6}  {'avg_t5':>8}")
    for r in rows:
        print(f"    {r[0]:<18} {r[1]:>6,}  {r[2]:>5.1f}%  {r[3]:>5.1f}%  {r[4]:>+7.2f}%")

    print("\n  By appearance_idx (first-time vs repeat pumper):")
    rows = con.sql("""
        SELECT
          CASE
            WHEN appearance_idx = 1 THEN 'A:1st_time'
            WHEN appearance_idx = 2 THEN 'B:2nd'
            WHEN appearance_idx <= 5 THEN 'C:3-5th'
            WHEN appearance_idx <= 15 THEN 'D:6-15th'
            ELSE                          'E:16+'
          END AS bucket,
          COUNT(*) n,
          ROUND(100.0 * SUM(CASE WHEN stratum_t5 = 'continuer' THEN 1 ELSE 0 END) / COUNT(*), 1) pct_cont,
          ROUND(100.0 * SUM(CASE WHEN stratum_t5 = 'fader' THEN 1 ELSE 0 END) / COUNT(*), 1) pct_fade,
          ROUND(AVG(ret_t5)*100, 2) avg_t5
        FROM aftermath_strat WHERE stratum_t5 != 'no_t5_data'
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    print(f"    {'bucket':<14} {'n':>6}  {'%cont':>6}  {'%fade':>6}  {'avg_t5':>8}")
    for r in rows:
        print(f"    {r[0]:<14} {r[1]:>6,}  {r[2]:>5.1f}%  {r[3]:>5.1f}%  {r[4]:>+7.2f}%")

    print("\n  By prior_7d_count (recent pump frequency):")
    rows = con.sql("""
        SELECT
          CASE
            WHEN prior_7d_count = 0 THEN 'A:fresh_(no_recent)'
            WHEN prior_7d_count = 1 THEN 'B:1_prior_in_7d'
            WHEN prior_7d_count = 2 THEN 'C:2_prior_in_7d'
            ELSE                       'D:3+_prior_in_7d'
          END AS bucket,
          COUNT(*) n,
          ROUND(100.0 * SUM(CASE WHEN stratum_t5 = 'continuer' THEN 1 ELSE 0 END) / COUNT(*), 1) pct_cont,
          ROUND(100.0 * SUM(CASE WHEN stratum_t5 = 'fader' THEN 1 ELSE 0 END) / COUNT(*), 1) pct_fade,
          ROUND(AVG(ret_t5)*100, 2) avg_t5
        FROM aftermath_strat WHERE stratum_t5 != 'no_t5_data'
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    print(f"    {'bucket':<22} {'n':>6}  {'%cont':>6}  {'%fade':>6}  {'avg_t5':>8}")
    for r in rows:
        print(f"    {r[0]:<22} {r[1]:>6,}  {r[2]:>5.1f}%  {r[3]:>5.1f}%  {r[4]:>+7.2f}%")

    # Persist summary
    out = DERIVED / "continuer_vs_fader_features.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n  Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
