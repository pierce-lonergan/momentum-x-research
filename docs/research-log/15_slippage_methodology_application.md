# 15 — Applying the Alimoradian-Derived Slippage Methodology to MOMENTUM-X

**Mon 2026-04-20 (evening, post-bug-sweep) — planning document, no code.**

Source research: `compass_artifact_wf-e274cbca-f07b-401e-b224-b38431187ede_text_markdown.md`
("Live-fill slippage methodology for microcap gap-up momentum at $5M AUM").

This document maps that methodology against MOMENTUM-X's current capability
surface, identifies the gaps, and stages the work needed before any
calibration attempt. **Nothing here is a code change.** Every claim about
"the system has X" or "the system lacks Y" is sourced from the
exploration audit completed tonight (file paths and line ranges in §4).

---

## 0. TL;DR — three findings, in priority order

**Finding 1 — The headline is unfavorable, and it must be named clearly.**
At the central parameter set the research derives, **$5M AUM is not
supported with 7% gross per-trade alpha** in the current microcap gap-up
universe. The honest ceiling at present sizing (5% per position) is
roughly **$1.5M–$2.5M AUM**. Reaching $5M requires *one* of:
(a) lowering per-position allocation to ≤2.5% (i.e. $125K per trade),
(b) migrating the universe upward to $5M–$50M ADV mid-cap names, or
(c) a materially better realized γ than the prior would predict — and
even (c) helps less than the user's framing suggests, because **permanent
impact (η_perm), not γ, is the dominant scaling parameter** at
Q/V_τ ≈ 50%. This inverts the framing we've been carrying.

**Finding 2 — Calibrating to the methodology requires four
infrastructure capabilities we currently lack.** The system has rich
trade journaling and two slippage models (Almgren-style + D196 tiered),
but cannot yet measure the quantities the methodology demands:
quote-stream capture at the order boundary, per-partial-fill tick
persistence, multi-horizon post-trade price tracking, and a persisted
matched-cohort baseline. None are exotic. All four block the calibration.
Estimated total instrumentation cost: ~3-5 person-days, well-scoped.

**Finding 3 — The 30-trade calibration is a Phase-1 deliverable,
not a Phase-0 one.** The research is unambiguous: 30 trades cannot
jointly identify (η, γ); 60–80 minimum. The right Phase-0 move is to
*instrument* so we can begin collecting calibration-grade trades
*from the first session after instrumentation lands*. The
calibration itself is a Phase-1 artifact gated on N≥30 *with passing
diagnostics*, leading to a Phase-2 staged-scale decision at $2.5M AUM
before any move toward $5M. The whole thing is a 6–10 week arc, not
a sprint.

The rest of the document defends those three findings with mappings
to current code, capability gaps, diagrams of the to-be data flow,
and a phased plan with explicit decision gates.

---

## 1. The methodology, crystallized

The research extracts an empirically-implementable slippage estimator
from the Alimoradian–Barigou–Eyraud-Loisel (2026) paper by stripping
its options/HJB scaffolding and keeping four portable objects:

| # | Portable object | What it gives us |
|---|---|---|
| 1 | **Decomposition architecture** | Splits the price path during a trade into ambient drift `m_t`, diffusion `σ_t dW_t`, *our* permanent footprint `Λ(χ_t) dN_t`, and microstructure noise `dξ_t`. |
| 2 | **Counterfactual price `S*_t`** | The path that would have happened *without our flow*. Operationalized as a **matched-cohort baseline** (median return of same-day non-traded gap-up names that passed the same screen). |
| 3 | **Power-law functional form** | `I_temp = Spread₀/2 + η · (Q/V_τ)^γ · σ_τ`. Free parameters η and γ, with a `σ_τ` volatility prefactor that orthogonalizes across heterogeneous tickers. |
| 4 | **Martingale diagnostic** | The residual `r_i − Î_perm,i` should have zero mean across the calibration sample. If not, the decomposition is leaking — the calibration is invalid. |

The estimator is **fitted Bayesianly** because at small N the
posterior must be dominated by a literature-anchored prior:

- `γ ~ TruncatedNormal(0.70, 0.20, [0.4, 1.2])` — centered above the
  large-cap consensus of γ≈0.5 to reflect thin microcap books, but
  with tails covering both the square-root attractor and the linear
  limit.
- `log η ~ Normal(log 0.1, 1.5)` — anchored at Almgren-magnitude.
- Likelihood is **Student-t(ν=4)** to automatically downweight the
  1–3 outliers expected at N=30.

The decision rule is a **staged-scale gate** indexed on the upper-75%
posterior bound of the slippage-to-alpha ratio:

```
UCB_75%(TotalSlip / α)  <  35%   →   scale
                          [35%, 50%)  →  stage-scale (go to $2.5M, recalibrate at N=60)
                          ≥ 50%       →  do not scale; reconsider universe or sizing
```

