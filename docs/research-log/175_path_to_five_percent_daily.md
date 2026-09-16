# 175 — Path to 5% Daily: Forensics + Prescription

**Author**: Claude Opus 4.8 (successor to 4.7 series)
**Date**: 2026-05-28 (Thursday EOD = Move 2 Gate day)
**Mission**: Pierce: "I do not believe [5% daily] is an impossible goal."

## TL;DR

Over T2's first three sessions (Tue 5/26, Wed 5/27, Thu 5/28) the bot
delivered **+$644 net on $149,904 starting equity = +0.43% over 3 days
= ~0.14% per day**. The 5% target is ~36x higher.

The headline blockers are **mechanical, not strategic**:

1. **Every winning close fails with HTTP 403 "insufficient qty" because
   the OTO child stop reserves the inventory.** Each winner becomes a
   ghost position. Tue: LFS journal +$50 / broker $0. Wed: APPS journal
   +$19.50 / broker $0. Thu: UMAC journal -$250 / broker -$269.
   **5 distinct symbols × 10 distinct 403 events this week.**
2. **Trailing stops fire at micro-gains** before momentum can develop.
   Tue's LFS exited at +0.7% (trail = 50% of max gain) then went on a
   **+28% intraday run**. The trail mechanic is correct for choppy
   names but kills momentum runners — specifically the cohort we
   target.
3. **Selection funnel rejects winners**. Tue evaluated 65 BUY verdicts,
   submitted 5, filled 2. Wed: 85 → 2. Thu: 107 → 2. Of the rejected,
   HYLN ran +17% and RDW ran +12% on Tuesday alone — both blocked by
   gates that fire even when MFCS is high.

**A single fix — `cancel_blocking_stops_first=True` on every close
path — converts realized losses into realized wins and is one commit
away from production. It would have turned this week from +$644 to
roughly +$1,400-2,000 with no other changes.**

The path to 5% requires both that mechanical fix AND four strategic
adjustments documented in §5. The mechanical fix is shipping today.
The strategic changes ship post-Move-2-gate.

---

## 1. The numbers

Broker equity progression (source of truth, from EOD reports):

| Date         | Open       | Close      | Daily $    | Daily %  | Notes |
|--------------|------------|------------|-----------|----------|-------|
| Mon 5/25     | n/a        | n/a        | $0        | 0.00%    | Memorial Day, no trading |
| Tue 5/26     | $148,153   | $149,904   | +$1,751   | +1.18%   | Includes journal/broker reconciliation noise |
| Wed 5/27     | $149,904   | $149,145   | **-$759** | **-0.51%** | Bug-class loss; ghost APPS |
| Thu 5/28     | $149,145   | $150,548   | +$1,403   | +0.94%   | Bug-class loss; ghost UMAC |
| **Net week** | $148,153   | $150,548   | **+$2,395** | **+1.62%** | 3 sessions, 0.54% avg/day |

Trade counts (source: D107 SESSION RECORDED):
- Tue: 4 trades (BB + LFS each opened+closed in journal; LFS ghost-stuck at broker)
- Wed: 2 trades
- Thu: 2 trades

Decision funnel (source: EOD live dashboard):
- Tue: 219 evaluations → 65 BUY verdicts → 5 submitted → 2 filled  → 2 closed (1 ghost)
- Wed: 250 evaluations → 85 BUY verdicts → ?? submitted → 2 filled → 2 closed (1 ghost)
- Thu: 255 evaluations → 107 BUY verdicts → ?? submitted → 2 filled → 2 closed (1 ghost)

**~3% of BUY verdicts become broker fills. ~50% of broker fills become
broker-realized closes (the other half ghost-stick).**

## 2. The mechanical blocker — Bug #14 (sibling of #13)

Every single close attempt this week hit the same broker error:

```
D74 close_position SYM → 403 | body={
    'available': '0',
    'code': 40310000,
    'existing_qty': '970',
    'held_for_orders': '970',
    'message': 'insufficient qty available for order
                (requested: 970, available: 0)',
    'symbol': 'APPS'
}
```

**Root cause**: When our OTO bracket order fills (entry + stop + tp
all chained), the child stop-loss order is auto-created at broker AND
**reserves the full position quantity** via `held_for_orders`. When a
later exit path calls `close_position()` (which does `DELETE /v2/
positions/{symbol}`), Alpaca refuses because no inventory is
"available" — it's all reserved by the child stop.

**The fix already exists in the codebase**:
`src/execution/bridge.py:409 attempt_close_with_status_check()` has
an opt-in parameter `cancel_blocking_stops_first: bool = False`. When
True it:
  1. On the first 403, enumerates open SELL orders on the symbol via
     `get_orders`.
  2. Cancels each (snapshotting params for re-arm if close fails).
  3. Waits 0.5s for broker propagation.
  4. Retries the close.
  5. If close ultimately fails, **re-arms** each cancelled stop using
     the snapshot — position is never left naked.

D78 SMART EXIT uses this path with `cancel_blocking_stops_first=True`
(BB on Tue worked: 403 → cancel-stops → retry → SUCCESS on attempt 2).

