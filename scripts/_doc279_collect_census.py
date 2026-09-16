import glob, json, sys
WF = sys.argv[1]
concl, triage = [], None
for p in glob.glob(WF + "/agent-*.jsonl"):
    last = None
    for line in open(p, encoding="utf-8", errors="replace"):
        try:
            ev = json.loads(line)
        except Exception:
            continue
        for blk in ((ev.get("message") or {}).get("content") or []):
            if isinstance(blk, dict) and blk.get("type") == "tool_use" and blk.get("name") == "StructuredOutput":
                last = blk.get("input")
    if not last:
        continue
    if "conclusions" in last:
        concl.extend(last["conclusions"])
        print(p.split("agent-")[-1][:10], "->", last.get("doc_range"), len(last["conclusions"]), "conclusions")
    elif "load_bearing_nulls_ranked" in last:
        triage = last
        print(p.split("agent-")[-1][:10], "-> TRIAGE", last.get("n_total"), "total")
json.dump({"conclusions": concl, "triage": triage}, open("data/research/doc279/census.json", "w"), indent=1)
from collections import Counter
print("\nTOTAL conclusions:", len(concl))
print("corpus_dependent:", sum(1 for c in concl if c.get("corpus_dependent")))
print("load_bearing:", sum(1 for c in concl if c.get("load_bearing")))
print("flip_likelihood:", dict(Counter(c.get("flip_likelihood") for c in concl)))
print("triage present:", triage is not None)
