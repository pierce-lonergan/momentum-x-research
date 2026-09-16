# SEC EDGAR degradation — "unknown" state is not plumbed

**Date filed:** 2026-04-18 (D221 Phase F prep)
**Status:** Known, not yet fixed. Read-only audit. No code changed.
**Audience:** Future-Pierce, debugging a weird composite score on a day EDGAR was flaky.

---

## TL;DR

When SEC EDGAR returns 5xx (or any other fetch failure), the live risk path
emits the equivalent of "no filings found" — indistinguishable from a genuine
empty response. The deterministic hard-veto silently stands down, the LLM
risk agent gets a prompt that looks identical to a clean fetch, and MFCS has
no feature that can weight "SEC was unreachable."

The correct data shape already exists in the codebase (`FilingSignal.UNKNOWN`
in `src/data/sec_prefetcher.py`). It is structurally preserved at the source
and structurally discarded at every consumer. The fix is plumbing, not
invention.

---

## The three layers of the current (wrong) behavior

### Layer 1 — `src/data/sec_client.py:403-410` (the live fetcher)

```python
try:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()      # 5xx → httpx.HTTPStatusError
        data = resp.json()
except Exception as e:
    logger.error("SEC EDGAR search failed for %s: %s", ticker, e)
    return []                         # ← outage indistinguishable from "no filings"
```

A 5xx response raises `HTTPStatusError`, which is caught by the blanket
`except Exception` and collapsed to an empty list. The return type
(`list[Filing]`) has no `status`, `available`, or `error` field — the caller
cannot tell "EDGAR is down" from "this ticker genuinely has no recent
filings."

### Layer 2 — `src/core/orchestrator.py:1780-1807` (`_fetch_sec_filings`)

```python
try:
    filings = await self._sec_client.search_filings(...)
    return {
        "filings": [
            {"form": f.form_type, "description": f.description, "date": str(f.filed_date)}
            for f in filings[:10]
        ]
    }
except Exception as e:
    logger.warning("SEC filing fetch failed for %s: %s", ticker, e)
    return {}
```

Same pattern one level up. Even if Layer 1 had raised instead of swallowed,
this layer would collapse the exception to `{}`. Both shapes —
`{"filings": []}` from Layer 1's swallow, and `{}` from Layer 2's catch —
render identically in the downstream prompt.

### Layer 3 — `src/agents/risk_agent.py:78, 93` (the consumer)

```python
sec_filings = kwargs.get("sec_filings", {})
...
sec_text = "\n".join(f"  {k}: {v}" for k, v in sec_filings.items()) \
           if sec_filings else "  (No recent SEC filings retrieved)"
```

The prompt string is identical when EDGAR returned 200-with-no-filings vs 500.
And the system prompt at `risk_agent.py:63-65` codifies this blindness as a
rule:

> "If data is insufficient to determine a condition, use CAUTION with an
> elevated risk_score — do NOT issue a VETO. **Absence of data is NOT
> evidence of risk.**"

The deterministic hard-veto at `risk_agent.py:159-205` takes
`recent_dilution_filing: bool` as an **input**, not a fetch. If the caller
derived `False` from an empty list (for any reason), the hard veto silently
stands down. The safety net has the same blind spot as the LLM.

---

## The quiet discovery — `src/data/sec_prefetcher.py`

The correct data shape **already exists** in the repo. Lines 69-75:

```python
class FilingSignal(Enum):
    DILUTION_ATM = "dilution_atm"
    DILUTION_ACTIVE = "dilution_active"
    SHELF_REGISTERED = "shelf_registered"
    MATERIAL_EVENT = "material_event"
    EARNINGS_FILED = "earnings_filed"
    CLEAN = "clean"
    UNKNOWN = "unknown"                  # Fetch failed — no data available
```

And the result dataclass carries an explicit `error: Optional[str]` alongside
the signal (lines 95, 109). This is exactly the first-class "unknown" state
the fix needs.

**Why the shape is unused by the risk agent:**

1. `SECPrefetcher` is imported at `main.py:477` and consumed by
   `src/execution/faller_detection.py:347`. It is **not imported by
   `orchestrator.py` or `risk_agent.py`** — `grep` confirms. The live
   risk-agent path goes through `SECClient.search_filings` (Layer 1 above),
   which has no UNKNOWN shape.

2. Even the consumers that *do* touch `SECFilingResult` throw away the
   UNKNOWN state. `faller_detection.py:347-367` reads only the positive
   booleans (`atm_detected`, `dilution_detected`, `material_event_detected`).
   All three are `False` on fetch failure. `is_hard_bear()` at
   `sec_prefetcher.py:111-113` returns True only for `DILUTION_ACTIVE` /
   `DILUTION_ATM` — `UNKNOWN` is silently bucketed with `CLEAN`.

