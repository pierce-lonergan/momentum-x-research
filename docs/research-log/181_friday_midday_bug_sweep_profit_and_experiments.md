# 181 — Friday Midday: Bug Sweep, Profit-Lost, and the Experiment Gap (with a doc-180 correction)

**Author**: Claude Opus 4.8
**Date**: 2026-05-29 (Friday), snapshot ~10:50 ET (LIVE)
**Method**: 3 parallel forensic agents (bug sweep / profit-lost / experiment
assessment) + direct log verification.
**Mandate**: Pierce — "bug sweep. assess profit lost so far. understand why. and
if our current/shadow experiments need updating to better test bridging the gap."

---

## 0. Correction to doc 180

Doc 180 said "APPS 970-ghost cleaned at open (qty_drift=0)." **That was wrong.**
`qty_drift=0` means internal qty (970) == broker qty (970) — they *reconcile* —
**NOT that the position is flat.** APPS is a **LIVE 970-share position**: the D91
open-close 403-failed (the stop-cancel→market-sell race didn't settle in time, 3
retries exhausted), so APPS rode the gap to **+23.8% ($8.56, +$1,625 unrealized)
at 10:48**. It's the second straight session the bot makes its only "profit" on an
*accidental* overnight hold it tried and failed to close.

---

## 1. Profit lost so far today: ≈ $0 — and that's the gates working

**Realized P&L today = $0.00 (zero fills).** Equity $150,757 (+$208 vs 5/28
close) is almost entirely the APPS unrealized hold.

The names the bot saw, blocked, or couldn't fill **faded after open** — the gates
*saved* money, they didn't lose it:

| Ticker | Gap | What blocked it | Post-decision price action | Verdict |
|---|---|---|---|---|
| **CGTL** | 84.9% | faller (0.669>0.60, manip 90%, dolvol $641K) → then D170 | $0.51 → **$0.44** (−14%) | block CORRECT (faded) |
| **UMAC** | 55.5% | D170 drawdown | $29 → **$25.32** (−13.8%) | avoid CORRECT (faded) |
| **CODX** | 64% | faller / D170 | $11.81 vwap → **$10.62** (−11.3%) | avoid CORRECT (faded) |
| **NCT** | 31% | faller (0.960) | micro-cap, 159× RVOL exhaustion | avoid CORRECT (pump) |
| **CMND** | 45.7% | limit didn't fill | thin ($243K dolvol), STRONG_BEAR | avoid likely correct |

**Estimated profit actually lost: ~$0–600** (mostly ATPC, which the bot never
tried — low MFCS). The faller gate + D170 **avoided ~$1–3K of fades.** Today the
defensive layer is working as designed: it correctly refused to chase promotional
pumps that faded 11–14%.

**The uncomfortable flip side**: the bot's only P&L (+$1,625) is an *accidental*
unprotected hold, and the analysis below shows it likely **could not have entered
even a GOOD continuation squeeze** if one had appeared. The problem isn't that we
lost money today — it's that the machine can neither (a) capture upside nor (b)
protect the upside it accidentally has.

---

## 2. Bug sweep — findings (severity-ranked)

### H1 — APPS stop NOT synced to broker (live risk on a +24% position)
`D231 RECON_HARD_BLOCK STOP APPS: internal_stop=$8.46 broker_stop=$5.88 ... Bug R
regression — Phase-0/1 stop tightening may not have propagated to
ManagedPosition.stop_loss` (firing every ~30s; 95×). The D78/D89 software trail
ratcheted to $8.46 but the **broker stop order is still $5.88**.
- **Nuance**: APPS *is* software-protected at ~$8.46 (the D163 trail fires a market
  exit each Phase-3 cycle if breached). The $5.88 broker order is the hard backstop.
  The real exposure is a **between-cycle gap-down or a bot crash** — then only $5.88
  protects, risking ~$2,500 of the +$1,625 gain. Not "fully naked," but the hard
  backstop is 30% below where the bot thinks it is.
- This also drives the shadow **RECON_LETHAL ×82** ("WOULD flat-all + halt") — on a
  *real* divergence. If recon is ever un-shadowed with this condition, it halts.

