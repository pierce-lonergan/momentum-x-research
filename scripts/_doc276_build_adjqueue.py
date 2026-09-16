"""doc276: build the blind adjudication queue = all L4 findings + 6 golden cases, shuffled,
stripped of provenance. Adjudicators see only {claim, locus, check}; the key stays sealed.
Usage: python scripts/_doc276_build_adjqueue.py <l4_findings.json>"""
import glob
import json
import os
import random
import sys

D = "data/research/doc276"
QDIR = os.path.join(D, "adj_queue")
L4 = sys.argv[1] if len(sys.argv) > 1 else os.path.join(D, "l4_findings_opus.json")

# plant hosts (for key tagging only — NOT shown to adjudicators)
PLANT_HOSTS = {"R273_F1-stats.md", "R273_F2-econ.md", "R273_F3-leak-a.md", "R273_F4-mech.md",
               "R273_F6-verdict.md", "R274_F2-d9-grading.md", "R274_F3-frontier-claim.md",
               "R274_F5-protocol.md", "R275_F4-drepro.md"}
CLEAN_CONTROLS = {"R273_F3-leak-b.md", "R273_F5-power.md", "R274_F1-matrix.md"}

audits = json.load(open(L4, encoding="utf-8"))
golden = json.load(open(os.path.join(D, "GOLDEN_CASES_SEALED.json"), encoding="utf-8"))["cases"]
GOLDEN_DISGUISE = {  # each golden case gets a plausible unit/lens cover
    "G1": ("R274_F1-matrix.md", "STRUCTURE"), "G2": ("R275_F4-drepro.md", "RECOMPUTE"),
    "G3": ("R273_F1-stats.md", "RECOMPUTE"), "G4": ("R275_F1-calibration.md", "RECOMPUTE"),
    "G5": ("R275_F1-calibration.md", "STRUCTURE"), "G6": ("R273_F5-power.md", "STRUCTURE"),
}

items, key = [], {}
for a in audits:
    unit, lens = a["unit"], a.get("lens", "?")
    for j, f in enumerate(a.get("findings", [])):
        items.append({"source": "L4", "unit": unit, "lens": lens,
                      "claim_quote": f["claim_quote"], "locus": f["locus"],
                      "defect_class": f["defect_class"], "severity": f["severity"],
                      "decidability": f.get("decidability", "?"),
                      "runnable_check": f["runnable_check"], "rationale": f.get("rationale", "")})
for g in golden:
    u, lens = GOLDEN_DISGUISE[g["id"]]
    items.append({"source": "GOLDEN", "golden_id": g["id"], "golden_truth": g["truth"],
                  "unit": u, "lens": lens, "claim_quote": g["finding"], "locus": "(finding under audit)",
                  "defect_class": "OTHER", "severity": "S2", "decidability": "MD-EXACT",
                  "runnable_check": g["recipe"], "rationale": ""})

rng = random.Random(276)
rng.shuffle(items)

os.makedirs(QDIR, exist_ok=True)
for f in glob.glob(os.path.join(QDIR, "*.json")):
    os.remove(f)
for i, it in enumerate(items):
    aid = f"adj_{i:03d}"
    # blind item shown to adjudicators (no source/golden/rationale-conclusion leak of provenance)
    blind = {"adj_id": aid, "unit": it["unit"], "lens": it["lens"], "claim_quote": it["claim_quote"],
             "locus": it["locus"], "defect_class": it["defect_class"], "severity": it["severity"],
             "decidability": it["decidability"], "runnable_check": it["runnable_check"]}
    json.dump(blind, open(os.path.join(QDIR, aid + ".json"), "w"), indent=1)
    host = it["unit"]
    key[aid] = {"source": it["source"], "unit": host,
                "is_plant_unit": host in PLANT_HOSTS, "is_clean_control": host in CLEAN_CONTROLS,
                "golden_id": it.get("golden_id"), "golden_truth": it.get("golden_truth"),
                "orig_severity": it["severity"], "orig_class": it["defect_class"],
                "claim_quote": it["claim_quote"], "locus": it["locus"]}
json.dump(key, open(os.path.join(D, "adj_key_SEALED.json"), "w"), indent=1)

# args manifest for the adjudication workflow: id + unit + severity ONLY (no provenance).
# dual-adjudicate S1/S2 + plant-unit + (golden, which are S2); single-adjudicate S3-on-real (prereg rule 5).
PLANT = PLANT_HOSTS
manifest = []
for i, it in enumerate(items):
    aid = f"adj_{i:03d}"
    dual = it["severity"] in ("S1", "S2") or it["unit"] in PLANT
    manifest.append({"id": aid, "unit": it["unit"], "severity": it["severity"], "dual": dual})
json.dump(manifest, open(os.path.join(D, "adj_manifest.json"), "w"), indent=1)
nd = sum(1 for m in manifest if m["dual"])
print(f"queue: {len(items)} items ({sum(1 for i in items if i['source']=='L4')} L4 + "
      f"{sum(1 for i in items if i['source']=='GOLDEN')} golden) -> {QDIR}")
print(f"adjudication plan: {nd} dual + {len(manifest)-nd} single = {2*nd + (len(manifest)-nd)} adjudicator agents")
