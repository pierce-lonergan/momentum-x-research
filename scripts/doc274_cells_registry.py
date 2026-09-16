"""doc 274 — gauntlet cell registry: injectors + frozen cell grid + capability matrix.

Implements the design-stress panel's required restructure (wf_8c00cf6d-1d6):
  * DUAL-MOUNT OVERLAY at the query level: hole-type mutations are date-masks applied to
    day_aggs queries; "overlay-mount" = the AUDITS apply the same mask (poisoned source —
    the registered capability number); "truth-mount" = audits see the unmasked warehouse
    (code-bug variant — reported separately as a regression test).
  * Mechanism-level P1 arms (warehouse-hole replay + ADV-window hole) instead of forged
    qualification; potency oracle (cohort-membership-indicator odll) on every membership cell.
  * P2 reduced to ONE designed positive-control arm (t+600s); the graded FP dial is
    P3 exit-statistic mixing (lambda column).
  * P4 re-taxonomized to the economics branch (pi=0.30 only).
  * P5 within-gate composition drift + the NEW D10 drift monitor (monthly KS).
  * P6 as a paired tail-deflation measurement + cross-t coupling diagnostic.
  * Null-sharing rule: row-set-preserving mutants reuse ONE clean B=200 null (spot-checked);
    membership-changing mutants get fresh B=60 nulls from the mutant spine.
  * Measurement = seed-paired continuous margins (S* - q95), not binary only.

Frozen mask windows (chosen where the warehouse HAS data; the real Jan-Mar-2026 hole is
already in the reference warehouse and is carried by the SPECIMEN cell, not re-injected):
  HOLE_REPLAY mask  : delete day_aggs sessions 2025-09-15..2025-12-05 (12wk) ->
                      re-entry day = first session after; stale-prev-close geometry replayed.
  ADV_HOLE mask     : delete 2025-10-13..2025-11-07 (4wk); qualification days
                      2025-11-17..2025-12-31 (prev close FRESH; the 20-row ADV window
                      silently reaches back across the hole).
"""
from __future__ import annotations
import json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Import BY NAME (not importlib-by-file): multiprocessing spawn workers must be able to
# re-import the module that defines the pool worker (_day_rows_mut) — an importlib-loaded
# anonymous instance is unpicklable on Windows (the P1/P2 crash class).
import mutation_gauntlet_doc274 as mg
kf = mg.kf

DAY_AGGS = "data/polygon_warehouse/day_aggs/**/*.parquet"
CLEAN_NULL_F = "data/research/doc274_clean_null_B200.parquet"
MATRIX_F = "data/research/doc274_capability_matrix.json"

HOLE_REPLAY_MASK = ("2025-09-15", "2025-12-05")
ADV_HOLE_MASK = ("2025-10-13", "2025-11-07")
ADV_HOLE_QUALIFY = ("2025-11-17", "2025-12-31")


# ─────────────────────── shared helpers ───────────────────────

def _gate_sql(mask: tuple[str, str] | None) -> str:
    """The UNMODIFIED doc-235 gate over day_aggs, optionally with a hole mask applied
    (the overlay). Returns qualifying (ticker, session_date) plus gate inputs."""
    where_mask = (f"WHERE ts_et::DATE NOT BETWEEN DATE '{mask[0]}' AND DATE '{mask[1]}'"
                  if mask else "")
    return f"""
      WITH src AS (SELECT * FROM read_parquet('{DAY_AGGS}') {where_mask}),
      b AS (SELECT ticker, ts_et::DATE d, open, close, volume,
              lag(close) OVER (PARTITION BY ticker ORDER BY ts_et) pc,
              lag(ts_et::DATE) OVER (PARTITION BY ticker ORDER BY ts_et) pd,
              avg(volume*close) OVER (PARTITION BY ticker ORDER BY ts_et
                  ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) adv20
            FROM src)
      SELECT ticker, strftime(d, '%Y-%m-%d') session_date, open, pc, adv20,
             date_diff('day', pd, d) lag_days
      FROM b WHERE pc>0 AND open/pc-1>=0.08 AND open BETWEEN 0.50 AND 20.0 AND adv20>=1e6"""


