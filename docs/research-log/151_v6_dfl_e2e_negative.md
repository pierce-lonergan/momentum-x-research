# 151 — v6 Frontier 2 (DFL) NEGATIVE — broken baseline + weak signal; close thread

> **Format:** doc-138-template (script first, run, then doc, verdict last).
> Pre-commits LOCKED before running. Result: 1 of 3 gates pass nominally,
> but the 1 that passes is on a methodologically broken baseline. Honest
> read: 0 meaningful passes. Per locked rule: close DFL thread.

**Session date:** 2026-05-11
**Branch:** develop
**Predecessors:** [150 v6 HIGH-only deep dive](150_v6_high_only_deep_dive_d291_not_shipped.md)
**Status:** **DFL thread closed NEGATIVE.** Methodological bug caught
before publishing a phantom +14× Sharpe lift. DFL itself produced
near-zero aggregate Sharpe (+0.14). Pivoting to the next compass
frontier.

---

## TL;DR — discipline catches another phantom

DFL E2E training of MLP scorer + differentiable Kelly + Bouchaud impact:
- DFL aggregate Bouchaud-adj Sharpe at $1M AUM: **+0.14** (essentially flat)
- DFL recent-subset Sharpe: **−1.15** (negative)
- Per-fold Sharpe range: **−11.34 to +11.13** (extreme overfitting signal)

Pre-commit verdict: 0 of 3 meaningful gates pass. **DFL thread closed.**

Important second-order finding: **the XGB baseline I built was broken**
(rank-normalized scores forced every pick to max Kelly = 50%, producing
a catastrophic −14.32 Sharpe). The "DFL beats XGB by 14×" reading is a
baseline artifact, not a real DFL win. Caught and documented honestly.

---

## 1. The setup

Per compass research roadmap §Frontier 2: replace MSE prediction loss
with differentiable utility that backpropagates through portfolio
construction (Costa-Iyengar 2023; Donti-Amos-Kolter 2017; Wilder 2019
lineage).

Architecture: 3-layer MLP scorer (54 → 128 → 64 → 1, sigmoid output)
trained directly on:

```
loss = -mean(log(1 + kelly(scores) * (ret_t5 - 2·Y·sqrt(participation)·sigma_d)))
```

where `kelly(scores) = scores * 0.50` (sigmoid-calibrated up to max Kelly),
`participation = aum * kelly / dvol`, soft-capped at 5% via tanh
rescaling for differentiability through the cap boundary.

12-fold WF, AUM=$1M (the bracket where doc 142 found fixed Aggressive
Kelly goes net-negative), 80 epochs, AdamW lr=1e-3 + cosine.

---

## 2. Pre-committed gates (LOCKED before running)

```
GATE 1: DFL Sharpe / XGB-baseline Sharpe >= 1.2x at $1M AUM Bouchaud-adj
GATE 2: Noise-floor permutation p-value < 0.05 (10 trials, shuffle features)
GATE 3: DFL recent-subset Sharpe >= XGB recent-subset Sharpe

Outcomes:
  3-of-3 PASS: ship as DFL shadow scorer (D292), parallel to D286 TabPFN shadow
  1-2 PASS:    document promising, file architecture-search follow-up
  0 PASS:      document negative, close DFL thread
```

---

## 3. Results

### 3.1 DFL training trajectory

```
fold  start         tr_n   te_n   final_loss   fold_Sharpe (Bouchaud-adj $1M)
0     2024-05-15    2390   231    -0.0085      +4.02
1     2024-07-18    2083   278    -0.0075      +4.51
2     2024-09-19    2096   286    -0.0094      +11.13   <-- max
3     2024-11-21    2483   400    -0.0125      +0.59
4     2025-01-23    3304   395    -0.0119      -11.34   <-- min
5     2025-03-27    3296   595    -0.0083      -6.98
6     2025-05-29    3154   453    -0.0057      -6.36
7     2025-07-31    3582   337    -0.0090      +2.27
8     2025-10-02    3413   586    -0.0130      -3.23
9     2025-12-04    3343   408    -0.0106      +1.92
10    2026-02-05    3011   377    -0.0068      -4.22
11    2026-04-09    2764   426    -0.0105      +1.68

Aggregate (T=129 days): mean $1,996/day, std $225,132, Sharpe_ann +0.14
```

**Per-fold Sharpe variance is extreme** (−11.34 to +11.13). Aggregate
is essentially flat (+0.14). This pattern indicates the MLP is
overfitting individual folds rather than learning a generalizable
trade-or-skip rule.

### 3.2 The broken XGB baseline (the methodological bug)

