# 61 — Block D: Bar-recorder coverage gap (D121 dynamic subscriptions not captured)

**Status:** diagnosed 2026-04-28 PM during the rig forensics session. **Fix deferred** — finding only, per the audit-session discipline rule.
**Severity:** infrastructure. **42% of recent intraday trades** (5 of 12 in the 2026-04-22 → 2026-04-28 window) have no bar coverage in `data/bar_recordings/`, blocking arena replay for those trades.

---

## §0 — TL;DR

Production has a **D121 BUG-P9 dynamic subscription** path: when the strategy adds a ticker to its candidate set mid-session (via `Adding N movers not in most-active`, intraday VWAP breakouts, or scanner refresh), the WebSocket client subscribes the new symbol on the fly:

```
10:32:40 | src.data.websocket_client | INFO | D121 BUG-P9: Dynamically subscribed 1 new symbols: ['SEGG']
```

The bar recorder, however, runs with the **initial session-start watchlist only** and does not get notified about D121 additions. As a result, every dynamically-added ticker that the strategy then traded is **missing from `data/bar_recordings/`** entirely.

This is the cause of the previous diff report's `missing_bars` category (5 trades). The replay rig cannot test arena fidelity on these trades because there are no bars to replay against.

**No fix tonight** — diagnosis only. The recorder-fix has its own scope and would inflate session risk.

---

## §1 — Evidence: 5 of 12 trades affected

For each missing-bars trade (4/22-4/28 window), I scraped `logs/momentum_<date>.log` for (a) the earliest log mention of the ticker and (b) the dynamic subscription event:

| Date | Ticker | Earliest log mention | D121 subscription | Pattern |
|---|---|---|---|---|
| 2026-04-22 | AGPU | 10:01:00 — `Screener returned 50 most-active (top: ... AGPU)` | 10:51:28 | Dynamic add 50 min after market open |
| 2026-04-22 | MAAS | 10:12:01 — `D117: Adding 18 movers not in most-active: ... MAAS` | 11:24:28 | Mover-list dynamic add |
| 2026-04-24 | SCNI | 10:01:27 — `Screener returned 50 most-active (top: ... SCNI)` | 10:16:45 | Dynamic add ~15 min after open |
| 2026-04-24 | ONMD | 10:36:32 — `VWAP BREAKOUT: ONMD ... +1.92% RVOL=36.1x` | 11:21:46 | Intraday VWAP-breakout scanner add |
| 2026-04-28 | SEGG | 10:31:44 — `Evaluating SEGG (gap 22.1% RVOL 1.5x)` | 10:32:40 | Mid-session evaluation add |

The pattern is uniform: **bar recorder was never subscribed to these tickers** because the static initial subscription list didn't include them, and the D121 BUG-P9 dynamic-add path doesn't propagate to the recorder.

---

## §2 — Architectural analysis

### What's happening

`src.data.websocket_client.D121 BUG-P9` is a fix that lets the WS client add new symbols to its subscription set mid-session. This was shipped to support dynamic mover detection and VWAP-breakout adds.

### What ISN'T happening

The bar recorder (whatever module writes `data/bar_recordings/{date}/{ticker}.json`) appears to take its subscription list from a SEPARATE source — likely a static initial-watchlist file or first-pass screener output. It does not listen to the same dynamic-add events the trading WS client uses.

### Why this matters for the parity program

