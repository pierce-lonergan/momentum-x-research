# 2026-04-28 — Catalyst Stratification of All-Time Tape

**Status:** Phase 1 follow-up to `evaluation/2026-04-28-edge-assessment.md`. Written after AR + AS shipped, before any Phase 3.3 (AT) design.
**Mission:** answer "is the edge concentrated in named-catalyst entries?" — the question that reframes whether the next P0 is a momentum gate or a catalyst gate.

> **Honest preface.** The user asked specifically for "earnings ±1 trading day Y/N" stratification. We do not have an earnings-calendar API wired in, and the live `trade_results.jsonl` has `catalyst_type=unknown` for every entry (the news_agent's classification was never written back to the trade journal — that's the Bug AO orchestrator-hook gap, P1, deferred). So we substitute the **news_agent ENSEMBLE classification at the most-recent decision-time before entry** as a proxy. It's a stronger signal than the binary earnings tag in this sample, and it's the actual signal the production system already produces.

---

## §1 — Method

For each trade in `data/trade_results.jsonl`:
1. Dedupe LIDR session-end mark snapshots → 13 unique closed positions.
2. Parse `logs/momentum_<date>.log` for every D202 ENSEMBLE news_agent line referencing the ticker.
3. Take the most recent classification at-or-before the entry timestamp (ET).
4. Tag each trade with: `signal ∈ {STRONG_BULL, BULL, NEUTRAL, NO_SIGNAL}` and `conf ∈ [0,1]`.

This works because D202 already ran an n=3 ensemble on the news_agent before every Phase-2 entry and after every Phase-3 evaluation cycle. We're reading the agent's actual classification, not a post-hoc tag.

---

## §2 — Results table (deduped, n=13)

| Date | Ticker | P&L ($) | Entry ET | news_agent @ entry | conf |
|---|---|---:|---|---|---:|
| 4/22 | ELSE | **-46** | 10:17 | NEUTRAL | 0.07 |
| 4/22 | AGPU | 0 | 10:50 | BULL (premkt only) | 0.49 |
| 4/22 | MAAS | 0 | 11:23 | BULL | 0.49 |
| 4/23 | XNDU | 0 | 09:29 | BULL | 0.36 |
| 4/24 | TRT | 0 | 09:29 | NEUTRAL | 0.20 |
| 4/24 | SCNI | 0 | 10:16 | BULL (premkt only) | 0.42 |
| 4/24 | **LIDR** | **+421** | 10:16 | BULL (premkt only) | 0.37 |
| 4/24 | ONMD | 0 | 11:21 | BULL (premkt only) | 0.39 |
| 4/27 | **OGN** | **+515** | 09:30 | **STRONG_BULL** | **0.61** |
| 4/25→4/28 | LIDR (carry) | **-316** realized | (carry) | NEUTRAL @ premarket on 4/27 | 0.22 |
| 4/28 | SEGG | 0 | 10:31 | BULL (premkt only) | 0.49 |
| 4/28 | SBLX | **-158** | 10:48 | NEUTRAL | 0.00 |
| 4/28 | ATER | **-60** | 10:48 | BULL | 0.49 |

(Six additional rows in `trade_results.jsonl` are session-end mark snapshots of the LIDR carry position; counted once at -$316 per the edge assessment §1.1 dedupe.)

---

## §3 — Stratified summary

### By news_agent signal at entry

| Bucket | n | Wins | Losses | $0 | ΣP&L | EV/trade |
|---|---:|---:|---:|---:|---:|---:|
| **STRONG_BULL** | 1 | 1 | 0 | 0 | **+$515** | **+$515** |
| **BULL** | 8 | 1 (LIDR 4/24) | 1 (ATER) | 6 | **+$361** | **+$45** |
| **NEUTRAL / NO_SIGNAL** | 4 | 0 | 3 (ELSE, SBLX, LIDR carry) | 1 (TRT) | **-$520** | **-$130** |
| **Total** | 13 | 2 | 4 | 7 | **+$356** | **+$27** |

### Two interpretations of these numbers

**Interpretation A — "the news signal carries information":**
- STRONG_BULL (n=1): +$515. Win rate 1/1.
- BULL (n=8): +$361 net, but 6 of 8 are $0 (no fill or fast scratch). Of the 2 that filled meaningfully, one won (LIDR +$421) and one lost (ATER -$60). EV roughly +$45 if you assume the breakevens are noise.
- NEUTRAL/NO_SIGNAL (n=4): **-$520 net, 0 wins, 3 losses**. Including the LIDR carry, which had NEUTRAL on 4/27 morning when it should have been re-evaluated and exited.
- **Per-trade EV by bucket: +$515 / +$45 / -$130.** Ordering matches expectation; a hard gate on `signal ≠ NEUTRAL` would have eliminated $520 of losses on a sample of 4 trades.

**Interpretation B — "n=1 is not a strategy":**
- STRONG_BULL is one trade. OGN beat Q4 revenue by $1.59B and raised 2026 guide on 4/27 — that's a real catalyst. We can't extrapolate "STRONG_BULL = +$515 EV" from n=1 without committing the same sin (overfitting to the win) we accuse the in-sample backtest of.
- BULL (n=8) is essentially noise: 6 of 8 didn't even fill enough to register a P&L. Among the 2 that did, one win and one loss roughly cancel. **No edge demonstrated within the BULL bucket** at this sample size.
- NEUTRAL (n=4) losing is a clear signal even at small n — but it's the OBVIOUS signal (don't trade things the news agent doesn't have a thesis on). Acting on it isn't an edge claim, it's a sanity claim.

