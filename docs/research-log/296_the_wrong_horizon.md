# DOC 296 — THE WRONG HORIZON: Stage-3 unblinds, passes every frozen gate, and the fleet kills it

**Verdict in TARGET.md units: the volatility door contributes 0% of the +10%/ticket-net daily
requirement at the frozen 5% cap. The Stage-3 "pass" is VOID — the runner evaluated a 1-day
forecast horizon where the frozen prereg specifies the 21-day (30d option tenor) horizon; at the
frozen horizon the same panel is NEGATIVE (−4.43%, CI [−0.0119, +0.0005]), replicating the doc-292
pilot. Family closure recommended (The operator ratifies). Certified edges program-wide: still none.**

Session date: **2026-07-28** (corrected in doc 297 — this doc and commit `09a700f` were stamped
2026-07-13 by a skewed sandbox clock; the OS clock, the live bot journals, the broker recon tick and
62 EOD reports all place the work on 2026-07-28). Prereg: `scripts/_doc294_PREREG.md`, sha256
`ae62ea59…f098fe`, frozen at `5c2b0e7` — re-verified byte-exact before unblinding.

**Death-date correction:** with the true date, Stage-3's death (2026-08-15) is 18 days out and SEVP's
(2026-09-01) is 34 days out — and per doc 297 the IV collector has not run since 2026-07-13, so SEVP
has been frozen at 205/300 for 11 sessions. The §9 Pierce item below is now urgent, not routine.

---

## 1. What happened, in order (§A protocol, executed exactly)

1. **Power gate went GREEN blind.** After ~96 aimed collection slices, the sign-randomized
   resolution reached 3.999% ≤ 4.0% on 62 usable names (12,170 panel rows, 7,285 scored by the
   walk-forward, 159 eval days). No gate direction was read before this point.
2. **Prereg hash re-verified** byte-exact against `5c2b0e7`. Runner committed blind at `d64f02d`,
   unchanged since; IV store last write preceded unblind. Chain clean (skeptic-confirmed).
3. **Frozen gates ran once:** G1 pooled QLIKE improvement **+5.106%** (≥2% MMI), block-42 CI95
   [0.000859, 0.001893] excluding 0; both halves positive with CIs excluding 0; G2 HLN λ **0.639**,
   block-42 CI [0.588, 0.702] excluding 0. Every frozen gate passed.
   (`data/research/doc294/stage3_gates.json` + `stage3_gates_completion.json`.)
4. **Status: PROVISIONAL.** Per §A, no pass is a PASS until it survives the mandatory 3-skeptic
   fleet. The fleet ran (3 agents, ~303K tokens, 76 tool calls, ~11 min).
5. **The fleet killed it.** Skeptic 1 (leakage/protocol): **ARTIFACT = TRUE, high confidence.**

This is the first §A unblind in program history, and the first time the mandatory-fleet rule fired
on a live pass. It worked.

## 2. The artifact: a silent horizon swap

