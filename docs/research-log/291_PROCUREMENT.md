# 291 PROCUREMENT MEMO — what Stage 3/4 of the volatility door would need (DECISION: PIERCE'S)

> **doc-293 UPDATE (2026-07-12) — the entitlement probe changes this memo's economics.** Read-only probe of
> the EXISTING Polygon key: historical option-contract EOD aggregates are **ENTITLED, ~2-year rolling window,
> at 5 calls/minute** (reference contracts entitled; only the IV *snapshot* endpoint is 403-paywalled). IV is
> therefore computable at **$0** by Black-Scholes inversion from entitled option closes — built, validated
> (`_doc293_iv_collector.py`; AAPL median 30d ATM IV 25.9%, correct scale), and collecting now. The full
> 150-name × 22-month Stage-3 panel is a **multi-night rate-limited drip (~9 nights at a 4h/night budget)**.
> **What a paid tier now buys is SPEED and vendor-grade IV (dividends/rates/smoothing), not access.** The
> spend decision is accordingly lower-stakes: wait ~a week at $0, or pay to compress it. Probe artifacts:
> `data/research/doc293/entitlement_probe*.json`.

**Status**: Stage 2 passed its frozen gates and survived a 3-skeptic adversarial fleet (doc 291). Honest
numbers for this decision: the **durable improvement is ~3.6%** pooled QLIKE (the 5.9% headline is
shock-loaded — the April-2025 vol spike carries ~42% of it); one recent quarter (2026Q1) was flat-to-negative;
and the gain is carried by **generic vol-literature features** (intraday RV timing + a liquidity proxy), NOT
by the momentum-x signature vocabulary (its contribution measured ≈0). That lowers the prior for Stage 3:
implied vol already embeds generic public information, and the mechanism of our gain (fast reaction on
regime-transition days) is exactly where IV is fastest. Budget accordingly — this is a lottery ticket on a
measured foundation, not a discovered edge. **Nothing here has been purchased, subscribed to, credentialed,
or permissioned. Every item is an option to verify, not a recommendation. Costs are approximate.**

## What Stage 3 needs (the IV benchmark test — the *sufficient*-condition gate)
Question: does our RV forecast add value over the **implied-vol** benchmark (the market's own forecast)?
This is the gate that decides whether the door has *edge*, not just forecast skill.

- **Data**: historical **end-of-day options chains with IV** (or precomputed IV surfaces / 30-day
  constant-maturity IV) for the ~150-name liquid universe, ≥ 2 years (to overlap our 2024-01..2026-07 RV
  panel). Granularity: EOD is sufficient for Stage 3 (ATM IV or 30d interpolated); intraday IV is NOT needed.
- **Vendor options to verify** (in rough order of likely cost-fit; verify current pricing/entitlements):
  1. **Polygon.io options tier** — we already hold a Polygon market-data subscription for equities;
     CHECK FIRST whether the existing plan or a tier upgrade covers historical options aggregates/IV.
     If entitled, incremental cost may be smallest and the pipeline (flatfiles/REST) is already built.
  2. **ORATS** — purpose-built historical IV/surfaces (SMV vol, constant-maturity series); typically
     subscription ~low-hundreds $/mo for API history; strong fit for IV-benchmark research.
  3. **CBOE DataShop / LiveVol** — one-off historical EOD chain purchases (pay-per-dataset); good for a
     bounded 2-year, 150-name pull without a subscription.
  4. **OptionMetrics (IvyDB)** — the academic gold standard; likely overkill/enterprise-priced.
  5. **Free proxies for a pilot-of-the-pilot**: CBOE publishes VIX-family indices (single-name VIX-style
     indices exist for a handful of megacaps); could sanity-check the methodology on ~5 names at $0 before
     any purchase, at the cost of universe breadth.
- **Stage-3 design sketch** (to be pre-registered before any purchased data is touched): challenger RV
  forecast vs IV-implied expected RV (variance-risk-premium adjusted); gates on QLIKE vs IV and on the
  *incremental* value of our features over [HAR + IV] — the honest bar, since IV subsumes most public info.

## What Stage 4 would need (monetization sim — only if Stage 3 passes)
- Historical option **quotes/spreads** (EOD bid/ask at minimum) for realistic delta-hedged straddle or
  VRP-capture backtests net of spreads — this is where most retail vol strategies die; the sim must price
  crossing the spread. Same vendors; DataShop one-off pulls are the bounded-cost path.
- **Broker capability** (far-future, live path only): options approval on the brokerage account
  (spread/straddle level), and a paper-options environment (Alpaca supports options paper trading —
  verify current API coverage for multi-leg).

## What this memo does NOT do
No purchase, no trial signup, no API keys, no entitlement changes, no broker permission requests. The
decision — whether the Stage-2 pass justifies ~$0-500 of data spend to run Stage 3, at what vendor, on what
timeline — is Pierce's. The engineering is ready to consume EOD IV series the day data exists locally.

## Proposed kill/continue for Pierce to ratify (from the frozen prereg)
The volatility door is called REAL only if: Stage 2 ✅ (done) AND Stage 3 (challenger adds value over the
IV benchmark, pre-registered) AND Stage 4 (monetization sim clears realistic spreads). If Stage 3 fails,
the door closes with a definitive negative and no further vol-family spend without a doc-289/290-class new
hypothesis.