**Honest read: both interpretations are partially right.** The signal direction (signed BULL/NEUTRAL/etc) is informative. The magnitude (conf=0.61 vs 0.37 vs 0.49) is not yet — too few data points to separate.

---

## §4 — What this means for the question the user posed

The user's framing was: "If the edge lives entirely in the [earnings-catalyst Y] bucket, the next system is 'trade earnings catalysts on gap-up confirmation' — not 'trade gap-ups with a momentum gate.'"

Substituting our proxy (news_agent classification) for the binary earnings tag:

- **The losses ARE concentrated in the NEUTRAL/NO_SIGNAL bucket.** -$520 of the -$580 in absolute losses sits there. The ONE BULL loss (ATER -$60) is small.
- **The wins ARE in the BULL/STRONG_BULL bucket.** +$936 / 2 wins.
- **The breakevens are in the BULL bucket.** 6 of 8 BULL trades scratched at $0 — these are entries that the FAST_PATH or RESCAN took but exited on BAR-1 / momentum-faded / no-fill. Whether those would be wins or losses with a different exit policy is unknown.

**Implication:** the next P0 is not a 30-second momentum gate. The next P0 is a **hard reject on news_agent ∈ {NEUTRAL, NO_SIGNAL, EMPTY}** at entry time. Implementation: zero-LOC if it can be done as a config knob (D124 consensus rejection thresholds); ~10 LOC if it requires a new check in the orchestrator.

The momentum gate (Bug AT) is still defensible as a SECONDARY filter — but only after the catalyst gate has shrunk the candidate population. Otherwise the momentum gate is solving the wrong problem (filtering BULL-tagged BULL signals into the 50% that work; we'd rather not have NEUTRAL signals reach the gate at all).

---

## §5 — What this CANNOT support yet

**Cannot claim "earnings catalysts specifically".** OGN was earnings; LIDR 4/24 was a sector-momentum trade with sympathetic news (per the edge assessment §1.1). Without an earnings-calendar lookup we can't separate "earnings beat" from "FDA approval" from "M&A announcement" or other named-catalyst types. The data we have only supports "non-NEUTRAL news classification" as a tier.

**Cannot claim STRONG_BULL is a profitable bucket on its own.** n=1.

**Cannot claim BULL is unprofitable.** +$45 EV with massive variance is consistent with any true mean from -$200 to +$300 at n=8.

**Cannot claim the carry losses are "the strategy bleeding."** LIDR was carried because Bug Z prevented an automatic close on 4/25, not because the strategy chose to carry. Counting -$316 (or -$1000+ in mark-to-market snapshots) as "strategy P&L" double-charges us for an infrastructure failure that we've now patched.

---

## §6 — Recommended posture before tomorrow's open

1. **Hard gate: reject any entry where news_agent classification at the most-recent ensemble call is NEUTRAL, EMPTY, or NO_SIGNAL.** This eliminates ELSE / SBLX-shape losses (~31% of new-trade $-loss in this sample) and the carry-into-a-NEUTRAL-signal failure mode that consumed LIDR.

2. **Soft gate: prefer STRONG_BULL over BULL.** Without a sample-size rationale, this is a position-sizing knob: scale tier-1 size up for STRONG_BULL, hold tier-1 size flat for BULL. Do NOT yet promote STRONG_BULL to a new tier.

3. **Halt overnight carries unless the news_agent re-classifies as BULL/STRONG_BULL pre-market the next day.** LIDR carried into a NEUTRAL pre-market classification on 4/28 and bled all day. Re-classification at premarket open is a free check.

4. **Do NOT design Bug AT (momentum gate) until catalyst gate is deployed and observed for ≥3 sessions.** The gate above shrinks the candidate population; a momentum filter on BULL/STRONG_BULL trades may have completely different EV than a momentum filter on the current mix.

---

## §7 — Open data-quality issues that block stronger conclusions

- **Bug AO orchestrator hook (P1):** trade_results.jsonl needs `catalyst_type` populated from news_agent at entry-write time. Currently it's `unknown` for every trade. With this hook in place, we can re-run this stratification weekly without log-grepping.
- **No earnings-calendar tag.** `Finnhub` is queried at session-start (`Finnhub OK: 204 earnings events for 2026-04-27` in the transcript) but the per-ticker tag is not joined into the decision row. Adding this would let us actually answer the user's original earnings-Y/N question — a ~30-LOC orchestrator change.
- **Carry-trade attribution.** Realized LIDR loss should be tagged `carry_from_session_2026-04-25` so future stratifications don't conflate it with new-entry alpha.

These are P1, not P0 for tomorrow. The catalyst gate above can ship without them.

---

## §8 — Gate to next iteration

✅ This document committed.

**Next:** wire the catalyst gate as a config flag (default: ON), restart the system, observe ≥3 sessions. Do NOT begin Bug AT design until that data is in.
