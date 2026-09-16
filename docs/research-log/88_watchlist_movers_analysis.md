# 88 — Watchlist Movers Analysis: Can We Pre-Identify the Day's Huge Mover?

**Date:** 2026-04-29
**Branch:** develop
**Question (verbatim from operator):**
> "There is almost one stock in every watchlist day by day that moves
> extraordinary amounts of percentage. I want you to look at all the watchlists
> we have data for and isolate the complete list of high moving stocks the day
> they were in the watch list. I want to see if we can find some way to
> identify just the high movers within the watchlist… buy in as soon as the
> market opens or with 30 minutes and sell when we make a certain amount."

**Headline answer (one sentence):** the operator's intuition that "there's a
huge mover in nearly every watchlist day" is **factually correct** (84% of days
have a ≥30% mover), but every pre-emptive selection rule we tested — first-30-
min momentum top-K, premarket volume top-K — produces a **negative-expectancy
intraday strategy** because (a) 23% of top movers are sleepers that look weak
at 10:00 AM, and (b) of stocks that ARE strong at 10:00 AM, 72% reverse
through a -10% stop before any +20% target. The microstructure of this
small-cap watchlist universe is actively hostile to "buy strength" with
fixed targets.

---

## §1 — Data substrate

- `data/bar_recordings/{date}/{ticker}.json` — 88 trading day directories
  (2025-12-11 → 2026-04-29), 5,955 (date, ticker) JSON files total.
- Each file: 1-minute OHLCV+VWAP bars. Older files (pre-2026-04) include
  premarket (~04:00–09:30 ET); newer files are RTH-only. 79 of 88 dates
  have at least some premarket data for at least some tickers.
- We collapsed each (date, ticker) to RTH (09:30–15:59 ET) intraday metrics
  via `scripts/analyze_watchlist_movers.py` and ran a backtest via
  `scripts/backtest_first_30min_signal.py`.

Outputs:
- `data/audits/watchlist_movers_metrics.parquet` — 5,955 rows, 26 cols
- `data/audits/high_movers_catalog.parquet` — 188 rows where intraday
  max return ≥ 30%
- `data/audits/first_30min_backtest.parquet` — 5,954 rows with simulated
  10:00 ET entry + target/stop/time-stop exit
- `data/audits/{watchlist_movers_summary,first_30min_backtest_summary}.json`

---

## §2 — Confirmation: yes, there's a huge mover almost every day

Per-day distribution of the **top mover's intraday max-return-from-open**:

| Threshold | Days with a mover ≥ X | Share |
|-----------|----------------------|-------|
| ≥10%      | 88 / 88              | 100%  |
| ≥20%      | 85 / 88              | 96.6% |
| **≥30%**  | **74 / 88**          | **84.1%** |
| ≥50%      | 48 / 88              | 54.5% |
| ≥100% (doubler) | 19 / 88        | 21.6% |
| ≥200% (tripler) | 3 / 88         | 3.4%  |

The operator's intuition is empirically correct. The watchlist is producing
the right type of candidate ~every day.

**Top 10 movers across the full sample** (188-row catalog at
`data/audits/high_movers_catalog.parquet`):

| Date | Ticker | Open | Max Return | First-30 Max | Rank-30 | Backtest exit |
|------|--------|------|-----------|--------------|---------|---------------|
| 2026-03-16 | WNW  | $2.25 | **+501%** | +79% | #1 | stopped −10% |
| 2026-03-25 | UGRO | $8.07 | +350% | +9% | #21 | target +20% |
| 2025-12-22 | FJET | $7.73 | +307% | +34% | #2 | target +20% |
| 2026-01-15 | SPHL | $7.79 | +175% | +3% | #24 | target +20% |
| 2026-03-26 | EEIQ | $4.80 | +165% | +100% | #1 | target +20% |
| 2026-03-23 | PTLE | $4.75 | +162% | +22% | #2 | target +20% |
| 2026-02-20 | ABTS | $2.15 | +156% | +43% | #1 | stopped −10% |
| 2025-12-15 | AMCI | $3.62 | +153% | +33% | #1 | target +20% |
| 2026-03-27 | ARTL | $7.99 | +149% | +56% | #1 | stopped −10% |
| 2025-12-12 | RYM  | $20.71 | +143% | +43% | #2 | target +20% |

