# 57 — Bug AS: RESCAN/Phase2/VWAP exit-ladder corrupts ManagedPosition.stop_order_id with stale OTO leg or entry-order ID

**Status:** patched 2026-04-28 afternoon (Phase 3.2 of the Discipline Framework).
**Severity:** **P0** — caused D231 RECON_HARD_BLOCK to fire for every RESCAN entry today (SBLX, ATER, SEGG). The system claimed broker-confirmed protective stops it did not have. Operationally equivalent to running unprotected positions.
**Surface:** Phase 0 infrastructure audit §0.4 — RESCAN tracker `stop_order_id` stored values like `b9703115` that were neither the OTO stop leg ID nor any active broker stop ID.
**Bug class:** silent-corruption-by-fallback + asymmetric in-memory write between FAST_PATH (correct) and Phase2/VWAP/RESCAN (broken).

## §0 — TL;DR

Two compounding defects in main.py at three call sites
(Phase2 ~3805, VWAP ~6291, RESCAN ~6710):

**Root cause #1 — missing in-memory mirror.** FAST_PATH at line 2315 has:
```python
fp_pos.stop_order_id = _fp_new_stop_oid
```
Phase2/VWAP/RESCAN never mirrored. So the in-memory ManagedPosition kept the **stale OTO stop leg ID** that `cancel_stop_and_submit_exit_ladder` had ALREADY CANCELLED. D231 RECON_HARD_BLOCK fired on every reconciliation because the broker no longer had any order with that ID.

**Root cause #2 — entry-order-ID fallback corruption.** All three call sites used:
```python
_eff_stop = _stop_oid or order.stop_order_id or order.order_id
```
When `_stop_oid` was empty (D147 short-circuit, residual_qty=0, or stop submit failure):
- `order.stop_order_id` → stale OTO leg ID (already cancelled by exit_ladder)
- `order.order_id` → **the ENTRY ORDER ID** — not a stop at all

This corrupted ID was then handed to `stop_resubmitter.register_stop`. Today's SBLX/ATER/SEGG RESCAN entries each registered with their entry order ID (e.g. `b9703115...`) as their "stop." Any later ratchet attempt would have tried to cancel an entry order to "move the stop up."

## §1 — How Phase 0 caught this

The Tue 2026-04-28 transcript showed D231 RECON_HARD_BLOCK firing for every RESCAN position post-fill. The audit followed the chain:

1. ManagedPosition.stop_order_id at the moment of D231 reconciliation: `b9703115...`
2. Broker `/v2/orders` query: no order with that ID exists.
3. trade_results.jsonl: that ID matches `entry_order_id` for SBLX RESCAN entry.
4. main.py:6738 — `_rs_eff_stop = _rs_stop_oid or order.stop_order_id or order.order_id`. If both first two are empty, third is the entry order ID.
5. main.py:6700 — `_rs_result.new_stop_order_id` — confirmed empty for RESCAN paths because the residual stop submission was reaching `is_position_unprotected=True` (SBLX qty was small enough that limit tranches consumed everything; residual_qty=0; no stop was needed BUT the registration code still wrote SOMETHING and that something was the entry order ID).
6. Compared to FAST_PATH (line 2315): the in-memory write is present there. Asymmetry.

This is the same **SCAFFOLDING-COMPLETE** pattern the Tier 3 audit flagged repeatedly: every piece in place, the WIRING was wrong on three of four paths.

## §2 — Fix

Three call sites in main.py, two changes each:

```python
# (1) After exit_ladder returns: mirror the new OID into the in-memory
#     ManagedPosition. Mirrors FAST_PATH's existing line at 2315.
_p2_pos.stop_order_id = _new_stop_oid       # Phase2  ~3805
_vwap_pos.stop_order_id = _vwap_stop_oid    # VWAP    ~6291
_rs_pos.stop_order_id = _rs_stop_oid        # RESCAN  ~6710

# (2) Stop registration: NO entry-order-ID fallback. When empty,
#     register an empty OID (StopResubmitter then knows there is no
#     ratchetable broker stop) and log a warning.
if not _new_stop_oid:
    logger.warning(
        "Phase2 stop register %s: exit_ladder yielded no new stop OID "
        "(residual_qty=%d skip=%s unprotected=%s) — registering empty OID; "
        "position has NO ratchetable broker stop",
        order.ticker, ...,
    )
_effective_stop_oid = _new_stop_oid    # was: ... or order.stop_order_id or order.order_id
```

The empty-OID path is the OPERATIONALLY-CORRECT signal: D230 RECON_WARN logs "no broker-confirmed stop" cleanly; D231 RECON_HARD_BLOCK no longer fires on a phantom OID; StopResubmitter doesn't try to cancel an entry order on its next ratchet.

FAST_PATH's existing line is unchanged — it was always correct and now serves as the parity baseline for the source-grep guard.

