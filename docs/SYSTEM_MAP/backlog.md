# backlog.md — prioritized work items

One TOML block per item. Priority tiers:

- **S** — Production-critical. Blocks T2/T3 progression OR has known production risk.
- **1** — High-value enhancement. Meaningfully changes P&L or signal quality.
- **2** — Quality of life / observability.
- **3** — Research. Exploratory, no near-term ROI.

`blocks` and `blocked_by` are bidirectional pointers (linter checks
they agree). `target_date` is aspirational; hard dates go in
`experiments.md`.

**Schema example** (real entries below):

```toml
[Example_Backlog_Schema]
priority = "S"               # S | 1 | 2 | 3
effort_hours = 6
filed_in = ["169", "171"]
blocks = ["T3", "Move2"]
blocked_by = []
target_date = "2026-06-15"
description = "FSM rewrite OR deletion via Move 2 (data-dependent)."
```

---

## Tier-S — blocking

```toml
[BOCPD_action_status_audit]
priority = "S"
effort_hours = 2
filed_in = ["_recovery_log.md (2026-05-25 09:48 ET)"]
blocks = []
blocked_by = []
target_date = "2026-05-26"
description = """
experiments.md [BOCPD_kill_switch] claims one_line = "wired to
logging but never to action", but src/execution/alpaca_executor.py:203
reads self._kelly_governor.current_multiplier() and HALVES position
size for 5 trades after D223 BOCPD_BREAK. The action wiring is in
production. Either (a) BOCPD has been promoted from SHADOW to LIVE
and experiments.md is stale -- update status + remove disposition_if_*
since the disposition has already executed; OR (b) the kelly_governor
wiring was added unintentionally and should be guarded behind a
feature flag until BOCPD passes its gating metric (hit rate >= 60%
on detected regime breaks over 30d). Decision required from Pierce.
Discovered by Day-1 monitoring drill 09:48 ET.
"""

[Options_provider_INFO_to_DEBUG]
priority = "2"
effort_hours = 0.5
filed_in = ["_recovery_log.md (2026-05-26 06:30 ET)"]
blocks = []
blocked_by = []
target_date = "2026-05-29"
description = """
src.data.options_provider emits 2,415 lines at INFO level in 2hr
pre-market (~20/min). Most are "Parsed N option contracts for chain
(from M raw)". One-line demotion to DEBUG would cut ~30% of log
volume over a full session. No information loss -- INFO summary
already captured elsewhere.
"""

[Live_dashboard_suppress_unchanged]
priority = "2"
effort_hours = 1
filed_in = ["_recovery_log.md (2026-05-26 06:30 ET)"]
blocks = []
blocked_by = []
target_date = "2026-06-01"
description = """
src.monitoring.live_dashboard prints the full ASCII dashboard every
~30s pre-market, even when EVERY field is identical ("Trades: 0.0,
Errors: 0.0, Open: 0.0"). 233 redundant blocks in 2hr. Suppress when
state hash equals prior tick's hash; print only on change OR every
5 min (whichever first) as a heartbeat.
"""

[D64_session_state_warning_wording]
priority = "2"
effort_hours = 0.25
filed_in = ["_recovery_log.md (2026-05-26 06:30 ET)"]
blocks = []
blocked_by = []
target_date = "2026-06-01"
description = """
D64 logs WARNING "Backup recovery failed (state=loaded, stale=True)
-- falling through to fresh start" for what is correct behavior (a
stale-by-design backup was correctly discarded). Reads like a bug
to anyone scanning the logs. Reword to INFO + "Backup stale by
design (last_run=YYYY-MM-DD, today is a new trading day) -- fresh
start".
"""

[T2_arm_assignment_fail_should_halt_entry]
priority = "2"
effort_hours = 0.5
filed_in = ["_recovery_log.md (2026-05-26 06:30 ET)"]
blocks = []
blocked_by = []
target_date = "2026-05-29"
description = """
orchestrator.py:1747 wraps assigned_arm_for_verdict() in try/except
that logs WARNING "D310.T2 arm assignment failed (non-fatal)" and
continues. If hash fails for any reason, the trade ENTERS WITHOUT
ARM ASSIGNMENT -- silently breaking the A/B experiment. During the
T2 window, this should HARD ABORT the entry (don't trade rather
than trade un-assigned). Single line change: re-raise after log.
"""

[Shadow_output_emission_verify_post_market]
priority = "3"
effort_hours = 1
filed_in = ["_recovery_log.md (2026-05-26 06:30 ET)"]
blocks = []
blocked_by = []
target_date = "2026-05-27"
description = """
No 2026-05-25 or 2026-05-26 files in data/shadow/, data/features/,
data/journals/, data/shadow_stops/. Last writes are 2026-05-22.
Likely correct (event-triggered, no fills since Friday) but should
be verified after T2's first real session (today) closes. If no
files appear by EOD 5/26 despite trades occurring, that's a real
write-failure bug. If files do appear, document the event-trigger
contract in monitoring.md so future operators don't worry.
"""

[Holiday_calendar_check_pre_launch]
priority = "2"
effort_hours = 0.5
filed_in = ["_recovery_log.md (2026-05-25 11:55 ET)"]
blocks = []
blocked_by = []
target_date = "2026-06-01"
description = """
T2 was scheduled to launch Monday 2026-05-25 -- which is Memorial Day,
a US market holiday. The bot correctly idled all day via D79, but the
plan cost a full session of T2 data and shifted Move 2 Thursday gate
from 3 sessions to 2. Defense: any ship doc that schedules a launch
must call src.scheduling.market_calendar.is_trading_day() before
committing to the date. Add to the pre-commit hook? Or just to the
ship-doc template. Cheap mechanical fix; would have caught this
during doc 169 draft.
"""

[EOD_recon_naming_or_cadence]
priority = "2"
effort_hours = 1
filed_in = ["_recovery_log.md (2026-05-25 09:48 ET)"]
blocks = []
blocked_by = []
target_date = "2026-06-01"
description = """
src.monitoring.eod_recon fires every 30 seconds during regular
session hours, logging 2 INFO lines per tick ("EOD RECON EQUITY OK"
+ "EOD RECON SUMMARY"). The module is named "EOD" (End-Of-Day) but
runs continuous-recon cadence. Either rename to continuous_recon to
match behavior, OR change schedule to fire only at 16:00 ET (which
is what the name implies). ~1500 redundant log lines per session.
Equity numbers themselves are correct; this is naming/cadence
hygiene only.
"""

[Launcher_required_env_check]
priority = "S"
effort_hours = 1
filed_in = ["_recovery_log.md (2026-05-25 09:35 ET)"]
blocks = []
blocked_by = []
target_date = "2026-05-26"
description = """
scripts/daily_paper_trade.ps1 must ABORT BOOT if MOMENTUM_T2_ENABLED
is unset during the T2 window (2026-05-25 -> 2026-06-04). On 2026-05-25
launch day, the scheduled task booted at 04:30 ET without the env var
set, silently fell back to legacy single-arm logic, and the bot looked
fully healthy (process alive, heartbeat fresh, zero errors) while
running the wrong experiment. Caught only by D315 BOOT_SELF_TEST. The
default-off behaviour is the bug -- fix is to add a required-env
guard at the top of the launcher with a hard exit. Add a sibling
guard for MOMENTUM_EXIT_POLICY (also business-critical post-D278).
"""

[Runbook_elevated_kill_pattern]
priority = "S"
effort_hours = 0.5
filed_in = ["_recovery_log.md (2026-05-25 09:35 ET)"]
blocks = []
blocked_by = []
target_date = "2026-05-26"
description = """
Add to monitoring.md runbook: when MomentumX-PaperTrading task runs
with RunLevel=Highest (Pierce-elevated), Stop-Process from a
non-elevated shell fails with Access Denied. Reliable pattern is
Stop-ScheduledTask + Start-ScheduledTask -- goes through the
scheduler service, no UAC prompt, works for any task the user has
rights to manage. Demonstrated successfully 09:38 ET today.
"""

[D310_L3_FSM_or_delete]
priority = "S"
effort_hours = 6
filed_in = ["169", "170", "171"]
blocks = ["T3_production_cutover"]
blocked_by = []
target_date = "2026-05-30"
description = """
TrailingStopManager has dead callbacks (since 2026-02-02 commit
ff865b7c). Two paths:
  (a) FSM rewrite: explicit PENDING_CANCEL -> CANCELLED ->
      PENDING_SUBMIT -> ACTIVE state machine with retry-on-reject.
  (b) DELETE: if Move 2 ships (tight arm migrates to L1 standalone-
      stop pattern), TrailingStopManager has no callers and can be
      removed entirely.
Decision branch depends on T2 data Mon-Wed.
"""

[External_process_watchdog]
priority = "S"
effort_hours = 3
filed_in = ["173", "174"]
blocks = []
blocked_by = []
target_date = "2026-05-27"
description = """
Closes last in-scope SPOF: main.py crash kills every watcher including
L2_WATCHDOG. PowerShell scheduled task running every 60s, checks mtime
on heartbeat.json (written by L2 each tick). If stale > 3 min: post
Discord webhook directly (bypassing the dead bot) + restart MomentumX-
PaperTrading scheduled task.
"""

[Move_2_decision_gate]
priority = "S"
effort_hours = 0                          # decision, not implementation
filed_in = ["171", "172", "174"]
blocks = ["Selection_arena_cleanup", "ADR025_backtest_command_cleanup", "Strategy_enhancement_audit_post_T2_week1"]  # post-T2-ratchet cleanup + strategy review deferred until after this gate
blocked_by = []                          # T2_3_session_data is a milestone, not a backlog item
target_date = "2026-05-28"
description = """
Decision on Thursday 5/28: if 0 D313 HEDGE_VIOLATION across 3 T2
sessions (Mon/Tue/Wed), migrate tight arm to L1 standalone-stop
pattern + delete TrailingStopManager. Otherwise, hold the asymmetric
A/B setup another week for cleaner data.
"""

[T3_production_cutover]
priority = "S"
effort_hours = 8
filed_in = ["169", "171"]
blocks = []
blocked_by = ["D310_L3_FSM_or_delete"]
target_date = "2026-06-08"
description = """
Replace D142 Phase 1 1.5% override entirely; both arms use ATR-based
stops via L1 standalone-stop pattern. T2 success criterion: 10
sessions, wide > tight by 1.5sigma, 0 EMERGENCY_STOP_FAILED, D310
RESUBMIT_NO_CALLBACK trending to 0.
"""
```

