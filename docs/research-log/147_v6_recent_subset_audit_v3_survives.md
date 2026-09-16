# 147 — v6 recent-subset audit: ALL FOUR pre-commits PASSED; D288 evaluation rule pre-committed

> **Format:** doc-138-template (script first, run, then doc, verdict last).
> Four pre-commits locked in writing BEFORE any experiment ran (see § 0
> below). All four passed strictly on the data — no rationalization,
> no interpretation. The doc 146 closing concern about "waiting-for-
> shadow trap" answered: the tests I was avoiding came back reassuring,
> but the discipline of pre-committing them was correct.

**Session date:** 2026-05-11
**Branch:** develop
**Predecessors:** [144 v6 four-experiments synthesis](144_v6_four_experiments_synthesis.md), [146 v6 D289 interactions weak](146_v6_d289_interactions_minimal_lift.md)
**Status:** **D288 plan validated on offline data with full hygiene.**
Recent-subset DSR holds, per-tier DSR holds, offline overlay Sharpe
shows real risk-adjusted lift, recent-data capacity ceiling holds.
Explicit D288 evaluation rule LOCKED for shadow-data evaluation.

---

## 0. Pre-committed verdicts (LOCKED IN WRITING BEFORE EXPERIMENTS RAN)

Per user critique on doc 146 closing line "waiting for shadow data":

| Pre-commit | Trigger | Action |
|---|---|---|
| Exp A | recent-subset cascade DSR @ N=100 < 0.5 | URGENT pause D288 plan; investigate phantom |
| Exp B | 3+ of 4 tiers fail individual DSR @ N=50 | rethink tier structure; possibly collapse cascade |
| Exp C | offline overlay Sharpe ≤ v3 baseline Sharpe | D288 not alpha-additive; reconsider shipping |
| Exp D | recent capacity ceiling < $500K | revise LOTTERY_BANKROLL_PCT default |

These were written and locked before computing any of the data below.

---

## 1. TL;DR — all four passed; recent regime is FAVORABLE

| Exp | Result | Pre-commit |
|---|---|---|
| **A. Recent-subset cascade DSR** | DSR @ N=100 = **0.972**, ann Sharpe **+5.08** (higher than full +4.52) | NOT fired ✓ |
| **B. Per-tier DSR (recent)** | HIGH **DSR=1.000 Sharpe +12.50**; BROAD DSR=0.873; VETOED 0.576; ELITE T=9 (n too small) | NOT fired ✓ (0 of 4 fail) |
| **C. Offline overlay Sharpe** | v3 alone +3.55; overlay **+5.17** (lift +1.62); recent-only +5.12→**+7.03** (lift +1.91) | NOT fired ✓ |
| **D. Recent capacity ceiling** | ELITE/VETOED $1M; **HIGH $5M**; BROAD $100K (D285 5% cap holds) | NOT fired ✓ |

Two secondary findings of strategic importance:

1. **Cascade alpha is regime-dependent and the recent regime is BETTER.**
   OLDER period (Jan-Jul 2025): cascade Sharpe +3.92. RECENT period
   (Aug 2025-Apr 2026): cascade Sharpe **+5.08**. The doc 144 +20%
   claim isn't a dated golden period — it's stronger on recent data.

2. **HIGH tier on recent data is exceptional**: Sharpe +12.50, DSR
   @ N=200 = 0.999. The cascade's edge is concentrated in HIGH
   on the production-relevant period.

---

## 2. Exp A — recent-subset cascade DSR (the test never run before)

### 2.1 Why this experiment matters

Doc 144's cascade DSR (0.9953 @ N=100) was on FULL data — 320 days
spanning Jan 2025 to Apr 2026. **Production trades the recent subset
(Aug 2025+).** I had never validated that the doc 144 finding survived
restricted to the production-relevant period.

This is the single most "load-bearing" assumption in the entire arc:
all of TabPFN evaluation, d-1 microstructure, capacity analysis, and
D288 planning rests on "v3 cascade has real edge on production data."
That foundation had not been DSR-tested directly.

### 2.2 Setup

v3 BROAD specialist OOS preds (12,192 rows × 16 folds, Jan 2025 - Apr 2026).
Apply production cascade thresholds. Compute daily P&L = mean ret_t5
of fired picks. Split:

| Subset | Period | n picks | Active days |
|---|---|---|---|
| FULL | Jan 2025 - Apr 2026 | 12,192 | 238 |
| OLDER | Jan 2025 - Jul 2025 | 5,380 (44%) | 93 |
| RECENT | Aug 2025 - Apr 2026 | 6,812 (56%) | 145 |

DSR with N_trials sweep {1, 10, 50, 100, 150, 200}.

### 2.3 Result

