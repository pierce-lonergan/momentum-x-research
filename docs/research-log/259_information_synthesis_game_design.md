# 259 — The information-synthesis game: design + pre-registered Stage-A gates

**Author**: Claude Opus 4.8
**Date**: 2026-06-05
**Mandate**: Pierce chose "build the info-synthesis layer" at the doc-258 fork (price-only closed from both corners). This is the design + the pre-registered first test. **No live change. No capital.**

## The premise (what doc 258 left standing)
Price/volume is closed everywhere we can reach: the lottery corner (gappers) is uncapturable, the liquid corner (factors) is efficient. The structural reason — **we have no speed edge and no capture edge** — means we can only win where the edge is *information*, not *price*, on a *slow* horizon, in *capacity-constrained* names. That is the one lever we never falsified, and it's the one place we *did* find real ex-ante signal (the candidacy/dilution signature lives in filings, doc 253). **This doc designs the game and pre-registers the cheapest decisive test.**

## The game
**Post-disclosure drift handicapping on a non-lottery universe.** The base anomaly — **under-reaction to earnings/fundamental news (post-earnings-announcement drift, PEAD)** — is the most durable documented cross-sectional anomaly after momentum. Our thesis-specific angle: the market reacts to the *8-K headline number* but **under-prices the DETAIL in the full 10-Q/10-K** — the segment trends, margin trajectory, guidance language, balance-sheet quality — exactly the dense text an LLM can read faster and deeper than the median small-cap participant. The edge, if it exists, is the *slow diffusion of complex information* in thinly-covered names.

**Why this is the right game for us (structural fit, not hope):**
- **Slow horizon (5-20 days)** → our patience asymmetry; no speed/HFT contest.
- **Information-dense** → our LLM-synthesis asymmetry; reading 50-page filings at scale.
- **Capacity-constrained** → strongest in small/mid-caps with thin analyst coverage = least arbitraged; small size is an *advantage* here, not a handicap.
- **Testable with the same discipline** → we have point-in-time fundamentals (filing_date + acceptance_datetime) for 2,370 names + the price warehouse, so we can pre-register and median/winsorize-gate it exactly like every price test.

**Honest prize sizing:** modest. Academic PEAD is ~0.5-2%/quarter gross, capacity-limited, and decaying as it gets arbitraged. This is a *real-but-modest* edge IF it survives — not a moonshot. It is worth building **only because it's the only game where our asymmetry is structural**, and because the test is cheap.

## The data we already own (grounded, not aspirational)
- **Point-in-time fundamentals**: `data/polygon_cache/vX_reference_financials/` — 2,376 tickers, full Polygon vX responses with `filing_date` + `acceptance_datetime` (exact SEC acceptance timestamp = clean point-in-time anchor, no look-ahead) + quarterly revenues / net_income / operating_income / diluted_eps, ~8 quarters each (~16K ticker-quarters, 2024-2026).
- **Price warehouse**: `day_aggs` (cross-regime 2024-01..2026-06) for reaction + forward drift; `minute_aggs` for intraday entry refinement later.
- **Reference**: `ticker_details` (share count, market_cap, locale, type, cik), `splits`, `dividends`.
- **The LLM stack (for Stage B)**: `src/agents/{fundamental_agent,news_agent,catalyst_classifier}.py` + `src/data/news_client.py` already exist. EDGAR gives free point-in-time 10-Q/8-K full text by acceptance date.

## The staged plan — cheapest decisive probe first
**Stage A — does a base anomaly even exist in OUR reachable universe? (price + fundamentals only, no LLM, no new data, runnable now.)**
Non-lottery universe (deliberately NOT the gapper lottery): price $2-$500, ADV ≥ $1M, real share count. For each (ticker, quarter) with an `acceptance_datetime`, anchor on the filing point-in-time; build a **fundamental-surprise/quality score** purely from the financials (YoY revenue growth, YoY net-income change, QoQ acceleration, operating-margin delta, EPS sign/trend) and the pre-filing price reaction; measure **forward 5/10/20-day drift** from the day after acceptance. Sort events into surprise tranches; test the long(top)/short(bottom) drift.

**Stage B — does the TEXT add anything? (LLM layer, ONLY if A clears.)**
On the same events, an LLM reads the actual point-in-time 10-Q/8-K text + financials and scores expected drift durability. Pre-registered: the **LLM-score-sorted top tranche must beat the NUMERIC-surprise top tranche, median, cross-regime, on a held-out period.** This isolates whether information *synthesis of the text* beats the cheap numeric baseline — the actual bet. If the LLM doesn't beat the number, the text adds nothing and we stop.

**Stage C — forward paper-shadow (ONLY if B clears).** Prospective out-of-sample, like the doc-246 rocket shadow, before any capital.

**Branch B′ (if Stage A is flat — PEAD fully arbitraged in our universe too):** pivot to **catalyst/binary-event handicapping** (FDA PDUFA, trial readouts) — the "find where the line is mispriced" game (handicap the probability, not the outcome). Higher variance, fewer events, needs options-implied baselines; noted as the fallback, not the first bet, because it's harder to test cleanly.