The 35% threshold sits at the industry midpoint adjusted downward for
alpha-uncertainty: if realized α is 5% (within 1σ of the 7% prior),
35%-of-7% slippage equals 49%-of-5%, which is on the abort edge. The
threshold protects against alpha-optimism.

---

## 2. The headline finding, defended

The research's worked example at the central prior set:

| Parameter | Value | Source |
|---|---|---|
| η_temp | 0.25 | 2.5× Almgren large-cap; microcap-amplified |
| γ_temp | 0.70 | Prior central |
| η_perm | 0.40 | Permanent coefficient, similarly amplified |
| σ_τ | 6% | First-hour realized vol of typical microcap gap-up |
| Spread₀ | 0.4% | 40 bps in a $3 microcap |

At **current** $500K-class AUM, 10% per position → $50K, Q/V_τ ≈ 10%:

```
Temp = 0.20% + 0.25 · (0.10)^0.70 · 6% = 0.50%
Perm = 0.40 · 0.10 · 6%               = 0.24%
Total ≈ 0.74%   → ~10.6% of 7% alpha   → comfortably scalable
```

At **target** $5M AUM, 5% per position → $250K, Q/V_τ ≈ 50%:

```
Temp = 0.20% + 0.25 · (0.50)^0.70 · 6% = 1.12%
Perm = 0.40 · 0.50 · 6%               = 1.20%
Total ≈ 2.32%   → ~33% of 7% alpha     → marginal; UCB_75% ≈ 43%
                                        → above the 35% scale threshold
                                        → below the 50% abort threshold
                                        → STAGE-SCALE territory
```

**Permanent impact dominates** because at Q/V_τ = 0.5 the
`(Q/V_τ)^γ_temp` term ranges only 0.50–0.71 across γ ∈ [0.5, 1.0] —
γ matters surprisingly little. The 3.0% sensitivity of total slippage
to η_perm (vs 0.37% for η_temp and 0.08% for γ) means **the calibration
must prioritize η_perm estimation, not γ tuning**. That's a directly
actionable inversion of priorities.

**Implications for our roadmap:**

1. The 12-month $5M target as currently scoped is **mathematically
   marginal** — not impossible, but requires per-position discipline
   (≤2.5%) or universe migration. A frank conversation about which
   path the strategy commits to is overdue.
2. The "γ ≈ 1 in microcap" hypothesis carried in some of our prior
   discussions is downgraded from a load-bearing claim to a *motivated
   upper bound on a wide prior*. It does not get tested at N=30; it
   gets tested at N≥75.
3. Our two existing slippage models (`src/execution/slippage.py` and
   `src/execution/slippage_model.py`) both bake in *fixed* coefficients
   that have never been calibrated against realized fills under this
   methodology. We do not actually know whether they are conservative
   or optimistic in the regime we trade.

---

## 3. The methodology's data demands

Per-trade, at millisecond resolution, the methodology requires:

```
TIME              | OBJECT                          | METHODOLOGY ROLE
------------------|---------------------------------|------------------------------------------
t − 60s ... t     | Time-weighted NBBO (rolling)    | → Spread₀ definition (TWAP, drop crossed/locked)
t − 1s            | NBBO snapshot                   | → leakage check
t − 100ms         | NBBO snapshot                   | → P₀ = mid (THE arrival reference price)
t                 | Order submission                | → side, qty, type, limit, server clock
[t, t+fill_dur]   | Per-partial fill stream         | → individual price/qty/venue/timestamp
Σ fills           | VWAP P_exec, total filled qty   | → I_temp numerator
[t, t+360s]       | Continuous NBBO + tape          | → realized vol prefactor σ_τ, halt detection
t+300s,900s,...   | NBBO mid at 5/15/30/60 min      | → I_perm term-structure (multi-horizon)
EOD               | NBBO close + last trade         | → terminal I_perm horizon

PARALLEL OBJECT   | MATCHED COHORT (same-day, same-screen, NOT-traded)
                  | → cohort returns at same horizons → counterfactual S*_t
                  | → I_perm = our_return − cohort_median_return  (DiD estimator)
```

Plus, persisted exactly once at the trade level:

- **Sizing rule witness** (the rule that produced Q_i, in machine-readable form,
  so identification assumption A1 — exogeneity of Q given the rule — can be audited).
- **VIX at trade time** (regime stratification per §5 of the research).
- **Halt flags within post-trade window** (segregation per §2.6).

---

## 4. Gap analysis: methodology vs MOMENTUM-X today

The exploration audit (§ "What exists" lines below all reference real
files in the repo) found the following capability surface. The gap
column drives the Phase-0 plan in §6.

### 4.1 Gap matrix (9 capabilities)

