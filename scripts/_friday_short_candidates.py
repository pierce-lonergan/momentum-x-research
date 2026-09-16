"""Who would the chronic-fader-short have targeted on Friday 2026-05-01?

Cross-references Friday's high-mover catalog rows against the per-ticker
continuer prior to identify S3-eligible shorts (cont_rate <3%, n>=5,
sustained close >=+30%, dvol >$5M optional).
"""
import duckdb
from pathlib import Path
con = duckdb.connect()
con.sql("SET memory_limit='6GB'")
con.sql(f"SET temp_directory='{Path(__file__).resolve().parent.parent.as_posix()}/.duckdb_tmp'")

con.sql("""
    CREATE TABLE base AS
    SELECT * FROM read_parquet('data/polygon_warehouse/derived/aftermath_strat.parquet')
""")
con.sql("""
    CREATE TABLE prior AS
    SELECT * FROM read_parquet('data/polygon_warehouse/derived/per_ticker_continuer_prior.parquet')
""")

print("=" * 80)
print("CHRONIC-FADER SHORT CANDIDATES on 2026-05-01 (Friday)")
print("=" * 80)

# Friday's catalog rows joined with prior
rows = con.sql("""
    SELECT b.ticker, b.open, b.close_t0,
           ROUND(b.intraday_pct*100, 1) AS intraday_pct,
           ROUND(b.ret_open_close_d0*100, 1) AS oc_pct,
           ROUND(b.dvol_d0/1e6, 2) AS dvol_M,
           p.n_appearances, p.smoothed_continuer_rate AS cont_rate,
           p.avg_ret_t5_pct AS hist_avg_t5
    FROM base b LEFT JOIN prior p ON b.ticker = p.ticker
    WHERE b.d0 = '2026-05-01' AND b.ca_flag = 'clean'
    ORDER BY b.intraday_pct DESC
""").fetchall()

print(f"  {'ticker':<7} {'open':>6} {'close':>6} {'intra%':>7} {'oc%':>6} {'dvol_M':>8} {'n':>3} {'rate':>6} {'avg_t5':>8} {'S3?':>4}")
n_s3 = 0
for r in rows:
    sym = r[0]
    intra, oc, dvol, n_app, rate, h_t5 = r[3], r[4], r[5], r[6], r[7], r[8]
    s3_eligible = (rate is not None and rate < 3.0
                    and n_app is not None and n_app >= 5
                    and oc >= 30
                    and dvol > 5)
    flag = "YES" if s3_eligible else ""
    if s3_eligible: n_s3 += 1
    n_app_str = f"{n_app}" if n_app else "-"
    rate_str = f"{rate:>5.1f}%" if rate else "    -"
    h_t5_str = f"{h_t5:>+6.1f}%" if h_t5 else "    -"
    print(f"  {sym:<7} {r[1]:>6.2f} {r[2]:>6.2f} {intra:>+6.1f}% {oc:>+5.1f}% {dvol:>7.2f}M {n_app_str:>3} {rate_str:>6} {h_t5_str:>8} {flag:>4}")
print(f"\n  S3-eligible shorts on Friday: {n_s3}")

# Also S2 (less restrictive)
print("\n" + "=" * 80)
print("S2 CANDIDATES (chronic-fader gate only, no sustained-close requirement):")
print("=" * 80)
n_s2 = 0
for r in rows:
    rate = r[7]; n_app = r[6]
    if rate is not None and rate < 3.0 and n_app is not None and n_app >= 5:
        n_s2 += 1
        print(f"  {r[0]:<7}  rate={rate:.1f}%  n={n_app}  intra={r[3]:+.1f}%  oc={r[4]:+.1f}%  hist_t5={r[8]:+.1f}%")
print(f"\n  S2-eligible shorts on Friday: {n_s2}")