---

## Tier-1 — high-value enhancement

```toml
[Strategy_enhancement_audit_post_T2_week1]
priority = "1"
effort_hours = 16
filed_in = ["_recovery_log.md (2026-05-27 07:00 ET)"]
blocks = []
blocked_by = ["Move_2_decision_gate"]
target_date = "2026-06-01"
description = """
T2 Day-1 missed-profit analysis identified 5 specific strategy
enhancements that could have captured an estimated $2,700-$4,000
of additional alpha vs our $172 realized. None are bugs -- they are
deliberate-conservative defaults that left money on the table on a
defensive day:
  (1) D163 trailing-stop WIDEN at +3/+6/+10% breakpoints (not
      tighten) so winners can run after target hits. LFS exited
      at +0.7% but went +27% intraday.
  (2) D106 PROMOTIONAL_EARLY 10:30 AM hard exit assumes pumps
      reverse mid-morning. Replace with VWAP-anchored trail: hold
      while price > VWAP, exit on VWAP break. LFS was a 60% gap
      promotional that kept running.
  (3) D200-E4 CATALYST GATE: when MFCS is HIGH+ and price action
      confirms, DOWNGRADE (halved size) rather than full BLOCK.
      HYLN ran +17% intraday without a confirmed catalyst.
  (4) D101/D124 CONSENSUS: separate 0-bullish-vs-0-bearish (no
      signal) from 2-bearish-vs-0-bullish (real signal). Today
      both reject identically; the former is grey-area.
  (5) Position-limit raise 3 -> 5 concurrent (with halved size
      per slot to keep portfolio risk equal). Would let LFS+BB
      coexist with HYLN+RDW.
Decision sequencing: do NOT change strategy mid-T2 (conflates the
experiment). Wait for Move 2 Thursday gate (5/28) + first week of
T2 data, then evaluate which enhancement(s) to prototype in shadow.
"""

[D313_writeback_on_existing_matched_stop]
priority = "2"
effort_hours = 1
filed_in = ["_recovery_log.md (2026-05-27 07:00 ET)"]
blocks = []
blocked_by = []
target_date = "2026-06-01"
description = """
D313.v4 (committed 50305b9 today) calls
position_manager.attach_external_stop() ONLY when the watcher
submits a NEW emergency stop. Gap: when the watcher boots and
observes a pre-existing matching protective order (e.g. left over
from a previous bot process), it marks position as hedged but
doesn't write the broker oid back. Result: D230 RECON_WARN STOP
spam continues for that ticker until position closes. Observed
live this morning for LFS (oid 55532ce9 from 04:31 ET pre-fix
bot run, persisted through 07:04 restart). Fix: in check_once
after is_hedged=True branch, if position.stop_order_id is empty
but a matching order was found, call attach_external_stop with
the matched order's oid.
"""

[D313_emergency_stop_writeback_oid]
priority = "S"
effort_hours = 1
filed_in = ["_recovery_log.md (2026-05-27 06:15 ET)"]
blocks = []
blocked_by = []
target_date = "2026-05-27"
description = """
hedge_integrity_watcher.py D313 EMERGENCY_STOP_SUBMITTED successfully
puts an emergency stop at broker (oid 55532ce9 on LFS this morning,
position confirmed hedged), but does NOT write the new broker_order_id
back to ManagedPosition.stop_order_id. Effect: D230 RECON_WARN STOP
fires every 30s for that ticker (~1700/day rate) saying "no
stop_order_id". The position IS hedged; the reconciler is wrong.
Fix: after submit succeeds in hedge_integrity_watcher.py, call
position_manager.set_stop_order_id(symbol, oid) or equivalent. Also
update the stop level field (D313 uses 0.15 below avg_entry,
not the position's internal_stop). Quick fix, 1-line + test update.
Filed 2026-05-27 06:15 ET after observing the bug live this morning.
"""

[LFS_ghost_close_d246_retry_consistency]
priority = "1"
effort_hours = 3
filed_in = ["_recovery_log.md (2026-05-27 06:15 ET)"]
blocks = []
blocked_by = []
target_date = "2026-06-01"
description = """
Yesterday (2026-05-26) BB and LFS both got initial 403 Forbidden on
close (insufficient qty available, held_for_orders=N). BB's D78 SMART
EXIT went through D246 SMART_EXIT_RETRY which succeeded on attempt
2/3. LFS's D163 TRAILING STOP EXIT did NOT retry and got ghost-stuck.
Two questions:
  (a) Does D163 trailing-stop path have the same D246 retry logic
      as D78 smart-exit? If not, port it -- ALL exit paths should
      retry on 403.
  (b) Why did the broker have 2506 LFS shares "held_for_orders" at
      10:15 ET? Was it a left-over D85 fast-path entry order? A
      tranche profit-take order? Was D241 EOD_SAFETY_CANCEL the
      only thing that finally cleared the lock?
Trace the LFS order graph from 09:32 entry to 16:00 EOD cancellation
and document the order-id lifecycle. May reveal a tranche/exit
interaction bug we haven't seen before.
"""

[Websocket_trade_updates_disconnect_root_cause]
priority = "1"
effort_hours = 4
filed_in = ["_recovery_log.md (2026-05-26 06:30 ET)"]
blocks = []
blocked_by = []
target_date = "2026-05-29"
description = """
Alpaca trade_updates WebSocket disconnects every 5-9 min with
"no close frame received or sent" then auto-reconnects in 2s.
9 disconnects observed in 2 hours pre-market 2026-05-26.
Pre-market low impact (no orders). At market hours, each disconnect
is a ~2s window for missed FILL notifications. Investigation:
  (a) confirm REST fill-poll backup is active and gap-aware
  (b) check if Alpaca paper API server-side idle timeout (and tune
      our keepalive accordingly)
  (c) check if any other clients exhibit this pattern (maybe a
      network NAT timeout on Pierce's machine)
  (d) consider client-side ping/heartbeat at < server timeout
Filed by Tuesday morning deep-dive monitoring loop.
"""

[Async_timeout_coverage_critical_paths]
priority = "1"
effort_hours = 3
filed_in = ["_recovery_log.md (2026-05-26 06:30 ET)"]
blocks = []
blocked_by = []
target_date = "2026-06-01"
description = """
19 `async def` functions in src/{core,execution,monitoring}/. Only
5 `asyncio.wait_for` / `asyncio.timeout` calls. Specific external-
API callers without timeouts (could block indefinitely on hang):
  src/core/orchestrator.py:1893 _fetch_sec_filings
  src/core/orchestrator.py:1942 _fetch_vix
  src/core/orchestrator.py:1993 _fetch_spy_return
  src/core/orchestrator.py:2052 _fetch_options_summary
SEC EDGAR 500s are already a known noise source -- when they
escalate to hangs, the orchestrator awaits forever. Add explicit
`asyncio.wait_for(..., timeout=N)` wrappers per call site with
appropriate fallback (return None / cached value / partial result).
"""

```

