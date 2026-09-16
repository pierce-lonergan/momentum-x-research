# 229 — Close-path phantom sweep (2 CRITICAL) + the multi-day-hold data-gap analysis

**Author**: Claude Opus 4.8
**Date**: 2026-06-02 (Tuesday, post-close)
**Mandate**: Pierce — "do a comprehensive sweep for bugs. then I want to back fill as much data
as possible… many times when we fail to close the next day the stock unsuspectingly skyrockets.
I want to run an experiment for holding stocks for multiple days, 5 days max. Identify gaps in
the data — what is the current data NOT telling us? Can we close the gaps using the premium
services we have?"

Two halves: **(A)** the bug sweep found two CRITICAL phantom-P&L paths the entire 216-228 arc
missed; **(B)** the multi-day-hold question — answered with a clean dataset that *overturns the
hypothesis* and exposes a data-integrity trap.

---

## PART A — the comprehensive bug sweep (an adversarial agent + line-by-line verification)

The D227/228 merge sweep was extended to the whole close path. An adversarial agent surfaced 6
candidates; I verified each against the real code (it mis-claimed one — see #3). Net: **2 CRITICAL
phantom-P&L paths, 1 HIGH observability, 2 MED/LOW**, all fixed.

### CRITICAL #1 — shutdown close booked phantom P&L with ZERO broker confirmation `main.py:7787`
The post-loop shutdown handler did `close_with_attribution(exit_price=MTM)` for every open
position — **no `client.close_position()`, no status check** — then removed it from the tracker
*and persisted state*. On the normal overnight-carry path (Phase 4 `_skip_close` deliberately
leaves positions open at 16:00, logs "will carry overnight"), the shutdown loop then **booked
unrealized MTM as realized**, poisoning the BOCPD/Kelly corpus AND orphaning the still-open broker
position (next session loses its stop/targets/opened_at). This is the *exact* APPS-5/28 / LFS-5/27
phantom class — surviving on the shutdown path because the arc only hardened D76/D242/Phase-A.
- **Fix**: gate on a **market-open check** (cancelling protective stops while the market is closed
  would leave the carry *naked overnight* — worse than the bug), route the close through
  `attempt_close_with_status_check(cancel_blocking_stops_first=True)`, and **book + untrack ONLY on
  a confirmed broker close**. When closed/failed, the position carries fully intact (tracked +
  persisted, stops untouched) for next-session D91.

### CRITICAL #2 — Phase 4 EOD fallback booked "regardless" `main.py:7149`
`broker_closed` was computed (False on a 403/timeout) but the booking comment literally read
*"Record internal close regardless (so we don't lose tracking)"* and called
`close_with_attribution` unconditionally + removed tracking. On a 403 (a protective stop
re-reserved the qty) it booked fake ~$0 P&L and dropped tracking → untracked overnight carry +
corpus poisoning. The sibling **D76 path was hardened** (`if not _d76_close_ok: continue`); Phase 4
was not.
- **Fix**: mirror the D76 guard — `if not broker_closed: log + leave tracked for D242/D91; continue`.
  Only book on a confirmed broker close.

### HIGH #3 — `close_with_attribution` NameError silently killed close-Discord + decision-learning `bridge.py:1684`
`exit_reason` was referenced inside the post-close Discord (`post_trade_close`) and
decision-learning (`post_trade_close_enriched`) blocks but **was never a parameter** → NameError on
*every* close, swallowed by the surrounding bare `except` → both have been **silently dead since
D218**. (The agent's claim that `_watchlist_webhook_url` is "never set" was **wrong** — `main.py:721`
sets it; the webhook is wired. The NameError was the real, always-fires bug.) P&L booking,
upstream of these blocks, was unaffected — pure observability loss masked as "Discord is quiet."
- **Fix**: add `exit_reason: str = "unknown"` to the signature (restores *both* channels);
  defensive `self._watchlist_webhook_url = None` in `__init__` for non-main construction paths.

### MED #4 — the D227 merge left stale targets/stop after the weighted-avg entry `position_manager.py`
A consequence of *my own* D227 merge: a runner re-bought **higher** gets a blended entry above
buy#1's absolute `target_prices`, so the freshly-armed exit ladder
(`compute_exit_tranches` reads stored `target_prices`) would place limit **sells below cost basis**.
`tranches_filled` also stayed stale → premature stop-ratchet on the larger merged qty.
- **Fix**: on merge, **rescale `target_prices` + `stop_loss` by the entry ratio** (preserves the
  %-offset risk/reward geometry; ratio>1 on a runner → *tighter*, never looser for a long) and reset
  `tranches_filled = 0` (re-laddered fresh on the fill). Pinned by
  `test_merge_rescales_targets_and_stop_and_resets_tranches_doc229`.

### LOW #5 — stop registration sized off single fill `post_fill_handler.py:288`
doc-228 fixed the exit ladder to `pos_obj.remaining_qty` but the `stop_resubmitter.register_stop`
50 lines later still passed `order.qty`. Mitigated by a drain-time re-fetch (hence LOW), fixed for
consistency.

**Categories verified CLEAN (not assumed):** every `add_position` consumer uses the canonical
object (D228 holds); the REPLACE path installs a clean position; D76 final-pass gating is correct;
single-threaded asyncio means no torn-read concurrency on `add_position`.

**Tests**: `test_position_merge.py` now 9/9 (added the #4 rescale). Targeted execution + crash-
recovery suites green. The 10 `test_pipeline_guards.py` failures are **pre-existing** (proven: identical
10-fail/18-pass at clean HEAD with edits stashed — a `settings.scoring` MagicMock fixture break from
doc 178, unrelated). **Open follow-up**: the two CRITICAL paths live inline in `main.py`'s loop and
have **no direct regression test** (the guards mirror the tested D76 pattern + are documented, but a
main-loop harness or an Adversary `shutdown_market_closed_no_phantom_book` scenario should be added).

---

## PART B — the multi-day-hold data-gap analysis

> Hypothesis: "many times when we fail to close the next day the stock unsuspectingly skyrockets."

### B.0 — What we have (inventory)
| Source | What | Coverage |
|---|---|---|
| `data/journals/journal_*.jsonl` | every **evaluated candidate** (ticker, date, eval price, MFCS, gap, RVOL) | 49 files, since 2/10, 100s/day |
| `data/trade_results.jsonl` | actual trades (pnl, is_win, entry/exit **time**) | 53 rows, 39 tickers, 18 sessions |
| `data/polygon_warehouse/day_aggs` | daily OHLCV (the forward backbone) | **16,000 tickers, 2024-2026**, RAW |
| `…/minute_aggs` | full-universe minute bars (intraday-exit modeling) | 30 days |
| `…/reference/splits.parquet` | split calendar | 2,914 splits, **max 5/18** |
| Premium: Polygon API + S3 flat-files, Finnhub, TabPFN, Alpaca SIP | — | available |

### B.1 — The empirical answer (and the trap)
Built `scripts/build_multiday_hold_dataset.py`: every evaluated candidate joined to **split-adjusted**
forward 1-5-day bars, anchored to the **signal-day close** (the price we "failed to sell at").
**788 entries, 40 sessions.**

**First run looked bullish — then I red-teamed it.** The extreme tail was ASBP (+4,023%), WGRX
(+3,195%) … all sub-$1 names. Inspection: ASBP 5/8 close $0.185 (vol 31M) → 5/11 **open $5.50** (vol
**249K**). A 30× jump with volume cratering 99% = a **reverse split** — *not in splits.parquet* (stale
at 5/18; ASBP's last recorded split is 1/16). The "skyrockets" were **uncaptured-split phantoms.**

Added a volume-signature guard (real squeezes *expand* volume; splits *crater* it: >2.5× price jump
+ <0.25× volume + no recorded split → flag & exclude). Excluding just **4 of 788** rows:

| | d1 | d2 | d3 | d5 |
|---|---|---|---|---|
| median close-ret | −1.4% | −1.2% | −2.5% | **−4.8%** |
| mean (raw, contaminated) | +1.3% | +5.3% | +9.8% | **+12.0%** |
| **mean (clean)** | +1.3% | +1.1% | +2.2% | **+1.2%** |
| % < −10% | 21.9% | 29.2% | 32.9% | **39.7%** |

**4 contaminated rows moved the d5 mean 10× (+1.2% → +12.0%).** The entire "hold and it skyrockets"
impression was ~90% reverse-split data artifacts + availability bias (we remember LASE +143%, STG
−29% is forgotten).

**Clean verdict on the hypothesis: FALSE as stated.** Holding to the next close is a *losing* policy
on the median, worsening with horizon (−1.4% → −4.8%); the mean is ~flat (+1-2%). Holding longer buys
**variance, not expectancy** (downside tail 22% → 40%). This is the same convergent truth the stress
tests keep producing: selection/holding ≠ edge; **timing + execution** are the durable levers.

**BUT the real opportunity is in the PATH, not the endpoint:** median **peak** (perfect trail) is
**+11.6% by d5** (p75 +31%, max +1,171% — a *real*, volume-expanding squeeze) vs median close −4.8%.
The ~16% gap is round-tripped gains. **Multi-day holding is only viable with a peak-capture trailing
exit — never hold-to-close.**

MFCS>0.55 is the only positive-median bucket (+1.7%, P(up)=54%) but **n=26, CI[35,71]** — inconclusive
(MFCS edge evaporates under CI, as always).

### B.2 — What the current data is NOT telling us (the gaps)
1. **Unadjusted bars + an incomplete/stale split calendar (CRITICAL).** day_aggs is RAW; splits.parquet
   maxes 5/18 and *misses splits entirely* (ASBP 5/11, WGRX ~5/23). Manufactures phantom skyrockets.
   → **Close it:** re-pull `scripts/polygon_splits_backfill.py` to today + use the volume-signature guard.
2. **Staleness / T+1 lag.** day_aggs maxes 6/1; the LASE 6/2 carry that *motivated this* isn't in the
   warehouse yet. → `scripts/polygon_flatfile_pull.py` daily; Polygon API for the last 1-2 sessions.
3. **No entry/exit price or qty in `trade_results.jsonl`** — only pnl/times. Can't reconstruct % return.
   → join eval-journal price + day_aggs (done), or pull Alpaca fills (`scripts/pnl_breakdown.py`).
4. **Small n + contamination** (53 real trades). → score the full evaluation corpus (done: n=784).
5. **Selection bias in the carry set** — "failed to close" is *not random*: it's where the close 403'd
   (qty-drift, multi-buy runners, stop reservations). "Carry → skyrocket" is likely **reverse causation**
   (we carry *because* it ran + qty-drifted), not a tradeable signal. The runners we *do* exit (APPS)
   we exited profitably. → tag carry-reason from `eod_failsafes` (bug-403 vs unfilled vs intentional).
6. **No corporate-action / dilution / halt feed (deepest real gap for LIVE multi-day).** Small-cap
   runners die on ATM/S-1/424B dilution and T12 halts — a close-to-close hold treats a halted/diluted
   stock as freely tradeable. → **Finnhub** filings/offering calendar; Polygon SIP halt condition codes.
7. **Liquidity / tradeable-at-size unknown.** The surviving tail is sub-$1 penny names where a +1000%
   "win" might fill $500. → use minute_aggs $-volume + spread to gate the experiment to tradeable names.

### B.3 — Deliverable state
`data/research/multiday_hold_dataset.parquet` (784 clean entries × forward 1-5d split-adjusted
OHLC + close/high/low returns + run-high "perfect-trail" + per-day split-adj factor + open-gap +
`suspected_uncaptured_split` flag). Reproducible via `scripts/build_multiday_hold_dataset.py
--horizon 5 [--min-mfcs X]`. **This is the state to explore multi-day holds** — and the immediate
next experiment is to simulate a **trailing-stop multi-day exit** (peak-capture) over it, since
hold-to-close is proven negative.

---

## Decisions deferred to Pierce (live-behavior, not mechanical)
- Whether to **enable a multi-day hold at all** — and if so, gated to **MFCS>0.55 + a trailing-stop
  exit + a liquidity/dilution filter** (hold-to-close is a *losing* policy; only peak-capture has edge).
- Re-pulling splits + wiring a Finnhub dilution/halt feed are *backfill* (mechanical) — but acting on
  multi-day holds in live trading is your call.

**Initiator**: Claude Opus 4.8 (1M ctx). **Found-by**: adversarial bug-sweep agent (Part A) + a
self-red-team of the dataset tail (Part B, the split-phantom catch). **Predecessor**: 227/228
(the merge this sweep extends), 213-215 (the no-cap+CI stress-test methodology this reuses).