The frozen chain specifies **h=21 trading days** — target V(t) = (252/h)·Σ rv²(t+1…t+h), the
30d-tenor horizon that matches the IV being beaten and the only horizon with a monetization path
(doc-292's own verdict: "Stage 3 stays at the 30d tenor"). The runner I built for doc 294 inherited
the doc-291 panel builder's **h=1 next-day target** — its own comment said so ("h=1 here: next-day
target") — and nobody caught it until the fleet. Committed blind, not retro-tuned: a construction
error, not a peek. But it means the gates certified the wrong question.

- At h=1, intraday features genuinely predict next-day vol — that is doc-291's already-known
  result. A 30d ATM IV adds little at h=1, so "challenger beats baseline" was near-preordained.
- **Skeptic counterfactual at the frozen h=21 target, same 62-name panel:** improvement **−4.433%**,
  CI95 [−0.011857, +0.000450] (includes 0), both halves negative — quantitatively replicating the
  doc-292 pilot's single-name −4.2%. There was never a reversal to explain.
- The other two skeptics confirmed the h=1 arithmetic is real and even interesting (below), but
  interesting-at-the-wrong-horizon is not a Stage-3 verdict.

## 3. What the fleet found beyond the kill

**Composition (skeptic 2, artifact=false within the h=1 frame):** 47/62 names improved; the
doc-292 pilot mega-caps REPLICATE their null inside this panel (AAPL/AMZN/GS/IBM pooled +1.20%,
CI [−0.0012, +0.0012]); the effect lives in the 58 less-liquid names (+5.47%), anti-correlated with
liquidity (Spearman −0.257). Durable ex-top-10-days figure +2.39% (CI excl 0); joint harsh stress
(drop top-5 names AND top-10 remaining days) collapses to +0.77%, CI including 0.

**Attribution/IV-quality (skeptic 3, artifact=false on the numbers):** reproduced the certified
result exactly. The momentum-x vocabulary ALONE certifies at h=1 (+2.97%, all CIs excl 0) while the
doc-291 generic pair alone does not (+2.49%, CI incl 0) — the first panel where our vocabulary
carries anything. **But:** our self-computed BS-inverted IV is objectively weak (median corr with
next-day RV 0.364 vs HAR's 0.626; below HAR on 61/62 names), and matched-training on the
cleanest-IV half of names cuts the effect to +2.62% with CI including 0 (dirty half +6.69%; random
control +4.34%) — a real "our features repair our own IV noise" component. Effect decays −60% in
the second half of the eval window.

**Disclosed knife-edge:** the power gate went GREEN at 3.9994% vs the 4.0% bar; 2 of 10 alternate
bootstrap seeds land above 4.0%. Procedurally valid (frozen seed, direction-blind statistic), but
the unblind timing rests on ~0.03% of Monte-Carlo luck. Also disclosed: QLIKE was computed on
volatility rather than annualized-variance units (symmetric across arms; weakens comparability to
doc-291/292 headline numbers), and the ≥120-IV-day usability filter (68→62 names) was implied but
not written in the prereg text.

## 4. The MAJOR find: IV-store look-ahead — found, purged, guarded

Skeptic 1 also found that **44.1% of the panel's IV rows came from contracts whose strike was
selected using spot at a FUTURE monthly anchor** (median 7, max 22 days ahead): the collector
emitted a contract's full price history, including days before the anchor that chose it. This
contaminates day-t-ATM semantics, the MZ calibration, and — critically — the **still-blind SEVP
gate's premium legs** (a straddle whose moneyness knows the earnings move).

Remediation, all landed this session:
- **Collector emission guard** ([_doc293_iv_collector.py](scripts/_doc293_iv_collector.py)): IV and
  price rows are emitted only for dates ≥ the contract's selection anchor; `contract_anchors()`
  provenance map added; backfill path guarded identically.
- **Retroactive purge** ([_doc296_purge_lookahead.py](scripts/_doc296_purge_lookahead.py),
  conservative min-anchor rule, tenor-floor off-by-one handled): option prices 67,579 → 50,410
  (−17,169 rows); IV 38,574 → 27,263 (−11,311). Backups `*.bak-doc296`.
- **SEVP coverage recount: 227 → 205 of 300** two-leg events (22 events stood on contaminated
  legs). SEVP remains BLIND and its validity is now protected; death date 2026-09-01 unchanged.

## 5. State of the frozen Stage-3 instrument after the fixes

The runner is now conformed to the frozen target
([_doc294_stage3_runner.py](scripts/_doc294_stage3_runner.py): h=21 forward annualized variance,
calibration restricted to fully-realized rows, 21-session train embargo). On the purged store it
reports: 59 usable names, 2,350 panel rows, **0 scored eval days — NOT-COMPUTABLE**
(`data/research/doc294/stage3_status_h21_postpurge.json`). Post-anchor-only monthly coverage yields
~8 tenor-days/name/month; the realized-calibration + walk-forward warm-up consume all of it.

Making the frozen gate computable on clean data would need densified backdated anchors (legitimate
— selection uses only anchor-date information — but ~3 calls per anchor ≈ **~18K calls ≈ 3+ weeks
of nightly budget**), past the 2026-08-15 death date, to re-ask a question that has now been
answered negative twice (pilot; counterfactual) — and whose blindness is already broken by the
mandated audit. That is bad arithmetic.

## 6. Verdict-map application (frozen text, applied verbatim)

- The unblinded h=1 result is **VOID as a Stage-3 verdict** (critical protocol deviation).
- At the frozen horizon the evidence is **negative** (counterfactual CI includes 0 with negative
  point and both halves negative; pilot replicated). Per the frozen map, Stage-3 fail →
  **"family closure recommended"** — the single-name RV-vs-IV 30d-tenor family, including the
  queued **VRP-ladder (doc-293 family #3), which does NOT unqueue**. The operator ratifies closure;
  until then nothing is built on this family.
- The h=1 residual (vocabulary-carried next-day vol skill on mid-liquidity names) is recorded as
  **disclosed-not-certified**: wrong horizon, IV-noise confound (clean-IV half n.s.), H2 decay, and
  doc-290 already established predictable-vol-scale is unmonetizable at the frozen cap. The live
  **forward RV ledger (n≥60) remains the standing arbiter** of any next-day-vol claim, at $0.

## 7. Method lessons (each is now a standing rule)

1. **A prereg target must be executable, not prose.** The frozen spec said h=21 in words; the
   runner said h=1 in code; the hash covered the words. New rule: every armed gate ships with a
   synthetic fixture whose known answer the runner must reproduce (target definition included)
   before the power gate may be consulted — the doc-274 "reproduce-from-stated-features" rule,
   extended to instruments.
2. **Selection provenance is part of the data.** Any derived row must carry (or be traceable to)
   the info-set date of every selection step that produced it. The anchor guard implements this
   for the IV warehouse.
3. **Knife-edge disclosures are mandatory.** A power/accept statistic within noise of its threshold
   gets a seed-sensitivity table in the unblind record, always.
4. **The §A fleet rule earns its cost.** Three skeptics, eleven minutes, ~303K tokens — versus a
   false "first certified edge" entering TARGET.md and everything downstream of it. Cheap.

## 8. Ledger and distance updates

- ATTEMPTS_LEDGER: single-name RV-vs-IV @30d tenor (Stage 3 + VRP ladder) moved to
  CLOSED-recommended (The operator ratifies); h=1 residual recorded as disclosed-not-certified.
  Multiplicity: this unblind consumed the family's one shot; the skeptics' extra stress tests are
  counted as disclosed post-hoc, not as new families.
- TARGET.md distance statement: unchanged at **certified edges: none**. The doc-291 "RV-forecast
  advantage" row now points here for its Stage-3 disposition.

## 9. Live state + Pierce items

| gate / instrument | state |
|---|---|
| Stage-3 (h=21, frozen) | VOID pass; negative counterfactual; NOT-COMPUTABLE post-purge; **closure recommended** |
| SEVP kill test | BLIND, 205/300 two-leg events (post-purge), death 2026-09-01 — feeding continues |
| Forward RV ledger | live, forward-only, n≥60 bar — untouched |
| Rocket-gate forward ledger | live, prereg frozen `7f003f6` — untouched |
| Kalshi shadow | live 18:00 task — untouched |

Pierce (nothing here is mine to decide):
1. **Ratify vol-door family closure** (recommended), or direct densified recollection past the
   death date with the blindness taint on record (not recommended; §5 arithmetic).
2. **Register the nightly IV-collector task** (one-liner in `scripts/iv_collector_nightly.cmd`) —
   still the binding constraint on SEVP reaching 300 by 2026-09-01 (~2 nightly runs of calls
   remain at the doc-295 rate).
3. Elevated `_doc288_apply_catchup_triggers.ps1` — sixth session pending.

## 10. The honest sentence

The program's first gate pass died under its own protocol in eleven minutes, for the most
instructive possible reason: the instrument silently asked an easier question than the one frozen.
The discipline held — the pass was never reported as anything but PROVISIONAL, the fleet was
mandatory, and the door closes on evidence, not fatigue. Distance to 0.5%/day: unshrunk, and
honestly so.
