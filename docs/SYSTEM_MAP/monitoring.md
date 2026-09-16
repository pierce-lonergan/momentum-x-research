# monitoring.md — alerts, watchers, recon, runbooks

The 30% of the system that isn't pipeline or scoring: how the operator
knows what's happening + what to do when it isn't.

---

## Alert taxonomy (Discord)

Two webhook channels:

| Channel | Frequency | Severity | Examples |
|---|---|---|---|
| **OPS_ALERT_WEBHOOK_URL** | ~5 msgs/day | CRITICAL + HIGH | HEDGE_VIOLATION, EMERGENCY_STOP_FAILED, L2_WATCHDOG stale, halt-blocked entry, EOD summary |
| **OPS_WATCHLIST_WEBHOOK_URL** | continuous | INFO | Per-trade BUY / FILLED / SOLD, intraday watchlist, fast-path scores |

### Severity stratification (D314)

| Severity | Behavior | Retry budget |
|---|---|---|
| **CRITICAL** | Injects `@here` mention (subject to D313.v3 5-min debounce) | 50 cycles (~50 min) |
| **HIGH** | No mention, embed only | 20 cycles |
| **INFO** | No mention, embed only | 20 cycles |

### Alert lifecycle

1. Caller invokes `alert_critical()` / `post_trade_open()` / etc.
2. `_post()` writes payload to `data/alerts/YYYY-MM-DD/<ts>_<seq>_<sev>.json` BEFORE live POST (D314)
3. Live POST attempted (5s timeout)
4. On 2xx: spool file deleted
5. On failure: spool file persists; `run_retry_loop` (background task) retries every 60s
6. Records older than 24h move to `data/alerts/stale/` for operator review
7. Records ≥2h old delivered with `[STALE - originally fired at HH:MM ET]` prefix (D314.v2)

**Guarantee:** alert delivery failure never blocks the trading thread.

---

## Watchers (background async tasks)

| Watcher | Interval | Purpose |
|---|---|---|
| **Track B recon daemon** | 30s | Broker-truth vs internal tracker; shadow mode (logs only, no action) |
| **L2 HedgeIntegrityWatcher (D313)** | 20s | Invariant: `qty != 0 → has_protective_order`; auto-submits emergency stop after 60s tolerance |
| **L2_WATCHDOG (D313.v2)** | 60s | Watches the watcher: pages `@here` CRITICAL if L2 heartbeat > 90s stale OR task crashed |
| **D314 retry loop** | 60s | Walks `data/alerts/` for undelivered records; redelivers; quarantines stale |
| **Phase 3 keeper loop** | 60s | Position management, stop ratcheting, tranche exits |

**Wiring order in `main.py`:** Track B → L2 → L2_WATCHDOG → D314 retry → D315 boot self-test. If any FAIL to launch, an ERROR is logged but the bot continues — operator sees the missing component via D315 boot self-test status field.

---

## Reconciliation

| Code | When | Mechanism |
|---|---|---|
| **D222 PNL_RECON** | EOD | Journal-derived vs broker-derived P&L; tolerance $1 USD |
| **D238 EOD_BROKER_TRUTH_RECON** | EOD | Mirror of D222 with inverted direction (broker as source) |
| **D311 after-filter** | every call | All `get_orders` for recon use `after=<today UTC midnight>` to prevent historical false positives |
| **D295 D91 journal close** | per next-open exit | Records explicit close in journal when D91 fires carry-over close |
| **D297 OTO stop-fill journal** | per stop-leg fill | Records close when OTO stop leg fills |
| **90-day audit ledger** | one-shot 2026-05-24 | Historical scan: 185 unhedge windows found, persisted as `data/shadow_stops/historical_unhedge_audit_90d.json` |

---

## D315 — Boot self-test

Posted once at startup, INFO severity (no `@here`):

```
🟢 Boot self-test (D315)
🤖 Bot startup @ commit `6a365fd` (08:30 ET)
  T2: ON  HALT: OFF
  L2: OK  L2_WATCHDOG: OK  D314_RETRY: OK
  Account equity: $148,153.35
```

