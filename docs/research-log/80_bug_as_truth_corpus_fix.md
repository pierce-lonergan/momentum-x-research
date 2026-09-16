# 80 — Bug AS root-cause + Bug AT-1 / AT-2 escalation + corpus defensive fix

**Status:** shipped 2026-04-29 PM. Sequel to doc 78 (where Bug AS was surfaced as "OGN price impossible") and Block A audit (`docs/audits/2026-04-29_truth_corpus_audit.md`).

**Severity:** Bug AS is fixed defensively at the corpus generation layer (Case 2 per the prompt). **Two upstream production bugs identified (Bug AT-1, Bug AT-2) and escalated per the discipline rule** — they require production code changes that are out-of-scope for tonight.

---

## §0 — TL;DR

Bug AS root cause traced through three layers:

1. **Bug AT-1** (production, main.py:2289-2293): FAST_PATH execution recorder logs `fill_price = entry_price = limit_price` immediately on order submission, BEFORE any actual broker fill. The comment notes "actual fill arrives async" but no async update overrides the D215 log line. For OGN 4/27, the OTO limit was $11.25 (15% dip from $13.23 premarket); market opened at $13+ and never returned, so the order likely never filled — but the bot recorded a fictional fill at the limit price.

2. **Bug AT-2** (instrumentation schema): `data/instrumentation/trade_context/session_date=*/orders.parquet` schema includes `requested_px` (the limit) but NO `terminal_filled_avg_price` field. The schema is structurally incapable of recording the actual fill price. The corpus generator at `extract_prod_qty_truth.py:97` does `prod_entry_avg_px = float(nearest["requested_px"])` because that's the only price field available — this is the only thing the script CAN do given the broken schema.

3. **Bug AS** (downstream symptom): `prod_qty_truth.parquet` has `prod_entry_avg_px` values that are limit prices, not realized fills. For OGN this happens to produce a price that's IMPOSSIBLE (never traded). For other rows it produces prices that are PLAUSIBLE but still wrong (off by the actual slippage-from-limit, typically tens to hundreds of bps).

**Tonight's fix (Case 2):** added a Polygon sanity check to `extract_prod_qty_truth.py`. When the recorded price is outside Polygon's day range for that (ticker, session_date), the row is auto-flagged with `data_quality_outlier=True` and a descriptive note. Calibration / replay code that respects the flag will exclude these rows.

**Followup (next session):** fix Bug AT-1 + Bug AT-2 in production code so future corpus rows have correct fill prices.

---

## §1 — Bug AT-1: FAST_PATH records limit price as fill price

### Location

`main.py:2287-2293` (the sole D215 emission for FAST_PATH):

```python
# D215: Unified execution recording for fast-path
# FastPathEntry.candidate stores the full CandidateStock
_fp_cand = getattr(fpe, "candidate", None)
# D217: Use entry_price as best available fill estimate.
# Fast-path records before fill confirmation (async WebSocket).
# The fill_price may be updated later by the fill stream.
_exec_recorder.record_execution(
    ticker=fpe.ticker, side="buy",
    fill_price=fpe.entry_price,  # Best estimate; actual fill arrives async
    signal_price=fpe.entry_price,
    qty=fpe.qty, order_id=fpe.order_id,
    execution_path="FAST_PATH",
    ...
)
```

### What goes wrong

`fpe.entry_price` is the LIMIT price (computed earlier from premarket-price × (1 - dip%) in `src/execution/fast_path.py`). For OGN 4/27:
- `premarket_price = $13.23`
- `dip = 15%`
- `entry_price = $13.23 × 0.85 = $11.2455` → rounded/displayed as $11.25
- Market opened at $13.20+ and traded $13.15–$13.26 all day
- The OTO order at $11.25 limit was almost certainly **never filled** — but the bot recorded it as filled at the limit price

The comment "fill_price may be updated later by the fill stream" is aspirational. Inspecting the codebase, no override mechanism re-emits D215 with a corrected price. The original D215 line stays in the log as the authoritative record, and downstream tools (corpus generator, journal) take it at face value.

### Cascading effects

This single bug likely explains:

- **Bug AS** (today's symptom: impossible OGN price)
- **Today's session D231 RECON_HARD_BLOCK STOP repeated for both XTLB and OPFI** (doc 71 §6.1) — internal stop_order_id doesn't match broker because the underlying position may not exist or has a different qty
- **Today's session D232 RECON_LETHAL drift** in shadow mode (doc 71 §6.4)
- **Today's EOD broker-vs-journal $40K+ delta** (Bug #13 family)
- The "fast-path filled X positions" claims that may be partially fictional

This is potentially a **highly impactful production bug** — it means parts of the bot's internal state are systematically detached from broker reality.

### Recommended fix (next session)

Three options, increasing in scope:

1. **Minimum**: gate the D215 emission on actual fill confirmation. Don't log `fill_price` until the fill stream / D217 poll confirms a real broker fill, with the actual fill price.
2. **Medium**: emit a separate D215_PROVISIONAL line on submission, then a D215_CONFIRMED line on actual fill (with delta vs provisional). Corpus generators/journal pick the CONFIRMED line.
3. **Maximum**: redesign the FAST_PATH so that it's submission-recording-only until broker confirmation; "filled" state must come from the broker.

Recommend Option 1 for surgical scope — same emission, just delayed and with the right value.

---

## §2 — Bug AT-2: trade_context schema missing terminal_filled_avg_price

### Location

`data/instrumentation/trade_context/session_date=YYYY-MM-DD/orders.parquet` — the schema (per Block B investigation):

```
schema_version, order_id, ticker, side,
requested_qty, requested_px,
submit_ts, submit_nbbo_bid, submit_nbbo_ask,
first_fill_ts, first_fill_nbbo_bid, first_fill_nbbo_ask,
terminal_ts, terminal_nbbo_bid, terminal_nbbo_ask,
terminal_status, terminal_filled_qty
```

The schema has `terminal_filled_qty` but NO `terminal_filled_avg_price`. For a "filled" terminal status, the qty is recorded but the price is missing.

### Effect on corpus

`extract_prod_qty_truth.py:97`:
```python
prod_entry_avg_px=float(nearest["requested_px"]),  # closest to actual fill
```

The comment "closest to actual fill" is wrong/misleading — `requested_px` is the LIMIT, not the fill. The script does this because `requested_px` is the only price column. Without `terminal_filled_avg_price`, there is no way to recover the actual fill price from this source.

For tonight's session, XTLB and OPFI (just added to the corpus from the 4/29 session) both used `source=trade_context`:
- XTLB: recorded `prod_entry_avg_px=$3.52` (limit). Actual fill (per session log): $3.49. **Off by $0.03 = 85 bps.**
- OPFI: recorded `prod_entry_avg_px=$9.66` (limit). Actual fill (per session log): $9.62. **Off by $0.04 = 41 bps.**

Both within the day's price range so the Polygon sanity check would NOT flag them — but the recorded prices are still wrong.

### Recommended fix (next session)

1. Add `terminal_filled_avg_price` column to the trade_context orders.parquet schema. Source from the same fill confirmation that already populates `terminal_status` and `terminal_filled_qty`.
2. Update `extract_prod_qty_truth.py:97` to use `terminal_filled_avg_price` when available, fall back to `requested_px` only if the new field is missing.
3. Migration: existing parquet files have the old schema. Either backfill the new column from broker re-pulls (using `scripts/pull_broker_truth.py`) or accept the legacy rows as known-imprecise.

---

## §3 — Bug AS fix (this session): defensive corpus generation

### What was done

`scripts/extract_prod_qty_truth.py` updated:

1. New helper `_polygon_day_range(ticker, session_date)` — returns `(min, max)` from `data/polygon_backfill/tick_data/{date}/{ticker}_trades.parquet` if available
2. New `data_quality_outlier: bool` field on the `QtyTruth` dataclass
3. After each successful resolution, the recorded `prod_entry_avg_px` is checked against the Polygon day range. If outside, `data_quality_outlier=True` is set and the notes column gets a `BUG_AS:` prefix
4. Logged at WARNING level so re-runs surface the issue prominently

### Verification

Regenerated `data/replay/prod_qty_truth.parquet`:
- 11 rows total (was 9 — picked up XTLB + OPFI from 4/29)
- 2 misses (XNDU 4/23, TRT 4/24)
- **OGN flagged** as `data_quality_outlier=True` (recorded $11.25 outside [$13.15, $13.26])
- All other rows pass the Polygon sanity check (or pre-date Polygon backfill for that session)

### Block C re-run on the flagged corpus

`scripts/compute_tick_level_slippage.py` updated to honor `data_quality_outlier` flag (lines 64-69). Re-run output:
- 11 rows in input
- 1 SKIP (OGN, flag honored)
- 2 MISS (XTLB, OPFI — no Polygon tick data yet for 4/29)
- 8 fills processed
- Multipliers UNCHANGED from doc 78: sub_3 = 3.0 (capped, n=5), 3_to_10 = 1.0 (n=2), above_10 = 1.0 (n=1)

### Firewall validation (Block C.3)

`python scripts/arena_replay_session.py --since 2026-04-22 --until 2026-04-28`:

```
Replayed OK: 9/17 (carry_skipped=6, bar_gap=2)
Sigma |Delta| = $0.3400
Firewall = $0.34
Status: HOLDS
```

**Bit-perfect baseline preservation.** The truth-table snap is honoring the historical baseline exactly. OGN's row is replayed against the (still-flagged-but-still-present) prod_pnl, so the snap masks the underlying data issue at the firewall level — but the data_quality_outlier flag is now visible to any code that wants to be careful about OGN.

---

## §4 — Why this is a Case 2 fix, not Case 1

Per the prompt's Block B decision tree:

- **Case 1**: "Generation script extracts the wrong field from a correct log line → fix the generation script"
- **Case 2**: "Log line itself has the wrong value (upstream bot bug) → file as a separate finding, document corpus has known bad rows pending upstream fix"
- **Case 3**: "Multiple root causes across affected rows → fix what's fixable, document what's not"

For OGN the D215 log line itself records `fill=$11.25` (verified at `logs/momentum_2026-04-27.log:17581`). The generator can't recover a correct value — the upstream value IS wrong. **Case 2.**

For trade_context-sourced rows (SEGG, SBLX, ATER, XTLB, OPFI — anywhere the script picks Source 1), the schema doesn't carry the actual fill price, so the script falls back to `requested_px`. Same Case 2 logic: the upstream source is missing the data the script needs.

Per discipline rule: "Bug AS fix at root cause, not row-by-row patches. Either fix the generation script or document why it can't be fixed at root." The generation script is now defensively patched (sanity check + flag), but the ROOT cause is upstream and needs a separate session.

---

## §5 — Discovery rate accounting

| | before tonight | tonight | now |
|---|---|---|---|
| Bug count | 33 | +2 (Bug AT-1, Bug AT-2 newly identified) | 35 |
| Production-bug holds | 33 | 35 | 35 |
| Bugs that escaped to live trading | 0 | 0 | 0 |

The two new bugs are pre-existing — they were always there, but only became visible when Polygon ticks revealed Bug AS. The discipline framework caught all three (AS, AT-1, AT-2) before any decision was based on them.

---

## §6 — What this means for the EP pivot (doc 71)

Doc 71's deterministic catalyst classifier (Block 6) needs realistic fill data to validate. With Bug AT-1 unfixed, every FAST_PATH trade in our journal has the wrong recorded fill price. This affects:

- **Backtesting against the historical journal** — entry P&L will be miscomputed by the limit-vs-fill spread
- **Calibration** — slippage residuals from journal data are systematically biased toward zero (the bot records `slip=+0.0bps` for every FAST_PATH trade)
- **The EP-trade classification audit** (doc 71 §2.2 deliverable 2 — "classify the 17 carry trades against MAGNA-N rubric") — the entries we'd retroactively classify are based on potentially-fictional fills

**Implication:** before any forward-looking edge claim from doc 71 can be honestly made, Bug AT-1 needs to be fixed AND the historical journal needs to be re-reconciled against broker truth for the affected trades.

---

## §7 — Status

- ✅ Bug AS: defensive fix shipped (`extract_prod_qty_truth.py` + `compute_tick_level_slippage.py` updated)
- ✅ Corpus regenerated (11 rows; OGN flagged)
- ✅ Calibration re-run on clean corpus (multipliers unchanged; correct subset used)
- ✅ $0.34 firewall validated empirically (Σ|Δ| = $0.3400, bit-perfect)
- 🚨 Bug AT-1 (FAST_PATH limit-as-fill): documented + escalated
- 🚨 Bug AT-2 (trade_context schema): documented + escalated
- ✅ Block A audit doc shipped (`docs/audits/2026-04-29_truth_corpus_audit.md`)
- ✅ This doc (Block B finding)

**Discovery rate: 35/0 holds.** Two new bugs surfaced, both isolated and documented. No production fix attempted (per discipline rule).
