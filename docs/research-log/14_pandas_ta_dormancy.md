# 14 — pandas_ta Dormancy: Break-Date Investigation

**Mon 2026-04-20** — written immediately post-fix (commits 69dc84f, 59a1a8e).
20-minute time budget. Findings nuanced; recommendations narrow.

## TL;DR

The "silent pandas_ta dormancy" is **partial, not total**.

- `_compute_indicators` in `src/agents/deterministic_technical.py` has been
  returning `{}` since at least **August 6, 2025** (~8.5 months) — every
  call hit the `except ImportError: return {}` branch.
- BUT `main.py:2025` independently computes indicators via the **pure-Python
  `src.data.technical_indicators.compute_indicators`** module and threads
  them in as `pre_indicators` (kwarg `indicators=...`).
- The deterministic technical agent **merges** `pre_indicators` over the
  empty `computed` dict, so most signals still flow.
- **Three concrete losses** persist regardless:
  1. `atr_14` — pre_indicators **never** provides this; ATR-based stop sizing
     silently falls back to flat ±5%/±10% instead of the D100 ATR-anchored stops.
  2. `recent_high` / `recent_low` — pre_indicators never provides these;
     `CONSOLIDATION_BREAKOUT` and `ASC_TRIANGLE` pattern detection are dead.
  3. `bb_mid` — pre_indicators uses `bb_middle` (different key); cosmetic
     reasoning-string difference, no behavioral change because `_detect_pattern`
     reads `bb_width` (correctly populated).

## Forensic timeline

| Date | Event | Source |
|------|-------|--------|
| 2026-03-12 | D101 deterministic_technical.py introduced (commit 2b11f53). Used `import pandas_ta as ta` inside `_compute_indicators`. | `git log --follow` |
| 2025-08-03 23:34 EDT | NumPy 2.2.6 installed on this machine. NumPy 2.0 removed the `NaN` alias (renamed to `nan`). | `numpy/__init__.py` mtime in site-packages |
| 2025-08-06 00:22 EDT | pandas_ta 0.3.14b0 installed (3 days after numpy 2.x). Its `momentum/squeeze_pro.py` line 2 does `from numpy import NaN as npNaN` → ImportError forever. | `pandas_ta/__init__.py` mtime in site-packages |
| 2025-08-06 → 2026-04-20 | **Dormancy window**: every call to `_compute_indicators` hit the silent `except ImportError: return {}`. ~8.5 months. | Inferred — log retention only goes back to 2026-04-10 (8 days). |
| 2026-04-20 19:19 EDT | Caught during post-mortem of Mon 09:40 EDT crash. pandas_ta_classic 0.4.47 installed. Module-level backend probe added (no longer silent). | This commit. |

**Note on log retention.** 2026-04-10 is the earliest `momentum_*.log` we still
have. Grep for `pandas_ta not available` across all 143 log files: zero hits.
That's not because the warning didn't fire — it's because the warning fired
inside `_compute_indicators` only after the `if not bars or len(bars) < 5`
guard, and most calls never made it past that guard (the price_data-not-loaded
path returns before importing). When it *did* fire, the WARNING-level line
was logged but is buried inside daily logs that have since rotated out.
Conservative claim: **dormancy began no later than August 6, 2025**, 8.5
months ago. We cannot prove an exact start date from available logs.

## Why the technical agent kept producing reasonable signals

Path through the technical agent on a real candidate:

```
main.py:2025
  pre_indicators = compute_indicators(bars)        # pure-Python, works
  market_data["indicators"] = pre_indicators
  …
deterministic_technical.analyze(indicators=market_data["indicators"]):
  computed = _compute_indicators(bars)             # returns {} (dormant)
  if pre_indicators:                               # YES
      for k, v in pre_indicators.items():
          if k not in computed:                    # always True (computed=={})
              computed[k] = _safe_float(v)         # backfill from pre-computed
  # computed now == pre_indicators
```

So the **agent reads from pre_indicators**. Sample log line from
`logs/momentum_2026-04-17.log`:

```
09:30:33 D101 TECH NVTS: signal=BEAR conf=0.00 pattern=NONE breakout=False
  RVOL=2.6 VWAP=BELOW | MACD histogram positive (0.0089);
  EMA(21) > EMA(9) — bearish alignment; Price below VWAP — bearish bias
```

