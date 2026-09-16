# 289 — THE COHORT FIELD: is the edge relational? A channel-capacity probe of the morning as a whole

**Author**: Claude (Fable 5, ultracode; a 6-lens divergent design panel + a hand-built, pre-registered, permutation-nulled pipeline, wf_f75c8cf3) | **Date**: 2026-07-11 | **Class**: NOVEL EXPERIMENT (never done here) — pre-registered, cross-regime, denominator-honest | **Mandate (Pierce)**: "treat the problem space as the universe and you the curious civilization… theorize and create an experiment that has never been done, coalesce ideas never thought to combine. full send."

> Every experiment in this project's four-month search asked the same question — *"will ticker X continue?"* — one name at a time, and every answer was no. This one asks a question the per-name lens was structurally blind to: **is the winner of a morning decided not by any name's absolute traits but by its POSITION in the cohort** — the ~18 low-floats that gapped together and compete for one finite pool of retail attention? We measured the information capacity of that relational structure directly. **The verdict: the cohort→winner channel is essentially empty (pre-registered test FAILS) — but the probe uncovered a real, beautiful law underneath: the cohort's *attention* field is strongly ordered and forecastable, and it is orthogonal to price. The structure was always there; it just lives in the wrong observable.**

## §1 — The reframe, and why it is new

A per-name model can be near-random on every single name yet still leak enough *ranking* information to pick the cohort's winner — or provably not. No prior run ever conditioned on cohort membership as the unit, represented a name by its within-cohort position, or measured an information quantity over the argmax. When we ran a **6-lens divergent design panel** (information theory, physics, topology, ecology, mechanism-design, adversary), the information-theorist lens *independently proposed almost exactly this experiment* — "a Fano floor on within-cohort selection" — which is strong convergent evidence it is the right probe. The physicist lens proposed an orthogonal one (attention as a conserved, condensing quantity); we ran both.

## §2 — Stage 0: the cohort dataset (leakage-hard, warehouse-truthed)

`scripts/_doc289_build_cohorts.py` → **53 morning cohorts (2026-04-14…07-09), 936 names, median 18/cohort, 99.2% outcome coverage.** Decision time = each name's first morning snapshot (pre-decision state); absolute features read there; the outcome (60-min peak-run, return-to-close) comes from the **authoritative minute-bar warehouse**, not the feature-file `current_price` — which we proved is *stale 57% of the time* (a data-integrity landmine avoided). Relative features = within-cohort z-scores, ranks, centroid distance, and cohort dispersion. Target `is_winner` = argmax peak-run within the cohort (relational by construction). Regime split frozen at 2026-05-26.

## §3 — Stage A: the relational existence test (pre-registered; frozen before the run)

`scripts/_doc289_stageA_relational.py`. Grouped-5-fold-CV-by-session winner-identification (a cohort never spans train/test), a **within-cohort winner-permutation null** (B=500), and the cross-regime gate that has killed every prior edge. Frozen acceptance: G1 predictability exists (perm p<0.01), G2 relative beats absolute (perm p<0.05), G3 survives out-of-regime.

| metric | ABS | REL | BOTH | random |
|---|---|---|---|---|
| winner-ID accuracy | 0.094 | 0.094 | 0.076 | 0.059 |
| pooled AUC (is_winner) | 0.606 | 0.583 | 0.622 | 0.50 |

- **Relational lift (BOTH − ABS) = −0.019** — negative. Relative representation did **not** beat absolute.
- **p_acc = 0.34, p_lift = 0.77** — neither the accuracy nor the lift clears the permutation null.
- **G1 FAIL, G2 FAIL → STAGE A FAILS.** The cohort→winner channel carries ≈ 0 *usable selection bits*: no selector — absolute or relational — identifies the winner better than a within-cohort coin flip.

**The whisper (exploratory characterization, `_doc289_stageA_robustness.py`).** There *is* a faint pulse: pooled AUC ≈ 0.61 with permutation p ≈ 0.02, and the OOS score weakly tracks continuation rank (Spearman ρ = 0.15, p = 3×10⁻⁶). But it is **absolute, not relational**, and its content is counter-intuitive — the top standardized coefficients are an *inverse* relation to `has_news` (−0.41) and to the bot's own composite `mfcs` (−0.54): the names the bot's score likes, and the names *with* a news catalyst, are **less** likely to run. The whisper is "avoid what looks obvious," not "find the rocket."

