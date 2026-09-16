"""doc279 CORRECTED FOUNDATION (panel fix): separate the two toggles my first pass CONFLATED.
TOGGLE-A = data-completeness (day_aggs Q1-hole vs minute-rolled FILLED).
TOGGLE-B = window policy (partial-window-trusted [original] vs min_periods=20 [my first-pass artifact]).
The DECONTAMINATION-ONLY flip holds window policy FIXED at the doc's ORIGINAL policy (partial trusted) and toggles
ONLY the data. My first pass used stored-adv20 (partial) vs min_periods=20-corrected (strict) = conflated, so its
"0 flips in 2024/2025" was basis-contaminated. This recomputes both sides on the SAME partial policy, over the corpus
ticker-days, at MULTIPLE gates ($1M, $5M, $10M) since load-bearing conclusions gate at different thresholds.
Read-only."""
import duckdb, numpy as np, pandas as pd
WH = "data/polygon_warehouse"; OUT = "data/research/doc279"
con = duckdb.connect(); con.execute("SET threads=4")
corpus = "data/research/exit_labels_cross_regime.parquet"
rebuild = f"{OUT}/../doc278/day_aggs_q1_rebuild.parquet"
da = f"{WH}/day_aggs/**/*.parquet".replace("\\", "/")  # ALL years (2024/2025/2026) so pre-hole rows are comparable

# daily dollar-volume series, PARTIAL-window policy on BOTH arms (original policy), toggling ONLY the data:
#  contaminated = day_aggs 2026 only (hole present);  filled = day_aggs 2026 + minute-rolled Q1 rebuild (hole filled)
con.execute(f"""
CREATE TEMP TABLE cont_dd AS SELECT ticker, ts_et::DATE d, volume*close dv FROM read_parquet('{da}', union_by_name=true);
CREATE TEMP TABLE fill_dd AS
  SELECT ticker, ts_et::DATE d, volume*close dv FROM read_parquet('{da}', union_by_name=true)
  UNION ALL SELECT ticker, session_date d, close*volume dv FROM read_parquet('{rebuild}');
""")
def adv_partial(tbl):  # PARTIAL window (no min_periods) -- the ORIGINAL policy
    return f"""SELECT ticker, d, avg(dv) OVER (PARTITION BY ticker ORDER BY d ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) adv
               FROM {tbl}"""
con.execute(f"CREATE TEMP TABLE cont AS {adv_partial('cont_dd')}")
con.execute(f"CREATE TEMP TABLE fill AS {adv_partial('fill_dd')}")

ccols = con.execute(f"SELECT * FROM read_parquet('{corpus}') LIMIT 0").df().columns.tolist()
sd = "session_date"
merged = con.execute(f"""
SELECT c.ticker, CAST(c.{sd} AS DATE) d, c.year,
  co.adv adv_cont_partial, fi.adv adv_fill_partial
FROM read_parquet('{corpus}') c
LEFT JOIN cont co ON co.ticker=c.ticker AND co.d=CAST(c.{sd} AS DATE)
LEFT JOIN fill fi ON fi.ticker=c.ticker AND fi.d=CAST(c.{sd} AS DATE)
""").df()
merged["yr"] = merged.d.astype(str).str[:4]

print("=== FACTORIAL: DATA-ONLY flip (window policy held FIXED at PARTIAL, both arms) ===")
print("(vs my first-pass CONFLATED flip which mixed partial-vs-min_periods=20)\n")
for gate, lbl in [(1e6, "$1M"), (5e6, "$5M"), (1e7, "$10M")]:
    print(f"  GATE {lbl}:")
    for yr in ["2024", "2025", "2026"]:
        sub = merged[(merged.yr == yr) & merged.adv_cont_partial.notna() & merged.adv_fill_partial.notna()]
        if not len(sub):
            print(f"    {yr}: no comparable rows"); continue
        gc = sub.adv_cont_partial >= gate
        gf = sub.adv_fill_partial >= gate
        flips = int((gc != gf).sum())
        p2f = int((gc & ~gf).sum()); f2p = int((~gc & gf).sum())
        print(f"    {yr}: n={len(sub):,} DATA-only flips={flips} ({100*flips/len(sub):.3f}%)  pass->fail={p2f} fail->pass={f2p}")
# the key check: are 2024/2025 REALLY 0 under data-only (holding policy fixed)?
con.close()
