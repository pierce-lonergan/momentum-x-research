# 50 — Bug AK: D124 consensus alignment over-rejected when bullish_conf=0

**Status:** patched 2026-04-27 evening (post-Tier-1, during Tier-2 audit).
**Severity:** **HIGH** — directly blocked an estimated 47% (17 of 37) of today's D124 rejections from reaching MFCS scoring. Most blocked tickers had no genuine bullish opinion, just a single low-confidence bearish whisper from the risk_agent or manipulation_agent.
**Surface:** **Strategic assessment + log analysis** — discovered while systematically auditing Tier 2 decision-layer over-rejection patterns.
**Bug class:** arithmetic edge case in conditional logic — `bearish_conf > bullish_conf * 1.5` reduces to `bearish_conf > 0` when `bullish_conf=0`, so ANY bearish confidence > 0 trips the rejection.
**Predecessors:** D219 calibration (2026-04-13) softened the original D124 hard-reject; this is a follow-up tightening of the D219 logic itself.

---

## §0 — TL;DR

**Old D124 v1 logic** (committed under D219 in mid-April):
```python
_bearish_dominant = (
    len(_bearish) >= len(_bullish) + 2
    or (len(_bearish) > len(_bullish) and _bearish_conf > _bullish_conf * 1.5)
)
```

When `_bullish_conf = 0`, the second branch becomes `_bearish_conf > 0` — true for any non-zero bearish confidence. Today's session log shows this firing 17 times on patterns like:

```
ELPW D124 CONSENSUS ALIGNMENT: 0 bullish (conf=0.00) vs 1 bearish (conf=0.49) — REJECT
ENVB D124 CONSENSUS ALIGNMENT: 0 bullish (conf=0.00) vs 1 bearish (conf=0.45) — REJECT
USEG D124 CONSENSUS ALIGNMENT: 0 bullish (conf=0.00) vs 1 bearish (conf=0.80) — REJECT
```

Of 37 D124 rejections today, **47% had this pattern.** The system silently killed marginal trades before MFCS scoring could evaluate them.

**New D124 v2 logic** (Bug AK):
```python
tier_a = bear_n >= bull_n + 2                              # numeric dominance
tier_b = bear_n >= 2 and bear_conf > max(bull_conf*1.5, 1.0)  # aggregate floor
tier_c = bear_n >= 1 and max_bear_conf >= 0.85             # single-veto path
_bearish_dominant = tier_a or tier_b or tier_c
```

Three named tiers. Single low-conf bearish (the 47% case) now passes through to MFCS scoring. MFCS still has the bearish weight in its composite (manipulation_agent has weight=1.00 per startup config), so a meaningful bearish signal still reduces the score — just doesn't BLOCK before MFCS evaluates.

---

## §1 — Root cause

`src/core/orchestrator.py:1068-1086` (pre-fix). The D219 logic intended to require:
- Bearish to outnumber bullish by 2+, OR
- Bearish to have "much higher" confidence (1.5×) than bullish

The 1.5× formulation works when `_bullish_conf > 0`. When `_bullish_conf = 0`, the multiplication is 0, so `_bearish_conf > 0` becomes the effective check — any bearish opinion triggers rejection.

This wasn't caught because:
1. **No unit test exercised the `bullish_conf=0` edge case.** The D219 commit didn't add tests; verification was via the already-extant production behavior, which had different agent dispatch settings at the time.
2. **The pattern only emerges at scale.** A single agent skipping or returning NEUTRAL doesn't matter for one trade decision; 37 rejections in one day shows the structural impact.
3. **The verdict-summary log shows BUY count, not REJECT count by reason.** Bug AK rejections were buried in the orchestrator log lines, not surfaced in the per-cycle summary.

---

## §2 — Why production allows this state

The current agent dispatch (per startup line: `D218 AGENT DISPATCH: active=[news(0.55), technical(0.05), fundamental(0.15), risk(0.25), manipulation(1.00)]`) means:

