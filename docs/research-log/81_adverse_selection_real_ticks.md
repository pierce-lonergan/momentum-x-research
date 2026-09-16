# 81 — Adverse-selection on 60min post-fill windows (real Polygon ticks)

**Status:** shipped 2026-04-29 PM. Block D of tonight's session. Companion to doc 78 (calibration v3) and doc 80 (Bug AS / Bug AT root-cause).

**Headline:** A.5's synthetic adverse-selection stress (50 bps/min adverse after T+5min) was **13–16× too pessimistic** vs real cohort behavior. Median post-fill drift is FAVORABLE through T+15 (+264 bps) and only turns adverse at T+30+ (mean-reverts). Per-fill heterogeneity is huge (T+60 range: -504 to +2721 bps).

---

## §0 — TL;DR

For 8 prod fills (excluding OGN per Bug AS), Polygon NBBO ticks loaded for the 60 minutes following each `entry_ts`. Adverse drift = `(nbbo_mid_at_T - prod_entry_px) / prod_entry_px × 1e4`, NEGATIVE = adverse for a long position.

**Cohort medians by T:**

| T (min) | n | p25 | p50 (median) | p75 | mean |
|---|---|---|---|---|---|
| T+1 | 8 | -141 | **-39** | -20 | -76 |
| T+5 | 8 | +269 | **+302** | +387 | +302 |
| T+10 | 8 | +182 | **+262** | +431 | +283 |
| T+15 | 8 | +153 | **+264** | +340 | +229 |
| T+30 | 8 | -324 | **-74** | +200 | +263 |
| T+45 | 8 | -96 | **+210** | +383 | +490 |
| T+60 | 8 | -394 | **-214** | +368 | +232 |

(All values in bps. Negative = adverse for long.)

**Reading:** post-fill, the median trajectory snaps slightly negative at T+1 (microstructure: fill at ask, next mid is a touch back), then turns strongly favorable through T+15, then mean-reverts and oscillates around zero. Per-fill outcomes vary enormously.

---

## §1 — Methodology

`scripts/compute_adverse_selection.py`:

1. Load `data/replay/prod_qty_truth.parquet`; exclude rows where `data_quality_outlier=True` (Bug AS — OGN is excluded)
2. For each remaining (ticker, session_date, entry_ts, prod_entry_px) tuple:
   - Load Polygon NBBO quotes from `data/polygon_backfill/tick_data/{date}/{ticker}_quotes.parquet`
   - At each 1-minute increment T from T+1 to T+60: find the LAST quote with `sip_ts_ns ≤ entry_ts_ns + T·60·10⁹`
   - Compute `nbbo_mid_at_T = (bid + ask) / 2`
   - `adverse_drift_bps = (mid_at_T - prod_entry_px) / prod_entry_px × 1e4`
3. Output: `data/calibration/adverse_selection_curves.parquet` (480 rows = 8 fills × 60 increments)

Cohort: 8 fills (AGPU, MAAS, SCNI, LIDR, ONMD, SBLX, ATER, SEGG). 2 fills missing tick data (XTLB, OPFI from 4/29 — not yet pulled). 1 fill excluded (OGN, Bug AS flagged).

---

## §2 — Per-fill trajectories

| ticker | date | entry $ | T+5 | T+15 | T+30 | T+60 |
|---|---|---|---|---|---|---|
| AGPU | 04-22 | 9.59 | +292 | +83 | -16 | -83 |
| MAAS | 04-22 | 11.73 | +286 | +333 | -132 | -401 |
| SCNI | 04-24 | 0.86 | +384 | +176 | -316 | -345 |
| LIDR | 04-24 | 2.42 | +21 | +393 | +21 | **+558** |
| ONMD | 04-24 | 1.15 | +217 | +304 | +739 | +304 |
| SBLX | 04-28 | 3.03 | +313 | +362 | **+2639** | **+2721** |
| ATER | 04-28 | 1.29 | +504 | -39 | -349 | -504 |
| SEGG | 04-28 | 1.14 | +398 | +223 | -479 | -391 |

(All values in bps. Per-fill range at T+60 is -504 to +2721 — over 30× the cohort median in absolute value.)

**Pattern observations:**

- **6 of 8** fills have POSITIVE drift at T+5 (favorable for long); 1 has near-zero (LIDR +21); only 0 are negative
- **6 of 8** still positive at T+15
- **Bifurcation at T+30**: SBLX (+2639), LIDR (+21), ONMD (+739) running; AGPU, MAAS, SCNI, ATER, SEGG reversing
- **At T+60**: 4 winners (LIDR, ONMD, SBLX, MAAS… wait MAAS turned negative — actually 3 winners), 5 losers. SBLX is the outsized winner that pulls the mean above zero despite median being negative.

The classic EP shape — the moves that "work" really work, and the median trade in the early 60 minutes is just noisy.

---

## §3 — Comparison vs A.5 synthetic model

A.5 (BAR-1 timing falsification) used a 50 bps/min adverse drift starting at T+5. Implied cumulative adverse:

