# 287 — WEEK ONE UNDER THE CORRECTED POSTURE: the honest read, and the reliability fixes that make week two real

**Author**: Claude (Fable 5, ultracode; 4-hunter fleet wf_a5be034a — reliability / pnl-posture / instruments / goal-reality — interrupted mid-run, findings salvaged from agent transcripts and re-verified by hand) | **Date**: 2026-07-11 (Sat, market closed; bot idle) | **Class**: WEEKLY FORENSIC + 3 RELIABILITY/OBSERVABILITY FIXES (validated, tested, weekend-inert) + operator recommendations | **Mandate (Pierce)**: "deep dive on how the system performed this week. full send. remember we are trying to achieve a .5% to 1% daily increase in the account." + "fix why this system failed today / didn't run correctly."

> **The number, not bent: the account went −1.16% this week ($192,539.20 → $190,309.44, −$2,229.76).** Almost all of it is two things that are now fixed or already closed: **50% uptime** (two of four scheduled days lost to a single preflight timeout) and **one overnight-carry cycle** (the "+0.76%" Monday was a paper mark on carried positions that reversed into Tuesday's −1.75%). The one day that traded cleanly under the fully-corrected posture (7/9) was −0.15% — a small loss, squarely on the honest frontier of doc 283, not the +0.5–1%/day target. This doc lands the reliability fixes; it does not claim to have found the alpha, because the alpha isn't the thing that broke this week.

---

## §1 — The account, straight from the broker

Portfolio-history (paper), 1D resolution, ET-labelled. Now: **$190,309.44, 0 positions.** (`data/research/doc287/week_truth_2026-07-11.txt`.)

| Date (ET) | Equity close | Δ$ | Δ% | Ran? |
|---|---|---|---|---|
| Fri 07-02 | $192,539.20 | — | — | (prior wk close = week basis) |
| **Mon 07-06** | $193,997.32 | **+1,458.12** | **+0.76%** | yes |
| **Tue 07-07** | $190,600.81 | **−3,396.51** | **−1.75%** | yes |
| **Wed 07-08** | $190,600.81 | 0.00 | 0.00% | **NO — preflight abort** |
| **Thu 07-09** | $190,309.44 | **−291.37** | **−0.15%** | yes |
| **Fri 07-10** | $190,309.44 | 0.00 | 0.00% | **NO — preflight abort** |

**Week: −1.158%.** Trailing 2-week arc: $199,347.70 → $190,309.44 = −4.53%.

## §2 — What actually happened on the two days that traded and mattered

**Mon +0.76% and Tue −1.75% are one event, not two.** The fills tape proves it:

- **RIVN** (1,552 sh) and **LUCY** (12,842 sh) were **opened Monday 7/6 and closed at Tuesday's open** — carried overnight. RIVN round-trip realized **−$1,940.69**; LUCY **+$1,027.36**. Net carry **−$913**.
- Monday also *flushed* three prior-week overnight carries (YRD 8,316 sh, PPCB 2,116 sh, TC 1,095 sh — all sells, no matching buys in-window), which lifted Monday's equity.
- Monday's close **marked the RIVN/LUCY carries to a paper gain** (part of the +$1,458). Tuesday those marks **reversed into realized losses**. That is why Tuesday's −$3,396 is larger than any same-day trading: it is the give-back of Monday's paper mark plus the carry loss.
- **Net of the pair (7/6 + 7/7): −$1,938 ≈ −1.0% over two days.** That is the true cost of the carry cycle, and it is exactly the **doc-280 verdict in the flesh — overnight is the one robustly-negative arm.**

Why did positions carry overnight on 7/6? Because `MOMENTUM_EXIT_POLICY=t1_next_open` (a USER-scope Windows env var — the doc-285 invisible surface) was **still live on Monday**; it was removed for **7/7+** (now `bar1_legacy` = T+60s flatten). **This loss mode is structurally closed going forward.** The doc-285 "+0.686% Monday" excitement and this doc's "+0.76% Monday" are the same mirage: a promo mark on carried inventory, not captured alpha.

**Thursday 7/9 is the one clean datapoint.** Fully-corrected posture, no carry: two small scalps, **FBRX −$108.56 + RPGL −$182.43 = −$291 = the entire day** (realized matches the equity delta to the dollar). Both sized correctly (0.7% risk / 5% cap — the doc-285 package works). A −0.15% clean day is **not** the +0.5–1%/day target; it is a normal draw on a ~flat honest edge.

## §3 — Why the system "didn't run" — and the fixes that land Monday

**Root cause of 7/8 + 7/10 (both FLAT): a single 10-second Alpaca timeout aborted the whole session.** `scripts/preflight_check.py::check_alpaca()` did one `httpx.get(timeout=10)` on `/v2/account` with **zero retries** and treated any failure as FATAL. Both days: `[FAIL] Alpaca API: … timed out` at 04:30:15 → `PREFLIGHT FAILED — System will NOT start.` One attempt each (a healthy launch on 5/27 shows five). **Architectural inversion:** the preflight gate (10s / 0 retry) was *stricter than the running client it fronts* (`alpaca_client.py`: 30s, 3 retries D73, D87 circuit breaker). A morning blip the live bot would have shrugged off killed the day before the bot existed.

**Three fixes, all conservative, all tested, all weekend-inert (execute Monday):**

1. **`preflight_check.py::check_alpaca()` — retry + degraded-start.** Transient `httpx.TransportError` now retries 3× (15s, 2s/5s backoff). Real problems still BLOCK: missing/bad keys, HTTP 401/403, equity ≤ 0, non-ACTIVE status, any non-transport error. If all three attempts exhaust on a *transient* failure, the bot **starts DEGRADED-WITH-ALARM** (returns pass + fires the ops Discord webhook) rather than aborting — the live client's D73/D87 resilience reconnects on its own, and no order can fill while the API is down, so degraded-start is strictly safer than losing the session. **7 tests; 3 fail on pre-fix HEAD** (degrade-start, recover-on-retry, auth-message), verified via `git stash`.

2. **`watchdog_monitor.ps1` — stop lying, stop spamming.** The watchdog can only KILL a hung process; it has **no ability to LAUNCH** one. On the abort days (no process → no heartbeat file ever), the old code counted a *phantom restart* for a process it never killed, alerted "killed + Task Scheduler will auto-restart" (both false), then tripped its breaker every 2 min claiming **"restarted 3 times — manual intervention required"** (it restarted zero) — **~200 false Discord posts/abort-day**, textbook alert-fatigue. Fixed by splitting the two cases explicitly: **(A) no process exists** → nothing to kill; alert the operator *once per hour* (disk-backed dedup) with the truth ("not running; manual start required"), record **no** phantom restart, never trip the breaker; **(B) process hung** → the designed path (py-spy dump, kill, record a *real* restart), with honest messaging that relaunch is the launcher's job and may not happen. Parses clean under the prod-equivalent (no-BOM/Windows-1252) reader; kept ASCII-only to guarantee that.

3. **`config_truth_recon.py` — deepen the freshness check (fix the sentinel's blind spot).** The doc-286 recon reported the rocket-gate ledger "fresh" whenever *a row for the date existed* — but **every ran-day row (7/6, 7/7, 7/9) was measurement-dark** (`n_filled=0`, `no_bar_data=true`, 100% unmeasured). The sentinel built to catch the collector-dark class was blind to the exact instance of it. Now a rocket-gate row is fresh **only if it actually measured something** (`n_filled>0` and not `no_bar_data`); otherwise it fails loud as `DARK (present but 0 measured)`. Verified against real 7/9 data (was silent → now breaches). **56 recon+ledger tests green**, including a new test that bites on the exact 7/6-style dark row.

## §4 — Instrument health (the doc-283/284 collectors)

| Instrument | State | Verdict |
|---|---|---|
| **rocket-gate forward ledger** | DARK — gates 18/30/23 candidates but `no_bar_data:true`, 0 measured, every day | The n≥30 *forward* acceptance gate is unreachable while the forward-bar pull returns nothing. Recon now *sees* this (§3.3); the underlying bar-pull is a **real defect** — see §5. |
| **posture_delta_trend** (size-cut RESTORE gate) | DARK — file missing entirely | The `--append` subprocess writes no file (crashes or finds no sessions). The size cut stays conservative, which is *safe*, but the operator has no scoreboard to make the restore call. Recon correctly flags it. |
| **Kalshi zero-capital shadow** | FIXED this session | The scheduled task's space-in-path bug (`<local-path>` split from `Lonergan\…`) was repaired via `Register-ScheduledTask` + `cmd /c`; verified collecting ("2/200 resolved → PENDING-COLLECTION"). |
| **red-flag watchlist** | fresh | ok |

## §5 — Operator recommendations (out-of-repo infra / bigger fixes — not done autonomously)

1. **Give the bot a real relaunch path (highest-value latent gap).** `MomentumX-PaperTrading` has `RestartOnFailure Count=3/PT5M` configured but it fired **zero** retries on 7/8 and 7/10 — almost certainly the PowerShell launcher swallows the inner Python non-zero exit (Task Scheduler sees success, never retries). Either make the launcher `exit $LASTEXITCODE`, or give the watchdog a guarded launch capability. The §3.1 degraded-start prevents *this specific* death, but nothing recovers a mid-session crash.
2. **Fix the rocket-gate forward-bar pull** (`no_bar_data:true` every day) so the forward ledger can actually accumulate toward n≥30. Until then the doc-284 rocket gate is measuring nothing.
3. **Repair the `posture_delta_trend --append` job** so the size-cut restore gate is live.
4. **`Doc286TagPhantomOnce` still has the split-path bug** — the two 7/6 phantom stop-out rows were never tagged `infrastructure_contaminated`. Re-register with the `cmd /c "<full path>"` pattern (same fix as Kalshi) or tag the two rows manually.
5. **Mirror or clear `MOMENTUM_T2_ENABLED=1`** (USER-scope env, unmirrored in `.env`) — the recon flags it every night.

## §6 — Goal reality: +0.5–1%/day vs what week one shows

Doc 283 already settled this as a **negative existence proof**: the frontier of all of finance is ~0.2–0.45%/day (Medallion's best-ever ≈ 0.20%/day gross); this system's honest central frontier is **~+0.13%/day**, reaching +0.5–0.65%/day only if *every* open door validates — and that upper number was **explicitly conditional on the locate-short door, which doc-284 tombstoned** (net −1.205%/ticket). The 4-hunter fleet sharpened the read with the realized-return distribution, and it is worse than "flat":

- The realized daily return is **measured negative with a 95% CI that excludes zero in every window** — last-10 ran-days mean −0.70%/day, CI [−1.13%, −0.26%], **0/10 win-rate**; all-35 ran-days CI [−0.56%, −0.08%]. This is a statistically robust −EV book, not noise.
- +0.5%/day = **+$952/day realized**; the book delivers −$150 to −$1,341/day. That ~0.8–1.2 pp/day gap **cannot be manufactured by leverage on a losing distribution** — the size cut lowered the loss magnitude and killed the fat left tail (max corrected loss −$182 vs −$1,941 RIVN), but the sign stayed negative.
- The two doors left (rocket-gate ~+0.07%/day, Kalshi +0.05–0.2%/day on a small side-pocket) **sum to ~+0.1–0.2%/day even if both fully validate.** P(sustained +0.5%/day | current instrument set) ≈ near-zero. The goal now depends **entirely on a new edge that has not been found.**

**The fleet's decisive recommendation — CANARY, don't full-send (operator call).** Both maturing gates are **shadow / zero-capital computations that do not consume live entries**, so full-size live trading during the collection window is **pure realized bleed that buys zero edge-discovery** — tuition on a measured-negative book. The dominant play is to canary the live book to the *minimum size that still produces real fills* (keeps `config_truth_recon` + the D98/re-entry/stale-anchor guardrails exercised), keep the **scanner at full cadence** to feed the rocket-gate + posture instruments, and redirect the saved bleed into collection integrity. This beats both "bleed at full size" and "full halt."

**Honest reframe on uptime:** this week's two aborts happened to **avoid** losses (all three ran-days were negative), so the reliability fixes' near-term payoff is **not** a profit bump — it is (1) restoring the statistical power to ever validate an edge (dropping ~40% of sessions roughly doubles the calendar time to the doc-284 n≥30 verdict) and (2) system trustworthiness. **The realistic next-60-session goal is capital preservation (~flat) with the missing-day rate driven to ~0** — the first stretch where a bad number means the *strategy*, not the *plumbing*.

## §7 — What shipped (two commits)

- `scripts/preflight_check.py` — retry + degraded-start (§3.1) + `tests/unit/test_preflight_alpaca_retry_doc287.py` (7 tests, 3 bite on old HEAD).
- `scripts/watchdog_monitor.ps1` — no-process vs hung split, dedup'd honest alerts (§3.2).
- `scripts/config_truth_recon.py` — measurement-aware rocket-gate freshness (§3.3) + `tests/unit/test_config_truth_recon.py` (+1 dark-row test; 28 green).
- `scripts/post_close_scorecard.py` + `scripts/_doc280_posture_scoreboard.py` — **un-dark the posture restore gate** (§8.1): force `PYTHONIOENCODING=utf-8` on the three EOD subprocesses + harden the scoreboard's stdout, so the `Δ`-glyph print can no longer `UnicodeEncodeError` before the file append. Verified: reproduced the cp1252 crash, then confirmed a real 7/9 row now writes.
- `scripts/_doc287_week_truth.py` + `data/research/doc287/week_truth_2026-07-11.txt` — the read-only broker-truth puller and its output (§1–§2).

Combined: **63 targeted tests green; py_compile clean on all touched Python; watchdog parses clean under the prod reader.** No hot-path trading logic changed — the four fixes are reliability, honesty, and observability.

## §8 — Fleet reconciliation and the remaining work-list

The 4-hunter fleet (wf_a5be034a, 4× Opus-4.8 + synthesis, 485K tok) **independently reproduced all three §3 fixes** (same root causes, same diffs) and ranked the rest of the work-list by "profit-path gate delay × cheapness." What that surfaced beyond §3:

**§8.1 — Landed this session (safe, non-trading):** the posture restore-gate un-dark (§7). Root cause was a precise `UnicodeEncodeError` on `Δ`/`★` under the EOD's cp1252 stdout — the cheapest fix on the board, and it revives the doc-282 gate that had never written a row. (It currently reads negative → "stay cut," which is correct; the point is it can now *fire legitimately*.)

**§8.2 — Deferred (needs its own careful session; recon now fails loud in the interim):**
- **rocket-gate T+1 backfill.** `compute_session` runs 16:01 ET *same-day*, before the local minute-bar warehouse is populated → every forward ticket is a whole-session hole; the nightly `--append-today` skips existing dates so holes never backfill. Fix = a T+1 `--force` recompute once bars land (permitted by prereg-284 §3). Touches the frozen-prereg collector — not a one-line change. **The §3.3 recon-deepen means this now fails loud instead of false-greening.**
- **broker_truth_recon is carry-blind** (`eod_failsafes.py`): matches only same-day buy+sell pairs, so it called 7/6 "+$67, clean" while the account sat on −$3,380 of latent RIVN/LUCY carry. Needs a carried-position leg or a portfolio-history reconcile. Hot-path monitoring — treat its per-day figure as intraday-scalp-only until fixed.
- **EOD force-close falsely self-reported success on 7/6** (`force_closes_succeeded:2` while the positions actually swept at the 7/7 open). Tie into the doc-286 D98 absence-confirmation and add a post-close `open_positions==0` broker assertion.
- **`trade_results.jsonl` is contaminated** — the two 7/6 rows (RIVN −175 / LUCY −1,097) are D98 phantom-closes (real: RIVN −$1,941 / LUCY +$1,027); use the broker-reconciled equity bridge, not the ledger, for weekly P&L until the tagger runs.

**§8.3 — Operator decisions (not autonomous):** (1) **CANARY the live book** to minimum-fill size during collection (§6) — the highest-leverage call; (2) add **05:15 + 06:00 ET catch-up triggers** to the idempotent launcher (D90 lock makes re-runs safe — 2 free retries, no code); (3) give the **launcher a real relaunch / `exit $LASTEXITCODE`** so RestartOnFailure actually fires; (4) fix the **`Doc286TagPhantomOnce` split-path task** (same `cmd /c` fix as Kalshi) and tag the two 7/6 rows; (5) **mirror `MOMENTUM_T2_ENABLED=1` into `.env`** (75/25 is intended — mirror, don't clear).

The corrected trading posture from doc 286 is unchanged and untested-in-anger, because week one never gave it five clean days. The binding constraint is **not strategy or sizing — it is reliability and measurement plumbing.** Week two's job is to run all five days and get the instruments collecting, so the doc-284 forward ledgers can finally answer whether any edge exists at all.
