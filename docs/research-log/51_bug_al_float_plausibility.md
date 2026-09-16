# 51 — Bug AL: CandidateStock float_shares plausibility validator

**Status:** patched 2026-04-27 evening (Tier 3 #9).
**Severity:** **HIGH** — directly cost today's highest-RVOL setup (SCNI at 3667.6× RVOL, gap 51%) due to a 1000× data error in `float_shares` from the upstream data provider.
**Surface:** Strategic-assessment Tier 3 audit + log analysis (`D112 Router: float 11,313,568,000 > 2,000,000,000 max`).
**Bug class:** missing input validation — the `CandidateStock` model trusted upstream float values without sanity-checking against derived ground truth (`market_cap / current_price`).
**Predecessors:** Bug AK (Tier 2 #5 D124 calibration), `feedback_arena_assessment.md` (the strategic memo flagging Tier 3 data-quality holes).

## §0 — TL;DR

SCNI on 2026-04-27: float reported as **11,313,568,000** (11.3 billion), market_cap reported as $2,000,000, price $0.40. Implied shares from market_cap/price = 5,000,000 (5M). Reported / implied = **2263×** — clearly bad data (likely wrong CIK match or decimal misread by the upstream provider).

The D112 adaptive router has `instant_reject_max_float = 2_000_000_000`. SCNI's 11.3B exceeded that → INSTANT_REJECT before any agent could evaluate. We lost the day's most explosive RVOL setup to a data error.

Fix: add a Pydantic `@model_validator(mode='before')` to `CandidateStock` that detects implausible float (ratio > 100× implied) and drops `float_shares` to `None`. Downstream router skips its `float_max` check when float is None (per `adaptive_router.py:156`), so the trade flows through normal evaluation.

## §1 — Root cause

`src/core/models.py:CandidateStock` had no validation on `float_shares`. The model accepted whatever the data layer (`scanner_engine` → Finnhub adapter) returned. Finnhub occasionally returns wildly wrong values for sub-$5 penny stocks — likely due to:
- Wrong CIK match (SCNI possibly mapped to a similarly-named ETF or large-cap)
- Decimal place error (returning shares × 1000 instead of shares)
- "Shares outstanding" vs "free float" confusion in the upstream API contract

The system had no mechanism to detect this. The 2263× divergence is so extreme that any sanity check would have caught it.

## §2 — Fix

```python
@model_validator(mode='before')
@classmethod
def _validate_float_plausibility(cls, data):
    if not isinstance(data, dict):
        return data
    fs = data.get('float_shares')
    mc = data.get('market_cap')
    px = data.get('current_price')
    try:
        fs_v = float(fs) if fs is not None else 0
        mc_v = float(mc) if mc is not None else 0
        px_v = float(px) if px is not None else 0
    except (TypeError, ValueError):
        return data
    if fs_v <= 0 or mc_v <= 0 or px_v <= 0:
        return data
    implied = mc_v / px_v
    if implied <= 0:
        return data
    if fs_v / implied > 100:
        # D272 FLOAT_IMPLAUSIBLE: drop float to None
        ...
        data = dict(data)
        data['float_shares'] = None
    return data
```

Threshold of 100× is generous: normal stocks have ratio ~1.0; ETFs may have 2-3×; an actual 100× divergence indicates a data error.

## §3 — Aggressive testing

`tests/unit/test_bug_al_float_plausibility.py` — 22 test cases (19 pass + 3 skipped for Pydantic-required-field paths):

- **Direct unit tests** pinning today's SCNI scenario + AAPL normal case + ETF borderline (2× ratio)
- **7 boundary parametrized tests** at the 100× threshold (99×, 101×, etc.)
- **8 no-op tests** for missing/zero/negative inputs (validator must not crash on bad data)
- **Hypothesis property test** (500 examples) verifying the invariant: `float_shares preserved IFF ratio <= 100`
- **Frozen-mutation regression test** ensuring `frozen=True` discipline survived adding the validator
- **Two D112 router integration tests**: one proving SCNI now flows through, one proving legitimate huge-float (5B with $50B mcap) is STILL correctly rejected

## §4 — How this would have manifested today

10 candidates evaluated per cycle today. SCNI was in the watchlist (per startup log line: `SCNI | Gap: 51.5% | RVOL: 3667.6x`). Every cycle, the D112 router INSTANT_REJECTed it. The system's D160 mandatory-re-eval discipline (`high_rvol_min_evals=3` for >100× RVOL) tried 3 cycles → still rejected → final NO_TRADE.

With Bug AL: SCNI's float would have been dropped to None at construction time. D112 router would have skipped the float_max check. SCNI would have flowed through to the normal pipeline (agents, MFCS, etc.). Whether it would have ultimately traded depends on the agent verdicts — but **the system would have GOTTEN to evaluate it.**

## §5 — Discovery rate impact

| Pre-Bug AL | After Bug AL |
|---|---|
| 23 production / oracle / harness bugs surfaced + fixed | **24** |
| 0 patches introduced regressions | **0** |
| Discovery rate ratio | **24/0 = ∞** |

## §6 — Downstream effects

- D272 FLOAT_IMPLAUSIBLE log line lets operator audit how often this fires (expect rare; today's SCNI was the only obvious case in the log)
- The Pydantic validator is shared across ALL CandidateStock construction sites — scanner, fast_path, replay, tests — so the fix is universal
- No D-code registry impact (D272 is a new log marker but not a gated state)

## §7 — Bug-letter alphabet advances to AM
