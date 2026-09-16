# 188 — The Opening-Range Continuation Edge: trade the pumps that CONTINUE

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "why don't we trade pumps? isn't that easy money, get in/out?
full send on the highest-ROI features to extract max profit from our watchlists."

---

## 0. Why we don't *blindly* trade pumps (the honest answer)

"Get in, get out" is the trap. Three realities — all confirmed by our own data:
1. **You can't reliably get OUT.** On a $641K-dollar-volume name (CGTL, Friday) the
   reversal hits no bids; our 30s pipeline is last out. **Friday: the pumps we traded
   *deliberately* lost −$1,500; the ones we were *forced to hold* (403-ghosts) won
   +$6,999.** The market punishes the slow exit.
2. **Most pumps FADE** (~⅔; only ~21% of +30% movers continue per prior work). Without
   an edge to pick the ⅓, it's a negative-expectancy bet: small wins, occasional
   catastrophic fade-loss.
3. **The "obvious" pump signals don't work** — doc 187's deep-research **killed 12 of
   them** (float rotation, premarket volume, short interest, displayed bid walls).
   Traders who "scalp pumps for easy money" are usually trading noise + surviving on
   luck until a fade wipes the run.

**So the answer is NOT "avoid pumps" — it's "trade the ~⅓ that CONTINUE, which the
research tells us how to spot, and skip the faders."** This doc builds that detector.

## 1. The validated edge (doc 187, 3-vote confirmed)

A gap-up CONTINUES when, in the first ~30 min, it shows:
- **Opening RVOL** ≥ threshold (first-5-min vol / 14d-avg of that window) — RANK 1.
- **Bullish opening range** (sign of the 9:30-9:35 candle) **AND price breaks the
  5-min high** — RANK 2 (trade IN the open's direction; don't fade it).
- **Holds above VWAP** — a top confirmed feature.
- Weighted most in the **first ~45 min** — RANK 4.

## 2. Shipped: the detector + observe-mode wiring

**`src/analysis/opening_range.py`** — pure, tested (9 tests): `compute_signal(...)`
returns `{opening_rvol, range_sign, broke_high, above_vwap, confirmed, score}`.
`confirmed` = the validated LONG signature (bullish open ∧ broke 5-min high ∧
RVOL≥threshold ∧ above VWAP). `score` = a 0..1 continuation confidence (RVOL-weighted)
for sizing/ranking. `OpeningRangeTracker` captures the 9:30-9:35 bar per ticker from
minute bars. It DELIBERATELY ignores the doc-187 debunked signals.

**Wired LIVE (observe-mode, safe):**
- Tracker instantiated; the opening bar captured at the Phase-2 `_fetch_bars` site
  (guarded, idempotent).
- At the faller-block gate, the signal is computed + the 3 features (`opening_rvol`,
  `opening_range_sign`, `broke_or_high`) are logged into the doc-182 rejection-shadow,
  and a **`D188 CONTINUATION-CONFIRMED but FALLER-BLOCKED`** warning fires when the
  gate blocks a name the validated signal says will continue — exactly the case the
  grader will measure (did it run? then the gate was wrong).
- The 3 features added to the intraday-continuer contract (doc 184, now 13 features)
  + `vwap_distance` (doc 187) — train==serve. 18 unit tests pass.

## 3. Why observe-FIRST and not gate-on-now (the universe caveat = the discipline)

doc 187's headline evidence was validated on **LIQUID** stocks (>$5, >1M vol) — **NOT
our low-float sub-$50 universe.** The research itself flags that the *mechanism*
transfers but the *magnitudes* don't, and that OFI/RVOL can break on the explosive
gap-up regime. **So even the validated signal is not yet validated on OUR thin pumps.**
Flipping it to gate live now would repeat the exact mistake your question probes —
trusting a pump signal before it's proven on the names we actually trade.

The disciplined "full send": **build the edge (done), compute + log it on our universe
NOW (this ships), and flip the gate-action after a few sessions confirm that
continuation-CONFIRMED names actually run on OUR tape.** The rejection-grader (doc 182)
+ the `D188` warnings give us that proof, fast.

## 4. The gate-action (the flag-flip — ready, pending the observe data)

Once the data confirms confirmed-continuers run on our universe, flip these (flag-gated):
1. **Faller exemption**: a continuation-CONFIRMED name is EXEMPT from the D160 faller
   block (the faller blocked CGTL; the validated signal says trade it). Add a
   **liquidity floor** (min dollar-volume) — the edge needs fills; don't act on the
   thinnest names where execution is the killer.
2. **Catalyst-downgrade upgrade**: a confirmed continuer that the D200/D204 gate would
   downgrade to half size gets admitted at FULL size (press the validated continuer).
3. **Continuer Kelly multiplier**: once trained (doc 184), size by `score` × the
   continuer's P(continue).
These turn "downgrade all no-catalyst high-MFCS" into "downgrade UNLESS the validated
continuation signature is present, then press" — i.e., **trade the continuers,
aggressively, skip the faders.**

## 5. The full profit chain (where this fits)
PICK the continuer (THIS, doc 188 + the continuer doc 184) → FILL it (marketable
limits, B6 — the next build) → HOLD the winner (exits, doc 176/178) → SIZE it (ELITE
press doc 178 + continuer Kelly). doc 188 is the front of the chain — the rest are
the staged enhancements. And none of it is live until the elevated restart (177-188).

## Appendix — files
- `src/analysis/opening_range.py` (new, 9 tests), `tests/unit/test_opening_range.py`
- `src/analysis/intraday_continuer.py` (+3 opening-range feats, now 13), `src/shadow/rejection_outcome_shadow.py` (+3 feats)
- `main.py` (tracker instantiate + capture + faller-site observe + EOD reset)
- This doc + `docs/SYSTEM_MAP/changelog.md`
