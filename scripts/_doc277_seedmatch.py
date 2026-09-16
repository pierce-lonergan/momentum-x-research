"""doc277: build the CATCH MATRIX. For each seed x (model_tier, frame): did that panel run catch the seed?
Match = a finding on the seed's host brief that quotes the planted/original token or the OMIT-omission subject.
Reuses the doc-276 seed manifests for loci. Outputs catch_matrix.json (long form: one row per seed x rater x frame)."""
import json
import os
import re
from collections import defaultdict

D276 = "data/research/doc276"
D277 = "data/research/doc277"
panel = json.load(open(os.path.join(D277, "panel_results.json"), encoding="utf-8"))
seals = json.load(open(os.path.join(D277, "DIFFICULTY_SEALED.json"), encoding="utf-8"))["items"]
man = json.load(open(os.path.join(D276, "MANIFEST_SEALED.json"), encoding="utf-8"))
wound = json.load(open(os.path.join(D276, "WOUND_MANIFEST_SEALED.json"), encoding="utf-8"))

# seed_id -> {host_brief_name, class, tokens, omit_hints}
OMIT_HINTS = {
    "R274_F2-d9-grading": ["bridge", "spine", "GLXG", "substrate", "warehouse", "contaminat"],
    "R273_F3-leak-a": ["collider", "0.948", "AUC", "liquidity", "price-path", "selection"],
    "R274_F5-protocol": ["README", "template", "run-label", "per-condition", "steered", "byte-identical"],
}
seedinfo = {}
def brief_name(host):
    return host.replace(".md", "")
for m in man:
    host = m["host"]
    for i, s in enumerate(m["seeds"]):
        sid = f"{brief_name(host)}__s{i}"
        toks = set(re.findall(r"[+-]?\d[\d,]*\.\d+(?:e-?\d+)?|\b\d{3,}\b|[0-9.]+%|side='\w+'|session_open_px|last_prefix|factor_resid|1e-9|1e-6",
                              (s.get("original", "") + " " + s.get("mutated", ""))))
        toks = {t for t in toks if len(t) >= 3}
        seedinfo[sid] = {"brief": brief_name(host), "class": s["seed_class"],
                         "tokens": toks, "omit": OMIT_HINTS.get(brief_name(host), []) if s["seed_class"] == "OMIT" else []}
for m in wound:
    host = m["host"]
    sid = f"WOUND__{brief_name(host)}"
    toks = set(re.findall(r"[+-]?\d[\d,]*\.\d+(?:e-?\d+)?|\b\d{3,}\b", (m.get("original", "") + " " + m.get("mutated", ""))))
    seedinfo[sid] = {"brief": brief_name(host), "class": m["seed_class"], "tokens": {t for t in toks if len(t) >= 3}, "omit": []}

# keep only the 16 sealed seeds
sealed_ids = {i["item_id"] for i in seals}
sealed_cls = {i["item_id"]: i["seed_class"] for i in seals}
sealed_diff = {i["item_id"]: i["difficulty"] for i in seals}

# index panel runs by (brief, tier, frame) -> findings
runidx = {}
for r in panel:
    runidx[(r["brief"], r["model_tier"], r["frame"])] = r

def caught(sid, run):
    if run is None or run.get("refused"):
        return None  # refusal = excluded from 2x2
    info = seedinfo[sid]
    for f in run.get("findings", []):
        blob = (f.get("claim_quote", "") + " " + f.get("locus", "") + " " + f.get("rationale", "")).lower()
        if any(t.lower() in blob for t in info["tokens"]):
            return 1
        if info["omit"] and (f.get("defect_class") == "OMIT" or "omit" in blob or "never" in blob or "missing" in blob or "does not" in blob or "fails to" in blob or "absent" in blob):
            if any(h.lower() in blob for h in info["omit"]):
                return 1
    return 0

RATERS = ["opus", "sonnet", "haiku", "opus_rep"]
FRAMES = {"opus": ["F1", "F2"], "sonnet": ["F1", "F2"], "haiku": ["F1", "F2"], "opus_rep": ["F1"]}
rows = []
for sid in sealed_ids:
    info = seedinfo[sid]
    for tier in RATERS:
        for frame in FRAMES[tier]:
            run = runidx.get((info["brief"], tier, frame))
            c = caught(sid, run)
            rows.append({"seed": sid, "class": sealed_cls[sid], "difficulty": sealed_diff[sid],
                         "tier": tier, "frame": frame, "catch": c})
json.dump(rows, open(os.path.join(D277, "catch_matrix.json"), "w"), indent=1)

# quick summary
print(f"catch matrix: {len(rows)} rows ({len(sealed_ids)} seeds x raters x frames)")
from collections import Counter
refus = sum(1 for r in rows if r["catch"] is None)
print("refusals (null catch):", refus)
# marginal catch rate by tier (F1) and class
for tier in RATERS:
    fr = "F1"
    sub = [r for r in rows if r["tier"] == tier and r["frame"] == fr and r["catch"] is not None]
    if sub:
        cr = sum(r["catch"] for r in sub) / len(sub)
        print(f"  {tier} {fr}: catch rate {cr:.2f} (n={len(sub)})")
print("\ncatch rate by class (tier=opus, F1):")
for cls in ["NUM", "CODE", "OMIT", "MECH"]:
    sub = [r for r in rows if r["class"] == cls and r["tier"] == "opus" and r["frame"] == "F1" and r["catch"] is not None]
    if sub:
        print(f"  {cls}: {sum(r['catch'] for r in sub)}/{len(sub)}")
