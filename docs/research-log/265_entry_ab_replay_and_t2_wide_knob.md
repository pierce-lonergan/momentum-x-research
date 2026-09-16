# 265 — Entry A/B replay: marketable REFUTED; the real winner is decision-price entries + wide stops — and the system already built half of it

**Author**: Claude (Fable 5 era)
**Date**: 2026-06-09 (late evening)
**Mandate**: Pierce — "all-in on rockets, protect the downside, money uphill." Stated plainly first: **rocket-picking stays closed** (docs 245-254; the live rocket-shadow is 0-for-4, top picks −22.6% mean). Nobody sees through that noise; the uphill door is mechanical — stop DE-selecting the tickets the system already finds (doc 264). This doc runs the decisive entry test, and the discipline kills my own doc-264 lever on schedule.

## 1. The replay (`scripts/entry_ab_replay_doc265.py`)
Every journal BUY decision May 1 → Jun 9 (328 deduped, 26 sessions), replayed on real REST minute bars (ET from raw UTC ms — never warehouse ts_et). Arm A = limit at the journal decision price, zero slip (generous incumbent). Arm B = marketable at decision +50bps. Both: flat 15:55 −30bps. Pre-registered rule for B: beat A on total $ AND majority of days AND survive winsorize ±30%.

| @$5k/ticket | n | mean | median | win% | winsor mean | TOTAL |
|---|---|---|---|---|---|---|
| A decision-price limit | 306 | +1.98% | −1.23% | 48% | −0.45% | **+$30,274** |
| B marketable +50bps | 326 | +0.81% | −2.14% | 42% | −1.34% | +$13,130 |
| B on A-missed only | 20 | +10.21% | +7.83% | 70% | +7.73% | +$10,208 |

**B REFUTED on every clause** (leads 5/26 days). Paying 50bps on all 326 names to catch 20 runners loses to just placing the limit AT the decision price. *The discipline killed my own doc-264 "go marketable" lever within 24 hours — working as designed.*

## 2. The calibration miss IS the finding
Sim-A filled **16/19 on 6/8; the broker actually filled 5/19.** My "Arm A" was NOT production — production's dip-limits sit **2-15% below** (fast-path `DIP_TABLE`) and filled 26%, adversely selecting the 5 faders and missing all 7 runners (doc 264). A limit **at the decision price** fills **94%**, misses only 20/326 (the +10.2%-mean NPT class — $10K left, not everything), and still beats marketable. **The leak is dip DEPTH, not passivity.** (Main-path entries already went marketable-crossing in doc 189/190 — "a perfect PICK we can't FILL earns $0" — the deep-dip leak lives in the fast-path/other flows; daylight audit to enumerate.)

## 3. The stop panel — third independent confirmation
Total $ on the same 306 fills: no stop **+$30.3K**, −20% **+$30.8K**, −15% **+$20.7K**, **−10% +$11.1K**. Tight stops destroy half to two-thirds of the P&L; −15/−20% disaster stops keep ~70-100%. Concordant with the D308/D309 live shadow (n=14: ATR +$425/decision) and the LASE specimen (stopped −$684 at second 31 of a position that paid +$73K on re-entry).

## 4. The system already built the fix — and it's running 50/50 RIGHT NOW
- The binding tight stop is **D139 phase1: 1.5% for the first 7 minutes** (`alpaca_executor.py:422-429`), then ATR. On names that move 10%/minute, that's the churn engine.
- **Doc 171 (May 24) already built the cure**: the T2 `wide_stop` arm — "skips the Phase 1 override entirely... so the bot doesn't shake out of momentum runs at 1.5% noise" — with halved qty to bound risk. **The A/B is LIVE**: 6/8+6/9 stop submissions = 4 phase1 vs 5 wide_arm_standalone. The system has been randomizing on exactly our question for ~2.5 weeks.
- **Shipped tonight**: `MOMENTUM_T2_WIDE_PCT` env knob in `t2_arm_assignment.py` (per-call read; fraction or percent; unset/invalid = exact original 50/50; verified: 1.0→all-wide, 0→all-tight, deterministic; doc-171 test passes). The module's own comment anticipated this ("future iterations can tilt this... once the data confirms").
- **NOT flipped tonight, per the rule**: the live arena's per-arm P&L needs broker-truth attribution (journal P&L is sparse + partially phantom-poisoned, doc 263); the quick join returned no matches. No flip without the live read.

## 5. Morning checklist (in order, before/at open)
1. **Broker-truth per-arm read**: join `stop_decisions_*.jsonl` arms (42 tagged: 15 wide/27 tight since 5/20) to Alpaca fills per (symbol, date). If wide leads — as the shadow (n=14), the replay panel (n=306), and the LASE mechanism all say — **flip `MOMENTUM_T2_WIDE_PCT=1.0`** in secrets (one line, instantly revertible).
2. **Dip-table flatten** (fast-path): `DIP_TABLE` → ~decision-price entries behind an env flag, validated through `simulate_day.py` first.
3. **bar1_exit audit** (D145 "sell 100% at T+60s" default-True but zero 6/8 journal traces — confirm dormant or kill).
4. D164/D165 partial-exit phantom siblings + OCC journal re-stamp (doc 263 carry-over).
5. Grader v2 nightly now reports FILLED vs SUBMITTED-UNFILLED + UNFILLED-THAT-RAN — the daily scoreboard for all of the above.

## 6. Sizing truth ("all-in on rockets")
All-in on one lottery name = the ASNS/CANF −$6K days at 15× size = ruin. The replay's own arithmetic: ~13 tickets/day × $5-7K, worst day −$10.5K (−4.9% of account) with NO stops — survivable and it kept every tail. The structure is the barbell: **many small tickets × wide stops × hold to close × disaster-stop + the −10% daily circuit (already pinned)**. Maximum tail exposure per dollar of ruin-risk — that's the honest "all-in."

**Basis**: 328 replayed decisions (26 sessions, REST minute bars, cached `_entry_ab_minute_ckpt.jsonl`), `data/shadow_stops/` (live shadow + arena tags), `alpaca_executor.py` stop-path trace, `t2_arm_assignment.py`. **Predecessors**: 264 (the adverse-selection finding + account decomposition), 171 (T2 arena), 169 (stop shadow), 189/190 (marketable main paths). **No live behavior changed tonight**; one dormant knob shipped + the flip is one env line after the morning read.
