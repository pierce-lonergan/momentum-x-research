"""doc276: retroactive fix for the peer-review SEAL-BLINDING finding. The original echo_hits()
only scanned MUTATED tokens across siblings; a plant's TRUE (original) value colliding with a
value in another served brief is an undetected catchability tell. Scan all 15 seeds' ORIGINAL
distinctive tokens across the OTHER served briefs and report every collision."""
import json
import os
import re

D = "data/research/doc276"
SERVED = os.path.join(D, "served")
man = json.load(open(os.path.join(D, "MANIFEST_SEALED.json"), encoding="utf-8"))

served = {f: open(os.path.join(SERVED, f), encoding="utf-8").read()
          for f in os.listdir(SERVED) if f.endswith(".md")}


def distinctive_tokens(s):
    """numeric/identifier tokens from the ORIGINAL span worth collision-checking."""
    toks = set(re.findall(r"[+-]?\d[\d,]*\.\d+(?:e-?\d+)?|\b\d{4,}\b|[0-9]+\.[0-9]+%", s))
    return {t for t in toks if len(t) >= 4}


collisions = []
for m in man:
    host = m["host"]
    for sd in m["seeds"]:
        if sd["seed_class"] == "OMIT":
            continue
        for tok in distinctive_tokens(sd["original"]):
            for f, txt in served.items():
                if f == host:
                    continue
                if tok in txt:
                    collisions.append({"host": host, "seed_locus": sd["locus"][:50],
                                       "true_token": tok, "collides_in": f})

print(f"cross-brief TRUE-value collisions found: {len(collisions)}")
for c in collisions:
    print(f"  {c['host']}  true='{c['true_token']}'  ALSO IN  {c['collides_in']}")
json.dump(collisions, open(os.path.join(D, "collision_scan.json"), "w"), indent=1)
if not collisions:
    print("(none — F5/F2 15385 is the only known one; verifying it surfaces below if present)")
