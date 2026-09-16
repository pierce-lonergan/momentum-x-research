"""doc276: plant-recall via seed-matching. For each of the 15 planted seeds, decide whether the
L4 fleet CAUGHT it (a finding on the host targeting the seed site) AND whether that catch survived
adjudication as VERIFIED-DEFECT. Reports recall with and without the F5 cross-brief tell (seed 11).
Usage: python scripts/_doc276_seedmatch.py <l4_findings_opus.json> <adjudications.json>"""
import json
import os
import re
import sys
from collections import defaultdict

D = "data/research/doc276"
L4 = json.load(open(sys.argv[1], encoding="utf-8"))
adjs = json.load(open(sys.argv[2], encoding="utf-8")) if len(sys.argv) > 2 else []
man = json.load(open(os.path.join(D, "MANIFEST_SEALED.json"), encoding="utf-8"))
key = json.load(open(os.path.join(D, "adj_key_SEALED.json"), encoding="utf-8"))

# consensus verdict per adj_id
byid = defaultdict(list)
for a in adjs:
    byid[a["adj_id"]].append(a["verdict"])
consensus = {}
for aid, vs in byid.items():
    consensus[aid] = vs[0] if len(set(vs)) == 1 else "UNRESOLVED"

# map (unit, claim_quote) -> adj_id (via key)
claim2adj = {}
for aid, k in key.items():
    if k["source"] == "L4":
        claim2adj[(k["unit"], k["claim_quote"][:80])] = aid

# per-host findings
byhost = defaultdict(list)
for a in L4:
    for f in a.get("findings", []):
        byhost[a["unit"]].append(f)

# OMIT keyword hints
OMIT_HINTS = {
    "R274_F2-d9-grading.md": ["bridge", "spine", "GLXG", "substrate", "warehouse"],
    "R273_F3-leak-a.md": ["collider", "0.948", "AUC", "liquidity", "price-path", "selection"],
    "R274_F5-protocol.md": ["README", "template", "run-label", "per-condition", "steered"],
}

rows = []
for m in man:
    host = m["host"]
    for sd in m["seeds"]:
        planted = sd.get("mutated", "")
        orig = sd["original"]
        # distinctive tokens to match
        toks = set()
        for s in (planted, orig):
            toks |= set(re.findall(r"[+-]?\d[\d,]*\.\d+(?:e-?\d+)?|\b\d{3,}\b|[0-9.]+%|side='\w+'|session_open_px|last_prefix|factor_resid|1e-9|1e-6", s))
        toks = {t for t in toks if len(t) >= 3}
        hints = OMIT_HINTS.get(host, []) if sd["seed_class"] == "OMIT" else []

        matched_finding, verdict = None, None
        for f in byhost.get(host, []):
            blob = (f["claim_quote"] + " " + f.get("runnable_check", "") + " " + f.get("rationale", "")).lower()
            hit = any(t.lower() in blob for t in toks) or any(h.lower() in blob for h in hints)
            # OMIT: also require the finding to be an omission/missing-check flavor
            if sd["seed_class"] == "OMIT":
                hit = hit and (f["defect_class"] == "OMIT" or "omit" in blob or "never" in blob or "missing" in blob or "does not" in blob or "fails to" in blob)
            if hit:
                aid = claim2adj.get((host, f["claim_quote"][:80]))
                v = consensus.get(aid, "NOT-ADJUDICATED") if aid else "NOT-ADJUDICATED"
                # prefer a verified match
                if matched_finding is None or v == "VERIFIED-DEFECT":
                    matched_finding, verdict = f, v
                if v == "VERIFIED-DEFECT":
                    break
        rows.append({"host": host, "class": sd["seed_class"], "sev": sd["severity"],
                     "caught": matched_finding is not None, "verdict": verdict,
                     "tell": host == "R274_F5-protocol.md" and "15835" in planted})

caught = [r for r in rows if r["caught"]]
verified = [r for r in rows if r["verdict"] == "VERIFIED-DEFECT"]
print(f"=== PLANT RECALL (15 seeds) ===")
print(f"caught (fleet flagged the seed site): {len(caught)}/15")
print(f"caught AND adjudicated VERIFIED-DEFECT: {len(verified)}/15")
tell = [r for r in verified if r["tell"]]
print(f"  of which tell-confounded (F5 15385 cross-brief): {len(tell)}")
print(f"  RECALL excluding tell-confounded seed: {len(verified)-len(tell)}/{15-len(tell)}")
print("\nper-seed:")
for r in rows:
    flag = " [TELL]" if r["tell"] else ""
    print(f"  {r['host']:28} {r['class']}/{r['sev']}  caught={r['caught']}  adj={r['verdict']}{flag}")
json.dump(rows, open(os.path.join(D, "seedmatch.json"), "w"), indent=1)
