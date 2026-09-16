# 236 — BET#1 closure test: two sign-decoupled reframes of the model-as-tool hypothesis (PRE-REGISTERED)

**Author**: Claude Opus 4.8
**Mandate**: Pierce — "Test two reframes of the risk-filter/model-as-tool hypothesis using the doc-235
corpus. Pre-register before running. The literal original risk-filter ('veto low-P faders') is logically
broken by doc 235 — do NOT test it. If both reframes fail, BET#1 closes fully."

**Inheritance from doc 235:** the model's *sign* relationship to forward returns is **regime-unstable**
(high-P profitable in May-2026, unprofitable + CI-significant-negative in 2024-25). So any further use must
clear **regime-stability**, not just edge-presence. Both reframes below are deliberately **sign-decoupled**.

---

## ⛔ PRE-REGISTRATION — written 2026-06-02 23:32 EDT, BEFORE any (b1)/(b2) result existed. Binding.

**Data:** the doc-235 corpus exactly as-is (`exit_labels_cross_regime.parquet`, 10,786 ticker-days,
2024-2026). **Mode-B CPCV-refit OOS predictions only** (no leakage). No new data work. No dial-tuning
after results.

### REFRAME (b1) — MODEL AS VARIANCE PREDICTOR (not direction)
- **Hypothesis:** `|P(continue) − 0.5|` predicts realized intraday *volatility* (direction-agnostic), and
  is therefore usable as a position-sizing input.
- **Test:** bucket ticker-days by `|P − 0.5|` into 5 quantiles; per bucket compute realized 30-min
  absolute return and realized 30-min range, with bootstrap CIs. Run in 2024, 2025, 2026 (Mode-B OOS) AND
  pooled. Rank correlation = Spearman(`|P−0.5|`, realized-vol) per year + pooled.
- **SURVIVES:** monotonic `|P−0.5|`→realized-vol with **CI-significant Spearman in ALL THREE years AND
  pooled, consistent sign**, Bonferroni-corrected across (3 years + pooled) = α 0.05/4 = 0.0125.
- **FAILS:** any year non-significant or sign-inconsistent.

### REFRAME (b2) — LOSS-TAIL FILTER, DIRECTION-AGNOSTIC
- **Hypothesis:** SOME threshold partition on `P(continue)` reduces the realized P5/P10 loss tail without
  proportionally reducing the mean, across all three years.
- **Test:** sweep thresholds {0.1…0.9}. For each threshold × direction (keep-above / keep-below), per year:
  mean and P5/P10 of realized 30-min return for the kept partition vs all; "tail-cut efficiency" =
  (mean retained) / (P5 improvement); bootstrap CI per year.
- **SURVIVES:** at least one (threshold, direction) produces, in ALL THREE years, **CI-significant P5
  improvement with mean-retention ratio > 1.0** (more tail cut than mean lost), Bonferroni across
  (thresholds × years × direction).
- **FAILS:** no threshold survives the cross-year test.

### DECISION RULES
- **(b1) SURVIVES** → P(continue) is a variance input for sizing; promote to a Sharpe-on-sizing test (separate doc).
- **(b2) SURVIVES** → flag the specific threshold/direction as a risk overlay; promote to live-tape backtest.
- **BOTH FAIL** → **BET#1 closes fully.** The model is a research artifact, not a tool. All bandwidth → the
  structural-lever program (phantom-P&L audit doc 237, etc.). **Do not propose a third reframe.**

### HARD RULES
Pre-registration binding; doc-235 corpus as-is; Mode-B OOS only; Bonferroni/Holm on the full family, report
pre- and post-correction; no live change; if both fail, write the closure cleanly — no further iteration.

---

## RESULTS (run 2026-06-02 23:33 EDT; nothing above this line changed)
`scripts/reframe_audit_doc236.py`, Mode-B CPCV-refit OOS, 10,552 ticker-days.

### (b1) variance-predictor — FAILS as pre-registered (but informatively)
Spearman(`|P−0.5|`, abs 30-min return): **−0.422 (2024), −0.405 (2025), −0.470 (2026), −0.428 (pooled)**,
all p<0.0001. Monotonic quintiles: Q1 (P≈0.5, "uncertain") abs-move **7.80%** → Q5 (P near 0/1,
"confident") abs-move **1.79%**. The relationship is strong, monotonic, and **stable across all 3 years**
— but it is the **INVERSE** of the pre-registered hypothesis (I predicted confident=volatile; reality is
**confident = calm**). Per the hard rules (no post-hoc sign flip, no third reframe) **b1 FAILS as specified.**
I note the inverse only because it is the *same underlying signal* that powers b2 (below) — not to chase it.

### (b2) direction-agnostic tail-filter — SURVIVES the pre-registered cross-year test
"Keep-below" thresholds **0.1 / 0.2 / 0.3 / 0.4** each produce a CI-significant P5 loss-tail improvement
in **all 3 years**, Bonferroni-corrected (family=54, α=0.0009), with mean retained:

| keep-below | 2024 P5-imp | 2025 P5-imp | 2026 P5-imp |
|---|---|---|---|
| P<0.3 | +4.6% [+3.6,+5.5] | +4.0% [+3.6,+4.8] | +3.1% [+2.0,+3.9] |

