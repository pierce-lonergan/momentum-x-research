# 192 — Fill-realism backtest + the SELECTION reframe (quantifying 189/190)

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "are you testing performance in the arena sim? if not, start
looking into it to quantify our changes, and identify arena-realism enhancements."

---

## 0. Honest answer: NO — and the existing sims literally can't see my fill change

Two parallel investigations mapped every simulator. The verdict:
- **All three deterministic sims** (`backtest.py`, `replay_optimizer.py`,
  `phase3_replay_simulator.py`) assume a **perfect fill at the eval price / bar close**,
  or read the production fill from a truth corpus. **The docs 189/190 marketable-fill
  change is invisible to all of them.**
- The one tool with a real fill model (`arena_replay_session.py` + `mx-arena`'s
  Polygon-calibrated `SpreadModel` + `LimitAwareFillModel`) is a SHADOW replay of *past*
  production fills — it can't forward-test a new entry strategy.
- The doc-182 rejection grader **had never been run** (no output files). I ran it on
  5/29: of 4 logged rejections only 1 had forward data — a below-VWAP block that **faded
  −81.6% by EOD** (gate dead right, n=1). All 4 were D170, **zero faller rows → doc 191
  has no data yet** (instrumentation lands Monday).
- **There is no real "Gemini arena"** — it's aspirational in the docs.

So I built the missing capability.

## 1. The tool: `scripts/fill_model_backtest.py`

Forward-models PASSIVE (limit at the eval price) vs MARKETABLE (docs 189/190) fills on
real historical BUY candidates, and **sweeps the fill window** — because the window is
the crux:

> The bot's WIDE_STOP entry is DAY-TIF but only ARMS its protective stop if filled within
> ~120s (`_t2_arm_standalone_stop poll_timeout_sec=120`); a later fill is an UNPROTECTED
> ghost, not a good fill. Fast-path limits "expire unfilled if the dip doesn't reach
> target." So the realistic *good-fill* window is ~1–2 min, NOT all day.

- **Candidates**: every BUY-action eval from `data/features/features_<date>.jsonl`.
- **Forward path**: 1-min Alpaca bars (same fetch as the grader).
- **Spread**: the arena's calibrated `SpreadModel.get_bid_ask` (realism; 30bps fallback).
- **train==serve**: imports **production's** `compute_marketable_limit` — the sim uses the
  exact code the bot runs.
- **Fill model** (corrected — see §3): a buy limit fills the first bar whose LOW ≤ limit,
  at `max(min(limit, open), low)` (price improvement for BOTH sides). The only real
  difference is the limit LEVEL (marketable is higher → fills more often).

## 2. The finding (3 days, robust): SELECTION dominates fills ~30×

| Day | Selection: BUY-set fwd return | Win rate | Marketable EDGE | Caught names |
|---|---|---|---|---|
| 2026-05-28 | **+3.68%** | 50% (54/107) | **+0.02% → +0.17%** | runners (+3% → +12.6%) |
| 2026-05-26 | −2.97% | 23% (15/65) | −0.03% → −0.07% | faders |
| 2026-05-29 | **−6.86%** | **7%** (9/130) | −0.2% → −0.5% | faders (−11.7%) |

Two conclusions:

1. **Selection is the lever.** The day-to-day swing in "where the BUY set goes" is **±7%**
   — when selection is good (5/28, 50% win) the bot prints; when blind (5/29, 7% win) it
   bleeds. The marketable fill edge is **±0.2%** — roughly **30× smaller**.
2. **189/190 is a SELECTION-CONDITIONAL amplifier, not a standalone edge.** It catches
   *more of whatever you're selecting*: real runners on a good day (5/28 caught_fwd up to
   +12.6%), more faders on a bad day (5/29 −11.7%). Keep it — the premium is ~0.2% and it
   helps when selection is positive — but **it cannot rescue a fader-heavy funnel.** My
   last two days of fill work (189/190) were polishing the wrong link.

**This is the 5% gap, quantified: it lives in SELECTION.** It validates the continuation
edge (188/191) as THE priority — picking the ~⅓ that continue is worth the ±7%, and fills
only convert that edge once it exists.

## 3. A bug I caught in my own sim (why measurement needs scrutiny)

My first sweep showed a spurious **+6% edge** for marketable. Cause: I filled the PASSIVE
order *at its limit price* even when the market opened lower — but a real resting buy
limit gets **price improvement** (fills at the better market price). Fixing both sides to
fill at `max(min(limit, open), low)` collapsed the edge to its true ±0.2%. A sim is only
as honest as its fill model — stated caveats below.

## 4. Caveats (stated, not hidden)

- `current_price` is the entry anchor, not the exact submitted limit (prod's was ~30–50s
  staler — so prod's real passive miss-rate is *higher* than modeled; marketable's
  relative value is a mild **under**-estimate here).
- 1-min `LOW ≤ limit` is a fill proxy (no tick/queue model).
- Return is **gross capture** (fill → fixed +60min close), NOT net P&L — exits/stops are
  modeled separately (`phase3_replay_simulator.py`). The SELECTION line is robust to all
  of these (it's just "where the BUY set went").

## 5. Arena-realism enhancements (the roadmap this surfaced)

1. **DONE (this doc): a forward fill model.** The #1 gap — no sim could see entry fills.
2. **Operationalize measurement (next, recommended):** a post-close runner that auto-runs
   the rejection grader + this backtest and appends a daily SELECTION scorecard
   (win%, mean fwd return, fill edge, gate-correct%) to a trend file. The grader was built
   (doc 182) but **never run** — measurement that isn't habitual is measurement that
   doesn't happen.
3. **Higher-fidelity fills:** swap the 1-min proxy for `mx-arena`'s `LimitAwareFillModel`
   (queue/partial-fill aware) and anchor to the *staler* submitted limit.
4. **Exit parity in the entry backtest:** layer `phase3_replay`'s D122 exit logic so the
   metric is net P&L, not gross capture.
5. **Selection-quality tracking is the headline metric** — daily BUY-set fwd return +
   win-rate is the number to move; everything else is 2nd order.

## 6. Implication for "build the next features"

The data redirects the roadmap: **the next feature should improve SELECTION, not fills or
sizing.** The selection levers (188 continuation detector, 191 faller exemption, the 184
intraday continuer) are **data-gated** — they need the Monday-onward observe data the
grader + this backtest will produce. So the disciplined next build is the **daily
measurement runner** (operationalize §5.2), then the selection improvements once the data
lands. Building more fill/size features now would repeat the exact "ship on logic, not
measurement" pattern this doc was written to break.

## Appendix — files
- `scripts/fill_model_backtest.py` (new) — the fill-realism backtest + window sweep.
- Reuses: `src/execution/alpaca_executor.compute_marketable_limit` (train==serve),
  `mx-arena/arena/spread_model.SpreadModel` (calibrated spreads),
  `src/data/alpaca_client.get_bars`.
- Ran: `scripts/finalize_rejection_outcomes.py 2026-05-29` (grader, 1 graded row).
- This doc + `docs/SYSTEM_MAP/changelog.md`.
