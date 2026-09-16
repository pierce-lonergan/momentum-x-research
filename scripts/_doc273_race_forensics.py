"""doc 273: pre-registered V-RACE forensics — loading inspection + mid-range-entry
robustness at the maxT-significant t (13:00, 14:00), plus the score-decile payoff
profile (the anti-selection shape check). Descriptive; the verdict logic is frozen."""
import importlib.util
import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location("kf", "scripts/knowability_frontier_doc273.py")
kf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kf)

df = kf._load_spine()
fams = kf._families()

for lbl in ("13:00", "14:00"):
    s, X, y, fold = kf._per_t(df, lbl)
    print(f"\n=== {lbl} (n={len(s)}, base {y.mean()*100:.1f}%) ===")

    # 1) logit loadings on the full data (descriptive; the OOF dll already established skill)
    mk = fams["logit"](len(y))
    mk.fit(X, y)
    coefs = mk.named_steps["logisticregression"].coef_[0]
    order = np.argsort(-np.abs(coefs))
    print("  top-8 |logit| loadings (rank-gauss space):")
    for i in order[:8]:
        print(f"    {kf.FEATURES[i]:>22} {coefs[i]:+.3f}")

    # 2) decile payoff profile of GBM and LOGIT OOF scores (net of 1%, winsorized)
    net = s.gross_ret.to_numpy(float) - kf.THETA
    wnet = kf._wins(net)
    for fam in ("gbm", "logit"):
        dll, oof = kf._dll_curve(X, y, None, fold, {fam: fams[fam]})
        if fam == "logit":   # _dll_curve only returns gbm oof; refit per-fold for logit
            oof = np.full(len(y), np.nan)
            for f in range(kf.NFOLD):
                tr, te = fold != f, fold == f
                if te.sum() and len(np.unique(y[tr])) == 2:
                    m = fams["logit"](int(tr.sum())); m.fit(X[tr], y[tr])
                    oof[te] = m.predict_proba(X[te])[:, 1]
        ok = ~np.isnan(oof)
        q = pd.qcut(pd.Series(oof[ok]).rank(method="first"), 10, labels=False)
        prof = [float(wnet[ok][q == d].mean()) * 100 for d in range(10)]
        hit = [float(y[ok][q == d].mean()) * 100 for d in range(10)]
        print(f"  {fam} OOF decile net-mean% (D1=lowest..D10=highest score):")
        print("    net:", " ".join(f"{v:+.1f}" for v in prof))
        print("    hit:", " ".join(f"{v:.0f}" for v in hit))

# 3) mid-range-entry robustness: rebuild labels at 13:00/14:00 with entry = mid of
#    the entry-window price RANGE (kills VWAP/bounce coupling), rerun logit dll.
print("\n=== mid-range-entry robustness (pre-registered confound check) ===")
corpus = kf._corpus()
jobs = {(r.ticker, r.session_date): (float(r.base_close), float(r.adv20))
        for r in corpus.itertuples()}
SIG_SEC = {780 * 60: "13:00", 840 * 60: "14:00"}   # ET minutes -> seconds (the unit trap, again)
mid_rows = {lbl: {} for lbl in SIG_SEC.values()}
for (tk, d), (bc, adv) in jobs.items():
    got = kf._read_day(tk, d)
    if got is None:
        continue
    sec, px, sz = got
    rth = sec >= kf.RTH0
    rsec, rpx, rsz = sec[rth], px[rth], sz[rth]
    if len(rpx) < 5:
        continue
    ea, eb = np.searchsorted(rsec, kf.EXIT0), np.searchsorted(rsec, kf.EXIT1)
    if eb > ea:
        exit_px = float((rpx[ea:eb] * rsz[ea:eb]).sum() / rsz[ea:eb].sum())
    else:
        late = np.searchsorted(rsec, kf.EXIT_FB)
        exit_px = float(rpx[-1]) if len(rpx) > late else np.nan
    if not np.isfinite(exit_px):
        continue
    for sec_t, lbl in SIG_SEC.items():
        ia, ib = int(np.searchsorted(rsec, sec_t + 5)), int(np.searchsorted(rsec, sec_t + 300))
        if ib - ia >= kf.ENTRY_MIN_PRINTS and rsz[ia:ib].sum() >= kf.ENTRY_MIN_SH:
            entry_mid = float((rpx[ia:ib].max() + rpx[ia:ib].min()) / 2.0)
            mid_rows[lbl][(tk, d)] = exit_px / entry_mid - 1.0

for lbl in SIG_SEC.values():
    s, X, y_vwap, fold = kf._per_t(df, lbl)
    key = list(zip(s.ticker, s.session_date))
    gm = np.array([mid_rows[lbl].get(k, np.nan) for k in key])
    ok = ~np.isnan(gm)
    y_mid = (gm[ok] > kf.THETA).astype(int)
    dll, _ = kf._dll_curve(X[ok], y_mid, None, fold[ok], {"logit": fams["logit"]})
    agree = float((y_mid == y_vwap[ok]).mean())
    print(f"  {lbl}: n={ok.sum()} label-agreement(vwap vs mid)={agree*100:.1f}% "
          f"logit dll VWAP-entry={kf._dll_curve(X, y_vwap, None, fold, {'logit': fams['logit']})[0]['logit']:+.4f} "
          f"-> MID-entry={dll['logit']:+.4f}")
