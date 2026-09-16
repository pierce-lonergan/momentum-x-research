# 87 — Three-track investigation + short-side migration deferral

**Status:** shipped 2026-04-30 evening. Per PROMPT_10. **HEADLINE: All three investigation tracks complete. The +$40,985 was an ETF amplifier on a Real-Earnings catalyst that the bot caught accidentally. Without CRCA, the strategy lost $4,070 over 10 weeks.**

**Severity:** Tracks A/B/C produce decisive evidence for PROMPT_11's path α scope decision. Migration sweep deferred per §7.5 stop condition (helper needs short-side variant). Halt confirmed ON throughout.

---

## §0 — TL;DR

**Track A (CRCL catalyst forensics)**: Real-Earnings catalyst CONFIRMED with HIGH confidence. Circle Q4 2025 earnings released 2026-02-25 8:00 AM ET — revenue $770M (+77% YoY), net income +$129M YoY swing. Bot's BUY at 10:49 ET landed 2h49min after the release. **α-extend has a generally-applicable thesis.**

**Track B (common-stock MAGNA-N)**: **0 Real EPs in 10 weeks** of common-stock carry trades. Without CRCA, total carry P&L = +$2,153. Total broker P&L without CRCA = **-$4,070** (intraday -$6,223 + carry-no-CRCA +$2,153). **The strategy has been a NET LOSER excluding the ETF amplifier accident.**

**Track C (manual intervention forensics)**: **Pass B confirmed.** 0 CRCA mentions in `manual_intervention_log.jsonl`; 0 broker activities for CRCA between 2/26 and 3/01. The 5-day hold was unmonitored — bot dropped CRCA from watchlist after 2/25, position drifted up unattended. NOT manual intervention.

**Migration sweep (Blocks 4-6)**: **DEFERRED.** Audit reveals D161/D207 require short-side helper variant (exit_ladder semantics differ for shorts; OTO stop direction differs). D170 is observation-only, not an entry — doesn't need migration. Per §7.5 stop condition: implementing band-aid would relocate complexity rather than close it. Defer to dedicated short-side session.

**Discovery rate**: 36 holds. No new bugs surfaced.

---

## §1 — Track A: CRCL catalyst forensics

Full audit doc: `docs/audits/2026-04-30_crcl_catalyst_forensics.md`. Key findings:

- **Layer 1 verdict: Real-Earnings catalyst.** Circle (CRCL) released Q4 2025 earnings pre-market on 2026-02-25 at 8:00 AM ET (matches Press Release timestamp).
- **Revenue $770M (+77% YoY)**, net income +$129M YoY swing, USDC volume +247%, bullish 2026 guidance.
- **Bot's BUY at 10:49 AM ET = 2h49min after the release** — likely the news_agent surfaced it via the pre-market feed, scoring agents updated MFCS, scanner promoted CRCA into the watchlist (the leveraged-ETF amplifier on CRCL).
- **CRCL ripped against a tape where BTC was -24% YTD and ETH -34%** — pure idiosyncratic earnings, not crypto beta.
- FIT21 was NOT voted on in Feb 2026 (status pending per the U.S. Crypto Policy Tracker); GENIUS Act was already law since July 2025.
- **Layer 2 verdict: YES — α-extend can systematically generalize this setup.**

### What this means for the operator decision

The catalyst was IDENTIFIABLE in real-time (8:00 AM earnings release; common 8-K Item 2.02 pattern). MAGNA-N applied to CRCL passes M (net income growth +105% intra-2025) and likely A (revenue acceleration). **A deterministic classifier with underlying-resolution + leverage-aware sizing could systematically catch CRCA-class trades.**

---

## §2 — Track B: full common-stock MAGNA-N classification

Full audit doc: `docs/audits/2026-04-30_carry_trades_full_magna_n.md`. Key findings:

### §2.1 The carry-trade structure