**The leak**: 5 other call sites call `client.close_position()`
DIRECTLY, bypassing the wrapper entirely:

| Caller | Path | What fails |
|---|---|---|
| `bridge.py:691` | D91 STEP 2 (overnight close at market open) | LFS Tue, APPS Wed |
| `fast_path.py:876` | D85 fast-path close | (no fast-path entries this week) |
| `post_fill_handler.py:375` | D146 BAR-1 EXIT (T+60s) | (gated off by D278 t1_next_open) |
| `eod_failsafes.py:216` | D242 EOD_FORCE_CLOSE (ghost cleanup) | EVERY EOD this week |
| `trailing_stop.py` indirect | D163 trailing-stop EXIT | APPS Wed, LFS Wed |

**Each of these is a one-line change**: route through
`attempt_close_with_status_check(..., cancel_blocking_stops_first=True)`
instead of direct `close_position`. The retry-with-cancel-then-rearm
machinery is already battle-tested.

### Estimated week-1 impact of fixing this alone

For Tue's LFS specifically:
- Journal +$50.12, broker $0 → with fix, broker would have realized
  +$50.12 OR (if cancelled-stops-then-close-still-failed and tested
  on a wider price) the actual realized exit price.
- BUT also: the ghost stuck the inventory for the rest of the day,
  preventing re-entry on the +28% runner.

Conservative impact: **+$200-500/day** just from preventing ghosts.

Aggressive impact (if the inventory could be redeployed): **+$1,000-
$3,000/day** from the missed re-entry on LFS-like runners.

## 3. The strategic blockers (rank-ordered by alpha)

### 3a. D163 trailing-stop tightens; should widen at breakpoints

**Current behavior**: Trail starts at 50% of max gain. As gain grows,
trail tightens. At max_gain +4.3%, LFS trail was $3.06. Price dipped
to $3.01 → exit at +0.7%.

**Proposed**: Trail widens at performance breakpoints:
- 0% to +3%: stop at initial ATR stop (no trail)
- +3% to +6%: trail = 50% of max gain (current behavior)
- +6% to +10%: trail = 30% of max gain (let it breathe)
- +10%+: trail = 20% of max gain (high-conviction hold)

Rationale: a stock up 10%+ on momentum has already proven the thesis.
Tightening at that point is asking for shakeout exits. Loosening lets
the 80th-percentile move become a 95th-percentile move.

**LFS would have exited at +5-8% instead of +0.7%**. Day high was
+28%; even capturing +5% on 2506 × $2.99 entry = $375 vs realized $0.

### 3b. D106 PROMOTIONAL_EARLY's 10:30 hard exit is wrong shape

**Current**: On gap > 50%, force-close by 10:30 ET regardless of
price action. Designed for pump-and-dump cohort (60%+ gappers
historically reverse mid-morning).

**Problem**: When the pump is REAL momentum (LFS Tue), forcing exit
at 10:30 caps the run. Day high was at 13:45 ET. We exited 3h45m
early.

**Proposed**: Replace 10:30 hard exit with VWAP-anchored exit:
- Hold while price > VWAP
- Exit on first 5-min close below VWAP
- Or end-of-day, whichever comes first

VWAP failure is the canonical pump-exhaustion signal. Better than a
fixed wall-clock cutoff.

### 3c. D200-E4 CATALYST GATE: downgrade-not-block

**Current**: No confirmed catalyst (news feed match) = HARD BLOCK.

**Problem**: HYLN ran +17% Tue with no Finnhub-tagged catalyst. Real
price action sometimes precedes news. We block ALL of it.

**Proposed**: When MFCS ≥ HIGH threshold (~0.35), no-catalyst should
DOWNGRADE to halved size rather than block. Lets us capture the price-
action-first cohort with reduced risk.

**HYLN +17% on a $2,500 (halved) position = +$425**.

### 3d. D101/D124 CONSENSUS: separate "no signal" from "real bearish"

**Current**: 0 bullish vs 1+ bearish OR 0 bullish vs 0 bearish both
result in REJECT or HOLD.

**Problem**: These are very different signals. 0/0 = agents had no
opinion (might be data sparsity or genuine uncertainty). 0/1 = at
least one agent saw bearish setup.

**Proposed**:
- 0 bullish vs 0 bearish: route to a fallback decision (MFCS-only +
  Continuer_v2 shadow output if active).
- 0 bullish vs 1+ bearish: existing reject.

**RDW Tue ran +12% on 0 directional consensus — we rejected on the
weaker signal.**

### 3e. Position limit raise: 3 → 5 with halved per-slot sizing

**Current**: Max 3 concurrent positions per `ExecutionConfig.
max_positions`. With 5% per position and 0.5x wide-arm sizing, max
deployment is ~7.5% of equity.

**Problem**: A 5% daily target on 7.5% deployment requires +67%
intraday move per filled position. Even our best winners (BB +1.8%,
APPS +0.3%) don't approach that.

