"""doc 275 — THE GATE (core module, pre-registered research, DORMANT-C, read-only vs production).

273 measured what the tape can know; 274 measured what the engine can certify; 275 builds and
certifies the GATE that makes a hundred-experiment factory sound: fleet-level inference under
the TRUE cross-experiment dependence (shared days + shared universe), via ONE shared set of
within-day label permutations applied to every fleet member (Westfall-Young at factory scale).

This module holds the panel-independent cores, written while the design-stress panel runs;
the cell orchestration (fleet sweep, ladder, contamination arm) is appended post-prereg:

  * shared_permutations(spine, B, seed): B shared within-day ticker-donor mappings (the
    doc-273 mirrored construction) materialized as donor-index arrays per grid time.
  * tier1_members / tier1_null_matrix: fixed random linear-score members; because the score
    never sees labels, each member's top-decile row set is FIXED, and the (B x M) null matrix
    is pure index arithmetic over permuted-label vectors (vectorized; thousands of members).
  * thresholds: naive per-member q95 vs fleet-wide Westfall-Young q95 of max-over-members.
  * d_repro: the reproduce-from-stated-features organ (the missing auditor 274's existence
    proof specified), plus its validation harness against 274's own cell artifacts.

Membership burden: the fleet runs on the IMMUNE-FILTERED doc-273 spine — filtered by the
engine_immune_system criteria (gate + prev-close freshness + ADV-window span), explicitly
labelled "immune-filtered", NOT "clean" (the 274 lesson: the clean spine carried the
ADV-window residue). The as-is-vs-filtered contamination arm is the discharge made into a
measurement.
"""
from __future__ import annotations
import argparse, json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "kf", os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowability_frontier_doc273.py"))
kf = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(kf)

SPINE_ASIS = "data/research/doc273_spine.parquet"          # as-is (carries 2026-03-23 + residue)
SPINE_273CLEAN = "data/research/doc273_spine_clean.parquet"  # 273's lag<=4d filter only
OUTDIR = "data/research/doc275"
GRID5 = ["9:40", "10:30", "12:00", "14:00", "15:00"]
THETA = kf.THETA


# ─────────────────── immune filter (the 274-informed membership discharge) ───────────────────

def immune_filtered_spine() -> pd.DataFrame:
    """273-clean spine additionally stripped of ADV-window-bridged ticker-days (the residue
    274's blinded fleets found): drop any (ticker, session_date) whose day_aggs trailing-20-ROW
    window spans > 40 calendar days (hole-bridging) or whose prev close is stale."""
    import duckdb
    spine = pd.read_parquet(SPINE_273CLEAN)
    spine = spine[spine.t_lbl.isin(GRID5)].reset_index(drop=True)
    aud = duckdb.connect().execute("""
      WITH b AS (
        SELECT ticker, ts_et::DATE d,
          lag(ts_et::DATE) OVER (PARTITION BY ticker ORDER BY ts_et) pd,
          (ts_et::DATE - lag(ts_et::DATE, 20) OVER (PARTITION BY ticker ORDER BY ts_et)) win_span
        FROM read_parquet('data/polygon_warehouse/day_aggs/**/*.parquet'))
      SELECT ticker, strftime(d, '%Y-%m-%d') session_date,
             (pd IS NULL OR date_diff('day', pd, d) > 4) stale,
             (win_span IS NULL OR win_span > 40) bridged
      FROM b""").df()
    bad = {(r.ticker, r.session_date) for r in aud.itertuples() if r.stale or r.bridged}
    keep = [(t, d) not in bad for t, d in zip(spine.ticker, spine.session_date)]
    out = spine[keep].reset_index(drop=True)
    print(f"immune filter: {len(spine)//len(GRID5)} -> {len(out)//len(GRID5)} ticker-days "
          f"({(len(spine)-len(out))//len(GRID5)} dropped)")
    return out


# ─────────────────── shared permutations (the dependence-preserving core) ───────────────────

def build_packs(spine: pd.DataFrame) -> dict:
    """Per grid time: valid rows (own entry valid), X, net returns, day codes, and the
    full-day ticker->row lookup needed for shared donor mappings."""
    if "day" not in spine.columns:
        spine = spine.assign(day=spine.session_date)
    packs = {}
    for lbl in GRID5:
        s, X, y, fold = kf._per_t(spine, lbl)
        net = s.gross_ret.to_numpy(float) - THETA
        packs[lbl] = dict(s=s, X=X, y=y, fold=fold, net=net,
                          day=s.session_date.to_numpy(), ticker=s.ticker.to_numpy())
    return packs


