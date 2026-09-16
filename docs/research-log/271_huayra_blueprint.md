# 271 — The Huayra Blueprint: an honest critique of what we have, and the engineering path to a generational machine

**Author**: Claude (Fable 5 era) | **Date**: 2026-06-10 | **Mandate**: Pierce — "right now this is a 1997 Ford Focus... turn this into a Pagani Huayra." This doc is the honest assessment and the blueprint. Companion: doc 270 (today's live-fire record).

## Part I — What the Focus actually is (the critique, unsoftened)

**The engine block**: `main.py` is a ~9,000-line monolith where 300+ D-codes have accreted like sediment — every fix a stratum, every stratum a place for the next bug to hide. The phantom-P&L family needed SIX kills (D91/D242/D85, D76, D163, D164×2/D165, and today's first-leg-as-terminal) because there is no single close path — there are at least seven, each hand-rolling its own booking. Today, on the first live day after hardening five of them, **a sixth path booked +$54.79 on a position the broker says lost −$916** — a tranche leg stamped as the whole close. The disease isn't any one path; it's that *paths can exist*.

**The fuel system**: behavior is defined by env-pins-overriding-defaults, and the D93 class has now recurred four times (Kimi pins, dead V3.1 defaults, the 4-week PARSE_FAILED embed, EXEC_MAX_POSITIONS=8-vs-3). Until yesterday, the configured *fallback* model had been unreachable for weeks — the system's safety net was a 400 error, and nothing noticed.

**The instrument cluster**: until this week, the speedometer was a journal that has **never matched broker truth on any audited day** (drift every single day of the last week: −284, +517, +1,758, +225, −79, and today −1,039). The gates that silently ate seven rockets in one day (D200/D204) ran unlogged for weeks. The watchdog's alarm wire was never connected. Three of nine pipelines had no failure path at all. The test suite carries a 76-red noise floor that camouflages regressions. Three bots share one account. The simulators fill 16 where the broker fills 5.

**And yet — the chassis is real.** The discipline became world-class before the architecture did: pre-registered experiments, winsorize-hostile verification, broker-truth doctrine, the adversary harness, a property-based oracle, an event-sourced ledger design that — in its first week of shadow — matched the broker **to the cent** on two of five days. The parts for the Huayra are already on the bench. That's the honest state: a Focus with a carbon-fiber tub in the trunk, unassembled.

## Part II — The Huayra: one organizing principle, eight builds

**The principle: ONE SOURCE OF TRUTH PER QUESTION; ZERO SILENT ANYTHING.** Every defect this month — phantom P&L, silent verdict deaths, sim divergence, dead alerts, config drift — is the same defect: two sources of truth disagreeing with nobody watching. The Huayra is the machine where that is structurally impossible, not gate-prevented.

**1. The Execution Core as a typed state machine.** Decompose the monolith's trading loop into five stages with hard interfaces: `Sense → Decide → Gate → Execute → Book`. Every order and position is a formal lifecycle (the ledger's event grammar IS the law: SUBMITTED→ACKED→FILL*→TERMINAL). There is exactly ONE entry path and ONE close path; "a new exit idea" becomes a *policy object* feeding the close path, never a new code path. The D-code sediment compiles down to versioned, testable policies. *This kills the phantom family at the species level.*

**2. The Ledger Cutover.** The shadow's exact-zero days prove the event-sourced fold is truth-grade. Promote it: the ledger becomes THE book; the journal becomes commentary; BOCPD/Kelly/reports/scorecards read the ledger. Booking P&L without a broker execution_id becomes a type error. (Path: ≥5 clean shadow sessions → drift forensics on the 6/4-6/8 deltas → cutover proposal to Pierce. Clock is running, day 1 tonight.)

**3. Train == Serve realism.** One `src/sim/execution_model.py` — the production gate stack + marketable-limit fill rule + calibrated spread — consumed by *every* replay (the B1 matrix proved gates explain 100% of divergence; fills were never the problem). Nightly sim-vs-broker calibration line with a CI gate (P/R ≥ 0.9), and **every live session automatically becomes a regression corpus** — replay-driven development. A strategy idea is then testable in an environment that tells the truth, which this repo has never once had.

**4. The Correlation Spine.** `verdict_id → order_id → execution_id → ledger event → fill` as one key family carried through every artifact, with `trace_lineage.py` as the proof: any fill, full provenance, one command. (VLL is the seed; the HWH bug would have been a 10-second query instead of a forensic.)

**5. Config as law.** Single typed source; `config_drift_audit.py` in CI; boot-time assertion that resolved-config == intended-config posted to the morning brief. The "default was dead for four weeks" class ends.

**6. Self-proving observability.** Every alert path fires a synthetic monthly (the watchdog lesson: an alarm that has never rung is decoration). Real-time CRITICAL paging with synthetic-marker filtering. SLOs (boot, pipelines green, recon zero, VLL orphans zero, calibration in-band) rendered as ONE morning brief — the entire machine's health in one message.

**7. The Research Factory.** Productize what this month proved out by hand: hypothesis → pre-registered gates → staged flag (default-OFF) → arena A/B with a promotion rule (the T2 n≥30 pattern) → graded nightly by the same scoreboard. Strategy changes stop being deploys and become *experiments with exit conditions*. The falsification engine that killed seven illusions becomes the assembly line.

**8. Capital architecture.** Per-strategy paper sub-accounts (isolation keys), a portfolio-level risk budget object (daily circuit, per-name caps, breadth structure as config), and the barbell doctrine (many small tickets × wide stops × hold) encoded once, not scattered across env pins.

## Part III — The build order (no rewrite; eight refits, most already seeded)

| # | Build | Seed already on the bench | Effort |
|---|---|---|---|
| 1 | Ledger cutover | shadow live, 2× exact-zero days | shadow week → flip |
| 2 | Unified execution model + CI calibration | B1 matrix + 10-line spec | days |
| 3 | One-close-path refactor (state machine core) | attempt_close_with_status_check is the prototype | the big one — weeks, staged |
| 4 | Spine + trace_lineage | VLL live, execution_id verified | days |
| 5 | Config-as-law + drift CI | drift auditor speced, health check live | days |
| 6 | Self-proving alerts + morning brief | health line live, paging built flag-OFF | days |
| 7 | Research factory | T2 arena + promotion rule pattern proven | process, not code |
| 8 | Capital architecture | isolation plumbing + caps shipped | Pierce's keys + days |

The Focus got us here. The discipline is already Huayra-grade — this month it found and killed more real defects than most systems find in a year, and not one number was bent doing it. What remains is to make the *architecture* deserve the discipline: one truth per question, zero silent anything, and a machine where the next HWH is a type error instead of a forensic.