So the codebase contains two independent implementations of roughly the
same thing, one of which has the right shape and has it silently discarded,
and one of which doesn't have the shape at all and is the one the risk
agent actually sees. Both were probably built by past-Pierce.

---

## The subtle observation worth writing down

The system prompt rule "absence of data is NOT evidence of risk" is **correct
as written** for a system that can distinguish "no filings" from "couldn't
check."

In a system that **cannot** distinguish those two states — which is the one
we have — the same rule silently becomes "API outages cannot trigger
caution." That's not a prompt bug. It's a data-plumbing bug that the
prompt's correct behavior is masking.

This is the kind of thing that makes the fix feel obvious in retrospect and
very easy to punt on in the moment: the prompt looks right, the agent looks
right, the risk verdict on any given day looks right. Only when you aggregate
across a flaky-EDGAR week does the bias become visible — and by then you're
debugging composite scores, not SEC plumbing, and the connection is not
obvious.

---

## The three-part fix (when you're ready, not now)

### 1. Return shape with explicit fetch status

In `orchestrator.py:_fetch_sec_filings`, return something that can carry
"unavailable" as a first-class state:

```python
{
    "filings": [...],
    "fetch_status": "ok" | "unavailable" | "rate_limited" | "timeout",
    "error": str | None,
}
```

Alternatively: raise a typed `SECDataUnavailable` exception the caller
handles differently from "success with zero filings." Both are acceptable.
The return-shape version is lower friction for the many call sites that
currently treat `{}` as a valid empty result.

`sec_client.search_filings` should either return the same richer shape or
raise typed exceptions that the orchestrator catches and translates. No
more blanket `except Exception: return []`.

### 2. Risk agent prompt must render fetch status explicitly

In `risk_agent.py:build_user_prompt`, change the SEC section to:

```
--- SEC FILINGS ---
  fetch_status: unavailable  (EDGAR returned 502 at 09:32:17)
  (Note: filings could not be verified this run. Treat as UNCHECKED,
   not as evidence of safety.)
```

And update the system prompt. The current rule

> "Absence of data is NOT evidence of risk."

should become

> "If `fetch_status == 'ok'` and `filings == []`, absence of filings is NOT
> evidence of risk.
> If `fetch_status != 'ok'`, SEC data is UNCHECKED. Do not approve trades
> whose thesis depends on SEC verification (e.g., 'no recent dilution')
> without acknowledging this gap. Elevate `risk_score` by at least 0.15 and
> include 'sec_unchecked' in `critical_risks`."

### 3. MFCS feature so the composite can weight it

Add `sec_data_available: bool` (or `sec_fetch_status: str`) to the feature
vector. Log it in `features_labeled.jsonl`. After the next retrain the
composite can learn whatever the right discount is on EDGAR-flaky days
rather than treating them as equivalent to clean days. This is the one part
of the fix that has latency — the signal only becomes useful after a retrain
with enough flaky-day samples, probably 1-2 months.

---

## Engineering time estimate

- **3-4 hours, disciplined:** layers 1-2 return-shape change, prompt edit,
  MFCS feature plumbing, one new unit test per layer. Do not touch the
  prefetcher.
- **Full day (or more), if you refactor the prefetcher in the same PR:**
  the temptation will be strong, because the right shape already lives
  there and it's tempting to migrate the orchestrator onto `SECPrefetcher`
  instead of patching `SECClient`. **Resist that urge when the time comes.**
  The prefetcher's consumers (`faller_detection.py`, `main.py:1384-1386`)
  also ignore the UNKNOWN state — fixing them in the same PR doubles the
  scope, doubles the test surface, and delivers the plumbing fix behind a
  larger, slower review. Ship the narrow fix first. Consolidate later.

---

## References

- `src/data/sec_client.py:381-412` — `SECClient.search_filings` (Layer 1)
- `src/core/orchestrator.py:1780-1807` — `_fetch_sec_filings` (Layer 2)
- `src/agents/risk_agent.py:74-117, 159-205` — `build_user_prompt` and
  `apply_hard_veto_rules` (Layer 3)
- `src/data/sec_prefetcher.py:60-127` — `FilingSignal.UNKNOWN`,
  `SECFilingResult.error` (the shape that exists and is unused)
- `src/execution/faller_detection.py:343-367` — consumer that discards
  the UNKNOWN state
- `main.py:477, 1384-1386` — only live import of `SECPrefetcher`
