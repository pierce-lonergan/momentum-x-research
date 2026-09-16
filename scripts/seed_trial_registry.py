"""DOC 298 — seed the trial registry with the program's actual history.

The registry was built in doc 297 but reads zero, so it enforces nothing. A multiplicity counter that
starts at zero after sixty gated hypotheses tells you the bar is Sharpe 0.52 when the honest bar is
1.48 — it would license exactly the false positive it exists to prevent.

This seeds it from docs/ATTEMPTS_LEDGER.md: one trial per family the program actually gated, with the
doc that ran it and the verdict it reached. These are historical facts, not new hypotheses, so they are
registered as REPORTED. Idempotent — re-running does not double-count.

After seeding, `python scripts/trial_registry.py --status` reports the real bar, and every NEW
hypothesis registered from here raises it further.

Usage:  python scripts/seed_trial_registry.py --seed
        python scripts/seed_trial_registry.py --dry-run
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("tr", _ROOT / "scripts" / "trial_registry.py")
TR = importlib.util.module_from_spec(_SPEC); _SPEC.loader.exec_module(TR)

# (family, hypothesis, docs, verdict). One row per family the program actually GATED — triage-only
# entries are excluded on purpose: doc 293 states "0 kill tests yet run" for those, and an unmeasured
# family did not consume a look.
HISTORY = [
    ("per-name-direction", "Price/momentum direction is predictable in the gapper universe", "250-261,283", "no edge"),
    ("exit-timing", "Exit-timing optimisation converts AUC into dollars", "230-234", "no deployable edge"),
    ("loss-cap-b1b2", "Loss-cap / re-framed exit variants improve expectancy", "236", "no edge"),
    ("loss-cap-c2", "c2 loss-cap variant improves expectancy", "239", "no edge"),
    ("rocket-exante", "Rockets are selectable ex ante from tick/OFI/tape microstructure", "BET#3,242-248", "cross-regime money gate fails"),
    ("deepset-tape", "Deep-set raw-tape screens carry transferable signal", "248", "no transferable signal"),
    ("rl-scalper", "An RL scalper beats cost-realistic baselines", "256-257", "no transferable signal"),
    ("pead-stage-a", "Post-earnings drift survives in the modern sample", "259", "gate fails"),
    ("filing-text", "LLM filing-text amplification adds signal over PEAD", "260", "gate fails"),
    ("coiled-catalyst", "Prior-runner / coiled-catalyst patterns predict continuation", "254", "no edge (clean forward test)"),
    ("multiday-dilution", "Multi-day candidacy / dilution signal predicts returns", "258", "no edge"),
    ("binary-event", "Binary-event handicapping from a catalyst calendar is feasible", "261", "pilot fails feasibility"),
    ("exit-posture-flip", "Blanket exit-posture flip (incl. overnight carry) improves P&L", "280", "null-to-negative"),
    ("short-door", "A short edge survives borrow costs on the gapper universe", "283-284,288", "net negative; tombstoned"),
    ("cohort-relational", "Within-cohort position predicts the winner", "289", "channel approximately empty"),
    ("attention-coupling", "Attention coupling predicts price across 16 pre-declared regime cells", "289-290", "0/32 after correction"),
    ("h-local-vectordb", "Micro-regime kNN retrieval beats a rank-calibrated baseline", "290", "refuted at gate and effect size"),
    ("low-freq-concentration", "Low-frequency concentration substitutes for an edge", "290", "arithmetic + risk ruling"),
    ("short-tenor-rv-iv", "Short-tenor (<=9d) RV forecasting beats IV", "292", "sign-negative"),
    ("stage3-rv-iv-30d", "Single-name RV forecasting beats calibrated IV at the 30d tenor", "294-296", "VOID at h=1; negative at frozen h=21"),
    ("vol-door-stage1", "In-universe vol-score risk-shaping improves outcomes", "291", "gates passed; proposal HELD"),
    ("vol-door-stage2", "RV challenger beats HAR-class baselines out of sample", "291", "passed, generic-feature-carried"),
    ("iv-pilot", "Our RV forecast beats calibrated index IV ($0 pilot)", "292", "ambiguous; single-name subgroup weak"),
    ("overnight-etf", "Overnight/intraday ETF decomposition delivers a bankable net edge", "297", "beta + T-bill; buy-and-hold wins 4/4"),
    ("gapper-long-universe", "The bot's own long universe has positive expectancy", "297", "-2.041%/ticket, negative in all 3 years"),
    ("doc251-liquid-fade", "Liquid gap-up fade earns a positive net spread", "251,297", "day-blocked CI spans zero"),
    ("rocket-gate-forward", "The rvol>100 x hour-9 conditional gate is real forward", "284", "collecting, n<30"),
    ("kalshi-shadow", "LLM forecasts beat Kalshi prices net of fees", "284", "failing: Brier advantage negative"),
    ("sevp-event-vol", "Short ATM straddle T-1 to T+1 around earnings carries positive net", "294", "ARMED, blind, 205/300"),
    ("fwd-rv-ledger", "The doc-291 RV advantage replicates forward", "292", "collecting, n<60"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    existing = {r.get("family") for r in TR._read() if "family" in r}
    todo = [h for h in HISTORY if h[0] not in existing]

    if args.dry_run or not args.seed:
        print(json.dumps({
            "already_registered": len(existing), "would_add": len(todo),
            "resulting_trial_count": len(existing) + len(todo),
            "bar_now": round(TR.expected_max_sharpe(max(len(existing), 2), 630), 3),
            "bar_after": round(TR.expected_max_sharpe(len(existing) + len(todo), 630), 3),
            "note": "bar = null-expected best Sharpe on a 2.5yr sample; run with --seed to apply",
        }, indent=2))
        return 0

    for fam, hyp, docs, verdict in todo:
        row = TR.register(family=fam, hypothesis=hyp,
                          universe="see doc " + docs, horizon="historical")
        TR.set_state(row["trial_id"], "REPORTED", note=f"doc {docs}: {verdict}")
    st = TR.status()
    print(json.dumps({"seeded": len(todo), "n_trials": st["n_trials"],
                      "expected_max_sharpe_under_null_2p5yr": round(
                          TR.expected_max_sharpe(st["n_trials"], 630), 3),
                      "expected_max_sharpe_under_null_10p5yr": round(
                          TR.expected_max_sharpe(st["n_trials"], 2646), 3),
                      "chain": st["chain"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
