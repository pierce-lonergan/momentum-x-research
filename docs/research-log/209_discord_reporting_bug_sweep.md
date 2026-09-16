# 209 — Discord reporting bug sweep (from the live 5/27–5/29 alert stream)

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "one last sweep for bugs and Discord reporting issues" (with the
actual 5/27–5/29 alert stream pasted in).

The pasted alerts were a goldmine. Three parallel investigations + log verification found
**4 mechanical reporting bugs + 1 real trading bug (the APPS overnight carry)**, all
root-caused. Fixed the high-confidence/safe ones; documented + spawned the rest.

---

## FIXED (committed, verified)

### Bug 1+2 — EOD headline always $0.00 / "Profitable day" on losing days (commit 300c121)
Every EOD report showed `Realized P&L $+0.00 · Starting/Ending Equity $0.00 · Day Change
+0.00% · "Profitable day"` — even 5/28 (−$269) and 5/29 (−$1,500) — while the recon
SUB-section had the truth (`broker_pnl=$-1,500.60`).
- **Cause** (`main.py`): `reset_daily()` (line ~7521) zeroes `_daily_realized_pnl`, then the
  EOD payload was built AFTER it from that (=0) + `_unrealized_pnl` / `_session_start_equity`
  (**non-existent attributes** → 0). The headline read empty sources.
- **Fix**: capture `_daily_realized_pnl` + `_starting_equity` BEFORE reset; source the
  headline from `broker_truth_recon.broker_total_pnl` first (the truth that already shipped
  in the sub-section), else the pre-reset realized value.
- **"Profitable day" label** (`alerts.py:943`): `realized_pnl >= 0` made $0.00 read
  "Profitable day". Fixed: `>0` profitable / `<0` loss / `==0`-with-trades = honest "verify
  reconciliation" (a 0.0 with trades was the empty-source bug, not a true scratch).

### Bug 3 — D315 boot self-test "Account equity: $0.00" (commit 300c121)
The boot self-test read `getattr(position_manager, "_session_start_equity", 0)` — a
**non-existent attr** → always $0.00 (while the sibling "Trading Session Started" alert
showed the correct $150,563). Fixed: prefer the live `account.equity` (same source as the
session-start alert), then `_starting_equity`, then 0.

### Bug 5 — D91 overnight-close cancel-settle race = the APPS 3-session carry (commit 7d573a5)
**The real trading bug.** The 5/29 log is the smoking gun:
```
09:30:15 D91 STEP 1 APPS: cancel stop oid=8525e4f3…
09:30:16 D91 STEP 2 APPS: submit market sell qty=970
09:30:16 D74 close_position APPS → 403 held_for_orders: 970, available: 0   (×3)
09:30:18 D247 SMART_EXIT_ESCALATE APPS: 3 retries exhausted; broker close FAILED.
```
STEP 1 cancels the stop; STEP 2 sells ~1s later but the cancel hasn't **settled**, so the
970 shares are still `held_for_orders` → 403. The cancel-blocking-stops logic finds nothing
left to cancel (STEP 1 already did) → skips its settle-wait → burns all 3 retries into the
same unsettled 403 → close fails → APPS carries overnight. Repeated 5/27→28→29. (This is the
documented `held_for_orders` phantom class.) **Fix**: STEP 1 now polls `qty_available` until
the cancel settles (~3s, bounded) before STEP 2; the retry loop now waits 1.5s when
qty-blocked-but-nothing-to-cancel. 31/31 recovery tests pass; 0 new failures.

## DOCUMENTED + SPAWNED (not rushed — safety-critical or separate process)

### Bug 4 — D313 HEDGE VIOLATION re-fires every session for a carried position
APPS/STG/CMND fired D313 repeatedly across sessions. The watcher computes `is_hedged` from
`matched_orders` (broker stops matching the position); a carried position WITH its emergency
stop still at the broker should match → no violation. It re-fires, so the **matched-order
detection isn't finding the carried stop** (or flags a correctness_issue) on the new
session. This is subtle, safety-critical hedge-detection logic — the right fix needs live
order-state verification (exactly the Operator's job), NOT a rushed guess. **Spawned as a
task.** (Note: APPS rallied +22% so the −15% emergency stop correctly never fired — this is
alert-noise + an unmanaged-carry symptom, not a protection failure.)

### Bug 6 — Daily Data Ingest "max d0 ?, rows ?, runtime 0s"
`scripts/daily_data_ingest.ps1`: the duration is computed from a misplaced start-timestamp /
the log's LastAccessTime (not the real start), and the rows/max_d0 regex can pass None. A
separate process (not the trading bot); low risk. **Spawned as a task.**

### Bug (minor) — STALE EOD re-fires next morning + Top-Winner +$ with −%
The durable alert spool redelivers the prior EOD ("[STALE - originally fired at …]") at next
boot — cosmetic. The Top-Winner "+$507 (−0.2%)" mixes journal realized-$ with a price-path %
(the dollar is right; the % is computed from entry→exit on a different basis). Both noted for
the Operator; low priority.

## Why this split is the right discipline
The reporting bugs (1/2/3) are mechanical, high-confidence, zero trading-logic risk → fixed
now. The close-race (5) was log-proven with a precise, bounded, tested fix → fixed now. The
hedge-detection (4) is safety-critical and needs live state → documented + spawned, not
guessed. Same rule as all session: mechanical fixes unilaterally; anything touching live
protection logic gets verified, not rushed.

## Appendix — files
- `main.py` (EOD P&L capture-before-reset + source from broker-truth; D315 boot equity),
  `src/monitoring/alerts.py` (Profitable/Loss/scratch label), `src/execution/bridge.py`
  (D91 cancel-settle poll + retry settle-wait).
- This doc + `docs/SYSTEM_MAP/changelog.md`.
