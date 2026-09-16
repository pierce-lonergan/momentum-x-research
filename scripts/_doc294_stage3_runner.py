"""DOC 294 — STAGE-3 RUNNER (armed, blind until power certifies). Prereg frozen in _doc294_PREREG.md
(sha256 ae62ea59..., commit 5c2b0e7), gates inherited from the doc-292 draft: G1 QLIKE(C)<QLIKE(B) block-42
CI excl 0 pooled+halves AND >=2% improvement; G2 HLN lambda CI excl 0. PRE-UNBLINDING POWER GATE:
null-resolution <= 4.0% of baseline QLIKE, computed WITHOUT reading gate direction (sign-randomized
day-mean differentials -> bootstrap half-width). Death date 2026-08-15.

  --status : IV-panel coverage + (when assemblable) the power resolution — no gate direction is read
  --run    : REFUSES unless the power gate is green; then runs the frozen gates

DOC 296 CONFORMITY FIX: the original runner evaluated an h=1 next-day target (inherited from the
doc-291 builder) where the frozen prereg chain specifies h=21 forward variance (30d tenor) — the
skeptic fleet flagged this as a critical protocol deviation, and the h=1 "pass" it produced is VOID
as a Stage-3 verdict (see docs/research-log/296_*.md). This version implements the frozen target:
V(t) = (252/21) * sum rv_d^2(t+1..t+21), log scale; MZ calibration restricted to FULLY-REALIZED
rows (k <= j-22); walk-forward adds a 21-session train embargo. Blindness at h=21 is already broken
(the skeptic's disclosed counterfactual was negative), so any future run of this instrument is for
the record, not for untainted certification.
"""
from __future__ import annotations
import argparse, json, math
from pathlib import Path
import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
OUT = _ROOT / "data" / "research" / "doc294"
OUT.mkdir(parents=True, exist_ok=True)
IV_STORE = _ROOT / "data" / "iv_store" / "iv_eod.jsonl"
SEED = 294
POWER_MAX_RES = 0.04
MMI = 0.02
BLOCK = 42
DEATH = "2026-08-15"
HAR_F = ["log_rv_d", "log_rv_w", "log_rv_m"]
MIN_NAMES = 20          # panel must have this many names with >=120 IV days before power is even computed


def load_iv():
    rows = []
    if IV_STORE.exists():
        rows = [json.loads(l) for l in IV_STORE.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r.get("source") == "polygon_bs_inverted" and 21 <= int(r["tenor_days"]) <= 45]
    best = {}
    for r in rows:                       # nearest-30 per (ticker, date)
        k = (r["ticker"], r["date"])
        if k not in best or abs(r["tenor_days"] - 30) < abs(best[k]["tenor_days"] - 30):
            best[k] = r
    return best


def assemble():
    """(ticker, date) -> row with HAR features + calibrated IV + our features + next-day target."""
    import importlib.util
    _spec = importlib.util.spec_from_file_location("H", str(_ROOT / "scripts" / "_doc291_stage2_harrv.py"))
    H = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(H)
    iv = load_iv()
    names = sorted({t for t, _d in iv})
    counts = {n: sum(1 for (t, _d) in iv if t == n) for n in names}
    usable = [n for n in names if counts[n] >= 120]
    status = {"names_with_iv": len(names), "names_usable_120d": len(usable), "per_name_days": counts}
    if len(usable) < MIN_NAMES:
        return None, status, H
    H.LIQUID = usable
    df = H.build_panel()
    # FROZEN TARGET (doc 296 conformity): h=21 forward annualized variance, log scale —
    # V(t) = (252/21) * sum rv_d^2(t+1..t+21). Computed on the FULL per-name series BEFORE the
    # IV-availability filter so gaps in IV coverage don't distort the forward window.
    H_FWD = 21
    tgt = np.full(len(df), np.nan)
    for _t, g in df.groupby("ticker"):
        g = g.sort_values("d")
        idx = g.index.to_numpy()
        rv2 = np.exp(g.log_rv_d.values) ** 2
        for j in range(len(idx) - H_FWD):
            w = rv2[j + 1:j + 1 + H_FWD]
            if np.all(np.isfinite(w)):
                tgt[idx[j]] = math.log((252.0 / H_FWD) * float(w.sum()))
    df["target"] = tgt
    df = df[np.isfinite(df.target)].reset_index(drop=True)
    df["iv"] = [iv.get((t, d), {}).get("iv") for t, d in zip(df.ticker, df.d)]
    df = df[df.iv.notna()].reset_index(drop=True)
    # rolling MZ calibration per name on FULLY-REALIZED rows only: row k's target window ends at
    # k+21, so at forecast row j the window may use rows k <= j-22 (slice hi = j-21).
    df["log_iv2"] = np.log(df.iv.astype(float) ** 2)   # annualized variance scale, matching the target
    cal = np.full(len(df), np.nan)
    for t, g in df.groupby("ticker"):
        idx = g.index.to_numpy()
        y = g.target.values; x = g.log_iv2.values
        for j in range(len(idx)):
            hi = j - H_FWD
            lo = max(0, hi - 252)
            if hi - lo < 100:
                continue
            xs, ys = x[lo:hi], y[lo:hi]
            ok = np.isfinite(xs) & np.isfinite(ys)
            if ok.sum() < 100:
                continue
            b, a = np.polyfit(xs[ok], ys[ok], 1)
            cal[idx[j]] = a + b * x[j]
    df["log_fcal"] = cal
    df = df[np.isfinite(df.log_fcal)].reset_index(drop=True)
    status["panel_rows"] = len(df)
    return df, status, H


