"""DOC 291 STAGE 1 — IN-UNIVERSE DEFENSIVE UTILITY of the vol score (risk-shaping, not edge creation).
Gates frozen in scripts/_doc291_PREREG.md (sha256 5155f3df..., 7cc09fe). Disclosed substitution (frozen in
prereg): the live ledger has n~10 -> underpowered; this runs on the 875-event book under the fillable policy
(enter decision px, exit close, net 1.5%), equal-$ per event. Score = Stage-0 pooled-OOS FULL score.
Frozen expectation: variance/drawdown reduction at ~unchanged mean. Analysis only; passing gates emit a
config-diff PROPOSAL. Sizing may only SHRINK (5% cap frozen; doc-290 §6 standing constraint).
"""
from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path
import numpy as np
import importlib.util

_spec = importlib.util.spec_from_file_location("A", str(Path(__file__).resolve().parent / "_doc290_stageA_hlocal.py"))
A = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(A)

OUT = Path(__file__).resolve().parent.parent / "data" / "research" / "doc291"
SEED = 291
FLOOR = 0.015
B_BOOT = 5000


def session_boot(vals_by_session_fn, sessions_uniq, B=B_BOOT, seed=SEED):
    """bootstrap sessions; vals_by_session_fn(session_list) -> statistic. Returns (lo, hi)."""
    rng = np.random.default_rng(seed)
    stats = []
    n = len(sessions_uniq)
    for _ in range(B):
        pick = [sessions_uniq[i] for i in rng.integers(0, n, n)]
        stats.append(vals_by_session_fn(pick))
    return float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))


def book_metrics(rets_by_sess, weights_by_sess=None):
    """mean net/event (per-dollar), session-level cumulative max drawdown, stop-rate handled by caller."""
    sess = sorted(rets_by_sess)
    day_pnl = []
    tot_ret, tot_w = 0.0, 0.0
    for s in sess:
        r = np.array(rets_by_sess[s])
        w = np.array(weights_by_sess[s]) if weights_by_sess else np.ones(len(r))
        day_pnl.append(float((r * w).sum()))
        tot_ret += float((r * w).sum()); tot_w += float(w.sum())
    cum = np.cumsum(day_pnl)
    peak = np.maximum.accumulate(cum)
    maxdd = float((peak - cum).max())
    mean_per_dollar = tot_ret / tot_w if tot_w else 0.0
    return {"mean_net_per_dollar": mean_per_dollar, "maxdd_units": maxdd, "total_deployed": tot_w}


