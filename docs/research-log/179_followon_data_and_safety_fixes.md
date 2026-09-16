# 179 — Follow-on Fixes: Real-Time Data (M2), Recon Safety (M1), and the D112/VCIG Reconsideration

**Author**: Claude Opus 4.8
**Date**: 2026-05-28 (Thursday, post-close — continuation after docs 177/178)
**Mandate**: Pierce — "keep going." Working the doc-178 §3 deferred list,
highest-leverage first.

---

## 0. Summary

Three things this pass: (1) **M2** — evaluated candidates now subscribe to the
live WebSocket so post-open names stop trading on stale REST data (fixes the 59×
"VWAP unavailable"); (2) **M1** — the recon equity check now marks-to-market and
uses a sane band, killing ~2,600 false WARN/ERROR lines/day AND making the
shadow `recon_daemon` safe to un-shadow; (3) a **forensic correction**: VCIG
(+73%) was *not* a clean missed winner — it ran on ~0.3× RVOL (near-zero
liquidity), so D112's reject was defensible and **needs no change**. The real
tradeable misses (NCPL, AMSS) are already handled by doc 178's catalyst downgrade.

---

## 1. M2 — real-time data for evaluated candidates (SHIPPED)

**Problem (doc 177 sweep):** the data WebSocket subscribed only the ~19-symbol
premarket list + tickers we *filled* (`add_symbols` was called only at the two
post-fill sites, `main.py:6369/6668`). Every candidate discovered/evaluated
post-open ran on **stale REST data** → 59× `D106 VWAP gate SKIPPED — VWAP
unavailable (WebSocket not connected)` on 5/28, plus stale price/volume feeding
the technical agent and the D112 router.

**Fix** (`main.py`, Phase-2 dispatch before `evaluate_candidates`): subscribe the
evaluated set to the live WS.
```python
if _market_ws_client is not None and top_candidates:
    await _market_ws_client.add_symbols([c.ticker for c in top_candidates])
```
`add_symbols` is idempotent + chunked; `top_candidates` is ~10 names so the
subscribed union stays bounded across the session. Real-time trades/bars are
streaming by the next cycle, so VWAP and live price/volume are available when the
bot scores and decides. The Phase-3 rescan path already pulls fresh REST bars
(`get_bars`), so it was left as-is.

*Effect:* removes the structural staleness behind the VWAP-unavailable epidemic
and improves every technical/VWAP-dependent signal on post-open candidates.

## 2. M1 — recon equity tolerance: MTM + sane band (SHIPPED)

**Problem:** `eod_recon.run_eod_invariants` computed
`internal_equity_estimate = starting_equity + realized_pnl` — **realized-only**,
no unrealized MTM. But `broker_equity` includes unrealized P&L on open positions,
so any open position made `delta >> $1` and the fixed `$1.00` tolerance fired
every 30s: **1,308 `D230 RECON_WARN EQUITY` + 1,290 `EOD RECON SUMMARY` lines on
5/28**, burying real signals. Worse, the shadow `recon_daemon` consumes this same
`internal_equity_estimate` (`recon_daemon.py:172`) for its `equity_drift_pct`
lethal trigger — so the flaw would have produced spurious equity-lethal halts if
ever un-shadowed.

**Fix** (`src/monitoring/eod_recon.py`): two parts —
1. **Mark-to-market**: add `_unrealized_pnl` to the internal estimate (parity with
   broker equity).
2. **Band**: compare against `max(equity_tolerance_usd, 0.5% × broker_equity)`
   instead of a flat $1. A real position-tracking drift (a phantom/missing
   position) is 5–15% of equity — far above 0.5% — so it is still caught; normal
   intraday unrealized swings are not.

Because the daemon reads the corrected estimate, **one fix resolves both** the
WARN spam and the un-shadowing-safety blocker. Test updated
(`test_d230_eod_recon_invariants.py::test_equity_exceeds_tolerance_emits_d230`
now uses a ~2.5% drift to represent a real anomaly). All 21 recon tests pass.

## 3. D112 / VCIG — forensic correction (NO change needed)

Docs 176/177 listed **VCIG (+73%)** as a gate-rejected missed winner (D112
INSTANT_REJECT at RVOL 0.3×). On re-examination that framing is **partly wrong**:
- The router is **stateless** (re-classifies each call on `candidate.rvol`), and
  the scan universe **already merges top-movers/gainers** (D117, `main.py:301`) —
  so VCIG was re-surfaceable each cycle. Its non-recovery means its **RVOL stayed
  ~0.3×** even as price ran.
- A **+73% move on 0.3× RVOL is a near-zero-liquidity move** — there was no volume
  to buy into at meaningful size without enormous slippage. The D112 reject
  (`instant_reject_min_rvol = 1.0`) **correctly avoided an untradeable name**.

So VCIG is largely a *false* missed winner, and **D112 needs no code change**. The
genuinely tradeable misses on 5/28 were **NCPL (RVOL 3.5×, ran +38.5%)** and
**AMSS** — both blocked by the *catalyst* gate, already addressed by doc 178's
D200/D204 downgrade-not-block. This is the honest correction: the lever for the
catchable winners is the catalyst gate (shipped), not the RVOL filter.

## 4. Noted but not changed
- **premarket_research universe (Phase 0)** uses only `get_most_active_tickers`
  (`premarket_research.py:73`); the main scan also merges gainers. Adding gainers
  to the Phase-0 universe is a clean, additive, low-value improvement — left for a
  later pass to keep this commit focused.

## 5. Still deferred (from doc 178 §3)
- **H2** — Phase-4 shutdown `state_mgr.reset()` wipes `eod_close_completed`/
  `phase0_completed`. Real but narrow (post-Phase-4 same-day restart) and the fix
  touches the daily-reset state machine (needs date-keyed state so the *next*
  day's EOD isn't blocked). Deferred deliberately.
- **D106** PROMOTIONAL_EARLY 10:30 hard-exit → VWAP-anchored. The doc-178 trail
  widening already helps these names; the refactor is lower marginal value.

## 6. Verification
- `main.py`, `eod_recon.py` compile. 21/21 recon tests pass (incl. the updated
  invariant). M2 is an additive subscribe call guarded by `is not None` + try/except.
- No new env flags; M2/M1 are correctness fixes (not behavior toggles). They
  layer cleanly under the doc-178 posture inversion.

## Appendix — files touched
- `main.py` (M2 candidate WS subscribe at Phase-2 dispatch)
- `src/monitoring/eod_recon.py` (M1 MTM + band)
- `tests/unit/test_d230_eod_recon_invariants.py` (updated invariant)
- This doc + `docs/SYSTEM_MAP/changelog.md`
