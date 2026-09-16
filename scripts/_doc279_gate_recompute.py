"""doc279 retest amendment: recompute the 2026 EOD-return cell (the load-bearing expectancy/selection null)
at EACH gate ($1M/$5M/$10M) on the CORRECTED data-only basis (partial window, both arms), not just $1M.
The hostile retest found my $1M-only recompute measured the smallest, wrong-signed effect; at $5M/$10M decontam
ADMITS fail->pass names -> the 2026 cell can SHARPEN. This measures it honestly + paired block-bootstrap CI on the
CHANGE (kills the phantom-CI-wobble the panel flagged). Read-only, deterministic RNG."""
import duckdb, numpy as np
D = "data/research/doc279"; WH = "data/polygon_warehouse"
con = duckdb.connect(); con.execute("SET threads=4")
corpus = "data/research/exit_labels_cross_regime.parquet"
rebuild = f"{D}/../doc278/day_aggs_q1_rebuild.parquet"
da = f"{WH}/day_aggs/**/*.parquet".replace("\\", "/")
con.execute(f"""
CREATE TEMP TABLE cont_dd AS SELECT ticker, ts_et::DATE d, volume*close dv FROM read_parquet('{da}', union_by_name=true);
CREATE TEMP TABLE fill_dd AS
  SELECT ticker, ts_et::DATE d, volume*close dv FROM read_parquet('{da}', union_by_name=true)
  UNION ALL SELECT ticker, session_date d, close*volume dv FROM read_parquet('{rebuild}');
CREATE TEMP TABLE cont AS SELECT ticker,d, avg(dv) OVER (PARTITION BY ticker ORDER BY d ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) adv FROM cont_dd;
CREATE TEMP TABLE fill AS SELECT ticker,d, avg(dv) OVER (PARTITION BY ticker ORDER BY d ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) adv FROM fill_dd;
""")
df = con.execute(f"""
SELECT c.ticker, CAST(c.session_date AS DATE) d, c.ret_session, co.adv adv_c, fi.adv adv_f
FROM read_parquet('{corpus}') c
LEFT JOIN cont co ON co.ticker=c.ticker AND co.d=CAST(c.session_date AS DATE)
LEFT JOIN fill fi ON fi.ticker=c.ticker AND fi.d=CAST(c.session_date AS DATE)
WHERE CAST(c.session_date AS DATE) >= DATE '2026-01-01'
""").df()
con.close()
df = df.dropna(subset=["adv_c", "adv_f", "ret_session"])
rng = np.random.RandomState(279)

def paired_boot(sub_c, sub_f, B=2000):
    """paired day-block bootstrap of the CHANGE in mean ret_session (resample days once, apply to both arms)."""
    days = np.union1d(sub_c.d.unique(), sub_f.d.unique())
    cby = {dd: sub_c[sub_c.d == dd].ret_session.values for dd in days}
    fby = {dd: sub_f[sub_f.d == dd].ret_session.values for dd in days}
    ch = []
    for _ in range(B):
        samp = rng.choice(days, size=len(days), replace=True)
        cv = np.concatenate([cby[dd] for dd in samp if len(cby[dd])]) if any(len(cby[dd]) for dd in samp) else np.array([np.nan])
        fv = np.concatenate([fby[dd] for dd in samp if len(fby[dd])]) if any(len(fby[dd]) for dd in samp) else np.array([np.nan])
        ch.append(np.nanmean(fv) - np.nanmean(cv))
    return round(float(np.percentile(ch, 2.5)) * 100, 3), round(float(np.percentile(ch, 97.5)) * 100, 3)

print("=== 2026 EOD-return cell: contaminated vs decontam universe, mean ret_session, PER GATE ===")
print("(the load-bearing expectancy/selection null -- doc 249/250/251/258; cost bar ~1%)\n")
for gate, lbl in [(1e6, "$1M"), (5e6, "$5M"), (1e7, "$10M")]:
    sc = df[df.adv_c >= gate]; sf = df[df.adv_f >= gate]
    mc = float(np.nanmean(sc.ret_session)) * 100; mf = float(np.nanmean(sf.ret_session)) * 100
    n_adm = int(((df.adv_c < gate) & (df.adv_f >= gate)).sum())
    n_ej = int(((df.adv_c >= gate) & (df.adv_f < gate)).sum())
    adm_ret = float(np.nanmean(df[(df.adv_c < gate) & (df.adv_f >= gate)].ret_session)) * 100 if n_adm else float("nan")
    ej_ret = float(np.nanmean(df[(df.adv_c >= gate) & (df.adv_f < gate)].ret_session)) * 100 if n_ej else float("nan")
    ci = paired_boot(sc, sf)
    rel = (mf - mc) / abs(mc) * 100 if mc else float("nan")
    print(f"  GATE {lbl}: n_c={len(sc):,} n_f={len(sf):,}")
    print(f"    mean ret_session: {mc:+.3f}% -> {mf:+.3f}%  (Δ={mf-mc:+.3f}pp, {rel:+.0f}% rel)  paired ΔCI95[{ci[0]:+.3f},{ci[1]:+.3f}]pp")
    print(f"    admitted(fail->pass) n={n_adm} mean_ret={adm_ret:+.3f}% | ejected(pass->fail) n={n_ej} mean_ret={ej_ret:+.3f}%")
    verdict_moves = not (ci[0] <= 0 <= ci[1])
    print(f"    change CI excludes 0? {verdict_moves}  | either cell clears the ~1% cost bar? {max(mc,mf) > 1.0}\n")