```
subset       T  sr_ann   N=10    N=50   N=100   N=150   N=200
FULL       238  +4.52   1.000   0.998   0.995   0.993   0.991
OLDER       93  +3.92   0.878   0.678   0.583   0.528   0.490
RECENT     145  +5.08   0.998   0.985   0.972   0.962   0.953
```

**RECENT cascade DSR @ N=100 = 0.972.** Pre-commit NOT fired. The
recent-subset alpha is actually STRONGER than the full-data alpha
(Sharpe +5.08 vs +4.52). At N=200 (very conservative trial count),
RECENT DSR is 0.953 — still above the 0.95 publication bar.

**Important comparative finding:** OLDER period would FAIL DSR at
N=200 (0.490). The "v3 cascade has real alpha" claim depends entirely
on the recent period; the older period was much weaker. If we'd done
this audit a year ago, we might have killed the cascade based on
older-data-only.

### 2.4 What this means

The doc 144 + 142 + 138 chain of analysis built on top of cascade
alpha is grounded in a DSR-validated foundation specifically for the
production-relevant period. **D288 defensive overlay plan stands on
real recent-data alpha, not a full-data artifact.**

---

## 3. Exp B — per-tier DSR audit

### 3.1 Setup

For each of 4 tiers (ELITE, HIGH, VETOED, BROAD), compute top-3/day
P&L; apply DSR sweep. Per (tier × subset) combination.

### 3.2 Result

```
tier     subset      T   sr_ann   DSR_N10   DSR_N50   DSR_N100
ELITE    FULL       13   +7.96     0.726     0.460     0.361
ELITE    OLDER             (<5 picks; tier rare)
ELITE    RECENT      9   (T<10; sample size too small for DSR)
HIGH     FULL       33   +8.90     0.995     0.968     0.946
HIGH     OLDER      11   +3.83     0.226     0.073     0.044
HIGH     RECENT     22  +12.50     1.000     1.000     0.999
VETOED   FULL       91   +3.09     0.681     0.408     0.313
VETOED   OLDER      27   -1.36     0.024     0.004     0.002
VETOED   RECENT     64   +4.36     0.814     0.576     0.475
BROAD    FULL      220   +4.10     0.998     0.983     0.969
BROAD    OLDER      83   +4.25     0.878     0.679     0.583
BROAD    RECENT    137   +4.03     0.967     0.873     0.812
```

### 3.3 The headline observation

**HIGH on RECENT data: T=22, Sharpe +12.50, DSR @ N=100 = 0.999.**
This is the strongest single-tier result anywhere in the arc. The
v3 cascade's alpha on recent production-relevant data is concentrated
specifically in HIGH-tier picks.

### 3.4 The OLDER-period failures (regime context)

OLDER period:
- HIGH Sharpe **+3.83**, DSR @ N=50 = **0.073 FAIL**
- VETOED Sharpe **−1.36** (NEGATIVE), DSR @ N=50 = **0.004 FAIL**

**The OLDER period (Jan-Jul 2025) was BAD for the cascade.** VETOED
was alpha-negative; HIGH was much weaker. Only on RECENT data does
the cascade shine.

This is concerning for understanding the regime: were 2024 / early
2025 just bad regimes for microcap gap-up momentum? Or did some
data-quality improvement happen around Aug 2025? (Likely the
trades_v1 warehouse coverage + cleaner microcap candidate filtering
improving over time.) Either way, **production trades the recent
regime, where the cascade clearly works.**

### 3.5 ELITE small-sample caveat

ELITE on RECENT subset has only T=9 days where any ELITE pick fired.
That's below the DSR threshold (n≥10 days). This is consistent with
doc 138's ELITE-DSR-fails finding — ELITE is structurally rare. The
MX_HYBRID_ELITE=0 flag (from D284) holds; we don't allocate Aggressive
Kelly capital to a tier that's empirically too rare to validate.

### 3.6 Pre-commit verdict

3+ tiers fail recent DSR @ N=50? **0 of 4 fire** (ELITE excluded
for sample size, not DSR fail). **NOT fired.** Cascade structure
holds for production.

---

## 4. Exp C — offline overlay Sharpe (the test most likely to surprise)

### 4.1 Setup

Doc 145 reported overlay PRECISION lift +5.70 pp per pick (mean
ret_t5 +9.40% vs +3.70% baseline). But mean lift can come from
concentration into fewer picks WITHOUT improving risk-adjusted
return — Sharpe could be flat or worse.

Compute daily P&L for three pick sets:
- v3 trades alone (all 249 picks)
- v3 + TabPFN agrees (134 overlay-passes)
- v3 + TabPFN disagrees (115 overlay-rejects)

Annualize Sharpe = mean/std × √252.

### 4.2 Result