### H2 — The running process is on OLD code (doc 178); my fixes are inert
`D217 STARTUP ... commit=b33b853d` + `D239 HEARTBEAT_DIRTY_WORKTREE: commit=b33b853d
... worktree dirty=True ahead=2`. The live process is **doc 178**. **doc 179 (M2
real-time data) and doc 180 (D249 re-arm fix) are on disk but NOT running.** Today's
D249 naked-window and the M2-absence (below) are *because the fixes aren't deployed*.
A **restart** loads them.

### H3 — D170 entry-delay gate approved ZERO entries; the dominant 0-fills cause
**20 DEFERRED, 0 APPROVED, 0 FILLED.** Every gate-passing gapper went to the D170
observation window and was rejected:
`D170: UMAC REJECTED ... drawdown_13.8pct_exceeds_10pct_limit`, same for CGTL/CODX/
RCAX. Root cause (`entry_delay.py`): (a) `max_drawdown_from_open=10%` — one >10% dip
= permanent reject (kills every volatile gapper); (b) `require_higher_lows=True` — a
fading-then-recovering name can't satisfy a ratcheting min; (c) early-entry needs
MFCS≥0.60 **AND ≥4 bullish agents** — structurally impossible under the blind funnel
(only 1–2 non-NEUTRAL agents), so UMAC at MFCS **0.856** still failed.
- **Double-edged**: today D170 correctly avoided fades. But it is *mathematically
  unable to approve a fast gapper*, so it would also reject a genuine continuation
  squeeze. It's a blunt "don't buy anything volatile" gate, not a continuation filter.

### H4 — M2 dynamic WS subscription never fired (because of H2)
`Connecting to ... (22 symbols, 0 dynamic)`, zero `add_symbols`/`Dynamically
subscribed`. → **39× VWAP unavailable** on the real intraday candidates. The doc-179
M2 fix isn't in the running build. (Will activate on restart.)

### M1 — Limit orders don't fill on thin names (CMND 0/2)
`WIDE_STOP CMND: submitting plain BUY limit ... entry=$3.57` → `never reached
terminal in 6 polls (status=new, filled_qty=0)` → cancelled, twice. Root cause
(`alpaca_executor.py`): the limit is placed **at the stale eval price, not
marketable**, with a **12-second** fill window (6 polls × 2s), on a $243K-dollar-
volume fader. Submit→fill conversion today = **0%**.

### M2 — LLM circuit breaker tripped 39× during 9:21–10:32; news EMPTY 54%
32 `litellm.Timeout` + 7 `RateLimitError`; news-ensemble latencies 32–37s; `D99
Cancelled slow agent ×94`. The "blind funnel" at 54% today. The doc-178 downgrade
*mitigates the blocking* (admits high-MFCS anyway) but the data is still degraded.

### L — recon WARN spam ×1,233 ($1 equity tolerance, in the running OLD build —
the M1 doc-179 band fix isn't deployed), SEC EDGAR 500s ×65, Finnhub fails ×12.

---

## 3. Experiment / shadow assessment — blind on the layer that matters

The experiment portfolio is **well-instrumented for EXITS but blind on SELECTION**,
which is exactly today's binding layer.

| Experiment | Active today | Tests the right thing? |
|---|---|---|
| **Stop-widening A/B (T2)** | LIVE (graduated shadow→A/B) | ✅ exit layer — the one good one |
| **D102 param replay** | 138× | ❌ sweeps *scoring* knobs (weights/threshold) computed **below** the gate layer. Records `primary=BUY` for CGTL — but the faller gate killed CGTL. Blind to the binding constraint. |
| **composite_v0 shadow** | 214× | ⚠️ samples `production_decision` **above** the faller gate; can't even attribute the block; no realized-outcome field. |
| **inverted_shadow** (grades REJECTED names) | **DEAD CODE** (0 entries, finalizer script missing) | The one tool purpose-built for "should we have bought what we rejected?" — unwired. |
| **Continuer_v2 / meta-scorer / TabPFN** | Wired to the **LOTTERY**, not the momentum bot | ❌ the squeeze-vs-pump continuation edge exists + is WF-validated but never scores the momentum bot's picks. |
| **Order-type / marketable-limit shadow** | **MISSING entirely** | Today's $0-realized cause is untested. |
| BOCPD kill-switch | armed | ⚠️ safety governor (prior still shows mu_edge=−255 contaminated value in this old build). |

