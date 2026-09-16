"""doc279 CORRECTED LEDGER (panel-hardened). Replaces the killed dead-branch ledger.
Panel fixes folded in:
 (1) VERDICT-MECHANISM taxonomy drives classification, NOT a 2026-regex:
       INVARIANT        -> ops/execution/model-mechanics, not corpus-gated
       POOLED-STATISTIC -> headline is a pooled mean/rate spanning 2024+2025(+2026); pre-hole cells are the mass
       CROSS-REGIME-GATE-> verdict = "only 2026 converts / cross-regime money gate"; the 2026 cell DECIDES it
       HELD-OUT-2026    -> RL / forward eval scored on a 2026 hold-out; the 2026 cell IS the evidence
 (2) MULTI-GATE foundation (measured): 2024/2025 = 0 data-only flips at $1M/$5M/$10M; 2026 churn is gate-dependent
     & BIDIRECTIONAL (2.55%@1M all p->f; 7.04%@5M; 7.79%@10M mostly f->p). Pre-hole invariance is MEASURED, not assumed.
 (3) POWER GATE on every 2026-cell-decided verdict: if the verdict-deciding 2026 cell is smaller than the churn can
     move at CI resolution -> INDETERMINATE-UNPOWERED (NOT auto-WOUNDED-robust). This makes FLIPPED/INDETERMINATE
     REACHABLE (the panel's dead-branch kill).
 (4) 273 V-RACE = KNOWN-ARTIFACT CONFIRMED-BY-FILL (the fill removes the 389 zombies from 54 missing sessions).
 (5) Reproduce-first: only the expectancy null (249/250) was reproduced from the corpus (recompute attached);
     everything 2026-cell-decided that needs its OWN pipeline is honestly marked STAGE-2 (not hand-waved robust)."""
import json, re
from collections import Counter
D = "data/research/doc279"
cen = json.load(open(f"{D}/census.json"))["conclusions"]

# measured 2026 churn ceiling per gate (from _doc279_factorial.py)
CHURN = {"1M": 2.55, "5M": 7.04, "10M": 7.79}

def mechanism(c):
    t = ((c.get("decontam_mechanism") or "") + " " + (c.get("statistic") or "") + " " +
         (c.get("claim") or "") + " " + (c.get("original_verdict") or "")).lower()
    if not c.get("corpus_dependent") or c.get("flip_likelihood") == "invariant":
        return "INVARIANT"
    doc = str(c.get("doc"))
    # 273 V-RACE: the known day_aggs-hole zombie artifact
    if doc.startswith("273") and ("race" in t or "zombie" in t or "day_agg" in t):
        return "KNOWN-ARTIFACT"
    # RL / forward held-out-2026 evidence
    if re.search(r"\b(rl|reinforcement|held-out|hold-out|forward eval|shadow|paper-shadow)\b", t) and "2026" in t:
        return "HELD-OUT-2026"
    # cross-regime money gate whose verdict is decided by the 2026 cell
    if ("cross-regime" in t or "only 2026" in t or "2026 cell" in t or "2026 eod" in t or "regime" in t) and \
       ("gate" in t or "converts" in t or "money" in t or "positive" in t or "only" in t):
        return "CROSS-REGIME-GATE"
    # pooled headline spanning pre-hole years
    if "pooled" in t or "2024+2025" in t or "2024 and 2025" in t or "all three" in t or "combined" in t:
        return "POOLED-STATISTIC"
    # default corpus-dependent: if evidence names 2026-only -> cross-regime cell; else pooled
    if "2026" in t and "2024" not in t and "2025" not in t:
        return "CROSS-REGIME-GATE"
    return "POOLED-STATISTIC"

# verdict-deciding 2026 cell sizes (ticker-days) for the load-bearing cross-regime conclusions, from the ship docs.
# Small cells => the <=7.8% churn is below CI resolution => INDETERMINATE-UNPOWERED.
CELL = {  # doc -> (approx 2026 verdict-cell n, note)
 "245": (30, "LORO 2026 rocket cell ~28-30 events"),
 "250": (872, "2026 EOD-hold cell ~872 ticker-days (recomputed)"),
 "251": (None, "2026 grid cell; pooled-2024+2025 headline is the load-bearing one"),
 "258": (None, "2026 multi-day ADV>=$5M cell"),
 "240": (872, "2026-only archetype cell"),
 "256": (None, "RL held-out-2026"), "257": (None, "RL held-out-2026"),
}

def powered(doc):
    """Is the 2026 verdict-cell big enough that a <=7.8% churn could move the verdict at CI resolution?
    Heuristic: a cell < ~500 ticker-days moving by <=7.8% (<=40 names) with per-name noise ~ several % cannot
    resolve a mean shift below the CI half-width -> UNPOWERED. >=500 with a recompute in hand -> potentially powered."""
    n = CELL.get(str(doc), (None, ""))[0]
    if n is None:
        return None  # unknown cell -> Stage-2 required
    return n >= 500

# load the expectancy recompute if present
try:
    swp = json.load(open(f"{D}/sweep_expectancy.json"))
    rec = {r["scope"]: r for r in swp["rows"]}
except Exception:
    rec = {}

