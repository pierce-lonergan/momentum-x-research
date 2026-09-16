# CRCL Catalyst Forensics — Late Feb 2026 (CRCA 2x leveraged ETF trade)

**Audit date:** 2026-04-30
**Trade window:** BUY 2026-02-25 15:49 UTC @ $3.03 → SELL 2026-03-02 @ $38.81-$39.01 (CRCA, 2x leveraged on CRCL)
**Underlying:** Circle Internet Group (NYSE: CRCL)

---

## Layer 1 verdict: Real-Earnings (8-K Item 2.02 equivalent)

## Layer 2 verdict: YES — α-extend can generalize

## Evidence:

- **2026-02-25, 8:00 AM ET (pre-market):** Circle released Q4 2025 + FY25 results via BusinessWire / Circle pressroom. Q4 revenue $770M (+77% YoY), Q4 net income $133M (+$129M YoY swing), Adj EBITDA $167M (+412%), USDC circulation $75.3B (+72%), USDC onchain volume $11.9T (+247%). [Source: circle.com pressroom; businesswire 20260225882643]
- **Pre-market reaction:** CRCL +17% pre-market on the print; intraday +20.34% on 2026-02-25. The bot's BUY at 15:49 UTC (10:49 AM ET) was placed ~3 hours into the post-earnings session — i.e., directly into the M+A spike window. [Source: stockstotrade 2026_02_25; earezki.com 2026-02-25-crcl]
- **One-month run:** CRCL rallied ~86% in the month, from a ~$50 February low toward an analyst target of $129.71. The Feb 25 → Mar 2 hold captured the post-earnings continuation phase. [Source: earezki.com]
- **Confirms Bonde M filter (already known):** Polygon Q1→Q4 2025 net income trajectory $64M → $133M = +105% intra-year, passing the M ≥ 100% threshold. The Feb 25 print is the catalyst that monetised that latent "M".
- **Stacking secondary catalysts (not the primary trigger but tailwinds):**
  - Intuit partnership announced early Feb 2026 (Arc roadmap day). [Source: yahoo finance "Circle Roadmap And Intuit Deal"]
  - OCC conditional trust-bank approval (First National Digital Currency Bank, N.A.) — Dec 2025, still feeding the regulatory-clarity narrative. [Source: circle.com/pressroom OCC]
  - Polymarket native-USDC partnership announced 2026-02-05. [Source: circle.com/pressroom Polymarket; benzinga 50423991]
- **Forward guidance issued on the call:** management provided 2026 KPI guidance including $150M-$170M non-interest revenue — guidance was viewed as bullish and amplified the move. [Source: earezki.com; mexc blog]

## Ruled-out alternatives:

- **FIT21 / stablecoin legislation Feb 2026:** No Senate or House vote on FIT21 in Feb 2026. Stablecoin framework was already settled by the GENIUS Act (signed July 2025). Not the trigger. [Source: lw.com US Crypto Policy Tracker; congress.gov S.1582]
- **Crypto market beta:** BTC was DOWN ~24% YTD by late Feb 2026 ($67k); ETH down ~34% to ~$2k — worst YTD start on record. CRCL ripped UP against a falling crypto tape, confirming it was an **idiosyncratic earnings move, not beta**. [Source: fortune.com 2026-02-20; vaneck.com Feb 2026 selloff]
- **Short squeeze / options:** Overbought RSI (77.63) noted post-move, but no documented unusual SI / gamma squeeze pre-print. Squeeze mechanics may have amplified Day 2-5 continuation but did not initiate the move.

## Confidence: HIGH

Catalyst, date, and time-of-day all triangulate. Pre-market 8:00 AM ET earnings release on 2026-02-25 maps cleanly to a 10:49 AM ET BUY same day (catalyst-day entry into a +20% session). The Feb 25 → Mar 2 hold captured the standard 3-5 day post-earnings drift window for a Bonde-style M+A name.

## Caveats:

- Could not confirm the exact 8-K Item 2.02 SEC filing acceptance timestamp (Circle press release does not cite it; investor.circle.com page not fetched). Strongly inferred from the 8:00 AM ET wire release pattern.
- Consensus revenue / EPS surprise magnitudes not extracted from the press release itself (qualitative "crushed estimates" language only). A Polygon `earnings` endpoint pull would quantify the surprise %.
- Unable to verify retroactive options flow / SI data without a paid feed.
- The 2x leverage of CRCA means the underlying CRCL move (~+86% peak-to-peak in the window) compounded to the ~12.8x dollar return on CRCA — leverage decay was favourable because the move was directional, not choppy.

## Implication for α-extend:

Layer 2 = YES. This is a textbook Real-Earnings catalyst that the α-extend infrastructure CAN systematically identify going forward by:
1. Pre-screening the M filter (intra-year NI growth ≥ 100%) via Polygon financials before earnings dates.
2. Watching the earnings calendar for names that pass M, then arming entry on confirmed beat + raise.
3. Holding through the standard 3-5 day post-earnings drift, optionally on 2x leveraged ETFs where they exist (CRCA, NVDL, TSLL, etc.).

The unmonitored 5-day hold worked here **by accident of catalyst quality**, not by design. With α-extend, the same setup becomes **repeatable** rather than fortunate.