Read column 4 vs column 5: **WNW ran 501% intraday but stopped us out at
−10% first.** UGRO and SPHL ran 350% / 175% but were ranked #21 / #24 by
first-30-min momentum — we'd never have bought them.

---

## §3 — Can first-30-min momentum identify the day's top mover?

For each day we ranked all watchlist tickers by **first_30min_max_return**
(highest = rank 1). How often is the actual top mover (highest full-day
max return) caught in the top-K of that ranking?

| K | True top movers in top-K | Share |
|---|--------------------------|-------|
| 1 | 36 / 88 | 40.9% |
| 2 | 51 / 88 | 57.9% |
| 3 | 56 / 88 | 63.6% |
| 5 | 62 / 88 | 70.5% |

So the signal HAS predictive power: by 10:00 AM, the day's top mover is in
the top-5 of first-30-min ranking 70% of the time. **But that's a watchlist-
filtering signal, not a buy signal.** The next section shows why.

### §3.1 — Distribution of first_30 for true top movers

For the 88 daily top movers, first_30min_max_return distributes as:

| Pctile | first_30 |
|--------|---------|
| min    | +0.0% |
| p10    | +6.3% |
| p25    | +10.5% |
| **p50** | **+18.8%** |
| p75    | +34.5% |
| p90    | +46.8% |
| max    | +99.8% |

**20 of 88 (23%) of true top-movers were up <10% at 10:00 AM** — they were
sleepers. Another 38 (43%) were up 10–30% — they LOOK like the day's mover
but don't yet show breakout-grade strength.

---

## §4 — The backtest result (the punch line)

**Strategy under test:** at 10:00 ET, rank all watchlist tickers by
first_30min_max_return. Buy top-K equal-weight at the 10:00 ET bar's open.
Exit on whichever fires first: target hit, stop hit, 15:55 ET time-stop.

### §4.1 — Top-K sweep at +20%/-10%

| K | Trades | Avg/trade | Win | Target | Stop | Compound |
|---|--------|-----------|-----|--------|------|----------|
| 1 | 88     | **−3.15%** | 25.0% | 14.8% | **62.5%** | −96.4% |
| 2 | 176    | −1.00% | 34.1% | 19.9% | 55.1% | −70.5% |
| 3 | 264    | −0.80% | 36.4% | 16.7% | 50.4% | −59.3% |
| 5 | 440    | −0.68% | 40.0% | 13.6% | 43.4% | −50.6% |

**Every K is negative-expectancy.** Tighter rank → more concentration on
the "obvious" early ripper, which reverses harder.

### §4.2 — Stop/target sensitivity (top-3 picks)

| Config | Avg/trade | Win rate | Compound |
|--------|-----------|----------|----------|
| +10%/−5%      | −0.24% | 33.0% | −70.5% |
| +20%/−10%     | −0.80% | 36.4% | −97.7% |
| +20%/−15%     | −0.78% | 40.5% | −98.6% |
| +30%/−15%     | −1.46% | 35.6% | −99.9% |
| +50%/−20%     | −1.84% | 35.6% | −99.99% |
| **hold-EOD-no-stop** | **−2.99%** | 36.0% | −100% |

**No stop/target combination produces positive expectancy.** Removing the
stop entirely makes it WORSE — the average per-trade return drops to −3%
because losers run further than winners on average. The market is teaching
us that buying the morning ripper is the wrong side of the trade on this
universe.

### §4.3 — Filtering only to "already up ≥30% in first 30 min"

| Filter | n | Avg/trade | Win | Stop hit |
|--------|---|-----------|-----|----------|
| first_30 ≥ 30% | 57 | −3.04% | 24.6% | **71.9%** |
| first_30 ≥ 50% | 15 | −3.52% | 20.0% | **73.3%** |

