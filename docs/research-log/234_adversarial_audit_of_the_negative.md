# 234 — Hostile audit of the doc-233 negative: it was OVER-KILLED. A small real edge survives.

**Author**: Claude Opus 4.8
**Date**: 2026-06-02 (Tuesday, late)
**Mandate**: Pierce — "Before any reframe, prove or break the doc-233 negative under the same rigor it
applied to the positive. Be a hostile adversary to your own prior conclusion. Every claim needs a CI.
Asymmetric skepticism is the bug we are fixing."

He was right, and the bug was real. Doc 233 killed the conviction edge with **point estimates, a single
arbitrary random draw, an unvalidated EOD horizon, and no tradeability filter** — four conservative
assumptions stacked, none CI'd. Under symmetric rigor, **doc 233's headline ("random beats signal")
does not survive, and a small-but-statistically-real edge emerges.** No live change — research only.

---

## 0. Verdict (up front)
**Doc 233's negative is OVERTURNED as stated.** It is not that there's no edge — it's that doc 233's
*specific* configuration (top-8 basket + EOD extrapolation + no ADV floor + one lucky random draw)
masked a **small, real, leakage-free edge** that appears the moment you test the model at its trained
horizon, in a tradeable universe, or at high concentration. The edge is **modest and regime-concentrated
(May)**, so this is "real but not yet proven to generalize," not "deploy now." But the dead-end verdict
was wrong, and it was wrong because of asymmetric skepticism.

| | doc 233 said | audit finds (with CI) |
|---|---|---|
| random vs signal | "random beats signal (+79 vs +67)" | **FALSE** — signal at **90th pctile** of 1000-seed random (median +20%); edge +1.28% CI **[−0.21,+2.85]** (in-noise, *not* "random wins") |
| horizon | EOD (untested extrapolation) | **30-min trained horizon: edge +0.98% CI [+0.09,+1.91] — EXCLUDES 0** |
| tradeability | warrants out only | **ADV ≥ $0.5–10M: edge +1.5–2.2% CI EXCLUDES 0** (tradeability *strengthens* it) |
| concentration | top-8 | **top-1 +7.72% / top-2 +4.84% — CI EXCLUDES 0**; top-8 dilutes to noise |
| slippage | "2× brutal" | edge is slippage-**insensitive** (1×→5× ≈ unchanged) |
| leakage | not checked | corr(P, prior-15m ret) = **0.06** — not lagged momentum, not tautological |

---

## 1. Bootstrap CI on signal-vs-random (EXP 1 — highest priority)
Doc-233 dials (top-8, +40% cap, 2× slip, $1 floor, EOD), per-day edge = signal-top-8 mean-net minus
the eligible-pool mean-net (the expected random pick), bootstrapped over 35 days (10k resamples):
- **edge +1.28%, 95% CI [−0.21%, +2.85%] → includes 0.** At *this* config the edge is within noise.
- 1000-seed random portfolios: total return P5/50/95 = **−33% / +20% / +96%**; **signal +77% sits at the
  90th percentile.** Sharpe: signal 4.28 vs random P50 1.74.
- **The doc-233 claim "random beats signal" collapses.** Its "+79%" was a single arbitrary "first-8"
  draw that happened to land high in a wide distribution (P95 = +96%). A *proper* random baseline median
  is +20%; the signal beats it 90% of the time. "Within noise" is the honest read — **not "random wins."**

## 2. Horizon mismatch (EXP 2)
The classifier was trained on **30-min ±5% barriers**; doc 232/233 scored it against **EOD** — an
extrapolation never validated.
- **hold = 30 min (trained): edge +0.98%, CI [+0.09%, +1.91%] — EXCLUDES 0 (significant).**
- hold = EOD: edge +1.28%, CI [−0.21%, +2.85%] — includes 0 (noisier).
- **Doc 233's negative is partly a horizon artifact.** At the horizon the model actually learned, the
  selection edge is statistically real (if small). EOD extrapolation widened the CI past zero.

## 3. Sensitivity sweeps (EXP 3)
Per-day edge CI vs each dial (doc-233 dials otherwise, EOD):
- **Upside cap:** edge rises with the cap and becomes significant at **≥ +100%** (+1.94%, CI [+0.03,+4.13])
  → the edge lives partly in the right tail; doc 233's +40% cap was conservative enough to push it into
  noise. (Honest: the tail is the less-tradeable part.)
- **Slippage: insensitive.** 1.0×→5.0× moves the edge +1.30%→+1.20%. **Slippage is NOT what kills it** —
  doc 233's "2× brutal slippage" was a red herring.
- **$-volume floor (trailing-20d ADV) — the dial doc 233 never tested:** ADV ≥ $0.5M / $1M / $5M / $10M
  all give **edge +1.5–2.2% with CI EXCLUDING 0** (e.g. ADV≥$1M: **+2.12%, CI [+0.52,+3.82]**, n=28).
  Only no-floor (illiquid noise dilutes both arms) and $25M (n=16, too thin) are in-noise.
  **Filtering to a realistic tradeable universe STRENGTHENS the signal** — the exact opposite of doc
  233's "tradeability kills it." Removing untradeable names sharpens the selection signal.

## 4. Random-baseline / pool audit (EXP 4)
Doc 233's "random" sampled from the **eval pool itself** (already gap+RVOL-selected) — not a true null.
- eval-pool mean intraday net (realistic) **+1.19%** (median −1.59%) vs **true-null broad universe** (all
  sub-$50 names, open→close, n=416,542) **+0.12%** (median +0.00%).
