# Immediate Fixes — Three Changes That Will Produce Trades Tomorrow

These are tonight-or-tomorrow-morning changes. They are minimal, evidence-based, and reversible. Each is justified by a specific rejection from today's journal or a finding from yesterday's 79-day backfill. Total code changed: ~30 lines across 2 files.

After these three changes, the system will trade. It may trade things that lose money — that's a separate problem (calibration), and it's the one we WANT to be solving instead of "why are we at 0/525 again."

## Fix 1 — D112 router structural gates (the killer)

**What:** Raise the float cap, lower the price floor, and add an MFCS-based override.

**Why:** Today, **5 candidates with MFCS=0.821 were rejected at D112** for float >200M. The 200M threshold was set when the system targeted micro-floats only. The user's stated goal includes "the most explosive stocks" — IMMP at 1.17B float gapping 73% IS an explosive stock. We are filtering exactly the universe the user wants.

**Where:** `config/settings.py` lines 1338–1349.

**Diff:**

```python
# config/settings.py:1338-1349

# BEFORE
instant_reject_min_price: float = Field(
    default=0.50,
    description="D112: Price below this is instant reject (absolute floor).",
)
instant_reject_max_float: int = Field(
    default=200_000_000,
    description="D112: Float above 200M shares is wrong universe for gap-and-go.",
)

# AFTER
instant_reject_min_price: float = Field(
    default=0.50,  # unchanged — sub-50¢ is illiquid; HUBC at $0.16 was right to reject
    description="D112: Price below this is instant reject (absolute floor).",
)
instant_reject_max_float: int = Field(
    default=2_000_000_000,  # 200M -> 2B (10x). IMMP, QBTS, VSA were all 200M-1.2B.
    description="D112: Float above 2B shares is wrong universe for gap-and-go. "
    "(Apr 16 hotfix: was 200M, killed 5 candidates with MFCS=0.821.)",
)
```

Plus, in `src/core/adaptive_router.py` around line 149-157, add an MFCS-based escape hatch:

```python
# src/core/adaptive_router.py — replace the float check at line 148-157

# 3. Float too large (wrong universe) — UNLESS deterministic MFCS is strong
if (
    candidate.float_shares is not None
    and candidate.float_shares > self._config.instant_reject_max_float
    and (deterministic_mfcs is None or deterministic_mfcs < 0.40)  # NEW
):
    return self._decide(
        EvalTier.INSTANT_REJECT,
        f"float {candidate.float_shares:,} > {self._config.instant_reject_max_float:,} max",
        t0,
    )
```

**Effect:** If `deterministic_mfcs >= 0.40` (per existing `deterministic_strong_pass`), the float check is bypassed. This means a high-conviction signal can override the structural gate — which is exactly the right semantics for an "instant reject" tier. Today's 5 MFCS=0.821 stocks would all pass.

**Also fix the obvious data bug at the same time:**

```python
# main.py around line 1448-1450 (D219 enrichment)

# BEFORE
if _so and _so > 0:
    _enrich["float_shares"] = int(_so * 1_000_000 * 0.80)

# AFTER
if _so and 0 < _so < 50_000:  # millions; 50B shares is the sanity ceiling
    _enrich["float_shares"] = int(_so * 1_000_000 * 0.80)
```

This catches the XHG-style "98 billion shares" data error from Finnhub before it hits the router.

## Fix 2 — VWAP bias gate (the subtle killer)

**What:** Loosen the VWAP bias rejection from "0.5% below VWAP" to "2% below VWAP" AND skip the gate entirely in the first 10 minutes after open.

**Why:** Today the journal shows 14 rejections for prices 0.6%–3.5% below VWAP. A 0.6% deviation is bid/ask spread. The Zarattini ORB research (Sharpe 2.81) explicitly enters on first move *above* the opening range — which means by definition the stock can be below VWAP for the first 5–10 minutes. Today's 14 rejections include candidates that broke out 5 minutes later.

**Where:** `src/core/orchestrator.py` around line 1315–1360 (the D101 VWAP bias check).

**Diff (sketch — exact code at runtime):**

