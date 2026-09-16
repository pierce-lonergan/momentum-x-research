# BAR-1 Timing Sweep Falsification Report

**Generated:** by `scripts/falsify_bar1_sweep.py` against `data/instrumentation/trade_attribution/`.
**Trigger:** last session's BAR-1 timing sweep produced a 6.6× lift at T+15min over prod baseline. This report runs 5 orthogonal stress tests designed to COLLAPSE the lift if it was a simulator artifact.

**Baseline 6.6× lift reference:** T+15min Σ ≈ +$4,786 / prod baseline +$719 = 6.65×.

---

## A.1 — Multi-seed variance (n=10 seeds, single-trade rng)

| Hold time | Mean Σ | Stdev | Min | Max | Stdev / Mean |
|---|---:|---:|---:|---:|---:|
| T+60s | $+2,260.47 | $0.00 | $+2,260.47 | $+2,260.47 | 0.00 |
| T+5min | $+4,355.98 | $0.00 | $+4,355.98 | $+4,355.98 | 0.00 |
| T+15min | $+4,785.52 | $0.00 | $+4,785.52 | $+4,785.52 | 0.00 |
| EOD | $-1,403.06 | $0.00 | $-1,403.06 | $-1,403.06 | 0.00 |

**A.1 verdict:** T+15min mean $+4,785.52 ± $0.00, T+60s mean $+2,260.47 ± $0.00. Lift mean / mean = **2.12×**.
- ⚠️ **TEST DEGENERATE:** stdev = 0 across all seeds. The arena fill model is deterministic in PRICE for market orders (rng only affects partial-fill qty, which the sweep ignores). Multi-seed variance test cannot exercise the simulator's stochasticity as currently wired. **This is itself a rig finding** — see §X. The test does NOT validate the lift; it surfaces that the simulator has no stochastic exit pricing under this codepath.

## A.2 — Slippage-multiplier stress

Same trades, scale arena's spread (and thus per-trade fill cost) by N×. If the 6.6× was driven by under-modeled slippage, larger multipliers should compress it.

| Multiplier | Σ T+60s | Σ T+5min | Σ T+15min | Σ EOD | Lift (T+15/T+60) |
|---|---:|---:|---:|---:|---:|
| 1.0x | $+2,260.47 | $+4,355.98 | $+4,785.52 | $-1,403.06 | 2.12× |
| 2.0x | $+2,118.45 | $+4,211.77 | $+4,667.70 | $-1,621.88 | 2.20× |
| 3.0x | $+1,975.68 | $+4,066.44 | $+4,549.48 | $-1,842.18 | 2.30× |
| 5.0x | $+1,691.17 | $+3,773.97 | $+4,311.69 | $-2,281.39 | 2.55× |

- T+15min Σ at 5× / 1× = 0.901 (uniform spread scaling preserves ratios; differential hold-time penalty would be needed to falsify properly)
- ⚠️ **TEST METHODOLOGICALLY LIMITED:** uniform spread-multiplier scales T+60s and T+15min Σ proportionally, so the ratio is preserved by construction. This test cannot falsify the lift via this mechanism; see A.5 for the differential-fade approach that CAN.

## A.3 — Per-trade decomposition (which trades drive the lift?)

Sorted by `lift_contribution_t15` (T+15min P&L − T+60s P&L). Top contributors carry the aggregate lift.

| Ticker | Date | Signal | T+60s | T+15min | Δ (lift contrib) |
|---|---|---|---:|---:|---:|
| SBLX | 2026-04-28 | NEUTRAL | $+8.38 | $+669.38 | $+661.00 |
| MAAS | 2026-04-22 | BULL | $+192.13 | $+812.95 | $+620.82 |
| LIDR | 2026-04-24 | BULL | $-26.85 | $+455.34 | $+482.19 |
| ONMD | 2026-04-24 | BULL | $+398.41 | $+829.46 | $+431.05 |
| SCNI | 2026-04-24 | BULL | $-223.65 | $+17.96 | $+241.61 |
| SEGG | 2026-04-28 | BULL | $-80.74 | $+104.88 | $+185.62 |
| AGPU | 2026-04-22 | BULL | $-14.90 | $+47.72 | $+62.62 |
| OGN | 2026-04-27 | STRONG_BULL | $+1,927.77 | $+1,920.88 | $-6.89 |
| ATER | 2026-04-28 | BULL | $+79.92 | $-73.05 | $-152.97 |

