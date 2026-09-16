# 180 — Friday Live Analysis (intraday) + the D249 Re-Arm Fix

**Author**: Claude Opus 4.8
**Date**: 2026-05-29 (Friday), snapshot ~10:15 ET (~45 min into the session, LIVE)
**Context**: First live session running docs 177–179 (mechanical fixes + full
posture inversion + M2/M1). This is the real test.

---

## 0. Headline

The posture inversion is **live and firing** and the mechanical fixes are
**holding**. The APPS 970-share ghost was cleaned at open (qty_drift 712→0). But
the close path exposed a **latent safety bug** (D249 re-arm `submit_order`
signature mismatch → position naked ~90s until D313 re-hedged) — **now fixed**.
The deeper read: the catalyst downgrade opened the gate it was meant to, and the
**faller gate is now the binding constraint** on the high-MFCS no-catalyst names —
and it appears to be doing its job (filtering illiquid pumps), so the bot is being
*appropriately selective*, not blindly blocked.

---

## 1. What's working (live validation of docs 177–179)

| Check | Result | Evidence |
|---|---|---|
| Slice crash (Bug A) | **0 occurrences** | `grep slice(` → 0 |
| Phantom-P&L (Bug B) | **0 occurrences** | `grep "WITHOUT attribution"` → 0 |
| Slice-induced UNPROTECTED | **0** | (vs the CRITICAL on 5/28) |
| APPS 970-ghost | **cleaned** | recon `qty_drift_count: 0` (was 712 ticks 5/28) |
| Posture inversion (D200/D204) | **44 DOWNGRADED events** | UMAC 0.551, CGTL 0.615 admitted at qty_mult 0.50 |
| qty_multiplier executor fix | **working** | `SIZING CMND: qty_multiplier=0.50 → qty 6334→3167` |
| M1 recon equity band | **working** | `equity_drift_pct 0.13%` — no lethal-tier spam |
| D248 cancel-blocking-stops | **fires correctly** | APPS 09:30:16 (was masked by the slice crash on 5/28) |

Notably, **D248 only fires because of the slice fix** — yesterday the
`body[:120]` crash masked the 403 and the cancel-blocking-stops detection never
ran. Today it ran. The fixes compound correctly.

## 2. NEW BUG (found + FIXED): D249 re-arm signature mismatch

**Trace** (APPS, 09:30:16–18): D91 overnight-close → 403 → D248 cancelled the
blocking stop → 3 close retries still 403 (broker didn't release `held_for_orders`
within ~2s of the cancel) → D247 escalate → **D249 re-arm FAILED**:
```
D249 STOP_REARM_FAILED APPS: ... POSITION IS NOW NAKED. Operator MUST intervene.
Error: AlpacaDataClient.submit_order() missing 2 required positional arguments: 'qty' and 'side'
```
**Root cause**: `_rearm_protective_stop_from_snapshot` (bridge.py) built a payload
dict and called `client.submit_order(payload)`, but the real signature is
`submit_order(symbol, qty, side, ...)`. The re-arm safety net was **broken**, so a
close-after-cancel failure left the position naked. It was *latent* until the
doc-177 slice fix made the cancel-blocking-stops path actually execute.

**Why it escaped CI**: the unit test mocked `submit_order` as a permissive
AsyncMock that accepted `submit_order(payload)` — masking the real-signature
mismatch. The test "passed" while production failed.

**Fix** (`bridge.py`): use the dedicated `submit_stop_order(symbol, qty, side,
stop_price, time_in_force='gtc', position_intent='close')`. Test updated to assert
`submit_stop_order` with the correct kwargs + mock it faithfully. 7/7 force-close
tests pass.

**Impact bound**: D313 hedge-watcher re-hedged APPS ~90s after the naked window
(09:31:58, new stop oid 63671668), so exposure was limited. But the re-arm net is
now actually functional.

## 3. The execution funnel reality — the faller gate is the new wall

The downgrade opened the catalyst gate, but candidates then hit **D160 FALLER**:

| Ticker | MFCS | Catalyst gate | Faller gate | Outcome |
|---|---|---|---|---|
| **CGTL** | 0.615 (top momentum rank 81.5) | DOWNGRADED (pass) | **score 0.669 > 0.60 → BLOCKED** (manip 90%, no cat, dolvol $641K) | no entry |
| **UMAC** | 0.551 | DOWNGRADED (pass) | score 0.467 → REDUCE 75% (later 0.000 → FULL) | size-reduced |
| **CMND** | 0.265 (not downgraded) | passed | (passed) | **ENTERED** @ 10:15, qty 3167 |

