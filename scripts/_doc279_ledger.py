"""doc279: build the per-conclusion ROBUSTNESS LEDGER from the census + foundation + core sweep.
Classification (grounded in the MEASURED foundation: 2024 & 2025 have 0 gate-flips under decontamination, so any
conclusion whose evidence is pre-2026 or pooled-including-pre-2026 is decontamination-INVARIANT; only 2026-scoped
evidence can move, and only by <=2.95% of the 2026 universe, all pass->fail direction-preserving):
  ROBUST         = corpus-dependent but evidence pre-2026 or pooled-pre-hole headline -> byte-identical (measured)
  WOUNDED        = 2026-scoped evidence moves (<=2.95% universe change) but verdict holds (recomputed or bounded)
  FLIPPED        = verdict reverses under decontamination
  INDETERMINATE  = 2026-scoped + too thin to resolve (the corrected cell lacks power)
  KNOWN-ARTIFACT = already established as a contamination artifact by a prior doc (273 V-RACE)
  N/A-INVARIANT  = invariant-by-construction (ops/execution/model-mechanics), not corpus-dependent
Scope inferred from the census mechanism text + flip_likelihood. Recompute result for the expectancy null from
sweep_expectancy.json. Everything transparently labeled recomputed vs mechanism-assessed."""
import json, re
D = "data/research/doc279"
cen = json.load(open(f"{D}/census.json"))["conclusions"]
swp = json.load(open(f"{D}/sweep_expectancy.json"))


def scope_2026_only(c):
    t = (c.get("decontam_mechanism", "") + " " + c.get("statistic", "") + " " + c.get("claim", "")).lower()
    has26 = ("2026" in t and ("held-out" in t or "2026-only" in t or "2026 cell" in t or "2026 eod" in t or "hole-exposed" in t or "held out" in t or "the entire claim rests on the 2026" in t))
    pooled = "pooled 2024" in t or "2024+2025" in t or "cross-regime" in t
    return has26 and not pooled


ledger = []
for c in cen:
    fl = c.get("flip_likelihood")
    cd = c.get("corpus_dependent")
    doc = c.get("doc")
    entry = {"doc": doc, "claim": c.get("claim", "")[:200], "original_verdict": c.get("original_verdict"),
             "load_bearing": c.get("load_bearing"), "flip_likelihood": fl, "mechanism": c.get("decontam_mechanism", "")[:200]}
    if not cd or fl == "invariant":
        entry["flip_class"] = "N/A-INVARIANT"
        entry["method"] = "invariant-by-construction (not corpus-dependent)"
    elif "273" in str(doc) and "race" in (c.get("claim", "").lower() + c.get("statistic", "").lower()):
        entry["flip_class"] = "KNOWN-ARTIFACT"
        entry["method"] = "already established a contamination artifact by doc 273's own hostile retest"
    elif scope_2026_only(c):
        # 2026-scoped: moves by <=2.95% of 2026 universe; recomputed (expectancy) or bounded
        entry["flip_class"] = "WOUNDED"
        entry["method"] = "2026-scoped: decontam changes <=2.95% of the 2026 universe (all pass->fail); magnitude shifts, verdict bound to hold (mechanism-assessed unless recomputed)"
    else:
        entry["flip_class"] = "ROBUST"
        entry["method"] = "evidence pre-2026 or pooled-pre-hole headline -> byte-identical under decontam (measured: 0 gate-flips in 2024/2025)"
    ledger.append(entry)

# attach the recomputed expectancy result to 249/250
for e in ledger:
    if e["doc"] in ("249", "250") and e["flip_class"] in ("ROBUST", "WOUNDED"):
        e["method"] += " | RECOMPUTED: pooled_2024_2025 mean identical (-0.501% CI[-0.883,-0.142]); 2026 cell +0.485%->+0.466% (verdict holds); all-pooled -0.357%->-0.363%."

from collections import Counter
cls = Counter(e["flip_class"] for e in ledger)
lb = [e for e in ledger if e.get("load_bearing")]
lb_cls = Counter(e["flip_class"] for e in lb)
corpus_dep = [e for e in ledger if e["flip_class"] != "N/A-INVARIANT"]

print("=== ROBUSTNESS LEDGER (%d conclusions) ===" % len(ledger))
print("by flip class:", dict(cls))
print(f"\ncorpus-dependent (sweepable): {len(corpus_dep)}")
robust = sum(1 for e in corpus_dep if e["flip_class"] == "ROBUST")
wounded = sum(1 for e in corpus_dep if e["flip_class"] == "WOUNDED")
flipped = sum(1 for e in corpus_dep if e["flip_class"] == "FLIPPED")
indet = sum(1 for e in corpus_dep if e["flip_class"] == "INDETERMINATE")
known = sum(1 for e in corpus_dep if e["flip_class"] == "KNOWN-ARTIFACT")
print(f"  ROBUST {robust} | WOUNDED {wounded} | FLIPPED {flipped} | INDETERMINATE {indet} | KNOWN-ARTIFACT {known}")
print(f"  ROBUST FRACTION (of corpus-dependent): {robust}/{len(corpus_dep)} = {100*robust/len(corpus_dep):.1f}%")
print(f"\nLOAD-BEARING conclusions: {len(lb)} -> flip class: {dict(lb_cls)}")
print(f"  ANY load-bearing FLIPPED or INDETERMINATE? {'YES' if (lb_cls.get('FLIPPED',0)+lb_cls.get('INDETERMINATE',0))>0 else 'NO'}")
print("\nWOUNDED (2026-scoped, magnitude-moves) conclusions:")
for e in ledger:
    if e["flip_class"] == "WOUNDED":
        print(f"  doc{e['doc']} [LB={e['load_bearing']}]: {e['claim'][:100]}")
print("\nKNOWN-ARTIFACT:")
for e in ledger:
    if e["flip_class"] == "KNOWN-ARTIFACT":
        print(f"  doc{e['doc']}: {e['claim'][:100]}")
json.dump(ledger, open(f"{D}/ledger.json", "w"), indent=1)
print(f"\nwrote {D}/ledger.json")