The "obvious" winners get worse as we tighten the filter. Stocks that
have already moved 30–50% in 30 minutes are reverting, not extending.

---

## §5 — Why "buy strength" doesn't work here

Three intersecting causes:

### §5.1 — Time-to-peak is late-day skewed

For the 88 daily top movers, **median time-to-peak is 168 minutes after open
(≈12:18 PM ET)**:

| Bucket | Share of top movers |
|--------|---------------------|
| 0–30 min   | 14.8% |
| 30–60 min  | 6.8% |
| 60–120 min | 17.0% |
| 120–240 min | 21.6% |
| **240–390 min** | **39.8%** |

**40% of the day's biggest movers don't peak until after 1:30 PM ET.**
Buying at 10:00 with a +20% target essentially catches the morning
rippers (15%) and misses the slow-burners (40%). The slow-burners often
don't even appear strong at 10:00 AM (see §3.1 — 23% are up <10%).

### §5.2 — Microstructure friction on naive buy-at-open

5,955 watchlist tickers held from 09:30 ET open to 10:30 ET (60-min flat
strategy):
- Mean return: **−0.11%**
- Win rate: 45.9%

There is a small-but-real **negative drift** in the first hour for the
typical watchlist ticker. This is consistent with: (i) PFOF / wholesaler
internalization extracting a few bps from each retail buy, (ii) overnight-
gap-fade as market makers fade morning enthusiasm, (iii) the watchlist
itself being momentum-biased so the "average" ticker is at a price that's
already pricing in good news.

### §5.3 — Premarket volume is anti-correlated

Top-3 picks ranked by premarket volume (across 79 dates with PM data, 237
trades), held to RTH close:
- Mean close_return: **−2.27%**
- Win rate: 38.8%
- Compound: **−99.98%**

Premarket volume identifies the stocks RETAIL is hyping — and those fade
the hardest after open. This is the well-documented "premarket pump
exhaustion" effect.

---

## §6 — What this means for the operator's strategy hypothesis

The operator's framing was:
> "the strat would be easy if we could identify these, buy in as soon as
> the market opens or within 30 minutes and sell when we make a certain
> amount."

The data forces a hard rejection of this framing on this universe:

1. **"Easy if we could identify them"** — partly. We CAN narrow the field
   to top-5 by first-30-min momentum and capture 70% of the day's top
   movers in that filter. So as a watchlist-pruner the signal works.

2. **"Buy as soon as market opens or within 30 minutes"** — this is where
   it breaks. Buying at 10:00 with any reasonable stop/target loses money
   under every config we tested. The reasons are structural:
     - 23% of top movers haven't moved by 10:00 (we'd miss them entirely)
     - 72% of "already strong by 10:00" stocks reverse 10% before going +20%
     - 40% of top movers don't peak until afternoon (we'd be exited via
       stop or time-stop long before)

3. **"Sell when we make a certain amount"** — fixed targets fight the
   path-dependence of the move. The target hit-rate is ~15–20%, the stop
   hit-rate is ~50–70%. The asymmetry is wrong because pullbacks of 10%+
   are NORMAL inside a 100%+ runner.

This is the same conclusion the broker-truth aggregation reached
independently in PROMPT_10: **intraday strategy on this universe lost
$6,223 over 270 actual trades**. The backtest above explains WHY: the
underlying market structure is hostile to "buy strength early."

---

## §7 — What MIGHT work (untested hypotheses, ranked)

These are honest hypotheses for next-step experiments — none yet
validated. Listed in rough order of how much the data above supports them:

1. **Late-day breakout entry (12:00–14:00 ET).** Median peak is 12:18 PM.
   A "buy after 11:30 break of morning high with confirmation" rule would
   structurally align with when the mover actually moves. Test: replicate
   §4 with entry at 11:30/12:00/13:00 ET.

