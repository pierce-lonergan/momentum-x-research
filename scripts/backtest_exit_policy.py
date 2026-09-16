"""AUC -> DOLLARS: backtest the abstaining model-exit policy vs the fixed trail (doc 231 §6).

THE decisive gate. CPCV proved P(continue) is predictable + calibrated (AUC 0.768). This asks the
only question that matters: does an exit policy that USES it make more money than our current fixed
trail (D163)?

FAIR A/B (entry held constant across all policies, so only the EXIT differs):
  - Entry: long at the first RTH decision point of each eval ticker-day (~9:35). Entry realism is
    irrelevant to the A/B — every policy enters identically; only the exit rule changes.
  - HOLD-EOD: never exit early (exit at the last decision point).
  - TRAIL-ONLY(tau): the current approach — exit when price falls tau% off the running intraday high.
  - MODEL overlay(tau, tau_exit): the ABSTAINING policy — exit on (trail breach) OR (P(continue) <
    tau_exit); i.e. the classifier exits faders EARLY, and the trail is the fallback when the model is
    uncertain (abstention -> defer to the rule). Isolates the model's marginal contribution over trail.

NO LEAKAGE: P(continue) is an OUT-OF-FOLD prediction (purged K-fold by day-group; a ticker-day is
predicted only by a model trained on OTHER days). Same discipline as the CPCV validator.

HEADLINE METRIC: win/loss ratio (avg win / |avg loss|). Live realized is ~0.98 at 30% win = losing.
Does the model overlay push it up?

Usage: python scripts/backtest_exit_policy.py --tau 0.20 --hardstop 0.10
"""
from __future__ import annotations

import argparse
import numpy as np
import duckdb

PARQUET = "data/research/exit_labels.parquet"
FEATS = ["minute_idx", "ret_session", "ret_5m", "ret_15m", "ret_30m", "vwap_dist", "high_dist",
         "low_dist", "range_pos", "rvol_cum", "vol_accel_5m", "realized_vol_15m",
         "up_min_frac_15m", "gap_pct", "rvol_entry", "mfcs"]


def oos_predict(df, groups=6):
    """Purged K-fold by day-group -> an out-of-fold P(continue) for every row (no leakage)."""
    from sklearn.ensemble import HistGradientBoostingClassifier as GBM
    days = sorted(df["session_date"].unique())
    grp = {d: gi for gi, g in enumerate(np.array_split(days, groups)) for d in g}
    df = df.copy()
    df["_g"] = df["session_date"].map(grp)
    df["p_oos"] = np.nan
    for gi in range(groups):
        tr = df[df["_g"] != gi]
        te_idx = df.index[df["_g"] == gi]
        if len(te_idx) == 0 or tr["continued"].nunique() < 2:
            continue
        m = GBM(max_iter=250, learning_rate=0.05, max_depth=4, l2_regularization=1.0)
        m.fit(tr[FEATS], tr["continued"])
        df.loc[te_idx, "p_oos"] = m.predict_proba(df.loc[te_idx, FEATS])[:, 1]
    return df


def sim_day(rel, peak_rel, p, mode, tau, tau_exit, hardstop):
    """Return realized return for one ticker-day under a policy. rel/peak_rel/p aligned by minute."""
    entry = rel[0]
    pk = peak_rel[0]
    for i in range(1, len(rel)):
        pk = max(pk, peak_rel[i])
        trail_hit = rel[i] <= pk * (1 - tau)
        model_hit = (mode == "model") and (p[i] == p[i]) and (p[i] < tau_exit)
        hard_hit = rel[i] <= entry * (1 - hardstop)
        if trail_hit or model_hit or hard_hit:
            return rel[i] / entry - 1.0
    return rel[-1] / entry - 1.0


def stats(rets):
    r = np.array(rets)
    w = r[r > 0]; l = r[r < 0]
    wl = (w.mean() / abs(l.mean())) if len(w) and len(l) else float("nan")
    return dict(n=len(r), median=np.median(r), mean=r.mean(), win=(r > 0).mean(),
                avg_win=w.mean() if len(w) else 0, avg_loss=l.mean() if len(l) else 0, wl=wl)


def line(name, s):
    print(f"  {name:<26} n={s['n']:>4}  median {s['median']*100:>+5.1f}%  mean {s['mean']*100:>+5.1f}%  "
          f"win {s['win']*100:>3.0f}%  avgW {s['avg_win']*100:>+4.1f}%  avgL {s['avg_loss']*100:>+5.1f}%  "
          f"W/L {s['wl']:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tau", type=float, default=0.20, help="trailing-stop width (off running high)")
    ap.add_argument("--hardstop", type=float, default=0.10)
    a = ap.parse_args()

    df = duckdb.connect().execute(f"SELECT * FROM read_parquet('{PARQUET}')").df()
    df = df.dropna(subset=["continued"]).copy()
    for c in FEATS:
        df[c] = df[c].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0)
    df = oos_predict(df).dropna(subset=["p_oos"])
    print(f"{len(df):,} rows with OOS P(continue) | tau(trail)={a.tau:.0%} hardstop={a.hardstop:.0%}\n")

    # build per-ticker-day aligned series
    daydata = []
    for (tk, d), g in df.groupby(["ticker", "session_date"]):
        g = g.sort_values("minute_idx")
        rel = (1.0 + g["ret_session"].to_numpy())
        peak_rel = rel / (1.0 + g["high_dist"].to_numpy())   # intraday running high (high_dist = px/run_high - 1)
        p = g["p_oos"].to_numpy()
        if len(rel) >= 3 and rel[0] > 0:
            daydata.append((rel, peak_rel, p))
    print(f"simulated ticker-days: {len(daydata)}\n")

    # baselines
    hold = [sim_day(r, pk, p, "hold", 9.9, 0, 9.9) for r, pk, p in daydata]
    trail = [sim_day(r, pk, p, "trail", a.tau, 0, a.hardstop) for r, pk, p in daydata]
    print("=== BASELINES (entry held constant; return per ticker-day) ===")
    line("HOLD-to-EOD", stats(hold))
    line(f"TRAIL-only ({a.tau:.0%})", stats(trail))

    print(f"\n=== MODEL OVERLAY: exit on trail OR P(continue)<tau_exit (the abstaining policy) ===")
    best = None
    for te in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35]:
        m = [sim_day(r, pk, p, "model", a.tau, te, a.hardstop) for r, pk, p in daydata]
        s = stats(m); line(f"MODEL tau_exit={te:.2f}", s)
        if best is None or s["mean"] > best[1]["mean"]:
            best = (te, s)

    ts = stats(trail)
    print(f"\n=== VERDICT (model best vs trail-only) ===")
    bte, bs = best
    d_mean = (bs["mean"] - ts["mean"]) * 100
    d_wl = bs["wl"] - ts["wl"]
    print(f"  best model tau_exit={bte:.2f}: mean {bs['mean']*100:+.2f}% vs trail {ts['mean']*100:+.2f}%  "
          f"-> edge {d_mean:+.2f}pp/trade")
    print(f"  win/loss ratio: model {bs['wl']:.2f} vs trail {ts['wl']:.2f}  -> {d_wl:+.2f}")
    verdict = "CONVERTS — model overlay beats the fixed trail in $" if d_mean > 0.1 and bs["wl"] >= ts["wl"] \
        else ("MARGINAL — small/ambiguous edge" if d_mean > -0.1 else "DOES NOT CONVERT — AUC real but policy <= trail")
    print(f"  >>> {verdict}")
    print(f"  (live realized W/L is ~0.98 @30% win = losing; >1.0 here = the asymmetry flips)")


if __name__ == "__main__":
    main()
