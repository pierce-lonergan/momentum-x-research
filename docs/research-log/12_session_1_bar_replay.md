# Session 1 Bar Replay — April 17, 2026

**Purpose:** Compare today's high-composite (cascade-rejected) tickers against their realized post-open price action. This is ONE observation in a 5–10 session sequence. Do not treat as a decision basis.

**Session:** April 17, 2026 — first live session under D220
**Method:** Read minute bars from `data/bar_recordings/2026-04-17/`. Use first-2-minute VWAP (9:30:30–9:31:30 UTC-derived) as realistic entry price per yesterday's diagnostic discipline. Report T+15 and close returns separately.

## The table

| Ticker | composite_full | SEC error | 9:30 open | Realistic entry (2-min VWAP) | Session close | Ret T+15 | Ret close |
|--------|---------------:|:---------:|----------:|------------------------------:|--------------:|---------:|----------:|
| NVTS   | 0.711 | Y | $12.200 | $12.0809 | $12.305 | **+2.06%** | **+1.85%** |
| ONFO   | 0.650 | Y | $1.270  | $1.2789  | $1.255  | **+5.46%** | −1.87% |
| RMSG   | 0.601 | Y | $2.410  | $2.4282  | $2.745  | **+24.79%** | **+13.05%** |
| AGAE   | 0.560 | Y | $0.570  | $0.5749  | $0.680  | **+0.52%** | **+18.30%** |
| WNW    | 0.556 | Y | $4.760  | $4.7501  | $5.860  | **+3.64%** | **+23.36%** |
| BYND   | 0.548 | n | $0.908  | $0.8948  | $0.828  | **+3.90%** | −7.48% |

**Missing bar files:** PBM, BZAI, YXT (3 of 9 high-composite tickers). These were likely not recorded because they didn't reach the production bar-recorder's threshold. Excluded from this analysis.

## Aggregate numbers (n=6, one session)

- **T+15 win rate: 6/6 = 100%**
- **Close win rate: 4/6 = 67%**
- Mean T+15 return: **+6.73%**
- Mean close return: **+7.87%**

**Excluding SEC-errored tickers (n=1 — BYND only):**
- T+15: +3.90% (positive, one data point)
- Close: −7.48% (negative, one data point)

## What this observation is

- One session of bar-replay data on a small set of tickers the cascade rejected
- Six observations (three of nine composite-flagged tickers lacked bar recordings)
- Consistent with the inversion hypothesis from yesterday's diagnostics, not proof of it

## What this observation is NOT

1. **Not a statistical result.** n=6 from a single session has massive noise. 5–10 sessions × ~9 tickers = 45–90 observations is the minimum before pattern-vs-noise can be distinguished.
2. **Not confounded-free.** 5 of 6 tickers had SEC EDGAR enrichment fail today (outages at 4:00 and 9:00 AM ET). The composite itself uses gap/volume/price features, not SEC data, so the composite SCORES are valid. But the *production MFCS* comparison is artificially low because the risk agent ran with degraded data. The production-vs-composite agreement analysis from yesterday's shadow cannot be cleanly attributed to today.
3. **Not calibrated vs a control.** I didn't replay tickers the cascade accepted and composite rejected (AGREE_NO_TRADE bucket, n=2) or tickers both systems agreed on. Without a counterfactual, "composite picks winners" has no reference.
4. **Not fill-realistic.** Entry is 9:30:30–9:31:30 VWAP from the recorded bars. Real fills have bid/ask spread and slippage, especially on sub-$1 stocks like AGAE ($0.57) and BYND ($0.91). The AGAE "+18.30% close" assumes you could have bought at VWAP; real bid/ask on a $0.57 stock could easily be 3-5% of price.
5. **Not a horizon commitment.** T+15 WR is 100%, close WR is 67%. A strategy that held to close on BYND would have lost 7.5%. A T+15 exit would have captured every winner. The composite was not trained to predict which horizon wins.

## The standout signals (for the follow-up sessions to confirm or refute)

