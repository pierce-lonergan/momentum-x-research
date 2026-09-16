# 262 — Shadow processes vs the prod pipeline: a deep dive on 2026-06-08

**Author**: Claude Opus 4.8
**Date**: 2026-06-08
**Scope**: forensic comparison of every parallel MomentumX process that ran today against the prod paper-trading bot. Read-only. (Note: "doc 262" here is a ship-doc number; unrelated to the prod "D262" BOCPD-refit fire in `eod_2026-06-08.json`.)

## The roster — 8 scheduled processes ran today (all exit-0)
| Process | Time ET | Role | Capital? | Today's result |
|---|---|---|---|---|
| **PaperTrading** (prod) | 04:30 | the live long bot (4-phase, 7 LLM agents) | **YES (real paper acct)** | **−$1,172.71** broker |
| Lottery | 09:00 | freshness-tilted microcap momentum, ML MetaScorer | YES (**same acct**) | **0 trades** (meta SKIP'd all 10) |
| FaderShort | 15:50 | shorts chronic-fader gappers | YES (**same acct**) | **0 fills** (all 4 cands not-shortable) |
| Operator | 12 slots | AI ops watchdog | no (observe-only, **gated OFF**) | no-op |
| RocketShadow | 19:00 | doc-246 frozen-ML rocket predictor | no (passive) | couldn't score 06-08 (data lag) |
| RocketWatchlist | 19:15 | doc-255 candidacy + doc-261 red-flag | no (passive) | scored through 06-05 |
| DataIngest / Watchdog | 17:30 / hourly | warehouse refresh / health | — | ran; warehouse lags ~1d |

**Operational finding #1 — three trading bots share ONE paper account** (`PA3A2I4TN9AZ`, same `ALPACA_API_KEY`). Prod, Lottery, and FaderShort all book to the same equity. The "$216K vs Gemini" is the *combined* ledger — but only prod actually trades. This is a structural hazard: if Lottery ever fires (it sizes Aggressive-Kelly up to ~50% of total equity), it draws on prod's cash. Today it's moot (Lottery + FaderShort = 0 fills), but it is real coupling, not isolated shadow accounts.

## Prod (the only risk-taker today): −$1,172.71, and the loss was selection, not plumbing
- **Equity $216,252.93, 0 positions overnight.** Broker-truth P&L **−$1,172.71** (journal −$1,160.15; recon Δ −$12.56 — the broker-truth discipline holding).
- **The whole loss is one low-conviction long.** Prod opened exactly one genuine new long on conviction: **GMHS, BUY at MFCS 0.20** (gap 33%), **STOP_FILL → −$2,304** (broker-real). The rest is ghost/overnight-close noise: **TNGX** BUY MFCS 0.22 booked **journal +$747 but broker $0 — a phantom**; OCC (+$742) and ABAT (+$528) were broker-real closes of prior positions that the journal mis-attributed. The EOD failsafes **force-closed 3 ghost positions** (3/3) and cancelled 3 orphan orders — the phantom-P&L bug class is **still generating ~3 ghosts/day**, but the failsafes catch it (so −$1,172.71 is trustworthy).
- **Selection is the bleed; execution is healthy.** Today's post-close scorecard: **selection win-rate 30.8%**, median candidate return **−3.46%**, net edge/candidate **−1.07%**, no fill alpha (every window negative). **MFCS is near-useless (AUC 0.548; the highest-MFCS bucket has fwd-60 −17.1%)** — prod is forced to trade marginal 0.20-MFCS names because nothing scores high. Meanwhile the **execution close-path is sound** (adversary 10/11 survived, 0 wrapper bugs, only the known EOD-carry flag). BOCPD edge estimate: **mu_edge −$174/trade** (n=60) — still negative, i.e. the kill-switch corpus says the strategy is −EV.

## The trading shadows both went to cash today — for two *different, instructive* reasons
- **Lottery → 0 trades (its own ML vetoed everything).** The MetaScorer SKIP'd all 10 candidates; it has now gated out **100% of candidates for ~5 straight weeks (0 trades since ~May 5)**. Its silence is itself a verdict: a second, independent model agrees there's no edge in this universe. **Crucially, GMHS — prod's −$2,304 loser — was one of the names Lottery's MetaScorer SKIP'd today (score 0.169).** A shadow gate would have saved prod its entire day's loss.
- **FaderShort → 0 fills (the borrow wall, proven live).** Today it found 4 short candidates (TDIC, SDOT, NPT, SUNE) and **all 4 were rejected NOT SHORTABLE (HTB)**. Lifetime: **26 sessions, 67 short attempts, 0 fills** — every signal blocked by Alpaca's locate gate. **This is the live, empirical confirmation of the doc-260/261 finding: the fader/short edge is uncapturable not because the signal is wrong but because you cannot borrow these names.** The only deployable form of the fader signal is *avoidance on the long book*, never an actual short.

## The passive shadows: convergent negatives, one faint positive
- **RocketShadow (doc-246 ML) is anti-predictive out-of-sample.** Forward-OOS record (4 days, 93 candidates): **top-5% mean EOD −14.4%, top-1 mean −22.6%, top-1 hit-rate 0%, rocket-capture 0%.** The high-confidence picks systematically *lose* (06-02 DBGI −34.5%, 06-04 FOXX −25.8%, 06-05 BESS −13.9%) — the exact opposite of the in-sample +10.2% that motivated the bet. Per its own design ("the shadow tells the truth"), this is a **clean negative verdict on the rocket-ML regime bet.**
- **RocketWatchlist red-flag filter (doc-261) is weakly replicating forward.** 5 days, 133 gappers (4.5% rocket): HIGH red-flag (≥0.6, n=37) median oc_ret **−7.4%** vs LOW (<0.4, n=53) **−3.0%** → **−1.9pp** fade spread, correct direction, building power. The dilution-detection is crisp (06-05: BGMS 0.95 "reverse split + 99% dilution" → −45%; DEVS 0.9 "convertible note default" → −15%). The candidacy leads (insider/coiled/micro-float) sit at **~1.0× lift = no edge** (confirms doc 254). *Caveat: AIM hit red-flag 0.95 on 06-01 and still rocketed +62% — the filter is a risk-tilt, not a guarantee.*
- **Operator** is observe-only and **gated OFF** — contributes no edge signal; it's a heartbeat watchdog, not a strategy.

## Operational finding #2 — the shadows run a day behind
DataIngest (17:30) hadn't published 06-08 day-aggs by the time RocketShadow (19:00) and RocketWatchlist (19:15) ran, so **neither could grade today's gappers** (both latest-score 06-05). The red-flag filter therefore *can't yet be cross-referenced against prod's 06-08 trades* — the most valuable comparison (did the filter flag GMHS?) is blocked by pipeline lag. Worth fixing if we want same-day shadow-vs-prod grading.

## The unified story (what every process agrees on)
Every angle converges on one truth: **MomentumX has no durable long selection edge in this tape, and the short edge is unborrowable.**
- Prod: −EV (mu_edge −$174), selection 30.8% win, MFCS useless — bleeds on marginal longs.
- Lottery: its ML refuses to trade the universe at all (5 weeks of cash).
- FaderShort: can't borrow a single fader (67/67 blocked).
- RocketShadow: high-confidence picks lose −22.6% OOS.
- RocketWatchlist: candidacy gives no lift; only the red-flag *fade-avoidance* tilt shows faint, correct signal.
The one healthy subsystem is **execution** (adversary 10/11, clean close-path) — the plumbing works; the alpha is the problem. And the one faint forward-positive anywhere in the fleet is the **doc-261 red-flag fade-filter**.

## Three concrete, actionable takeaways
1. **A veto gate would have saved prod today.** GMHS (−$2,304, the entire day's loss) was independently rejected by the Lottery MetaScorer (0.169 SKIP) and is exactly the kind of low-MFCS, dilution-prone name the red-flag filter targets. The deployable move (when the red-flag filter reaches power, ~weeks): **soft-veto / size-down prod longs that the red-flag filter scores ≥0.6** — the first time the research arc would touch live behavior, earned by the forward data.
2. **Stop trying to short faders; the borrow wall is absolute.** FaderShort's 0-for-67 is conclusive: capital and scheduler time are being spent on a strategy that *structurally cannot fill*. Either repurpose it as a pure logging/avoidance signal feeding the prod red-flag gate, or retire it. The short edge is real on paper and dead in practice.
3. **The account coupling + the phantom-ghost generation are live operational risks.** Three bots on one paper account, plus ~3 phantom ghosts/day (TNGX today) that only the EOD failsafe catches — both are latent hazards. Isolate the bots onto separate paper sub-accounts, and keep hardening the close-path so ghosts aren't created in the first place (not just force-closed at EOD).

**Basis**: `data/reports/eod_2026-06-08.json`, `measurement_trend.jsonl`, `fill_backtest_2026-06-08.json`, `adversary_report.json`, `selection_study_2026-06-05.json`; `data/journals/journal_2026-06-08_*.jsonl`; `data/research/rocket_shadow_log.jsonl`, `rocket_watchlist_log.jsonl` + reports; `logs/lottery_2026-06-08.log`, `logs/fader_short_2026-06-08.log`; the scheduled-task definitions. **No live change. Read-only forensic.**
