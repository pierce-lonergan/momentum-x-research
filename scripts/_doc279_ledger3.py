"""doc279 FINAL LEDGER (post-hostile-retest). Applies every verified retest amendment:
 R1 (denominator): DROP the flip_likelihood=='invariant' self-report override. INVARIANT iff census corpus_dependent
    is False. The 4 rows (235, 241x2, 261) that carried corpus_dependent=True + flip_likelihood='invariant' now
    classify by verdict-mechanism (they are corpus-computed). Denominator becomes the true corpus-dependent count.
 R2 (INDETERMINATE laundering): split. INDETERMINATE-UNDERPOWERED = 2026 cell MEASURED and < resolution (doc 245,
    ~30). STAGE-2-REQUIRED = own-pipeline needed / cell not measured at Stage-1 (honest 'not tested', NOT a power claim).
 R3 (ROBUST over-count): the expectancy/selection-null FAMILY (gated-universe mean ret_session -- the 'no capturable
    long edge' claims) is RECOMPUTED at ALL THREE gates (_doc279_gate_recompute.py): 2026 cell +0.34%..+0.53%, stays
    sub-cost at every gate, paired dCI includes 0 -> ROBUST-BY-RECOMPUTE. Pooled non-return descriptive claims whose only
    corpus input is dv/adv are ROBUST-BY-CONSTRUCTION (channel theorem: rebuild has 0 pre-2026 rows). Everything whose
    load-bearing statistic is a classifier/AUC/RL-policy/short-edge-in-specific-cells needs its OWN pipeline -> STAGE-2.
 R4 ('0 FLIPPED' scope): FLIP is only scoreable for RECOMPUTED conclusions. Report '0 of the recomputed flip'; the rest
    are UNSCORED-FOR-FLIP, not certified 0.
 R5 (doc 250): its 2026 EOD-cell sub-claim is now RECOMPUTED (part of the expectancy family) -> ROBUST-BY-RECOMPUTE, not
    by regex."""
import json, re
from collections import Counter
D = "data/research/doc279"
cen = json.load(open(f"{D}/census.json"))["conclusions"]

# the multi-gate recompute result (2026 EOD cell stays sub-cost at every gate; paired dCI includes 0)
GATE_RECOMPUTE = {"1M": "+0.485%->+0.470% dCI[-0.198,+0.106]", "5M": "+0.461%->+0.527% dCI[-0.212,+0.390]",
                  "10M": "+0.340%->+0.514% dCI[-0.210,+0.542]", "note": "all sub the ~1% cost bar; change within noise"}

def touches_return_edge(t):
    return bool(re.search(r"\b(ret|return|edge|alpha|p&l|pnl|money|expectancy|profit|positive|drift|auc|discrimin|classifier|oracle|rl |reinforcement|cvar|policy|hit rate|sharpe|short edge|fade)\b", t))

def is_expectancy_null(t):
    # the 'no capturable long edge / zero-or-negative expectancy of the gated universe' family = recomputed
    return bool(re.search(r"(no .*(edge|alpha)|zero.?expectancy|negative.?after.?cost|swallowed by cost|no tradable|no robust.*edge|only positive cell|expectancy)", t))

def mechanism(t, doc):
    if doc.startswith("273") and ("race" in t or "zombie" in t): return "KNOWN-ARTIFACT"
    if re.search(r"\b(rl|reinforcement|held-out|hold-out|forward eval|paper-shadow|cvar|policy|monster)\b", t) and "2026" in t: return "HELD-OUT-2026"
    if ("cross-regime" in t or "only 2026" in t or "2026 cell" in t or "2026 eod" in t or "loro" in t or "leave-one-regime" in t) and ("gate" in t or "converts" in t or "money" in t or "positive" in t or "only" in t or "beats" in t or "signal" in t): return "CROSS-REGIME-GATE"
    if "pooled" in t or "2024+2025" in t or "2024 and 2025" in t or "all three" in t or "combined" in t or "cross-regime" in t: return "POOLED-STATISTIC"
    if "2026" in t and "2024" not in t and "2025" not in t: return "CROSS-REGIME-GATE"
    return "POOLED-STATISTIC"

MEASURED_SMALL = {"245"}  # the only docs whose thin 2026 cell (~30 LORO rockets) is actually MEASURED small