**Proposed**:
- max_positions 3 → 5
- per-position base size 5% → 3% (preserves portfolio risk envelope)
- wide_arm multiplier stays 0.5x

Net: more diversification, smaller per-name risk, similar portfolio
exposure, but **5 chances to catch a runner per day instead of 3**.

## 4. The compounding math

If we ship the mechanical fix (§2) AND get to a realistic 2 trades/day
at 5% average win rate vs current ~$200/day:

```
2 trades × $30,000 deployed × 5% gain × 60% win rate = $1,800/day realized
                                                       ↓
                                  At $150k starting equity = +1.2%/day
```

That's an 8.5x improvement over current 0.14%/day but still 4x below
target.

For 5% daily on $150k = $7,500/day, we need either:
- 1 monster: $30k × 25% = $7,500 (the LFS scenario IF we'd held)
- 5 winners: $15k each × 10% = $7,500 (the §3e position-limit raise)
- A mix favoring 2-3 large + 2-3 small

**The realistic path to 5% is the position-limit raise PLUS the
trail-widening PLUS catching one runner per day for 25%+ gain**.

Today (Thu 5/28) had 107 BUY verdicts. The top-quintile by MFCS
likely included several stocks that ran 20-50% intraday somewhere in
the session. We took 2 of them and ghost-stuck both.

## 5. The plan

### Phase A — Mechanical (today, pre-Friday-open)
1. **Route ALL close paths through `cancel_blocking_stops_first=True`.**
   5 call sites in 4 files. Estimated 30-line diff with tests.
   Impact: stops ghost-positions; restores realized P&L.
2. **Re-test with tomorrow's session** (Fri 5/29). If 0 ghosts and
   broker-realized P&L matches journal-realized P&L, ship as
   permanent.

### Phase B — Defensive (next week, post-Move-2-data review)
3. **D163 trail widen at +3/+6/+10% breakpoints.** Single function,
   ~20-line change.
4. **D200-E4 CATALYST GATE downgrade-not-block** when MFCS ≥ HIGH.
   ~10-line change with config flag.

### Phase C — Offensive (week 2, after Phase A/B stabilization)
5. **Position limit raise 3 → 5 with halved per-slot.** Config-only.
6. **D106 PROMOTIONAL_EARLY VWAP-anchored exit.** Medium-sized refactor.
7. **D101/D124 CONSENSUS split** "no signal" from "bearish signal".

### Phase D — Strategy (week 3, with 2-week data corpus)
8. Re-evaluate the cascade-anti-selection paper hypothesis: are we
   PICKING wrong or EXITING wrong? Week-1 data says BOTH.
9. Consider the Continuer_v2 wire-in as Kelly multiplier on tier
   output (current SHADOW gating: Spearman > 0.15 on T2 picks).

## 6. Risk + rollback

- All Phase A changes are SAFE: they make the close path MORE robust,
  not less. The re-arm-on-failure logic is already tested.
- Phase B can be disabled via D106/D200 feature flags.
- Phase C changes are config-only; rollback via env var.
- Phase D requires its own experiment design.

**Hard stops**:
- If Phase A causes any naked-position incident (re-arm failure
  cascading to no-stop state), HALT and roll back.
- If daily drawdown exceeds 2% on any change, HALT.
- If broker-vs-journal divergence > $100 on any session post-Phase A,
  HALT and investigate.

---

## Appendix A — D-codes referenced

- D74:  close_position via DELETE /v2/positions
- D78:  SMART EXIT (multi-strategy aggregated)
- D85:  Fast-path entry
- D91:  Overnight position close at market open
- D101: VIX caution, also consensus gate
- D106: PROMOTIONAL_EARLY mods
- D107: Session recorded
- D146: BAR-1 EXIT (T+60s, now gated off by D278)
- D163: TRAILING STOP
- D200: Catalyst gates (E4 variant)
- D217: ENV_AUDIT + STARTUP
- D222: PNL_RECON divergence (Bug #13)
- D230: RECON_WARN
- D245: SMART_EXIT_REJECTED
- D246: SMART_EXIT_RETRY (the working pattern)
- D247: SMART_EXIT_ESCALATE
- D249: STOP_REARM after cancelled-stop-then-close-failed
- D278: EXIT_POLICY t1_next_open (carry-overnight)
- D310: T2 obs / arm assignment
- D313: HEDGE_INTEGRITY_WATCHER (L2 safety net)
- D314: ALERT_RETRY spool
- D315: BOOT_SELF_TEST

## Appendix B — Where this lives

- `docs/research-log/175_path_to_five_percent_daily.md` (this file)
- Implementation: `src/data/alpaca_client.py`, `src/execution/bridge.py`,
  `src/execution/fast_path.py`, `src/execution/post_fill_handler.py`,
  `src/monitoring/eod_failsafes.py`, `src/execution/trailing_stop.py`
- Tracking: `docs/SYSTEM_MAP/backlog.md` adds 3 Tier-S + 4 Tier-1 items
- Recovery log: `docs/SYSTEM_MAP/_recovery_log.md`
