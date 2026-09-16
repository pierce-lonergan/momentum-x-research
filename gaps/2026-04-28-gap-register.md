# Gap Register — 2026-04-28

**Status:** PHASE 2 deliverable. Three categories per the discipline framework.

---

## §2.1 — Engineering Gaps (we can fix)

| ID | Gap | Severity | Phase | Hours |
|---|---|---|---|---|
| **AR** | Bug AI cancel-and-coordinate doesn't fire — error string lacks Alpaca body so substring match fails | **P0** (blocks LIDR-class deadlocks) | 3.1 | 1-2 |
| **AS** | RESCAN tracker `stop_order_id` corruption — stored value is the close-order ID (e.g. `b9703115`), not a stop ID. D231 RECON_HARD_BLOCK fires for every RESCAN entry. | **P0** (no real broker stop = unbounded loss potential) | 3.2 | 2-3 |
| **AT** (renamed from earlier P0) | No pre-entry momentum confirmation. Today's 0/3 entries bought local tops. Bug AK calibration enabled them; need a 30s-confirmation gate. | **P0** (today's actual cause of loss) | 3.3 | 4-6 |
| **AU** | Tier 4 #14 — execution-quality feedback. Slippage observed (SEGG -172 bps) but NOT acted on. No daily report on alpha-eaten-by-slippage. | P1 | 4.3 | 6 |
| **AV** | Tier 4 #13 — EV-based sizing. Today all 3 RESCAN entries used same tier-1 size. EV-based would put size into highest-conviction. Depends on AU. | P1 | post-4.3 | 8 |
| **AW** | Limit-order non-fill diagnostic logging. SNBR failed to fill twice today (10:32:25, 10:49:20). No log explaining WHY (price moved away? insufficient liquidity?) | P2 | TBD | 2 |
| **AX** | No alerting on the mystery 6181f681 codepath firing. If a fallback path closes positions outside known D-codes, we should KNOW. | P2 (depends on attribution) | TBD | 1-2 |
| **AY** | D64 enhanced-recovery path bypasses Bug AM/AP fixes — when session_state exists with empty positions, the per-position fallback in `sync_from_state_and_orders` doesn't go through `sync_from_broker`. Cascade Bug AQ from yesterday. | P2 (low-incidence — only on mid-day restarts with stale session_state) | post-P0 | 2 |
| **AZ** | CI debt: 3019 ruff lint errors + missing pandas in CI install | P3 (gates merges in theory) | TBD | 4 |
| **BA** | D-code registry update for D248/D249/D272/D273 | P3 | TBD | 0.5 |
| **BB** | Pyright baseline cleanup (24 latent reportPossiblyUnboundVariable findings) | P3 (chip away 1/day) | TBD | 0.5/day |

---

## §2.2 — Research Gaps (we can investigate; answers uncertain)

| ID | Question | Severity | Investigation method |
|---|---|---|---|
| R1 | Is small-cap gap-up momentum a robust phenomenon at <60s timescales, or microstructure noise? | **Critical** (this is the core thesis of the entry signal) | Backtest 30-day RESCAN entries vs hold-time sweep (10s, 30s, 60s, 5min, EOD); separate by catalyst-presence |
| R2 | Does BAR-1 EXIT (T+60s) capture the right slice of the move, or systematically exit before the meat? | High | Hold-time sweep on D129–D150 + the trailing 30 days; tag each entry with "would-have-been-profit-if-held-Xs" |
| R3 | What features predict entry-top vs entry-launch? Today: 0/3 launches. | High | Train a classifier on entry-time features (RVOL trajectory, NBBO spread, prior 60s tick balance) → 60s-forward return |
| R4 | Bug AK calibration: profitable subset, or merely *more* trades? | High (today's data leans toward "merely more") | EV-weighted analysis of post-Bug-AK pass-throughs in the AO replay corpus once we have ≥30 sessions of data |
| R5 | Is overnight carry a distinct strategy worth isolating? | Medium (LIDR provided +$1K today) | Backtest "hold every entry overnight to next-day open" vs current intraday-only, on D129–D150 |
| R6 | Catalyst-confirmed vs catalyst-absent EV split | **Critical** (Phase 1 hypothesis) | Filter all-time trades by news_agent catalyst classification; compare per-bucket EV |

---

## §2.3 — Knowledge Gaps (require external research / data we don't own)

| ID | Question | Severity | Source needed |
|---|---|---|---|
| K1 | Sub-$3 small-cap order book microstructure (MM fade behavior on NBBO crosses) | High (likely the cause of today's entry-top pattern) | L2 data: Polygon ($79/mo+) or Databento (variable) |
| K2 | Hidden liquidity / midpoint executions — Alpaca routing decisions | Medium | Alpaca documentation request + cross-broker comparison |
| K3 | Capacity ceiling — accurate ADV impact modeling above 5% participation | High (bounds $1M target) | L2 + sweep data |
| K4 | Is Alpaca paper representative of live IBKR/Tradier? | Medium (gates real-money deployment) | Live small-test on Alpaca + shadow run on IBKR/Tradier |
| K5 | 2026 regime shift in small-cap gap-up after zero-commission saturation | Medium (training-window relevance) | SSRN literature scan: "small-cap momentum decay 2024-2026" + "retail flow regime change post-Robinhood" |
| K6 | Best-practice catalyst NLP classification for trading systems | Medium (improves news_agent accuracy) | Recent academic literature on financial NLP — there are 2024+ papers comparing prompt-based vs fine-tuned approaches |

---

## Summary

- **3 P0 engineering items** must ship before tomorrow's open
- **6 P1/P2 engineering items** queued for this week
- **6 research questions** answerable internally over the next 30-90 days (R1-R6)
- **6 knowledge gaps** requiring external data or expertise (K1-K6); some may be deferred until edge is proven

The single highest-leverage research question is **R6**: catalyst-confirmed vs catalyst-absent EV split. If the data shows catalyst-confirmed entries have positive EV and catalyst-absent entries have negative EV (which Phase 1.4 hypothesizes), the right immediate fix is a hard gate: **reject any entry without a catalyst classification of CONFIRMED**. That's a calibration change, not a code change, and could be tested via the Bug AO replay corpus with ZERO production risk.

**Gate to Phase 3:** ✅ this document committed.
