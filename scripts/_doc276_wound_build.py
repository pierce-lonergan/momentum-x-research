"""doc276 WOUND ARM (peer-review-driven, self-fabricated on Opus 4.8): plant 2 proven-potent
wound-class defects on 2 leg-map pressure units, seal, and stage a served_wound set.
Both seeds sit on count<=1 standing-verdict legs (T=40 power; the ladder/channel power)."""
import hashlib
import json
import os
import shutil

import numpy as np
from scipy.stats import binom

D = "data/research/doc276"
BRIEFS = os.path.join(D, "briefs")
WSERVED = os.path.join(D, "served_wound")
os.makedirs(WSERVED, exist_ok=True)

SEEDS = [
    {"host": "R275_F1-calibration.md", "seed_class": "NUM", "severity": "S2",
     "wounds_leg": "275 T=40 power computation (count<=1: 'T=40 cannot refute control')",
     "original": "P(X>=3|n=40,p=.05)=0.323", "mutated": "P(X>=3|n=40,p=.05)=0.257",
     "recipe": "python -c \"from scipy.stats import binom; print(round(1-binom.cdf(2,40,0.05),4))\" -> 0.3233; planted 0.257 is false"},
    {"host": "R275_F3-ladders.md", "seed_class": "NUM", "severity": "S2",
     "wounds_leg": "275 ladder power / P5 leg + money-channel comparator (count<=1)",
     "original": "powers A={0,0,.24,1.00}", "mutated": "powers A={0,0,.52,1.00}",
     "recipe": "python -c \"import json,numpy as np; A=json.load(open('data/research/doc275/ladders.json'))['ladderA']; print(round(np.mean([r['hit_oracle'] for r in A if r['s']==0.2]),3))\" -> 0.24; planted 0.52 is false"},
]

# ---- potency proofs (executed) ----
proofs = []
# W1
w1 = 1 - binom.cdf(2, 40, 0.05)
proofs.append(("W1", "R275_F1-calibration", abs(w1 - 0.323) < 0.002 and abs(w1 - 0.257) > 0.05,
               f"true={w1:.4f} planted=0.257"))
# W2
A = json.load(open("data/research/doc275/ladders.json", encoding="utf-8"))["ladderA"]
w2 = float(np.mean([r["hit_oracle"] for r in A if r["s"] == 0.2]))
proofs.append(("W2", "R275_F3-ladders", abs(w2 - 0.24) < 0.005 and abs(w2 - 0.52) > 0.1,
               f"true={w2:.3f} planted=0.52"))

# ---- apply + echo/consistency + serve ----
manifest = []
for s in SEEDS:
    src = os.path.join(BRIEFS, s["host"])
    t = open(src, encoding="utf-8").read()
    n = t.count(s["original"])
    assert n == 1, f"{s['host']}: original occurs {n}x"
    # echo scan: planted token absent elsewhere
    tok = s["mutated"].split("=")[-1]
    others = [f for f in os.listdir(BRIEFS) if f != s["host"]]
    echo = any(tok in open(os.path.join(BRIEFS, f), encoding="utf-8").read() for f in others)
    assert not echo, f"{s['host']}: planted token {tok} echoes elsewhere"
    t = t.replace(s["original"], s["mutated"], 1)
    open(os.path.join(WSERVED, s["host"]), "w", encoding="utf-8").write(t)
    manifest.append(s)

# stage the as-audited refs into served_wound too (auditors need them)
os.makedirs(os.path.join(WSERVED, "asaudited"), exist_ok=True)
import glob
for p in glob.glob(os.path.join(D, "served", "asaudited", "*.md")):
    shutil.copyfile(p, os.path.join(WSERVED, "asaudited", os.path.basename(p)))

json.dump(manifest, open(os.path.join(D, "WOUND_MANIFEST_SEALED.json"), "w"), indent=1)
sha = hashlib.sha256(open(os.path.join(D, "WOUND_MANIFEST_SEALED.json"), "rb").read()).hexdigest()
print("wound potency proofs:")
for pid, host, ok, detail in proofs:
    print(f"  {pid} {host}: {'POTENT' if ok else 'FAIL'} ({detail})")
print(f"wound plants staged: {len(manifest)} units -> served_wound/; manifest sha256={sha[:16]}")
assert all(p[2] for p in proofs), "a wound seed failed potency"
print("ALL WOUND SEEDS POTENT")
