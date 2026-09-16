# 189 — The FILL link: marketable, momentum-adaptive entry limits

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "full-send the marketable/momentum-adaptive limit fix next, so
the bot actually captures the continuers it now identifies."

---

## 0. Why this is the highest-ROI next build

doc 188 gave us the PICK (spot the pump that continues). But a perfect pick we
**can't fill earns exactly $0** — and Friday 5/29 proved the fill is broken:

> `D310.T2 WIDE_STOP CMND: submitting plain BUY limit (no OTO) qty=3167 entry=$3.5700`
> → **CMND filled 0/2.**

The active entry path (WIDE_STOP arm, `MOMENTUM_T2_ENABLED=1`) submitted a **passive
limit at the STALE eval price**. Two ways that fails to fill on a fast, thin mover:
1. **Staleness** — the eval price is ~30-50s old; if the name ticked up, a limit at
   the old price sits *below* the market and never fills.
2. **Passivity** — even at the right price, a limit resting *at the bid* needs a
   seller to cross it. On a momentum name with buyers lifting the offer, nobody does.

So we were vetoing our own continuers at the very last step. **FILL is the chain link
that silently zeroes everything upstream.**

## 1. The fix (shipped, flag-gated, default ON)

`src/execution/alpaca_executor.py` — the WIDE_STOP BUY submission now places a
**MARKETABLE, momentum-adaptive, capped** limit instead of a passive one:

1. **Anchor to the freshest price.** Fetch a live `get_snapshots([ticker])` and use
   `max(entry_price, ask)` — never anchor *below* a price that already moved up.
   (Fallback = entry_price if the snapshot fails; guarded, never raises.)
2. **Cross the spread by a conviction-scaled offset.** `compute_marketable_offset(mfcs)`
   — a pure, tested helper — scales linearly from `base_offset` (0.4%, low/None MFCS)
   to `max_offset` (1.5%) at `mfcs_full_at` (0.55) and **clamps there.** A strong
   continuer chases a bit more to *guarantee* the fill; a marginal name stays passive.
3. **CAP it.** The offset never exceeds 1.5% above the anchor — we ensure fills, we do
   **not** chase a runner arbitrarily far.
4. **Keep it a LIMIT, not a market order.** A market order on a thin small-cap can
   fill catastrophically far up the book. A marketable *limit* bounds the worst fill
   to anchor × (1 + cap).

```
limit = round( max(entry, ask) * (1 + offset), 4 )
offset = base + (max - base) * clamp(mfcs / mfcs_full_at, 0, 1)
```

The standalone protective STOP is **unchanged** (ATR-from-entry); the ≤1.5% buffer only
modestly widens effective risk on the fill — a deliberate, bounded trade for the fill.

## 2. Why momentum-adaptive (not a flat marketable offset)

Conviction *is* the fill-urgency signal. The names we most want — high-MFCS confirmed
continuers (doc 188) — are exactly the ones moving fastest, where a passive limit is
most likely to miss. Scaling the offset with MFCS means **we chase hardest precisely
where the edge is strongest and missing the fill is most expensive**, and we stay
cheap/passive on marginal names where a missed fill costs us little. It's the FILL-side
mirror of the doc-178 ELITE sizing press: lean in on conviction.

## 3. Safety / reversibility

- **Flag**: `EXEC_MARKETABLE_LIMIT_ENABLED=false` reverts to the old passive limit.
- **Tunable**: `EXEC_MARKETABLE_BASE_OFFSET_PCT`, `EXEC_MARKETABLE_MAX_OFFSET_PCT`,
  `EXEC_MARKETABLE_MFCS_FULL_AT`. New fields ⇒ no stale `.env` override (D93 lesson).
- **Bounded**: capped offset + LIMIT (not market) ⇒ worst fill is known a priori.
- **Tests**: 9 new (`test_marketable_limit.py`, pure offset math: base/cap/clamp/
  monotonic/midpoint/div-by-zero/negative-MFCS/anchored-limit) + 50 existing WIDE_STOP
  path tests still green (no regression).

## 4. Scope + follow-ons

- **NOW**: the WIDE_STOP arm — the **active** path (`MOMENTUM_T2_ENABLED=1`) and the
  one that failed Friday. This is the fill fix that matters today.
- **Follow-on (noted, not built)**: mirror the marketable logic on the OTO/tight-stop
  arm (inactive today) and a bid-anchored marketable SELL on the short/fader path.
- **Future**: anchor to the real-time WebSocket price (M2) instead of a REST snapshot
  to drop the per-entry round-trip once the post-open subscription is fixed (doc 178 §3).

## 5. The profit chain (this is the FILL link)

**PICK** (doc 188 + continuer 184) → **FILL** ✅ *(this doc)* → **HOLD** the winner
(exits, doc 176/178) → **SIZE** it (ELITE press 178 + continuer Kelly). With PICK and
FILL both shipped, the front half of the chain is complete: the bot can now both
*identify* the continuers and *capture* them. Still gated on the elevated restart to go
live (177-189 all on disk, none deployed).

## Appendix — files
- `src/execution/alpaca_executor.py` — `compute_marketable_offset()` helper + WIDE_STOP
  marketable submission.
- `config/settings.py` — ExecutionConfig `marketable_*` fields (EXEC_ prefix).
- `tests/unit/test_marketable_limit.py` (new, 9 tests).
- This doc + `docs/SYSTEM_MAP/changelog.md`.
