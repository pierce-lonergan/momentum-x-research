# 235 — Cross-regime test of the doc-234 selection edge (PRE-REGISTERED)

**Author**: Claude Opus 4.8
**Mandate**: Pierce — "Extend the labeled corpus to 2024-2025 and re-run the doc-234 audit out-of-period.
Pre-register every dial. Pre-specify decision rules. Do NOT tune anything on the new data."

---

## ⛔ PRE-REGISTRATION BLOCK — written 2026-06-02 23:10 EDT, BEFORE any Mode-A/Mode-B test was run.
> This block is binding. Everything below the Stage-0 report was fixed before a single out-of-period
> portfolio number existed. No dial may be changed after seeing results; any deviation is logged as a
> protocol violation in the verdict.

### Locked eligibility gate (identical for ALL years — 2024, 2025, AND a rebuilt 2026)
`gap% ≥ 8%  AND  open ∈ [$0.50, $20]  AND  trailing-20d ADV ≥ $1M` — all three components are **ex-ante /
no-look-ahead** (gap & price known at the open; ADV uses the prior 20 days only).
- **Declared deviation from the live scanner (binding caveat):** the live 2026 scanner gated on
  *premarket-RVOL ≥ 2.5*. Premarket-RVOL is not cheaply replicable across the full 2024-25 historical
  universe, so it is **replaced by the no-look-ahead trailing-20d ADV ≥ $1M liquidity filter**, applied
  *identically across all years*. Consequence: the cross-regime pools differ from the doc-234 live-journal
  2026 pool (which was also broader: it logged all multi-phase evaluations). **2026 is therefore REBUILT
  under this same clean gate** so the 3-year comparison is apples-to-apples. The doc-234 result remains the
  live-pool reference; this test asks whether the *edge* survives under a consistent cross-period gate.

### Locked feature set (microstructure-only, 13 features, all minute-bar-derived)
`minute_idx, ret_session, ret_5m, ret_15m, ret_30m, vwap_dist, high_dist, low_dist, range_pos, rvol_cum,
vol_accel_5m, realized_vol_15m, up_min_frac_15m`.
**Dropped** `gap_pct, rvol_entry, mfcs` (live-eval-only; not computable for 2024-25). The 2026 CPCV AUC
will be re-confirmed on this reduced set; if it materially collapses, that is reported as a finding (the
doc-234 leakage audit showed the dropped features were minor; expected ≈ unchanged).

### Locked test dials (copied from doc-234's CI-significant configs)
- Concentration: **top-1 AND top-2** (both reported).
- Hold horizon: **30 minutes from entry = PRIMARY** (the trained ±5% barrier). EOD = secondary only.
- ADV floor: trailing-20d **≥ $1M** (nominal) + the per-period **percentile equivalent** reported.
- Upside cap **+40%**, slippage **2.0×** bucketed, price floor **$1**, **warrants excluded**.
- Entry: decision point #3 (~9:50), AFTER the early_p window (no look-ahead). Equal-weight within top-N.
- Random baselines: **1000-seed** per-day eligible-pool draw (Exp-1 method) **AND** broad sub-$50 universe
  true-null (Exp-4 method).
- Bootstrap: **paired-day, 10,000 resamples, 95% CI** on the per-day edge (signal-top-N − pool-mean).

### Two modes (run both; they answer different questions)
- **Mode A — FROZEN-MODEL temporal generalization:** train the micro-only classifier on the 2026 (clean-gate)
  rows, FREEZE, score 2024-25. Answers: does the trained model transfer across regimes (concept drift)?
- **Mode B — REFIT-WITH-CPCV method generalization:** CPCV across 2024-26 (day-grouped, 1-day embargo, OOS
  predictions only). Answers: does the METHOD extract edge across regimes given multi-regime data?

### PRIMARY ENDPOINT (decision-relevant, pre-specified)
**Pooled 2024+2025, top-1 AND top-2, ADV ≥ $1M, 30-min horizon, per-day edge bootstrap 95% CI,
Bonferroni-corrected** across the test family. Everything else (EOD, sweeps, per-month, Mode-A-vs-B) is
**supporting**, reported pre- and post-correction.

### Decision rules (pre-specified — no post-hoc reframing)
- **SURVIVES:** bootstrap CI excludes 0 in pooled 2024+2025, in 2024 alone, AND in 2025 alone; effect
  direction consistent with 2026.
- **SEASONAL / ARTIFACT:** CI includes 0 in pooled out-of-period, OR direction reverses, OR edge appears in
  < 30% of months.
- **AMBIGUOUS:** pooled excludes 0 but per-year does not — needs more data.

### Multiple-comparisons control
The family = {2 concentrations × 2 horizons × ~5 ADV levels × {2024, 2025, pooled} × 2 modes}. Bonferroni
(and Holm) correction applied to the aggregate verdict; the PRIMARY endpoint above is the single
decision-relevant test, pre-declared, so it is not subject to the full-family penalty — supporting tests are.