- Top single contributor: **SBLX** = 26% of aggregate lift
- Top 2 contributors: 51% of aggregate lift
- ✅ **DISTRIBUTED:** lift spread across multiple trades → genuine cross-trade signal.

## A.4 — Out-of-sample bar test (Dec 2025 - Feb 2026 synthesized entries)

Synthesized entry: first regular-hours bar (09:30 ET) of each session at tier-1 default sizing ($75K notional). Hold-time sweep on the same bar corpus arena uses for the in-sample sweep.

| Ticker | Date | qty | entry | T+60s | T+15min | EOD |
|---|---|---:|---:|---:|---:|---:|
| AMCI | 2025-12-16 | 6637 | $11.30 | $-442.69 | $-3,925.12 | $+12,052.13 |
| AEVA | 2026-01-06 | 4573 | $16.40 | $-344.35 | $+3,131.59 | $+7,679.90 |
| ABOS | 2026-01-28 | 25510 | $2.94 | $-267.85 | $+73.98 | $-4,785.68 |
| APPX | 2026-02-02 | 4249 | $17.65 | $-44.61 | $+847.25 | $-4,355.65 |
| AAOI | 2026-02-06 | 1874 | $40.01 | $+208.39 | $+225.25 | $+8,004.42 |

- Σ T+60s: $-891.11
- Σ T+15min: $+352.95
- Σ EOD: $+18,595.12
- OOS lift T+15/T+60: **-0.40×** (in-sample reference: 6.65×)
- ❌ **COLLAPSES OOS:** lift is specific to in-sample window → overfit to 4/22-4/28.

## A.5 — Adversarial gap-fade exit (50 bps/min after T+5min)

Replace exit fills with a worst-case model: every minute past T+5min the price drifts 50 bps adversely (against long position). Tests whether the lift survives if real-world adverse selection bites long holds.

| Hold time | Σ adversarial | vs uncalibrated baseline |
|---|---:|---|
| T+60s | $+2,260.47 | Δ $+0.00 |
| T+5min | $+4,142.46 | Δ $-213.52 |
| T+15min | $-723.03 | Δ $-5,508.55 |
| EOD | $-157,078.93 | Δ $-155,675.87 |

- Adversarial T+15/T+60 lift: **-0.32×**
- ❌ **COLLAPSE:** adversarial fade brings lift below 1.5× → real-world adverse selection erases the apparent edge.

---

## Aggregate verdict

| Test | Outcome |
|---|---|
| A.1 multi-seed variance | ⚠️ PARTIAL |
| A.2 slippage-multiplier stress | ⚠️ PARTIAL |
| A.3 per-trade decomposition | ✅ SURVIVES |
| A.4 out-of-sample test | ❌ COLLAPSES |
| A.5 adversarial gap-fade | ❌ COLLAPSES |

**Tally: 1 SURVIVE / 2 PARTIAL / 2 COLLAPSE.**

### Verdict: **COLLAPSES** (2 hard collapses ≥ 2 threshold)

The 6.6× BAR-1 timing lift was a simulator artifact. The specific failure modes that produced the apparent edge are documented above. Operational implication: the BAR-1 EXIT default at T+60s is **not** clearly suboptimal based on this rig; the apparent T+15min edge does not survive skeptical testing. Move on; do not chase BAR-1 timing as a candidate config.

_Verdict marker (machine-readable): COLLAPSES_