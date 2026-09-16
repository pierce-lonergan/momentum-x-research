# SYSTEM_MAP changelog

Append-only. Newest entry on top. Every commit that adds a new ship doc
to `docs/research-log/` MUST add an entry here describing what changed in
SYSTEM_MAP (enforced by `.githooks/pre-commit`).

Format per entry: `## doc N -- YYYY-MM-DD -- short title`, followed by
bullet list grouped by SYSTEM_MAP file.

---

## doc 297 -- 2026-07-28 -- THE COST RESULT: new 0.1%/day directive, the universe closed, the counter built
- DIRECTIVE (Pierce): floor target is now +0.1%/day (was 0.5%/day). docs/TARGET.md rewritten: the old
  "+10.0%/ticket" per-ticket table is REPLACED by the identity `account return = deployment x turns x
  net-per-ticket`. Deployment/turns are sign-preserving multipliers on the REQUIREMENT side only --
  the 5x bar cut reopens NOTHING. Requirement at measured deployment = 187 bps/ticket.
- 12-agent assessment fleet + 5-agent verification fleet (~3.1M tokens). Verification changed FOUR of
  the assessment's headline numbers; both are reported in the doc.
- SEARCH CLOSED on the low-float gapper long universe: -2.041%/ticket, day-blocked CI [-2.823,-1.226],
  negative in 2024, 2025 AND 2026 separately, 64.8% of sessions negative. Round-trip cost 61.3 bps
  (Roll estimator; the 213.7 bps figure that circulated is a post-close NBBO snapshot, 3.5x overstated)
  vs a 25-58 bps/ticket requirement.
- Overnight-ETF sleeve CLOSED: replication SUCCEEDED (SPY 6.13 bps gross, robust to exit timing) but
  1.566 of 4.769 net bps is T-bill interest, excess-of-cash CI spans zero, buy-and-hold beats it on
  Sharpe 4/4 in 2025+, 1.50 effective bets. Certifying it beats a T-bill = 3,079 sessions.
- Book state: flat, not bleeding (+1.2 bps/day, three independent measurements). Books are CLEAN --
  the "29% unattributable equity" scare was a UTC-dating artifact; true unattributed motion is $0.00
  and the account reconciles to the tape at $0.63 over 110 sessions. Lifetime "+90.6%" is two lottery
  tickets (LASE = 81.3% of all P&L at 27.4% of equity).
- BUILT `scripts/trial_registry.py` + tests: hash-chained, tamper-evident multiplicity counter; the
  promotion bar is computed FROM the trial count (Bailey/LdP expected-max-Sharpe, reproduces the
  reference figures 1.068/1.628); `promote()` refuses by design. At ~60 trials on 1yr the null-expected
  best Sharpe is 2.35.
- FIXED a live safety defect: `deterministic_technical.py` derived stops as `price - 1.5*ATR(14)` with
  no floor, producing NEGATIVE (unplaceable) stops 10x Feb-Jul 2026 -- incl. 2026-07-28 (LGHL) and one
  that reached `filled` (EHGO 07-01). Naked positions = the doc-281 "vanished stop" class at its source.
  Clamped + loud red flag. 9/10 new tests fail on old code, 10/10 pass on new, 125 existing tests green.
- RECORD: doc 296 and commit 09a700f were misdated 2026-07-13 by a skewed sandbox clock; corrected.
  Stage-3 death is 18 days out, SEVP 34. IV collector has not run since 07-13 (task never registered).
- VERIFIED entitlements: PAPER account has options L3, 5,249 shortable, 4x margin, crypto, overnight;
  Alpaca serves full option chains with OPRA NBBO + free greeks at 10,000/min. Polygon was NEVER
  5 calls/min either (measured 708/min) -- the 20-hour doc-293 drip was self-imposed. BUT: paper != live
  (live host 401), historical option NBBO is NOT served (404), short-door tombstone stands.
- SUBSTRATE (Pierce): 40% deployment is authorised in .env and config_truth_recon alarms only on
  OVER-deployment -- a ramp would be silent; 1,584/1,584 incidents unresolved because no resolve code
  path exists; the watchdog's `$cmd -match "momentum"` matcher has killed a sibling sleeve 6 times.

## doc 296 -- 2026-07-13 -- THE WRONG HORIZON: Stage-3 unblind passes every frozen gate, fleet kills it (h=1 vs frozen h=21)
- SS-A unblind executed exactly: power GREEN 3.999% at 62 names -> hash re-verified (ae62ea59 = 5c2b0e7) ->
  all frozen gates passed (G1 +5.106% CI excl 0 both halves; G2 HLN lambda 0.639 CI [0.588,0.702]) ->
  PROVISIONAL -> mandatory 3-skeptic fleet -> ARTIFACT (critical): runner evaluated h=1 next-day RV where
  the prereg chain froze h=21/30d tenor. Frozen-horizon counterfactual on the same panel: -4.43%, CI
  [-0.0119,+0.0005], both halves negative -- replicates the doc-292 pilot. VERDICT: pass VOID; vol-door
  family closure RECOMMENDED (The operator ratifies); VRP ladder did NOT unqueue. TARGET.md distance: certified
  edges still none.
- MAJOR fleet find: IV-store look-ahead -- 44% of panel rows from contracts strike-selected at a FUTURE
  monthly anchor. Fixed: `_doc293_iv_collector.py` emits only anchor-or-later rows (IV + prices; backfill
  guarded; `contract_anchors()` provenance); `_doc296_purge_lookahead.py` purged stores retroactively
  (prices 67,579->50,410; IV 38,574->27,263; backups .bak-doc296). SEVP coverage recounted 227->205/300 --
  SEVP stays BLIND and is now protected; death 2026-09-01 unchanged.
- `_doc294_stage3_runner.py` conformed to the frozen target (h=21 forward annualized variance,
  fully-realized MZ calibration, 21-session train embargo, empty-panel guard): post-purge status 59 usable
  names / 2,350 rows / 0 scored eval days = NOT-COMPUTABLE; densification would cost ~18K calls past the
  death date to re-ask an answered question.
- Disclosed: power-gate knife-edge (3.9994% vs 4.0; 2/10 seeds RED), QLIKE vol-vs-variance units, 120-day
  usability filter. New standing method rules in ATTEMPTS_LEDGER: executable-prereg fixture before power;
  pass = PROVISIONAL until fleet; selection-provenance on derived rows.
- ATTEMPTS_LEDGER: Stage-3 family -> CLOSED-recommended; VRP ladder closes with it; Stage-3 accept/kill
  process gate CONSUMED. TARGET.md doc-291 row updated to point at doc 296.

## doc 295 -- 2026-07-12 -- BLIND-MODE FULL STEAM: gates fed + aimed, death-date math explicit, LETF ruled BLOCKED-AT-$0
- Census: SEVP 11/300, Stage-3 3/20 -> branch B (no gate opened; ZERO gate statistics computed). Clock-corrected:
  rocket-gate 3/30 + forward-RV 0 are CORRECT (still Sunday; both advance Monday). Nightly IV task NOT
  registered; catch-up triggers FIFTH session pending.
- Full-panel earnings-event cache: 603 in-window events / 139 names, MEDIAN 4/name (deep-pull verified: yfinance
  history genuinely sparse, not clipping) -> honest denominator. GREEDY events-per-call scheduler
  (collector_priority.json + `--names auto`, nightly wrapper updated): ~71 names / ~4,686 calls / 16.3h drip to
  SEVP unblind; Stage-3's 20 names reached in passing. DEATH-DATE MATH: with nightly task Stage-3 ~1 night,
  SEVP ~5 nights; without it ~19 sessions -- the task one-liner is quantifiably the binding constraint.
- Pre-built blind-safe: `_doc295_sevp_forward_ledger.py` (FUTURE events only, forward-start 7/14, cert bar
  copied from the frozen verdict map, self-repairing) + `_doc295_STAGE4_PREREG_DRAFT.md` (spread-aware sim;
  blocked by design on EOD option NBBO quotes -- procurement field for Pierce).
- LEDGER RULING: LETF close-window flow = BLOCKED-AT-$0 -- the doc-293-mandated flow-vs-raw-return
  rank-calibration is unsatisfiable under constant-AUM (flow == monotone transform of day return); queued
  behind historical AUM data; recorded without build or peek.

## doc 294 -- 2026-07-12 -- ARMED AND BLIND: SEVP kill test + Stage-3 accept/kill frozen, built, refusing to peek
- Prereg frozen pre-execution (5c2b0e7, sha256 ae62ea59...): SEVP (short ATM straddle T-1->T+1 earnings;
  option-PRICE economics; cost lines 5%/10% of premium; G1 unconditional carry + G2 RV-conditioner sub-gate
  + fat-tail rule; unblind at N>=300 two-leg events; death 2026-09-01) + Stage-3 dated accept/kill (from the
  doc-292 draft, sources filled; sign-randomized power gate <=4% resolution; death 2026-08-15). Verdict maps
  frozen before any answer existed.
- Earnings dates $0: Finnhub free tier has NO history (probed, 0 rows); yfinance DOES (timestamped, 12q) +
  Polygon filing-date cross-check. 43 events cached (10 names).
- `_doc293_iv_collector.py` now stores option EOD PRICES alongside IV (+ --backfill-prices re-pull for
  pre-change contracts; 399 price rows landing). `_doc294_sevp_pipeline.py` (events/coverage/run-with-refusal:
  live 3/300 covered, PENDING-COLLECTION) + `_doc294_stage3_runner.py` (panel+MZ-calibration+power gate:
  NOT-COMPUTABLE at 2/20 names, stays blind, refuses --run). ZERO gate statistics computed this session.
- Unblind ETA ~2 nights of the nightly collector task -- whose registration (one-liner, no elevation) is now
  the binding constraint on both gates. Catch-up-triggers script: FOURTH session pending.

## doc 293 -- 2026-07-12 -- THE 0.5%/DAY BAR frozen as engineering + Stage 3 UNLOCKED AT $0 + the candidate triage
- Pierce standing directive: target = 0.5%+/day (+251%/yr). docs/TARGET.md commits the requirements table
  (daily @5% cap = +10%/ticket net; best-ever measured config = 27% of requirement, post-hoc; certified = 0)
  + the >=10%-of-requirement build filter + the constraints the target does NOT override (5% cap frozen;
  leverage/options = Pierce). docs/ATTEMPTS_LEDGER.md = program-level multiplicity (16 closed families).
- ENTITLEMENT PROBE (read-only, existing key): historical option EOD aggregates ENTITLED (~2yr rolling,
  5 calls/min; ladder-probed); IV snapshot 403. -> `_doc293_iv_collector.py`: BS-inversion IV collector
  (monthly ~30d ATM call+put anchors, spot from own warehouse, resumable state, polite) VALIDATED live
  (AAPL median 30d IV 25.9%); first 8-name tranche collecting; `iv_collector_nightly.cmd` written (task
  registration = Pierce one-liner; auto-mode classifier correctly denied agent-side task creation).
  Stage 3 needs NO purchase -- ~9 nights of drip. 291_PROCUREMENT.md updated (paid tier = speed only).
- CANDIDATE PANEL (6 lenses vs TARGET, triage recomputed every chain): BUILD-NEXT = SEVP scheduled-event
  vol carry (~18% of req at its own pessimistic line -- only family clearing the filter there) + the
  Stage-3 dated accept/kill; QUEUE = conditional VRP ladder (13%, 44% behind Stage-3), LETF close-flow
  (<5% central); REJECT = turns-compressor (4-8%, fails filter), market-neutral reversion (closed-family
  variant). All recorded in ATTEMPTS_LEDGER (FILTERED/QUEUED sections).
- Housekeeping: rocket-gate n=3/30 correct; forward RV ledger PENDING (forward starts 7/13); catch-up
  triggers STILL unregistered (4th session) -- top of Pierce list.

## doc 292 -- 2026-07-12 -- THE $0 IV PILOT: AMBIGUOUS by the frozen map (G1 unmeasurable at +/-6.3% resolution; G2 non-subsumption SURVIVES the HLN skeptic) + forward RV shadow-ledger LIVE + Stage-3 drop-in ingestion
- Prereg frozen pre-execution (73cc3bc, sha256 eb83df87...). A1: all 13 CBOE vol-index series ALIVE through
  7/10 (single-name VXAPL/VXAZN/VXGOG/VXGS/VXIBM not discontinued); 11 pairs covered by the warehouse; $0.
- A2 PILOT (30d tenor, 4,620 OOS forecasts, walk-forward, VRP-calibrated IV): G1 FAIL (+1.05% < 2% MMI,
  inside +/-6.26% resolution -- the frozen power clause bit as written); G2 PASS (encompassing w_C=0.42
  CI[0.23,0.77]). VRP calibration mattered hugely (raw IV 0.352 -> calibrated 0.266; "raw-IV comparisons are
  flattery" validated). 9d replication sign-NEGATIVE (short tenor dead). VERDICT: AMBIGUOUS per frozen map.
- G2 SKEPTIC (HLN form): SURVIVES pooled (lambda 0.47, CI[0.29,0.82] block-42, [0.31,0.83] block-63; not a
  multicollinearity artifact despite corr 0.956) BUT per-group: index-ETF lambda 0.60/+6.4% QLIKE vs
  SINGLE-NAME lambda 0.27 (block-63 CI includes 0) with QLIKE improvement -4.2% (4/5 names negative) --
  the group a purchase would buy is the weakest. Recommendation: conditional yes, hardened -- cheapest path
  (entitlement check) only; priced pull weighed against letting the free forward ledger accumulate.
- WORKSTREAM B: `scripts/_doc292_rv_forward_ledger.py` LIVE -- nightly challenger-vs-HAR scoring on the
  151-name panel, T+1 self-repairing, wired into post_close_scorecard + config_truth_recon (measurement-
  aware). Certification bar FROZEN: n>=60 fwd sessions, CI excl 0, >=2%; review-dead at 120. Seeded 10
  retro rows (excluded); PENDING-COLLECTION; forward from 2026-07-13. 6 status-logic tests.
- WORKSTREAM C: vendor-agnostic EOD-IV store + Polygon/ORATS-shape loaders (percent-vs-decimal autodetect),
  synthetic fixtures only, 5 tests; `_doc292_STAGE3_PREREG_DRAFT.md` (HLN G2 + pre-unblinding power gate).
- Housekeeping: rocket-gate n=3/30 zero holes (correct -- no sessions since 7/9); catch-up-triggers script
  STILL not run (third session pending).

## doc 291 -- 2026-07-11 -- THE VOLATILITY DOOR: open at the HAR-RV level (first passing gate in program history) -- carried by GENERIC features, not our vocabulary
- Preregs frozen pre-execution (7cc09fe, sha256 5155f3df...): Stage 0 decomposition, Stage 1 defensive
  utility, Stage 2 HAR-RV transfer incl. frozen minimum-meaningful-improvement (>=2% pooled QLIKE, >=1%/half).
- STAGE 0 (decomposition of the doc-290 signal): ladder B1 log-price 0.128 -> B3 +trailing-vol 0.198 ->
  B4 +ticker-history 0.228 -> FULL 0.313. D1 (beats cheap+small+recently-volatile) PASSES; D2 (beats that +
  ticker history) FAILS (p=0.085 BH-fail, late-regime +0.004) -> frozen verdict: SIGNAL REDUCES TO A KNOWN
  VOL-PROXY STACK. Descriptive: FULL flags stop-outs at AUC 0.70.
- STAGE 1 (risk-shaping, proposal-only, 5% cap frozen/shrink-only): U1 top-quintile exclusion PASSES
  (stop-rate -22% CI excl 0, maxDD -25%) + U2 inverse-vol sizing PASSES (maxDD -61% @ 50% deployed); mean-net
  point estimates lean NEGATIVE (CIs incl 0) -> config-diff proposal emitted, recommendation HOLD (the book's
  problem is its mean, not its variance). U3 stop-width FAILS.
- STAGE 2 (the pivot go/no-go; $0 spend -- 151 liquid names / 70,966 OOS forecasts already local): ALL FROZEN
  GATES PASS -- challenger GBM[HAR+10 features] QLIKE 0.125 vs GBM[HAR-only] 0.133 (binding baseline; HAR-OLS
  0.254, EWMA 0.323): pooled +5.91%, H1 +8.4% / H2 +2.8%, CIs excl 0. 3-skeptic fleet: leakage CLEAN (survives
  transform-free losses; headline reproduced to the digit), composition ROBUST but SHOCK-LOADED (April-2025
  spike = 42% of differential; durable figure ~3.6%; 2026Q1 was a dead quarter; mechanism = shock-day reaction
  speed = IV's home turf), attribution: gain carried by last_hour_rv_share + volume/liquidity (GENERIC post-HAR
  literature), momentum-x path-shape/gap vocabulary ~= 0 (dropping path-shape IMPROVES it). Honest restatement:
  competence proven, not property transferred. Convergent with Stage 0 across two universes.
- 291_PROCUREMENT.md: Stage-3 (vs implied vol) vendor OPTIONS (Polygon entitlement check first, ORATS, CBOE
  DataShop, $0 VIX-family pilot), Stage-4 spread-realistic sim needs, kill/continue proposal for Pierce.
  NOTHING purchased/credentialed/permissioned. Pierce-action list incl. catch-up-triggers script STILL not run.

## doc 290 -- 2026-07-11 -- MICRO-REGIME CAMPAIGN: vector-DB architecture DEAD (H-LOCAL refuted at gate + effect-size level); the durable positive = predictable volatility scale
- Mandate: "micro-trends -> per-pattern strategies -> vector DB, ~2000 patterns, break 0.1%/day." Trap avoided
  by design (2000/6000 = n~3/pattern = industrial p-hacking); the falsifiable primitive H-LOCAL tested first.
  PREREG FROZEN pre-execution (commit 167a911, sha256 47b0aa95...c868dd): gates, features, models, nulls,
  multiplicity. Stage 0: 875 events / 51 sessions / 39 features (decision t0+15min, path-shape features,
  warehouse outcomes; warehouse minute_aggs ts_et VERIFIED correct -- trades_v1 trap does not afflict it).
- STAGE A FAILS (frozen gates): G1 fail (p=0.00995 vs alpha=0.005 -- and the verification fleet then KILLED
  the delta entirely: the +0.136 kNN-vs-GBM lift was a WEAK-BASELINE artifact; the same GBM on rank-transformed
  y scores 0.29-0.31 ~= kNN 0.31-0.33; true local lift ~0.00-0.03); G3 fail decisively (top-decile net -1.13%,
  CI[-8.3,+6.7]). Selectivity INVERTS: top-2% picks net -14.6% (verified by hand, 18 events/13 sessions,
  reproduced to 1e-9; open-entry sensitivity unchanged). Info-vs-scale curve rises to k~200 = one smooth
  GLOBAL surface, ~3-4 neighborhoods max, NOT 2000 micro-regimes. Stages B/C/D do not execute per prereg.
- THE CHARACTERIZATION: the real structure (rank-rho~0.30, survives leakage/ticker-exclusion/seed/regime/
  single-appearance-ticker attacks) is VOLATILITY SCALE, not direction -- score deciles order peak 3.7%->30.9%
  AND stop-out rate 5.7%->46.6% in lockstep; rho(score, fillable ret_close) = -0.098. Unmonetizable here (no
  direction; no options on microcaps). First measured, prereg'd, adversarially-survived signal of the program.
- WORKSTREAM B (doc-289 crack CLOSED): 0/32 pre-declared attention-coupling tests survive BH-within/Bonferroni-
  across (6 families, 16 cells); pm-$vol leader wins 0/51 cohorts (verified by independent recount); leader's
  mean outcome rank = bottom 29% (sub-threshold anti-coupling, correctly not promoted); pooled power bounds any
  global coupling |rho|<~0.12.
- CEILING MATH (the 28%/yr answer): at the 5% cap, daily needs +2.0%/ticket net; monthly needs +42%/trade;
  measured per-ticket edge ~0/negative; best post-hoc slice +2.7% net (optimistic: the 1.5% floor is too
  GENEROUS for sub-$1 names -- skeptic finding). 28%/yr is NOT available from this universe with these
  instruments. Roads that could change it: different instrument class where volatility is tradable; rocket-gate
  n>=30 certification (collecting, n=3).
- Process: adversarial design review pre-interpretation (CRITICAL same-ticker-leakage concern -> sensitivity
  run -> empirically absent, <1% effect) + 3-skeptic verification fleet (1 claim killed, 2 confirmed, all HIGH).
  Erratum: prereg G1 parenthetical mis-stated its own arithmetic (only 0/200 passes); implementation was strict.
  scripts: _doc290_stage0_events.py, _doc290_stageA_hlocal.py, _doc290_sens_ticker_exclusion.py,
  _doc290_wsB_coupling.py, _doc290_PREREG.md.

## doc 288/289 -- 2026-07-11 -- edge-corpus RE-AUDIT (thesis survives) + THE COHORT FIELD (novel experiment) + 2 chips
- DOC 288 (audit, 15-agent fleet wf_2250281a): the multi-month no-edge thesis survives intact. 1 confirmed
  magnitude defect, 8 checked-and-clean, 1 systemic trap. experiments.md: doc-284 short-door net was OVERSTATED
  ~2x -- `_doc284_borrow_pricer.py` charges the 0.90% stop-overshoot FLAT to every fillable ticket when it is
  only incurred on stopped tickets (stop-rate ~0.34); corrected net ~-0.62%/ticket CI~[-0.99,-0.24] vs reported
  -1.205% CI[-1.571,-0.822]. TOMBSTONE UNCHANGED (still <0, CI below zero). Disclosed via code comment (frozen
  prereg param NOT retro-tuned, per doc-275/277); MEMORY doc-284 annotated.
- DOC 288 systemic: architecture.md -- the `trades_v1.ts_et` UTC-mislabel trap (broke 242/244, fixed 245,
  RE-INTRODUCED in v6_microstructure_pack). NEW guard `scripts/_doc288_tset_guard.py` (50 hits -> 25 suspect)
  + `tests/unit/test_tset_guard_doc288.py` freezes the allowlist so a NEW ts_et wall-clock site FAILS CI.
- DOC 289 (novel experiment, 6-lens design panel wf_f75c8cf3): THE COHORT FIELD. Reframe per-name -> cohort-
  relational. Pre-registered relational-existence test (53 cohorts, 936 names, grouped-CV, within-cohort
  permutation null, cross-regime). VERDICT: STAGE A FAILS -- relative structure adds nothing over absolute
  (lift -0.019, p_lift=0.77), winner-ID at the permutation floor (p_acc=0.34); the whisper (AUC 0.61) is
  absolute-not-relational and UNBANKABLE (Stage-B ceiling: fillable hold-to-close -0.89% net). Orthogonal
  condensation probe: attention is conserved (rho 0.87) + winner-take-all condenses (top1 43% of $vol) +
  forecastable (rho 0.66) BUT DECOUPLED from price (premarket $vol leader is price winner 1.9% vs 6% random).
  THE LAW: the cohort's attention field is ordered + forecastable but orthogonal to price returns -- structure
  lives in the wrong observable. experiments.md: doc-289 added (negative-with-structure; extends 250-284/273/283).
- CHIP (rocket-gate T+1 backfill): `_doc284_rocket_gate_ledger.py` new `--backfill-prior` recomputes prior
  FORWARD holes once T+1 bars land (compute ran same-day pre-warehouse -> 100% holes); wired into nightly
  post_close_scorecard. Repaired 7/6/7/7/7/9 (all no_bar_data=true -> MEASURED); acceptance now n=3/30
  PENDING (was n=0), recon sees it FRESH. 28 tests green, frozen math untouched.
- CHIP (bot relaunch): launcher exit-code propagation already correct (exit $exitCode); RestartOnFailure
  does not fire on a completed-nonzero-exit run (Windows), so `scripts/_doc288_apply_catchup_triggers.ps1`
  (operator, elevated) adds 05:15+06:00 ET catch-up triggers to the idempotent D90-lock launcher (2 free retries).

## doc 287 -- 2026-07-11 -- WEEK-ONE DEEP DIVE: reliability fixes (preflight retry, watchdog honesty, recon deep-freshness); week -1.16%
- Week-1 (07-06..07-10) account truth: $192,539.20 -> $190,309.44 = -1.16%. Two of four days FLAT (7/8+7/10 lost to a
  single 10s Alpaca preflight timeout). Mon +0.76% and Tue -1.75% are ONE overnight-carry cycle (RIVN/LUCY opened 7/6
  under the still-live t1_next_open, marked up at Mon close, reversed at Tue open; net pair -$1,938 ~= -1.0%/2d) --
  the doc-280 overnight-negative arm; already closed for 7/7+ (bar1_legacy). Clean corrected-posture day (7/9) = -0.15%
  (FBRX+RPGL, both correctly sized) -- on the doc-283 honest frontier, not the +0.5-1%/day target.
- architecture.md: `scripts/preflight_check.py` check_alpaca() now RETRIES transient httpx.TransportError (3x, 15s,
  2s/5s backoff) and DEGRADED-STARTS with a Discord alarm instead of FATAL-aborting the session; real problems still
  block (missing/bad keys, 401/403, equity<=0, non-ACTIVE, non-transport). Closes the SPOF that cost 7/8+7/10. Was
  stricter than the client it fronts (alpaca_client 30s/3-retry/D87). tests/unit/test_preflight_alpaca_retry_doc287.py
  (7 tests, 3 fail on pre-fix HEAD).
- architecture.md: `scripts/watchdog_monitor.ps1` split "no process exists" (nothing to kill -> alert once/hour deduped,
  NO phantom restart, never trip breaker) from "process hung" (designed kill path, honest relaunch messaging). Kills the
  ~200 false Discord posts/abort-day + the false "restarted 3 times / auto-restart" claims (it restarted zero; the
  watchdog cannot LAUNCH). ASCII-only (file is UTF-8 no-BOM; PS 5.1 -File reads as 1252).
- architecture.md: `scripts/config_truth_recon.py` check_instruments() rocket-gate freshness is now MEASUREMENT-aware:
  a present-but-dark row (n_filled=0 / no_bar_data) fails loud as DARK instead of passing as fresh. Every ran-day row
  (7/6,7/7,7/9) was dark and the doc-286 sentinel was blind to it. tests/unit/test_config_truth_recon.py +1 (28 green).
- backlog.md: OPEN operator items -- (1) launcher/Task-Scheduler relaunch never fires (swallowed exit code); (2)
  rocket-gate forward-bar pull returns no_bar_data every day (forward n>=30 unreachable); (3) posture_delta_trend
  --append writes no file (size-cut restore gate dark); (4) Doc286TagPhantomOnce task still has split-path bug
  (7/6 phantom rows untagged); (5) MOMENTUM_T2_ENABLED user-scope env unmirrored.
- Kalshi shadow scheduled task repaired this session (space-in-path -> Register-ScheduledTask + cmd /c); verified
  collecting (2/200 resolved). No hot-path trading logic changed.
- (2nd commit) architecture.md: `scripts/post_close_scorecard.py` forces PYTHONIOENCODING=utf-8 on the 3 EOD
  subprocesses + `scripts/_doc280_posture_scoreboard.py` hardens stdout -> UN-DARKS the doc-282 posture restore
  gate (it never wrote a row: a Delta/star glyph print died with UnicodeEncodeError under the EOD's cp1252 stdout
  BEFORE the file append). Verified: reproduced the crash, then a real 7/9 row now writes. 4-hunter fleet
  (wf_a5be034a, 4x Opus-4.8) independently reproduced all 3 first-commit fixes; the realized book is measured
  NEGATIVE-EV (last-10 ran-days CI[-1.13%,-0.26%], 0/10 win) so +0.5%/day needs a NEW edge, not sizing -- fleet's
  decisive rec is CANARY the live book to min-fill size while the shadow gates collect (operator call).
- backlog.md (deferred, recon now fails-loud in interim): rocket-gate T+1 --force backfill (compute runs same-day
  pre-warehouse); broker_truth_recon carry-blind (same-day pairs only -> missed 7/6's -$3,380 carry); EOD
  force-close false-success on 7/6; trade_results.jsonl 7/6 rows are D98 phantom (use broker equity bridge).

## doc 284 (validation fix) -- 2026-07-05 -- coverage-integrity clause hardened pre-window (adversarial validation)
- `scripts/_doc284_rocket_gate_ledger.py` acceptance(): GATE-CORRUPTING fix -- the unmeasured-ticket fraction was
  computed EXCLUDING no_bar_data sessions, so whole-session warehouse holes (the only observed hole mode:
  2026-05-29 = 17/17, 2026-06-30 = 65/65 unmeasured) contributed ZERO to coverage and a 63%-unmeasured synthetic
  forward window PASSED. Now counts every gated forward ticket incl. hole sessions, per the prereg #4 text
  (which was already correct -- code brought into conformity with the frozen doc BEFORE the window opens 07-06).
- Freeze-integrity: submit offset (45s) + fill window (2 min) promoted from inline literals to frozen module
  constants (SUBMIT_OFF_MIN / FILL_WINDOW_MIN, value-identical; retro 2026-07-02 force-recompute reproduces
  -$5,964.06 exactly) and added to the tripwire test. Loud WARNING added when --force replaces a FORWARD row.
- tests/unit/test_rocket_gate_ledger.py: +9 acceptance() tests (retro exclusion, halves rule, DEAD horizon,
  hole-coverage regression, strict 20% boundary) -> 28 green. Prereg #5 stale median corrected (+$10,839
  pre-hardening -> +$11,807; #2/#4 frozen parameters untouched).

## doc 286 -- 2026-07-06 -- GAP FIXES LANDED: phantom-close/book-integrity/stale-anchor fixed + config-truth recon (fires 9 on its own birthday)
- All doc-285 gaps fixed for the 7/7 4:30 launch (4 builders + 3 adversarial validators, ~1.0M tok; every hot-path fix
  strictly conservative, every fix's tests VERIFIED failing on pre-fix HEAD, validators mutated fixes to prove tests bite).
- A) D98 ABSENCE-CONFIRMATION (main.py): failed get_positions = UNKNOWN -> skip cycle, never fabricate []; stop-outs
  book only on stop-order status=filled (at REAL filled_avg_price) or 2 consecutive successful absent snapshots
  (streaks reset on failure/reappearance/episode-boundary — validator catch). 19 tests, 19/19 fail on old HEAD.
- B) BOOK INTEGRITY (bridge/position_manager/hedge_watcher): ROOT CAUSE — nothing decremented ManagedPosition.qty on
  partial sells; the stale qty then made attach_external_stop REFUSE the already-existing D313 write-back. Fixed both:
  confirmed partials book qty once (double-fire-proof); emergency-stop oid written back -> one shared protection view.
- C) STALE-ANCHOR (alpaca_executor): qty capped at max(eval, marketable limit) = the worst legal fill (RIVN regression
  test: 492 not 1552); tranche targets re-anchor to ACTUAL fill (no more collapsed-geometry 4-min clips). Provably
  never increases size.
- D) CONFIG-TRUTH RECON (scripts/config_truth_recon.py + nightly wiring): per-fill notional-vs-cap, risk-vs-intent,
  T2 split, exit-policy applied-vs-intended, USER-SCOPE WINDOWS ENV AUDIT, instrument freshness. PROOF: run on 7/6 it
  fires 9 breaches — every gap the manual forensic found by hand. + LLM fail-loud (CRITICAL after 5 consecutive
  primary rejects even when fallbacks serve — src/agents/base.py) + dead Tier-1 model replaced
  (LLM_TIER1_MODEL=Qwen/Qwen3-235B-A22B-Instruct-2507-tput, verified serverless by live call).
- ENV (verified through Settings): corrected size package EXEC_TIER1/2/3_POSITION_PCT=0.05 + KELLY_TIER1..4_RISK_PCT
  =0.0067 (bak-doc285); MOMENTUM_EXIT_POLICY user-scope var REMOVED (operator-authorized; doc-280 verdict) -> default
  bar1_legacy. One-shot 16:45 task tags today's 2 phantom ledger rows infrastructure_contaminated (self-deleting).
- Combined new tests 108/108 green; full-suite delta running vs the known pre-existing baseline (chip task_b24afcae).

## doc 285 -- 2026-07-06 -- FIRST-SESSION GAP HUNT: "+0.686%" decomposed; sizing knobs were DEAD LETTERS; phantom-close recurrence
- LIVE mid-session forensic (4 hunters + synthesis). HONEST HEADLINE: +0.686% was never trading profit — realized
  -$940 (weekend-orphan gap-down -$1,007 + real RIVN T1 +$67) + one promo-pump unrealized mark (LUCY = 136% of the
  snapshot gain); decayed to +$174 by 11:52.
- PHANTOM-P&L RECURRED (inverted): 10:48 circuit-breaker-open get_positions() returned "using empty" -> D98 stop-out
  poller booked TWO PHANTOM STOP-OUTS on live positions -> bot believes it is FLAT (-$1,204 incl -$1,272 phantom);
  regime flipped cautious off a fake loss; $35.7K live stock unmanaged (GTC stops only; verified resting). OPERATOR
  DECISION BEFORE 16:00: re-adopt / flatten / ride stops+EOD failsafe.
- DOC-282 SIZING KNOBS PROVEN DEAD LETTERS: executor overwrites max_position_pct with D150 aggressive-mode tier pcts
  (50/30/15%); risk comes from D115 Kelly tier (2%, tiers 2-4 = 4/6/8%). RIVN reconstructed to the share: 2% risk,
  tier3 15% cap, filled +4.4% over the cap anchor = 15.65% of equity. **CORRECTED PACKAGE APPLIED for tomorrow
  (backup .bak-doc285): EXEC_TIER1/2/3_POSITION_PCT=0.05 + KELLY_TIER1..4_RISK_PCT=0.0067, Settings-verified.**
