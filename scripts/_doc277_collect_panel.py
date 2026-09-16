"""doc277: collect panel StructuredOutput payloads -> panel_results.json."""
import glob
import json
import sys
from collections import Counter

WF_DIR = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else "data/research/doc277/panel_results.json"
runs = []
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
                if "brief" in inp and "model_tier" in inp:
                    last = inp
    if last:
        runs.append(last)
json.dump(runs, open(OUT, "w"), indent=1)
n_ref = sum(1 for r in runs if r.get("refused"))
by = Counter((r["model_tier"], r["frame"]) for r in runs)
print(f"collected {len(runs)} panel runs ({n_ref} refused) -> {OUT}")
print("by (tier,frame):", dict(by))
