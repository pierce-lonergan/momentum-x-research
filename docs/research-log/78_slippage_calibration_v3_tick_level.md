# 78 — Tick-level slippage calibration v3 + Bug AS (truth-corpus data quality)

**Status:** shipped 2026-04-29 PM. Replaces v2 identity (doc 64).
**Severity:** **calibration ships, but headline finding is Bug AS** — `data/replay/prod_qty_truth.parquet` contains an OGN record with a price (`$11.25`) that did not trade on its session date (full-day range was $13.15-$13.26 across 146,605 trades). The truth corpus is the foundation of replay/calibration; one bad record corrupts everything downstream.

---

## §0 — TL;DR

With Polygon NBBO ticks (Block B.3, ~6.4M ticks across 9 prod fills × quotes+trades), we can finally separate the components doc 64 documented as "structurally indistinguishable":

- ✅ Pure intra-bar drift — now measurable; not slippage
- ✅ Limit-vs-bar-open structural mismatch — already fixed in doc 66
- ✅ **Real broker slippage vs NBBO mid** — finally clean

**Tick-clean residuals (excluding 1 data-quality outlier, n=8):**

| Stat | bps |
|---|---|
| Median | +22.9 |
| Mean | +28.0 |
| Median absolute | 51.9 |
| p25 / p75 | -27.0 / +47.8 |
| Min / Max | -168.6 / +320.0 |

This is a **dramatic** reduction from doc 64's contaminated residuals (which spanned 100-1500 bps because intra-bar drift + limit-vs-bar-open were folded in). The doc 64 finding is closed: **the prod fill quality vs NBBO mid is reasonable** — typical adverse selection of 50 bps median absolute on small-caps, with positive median (paid up by ~20 bps on average) consistent with liquidity-taking buys.

Calibration v3 is shipped at `mx-arena/arena/calibration/spread_v3.json`:

| tier | n | multiplier | rationale |
|---|---|---|---|
| sub_3 | 5 | 3.00 (capped) | winsorized median \|slip\| = 61.6 bps; raw multiplier 12.3, capped at 3.0 |
| 3_to_10 | 2 | 1.00 | n<3, ship identity per doc 64 second-stop-condition |
| above_10 | 1 | 1.00 | n<3, ship identity (was 2 before excluding OGN) |
| side(buy) fallback | 8 | 3.00 | for cells with insufficient samples |

---

## §1 — Bug AS: OGN price impossible in truth corpus (HEADLINE FINDING)

`data/replay/prod_qty_truth.parquet` row 5:

| Field | Value |
|---|---|
| ticker | OGN |
| session_date | 2026-04-27 |
| entry_ts | 2026-04-27T13:30:30.078014+00:00 |
| prod_qty | 999 |
| prod_entry_avg_px | **$11.25** |
| source | d215_log |
| notes | fill_clock_et=09:30:30 |

**Polygon ground truth for OGN 2026-04-27** (146,605 trades, full day):

| Metric | Value |
|---|---|
| Lowest trade price | $13.15 |
| Highest trade price | $13.26 |
| Trades in $11.20-$11.30 range | **0** |
| NBBO mid at exact entry_ts | $13.185 |

**OGN never traded at $11.25 on 2026-04-27.** The recorded prod_px is physically impossible. Confirmed via two independent sources:
1. `/v3/quotes/OGN` — bid/ask snapshot at exact ms shows $13.18/$13.19
2. `/v3/trades/OGN` — full-day tape; minimum price is $13.15

This produces a **-1467 bps "slippage"** in the tick-level analysis (paid 14.67% BELOW mid), which is implausible and was correctly flagged as an outlier.

### What Bug AS is NOT

- Not a Polygon issue (cross-checked across both endpoints)
- Not a timing alignment issue (verified ±5s window has no trades < $13)
- Not a side-flip issue (entries are unambiguously BUYs)

### What Bug AS could be (root-cause hypotheses)

Listed by likelihood, highest first:

1. **`prod_entry_avg_px` ≠ actual fill price.** The d215_log source may record the original LIMIT price or an intermediate value, not the realized fill. Check d215_log emission code in `src/analysis/execution_recorder.py`.

2. **Wrong OGN session.** d215_log may have associated 09:30:30 with a different (earlier or later) session where OGN actually traded at $11.25. Check the journaling timestamp normalization.

3. **Order rejected → silent attribution.** OTO order placed at $11.25 limit, never filled, but recorded as filled in the journal. This would mirror the D139/D222 family of journal-vs-broker drift.

4. **Symbol confusion.** Some other ticker filled at $11.25 around 09:30 ET on 4/27, attributed to OGN by mistake. Less likely but possible.

### Fix scope (next session)

- Open `src/analysis/execution_recorder.py` and find where `prod_entry_avg_px` enters the journal. Confirm it's the post-fill realized average, not a limit/intent price.
- Cross-check the 9 corpus rows against the bot's source logs: `data/journal/2026-04-22.parquet` etc. (or the equivalent path) for the same (ticker, date, fill_clock_et) row.
- If the journal has the right value but the parquet has the wrong one, the truth-corpus generation script is the bug — find and fix.
- Re-run Block C analysis after the corpus is corrected; the multipliers may shift (especially for `above_10` which had OGN as one of two samples).

### Severity

**HIGH** — the truth corpus is the foundation of:
- Prod-mirror replay ($0.34 firewall depends on each row)
- Calibration v2 / v3 (residuals built off these prices)
- Falsification suite (any per-trade analysis)

Until Bug AS is fixed, **all tick-level slippage analysis on the OGN row is excluded** (data_quality_outlier flag). Other rows pass sanity checks (next section).

---

## §2 — Methodology

### §2.1 Tick-level residual definition

For each prod fill (ticker, session_date, entry_ts, prod_fill_px):

1. Load `data/polygon_backfill/tick_data/{date}/{ticker}_quotes.parquet`
2. Find the LAST quote whose `sip_ts_ns ≤ entry_ts_ns` (binary search via `searchsorted`)
3. Compute `nbbo_mid = (bid + ask) / 2`
4. `slippage_bps = (prod_fill_px - nbbo_mid) / nbbo_mid × 1e4`
5. Sign convention: positive = paid worse than mid (for buys, paid up; for sells, hit through)
6. All 9 truth-corpus rows are entries (BUYs)

Also computed: 5-second VWAP of trades around entry_ts as a secondary reference.

### §2.2 Sanity checks

For each fill, the script flags `structural_outlier_flag = abs(slippage) > 500 bps`. Two records flagged:

- **OGN -1468 bps** — investigated; root cause is Bug AS (truth corpus, not execution)
- **ATER +320 bps** — investigated; consistent with low-priced thin-liquidity buy. ATER's NBBO at fill was $1.245-$1.255, prod paid $1.29 = 4 cents above ask. Realistic adverse selection on a $1.25 stock.

### §2.3 Cell stratification

Per doc 64 §2 design:
- price_tier: sub_3 / 3_to_10 / above_10
- tod_quintile: q1-q5 (within 09:30-16:00 ET)
- side: only buy in current corpus

n<3 cells get identity multiplier (1.0). The cap is 3x to avoid blowing up the model on noise.

---

## §3 — Per-fill residuals (the canonical table)

