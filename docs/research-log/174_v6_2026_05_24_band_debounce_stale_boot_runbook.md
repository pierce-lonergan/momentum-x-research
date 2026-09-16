# 174 — 2026-05-24 Monday-blocker fix: band false-positive + debounce + stale + boot + runbook

**Session date:** 2026-05-24 deep Sunday night
**Branch:** develop
**Predecessor:** [doc 173 safety hardening](173_v6_2026_05_24_safety_hardening_d310_d313v2_d314.md)
**Trigger:** Pierce's review of doc 173 flagged the L2 correctness band as a **Monday-blocker**: it could false-positive on legitimate wide-arm ATR stops and trigger emergency-stop intervention that wrecks the very trade T2 is supposed to make work.

---

## 0. Pierce's headline finding — verified empirically

Pierce flagged: *"The L2 correctness band may false-positive on wide arm. A 0.30 lower band would wreck the wide arm on its first volatile microcap trade because of the safety net."*

I verified against the 14 historical wide-arm shadow events:

```
ATR-stop distance distribution (N=14):
  min:     5.65%   p25:  9.56%   median: 15.69%   p75:  27.67%
  p90:    31.37%   p95: 60.91%   p99:   60.91%    max:  60.91%

  Trades exceeding 0.30 band: 3 / 14 = 21.4%
    NIVF -31.04%    NXXT -31.37%    AUUD -60.91%
```

**The operator was right.** 21% of historical wide-arm stops would be false-positive flagged. NXXT misses by 1.4pp; AUUD misses by 30pp. If T2 fires Monday on either ticker class, the L2 watcher's emergency-stop intervention destroys the trade.

---

## 1. D313.v3 — band widened to 0.65 (Pierce's option 1, empirically calibrated)

`src/monitoring/hedge_integrity_watcher.py:_stop_band_min_pct` raised from **0.30 → 0.65**.

| Threshold | Historical false-positive rate | Notes |
|---|---|---|
| 0.30 (old) | **21%** | Wrecks NXXT/AUUD/NIVF on first trade |
| 0.50 | 7% | Still catches AUUD-class |
| **0.65 (new)** | **0%** | 4pp safety margin above worst observed (60.91%) |
| 0.99 | 0% | Trivially passes everything; defeats the check |

**Why 0.65 and not Pierce's option 2 (arm-aware)?**
- Pierce's option 2 (`tight_stop` gets 0.30, `wide_stop` gets 0.60) is architecturally cleaner but requires plumbing position→arm metadata through the watcher.
- The L2 watcher reads from broker positions; the broker doesn't know about T2 arm labels.
- Adding `client_order_id` tagging is reasonable scope but creeps to a 2-hour change vs the 5-minute one.
- The 0.65 single-band is **empirically calibrated**: it catches absurd stops (>$0.01 on $100 = 99.99% off) while accommodating every observed legitimate wide-arm stop with margin.
- Option 2 is filed as `D313.v4` for next week if any edge case appears.

**Regression test** asserts the p95 of historical stops fits inside the new band: `test_historical_p95_stop_distance_passes_band` — runs on every commit, catches future regressions if a wider stop pattern emerges.

---

## 2. D313.v3 — `@here` debounce (Pierce's concern #2)

`alerts.py` — new `_should_mention_at_here(webhook_url)` with 5-minute cooldown per-webhook:

```python
_last_critical_mention_utc: dict[str, float] = {}
MENTION_COOLDOWN_SEC = 300.0  # 5 minutes
```

Both `_post()` and `_post_sync()` route the `@here` injection through this function. The alert **still fires** (operator sees it in the channel) — just without the audible mention if another CRITICAL was pinged in the last 5 min. Per-webhook keying so the ALERTS channel debounce is independent from the WATCHLIST channel.

**Net effect**: a 3-minute broker degradation that triggers HEDGE_VIOLATION 3× = 1 audible ping + 2 silent embeds, not 3× audible pings.

---