## §3 — Aggressive testing (7 tests, all pass)

`tests/unit/test_bug_as_stop_oid_corruption.py`:

1. **In-memory mirror behavior** — after exit_ladder, `_pos.stop_order_id` MUST equal `_result.new_stop_order_id`. Pin the assignment.
2. **Empty-OID is empty (not entry-OID)** — when exit_ladder yields no new stop, the in-memory field MUST be empty. Critically NOT the entry order ID.
3. **D231 PASSES when OID matches broker** — end-to-end with `run_eod_invariants`: a position whose internal stop_order_id matches a real active broker stop survives reconciliation cleanly. This is the test today's RESCAN entries would have failed.
4. **D231 FIRES when OID is stale OTO leg** — negative case: pins the exact failure mode the fix eliminates. Stale OID → no broker order matches → `stop_oid_missing` detail in result.
5. **Source-grep: NO entry-order-ID fallback** — scans main.py for `_*_stop_oid or order.stop_order_id or order.order_id` pattern. Skips comment lines so the fix's own documentation comments don't trip the guard. Future regressions surface immediately.
6. **Source-grep: every exit-ladder call site mirrors the new OID** — every `await cancel_stop_and_submit_exit_ladder` MUST be followed within 30 lines by an `_pos.stop_order_id = ...` write. Pins symmetric handling across all four call sites.
7. **FAST_PATH parity baseline preserved** — pins that `fp_pos.stop_order_id = _fp_new_stop_oid` is still present in main.py. If a refactor removes it, ALL paths regress (the working template would be gone).

Plus 36 adjacent tests verified non-regressed: `test_d230_eod_recon_invariants.py` (10), `test_exit_ladder.py` (16), `test_d24_bug_w_exit_ladder_stop.py` (3), `test_session_state_recovery.py` (7).

## §4 — Effect on tomorrow (Wed 2026-04-29) and beyond

For any RESCAN, VWAP, or Phase2 entry tomorrow:
- Post-fill, exit_ladder runs → new residual stop submitted → **in-memory `position.stop_order_id` now matches the broker stop** (previously: stale).
- D231 RECON_HARD_BLOCK does not fire on a phantom OID (previously: fired on every such entry).
- StopResubmitter ratchet-up logic operates on a real broker stop OID (previously: would try to cancel an entry order to "ratchet").
- For positions where exit_ladder yielded no stop (residual_qty=0 case), the empty OID surfaces cleanly in D230 RECON_WARN logs — operator sees "no broker-confirmed stop" instead of a misleading green-status with a phantom ID.

## §5 — Why this is also a TEST DISCIPLINE finding

Three exit-ladder call sites had asymmetric handling for over a month. No test caught the asymmetry. The fix added a **source-grep guard** that scans for `cancel_stop_and_submit_exit_ladder(` and verifies each is followed by a `_pos.stop_order_id = ...` mirror — turning architectural symmetry into a CI-checkable invariant.

**Discipline going forward:**
- **When N call sites do "the same thing," at least one source-grep test must enforce that they actually do.** Manual review of N call sites scales linearly; CI checks scale with the count of regressions caught.
- **A documented "correct" call site (FAST_PATH at 2315) is also a parity baseline.** If a refactor removes it, the test that pins its presence catches the regression — preventing the symmetric removal of the "correct" path.

## §6 — Discovery rate impact

| Pre-Bug AS | After Bug AS |
|---|---|
| 29 production / oracle / harness / architectural bugs | **30** |
| 0 patches introduced regressions | **0** |
| Discovery rate ratio | **30/0 = ∞** |

Bug AS is a **multi-week latent corruption** discovery. The asymmetric in-memory write has been broken since the exit_ladder helper landed (Tue 2026-04-21). Today's RESCAN entries surfaced it because (a) RESCAN positions hit `residual_qty=0` more often than FAST_PATH (smaller sizes; tranches consume the position), and (b) D231 RECON_HARD_BLOCK now exists and screams about the phantom OID where previously it would have stayed silent.

## §7 — Discipline this preserves

`ManagedPosition.stop_order_id` is the SINGLE source of truth for "which broker order is protecting this position." Every downstream consumer (StopResubmitter ratchets, eod_recon invariants, Bug AM auto-attach, Bug AP D91 detection) depends on this field being honest. Bug AS closes the gap where three of four exit-ladder call sites were producing dishonest values — either stale OIDs or, worse, entry-order IDs masquerading as stop IDs.

The 30/0 ratio holds. Cost of Bug AS in production: D231 RECON_HARD_BLOCK fired on every RESCAN entry today (3 positions); StopResubmitter would have ratcheted entry orders if any RESCAN entry had reached its tranche-1 trigger; multi-week latent corruption escaped all prior tests. Fix cost: ~50 LOC across 3 main.py sites + 7 tests + 1 finding doc.
