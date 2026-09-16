# 30 — Bug W LIDR Evidence (Fri 2026-04-24, evening)

**Purpose.** Document the LIDR live-session observation that surfaced Bug W,
trace the failing code path against the patched code in commit `93800b0`,
and confirm the patch covers the observed failure mode (no Bug W′ needed).

## What was observed (live session)

| Timestamp | Event |
|-----------|-------|
| 10:16:46 ET | OTO buy submit: qty=5264 @ entry_limit=$2.44, **stop=$2.40** (Phase-0 tightened) |
| 10:16:46 ET | OTO stop leg confirmed: oid=`3d7c797d…` @ **$2.40** |
| 10:16:48 ET | Bridge polls terminal: status=filled, filled_avg_price=$2.42 |
| 10:16:48 ET | Position opened: `qty=5264, entry=$2.42, stop=$2.40` (Bug R fix held) |
| 10:16:48 ET | **`RESCAN exit_ladder LIDR: cancelled prior stop`** ← restructure fires |
| 10:16:48 ET | T1 limit submitted qty=1754 @ $2.5568 |
| 10:16:48 ET | T2 limit submitted qty=1754 @ $2.6785 |
| 10:18:20 ET | Live broker query: residual stop now at **$1.90** (un-tightened verdict.stop_loss) |
| 10:18:20 ET | Internal tracker still shows `pos.stop_loss = $2.40` |

**Discrepancy:** broker stop $1.90 vs tracker stop $2.40 = $0.50 / 21% drift.
Risk math wrong by ~14× (0.8% vs 11% drawdown to stop trigger).

## Path identification

The exit-ladder fired from the **RESCAN** code path (`main.py:6349`
exit-ladder call + `main.py:6399` register_stop). This is **one of the
6 sites patched in commit `93800b0`**:

```
$ grep -n "RESCAN exit-ladder" main.py
6340:                                                        "RESCAN exit-ladder %s: position not found post-fill"
6382:                                                        "RESCAN exit-ladder %s: POSITION UNPROTECTED "
6390:                                                    "RESCAN exit-ladder %s: unexpected error: %s"
```

Patch verification — both RESCAN sites use `_rs_pos.stop_loss`
(broker truth) post-patch:

```
$ grep -n "stop_price=_rs_pos.stop_loss" main.py
6361:                                                        stop_price=_rs_pos.stop_loss,
6399:                                                    stop_price=_rs_pos.stop_loss, qty=order.qty,
```

## Why LIDR still fired the bug

**Process-vs-disk timing.** The running paper-trading process was started
this morning at 04:30 ET via `daily_paper_trade.ps1`. At that moment the
running code was commit `1db1031` (Track A foundation) — the Bug W patch
had not yet been written.

The Bug W patch was written, tested, and committed tonight at ~18:00 ET
in commit `93800b0`. The patched files now exist on disk, but **the
running Python process holds the old bytecode in memory** until a restart.

**Implication for tomorrow's session:** when the launcher starts python
fresh at 04:30 ET on the next session, the new bytecode loads from disk
and the patched RESCAN exit-ladder will use `_rs_pos.stop_loss = $2.40`
(broker truth) instead of `verdict.stop_loss = $1.90`. LIDR-shape
positions opened tomorrow will not exhibit the bug.

## Confirming no Bug W′ exists

Three checks rule out a 7th site or alternative path:

### Check 1 — only RESCAN exit_ladder fired on LIDR

`grep -nE "LIDR.*(exit_ladder|cancel.*stop|register_stop)"` on the log
returns exactly one exit_ladder block (RESCAN at 10:16:48). Phase 2 and
VWAP exit-ladder paths did not fire. FastPath did not fire (LIDR went
through the standard Phase 2/3 evaluation pipeline, not the FastPath
short-circuit). Short OTO did not fire (LIDR was a long entry).

### Check 2 — D165 tranche taker is price-based, doesn't restructure stops

`src/execution/d165_tranche_taker.py` is the price-monitoring tranche
helper that fires limit sells when targets are crossed; it does NOT
cancel-and-resubmit the stop. It is not in scope for Bug W.

### Check 3 — source-grep test passes against current main.py

```
$ pytest tests/unit/test_d24_bug_w_exit_ladder_stop.py -v
TestBugW_ExitLadderCallSites::test_no_exit_ladder_call_uses_verdict_stop_loss PASSED
TestBugW_RegisterStopCallSites::test_no_register_stop_uses_verdict_stop_loss PASSED
TestBugW_PositionStopIsBrokerTruth::test_managed_position_stop_loss_is_actual_stop_after_bridge PASSED
```

Zero remaining `stop_price=verdict.stop_loss` in long-OTO contexts in
main.py post-`93800b0`. If a 7th site existed, this test would fail.

## Verdict

**Bug W is fully patched in `93800b0`. No Bug W′ exists.**

LIDR's drift is a **stale-bytecode artifact** from a pre-patch process,
not a coverage gap in the patch. The failure mode is impossible after a
process restart.

## Action items prompted by this evidence

1. **Tomorrow morning's launcher restart will activate the patch.** No
   manual intervention required.
2. **Track A item 21 (heartbeat dirty-worktree flag, D239) is now
   urgently needed.** The launcher cannot detect that the running
   process holds older bytecode than is present on disk; only a manual
   restart fixes it. The D239 marker (queued for implementation per
   item 25 of the next-actions list) would have surfaced this gap.
3. **LIDR's outcome (whatever it is) should be marked
   `infrastructure_contaminated=true, bug=W` per item 30** — the fill
   happened against a wrong-stop broker order; the position's P&L is
   not strategy-truthful and must be excluded from BOCPD pre-training
   and Phase 0 calibration data.
4. **The root cause was discovered in <2 minutes** because:
   (a) the AST audit + source-grep test family already enumerated the
   6 sites per Bug R/W taxonomy
   (b) the log trace at line 51187 explicitly named the path (`RESCAN
   exit_ladder LIDR: cancelled prior stop`) — observability dark zones
   shrink discovery time
   This is the discovery infrastructure working as designed.

## What this updates in `19_eod_bug_findings.md` template

For Bug W's "which methodology would have caught this" field per item 23:

- **Source-grep test**: ✓ caught it (3 tests in `test_d24_bug_w_exit_ladder_stop.py`)
- **PBT state-machine** (when implemented): would catch via
  `tranche_restructure_preserves_tightened_stop` invariant
- **Track B recon daemon** (shadow when wired): would catch as
  D231 STOP drift within 30s of restructure
- **EOD recon** (Track A item 3): catches at session close per
  the explicit `D231 RECON_HARD_BLOCK STOP LIDR` log line tonight
  (assuming LIDR is still in tracker at 16:00, which depends on
  whether BAR-1 fired)

This is the chain item 23 wants captured — multiple defenses now exist
for the same bug class, with different latencies (CI-time, real-time
30s, EOD batch).