def d2_membership_mount(spine: pd.DataFrame, mask: tuple[str, str] | None) -> dict:
    """D2 with a mount: mask=None -> truth-mount; mask=hole -> overlay-mount."""
    import duckdb
    rows = spine[["ticker", "session_date"]].drop_duplicates()
    qual = duckdb.connect().execute(_gate_sql(mask)).df()
    qual_fresh = qual[qual.lag_days <= 4]
    qset = set(zip(qual.ticker, qual.session_date))
    fset = set(zip(qual_fresh.ticker, qual_fresh.session_date))
    in_gate = np.array([(t, d) in qset for t, d in zip(rows.ticker, rows.session_date)])
    fresh = np.array([(t, d) in fset for t, d in zip(rows.ticker, rows.session_date)])
    return dict(n=len(rows), gate_fail=int((~in_gate).sum()), fresh_fail=int((in_gate & ~fresh).sum()),
                fired_gate=bool((~in_gate).any()), fired_fresh=bool((in_gate & ~fresh).any()),
                fired=bool((~fresh | ~in_gate).any()))


def potency_oracle(spine: pd.DataFrame, member_keys: set) -> dict:
    """odll of the cohort-membership indicator (the run_plant oracle pattern): how much
    label information does cohort membership itself carry? ~0 -> the cell is UNINFORMATIVE.
    member_keys = set of (ticker, session_date) — keyed, never index-aligned (the index-
    alignment bug returned 0.0 on the specimen; caught in-run, fixed, recomputed)."""
    out = {}
    fams = kf._families()
    for lbl in mg.GRID5:
        s, X, y, fold = kf._per_t(spine, lbl)
        m = np.array([(t, d) in member_keys for t, d in zip(s.ticker, s.session_date)],
                     float).reshape(-1, 1)
        if m.sum() < 5:
            out[lbl] = 0.0
            continue
        dll, _ = kf._dll_curve(m, y, None, fold, {"logit": fams["logit"]})
        out[lbl] = float(dll["logit"])
    out["max"] = float(max(out.values()))
    return out


def d10_drift(spine: pd.DataFrame, ref_thr: float | None = None) -> dict:
    """NEW defense: monthly composition KS on {log_adv20, log_open_px, intensity, pm_to_open}
    vs the pooled H1 reference. Threshold = max month-vs-rest KS inside the clean H1
    (frozen from the clean cell; 1-sample calibration disclosed)."""
    from scipy.stats import ks_2samp
    cols = ["log_adv20", "log_open_px", "intensity", "pm_to_open"]
    g = spine[spine.t_lbl == "10:30"].copy()
    g["month"] = g.session_date.str[:7]
    h1 = g[g.session_date <= "2025-12-31"]
    stats = {}
    for mth, sub in g.groupby("month"):
        ref = h1[h1.month != mth] if mth <= "2025-12" else h1
        ks = max(float(ks_2samp(sub[c].dropna(), ref[c].dropna()).statistic)
                 for c in cols if sub[c].notna().sum() > 20 and ref[c].notna().sum() > 20)
        stats[mth] = ks
    h2_max = max((v for k, v in stats.items() if k >= "2026-01"), default=float("nan"))
    h1_max = max((v for k, v in stats.items() if k <= "2025-12"), default=float("nan"))
    thr = ref_thr if ref_thr is not None else h1_max
    return dict(per_month=stats, h1_max=h1_max, h2_max=h2_max, thr=float(thr),
                fired=bool(np.isfinite(h2_max) and h2_max > thr))


# ─────────────────────── injectors (frozen) ───────────────────────