Characterization of the survivor (keep-below P<0.3, absolute levels):
| year | kept % | mean kept vs all | **P5 kept vs all** | P95 kept vs all |
|---|---|---|---|---|
| 2024 | 43% | +0.13% vs −0.34% | **−5.7% vs −10.4%** | +6.3% vs +9.9% |
| 2025 | 48% | +0.04% vs −0.12% | **−5.7% vs −9.8%** | +5.8% vs +9.6% |
| 2026 | 66% | −0.09% vs −0.10% | **−4.8% vs −7.8%** | +5.0% vs +7.5% |

**Honest read:** the filter ~**halves the left tail** (P5) — but it ~symmetrically cuts the **right tail**
(P95) too. So this is fundamentally **VARIANCE REDUCTION**, not a tail *asymmetry* edge. The mean is
*retained* (slightly improved, because the high-P names it drops are the doc-235 underperformers), but the
base mean is ≈0 (these are gappers). It is a **risk/variance-control OVERLAY**, not standalone alpha.

### DECISION (per the pre-registered rules)
- **b1: FAILS.** **b2: SURVIVES.** → BET#1 does **NOT** fully close.
- **The model's one cross-regime-STABLE, regime-robust use is variance / loss-tail control** (b1-inverse and
  b2 are the same signal). It is **dead for direction/selection** (doc 235) and **alive only as a
  variance-control overlay** — which is precisely doc-230's wound (the catastrophic loss tail: 30% win ×
  ~1.0 W/L, LIDR −$6K). Halving P5 while holding the mean is the right shape of tool for that wound.
- **PROMOTION (per the rule "b2 survives → backtest on live-policy tape"):** flag **keep-below P(continue)
  ≈0.3 as a candidate variance/tail-control overlay**, and test it on the **actual live-trade tape** — does
  applying it to what we *really traded* cut our realized drawdown/loss-tail without killing realized mean?
  That backtest depends on the live-trade tape, which is exactly what the **phantom-P&L audit (doc 237)**
  assembles — so 237 is run next and feeds this.

### Honest caveats (do not over-sell)
1. b2's edge is **mechanical variance reduction** (cuts both tails); the *asymmetry* (mean retained) is
   small and rides on doc-235's high-P-underperforms, which itself is direction-fragile — so the mean
   benefit may be the weakest part. The **variance/tail reduction** is the robust part.
2. It is an **overlay**, not alpha — only valuable on top of a profitable base strategy. We don't have a
   confirmed profitable base yet (that's the structural-lever program).
3. Not deployed; needs the live-tape gate. **No live change.**

**Initiator**: Claude Opus 4.8 (1M ctx). **Basis**: `scripts/reframe_audit_doc236.py` on the doc-235
corpus, pre-registered above. **Predecessors**: 235 (direction killed OOP), 230 (loss-tail = the wound),
198 (variance-vs-signal). **Next**: doc 237 phantom-P&L audit (assembles the live tape the b2 overlay needs).

---

## ADDENDUM (2026-06-02 23:5x) — Pierce's no-op sanity check: **b2 CLOSES too.**
Pierce's sharp catch: "halves both tails with mean retained" is *mechanically what trading smaller does.*
A flat size cut leaves **Sharpe invariant**, so b2 has signal-beyond-size *only if* Sharpe(b2-kept) >
Sharpe(full pool). Result (`/tmp/b2_sanity.py`, bootstrap 95% CI on the Sharpe difference, per year):

| year | pool Sharpe | b2 Sharpe | diff | 95% CI | verdict |
|---|---|---|---|---|---|
| 2024 | −1.88 | +0.13 | +2.00 | [−0.79, +5.04] | incl 0 |
| 2025 | −0.77 | −0.21 | +0.56 | [−2.23, +3.44] | incl 0 |
| 2026 | +1.90 | +2.06 | +0.16 | [−6.58, +5.99] | incl 0 |
| pooled | −1.08 | +0.18 | +1.26 | [−0.68, +3.19] | incl 0 |

**The Sharpe difference includes zero in all three years AND pooled.** b2 is **statistically
indistinguishable from a flat size cut** — a no-op. Its doc-236 "survival" was purely the tail-symmetric
variance reduction (= trading smaller); the small mean-retention is not Sharpe-significant and rides on the
regime-unstable direction signal doc-235 killed. **→ b2 CLOSES. BET#1 has ZERO deployable use. The model is
a research artifact, full stop.** (This does not violate the pre-registration — the pre-registered b2 test
(P5-tail-cut) passed; this *sharper* follow-up shows that metric was insufficient to establish deployable
value. More skepticism, not less.)

**The framing that matters (Pierce):** the honest base is a **losing strategy** (−$8K realized / 15
sessions ≈ −134%/yr; pool Sharpe −1.88/−0.77 in 2024-25). The phantom fix (226-229) removed the *inflation
hiding* that number — it did not create edge. Every remaining lever (entry-timing, EOD-flatten, loss-cap)
is a **multiplier on a profitable base, which is a precondition we do not yet have.** Tail-control on a
losing system shrinks the loss variance; it does not flip the sign. **The next real question is not "what
multiplier" — it is "is there ANY profitable base, and what would create one."**
