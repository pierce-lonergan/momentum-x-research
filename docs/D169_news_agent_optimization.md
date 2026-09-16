# D169: News Agent Optimization — Catalyst Detection Fix

**Date**: 2026-04-02
**Branch**: claude/confident-hoover
**Status**: Implemented

---

## Problem Statement

LLM Arena (509 scenarios, D168) exposed catastrophic news agent performance:

| Metric | Before | Target |
|--------|--------|--------|
| Direction accuracy | 55.7% | 75%+ |
| F1 score | 0.000 | 0.50+ |
| Overconfidence | 73.9% | <40% |
| False BULL rate | 40.7% of calls | <20% |

**Arena finding**: Every winning trade had a real catalyst. Every losing BULL call was on promotional content — generic listicle headlines treated as catalysts.

---

## Root Cause Analysis

### Failure Mode A: Promotional headline confusion (primary)

The old system prompt said:

> "ANY identifiable news that explains why a stock gapped (corporate updates, sector moves, crypto/commodity correlation, regulatory news, social media momentum) is a valid catalyst"

This is the root cause. "12 Healthcare Stocks Moving Pre-Market" matches "sector moves" — it's technically news about sector movement. The LLM correctly followed the instruction and returned BULL. The instruction was wrong.

### Failure Mode B: SECTOR_CATALYST / CORPORATE_UPDATE too permissive

The `SECTOR_CATALYST` type was designed for "Bitcoin rallying drives crypto miners." In practice, it was being assigned to roundup articles where no specific company event existed. The catalyst type `CORPORATE_UPDATE` similarly captured vague announcements with no specific terms.

### Failure Mode C: No pre-screening of headlines

All headlines — including "Top 10 Penny Stocks to Watch Today" — went directly to the LLM. The LLM had no mechanism to reject these before spending 5-15s analyzing them.

---

## Changes Implemented

### 1. Deterministic headline pre-filter (`_LISTICLE_RE`, `_filter_promotional_headlines`)

A compiled regex catches 15+ patterns of promotional/roundup headlines before the LLM sees them:

```
- "12 Biotech Stocks Moving Pre-Market" → blocked
- "Top 10 Stocks to Watch Today" → blocked
- "Pre-Market Movers: Big Gainers" → blocked
- "Morning Brief: Today's Top Movers" → blocked
```

Multi-ticker heuristic: if a headline contains 3+ distinct ALL-CAPS tokens that look like tickers (not in a stop-word list), it's a roundup article.

If **all** headlines are filtered out → `NEUTRAL` returned immediately. **No LLM call.** This eliminates:
- False BULL signals from promotional content
- LLM latency on no-catalyst stocks (majority of candidates)

### 2. Rewritten system prompt

Old prompt gave the LLM permission to be permissive ("ANY identifiable news"). New prompt is explicitly restrictive:

**Added**: "WHAT IS NOT A CATALYST" section with 7 explicit exclusion rules
**Added**: 8 few-shot examples (4 REAL, 4 NOT REAL) with correct outputs
**Removed**: "ANY identifiable news...is a valid catalyst" — the root cause line
**Changed**: Decision tree now starts with "Does a specific press release or SEC filing exist?"

### 3. Two-stage `analyze()` override

```python
async def analyze(self, ticker, **kwargs):
    # Stage 1: deterministic pre-filter (~0ms)
    filtered = _filter_promotional_headlines(news_items, ticker)
    if n_original > 0 and n_filtered == 0:
        return NEUTRAL  # no LLM call

    # Stage 2: LLM analysis on specific headlines only
    kwargs["news_items"] = filtered
    return await super().analyze(ticker=ticker, **kwargs)
```

### 4. User prompt filter note

When headlines pass the pre-filter, the user prompt now tells the model:

> "These N headlines have passed a specificity pre-filter that removed generic roundups and multi-stock listicles. If the remaining headlines still lack a specific company catalyst, return NEUTRAL."

This reinforces the instruction that the model should remain skeptical even of filtered headlines.

---

## What Was NOT Changed

- `parse_response()` invariants — these are correct and tested
- `NewsSignal` model fields — no new fields needed
- Confidence deflation factor (0.65) — still applied by `BaseAgent`
- Scoring weight (0.25) — remains at D168 level
- Fallback chain — primary/fallback/emergency model chain unchanged

---

## Test Coverage

New tests in `tests/unit/test_news_agent.py`:

**`TestHeadlinePreFilter`** (15+ tests):
- 15 parametrized promotional headline patterns that should be caught
- 9 parametrized specific catalyst headlines that should pass through
- Multi-ticker listicle detection
- Filter removes promotional, keeps specific
- Empty input, all-promotional, all-specific edge cases

**`TestNewsAgentAnalyzePreFilter`** (3 tests):
- `all_promotional_returns_neutral_no_llm`: confirms `_call_llm` is never called
- `empty_news_calls_llm`: empty news list still goes to LLM (no-news path unchanged)
- `mixed_headlines_filters_and_calls_llm`: mixed input correctly filters then calls LLM

---

## Expected Impact

| Failure Mode | Before | After |
|---|---|---|
| Promotional BULL calls | ~40% of calls | Near 0% (pre-filter) |
| LLM calls on roundup stocks | 100% | ~10-20% (pre-filter exits early) |
| LLM given explicit NOT-A-CATALYST rules | 0 | 7 explicit rules + 4 examples |
| Few-shot examples in prompt | 0 | 8 (4 real, 4 not real) |
| Overconfidence | 73.9% | Expected <40% |
| Direction accuracy | 55.7% | Expected 75%+ |

---

## Arena Validation

To measure improvement:

```bash
python scripts/run_llm_arena.py seed
python scripts/run_llm_arena.py replay --agent news --save news_d169
python scripts/run_llm_arena.py score --results news_d169
python scripts/run_llm_arena.py compare --baseline news_baseline --variant news_d169
```

The pre-filter improvement is deterministic — any scenario where the only headlines are roundup articles will flip from BULL to NEUTRAL regardless of model behavior.
