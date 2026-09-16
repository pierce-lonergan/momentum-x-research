# 238 — Structural-lever audit (c2/c3/c4): no cheap lever creates a profitable base; c2 is the one real candidate left

**Author**: Claude Opus 4.8
**Date**: 2026-06-03 (overnight)
**Mandate**: Pierce — after BET#1 closed, "full attention on (b) the structural levers," framed by the hard
truth: **the honest base is a losing strategy** (−$8K realized / 15 clean sessions ≈ −134%/yr), so every
lever is only valuable if it can **create a profitable base** — not merely shrink the variance of the loss.

---

## 0. The frame (Pierce's, kept front-and-center)
The phantom fix (226-229) removed the *inflation hiding* the true number; it did not create edge. b2
closed as a no-op (= trading smaller). So the question is no longer "what multiplier" — it is **"is there
ANY profitable base, and what creates one."** A lever earns attention only if it can flip the *sign* of
expectancy, not just reduce loss-variance.

## 1. c4 — EOD-flatten enforcement audit → INTEGRITY lever, not base-creating
From 26 EOD reports: positions were open at force-close on ~11 sessions; **qty_drift>0 on 10/26**. The
not-flat sessions cluster with qty-drift — i.e. the **doc 226-229 phantom-position class**, already fixed.
(Data caveat: the `eod_recon.broker_positions` "still-open-after" field is unpopulated/None, so a clean
overnight-carry count isn't recoverable from the reports; the qty-drift incidence is the usable signal.)
**Verdict:** EOD-flatten is an *integrity* lever (prevents phantom/naked carry) — it makes P&L honest, it
does not add expectancy. Forward KPI (doc 237): drift/carry sessions → 0 post-226-229 deploy.

## 2. c3 — entry-timing audit → NULL (not the lever the doc-230 sim hoped)
30 real trades matched to their signal time (earliest journal eval that ticker-day):
- entry lag (signal→fill): **median 14 min**, mean 19, p90 48, max 75.
- **corr(lag, pnl) = +0.05; corr(lag, win) = −0.03** — ~zero.
- terciles: FAST (−1 min) +$12 mean / 30% win, MID (12 min) −$124 / 20%, SLOW (46 min) **+$97 / 30%** —
  no monotonic "late entries lose"; SLOW is nominally *best*.

**Verdict:** on the real trades, entry lag is small and **uncorrelated with outcome** — entry-timing is
**not** an obvious base-creating fix. (doc-230's "entry timing 5× exit" was a *simulation* of optimal-vs-
actual entry, not a lag-vs-P&L correlation; the real-trade evidence does not support it. Caveat: n=30,
$-P&L not %, signal-time = first journal eval which may understate true premarket-scan lag.)

## 3. c2 — loss-tail cap → the ONE remaining lever that could plausibly create a base (UNTESTED)
This is **not** a no-op like b2. A symmetric variance filter (b2) cuts both tails and can't flip the sign.
But a **catastrophic-loss cap** is asymmetric *if the worst losers don't recover*: doc-230's wound is the
30% win × ~1.0 W/L ratio with a fat left tail (LIDR −$6K, ASNS −$6.2K, CANF −$6.1K — all *single names*
that ran far past any sane stop). Capping those **raises the W/L ratio and the mean** — it can move
expectancy, not just variance. At 30% win, lifting W/L from ~1.0 to >2.3 flips the sign. **This is the
only lever audited that can plausibly create a profitable base.**
- **The test (next experiment):** for each historical entry, reconstruct the intraday **max-adverse-
  excursion** (from minute bars) and the eventual outcome; sweep hard-stop levels; find the level that
  cuts the worst-N% outcomes while **preserving the mean of the rest** (does the realized W/L / expectancy
  improve, cross-regime, pre-registered + CI?). Needs the per-trade MAE tape (minute-bar reconstruction —
  the doc-235 corpus has the paths; join to actual trades or simulate on the gapper universe).
- **Honest prior:** even this may fail — the session's pattern is that every lever evaporates — but it is
  the one with a *mechanism* to flip the sign, so it earns the rigorous test the others didn't survive.

## 4. Synthesis — where the whole arc (231-238) lands
| lever | verdict |
|---|---|
| predictive selection alpha (231-235) | DEAD — sign-reverses out-of-period |
| model-as-tool overlay (236) | NO-OP — b2 = trading smaller (Sharpe-invariant) |
| phantom-P&L leak (237) | REAL (+33%/~$44K-yr) but ALREADY FIXED (226-229); integrity not edge |
| EOD-flatten (c4) | integrity lever (the 226-229 class), not base-creating |
| entry-timing (c3) | NULL — lag uncorrelated with outcome |
| **loss-tail cap (c2)** | **the one untested lever with a mechanism to flip the sign — next experiment** |

**The honest bottom line:** nothing audited so far creates a profitable base on the current low-float
gapper universe; the apparent edges were variance, artifacts, or phantom inflation. **The base is
structurally ~break-even-to-losing.** Two honest paths remain: (a) the **loss-cap experiment (c2)** — the
last lever with sign-flipping potential, tested at the doc-235 rigor bar; and (b) if c2 also fails,
**accept that this universe has no extractable edge for us** and either change the selection universe
(different catalyst/float/liquidity regime) or treat the system as a hardened paper research instrument.
The durable, *confirmed* win of this whole session is **execution integrity** — phantom P&L killed,
quantified (+$44K/yr distortion), regression-tested. That doesn't make money, but it makes the P&L
**honest**, which is the precondition for ever finding a real edge.

**No live change.** **Initiator**: Claude Opus 4.8 (1M ctx). **Basis**: 26 EOD reports (c4), 30 real
trades × journal signal-times (c3), doc-230 loss-distribution (c2 motivation). **Predecessors**: 230
(execution = the lever; loss-tail = the wound), 235/236 (alpha + overlay dead), 237 (phantom fixed).
