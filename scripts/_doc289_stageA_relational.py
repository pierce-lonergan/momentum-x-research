"""DOC 289 STAGE A — the RELATIONAL EXISTENCE TEST (pre-registered; acceptance frozen BELOW before any run).

Question: does a name's WITHIN-COHORT RELATIVE position carry information about WHICH name runs, BEYOND its
absolute per-name features? Every prior experiment used the absolute representation and found no edge. If the
market's informative representation is cohort-relative, per-name models were structurally blind to it.

Design (leakage-hard, cross-regime, permutation-nulled):
- Target: is_winner (one per cohort = argmax intraday peak-run). Relational by construction.
- ABS features = raw per-name (gap, rvol, log price/float/pmv, mfcs, has_news, float_rotation + missing-indicators).
- REL features = within-cohort normalization of the same info (ranks, z-scores, centroid distance, cohort dispersion).
- Metric: winner-identification accuracy under GROUPED 5-fold CV by session (a cohort never spans train/test);
  random baseline = mean(1/cohort_size). Secondary: pooled AUC on is_winner.
- Null: shuffle is_winner WITHIN each cohort (preserves cohort structure + absolute marginals), rerun the full
  grouped-CV, B times -> null distribution of accuracy and of the REL-beyond-ABS lift.
- Cross-regime gate: train early (< split), test late (>= split) -- the acid test that has killed every prior edge.

FROZEN ACCEPTANCE (pre-registered; do not tune after seeing results):
  G1 predictability-exists : max(acc_REL, acc_BOTH) > random AND permutation p(acc) < 0.01
  G2 relational-beyond-abs : acc_BOTH - acc_ABS > 0 AND permutation p(lift) < 0.05
  G3 cross-regime-survival : on the late held-out regime, acc_BOTH > acc_ABS AND acc_BOTH > random
  PASS = G1 and G2 and G3  -> proceed to Stage B (capacity -> dollars)
  else -> honest negative; the observed lift magnitude BOUNDS any relational edge (a knowability result).
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

_ROOT = Path(__file__).resolve().parent.parent
OUT = _ROOT / "data" / "research" / "doc289"
SPLIT = "2026-05-26"      # frozen regime split (Stage-0 chronological midpoint)
B_PERM = 500             # permutation draws
SEED = 289
N_FOLDS = 5

ABS_FEATS = ["gap_pct", "rvol", "log_price", "log_float", "log_pmv", "mfcs", "has_news",
             "float_rotation", "miss_float", "miss_pmv", "miss_fr"]
REL_FEATS = ["rank_gap_pct", "rank_rvol", "rank_price", "rank_mfcs", "rank_float_rotation",
             "z_gap_pct", "z_rvol", "z_price", "z_mfcs", "centroid_dist", "cohort_size",
             "cohort_sd_gap_pct", "cohort_sd_rvol", "cohort_sd_float_rotation"]


def load():
    rows = [json.loads(l) for l in (OUT / "cohorts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    # derive log + missing-indicator absolute features (median-impute at GLOBAL level for indicators only;
    # per-fold imputation happens in the pipeline to avoid leakage)
    for r in rows:
        r["log_price"] = math.log(max(r.get("price") or 0.01, 0.01))
        fl = r.get("float_shares"); pmv = r.get("premarket_volume"); fr = r.get("float_rotation")
        r["miss_float"] = 0.0 if fl else 1.0
        r["miss_pmv"] = 0.0 if pmv else 1.0
        r["miss_fr"] = 0.0 if fr is not None else 1.0
        r["log_float"] = math.log(fl) if fl else None
        r["log_pmv"] = math.log(pmv) if pmv else None
        # float_rotation already present or None
    return rows


def _matrix(rows, feats):
    X = np.array([[(r.get(f) if r.get(f) is not None else np.nan) for f in feats] for r in rows], float)
    return X


def _impute_scale(Xtr, Xte):
    med = np.nanmedian(Xtr, axis=0)
    med = np.where(np.isnan(med), 0.0, med)
    Xtr = np.where(np.isnan(Xtr), med, Xtr)
    Xte = np.where(np.isnan(Xte), med, Xte)
    mu = Xtr.mean(axis=0); sd = Xtr.std(axis=0); sd = np.where(sd < 1e-9, 1.0, sd)
    return (Xtr - mu) / sd, (Xte - mu) / sd


def grouped_cv_winner_acc(rows, feats, y, sessions, folds, rng):
    """Winner-identification accuracy under grouped-by-session CV. Returns (acc, auc)."""
    X = _matrix(rows, feats)
    sess = np.array(sessions)
    uniq = np.array(sorted(set(sessions)))
    rng.shuffle(uniq)
    fold_of = {s: i % folds for i, s in enumerate(uniq)}
    fold = np.array([fold_of[s] for s in sess])
    proba = np.full(len(rows), np.nan)
    for k in range(folds):
        tr, te = fold != k, fold == k
        if y[tr].sum() < 2 or te.sum() == 0:
            continue
        Xtr, Xte = _impute_scale(X[tr], X[te])
        clf = LogisticRegression(C=0.5, max_iter=1000, class_weight="balanced")
        clf.fit(Xtr, y[tr])
        proba[te] = clf.predict_proba(Xte)[:, 1]
    # winner-identification: argmax proba within each session
    correct = 0; total = 0
    for s in uniq:
        m = sess == s
        if not m.any() or np.isnan(proba[m]).all():
            continue
        idx = np.where(m)[0]
        pick = idx[np.nanargmax(proba[idx])]
        correct += int(y[pick] == 1); total += 1
    acc = correct / total if total else 0.0
    valid = ~np.isnan(proba)
    auc = roc_auc_score(y[valid], proba[valid]) if valid.sum() > 10 and len(set(y[valid])) == 2 else float("nan")
    return acc, auc


def regime_winner_acc(rows, feats, y, sessions, split):
    """Train early (< split), test late (>= split); winner-accuracy on the late held-out cohorts."""
    X = _matrix(rows, feats); sess = np.array(sessions)
    tr = np.array([s < split for s in sessions]); te = ~tr
    if y[tr].sum() < 2 or te.sum() == 0:
        return float("nan")
    Xtr, Xte = _impute_scale(X[tr], X[te])
    clf = LogisticRegression(C=0.5, max_iter=1000, class_weight="balanced")
    clf.fit(Xtr, y[tr])
    p = np.full(len(rows), np.nan); p[te] = clf.predict_proba(Xte)[:, 1]
    correct = 0; total = 0
    for s in sorted(set(np.array(sessions)[te])):
        idx = np.where(sess == s)[0]
        pick = idx[np.nanargmax(p[idx])]
        correct += int(y[pick] == 1); total += 1
    return correct / total if total else float("nan")


def permute_within_cohort(y, sessions, rng):
    yp = y.copy()
    for s in set(sessions):
        idx = np.where(np.array(sessions) == s)[0]
        yp[idx] = 0
        yp[idx[rng.integers(len(idx))]] = 1
    return yp


def main():
    rows = load()
    sessions = [r["date"] for r in rows]
    y = np.array([r["is_winner"] for r in rows])
    sizes = {}
    for r in rows:
        sizes[r["date"]] = r["cohort_size"]
    random_acc = float(np.mean([1.0 / n for n in sizes.values()]))
    rng = np.random.default_rng(SEED)

    # real point estimates
    acc_abs, auc_abs = grouped_cv_winner_acc(rows, ABS_FEATS, y, sessions, N_FOLDS, np.random.default_rng(SEED))
    acc_rel, auc_rel = grouped_cv_winner_acc(rows, REL_FEATS, y, sessions, N_FOLDS, np.random.default_rng(SEED))
    acc_both, auc_both = grouped_cv_winner_acc(rows, ABS_FEATS + REL_FEATS, y, sessions, N_FOLDS, np.random.default_rng(SEED))
    lift = acc_both - acc_abs

    # cross-regime
    reg_abs = regime_winner_acc(rows, ABS_FEATS, y, sessions, SPLIT)
    reg_both = regime_winner_acc(rows, ABS_FEATS + REL_FEATS, y, sessions, SPLIT)

    # permutation null (within-cohort winner shuffle)
    null_best = []; null_lift = []
    for b in range(B_PERM):
        yp = permute_within_cohort(y, sessions, rng)
        a_abs, _ = grouped_cv_winner_acc(rows, ABS_FEATS, yp, sessions, N_FOLDS, np.random.default_rng(1000 + b))
        a_rel, _ = grouped_cv_winner_acc(rows, REL_FEATS, yp, sessions, N_FOLDS, np.random.default_rng(1000 + b))
        a_both, _ = grouped_cv_winner_acc(rows, ABS_FEATS + REL_FEATS, yp, sessions, N_FOLDS, np.random.default_rng(1000 + b))
        null_best.append(max(a_rel, a_both))
        null_lift.append(a_both - a_abs)
    null_best = np.array(null_best); null_lift = np.array(null_lift)

    p_acc = float((null_best >= max(acc_rel, acc_both)).mean())
    p_lift = float((null_lift >= lift).mean())

    G1 = (max(acc_rel, acc_both) > random_acc) and (p_acc < 0.01)
    G2 = (lift > 0) and (p_lift < 0.05)
    G3 = (reg_both > reg_abs) and (reg_both > random_acc)
    PASS = bool(G1 and G2 and G3)

    res = {
        "n_rows": len(rows), "n_cohorts": len(sizes), "random_winner_acc": round(random_acc, 4),
        "acc_ABS": round(acc_abs, 4), "acc_REL": round(acc_rel, 4), "acc_BOTH": round(acc_both, 4),
        "auc_ABS": round(auc_abs, 4), "auc_REL": round(auc_rel, 4), "auc_BOTH": round(auc_both, 4),
        "relational_lift_BOTH_minus_ABS": round(lift, 4),
        "perm_null_best_acc_mean": round(float(null_best.mean()), 4),
        "perm_null_best_acc_p95": round(float(np.percentile(null_best, 95)), 4),
        "perm_null_lift_mean": round(float(null_lift.mean()), 4),
        "perm_null_lift_p95": round(float(np.percentile(null_lift, 95)), 4),
        "p_acc": p_acc, "p_lift": p_lift,
        "regime_split": SPLIT, "regime_acc_ABS_late": round(reg_abs, 4) if reg_abs == reg_abs else None,
        "regime_acc_BOTH_late": round(reg_both, 4) if reg_both == reg_both else None,
        "G1_predictability_exists": bool(G1), "G2_relational_beyond_abs": bool(G2),
        "G3_cross_regime_survival": bool(G3), "STAGE_A_PASS": PASS,
        "B_perm": B_PERM,
    }
    (OUT / "stageA_result.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
