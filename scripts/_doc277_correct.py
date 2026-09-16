"""doc277 CORRECTED analysis after the hostile retest. Fixes (1) the brief-field-corruption that dropped
6 runs, (2) the OMIT seed-match false-catches (leaky tokens {'2026'}, 'liquidity', '100'). The retest
hand-verified that ALL 3 OMIT seeds are missed by ALL tiers x frames (0 true catches). Recompute the honest
Delta + item-permutation p under the corrected OMIT catches. NUM/CODE/MECH catches (numeric tokens, less leaky)
are kept from the matcher but the OMIT catches are set to the hand-verified 0."""
import json
import os
import numpy as np
from itertools import permutations

D = "data/research/doc277"
rows = json.load(open(os.path.join(D, "catch_matrix.json"), encoding="utf-8"))

# CORRECTION 1+2: hand-verified (hostile-retest + Sonnet peer, both independent) — all OMIT seeds, all
# tiers x frames = MISS (0). The prior matcher false-caught bridge (token {'2026'}) and collider ('liquidity'/'100').
OMIT_SEEDS = {r["seed"] for r in rows if r["class"] == "OMIT"}
for r in rows:
    if r["class"] == "OMIT" and r["catch"] == 1:
        r["catch"] = 0  # correct the false-catch
        r["_corrected"] = True

TIERS = ["opus", "sonnet", "haiku"]
CLASSES = ["NUM", "CODE", "OMIT", "MECH"]
SC = {r["seed"]: r["class"] for r in rows}


def catch_map(frame):
    m = {}
    for r in rows:
        if r["frame"] == frame and r["tier"] in TIERS:
            m.setdefault(r["seed"], {})[r["tier"]] = r["catch"]
    return m


def excess(cm):
    out = {}
    for cls in CLASSES:
        seeds = [s for s, d in cm.items() if SC[s] == cls and all(d.get(t) is not None for t in TIERS)]
        if not seeds:
            continue
        miss = {t: np.mean([1 - cm[s][t] for s in seeds]) for t in TIERS}
        exp = float(np.prod([miss[t] for t in TIERS]))
        obs = float(np.mean([1 if all(cm[s][t] == 0 for t in TIERS) else 0 for s in seeds]))
        out[cls] = {"n": len(seeds), "obs_allmiss": round(obs, 3), "exp_allmiss": round(exp, 3),
                    "excess": round(obs - exp, 3), "marg_miss": {t: round(miss[t], 2) for t in TIERS}}
    return out


cmF1 = catch_map("F1")
ex = excess(cmF1)
print("=== CORRECTED joint-miss / excess by class (F1, OMIT hand-verified 0-catch) ===")
for cls in CLASSES:
    if cls in ex:
        print(f"  {cls}: {ex[cls]}")

d = (ex.get("OMIT", {}).get("excess", 0)) - (ex.get("NUM", {}).get("excess", 0))
print(f"\nCORRECTED Delta = excess(OMIT) - excess(NUM) = {d}")

# honest item/class-label permutation p: shuffle class labels across complete-F1 items, preserve each item's
# joint 3-tier miss vector (the exchangeability null the retest endorsed)
seeds = [s for s, dd in cmF1.items() if all(dd.get(t) is not None for t in TIERS)]
lab = [SC[s] for s in seeds]
allmiss = {s: 1 if all(cmF1[s][t] == 0 for t in TIERS) else 0 for s in seeds}
exp_by_lab = {}  # per observed permutation we recompute excess from marginals -> but marginals depend on labels
def delta_for_labels(labels):
    byc = {}
    for s, L in zip(seeds, labels):
        byc.setdefault(L, []).append(s)
    def exc(cls):
        ss = byc.get(cls, [])
        if not ss:
            return 0.0
        miss = {t: np.mean([1 - cmF1[s][t] for s in ss]) for t in TIERS}
        return float(np.mean([allmiss[s] for s in ss]) - np.prod([miss[t] for t in TIERS]))
    return exc("OMIT") - exc("NUM")
obs_d = delta_for_labels(lab)
rng = np.random.RandomState(277)
perm = [delta_for_labels(list(rng.permutation(lab))) for _ in range(20000)]
perm = np.array(perm)
p = float(np.mean(perm >= obs_d - 1e-12))
print(f"honest item-permutation p (class-label shuffle, preserves joint miss vectors) = {round(p,3)} (obs Delta={round(obs_d,3)})")

# corrected marginal OMIT catch (true) across all tiers/frames
print("\n=== CORRECTED marginal catch by class (opus, F1) ===")
for cls in CLASSES:
    sub = [r for r in rows if r["class"] == cls and r["tier"] == "opus" and r["frame"] == "F1" and r["catch"] is not None]
    print(f"  {cls}: {sum(x['catch'] for x in sub)}/{len(sub)}")
print("\n=== README witness (unchanged, verified 7/7 miss) ===")
wit = [r for r in rows if r["seed"] == "R274_F5-protocol__s0"]
print("  ", {(r["tier"], r["frame"]): r["catch"] for r in wit})
json.dump({"corrected_excess": ex, "corrected_delta": d, "honest_perm_p": round(p, 3)},
          open(os.path.join(D, "analysis_corrected.json"), "w"), indent=1)
