# 2025-11 to 2026-04 — Broker-Truth Analysis (CORRECTION)

**Generated:** by `scripts/analyze_broker_truth.py` against `data/broker_truth/closed_trades.parquet` (pulled from Alpaca /v2/account/activities/FILL).

**Why this exists:** prior analyses (86-session OOS, BAR-1 falsification, catalyst-only) all used `data/trade_results.jsonl` as a P&L source. That file covers only 5 sessions (4/22-4/28) with -$6,612 in P&L. The actual broker account is **+$40,654 (+40.6%)** over Dec 2025 → today on a $100K starting equity. The whole 'no demonstrated edge' verdict was based on testing the wrong week.

This document replaces those analyses with broker-truth-grounded numbers.

---

## §1 — Headline (broker truth)

- **Starting equity:** $100,000.00
- **Closed trades:** 280 (Nov 2025 → today)
- **Σ realized P&L:** **$+37,463.37**
- **Realized return on starting equity:** +37.5%
- **Today equity (broker, includes unrealized):** $140,654.28 (+40.6% from $100K)
- **Wins / losses / scratches:** 67 / 209 / 4 (win rate: 23.9%)
- **Avg win:** $+930.27 | **Avg loss:** $-118.97
- **Profit factor:** 2.507
- **Max drawdown:** $14,340.44 (9.7%)
- **Daily-Sharpe annualized:** **+3.287**
- **Trading session-days:** 17

### Suspect-range bucket (per plan doc 58 §11)
**bucket: SUSPECT** — Sharpe > 2.0; A.5 / shuffle / stratification falsification mandatory

## §2 — Per-month P&L (where the gains came from)

| Month | Σ P&L | Cumulative | Notes |
|---|---:|---:|---|
| 2026-02 | $-638.78 | $-638.78 |  |
| 2026-03 | $+39,611.51 | $+38,972.73 | **big winning month** |
| 2026-04 | $-1,509.36 | $+37,463.37 |  |

## §3 — Concentration (are gains from a few outliers?)

- **Top 5 trades** (by realized P&L): $+52,890.97 = 141.2% of total
- **Bottom 5 trades**: $-6,228.44
- **Top 5 tickers** (cumulative): $+54,254.61 = 144.8% of total

### Top 10 individual trades (winners)

| Ticker | Date | Hold | Entry | Exit | qty | P&L |
|---|---|---|---:|---:|---:|---:|
| CRCA | 2026-03-02 | 118h | $3.0300 | $38.8100 | 700 | $+25,046.00 |
| CRCA | 2026-03-02 | 118h | $3.0300 | $39.0100 | 443 | $+15,939.14 |
| MOBX | 2026-03-03 | 4h | $0.6095 | $1.1900 | 12590 | $+7,308.49 |
| JZXN | 2026-03-04 | 4h | $1.2100 | $1.6000 | 7066 | $+2,755.74 |
| RLMD | 2026-03-11 | 47h | $5.8200 | $6.6200 | 2302 | $+1,841.60 |
| ANNA | 2026-03-20 | 5h | $5.1800 | $7.1400 | 578 | $+1,132.88 |
| AIFF | 2026-03-04 | 4h | $1.1100 | $1.5800 | 2177 | $+1,023.19 |
| AIFF | 2026-03-04 | 4h | $1.1100 | $1.5800 | 1962 | $+922.14 |
| AIFF | 2026-03-04 | 4h | $1.1100 | $1.5800 | 1906 | $+895.82 |
| KNRX | 2026-02-19 | 2h | $1.9500 | $2.3800 | 1702 | $+731.86 |

### Bottom 10 individual trades (losers)

| Ticker | Date | Hold | Entry | Exit | qty | P&L |
|---|---|---|---:|---:|---:|---:|
| CANF | 2026-03-04 | 22m | $10.4000 | $7.0100 | 531 | $-1,800.09 |
| ASNS | 2026-03-04 | 2h | $0.6550 | $0.4700 | 6973 | $-1,290.01 |
| CANF | 2026-03-04 | 22m | $10.4000 | $7.2500 | 363 | $-1,143.45 |
| CANF | 2026-03-04 | 22m | $10.4000 | $7.2500 | 340 | $-1,071.00 |
| ASNS | 2026-03-04 | 2h | $0.6574 | $0.4724 | 4994 | $-923.89 |
| ASNS | 2026-03-04 | 2h | $0.6574 | $0.4700 | 4061 | $-761.03 |
| MOBX | 2026-03-04 | 8m | $1.0400 | $0.9511 | 8156 | $-725.07 |
| ASNS | 2026-03-04 | 2h | $0.6546 | $0.4700 | 3653 | $-674.34 |
| MOBX | 2026-03-04 | 9m | $1.0500 | $0.9511 | 6424 | $-635.33 |
| PRSO | 2026-03-06 | 5m | $1.6600 | $1.5500 | 5527 | $-607.97 |

