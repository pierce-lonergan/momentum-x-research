# 191 — The gate-action, staged: liquidity-floored faller exemption (flag OFF)

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "(b) pre-build the liquidity-floored faller-exemption,
flag-gated OFF, ready to flip the moment the observe data confirms — and trade the
continuers."

---

## 0. What this is (the flip doc 188 promised)

doc 188 built the continuation detector and wired it in OBSERVE mode at the faller
gate (the `D188 CONTINUATION-CONFIRMED but FALLER-BLOCKED` warning). doc 191 builds the
**gate-ACTION** that warning is the precursor to — the switch that turns the faller
block from:

> "block **every** predicted fader"

into:

> "block **UNLESS** the validated continuation signature (doc 188) is present AND the
> name is liquid enough to fill + exit — then let it through."

It is **shipped OFF**. When OFF (the default), the wired call is a strict no-op and the
faller block behaves exactly as before. This is the "build it, stage it, flip it after
the data proves it" discipline — not "trust a pump signal on our universe today."

## 1. The shipped change

**`faller_exemption_ok(*, enabled, confirmed, dollar_volume, opening_rvol, …)`** — a
pure predicate (primitive args → bool) in `opening_range.py`. Returns True ONLY when
ALL hold:
1. **enabled** — the gate-action flag (default **False**);
2. **confirmed** — the doc-188 opening-range continuation signature is present;
3. **dollar_volume ≥ floor** — a **liquidity floor** (default **$5M**); the edge needs
   fills + a survivable exit, so we NEVER exempt the thinnest names (the doc-187 caveat
   made operational — its edge was validated on liquid stocks);
4. **opening_rvol ≥ min** (default **2.0**) — a higher conviction bar than the
   detector's confirm threshold, because a LIVE override of a hard gate deserves more
   than a marginal signal.

**Config** (`FallerDetectionConfig`, `FALLER_` prefix): `continuation_exemption_enabled`
(False), `_min_dollar_volume` ($5M), `_min_opening_rvol` (2.0).

**Wiring** (`main.py`, the D160 faller-reject): the previously *unconditional* `continue`
(skip-execution) is now `if exempt: <fall through> else: continue`. An exempted name
falls through to the existing **D160 size-reduce** → **D170 entry-delay** → execution —
i.e., it trades at the faller-**reduced** size (conservative on the first flip; the
full-size *press* is the next, separate flag per doc 188 §4).

## 2. Why default-OFF is the whole point

The honest answer to "why don't we just trade the pumps now": doc-187's continuation
edge was validated on **liquid** stocks, and we have **zero** confirmation it holds on
our **low-float** universe yet. Flipping the gate live today would be the exact
trade-pumps-blindly mistake. So:

1. **Observe** (doc 188, live): the `D188` warnings + the doc-182 rejection-grader
   record every confirmed-but-blocked name and grade post-close whether it ran.
2. **Calibrate**: after ~20-30 graded confirmed-continuers, check the run-vs-fade rate
   on OUR tape and tune the floor / RVOL bar.
3. **Flip**: set `FALLER_CONTINUATION_EXEMPTION_ENABLED=true`. doc 191 makes step 3 a
   one-line env change — the logic, sizing path, and safety floor are already in place
   and tested.

## 3. Safety / reversibility

- **Default OFF** ⇒ exact prior behavior (proven by `test_all_four_conditions_required`
  + `test_disabled_is_always_false…`). The wiring is guarded (try/except → safe
  `continue` on any error) and `_or_sig` is hoisted so the check is never a NameError.
- **Liquidity floor** is mandatory in the predicate — a confirmed-but-thin name is NOT
  exempted, by construction.
- **Conservative sizing** on flip: exempted names still take the faller size-reduce.
- **Tests**: 7 new in `test_faller_exemption.py` (disabled-no-op, unconfirmed, the happy
  path, below-floor, below-RVOL, at-threshold inclusive, all-four-required). 83 unit
  tests pass across the related suites.

## 4. The chain — selection side

This is the **SELECT** counterpart to doc 189/190's FILL: PICK the continuer (188) →
*stop vetoing it* (191, when flipped) → FILL it (189/190) → HOLD (176/178) → SIZE
(178 + the next press flag + continuer Kelly). With 188+191 the selection layer can
(once the data says so) stop blocking its own best continuation ideas; with 189+190 it
can fill them. The offensive machine is built end-to-end; the live flip waits on the
observe data — exactly as it should.

## Appendix — files
- `src/analysis/opening_range.py` — `faller_exemption_ok()` pure predicate.
- `config/settings.py` — `FallerDetectionConfig.continuation_exemption_*` (3 fields).
- `main.py` — D160 faller-reject: `_or_sig` hoist + exemption-conditional `continue`.
- `tests/unit/test_faller_exemption.py` (new, 7 tests).
- This doc + `docs/SYSTEM_MAP/changelog.md`.
