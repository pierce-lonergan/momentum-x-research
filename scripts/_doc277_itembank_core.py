"""doc277: assemble the reusable CORE item bank from the doc-276 potency-proven seeds (15 main + 2 wound).
Each item carries: class, the served plant brief path, the seed loci, potency status, and an EX-ANTE
difficulty covariate computed from ITEM FEATURES ONLY (never from any model's catch — circularity guard).
The panel-approved fabricated EXTENSION is added by a later script after freeze."""
import json
import os
import re

D276 = "data/research/doc276"
D277 = "data/research/doc277"
os.makedirs(D277, exist_ok=True)

man = json.load(open(os.path.join(D276, "MANIFEST_SEALED.json"), encoding="utf-8"))
wound = json.load(open(os.path.join(D276, "WOUND_MANIFEST_SEALED.json"), encoding="utf-8"))


def difficulty(seed, host_text):
    """Ex-ante difficulty in [0,1] from ITEM FEATURES ONLY (pre-registered function).
    Higher = harder to catch. NEVER uses any model's catch outcome."""
    cls = seed["seed_class"]
    orig = seed.get("original", "")
    mut = seed.get("mutated", "")
    # base class difficulty priors (from 276: OMIT + recompute-identity hardest)
    base = {"NUM": 0.30, "CODE": 0.35, "MECH": 0.50, "OMIT": 0.70}[cls]
    d = base
    # NUM: smaller relative error = harder (must recompute to notice)
    if cls == "NUM":
        mo = re.findall(r"[-+]?\d*\.?\d+", orig)
        mm = re.findall(r"[-+]?\d*\.?\d+", mut)
        try:
            a, b = float(mo[0]), float(mm[0])
            rel = abs(a - b) / (abs(a) + 1e-9)
            d += 0.25 * (1 - min(rel, 1.0))  # tiny error -> harder
            # "recompute-identity": planted value rounds to the same display or equals a stored artifact
            if abs(a - b) / (abs(a) + 1e-9) < 0.03:
                d += 0.15
        except (ValueError, IndexError):
            pass
    # OMIT: longer deleted span with a load-bearing conclusion retained = harder (nothing visibly wrong)
    if cls == "OMIT":
        d += 0.10 if len(orig) > 200 else 0.0
    # CODE: file:line-distance proxy — a wrong line-number is easy; a wrong-behavior claim is harder
    if cls == "CODE":
        d += 0.10 if ("side=" in orig or "tol=" in orig) else 0.0
    # severity: S1 (flips central support) items tend to be subtler recompute-identity
    if seed.get("severity") == "S1":
        d += 0.10
    return round(min(d, 0.98), 3)


items = []
for m in man:
    host = m["host"]
    txt = ""
    p = os.path.join(D276, "served", host)
    if os.path.exists(p):
        txt = open(p, encoding="utf-8").read()
    for i, s in enumerate(m["seeds"]):
        items.append({
            "item_id": f"{host.replace('.md','')}__s{i}",
            "source": "doc276-main", "host": host, "served_path": p,
            "seed_class": s["seed_class"], "severity": s["severity"],
            "original": s.get("original", "")[:200], "mutated": s.get("mutated", "")[:200],
            "locus": s.get("locus", "")[:120], "potent": True,  # 276 proved 15/15
            "difficulty": difficulty(s, txt),
        })
for m in wound:
    host = m["host"]
    p = os.path.join(D276, "served_wound", host)
    items.append({
        "item_id": f"WOUND__{host.replace('.md','')}", "source": "doc276-wound", "host": host,
        "served_path": p, "seed_class": m["seed_class"], "severity": m["severity"],
        "original": m.get("original", "")[:200], "mutated": m.get("mutated", "")[:200],
        "locus": m.get("locus", "")[:120], "potent": True, "difficulty": difficulty(m, ""),
        "wounds_leg": m.get("wounds_leg", ""),
    })

json.dump(items, open(os.path.join(D277, "itembank_core.json"), "w"), indent=1)
from collections import Counter
byc = Counter(i["seed_class"] for i in items)
print(f"CORE item bank: {len(items)} potency-proven items")
print("by class:", dict(byc))
print("difficulty by class (mean):")
for c in ["NUM", "CODE", "OMIT", "MECH"]:
    ds = [i["difficulty"] for i in items if i["seed_class"] == c]
    if ds:
        print(f"  {c}: n={len(ds)} mean_diff={sum(ds)/len(ds):.3f} range[{min(ds)},{max(ds)}]")
