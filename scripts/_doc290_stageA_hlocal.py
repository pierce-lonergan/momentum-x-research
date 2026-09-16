"""DOC 290 STAGE A — the H-LOCAL existence test. Gates FROZEN in scripts/_doc290_PREREG.md
(sha256 47b0aa9528b4d21038b0a2496e20373add41a6be704084d93da710f835c868dd, commit 167a911) BEFORE this ran.

Question: does local neighborhood conditioning (k-NN / global+local-residual hybrid) add payoff information
beyond a strong global model? If no — the vector-DB micro-strategy architecture is dead for this universe.

Also reports (descriptive, either way): the information-vs-scale curve (pure kNN at fixed k — the empirical
"how many micro-trends does the data support") and the selectivity curve (net-of-floor payoff at top
2/5/10/20% — the honest answer to "one trade per month").
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.neighbors import KNeighborsRegressor

_ROOT = Path(__file__).resolve().parent.parent
OUT = _ROOT / "data" / "research" / "doc290"
SEED = 290
N_FOLDS = 5
B_PERM = 200
B_BOOT = 5000
COST_FLOOR = 0.015
REGIME_SPLIT = "2026-05-26"
K_GRID = [5, 10, 25, 50, 100, 200]
K_CURVE = [5, 10, 25, 50, 100, 200, 500]

ABS_F = ["gap_pct", "rvol", "log_price", "log_float", "log_pmv", "log_mcap", "has_news", "mfcs",
         "float_rotation", "miss_float", "miss_fr", "miss_mcap"]
ATT_F = ["pm_dvol_share", "cohort_pm_herfindahl", "cohort_size", "cohort_sd_gap_pct", "cohort_sd_rvol",
         "cohort_sd_float_rotation", "centroid_dist", "z_gap_pct", "z_rvol", "z_price", "z_mfcs",
         "z_float_rotation", "rank_gap_pct", "rank_rvol", "rank_price", "rank_mfcs",
         "rank_float_rotation", "dvol15_share"]
PATH_F = ["r5", "r10", "r15", "maxdrawup15", "maxdd15", "range15", "vwap_dev15", "higher_low_frac",
          "vol_slope15"]
FEATS = ABS_F + ATT_F + PATH_F


def load():
    rows = [json.loads(l) for l in (OUT / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for r in rows:
        r["log_price"] = math.log(max(r.get("price") or 0.01, 0.01))
        fl, pmv, mc, fr = r.get("float_shares"), r.get("premarket_volume"), r.get("market_cap"), r.get("float_rotation")
        r["miss_float"] = 0.0 if fl else 1.0
        r["miss_fr"] = 0.0 if fr is not None else 1.0
        r["miss_mcap"] = 0.0 if mc else 1.0
        r["log_float"] = math.log(fl) if fl else None
        r["log_pmv"] = math.log(pmv) if pmv and pmv > 0 else None
        r["log_mcap"] = math.log(mc) if mc and mc > 0 else None
    return rows


def matrix(rows):
    return np.array([[(r.get(f) if r.get(f) is not None else np.nan) for f in FEATS] for r in rows], float)


def impute_scale(Xtr, Xte):
    med = np.nanmedian(Xtr, axis=0); med = np.where(np.isnan(med), 0.0, med)
    Xtr = np.where(np.isnan(Xtr), med, Xtr); Xte = np.where(np.isnan(Xte), med, Xte)
    mu, sd = Xtr.mean(0), Xtr.std(0); sd = np.where(sd < 1e-9, 1.0, sd)
    return (Xtr - mu) / sd, (Xte - mu) / sd


def fold_assign(sessions, folds, seed):
    uniq = sorted(set(sessions))
    rng = np.random.default_rng(seed); rng.shuffle(uniq)
    fold_of = {s: i % folds for i, s in enumerate(uniq)}
    return np.array([fold_of[s] for s in sessions])


def inner_k(Xtr, ytr, sess_tr, grid, seed):
    """inner grouped 3-fold on TRAIN ONLY to select k (no outer leak)."""
    fold = fold_assign(list(sess_tr), 3, seed)
    best_k, best = grid[0], -np.inf
    for k in grid:
        if k >= len(ytr):
            continue
        preds = np.full(len(ytr), np.nan)
        for j in range(3):
            tr, te = fold != j, fold == j
            if tr.sum() < k + 1 or te.sum() == 0:
                continue
            a, b = impute_scale(Xtr[tr], Xtr[te])
            preds[te] = KNeighborsRegressor(n_neighbors=k).fit(a, ytr[tr]).predict(b)
        v = ~np.isnan(preds)
        if v.sum() > 20:
            rho = spearmanr(preds[v], ytr[v])[0]
            if rho is not None and rho > best:
                best, best_k = rho, k
    return best_k


def oos_scores(rows, X, y, sessions, seed, models=("global", "knn", "hybrid")):
    """grouped 5-fold OOS scores for each requested model. Returns dict name->score array (+chosen ks)."""
    fold = fold_assign(sessions, N_FOLDS, seed)
    out = {m: np.full(len(y), np.nan) for m in models}
    ks = []
    sess_arr = np.array(sessions)
    for kf in range(N_FOLDS):
        tr, te = fold != kf, fold == kf
        if te.sum() == 0 or tr.sum() < 50:
            continue
        Xtr, Xte = impute_scale(X[tr], X[te])
        gbm = None
        if "global" in models or "hybrid" in models:
            gbm = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, min_samples_leaf=20,
                                                l2_regularization=1.0, random_state=SEED)
            gbm.fit(Xtr, y[tr])
            if "global" in models:
                out["global"][te] = gbm.predict(Xte)
        if "knn" in models:
            k = inner_k(X[tr], y[tr], sess_arr[tr], K_GRID, seed + kf)
            ks.append(k)
            out["knn"][te] = KNeighborsRegressor(n_neighbors=k).fit(Xtr, y[tr]).predict(Xte)
        if "hybrid" in models:
            resid = y[tr] - gbm.predict(Xtr)
            k2 = inner_k(X[tr], resid, sess_arr[tr], K_GRID, seed + 100 + kf)
            out["hybrid"][te] = gbm.predict(Xte) + KNeighborsRegressor(n_neighbors=k2).fit(Xtr, resid).predict(Xte)
    return out, ks


def m1(score, y):
    v = ~np.isnan(score)
    return spearmanr(score[v], y[v])[0] if v.sum() > 20 else np.nan


def m2_topdecile(score, ret_close, floor=COST_FLOOR):
    v = ~np.isnan(score)
    s, r = score[v], ret_close[v]
    thr = np.quantile(s, 0.9)
    sel = r[s >= thr]
    return float(sel.mean() - floor), int(len(sel))


def session_boot_ci(vals, sess, B=B_BOOT, seed=SEED):
    """bootstrap over SESSIONS of the mean."""
    bysess = {}
    for v, s in zip(vals, sess):
        bysess.setdefault(s, []).append(v)
    keys = sorted(bysess)
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(B):
        pick = rng.choice(len(keys), len(keys), replace=True)
        pool = [x for i in pick for x in bysess[keys[i]]]
        means.append(np.mean(pool))
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def shuffle_within_session(y, sessions, rng):
    yp = y.copy()
    sess_arr = np.array(sessions)
    for s in set(sessions):
        idx = np.where(sess_arr == s)[0]
        yp[idx] = yp[rng.permutation(idx)]
    return yp


def main():
    rows = load()
    sessions = [r["date"] for r in rows]
    X = matrix(rows)
    y = np.array([r["peak_run60"] for r in rows])
    rc = np.array([r["ret_close"] for r in rows])

    # ---- point estimates -----------------------------------------------------
    sc, ks = oos_scores(rows, X, y, sessions, SEED)
    M1 = {m: float(m1(sc[m], y)) for m in sc}
    M2 = {m: m2_topdecile(sc[m], rc) for m in sc}
    d_knn = M1["knn"] - M1["global"]
    d_hyb = M1["hybrid"] - M1["global"]

    # ---- G1: within-session permutation null (full pipeline recompute) -------
    rng = np.random.default_rng(SEED)
    null_knn, null_hyb = [], []
    for b in range(B_PERM):
        yp = shuffle_within_session(y, sessions, rng)
        scp, _ = oos_scores(rows, X, yp, sessions, 1000 + b)
        g = m1(scp["global"], yp)
        null_knn.append(m1(scp["knn"], yp) - g)
        null_hyb.append(m1(scp["hybrid"], yp) - g)
    null_knn, null_hyb = np.array(null_knn), np.array(null_hyb)
    p_knn = float((1 + (null_knn >= d_knn).sum()) / (1 + B_PERM))
    p_hyb = float((1 + (null_hyb >= d_hyb).sum()) / (1 + B_PERM))
    alpha = 0.01 / 2  # Bonferroni x2 (frozen)
    G1 = (p_knn < alpha) or (p_hyb < alpha)

    # ---- G2: cross-regime replication ----------------------------------------
    def regime_delta(side):
        idx = [i for i, s in enumerate(sessions) if (s < REGIME_SPLIT) == (side == "early")]
        rsub = [rows[i] for i in idx]
        scs, _ = oos_scores(rsub, X[idx], y[idx], [sessions[i] for i in idx], SEED)
        g = m1(scs["global"], y[idx])
        return {"knn": float(m1(scs["knn"], y[idx]) - g), "hybrid": float(m1(scs["hybrid"], y[idx]) - g)}
    reg_early, reg_late = regime_delta("early"), regime_delta("late")
    best_local = "hybrid" if d_hyb >= d_knn else "knn"
    G2 = (reg_early[best_local] > 0) and (reg_late[best_local] > 0)

    # ---- G3: top-decile net of floor, session-blocked bootstrap --------------
    v = ~np.isnan(sc[best_local])
    s_, r_, se_ = sc[best_local][v], rc[v], np.array(sessions)[v]
    thr = np.quantile(s_, 0.9)
    sel_r, sel_s = r_[s_ >= thr], se_[s_ >= thr]
    net_mean = float(sel_r.mean() - COST_FLOOR)
    ci_lo, ci_hi = session_boot_ci(sel_r - COST_FLOOR, sel_s)
    G3 = (net_mean > 0) and (ci_lo > 0)

    STAGE_A = bool(G1 and G2 and G3)

    # ---- descriptive: information-vs-scale curve (pure kNN, fixed k) ---------
    curve = {}
    for k in K_CURVE:
        if k >= len(y):
            continue
        fold = fold_assign(sessions, N_FOLDS, SEED)
        pred = np.full(len(y), np.nan)
        for kf in range(N_FOLDS):
            tr, te = fold != kf, fold == kf
            Xtr, Xte = impute_scale(X[tr], X[te])
            pred[te] = KNeighborsRegressor(n_neighbors=k).fit(Xtr, y[tr]).predict(Xte)
        curve[k] = round(float(m1(pred, y)), 4)

    # ---- descriptive: selectivity curve --------------------------------------
    selcurve = {}
    for model in ["global", best_local]:
        vv = ~np.isnan(sc[model])
        ss, rr = sc[model][vv], rc[vv]
        row = {}
        for pct in [0.02, 0.05, 0.10, 0.20]:
            t = np.quantile(ss, 1 - pct)
            pick = rr[ss >= t]
            row[f"top{int(pct*100)}pct"] = {"net_mean": round(float(pick.mean() - COST_FLOOR), 4), "n": int(len(pick))}
        selcurve[model] = row

    res = {
        "prereg_sha256": "47b0aa9528b4d21038b0a2496e20373add41a6be704084d93da710f835c868dd",
        "n_events": len(rows), "n_sessions": len(set(sessions)), "n_features": len(FEATS),
        "M1_spearman": {k: round(v, 4) for k, v in M1.items()},
        "M2_topdecile_net": {k: [round(v[0], 4), v[1]] for k, v in M2.items()},
        "delta_knn_minus_global": round(d_knn, 4), "delta_hybrid_minus_global": round(d_hyb, 4),
        "knn_inner_k_chosen": ks,
        "perm": {"B": B_PERM, "p_knn": p_knn, "p_hybrid": p_hyb, "alpha_bonferroni": alpha,
                 "null_knn_p95": round(float(np.percentile(null_knn, 95)), 4),
                 "null_hyb_p95": round(float(np.percentile(null_hyb, 95)), 4)},
        "regime": {"early": reg_early, "late": reg_late, "best_local": best_local},
        "G3": {"net_mean_topdecile": round(net_mean, 4), "ci95": [round(ci_lo, 4), round(ci_hi, 4)],
               "n_selected": int(len(sel_r))},
        "gates": {"G1": bool(G1), "G2": bool(G2), "G3": bool(G3)},
        "STAGE_A_PASS": STAGE_A,
        "info_vs_scale_curve_spearman": curve,
        "selectivity_curve_net_of_floor": selcurve,
    }
    (OUT / "stageA_result.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
