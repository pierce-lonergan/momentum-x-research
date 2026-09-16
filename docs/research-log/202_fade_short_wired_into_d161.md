# 202 — The fade-short, wired (flag-gated) — the second income stream

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "full send doc 202" — wire the ELITE-fade-short into D161,
flag-gated, with the squeeze circuit-breaker.

---

## 0. What shipped (flag-gated OFF — a strict no-op until flipped)

The doc-198→201 arc proved the bot's own high-MFCS BUY picks fade, and shorting the
**borrowable** ELITE ones nets **+9.47% / 100% win** (doc 201, n=11). doc 202 turns that
into a live-ready path:

1. **`fade_short_signature_ok(...)`** (pure, in `opening_range.py`, 8 tests) — True only
   when ALL hold: gate **enabled** (default False) ∧ **MFCS ≥ 0.50** (the ELITE-fade
   bucket) ∧ **shortable ∧ easy-to-borrow** (doc 201: only borrowable names are shortable)
   ∧ **dollar-volume ≥ floor** ∧ the **SQUEEZE CIRCUIT-BREAKER** passes.
2. **Config** (`EXEC_FADE_SHORT_*`): `enabled` (False), `min_mfcs` (0.50),
   `min_dollar_volume` ($5M), `size_mult` (0.25 — start small at n=11).
3. **main.py gate** at the long-BUY-proceed point (after the faller gate, before D170):
   when the signature matches, build a SHORT verdict (reuse `short_selling`: stop above
   entry, targets below, fractional size) → `bridge.execute_verdict` → register the stop →
   `continue` (do NOT also buy). When the flag is OFF (default), the whole block is skipped.

## 1. The squeeze circuit-breaker (the safety core)

The catastrophic risk in shorting pumps is the ~4% that RUN (the +108% tail). The guard:
**never short a name that's running.** Concretely, `fade_short_signature_ok` returns False
if either:
- **`orb_confirmed`** — the doc-188 opening-range CONTINUATION signature is present (bullish
  open ∧ broke the 5-min high ∧ RVOL ∧ above VWAP). That's a confirmed runner — the exact
  thing doc 188 detects — so we step aside.
- **`above_vwap`** — holding above VWAP = continuation (doc 187). We short only names that
  are *already failing* (below VWAP, not ORB-confirmed).

This reuses the continuation detector we built for the LONG side as the SHORT side's
kill-switch — the two halves of the selection edge protect each other.

## 2. Protection + execution

- The short routes through the executor's `_is_short` path → `submit_oto_short_order`
  (sell-limit + **broker buy-stop** above entry) — so it's broker-protected on fill — and
  the doc-190 **bid-anchored marketable SELL** gives it a realistic short fill.
- `stop_resubmitter.register_stop` adds Phase-3 software monitoring of the cover-stop.
- Fractional size (`fade_short_size_mult` 0.25) — start small given n=11.

## 3. Safety posture + pre-live checklist

- **Flag OFF by default** — zero live behavior change until `EXEC_FADE_SHORT_ENABLED=true`.
  24 unit tests green; bot boots; the gate is a verified no-op when off.
- **Pre-live follow-ups (before flipping)**: (a) full crash-recovery state persistence for
  shorts (the D161 long-short path persists more state than this minimal gate — the broker
  OTO stop + Phase-3 registration protect intraday, but a short held across a *restart*
  needs the state-recovery parity); (b) **more data** — n=11 ETB-ELITE over 3 days is a
  directional GO, not a sized commitment; let the weekly study (doc 199) accrue ~a month;
  (c) Pierce's call to flip (live competition behavior). The scorecard will show the
  fade-short's realized edge before any size-up.

## 4. The full arc is complete (177 → 202)

The selection edge is now built end-to-end, **both directions**:
- **LONG**: SELECT the continuers (188/191/198) · FILL marketably (189/190/195) · HOLD via
  tranches+trail (196/197) · don't size into faders (199, **live Monday**).
- **SHORT**: short the borrowable ELITE faders (200/201/202) — with the continuation
  detector as the squeeze guard.
- **MEASURED**: the auto-scorecard (193/194) + weekly study (199) judge every change on a
  realistic SELECT→FILL→EXIT backtest before it goes live.

Two flags live Monday (ELITE press off, exhaustion down-weight on); the fade-short stands
ready to flip the moment the data confirms the n. 🌙

## Appendix — files
- `src/analysis/opening_range.py` — `fade_short_signature_ok()` (squeeze-guarded).
- `config/settings.py` — `ExecutionConfig.fade_short_*` (4 fields, EXEC_ prefix).
- `main.py` — D202 ELITE-fade-short gate (after the faller block, before D170; flag-gated).
- `tests/unit/test_fade_short_signature.py` (new, 8 tests).
- This doc + `docs/SYSTEM_MAP/changelog.md`.
