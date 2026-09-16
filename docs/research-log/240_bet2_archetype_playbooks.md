# 240 — BET#2 (catalyst-archetype playbooks): fails Gate 3 (separation) on available data; closes.

**Author**: Claude Opus 4.8
**Date**: 2026-06-03
**Mandate**: Pierce — build the {pattern signature → playbook} store, LLM tags the live setup, retrieves the
matching playbook. Gates: (1) tagger kappa ≥ 0.6; (2) per-archetype N ≥ 30/year; (3) archetypes separate
(KS on 30-min returns, Bonferroni). Any survivor → doc-235-style cross-regime CPCV pre-registration.
"Expect most to fail; do not relax gates to preserve the project."

## Approach decision (my call) + two blockers found
- **Production LLM tagger is BLOCKED here:** direct `api.together.xyz` returns **403 on every model**
  (auth/permission for this shell; the bot reaches Together via its own litellm path). So Gate-1's
  LLM-vs-LLM kappa can't run from here.
- **Cross-regime news is not on disk:** only the 2026 eval journals carry `news_headlines`; 2024-25 would
  need a news fetch (~9k ticker-days).
- **So I ran the cheapest *decisive* gate first — Gate 3 (separation) — on the 2026 data I have, with a
  reproducible RULE-BASED tagger.** Rationale: if archetypes don't separate even in-sample, no tagger or
  playbook work is justified (Gate 1 kappa is moot — tagger quality can't rescue non-separating archetypes).
  `scripts/bet2_archetype_separation_doc240.py`. Taxonomy (8): FDA_APPROVAL, CLINICAL_DATA, MA_DEAL,
  OFFERING_DILUTION, REVERSE_SPLIT, EARNINGS, GENERAL_PROMO, NO_CATALYST/OTHER (doc-231 + MEMORY patterns).

## Gate 2 (preliminary, 2026-only): sample sizes + 30-min returns
| archetype | N (2026) | median r30 | mean r30 | P(up) |
|---|---|---|---|---|
| GENERAL_PROMO | 250 | +0.33% | +1.40% | 52% |
| NO_CATALYST | 303 | +0.36% | +2.11% | 54% |
| EARNINGS | 104 | +0.66% | +0.48% | 55% |
| MA_DEAL | 84 | −0.07% | +3.62% | 48% |
| OTHER_NEWS | 83 | −0.06% | +1.46% | 46% |
| OFFERING_DILUTION | 22 | +0.92% | −0.19% | 55% |
| CLINICAL_DATA | 18 | +0.75% | +3.60% | 56% |
| FDA_APPROVAL | 6 | +0.66% | +5.38% | 50% |
| REVERSE_SPLIT | 2 | −3.93% | −3.93% | 0% |
Only **5 archetypes reach N≥30** (and only in 2026; a per-*year* N≥30 needs the cross-regime fetch).

## Gate 3 (DECISIVE): separation — FAILS
KS test, each N≥30 archetype vs the base (all) 30-min returns, Bonferroni α=0.01:
| archetype | KS | p | separates? |
|---|---|---|---|
| EARNINGS | 0.095 | 0.352 | no |
| GENERAL_PROMO | 0.062 | 0.424 | no |
| MA_DEAL | 0.078 | 0.706 | no |
| NO_CATALYST | 0.060 | 0.381 | no |
| OTHER_NEWS | 0.080 | 0.684 | no |

**None separates from base (p 0.35–0.71, all ≫ α); no pairwise separation either.** Per the gate, the
playbooks would be **the base strategy mislabeled.** → **BET#2 fails Gate 3 on available data.**

## Honest scope (anti-asymmetric-skepticism — what this does NOT fully foreclose)
1. **Tagger noise caveat:** a rule-based tagger mislabels some headlines, blurring archetypes toward base
   and *reducing* apparent separation — which is exactly why Gate 1 (kappa) is supposed to come first. So
   this null is **suggestive, not dispositive.** Counter: the rule-based tagger is *not* pure noise — it
   surfaces FDA/clinical as nominally higher-mean, so it captures real signal, and the big archetypes
   still show no separation. Strong archetype edge would have partially shown through.
2. **The one un-foreclosed sliver:** **biotech catalysts (FDA +5.4%, CLINICAL +3.6%)** are nominally the
   highest-mean and the doc-231 research flagged them as genuinely distinct — but both **fail Gate 2 (N=6,
   18 ≪ 30)**, so they are *untestable* under your bar here. Testing them needs (a) the cross-regime news
   fetch to accumulate N≥30/year, (b) the kappa-validated LLM tagger, and (c) they would *still* face the
   cross-regime CPCV gauntlet (strong prior: fail, per the session's pattern).
3. **2026-only, in-sample, 30-min horizon.** If archetypes don't separate in-sample, cross-regime (harder)
   won't rescue them — so the null is unlikely to flip, but it is not the binding cross-regime test.

## Verdict
**BET#2 does not clear its gates on the available data, and per your rule I do not relax them to preserve
the project: BET#2 closes.** The only un-foreclosed path is the rare biotech-catalyst sliver (FDA/clinical),
which is untestable here (N<30) and gated behind the Together-403 + cross-regime-news blockers — pursued
**only** if you want to resolve those blockers AND accept it still faces the cross-regime gauntlet.

## Where this lands the whole program
Catalyst archetypes don't separate (240) on top of: structural segments homogeneous (235), selection alpha
dead (235), model overlay a no-op (236), all structural levers exhausted (238/239). **The low-float gapper
universe is homogeneous across BOTH structure AND catalyst — there is no extractable sub-population edge for
us in it.** That is the hard, repeatedly-confirmed truth. The honest path forward is Pierce's pre-stated
fork **(b): change the selection universe** (a different float/liquidity/catalyst-quality regime where
sub-populations *do* separate — e.g. higher-float, higher-priced, genuine-biotech-catalyst names with
tradeable liquidity), **or treat the system as the hardened paper research instrument it now is.** The
durable, confirmed win of this entire arc remains **execution integrity** (phantom P&L killed + regression-
locked) — it makes the P&L honest, which is the precondition for any edge in any universe.

**No live change.** **Basis**: `scripts/bet2_archetype_separation_doc240.py`, 872 2026 ticker-days
(journal headlines × exit_labels 30-min returns). **Predecessors**: 231 (the playbook-RAG idea + research),
235 (structural homogeneity + regime-fragility), 239 (structural levers exhausted).
