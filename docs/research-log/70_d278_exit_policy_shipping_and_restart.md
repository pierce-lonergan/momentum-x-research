# 70 — D278 EXIT_POLICY shipped: t1_next_open mode + restart procedure

**Status:** shipped 2026-04-29 PM. Commit pending push. Restart required for new policy to take effect on running bot.
**Severity:** PRODUCTION change. Modifies live (paper) trading behavior. Skips intraday BAR-1 EXIT (T+60s) + D101 §3.5 TIME_EXIT (T+20min) when env `MOMENTUM_EXIT_POLICY=t1_next_open` is set.

---

## §0 — TL;DR

Shipped Enhancement 1 from doc 69: `MOMENTUM_EXIT_POLICY` env var that disables BAR-1 EXIT (D146) + D101 §3.5 TIME_EXIT when set to `t1_next_open`. Per doc 69 broker-truth counterfactual analysis: this change converts the strategy from -$5K (intraday-only) to +$59K (T+1 next-day open) on the actual prod 117-trade pool — net incremental +$23K per equivalent trade pool.

13 regression tests added (all passing). 55 adjacent tests still pass. Default behavior is `bar1_legacy` (the existing mode); the new behavior is opt-in via env var.

---

## §1 — Code changes

### `config/settings.py` (ExecutionConfig)

```python
exit_policy: str = Field(
    default="bar1_legacy",
    description="D278 EXIT_POLICY (2026-04-29): which intraday-exit rules fire. ..."
)
```

### `main.py`

Added two module-scope helpers:
- `_d278_active_policy(settings) -> str` — resolves the active policy. Priority: env var → config → safe default.
- `_d278_bar1_gated_off(settings) -> bool` — convenience wrapper.

Wired the helpers into BOTH BAR-1 EXIT call sites:
- **Phase 2** (post-fill scheduler, line ~3922): if gated off, logs the skip + does not schedule the T+60s sell task.
- **Phase 3** (monitoring loop, line ~4664): if gated off, the BAR-1 EXIT branch is skipped.

### `src/execution/exit_intelligence.py`

D101 §3.5 TIME_EXIT (the 20-minute "no 1R move → exit" rule) gated on the same env var. Without this, positions would be force-closed at T+20min and never carry overnight.

### What was NOT changed

These are deliberately preserved (still active under `t1_next_open`):
- **Stop-losses** — essential downside protection
- **D245/D246/D247 SMART_EXIT_ESCALATE** — error-recovery path for failed closes
- **D163 software trailing stops** — locks in gains; doesn't force exit
- **D164 EARLY_PROFIT_TAKE** — captures early gains in T+120-300s window; helpful for the new policy
- **D78 SMART_EXIT composite signal** — signal-based exit (manipulation phase, contagion, etc.); intentionally retained
- **D86/D91/Bug AJ overnight detection** — used by the new policy: positions become "overnight" at session close → next morning's close-at-open path closes them at T+1 open

---

## §2 — Restart procedure

The currently-running bot (started 04:30 ET on commit `b62488e9`) is on the OLD code. It must be killed and restarted for the new policy to take effect.

### Step 1 — set the halt switch (precaution)

Before the restart, set the halt switch in the launcher env so the new bot doesn't immediately take entries while you verify the policy is active:

```
$env:MOMENTUM_HALT_NEW_ENTRIES = "1"
```

(Or whatever your launcher uses — `.env` file, batch script, etc. Add `MOMENTUM_HALT_NEW_ENTRIES=1`.)

### Step 2 — kill the running bot

Find the PID and kill the process. From your earlier startup banner: `pid=35940`.

```powershell
Stop-Process -Id 35940
# Or, find by name:
Get-Process python | Where-Object {$_.MainWindowTitle -match "momentum"} | Stop-Process
```

### Step 3 — pull the latest code

```
cd <repo-root>
git pull origin develop
```

You should see commit (TBD — will be the commit hash this commit gets pushed as).

### Step 4 — set the new exit policy

In your launcher env (or `.env`, batch script, PowerShell session):

```
$env:MOMENTUM_EXIT_POLICY = "t1_next_open"
```

### Step 5 — start the bot

Whatever your normal start command is. The bot will print the startup banner including ENV_AUDIT.

### Step 6 — verify the policy loaded correctly

Watch the log for these confirmations:

1. **D278 startup line should be visible** (NOT YET added to ENV_AUDIT — see §4 future work). For now, verify by looking for log lines like:
   ```
   D278 BAR-1 SKIPPED Phase2 <TICKER>: exit_policy=t1_next_open ...
   ```
   This will appear AFTER the bot fills its first OTO order today.

2. **No "D146 BAR-1 EXIT" lines for new entries** today. If you see them, the policy didn't load — the env var isn't propagating to the bot's process environment. Check your launcher.

3. **D101 §3.5 TIME_EXIT lines should also be absent** for new entries.

### Step 7 — lift the halt switch (when verified)

Once you've confirmed the policy is active and the bot is operating correctly:

```
# Remove the halt
Remove-Item Env:\MOMENTUM_HALT_NEW_ENTRIES
# Or set to 0:
$env:MOMENTUM_HALT_NEW_ENTRIES = "0"
```

You'll need to restart the bot AGAIN OR the bot's halt-switch check will pick up the change on its next `submit_oto_order` call (it re-reads env per call). Verify by watching logs for `D277 HALT_NEW_ENTRIES` to STOP appearing.

