# 49 — Bug AJ: Morning resolution alert + hold-overnight env override

**Status:** patched 2026-04-27 evening (post-close, after Bug AI).
**Severity:** **MEDIUM** — operational visibility gap. Doesn't directly cost P&L; instead, it allows BIG P&L losses to hide because the operator doesn't know overnight positions exist until they look.
**Surface:** **Strategic assessment from today's session-log review.** The LIDR position carried since 2026-04-25 (Bug Z intervention) bled $1316 today and would have continued bleeding tomorrow without explicit operator awareness.
**Bug class:** missing notification surface — operator runbook §1 says "read startup log" but doesn't mandate seeing the overnight-position warning, and the `OPS_ALERT_WEBHOOK_URL` infrastructure existed in env_audit but was never wired to the D91 detection path.
**Predecessors:** Bug AG/AH/AI (today's three live-surfaced production bugs), `42_monday_operator_runbook.md` §1.

---

## §0 — TL;DR

Three additions:

1. `src/monitoring/alerts.py::alert_morning_resolution_sync` — new helper that posts a Discord embed to `OPS_ALERT_WEBHOOK_URL` with per-position P&L, days held, and the env escape hatch description.

2. `main.py` D91 startup block — wires the alert to fire whenever `_overnight_positions_to_close` is non-empty.

3. `MOMENTUM_HOLD_OVERNIGHT=TICKER1,TICKER2` env var — operator override that REMOVES specified tickers from the auto-close list. Useful when:
   - Overnight gap-up scenario where the position should be held
   - Operator has a different exit strategy than auto-close at market open
   - Recovery from a Bug-AI-class deadlock where the position couldn't be closed yesterday

The combination: operator gets a Discord ping at startup, sees the positions + P&L + days held + override mechanism, and can act before Phase 0.

---

## §1 — Root cause

The codebase had:
- `OPS_ALERT_WEBHOOK_URL` defined in `src/utils/env_audit.py:80` and properly populated from secrets
- `src/monitoring/alerts.py` with `alert_critical_sync` + supporting infrastructure
- `main.py:1275-1280` D91 detection emitting a `logger.warning`

But:
- The webhook URL was **never actually called from any code path** (`grep -rn 'OPS_ALERT_WEBHOOK_URL' src/` returns only the env_audit definition)
- The D91 warning was buried in a 50K+ line log that only an operator manually grepping would see

In production today (2026-04-27): LIDR was in the warning at 04:30:10 ET startup. The operator (Pierce) didn't see it until ~17:00 ET when reviewing why the day showed "0 trades." 12.5 hours of operational exposure on a position that had been silently bleeding.

---

## §2 — Why production allows this state

The infrastructure was scaffolded but never wired. Two factors:

1. **Sprint focus**: the discovery infrastructure work (Tracks A/B/C/D, all 6+1 layers) prioritized DETECTION over NOTIFICATION. The system can detect Bug-Z-class divergence, log D86/D107/D240/D91 — but the operator only sees these via post-hoc log review.

2. **Webhook silence is non-fatal**: an unset `OPS_ALERT_WEBHOOK_URL` doesn't prevent the system from running. The env_audit warns at startup but doesn't fail. The path-of-least-resistance for the developer was to add detection logic without adding the alert wiring.

The result: the D91 warning has been firing on every overnight-carry session for at least 4 days (since the LIDR Bug Z intervention on 2026-04-25), and the operator only learned about the cumulative exposure today.

---

## §3 — Fix

### `src/monitoring/alerts.py`

New function `alert_morning_resolution_sync(*, overnight_positions, webhook_url)`:
- Takes a list of dicts with `{ticker, qty, entry, current, pnl_dollar, pnl_pct, days_held}`
- Builds a Discord embed:
  - Title: ⚠️ Morning Resolution Required (D91 Overnight Positions)
  - Description: per-position lines with P&L emoji + values, total P&L summary, default action description, env-override instructions
  - Color: amber (0xF39C12) — distinguishable from critical-red alerts
- Posts via the existing `_post_sync` helper
- Wrapped in defensive try/except — never crashes the startup path

### `main.py` D91 block

Two additions immediately after the existing `D91: %d OVERNIGHT positions detected` warning:

1. **`MOMENTUM_HOLD_OVERNIGHT` env override**: parses comma-separated tickers, partitions `_overnight_positions_to_close` into "will-close" and "will-hold," logs the partition, and updates the auto-close list.

2. **Discord alert post**: builds the per-position payload by joining tracker info with broker `_startup_broker_pos` data (for current price + P&L), then calls `alert_morning_resolution_sync`. Wrapped in try/except for non-fatal posting.

### `tests/unit/test_bug_aj_morning_resolution.py` (NEW, 6 tests):

- `test_no_webhook_url_silently_returns` — no-op when env var unset
- `test_empty_positions_silently_returns` — no-op when no overnights
- `test_single_position_payload_shape` — embed contains ticker/qty/entry/current/days/override
- `test_multi_position_aggregates_total_pnl` — total P&L sums correctly across positions
- `test_alert_color_is_warning_amber_not_error_red` — distinguishable from critical alerts
- `test_post_sync_failure_doesnt_raise` — defensive: Discord outage doesn't crash startup

---

## §4 — How this would have manifested in production today

If Bug AJ had been in place at 04:30:10 ET startup, Pierce would have received a Discord embed in the OPS_ALERT channel:

```
⚠️ Morning Resolution Required (D91 Overnight Positions)

MORNING RESOLUTION REQUIRED — 1 overnight position(s) carried into today.

  ❌ LIDR qty=5264 entry=$2.42 cur=$2.20 P&L=$-1158 (-9.1%) held=4d

Total unrealized P&L: ❌ $-1,158.00

Default action: auto-close at market open via _close_overnight_position.
To override, set MOMENTUM_HOLD_OVERNIGHT=TICKER1,TICKER2 in env...

Investigate: check logs/momentum_<date>.log for D91 lines + review
recent journal entries before market open.
```

Pierce would have seen this at 4:30 AM (or whenever he looked at his phone), and could have:
- Set `MOMENTUM_HOLD_OVERNIGHT=LIDR` if he wanted to hold (not the case today)
- Manually closed LIDR pre-market (today's actual right call)
- Investigated the 4-day carry sooner

Instead, the position bled until 17:00 ET when log review surfaced it.

---

## §5 — How this connects to Bug AG / AH / AI

Today surfaced FOUR production bugs in one trading day:
- **Bug AG** (08:00 AM): Track B daemon UnboundLocalError on `_d217_hb_repo`
- **Bug AH** (post-close): Track B daemon `run_eod_invariants` returns None
- **Bug AI** (post-close): Bridge can't force-close when external stop holds shares
- **Bug AJ** (this commit): Morning resolution alert was never wired

The pattern across all four: **detection layer existed, action/notification layer was missing**. Bug AJ closes the operator-notification gap; AG/AH closed the daemon-launch and tick-execution gaps; AI closes the close-coordination gap.

After AG+AH+AI+AJ, the discovery-infrastructure-to-operational-action pipeline is meaningfully tighter.

---

## §6 — Discovery rate impact

| Pre-Bug AJ | After Bug AJ |
|---|---|
| 21 production / oracle / harness bugs surfaced + fixed | **22** |
| 0 patches introduced regressions | **0** |
| Discovery rate ratio | **22/0 = ∞** |

Bug AJ is the **fourth bug surfaced + fixed in today's session.** The combined ship rate (4 in 24 hours) is the highest single-day discovery rate of the project to date, and validates the operator-log-reading discipline as a complement to the test layers.

---

## §7 — What changes downstream

- **Next session start**: if any positions are overnight, a Discord alert fires within 1-2 seconds of D91 detection (before Phase 0 begins, ~30 seconds before pre-market scan).
- **`MOMENTUM_HOLD_OVERNIGHT` env var** is the operator's single-knob control for overnight-position policy. No code change needed to override.
- **`docs/research-log/26_d_code_registry.md`** doesn't need a new code (D91 is already registered).
- **Bug-letter alphabet advances to AK** for the next surface.

---

## §8 — The discipline this preserves

Per `42_monday_operator_runbook.md` §1: "First 5 minutes — read the startup log." Bug AJ converts this from **tribal knowledge** ("the operator knows to grep for 'D91'") to **infrastructure** ("the system PINGS the operator with the relevant info").

The Bug AJ pattern generalizes: anywhere we have a `logger.warning` for a state that requires operator action, there should also be a Discord alert. This commit does it for D91; future commits should extend the pattern to:
- D231 RECON_HARD_BLOCK (broker-vs-internal qty drift)
- D247 SMART_EXIT_ESCALATE (close-failure escalation; partially obsoleted by Bug AI)
- D249 STOP_REARM_FAILED (post-close stop re-arm failure)
- D86 PRESERVED_STOP mismatch (stop_oid vs broker disagree)

The 22/0 ratio holds. Cost of this fix: ~150 LOC + 6 tests + 1 doc + 1 wire-line in startup.
