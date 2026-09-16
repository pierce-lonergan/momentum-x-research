# 268 — Night ops: the silent-verdict killer NAMED (D200/D204 data-absence complex) + the Verdict Lifecycle Ledger

**Author**: Claude (Fable 5 era) | **Date**: 2026-06-09 ~22:00 → pre-deploy | **Mandate**: Pierce's doc-268 night-ops protocol. HEAD deploys 04:30 Wed.

## P0 RESULT — the 6/8 killer is named, with file:line and log evidence
On 6/8, 14 of 19 BUY verdicts died with no order_id (7 = the day's rockets). On 6/9 it fired again: 10 of 12 silent. Forensics (gate inventory by Explore agent, verified against runtime logs):
- **Killer #1 — D200-E4 CATALYST GATE (`main.py:3453`)**: blocks BUY verdicts when `catalyst_type=NONE`. **105 block-lines on 6/8** naming BGMS, BNAI, DEVS, ELOG, RMSG, SMTK, SUGP, VVOS — straight off the silent-14. On 6/9 it ate **BYAH** (gap 41%, RVOL 2582×, float 2.6M, **ranked #1 in the momentum order at score 1120**) one log-line after its BUY verdict.
- **Killer #2 — D204 NEWS GATE (`main.py:6676` VWAP / `:6975` RESCAN)**: blocks when no BULL news ≥0.30 conf. Ate ELPW, PAVS, GLE, CGTL, CPOP on 6/9 (12 block-lines).
- **#3 — D56 already-held (`main.py:3306`)**: the carried ABAT/OCC/TNGX rows — correct behavior, noisy journal.
- **The mechanism behind both killers = DATA ABSENCE, not bearish signal**: no-coverage micro-caps have no news (BYAH) and the SEC EDGAR fetch **500'd live in the log**. Same absence zeroes `catalyst_news×0.55` in MFCS (BYAH scored 0.289 with 55% of its score structurally zero). This is the doc-177 "blind funnel," still the system's #1 verdict-eater — **and it is anti-selective against the lottery rockets, which are by nature the no-news names** (doc 253). The cap-8 hypothesis is REFUTED as the primary (no cap-block lines either day); the D170-observation hypothesis is REFUTED (zero DEFERRED lines).
- **Policy note (NOT changed tonight)**: whether D200/D204 *should* block on absence is a strategy decision touching closed research (selection layer). Reported with evidence; The operator decides. The journal row is never stamped with the rejection (gates fire after `record_verdict`, before any fill stamp) — that observability hole is what tonight fixes.

## SHIPPED — Verdict Lifecycle Ledger (VLL), risk class DORMANT-C
`src/ops/verdict_ledger.py`: `vll_emit(stage, ticker, reason, **ctx)` → appends structured events to `data/ops/verdict_trace_<ET-date>.jsonl`. **Never raises** (proven in smoke: swallowed a non-serializable arg), pure logging, zero behavior change. Emits wired at the proven drop points + the success terminal:
- `BLOCKED_CATALYST_GATE` @ main.py D200-E4 block (with catalyst_type, mfcs, path=MAIN)
- `BLOCKED_NEWS_GATE` @ main.py D204 VWAP + RESCAN blocks
- `SUBMITTED` @ `bridge.py` execute_verdict success path (order_id, fill_price, qty)
From tomorrow's open, every silent-class death self-names. Invariant check (journal BUYs vs trace terminals + broker fills) = grader extension, chipped for tomorrow (trace file is grep-able meanwhile).
**Gauntlet**: compileall OK; emitter smoke OK; **34/34 close-path suite green** (bridge touched); risk class DORMANT-C (never-raises wrappers at all 4 sites); rollback = `git revert <this sha>`.

## DEFERRED WITH EVIDENCE (context-bounded night; per §10 a clean small ship wins)
- **Phase 2 (D164/D165/OCC phantom siblings)** — LIVE-A close-path surgery; NOT attempted at night without the full gauntlet budget. All file:lines + the proven D163 pattern are staged in `data/research/doc268_night_state.md` §stack-2. First daylight item after the open settles.
- **Phase 3 (test debt)** — both chips already filed (task_5aff8d1f MagicMock ×10, task_5fe2c077 default-drift ×7).
- **Phase 4 sweeps / Phase 5 builds** — not reached; the state file holds the taxonomy + plan.

## MORNING CHECKLIST (Pierce)
1. **The 3 flip lines (doc 266)** — now better-informed: cap-8 was NOT the verdict-eater, so `EXEC_MAX_POSITIONS=16` is breadth-structure only (still replay-justified); the **T2 tilt 0.75** stands on its own evidence. **New decision surfaced tonight**: D200/D204 block on data-absence and ate BYAH/NPT-class rockets two days running — if you want those tickets, the gates need an absence-vs-bearish distinction (e.g., fail-open at reduced size when news is EMPTY rather than BEAR). That is a strategy call; the VLL will count exactly what each choice costs from tomorrow.
2. Isolation keys (doc 263) — Lottery stays $5k-capped until done.
3. Watch at the open: `data/ops/verdict_trace_2026-06-10.jsonl` populating; 19:30 grader; zero recon deltas (6/9 had ghosts=0 — D163 fix confirmed live, second clean day pending).

**Deploy statement**: the ONLY live-behavior-adjacent change tonight is VLL logging (never-raises, DORMANT-C). No config, no flags, no gate behavior touched. Not one number bent.
