"""doc276 seal step: apply fabricator seeds surgically, echo-scan, build manifests + assignment.

Input: data/research/doc276/fabricator_output.json  (dumped from the fabrication workflow)
Rules enforced here (RC3-RC5):
  - span-replacement ONLY on the pristine brief (fabricator full-texts are advisory; byte-identity
    outside seed spans is guaranteed, killing format-tell unblinding);
  - OMIT seeds delete the exact span;
  - echo-scan: the mutated token must NOT appear in docs/, data/research/doc27*_state.md, the corpus,
    or any OTHER brief; the original span MUST appear exactly once in the host brief;
  - potency recipes are NOT executed here — they are exported to potency_todo.json and executed
    one-by-one under operator control (standing rule: mechanical potency assert, executed, sealed).
Output: plants/<host>.md (mutated briefs), MANIFEST_SEALED.json, potency_todo.json.
"""
import glob
import json
import os

OUT = "data/research/doc276"
BRIEFS = os.path.join(OUT, "briefs")
PLANTS = os.path.join(OUT, "plants")

ECHO_SCAN_GLOBS = [
    "docs/research-log/27*.md",
    "data/research/doc273_state.md",
    "data/research/doc274_state.md",
    "data/research/doc275_state.md",
    "data/research/doc276_state.md",
    "data/research/doc276_corpus/*.md",
    "data/research/doc276_corpus/*.txt",
]


def echo_hits(token: str, exclude_brief: str) -> list[str]:
    """Files where the mutated token already appears (echo = reject)."""
    if not token or len(token) < 6:
        return ["<token-too-short-to-scan>"]
    hits = []
    files = []
    for g in ECHO_SCAN_GLOBS:
        files += glob.glob(g)
    files += [p for p in glob.glob(os.path.join(BRIEFS, "*.md"))
              if os.path.basename(p) != exclude_brief]
    for p in files:
        try:
            if token in open(p, encoding="utf-8", errors="replace").read():
                hits.append(p)
        except OSError:
            continue
    return hits


def main():
    fab = json.load(open(os.path.join(OUT, "fabricator_output.json"), encoding="utf-8"))
    os.makedirs(PLANTS, exist_ok=True)
    manifest, todo, rejects = [], [], []
    for plant in fab["plants"]:
        host = plant["host_brief"]
        host_path = os.path.join(BRIEFS, host)
        if not os.path.exists(host_path):
            # tolerate prefix names from fabricators
            cands = [p for p in glob.glob(os.path.join(BRIEFS, "*.md"))
                     if os.path.basename(p).startswith(host.replace(".md", ""))]
            if len(cands) != 1:
                rejects.append({"host": host, "why": f"host resolution failed ({len(cands)} candidates)"})
                continue
            host_path = cands[0]
            host = os.path.basename(host_path)
        text = open(host_path, encoding="utf-8").read()
        applied = []
        for s in plant["seeds"]:
            orig, mut = s["original"], s.get("mutated", "")
            n = text.count(orig)
            if n != 1:
                rejects.append({"host": host, "seed": s["locus"], "why": f"original span occurs {n}x (need exactly 1)"})
                continue
            if s["seed_class"] != "OMIT":
                hits = echo_hits(mut, host)
                if hits:
                    rejects.append({"host": host, "seed": s["locus"], "why": "ECHO", "hits": hits[:4]})
                    continue
                text = text.replace(orig, mut, 1)
            else:
                text = text.replace(orig, "", 1)
            applied.append(s)
            todo.append({"host": host, "locus": s["locus"], "seed_class": s["seed_class"],
                         "severity": s["severity"], "recipe": s["potency_recipe"],
                         "original": orig[:400], "mutated": (mut or "<DELETED>")[:400]})
        if applied:
            open(os.path.join(PLANTS, host), "w", encoding="utf-8").write(text)
            manifest.append({"host": host, "n_seeds": len(applied), "seeds": applied})
    json.dump(manifest, open(os.path.join(OUT, "MANIFEST_SEALED.json"), "w"), indent=1)
    json.dump(todo, open(os.path.join(OUT, "potency_todo.json"), "w"), indent=1)
    json.dump(rejects, open(os.path.join(OUT, "seal_rejects.json"), "w"), indent=1)
    sev = [s["severity"] for m in manifest for s in m["seeds"]]
    cls = [s["seed_class"] for m in manifest for s in m["seeds"]]
    print(f"plants written: {len(manifest)} | seeds applied: {len(sev)} "
          f"(S1={sev.count('S1')} S2={sev.count('S2')}) "
          f"classes: NUM={cls.count('NUM')} CODE={cls.count('CODE')} OMIT={cls.count('OMIT')} MECH={cls.count('MECH')}")
    print(f"rejects: {len(rejects)} -> seal_rejects.json")


if __name__ == "__main__":
    main()