def day_mean_diffs(df, H):
    """walk-forward B vs C, return per-day mean QLIKE differentials + baseline day-mean losses.
    Used by BOTH power (sign-randomized) and gates (real) — computed once, direction read only by gates."""
    df2, pB, pC = None, None, None
    from sklearn.ensemble import HistGradientBoostingRegressor
    dates = sorted(df.d.unique())
    di = {d: i for i, d in enumerate(dates)}
    df = df.assign(di=df.d.map(di))
    y = df.target.values
    pB = np.full(len(df), np.nan); pC = np.full(len(df), np.nan)
    featB = HAR_F + ["log_fcal"]; featC = featB + H.OUR_F[:]  # doc-292 draft: 9 features, ratio dropped
    featC = [f for f in featC if f != "rv_ratio_dw"]
    for rp in range(120, len(dates), 21):
        tr = df.di < rp - 21     # doc 296: 21-session embargo — train targets fully realized pre-test
        te = (df.di >= rp) & (df.di < rp + 21)
        if te.sum() == 0 or tr.sum() < 1000:
            continue
        for feats, dest in [(featB, pB), (featC, pC)]:
            gb = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, min_samples_leaf=50,
                                               l2_regularization=1.0, random_state=SEED)
            ok = np.isfinite(y[tr.values])
            gb.fit(df.loc[tr, feats].values[ok], y[tr.values][ok])
            dest[te.values] = gb.predict(df.loc[te, feats].values)
    v = ~np.isnan(pB) & ~np.isnan(pC) & np.isfinite(y)
    sub = df.loc[v]
    V = np.exp(y[v])
    lB = V / np.exp(pB[v]) - np.log(V / np.exp(pB[v])) - 1
    lC = V / np.exp(pC[v]) - np.log(V / np.exp(pC[v])) - 1
    darr = sub.d.values
    dts = sorted(set(darr))
    dm = np.array([(lB[darr == d] - lC[darr == d]).mean() for d in dts])
    bm = np.array([lB[darr == d].mean() for d in dts])
    return dm, bm, dts, (pB, pC, v, sub, lB, lC)


def power_resolution(dm, bm, seed=SEED):
    """null-resolution WITHOUT reading direction: randomize the sign of each day-mean differential,
    block-bootstrap the mean, take the CI half-width as % of the baseline mean loss."""
    rng = np.random.default_rng(seed)
    n = len(dm); nb = int(np.ceil(n / BLOCK))
    mags = np.abs(dm)
    widths = []
    for _ in range(1000):
        signs = rng.choice([-1.0, 1.0], n)
        x = mags * signs
        starts = rng.integers(0, max(n - BLOCK, 1) + 1, nb)
        s = np.concatenate([x[st:st + BLOCK] for st in starts])[:n]
        widths.append(s.mean())
    lo, hi = np.percentile(widths, [2.5, 97.5])
    return float((hi - lo) / 2 / bm.mean())


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()
    df, status, H = assemble()
    if df is None:
        print(json.dumps({"power_gate": "NOT-COMPUTABLE", "death_date": DEATH, **status}, indent=2))
        return 0
    dm, bm, dts, artifacts = day_mean_diffs(df, H)
    if len(dm) == 0:
        # doc 296: post-purge the h=21 pipeline can score zero eval days (calibration realization +
        # walk-forward warm-up consume the thinned per-name coverage). Report, don't crash.
        print(json.dumps({**status, "n_eval_days": 0, "power_gate":
                          "NOT-COMPUTABLE (0 scored eval days on current panel)", "death_date": DEATH}, indent=2))
        return 0
    res = power_resolution(dm, bm)
    status.update(n_eval_days=len(dts), power_resolution_pct=round(100 * res, 3),
                  power_gate=("GREEN" if res <= POWER_MAX_RES else "RED (stay blind, keep collecting)"),
                  death_date=DEATH)
    if args.status or (args.run and res > POWER_MAX_RES):
        if args.run and res > POWER_MAX_RES:
            status["REFUSED"] = "power gate RED — gates stay blind (prereg ae62ea59...)"
        print(json.dumps(status, indent=2))
        return 0
    if args.run:
        # gates unblind (frozen math)
        rng = np.random.default_rng(SEED)
        n = len(dm); nb = int(np.ceil(n / BLOCK))
        boots = []
        for _ in range(5000):
            starts = rng.integers(0, max(n - BLOCK, 1) + 1, nb)
            s = np.concatenate([dm[st:st + BLOCK] for st in starts])[:n]
            boots.append(s.mean())
        lo, hi = np.percentile(boots, [2.5, 97.5])
        impr = float(dm.mean() / bm.mean())
        half = len(dm) // 2
        pB, pC, v, sub, lB, lC = artifacts
        # HLN
        yy = np.log(np.exp(sub.target.values))
        dcf = pC[v] - pB[v]
        e = yy - pB[v]
        lam = float(np.dot(dcf - dcf.mean(), e - e.mean()) / np.dot(dcf - dcf.mean(), dcf - dcf.mean()))
        out = {"G1": {"improvement_pct": round(100 * impr, 3), "ci95": [round(float(lo), 6), round(float(hi), 6)],
                      "half1_mean": round(float(dm[:half].mean()), 6), "half2_mean": round(float(dm[half:].mean()), 6)},
               "G2_HLN_lambda_point": round(lam, 4),
               "note": "full HLN block-CI + verdict map applied in the doc after skeptic pass",
               **status}
        (OUT / "stage3_gates.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
