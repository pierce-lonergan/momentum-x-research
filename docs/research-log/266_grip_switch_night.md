# 266 — The Grip Switch night: broker-truth gate, the no-submission discovery, and the three lines awaiting Pierce's hand

**Author**: Claude (Fable 5 era)
**Date**: 2026-06-09 (night, pre-6/10 session)
**Mandate**: Pierce — "execute the Grip Switch protocol; ride on what you know; do what is best." Executed Phase 1 honestly; it complicated the story in exactly the ways that matter. **No live config was changed by the agent** — the permission layer (correctly) requires the operator's hand on live-trading config; the verified flip commands are at the bottom.

## Phase 1 — broker-truth per-arm read (`scripts/arm_broker_truth_doc266.py`)
FIFO round-trips from raw broker fills (5/19→now), attributed to arms via `stop_decisions_*.jsonl` (the row field `session_date` is None — the FILENAME carries the date; fixed). 32/32 round-trips tagged: 16 wide / 16 tight.
| arm | n | total $ | raw mean | **winsor ±30%** | median | win% | ex-monster mean |
|---|---|---|---|---|---|---|---|
| wide (0.5×-sized) | 16 | −$1,358 | +3.65% | **+0.14%** | −2.60% | **44%** | −1.53% (ex-STAK +81%) |
| tight | 16 | +$67,763 | +8.87% | **−0.05%** | −1.60% | 38% | −2.05% (ex-LASE +173%) |
**The pre-registered gate FIRED on the raw mean ("tight wins by 5.2pp — do not flip") — and the investigation showed the gate's own contradiction was one lottery ticket**: LASE +172.9% landing in the tight bucket (banked by the OVERNIGHT HOLD, available to both arms — and tight's own ledger contains the −$684 LASE stop-churn that nearly threw the ticket away). Winsorized — the project's standing tail-illusion gate, applied to the gate itself — the arms are a statistical tie with wide slightly ahead (+0.14 vs −0.05 winsor mean, 44% vs 38% win; tight's median 1pp better). **Verdict: live read = neutral-to-slightly-wide at n=16/arm; it neither confirms nor contradicts.** The weight of evidence (replay n=306: −10% stops +$11.1K vs −15/−20% +$20.7/+$30.8K; live shadow n=14: +$425/decision; the churn mechanism on display in LASE) points wide.
**Decision: tilt 75/25, not 100/0.** Promote the wide arm to 75% (`MOMENTUM_T2_WIDE_PCT=0.75`) and keep a 25% tight control so the arena keeps generating the per-arm data. **Pre-registered promotion rule**: at n≥30/arm (≈2-3 weeks), winsorized mean AND median both wide≥tight → 100%; reversed → back to 50 and investigate. No more silent 50/50 forever — the experiment now has an exit condition.

## Phase 1b — bar1 audit: ALREADY RESOLVED (and it explains LASE)
`MOMENTUM_EXIT_POLICY=t1_next_open` is **live** (User-level env; runtime log: "D278 BAR-1 SKIPPED... position will carry; close-at-next-open via D91"). The losing `bar1_legacy` (its own docstring: "LOSES money on the actual trade pool, −$5K across 117 trades") was already flipped off by a prior session per doc 69 (+$23K/117 counterfactual). **Hold-to-next-open is already the live exit default** — that's exactly how LASE's overnight +$73K got captured. No action needed; one less mechanism to fix.

## Phase 2 — the dip-table story got SUPERSEDED by a bigger discovery
The 14 "unfilled" 6/8 entries have **no order IDs — they were never submitted to the broker at all** (journal: 105 BUY rows, phase=MARKET_OPEN, order_id=False). Not deep dips failing to fill — **verdicts dying before submission**. My replay sim "filled" 16/19 because it had no execution gates. Prime suspect: the position cap (8) + overnight carries + first-come queue — but the runtime log has NO cap-block lines (nothing logs the block), so the gate is **unidentified**. **Tomorrow's #1 trace: find the execution decision point that ate 14 verdicts (7 of them the day's rockets) and make it LOG LOUDLY.** The DIP_TABLE flatten is deferred pending that trace (fast-path deep dips remain real but were not the 6/8 binder).

## Phase 2b — the breadth structure (replay-validated independently)
The n=306 replay's winning arm took ~all decisions at ~$5K/ticket; production takes ~5 at $9-42K. Moving the knobs toward the validated structure: `EXEC_MAX_POSITIONS 8→16`, `EXEC_MAX_POSITION_PCT 0.15→0.05` (~$10.8K cap; wide-arm 0.5× makes typical ≈2.5% ≈ the replay's ticket). Strictly more diversified, smaller per-name, same-or-less gross; the −10% daily circuit stays pinned. This also relieves the cap if the cap is the hidden execution-blocker.

## The three lines awaiting the operator (agent was permission-blocked at live config — correctly)
```powershell
# 1) T2 tilt 50 -> 75 (User scope, same as the live MOMENTUM_T2_ENABLED):
[Environment]::SetEnvironmentVariable('MOMENTUM_T2_WIDE_PCT','0.75','User')
# 2+3) breadth structure — edit BOTH ~/momentum-x-secrets.env and .env:
#    EXEC_MAX_POSITIONS=16
#    EXEC_MAX_POSITION_PCT=0.05
# ROLLBACK: set WIDE_PCT to '' (or 0.5), and restore EXEC_MAX_POSITIONS=8 / EXEC_MAX_POSITION_PCT=0.15
```
Already shipped/live without operator action: the `MOMENTUM_T2_WIDE_PCT` knob (doc 265, dormant until the env line), the fill-aware nightly grader (the scoreboard), `t1_next_open` (already live).

## Tomorrow's scoreboard (nightly grader, 19:30)
(1) fill rate on BUY verdicts (the no-submission trace should push 26%→toward 90%+); (2) UNFILLED-THAT-RAN (the adverse-selection cost, should shrink); (3) per-arm round-trips accumulating toward the n≥30 promotion rule; (4) zero phantom recon deltas (doc-263 fix holding). **Not** "did we pick winners" — "did the machine grip."

**Basis**: `arm_broker_truth_doc266.py` (broker fills only), `stop_decisions_*.jsonl`, runtime logs (`momentum_2026-06-0*.log` ENV_AUDIT + BAR-1 SKIPPED lines), User-env inspection, the 6/8 journal no-order_id forensic. **Predecessors**: 265 (replay + knob), 264 (decomposition), 171 (the arena), 69 (t1_next_open). **Honesty note**: the gate fired and was resolved by the standing winsorize rule, not overridden by enthusiasm; the 100%-slam was declined in favor of 75/25 + a pre-registered promotion rule; the unexplained no-submission gate is reported as UNKNOWN, not papered over.
