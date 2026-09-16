# MOMENTUM-X Paper Trading Log

> Running log of daily paper trading observations, configuration/architecture
> decisions, and the reasoning behind them. This is NOT a bug tracker — it
> documents what we learn from live market behavior and how we adapt.

---

## 2026-03-16 (Day 1) — First Live Session

### Market Conditions
- VIX: Normal range
- Broad market: Mixed — crypto/mining sector strong

### Watchlist (Phase 1 Pre-Market)
| Ticker | Gap | RVOL | Price | Sector |
|--------|-----|------|-------|--------|
| MARA | 12.7% | 2.0x | $9.79 | Crypto mining |
| BMNR | 8.5% | 2.5x | $22.39 | Crypto mining |
| NIO | 8.0% | 1.0x | $5.99 | EV/Auto |
| INTC | 5.2% | 0.8x | $47.93 | Semiconductor |

### System Decisions
| Ticker | Router (D112) | Evaluation | Final Verdict | Reason |
|--------|--------------|------------|---------------|--------|
| MARA | PASS | Full pipeline | NO_TRADE | D101 consensus: 0 directional agents (no catalyst found) |
| BMNR | PASS | Full pipeline | NO_TRADE | D101 consensus: 0 directional agents (crypto holdings = "NONE" catalyst) |
| NIO | REJECT | Skipped | NO_TRADE | D112 Router: RVOL 1.0x < 1.5x minimum |
| INTC | REJECT | Skipped | NO_TRADE | D112 Router: RVOL 0.8x < 1.5x minimum |

### Actual Outcomes (EOD)
| Ticker | Gap | Intraday Move | Outcome |
|--------|-----|--------------|---------|
| BMNR | +8.5% | **+12% from prev close** | **MISSED — should have traded** |
| NIO | +8.0% | Up (modest) | Correct pass — low RVOL, no catalyst |
| INTC | +5.2% | Up (modest) | Correct pass — low RVOL, large cap |
| MARA | +12.7% | Flat/down from open | Correct pass — no catalyst, gap fade |

### Key Learnings

**1. News agent catalyst types were too narrow (FIXED → D114)**

The news agent only recognized FDA/M&A/earnings-level catalysts. BMNR had real
news (crypto holdings disclosure + "Why Is BitMine Stock Soaring Monday?") but it
was classified as `catalyst_type="NONE"` because "corporate update" wasn't in the
enum. The hardcoded rule `NONE → force NEUTRAL` then killed the signal entirely.

**Root cause**: The system prompt was calibrated for "+20% explosive moves" — the
wrong bar for gap-and-go day trading where ANY identifiable news explaining the
gap is meaningful.

**Fix (D114)**:
- Added `CORPORATE_UPDATE` and `SECTOR_CATALYST` catalyst types
- Updated prompt from "+20% movers" to "any identifiable news explaining the gap"
- Added 0.65 confidence cap for weaker catalysts (user chose aggressive tier)
- Preserved `NONE → NEUTRAL` for stocks with truly no news

**Expected impact**: BMNR-type setups (real news, strong RVOL, doesn't fit narrow
catalyst categories) will now reach debate stage instead of being instantly zeroed.

**2. D112 Router RVOL threshold (1.5x) performed correctly**

NIO (1.0x RVOL) and INTC (0.8x RVOL) were correctly rejected. Both had modest
intraday moves — not the explosive gap-and-go behavior we target. The 1.5x RVOL
floor saved compute without missing profitable setups.

**Decision**: Keep `ROUTER_INSTANT_REJECT_MIN_RVOL=1.5` — validated today.

**3. MARA gap faded — no-catalyst rejection was correct**

MARA gapped +12.7% with 2.0x RVOL but had zero news. The system correctly
identified no catalyst and passed. MARA faded from its gap, confirming that
high gap + volume without a catalyst is a trap.

**Decision**: The D101 consensus gate (2+ directional agents required) is working
as intended for truly catalyst-less moves.

### Incidents

**Heartbeat timeout crash (05:30 ET) — FIXED → D113**

The system started at ~04:30 ET, scanned correctly through pre-market, but the
heartbeat watchdog had zero pulse calls during Phase 1. After 3600s the watchdog
triggered shutdown before market open.

**Root cause**: `pulse()` was only called inside Phase 3 (line 2348 in main.py).
Phase 1 and Phase 2 had no pulse calls. Before D112, Phase 2 evaluations took
minutes (LLM calls), so the system always reached Phase 3 in time. D112's
instant-reject speed exposed the missing pulses.

**Fix (D113)**: Added pulse calls to main loop top, Phase 1 end, and Phase 2
verdict summary. System restarted at 08:18 ET and ran stable through close.

### Configuration State
```
ROUTER_ENABLED=true (D112 — validated, keeping)
ROUTER_INSTANT_REJECT_MIN_RVOL=1.5 (validated today)
ROUTER_PUMP_GAP_THRESHOLD=0.30
ROUTER_PUMP_PRICE_THRESHOLD=3.00
min_directional_agents=2 (D101 — validated today)
News agent: D114 broader catalyst types (deploying tomorrow)
```

### Open Questions for Tomorrow
1. Will D114's broader catalyst types produce false positives? Monitor for
   stocks with weak corporate updates getting undeserved BULL signals
2. Should we track "gap fade" rate by catalyst type? MARA (no catalyst) faded,
   BMNR (corporate update) held — this pattern may be systematic
3. Consider adding a "SOCIAL_MEDIA" catalyst type if we see stocks moving on
   Reddit/Twitter momentum without traditional news

---

*Next entry: 2026-03-17*