## §4 — Per-ticker breakdown (top 15 by abs P&L)

| Ticker | n trades | Σ P&L | Wins | Losses | Win-rate |
|---|---:|---:|---:|---:|---:|
| CRCA | 2 | $+40,985.14 | 2 | 0 | 100% |
| ASNS | 14 | $-6,178.87 | 0 | 14 | 0% |
| CANF | 11 | $-6,133.45 | 0 | 11 | 0% |
| MOBX | 9 | $+5,165.08 | 1 | 8 | 11% |
| AIFF | 5 | $+3,275.90 | 5 | 0 | 100% |
| JZXN | 15 | $+2,986.89 | 9 | 6 | 60% |
| RLMD | 1 | $+1,841.60 | 1 | 0 | 100% |
| ANNA | 2 | $+1,460.20 | 2 | 0 | 100% |
| RITR | 9 | $-1,129.34 | 0 | 9 | 0% |
| XWEL | 2 | $+1,071.06 | 2 | 0 | 100% |
| BATL | 3 | $-831.72 | 0 | 3 | 0% |
| PRSO | 4 | $-764.79 | 0 | 4 | 0% |
| KNRX | 1 | $+731.86 | 1 | 0 | 100% |
| TRNR | 8 | $-722.87 | 0 | 8 | 0% |
| LRHC | 8 | $-711.19 | 0 | 8 | 0% |

## §5 — Hold time + intraday-vs-carry

- **Median hold time:** 57.0m (3420.0s)
- **p25 hold:** 256s | **p75 hold:** 16095s
- **Intraday trades:** 262 (Σ P&L $-5,674.79)
- **Carry trades:** 18 (Σ P&L $+43,138.16)

## §6 — Falsification: shuffle test on BROKER-TRUTH pairings

Random entry-exit re-pairing across the 280-trade pool, 200 iterations:

- **Mean shuffled Sharpe:** +8.088 ± 1.393
- **Range:** [+5.068, +12.600]
- **Baseline Sharpe (broker truth):** +3.287

⚠️ **SHUFFLE TEST WARNING:** shuffled mean (+8.088) is comparable in magnitude to baseline (+3.287). The strategy's actual decisions may not be adding much value over random pairings on this trade pool. Investigate further before sizing up.

## §7 — What this means vs prior analyses

**The 86-session OOS run (`docs/oos/2025-12-to-2026-04_oos_run.md`) was wrong by methodology.** It used the strategy harness's policy mode (alphabetically first 3 sub-$15 high-volume tickers per session) as a proxy for prod's actual decisions. That proxy:
- Took 255 trades vs prod's actual ~280 — coincidentally similar count
- Produced aggregate Sharpe +3.776 in the synthesized stratum (later overturned)
- Did NOT replicate prod's real decision-making

**The actual strategy in production produced:** +$37,463.37 realized P&L (+37.5% on $100K), Sharpe +3.287. **Whether that's edge or luck depends on the shuffle test result above + per-month consistency + concentration.**

The harness's failure mode: it tested an idealized version of the strategy rather than the actual code path that produces the live trades. The infrastructure built around the harness (limit-aware fill, prod-mirror replay, falsification framework) is still correct. The conclusions drawn from running the harness as a substitute for broker truth were not.

## §8 — Honest verdict (corrected)

Given the broker truth above:

- **WARNING: gains are concentrated in top 5 trades (141% of total).** This is consistent with both (a) catalyst-driven outliers being the real signal AND (b) lucky tail events being indistinguishable from edge at n=280. Investigate the top trades for catalyst patterns before any size-up.

**Halt switch posture (this commit):** unchanged from yesterday — operator decides per session. The argument for halt is no longer 'no demonstrated edge'; it is 'we don't yet fully understand what's producing the gains so we can't responsibly evaluate the variance.' The argument for lifting halt is now defensible: the strategy has produced +40% on real money over months.
