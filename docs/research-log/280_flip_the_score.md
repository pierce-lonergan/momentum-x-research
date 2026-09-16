# 280 — FLIP THE SCORE: the profit charter — point the falsification engine at the winner-clipping bleed

**Author**: Claude (Fable 5 driving; Opus 4.8 panels/retest; deep-research cross-family) | **Date**: 2026-07-03 | **Class**: DORMANT-C (read-only vs production; the mechanism ships as flag-gated code + staged config — **Pierce flips it**, the agent does not) | **Charter**: round seven, the directed PROFIT mandate — turn seven nights of falsification on the −$20k paper book and move it in the right direction, provably, on real tape. `/ultracode` throughout. **§1–§3 (survey + pre-registration) commit BEFORE any P&L result.**

> Seven nights proved where the money *isn't* (selection alpha, every form). Tonight points the same apparatus at the one lever the evidence favored — **exit posture** — and the honest verdict is **NO-SHIP: flipping the exits to a hold posture does not flip the score.** On the actual −$22,961 book (the −$20,131 headline hid +$2,830 of phantom P&L) the blanket flip is **null-to-mildly-negative** (every arm's CI includes 0), with only the *overnight* arm genuinely negative — and **doc-69's motivating "+$23K" evaporated under the realism gauntlet.** The winner-clipping exits the mandate indicted turn out to be roughly *protective* on this rocket-free window. What survives is not a profit flip but an honest kill of the *blanket* lever, an untested *conditional* one, and an instrument to catch the regime when it turns. Per the charter, a falsified profit lever honestly killed beats a fantasy shipped — and this one took a design panel (4/4 DO-NOT-FREEZE) to stop it printing a fantasy positive, then a hostile retest to stop *me* overstating the negative.

---

## §1 — THE SURVEY (graded over the ruled-out map) and the lever choice

**The ruled-out map (certified robust by doc 279):** selection alpha is closed in every form (245–260); the profit cannot come from *which name*. Ruled IN: **variance is the product** (273); **holding is the edge, the exits destroy it** (185/264); execution integrity is the durable win (263–272), so the −$20k is *honest*.

**Grounded confirmation (this session, on the real artifacts):**
- **The −$20,131 book is real and its shape is the diagnosis** (`data/trade_results.jsonl`, 84 trades, 32 sessions). Biggest realized **win +$1,810**; worst **loss −$3,876**; 29% win-rate; wins +$9,830 vs losses −$29,962. **Winners are clipped to a cap while losers run bigger** — the backwards asymmetry a capital-preservation machine imposes on fat-tailed prey.
- **The winner-clippers are in the config, with self-incriminating comments.** `exit_policy=bar1_legacy` (the prod default) — the docstring itself: *"LOSES money on the actual trade pool (−$5K across 117 trades) even though the entries identify winners (median MFE +2.61%)."* Plus D164 (sell 50% at T+2min), the 5.5% tight stop, D163 trail. `exit_policy=t1_next_open` (carry overnight) is documented at *"+$23K incremental EV per 117-trade pool"* (doc 69) — **built, flag-gated, and OFF.**
- **Two contaminated trades must be excluded** (Bug C): LFS +$1,403, APPS +$1,426 are phantom P&L. **The honest clean baseline is −$22,961**, not −$20,131 (the headline *includes* +$2,830 of phantom wins). We beat the clean number.

**The graded slate:**