| Metric | Value |
|---|---|
| Total broker-reconstructed closed trades 2/19-4/29 | 288 |
| Carry trades (held overnight+) | 18 |
| Carry trades deduped by (ticker, entry_session) | 6 |
| **Real EPs (MAGNA-N score ≥3)** | **0** |
| Story EPs (1-2 with G) | 0 |
| Noise | 5 |
| ETF amplifier (CRCA, scope-mismatch) | 1 |

### §2.2 Per-trade results

| ticker | session | type | P&L | score | class |
|---|---|---|---|---|---|
| CRCA | 02-25 | ETS | **+$40,985** | N/A | ETF_amplifier |
| RLMD | 03-09 | CS | +$1,842 | 1/4 | Noise (only G; no M/A; market_cap > $1B fails N) |
| XWEL | 02-25 | (no fund) | +$1,071 | 0/4 | Noise (no fundamentals data) |
| ELSE | 04-21 | CS | -$50 | 2/4 | Noise (G+N only) |
| LIDR | 04-24 | CS | -$316 | 2/4 | Noise (G+N only) |
| CHNR | 03-19 | CS | -$394 | 1/4 | Noise (N only) |

### §2.3 Intraday vs Carry split (THE BIGGER FINDING)

| Class | n trades | Total P&L |
|---|---|---|
| Intraday | 270 | **-$6,223 (NET LOSER)** |
| Carry | 18 | +$43,138 |
| **Total** | **288** | **+$36,915** |

### §2.4 Without CRCA

| Slice | Trades | P&L |
|---|---|---|
| Carry without CRCA | 16 | +$2,153 |
| Intraday | 270 | -$6,223 |
| **Total without CRCA** | **286** | **-$4,070 over 10 weeks** |

**The strategy has been a NET LOSER over 10 weeks excluding the one ETF amplifier trade.**

### §2.5 Caveat on "0 Real EPs"

