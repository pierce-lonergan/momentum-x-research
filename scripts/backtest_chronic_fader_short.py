"""Chronic-fader SHORT backtest — symmetric counterpart to the lottery long.

Hypothesis: tickers with smoothed_continuer_rate < 2% AND n_appearances >= 5
have proven historical fade behavior (avg T+5 = -15% to -32% across
multiple appearances). When they appear in the catalog (>=30% intraday),
SHORT at the close of d0, hold 1-5 days, target -10% / stop +10%.

This complements the lottery (long the gap, exit same day) by capturing
the OVERNIGHT fade we observed in the catalog T+5 = -7% median.

Variants tested (all with WALK-FORWARD prior to avoid survivorship bias):
  S1 baseline: short EVERY catalog row at close, hold to T+5
  S2 chronic-fader gate: only short tickers with WF rate <2% and n>=5
  S3 S2 + ret_open_close strong (>=+30% same-day): "spike + dump" pattern
  S4 S2 + dvol >$5M (institutional flow, more borrow available)
  S5 S2 + intraday >100% (extreme spike, fade likely)
  S6 same as S2 but exit T+1 instead of T+5

CRITICAL: this is the WALK-FORWARD version. Per-ticker rate computed using
ONLY appearances strictly before d0.

Risks NOT modeled:
  - Borrow availability (chronic faders are often hard-to-borrow)
  - Short-side dividend/short-interest fees
  - Real fills at close (we use d0_close as entry)
  - Margin requirements

Outputs:
  data/polygon_warehouse/derived/chronic_fader_short_summary.json
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import duckdb

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"


def section(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def main():
    aftermath = str(DERIVED / "aftermath_strat.parquet").replace("\\", "/")
    con = duckdb.connect()
    con.sql("SET memory_limit='6GB'")
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")

    section("STEP 1 - Build base + walk-forward per-ticker prior")
    con.sql(f"""
        CREATE OR REPLACE TABLE base AS
        SELECT *,
               COUNT(*) OVER (PARTITION BY ticker
                               ORDER BY d0
                               RANGE BETWEEN INTERVAL 7 DAY PRECEDING
                                       AND INTERVAL 1 DAY PRECEDING) AS prior_7d_count
        FROM read_parquet('{aftermath}')
        WHERE ca_flag = 'clean' AND ret_t5 IS NOT NULL AND ret_t1 IS NOT NULL
          AND open BETWEEN 0.5 AND 50 AND volume > 100000
    """)

    # WALK-FORWARD prior at each row
    con.sql("""
        CREATE OR REPLACE TABLE wf AS
        SELECT *,
               COUNT(*) OVER (PARTITION BY ticker ORDER BY d0
                               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_n,
               SUM(CASE WHEN ret_t5 >= 0.10 THEN 1 ELSE 0 END) OVER (
                   PARTITION BY ticker ORDER BY d0
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_cont,
               SUM(CASE WHEN ret_t5 <= -0.10 THEN 1 ELSE 0 END) OVER (
                   PARTITION BY ticker ORDER BY d0
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_fade
        FROM base
    """)
    con.sql("""
        CREATE OR REPLACE TABLE wf2 AS
        SELECT *,
               1.0 * (20.78 + prior_cont * 100.0) / (100 + prior_n) AS wf_cont_rate,
               -- For SHORT: a 'fader rate' = prior fades / prior n (smoothed alpha=43, beta=57 prior)
               1.0 * (43.36 + prior_fade * 100.0) / (100 + prior_n) AS wf_fade_rate
        FROM wf
    """)
    n = con.sql("SELECT COUNT(*) FROM wf2").fetchone()[0]
    print(f"  base + wf prior: {n:,} rows")

    # Helper SQL: short trade P&L
    # SHORT entry at d0 close, hold to T+1 or T+5 close
    # P&L = (entry - exit) / entry (positive = short profit)
    # No intraday target/stop modeled (would need minute bars per held day)
    section("STEP 2 - Variant sweep (T+5 horizon unless noted)")
    print(f"  {'variant':<55} {'n':>6} {'avg':>9} {'med':>9} {'win%':>6} {'p25':>9} {'p75':>9}")

    variants = [
        ("S1 baseline: short every catalog row, hold T+5",
         "TRUE", "-ret_t5"),
        ("S2 chronic-fader: WF cont_rate <3% AND prior_n >=5, T+5",
         "wf_cont_rate < 3.0 AND prior_n >= 5", "-ret_t5"),
        ("S3 S2 + sustained close (open-close >= +30%), T+5",
         "wf_cont_rate < 3.0 AND prior_n >= 5 AND ret_open_close_d0 >= 0.30", "-ret_t5"),
        ("S4 S2 + dvol > $5M (better borrow), T+5",
         "wf_cont_rate < 3.0 AND prior_n >= 5 AND dvol_d0 > 5e6", "-ret_t5"),
        ("S5 S2 + intraday > +100% (extreme spike), T+5",
         "wf_cont_rate < 3.0 AND prior_n >= 5 AND intraday_pct > 1.0", "-ret_t5"),
        ("S6 S2 but exit T+1 (overnight only)",
         "wf_cont_rate < 3.0 AND prior_n >= 5", "-ret_t1"),
        ("S7 S3 + exit T+1",
         "wf_cont_rate < 3.0 AND prior_n >= 5 AND ret_open_close_d0 >= 0.30", "-ret_t1"),
        ("S8 LOOSER fader gate (rate <5%, n>=3), T+5",
         "wf_cont_rate < 5.0 AND prior_n >= 3", "-ret_t5"),
        ("S9 fader gate + recurrent (prior_7d >=1), T+5",
         "wf_cont_rate < 3.0 AND prior_n >= 5 AND prior_7d_count >= 1", "-ret_t5"),
        ("S10 only fader-rate >5%, T+5 (control: above-avg historical fader)",
         "wf_fade_rate > 50.0 AND prior_n >= 5", "-ret_t5"),
    ]

    out = {}
    for label, where, pnl_expr in variants:
        rows = con.sql(f"""
            SELECT COUNT(*) AS n,
                   AVG({pnl_expr}) AS avg,
                   MEDIAN({pnl_expr}) AS med,
                   STDDEV({pnl_expr}) AS std,
                   1.0 * SUM(CASE WHEN {pnl_expr} > 0 THEN 1 ELSE 0 END) / COUNT(*) AS win,
                   QUANTILE_CONT({pnl_expr}, 0.25) AS p25,
                   QUANTILE_CONT({pnl_expr}, 0.75) AS p75
            FROM wf2 WHERE {where}
        """).fetchone()
        n, avg, med, std, win, p25, p75 = rows
        if n == 0:
            print(f"  {label[:55]:<55} {n:>6}  (no rows)")
            continue
        sharpe = (avg / std * (252/5)**0.5) if std and std > 0 else 0
        print(f"  {label[:55]:<55} {n:>6,} {avg*100:>+7.2f}% {med*100:>+7.2f}% {win*100:>5.1f}% {p25*100:>+7.2f}% {p75*100:>+7.2f}%")
        out[label] = {
            "n": n, "avg": avg, "med": med, "win": win,
            "std": std, "sharpe": sharpe, "p25": p25, "p75": p75,
        }

    section("STEP 3 - Monthly stability (best variant: S2 chronic-fader gate)")
    print(f"  {'month':<8} {'n':>4} {'avg_short_pnl':>13} {'win':>6}")
    rows = con.sql("""
        SELECT date_trunc('month', d0) AS month,
               COUNT(*) AS n,
               ROUND(AVG(-ret_t5)*100, 2) AS avg_pct,
               ROUND(100.0 * SUM(CASE WHEN -ret_t5 > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win
        FROM wf2 WHERE wf_cont_rate < 3.0 AND prior_n >= 5
        GROUP BY 1 ORDER BY 1 DESC LIMIT 28
    """).fetchall()
    for r in rows:
        print(f"  {str(r[0])[:7]:<8} {r[1]:>4} {r[2]:>+12.2f}% {r[3]:>5.1f}%")

    section("STEP 4 - Top 25 chronic-fader tickers in walk-forward universe")
    rows = con.sql("""
        WITH labeled AS (
            SELECT ticker, COUNT(*) AS n_app,
                   AVG(-ret_t5) AS avg_short_pnl,
                   1.0 * SUM(CASE WHEN -ret_t5 > 0 THEN 1 ELSE 0 END) / COUNT(*) AS win,
                   AVG(wf_cont_rate) AS avg_wf_rate
            FROM wf2
            WHERE wf_cont_rate < 3.0 AND prior_n >= 5
            GROUP BY 1
            HAVING COUNT(*) >= 3
        )
        SELECT ticker, n_app,
               ROUND(avg_wf_rate, 2) AS wf_rate,
               ROUND(avg_short_pnl*100, 2) AS avg_short,
               ROUND(win*100, 1) AS win
        FROM labeled
        ORDER BY avg_short_pnl DESC LIMIT 25
    """).fetchall()
    print(f"  {'ticker':<7} {'n':>3} {'wf_rate':>8} {'avg_short':>10} {'win%':>6}")
    for r in rows:
        print(f"  {r[0]:<7} {r[1]:>3} {r[2]:>7.2f}% {r[3]:>+9.2f}% {r[4]:>5.1f}%")

    section("STEP 5 - Bracket simulation: target -10% / stop +10% from d0 close, exit by T+5")
    # If price falls to (entry * 0.90) within 5 days, target hit (-10% on entry, +10% short profit)
    # If price rises to (entry * 1.10) within 5 days, stop hit (+10% on entry, -10% short loss)
    # Else exit at T+5 close
    # We use min/max approximation from d0 close × intraday cumulative range
    # SIMPLIFICATION: we don't have minute bars for d+1..+5, so we approximate using
    #   ret_t1, ret_t5: if min(0, ret_t1, ret_t5) <= -0.10 → target hit
    #                   if max(0, ret_t1, ret_t5) >= +0.10 → stop hit
    # This is a LOWER BOUND approximation (real intraday paths may have hit either earlier)
    print(f"  {'variant':<55} {'n':>6} {'avg':>9} {'win%':>6} {'tgt':>5} {'stp':>5} {'eod':>5}")
    for label, where in [
        ("S2-bracket (chronic-fader -10/+10 to T+5)",
         "wf_cont_rate < 3.0 AND prior_n >= 5"),
        ("S3-bracket (S2 + sustained close)",
         "wf_cont_rate < 3.0 AND prior_n >= 5 AND ret_open_close_d0 >= 0.30"),
        ("S4-bracket (S2 + dvol > $5M)",
         "wf_cont_rate < 3.0 AND prior_n >= 5 AND dvol_d0 > 5e6"),
    ]:
        rows = con.sql(f"""
            WITH lim AS (
                SELECT *,
                       LEAST(COALESCE(ret_t1, 0), COALESCE(ret_t5, 0)) AS min_ret,
                       GREATEST(COALESCE(ret_t1, 0), COALESCE(ret_t5, 0)) AS max_ret
                FROM wf2 WHERE {where}
            ),
            b AS (
                SELECT *,
                       CASE
                         WHEN max_ret >= 0.10 AND (min_ret > -0.10 OR ABS(max_ret) > ABS(min_ret))
                              THEN 'stop'  -- price went up >=10% (we lose 10%)
                         WHEN min_ret <= -0.10 THEN 'target'  -- price went down >=10% (we win 10%)
                         ELSE 'eod'
                       END AS exit_reason,
                       CASE
                         WHEN max_ret >= 0.10 AND (min_ret > -0.10 OR ABS(max_ret) > ABS(min_ret))
                              THEN -0.10
                         WHEN min_ret <= -0.10 THEN 0.10
                         ELSE -ret_t5
                       END AS short_pnl
                FROM lim
            )
            SELECT COUNT(*) AS n,
                   AVG(short_pnl) AS avg,
                   1.0 * SUM(CASE WHEN short_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) AS win,
                   1.0 * SUM(CASE WHEN exit_reason='target' THEN 1 ELSE 0 END) / COUNT(*) AS tgt,
                   1.0 * SUM(CASE WHEN exit_reason='stop' THEN 1 ELSE 0 END) / COUNT(*) AS stp,
                   1.0 * SUM(CASE WHEN exit_reason='eod' THEN 1 ELSE 0 END) / COUNT(*) AS eod
            FROM b
        """).fetchone()
        n, avg, win, tgt, stp, eod = rows
        if n == 0: continue
        print(f"  {label[:55]:<55} {n:>6,} {avg*100:>+7.2f}% {win*100:>5.1f}% {tgt*100:>4.1f}% {stp*100:>4.1f}% {eod*100:>4.1f}%")

    out_path = DERIVED / "chronic_fader_short_summary.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