- MORE CRITICALS: MOMENTUM_EXIT_POLICY=t1_next_open live from a Windows USER-scope env var (invisible to file greps;
  contradicts doc-280's overnight-negative verdict; staged one-liner for Pierce; also explains the weekend orphans);
  Tier-1 LLM (Qwen non-serverless reject) DEAD all session w/ zero alarms; no config-truth recon exists (the meta-gap:
  offline env->Settings verification says nothing about Settings->hot-path behavior).
- MAJORS: ledger fiction both directions (phantom rows need infrastructure_contaminated tags); re-entry-ban/halt only
  cover the OTO chokepoint; stale-anchor class (caps/targets on frozen eval price vs chased fills — RIVN tranche
  geometry collapsed -> 4-min clip); position book lost its own positions in stages (partial-sell qty regression +
  unregistered stop-oids + phantom close); 3 subsystems hold 3 stop views; instrument dark-running unmonitored.
- GENUINELY GOOD: T2 75/25 live+correct (halved LUCY); zero stop>=fill all session; holiday-orphan fix worked; D313
  backstop placed a real emergency stop; shadow-mode discipline prevented a FALSE force-flatten of two winners TWICE;
  selection cadence normal (size was the anomaly).

## doc 284 -- 2026-07-05 -- ROCKET-GATE STAGE-2 PREREG frozen + forward ledger instrument (doc-283 Step 3b)
- experiments.md-class change: the rvol>100 x hour-9 hold-to-close cell (doc 264 unfalsified conditional; doc 281
  graded D+ threshold-mined/OOS-failed/book-negative) gets its FROZEN pre-registration: gate = entry-time rvol>100
  AND entry hour 9 ET on the bot's own BUY rows; posture = hold-to-close, 15% disaster stop, never overnight; unit =
  per-session dollar delta (B_hold_close - A_bar1, doc-280 arms, arena fill model + local minute bars, $8K/ticket,
  row-level). ACCEPTANCE (forward-only, sessions >= 2026-07-06): day-blocked bootstrap 95% CI lower bound > 0 AND
  both chronological halves positive at n>=30 gated sessions; KILL at n=60 if not passed; ANY re-tune = automatic
  kill (constants tripwired in tests/unit/test_rocket_gate_ledger.py).
- NEW instrument `scripts/_doc284_rocket_gate_ledger.py` -> data/reports/rocket_gate_ledger.jsonl (append-only,
  per-session deterministic); nightly --append-today wired into scripts/post_close_scorecard.py as a best-effort
  never-fatal block (doc-282 pattern). Retro seeding 2026-04-14..07-05 (56 rows, retro=true, EXCLUDED from
  acceptance): 39 gated sessions, retro delta +$854,578 = the row-level MINED CEILING, loudly not money (doc 281's
  deployable estimand of the same cell was book-negative).
- DATA-QUALITY find during seeding: minute_aggs warehouse HOLES at 2026-05-29 (0 rows) and 2026-06-30 (1 row) --
  the doc-280 bar loader silently returns NEXT-session bars there; ledger adds a same-session guard (first bar
  <= 390 min) + no_bar_data flag + a coverage-integrity clause (unmeasured > 20% of forward gated tickets ->
  acceptance BLOCKED-COVERAGE, cannot PASS until repaired).

## doc 284 -- 2026-07-05 -- FRONTIER EXECUTION: the doc-283 path built+validated; SHORT DOOR TOMBSTONED by measurement
- Pierce green-lit the doc-283 path ("full send"). Fleet wf_f9b12f22: 3 builders + 3 adversarial validators (~2.5M tok).
  Validators caught + FIXED two GATE-CORRUPTING bugs before any verdict could be poisoned (rocket acceptance() coverage
  bypass; Kalshi fee must CEIL to next cent per the published schedule).
- STEP 1 RAN -- **GATE FAILED -> TOMBSTONE** (the decider, decided): scripts/_doc284_borrow_pricer.py on the actual
  2,167-ticket shadow ledger (275 ticker-days/186 tickers/20 dates) with REAL IBKR borrow data at FULL CENSUS (196/196
  symbols, 0 fetch failures, cached). Fillability 68.9% (clears 30%); fees on fillable: median 103% APR, p95 812%
  (GLXG 35%->776% APR exactly as it entered the BUY ledger = "the fee IS the fade", measured). NET/ticket = -1.205%,
  day-blocked CI95 [-1.571,-0.822] excludes 0 NEGATIVE -- inside doc-283's predicted band at the OPTIMISTIC bound
  (specialist locates cost more; lenient +/-3d arm = MORE negative). Only the freak +2.48%-gross week netted +0.21%.
  CONSEQUENCES: short door closed PERMANENTLY; Step 2 (TradeZero $2.5K) CANCELLED; Step 3a dead; frontier center
  ~0.07%/day pending the two live collections. Validator independently reproduced net+CI from raw rows.
- STEP 3b LIVE: rocket-gate Stage-2 forward ledger. Prereg FROZEN in its own pre-window commit 7f003f6
  (docs/research-log/284_rocket_gate_prereg.md): rvol>100 AND hour-9-ET, hold-to-close+15% stop, never overnight,
  per-session $ delta, acceptance = forward-only >=2026-07-06, CI-lo>0 + both halves positive at n>=30, DEAD at n=60,
  re-tune = kill, >20% unmeasured -> BLOCKED-COVERAGE. Instrument scripts/_doc284_rocket_gate_ledger.py (doc-280 arena
  machinery, deterministic seeds) + nightly best-effort block in post_close_scorecard.py; 56 retro sessions seeded
  (39 measurable, median +$11,807 -- retro EXCLUDED from acceptance); 28 tests green. Validator fixed the coverage
  bypass + froze SUBMIT_OFF/FILL_WINDOW as tripwired constants.
- STEP 4 LIVE: Kalshi zero-capital shadow (scripts/kalshi_shadow_doc284.py + kalshi_shadow_daily.cmd + scheduled task
  MomentumX\KalshiShadowDoc284 daily 18:00, first run Mon 7/6). Tonight real: 1,206 text-rich markets snapshotted
  (34 pages/6,650 events; Economics 572/Climate 256/Politics 216), 12/12 LLM forecasts parsed at $0.0026 total
  (60-day collection ~= $0.34). FROZEN GATE: >=200 resolved, fee-adj divergence P&L CI>0 AND Brier(LLM)<Brier(market)
  -> $5-20K side-pocket proposal; else CLOSED. NO positions ever. Validator fixed fee-ceil + 12h staleness lookahead
  guard; 13 in-repo tests green (3 stale fee expectations synced to ceil).
- Scoreboard: Step 0 done (282) / 1 TOMBSTONE / 2 cancelled / 3a dead / 3b collecting / 4 collecting / bleed-cut live.
  One door died cheaply, two doors are being measured properly, nothing bent.

## doc 283 -- 2026-07-05 -- THE 2%/DAY QUESTION: the transformation frontier, priced (negative existence proof + the path)
- Pierce asked what gets the account to 2%/day consistently. Priced adversarially (5 door-pricers + synthesis, web+repo):
  **2%/day consistently DOES NOT EXIST** -- Medallion (best sustained record ever) = 0.20%/day gross; best 0.1% of 450K
  measured day traders = 0.38%/day net; every documented 1-2%/day run = ex-post lottery survivor from 64-97%-loss
  cohorts. 2%/day = 147x/yr = $28M yr1 -- absent from the record, not gated.
- THREE STRUCTURAL FINDINGS: (1) THE FEE IS THE FADE -- day-of locates on low-float gappers (1-3% of notional) are the
  market-clearing price of the +1.33%/ticket shadow edge (net central -0.7 to -3.0%/ticket; even zero-cost fantasy
  +0.66%/day = 3x short); (2) NO-ARBITRAGE PIPES THE BORROW INTO OPTIONS via put-call parity (a CORRECT -5% fade call
  loses 32-62% of premium to theta+IV-crush+spread) -- instrument-hopping changes who invoices, not whether you pay;
  (3) LLMs BEAT THE CROWD NOT THE MARKET (o3 Brier 0.135 vs crowd 0.149 vs superforecasters 0.121; deep prediction
  markets = bot fleets; Kalshi taker ~3.5%).
- DOORS: Kelly-frontier MARGINAL (+0.13%/day central stack, quarter-Kelly 2-3%/ticket -- worst_mfe +264% makes full
  Kelly ruin); Kalshi shadow MARGINAL (+0.05-0.2%/day, >50% odds ~0, the one executable positive, capacity $0.5-2M);
  locate-broker CLOSED; reference-class CLOSED (frontier 0.2-0.45%/day sustained); puts-as-short CLOSED hardest.
- THE HONEST FRONTIER: +0.13%/day central (+39%/yr, $192K->$266K yr1, ~$870K yr3); ceiling +0.5-0.65%/day only if
  EVERYTHING validates (Medallion-order = the tell). THE PATH (12 wks, <$5K): Step 0 collector fix (DONE, doc 282);
  Step 1 price-the-borrow on the 2,500 shadow ticker-days via IBKR data (GATE: net<=0 -> tombstone the short door);
  Step 2 TradeZero locate-quote shadow ($2.5K, decline-all); Step 3a live short pilot only on gates; Step 3b
  rocket-gate Stage-2 (chipped); Step 4 Kalshi zero-capital 60-day shadow. Compound what validates; re-price quarterly.
- Doc: docs/research-log/283_the_two_percent_question.md. No live change.

## doc 282 -- 2026-07-04 -- FULL SEND: doc-281 play EXECUTED (bleed-cut live, stop-gate FAILED honestly, instruments relit)
- OPERATOR AUTHORIZED ("full send") -> executed doc 281's Stages 1-3. LIVE FOR MONDAY 7/6 (env, backup
  momentum-x-secrets.env.bak-doc281): EXEC_RISK_PER_TRADE_PCT=0.0067 (1%->0.67%), EXEC_MAX_POSITION_PCT=0.05
  (0.15->0.05; bounds the PLSM 14.8%-of-equity class 3x), MOMENTUM_T2_WIDE_PCT=0.75 (doc-266 fired prereg rule;
  demotion re-registered n>=60/arm), MOMENTUM_REENTRY_BAN_DAYS=10, FADER_SHORT_DRY_RUN=1 (doc-262 rec #2 executed).
  All verified through the real Settings machinery + T2 module. Restore rule PRE-REGISTERED: posture scoreboard
  durably positive n>=30 (NOT vibes).
- VANISHED-STOP FORENSIC (scripts/_doc282_stop_forensic.py, 56 OTO entries/28 sessions): the class is REAL — 35
  affected (13 submitted-stop>=fill = the mechanical bug: %-stop priced off INTENDED entry + favorable fill slippage
  -> stop above fill -> broker kills the stop leg -> naked position; PLSM anatomy confirmed). BUT class-wide excess
  = $5,990 < the pre-registered $8K GATE (92% = PLSM+RTB alone) -> **NO close-path code ships** — the gate held
  under full send. Mitigations standing: the 5% cap + the chipped hedge-underwater fix (task_76fc858a).
- LOSER-RE-ENTRY BAN built+tested+LIVE: _check_reentry_ban() in src/data/alpaca_client.py at the single entry
  chokepoint (after the D277 halt check; LONG entries only; fail-open). Counterfactual re-verified independently
  (strict rule: 5 trades -$6,027, zero forgone winners; investigator rule: 8/-$9,948/zero). 8/8 new tests
  (tests/unit/test_reentry_ban.py) + 255 related green + live check (PPCB banned, fresh ticker passes).
- INSTRUMENTS RELIT: red-flag collector NA-crash FIXED (rocket_watchlist_engine_doc255.py — pd.NA leaked through
  candidacy flags -> int(NAType) TypeError, dark since 6/8; NA-hardening + loud drops; verified on 6/09, dark-month
  backfill running); posture-delta scoreboard WIRED into post_close_scorecard.py (best-effort; the size-restore gate
  now accumulates nightly); FaderShort submissions retired to pure avoidance logging.
- HONEST MONDAY EXPECTATION: materially smaller red, not green — 40-70% bleed cut (doc-281 range). Green requires an
  answer-changer: regime turn (scoreboard), a broker with locates, or a new edge surviving the gauntlet.

## doc 281 -- 2026-07-04 -- WHERE PROFIT LIVES: the full-system profit deep-dive -- no offense to ship; lose slower + forensic
- THE QUESTION ("how do we make this thing profitable"), answered by 6 grounded investigations x 3 adversarial judges x
  synthesis (ultracode, ~816K subagent tokens, claims repo-verified). HEADLINE: no offense exists to ship — every alpha
  door is closed (selection 245-260), retracted, OOS-failed, or physically unfillable. The only gauntlet-grade dollars
  found are NEGATIVE facts. "Trade nothing" beats every package (+$43-71K/60 sessions); reduced-size trading is a paid
  subscription to the forward instruments, stated honestly.
- GROUND TRUTH: bleed is BROAD not tail — median session -$358, 7/32 positive, clean -$718/session; BROKER-EQUITY truth
  -$1,185/session (book under-attributes; recon sign-flips + 4 book-absent losing sessions).
- SLATE: #1 B+ STOP-THE-BLEED (tape-verified facts, risk-reducing, shippable) / #2 B- FLAG-OFF (T2 wide prereg rule
  FIRED) / #3 C- costs (friction $2-4K/100 trades, immaterial; entry chase nets FAVORABLE) / #4 D+ rvol-gated hold
  (mined thresholds, OOS-fails, book-negative at every threshold) / #5 D different-game / #6 F SHORT-FADE.
- DECISIVE FINDINGS (verified): (1) BORROW WALL ABSOLUTE — 0-for-94 live short attempts since 5/02 (71 NOT-SHORTABLE +
  20 422s, logs/fader_short_*.log); the 5/5-week-positive shadow short signal is real-as-signal, unmonetizable-as-trade;
  d215 live shorts PF 0.11 = shorting closed PERMANENTLY on this broker. (2) VANISHED-STOP BUG CLASS: PLSM 6/24 filled
  $29.8K=14.8% equity w/ OTO stop ABOVE its own fill, RECON_HARD_BLOCK 8s later, -$3,876 = worst trade; class in 26
  session files, ~$5.4K log-verified on 2 trades, class-wide = the most valuable unmeasured number. (3) doc-266 T2
  wide-stop PRE-REGISTERED PROMOTION RULE FIRED OOS (wide -1.10%/n=36 vs tight -2.75%/n=42; CI spans 0 -> $0 credit
  until n>=60). (4) re-entry-ban counterfactual +$9,948 on the book, zero forgone winners. (5) MEASUREMENT LAYER DARK:
  red-flag collector frozen since 6/8 (NAType crash line 178, silent ps1); fade_short_realistic never computed.
- RETRACTED/DEAD: doc-201 "+9.47% n=11" pilot (doc-213 corrected n=66 = -1.65%; was cited live in config for months);
  doc-264 overnight gate (refuted, -20.9%/ticket); red-flag hard entry-block (would delete LASE +$73,825); EMPTY!=BEAR
  "+$7.2K" (doc-233 from-open mirage class); dip-flatten "+$30,274" justification (tainted); rvol>100 "discovery"
  (demoted to prereg-frozen candidate); prior-rocket/SPY/IWM regime gates; all ceiling-arithmetic dollar claims.
