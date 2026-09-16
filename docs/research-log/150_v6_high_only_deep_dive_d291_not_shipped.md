# 150 — v6 HIGH-only deep dive: D291 NOT shipped (2-of-5 sub-experiments PASS); two actionable findings

> **Format:** doc-138-template (script first, run, then doc, verdict last).
> Pre-commits LOCKED in writing before running. Bold ship rule: 4-of-5
> sub-experiments PASS AND Sharpe ratio ≥1.5×. Got 2-of-5 + ratio 1.60×.
> Per locked rule: D291 NOT shipped. Same discipline as a negative result.

**Session date:** 2026-05-11
**Branch:** develop
**Predecessors:** [149 v6 D290 adaptive Kelly](149_v6_d290_adaptive_kelly_shipped.md)
**Status:** **D291 NOT shipped.** Bold ship pre-commit failed on
breadth-of-evidence (4-of-5 sub-experiments needed; got 2). Two
secondary findings filed: HIGH supports more Kelly than D290 allows
(but n too small to ship); d-1 features hurt HIGH but help BROAD
(refines feature-routing plan).

---

## 0. NEW HYGIENE RULE (locked in this session)

Per user critique on doc 149: "any finding with DSR > 0.95 at N≥100
becomes the next session's top priority, regardless of what was
previously queued. The verdict-blank rule applies to positive outliers
too."

This is now a permanent addition to the v6 hygiene contract:

> **Rule 6 (positive-outlier escalation):** Any single-tier or single-
> configuration finding with **DSR > 0.95 at N_trials ≥ 100** is escalated
> to the NEXT session's top priority. Filing it as "thread #N for future
> session" requires explicit justification why a more dramatic positive
> can be deferred.

Doc 147 §3 found HIGH-recent Sharpe +12.50, DSR @ N=100 = 0.999. I
filed it as thread #3 (after regime indicator and adaptive Kelly).
That filing was a discipline failure — the user's critique correctly
identified it as the same pattern as filing a dramatic negative as
"marginal." The pattern that catches false positives must equally
catch buried positives.

This session corrects the failure: HIGH-recent gets the deep-dive
treatment, with pre-committed verdict criteria.

---

## 1. Pre-committed verdicts (LOCKED before running)

```
Sub-exp A (per-month robustness):
  PASS:   min(leave-one-month-out Sharpe) >= 5.0
  FAIL:   any month removal drops Sharpe below 3.0 (outlier-driven)

Sub-exp B (Bouchaud-optimal HIGH Kelly @ $1M):
  PASS:   optimal Kelly > 35% (D290 current cap at $1M)

Sub-exp C (tier-definition sensitivity, threshold in [0.45, 0.55]):
  PASS:   max deviation across thresholds <= 2.0 Sharpe units

Sub-exp D (d-1 microstructure on HIGH-eligible candidates):
  PASS:   v3+d-1 Spearman lift >= +0.020 vs v3-alone

Sub-exp E (TabPFN overlay isolated to HIGH):
  PASS:   HIGH+TabPFN-agree Sharpe >= 1.2x HIGH-alone Sharpe

BOLD SHIP D291 (cascade collapse to HIGH+ELITE only):
  IF 4-of-5 sub-experiments PASS
  AND HIGH-only Sharpe at $1M AUM (Bouchaud-adjusted)
      >= 1.5x cascade Sharpe at $1M (Bouchaud-adjusted)
  THEN: launcher commit defaults LOTTERY_MIN_TIER_THRESHOLD=0.50.
        Direct production change, not env-flag-default-off.
```

---

## 2. Sub-experiment results

### Sub-exp A — per-month robustness — PASS

```
HIGH-recent active months: 5 (Sep, Oct, Nov, Dec 2025; Mar 2026)

month_removed   n_remaining   sharpe_remaining
2025-09         13             +8.232    <- min
2025-10         16            +14.291    <- max
2025-11         20            +12.568
2025-12         19            +12.828
2026-03         20            +12.444

full-data Sharpe:        +12.217
min single-month-removed: +8.232 (above 5.0 threshold)
median single-month-removed: +12.568
```