**Read**: CGTL is the top-ranked name every cycle, but it's an illiquid
promotional pump (84% gap, RVOL 96×, but only $641K dollar-volume, volume >
float, manip 90%, no catalyst) — the faller gate **correctly** blocks it (it would
likely be untradeable / fade, the VCIG pattern from doc 179). So the layered
defense (catalyst-downgrade → faller → liquidity) is working: it admits the
tradeable conviction names and filters the genuine pumps.

**Caveat worth watching**: the one entry so far (CMND) is *itself* a thin pump
(dolvol $243K < CGTL's $641K, STRONG_BEAR technical, RVOL 549×) yet it passed the
faller gate while CGTL didn't. There may be a faller-gate consistency issue (a
lower-conviction thinner name got in while a higher-ranked one was blocked). Flag
for EOD review with the actual outcomes.

## 4. Secondary observations (not yet actioned)

- **D99 cancels slow agents at the 29s phase-1 cutoff: 93× today** (36 news, 51
  fundamental). Pipeline latency 35–36s. The doc-178 timeout bump (15→25s) does
  NOT help here — the *orchestrator's* 29s cutoff cancels agents regardless, and
  raising the per-call timeout may even push agents past it. **But the downgrade
  posture mitigates the blocking** (EMPTY news no longer vetoes — UMAC admitted at
  0.551). The real fix is pipeline speed (fewer ensemble calls, faster news fetch)
  or aligning the cutoff with the timeout — deferred, not blocking.
- **Kelly Tier 1 already sets risk=2.0%** (`D115 KELLY ... risk=2.0% max_pos=25%`).
  So the doc-178 ELITE press (also 2%) is **redundant** — `0.02 > 0.02` is false,
  so it never fires (no ELITE SIZING logs). Not a bug; the baseline is already 2%.
  To actually press the tail, bump `elite_risk_per_trade_pct` to 0.03, or drop the
  press. (Correction: doc 178 assumed a 1% base; live base is 2%.)
- **APPS zombie tracker entry**: after the failed close, the tracker holds an APPS
  entry with qty=0 + a stale stop_order_id → `D231 RECON_HARD_BLOCK` spam, plus an
  orphaned $5.88 stop at the broker (no position). Cosmetic (qty 0, no trading
  risk) but should be cleaned. Likely the same close-path that failed left the
  tracker half-updated.
- **Cancel-then-close timing**: even 2s after D248 cancelled the APPS stop, the
  broker still showed `held_for_orders=970` across all 3 retries. The wrapper's
  0.5s propagation wait may be too short for the paper broker (or a second order
  held the qty). With the D249 re-arm now fixed, a failed close at least re-arms
  protection; but lengthening the post-cancel wait would let more closes succeed
  outright. Filed.

## 5. P&L / positions (snapshot ~10:15 ET)
- broker_equity **$150,757** (vs 5/28 close $150,548 → +$208, mostly MTM + the APPS
  resolution; not realized strategy P&L yet).
- **1 entry in progress: CMND** (qty 3167 @ $3.57 limit). No closes yet → no
  realized P&L. Session is young.

## 6. Recommendations
1. **Shipped now**: D249 re-arm fix (this doc). Restores the naked-position safety net.
2. **EOD review**: did CMND (the thin-pump entry) win or lose? Did the faller gate's
   block of CGTL prove correct (did CGTL fade)? This validates whether the
   catalyst-downgrade + faller-gate combination is catching the right names.
3. **Consider**: bump `elite_risk_per_trade_pct` 0.02→0.03 to make the ELITE press
   actually do something (currently redundant vs Kelly's 2% base).
4. **Deferred (still)**: pipeline speed / D99-vs-timeout alignment; APPS zombie +
   orphaned-stop cleanup; cancel-then-close wait tuning; H2 shutdown-flag.

## Appendix — files touched
- `src/execution/bridge.py` (`_rearm_protective_stop_from_snapshot` → submit_stop_order)
- `tests/unit/test_bug_ai_force_close.py` (re-arm test updated to the correct signature)
- This doc + `docs/SYSTEM_MAP/changelog.md`
