# D220 Full-Throttle Sprint — Execution Log

**Date:** April 16, 2026
**Branch:** `d220-full-throttle`
**Commits:** 5 phases + 1 hotfix
**Wall clock:** ~5 hours including diagnostics
**Tests:** 120 D220-touched, all passing
**Production status:** code on branch, NOT merged to develop, NOT deployed

This is the document tomorrow-morning Pierce needs to read before doing anything
with the shadow data. It is the day's narrative, not a checklist. The most
important section is § Phase 4 → diagnostics → Phase 5 — read that one twice.

---

## Phase 1 — Day 0 fixes (25 min, vs 90 min SLA)

Three changes that unblocked trading:

1. **D112 router float cap 200M → 2B** with MFCS-based escape hatch in
   `adaptive_router.py:148`. The escape hatch is a CODE change, not just a
   config value: when `deterministic_mfcs >= 0.40`, the structural float check
   is bypassed. This means even reverting the config value to 200M wouldn't
   re-create the original bug.
2. **VWAP gate skip 5min → 10min** and threshold 0.5% → 2% in
   `orchestrator.py:1320`. Apr 16 had 14 rejections at 0.6%-3.5% below VWAP,
   most of which are noise on small caps.
3. **Float sanity ceiling** in `main.py:1450`: drop Finnhub
   shareOutstanding ≥ 50,000 millions (50B shares). Catches the XHG=98B
   shares Finnhub units bug from this morning.

7 unit tests + drift guards on the orchestrator/main.py constants. Static
analysis baselines unchanged. Commit `36c5fbb`.

## Phase 2 — Production arena (65 min, vs 3hr target)

Built `src/production_arena/` — an end-to-end harness that runs the production
gate cascade against historical scenarios. Five files (~1,870 LoC):

- `types.py` — frozen Scenario / ProductionVerdict / SweepResult / ArenaConfig
- `scenarios.py` — loader for the 502-scenario backfill
- `verdict.py` — JSONL I/O + `journal_to_verdict()` bridge for divergence checks
- `aggregator.py` — gate counts, daily rollup, Kelly-sized PnL sim, sweeps
- `pipeline_runner.py` — gate-replay engine
- `cli.py` — `--validate-load`, `--date`, `--dates`, `--sweep`, `--parallel`

**Key design pivot during 2.4:** the orchestrator audit revealed that full
LLM-replay needs litellm mocking + time.monotonic patching (used 13+ times) +
reset of FinBERT/circuit-breaker singletons — 4-6 hours for limited extra
signal since LLM mocks return deterministic outputs anyway. I switched to
**gate-replay mode**: synthesize agent signals deterministically from candidate
features, run the EXACT gate logic. Sub-second for 502 scenarios. Faithful to
gate semantics. Documented at the top of `pipeline_runner.py`.

**First-run result:** 233 BUY verdicts vs 0 from production. Equal-weight P&L
−23.82% / Sharpe −13.35 — confirmed the D219 backfill finding (selection alone
is unprofitable, calibration is the value-add).

Divergence vs production journal: April 14 = +5 (within ±20 budget, fully
attributable to D219+D220 changes). Apr 15-16 = 0 vs 0 (no scenarios). 44 tests.
Commit `89a6764`.

## Phase 3 — Composite score V0 (45 min)

Trained two L2-regularized logistic regressions on 407 labeled rows:

| Model | Train AUC | CV AUC | Overfit gap |
|-------|-----------|--------|-------------|
| FULL (with arena_buy_verdict) | 0.7820 | **0.7564** | +0.026 ✓ |
| PRESCORE (without) | 0.6894 | 0.6612 | +0.028 ✓ |

The +0.10 AUC gap between FULL and PRESCORE quantifies how much information
arena_buy_verdict carries — the cascade-anti-selection finding from Phase 3.0
made concrete.

**Hard-isolation rule:** `src/composite/*` MUST NOT import from `src.core.*`,
`src.agents.*`, or `src.production_arena.*`. Three AST tests verify this.
The composite scorer is the future replacement for the cascade and must be
runnable from a Jupyter notebook with no production code in its import graph.

**Phase 3.4 in-sample finding (later corrected — see Phase 4 → diagnostics
narrative below):** composite-threshold sweep on 171 cascade-BUYs showed
threshold ≥ 0.40 gave 14 trades, 57.1% WR, +0.73% avg PnL. This was the first
positive-EV number the system had produced and it felt like a discovery.

24 tests. Commit `f24fcd4`.

## Phase 4 → diagnostics → Phase 5 (the most important section in this document)

This is the sequence that defined the day. Read it carefully.

### Phase 4 (75 min) — the calibration sweep that produced the wrong headline

Ran a 1,080-config × 502-scenario × 2-mode sweep with 5-fold cross-validation.
Two strategies tested:

- **STANDARD**: composite-filter cascade BUYs at various thresholds.
- **INVERTED**: trade what the cascade rejected, excluding ORB-rejections.

The headline result that I shipped:

| Strategy | CV avg PnL | CV trades | CV stability |
|----------|------------|-----------|--------------|
| STANDARD (composite ≥ 0.40) | −0.48% | 17 | wild: +9.5% to −6.6% |
| **INVERTED (NT-excl-ORB)** | **+16.19%** | **155** | every fold +12% to +20% |

I framed the +16.19% as "rock-solid across all 5 CV folds" and went into the
Phase 5 commit ready to ship shadow wiring around this finding. The Phase 4
diagnostics section also produced what looked like a deep mechanism finding:
the cascade had median MFE +8% / median close −7% (spike-and-fade), while
NT-excl-ORB had median MFE +23% / median close +10% (genuine winners). The
narrative that wrote itself: "the gates are anti-selecting, here's the
inverse strategy that beats them."

### The pushback (5 min that saved the project)

The user stopped me before Phase 5 wrote any code. Direct quote of the gist:

> "+16.19% CV average PnL over 155 trades with 84-91% win rates across every
> fold is not a finding that arrives without a mechanism. Legitimate edges in
> small-cap gap-ups do not look like this. ... When something looks like free
> money, it almost always means the measurement apparatus is wrong, not that
> the market is."

Four specific concerns named: entry price assumption, ORB lookahead, composite
filter contributing nothing, MFE/MAE 5:1 ratio inconsistent with small-cap
volatility.

### The diagnostics (30 min — `scripts/d220_phase4_diagnostics.py`)

Five checks — actually six, the user added a "true holdout" requirement:

| # | Check | Verdict |
|---|-------|---------|
| 1 | Entry price (labeled vs realistic VWAP) | **PASS** — slippage 0.31% favorable; 2.5pp WR drop. Not the artifact. |
| 2 | ORB lookahead | **FAIL** — the +16.19% requires post-9:35 ORB info filtered against a 9:31 entry. Honest 9:31 universe (ALL NT) is +5.87%. |
| 3 | Composite filter contribution | **PARTIAL** — composite is high on the inverted set (median 0.654) and stratifies it, but the model was trained on this same data with arena_buy_verdict as a feature, so it memorized the very signal we're testing. |
| 4 | MFE/MAE 5:1 ratio | **REAL** — survives realistic-entry recompute (4.59:1). The inverted set genuinely has favorable excursion. |
| 5 | True 20% holdout | **FAIL** — IS +16.19% drops to +10.22% on holdout (with lookahead). Without lookahead: +0.62%. ~6pp additional sweep-tuning overfit on top of the lookahead artifact. |
| 6 | Realistic strategy (decision at 9:36, entry 9:36-9:37 VWAP, ORB filter) | **REAL but smaller** — n=32, 65.6% WR, +9.86% mean PnL. Holdout n=7, 71.4% WR, +8.24% mean PnL. |

### The corrected finding

> **There may be a tradeable edge in cascade-rejected candidates that broke
> their 5-min opening range high by 9:35, entered at 9:36-9:37 VWAP, with
> realized per-trade EV somewhere in the +2% to +10% range, that warrants
> live shadow validation over 5-10 sessions. Sample sizes are small (n=32 IS,
> n=7 holdout) so the point estimate is noisy.**

### What the CV failed to catch and why

The lookahead was structural to the universe definition (NT-excl-ORB used
post-9:35 ORB information while the entry was at 9:31). Every fold shared the
same artifact, so the CV looked clean. The 20% holdout caught it because the
sample was small enough to feel the noise; the realistic 9:36-entry diagnostic
confirmed the mechanism. **Lesson: always pair CV with a true holdout when
the universe definition itself might leak information.**

### Phase 5 (60 min) — the corrected scope

Per user's revised spec, four constraints:

1. **Composite shadow is primary.** Inverted is secondary. Independent kill
   switches.
2. **Inverted shadow uses a paranoid schema.** Every decision-time field is
   timestamped. Construction validates: decision_timestamp ET ≥ 9:36:00,
   orb_break_timestamp < decision_timestamp, simulated_entry_basis from a
   fixed enum, would_have_inverted_bought requires orb_broken_by_decision_time.
3. **Two-layer kill switch.** `SHADOW_SCORING_ENABLED` and
   `SHADOW_INVERTED_ENABLED` are independent env vars.
4. **Both shadows are write-only.** Production decision code MUST NOT read
   shadow fields. Enforced by a static AST guard
   (`tests/static_analysis/test_shadow_isolation.py`, 4 tests).

Files: `src/shadow/{logger,composite_shadow,inverted_shadow}.py`. Orchestrator
hook in `_build_no_trade_verdict` and at the end of `evaluate_candidate`,
both wrapped in try/except. Calibration report (`06_threshold_calibration.md`)
got SUPERSEDED markers and Appendix B with the corrected findings — the
original +16.19% claim is preserved with strikethrough so future-Pierce can
see the correction as a first-class fact.

22 tests. Commit `de94f22`.

---

## What I got right today

- Phase 1 shipped clean and on time. Tests caught my own test-fixture mistake
  (1.2B vs 2B float) before commit.
