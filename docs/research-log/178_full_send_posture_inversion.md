# 178 — Full-Send Posture Inversion (the path-to-5% changes, shipped)

**Author**: Claude Opus 4.8
**Date**: 2026-05-28 (Thursday, post-close)
**Mandate**: Pierce chose **"Full send — all tiers ON for Friday"** + "commit now"
after the doc-177 forensics. This doc is the implementation: the selection /
exit / sizing posture inversion doc 176 prescribed and doc 177 reframed, shipped
behind env flags (default ON) with the guardrails kept.
**Prior commit**: `463ad4c` (doc 177 — the 4 mechanical bug fixes). This is a
SEPARATE commit so the aggressive posture can be reverted as one unit
independent of the mechanical fixes.

---

## 0. What this changes, in one paragraph

The bot was a capital-preservation machine pointed at the fattest right-tail in
equities (doc 176), and it was **blind ~2/3 of the time** so its gates blocked on
data absence (doc 177). This commit inverts the posture on the high-conviction
subset: **see better** (news timeout 15s→25s), **stop vetoing the thesis**
(catalyst/news gates downgrade-not-block on MFCS≥0.45), **let winners run** (trail
6%/30%/5%, exit-override needs 2 strategies @0.85 and excludes the two
winner-killers), and **press the best names** (ELITE MFCS≥0.50 → 2% risk). A −3%
daily drawdown guardrail is the counterweight. Everything is one env var from
revert.

---

## 1. Shipped — tier by tier (file:line, flag, revert, expected effect)

### Tier 1 — DATA
| Δ | File | From→To | Revert env | Why |
|---|---|---|---|---|
| **H3** news timeout | `settings.py` `litellm_timeout_tier2` | 15s → **25s** | `LLM_LITELLM_TIMEOUT_TIER2=15` | news_agent (Tier-2, ensemble×3) timed out at 15s → EMPTY 67% → gates blind. #1 selection unlock, lowest risk. |

### Tier 2 — SELECTION (downgrade-not-block; the funnel opener)
New `UniverseConfig` flags: `catalyst_gate_downgrade_high_mfcs=True`,
`..._mfcs_threshold=0.45`, `..._qty_mult=0.5` (env `UNIVERSE_CATALYST_GATE_DOWNGRADE_*`).
- **D200 catalyst gate** (`main.py` ~3392): when catalyst_type∈{None,…} AND
  MFCS≥0.45 → **downgrade** (`verdict.qty_multiplier=min(cur,0.5)`) and admit,
  instead of `continue`-block. Below 0.45 still hard-blocks.
- **D204 news gate** (`main.py` ~3423): same — and explicitly treats EMPTY/
  timed-out news as *abstain*, not bearish (the blind-funnel fix).
- **Executor fix** (`alpaca_executor.py` ~302): `qty_multiplier` was applied
  ONLY on the `wide_stop` arm — it **silently no-op'd the downgrade on the
  default tight_stop arm**. Now applied for any arm when `mult<1.0` (tight_stop
  default is 1.0, so T2 behavior is unchanged; 33 T2 tests still pass).
- *Effect:* admits NCPL (0.525, ran +38.5%) / AMSS (0.546) at half size; keeps
  the gate for the marginal no-news pump cohort (<0.45).

### Tier 3 — EXITS (doc 176's "biggest single lever")
- **D163 trail** (`settings.py` `TrailingStopConfig`): activation 2%→**6%**,
  trail-of-gain 50%→**30%**, min-distance 2%→**5%**. Revert `TRAIL_*`. The 2%
  activation exited LFS at +0.7% (ran +28%) and APPS at +0.3%.
- **D122 single-strategy override** (`settings.py` `ExitIntelligenceConfig`):
  `parallel_exit_min_confidence` 0.7→**0.85**, `parallel_min_strategies_for_exit`
  1→**2**, and **added `gratitude`+`alpha_oracle`** to the excluded list (they
  fired on winners — gratitude flattened BB at +1.8%, alpha_oracle exited UMAC at
  −2.2% while it was set to run +26%). Revert `EXIT_PARALLEL_*`.
- *Effect:* the trail + hard stop remain primary protection; the itchy
  single-strategy flatten is gone. Lets the 1-in-5 monster run.

### Tier 4 — SIZING (press the tail)
New `ExecutionConfig` flags: `elite_sizing_press_enabled=True`,
`elite_sizing_mfcs_threshold=0.50`, `elite_risk_per_trade_pct=0.02`
(env `EXEC_ELITE_SIZING_*`).
- `main.py` (after D204): MFCS≥0.50 → `verdict.risk_per_trade_pct = max(cur, 0.02)`
  (2% vs 1% base). Independent of the qty_multiplier downgrade: a no-catalyst
  ELITE name nets ~base (2%×0.5), a clean ELITE name presses to 2%.
