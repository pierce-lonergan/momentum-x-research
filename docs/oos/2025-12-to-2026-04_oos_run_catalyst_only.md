# 86-Session OOS Run — 2025-12 to 2026-04

**Generated:** by `scripts/run_block_44_oos.py` driving `arena.harness`. **PROVISIONAL.** This is the FIRST 86-session OOS run on the rig.

---

## §0 — TL;DR — VERDICT FROM FALSIFICATION (read this first)

Aggregate Sharpe = +3.776 (SUSPECT bucket). Per discipline rule, falsification was applied (`docs/oos/2025-12-to-2026-04_oos_falsification.md`). **VERDICT: HEADLINE OVERTURNED.**

Three converging tests reject the aggregate as fictional:
1. **Stratification** (the brief's exact warning): synthesized_no_news (n=225) Sharpe +4.36 carries all the apparent edge; full_decision_row (n=3) and partial_news_only (n=27) are losing/zero. **In the high-quality strata where we have actual news data, the strategy LOSES money.**
2. **Shuffle test** (random entry-exit re-pairing, 100 iters): shuffled mean Sharpe +7.05 — HIGHER than the baseline +3.78. Random pairings beat the strategy → the 'edge' is from the bar corpus's price distribution, not from strategy decisions.
3. **A.5 adversarial fade**: structurally inapplicable to T+60s exits (fade threshold is 5 min). Logged as a discipline-suite finding: A.5 needs exit-policy-awareness for OOS runs.

**OPERATIONAL IMPLICATION: NO DEMONSTRATED EDGE on this rig + corpus.** Halt switch stays on. Per the SUSPECT-bucket discipline, the failed falsification IS the headline.

---

## §1 — Aggregate (FICTIONAL — see §0)

- **Trades**: 3 (over 1 session days)
- **Total P&L**: $-2,012.27
- **Win rate**: 66.7% (2 wins / 1 losses)
- **Avg win**: $+132.06 / **Avg loss**: $-2,276.40
- **Profit factor**: 0.116
- **Max drawdown**: $2,276.40
- **OOS Sharpe (annualized)**: **+0.000**

### Suspect-range bucket: **no_edge**

Pre-committed (plan doc 58 §11):
- < 0.5 → no demonstrated edge; halt stays on
- 0.5-1.5 → realistic 'needs more work but not hopeless'; do not size up
- 1.5-2.0 → encouraging but not yet defensible at single-shot OOS
- > 2.0 → SUSPECT simulator exploit; A.5 adversarial fade test required before claim

## §2 — Per-data-completeness stratification (more important than aggregate)

| Stratum | n trades | n days | Total P&L | Win rate | Sharpe |
|---|---:|---:|---:|---:|---:|
| full_decision_row | 3 | 1 | $-2,012.27 | 66.7% | +0.000 |
| partial_news_only | 0 | 0 | $0.00 | n/a | n/a |
| synthesized_no_news | 0 | 0 | $0.00 | n/a | n/a |

**The stratification is the deliverable, not the aggregate.** Per the brief: 'if aggregate Sharpe is encouraging but full_decision_row is < 0.5 and synthesized_no_news is > 1.5, the headline number is being dragged up by the lowest-quality data.'

## §3 — Per-session breakdown

| Session | Mode | Strata | n trades | Session P&L |
|---|---|---|---:|---:|
| 2025-12-11 | policy | synthesized_no_news | 0 | $+0.00 |
| 2025-12-12 | policy | synthesized_no_news | 0 | $+0.00 |
| 2025-12-15 | policy | synthesized_no_news | 0 | $+0.00 |
| 2025-12-16 | policy | synthesized_no_news | 0 | $+0.00 |
| 2025-12-17 | policy | synthesized_no_news | 0 | $+0.00 |
| 2025-12-18 | policy | synthesized_no_news | 0 | $+0.00 |
| 2025-12-19 | policy | synthesized_no_news | 0 | $+0.00 |
| 2025-12-22 | policy | synthesized_no_news | 0 | $+0.00 |
| 2025-12-23 | policy | synthesized_no_news | 0 | $+0.00 |
| 2025-12-24 | policy | synthesized_no_news | 0 | $+0.00 |
| 2025-12-29 | policy | synthesized_no_news | 0 | $+0.00 |
| 2025-12-30 | policy | synthesized_no_news | 0 | $+0.00 |
| 2025-12-31 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-05 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-06 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-07 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-08 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-09 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-12 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-13 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-14 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-15 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-16 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-21 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-22 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-23 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-26 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-27 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-28 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-29 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-01-30 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-02 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-03 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-04 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-05 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-06 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-09 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-10 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-11 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-12 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-13 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-18 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-19 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-20 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-23 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-24 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-25 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-26 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-02-27 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-02 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-03 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-04 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-05 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-06 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-09 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-10 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-11 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-12 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-13 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-16 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-17 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-18 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-19 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-20 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-23 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-24 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-25 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-26 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-27 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-30 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-03-31 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-04-01 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-04-02 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-04-07 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-04-08 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-04-09 | policy | synthesized_no_news | 0 | $+0.00 |
| 2026-04-10 | policy | partial_news_only | 0 | $+0.00 |
| 2026-04-13 | policy | partial_news_only | 0 | $+0.00 |
| 2026-04-14 | policy | partial_news_only | 0 | $+0.00 |
| 2026-04-15 | policy | partial_news_only | 0 | $+0.00 |
| 2026-04-17 | policy | partial_news_only | 0 | $+0.00 |
| 2026-04-22 | policy | partial_news_only | 0 | $+0.00 |
| 2026-04-23 | policy | partial_news_only | 0 | $+0.00 |
| 2026-04-24 | policy | partial_news_only | 0 | $+0.00 |
| 2026-04-27 | policy | partial_news_only | 0 | $+0.00 |
| 2026-04-28 | decision_row | full_decision_row | 3 | $-2,012.27 |

## §4 — Caveats (the doc's most-important section)

1. **Limit-aware fills are MVP.** Trades where the limit price was outside bar range at entry minute are marked failed-to-fill (conservative under-count). Multi-bar walk-forward deferred.
2. **Slippage calibration v2 is identity.** Real fit requires intra-bar tick data we don't have.
3. **Exits**: T+60s bar-anchored where no prod-mirror truth available. Modeled exit fidelity is bounded by Block A falsification (BAR-1 timing COLLAPSES verdict suggests modeled exits over-reward long holds).
4. **Decision_row coverage is 4/28-only**. Other sessions use policy-mode candidate detection (price + liquidity filter, gap/RVOL approximated).
5. **Bar coverage**: backfilled for 4/22-4/28; pre-4/22 coverage is whatever the recorder captured at the time (D121 dynamic-subscription gaps possible — see doc 61).
6. **Consensus stub** mocks the n=3 ensemble (news + gap + rvol); does not replicate prod's full debate logic / kelly sizing / position-tier rules.
7. **Position-count limit**: configurable, default 3 (mirrors prod's typical ceiling). Sensitivity to this parameter not yet swept.
8. **86 sessions ≈ 4 months**. Not n=multi-year. The Sharpe number's confidence interval is wide.
9. **Halt switch stays ON.** No live config promotions regardless of this run's verdict.

## §5 — Verdict + operational implication

**No demonstrated edge** on this rig + corpus. Halt switch stays on indefinitely; research continues. The negative result is documented with the same rigor as a positive one would have been.