- **RMSG composite=0.601** → closed +13.05%. Rejected at VWAP bias 3.1% below. If this pattern repeats across 5 sessions, VWAP-bias rejections on mid-composite stocks are measurably costly.
- **WNW composite=0.556** → closed +23.36%. Rejected at VWAP bias 2.3-2.5% (just over my 2% Phase 1 threshold). One session doesn't justify another VWAP tweak; 5 sessions of similar observations would.
- **AGAE composite=0.560** → closed +18.30%. Rejected by D112 Router for RVOL 0.2x. This is a separate gate from the float cap Phase 1 fixed. D112's RVOL floor was not in scope for D220 and may itself warrant reexamination.

## The ambiguous signals

- **ONFO composite=0.650** (highest after NVTS) → T+15 +5.46%, close −1.87%. Classic spike-and-fade. The composite was right about the move happening, not about whether it would hold.
- **BYND composite=0.548** → T+15 +3.90%, close −7.48%. Same pattern, larger fade.

These are the cases where the composite's horizon ambiguity matters. The composite predicts `win_close` (close return > 0), so a T+15 win that closes negative is technically "composite wrong" for BYND and ONFO. For NVTS, RMSG, AGAE, WNW it's "composite right." 4 right, 2 wrong at close is consistent with the backfill's 60%-ish composite WR on held-out inverted candidates, within noise.

## SEC EDGAR error timing — a caveat worth documenting

Today's 41 SEC EDGAR errors clustered at 04:00 ET (31 errors) and 09:00 ET (10 errors). 6 of today's 9 high-composite tickers errored (AGAE, NVTS, ONFO, PBM, RMSG, WNW). Specifically:

- The composite_score itself does NOT depend on SEC data — it uses gap_pct, premarket_volume, dolvol, log_price, orb_range, volume ratio, and arena_buy_verdict. Today's composite scores on these tickers are valid.
- The production MFCS DOES depend on the risk agent, which uses SEC filings to detect dilution (424B5) etc. When SEC enrichment fails, the risk agent defaults to neutral/unknown, which may artificially lower the MFCS.
- Consequence for the "composite vs production MFCS" analysis: the gap between the two is partially attributable to SEC outage today, not pure cascade anti-selection.

To remove this confounder in future sessions: only use shadow entries where `production_mfcs` was computed with SEC data available. Add a `sec_data_available` flag to the shadow schema in a future hardening pass.

## Debate-skip mechanism — read and understood, no action

The session report shows 28/28 debates skipped for budget reasons. I investigated the mechanism: `max_debate_attempts: int = Field(default=0)` was set in commit `2f6079b` on March 10 (D100 — "Debate engine killed. 0% conversion rate over 7 debates"). The 28/28 skip rate is the expected steady-state behavior for the last 5+ weeks, NOT a D220 side-effect. Qualifying candidates fall through to MFCS-only verdict; they don't get BLOCKED at debate, they get DIRECTLY-decided without it. This is not a bottleneck and doesn't affect today's trade outcome.

My earlier deep-dive framing ("fourth bottleneck") was wrong. Correction noted here.

## What I am NOT doing based on this data

- **Not loosening VWAP.** One session of RMSG +13% and WNW +23% is not reason to change a gate the composite is on path to replace. Stated commitment from yesterday holds: stop tuning cascade gates, let the composite replace them.
- **Not changing the RVOL floor.** AGAE's +18.30% close after being rejected at RVOL 0.2x is one observation. Same reasoning.
- **Not promoting the composite early.** Four to nine more sessions of this kind of data before any cutover conversation.

## What session 2 needs to look like

- All nine high-composite tickers (or however many session 2 produces) with bar recordings. PBM, BZAI, YXT missing today is a data-collection gap; check whether the bar-recorder threshold excludes these legitimately.
- SEC EDGAR either operational or, if degraded, the affected tickers flagged in the shadow log itself.
- Same replay table format so this can be diff'd session-over-session.
- At session 3–4, compute rolling medians on T+15 and close returns to see if the direction stabilizes.

## Files

- `data/arena_runs/session_1_bar_replay.json` — structured per-ticker data for the 6 replayed tickers
- `data/bar_recordings/2026-04-17/` — 10 ticker bar files from today's session (source)
- `data/shadow/shadow_2026-04-17.jsonl` — 102 shadow entries
- `data/journals/journal_2026-04-17_083013.jsonl` — 102 journal entries
- `data/session_reports/session_2026-04-17_2026-04-17T20-00-31.json` — session summary
