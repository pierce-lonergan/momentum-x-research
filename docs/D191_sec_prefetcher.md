# D191: SEC EDGAR Real-Time Pre-Fetcher

**Decision date:** 2026-04-03
**Status:** Implemented
**Module:** `src/data/sec_prefetcher.py`
**Config:** `FallerDetectionConfig.weight_sec_*` in `config/settings.py`
**Tests:** `tests/unit/test_sec_prefetcher.py` (18 tests)

---

## Why SEC Analysis Beats LLM Analysis for Dilution Detection

The LLM news agents (news_agent, catalyst_agent) miss dilution events because:

1. **News articles lag.** A 424B5 filed at 6 AM ET may not appear in news feeds until 8–9 AM. By market open the gap-up looks "organic" but shares are already being sold.
2. **LLMs can't read SEC EDGAR directly.** They process news summaries and titles, not raw filing metadata.
3. **Promotional language hides intent.** Pump operations often issue press releases alongside dilutive offerings. The LLM sees a "positive catalyst" while the 424B5 in EDGAR reveals the mechanism.

The SEC pre-fetcher is **deterministic** — no inference, no hallucination. A 424B5 filed today is a fact.

---

## The 424B5 Dilution Signal

A **424B5** (Prospectus Supplement) means the company is actively selling shares into the market right now, under a previously filed shelf registration (S-3). This is the mechanism behind most small-cap gap-and-dump events:

```
Day N:   Company files S-3 (shelf registration, pre-authorises future sales)
Day N+X: Stock runs up on promotional activity or legitimate news
Day N+X: Company files 424B5 at 6 AM ET — "we're using our shelf to sell shares"
9:30 AM: Market opens, stock gaps up on retail FOMO
9:31 AM: Company/underwriter begins selling into the gap
10:00 AM: Gap fades -30% to -60% as supply overwhelms demand
```

**Detection rule:** `424B5` filed today or yesterday → `DILUTION_ACTIVE` → add `+0.40` to faller score.

---

## ATM Offering Detection

An **at-the-market (ATM) offering** (Rule 415(a)(4)) is worse than a standard 424B5 because:

- The company can sell shares **continuously** at prevailing market prices
- There is no single "offering price" — they sell into every bid
- The program can last months or years, suppressing every rally

ATM programs make sustained upward momentum **structurally impossible**. Every time the stock rallies, the company has financial incentive to sell more shares.

**Detection:** If a 424B5 is filed today/yesterday AND the filing text contains any of these phrases:
- `"at-the-market offering"` / `"at the market"`
- `"Rule 415(a)(4)"`
- `"sales agent"` (agent who executes the continuous selling)
- `"distribution agreement"` (the contract that enables ongoing sales)
- `"at prices related to prevailing market prices"`

→ Signal upgrades from `DILUTION_ACTIVE` to `DILUTION_ATM` → adds `+0.50` to faller score.

The fetcher reads only the first 8KB of the filing document (the cover page), which is where ATM language appears by SEC convention.

---

## The EDGAR Submissions API

This module uses `data.sec.gov/submissions/CIK##########.json` — **not** the EFTS full-text search API used by `sec_client.py`.

| | EFTS (`sec_client.py`) | Submissions API (`sec_prefetcher.py`) |
|---|---|---|
| **Endpoint** | `efts.sec.gov/LATEST/search-index` | `data.sec.gov/submissions/CIK.json` |
| **Use case** | Keyword search across filing text | "What did company X file recently?" |
| **Input** | Ticker + keyword | CIK number |
| **Output** | Matching filing snippets | All filing metadata, newest first |
| **Requests per ticker** | 1 (search) | 1 (submissions) + 1 if ATM check |
| **Speed** | Medium | Fast |

CIK lookup is done via `https://www.sec.gov/files/company_tickers.json` — a bulk JSON file (~5MB) that maps all ~10K US public company tickers to CIKs. It is loaded once per premarket session and cached in memory.

---

## Rate Limiting and SEC Compliance

The SEC limits automated access to **10 requests/second** per IP. Exceeding this may result in temporary IP blocks or account flags.

Implementation:
- `asyncio.Semaphore(8)` — at most 8 concurrent requests (20% buffer below limit)
- Monotonic clock enforcement: minimum 125ms gap between each request release
- `User-Agent: MomentumX Trading Research admin@momentum-x.dev` — required by SEC EDGAR terms

