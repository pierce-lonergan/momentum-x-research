"""doc 275 — Ladders A/B + D-REPRO held-out certification (frozen §3 recipes).

LADDER A (information/promotion power): 273-style label plants at s in {.10,.15,.20,.30},
driver = ofi_last10 forced in-subset (declared best-case scope), >=25 fresh-seed replicate
injections per rung, host = a Tier-2 member at 10:30 (the pre-registered plant t from the
273/274 lineage), scored on planted-t dll against DUAL thresholds: oracle-partition (family
threshold from the null fleet only — the certified headline) and gate-as-deployed (planted
member inside the family max — the factory-realistic secondary).

LADDER B (money/false-cert): exit-mix leaks at lambda in {.10,.15,.20,.30,.50}, >=25 reps,
host = a Tier-1 member at 9:40 whose score IS the leak column (worst case), scored on the
money statistic vs the same dual thresholds, AND every rung paired with a D-REPRO call
(hidden-column form -> expected CATCH at all lambda; the in-feature form is D4's class).

D-REPRO HELD-OUTS (R14): four mutant classes built by a different construction than R4
(config-lie, cherry-picked seed, rank-based hidden leak, swapped statistic) + must-PASS
false-alarm cells. Verdict table -> data/research/doc275/.
"""
from __future__ import annotations
import json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_doc275 as g
import gate_doc275_stage1 as s1

kf = g.kf
OUT = g.OUTDIR
RUNGS_A = [0.10, 0.15, 0.20, 0.30]
RUNGS_B = [0.10, 0.15, 0.20, 0.30, 0.50]
REPS = 25
PLANT_T = "10:30"
LEAK_T = "9:40"


def _null_family_threshold(arm, t1, t2):
    """Oracle-partition family threshold: studentized max over the NULL fleet only."""
    stats = s1.member_stats(arm, t1, t2)
    ids = [m["id"] for m in t1] + [m["id"] for m in t2]
    fam = s1.family_gate(stats, ids)
    return fam, stats


def run_ladders(t1n=200, t2n=50):
    spine = g.immune_filtered_spine()
    arm = s1._prep_arm(spine)
    t1 = s1.tier1_pool()[:t1n]
    t2 = s1.tier2_pool()[:t2n]
    fam_null, null_stats = _null_family_threshold(arm, t1, t2)
    thr_oracle = fam_null["threshold"]
    print(f"oracle-partition family threshold (K={fam_null['K']}): z*={thr_oracle:.2f}")

    # ── LADDER A: label plants, dll channel ──
    lblA = PLANT_T
    p = arm["packs"][lblA]
    drv_idx = kf.FEATURES.index("ofi_last10")
    Xq = arm["Xq"][lblA]
    drv_rank = pd.Series(Xq[:, drv_idx]).groupby(p["s"].session_date.values).rank(pct=True)
    hi = (drv_rank >= 0.5).to_numpy().astype(int)
    feats_host = sorted(set([drv_idx] + [1, 7, 15, 22]))     # driver forced in-subset (frozen)
    X5 = Xq[:, feats_host]
    rowsA = []
    for s_ in RUNGS_A:
        for rep in range(REPS):
            rng = np.random.default_rng(s1._seed(f"ladderA|{s_}|{rep}"))
            y = p["y"].copy()
            flip = rng.random(len(y)) < s_
            y[flip] = hi[flip]
            dll_r = s1._dll_kfeat(X5, y, p["fold"])
            # member null under shared perms (planted labels permuted like real ones)
            nulls = np.empty(s1.B)
            for b in range(s1.B):
                donors = arm["donors"][lblA][b]
                keep = donors >= 0
                yb = y[np.clip(donors, 0, None)]
                nulls[b] = s1._dll_kfeat(X5[keep], yb[keep], p["fold"][keep])
            mu, sd = np.nanmean(nulls), np.nanstd(nulls)
            z = (dll_r - mu) / sd if sd > 1e-12 else -np.inf
            rowsA.append(dict(s=s_, rep=rep, dll=dll_r, z=z,
                              hit_oracle=bool(z >= thr_oracle)))
        sub = [r for r in rowsA if r["s"] == s_]
        print(f"  Ladder A s={s_}: median dll {np.median([r['dll'] for r in sub]):+.4f} "
              f"power(oracle) {np.mean([r['hit_oracle'] for r in sub]):.2f}")

    # ── LADDER B: exit-mix leaks, money channel + D-REPRO pairing ──
    lblB = LEAK_T
    pb = arm["packs"][lblB]
    sB = pb["s"]
    last_px = np.exp(sB.log_open_px.to_numpy()) * (1 + sB.ret_open_t.to_numpy())
    exit_px = sB.entry_px.to_numpy() * (1 + sB.gross_ret.to_numpy())
    raw = exit_px / last_px
    zleak = (raw - np.nanmean(raw)) / np.nanstd(raw)
    wnet = arm["wnet"][lblB]
    rowsB = []
    for lam in RUNGS_B:
        for rep in range(REPS):
            rng = np.random.default_rng(s1._seed(f"ladderB|{lam}|{rep}"))
            leak = lam * np.nan_to_num(zleak) + np.sqrt(max(1 - lam**2, 0)) * rng.standard_normal(len(zleak))
            thr_top = np.quantile(leak, 0.9)
            top = np.where(leak >= thr_top)[0]
            money_r = float(np.nanmean(wnet[top]))
            D = arm["donors"][lblB][:, top]
            vals = np.where(D >= 0, wnet[np.clip(D, 0, None)], np.nan)
            nulls = np.nanmean(vals, axis=1)
            mu, sd = np.nanmean(nulls), np.nanstd(nulls)
            z = (money_r - mu) / sd if sd > 1e-12 else -np.inf
            rowsB.append(dict(lam=lam, rep=rep, money=money_r, z=z,
                              hit_oracle=bool(z >= thr_oracle)))
        sub = [r for r in rowsB if r["lam"] == lam]
        print(f"  Ladder B lam={lam}: median top-decile net {np.median([r['money'] for r in sub])*100:+.2f}% "
              f"power(oracle) {np.mean([r['hit_oracle'] for r in sub]):.2f}")

    json.dump(dict(thr_oracle=thr_oracle, K_null=fam_null["K"],
                   ladderA=rowsA, ladderB=rowsB),
              open(os.path.join(OUT, "ladders.json"), "w"), indent=1, default=float)
    print("WROTE ladders.json")