## §4 — Stage B: can the whisper cash out? (ceiling only — Stage A did not pass)

`scripts/_doc289_stageB_ceiling.py`. Betting the single top-scored name per cohort:

- **Peak-run edge vs a random cohort name = +0.14%** (0.089 vs 0.088) — the score does **not** select the runners. The actual winners average a +76% peak; the model cannot find them.
- **Fillable policy (buy top-scored, hold to close) = −0.89% net** of a conservative 1.5% round-trip cost floor. The whisper's only real content is mild fade-avoidance (+5.6% on the close vs a random name), and it is not enough to pay costs.
- **VERDICT: WHISPER UNBANKABLE.**

## §5 — The orthogonal probe: a conservation + condensation law (`_doc289_condensation.py`)

Treating each cohort as a closed system competing for one pool of attention (proxy = intraday dollar-volume):

| question | result | reading |
|---|---|---|
| **Q1 Conservation** | Spearman(log premarket \$vol, log intraday \$vol) = **0.87** (p=2.5e-17) | the total attention "pie" is largely *set pre-open* |
| **Q2 Condensation** | top-1 name captures **43%** of intraday \$vol (uniform ≈ 6%; max 96%) | massive **winner-take-all** condensation of attention |
| **Q3 Control param** | Spearman(premarket H, intraday H) = **0.66** (p=1e-7) | you can tell *pre-open* whether it will condense |
| **Q4 Selection** | premarket \$vol leader is the price winner **1.9%** of the time (random 6%, p=0.97); yet stays the \$vol leader **60%** of the time | attention persists — **but is decoupled from price** |

**This is the beautiful part.** The cohort's *attention* field is strongly ordered and forecastable — conserved from premarket, condensing winner-take-all onto one name, and that name's identity persists from premarket to intraday. But **the attention condensate is not the price winner** (it is *anti*-selected, 1.9% vs 6%): the most-traded name is the large, liquid, mean-reverting one, not the small illiquid rocket. The one layer of this universe that is genuinely predictable (who captures attention) is nearly orthogonal to the one layer everyone trades (who appreciates in price).

*Honest caveats:* Q1/Q2 are partly mechanical — dollar-volume is heavy-tailed, so a cohort spanning very different liquidities will "condense" onto its largest name somewhat trivially, and the log-log conservation partly reflects shared scale (cohort size, day-wide activity). The load-bearing, non-mechanical result is **Q4's decoupling**, which is permutation-clean.

## §6 — The law this experiment adds

Stack the two probes and a single statement falls out, one the project has never articulated:

> **A gap-up cohort has a rich, forecastable attention geometry, but price returns are a near-orthogonal, near-random projection of it. The predictable structure is real — it just lives in the wrong observable. That is why the price edge has been unfindable: not because the market is featureless, but because its features and its payouts occupy decoupled subspaces.**

This *extends* the negative thesis (docs 250-284) to a genuinely new frame — cohort-relational and information-theoretic — and it does so as a **knowability result**, not another failed fit: we measured the channel (≈0 selection bits), bounded the whisper (unbankable), and localized the one strongly-ordered layer (attention) and its decoupling from price. Consistent with doc-283's "the fee is the fade" and doc-273's knowability frontier, now proven one level deeper.

## §7 — What this earns, and the one crack left open

- **Do not chase the whisper** (AUC 0.61, unbankable) or the attention condensate (anti-selected for price). Both are pre-registered dead ends now.
- **The one open question the probe raises, not answers:** *is there any regime or conditioning under which the attention layer couples to the price layer?* The decoupling is an average over 53 cohorts; if a rare sub-population exists where the condensate *does* run (or reliably fades — a short thesis, though borrow is tombstoned), that is where the only remaining structural edge could hide. A pre-registered conditional-coupling test (does Q4 selection accuracy rise inside any pre-declared regime slice?) is the honest next probe — with the same permutation-null discipline, and the same readiness to write another clean negative.

**Artifacts** (all under `data/research/doc289/`): `PREREG_stageA.md` (frozen), `cohorts.jsonl`, `stage0_report.json`, `stageA_result.json`, `stageA_robustness.json`, `stageB_ceiling.json`, `condensation_result.json`. Scripts: `_doc289_build_cohorts.py`, `_doc289_stageA_relational.py`, `_doc289_stageA_robustness.py`, `_doc289_stageB_ceiling.py`, `_doc289_condensation.py`, `_doc289_design_panel.js`.
