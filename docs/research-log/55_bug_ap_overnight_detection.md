# 55 — Bug AP: D56 sync's opened_at default broke D91 overnight detection (cascade-killed Bug AJ alert)

**Status:** patched 2026-04-28 morning (Tuesday, ~08:15 ET, before market open).
**Severity:** **MEDIUM-HIGH** — directly killed Bug AJ's morning-resolution Discord alert this morning. LIDR (carried since 2026-04-25) was misclassified as `overnight=0`. Operator (Pierce) had no Discord ping despite the position being 4 days old at -$1,210 unrealized.
**Surface:** Tuesday morning live-system check — operator observation that the Bug AJ alert never fired despite LIDR being clearly overnight. Verified by inspecting startup log (`D86: ... overnight=0`) and tracing to ManagedPosition.opened_at default-factory.
**Bug class:** silent default-factory mismatch — `ManagedPosition.opened_at` defaulted to `datetime.now()`, so positions discovered via `sync_from_broker` (with no explicit opened_at) got "now" instead of a prior-session sentinel. D91's overnight detection (`opened_at < today's 04:00 ET`) then misclassified them.

## §0 — TL;DR

Yesterday's Bug AJ shipped a Discord alert that fires when D91 detects overnight positions. Today's Tuesday startup log:

```
04:30:10 | momentum_x | INFO | D86: Broker has 1 positions at startup,
tracker has 1, ghosts=0, overnight=0
```

`overnight=0` despite LIDR being from 2026-04-25 (4 days old). The Bug AJ alert was wired to fire only IF `_overnight_positions_to_close` is non-empty, so it never fired.

**Root cause** (`src/execution/position_manager.py:56`):
```python
opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
```

When `sync_from_broker` constructs a ManagedPosition without explicitly setting `opened_at`, it defaults to `datetime.now()`. D91's logic (`main.py:1273`):
```python
if _d91_is_overnight(opened_at=_opened_at, now_et=_d91_now_et):
    _overnight_positions_to_close.append(sym)
```

`_d91_is_overnight` checks if `opened_at < today's 04:00 ET`. With opened_at = now (post-04:00 ET), the check fails → not overnight.

The defensive `if _opened_at is None: treat as overnight` branch (main.py:1261-1271) never fires because the default-factory always produces a non-None value.

## §1 — Why this only surfaced today (not yesterday)

Yesterday's startup also hit this code path, but Bug AJ shipped that same evening. So today is the first morning the AJ alert was supposed to fire — and it didn't, because of Bug AP.

The compounding chain: Bug AJ wired the alert correctly, but it depends on D91 producing the right detection input. D91 in turn depends on ManagedPosition.opened_at being honest. The default-factory wiring made that input dishonest for any position discovered via D56 sync.

This is a SCAFFOLDING-COMPLETE failure pattern (the same one Tier 3 audit found three times yesterday): the system had every piece in place but the WIRING (in this case, the explicit opened_at on D56-constructed positions) was missing.

## §2 — Fix

`src/execution/position_manager.py:sync_from_broker`:

```python
# Bug AP fix (2026-04-28): compute the sentinel
from zoneinfo import ZoneInfo as _bug_ap_zi
_bug_ap_now_et = datetime.now(timezone.utc).astimezone(_bug_ap_zi("America/New_York"))
_bug_ap_yesterday_close_et = (
    _bug_ap_now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    - timedelta(days=1)
)
_bug_ap_opened_at = _bug_ap_yesterday_close_et.astimezone(timezone.utc)

position = ManagedPosition(
    ...
    opened_at=_bug_ap_opened_at,  # explicit Bug AP sentinel
)
```

The sentinel `yesterday at 16:00 ET` (yesterday's market close) is GUARANTEED before today's 04:00 ET, so D91 always classifies broker-synced positions as overnight.

**Why the sentinel and not Alpaca's actual creation timestamp?**
- Alpaca's `/v2/positions` does NOT return creation/acquisition time directly
- Getting the real entry time requires `/v2/orders` history (additional API call)
- A position discovered via D56 sync (with no session_state) is BY DEFINITION from a prior session
- The sentinel is operationally correct: treat-as-overnight is the safe default for unknown-acquisition positions

A future enhancement could query order history and use the most recent BUY order's filled_at as a richer opened_at. For now, the sentinel solves the immediate problem.

## §3 — Aggressive testing (14 tests, all pass)

`tests/unit/test_bug_ap_overnight_detection.py`:

1. **Direct sentinel test** — pin the exact value (`yesterday 16:00 ET`)
2. **Core invariant test** — opened_at MUST be < today's 04:00 ET regardless of when sync runs
3. **D91 integration test** — end-to-end with a mirror of D91's detection logic; confirms broker-synced positions now trigger overnight classification
4. **False-positive guard** — a position with TODAY's opened_at (e.g., a fast-path entry from this morning) MUST NOT be classified as overnight
5. **D64 path regression** — `sync_from_state_and_orders` (the enhanced-recovery path with session_state) must NOT be affected; verifies via `inspect.getsource` that the session-state opened_at handling is preserved
6. **Property-style parametrized test** — invariant holds across 8 different times-of-day (00:00, 03:00, 04:00, 09:00, 12:00, 16:00, 20:00, 23:00 ET)
7. **Bug AM + AP coexistence** — both fixes apply on the sync_from_broker path without interference (today's auto-attached stop + the new opened_at sentinel both work)

## §4 — Tomorrow's effect (and beyond)

Today's session is unfortunately too late to benefit from this fix (it's already in flight with the broken behavior). But:

- **Tomorrow morning's startup**: if any positions are carried, D91 correctly classifies them as overnight → Bug AJ Discord alert fires → operator gets the morning-resolution ping with per-position P&L summary + the `MOMENTUM_HOLD_OVERNIGHT` env override hint.
- **Every future session-state-deletion event**: positions discovered via D56 sync now correctly trigger D91 instead of being silently treated as today-opened.
- **Bug Z prevention**: the morning-resolution alert is the operator's first signal that something needs investigation — Bug AP restores it.

## §5 — Discovery rate impact

| Pre-Bug AP | After Bug AP |
|---|---|
| 27 production / oracle / harness / architectural bugs surfaced + fixed | **28** |
| 0 patches introduced regressions | **0** |
| Discovery rate ratio | **28/0 = ∞** |

Bug AP is a **next-day cascade** discovery — the kind of bug only surfaceable by ACTUALLY RUNNING the previous day's fix in production. Yesterday's Bug AJ shipped tested + working in isolation; today's run revealed that an upstream input (D91 detection) had its own latent bug that prevented Bug AJ from firing.

This is the operator-vigilance discipline working as designed: ship the fix, run the next session, observe what happens, surface the cascade.

## §6 — Discipline this preserves

The `_overnight_positions_to_close` list is the SINGLE source of truth for D91 → Bug AJ → MOMENTUM_HOLD_OVERNIGHT chain. Every component downstream depends on D91 producing the right list. Bug AP closes the gap where D91 was producing the WRONG list (always empty for D56-sync positions).

The discipline: **when an upstream signal feeds multiple downstream consumers, the upstream signal MUST be tested for correctness BEFORE the consumers are wired.** Bug AJ assumed D91 was correct. Bug AP shows D91 was correct only on the D64 (session-state) path, not the D56 (fresh-start) path. Future calibration should test D91 in BOTH startup modes.

The 28/0 ratio holds. Cost of bug AP in production: 1 missed Discord alert this morning + the operational risk of LIDR sitting unnoticed at -$1,210 (caught by operator manual check). Fix cost: ~10 LOC + 14 tests + 1 finding doc.