def main():
    rows = A.load()
    score = np.load(OUT / "stage0_full_score.npy")
    v = ~np.isnan(score)
    rows = [r for r, ok in zip(rows, v) if ok]
    score = score[v]
    rank = (np.argsort(np.argsort(score)) + 0.5) / len(score)   # pooled-OOS score rank in [0,1]
    net = np.array([r["ret_close"] for r in rows]) - FLOOR
    dd = np.array([r["maxdd60"] for r in rows])
    sess = np.array([r["date"] for r in rows])
    uniq = sorted(set(sess))

    def pack(mask=None, weights=None):
        rb, wb = defaultdict(list), defaultdict(list)
        for i in range(len(rows)):
            if mask is not None and not mask[i]:
                continue
            rb[sess[i]].append(net[i])
            wb[sess[i]].append(1.0 if weights is None else float(weights[i]))
        return rb, wb

    def stat_fn(rb, wb):
        def f(sess_pick):
            tot_r = sum(sum(np.array(rb[s]) * np.array(wb[s])) for s in sess_pick if s in rb)
            tot_w = sum(sum(wb[s]) for s in sess_pick if s in wb)
            return tot_r / tot_w if tot_w else 0.0
        return f

    results = {"prereg_sha256": "5155f3df3ffe3ec425c931ff96e05de508a0ddfe16a8c4c9dd9d08573a85a0c4",
               "n_events": len(rows), "n_sessions": len(uniq), "uses": {}}

    # baseline book
    rb0, wb0 = pack()
    base = book_metrics(rb0, wb0)
    base["stop_rate"] = float((dd <= -0.10).mean())
    results["baseline"] = {k: round(v2, 4) if isinstance(v2, float) else v2 for k, v2 in base.items()}
    base_mean_ci = session_boot(stat_fn(rb0, wb0), uniq)

    gates_p = []

    # U1 exclusion at two pre-declared thresholds
    for thr, nm in [(0.9, "U1_excl_topdecile"), (0.8, "U1_excl_topquintile")]:
        mask = rank < thr
        rb, wb = pack(mask)
        m = book_metrics(rb, wb)
        m["stop_rate"] = float((dd[mask] <= -0.10).mean())
        # improvement stats
        dd_impr = (base["maxdd_units"] - m["maxdd_units"]) / base["maxdd_units"] if base["maxdd_units"] else 0.0
        sr_impr = (base["stop_rate"] - m["stop_rate"]) / base["stop_rate"] if base["stop_rate"] else 0.0
        d_mean = m["mean_net_per_dollar"] - base["mean_net_per_dollar"]
        # session-blocked bootstrap of the mean-net delta (paired by session)
        def paired(sp, rb=rb, wb=wb):
            a = stat_fn(rb, wb)(sp); b = stat_fn(rb0, wb0)(sp)
            return a - b
        lo, hi = session_boot(paired, uniq, seed=SEED + hash(nm) % 1000)
        # stop-rate bootstrap
        def sr_delta(sp, mask=mask):
            pick = np.isin(sess, sp)
            b_ = (dd[pick] <= -0.10).mean()
            m_ = (dd[pick & mask] <= -0.10).mean() if (pick & mask).sum() else b_
            return b_ - m_
        slo, shi = session_boot(sr_delta, uniq, seed=SEED + 7)
        no_harm = hi >= 0  # CI does not exclude 0 from below is wrong direction; harm = delta<0 certain -> hi<0
        risk_ok = (dd_impr >= 0.20 or sr_impr >= 0.20) and slo > 0 if sr_impr >= 0.20 else (dd_impr >= 0.20)
        results["uses"][nm] = {"n_kept": int(mask.sum()), "mean_net": round(m["mean_net_per_dollar"], 4),
                               "delta_mean_net": round(d_mean, 4), "delta_mean_ci95": [round(lo, 4), round(hi, 4)],
                               "maxdd_impr_rel": round(dd_impr, 3), "stop_rate": round(m["stop_rate"], 3),
                               "stop_rate_impr_rel": round(sr_impr, 3), "stop_delta_ci95": [round(slo, 4), round(shi, 4)],
                               "gate_pass": bool(risk_ok and no_harm)}
        gates_p.append((nm, results["uses"][nm]["gate_pass"]))

    # U2 inverse-vol sizing (shrink-only)
    w2 = 1.0 - rank
    rb, wb = pack(None, w2)
    m = book_metrics(rb, wb)
    dd_impr = (base["maxdd_units"] - m["maxdd_units"]) / base["maxdd_units"] if base["maxdd_units"] else 0.0
    def paired2(sp):
        return stat_fn(rb, wb)(sp) - stat_fn(rb0, wb0)(sp)
    lo, hi = session_boot(paired2, uniq, seed=SEED + 21)
    results["uses"]["U2_inverse_vol_sizing"] = {
        "mean_net_per_dollar": round(m["mean_net_per_dollar"], 4),
        "delta_mean_per_dollar": round(m["mean_net_per_dollar"] - base["mean_net_per_dollar"], 4),
        "delta_ci95": [round(lo, 4), round(hi, 4)],
        "maxdd_impr_rel": round(dd_impr, 3), "deployed_frac": round(m["total_deployed"] / base["total_deployed"], 3),
        "gate_pass": bool(dd_impr >= 0.20 and hi >= 0)}
    gates_p.append(("U2", results["uses"]["U2_inverse_vol_sizing"]["gate_pass"]))

    # U3 stop-width: fixed -10% vs conditioned (-7% low-score half, -13% high-score half)
    def stopped_ret(thr_arr):
        out = net.copy()
        stopped = dd <= -thr_arr
        out[stopped] = -thr_arr[stopped] - FLOOR
        return out, stopped
    fix_r, fix_s = stopped_ret(np.full(len(rows), 0.10))
    cond_thr = np.where(rank >= 0.5, 0.13, 0.07)
    con_r, con_s = stopped_ret(cond_thr)
    def mk(rets):
        rb_, wb_ = defaultdict(list), defaultdict(list)
        for i in range(len(rows)):
            rb_[sess[i]].append(rets[i]); wb_[sess[i]].append(1.0)
        return rb_, wb_
    rbf, wbf = mk(fix_r); rbc, wbc = mk(con_r)
    mf, mc = book_metrics(rbf, wbf), book_metrics(rbc, wbc)
    def paired3(sp):
        return stat_fn(rbc, wbc)(sp) - stat_fn(rbf, wbf)(sp)
    lo, hi = session_boot(paired3, uniq, seed=SEED + 33)
    sr_f, sr_c = float(fix_s.mean()), float(con_s.mean())
    dd_impr = (mf["maxdd_units"] - mc["maxdd_units"]) / mf["maxdd_units"] if mf["maxdd_units"] else 0.0
    sr_impr = (sr_f - sr_c) / sr_f if sr_f else 0.0
    results["uses"]["U3_stop_width"] = {
        "fixed": {"mean_net": round(mf["mean_net_per_dollar"], 4), "stop_rate": round(sr_f, 3),
                  "maxdd": round(mf["maxdd_units"], 3)},
        "conditioned": {"mean_net": round(mc["mean_net_per_dollar"], 4), "stop_rate": round(sr_c, 3),
                        "maxdd": round(mc["maxdd_units"], 3)},
        "delta_mean_ci95": [round(lo, 4), round(hi, 4)], "maxdd_impr_rel": round(dd_impr, 3),
        "stop_rate_impr_rel": round(sr_impr, 3),
        "gate_pass": bool((dd_impr >= 0.20 or sr_impr >= 0.20) and hi >= 0)}
    gates_p.append(("U3", results["uses"]["U3_stop_width"]["gate_pass"]))

    results["any_gate_pass"] = any(g for _n, g in gates_p)
    (OUT / "stage1_defensive.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