**The gap**: the two highest-volume shadows (D102, composite) both sample the
decision *above* the faller/D170 gates, so neither can tell you the gate fired, let
alone whether it was right. The faller gate and D170 are **hand-tuned heuristics with
no outcome-calibration loop** — yet they are the binding constraints.

### Recommended experiment changes (ranked)
1. **Wire `inverted_shadow.py` to grade faller/D170-REJECTED names** (HIGHEST). At
   every `FALLER GATE BLOCKED` / `D170 REJECTED`, emit {ticker, score, factors,
   price, ts}; a post-close finalizer fills realized T+15/T+60/EOD return. Then the
   0.60 faller threshold and the 10% D170 drawdown are calibrated on **data**, not the
   Mar-30 ARTL/SST anecdote. ~80% of the machinery already exists (just unwired) — and
   it directly answers doc 180's manual "did CGTL fade?" question (today: it did).
2. **Run Continuer_v2 as a write-only shadow on the momentum bot's picks** (HIGH).
   Log `continuer_proba` next to `faller_score` on every candidate → finally A/B the
   ML continuation edge vs the heuristic faller gate on the *same* names. This is the
   doc-176 Phase-C unlock, currently pointed at the wrong process.
3. **Order-fill opportunity-cost shadow** (HIGH). Per entry attempt, log NBBO-at-
   submit + what a marketable/aggressive-offset limit would have paid + whether the
   unfilled name then ran. Quantify the cost of passive limits on thin names before a
   live order-type change.
4. **Make faller threshold + D170 drawdown live A/B** once #1 yields outcome data.

---

## 4. The reframe (what today actually teaches us about the 5% path)

Today is NOT a selection-loss day — the gates correctly avoided pumps that faded.
Today reveals the *real* shape of the problem:
- **The defensive layer works** (avoided 11–14% fades).
- **The offensive machinery can't fire**: D170 rejects anything that dips >10% (i.e.
  every volatile gapper, including a real squeeze); limit orders don't fill on thin
  names; the bot has no continuation edge to tell a squeeze from a pump.
- **Winners are held by accident, unprotected** (APPS +24%, broker stop 30% low).

So the path to 5% is NOT "loosen the gates to trade more pumps" (they fade). It is:
(a) a **continuation edge** to identify the rare squeeze that *keeps going* (Continuer
shadow → live), (b) an **entry path that can actually fill** a fast name (marketable
limits, D170 that distinguishes "healthy pullback" from "fade"), and (c) **deliberate
protected holds** of winners (sync the trail to the broker; intentional overnight
carry) instead of accidental ones. The experiment changes in §3 are the instrumentation
to get there.

---

## 5. Decisions for Pierce (operational + strategic)
**Operational (live, your call):**
- **APPS**: software-protected at $8.46, broker-backstop $5.88. Move the broker stop
  up to ~$8.46 to lock the +$1,625 against a gap/crash, or flatten. (Code fix: D78
  trail → broker-stop propagation — "Bug R".)
- **Restart** the bot to deploy doc 179 (M2) + doc 180 (D249 fix), which are on disk
  but not running.

**Strategic (code, ranked):**
1. Wire `inverted_shadow` to grade faller/D170 rejections (instrument the binding layer).
2. D170: distinguish healthy-pullback from fade; drop the impossible ≥4-bullish-agents
   early-entry requirement (flag-gated).
3. Marketable/aggressive limit on the WIDE_STOP entry path (so fills happen).
4. Continuer_v2 shadow on momentum picks.
5. APPS-class stop→broker propagation fix.

## Appendix — files
Evidence: `logs/momentum_2026-05-29.log`. Key code: `src/execution/entry_delay.py`
(D170), `src/execution/faller_detection.py`, `src/execution/alpaca_executor.py`
(limit pricing), `src/shadow/inverted_shadow.py` (dead), `scripts/ml_continuer_v2_*`.
