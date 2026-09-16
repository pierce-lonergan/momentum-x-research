# 290 — THE MICRO-REGIME CAMPAIGN: the vector-DB gate, the H-LOCAL verdict, and the honest road to 28%/yr

**Author**: Claude (Fable 5, ultracode; frozen prereg `167a911` sha256 `47b0aa95…c868dd` committed BEFORE execution; adversarial design review + 3-skeptic verification fleet) | **Date**: 2026-07-11 | **Class**: PRE-REGISTERED CAPACITY EXPERIMENT (Stage A go/no-go for the micro-strategy vector-DB architecture) + the doc-289 crack CLOSED + the 28%/yr ceiling decomposition | **Mandate (Pierce)**: "detect micro-trends… learn a micro-strategy for each, store in a vector DB, retrieve live — ~2000 micro-trends over ~6000 events — break the 0.1%/day ceiling… go with what you think is possible."

> **First line, per the denominator-honesty rule: STAGE A FAILS its frozen gates — the vector-DB / per-micro-trend strategy-library architecture is dead for this universe.** And the adversarial verification sharpened the verdict into three clean clauses: the predictable structure in this data is **(1) real** (rank-ρ≈0.30, survives every attack — leakage, ticker-exclusion, seeds, regimes), **(2) global, not local** (the headline kNN-vs-GBM lift of +0.136 was a weak-baseline artifact — the same GBM on rank-transformed y scores 0.29–0.31, indistinguishable from kNN; true local lift ≈ 0.00–0.03), and **(3) volatility, not direction** (the score that orders which names will *move* — peak 3.7% → 30.9% across deciles — orders their stop-out rate in lockstep, 5.7% → 46.6%; its correlation with the fillable outcome is **negative**, ρ=−0.098). There are no 2000 micro-trends; there is one smooth global volatility surface. Separately, Workstream B closes the doc-289 crack: **0/32 pre-declared attention-coupling tests survive correction** — the attention field couples to price in no regime we can name.

## §1 — The trap, avoided by design

2000 patterns over 6000 events is n≈3 per pattern; nothing is estimable at n≈3, and fitting per-pattern strategies at that granularity manufactures false discoveries at industrial scale. The salvageable core of the idea is mathematically a **nonparametric conditional policy** — k-NN retrieval in feature space — so the campaign tested the one falsifiable primitive first: **H-LOCAL** (is the conditional payoff structure locally organized beyond what a strong global model captures?). Capacity first; infrastructure only if earned. Prereg (gates, features, models, nulls, multiplicity) frozen and committed at `167a911` **before** any statistic was computed.

## §2 — Stage 0: the event universe (built, no claims)

One event per name-session: t0 = first morning snapshot, observation window [t0, t0+15min) buys path-*shape* features (the "micro-trend signature"), decision at t0+15, outcomes strictly after, from the minute-bar warehouse (snapshot prices are stale 57% — never an outcome source). **875 events / 51 sessions / 39 features** (absolute + doc-289 attention-field + path shape). ICC(peak_run60)=0.0026 → effective N≈840 by session; the design review adds ticker-level ICC 0.158 (rank) / 0.929 (raw, tail-driven) → effective N as low as ~512 for mean-based statistics — reported, not hidden. Warehouse ET labels verified correct (391-bar RTH sessions, DST-clean) — the trades_v1 `ts_et` trap does **not** afflict minute_aggs.

## §3 — Stage A: the H-LOCAL verdict (frozen gates)

Grouped-5-fold-by-session CV; global = HistGB; local = kNN (inner-CV k) and GBM+kNN-residual hybrid; B=200 within-session permutation null; frozen α=0.005 (Bonferroni ×2).

| | global GBM | kNN | hybrid |
|---|---|---|---|
| OOS Spearman vs peak_run60 | 0.175 | **0.311** | 0.180 |
| top-decile net ret_close (G3 metric) | −2.65% | **−1.13%** | −2.59% |

- **G1 (local beats global): FAIL — by exactly one permutation draw** (Δ=+0.136, p=0.00995 vs frozen α=0.005). *Erratum disclosed: the prereg parenthetical "0/200 or 1/200" mis-stated its own arithmetic — only 0/200 passes; the implementation used the strict correct reading.* **The verification fleet then dissolved the near-miss entirely (artifact=TRUE, HIGH confidence): the Δ was measured against a WEAK baseline.** Squared-error GBM on a heavy-tailed target (p99 = 21 MADs) under-performs on a *rank* metric; the identical GBM on rank-transformed y scores **0.288–0.307 across seeds — indistinguishable from kNN's 0.31–0.33**. The true local-beyond-global lift is **~0.00–0.03**. (The raw-y baseline was also seed-unstable: 0.175/0.216/0.173 — the point Δ was taken against a low draw.) H-LOCAL is refuted at the effect-size level, not just the gate level.
- **G2 (cross-regime): PASS** in sign for the kNN Δ (+0.060/+0.055) — moot given the Δ itself is a baseline artifact.
- **G3 (money): FAIL, decisively.** Top-decile net = **−1.13%**, session-blocked CI [−8.3%, +6.7%]. **STAGE A FAILS under every reading of G1.** Stages B/C/D (clustering, per-regime policies, the vector DB) do not execute, per prereg.

