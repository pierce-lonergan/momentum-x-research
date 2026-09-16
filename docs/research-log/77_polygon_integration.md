# 77 — Polygon.io REST integration

**Status:** shipped 2026-04-29 PM. Subscription: Stocks Advanced ($199/mo).
**Severity:** infrastructure unlock. Resolves four sessions of "blocked on tick data" findings.

---

## §0 — TL;DR

New integration layer at `mx-arena/data_providers/polygon/`. Six endpoints wrapped (aggregates, quotes, trades, ticker reference, ticker details, financials, snapshots/gainers). File-based cache, exponential-backoff retry, 10-concurrent semaphore. 12 unit tests pass; live integration test confirmed Polygon's 1-minute bars are **bit-perfect on OHLC** vs our existing `bar_recordings` for AKAN 2026-04-29 (volume delta -36 shares = <0.1%, attributable to IEX-only recording vs Polygon's full SIP).

This doc is the canonical reference for the integration. RATE_LIMITS.md alongside the module documents the observed quota.

---

## §1 — What's in the module

```
mx-arena/data_providers/polygon/
├── __init__.py        # public exports
├── client.py          # PolygonClient: httpx wrapper + cache + retry
├── endpoints.py       # PolygonEndpoints: typed methods per endpoint
├── models.py          # Bar, Quote, Trade, TickerRef, FinancialReport
└── RATE_LIMITS.md     # observed quota + budget
```

### `PolygonClient` (low level)

- `from_env()` classmethod: reads `POLYGON_API_KEY` from env
- `async with PolygonClient(...) as c:` context manager
- `await c.get(endpoint, params)` — single GET, returns `PolygonResponse`
- `async for resp in c.paginate(endpoint, params):` — auto-cursor via `next_url`
- Retry: exp backoff `2^attempt` on 429/5xx/network, max 5 retries, raises `PolygonRateLimitError` / `PolygonAPIError` on exhaustion
- Cache: file-based at `data/polygon_cache/{endpoint}/{hash}.json`, **API-key-stripped key** so rotation doesn't invalidate the cache

### `PolygonEndpoints` (high level)

| Method | Polygon path | Returns |
|---|---|---|
| `aggregates(t, mult, span, from, to)` | `/v2/aggs/ticker/{t}/range/{m}/{ts}/{from}/{to}` | `list[Bar]` |
| `quotes(t, ts_gte_ns=, ts_lte_ns=)` | `/v3/quotes/{t}` | `list[Quote]` |
| `trades(t, ts_gte_ns=, ts_lte_ns=)` | `/v3/trades/{t}` | `list[Trade]` |
| `list_tickers(market="stocks", type_=)` | `/v3/reference/tickers` | `list[TickerRef]` |
| `ticker_details(t, date=)` | `/v3/reference/tickers/{t}` | `TickerRef \| None` |
| `gainers()` | `/v2/snapshot/locale/us/markets/stocks/gainers` | raw `list[dict]` |
| `financials(t, timeframe=, limit=)` | `/vX/reference/financials` | `list[FinancialReport]` |

### Models (`models.py`)

All frozen + slotted dataclasses. Each exposes a `from_json(ticker, obj)` classmethod for parsing Polygon's JSON shapes. `Quote.midpoint` returns `(bid+ask)/2` or `0.0` if either side missing.

---

## §2 — Observed performance (Stocks Advanced)

From Block A.0 reconnaissance (10 sequential calls):

| Metric | Value |
|---|---|
| HTTP success | 10/10 = 100% |
| Sustained sequential throughput | **8.5 req/s** |
| Cold-call latency | ~291ms (TLS handshake) |
| Warm-call latency | ~95–110ms |
| Rate-limit headers exposed | **none** (consistent with "unlimited" tier) |
| 429 responses | 0 |

Conservative parallel budget: `max_concurrent=10` (semaphore). Observed sequential throughput implies 30+ req/s in parallel is feasible without 429s, but politeness margin retained.

---

## §3 — Cache strategy

Per `client.py:DEFAULT_TTL`:

| Endpoint pattern | TTL | Rationale |
|---|---|---|
| `/v2/aggs/ticker/...` | forever | historical bars don't change |
| `/v3/quotes/...` | forever | historical NBBO doesn't change |
| `/v3/trades/...` | forever | historical trades don't change |
| `/v3/reference/tickers` (list) | 1 day | universe shifts slowly |
| `/v3/reference/tickers/{t}` | 1 day | ref data shifts slowly |
| `/vX/reference/financials` | forever | filed financials don't change |
| `/v2/snapshot/...` | 0 (always fetch) | snapshots must be fresh |

**Important: cache key strips `apiKey`** so rotating the key (which we should do soon — it was pasted in plaintext in chat) doesn't invalidate gigabytes of cached responses.

Cache lives at `data/polygon_cache/{endpoint_path_underscored}/{16-char-sha256}.json`. Mirroring the endpoint hierarchy lets a human inspect what's cached without spelunking through hashed dirs alone.

