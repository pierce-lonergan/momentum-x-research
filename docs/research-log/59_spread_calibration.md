# 59 — Block 2.1: Spread calibration framework + first-fit attempt (REVERTED per stop condition)

**Status:** framework shipped 2026-04-28 PM. **First fit attempt reverted** because it worsened aggregate divergence on exits.
**Severity:** infrastructure (no production impact). The calibration framework is now in place; the FIT is gated on more replay data.
**Surface:** Block 1 diff report identified `fill_price` as the dominant divergence source ($3,374 |Δ| across 4 trades). Block 2.1 brief: fit empirical slippage distribution per tier.

---

## §0 — TL;DR

Two deliverables, two outcomes:

1. **Framework: SHIPPED.**
   - `mx-arena/arena/spread_model.py`: `SpreadModel.__init__` now accepts `calibration` dict keyed by tier (`sub_1`, `sub_3`, `sub_10`, `sub_50`, `above_50`). Tier multipliers scale the half-spread.
   - `scripts/calibrate_spread.py`: reads replay parquet, computes per-tier median |Δ| in bps, writes JSON calibration to `mx-arena/arena/calibration/spread_v0.1.json`.
   - `scripts/arena_replay_session.py`: auto-loads calibration JSON if present.

2. **First fit: REVERTED.**
   - With n=4 replayable trades and one extreme outlier (ATER -508 bps entry), median-based per-tier calibration produced multipliers (`sub_3: 4.24×`, `sub_10: 8.75×`) that brought entry-side fills closer to prod but pushed exit-side fills FURTHER from prod.
   - Aggregate |Δ_bps| across 8 fill points: **772 → 817** (worse by 6%).
   - Per Block 2 stop condition: "If a fix INCREASES divergence on other trades (the fix overshoots), revert and document as a calibration question. Do not paper over with hand-tuned constants."
   - Calibration JSON reverted to identity (all 1.0). Arena behaves identically to pre-Block-2.1 until a better fit lands.

---

## §1 — What the data showed

Pre-calibration (Block 1 first run), per-trade fill-deltas:

| Ticker | Price | Tier | Entry Δ (bps) | Exit Δ (bps) |
|---|---:|---|---:|---:|
| LIDR | $2.47 | sub_3 | -39.8 | -22.6 |
| OGN | $13.22 | sub_50 | +2.1 | -7.4 |
| SBLX | $3.07 | sub_10 | -59.3 | -45.7 |
| ATER | $1.44 | sub_3 | **-507.6** | -87.5 |

Pattern: arena under-models slippage at lower price tiers. ATER is an extreme outlier — a -508 bps fill on a $1.44 stock is $0.073 of slippage. Either (a) ATER had a genuine outlier fill (illiquid + opening-minute-class spread), or (b) prod's actual fill timestamp was off the bar's open by enough that the bar-anchored comparison overstates slippage.

Post-calibration deltas (with the v0.1 multipliers `sub_3:4.24, sub_10:8.75, sub_50:1.89`):

| Ticker | Entry Δ (bps) | Exit Δ (bps) |
|---|---:|---:|
| LIDR | +29.2 | -69.0 |
| OGN | +7.5 | -10.5 |
| SBLX | -15.3 | -89.5 |
| ATER | -463.2 | -133.1 |

The pattern after calibration: entries got CLOSER to tolerance (LIDR went from -40 to +29, SBLX from -59 to -15), but **exits got worse** (LIDR went from -23 to -69, SBLX from -46 to -90).

---

## §2 — Why it overshot

The calibration is symmetric per tier (one multiplier scales both sides), but production's slippage was asymmetric:
- **Entries pay near the ASK** (long buys lift the offer), with extra cost in opening minutes when imbalances are largest.
- **Exits hit near the BID** but tend to be more controlled — limits, BAR-1 timed exits, smart-exit routing.

So prod's entry-side slippage is meaningfully larger than its exit-side slippage. Symmetric calibration optimized for the entry-side bias overshoots the exit-side.

A correct fit needs:
1. **Per-side multipliers**: `{sub_3: {entry: 2.5, exit: 1.5}, ...}` or equivalent.
2. **Outlier handling**: ATER's -508 bps entry pulled the sub_3 median from a more-typical -50 bps to -64 bps. With n=4 we have no robust trim. Need n ≥ 20 per tier to use a winsorized fit.
3. **Time-of-day stratification**: spreads are 2.5× wider in the first 15 min. Several of the entries are pre-09:45 ET. The base SpreadModel already has a TOD multiplier; the calibration should fit ON TOP of TOD, not replace it.

---

## §3 — Why this is OK to ship as "framework only"

The brief explicitly anticipates this:

> "Stop condition: if a parity fix INCREASES divergence on other trades (the fix overshoots), revert and document as a calibration question. Do not paper over with hand-tuned constants."

We followed the discipline. The framework (spread_model + calibrate_spread + arena_replay auto-load) is in place; the FIT is honest: identity multipliers, with a roadmap for richer fits when more data arrives.

Future calibration runs should:
- Wait for ≥20 fills per tier before fitting.
- Use per-side (entry vs exit) multipliers.
- Trim outliers (winsorize at p10/p90) before computing the median.
- Test the fit BEFORE writing the JSON: re-run replay with the candidate calibration, accept only if aggregate |Δ| strictly decreases.

The framework supports all of this. The current data does not.

---

## §4 — Test discipline preserved

The fitting script does NOT auto-write under failure conditions; it requires explicit invocation. The reverted calibration is a JSON file with all-1.0 tier multipliers, transparently labeled "REVERTED — see this doc."

Discovery rate: this is NOT a new bug — it's an expected outcome of insufficient data for a parametric fit. **30/0 ratio holds.** The discipline of stopping on regression IS the prevention of "fixed it with constants" silent debt.

---

## §5 — What unblocks the next fit

1. **Bar-recording coverage gap closure** (Block 2 separate finding): production trades on a wider universe than what we capture. Closing this gap lets future replays cover the 5 currently-`missing_bars` trades.
2. **Bug AO orchestrator hook** (P1, deferred): persist `entry_price` and `qty` into trade_results.jsonl. This eliminates the qty-reconstruction approximation and makes per-trade $-deltas honest.
3. **Time-window expansion**: replay 86 days of bars (Block 4.4) once Block 4.3 (partial-fill races) is in. With ~2 fills per session × 30 sessions, n=60 per tier is plausible — enough for a winsorized fit.

---

## §6 — Block 2.1 status: COMPLETE-AS-FRAMEWORK

- ✅ `SpreadModel.calibration` parameter wired
- ✅ `scripts/calibrate_spread.py` written and tested against replay parquet
- ✅ `scripts/arena_replay_session.py` auto-loads calibration if present
- ✅ First fit attempted, **rejected by stop condition**, reverted
- ✅ Finding documented (this doc)

**Next:** Block 2.2 (FailureInjector — Bug AR scenario reproduction) is higher-leverage than re-attempting the fit with current data. Move to it.