**The characterization — what the local structure *is*:**

| kNN score decile | mean peak_run60 | net ret_close | stop-10% rate |
|---|---|---|---|
| 1 (lowest) | +3.7% | −0.8% | 5.7% |
| 5 | +7.0% | +0.4% | 13.8% |
| 8 | +12.5% | +9.9% | 28.4% |
| 10 (highest) | **+30.9%** | +2.9% | **46.6%** |

The score orders *movement magnitude* monotonically — and orders *blow-up risk* in lockstep. Spearman(score, ret_close) = **−0.098**. **The retrievable structure is volatility, not direction.** A retrieval system would return "names like this one moved a lot" — true, real, and unmonetizable long (you cannot capture a peak you can't exit at, and the same names collapse), unharvestable via options (none exist on these microcaps; doc-283's parity wall). The skeptic's decomposition adds nuance: part of the rank signal is *per-ticker persistent volatility scale* (a ticker-LOO-mean predictor alone scores 0.229 on repeat tickers — legitimate cross-session information, "this name is a mover," not micro-trend geometry), yet the structure holds at 0.313 on single-appearance tickers too — the volatility surface is predictable from features alone.

**Granularity — the honest micro-trend count:** the information-vs-scale curve *rises* monotonically to k≈200 (ρ: 0.234 at k=5 → 0.326 at k=200 → 0.252 at k=500). Rising information with *larger* neighborhoods is itself evidence **against** fine local granularity — big-k kNN approximates a global smooth, which is exactly what the fair-baseline test confirmed. With N_train≈700, at most **~3–4 distinguishable neighborhoods** exist even on the kNN's own terms. Not 2000. There is one smooth surface, not a library of micro-regimes.

**Selectivity (the "one trade per month" answer, empirically):** the curve *inverts* — top-2% picks net **−14.6%** (kNN) / −15.2% (global); top-20% only +1.3–2.7%. The models' highest-conviction picks are their most catastrophic — the doc-289 "obvious names are traps" signature, now measured at every selectivity level. There is no selectivity threshold at which per-trade net approaches the +8.5–42% that low-frequency 28%/yr requires (§6).

## §4 — Adversarial review: the design survived its own audit

An independent design-review agent attacked the prereg + builder before results were interpreted; a 3-skeptic fleet verified the results after. Outcomes:

- **CRITICAL (raised): same-ticker cross-session leakage** — 68% of events are repeat tickers with sticky features; kNN could be memorizing ticker identity, invisibly to the within-session null. **Sensitivity run (disclosed): the concern is empirically absent.** Excluding all same-ticker rows from every neighborhood moves the kNN Spearman by <1% (0.308→0.310 at k=100); same-ticker content of neighborhoods was only ~0.5–1%. The local information is feature geometry, not ticker memory.
- **MAJOR (disclosed): cohort-feature timing** — cohort stats pool other names' snapshots up to 10:30, which lie inside early deciders' outcome windows; ≈Δ-neutral for G1 (both models see it) but contaminates absolute gates in the *optimistic* direction — and those gates **failed anyway**. Every identified bias pointed toward false positives; the frozen result was a FAIL, which the reviewer's own bottom line rates as the believable outcome.
- **MAJOR (disclosed): Workstream B's pm_dvol price basis** is not purely pre-open (snapshot price, stale-prone) — a bias that *manufactures* coupling; WB still found **none** (and measurement error attenuates — the negative is conditional on this noisy attention proxy).
- **MINOR (checked): decision-price staleness** — 0/875 events have an empty back-half observation window; every decision price is ≤7.5 min fresh, most far fresher.