```toml
[Event_sourced_trade_journal]
priority = "1"
effort_hours = 16                         # design 4h + impl 12h
filed_in = ["170", "175"]                # promoted from Part 4 #19 per Pierce
blocks = ["Cascade_anti_selection_paper", "Historical_similar_setup_kb"]
blocked_by = []                          # don't touch trade_journal mid-bake (informally)
target_date = "2026-06-15"
description = """
SQLite or DuckDB append-only events: OPENED, STOP_SUBMITTED,
STOP_FILLED, TRANCHE_HIT, CLOSED, JOURNAL_AMENDED with monotonic
event IDs and FK to D-codes for provenance. Crash recovery = one-line
replay. T2 A/B analysis = SQL WHERE execution_arm. Cascade paper =
SQL queries against historical truth. Pierce: "every foundational
item depends on having a durable, append-only event log."
"""

[Cascade_paper_data_layer]
priority = "1"
effort_hours = 4                          # feature_logger audit + fixes
filed_in = ["172", "175"]
blocks = []                              # outline is doc not backlog
blocked_by = []
target_date = "2026-05-25"                # Mon AM before T2 launch
description = """
Audit feature_logger.log_evaluation against the cascade paper
outline's "dataset spec." Ensure every T2 verdict captures:
execution_arm, qty_multiplier, per-gate rejection reason (D216 etc),
final action, post-fill outcome. Gaps must be fixed BEFORE first
T2 verdict lands (observability, not freeze violation).
"""

[Replay_regression_seed]
priority = "1"
effort_hours = 6
filed_in = ["172", "174"]
blocks = ["Replay_regression_harness_15_unhedges"]
blocked_by = []
target_date = "2026-05-26"                # Tuesday PM per doc 174
description = """
2 replays (not 15): NXXT 5/18-5/20 (66h unhedge) + Friday 5/22
silent-BUY (D216 ate 123 entries). Through current code, assert:
L2 fires < 60s + EOD veto_summary surfaces D216 dominance + boot
self-test lands. Seed for 15-replay harness Wed-Fri.
"""

[Audit_124_sub60s_windows]
priority = "1"
effort_hours = 3
filed_in = ["172", "174"]
blocks = ["L2_polling_tighten"]
blocked_by = []
target_date = "2026-05-26"                # Tuesday AM per doc 174
description = """
For each of 124 sub-60s unhedge windows from doc 172, pull tick data.
Check if any contained > 2% adverse moves during gap. If 5+/124 had
real adverse moves, pre-emptively tighten L2 polling from 20s -> 10s
BEFORE wide arm sees its first volatile microcap. Data already on disk.
"""

[D293_8_ensemble_retest]
priority = "1"
effort_hours = 3
filed_in = ["155", "162", "167"]
blocks = []
blocked_by = ["T2_200_labeled_picks"]
target_date = "2026-07-12"
description = """
Re-test multi-seed TabPFN ensemble at n >= 200 labeled picks. Initial
test (n=2-7 dates) failed bootstrap CI gate; retest at proper sample
size. Validates whether ensemble's small per-config Spearman gain
(+0.0035) holds with more data.
"""

[Live_vs_shadow_nightly_delta]
priority = "1"
effort_hours = 3
filed_in = ["172"]
blocks = []
blocked_by = ["T2_1_week_of_live_wide_arm_data"]
target_date = "2026-06-02"
description = """
Nightly script diffs live wide-arm realized P&L (from broker
activities) vs replayer's prediction. If gap widens over time =
L1 wiring drift. If narrows = T2 converging (good signal for T3).
"""
```

