"""doc276: collect adjudicator StructuredOutput payloads from the adjudication workflow transcripts."""
import glob
import json
import sys
from collections import Counter

WF_DIR = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else "data/research/doc276/adjudications.json"
adjs = []
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
                inp = blk.get("input") or {}
                if "adj_id" in inp and "verdict" in inp:
                    last = inp
    if last:
        adjs.append(last)
json.dump(adjs, open(OUT, "w"), indent=1)
v = Counter(a["verdict"] for a in adjs)
print(f"collected {len(adjs)} adjudications -> {OUT}")
print("verdicts:", dict(v))