| ticker | date | px | mid | bps_vs_mid | bps_vs_5s_vwap | tier | tod | flag |
|---|---|---|---|---|---|---|---|---|
| AGPU | 04-22 | $9.59 | $9.585 | +5.2 | +8.9 | 3_to_10 | q2 | clean |
| MAAS | 04-22 | $11.73 | $11.66 | +60.0 | +5.7 | above_10 | q2 | clean |
| SCNI | 04-24 | $0.856 | $0.871 | -168.6 | -172.7 | sub_3 | q1 | clean (large favorable; thin spread) |
| LIDR | 04-24 | $2.42 | $2.435 | -61.6 | -74.4 | sub_3 | q1 | clean (favorable) |
| ONMD | 04-24 | $1.15 | $1.145 | +43.7 | NaN | sub_3 | q2 | clean (no nearby trades) |
| **OGN** | **04-27** | **$11.25** | **$13.185** | **-1467.6** | **-1465.4** | **above_10** | **q1** | **DATA-QUALITY OUTLIER (Bug AS)** |
| SEGG | 04-28 | $1.140 | $1.135 | +40.5 | +9.1 | sub_3 | q1 | clean |
| SBLX | 04-28 | $3.030 | $3.035 | -15.5 | +16.4 | 3_to_10 | q2 | clean |
| ATER | 04-28 | $1.29 | $1.25 | +320.0 | NaN | sub_3 | q2 | clean (large adverse; thin) |

Output: `data/calibration/tick_level_slippage_residuals.parquet` with all 9 rows + `data_quality_outlier` boolean.

---

## §4 — Distribution shift vs doc 64

| | doc 64 (v2 contaminated) | doc 78 (v3 tick-clean) |
|---|---|---|
| residual range | 100-1500 bps | -169 to +320 bps (excl OGN) |
| naive multiplier | 18× (capped to 1.0) | 12.3× (capped to 3.0) for sub_3 |
| ship-status | identity 1.0 | tier-stratified, sub_3=3.0 |

The tick-clean residuals are roughly **5-10× tighter** than the contaminated v2 residuals. This validates doc 64's hypothesis that v2 was largely measuring intra-bar drift + structural mismatch, NOT slippage.

What's left (the ~50-100 bps median absolute on sub_3) is plausibly real broker slippage — though with n=5 in the sub_3 cell, the 95% CI is wide.

---

## §5 — Validation against $0.34 firewall

Per the prompt's Block C.4: "Σ |Δ| must remain ≤ $0.34" across 4/22-4/28 prod-mirror replay.

**Validation by construction (not by re-run tonight):** the prod-mirror replay's truth-table snap takes priority over calibration for in-corpus trades. Calibration only fires for trades NOT in `prod_qty_truth`. The 9 in-corpus trades will replay at their truth values regardless of v3 multipliers, so `Σ |Δ|` for the 9 trades is **identically zero** by snap.

For trades in the replay window NOT in the corpus (cancelled, rejected, partial fills), the v3 calibration applies. Most such non-corpus trades had no fills, so the multiplier is moot. A formal re-run is deferred to doc 79 (Block D) which will do an end-to-end `unified_bar_source` + v3 replay.

---

## §6 — Adverse-selection check on the modeled-exit path

Per Block C.5 — load 60-minute windows of NBBO post-fill for the 9 corpus trades and observe whether NBBO mid drifts adversely.

**DEFERRED to next session.** The B.3 tick data covers 09:00-16:30 UTC for each session, so the post-fill 60min windows ARE in the data. The analysis script just hasn't been written yet (time budget tonight).

The doc 64 finding "modeled-exit fidelity needs adverse-selection cost" remains open — but at least we now have the data to test it. Filed for doc 79.

---

## §7 — Calibration v3 file format

`mx-arena/arena/calibration/spread_v3.json`:

```json
{
  "meta": {
    "version": 3,
    "fit_date": "2026-04-29T...",
    "method": "median absolute slippage vs NBBO mid, excluding data_quality_outlier=True, capped at 3x",
    "n_total_residuals": 9,
    "n_excluded_data_quality": 1,
    "n_used_for_fit": 8,
    "exclusions_note": "OGN ... Bug AS filed for follow-up."
  },
  "per_tier": {
    "sub_3":   {"multiplier": 3.0, "n_samples": 5, "note": "..."},
    "3_to_10": {"multiplier": 1.0, "n_samples": 2, "note": "n<3, ship identity"},
    "above_10":{"multiplier": 1.0, "n_samples": 1, "note": "n<3, ship identity"}
  },
  "side_fallback": {
    "buy": {"multiplier": 3.0, "n_samples": 8}
  }
}
```