---

## Tier-2 — quality of life

```toml
[SMS_PagerDuty_tee]
priority = "2"
effort_hours = 2
filed_in = ["173"]
blocked_by = ["operator_provides_twilio_or_pagerduty_webhook"]
target_date = "post_provider_setup"
description = "Tee CRITICAL severity spool records to second webhook for SMS/page."

[L2_polling_tighten]
priority = "2"
effort_hours = 1
filed_in = ["173"]
blocked_by = ["Audit_124_sub60s_windows", "T2_1_clean_session"]
target_date = "2026-05-27"
description = "Reduce L2 polling 20s -> 10s + tolerance 60s -> 30s after first clean session + tick audit."

[Dead_comment_pattern_grep]
priority = "2"
effort_hours = 2
filed_in = ["172", "174"]
blocked_by = []
target_date = "2026-06-01"
description = """
TrailingStopManager had 'Executor callback' comments without callsites
for 4 months (genesis 2026-02-02). Grep codebase for '# Executor callback',
'# wired via', '# implementation TBD' patterns without actual code below.
Catch siblings of the same antipattern.
"""

[Min_price_for_stop_loss_floor]
priority = "2"
effort_hours = 2
filed_in = ["169"]
blocked_by = ["T2_data_on_sub_dollar_names"]
target_date = "2026-06-15"
description = """
Sub-$1 tickers (NXXT-class) may have unreliable Alpaca stop-trigger
behavior. Investigate adding $1.00 floor and time-based market exit
below. Observe T2 sub-$1 behavior first.
"""

[Together_ai_fallback_provider]
priority = "2"
effort_hours = 4
filed_in = ["152"]
blocked_by = []
target_date = "2026-06-15"
description = """
TabPFN cloud calls degrade if Together.ai rate-limits. Implement
fallback to local model or alternative endpoint. Gated behind
feature flag.
"""

[Nightly_z_score_gate_summary]
priority = "2"
effort_hours = 6
filed_in = ["173"]
blocked_by = ["7d_baseline_data"]
target_date = "2026-06-08"
description = """
Persist gate_summary (D312 output) nightly to SQLite. Compute z-scores
vs trailing-7-day baseline. Surface anomalies (e.g., D216_RECENTLY_CLOSED
spike). Needs 7+ days of T2 baseline first.
"""

[Single_LLM_schema_versions]
priority = "2"
effort_hours = 1                          # just adding the field
filed_in = ["175"]
blocked_by = []
target_date = "2026-05-26"
description = """
Add schema_version: int = 1 field to TradeVerdict, MFCS, AgentSignal
models. Pierce: 'tagging the version now means the migration is
mechanical when it arrives.' Actual migration framework only when
first migration is needed (single-LLM agent experiment, Q3).
"""

[Selection_arena_cleanup]
priority = "2"
effort_hours = 3
filed_in = ["_dead_audit_findings.md"]
blocked_by = ["Move_2_decision_gate"]
target_date = "2026-05-29"
description = """
src/selection_arena/__init__.py imports filter_replay, market_movers,
backtest -- none exist on disk. Importing the package crashes.
scripts/run_selection_arena.py refs 3 more missing modules
(backtest, market_movers, report). Decide: restore from git history
or delete the package + script. Surfaced by _dead_audit.py first run.
"""

[ADR025_backtest_command_cleanup]
priority = "2"
effort_hours = 2
filed_in = ["_dead_audit_findings.md"]
blocked_by = ["Move_2_decision_gate"]
target_date = "2026-05-29"
description = """
main.py cmd_build_scenarios / cmd_record_scenarios import
src.data.scenario_builder + scenario_recorder -- modules do not exist.
ADR-025 Phase-3 backtest validation work appears abandoned. Decide:
re-build or delete the CLI commands. Surfaced by _dead_audit.py.
"""

[Docstring_lies_fix]
priority = "2"
effort_hours = 1
filed_in = ["_dead_audit_findings.md"]
blocked_by = []
target_date = "2026-06-01"
description = """
4 real docstring lies caught by _dead_audit.py:
  slippage_calibration.py:25 -- 'fit_eta_gamma_from_corpus' should be 'fit_eta_gamma'
  stop_decision_log.py:50    -- 'AlpacaExecutor.submit_entry' no longer exists
  recon_daemon.py:34/36      -- 'skip_entry()' / 'flat_all_and_halt()' never built
  replay_engine.py:10/19     -- promised v2 API ('from_phase0_partition', 'git_replay_compare')
Cheap to fix, no production risk. See _dead_audit_findings.md for triage.
"""

[Scanner_universe_fallback_cleanup]
priority = "2"
effort_hours = 1
filed_in = ["_dead_audit_findings.md"]
blocked_by = []
target_date = "2026-06-15"
description = """
scripts/backtest.py:545/940/1111 -- try-imports src.scanners.universe.get_scan_universe
which doesn't exist; silently falls back to a hardcoded ticker list.
Worst kind of dead code: pretends to work, emits a 'WARNING' nobody reads.
Either build the module or delete the import + fallback dance.
"""

[Wire_dead_audit_to_CI]
priority = "2"
effort_hours = 1
filed_in = ["_dead_audit_findings.md"]
blocked_by = []
target_date = "2026-05-26"
description = """
_dead_audit.py exists and produces 0 errors on the 0-deprecated-D-code
check today. Add `python docs/SYSTEM_MAP/_dead_audit.py --strict` to CI
as warning-only initially; promote to blocking once broken-import findings
are resolved.
"""
```

