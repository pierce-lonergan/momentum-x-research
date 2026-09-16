# 239 — c2 loss-tail cap experiment: FAILS cross-regime. No structural lever creates a base.

**Author**: Claude Opus 4.8
**Date**: 2026-06-03
**Mandate**: Pierce — "run c2" (the last sign-flipping structural lever from doc 238), then BET#2.

## Pre-registration (inline, before results)
Hypothesis: some hard-stop S, applied to the gapper universe (doc-235 corpus, entry = decision pt #3
~9:50, hold to EOD), improves the realized **MEAN** (not just variance/WL) by cutting catastrophic
non-recovering losers. **SURVIVES** iff some S improves the mean (paired mean-diff CI excl 0 positive) in
**all 3 years** (Bonferroni across S). **FAILS** otherwise. (`scripts/loss_cap_experiment_doc239.py`.)

## Result — FAILS
| stop | 2024 mean (Δ-CI) | 2025 mean (Δ-CI) | 2026 mean (Δ-CI) | W/L (3yr) |
|---|---|---|---|---|
| none | −0.13% | −0.33% | +0.50% | 1.17 / 1.19 / 1.32 |
| 3% | +0.22% [−0.1,+0.8] | +0.06% **[+0.0,+0.7]*** | −0.15% **[−1.5,+0.0]** | 3.01 / 3.09 / 2.12 |
| 8% | +0.13% [−0.0,+0.5] | +0.10% **[+0.2,+0.7]*** | +0.45% [−0.4,+0.3] | 1.52 / 1.58 / 1.50 |
| 20% | −0.02% | −0.17% | +0.49% | 1.21 / 1.25 / 1.33 |

- **No stop level improves the MEAN in all 3 years.** Significant-positive mean-diff appears **only in 2025**;
  it includes 0 in 2024 and trends **negative in 2026** (a tight stop *hurts* the trendy regime where dips
  recover — exactly the regime-fragility doc-235 warned of). With 0.5% stop slippage, 2026 turns
  significantly negative. 30-min horizon: stops mostly *hurt* the mean. → **FAILS the cross-regime rule.**
- **The W/L red herring:** a 3% stop lifts W/L from ~1.2 to ~3.0 — looks great, *isn't*. It's offset by a
  lower win rate (you stop out would-be-recoverers), so the **mean stays flat**. W/L is the same
  "looks-better-but-isn't" trap as b2; the MEAN is the honest metric, and it doesn't flip the sign.
- **Verdict:** the loss-cap is a variance/loss-size lever (like b2), **not a sign-flipper**, and it is
  **regime-dependent** (marginally helps choppy 2025, hurts trendy 2026).

## Synthesis — the structural-lever program is exhausted
| lever | result |
|---|---|
| selection alpha (231-235) | DEAD (sign-reverses OOP) |
| model overlay (236) | NO-OP (= trading smaller) |
| phantom leak (237) | real but FIXED (226-229); integrity not edge |
| entry-timing (238/c3) | NULL |
| EOD-flatten (238/c4) | integrity, not base-creating |
| **loss-cap (239/c2)** | **FAILS — variance/WL lever, doesn't flip the mean cross-regime** |

**No structural lever creates a profitable base.** The aggregate low-float gapper universe is structurally
~break-even-to-losing, and every lever only reshapes the variance — none moves the sign durably. Per
Pierce's pre-stated fork, this leaves two honest paths: **(a)** stop expecting edge from the *aggregate*
universe and instead test whether a **SUB-POPULATION** has edge the aggregate hides — i.e. **BET#2**
(catalyst-archetype playbooks); **(b)** if BET#2's archetypes also fail to separate/persist, accept this
universe has no extractable edge for us and change the universe or treat the system as a hardened paper
research instrument. **We proceed to BET#2 (doc 240) — with the explicit prior that most archetypes will
fail the same gauntlet, and the gates set so we learn that cleanly rather than fool ourselves.**

**No live change.** **Predecessors**: 238 (c2 framed), 235 (regime-fragility), 230 (loss-tail = the wound).
**Next**: doc 240 — BET#2 strategy-library, gated on tagger-kappa ≥ 0.6, N ≥ 30/archetype/year, KS-separation.
