"""doc 276 — THE REGRESS harness (DORMANT-C, read-only vs production).

Phase A: parse the frozen L3 retest artifacts into 18 uniform re-serialized ruling BRIEFS
(metadata stripped: no workflowProgress, no agentIds, no token counts, no verdict rosters).
Phase B: assemble the mixed audit set = real briefs + surgically-perturbed plant briefs +
clean plants (untouched briefs re-serialized identically), sealed assignment hashed.
Phase C: emit per-unit sandbox instructions for the L4 fleet.

Frozen corpus: data/research/doc276_corpus/ (hashes in the prereg commit log).
Outputs: data/research/doc276/briefs/ + assignment.json (sealed) + manifests.
"""
from __future__ import annotations
import hashlib, json, os, re, sys

CORPUS = "data/research/doc276_corpus"
OUT = "data/research/doc276"
BRIEFS = os.path.join(OUT, "briefs")

SOURCES = {
    "273": os.path.join(CORPUS, "L3_retest_doc273.txt"),
    "274": os.path.join(CORPUS, "L3_retest_doc274.txt"),
    "275": os.path.join(CORPUS, "L3_retest_doc275.txt"),
}


def _extract_rulings(path: str) -> list[dict]:
    """Pull the rulings array out of a retest task-output file (JSON embedded in text).
    Robust to truncation banners: find "rulings" and parse finding objects field-wise."""
    txt = open(path, encoding="utf-8", errors="replace").read()
    rulings = []
    # find each finding object by its finding_id anchor (files are pretty-printed:
    # scan back from the key to the enclosing brace, then brace-match forward)
    for m in re.finditer(r'"finding_id"\s*:\s*"', txt):
        start = txt.rfind("{", max(0, m.start() - 200), m.start())
        if start < 0:
            continue
        # brace-match forward
        depth = 0
        end = None
        in_str = False
        esc = False
        for i in range(start, min(len(txt), start + 200_000)):
            ch = txt[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            continue
        try:
            obj = json.loads(txt[start:end])
            if all(k in obj for k in ("finding_id", "verdict", "evidence")):
                rulings.append(obj)
        except Exception:
            continue
    # dedupe by finding_id keeping the longest evidence (files can contain previews)
    best = {}
    for r in rulings:
        k = r["finding_id"]
        if k not in best or len(r.get("evidence", "")) > len(best[k].get("evidence", "")):
            best[k] = r
    return list(best.values())


def build_briefs() -> dict:
    os.makedirs(BRIEFS, exist_ok=True)
    census = {}
    for doc, path in SOURCES.items():
        rl = _extract_rulings(path)
        census[doc] = [r["finding_id"] for r in rl]
        for r in rl:
            fid = re.sub(r"[^A-Za-z0-9_-]", "_", r["finding_id"])[:60]
            name = f"R{doc}_{fid}.md"
            body = (
                f"# AUDIT UNIT\n\n"
                f"## Ruling under audit (verbatim)\n\n"
                f"**Assigned finding id**: {r['finding_id']}\n\n"
                f"**Verdict issued**: {r['verdict']} (confidence: {r.get('confidence','?')})\n\n"
                f"### Evidence section (the auditable body)\n\n{r.get('evidence','')}\n\n"
                f"### Required-doc-changes section\n\n{r.get('required_doc_changes','')}\n"
            )
            open(os.path.join(BRIEFS, name), "w", encoding="utf-8").write(body)
    json.dump(census, open(os.path.join(OUT, "census.json"), "w"), indent=1)
    n = {k: len(v) for k, v in census.items()}
    print("brief census:", n, "total", sum(n.values()))
    return census


def seal_assignment(units: list[dict]) -> str:
    """units: [{unit_id, kind: real|plant|clean, source, manifest?}] — write sealed file,
    return sha256. The fleet never sees this file."""
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "ASSIGNMENT_SEALED.json")
    json.dump(units, open(p, "w"), indent=1)
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    print(f"assignment sealed: {len(units)} units sha256={h}")
    return h


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--briefs", action="store_true")
    a = ap.parse_args()
    if a.briefs:
        build_briefs()