- THE PLAY (staged, Pierce flips): S1 pre-Monday env lines — size package EXEC_RISK_PER_TRADE_PCT=0.0067 +
  EXEC_MAX_POSITION_PCT=0.05 (restore ONLY on posture-scoreboard-positive n>=30, pre-registered) + MOMENTUM_T2_WIDE_PCT
  =0.75 (fired rule; demotion re-registered at n>=60/arm). S2 wk1 — vanished-stop forensic (GATE A: >=$8K class loss ->
  the ONE close-path code fix, full gauntlet) + re-entry-ban flag (flip after recon confirms). S3 wks1-2 — $0-credited
  measurement layer (fix collector+fail-loud, exit-slippage instrumentation, frozen gated-hold forward ledger,
  full-denominator unfilled-cohort replay, RETIRE FaderShort submission path per doc-262 rec #2).
- 60-SESSION EV: baseline -$43K..-$71K; play = 40-70% bleed cut => -$13K..-$26K (+$17K..+$45K vs status quo), ZERO
  alpha credited. Answer-changers: regime turn (scoreboard), a broker with locates, forensic >=$8K, collector separating
  at n>=300. The infra is the asset; the strategy is the liability.

## doc 280 -- 2026-07-03 -- FLIP THE SCORE (PRE-REGISTRATION) -- exit-posture profit lever, measurement frozen before results
- PROFIT mandate. The -$20,131 paper book (clean -$22,961 after excluding 2 phantom-P&L trades LFS/APPS) is a
  WINNER-CLIPPING posture failure: biggest realized win +$1,810, worst loss -$3,876 (small capped wins, large losses).
  Confirmed in data/trade_results.jsonl (84 trades, 32 sessions). Selection alpha is CLOSED (245-260) -> the lever is
  POSTURE, not picking.
- LEVER CHOSEN (survey over the ruled-out map): the EXIT POSTURE. The winner-clippers are in config with self-
  incriminating comments: exit_policy=bar1_legacy (prod default) "LOSES money on the actual trade pool -$5K/117 trades
  even though entries identify winners median MFE +2.61%"; exit_policy=t1_next_open documented "+$23K incremental EV/
  117-trade pool" (doc 69) = a SUSPECT counterfactual that must run the gauntlet. Plus D164 (sell 50% @T+2min), 5.5%
  tight stop, D163 trail. All flag-gated, the hold flag OFF.
- BOTH adversarial pre-freeze stages landed hard: NOVELTY sweep (deep-research) RAISED THE PRIOR AGAINST the thesis
  (overnight gaps REVERT not continue -> holding a winning gapper may give gains back to mean reversion; agrees w/
  doc-251 fade). DESIGN PANEL = 4/4 DO-NOT-FREEZE -> full REDESIGN: measure the ACTUAL 82-trade book per-session-dollar
  (not 5,140 candidates = 62x inflation); price EVERY exit through the validated SpreadModel bid (not clean closes);
  survivorship-gate next-open; hard one-sided +20% cap (winsor@5% is theater); kill the variance-harvest laundering
  branch (require out-of-sample). Outcome branches frozen (MOVES-THE-BOOK / TAIL-HARVEST / NO-EFFECT / NEGATIVE); the
  NEGATIVE branch is a live, EXPECTED outcome given the gap-reversion prior.
- Harnesses (procedure frozen): scripts/_doc280_counterfactual.py (PRIMARY: actual book, per-session-dollar, trust-
  tiered CONFIRMED vs SIZE_BASED, through SpreadModel) + scripts/_doc280_posture_backtest.py (CEILING: all candidates,
  4 arms, arena AlpacaFillModel). Read-only vs production; mechanism ships flag-gated, Pierce flips it.

## doc 280 -- 2026-07-03 -- FLIP THE SCORE (RESULT) -- posture flip is NO-SHIP: it deepens the loss (the +$23K evaporated)
- RESULT = the NEGATIVE branch. Flipping the exit posture to HOLD does NOT flip the score, it DEEPENS the loss.
  Per-session-dollar Δ (hold minus actual) on the -$22,961 clean book: HOLD-TO-CLOSE confirmed -$1,363 / full -$8,739;
  HOLD-TO-NEXT-OPEN (the doc-69 flag) confirmed -$1,582 / full -$14,720 -> new book -$24,169 (worst arm); HOLD+15%stop
  confirmed -$1,690 / full -$3,441. Holding helped 14/41 trades. NO arm's CI excludes 0. The doc-69 "+$23K/117" EVAPORATED
  /INVERTED under the realism gauntlet (the 233 cautionary tale). The overnight arm being worst confirms the novelty
  sweep's gap-reversion prior + doc-251 (gap-up long = fade).
- ROBUSTNESS (hostile retest independently re-priced the probes): uncap does NOT flip it (CONFIRMED subset is cap-
  insensitive at -$1,363/-$1,582); MISMATCH-inclusion makes it worse; AZI -$8,245 is a VERIFIED-REAL gap-reversion
  hold-loss (entered $4.06 -> ran $7.00 -> closed $1.71), not a qty artifact. Retest verdict NEGATIVE-NEEDS-QUALIFICATION.
- NUANCE (ceiling, all-candidate avg): hold-to-close beats bar1 by +1.58%/ticket (CI excl 0, survives winsor@5%) but is
  75% TAIL-DRIVEN and REGIME-FLIPPING -- positive in rocket-rich 2026-04/05, NEGATIVE in 2026-06/07 (the actual book's
  window). So holding-to-close is a REGIME-CONDITIONAL rocket harvest that is currently OFF; the blanket flip is dead,
  a regime-GATED hold is the one open (untested) door. Reconciles docs 185/264 (holding won -- in the rocket regime).
- MECHANISM: on this gapper book, holding gives back more than it captures; the winner-clipping exits were ~PROTECTIVE.
  The -$22,961 is (this regime) an entry/selection problem the exits were LIMITING, not a posture problem. Both
  adversarial pre-freeze stages earned their keep (novelty predicted the sign; panel's 4/4 DO-NOT-FREEZE forced the
  redesign without which the first design would have printed a fantasy positive).
- SHIP: NONE (the flag stays OFF; the measurement VALIDATES keeping bar1_legacy/the exits ON). Gamification deliverable
  BUILT: scripts/_doc280_posture_scoreboard.py = the posture-delta instrument tracking "would holding have beaten the
  exits?" session-over-session (cum Δ +$4,702 through 06-08 then one AZI session sank it to -$8,739) -> lights up only
  when the regime turns rocket-rich; staged for Pierce to wire into post_close_scorecard.py. Prereg commit a104a0c.
- RETEST AMENDMENT (the retest corrected MY OWN overstatement — no number bent in EITHER direction): the intraday arms
  are a NULL leaning negative, NOT a demonstrated negative (all CIs include 0; CONFIRMED n=22/14 sessions, within one
  AZI-trade of noise). DROP-WORST: hold-close FULL -$8,739->-$494, hold+stop -$3,441->-$187 (one-trade-fragile); ONLY
  hold-to-next-open survives drop-worst (-$8,184) = the one genuinely-negative arm (maps to the doc-69 flag). And doc 280
  measured a BLANKET flip = a STRAWMAN: docs 258/264 already conceded blanket holding is -EV; 264's real proposal was a
  CONDITIONAL ex-ante rocket-gate (close>+30% on RVOL>10 -> hold), which was NOT tested and remains OPEN (Stage-2 chip).
  Honest verdict: blanket flip null-to-negative on this rocket-free book; conditional gate unfalsified.

## doc 279 -- 2026-07-03 -- BREAK THE CEILING: the full contamination-fragility sweep -- FLIP-free, 17 load-bearing staged
- THE SWEEP RAN on 278's tape-validated rebuilt corpus. Census: 135 falsifiable conclusions from docs 235-273
  (63 not-corpus-gated, 72 corpus-dependent, 69 load-bearing). Read-only vs production; rebuilt corpus is a PARALLEL
  artifact; the live warehouse/read path are UNTOUCHED (promotion staged for Pierce, not executed).
- TWO adversarial passes were needed to make the ledger honest (lineage "no number bent, incl. your own", twice):
  (1) DESIGN PANEL killed my first-pass "77.9% robust / 0 flips" headline on 5 verified counts (gate-mismatch: flip
  stat hard-coded to $1M but load-bearing docs gate at $5M/$10M; min_periods window-policy toggle CONFLATED with the
  data-hole toggle; cross-regime verdicts DECIDED by the 2026 cell so byte-identical pre-hole doesn't certify them;
  no reproduce-first; FLIPPED/INDETERMINATE branches were unreachable dead code). (2) HOSTILE RETEST then wounded the
  CORRECTED ledger too (denominator 69 vs true 72 via a census self-report override; "INDETERMINATE-UNPOWERED" was a
  laundered not-measured for 29/33 cells; ROBUST over-counted via keyword-regex; the recompute ran at ONLY $1M -- the
  smallest, wrong-signed gate).
- CORRECTED FOUNDATION (factorial toggle split, _doc279_factorial.py): pre-hole years EXACTLY decontamination-invariant
  (0 gate-flips in 2024 AND 2025 at $1M/$5M/$10M); 2026 churn is GATE-DEPENDENT + BIDIRECTIONAL (2.55%@1M all
  pass->fail; 7.04%@5M; 7.79%@10M mostly FAIL->PASS = decontam ADMITS real names at the higher gates the live selector
  uses). My first-pass "2.95% direction-preserving" was an artifact of the min_periods conflation + $1M-only screen.
- THE RETEST FORCED THE MULTI-GATE RECOMPUTE (_doc279_gate_recompute.py, paired day-block bootstrap on the CHANGE):
  the 2026 EOD mean ret_session cell = +0.485%->+0.470% @$1M / +0.461%->+0.527% @$5M / +0.340%->+0.514% @$10M
  (+51% rel, admits names avg +2.22%). BUT paired dCI includes 0 at every gate AND neither cell clears the ~1% cost
  bar -> the expectancy/no-long-edge null verdict is CERTIFIED ROBUST across all three load-bearing gates. The wound
  made the result more honest AND stronger where actually measured.
- FINAL LEDGER (ledger3.json, 135): 63 N/A-INVARIANT, 37 ROBUST-BY-RECOMPUTE (gated-universe-return channel, all gates),
  5 ROBUST-BY-CONSTRUCTION (dv/adv-only channel theorem), 23 STAGE-2-REQUIRED (own pipeline, cell UNMEASURED = honestly
  not-tested, NOT a power claim), 4 INDETERMINATE-UNDERPOWERED (doc 245's ~30-rocket LORO cell measured thin),
  3 KNOWN-ARTIFACT (273 V-RACE confirmed-by-fill), 0 FLIPPED.
- TWO HONEST HEADLINES: (1) 0 of 72 corpus-dependent conclusions shown to FLIP; 42 certified robust; 27 deferred to
  Stage-2 (unscored, NOT certified 0). (2) NO load-bearing null FLIPS; 29/69 load-bearing certified robust; 17 deferred
  to a pre-registered STAGE-2 (245/251-short/254/256/257/258-multiday/259/260 -- bespoke 2026-cell pipelines: rocket
  classifiers, RL policies, short-edge cells).
- PROMOTION REC (staged, operator-owned; split per the retest): PROMOTE day_aggs_q1_rebuild.parquet as a DATA-QUALITY
  upgrade (strictly more complete, tape-validated, flips 0 conclusions, corrects the 2026 universe in the right
  direction) via parallel-column -> shadow N sessions -> cutover; do NOT read promotion as a robustness certification
  of downstream conclusions (that is gated on Stage-2). Live warehouse untouched until Pierce executes.
- Commits: prereg/panel-fixes frozen into the classification rules BEFORE ledger3 ran; results (this).

## doc 278 -- 2026-07-03 -- THE OPEN FRONTIER: RESULTS -- the lock is open; fragility is ~8%, multi-channel
- ENABLER BUILT + TAPE-VALIDATED: day_aggs_q1_rebuild.parquet (621,164 ticker-days, 54 Q1 sessions, all pre-hole,
  0 dups -- verified exactly by cross-tier peer). task_4994579a is buildable READ-ONLY from local minute bars.
  At scale day_aggs' volume*close deviates >25% from the raw tape on 9.5% of days -> day_aggs is the erratic proxy,
  unfit as reconciliation ground truth (the panel's demanded target was itself unreliable; the tape settled it).
- FRAGILITY (R-TAIL, corrected UP by the hostile retest): 996/1,636 corpus days hole-affected. My first headline
  (0 value-flips, 1.57% ejection) was WOUNDED by the retest at full strength: (a) "0 value-flips" was a TAUTOLOGY
  (affected set = exactly the uncomputable-ADV rows), the proper full-20 test finds 7 real gate flips (DERM/PRTH/
  MPL/RMNI/LFVN/SIF/NAMM); (b) the 1.57% rested on a self-selected 511 denominator hiding 444 affected days -> the
  denominator-honest illiquid-contamination rate is 78/954 = 8.18% (~5x); (c) "GLXG-class tail" is a FALSE label --
  the k>5 names are UGRO/FEED/VSA hole-edge partial-window artifacts (max k=25.6 = UGRO n_win=1), GLXG absent.
  VERDICT: universe ROBUST-in-the-median (k median 1.04) but ~8% value-channel fragility across TWO channels
  (partial-window inflation + full-20 proxy error = 15 gate errors). Validates 273's direction-preserving zombies
  in aggregate while sharpening the minority 1.6% -> ~8%.
- Cross-tier Sonnet peer: every number reproduces EXACTLY (arithmetic CLEAN); the corrections are about MEANING.
  4 standing rules: your ground truth can be the contaminated thing (use a 3rd source); define "affected"
  orthogonal to the statistic (else the null is manufactured); report the denominator-honest rate; robust-in-median
  != robust. NEXT: the mechanical fragility sweep of the 235-273 catalog on the hole-filled corpus. Commits: prereg
  c72e2e9 -> results (this).

## doc 278 -- 2026-07-03 -- THE OPEN FRONTIER: build the lock open (pre-registration)
- Round-five charter, NEW freedom to BUILD an unlock vs chase a lock-free question. Survey (ground truth re-checked):
  cascade 12/40 locked-on-TIME, causal no-arm locked-on-INFRA, contamination-fragility locked on the unbuilt
  day_aggs Q1 rebuild (task_4994579a). DECIDER: the rebuild was assumed to need a Polygon fetch -- it does NOT;
  minute_aggs has full local Q1 coverage, schema-identical. Likely why it sat unbuilt: assumed remote, was local.
- DECISION = FUSE (build the enabler + measure the fragility it unlocks). Built day_aggs_q1_rebuild.parquet
  (621,164 ticker-days, 54 Q1 sessions; PARALLEL artifact, live warehouse untouched).
- NOVELTY SWEEP: NOVEL-WITH-CAVEATS (nearest Goes 2023 vintage-flip decomposition; imports Fragility Index,
  coverage-vs-value split, point-in-time discipline). DESIGN PANEL: 4 VERIFIED headline-invalidating flaws
  (minute vol 10-34% below day_aggs; extended-hours; NULL-vs-computed flip conflation; inert cell empty by
  construction). TAPE TIE-BREAKER resolved the panel's #1: consulting the raw consolidated tape (trades_v1) showed
  minute tracks the tape within 2-10% while day_aggs diverges 0.79-1.92x -- day_aggs is the erratic proxy, NOT
  ground truth. The panel's demanded verification target was itself unreliable (277 "verify the verification"
  recursing). Frozen estimand = coverage-flip (diagnostic) vs value-flip/retroactive-ejection (fragility headline)
  + Fragility Index + inflation-k; decision tree R-ROBUST-INERT / R-FRAGILE / R-TAIL / R-INSTRUMENT-UNFIT /
  R-COVERAGE-ONLY. Committed before the fragility result.

## doc 277 -- 2026-07-03 -- RESULTS: the audit instrument audited itself and found a blind spot
- STAGE-1 VERDICT: **R-INSTRUMENT-BOUND + R-UNDERPOWERED** (informative null on the headline statistic).
  The pre-registered class-geometry estimand (Delta = excess-co-miss(OMIT) - excess-co-miss(NUM)) is
  UNIDENTIFIABLE on the reused catchable-by-construction seed bank: marginals SATURATE (NUM all-caught,
  OMIT all-MISSED across all 3 tiers), so excess-over-independence is 0 by construction -- exactly the
  degeneracy the design panel PREDICTED ("undefined correlations at low catch rates").
- THE HEADLINE IS SELF-REFERENTIAL: my FIRST pass reported Delta=+0.375, p=.008 -- and the mandated
  HOSTILE RETEST REFUTED IT by finding a blind spot in MY OWN scoring instrument. The token-heuristic
  seed-matcher false-caught 2 of 3 OMIT seeds via leaky tokens (the bridge-OMIT's entire token set was
  {'2026'} pulled from a date; the collider "catches" fired on '100'/'liquidity'). Hand-reading every raw
  finding: 0/7 and 0/6 truly caught those omissions. Corrected: excess(OMIT) +0.375->0.0, Delta->0.0,
  honest item-permutation p 0.008->0.33 (the 0.008 was a cell-shuffle null that destroys the correlation
  it tests). A doc about audit-instrument blind spots found one in its own instrument -- the 276 "no clean
  floor" recursing onto THIS experiment's instrumentation.
- SURVIVES (verified 3x incl. independent cross-tier Sonnet): (1) a single-item EXISTENCE WITNESS -- the
  R274_F5 README template-omission (a deleted load-bearing check with the conclusion left standing) is
  jointly missed by ALL tiers x BOTH frames (7/7) while each found 3-5 OTHER real defects; blind-solvable
  (the tool was in-hand; they never formed the hypothesis). (2) UNIFORM cross-tier OMIT blindness: a
  second/third Anthropic tier did NOT cover the seeded omissions (opus/sonnet/haiku all 0 on the specific
  seeded omissions). (3) METHODOLOGICAL: automated audit-scoring by token heuristics is itself blind-spot-
  prone and manufactured a spurious cross-model correlation -- a warning for all seeded-defect studies.
- ADVERSARIAL TRAIL (the registered->final distance): novelty sweep killed the "never measured" framing
  (Kim/Kuai/Nine-Judges); design panel rewrote the analysis plan (killed AC1-veto, per-cell CMH binning,
  floating class); hostile retest refuted my own primary statistic; cross-tier Sonnet peer confirmed the
  witness + flagged the primary-estimand deviation (shipped a coarse proxy, not the frozen residualized
  primary -- disclosed). SCOPE: intra-vendor cross-tier (all Anthropic) = near-worst case, NOT cross-vendor.
- STAGE 2 (gated): mid-difficulty OMIT seeds (marginals ~0.5, where correlation is identifiable) + fresh
  fabrication + the affordance sighted-control arm (never run) + cross-VENDOR replication. Commits: prereg
  9d9d0c6 -> results (this).

## doc 277 -- 2026-07-03 -- WHEN IS A SECOND MODEL REAL DIVERSITY? (pre-registration commit)
- Round-four charter, problem CHOSEN BY SURVEY (outward tine — turn the gauntlet apparatus on a question it was
  never built for). Counts CHECKED not assumed: cascade anti-selection 12/40 LOCKED, causal arms no-valid-arm
  LOCKED, day_aggs rebuild NOT landed → every corpus-consuming/engineering-gated candidate off the table; the
  survivor consumes AUDIT ARTIFACTS not the market corpus (membership/window discharged by design, like 276).
- QUESTION: the DEFECT-CLASS GEOMETRY of cross-model blind spots. Not "are two models correlated" (known: yes,
  cross-family r≈0.53 — Kim/Kuai/Nine-Judges) but "on WHICH defect classes does a cheaper same-vendor tier
  (Haiku/Sonnet behind Opus) buy independent coverage vs FALSE assurance", against a potency-proven seeded oracle.
- NOVELTY SWEEP killed the "never measured / correlated-instrument-trap" framing (0/3, false); the defensible
  delta (3/3) = defect-class-conditional + certified-seed + research-audit setting. DESIGN PANEL killed the
  analysis plan: Gwet-AC1-veto (makes R-DIVERSITY-REAL unreachable), per-cell CMH binning (flips headline), flat
  power rule, floating "recompute-identity" class, single-pair wound, universal framing — all before freeze.
- FROZEN DESIGN: primary = the CLASS CONTRAST Δ = excess-co-miss(OMIT) − excess-co-miss(NUM) above a same-tier
  two-Opus-draws lineage floor, difficulty-residualized (sealed item-feature difficulty), item-level-permutation
  FWER; co-primary joint-miss P(all-miss|class) vs difficulty-matched independence; mixed-effects logistic;
  n_eff descriptive-only. Two deep control arms: PROMPT-DIVERSITY (absence-priming frame → R-PROMPT-BOUND) +
  AFFORDANCE (sighted recompute control → R-AFFORDANCE-BOUND). H1: Δ>0 (OMIT jointly-blinder). Panel = Opus 4.8 /
  Sonnet 5 / Haiku 4.5 + Opus-replica floor (intra-vendor cross-tier — scoped, NOT cross-vendor). Stage 1 tonight
  = the Δ headline; Stage 2+ gated = full per-class geometry + cross-vendor. Committed BEFORE any panel run.

## doc 276 -- 2026-07-02 -- THE REGRESS: RESULTS -- R-INSTRUMENT-BOUND, no clean fixed point
- VERDICT: **R-INSTRUMENT-BOUND** (Step 1 sensitivity 0.67<0.75 AND Step 1b adjudication 4/6<5/6). Scientific
  answer: recursive self-audit does NOT converge to a clean fixed point -- every level incl. double-verified
  CONTROLS yields fresh verified defects (R274_F1-matrix CLEAN_A/CLEAN_B byte-identical = double-count S2),
  and the auditing instrument has class-structured blind spots. Empirical Milawa: tower terminates at an
  imperfect human-auditable trusted base, not zero-trust self-cert. Standing verdicts 273/274/275 SURVIVE
  (not overturned; wound arm 2/2 shows a flipper WOULD be caught) but are NOT certified clean.
- INSTRUMENT: 36 L4 referees (Opus 4.8; Fable 5 hit usage limit mid-run -> re-run homogeneous on Opus, Fable
  16-unit partial kept as cross-model exhibit) x 2 lenses = 60 findings; 117-adjudicator dual re-implementation
  (50 verified / 10 refuted / 4 unresolved).
- INDEPENDENT CROSS-MODEL PEER REVIEW (5 arms, the user-requested gold standard): POTENCY CLEAN (15/15 seeds
  re-derived potent from raw artifacts; zero blanks -- the lineage's recurring failure mode confirmed absent by
  a DIFFERENT model), FAITHFULNESS CLEAN (0 undeclared edits), GOLDEN key 6/6, SEAL MINOR (found a cross-brief
  TRUE-value tell F5/F2 15385 my mutated-token-only echo-scan missed -> fixed via collision_scan.py; +gitignore
  note), DESIGN-ADVERSARY MINOR->major (plants disjoint from pressure set = P4 biased by construction).
- ACTED on the peer critique (not just disclosed): WOUND ARM -- 2 proven-potent wound-class plants on 2 pressure
  legs (T=40; ladder power), blind sub-fleet caught BOTH on both lenses (2/2) -> pressure-leg detection power
  calibrated at 100%, refuting the P4-bias worry empirically.
- ERROR BAR (primary deliverable): sensitivity ~0.67 class-structured (OMIT 1/3, S1-recompute 1/3 = the blind
  spot; NUM/CODE ~caught); adjudicator directional 6/6 but resolution 4/6 (G2 framing, G6 recipe-ambiguity
  PREDICTED by the peer GOLDEN arm; +a proven-potent seed split); clean floor FALSIFIED. P1/P3/golden FALSIFIED;
  P4 SPLIT (RC11: wound YES on F1-calibration T=40 leg, flip NO). 6 standing rules (no clean floor; class-power
  the audit gate; pin adjudicator recipes; plant on count<=1 legs; echo-scan TRUE values; different-model peer
  review). Commits: prereg 2050cca -> seal a059832 -> results (this).
- Recursive tower now: 273=inputs, 274=measurement, 275=verdict layer, **276=the verifier of the verdict layer
  (itself instrument-bound; the error bar is the output).**

## doc 276 -- 2026-07-01/02 -- THE REGRESS: does recursive self-audit converge? (pre-registration commit)
- DORMANT-C, /ultracode, round three w/ the problem CHOSEN BY SURVEY (7 candidates graded; contamination
  counterfactual = strongest rejected). 273=inputs, 274=measurement, 275=verdict layer; 276 makes the regress
  the measured object. PRIMARY DELIVERABLE = the VERDICT-LEVEL ERROR BAR: frozen leg map + single-point-of-
  failure census + a sealed L4 audit fleet concentrated on every count<=1 leg (274's no-G-FALSE-CERT, 275's
  T=40/0.399, 275's B25/subsample/money-only referee comparators — the lineage's trust has migrated UPWARD
  into never-audited referee computations). Confirmatory statistic x4 = verified S1-S2 defect phenomena in
  the 18 real rulings, clean-arm-corrected; decay curve DEMOTED to descriptive exhibit (both letters were
  derivable by stratum-weight choice — panel kill).
- Design-stress panel (wf_8d29fab2-d2c) found the version-pinning trap (all 3 ship docs POSTDATE their
  retests — as-audited git blobs frozen into the corpus), broke the draft blinding (state files print verdict
  rosters), convicted K~6 plants of rebuilding the T=40 disease -> ~9 surgical perturbations, 12-16 seeds,
  >=8 S1-S2 incl. omission+mechanism classes, potency PROVEN by executed recomputation, golden adjudication
  cases (the adjudicator is planted too), clean/specificity arm, exhaustive decision tree w/ R-INDETERMINATE
  modal + R-OTHER residual. Deep-research (wf_b9e8ae44-c75, 105 agents): composite NOVEL (nearest: statcheck
  = one layer not a recursion; Milawa = provable not measured); human planted-flaw baselines ~17-39% (Baxt,
  Schroter) adopted into predictions. Corpus frozen+hashed (20 artifacts incl. as-audited blobs + both design
  artifacts) in data/research/doc276_corpus/. Membership burden discharged BY DESIGN (no universe defined).
- New: docs/research-log/276_the_regress.md (SS1-3). Results + L5 retest in later commits.

## doc 275 -- 2026-07-01 -- THE GATE: certifying the experiment factory (pre-registration commit)
- DORMANT-C, /ultracode, round three: 273 measured the tape, 274 the engine, 275 the FACTORY — fleet-level
  inference under true cross-experiment dependence (shared days+universe) via ONE shared donor-bundle
  permutation set applied to every member (White's-RC architecture, permutation semantics, studentized maxT,
  Phipson-Smyth p). Certified the way genomics certifies FDR methods and nobody certifies backtest gates:
  day-rotation truth-null calibration (Wilson CI contains .05 AND excludes .15), residue-detection arm with
  frozen per-t predictions, TWO crossed dose-response ladders (273-plants for dll power, exit-mix leaks for
  money/false-cert, >=25 reps/rung, dual-mode thresholds), placebo-decomposed contamination-cost arms
  (A as-is/B clean/C immune-filtered/D placebo-truncation), D-REPRO graded on HELD-OUT mutant classes.
- ⚡ Pre-prereg pilots by the design-stress panel (wf_f1a9c2fd-6d9) BROKE the central premise and became the
  first finding: the "certified-null" substrate is NOT null at fleet level — 400 random-score members false-
  fire 15.8-20.2% naive and the family max FIRES @14:00 on dll AND money, loading the 273 rv_1m direction-
  free residue; payability-null != fleet-exchangeability-null. "Certified-null" struck everywhere (B5).
  Frozen-score approximation killed (refit measured 2ms; the expense premise was false). Deep-research
  wf_f6e6e650-d16 (106 agents): components precedented (White RC, Romano-Wolf, WY block-optimality), the
  empirical certification composite NOVEL (angles 3/4/5 returned zero surviving claims).
- Frozen: P1-P10 quantitative predictions + B1-B15 banned headlines + dedup convention + drop-order +
  G-GATE-SOUND/LEAKY/POWER-PRICE/UNINFORMATIVE verdicts (full text data/research/doc275_state.md).
  New: scripts/gate_doc275.py (shared-permutation core, Tier-1 vectorized nulls, D-REPRO organ — already
  validated on 274's artifacts: catches R4, passes CLEAN_A; smoke fleet M=50/B=20: naive 6 false
  discoveries, WY 0). Results + mandated hostile retest in later commits.

## doc 275 -- 2026-07-01/02 -- THE GATE RESULT: G-UNINFORMATIVE-ON-CALIBRATION (disclosed taxonomy extension); no certificate; the verdict layer joins the recursive tower
- Stage-1 ran all six arms. Raw numbers ALL bit-reproduced by the 5-referee hostile retest (wf_8559de52-3b2;
  completeness critic lost to a session limit, disclosed). Two rulings REFUTED the STORY: the q95 threshold
  ordering = sample-size + rank-10-of-200 MC noise (random subsamples bracket arm C exactly); the real-side
  firing = CALENDAR composition (referee comparator B-minus-2026 reproduces arm C with the same member IDs);
  the D placebo was MIS-BUILT vs its own spec (wrong regime, 42% unmatched) -> ban B15 formally met,
  substantively void; "membership price ~0" violated ban B10; P9's which-is-larger call REFUTED sign-reversed
  (multiplicity FD 11-15 >> contamination <=1 at N=100).
- SURVIVING MEASURED PARTIALS: naive per-member testing = 64-99 false dll discoveries per 500-member fleet on
  EVERY substrate, gate cuts to 2-17 (per-experiment p<.05 is dead as a promote-criterion, by measurement);
  fleet-selection finds the rv_1m residue on all substrates (payability-null != fleet-exchangeability-null,
  referee-proof); three instrument laws (nulls-only studentization anti-conservative O(1/B) — the truth arm's
  0.075 sits ON the predicted bias; B must scale with K; pooled cross-channel max lets dll t-tails blind the
  money channel — money-only family detects lambda=.3 at 0.64 vs pooled 0.00 -> channel-stratified families =
  Stage-2 design); D-REPRO = deterministic reproducibility checksum, ROW-ORDER-FRAGILE at the logit member
  (referee-run must-pass cell), tolerance deployed 1e-6 vs frozen 1e-9 (disclosed).
- VERDICT MACHINERY FINDING (the tower's third floor): the frozen G-* taxonomy could not name the outcome;
  the T=40 acceptance band passes a PERFECTLY CALIBRATED gate with p~=0.40 (structurally unable to accept);
  drop-order violated (T dropped before placebo-D); NEVER-DROP conjunction result not computed. NEW STANDING
  RULE: pre-register the acceptance test's own POWER + pre-verify placebo geometry. Stage-2 = the 10 referee
  gates, verbatim in doc SS6. 273=inputs, 274=measurement, 275=verdict layer.

## doc 274 -- 2026-06-11 -- THE MUTATION GAUNTLET: capability curve of the falsification engine (pre-registration commit)
- DORMANT-C, /ultracode throughout, read-only vs production. 273 measured what the tape can know; 274 measures
  what the ENGINE can certify: mutation testing transplanted to the quant falsification stack — mechanism-typed,
  graded-severity pathologies (membership holes via DUAL-MOUNT overlay, feature look-ahead, exit-mix leakage,
  survivorship, within-gate drift, null mis-spec) injected into a reduced surrogate of the doc-273 engine on the
  certified-null clean spine; 10 defense layers instrumented (incl NEW D10 drift monitor) + the LLM referee
  fleet itself under a sealed instance-blinded protocol (2 mandatory clean decoys + frozen mutant rule + the
  real 2026-03-23 specimen as positive control). Single confirmatory statistic = SURPRISE COUNT vs a frozen
  expected-outcome matrix; banned: any "X% caught" aggregate, per-cell probabilities (n=1), D9 rate language.
- Pre-prereg apparatus (both changed the design): deep-research wf_11184ab9-a78 (106 agents, 22 claims 3-0):
  composite NOVEL — DeepMutation/LIGO/ERCC/Kepler/CSCV-PBO cover ingredients, nothing covers the stack,
  LLM-referee-as-measured-detector has ZERO prior art; design-stress panel wf_8c00cf6d-1d6 (5 attackers) caught
  a structural tautology (spine-level injection vs warehouse audits) -> dual-mount restructure adopted wholesale;
  attacker measurements disclosed (tau=600 harm +0.016; Bernoulli survivorship cannot FP on a null spine).
- New: scripts/mutation_gauntlet_doc274.py (engine cells, defense battery, parameterized worker; zero-knob
  identity PASS 125 rows/0 mismatch), scripts/doc274_cells_registry.py (injectors + frozen 11-cell grid),
  docs/research-log/274_mutation_gauntlet.md (SS1-3 prereg). Results + mandated hostile retest in later commits.
- RESULT (SS4-6): 11 cells ran. VERDICT = G-HOLES (family). Genuine deliverable (airtight, sealed+blinded,
  committed 46fd6be before any cell): a moderate label-leak (factor_resid, lambda=0.3, +0.0021 nats) passed the
  ENTIRE mechanical battery — D1 calls it significant, D4 blind to synthetic cols, D7 ceiling above it, D3's
  "catch" an artifact; only a reproduce-from-stated-features check (which the engine LACKS) flips the sign.
  The blinded D9 agent fleet found it (R4) + re-derived the wild 2026-03-23 kill (R2) + its DECOYS found a REAL
  latent defect (post-hole ADV20 windows bridge the Jan-Mar warehouse hole -> GLXG 22-31x, true gate FAIL).
- HOSTILE RETEST (wf_8f296538-7e3, 7 agents, all 6 QUALIFIED + critic): the gauntlet's OWN headline statistic
  is ill-posed -- flat "6 of 11 surprises" = ~4 distinct defects (CLEAN_A/B byte-identical; 2 D6 surprises vs an
  unregistered baseline, 1 a B=60 coin flip; D3 organ MIS-IMPLEMENTED vs its own prereg). Recursive lesson: the
  discipline stack cannot certify its own instrumentation any more than universe membership (273) -- only
  inward-turned adversary caught it. SS4 framing preserved + banner-marked; SS5b carries every correction.
- New (substrate, doc-272 bus): src/research/experiments/engine_immune_system.py -- membership audit as a
  NIGHTLY standing gate; first run flags 1,486/10,786 corpus ticker-days (13.8%) contaminated. Chip
  task_4994579a (day_aggs rebuild + window-integrity guard) supersedes task_80964988.
- SYSTEM_MAP impact: none live. Research lineage: 273 found a blind class in the INPUTS; 274 found one in the
  MEASUREMENT APPARATUS. Next: factory-FDR night builds the reproduce-from-features + membership + fleet-
  deflation gate triad, validated on a certified-null synthetic fleet with planted graded-strength leaks.

## doc 273 -- 2026-06-10/11 -- THE KNOWABILITY FRONTIER: open-charter research night (pre-registration commit)
- DORMANT-C research, read-only vs production (deployed tree 2891a67 untouched). THE QUESTION: I(t) = how much
  information about the cost-cleared remainder-of-day outcome exists in the tape-up-to-t, on a 13-point decision
  grid 9:35-15:00, vs M(t)/A(t) = whether the remaining move still pays at the first detectable t — the race
  between revelation and exhaustion, run on the doc-235 certified-null gapper universe (3,553 ticker-days,
  2025-08..2026-04 tick window). Inference: within-day label permutation, Westfall-Young maxT over (t,family);
  power: planted-signal MDE (s in {.05,.10,.20}) — a null without power proof is not a null. Verdicts frozen
  pre-run: V-NULL / V-RACE / V-WINDOW / V-UNPOWERED, meanings written before results.
- Deep-research workflow wf_25d579e3-54c (107 agents, 22 claims verified 3-0, 3 refuted): composite design has
  NO direct prior art (closest: Barclay-Hendershott RFS 2003 unbiasedness curves — realized not predictive, no
  cost overlay, liquid Nasdaq); MINE/DV formally avoided per McAllester-Stratos/Poole/Song-Ermon; planted-signal
  power calibration in financial ML has zero published precedent (the harness is itself new equipment).
- New: scripts/knowability_frontier_doc273.py (procedure = the pre-registration; committed BEFORE any model
  touched labels — git history is the audit trail), docs/research-log/273_knowability_frontier.md (SS1-3 prereg;
  results appended post-run in a separate commit), scripts/probe_doc273_assets.py (recon probe).
- SYSTEM_MAP impact: none live. Research lineage: extends docs 245-261 from point-nulls to the full curve;
  the MDE harness becomes the standard power-audit for every future null.
- RESULT (same night, post-retest): as-registered run gave V-RACE p=0.010 (S*=+0.012 @14:00) — KILLED by the
  8-agent hostile retest: day_aggs warehouse silently missing 54 sessions 2026-01-02..03-20 -> corpus gate's
  lag(close) admitted 389 stale-prev-close zombies on 2026-03-23 = 92% of the statistic. Clean re-run
  (prev-close lag <= 4d filter + fully mirrored null): S* = -0.0005, NOT ONE positive cell -> **V-NULL**
  (boundary case p=0.0495-with-negative-statistic disclosed, not rounded away). A(t) < 0 at all 13 t survived
  every referee incl. ex-contamination. NEW STANDING RULE: every research universe gets a membership audit
  vs raw warehouse data before inference (exchangeability tests cannot catch membership contamination).
  Chip task_80964988: rebuild day_aggs 2026-Q1 + re-audit corpus consumers (docs 235-261). Live experiment
  knowability_frontier grades production placement vs the CLEAN frontier nightly.

## doc 272 -- 2026-06-10 -- THE DIAMOND PASS: flawless layer shipped (deploys next boot) + concurrent-experiment foundation seeded
- MERGE TRAIN (6 commits, hostile-reviewed, risk-tagged): 1ad617f B1 spine (trace_lineage.py: HWH forensic = one
  command; drift forensics: ledger-broker deltas = avg-entry-vs-FIFO + day-attribution, NOT integrity) | a0b8811
  C2 DIAMOND SEED (read-only event_bus over durable streams + Experiment interface/registry/isolated runner +
  reference experiment that independently re-derived the HWH phantom on first run + 19:30 hook - new experiment =
  REGISTRATION not pipeline) | 744d843 D2+D3 LEVERS priced+OFF (MOMENTUM_EMPTY_NOT_BEAR absence-fail-open 0.5x,
  conservative classification; FAST_PATH_DIP_OVERRIDE_PCT; no-op-when-OFF pinned 17-case both-ways, 50 tests) |
  e13e543 A1 VLL EXHAUSTIVE (52 emit sites, 26+ gates; 3 truth-over-plan finds incl MAIN-D204 never wired by 268
  + SUBMITTED orphaned every instant fill; zero control-flow changes; pin test) | b8eef0a pin sync | 2891a67 A2
  PAGING (incident_pager DRY-unless-armed, 6/6 self-test vs real bus; 6 detectors emit through never-raises
  wrappers: RECON_LETHAL/QTY_GHOST/HEDGE_VIOLATION/EMERGENCY_STOP_FAILED/CB_TRIP/WATCHER_DOWN; synthetics
  namespace; conftest quarantines pytest bus pollution - 16 fake CRITICALs were leaking).
- GAUNTLET on merged tree: compileall OK, 116/116 consolidated gate, 297-module import graph clean. Deploys at
  the next 04:30 boot (files-on-disk; bot exited 16:00 - forced midnight restart would idle till 04:00 + risk
  session-state edges; no-drawer intent met). Residual: full suite + 16:01 adversary post-deploy on record.
- CHIPS: 9 closed (3 of them found TODAY during the work), carried items have owners. Scorecard: VLL B-→A-,
  incident_bus C→B+ (A on arm), recon B→B+, health B+→A-, grader B+→A-.
- PIERCE one-liners: INCIDENT_PAGER_ARMED=1 (+optional 5-min loop); MOMENTUM_EMPTY_NOT_BEAR=1;
  FAST_PATH_DIP_OVERRIDE_PCT=0.0; standing flips/keys/cutover-go after >=5-session shadow.

## doc 271 -- 2026-06-10 -- THE HUAYRA BLUEPRINT: honest system critique + the 8-build path to a generational machine
- The unsoftened critique (7 close paths bred 7 phantom siblings; journal never matched broker on any audited day;
  D93 config-drift class recurred 4x; alarm wires never connected) + the principle: ONE SOURCE OF TRUTH PER
  QUESTION, ZERO SILENT ANYTHING. 8 refits, most seeded this week: (1) ledger cutover (shadow live, 3 exact-zero
  days), (2) unified execution model + CI calibration (B1 spec), (3) one-close-path state machine (the big one),
  (4) correlation spine + trace_lineage, (5) config-as-law + drift CI, (6) self-proving alerts + morning brief,
  (7) research factory (T2 promotion-rule pattern), (8) capital architecture. No rewrite - refits, seeds on the bench.

## doc 270 -- 2026-06-10 -- LIVE-FIRE DAY: new phantom sibling caught + FIXED same day; ledger==broker to the cent; fleet graded; cost ledger
- LIVE-FIRE: VLL day-1 flawless (93 events, all verdicts terminal'd, 3 SUBMITTED == 3 broker fills). NEW PHANTOM
  SIBLING caught w/ 2 same-day specimens: D297 fill_stream_bridge booked the FIRST exit leg as the TERMINAL close
  (HWH: D165 T1 sell 5479@2.02 booked +54.79 STOP_FILL for all 16437 sh; the stop's -970.94 wave NEVER booked;
  FLD -68.55; EPSM = 5.8h ghost + 700 unread shadow-LETHAL ticks). Journal poisoned +1039.49 on the day. FIXED
  cb880fd [LIVE-A]: partial-aware per-leg booking (cumulative increment math, exec-id dedupe before side effects,
  tranche/short guards, broker position_qty terminal truth); 138/138 gate incl exact HWH replay; hostile review
  passed; deploys Thu 04:30.
- THE CUTOVER PROOF: nightly 3-way recon via the fixed consumer path: LEDGER -714.68 == BROKER -714.68 ($0.00) on
  the day the journal drifted +1039. Three exact-zero ledger days (6/3, 6/9, 6/10). Adversary wrapper_bugs=[].
- SHIPPED: 269c032 testdebt (baseline 4180/76 published) | bb73e5d ledger shadow | 2fdf9b4 cost census (dead-
  fallback bleed ENDED w/ today's boot; EDGAR 100%-failing all week; FINNHUB ALIVE - doc-249 corrected; tier2
  p95 23.4s vs 25s = 1.6s slack, staged) | 08a3dad EDGAR hardening [LIVE-A] | 1cfab0f grader import-sys (caught
  by the fleet assessment BEFORE tonight's 19:30) + watchdog weekend guard (Saturday ~200-post storm defused).
- B1 FIDELITY MATRIX: 13/13 submitted orders filled -> 100% of sim divergence = pre-submission gates (D200 56%/
  D204 31%); all sims C-/D as fill models; execution_model.py unification spec captured. simulate_day.py never
  existed (stale MEMORY lore corrected).
- FLEET GRADES (no A's): B+ to C across 16 pipelines; systemic top-5 = alarms-without-sirens, book-from-intent,
  shared-account/synthetic entanglement, never-test-fired alerts, untested producer-consumer contracts. Priority
  gap = incident bus has 0 real consumers (today's real 5.8h naked position emitted NOTHING). +10 evidence-tagged
  chips (stop_widening dead since 6/4; raw_fills completeness; registry self-verify; VLL 3/26 coverage; etc).
- Pierce checklist: flip lines + isolation keys; D200/D204 dollar brief (VLL prices it nightly); paging arm (G1
  builds flag-OFF next); rot dispositions; Thu deploys = 08a3dad + cb880fd, one-line rollbacks each.

## doc 268 -- 2026-06-09 -- NIGHT OPS: silent-verdict killer NAMED (D200/D204 data-absence) + Verdict Lifecycle Ledger shipped (DORMANT-C)
- KILLER NAMED w/ file:line + log evidence: D200-E4 CATALYST GATE (main.py:3453) = 105 blocks on 6/8 naming 8 of
  the silent-14; ate BYAH on 6/9 (gap 41%, RVOL 2582x, #1 momentum rank 1120) one line after its BUY verdict.
  D204 NEWS GATE (main.py:6676 VWAP / :6975 RESCAN) ate ELPW/PAVS/GLE/CGTL/CPOP. D56 already-held = the carried 3.
  MECHANISM = DATA ABSENCE not bearish signal (no-coverage micro-caps + SEC EDGAR 500s; same absence zeroes
  catalyst_news x0.55 in MFCS -> BYAH 0.289). The doc-177 blind funnel is the #1 verdict-eater and is ANTI-
  SELECTIVE against the no-news lottery rockets. Cap-8 hypothesis REFUTED (no cap-block lines); D170 REFUTED.
  Gate policy NOT changed (strategy call -> Pierce; absence-vs-bearish distinction proposed, VLL will price it).
- SHIPPED VLL (DORMANT-C): src/ops/verdict_ledger.py vll_emit -> data/ops/verdict_trace_<date>.jsonl, NEVER
  raises (smoke-proven). Emits: BLOCKED_CATALYST_GATE (D200 block), BLOCKED_NEWS_GATE (D204 VWAP+RESCAN),
  SUBMITTED (bridge.py success terminal w/ order_id). Every silent-class death self-names from tomorrow's open.
  Gauntlet: compileall OK, 34/34 close-path suite green, never-raises wrappers at all 4 sites. Grader invariant
  (journal BUYs vs trace terminals) chipped for daylight.
- DEFERRED w/ staged evidence (clean small ship > sprawl; HEAD deploys 04:30): Phase 2 D164/D165/OCC (LIVE-A,
  first daylight item; pattern + lines in data/research/doc268_night_state.md), Phase 3 test chips (filed),
  Phase 4/5 (state file holds the plan). 6/9 session: broker +$149.76, ghosts=0 (FIRST clean-ghost day - D163
  fix confirmed live). Deploy statement: only live-adjacent change = VLL logging. No config/flag/gate changes.

## doc 267 -- 2026-06-09 -- New serverless models benched; debate assessed (keep OFF); dead settings-defaults FIXED; final sweep green
- MODEL BENCH (model_bench_doc267.py, drop-in bare-JSON + judgment trap): only DeepSeek-V4-Pro is production-
  grade among the 6 new (0.8s, 3/3 JSON, judge PASS, $1.74/$3.48); GLM-5.1/Nemotron-3/Kimi-K2.6/MiniMax-M2.7
  all 0/3 JSON (format non-compliant on bare-JSON = not drop-ins); Qwen3.7-Max 400. Current stack all 3/3+PASS
  and faster (live T2 0.7s) -> KEEP primaries; V4-Pro = noted candidate iff a Tier-1 reasoning gap appears.
- DEAD-DEFAULTS BUG FIXED (config/settings.py): tier1/tier3 defaulted to DeepSeek-V3.1 and tier2 to Qwen3-Coder-
  480B -- BOTH no longer Together-serverless (verified 400) -- and the tier2 FALLBACK was also V3.1 = broken
  middle link (primary fail -> instant 400 -> emergency). If env ever fails to load (D93 class), the bot boots on
  dead models. Fixed: tier1/tier3 -> Qwen3.5-397B, tier2 -> Qwen3-235B-2507-tput (the live pins), tier2-fallback
  -> Llama-3.3-70B (alive, diverse architecture). Settings load verified; 241 config tests pass; 7 failures are
  PRE-EXISTING default-drift (identical with edit stashed; D104/D105/D122/D207/debate-timeout test expectations
  stale -- chip filed, same class as the pipeline-guard MagicMock chip).
- DEBATE ASSESSMENT: intentionally OFF (max_debate_attempts default 0; D100 "0% conversion over 7 debates,
  20-45s each"). Code healthy (judge = CURRENT Qwen3.5-397B at debate_engine.py:89; D94 cascade bugs fixed;
  thresholds tuned). VERDICT: keep OFF -- debate is a selection-quality layer and docs 245-254 proved selection
  has no signal (it rejected 10/12 profitable candidates historically); a smarter judge can't mine signal that
  isn't there. Only future role = a specific measurable adjudication task; bench V4-Pro as judge then.
- SWEEP: all 10 touched files compile; 34/34 close-path + t2 + 241 config tests pass; tonight's ShadowGrader
  cron ran exit-0 (doc-263 ship alive). PENDING Pierce's hand: the doc-266 flip lines (T2_WIDE_PCT=0.75 +
  EXEC_MAX_POSITIONS=16 + EXEC_MAX_POSITION_PCT=0.05). Tomorrow #1: trace the silent execution-blocker (6/8's
  14 no-order-id verdicts) + make it log loudly.

## doc 266 -- 2026-06-09 -- Grip Switch night: broker-truth gate fired + resolved (one-ticket artifact); no-submission discovery; 3 flip lines await operator
- PHASE 1 (arm_broker_truth_doc266.py, broker fills only; stop_decisions row session_date=None -> FILENAME is
  the date): 32/32 FIFO round-trips arm-tagged (16 wide/16 tight). Gate FIRED on raw mean (tight +8.87% vs wide
  +3.65%) -- investigation: the contradiction = ONE ticket (LASE +172.9% in the tight bucket, banked by the
  OVERNIGHT HOLD available to both arms; tight's own ledger holds the -$684 LASE stop-churn). WINSORIZED (the
  standing tail gate applied to the gate itself): wide +0.14% vs tight -0.05%, win 44% vs 38%, ex-monster wide
  -1.53% vs tight -2.05% -> statistical TIE, slightly wide. Live read neither confirms nor contradicts; weight
  of evidence (replay n=306 + shadow n=14 + churn mechanism) = wide.
- DECISION: tilt 75/25 (MOMENTUM_T2_WIDE_PCT=0.75), KEEP 25% tight control. PRE-REGISTERED promotion: n>=30/arm,
  winsor mean AND median wide>=tight -> 100%; reversed -> back to 50. The arena now has an exit condition.
- BAR1 RESOLVED: MOMENTUM_EXIT_POLICY=t1_next_open is LIVE (User env; log "BAR-1 SKIPPED... carry overnight via
  D91") -- the losing bar1_legacy (-$5K/117 by its own docstring) was already flipped off per doc 69 (+$23K/117).
  Hold-to-next-open is already the live default (= how LASE's overnight +$73K was captured). No action needed.
- DISCOVERY (supersedes the dip-table story): the 14 "unfilled" 6/8 entries have NO ORDER IDs -- verdicts died
  BEFORE submission (105 BUY rows, phase=MARKET_OPEN, no oid; 7 of the 14 were the day's rockets). Replay sim
  "filled" 16/19 because it had no execution gates. Cap(8)+carries+queue is the prime suspect but NOTHING LOGS
  the block -> tomorrow #1: trace the execution decision point + make it log loudly. DIP_TABLE flatten deferred.
- BREADTH (replay-validated independently): EXEC_MAX_POSITIONS 8->16 + EXEC_MAX_POSITION_PCT 0.15->0.05 (the
  many-small-tickets structure; ~$10.8K cap, wide-arm 0.5x -> typical ~2.5% ~= replay's $5K ticket). More
  diversified, smaller per-name; -10% daily circuit pinned.
- AGENT PERMISSION-BLOCKED at live config/secrets writes (correctly) -> the 3 flip lines + rollback are in the
  doc for Pierce's hand. Shipped without operator action: the WIDE_PCT knob (dormant), fill-aware grader, and
  t1_next_open was already live. Scoreboard = nightly grader: fill rate, UNFILLED-THAT-RAN, per-arm n toward 30.

## doc 265 -- 2026-06-09 -- Entry A/B replay: marketable REFUTED; winner = decision-price entries + wide stops; T2 wide-fraction knob shipped
- REPLAY (entry_ab_replay_doc265.py): 328 journal BUY decisions (26 sessions) on REST minute bars. Arm A
  (limit AT decision price, no slip) vs Arm B (marketable +50bps), identical flat-15:55 exits. B REFUTED on
  every pre-registered clause: A +$30,274 vs B +$13,130 @$5k/ticket, B leads 5/26 days, B worse winsorized.
  The discipline killed my own doc-264 "go marketable" lever in 24h.
- CALIBRATION MISS = THE FINDING: sim-A filled 16/19 on 6/8 vs broker's actual 5/19 -> production's DEEP dips
  (DIP_TABLE 2-15% below) fill 26% + adversely select; a DECISION-PRICE limit fills 94%, misses only 20/326
  (the +10.2%-mean NPT class), still beats marketable. The leak is dip DEPTH, not passivity. (Main paths went
  marketable-crossing in doc 189/190 already; the deep-dip leak lives in fast-path/other flows.)
- STOP PANEL (3rd independent confirmation): same 306 fills -- no stop +$30.3K, -20% +$30.8K, -15% +$20.7K,
  -10% +$11.1K. Tight stops destroy half-to-two-thirds. Concordant w/ D308/D309 live shadow (n=14, ATR
  +$425/decision) + the LASE specimen (stopped -$684 at second 31; banked +$73K only via re-entry+hold).
- THE SYSTEM ALREADY BUILT THE FIX: binding tight stop = D139 phase1 1.5% x 7min (alpaca_executor.py:422);
  doc-171 T2 wide_stop arm skips it ("don't shake out of momentum runs at 1.5% noise") and the A/B is LIVE
  50/50 NOW (6/8+6/9: 4 phase1 vs 5 wide_arm_standalone submissions).
- SHIPPED: MOMENTUM_T2_WIDE_PCT env knob in t2_arm_assignment.py (per-call read; unset=original 50/50;
  verified 1.0->all-wide / 0->all-tight / garbage->fallback / deterministic; doc-171 test passes). NOT flipped
  tonight per the rule: the live per-arm broker-truth read isn't computable from journals (sparse + phantom-
  poisoned); quick join = 0 matches. MORNING CHECKLIST: (1) broker-truth per-arm read -> if wide leads, flip
  MOMENTUM_T2_WIDE_PCT=1.0 (one env line); (2) DIP_TABLE flatten behind env flag + simulate_day; (3) bar1_exit
  (D145) audit -- default-True "sell 100% @T+60s" but 0 traces on 6/8, confirm dormant; (4) D164/165 + OCC
  journal fixes; (5) grader v2 nightly is the scoreboard.
- SIZING TRUTH: "all-in on rockets" = ruin; the structure is many small tickets x wide stops x hold-to-close
  x disaster-stop + the pinned -10% daily circuit. Max tail exposure per dollar of ruin-risk.
- ROCKET-PICKING remains CLOSED (245-254; live rocket-shadow 0-for-4, -22.6% mean) -- the uphill door is
  mechanical (stop de-selecting tickets), not predictive. No live behavior changed tonight.

## doc 264 -- 2026-06-08 -- Account forensics: the doubling = 2 lottery tickets; the system DE-SELECTS its own winners; the honest max-profit program
- DECOMPOSITION ($100K->$216K, account_forensics_doc264.py): median day +0.000%, daily win 29%, top-5 days = 140%
  of total gain (other 72 days NET -$46K), median ticker -$90. TOP-2 TICKERS = $114K of $116K: LASE +$73,140 (real
  squeeze, 6/3) + CRCA +$40,985 (Feb reverse-split PAPER ARTIFACT). The live account fails the same hostile gate
  that killed 7 backtest illusions -> the doubling IS the lottery's right tail, not a process.
- LASE mechanics (broker fills): stopped out -$684 31 SECONDS after entry -> re-bought -> HELD OVERNIGHT -> sold
  ~26,800 sh @ $4.08 into the morning spike. Biggest win ever required re-entry after stop-churn + overnight hold.
  LASE was red-flag 0.9 the day it paid.
- THE FINDING (fill-aware grader on 6/8): journal BUYs 19 -> broker-FILLED 5 = the FADERS (OCC -3.1, TNGX +3.1,
  GMHS -33, ABAT -5, AIM -16); SUBMITTED-UNFILLED 14, of which SEVEN RAN: NPT +274%, BYAH +121%, MTEN +76%, TDIC
  +60%, SMTK +36%, RYET +18%, SUGP +16%. The dip-limit entry is an ADVERSE-SELECTION MACHINE by construction
  (fills what falls through it, misses what runs). Same anti-tail tuning at every layer: stop-widening shadow
  (n=14 live): ATR stops +$7,733 vs tight +$1,778 = +$425/decision left on table; early tranche shaving gave away
  a 3x on LASE shares. System tuned as a MEAN-game; the P&L proves it's a TAIL-game.
- REVERSAL: doc-262's veto-gate recommendation is DEAD -- LASE rf=0.9 paid +$73K, AIM rf=0.95 +62%; flagged names
  = variance cluster (doc 254) and variance is the only product. Veto NOT wired; red-flag stays passive/research.
- 5%/day math: 1.05^252 ~ 218,000x/yr -> category error, refused. Honest target: kill the ~-$640/day median bleed
  + STOP de-selecting tickets (entry/stops/exits) + bank lumps. Lumpy right-skewed compounding, not daily law.
- PROGRAM (each flag-gated + replay-validated, NO blind hot-path tonight): (1) entry A/B replay dip-limit vs
  marketable-at-trigger on every journal BUY since May (prices the missed-tail asymmetry the fill_backtest never
  measured); (2) ATR stops after shadow n>=30 + simulate_day validation; (3) conditional runner-hold (LASE
  pattern) tested median-gated; (4) D164/D165 + OCC journal integrity fixes. Will NOT: fake-5%/day backtests,
  leverage-up a -EV process, measurement games.
- SHIPPED tonight (safe): account_forensics_doc264.py + fill-aware grader v2 (FILLED vs SUBMITTED-UNFILLED split
  + UNFILLED-THAT-RAN line, nightly 19:30) + this doc. No live trading-behavior change.

## doc 263 -- 2026-06-08 -- Ops hardening: phantom-ghost root-cause fix + bot account isolation + same-day grader
- PHANTOM-GHOST FIXED AT SOURCE (main.py D163 trailing-stop close, ~6064): the path closed via close_position(),
  swallowed the 403 when qty held_for_orders by the OTO stop, booked P&L from an MTM SNAPSHOT (not a fill) + cancelled
  the stop -> naked position + phantom P&L (TNGX +747 broker $0, ABAT +730 broker $528 on 6/8). FIX: route through
  attempt_close_with_status_check(cancel_blocking_stops_first=True) + the Bug-Z gate (if not succeeded: do NOT cancel
  stops, do NOT book, continue) -- the exact hardening D78 SMART_EXIT (~6280) already had + D163 lacked. 34/34
  close-path tests pass. FOLLOW-UP flagged: sibling partial-exit paths D164 EARLY_PROFIT_TAKE (~4393/~5720) + D165
  tranche-take share the pattern; OCC's +936 delta is a separate trade_journal.record_close ticker-fallback overwrite
  (stamps re-buy close onto first BUY) -- both deferred to a careful follow-up.
- ACCOUNT ISOLATION (lottery_runner.py + fader_short_runner.py): per-bot key override LOTTERY_ALPACA_*/FADER_ALPACA_*
  with fallback to shared ALPACA_* (no behavior change today). IMMEDIATE mitigation: Lottery bankroll HARD-CAPPED to
  $5k whenever NOT isolated (aggressive-Kelly can't draw prod's equity); FaderShort already bounded ~$1,250. Loud
  ISOLATED-vs-SHARED startup log. ACTION REQUIRED (Pierce): create 2 Alpaca paper sub-accounts + add LOTTERY_ALPACA_
  API_KEY/_SECRET_KEY + FADER_ALPACA_API_KEY/_SECRET_KEY to secrets -> full isolation, cap auto-lifts.
- DATAINGEST LAG is inherent (Polygon S3 flat files publish next-day ~04:00 ET; daily_data_ingest.ps1:279-283).
  Only affects research freshness (prod uses live API). Rather than re-plumb, the grader fetches same-day prod-trade
  outcomes via Polygon REST open-close -> prod grading is same-day; red-flag/rocket cross-ref backfills next day.
- SAME-DAY GRADER (shadow_vs_prod_grader.py + run_shadow_grader.ps1 + MomentumX-ShadowGrader @19:30 daily): parses
  prod (journal+eod) + Lottery/FaderShort logs + red-flag/rocket shadows, fetches same-day outcomes via REST,
  cross-refs prod longs vs every shadow signal, grades veto-would-have-helped, posts OPS_ALERT. Verified 6/8: prod
  opened 19 longs (CORRECTS doc 262's manual count of 1) - high-variance (NPT +274%, BYAH +121% vs RMSG -45%, GMHS
  -33%); Lottery flagged 2 (RMSG/GMHS), both losers. No live trading-behavior change beyond the phantom fix (which
  only PREVENTS booking on unconfirmed closes).

## doc 262 -- 2026-06-08 -- Shadow processes vs prod: deep-dive forensic comparison (2026-06-08)
- Read-only forensic: 8 scheduled processes ran today. PROD (PaperTrading) was the ONLY risk-taker: -$1,172.71
  broker, $216.2K equity, 0 overnight. The whole loss = ONE low-conviction long: GMHS BUY @ MFCS 0.20 ->
  STOP_FILL -$2,304; rest is ghost/overnight noise (TNGX journal +747 but broker $0 = phantom; 3 ghosts force-
  closed). Selection is the bleed (win 30.8%, median candidate -3.46%, MFCS AUC 0.548 useless, mu_edge -$174);
  execution healthy (adversary 10/11, 0 wrapper bugs).
- OPERATIONAL: 3 trading bots (prod, Lottery, FaderShort) share ONE paper account PA3A2I4TN9AZ -> P&L commingles;
  Lottery aggressive-Kelly (up to 50% equity) draws on prod cash if it ever fires. Shadows run a DAY BEHIND
  (DataIngest lag -> RocketShadow/Watchlist couldn't score 06-08).
- TRADING SHADOWS both 0-fill today, instructively: Lottery MetaScorer SKIP'd all 10 (incl GMHS @ 0.169 - the
  shadow gate would have vetoed prod's -$2,304 loser!); 0 trades for ~5 weeks. FaderShort: all 4 short cands
  NOT-SHORTABLE; lifetime 67 attempts / 0 fills = the borrow wall PROVEN LIVE (confirms doc-260/261: fader short
  uncapturable; only AVOIDANCE works).
- PASSIVE SHADOWS: RocketShadow (doc-246) anti-predictive OOS (top-1 -22.6%, hit 0%); RocketWatchlist red-flag
  (doc-261) weakly replicating (HIGH median -7.4% vs LOW -3.0%, -1.9pp); candidacy leads ~1.0x (no edge);
  Operator observe-only/gated OFF.
- CONVERGENT VERDICT: no durable long edge, short unborrowable, bleed is selection not execution. 3 actionable:
  (1) a red-flag/veto gate would have saved prod today (GMHS) -> soft-veto prod longs scoring >=0.6 once the
  filter reaches power; (2) retire/repurpose FaderShort (0/67 borrow wall); (3) isolate the 3 bots onto separate
  paper accounts + keep killing phantom-ghost generation at the source. No live change.

## doc 261 -- 2026-06-05 -- "1 & 3": LLM red-flag filter (shipped, prospective) + catalyst-handicapping pilot (pipeline works, universe doesn't)
- Pierce chose "1 & 3" at the doc-260 fork: build BOTH the prospective LLM red-flag filter (Track 1) + catalyst
  handicapping (Track 2).
- TRACK 1 (SHIPPED): wired an LLM red-flag/fader scorer into rocket_watchlist_engine_doc255.py - reads a gapper's
  recent point-in-time SEC filings (8-K text + dilution filing types S-1/S-3/424B/ATM) -> Qwen3-235B scores
  dilution/toxic-financing/weak-fundamental fade-risk 0-1 + reason. Scores MECHANISM-PERFECT (AAOI 0.9 "ATM
  offering $600M"; AAL 0.1 "debt refinancing only"). Verification 2026-06-03 (26 gappers, 21 scored): 8/9
  high-flag names FADED on the day (DEVS 0.9->-9.5%, LASE 0.9->-11%, ZENA 0.6->-11.7%) - a ONE-DAY illustration,
  NOT validation. report() adds high(>=0.6)-vs-low(<0.4) red-flag -> oc_ret/rocket-rate; scheduled task now runs
  --llm so it accumulates OOS + look-ahead-immune. This is the deployable form of the doc-260 fader morsel (a RISK
  FILTER: avoid the gappers that smell like dilution-distribution), validated PROSPECTIVELY not in-sample.
- TRACK 2 (PILOT - pipeline works, universe doesn't): catalyst_handicap_pilot_doc261.py. Hostility caught a false
  "0% feasibility" = Polygon contracts endpoint needs &expired=true for past expiries (0->48-50 contracts). REAL
  finding: small-cap biotech binary events (|gap|>=30%, 263 found) mostly HAVE listed options (82%) but only ~8%
  have a daily-traded pre-event straddle price -> too ILLIQUID to price the implied move (trade aggs; quotes are a
  higher tier). Measurable only on the more-liquid subset = NOT our micro-cap problem universe. The 3 measurable
  cases' implied<realized is a SELECTION artifact (conditioned on big moves). Proper Track 2 needs a scheduled-
  catalyst calendar + options QUOTES on a liquid subset = bigger build, narrower universe, low prior post-Stage-B.
  Recommend NOT now.
- META: only deployable artifact = the LLM red-flag risk filter, collecting prospectively (a hypothesis under
  forward test, not validated). No tradable long alpha in price OR information (docs 250-260). NO capital, NO live change.

## doc 260 -- 2026-06-05 -- Stage B: LLM reads earnings TEXT -> weak reproducible SHORT-side fader-detector, NOT a tradable long edge
- Pierce chose "1 & 2" at the doc-259 fork: run BOTH the cheap LLM-text de-risk probe (Track 1) + scope catalyst
  handicapping (Track 2). Built stage_b_text_probe_doc260.py: point-in-time EDGAR 8-K text -> Qwen3-235B scores
  expected drift -> measure separation vs numeric baselines. 400 events + OOS replication on fresh 400.
- RESULT: the LLM-text score is MEDIAN-ROBUST + winsor-stable + ORTHOGONAL to price(corr +0.09)/fundamentals(+0.05)
  + directionally REPRODUCIBLE OOS = better than all 6 false-positives this session. BUT fails the tradable bar:
  L-S median +2.82%->+1.35% f10 across the 2 samples = NOT CI-separated (n~55-93/tranche, CI crosses 0);
  LONG-only leg does NOT clear cost (skill is all SHORT side, borrow-constrained); "beats price-reaction baseline"
  FAILS OOS. Verdict: earnings-drift info-game CLOSED as a STRATEGY (no capturable long edge).
- The reproducible morsel: a WEAK SHORT-side FADER-detector (down-pred ~-3.5% vs universe -1.1%, both samples) =
  mechanism-consistent with doc 253 ("dilution is the distribution") - the LLM plausibly reads financing/guidance
  red flags. Look-ahead caveat (8-K matched +/-5d): a leak can only INFLATE, so the clean number is <= +1.35%
  non-separated. => a RISK-FILTER hypothesis to validate PROSPECTIVELY (fold LLM-text score into the doc-255
  forward engine, measure if flagged-weak names underperform OOS), NOT a confirmed edge. NO capital.
- TRACK 2 (catalyst/binary handicapping): feasible (Polygon historical options aggregates ARE reachable ->
  straddle-implied odds buildable; clinicaltrials.gov + EDGAR for the calendar) but a LARGE build, prior tightly
  correlated w/ Stage B -> since Stage B failed the tradable bar, do NOT build the big options/calendar pipeline
  now. Pursue the LLM-text layer (if at all) as the prospective red-flag risk-filter only.
- META: information-synthesis (the last un-falsified door) yields NO capturable LONG edge on earnings text either.
  Combined w/ docs 250-259: no tradable long alpha for us in price OR earnings-information. Single forward-
  validatable residue = an LLM red-flag filter via doc-255. NO live change.

## doc 259 -- 2026-06-05 -- The information-synthesis game: design + pre-registered Stage-A gates (post-disclosure drift handicapping)
- PIVOT: price-only closed (doc 258) -> build the only un-falsified game = LLM event/fundamental handicapping on a
  NON-lottery universe (Pierce's call at the doc-258 fork). This doc DESIGNS it + pre-registers the first test.
- THE GAME: post-disclosure (PEAD) drift handicapping - market reacts to the 8-K headline but under-prices the
  DETAIL in the full 10-Q/10-K (segment/margin/guidance text an LLM reads deeper than the median small-cap
  participant). Structural fit: slow horizon (patience, no HFT contest) + info-dense (LLM synthesis) +
  capacity-constrained thin-coverage small/mid-caps (least arbitraged, small size is an ADVANTAGE).
- DATA WE ALREADY OWN (grounded): vX_reference_financials = 2,376 tickers w/ filing_date + acceptance_datetime
  (exact SEC point-in-time anchor) + quarterly rev/NI/op-inc/EPS (~16K ticker-quarters 2024-26) + day_aggs price
  + existing LLM stack (fundamental_agent/news_agent/catalyst_classifier/news_client) + EDGAR free full text.
- STAGED CHEAP-FIRST PLAN: Stage A (price+fundamentals only, no LLM, runnable now) = does a base anomaly even
  exist in OUR universe? Stage B (only if A clears) = does an LLM reading the TEXT beat the numeric-surprise
  tranche? Stage C = forward paper-shadow. Branch B' (if A flat) = catalyst/binary-event handicapping.
- PRE-REGISTERED Stage-A gates (committed BEFORE running): (1) MEDIAN top-tranche drift positive & > bottom @10/20d
  (2) cross-regime 2024&2025&2026 (3) NOT tail-illusion (winsorize@+/-20% survives, top-decile <60% of P&L)
  (4) after ~0.4% cost, long-only leg clears (5) edge concentrates in thin-coverage corner (capacity check)
  (6) look-ahead audit: every input knowable at acceptance_datetime, drift starts next session. ALL 6 or it fails.
- Discipline carried forward (killed 6 false-positives this session) + ONE new trap: point-in-time/no-look-ahead.
  NO live change, NO capital. Next: scripts/pead_stage_a_doc259.py.
- STAGE A RESULT (pead_stage_a_doc259.py + _v2_): NO numeric base anomaly. v2 detected the TRUE earnings day
  (volume-spike in [qtr_end+10,+80], median lag 40d) + tested the LONG-SHORT directly (5,586 events, 1,266
  tickers): announcement-reaction sort = the biggest earnings pops FADE (L-S f10 -3.3/-2.5%, f20 -2.7/-3.8%,
  robust both big regimes) = post-earnings REVERSAL not drift (same fade as gappers); fundamental-surprise sort
  = FLAT (+0.1/+0.5% f10, fails f20). Reversal identical thin vs large (no thin-corner drift). 7th efficiency/
  fade finding this session - the hard earnings info is priced (even over-reacted) within 10-20d.
- IMPORTANT: Stage A only tested the NUMERIC layer; doc-259 thesis was always SOFT TEXT (an LLM reads guidance/
  tone/segments). Stage A makes Stage B a genuine-but-LOWER-probability + higher-cost bet (LLM must create ~100%
  of the edge vs a mildly-reversing base). Pre-registered decision honored: do NOT build the full LLM pipeline on
  a non-existent base. Responsible next = CHEAP Stage-B de-risk (~300-500 held-out events, EDGAR point-in-time
  8-K text, Tier-2 LLM scores drift from text alone, check median separation cross-regime BEFORE scaling).
  Spend deferred to Pierce - economics shifted. Branch B' (catalyst/binary handicapping) = the alternative.

## doc 258 -- 2026-06-04 -- The slow game: price/volume closed in BOTH corners (lottery=uncapturable, liquid=efficient); only door left = information synthesis
- PROBE 1 (multi_day_candidacy_signal_doc258.py): the candidacy/dilution signal on a MULTI-DAY gapper hold LOOKS
  like a cross-regime edge (FRESH +6/+5/+24% @10d; long-FRESH/short-BEARISH net +2.5/+8.9/+8.5%, CI-sep 2025) but
  DIES on the hostile winsorize/median check: FRESH median NEGATIVE (-1.5/-4.2/ALL -1.8%), win<50%, winsor@+30%
  flips negative, top-10% carries 175-259% of P&L. Same fat-tail lottery on a 10-day clock -> the candidacy
  signal CLUSTERS rockets (FRESH>BEARISH, dilution-distribution real) but clustering is NOT capturable. 6th
  false-positive the discipline caught this session.
- PROBE 2 (liquid non-lottery momentum, 5825 tickers/1.6M stock-days): cross-sectional 60d-momentum -> fwd 20d,
  medians now SANE (win~50%) -> L-S median +0.36/+0.64/-0.26% by regime = ~flat + regime-flips (inverts to
  reversal 2026). The well-known factor competed away. Efficient.
- DECISIVE: no median-positive cross-regime-robust price/volume edge in EITHER corner - lottery uncapturable
  (median loses every horizon), liquid arbitraged (factor ~0). Structural: we have no speed edge (lose HFT in
  liquid) + no capture edge (taker paying spread/borrow in lottery). Price is efficient where we can reach it.
- ONLY DOOR LEFT = INFORMATION SYNTHESIS (LLM handicapping catalysts/fundamentals on slow horizons, candidacy/
  dilution as the risk filter) - a DIFFERENT build, not a price backtest, where our structural advantage
  (patience + info-synthesis + small size) actually lives. The price-only door is closed from both ends. NO live change.

## doc 257 -- 2026-06-04 -- RocketScalp-RL "the Monster" v2: 10-floor SOTA RL design + force-to-trade redesign -> the RESPECTABLE NULL (forced-aggressive RL can't beat flat/long after cost)
- 7-agent architecture deep-research (wf_6b61e20f-5c7) -> the monster stack: env -> twin Mamba(tick)+TCN(bar)
  towers -> cross-attention fusion -> FiLM candidacy conditioning -> IQN ensemble critic -> tail-SEEKING risk
  policy (force-to-trade) -> conformal sizing -> CPCV+Deflated-Sharpe. Every layer justified; the ornamental
  ones (retrieval/peer-graph/Decision-Transformer/world-model/KAN) flagged as "gargoyles on the facade".
- FORCE-TO-TRADE = flip CVaR-averse to a tail-SEEKING spectral distortion (Wang eta>0), DECISION-TIME ONLY,
  learning target undistorted (the Pitfall-of-Optimism detail), 0 new params.
- BUILT (Phase 1, rocketscalp_rl_v2_doc257.py): FiLM candidacy+tick-microstructure conditioning (use ALL
  collected data) + tail-seeking distortion + state-augmentation + VECTORIZED rollout (v1 was hours/epoch ->
  minutes). enrich_episodes_doc257.py wires the doc-255 candidacy features into every episode.
- A/B RESULT (held-out 2026 net @30bps, 4-epoch walk-forward): MONSTER[wang/aggressive] -0.54% (turns 1.6),
  MONSTER[cvar_low/conservative] +0.63% (turns 2.6), long-only +0.79%, vwap-mr +0.52%, flat 0%. Both RL arms
  COLLAPSE TO ~FLAT (IQN loss->0 = policy abstains); forcing-to-trade did NOT work (wang abstained + went
  negative); the +0.63% is a noise mirage BELOW trivial long-only. RESPECTABLE NULL: a SOTA distributional
  risk-sensitive RL agent, forced aggressive with all features, can't beat flat/long net-of-cost. NO live change.

## doc 256 -- 2026-06-04 -- RocketScalp-RL: cost-realistic distributional risk-sensitive deep-RL intraday trader (env + multi-scale encoder + IQN-ensemble CVaR agent + baselines)
- Tests Pierce's harvest-the-variance thesis (stop predicting direction; trade the movement). ENV = cost-realistic
  price-replay charging 30bps/turn (random churn -89% = the spread wall confirmed; flat exactly 0). Encoder =
  multi-scale dilated-TCN + slow agg + static. Critic = IQN distributional, K=4 randomized-prior ensemble. Policy
  = CVaR-greedy (tail-averse). Walk-forward train 2024+2025 -> held-out 2026, net-of-cost.
- Baselines (net @30bps): flat 0%, long -0.75%, random -89%, selective vwap-mr -0.16% (near-breakeven, the narrow
  window). v1 full-scale train too slow (per-step rollout); the conservative design re-run faster as doc-257's
  cvar_low arm -> +0.63% held-out 2026 = abstention null (below trivial long-only). NO capital / NO live change.

## doc 255 -- 2026-06-04 -- Morning rocket WATCHLIST + collection engine (the honest month-long vision: a better universe + a POWERED forward test of the 2 leads, NOT a predictor)
- scripts/rocket_watchlist_engine_doc255.py: daily candidacy screen (doc-253 signature, computable: micro_float
  [ticker_details sh_out<5M], recent_reverse_split [splits<90d], foreign_issuer [locale], prior_runner +
  coiled [day_aggs]) -> candidacy score + ranked watchlist of the day's gap>=8% names. Validated 6/03 (26
  gappers, top SDOT/APLZ+397%/RUBI/HUBC, ATPC rocketed).
- LEAD DETECTION (the point of the month): INSIDER Form-4 open-market buy clusters via SEC EDGAR (submissions API
  -> code-P purchases, >=2 owners, 21d window) + COILED flag. Logs features + realized outcome (close/open +
  rocket flag) -> data/research/rocket_watchlist_log.jsonl. --report = running lead-vs-outcome rates.
- HONEST per doc 254: NOT a runner-predictor (none exists). It builds a better UNIVERSE + accumulates a
  prospectively-powered out-of-sample sample (~400 fresh gapper-rows/month) to finally settle whether the
  5%-rare insider/coiled leads convert. Month-end: re-test; if they lift -> first prospectively-validated edge;
  if not -> selection closed for good.
- SCHEDULED: MomentumX-RocketWatchlist daily 19:15 (after DataIngest 17:30) via run_rocket_watchlist.ps1
  (--catchup + --report -> OPS_WATCHLIST Discord). NO capital, NO live-trading change.

## doc 254 -- 2026-06-04 -- Runner-vs-faller predictor hunt: NO positive predictor; leads too rare; the "veto" was a CURATION ARTIFACT (validation caught it). Info = watchlist, not selection.
- DISCRIMINATOR workflow (wf_d1811372-cc1, 20 fader forensic dossiers + synthesis vs the doc-253 rocket
  signature): the candidacy shell is STATISTICALLY IDENTICAL rocket-vs-fader -- theme 85/90%, dilution 80/95%
  (HEAVIER in faders), reverse-split 60/60%, foreign-issuer 60/55%. Conditional on candidacy these carry ~0
  discriminating info (confirms doc 253 from the other side).
- The 2 LEADS are real-in-SIGN but 5%-RARE in the winners -> tie-breakers, not predictors: insider-buy-cluster
  rocket 1/20 vs fader 0/20 (+3 faders showed net insider SELLING into the gap); coiled-catalyst ~1/20 vs 0/20.
  Social-pre-build 25% vs 0% (largest positive gap but 25% recall).
- The one "robust" finding (prior-runner FADE-VETO, faders 75% prior-runner) = a CURATION ARTIFACT. Validated on
  the UNFILTERED 13,511-gapper universe (prior_runner_veto_doc254.py): prior-runners ROCKET MORE not less (6.6%
  vs 2.4% close>=+30%, cross-regime), they're a VARIANCE indicator (more rockets AND worse mean -2.0 vs -0.9%).
  Only survivor: FRESH gappers ~1pp better MEAN (CI[+0.23,+1.92]) = marginal long-BASKET de-selection, but it
  would veto the rocket-richest population. The synthesis agent flagged this caveat itself; the re-test caught it.
- COILED computable (coiled_catalyst_backtest_doc254.py): extreme price-signature (>=20x vol + flat) = 2x
  forward-10d +30%-pop (15% vs 7.6%) but 2024-weak + fwd-max-HIGH upper bound -> a watchlist FLAG, not a trade.
- VERDICT: "information is the gap" = TRUE for universe construction (doc 253), FALSE for runner-prediction. No
  positive ex-ante predictor conditional on candidacy; magnitude irreducibly random. The morning Opus-agent
  pipeline is worth building as a WATCHLIST + forward DATA-COLLECTION engine (candidacy screen + prospective
  logging of the 5%-leads + outcomes), NOT a runner-predictor. NO live change.

## doc 253 -- 2026-06-04 -- Forensic deep-dive on 20 rockets: "information IS the gap" PARTIALLY VINDICATED (structural watchlist signature, not timing) + 2 testable new leads
- 20-agent forensic workflow (wf_849f8f32-244, 1.36M tokens, 525 web tool-uses): per-rocket EDGAR/news/social/
  float/sector dossiers on 2026's biggest rockets (TDIC+453%..PHGE+32%) + synthesis. Pierce's "information is the
  gap" hunt - the FIRST affirmative finding of the arc.
- REAL ex-ante STRUCTURAL-CANDIDACY signature the price/volume tests were blind to: micro-float 18/20 (<5M sh),
  theme-attached 17/20 (AI/quantum/drone/crypto/biotech), dilution overhang 16/20, recent reverse-split 12/20
  (8-9 within 90d = the float-compression engine), foreign-issuer 12/20, prior-runner ~8/20. Systematizable as a
  WATCHLIST (~tens of names/day), NOT a trade. ~14/20 had NO fresh launch-day catalyst (continuation/squeeze/
  catalyst-of-convenience). Ex-ante: candidacy ~20/20 observable, actionable-timing ~5/20, magnitude 0/20.
- TWO TESTABLE NEW LEADS (never tested, worth a real backtest): (1) ★ INSIDER open-market BUY CLUSTER on a thin/
  broken micro-float (VIDA: CEO+2 dirs+fund, T-2 lead, the ONLY smart-money tell in 20 cases); (2) ★ "COILED
  CATALYST" = a real verifiable catalyst absorbed on huge volume with NO price move (AIXI: 115x vol flat -> +515%
  6d later). Secondary: calendar-catalyst sizing on the filtered float; Stage-A as a variance/optionality screen.
- POISON PILL: dilution IS the distribution (16/20) - the rally is what gets sold into (ELAB -52% on ELOC, AIXI
  Streeterville fade, AKTX/AIM/CODX raised into the spike). Any systematic LONG must treat dilution as the
  dominant risk. VERDICT: "information is the gap" = a UNIVERSE-CONSTRUCTION thesis, not a market-timing thesis.
- Posted doc 253 to Discord OPS_ALERT (scripts/send_discord_file.py; Discord 403s default urllib UA - add
  User-Agent). NO live change.

## doc 252 -- 2026-06-04 -- Pre-gap anticipation: model predicts gaps (AUC 0.86) but the "edge" is an un-capturable TAIL ILLUSION (winsorized-negative); "different game" search exhausted
- PRE-GAP test (scripts/pregap_anticipation_doc252.py, 3.0M stock-days, predict gap_t+1 from prior-day features
  <=close_t, capture close_t->open_t+1, LORO): AUC 0.83-0.87 cross-regime, top-0.5% gap-hit 12.4% vs 0.49% base
  (25x). Top-0.5% overnight +0.82% CI[+0.49,+1.19]; SURVIVED dropping momentum features, liquid-only, 0.5% slip.
- KILL TEST (pregap_verify_doc252.py, winsorize/tail-concentration): top 10% of trades = 341% of P&L; winsorize
  @+20% -> -0.39% (NEGATIVE), @+10% -> -0.98%; non-gapping 88% lose -2.48% overnight; win 47%, median 0.00%, p5
  -13.8%. The predictable part = the UN-CAPTURABLE tail (monster gaps halt/slip/HTB/squeeze); capturable part is
  NEGATIVE. Same fat-right-tail lottery as the rockets, overnight form. FALSIFIED as a harvestable edge. Caught a
  +1% false lead on the LAST test - hostile-to-surprising-positives rigor paid off.
- "DIFFERENT GAME" SEARCH EXHAUSTED: selection (245-249) + execution (250) + regime-timing (250) + universe scan
  (251) + pre-gap (252) ALL reduce to the same un-harvestable lottery (predictable tail you can't capture;
  capturable part zero-to-negative; negative after costs). NO harvestable alpha in the low-float gapper universe.
- FORWARD: (a) fundamentally different strategy/universe/timeframe; (b) compete on friction only (operational);
  (c) accept + stop allocating research here. Durable: the research method + infra + a complete definitive negative.
  NO live change.

## doc 251 -- 2026-06-04 -- Universe gate scan: gap-ups systematically FADE across EVERY corner; no capturable long edge; short real but mostly unborrowable; the bot buys the fade
- SCAN (scripts/universe_gate_scan_doc251.py, 112,015 gap-up stock-days 2024-26, day_aggs, open->close basket
  mean by gap x price x ADV gate, day-block CIs): EVERY corner is negative-or-CI-spans-0 for the LONG. ZERO
  long +EV corners. The fade deepens with gap size, shrinks with price/liquidity. Most robust cross-regime
  signal in the project: GAP-UPS FADE.
- 3 CI-separated SHORT edges: 30%+/$5-20/ADV<10M -5.0%[-7.7,-2.4]; 8-15%/$0.5-2/ADV<10M -2.4%[-3.3,-1.4];
  3-5%/$0.5-2/ADV>10M -1.6%[-2.1,-1.0]. BUT mostly UNCAPTURABLE: biggest fades are HTB/no-locate (50-300%
  borrow) + catastrophic squeeze tail-risk (the rockets rip +100% against the short). Only the liquid corner
  ($50-200/ADV>10M, ~-0.5%) is cheaply shortable but thin/low-Sharpe. Market prices the fade into borrow+squeeze
  -> efficient, no free lunch either side at the open.
- EXPLAINS THE BLEED: buying gap-ups at the open = buying a systematic fade (confirmed independent of the
  selection 245-249 + execution 250 work). The bot's cumulative loss is STRUCTURAL. Live implication: long-the-
  open in any gapper corner is negative-EV.
- LEADS (thin): (a) liquid gap-up fade short (capturable but ~breakeven net); (b) PRE-GAP ANTICIPATION (predict
  the gap BEFORE it prints -> capture the 8%+ overnight move; the open is efficient because the gap already
  happened) - the higher-ceiling untested angle, next. NO live change.

## doc 250 -- 2026-06-04 -- Asymmetry-harvest FAILS: the 9:50 gapper universe is ZERO-EXPECTANCY (negative after costs); selection+execution+regime-timing all fail -> needs a different game
- BROAD-BASKET backtest (scripts/rocket_basket_exits_doc250.py, real intraday minute paths 9:50->16:00, all
  10,254 candidates equal-weight, 7 exit policies): basket mean ~0% GROSS under EVERY policy. Best cross-regime
  (pooled 2024+2025) = -0.04% 95%CI[-0.3,+0.2] (indistinguishable from 0). trail-15% is CI-separated NEGATIVE.
- ASYMMETRIC exit (cut -5% / ride trail-20) works on the DISTRIBUTION (rides rockets +30% mean, p95 +15%;
  truncates left tail, mean -0.31->-0.04) but win-rate 33%, median trade -5% -> the 2/3 stop-outs offset the rare
  winners. CANNOT manufacture expectancy from a zero-EV entry. doc 235's variance-not-expectancy, execution
  corollary: the asymmetric exit reshapes variance, doesn't create a positive mean.
- COSTS: universe mean eod -0.19%, median -0.8%, median entry $4.94 -> a ~1% small-cap round-trip spread/
  slippage swamps the ~0% gross basket -> clearly NEGATIVE after costs.
- REGIME-TIMING (#2) answered: only 2026 EOD-hold positive (+0.6-1.1% gross) = ~breakeven after costs, recent/
  thin -> not net-profitably timeable; it's what the doc-246 shadow already tracks.
- COMPLETE CLOSURE: selection (245-249) + execution-asymmetry (250) + regime-timing (250) ALL fail. The 9:50
  low-float-gapper-momentum game has NO extractable edge; oracle +40% is ex-ante unreachable AND not exit-
  harvestable -> universe efficiently priced at the open. STRATEGIC: the bot's core strategy is zero-EV /
  negative-after-costs; the bleed is STRUCTURAL not a bug. Edge needs a DIFFERENT game (universe/timeframe/
  signal) or compete on friction only. Durable assets: the research method + infra + a definitive negative. NO live change.

## doc 249 -- 2026-06-04 -- Data levers resolved (free-float BLOCKED, news NOT significant); ex-ante rocket SELECTION comprehensively closed; pivot selection -> execution
- FREE-FLOAT hard-blocked: Finnhub exposes no free-float (profile2 = current shareOutstanding only = doc 243's
  already-negative quantity + staleness; metric=all has zero share-supply fields). No point-in-time free-float
  source. The doc-247 synthesis's "Finnhub floatShares" was wrong for this plan.
- CATALYST/NEWS tested (build_news_features_doc249.py, Polygon /v2/reference/news, no LLM - Polygon ships
  sentiment + keyword catalyst-type): coverage 18.7% (rocket 24% vs fader 19%) -> ~76% of rockets are NO-NEWS
  squeezes. LORO top-5% GBM(25) vs GBM(25+news): 2024 -1.7->-1.5, 2025 +1.5->+2.5, 2026 +10.2->+9.7; AUC flat.
  Pooled 2024+2025 paired +1.3pp 95%CI [-0.8,+3.0] -> includes 0, NOT significant; 2024 still negative.
- COMPREHENSIVE CLOSURE: every selection lever now negative cross-regime (structure 235, catalyst-type 240,
  L2 244, float 243, 25-feat all model classes 245/248, raw tape 248, SOTA archs 247, free-float + news 249).
  Oracle +40% (rockets real) but ex-ante UNREACHABLE - the separating info isn't present at 9:50 in anything
  obtainable. Earned conclusion: rockets are predominantly no-news squeezes whose 9:50 signature is shared with
  violent faders; the right tail is ex-ante random conditional on observables.
- CONSTRUCTIVE PIVOT (selection -> EXECUTION): (1) harvest the +40% oracle ASYMMETRY selection-free - broad
  gapper basket + sharply asymmetric exits (cut faders fast, ride rockets; runner/chandelier, doc 178) can be
  positive-EV even with random selection; revisit exit research (docs 230-239) with the rocket-tail framing.
  (2) regime-timing via the doc-246 forward shadow (the one place signal survived = the current regime). (3)
  redirect to execution/exits/reliability + the live competition. AUC retired. NO live change.

## doc 248 -- 2026-06-04 -- Phase 0 + raw-tape Deep-Sets screen: architecture bet COMPREHENSIVELY falsified; tape improves AUC but DESTROYS realized return; pivot to DATA
- PHASE 0 (scripts/rocket_phase0_diagnostic_doc247.py, LORO top-5% realized EOD): the overfit-proof TabPFN-v2
  referee is the WORST in every regime incl 2026 (-6.1/-6.1/-2.6 vs GBM -1.7/+1.5/+10.2) -> capacity was never
  the constraint. kNN_AUC 0.66-0.77 (separability exists) but kNN top-slice negative (doesn't convert). Oracle
  +40% all regimes (rockets real, ex-ante unreachable from the 25 feats). Escalation condition NOT met.
- RAW-TAPE DEEP-SETS SCREEN (build_rocket_trade_cache_doc248.py cached 120.7M early trades; rocket_deepsets_
  screen_doc248.py): KILL CRITERION 2a met emphatically. DeepSets over the un-aggregated 9:30-9:50 trades
  IMPROVES AUC in all 3 regimes (2026 0.835->0.905!) but the top-5% realized return is WORSE everywhere; pooled
  2024+2025 paired DS-GBM = -6.6% CI[-10.0,-2.6] (CI-separated WORSE). Purest AUC!=P&L result in the project.
- MECHANISM (confirmed): the tape signature that best identifies rockets (violent block-print bursts+intensity)
  is ALSO the signature of the most violent FADERS (gap-and-crap). The top-5% slice is exactly where the
  lookalike faders concentrate; their -30%+ crashes dominate the mean. Better "rocket-like" ranking = worse
  traded slice. -> the full Set Transformer will NOT rescue it.
- UNIFIED VERDICT: ex-ante rocket SELECTION from 9:50 micro+tape does not generalize cross-regime. Bottleneck =
  the INFORMATION observable at 9:50 (shared between rockets and lookalike faders), not the model/aggregation.
  Right tail is ex-ante random conditional on what's visible at 9:50.
- PIVOT: only un-falsified levers are DATA (not architecture) - true FREE-float (Finnhub floatShares; the
  squeeze constraint) + catalyst/news CONTENT (Polygon /v2/reference/news + LLM tagger; why THIS stock,
  dilution risk). The whole architecture bet closed for ~1 afternoon + ~1 GPU-hr. AUC RETIRED. NO live change.

## doc 247 -- 2026-06-03 -- SOTA architecture sweep (8-agent workflow): honest verdict + cost-ordered decision protocol
- 8 Opus agents (6 dimension researchers -> lead-architect synthesis -> adversarial feasibility/EV). The lead
  agent READ THE REPO, verified ground truth live (baseline AUC 0.645/0.719/0.831 reproduced), and corrected
  3 dimension-report errors -- most important: the claimed "ENTRY off-by-one bug" is FALSE (ENTRY=3 = minute_idx
  20 = 9:50, verified on 10,552 ticker-days; do NOT change it).
- VERDICT (~65-70%): nothing ships, correct result. The cross-regime gate already FAILED (doc 245, CPCV+LORO);
  regime-dependence is most likely a DATA truth (marginal buyer changed 2024-25 -> 2026), not capacity. With 216
  rockets (2024=69/2025=117/2026=30) signal not capacity is the wall; a bigger model only fits 2026 harder.
- THE ONE ~25-30% SHOT: doc 245 falsified the 11 hand-aggregated tick SCALARS, NOT the raw asynchronous tape.
  A permutation-invariant Set Transformer / Deep-Sets over the raw opening-trade SET is the ONLY proposal that
  ingests un-falsified info. Everything on the 25 features (FT-T/SAINT/TabNet/TabM/TabR/PatchTST/iTransformer)
  is logically barred from a cross-regime win. Payoff-weighted PnL loss = explicit overfit trap. MoE regime-
  routing = 2026-overfit in architecture form.
- RECOMMENDED (if Phase 0+1 pass): RocketSetNet -- <=350k-param per-day-normalized Deep-Sets->Set-Transformer
  over raw trades + 25 aggregates, SSL-pretrained on 10,552 unlabeled days then frozen, ASL(g+=0,g-=4)+day-
  grouped LambdaRank loss under ASAM, seed-ensemble >=10.
- PRE-REGISTERED BLUEPRINT: 8-group regime-stratified CPCV (k=2, 28 splits, 7 paths as FRAGILITY not iid),
  purge on adv20 window + 21-day embargo; binding metric = top-5% realized EOD return per regime with day-block
  BCa bootstrap + paired-vs-GBM test; exact regularized LightGBM floor config; SPA campaign deflation. AUC RETIRED.
- PHASED PLAN w/ KILL CRITERIA (inverted - free experiment first): Phase 0 (1/2 day, no GPU) = oracle/kNN-purity
  + TabPFN-v2 overfit-proof referee (tabpfn 7.1.1 + ml_v6_*tabpfn* infra EXIST; prior work was the continuation
  label so rocket-binary is new) + order-flow proxy -> KILL 0 can close the whole arc. Phase 1 = LightGBM
  +LambdaRank+ASL LORO-tuned. Phase 2 = RocketSetNet (Deep-Sets mean-pool screen first). Phase 3 = SPA verdict.
- CHEAPEST FALSIFIER (afternoon): kNN rocket-purity + TabPFN referee under LORO. If 2024/25 purity ~ base-rate
  AND TabPFN = GBM negative -> no encoder fixes it; redirect to DATA (free-float, news content). NO live change.

## doc 246 -- 2026-06-03 -- Forward rocket PAPER SHADOW (the one honest continuation) + untested-lever register
- HARNESS (scripts/rocket_shadow_doc246.py): froze GBM(MICRO+TICK) on the doc-245 corpus (n=10,254, 202
  rockets, cutoff 2026-06-01). --run/--run-since gates the live universe, computes ex-ante-9:50 features the
  EXACT way the corpus did (reuses label_day + compute_tick_feats, correctly windowed via sip_timestamp -> no
  train/serve skew), scores, logs every candidate + realized EOD, flags FORWARD-OOS (date>cutoff). --report
  separates the honest forward track record from in-sample. NO capital, NO live change. Explicitly a REGIME BET.
- WHY: doc 245 closed BET#3 as cross-regime-robust, but 2026 (=now) genuinely works (+10.3% top-5%) and a
  model trained on 2024+2025 still ranked 2026 rockets (+10.2% LORO). Shadow tells us truthfully if "now" is
  tradeable before any capital.
- INITIAL: harness validated (in-sample 5-day top-5% +2.6%/40% top-1 hit). 06-01 in-sample AIM +68% ROCKET;
  06-02 FORWARD-OOS DBGI -34.5% (N=1 -> proves nothing; vivid variance reminder). Must run daily to accrue.
  Fixed a run_since date bug (str(date) carried ' 00:00:00' -> mislabeled the cutoff day as forward).
- UNTESTED-LEVER REGISTER (Pierce asked): only 2 feature classes NOT yet falsified -- (1) true FREE-float
  (Finnhub floatShares; doc 243 only killed shares-outstanding); (2) real catalyst/news CONTENT (Polygon
  /v2/reference/news + a working LLM tagger; doc 240 only killed catalyst-type homogeneity, news pipeline is
  blocked/timing-out). Both infra-blocked, both worth ONE pre-registered cross-regime test if unblocked.
- OFFERED (not done): a Windows scheduled task to run the shadow daily after the close (touches live host -> needs Pierce's go).

## doc 245 -- 2026-06-03 -- Cross-regime rocket money test FAILS pre-registered gate; doc-242 tape lift was a TIMEZONE-BUG artifact; BET#3 closes
- THE TEST (scripts/build_xregime_tick_features_doc245.py): extended doc-242 TICK tape to the full 2024-2026
  corpus (10,254 ticker-days, 202 rockets ~3x doc-242) via targeted /v3/trades API (no 1 TB flat-file pull;
  candidates+MICRO+label 100% local). Pre-registered: MICRO+TICK wins IFF top-5% mean EOD ret >0 in ALL
  2024/2025/2026 AND rescues the two negative regimes. RESULT = FAIL.
- top-5% mean EOD MICRO+TICK: 2024 -4.0% (CI[-6.8,-1.1]), 2025 -0.5%, 2026 +10.3%. 2024 WORSE (lift 0.9);
  only 2026 (already +, thinnest 28-rocket regime) amplified; median negative all 3 years. Robustness (LORO +
  slice sweep 1/2/5/10/20%) CONFIRMS: 2024 negative at EVERY slice; 2025 breakeven/neg-median; 2026 only.
- CRITICAL BUG caught by the API-vs-local sanity check: `trades_v1_parquet.ts_et` is UTC-mislabeled-as-ET
  (DuckDB AT TIME ZONE double-conversion in polygon_trades_v1_pull.py). doc242/244 read the "9:30-9:50 open"
  off it -> actually captured ~5:30am premarket (AAL: 1,102 vs correct 38,256 trades). doc-242's +0.05-0.07
  AUC lift was largely this artifact; correctly windowed it is +0.01. FIX: derive ET from raw sip_timestamp,
  NEVER ts_et. (Also fixed a bool(DataFrame) truthiness bug: coverage 70% -> 97.2%.)
- VERDICT: the ex-ante (9:50) microstructure+tape rocket signal is REGIME-DEPENDENT (real in 2026, absent
  2024, breakeven 2025) -> NOT a cross-regime-robust edge. The universe's right tail is largely ex-ante
  random across regimes. Consistent with 235 (structure homogeneity) + 240 (catalyst homogeneity) + 241.
- FORWARD (Pierce's call): (1) close BET#3 as a robust edge [recommended]; (2) the one honest exception = a
  forward PAPER SHADOW of the 2026/current-regime top-slice (regime bet, no capital, tracks OOS realized
  return live); (3) untested levers = true free-float (Finnhub) + real catalyst/news CONTENT (blocked); (4)
  model training moot (no cross-regime signal to amplify). NO live change.
- INFRA: reclaimed +450 GB (deleted verified-redundant raw trades_v1; parquet is faithful copy; imperfect
  last day backed up to D:); My Passport 4.6 TB mounted as D: (reusable warehouse/fallback). C: 254->704 GB.
- MEMORY: new [[trades-parquet-tzbug]] (ts_et corrupt; use sip_timestamp). 245 supersedes the doc-242 tape claim.

## doc 244 -- 2026-06-03 -- True L2 OFI does NOT beat tick-rule; L2+float investments KILLED; lever = cross-regime POWER
- THE true-OFI test (scripts/build_true_ofi_doc244.py, no disk -- Polygon quotes API): pulled 9:30-9:50
  NBBO for 333 ticker-days (73 +20% rockets + 260 controls), Lee-Ready aligned to the local trade tape,
  computed TRUE book-imbalance OFI + quoted spread + depth-at-touch. 96% coverage. RESULT: micro+tick
  (tick-rule) AUC 0.699 -> +TRUE-L2-OFI 0.692 (DROPS); lift -0.006 CI[-0.033,+0.021] NS. TRUE-OFI-only
  0.581. The two OFI measures DISAGREE in sign on rockets (tick_ofi -0.049 vs true_ofi +0.048) -> OFI is a
  weak/noisy rocket signal either way; the doc-242 tape lift comes from VOLUME/INTENSITY/large-prints not
  OFI. => **L2 quotes add NOTHING over the trades-only tape -> the 1-2TB L2 warehouse is NOT justified**
  (the no-disk API test paid for itself by killing the largest spend). doc-187's true-OFI>tick-rule thesis
  does NOT transfer to rocket detection here.
- TWO CLEAN NEGATIVES focus the program: L2 quotes (244) + float (243) don't help; ONLY the trade tape
  (on hand, 451GB) carries rocket signal. The lever is NOT richer features -- it's cross-regime POWER (more
  ROCKETS) to confirm doc-242's underpowered/within-window green shoot.
- DISK RECLAIM CONFIRMED: raw trades_v1 = CSV.gz, EXACT same coverage as the parquet (2025-08..2026-04) =
  redundant -> safely deletable for +451GB (re-derivable from Polygon S3), 254GB->~705GB free = enough for
  the cross-regime tick pull. (Pierce's call; or attach a drive.)
- REFOCUSED BET#3 PLAN: (1) reclaim 451GB or add drive; (2) pull cross-regime tick history (2024/2025-H1/
  2026-05+) -> 3-4x more rockets; (3) re-run doc-242 tape rocket-classifier PRE-REGISTERED cross-regime,
  decision = traded-slice return positive ALL regimes (not AUC). DROPPED: L2 quotes + float. Model = LAST
  step, post-data. NO live change.
- SYSTEM_MAP: strategy.md (L2/float killed; tape is the signal; cross-regime power is the lever), data.md
  (raw trades_v1 redundant w/ parquet = 451GB reclaimable; quotes API works but L2 adds no rocket lift).

## doc 243 -- 2026-06-03 -- BET#3 feature push: float redundant; L2/true-OFI path OPEN; data investment scoped
- FLOAT (scripts/add_float_features_doc243.py): ticker_details has shares-outstanding+market_cap (81% cov).
  Univariate rocket signature IS there (rockets ~half float 16M vs 31M shares, 4-5x premarket rotation) BUT
  float does NOT add to the model: FLOAT-only AUC 0.50, +float DROPS micro+tick 0.689->0.671 (lift ns).
  Redundant w/ tape volume features + shares-outstanding != free-float. => the TAPE is the signal; float
  proxy closed (negative). True free-float (Finnhub) low-priority revisit.
- L2/TRUE-OFI: doc-242 used tick-rule OFI (a PROXY); doc-187's validated signal is true book-imbalance OFI
  (Lee-Ready, needs quotes). **Polygon quotes API is REACHABLE** -> true OFI buildable via TARGETED API
  pulls (early-window NBBO for rocket+control sample), NOT gated by the disk wall. = the highest-EV next
  feature. RECOMMENDED NEXT BUILD: true-OFI-via-API sample test vs tick-rule OFI.
- DISK CONSTRAINT (the honest blocker on "all three"): 254GB free; full extra tick history (~760GB) +
  full L2 quotes flat-files (~1-2TB) DON'T FIT. Paths: provision disk, or targeted API sampling (works now
  for the L2 proof), or prune. STATUS: float=done(neg); L2-OFI=path OPEN via API(build next); more-tick-
  history=disk-blocked; catalyst=blocked(news+Together-403 LLM).
- MODEL = PREMATURE: with 34-73 rockets/9mo a high-capacity model OVERFITS; data is the bottleneck not
  capacity. GBM right now. The week-long checkpoint-validated SOTA model is the LAST step, AFTER cross-
  regime data + true-OFI/float/catalyst features. Bar unchanged: traded-slice return positive ALL regimes (not AUC).
- SEQUENCED PLAN: (1) true-OFI-via-API sample test (cheapest proof, no disk); (2) provision disk + cross-
  regime tick pull -> powered pre-registered cross-regime test; (3) free-float + catalyst; (4) THEN bigger
  model w/ checkpoint validation. NO live change.
- SYSTEM_MAP: strategy.md (float closed-negative; L2/true-OFI is the next lever + accessible; model
  sequencing), data.md (disk wall on full pulls; quotes API as the targeted path).

## doc 242 -- 2026-06-03 -- BET#3 step1: the TAPE adds rocket signal (first feature improvement that clears significance)
- AUDIT: trades_v1 = 451GB raw tape, partitioned year/month/day/ticker -> per-ticker-day partition reads
  (no scan), ~93% coverage. TRADES-ONLY (no quotes) -> OFI via tick-rule. Coverage 2025-08..2026-04 (within-
  window only). Built ex-ante tick-feature layer (scripts/build_tick_features_doc242.py): premarket+9:30-9:50
  tick-rule OFI (full+late), premarket vol/$vol, large-print(block) ratio, trade intensity, mean size,
  tick-VWAP-dist, odd-lot ratio -> data/research/rocket_tick_features.parquet (1,783 ticker-days w/ >=3 early
  trades; ~49% dropped = illiquid <3-trade names = liquidity skew toward tradeable).
- RESULT (rocket-classifier GBM class-balanced, CPCV OOS within-window, bootstrap CI on AUC lift): tick adds
  discrimination -- micro+TICK AUC 0.68-0.69 vs micro-only 0.60-0.64; lift +0.05 to +0.07; **CI EXCLUDES 0 at
  eod>=20% (73 rockets, [+0.002,+0.104]) and eod>=30% (34, [+0.007,+0.137])**, borderline at the strict
  sustain cut ([-0.005,+0.136]). **TICK-only (0.636) > OHLCV-micro-only (0.598)** -- the tape carries MORE
  rocket signal than 5-min OHLCV. Top-2% precision 0%->11% (~=12% break-even bar), top-slice ret -2.5%->+3.1%
  (noisy ~4 rockets/slice). FIRST time in the arc that adding features improved discrimination AND cleared
  significance -> the doc-241/187 hypothesis (rocket-vs-fader signal lives in the tape) is SUPPORTED.
- LIMITS: underpowered + within-window (9mo, 34-73 rockets, borderline at strict label); tick-rule OFI is a
  PROXY for true book-imbalance OFI (needs L2 quotes, likely stronger); AUC != P&L (top-slice median still
  ~0; traded-slice-return-positive-cross-regime is the binding gate, unproven). Green shoot, NOT a confirmed edge.
- JUSTIFIED INVESTMENT (now evidence-backed): (1) acquire MORE tick history (2024/2025-H1/2026-05+) -> power +
  cross-regime; (2) L2 QUOTE data -> true OFI/bid-wall/VWAP-reclaim (highest-EV add); (3) float + float-
  rotation + kappa-catalyst. Then pre-registered cross-regime rocket-classifier, decision = traded-slice
  return positive ALL regimes. First result that EARNS the next investment instead of closing. NO live change.
- SYSTEM_MAP: strategy.md (the tape adds rocket signal; BET#3 data-acquisition roadmap), data.md (trades_v1
  451GB year/month/day/ticker, coverage 2025-08..2026-04, trades-only).

## doc 241 -- 2026-06-03 -- ROCKET DETECTION (the reframe): the tail IS rankable ex-ante; feature-gated; FIRST real signal
- Pierce's reframe: stop trading the homogeneous mean; identify ONLY the ~1/day-to-week ROCKET (explodes
  +stays). A rare-event PRECISION problem. Rockets confirmed real+recurring+cross-regime: EOD-from-9:50
  median -0.8% but fat tail (p99 +49%, max +453%); rocket (EOD>=+30% & sustained close>=50% of run-high) =
  2.05% base rate (~1/3.5 days), present all 3 years (69/117/30).
- GATING RESULT: the EXTREME TAIL is partially rankable EX-ANTE (unlike the mean) -- rocket-classifier
  (GBM class-balanced, microstructure@9:50, CPCV OOS) AUC 0.645/0.719/0.831 (24/25/26), pooled 0.711, lift
  up to 6.6x. The KS-on-bulk homogeneity tests (235/240) MISSED this thin-tail signal. The reframe found
  signal where the aggregate mean had none.
- BUT NOT YET TRADEABLE: realized FORWARD return of the top-scored slice is NEGATIVE in 2024+2025, positive
  only 2026; the top slice MEDIAN is -5% to -7% EVERY year (loser-dominated; rockets still only 6-12% of the
  slice). At 9:50 with 5-min OHLCV a rocket and a pump-that-fades look identical -> classifier catches both.
  Quantified bar: traded slice breaks even at ~12% rocket-precision (rockets ~+50% vs losers ~-7%); current
  features hit ~12% only in 2026's top-1-2%, 2024-25 sit at 3-6% -> precision must ~DOUBLE.
- WHY HOPEFUL + WHERE TO INVEST: limitation is PRECISION = a FEATURE problem (ACQUIRABLE), not absence of
  signal. The rocket-vs-fader discriminator is exactly the doc-187-validated microstructure we LACK: tick/L2
  OFI + bid-wall + tape (accumulation vs distribution) + VWAP reclaim-vs-reject; float/rotation; premarket
  structure; kappa-validated catalyst quality. THE ENGINEERING BET = acquire these ex-ante features (Polygon
  trades+quotes, float, premarket) + rebuild the rocket-classifier, pre-registered cross-regime; decision
  metric = realized slice return positive in ALL regimes (NOT AUC -- AUC was real here and still lost money).
- VERDICT: reframe SURVIVES the first gate (tail rankable cross-regime -- FIRST positive of the session);
  NOT tradeable yet; path is data-acquisition not model-tuning. This is BET#3 (rocket detection). NO live change.
- SYSTEM_MAP: strategy.md (the rocket/tail reframe + the ~12% precision bar + the feature-acquisition program).

## doc 240 -- 2026-06-03 -- BET#2 (catalyst-archetype playbooks): FAILS Gate 3 (separation); closes
- BET#2 = {pattern->playbook} store + LLM tagger. Gates: kappa>=0.6, N>=30/archetype/yr, KS-separation.
  TWO BLOCKERS found: production LLM tagger BLOCKED (direct api.together.xyz 403 on every model from this
  shell; bot uses its own litellm path) + cross-regime news not on disk (only 2026 journals have headlines).
  MY CALL: ran the cheapest DECISIVE gate first -- Gate 3 (separation) -- on 2026 data w/ a reproducible
  RULE-BASED tagger (if archetypes don't separate, tagger-kappa is moot). scripts/bet2_archetype_separation_doc240.py.
- GATE 3 FAILS: of the 5 archetypes with N>=30 (EARNINGS/GENERAL_PROMO/MA_DEAL/NO_CATALYST/OTHER), NONE
  separates from the base gapper 30-min return distribution (KS p=0.35-0.71, all >> Bonferroni a=0.01); no
  pairwise separation. Per the gate, the playbooks = the base mislabeled. BET#2 fails on available data.
- HONEST SCOPE (anti-asymmetric): rule-based tagger noise blurs archetypes toward base (why kappa is
  supposed to be first) -> null is SUGGESTIVE not dispositive; BUT the tagger surfaces FDA/clinical as
  nominally higher so it's not pure noise. The ONE un-foreclosed sliver = biotech catalysts (FDA +5.4%/
  CLINICAL +3.6% nominal mean) -- but both FAIL Gate 2 (N=6,18<<30), untestable here; need cross-regime
  news fetch + kappa-validated LLM tagger + would STILL face cross-regime CPCV (strong prior: fail).
- VERDICT: BET#2 CLOSES (gates not cleared; not relaxed). WHERE THE PROGRAM LANDS: catalyst archetypes
  don't separate (240) + structural segments homogeneous (235) => the low-float gapper universe is
  homogeneous across BOTH structure AND catalyst -> NO extractable sub-population edge for us. Honest fork:
  (b) CHANGE THE SELECTION UNIVERSE (different float/liquidity/catalyst-quality regime where sub-pops
  separate) or treat the system as the hardened paper research instrument it now is. Durable confirmed win
  = execution INTEGRITY (phantom killed + regression-locked) = honest P&L = precondition for any edge.
- SYSTEM_MAP: strategy.md (BET#2 closed; gapper universe homogeneous on structure+catalyst; next = new
  universe or paper-instrument). NO live change.

## doc 239 -- 2026-06-03 -- c2 loss-tail cap: FAILS cross-regime. Structural-lever program exhausted.
- Pre-registered loss-cap sweep on the doc-235 corpus (entry pt#3, hold EOD): does a hard stop improve the
  MEAN (not just WL) cross-regime? FAILS: no stop level improves the mean in all 3 years -- significant
  only in 2025, includes 0 in 2024, NEGATIVE in 2026 (stops HURT the trendy regime where dips recover; with
  0.5% slippage 2026 turns sig-negative). The WL "improvement" (3% stop -> WL ~3.0 vs 1.2) is a RED HERRING
  -- offset by a lower win rate (stops out recoverers), mean stays flat (same trap as b2). Loss-cap = a
  variance/WL lever, NOT a sign-flipper, regime-dependent. scripts/loss_cap_experiment_doc239.py.
- SYNTHESIS: NO structural lever creates a profitable base (selection DEAD, overlay NO-OP, phantom FIXED,
  entry NULL, EOD integrity-only, loss-cap FAILS). The aggregate low-float gapper universe is structurally
  ~break-even-to-losing; every lever only reshapes variance, none moves the sign durably. Two honest paths:
  (a) BET#2 -- test whether a SUB-POPULATION (catalyst archetype) has edge the aggregate hides; (b) if that
  also fails, change the universe or treat the system as a hardened paper research instrument. Proceeding to
  BET#2 with the explicit prior that most archetypes fail the same gauntlet. NO live change.
- SYSTEM_MAP: strategy.md (structural-lever program exhausted; aggregate universe has no extractable edge;
  next test = sub-population archetypes).

## doc 238 -- 2026-06-03 -- structural-lever audit (c2/c3/c4): no cheap lever creates a profitable base
- Post-BET#1-closure, audited the structural levers under Pierce's frame (base is LOSING -$8K/15 sessions;
  a lever must FLIP THE SIGN, not just cut loss-variance). Plus: regression tests for the 2 CRITICAL
  phantom guards (tests/unit/test_phantom_close_guards_doc229.py, 4 pass: behavioral primitive via the
  Adversary + source tripwires on the inline main.py guards). Reporting-fix #2 was ALREADY shipped (D238,
  5/12, eod_failsafes.py:314 -- the spawned chip is redundant).
- c4 EOD-flatten: ~11/26 sessions open-at-force-close, qty_drift 10/26 = the 226-229 phantom class ->
  INTEGRITY lever (makes P&L honest), NOT base-creating. (open@recon field unpopulated -> clean carry
  count not recoverable.)
- c3 entry-timing: 30 real trades, lag median 14min, corr(lag,pnl)=+0.05 / corr(lag,win)=-0.03 ~ZERO;
  terciles non-monotonic (SLOW nominally best) -> NULL; entry-timing is NOT the lever the doc-230
  SIMULATION hoped (that was optimal-vs-actual, not lag-vs-P&L). n=30 caveat.
- c2 loss-tail cap: the ONE remaining lever with a MECHANISM to flip the sign (not a b2-style no-op) --
  capping catastrophic non-recovering losers (LIDR -$6K / ASNS / CANF class) raises W/L + mean; at 30%
  win, W/L 1.0->2.3 flips expectancy. UNTESTED -> next experiment (reconstruct per-trade MAE from minute
  bars, sweep stop levels, find one that cuts worst-N% while preserving the rest, cross-regime + CI).
- SYNTHESIS (arc 231-238): selection alpha DEAD (235), overlay NO-OP (236), leak was phantom & FIXED (237),
  entry/EOD null (238); base is structurally ~break-even-to-losing on the low-float gapper universe. Two
  honest paths: (a) the c2 loss-cap experiment (last sign-flipping candidate, doc-235 rigor); (b) if it
  fails, change the selection universe or treat the system as a hardened paper research instrument. The
  CONFIRMED session win = execution INTEGRITY (phantom killed, +$44K/yr distortion removed, regression-
  tested) -- makes P&L honest = precondition for any real edge. NO live change.
- SYSTEM_MAP: strategy.md (no base-creating lever found; c2 is the last candidate), known_bugs.md (phantom
  guards regression-locked).

## doc 237 -- 2026-06-02 -- phantom-P&L audit: the leak IS the qty-drift class (+33%, already fixed by 226-229)
- Reconciled recorded (journal_total_pnl) vs realized (broker_truth_recon = bot's EOD Alpaca pull) across
  26 EOD reports. STAGE-1 data-integrity gate: (a) pre-5/12 broker_total_pnl is a STATIC ~+$41,256 repeated
  identically = cumulative/cached REPORTING BUG, not daily realized -> full-YTD recon impossible, clean
  window = 5/12-6/02 (15 sessions); (b) NO per-trade fill files exist (trade_journal/executed = 0) -> per-
  trade slippage/fee decomposition not assemblable + moot on PAPER (idealized fills).
- CLEAN-WINDOW LEAK: recorded OVERSTATES realized by +$2,631 (mean +$175/session) = +33% of |realized|
  (-$7,977) -- ABOVE the 20% bar. 81% of the leak ($2,143) concentrates on QTY-DRIFT days = the phantom/
  ghost-MTM class (close 403s on reserved qty / multi-buy mis-track book P&L the broker never realized;
  also poisoned BOCPD/Kelly mu_edge). Annualized ~+$44K/yr if unfixed -- dwarfs actual realized (~-$8K)
  -> phantom leak was the single largest distortion of perceived edge.
- VERDICT: the mandate's "largest concrete improvement" = the phantom/qty-drift class, and it was ALREADY
  SHIPPED this session (D227 merge, D229 2 CRITICAL close guards, D218 reconcile). Audit = confirmation +
  quantification. FORWARD KPI: post-deploy, drift-day journal-vs-broker leak should collapse toward $0;
  any residual gap on a drift day = a missed phantom guard.
- RANKED FIXES: #1 phantom guards (SHIPPED 226-229); #2 fix broker_total_pnl reporting field (daily not
  cumulative) [spawned]; #3 persist per-trade fills (executed_*.jsonl) to unlock per-trade audit + the
  doc-236 b2-overlay live-tape backtest; #4 market-impact fill model (live-cutover only, ~0 on paper).
- SYSTEM_MAP: known_bugs.md (broker_total_pnl cumulative-vs-daily reporting bug), research.md (phantom leak
  quantified at +33%, confirms 226-229 was highest-ROI). NO live change (the fixes that matter already shipped).

## doc 236 -- 2026-06-02 -- BET#1 closure test: two sign-decoupled model-as-tool reframes (PRE-REGISTERED)
- doc-235 killed P(continue) as selection alpha (regime-unstable SIGN). Per Pierce: test only the two
  SIGN-DECOUPLED reframes (the literal "veto low-P faders" risk-filter is logically broken by doc 235).
  Pre-registered (committed BEFORE results), doc-235 corpus as-is, Mode-B CPCV-refit OOS only, Bonferroni.
- (b1) MODEL AS VARIANCE PREDICTOR: |P-0.5| predicts realized 30-min volatility -> usable for sizing.
  SURVIVES iff CI-sig + positive Spearman in ALL of 2024/2025/2026/pooled (Bonferroni a=0.0125).
- (b2) DIRECTION-AGNOSTIC TAIL FILTER: some P-threshold cuts realized P5 loss without proportionally
  killing the mean, in ALL 3 years. SURVIVES iff >=1 (threshold,direction) has CI-sig P5-improvement +
  tail-cut > mean-lost in all 3 years (Bonferroni across thresholds x directions x years).
- DECISION: b1 survives -> variance-sizing input (promote); b2 survives -> risk overlay (promote);
  BOTH FAIL -> BET#1 CLOSES FULLY, bandwidth -> structural levers (phantom-P&L audit doc 237). No 3rd reframe.
- scripts/reframe_audit_doc236.py. RESULTS (run 23:33): b1 FAILS as specified (Spearman(|P-0.5|,realized
  vol) is significantly NEGATIVE/INVERSE in all 3 years -0.42/-0.41/-0.47 -- confident=CALM not volatile;
  per hard-rules NOT flipped). b2 SURVIVES (keep-below P<0.3-0.4 cuts P5 loss tail +3 to +7.5% in ALL 3
  years, Bonferroni a=0.0009, mean retained). CHARACTERIZED: it ~halves the LEFT tail (P5 -10.4%->-5.7%
  2024) but ~symmetrically cuts the RIGHT tail (P95 +9.9%->+6.3%) => fundamentally VARIANCE REDUCTION, not
  tail-asymmetry; mean retained (small, rides on doc-235 high-P-underperforms). VERDICT: BET#1 does NOT
  fully close -- the model's ONLY cross-regime-STABLE use is variance/loss-tail CONTROL (b1-inverse = b2 =
  same signal), dead for direction (doc 235), alive as a risk OVERLAY (not alpha). This is exactly
  doc-230's wound (catastrophic loss tail). PROMOTE: flag keep-below P~0.3 as a candidate variance/tail
  overlay; backtest on the ACTUAL live-trade tape (which doc 237 assembles). Caveats: mechanical variance
  reduction; overlay not alpha; needs a profitable base + live-tape gate. NO live change.
- ADDENDUM (Pierce's no-op sanity check): b2 CLOSES. Sharpe(b2-kept) vs Sharpe(full-pool) diff CI INCLUDES
  0 in ALL 3 years + pooled (2024 +2.00[-0.79,+5.04], 2025 +0.56[-2.23,+3.44], 2026 +0.16[-6.58,+5.99],
  pool +1.26[-0.68,+3.19]). A flat size cut leaves Sharpe invariant -> b2 is statistically INDISTINGUISHABLE
  from trading smaller = a NO-OP. The doc-236 "survival" was tail-symmetric variance reduction; mean-
  retention not Sharpe-significant + rides on the dead direction signal. => BET#1 has ZERO deployable use;
  model is a research artifact. FRAMING (Pierce): the honest base is a LOSING strategy (-$8K/15 sessions
  ~-134%/yr, pool Sharpe -1.88/-0.77 in 2024-25); phantom fix removed inflation, did NOT create edge; every
  remaining lever is a MULTIPLIER on a profitable base we don't have. Next real question: is there ANY
  profitable base + what creates one (not "what multiplier").

## doc 235 -- 2026-06-02 -- cross-regime test of the doc-234 edge: PRE-REGISTERED (Stage 0+1 committed first)
- The gating experiment: does the doc-234 selection edge survive OUT-OF-PERIOD (2024-25) or is it a
  May-2026 seasonal artifact? Pre-registered per Pierce's mandate: dials locked, decision rules
  pre-specified, NO tuning on new data, Bonferroni-corrected, two generalization modes.
- STAGE 0 (data integrity) CLEARED: survivorship PASS (point-in-time -- 23% of 2024 tickers gone by 2026,
  MULN delisting retained); corporate actions PASS (small-cap splits covered; intraday labels split-safe
  by construction); minute coverage PASS (2024 366M + 2025 427M bars). Eligible pool under the locked
  ex-ante gate (gap>=8%, open $0.50-$20, trailing-20d ADV>=$1M): 2024=3,776 / 2025=5,595 / 2026=1,712
  ticker-days (~9.4k OOP -> CIs ~3.8x tighter than 2026's 35 sessions).
- STAGE 1 PRE-REGISTRATION (committed 2026-06-02 23:10 EDT BEFORE any Mode-A/B result existed): locked
  gate (RVOL replaced by no-look-ahead trailing-ADV; deviation documented; 2026 REBUILT under same gate
  for apples-to-apples), microstructure-ONLY 13-feature set (dropped live-eval gap/rvol/mfcs), dials
  (top-1/top-2, 30-min PRIMARY + EOD secondary, ADV>=$1M nominal+percentile, +40% cap, 2x slip, $1 floor,
  warrants out, 1000-seed + broad-universe null, 10k paired bootstrap). PRIMARY ENDPOINT = pooled 2024+25
  top-1/2 @ ADV>=$1M @ 30-min, Bonferroni-corrected. Decision rules SURVIVES/SEASONAL/AMBIGUOUS verbatim.
  Modes: A frozen-2026 transfer, B CPCV-refit across 2024-26. Build script scripts/build_cross_regime_corpus.py.
- STAGES 2-5 RESULTS (run 2026-06-02 23:18, scripts/cross_regime_audit_doc235.py on 10,786 ticker-days):
  VERDICT = **SEASONAL/ARTIFACT, and stronger -- the edge REVERSES SIGN out-of-period.** Micro-only 2026
  CPCV AUC 0.839 (HIGHER than full-feature 0.768 -> model NOT crippled; prediction is genuinely good).
  Yet top-P(continue) selection SIGNIFICANTLY UNDERPERFORMS random in BOTH 2024 AND 2025, BOTH modes
  (frozen + CPCV-refit), every concentration + ADV level, Bonferroni-corrected: pooled top-2 Mode A
  -1.13% CI[-1.70,-0.56], Mode B -1.25% CI[-1.82,-0.66]; 2024 -1.2/-1.5%, 2025 -1.0/-1.0% all excl 0
  NEGATIVE; months-positive 21%/8% (< 30% floor); EOD -4.3/-4.6%. 2026 clean-gate ref itself nominally
  negative (n=48) -> doc-234's positive was partly a LIVE-JOURNAL-POOL artifact, not just calendar.
  True-null broad market flat (-0.06%). DEEP FINDING: a 0.84-AUC continuation predictor yields a
  significant NEGATIVE selection edge OOP -- the strongest form of AUC!=P&L (the predictable continuers
  are exhausted names that fade). DECISION: P(continue)-as-selection-alpha is KILLED out-of-period
  (pre-registered, CI-significant, sign-reversed). Not a deployable regime-gated edge (the "good" regime
  is only ex-post labelable). Risk-filter reframe remains a SEPARATE open question (loss-tail, not
  selection), now lower-priority + same-rigor-required. Durable levers unchanged (execution integrity,
  trade early, flatten at close, cut losers). NO live change. SYSTEM_MAP: research.md (pre-registration
  protocol; selection-edge killed OOP; AUC-can-be-anti-profit), strategy.md (no durable selection alpha).

## doc 234 -- 2026-06-02 -- hostile audit of the doc-233 NEGATIVE: it was OVER-KILLED, a small real edge survives
- Pierce: "prove or break the doc-233 negative under the same rigor; asymmetric skepticism is the bug."
  8 experiments, CIs everywhere (scripts/adversarial_audit_doc234.py). VERDICT: doc-233 negative is
  OVERTURNED as stated -- not no-edge, but a SMALL real edge masked by 4 stacked conservative choices.
- EXP1 bootstrap: at doc-233 dials edge +1.28% CI[-0.21,+2.85] (in-noise) BUT signal total +77% = 90th
  pctile of 1000-seed random (median +20%). doc-233's "random beats signal (+79 vs +67)" was ONE lucky
  arbitrary draw, NOT a finding -> COLLAPSES.
- EXP2 horizon: at the model's TRAINED 30-min horizon edge +0.98% CI[+0.09,+1.91] EXCLUDES 0; EOD
  (untested extrapolation) noisier -> doc-233 partly a horizon artifact.
- EXP3 sweeps: slippage-INSENSITIVE (1x->5x flat; "2x brutal" was a red herring); edge tail-influenced
  (significant at cap>=+100%); ADV floor [NEW] ADV>=$0.5-10M -> edge +1.5-2.2% CI EXCLUDES 0 (e.g.
  $1M: +2.12% CI[+0.52,+3.82]). Tradeability STRENGTHENS the signal, opposite of doc-233.
- EXP4 true-null: eval-pool +1.19% vs broad sub-$50 universe +0.12% -> the SCANNER selection is itself a
  ~+1% edge; doc-233 "random" inherited it (not a true null).
- EXP5 regime: significant only in May (+2.43% CI[+0.23,+4.90]); Feb/Apr in-noise -> edge so far
  May-concentrated, generalization unproven (the strongest SURVIVING caveat).
- EXP6 concentration: top-1 +7.72% CI[+0.85,+14.21], top-2 +4.84% CI[+1.10,+8.64] EXCLUDE 0; top-8
  dilutes to noise -> doc-233's top-8 basket buried the edge.
- EXP7 Wilcoxon top-8 p=0.126 (ns, consistent). EXP8 leakage: corr(P, prior-15m ret)=0.06 -> NOT lagged
  momentum, NOT tautological; P stable across bars (autocorr 0.8-0.9). Purge/embargo clean.
- HONEST STATE: small real leakage-free edge at trained-horizon + tradeable-universe + high-concentration;
  slippage-robust; tail-influenced + May-concentrated -> NOT deploy-now, but NOT the dead-end doc-233
  declared. NEXT (research only): more regimes (2024-25 corpus), market-impact fill model, benchmark vs
  ACTUAL live policy, pursue top-1/2 + ADV>=$1M + 30-min form under policy CPCV/PBO; risk-filter reframe
  now COMPLEMENTARY not a replacement. NO live change.
- SYSTEM_MAP: research.md (asymmetric-skepticism lesson; the edge survives symmetric rigor at the trained
  horizon/tradeable-universe/high-concentration), strategy.md (selection edge revived, generalization is
  the gate).

## doc 233 -- 2026-06-02 -- realism pass: BET#1 profit edge is a MIRAGE (prediction real, alpha not)
- "let's see if it holds up." It does NOT. The frictionless conviction portfolio (scripts/
  backtest_conviction_portfolio.py) looked spectacular (+931%/Sharpe 7.28) but is FAKE: the no-signal
  RANDOM baseline scored Sharpe 9.68 (higher than the signal) -> measuring an artifact, not skill.
- ROOT CAUSE (3 artifacts): (1) untradeable tail -- top-10 of 885 trades = 40% of P&L, they're
  WARRANTS (PIIIW/TALKW/USGOW) + penny rockets (WOK +540%, TDIC +453%) unfillable at size; (2)
  frictionless fills (can't dump 8 thin smallcaps at the close price); (3) hot regime (May 2026 mean
  +8.7%, 514/885 days). Raw hold_ret mean +6.7% but MEDIAN +0.9%; winsor@+20% collapses mean to +1.3%.
- BRUTAL-REALISM FLOOR (warrants out, +40% upside cap, 2x slippage, $1 floor): per-trade median net
  = -1.60% (typical tradeable trade LOSES); portfolio P(continue) +67% does NOT beat random +79%
  (MFCS +66%). Within 38-session noise => P(continue) shows NO reliable deployable edge over random
  net of costs.
- WHAT SURVIVES: P(continue) AUC 0.768 (CPCV) is a REAL PREDICTION, but a prediction != deployable
  alpha. A 0.768 AUC makes no money because the profitable continuations are the untradeable tail;
  among tradeable names the edge < round-trip cost. = the "IC detectable but worthless after spreads"
  failure mode the doc-231 research warned about (Kinlay/Kronos).
- VERDICT: do NOT deploy. NO live change. Bar to revisit (all required): real market-impact fill model
  (per-name $-volume), tradeable-universe only (no warrants/borrow/halt), benchmark vs ACTUAL live
  policy same-days, out-of-regime + policy-level CPCV/PBO; most promising reframe = model as a RISK
  FILTER vetoing predicted-fade catastrophic losers (attacks the loss tail, doc 230), not an alpha source.
- SESSION PATTERN CONFIRMED: every apparent edge (MFCS=variance, multi-day skyrocket=split artifacts,
  conviction portfolio=tail+frictionless+regime) evaporates under scrutiny; durable levers stay
  structural (execution integrity, trade early, flatten at close, cut losers -- doc 230).
- NEW: scripts/backtest_conviction_portfolio.py. SYSTEM_MAP: strategy.md (conviction alpha killed under
  realism; risk-filter reframe is the open lead), research.md (AUC!=deployable-alpha; spread-haircut lesson).

## doc 232 -- 2026-06-02 -- AUC->dollars gate: exit-trigger FAILS, conviction-filter WORKS (BET#1 pivot)
- THE decisive policy backtest (scripts/backtest_exit_policy.py, leakage-free: entry held constant,
  P(continue) from OUT-OF-FOLD purged K-fold preds, model = abstaining overlay on the trail).
- EXIT-TRIGGER FAILS: ranking HOLD-to-EOD (mean +9.6%, W/L 2.60) > TRAIL-only 20% (+6.9%, 2.49) >
  every MODEL overlay (+4.2% to +6.1%), on mean AND median. Early exits CAP the right-tail winners
  (avg win +25%) more than they save on losers. AUC != P&L again. Objective mismatch: a 30-min +-5%
  continuation label optimizes the wrong thing for total return.
- CONVICTION-FILTER WORKS: hold-to-EOD by EARLY P(continue) tercile is MONOTONIC — low +4.7% / mid
  +9.5% / high +14.7% mean (top-quartile +16.9% = 1.75x all). BEATS MFCS, which is NON-monotonic
  (low bucket +12.8% > high +9.0%) -> P(continue)-as-conviction > MFCS-as-conviction.
- PIVOT: P(continue) is a calibrated SELECTION/SIZING signal (as the doc-231 research said), NOT an
  exit trigger. Strategy hypothesis to paper-test (inverts live behavior): enter EARLY -> size by
  P(continue) -> HOLD to EOD -> FLATTEN at close (never overnight, per doc 230). Dominant lever stays
  HOLD-to-close, not smarter exits.
- CAVEATS: frictionless/idealized (enter-all-893 @9:35, equal-weight, no slippage) -> absolute +9.6%/
  +14.7% are upper bounds; the RELATIVE rankings are the robust signal. Tail-driven means (medians
  modest). Same-day only (reconciles 230: intraday-hold good, overnight bad). Needs realism + forward.
- NEXT GATES: (1) realism pass (spread/slippage + top-N capital cap -> deployable return); (2) selection
  backtest vs actual live policy; (3) shadow paper-trade if it survives, CPCV/PBO-gated.
- NEW: scripts/backtest_exit_policy.py. SYSTEM_MAP: strategy.md (the conviction-sizing + hold-to-close
  hypothesis; exit-trigger killed), research.md (AUC!=P&L confirmed, calibrated-prob->sizing validated).

## doc 231 -- 2026-06-02 -- BET#1 done right: unified intraday-exit architecture (SOTA research + CPCV-validated)
- DEEP RESEARCH (workflow wf_d510a2fe-9b6, 111 agents/3.5M tok, adversarial: 132 claims->25 tested->21
  confirmed/4 killed) mapped SOTA AI for intraday continuation-vs-fade exit. VERDICT (anti-hype):
  * TIER 1 (build): overfitting-aware validation (PBO/CPCV + Lopez de Prado triple-barrier/meta-labeling),
    GRADIENT BOOSTING (recommended under concept-shift+imbalance = our regime), microstructure features,
    and CALIBRATED-PROBABILITY + ABSTENTION + sizing as the decision frame (edge is risk-mgmt not point accuracy).
  * TIER 2 (narrow): domain-native TSFM (Kronos), deep-RL EXECUTION for the liquidation leg only, TabPFN
    as a FEATURE generator only (KILLED 0-3 vs GBM; can't forecast new highs; 30x slower).
  * HYPE/avoid: generic zero-shot TSFM (TimesFM/Chronos) as predictors. UNPROVEN/no surviving evidence
    for exit DECISIONS: diffusion, 2-bit/BitNet, Meta memory-RAG (Pierce's wildcards -> parked as research bets).
- VALIDATION: per the research's #1 mandate (single-split walk-forward => false positives), re-tested the
  exit classifier with COMBINATORIAL PURGED CV (scripts/validate_exit_classifier_cpcv.py, day-grouped,
  1-day embargo, C(6,2)=15 paths): AUC 0.768 +/- 0.014, floor 0.742, 100% paths >0.65, top-decile lift
  2.10x. ROBUST -- survives the false-positive killer. First edge all session to STRENGTHEN under max rigor.
- ARCHITECTURE (doc 231 Mermaid): feature-extractor -> meta-labeled GBM -> calibration -> ABSTAINING policy
  (hold/scale/exit/abstain), regime-gated (BOCPD), TIER-2 strategy-library RAG (the "RAG of strategies" idea,
  per-playbook CPCV-gated), RL-liquidation leg; CPCV/PBO gates every model before live. Phased build plan.
- NEXT GATE: AUC->dollars policy backtest (abstaining exit vs the D163 fixed trail) -- the decisive P&L test.
- NEW: scripts/build_exit_label_corpus.py (55,628 labeled rows) + validate_exit_classifier_cpcv.py.
- SYSTEM_MAP: strategy.md (the validated exit edge + CPCV mandate), architecture.md (unified exit design),
  research.md (the SOTA corpus verdict + what's hype).

## doc 230 -- 2026-06-02 -- system assessment: why we lose + multi-day verdict + exit brainstorm + data-flow diagram
- THINKING doc (no live change). Ran the trailing-stop multi-day experiment (scripts/
  simulate_multiday_trailing_exit.py) over 787 clean entries: a daily trailing stop does NOT beat
  selling at the signal-day close -- EVERY trail width (10-40%) has NEGATIVE median (-5% to -9.4%) +
  <40% win. Liquid subset marginally less bad, still losing. 41% of names peak on d1 but peak
  MAGNITUDE grows d1->d5 (+3.8%->+11.6%) => exit is irreducibly CONDITIONAL (continue-vs-fade), not a
  fixed policy. Multi-day hold = NOT the fix.
- WHY WE LOSE (realized n=53): total -$5,467, win 30%, win/loss ratio 0.98 -> negative expectancy by
  arithmetic (need >2.3:1 at 30% wins). Deliberate same-day exits (-$3,829/26% win) WORSE than
  accidental carries (-$1,639/39% win) -> our EXITS destroy edge. Loss tail concentrated (LIDR ~-$6.1K).
  catalyst_type='unknown' for ALL trades -> can't measure per-pattern (blocks a strategy library).
- CURATION VIABILITY: segment sweep (gap/price/RVOL/MFCS) shows NEAR-HOMOGENEOUS subsets (peak ~+12%,
  close ~-5% everywhere) -> current features DON'T separate winners. Mild real signals: moderate-RVOL
  (20-50) best, extreme RVOL>50 worst (exhaustion), >$10 holds gains, MFCS>0.55 only +median bucket (n=26).
- BRAINSTORM (higher-level, what-would-it-take): BET#1 = learned continuation-EXIT classifier (TabPFN/GBM
  on minute-bar labels, re-scored intraday, wired into D122 slot) -- highest ROI, buildable now. BET#2 =
  curated strategy library + RAG router keyed to catalyst x structure archetypes (Pierce's idea) -- right
  long-horizon architecture but GATED on catalyst tagging + separating features + per-pattern n. The
  UNLOCK: label the whole 16k-ticker x 2yr warehouse (not just our 53 trades) to kill the small-n problem.
- DIAGRAM: comprehensive Mermaid data-flow of ALL paths (8 subgraphs: sources, phase loop, agent pipeline,
  execution, fill tracking, exit/close x5 paths, booking, reporting; dormant=dashed, hardened-close=red).
- SYSTEM_MAP: strategy.md (selection=variance, exit=lever, the 30%/0.98 math), data.md (universe-labeling
  unlock + catalyst-tagging gap), architecture.md (the data-flow diagram).

## doc 229 -- 2026-06-02 -- close-path phantom sweep (2 CRITICAL) + multi-day-hold data-gap analysis
- BUG SWEEP (adversarial agent + line-by-line verify) found 2 CRITICAL phantom-P&L paths the
  216-228 arc MISSED, both fixed:
  * CRITICAL #1 main.py:7787 shutdown close booked MTM as realized with NO broker close + removed
    tracking/state -> on the normal overnight-carry path (Phase 4 _skip_close) it poisoned BOCPD/
    Kelly + orphaned the open broker position (APPS-5/28/LFS-5/27 class). FIX: market-open gate +
    attempt_close_with_status_check + book ONLY on confirmed close (else carry intact, stops untouched).
  * CRITICAL #2 main.py:7149 Phase 4 EOD fallback booked "regardless" of broker_closed. FIX: mirror
    the D76 `if not broker_closed: continue` phantom guard.
- HIGH #3 bridge.py:1684 close_with_attribution referenced undefined `exit_reason` -> NameError on
  EVERY close (swallowed) -> close-Discord + decision-learning silently DEAD since D218. FIX: add
  exit_reason param (+ defensive __init__ _watchlist_webhook_url=None; main.py:721 wires the real one).
- MED #4 position_manager.py merge left buy#1's absolute target_prices/stop after the weighted-avg
  entry -> runner re-bought higher would sell BELOW cost. FIX: rescale targets+stop by entry ratio
  (tighter not looser for longs) + reset tranches_filled. Pinned by new test (9/9 in test_position_merge).
- LOW #5 post_fill_handler.py:288 stop register used order.qty not merged remaining_qty. Fixed.
- DATA: multi-day-hold hypothesis ("fail to close -> skyrockets") tested on 784 clean entries
  (eval corpus x split-adjusted forward 1-5d bars). OVERTURNED: excluding 4 UNCAPTURED-SPLIT phantoms
  (splits.parquet stale@5/18, missed ASBP 5/11 / WGRX 5/26) collapsed the d5 mean +12.0%->+1.2%.
  Clean: median NEGATIVE & worsening (-1.4%->-4.8% d1->d5), mean ~flat -> holding buys VARIANCE not
  expectancy. Real edge is PEAK-CAPTURE (median peak +11.6% by d5 vs close -4.8%), only with a
  trailing exit. 7 data gaps identified (unadjusted bars+stale split calendar, T+1 staleness, no
  price/qty in trade_results, selection bias in carry set, no dilution/halt feed, liquidity unknown).
- NEW: scripts/build_multiday_hold_dataset.py -> data/research/multiday_hold_dataset.parquet (the
  state to explore multi-day holds). SYSTEM_MAP: execution.md (close-path phantom guards), data.md
  (split-calendar staleness + the multi-day dataset), known_bugs.md (2 CRITICAL closed).

## doc 227 -- 2026-06-02 -- entry-fill qty-drift ROOT fix: add_position MERGES not replaces (+228 ripples)
- ROOT of the 6/2 LASE +143%/+$50,921 ghost (doc 226): `PositionManager.add_position` did
  `self._positions[ticker] = position` (REPLACE). LASE bought 3x -> each re-buy overwrote the
  tracker with the last fill's qty, dropping prior shares -> tracker 10,680 sh short of broker ->
  D218 qty-drift all day -> EOD close 403 -> accidental overnight carry. Realized was -$3,950.
- FIX D227 (position_manager.py): add_position MERGES same-ticker same-direction re-buy:
  share-weighted-avg entry (weighted by remaining_qty/held), summed qty, earliest opened_at,
  max peak, adopt stop only if unset. remaining_qty==0 => REPLACE (no resurrecting phantom shares).
- FIX D228 (the 3 ripples, found by adversarial sweep agent, each verified vs real code):
  (1) CRITICAL bridge.py qty-drift reconcile ran against the discarded single-fill object ->
  add_position now RETURNS the canonical merged object; reconcile+schedule use it.
  (2) CRITICAL post_fill_handler.py state persistence wrote single-fill qty -> persists merged
  pos_obj values (survives D95 crash-restart with correct qty).
  (3) HIGH post_fill_handler.py exit ladder sized off order.qty -> sizes off pos_obj.remaining_qty.
- TESTS: tests/unit/test_position_merge.py (8) — every LASE failure mode pinned. 40 targeted pass,
  main boots clean. Holds the line until the B1 event-sourced ledger (doc 222) cuts over.
- SYSTEM_MAP: execution.md (add_position merge contract + canonical-return); known_bugs.md
  (D218/D227 qty-drift class CLOSED at source).

## doc 226 -- 2026-06-02 -- Tuesday post-mortem: the +31% truth + a NEW bug class (D218 qty-drift)

Pierce: "+31% today! but how did the SYSTEM perform vs what we set out for?" HONEST: +31% is
real paper equity but UNREALIZED + accidental; realized trades LOST -$3,950.

- THE NUMBERS: equity 148,744 -> 193,336 (+30%); realized -$3,950; the entire +$44K is LASE
  +$50,921 UNREALIZED (entry ~$1.33 -> $3.23, +143%), an accidental overnight carry. Deployed
  f29a0bd (all 18 commits live). 2 positions carried (LASE, STAK) unintended.
- WORKED: doc-209 honest EOD report (showed broker -$3,950 truth + qty_drift=2 flag); doc-216/
  220 cancel-settle + D242 fired (16:00:37 "qty FREED after settle-poll" -> force-close).
- NEW BUG CLASS (D218 qty-drift ghost at ENTRY): the D217 poll captured a PARTIAL fill
  (16,092) as terminal while the broker filled 26,772 -> internal tracker 10,680 SHORT all day
  -> D231 RECON_HARD_BLOCK x1985 + D313 EMERGENCY_STOP_FAILED x839 (alert storm) + the EOD close
  403'd (held_for_orders) + D222 phantom (LASE journal +929 vs broker -896). Distinct from the
  CLOSE-path bugs (216/218/219/220); this is the ENTRY/fill-tracking path.
- VERDICT vs doc-223 (execution integrity not P&L): MIXED-FAIL. The day made money by LUCK (a
  ghost it couldn't close ran +143%); the system did NOT do what we set out for. Most dangerous
  kind of green day. RE-RANKED priorities: P0 fix D218 partial-fill-at-entry; P0 decide LASE
  +$50K open overnight (D91 will retry 04:00, same qty-drift may 403); P1 accelerate doc-222
  ledger to authoritative (phantom recurred); P1 Adversary partial_entry_fill_drift scenario;
  + the D313/D231 carried-position dedup (2,824 alerts on one ghost).

## doc 225 -- 2026-06-01 -- pre-Tuesday bug sweep (adversarial review of today's live-path changes)

Final confidence pass before the 6/2 deploy. Adversarial code-review of the live-path changes.

- HIGH FIX (main.py): the doc-220 EOD final-pass had a 15:59->16:00 SKIP RACE -- final-pass was
  min_et>=59, but loop cadence (30-60s + D76 processing) can jump a 15:58 sample to 16:00:xx,
  where hour_et==16 and the D76 block (gated hour_et==15) is NEVER re-entered -> flag stays
  False -> Phase 4 carries the RETRYABLE position overnight (minute 59 SKIPPED, D242 doesn't
  rescue a tracked position). FIX: final-pass = min_et>=58 (still ~2min/3-4 passes of headroom;
  sets the flag before the hour rolls). + regression test test_doc225_minute59_skip_race.
- LOW FIX (bridge.py): result.get("last_error","")[:160] -> (result.get("last_error") or "")
  [:160] at the 2 NAKED_POSITION_RISK emit sites (last_error inits None; None[:160] could raise
  and silently drop the CRITICAL incident).
- CLEARED by the review (empirically): the rewritten close while-loop cannot infinite-loop
  (one-shot sentinel; max_retries+1 calls), _await_qty_available bounded/never-raises, re-arm
  correct, no double-close in D76 retry, parser None-safe, fill_capture tee double-guarded.
- NOT changed (right risk trade-off night-before-deploy): the settle_budget dead-counter
  (strictly conservative, 0 runtime risk -> calm-day refactor); order_id-None nit (pre-existing).
- VERIFICATION: 137 targeted tests green. FULL SWEEP 4021 passed / 81 failed -- the 81 are ALL
  PRE-EXISTING (0 in any touched file; proven by running the failing suites at fc9b4c0 [Monday's
  commit] = identical 13/58 fail). 18 commits = 0 new failures. main boots; adversary wrapper-bugs=0.
- CONFIDENCE: live-path adversarially reviewed line-by-line; the 1 real bug fixed+pinned;
  Tuesday cleared on the code dimension.

## doc 224 -- 2026-06-01 -- Operator OFF for the Tuesday (6/2) deploy

Pierce: "turn our operator off for now." Master kill-switch so the Tuesday deploy focuses on
execution-hardening + raw-fill capture without Operator noise.

- ops: NEW master kill-switch OPS_OPERATOR_ENABLED (default 1). When 0/false: operator_pulse.ps1
  exits before spawning + operator_observe.py main() no-ops. Set as a USER-scope env var (the
  scheduled MomentumX-Operator task is its own process) + mirrored to secrets/.env. Also pinned
  OPS_OPERATOR_T1_ENABLED=false (was default-true-but-unreachable -> now off explicitly).
- STAYS ON (NOT the Operator): raw-fill capture [websocket, the Tuesday GOAL], incident bus
  [bot-side CRITICAL escalations], post-close scorecard + Adversary battle [trading-launcher
  Phase-4 tail], EOD report/recon/execution-hardening. So Tuesday keeps hardened closes + honest
  reporting + same-session phantom detection + raw_fills, minus the 12 heartbeats.
- Verified: kill-switch no-ops; ps1 AST-parse-clean. Re-enable: OPS_OPERATOR_ENABLED=1, then
  later (deliberately) OPS_OPERATOR_USE_CLAUDE_CLI=1 + OPS_OPERATOR_T1_ENABLED=true after a clean
  observe shakedown. Closes doc-223 TODO G3.

## doc 223 -- 2026-06-01 -- Tuesday (6/2) green-light plan + TODO + expected outcomes

Planning doc. The deployment fact: the launcher runs the local checkout (no git pull), so HEAD
auto-deploys at 04:30. Bot ran fc9b4c0 today; HEAD bdf518a -> 18 commits (docs 211-222) deploy
Tuesday with no restart.

- backlog.md: classified all 18 by live risk -- (A) execution-hardening 216/218/219/220 =
  PROVEN-SAFE behavior change (no more naked overnight carry); (B) 2 pinned flags (ELITE off,
  exhaustion on); (C) DORMANT/observe-only (raw-fill capture [the Tuesday GOAL], ledger
  [imported in NO trade path], Operator observe-only, adversary post-close); (D) subtlety:
  OPS_OPERATOR_T1_ENABLED default-true but UNREACHABLE (observe runner takes no actions, claude
  -p off) -> RECOMMEND pinning it =false explicitly.
- GATES RUN TONIGHT: G5 adversary wrapper-bugs=0 GREEN; G1 67 new-suite tests GREEN; G2 boot
  clean. Green-light criterion + RED rollback (git revert on develop = HEAD rollback) documented.
- EXPECTED OUTCOMES: no unintended naked carry; honest EOD report; phantom now catchable
  same-session (incident_synth CRITICAL); the Tuesday GOAL = raw_fills_2026-06-02.jsonl confirms
  the execution_id key (unblocks B1 Phase-4). Judge Tuesday on EXECUTION INTEGRITY, not the P&L
  number (selection still buys variance, 213-215). DEFERRED (not Tuesday): ledger cutover, T1
  arm, fade-short/faller/ELITE flags.

## doc 222 -- 2026-06-01 -- B1: raw-fill capture + the event-sourced ledger (Phase 1-3)

Pierce: "wire the raw capture, then build Phase 1/2 schema + ledger on confirmed evidence."

- ops: src/data/websocket_client.py tees every raw trade_updates msg verbatim BEFORE parsing
  -> data/ops/raw_fills_<date>.jsonl (src/ops/fill_capture.py, never-raises,
  OPS_RAW_FILL_CAPTURE). Monday's session = ground truth of the per-fill id key + replay corpus.
- d_codes.md: parse_trade_update now EXTRACTS data.execution_id (the Phase-0-dropped dedup key)
  into TradeUpdateEvent.execution_id + computes broker_event_id = exec:<id> ELSE a
  DETERMINISTIC hash of (event,order_id,filled_qty,price,ts). Dedups regardless of payload;
  raw capture confirms which path is live. Never random (replay-safe).
- models.md: NEW src/ops/ledger.py = the event-sourced ledger. Phase 1 schema (ORDER_* never
  book; FILL_PARTIAL/COMPLETE book + REQUIRE broker_event_id -- THE structural rule enforced on
  append; RECON_DELTA). Phase 2 durable SQLite (WAL+synchronous=FULL+checkpoint on fills;
  broker_event_id UNIQUE -> dup fill = idempotent no-op). Phase 3 position/realized_pnl = PURE
  FOLD over fills (no divergent cache).
- PROVEN: the 6/1 STG phantom (ORDER_SUBMITTED->ORDER_REJECTED, never filled) books $0.00 (old
  code +$304.20); a real FILL books +$100. Phantom now STRUCTURALLY impossible. Gates 1-3 green
  (fold purity, dedup, structural rule); 8 ledger + 19 trade_updates tests; main boots.
- backlog.md: NOT done yet (gated, do NOT rush) = Phase 4 rewire O1-O6 (only FILL_* book,
  delete optimistic path) + Phase 5 recon (LEDGER_BROKER_DRIFT) + Phase 6 Adversary 5 scenarios
  + crash test + CI grep-guard + >=5-session SHADOW. Cutover gated on the raw-capture
  confirmation + the shadow being clean. The ledger is proven in isolation, waiting for evidence.

## doc 221 -- 2026-06-01 -- B1 Phase 0: ground truth (optimistic-booking map + confirmation-channel gap)

Pierce's B1 mandate, Phase 0 (investigation only, NO ledger code). Two parallel read-only
audits + my own verification of the load-bearing claim.

- HEADLINE FINDING (surface-before-design): a broker fill-confirmation channel EXISTS + is
  wired live (the Alpaca trade_updates WebSocket -> fill_stream_bridge.on_trade_update,
  main.py:1083). The gap is NOT "no channel" (the agent's framing) -- it's that the channel
  (a) is NOT durable (in-memory deque, drained per cycle, never persisted) and (b) DROPS the
  per-fill dedup key: parse_trade_update reads only data.order, never data.execution_id
  (verified: execution_id referenced NOWHERE in the code). So B1's prerequisite = extract
  execution_id + persist fills durably BEFORE the fold.
- experiments.md / d_codes.md: OPTIMISTIC-BOOKING SURFACE = ~6 sites, all funneling into O1
  = close_with_attribution / position_manager.close_position_with_attribution (pm.py:1097-1107)
  which books pnl from a PASSED-IN exit_price with NO fill check -- the engine of the phantom.
  docs 177/216-220 guard WHEN succeeded is true, but succeeded is a wrapper hint, not a fill,
  and O2 (D146 BAR-1, post_fill_handler.py:374) / O3 (stopout) / O4 / O6 reach O1 with weak/no
  guards. CONFIRMED (correct) model: tranche_monitor.py:213 + fill_stream_bridge.py:248 (book
  off a real stream fill).
- 6/1 phantom replay seed: STG close 403'd (held_for_orders, never filled) yet journal booked
  +$304.20 via an O1 path -> D222 caught it after the fact. In B1 (no FILL event -> no
  execution_id) the fold books $0; phantom impossible.
- backlog.md: REVISED phase order -- Phase 1/2 FIRST (extract execution_id into
  TradeUpdateEvent + SQLite append-only log, dedup PK=broker_event_id) -> Phase 3 fold ->
  Phase 4 replace O1-O6 -> Phase 5 recon (LEDGER_BROKER_DRIFT CRITICAL) -> Phase 6 the 5
  Adversary scenarios + 8-gate gauntlet + >=5-session shadow. PHASE-1 TASK #0: capture ONE
  real raw trade_updates msg to CONFIRM the execution_id field before designing the schema
  around it (don't build on faith -- the cap-bug/MFCS-artifact discipline). NO code shipped.

## doc 220 -- 2026-06-01 -- EOD close retries until the bell (the Adversary's architectural flag, closed)

Pierce: "take the EOD-timing fix next." Closed the 1 architectural flag the Adversary
couldn't survive (eod_deadline_never_settles -> carry).

- d_codes.md: D76 EOD close -- ROOT CAUSE was NOT a late fire-time (it fired 15:55:23, ~4.5min
  before the bell) but that D76 made ONE pass (~7s) and set eod_close_completed=True EVEN WHEN
  closes FAILED -> CMND/OPTU 403'd and were abandoned -> Phase 4 carried them (6/1). FIX
  (main.py): track _d76_all_closed; mark complete ONLY if every position closed OR it's the
  final pass (min_et>=59). If any fail, leave the flag FALSE -> the next Phase-3 cycle (~30s,
  still <16:00) RETRIES. Single 7s burst -> repeated passes across 15:55->15:59 (the window the
  doc-216 cancel-settle poll needs). Past 15:59 accepts the carry (Phase 4 + doc-216/218 keep
  it protected/escalated, never naked/phantom).
- SAFE: positions re-fetched each cycle (open_positions) + D86 skip-if-gone -> no double-close;
  hard stop at 15:59 -> bounded. 7 gating tests (test_eod_close_retry.py); 27 crash-recovery
  green; 10 pipeline_guards fails PRE-EXISTING (stash-confirmed). main boots.
- EXECUTION-HARDENING ARC COMPLETE (216 cancel-settle + 218 naked re-arm + 219 partial strand
  + 220 EOD retry): a held_for_orders position at EOD now gets many settle-aware close passes
  before the bell; carries only if genuinely un-closeable (then protected + CRITICAL incident).
  The 6/1 CMND/OPTU carry would not recur.
- backlog.md: remaining = doc-185 B1 event-sourced ledger (the phantom ROOT) + an Adversary
  multi-pass-EOD scenario to assert the retry flattens a slow-settle before the sim bell.

## doc 219 -- 2026-06-01 -- adversary batch #2 (partial-fill strand fix) + continuous battle in scorecard

Pierce: "(b) then (a)". 5 new adversary weapons + wire it into the scorecard.

- (b) NEW weapons (adversary.py): partial_fill, stop_fills_mid_close, trading_halt,
  eod_deadline, eod_deadline_never_settles. Found 2 issues:
  * d_codes.md: D245/D219 PARTIAL-FILL bug (REAL, fixed): attempt_close set succeeded=True on
    ANY response -> a partially_filled close stranded the unfilled remainder (no stop, bot
    thinks flat). Fix (bridge.py): detect partial (status/filled_qty<qty) -> RE-CLOSE the
    remainder; if still partial after retries, succeeded=False (never a silent strand).
  * eod_deadline_never_settles = ARCHITECTURAL flag (not a wrapper bug): close past the bell +
    can't settle -> carry; only the EOD-timing fix prevents it. Harness now SPLITS wrapper-bugs
    vs architectural-flags, PASSES on 0 wrapper bugs. Result 10/11 (0 wrapper bugs).
  * Scoreboard so far: 216 cancel-settle race, 218 naked re-arm, 219 partial strand -- 3 real
    execution bugs found+fixed by the Adversary.
- (a) CONTINUOUS BATTLE: post_close_scorecard.py now runs the full sweep every session
  (MX_ADVERSARY_BATTLE, default on); prints survived N/total + wrapper-bugs + arch-flags, and
  emits a CRITICAL ADVERSARY_WRAPPER_BUG incident if the close path REGRESSES (Operator
  triages; don't arm T1 until green). Verified live: "survived 10/11 | wrapper bugs: 0 (clean)".
- 8 adversary tests + 39 close/recovery green. The durable execution edge: invariants once
  hardened + continuously battled STAY hardened (vs selection edges that evaporate, 213-215).

## doc 218 -- 2026-06-01 -- the Adversary execution harness (found+fixed a real naked-position bug)

Pierce: "build it!" Per doc 217: built as a ROBUSTNESS tool vs our DETERMINISTIC execution
plumbing (can't be overfit), NOT a selection-edge discoverer.

- models.md: NEW src/ops/adversary.py -- AdversaryBroker (drop-in for AlpacaDataClient: the 6
  close-path methods; a stop RESERVES qty/held_for_orders so close_position 403s with the
  byte-real 40310000 body until the cancel SETTLES, settle latency adversary-controlled) +
  check_invariants scoreboard (never silent-naked / never phantom / never stranded).
  scripts/adversary_run.py drives our REAL attempt_close_with_status_check across a 6-scenario
  settle/failure sweep. tests/unit/test_adversary.py (5).
- d_codes.md: D249 re-arm HARDENED. The Adversary's never_settles scenario found a real
  SILENT-NAKED bug: when the cancel never settles, D248 cancels the stop, close fails, and the
  D249 re-arm ALSO 403s (qty reserved) -> position naked + silent (the exact 6/1 STOP_REARM_
  FAILED path). FIX (bridge.py): re-arm waits via _await_qty_available first; re-arms only
  from REAL snapshots; if still unprotected, emits a CRITICAL NAKED_POSITION_RISK incident
  (doc 205/212) instead of a silent loss. SILENT-naked = adversary wins; ESCALATED-naked =
  survived. After fix: 6/6 scenarios survive (realistic_settle CLOSES = doc-216 holds).
- backlog.md: harness is extensible (partial-fill close, halt mid-close, 5xx storm, tranche
  403-war, EOD timing window); wire into post-close scorecard/CI to run continuously. Does
  NOT generate synthetic selection data (overfitting trap, doc 217). 40 tests green; main boots.

## doc 217 -- 2026-06-01 -- adversarial market sim: honest assessment (strategy, no code)

Pierce: "build a market that BATTLES our process to make us lose; is it worth it + how does
it fit; accelerate + compound the edge; dig like a tick."

- STANCE: YES as a ROBUSTNESS (execution-breaking) tool, NOT an edge-DISCOVERY tool. ~70%
  already exists in mx-arena/ (scenario.py inject flash_crash/halt/squeeze; failure_injector.py
  Alpaca-403/40310000/held_for_orders/422/5xx; exchange.py + sim_alpaca_client.py SimExchange;
  adverse_selection_sampler.py; synthetic_candidates.py; regime/walk_forward/calibration) --
  research-only, never wired to drive our real code.
- THE WARNING (from docs 213-215): every selection "edge" evaporated under no-cap+CI. A
  synthetic market is a data-generating model -> optimizing SELECTION against it = "finding"
  edges against an INVENTED distribution = overfitting (the exact trap just exposed). And we
  have NOT confirmed a real selection edge to compound; a sim HARDENS a confirmed edge, can't
  FIND one.
- RECOMMENDED ARCHITECTURE (build now, doc 218 proposed): the ADVERSARIAL EXECUTION HARNESS
  -- wire failure_injector + scenario + sim_alpaca_client to drive our REAL bridge.
  attempt_close / exit_intelligence / eod_recon / position_manager, scored by an INVARIANT
  CHECKER (never naked / never phantom / never unintended-overnight / drawdown holds / recon
  delta==0). Adversary wins by breaking an invariant or maximizing loss; we harden until it
  can't. DETERMINISTIC plumbing -> can't be overfit. Would have caught 216/the phantom/the
  carry before they cost (paper) money.
- DEFER: the synthetic-distribution SELECTION adversary until the real-data scorecard
  confirms a genuine edge (~2-4wk observe); final judge always held-out REAL data.
- "dig like a tick": the discipline (observe-first/no-cap+CI/beat-baseline) is the HOST the
  tick must not kill -- dig faster via a TIGHTER MEASUREMENT LOOP, not by loosening the gate.

## doc 216 -- 2026-06-01 -- EXECUTION HARDENING #1: the cancel-settle race (the dominant P&L leak)

Pierce: "pivot to hardening execution, highest ROI." The stress tests (213-215) proved
selection buys variance, not expectancy -> execution is the path to 5%. THE #1 execution
fix, proven from the 6/1 log.

- d_codes.md: D248/D245 close path -- fixed the cancel-settle RACE in
  attempt_close_with_status_check (the shared wrapper used by D76 EOD / D91 / D245). 6/1 log:
  D76 15:55 close CMND/OPTU -> 403 held_for_orders -> D248 cancels stop -> retry 403 (cancel
  not settled, ~1s) -> 3 retries exhausted -> close FAILED -> D249 re-arm ALSO 403 ->
  "POSITION IS NOW NAKED" -> both carried overnight (-$1,508 CMND). Old code slept a BLIND
  0.5s and each settle-wait BURNED a close-retry attempt. (doc 209 fixed only the D91
  overnight path, not the shared wrapper D76 uses.)
- models.md: NEW _await_qty_available() polls the broker's qty_available until the cancel
  SETTLES (bounded ~6s). The qty-blocked branch settle-polls on a SEPARATE bounded budget
  (settle_budget=3, while-loop, attempt decremented on a settle-wait) so it does NOT consume
  the close-retry budget. Hard-capped -> no infinite loop.
- 4 new tests (test_close_cancel_settle.py): the exact 6/1 CMND scenario now SUCCEEDS (not
  naked) + clean bounded FAIL if qty never frees. 35 close/recovery tests green; main boots.
  All callers pass qty.
- backlog.md: follow-ons -- EOD-close timing margin (spawned task), the phantom root
  (doc-185 B1 event-sourced ledger). This is the execution-over-selection pivot in action.

## doc 215 -- 2026-06-01 -- stress test #3: the doc-187 continuation edge is UNVALIDATED on our data

Pierce: "next suspects." Tested the doc-187 continuation features that underpin docs
188/191/192.

- FINDING: the continuation features (opening_rvol, opening_range_sign, broke_or_high,
  vwap_distance) are NOT in the historical feature logs -- only computed live in doc-188
  observe-mode (since ~today). So the doc-187 continuation edge has NEVER been tested on our
  data; it was wired on external liquid-stock research (exactly doc 187's own caveat).
  Status: UNVALIDATED, not debunked. docs 188/191/192 rest on an unproven-on-our-data
  premise -- correctly held in observe-mode.
- backlog.md: doc-191 faller-exemption STAYS FLAG-GATED OFF (flipping it = acting on faith,
  same error class as the MFCS press flip); doc-188 detector keeps logging -> first
  on-our-data test in ~2-4 weeks; doc-184 continuer trains on observe data ONCE IT EXISTS.
- TESTABLE sub-claim that HOLDS: first-window timing (doc-187 RANK 4). minute_et is the #1
  separator (AUC 0.40, sep 0.10; ran@37min vs fade@41min, earlier->RAN) -- modest (hour_et
  near-flat 0.52) but VALIDATES the D97 stale-entry-cutoff on our data. Keep D97.
- GAUNTLET (3 suspects): MFCS OVERTURNED | fade-short FAILED->OFF | marketable SURVIVES-down
  ->ON | continuation UNVALIDATED->observe | timing HOLDS->keep D97. CONVERGENT TRUTH:
  selection buys VARIANCE not expectancy (median fwd negative every bucket/regime); the
  durable edges are TIMING (trade early) + EXECUTION (capture the spike, don't round-trip /
  carry losers). The boring structural levers survive; the "magic feature" claims evaporate.

## doc 214 -- 2026-06-01 -- stress test #2: marketable-fill edge SURVIVES but downgraded (+0.7% net)

Pierce: "next suspects." Ran the marketable-fill claim (docs 189/190/195, LIVE on disk)
through the doc-213 gauntlet.

- experiments.md: SAME cap bug as doc 213 was in fill_model_backtest.py (cands[:max] =
  time-truncation) -> FIXED (stratified even-spaced / --max 0 = no cap).
- 5-day no-cap exit-aware NET edge: 5/27 -0.06%, 5/28 +1.56%, 5/22 +1.52%, 6/1 +1.17%,
  5/29 -0.70% => ~+0.7% avg (NOT the headline +2-3%, which was GROSS on 2 days). VERDICT:
  SURVIVES, DOWNGRADED. Net-positive 4/5 days; biggest on MIDDLING days; ~0 on the great day
  (passive already fills); negative only on the catastrophic day.
- backlog.md: EXEC_MARKETABLE_LIMIT_ENABLED stays ON (correct -- net-positive avg; we did NOT
  flip it on an artifact, unlike the MFCS press). Correction is to believed MAGNITUDE: fills
  = real but 2nd-order lever, not the path to 5%. Corroborates doc 213 (median fwd negative
  3/5 days -> selection buys variance).
- GAUNTLET: MFCS-anti-predictive OVERTURNED (213) | fade-short FAILED wide -> OFF | marketable
  fill SURVIVES-downgraded -> ON. NEXT: doc-187 continuation features (liquid-validated, applied
  to low-float).

## doc 213 -- 2026-06-01 -- STRESS TEST: the MFCS-anti-predictive thesis was a 3-day artifact (overturned)

Pierce: "aggressively stress-test our approaches." Re-tested the claim we FLIPPED A LIVE FLAG
on (EXEC_ELITE_SIZING_PRESS_ENABLED=false). It was a methodology artifact; the flip was NOT
empirically justified.

- RED-TEAM (adversarial agent) found: (1) a BUG in selection_study.py `_load` -- `rows[:max]`
  truncated by FILE/TIME order, so the 3-day(cap110) vs wide(cap60) "contradiction" was a
  TIME-OF-DAY sampling artifact, not sample size; (2) MFCS-AUC within the BUY set is
  near-meaningless (67% of BUYs cluster 0.20-0.30 = range-restricted by the gate); (3) the
  3-day ELITE bucket was n=23, RAN-CI [0.8%,21%], + 5/29 (7%-win day) outsized.
- DEFINITIVE re-run (bug fixed: stratified/no-cap sample, n=2048, +Wilson CIs +median):
  MFCS is NOT anti-predictive -- higher MFCS -> HIGHER RAN% (17%->36% monotonic, the
  opposite of the 3-day claim). BUT the ELITE edge is in RAN% (spike frequency), NOT median
  P&L (ELITE median fwd60 = -0.18%, CI [29,44]% overlaps neighbors). DEEPEST stable finding:
  median fwd60 is NEGATIVE in EVERY bucket -> selection buys VARIANCE, not positive
  expectancy (why exits/sizing matter as much as picking).
- DECISION: the flag flip was unjustified; corrected data WEAKLY FAVORS the press (ELITE =
  most fat-tail spikes). But will NOT reflexively flip back (ELITE n=148 still thin) -- leave
  OFF, let the (now-fixed) scorecard/weekly study confirm on accruing data, then flip with
  evidence. Conservative error (under-size), not capital risk.
- TOOL FIXED: selection_study.py cap bug (stratified / --max 0) + Wilson CIs + median; the
  scorecard/weekly study inherit it. META: fade-short wide re-run showed ELITE fade-short
  LOSES (-1.65%/41% on n=66) -> doc-202 fade-short correctly stays flag-gated OFF. Every
  other small-sample claim must now clear no-cap+CI+median+multi-regime before gating live.

## doc 212 -- 2026-06-01 -- Day 1 results + the Operator's first blind spot (FIXED)

First live day with the Operator watching. HONEST result (broker truth, not the journal):
a LOSS. Journal said +$304.20 "profitable" = STG PHANTOM; broker realized CMND -$949.74,
OPTU -$487.48, STG ghost force-closed ~+$67. CMND+OPTU carried overnight UNINTENDED (EOD
close at 16:00 = past the bell -> D90 skipped it; the 15:55 close didn't flatten avail=0
held_for_orders positions). Equity 158,785 -> 147,045.

- THE FINDING: at 16:10 close-out the Operator reported "0 incidents / correct no-op /
  score 25" while a $3K+ ghost/phantom/carry mess sat at the broker (D231 RECON_HARD_BLOCK
  every 30s). A FALSE NEGATIVE = exactly the meta-anti-selection failure Pierce's critique
  predicted. Observe-first discipline VINDICATED: T1 was disarmed, so a blind Operator did
  no harm — it just mis-scored. Must be fixed BEFORE T1 is armed.
- FIX (incident_synth.py, Operator-side only, ZERO trading-logic risk): NEW _from_eod_report
  reads the EOD json broker-truth -> PNL_RECON_DIVERGENCE (|delta|>$1 = phantom) +
  QTY_DRIFT_GHOST (qty_drift>0) + EQUITY_OUT_OF_TOLERANCE; broadened log patterns
  (RECON_HARD_BLOCK/PNL_RECON DIVERGENCE/EOD_FORCE_CLOSE/HEDGE VIOLATION) + OVERNIGHT_CARRY.
  PROVEN: re-run on today's real artifacts now emits 2 CRITICAL (was 0). 4 regression tests
  pin the day-1 case (test_incident_synth.py); 26 ops tests green.
- WHAT WORKED: all 10 Operator pulses fired on time + Discord posted; doc-209 D248
  cancel-stops force-closed the STG ghost (not naked). NEXT (P1, live-behavior): EOD close
  must complete BEFORE the bell + apply the cancel-settle path to avail=0 positions. ROOT:
  the phantom = doc-185 B1 event-sourced ledger (strategic).

## doc 211 -- 2026-06-01 -- Operator Discord UA (Cloudflare 1010) + register-task cmdlet fixes

- src/ops/discord_notify.py: Discord sits behind Cloudflare which blocks the default
  urllib User-Agent (error 1010 -> 403); send a browser-like UA. Verified with a real post.
  Without it EVERY Operator heartbeat silently 403'd.
- scripts/register_operator_schedule.ps1: New-ScheduledTaskSettingsSet (NOT
  ...Settings); -RunLevel Highest direct to Register-ScheduledTask; mirrors the proven
  install_scheduler.ps1. Dry-ran every cmdlet. Operator task now registered + firing (10
  pulses 6/1, Discord heartbeats live).

## doc 210 -- 2026-05-31 -- Daily Data Ingest Discord metrics fix (doc 209 Bug 6 resolved)

Resolves the Bug 6 spawned by doc 209: the nightly "✅ Daily Data Ingest OK" embed
always showed `max d0 ?` · `rows ?` · `runtime 0s`. Separate process (the
MomentumX-DataIngest scheduled task), launcher-only fix; zero trading-code risk.

- ops/scripts: `scripts/daily_data_ingest.ps1` — 3 edits.
  (1) `$_ingest_start_ts` moved to the TOP (before STEP 1a); duration is now
  `[int]((Get-Date) - $_ingest_start_ts).TotalSeconds` (was computed off the log
  file's LastAccessTime ≈ now → always 0s).
  (2) report query slices max_d0 to `YYYY-MM-DD` (`str(...)[:10]`). DEEPER root
  cause than doc 209 framed: `MAX(d0)` is a pandas Timestamp rendering as
  `2026-05-28 00:00:00` — the embedded SPACE made `(\S+),` never match, so the
  embed was `?` on EVERY successful run, not just failures (proven: old regex vs
  old line → False).
  (3) robust extraction — capture `$LASTEXITCODE`, flatten with `Out-String` so
  `-match` populates `$matches` even when 2>&1 makes `$report` an array, and pass
  `PARSE_FAILED` sentinel (not silent None) on query failure / format mismatch.
- alerts.py: NO change. `alert_data_ingest_result` signature + field rendering
  (alerts.py:868-870) already correct; confirmed every ps1 kwarg matches by
  name/type. Bug was 100% PowerShell-side.
- Verified: AST ParseFile 0 errors (1249 tokens); 4-case extraction harness;
  offline end-to-end embed render (monkeypatched _post, no network) → FIXED run
  shows `2026-05-28` / `21,229` / `247s`. Parquet path confirmed canonical
  (DERIVED/aftermath_strat.parquet, used by 15+ scripts, fresh mtime 5/29).
- Out of scope (noted in doc): no failure-path Discord alert (ps1 exits before
  the Discord block on critical-step failure); `rows==0`→`?` (unreachable past
  STEP 3's regression guard).

## doc 209 -- 2026-05-31 -- Discord reporting bug sweep (from the live 5/27-5/29 alerts)

Pierce pasted the actual 3-day Discord alert stream. 3 parallel investigations + log
verification found 4 mechanical reporting bugs + 1 real trading bug; fixed the safe ones.

- FIXED (300c121): EOD headline always $0.00 / "Profitable day" on losing days. reset_daily()
  zeroes _daily_realized_pnl, and the EOD payload was built AFTER it from that (=0) +
  _unrealized_pnl/_session_start_equity (NON-EXISTENT attrs → 0); the recon sub-section had
  the truth. Fix: capture P&L+equity BEFORE reset; source from broker_truth_recon.
  broker_total_pnl first. "Profitable day" label (alerts.py): >0/<0/==0-honest (was >=0).
- FIXED (300c121): D315 boot self-test "Account equity: $0.00" — read non-existent
  _session_start_equity; fix prefers live account.equity then _starting_equity.
- FIXED (7d573a5): D91 overnight-close cancel-settle RACE = the APPS 3-session carry. 5/29
  log: STEP1 cancels stop, STEP2 sells ~1s later -> 403 held_for_orders=970 (cancel unsettled)
  -> cancel-blocking-stops found nothing to cancel -> burned 3 retries -> close FAILED ->
  overnight carry (the held_for_orders phantom class). Fix: STEP1 polls qty_available until
  settled (~3s); retry loop waits 1.5s when qty-blocked-nothing-to-cancel. 31/31 recovery
  tests pass; the 3 test_s018 fails are PRE-EXISTING (stash-confirmed).
- SPAWNED (not rushed): D313 re-fires every session for a carried+hedged position (subtle
  matched-order detection; safety-critical -> the Operator's job); data-ingest "? / 0s"
  placeholders (separate ps1 process). Both tracked.

## doc 208 -- 2026-05-31 -- Operator scheduler + detector wiring + OBSERVE mode (Monday-ready)

Pierce: "build the scheduler + detector wiring next. Let's see this working for Monday!"
The Operator now RUNS (observe-only, T1 disarmed, out of the bot's process = zero risk).

- ops: scripts/register_operator_schedule.ps1 (run ONCE elevated -> MomentumX-Operator task,
  12 weekday triggers at the runbook times) + scripts/operator_pulse.ps1 (per-trigger:
  time->label, weekend skip, run observe; optional claude -p Opus-4.8-MAX gated by
  OPS_OPERATOR_USE_CLAUDE_CLI). scripts/operator_observe.py = the observe-only session
  (synthesize -> ORIENT brief -> triage classify-only -> Discord -> daily operator log ->
  restraint-aware score -> clear WAKE). Dry-run verified (clean=True, log written); both ps1
  parse clean.
- models.md: src/ops/discord_notify.py (self-contained Discord post via OPS_ALERT_WEBHOOK_URL,
  never-raises). src/ops/incident_synth.py = DETECTOR WIRING done safely -- derives incidents
  from the bot's EXISTING artifacts (recon_status.json -> RECON_STATUS_BAD; day's log ->
  LOG_CRITICAL_PATTERN/LOG_ERROR_RATE_HIGH), ZERO live-trading-code edits, dedup'd. Real-time
  emit_incident() into specific detectors (CB/drawdown/ghost) = clean verify-each follow-on.
- PIERCE'S ONE ACTION for Monday: run `.\scripts\register_operator_schedule.ps1` elevated.
  Then 12 Discord heartbeats/day (all-clear or N-need-attention + game line). ARM SEQUENCE:
  watch the shakedown -> flip OPS_OPERATOR_USE_CLAUDE_CLI=1 + OPS_OPERATOR_MODEL -> arm T1
  (OPS_OPERATOR_T1_ENABLED) -> wire live detectors. NO live trading behavior change (observe-only).

## doc 207 -- 2026-05-31 -- Operator live-action layer: T1 toolkit + ORIENT context assembler

Pierce: "proceed straight into the live-action layer" (on the doc-206 spec).

- d_codes.md: D277 HALT got a LIVE FILE CHECK (alpaca_client.py) -- also halts if
  data/HALT_NEW_ENTRIES exists, checked each submission (the env-var-only path couldn't be
  set by an out-of-process Operator; halt_new_entries isn't a declared field). Additive --
  only ADDS a halt; positions/exits/stops never gated. Caught by verifying the real D277 path.
- models.md: src/ops/operator_actions.py (T1 toolkit, governance-gated + reversible):
  halt_new_entries (flagship, LIVE via the file, rollback `rm data/HALT_NEW_ENTRIES`) +
  clear_halt + stage_flag_safer (ladder-only, restart-STAGED, atomic secrets upsert).
  Every action -> ActionResult{ok,blocked,rollback_cmd,snapshot_id,applies}. 5 tests.
  src/ops/operator_session.py = the ORIENT context assembler (assemble_context/render_brief:
  deployed-vs-HEAD, HALT/T1/fuse, unresolved incidents+WAKE, in-flight, scorecard, recon/EOD)
  -- the "never guess" brief the scheduled claude -p reads first. Guarded; never raises.
- 22 ops tests green; main boots; brief renders. NO live change until the scheduler+T1 armed.
- NEXT (final): operator_pulse.ps1 + Task Scheduler (claude -p Opus 4.8 MAX, 12 runbook
  times) + wire ~5 detectors to emit + Discord/cost-cap/counterfactual logs -> observe-only
  dry-run -> arm T1 (OPS_OPERATOR_T1_ENABLED).

## doc 206 -- 2026-05-31 -- Operator v2: Pierce's critique incorporated (hardened protocol)

Pierce's design review of 204/205 -- "make the changes you think are right, then proceed
into the live-action layer." Every point incorporated (tested).

- SCORING Goodhart fix (operator_scorecard v2): RESTRAINT is the win (+correct_noop headline;
  -unnecessary_action tax); "incidents triaged" removed as a positive; VALUE gated (pnl_saved
  needs a logged counterfactual; regressions need the mechanical gate) + capped (can't
  dominate hygiene); MTTR penalized only when action warranted; confidence-vs-outcome tracked.
- DEFINITIONS made MECHANICAL (operator_governance NEW): declared T1 transition LADDERS
  (risk/positions/drawdown step toward safety only -- no "safer by feel"; re-enable = Pierce);
  t2_gate = dominate CURRENT PRODUCTION on a pre-registered set (primary>=, maxDD<=,
  hit_rate>=prod-2pp, tests green) -- no narrative "ties safely"/"best ever"; the 10
  pipeline_guards rot fails get a FIX-BY date (2026-06-13).
- SESSION COORDINATION added: repo write LOCK + in_flight registry; ORIENT reads the last 2
  sessions' decisions.
- SELF-GOVERNANCE hardened: daily T1 cap (12) + session cap (6); SOFT FUSE (3 failed T1s / 2
  unresolved escalations -> auto-downgrade to T0 until Pierce re-arms); confidence gate
  (>=0.70); adversarial check on T2; the explicit one-line REVERSIBILITY TEST.
- META-ANTI-SELECTION check: instrument Operator confidence vs outcome (the -0.86 curve may
  recur at the meta layer); Sunday self-audit reviews it.
- ADDED: CONSULT tier (T0<->T1, 120s Discord veto), counterfactual log, "what I almost did"
  log, hypothesis registry (falsification-condition-first), weekly self-audit, cost cap
  (over-budget = an incident), backup escalation for CRITICAL.
- SCHEDULE revised: cut redundant mid-morning, +1 pre-open (06:30), split 16:10 close-out /
  17:30 deep-T2, + Sunday self-audit.
- 17 ops unit tests green. NEXT: the live-action layer on this spec (operator_actions +
  operator_session + operator_pulse.ps1 + detector wiring -> observe dry-run -> enable T1).

## doc 205 -- 2026-05-31 -- the Operator FOUNDATION (built): incident bus + rollback + the game

Pierce approved doc 204: "Full T0-T1, rollback via snapshots/git, Opus 4.8 MAX on auto,
never guessing about state, ~30min pulses at the important windows (you choose), Discord-
heavy, full daily budget, core code changes documented+tested+quantified vs best baseline
before a restart deploys them. Paper trading, full send, full permissions."

- models.md: NEW src/ops/ package. incident_bus.py (durable never-raises emit ->
  data/ops/incidents_<date>.jsonl; severity INFO/WARN/CRITICAL/DECISION; dedup; CRITICAL
  drops a WAKE sentinel; kill-switch OPS_INCIDENT_BUS_ENABLED). state_snapshot.py (the
  ROLLBACK backbone Pierce required: snapshot() records git HEAD + copies session_state.json/
  .env/recon_status.json; restore_state() rolls state back; code rollback = git revert).
  operator_scorecard.py (the GAME: transparent linear score + leaderboard + standings
  "beat your own best" + game_line for Discord/EOD).
- docs: operator_runbook.md = the per-session brain (ORIENT never-guess -> TRIAGE -> ACT
  [T0 observe / T1 reversible-risk-reducing-allow-listed-snapshot-first-<=6 / T2 test+
  quantify-vs-baseline+restart-deploy / T3 forbidden] -> ROLLBACK -> DISCORD -> SCORE+LEARN)
  + the 12-pulse intraday SCHEDULE I chose (03:45 boot, 07:15, 09:25, 09:40 post-open,
  10:20, 11:00, 11:45, 13:00, 14:15, 15:00, 15:50, 16:10 post-close).
- tests: test_ops_foundation.py (8) -- bus roundtrip/never-raises/dedup/disabled; snapshot->
  mutate->restore; score transparency; beat-your-own-best. NO live behavior change yet
  (Operator not scheduled until the next build).
- NEXT (Phase 2/3): operator_actions.py (T1 allow-list primitives, reuse D277 HALT) +
  operator_session.py (context assembler + Discord + score) + operator_pulse.ps1 + Task
  Scheduler (claude -p Opus 4.8 MAX) + wire ~5 detectors to emit. Then observe-only dry-run
  -> enable T1.

## doc 204 -- 2026-05-31 -- THE OPERATOR: AI co-pilot design (engage Claude to run the bot live, as a game)

Pierce: "enhance the system so it engages you at errors/critical decisions to debug/fix/
enhance live -- a management mechanism -- throughout the day, like a game beating your own
high score. Are you open to it + how?" DESIGN doc (no code yet -- awaiting Pierce's autonomy
decisions in sec 10).

- STANCE: yes, as an on-call OPERATOR not an unsupervised autopilot. Bot NEVER blocks on me;
  existing CB/HALT/stops are the real safety; no auto-deploy of risky code; autonomy tiered
  + earned + kill-switched.
- LOOP: DETECT -> CAPTURE -> ENGAGE -> ACT -> SCORE -> LEARN.
- DETECT: src/ops/incident_bus.py (never-raises emit -> data/ops/incidents_<date>.jsonl);
  wire existing detectors (CB trip, RECON_LETHAL, ghost/phantom, drawdown, fade-short
  candidate, wrong-flag). CAPTURE: act-cold incident packet (logs+state+file:line+playbook hint).
- ENGAGE: (1) scheduled `claude -p` pulse every ~10-15min RTH; (2) event-WAKE sentinel +
  OPS_ALERT_WEBHOOK Discord on CRITICAL; (3) one-tap human escalation for T2/T3. Mechanism =
  Claude Code headless w/ scoped tools.
- ACT (autonomy tiers = the standing mechanical-vs-live-behavior rule): T0 observe (auto) /
  T1 safe-reversible-risk-reducing remediation (auto, allow-listed, rate-limited, kill-switch)
  / T2 propose code+config (human-gated: branch+diff+tests+score-delta -> approve) / T3
  forbidden auto.
- SCORE (the game): Operator Scorecard (incidents triaged, P&L saved, regressions prevented,
  -MTTR, -false-positives) + cumulative leaderboard, "beat your own rolling best", badges,
  posted to EOD report.
- LEARN: operator_playbook.md (kind->diagnosis->fix->outcome); proven remediations promoted
  T2->T1; extends MEMORY.md + ship-doc discipline.
- PHASES: 1 observe foundation (bus + triage, ZERO risk, build first) -> 2 operator loop +
  game -> 3 T1 remediation + T2 approval queue -> 4 playbook + auto-promotion. DECISIONS FOR
  PIERCE (sec 10): autonomy ceiling (rec T0-only 2wk), cadence, escalation channel, T1
  allow-list, cost budget. RECOMMENDATION: build Phase 1 now (zero-risk).

## doc 203 -- 2026-05-31 -- short crash-recovery parity (the fade-short pre-live gap)

Pierce: "pursue h." The one real pre-live gap for the doc-202 fade-short -- and a latent
LIVE bug for D161 shorts too.

- GAP: BOTH recovery paths (sync_from_state_and_orders D64 + sync_from_broker D56) had
  `if side != "long": continue` + `if qty<=0: continue` -> a broker SHORT is DROPPED on
  restart (unmanaged ghost). ManagedPosition defaulted direction="long" and PositionState
  didn't persist direction at all -> a recovered short came back as a long (stop computed
  BELOW entry = inverted/useless). Affected the live D161 faller-short, not just the
  flagged-OFF fade-short.
- FIX: (1) session_state PositionState.direction field (+from_dict; legacy->"long").
  (2) PositionManager._recovery_stop_targets(entry,is_short) -- short: stop ABOVE
  (entry*(1+stop_pct)) / targets BELOW. (3) both D64+D56 recover shorts (abs qty, direction
  from broker side, direction-aware stop/targets, direction on ManagedPosition + a
  "D202 SHORT RECOVERED" warning); the LONG branch is byte-for-byte unchanged. (4) D161 +
  D202 persist direction="short".
- VERIFIED: long path intact (44 crash-recovery+executor pass; direction round-trips;
  legacy->long); 4 new short-recovery tests (test_short_recovery.py); ZERO new failures --
  the 10 pipeline_guards fails are the PRE-EXISTING orchestrator MagicMock rot (confirmed:
  stashing the (h) changes gives the identical 10 failed/18 passed).
- DORMANT until shorts exist (fade-short flag OFF) = zero live behavior change today; just
  makes recovery correct the day a short exists. Closes fade-short pre-live (a); remaining:
  (b) more data, (c) Pierce flips EXEC_FADE_SHORT_ENABLED.

## doc 202 -- 2026-05-31 -- the fade-short, WIRED (flag-gated) -- the second income stream

Pierce: "full send doc 202." Wire the ELITE-fade-short into the live path, flag-gated, with
a squeeze circuit-breaker. SHIPPED OFF (strict no-op until flipped).

- models.md: NEW fade_short_signature_ok() in opening_range.py -- True only when enabled
  (default False) ∧ MFCS>=0.50 ∧ shortable&ETB ∧ dolvol>=floor ∧ SQUEEZE GUARD passes
  (NOT doc-188 ORB-confirmed AND NOT above-VWAP -- never short a runner; reuses the LONG
  continuation detector as the SHORT kill-switch). 8 tests.
- config: ExecutionConfig.fade_short_{enabled(False),min_mfcs(0.50),min_dollar_volume(5M),
  size_mult(0.25)} (EXEC_FADE_SHORT_*).
- d_codes.md: D202 gate in main.py (after the faller block, before D170) -- when the
  signature matches a long BUY, build a SHORT verdict (reuse short_selling: stop above,
  targets below, fractional size) -> bridge.execute_verdict (-> _is_short ->
  submit_oto_short_order = broker buy-stop + doc-190 bid-marketable SELL) -> register stop
  -> continue (do NOT also buy). Flag OFF => block skipped entirely (no-op). 24 unit tests green.
- backlog.md: PRE-LIVE checklist -- (a) full crash-recovery state-persistence parity for
  shorts, (b) MORE DATA (n=11/3d = directional, let the weekly study accrue ~a month),
  (c) Pierce's call to flip EXEC_FADE_SHORT_ENABLED. Scorecard shows the realized edge first.
- ARC COMPLETE (177-202): selection edge built BOTH directions -- LONG (continuers:
  188/191/198, fill 189/190/195, hold 196/197, don't-size-faders 199 live) + SHORT
  (borrowable ELITE faders 200/201/202) -- all measured by the auto-scorecard (193/194/199).

## doc 201 -- 2026-05-31 -- the realistic NET fade-short: the edge survives borrow + squeeze

Pierce: "full send the realistic net fade-short backtest."

- selection_study.py --realistic-short: adds the 3 doc-200 killers -- Alpaca ETB/shortable
  filter (check_asset_tradable per unique ticker; un-borrowable excluded), squeeze
  gap-through (stopped short covers at min(MFE, stop*k), k=1.5), round-trip friction (0.5%)
  + borrow haircut (0 intraday). fade_short_realistic in --json.
- VERDICT (3 days, NET): only 18% of ALL BUY candidates are ETB (most low-float can't be
  shorted) BUT 48% of the ELITE bucket is. The ELITE ETB subset nets **+9.47% / 100% win
  (11/11)** AFTER ETB+squeeze+friction -- HIGHER than the gross +6.38% (the ETB filter strips
  the un-borrowable noise; borrowable ELITE names are the cleanest fades, none squeezed).
  Lowest bucket (best longs) correctly the worst short (-8.73%). The edge SURVIVES + improves.
- LIMITS: n=11 ETB-ELITE/3d (100% win = small-sample, not a guarantee); current-ETB proxy;
  squeeze-k assumption. Directional GO, not a sized commitment; weekly study accrues n.
- PATH (doc 202, proposed, flag-gated): wire the ELITE-fade signature (MFCS>=0.50 ∧ ETB ∧
  liquid) into D161 SHORT (reuse shortability + doc-190 bid-marketable SELL) + a squeeze
  circuit-breaker (never short a live squeeze: above-VWAP/green or ORB-confirmed) -> validate
  via scorecard, start fractional size, Pierce's call to flip. Second income stream from the
  selection edge.

## doc 200 -- 2026-05-31 -- flags LIVE for Monday + the fade-short opportunity

Pierce: "make it live for Monday, full send it; then take advantage of the predictable
faders."

- PART A (LIVE Mon 4:30): flipped 2 flags in ~/momentum-x-secrets.env (+ .env mirror;
  binding verified vs ExecutionConfig). EXEC_ELITE_SIZING_PRESS_ENABLED=false (doc 199: the
  >=0.50 bucket RAN 4% / faded -6.19% -- the press was sizing 2x into the worst bucket;
  HIGHEST-impact change of the weekend). EXEC_RVOL_EXHAUSTION_SIZE_PENALTY_ENABLED=true
  (exhausted ran 20% vs 41% -> half size). Secrets/.env are local+gitignored; code committed
  in 198/199.
- PART B (fade-short analysis, selection_study.py --short-stop): puts/futures NON-VIABLE for
  low-float small-caps (no/illiquid options, no single-stock futures). SHORTING is the path
  (D161 faller-short + doc-190 bid-marketable SELL exist). FADE-SHORT (short@entry, cover
  +8% squeeze-stop, else +60min; GROSS): whole BUY set +2.26%/62%win/33%squeezed; **>=0.50
  ELITE bucket +6.38%/83%win/4%squeezed** -- the bot's own ELITE picks are the best shorts
  (inverse of the press we disabled); lowest bucket (best longs) is worst short (72%
  squeezed) = internally consistent. KILLERS: HTB/no-borrow, +108% squeeze-gap tail, borrow
  fees, bid fills. Net sensitivity: ELITE bucket ~+5.4% after a squeeze/borrow haircut --
  short the BUCKET not everything. PATH: (1) realistic net fade-short backtest (ETB filter +
  borrow haircut + squeeze gap-through), (2) wire fade signal into D161 flag-gated OFF with
  liquidity floor + squeeze circuit-breaker, (3) validate via scorecard before live.

## doc 199 -- 2026-05-31 -- ELITE-press verdict + exhaustion down-weight + weekly auto-study

Pierce: "full send the next actions" -- (f) exhaustion down-weight + MFCS-bucketed ELITE
test, (g) weekly study.

- (f2) MFCS BUCKETS (selection_study.py): the doc-178 ELITE press is CONFIRMED mis-targeted.
  >=0.50 (ELITE) bucket: n=23, RAN 4%, meanMFE -1.6%, mean fwd60 -6.19% -- the WORST bucket;
  the press sizes 2x into it. Lowest bucket (0.00-0.20) is BEST (66% RAN, +4.09%). Relation
  is ~inverted. RECOMMEND Pierce flip EXEC_ELITE_SIZING_PRESS_ENABLED=false or gate on a
  continuation signal (his call; left ON pending). Now measurable every session.
- (f1) EXHAUSTION DOWN-WEIGHT (shipped, flag-gated OFF): main.py before execute_verdict --
  if EXEC_RVOL_EXHAUSTION_SIZE_PENALTY_ENABLED and candidate.rvol_exhaustion, size x
  EXEC_RVOL_EXHAUSTION_SIZE_MULT (0.5). Data (doc 198): exhausted ran 20% vs 41%.
  Risk-reducing, default OFF, no-op until flipped. 74 regression tests green.
- (g) WEEKLY STUDY: post_close_scorecard.py auto-runs selection_study.py --days 5 on FRIDAY
  closes (MX_WEEKLY_SELECTION_STUDY, default on) -> data/reports/selection_study_<date>.json.
  The AUC table + MFCS buckets re-harden weekly; 188/191/184 signals continuously re-validated.
- config: ExecutionConfig.rvol_exhaustion_size_{penalty_enabled(False),mult(0.5)}.

## doc 198 -- 2026-05-31 -- selection study: what separates continuers from faders

Pierce: "(e) mine the feature logs for what separated winners from faders." THE most
actionable finding of the session.

- scripts/selection_study.py (NEW): labels every BUY candidate (multi-day) by forward path
  -- RAN (MFE>=10% in 60min) vs FADED -- and ranks decision-time features by rank-based AUC
  + boolean ran-rate splits. --json out.
- RESULT (302 candidates, 5/27-29): 28% RAN, runners +23.3% mean MFE (the prize is real).
  HEADLINE: **MFCS is ANTI-predictive** -- AUC 0.40, higher MFCS -> FADE (ran-mean 0.251 vs
  fade-mean 0.300). The bot's PRIMARY selection score inversely ranks continuation WITHIN
  its BUY set = the mechanical root of the selection problem. Also: bigger gap -> FADE
  (gap_pct AUC 0.36, #1 separator; +46% gaps ran, +55% faded); lower price -> RAN ($5.3 vs
  $9.3); rvol_exhaustion=True ran 20% vs 41% (real, already-computed down-weight signal);
  high premarket RVOL -> FADE (confirms doc-187's debunk on OUR tape).
- IMPLICATIONS: selection lever is NOT "trust MFCS more" -- rebuild ranking on the
  separating features (moderate gap + lower price + not-exhausted + opening-RVOL/VWAP once
  live) feeding the 184 continuer. RECONSIDER doc-178 ELITE press (size UP at MFCS>=0.50):
  if higher MFCS fades more, it may be sizing INTO faders -- now measurable via scorecard.
  Cheap immediate win: down-weight rvol_exhaustion names. CAVEAT: n=302/3d, directional;
  use AUC/rank not the outlier-skewed means; re-run weekly as data accrues.

## doc 197 -- 2026-05-31 -- tranche + runner exit model (the runner-upside half of net P&L)

Pierce: "(d) add tranche partial-sells to net P&L." doc 196's single-exit model
under-counted runners (MOBX +95% real vs single-exit +6%).

- scripts/fill_model_backtest.py: NEW _replay_tranche -- sell 1/3 at T1(+3%)/T2(+8%), the
  final 1/3 RIDES the D163 trail (activate +6%, give back 30% of max gain, >=5%/<=35%); hard
  stop ratchets to breakeven after T1; EOD closes the remainder. _net_for_fill now returns
  BOTH models; the net section prints a per-model table + exit mixes; --json
  net_pnl.models.{d122,tranche}. Config-driven (tranche_t*_pct + TrailingStopConfig).
- FINDING -- exit-posture tradeoff (no clear winner): GOOD day (5/28) D122 +3.38%/60%win >
  tranche +2.64%/79%win (intelligence rides modest runners better); BAD day (5/29) tranche
  -0.41%/median +1.00%/58%win > D122 -1.12%/-1.22%/39%win (scale-out locks the +3% T1 pop
  before the fade). Tranche = more ROBUST; D122 = higher good-day mean. The live bot runs
  BOTH -> bracketed by the two, and is MORE robust than the d122-only view (doc 196) showed
  (a -8.9%-median day nets only -0.4% under scale-out -> why blind days land near breakeven).
- 189/190 verdict unchanged: marketable NET edge +1.0..1.7% (good) / ~-0.7% (bad) under
  BOTH models -- robustly selection-conditional. Keep ON.
- scripts/post_close_scorecard.py: netReal/netEdge now read the runner-aware TRANCHE model
  (fallback d122); trend row tags net_model.

## doc 196 -- 2026-05-31 -- close the fidelity loop: exit-aware NET P&L

Pierce: "(c) close the fidelity loop with exit-aware net P&L." Gross capture (fill->+60min)
ignored the stop / D122 exits / EOD hold -> a fader "captured" its full fade; really the
stop cuts it at -5.5%.

- scripts/fill_model_backtest.py: REUSES phase3_replay_simulator (SimPosition, SimBar,
  D122ExitEvaluator = the 6 parallel exit strategies w/ upgrade-only semantics,
  replay_position = stop -> D122 -> EOD) to replay each fill to EOD -> realized NET P&L per
  name. --net-pnl (default ON), --net-window (2min), --stop-pct (0.055). D122 uses live
  config (conf 0.85 / 2 strat). Fetch extended to ~EOD (_FETCH_MIN=400). train==serve.
- FINDING: the STOP transforms the distribution. 5/29 (bad): selection win% 3 / median
  -8.92% (catastrophic gross) -> NET realized -1.98% (43% stopped @ -5.5%, rest D122/EOD).
  5/28 (good): NET realized +3.38%, NET edge +1.68%. Realized P&L is FAR less volatile than
  raw selection -- the risk machinery works (why blind-funnel days land near break-even,
  not -7%). Marketable NET edge +1.68% good / -0.84% bad -- still selection-conditional,
  smaller than the gross +-2-3% (shared exits compress the entry diff). Keep 189/190 ON.
- METRIC FIX: the selection MEAN was outlier-skewed (a +400% rocket made a 3%-win day read
  +3.49%). Now lead with WIN% + MEDIAN (5/29 median -8.92% = honest). scorecard table is now
  selWin%/selMed/netReal/netEdge; trend row carries them.
- FIDELITY LADDER COMPLETE: 192 (proxy/gross) -> 195 (NBBO + 45s staleness) -> 196 (exit-
  aware net P&L + honest selection metric). The backtest now models SELECT->FILL->EXIT.
- CAVEATS: single-exit (no tranche partials), config-% stop (sweepable), modeled NBBO.

## doc 195 -- 2026-05-31 -- higher-fidelity fill model: the staleness correction

Pierce: "(b) the higher-fidelity fill model." doc 192's proxy (LOW<=limit, no submission
staleness) under-counted the marketable edge by ~10x.

- scripts/fill_model_backtest.py: two realism upgrades, now the DEFAULT. (1) --fidelity
  arena drives the arena AlpacaFillModel (Alpaca's real rule: limit buy fills only when
  limit>=ask, AT the ask) with NBBO from the calibrated SpreadModel; --fidelity proxy
  reproduces doc 192. (2) --submission-delay-sec 45: order live only from ts0+45s (the bot
  submits ~45s post-eval), MARKETABLE limit anchored to the ask AT SUBMISSION -- the real
  reason CMND filled 0/2 (a stale passive limit misses a runner whose ask already moved up).
- FINDING (corrects doc 192, sharpens not overturns): marketable edge is +-2..3%,
  selection-CONDITIONAL, not +-0.2%. 5/28 (good sel): passive fills only 52% @1min vs mkt
  93%, EDGE +3.42% (caught 44 runners @ +8.3%). 5/26: ~0. 5/29 (bad): EDGE -1.97% (caught
  29 faders @ -8.3%). SELECTION still dominates ABSOLUTE P&L; 189/190 is LEVERAGE on it
  (+3% good day / -2% bad) -> KEEP ON; synthesis = 188/191 SELECT continuers -> 189/190
  FILL them for +2-3%. The proxy under-counted because it missed the fill rule + the lag.
- scripts/post_close_scorecard.py: footer note corrected (fill edge = selection leverage,
  not "2nd order"); the scorecard inherits the realistic defaults so the trend's fill-edge
  column is realistic from day 1.

## doc 194 -- 2026-05-31 -- wire the post-close scorecard into the launcher (auto-measurement)

- ops: scripts/daily_paper_trade.ps1 -- after `python -m main paper` exits (Phase-4 tail,
  before Stop-Transcript), best-effort runs `post_close_scorecard.py $Today`. Decoupled
  from trading (bot already exited) -> cannot touch the hot path or change the exit code;
  runs even if the bot crashed; full day's bars settled by ~4PM ET. Scores only $Today
  (~130 calls). Disable: $env:MX_POSTCLOSE_SCORECARD="0". Launcher parses clean (AST check).
- Effect: from Mon's close on, data/reports/measurement_trend.jsonl grows one row/session
  (selWin%/selMean headline + fill edge + gate-correct%) -- the dataset the SELECTION
  levers (188/191/184) get judged on before any live flip. Deploys with the Mon 4:30 auto-launch.

## doc 193 -- 2026-05-31 -- post-close scorecard: make quantification a habit

doc 192 found SELECTION dominates fills ~30x AND that our measurement was a one-off (the
doc-182 grader was built but never run). This operationalizes it.

- scripts/: NEW post_close_scorecard.py -- per recent session runs the fill-realism
  backtest (selection win% + fwd return + fill edge) + the doc-182 rejection grader
  (gate-correct%), and appends an idempotent row per date to
  data/reports/measurement_trend.jsonl {date,n,selection_win_rate,selection_mean_ret,
  fill_edge_2min,gate_graded_n,gate_correct_pct}. Prints a trend table. Best-effort,
  never crashes. HEADLINE metric = selWin%/selMean (the number to move).
- scripts/fill_model_backtest.py: added --json <path> summary output (consumed by the
  scorecard).
- backlog.md: run after every close (or wire into the launcher Phase-4 tail). The trend
  file is the dataset that tells us -- on OUR tape, day over day -- whether the SELECTION
  levers (188/191/184) move the headline number BEFORE we flip them live. Build the gauge
  before turning the knobs.

## doc 192 -- 2026-05-31 -- fill-realism backtest + the SELECTION reframe (quantify 189/190)

Pierce: "are you testing performance in the arena sim? if not, quantify our changes +
identify arena-realism gaps." Honest answer: NO, and the existing sims CAN'T see the fill
change. Two-agent recon: backtest.py / replay_optimizer.py / phase3_replay all assume a
perfect fill at eval-price/bar-close; arena_replay is a shadow of PAST prod fills; the
doc-182 grader had NEVER been run; there is no real Gemini arena.

- scripts/: NEW fill_model_backtest.py -- forward-models PASSIVE (limit at eval price) vs
  MARKETABLE (docs 189/190) fills on real BUY candidates from data/features, SWEEPS the
  fill window (1/2/5/15/60min; the bot's real good-fill window is ~120s = the stop-arming
  timeout). Reuses PRODUCTION compute_marketable_limit (train==serve) + arena SpreadModel
  (calibrated) + alpaca get_bars. Caught + fixed my own fill-price bug (passive must get
  price improvement, not fill at limit) -> spurious +6% edge collapsed to true +-0.2%.
- FINDING (3 days, robust): SELECTION dominates fills ~30x. BUY-set fwd return swings
  +3.68% (5/28, 50% win) to -6.86% (5/29, 7% win); marketable EDGE is only +-0.2% and is
  SELECTION-CONDITIONAL (catches runners on a good day, more faders on a bad one). 189/190
  is a fine 2nd-order amplifier, NOT the lever. The 5% gap lives in SELECTION -> validates
  the continuation edge (188/191) as THE priority.
- d_codes.md: ran finalize_rejection_outcomes.py 2026-05-29 (grader works; 1 graded row,
  a below-VWAP block that faded -81.6% = gate right; 0 faller rows -> doc 191 unfed till Mon).
- backlog.md: ARENA-REALISM ROADMAP -- (1) forward fill model [done], (2) operationalize
  measurement = daily post-close scorecard runner [next, recommended], (3) mx-arena
  LimitAwareFillModel for queue/partial fidelity, (4) exit parity (phase3 D122) for net
  P&L, (5) SELECTION win%/fwd-return is the headline metric. NEXT FEATURE should improve
  SELECTION (data-gated on Mon observe data), not fills/sizing.

## doc 191 -- 2026-05-31 -- gate-action staged: liquidity-floored faller exemption (flag OFF)

Pierce (b): "pre-build the liquidity-floored faller-exemption flag-gated OFF, ready to
flip when the observe data confirms -- and trade the continuers." The flip doc 188's
OBSERVE warning was the precursor to: turn the faller block from "block every predicted
fader" into "block UNLESS the validated continuation signature (doc 188) is present AND
the name is liquid enough to fill+exit." SHIPPED OFF (no-op until flipped).

- models.md: NEW pure predicate faller_exemption_ok(enabled, confirmed, dollar_volume,
  opening_rvol, min_dollar_volume, min_opening_rvol) in opening_range.py. True only when
  ALL hold: flag ENABLED (default False) + doc-188 CONFIRMED + dolvol >= floor ($5M, the
  doc-187 liquidity caveat made operational) + opening_rvol >= 2.0 (higher bar than
  confirm, for a LIVE hard-gate override). 7 unit tests.
- config: FallerDetectionConfig.continuation_exemption_{enabled(False),min_dollar_volume
  (5M),min_opening_rvol(2.0)} (FALLER_ prefix).
- d_codes.md: D191 wired at the D160 faller-reject -- the previously UNCONDITIONAL
  `continue` is now `if exempt: <fall through to D160 size-reduce -> D170 -> exec> else:
  continue`. Default OFF => identical prior behavior (proven by tests). _or_sig hoisted
  so the check is never a NameError; guarded try/except -> safe continue on any error.
  Exempted names trade at faller-REDUCED size (conservative; full-size press = next flag).
- backlog.md: FLIP PROCEDURE -- observe (188 warnings + 182 grader) -> calibrate run-vs-
  fade on OUR tape (~20-30 confirmed-continuers) -> set FALLER_CONTINUATION_EXEMPTION_
  ENABLED=true. doc 191 makes the flip a one-line env change.

## doc 190 -- 2026-05-31 -- fill symmetry: marketable limits on EVERY entry path

Pierce (a): "mirror the marketable fill onto OTO/tight + a bid-anchored marketable SELL
for the fader/short path." doc 189 fixed only the active WIDE_STOP arm; the executor has
3 entry sites and a passive limit on ANY is the same $0-fill bug waiting for that path to
go active (toggle MOMENTUM_T2 off -> OTO active; faller->D161 short).

- models.md: compute_marketable_limit(side, entry, quote, mfcs, ...) -- pure two-sided
  extension of doc-189's offset. buy: anchor=max(entry,ask) cross UP; sell(short):
  anchor=min(entry,bid) cross DOWN. quote None/<=0 -> entry fallback. Same capped offset,
  still a LIMIT (bounded worst fill). 8 new two-sided unit tests.
- d_codes.md: D190 -- executor fetches ONE snapshot (ask+bid) before the long/short
  branch; an _mk_limit(side,quote) closure applies the helper at all 3 sites (WIDE_STOP
  long, OTO long, OTO short). WIDE_STOP refactored to share -> net -1 round-trip vs
  per-site. Short ENTRY = sell-to-open -> crosses DOWN to the bid to fill the D161 fader
  path. Same EXEC_MARKETABLE_* flag/caps revert all 3.
- PROFIT CHAIN: PICK (188+184) -> FILL (189 active + 190 every path) -> HOLD (176/178)
  -> SIZE (178+Kelly). FILL link now complete across the whole entry surface.

## doc 189 -- 2026-05-31 -- the FILL link: marketable, momentum-adaptive entry limits

Pierce: "full-send the marketable/adaptive limit fix so the bot actually captures the
continuers it now identifies." doc 188 gave us the PICK; a pick we can't FILL earns $0.
Friday 5/29: the WIDE_STOP arm submitted a PASSIVE limit at the STALE eval price ->
CMND filled 0/2. We were vetoing our own continuers at the last step.

- d_codes.md: D189 -- the active WIDE_STOP BUY submission (alpaca_executor.py) now
  places a MARKETABLE, momentum-adaptive, CAPPED limit. Anchor = max(entry, fresh ask
  via get_snapshots) so it never sits below a moved-up price; cross the spread by an
  offset that scales with conviction (base 0.4% -> cap 1.5% at MFCS 0.55) and CLAMPS;
  kept a LIMIT (not market) so the worst fill is bounded. Standalone STOP unchanged.
- models.md: NEW pure helper compute_marketable_offset(mfcs, base, max, full_at) --
  linear-scale-then-clamp; the FILL-side mirror of doc-178 ELITE sizing (lean in on
  conviction). 9 new unit tests (base/cap/clamp/monotonic/midpoint/div0/negative/anchored).
- config: ExecutionConfig.marketable_{limit_enabled,base_offset_pct,max_offset_pct,
  mfcs_full_at} (EXEC_ prefix, default ON; new fields => no stale .env override).
- backlog.md: follow-ons -- mirror on OTO/tight + bid-anchored marketable SELL on the
  short/fader path; anchor to the M2 WebSocket price to drop the REST round-trip.
- PROFIT CHAIN: PICK (188+184) -> FILL (THIS) -> HOLD (176/178) -> SIZE (178 + Kelly).
  Front half of the chain now complete. Still gated on the elevated restart (177-189).

## doc 188 -- 2026-05-31 -- opening-range continuation edge (trade the pumps that CONTINUE)

Pierce: "why not trade pumps? full send on highest-ROI to extract max profit."
ANSWER: don't blindly trade pumps (Friday: deliberate pump trades -$1,500, forced
ghost-holds +$6,999; ~2/3 fade; doc-187 killed 12 "obvious" pump signals). DO trade
the ~1/3 that CONTINUE, which the research tells us how to spot.

- models.md: NEW src/analysis/opening_range.py -- the validated continuation detector
  (doc 187 RANK 1+2): compute_signal() -> {opening_rvol, range_sign, broke_high,
  above_vwap, confirmed, score}. confirmed = bullish open AND broke 5-min high AND
  RVOL>=threshold AND above VWAP. OpeningRangeTracker captures the 9:30-9:35 bar.
  Ignores the debunked signals. 9 tests.
- d_codes.md: wired LIVE in OBSERVE-mode (safe): tracker instantiated; opening bar
  captured at the Phase-2 _fetch_bars; at the faller gate the signal is computed +
  3 feats (opening_rvol, opening_range_sign, broke_or_high) logged to the doc-182
  rejection-shadow + a "D188 CONTINUATION-CONFIRMED but FALLER-BLOCKED" warning fires
  (the case the grader will measure). 3 feats + vwap_distance added to the continuer
  contract (doc 184, now 13 feats). EOD reset wired. 18 unit tests pass.
- backlog.md: OBSERVE-FIRST discipline -- doc-187's edge was validated on LIQUID
  stocks, NOT our low-float universe; flipping it to gate live now repeats the
  trade-pumps-blindly mistake. GATE-ACTION (flag-gated, pending observe data): (1)
  faller exemption for confirmed continuers + liquidity floor, (2) catalyst-downgrade
  UPGRADE to full size for confirmed continuers, (3) continuer Kelly mult once trained.
  Turns "downgrade all no-catalyst high-MFCS" into "downgrade UNLESS confirmed, then press".
- PROFIT CHAIN: PICK (doc 188 + continuer 184) -> FILL (marketable limits, next) ->
  HOLD (exits 176/178) -> SIZE (ELITE press 178 + continuer Kelly). doc 188 = the front.

## doc 187 -- 2026-05-31 -- continuation-edge deep-research report + feature translation

Deep-research workflow wf_44e660ad-b68 completed (6 angles, 27 sources, 116 claims,
25 verified 3-vote, 13 confirmed / 12 KILLED). Synthesized + translated to the bot.

CONFIRMED (high conf): (1) opening RVOL (first-5min vol / 14d avg of that window) is
the #1 signal -- THRESHOLD/RANK effect (>=100%, top-20), NOT a monotonic gradient
(that was refuted); (2) direction = sign of the first 5-min candle (don't fade the
open); (3) OFI scaled by book depth (real vs hollow bid wall) -- but contemporaneous,
degrades on jumps; (4) the decision window is the first ~45 min (U-shaped vol).
DEBUNKED (actively distrust): short-interest/days-to-cover ALONE (0 edge; informed
shorts -> bearish), static displayed L2 depth in isolation (spoofing -- only score
absorption), float-rotation squeeze-loop, "high PM RVOL = real gap", market-level
first-30-min momentum (not single-stock). CAVEAT: all validated on LIQUID stocks, not
our low-float universe -- mechanisms transfer, magnitudes don't.

- models.md / d_codes.md: SHIPPED vwap_distance into the intraday continuer contract
  (doc 184, now 10 features) + the rejection-shadow logging (doc 182) + the faller
  wiring (sourced from FallerAssessment.vwap). VWAP interaction is a top confirmed
  signal. 9/9 tests pass.
- backlog.md: FEATURE ROADMAP (with data needs): (1) opening-5min RVOL [small bar
  tracker], (2) opening-range sign [first-5min candle], (3) OFI/depth imbalance [L2
  feed = data-acquisition gap; approximate w/ tape uptick-downtick until then].
  DON'T-BUILD: short-interest-bullish, displayed-depth-alone, float-rotation features.
  STRATEGIC option (future A/B): the validated ORB+RVOL entry (rank by opening RVOL,
  stop-entry at the 5-min-range break in the open's direction).
- experiments.md: the research gives PRIORS + the feature menu; the doc-182/184
  pipeline settles the magnitudes on OUR universe (open questions: does it hold on
  low-float; can OFI be predictive; optimal weighting; conditional squeeze signal).

## doc 186 -- 2026-05-31 -- tranche 403-war + ghost fix (comprehensive close-discipline, tactical)

Fixes the ROOT of Friday's recurring phantom/ghost: the PARTIAL-sell (D165 tranche)
path that doc 177 never hardened. Plus kicked off the deep-research run on the
continuation edge (doc 185 B3).

- d_codes.md: D165 TRANCHE path -- the standalone protective stop reserves the full
  qty (held_for_orders), so the plain submit_order tranche sell 403'd "insufficient
  qty" (the bot fights its own stop = B2 403-war; 66x on 5/29 CMND/STG -> ghost).
  doc 177 hardened full-closes (D76/D91/D242) but not partial-sells. FIX (main.py
  D165 block): (1) cancel the blocking stop BEFORE the tranche sell (+0.4s release),
  (2) re-arm for REMAINING qty on success -- now MANDATORY, not gated on
  adjust_stop_after_partial, (3) re-arm for FULL qty on sell failure (never naked;
  D313 is the 60s backstop). Tranches now execute instead of ghosting.
- backlog.md: this closes the OBSERVED phantom paths (D76/D91/D242/D165). The
  STRATEGIC fix (doc 185 B1: event-sourced ledger, P&L = projection of confirmed
  broker fills) remains the structural follow-on; B2 alt (no standalone stops that
  reserve qty -- software trail + single un-reserved close) filed.
- Verified: main.py compiles; test_d165_tranche_system passes; the 5 test_s022/s023
  failures are PRE-EXISTING source-introspection tests (confirmed via git stash) --
  zero new failures. Deploys on the next (elevated) restart with 177-185.
- experiments.md: deep-research workflow LAUNCHED on intraday continuation
  microstructure (squeeze vs pump) -- the #1 alpha bet; report will shape the
  doc-182/184 feature set.

## doc 185 -- 2026-05-31 -- research gaps + creative ideas (incl. Friday 5/29 review)

Reflective exercise (Pierce request) + Friday EOD review. No code; a research/ideas map.

FRIDAY 5/29 REVIEW: NO restart happened (ran b33b853/doc 178; docs 179-184 never
deployed). Realized -$1,500.60; equity +$6,363 = UNREALIZED ghost MTM. 9 submitted /
3 filled / 2 ghosts. THE KEY OBSERVATION: STG was a ghost worth +$6,999 unrealized at
EOD (bot "closed" it at +$507 in journal, broker 403'd, forced-hold, it RAN). Same as
APPS. => the bot's DELIBERATE trades lose; its ACCIDENTAL forced-holds win. Holding is
the edge; the exits destroy it. Phantom/ghost bug STILL recurring (doc 177 fixed only
the D76 path; intraday exit paths still ghost+book). 66x 403. D249 re-arm failed 2x
(fix not deployed). 0 slice errors (doc-177 fix held).

GAPS RECORDED (doc 185):
- PROCESS: A1 no pre-deploy strategy-replay gate (shipped doc 178 untested); A2 the
  deploy gap (fixes sit undeployed); A3 reactive bug discovery (need fault-injection +
  real-signature contract tests — a mock masked D249); A4 producer/consumer contract
  drift (a feature-availability linter would've flagged Continuer_v2 at 22%).
- SYSTEM: B1 phantom/ghost is STRUCTURAL -> event-sourced ledger where P&L = projection
  of CONFIRMED broker fills (kills the class); B2 403/held_for_orders war -> software
  stops + single un-reserved close path / OCO; B3 the continuation edge (squeeze vs
  pump) -> microstructure features (order-flow imbalance, L2 depth, tape, VWAP-reclaim,
  float-rotation, borrow); B4 decouple the catalyst signal from the 25s LLM (fast
  deterministic detector); B5 regime-conditional posture (the Ising mag_5d is computed
  but only wired to the lottery); B6 momentum-adaptive limit pricing.
- TOP-3 DEEP-RESEARCH BETS: (1) intraday continuation microstructure [run deep-research
  here], (2) broker-fill-driven position architecture, (3) pre-deploy replay harness.

## doc 184 -- 2026-05-29 -- intraday continuer scaffold (Phase-C, end-to-end, ready for data)

Builds the MODEL half of Phase-C (doc 183 built the data half). The full pipeline is
now end-to-end: doc-182 shadow (features) -> finalize_rejection_outcomes (labels) ->
train_intraday_continuer (model + "beats faller gate?" verdict) -> (future) live
shadow A/B -> (future) flip D170/limit flags.

- models.md: NEW src/analysis/intraday_continuer.py -- shared contract (train==serve):
  FEATURE_NAMES (9, ALL bot-available: gap_pct, rvol_log, mfcs, faller_score,
  minutes_since_open, log_price, log_float, log_mcap, is_d170), extract_features(),
  label_from_outcome() (capturable continuation = MFE>=+8% not fully reversed),
  IntradayContinuer.load().score() -> P(continue) (the eventual live scorer; loads
  nothing until trained = safe).
- experiments.md: NEW scripts/train_intraday_continuer.py -- assembles graded
  rejections, leave-one-day-out CV, compares OOF AUC to the faller-gate baseline
  (rank by -faller_score). SHIP-DISCIPLINE: refuses to write an artifact unless
  n>=60, both classes, AND edge>=+0.03 over the baseline -> a small logistic model
  only earns its keep if it beats the heuristic. Refusing keeps the working gate.
- Validated on synthetic (planted signal + noise faller): OOF AUC 0.865 vs baseline
  0.481 -> edge +0.384 -> SHIPPED; coefficients recovered the signal; scorer
  round-trips (early/low-float/big-gap P=0.765 vs late/large/small P=0.015). 5 new
  unit tests pass. No live-path code (offline scaffold); synthetic artifacts cleaned.
- backlog.md: ready; blocked only on the restart (deploy 179-184) + ~1-2wk graded
  data. Operational loop: finalize_rejection_outcomes.py each EOD; train weekly.

## doc 183 -- 2026-05-29 -- Continuer_v2 mismatch (NOT built) + the real intraday edge

Pierce greenlit "build the Continuer_v2 shadow on momentum picks." I started, then
inspected the model + tested it empirically, and found it's the WRONG tool. Reported
+ pivoted rather than ship garbage.

- experiments.md: Continuer_v2 (data/models/continuer_v2_v3_tuned.pkl, 54 features)
  is an END-OF-d0, MULTI-DAY model (the lottery's horizon) -- ret_open_close_d0,
  close_strength, prior_cont_rate, rank_intra, etc. The intraday momentum bot can
  supply only 12/54 features (22%); the rest default to 0 -> the model floors every
  candidate at ~0.18 (SKIP). EMPIRICAL: scored CGTL with bot-available features ->
  proba 0.176; perturbing missing priors swung only 0.18->0.23. A live shadow would
  be uninformative. NOT BUILT (by design).
- models.md: the momentum bot needs its OWN INTRADAY continuation model (squeeze vs
  pump over 30-60 min), trained on its own decision features + intraday forward
  outcomes. Foundation now in place: feature_logger (ACTIVE, features) + doc-182
  rejection grading (LABELS) + trade_results (entered outcomes).
- d_codes.md: ENRICHED the doc-182 rejection shadow (rejection_outcome_shadow.py)
  with minutes_since_open (DST-robust ET; time-of-day is a primary squeeze/pump
  discriminator), float_shares, market_cap. Faller call site passes float/mcap.
  4/4 tests still green. Write-only, deploys on restart.
- backlog.md: CORRECTED Phase-C roadmap (replaces "shadow Continuer_v2"): (1)
  accumulate ~1-2wk of doc-182 graded data, (2) train a SMALL intraday continuer
  (logistic/GBM, not a 54-feat multi-day ensemble), (3) shadow THAT + A/B vs the
  faller gate, (4) only then flip the staged D170/limit flags to capture continuers.
- LESSON: inspect a model's feature contract + training horizon before wiring it
  into a new process. "A model exists for X" != "it fits process Y."

## doc 182 -- 2026-05-29 -- rejection-outcome shadow (keystone): grade gates before loosening

Pierce greenlit all 4 next-gen fixes. Shipped #1 (the keystone that makes the
other 3 SAFE); staged the rest behind the safety gate.

SEQUENCING PRINCIPLE: instrument -> calibrate -> only THEN loosen. Doc 181 showed
the gates correctly avoid fades; loosening entry (D170, marketable limits) without
a continuation edge = buying those fades. So grade the rejections FIRST.

- experiments.md / d_codes.md: NEW src/shadow/rejection_outcome_shadow.py +
  log_rejection_for_grading(). Wired at the D160 faller block (main.py) and the
  D170 entry-delay reject (entry_delay.py) -- both guarded, write-only, never raise.
  Logs decision-time {ticker,gate,price,ts,mfcs,score,reason,gap,rvol,outcome=null}.
  Env kill-switch SHADOW_REJECTION_GRADING_ENABLED (default true).
- NEW scripts/finalize_rejection_outcomes.py: post-close, fetches each rejected
  name's FORWARD bars, grades block_CORRECT_faded (EOD<=-5%) vs block_WRONG_ran
  (MFE>=+10% & EOD>=0) vs neutral, prints a per-gate scorecard (%correct, %wrong,
  mean EOD return, names every MISSED RUNNER). The calibration signal for the
  faller 0.60 threshold + D170 10%-drawdown limit. No lookahead (outcome filled
  only from strictly-later bars).
- Verified: 4 files compile; 4/4 new unit tests pass; 19/19 D170 tests still pass.
- backlog.md: STAGED (not deployed-active, gated on the scorecard + a flag):
  (1) D170 redesign (pullback-vs-fade + drop the impossible >=4-bullish-agents
  early-entry rule), (2) marketable limits on WIDE_STOP (CMND was 0/2), (3)
  Continuer_v2 shadow on momentum picks (the squeeze-vs-pump edge, currently only
  on the lottery). Deploy these only after the scorecard shows the gates block
  genuine runners.
- ops: RESTART NEEDED (Pierce, elevated) to deploy doc 179+180+182 -- the live
  process (PID 32120) is on b33b853 (doc 178) and runs as the scheduler's elevated
  user (my session got Access denied). Command in doc 182 sec 2.

## doc 181 -- 2026-05-29 -- Friday midday: bug sweep + profit-lost + experiment gap (corrects doc 180)

3 parallel forensic agents + log verification. Midday snapshot ~10:50 ET.

CORRECTION TO DOC 180: "APPS ghost cleaned (qty_drift=0)" was WRONG. qty_drift=0 =
internal qty (970) == broker qty (970), i.e. they RECONCILE, not flat. APPS is a
LIVE 970-share +24% hold (+$1,625 unrealized) — the D91 open-close 403-failed
(cancel->sell race) so it rode the gap. Second straight session the only "profit"
is an accidental unprotected overnight hold.

PROFIT LOST TODAY ~= $0 (zero fills) — and the gates WORKED: the names blocked/
not-filled FADED after open (CGTL $0.51->$0.44 -14%, UMAC $29->$25.32 -13.8%, CODX
-11.3%). Faller gate + D170 correctly avoided ~$1-3K of fades. The problem isn't
loss; it's that the offensive machinery can't fire.

- backlog.md: H1 APPS stop NOT synced to broker (Bug R regression) — software trail
  $8.46 but broker stop $5.88; software-protected intra-cycle, hard-backstop 30% low
  -> between-cycle/crash risk on +$1,625. Drives RECON_LETHAL x82 (shadow) on a REAL
  divergence.
- backlog.md: H2 RUNNING PROCESS IS ON OLD CODE (commit b33b853d = doc 178). doc 179
  (M2) + doc 180 (D249 fix) are on disk but NOT live -> need RESTART. Today's D249
  naked-window + M2-absence are because the fixes aren't deployed.
- backlog.md: H3 D170 entry-delay gate = 20 DEFERRED / 0 APPROVED (dominant 0-fills
  cause). Rejects on max_drawdown_from_open=10% (any volatile gapper) + require
  higher_lows + early-entry needs >=4 bullish agents (impossible under blind funnel,
  UMAC MFCS 0.856 still failed). Correctly avoided fades today but can't approve a
  real squeeze either.
- backlog.md: M1 limit orders don't fill on thin names (CMND 0/2) — passive limit at
  stale eval price + 12s window. M2 LLM CB tripped 39x, news EMPTY 54%.
- experiments.md: SHADOWS BLIND ON SELECTION LAYER. D102 replay (138x) + composite_v0
  (214x) sample decision ABOVE the faller/D170 gates -> can't see the binding
  constraint. inverted_shadow (grades REJECTED names) is DEAD CODE (finalizer
  missing). Continuer_v2 continuation edge wired to LOTTERY not momentum bot. No
  order-fill experiment. RECOMMENDED: (1) wire inverted_shadow to grade faller/D170
  rejections [~80% built], (2) Continuer_v2 shadow on momentum picks, (3) order-fill
  opportunity-cost shadow, (4) faller/D170 live A/B once outcome data exists.

REFRAME: path to 5% is NOT looser gates (pumps fade) — it's (a) a continuation edge
(squeeze vs pump), (b) an entry path that can fill fast names, (c) deliberate
protected holds. The experiments must instrument the gate layer, not scoring knobs.

## doc 180 -- 2026-05-29 -- Friday live analysis + D249 re-arm fix

First live session on docs 177-179. Intraday snapshot ~10:15 ET.

LIVE VALIDATION: 0 slice crashes, 0 phantom-attribution, APPS 970-ghost cleaned
(qty_drift 712->0), 44 D200/D204 DOWNGRADED events firing (UMAC 0.551, CGTL 0.615
admitted at qty_mult 0.50), executor qty_multiplier halving works, M1 recon band
working (equity_drift 0.13%, no lethal spam). D248 cancel-blocking-stops now fires
(was masked by the slice crash on 5/28 — the fixes compound).

- d_codes.md: D249 RE-ARM BUG FIXED. _rearm_protective_stop_from_snapshot called
  client.submit_order(payload-dict) but the real signature is submit_order(symbol,
  qty, side, ...) -> "missing 2 required positional arguments" -> re-arm FAILED ->
  APPS left NAKED ~90s on 5/29 09:30:18 (D313 re-hedged). LATENT until the doc-177
  slice fix made the cancel-blocking-stops path execute. Fix: use submit_stop_order
  (gtc, position_intent='close'). Test masked it (permissive AsyncMock accepted the
  dict) — test updated to the correct signature. 7/7 force-close tests pass.
- backlog.md: FALLER GATE is the new binding constraint on high-MFCS no-catalyst
  names — CGTL (rank #1, MFCS 0.615) faller-BLOCKED (0.669>0.60, illiquid pump
  $641K dolvol, manip 90%). Appears CORRECT (filters pumps). UMAC reduced to 75%.
  CMND (0.265, thin pump) ENTERED — possible faller-consistency issue, flag for EOD.
- backlog.md: D99 cancels slow agents at 29s phase-1 cutoff (93x today) — the
  doc-178 timeout bump doesn't help (orchestrator cutoff is separate); downgrade
  mitigates the blocking. Kelly Tier 1 already = 2% risk so doc-178 ELITE press is
  redundant (correction: doc 178 assumed 1% base). APPS zombie tracker entry +
  orphaned stop -> D231 spam. Cancel-then-close 0.5s wait may be too short.
- regression: yesterday's full unit run = 3721 passed / 41 failed (all pre-existing:
  faller/SEC, README-sync, infra wiring, date-sensitive). ZERO new failures from
  docs 177-180.

## doc 179 -- 2026-05-28 -- follow-on data (M2) + recon safety (M1) + D112 reconsidered

Continuation of doc 178 §3 deferred list. "keep going" (Pierce).

- d_codes.md: M2 — evaluated candidates now subscribe to the live WebSocket at the
  Phase-2 dispatch (main.py, add_symbols on top_candidates). Was: WS subscribed only
  the ~19 premarket symbols + filled tickers → 59x "VWAP unavailable" on post-open
  candidates trading on stale REST data. Idempotent + chunked; ~10 names/cycle.
- d_codes.md: M1 — eod_recon equity check now (1) marks-to-market (adds
  _unrealized_pnl to internal_equity_estimate) and (2) compares against a band
  max($1, 0.5% of equity) instead of a flat $1. Kills ~2,600 false D230 WARN /
  EOD-RECON-SUMMARY lines/day. recon_daemon consumes the same estimate
  (recon_daemon.py:172) so this ALSO fixes its equity-lethal trigger → safe to
  un-shadow. Test updated (test_d230_eod_recon_invariants). 21/21 recon tests pass.
- backlog.md / experiments.md: D112/VCIG CORRECTION — VCIG (+73%) was NOT a clean
  missed winner: it ran on ~0.3x RVOL (near-zero liquidity, untradeable), the router
  is stateless, and the universe already merges gainers (D117). D112's reject was
  defensible; NO change. The catchable misses (NCPL 3.5x RVOL, AMSS) are handled by
  doc 178's catalyst downgrade. Corrects docs 176/177's VCIG framing.
- backlog.md: still deferred — H2 (shutdown flag wipe, needs date-keyed state),
  D106 (VWAP-anchored exit). premarket_research Phase-0 universe could add gainers
  (noted, low value, not done).

## doc 178 -- 2026-05-28 -- full-send posture inversion (path-to-5% changes)

Pierce chose "full send — all tiers ON for Friday". The selection/exit/sizing
posture inversion from docs 176/177, shipped behind env flags (default ON) with
guardrails. SEPARATE commit from doc 177's mechanical fixes (revertable as a unit).

- d_codes.md: D92 LLM Tier-2 timeout 15s→25s (news_agent EMPTY 67%→target <20%).
  Revert LLM_LITELLM_TIMEOUT_TIER2=15.
- d_codes.md: D200/D204 catalyst+news gates — DOWNGRADE-not-BLOCK on MFCS>=0.45
  (qty_multiplier=0.5) instead of hard veto. Admits the no-news squeezes the
  thesis targets (NCPL +38.5%, AMSS) at half size. New UniverseConfig flags
  catalyst_gate_downgrade_*. Revert UNIVERSE_CATALYST_GATE_DOWNGRADE_HIGH_MFCS=false.
- d_codes.md: ALPACA EXECUTOR FIX — qty_multiplier was applied ONLY on the T2
  wide_stop arm; silently no-op'd the doc-178 downgrade on the default tight_stop
  arm. Now applied for any arm when mult<1.0 (tight_stop default 1.0 → T2 unchanged,
  33 T2 tests pass).
- d_codes.md: D163 trail WIDENED — activation 2%→6%, trail-of-gain 50%→30%,
  min-distance 2%→5%. Revert TRAIL_*. (LFS exited +0.7% on a +28% run under 2%.)
- d_codes.md: D122 exit override — min_confidence 0.7→0.85, min_strategies 1→2,
  EXCLUDED gratitude+alpha_oracle (fired on winners: BB +1.8%, UMAC −2.2% vs +26%).
  Revert EXIT_PARALLEL_*.
- d_codes.md / models.md: Tier-4 ELITE sizing press — MFCS>=0.50 → risk_per_trade
  1%→2% (max(), never reduces Kelly). New ExecutionConfig elite_sizing_* flags.
  Revert EXEC_ELITE_SIZING_PRESS_ENABLED=false.
- backlog.md: daily_drawdown_limit 5%→3% (guardrail for the inversion).
  max_positions confirmed env-pinned at 8 (NOT 3 — corrects doc 176); slot-limit
  raise moot.
- backlog.md: DEFERRED (documented in doc 178 §3): H2 shutdown-flag wipe, D112
  RVOL re-eval, D106 VWAP exit, M1 recon tolerance, M2 WebSocket dynamic sub.

Verified: all compile; 126 sizing/T2/executor unit tests pass; touched-path
integration 77 pass / same 10 pre-existing failures (orchestrator.py:312 rot).

## doc 177 -- 2026-05-28 -- today's run: 4 bugs fixed + the blind funnel

Independent re-verification of 175/176 + 3 forensic agents (phantom-P&L
learning-corruption / selection hard-counts / fresh-eyes sweep). Today
(5/28) reported +$1,175.94 but BROKER-realized -$269.56 — the delta is a
970-share APPS ghost's overnight MTM booked as realized P&L, which had
poisoned the BOCPD/Kelly learning corpus.

FIXED (mechanical/accounting, shipped):
- d_codes.md: D73 — `body[:120]` slice crash in the broker error handler
  (alpaca_client.py:1266) raised INSIDE the 403 handler, masking the real
  error and DEFEATING Phase-A cancel-blocking-stops detection (it matches
  "40310000", never a slice repr). Left UMAC unprotected + blocked APPS
  tranches. Fix: `str(body)[:120]` + regression test
  (test_alpaca_client.py::TestD177TradingPostErrorHandling).
- d_codes.md: D76 — EOD close used raw client.close_position and FELL
  THROUGH on 403 to book phantom P&L via close_with_attribution. D76 is
  the close site Phase A (doc 175) missed. Fix: route through
  attempt_close_with_status_check(cancel_blocking_stops_first=True); book
  P&L ONLY on a confirmed broker close.
- d_codes.md / models.md: BOCPD/Kelly corpus poisoned by 2 phantom rows
  (APPS +1426 5/28, LFS +1403 5/27). Tagged infrastructure_contaminated +
  added to KNOWN_CONTAMINATED (pretrain_bocpd_prior.py). mu_edge corrected
  -128.60 → -202.08 (the loss-regime kill-switch was being desensitized).
- d_codes.md: debate max_debate_attempts=0 confirmed INTENTIONAL (D100), not a bug.

backlog.md (filed, not yet fixed): H2 shutdown wipes D95 recovery flags;
H3 Tier-2 15s timeout → news_agent EMPTY 67% → D200/D204 block on data
ABSENCE (the blind funnel — #1 selection reframe); M1 $1 recon tolerance
→ 711 shadow RECON_LETHAL (would flat-all+halt in live); M2 fixed
19-symbol WebSocket sub starves post-open candidates of real-time data.

THESIS REFRAME: doc 176 = posture inverted. 177 adds: the bot was BLIND
~2/3 of evals (news timeouts), so D200/D204 blocked on absence, vetoed its
best ideas (NCPL 0.525 ran +38.5%, AMSS 0.546), and the 2 names it traded
(UMAC, SPRC) weren't even in its top-6 BUY verdicts.

## doc 176 -- 2026-05-28 -- Mariana Trench audit: why not 5%

Deep 3-agent parallel audit (sizing / selection / exits) +
cross-verified log/code trace. The keystone strategic finding.

CORE THESIS: the failure is MULTIPLICATIVE across three layers, not
located in any one. capture = fill-rate(~2%) × deployment(~6% equity)
× move-capture(~5%) = 0.14%/day. The 36x gap to 5% factors as
2 × 2.5 × 8 across the three layers. Fixing one barely moves the
product; all three must invert together.

DEEPER REFRAME: momentum-x is a long-fat-tail strategy (gap-up
momentum) wearing a short-vol costume. Every default — catalyst
vetoes, consensus gates, halved fixed-risk sizing, 50%-of-gain
trailing stops, single-strategy exit overrides — clips the exact
right-tail the thesis exists to harvest. It's an excellent capital-
preservation machine pointed at the fattest-tail prey in equities.

KEY EVIDENCE:
- Selection: 217 BUY-verdicts/3 days → ~2 fills/day. Biggest movers
  (QTEX +86%, VCIG +73%, CODX +46%) ARE evaluated but die at D200
  catalyst + D124/D101 consensus because no-news low-float squeezes
  produce all-NEUTRAL agents → MFCS ~0.10 → reject. $16.6k-$27.9k
  missed alpha in 3 days on $5k hypothetical positions.
- Sizing: the elaborate D96 chain is DEAD CODE; executor uses
  fixed-risk 2%÷stop (alpaca_executor.py:239,254) + T2 0.5x halving.
  Peak deployment all week = 12% of equity. A +28% runner caps at
  ~$600-1200 P&L structurally.
- Exits: D163 trail (50%-of-gain, 2% activation) + D122 single-
  strategy override cut winners at +0.3% to +1.8% on +10-28% movers.
  ~$8,100 left on table in 3 days. APPS exited +0.3% via D163 then
  ran +20% next day — proof the trail is the problem.

PRESCRIPTION (Phase B posture-inversion on ELITE/HIGH tier only,
keep fortress for the body):
- Exits: D163 activation 2→6%, trail 50→30%, min-dist 2→5%; D122
  require 2 strategies + conf 0.85, exclude gratitude/alpha_oracle;
  volume_fade weight 0.12→0.04.
- Selection: D200 downgrade-not-block at MFCS HIGH+; exempt
  risk/manipulation agents from D124 bearish count.
- Sizing: reconnect position_size_pct OR raise fixed-risk to 4-5%
  on ELITE; position limit 3→5.
- Phase C: wire Continuer_v2 shadow→Kelly multiplier (the
  continuation edge on no-news squeezes).

Filed 5 Tier-1 backlog items (one per prescription).

Reconciliation caveat noted: a fill-marker grep mismatch + bar-
recording price-basis difference; funnel + exit conclusions stand.

---

## doc 175 -- 2026-05-28 -- Path to 5% daily: 403 ghost root-cause fix

Opus 4.8 takes over. New ship-doc directory: `docs/research-log/`.
Pre-commit hook extended to cover both research-log and research-log.

doc 175 (path_to_five_percent_daily.md) is the master deep-dive:
- T2 week-1 result: +$2,395 across 3 sessions = 0.54%/day. 5% target
  is 9.3x higher.
- Funnel: ~100 BUY verdicts -> ~2 fills -> ~1 broker-realized close
  per day. 98% of opportunities lost.
- Root cause: EVERY winning close hits HTTP 403 because OTO child
  stop reserves inventory. Each winner becomes a ghost. Tue LFS,
  Wed APPS, Thu UMAC — same pattern, 5 distinct symbols, 10 events.
- Fix exists: `attempt_close_with_status_check(...
  cancel_blocking_stops_first=True)` cancels the child stop, retries
  close, re-arms stop if close still fails. Already used by D78
  SMART EXIT successfully.
- Leak: 5 other close paths bypass the wrapper, calling
  `client.close_position()` directly.

Phase A shipped today (mechanical fix to 3 of the 5 leaky paths):
- `bridge.py:691` D91 STEP 2 (overnight close at market open)
- `fast_path.py:876` D85 fast-path cancel-after-fill close
- `eod_failsafes.py:216` D242 EOD_FORCE_CLOSE ghost cleanup

Deferred to Phase B-D (strategic, post-Move-2):
- D163 trail-widen at +3/+6/+10% breakpoints (let winners run)
- D106 PROMOTIONAL_EARLY VWAP-anchored exit (vs 10:30 hard)
- D200-E4 CATALYST downgrade-not-block when MFCS HIGH+
- D101/D124 CONSENSUS split no-signal from real-bearish
- Position limit 3 -> 5 with halved per-slot

Tests: 173 pass, 7 fail. All 7 failures pre-existing (confirmed
reproduces on prior commit 39e38c9).

---

## T2 Day-1 retro + Day-2 morning -- 2026-05-27 06:15 ET -- L2 saved the day

User-requested deep dive on yesterday's first real T2 trading day
plus this morning's pre-market. Major findings.

YESTERDAY (Tuesday 2026-05-26, T2 Day-1):
* T2 A/B arm assignment WORKING: 20+ assignments first 4 min, ~50/50
  split per MD5 hash. No silent-fallbacks. Both fills were wide_arm
  (BB and LFS, both 0.5x sizing).
* 5 orders submitted, 2 filled (40% fill rate).
* BB clean exit +$183.73 (1.8%) via D78 SMART EXIT (D246 retry
  succeeded on attempt 2/3 after initial 403 Forbidden).
* LFS GHOST-STUCK at broker: D163 TRAILING got initial 403, did
  NOT retry, journal recorded +$50.12 close but broker still held
  2506 shares. D241 EOD cancelled the orphan SELL order, D242
  EOD_FORCE_CLOSE failed (403 again) -- position remained open
  overnight.
* D278 carry-overnight verified live in production for both fills.
* D222 PNL_RECON divergence -$61.39 (Bug #13 recurrence: exit path
  closed in journal without broker confirmation).
* D313 hedge_integrity_watcher: 1930 checks, 0 violations intraday.

THIS MORNING (Wednesday 2026-05-27 04:30 ET):
* L2 SAFETY NET SAVED THE SYSTEM. D313 hedge_integrity_watcher
  detected LFS unhedged at 04:30:11 ET, waited 60s tolerance, then
  AUTO-SUBMITTED emergency stop at 04:31:13 ET. Position hedged
  in 61 seconds wall-clock with zero human intervention. This is
  the runbook scenario from doc 174 firing for real.
* Recovery flow worked end-to-end: 14h kill yesterday 18:30 ET ->
  fresh boot today 04:30 ET, MOMENTUM_T2_ENABLED=1 persisted.
* New Tier-S bug: D313 EMERGENCY_STOP_SUBMITTED doesn't write the
  broker oid back to position.stop_order_id, causing 180+ D230
  RECON_WARN STOP warnings (~1700/day rate). Position IS hedged;
  reconciler is wrong. Filed `D313_emergency_stop_writeback_oid`.

2 new backlog items:
* `D313_emergency_stop_writeback_oid` (Tier-S) -- 1-line fix
* `LFS_ghost_close_d246_retry_consistency` (Tier-1) -- audit why
  D163 trailing-stop didn't use D246 retry like D78 smart-exit

Pending risk: at 09:30 ET today, D91 will trigger
_close_overnight_position for LFS. If yesterday's race recurs,
re-ghost possible. Will monitor closely.

---

## Pre-open deep-dive -- 2026-05-26 06:30 ET -- 8 findings (2 Tier-1, 4 Tier-2, 2 Tier-3)

User-requested deep dive on T2 real launch day. Systematic audit
across bot state, log volume, async coverage, shadow outputs, test
coverage, and pattern-class bug searches. Recovery flow worked
overnight (yesterday's 14h kill -> Task Scheduler relaunch with
MOMENTUM_T2_ENABLED=1 persisting from Monday's fix).

State: PID 26692, T2=True, commit 76f8acf6, 119min uptime,
349MB RAM / 131 threads / 598 handles. Zero errors. 24 candidates
per scan, ~70s cadence. Discipline checks: ZERO TODO/FIXME/HACK
in critical paths, ZERO bare `except:`, ZERO `except: pass`.

Findings:
  Tier-1:
    * Websocket_trade_updates_disconnect_root_cause -- 9 disconnects
      in 2hr pre-market (every 5-9 min), 2s recovery. Risk of
      missed FILLs at market hours. Need REST polling-backup
      verification + server-side timeout investigation.
    * Async_timeout_coverage_critical_paths -- 19 async defs / only
      5 wait_for guards. Specific external-API gaps in
      orchestrator.py: _fetch_sec_filings/_fetch_vix/_fetch_spy_return
      /_fetch_options_summary. SEC EDGAR 500s already noise; if they
      escalate to hangs, orchestrator blocks indefinitely.
  Tier-2:
    * Options_provider_INFO_to_DEBUG -- 2,415 spam lines/2hr
    * Live_dashboard_suppress_unchanged -- 233 redundant
      zero-state prints
    * D64_session_state_warning_wording -- correct behavior logs
      as WARNING "failed"
    * T2_arm_assignment_fail_should_halt_entry -- silent-non-fatal
      try/except wraps the A/B assignment; if it fails, trade
      enters un-assigned (breaks experiment integrity)
  Tier-3:
    * Shadow_output_emission_verify_post_market -- no shadow files
      since 5/22; verify after first T2 session
    * (no item) Test coverage observations: t2_arm_assignment +
      D315 both have tests; pattern-class audit clean

- **_recovery_log.md** (updated): full entry with all 8 findings
- **backlog.md** (modified): 6 new items, linter passes 0/0

Bot continues healthy through pre-market. Monitoring loop active.

---

## Recovery drill -- 2026-05-25 09:35 ET -- T2 launch-day rescue

T2 went live at 09:30 ET as planned. Autonomous monitoring loop's
first tick (09:35 ET) caught that MOMENTUM_T2_ENABLED was unset at
the 04:30 ET launcher boot -- bot was running legacy single-arm path
with D315 BOOT_SELF_TEST showing T2=False. **5-min recovery executed
in auto-fix mode**:

  1. Set env var at User scope (no UAC).
  2. Stop-Process FAILED (Access Denied -- bot is elevated via Task
     Scheduler RunLevel=Highest).
  3. Pivoted to `Stop-ScheduledTask` -> succeeded without UAC.
  4. Removed stale lock; `Start-ScheduledTask` -> new PID 50776,
     D315 shows T2=True.

- **_recovery_log.md** (NEW): append-only intervention log for the
  drill. Entry 1 captures the full diagnosis + fix + lesson.
- **backlog.md** (modified): 2 new Tier-S items filed from drill:
  - `Launcher_required_env_check`: daily_paper_trade.ps1 must abort
    boot if MOMENTUM_T2_ENABLED is unset during the T2 window. The
    default-off behaviour was the failure mode that made the bug
    invisible (everything looked healthy except the experiment).
  - `Runbook_elevated_kill_pattern`: document the
    Stop-ScheduledTask + Start-ScheduledTask workaround for
    Pierce-elevated tasks (per Pierce's prior loop guidance,
    Stop-Process fails with Access Denied without UAC).

**Operational meaning**: D315 boot self-test (shipped doc 174) paid
for itself on its first production run. Without it, the bot would
have looked fully healthy while silently running the wrong code path.

Monitoring loop continues through 22:00 ET.

---

## Dead-code audit -- 2026-05-24 night -- ghost machinery exposed

Phase 4 of the SYSTEM_MAP genesis. Built `_dead_audit.py` to catch
Pierce's TrailingStopManager pattern (comments referring to functions
that no longer exist) at the structural level. First run surfaced more
than expected -- the ghost machinery hypothesis was correct.

- **_dead_audit.py** (NEW, ~600 LOC):
  - 3 checks: dead comment refs (Foo.bar / bar() in comments and
    docstrings that don't resolve to any defined symbol), deprecated
    D-code uses, broken intra-repo imports
  - Symbol pool: walks .py files in src/ + config/ + main.py + scripts/,
    collects function names, class names, dataclass/Pydantic field
    declarations, `self.X = ...` assignments inside methods
  - Whitelists: PYTHON_BUILTINS, PROSE_WHITELIST (TODO/PASS/STOP/etc.),
    THIRD_PARTY_MODULES, FILE_EXTENSIONS (DD.jsonl etc.),
    DOCSTRING_EXAMPLE_NAMES (fetch_data/do_work)
  - Output: human report (default), --json for machine, --strict to
    exit 1 on any finding (for CI)
  - Runtime: ~3 seconds on 425-file scan

- **_dead_audit_findings.md** (NEW):
  - Triaged report from first full audit
  - **23 total findings**: 13 broken imports, 0 deprecated D-codes,
    10 comment refs (7 real, 3 false-positive notes)
  - Headline: `src/selection_arena/` is half-deleted -- __init__.py
    imports 3 non-existent modules. Importing the package crashes.
  - main.py `cmd_build_scenarios` / `cmd_record_scenarios` import
    scenario_builder + scenario_recorder modules that don't exist.
    ADR-025 Phase 3 backtest work was apparently abandoned mid-flight.
  - 4 real docstring lies in slippage_calibration, stop_decision_log,
    recon_daemon, replay_engine (described APIs that don't exist)

- **backlog.md** (modified):
  - 5 new Tier-2 items filed from audit findings:
    `Selection_arena_cleanup`, `ADR025_backtest_command_cleanup`,
    `Docstring_lies_fix`, `Scanner_universe_fallback_cleanup`,
    `Wire_dead_audit_to_CI`
  - Move_2_decision_gate.blocks updated with bidirectional pointers
    to the two cleanup items it gates (post-T2-ratchet timing)

**Operational meaning**: production T2 path is unaffected by any of
the findings. All 13 broken imports + 4 docstring lies are in research
scaffolding or abandoned CLI commands. Fixes deferred until after
T2 ratchet (Thursday) per code-freeze discipline.

**The discipline worked.** First time the audit ran, it found work
that should have been deleted months ago. From this commit forward,
that work is structurally enforced to be cleaned up rather than
forgotten again.

**Deferred to Monday morning**:
- Wire `_dead_audit.py --strict` into CI as warning-only

---

## SYSTEM_MAP linter -- 2026-05-24 late evening -- discipline becomes code

Phase 3 "Full blast" extension. The pre-commit hook enforces atomicity
between ship docs and changelog.md, but the SYSTEM_MAP content itself
had no structural validation -- TOML schemas were aspirational. This
commit closes that loop: `_linter.py` now parses every TOML block and
enforces 10 invariants across d_codes / experiments / backlog.

- **_linter.py** (NEW, ~530 LOC):
  - `extract_toml_blocks()`: regex-extracts fenced ```toml blocks from
    markdown, concatenates, parses with tomllib (Python 3.11+)
  - d_codes invariants (6): status-supersession consistency,
    bidirectional supersedes/superseded_by pointers, dependency
    resolution (other D-codes or src/scripts/tests paths), category
    mandatory for ACTIVE, doc references resolve to research-log/N.md,
    no orphan ACTIVE codes (must have docs OR depends_on OR gates)
  - experiments invariants (2): decision_date mandatory for
    SHADOW/LIVE; SHADOW with past decision_date fails as a sunk-cost
    alarm (the whole reason we wrote it down)
  - backlog invariants (2): blocks/blocked_by bidirectional agreement;
    priority must be S/1/2/3
  - Schema example placeholders (DXXX_EXAMPLE / Example_*_Schema)
    are skipped by `_is_example_identifier()` so they don't trip
    the orphan check
  - Emits 3 derived artifacts on success:
    - `_by_category.md`: D-codes grouped by category
    - `_by_status.md`: D-codes grouped by status
    - `_dependency_graph.dot`: Graphviz source for visualizing the
      D-code dependency graph (Task #38 bundled in)

- **d_codes.md** (modified):
  - Schema example block renamed from `[D310]` to `[DXXX_EXAMPLE]`
    -- the linter caught a real duplicate-key TOML collision with
    the actual `[D310]` entry. Exactly what discipline is for.
  - D317 (event-sourced trade journal) docs changed from `["175"]`
    to `[]` because doc 175 doesn't exist yet (linter caught broken
    doc ref)

- **experiments.md** (modified):
  - Schema example renamed to `[Example_Experiment_Schema]` with
    decision_date `2099-12-31` so the past-date SHADOW check doesn't
    false-positive on the documentation example

- **backlog.md** (modified):
  - Schema example `[Example_Backlog_Schema]` added with explicit
    placeholder identifier
  - Five real bidirectional pointer inconsistencies fixed across:
    D310_L3_FSM_or_delete, Move_2_decision_gate,
    Event_sourced_trade_journal, Cascade_paper_data_layer,
    T3_production_cutover. Pierce's "ghost machinery" warning was
    structurally correct -- the pointers had drifted from semantics.
    Move_2 informs D310_L3 shape but doesn't block it (T3 cutover
    is what's blocked by both); reworked the graph to match.

**First-run result**: 0 errors, 0 warnings against current SYSTEM_MAP.
**Operational meaning**: any future SYSTEM_MAP edit that breaks an
invariant will fail CI (or the pre-commit hook when wired into it
tomorrow morning).

**Deferred to Monday**:
- Wire `_linter.py` into `.githooks/pre-commit` (currently only the
  atomicity check runs)
- CI step that calls the linter on PRs touching `docs/SYSTEM_MAP/**`

---

## SYSTEM_MAP fill-out -- 2026-05-24 evening -- under-budget extension

Pierce gave 90-min budget; came in at 10 for the skeleton. Extended
the runway to fill the operationally-critical content while context
was still loaded.

- **d_codes.md**: populated with 25 worked examples (the operationally-
  critical codes). Covers all D-codes referenced 20+ times in src/ OR
  that gated major shipping decisions. Includes:
  - LLM layer: D91, D92, D101, D116
  - Stop/position: D142, D146, D147, D163, D165
  - Carry overnight + Kelly: D278, D281, D290, D291.5
  - Defense/observability cluster: D277, D293, D294, D295, D297,
    D304, D308, D310, D311, D312, D313, D314, D315
  - DEPRECATED/REVERTED: D26, D164, D293a
  - FILED: D293.8, D316 (external watchdog), D317 (event-sourced
    trade journal)
  - Smoke-test verification block added (both negative + positive
    pre-commit hook paths verified end-to-end)
- **architecture.md**: full 11-layer pipeline diagram + defense layer
  wraparound + production vs shadow paths + latency budget + most-
  touched subsystems table. Cross-references models.md, monitoring.md,
  data.md, d_codes.md, experiments.md.
- **models.md**: per-layer model details for Layers 3-10 (agent
  weights, MFCS formula, debate roles, meta-scorer tier cascade with
  win rates, Continuer v2 features, BOCPD algorithm, Kelly tiers,
  Composite v0 architecture). Calibration data + recent enhancement
  D-codes per layer.
- **monitoring.md**: alert taxonomy (CRITICAL/HIGH/INFO + retry
  budgets), watcher inventory (Track B, L2, L2_WATCHDOG, D314 retry,
  Phase 3 keeper), reconciliation table, D315 boot self-test contract,
  runbooks index, anti-patterns, code freeze status, ratchet criteria.
- **glossary.md**: 100+ terms defined (core trading, momentum-x
  specific, defense/safety/observability, pipeline + infra, files &
  locations, acronyms).

**Pre-commit hook smoke-tested end-to-end:**
- Negative case (no changelog update): hook REJECTED with format-
  example error message + bypass note ✓
- Positive case (changelog staged): hook ACCEPTED, commit went through ✓

Discipline is **enforced structurally**, not by memory.

---

## SYSTEM_MAP genesis -- 2026-05-24 -- skeleton committed

- INDEX.md created (1-page orientation + Monday launch sequence)
- changelog.md seeded (this file)
- d_codes.md schema header committed (registry TOML format, validation invariants)
- experiments.md populated with 11 entries:
  - T1 (PASS, stop-widening shadow), T2 (LIVE, gated Monday)
  - 3 SHADOW with decision_dates: Continuer_v2 (6/25), BOCPD (6/15),
    Composite_v0 (6/30) -- each declares disposition_if_pass and
    disposition_if_fail
  - 5 historical PASS/MARGINAL/REVERT: D293, D291.5, D290, D162 E1,
    D281
  - FILED: Move 2 decision (5/28), Replay regression seed (5/26),
    Single LLM tool-using agent (Q3)
- backlog.md: Tier S/1/2/3 priority + bidirectional pointers
  - S: D310 L3 + External watchdog + Move 2 decision + T3 cutover
  - 1: Event-sourced trade journal (promoted per Pierce), cascade
    paper data layer, replay regression seed, 124-window tick audit,
    D293.8 retest, live-vs-shadow delta
  - 2: SMS tee, L2 polling tighten, dead-comment grep, min-price
    floor, Together fallback, z-score gating, schema_version field
  - 3: single-LLM agent, vision LLM, RAG news/SEC, KB, BOCPD wire-in,
    regime sizing, chaos client, mutation testing, cascade paper
- _linter.py stub (Monday afternoon implementation)
- .githooks/pre-commit installed: ship doc + changelog.md atomicity

**Discipline binding:** from this commit forward, any new
`docs/research-log/N.md` ship doc that lands without a corresponding
changelog.md entry will be REJECTED by the pre-commit hook. Bypass
via `--no-verify` is logged in reflog and must be followed by a
SYSTEM_MAP update commit within 24h.

**Initiator:** Claude Opus 4.7 (1M ctx) + the operator review cycle.
**Predecessor docs:** 170 (weekly deep dive), 171-174 (T2 rollout +
safety hardening + Pierce's reviews including band-fix Monday-blocker).