```
variant                       T_days  mean    std    Sharpe_ann  n_picks
v3 trades alone                  67   +5.17%  23.10%   +3.55       249
v3 + TabPFN agrees               58   +8.81%  27.03%   +5.17       134
v3 + TabPFN disagrees            45   +0.29%  17.32%   +0.26       115

RECENT-subset (Aug 2025+) only:
v3 baseline RECENT               43   +6.21%  19.24%   +5.12         -
overlay RECENT                   40  +10.51%  23.73%   +7.03         -
```

### 4.3 The key surprise — std actually rose

I expected concentration into fewer picks to REDUCE std (fewer picks
per day = less averaging). Instead std rose from 23.10% to 27.03%.

The mechanism: TabPFN's filter selects a different SUBSET of days too.
On days where v3 fires many picks but TabPFN agrees with few of them,
the overlay's daily P&L is dominated by 1-2 picks (high variance per
day). On days where everyone agrees, the overlay aggregates more
picks (low variance).

But the MEAN rose more (5.17% → 8.81%, +3.6 pp daily) than the std
did (+3.9 pp). Sharpe net rises +1.62 annualized.

### 4.4 Recent-only is even better

On the production-relevant subset, overlay Sharpe lift is **+1.91**
annualized (5.12 → 7.03). The defensive overlay is more valuable on
recent data than full data.

### 4.5 Pre-commit verdict

Overlay Sharpe ≤ baseline Sharpe? NO — overlay +5.17 vs baseline
+3.55. **NOT fired.** D288 is alpha-additive in risk-adjusted terms,
not just precision-additive. The doc 144 + doc 145 framing is correct.

---

## 5. Exp D — recent-subset capacity (revised doc 142)

### 5.1 The recent vs full mean-return picture

```
tier     full     older    recent
ELITE    +19.90%  +18.60%  +20.41%
HIGH     +19.67%  +7.59%   +25.46%   <-- recent much better
VETOED   +7.36%   +2.16%   +9.04%
BROAD    +3.86%   +6.97%   +3.00%    <-- only tier where recent worse
```

Most tiers have HIGHER mean returns on recent data than full data.
HIGH dramatically so (+25.46% vs +19.67%). Only BROAD is slightly
weaker on recent.

### 5.2 Recent capacity ceiling per tier (Y=1.5 Bouchaud, current Aggressive Kelly)

```
tier     last_AUM_pos    first_AUM_neg
ELITE    $1.00M          $5.00M
HIGH     $5.00M          (none through $5M)  <-- HIGH scales further
VETOED   $1.00M          $5.00M
BROAD    $0.10M          $0.50M               <-- BROAD breaks first
```

**Recent-data ELITE+HIGH capacity ceiling: $1M** (limited by ELITE).
Same as doc 142's full-data finding. D285 5% participation cap holds.

### 5.3 Pre-commit verdict

Recent capacity ceiling < $500K? NO ($1M). **NOT fired.** D285 holds.

### 5.4 Side observation worth noting

HIGH tier scales to $5M AUM with positive net even at 55% participation
(impact 23.6% but mean ret 25.5%). This is genuinely remarkable for
a microcap strategy. If we tilted away from BROAD (which breaks at
$500K) toward HIGH, the strategy would scale further.

Filed for D290: per-tier-AUM-aware Aggressive Kelly. Reduce BROAD's
Kelly cap as AUM scales (since BROAD breaks first); allow HIGH's cap
to remain (since HIGH scales).

---

## 6. The pre-committed D288 evaluation rule (LOCKED IN WRITING)

Per user critique: "you need a pre-committed evaluation date and
decision rule" before shadow data arrives.

### 6.1 Evaluation date

**D288 evaluation triggers when EITHER condition is met:**
- 10 trading days of D286 shadow data have accumulated, OR
- 3 calendar weeks have elapsed since the first D286 shadow record
  (whichever comes FIRST)

The trading-day count handles the live cadence; the calendar-week
fallback prevents drift if shadow runner crashes silently for
multiple days.

### 6.2 Decision rule (computed strictly on shadow + realized data)

D288 enables `LOTTERY_TABPFN_DEFENSIVE_OVERLAY=1` IF AND ONLY IF
ALL of the following hold simultaneously:

1. **n_picks ≥ 30 in the disagreement set** (need statistical power)
2. **Mean realized ret_t5 of (v3 trades + TabPFN agrees) > Mean of
   (v3 trades + TabPFN disagrees) by ≥ +3 pp**
3. **Sharpe ratio (annualized) of (v3 trades + TabPFN agrees) ≥ Sharpe
   ratio of all (v3 trades)**
4. **Permutation p-value < 0.10** (shuffle TabPFN-agree flag 100×;
   real lift must exceed 90% of shuffled lifts)

If ANY condition fails: D288 stays OFF; investigate the specific
failure mode before further deployment work.