**The +12.50 Sharpe is structural across months.** Removing the worst
month (Sep 2025) drops Sharpe to +8.23, still well above any normal
"good strategy" baseline. No single month is carrying the result.

### Sub-exp B — Bouchaud-optimal HIGH Kelly @ $1M — PASS (with sample-size caveat)

```
HIGH-recent at $1M AUM, n=25 picks:
  optimal Kelly: 98.0%
  avg participation at optimal: 48.6%
  mean ret/pick: +25.46%
  mean impact/pick: 21.31%
  mean net/pick: +4.16%
  $-PnL per pick: $40,750

Current adaptive HIGH Kelly at $1M (D290): 35.0%
```

**Sub-exp B passes the trigger** (optimal > current D290 cap). But the
optimization runs on n=25 HIGH-recent picks — a small sample. The
98% optimum may overfit to these 25 specific picks. Mean ret_t5 of
+25.46% on n=25 has wide CI.

Conservative interpretation: HIGH supports MORE Kelly than D290's 35%,
but I should not ship a 98% cap based on n=25. A modest raise (e.g.,
35% → 50%) might be defensible after more data accumulates.

**Filed (but not shipped tonight):** D291.5 — HIGH-tier Kelly raise
from 35% to 50% at $1M AUM bracket. Wait for at least 50 HIGH-recent
picks (currently 25) before shipping.

### Sub-exp C — tier-definition sensitivity — FAIL

```
thr_high  n_picks  days  ann_sharpe  mean_ret
0.45      67       52    +8.724      +21.26%
0.48      38       31    +11.192     +25.82%
0.50      25       22    +12.217     +25.46%   <- baseline
0.52      20       17    +13.052     +28.53%
0.55      8        8     +9.546      +31.65%   <- collapses (n=8)
0.58      2        (<5)
0.60      0        (<5)

baseline at thr=0.50: +12.217
max deviation: 3.493  (above 2.0 gate)
```

**The boundary is fragile.** Sharpe peaks at thr=0.52 (+13.05) but
collapses to +9.55 at thr=0.55 (only 8 picks). At thr=0.45, Sharpe
drops to +8.72 because lower-conviction picks dilute.

The "HIGH = v3_proba ≥ 0.50" boundary is reasonable but not robust to
small shifts. The +12.50 Sharpe at exactly thr=0.50 is sample-
dependent on which 25 picks make the cut. Production should not
depend on this exact boundary.

### Sub-exp D — d-1 microstructure on HIGH-eligible — FAIL

```
HIGH-eligible (top-quartile per day) Spearman:
  v3-only:   +0.0874
  v3+d-1:    +0.0630
  delta:     -0.0243   <- d-1 HURTS HIGH-eligible
```

**d-1 features hurt HIGH.** Doc 138 found d-1 features lifted BROAD
P@30 by +0.039. Tonight's test on HIGH-eligible (top-quartile)
shows d-1 hurts by −0.024 Spearman.

**The bigger pattern emerging from this:**
- d-1 features lift BROAD (lower-conviction picks where extra info helps)
- d-1 features hurt HIGH (high-conviction picks where extra info adds noise)
- d-1 features hurt TabPFN (doc 144 §4)

d-1 microstructure is a "noise reduction for marginal picks" feature,
not a "signal amplification for high-conviction picks." This refines
the deployment plan: **d-1 features wired into BROAD specialist
specifically, not added to v3 globally.** Filed as a refinement to
the D286-shadow-data follow-up plan.

### Sub-exp E — TabPFN overlay on HIGH — INCONCLUSIVE

```
Merged TabPFN + v3: 3,577 rows
HIGH-tier picks with TabPFN preds: 8 (too few for stable Sharpe)
```

The TabPFN/v3 merge has only 8 HIGH-tier overlap points (because
TabPFN ran on a different fold scheme that has thin coverage of the
production-cascade HIGH boundary). Cannot reliably compute overlay
Sharpe on HIGH alone.

**Filed for next iteration:** when D286 shadow data accumulates
~50 HIGH picks with TabPFN coverage, re-run sub-exp E.

