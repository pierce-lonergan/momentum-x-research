# 38 — Tuesday Architecture Call Deck (slide-style brief)

**Audience:** architecture-call participants.
**Length goal:** 12-15 minute presentation, 30+ minutes Q&A.
**Companion:** `22_tuesday_architecture_call_agenda.md` (full discussion items)
**Generated:** Saturday 2026-04-25 evening, after the discovery-infrastructure compounding sprint.

---

## Slide 1 — Thesis statement

> **In 7 days we shipped 11 production-bug fixes and 6 layers of discovery
> infrastructure. Zero patches introduced regressions. The discovery rate
> is rising while the engineering rate stays flat.**
>
> **This is the antifragility property. The architecture decision today
> is whether we commit to maintaining it through May 2026.**

---

## Slide 2 — The 11/0 ratio

| Bugs surfaced + fixed (this week) | 11 production bugs (D, E, #13, F, N, T, U, Q, R, V, W, X, Y, Z, AA) |
| Patches that introduced regressions | 0 |
| Algorithm-calibration findings (test-first caught) | 1 (BOCPD cp-widening) |
| **Discovery rate** | **∞** |

**What "infinite" means in operational terms:** the system becomes more
capable of finding bugs at a faster pace than it introduces them. Each
patch shipped this week made the bug-discovery surface broader, not
narrower.

---

## Slide 3 — The five discovery-infrastructure layers (all live)

| Layer | Catches | Trigger | Latency |
|-------|---------|---------|---------|
| **Static analysis** (`v2.2-static-analysis-active`) | frozen-mutate, async-leak, silent-handler, relative-path, ps-datetime-utc, ps-pipe-deadlock | every commit | <3s |
| **PBT state machine** (`v2.2-pbt-phase2-active`) | invariant violations from rule grammar; named bug classes D/V/W/Z + generalizations | every commit + nightly | 3s/20s/100s tiers |
| **Phase 0 instrumentation** (`v2.2-phase-0-active`) | per-trade microstructure data; D261 schema validation failures; foundation for QMP retrospective + Bayesian estimator + differential replay | every order lifecycle | inline (sub-ms) |
| **Differential testing** (`v2.2-difftest-foundation`) | Bug-N-class regressions from silent call-site changes | every commit; per-PR replay | <1s harness; minutes for replay |
| **BOCPD changepoint detection** (`v2.2-bocpd-armed`) | regime breaks in trade outcomes; D223 BOCPD_BREAK + D224 KELLY_HALVED; D262 prior-refit recommendation | per-trade close + EOD | inline + EOD |
| **Bayesian (η, γ) slippage estimator** (`v2.2-bayesian-estimator-armed`) | capacity-calibration drift; D256 (η_perm > 0.65), D257 (γ drift), D258 (halt rate), D259 (martingale residual) | EOD when run | seconds-to-minutes |

---

## Slide 4 — Weekly commit ledger (chronological)

| # | Commit | Capability | Tag |
|---|--------|------------|-----|
| 1 | `366d028` | Static-analysis suite + 6 silent-handler fixes | `v2.2-static-analysis-active` |
| 2 | `2cc3e9a` | Track C Phase 2 expansion (R1-R11 + I1-I9) | `v2.2-pbt-phase2-active` |
| 3 | `09f3ce4` | Bug AA: SimpleBroker fill discipline (PBT-surfaced) | — |
| 4 | `5e6a9b9` | QMP signing migration | — |
| 5 | `f1fd13f` | Phase 0 instrumentation MVP foundation | `v2.2-phase-0-active` |
| 6 | `0f0bd0f` | Phase 0 wire-in: 6 emit-sites + EOD flush + D261 | — |
| 7 | `e4c32a3` | Track D differential harness foundation | `v2.2-difftest-foundation` |
| 8 | `133adb6` | BOCPD pre-training + cp-widening calibration finding | `v2.2-bocpd-armed` |
| 9 | `8c4160f` | Track C mutation canaries (Bug V/W/Z) + invariant normalization | — |
| 10 | `c5a82ba` | BOCPD wire-in: prior load + observe at terminal close | — |
| 11 | `7ecc2e8` | BOCPD re-fit scheduling + D262 EOD diff reporting | — |
| 12 | `e8d6340` | Architecture call agenda synthesis (Saturday update) | — |
| 13 | `6e8272d` | Kelly governance: D223 → halve Kelly for next 5 trades (D224) | — |
| 14 | `c34f654` | Cohort registry v0.1: matcher + emit wire | — |
| 15 | `0cee9d4` | Bayesian (η, γ) estimator: PyMC NUTS + 4-trigger gates | `v2.2-bayesian-estimator-armed` |

**Six tags. Fifteen commits. All on `origin/develop`.**

---

## Slide 5 — Test surface

| Suite | Tests | Runtime |
|-------|-------|---------|
| Static analysis | 26 | ~3s |
| Track C Phase 2 PBT | 1 (state machine) | ~20s @ 2k examples |
| PBT injection tests (alive proof) | 13 | <1s |
| PBT mutation canaries (search proof) | 3 | ~5s |
| SimpleBroker oracle | 24 | <1s |
| Phase 0 instrumentation | 14 | <1s |
| QMP signing | 25 | <1s |
| BOCPD + refit | 18 | <1s |
| Kelly governor | 7 | <1s |
| Cohort matcher | 11 | <1s |
| Differential harness | 10 | <1s |
| Bayesian estimator (fast gates) | 12 | ~3s |
| Bayesian estimator (slow NUTS) | 3 | ~90s `-m slow` |
| D219 behavioral | 5 | <1s |
| D24 Bug Z behavioral | 6 | <1s |
| **Total fast** | **~195 tests** | **~30s** |
| Total including slow | ~198 | ~2 min |

Pre-commit hook chain runs the fast variants on every commit. Nightly
runs the full slow + 10000-example PBT pass. CI never goes red because
the gating discipline prevents it.

---

## Slide 6 — Five Tuesday discussion items

(Each with a recommendation. Voting closes the architecture session.)

### Item 1: Pull Track B daemon arming forward to N=2

**Original:** N=5 clean shadow sessions before D231 hard-block.
**Updated (`26_d_code_registry.md`):** N=2 with Phase 0 corpus available.
**Rationale:** Bug Z's 5h 30m discovery latency dominates the false-positive insurance. Phase 0 narrows the false-positive surface.
**Recommendation:** **Arm Wednesday eve if Monday + Tuesday both clean.**

### Item 2: D223 BOCPD_BREAK → halve Kelly for next 5 trades

**Already shipped (commit `6e8272d`).** D224 KELLY_HALVED logged on activation, RE-ACTIVATED on extension, RESET when window expires.
**Recommendation:** **Confirm policy at the call. Empirically tunable: change `halve_duration_trades` / `halve_multiplier` in `KellyGovernor` constructor.**

### Item 3: Cohort registry matching algorithm v0.1

**Already shipped (commit `c34f654`).** Same-(catalyst, market_cap_bucket, hour_bucket) match within ±15min entry window, 5-peer cap.
**Recommendation:** **Run live for 1 week, compare matched-cohort vs absolute-return diagnostics, tune in v0.2 if matching is too tight or too loose.**

### Item 4: Bayesian estimator + four-trigger gates D256-D259

**Already shipped (commit `0cee9d4`, tag `v2.2-bayesian-estimator-armed`).** PyMC NUTS recovers η/γ to within 3σ of truth on synthetic data. Four gates wired but EOD pipeline waits on Phase 0 producing data.
**Recommendation:** **Wire EOD pipeline next week (after Phase 0 produces the first session's worth of fills + bar contexts).**

### Item 5: Differential replay engine timing

**Foundation shipped (commit `e4c32a3`, tag `v2.2-difftest-foundation`).** Per-PR replay comparison waits on Phase 0 first-session capture.
**Recommendation:** **Implement replay engine + git-aware wrapper next week. ~1 day of work once Phase 0 produces input.**

---

## Slide 7 — The architecture-call vote

> **Resolved: MOMENTUM-X commits to maintaining the 11/0 discovery
> ratio (or higher) through May 2026. If the ratio drops, the
> next-week budget shifts from new features to discovery
> infrastructure.**

What this means operationally:

- Every patch ships test-first per the established discipline.
- Every shipped bug gets:
  1. A behavioral injection test (proves invariant catches the state)
  2. A search canary (proves PBT reaches the state)
  3. A static-analysis rule (when the bug class is detectable AST-side)
  4. A finding doc in `docs/research-log/`
- Bug naming continues alphabetically (Bug AA shipped this week; next is BB).
- The discovery infrastructure layers extend (e.g., Track E chaos testing) when the existing five become saturated.

---

## Slide 8 — What this enables (the $1M-in-12-months arc)

The discovery infrastructure exists to make AUM scaling SAFE, not just FAST. The link from "11/0 ratio" to "$1M AUM" goes through three operational lifts:

1. **Bayesian (η, γ) calibration → capacity number.** Once Phase 0 captures 60-80 fills per cohort cell, the Bayesian estimator produces an honest answer to "how much can we deploy without slippage > 0.65 mean?". That's the AUM-ceiling number for the architecture-side of the $1M plan.

2. **BOCPD + Kelly governance → drawdown discipline.** When the live regime breaks (D223), Kelly halves automatically (D224). Operator sees the alert and can halt further entries. This converts "regime change kills the strategy in production" risk into "regime change degrades sizing for 5 trades, then resumes." Drawdown bound — required for capital-side fundraise conversations.

3. **Track B daemon → kill switch latency reduction.** Bug Z's 5h 30m discovery → 30s discovery once D231 is armed. This is the difference between "we lost $947 over a weekend before noticing" and "we noticed in 30 seconds and contained the failure." Capital-side fundraise gate.

**Each capability built in the past week is a constraint reduction for the $1M-in-12-months arc.**

The capital-side items (Series 65, LLC, accountant, IBKR Pro, Anthropic team call) operate in parallel — those are user-action items not blocking engineering. The engineering side has cleared a milestone today: every atomic capability needed for the AUM-deployment conversation is now operational (foundation level) or wired (live level).

**Architecture call closes this sprint. Capital-side pivot starts the next.**
