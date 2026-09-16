"""DOC 289 STAGE B (ceiling only) — Stage A FAILED its pre-registered gate, so there is no validated edge to
size. But denominator-honesty demands we prove the *whisper* (AUC~0.61) cannot pay. This computes an
OPTIMISTIC UPPER BOUND: using the OOS BOTH-model score, bet the single top-scored name in each cohort and
credit it the most generous plausible capture, then compare to the transaction-cost floor for these
sub-$1/low-$ illiquid names. If even the optimistic bound sits below the floor, the whisper is unbankable and
the negative is airtight (a knowability-AND-unprofitability result).
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import importlib.util

_spec = importlib.util.spec_from_file_location("relmod", str(Path(__file__).resolve().parent / "_doc289_stageA_relational.py"))
R = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(R)
_specb = importlib.util.spec_from_file_location("robmod", str(Path(__file__).resolve().parent / "_doc289_stageA_robustness.py"))
# reuse grouped_proba from robustness
RB = importlib.util.module_from_spec(_specb); _specb.loader.exec_module(RB)

OUT = Path(__file__).resolve().parent.parent / "data" / "research" / "doc289"
COST_FLOOR = 0.015   # ~1.5% marketable round-trip for illiquid low-float gappers (spread+slippage+fees), conservative-LOW
CAPTURE = 0.40       # optimistic: capture 40% of the intraday peak run (you never sell the exact high)


def main():
    rows = R.load()
    sessions = [r["date"] for r in rows]
    y = np.array([r["is_top3"] for r in rows])   # the whisper's best target
    p = RB.grouped_proba(rows, R.ABS_FEATS + R.REL_FEATS, y, sessions, R.N_FOLDS, R.SEED)

    peak = np.array([r["peak_run"] for r in rows])
    rclose = np.array([r["ret_close"] for r in rows])
    sess = np.array(sessions)

    top1_peak, top1_close, rand_peak, rand_close, winner_peak = [], [], [], [], []
    rng = np.random.default_rng(7)
    for s in sorted(set(sessions)):
        idx = np.where(sess == s)[0]
        sc = p[idx]
        if np.isnan(sc).all():
            continue
        pick = idx[np.nanargmax(sc)]
        top1_peak.append(peak[pick]); top1_close.append(rclose[pick])
        r = idx[rng.integers(len(idx))]
        rand_peak.append(peak[r]); rand_close.append(rclose[r])
        winner_peak.append(peak[idx].max())

    def m(a): return float(np.mean(a))
    res = {
        "n_cohorts_scored": len(top1_peak),
        "cost_floor_roundtrip": COST_FLOOR, "optimistic_capture_of_peak": CAPTURE,
        # raw (pre-cost) means
        "top1_by_score__mean_peak_run": round(m(top1_peak), 4),
        "top1_by_score__mean_ret_close": round(m(top1_close), 4),
        "random_name__mean_peak_run": round(m(rand_peak), 4),
        "random_name__mean_ret_close": round(m(rand_close), 4),
        "actual_winner__mean_peak_run": round(m(winner_peak), 4),
        # MODEL-ATTRIBUTABLE optimistic bound: capture applies only to the EXCESS peak the score
        # actually selects over a random in-cohort name (you cannot bank the part a coin-flip also gets).
        "edge_vs_random_peak": round(m(top1_peak) - m(rand_peak), 4),
        "OPTIMISTIC_net_per_cohort": round(CAPTURE * (m(top1_peak) - m(rand_peak)) - COST_FLOOR, 4),
        "REALISTIC_net_per_cohort_hold_to_close": round(m(top1_close) - COST_FLOOR, 4),
        # the whisper's only real content: fade-avoidance vs a random name on the close
        "fade_avoidance_close_vs_random": round(m(top1_close) - m(rand_close), 4),
    }
    fillable_neg = res["REALISTIC_net_per_cohort_hold_to_close"] <= 0
    peak_edge_gone = res["OPTIMISTIC_net_per_cohort"] <= 0
    res["VERDICT"] = (
        "WHISPER UNBANKABLE — no peak-selection edge (top1 peak ~= random), and the fillable hold-to-close "
        "policy is net-negative after a conservative 1.5% cost floor; the score only mildly avoids faders, "
        "not enough to pay costs"
        if (fillable_neg and peak_edge_gone) else
        "residual positive bound — warrants a pre-registered forward money test")
    (OUT / "stageB_ceiling.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