---

## 3. Bold ship pre-commit verdict

```
Sub-experiments passing: 2 of 5  (need 4-of-5)
  A: PASS    B: PASS    C: FAIL    D: FAIL    E: INCONCLUSIVE

Sharpe ratio HIGH/cascade at $1M Bouchaud-adj: 1.603x  (passes 1.5x)
  cascade Sharpe at $1M:    +1.883
  HIGH-only Sharpe at $1M:  +3.018  (Kelly capped at 50% for safety)

D291 BOLD SHIP: pre-commit NOT met (4-of-5 gate fails).
```

**D291 NOT shipped.** Per locked rule, even though the Sharpe ratio
criterion passes, the breadth-of-evidence criterion (4-of-5 sub-
experiments) doesn't. The data is showing real HIGH structure (A
passes) but the tier boundary is fragile (C fails) and d-1 doesn't
compose (D fails) and we can't test the TabPFN angle (E
inconclusive). Three out of five tests don't support the
deployment-grade ship.

This is exactly the pattern the user critique was about — but in
the OPPOSITE direction from what they expected. The user's concern:
"can you act on a positive the way you act on a negative." The
answer: **with the same locked discipline, here the locked criterion
fails to fire.** Shipping D291 anyway because "the Sharpe ratio is
1.6×" would be exactly the rationalize-after-the-fact pattern the
hygiene contract is designed to prevent.

---

## 4. The honest interpretation of HIGH-recent

The HIGH-recent finding (Sharpe +12.50, DSR 0.999) IS real on the
data we have. Sub-exp A confirms it's structural across months.
Sub-exp B confirms it could absorb more Kelly than current D290 allows.

But:
- Only 25 HIGH picks across 5 months. Statistical power is weak.
- The boundary at v3_proba=0.50 is fragile (Sub-exp C); +-3 percentage
  points on the threshold meaningfully shifts the result.
- d-1 features don't compose with HIGH (Sub-exp D).
- TabPFN overlay can't be tested on HIGH alone with current data
  (Sub-exp E).

**The most honest framing:** HIGH-recent is a real high-conviction
pocket of v3's predictions, but our data is too thin to build a
production architecture around it alone. The cascade structure
(BROAD + VETOED + HIGH + ELITE) provides volume and diversification
even if HIGH carries most of the per-pick alpha.

The cascade-union DSR (doc 144 = 0.995, doc 147 RECENT = 0.972) is
based on n=145-238 active days vs HIGH-only n=22 days. The breadth
matters for statistical confidence even when HIGH carries the per-pick
edge.

---

## 5. What to do instead — modest moves filed

### 5.1 D291.5 — HIGH-tier Kelly raise (filed, not shipped)

When at least 50 HIGH-recent picks accumulate (currently 25), re-run
sub-exp B. If optimal Kelly remains > 50% on n≥50, raise the D290
adaptive HIGH cap from 35% to 50% at the $1M AUM bracket. Don't
ship now on n=25.

### 5.2 d-1 features routed to BROAD specifically (refinement to D288 plan)

Current plan (doc 144 + doc 138): add d-1 features to v3 BROAD
specialist's input space. Sub-exp D confirms this routing: d-1
helps BROAD (+0.039 P@30) and hurts HIGH (−0.024 Spearman). When
v3 specialists get retrained, only the BROAD specialist gets d-1
features. HIGH and ELITE specialists do NOT get d-1.

### 5.3 Sub-exp E (TabPFN overlay on HIGH) re-run after shadow data

D286 shadow runner is accumulating live TabPFN predictions. Once we
have ~50 HIGH-tier picks with paired TabPFN predictions (3-4 weeks
of shadow data), re-run sub-exp E. If overlay Sharpe ratio ≥ 1.2×
HIGH-alone, we have evidence for HIGH-specific overlay deployment.

### 5.4 Cascade structure stays intact

