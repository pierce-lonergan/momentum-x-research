"""DOC 292 WORKSTREAM B — the RV FORWARD SHADOW-LEDGER: converts the doc-291 backtest into accumulating
genuinely-forward evidence. Certification bar FROZEN in scripts/_doc292_PREREG.md (sha256 eb83df87...,
commit 73cc3bc): the ledger may be cited as confirmation ONLY at n>=60 forward sessions with day-blocked
bootstrap QLIKE-improvement CI95 excluding 0 AND pooled improvement >=2.0%; review-for-dead at n=120 if the
CI still includes 0. Until then: PENDING-COLLECTION. The ledger only collects; it changes no decision.

Design (rocket-gate durability pattern): append-only data/reports/rv_forward_ledger.jsonl, one row per
scored session {date, n_names, qlike_har, qlike_challenger, improvement_pct, computed_at}. Scoring session t
uses models trained ONLY on rows whose next-day targets are realized by t-1 (train date index <= t-2),
predicting from day t-1 features -- a true out-of-sample forward score. Self-repairing: --append scores every
unscored session the warehouse can support (bars land T+1), so a missed night heals itself. FORWARD_START
frozen = 2026-07-13 (the first session after this instrument was built); earlier backfill rows are marked
retro=true and excluded from certification, mirroring the rocket-gate discipline.

Usage:  python scripts/_doc292_rv_forward_ledger.py --append   # nightly hook (post_close_scorecard)
        python scripts/_doc292_rv_forward_ledger.py --status   # certification status
"""
from __future__ import annotations
import argparse, json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
LEDGER = _ROOT / "data" / "reports" / "rv_forward_ledger.jsonl"
FORWARD_START = "2026-07-13"
N_MIN, N_REVIEW = 60, 120
MMI = 0.02
SEED = 292
HAR_F = ["log_rv_d", "log_rv_w", "log_rv_m"]


def read_ledger():
    rows = []
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def write_row(row):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def qlike(V, F):
    r = V / np.maximum(F, 1e-12)
    return r - np.log(r) - 1.0


def score_sessions(max_new=10):
    """score every unscored session (self-repairing); returns list of appended rows."""
    import importlib.util
    from sklearn.ensemble import HistGradientBoostingRegressor
    _spec = importlib.util.spec_from_file_location("H", str(_ROOT / "scripts" / "_doc291_stage2_harrv.py"))
    H = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(H)

    df = H.build_panel()          # includes target = next-day log RV per (ticker, day)
    dates = sorted(df.d.unique())
    di = {d: i for i, d in enumerate(dates)}
    df = df.assign(di=df.d.map(di))
    done = {r["date"] for r in read_ledger()}
    # a session t is scoreable if it has a prior day t-1 (features) and enough history
    candidates = [d for d in dates[H.MIN_TRAIN_SESSIONS + 2:] if d not in done]
    candidates = candidates[-max_new:]
    appended = []
    feats_c = HAR_F + H.OUR_F
    for d in candidates:
        t = di[d]
        # realized rv for session t per name (from the panel's own rv column)
        today = df[df.di == t]
        prev = df[df.di == t - 1]
        if today.empty or prev.empty:
            continue
        train = df[df.di <= t - 2]      # targets realized by t-1
        if len(train) < 1000:
            continue
        y = train["target"].values
        ok = np.isfinite(y)
        preds = {}
        for feats, name in [(HAR_F, "har"), (feats_c, "challenger")]:
            gb = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, min_samples_leaf=50,
                                               l2_regularization=1.0, random_state=SEED)
            gb.fit(train.loc[ok, feats].values, y[ok])
            preds[name] = gb.predict(prev[feats].values)
        merged = prev[["ticker"]].assign(f_har=preds["har"], f_ch=preds["challenger"]).merge(
            today[["ticker", "rv"]], on="ticker", how="inner")
        if len(merged) < 30:
            continue
        V = merged["rv"].values ** 2
        lh = qlike(V, np.exp(merged["f_har"].values) ** 2)
        lc = qlike(V, np.exp(merged["f_ch"].values) ** 2)
        row = {"date": d, "n_names": int(len(merged)),
               "qlike_har": round(float(lh.mean()), 6), "qlike_challenger": round(float(lc.mean()), 6),
               "improvement_pct": round(100 * float((lh.mean() - lc.mean()) / lh.mean()), 4),
               "retro": d < FORWARD_START,
               "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        write_row(row)
        appended.append(row)
    return appended


def status():
    rows = [r for r in read_ledger() if not r.get("retro") and r.get("n_names", 0) >= 30]
    out = {"n_forward_sessions": len(rows), "n_min": N_MIN, "n_review": N_REVIEW, "mmi": MMI,
           "forward_start": FORWARD_START}
    if not rows:
        out["status"] = "PENDING-COLLECTION"
        return out
    lh = np.array([r["qlike_har"] for r in rows])
    lc = np.array([r["qlike_challenger"] for r in rows])
    d = lh - lc
    impr = float(d.mean() / lh.mean())
    rng = np.random.default_rng(SEED)
    block = 10
    nb = int(np.ceil(len(d) / block))
    boots = []
    for _ in range(5000):
        starts = rng.integers(0, max(len(d) - block, 1) + 1, nb)
        s = np.concatenate([d[st:st + block] for st in starts])[:len(d)]
        boots.append(s.mean())
    lo, hi = float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))
    out.update(pooled_improvement_pct=round(100 * impr, 3), diff_ci95=[round(lo, 6), round(hi, 6)])
    if len(rows) >= N_MIN and lo > 0 and impr >= MMI:
        out["status"] = "CONFIRMED (forward)"
    elif len(rows) >= N_REVIEW and lo <= 0:
        out["status"] = "REVIEW-FOR-DEAD"
    else:
        out["status"] = "PENDING-COLLECTION"
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--append", action="store_true", help="nightly: score unscored sessions (self-repairing)")
    ap.add_argument("--status", action="store_true", help="certification status vs the frozen bar")
    args = ap.parse_args()
    if args.append:
        rows = score_sessions()
        for r in rows:
            print(f"rv-forward {r['date']}: n={r['n_names']} qlike HAR {r['qlike_har']:.4f} vs CH "
                  f"{r['qlike_challenger']:.4f} ({r['improvement_pct']:+.2f}%) retro={r['retro']} [appended]")
        if not rows:
            print("rv-forward: nothing new to score")
        print(json.dumps(status(), indent=2))
        return 0
    print(json.dumps(status(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
