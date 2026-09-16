# Polygon Stocks Advanced — observed rate limits

**Subscription:** Stocks Advanced ($199/mo)
**Reconnaissance date:** 2026-04-29 PM
**Method:** 10 sequential GET calls to `/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-01-02`

## Findings

| Metric | Value |
|---|---|
| HTTP status (all 10 calls) | 200 |
| Sustained throughput | 8.5 req/s |
| Per-call latency (cold) | ~291ms (TLS handshake) |
| Per-call latency (warm) | ~95–110ms |
| `X-RateLimit-*` headers | **none returned** |
| `Retry-After` headers | none returned |
| 429 responses | 0 |

## Polygon's documented behaviour (Stocks Advanced)

- Per the marketing copy: **"Unlimited API calls"**.
- Polygon does **not** publish per-second / per-minute hard caps for Advanced.
- 429s should be exceedingly rare; if observed, indicates either (a) a
  catastrophic spike (parallel >100 in flight) or (b) provider incident.

## Our self-imposed budget

Reasonable upper bounds even on Advanced (be a good neighbour):

| Knob | Value | Rationale |
|---|---|---|
| `max_concurrent` (semaphore) | **10** | Plenty of headroom; observed 8.5 req/s sequential implies 30+ req/s parallel is feasible. |
| Retry on 429 | exp backoff `2^attempt` | Defensive; 429s shouldn't occur but handle them. |
| Max retries on 5xx / network | 5 | Polygon occasionally returns 503 during deploys. |

## Cache strategy

| Endpoint pattern | TTL |
|---|---|
| `/v2/aggs/ticker/...` | forever (historical bars don't change) |
| `/v3/quotes/...` | forever (historical NBBO doesn't change) |
| `/v3/trades/...` | forever (historical trades don't change) |
| `/v3/reference/tickers` (list) | 1 day (universe shifts slowly) |
| `/v3/reference/tickers/{ticker}` | 1 day (ref data shifts slowly) |
| `/vX/reference/financials` | forever (filed financials don't change) |
| `/v2/snapshot/...` | 0 (snapshots always fresh) |

## Re-reconnaissance triggers

Re-run rate-limit recon if any of these:

1. Subscription tier changes (downgrade to Developer would cap at ≤100 req/min)
2. We start hitting 429s during normal backfill
3. Polygon emails about a quota change
4. Sustained throughput drops below 5 req/s without obvious cause

## Procedure to re-reconnaissance

```python
# Run from repo root with .env loaded
import asyncio, time, os, httpx
key = os.environ["POLYGON_API_KEY"]
url = f"https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-01-02?apiKey={key}"

async def probe():
    async with httpx.AsyncClient(timeout=15) as c:
        for i in range(10):
            r = await c.get(url)
            print(i, r.status_code, dict(r.headers))
asyncio.run(probe())
```

Document the new findings here, replacing this section.
