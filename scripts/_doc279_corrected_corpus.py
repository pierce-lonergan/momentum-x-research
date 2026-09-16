"""doc279: build the CORRECTED-ADV corpus = the research corpus + a tape-faithful corrected adv20 per ticker-day,
so every conclusion's gate can be recomputed on contaminated vs decontaminated universe. Corrected adv20 =
tape-faithful minute-rolled avg(volume*close) over 20 preceding sessions (min_periods=20) for 2026 dates; the
contaminated adv20 is the corpus's stored value. Affected = |corrected-contaminated|/contaminated material AND
the gate crossing changes. Read-only. Foundation for the per-conclusion sweep."""
import os
import duckdb
import numpy as np

WH = "data/polygon_warehouse"
OUT = "data/research/doc279"
os.makedirs(OUT, exist_ok=True)
con = duckdb.connect()
con.execute("SET threads=4")
corpus = "data/research/exit_labels_cross_regime.parquet"

# tape-faithful minute-rolled daily dollar-volume for 2026 (Q1 hole + post-hole = one consistent basis)
mg = f"{WH}/minute_aggs/year=2026/**/*.parquet".replace("\\", "/")
print("building tape-faithful corrected adv20 for 2026 ...")
con.execute(f"""
CREATE TEMP TABLE dd AS
SELECT ticker, make_date(year::BIGINT, month::BIGINT, day::BIGINT) d, sum(volume*close) dvol
FROM read_parquet('{mg}', union_by_name=true) GROUP BY ticker, year, month, day;
CREATE TEMP TABLE corr AS
SELECT ticker, d,
  CASE WHEN count(*) OVER w = 20 THEN avg(dvol) OVER w END adv20_corr
FROM dd WINDOW w AS (PARTITION BY ticker ORDER BY d ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING);
""")

# merge onto the corpus: corrected adv20 for 2026 corpus dates; keep stored adv20 elsewhere (pre-2026 = unaffected)
con.execute(f"""
CREATE TEMP TABLE merged AS
SELECT c.*,
  cr.adv20_corr,
  CASE WHEN CAST(c.session_date AS DATE) >= DATE '2026-01-01' AND cr.adv20_corr IS NOT NULL
       THEN cr.adv20_corr ELSE c.adv20 END AS adv20_decontam
FROM read_parquet('{corpus}') c
LEFT JOIN corr cr ON cr.ticker=c.ticker AND cr.d=CAST(c.session_date AS DATE)
""")

df = con.execute("SELECT * FROM merged").df()
df.to_parquet(f"{OUT}/corpus_decontam.parquet")
n = len(df)
y2026 = df[df.session_date.astype(str) >= "2026-01-01"]
# affected = gate-crossing changes under $1M ADV
gate_c = (df.adv20 >= 1e6)
gate_d = (df.adv20_decontam >= 1e6)
flipped = (gate_c != gate_d)
print(f"corpus rows: {n:,}; 2026 rows: {len(y2026):,}")
print(f"corrected adv20 available (2026): {df.adv20_corr.notna().sum():,}")
print(f"ADV gate ($1M) crossing CHANGES under decontam: {int(flipped.sum()):,} ({100*flipped.sum()/n:.3f}% of corpus)")
print(f"  of which fail->... : contaminated-pass now fail: {int((gate_c & ~gate_d).sum()):,}; contaminated-fail now pass: {int((~gate_c & gate_d).sum()):,}")
# by year
for yr in ['2024', '2025', '2026']:
    sub = df[df.session_date.astype(str).str[:4] == yr]
    if len(sub):
        fc = (sub.adv20 >= 1e6) != (sub.adv20_decontam >= 1e6)
        print(f"  {yr}: {len(sub):,} rows, gate-flips {int(fc.sum()):,} ({100*fc.sum()/len(sub):.3f}%)")
print(f"\nwrote {OUT}/corpus_decontam.parquet")
con.close()
