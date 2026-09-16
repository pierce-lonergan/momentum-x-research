# 190 — Fill symmetry: marketable limits on EVERY entry path

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "(a) mirror the marketable fill onto the OTO/tight + a
bid-anchored marketable SELL for the fader/short path."

---

## 0. Why symmetry (doc 189 only fixed the active path)

doc 189 made the **WIDE_STOP** long arm marketable — the path that failed Friday
(CMND 0/2). But the executor has **three** entry submission sites, and a passive limit
on *any* of them is the same $0-fill bug waiting for the day that path goes active:

| Path | When active | Was | Now |
|---|---|---|---|
| WIDE_STOP long (`submit_limit_order`) | `MOMENTUM_T2_ENABLED=1` (today) | marketable (189) | marketable (shared) |
| OTO long (`submit_oto_order`) | T2 off / tight arm | **passive @ entry** | **ask-anchored marketable BUY** |
| Short/fader (`submit_oto_short_order`) | D161 faller→short | **passive @ entry** | **bid-anchored marketable SELL** |

The moment we toggle `MOMENTUM_T2_ENABLED` off, or the faller routes a name to a D161
short, the FILL link breaks again on an un-fixed path. Symmetry closes that.

## 1. The shipped change (one helper, one snapshot, three sites)

**`compute_marketable_limit(side, entry_price, quote_price, mfcs, …)`** — a pure,
two-sided extension of doc-189's `compute_marketable_offset`:
- `side='buy'` (long): anchor = `max(entry, ask)`, cross **UP** → `anchor*(1+offset)`.
- `side='sell'` (short): anchor = `min(entry, bid)`, cross **DOWN** → `anchor*(1-offset)`.
- `quote_price` None/≤0 ⇒ fall back to entry (still marketable by the offset).
- Same conviction-scaled, **capped** offset; result is a **LIMIT** so the worst fill is
  bounded. Pure + deterministic ⇒ train==serve testable.

The executor now fetches **one** `get_snapshots([ticker])` (ask + bid) *before* the
long/short branch and a small `_mk_limit(side, quote)` closure applies the helper at
each site (logs `D190 MARKETABLE {BUY|SELL} LIMIT …`). The doc-189 WIDE_STOP inline was
refactored to use the shared snapshot — net **−1 round-trip** vs. doing it per-site.

## 2. Direction semantics (why a short crosses DOWN)

A short *entry* is a **sell-to-open** — to fill you must meet buyers at/below the bid,
so the marketable short limit anchors to the **bid** and subtracts the offset. Same
trade-off as the long: a fill below the eval price means slightly less room before the
(above-entry) stop, bounded by the ≤1.5% cap. A short that doesn't fill earns $0 too —
this makes the D161 fader-short path actually execute on thin names.

## 3. Safety / reversibility

- Same flag as doc 189: `EXEC_MARKETABLE_LIMIT_ENABLED=false` reverts ALL three paths to
  passive-at-entry. Same caps/tunables (`EXEC_MARKETABLE_*`).
- Bounded: capped offset + LIMIT (not market) on every path.
- **Tests**: 8 new two-sided cases in `test_marketable_limit.py` (buy anchors to ask /
  ignores ask-below-entry / no-quote; sell anchors to bid / ignores bid-above-entry /
  no-quote; buy≥entry & sell≤entry invariant; both-sides cap) + the 50 WIDE_STOP-path
  tests still green. 83 unit tests pass across the marketable + faller + opening-range +
  executor suites.

## 4. The profit chain

PICK (188+184) → **FILL** ✅ *(189 active path + 190 every path)* → HOLD (176/178) →
SIZE (178 + Kelly). The FILL link is now complete across the whole entry surface — long
fast-path, long OTO, and fader-short all cross the spread to actually capture the name.

## Appendix — files
- `src/execution/alpaca_executor.py` — `compute_marketable_limit()` two-sided helper;
  shared snapshot + `_mk_limit` closure; WIDE_STOP / OTO-long / short all use it.
- `tests/unit/test_marketable_limit.py` (+8 tests).
- This doc + `docs/SYSTEM_MAP/changelog.md`.
