# 186 — The Tranche 403-War + Ghost Fix (the comprehensive close-discipline, tactical)

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "make the comprehensive fixes." This fixes the ROOT of
Friday's recurring phantom/ghost (the partial-sell path doc 177 never hardened),
and frames the full structural fix (doc 185 B1) as the strategic follow-on.

---

## 0. The Friday root cause (traced line-by-line)

doc 177 hardened the FULL-close paths (D76/D91/D242) to book-only-on-confirmed-close
+ cancel-blocking-stops. But Friday's CMND/STG ghosted through the **partial-sell
(D165 tranche) path, which was never hardened**:

```
11:06  CMND filled 3282 @ $3.46; D310.T2 submits a STANDALONE protective stop
       (reserves ALL 3282 shares via held_for_orders)
11:09  D165 TRANCHE T1 HIT (+7.9%) → plain submit_order sell 1094
       → 403 "insufficient qty available" (the stop holds the shares)
11:10  TRANCHE T2 HIT → 403 again
...    tranches never execute → position stays full → ghosts → EOD failsafe cleans it
```

**The bot fought its own protective stop** (B2, the 403 war) — and a sell that 403s
contributes to the ghost/phantom (B1). 66× 403s on Friday, almost all this pattern.

`main.py:5540` sold via **raw `client.submit_order`** with no stop-cancel — unlike
the Phase-A wrapper used by full closes. The partial-sell path was the gap.

## 1. The fix (surgical, contained to the D165 tranche block)

Applied the cancel-blocking-stops discipline to partial sells:
1. **Before the tranche sell**: cancel the position's standalone protective stop
   (`stop_order_id`) + 0.4s for the broker to release `held_for_orders`, so the
   sell has shares available — no 403.
2. **On sell success**: re-arm the stop for the **remaining** qty — now MANDATORY
   (no longer gated on `adjust_stop_after_partial`), because we cancelled the
   protective stop and must restore it or the remaining position is naked.
3. **On sell failure**: re-arm the stop for the **full** remaining qty (nothing
   sold) — the position is never left naked (D313 hedge-watcher is the 60s backstop).

Net: tranches actually execute (capturing the +7.9%/+6.1% they were hitting), the
position closes properly instead of ghosting, and protection is continuous.

## 2. Why this is the "comprehensive" tactical fix (and what's still strategic)

The phantom/ghost bug class has ONE root — **booking/closing decoupled from broker
confirmation, while a standalone stop reserves the qty.** It manifests per-path:
- D76 EOD close — fixed doc 177.
- D91 overnight / D242 EOD failsafe — fixed Phase A (doc 175).
- **D165 tranche partial-sell — fixed HERE (doc 186).**

That closes the *observed* paths. The STRATEGIC fix (doc 185 §B1) remains: an
**event-sourced position ledger where P&L is a projection of confirmed broker fill
events** (from the trade-updates stream), so NO path can ever book a sale that
didn't happen — the bug class dies structurally rather than per-path. That's a
larger re-architecture; this tactical fix stops the bleeding now.

A related structural option (doc 185 §B2): stop submitting standalone stops that
reserve the full qty at all — use a software trail intraday + a single un-reserved
close path — so there's no stop to fight. Filed.

## 3. Verification
- `main.py` compiles. The D165 tranche-logic tests (`test_d165_tranche_system.py`)
  pass. 5 `test_s022/s023` failures are PRE-EXISTING source-introspection/wiring
  tests (grep main.py for log strings my edit doesn't touch) — confirmed identical
  on a clean tree via `git stash`. **Zero new failures.**
- Not yet live: like 177-185, this deploys on the next (elevated) restart.

## 4. Note: the deep-research run
In parallel, kicked off the deep-research workflow on the intraday continuation edge
(squeeze vs pump microstructure) — doc 185 §B3, the #1 alpha bet. Its cited report
will shape the feature set the doc-182/184 pipeline collects. (Separate doc when it
lands.)

## Appendix — files
- `main.py` (D165 tranche block: pre-sell stop-cancel + mandatory re-arm on success/failure)
- This doc + `docs/SYSTEM_MAP/changelog.md`.
