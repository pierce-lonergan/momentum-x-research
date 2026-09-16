"""doc279 CORE SWEEP: recompute the load-bearing expectancy/selection nulls (docs 249/250) on the CONTAMINATED
(corpus adv20) vs DECONTAMINATED (corrected adv20) universe, per year + pooled, with day-block bootstrap CIs.
Discipline (panel-anticipated): (1) reproduce the ORIGINAL contaminated number first; (2) affected-fraction defined
ORTHOGONALLY (share of a conclusion's ticker-days in the 2,716 gate-flip set), not from the flip statistic;
(3) flip class ROBUST/WOUNDED/FLIPPED/INDETERMINATE by whether the VERDICT (not just the magnitude) moves.
Read-only; deterministic RNG."""
import numpy as np
import pandas as pd

D = "data/research/doc279"
df = pd.read_parquet(f"{D}/corpus_decontam.parquet")
df["yr"] = df.session_date.astype(str).str[:4]
rng = np.random.RandomState(279)

RETCOL = "ret_session"  # the EOD realized return the expectancy null rests on
GATE = 1e6


def block_boot_ci(sub, col, B=2000):
    """day-block bootstrap of the mean (resample session_dates)."""
    if len(sub) < 5:
        return (np.nan, np.nan)
    days = sub.session_date.unique()
    byday = {d: sub[sub.session_date == d][col].values for d in days}
    means = []
    for _ in range(B):
        samp = rng.choice(days, size=len(days), replace=True)
        vals = np.concatenate([byday[d] for d in samp])
        means.append(np.nanmean(vals))
    return (round(float(np.percentile(means, 2.5)) * 100, 3), round(float(np.percentile(means, 97.5)) * 100, 3))


def stat(universe_mask, scope_mask, label):
    sub = df[universe_mask & scope_mask]
    if len(sub) == 0:
        return None
    m = float(np.nanmean(sub[RETCOL])) * 100
    ci = block_boot_ci(sub, RETCOL)
    return {"label": label, "n": len(sub), "n_days": sub.session_date.nunique(),
            "mean_pct": round(m, 3), "ci95": ci}


gate_c = df.adv20 >= GATE
gate_d = df.adv20_decontam >= GATE

print("=== CORE SWEEP: expectancy/selection null (mean %s of the gated universe) ===" % RETCOL)
scopes = {"2024": df.yr == "2024", "2025": df.yr == "2025", "2026": df.yr == "2026",
          "pooled_2024_2025": df.yr.isin(["2024", "2025"]), "all": df.yr.notna()}
rows = []
for sc, mask in scopes.items():
    c = stat(gate_c, mask, sc)
    d = stat(gate_d, mask, sc)
    # ORTHOGONAL affected fraction: share of the CONTAMINATED-universe ticker-days in the 2,716 gate-flip set
    uni = df[gate_c & mask]
    flip_set = (df.adv20 >= GATE) & (df.adv20_decontam < GATE)  # the 2,716 (orthogonal to the return statistic)
    aff = int((flip_set & mask).sum())
    aff_frac = round(100 * aff / max(len(uni), 1), 3)
    # flip class: does the verdict (negative-after-costs / ~zero-expectancy => mean <= 0-ish, CI includes/near 0) move?
    verdict_move = "IDENTICAL" if (c and d and c["mean_pct"] == d["mean_pct"]) else "changed"
    rows.append({"scope": sc, "contaminated": c, "decontam": d, "affected_frac_pct": aff_frac, "n_affected": aff, "verdict_move": verdict_move})
    cm = c["mean_pct"] if c else None
    dm = d["mean_pct"] if d else None
    cc = c["ci95"] if c else None
    dc = d["ci95"] if d else None
    print(f"\n  [{sc}] n_contam={c['n'] if c else 0} affected={aff} ({aff_frac}% of universe)")
    print(f"     contaminated mean={cm}% CI{cc}")
    print(f"     decontam     mean={dm}% CI{dc}  ({verdict_move})")

import json
json.dump({"retcol": RETCOL, "gate": GATE, "rows": [
    {"scope": r["scope"], "affected_frac_pct": r["affected_frac_pct"], "n_affected": r["n_affected"],
     "contam_mean": r["contaminated"]["mean_pct"] if r["contaminated"] else None,
     "contam_ci": r["contaminated"]["ci95"] if r["contaminated"] else None,
     "decontam_mean": r["decontam"]["mean_pct"] if r["decontam"] else None,
     "decontam_ci": r["decontam"]["ci95"] if r["decontam"] else None,
     "verdict_move": r["verdict_move"]} for r in rows]},
    open(f"{D}/sweep_expectancy.json", "w"), indent=1)
print(f"\nwrote {D}/sweep_expectancy.json")