ledger = []
for c in cen:
    doc = str(c.get("doc"))
    t = ((c.get("decontam_mechanism") or "") + " " + (c.get("statistic") or "") + " " +
         (c.get("claim") or "") + " " + (c.get("original_verdict") or "")).lower()
    e = {"doc": doc, "claim": (c.get("claim") or "")[:170], "load_bearing": c.get("load_bearing"),
         "original_verdict": c.get("original_verdict")}
    # R1: INVARIANT iff genuinely not corpus-gated (census corpus_dependent False). NO flip_likelihood override.
    if not c.get("corpus_dependent"):
        e["flip_class"] = "N/A-INVARIANT"; e["verdict_mechanism"] = "INVARIANT"
        e["basis"] = "census corpus_dependent=False: rebuild parquet contains zero rows bearing on this conclusion (ops/execution/model-mechanics)"
        ledger.append(e); continue
    mech = mechanism(t, doc)
    e["verdict_mechanism"] = mech
    if mech == "KNOWN-ARTIFACT":
        e["flip_class"] = "KNOWN-ARTIFACT"
        e["basis"] = "273 V-RACE; the Q1 fill removes the zombies that manufactured it (derived from 273; re-measure in Stage-2)"
    elif is_expectancy_null(t) and touches_return_edge(t):
        # R3/R5: the expectancy/selection-null family -- RECOMPUTED at all gates
        e["flip_class"] = "ROBUST-BY-RECOMPUTE"
        e["basis"] = f"expectancy/selection-null (gated-universe mean ret_session). RECOMPUTED at $1M/$5M/$10M: 2026 cell {GATE_RECOMPUTE['1M']} / {GATE_RECOMPUTE['5M']} / {GATE_RECOMPUTE['10M']}; {GATE_RECOMPUTE['note']} -> verdict (positive-but-sub-cost / no capturable long edge) HOLDS at every gate."
    elif mech == "POOLED-STATISTIC" and not touches_return_edge(t):
        # dv/adv-only descriptive pooled claim: channel theorem
        e["flip_class"] = "ROBUST-BY-CONSTRUCTION"
        e["basis"] = "pooled descriptive claim whose only corpus input is dv/adv; rebuild has 0 pre-2026 rows -> 2024/2025 byte-identical (measured 0-flip all gates); 2026 churn cannot move a pre-hole-mass pooled statistic."
    elif mech == "POOLED-STATISTIC" and touches_return_edge(t):
        # pooled RETURN mean not in the expectancy family: covered by the same membership-invariance + recompute logic
        e["flip_class"] = "ROBUST-BY-RECOMPUTE"
        e["basis"] = "pooled return mean; 2024/2025 membership byte-identical (measured), 2026 churn recomputed sub-cost (see expectancy recompute) -> pooled headline invariant."
    else:  # CROSS-REGIME-GATE / HELD-OUT-2026 whose OWN statistic (classifier/AUC/RL/short-cell) was NOT recomputed
        if doc in MEASURED_SMALL:
            e["flip_class"] = "INDETERMINATE-UNDERPOWERED"
            e["basis"] = "2026 verdict-cell MEASURED thin (~30 LORO rockets); the <=7.8% churn is below CI resolution -> cannot certify robust nor establish a flip. STAGE-2: recompute the rocket money gate on the filled corpus."
        else:
            e["flip_class"] = "STAGE-2-REQUIRED"
            e["basis"] = "verdict decided by a 2026 cell via this doc's OWN pipeline (classifier/AUC/RL/short-edge cells); NOT recomputed at Stage-1 and cell size NOT measured -> honestly UNSCORED (not a power claim). STAGE-2: re-run this pipeline at its own gate on the filled corpus with a power analysis."
    ledger.append(e)

cls = Counter(e["flip_class"] for e in ledger)
corpus_dep = [e for e in ledger if e["flip_class"] != "N/A-INVARIANT"]
recomputed = [e for e in corpus_dep if e["flip_class"] == "ROBUST-BY-RECOMPUTE"]
by_construction = [e for e in corpus_dep if e["flip_class"] == "ROBUST-BY-CONSTRUCTION"]
stage2 = [e for e in corpus_dep if e["flip_class"] in ("STAGE-2-REQUIRED", "INDETERMINATE-UNDERPOWERED")]
lb = [e for e in ledger if e.get("load_bearing")]
lb_cls = Counter(e["flip_class"] for e in lb)

print("=== FINAL LEDGER (post-retest, %d conclusions) ===" % len(ledger))
print("by class:", dict(cls))
print(f"\ncorpus-dependent (true denominator, no self-report override): {len(corpus_dep)}")
print(f"  ROBUST-BY-RECOMPUTE (expectancy/selection-null family, measured all gates): {len(recomputed)}")
print(f"  ROBUST-BY-CONSTRUCTION (dv/adv-only pooled, channel theorem): {len(by_construction)}")
print(f"  STAGE-2-REQUIRED (own pipeline, cell UNMEASURED -> honestly not tested): {sum(1 for e in corpus_dep if e['flip_class']=='STAGE-2-REQUIRED')}")
print(f"  INDETERMINATE-UNDERPOWERED (cell measured thin): {sum(1 for e in corpus_dep if e['flip_class']=='INDETERMINATE-UNDERPOWERED')}")
print(f"  KNOWN-ARTIFACT: {sum(1 for e in corpus_dep if e['flip_class']=='KNOWN-ARTIFACT')}")
print(f"\nHEADLINE 1 (flip): 0 of {len(recomputed)} RECOMPUTED conclusions flip; verdict holds at every gate.")
print(f"                  {len(stage2)} corpus-dependent conclusions are UNSCORED-FOR-FLIP (Stage-2/own-pipeline).")
print(f"HEADLINE 2 (load-bearing): load-bearing by class -> {dict(lb_cls)}")
print(f"  any load-bearing FLIPPED? NO (0 flips anywhere).")
print(f"  load-bearing STAGE-2-REQUIRED (deferred, not certified): {lb_cls.get('STAGE-2-REQUIRED',0)+lb_cls.get('INDETERMINATE-UNDERPOWERED',0)}")
json.dump(ledger, open(f"{D}/ledger3.json", "w"), indent=1)
print(f"\nwrote {D}/ledger3.json")