- Phase 2's gate-replay pivot was the right call. Full LLM replay was a
  3-hour distraction that wouldn't have changed the answer.
- The cascade-anti-selection diagnostic mechanism (cascade selects pump
  patterns, rejects genuine catalysts) is real and consistent with the
  user's prior intuition. It's *smaller* than I claimed but real.
- Phase 5's hard-isolation guard for src/composite caught zero violations
  today, but it's the wall that prevents future-Pierce from quietly wiring
  the composite into production decisions before shadow validates.

## What I got wrong today

- **Phase 4 headline (the +16.19% claim) was lookahead-inflated.** I framed
  "every fold +12% to +20%" as rock-solid without checking whether the
  universe definition required information not available at decision time.
  This is the mistake the day was supposed to catch in pre-deployment
  analysis, not in tomorrow morning's live trading.
- I built CV folds that all shared the same lookahead structure, so the CV
  couldn't catch it. **A true holdout would have caught it on the first
  pass; CV alone was insufficient.**
- The "composite filter doesn't add value in inverted mode" finding was a
  RED FLAG I missed. If a filter does nothing, the strategy is the universe,
  not the filter — which means the labeling methodology is the strategy.
  That's an artifact warning sign and I read past it.

## What is now on disk vs what is in production

**On `d220-full-throttle` branch (commits `36c5fbb` → `de94f22`):**
- All Phase 1 fixes (D112 float, VWAP gate, sanity check)
- Production arena (`src/production_arena/`)
- Composite score V0 (`src/composite/`, models pickled in `models/`)
- Shadow telemetry (`src/shadow/`)
- Diagnostics + calibration reports in `docs/research-log/`
- 120 D220-touched tests

**In production (`develop` branch):**
- Nothing from D220 yet. The branch has not been merged.

## Decision made: merged to develop

**At ~9:30 PM ET April 16:** d220-full-throttle was fast-forward merged to develop
(HEAD `d0f4c67`) and pushed to origin. Pre-merge full pytest suite confirmed
the 19 pre-existing test failures are NOT D220 regressions; one D220 hotfix
(silent-handler whitelist on `src/model_arena/__init__.py:25` for the optional
dotenv import) was committed before merge. Task Scheduler verified pointing
at the right repo path; NextRunTime = 4/17 04:30 AM ET. The 4:30 AM scheduler
will pick up D220 automatically.

## (Original) Decision needed section, preserved for the audit trail:

Pre-merge checklist:

- [x] All 5 phases committed and tests passing
- [x] Static-analysis baselines unchanged
- [x] Hard-isolation guards in place (composite + shadow)
- [x] Visible correction of the Phase 4 headline
- [x] Two-layer kill switch ready
- [ ] User approves merge

If you merge to develop tonight:
- Tomorrow's 4:30 AM scheduled run will execute Phase 1 fixes (trades unblock)
- Composite shadow will start logging to `data/shadow/shadow_2026-04-17.jsonl`
- Inverted shadow stays inactive UNLESS a separate batch job is wired (which
  Phase 5 did NOT ship — see § What's NOT shipped)

If you don't merge:
- The system runs Apr 16 evening's deployed code (D219 only). No D220 fixes.
- We lose tomorrow's first shadow data session.

## What's NOT shipped (intentional gaps)

- **Inverted-shadow batch script.** The schema and write API exist; the
  thing that actually runs at 9:36 ET to compute inverted-shadow entries
  was deferred. To be added Day 6 (April 17 evening) once we see the day's
  composite shadow data and decide whether the inverted shadow is worth the
  complexity.
- **Auto-promote logic.** No code path that uses composite or inverted scores
  to make trade decisions. Per the calibration: "no live promotion until
  5+ shadow sessions confirm." This is enforced by the AST guard.
- **Live retraining.** Composite v0 is the only trained model. V1 will be
  trained on shadow data + backfill once we have ≥5 sessions of real-LLM
  signals.

## File index of D220 deliverables

```
src/composite/                        Composite score package
src/composite/{__init__,features,score,train}.py
src/production_arena/                 Arena package
src/production_arena/{__init__,types,scenarios,verdict,aggregator,pipeline_runner,cli}.py
src/shadow/                           Shadow telemetry package
src/shadow/{__init__,logger,composite_shadow,inverted_shadow}.py

tests/static_analysis/test_composite_isolation.py
tests/static_analysis/test_shadow_isolation.py
tests/integration/test_arena_pipeline_runner.py
tests/integration/test_shadow_scoring.py
tests/unit/test_d220_day0_fixes.py
tests/unit/test_arena_scenarios.py
tests/unit/test_arena_verdict.py
tests/unit/test_arena_aggregator.py
tests/unit/test_composite_score.py

models/composite_v0_full.pkl
models/composite_v0_prescore.pkl
models/composite_v0_metadata.json

scripts/d220_phase4_sweep.py
scripts/d220_phase4_diagnostics.py

docs/research-log/00_index.md  through  08_morning_monitoring_plan.md
```

The morning monitoring plan is in `08_morning_monitoring_plan.md` —
read that next.