def shared_permutations(spine: pd.DataFrame, packs: dict, B: int, seed: int = 275_000) -> dict:
    """B shared within-day ticker permutations, materialized as donor ROW indices per t.

    One mapping per (day, permutation) shared across ALL fleet members and ALL grid times —
    this is what preserves the true cross-member and cross-t dependence. Donor rows must be
    valid at the same t (own-valid AND donor-valid, the 273 mirrored construction); rows whose
    donor is invalid at t get index -1 (excluded from that permutation's metric).
    Returns {t_lbl: int32 array (B, n_valid_rows)} of donor row indices into packs[t]['net'].
    """
    days_all = sorted(spine.session_date.unique())
    tickers_by_day = {d: sorted(spine[spine.session_date == d].ticker.unique()) for d in days_all}
    out = {}
    for lbl in GRID5:
        p = packs[lbl]
        row_of = {}
        for i, (d, t) in enumerate(zip(p["day"], p["ticker"])):
            row_of[(d, t)] = i
        n = len(p["net"])
        donors = np.full((B, n), -1, dtype=np.int32)
        rng = np.random.default_rng(seed)
        for b in range(B):
            perm = {}
            for d in days_all:
                tk = tickers_by_day[d]
                shuffled = [tk[i] for i in rng.permutation(len(tk))]
                perm[d] = dict(zip(tk, shuffled))
            for i, (d, t) in enumerate(zip(p["day"], p["ticker"])):
                j = row_of.get((d, perm[d].get(t)))
                if j is not None:
                    donors[b, i] = j
        out[lbl] = donors
    return out


# ─────────────────── Tier-1 fleet (fixed random scores; vectorized nulls) ───────────────────

def tier1_members(M: int, n_features: int = 38, sparsity: int = 5, seed: int = 275_100):
    """M fixed random linear-score members: (t_lbl index, weight vector over features).
    Scores never see labels — the member's ranking of rows is label-independent."""
    rng = np.random.default_rng(seed)
    members = []
    for m in range(M):
        t_idx = int(rng.integers(len(GRID5)))
        idx = rng.choice(n_features, size=sparsity, replace=False)
        w = np.zeros(n_features)
        w[idx] = rng.choice([-1.0, 1.0], size=sparsity) * rng.uniform(0.5, 1.5, size=sparsity)
        members.append(dict(id=m, t_lbl=GRID5[t_idx], w=w))
    return members


def tier1_stats(members, packs, donors, top_frac: float = 0.10):
    """For every member: the REAL money statistic (top-decile mean net, winsorized 1/99)
    and its B-vector of shared-permutation null statistics. Vectorized: the member's
    top-decile row set is fixed; each null value = mean of donor-permuted winsorized net
    over those rows (donor -1 rows dropped)."""
    # pre-winsorize nets per t (fixed clips — identical for real and null, per the 273 recipe)
    wnet = {lbl: kf._wins(packs[lbl]["net"]) for lbl in GRID5}
    real = np.zeros(len(members))
    null = np.zeros((len(members), donors[GRID5[0]].shape[0]))
    # rank-gauss the features once per t for score stability
    from sklearn.preprocessing import QuantileTransformer
    Xq = {}
    for lbl in GRID5:
        X = packs[lbl]["X"]
        qt = QuantileTransformer(output_distribution="normal",
                                 n_quantiles=min(1000, len(X)), random_state=0)
        Xi = np.nan_to_num(X, nan=0.0)
        Xq[lbl] = qt.fit_transform(Xi)
    for k, m in enumerate(members):
        lbl = m["t_lbl"]
        score = Xq[lbl] @ m["w"]
        thr = np.quantile(score, 1 - top_frac)
        top = np.where(score >= thr)[0]
        real[k] = float(wnet[lbl][top].mean())
        D = donors[lbl][:, top]                    # (B, |top|) donor indices
        valid = D >= 0
        vals = np.where(valid, wnet[lbl][np.clip(D, 0, None)], np.nan)
        null[k] = np.nanmean(vals, axis=1)
    return real, null