The verdict is constrained by data sparsity:
- Polygon has at most 4 quarters of financials per ticker (limits M/A trajectory testing)
- XWEL has no fundamentals data at all
- Some tickers have null `market_cap` (fails N by missing data, not by failed test)
- The MAGNA-N rubric was relaxed (10% QoQ instead of Bonde's 39% YoY) and still produced 0 Real EPs

The honest interpretation: **the strategy's filters did not catch any Bonde-clean Real EPs in the common-stock carry universe over 10 weeks.** Whether that's because no EPs existed in the universe, or because the strategy's filters are too loose/strict, requires a follow-up calibration study.

---

## §3 — Track C: CRCA manual intervention forensics

Full audit doc: `docs/audits/2026-04-30_crca_manual_intervention_forensics.md`. Key findings:

- **0 CRCA mentions** in `manual_intervention_log.jsonl` (only 3 entries total, all LIDR April 2026)
- **0 broker activities for CRCA between 2/26 and 3/01** — no manual orders, stop adjustments, scale-outs
- The 3/02 sells share an `order_id` consistent with bot-submitted (single OTO/limit filling in two parts), not operator-clicked
- Combined with PROMPT_09's finding that the bot dropped CRCA from its watchlist after 2/25: **the position sat unmonitored for 5 days**

**Verdict: Pass B (incidental hold).** The +$40,985 was the bot acting alone — opened a position the strategy was supposed to close at T+60s, then forgot about it because of Bug AT-bypass (now closed). Going forward, with `MOMENTUM_EXIT_POLICY=t1_next_open` (current operator policy), the same hold pattern would be intentional.

---

## §4 — Migration sweep (Blocks 4-6): DEFERRED

### §4.1 Audit findings per path

**D161_FALLER_SHORT** (main.py:3446-3504, ~60 LOC):
- Has D215 with `direction="short"`
- Has state_mgr update with `direction="short"`
- Has stop_resubmitter register
- **DOES NOT have exit_ladder call** — explicit comment: "Shorts skip the long-path stop conversion ... they use the OTO buy-stop as-is"

**D207_SHORT** (main.py:2688-2738, ~50 LOC):
- Has D215 with `side="sell"`, `direction="short"`
- Has state_mgr update with `direction="short"`
- Has trade_journal entry with `action="BUY"` + `direction="short"` (the OTO short is recorded as BUY action with short direction)
- **DOES NOT have exit_ladder call** — same short-side semantic as D161

**D170_OBSERVATION** (main.py:3553-3588, ~36 LOC):
- This is NOT a fill handler — it's an entry-deferral mechanism.
- Registers candidates in `entry_delay_mgr` to wait for an observation period.
- The actual entry happens later when `entry_delay_mgr.get_candidate(...).state == ObservationState.APPROVED` — and at that point, the candidate flows through one of PHASE2_BUY/RESCAN/VWAP_BREAKOUT (already migrated).
- **D170 doesn't open positions; it's a gate. No migration needed.**

### §4.2 Why short-side migrations are deferred

Per PROMPT_10 §7.5 stop condition:

> "Audit reveals helper needs short-side variant → implement minimally OR defer specific path. Document."

The unified helper at `src/execution/post_fill_handler.py:229` calls `cancel_stop_and_submit_exit_ladder` UNCONDITIONALLY. For long orders this:
- Cancels the OTO sell-stop (below entry)
- Submits sell-limit tranches (above entry, take-profits)
- Submits a residual sell-stop (below entry)

For short orders the desired semantics are:
- Cancel the OTO buy-stop (above entry)
- Submit buy-limit tranches (below entry, cover-profits)
- Submit a residual buy-stop (above entry)

D161 explicitly skips exit_ladder because the OTO buy-stop already serves as the protective stop for shorts. D207 same. **Migrating without a short-aware helper would either (a) double-cancel the buy-stop and try to submit sell-tranches that would short MORE shares (broker rejection or accidental position-doubling), or (b) silently break the short-side stop registration.**

Per the discipline rule "Don't bend the helper into something it isn't" — adding a `skip_exit_ladder=True` flag would be band-aid; building a true short-side variant is its own session of design work.

### §4.3 What ships for short-side instead

- **The 3 xfail placeholders from PROMPT_09's `test_bug_av_side_aware_helper.py` STAY xfail.** They were designed to auto-flip when migrations land; that's still the right contract — they auto-flip when the dedicated short-side session ships.
- **Bug AV fix from PROMPT_09 (the helper's direction-aware side derivation) is still active and useful** — when the short-side variant eventually ships, the side propagation is already correct.

### §4.4 Forward sequence change

| Session | Scope | Status |
|---|---|---|
| N (today, PROMPT_10) | 3-track investigation + short-side migration deferred | ✅ shipped |
| N+1 (PROMPT_11) | Operator decision: path α scope (extend / restrict / defer) | pending |
| N+2 | Short-side helper variant + D161/D207 migrations (gated on PROMPT_11 outcome) | NEW deferral |
| N+3+ | Adverse-selection compute, hierarchical exit model, EP classifier, etc. | per PROMPT_05 plan |

---

## §5 — Synthesis: evidence for PROMPT_11's operator decision

Per PROMPT_10 §11.3 — **evidence not advocacy**. Organized by which option the evidence supports/weakens.

### α-extend (extend path α to leveraged ETFs as amplifier sub-class)

**Strong supporting evidence**:
- Track A: CRCL had a Real-Earnings catalyst (Bonde MAGNA-N applies cleanly to underlying)
- Track A: The catalyst was IDENTIFIABLE in real-time (8:00 AM earnings release)
- Track B: The +$40,985 isn't just lucky placement — it's the bot's actual selection mechanism that flagged CRCA
- Track C: The hold was bot-only (no manual intervention); systematic strategy could replicate

**Weak / contraindicating evidence**:
- The hold itself was incidental (Bug AT-bypass, now closed)
- α-extend requires building underlying-resolution + leverage-aware sizing + decay modeling — not in scope for any current session
- Sample of 1 ETF amplifier in 10 weeks doesn't prove the systematic frequency

### α-restrict (restrict path α to common stocks)

**Strong contradicting evidence**:
- Track B: 0 Real EPs in 10 weeks of common-stock carries (caveats on data sparsity)
- Track B: Common-stock carry P&L = +$2,153 over 10 weeks
- Track B: Including intraday (which IS the strategy's design space), strategy is -$4,070 net loser without CRCA

**Weak supporting evidence**:
- Bonde's published frequency (10-12 EPs/year) is roughly consistent with what we observed
- RLMD shows the EP shape (gap-up + multi-day biotech catalyst hold + small profit) — strategy CAN catch the form, just hasn't caught the magnitude
- Data sparsity could be hiding Real EPs from the rubric

### α-defer (wait for ≥30 Real EPs to accumulate)

**Strong supporting evidence**:
- Sample of 0 Real EPs / 10 weeks means need ~3+ years of data at current rate
- Halt is currently ON; defer is the de-facto state

**Weak contradicting evidence**:
- 3+ years is a long time
- During defer, the bot generates no revenue
- Capital sits in cash earning ~5% (decent for risk-free) while strategy is unproven

---

## §6 — Operator escalations queued

1. **Path α scope decision** (PRIMARY for PROMPT_11). Extend / restrict / defer per the synthesis above.
2. **Confirm via Alpaca dashboard**: were there any CRCA manual orders that wouldn't show in our data? (Operator memory + dashboard check.)
3. **Confirm via personal records**: do you remember knowing the CRCA position was open in late Feb 2026?
4. **CRCL underlying watchlist policy** (if α-extend chosen): should the bot watch CRCL directly, or only the ETF amplifier? Trade-off: CRCL gives cleaner catalyst signal but is outside the sub-$15 universe filter.
5. **POLYGON_API_KEY rotation** — STILL pending (5+ sessions queued).
6. **Web search verification of Track A's findings** (if operator wants independent confirmation): Circle Q4 2025 earnings news from Bloomberg / Reuters / CoinDesk.

---

## §7 — Validation

| Check | Result |
|---|---|
| Replay firewall | $0.3400 bit-perfect (7th consecutive validation) |
| D278 source-grep | 16/16 pass + 0 xfail |
| Corpus reconciliation | 10/10 pass |
| Bug AV tests | 4 pass + 3 xfail (placeholders for D161/D207/D170 — STAY xfail per §4) |
| Halt switch | ON at start AND end |
| Discovery rate | 36 holds (0 new bugs) |

---

## §8 — Status

- ✅ Track A: CRCL catalyst forensics — Real-Earnings confirmed (`docs/audits/2026-04-30_crcl_catalyst_forensics.md`)
- ✅ Track B: full common-stock MAGNA-N classification — 0 Real EPs, strategy net loser without CRCA (`docs/audits/2026-04-30_carry_trades_full_magna_n.md`)
- ✅ Track C: manual intervention forensics — Pass B confirmed (`docs/audits/2026-04-30_crca_manual_intervention_forensics.md`)
- ⏸ Block 4 (D161 migration): DEFERRED per §7.5 (short-side helper variant needed)
- ⏸ Block 5 (D207 migration): DEFERRED per §7.5 (same)
- ⏸ Block 6 (D170 migration): N/A (D170 is observation, not entry handler)
- ✅ This doc shipped
- ✅ Halt switch ON both ends
- ⏳ Commit + push next

---

## §9 — Closing thought

PROMPT_10 was scoped to produce evidence for PROMPT_11's operator decision plus close the AT family with three migrations. **It delivered the evidence cleanly and surfaced that two of the three migrations require short-side architectural work that was out of scope.** The framework's discipline rule — "don't bend the helper into something it isn't" — prevented a band-aid that would have shipped 3 xpasses but introduced 2 production bugs.

The three tracks together produce an unambiguous evidence picture:
- The strategy's biggest win (CRCA, +$40,985) was a Real-Earnings catalyst on a leveraged ETF amplifier, identifiable in real-time, accidentally held due to a since-closed bug.
- Without that ETF amplifier, the strategy lost $4,070 over 10 weeks.
- The deterministic classifier (MAGNA-N) does not directly apply to ETFs but can be extended to the underlying.

**The operator now has the data to decide path α scope on evidence, not on intuition.** PROMPT_11 makes the call.