**The 3-skeptic verification fleet (all HIGH confidence): one claim KILLED, two confirmed —**
- *kNN-lift skeptic: artifact=TRUE.* The local-vs-global Δ is a weak-baseline artifact (rank-calibrated GBM ≈ kNN; §3). But every *leakage* kill attempt failed: fold overlap 0; dropping session-constant features *raises* kNN to 0.3145; stable across seeds/10-fold/regime sides; 0.313 on single-appearance tickers; rank-space session ICC 0.033 (cited alongside the raw 0.0026 per the skeptic's correction). The ~0.31 structure is real; its "locality" is not.
- *Gates + inversion*: every point estimate reproduced **byte-identically**; the top-2% inversion hand-verified — 18 events across 13 sessions, zero duplicate tickers, all 18 `ret_close` values reproduced to 1e-9 from an independent warehouse reload; 13/18 negative, median gross **−18.6%** (worse than the mean — not one outlier); every one has a *positive* peak_run60 — pure spike-and-fade. **Open-entry sensitivity** (enter at the next bar's open instead of the last observation close): inversion unchanged (−14.6% → −14.4%; top-20% +1.25% → +1.01%). G1's wording ambiguity confirmed immaterial: G3 fails both prongs under every reading.
- *Cost-floor honesty (skeptic's addition)*: the frozen 1.5% floor is far too **generous** for the sub-$1 names the selectors pick (SABSW $0.04, DSYWW $0.05, HUBC $0.12 — one tick is >25% of price on the smallest); the mildly-positive top-20% cells are optimistic, the negative cells are worse in reality. The FAIL direction only strengthens.
- *Workstream B*: BH unit-tested correct; S1 reimplemented from scratch (exact rho match, fresh seed, B=4000); S2 recounted independently — **0/51 leader-wins is real** (spot-checks: MARA led 4/14 and ranked 11/13; QBTS led 5/22 and ranked 9/18). The design was not rigged-to-fail (min achievable p 0.0005 < the tightest BH threshold; observed min p 0.053 — nowhere near).

## §5 — Workstream B: the doc-289 crack, closed

16 pre-declared cells × 2 statistics = **32 tests** (condensation quartiles, catalyst split, float-rotation extremes, SPY overnight tape, four outcome horizons, the regime split), within-cohort permutation nulls (B=2000), BH within family at Bonferroni-across-families α=0.05/6. **Zero survive.** The premarket-$vol leader is the price winner in ~0% of cohorts in nearly every cell (vs ~6% random — the doc-289 anti-coupling direction, directionally consistent everywhere, significant nowhere after correction). Honest power note: with 13–26 cohorts per cell, only large couplings (|ρ|≳0.3) were detectable — the verdict is "no detectable coupling," not "coupling bounded to zero." **The attention field couples to price in no regime we pre-declared. The crack is closed.**

## §6 — The ceiling math: what 28%/yr actually requires

0.1%/day × 252 ≈ **28.6%/yr**. Decomposed by trade frequency × position size, net of the frozen 1.5% floor:

| frequency | account/trade | @5% sizing needs | @25% sizing | @100% sizing |
|---|---|---|---|---|
| daily (252/yr) | +0.10% | **+2.0% net** (+3.5% gross) | +0.40% | +0.10% |
| weekly (52/yr) | +0.48% | +9.7% | +1.9% | +0.48% |
| monthly (12/yr) | +2.12% | **+42.4%** | +8.5% | +2.1% |
| yearly (1/yr) | +28.6% | +572% | +114% | +28.6% |

Against these requirements the program's measurements: per-ticket edge ≈ 0 or negative (docs 280–287); best selectivity slice this campaign ≈ +2.7% net at top-20% (n=175, unstable, post-hoc); highest-conviction slices *negative*. **At the current 5% cap, no measured configuration reaches the daily requirement; the low-frequency path requires concentration (≥25%) × per-trade nets (+8.5–42%) that no instrument here has ever recorded.** The honest statement: **28%/yr is not available from this universe with these instruments.** What would change the answer: (a) a different universe/instrument class where the doc-283 frontier is higher (liquid options on indices/large-caps, where volatility structure — the one thing we *can* measure locally — is directly harvestable); (b) the rocket-gate forward ledger certifying at n≥30 (collecting, n=3); (c) concentration — a risk decision, not an edge, and on a −EV book it only compounds losses faster.

## §7 — Multiplicity ledger (the denominator of attempts)

Stage A: 2 gate hypotheses (Bonferroni ×2), 7 curve points + 8 selectivity points + 1 decile profile (descriptive). Sensitivities: 3 (ticker-exclusion ×3 k-values). Workstream B: 32 corrected tests. Design-review sensitivities run: 2. **Everything above is every test this campaign ran.** Verdicts survive as stated with all attempts counted.

## §8 — What this closes, and the one true door

**Closed (do not re-propose):** the micro-strategy vector-DB / retrieval architecture on this universe (Stage A fail at both the gate and the effect-size level; one smooth surface, not 2000 micro-regimes); per-name *and* cohort-relational *and* now *local-neighborhood* directional prediction; attention-coupling in any pre-declared regime; low-frequency concentration as an edge substitute.

**The one durable positive:** *predictable volatility scale.* Which names will move — as ranked magnitude of movement — is genuinely forecastable from pre-decision features (rank-ρ≈0.29–0.33, whether by a rank-calibrated global model or kNN — same information), and it survived every attack the fleet threw at it: leakage checks, ticker exclusion, seed/fold/regime stability, single-appearance tickers. It is unmonetizable *here* — the same score that finds the movers finds the blow-ups (direction is absent, ρ(score, fillable outcome) = −0.098), and these names have no options to harvest volatility through. If this program ever moves to an instrument class where **volatility itself is tradable**, this is the first measured, pre-registered, adversarially-survived signal the program has produced. That is the honest road out: **the edge we can measure lives in an observable this universe gives us no instrument to trade.**

**Artifacts** (`data/research/doc290/`): PREREG (frozen, sha256 `47b0aa95…c868dd`), events.jsonl + datasheet, stageA_result.json, sens_ticker_exclusion.json, wsB_coupling_result.json. Scripts: `_doc290_stage0_events.py`, `_doc290_stageA_hlocal.py`, `_doc290_sens_ticker_exclusion.py`, `_doc290_wsB_coupling.py`, `_doc290_PREREG.md`.