| # | Methodology requirement | Current capability | File / line | Gap | Severity |
|---|---|---|---|---|---|
| 1 | Order submission with full request payload persisted | `submit_order()` captures side/qty/type/limit + Alpaca response | `src/data/alpaca_client.py:453-520`, `src/execution/alpaca_executor.py:50-370` | Order *request* fields not journaled at submit time; only the response is persisted post-fill | **Low** — single new write |
| 2 | Per-partial-fill tick stream (price, qty, venue, ts) | Aggregate `filled_avg_price` only; partial-fills detected but not persisted | `src/execution/bridge.py:200-370`, `src/execution/alpaca_executor.py:291-325` | No tick-level fill record. Methodology cannot compute clean P_exec without it on multi-fill orders | **High** — required for I_temp |
| 3 | NBBO snapshots at t-60s..t-100ms + t+5/15/30/60min/EOD | Single snapshot at request time only | `src/data/alpaca_client.py:189-265` | No time-series. P₀ at t-100ms cannot be reconstructed; I_perm horizons cannot be measured | **Critical** — blocks both estimators |
| 4 | Bayesian (η, γ) estimator with priors | Two *forward* slippage models (Almgren, D196 tiered) with hardcoded coefficients | `src/execution/slippage.py:28-261`, `src/execution/slippage_model.py:1-168` | No fit. No priors. No posterior. Predicted-vs-realized never persisted | **Medium** — needed at calibration time, not before |
| 5 | Trade journal per-trade record | Rich journal: ticker, fill_price, signal_price, qty, MFCS, kelly_tier, gap, RVOL, VIX, target/stop | `src/analysis/execution_recorder.py:1-288`, `src/execution/bridge.py:325-375` | NBBO / multi-horizon / per-fill columns missing. Schema needs 10–15 new fields | **Low** — additive schema change |
| 6 | Matched-cohort baseline (all candidates that passed the screen but were NOT traded, with their post-screen returns at all horizons) | Scanner emits `CandidateStock`; rejected candidates logged but not persisted with post-screen price tracking | `src/core/models.py:116-193`, `src/core/scan_loop.py`, `src/core/orchestrator.py:3118` | No daily candidate registry. No price tracking on rejected names | **Critical** — blocks I_perm counterfactual |
| 7 | AUM scaling logic + per-position cap | `starting_equity` from Alpaca account; tier-based per-position % (T1=2%, T2=3%, T3=5%, T4=10%) | `config/settings.py:611, 1400-1506`, `src/execution/position_manager.py:129-219` | Current paper account ≈ $142K. No live $5M scenario tested. The 5% Tier 3 default is *exactly* the sizing the research flags as marginal at $5M | **N/A** — this is the *target*, not the gap |
| 8 | Post-trade price tracking (5/15/30/60min/EOD after position close) | Position close fires `post_trade_close()` async hook but no price-following | `src/monitoring/alerts.py` (referenced); position closure path | No persistent post-trade price journal. I_perm cannot be measured | **Critical** — blocks I_perm |
| 9 | Experiment framework (could host the calibration) | YAML-defined variants with Settings overrides; per-variant journal | `src/experiments/registry.py:1-260`, `data/experiments/experiments.yaml` | Ready as-is. Calibration becomes a new experiment variant set | **None** — leverage existing |

### 4.2 What we *do* have that's directly reusable

- The **Almgren functional form** in `slippage.py` is exactly the
  research's `(★)` equation — so the *forward* prediction shape is
  already correct. It just hasn't been calibrated.
- The **D196 tiered model** in `slippage_model.py` already encodes
  the realization that microcap impact differs from large-cap. Its
  participation multipliers (15× for micro, 6× for low) are an
  *implicit* η_temp scaling that the methodology will either
  validate or reject — useful as a Bayesian prior for the per-tier
  η.
- The **ExecutionRecorder** writes to multiple sinks (trade journal,
  session collector, phantom journal) — adding NBBO columns is a
  schema change, not a new write path.
- The **experiment framework** can host the calibration as a named
  experiment with variants (e.g., `slippage_calibration_v0` with
  variants for prior choice, σ_τ proxy, V_τ blend coefficients).
  No new infra needed for this.
- The **scanner already logs rejected candidates** at
  `orchestrator.py:3118` — the rejection lines exist; they just
  don't carry post-screen return tracking.

### 4.3 What we *don't* have, in priority order

