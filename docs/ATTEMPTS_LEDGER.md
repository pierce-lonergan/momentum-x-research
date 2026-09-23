# ATTEMPTS_LEDGER.md — program-level multiplicity ledger

Every hypothesis family this program has gated, its verdict, and its doc — so multiplicity is tracked
across the whole program and closed doors cannot be silently re-litigated. Append-only; a family leaves
CLOSED only by a new pre-registered hypothesis of doc-289/290-class novelty plus Pierce's sign-off.
Established doc 293.

## CLOSED (do not re-propose)

| family | verdict | doc(s) |
|---|---|---|
| Per-name directional prediction (price/momentum, all horizons, all universes) | no edge | 250–261, 283 |
| Exit-timing optimization / AUC→dollars | no deployable edge | 230–234 |
| Loss-cap / re-framed exit variants (b1/b2, c2) | no edge | 236, 239 |
| Rocket ex-ante selection (tick/OFI/tape microstructure) | right tail ex-ante random; cross-regime money gate fails | BET#3, 242–248 |
| Deep-set raw-tape screens / RL scalper | no transferable signal | 248, 256–257 |
| Catalyst/news amplification (PEAD Stage A/B, filing text) | gate fails. **UNDERPOWERED, not closed** (doc 301): the primary endpoint is positive in both samples, n≈55-93/tranche, and the LLM's edge over the price-reaction baseline failed OOS. **doc 304 corrections:** (a) the short leg is **not** behind the borrow wall. Doc 260 asserted that wall and never measured it; 80.7% of eligible stage-B names are easy-to-borrow at Alpaca (paper flags), and only 34 of 1,447 stage-B tickers overlap the gapper borrow data behind the doc-284 tombstone. Doc 303 §4b's `STRUCTURALLY_UNAVAILABLE` was wrong. (b) The event anchor has look-ahead: the universe median f10 is −1.74% on the argmax anchor and −0.40% on a knowable first-spike anchor. (c) Listed puts are dominated (median round trip 50% of premium = 264 bps of S, against ~183 bps capture), and an IWM/sector-ETF short cannot capture the leg (favourable ceiling −27.7 to −50 bps gross). **Forward pre-registration drafted (304b), not frozen** **doc 306 — SS0006 forward design (304b) NO-GO, Trial 34 not registered:** P-3 on the true forward definition (11,769 eligible 2025 events; Rule 201 in force 24.5% on 1-minute bars) gives Sharpe-per-bps 0.02407 (−13.8% vs basis A), a non-SSR universe that drifts **−61.9 bps against the short**, and a **26.9 bps** mean ticket cost. 80% power at ω=0.25 needs a 249 bps (24 mo) / 315 bps (12 mo) tranche-minus-universe gap, above doc 260's contaminated 240 bps upper bound. CEREMONIAL (252) / UNDISCRIMINATING (504) at N(0,0.5); skeptic re-derived every number. **And the frozen model id `Qwen3-235B-A22B-Instruct-2507-tput` is no longer served** (absent from Together's model list; 400 'non-serverless' since 2026-07-13), so the draft's instrument no longer exists (304b §3.7); any substitute is a new pre-registration | 259–260, 301, 304, 306 |
| Coiled-catalyst / prior-runner patterns | no edge (clean forward test) | 254 |
| Multi-day candidacy / dilution signal | no edge | 258 |
| Binary-event handicapping (catalyst calendar) | pilot fails feasibility | 261 |
| Blanket exit-posture flip (incl. overnight carry) | null-to-negative; overnight robustly negative | 280 |
| Short door (borrow-priced, full census) | net negative after borrow (magnitude corrected ~−0.6%/ticket, doc 288); TOMBSTONED | 283–284, 288 |
| Cohort-relational selection (within-cohort position) | channel ≈ empty; whisper unbankable | 289 |
| Attention-coupling (all 16 pre-declared regime cells) | 0/32 after correction | 289–290, 292(WB context) |
| H-LOCAL / micro-regime vector-DB retrieval | refuted at gate + effect size; ~4 neighborhoods, one smooth surface | 290 |
| Low-frequency concentration as an edge substitute | arithmetic + risk ruling (doc-290 §6; TARGET.md §4) | 290, TARGET.md |
| Short-tenor (≤9d) RV forecasting vs IV | sign-negative | 292 |
| Passive liquidity provision / maker rebates (~10 hypotheses) | **CLOSED doc 298** — structurally unavailable, not merely unprofitable. Alpaca's 2026Q1 Rule 606: **100% non-directed, 100% internalised to wholesalers, ZERO lit-exchange venues** (incl. 47.6% of options non-marketable limit flow). The 20-mils/share passive rebate is paid to Alpaca Securities LLC, not the customer. The one escape hatch (Elite DMA Cost Plus) is net **−$0.0005 to −$0.0009/share** at every price this account can reach, and its best tier needs 50M sh/mo against a ~5.3M ceiling | 298 |
| Single-name option short-volatility (all defined-risk forms) | **CLOSED doc 298** — the premium is not there and the cost would eat it anyway: measured single-name VRP **−6.5%**. ⚠ **CI corrected doc 304:** [−9.9, −3.1] is the i.i.d. interval over 1,175 windows. K1's own month-clustered interval is **[−19.9, +6.8] on an effective n of ~77**, "not distinguishable from zero in either direction" (doc298/T00033_K1_vrp_rebuild.md:66-71). The closure stands on **cost**, not sign: against an un-winged break-even of +6.0%; round-trip cost 28.6–67.9% of net debit on TSLA/AAPL/NVDA | 298 |
| LETF close-window rebalance-flow harvest | **CLOSED doc 298** — the doc-295 $0 blocker IS genuinely lifted (point-in-time shares-outstanding is served and moves) and the design is **well-powered** (3.3–15.1× MDE, contradicting the expectation of underpowered), but it fails the arithmetic. At the maximum the standing constraints permit (10 families × 5% cap = 50% gross) it needs **R² ≥ 47.6%** of day-demeaned close-window return variance to carry 5 bps/day at the only permitted deployment, against published estimates of **2–3%** — a **16–24×** shortfall. (At the superseded 10 bps/day target the same requirement was a mean of 21.07 bps against a window sd of 15.06 — Sharpe 22.2 — i.e. arithmetically impossible; halving the target made it merely implausible.) Annualised Sharpe required **11.67** (resid sd) / **25.82** (portfolio sd) against perfect-predictor ceilings of **1.27–1.80** / **3.10–4.41** — **5.8–9.2× short with denominators matched**. **VOID doc 303**; trial NOT registered. ⚠ This row previously read *“Ceiling 6.0–8.6% of requirement vs the ≥10% filter”* — the ground doc-298 M4 (2026-07-29) killed and asked to have replaced. The amendment sat unapplied for two months and directly caused the doc-299 revival. Cost is **not** the binding ground: measured all-in round trip is **0.575 bps** (primary tier, incl. SEC/TAF/CAT), 25% of the 2.268 bps gross ceiling | 295, 298, 303 |
| Overnight/intraday ETF decomposition (hold index ETFs close→open) | **CLOSED doc 297** — replication SUCCEEDED (SPY 6.13 bps gross, robust to exit timing 09:30–10:30, three independent constructions agree) but the sleeve is not a strategy: 1.566 of 4.769 net bps is T-bill interest (double-counts cash carry), excess-of-cash 1.03 bps/day with CI spanning zero, 46–59% of gross is beta×bull-sample, **buy-and-hold beats it on Sharpe 4/4 in 2025+**, effective independent bets 1.50. Certifying it beats a T-bill needs 3,079 sessions (12.2 yr); certifying 10 bps/day is impossible at any n **doc 306:**the live queue entry `overnight_etf_excess_of_cash` is retired on this row's evidence. The planner still rates it ADMISSIBLE (p_cert 0.0734, informativeness 2.469 at N(0, 0.5); threshold sd 0.289), but that measures information value and is not a licence. `minute_aggs` now holds 2,693 SPY sessions (2,692 close→open pairs, 2016-01-04 → 2026-09-21), still short of the 3,079 needed. ⚠ Stepping stone **SS0020** is still `refuted_by_arithmetic` and keyed on `lower_requirement`, so `epistemics.py revive` ranks it **#1 FULLY UNBLOCKED** today. It must be re-keyed to the grounds that do not depend on the bar (double-counted carry, buy-and-hold dominance 4/4, beta share), or it will be proposed again. That is doc 303's root cause **Applied doc 306:** SS0020 (and the LETF stone SS0019) re-keyed off `lower_requirement`, and `lower_requirement` itself moved out of `available_keystones` as consumed on 2026-07-29 (TARGET.md §0: zero of 35 families changed status at the halved bar). `revive` now ranks no stone FULLY UNBLOCKED. | 297, 306 |
| Low-float gapper long universe (the bot's own book) | **SEARCH CLOSED doc 297** — measured −2.041%/ticket, day-blocked CI [−2.823, −1.226], n=4,851 over 586 sessions, negative in 2024, 2025 **and** 2026 separately, 64.8% of sessions negative, robust to trimming. Round-trip cost 61.3 bps (Roll) against a 25–58 bps/ticket requirement. The most durable effect the program owns, pointing the wrong way | 250–261, 297 |
| Single-name RV-vs-IV @30d tenor (Stage 3 + conditional VRP ladder) | CLOSURE RECOMMENDED, awaiting Pierce: unblinded "pass" VOID (runner evaluated h=1 where the prereg froze h=21 — fleet-caught); at the frozen horizon the same panel is −4.43% CI incl 0, replicating the 292 pilot; untainted certification no longer possible; h=1 residual disclosed-not-certified (IV-noise confound; clean-IV half n.s.) | 294, 296 |

## OPEN — gated, collecting, or pending Pierce

| family | state | bar | doc |
|---|---|---|---|
| Volatility door: RV forecasting → options | Stage-3 unblind VOID at h=1 / negative at frozen h=21 → moved to CLOSED-recommended (see CLOSED table). The forward RV shadow-ledger and the HELD Stage-1 proposal were the last live pieces of this door, and **both were RETIRED in doc 306. Nothing from this door is live** | closes with CLOSED-table entry on Pierce's ratification | 291, 292, 296, 306 |
| Rocket-gate conditional (rvol>100 × hour-9 hold), trial **T00027** | ⛔ **CLOSED, `REFUTED_BY_NATURE` (doc 305).** The frozen acceptance test is NOT PASSED at n=36 (≥30): mean −$2,032/session, CI [−$11,084, +$7,582], halves +$60,053 / −$133,205, ≈ −69 bps/filled ticket. The forward CI excludes the mined +$21,912/session. The result was replicated from the frozen prose and 5 sessions re-priced from bars to the cent. Frozen status FAILING-SO-FAR; closed as a futility stop on Pierce's direction; nightly append stopped. This row had read "n=3/30" since July **doc 306 scope note (verdict unchanged):** the forward sample never re-tested the mined population. The configured tier-1 LLM answered 0% of calls in 34 of 36 forward sessions (16 on substitute models 07-13..08-10, **18 with no LLM at all from 08-11**, Together credit exhausted), and the H1/H2 split (+$60,053 / −$133,205) falls exactly on the LLM-dead onset. LLM-alive sessions alone also fail criterion 1 (CI [−$8,071, +$17,431]); alive-vs-dead difference p=0.276. NOT PASSED stands; the refutation is scoped to the selector as it actually ran | NOT PASSED → CLOSED | 284, 305, 306 |
| Kalshi zero-capital shadow (trial **T00028**) | ⛔ **CLOSED, `REFUTED_BY_NATURE` (doc 305).** Its frozen gate FAILED at n=237 and at every re-score to n=793. Brier LLM 0.2451 vs market 0.1101 (gap +0.135, day-blocked CI [+0.114, +0.158]); fee-adjusted P&L −$22.58 over 631 trades. This row had read "collecting (~2/200 resolved)" since July. Collector decommissioned (task disabled) **doc 306 check (verdict unchanged):** a constant 0.5 forecast scores Brier 0.25, so the 0.2451 was audited for silent defaults. None exist: rows are written only on a parsed HTTP 200, 781/796 match the run log, and token counts are stable; collection simply stopped on 08-11 when Together credit ran out. **Scope:** no-retrieval, single-call gpt-oss-120b once a day vs the Kalshi mid (resolution 0.0066 vs 0.0954); a forecaster with current-information access is what would reopen it | gate FAILED → CLOSED | 284, 305, 306 |
| RV forward shadow-ledger (doc-291 confirmation), trial **T00030** | ⛔ **RETIRED, `ABANDONED` (doc 306).** CEREMONIAL under the default prior N(0, 0.5) (EIG 0.0074 nats, p_cert 0.0307, informativeness 1.032) and under its declared N(0.2, 0.5) (p_cert 0.0342, informativeness 1.150). It becomes ADMISSIBLE only at prior sd ≥ 1.964 (mean 0) or ≥ 1.674 (mean 0.2), or at n_eff ≥ 927 sessions under N(0, 0.5). Its frozen gate was never computed and no outcome field was read: it is still blind, at **47/60** forward sessions (2026-07-13 → 09-17). A pass would change no decision (its own prereg says so), because every route from an RV forecast to money is closed or retired. Two instrument defects were found in passing. **2026-08-28 is missing from `minute_aggs`**, so it was never scored. And the 2016–2023 backfill landed inside `build_panel()`'s glob, which has no date floor, on 2026-09-21, so **4 forward rows were scored on a different training panel** from the other 43 (inferred from file mtimes and code, not re-run). **Nightly append stopped** (`post_close_scorecard.py` `_RV_FORWARD_CLOSED`); config-truth recon reports it CLOSED, not DARK. This row had read "LIVE, PENDING-COLLECTION" since July | n≥60, CI>0, ≥2%; review-dead at 120 → RETIRED unread at n=47 | 292, 306 |
| In-universe vol-score risk-shaping (veto/inverse-sizing), trial **T00021** | ⛔ **RETIRED, `ABANDONED` (doc 306); the HELD proposal is declined.** UNDISCRIMINATING under N(0, 0.5): informativeness **1.467** against the 1.5 floor, EIG 0.0973. CEREMONIAL at its declared N(0, 0.3). ⚠ **The verdict is fragile:** it turns ADMISSIBLE at prior sd ≥ 0.517, or at icc ≤ 0.180 (declared 0.20). The retirement does not depend on it. The overlay's only book is the gapper universe, which is SEARCH CLOSED (−2.041%/ticket) and quarantined in observe mode. Sizing is sign-preserving, and both passing uses lowered the point mean (U1 −0.87%/event, CI [−3.05%, +0.83%]; U2 −0.82%/dollar, CI [−2.92%, +0.81%]). It is a drawdown tool, not an edge | Pierce's disposition → RETIRED | 291, 306 |

## FILTERED (doc-293 panel — arithmetic or ledger rejection before any build; kill tests never run)

| family | reason | doc |
|---|---|---|
| Turns-compressor (RV-gated passive liquidity provision, intraday capital turns) | validated 4–8% of requirement — fails the ≥10% filter at honest central assumptions | 293 |
| Market-neutral 3σ residual-reversion spreads on liquids | closed-family variant (per-name directional prediction, price-feature, hedged); reopening requires doc-289/290-class novelty + Pierce sign-off | 293 |
| Adversary "NULL-CERT" blanket certificate | rejected as premature (its shape-(b) bound missed the event-vol channel); its Stage-3 dated accept/kill payload salvaged | 293 |
| LETF close-window rebalance-flow harvest | BLOCKED-AT-$0: the mandatory flow-vs-raw-return rank-calibration is unsatisfiable under the constant-AUM proxy (flow ≡ monotone transform of day return); queued behind historical AUM data; no build, no peek | 295 |
| Volatility-scale monetisation (`anomaly_vol_scale_monetisation`; the doc-290 anomaly) | **NO MEASURED ROUTE, doc 304.** The anomaly is a 60-minute cross-sectional ranking of *upside run-up* (`peak_run60`, doc 290:21) on ~$3 gappers, ~73% a known proxy stack (doc 291 D2). ⚠ The earlier "no usable options" was refuted by a skeptic: it came from a 4-ticker probe (COST_TABLE.json:18), while **202 of 496** doc-290 tickers are optionable and **311 of 875** events (35.5%) had contracts within 45 days. The route fails on **cost and horizon** instead. Option round trips on sub-$5 names run a median **~81% of premium** (doc 304 SS0006-options track), and a 60-minute upside run-up is not the |move| an option pays over its holding period. Long single-name options on the optionable subset remain **unmeasured**. Index routes do not need it and are each measured: short index vol is CLOSED (T00033); vol-managed SPY (doc-298 M2) is a drawdown tool, not an edge (IR vs a vol-matched SPY/BIL blend +0.08 full / −0.04 on 2018+; ~2.3 pp/yr CAGR given up unlevered); vol-scaling the gapper book leaves it negative. The delta-hedged index VRP is **unmeasured, not refuted** (a data-procurement question). Queue entry retired; not registered | 290, 291, 298, 304 |

## QUEUED (doc-293 panel — $0 kill tests designed, preregs to freeze next)

| family | validated ceiling | gate |
|---|---|---|
| SEVP scheduled-event vol carry (trial **T00029**) | ⛔ **CLOSED, `INSTRUMENT_LIMITED` (doc 305, Pierce's disposition).** Death date 2026-09-01 passed at 205/300 two-leg events; its IV collector was never scheduled. No gate was ever computed, so nothing is known about the effect. The frozen naked straddle cannot be traded at Alpaca, and the runner conditioned G2 on outcomes. Deadline not slid, nothing re-tuned. The UNRESOLVED report was made in doc 304 | CLOSED | 293, 294, 296, 304, 305 |
| Stage-3 dated accept/kill (10–45d RV-vs-IV, HLN λ + QLIKE + power gate, collector death dates) | CONSUMED (doc 296): unblind VOID (h=1 deviation), frozen-horizon negative | closure recommended |
| Conditional VRP ladder (21–30d delta-hedged straddles, RV-gated) | ~13% now; ~44% conditional on Stage-3 pass | did NOT unqueue (Stage-3 pass void); closes with the family on Pierce's ratification (296) |
| LETF close-window rebalance-flow harvest | 12.5% ceiling, central <5% | QUEUE optional; flow-vs-raw-return rank-calibration mandatory |

## Method verdicts (not edge families; binding on process)

Recursive self-audit does not converge (276); cross-model/adversarial peer review is the gold standard
(276–277); per-experiment p<.05 is dead as a promote criterion (275); universe-membership audits precede
inference (273); denominator honesty (278); rank-calibrate every baseline (290); raw-IV comparisons are
flattery (292); `trades_v1.ts_et` is poisoned — CI-guarded (288); a prereg target must be EXECUTABLE —
every armed gate ships a synthetic fixture the runner must reproduce (target definition included) before
the power gate may be consulted, and any pass is PROVISIONAL until the mandatory skeptic fleet clears it
(296); derived data rows carry the info-set date of every selection step that produced them (296).

**Doc 297 additions (multiplicity is now machinery, not prose):**
- `account daily return ≡ deployment × turns × net-per-ticket`. Deployment and turns sit on the
  REQUIREMENT side only — sign-preserving multipliers that cannot rescue a negative edge. No breadth or
  deployment increase until a per-ticket net edge has a CI excluding zero.
- **Un-filtered ≠ reopened.** Removing an arithmetic pre-filter is permission to run a $0 kill test. A
  family with no n, no CI and no out-of-sample has no %-of-requirement and gets no bps/day in any ledger.
- **A family is admissible only if its gross per-ticket edge exceeds its round-trip cost** in the
  universe it would actually trade. Cost is measured with an intraday estimator (Roll), not a post-close
  NBBO snapshot — the latter overstated by 3.5× in this very session.
- **The trial counter is now machine-readable** (`scripts/trial_registry.py`): every hypothesis
  registers before data is touched and the promotion bar is computed FROM the count. At ~60 trials on
  1 year of data the null-expected best Sharpe is **2.35**. A loop may propose, freeze, collect and
  report; `promote()` refuses by design.
- **Certification cost scales as n ∝ (σ/Δ)² against a fixed absolute bar.** More names per day helps;
  more deployment does not. The two were conflated by four agents in doc 297.

**Doc 299 additions (closures are now typed, and experiments are priced before they run):**
- **Every closure names its CLASS, not just a verdict.** Three classes are evidence about the market
  (`REFUTED_BY_NATURE`, `REFUTED_BY_COST`, `STRUCTURALLY_UNAVAILABLE`); five are evidence about us
  (`REFUTED_BY_ARITHMETIC`, `UNDERPOWERED`, `INSTRUMENT_LIMITED`, `VOIDED_BY_DEFECT`, `ABANDONED`).
  A closure in the second group **must name the keystone it was missing**, or it is filed as
  `ABANDONED` — honestly, rather than dressed up as a refutation. `src/epistemics/closure.py`.
- **`REFUTED_BY_NATURE` requires a recorded effect and interval.** Without them the honest class is
  `UNDERPOWERED`. On transcription, **13 of 25 historical closures fail this** — the measurements exist
  in the documents but not in the ledger, so those families cannot be retro-scored without re-reading
  prose. Every future closure ships its measurement.
- **Discrimination of the existing graveyard: 17/25 (68%) are measurements of the market.** Mean
  evidential weight 0.736; only 6 are mechanically revivable. This *strengthens* the no-edge thesis —
  the negative results are not an artifact of poor instruments.
- **Retro-validate on every capability landing, by query and not from memory.**
  `scripts/epistemics.py revive --have <keystone>`. Market-evidence closures do NOT revive on
  capability; only on an argued market-structure change, which is Pierce's call.
- **Price a trial before registering it.** `src/epistemics/eig.py` computes expected information gain,
  severity (would this test have failed if the hypothesis were false?), and the **multiplicity toll** —
  the bar increment this trial levies on every other hypothesis in the queue. A `CEREMONIAL` or
  `UNDISCRIMINATING` design must not be registered: it cannot teach and it raises the bar.
- **More data does not lower the FALSE-POSITIVE rate; fewer looks do.** Under a tempered likelihood
  `bar/σₑ = √ω·(E[max]/se + 1.6449)` exactly, with `n` cancelling — so `p_false_positive` is identical
  at every sample length (0.029342 at ω=0.25, 34 trials, at *any* n and *any* prior). More data lowers
  the bar, which is a separate and real benefit.
  ⚠ **CORRECTED doc 300.** This entry originally attributed the invariance to `p_cert`. It does not
  hold there: `p_cert` also carries the prior through `√(σ₀²+σₑ²)`, and at the module's default
  `prior_sd=0.5` it runs 0.0333 → 0.2413 from one year to a hundred, a factor of 7.2. The invariance
  belongs to `p_false_positive`; the 2.9% figure was real but misattributed. The single test certifying
  it used `prior_sd=0.001`, 500× below the default — doc 290's weak-baseline failure, committed against
  the program's own machinery.
- ⛔ **RETRACTED doc 303 — Revived (conditionally): LETF close-window rebalance-flow harvest.**
  **This revival is withdrawn.** It rested on the ledger row's *stated* ground (the ≥10% filter), which doc-298 M4 had already adjudicated as the **non-binding** one on 2026-07-29, recommending this very row be rewritten. The rescale arithmetic below is correct; the ground it was applied to was obsolete. The binding objection (R² 47.6% required vs 2–3% published) is bar-independent and survives. Family is **VOID**, closure `REFUTED_BY_ARITHMETIC`, **Trial 34 not consumed, registry stays at 33**. Original text follows.
- ~~**Revived (conditionally): LETF close-window rebalance-flow harvest.**~~ At the 5 bps/day target its
  ceiling is 12.0–17.2% of requirement and clears the ≥10% filter it was killed by (6.0–8.6% at
  0.1%/day). Ceiling-implied Sharpe 1.33–1.91. **Still blocked**: the operative bar on the intraday
  sample that exists is 2.319 (`minute_aggs` = **666 sessions, measured**, 2024-01-16→2026-09-17).
  Extending `minute_aggs` to 2016 gives ~2,690 sessions and drops the bar to 1.154. That extension is
  the program's top keystone, and it is **entitled and cheap**: flat files reach back to 2016-01-05 and
  the gap is ~38 GB at the probed 18.7 MB/day (the puller's docstring says 65 MB/day — 3.5× too high).
  ⚠ **doc 300**: the 2× requirement rescale this revival rests on is under-determined — it assumes the
  closure was evaluated at 0.1%/day. The procurement conclusion is bar-driven and survives either way;
  the "clears the ≥10% filter" claim is conditional until the target era is settled.

**Doc 300 addition — the governance stop rule (adopted 2026-09-21 on Pierce's directive):**
- ⛔ **NO NEW EPISTEMICS CODE WITHOUT A CANDIDATE STRATEGY IN FLIGHT.** The testing harness
  (96% coverage, 100% kill on 22 targeted mutations) and the provenance ledger (25/25 rows
  dated from git, `<=` marking bounds) are **COMPLETE. Freeze them.** Doc 276 established
  that recursive self-audit does not converge, and three rounds have now demonstrated it:
  every pass generated meta-observations about the previous pass (whether the
  informativeness floor cancels n_eff, 33-versus-34 trial labels, 2.0-versus-2.08-year
  nomenclature). Past this point the operational cost of auditing the harness exceeds any
  statistical edge it could protect. **An immune system does not generate metabolic
  energy.** The bottleneck is the alpha pipeline, and it gets the attention.
- ⚠ **The corollary, which is the harder half.** "Take risk" is not the same as "deploy on
  a measured-negative edge." Under the doc-297 identity deployment and turns are
  sign-preserving multipliers on the requirement side, and this book's per-ticket return is
  measured at **−2.041%, CI [−2.823, −1.226], negative in 2024, 2025 and 2026 separately**.
  Raising deployment against that is not courage, it is arithmetic pointed the wrong way.
  Freezing the harness means spending effort on MEASURING candidates, not on sizing up
  un-measured ones.
- **`risk_aversion_lambda` — what the provenance finding does and does not license.** The
  CE=0.541 / F1=0.667 / P=0.500 / R=1.000 figures behind the 0.15→0.25 hike have **no
  artifact** (`data/llm_arena/` is empty; they appear only in a `config/settings.py`
  comment). That means they may not be **cited as measured**. It does **not** license
  reverting λ, because `mfcs = weighted_sum − (λ × risk_score)`, so a lower λ raises MFCS,
  passes more candidates, and *increases deployment on the negative edge above*. The
  unsourced number and the parameter's value are separate questions: the first is settled
  (annotated, not citable), the second is a trading decision that needs a measured edge,
  not a provenance audit. Re-running the arena settles both — it regenerates the artifact
  **and** produces the reliability-versus-resolution decomposition the Jev gate needs.