---

## Tier-3 — research

```toml
[Single_LLM_tool_using_agent]
priority = "3"
effort_hours = 40                         # large redesign
filed_in = ["175"]
blocked_by = ["T2_wraps", "schema_versions_in_place"]
target_date = "2026-Q3"
description = """
Replace 6-agent ensemble with single agentic LLM (Claude w/ MCP tools).
Tools: fetch_news, fetch_sec, fetch_options, fetch_chart. Removes
linear-weight assumption (6 agents are correlated; weights are vibes).
70% LLM cost reduction. Risk: single point of failure for selection
(current ensemble has D91/D92 fallback chain).
"""

[Vision_LLM_intraday_chart]
priority = "3"
effort_hours = 8
filed_in = ["175"]
blocked_by = []
target_date = "2026-Q3"
description = """
ChatGPT-4o / Claude vision call on rendered intraday chart catches
patterns rule-based engine misses (pennants, support tests, volume
divergence). ~10s latency, ~$0.01/call. Run as Tier 7 agent. Shadow-
mode 2 weeks first.
"""

[Embedding_news_retrieval]
priority = "3"
effort_hours = 12
filed_in = ["175"]
blocked_by = []
target_date = "2026-Q3"
description = """
Replace keyword-based catalyst classification with embedding similarity
(BGE-M3, E5-mistral). Index Polygon news + SEC EDGAR + StockTwits
historicals. Query: prior-headline -> outcome conditioning.
"""

[Sec_edgar_vector_index]
priority = "3"
effort_hours = 8
filed_in = ["175"]
blocked_by = []
target_date = "2026-Q3"
description = """
Chunk + embed all 8-Ks, S-3s, 424B2/B5 from last 90d (dilution window).
Index in DuckDB+VSS. Retrieve top_k chunks similar to current filing.
Catches dilution patterns Claude alone misses.
"""

[Historical_similar_setup_kb]
priority = "3"
effort_hours = 16
filed_in = ["175"]
blocked_by = ["Event_sourced_trade_journal"]
target_date = "2026-Q3"
description = """
Vector index over every prior trade's (features, market_regime, outcome).
At decision time, retrieve nearest neighbors. Case-based reasoning for
trading. Used by: Bayesian sizing prior, debate context, meta-scorer
sanity check.
"""

[Bocpd_wire_in_kill_switch]
priority = "3"
effort_hours = 6
filed_in = ["bocpd.py docstring"]
blocked_by = ["BOCPD_SHADOW_decision_2026_06_15"]
target_date = "2026-06-15"
description = "Wire BOCPD as soft kill-switch (halve Kelly when P(break) > 0.75)."

[Multi_regime_sizing_matrix]
priority = "3"
effort_hours = 8
filed_in = ["175"]
blocked_by = []
target_date = "2026-Q3"
description = """
Ising regime detector classifies {calm, transition, stressed, crisis};
not wired to sizing. Wire as 4 x 4 Kelly matrix
(regime x tier). Crisis x ELITE = 30%; Stressed x VETOED = 0%.
"""

[Chaos_alpaca_client]
priority = "3"
effort_hours = 8
filed_in = ["173"]
blocked_by = []
target_date = "2026-Q3"
description = """
Wrap Alpaca calls with failure injection (drop responses, timeout,
stale data). Test L2/L1 failure modes. Filed for resilience work.
"""

[Mutation_testing_on_l2]
priority = "3"
effort_hours = 4
filed_in = ["173"]
blocked_by = []
target_date = "2026-Q3"
description = """
mutmut against hedge_integrity_watcher.py for ~100% kill rate.
Ensures emergency-stop logic is genuinely correct. L2 is the single
most important safety code path.
"""

[Cascade_anti_selection_paper]
priority = "3"
effort_hours = 80                         # outline + dataset + analysis + writing
filed_in = ["175"]
blocked_by = ["Event_sourced_trade_journal", "T2_n_geq_100"]
target_date = "2026-Q4"
description = """
Pierce framing: 'the paper IS the architectural roadmap dressed up
as research.' Outline drafted Mon EOD (DRAFT-INTERNAL until n>=100).
Methodology: train model on (features_at_decision, gate_decisions) ->
outcome; SHAP for per-gate alpha contribution. Venue: ICML workshops,
NeurIPS D&B, or blog -> quant Twitter.
"""
```