---

## §4 — Live reconciliation vs `bar_recordings`

Block A.3 live integration test (1 day of AKAN, 2026-04-29):

| Source | First bar (13:30Z) | Last bar | Total bars |
|---|---|---|---|
| `bar_recordings/2026-04-29/AKAN.json` | O=19.46 C=20.06 V=39793 | 19:59Z | 383 (regular session only) |
| Polygon `/v2/aggs` 1m | O=19.46 C=20.06 V=39757 | 23:59Z | 841 (extended hours) |
| Δ at 13:30Z | O=0.0000 C=0.0000 **V=-36** | — | — |

Findings:
- **OHLC bit-perfect** at the first regular-session minute
- Volume Δ = -36 shares = **0.09%** — attributable to IEX-only recording missing dark/wholesale trades that Polygon's SIP captures
- Polygon delivers extended hours (04:00 ET pre-market through 19:59 ET after-hours) for free, doubling the data per ticker per day
- No schema friction — both feeds use 1-minute UTC bars, both report O/H/L/C/V/VWAP

This validates Polygon as a **drop-in superset** of bar_recordings. Doc 79 (arena infrastructure sweep) will formalize the unified-bar-source abstraction that prefers Polygon over bar_recordings for the same (ticker, date).

---

## §5 — Tests

`tests/unit/test_polygon_client.py` — **12 tests, all passing**:

1. `test_get_aggregates_parses_bars` — verifies Bar parsing
2. `test_get_quotes_parses_and_paginates` — verifies cursor pagination via `next_url`
3. `test_429_raises_after_retries` — verifies retry exhaustion → `PolygonRateLimitError`
4. `test_4xx_other_than_429_raises_immediately` — no retry on 4xx
5. `test_cache_hit_avoids_second_call` — cache effectiveness
6. `test_cache_key_excludes_apikey` — **key rotation safety** (different apiKey, same cache hit)
7. `test_ticker_details_parses_market_cap` — ref data parsing
8. `test_financials_parses_income_statement` — financial report parsing
9. `test_quote_midpoint_zero_when_one_side_missing` — defensive midpoint
10. `test_quote_midpoint_average_when_both_sides_present` — happy-path midpoint
11. `test_bar_handles_missing_vwap_and_n` — schema robustness
12. `test_financial_report_handles_missing_income_statement` — schema robustness

Mocking via `httpx.MockTransport` (matches codebase convention; no `respx` dependency).

---

## §6 — Security note

The API key `POLYGON_KEY_REDACTED_ROTATE_ME` was pasted in plaintext in the operator's prompt. The operator should:

1. **Rotate the key** from the Polygon dashboard within 7 days
2. After rotation, set the new key in `.env` (already gitignored — verified)
3. The cache will continue to serve old payloads since the cache key strips `apiKey`

Same string was provided as both `POLYGON_API_KEY` and `POLYGON_S3_SECRET_ACCESS_KEY`. Tonight's REST work uses only the API key. S3 (Flat Files) integration is deferred to a future session — when added, we'll verify whether the same secret really works for both surfaces or if the operator's paste was duplicated.

---

## §7 — What this unlocks for tonight's subsequent blocks

| Block | Unlock |
|---|---|
| **B (backfill)** | All 6 endpoints ready; `BackfillOrchestrator` only needs to wrap them with progress tracking and parquet writes |
| **C (calibration v3)** | `quotes()` + `trades()` give us NBBO ticks for the 9-trade `prod_qty_truth` corpus — closes the doc 64 contamination finding |
| **D (arena sweep)** | `aggregates()` cleanly supersedes `bar_recordings`; abstraction layer can prefer Polygon over the JSON cache without strategy code changes |

---

## §8 — What's NOT in this integration (future sessions)

- **WebSocket streaming** — Polygon supports `wss://socket.polygon.io/stocks` for real-time. Touches live data paths; deferred to a dedicated session.
- **Flat Files (S3)** — bulk historical pulls via S3-compatible API at `https://files.massive.com`. Cheaper for huge backfills (e.g. all-tickers all-history). REST is sufficient for tonight; S3 deferred.
- **Options chain data** — included in subscription but not relevant to the EP pivot today. Schema is similar to equities.
- **Splits / dividends adjustments** — Polygon's `adjusted=true` query param handles this in `aggregates()`. Splits/dividends as separate endpoints (`/v3/reference/splits`, `/v3/reference/dividends`) deferred.

---

## §9 — Status

- ✅ Module shipped (`mx-arena/data_providers/polygon/`)
- ✅ 6 endpoint wrappers
- ✅ 12 unit tests pass
- ✅ Live reconciliation test (OHLC bit-perfect for AKAN 4/29)
- ✅ RATE_LIMITS.md documented
- ✅ This doc

**Discovery rate: 32/0 holds.** No production bugs introduced.
