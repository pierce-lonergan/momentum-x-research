"""DOC 292 WORKSTREAM A — the $0 IV PILOT (Stage 3-lite). Gates frozen in scripts/_doc292_PREREG.md
(sha256 eb83df87..., commit 73cc3bc) BEFORE this ran.

Question: does the doc-291 challenger add anything over an IV-INCLUSIVE baseline on the universes where
implied vol is free (CBOE index + single-name vol indices)? G1 = incremental QLIKE vs GBM[HAR+calibrated-IV]
(block-42 bootstrap, pooled + both halves, MMI >=2%); G2 = encompassing weight beside the IV baseline.
Raw-IV comparisons are flattery: computed, labeled descriptive, excluded from gates.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats
import importlib.util

_spec = importlib.util.spec_from_file_location("H", str(Path(__file__).resolve().parent / "_doc291_stage2_harrv.py"))
H = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(H)

_ROOT = Path(__file__).resolve().parent.parent
OUT = _ROOT / "data" / "research" / "doc292"
CBOE = OUT / "cboe"
SEED = 292
B_BOOT = 5000
MMI = 0.02

PAIRS_30 = {"VIX": "SPY", "VXN": "QQQ", "RVX": "IWM", "VXD": "DIA", "OVX": "USO", "GVZ": "GLD",
            "VXAPL": "AAPL", "VXAZN": "AMZN", "VXGOG": "GOOGL", "VXGS": "GS", "VXIBM": "IBM"}
PAIR_9 = {"VIX9D": "SPY"}
H30, H9 = 21, 6
BLOCK30, BLOCK9 = 42, 21
OUR_F = ["abs_overnight_gap", "range_pct", "first15_ret", "first15_range", "last_hour_rv_share",
         "vwap_dev_close", "higher_low_frac", "vol_slope", "log_dvol"]   # doc-291 set MINUS rv_ratio_dw
HAR_F = ["log_rv_d", "log_rv_w", "log_rv_m"]


def load_iv(series):
    p = CBOE / f"{series}.csv"
    df = pd.read_csv(p)
    df.columns = [c.strip().upper() for c in df.columns]
    val = "CLOSE" if "CLOSE" in df.columns else [c for c in df.columns if c != "DATE"][0]
    df["d"] = pd.to_datetime(df["DATE"], format="%m/%d/%Y").dt.strftime("%Y-%m-%d")
    return df[["d", val]].rename(columns={val: "iv"}).dropna()


def build_series_panel(underlyings):
    H.LIQUID = sorted(set(underlyings))          # monkeypatch the frozen builder's universe
    return H.build_panel()


def forward_target(g, h):
    """annualized forward variance over next h sessions of THIS underlying's own sequence."""
    rv2 = (g["rv"].values) ** 2
    n = len(rv2)
    tgt = np.full(n, np.nan)
    for i in range(n - h):
        tgt[i] = (252.0 / h) * rv2[i + 1:i + 1 + h].sum()
    return tgt


def calibrate_iv(g, h, win=252, min_rows=100):
    """rolling MZ on log-variance, trained only on rows whose windows are fully realized (idx <= t-h)."""
    logV = np.log(g["V"].values)
    logIV2 = np.log((g["iv"].values / 100.0) ** 2)
    n = len(g)
    out = np.full(n, np.nan)
    for t in range(n):
        hi = t - h
        if hi < min_rows:
            continue
        lo = max(0, hi - win)
        x, y = logIV2[lo:hi], logV[lo:hi]
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < min_rows:
            continue
        b, a = np.polyfit(x[ok], y[ok], 1)
        out[t] = a + b * logIV2[t]
    return out    # log calibrated variance forecast


def assemble(pairs, h):
    panel = build_series_panel(list(pairs.values()))
    rows = []
    for series, und in pairs.items():
        iv = load_iv(series)
        g = panel[panel.ticker == und].sort_values("d").reset_index(drop=True)
        g = g.merge(iv, on="d", how="inner").reset_index(drop=True)
        g["V"] = forward_target(g, h)
        g = g[np.isfinite(g["V"])].reset_index(drop=True)
        g["log_fcal"] = calibrate_iv(g, h)
        g["series"] = series
        rows.append(g)
    df = pd.concat(rows, ignore_index=True)
    df = df[np.isfinite(df["log_fcal"])].reset_index(drop=True)
    return df


def walk_forward(df, h, feats_b, feats_c):
    from sklearn.ensemble import HistGradientBoostingRegressor
    dates = sorted(df.d.unique())
    di = {d: i for i, d in enumerate(dates)}
    df = df.copy(); df["di"] = df.d.map(di)
    y = np.log(df["V"].values)
    predB = np.full(len(df), np.nan); predC = np.full(len(df), np.nan)
    for rp in range(0, len(dates), 21):
        tr = (df.di + h) <= rp                     # fully realized by refit date
        te = (df.di >= rp) & (df.di < rp + 21)
        if te.sum() == 0 or tr.sum() < 150:
            continue
        for feats, dest in [(feats_b, predB), (feats_c, predC)]:
            gb = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, min_samples_leaf=50,
                                               l2_regularization=1.0, random_state=SEED)
            gb.fit(df.loc[tr, feats].values, y[tr])
            dest[te.values] = gb.predict(df.loc[te, feats].values)
    return df, predB, predC


def qlike(V, F):
    r = V / np.maximum(F, 1e-12)
    return r - np.log(r) - 1.0