**Operator contract:** if this message doesn't land in Discord within 60s
of `schtasks /run`, something didn't launch. Do NOT continue the session.
Revert `MOMENTUM_T2_ENABLED=0`, restart, investigate.

---

## Runbooks

| Runbook | Trigger | Location |
|---|---|---|
| **HEDGE_VIOLATION 60-second response** | `D313 HEDGE_VIOLATION` `@here` Discord alert | [`docs/RUNBOOK_HEDGE_VIOLATION.md`](../RUNBOOK_HEDGE_VIOLATION.md) |
| EMERGENCY_STOP_FAILED | `D313 EMERGENCY_STOP_FAILED` Discord | covered in RUNBOOK_HEDGE_VIOLATION step 2-3 |
| L2_WATCHDOG heartbeat stale | `D313.v2 L2_WATCHDOG ... stale` Discord | covered in RUNBOOK_HEDGE_VIOLATION step 4-5 |
| Repeated HEDGE_VIOLATION (>3 single session) | `@here` debounce note + 3+ alerts | RUNBOOK section "Response — Repeated HEDGE_VIOLATION" |
| EOD recon $-divergence | `D222 PNL_RECON DIVERGENCE` log | none yet (filed) |

The HEDGE_VIOLATION runbook covers the operator-side 60-second response.
Without it, the L2 detection is wasted if the operator doesn't know what to
do. **Pin the runbook open in a browser tab during market hours.**

---

## Anti-patterns (operator DO-NOTs)

From the HEDGE_VIOLATION runbook + doc 174:

- ❌ Do not manually cancel the L2 emergency stop — that's exactly what created the NXXT 66h unhedge
- ❌ Do not flip `MOMENTUM_T2_ENABLED` between trades — arm assignment is deterministic per `(date, symbol)`; mid-session toggle = inconsistent decisions
- ❌ Do not `git pull` and restart mid-session to "fix" something — wait for EOD
- ❌ Do not clear `data/alerts/` mid-session — spool is the redelivery queue
- ✅ Do log every intervention to `docs/incidents/YYYY-MM-DD_*.md` for EOD post-mortem

---

## Code freeze status

**ACTIVE: 2026-05-24 22:00 ET → 2026-05-26 16:00 ET (Tuesday market close).**

Allowed to ship: observability, alerting, runbooks, off-bot tools, documentation, SYSTEM_MAP updates.

FROZEN: any change to orchestrator verdict logic, executor sizing, OTO/stop submission, gate filter rules, T2 arm assignment, qty_multiplier values, env var defaults for trading paths.

If the bot is misbehaving and the fix requires touching trading paths, the
response is **REVERT (T2 OFF + restart), not LIVE-EDIT.**

---

## Pre-committed ratchet criteria (T2 wide-arm sizing)

Per [`backlog.md`](backlog.md) `T2_stop_widening_ab` ratchet_criteria:

| Step | qty_multiplier | Trigger |
|---|---|---|
| Start | 0.5 (current) | Monday 2026-05-25 launch |
| Bump 1 | 0.75 | 5 sessions AND cum > +$3k AND 0 HEDGE_VIOLATION |
| Full | 1.0 | 10 sessions AND cum > +$8k AND 0 HEDGE_VIOLATION |
| Revert | 0.5 → 0.25 → 0 | Any EMERGENCY_STOP_FAILED OR 3+ HEDGE_VIOLATION/session |

**Override discipline:** if I want to ratchet outside these criteria, the override must land as a new doc with the explicit reasoning BEFORE env var changes. No mid-session ratchets. Codified `even if Monday is +$5,000`.

---

## See also

- [`docs/RUNBOOK_HEDGE_VIOLATION.md`](../RUNBOOK_HEDGE_VIOLATION.md) — operator response steps
- [`architecture.md`](architecture.md) — defense layer wraparound diagram
- [`d_codes.md`](d_codes.md) — D304/D308/D312/D313/D314/D315 entries
- [`experiments.md`](experiments.md) — T2 abort criteria + ratchet criteria