D288 defensive overlay plan (doc 147 § 6) proceeds as scheduled. The
cascade as a whole is DSR-validated; HIGH-only doesn't have enough
breadth to replace it. The user's correct critique was that I should
have INVESTIGATED HIGH-only as the top priority (which I now have).
The verdict of the investigation is "don't collapse to HIGH-only,"
and that verdict landed strictly per the locked rule.

---

## 6. The pattern this session validates

The user's critique on doc 149 was specifically:
> "Can you have the discipline to act on a positive result for one
> tier — to admit that three of your four tier specialists are
> diluting the one that actually works?"

The locked pre-commit was the answer: **4-of-5 sub-experiments AND
ratio ≥1.5×.** Got 2-of-5 + 1.6×. The Sharpe ratio favored shipping;
the breadth-of-evidence didn't.

If I had a SINGLE criterion (just the Sharpe ratio), I would have
shipped D291 tonight. The composite criterion caught the result that
"yes the Sharpe is high but the boundary is fragile and the
composition is wrong and we don't have data on the overlay test."

This is the discipline working symmetrically:
- It killed v4 architecture (multiple-testing didn't survive)
- It confirmed v3 cascade alpha (recent DSR @ N=100 = 0.972)
- It killed regime-indicator deployment (lag-1 sign flip)
- It shipped D285 + D290 capacity defenses (clear gate met)
- **It now blocks D291** (composite criterion fails)

The user's challenge was "would you act on a positive with the same
alacrity as a negative?" The answer is "with the same locked-
criterion discipline, the answer here is no — and that's correct."

---

## 7. Files this commit

| Path | Status |
|---|---|
| `scripts/ml_v6_high_only_deep_dive.py` | NEW (~340 LOC, 5 sub-experiments + bold ship pre-commit) |
| `data/models/v6_high_only_deep_dive.json` | NEW (gitignored) |
| `docs/research-log/150_v6_high_only_deep_dive_d291_not_shipped.md` | NEW (this) |

No code or launcher changes. Production unchanged.

---

## 8. Filed for future sessions

**Triggered by data accumulation:**
- D291.5 HIGH Kelly raise (when n_HIGH_recent ≥ 50)
- Sub-exp E re-run with shadow data (when ≥50 HIGH picks have TabPFN preds)
- D288 defensive overlay enable (per doc 147 §6 — 10 trading days of D286 shadow)

**Refinement to existing plans:**
- d-1 features routed to BROAD specialist only (per Sub-exp D)
- HIGH and ELITE specialists exclude d-1 from input

**RTX-5070-feasible research threads remaining:**
- Frontier 2 (Decision-focused E2E learning) — per attached compass research
- Frontier 3 (Causal discovery on 60k unlabeled)
- Frontier 4 (LLM-driven alpha mining via Benzinga catalysts)
- Frontier 5 (BOCPD-MoE regime specialists)
- TabPFN continual pretraining on 60k unlabeled (Real-TabPFN recipe, ~$600 H100 / or RTX 5070 over a few days)

**Compute-cloud research threads (deferred):**
- Frontier 1 FinPFN flagship (8×H100 week)
- Frontier 6 diffusion market simulator (2-4 H100-days)

---

## 9. The honest meta-note

Three threads from doc 147's §8 are now closed:
- Thread #1 (regime indicator): NEGATIVE (doc 148)
- Thread #2 (D290 adaptive Kelly): POSITIVE → SHIPPED (doc 149)
- **Thread #3 (HIGH-only deep dive): NOT SHIPPED — discipline-locked outcome (this doc)**

All three went through the same pre-commit / smell-test / locked-
verdict pattern. Each landed strictly on data. Two were obvious
verdicts (148 negative, 149 positive). This one was less obvious —
2-of-5 with ratio 1.6× is the kind of result that's most tempting
to rationalize one direction or the other.

The locked composite criterion (4-of-5 AND 1.5×) made the verdict
unambiguous. **That's what the hygiene contract is for.**

The new hygiene rule (Rule 6: positive-outlier escalation) is now
in force. The next time the analysis surfaces a finding with
DSR > 0.95 at N≥100, it gets the deep-dive treatment immediately,
not filed for "future session."

The work is downstream of the discipline. Hold it.