| Lever | Attacks | Prior | Verdict |
|---|---|---|---|
| **Exit posture** (bar1_legacy → hold-to-close/next-open) | posture | **highest** — built flag, doc-69 +$23K, book shape screams it | **CHOSEN** |
| Flag-OFF portfolio (T2, continuation-exemption, EMPTY≠BEAR) | posture/selection | medium — some are selection-adjacent (closed door) | deferred |
| Capital/sizing structure (Kelly on fat tails, breadth) | structure | medium — real but second-order to the exit clip | deferred (Stage-2) |
| Execution capture (marketable fills) | capture | low-medium — MARKETABLE_LIMIT already ON | partially shipped |
| Regime-conditioning | regime | low — 2026-overfit risk (245's cautionary tale) | rejected |

**Chosen lever: the EXIT POSTURE.** It attacks the open door (posture), it is the program's own built flag, its motivating number (+$23K) is precisely the suspicious counterfactual that must run the gauntlet, and the book's shape (capped wins) is direct evidence the clip is happening. **The other levers are downstream of this one** — sizing into conviction is moot if the position is flattened at T+60s.

**Expectancy-vs-variance claim, stated BEFORE measuring (mandate §2.4):** I claim the hold posture raises **TOTAL realized P&L primarily by un-clipping the right tail** — it stops selling winners at the ~$1,810 cap and lets them run to the close/next-open — i.e. it is a **variance-shape harvest on a right-skewed distribution**, NOT (necessarily) a lift in the winsorized per-trade mean. The counter-thesis, tested honestly: on this *post-LASE* window the winners are capped with no rockets present, so holding might **give the gains back to mean-reversion while making overnight losers worse → net negative.** The existing nightly d122-vs-tranche comparison is *a wash* (Δ mean −0.19%, >0 in 10/20 sessions), which is real cautionary evidence. **This is a genuine experiment, not a foregone win.**

---

## §2 — Grounding: the measurement machinery exists, runs, and is extensible

- **The validated fill/exit model runs** (`scripts/fill_model_backtest.py`): arena `AlpacaFillModel` (Alpaca's real limit-fill rule) + Polygon-calibrated `SpreadModel` + 45s submission staleness + a live-parity D122/tranche exit replay. Tested: exit code 0, real forward bars. It parameterizes the exact posture knobs (`--stop-pct`, exit model).
- **The book is reconstructable** two ways: the decision journals (`data/journals/journal_*.jsonl`) carry `entry_price`, `position_size_pct`, `direction` (and sparse `fill_qty`/`exit_price`); the local `minute_aggs` warehouse prices any counterfactual exit (ts_utc↔entry_time verified; ts_et for session boundaries).
- **The scoreboard exists** (`data/reports/eod_*.json`, `fill_backtest_*.json`, `measurement_trend.jsonl`, `post_close_scorecard.py`, shadow-vs-prod) — a metric can be wired into the nightly cycle.

---

## §3 — PRE-REGISTRATION (frozen; commits BEFORE any P&L result)

**Mechanism to ship (if it survives):** flip the exit posture from the winner-clipping default to a HOLD posture, via the existing `exit_policy` flag, disaster-stop + −10% daily circuit as the only brake. The winning *arm* is selected by the measurement (below), not pre-judged.

**The novelty/method sweep (deep-research, 106 agents) RAISED THE PRIOR AGAINST the thesis** — cross-family-verified refutations: (1) intraday opening-range-breakout held-to-close is real but *economically trivial* (~0% after costs); (2) pushing further into the tail *reduces* momentum return (no "size up the breakout"); (3) **for overnight gaps the edge is REVERSAL, not continuation** — the fade-the-gap-held-to-close earns up to +3.29%/day and reversal is *larger* for the most extreme gaps. This *agrees* with doc-251 (gap-up long = systematic fade). **So holding a winning gapper to close/next-open may give the gains straight back to mean reversion.** Imports adopted: per-session subsampling/block-bootstrap for heavy tails (Politis); fractional (not full) Kelly. **The NEGATIVE branch is a live, expected outcome; the burden of proof on a positive is high.**

**The design-stress panel (`wf_a7feedc6`, 4 Opus attackers) returned DO-NOT-FREEZE (4/4)** and redesigned the measurement. Verified headline-invalidating flaws, all fixed below: (i) **unit-of-analysis inflation** — a per-candidate CI over 5,140 candidates manufactures significance (→ per-session-dollar); (ii) **the estimand isn't deployable** — 5,140 scored candidates is 62× the 82-trade book (→ measure the *actual book*); (iii) **exits bypassed the validated fill model** — only entries went through it (→ price *every* exit through the SpreadModel bid); (iv) **train/serve mismatch** — pure-hold arms ≠ the flag, which keeps other exits firing (→ name the exact flag-set + measure the ceiling separately); (v) **survivorship** — next-open silently booked best-case (→ require a real next-session bar with volume, else last tradeable); (vi) **winsor@5% is theater** on N=5,140 (→ hard one-sided +20% cap); (vii) **the VARIANCE-HARVEST branch is a laundering loophole** (→ require an out-of-sample tail edge).

**Measurement (redesigned, frozen) — PRIMARY = the actual book, per-session-dollar** (`scripts/_doc280_counterfactual.py`): the 82 clean trades (2 phantom excluded). Each real entry (journal `entry_price` + `position_size_pct` × session equity → qty; qty-free ratio fallback), each counterfactual EXIT priced through the **validated `SpreadModel` bid** (SELL) + hostile slippage, at three policies — **hold-to-close** (closing bar), **hold-to-next-open** (survivorship-gated: real next-session volume or last tradeable), **hold-to-close + 15% disaster stop** (gap-through slippage). Right tail **hard-capped at +20%/trade**. Trust-tiered: **CONFIRMED** (large-pnl, reconciled → trustworthy lower bound) vs **SIZE_BASED** (breakeven-clips via journal size → fuller, less certain); both reported so the truth is bracketed. Inference **per-session-dollar**: sessions-improved sign count + block bootstrap over sessions + **out-of-sample early/late split** + drop-best-session + top-trade share. Baseline = clean **−$22,961**.

**SECONDARY / CEILING = `scripts/_doc280_posture_backtest.py`** (all BUY candidates through the arena `AlpacaFillModel`, 4 arms) — reported as the *average-candidate ceiling and robustness check*, explicitly NOT the deployable estimand.

**Outcome branches (frozen):**
1. **MOVES-THE-BOOK** — a hold policy's per-session-dollar Δ > 0, CI (block-boot over sessions) excludes 0, **both OOS halves same sign**, robust to drop-best-session → real → **SHIP**, staged.
2. **TAIL-HARVEST** — Δ > 0 but concentrated / dies out-of-sample → **do not ship as an edge**; report as an uncertain tail effect (the variance-harvest laundering branch is *killed* per the panel — no shipping on a lucky draw).
3. **NO-EFFECT** — Δ CI includes 0 → **null**; the +$23K doc-69 counterfactual did not survive realism; flag stays off.
4. **NEGATIVE** — Δ < 0 → the posture flip **hurts** → report at full strength (the gap-reversion prior confirmed).

**Ship discipline (if positive):** name the **exact flag-set** the winning policy maps to (`exit_policy` + `bar1_exit`/`d164`/`d163`/`phase_stop` states — a config diff, not a one-liner if the ceiling requires full inversion); gauntlet (compile, close-path gate, full-suite delta stash-compare, sandbox boot, Adversary wrapper-bugs=0); staged env line; **Pierce flips it.**

---

## §4 — THE RESULT: the posture flip does not flip the score (null-to-negative; the overnight arm is the one real negative)

The pre-registered measurement (committed `a104a0c` before it ran) returned **no positive on any arm** — and, per the hostile retest's own correction (§5), the honest reading is **NO-EFFECT leaning negative (branch 3)** for the intraday arms and a **genuine NEGATIVE (branch 4) for the overnight arm** — *not* a blanket "demonstrated negative." Every arm's CI includes zero. Per-session-dollar Δ (hold policy minus the actual exit), on the clean −$22,961 book:

| Policy | CONFIRMED Δ (lower bound) | FULL Δ | sessions improved | block-boot 95% CI (CONFIRMED) | OOS halves |
|---|---|---|---|---|---|
| **Hold-to-close** | **−$1,363** | −$8,739 | 5/14 | [−$9,784, +$7,281] — includes 0 | both negative |
| **Hold-to-next-open** (the doc-69 flag) | **−$1,582** | **−$14,720** → new book −$24,169 | 6/14 | [−$8,958, +$5,866] — includes 0 | both negative |
| **Hold-to-close + 15% stop** | **−$1,690** | −$3,441 | 5/14 | [−$11,450, +$7,416] — includes 0 | mixed |

**Holding helped only 14 of 41 trades.** The single best case (TRT, +$6,000, capped at +20%) is swamped by the single worst (AZI, −$8,245 — a breakeven-clip that reverted hard). **Leave-one-out (drop the worst trade) is decisive about robustness:** hold-to-close FULL goes −$8,739 → **−$494** and hold+stop −$3,441 → **−$187** — i.e. two of the three arms are *one-trade-fragile* (they ride on AZI). **Only HOLD-TO-NEXT-OPEN survives drop-worst** (−$14,720 → −$8,184 still deeply negative) — it is the one arm that is *robustly* negative, it maps to the actual doc-69 `t1_next_open` flag, and its sign matches the gap-reversion prior. The intraday arms are, honestly, a **null within one trade of noise** (CONFIRMED point estimates −$1,363 to −$1,690, CIs like [−$9,784, +$7,281] all spanning 0, n=22 trades / 14 sessions); the overnight arm carries the real directional weight.

**The doc-69 "+$23K/117-trade" counterfactual evaporated and inverted under the realism gauntlet** — the 233 cautionary tale (a profit number that a random baseline beat) playing out again, this time on the mandate's own headline motivator. It did not survive pricing the exits through the real spread model, survivorship-gating the overnight fills, per-session-dollar inference, and the one-sided tail cap.

**Uncapped sensitivity (fairness to the thesis, since the +20% cap is hostile to holding):** removing the cap leaves the trustworthy CONFIRMED subset *unchanged* (−$1,363 close, −$1,582 next-open — no confirmed trade even reaches +20%, so the core negative is not a cap artifact) and the overnight FULL still deeply negative (−$13,671). Only the FULL hold-to-close softens (−$8,739 → −$1,871), and *entirely* via one uncertain-qty breakeven-clip (TRT, +$11,517 uncapped) — the single-trade lucky-draw the panel explicitly barred from shipping. **The negative holds in every trustworthy cut.**

## §5 — MECHANISM, and reconciling with the program's own "holding won" (185/264)

**Why holding loses here:** on this *gapper* selection, holding gives back more than it captures. The book's winners are already capped (+$1,810 max realized) and the names *revert* (the documented gap-fade); holding a winner to the close/next-open mostly returns the gain, while holding a loser deepens it. **The winner-clipping exits the mandate indicted were, on this book, roughly protective — not the bleed.**

**The apparent contradiction with docs 185/264** (where accidental forced-holds LASE +$73k, STG won) resolves as **survivorship**: those anecdotes were the *winners* that happened to be held. Measured across the whole book — winners *and* losers — the held losers overwhelm the held winners. A rocket-rich window (which doc-69's earlier pool contained) can flip this; **this post-LASE window has no rockets**, and holding without a rocket is a losing reshape.

**Hostile retest + cross-family peer** (`wf_c4d22dd1`, 2 Opus attackers, **NEGATIVE-NEEDS-QUALIFICATION**) — both *re-priced the probes in code* and corrected my own overstatement in three ways I have folded into §4:
- **It is a NULL leaning negative, not a demonstrated negative (intraday arms).** Every arm's CI includes 0; the CONFIRMED estimates (−$1,363…−$1,690) sit within one AZI-sized trade of noise on n=22/14 sessions. The scientifically correct label is **NO-EFFECT (branch 3), leaning negative** — only the **overnight arm** (−$1,582 CONFIRMED, robust to drop-worst) is a genuine directional NEGATIVE, and it is the one that maps to the doc-69 flag and matches the gap-reversion prior. I had framed all three as equally solid; they are not.
- **The verified checks all cut *toward* the negative, not away.** Uncapping leaves CONFIRMED unchanged (only 5/68 trades ever touch the +20% cap, none in the trustworthy tier); including the 27 MISMATCH trades makes the money arms *worse* (−$10,763 close, −$22,062 next-open) — so my MISMATCH *exclusion* was generous-to-thesis, not hostile; and AZI −$8,245 is a verified-real gap-reversion loss (tape: $4.06→$7.00→closed $1.71), already outside the CONFIRMED bound.
- **I rebutted a strawman.** Doc 280 measured a *blanket* flip (hold all 41 trades), but docs 258/264 *already conceded* blanket holding is −EV — doc 264's actual proposal was a **conditional ex-ante rocket-gate** ("close > +30% on RVOL > 10 → hold to next open"), which this measurement **never tested**. So the honest scope is "**the blanket flip is null-to-negative on this rocket-free book; the conditional gate 264 named was not falsified**," not "holding is dead." (This is exactly the §6 open door + the Stage-2 chip.)

**Net:** the posture-flip lever is a **confirmed NO-SHIP in the current regime** — a *null-to-negative* blanket result with one genuinely-negative overnight arm — and the conditional/regime-gated hold remains an **open, unfalsified** question, not a killed one.

## §6 — What ships, the game, and the honest implication

**No P&L mechanism ships — and that is itself the finding: the measurement *validates keeping the current exits ON*.** Flipping `exit_policy` to a hold posture is pre-registered NO-SHIP; the flag stays off, and doc 280 is the evidence that keeps it off (a real, if unglamorous, protection of ~$1.4k–$14.7k the flip would have cost on this book).

**The game / scoreboard (mandate §6.6), honestly wired — BUILT:** the deliverable is not a flipped P&L (there is none to flip honestly tonight) but the **posture-delta instrument** (`scripts/_doc280_posture_scoreboard.py`) — a per-session score of "would holding have beaten the actual exits, and by how much?", with a running cumulative Δ, a beat-your-own-best marker, and an `--append` mode that logs to `data/reports/posture_delta_trend.jsonl`. Run tonight, it shows the story vividly: the cumulative hold-Δ climbed to **+$4,702 by 2026-06-08** (holding was winning through the rocket-rich stretch), then one session (2026-06-09, AZI) sank it to −$8,739 — a sign that flips on one trade is unshippable, and the instrument *shows* that rather than asserting it. It reads negative now; it lights up (and only then is the hold flag reconsidered) when the cumulative Δ turns *durably* positive — i.e. when the regime turns rocket-rich. **Staged for Pierce to wire into the nightly `post_close_scorecard.py`; the agent does not touch the running bot's cron.**

**The ceiling (all-candidate average, `ceiling.json`, 1,270 fills / 45 sessions) refines "dead" to "regime-gated and currently off."** On the average candidate — *not* the deployable book — hold-to-close beats bar1 by **+1.58%/ticket** (block-boot CI [+0.48%, +2.73%] excludes 0; survives winsor@5% at +0.68%; win-rate 47% vs 21%). But two facts disqualify it as a ship-now edge and *explain the primary negative*: it is **75% tail-driven** (top-1% of tickets = 75% of the Δ — a rocket harvest), and it is **regime-flipping** — positive in rocket-rich 2026-04/05 (+1.3%, +4.2%) but **negative in 2026-06/07 (−0.6%, −0.9%)**, exactly the window that dominates the actual −$22,961 book. The overnight arm is negative on the average candidate too (−0.35%, fails winsor), worst in the recent months. **So holding-to-close is a real *regime-conditional tail harvest* that pays only when rockets are present — and the current regime is the losing one.** This reconciles docs 185/264 (holding won) with tonight's negative: both are true, in different regimes. It is *not* deployable now (current regime negative; the actual book confirms it), and the overnight flag is dead in every cut.

**The implication for the −$20k.** Selection alpha is closed (245–260); the exit-posture lever measures **negative in the current regime**; the winner-clipping is protective, not the disease. The precise statement (the ceiling forbids the flat "structural" claim): **the bleed is not fixable by holding *now* — the hold-to-close edge is a regime-conditional, 75%-tail-driven rocket harvest that is currently *off* (and the overnight variant is dead outright).** For the −$20k to be flipped by this lever, the regime would have to turn rocket-rich *and* the bot would have to be holding the rockets it selects — and selection is closed. Converging with docs 250–261, the profit-preserving action this points at is *less exposure in the current regime*, not *more holding*.

## §7 — What this night established

- **The mandate's highest-prior lever is NO-SHIP: the *blanket* posture flip is null-to-negative on the current book** (intraday arms' CIs all include 0; the overnight arm is the one real negative), and the doc-69 +$23K counterfactual evaporated under the realism gauntlet. The *conditional* rocket-gated hold (doc 264's actual thesis) was **not** tested and remains open.
- **Three adversarial stages each caught a different failure:** the novelty sweep predicted the sign (gap reversion); the panel's 4/4 DO-NOT-FREEZE stopped the first design from printing a *fantasy positive*; the hostile retest stopped *me* from overstating the result as a demonstrated negative when it is a null-leaning-negative. The number was not bent in either direction.
- **The honest deliverable is a kill + an instrument + an open door, not a flip** — and per the charter ("a falsified profit lever honestly killed is worth more than a fantasy shipped"), that is the win: the blanket flip is off the table, the exits stay on (validated as protective), the scoreboard tracks the regime, and the conditional hold is chipped for a pre-registered Stage-2.

**The one open door (successor, not tonight):** a *regime-gated* hold — hold-to-close **only** when a rocket-presence signal fires, sized small — is the single lever this night did *not* close (the ceiling's +1.58% average-candidate edge lives entirely in the rocket-rich months). It is not a selection edge (which is closed) but a *conditional posture*; its honest test is a pre-registered Stage-2 with the same gauntlet, and its prior is guarded (75% tail-driven, thin, and it needs the bot to actually hold the rockets it selects). The blanket flip is dead; the conditional one is untested.

**Artifacts** (parallel; live warehouse + running bot untouched): `data/research/doc280/{primary.json, ceiling.json}`, `scripts/_doc280_{counterfactual,posture_backtest,posture_scoreboard}.py`. Pre-registration commit `a104a0c`; novelty + panel + retest transcripts under the session's workflow dir.