2. **Buy weakness, not strength.** A stock up 50% intraday that pulls back
   to VWAP often resumes; the early-ripper top-K rule buys at the wrong
   point of the pullback. Test: buy on VWAP-reclaim after a ≥10% pullback
   from morning high.

3. **Short the morning ripper.** If "buy top-K of first-30-min" is
   negative-expectancy, the symmetric short trade is positive-expectancy —
   constraints permitting (HTB, locate availability). The bot's existing
   short-side path needs the helper variant from PROMPT_10 §7 first.

4. **Combine with fundamental catalyst filter (MAGNA-N).** Per PROMPT_10
   Track A, the +$40,985 CRCA win was a real-earnings catalyst day.
   Filtering the watchlist to MAGNA-N≥3 + first-30-min top-5 may produce
   a much smaller, much higher-quality candidate set. Discovery rate at
   that intersection would be ~5–10/year, but per-trade expectancy could
   flip positive.

5. **Trail stops on the morning rippers, accept low win-rate / fat-tail.**
   The top-3 backtest's max return was +214.8% — a few moonshots exist
   in the noise. A trailing-stop strategy (e.g., enter at 10:00, trail
   25% from highest-high-since-entry) might convert those moonshots into
   realised P&L while accepting an even worse win rate. Worth testing.

6. **Stay halted on the long side; rebuild from §7.1–4 evidence first.**
   Most defensible. The existing strategy is documented as a net loser;
   restarting without an evidence-backed change is not an experiment, it's
   a coinflip with extra steps.

---

## §8 — Stop conditions (per PROMPT_10 §11.3 framing)

| Question | Result |
|---|---|
| Did the watchlist contain the day's huge movers? | YES — 84% of days, ≥30% mover |
| Could a simple rule pre-identify them? | PARTIALLY — top-5 of first-30-min captures 70% |
| Would buying that subset at 10:00 with +20%/-10% have made money? | **NO — every config ≤ −0.24% per trade** |
| Is the operator's "buy early, sell at target" framing actionable? | **NO on this universe** |

This is informational, not bug-discovery (discovery rate stays at 36/0).
The work directly answers the operator's question with a quantitative
"no" on the proposed strategy and a ranked list of what to test next.

---

## §9 — Files written

| Path | Purpose | Rows |
|------|---------|------|
| `scripts/analyze_watchlist_movers.py` | Per-(date, ticker) RTH metrics | — |
| `scripts/backtest_first_30min_signal.py` | First-30-min entry backtest + catalog | — |
| `data/audits/watchlist_movers_metrics.parquet` | All 5,955 watchlist-day rows | 5955 |
| `data/audits/high_movers_catalog.parquet` | All ≥30% intraday moves | 188 |
| `data/audits/first_30min_backtest.parquet` | Simulated trades + ranks | 5954 |
| `data/audits/watchlist_movers_summary.json` | Top-mover distribution + signal stats | — |
| `data/audits/first_30min_backtest_summary.json` | Backtest results + top-30 catalog | — |

---

## §10 — Next-step queue (operator decision)

Combined with PROMPT_11's queued path α decision:

- **α-extend**: still has an evidence base (CRCA was a real catalyst).
  This doc neither supports nor refutes it.
- **α-restrict**: this doc HARDENS the case against α-restrict — the
  intraday small-cap-momentum design space is structurally negative-
  expectancy under the operator's proposed entry/exit rules.
- **α-defer**: this doc supports defer until at least one of §7.1–4 is
  empirically tested.
- **NEW: α-rebuild-around-late-breakout**: rank-1 hypothesis from §7.
  Lowest implementation cost (just shift entry time + replicate backtest);
  highest information-per-dollar of next session's effort.

Recommendation framing (evidence not advocacy): the cheapest, highest-
information next step is to rerun the §4 backtest with entry at 11:30 /
12:00 / 13:00 ET and see whether the avg-per-trade return inverts. If it
does, that's a strategy. If it doesn't, the operator should stay halted
and consider whether this universe is the right universe at all.