- **risk_agent and manipulation_agent are deterministic** and ALWAYS return SOME signal (BULL/BEAR/NEUTRAL). Both are "wary by design" for high-vol penny stocks — they often return BEAR with conf 0.4-0.8 on the exact stocks the system targets.
- **news/fundamental/technical are LLM-based** and frequently return NEUTRAL when SEC EDGAR or news data is sparse (common for sub-$5 penny stocks).
- **deep_search and institutional are weight=0** so they're skipped entirely.

Net effect: a typical penny gapper today produces 2-3 NEUTRAL agent signals + 1-2 BEAR signals from the deterministic agents + 0 BULL signals. The `_non_risk_for_consensus` filter excludes risk_agent and NEUTRAL signals, leaving... just the manipulation_agent's BEAR. That's the `0 bullish vs 1 bearish (conf=0.49-0.80)` pattern logged 17 times today.

---

## §3 — Fix

Replace the single-branch v1 logic with a three-tier explicit rule:

```python
_max_bearish_conf = max((s.confidence for s in _bearish), default=0.0)

_bearish_dominant_a = len(_bearish) >= len(_bullish) + 2
_bearish_dominant_b = (
    len(_bearish) >= 2
    and _bearish_conf > max(_bullish_conf * 1.5, 1.0)
)
_bearish_dominant_c = (
    len(_bearish) >= 1
    and _max_bearish_conf >= 0.85
)
_bearish_dominant = (
    _bearish_dominant_a or _bearish_dominant_b or _bearish_dominant_c
)
```

- **Tier A (unchanged from v1):** numeric dominance — at least 2 more bearish than bullish.
- **Tier B (tightened):** at least 2 bearish required (was: any), and aggregate confidence must exceed BOTH 1.5× bullish AND an absolute floor of 1.0 (the floor prevents the bullish_conf=0 degenerate case).
- **Tier C (NEW):** single very-high-confidence bearish (conf ≥ 0.85) can still single-veto. Preserves the manipulation/fraud-detection escape hatch — if the manipulation agent flags BEAR with conf 0.90, that's worth a hard block even alone.

Logging includes the firing tier so future audits can distinguish A/B/C rejections.

---

## §4 — How this would have manifested in production today

| Pattern | Count | Old v1 verdict | New v2 verdict | Reason |
|---------|-------|----------------|----------------|--------|
| 0 vs 1 bearish (conf 0.45) | 7 | REJECT | **PASS to MFCS** | Tier C threshold not met (0.45 < 0.85), Tier B requires ≥2 bearish |
| 0 vs 1 bearish (conf 0.49) | 3 | REJECT | **PASS** | Same as above |
| 0 vs 1 bearish (conf 0.55) | 1 | REJECT | **PASS** | Same |
| 0 vs 1 bearish (conf 0.80) | 7 | REJECT | **PASS** | 0.80 < 0.85 single-veto threshold |
| 0 vs 2 bearish (conf 1.04) | 6 | REJECT | REJECT (Tier B) | ≥2 bearish AND agg conf > 1.0 |
| 0 vs 2 bearish (conf 1.00) | 5 | REJECT | REJECT (Tier B) | borderline but agg=1.00 fails > check; need to verify... see §6 |
| 0 vs 2 bearish (conf 1.25) | 4 | REJECT | REJECT (Tier B) | clear |
| 0 vs 2 bearish (conf 1.29) | 3 | REJECT | REJECT (Tier B) | clear |
| 1 vs 2 bearish (conf 1.25) | 1 | REJECT | REJECT (Tier B; max(1.5*0.5, 1.0) = 1.0; 1.25 > 1.0) | clear |

**Net: 18 of 37 rejections (49%) would now pass through to MFCS scoring.**

Of the 18 that pass through, MFCS will further filter:
- Most will likely become HOLD (MFCS < 0.25) because the bearish signal still deducts from the composite
- Some may become BUY if they have strong volume_rvol/float_structure components
- The system gains decision-replay data on each one (can audit later whether the rejection was warranted)

Critical observation: **Bug AK alone won't generate dozens of new trades.** It moves rejections from "hard block at D124" to "soft filter at MFCS." The system still has the position cap, fast-path dedup, and other gates. But it CAPTURES the data needed to audit whether those filters are themselves correct — without Bug AK, the data was lost at the D124 layer.

