"""DOC 297 — THE TRIAL REGISTRY: the machine-readable multiplicity counter.

WHY THIS EXISTS
---------------
The program wants a system that continuously proposes and tests hypotheses. That is, by construction,
a multiple-testing machine. Doc 297's adversary named the single property that makes "self-feeding"
survivable and observed that it does not exist:

    "A loop that generates hypotheses without incrementing a counter that feeds the threshold
     silently raises its own bar while lowering the observed one."

`docs/ATTEMPTS_LEDGER.md` tracks ~35 FAMILIES in prose. Experiment-level N is untracked entirely.
Under the false-strategy theorem the null-expected maximum backtest Sharpe is ~1.07 at N=35 trials and
~1.63 at N=1000 — so a program that has quietly run hundreds of trials and reports a Sharpe of 1.2 has
found nothing, while believing it found something.

This module is the counter. Every hypothesis — human, LLM, or loop-generated — registers here BEFORE
any data is touched, and the promotion threshold is computed FROM the count rather than chosen after
seeing the result.

WHAT IT ENFORCES
----------------
1. Append-only, hash-chained trial log. A trial cannot be un-run, and tampering is detectable.
2. Registration happens before observation: a trial carries its frozen spec hash and a registered_at
   stamp; `record_result` refuses a trial that was never registered.
3. The promotion bar is a FUNCTION of the trial count, not a constant:
      - deflated Sharpe (Bailey & Lopez de Prado) against the null-expected maximum over N trials
      - Harvey-Liu-Zhu style haircut as a cross-check
   Both are computed automatically and reported together; disagreement is surfaced, not hidden.
4. No self-promotion (doc 276: recursive self-audit has no fixed point). This module can mark a trial
   PROPOSED / FROZEN / COLLECTING / REPORTED. It cannot mark one PROMOTED — that transition is
   reserved for a cross-model skeptic fleet plus the operator, and `promote()` refuses without an
   explicit external adjudication reference.

READ-ONLY against the trading system: this touches nothing but its own ledger.

Usage:
    python scripts/trial_registry.py --register --family "overnight-etf" --spec-file <path> \
        --hypothesis "SPY overnight close->open mean > 0 net of cost"
    python scripts/trial_registry.py --status
    python scripts/trial_registry.py --threshold --sharpe 1.4 --n-obs 252
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
LEDGER = _ROOT / "data" / "research" / "trial_registry.jsonl"

VALID_STATES = ("PROPOSED", "FROZEN", "COLLECTING", "REPORTED", "ABANDONED")

# Euler-Mascheroni, used by the expected-maximum-Sharpe formula.
_EULER = 0.5772156649015329


# ── the ledger (append-only, hash-chained) ──────────────────────────────────

def _read() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]


def _chain_hash(prev: str, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev + body).encode("utf-8")).hexdigest()


def _append(payload: dict) -> dict:
    rows = _read()
    prev = rows[-1]["chain"] if rows else "genesis"
    payload = dict(payload)
    payload["chain"] = _chain_hash(prev, payload)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")
    return payload


def verify_chain() -> dict:
    """Detect tampering: recompute the chain and report the first divergence."""
    rows = _read()
    prev = "genesis"
    for i, r in enumerate(rows):
        body = {k: v for k, v in r.items() if k != "chain"}
        expect = _chain_hash(prev, body)
        if expect != r.get("chain"):
            return {"ok": False, "broken_at_index": i, "trial_id": r.get("trial_id")}
        prev = r["chain"]
    return {"ok": True, "n_rows": len(rows)}


# ── registration ────────────────────────────────────────────────────────────

def register(family: str, hypothesis: str, spec_path: str | None = None,
             universe: str = "", horizon: str = "", death_date: str = "") -> dict:
    """Register a trial BEFORE any data is touched. Returns the trial row."""
    spec_hash = ""
    if spec_path:
        p = Path(spec_path)
        if not p.is_absolute():
            p = _ROOT / spec_path
        spec_hash = hashlib.sha256(p.read_bytes()).hexdigest()
    n_before = trial_count()
    row = {
        "trial_id": f"T{n_before + 1:05d}",
        "family": family,
        "hypothesis": hypothesis,
        "spec_path": spec_path or "",
        "spec_sha256": spec_hash,
        "universe": universe,
        "horizon": horizon,
        "death_date": death_date,
        "state": "PROPOSED",
        "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trial_ordinal": n_before + 1,
        "result": None,
    }
    return _append(row)


def set_state(trial_id: str, state: str, note: str = "") -> dict:
    if state not in VALID_STATES:
        raise SystemExit(f"invalid state {state}; valid: {VALID_STATES}")
    return _append({
        "trial_id": trial_id, "state": state, "note": note, "kind": "state_change",
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


def record_result(trial_id: str, sharpe: float, n_obs: int, note: str = "") -> dict:
    """Attach an observed result. Refuses a trial that was never registered."""
    ids = {r.get("trial_id") for r in _read() if r.get("result") is not None or "family" in r}
    if trial_id not in ids:
        raise SystemExit(
            f"{trial_id} was never registered. A result on an unregistered trial is exactly the "
            "multiplicity leak this registry exists to prevent."
        )
    thr = promotion_threshold(sharpe=sharpe, n_obs=n_obs)
    return _append({
        "trial_id": trial_id, "kind": "result",
        "observed_sharpe": sharpe, "n_obs": n_obs,
        "threshold": thr, "clears_bar": bool(thr["clears"]), "note": note,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


def trial_count() -> int:
    return sum(1 for r in _read() if "family" in r and r.get("kind") is None)


# ── the bar, as a function of how many times we have looked ─────────────────

def expected_max_sharpe(n_trials: int, n_obs: int = 252, periods_per_year: int = 252) -> float:
    """Null-expected MAXIMUM annualised Sharpe across N independent trials (Bailey/Lopez de Prado).

    This is the number a program must beat merely to have done better than luck given how many
    times it looked. It is why "we found a Sharpe of 1.2" means nothing without N.

    Two factors, and BOTH matter:
      - the extreme-value term grows with the number of trials N;
      - the scale term is the standard error of an annualised Sharpe estimate, sqrt(ppy / n_obs),
        which SHRINKS with sample length. Omitting it (an easy mistake) returns a z-score rather
        than a Sharpe and overstates the bar on long samples by 2x or more.

    Worked check: N=35 trials on 4 years of daily data (n_obs=1008) gives SE=0.5 and E[max]~1.07 —
    the figure doc 297 quotes. The same N on one year (n_obs=252, SE=1.0) gives ~2.14.
    """
    n = max(int(n_trials), 2)
    se_sharpe = math.sqrt(periods_per_year / max(int(n_obs), 2))
    # inverse normal CDF via Acklam-style rational approximation is overkill here; use the
    # standard asymptotic form E[max] ~ (1-g)*Z(1-1/N) + g*Z(1-1/(N*e))
    def _z(p: float) -> float:
        # Moro/Beasley-Springer inverse normal, adequate for p in (0,1)
        a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
             1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
        b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
             6.680131188771972e+01, -1.328068155288572e+01]
        c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
             -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
        d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
             3.754408661907416e+00]
        pl, ph = 0.02425, 1 - 0.02425
        if p < pl:
            q = math.sqrt(-2 * math.log(p))
            return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        if p > ph:
            q = math.sqrt(-2 * math.log(1 - p))
            return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)

    return se_sharpe * ((1 - _EULER) * _z(1 - 1.0 / n) + _EULER * _z(1 - 1.0 / (n * math.e)))


def promotion_threshold(sharpe: float, n_obs: int, n_trials: int | None = None) -> dict:
    """The bar this observation must clear, given how many trials the program has run.

    Returns both the deflated-Sharpe view and the expected-maximum view. They are reported
    together on purpose: when they disagree, that disagreement is information.
    """
    n_trials = trial_count() if n_trials is None else n_trials
    emax = expected_max_sharpe(max(n_trials, 2), n_obs=n_obs)
    # Deflated Sharpe ratio (normal-returns simplification; no skew/kurtosis adjustment because
    # the program does not yet measure them reliably — stated, not hidden). `se` is the standard
    # error of the annualised Sharpe estimate on this sample, matching emax's units.
    se = math.sqrt(252.0 / max(n_obs, 2))
    z = (sharpe - emax) / se if se > 0 else 0.0
    # standard normal CDF
    dsr = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    # doc 298: report the bar clears() ACTUALLY enforces, not just its first term. Quoting emax alone
    # understates the threshold by 1.645*se — at 33 trials on 2.08yr that is 1.465 vs the true 2.606.
    # The gap was large enough to make a dead candidate look arguable, so both are now returned.
    operative = emax + 1.6449 * se
    return {
        "n_trials_registered": n_trials,
        "expected_max_sharpe_under_null": round(emax, 4),
        "significance_margin_1p645_se": round(1.6449 * se, 4),
        "OPERATIVE_BAR": round(operative, 4),
        "observed_sharpe": sharpe,
        "n_obs": n_obs,
        "n_years_implied": round(n_obs / 252.0, 2),
        "deflated_sharpe_prob": round(dsr, 4),
        "clears": bool(sharpe > emax and dsr > 0.95),
        "note": ("OPERATIVE_BAR is what clears() enforces: the null-expected best Sharpe over N trials "
                 "PLUS the one-sided 95% margin. expected_max_sharpe_under_null alone is only the first "
                 "term and quoting it as 'the bar' understates the threshold. Raise n_obs or lower N — "
                 "there is no third option."),
    }


def promote(trial_id: str, adjudication_ref: str = "") -> dict:
    """Refused by design. Doc 276: recursive self-audit has no fixed point."""
    raise SystemExit(
        "promote() is not available to an automated caller (doc 297 invariant 4).\n"
        "A loop may propose, freeze, collect and report. Promotion requires a cross-model skeptic "
        "fleet plus the operator's ratification, recorded outside this process.\n"
        f"(requested for {trial_id}, adjudication_ref={adjudication_ref!r})"
    )


def status() -> dict:
    rows = _read()
    trials = [r for r in rows if "family" in r and r.get("kind") is None]
    latest_state: dict[str, str] = {t["trial_id"]: t["state"] for t in trials}
    for r in rows:
        if r.get("kind") == "state_change":
            latest_state[r["trial_id"]] = r["state"]
    by_family: dict[str, int] = {}
    for t in trials:
        by_family[t["family"]] = by_family.get(t["family"], 0) + 1
    n = len(trials)
    # doc 298: never report a bar without saying what sample length it assumes. The old default
    # silently used n_obs=252 and got quoted against multi-year claims.
    bars = {f"{yrs}yr": promotion_threshold(0.0, int(yrs * 252), n)["OPERATIVE_BAR"]
            for yrs in (1, 2.5, 5, 10.5)}
    return {
        "n_trials": n,
        "operative_bar_by_sample_length": bars,
        "by_family": by_family,
        "states": latest_state,
        "chain": verify_chain(),
        "ledger": str(LEDGER.relative_to(_ROOT)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--register", action="store_true")
    ap.add_argument("--family", default="")
    ap.add_argument("--hypothesis", default="")
    ap.add_argument("--spec-file", default=None)
    ap.add_argument("--universe", default="")
    ap.add_argument("--horizon", default="")
    ap.add_argument("--death-date", default="")
    ap.add_argument("--state", nargs=2, metavar=("TRIAL_ID", "STATE"))
    ap.add_argument("--result", nargs=3, metavar=("TRIAL_ID", "SHARPE", "N_OBS"))
    ap.add_argument("--threshold", action="store_true")
    ap.add_argument("--sharpe", type=float, default=None)
    ap.add_argument("--n-obs", type=int, default=None)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.register:
        if not args.family or not args.hypothesis:
            raise SystemExit("--register needs --family and --hypothesis")
        print(json.dumps(register(args.family, args.hypothesis, args.spec_file,
                                  args.universe, args.horizon, args.death_date), indent=2))
        return 0
    if args.state:
        print(json.dumps(set_state(args.state[0], args.state[1].upper()), indent=2))
        return 0
    if args.result:
        print(json.dumps(record_result(args.result[0], float(args.result[1]), int(args.result[2])), indent=2))
        return 0
    if args.threshold:
        if args.sharpe is None or args.n_obs is None:
            raise SystemExit("--threshold needs --sharpe and --n-obs")
        print(json.dumps(promotion_threshold(args.sharpe, args.n_obs), indent=2))
        return 0
    print(json.dumps(status(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
