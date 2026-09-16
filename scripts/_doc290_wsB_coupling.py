"""DOC 290 WORKSTREAM B — the attention-coupling regime cells (closes the doc-289 crack).
Cells + statistics + correction FROZEN in scripts/_doc290_PREREG.md (sha256 47b0aa95..., commit 167a911).

Question: is there ANY pre-declared regime cell in which the attention field (premarket $vol dominance)
couples to price outcomes? Doc 289 established global decoupling; this is the conditional test.

Sidedness note (interpretation fixed BEFORE running, disclosed): the prereg did not state sidedness; both
statistics are evaluated TWO-SIDED — a significant ANTI-coupling (doc-289's 1.9%-vs-6% direction) is also
coupling information, and two-sided is the conservative choice for the positive claim.
"""
from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path
import numpy as np
from scipy.stats import rankdata, spearmanr

_ROOT = Path(__file__).resolve().parent.parent
OUT = _ROOT / "data" / "research" / "doc290"
SEED = 290
B_PERM = 2000
B_BOOT = 5000
COST_FLOOR = 0.015
SPLIT = "2026-05-26"
FAM_ALPHA = 0.05 / 6  # Bonferroni across the 6 frozen families


def load_cohorts():
    rows = [json.loads(l) for l in (OUT / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    coh = defaultdict(list)
    for r in rows:
        if r.get("pm_dvol_share") is not None:
            coh[r["date"]].append(r)
    return {d: v for d, v in coh.items() if len(v) >= 5}


def spy_overnight_sign():
    """SPY session 09:30 open vs prior session close, from the minute warehouse (correct ET labels)."""
    import duckdb
    con = duckdb.connect()
    df = con.execute("""
        SELECT strftime(ts_et, '%Y-%m-%d') d, strftime(ts_et, '%H:%M') t, open, close
        FROM read_parquet('data/polygon_warehouse/minute_aggs/**/*.parquet', union_by_name=true)
        WHERE ticker='SPY' AND ts_et >= TIMESTAMP '2026-04-01'
          AND strftime(ts_et, '%H:%M') BETWEEN '09:30' AND '16:00'
        ORDER BY ts_et""").df()
    con.close()
    opens, closes = {}, {}
    for d, g in df.groupby("d"):
        opens[d] = float(g.iloc[0]["open"]); closes[d] = float(g.iloc[-1]["close"])
    days = sorted(opens)
    sign = {}
    for i, d in enumerate(days):
        if i == 0:
            continue
        sign[d] = 1 if opens[d] >= closes[days[i - 1]] else -1
    return sign


# ── the two frozen statistics ────────────────────────────────────────────────

def s1_pooled_spearman(cohorts, outcome_key, rng, B=B_PERM):
    """pooled within-cohort Spearman(pm_dvol_share rank, outcome rank), two-sided within-cohort perm p."""
    xs, ys, groups = [], [], []
    for d, mem in cohorts.items():
        m = [e for e in mem if e.get(outcome_key) is not None]
        if len(m) < 5:
            continue
        xs.extend(rankdata([e["pm_dvol_share"] for e in m]))
        ys.extend(rankdata([e[outcome_key] for e in m]))
        groups.append(len(m))
    if len(xs) < 30:
        return None
    xs, ys = np.array(xs, float), np.array(ys, float)
    rho = float(spearmanr(xs, ys)[0])
    null = np.empty(B)
    bounds = np.cumsum([0] + groups)
    for b in range(B):
        yp = ys.copy()
        for i in range(len(groups)):
            seg = slice(bounds[i], bounds[i + 1])
            yp[seg] = yp[seg][rng.permutation(groups[i])]
        null[b] = spearmanr(xs, yp)[0]
    p = float((1 + (np.abs(null) >= abs(rho)).sum()) / (1 + B))
    return {"rho": round(rho, 4), "p": p, "n_names": int(len(xs)), "n_cohorts": len(groups)}


def s2_winner_coincidence(cohorts, outcome_key, rng, B=B_PERM):
    """P(argmax pm_dvol_share == argmax outcome) vs random-pick null, two-sided."""
    hits, sizes = 0, []
    usable = 0
    for d, mem in cohorts.items():
        m = [e for e in mem if e.get(outcome_key) is not None]
        if len(m) < 5:
            continue
        usable += 1
        lead = max(m, key=lambda e: e["pm_dvol_share"])
        win = max(m, key=lambda e: e[outcome_key])
        hits += int(lead["ticker"] == win["ticker"])
        sizes.append(len(m))
    if usable < 8:
        return None
    acc = hits / usable
    null = np.empty(B)
    for b in range(B):
        h = sum(1 for n in sizes if rng.integers(n) == 0)  # random pick == winner (uniform)
        null[b] = h / usable
    mu = float(null.mean())
    p = float((1 + (np.abs(null - mu) >= abs(acc - mu)).sum()) / (1 + B))
    return {"acc": round(acc, 4), "null_mean": round(mu, 4), "p": p, "n_cohorts": usable}


def bh(pvals, alpha):
    """Benjamini-Hochberg: returns the set of indices rejected at level alpha."""
    order = np.argsort(pvals)
    m = len(pvals)
    keep = set()
    max_i = -1
    for rank_i, idx in enumerate(order, 1):
        if pvals[idx] <= alpha * rank_i / m:
            max_i = rank_i
    for rank_i, idx in enumerate(order, 1):
        if rank_i <= max_i:
            keep.add(int(idx))
    return keep


def policy_net(cohorts, outcome_key="ret_close"):
    """buy the cell's pm-$vol leader at decision, hold to close, net floor; session-blocked bootstrap CI."""
    rets, sess = [], []
    for d, mem in cohorts.items():
        m = [e for e in mem if e.get("ret_close") is not None]
        if len(m) < 5:
            continue
        lead = max(m, key=lambda e: e["pm_dvol_share"])
        rets.append(lead["ret_close"] - COST_FLOOR); sess.append(d)
    if len(rets) < 8:
        return None
    rng = np.random.default_rng(SEED)
    keys = sorted(set(sess))
    means = []
    arr = defaultdict(list)
    for r, s in zip(rets, sess):
        arr[s].append(r)
    for _ in range(B_BOOT):
        pick = rng.choice(len(keys), len(keys), replace=True)
        means.append(np.mean([x for i in pick for x in arr[keys[i]]]))
    return {"net_mean": round(float(np.mean(rets)), 4),
            "ci95": [round(float(np.percentile(means, 2.5)), 4), round(float(np.percentile(means, 97.5)), 4)],
            "n": len(rets)}


def main():
    cohorts = load_cohorts()
    dates = sorted(cohorts)
    rng = np.random.default_rng(SEED)

    # cohort-level covariates for cell assignment (all pre-decision)
    cH = {d: cohorts[d][0].get("cohort_pm_herfindahl") for d in dates}
    cCat = {d: float(np.mean([e["has_news"] for e in cohorts[d]])) for d in dates}
    cFR = {d: max([e["float_rotation"] for e in cohorts[d] if e.get("float_rotation") is not None] or [np.nan]) for d in dates}
    spy = spy_overnight_sign()

    def subset(ds):
        return {d: cohorts[d] for d in ds}

    hq = np.nanquantile([cH[d] for d in dates if cH[d] is not None], [0.25, 0.5, 0.75])
    med_cat = float(np.median([cCat[d] for d in dates]))
    frq = np.nanquantile([cFR[d] for d in dates if cFR[d] == cFR[d]], [0.25, 0.75])

    families = {
        "F1_condensation_quartile": {
            "Q1_dispersed": [d for d in dates if cH[d] is not None and cH[d] <= hq[0]],
            "Q2": [d for d in dates if cH[d] is not None and hq[0] < cH[d] <= hq[1]],
            "Q3": [d for d in dates if cH[d] is not None and hq[1] < cH[d] <= hq[2]],
            "Q4_condensed": [d for d in dates if cH[d] is not None and cH[d] > hq[2]],
        },
        "F2_catalyst": {
            "hi_catalyst": [d for d in dates if cCat[d] >= med_cat],
            "lo_catalyst": [d for d in dates if cCat[d] < med_cat],
        },
        "F3_float_rotation_extremes": {
            "top_quartile": [d for d in dates if cFR[d] == cFR[d] and cFR[d] >= frq[1]],
            "bottom_quartile": [d for d in dates if cFR[d] == cFR[d] and cFR[d] <= frq[0]],
        },
        "F4_spy_overnight": {
            "spy_up": [d for d in dates if spy.get(d) == 1],
            "spy_down": [d for d in dates if spy.get(d) == -1],
        },
        "F5_horizon": {"peak_run15": dates, "peak_run30": dates, "peak_run60": dates, "peak_run_full": dates},
        "F6_regime": {
            "early": [d for d in dates if d < SPLIT],
            "late": [d for d in dates if d >= SPLIT],
        },
    }

    results = {"prereg_sha256": "47b0aa9528b4d21038b0a2496e20373add41a6be704084d93da710f835c868dd",
               "n_cohorts": len(dates), "family_alpha_bonferroni": FAM_ALPHA, "families": {}, "tests_run": 0}
    survivors = []
    for fam, cells in families.items():
        fam_res = {}
        pvals, labels = [], []
        for cell, ds in cells.items():
            outcome = cell if fam == "F5_horizon" else "peak_run60"
            sub = subset(ds if fam != "F5_horizon" else dates)
            s1 = s1_pooled_spearman(sub, outcome, rng)
            s2 = s2_winner_coincidence(sub, outcome, rng)
            fam_res[cell] = {"n_cohorts_cell": len(ds), "S1": s1, "S2": s2}
            for name, st in (("S1", s1), ("S2", s2)):
                if st is not None:
                    pvals.append(st["p"]); labels.append((cell, name))
                    results["tests_run"] += 1
        rejected = bh(np.array(pvals), FAM_ALPHA) if pvals else set()
        fam_res["_bh_rejections"] = [f"{labels[i][0]}:{labels[i][1]} (p={pvals[i]})" for i in sorted(rejected)]
        results["families"][fam] = fam_res
        for i in sorted(rejected):
            survivors.append((fam, labels[i][0], labels[i][1], pvals[i]))

    # survivor gates: replication across split + policy floor
    results["survivors_pre_gate"] = [f"{f}/{c}:{s} p={p}" for f, c, s, p in survivors]
    final = []
    for fam, cell, stat, p in survivors:
        cells = families[fam]
        ds = cells[cell] if fam != "F5_horizon" else dates
        outcome = cell if fam == "F5_horizon" else "peak_run60"
        gate = {"family": fam, "cell": cell, "stat": stat, "p": p}
        if fam != "F6_regime":
            early = {d: cohorts[d] for d in ds if d < SPLIT}
            late = {d: cohorts[d] for d in ds if d >= SPLIT}
            fn = s1_pooled_spearman if stat == "S1" else s2_winner_coincidence
            re_, rl_ = fn(early, outcome, rng), fn(late, outcome, rng)
            gate["replication_early"], gate["replication_late"] = re_, rl_
            key = "rho" if stat == "S1" else "acc"
            ok = (re_ and rl_ and np.sign(re_[key] - (0 if stat == "S1" else re_.get("null_mean", 0))) ==
                  np.sign(rl_[key] - (0 if stat == "S1" else rl_.get("null_mean", 0)))
                  and re_["p"] < 0.05 and rl_["p"] < 0.05)
            gate["replicates"] = bool(ok)
        else:
            gate["replicates"] = "descriptive (F6 exempt per prereg)"
        pol = policy_net({d: cohorts[d] for d in ds})
        gate["policy_pm_leader_net"] = pol
        gate["clears_floor"] = bool(pol and pol["ci95"][0] > 0)
        gate["COUPLES"] = bool(gate.get("replicates") is True and gate["clears_floor"])
        final.append(gate)
    results["survivor_gates"] = final
    results["ANY_CELL_COUPLES"] = any(g.get("COUPLES") for g in final)

    (OUT / "wsB_coupling_result.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    # terse console summary
    print(json.dumps({"tests_run": results["tests_run"],
                      "bh_survivors_pre_gate": results["survivors_pre_gate"],
                      "ANY_CELL_COUPLES": results["ANY_CELL_COUPLES"]}, indent=2))
    for fam, fr in results["families"].items():
        line = []
        for cell, v in fr.items():
            if cell.startswith("_"):
                continue
            s1 = v["S1"]; s2 = v["S2"]
            line.append(f"{cell}: S1 rho={s1['rho'] if s1 else 'NA'} p={s1['p'] if s1 else 'NA'} | "
                        f"S2 acc={s2['acc'] if s2 else 'NA'} (null {s2['null_mean'] if s2 else 'NA'}) p={s2['p'] if s2 else 'NA'}")
        print(f"\n[{fam}] BH-rejected: {fr['_bh_rejections']}")
        for l in line:
            print("   ", l)


if __name__ == "__main__":
    main()
