# 2026-04-30 — Track B: full common-stock MAGNA-N classification (PROMPT_10 §5)

**Status:** complete. **Headline: ZERO Real EPs in 10 weeks of common-stock carry trades.** Without CRCA (the ETF amplifier), total carry P&L = +$2,153, and total broker P&L = -$4,070 (NET LOSER over 10 weeks excluding the ETF win).

---

## §1 — Methodology

`scripts/classify_carry_trades_magna_n.py`. For all carry trades in `data/broker_truth/closed_trades.parquet` (is_carry=True, 2/19-4/29):

1. Dedupe by (symbol, entry_session_date) — collapses FIFO splits
2. Look up Polygon `data/polygon_backfill/fundamentals/{ticker}.json`
3. If type ∈ {ETF, ETS, ETV} → mark as ETF_amplifier (Bonde N/A)
4. Else apply MAGNA-N (relaxed proxies due to data sparsity):
   - **M**: net_income growth ≥100% (oldest → latest of available 4 quarters)
   - **A**: revenue acceleration ≥10% QoQ for two consecutive quarters (relaxed from Bonde's 39% YoY since we only have 4 quarters of data)
   - **G**: gap-up ≥10% on entry day (via bar_recordings)
   - **N**: market_cap ≤ $1B (project's neglect proxy; analyst count + institutional ownership not in Polygon dump)
5. Score 0/4 to 4/4. Tag: Real_EP (≥3), Story_EP (1-2 with G), Noise (otherwise)

---

## §2 — Results: 6 unique carry trades (deduped from 18 broker fills)

| ticker | session | type | n_legs | qty | P&L | hold (h) | M | A | G | N | score | class |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **CRCA** | 02-25 | **ETS** | 2 | 1143 | **+$40,985.14** | 118.8 | N/A | N/A | N/A | N/A | -1 | **ETF_amplifier** |
| RLMD | 03-09 | CS | 1 | 2302 | +$1,841.60 | 47.2 | ✗ | ✗ | ✓ | ✗ | 1 | Noise |
| XWEL | 02-25 | (no fund) | 2 | 3076 | +$1,071.06 | 114.4 | ✗ | ✗ | ✗ | ✗ | 0 | Noise |
| ELSE | 04-21 | CS | 1 | 2478 | -$49.56 | 23.7 | ✗ | ✗ | ✓ | ✓ | 2 | Noise |
| LIDR | 04-24 | CS | 10 | 5264 | -$315.84 | 95.7 | ✗ | ✗ | ✓ | ✓ | 2 | Noise |
| CHNR | 03-19 | CS | 2 | 896 | -$394.24 | 23.3 | ✗ | ✗ | ✗ | ✓ | 1 | Noise |

**0 Real EPs. 0 Story EPs. 5 Noise. 1 ETF_amplifier (CRCA).**

### §2.1 Why no Real EPs?

Per PROMPT_10 §5.5 stop conditions, this triggers escalation. Caveats on the "0 Real EPs" verdict:

1. **Data sparsity**: Polygon has at most 4 quarters of financials per ticker. M filter requires growth across the available 4 quarters; A filter requires 3 consecutive quarters of revenue. Tickers with sparse data fail M+A by lack of evidence, not by failed test.

2. **XWEL** has NO fundamentals at all in our data dump. Tagged Noise by absence of evidence.

3. **G filter** depends on bar_recordings for both day-of and prior trading day. LIDR (4-day average hold = recurring entry) and ELSE both have G=true via bar_recordings. CRCA also had G=true on entry day.

4. **N filter** is the project's "market_cap ≤ $1B" proxy. RLMD has market_cap > $1B (biotech, presumably $5B+ given its trade prices), so N fails — Bonde's "neglect" criterion typically wants <$1B. RLMD passes G (gap-up) but fails N (too big) and M+A (insufficient profitability data).

5. **The relaxed MAGNA-N rubric (10% QoQ instead of 39% YoY)** is already a generous interpretation. With strict Bonde criteria the count would be the same: 0 Real EPs.

**True reading: the strategy did not catch any Bonde-clean Real EPs in 10 weeks of common-stock carries.** The 1 plausibly-EP-shaped trade (RLMD biotech with gap-up + 47-hour hold + small profit) doesn't satisfy MAGNA-N's other filters.

---

## §3 — Intraday vs Carry split (THE BIGGER FINDING)

| Class | n trades | Total P&L | Avg per trade |
|---|---|---|---|
| Intraday | 270 | **-$6,223** | -$23 |
| Carry | 18 | +$43,138 | +$2,397 |
| **Total** | **288** | **+$36,915** | +$128 |

**Intraday strategy is a NET LOSER over 10 weeks** (-$6,223 / 270 trades). The +40% headline account number is entirely driven by carry trades, and **95% of carry P&L is CRCA (the ETF amplifier)**.

### §3.1 What survives if we exclude CRCA

| Slice | Trades | P&L |
|---|---|---|
| Carry without CRCA | 16 | +$2,153 |
| Intraday | 270 | -$6,223 |
| **Total without CRCA** | **286** | **-$4,070** |

**The strategy lost $4K over 10 weeks excluding the one ETF amplifier trade.**

---

## §4 — Top 10 most profitable individual trades

| Symbol | Entry | Exit | Qty | Entry $ | Exit $ | P&L | Carry? |
|---|---|---|---|---|---|---|---|
| CRCA | 02-25 | 03-02 | 700 | 3.03 | 38.81 | **+$25,046** | YES |
| CRCA | 02-25 | 03-02 | 443 | 3.03 | 39.01 | **+$15,939** | YES |
| MOBX | 03-03 | 03-03 | 12590 | 0.61 | 1.19 | +$7,308 | NO (intraday) |
| JZXN | 03-04 | 03-04 | 7066 | 1.21 | 1.60 | +$2,756 | NO |
| RLMD | 03-09 | 03-11 | 2302 | 5.82 | 6.62 | +$1,842 | YES |
| ANNA | 03-20 | 03-20 | 578 | 5.18 | 7.14 | +$1,133 | NO |
| AIFF | 03-04 | 03-04 | 2177 | 1.11 | 1.58 | +$1,023 | NO |
| AIFF | 03-04 | 03-04 | 1962 | 1.11 | 1.58 | +$922 | NO |
| AIFF | 03-04 | 03-04 | 1906 | 1.11 | 1.58 | +$896 | NO |
| KNRX | 02-19 | 02-19 | 1702 | 1.95 | 2.38 | +$732 | NO |

**Reading**: CRCA dominates the top 10. MOBX/JZXN/AIFF are intraday small-cap gap-ups (the strategy's design space) — they win individually but the cumulative intraday P&L is negative because the losses on the other 260+ intraday trades exceed these wins.

---

## §5 — Implications for PROMPT_11's operator decision

### α-extend (extend path α to leveraged ETFs as amplifier sub-class)
- **Strongest case from this evidence.** CRCA's +$40,985 is the only large win; α-extend would systematically catch similar setups by applying MAGNA-N to underlyings.
- Track A confirmed CRCL had a Real-Earnings catalyst (Q4 2025 release on 02-25 8:00 AM ET); α-extend's thesis is generally applicable.
- Implementation cost: classifier needs underlying-ticker resolution + leverage-aware sizing + decay modeling.

### α-restrict (restrict path α to common stocks)
- **Weakest case from this evidence.** Common-stock carry P&L = +$2,153 over 10 weeks. Intraday P&L = -$6,223. Total = -$4,070.
- 0 Real EPs in 10 weeks (caveats: data sparsity).
- The strategy as currently scoped has no evidence base for profitability outside the ETF amplifier accident.

### α-defer (wait for ≥30 Real EPs)
- At observed rate (0 Real EPs / 10 weeks), defer is effectively indefinite.
- During defer, what does the bot do? Halt? The current state (halted, no positions opened) IS effectively defer. So the question becomes: do we restart with reduced sizing on common-stock candidates, or stay halted?

### Recommendation framing (per PROMPT_10 §11.3 — evidence not advocacy)

The evidence leans toward α-extend OR fundamental-rethink. α-restrict requires accepting that the strategy hasn't worked on its intended scope. Operator's call is which trade-off matters more:
- **Bigger scope, more complex classifier (α-extend)**: higher upside if classifier works, but development cost and untested ETF-amplifier strategy class
- **Smaller scope, accept negative P&L (α-restrict)**: simpler, but the evidence base is "the strategy hasn't worked"
- **Stop and rebuild (α-defer)**: most conservative, preserves capital, longest path to revenue

---

## §6 — Stop condition check (PROMPT_10 §5.5)

| Condition | Result |
|---|---|
| Zero Real EPs across full window | **YES — triggered.** Documented above. Caveats noted. |
| Real EP frequency < 5 per year extrapolated | YES (0 actual; 0 extrapolated) |
| Multiple ETF amplifiers in carry universe | NO (only CRCA) |
| Real EP P&L per trade < 5% on average | N/A (no Real EPs) |

**The session continues per the prompt's intent — flagged for PROMPT_11.**
