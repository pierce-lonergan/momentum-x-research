# BAR-1 Exit Timing Sweep — Arena-Driven (Block E)

**Generated:** by `scripts/sweep_bar1_timing.py` against trade attribution corpus + bar parquets.
**Trade window:** 4/22-4/28 deduped, intraday, with full attribution data.

> **PROVISIONAL — MODELED EXITS, NOT PROD-MIRROR.** This sweep uses arena's `AlpacaFillModel` to compute hypothetical exit prices at each candidate hold time. There is NO prod fill at T+90s for a trade that exited at T+60s — these are arena-modeled what-ifs. The relative ranking across hold times is informative; the absolute P&L numbers are bounded by arena's slippage modeling fidelity (Block 2.1 calibration was reverted on stop condition; that uncertainty propagates here).

> The CATALYST GATE SWEEP (`docs/sweeps/catalyst_gate_pareto.md`) was rig-independent. THIS sweep is rig-dependent. They cannot be combined into a single conclusion until arena fidelity is calibrated.

---

## §1 — Per-trade hold-time matrix

Modeled arena P&L at each hold time. Compare to `prod_pnl` (actual realized) for context.

| Ticker | Date | Signal | qty | entry | prod $ | T+30s | T+60s | T+90s | T+120s | T+300s | T+15min | EOD |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| AGPU | 2026-04-22 | BULL | 505 | $9.59 | $+0.00 | $-15 | $-15 | $+33 | $+33 | $+168 | $+48 | $-394 |
| MAAS | 2026-04-22 | BULL | 1652 | $11.73 | $+0.00 | $+192 | $+192 | $+374 | $+374 | $+751 | $+813 | $-2,205 |
| LIDR | 2026-04-24 | BULL | 5264 | $2.42 | $+421.12 | $-27 | $-27 | $-124 | $-124 | $-27 | $+455 | $-1,178 |
| ONMD | 2026-04-24 | BULL | 17173 | $1.15 | $+0.00 | $+228 | $+398 | $+398 | $+436 | $+314 | $+829 | $-915 |
| SCNI | 2026-04-24 | BULL | 9293 | $0.86 | $+0.00 | $-224 | $-224 | $-339 | $-339 | $+197 | $+18 | $-1,026 |
| OGN | 2026-04-27 | STRONG_BULL | 999 | $11.25 | $+515.85 | $+1,940 | $+1,928 | $+1,928 | $+1,910 | $+1,913 | $+1,921 | $+1,904 |
| ATER | 2026-04-28 | BULL | 3122 | $1.29 | $-60.25 | $-60 | $+80 | $+80 | $+189 | $+88 | $-73 | $-580 |
| SBLX | 2026-04-28 | NEUTRAL | 3490 | $3.03 | $-157.75 | $-70 | $+8 | $+8 | $+237 | $+606 | $+669 | $+196 |
| SEGG | 2026-04-28 | BULL | 9281 | $1.14 | $+0.00 | $-81 | $-81 | $+173 | $+173 | $+346 | $+105 | $+2,795 |

## §2 — Aggregate Σ P&L per hold time

| Hold time | Σ arena P&L | vs prod baseline |
|---|---:|---:|
| **prod actual** | n/a | **$+718.97** |
| T+30s | $+1,884.60 | $+1,165.63 |
| T+60s | $+2,260.47 | $+1,541.50 |
| T+90s | $+2,530.74 | $+1,811.77 |
| T+120s | $+2,888.30 | $+2,169.33 |
| T+5min | $+4,355.98 | $+3,637.01 |
| T+15min | $+4,785.52 | $+4,066.55 |
| EOD | $-1,403.06 | $-2,122.03 |

## §3 — Sliced by news_signal (small-sample caveat)

### BULL (n=7)

| Hold time | Σ arena P&L | mean per trade |
|---|---:|---:|
| T+60s (BAR-1 default) | $+324.32 | $+46.33 |
| T+5min | $+1,836.98 | $+262.43 |
| T+15min | $+2,195.26 | $+313.61 |
| EOD | $-3,503.49 | $-500.50 |

### NEUTRAL (n=1)

| Hold time | Σ arena P&L | mean per trade |
|---|---:|---:|
| T+60s (BAR-1 default) | $+8.38 | $+8.38 |
| T+5min | $+606.21 | $+606.21 |
| T+15min | $+669.38 | $+669.38 |
| EOD | $+196.14 | $+196.14 |

### STRONG_BULL (n=1)

| Hold time | Σ arena P&L | mean per trade |
|---|---:|---:|
| T+60s (BAR-1 default) | $+1,927.77 | $+1,927.77 |
| T+5min | $+1,912.79 | $+1,912.79 |
| T+15min | $+1,920.88 | $+1,920.88 |
| EOD | $+1,904.29 | $+1,904.29 |

## §4 — Honest reading

**What this sweep CAN say** (pattern-level, hold-time relative):
- Whether longer holds capture more upside on BULL signals (or whether momentum decays past T+60s)
- Whether NEUTRAL signals get worse with longer holds (consistent with the catalyst gate finding)
- Whether STRONG_BULL benefits from holding past the BAR-1 default

**What this sweep CANNOT say** (absolute, strategy-level):
- Whether the strategy with hold-time X would profit at any specific number — arena's slippage uncertainty makes the absolute number untrustworthy
- Whether the prod-actual exits were near-optimal — can't compare modeled-exit-at-T+N to a prod fill that didn't happen at T+N
- Anything about the LIDR carry-class trades (filtered out of this sweep — only intraday trades with full attribution)

**Stop conditions checked:**
- ✅ EOD-hold Σ within reasonable range vs prod baseline.

**No promotion to prod from this sweep.** Halt switch stays on.