| T (min) | A.5 implied bps | Real cohort median bps | Ratio | Verdict |
|---|---|---|---|---|
| T+10 | -250 | +262 | -1.05 | ~~ similar magnitude, opposite sign |
| T+15 | -500 | +264 | -0.53 | ~~ similar magnitude, opposite sign |
| T+30 | -1250 | -74 | 0.06 | **<<< A.5 was 16× too pessimistic** |
| T+60 | -2750 | -214 | 0.08 | **<<< A.5 was 13× too pessimistic** |

**Reading:** A.5's "50 bps/min adverse" was an aggressive stress test designed to FALSIFY the BAR-1 timing finding. It worked — the BAR-1 lift collapsed under that stress. But the data now shows A.5 was directionally wrong on average:

- At T+5–T+15: real cohort drift is **strongly favorable** (median +200 to +300 bps), opposite of A.5's adverse direction
- At T+30–T+60: real cohort drift is small-negative (-74 to -214 bps median) — adverse, but **5–13× smaller** than A.5 stressed

**Implication for prior verdicts (informational, NOT recommending revisit):** the 86-session OOS COLLAPSES verdict on BAR-1 timing relied on A.5's adverse-fade model. With real ticks, A.5 was too harsh. This doesn't unwind the OOS verdict — there were other reasons for it (synthesized stratum carrying apparent edge, shuffle test) — but it does say A.5 was not a good falsification of the BAR-1 timing claim specifically. Filed for future falsification-suite revision.

---

## §4 — Implications for arena's modeled-exit fidelity

Arena currently models exits with bar-anchored fills + constant slippage. The data shows:

1. **Adverse-selection IS time-dependent**: median trajectory swings from +302 bps (T+5) to -214 bps (T+60). A constant slippage model can't capture this.
2. **Per-fill variance is huge** (T+60 range -504 to +2721 bps). Any deterministic adverse-selection model will be wrong on most fills.
3. **The shape suggests two phases**: favorable mean-reversion-to-favorable in early minutes (post-execution noise dampens), then random-walk noise beyond ~T+15.

For the EP pivot's Block 7 (Zarattini 5-min ORB on multi-day holds), arena needs:
- A **probabilistic** adverse-selection model (sample from a fitted distribution, not a point estimate)
- Different distributions per (price_tier, time_of_day, holding_duration_bucket)
- Acknowledgment that cohort variance is large; 8 trades is far too few for a robust fit per cell

**Recommendation:** continue arena work with the current bar-anchored fills + constant slippage as the BASELINE, and treat any extension to time-dependent adverse-selection as a per-cell empirical distribution sample (not a parametric fit) once we have ≥30 trades per cell.

---

## §5 — Implications for the EP pivot (doc 71)

**Doc 70's `t1_next_open` thesis** (positions held overnight to capture multi-day catalyst tail): the cohort median at T+60min is -214 bps. Holding past T+60 enters multi-hour overnight territory where:
- Gap risk dominates (research report §1.4 noted "positions gapping down 20% overnight blow through any reasonable stop")
- The 8-trade sample is silent on overnight; we have no NBBO ticks for after-hours / next-day-open in this analysis

**Doc 71's EP-style 3-10 day hold**: 60 minutes is a tiny fraction of the EP holding period. The cohort's ±300 bps swings within 60min are dwarfed by EP's target range (+20% to +100% over 5 days). At the EP timescale:
- Adverse-selection within the first 60min is essentially noise around the entry price
- The thesis is the **multi-day tail**, not the intraday trajectory
- A trade that hits stop within the first 60min was a wrong-thesis trade anyway

**SBLX is the most interesting datapoint**: still +2721 bps (27.2%) at T+60. If sustained for 5 days (EP holding period), this is exactly the kind of move EP targets. We don't yet know SBLX's longer trajectory in this dataset — would be a useful follow-up to extend the analysis to T+1d, T+5d.

---

## §6 — Stop-condition check

Prompt's Block D stop condition: "if the data shows adverse-selection profile is qualitatively different from anything tested before (e.g. positive drift on average, or massive variance), document prominently. Do not paper over with a one-line summary."

**Both conditions met:**
- Median is POSITIVE through T+15 (qualitatively different from A.5 which assumed adverse)
- Per-fill variance is huge (T+60 range -504 to +2721 bps; cohort SD vastly exceeds median)

This doc IS the prominent documentation. Not papering over.

---

## §7 — What this enables

1. **Honest forward-looking arena exits**: arena needs to evolve from constant-slippage bar-anchored fills to a probabilistic adverse-selection sampler. Filed for future doc.
2. **EP-pivot intra-day risk model**: the within-60min cohort trajectory is now characterized; deterministic catalyst classifier (doc 71 Block 6) can use this as a baseline for "did this trade get stopped out for a non-thesis reason in the first hour" filter.
3. **A.5 revisit (informational)**: the falsification suite's A.5 stress is too pessimistic. Future falsification work should use empirical distribution samples, not point-50-bps-per-minute heuristics.

---

## §8 — Status

- ✅ `scripts/compute_adverse_selection.py` shipped
- ✅ `data/calibration/adverse_selection_curves.parquet` (480 rows)
- ✅ Per-T quantile summary documented
- ✅ Per-fill trajectory table documented
- ✅ A.5 comparison documented
- ✅ This doc

**Discovery rate: 35/0 holds** (no new bugs surfaced; the A.5 finding is a falsification-suite quality observation, not a production bug).
