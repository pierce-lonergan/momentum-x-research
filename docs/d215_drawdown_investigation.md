# D215: Drawdown Investigation — What's Actually Going On

**Date:** 2026-04-08
**Trigger:** Live Kelly recompute showed PF=0.85 on recent 14 trades

## Finding 1: VARIANCE, not drift

Bootstrap test: P(PF<=0.85 in 14 trades from PF=1.03 pool) = 23.4%.
This is well above the 15% threshold for "plausible variance." The recent
losing streak is within the normal range for a thin-edge system.

## Finding 2: These are NOT bar-1 trades

Hold times range from 0 to 7,122 minutes. Median: 222 minutes (early),
152 minutes (recent). The system is making multi-hour swing trades using
the full exit intelligence suite (velocity, pullback, gratitude, alpha decay),
NOT the 60-second bar-1 exits from D214.

The bar-1 PF=1.08 finding applies to the ARENA simulation, not to
production. Production has never run bar-1 exits.

## Finding 3: The edge is jackpot-amplified

| Subset | n | PF |
|--------|---|-----|
| All trades | 34 | 6.84 |
| Without CRCA (+1183%) | 33 | ~1.5 |
| Without top 5 (>30%) | 29 | 1.03 |
| Trimmed (±50% cap) | 32 | 1.03 |
| Recent 14 | 14 | 0.85 |

The base edge (PF=1.03 trimmed) is real but tiny. Occasional jackpot
winners (+42%, +38%, +95%) amplify it. Without jackpots, the system
barely breaks even.

## Finding 4: March 4 was a disaster from re-entries

ASNS entered 3 times, CANF entered 2 times on the same day. Losses:
-26%, -11%, -28% (ASNS) and -33%, -41% (CANF). The D94b stopped-out
guard wasn't functioning properly. These 5 trades account for -139%
cumulative P&L, explaining most of the recent drawdown.

## Finding 5: CRCA (+1183%) is a black swan

CRCA was held for 119 hours (5 trading days). This is an overnight swing
that caught a massive move. It's not replicable by the system's design
(which targets intraday gap-up momentum). Remove it and the system goes
from "incredibly profitable" to "marginally positive."

## Sizing Verdict

Current 2.0% Tier 1 is appropriate. The live edge is too uncertain
(CI includes zero) to justify increasing. Do not decrease either —
the variance test says this drawdown is normal.

## What This Means for D215

The window stacker arena test is still valid but must be contextualized:
1. Production doesn't run bar-1, so "augmenting bar-1" means CHANGING
   the exit strategy, not augmenting what's running
2. The real question is whether bar-1 (T+1m exit) would produce better
   live PF than the current multi-hour holds
3. The current strategy's edge depends on catching occasional large winners
   during extended holds — bar-1 would sacrifice these
4. The investigation suggests entry selection (WHICH stocks) matters more
   than exit timing (WHEN to exit)

## Action Items

1. Collect 30+ more live trades with proper fill tracking
2. Build the window stacker arena test BUT compare against the ACTUAL
   production exit strategy (multi-hour holds), not just bar-1
3. Focus D215 on entry quality (FinBERT + catalyst classification)
   rather than exit optimization — the data says entries are the lever
