# 29 — EOD Bug Findings (Fri 2026-04-24 evening)

**Diagnostic-first writeup before any patch.** Same discipline as
D22/D23: each bug gets `what I found → root cause → fix → regression
test → rollout check`.

**Source:** today's `logs/paper_2026-04-24.log` (52K lines, decoded
mixed-mode UTF-8 + UTF-16-LE) + live broker state at 10:18 ET +
yesterday's findings (`24_eod_bug_findings_d23.md`).

**Session summary (ongoing — assessment captured during session per
"today is a wash" decision):**
- 3 positions opened: TRT (10:01), SCNI (10:16), LIDR (10:16)
- TRT BAR-1 EXIT fired at 10:02:29; D228 BAR1_EXIT_PX_DEGRADED
  warning fired exactly as Bug Q fix designed (snapshot empty + NBBO
  failed → entry-price fallback + explicit warning). **First
  production fire of D228; behaved correctly.**
- SCNI: status TBD (closed at broker by 10:18 — likely BAR-1)
- LIDR: open at 10:18 (qty 5264 @ $2.42, broker stop $1.90,
  tracker stop $2.40)
- Equity drift since open: $141,486.75 → $141,092.82 = **−$393.93
  in 48 minutes**

---

## Patches that HELD in production today ✅

| Patch | Evidence in today's log |
|-------|------------------------|
| **Bug D `_poll_for_terminal_fill`** | TRT, LIDR both reached terminal at poll 2/6 with correct `filled_qty` and `filled_avg_price`. Same shape as XNDU yesterday. |
| **Bug R `actual_stop_price` → ManagedPosition.stop_loss** | LIDR: OTO submit log `stop=2.40`, OTO stop leg log `@ $2.40`, position opened `stop=$2.40`. All three lines agree on the Phase-0 tightened value. **Bug R fix worked at the OTO submission boundary.** |
| **Bug Q `resolve_bar1_exit_price`** | TRT BAR-1 exit at 10:02:31: `D228 BAR1_EXIT_PX_DEGRADED TRT: snapshot empty AND broker NBBO lookup failed — falling back to entry_price ($11.5500)`. The previously-silent fallback now emits an explicit warning + tags the trade as degraded for future calibration exclusion. |
| **Bug N D236 marker** | Will fire at EOD (16:00). Pre-EOD verification not possible. |
| **Track A item 3 EOD recon** | Will fire at EOD. Expected to surface Bug W on LIDR. |

---

## Bug V — Bridge rejects non-terminal orders without cancelling at broker (HIGH)

### What I found (re-confirmed; same as D23 morning brief)

Yesterday's session: 5 OTO orders submitted (XNDU + AUUD ×2 + TZOO + CPIX). 4 of them did NOT reach terminal in the bridge's 6-poll budget (12s); the bridge correctly REJECTED them per the Bug D fix. But the broker likely accepted the underlying day-only OTOs, filled them later in the session, and the OTO stop legs absorbed losses we never tracked. Net equity drop yesterday: **−$978.50**.

### Root cause

`src/execution/bridge.py` `_poll_for_terminal_fill` rejection branch (at the call site that drives `_poll_for_terminal_fill` from `execute_verdict`):

```python
if terminal_filled_qty <= 0 or terminal_fill_price <= 0:
    logger.warning("D217: ... rejecting to prevent ghost position")
    self._pm._scored_cache.pop(ticker, None)
    return None
```

