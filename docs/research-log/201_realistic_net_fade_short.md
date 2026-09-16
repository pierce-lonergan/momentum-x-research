# 201 — The realistic NET fade-short: the edge survives borrow + squeeze

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "full send the realistic net fade-short backtest."

---

## 0. The verdict: YES — and realism *improves* the ELITE-bucket short

doc 200's fade-short was GROSS. The killers were borrow availability (HTB/no-shares),
squeeze gap-through, and frictions. doc 201 adds all three (`--realistic-short`):
- **ETB/shortable filter** — Alpaca `check_asset_tradable` per unique ticker (`shortable` ∧
  `easy_to_borrow`); names we can't borrow are excluded entirely.
- **Squeeze gap-through** — a stopped short covers at `min(MFE, stop × k)` (k=1.5), not at a
  clean +8%.
- **Round-trip friction** 0.5% (bid-fill + slippage) + borrow haircut (0 intraday).

| MFCS bucket | % ETB | **NET short P&L** | win% | n_ETB/n |
|---|---|---|---|---|
| 0.00–0.20 (best longs) | 34% | −8.73% | 20% | 10/29 |
| 0.20–0.30 | 13% | −0.17% | 67% | 24/191 |
| 0.30–0.40 | 11% | +6.44% | 83% | 6/53 |
| **≥0.50 (ELITE)** | **48%** | **+9.47%** | **100%** | **11/23** |

**Two realism gates, both pass for the ELITE bucket:**
1. **Borrowability**: only **18% of all BUY candidates are ETB** — most low-float names
   can't be shorted. BUT **48% of the ELITE bucket is ETB** (higher-MFCS names skew slightly
   larger/more-liquid → more borrowable). So the edge is *executable* on ~half the bucket,
   not a paper fantasy.
2. **Net P&L after squeeze+friction**: the ELITE ETB subset nets **+9.47% / 100% win
   (11/11)** — *higher* than the gross +6.38%. The ETB filter strips the un-borrowable
   noise, and the borrowable ELITE names are the cleanest fades (none squeezed through the
   stop in-sample). The lowest bucket (best longs) is correctly the worst short (−8.73%).

## 1. Why this is real (and the honest limits)

- **Real**: the bot's own ELITE picks (the ones the now-disabled press doubled into) fade
  hard, ~half are borrowable, and shorting the borrowable ones nets ~+9% with no squeeze in
  the sample. The infra exists (D161 + doc-190 bid-marketable SELL).
- **Limits (read with these)**: **n = 11 ETB-ELITE over 3 days** — small; the 100% win is a
  small-sample artifact, not a guarantee. Current ETB (Sunday) is a proxy for the trade-day
  ETB. The squeeze-gap k=1.5 is an assumption (a real +108%-type squeezer that's also ETB
  would hurt — none appeared in the ETB-ELITE subset, but the tail is fat). **This is a
  directional GO, not a sized commitment.** The weekly auto-study (doc 199) accrues the n.

## 2. The path to live (doc 202, proposed — flag-gated, validated)

The fade-short is now quantified GROSS (200) → NET (201). Next:
1. **Wire the fade signal into D161** (flag-gated OFF, `EXEC_FADE_SHORT_*`): when a
   candidate is the **ELITE-fade signature** (MFCS ≥ 0.50 ∧ shortable/ETB ∧ a liquidity
   floor) → route to a SHORT instead of skipping it, sized small. Reuse D161's
   shortability check + the doc-190 bid-marketable SELL.
2. **Squeeze circuit-breaker**: abort/skip the short if the name is already running (e.g.,
   above VWAP + green on the day, or opening-range CONFIRMED per doc 188) — never short a
   live squeeze.
3. **Validate via the scorecard** (the fade-short trend) before flipping live, and start at
   a fraction of normal size given n=11. Pierce's call to flip (live competition behavior).

This turns the selection study's #1 finding into a **second income stream**: don't just
stop sizing into faders (done, live Monday) — *short the borrowable ELITE ones.*

## 3. The full arc (177→201)
SELECT the continuers (188/191/198) · don't size into faders (199, live) · **short the
borrowable ones (200/201)** · FILL marketably (189/190/195) · HOLD via tranches+trail
(196/197) · all measured by the auto-scorecard (193/194) + weekly study (199). The
offensive *and* defensive sides of the selection edge are now mapped and quantified.

## Appendix — files
- `scripts/selection_study.py` — `--realistic-short` (ETB filter via `check_asset_tradable`
  + squeeze gap-through + frictions); `fade_short_realistic` in `--json`.
- This doc + `docs/SYSTEM_MAP/changelog.md`.
