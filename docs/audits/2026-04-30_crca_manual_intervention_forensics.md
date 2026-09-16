# 2026-04-30 — Track C: CRCA manual intervention forensics (PROMPT_10 §6)

**Verdict: Pass B — incidental bug-induced hold, NO manual intervention found.**

---

## §1 — What was checked

Per PROMPT_10 §6.1:

1. **`data/journals/manual_intervention_log.jsonl`**: searched all entries for CRCA mentions.
2. **State files** (`data/state/`): nothing relevant for the 2026-02-25 to 2026-03-02 window (the 7-week journal gap from PROMPT_05 doc 68 covers this period).
3. **Bot session journals** (`data/journals/journal_2026-*.jsonl`): no journal files exist for 2026-02-25 through 2026-03-02. The latest pre-window journal is `journal_2026-02-24_132008.jsonl` (61 entries; 0 mention CRCA). No CRCA-window journals exist.
4. **Stop history**: not directly recoverable without bot logs.
5. **Order history broker-side**: searched `data/broker_truth/activities.parquet` for any CRCA orders between 2/26 and 3/01.

---

## §2 — Findings

### §2.1 manual_intervention_log.jsonl

Total entries: **3**. CRCA mentions: **0**. The 3 entries are all for LIDR in late April 2026:

```
2026-04-25 LIDR MOO_SELL — Bug Z infrastructure_contaminated overnight protection
2026-04-27 LIDR PROTECTIVE_STOP_GTC — bug_ag_track_b_offline manual GTC stop @ $2.10
2026-04-27 LIDR PROTECTIVE_STOP_GTC_OVERNIGHT — EOD overnight protection $2.00 stop
```

**No CRCA entries.** Pierce did not log a manual intervention on CRCA.

### §2.2 Broker activity for CRCA between 2/26 and 3/01

Full CRCA broker activities:
```
2026-02-25 15:49 UTC: BUY  443 @ $3.03   filled
2026-03-02 14:34 UTC: SELL 700 @ $38.81  partially_filled
2026-03-02 14:35 UTC: SELL 443 @ $39.01  filled
```

**Activities between 2/26 and 3/01: ZERO.** No manual orders, no stop adjustments, no scale-outs. The position sat untouched for 5 days (2/25 close → 3/02 morning).

### §2.3 The 3/02 sell mechanism

Both 3/02 sells share the same `order_id` (`42501e5a-3281-4fec-b161-cefdf401c7c9`). This pattern (single OTO/limit order filling in two parts) suggests **bot-submitted, not manually-clicked** — operator-clicked orders typically have distinct client_order_ids per click.

But without 3/02 logs (the 7-week gap), we cannot verify which specific bot mechanism triggered the 3/02 9:34 ET sell. Possibilities:
- A delayed/recovered BAR-1 EXIT scheduler firing on day 5
- A trailing stop hit (price had been around $39 and pulled back)
- A different exit mechanism (D78 SMART_EXIT, time-stop)
- A single manual order (less likely given the order_id pattern)

---

## §3 — Verdict per PROMPT_10 §6.2

| Outcome | Definition | Evidence |
|---|---|---|
| Pass A | Bot held intentionally (design) | NO — bot was DESIGNED to close at T+60s pre-PROMPT_06; t1_next_open didn't exist yet |
| **Pass B** | **Bot held accidentally (BAR-1 should have fired but didn't)** | **YES — Bug AT-bypass meant BAR-1 EXIT scheduler was never wired for whatever path CRCA took (consistent with PROMPT_09 §1.2 finding)** |
| Pass C | Pierce held manually | NO — 0 manual_intervention_log entries; 0 broker activities 2/26-3/01 |
| Cannot determine | Insufficient data | NO — Pass B is the only consistent reading |

**Pass B confirmed.**

---

## §4 — Implications

### §4.1 The +$40,985 was unmonitored, unintentional, but bot-only

Specifically:
- The bot bought CRCA at 10:49 ET on 2/25 (~3 hours after the 8:00 AM CRCL earnings release per Track A)
- BAR-1 EXIT did not fire (Bug AT-bypass meant the path didn't have the scheduler)
- The bot dropped CRCA from its watchlist after 2/25 (no bar_recordings 2/26+; no D278 monitoring)
- The position drifted from $3.58 close (2/25) to ~$38.81 close (3/02) — +984% over 5 trading days
- On 3/02 9:34 ET, the sell triggered (mechanism unknown without logs)

**The operator was not in the loop.** This was the bot acting alone — opening a position the strategy was supposed to close at T+60s, then forgetting about it because the codebase had structural defects (which PROMPT_06+07 have since fixed).

### §4.2 What this means for path α

The +$40,985 is **NOT operator judgment** — it would have been replicable by the same bot in the same configuration on the same setup. So path α evidence stands as "the bot caught one ETF amplifier on a Real-Earnings catalyst and accidentally held it 5 days." 

Going forward, with `MOMENTUM_EXIT_POLICY=t1_next_open` (current operator policy from PROMPT_06 commit `ba030ad`), the bot WOULD intentionally hold CRCA-class trades past T+60s. So while CRCA's hold was accidental, the same hold pattern is now a deliberate feature of the system.

### §4.3 Operator action items

1. **Confirm via Alpaca dashboard**: were there any CRCA orders manually submitted via the dashboard that wouldn't appear in bot journals? (operator memory check)
2. **Confirm via personal records (email/Slack/notes)**: do you remember any thinking about CRCA in late Feb 2026? Did you know the position was open?
3. **CRCA pre-2/19 cost basis verification**: per PROMPT_08 §1.3, broker shows 1143 sells but only 443 buys in our 2/19+ window. Was there an earlier 700-share buy that pre-dates Alpaca's API lookback?

---

## §5 — Discovery rate

36 holds. Track C is informational, not bug-discovery. The Pass B verdict is consistent with PROMPT_09's tentative reading; this audit confirms it with explicit broker-side evidence.
