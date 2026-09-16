# Session Log — 2026-03-17 (Tuesday)

**First live trading day with D113-D115 changes deployed.**

---

## Session Summary

| Metric | Value |
|--------|-------|
| Session | 04:30 – 16:00 ET |
| Equity | $139,449.15 |
| Trades entered | 1 (NVTS) |
| Trades closed | 1 (D98 stop-out) |
| Realized P&L | **-$248.01 (-0.18%)** |
| Scans | 288 |
| Evaluations | 130 |
| Debates triggered | 0 |
| BUY verdicts | 1 |
| D92 fallback events | 81 (29 successful) |
| Total errors | 43 |
| Circuit breaker | OK (never tripped) |
| Phase timing | Phase 0: 20s, Phase 2: 86s avg, Phase 3 cycles: 352 |

---

## Trade: NVTS (Navitas Semiconductor)

| Field | Value |
|-------|-------|
| Entry time | 09:45:21 ET |
| Entry price | $10.61 |
| Quantity | 425 shares |
| Position size | 3.2% of equity ($4,509) |
| Stop loss | $8.43 (ATR-based, 20.6% distance) |
| Catalyst | GaNFast DC-DC power delivery board announcement |
| MFCS | 0.212 |
| Kelly tier | 1 (STANDARD) — failed higher: MFCS<0.6, <3 directional agents, PRODUCT_LAUNCH not in proven list |
| VIX sizing | Halved (VIX=26.1 > 25 threshold) |
| Exit | D98 stop-out at ~$10.03 |
| Exit time | ~10:00:44 ET |
| Hold time | ~15 minutes |
| P&L | -$248.01 |

### Agent Signals at Entry
| Agent | Signal | Confidence (raw/deflated) |
|-------|--------|--------------------------|
| news_agent | BULL | 0.60 → 0.42 |
| fundamental_agent | NEUTRAL | 0.50 → 0.35 |
| risk_agent | APPROVE | risk=0.05 |
| technical_agent | **ERRORED** | float(None) crash |
| institutional_agent | SKIPPED | weight=0.00, no data |
| deep_search_agent | SKIPPED | weight=0.00, no data |
| manipulation_classifier | ORGANIC_MOMENTUM | prob=0.20 |

### Sizing Chain
```
risk=$1,394 (1.0% of $139,449)
  / stop_dist=$2.18 (ATR-based)
  → qty_risk=638 shares
  → qty_cap=425 (3.2% cap, VIX halved from 6.4%)
  → final=425 shares @ $10.61 = $4,509
```

### Post-Mortem
NVTS closed the day at approximately $10.83 (per Investing.com). Our entry at $10.61 and stop-out at ~$10.03 occurred during early morning weakness. The stock recovered through the day. The stop-out was triggered ~15 minutes into the trade — the 20.6% ATR-based stop was wide but the position was exited via broker-side stop at a much smaller loss ($0.58/share = 5.5%). **The D98 stop-out detection found the position "gone from broker" suggesting the Alpaca stop order executed at market.**

**Verdict: BAD ENTRY TIMING.** The stock eventually recovered but we entered during volatile open action and got shaken out. The product launch catalyst was real but didn't provide enough intraday momentum.

---

## Watchlist — Actual Price Action vs Our Decisions

| Ticker | Gap | RVOL | Our Decision | Day's Range | Close | Day Change | Correct Call? |
|--------|-----|------|-------------|-------------|-------|------------|---------------|
| **CTMX** | 47.4% | 42.3x | NO_TRADE (consensus gate) | $6.04–$8.20 | ~$7.30 | +56% day 1, **-19.26% day 2** | **CORRECT — Would have been a trap. Down 19% next day.** |
| **NVTS** | 5.0% | 2.1x | BUY → stopped out | — | ~$10.83 | +7% | MIXED — Right stock, bad timing |
| **BMNR** | 13.9% | 0.9x | REJECT (low RVOL) | $22.97–$23.85 | ~$23.47 | +0.3% | **CORRECT — Flat, no edge** |
| **ETHA** | 10.3% | 1.2x | REJECT (low RVOL) | $17.03–$17.75 | ~$17.61 | +10.8% | MIXED — Decent move but ETF, not our edge |
| **AAL** | 6.7% | 0.8x | REJECT (low RVOL) | — | ~$10.93 | +4.2% | **CORRECT — Slow grind, not gap-and-go** |
| **OPEN** | 5.3% | 1.3x | REJECT (low RVOL) | $4.97–$5.23 | ~$5.25 | +6% | **CORRECT — Modest, not our setup** |

