"""doc276 scoring: fold dual adjudications into consensus verdicts, then compute the
instrument gates (golden accuracy, clean-arm specificity) + dump the VERIFIED-DEFECT set
for seed-matching. x4 + plant-recall are finalized after the seed-match pass.
Usage: python scripts/_doc276_score.py <adjudications.json>"""
import json
import os
import sys
from collections import Counter, defaultdict

D = "data/research/doc276"
adjs = json.load(open(sys.argv[1], encoding="utf-8"))
key = json.load(open(os.path.join(D, "adj_key_SEALED.json"), encoding="utf-8"))

# group replicas per adj_id
byid = defaultdict(list)
for a in adjs:
    byid[a["adj_id"]].append(a)

consensus = {}
for aid, reps in byid.items():
    verds = [r["verdict"] for r in reps]
    if len(set(verds)) == 1:
        v = verds[0]
        agree = "unanimous" if len(reps) > 1 else "single"
    else:
        v = "UNRESOLVED"
        agree = "split:" + "/".join(verds)
    # severity consensus among VERIFIED-DEFECT replicas
    sevs = [r["severity_confirmed"] for r in reps if r["verdict"] == "VERIFIED-DEFECT"]
    sev = Counter(sevs).most_common(1)[0][0] if sevs else "none"
    consensus[aid] = {"verdict": v, "agree": agree, "severity": sev,
                      "reimpl": [r.get("reimplemented_value", "")[:200] for r in reps]}

# ---- GOLDEN accuracy ----
golden_rows, gcorrect = [], 0
for aid, k in key.items():
    if k["source"] != "GOLDEN":
        continue
    c = consensus.get(aid, {"verdict": "MISSING"})
    want = "VERIFIED-DEFECT" if k["golden_truth"] == "VERIFY" else "REFUTED"
    ok = c["verdict"] == want
    gcorrect += ok
    golden_rows.append({"golden_id": k["golden_id"], "truth": k["golden_truth"],
                        "want": want, "got": c["verdict"], "correct": ok})

# ---- CLEAN-ARM specificity: verified-defects on clean-control units ----
clean_hits = []
for aid, k in key.items():
    if k["source"] == "GOLDEN" or not k.get("is_clean_control"):
        continue
    c = consensus.get(aid, {})
    if c.get("verdict") == "VERIFIED-DEFECT":
        clean_hits.append({"adj_id": aid, "unit": k["unit"], "severity": c["severity"],
                           "locus": k["locus"], "claim": k["claim_quote"][:160]})

# ---- VERIFIED-DEFECT set (for seed-matching + x4) ----
vdefs = []
for aid, k in key.items():
    if k["source"] == "GOLDEN":
        continue
    c = consensus.get(aid, {})
    if c.get("verdict") == "VERIFIED-DEFECT":
        vdefs.append({"adj_id": aid, "unit": k["unit"], "is_plant_unit": k["is_plant_unit"],
                      "is_clean_control": k.get("is_clean_control", False),
                      "severity": c["severity"], "orig_severity": k["orig_severity"],
                      "orig_class": k["orig_class"], "locus": k["locus"],
                      "claim": k["claim_quote"], "reimpl": c["reimpl"]})
json.dump(vdefs, open(os.path.join(D, "verified_defects.json"), "w"), indent=1)

# ---- summary ----
allv = Counter(c["verdict"] for c in consensus.values())
print("=== CONSENSUS VERDICTS ===", dict(allv))
print(f"\n=== GOLDEN: {gcorrect}/6 correct (gate: >=5/6) ===")
for r in sorted(golden_rows, key=lambda x: x["golden_id"]):
    print(f"  {r['golden_id']} truth={r['truth']:6} want={r['want']:16} got={r['got']:16} {'OK' if r['correct'] else 'XX'}")
print(f"\n=== CLEAN ARM: {len(clean_hits)} verified-defect(s) on clean-control units (Step-0 trigger unless manual-review reclassifies) ===")
for h in clean_hits:
    print(f"  {h['unit']} [{h['severity']}] {h['claim'][:100]}")
print(f"\n=== VERIFIED DEFECTS: {len(vdefs)} total "
      f"({sum(1 for v in vdefs if v['is_plant_unit'])} on plant units, "
      f"{sum(1 for v in vdefs if not v['is_plant_unit'] and not v['is_clean_control'])} on unperturbed real, "
      f"{sum(1 for v in vdefs if v['is_clean_control'])} on clean-control) -> verified_defects.json ===")
sevd = Counter(v["severity"] for v in vdefs)
print("   severity(confirmed):", dict(sevd))
json.dump({"consensus": consensus, "golden": golden_rows, "golden_correct": gcorrect,
           "clean_hits": clean_hits}, open(os.path.join(D, "score_intermediate.json"), "w"), indent=1)