def block_ci(day_means, block, B=B_BOOT, seed=SEED):
    n = len(day_means)
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(n / block))
    out = []
    for _ in range(B):
        starts = rng.integers(0, max(n - block, 1) + 1, nb)
        s = np.concatenate([day_means[st:st + block] for st in starts])[:n]
        out.append(s.mean())
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def day_means(diff, dates_arr):
    dts = sorted(set(dates_arr))
    return np.array([diff[dates_arr == d].mean() for d in dts]), dts


def run_tenor(pairs, h, block, label):
    df = assemble(pairs, h)
    df, pB, pC = walk_forward(df, h, HAR_F + ["log_fcal"], HAR_F + ["log_fcal"] + OUR_F)
    v = ~np.isnan(pB) & ~np.isnan(pC)
    sub = df.loc[v].reset_index(drop=True)
    V = sub["V"].values
    lB = qlike(V, np.exp(pB[v])); lC = qlike(V, np.exp(pC[v]))
    lR0 = qlike(V, np.exp(sub["log_fcal"].values))
    lraw = qlike(V, (sub["iv"].values / 100.0) ** 2)
    darr = sub["d"].values
    d = lB - lC                                     # >0 = challenger better
    dm, dts = day_means(d, darr)
    lo, hi = block_ci(dm, block)
    impr = float(d.mean() / lB.mean())
    halfd = dts[len(dts) // 2]
    halves = {}
    for hn, m in [("H1", np.array(dts) < halfd), ("H2", np.array(dts) >= halfd)]:
        lo_h, hi_h = block_ci(dm[m], block, seed=SEED + 7)
        mask_rows = np.isin(darr, np.array(dts)[m])
        halves[hn] = {"improvement_pct": round(100 * float(d[mask_rows].mean() / lB[mask_rows].mean()), 3),
                      "ci95": [round(lo_h, 6), round(hi_h, 6)], "n_days": int(m.sum())}
    # G2 encompassing (only meaningful at the gated tenor)
    X = np.column_stack([np.ones(len(sub)), pB[v], pC[v]])
    yy = np.log(V)
    w, *_ = np.linalg.lstsq(X, yy, rcond=None)
    # block bootstrap of w2 by date
    rng = np.random.default_rng(SEED + 11)
    dts_a = np.array(dts); nb = int(np.ceil(len(dts_a) / block))
    w2s = []
    for _ in range(2000):
        starts = rng.integers(0, max(len(dts_a) - block, 1) + 1, nb)
        pick_days = np.concatenate([dts_a[st:st + block] for st in starts])[:len(dts_a)]
        m = np.isin(darr, pick_days)
        if m.sum() < 100:
            continue
        wb, *_ = np.linalg.lstsq(X[m], yy[m], rcond=None)
        w2s.append(wb[2])
    w2lo, w2hi = float(np.percentile(w2s, 2.5)), float(np.percentile(w2s, 97.5))
    res = {"label": label, "n_rows_scored": int(v.sum()), "n_series": int(sub.series.nunique()),
           "n_days": len(dts), "eff_windows_per_series": round(len(dts) / h, 1),
           "qlike": {"raw_iv_descriptive": round(float(lraw.mean()), 5),
                     "calibrated_iv_R0": round(float(lR0.mean()), 5),
                     "B_gbm_har_calIV": round(float(lB.mean()), 5),
                     "C_challenger": round(float(lC.mean()), 5)},
           "G1": {"improvement_pct_pooled": round(100 * impr, 3), "diff_ci95": [round(lo, 6), round(hi, 6)],
                  "halves": halves, "resolution_ci_halfwidth_pct": round(100 * (hi - lo) / 2 / float(lB.mean()), 3)},
           "G2_encompassing": {"w_B": round(float(w[1]), 4), "w_C": round(float(w[2]), 4),
                               "w_C_ci95": [round(w2lo, 4), round(w2hi, 4)]}}
    return res, d, darr


def main():
    res30, _d, _da = run_tenor(PAIRS_30, H30, BLOCK30, "30d")
    res9, _d9, _da9 = run_tenor(PAIR_9, H9, BLOCK9, "9d_replication")

    g1 = (res30["G1"]["diff_ci95"][0] > 0
          and all(h_["ci95"][0] > 0 for h_ in res30["G1"]["halves"].values())
          and res30["G1"]["improvement_pct_pooled"] >= 100 * MMI)
    g2 = res30["G2_encompassing"]["w_C_ci95"][0] > 0
    if g1 and g2:
        verdict = "PILOT-PASS — recommendation FOR the bounded Stage-3 data pull"
    elif (not g1) and (res30["G2_encompassing"]["w_C_ci95"][0] <= 0 <= res30["G2_encompassing"]["w_C_ci95"][1]
                       or res30["G2_encompassing"]["w_C_ci95"][1] < 0):
        verdict = ("PILOT-FAIL — challenger adds nothing over calibrated IV at the free-index level; "
                   "recommendation AGAINST Stage-3 spend (family stays pending-Pierce; index/mega-cap "
                   "level != full single-name universe)")
    else:
        verdict = "AMBIGUOUS — exactly one gate passes; see doc for what a purchase would and wouldn't resolve"

    out = {"prereg_sha256": "eb83df870d19b717207675d06c40c64a7cc7fbc5ac57401fb9bfa15031c36528",
           "tenor_30d": res30, "tenor_9d": res9,
           "gates": {"G1": bool(g1), "G2": bool(g2)},
           "replication_9d_sign_positive": bool(res9["G1"]["improvement_pct_pooled"] > 0),
           "VERDICT": verdict}
    (OUT / "iv_pilot_result.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
