# 245 — Cross-regime rocket money test FAILS the pre-registered gate. doc-242 tape lift was a timezone-bug artifact. The right tail is ex-ante random across regimes.

**Author**: Claude Opus 4.8
**Date**: 2026-06-03
**Mandate**: Pierce — "pull cross-regime tick history (got the disk / 4.6 TB My Passport + freed 451 GB),
re-run the doc-242 tape rocket-classifier pre-registered cross-regime; decision = traded-slice return
positive in ALL regimes, not AUC." Full send.

## TL;DR
With **correctly-windowed** tape over the **full 2024-2026 corpus (10,254 ticker-days, 202 rockets — ~3×
the doc-242 within-window sample)**, adding the TICK tape to the MICRO model **does NOT** flip the top-slice
positive across regimes. **2024 loses at every operating point; 2025 is breakeven with a negative median;
only 2026 converts.** The pre-registered gate (positive in ALL of 2024/2025/2026) **FAILS**. BET#3 (ex-ante
rocket selection from microstructure+tape) **closes as a cross-regime-robust edge.**

## Two bugs caught *before* contaminating the test (the sanity check earned its keep)
1. **Timezone bug (CRITICAL, retroactively invalidates doc 242/244's tape lift).** `trades_v1_parquet.ts_et`
   is **UTC mislabeled as ET** (the DuckDB `… AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York'` conversion
   in `polygon_trades_v1_pull.py` lands +4h/+5h off). doc242/244 read the "9:30-9:50 open" via that column —
   so they actually captured **~5:30am premarket**. Proof (AAL 2026-04-08, 9:30-9:50): stored-ts_et → 1,102
   trades; raw sip_timestamp→ET → **38,256**; Polygon `/v3/trades` API → **38,256** (match). **Fix: derive ET
   from raw `sip_timestamp`, never `ts_et`.** doc-242's claimed +0.05-0.07 AUC lift was largely this artifact
   — correctly windowed, the lift is **+0.01**.
2. **`bool(DataFrame)` truthiness.** `local_trades(tk,d) or api_trades(tk,d)` raised `ValueError` for every
   in-window candidate (non-empty frame) → silently dropped them (100% of 2024 = API worked, 30% of 2026 =
   local failed). Fixed with an explicit `is not None` check. Coverage 70% → **97.2%**.

## Method (no 1 TB flat-file pull needed)
Candidates + MICRO features + rocket label are 100% local (`exit_labels_cross_regime.parquet`, identical
gate all years: gap≥8%, open∈[$0.50,$20], ADV≥$1M; rocket = EOD-from-9:50 ≥30% AND close≥50% of run-high).
Only the early-session **tape** was fetched — premarket(04:00-09:30)+09:30-09:50 trades, in-window via fast
local sip-reads, out-window via targeted `/v3/trades` API (~4.6 ticker-days/s, 35 min, 97% coverage). **In-
and out-window go through the SAME `compute_tick_feats()`** (ET from sip_timestamp) → no method confound,
validated by an API-vs-local diagnostic (38,256 trades both). `scripts/build_xregime_tick_features_doc245.py`.

## The pre-registered money test (committed BEFORE results) — FAIL
Decision rule: MICRO+TICK wins IFF top-5% (≈1 name/day) mean EOD forward return **>0 in ALL** 2024/2025/2026,
lift>1 all, AND it **rescues** the two currently-negative regimes (2024, 2025). CPCV 8-fold day-grouped OOS.

| top-5% mean EOD (95% CI) | 2024 | 2025 | 2026 | POOL AUC |
|---|---|---|---|---|
| MICRO-only | −2.1% `[−5.0,+1.2]` | −0.5% `[−3.4,+2.8]` | +2.8% `[−1.5,+7.7]` | 0.717 |
| **MICRO+TICK** | **−4.0% `[−6.8,−1.1]`** | **−0.5% `[−3.8,+3.1]`** | +10.3% `[+0.7,+25.3]` | 0.727 |

2024 gets **worse** (lift 0.9, CI entirely negative); 2025 unchanged; only 2026 (already positive) is
amplified. Top-slice **median is negative in all 3 years**. **FAILS** — the tape does not rescue 2024/2025.

## Robustness (symmetric rigor — confirm the negative as hard as we'd disprove it)
- **Leave-one-regime-out** (train 2 years, test the fully-unseen third), MICRO+TICK top-5%: 2024 **−1.7%**
  (med −5.3%), 2025 **+1.5%** (med −3.9% → one-winner-driven), 2026 **+10.2%** (med +0.5%). 2024 still loses.
- **Slice sweep** (top 1/2/5/10/20%), MICRO+TICK: **2024 negative at EVERY slice** (−1.3% to −5.4%); 2025
  hovers at breakeven (only top-2% scrapes +1.2%); 2026 positive everywhere.
- **Fair the other way:** the tape *does* add marginal real signal — MICRO+TICK beats MICRO under LORO in
  2025 (−2.2%→+1.5%) and 2026 (+3.9%→+10.2%). It is just **insufficient** to produce a money-positive
  selector outside 2026.

## Verdict — BET#3 closes; the right tail is ex-ante random across regimes
- The ex-ante (9:50) microstructure+tape signal is **regime-dependent**: real and strong in **2026**, absent
  in **2024**, breakeven in **2025**. A deployed selector would be **betting the current regime persists** —
  exactly the over-fit trap docs 235/240 flagged. 2026 is the thinnest sample (28 rockets) and most recent.
- This closes the arc's last live alpha hypothesis the same way as the rest: **selecting this universe's
  winners ex-ante from structure/catalyst/microstructure/tape does not generalize.** The universe buys
  **variance, not expectancy**; its right tail is **largely ex-ante random across regimes**.
- doc-242's "green shoot" was substantially the **timezone-bug artifact** + a single-window result. Honest
  cross-regime, correctly windowed: **+0.01 AUC, no money.**

## Forward options (Pierce's call)
1. **Close BET#3 as a robust edge** (the pre-registered gate failed under stricter follow-ups). Recommended.
2. **The one honest exception — a forward PAPER SHADOW of the 2026/current-regime signal.** Since *now* IS
   the 2026 regime and the tape-amplified top-slice is genuinely positive there, score the live watchlist
   daily, log the top-5% rocket-slice, and **track its realized return out-of-sample in real time** (no
   capital). Framed explicitly as a **regime bet** that will silently fail if the regime shifts — the only
   honest way to learn whether "now" is tradeable. Cheap; matches Pierce's "incrementally test checkpoints."
3. **Untested levers:** true *free*-float (Finnhub) and real catalyst/news *content* (blocked: news fetch +
   Together-403 LLM tagger) — the only feature classes not yet falsified. L2/true-OFI (244) and shares-
   outstanding float (243) are already negative.
4. **Model training is moot** for now: with no cross-regime signal to amplify, a higher-capacity model would
   only fit 2026. Data (a *generalizing* signal), not capacity, remains the bottleneck — and we now have
   strong evidence the signal isn't there in these features.

## Disk / infra (Pierce's session)
Reclaimed **+450 GB** (deleted verified-redundant raw `trades_v1` CSV.gz; parquet is the faithful copy, the
one 0.003% imperfect day backed up to D:). **My Passport 4.6 TB** mounted as D: (reusable tape warehouse /
fallback). The cross-regime test used the **targeted-API route** (few GB) — the 1 TB flat-file pull proved
unnecessary for the rocket goal.

**No live change.** Everything remains research. **Basis**: `scripts/build_xregime_tick_features_doc245.py`
(10,254 ticker-days, 202 rockets, correctly-windowed tape). **Predecessors**: 241 (rockets rankable ex-ante,
top-slice loser-dominated), 242 (tape lift — now shown to be the tz-bug artifact), 244 (L2 no lift), 235
(cross-regime homogeneity). **Bug memory**: [[trades-parquet-tzbug]].
