"""doc276 final gate resolution: x4, wound (P4), leg map, decision-tree inputs."""
import json
import os
from collections import defaultdict

D = "data/research/doc276"
vdefs = json.load(open(os.path.join(D, "verified_defects.json"), encoding="utf-8"))

PRESSURE = {"R274_F6-meaning.md", "R275_F1-calibration.md",
            "R275_F2-residue-ordering.md", "R275_F3-ladders.md"}
PLANT = {"R273_F1-stats.md", "R273_F2-econ.md", "R273_F3-leak-a.md", "R273_F4-mech.md",
         "R273_F6-verdict.md", "R274_F2-d9-grading.md", "R274_F3-frontier-claim.md",
         "R274_F5-protocol.md", "R275_F4-drepro.md"}
CLEAN = {"R273_F3-leak-b.md", "R273_F5-power.md", "R274_F1-matrix.md"}

# seed loci (planted claim quotes) to EXCLUDE plant-unit findings that are just seed catches
seed_quotes = set()
for m in json.load(open(os.path.join(D, "MANIFEST_SEALED.json"), encoding="utf-8")):
    for s in m["seeds"]:
        seed_quotes.add((m["host"], s.get("mutated", "")[:40]))

print("=== VERIFIED DEFECTS by unit-class & severity ===")
buckets = defaultdict(lambda: defaultdict(int))
for v in vdefs:
    u = v["unit"]
    cls = "plant" if u in PLANT else ("clean" if u in CLEAN else "real")
    buckets[cls][v["severity"]] += 1
for cls in ("real", "plant", "clean"):
    print(f"  {cls}: {dict(buckets[cls])}")

# WOUND check (P4): verified S1/S2 on PRESSURE legs (real, unperturbed)
print("\n=== WOUND (P4) — verified S1/S2 on the leg-map pressure set ===")
wounds = [v for v in vdefs if v["unit"] in PRESSURE and v["severity"] in ("S1", "S2")]
if not wounds:
    print("  NONE — no verified S1/S2 on any pressure leg. P4 (zero verdicts wounded) HOLDS on the real pressure set.")
for v in wounds:
    print(f"  WOUND CANDIDATE {v['unit']} [{v['severity']}]: {v['claim'][:110]}")

# x4: verified S1/S2 phenomena attributable to REAL rulings.
# = verified S1/S2 on unperturbed-real (incl clean-control genuine latents) + plant-unit S1/S2 at NON-seed sites
print("\n=== x4 (confirmatory: verified S1-S2 phenomena on real rulings) ===")
x4 = []
for v in vdefs:
    if v["severity"] not in ("S1", "S2"):
        continue
    u = v["unit"]
    if u in PLANT:
        # only count if NOT a seed catch (spontaneous on pristine text)
        is_seed = any(u == h and (mut and mut in v["claim"]) for (h, mut) in seed_quotes)
        if is_seed:
            continue
        x4.append((u, v["severity"], "plant-nonseed", v["claim"][:90]))
    else:
        x4.append((u, v["severity"], "real/clean", v["claim"][:90]))
for u, s, kind, c in x4:
    print(f"  [{s}] {u} ({kind}): {c}")
print(f"\n  x4_raw = {len(x4)} verified S1-S2 phenomena on real rulings (pre-dedup)")
print(f"  (S1={sum(1 for x in x4 if x[1]=='S1')}, S2={sum(1 for x in x4 if x[1]=='S2')})")
