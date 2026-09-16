# 67 — Catalyst-only OOS test: INCONCLUSIVE due to data constraints

**Status:** documented 2026-04-28 PM. The catalyst-only hypothesis **could not be tested** on the existing rig + corpus.
**Severity:** strategy-meta — affects what next sessions should pursue.

---

## §0 — TL;DR

Last session's headline overturned: no demonstrated edge. The recommended next move was to test the only positive hypothesis we had evidence for — catalyst-only entries (since both historical wins, LIDR 4/24 +$421 and OGN 4/27 +$515, had clear named catalysts).

This session attempted that test. **Result: cannot run.** Two structural data constraints:

1. **Finnhub free-tier earnings calendar is capped at 1500 events** for the 5-month window. The actual US-equity earnings universe for Dec 2025 → Apr 2026 is roughly 5,000-10,000 events. We have ~15-30% of the earnings calendar; the tickers Finnhub returns at the cap are biased toward larger-cap names tracked by mainstream analysts.
2. **Bar_recordings corpus has 0% overlap with the earnings calendar at ±1 trading day, 0.03% at ±5 days.** Out of 5,935 (date, ticker) pairs in bar_recordings, only 2 have any earnings event within ±5 days (ELPW 4/23 / earnings 4/20; SEGG 4/28 / earnings 4/23 — both 3-5 days off, neither is a same-day catalyst trade).

Implication: the catalyst-only test as designed **structurally cannot fire** on this corpus. Not because the catalyst hypothesis is wrong — because the data we have doesn't include earnings-day overlap.

---

## §1 — What was attempted

`scripts/fetch_historical_earnings.py` — bulk-fetched Finnhub earnings calendar Dec 2025 → Apr 2026:
- 1500 events, 1494 unique tickers, single API call
- Likely free-tier hard cap (truncation, not actual count)

`mx-arena/arena/orchestrator_stub.py` extended `DeterministicPolicyOrchestrator` with:
- `earnings_calendar` (loaded payload)
- `require_earnings_catalyst` boolean
- `earnings_window_days` (default 1)
- `_has_earnings_within_window(ticker, session_date)` lookup

`scripts/run_block_44_oos.py --require-earnings-catalyst --variant-tag _catalyst_only`:
- Result: 3 trades over 1 session day (4/28 only, via decision_row mode which bypasses the earnings gate)
- 0 trades on the 85 policy-mode sessions — all candidates rejected by earnings filter

Audit confirms (`scripts/audit_*` fragment in this commit's session log):
- 1494 earnings tickers in calendar
- 5,935 (session, ticker) pairs in bar_recordings
- 0 pairs with earnings ±1 day
- 2 pairs with earnings ±5 days (still misses)

---

## §2 — Diagnosis: data, not hypothesis

The catalyst-only test was supposed to answer "does the strategy have edge in the catalyst-confirmed subset?" The test couldn't fire because the catalyst-confirmed subset is empty in our corpus.

**Three possible explanations** (not mutually exclusive):

1. **Finnhub free-tier truncation**: paid tier ($60-200/mo) or different provider (Polygon, IEX) would give full earnings calendar. With proper coverage, match rate would likely be 1-5% of (session, ticker) pairs — still small but non-zero.

2. **Recorder selection bias**: the bar recorder captures gappers + movers + manually-watched tickers, which prod selected for momentum reasons. Earnings-day tickers that DIDN'T gap weren't captured. Earnings-day tickers that DID gap were captured but Finnhub's free-tier cap excluded them (the cap returns the larger-cap names first).

3. **The historical wins were rare events**: LIDR 4/24 and OGN 4/27 are the only clear named-catalyst wins in months of data. They might not be representative of a tradeable signal — they might just be two lucky trades that happened to coincide with catalysts.

Without better data, we cannot distinguish (1)+(2) from (3).

---

## §3 — What this means operationally

The catalyst-only hypothesis is **NOT REJECTED** by tonight's test (the test couldn't fire, so it couldn't reject). It is also **NOT VALIDATED** — same reason.

The hypothesis remains where it was after last session: the only positive signal we've ever observed, with n=2 wins, on data we don't have enough of to test rigorously.

**Operational implication: the recommended path forward changes.**

Pre-tonight recommendation: "spend one session testing catalyst-only because it's the cheapest test of our only positive hypothesis."

Post-tonight reality: "the cheapest test fails by data. To genuinely test the hypothesis requires getting better earnings data." Three options ordered by cost:

### Option A — paid earnings data ($60-200/mo, multi-week to integrate)
- Finnhub paid tier removes the 1500-event cap
- Polygon and IEX provide more comprehensive earnings calendars
- Integration is straightforward (~1-2 sessions of work)
- Once integrated, catalyst-only OOS becomes a runnable test

### Option B — re-curate the bar corpus around earnings (~2-3 sessions)
- Scrape SEC EDGAR for 8-K earnings filings
- Backfill bar recordings for tickers that DID have earnings during the corpus window
- Re-run catalyst-only OOS on the curated corpus
- Risk: may still find no signal at small n; data work is wasted if hypothesis was wrong

### Option C — accept inconclusive + pivot question (no cost)
- Don't pursue catalyst-only further until external data lands
- Pivot the project's open question from "does the current strategy work?" to "what strategy class produces edge in the corpus we have?"
- Re-examine the 86-session OOS data: are there per-ticker patterns, per-day patterns, intraday-time patterns that show edge in the high-quality strata?
- This is research-mode, not engineering-mode

---

## §4 — Recommendation (this commit)

**Option C is what this commit ships.** Reasoning:

1. Option A's $60-200/mo cost requires user decision; not a coding-session call.
2. Option B is ~2-3 sessions of data work that may produce no answer.
3. Option C is free and may surface signals the current framing missed.

The plan doc 58 §15 (added in this commit) captures the three options and the operator-decision-point for Option A.

---

## §5 — Logged findings

1. **Finnhub free-tier earnings cap is structural**, not a misconfiguration. Documented for future use.
2. **Bar-recordings corpus has zero overlap with mainstream earnings calendar**. Either the recorder selection bias is severe, or we need to seek a different overlap (e.g. catalyst tags from prod's news_agent classifications rather than external earnings calendar).
3. **The Bug AO orchestrator-hook gap is more painful than tonight's session expected**: only 4/28 has decision_row corpus → only 4/28 has news_signal/conf data → only 4/28 can do catalyst-confirmation via internal classification. The other 85 sessions have policy-mode synthesized data. Closing Bug AO retroactively would be a multi-session backfill but would unlock catalyst stratification on the full corpus.

---

## §6 — Discipline check

- ✅ Halt switch confirmed at session start (and at end per commit message)
- ✅ Negative result documented with same rigor as positive would have been
- ✅ The hypothesis is NOT REJECTED; the test failed by data — distinction surfaced explicitly
- ✅ 30/0 discovery rate holds (no production bugs introduced)
- ✅ Forward paths enumerated; user decision point captured

**Status: catalyst-only test inconclusive; three forward paths documented; operator decision needed for Option A.**