`MACD histogram positive (0.0089)` is a real, non-zero value — it came from
pre_indicators (which the pure-Python module computes correctly). MACD,
RSI(9), EMA(9), EMA(21), Bollinger Bands, VWAP, support/resistance all flow
through.

## The three concrete losses

Cross-reference of what `_compute_indicators` provides vs what
`compute_indicators` (pure-Python, in `src/data/technical_indicators.py`)
provides, vs what the agent reads:

| Indicator key | _compute_indicators | pre_indicators | Agent reads at | Effect of dormancy |
|---------------|---------------------|----------------|----------------|--------------------|
| `rsi_9` | ✓ | ✓ | line 326 | None (pre fills) |
| `macd_histogram` | ✓ | ✓ (12,26,9 standard) | line 327 | None (slightly different params; D87 wanted 5,13,4 but got 12,26,9) |
| `ema_9` | ✓ | ✓ | line 328 | None |
| `ema_21` | ✓ | ✓ | line 329 | None |
| `bb_upper` | ✓ | ✓ | line 193 | None |
| `bb_lower` | ✓ | ✓ | line 194 | None |
| `bb_mid` | ✓ | ✗ (uses `bb_middle`) | line 165–170 | Cosmetic — _detect_pattern uses bb_width directly |
| `bb_width_pct` | ✓ (derived) | ✓ (computed directly) | line 195, 198 | None |
| **`atr_14`** | **✓** | **✗** | **line 314** | **Always 0 → flat ±5%/±10% stops instead of ATR-anchored** |
| **`recent_high`** | **✓** | **✗** | **line 198** | **Always 0 → CONSOLIDATION_BREAKOUT, ASC_TRIANGLE patterns dead** |
| **`recent_low`** | **✓** | **✗** | line 198 | Used only with recent_high (also dead) |
| `last_close` / `last_high` / `last_low` | ✓ | ✗ | unused in pattern logic | Latent — no current consumer |

Three things have been silently degraded for 8.5 months:

### Loss #1: ATR-based stops fall back to flat percentages

`deterministic_technical.py:314-323`:
```python
atr = computed.get("atr_14", 0)
if atr > 0 and current_price > 0:
    projected_target = current_price + (atr * 3.0)  # 3R target
    stop_loss_level = current_price - (atr * 1.5)   # 1.5R stop
elif current_price > 0:
    projected_target = current_price * 1.10         # Default +10%
    stop_loss_level = current_price * 0.95          # Default -5%
```

Since `atr_14` was always 0, every `TechnicalSignal.stop_loss_level` and
`projected_target` from this agent has been the flat-percentage fallback
since dormancy started. **D100 was a stop-anchoring decision that has been
silently dead in this agent's output.**

Mitigation: this is the AGENT's recommendation; the orchestrator computes
its own ATR stop in `_build_trade_verdict` via `_compute_atr_stop()`
(orchestrator.py:2751–2758), which uses a separate ATR cache and works.
The agent's stop_loss_level is advisory. Net effect on positions: minimal.

### Loss #2: CONSOLIDATION_BREAKOUT and ASC_TRIANGLE patterns are dead

`_detect_pattern` (line 175–228):
```python
recent_high = indicators.get("recent_high", 0.0)  # always 0

if recent_high > 0 and current_price > recent_high:    # dead
    if ema_9 > ema_21 > 0:
        return "CONSOLIDATION_BREAKOUT", True, "15min"
    ...

if recent_high > 0 and current_price > recent_high * 0.97:  # dead
    if ema_9 > ema_21 > 0 and rsi > 50:
        return "ASC_TRIANGLE", current_price > recent_high, "15min"
```

Two of the four pattern types in this agent (`BB_SQUEEZE`, `BULL_FLAG`
still fire; `CONSOLIDATION_BREAKOUT`, `ASC_TRIANGLE` cannot fire because
`recent_high == 0`).

**Quick scan of `logs/momentum_2026-04-17.log`**: 0 occurrences of
`CONSOLIDATION_BREAKOUT` or `ASC_TRIANGLE`. Pattern distribution that day:
`GAP_MOMENTUM` (D126 override, computed directly from gap+rvol), `NONE`,
occasional `BULL_FLAG`. Hypothesis confirmed.