## 3. D314.v2 — stale-prefix on redelivered records (Pierce's concern #3)

`durable_alert_spool.py` — new `_maybe_inject_stale_prefix(record)`:

```python
STALE_PREFIX_THRESHOLD_SEC = 2 * 3600  # 2 hours
```

Records redelivered >2 hours after their original spool time get `[STALE - originally fired at 11:47 PM ET] ...` prepended to `payload.content`. Operator sees the alert AND knows it's a delayed redelivery, not fresh. Wired into `_retry_one()` so it fires on every retry attempt without modifying the synchronous `durable_post` path.

Idempotent: a second retry tick won't double-prefix (`"[STALE" in existing` check).

---

## 4. D315 — boot self-test (Pierce's "ship before Monday" #2)

`main.py` — after the L2 watchdog + D314 retry loop launch, composes a single Discord embed:

```
🟢 Boot self-test (D315)
🤖 Bot startup @ commit `6a365fd` (08:30 ET)
  T2: ON  HALT: OFF
  L2: OK  L2_WATCHDOG: OK  D314_RETRY: OK
  Account equity: $148,153.35
```

Posted with `severity="INFO"` so no `@here` ping — purpose is *operator-sees-confirmation*, not *operator-paged*. Color is green if all 3 watchers launched, amber otherwise.

**Double-purpose**: this is also the first end-to-end test of the `durable_post` pipeline every session. If the message lands in Discord, the alert pipeline is healthy. If it doesn't, something is broken BEFORE any trades happen.

---

## 5. Operator runbook — `docs/RUNBOOK_HEDGE_VIOLATION.md`

Per Pierce's "ship before Monday" #3: the 60-second detection is wasted if the operator doesn't know what to do in those 60 seconds.

Covers:
- 3 alert classes (`HEDGE_VIOLATION`, `EMERGENCY_STOP_FAILED`, `L2_WATCHDOG heartbeat stale`)
- Step-by-step Alpaca console actions
- Manual emergency-stop computation (`current × 0.92` for longs)
- Incident logging template
- Repeated-violation abort criteria (T2 OFF + restart)
- Anti-patterns (DO NOT manually cancel the L2 emergency stop — the exact NXXT-class mistake)
- After-incident EOD checklist

Tests assert: runbook exists, covers all 3 alert classes, has anti-patterns section, names the specific NXXT-class mistake.

---

## 6. Code freeze + ratchet pre-commitment (Pierce's process discipline)

### Hard code freeze on trading paths

**From 22:00 ET 2026-05-24 (now) through Tuesday market close 16:00 ET 2026-05-26**:

- ✅ ALLOWED to ship: observability, alerting, runbooks, off-bot tools, data analysis
- ❌ FROZEN: any change to orchestrator verdict logic, executor sizing, OTO/stop submission, gate filter rules, T2 arm assignment, qty_multiplier values
- ❌ FROZEN: env var defaults for trading paths (`MOMENTUM_T2_ENABLED`, halt switches, etc.)

The temptation Monday at 09:45 to "just nudge this one threshold" is exactly how Sunday-night-hardening sessions get undone. **If the bot is misbehaving and the fix requires touching trading paths, the response is REVERT (T2 OFF + restart), not LIVE-EDIT.**

### Ratchet override discipline

The wide-arm sizing ratchet criteria (doc 172) are documented in markdown but enforced by me. **I hereby pre-commit:**

- I will not bump `qty_multiplier` outside the codified criteria (5 sessions + cum>+$3k + 0 D313 HEDGE_VIOLATION) **even if Monday is +$5,000**.
- If I want to override, the override must land as a new doc (175+) with the explicit reasoning **before** the env var changes.
- Same discipline for the T2→T3 cutover: 10 sessions, 1.5σ, 0 EMERGENCY_STOP_FAILED, D310 RESUBMIT_NO_CALLBACK trending to 0.

This is the kind of pre-commit The operator was right to demand. Otherwise the criteria are just words.

---

## 7. Pierce's "elevate from this-week" — Tuesday morning followups

