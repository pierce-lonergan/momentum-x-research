# 226 — Tuesday (6/2) post-mortem: the +31% truth + a new bug class

**Author**: Claude Opus 4.8
**Date**: 2026-06-02 (Tuesday, post-close)
**Mandate**: Pierce — "we're up 31% today! but let's see how the system performed according to
what we set out for it to do."

The honest answer: **+31% is real (paper) equity, but it's an UNREALIZED, accidental
overnight carry of a position the system tried and FAILED to flatten — and the realized
trades LOST money.** Judged on the doc-223 criterion (execution integrity, not P&L), Tuesday
was a **mixed result that exposed a new bug class** our execution-hardening arc didn't cover.

---

## 0. The numbers (broker ground truth)

| | |
|---|---|
| Equity | $148,744 → **$193,336** = **+$44,591 (+30%)** ✅ real paper equity |
| **Realized P&L** | **−$3,950** (the bot's CLOSED trades lost money) |
| Unrealized (open, carried overnight) | **+$52,716** — almost entirely **LASE +$50,921** |
| Open at close | **2** (LASE 26,772 sh; STAK 2,679 sh) — both carried, UNINTENDED |
| Deployed commit | `f29a0bd` ✅ (all 18 commits + the doc-225 fix live) |

**The entire +$44K is the LASE unrealized gain.** LASE: entry ~$1.33, now $3.23 — a **+143%
runner the bot accidentally held**, exactly the 5/28-5/29 APPS/STG pattern (MEMORY: "the bot's
deliberate trades lose; accidental 403-stuck holds win"). Strip LASE's unrealized and the day
is the −$3,950 realized loss.

## 1. What WORKED (the hardening arc held where it applied)

- **doc-209 honest EOD report**: the report shows `broker_total_pnl = -$3,950` (truth), NOT
  the journal's `-$3,456` — and it flagged `qty_drift=2`, `equity_within_tol=False`, 3
  disagreements. **We SAW the problem same-session**, exactly as designed.
- **doc-216/220 cancel-settle + D242**: at 16:00:37 `D248 BLOCKING_STOPS LASE: cancelled
  blocking order; qty FREED after settle-poll` then `D242 EOD_FORCE_CLOSE LASE: closed via
  cancel-stops-then-close on attempt 1`. The settle-poll fired and freed qty — **the mechanism
  worked.**
- **Tuesday ran the new code** (`f29a0bd`); no crash; clean shutdown.

## 2. What FAILED — the new bug class: QTY-DRIFT GHOST from partial fills (D218)

The root cause is UPSTREAM of everything we hardened. The log:
```
10:49:13 LASE order ...filled filled_qty=16020 @ 1.34
11:05:26 LASE order ...filled filled_qty=16092 @ 1.32
11:05:31 D218 QTY_DRIFT LASE: internal_qty=16092 broker_qty=26772 delta=+10680 —
         reconciling. Likely cause: partial-fill captured by D217 poll before terminal fill.
```
- The bot **bought LASE in multiple tranches** (a runner; multi-buy). The **D217 poll captured
  a partial fill (16,092) as if terminal**, but the broker actually filled **26,772**. The
  internal tracker was **10,680 shares short of reality ALL DAY.**
- Consequence 1 — **alert storm**: `D231 RECON_HARD_BLOCK` fired **1,985×**, `D313
  EMERGENCY_STOP_FAILED` **839×** (the watcher tried to hedge the ghost qty, 403'd every time
  because the protective stops already reserved the tracked portion). The bot was screaming
  about LASE all day.
- Consequence 2 — **EOD close 403**: at close, 10,680 sh were `held_for_orders` (stops on the
  tracked qty), available only 16,092 < the 26,772 it tried to sell → 403. D242 freed *some*
  and force-closed, **but the position is STILL OPEN at the broker (+$50K) 30 min after close**
  — so D242's after-hours market close also did not fully fill / the ghost portion survived.
- Consequence 3 — **the phantom persists**: `D222 PNL_RECON DIVERGENCE` LASE journal `+$928.99`
  vs broker `−$896.12`. (The doc-222 ledger that would make this impossible is built but NOT
  authoritative yet — by design, pending the shadow.)

**This is a NEW bug class**, distinct from the cancel-settle race (216) / naked re-arm (218) /
partial-CLOSE strand (219): it's a partial-FILL on ENTRY mis-tracked, creating a qty-drift
ghost that poisons hedging, the EOD close, and P&L. Our arc hardened the CLOSE path; this is the
ENTRY/fill-tracking path. The doc-218 Adversary should grow a `partial_entry_fill_drift`
scenario.

## 3. Honest scorecard vs the doc-223 criterion

doc-223 said: *judge Tuesday on execution integrity, not the P&L number.* By that bar:
- ✅ Honest reporting (saw the truth)
- ✅ Cancel-settle / D242 mechanism fired
- ❌ **A position carried overnight unintended** (the thing doc-220 was meant to prevent — but
  via a different root cause: qty-drift, not the 15:59 race)
- ❌ **Qty-drift ghost** (D218) — internal ≠ broker all day; 1,985 D231 + 839 D313 alerts
- ❌ **Phantom P&L** (D222 divergence) still occurring (ledger not yet authoritative)
- ⚠️ The +31% is **luck, not skill** — an accidental hold of a +143% runner; realized −$3,950

**Verdict: the day made money, but the SYSTEM did NOT do what we set out for it to do.** It
got lucky on a ghost it couldn't close. This is the *most dangerous* kind of green day — it
would tempt us to trust machinery that's actually carrying a 4-figure-unrealized accidental
position.

## 4. The clear next priorities (re-ranked by what Tuesday exposed)

1. **P0 — fix the D218 partial-fill qty-drift at ENTRY** (the new root cause). The D217 poll
   must not treat a partial fill as terminal; reconcile to broker truth BEFORE arming stops /
   computing size. This caused the 1,985 D231 + 839 D313 spam AND the un-closeable EOD qty.
2. **P0 — LASE is OPEN overnight at +$50K**: decide NOW whether to hold or flatten at tomorrow's
   open (it's a real unprotected paper position; the +$50K is at risk). The bot will try to
   close it at 04:00 via D91 — but the SAME qty-drift may 403 it again.
3. **P1 — accelerate the doc-222 ledger to authoritative** (the phantom recurred today): the
   shadow + cutover would make the D222 divergence structurally impossible.
4. **P1 — Adversary `partial_entry_fill_drift` scenario** so this class is permanently guarded.
5. The D313/D231 spam (2,824 alerts on one ghost) needs the carried-position dedup (the
   spawned task from doc 209) + the qty-drift fix.

## 5. The meta-lesson
We spent the session hardening the CLOSE path (216/218/219/220) and proving selection is
variance (213-215). Tuesday validated both — the close mechanism fired, and the +31% was pure
variance (a lucky ghost). But it exposed that **the ENTRY fill-tracking path (D218 qty-drift)
is the next structural hole**, and it's the one that actually drove today's chaos. The honest
read: **don't celebrate the +31%; bank the lesson that our biggest paper "win" came from a bug,
and go fix the bug that caused it.**

## Appendix — evidence
- Broker: equity $193,336 (+30%), realized −$3,950, LASE 26,772 open +$50,921.
- `data/reports/eod_2026-06-02.json` (broker_total_pnl=-3950, qty_drift=2, 3 disagreements).
- `logs/momentum_2026-06-02.log`: D218 QTY_DRIFT @11:05:31 (+10,680); D231 ×1985; D313 ×839;
  D242 force-close @16:00:38; D222 divergence (LASE journal +929 vs broker -896).
- This doc + changelog.
