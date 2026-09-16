# Catalyst Gate Sweep — Pareto Frontier

**Generated:** by `scripts/sweep_catalyst_gate.py` against `data/trade_results.jsonl`.
**Trade window:** 2026-04-22 to 2026-04-28, deduped, intraday only.
**Arena fidelity at this sweep:** 0% (Block 2.1 calibration reverted; framework only)

> **PROVISIONAL** — this sweep uses RECORDED prod P&L per trade and asks which trades each candidate gate would have KEPT vs REJECTED. It does NOT use arena-replayed P&L (arena fidelity not yet earned). The result is a population-level policy comparison, not a strategy-level backtest.

---

## §1 — Trade tagging (news_agent classification at entry)

| Ticker | Date | P&L | news_signal | news_conf |
|---|---|---:|---|---:|
| AGPU | 2026-04-22 | $+0.00 | BULL | 0.49 |
| MAAS | 2026-04-22 | $+0.00 | BULL | 0.49 |
| XNDU | 2026-04-23 | $+0.00 | BULL | 0.36 |
| TRT | 2026-04-24 | $+0.00 | NEUTRAL | 0.20 |
| SCNI | 2026-04-24 | $+0.00 | BULL | 0.42 |
| LIDR | 2026-04-24 | $+421.12 | BULL | 0.37 |
| ONMD | 2026-04-24 | $+0.00 | BULL | 0.39 |
| OGN | 2026-04-27 | $+515.85 | STRONG_BULL | 0.61 |
| SEGG | 2026-04-28 | $+0.00 | BULL | 0.49 |
| SBLX | 2026-04-28 | $-157.75 | NEUTRAL | 0.00 |
| ATER | 2026-04-28 | $-60.25 | BULL | 0.49 |

**Total trades:** 11 | **Σ P&L:** $+718.97

## §2 — Sweep results

Two gate variants per threshold:
- **conf-only**: keep trade if news_conf ≥ threshold (any signal direction).
- **conf + non-NEUTRAL**: keep only if conf ≥ threshold AND signal ∈ {BULL, STRONG_BULL}.

| Threshold | Require BULL+ | Kept | Rej | Σ kept P&L | Σ rej P&L | Win-rate kept |
|---:|:---:|---:|---:|---:|---:|---:|
| 0.00 |   | 11 | 0 | $+718.97 | $+0.00 | 18% |
| 0.00 | ✓ | 9 | 2 | $+876.72 | $-157.75 | 22% |
| 0.20 |   | 9 | 2 | $+876.72 | $-157.75 | 22% |
| 0.20 | ✓ | 9 | 2 | $+876.72 | $-157.75 | 22% |
| 0.30 |   | 9 | 2 | $+876.72 | $-157.75 | 22% |
| 0.30 | ✓ | 9 | 2 | $+876.72 | $-157.75 | 22% |
| 0.40 |   | 6 | 5 | $+455.60 | $+263.37 | 17% |
| 0.40 | ✓ | 6 | 5 | $+455.60 | $+263.37 | 17% |
| 0.50 |   | 1 | 10 | $+515.85 | $+203.12 | 100% |
| 0.50 | ✓ | 1 | 10 | $+515.85 | $+203.12 | 100% |
| 0.60 |   | 1 | 10 | $+515.85 | $+203.12 | 100% |
| 0.60 | ✓ | 1 | 10 | $+515.85 | $+203.12 | 100% |

## §3 — Pareto frontier (max Σ kept P&L per trade-count)

| Trades kept | Best gate | Σ kept P&L | Win-rate |
|---:|---|---:|---:|
| 1 | conf≥0.50 | $+515.85 | 100% |
| 6 | conf≥0.40 | $+455.60 | 17% |
| 9 | conf≥0.00 + non-NEUTRAL | $+876.72 | 22% |
| 11 | conf≥0.00 | $+718.97 | 18% |

## §4 — Honest reading

**Sample size:** 11 trades is well below any threshold for statistical claims. Win-rate columns at small N's are not reliable.

**Pattern observable:**
- Worst-case gate kept Σ P&L: **$+455.60** (6 trades, conf≥0.40).
- Best-case gate kept Σ P&L: **$+876.72** (9 trades, conf≥0.00, non-NEUTRAL).

**Stop conditions checked:**
- ✅ No configuration rejects all trades.

**No promotion to prod from this sweep.** Halt switch stays on. Sweep results inform candidate configs only.

---

## §7 — Re-evaluation post rig-audit (2026-04-28 PM Block C)

**Context:** the rig forensic audit (`docs/replay_diffs/2026-04-28_error_decomposition.md`) found that the previous session's `arena_pnl` numbers were corrupted by qty multiplier errors (5.7-16.7× too big) and exit-semantics bugs (arena anchored to bar-open while prod exited via tranche limits / D245 close fills). Block B.1 shipped the qty fix; Σ |arena-prod Δ| dropped 70% from $3,374 to $1,040.

**Question for this re-eval:** were the catalyst gate Pareto conclusions corrupted by those rig bugs?

**Answer: NO. Conclusions hold.**

This sweep operates on `data/trade_results.jsonl` recorded `pnl` values directly — the prod broker's realized P&L per closed trade. It does NOT use `arena_pnl` from the replay rig at all. Rerunning the sweep post-fix produced **byte-for-byte identical** Pareto frontiers. The previous session's PROVISIONAL labeling was correct in spirit (arena fidelity not earned), but the specific concern that "the rig errors might have biased the gate conclusions" is now ruled out — the gate analysis was always sourced from prod P&L, never from arena P&L.

**Original PROVISIONAL label upgraded to: VALIDATED against prod P&L source (rig-independence confirmed).**

The "PROVISIONAL" caveat that REMAINS is the small-sample one: n=11 trades is far below significance. The Pareto frontier shape (rejecting NEUTRAL alone keeps Σ P&L = +$876.72 vs +$719 keeping all 11) is stable but the win-rate columns at small n are not.

**No change to the operational recommendation:** the catalyst gate (`reject news_signal ∈ {NEUTRAL, NO_SIGNAL}`) remains a candidate config. Halt switch stays on regardless. Promotion to prod requires more replay data + Block B.2 (exit semantics fix) so that ARENA-backed sweeps over different exit policies can corroborate.

**Audit completeness:** ✅ sweep re-run, ✅ identity check (zero diff), ✅ §7 documented.