### Power
Out-of-period N ≈ **9,371 eligible ticker-days over ~500 sessions** (2024: 3,776; 2025: 5,595) vs 2026's 35
sessions. Per-day-edge CI half-width scales ≈ 1/√(n_days); ~500 vs 35 → CIs ≈ **3.8× tighter**. Given
doc-234 effect sizes (+1–2% per-trade), the detectable effect at 80% power is ≈ **+0.5% per-day edge** —
adequately powered to discriminate SURVIVES vs SEASONAL. (top-1 has fewer effective trades/day → wider; both
top-1 and top-2 reported.)

### The five failure modes (Pierce's) — and how this design forecloses each
1. **Survivorship** → Stage-0 verified point-in-time (23% of 2024 tickers gone by 2026; MULN's delisting
   retained). PASS.
2. **Dial-tuning on new data** → all dials locked above before any OOP number existed; this block is the
   contract.
3. **ADV/threshold drift across regimes** → ADV floor reported BOTH nominal ($1M) AND per-period percentile.
4. **Frozen vs method generalization conflation** → Mode A and Mode B run separately and reported side-by-side.
5. **Multiple-comparisons inflation** → Bonferroni/Holm on the family; single pre-declared primary endpoint.

---

## Stage 0 — data-integrity report (CLEARED before pre-registration was finalized)
- **Survivorship: PASS.** day_aggs is point-in-time: 2024 = 12,647 tickers, 2026 = 12,910, and **2,883
  (23%) traded in 2024 but are gone by 2026** (delisted/acquired/merged retained). Spot-checks: MULN present
  2024-01→2025-07 then stops (delisting captured); SINT/GFAI/HKD/GNS/COSM (volatile small-caps) retained.
- **Gates: identifiable & locked** (above). Eligible pool under the locked gate — 2024: **3,776** ticker-days
  (1,535 tickers); 2025: **5,595** (2,041); 2026: **1,712** (1,093). (2026 partial: day_aggs starts 2026-03-23.)
- **Corporate actions: PASS.** splits well-covered for our small-cap universe (2024: 495, 2025: 543; MULN 4
  reverse-splits 100:1/60:1/100:1/100:1 with correct ratios, SINT 200:1) — and intraday (entry→30min/EOD)
  labels are **split-safe by construction** (no intraday splits), so no adjustment is needed for the labels.
- **Minute coverage: PASS.** 2024 = 366M bars, 2025 = 427M bars.
- **VERDICT: Stage 0 cleared. Proceed to the pre-registered run.**

---

## Stages 2-5 — RESULTS (run 2026-06-02 23:18 EDT; nothing above this line changed)

Corpus: **632,020 rows, 10,786 ticker-days** (2024: 243 sessions, 2025: 250, 2026: 48). 30-min
continuation base rate stable across regimes (2024: 14.8%, 2025: 14.0%, 2026: 10.4%).

**Model-integrity check (rules out a crippled-model confound):** micro-only 2026 CPCV AUC =
**0.839 ± 0.047** — *higher* than doc-234's full-feature 0.768. Dropping the live-eval features did NOT
weaken the model; microstructure was the real signal. **The model genuinely predicts 30-min continuation
well — the out-of-period failure is NOT a broken model.**

### Stage 2 — the two modes, side by side (PRIMARY endpoint: pooled 2024+25, top-1/2, ADV≥$1M, 30-min)
| test | Mode A (frozen 2026→OOP) | Mode B (CPCV refit 2024-26) |
|---|---|---|
| pooled **top-1** | **−1.61%** CI[−2.52,−0.68] *excl 0* | **−1.03%** CI[−1.99,−0.07] *excl 0* |
| pooled **top-2** | **−1.13%** CI[−1.70,−0.56] *excl 0* | **−1.25%** CI[−1.82,−0.66] *excl 0* |
| 2024 top-2 | −1.22% CI[−1.96,−0.47] *excl 0* | −1.51% CI[−2.27,−0.75] *excl 0* |
| 2025 top-2 | −1.04% CI[−1.88,−0.19] *excl 0* | −0.99% CI[−1.84,−0.14] *excl 0* |
| 2026 clean-gate ref | −1.98% CI[−4.15,+0.07] incl 0 | −1.69% CI[−4.34,+0.78] incl 0 |
| EOD (secondary) | −4.32% CI[−5.31,−3.39] | −4.56% CI[−5.56,−3.57] |
| **Bonferroni 98.75% CI** | top-1 [−2.50,−0.18], top-2 [−1.75,−0.28] — both **excl 0 (neg)** | top-2 [−1.97,−0.50] **excl 0 (neg)** |

**Every primary cell is negative and CI-excludes-zero. The edge does not merely vanish — it REVERSES SIGN.**