- **Replay rig**: 5 of 12 trades cannot be replayed → arena's fidelity number is structurally upper-bounded at 7/12 = 58% even with perfect arena machinery.
- **Catalyst gate sweep**: operates on `trade_results.jsonl` directly, so unaffected (sweep doesn't need bars).
- **BAR-1 timing sweep (deferred from prior session)**: needs bars to compute alternative exit prices. Currently only 7 of 12 trades would participate.
- **Block 4.4 (full historical 86-day corpus)**: assuming the same coverage gap holds historically, that's potentially ~40% of historical trades unrunnable through the strategy in arena.

---

## §3 — Diagnostic categorization (per session brief)

The brief asked to classify each missing trade as either:
- **Recorder bug** (ticker WAS on watchlist at session start, recorder failed)
- **Candidate-universe coverage gap** (strategy added the ticker mid-session, recorder didn't follow)

Result:

| Trade | Classification | Notes |
|---|---|---|
| AGPU 4/22 | Coverage gap | Surfaced by `Screener returned 50 most-active` after open |
| MAAS 4/22 | Coverage gap | Surfaced by `D117: Adding 18 movers not in most-active` |
| SCNI 4/24 | Coverage gap | Surfaced by `Screener returned 50 most-active` after open |
| ONMD 4/24 | Coverage gap | Surfaced by `VWAP BREAKOUT` intraday scanner |
| SEGG 4/28 | Coverage gap | Surfaced by mid-session candidate evaluation |

**5 of 5 are coverage gaps. Zero are recorder bugs in the strict sense** (no evidence of "should have been recording but failed"). The recorder is doing what its initial subscription told it to do; the strategy expanded the candidate universe at runtime and the recorder didn't get the memo.

This is GOOD news in one respect — the recorder isn't broken. It's BAD news in another — the fix needs cross-component wiring.

---

## §4 — Fix design (deferred)

**Out of scope tonight.** Sketch only:

1. The bar recorder needs to subscribe to the same `D121 BUG-P9` dynamic-add events that the trading WS client uses. Two options:
   - **Option A**: refactor the recorder to share the same subscription set as the trading WS client (single source of truth).
   - **Option B**: emit a "subscription updated" event from the trading WS client that the recorder listens for and mirrors.

2. Backfill capability: for the 5 known missing trades, we can fetch historical 1-min bars from Alpaca's `/v2/stocks/{symbol}/bars` endpoint and write them in the same JSON shape the recorder uses. This would let us replay these 5 trades retroactively without waiting for the recorder fix.

3. Regression test: a script that compares `data/bar_recordings/{date}/*.json` filenames against the deduped trade ticker set from `trade_results.jsonl` — flags any trade whose bars aren't present, run nightly.

**Recommended priority:** Option B (event-driven mirror) is least invasive. Backfill of historical bars is independently shippable and unblocks Block 4.4 without needing the recorder fix to land first.

---

## §5 — Implications for the parity program

- **Arena fidelity ceiling at 58% (7/12) until this is closed.** Reporting 100% in-tolerance on 7 of 7 trades would still mean 5 untested trades.
- **Block 4.4 (full historical 86-day OOS Sharpe)**: with this gap unfixed, the run will proxy ~60% of the actual trade tape. Worth flagging as a known caveat in the eventual deliverable; doesn't block but does limit the strength of the OOS claim.
- **Catalyst gate sweep**: unaffected (uses `trade_results.jsonl` not bars). The sweep's PROVISIONAL upgrade in `docs/sweeps/catalyst_gate_pareto.md §7` stands.

---

## §6 — Status

- ✅ Diagnosed: 5 of 5 missing-bars trades are D121 dynamic-subscription coverage gaps, not recorder bugs.
- ✅ Documented (this doc).
- ✅ **Backfill shipped** (see §7).
- ⏸️ Option B (event-driven recorder mirror) deferred — recurrence prevention is next-session work. Backfill closes the immediate symptom for the rig audit.

---

## §7 — Backfill landed (2026-04-28 PM, Block B)

Shipped: `scripts/backfill_historical_bars.py` (calls Alpaca `/v2/stocks/{symbol}/bars` with .env-loaded credentials) + `scripts/audit_bar_coverage.py` (compares trade tape against bar coverage; supports `--fail-on-missing` for CI).

Run on the 5 known gaps:

| Date | Ticker | Backfill bars | Status |
|---|---|---:|---|
| 2026-04-22 | AGPU | 383 | ✅ |
| 2026-04-22 | MAAS | 314 | ✅ |
| 2026-04-24 | SCNI | 387 | ✅ |
| 2026-04-24 | ONMD | 385 | ✅ |
| 2026-04-28 | SEGG | 391 | ✅ |

Backfilled JSONs are tagged `source: backfill_alpaca_v0.1` so audits can distinguish recorder-captured from backfilled bars.

**Coverage status post-backfill (17 deduped trades, 4/22-4/28):**
- present: 11 (live recordings)
- backfilled: 5 (this commit)
- partial: 1 (ELSE 4/22 — pre-session carry, only 60 bars in the recording window)
- missing: 0

**Replay rig effect:** with the backfill, the replay status went from `ok=4, missing_bars=5, bar_gap=2, carry=6` to `ok=9, missing_bars=0, bar_gap=2, carry=6`. All 9 OK trades within tolerance ($0.34 aggregate Σ |Δ|). Block A's prod-mirror exits + Block B's backfill **together** delivered the goal: replay rig at fidelity ceiling for all replayable trades.

**What remains for the recorder fix (Option B):** the underlying D121 BUG-P9 mirror is still not implemented. New sessions will continue to drop dynamically-added tickers from the recorder. Backfill remains the bridge until that lands.

---

## §8 — Audit script as nightly check

`scripts/audit_bar_coverage.py --fail-on-missing` exits 1 if any non-carry trade has missing bars. CI hook integration deferred. Operator can invoke nightly:

```
python scripts/audit_bar_coverage.py --since $(date +%Y-%m-%d) --until $(date +%Y-%m-%d) --fail-on-missing
```

Output: `data/audits/bar_coverage_status.parquet` — one row per deduped trade with status ∈ {present, missing, partial, backfilled, corrupt}.
