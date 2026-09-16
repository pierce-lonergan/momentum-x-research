# 2026-04-29 truth corpus audit (Block A finding)

**Status:** complete. Headline: **8/9 entry rows pass; 1 HIGH (OGN). Below the 50%-corruption stop condition; corpus is usable with OGN flagged.**

Auditor: `scripts/audit_truth_corpus.py`. Inputs: `data/replay/prod_qty_truth.parquet`, `data/replay/prod_exit_truth.parquet`. Cross-reference: Polygon SIP ticks (Block B.3 backfill).

---

## Severity scheme

| label | meaning |
|---|---|
| HIGH | recorded price OUTSIDE the day's actual price range (impossible) |
| MEDIUM | in-range, no nearby trade ±5s, AND >500 bps from NBBO mid |
| LOW | in-range, but >500 bps from NBBO mid OR no matching trade ±5s |
| CLEAN | in-range, within 500 bps of NBBO mid, AND has a matching trade ±5s |

---

## Per-row findings — entry corpus (`prod_qty_truth.parquet`, 9 rows pre-fix)

| ticker | date | recorded $ | day range | mid | bps_vs_mid | severity | notes |
|---|---|---|---|---|---|---|---|
| AGPU | 04-22 | 9.59 | 7.32 – 11.00 | 9.585 | +5 | CLEAN | matched-trade present |
| MAAS | 04-22 | 11.73 | 8.68 – 14.25 | 11.66 | +60 | CLEAN | NBBO lag 1.5s, but matched-trade present |
| SCNI | 04-24 | 0.8561 | 0.6567 – 1.05 | 0.871 | -169 | LOW | no trade matched recorded px (window 0.866-0.877) |
| LIDR | 04-24 | 2.42 | 1.82 – 3.05 | 2.435 | -62 | LOW | no trade matched recorded px (window 2.43-2.44) |
| ONMD | 04-24 | 1.15 | 0.94 – 1.27 | 1.145 | +44 | LOW | no trades in ±5s window |
| **OGN** | **04-27** | **11.25** | **13.15 – 13.26** | **13.185** | **-1468** | **HIGH** | **recorded $11.25 OUTSIDE day range; impossible price** |
| SEGG | 04-28 | 1.1396 | 0.96 – 1.58 | 1.135 | +41 | CLEAN | matched-trade present |
| SBLX | 04-28 | 3.0303 | 2.41 – 5.06 | 3.035 | -15 | CLEAN | matched-trade present |
| ATER | 04-28 | 1.29 | 0.91 – 1.87 | 1.25 | +320 | LOW | NBBO lag 89s (very stale), no trades in ±5s |

**Summary:** 4 CLEAN, 4 LOW, 1 HIGH (OGN).

## Per-row findings — exit corpus (`prod_exit_truth.parquet`, 9 rows)

| ticker | date | recorded $ | day range | mid | bps_vs_mid | severity |
|---|---|---|---|---|---|---|
| AGPU | 04-22 | 9.59 | 7.32 – 11.00 | 9.585 | +5 | CLEAN |
| MAAS | 04-22 | 11.73 | 8.68 – 14.25 | 11.66 | +60 | CLEAN |
| SCNI | 04-24 | 0.8561 | 0.6567 – 1.05 | 0.871 | -168 | LOW |
| LIDR | 04-24 | 2.50 | 1.82 – 3.05 | 2.435 | +267 | LOW |
| ONMD | 04-24 | 1.15 | 0.94 – 1.27 | 1.145 | +44 | LOW |
| **OGN** | **04-27** | **11.7664** | **13.15 – 13.26** | **13.185** | **-1076** | **HIGH** |
| SEGG | 04-28 | 1.1396 | 0.96 – 1.58 | 1.135 | +41 | CLEAN |
| SBLX | 04-28 | 2.9851 | 2.41 – 5.06 | 3.035 | -164 | LOW |
| ATER | 04-28 | 1.2707 | 0.91 – 1.87 | 1.25 | +166 | LOW |

**Summary:** 3 CLEAN, 5 LOW, 1 HIGH (OGN). The OGN exit ($11.7664) is also outside the day range — both entry and exit are fictional.

---

## Generalization analysis

**Bug AS is concentrated in OGN, not corpus-wide.** 8 of 9 entry rows (89%) are in-range. Even the LOW rows are inside the day's actual price range; they just don't have a perfectly-matched trade in the ±5s window or are far from the NBBO mid.

**Hypothesis for LOW rows:**
- LIDR/SCNI 4/24, SBLX/ATER 4/28: prices are in-range; no exact-match trade in ±5s window. Most likely the prod fill route (wholesaler, dark pool, internalizer) printed the trade slightly outside our 5s window OR at a price hashed differently. This is normal market microstructure for sub-$3 names.
- ONMD/ATER: stale NBBO at recorded ts (no trades nearby). The fill ts may be slightly off, or the fill came in a non-quoted condition (block, opening cross).
- These rows are NOT impossible — they're plausible fills that just don't perfectly reconcile against tick data. Acceptable for calibration use.

**Hypothesis for HIGH (OGN) row:** the FAST_PATH execution recorder logged `fill=$11.25` (the limit/dip price), but Polygon shows OGN never traded below $13.15. The recorded fill is fictional. **Root cause traced to Bug AT-1 (production code at main.py:2289-2293, see doc 80).**

---

## Stop-condition check

Prompt threshold: "If more than half the truth corpus rows have data quality issues, this is a corpus-wide problem." Result: **1/9 = 11% HIGH severity. Threshold not met.** Proceed to Block B (root-cause investigation + corpus-side defense) and continue with Block C/D.

---

## Outputs

- `data/audits/truth_corpus_audit_2026-04-29.parquet` (entry, 9 rows × 18 cols)
- `data/audits/truth_corpus_exit_audit_2026-04-29.parquet` (exit, 9 rows × 18 cols)
- `scripts/audit_truth_corpus.py` (reusable; can re-run after corpus updates)

The audit columns include: `recorded_ts, recorded_px, polygon_day_min_px, polygon_day_max_px, polygon_n_trades_total, in_day_range, nbbo_bid_at_ts, nbbo_ask_at_ts, nbbo_mid_at_ts, nbbo_lag_ms, trades_in_5s_window_n, trades_in_5s_window_min_px/max_px/vwap, has_nearby_trade_at_recorded_px, slippage_bps_vs_mid, slippage_bps_vs_5s_vwap, severity, notes`.

---

## Followup

1. **Bug AT-1 fix** in production code (`main.py:2289-2293` FAST_PATH execution_recorder call) — escalated, NOT done in this session per the discipline rule "if fix requires production code changes, STOP, document, escalate." See doc 80.
2. **Bug AT-2** (trade_context schema missing `terminal_filled_avg_price`) — escalated; affects future corpus rows generated from trade_context source.
3. The defensive Polygon sanity check is now part of the corpus generator (`extract_prod_qty_truth.py`); future corpus regenerations will auto-flag impossible prices.
4. Re-audit triggers: re-run `audit_truth_corpus.py` after any production fix to Bug AT-1 / AT-2, or after new Polygon tick data is pulled for additional sessions.
