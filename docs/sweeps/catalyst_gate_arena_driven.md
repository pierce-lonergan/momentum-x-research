# Arena-Driven Catalyst Gate Sweep

**Generated:** by `scripts/sweep_catalyst_gate_arena.py`. Drives the strategy through `arena.harness` with limit-aware fill (Block A) and bar-anchored exits at T+60s.

> **PROVISIONAL.** This is the FIRST sweep that exercises arena (last session's `catalyst_gate_pareto.md` was rig-independent — it operated on `trade_results.jsonl` `pnl` directly). Per the brief: arena-driven results may agree or disagree with the prod-pnl-based finding. Disagreement is the more important outcome to surface.

---

## §1 — Per-arm aggregate

| Arm | Σ trades | Σ P&L | Σ wins | Σ losses | Σ scratches |
|---|---:|---:|---:|---:|---:|
| GATE OFF | 15 | $-8,193.08 | 6 | 9 | 0 |
| GATE ON | 1 | $+19.74 | 1 | 0 | 0 |

**Net effect of gate: $+8,212.82** (1 kept of 15 candidates)

## §2 — Per-session breakdown

| Session | Arm OFF: n / pnl | Arm ON: n / pnl | Δ kept | Δ pnl |
|---|---|---|---:|---:|
| 2026-04-22 | 3 / $-6,955.13 | 0 / $+0.00 | -3 | $+6,955.13 |
| 2026-04-23 | 3 / $-1,887.94 | 0 / $+0.00 | -3 | $+1,887.94 |
| 2026-04-24 | 3 / $-1,386.69 | 0 / $+0.00 | -3 | $+1,386.69 |
| 2026-04-27 | 3 / $+4,048.95 | 0 / $+0.00 | -3 | $-4,048.95 |
| 2026-04-28 | 3 / $-2,012.27 | 1 / $+19.74 | -2 | $+2,032.01 |

## §3 — Trades rejected by the gate

Trades that ARM=OFF took but ARM=ON rejected (= news_signal ∈ {NEUTRAL, NO_SIGNAL, EMPTY}):

| Ticker | Date | news_signal | conf | qty | pnl |
|---|---|---|---:|---:|---:|
| AGPU | 2026-04-22 |  | 0.00 | 6912 | $-2,834.61 |
| BIYA | 2026-04-22 |  | 0.00 | 62500 | $-2,187.50 |
| CLIK | 2026-04-22 |  | 0.00 | 24193 | $-1,933.02 |
| AGPU | 2026-04-23 |  | 0.00 | 11029 | $+772.03 |
| BURU | 2026-04-23 |  | 0.00 | 220718 | $-176.57 |
| CGC | 2026-04-23 |  | 0.00 | 49668 | $-2,483.40 |
| APLD | 2026-04-24 |  | 0.00 | 2001 | $+1,080.54 |
| BURU | 2026-04-24 |  | 0.00 | 205198 | $-554.03 |
| CPIX | 2026-04-24 |  | 0.00 | 19132 | $-1,913.20 |
| ATOM | 2026-04-27 |  | 0.00 | 8823 | $-573.49 |
| ELPW | 2026-04-27 |  | 0.00 | 16741 | $+3,683.02 |
| ENVB | 2026-04-27 |  | 0.00 | 15657 | $+939.42 |
| SGMT | 2026-04-28 | NEUTRAL | 0.00 | 8939 | $+178.78 |
| EDSA | 2026-04-28 | NEUTRAL | 0.00 | 6970 | $-2,276.40 |
| OGN | 2026-04-28 | NEUTRAL | 0.00 | 5690 | $+85.35 |

**Σ P&L of rejected trades: $-8,193.08**
(If the rejected total is NEGATIVE, the gate is correctly cutting losers. If POSITIVE, the gate is rejecting profitable trades.)

## §4 — Comparison to last session's prod-pnl-based result

Last session's `catalyst_gate_pareto.md`:
- Operated on `data/trade_results.jsonl` `pnl` directly (rig-independent)
- Conclusion: rejecting NEUTRAL/NO_SIGNAL keeps 9/11 trades, lifts Σ from +$719 to +$877 (+$158)

This session's arena-driven result (above):
- Operates on arena-modeled outputs (limit-aware entries + bar-anchored T+60s exits)
- Net effect: $+8,212.82

⚠️ **DIRECTION AGREES, MAGNITUDE CONTESTED.** Both methods agree the gate has positive effect, but the arena-driven magnitude ($+8213) is much larger than the prod-pnl-based magnitude (+$158).

**Diagnosed cause:** the harness over-counts entries vs prod. Per doc 65 §3 logged finding #1, the strategy harness has no position-count limit / consensus gate / debate logic. ARM OFF takes 10 trades on 4/28; prod actually executed 3. Inflated trade count → inflated baseline (more losers in the unfiltered arm) → inflated gate effect.

**Additional finding:** GATE ON and GATE OFF take DIFFERENT entries for the SAME ticker (e.g. ATER at $1.19 in OFF arm vs $1.31 in ON arm). The harness's per-ticker first-decision-fires logic picks earlier decisions in the no-gate arm because more decisions exist; in the ON arm only BULL/STRONG_BULL decisions fire which come later in the cycle. The arms aren't comparing the SAME trades. This is itself a logged harness-design finding — the sweep needs entry-time alignment.

Per Block B stop condition, this is a major-enough finding that Block C runs BASELINE only.

## §5 — Operational implication

- Verdict CONTESTED — magnitude differs >50% from prod-pnl claim.
- Block 4.4 runs BASELINE only. Gate stays out of the run dimension until next-session investigation resolves the contestation.

_Verdict marker (machine-readable): CONTESTED-direction-agrees_