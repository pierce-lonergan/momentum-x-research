# 64 — Block B: Slippage calibration v2 (identity ship per second-stop-condition)

**Status:** shipped 2026-04-28 PM as IDENTITY multipliers per discipline. Two structural findings logged.
**Severity:** infrastructure. Closes Block B of the falsification session.
**Trigger:** doc 59's v0.1 calibration was reverted on the symmetric per-tier overshoot. With prod-mirror entries + exits + qty truth in place, the brief asked for a per-side / per-hold-time / per-volume winsorized fit.

---

## §0 — TL;DR

Three nested stop conditions tripped during this attempt; the discipline pattern from doc 59 (revert when the fit overshoots; ship framework + identity multipliers; document the limitation) was applied at each level.

1. **n<3 cells dominate** the per-side × per-tier × per-hold-time × per-volume grid (4 of 6 side×tier cells). **Stop:** fall back to side-only fit.
2. **Side-only fit produces 18× multiplier** — 17.2× for buys, 18.9× for sells, after structural-outlier exclusion. A 18× multiplier on arena's baseline half-spread (≈7.5 bps) gives ~135 bps half-spread = $0.08 per side on a $3 stock. **Order of magnitude unrealistic.** Stop: sanity-cap at 5× → identity.
3. **Cleaned residuals (n=14) still contain intra-bar drift, not pure slippage.** Without intra-bar tick data, the bar.open vs prod-fill difference can't be separated from intraminute price movement. **Cannot ship a fitted multiplier that won't mislead.** Stop: ship identity (1.0); document the data-quality requirement.

**Final state:** `mx-arena/arena/calibration/spread_v2.json` ships with all multipliers = 1.0 ("v2-identity"). Replay validation: 9/9 trades within tolerance, Σ |Δ| = $0.34 (unchanged — prod-mirror still wins for trades in the truth corpus, which is all of them).

---

## §1 — What was attempted

`scripts/calibrate_slippage_v2.py`:
1. For each in-corpus trade, compute residuals per leg (entry buy, exit sell):
   `residual_bps = (prod_actual_px - bar.open) / bar.open × 1e4`
2. Tag with side and price tier (sub_3, sub_10, sub_50 — sub_1 and above_50 had n=0).
3. Detect structural outliers (|residual| > 500 bps) and flag.
4. Per-side winsorized median (drop top/bottom 5%; with n=7 per side that's 0 dropped).
5. Convert median to multiplier vs avg arena baseline half-spread (7.5 bps).
6. Sanity cap: if naive multiplier > 5× → identity.

The framework (residuals → JSON output → arena auto-load) IS shipped. The fit is identity.

---

## §2 — Per-side diagnostic

After excluding 4 structural outliers (OGN buy, ATER buy/sell, SCNI buy — see §3):

| Side | n_kept | median \|residual\| (bps) | naive multiplier |
|---|---:|---:|---:|
| buy | 7 | 129.32 | 17.243 |
| sell | 7 | 141.99 | 18.932 |

Side-averaged uniform multiplier (pre-cap): 18.087. **Capped to 1.0 per sanity check.**

---

## §3 — Critical finding: structural outliers are NOT slippage

Per-trade residuals revealed several |residual| > 500 bps that are categorically different from slippage:

| Trade | Side | bar.open | prod fill | Residual (bps) | Cause |
|---|---|---:|---:|---:|---|
| OGN 4/27 | buy | $13.22 | $11.25 | -1487 | FAST_PATH OTO limit at $11.25; bar opened well above limit |
| ATER 4/28 | buy | $1.44 | $1.29 | -1042 | Limit set below opening tick |
| ATER 4/28 | sell | $1.44 | $1.27 | -1176 | Same trade — exit using entry-bar approximation |
| SEGG 4/28 | buy | $1.09 | $1.14 | +417 | Limit set above opening tick |
| SCNI 4/24 | buy | $0.88 | $0.86 | -315 | Limit below opening tick |

These are **LIMIT-PRICE vs BAR-OPEN structural mismatches**, not slippage. The strategy submitted FAST_PATH OTO orders with limit prices that didn't match the bar's first observed tick. Limit orders fill intra-bar at the limit price; the bar.open is the first observed price after the open auction.

**Implication for the rig:** arena's current model uses `bar.open` as a proxy for "where prod entered." For LIMIT orders this is structurally wrong by potentially 10-15% on small-caps. A correct model needs to:
- Read the order's limit price (not just the trade's avg fill)
- Check if `bar.high >= limit >= bar.low` to determine if the bar's range included the limit
- Use the limit price as the fill if so