The typical batch for a 20-stock premarket scan requires:
- 1 request: bulk CIK index (loaded once)
- 20 requests: one submissions fetch per ticker
- 0–5 requests: ATM text fetch (only for tickers with recent 424B5)
- **Total: ~21–26 requests, well within 8 req/sec limit**

---

## Integration with Faller Detection

The `FallerRiskDetector.score()` method accepts an optional `sec_result: SECFilingResult` parameter (D191 addition). When provided, three SEC signals are applied **before** all agent-derived signals, because they are facts rather than inferences:

| SEC Signal | Faller Score Effect | Config Parameter |
|---|---|---|
| `DILUTION_ATM` (424B5 + ATM language) | `+0.50` | `weight_sec_dilution_atm` |
| `DILUTION_ACTIVE` (424B5 today/yesterday) | `+0.40` | `weight_sec_dilution_active` |
| `MATERIAL_EVENT` (8-K today, no dilution) | `−0.25` | `weight_sec_material_event` |

Example scoring for a 424B5 ticker:
```
baseline:          +0.20
sec_atm_dilution:  +0.50
manipulation:      +0.14  (40% of 0.35, has catalyst flag from 8-K)
────────────────────────
total:              0.84  → REJECT (> 0.60 threshold)
position_action:  REJECT — routes to short evaluation
```

The `MATERIAL_EVENT` credit is **withheld** when dilution is also present (`dilution_detected=True`). A company issuing shares into a gap is bearish even if they also filed an 8-K.

---

## Premarket Timing

```
4:30 AM ET  Premarket scanner identifies initial candidates
4:31 AM ET  SEC prefetcher.analyze_batch(candidates) — submissions API
5:00 AM ET  Results cached in memory, available to faller detector
6:00 AM ET  EDGAR opens: new day's filings begin appearing
6:01 AM ET  Re-scan for any candidates that now have same-day 424B5
9:25 AM ET  Final faller detection run before market open
9:30 AM ET  Market open — all SEC analysis complete
```

The prefetcher is **idempotent and cache-safe**: running it twice for the same ticker returns the cached result immediately. Call `prefetcher.clear_cache()` between trading sessions.

---

## Usage Example

```python
from src.data.sec_prefetcher import SECPrefetcher, FilingSignal

async def premarket_phase_0(candidates: list[str]) -> None:
    async with SECPrefetcher() as prefetcher:
        sec_results = await prefetcher.analyze_batch(candidates)

    for ticker, result in sec_results.items():
        if result.signal == FilingSignal.DILUTION_ATM:
            print(f"{ticker}: ATM DILUTION — block long, route to short")
        elif result.signal == FilingSignal.DILUTION_ACTIVE:
            print(f"{ticker}: 424B5 DILUTION — block long")
        elif result.signal == FilingSignal.MATERIAL_EVENT:
            print(f"{ticker}: 8-K CATALYST — boost catalyst score")
        else:
            print(f"{ticker}: {result.signal.value}")

# In faller detection (after agent scoring):
assessment = faller_detector.score(
    candidate, scored, indicators,
    sec_result=sec_results.get(candidate.ticker)  # None = neutral
)
```

---

## Signal Priority

Signals are assigned in order of severity (most severe wins as primary signal):

```
DILUTION_ATM > DILUTION_ACTIVE > SHELF_REGISTERED > MATERIAL_EVENT > EARNINGS_FILED > CLEAN
```

But all detected signals are **additive** in faller scoring: a ticker with both ATM dilution and a material event gets the ATM weight only (the material event credit is withheld to prevent offset of a genuine hard-bear signal).

---

## Relationship to sec_client.py

`sec_client.py` (D-legacy) and `sec_prefetcher.py` (D191) are **complementary, not redundant**:

- `sec_client.py` → used by the ManipulationClassifier agent to build `ManipulationFilingSummary` for LLM context. Uses EFTS keyword search.
- `sec_prefetcher.py` → used by Phase 0 premarket pipeline and faller detector. Uses submissions REST API. Runs before agents, provides deterministic override.

The faller detector already consumes `ManipulationFilingSummary` indirectly (via `ManipulationSignal.filing_summary`). The D191 SEC result is a separate, direct input that bypasses LLM interpretation entirely.