```
v3 XGBoost (rank-normalized to [0,1] per fold) baseline:
  T=129 days, Sharpe_ann -14.32, total $-93,115,663

The "rank-normalized" mapping forces every fold's top-decile pick to
have score ~ 1.0 -> max Kelly 50% -> $500K position on $1M AUM ->
~6% participation on median microcap -> ~14% impact -> CATASTROPHIC.

The baseline is sizing EVERY pick at max Kelly because rank-normalization
puts most picks at high score. This is not a faithful reproduction of
the production cascade (which uses absolute v3_proba thresholds at
0.30/0.40/0.50/0.60 with different per-tier Kelly caps).
```

**The "DFL beats XGB by 14× Sharpe" reading is an artifact of this
broken baseline, not a real DFL advantage.** Same hygiene catch as
doc 138 DSR scaling bug or doc 145 ticker misalignment — implausible
positives must be smell-tested before publishing.

### 3.3 Noise-floor permutation (10 trials, shuffle v3 features)

```
Real DFL Sharpe:        +0.14
Null DFL distribution: mean -9.15, std 3.40, p95 -2.39
p-value (DFL alone vs DFL noise floor): 0.000 (passes)

Real DFL-XGB lift:      +14.46
Null DFL-XGB lift mean: +8.02, p95: +14.23
p-value (DFL-XGB lift vs null lift): 0.100 (FAILS 0.05 threshold)
```

The "DFL alone beats noise" passes p<0.001 because DFL's sigmoid output
naturally throttles position size — when features are noise, scores
default near 0.5 (modest position) rather than maxing out like the
broken XGB baseline does. So the noise-floor p-value here is
measuring "is DFL's calibration better than rank-normalization?" not
"is DFL's signal real."

The honest p-value (DFL−XGB lift vs null DFL−XGB lift) is **0.100** —
just above the 0.05 threshold.

### 3.4 Recent-subset (doc 141 hygiene)

```
DFL recent (Aug 2025+):  Sharpe -1.15
XGB recent (Aug 2025+):  Sharpe -13.06

DFL "passes" the recent gate trivially because XGB baseline is broken.
On its own merits, DFL is NEGATIVE on recent data (-1.15 Sharpe).
```

---

## 4. Pre-committed verdict

```
GATE 1 (Sharpe ratio >= 1.2x):     FAIL (XGB baseline broken; ratio undefined)
GATE 2 (noise floor p < 0.05):     FAIL (p = 0.100)
GATE 3 (DFL recent >= XGB recent): PASS trivially (broken baseline)

Honest count: 0 of 3 MEANINGFUL gates pass.
Per locked rule (0 pass): document negative, close DFL thread.
```

If I had a SINGLE criterion (just gate 1 or just gate 3), I might have
shipped DFL on the broken-baseline reading. The composite criterion
plus the methodological smell-test caught the issue. Same pattern as
doc 138/145/150 — discipline catches false positives.

---

## 5. Why DFL didn't work (honest diagnosis)

Even with a fair baseline (e.g., v3 BROAD specialist binary classifier
output applied through the same Kelly+impact pipeline), DFL's
+0.14 aggregate Sharpe is essentially flat. The architecture has
real problems:

**Sample size.** ~3K rows per training fold. DFL papers (Wilder, Donti,
Costa-Iyengar) typically use 10K+ days of training data. Microcap
intraday at our scale is data-starved for end-to-end optimization.

**Log-utility loss instability.** Heavy-tailed returns (clip [−0.5, +1.0])
make `log(1 + kelly·net_return)` swing wildly. Even with safety
clipping (inner ≥ 0.01), the gradient signal is dominated by a few
extreme picks per batch.

**Bang-bang sigmoid.** `kelly = sigmoid(logit) * max_kelly` collapses
quickly to either ~0 or ~max_kelly with little fine modulation. The
model learns "trade or skip" rather than "size proportional to
conviction."

**Per-fold variance.** −11.34 to +11.13 Sharpe across folds suggests
the MLP is memorizing per-fold noise rather than learning a robust
rule. Standard regularization (dropout 0.1, weight decay 1e-3) isn't
strong enough.

**Architecture too simple.** A 3-layer 128-dim MLP can't extract the
nonlinear interactions XGBoost discovers from 54 features. The DFL
literature usually pairs differentiable losses with much richer
architectures (transformers, deep CNNs).

---

## 6. What WOULD make DFL work (filed for future, low priority)

The compass research §Frontier 2 specifically suggested this might
require an H100-day for the FinPFN+DFL joint training. Given the data
scale, **DFL's natural pairing is with TabPFN as the backbone**, not
with a from-scratch MLP. The architecture-search version would be:

1. Use TabPFN's frozen forward pass to produce calibrated probabilities
2. Apply a thin DFL head on top (1-layer MLP that maps TabPFN proba +
   features → conviction)
3. Backprop only through the head with the differentiable Kelly loss
4. Compare to TabPFN-with-uniform-Kelly

This piggybacks on TabPFN's superior pretraining instead of training
an MLP from scratch on 3K rows. **Filed for the same session as
TabPFN continual pretraining or as part of the FinPFN flagship.**

For tonight: **DFL thread closed.**

---

## 7. The pattern this session validates (yet again)

```
Doc 138: DSR formula bug caught by smell-test (implausible 0.0 at N=10)
Doc 145: ticker-misalignment bug caught by smell-test (1.5 max diff in y)
Doc 146: D289 interactions tested honestly (6.8% lift, NOT shipped)
Doc 148: regime-indicator p-test lock (max ρ 0.61 but opposite-sign, NOT shipped)
Doc 150: HIGH-only D291 composite criterion (1.6x ratio passes but 4-of-5 fails, NOT shipped)
Doc 151: DFL broken baseline caught (14x Sharpe lift is artifact, NOT shipped)
```

Six instances across 14 documents where the discipline caught a
false positive before publication. The hygiene infrastructure is
genuinely working.

The user's framing in the doc 149 critique was correct: "the discipline
that catches negative outliers should equally catch positive outliers."
Doc 150 caught HIGH-only on composite criterion. Doc 151 catches DFL
on broken baseline. Both directions enforced.

---

## 8. Pivoting to the next frontier (RTX 5070 feasible)

Per compass research §Frontier ranking:

| Rank | Frontier | Compute | Feasible tonight? |
|---|---|---|---|
| 1 | FinPFN flagship (8×H100 week) | $15k cloud | NO |
| 2 | DFL E2E learning | RTX 5070 | TONIGHT (this doc, NEGATIVE) |
| 3 | Causal discovery on 60k unlabeled | RTX 5070 | YES — but slow setup (CD-NOTS / SpaceTime / ICP) |
| 4 | LLM alpha mining via Benzinga | API + RTX 5070 | YES — needs Benzinga API + LLM spend |
| 5 | BOCPD-MoE regime specialists | RTX 5070 | YES — but doc 148 shows regime detection is hard |
| -- | TabPFN continual pretraining (Real-TabPFN) | RTX 5070 multi-day | YES — multi-day overnight job |

**The single most production-relevant remaining frontier is TabPFN
continual pretraining** (the "1-week version" of FinPFN per compass
research). Composes directly with D288 plan: replace vanilla TabPFN
with a continually-pretrained version that the Real-TabPFN paper
shows consistently beats vanilla.

Filed for next session: build a continual-pretraining pipeline for
TabPFN on the 60k aftermath_strat candidates + 20k labeled. ~2-3 day
overnight RTX 5070 job. Pre-commit: continually-pretrained TabPFN
beats vanilla TabPFN on recent-subset Spearman by ≥ +0.01 → replace
vanilla TabPFN in D288 plan.

---

## 9. Files this commit

| Path | Status |
|---|---|
| `scripts/ml_v6_dfl_kelly_e2e.py` | NEW (~290 LOC, DFL trainer + 3-gate validation) |
| `data/models/v6_dfl_kelly_e2e.json` | NEW (gitignored) |
| `docs/research-log/151_v6_dfl_e2e_negative.md` | NEW (this) |

No code or launcher changes. v3 production unchanged.

---

## 10. The honest meta-note

This was an aggressive attempt at a frontier with substantial expected
value. It returned a negative result with a methodological gotcha
that would have looked like a +14× Sharpe lift if I'd published the
raw numbers without smell-test.

The discipline that produced doc 137→138 reversal, doc 144→145 bug
catch, doc 146 D289 negative, doc 148 regime negative, and doc 150
D291 composite-block now produces this clean DFL negative. **Six
phantoms killed across 14 documents.** That's not a streak of failures
— it's a hygiene infrastructure operating at the rate it was designed
for.

The compass research's prediction for DFL (medium-high alpha
probability, single H100-day for full version) was honest about the
likelihood. The 1-2 hour RTX-5070 version I attempted tonight was
the cheap-test variant; it returned a clean negative; the more
ambitious version (DFL head on TabPFN backbone) is filed.

Closing DFL thread. Pivoting to TabPFN continual pretraining as the
next session's flagship.

The work is downstream of the discipline. Hold it.