This is a separate finding (rig limitation: bar-anchored entry modeling vs limit-order semantics). Does NOT block tonight's other work; documented for next session's strategy-harness implementation (Block C) which will need to model limit fills properly.

---

## §4 — Cleaned-residual contamination

After excluding the 4 structural outliers, 14 residuals remain. Median |residual| 130-142 bps.

But these residuals STILL include intra-bar drift: the difference between `bar.open` (first tick of the minute) and prod's actual fill price (somewhere within that minute). Without tick-by-tick data we cannot separate:
- intra-bar price drift (price moved within the minute; not slippage)
- broker slippage (paid worse than NBBO; actual slippage)

A 130 bps "median residual" plausibly includes 50-100 bps of pure intra-bar drift on a small-cap with 30 bps natural spread. Fitting an arena multiplier of 17× to this residual would over-correct massively because the multiplier would inflate the spread (bid/ask) but the actual issue is inframinute movement (which a wider spread doesn't model).

**The honest scope for a real fit:** intra-bar tick data (Polygon or Databento, ~$80-200/mo) would let us extract pure slippage residuals. Without it, calibration cannot honestly differentiate.

---

## §5 — What the v2-identity ship preserves

Replay validation post-v2-identity calibration:
```
n_ok: 9, sigma_delta: $0.34, in_tolerance: 9/9
```

Same as before v2 (because prod-mirror snaps both legs to truth, overriding the modeled slippage entirely). The calibration only fires for non-prod-mirror trades — i.e. trades not in the truth corpus. For the in-corpus trades, identity vs 18× makes zero difference.

The framework still passes its end-to-end test. It's just not shipping a fitted fit. When intra-bar tick data lands (or when we figure out a better residual definition), v3 ships the actual numbers.

---

## §6 — How this connects to the BAR-1 falsification (doc-adjacent finding)

The falsification report (`docs/sweeps/bar1_timing_falsification.md`) identified two collapses:
- A.4 OOS test: lift collapses on Dec-Feb data
- A.5 adversarial fade: lift collapses under 50 bps/min adverse drift

A.5's collapse is exactly the kind of effect a properly-calibrated arena WOULD model. The 6.6× lift assumed long-hold exits at bar.open of the future minute — with no penalty for adverse selection on the held position. A.5 added a synthetic 50 bps/min drift and the lift went away.

**Implication:** the BAR-1 timing finding's collapse is consistent with the calibration finding (arena under-models adverse selection on long holds). They reinforce each other. Neither says "arena is wrong about everything" — they say "arena's exit modeling needs richer adverse-selection representation before forward-looking sweeps can be trusted."

---

## §7 — Status: SHIPPED FRAMEWORK + IDENTITY FIT

- ✅ `scripts/calibrate_slippage_v2.py` (residuals, side fit, sanity cap)
- ✅ `data/calibration/slippage_residuals.parquet` written for inspection (18 rows; 4 outliers flagged)
- ✅ `mx-arena/arena/calibration/spread_v2.json` ships with all multipliers = 1.0
- ✅ Replay validation: 9/9 in tolerance unchanged
- ⏭️ Real fit deferred: needs intra-bar tick data + limit-vs-bar-open separation
- ⏭️ Multi-strata fit deferred: needs n≥30 per cell (i.e. wait for 30+ session corpus or backfill more sessions)

**Discipline:** doc 59 precedent applied (revert overshoot, ship identity, document). 30/0 ratio holds.
