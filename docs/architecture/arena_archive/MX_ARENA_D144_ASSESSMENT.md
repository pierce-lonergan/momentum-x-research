# mx-arena D144 Assessment: 8.5/10 — Most Important Analytical Session

## Key Scores

Finding quality: 9.0/10 (capture decomposition is genuine breakthrough)
Walk-forward discipline: 9.0/10 (every variant tested before deployment)
Self-honesty: 9.5/10 ("the remainder was the problem all along")
Overall: 8.5/10 (unchanged — five structural gaps remain)

## The Decomposition Finding (9.5/10 validity)

Bar-1 captures +22% of MFE. Remainder captures -88%. 73/86 remainder
exits are stops. Price targets fire on 9% of remainder positions.

This retroactively explains every exit problem. The "46% capture" was
a blend that obscured bimodal reality: brilliant spike capture on sold
portion, catastrophic loss on held portion.

## What +240% Actually Means

Original +$1.28, current +$4.35. Average +$0.055/trade = +$5.50 on
$500 position. Not retirement money. Transformed breakeven into
marginally profitable. Thin edge at frequency. Top 5 = 85% of P&L.

## The 1.8x Overfit Warning

50% bar-1 had 1.3x overfit. 80% bar-1 has 1.8x. Approaching 2.0
danger zone. The higher fraction is more profitable but less robust.
Different train/test split might cross 2.0. Classic performance vs
robustness tradeoff.

## Innovations Identified

1. Conditional bar-1 fraction (50% on big spikes, 90% on weak bar-1)
2. Trailing stop activation delay (3-bar delay for pullback completion)
3. Entry limit below ask (0.3-0.5% for pullback entry capture)
4. Position sizing from expected bar-1 return (bigger on $3-$5 stocks)