def inject_p1_hole(mask, qualify_window) -> tuple[pd.DataFrame, pd.Series, dict]:
    """Admit whatever the UNMODIFIED gate admits over the holed warehouse that the clean
    universe does NOT contain; build real spine rows for the admitted cohort."""
    import duckdb
    clean = pd.read_parquet(mg.CLEAN_SPINE)
    clean = clean[clean.t_lbl.isin(mg.GRID5)].reset_index(drop=True)
    have = set(zip(clean.ticker, clean.session_date))
    qual = duckdb.connect().execute(_gate_sql(mask)).df()
    qual = qual[(qual.session_date >= qualify_window[0]) & (qual.session_date <= qualify_window[1])]
    new = qual[[(t, d) not in have for t, d in zip(qual.ticker, qual.session_date)]]
    new = new.dropna(subset=["adv20"])
    corpus_like = pd.DataFrame(dict(ticker=new.ticker, session_date=new.session_date,
                                    base_close=new.open, adv20=new.adv20))
    built = mg.rebuild_spine(corpus_like) if len(corpus_like) else pd.DataFrame()
    if len(built):
        built = built[built.t_lbl.isin(mg.GRID5)]
    spine = pd.concat([clean, built], ignore_index=True)
    member = (set(zip(built.ticker, built.session_date)) if len(built) else set())
    prov = dict(mask=mask, qualify=qualify_window, n_admitted_td=int(len(corpus_like)),
                n_admitted_rows=int(len(built)),
                admitted_days=sorted(built.session_date.unique().tolist()) if len(built) else [])
    return spine, member, prov


def inject_p2_entryvis() -> pd.DataFrame:
    """Feature boundary t+600s (designed D4 positive control); labels untouched."""
    corpus = kf._corpus()
    clean = pd.read_parquet(mg.CLEAN_SPINE)
    keys = set(zip(clean.ticker, clean.session_date))
    corpus = corpus[[(t, d) in keys for t, d in zip(corpus.ticker, corpus.session_date)]]
    return mg.rebuild_spine(corpus, feat_shift_s=600.0)


def inject_p3_exitmix(lam: float) -> tuple[pd.DataFrame, list]:
    """39th feature column: z-scored exit_vwap/last_prefix_px mixed at weight lambda with
    noise — the graded false-positive dial (label's own measurement leaked as a feature)."""
    spine = pd.read_parquet(mg.CLEAN_SPINE)
    spine = spine[spine.t_lbl.isin(mg.GRID5)].reset_index(drop=True)
    last_px = np.exp(spine.log_open_px) * (1 + spine.ret_open_t)
    exit_px = spine.entry_px * (1 + spine.gross_ret)          # exit reconstructed from label
    raw = (exit_px / last_px).replace([np.inf, -np.inf], np.nan)
    z = (raw - raw.mean()) / raw.std()
    rng = np.random.default_rng(740_001)
    spine["leak_mix"] = (lam * z.fillna(0.0) + np.sqrt(max(1 - lam ** 2, 0.0))
                         * rng.standard_normal(len(spine)))
    return spine, kf.FEATURES + ["leak_mix"]


def inject_p4_survivorship(pi: float = 0.30, seed: int = 740_002) -> pd.DataFrame:
    """Drop y=0 ticker-days w.p. pi (whole ticker-day vanishes, as delisting would)."""
    spine = pd.read_parquet(mg.CLEAN_SPINE)
    spine = spine[spine.t_lbl.isin(mg.GRID5)].reset_index(drop=True)
    g = spine[spine.t_lbl == "10:30"]
    loser = {(r.ticker, r.session_date) for r in g.itertuples()
             if pd.notna(r.gross_ret) and r.gross_ret <= kf.THETA}
    rng = np.random.default_rng(seed)
    drop = {k for k in loser if rng.random() < pi}
    keep = [(t, d) not in drop for t, d in zip(spine.ticker, spine.session_date)]
    return spine[keep].reset_index(drop=True)


