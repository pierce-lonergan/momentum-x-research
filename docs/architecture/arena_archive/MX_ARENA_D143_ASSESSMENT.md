# mx-arena D143 Assessment: 8.5/10 — Finding Quality Improving

## Finding Validity

Finding 6 (spread filter +130%): Second-best finding. Methodology sound (pre-entry
expected-value filtering). Caveats: per-tier MFE estimates from small samples (10-15
trades per tier), spread model not empirically calibrated, 51% filter rate means
opportunity cost of false rejections.

Finding 7 (time exits lose by 38%): Most valuable NEGATIVE finding. Prevented a
bad production change. Price targets exit at the spike (bar 1-2). Time exits sell
during the post-spike pullback. Bar-2 exit should be tested as it's still within
the spike window.

## Six Innovations Identified

1. MFE-conditional tranche targets (per price-tier/rvol-bucket adaptive T1/T2/T3)
2. Spread filter with CI-lower-bound bypass (reduce false rejections)
3. Entry quality score = expected_MFE - spread_cost (unified ranking)
4. Bar-2 exit test (confirm price targets still win at spike window)
5. Spread-adjusted Phase 0 stop (0.8% + spread_cost, prevent false stop-outs)
6. Per-regime tranche/stop configuration (different MFE on vol days)

## The Connected Model

The arena now has a coherent theory of gap-up momentum profitability:
- Stocks move most in bar 1
- 51% of trades are negative-expectancy after spread costs
- $3-$10 is the sweet spot, RVOL 3-10x is optimal
- Tight tranches capture +33% more, price targets beat time exits
- Pipeline inversion captures more alpha than sequential evaluation

Each finding constrains the next. The innovations above unify them into
a single entry quality function: enter, size, set tranches, and set stops
all from one number derived from the data.
