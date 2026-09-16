"""Cross-check Friday's 8 picks against the per-ticker continuer prior."""
import duckdb
con = duckdb.connect()
con.sql("CREATE TABLE prior AS SELECT * FROM read_parquet('data/polygon_warehouse/derived/per_ticker_continuer_prior.parquet')")
print('=== FRIDAYS 8 PICKS vs HISTORICAL CONTINUER PRIOR ===')
rows = con.sql("""
    SELECT ticker, n_appearances, n_continuer, n_fader,
           smoothed_continuer_rate, avg_ret_t5_pct, avg_intra_pct, avg_dvol_M
    FROM prior
    WHERE ticker IN ('HCAI','MRAM','RPGL','RYOJ','SKLZ','VLN','WNW','XRX')
    ORDER BY smoothed_continuer_rate DESC
""").fetchall()
print(f'  {"ticker":<7} {"n":>3} {"cont":>4} {"fade":>4} {"rate":>6} {"avg_t5":>8} {"avg_intra":>9} {"avg_dvol_M":>10}')
for r in rows:
    print(f'  {r[0]:<7} {r[1]:>3} {r[2]:>4} {r[3]:>4} {r[4]:>5.1f}% {r[5]:>+7.2f}% {r[6]:>+8.2f}% {r[7]:>9.2f}M')
print()
print('Friday-8 not in prior table (never hit catalog before):')
all_8 = {'HCAI','MRAM','RPGL','RYOJ','SKLZ','VLN','WNW','XRX'}
in_prior = {r[0] for r in rows}
absent = sorted(all_8 - in_prior)
print('  ', absent)
print()
print('Interpretation: tickers absent from prior have ZERO catalog history')
print('and would default to base-rate continuer prior (~21%).')
