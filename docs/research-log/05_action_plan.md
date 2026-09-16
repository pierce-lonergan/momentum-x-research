# Action Plan — 7 Days from Zero Trades to a Calibrated System

This is the executable plan. Each day has a specific goal, a verification step, and a fallback if the goal isn't hit. No vague aspirations.

## TL;DR

- **Tonight (Day 0):** Ship the D112 + VWAP fixes. Manual trigger tomorrow morning.
- **Day 1 (Apr 17, Friday):** Watch first trades execute. Capture data. Don't tune yet.
- **Day 2-3 (weekend):** Build the production arena. Validate against backfill.
- **Day 4-5 (Apr 20-21):** Train composite score. Run in shadow mode.
- **Day 6-7 (Apr 22-23):** Cutover to composite, remove obsolete gates.

By next Friday (Apr 24), the system is trading regularly with calibrated sizing and a single-score architecture.

## Day 0 (TONIGHT, April 16) — Ship the immediate fixes

### Tasks

1. Edit `config/settings.py` line 1343:
   ```python
   instant_reject_max_float: int = Field(default=2_000_000_000, ...)
   ```
2. Edit `src/core/adaptive_router.py` line 149-157 to add the MFCS-based escape hatch (per `03_immediate_fixes.md`).
3. Edit `main.py` around line 1448 to add the float sanity check (`< 50_000`).
4. Edit `src/core/orchestrator.py` VWAP bias check to use 2% threshold and skip first 10 minutes.
5. Compile-check, run the static-analysis suite, run the D219 enrichment test.
6. Commit with message: `D220 Day 0: unblock trades — D112 float cap 200M->2B, VWAP 0.5%->2%`.
7. Push to develop. Verify Task Scheduler picks up the new code at 4:30 AM tomorrow.

### Verification

```bash
python -m pytest tests/static_analysis/ tests/unit/test_d219_float_enrichment.py -v
python -c "from config.settings import Settings; s=Settings(); print(f'D112 max_float: {s.router.instant_reject_max_float:,}')"
```

Expected: 11+ tests pass; D112 max_float prints as `2,000,000,000`.

### Fallback if shipping breaks

- Revert with `git revert HEAD`. The previous behavior (no trades) is the worst outcome of failure, which is acceptable.
- Check `tests/static_analysis/_baselines.json` — if `frozen-mutate` jumped to 1, the change accidentally introduced a regression.

## Day 1 (April 17, Friday) — First trades, observation only

### Tasks

1. Verify Task Scheduler kicked off at 4:30 AM. Watchdog reports healthy.
2. Watch the journal during pre-market scan (7:30–9:30 AM ET).
3. After market close (4:00 PM ET): review the day's session report.

### What success looks like

- D112 rejection count: <5 (vs 65 today)
- VWAP bias rejection count: <5 (vs 14 today)
- BUY verdict count: 1–6 (vs 0 today)
- At least 1 trade executed (open AND close)

### What to capture

For each trade, record:
- Ticker, entry price, entry time, exit price, exit time, P&L
- MFCS at entry, agent breakdown (news, technical, risk, manipulation)
- Was ORB confirmed before entry?
- Did the trade go through D170 observation?
- Final exit reason (TP1, TP2, stop, trailing, end-of-day)

### What NOT to do

- DO NOT tune any thresholds during the trading day. Let it run.
- DO NOT add new gates if losses occur. We expect some losses; that's the point.
- DO NOT pause new entries unless drawdown exceeds 5% of equity.

### Fallback

If the system still produces zero trades after the Day 0 fixes:
- Manually inspect the journal for the new top rejection reasons
- The next bottleneck is likely the MFCS threshold or D170 observation
- Apply the "one weird trick" from `04_structural_redesign.md`: bypass everything when MFCS ≥ 0.50

## Day 2 (April 18, Saturday) — Build the production arena (part 1)

### Tasks

1. Create `src/production_arena/` directory structure per `02_arena_critique.md`.
2. Build `scenarios.py` — load `data/backfill/candidates.jsonl` + `data/bar_recordings/` into Scenario objects.
3. Build `pipeline_runner.py` — call `orchestrator.evaluate_candidate()` against a scenario with mocked clock and bars.
4. Verify against 1 known scenario (e.g. AHMA on April 14 — should reject with current config).

### Verification

```bash
python -m src.production_arena.cli --date 2026-04-14 --ticker AHMA
```

Output should show the gate-by-gate rejection cascade matching the actual April 14 journal entry for AHMA.

### Fallback

If wiring `evaluate_candidate()` proves too entangled with global state, fall back to a "replay simulator" that reads the actual journal entries and re-applies just the gate logic. Less faithful but faster to build.

## Day 3 (April 19, Sunday) — Production arena part 2 + counterfactual sweeps

### Tasks

1. Build `aggregator.py` — per-day, per-gate counts from a date range.
2. Build the counterfactual sweep: "if D112 max_float was X, +N trades, +Y% return."
3. Run against the full 79-day backfill (Dec 11 – Apr 14).
4. Generate a one-page report: `docs/research-log/06_arena_first_run.md`.

