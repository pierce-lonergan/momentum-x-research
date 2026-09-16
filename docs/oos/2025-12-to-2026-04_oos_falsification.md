# OOS Run Falsification — Required by SUSPECT-bucket rule

**Trigger:** baseline OOS Sharpe = +3.776 → SUSPECT bucket → falsification mandatory before claim.
**Baseline:** 255 trades over 86 session days, total P&L $+154,502.71, annual Sharpe **+3.776**.

---

## §1 — A.5 adversarial fade test (per-brief mandatory)

Apply 50 bps/min adverse drift after T+5min from entry. OOS run uses T+60s exits.

- Trades affected by fade (hold > 5 min): **0 of 255** = 0.0%
- Sharpe under fade: **+3.776** (baseline: +3.776)
- Total P&L under fade: $+154,502.71

⚠️ **A.5 STRUCTURALLY DOES NOT APPLY to this OOS run.** All trades exit at T+60s (1 min); the fade kicks in at T+5min. The brief's discipline rule ("run A.5 if Sharpe > 2.0") was written for the BAR-1 timing sweep where multiple hold times existed. For an OOS run with single-policy T+60s exits, A.5 is the wrong falsification — it cannot detect the kind of long-hold under-modeling it was designed for.

**This is itself a logged finding**: the discipline framework's falsification suite needs to be exit-policy-aware. A.5 only catches BAR-1-class issues; OOS runs need their own suite.

Per discipline rule, however, the test was RUN as required. The result (no effect) is documented above.

## §2 — Shuffle test (entry-exit re-pairing, n=100 iters)

Per-trade re-pairing: keep entries in natural order, permute exit prices across the pool, recompute Sharpe. If headline Sharpe survives shuffling, the result isn't from autocorrelation/sequencing; if shuffled Sharpe approaches the headline, the strategy 'edge' is illusory.

- Mean shuffled Sharpe: **+7.052** ± 0.820
- Range: [+5.341, +9.164]
- Baseline Sharpe: +3.776

❌ **SHUFFLE TEST COLLAPSES** the headline. The shuffled mean Sharpe (+7.052) is comparable in magnitude to the baseline (+3.776). Random entry-exit pairings produce similar Sharpe → the headline isn't from systematic strategy edge; it's from the underlying price distribution of the trade pool.

## §3 — Stratification (the actual headline)

Per the brief, the per-data-completeness stratification matters more than the aggregate.

| Stratum | n | Total P&L | Sharpe | Verdict |
|---|---:|---:|---:|---|
| synthesized_no_news | 225 | $+164,761.13 | +4.364 | encouraging — but check stratum data quality |
| partial_news_only | 27 | $-8,246.15 | -4.270 | **LOSING** |
| full_decision_row | 3 | $-2,012.27 | +0.000 | no signal |

❌ **STRATIFICATION COLLAPSES THE AGGREGATE HEADLINE.** Per the brief's exact warning: 'if aggregate Sharpe is encouraging but full_decision_row is < 0.5 and synthesized_no_news is > 1.5, the headline number is being dragged up by the lowest-quality data.'

The aggregate Sharpe of +3.776 is fictional. The strata where we have ACTUAL news data (full and partial) are LOSING money in arena. Only the synthesized stratum (where we make up gap=10%/RVOL=2.0 placeholders + take whatever 3 sub-$15 high-volume tickers happen to be in the bar corpus) shows positive Sharpe — and it shows it strongly.

## §4 — Aggregate verdict

**HEADLINE OVERTURNED.** The +3.776 aggregate Sharpe is rejected by the per-data-completeness stratification. The stratification is the truth: in the high-quality data strata (where we have actual news classifications), the strategy LOSES money. The aggregate's positive number is an artifact of the synthesized_no_news stratum where placeholder filter values + bar-corpus selection bias produce false positives.

**Operational implication: NO DEMONSTRATED EDGE on this rig + corpus.** Halt switch stays on. Per the SUSPECT-bucket discipline, the failed falsification IS the headline.

_Verdict marker (machine-readable): OVERTURNED_