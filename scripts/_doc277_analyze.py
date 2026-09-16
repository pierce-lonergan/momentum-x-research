"""doc277 frozen analysis (Stage 1). Operates on catch_matrix.json.
- Marginal catch rates by (tier, class, frame).
- Joint-miss estimand: observed P(all 3 tiers miss | class) vs independence baseline = product of per-class
  per-tier marginal miss rates (conditions on class as a coarse difficulty proxy). excess(c)=obs-exp.
- PRIMARY: Delta = excess-co-miss(OMIT) - excess-co-miss(NUM). Item-bootstrap CI + item-permutation p (shuffle
  each tier's catch across seeds independently -> breaks cross-tier dependence, preserves per-tier marginal).
- Same-tier lineage floor: opus vs opus_rep (F1) co-miss = intra-vendor baseline.
- PROMPT-DIVERSITY: OMIT all-miss under F1 vs F2 (absence-priming). Does F2 rescue OMIT -> R-PROMPT-BOUND?
- n_eff (descriptive) from the 3-tier catch covariance.
Deterministic RNG (seed 277). No Date/random-from-env."""
import json
import os
import numpy as np

D = "data/research/doc277"
rows = json.load(open(os.path.join(D, "catch_matrix.json"), encoding="utf-8"))
TIERS = ["opus", "sonnet", "haiku"]
CLASSES = ["NUM", "CODE", "OMIT", "MECH"]
rng = np.random.RandomState(277)


def catch_map(frame):
    """seed -> {tier: catch(0/1/None)} for the 3 cross-tier raters at a frame."""
    m = {}
    for r in rows:
        if r["frame"] != frame or r["tier"] not in TIERS:
            continue
        m.setdefault(r["seed"], {})[r["tier"]] = r["catch"]
    return m


def seed_class():
    return {r["seed"]: r["class"] for r in rows}


SC = seed_class()


def excess_by_class(cm, cls_filter=None):
    """returns per-class dict of {obs_allmiss, exp_allmiss, excess, n}. cm: seed->{tier:catch}."""
    out = {}
    for cls in CLASSES:
        seeds = [s for s, d in cm.items() if SC[s] == cls
                 and all(d.get(t) is not None for t in TIERS)]
        if not seeds:
            continue
        # per-tier marginal miss rate within class
        miss = {t: np.mean([1 - cm[s][t] for s in seeds]) for t in TIERS}
        exp_all = float(np.prod([miss[t] for t in TIERS]))
        obs_all = float(np.mean([1 if all(cm[s][t] == 0 for t in TIERS) else 0 for s in seeds]))
        out[cls] = {"n": len(seeds), "obs_allmiss": round(obs_all, 3), "exp_allmiss": round(exp_all, 3),
                    "excess": round(obs_all - exp_all, 3),
                    "marg_miss": {t: round(miss[t], 3) for t in TIERS}}
    return out


def delta_and_perm(cm, B=5000):
    """Delta = excess(OMIT) - excess(NUM); item-permutation p (shuffle each tier's catch across seeds)."""
    seeds_all = [s for s in cm if all(cm[s].get(t) is not None for t in TIERS)]
    def _delta(cmx):
        e = excess_by_class(cmx)
        if "OMIT" not in e or "NUM" not in e:
            return None
        return e["OMIT"]["excess"] - e["NUM"]["excess"]
    obs = _delta(cm)
    # bootstrap CI over items within class
    boots = []
    byc = {cls: [s for s in seeds_all if SC[s] == cls] for cls in CLASSES}
    for _ in range(B):
        cmx = {}
        for cls in CLASSES:
            pool = byc[cls]
            if not pool:
                continue
            samp = rng.choice(pool, size=len(pool), replace=True)
            for k, s in enumerate(samp):
                cmx[f"{cls}_{k}"] = cm[s]
                SC[f"{cls}_{k}"] = cls
        d = _delta(cmx)
        if d is not None:
            boots.append(d)
    boots = np.array(boots)
    # permutation null: shuffle each tier's catch vector across ALL seeds independently
    perm = []
    base = {t: np.array([cm[s][t] for s in seeds_all]) for t in TIERS}
    for _ in range(B):
        cmx = {}
        shuf = {t: rng.permutation(base[t]) for t in TIERS}
        for i, s in enumerate(seeds_all):
            cmx[s] = {t: int(shuf[t][i]) for t in TIERS}
        d = _delta(cmx)
        if d is not None:
            perm.append(d)
    perm = np.array(perm)
    p = float(np.mean(perm >= obs)) if len(perm) else None
    ci = [round(float(np.percentile(boots, 2.5)), 3), round(float(np.percentile(boots, 97.5)), 3)] if len(boots) else None
    return {"delta_obs": round(obs, 3) if obs is not None else None, "boot_CI95": ci,
            "perm_p_ge_obs": p, "boot_n": len(boots), "perm_n": len(perm)}