### Verification

The arena should reproduce the actual zero-trade outcome of April 14 and 15. If it produces 5 trades on April 14, the simulation is wrong (probably skipping a gate).

### Key sweeps to run

- `instant_reject_max_float` ∈ {200M, 500M, 1B, 2B, 5B, ∞}
- `instant_reject_min_price` ∈ {$0.50, $1.00, $2.00}
- VWAP bias threshold ∈ {0.5%, 1%, 2%, 3%, off}
- MFCS buy threshold ∈ {0.20, 0.25, 0.30, 0.35, 0.40, 0.50}

For each combination: report (n_trades, win_rate, avg_return, max_drawdown, Sharpe).

## Day 4 (April 20, Monday) — Real Monday data + composite score V0

### Tasks (during market hours)

1. Watch live trading. Capture session data.
2. Review the weekend's arena results vs Friday's actual results.

### Tasks (after market close)

1. Train logistic regression on the 407-row backfill predicting `close > 0`.
2. Validate via 5-fold cross-validation.
3. Compute calibration: do the predicted probabilities match actual frequencies?
4. Output: a single function `composite_score(candidate, agents) -> float` returning a probability.

### Verification

The trained model's AUC should be > 0.65 (above coin-flip with margin). If <0.55, we're missing key features and need to add catalyst type, manipulation classifier output, dilution flag.

## Day 5 (April 21, Tuesday) — Shadow mode integration

### Tasks

1. Add the composite score to the orchestrator alongside MFCS.
2. Log both scores to the journal for every candidate.
3. Do not change any trade decisions yet — shadow mode only.
4. Watch live trading: collect today's MFCS vs composite_score pairs.

### Verification

- Both scores should be present on every journal entry.
- Composite scores should correlate with MFCS but not perfectly (otherwise it's redundant).
- Composite scores should be in [0, 1] and well-distributed (not clustered at 0 or 1).

## Day 6 (April 22, Wednesday) — Composite-based decisions in parallel

### Tasks

1. Add a "shadow buy" signal: log when the composite score would have triggered a buy (>0.55 threshold or whatever calibration suggests).
2. Compare shadow buys to actual buys for the day.
3. Update `docs/research-log/06_shadow_results.md` with the comparison.

### Verification

If shadow buys are 80%+ aligned with actual buys: the composite is confirming the existing logic. Good.
If shadow buys diverge significantly: investigate. The composite may be capturing signal the cascade misses (or vice versa).

## Day 7 (April 23, Thursday) — Cutover to composite, remove obsolete gates

### Tasks

1. Switch the BUY decision to use the composite score as primary.
2. Keep MFCS as a logged-only metric for backward comparison.
3. Delete the `instant_reject_min_price`, `instant_reject_max_float` from D112 (they're folded into composite).
4. Loosen or delete the VWAP bias gate.
5. Verify ORB confirmation still runs as the final structural gate.
6. Tag the release: `git tag d220-composite-cutover`.

### Verification

After cutover, the journal should show:
- 1 score (composite) per candidate
- 1 BUY threshold (composite > 0.55)
- 1 final structural gate (ORB confirmation)
- Multiple soft penalties (logged but not cause for hard rejection)

## Risk management throughout

- **Daily loss limit:** 5% of equity ($7,100 from $142K). If exceeded, pause new entries via `/pause` endpoint.
- **Per-trade size:** Kelly tier 1 (2% of equity = $2,840 max). Don't override.
- **Max concurrent positions:** 3. Don't override.
- **Watchdog:** Already running. Don't disable.
- **Health alerts:** Already wired to Discord. Watch the channel.

## Definition of done

By end of Day 7 (April 23), the system has:

1. Made at least 5 trades (regardless of P&L)
2. A working production arena that matches actual session outcomes within ±10%
3. A trained composite score with AUC > 0.65 on backfill
4. Cascade architecture replaced by single-score architecture
5. Documentation updated to reflect the new architecture (`docs/system_architecture_master.md`)
6. All static-analysis tests still passing

## What this plan does NOT do

- Does not retrain LLM agents (deferred — too risky during stabilization).
- Does not change exit logic (current 6 strategies work; changing them is high-risk).
- Does not add new data sources (Ortex, alternative data — deferred to D221+).
- Does not implement multi-archetype models (deferred per D219 plan).
- Does not change Kelly sizing (current Tier 1 = 2% is appropriate while we stabilize).

## Final note

The system is one good week away from being a functioning trading system. The hardest part — agent infrastructure, scanner, GEX, sizing logic, exit intelligence, monitoring — is done. The remaining work is **simplifying the decision layer** so the rest can speak.

If on April 23 we have made 5 trades and learned something about calibration, this review was a success — even if the trades lost money. The current state (0 trades, 0 information, 0 calibration feedback) is strictly worse than any outcome where trades execute.