Backwards-compatible with v2 in shape (same `per_tier` keys). Drop-in replacement.

---

## §8 — What this enables for next session

1. **Doc 79 (arena sweep, Block D)** can wire `unified_bar_source` + spread_v3 into a clean end-to-end replay
2. **Bug AS investigation** — fix the truth corpus, then re-run Block C with corrected data
3. **Adverse-selection analysis** on the 60min post-fill windows (data is in `tick_data/`)
4. **Tighter cells** as more EP entries accumulate (need n≥3 per cell × multiple sides for proper stratification)

---

## §9 — Status

- ✅ `scripts/compute_tick_level_slippage.py` — analysis script
- ✅ `data/calibration/tick_level_slippage_residuals.parquet` — 9 residuals with data_quality_outlier flag
- ✅ `mx-arena/arena/calibration/spread_v3.json` — fitted multipliers
- ✅ This doc
- 🚨 **Bug AS filed** — truth corpus has OGN with impossible price; HIGH severity, file for next session
- ⏳ Adverse-selection on modeled exits (Block C.5) — deferred to doc 79

**Discovery rate: 33/0 holds** (Bug AS surfaced and isolated; v3 calibration ships with explicit data-quality exclusion; no existing prod-mirror replay regressed because of construction-level snap precedence).

---

## §10 — Re-fit on flag-aware corpus (added 2026-04-29 PM, follow-up session)

Sequel to docs 80 (Bug AS root-cause + corpus defensive fix) and the Block A audit (`docs/audits/2026-04-29_truth_corpus_audit.md`).

### What changed

`scripts/compute_tick_level_slippage.py` now honors the `data_quality_outlier` column on the truth corpus (added by `extract_prod_qty_truth.py`'s defensive Polygon sanity check). When the column is True, the row is skipped from the calibration fit with a `SKIP <ticker> <date>: data_quality_outlier=True (Bug AS)` log line.

### Re-fit run

Input: regenerated `prod_qty_truth.parquet` (now 11 rows, was 9 — picked up XTLB + OPFI from 4/29).

Run output:
```
Truth corpus: 11 prod fills
  SKIP OGN 2026-04-27: data_quality_outlier=True (Bug AS)
  MISS quotes: XTLB 2026-04-29
  MISS quotes: OPFI 2026-04-29

=== Tick-level residuals (8 fills) ===   ← same 8 as §3 above
=== Calibration v3 fit ===
  3_to_10:  multiplier=1.00  (n=2 < 3, ship identity)
  above_10: multiplier=1.00  (n=1 < 3, ship identity)
  sub_3:    multiplier=3.00  (n=5, winsorized median |slip|=61.6 bps)
  side(buy)_fallback: multiplier=3.00  (n=8)
```

Multipliers UNCHANGED from the original §7 fit (because OGN was already excluded as a data_quality_outlier in the original run). The change is **infrastructure**, not numbers — the corpus is now flagged at generation time, so any future calibration run on a fresh corpus will auto-honor the flag.

### Firewall validation (formal, not just by construction)

`python scripts/arena_replay_session.py --since 2026-04-22 --until 2026-04-28`:

```
Replayed OK: 9/17 (carry_skipped=6, bar_gap=2)
Sigma |Delta| = $0.3400
Firewall = $0.34
Status: HOLDS
```

**Bit-perfect baseline preservation.** Σ|Δ| = $0.3400, exactly matching the historical baseline. The truth-table snap is honoring the historical numbers for in-corpus trades regardless of the new data_quality_outlier flag (which is informational, not enforcing).

### Status

- ✅ Calibration script honors `data_quality_outlier`
- ✅ Re-fit on regenerated corpus
- ✅ Firewall validated empirically (was by-construction in §5; now bit-perfect-empirical here)
- ✅ Multipliers unchanged → spread_v3.json unchanged

**The doc 64 contamination thread is now closed at infrastructure level too**: corpus generator flags impossible rows, calibration script honors the flag, firewall holds end-to-end.
