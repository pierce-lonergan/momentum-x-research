# 255 — The morning rocket WATCHLIST + collection engine: a better universe + a powered forward test of the two leads (NOT a runner-predictor).

**Author**: Claude Opus 4.8
**Date**: 2026-06-04
**Mandate**: Pierce — "trigger agents on each watchlist stock to extrapolate a predictor... we'll spend a month." Built as the *honest* version (doc 254 proved no positive predictor exists): a watchlist + data-collection engine.

## What it is (and what it deliberately is NOT)
doc 254 established there is **no positive ex-ante runner-vs-faller predictor** in this information set. So this engine does **not** claim to predict the runner. It does three honest, valuable things, daily:
1. **Builds the CANDIDACY watchlist** from the doc-253 structural signature — a genuinely better universe than a price-only gap scanner.
2. **Detects + LOGS the two real-but-underpowered leads** — insider Form-4 open-market buy clusters (via SEC EDGAR) and coiled catalysts (volume/price) — which were directionally real but only 5%-rare at n=20.
3. **Logs the realized OUTCOME** (gap-day close/open + rocket flag) next to the ex-ante features, so a **forward month accumulates a properly-powered, out-of-sample sample** to finally settle whether the 5%-leads convert.

The bet: the leads were too rare to conclude on (n=20). A month of forward collection (~20 gappers/day × ~20 days ≈ 400 fresh, out-of-sample gapper-rows) gives the power to test them honestly — *prospectively*, immune to the curation/hindsight traps that killed the prior "robust" findings.

## The candidacy screen (computable, ex-ante)
`scripts/rocket_watchlist_engine_doc255.py` — each day's gap≥8% / $0.50-20 / ADV≥$1M gappers, joined to the warehouse:
- **micro_float** (share_class_shares_outstanding < 5M, ticker_details) — weighted ×2 (the most universal rocket trait)
- **recent_reverse_split** (splits.parquet, split_from>split_to within 90d — the float-compression engine)
- **foreign_issuer** (ticker_details `locale` ≠ us)
- **prior_runner** (day_aggs: trailing-10d high/low ≥1.8 or a ≥+50% day — a *variance* flag per doc 254, logged not vetoed)
- **coiled** (≥20× RVOL + flat price — the doc-254 B4 flag)
→ a **candidacy score** that ranks the watchlist. (6/03 test: 26 gappers; top-candidacy SDOT/APLZ(+397% gap)/RUBI/HUBC; ATPC rocketed.)

## The lead detection (the part worth the month)
- **Insider Form-4 cluster (the strongest lead, doc 254):** for each watchlist name's CIK, query SEC EDGAR `submissions` for Form 4s filed in the trailing 21 days; flag open-market PURCHASES (transactionCode P) and count distinct owners → `insider_cluster` (≥2 buyers). The only true smart-money tell in the forensics (VIDA).
- **Coiled catalyst:** the computable volume/price flag (agent news-verification can enrich later).
Both are logged every day with the outcome, building the prospective dataset.

## Operation
- **Daily, after the close:** `MomentumX-RocketWatchlist` scheduled task (19:15, after `DataIngest` 17:30) runs `--catchup` (self-healing, de-duped) → logs `data/research/rocket_watchlist_log.jsonl`, then `--report` (running lead-vs-outcome rates) → posted to the **OPS_WATCHLIST** Discord channel. NO capital, NO live-trading change.
- **`--report`** shows each feature's rocket-rate + lift on the accumulated sample. The insider/coiled leads need ~weeks to reach power; the report makes the accumulation visible.
- **The month-end test:** once powered, re-test whether `insider_cluster` / `coiled` materially lift the rocket-rate out-of-sample. If yes → the first real, prospectively-validated edge. If no → the leads are confirmed noise and rocket-selection is closed for good.

## Honest framing
This engine **improves the universe** (candidacy watchlist) and **collects the data** to settle the two leads with power — it does **not** predict which name rockets (the evidence says that isn't there). It's the scientifically-valid way to spend the month: a prospective, out-of-sample test of the only signals that showed any sign, with every prior trap (curation, hindsight, tail-illusion, tz-bug) already mapped. Whatever the month returns, it will be trustworthy.

## Status
**No live change.** Engine built + validated on 6/03 (candidacy screen + EDGAR insider + logging end-to-end); scheduled daily; Discord-reporting wired. The collection begins now and powers the doc-254 leads over ~a month.
**Basis**: `scripts/rocket_watchlist_engine_doc255.py`, `scripts/run_rocket_watchlist.ps1`, `MomentumX-RocketWatchlist` task. **Predecessors**: 253 (the candidacy signature), 254 (no predictor; the 5%-leads to power), 246 (the shadow pattern this mirrors). **Memory**: [[gapper-universe-no-edge]].
