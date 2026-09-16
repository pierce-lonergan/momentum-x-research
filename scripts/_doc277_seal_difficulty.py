"""doc277: seal the difficulty manifest (per-item ex-ante scalar, item-features only) + the run item set,
BEFORE any panel model runs. Drops the doc276 within-set-tell item (R274_F5 rows-seed) per the panel."""
import hashlib
import json
import os

D277 = "data/research/doc277"
items = json.load(open(os.path.join(D277, "itembank_core.json"), encoding="utf-8"))

# panel fix: exclude the within-set-tell item (R274_F5-protocol rows seed: true 15385 collided with F2)
TELL = [i for i in items if i["host"] == "R274_F5-protocol.md" and i["seed_class"] == "NUM"]
kept = [i for i in items if not (i["host"] == "R274_F5-protocol.md" and i["seed_class"] == "NUM")]

manifest = {
    "_meta": "doc277 SEALED difficulty manifest — per-item ex-ante difficulty (item features only, never model catch). "
             "Sealed BEFORE any panel model runs. Difficulty fn = scripts/_doc277_itembank_core.py::difficulty (frozen).",
    "excluded_tell_items": [i["item_id"] for i in TELL],
    "n_items": len(kept),
    "items": [{"item_id": i["item_id"], "seed_class": i["seed_class"], "severity": i["severity"],
               "difficulty": i["difficulty"], "source": i["source"],
               "recompute_identity_flag": (i["seed_class"] == "NUM" and i["difficulty"] >= 0.55),
               "served_path": i["served_path"]} for i in kept],
}
p = os.path.join(D277, "DIFFICULTY_SEALED.json")
json.dump(manifest, open(p, "w"), indent=1)
h = hashlib.sha256(open(p, "rb").read()).hexdigest()
from collections import Counter
byc = Counter(i["seed_class"] for i in kept)
print(f"SEALED {len(kept)} items (excluded {len(TELL)} tell item) sha256={h[:16]}")
print("by class:", dict(byc))
print("difficulty range:", round(min(i['difficulty'] for i in kept), 3), "-", round(max(i['difficulty'] for i in kept), 3))
# record the seal hash for the doc
open(os.path.join(D277, "SEAL_HASHES.txt"), "a").write(f"DIFFICULTY_SEALED.json sha256={h}\n")
