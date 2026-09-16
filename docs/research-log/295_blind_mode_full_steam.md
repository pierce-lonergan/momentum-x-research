# 295 — BLIND-MODE FULL STEAM: neither gate certifies, so the session fed them, aimed them, and priced the wait

**Author**: Claude (Fable 5, ultracode) | **Date**: 2026-07-12 (evening) | **Class**: §B blind-mode — coverage feeding, the aiming scheduler, death-date math, forward-instrument pre-build, Stage-4 draft; ZERO gate statistics computed | **Mandate (Pierce)**: full steam ahead — which, under the discipline, means exactly this.

> **First line: branch §B fired — neither gate certifies (SEVP 11/300 covered events; Stage-3 3/20 usable names), both stayed shut, and no statistic was computed on partial coverage.** The session's product is velocity with aim: the full 151-name earnings-event cache (603 events — revealing the honest denominator), a greedy events-per-call scheduler now steering the collector, continuous in-session collection slices, the SEVP *forward* shadow-ledger pre-built (so a future pass costs zero ramp time), the Stage-4 draft written to its procurement boundary, and one ledger ruling (LETF: blocked-at-$0 by its own calibration clause). **The unblind math is now explicit — and it puts a number on the one item only you can click.**

## §0 — Census (clock-corrected)

Still Sunday 7/12 evening — so rocket-gate n=3/30 and forward-RV n=0 are **correct, not stalled** (both advance starting tomorrow's session). Lineage clean at `f0c72c2`. Nightly IV task: **NOT REGISTERED**. Catch-up triggers: **still one trigger — FIFTH session pending.**

## §1 — The honest denominator, and the scheduler it forced

The full-panel event cache (yfinance, $0; Polygon filing cross-check deferred to unblind-time over covered events only, disclosed): **603 in-window events across 139 names — median 4/name** (deep-pull verified: not clipping; yfinance history is genuinely sparse for many names). That reshapes the unblind arithmetic: naive alphabetical collection would need ~75 names. The **greedy events-per-call scheduler** (`collector_priority.json`, consumed by `--names auto`, wired into the nightly wrapper) sorts names by in-window event count so every API call buys maximum gate progress: **~71 names ≈ 4,686 calls ≈ 16.3 hours of drip to SEVP's N≥300**, with Stage-3's 20-name minimum reached long before, in passing.

## §2 — The death-date math (the Pierce number)

| gate | needs | WITH nightly task (1,150 calls/night) | WITHOUT (session-only, ~250 calls) | death |
|---|---|---|---|---|
| Stage-3 power (20 names) | ~1,100 calls | **~1 night** | ~4-5 sessions | 2026-08-15 |
| SEVP N≥300 | ~4,686 calls | **~5 nights** | **~19 sessions** | 2026-09-01 |

Both make their death dates comfortably **with** the task; without it, SEVP's margin depends entirely on session cadence. Bar-lowering and deadline-sliding are not remedies and are not offered; the remedies are yours: **register the task (one-liner, no elevation), or optionally pay for vendor speed (291_PROCUREMENT).**

## §3 — Built this session (all blind-safe)

- **SEVP forward shadow-ledger** (`_doc295_sevp_forward_ledger.py`): tracks only FUTURE earnings events (forward-start 2026-07-14), marks at collector prices, certification bar copied verbatim from the frozen verdict map (n≥60, CI95>0 at the 5% line, median>0), self-repairing. If the backtest gate passes later, forward evidence is already accruing that same day; if SEVP dies, this retires unread. Status: PENDING-COLLECTION, n=0 (correct — no forward events exist yet).
- **Stage-4 monetization prereg DRAFT** (`_doc295_STAGE4_PREREG_DRAFT.md`): spread-aware fill rules (sell-at-bid/buy-at-ask), assignment/pin handling, gates stated directly in TARGET.md units (≥+1.0%/ticket net = the 10%-of-requirement floor), capacity statement mandatory. **Blocked, by design, on the one input we don't own: EOD option NBBO quotes** — the aggregates give trade closes; Stage 4's entire job is replacing haircuts with quoted spreads. Those procurement fields stay ⟨OPEN⟩ for you.
- **Collector aiming** (`--names auto` + nightly wrapper updated) and continuous in-session slices (state-resumable; the doc-294 network-retry hardening held).

## §4 — Ledger ruling: LETF close-window flow — BLOCKED-AT-$0 (recorded, not built)

The doc-293 triage QUEUED it with a *mandatory* flow-vs-raw-return rank-calibration (else it's intraday momentum in disguise). At $0 the only available demand proxy is constant-AUM — under which the flow term is a **monotone transform of the raw day-return**, making the mandatory separation test unsatisfiable. The ledger decides: **cannot-run-honestly-at-$0; queued behind historical AUM/shares-outstanding data; no build, no peek.** Recorded in ATTEMPTS_LEDGER.

## §5 — Multiplicity + statuses

Gate statistics computed this session: **0.** Plumbing: event cache (603), coverage counters (11/300; 3/20), scheduler projection, forward-ledger n=0, two probes (yfinance depth ×3 names). Attempts-ledger additions: LETF → BLOCKED-AT-$0. Rocket-gate 3/30 (advances tomorrow); forward-RV 0 (starts tomorrow); tests green; commits pinned in §6.

## §6 — Pierce-action list (priority order, with the cost of idleness attached)

1. **`_doc288_apply_catchup_triggers.ps1` elevated — FIFTH session pending.** One click; the bot still has no relaunch path until then.
2. **Register the IV-collector nightly task** — one-liner in `scripts/iv_collector_nightly.cmd`'s header. *The math: with it, Stage-3 opens in ~1 night and SEVP in ~5; without it, ~19 sessions of manual drip. Every idle night costs one night of both gates' progress.*
3. Standing: kill/continue ratification; Stage-1 config-diff HELD; Stage-4 quote-data procurement (only relevant after a gate passes).

**Distance statement: unmoved — 27% of requirement measured, 0% certified — and unmovable this session by design: no gate had earned its opening, and the discipline does not open unearned gates. What moved is the slope: the machine now aims every API call at the two verdicts, knows its unblind dates to the night, and has the forward instrument pre-built so a pass converts to accumulating evidence with zero lag. The next distance movement has a date, and the date has a price, and the price is one click of yours.**