### Phase 3 Candidates (scanned but not evaluated)
| Ticker | Day Change | Notes |
|--------|-----------|-------|
| **LNAI** | **+189%** | **REAL MISS.** $0.66 stock, 63.6x RVOL, $3.6M dolvol. Blocked by: (1) EMC price filter ($0.66 < $3.00), (2) dollar volume filter ($3.6M < $10M). D116 fix: extreme-RVOL override + lower dolvol threshold. |
| **BIAF** | **+39%** | **REAL MISS.** $2.73 stock, 43% gap. Blocked by D112 pump pattern instant-reject. D116 fix: route pump patterns to full pipeline for agent evaluation. |
| JTAI | — | Scanned, not evaluated |
| WNW | — | Rejected (price < $3.00) |
| LIDR | — | Scanned, not evaluated |

---

## CORRECTION: CTMX Was NOT a Miss

**Initial analysis identified CTMX (+56% on day 1) as the day's biggest missed opportunity. This was WRONG.**

CTMX is **down 19.26% the next day (2026-03-18)**. Had we traded it, we would have been holding an intraday gapper that reversed hard the following session — exactly the kind of trap we want to avoid. The consensus gate blocking CTMX (due to BUG-001 killing technical_agent) was accidentally correct. The system protected us from a loss.

**Lesson: Single-day price action is not sufficient to judge a miss. Multi-day follow-through matters.**

---

## The Real Misses: BIAF (+39%) and LNAI (+189%)

### BIAF — bioAffinity Technologies ($2.73 → $3.80, +39%)

| Field | Value |
|-------|-------|
| Price | $2.73 |
| Gap | 43% |
| RVOL | High |
| Dollar volume | $380M |
| Blocked by | D112 pump pattern instant-reject |
| Reason logged | "pump pattern: 43% gap on $2.73 stock, no catalyst" |

**Why this was wrong to reject:** BIAF had $380M dollar volume — massive institutional-level liquidity. The pump pattern filter blanket-rejected ALL sub-$3 stocks with >30% gaps, regardless of volume or legitimacy. This is exactly the explosive sub-$3 mover pattern we want to capture.

**D116 fix:** Route pump patterns to FULL_PIPELINE instead of INSTANT_REJECT. Let the manipulation_classifier and risk_agent evaluate whether the move is legitimate.

### LNAI — LumiNai ($0.66 → $1.91, +189%)

| Field | Value |
|-------|-------|
| Price | $0.66 |
| Gap | Massive |
| RVOL | 63.6x |
| Dollar volume | $3.6M |
| Blocked by | (1) EMC price filter ($0.66 < $3.00 min), (2) Dollar vol filter ($3.6M < $10M) |

**Why this was wrong to reject:** 63.6x RVOL is extraordinary — it signals genuine market-wide attention. The $10M dollar volume threshold was too high for sub-$3 stocks that trade at low absolute prices but massive relative volume.