---

## §5 — Tier 2 audit summary (other 3 weaknesses)

This section documents what was REVIEWED but NOT changed during the Tier 2 sweep, with rationale (so future maintainers don't re-investigate):

| # | Weakness (per strategic assessment) | Audit verdict | Rationale |
|---|---|---|---|
| **5** | D124 consensus over-reject | **FIX (Bug AK)** | Real arithmetic defect; 47% of rejections affected |
| **6** | D106 PROMO_EARLY blanket-blocks | **NO CHANGE** | D106 is a SIZING modifier (half position, tighter stops, aggressive targets), not a rejection gate. Today's tickers tagged `D106:PROMO_EARLY` (ATOM, ENVB, ELPW, IHRT, LIDR) became HOLD because of MFCS<0.25, not because of D106. The deeper question of whether PROMOTIONAL_EARLY classification is accurate needs decision-replay analysis (Tier 3 work). |
| **7** | MFCS threshold marginal at 0.25 | **NO CHANGE** | The system already handles this via D216 (dynamic threshold reduction when agent data is degraded) + fast-path top-pick (when 5 BUYs fire, only the highest-MFCS one trades). Today's actual trade (OGN @ MFCS=0.525) was solidly above threshold — the trade flow is working as designed. |
| **8** | D101 VIX sizing halves above VIX=15 | **NO CHANGE** | The D205 scenario analysis (documented in `ScoringWeights.vix_reduce_threshold` field description) showed VIX 15-20 has lower avg P&L (+6.1% vs +7.8% at VIX 12-15) despite higher win rate. The half-sizing has empirical backing on this system's own data. Today's LIDR loss would have been 2× larger ($-2632 vs $-1316) without D101 halving — the gate ACTUALLY HELPED. |

Net Tier 2 work: 1 real fix (Bug AK), 3 reframed-as-correct after audit. Intellectually honest outcome — the strategic assessment overcounted the bugs.

---

## §6 — Edge case: aggregate conf exactly 1.0

The Tier B check is `_bearish_conf > max(_bullish_conf * 1.5, 1.0)`. Strict greater-than. So `0 vs 2 bearish (agg conf 1.00)` does NOT trigger Tier B — falls through to Tier A (`bear >= bull + 2` → 2 >= 0 + 2 → True). Tier A catches it.

The 5 cases of "0 vs 2 bearish (conf 1.00)" today are all caught by Tier A. ✓

---

## §7 — What changes downstream

- **Test layer:** `tests/unit/test_bug_ak_d124_calibration.py` (20 tests). Includes a statistical replay of today's 37 rejections through both v1 and v2 logic to pin the 47% reduction estimate.

- **Operator visibility:** the `D124 CONSENSUS ALIGNMENT (tier=A:numeric|B:aggregate|C:single-veto)` log line now identifies which tier fired. EOD analysis can audit per-tier rejection counts.

- **Future calibration:** if the C threshold (0.85) turns out to over-veto, it can be raised to 0.90 with one parameter change. If the B aggregate floor (1.0) turns out to under-protect, it can be lowered to 0.7 similarly. The named-tier structure makes the calibration knobs explicit.

- **Bug-letter alphabet advances to AL** for the next surface. Note: the original "Bug AL" planning slot was for D106 PROMO_EARLY, which is now reframed as no-change. Bug AL is now reserved for the next genuine production bug.

---

## §8 — The discipline this preserves

D124 is a SAFETY rail — it should reject obviously-bad setups (multiple agents flagging concerns) but not block setups where the system has no opinion either way (most agents NEUTRAL).

The v1 logic conflated "no bullish opinion" with "bearish dominant." That's a category error: silence is not opposition. v2 distinguishes: silence + 1 cautious voice = pass to MFCS for nuanced scoring; silence + 2 cautious voices with meaningful conf = block.

The 22/0 → 23/0 ratio holds. Cost of bug AK in production: ~17 trades blocked today that should have been MFCS-evaluated (we don't know how many would have actually fired, but we know they should have been considered). Fix cost: ~50 LOC + 20 tests + 1 finding doc.