def thresholds(real: np.ndarray, null: np.ndarray, alpha: float = 0.05) -> dict:
    """Naive per-member vs fleet-wide Westfall-Young decisions.

    naive: member k 'discovers' if real[k] > q_{1-alpha} of its OWN null row.
    deflated: family threshold = q_{1-alpha} over b of max_k null[k, b] (shared perms ->
    exact dependence); member discovers if real[k] > that.
    """
    B = null.shape[1]
    naive_thr = np.quantile(null, 1 - alpha, axis=1)
    naive_disc = real > naive_thr
    fleet_max = np.nanmax(null, axis=0)            # per permutation, max over members
    wy_thr = float(np.quantile(fleet_max, 1 - alpha))
    wy_disc = real > wy_thr
    return dict(naive_discoveries=int(naive_disc.sum()), naive_rate=float(naive_disc.mean()),
                wy_threshold=wy_thr, wy_discoveries=int(wy_disc.sum()),
                naive_thr_median=float(np.median(naive_thr)), B=B, M=len(real))


# ─────────────────── D-REPRO (the organ 274's existence proof specified) ───────────────────

def d_repro(spine_path: str, feature_list: list[str], reported_I: dict,
            tol: float = 1e-9) -> dict:
    """Reproduce-from-stated-features: re-derive the engine statistic from EXACTLY the
    declared feature list; any mismatch vs the reported numbers = flag. The organ that
    flips the sign on the R4 mutant and that the doc-273-class engine lacked."""
    spine = pd.read_parquet(spine_path)
    spine = spine[spine.t_lbl.isin(GRID5)].reset_index(drop=True)
    if "day" not in spine.columns:
        spine = spine.assign(day=spine.session_date)
    old = kf.FEATURES
    kf.FEATURES = feature_list
    try:
        fams = kf._families()
        mism = {}
        for lbl, rep in reported_I.items():
            if lbl not in GRID5:
                continue
            s, X, y, fold = kf._per_t(spine, lbl)
            dll, _ = kf._dll_curve(X, y, None, fold, fams)
            for fam, v in dll.items():
                r = rep.get(fam)
                if r is not None and abs(v - r) > tol:
                    mism[f"{lbl}/{fam}"] = dict(recomputed=float(v), reported=float(r),
                                                delta=float(v - r))
        return dict(checked=len(reported_I), mismatches=mism, fired=bool(mism))
    finally:
        kf.FEATURES = old


def validate_d_repro() -> dict:
    """Certification harness on 274's own artifacts: must CATCH the R4 hidden-column mutant
    (reported stats used 39 features, declared list has 38), must PASS clean cells, and must
    correctly NOT catch P2_ENTRYVIS (in-feature look-ahead: the declared features themselves
    are corrupt — reproducing from them MATCHES the report; that class belongs to D4)."""
    out = {}
    r4 = "data/research/doc274_d9/R4"
    if os.path.exists(os.path.join(r4, "engine_result.json")):
        rep = json.load(open(os.path.join(r4, "engine_result.json")))
        out["R4_mutant"] = d_repro(os.path.join(r4, "spine.parquet"), list(kf.FEATURES),
                                   rep.get("I", {}), tol=1e-6)
        out["R4_expected"] = "fired=True (hidden column -> statistic not reproducible)"
    ca = "data/research/doc274_cells/cell_CLEAN_A.json"
    if os.path.exists(ca):
        rep = json.load(open(ca))
        out["CLEAN_A"] = d_repro(SPINE_273CLEAN, list(kf.FEATURES), rep.get("I", {}), tol=1e-6)
        out["CLEAN_A_expected"] = "fired=False (clean artifact reproduces exactly)"
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate-drepro", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUTDIR, exist_ok=True)
    if a.validate_drepro:
        res = validate_d_repro()
        json.dump(res, open(os.path.join(OUTDIR, "drepro_validation.json"), "w"),
                  indent=1, default=float)
        for k, v in res.items():
            if isinstance(v, dict):
                print(f"{k}: fired={v.get('fired')} mismatches={len(v.get('mismatches', {}))}")
            else:
                print(f"{k}: {v}")
    if a.smoke:
        spine = immune_filtered_spine()
        packs = build_packs(spine)
        donors = shared_permutations(spine, packs, B=20)
        members = tier1_members(50)
        real, null = tier1_stats(members, packs, donors)
        print("smoke:", thresholds(real, null))