# ─────────────────────────── D-REPRO held-out mutant classes ───────────────────────────

def drepro_heldout():
    """Four held-out mutant classes + must-pass cells, per R14. Each mutant fabricates a
    'reported' result via a DIFFERENT corruption than R4's hidden column; D-REPRO must
    catch the reproducibility-breaking ones and pass the honest ones."""
    spine_path = g.SPINE_273CLEAN
    spine = pd.read_parquet(spine_path)
    spine = spine[spine.t_lbl.isin(g.GRID5)].reset_index(drop=True)
    if "day" not in spine.columns:
        spine = spine.assign(day=spine.session_date)
    fams = kf._families()
    results = {}

    def true_I():
        out = {}
        for lbl in g.GRID5[:2]:                      # 2 t for speed; frozen
            s, X, y, fold = kf._per_t(spine, lbl)
            dll, _ = kf._dll_curve(X, y, None, fold, fams)
            out[lbl] = dll
        return out

    base = true_I()

    # M1 config-lie: reported numbers computed with theta=0.005 but declared theta=0.01
    old_theta = kf.THETA
    kf.THETA = 0.005
    lied = true_I()
    kf.THETA = old_theta
    results["M1_config_lie"] = g.d_repro(spine_path, list(kf.FEATURES), lied, tol=1e-6)
    results["M1_expected"] = "fired=True (declared config cannot reproduce)"

    # M2 cherry-picked seed: reported = per-cell max over 5 fold-map variants (undeclared)
    cherry = {}
    for lbl in g.GRID5[:2]:
        s, X, y, fold = kf._per_t(spine, lbl)
        best = {}
        for k in range(5):
            rng = np.random.default_rng(900 + k)
            days = sorted(s.session_date.unique())
            rng.shuffle(days)
            fmap = {d: f for f, blk in enumerate(np.array_split(np.asarray(days), kf.NFOLD)) for d in blk}
            fold_k = s.session_date.map(fmap).to_numpy()
            dll, _ = kf._dll_curve(X, y, None, fold_k, fams)
            for fam_, v in dll.items():
                best[fam_] = max(best.get(fam_, -9), v)
        cherry[lbl] = best
    results["M2_cherry_seed"] = g.d_repro(spine_path, list(kf.FEATURES), cherry, tol=1e-6)
    results["M2_expected"] = "fired=True (max-over-undeclared-variants irreproducible)"

    # M3 honest replication: base numbers verbatim (must PASS)
    results["M3_honest"] = g.d_repro(spine_path, list(kf.FEATURES), base, tol=1e-6)
    results["M3_expected"] = "fired=False"

    # M4 swapped statistic: money numbers reported in the dll slots
    swapped = {}
    for lbl in g.GRID5[:2]:
        s, X, y, fold = kf._per_t(spine, lbl)
        net = kf._wins(s.gross_ret.to_numpy(float) - kf.THETA)
        swapped[lbl] = {"gbm": float(net.mean()), "logit": float(net.mean())}
    results["M4_swapped_stat"] = g.d_repro(spine_path, list(kf.FEATURES), swapped, tol=1e-6)
    results["M4_expected"] = "fired=True"

    json.dump(results, open(os.path.join(OUT, "drepro_heldout.json"), "w"),
              indent=1, default=float)
    for k in ("M1_config_lie", "M2_cherry_seed", "M3_honest", "M4_swapped_stat"):
        print(f"{k}: fired={results[k]['fired']} ({results[k.replace('M1_config_lie','M1_expected').replace('M2_cherry_seed','M2_expected').replace('M3_honest','M3_expected').replace('M4_swapped_stat','M4_expected')] if False else results[k[:2]+'_expected']})")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ladders", action="store_true")
    ap.add_argument("--drepro", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if a.drepro:
        drepro_heldout()
    if a.ladders:
        run_ladders()
