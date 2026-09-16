# 203 — Short crash-recovery parity (the fade-short pre-live gap)

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "pursue h" — the short-side crash-recovery state parity, the one
real pre-live gap for the doc-202 fade-short.

---

## 0. The gap (worse than expected — a latent live bug too)

doc 202 wired the fade-short but flagged crash-recovery parity as a pre-live TODO. The
investigation found the gap is bigger than "persist some state":

- **Both recovery paths explicitly DROP shorts.** `sync_from_state_and_orders` (D64,
  primary) and `sync_from_broker` (D56, fallback) both had `if side != "long": continue
  # We only trade long`, and `if qty <= 0: continue` (Alpaca reports shorts with negative
  qty). So a short at the broker is skipped on restart → the bot comes up **not tracking
  it** → an unmanaged ghost (broker holds it, the bot never monitors/exits/stops it).
- **`ManagedPosition` defaulted `direction="long"`** and **`PositionState` didn't persist
  `direction` at all** — so even if recovered, a short would come back as a long (stop
  computed *below* entry instead of above → inverted, useless protection).

This affected not just the (flagged-OFF) fade-short but the **live D161 faller-short** too
— a latent bug: any D161 short held across a restart was already being dropped.

## 1. The fix

1. **`PositionState.direction`** (`session_state.py`) — new field (default "long"),
   persisted via `to_dict`, restored in `from_dict` (legacy state → "long"). Round-trip
   tested.
2. **`PositionManager._recovery_stop_targets(entry, is_short)`** — direction-aware
   defaults: long → stop BELOW / targets ABOVE; short → **stop ABOVE** (`entry*(1+stop_pct)`)
   / targets BELOW.
3. **Both recovery paths recover shorts** (D64 + D56): `side=="short"` no longer skipped;
   `qty = abs(...)`; stop/targets via the helper; `direction=("short" if _is_short else
   "long")` on the `ManagedPosition`; a `D202 SHORT RECOVERED` warning. **The LONG branch
   is byte-for-byte unchanged** (helper returns the identical long stop/targets; `abs()` is
   a no-op on positive qty).
4. **D161 + D202 persist `direction="short"`** in their `state_mgr.update_position(...)`
   calls — so there's correct state to recover (D161's call was missing it).

## 2. Verification

- **Long path intact**: 44 crash-recovery + executor tests pass; `PositionState` round-trip
  preserves direction; legacy state (no `direction`) defaults to "long".
- **Short path works**: 4 new tests (`test_short_recovery.py`) — D64 recovers a short
  (direction="short", qty=abs, stop ABOVE), D64 no-state fallback (stop above / targets
  below), D56 fallback recovers a short, and the LONG regression.
- **Zero new failures**: the 10 `test_pipeline_guards` failures are the **pre-existing**
  orchestrator MagicMock rot (`orchestrator.py:312: TypeError '>' MagicMock vs int`) —
  **confirmed by stashing the (h) changes and re-running: the identical 10 failed / 18
  passed.** My changes add nothing.

## 3. Safety + status

- **Dormant until shorts exist.** The short-recovery branch only triggers on a broker short
  position, which only happens once `EXEC_FADE_SHORT_ENABLED` (or a D161 short) fires. So
  this is **zero behavior change to the live long bot today** — it just makes the recovery
  *correct* the day a short exists.
- **Closes the fade-short pre-live gap (doc 202 §3a).** The fade-short pre-live checklist
  is now: (a) crash-recovery parity ✅ *(this doc)*, (b) more data (n=11 → the weekly study
  accrues), (c) Pierce's call to flip `EXEC_FADE_SHORT_ENABLED`. The infra is complete; only
  the data + the go remain.

## Appendix — files
- `src/execution/session_state.py` — `PositionState.direction` (+ `from_dict`).
- `src/execution/position_manager.py` — `_recovery_stop_targets` helper; D64 + D56 recover
  shorts (direction-aware).
- `main.py` — D161 + D202 persist `direction="short"`.
- `tests/integration/test_short_recovery.py` (new, 4 tests).
- This doc + `docs/SYSTEM_MAP/changelog.md`.
