"""DOC 290 — DISCLOSED SENSITIVITY (design-review CRITICAL #1): is the kNN information lift just
same-ticker memorization?

68% of events are repeat tickers across sessions; same-ticker outcomes correlate (+0.143) and features are
ticker-sticky, so a kNN neighborhood can simply retrieve "the same runner's other days" (including FUTURE
sessions — non-implementable) — an artifact the within-session permutation null cannot represent, and one
that hits kNN but not the leaf-limited GBM. This re-runs the pure-kNN OOS Spearman under a custom kNN whose
neighbor pool EXCLUDES all same-ticker train rows, vs an identical custom kNN with them included.

If the lift dies under exclusion, the Stage-A "local structure exists" narrative is dead: the locality was
ticker identity, not micro-trend geometry. (Per doc-275/277 this is a disclosed sensitivity, not a re-tune —
the frozen primary verdict [STAGE A FAIL] already stands on its own.)
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from scipy.stats import spearmanr
import importlib.util

_spec = importlib.util.spec_from_file_location("sA", str(Path(__file__).resolve().parent / "_doc290_stageA_hlocal.py"))
A = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(A)

OUT = Path(__file__).resolve().parent.parent / "data" / "research" / "doc290"
K_LIST = [25, 100, 200]
SEED = 290


def custom_knn_oos(rows, X, y, sessions, tickers, k, exclude_same_ticker):
    fold = A.fold_assign(sessions, A.N_FOLDS, SEED)
    pred = np.full(len(y), np.nan)
    tick = np.array(tickers)
    self_retrieval_frac = []
    for kf in range(A.N_FOLDS):
        tr, te = fold != kf, fold == kf
        Xtr, Xte = A.impute_scale(X[tr], X[te])
        ytr, ttr = y[tr], tick[tr]
        te_idx = np.where(te)[0]
        # pairwise distances (te x tr)
        d = ((Xte[:, None, :] - Xtr[None, :, :]) ** 2).sum(-1)
        for i, gi in enumerate(te_idx):
            drow = d[i].copy()
            same = ttr == tick[gi]
            if exclude_same_ticker:
                drow[same] = np.inf
            nn = np.argsort(drow)[:k]
            valid = nn[np.isfinite(drow[nn])]
            if len(valid) == 0:
                continue
            pred[gi] = ytr[valid].mean()
            if not exclude_same_ticker:
                self_retrieval_frac.append(float(same[valid].mean()))
    v = ~np.isnan(pred)
    rho = float(spearmanr(pred[v], y[v])[0])
    return rho, (float(np.mean(self_retrieval_frac)) if self_retrieval_frac else None)


def main():
    rows = A.load()
    sessions = [r["date"] for r in rows]
    tickers = [r["ticker"] for r in rows]
    X = A.matrix(rows)
    y = np.array([r["peak_run60"] for r in rows])

    res = {"gbm_reference_spearman": 0.1746, "note": "frozen primary already FAILED; this tests the salvage narrative",
           "n_repeat_ticker_events_frac": round(float(np.mean([tickers.count(t) > 1 for t in set(tickers)])), 3)}
    for k in K_LIST:
        rho_inc, self_frac = custom_knn_oos(rows, X, y, sessions, tickers, k, exclude_same_ticker=False)
        rho_exc, _ = custom_knn_oos(rows, X, y, sessions, tickers, k, exclude_same_ticker=True)
        res[f"k{k}"] = {"spearman_included": round(rho_inc, 4), "spearman_ticker_excluded": round(rho_exc, 4),
                        "lift_killed_pct": round(100 * (rho_inc - rho_exc) / rho_inc, 1) if rho_inc else None,
                        "mean_same_ticker_frac_in_neighborhood": round(self_frac, 4) if self_frac is not None else None}
    exc100 = res["k100"]["spearman_ticker_excluded"]
    res["VERDICT"] = ("LIFT IS TICKER MEMORIZATION — excluded-kNN falls to/below the GBM reference"
                      if exc100 <= res["gbm_reference_spearman"] + 0.02 else
                      "residual local structure survives ticker exclusion (report magnitude honestly)")
    (OUT / "sens_ticker_exclusion.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
