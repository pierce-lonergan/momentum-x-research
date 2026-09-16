# Recovery log -- T2 launch day onward

Append-only intervention log for the autonomous monitoring drill that
began 2026-05-25 09:35 ET. Every detection / diagnosis / fix / restart
gets one entry. Newest on top.

Format per entry:

```
## YYYY-MM-DD HH:MM ET -- title
trigger    : (alert | crash | anomaly | self-check)
detection  : what surfaced it
diagnosis  : root cause
fix        : exact change applied (commit/env-var/restart)
verify     : how recovery was confirmed
ttr_min    : time from detection to verified recovery
followup   : backlog item filed (if any)
```

The goal is dual-purpose: keep the bot running AND build a corpus of
real failure-recovery patterns. Pierce's framing: "test or improve our
ability to recover from failures."

---

## 2026-05-27 07:00 ET -- Missed-profit forensics + 4 fixes + recovery drill

trigger    : user request "fix bugs, restart, analyze yesterday's
             missed profit, deep dive"
detection  : full funnel analysis (scenarios -> verdicts -> orders ->
             fills) + bar-recording cross-reference for held-vs-rejected
             tickers + pattern-class audit + live fix-and-restart cycle

YESTERDAY MISSED-PROFIT FORENSICS (Tuesday 2026-05-26):

  Tickers tracked yesterday (10): BB, FJET, HYLN, LFS, NVTS, NXXT,
  RDW, RGTIW, RGTU, RGTX. Cross-referenced against bar_recordings:

  HELD (filled):
    LFS  entry $2.99 -> trail exit $3.01 (+0.7% = +$50 journal, $0 broker)
         Day close $3.65 (+22%), Day high $3.80 (+27%)
         HOLD-TO-CLOSE missed: $1,654 (66c x 2506 shares)
         HOLD-TO-HIGH missed:  $2,031 (81c x 2506 shares)
         GHOST-LOSS:           $50  (journal counted, broker didn't)
    BB   entry $8.24 -> smart exit $8.39 (+1.8% = +$172 broker)
         Day close $8.42 (+1.06%, fairly close to our exit)
         HOLD-TO-CLOSE marginal: +$38 (3c x 1252 shares)
         BB exit timing was actually good.

  REJECTED (could-have-won):
    HYLN  D200-E4 CATALYST GATE blocked 3x (no confirmed catalyst)
          Day close $6.61 (+7.31%), Day high $7.23 (+17.37%)
          On $5k wide-arm position: $366 missed (close) / $869 (high)
    RDW   D101/D124 CONSENSUS REJECT (0 bullish vs 1-2 bearish)
          Day close $22.02 (+12.12%), Day high $23.10 (+17.59%)
          On $5k wide-arm position: $606 missed (close) / $880 (high)
    NVTS  D124 CONSENSUS ALIGNMENT REJECT (2 bearish vs 0 bullish)
          Day close $31.78 (+1.70%), Day high $33.82 (+8.22%)
          Small upside; rejection was reasonable
    RGTX  D124 alignment fail; Day close -7.12%  -- correct rejection
    RGTU  D124 alignment fail; Day close -6.94%  -- correct rejection
    RGTIW D112 INSTANT_REJECT (RVOL 0.8 < 1.0); Day close -5.12% -- correct
    FJET  D200-E4 CATALYST blocked 9x; Day close -18.89% -- correct block!
    NXXT  rejection; Day close -19.96% -- correct rejection

  ROOT CAUSE of LFS missed alpha:
    LFS was classified D106 PROMOTIONAL_EARLY (60.8% gap = pump
    pattern) which forced:
      * Half position size (already halved again by T2 wide_arm = 1/4 sizing)
      * TIGHT trailing stop $2.42
      * Aggressive targets [+3/+6/+10%]
      * 10:30 AM HARD EXIT (force-close)
    D163 trailing-stop fired at +0.7% (well below the +3% first
    target) because trail relative to max_gain $3.16 caught a $0.05
    retracement. After exit, LFS went on a +27% run.
    The PROMOTIONAL_EARLY heuristic was DESIGNED to be conservative
    on 60%-gap stocks (often dump). Yesterday it was wrong.

  TOTAL ESTIMATED MISSED PROFIT (yesterday only):
    LFS trail+ghost:    $1,654 - $2,031 (held-to-close vs held-to-high)
    HYLN catalyst:      $366 - $869
    RDW consensus:      $606 - $880
    NVTS marginal:      $50 - $200
    ----------------------------------
    Conservative total: $2,676  vs realized $172  =  16x leverage left
    Aggressive total:   $3,980  vs realized $172  =  23x leverage left

  WHY THE GATES BLOCKED:
    * D200-E4 CATALYST GATE: requires "confirmed catalyst" (news
      event matched). HYLN had price action but no catalyst feed
      match. False-negative on the catalyst dimension.
    * D101/D124 CONSENSUS: requires N bullish > M bearish agents.
      RDW had 0 directional agents (HOLD); NVTS had 2 bearish vs 0
      bullish (REJECT). Bot defaults to "no strong consensus = no
      trade" which is reasonable but loses on grey-area movers.
    * D106 PROMOTIONAL_EARLY: gap > 50% = promotional heuristic
      tightens stop + caps duration. Cost us the LFS runner.

  COULD MORE SOPHISTICATED STRATEGY HAVE CAUGHT MORE?
    Yes -- candidate improvements:
    (1) D163 trailing-stop should WIDEN at +3%/+6%/+10% breakpoints
        instead of tightening (let winners run after target hits).
    (2) D106 PROMOTIONAL_EARLY's 10:30 AM hard exit assumes pump-
        dumps reverse by mid-morning. Sometimes they continue (LFS).
        Could replace with VWAP-anchored trail: hold while price >
        VWAP, exit on VWAP break.
    (3) D200-E4 CATALYST GATE could be DOWNGRADE (halved size) not
        BLOCK when MFCS is HIGH+ -- price action sometimes
        precedes news.
    (4) D101/D124 CONSENSUS could be 0-bullish-vs-0-bearish
        (no signal) treated separately from 2-bearish-vs-0-bullish
        (real signal). Today both go to REJECT.
    (5) Position-limit increase from 3 -> 5 concurrent would let
        us take the secondary opportunities without conflicting
        with LFS+BB.
    All five are STRATEGY enhancements, not bugs. Filed Tier-1
    `Strategy_enhancement_audit_post_T2_week1`.

THIS MORNING (Wednesday 2026-05-27 07:00 ET) -- FIX + RESTART DRILL:

  Fixes applied + committed (4):
    (a) D313.v4 EMERGENCY_STOP oid writeback (commit 50305b9)
        After auto-submitting emergency stop, watcher now calls
        position_manager.attach_external_stop() so internal tracker
        reflects reality. Eliminates the 1700/day D230 spam pattern
        going forward.
    (b) D310.T2.v2 ARM_ASSIGN_HARD_ABORT (commit 50305b9)
        Failed arm assignments now HOLD instead of entering
        un-assigned. Protects A/B experiment integrity.
    (c) options_provider INFO -> DEBUG (commit 50305b9)
        Removed 2,415 lines/2hr of parse-count chatter.
    (d) Preflight: Finnhub demoted to advisory (commit 9b23582)
        Discovered live during restart: 3 retries of launcher all
        failed on Finnhub timeout while curl showed 200ms latency.
        Made Finnhub non-blocking (news-only, has graceful fallback)
        and kept Alpaca/Disk/Heartbeat/Halt as blocking.

  Live recovery flow:
    07:00:00  user: "fix and restart"
    07:00:30  3 fixes committed (50305b9) + pushed
    07:01:00  Stop-ScheduledTask -- old PID 33248 gone in 5s
    07:01:10  Start-ScheduledTask -- FAILED on Finnhub preflight
    07:02:15  retry -- FAILED again on Finnhub
    07:03:00  curl confirms Finnhub responding at 200ms
    07:03:30  preflight fix committed (9b23582) + pushed
    07:04:00  Start-ScheduledTask -- SUCCESS new PID 57096
    07:04:30  D315 BOOT_SELF_TEST: T2=True HALT=False L2=OK
    TTR: ~4 minutes including discovering and fixing the
    preflight bug. Without the preflight fix, the bot would have
    stayed down indefinitely.

  LFS state post-restart:
    Bot synced LFS qty=2506 from broker (same overnight position).
    Existing broker emergency stop oid 55532ce9 from 04:31 ET this
    morning is STILL IN PLACE -- D313 watcher's next tick will
    detect it as a matching protective order and mark LFS hedged.
    D230 RECON_WARN STOP keeps firing temporarily until either
    (a) D313 also writes back oids for already-matched stops
    [followup filed], OR (b) D91 closes LFS at 09:30 ET market
    open (most likely resolution).

fix        : 4 commits (50305b9 + 9b23582)
verify     : new PID 57096 healthy, D315 clean, fixes deployed
ttr_min    : 4 min including discovering + fixing the preflight bug
followup   : Filed:
             * `D313_writeback_on_existing_matched_stop` (Tier-2):
               watcher should call attach_external_stop on hedged
               check too, not just on submit, so legacy emergency
               stops from older bot processes get plumbed back.
             * `Preflight_finnhub_advisory_not_blocking` -- already
               IMPLEMENTED in this commit, marked as DONE.
             * `Strategy_enhancement_audit_post_T2_week1` (Tier-1):
               5 specific strategy improvements identified from
               missed-profit analysis. Decide post-Move-2 Thursday.

---

## 2026-05-27 06:15 ET -- T2 Day-1 retrospective + Day-2 morning audit

trigger    : user request "deep dive on what happened yesterday + this morning"
detection  : full session log review (5MB yesterday, 660KB today),
             cross-checking T2 arm assignments, fills, exits, EOD
             reconciliation, ghost-position lifecycle, overnight
             transition, and pre-market state

YESTERDAY (Tuesday 2026-05-26, T2 Day-1):

  T2 A/B working as designed:
    20+ arm assignments logged in first 4 min of trading.
    MD5 hash producing ~50/50 split: wide_stop 0.50x for
    {BB, RGTX, HYLN, LFS, RGTU}, tight_stop 1.00x for
    {FJET, NVTS, NXXT, RDW}. T2_arm_assignment.assign_arm() never
    failed (no silent-non-fatal fallback triggered).

  Fills:
    LFS  09:32 ET   wide_arm 0.5x   entry $2.99 qty 2506
    BB   09:32 ET   wide_arm 0.5x   entry $8.24 qty 1252
    BRAI 10:17/10:34/11:27 ET -- 3 rejected entries (D217 timeout,
       all "still status=new filled_qty=0 after polls -- rejecting
       to prevent ghost position"). Same ticker, three different
       order IDs. Possible Alpaca-side rejection / low liquidity /
       halt. Worth investigating but D217 ghost-prevention worked
       as designed.

  D278 carry-overnight verified live:
    10:01:57 D278 BAR-1 SKIPPED Phase3 LFS: exit_policy=t1_next_open
             age=80s (carrying overnight via D86/D91 next-open path)
    10:01:57 D278 BAR-1 SKIPPED Phase3 BB ... age=63s
    Both positions correctly bypassed the legacy T+60s BAR-1 EXIT.

  Exits:
    BB   11:20 ET   D78 SMART EXIT  exit=$8.39 pnl=+$183.73 (+1.8%)
                    -> first attempt 403 Forbidden, D246 retry
                    succeeded on attempt 2/3. Clean.
    LFS  10:15 ET   D163 TRAILING   exit=$3.01 pnl=+$50.12 (+0.7%)
                    -> close_position returned 403 ("insufficient
                    qty available, held_for_orders=2506"). Journal
                    recorded the close, broker did NOT. Position
                    became GHOST.

  EOD damage report:
    D222 PNL_RECON DIVERGENCE: journal_total=$233.85
         broker_total=$172.46 delta=-$61.39
      BB:  journal=+$183.73 broker=+$172.46 delta=-$11.27 (small)
      LFS: journal=+$50.12  broker=+$0.00   delta=-$50.12 (ghost)
    "Likely cause: an exit path closed positions without calling
    TradeJournal.record_close() (BAR-1 / D146 was the original
    Bug #13)."
    D241 EOD_SAFETY_CANCEL LFS: cancelled orphan SELL order
         769bed1e (qty=2506 status=new)
    D242 EOD_FORCE_CLOSE LFS: ghost close FAILED -- still 403
         after retry budget exhausted. Position remained open
         at broker overnight (2506 shares, ~$7.5k exposure).
    D262 BOCPD_REFIT_RECOMMENDED: n_trades grew from 29 to 40,
         action: `python scripts/pretrain_bocpd_prior.py`. Not run.

  Yesterday's L2 watcher (D313) stats: checks=1930 violations=0.
  Clean intraday -- the LFS hedge issue arose at EOD only.

  Session result: 4 trades recorded, +$172.46 broker P&L net
  (1 winner $183, 1 ghost-loss net 0 from broker view).

THIS MORNING (Wednesday 2026-05-27 04:30-06:15 ET):

  Boot:
    PID 33248, commit 0170b0e9 (yesterday's deep-dive commit).
    D315 BOOT_SELF_TEST: T2=True HALT=False L2=OK WDOG=OK RETRY=OK.
    Recovery flow worked end-to-end overnight: 14h ExecutionTimeLimit
    killed yesterday's bot at 18:30 ET, Task Scheduler auto-relaunched
    04:30 ET today with all env vars persisted.

  GHOST POSITION CARRYOVER:
    04:30:09 D56 sync: LFS qty=2506 @ $2.99 (stop=$2.83 COMPUTED
             DEFAULT, no broker order) -- downstream code must
             verify or submit
    04:30:10 D91: 1 OVERNIGHT positions detected (opened before
             today 04:00 ET): ['LFS']. Will close at market open.

  *** L2 SAFETY NET SAVED THE SYSTEM ***
    04:30:11 D313 HEDGE_WATCHER LFS: long qty=2506 unhedged
             (no sell stop at broker); starting tolerance clock
    04:31:12 D313 HEDGE_VIOLATION LFS: unhedged for 61s
             (threshold 60s); will_submit=True
    04:31:13 D313 EMERGENCY_STOP_SUBMITTED LFS:
             oid=55532ce9-02f3-44bc-8993-e24b5d562c1e stop=$2.5415
             qty=2506 -- position now hedged
    61-second auto-recovery, zero human intervention. This is the
    runbook scenario from doc 174 firing for real. ttr=61 seconds
    from boot to hedged. The watcher worked exactly as designed.

  NEW BUG SURFACED (Tier-S):
    Despite D313 successfully submitting the emergency stop with
    broker oid 55532ce9, the position's internal `stop_order_id`
    field was NOT updated. Effect: D230 RECON_WARN STOP fires
    every 30s saying "internal_stop=$2.83 but no stop_order_id
    (cannot reconcile against broker; D56 no-broker-stop case)".
    180 such warnings logged in 1h45m pre-market (~1700/day rate).
    The position IS actually hedged at broker, but the bot's
    internal reconciliation still thinks it's not.
    Quick fix: hedge_integrity_watcher.py after submit must call
    something like `position.set_stop_order_id(emergency_oid)`.
    Filed `D313_emergency_stop_writeback_oid` Tier-S.

  Other morning findings:
    * WebSocket trade_updates disconnect pattern reproduced:
      7 disconnects in 1h45m (05:21, 05:31, 05:40, 05:49, 06:03, ...)
      = consistent ~9-10 min cadence. Yesterday's Tier-1 finding
      confirmed on second consecutive day.
    * BRAI not in today's candidate pool (21 from 62 vs yesterday's
      24/66) -- yesterday's 3 rejects may have been the bot
      shadow-banning the ticker, or it dropped out organically.
    * Equity drift -$12.53 = LFS unrealised overnight P&L (price
      moved from $2.99 entry to $3.165 = unrealised +$438 but bot
      may not have marked-to-market).

  Pending risk for TODAY's session:
    D91 will trigger _close_overnight_position for LFS at 09:30 ET.
    If the same 403 / held_for_orders=N race happens that ghost-
    stuck the position yesterday, we could get re-ghosted. The
    D313 emergency stop is at $2.5415 -- if market opens below
    that, the stop fires first (clean exit). If above, the
    close-overnight logic runs. Monitor closely 09:30-09:35 ET.

fix        : 0 immediate fixes during T2 window. 1 Tier-S filed.
verify     : L2 watcher emergency stop is at broker (oid 55532ce9,
             status pending in D313 log). Bot is healthy at 06:15 ET.
ttr_min    : L2 auto-recovery = 61 seconds wall-clock from boot
followup   : D313_emergency_stop_writeback_oid (Tier-S)
             Plus open question: how did BB exit succeed via D246
             retry while LFS exit got permanently 403-stuck?
             Different exit paths (SMART vs TRAILING), or different
             timing relative to other in-flight orders? Worth
             tracing for ghost-prevention learnings.

---

## 2026-05-26 06:30 ET -- T2 real launch day -- pre-open deep dive (8 findings)
trigger    : user request "deep dive, spot bugs, record gaps"
detection  : systematic audit of bot state, log volume, async coverage,
             shadow outputs, test coverage, and pattern-class bug
             searches (TODO/FIXME, bare except, silent failures)
state      : PID 26692, T2=True, commit 76f8acf6, 119min uptime,
             349 MB RAM / 131 threads / 598 handles (healthy footprint).
             Pre-market clean: zero errors, 24 candidates/scan, ~70s
             scan cadence. Recovery flow worked overnight: yesterday's
             14h ExecutionTimeLimit kill fired, Task Scheduler
             relaunched at 04:30 ET, MOMENTUM_T2_ENABLED=1 persisted
             in User env from Monday's recovery.

findings   : (filed individually below)

  (1) [Tier-1] WebSocket trade-updates disconnect every 5-9 min.
      9 disconnects in 2 hours pre-market on the trade_updates stream
      (not market_data). Each "no close frame received" -> 2s recovery
      with auth re-succeed. Pre-market low impact (no orders). At
      market hours: 2s window per disconnect = potential missed FILL
      notifications. Spacing: 9min, 42min, then accelerating: 17, 5,
      5, 5, 5, 5 min. Need confirmation REST-fill-poll backup is
      active. Filed `Websocket_trade_updates_disconnect_root_cause`.

  (2) [Tier-2] options_provider INFO spam: 2,415 lines / 2hr.
      ~20 lines/min. Most are "Parsed N option contracts for chain".
      Should be DEBUG-level. Demotion fix is one line; will reduce
      log size by ~30% over a full session.
      Filed `Options_provider_INFO_to_DEBUG`.

  (3) [Tier-2] live_dashboard zero-state spam: 233 lines / 2hr.
      Prints the same "Trades: 0.0 / Errors: 0.0 / Open: 0.0"
      dashboard every 30s during pre-market when nothing has changed.
      Add suppress-when-unchanged logic.
      Filed `Live_dashboard_suppress_unchanged`.

  (4) [Tier-2] D64 session_state WARNING is misleading.
      `D64: Backup recovery failed (state=loaded, stale=True) --
      falling through to fresh start`. This is CORRECT behavior --
      yesterday's state was stale by design (holiday), so it was
      discarded. WARNING wording reads like a bug. Should be INFO
      with "Backup stale by design (last_run=YYYY-MM-DD, today is a
      new trading day) -- fresh start" wording.
      Filed `D64_session_state_warning_wording`.

  (5) [Tier-1] Async-without-timeout coverage gap in critical paths.
      19 `async def` functions in src/{core,execution,monitoring}/.
      Only 5 `asyncio.wait_for` / `asyncio.timeout` calls. Specific
      gaps confirmed:
        orchestrator.py:1893 _fetch_sec_filings    (external API)
        orchestrator.py:1942 _fetch_vix            (external API)
        orchestrator.py:1993 _fetch_spy_return     (external API)
        orchestrator.py:2052 _fetch_options_summary (external API)
      If any of those APIs hang (SEC EDGAR 500s already observed
      regularly), the awaiting orchestrator could block indefinitely.
      Filed `Async_timeout_coverage_critical_paths` Tier-1.

  (6) [Tier-2] T2 arm-assignment failure is silent-non-fatal.
      orchestrator.py:1747:
          from src.execution.t2_arm_assignment import assigned_arm_for_verdict
          ...
          try: ...
          except: logger.warning("D310.T2 arm assignment failed for %s (non-fatal): %s", ...)
      If MD5 hashing fails (shouldn't, but: encoding edge case,
      memory error, etc.), the trade PROCEEDS WITHOUT ARM ASSIGNMENT.
      That silently conflates the A/B experiment. During the T2
      window, this should be a HARD ABORT (don't enter the trade
      rather than enter without assignment).
      Filed `T2_arm_assignment_fail_should_halt_entry`.

  (7) [Tier-3] Shadow output dirs are empty for today AND yesterday.
      No 2026-05-25 or 2026-05-26 files in data/shadow/,
      data/features/, data/journals/, data/shadow_stops/. Last
      writes are 2026-05-22 (Friday). Likely correct (event-triggered
      writes only fire on actual fills/decisions, and Mon was
      holiday + today is still pre-market). But should be VERIFIED
      after market opens. If no files appear by EOD 5/26 despite
      trades happening, that's a real write-failure bug.
      Filed `Shadow_output_emission_verify_post_market`.

  (8) [Tier-3] Test coverage observations.
      Positive: t2_arm_assignment.py has tests
      (test_doc171_t2_layers.py); D315 boot self-test has tests
      (test_doc174_band_debounce_stale_boot.py). Pattern-class
      audit found ZERO TODO/FIXME/HACK in critical paths, ZERO
      bare `except:`, ZERO `except: pass`. Code discipline is
      strong.
      No item filed.

fix        : 0 immediate fixes during T2 window (code freeze).
             6 backlog items filed (2 Tier-1, 3 Tier-2, 1 Tier-3).
verify     : n/a (findings only)
ttr_min    : 0
followup   : See backlog.md additions in this commit.

---

## 2026-05-25 11:55 ET -- "T2 launch day" is Memorial Day (T2 actually launches 2026-05-26)
trigger    : self-check (wake-up from missed 09:55 tick; user nudged)
detection  : tail of momentum_2026-05-25.log showed
             `[D79] Market CLOSED (holiday/weekend). Next open:
             2026-05-26T09:30:00-04:00. Skipping trading phases.`
             D79 has fired 169 times today (every ~60s, correct).
             First fire 09:00:04 ET (before market would have opened).
diagnosis  : 2026-05-25 = Memorial Day = US market holiday. Verified
             in `src/scheduling/market_calendar.py`:
             `date(2026, 5, 25),   # Memorial Day`
             The whole "Monday T2 launch" plan (docs 169-174, INDEX.md
             launch sequence, code-freeze window, ratchet criteria,
             Move 2 Thursday gate) was calibrated against Monday
             being a trading day. It's not.

             Cascade implications:
             * T2 actually launches TOMORROW (Tuesday 2026-05-26)
             * Move 2 Thursday gate (2026-05-28) now has TWO sessions
               of T2 data (Tue+Wed), not three (Mon+Tue+Wed)
             * Code-freeze window shifts: 2026-05-25 22:00 -> 2026-05-27
               16:00 ET (push out by 1 trading day)
             * Today's monitoring drill = pure dry run on an idle bot
               (still useful: caught MOMENTUM_T2_ENABLED missing, the
               EOD_recon spam, the BOCPD-acting drift, and the Day-1
               cold-cache D85 issue)

             None of this is a "bug" -- D79 holiday detection works
             perfectly. It's a PLANNING gap: T2 launch docs didn't
             cross-reference the market calendar.

fix        : (1) Today's bot stays idle and healthy. No intervention.
             (2) Tomorrow 04:30 ET Task Scheduler launches a fresh
                 instance with MOMENTUM_T2_ENABLED=1 inherited from
                 User env (set this morning, persists across reboots).
             (3) Today's bot will be killed by Task Scheduler at
                 18:30 ET (04:30 + 14h ExecutionTimeLimit). Verified.
             (4) MultipleInstances=IgnoreNew is a risk ONLY if today's
                 bot survives past 04:30 tomorrow. The 14h kill makes
                 that impossible. No defensive Stop needed.
verify     : Task Scheduler NextRunTime = 2026-05-26 04:30 AM ET.
             Memorial Day in market_calendar.py confirmed.
ttr_min    : 0 (no intervention needed; just discovery)
followup   : Filed `Holiday_calendar_check_pre_launch` Tier-2 -- ship
             docs that schedule launches must cross-reference
             market_calendar.is_trading_day() before committing to a
             launch date. Cheap to implement; would have caught this
             during doc 169 draft.

             Doc 175 (if it gets written) should open with: "Memorial
             Day cost us the Day-1 Monday session. T2's three-session
             Move 2 gate is now Tue-Wed-Thu morning instead of
             Mon-Tue-Wed."

---

## 2026-05-25 09:48 ET -- shadow-state audit (no fix yet, three findings)
trigger    : self-check (inter-tick analysis pass)
detection  : full inventory of `data/` shadow output dirs + cross-ref
             against `experiments.md` declared shadow status
diagnosis  : THREE integrity findings, none requiring immediate fix
             during the T2 window but all logged for post-T2 cleanup:

  (1) BOCPD is ACTING, not shadow-only.
      `experiments.md [BOCPD_kill_switch]` says
      `one_line = "Online regime-break detector; wired to logging but
      never to action."`
      Reality: `src/analysis/kelly_governor.py` reads BOCPD output
      and `src/execution/alpaca_executor.py:203` calls
      `self._kelly_governor.current_multiplier()` to HALVE position
      size for 5 trades after a BOCPD_BREAK. `bridge.py:1567,1620`
      counts down the halve window. The action wiring is in PRODUCTION.
      Either the promotion was intentional (and SYSTEM_MAP is stale)
      or unintentional (and it should be reverted). Cannot resolve
      without Pierce. Filed `BOCPD_action_status_audit` Tier-S.

  (2) EOD RECON firing every 30s during regular session.
      `src.monitoring.eod_recon` logs "EOD RECON EQUITY OK ... EOD
      RECON SUMMARY ..." 2 lines every 30 seconds. The module is
      named "EOD" (End Of Day) but is firing on a continuous-recon
      cadence at 09:45-09:48 ET. Either misnamed (should be
      `continuous_recon`) or mis-scheduled (should fire only at
      16:00 ET). Equity numbers look correct ($148,153.35, 0 drift)
      but the log spam is ~1500 redundant lines/session. Filed
      `EOD_recon_naming_or_cadence` Tier-2.

  (3) No D85 fast-path today.
      The 09:30:01 ET fast-path entry queue check ran at 09:39:50 ET
      (post-restart) with "0 fast-path entries queued (0 MFCS cached
      for bypass)". The high-conviction-pre-cached entry path is
      cold-cached after restart. Bot can still enter via the regular
      MFCS path. Lost the D85 fast-path window for today as collateral
      damage from the 09:38 restart. Acceptable trade-off vs running
      single-arm all day. No backlog item.

fix        : none yet (findings only). Two backlog items filed.
verify     : n/a
ttr_min    : 0 (informational)
followup   : Tier-S `BOCPD_action_status_audit` + Tier-2
             `EOD_recon_naming_or_cadence`

---

## 2026-05-25 09:35 ET -- MOMENTUM_T2_ENABLED unset at boot
trigger    : self-check (autonomous monitoring loop tick #1)
detection  : `grep "D315 BOOT" logs/momentum_2026-05-25.log` returned
             `T2=False HALT=False L2=OK WDOG=OK RETRY=OK`. Pre-open
             env audit also showed no MOMENTUM_T2_ENABLED entry. The
             0915 launch sequence (per INDEX.md) called for T2=True.
diagnosis  : MOMENTUM_T2_ENABLED was not set in User scope (verified
             via `[System.Environment]::GetEnvironmentVariable`).
             04:30 ET launcher booted with the default (T2 off). Bot
             is running the legacy single-arm tight-stop path; the
             whole wide/tight 50/50 A/B is dormant. No T2 selection
             logs anywhere in today's file.
fix        : (1) Set `MOMENTUM_T2_ENABLED=1` at User scope (no UAC).
             (2) `Stop-Process -Id 44588 -Force` FAILED with Access
             Denied -- bot was elevated (Task Scheduler config has
             `RunLevel=Highest`, owner Pierce). Pivoted to
             `Stop-ScheduledTask -TaskName MomentumX-PaperTrading`,
             which goes through the scheduler service and bypasses
             the per-process elevation barrier without UAC. Process
             gone in ~4s.
             (3) Remove stale lock file, then
             `Start-ScheduledTask -TaskName MomentumX-PaperTrading`.
             New PID 50776 came up in ~15s; lock file refreshed.
verify     : at 09:39:46 ET new D217 STARTUP banner posted (PID
             50776, same commit b47b899c). At 09:39:50 ET D315
             BOOT_SELF_TEST: `T2=True HALT=False L2=OK WDOG=OK
             RETRY=OK`. T2 A/B is live.
ttr_min    : 5 (detected 09:35 ET, T2=True confirmed 09:40 ET)
followup   : Filed `Launcher_required_env_check` Tier-S in backlog.md.
             Defense: the daily_paper_trade.ps1 launcher should
             ABORT BOOT if MOMENTUM_T2_ENABLED is unset during the
             T2 window (2026-05-25 -> 2026-06-04). Default-off was
             the bug -- silent fallback to legacy single-arm was the
             worst possible failure mode: looked healthy, ran the
             wrong experiment. Fix-it lesson logged below.

lesson learned:
  * Per Pierce's prior loop instructions, elevated processes can't
    be killed by a non-elevated shell with `Stop-Process` -- access
    denied. The reliable workaround for Task Scheduler-spawned bots
    is `Stop-ScheduledTask` + `Start-ScheduledTask`. No UAC prompt,
    works for any task the user has rights to manage. Add to
    monitoring.md runbook.
  * Boot self-test (D315) was the single signal that surfaced this.
    Without that check the bot would have looked fully healthy
    (process alive, heartbeat fresh, no errors) while silently
    running the wrong code path. D315 is high-value defense; its
    addition (doc 174 / task #34) just paid for itself on its first
    run.