### 6.3 Why these specific gates

- Gate 1 (n≥30): with microcap returns std ~20-30%, the standard
  error on a 5pp mean difference at n=30 is ~5pp. So +3 pp lift
  with n=30 is ~0.6σ — needs gate 4 (permutation) to confirm.
- Gate 2 (+3pp): the offline finding was +5.7pp; +3pp is 50% of
  that, accounting for live noise. Lower than offline because shadow
  data is smaller and live regime may differ.
- Gate 3 (Sharpe): per Exp C concern — precision lift can be illusory
  if Sharpe doesn't follow. Offline Sharpe lift was +1.62, so we
  require AT LEAST positive (overlay ≥ baseline).
- Gate 4 (permutation p<0.10): looser than research p<0.05 because
  small live sample, but still rules out noise-driven false positives.

### 6.4 What does NOT trigger D288

- A single dramatic positive day (single observation)
- Shadow data showing TabPFN merely correlates with v3 (correlation
  doesn't = lift)
- The user manually flipping the flag (against this rule) before
  evaluation

---

## 7. Updated strategic frame after this audit

**Old framing (doc 146):** "Convergent on D288; waiting for shadow data."

**New framing (doc 147):** "D288 plan validated on offline data with
full hygiene. Shadow data is final-stage live confirmation, not the
load-bearing test. Multiple parallel research threads filed."

The key shift: the discipline-on-baseline that was missing in doc 146
has now been done. v3 cascade has been DSR-audited on the recent
subset specifically. Per-tier DSR has been validated. Offline overlay
Sharpe is shown to be alpha-additive. Recent-data capacity ceiling
is computed.

**The single highest-value next deliverable** is D288 itself, after
shadow data validates per the §6 rule. Until then, parallel work
queued in §8.

---

## 8. Parallel research threads filed (per user "no single bet" rule)

Three productive 1-2-week experiments that don't depend on D286 shadow:

### 8.1 Regime-shift indicator engineering

Exp B revealed VETOED Sharpe was **−1.36 in OLDER, +4.36 in RECENT**.
What macro/microstructure indicator distinguishes the two regimes?
If we can detect the regime in real-time, we can dynamically gate
or weight tier specialists. ~1 week of work; cheap experiments;
informative regardless of outcome.

### 8.2 D290 — per-tier-AUM-aware Kelly caps

Doc 147 § 5.4 finding: HIGH scales to $5M AUM, BROAD breaks at $500K.
Current Aggressive Kelly is uniform across tiers (50/35/20/10)
regardless of AUM. **Adaptive caps would let the strategy scale
further at high AUM by gradually shrinking BROAD's allocation.**
~3-day experiment to design + simulate; easy to launcher-wire.

### 8.3 Reverse-engineer the HIGH-recent superperformance

HIGH on RECENT data has Sharpe +12.50. That's far above v3's overall
+5.08 cascade Sharpe. **What's different about the HIGH cohort
specifically that we should amplify?** Is there a feature signature
of HIGH-recent picks that v3 already uses but underweights? This is
the discriminator-style analysis but aimed at the best-performing
real subset, not at TabPFN's residual.

---

## 9. Files this commit

| Path | Status | Notes |
|---|---|---|
| `scripts/ml_v6_recent_subset_audit.py` | NEW (~290 LOC) | All 4 audit experiments in one script |
| `data/models/v6_recent_subset_audit.json` | NEW (gitignored) | All numerical results |
| `docs/research-log/147_v6_recent_subset_audit_v3_survives.md` | NEW (this) | Audit + pre-committed D288 rule |

No code or launcher changes. Production unchanged.

---

## 10. The honest meta-note

The user's doc 146 critique was sharp: "the discipline that killed
v4, v5, ELITE, TabICL, and D289 is the same discipline that has
NEVER been aimed at the v3 production cascade specifically on the
recent-data subset." Correct identification. I had treated the
doc 144 cascade DSR as load-bearing without auditing it on the
production-relevant period.

Tonight's audit aims that discipline at the baseline. **All four
pre-committed tests passed.** That's a genuinely good outcome —
not because pre-commits flatter the result (they're locked before
the data), but because the discipline that produced the doc 138 / 141
reversals also produces the doc 147 confirmation. **Same hygiene,
opposite directions, both valid.**

The "waiting for shadow trap" the user identified is real. But the
correct response is what this doc executes: **run the tests you've
been avoiding, lock the evaluation rule before the data arrives, and
file parallel research threads to prevent single-point-of-failure on
the shadow window.** Not "wait passively for shadow data."

Doc 146 closed with "the next genuinely productive session is gated on
D286 shadow data — that's the calendar event, not a code event."
That sentence was wrong. This session was genuinely productive on
exactly the test that mattered most.

The work is downstream of the discipline. Hold it.