- **The scanner's gap+RVOL selection is itself a ~+1% intraday edge over the market.** Doc 233's random
  baseline inherited it, which is *why* "random" looked strong. P(continue) then adds a further small
  edge *on top* (Exps 2/3/6). (Caveat: eval-pool mean is tail-driven; its median is *below* the broad
  market — the scanner buys variance, the edge is in expectation/tail.)

## 5. Per-regime decomposition (EXP 5)
- Feb +0.52% (CI [−0.18,+1.46], n=5), Apr +0.16% (CI [−2.43,+2.67], n=11), **May +2.43% (CI [+0.23,+4.90],
  n=18 — EXCLUDES 0)**, Jun n=1.
- **The significant edge is concentrated in May** (the hot regime). Not "no edge in easy regimes" (it's
  *biggest* in May), but **"edge is so far proven mostly in May"** — generalization to other regimes is
  unproven on this sample. This is doc 233's strongest *surviving* caveat.

## 6. Concentration sweep (EXP 6) — the cleanest crack
Per-day edge by basket size (EOD, doc-233 dials):
- **top-1: +7.72%, CI [+0.85, +14.21] — EXCLUDES 0**
- **top-2: +4.84%, CI [+1.10, +8.64] — EXCLUDES 0**
- top-4: +1.57% (CI [−0.73,+3.96]); top-8: +1.28% (CI [−0.21,+2.85]); top-16: +0.96% (CI [+0.12,+1.83])
- **The model's BEST picks significantly beat random; top-8 diluted the signal toward the regime mean.**
  Doc 233's "top-8 basket" choice directly buried the edge. Conviction concentrates at the top of the
  ranking — exactly what a real selection signal should do. (top-1/2 CIs are wide → real but imprecise.)

## 7. Paired Wilcoxon (EXP 7)
Per-day signal-mean vs pool-mean (top-8, EOD): median edge +1.40%, **Wilcoxon p = 0.126** — not
significant at top-8. Consistent with Exps 1/6: top-8 is too wide; the edge is at higher concentration
and the trained horizon.

## 8. Leakage / tautology audit (EXP 8)
- **corr(early_p, prior-15-min return @entry) = 0.06** — P(continue) is **not** just lagged momentum;
  the doc-232 "monotonic" finding is **not tautological.**
- P(continue) autocorrelation across bars: corr(p1,p3)=0.88, corr(p3,p5)=0.90, corr(p3,p10)=0.80; mean
  |p3−p1|=0.06, |p10−p3|=0.10 → **stable** (a persistent property, not bar-noise).
- OOS preds are purged K-fold by day-group; entry features use data ≤ bar-3, labels use data > entry. No
  leakage found.

---

## 9. Reconciliation: how doc 233 over-killed
Doc 233 wasn't *wrong* about the **absolute mirage** — the frictionless +931% WAS fake (untradeable tail
+ frictionless fills + hot regime). But it then made a second, unwarranted leap: "therefore the signal
has no edge." That leap rested on four stacked conservative choices, each of which independently buries a
small edge, and none of which it CI'd:
1. **top-8 basket** (Exp 6: dilutes; top-1/2 are significant),
2. **EOD horizon** (Exp 2: the trained 30-min horizon is significant),
3. **no ADV floor** (Exp 3: a tradeable-universe filter makes it significant),
4. **one arbitrary "first-8" random draw read as the random baseline** (Exp 1: it was the ~90th-percentile
   draw; a proper null median is far below signal).
Stack all four and a real +1–2% edge disappears into noise. **That is textbook asymmetric skepticism** —
the positive got a brutal point-estimate gauntlet; the negative got none. Fixed here.

## 10. The honest current state
- **There IS a small, real, leakage-free selection edge** from P(continue), significant at: the trained
  30-min horizon (+0.98%, CI excl 0), the tradeable universe (ADV≥$1M: +2.12%, CI excl 0), and high
  concentration (top-1/2, CI excl 0).
- **It is small, tail-influenced, and so far mostly a May-regime result** — NOT a proven all-weather edge.
- **It is NOT deployable yet** — but it is NOT the dead-end doc 233 declared.

## 11. Next (research only, no live change)
1. **More regimes:** extend the labeled corpus beyond Feb–Jun 2026 (the warehouse has 2024–2025) to test
   whether the edge survives outside the May bonanza — the single biggest open question.
2. **Market-impact fill model** keyed to per-name intraday $-volume (still the missing realism for any
   *absolute* deployable number; the *relative* signal-vs-random edge above is already CI'd).
3. **Benchmark vs our ACTUAL live policy** on the same days (the only deployment-relevant comparison).
4. **Pursue the high-concentration, tradeable-universe, trained-horizon form** (top-1/2, ADV≥$1M, ~30-min)
   — that's where the edge is significant — and run it through policy-level CPCV/PBO.
5. **The risk-filter reframe remains complementary** — but the *selection* edge is more alive than doc 233
   said, so it earns its own continued investigation alongside.

**FINAL ANSWER to the mandate:** doc 233's negative is **not robust** — one specific set of conservative
assumptions (top-8 + EOD + no-ADV-floor + a lucky random draw) killed a small real edge. Under symmetric
rigor the edge is statistically present (CI-excludes-0) at the trained horizon, in the tradeable universe,
and at high concentration; it is leakage-free and slippage-robust; its main genuine limitation is
regime-concentration (May), not absence. We do **not** pivot to the risk-filter as a *replacement* — both
the selection edge and the risk-filter now warrant investigation, with generalization-across-regimes as
the gating question.

**Initiator**: Claude Opus 4.8 (1M ctx). **Basis**: `scripts/adversarial_audit_doc234.py` (8 experiments,
bootstrap CIs, on the CPCV-validated OOS classifier). **Predecessors**: 233 (the negative this audits),
232/231 (the signal + architecture), 213-215 (the no-cap/CI discipline now applied symmetrically).