### Loss #3: `bb_mid` cosmetic mismatch

Pure-Python module emits `bb_middle`; D101 reads `bb_mid`. The pre_indicators
backfill copies whatever keys exist; `bb_mid` is never set; default 0.
But _detect_pattern reads `bb_width` (correctly populated via direct
computation in the pure-Python module), so pattern detection is unaffected.
Cosmetic only.

## Reroute on the regime-vs-overfit question

The Saturday composite-retrain debate (v0 → v1 CV AUC delta -0.18, blocked
by stability gate) had two competing explanations:
1. **Regime drift**: market structure changed; v0 weights are stale.
2. **Overfit**: v0 was lucky; the broader data exposed it.

**This investigation surfaces a third candidate**: feature-availability
drift. Specifically, the v0 model was trained on a feature distribution
that included ATR-based stops and `CONSOLIDATION_BREAKOUT` /
`ASC_TRIANGLE` patterns. After August 2025 those features silently went
to `0` / `NONE`. v1 was trained on the dormant distribution. So the AUC
collapse may reflect **the same model architecture trained on two
different feature distributions**, not regime change and not overfit.

**Tuesday morning audit checklist amendment**:

> Add a `pandas_ta_dormancy_active` column to the regime stratification:
> True for all rows on/after 2025-08-06 (conservative break-date),
> False before. If AUC delta tracks with that column rather than VIX
> regime, the overfit-vs-regime question has a third answer
> (feature-availability drift) and the right next step is **retraining
> with `recent_high`, `recent_low`, and `atr_14` populated** rather
> than weight tuning.

Caveat: every training row before 2026-03-12 (D101 introduction date)
predates the deterministic agent entirely, so the affected rows are only
the ones from 2026-03-12 → present that overlap with the dormancy
(2025-08-06 → present). Net affected window: 2026-03-12 → 2026-04-20,
~5.5 weeks of training data.

## What we cannot say from this investigation

- **Exact dormancy start date** — log retention is 8 days; pandas_ta install
  date is from filesystem mtime (could have been re-installed Aug 6 after a
  prior install). Conservative claim: ≤ Aug 6, 2025.
- **Which trades would have triggered if patterns fired** — would need to
  replay the dormancy window with `recent_high`/`atr_14` populated to count
  pattern hits. Not in scope for tonight.
- **Whether the v0 model's training data ever included non-zero
  `recent_high`** — depends on whether early D101 runs had `pandas_ta`
  working (i.e., whether the dev machine's numpy was <2.0 at training time
  in March). Has to be answered by inspecting feature-vector journals.

## Recommendations (Tuesday)

1. **Add `recent_high`, `recent_low`, `atr_14` to
   `src/data/technical_indicators.compute_indicators`** so pre_indicators
   provides everything D101 reads. Then `_compute_indicators` becomes a
   defense-in-depth layer rather than the primary source.
2. **Add a feature-vector audit** that asserts `recent_high > 0` for >50%
   of feature_log rows over the past 7 days. Fail loudly if not.
3. **Run a regime stratification with `pandas_ta_dormancy_active` column**
   as described above. Decision-relevant for the v0/v1 weight question.
4. **Consider migrating `src/data/technical_indicators.py` patterns into
   the deterministic agent** entirely, removing the pre_indicators
   bidirectional dependency. Single source of truth for indicator names.

## Process meta-finding

This is the second 8-month silent dormancy this quarter (D158 SEC dilution
classifier was the first). Both shared the pattern:

> A try/except guard with a benign-looking `return {}` on the failure
> branch. No log line that says "this catch fired N times in the last
> minute". No metric. No test that asserts the success path actually
> populated something.

The hardened bug-sweep template rule (e) added today addresses the
test gap. It does not address the "no observability that the catch
fired" gap. That's a structural change for next week:

> Every defensive `except` in the hot path emits a counter that
> appears in the heartbeat. If the counter is non-zero for >5 minutes
> the heartbeat shows it.

Not in scope for tonight. Logged for the Tuesday architecture call.