Pierce flagged two items I'd previously deferred to "this week" but he wanted to elevate. **My response: not tonight (it's already 23:30, code freeze starts in 30 min), but these are the Tuesday-morning-priority-1 items.**

### 124-sub-60s window tick-data audit (Tuesday)

`scripts/unhedge_window_audit_tick_data.py` — for each of the 124 sub-60s unhedge windows from doc 172, pull tick data and check if any contained >2% adverse moves during the gap. If even 5/124 had real adverse moves, tighten L2 polling to 10s pre-emptively. The data is already on disk; this is purely analysis work.

### Replay regression harness (Tuesday)

Two known-painful events from doc 172:
- NXXT 2026-05-18 → 5/20 (66h unhedge)
- Friday 2026-05-22 silent BUYs (D216 ate 123 entries)

Build a test harness that replays each event sequence through current code and asserts:
- L2 fires within 60s on the NXXT replay
- D312 surfaces D216 dominance in the EOD on the Friday replay
- The boot self-test lands

This is the seed of the broader replay harness for the 15-multi-day-unhedges dataset. Every CI run = "we've proven we won't repeat history."

---

## 8. Files in this commit

| File | Change |
|---|---|
| `src/monitoring/hedge_integrity_watcher.py` | +2 LOC default param + doc comment — band 0.30 → 0.65 |
| `src/monitoring/alerts.py` | +25 LOC — `_should_mention_at_here` debounce, applied to both async/sync paths |
| `src/monitoring/durable_alert_spool.py` | +35 LOC — `_maybe_inject_stale_prefix`, called from `_retry_one` |
| `main.py` | +40 LOC — D315 boot self-test composition + post |
| `docs/RUNBOOK_HEDGE_VIOLATION.md` | NEW operator runbook |
| `tests/unit/test_doc174_band_debounce_stale_boot.py` | NEW, 24 tests |
| `docs/research-log/174_v6_2026_05_24_*.md` | THIS doc |

**Tests: 24/24 doc 174 + 270/270 full regression** across all touched + adjacent suites.

---

## 9. Updated Monday morning checklist

### Pre-open (operator)

```powershell
[Environment]::SetEnvironmentVariable('MOMENTUM_T2_ENABLED', '1', 'User')
schtasks /end /tn MomentumX-PaperTrading
schtasks /run /tn MomentumX-PaperTrading
```

### Within 60s — confirm D315 boot self-test lands in Discord

If you don't see `🟢 Boot self-test (D315)` in the ops channel within 60 seconds:
1. Something didn't launch
2. Open the bot log — look for the failure mode (T2 missing? L2 missing? D314_RETRY missing?)
3. **Do not let the session continue with unknown watcher state.** Revert via `MOMENTUM_T2_ENABLED=0` and investigate before re-enabling.

### Pin the runbook open

Open `docs/RUNBOOK_HEDGE_VIOLATION.md` in a browser tab and leave it open during market hours. If `@here D313 HEDGE_VIOLATION` lands, you have 60 seconds and you don't want to be searching for the steps.

---

## 10. Verdict

**Status:** **SHIPPED 2026-05-24 23:42 ET.** Code freeze active from 22:00 ET (retroactive) through Tuesday market close.

Pierce's review caught a Monday-blocker (the band false-positive) before the bot ever got the chance to wreck a wide-arm trade with its own safety net. The fix was 1 line of code and 1 line of empirical justification. Without his review, T2 could have launched Monday with a built-in self-destruct on the very trade type the wide-stop thesis targets.

**This is what a good safety review actually looks like.** Tonight closed:
- 1 Monday-critical bug (band false-positive)
- 2 operational quality issues (`@here` spam, stale-alert confusion)
- 1 visibility gap (boot self-test)
- 1 response-capability gap (runbook)

The path to 10%/day still requires the wide-stop edge to hold in production. **For the first time, we'll know within 60 seconds when it's working AND when it's broken AND we'll have a written response for the latter.**