### Stage 3 — regime decomposition
- **Per-year:** negative and significant in *both* 2024 and 2025 (above) — not one bad year.
- **Per-month:** Mode A **5/24 (21%)** months positive, Mode B **2/24 (8%)** — far below the pre-registered
  30% floor. No calendar-month or hot-month concentration of a *positive* edge; the negative is pervasive.
- **2026 clean-gate** is itself nominally negative (−1.7 to −2.0%, n=48, incl 0) — so doc-234's positive
  did NOT even reproduce on a clean-gate 2026 pool, meaning it was partly an artifact of the *live-journal
  pool composition* (broader, multi-phase, May-heavy), not just the calendar.

### Stage 4 — robustness
- **ADV sweep** (pooled top-2, 30-min): negative at every level — ADV≥$1M −1.1%, $5M −0.9%, $10M −0.8%,
  $25M −0.2% (incl 0). The edge trends toward zero only as you restrict to the most liquid names (where
  it's just noise); it is never positive. The doc-234 "ADV strengthens it" pattern was 2026-specific.
- **Concentration sweep:** negative at top-1, top-2, AND top-4 in both modes. Not a dilution issue.
- **True null:** broad sub-$20 universe open→close mean −0.06% (≈flat). So the negative edge is specific
  to *top-P selection underperforming random within the gappers*, not a market-wide drag.
- **Bonferroni/Holm:** the primary endpoint survives correction — as a **significant NEGATIVE**.
- **Power:** n≈493 pooled sessions; CIs are tight (half-widths ~0.5–0.9%); the test was well-powered and
  the result is not a power failure.

### Stage 5 — VERDICT
**DECISION-RULE OUTCOME: SEASONAL / ARTIFACT (both modes) — and stronger: SIGN-REVERSING.**
Per the pre-registered rules: direction reverses (negative, not positive) AND <30% of months are positive
AND the per-year/pooled CIs exclude zero on the *wrong side*. The doc-234 selection edge is **not robust**;
it was a **2026- (May-) and live-journal-pool-specific artifact** that does not generalize and in fact
inverts out-of-period.

**The deep finding (the one to remember):** *a model that predicts 30-min continuation excellently
(AUC 0.84) yields a statistically significant NEGATIVE selection edge out-of-sample.* The names most
likely to make a small near-term pop are the extended/exhausted ones that then underperform — so
continuation-prediction is **anti-profitable as a selection signal** in 2024-25. This is the strongest
possible form of "AUC ≠ P&L": here the AUC is real and the trading use is negative. It vindicates the
session's most durable result (doc 198/230): **selection is not the edge; chasing the strongest signal is
backwards.**

### If SEASONAL → regime characterization + live-detectability (per mandate)
- The only regime where the edge was positive is **2026 (esp. May)** in the *live-journal* pool — a window
  where intraday continuation persisted unusually. Clean-gate 2026 already weakens it; 2024-25 inverts it.
- **Live-detectability:** you cannot know ex-ante that you're in a "continuation-persists" regime — it's an
  ex-post label. There is no real-time gate that would have switched the strategy on only in May-2026 and
  off otherwise. **Therefore it is NOT a deployable regime-gated edge.**

### What survives, and what dies
- **DIES:** P(continue) as a *selection/sizing alpha*. Killed out-of-period, pre-registered, sign-reversed,
  CI-significant, both modes. We do not pursue it. (The doc-234 reversal of doc-233 was correct *for 2026*;
  doc-235 shows the 2026 positive was itself the artifact — the honest arc converges on NO durable
  selection edge.)
- **SURVIVES (as a tool, not an alpha):** the model predicts 30-min continuation well (AUC 0.84) — but
  that prediction is not tradeable as selection.
- **STILL OPEN (separate question, now lower-priority):** the **risk-filter reframe** — does vetoing
  predicted-*faders* cut the catastrophic loss tail (doc 230's real wound)? This is a different target
  (loss-tail reduction, not selection mean) and is NOT answered by this test. Given the regime-instability
  of the model's relationship to forward returns, pursue it *only* with the same pre-registered,
  cross-regime, CI rigor — and expect skepticism.
- **DURABLE LEVERS (unchanged):** execution integrity (kill phantom P&L), trade early, flatten at the
  close (never carry overnight), cut catastrophic losers. The 5%/day, if it exists, is here — not in a
  predictive model.

**No live change. Research only. The gating question is resolved: SEASONAL/ARTIFACT — the selection edge
does not survive out-of-period.**

**Initiator**: Claude Opus 4.8 (1M ctx). **Basis**: `scripts/build_cross_regime_corpus.py` +
`scripts/cross_regime_audit_doc235.py` on 10,786 ticker-days across 2024-2026, pre-registered above.
**Predecessors**: 234 (the 2026 edge this tests), 233/232/231, 198/230 (selection-is-not-the-edge, now
confirmed out-of-period).