**D116 fixes:**
1. Lowered dollar volume threshold from $10M to $2M (LNAI's $3.6M now passes)
2. Added extreme-RVOL override: RVOL > 30x bypasses dollar volume check entirely
3. Both paths would now let LNAI through to evaluation

**Estimated missed profit (LNAI):**
- Entry ~$0.66, exit at conservative $1.00 (52% gain), 3.2% allocation
- ~$4,462 position → ~$2,320 profit
- At day high $1.91: ~$8,459 profit (189%)

---

## D102 Experiment Replay Analysis

Experiments only ran for NVTS (the sole BUY). 23 variants tested, 20 would have entered:

### ATR Stop Multiplier Sweep
| Variant | MFCS | Would Enter? | Notes |
|---------|------|-------------|-------|
| atr_1.5 | 0.212 | YES | Tighter stop → same MFCS (stop doesn't affect MFCS) |
| atr_2.0 | 0.212 | YES | **Current production** |
| atr_2.5 | 0.212 | YES | |
| atr_3.0 | 0.212 | YES | Wider stop → same MFCS but would have survived longer |

**Key insight**: With ATR multiplier 3.0, the stop would have been ~$7.34 instead of $8.43. NVTS dropped to ~$10.03 before recovering to $10.83. **All ATR variants would have been stopped out** since the broker-side stop triggered around $10.03 regardless of our internal stop level. This suggests the Alpaca stop was tighter than our ATR-based stop — need to investigate whether the OTO stop order was set correctly.

### MFCS Buy Threshold Sweep
| Variant | Would Enter? | Notes |
|---------|-------------|-------|
| thresh_0.15 | YES | **Current production** (MFCS 0.212 > 0.15) |
| thresh_0.20 | YES | MFCS 0.212 > 0.20 — barely |
| thresh_0.25 | NO | MFCS 0.212 < 0.25 |
| thresh_0.30 | NO | |
| thresh_0.35 | NO | |

**Key insight**: Raising threshold to 0.25 would have avoided this losing trade. But with only 1 BUY in 130 evals, we're already too selective. The problem isn't the threshold — it's the broken technical_agent starving the consensus gate.

### Weight Variants
| Variant | MFCS | Would Enter? |
|---------|------|-------------|
| news_heavy (0.45) | 0.198 | YES |
| tech_heavy (0.40) | 0.214 | YES |
| balanced (0.30/0.30) | 0.197 | YES |

All variants would enter — MFCS is similar across weight schemes because only 2 agents contributed non-zero signals.

### Lambda (Risk Aversion) Sweep
| Variant | MFCS | Would Enter? |
|---------|------|-------------|
| lambda_0.05 | 0.220 | YES |
| lambda_0.10 | 0.217 | YES |
| lambda_0.15 | 0.215 | YES |
| lambda_0.20 | 0.212 | YES — **Current production** |
| lambda_0.30 | 0.207 | YES |

Higher lambda (more risk-averse) slightly reduces MFCS but not enough to change the verdict.

### Deflation Factor Sweep
All 6 variants (0.4 to 0.8) would enter. MFCS=0.212 across all. Deflation affects confidence but not the MFCS calculation for this trade.

### Experiment Conclusions
- **No parameter variant would have prevented this loss** — the issue was entry timing, not parameters
- **Threshold 0.25+ would have prevented the trade** but at the cost of even fewer entries
- **ATR investigation needed**: Why did broker stop trigger at $10.03 when our stop was $8.43?

---

## Systems That Worked Well

1. **D112 Adaptive Router** — Correctly instant-rejected 4/6 candidates with weak RVOL. BMNR (+0.3%), AAL (+4.2%), OPEN (+6%), ETHA (+10.8%) were all rejected. Only ETHA had a meaningful move but it's an ETF, not a gap-and-go setup.

2. **D101 VIX sizing** — VIX=26.1 halved all position sizes. Our loss was $248 instead of ~$496. On a bad day, this saved real money.

3. **D115 Kelly tier (shadow mode)** — Correctly classified NVTS as Tier 1 STANDARD. Logged reasoning clearly: "failed: mfcs=0.212<0.6, directional=2<3, catalyst=PRODUCT_LAUNCH not in proven list". Data collection working.

4. **D92 LLM fallback chain** — Primary Qwen3-235B timed out 81 times. MiniMax-M2.5 rescued 29 of those (36% recovery rate). Without fallback, we would have had even fewer agent signals.

5. **D112 pump pattern detection** — ~~BIAF flagged as pump pattern — initially thought correct.~~ **WRONG.** BIAF ran +39% with $380M dollar volume. The blanket pump-pattern reject was too aggressive. Fixed in D116: pump patterns now route to full pipeline for agent evaluation.

6. **D96 EMC price filter** — Rejected sub-$3 stocks including WNW, HCWB, BMNU. ~~LNAI correctly rejected.~~ **WRONG on LNAI.** LNAI ran +189% with 63.6x RVOL — extreme relative volume signaling genuine attention. Fixed in D116: extreme-RVOL override (>30x) + lower dollar vol threshold ($10M → $2M).

7. **D101 consensus gate** — Blocked CTMX due to BUG-001. Initially thought wrong — **actually correct.** CTMX is down 19.26% the next day. The gate accidentally protected us from a trap.

---

## Bugs Found

### BUG-001: technical_agent float(None) crash [P0 — 42 errors]
- **Error**: `float() argument must be a string or a real number, not 'NoneType'`
- **Impact**: **Technical agent dead ALL DAY.** Every evaluation ran with only 3/6 agents. This directly caused the CTMX miss (consensus gate couldn't be satisfied with only 1 directional agent).
- **Root cause**: LLM returns JSON with `null` values. `dict.get(key, default)` returns `None` when key exists with null value (not the default). `float(None)` raises TypeError.
- **Location**: `src/agents/technical_agent.py:108,111`
- **Same pattern in**: fundamental_agent, risk_agent, institutional_agent, deep_search_agent, news_agent, debate_engine
- **Fix**: Use `float(raw.get("confidence") or 0.0)` or replicate `_safe_float()` from `deterministic_technical.py`

### BUG-002: VWAP scan NoneType comparison [P0 — 233 errors]
- **Error**: `'>' not supported between instances of 'NoneType' and 'int'`
- **Impact**: Phase 3 VWAP rescan failed every cycle for 4+ hours. No new VWAP-based entries possible.
- **Root cause**: `get_vwap()` returns `None` when WebSocket disconnected. `if vwap > 0:` crashes.
- **Location**: `src/core/orchestrator.py:1470`
- **Fix**: `if vwap is not None and vwap > 0:`

### BUG-003: Dashboard "Open" counter stale [P2 — cosmetic]
- Dashboard showed `Open: 1` after NVTS stopped out, while Phase 3 correctly showed `monitoring 0 positions`
- **Root cause**: `open_positions` gauge not decremented on all position removal paths
- **Fix**: Audit all `remove_position()` call sites for gauge update

### BUG-004: D98 broker polling error [P2 — 1 occurrence]
- **Error**: `fromisoformat: argument must be str`
- Single occurrence after stop-out. None timestamp from broker response.
- **Fix**: Add type guard before `fromisoformat()` call

### BUG-005: WebSocket not connected [P1 — systemic]
- `NVTS D106 VWAP gate SKIPPED — VWAP unavailable (WebSocket not connected)`
- VWAP confirmation gate never fires. All entries proceed without VWAP check.
- **Fix**: Investigate WebSocket connection lifecycle

### BUG-006: Experiment replay log spam [P3 — noise]
- 20+ MFCS log lines per candidate per cycle from D102 replay
- By design but noisy. Consider reducing verbosity.

### BUG-007: SEC EDGAR 500 errors [P3 — 2 occurrences]
- Intermittent SEC API failures for NVDA, AMD. Already handled gracefully.

---

## Market Context

| Indicator | Value | Impact |
|-----------|-------|--------|
| VIX (est.) | 26.1 | Elevated — all sizing halved |
| VIX delta | +4.8% (VIXY $31.17→$32.68) | Rising fear |
| SPY | -0.16% ($678.27→$677.18) | Slight weakness |
| Macro | Oil up on Iran tensions, partial govt shutdown | Risk-off environment |

**Overall market character**: Risk-off day with elevated volatility. Not ideal for gap-and-go momentum plays. The few stocks that ran (CTMX on clinical data, LNAI on delisting speculation) were catalyst-specific, not broad momentum.

---

## Priority Fixes for Tonight

| Priority | Bug | Impact | Effort | Status |
|----------|-----|--------|--------|--------|
| **P0** | BUG-001: float(None) across agents | Technical agent dead all day | Medium (7 files, 15 instances) | ✅ FIXED |
| **P0** | BUG-002: VWAP None guard | 233 errors/day, Phase 3 broken | Low (1 line) | ✅ FIXED |
| **P0** | D116: Pump pattern blanket reject | BIAF +39% missed | Low | ✅ FIXED — routes to FULL_PIPELINE |
| **P0** | D116: Dollar vol threshold too high | LNAI +189% missed | Low | ✅ FIXED — $10M → $2M + extreme-RVOL override |
| **P1** | BUG-005: WebSocket connection | VWAP gate non-functional | Medium | ✅ FIXED — market data WS wired in main.py, launched after Phase 1 |
| **P2** | BUG-003: Dashboard gauge | Cosmetic | Low | ✅ FIXED — gauge sync centralized in remove_position() |
| **P2** | BUG-004: fromisoformat guard | 1 error | Low | ✅ FIXED — isinstance check handles both str and datetime |

---

## Questions for Review

1. **ATR stop vs Alpaca stop**: Our internal stop was $8.43 but the position exited at ~$10.03. Did the Alpaca OTO stop order have a different stop price? Need to check order details.
2. **Should we lower RVOL threshold?** ETHA (1.2x RVOL) had a 10.8% day. But it's an ETF — is that our edge?
3. **Consensus gate at 2 agents**: With technical_agent fixed, 3 agents can provide directional signals (news, technical, risk). Is 2/3 the right threshold, or should we consider 2/4+ when more agents are online?
4. **WebSocket priority**: Is VWAP confirmation worth the infrastructure? Or should we make it optional and not gate entries?