1. **NBBO time-series capture around the order boundary**
   (#3 above; blocks P₀, blocks Spread₀, blocks σ_τ, blocks I_perm). Critical.
2. **Matched-cohort post-screen price tracking** (#6, #8 jointly;
   blocks I_perm counterfactual). Critical.
3. **Per-partial-fill tick persistence** (#2; blocks clean P_exec
   on multi-fill orders, which are the norm at the open in microcap). High.
4. **Trade-journal schema extension** for NBBO/horizon/fill columns
   (#5; additive). Low.
5. **Bayesian estimator scaffolding** (#4; needed only at calibration
   time, after N≥30 instrumented trades). Medium.

---

## 5. The to-be data flow (diagram)

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                           PHASE-0 INSTRUMENTATION                              │
│  (no behavior change; pure observability before behavior change — same         │
│   discipline as Phase F env-audit)                                             │
└────────────────────────────────────────────────────────────────────────────────┘

  ┌──────────────────┐
  │ SCANNER          │── all candidates that pass screen ──┐
  │ scan_loop.py     │                                     │
  └────────┬─────────┘                                     ▼
           │                                ┌─────────────────────────────────┐
           │ traded                         │  COHORT REGISTRY (NEW)          │
           │                                │  data/cohort/                   │
           │                                │    daily_candidates_<date>.jsonl│
           │                                │  fields: ticker, screen_ts,     │
           │                                │    screen_features, traded:bool │
           ▼                                └────────────────┬────────────────┘
  ┌──────────────────┐                                       │
  │ ORCHESTRATOR     │                                       │
  │ orchestrator.py  │                                       │
  └────────┬─────────┘                                       │
           │ verdict.action == BUY                           │
           ▼                                                 │
  ┌──────────────────┐                                       │
  │ EXECUTOR         │                                       │
  │ alpaca_executor  │                                       │
  └─┬─────────┬──────┘                                       │
    │         │                                              │
    │   ┌─────┴──────────────────────────────┐               │
    │   │ NBBO CAPTURE LOOP (NEW)            │               │
    │   │ src/data/nbbo_capture.py           │               │
    │   │   - subscribes to quote stream     │               │
    │   │     for the ticker T               │               │
    │   │   - rolling 60s buffer for Spread₀ │               │
    │   │   - schedules snapshots at         │               │
    │   │     t-60s, t-1s, t-100ms,          │               │
    │   │     t, t+5m, t+15m, t+30m,         │               │
    │   │     t+60m, EOD                     │               │
    │   │   - writes to journal at each tick │               │
    │   └─────┬──────────────────────────────┘               │
    │         │                                              │
    │         │                                              │
    ▼         ▼                                              │
  ┌──────────────────────────────────────────┐               │
  │ ORDER SUBMIT + FILL STREAM (EXISTING +   │               │
  │ NEW per-partial-fill persistence)        │               │
  │ - request payload journaled at submit    │               │
  │ - each partial fill captured: price,     │               │
  │   qty, venue, server_ts, exch_ts         │               │
  │ - aggregate VWAP computed from ticks,    │               │
  │   not just from response                 │               │
  └────────────┬─────────────────────────────┘               │
               │                                             │
               ▼                                             │
  ┌──────────────────────────────────────────┐               │
  │ TRADE JOURNAL (EXISTING + NEW columns)   │◄──────────────┘
  │ src/analysis/execution_recorder.py       │  cohort linked at trade write
  │   adds: nbbo_t_minus_60s..t_plus_eod,    │  via cohort_id = date+ticker
  │     fill_ticks[], cohort_ids[],          │
  │     vix_at_trade, halt_flags[]           │
  └────────────┬─────────────────────────────┘
               │
               ▼
  ┌──────────────────────────────────────────┐
  │ POST-TRADE PRICE FOLLOWER (NEW)          │
  │ src/data/post_trade_tracker.py           │
  │   - on position close: schedule          │
  │     mid-price snapshots at +5/15/30/60m  │
  │     and at EOD                           │
  │   - parallel: same schedule for every    │
  │     cohort ticker (traded + not traded)  │
  │   - writes to: data/post_trade/<trade_id>.jsonl
  └──────────────────────────────────────────┘


┌────────────────────────────────────────────────────────────────────────────────┐
│                           PHASE-1 CALIBRATION                                  │
│  (after N ≥ 30 instrumented trades accumulate; runs offline against            │
│   the journal + cohort + post-trade artifacts)                                 │
└────────────────────────────────────────────────────────────────────────────────┘

   data/journal/*  +  data/cohort/*  +  data/post_trade/*
                              │
                              ▼
   ┌────────────────────────────────────────────────────┐
   │ scripts/slippage_calibration.py (NEW)              │
   │   1. Load N trades + matched cohorts               │
   │   2. Compute I_temp,i from fill ticks vs P₀        │
   │   3. Compute I_perm,i (5/15/30/60m/EOD)            │
   │       = our_return − cohort_median_return          │
   │   4. Bayesian fit (PyMC):                          │
   │      - log η ~ Normal(log 0.1, 1.5)                │
   │      - γ    ~ TruncNormal(0.70, 0.20)              │
   │      - Student-t(ν=4) likelihood                   │
   │   5. Diagnostics:                                  │
   │      - prior-vs-posterior overlap                  │
   │      - PSIS-LOO (k_i > 0.7 = influence point)      │
   │      - rolling-γ stability (1-15 vs 16-30)         │
   │      - residual martingale test                    │
   │   6. Project to $5M AUM:                           │
   │      - UCB_75% on TotalSlip/α                      │
   │      - decision: scale / stage / abort             │
   └────────────────────────────────────────────────────┘
                              │
                              ▼
                  reports/slippage_calibration_<date>.{md, json, pdf}
```

---

## 6. Phased plan (no work tonight; sequencing only)

The work decomposes into four phases. Each phase has a **gate**: a
specific deliverable that must be present before the next phase
starts. This is the same compounding-discipline the weekend's bug
sweeps used.

### Phase 0 — Instrumentation (estimated 3-5 days)

**Goal:** make the system *capable* of producing calibration-grade
trade records, with zero behavior change. Same pattern as the env-audit
ship: observability before behavior change.

| Deliverable | What | Where it lands |
|---|---|---|
| 0.1 NBBO capture loop | Subscribes to quote stream for any ticker the orchestrator is about to trade. Rolling 60s buffer + scheduled snapshots at t-60s, t-1s, t-100ms, t, +5m, +15m, +30m, +60m, EOD. | New module `src/data/nbbo_capture.py` |
| 0.2 Per-partial-fill persistence | Capture each partial fill from the WebSocket trade-update stream as a discrete event (price, qty, venue, server_ts, exchange_ts). Compute aggregate VWAP from ticks, not from response. | Extend `src/execution/alpaca_executor.py` and the trade journal |
| 0.3 Cohort registry | At end of each scan, write `data/cohort/daily_candidates_<date>.jsonl` with ALL candidates that passed the screen, including their screen-time features and `traded: bool`. | New writer hook in `scan_loop.py` |
| 0.4 Post-trade price follower | On position close, schedule mid-price snapshots at +5/15/30/60m and EOD. Parallel: same schedule for every cohort ticker that day, traded or not. | New module `src/data/post_trade_tracker.py` |
| 0.5 Trade-journal schema extension | Add columns: `nbbo_<ts>` for each schedule point, `fill_ticks[]`, `cohort_id`, `vix_at_trade`, `halt_flags[]`, `sizing_rule_witness`. | Extend `src/analysis/execution_recorder.py` |
| 0.6 Adversarial test suite | Per the hardened bug-sweep template (rule (e)): every defensive `except` in the new code ships with a positive-case test asserting non-empty output under normal conditions. | `tests/unit/test_nbbo_capture.py`, `test_post_trade_tracker.py`, `test_cohort_registry.py` |

**Phase 0 gate:** one full live trading session produces a complete
record set: ≥1 trade with all NBBO snapshots, ≥1 partial-fill tick
record, the cohort file populated, and post-trade snapshots arriving
on schedule. **Do not start Phase 1 until this gate passes.**

### Phase 1 — Calibration sample accumulation + first fit (estimated 3-5 weeks of trading)

**Goal:** accumulate N ≥ 30 instrumented trades within a narrow
regime band (gap 5–15%, RVOL 2–8, price $1–$10), then run the
Bayesian estimator and the diagnostic battery.

| Sub-phase | Deliverable | Gate |
|---|---|---|
| 1.1 Accumulation | N=30 trades inside the regime band | Trades counter in heartbeat |
| 1.2 Estimator scaffolding | `scripts/slippage_calibration.py` — loads journal, cohort, post-trade; fits Bayesian (PyMC); writes report. **This is the first NEW code outside Phase 0** | Reproducible on synthetic data |
| 1.3 First fit | Run estimator on N=30; produce posterior for η_temp, η_perm, γ; produce diagnostics report | Report committed to `docs/calibration/<date>.md` |
| 1.4 Pass-through diagnostic check | All of: prior-vs-posterior CI narrowing, PSIS-LOO clean (no k > 0.7 outliers), rolling-γ Δ < 0.15, martingale residual test ≠ rejected at 2σ | If any fail: declare regime non-stationary or decomposition leaking; do not proceed |

**Phase 1 gate (decision-relevant):** the *posterior*, not a point
estimate. The deliverable is a posterior over `TotalSlip / α` projected
to $5M AUM. Compute UCB_75%. **Decision rule:**

```
UCB_75% < 35%        →  proceed to Phase 2 staged scale
35% ≤ UCB_75% < 50%  →  Phase 1.5 — extend to N=60, refit
UCB_75% ≥ 50%        →  do not scale microcap; trigger Phase 4 (universe migration evaluation)
```

### Phase 2 — Staged scale to $2.5M (estimated 6-10 weeks at $2.5M)

**Goal:** validate the Phase 1 posterior holds under live capital
deployment at half the target AUM.

- Move paper-account equity to a $2.5M scenario (or, if real capital is
  deploying, deploy half).
- Continue full Phase-0 instrumentation; the calibration-grade
  recording must persist.
- Recalibrate at N=60 cumulative (so 30 new trades at $2.5M sizing).
- Track *realized* TotalSlip vs the Phase-1 forward projection. The
  ratio is the **calibration credibility metric**: if realized falls
  inside the 90% credible interval of the projection, the methodology
  is calibrated. If realized lands above the 95th percentile, the
  prior was wrong — back to Phase 1.

**Phase 2 gate:**

```
At N=60, refit and project to $5M:
  UCB_75% < 35%   AND   realized N=30..N=60 inside 90% CI of Phase 1 projection
                  → Phase 3 (scale to $5M)
  Either fails    → stop. Reassess universe.
```

### Phase 3 — Scale to $5M (only if Phase 2 gate passes)

Trivial in code (sizing change). Non-trivial in operations: ongoing
calibration becomes a permanent loop, not a project.

**Permanent-loop deliverables:**
- Quarterly recalibration on a rolling 90-trade window.
- Heartbeat-embedded "current calibrated η_perm, η_temp, γ" so any
  drift from the priors is observable in real time.
- Auto-trigger Phase 1 reset if quarterly rolling γ shifts by > 0.20.

### Phase 4 — Universe migration (only if Phase 1 or 2 gate fails)

Triggered by:
- Phase 1 UCB_75% ≥ 50%, OR
- Phase 2 realized slippage materially exceeds projection, OR
- Stability triggers (regime non-stationarity, halt-rate > 25%).

The research's mid-cap migration path (§4.2) requires:
- A separate 30-trade calibration on $5M–$50M ADV names.
- An IV-dispersion cross-validation backtest (Bali–Hovakimian + Xing
  skew + Cremers–Weinbaum) to validate that the cascade-anti-selection
  signal *transfers* to mid-cap, given that mid-cap order flow is
  institutional-dominated rather than retail-dominated.
- Operational caveat: each regime requires its own complete Phase-0
  through Phase-1 cycle. Hybrid microcap-at-$2M + mid-cap-building-to-$3M
  doubles the calibration burden.

---

## 7. The decision tree, drawn

```
                       ┌─────────────────────────┐
                       │ Phase 0: instrumentation │
                       │   gate: 1 trade with    │
                       │   complete record set    │
                       └────────────┬─────────────┘
                                    │ pass
                                    ▼
                       ┌─────────────────────────┐
                       │ Phase 1: accumulate N=30 │
                       │   in regime band         │
                       │   gate: diagnostics pass │
                       └────────────┬─────────────┘
                                    │
                          ┌─────────┴───────────┐
                          │ diagnostics fail?    │
                          └─────────┬───────────┘
                                    │ yes
                                    ▼
                          ┌────────────────────┐
                          │ regime non-stationary │
                          │ or decomposition leak │
                          │ → Phase 4 evaluation  │
                          └────────────────────┘
                                    │
                                    │ no
                                    ▼
                       ┌─────────────────────────┐
                       │ project to $5M AUM       │
                       │   compute UCB_75%        │
                       └────────────┬─────────────┘
                                    │
                ┌───────────────────┼───────────────────┐
                │                   │                   │
                ▼                   ▼                   ▼
          UCB_75% < 35%      35% ≤ UCB < 50%       UCB ≥ 50%
                │                   │                   │
                ▼                   ▼                   ▼
        ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐
        │ Phase 2:     │  │ Phase 1.5:       │  │ Phase 4:         │
        │ stage to     │  │ extend to N=60,  │  │ universe         │
        │ $2.5M        │  │ refit, decide    │  │ migration:       │
        │ for 30 trades│  │ at the same gate │  │  - mid-cap       │
        └──────┬───────┘  └────────┬─────────┘  │    calibration   │
               │                   │             │  - or sub-2.5%   │
               ▼                   │             │    per-position  │
        ┌──────────────┐           │             └──────────────────┘
        │ Phase 2 gate │◄──────────┘
        │ at N=60:     │
        │  realized in │
        │  90% CI of   │
        │  projection? │
        └──────┬───────┘
               │
        ┌──────┴──────┐
        │ pass        │ fail
        │             │
        ▼             ▼
   Phase 3:     stop, reassess
   scale to     universe; do not
   $5M;         force scale
   permanent
   recalibration
   loop
```

---

## 8. Justification for ordering (why Phase 0 before anything else)

There are three temptations to do something "more interesting" first.
Each has a counter-argument that points back to Phase 0.

**Temptation A: "Just calibrate from existing trade journal data."**

Counter: existing trade records lack P₀ at t-100ms, lack per-fill
ticks, lack post-trade prices at the methodology's horizons, and
lack matched cohort returns. A calibration on existing data would
produce a number, but the number would be biased by (a) using the
ask instead of the mid as P₀ (which absorbs market-maker response
twice — research §1.5), (b) treating multi-fill VWAP from the
aggregated response as if it were instantaneous, (c) using
single-horizon return as I_perm (the research demands a term
structure to identify decay vs unreverted-temporary contamination),
and (d) having no counterfactual at all (the entire post-trade
return would be attributed to our flow). The number would be
publishable as "an estimate" and would be wrong by a factor that
we cannot bound. **Worse than no number.**

**Temptation B: "Build the Bayesian estimator first while the calibration data accumulates."**

Counter: the estimator is ~200-400 lines of PyMC and ~50 lines of
diagnostics. It is not the long pole. The long pole is the data.
Worse, building the estimator before the data exists invites
implementing it against synthetic-data assumptions that turn out
not to match the real data's structure (e.g., assumed normal
residuals when the real data is heavy-tailed; assumed
homoscedasticity when the real data is heteroskedastic across
gap-size bins). Build the estimator *against early Phase-1 data*
so its assumptions are pinned by evidence, not by prior.

**Temptation C: "Migrate to mid-cap right now since the research says microcap can't reach $5M."**

Counter: the research is a *prior-driven projection*, not an
observation. The actual microcap-at-$5M slippage may be lower
than the central prior — the calibration is what would tell us.
Migrating now is choosing the universe-migration cost (separate
calibration regime, IV-dispersion validation, separate signal
fit) on the basis of a number we have not yet measured. The
methodologically correct sequence is: instrument → measure →
decide. Even if the decision turns out to be "migrate," we want
to make it on data, not on a worked example with prior-set
parameters.

---

## 9. Risk register (research §5 mapped to mitigations)

The research is explicit about what its methodology cannot resolve.
Each known unknown maps to a mitigation in our plan or to an
explicit "we accept this".

| # | Known unknown (research §5) | Our mitigation |
|---|---|---|
| 1 | VIX-regime stationarity — impact differs across VIX regimes | Capture VIX-at-trade in the journal extension; stratify the calibration posterior by VIX regime; refuse to extrapolate across regime |
| 2 | Alpaca routing opacity (PFOF wholesalers) | Accept. Document the calibration as "Alpaca-routed retail-microcap impact." If we later move to DMA, recalibrate from scratch. Add this caveat to every report |
| 3 | LULD halt dynamics | Capture halt flags in the post-trade window; segregate halt-during-window trades in a separate subsample; if > 25% of trades halt, switch to halt-event-study (out-of-scope for this methodology) |
| 4 | SIP feed gaps; off-exchange volume | Accept the bias direction (η over-estimated). Note in every report. Live with it until DMA + direct feeds become economically justifiable at scale |
| 5 | Signal-source dependence of γ | Don't claim generalizable γ — claim "γ for our screen's selected universe." Document the screen rule in every calibration report |
| 6 | Alpha non-stationarity | Run a parallel walk-forward alpha test alongside the calibration. The 7% prior is not a fact; it's a hypothesis under decay risk |
| 7 | Execution-timing endogeneity (slippage-aware sizing) | Use a fixed sizing rule during Phase 1 calibration windows. Accept the slippage cost as the price of clean identification |

---

## 10. What we are explicitly NOT doing

- **No code tonight.** Per the user's directive.
- **No ML for impact estimation.** The research is unambiguous: ML
  needs N > 10⁵ per stock; we are at N=0 calibrated trades. GP
  regression with parametric mean is "essentially a regularized
  power law," not a separate method. **Recommend against ML entirely
  until N > 500.**
- **No Hasbrouck VAR.** Requires hundreds of observations per stock.
  Matched-cohort is the small-sample substitute.
- **No options-overlay (the original Alimoradian apparatus).** Strip it.
- **No HJB optimization of execution schedule.** We are *observing*
  flow under a rule-based screen, not optimizing χ_t.
- **No live mid-cap until microcap calibration is complete.** The
  research's hybrid path is mathematically sensible but doubles
  calibration burden — sequence it.
- **No joint (η, γ) estimate at N=30.** Fix γ at the prior; estimate
  η. Re-open γ at N≥75. The honest statement at N=30 is
  "η-conditional-on-γ-prior with γ-sensitivity pass."

---

## 11. Open questions for the architecture call

These are decisions a human needs to make; this plan does not
prejudge them.

1. **Universe commitment.** If the calibration's most likely outcome
   is "microcap caps at $1.5–$2.5M" (the prior central case), do we
   stage-scale microcap to its ceiling and migrate the marginal AUM
   to mid-cap, or do we abandon the microcap track in favor of
   mid-cap from the start? The research mildly favors the hybrid
   path; the operational cost is real (two calibration regimes).
2. **Per-position policy.** Are we willing to lower per-position
   allocation from 5% to 2.5% to make the $5M target reachable on
   pure microcap? This halves trade count per AUM-dollar, which
   has implications for catalyst capture (you miss half the
   opportunities at the same AUM).
3. **Calibration cadence at scale.** The research treats the 30-trade
   calibration as a one-time event leading to a scaling decision.
   Operationally, *quarterly recalibration* on a rolling window
   is the safer steady state. Acceptable to commit to that loop?
4. **What goes in the heartbeat.** Tonight's pattern was: when
   activation state is opaque, surface it in the heartbeat. Same
   logic applies here — should current calibrated (η_temp, η_perm,
   γ_temp) values appear in the heartbeat alongside the env_audit
   block?
5. **The cohort universe definition.** The research recommends
   "minimum cohort size ≥ 5 matched non-traded names per trade." If
   our screen produces only 3 candidates on some days, is that a
   no-trade day (because counterfactual would be undefined) or a
   reduced-confidence day (we still trade but flag the calibration
   sample for cohort-thin)? The research suggests the latter; the
   risk-aversion case suggests the former.
6. **Alpha-decay parallel test.** We have a separate experiment-arena
   apparatus (`src/selection_arena/`). Could the alpha-decay test
   (research §5 unknown #6) be hosted there rather than as a new
   project, since it's the same "offline counterfactual against
   live behavior" pattern?

---

## 12. Tie-back to the weekend's compounding-discipline framing

The weekend bug sweeps shipped a pattern: **observability before
behavior change**. Phase F shipped env-audit *before* the planned
preflight-abort that the audit data will eventually justify. Tonight's
crash-report MVP shipped *before* graceful-recovery. The pattern
repeats here: instrument *before* calibrate, calibrate *before*
scale, stage-scale *before* full deployment.

Each layer is one observability or measurement step that earns the
next behavior change. The slippage methodology's staged-scale gate
(35% → stage at $2.5M → recalibrate → 35% again → $5M) is the
research's expression of the same discipline.

The headline finding ($5M is marginal under prior parameters) is
unwelcome but is *exactly the kind of information* this discipline
exists to surface. The honest path is: build the instrument, take
the measurement, let the data say what it says, decide on data not
on framing. If the data confirms the prior, we know the universe
caps at $2.5M and we plan accordingly. If the data refutes the
prior in our favor, we earned the $5M honestly. If it refutes
in the unfavorable direction, we'd have learned that during a
$5M deployment with real money — and that's the failure mode this
plan exists to prevent.

---

## Appendix A — Reference matrix from research → our code

For implementation Phase 0, the following research sections map to
specific code locations:

| Research § | Concept | Likely landing site in our repo |
|---|---|---|
| §1.5 | V_τ blended proxy `max(α·V₀:₆^residual, β·ADV/13)` | New helper in `src/data/volume_proxies.py` (NEW) |
| §1.5 | Spread₀ definition (TWAP, drop crossed/locked, widen on disorder) | New helper alongside NBBO capture |
| §1.5 | P₀ = NBBO mid at t-100ms via SIP timestamp | NBBO capture loop, schedule entry |
| §1.6 | Tobit-style censoring for I_temp ≤ Spread₀/2 | Estimator script only (not production path) |
| §1.7 | Multi-horizon I_perm | Post-trade tracker schedule |
| §1.8 | Matched-cohort baseline (median return) | Estimator script + cohort registry |
| §1.9 | Effective vs realized half-spread (Glosten–Milgrom triangulation) | Estimator script — secondary check |
| §2.4 | Bayesian model in Stan/PyMC | `scripts/slippage_calibration.py` (NEW) |
| §2.5 | Diagnostics battery (PSIS-LOO, rolling-γ, martingale) | Same script |
| §2.7 | Minimum-honest-N table | Documentation; no code |
| §3.5 | Capacity verdict at central parameters | Report template `reports/slippage_calibration_<date>.md` |
| §4.2 | IV-dispersion cross-validation (mid-cap migration) | Phase 4 only — separate research project |

## Appendix B — What the methodology buys us (in one sentence)

**Replace the implicit claim "$5M AUM is reachable in 12 months"
with the empirical claim "η_perm at $250K notional in microcap
gap-up regime is X bps with 90% CI [Y, Z], and the resulting
projected slippage at $5M is W% of 7% alpha with UCB_75% V%, which
is below/above the 35% scale gate."** Either the data agrees with
the timeline or it disagrees. We won't have to argue about it.

---

*End of plan. No code committed tonight. Next action: discuss this
document at the Tuesday architecture call alongside the bug-sweep
followups and the silent-fallback audit output.*