- **Position limit:** *no change* — `.env`/secrets already pin
  `EXEC_MAX_POSITIONS=8` (doc 176's "max 3" read the un-overridden default). The
  binding constraint was never slot count; it was selection + per-name sizing.

### Guardrail
- **Daily drawdown** (`settings.py` `daily_drawdown_limit_pct`): 5%→**3%**.
  Disables aggressive sizing (reverts to conservative `position_size_pct`) at
  −3% — the counterweight to the higher per-day variance the inversion creates.
  Revert `EXEC_DAILY_DRAWDOWN_LIMIT_PCT=0.05`.

**Conviction gating preserved (doc 176/177 guardrail):** every Tier-2/4 change
keys off MFCS (0.45 / 0.50). The MEDIUM/LOW body keeps the defensive defaults.
Tier-3 exits are applied globally (the winner-killing was not conviction-specific
and the trail/stop still protect every name).

---

## 2. Verification
- All edited files compile; config load-checked (timeout=25, trail 0.06/0.30/0.05,
  override 0.85/2/[+gratitude,+alpha_oracle], catalyst downgrade 0.45/0.5, elite
  0.50/0.02, drawdown 0.03, max_positions=8 env-pinned).
- **126 sizing/T2/executor unit tests pass** (incl. 33 `test_doc171_t2_layers`,
  17 `test_executor`) — the executor qty_multiplier change broke nothing.
- Touched-path integration: **77 pass, same 10 pre-existing failures**
  (`orchestrator.py:312` MagicMock rot, confirmed via stash) — zero new failures.
- The downgrade/press knobs (`qty_multiplier`, `risk_per_trade_pct`) were traced
  end-to-end from the gate → through faller (which only touches the cosmetic
  `position_size_pct` on longs and a separate `_short_verdict`) → to the executor.

## 3. Deferred (documented, NOT shipped tonight) — with reasons
| Item | Why deferred |
|---|---|
| **H2** shutdown wipes recovery flags | Fix touches the daily-reset state machine; a wrong edit could block the *next* day's EOD. Impact is narrow (post-Phase-4 same-day restart). Best done with date-keyed state, deliberately. |
| **D112 RVOL re-evaluate** (VCIG +73% rejected at 0.3x then never revisited) | Needs re-scan loop logic; higher breakage risk than config changes. High value — next. |
| **D106 PROMOTIONAL_EARLY VWAP-exit** (vs 10:30 hard cut) | Exit refactor; the Tier-3 trail changes already help these names. |
| **M1** $1 recon tolerance (711 shadow RECON_LETHAL) | Shadow-mode, won't halt live yet; fix before un-shadowing. |
| **M2** fixed 19-symbol WebSocket sub | Post-open candidates trade on stale REST data; medium refactor. Real, next. |

## 4. Friday hypothesis & what to watch
With doc 177 (mechanics) + this (posture) live:
1. **Fills up:** expect 2 → 4-6 entries (downgrade-not-block + better news data).
   Watch the new log lines `D200-E4 … DOWNGRADED`, `D204 … DOWNGRADED`,
   `doc178 ELITE SIZING`, `SIZING … qty_multiplier=`.
2. **Right names:** the high-MFCS squeezes (NCPL/AMSS-class) should now appear in
   the fill set, not just the leftover 6th-best names.
3. **Hold longer / capture more:** fewer single-strategy flat-exits; trail should
   exit winners at a fraction of *peak*, not +0.7%.
4. **news_agent EMPTY < 20%** (H3).
5. **Guardrail:** if the day hits −3%, aggressive sizing auto-disables.

**Attribution caveat (accepted by Pierce):** many changes ship together, so a good
or bad Friday won't cleanly attribute to one lever. The per-tier env flags let us
A/B afterward. If Friday is ugly, the fastest blunt revert is
`UNIVERSE_CATALYST_GATE_DOWNGRADE_HIGH_MFCS=false` (re-close the funnel) and/or
`EXEC_ELITE_SIZING_PRESS_ENABLED=false` (stop pressing); the whole commit can be
`git revert`ed as a unit.

## 5. Risk acknowledgment
This is an aggressive, multi-lever change to a live (paper) competition bot, shipped
the night before the session, at the user's explicit direction. It is paper money;
absolute size is bounded by `max_position_pct=15%` + D150 tier caps; the −3%
guardrail and L2 hedge watcher remain; every lever is one env var from revert; and
it is isolated in its own commit. The biggest residual risk is the Tier-2 downgrade
re-admitting promotional pumps — bounded to half size and gated to MFCS≥0.45.

## Appendix — files touched
- `config/settings.py` (H3, trail, D122, catalyst-downgrade flags, ELITE flags, drawdown)
- `main.py` (D200/D204 downgrade, ELITE press)
- `src/execution/alpaca_executor.py` (qty_multiplier any-arm fix + doc-176 comment annotations)
- `src/core/orchestrator.py` (doc-176 advisory comment annotations only)
- This doc + `docs/SYSTEM_MAP/changelog.md`
