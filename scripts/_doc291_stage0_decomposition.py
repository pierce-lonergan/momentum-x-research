"""DOC 291 STAGE 0 — DECOMPOSITION: what is the doc-290 volatility signal made of?
Gates frozen in scripts/_doc291_PREREG.md (sha256 5155f3df..., commit 7cc09fe) BEFORE this ran.

Nested-baseline ladder, SAME learner on every rung (rank-calibrated GBM — the doc-290 fair-baseline lesson):
  B0 unconditional | B1 log_price | B2 +float/rotation | B3 +trailing realized vol (warehouse) |
  B4 +ticker-LOO historical peak mean | FULL = doc-290 39 features ∪ B1..B4.
Gates: D1 = M(FULL)-M(B3), D2 = M(FULL)-M(B4); within-session permutation nulls (B=200, full recompute),
BH α=0.05; sign-replication across the 2026-05-26 split. Deliverable: the attribution curve.
"""
from __future__ import annotations
import json, math
from collections import defaultdict
from pathlib import Path
import numpy as np
from scipy.stats import rankdata, spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score
import importlib.util

_spec = importlib.util.spec_from_file_location("A", str(Path(__file__).resolve().parent / "_doc290_stageA_hlocal.py"))
A = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(A)

_ROOT = Path(__file__).resolve().parent.parent
OUT = _ROOT / "data" / "research" / "doc291"
OUT.mkdir(parents=True, exist_ok=True)
SEED = 291
B_PERM = 200
SPLIT = "2026-05-26"

B1_F = ["log_price"]
B2_F = B1_F + ["log_float", "float_rotation", "miss_float", "miss_fr"]
B3_F = B2_F + ["trail_rv20", "trail_atr14_pct", "miss_trail"]
B4_F = B3_F + ["ticker_loo_peak_mean", "miss_loo"]


def build_trailing_and_loo(rows):
    """B3: per-(ticker,event-date) trailing realized vol + ATR% from warehouse DAILY bars (dates strictly
    before the event date). B4: ticker-LOO peak_run60 mean from the events themselves."""
    import duckdb
    tickers = sorted({r["ticker"] for r in rows})
    tk = "','".join(tickers)
    con = duckdb.connect(); con.execute("SET threads=4")
    df = con.execute(f"""
        SELECT ticker, strftime(ts_et,'%Y-%m-%d') d,
               first(open ORDER BY ts_et) o, max(high) h, min(low) l, last(close ORDER BY ts_et) c
        FROM read_parquet('data/polygon_warehouse/minute_aggs/**/*.parquet', union_by_name=true)
        WHERE ticker IN ('{tk}') AND strftime(ts_et,'%H:%M') BETWEEN '09:30' AND '16:00'
          AND ts_et >= TIMESTAMP '2026-01-01'
        GROUP BY ticker, d ORDER BY ticker, d""").df()
    con.close()
    hist = defaultdict(list)  # ticker -> [(d, o,h,l,c)]
    for t in df.itertuples():
        hist[t.ticker].append((t.d, float(t.o), float(t.h), float(t.l), float(t.c)))
    cov = {"events_with_trail": 0, "events_without_trail": 0}
    for r in rows:
        past = [x for x in hist.get(r["ticker"], []) if x[0] < r["date"]][-21:]
        if len(past) >= 6:
            closes = np.array([x[4] for x in past])
            rets = np.diff(np.log(np.maximum(closes, 1e-9)))
            r["trail_rv20"] = float(np.std(rets[-20:]))
            trs = []
            for i in range(1, len(past)):
                _d, o, h, l, c = past[i]; pc = past[i - 1][4]
                trs.append(max(h - l, abs(h - pc), abs(l - pc)) / max(pc, 1e-9))
            r["trail_atr14_pct"] = float(np.mean(trs[-14:]))
            r["miss_trail"] = 0.0
            cov["events_with_trail"] += 1
        else:
            r["trail_rv20"] = None; r["trail_atr14_pct"] = None; r["miss_trail"] = 1.0
            cov["events_without_trail"] += 1
    # B4: ticker-LOO peak mean (other sessions of the same ticker)
    byt = defaultdict(list)
    for r in rows:
        byt[r["ticker"]].append(r)
    n_loo = 0
    for r in rows:
        others = [x["peak_run60"] for x in byt[r["ticker"]] if x["date"] != r["date"]]
        if others:
            r["ticker_loo_peak_mean"] = float(np.mean(others)); r["miss_loo"] = 0.0; n_loo += 1
        else:
            r["ticker_loo_peak_mean"] = None; r["miss_loo"] = 1.0
    cov["events_with_loo"] = n_loo
    return cov


def rung_score(rows, feats, y, sessions, seed):
    """pooled-OOS score of a rank-calibrated GBM on the given feature set (grouped 5-fold by session)."""
    X = np.array([[(r.get(f) if r.get(f) is not None else np.nan) for f in feats] for r in rows], float)
    fold = A.fold_assign(sessions, A.N_FOLDS, seed)
    pred = np.full(len(y), np.nan)
    for kf in range(A.N_FOLDS):
        tr, te = fold != kf, fold == kf
        if te.sum() == 0 or tr.sum() < 50:
            continue
        Xtr, Xte = A.impute_scale(X[tr], X[te])
        ytr_rank = rankdata(y[tr]) / len(y[tr])
        gbm = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, min_samples_leaf=20,
                                            l2_regularization=1.0, random_state=SEED)
        gbm.fit(Xtr, ytr_rank)
        pred[te] = gbm.predict(Xte)
    return pred