Bridge gives up tracking the order, but **does not cancel it at the broker**. The OTO with `time_in_force="day"` continues to live at the broker; if it fills before 16:00 ET, the position becomes a ghost (broker has it, internal tracker doesn't) and the OTO stop leg arms unwatched.

### Fix

`bridge.py` rejection branch: call `await self._client.cancel_order(order_result.order_id)` BEFORE the `_scored_cache.pop` + `return None`. Wrap in `try/except` so cancel failures don't block the bridge return path; emit `D237 BRIDGE_CANCEL` INFO on success or WARN on failure.

### Regression test

`tests/unit/test_d24_bug_v_bridge_cancel.py` (3 cases):
- T1: `status=new, filled_qty=0` after 6 polls → cancel awaited with the order_id → cancel awaited BEFORE return None
- T2: successful fill → cancel NOT called
- T3: cancel raises → D237 WARN fires + bridge still returns None gracefully

### Rollout check

Tomorrow's session: any D217 "rejecting to prevent ghost position" warning MUST be followed within 1 second by a `D237 BRIDGE_CANCEL` INFO line. If D237 is missing, Bug V has regressed. If broker_state has any positions/orders at startup that internal tracker doesn't know about, D86 cleanup catches it.

---

## Bug W — Exit-ladder restructure discards Phase-0 tightened stop (HIGH)

### What I found

LIDR live state at 10:18 ET:
- **Internal tracker:** `pos.stop_loss = $2.40` (Phase-0 tightened, ~0.8% below entry)
- **Broker live order:** `stop_price = $1.90` (verdict.stop_loss un-tightened, ~22% below entry)
- 3 broker orders for the position: 1× stop @ $1.90 (qty 1756) + 2× limit sells (qty 1754 each, T1/T2 targets)

The tracker thinks the position is protected at $2.40. The broker has it protected at $1.90. **A move to $2.30 would be a "still safe" event in the tracker but would NOT trigger the broker stop.** Risk math is wrong by ~14× (0.8% vs 11% drawdown to stop trigger).

### Root cause

`main.py:3488` Phase-2 exit-ladder call:
```python
_p2_result = await cancel_stop_and_submit_exit_ladder(
    ...
    stop_price=verdict.stop_loss,      # ← un-tightened original
    ...
)
```

And `main.py:3528` stop-resubmitter registration:
```python
stop_resubmitter.register_stop(
    ticker=order.ticker,
    order_id=_effective_stop_oid,
    stop_price=verdict.stop_loss,       # ← same bug
    qty=order.qty,
)
```

Both call sites read `verdict.stop_loss` (the un-tightened verdict-time stop) instead of `pos.stop_loss` (which yesterday's Bug R fix correctly populates with the Phase-0 tightened actual_stop_price). The exit ladder cancels the OTO stop at $2.40 and submits a NEW stop at $1.90 — recreating the Bug R class divergence at a different layer.

**This is a Bug R regression in a different code path.** Bug R fixed the bridge ManagedPosition construction; Bug W shows the same bug pattern surviving in the exit-ladder restructure.

Two related sites in FastPath (line 1997, 2040) and one in Short OTO (line 3215). All 5 sites read the un-tightened verdict / fast-path-entry stop, but only Phase-2 (3488/3528) and FastPath (1997/2040) trigger restructure. Need to verify FastPath path's `fpe.stop_loss` carries the tightened or un-tightened value.

### Fix

All 5 sites: pass `pos.stop_loss` (or equivalent ManagedPosition-tracked field) instead of `verdict.stop_loss` / `fpe.stop_loss`. Source of truth: the position object that `bridge.execute_verdict` already populated correctly per Bug R.

Concretely for `main.py:3488` and `:3528`:
```python
# Use the broker-truth stop the bridge already stored (Bug R fix),
# not verdict.stop_loss which is the un-tightened original.
stop_price=_p2_pos.stop_loss,
```

For FastPath sites: investigate whether `fpe.stop_loss` is set from the OTO submission's actual_stop_price or from the verdict. If from verdict, propagate the fix.

### Regression test

`tests/unit/test_d24_bug_w_exit_ladder_stop.py` (3 cases):
- T1: build a `ManagedPosition` with `stop_loss=$2.40` (Phase-0 tightened) and a verdict with `stop_loss=$1.90` (un-tightened); exit-ladder call site must use `$2.40`
- T2: source-grep guard — every `cancel_stop_and_submit_exit_ladder` call site in main.py must use `pos.stop_loss` not `verdict.stop_loss`
- T3: source-grep guard — every `stop_resubmitter.register_stop` call site must use `pos.stop_loss` not `verdict.stop_loss`

### Rollout check

Tonight's EOD recon will fire `D231 RECON_HARD_BLOCK STOP LIDR: internal=$2.40 broker=$1.90 delta=-$0.50` — confirming the bug at session close. After the patch ships, tomorrow's first BAR-1 entry should show Phase-2 restructure log lines using the tightened stop and broker-side state should match.

---

## Track B recon daemon — separate work item (this evening)

Per items 14-20 of the 2026-04-24 next-actions list. Not a bug — the discovery-infrastructure component that catches Bug-V/Bug-W class within 30 seconds in real time, instead of waiting for EOD. Daemon spec:

- `src/monitoring/recon_daemon.py` — long-running asyncio task
- 30-second polling cadence
- Three escalation tiers (D230 warn / D231 hard-block / D232 lethal)
- Shadow mode for first week (logs what it WOULD have done)
- D232 lethal default threshold: 5% equity divergence OR qty mismatch >60s
- Integration tests against synthetic event streams including the
  benign partial-fill window from Bug D (must not false-positive)
- Run as separate process per playbook §3.5 (independence from
  trading loop)

Wires the existing `eod_recon.run_eod_invariants()` (which is one-shot,
ships in main.py at session close) into a continuous loop. The
invariants are already implemented and tested (Track A item 3).

---

## Cross-cutting observation

Bug V (yesterday-discovered, today-confirming) and Bug W (today-discovered) **share the same shape**: the bridge correctly tracks the patched-in correct value, but a downstream consumer reads from the original (verdict / pre-patch) source instead. Bug V loses a broker-cancel; Bug W loses a stop-tightening.

**This is the canonical "fix surfaces in one layer, second layer hasn't caught up" pattern.** Static analysis won't catch it (the verdict.stop_loss reference is type-correct). Property-based testing WOULD catch it: the invariant `for any position lifecycle, broker_stop == tracker_stop after every operation` would shrink to "after exit_ladder restructure" as the failing case.

This is the strongest argument yet for the v2.2 §0.2 PBT state-machine sprint. Recording in tonight's commit message + tomorrow's findings dossier.

---

## D24 EOD addendum — three more bugs surfaced via Item 34 EOD recon

The Track A EOD recon fired at 16:00:25 and **caught the catastrophe of the
day** — LIDR is open at the broker (5264 sh, unrealized -$789→-$947 and
worsening) but the internal tracker thinks it's closed at +$421.12. Net
divergence: **$1,210.72**. Without the EOD recon shipped yesterday in
Track A, this would have been carried overnight invisibly.

### Bug Z — D78 SMART_EXIT proceeds to "closed" state on 403 broker rejection (CRITICAL)

**What I found.** At 10:30:36 the SMART_EXIT path fired on LIDR. The
`close_position` POST returned `403 Forbidden`. Bridge logged the
ERROR but proceeded to:
1. Cancel the protective stop (success at broker)
2. Cancel both tranche limit sells (success at broker)
3. Mark position closed in PositionManager
4. Log fake `Closed with attribution PnL=$+421.12 (5264 shares)`
5. Log fake `D215 PATH P&L: LIDR closed via SMART_EXIT P&L=+$421.12`
6. Log fake `D96 SMART EXIT: LIDR exit=$2.50 pnl=$421.12 (3.3%) reason=Thesis intact`

Result: **LIDR is naked at the broker (no stop, no tranches), no
internal tracking, all subsequent EOD-close logic ran against an
empty tracker**, and the position carries over a 3-day weekend with
unrealized -$947+.

**Root cause.** The `close_with_attribution` /
`SMART_EXIT` cleanup chain at the bridge layer trusts that the broker
close succeeded once it submits the call. There is no response-status
check before the cancel-stop / cancel-tranches / mark-closed sequence.
A 403 (or any error) on the close leaves all the cleanup operations
firing against a position the broker still has.

**Fix (deferred to Monday evening — needs proper test coverage):** wrap the
close-position call in a status check; if the close returns non-2xx OR
raises, the cleanup chain MUST abort and re-raise OR re-queue the
close attempt. Reserve **D243 BRIDGE_CLOSE_FAILED** for the warning
emitted when this branch fires.

**Detection mechanism that DID catch it:** Track A item 3 EOD recon
(`run_eod_invariants`), which fired `D231 RECON_HARD_BLOCK QTY LIDR:
internal_qty=MISSING broker_qty=5264` at 16:00:25 — the exact
divergence type Track A was built for. **The discovery infrastructure
worked. The execution layer failed.**

### Bug X — D146 BAR-1 EXIT fires repeatedly without successful ACTUAL completion

**What I found.** Between 10:22:04 and 10:30:33, `D146 BAR-1 EXIT:
LIDR — selling 5264/5264 shares (100%)` fired **9 times** at T+316s
through T+825s. Zero matching `D146 BAR-1 ACTUAL` lines (compare
ONMD at 11:22:46 which has both EXIT + ACTUAL).

**Root cause hypothesis.** Each BAR-1 fire calls `close_with_attribution`,
which today returned without raising but ALSO without succeeding (likely
the same 403 path as Bug Z — close fails, cleanup proceeds against a
phantom-closed tracker, position remains open at broker). Reserve
**D244 BAR1_EXIT_REPEATEDLY_FAILED** for the warning when BAR-1 fires
>1 time on the same position.

### Bug Y — D76 EOD-close says "no positions" with broker still holding 5264 shares

**What I found.** `15:55:24 | D76 EOD CLOSE: No positions at 15:55 ET
— marking EOD`. But broker has LIDR with qty=5264. Tracker desync.

**Root cause.** Bug Z mutated the tracker to mark LIDR closed at
10:30:36 (the fake close) — so by 15:55, `position_manager.open_positions`
returns `[]`. D76 EOD-close iterates empty list → "nothing to close."

**Fix:** Bug Z's fix.

### Cumulative D24 day-of-trading economics

Realized P&L from 7 filled orders:
- TRT:    -$55.75 (slippage on close)
- SCNI:   -$289.10 (sub-dollar stop-out)
- ONMD:   +$171.73 (clean BAR-1 win)
- LIDR:   -$947.52 unrealized (Bug Z naked overnight)
- **Total:** -$173 realized, -$948 unrealized = **-$1,121 today**

**Yesterday's $978 silent loss + today's $1,121 = $2,099 infrastructure-
attributable losses across 2 days**, vs ~$0 strategy-attributable P&L.
Until Bug Z is patched, every position is one 403 away from going naked.

### Operational decision required tonight (LIDR overnight)

Per item 32 of the next-actions list: "Intervention only if equity drift
exceeds another -$300." Drift since morning baseline is now -$553+
(exceeds threshold). **Per the pre-committed criterion, manual
intervention is authorized.**

Options:
1. Submit a Market-on-Open (`tif=opg`) sell for 5264 shares — closes at
   Monday's opening auction. Locks in current loss, eliminates 3-day
   weekend drift risk.
2. Submit a GTC stop at $2.10 — partial protection if Monday opens above.
   Risk: gap-down through stop.
3. Let it ride — D91 (Bug E patch) will detect overnight position at
   tomorrow morning's startup and route through corrected close logic.
   Risk: Bug Z + Bug Y still active in tomorrow's bytecode (until restart);
   D91 might fire the same broken path.

**Recommendation: Option 1 (MOO sell).** The infrastructure that should
manage LIDR has already failed today (Bug Z). Trusting it again over a
3-day weekend with -$947 already outstanding is not defensible.
Surfacing for the user's decision; will not auto-submit.
