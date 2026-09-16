# 200 — Flags LIVE for Monday + the fade-short opportunity

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "make it live for Monday, full send it; then let's take advantage of
the predictable faders (futures, puts, or something else)."

---

## Part A — the ROI flags are LIVE (Monday 4:30 boot)

Pierce gave the go. Set in `~/momentum-x-secrets.env` (the source of truth the launcher
copies → `.env` at boot) + mirrored to `.env`; binding verified against `ExecutionConfig`:

- **`EXEC_ELITE_SIZING_PRESS_ENABLED=false`** — the doc-178 ELITE press (size 2× at
  MFCS ≥ 0.50) is **OFF**. doc 199 proved that bucket RAN 4% / faded −6.19% — the press was
  doubling down on the worst names. **This is the single highest-impact change of the
  weekend**: it stops live capital flowing 2× into the bot's worst-performing picks.
- **`EXEC_RVOL_EXHAUSTION_SIZE_PENALTY_ENABLED=true`** — exhausted names (ran 20% vs 41%)
  now take half size (`EXEC_RVOL_EXHAUSTION_SIZE_MULT=0.5`).

(Secrets/`.env` are local + git-ignored — the code is committed in docs 199/198; only the
flag *values* are local config. `FALLER_CONTINUATION_EXEMPTION_ENABLED` stays OFF pending
the grader's live data.)

## Part B — taking advantage of the predictable faders

The selection study (doc 198/199) shows the bot's high-MFCS / big-gap / exhausted picks
FADE predictably. Can we monetize the fade? Three instruments:

### Puts / futures — NOT viable for this universe
- **Puts**: our universe is sub-$10 low-float small-caps. Most have **no listed options**,
  and the few that do have illiquid, wide-spread chains that eat any edge. Not actionable.
- **Futures**: there are **no single-stock futures** for these names; index futures don't
  express a stock-specific fade. Not actionable.

### Shorting the stock — the only viable path (and the bot already has the rails)
The fade-short model (short at entry, cover at +8% squeeze-stop, else at +60min; GROSS):

| MFCS bucket | short P&L | win% | squeezed% | n |
|---|---|---|---|---|
| 0.00–0.20 (best longs) | −3.86% | 24% | 72% | 29 |
| 0.20–0.30 | +3.32% | 68% | 28% | 191 |
| **≥0.50 (ELITE)** | **+6.38%** | **83%** | **4%** | 23 |
| **whole BUY set** | **+2.26%** | 62% | 33% | 302 |

**The bot's own ELITE bucket is a +6.38% short at 83% win / 4% squeeze** — the exact
inverse of the press we just disabled. The lowest-MFCS bucket (best longs) is the worst
short (72% squeezed) — the model is internally consistent (short edge = inverse of long
edge). The infra exists: the **D161 faller-short path** (shortability/ETB check, OTO short
order) + the **doc-190 bid-anchored marketable SELL**.

### The real-world killers (why GROSS ≠ net — be honest)
1. **Hard-to-borrow / no shares**: low-float small-caps are frequently HTB or un-shortable;
   borrow fees can run 50–500% annualized. The Alpaca `shortable`+`easy_to_borrow` check
   (already in D161) will *exclude* many of the best fade-shorts.
2. **Squeeze gap-through**: the **+108% worst-case MFE** in the sample. A +8% stop on a thin
   name can gap to +30/50% intrabar with no offers — one squeeze can erase many fade wins.
   The 4% squeeze-rate on the ELITE bucket is low, but the tail is fat.
3. **Bid-side fills + borrow fees** shave the gross.

**Net sensitivity**: the ELITE bucket survives a reasonable haircut — assume the 4%
squeezers gap to +20% (not +8%) and a ~0.5% borrow cost: net ≈ 6.38% − (0.04×12% extra) −
0.5% ≈ **+5.4%**. Still strongly positive *for that bucket*. The whole-set (33% squeeze-
rate) is far more tail-exposed — short the **bucket**, not everything.

## The disciplined path (recommended next build)
1. **Realistic fade-short backtest** (doc 201, proposed): add to the model an Alpaca
   ETB/shortability filter, a borrow-cost haircut, and squeeze gap-through (stopped shorts
   cover at `min(MFE, stop×k)`) → **net** P&L by bucket. Confirms the +5%-ish ELITE edge
   survives execution on names we can actually borrow.
2. **Wire the fade signal into D161** (flag-gated OFF): when a candidate is the fade
   signature (MFCS ≥ 0.50 ∧ exhausted ∧ shortable ∧ liquid) → route to a SHORT instead of
   skipping, with a hard liquidity floor + a squeeze circuit-breaker (abort if the name is
   already running). Validate via the scorecard before flipping live.
3. This turns the selection study's #1 finding into a *second* income stream: **don't just
   stop sizing into faders (done) — short the most predictable ones.**

## Appendix — files
- `~/momentum-x-secrets.env` + `.env` — the two live flag flips (local config).
- `scripts/selection_study.py` — added the FADE-SHORT model (`--short-stop`) + per-bucket
  short P&L / win% / squeeze% + `fade_short` in `--json`.
- This doc + `docs/SYSTEM_MAP/changelog.md`.