def inject_p5_drift(seed: int = 740_003) -> pd.DataFrame:
    """Within-gate composition drift: drop 50% of H2 ticker-days with above-median adv20
    (gate stays formally TRUE; H2 composition shifts to the low-ADV cohort)."""
    spine = pd.read_parquet(mg.CLEAN_SPINE)
    spine = spine[spine.t_lbl.isin(mg.GRID5)].reset_index(drop=True)
    g = spine[spine.t_lbl == "10:30"]
    med = g.log_adv20.median()
    h2_hi = {(r.ticker, r.session_date) for r in g.itertuples()
             if r.session_date >= "2026-01-01" and r.log_adv20 > med}
    rng = np.random.default_rng(seed)
    drop = {k for k in h2_hi if rng.random() < 0.5}
    keep = [(t, d) not in drop for t, d in zip(spine.ticker, spine.session_date)]
    return spine[keep].reset_index(drop=True)


# ─────────────────────── null-sharing machinery ───────────────────────

def clean_null_B200() -> pd.DataFrame:
    if os.path.exists(CLEAN_NULL_F):
        return pd.read_parquet(CLEAN_NULL_F)
    spine = pd.read_parquet(mg.CLEAN_SPINE)
    spine = spine[spine.t_lbl.isin(mg.GRID5)].reset_index(drop=True)
    nd = mg.mirrored_null(spine, B=200, seed0=74_000)
    nd.to_parquet(CLEAN_NULL_F, index=False)
    return nd


def run_cell_with_null(name, spine, null_df=None, features=None, ref=None,
                       provenance=None, member=None, with_plants=True):
    """Cell runner honoring the null-sharing rule + optional 39-col feature set."""
    old_feats = kf.FEATURES
    if features is not None:
        kf.FEATURES = features
    try:
        spine = mg._grid_df(spine)
        eng = mg.engine_run(spine)
        nd = null_df if null_df is not None else mg.mirrored_null(spine, B=mg.B_CELL)
        p = float((1 + (nd.S_star >= eng["S_star"]).sum()) / (len(nd) + 1))
        thr95 = float(nd.S_star.quantile(0.95))
        rec = dict(cell=name, provenance=provenance or {},
                   S_star=eng["S_star"], S_star_at=eng["S_star_at"], I=eng["I"],
                   base=eng["base"], uncond=eng["uncond"], valid_n=eng["valid_n"],
                   null_p=p, null_thr95=thr95, margin=float(eng["S_star"] - thr95),
                   null_shared=bool(null_df is not None),
                   D1=dict(p=p, fired=bool(p < mg.DEFENSE_THRESHOLDS["D1_p"])),
                   D3=mg.d3_lodo(eng), D4=mg.d4_truncation(spine),
                   D5=mg.d5_split_half(spine, eng), D7=mg.d7_plausibility(eng),
                   D10=d10_drift(spine))
        if with_plants:
            rec["D6"] = mg.d6_plants(spine, thr95)
        if ref is not None:
            rec["D8"] = mg.d8_econ(eng, ref)
        if member is not None:
            rec["potency"] = potency_oracle(spine, member)
        os.makedirs(mg.OUTDIR, exist_ok=True)
        json.dump(rec, open(os.path.join(mg.OUTDIR, f"cell_{name}.json"), "w"),
                  indent=1, default=float)
        fired = [d for d in ("D1", "D3", "D4", "D5", "D6", "D7", "D8", "D10")
                 if d in rec and isinstance(rec[d], dict) and rec[d].get("fired")]
        print(f"CELL {name}: S*={eng['S_star']:+.4f}@{eng['S_star_at']} p={p:.3f} "
              f"margin={rec['margin']:+.4f} | fired: {fired or 'NONE'}")
        return rec
    finally:
        kf.FEATURES = old_feats


# ─────────────────────── the frozen cell grid ───────────────────────

