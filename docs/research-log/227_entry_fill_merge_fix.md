# 227 — The entry-fill qty-drift root fix: `add_position` MERGES, not replaces (+ doc 228 ripples)

**Author**: Claude Opus 4.8
**Date**: 2026-06-02 (Tuesday, post-close)
**Mandate**: Pierce — "Fix the fill D218 entry-fill-tracking fix, then lets do a comprehensive
sweep for bugs."
**Commits**: `82cc24f` (doc 227 merge), `72a1e95` (doc 228 ripples)

---

## 0. TL;DR

The 6/2 LASE **+143% / +$50,921 ghost** (doc 226) was not a close-path bug — the whole 216→220
hardening arc fixed the close path. It was an **entry/fill-tracking** bug:

> `PositionManager.add_position` did `self._positions[ticker] = position` — a **REPLACE**.
> LASE was bought **3×** (a runner we kept adding to). Each re-buy **overwrote** the tracker
> with only the latest fill's share count, **silently discarding** the shares the broker still
> held. The tracker ended **10,680 shares short of broker truth** → D218 qty-drift fired all
> day (D231×1985, D313×839 alerts) → the EOD close saw the wrong qty → 403 → accidental
> overnight carry. The +143% was **luck on a mis-tracked ghost**; realized P&L was **−$3,950**.

**Fix (D227):** `add_position` now **MERGES** a same-ticker, same-direction re-buy:
share-weighted-average entry, summed quantity, earliest `opened_at` preserved. **Fix (D228):**
the merge returns the **canonical tracked object**, and the three consumers that previously read
the discarded single-fill object (qty-drift reconcile, state persistence, exit-ladder sizing)
now operate on the merged object.

---

## 1. Root cause (the REPLACE)

`src/execution/position_manager.py` — old:

```python
def add_position(self, position: ManagedPosition) -> None:
    self._positions[position.ticker] = position   # REPLACE — drops prior shares on a re-buy
```

A momentum runner like LASE is bought in tranches (fast-path dip + Phase-2 confirm + Phase-3
rescan add). Three `add_position` calls → the dict holds only the **last** fill's `qty`. The
broker, however, holds the **sum**. Every reconcile after that saw tracker≠broker → qty-drift.

This is a **distinct bug class** from the close-path arc (216 cancel-settle race, 218 naked
re-arm, 219 partial-fill strand, 220 EOD retry-until-bell). Those all assumed the tracked qty
was *correct* and fought to close it. D227 is why the tracked qty was *wrong in the first place*.

## 2. The fix (D227 — merge)

```python
def add_position(self, position: ManagedPosition) -> ManagedPosition:
    existing = self._positions.get(position.ticker)
    held = int(getattr(existing, "remaining_qty", 0) or 0) if existing is not None else 0
    if (existing is not None
            and getattr(existing, "direction", "long") == getattr(position, "direction", "long")
            and held > 0 and position.qty > 0):
        add_qty = position.qty
        new_held = held + add_qty
        # share-weighted-average entry, weighted by HELD (post-tranche) shares
        existing.entry_price = round(
            (existing.entry_price * held + position.entry_price * add_qty) / new_held, 6)
        existing.qty = new_held
        existing.remaining_qty = new_held
        existing.opened_at = min(existing.opened_at, position.opened_at)  # earliest age (D91)
        existing.peak_price = max(existing.peak_price, position.peak_price)
        if not existing.stop_loss and position.stop_loss:
            existing.stop_loss = position.stop_loss
        tracked = existing
    else:
        self._positions[position.ticker] = position   # new ticker OR fully-exited -> REPLACE
        tracked = position
    # ... metrics ...
    return tracked
```

Key decisions, each pinned by a test:
- **Weight by `remaining_qty` (held), not original `qty`** — if a tranche already sold, the
  weighted-average entry must reflect what's still held, and the new `qty` is the held total.
  (`test_merge_weights_by_held_not_original_after_tranche`)
- **`remaining_qty == 0` → REPLACE, not resurrect** — a fully-exited-but-not-yet-removed
  position must not be merged into phantom shares.
  (`test_fully_exited_not_resurrected`)
- **Earliest `opened_at`** — the position's true age drives D91 overnight logic.
- **Distinct tickers never merge** (`test_distinct_tickers_do_not_merge`).

## 3. The ripples (D228 — the merge wasn't enough on its own)

A merge that mutates `existing` in place but returns `None` is a trap: every caller that held a
reference to the **single-fill object it passed in** would then reconcile / persist / size off the
**discarded** object. An adversarial bug-sweep agent found three such consumers; each was verified
against the real code before fixing:

| # | Sev | Consumer | Bug | Fix |
|---|-----|----------|-----|-----|
| 1 | **CRITICAL** | `bridge.py` qty-drift reconcile | `_schedule_qty_drift_checks(position)` ran against the orphaned single-fill object → would "detect drift" against a stale 6,092-share view and mis-correct | `tracked_position = self._pm.add_position(position) or position`; reconcile + schedule against `tracked_position` |
| 2 | **CRITICAL** | `post_fill_handler.py` state persistence | wrote the **single-fill** `order.qty`/`entry_price`/`opened_at` to `session_state.json` → a crash-restart would reload the WRONG (last-fill-only) qty, re-introducing the drift across the D95 recovery boundary | persist the merged `pos_obj` values (qty, remaining_qty, entry_price, opened_at, tranches_filled) |
| 3 | **HIGH** | `post_fill_handler.py` exit ladder | exit tranches sized off `order.qty` (single fill) → laddered out only the last tranche's shares, stranding the rest naked | `position_qty=int(getattr(pos_obj, "remaining_qty", 0) or order.qty)` |

`add_position` now **returns the canonical object** (`-> ManagedPosition`), and the contract is
pinned by `test_add_position_returns_canonical_object_doc228`: a new ticker returns *itself*; a
re-buy returns the *merged existing* object (`is not` the discarded single-fill object) carrying
the summed qty.

## 4. Tests

`tests/unit/test_position_merge.py` — **8 tests**, every one a regression of the LASE failure:
sum-qty + weighted-avg, broker-aggregate match (no drift), single tracker entry, earliest
`opened_at`, distinct-tickers-don't-merge, canonical-return contract (D228), held-weighted avg
after a tranche, fully-exited-not-resurrected.

**Verification:** 40 targeted tests pass (merge + crash/short recovery + close-cancel-settle);
`python main.py` boots clean; no secrets/.env staged.

## 5. What this does — and does NOT — fix

- ✅ Eliminates the **multi-buy REPLACE** drift (the LASE root) at the source.
- ✅ Keeps the tracker == broker aggregate across re-buys, crash-restart, and partial tranches.
- ❌ Does **not** retro-protect the LASE position already open overnight from 6/2 — D91 retries
  the close at 04:00; the merge fix makes that retry see the *correct* 26,772-share qty so it can
  actually flatten (the same qty-drift that 403'd it Tuesday is now gone).
- ❌ Does **not** replace the **B1 event-sourced ledger** (doc 222) — that remains the structural
  end-state where qty *cannot* drift because position is a pure fold over confirmed FILL events.
  D227/228 is the tracker-level fix that holds the line until B1 cuts over.

---

**Initiator**: Claude Opus 4.8 (1M ctx). **Found-by**: doc 226 post-mortem (root) + adversarial
bug-sweep agent (the 3 ripples). **Predecessor**: 226 (post-mortem that ID'd the bug class),
216/218/219/220 (the close-path arc this completes), 222 (the B1 end-state).
