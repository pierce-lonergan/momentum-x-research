"""DOC 289 STAGE A — robustness / characterization (EXPLORATORY, clearly labeled; the pre-registered PRIMARY
verdict in _doc289_stageA_relational.py stands regardless). Three questions:
  (1) Is the FAIL an artifact of the harsh single-winner argmax metric? -> test a softer target (is_top3) and a
      continuation-RANK correlation (Spearman of OOS score vs peak_run_rank), which are far more powered.
  (2) The pooled AUC ~0.60 whisper -- is it ABSOLUTE or RELATIONAL? -> AUC(ABS) vs AUC(REL) vs AUC(BOTH) with a
      within-cohort permutation p on AUC.
  (3) WHAT carries the whisper? -> standardized logistic coefficients of the BOTH model (full-fit).
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from scipy.stats import spearmanr

import importlib.util
_spec = importlib.util.spec_from_file_location("relmod", str(Path(__file__).resolve().parent / "_doc289_stageA_relational.py"))
R = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(R)

OUT = Path(__file__).resolve().parent.parent / "data" / "research" / "doc289"
B = 500


def grouped_proba(rows, feats, y, sessions, folds, seedbase):
    X = R._matrix(rows, feats); sess = np.array(sessions)
    uniq = np.array(sorted(set(sessions)))
    np.random.default_rng(seedbase).shuffle(uniq)
    fold_of = {s: i % folds for i, s in enumerate(uniq)}
    fold = np.array([fold_of[s] for s in sess])
    proba = np.full(len(rows), np.nan)
    for k in range(folds):
        tr, te = fold != k, fold == k
        if y[tr].sum() < 2 or te.sum() == 0:
            continue
        Xtr, Xte = R._impute_scale(X[tr], X[te])
        clf = LogisticRegression(C=0.5, max_iter=1000, class_weight="balanced").fit(Xtr, y[tr])
        proba[te] = clf.predict_proba(Xte)[:, 1]
    return proba


def auc_of(rows, feats, y, sessions):
    p = grouped_proba(rows, feats, y, sessions, R.N_FOLDS, R.SEED)
    v = ~np.isnan(p)
    return roc_auc_score(y[v], p[v]) if len(set(y[v])) == 2 else float("nan"), p


def main():
    rows = R.load()
    sessions = [r["date"] for r in rows]
    rng = np.random.default_rng(R.SEED)

    out = {}
    for target in ["is_winner", "is_top3"]:
        y = np.array([r[target] for r in rows])
        a_abs, _ = auc_of(rows, R.ABS_FEATS, y, sessions)
        a_rel, _ = auc_of(rows, R.REL_FEATS, y, sessions)
        a_both, pboth = auc_of(rows, R.ABS_FEATS + R.REL_FEATS, y, sessions)
        # permutation p on AUC(BOTH): shuffle target within cohort
        null = []
        for b in range(B):
            yp = R.permute_within_cohort(y, sessions, rng)
            ab, _ = auc_of(rows, R.ABS_FEATS + R.REL_FEATS, yp, sessions)
            null.append(ab)
        null = np.array(null)
        out[target] = {
            "auc_ABS": round(a_abs, 4), "auc_REL": round(a_rel, 4), "auc_BOTH": round(a_both, 4),
            "auc_lift_BOTH_minus_ABS": round(a_both - a_abs, 4),
            "perm_null_auc_mean": round(float(null.mean()), 4),
            "perm_null_auc_p95": round(float(np.percentile(null, 95)), 4),
            "p_auc_BOTH": round(float((null >= a_both).mean()), 4),
        }
        # continuation-rank correlation: does the OOS score track actual peak_run_rank?
        prr = np.array([r["peak_run_rank"] for r in rows])
        v = ~np.isnan(pboth)
        rho, prho = spearmanr(pboth[v], prr[v])
        out[target]["spearman_score_vs_peakrunrank"] = round(float(rho), 4)
        out[target]["spearman_p"] = float(prho)

    # what carries the whisper: standardized coefficients of the BOTH model on is_top3 (full fit)
    y = np.array([r["is_top3"] for r in rows])
    feats = R.ABS_FEATS + R.REL_FEATS
    X = R._matrix(rows, feats)
    Xs, _ = R._impute_scale(X, X)
    clf = LogisticRegression(C=0.5, max_iter=1000, class_weight="balanced").fit(Xs, y)
    coefs = sorted(zip(feats, clf.coef_[0]), key=lambda t: -abs(t[1]))[:10]
    out["top_coefs_is_top3"] = [{"feature": f, "coef": round(float(c), 3),
                                 "family": "REL" if f in R.REL_FEATS else "ABS"} for f, c in coefs]

    (OUT / "stageA_robustness.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