def main(args):
    clean = pd.read_parquet(mg.CLEAN_SPINE)
    clean = clean[clean.t_lbl.isin(mg.GRID5)].reset_index(drop=True)
    ref_eng = mg.engine_run(clean)
    ref = dict(uncond=ref_eng["uncond"], base=ref_eng["base"])
    nd200 = clean_null_B200()
    print(f"clean B=200 null: q95={nd200.S_star.quantile(.95):+.5f} "
          f"q98={nd200.S_star.quantile(.98):+.5f} max={nd200.S_star.max():+.5f}")

    def want(c):
        return args.all or args.cell == c

    recs = []
    if want("CLEAN_A"):
        recs.append(run_cell_with_null("CLEAN_A", clean, nd200, ref=ref))
    if want("CLEAN_B"):   # fresh-null clean rep (end-to-end specificity incl. null machinery)
        recs.append(run_cell_with_null("CLEAN_B", clean, None, ref=ref))
    if want("SPECIMEN"):
        spec = pd.read_parquet(mg.CONTAM_SPINE)
        spec = spec[spec.t_lbl.isin(mg.GRID5)].reset_index(drop=True)
        member = {(t, d) for t, d in zip(spec.ticker, spec.session_date) if d == "2026-03-23"}
        rec = run_cell_with_null("SPECIMEN", spec, None, ref=ref, member=member,
                                 provenance=dict(source="real 2026-03-23 contamination"))
        rec["D2_truth"] = d2_membership_mount(mg._grid_df(spec), None)
        json.dump(rec, open(os.path.join(mg.OUTDIR, "cell_SPECIMEN.json"), "w"),
                  indent=1, default=float)
        recs.append(rec)
    if want("P1_ADVHOLE"):
        spine, member, prov = inject_p1_hole(ADV_HOLE_MASK, ADV_HOLE_QUALIFY)
        rec = run_cell_with_null("P1_ADVHOLE", spine, None, ref=ref, member=member,
                                 provenance=prov)
        rec["D2_overlay"] = d2_membership_mount(mg._grid_df(spine), ADV_HOLE_MASK)
        rec["D2_truth"] = d2_membership_mount(mg._grid_df(spine), None)
        json.dump(rec, open(os.path.join(mg.OUTDIR, "cell_P1_ADVHOLE.json"), "w"),
                  indent=1, default=float)
        recs.append(rec)
    if want("P1_HOLEREPLAY"):
        q = (HOLE_REPLAY_MASK[1], "2026-01-15")
        spine, member, prov = inject_p1_hole(HOLE_REPLAY_MASK, q)
        rec = run_cell_with_null("P1_HOLEREPLAY", spine, None, ref=ref, member=member,
                                 provenance=prov)
        rec["D2_overlay"] = d2_membership_mount(mg._grid_df(spine), HOLE_REPLAY_MASK)
        rec["D2_truth"] = d2_membership_mount(mg._grid_df(spine), None)
        json.dump(rec, open(os.path.join(mg.OUTDIR, "cell_P1_HOLEREPLAY.json"), "w"),
                  indent=1, default=float)
        recs.append(rec)
    if want("P2_ENTRYVIS"):
        spine = inject_p2_entryvis()
        recs.append(run_cell_with_null("P2_ENTRYVIS", spine, nd200, ref=ref,
                                       provenance=dict(feat_shift_s=600)))
    if want("P3_MIX03"):
        spine, feats = inject_p3_exitmix(0.3)
        recs.append(run_cell_with_null("P3_MIX03", spine, None, features=feats, ref=ref,
                                       provenance=dict(lam=0.3, note="fresh null spot-check cell")))
    if want("P3_MIX01"):
        spine, feats = inject_p3_exitmix(0.1)
        recs.append(run_cell_with_null("P3_MIX01", spine, nd200, features=feats + [], ref=ref,
                                       provenance=dict(lam=0.1)))
    if want("P4_SURV"):
        spine = inject_p4_survivorship(0.30)
        recs.append(run_cell_with_null("P4_SURV", spine, None, ref=ref,
                                       provenance=dict(pi=0.30)))
    if want("P5_DRIFT"):
        spine = inject_p5_drift()
        recs.append(run_cell_with_null("P5_DRIFT", spine, None, ref=ref,
                                       provenance=dict(rule="drop 50% of H2 above-median-ADV")))
    if want("P6_NULLFID"):
        # paired tail-deflation: asymmetric (273-original) vs mirrored null on identical data
        spine = clean
        packs = {}
        for lbl in mg.GRID5:
            s = spine[spine.t_lbl == lbl].reset_index(drop=True)
            X = s[kf.FEATURES].to_numpy(float)
            gross = s.gross_ret.to_numpy(float)
            day_codes = pd.factorize(s.session_date)[0]
            fold = s.session_date.map(kf._fold_of_day(sorted(s.session_date.unique()))).to_numpy()
            feat_ok = ~np.isnan(X).all(axis=1)
            packs[lbl] = (X[feat_ok], gross[feat_ok], day_codes[feat_ok], fold[feat_ok])
        from joblib import Parallel, delayed

        def asym_rep(seed):
            rng = np.random.default_rng(seed)
            fams = kf._families()
            per_t = {}
            for lbl, (Xa, ga, dc, fd) in packs.items():
                g = ga.copy()
                for c in np.unique(dc):
                    m = np.where(dc == c)[0]
                    g[m] = g[rng.permutation(m)]
                ok = ~np.isnan(g)
                yb = (g[ok] > kf.THETA).astype(int)
                if len(yb) < 200 or yb.sum() in (0, len(yb)):
                    per_t[lbl] = np.nan
                    continue
                dll, _ = kf._dll_curve(Xa[ok], yb, None, fd[ok], fams)
                per_t[lbl] = float(np.nanmax(list(dll.values())))
            return max(v for v in per_t.values() if v == v)

        asym = Parallel(n_jobs=mg.N_JOBS)(delayed(asym_rep)(74_500 + b) for b in range(60))
        asym = pd.Series(asym)
        ratio = float(nd200.S_star.quantile(.95) - asym.quantile(.95))
        # cross-t coupling diagnostic: mirrored null preserves it; asym destroys it
        lbl_corr = clean.pivot_table(index=["ticker", "session_date"], columns="t_lbl",
                                     values="gross_ret")
        real_coupling = float(lbl_corr.corr().where(
            ~np.eye(len(mg.GRID5), dtype=bool)).stack().mean())
        rec = dict(cell="P6_NULLFID",
                   q95_mirrored=float(nd200.S_star.quantile(.95)),
                   q95_asym=float(asym.quantile(.95)),
                   tail_deflation=float(nd200.S_star.quantile(.95) - asym.quantile(.95)),
                   real_label_cross_t_coupling=real_coupling,
                   note="mirrored preserves cross-t coupling (~real); asym destroys it; "
                        "deflation>0 = asym null is anti-conservative")
        json.dump(rec, open(os.path.join(mg.OUTDIR, "cell_P6_NULLFID.json"), "w"),
                  indent=1, default=float)
        print(f"CELL P6_NULLFID: q95 mirrored {rec['q95_mirrored']:+.5f} vs asym "
              f"{rec['q95_asym']:+.5f} -> deflation {rec['tail_deflation']:+.5f} | "
              f"real cross-t coupling {real_coupling:+.2f}")
        recs.append(rec)

    if args.matrix:
        cells = {}
        for f in sorted(os.listdir(mg.OUTDIR)):
            if f.startswith("cell_") and f.endswith(".json"):
                cells[f[5:-5]] = json.load(open(os.path.join(mg.OUTDIR, f)))
        json.dump(cells, open(MATRIX_F, "w"), indent=1, default=float)
        print(f"WROTE {MATRIX_F} ({len(cells)} cells)")
    return recs
