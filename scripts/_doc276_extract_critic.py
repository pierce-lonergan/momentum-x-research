"""doc276: extract the L3 critic blocks (273, 274) into the sealed power key (RC10)."""
import re, json, hashlib, os

out = {}
for doc in ["273", "274"]:
    t = open(f"data/research/doc276_corpus/L3_retest_doc{doc}.txt", encoding="utf-8", errors="replace").read()
    m = re.search(r'"critic"\s*:', t)
    print(doc, "explicit critic key:", bool(m), m.start() if m else "-")
    if not m:
        continue
    s = t.find("{", m.end())
    depth = 0
    instr = False
    esc = False
    e = None
    for i in range(s, len(t)):
        ch = t[i]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            instr = not instr
            continue
        if instr:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                e = i + 1
                break
    out[doc] = t[s:e]
    print("  extracted", len(out[doc]), "chars")

os.makedirs("data/research/doc276", exist_ok=True)
p = "data/research/doc276/CRITIC_KEY_SEALED.json"
json.dump(out, open(p, "w"), indent=1)
h = hashlib.sha256(open(p, "rb").read()).hexdigest()
print("sealed critic key sha256", h[:16], "docs:", list(out))