def n_eff(cm):
    """descriptive: effective independent judges from 3-tier catch covariance (Kish)."""
    seeds = [s for s in cm if all(cm[s].get(t) is not None for t in TIERS)]
    if len(seeds) < 3:
        return None
    X = np.array([[cm[s][t] for t in TIERS] for s in seeds], float)
    C = np.cov(X.T)
    w = np.linalg.eigvalsh(C)
    w = w[w > 1e-9]
    if len(w) == 0:
        return None
    return round(float((w.sum() ** 2) / (w ** 2).sum()), 2)


print("=== MARGINAL CATCH RATE by tier x class (F1) ===")
cmF1 = catch_map("F1")
for tier in TIERS + ["opus_rep"]:
    line = [tier]
    for cls in CLASSES:
        sub = [r for r in rows if r["class"] == cls and r["tier"] == tier and r["frame"] == "F1" and r["catch"] is not None]
        line.append(f"{cls} {sum(x['catch'] for x in sub)}/{len(sub)}" if sub else f"{cls} -")
    print("  " + "  ".join(line))

print("\n=== JOINT-MISS (cross-tier, F1): obs vs independence-expected all-miss, excess by class ===")
ex = excess_by_class(cmF1)
for cls in CLASSES:
    if cls in ex:
        e = ex[cls]
        print(f"  {cls}: n={e['n']} obs_allmiss={e['obs_allmiss']} exp={e['exp_allmiss']} EXCESS={e['excess']} marg_miss={e['marg_miss']}")

print("\n=== PRIMARY: Delta = excess(OMIT) - excess(NUM), F1 ===")
dd = delta_and_perm(cmF1)
print(" ", dd)

print("\n=== PROMPT-DIVERSITY: OMIT all-miss F1 vs F2 (does absence-priming rescue OMIT?) ===")
cmF2 = catch_map("F2")
for frame, cm in [("F1", cmF1), ("F2", cmF2)]:
    for cls in ["OMIT", "NUM"]:
        seeds = [s for s, d in cm.items() if SC.get(s) == cls and all(d.get(t) is not None for t in TIERS)]
        if seeds:
            am = np.mean([1 if all(cm[s][t] == 0 for t in TIERS) else 0 for s in seeds])
            cr = np.mean([np.mean([cm[s][t] for t in TIERS]) for s in seeds])
            print(f"  {frame} {cls}: all-miss={am:.2f} mean-catch={cr:.2f} (n={len(seeds)})")

print("\n=== SAME-TIER LINEAGE FLOOR: opus vs opus_rep co-catch/co-miss (F1) ===")
pairs = {}
for r in rows:
    if r["frame"] == "F1" and r["tier"] in ("opus", "opus_rep") and r["catch"] is not None:
        pairs.setdefault(r["seed"], {})[r["tier"]] = r["catch"]
both = [s for s, d in pairs.items() if "opus" in d and "opus_rep" in d]
if both:
    agree = np.mean([1 if pairs[s]["opus"] == pairs[s]["opus_rep"] else 0 for s in both])
    comiss = np.mean([1 if pairs[s]["opus"] == 0 and pairs[s]["opus_rep"] == 0 else 0 for s in both])
    print(f"  same-tier agreement={agree:.2f} co-miss={comiss:.2f} (n={len(both)}) — the intra-vendor baseline")

print("\n=== n_eff (descriptive, 3-tier, F1) ===")
print("  overall:", n_eff(cmF1))
json.dump({"marginal": ex, "delta": dd}, open(os.path.join(D, "analysis_stage1.json"), "w"), indent=1)