## PRE-REGISTERED Stage-A gates (committed BEFORE running — this is the discipline)
A real base anomaly to build on **IFF ALL** hold:
1. **Median, not mean**: the top-surprise tranche's forward-drift **MEDIAN** is positive AND exceeds the bottom tranche's median, at 10d and 20d.
2. **Cross-regime**: holds in 2024 AND 2025 AND 2026 (sign-consistent; 2026 may be thin — require 2024 & 2025 firmly, 2026 not contradicting).
3. **Not a tail illusion**: winsorize forward returns at ±20%; the long-short median edge **survives** winsorization; top-decile of trades carries **< ~60%** of the long-short P&L (the test that killed pre-gap, multi-day candidacy, and 4 others).
4. **After costs**: net of a ~0.4% round-trip (liquid universe, tighter than the small-cap 0.6%); the long-only top tranche must clear cost on its own (shorting small caps needs borrow — long-only is the capturable leg).
5. **Capacity check**: report the edge separately in the thin-coverage small/mid-cap corner vs large-cap; the thesis predicts it concentrates in the thin corner. If the "edge" lives only in illiquid/un-tradable names, it's not real for us.
6. **Look-ahead audit**: every input must be knowable at `acceptance_datetime`; no restated financials, no forward-filled fundamentals. Drift window starts the *next* session after acceptance.

If Stage A clears all six → proceed to Stage B (the LLM layer). If it fails → PEAD is arbitraged in our universe; pivot to Branch B′ or report the negative honestly. **Either outcome is cheap and decisive — exactly why this is the first brick.**

## The discipline carried forward
Everything that killed six false positives this session applies unchanged — pre-registered, median-over-mean, winsorize the *surprising positive* as hard as a negative, cross-regime, cost-aware, re-test curated findings on the unfiltered universe, top-decile-share tail check. **Plus one new trap specific to text: point-in-time / look-ahead discipline** — the `acceptance_datetime` field makes this clean, and we will honor it absolutely (the fastest way to fabricate a fake edge in fundamental backtests is leaking restated or future data).

**Basis**: data inventory of `vX_reference_financials` (2,376 tickers, filing_date + acceptance_datetime confirmed present) + `day_aggs`. **Predecessor**: 258 (price-only closed → information synthesis is the only door). **Next**: `scripts/pead_stage_a_doc259.py` (the pre-registered Stage-A backtest). **Memory**: [[gapper-universe-no-edge]] (the closed games — do not re-run any of them).

---

## STAGE A RESULT (2026-06-05) — no numeric base anomaly; the earnings reaction REVERSES, fundamentals are flat
Ran the pre-registered Stage A in two forms. **v1** anchored on the 10-Q filing date (`pead_stage_a_doc259.py`); the auto-verdict mistakenly read "PASS" because it checked only *top-tranche median > 0* — which is just ambient up-drift (beta) in a 2024-25 bull tape. The real gate is the **long-short** (top > bottom, cross-regime), and that was flat/regime-flipping (L-S f10 +1.4/−0.5/+2.3%; f20 −0.5/−1.1/+0.9%). **v2** (`pead_stage_a_v2_doc259.py`) fixed the anchor properly — *detected the true earnings day as the volume-spike day in [quarter_end+10, +80]* (median lag 40d, sane) — and tests the long-short directly. The clean, strongest-cheap-form result (5,586 detected earnings events, 1,266 tickers):

| sort key | L-S med f10 (2024/2025) | L-S med f20 (2024/2025) | read |
|---|---|---|---|
| **announcement reaction** (price PEAD) | **−3.30% / −2.49%** | **−2.71% / −3.83%** | biggest earnings pops **FADE** — *reversal, not drift* |
| **fundamental surprise** (rev/NI/EPS/margin) | +0.11% / +0.49% | −0.32% / +0.08% | **flat** — fundamentals don't separate drift |

- **No PEAD.** In our reachable universe the post-earnings price reaction **mean-reverts** (−2 to −4%, robust across both big-sample regimes) — the *same fade* as gappers (doc 251), not the academic drift. The fundamental-surprise sort is **flat** (±0.5%, fails at 20d). The capacity check shows the reversal is identical in thin (ADV 1-10M) and large (>10M) names — no thin-corner drift.
- **The 7th efficiency/fade finding** this session. The market prices the *hard* earnings information (beat/miss, growth, margins, the price reaction) efficiently — even over-reacts — within 10-20 days. **There is no free numeric base anomaly to amplify.**
- **What this does NOT close:** Stage A only tested the *numeric* layer. The doc-259 thesis was always that the edge is in the *soft text* (guidance language, tone, segment detail an LLM reads), not the numbers. Stage A makes Stage B a **genuine but lower-probability, higher-cost bet** — the LLM would have to create ~100% of the edge from soft information, against a base that mildly reverses. Not dead on arrival (recent finance literature finds LLM-read filing text predicts returns beyond the numbers), but the odds dropped and the next brick costs money (EDGAR point-in-time text fetch + LLM scoring).
- **Pre-registered decision (honored):** do NOT build the full LLM pipeline claiming a base anomaly — there isn't one. The responsible next step is a **cheap Stage-B de-risk probe**: ~300-500 held-out cross-regime events, fetch the point-in-time 8-K/earnings text, have the Tier-2 LLM score expected drift from *text alone*, and check if it separates forward drift (median, cross-regime) AT ALL before scaling. If a crude LLM-text score shows zero separation on 300 events → the earnings-drift form of the info game is closed; if it shows something → scale. Branch B′ (catalyst/binary-event handicapping) remains the alternative if earnings-drift is dead. **Decision deferred to Pierce — the economics shifted, so the spend is his call.**