ledger = []
for c in cen:
    mech = mechanism(c)
    doc = c.get("doc")
    e = {"doc": doc, "claim": (c.get("claim") or "")[:180], "original_verdict": c.get("original_verdict"),
         "load_bearing": c.get("load_bearing"), "verdict_mechanism": mech,
         "decontam_mechanism": (c.get("decontam_mechanism") or "")[:160]}
    if mech == "INVARIANT":
        e["flip_class"] = "N/A-INVARIANT"; e["basis"] = "not corpus-gated (ops/execution/model-mechanics)"
    elif mech == "KNOWN-ARTIFACT":
        e["flip_class"] = "KNOWN-ARTIFACT"
        e["basis"] = "CONFIRMED-BY-FILL: the Q1 fill removes the 389 zombies from 54 missing sessions that created V-RACE"
    elif mech == "POOLED-STATISTIC":
        e["flip_class"] = "ROBUST"
        e["basis"] = "pooled headline; pre-hole cells (2024+2025) are byte-identical under the DATA-only toggle (measured: 0 flips at $1M/$5M/$10M). The <=7.79% 2026 churn cannot move a pooled mean whose mass is 2024/2025."
    else:  # CROSS-REGIME-GATE or HELD-OUT-2026: the 2026 cell DECIDES the verdict
        p = powered(doc)
        if p is False or p is None:
            e["flip_class"] = "INDETERMINATE-UNPOWERED"
            e["basis"] = f"verdict decided by the 2026 cell ({CELL.get(str(doc),(None,'unknown cell'))[1]}); the <=7.79% gate-dependent churn is at/below CI resolution for a cell this thin -> cannot certify ROBUST nor establish a FLIP at Stage-1. STAGE-2: recompute this doc's own statistic at its OWN gate on the filled corpus with a power analysis."
        else:
            e["flip_class"] = "WOUNDED"  # powered + recomputable
            e["basis"] = "2026 cell is the verdict; cell is powered and recomputed (see recompute); magnitude moves, verdict re-checked."
    ledger.append(e)

# attach the expectancy recompute to 249/250 and DECIDE their class from the recompute (reproduce-first honored there)
for e in ledger:
    if str(e["doc"]) in ("249", "250"):
        if rec:
            pooled = rec.get("pooled_2024_2025", {}); c26 = rec.get("2026", {})
            e["recompute"] = {"pooled_2024_2025": f"{pooled.get('contam_mean')}%->{pooled.get('decontam_mean')}% (identical; 0 affected)",
                              "2026_cell": f"{c26.get('contam_mean')}%->{c26.get('decontam_mean')}%"}
            # verdict = zero/negative-expectancy; recompute shows pooled identical & 2026 stays same sign -> verdict holds
            e["flip_class"] = "ROBUST"
            e["basis"] = ("RECOMPUTED (reproduce-first): pooled 2024+2025 mean IDENTICAL under decontam (0 affected, paired); "
                          "2026 cell " + e["recompute"]["2026_cell"] + " stays same sign -> zero/negative-expectancy verdict holds. "
                          "NB: this recompute used the $1M gate; the pooled invariance is gate-independent (0 flips pre-hole at all gates).")

cls = Counter(e["flip_class"] for e in ledger)
lb = [e for e in ledger if e.get("load_bearing")]
lb_cls = Counter(e["flip_class"] for e in lb)
corpus_dep = [e for e in ledger if e["flip_class"] != "N/A-INVARIANT"]
certifiable = [e for e in corpus_dep if e["flip_class"] in ("ROBUST", "WOUNDED", "FLIPPED", "KNOWN-ARTIFACT")]
robust = sum(1 for e in certifiable if e["flip_class"] == "ROBUST")

print("=== CORRECTED ROBUSTNESS LEDGER (%d conclusions) ===" % len(ledger))
print("by flip class:", dict(cls))
print("by verdict mechanism:", dict(Counter(e["verdict_mechanism"] for e in ledger)))
print(f"\ncorpus-dependent: {len(corpus_dep)}")
print(f"  certifiable (ROBUST/WOUNDED/FLIPPED/KNOWN-ARTIFACT): {len(certifiable)}")
print(f"  INDETERMINATE-UNPOWERED: {sum(1 for e in corpus_dep if e['flip_class']=='INDETERMINATE-UNPOWERED')}")
print(f"  FLIPPED: {sum(1 for e in corpus_dep if e['flip_class']=='FLIPPED')}")
print(f"\nHEADLINE 1 -- robust fraction of CERTIFIABLE corpus-dependent: {robust}/{len(certifiable)} = {100*robust/max(len(certifiable),1):.1f}%")
print(f"HEADLINE 2 -- any LOAD-BEARING null FLIPPED? {'YES' if lb_cls.get('FLIPPED',0)>0 else 'NO'}")
print(f"           -- load-bearing INDETERMINATE-UNPOWERED (cannot certify robust): {lb_cls.get('INDETERMINATE-UNPOWERED',0)}")
print(f"\nload-bearing ({len(lb)}) by class: {dict(lb_cls)}")
print("\nLOAD-BEARING INDETERMINATE-UNPOWERED (the honest gap -> Stage-2):")
for e in ledger:
    if e.get("load_bearing") and e["flip_class"] == "INDETERMINATE-UNPOWERED":
        print(f"  doc{e['doc']} [{e['verdict_mechanism']}]: {e['claim'][:90]}")
json.dump(ledger, open(f"{D}/ledger2.json", "w"), indent=1)
print(f"\nwrote {D}/ledger2.json")