---

## §3 — Expected behavior changes today

With `MOMENTUM_EXIT_POLICY=t1_next_open` set:

**Same as before**:
- Pre-market scanning, candidate evaluation, MFCS scoring, D124 consensus
- OTO entry orders submitted with stops attached
- Position tracking, recon, trade journaling
- Discord alerts for entries

**Different from before**:
- Positions opened today will NOT auto-close at T+60s (BAR-1 EXIT skipped)
- Positions opened today will NOT auto-close at T+20min if no 1R move (D101 §3.5 skipped)
- Positions held into 16:00 ET close BECOME OVERNIGHT POSITIONS
- Bug AJ Discord alert WILL FIRE morning of 4/30 listing all carried positions
- At 09:30 ET on 4/30, existing close-at-open path closes all overnight positions at next-day open

**Intermediate-time exits still ACTIVE** (NOT skipped by D278):
- D78 SMART_EXIT composite signal (manipulation, contagion, distribution)
- Stop-loss (5.5% default)
- D245 SMART_EXIT_ESCALATE error recovery
- D163 trailing stops (after +2% gain)
- D164 early profit take (T+120-300s window)
- Tranche limit fills (D165 take-profit ladder)

---

## §4 — Known gaps / future work

1. **ENV_AUDIT does NOT include MOMENTUM_EXIT_POLICY at startup.** Should be added to SAFETY_KILL_SWITCHES audit alongside MOMENTUM_HALT_NEW_ENTRIES so future startups make the policy state visible at-a-glance. ~5 min change in next session.

2. **No t5_close mode shipped.** The doc 69 counterfactual showed T+5 close = +$71K (vs T+1 +$59K) but only on 13 trades with survivor bias. Adding t5_close mode would require more complex carry tracking. Deferred until t1_next_open is validated for ≥30 days.

3. **D78 SMART_EXIT composite signal not yet stratified.** Some D78 signals (urgency, manipulation phase) may close positions intraday. If t1_next_open positions get closed by D78 signals, that's expected behavior (signal-based, not time-based). But operator should monitor for unexpected D78 closes.

4. **Bug AJ Discord noise.** Every morning under t1_next_open, the overnight Discord alert will fire for ALL held positions. This is currently designed for unexpected carries (LIDR pre-Bug-AP). The alert format should eventually distinguish "intentional T+1 carry" from "unexpected overnight." Not a blocker; just noisy.

5. **Multi-day carries (>T+1).** Under current implementation, EVERY position closes at T+1 open. The CRCA-class +$40K outlier needed 5-day hold. Capturing that pattern requires either:
   - Setting `MOMENTUM_HOLD_OVERNIGHT=TICKER1,TICKER2,...` env to whitelist specific tickers for multi-day hold
   - OR shipping a t5_close mode (deferred per §4.2)

---

## §5 — Quantified expected EV (per doc 69)

Per 117-trade window (the broker-truth counterfactual sample):
- ACTUAL (mixed prod policy): +$36K realized
- Pure intraday (T+60s through T+30min): -$1K to -$5K
- **t1_next_open**: +$59K (vs ACTUAL +$36K = **+$23K incremental**)
- t5_close (small sample): +$71K (vs ACTUAL +$36K = +$35K incremental, survivor-biased)

**t1_next_open incremental EV: +$23K per ~3-month trade pool (estimated).** Annualized: ~+$92K, on a $100K starting equity = ~+92% incremental return per year.

**Caveats**:
- Counterfactual sample is 117 of 280 trades (42% coverage; bar coverage is biased toward tickers prod traded)
- Forward results may differ from historical counterfactual due to regime change
- Overnight gap risk is NEW: positions held overnight are exposed to news/halt/gap risk that intraday-only entries avoid
- Stop-losses still protect, but a position gapping down 20% overnight blows through any reasonable stop

---

## §6 — Halt switch posture

**The halt switch is INDEPENDENT of the exit policy.** Setting `MOMENTUM_EXIT_POLICY=t1_next_open` does NOT lift the halt; setting `MOMENTUM_HALT_NEW_ENTRIES=1` does NOT change the exit policy.

For today's session, the recommended sequence:
1. Halt switch ON before restart
2. Restart bot with new exit policy
3. Verify policy loaded correctly (per §2 step 6)
4. Lift halt switch when verified

If at any point you want to revert: set `MOMENTUM_EXIT_POLICY=bar1_legacy` (or unset the env var entirely) and restart. The bot will revert to original BAR-1 EXIT at T+60s + D101 §3.5 TIME_EXIT at T+20min.

---

## §7 — Status: SHIPPED (commit pending push)

- ✅ `config/settings.py`: `exit_policy` field added with discipline-aligned default
- ✅ `main.py`: `_d278_active_policy` + `_d278_bar1_gated_off` helpers + Phase 2 + Phase 3 BAR-1 gates
- ✅ `src/execution/exit_intelligence.py`: D101 §3.5 TIME_EXIT gated on env
- ✅ `tests/unit/test_d278_exit_policy.py`: 13 tests including source-grep guards on both BAR-1 sites + D101 site
- ✅ Adjacent regression: 55 unit tests still pass
- ✅ Settings load test: default is `bar1_legacy`
- ✅ This restart-procedure doc

**Discovery rate: 30/0 holds.** No production bugs. The change is opt-in via env var; default behavior unchanged.

**Ready for restart.**