def M(pred, y):
    v = ~np.isnan(pred)
    return float(spearmanr(pred[v], y[v])[0]) if v.sum() > 20 else float("nan")


def main():
    rows = A.load()
    cov = build_trailing_and_loo(rows)
    sessions = [r["date"] for r in rows]
    y = np.array([r["peak_run60"] for r in rows])
    stop = np.array([r["stop10_hit"] for r in rows])
    FULL_F = sorted(set(A.FEATS) | set(B4_F))

    ladder = {"B0": 0.0}
    preds = {}
    for name, feats in [("B1", B1_F), ("B2", B2_F), ("B3", B3_F), ("B4", B4_F), ("FULL", FULL_F)]:
        preds[name] = rung_score(rows, feats, y, sessions, SEED)
        ladder[name] = round(M(preds[name], y), 4)

    d1 = ladder["FULL"] - ladder["B3"]
    d2 = ladder["FULL"] - ladder["B4"]

    # permutation nulls (within-session shuffle, full recompute of FULL, B3, B4 rungs)
    rng = np.random.default_rng(SEED)
    null_d1, null_d2 = [], []
    for b in range(B_PERM):
        yp = A.shuffle_within_session(y, sessions, rng)
        pf = rung_score(rows, FULL_F, yp, sessions, 1000 + b)
        p3 = rung_score(rows, B3_F, yp, sessions, 1000 + b)
        p4 = rung_score(rows, B4_F, yp, sessions, 1000 + b)
        null_d1.append(M(pf, yp) - M(p3, yp))
        null_d2.append(M(pf, yp) - M(p4, yp))
    null_d1, null_d2 = np.array(null_d1), np.array(null_d2)
    p1 = float((1 + (null_d1 >= d1).sum()) / (1 + B_PERM))
    p2 = float((1 + (null_d2 >= d2).sum()) / (1 + B_PERM))
    # BH over the 2 gate tests at alpha=0.05
    ps = sorted([("D1", p1), ("D2", p2)], key=lambda t: t[1])
    bh_pass = {}
    m = 2
    max_i = 0
    for i, (nm, p) in enumerate(ps, 1):
        if p <= 0.05 * i / m:
            max_i = i
    for i, (nm, p) in enumerate(ps, 1):
        bh_pass[nm] = i <= max_i

    # regime replication of the increments
    def side_M(feats, idx):
        rsub = [rows[i] for i in idx]
        ysub = y[idx]
        pr = rung_score(rsub, feats, ysub, [sessions[i] for i in idx], SEED)
        return M(pr, ysub)
    early = [i for i, s in enumerate(sessions) if s < SPLIT]
    late = [i for i, s in enumerate(sessions) if s >= SPLIT]
    rep = {}
    for side, idx in [("early", early), ("late", late)]:
        mf = side_M(FULL_F, idx); m3 = side_M(B3_F, idx); m4 = side_M(B4_F, idx)
        rep[side] = {"FULL": round(mf, 4), "B3": round(m3, 4), "B4": round(m4, 4),
                     "D1": round(mf - m3, 4), "D2": round(mf - m4, 4)}
    G_D1 = bool(bh_pass["D1"] and rep["early"]["D1"] > 0 and rep["late"]["D1"] > 0)
    G_D2 = bool(bh_pass["D2"] and rep["early"]["D2"] > 0 and rep["late"]["D2"] > 0)

    # descriptive: stop10 AUC of FULL
    v = ~np.isnan(preds["FULL"])
    auc_stop = float(roc_auc_score(stop[v], preds["FULL"][v])) if len(set(stop[v])) == 2 else None

    reduces = (not G_D2) or (d2 < 0.03)
    res = {"prereg_sha256": "5155f3df3ffe3ec425c931ff96e05de508a0ddfe16a8c4c9dd9d08573a85a0c4",
           "coverage": cov, "attribution_ladder_spearman": ladder,
           "D1_FULL_minus_B3": round(d1, 4), "D2_FULL_minus_B4": round(d2, 4),
           "perm": {"B": B_PERM, "p_D1": p1, "p_D2": p2, "bh_alpha": 0.05, "bh_pass": bh_pass},
           "regime_replication": rep, "gate_D1": G_D1, "gate_D2": G_D2,
           "stop10_auc_FULL_descriptive": round(auc_stop, 4) if auc_stop else None,
           "DECOMPOSITION_VERDICT": ("SIGNAL REDUCES TO KNOWN VOL-PROXY STACK (cheap+small+recently-volatile"
                                     " +ticker history); transfer thesis weakened accordingly" if reduces else
                                     "FULL adds real information beyond known proxies — the incremental features"
                                     " are the transfer candidates")}
    (OUT / "stage0_decomposition.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    # persist OOS FULL score for Stage 1
    np.save(OUT / "stage0_full_score.npy", preds["FULL"])
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
