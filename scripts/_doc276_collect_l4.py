"""doc276: collect L4 fleet StructuredOutput payloads -> l4_findings.json + summary table."""
import glob
import json
import sys
from collections import Counter

WF_DIR = sys.argv[1]
OUTP = sys.argv[2] if len(sys.argv) > 2 else "data/research/doc276/l4_findings.json"
audits = []
for p in glob.glob(WF_DIR + "/agent-*.jsonl"):
    last = None
    for line in open(p, encoding="utf-8", errors="replace"):
        try:
            ev = json.loads(line)
        except Exception:
            continue
        msg = ev.get("message") or {}
        for blk in (msg.get("content") or []):
            if isinstance(blk, dict) and blk.get("type") == "tool_use" and blk.get("name") == "StructuredOutput":
                last = blk.get("input")
    if last and "unit" in last:
        audits.append(last)
    else:
        print("NO OUTPUT:", p.split("agent-")[-1][:12])

json.dump(audits, open(OUTP, "w"), indent=1)
print(f"collected {len(audits)}/36 audits -> {OUTP}")
n_find = sum(len(a.get("findings", [])) for a in audits)
sev = Counter(f["severity"] for a in audits for f in a.get("findings", []))
dec = Counter(f["decidability"] for a in audits for f in a.get("findings", []))
cls = Counter(f["defect_class"] for a in audits for f in a.get("findings", []))
ex = Counter(f["executed_locally"] for a in audits for f in a.get("findings", []))
print(f"findings: {n_find} | sev {dict(sev)} | decid {dict(dec)} | class {dict(cls)} | executed {dict(ex)}")
print("\nper-unit finding counts (lens R / S):")
units = {}
for a in audits:
    u = a["unit"]
    units.setdefault(u, {}).update({a["lens"]: len(a.get("findings", []))})
for u in sorted(units):
    r = units[u].get("RECOMPUTE", "-")
    s = units[u].get("STRUCTURE", "-")
    print(f"  {u:32} R={r} S={s}")