```python
# src/core/orchestrator.py — VWAP bias check

# BEFORE
if vwap and current_price < vwap * 0.995:  # >0.5% below VWAP
    return self._reject(
        f"D101 VWAP bias: price ${current_price:.2f} is "
        f"{(vwap - current_price) / vwap * 100:.1f}% below VWAP ${vwap:.2f}"
    )

# AFTER
# Skip the gate in the first 10 minutes — VWAP is unreliable that early
# and ORB breakouts often start below VWAP.
_minutes_since_open = (now_et - market_open_et).total_seconds() / 60
if _minutes_since_open >= 10:
    if vwap and current_price < vwap * 0.98:  # >2% below VWAP (was 0.5%)
        return self._reject(
            f"D101 VWAP bias: price ${current_price:.2f} is "
            f"{(vwap - current_price) / vwap * 100:.1f}% below VWAP ${vwap:.2f}"
        )
```

**Effect:** The 14 today-rejected candidates would have proceeded to the MFCS check. The intent of the original gate (don't buy stuff that's tanking through VWAP) is preserved, but the threshold is moved from "noise" to "signal."

## Fix 3 — Force the ORB confirmation to be the FINAL gate

**What:** Re-order the gates so ORB confirmation (D219 Phase 5) is the only structural gate after MFCS scoring. Remove or relax everything else.

**Why:** From yesterday's 79-day backfill: **ORB-broken: 53% close WR, +25.9% MFE; ORB-held: 11% WR, +4.4% MFE. +41.6 percentage point edge.** This is the largest signal in the data. Every other gate has small edge (1–5pp) by comparison. If we trust one gate, trust ORB.

The current pipeline runs ORB inside the D170 observation window AFTER all the other rejection gates. That means a high-MFCS candidate can be killed by D112/VWAP before ORB even gets a chance to confirm. Reverse this: kill candidates that don't show ORB breakout, accept everything else with high MFCS.

**Where:** `src/execution/entry_delay.py` (D170 observation) and the call site in main.py.

**Approach:** Don't change the code yet — first measure with the production arena (per `02_arena_critique.md`). But the design intent is:

1. EMC scan → produces 24 candidates ✓ (currently working)
2. Brief deterministic scoring (RVOL, gap, dolvol composite) → keep top 10
3. Top-10 enter D170 observation window
4. ORB confirmation OR MFCS≥0.40 → BUY
5. Position sizing per Kelly tier → submit order

Everything else (D112 router, D101 consensus, D124 alignment, VWAP bias) becomes either a soft penalty in the MFCS score or is removed entirely.

## Verification before tomorrow morning

Before you ship Fix 1+2, run this sanity check:

```bash
cd "<repo-root>"
python -c "
from config.settings import Settings
s = Settings()
assert s.thresholds.gap_pct_max == 1.0, 'gap_pct_max should be 1.0'
print(f'D112 max_float will be: {s.router.instant_reject_max_float:,}')
print(f'D112 min_price will be: {s.router.instant_reject_min_price}')
"

# Run the new behavioral test (we wrote it this morning)
python -m pytest tests/unit/test_d219_float_enrichment.py tests/static_analysis/ -v

# Compile-check
python -m py_compile main.py src/core/adaptive_router.py src/core/orchestrator.py
```

After committing, manually trigger a brief market-hours run via the Task Scheduler:

```
schtasks /Run /TN "MomentumX-PaperTrading"
```

Watch the journal in real-time and confirm:
1. D112 rejection count drops to ~0
2. VWAP bias rejection count drops below 5
3. At least one candidate reaches the MFCS buy threshold

## What to do if the system trades and immediately loses money

That's the desired outcome of this round of changes. We need real losses to calibrate the system — currently we have zero feedback. A losing trade tomorrow is more informative than another zero-trade day.

The Kelly tier sizing is conservative (1–2% of equity per trade per `config/settings.py:1390`). Even a string of full losers caps drawdown at ~5% of $142K = ~$7K. That's the cost of switching from "system never trades" to "system trades and we learn what calibration is needed."

Once we have a few real trades, the production arena (`02_arena_critique.md`) becomes operational — we can compare backfill predictions to live results and tune.

## What NOT to change yet

- Do not touch the EMC scanner thresholds. They're producing 24 candidates per scan today. That's fine.
- Do not touch the MFCS buy threshold (0.25). It's not the bottleneck.
- Do not touch the agent weights. That's a calibration question, downstream of "does the system trade at all."
- Do not touch the consensus gates (D101 / D124). D219 Phase 2 fixed them; current rejection counts are 1 and 3 respectively.
- Do not touch position sizing or exits. They have not had a chance to be wrong yet.

Fix 1 alone, in isolation, is likely sufficient to produce 1–3 trades tomorrow. Fix 1+2 together is likely 3–6 trades. Anything beyond that is calibration territory.
