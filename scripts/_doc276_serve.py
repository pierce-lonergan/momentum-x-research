"""doc276: assemble the served audit set (9 plants replace their hosts + 9 unperturbed real,
uniform fresh copies = uniform mtimes) + as-audited doc blobs; seal the assignment with hashes."""
import hashlib
import json
import os
import shutil

OUT = "data/research/doc276"
SERVED = os.path.join(OUT, "served")
CORPUS = "data/research/doc276_corpus"

PLANT_HOSTS = ["R273_F1-stats.md", "R273_F2-econ.md", "R273_F3-leak-a.md", "R273_F4-mech.md",
               "R273_F6-verdict.md", "R274_F2-d9-grading.md", "R274_F3-frontier-claim.md",
               "R274_F5-protocol.md", "R275_F4-drepro.md"]
REAL = ["R273_F3-leak-b.md", "R273_F5-power.md", "R274_F1-matrix.md", "R274_F4-latent.md",
        "R274_F6-meaning.md", "R275_F1-calibration.md", "R275_F2-residue-ordering.md",
        "R275_F3-ladders.md", "R275_F5-scope.md"]
CLEAN_CONTROLS = ["R273_F3-leak-b.md", "R273_F5-power.md", "R274_F1-matrix.md"]
PRESSURE_SET = ["R274_F6-meaning.md", "R275_F1-calibration.md", "R275_F2-residue-ordering.md",
                "R275_F3-ladders.md"]

os.makedirs(SERVED, exist_ok=True)
os.makedirs(os.path.join(SERVED, "asaudited"), exist_ok=True)

units = []
for h in PLANT_HOSTS:
    shutil.copyfile(os.path.join(OUT, "plants", h), os.path.join(SERVED, h))
    units.append({"unit": h, "kind": "plant"})
for h in REAL:
    shutil.copyfile(os.path.join(OUT, "briefs", h), os.path.join(SERVED, h))
    kind = "clean-control" if h in CLEAN_CONTROLS else "real"
    units.append({"unit": h, "kind": kind, "pressure": h in PRESSURE_SET})

import glob
for p in glob.glob(os.path.join(CORPUS, "ASAUDITED_*.md")):
    shutil.copyfile(p, os.path.join(SERVED, "asaudited", os.path.basename(p)))

sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
assignment = {
    "_meta": "doc276 SEALED ASSIGNMENT — written before the L4 fleet launch; the fleet never reads this.",
    "units": units,
    "unit_hashes": {u["unit"]: sha(os.path.join(SERVED, u["unit"])) for u in units},
    "manifest_sha256": sha(os.path.join(OUT, "MANIFEST_SEALED.json")),
    "potency_sha256": sha(os.path.join(OUT, "POTENCY_PROOF.json")),
    "critic_key_sha256": sha(os.path.join(OUT, "CRITIC_KEY_SEALED.json")),
    "negative_controls_sha256": sha(os.path.join(OUT, "NEGATIVE_CONTROLS_SEALED.json")),
    "golden_cases_sha256": sha(os.path.join(OUT, "GOLDEN_CASES_SEALED.json")),
}
ap = os.path.join(OUT, "ASSIGNMENT_SEALED.json")
json.dump(assignment, open(ap, "w"), indent=1)
print("served units:", len(units), f"({len(PLANT_HOSTS)} plants, {len(REAL)} real incl. {len(CLEAN_CONTROLS)} clean-controls)")
print("ASSIGNMENT_SEALED sha256 =", sha(ap))
print("  manifest:", assignment["manifest_sha256"][:16], "| potency:", assignment["potency_sha256"][:16],
      "| critic:", assignment["critic_key_sha256"][:16], "| negctl:", assignment["negative_controls_sha256"][:16],
      "| golden:", assignment["golden_cases_sha256"][:16])
