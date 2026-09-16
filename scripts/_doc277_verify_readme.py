import json
panel = json.load(open("data/research/doc277/panel_results.json", encoding="utf-8"))
# what did each rater say about R274_F5-protocol (the README-omission host)?
for r in panel:
    if r["brief"] == "R274_F5-protocol":
        print(f"\n=== {r['model_tier']} {r['frame']} (refused={r.get('refused')}) ===")
        for f in r.get("findings", []):
            q = (f.get("claim_quote","") + " | " + f.get("locus",""))[:150]
            print(f"  [{f.get('defect_class')}] {q}")
        if not r.get("findings"):
            print("  (no findings) attestation:", r.get("attestation","")[:200])
