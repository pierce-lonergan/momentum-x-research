#!/usr/bin/env python
"""doc 184: train the momentum bot's intraday continuation model.

Assembles the doc-182 graded rejections (data/shadow/rejection_outcomes_graded_*.jsonl,
produced by scripts/finalize_rejection_outcomes.py), extracts the shared feature
contract (src/analysis/intraday_continuer.py), trains a SMALL logistic model, and —
the whole point — measures whether it BEATS the heuristic faller gate at separating
intraday continuers from fades. Leave-one-day-out CV (no temporal leakage).

Run:
    python scripts/train_intraday_continuer.py                 # all graded files
    python scripts/train_intraday_continuer.py --min-rows 60   # require 60 labeled rows
    python scripts/train_intraday_continuer.py --run-mfe 0.10  # stricter "continued" label

SHIP DISCIPLINE: refuses to write an artifact unless n >= min_rows, both classes
present, and the model's OOF AUC beats the faller-gate baseline by --min-edge. Until
then it prints the scorecard and exits — so a thin/biased early sample can't ship a
bad model into the live A/B.
"""
from __future__ import annotations

import argparse
import glob
import json
import pickle
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.analysis.intraday_continuer import (  # noqa: E402
    FEATURE_NAMES, MODEL_PATH, extract_features, features_vector, label_from_outcome,
)

_SHADOW_DIR = _ROOT / "data" / "shadow"


def load_graded_rows(pattern: str | None = None) -> list[dict]:
    files = sorted(glob.glob(pattern or str(_SHADOW_DIR / "rejection_outcomes_graded_*.jsonl")))
    rows: list[dict] = []
    for fp in files:
        for line in Path(fp).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("kind") == "rejection_outcome":
                rows.append(r)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pattern", default=None, help="glob for graded files")
    ap.add_argument("--min-rows", type=int, default=60)
    ap.add_argument("--run-mfe", type=float, default=0.08)
    ap.add_argument("--min-edge", type=float, default=0.03, help="min OOF-AUC edge over faller baseline to ship")
    ap.add_argument("--out", default=str(MODEL_PATH))
    args = ap.parse_args()

    rows = load_graded_rows(args.pattern)
    X, y, faller, days = [], [], [], []
    for r in rows:
        lab = label_from_outcome(r.get("outcome"), run_mfe=args.run_mfe)
        if lab is None:
            continue
        feats = extract_features(r)
        X.append(features_vector(feats))
        y.append(lab)
        faller.append(float(r.get("score") or 0.0))
        days.append(r.get("session_date") or "?")

    n = len(y)
    print(f"=== intraday continuer training ===")
    print(f"graded rows scanned: {len(rows)} | labelable: {n}")
    if n == 0:
        print("No labeled rows yet. Run scripts/finalize_rejection_outcomes.py after a "
              "session (post doc-182 deploy) to grade rejections, then re-run this.")
        return 0
    cls = Counter(y)
    print(f"class balance: continued(1)={cls.get(1,0)} faded(0)={cls.get(0,0)} | "
          f"sessions={len(set(days))}")
    if n < args.min_rows:
        print(f"INSUFFICIENT DATA: {n} < min_rows={args.min_rows}. Accumulate more graded "
              "rejections before training (a model on a thin sample overfits / mis-ships).")
        return 0
    if len(cls) < 2:
        print("ONLY ONE CLASS present — cannot train a discriminator yet. Need both "
              "continuers and faders in the sample.")
        return 0

    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score

    Xa, ya, fa = np.array(X, float), np.array(y, int), np.array(faller, float)
    day_arr = np.array(days)

    # Leave-one-day-out out-of-fold predictions (no temporal leakage).
    oof = np.full(n, np.nan)
    for d in sorted(set(days)):
        tr, te = day_arr != d, day_arr == d
        if tr.sum() < 10 or len(set(ya[tr])) < 2:
            continue
        sc = StandardScaler().fit(Xa[tr])
        clf = LogisticRegression(max_iter=1000, C=1.0, class_weight="balanced")
        clf.fit(sc.transform(Xa[tr]), ya[tr])
        oof[te] = clf.predict_proba(sc.transform(Xa[te]))[:, 1]
    have_oof = ~np.isnan(oof)
    model_auc = (roc_auc_score(ya[have_oof], oof[have_oof])
                 if have_oof.sum() >= 10 and len(set(ya[have_oof])) == 2 else None)
    # Faller-gate baseline: high faller_score => predicted FADER, so its implied
    # P(continue) ranks by -faller_score.
    base_auc = (roc_auc_score(ya, -fa) if len(set(ya)) == 2 and np.ptp(fa) > 0 else None)

    print(f"OOF model AUC : {model_auc if model_auc is None else round(model_auc,3)} "
          f"(leave-one-day-out, n_oof={int(have_oof.sum())})")
    print(f"faller baseline AUC: {base_auc if base_auc is None else round(base_auc,3)} "
          "(ranking by -faller_score)")

    if model_auc is None or base_auc is None:
        print("Not enough cross-day signal to evaluate yet. Accumulate more days.")
        return 0
    edge = model_auc - base_auc
    print(f"EDGE over faller gate: {edge:+.3f} (need >= {args.min_edge:+.3f} to ship)")
    if edge < args.min_edge:
        print("MODEL DOES NOT BEAT THE FALLER GATE by the required margin — NOT shipping. "
              "The heuristic is still the best we have; keep it. (This is a feature, not a "
              "failure: it prevents replacing a working gate with a weaker model.)")
        return 0

    # Ship: refit on ALL data and save.
    scaler = StandardScaler().fit(Xa)
    model = LogisticRegression(max_iter=1000, C=1.0, class_weight="balanced").fit(
        scaler.transform(Xa), ya)
    coefs = dict(zip(FEATURE_NAMES, model.coef_[0].round(4).tolist()))
    metrics = {"n": n, "sessions": len(set(days)), "oof_auc": round(model_auc, 4),
               "faller_baseline_auc": round(base_auc, 4), "edge": round(edge, 4),
               "run_mfe": args.run_mfe, "class_balance": dict(cls), "coefficients": coefs}
    with open(args.out, "wb") as f:
        pickle.dump({"model": model, "scaler": scaler,
                     "feature_names": FEATURE_NAMES, "metrics": metrics}, f)
    (_SHADOW_DIR / "intraday_continuer_metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8")
    print(f"\nSHIPPED model -> {args.out}")
    print(f"coefficients: {coefs}")
    print("Next: wire IntradayContinuer.load().score() as a write-only live shadow "
          "(A/B vs faller gate) before letting it gate entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
