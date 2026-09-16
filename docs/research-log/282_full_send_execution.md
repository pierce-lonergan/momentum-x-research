# 282 — FULL SEND: executing the doc-281 play (the bleed-cut package, the stop forensic verdict, the instruments relit)

**Author**: Claude (Fable 5) | **Date**: 2026-07-04 | **Class**: LIVE-CONFIG + FLAG-GATED CODE (operator-authorized: "DO whatever you need to make this happen. Full send") | **Scope**: doc 281's Stages 1–3, executed and verified; every change one-line-revertible; no close-path code touched (the pre-registered gate said no).

> Doc 281 concluded there is no offense to ship and prescribed a three-stage defense-first play. Pierce authorized execution. This doc records what was DONE — with the one honest gate-fail reported at full strength: the vanished-stop forensic came in at **$5,990 < the pre-registered $8K gate**, so the stop-path code fix does **not** ship, even under full send. The discipline is the product; the gate held against the strongest temptation this program generates — an operator saying "do whatever it takes."

## §1 — Stage 1: the bleed-cut package (LIVE for Monday 2026-07-06)

Applied to the operator env (backup: `momentum-x-secrets.env.bak-doc281`), verified loading through the real `Settings` machinery and the T2 module:

| Change | Value | Basis | Restore rule (pre-registered) |
|---|---|---|---|
| `EXEC_RISK_PER_TRADE_PCT` | **0.0067** (was 1% default) | doc 281 §4 size package | posture scoreboard durably positive, n≥30 |
| `EXEC_MAX_POSITION_PCT` | **0.05** (was 0.15) | bounds the PLSM 14.8%-of-equity class 3× | same |
| `MOMENTUM_T2_WIDE_PCT` | **0.75** (was unset = 50/50) | the doc-266 prereg promotion rule FIRED (wide −1.10% vs tight −2.75% OOS, n=36/42) | demotion at n≥60/arm if winsor mean AND median flip |
| `MOMENTUM_REENTRY_BAN_DAYS` | **10** (~7 sessions) | §3 below | unset to disable |
| `FADER_SHORT_DRY_RUN` | **1** | doc-262 rec #2 executed: 0-for-94 borrow attempts; runner becomes a pure avoidance-signal logger | delete line |

Verified: `risk_per_trade_pct=0.0067`, `max_position_pct=0.05`, `_wide_bucket_threshold→75`, `halt_new_entries=False` (entries stay ON), elite press already OFF (Pierce's earlier call — it sized 2× into the worst bucket).

## §2 — Stage 2a: the vanished-stop forensic — GATE FAIL, reported straight

`scripts/_doc282_stop_forensic.py` parsed 56 OTO-filled entries across 28 session logs. **The class is real**: 35 affected entries (13 submitted stop ≥ fill — the mechanical bug: a %-stop priced off the *intended* entry lands above a favorably-slipped *actual* fill, the broker kills the stop leg, the position runs naked; 31 D231-blocked; 12 caught by emergency stops). Root cause confirmed on PLSM (D101 sized $29.8K = 15%-cap-bound; OTO stop $11.15 vs fill $11.14; D313 "unhedged" 5s later).

**But the class-wide excess loss = $5,990 — below the pre-registered $8K gate** (92% of it is two trades: PLSM +$3,141, RTB +$2,242; most affected trades exited fine anyway). **Per GATE A: no close-path code ships.** Mitigations that DO stand: the 5% position cap (cuts any future naked position 3×) and the already-chipped hedge-underwater fix (task_76fc858a, same 422 family). Artifact: `data/research/doc282_stop_class.json`.

## §3 — Stage 2b: the loser-re-entry ban (BUILT, TESTED, LIVE)

Doc-281's counterfactual re-verified independently before shipping: under a stricter rule, **5 re-entry trades, −$6,027, zero winners forgone** (investigator's rule: 8 trades, −$9,948, zero forgone). Both cuts agree: re-entering a ticker that just lost is pure bleed on this book.

Shipped as `_check_reentry_ban()` in `src/data/alpaca_client.py`, called at the single entry chokepoint (`submit_oto_order`, immediately after the D277 halt check; long entries only — exits/stops/ratchets/shorts never gated). Env-gated per call (`MOMENTUM_REENTRY_BAN_DAYS`, unset/0/invalid = OFF), fail-open on any read error (never blocks trading on its own bug). **8/8 new regression tests** (`tests/unit/test_reentry_ban.py`) + 255 related tests green + live behavioral check (PPCB, which lost 7/3, correctly banned; fresh ticker passes).

## §4 — Stage 3: the instruments relit

- **The red-flag collector is fixed and backfilling.** Root cause of the month-dark crash: nullable dtypes leak `pd.NA` through the candidacy flags → `int(NAType)` TypeError (since 6/8). Fixed with NA-hardening at the flag source + a loud drop of unpriceable rows (`rocket_watchlist_engine_doc255.py`). Verified on the first crashed date (6/09: 27 gappers logged); dark-month backfill (6/09→7/02) running.
- **The posture-delta scoreboard is wired into the nightly** (`post_close_scorecard.py`, best-effort block): the restore-gate for the size cut now accumulates automatically — sizing comes back when the machine, not vibes, says the regime turned.
- **FaderShort submissions retired** (env), keeping the logger alive as the avoidance-signal instrument.

## §5 — What Monday looks like, honestly

The bot trades at ⅓ size with a 5% position cap, 75% wide-stop arm, no re-buys of recent losers, entries otherwise unchanged; the three orphans flatten at the open (MOO orders resting); the collectors and scoreboard accumulate. **Expected: materially smaller red, not green** — doc 281's honest range is a 40–70% bleed cut (≈ −$13K→−$26K per 60 sessions vs −$43K→−$71K status quo). Green requires one of the pre-registered answer-changers: the regime turning (the scoreboard will say), a broker with locates (the 5/5-week short signal waits), or a genuinely new edge surviving the gauntlet. Nothing was bent to pretend otherwise.

**Rollback (one line each)**: restore `momentum-x-secrets.env.bak-doc281`, or individually revert the five env lines; the re-entry ban dies with `MOMENTUM_REENTRY_BAN_DAYS` unset; the code changes are additive and flag-gated.

## §6 — Post-gauntlet addendum: the full-suite triage (all 49 remaining failures pre-existing)

The full 3,970-test suite returned 50 failures. Complete triage: **one** was tonight's by-design behavior change (the T2 50/50 distribution test now that the live split is 75/25 — an env-isolation bug in the test, fixed with `monkeypatch.delenv`, commit `2024252`). **The other 49 are pre-existing debt in three classes**, each verified: (a) **env leakage** — tests asserting default config while `Settings` reads the operator `.env` (fail under the baseline env too, e.g. the 11 bankroll-sizing tests); (b) **stale mocks** — D192-era MagicMocks that D212 code outgrew (`faller_detection.py:595`, commit `cdaf0b8`); (c) **stale source-scanners** — tests grepping `main.py` for the Bug-AS stop-oid mirror, which the 2026-04-29 AT-family refactor (`3fb9f9a`) moved into `src/execution/post_fill_handler.py:243-245`, **where the protection is verified intact**. No live regression; nothing from tonight; cleanup chipped (`task_b24afcae`). Enumeration artifact: `data/research/doc282_suite_failures.txt`.
