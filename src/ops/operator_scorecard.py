"""doc 205/206: The Operator GAME — v2, Goodhart-hardened (per Pierce's critique).

v1 ('incidents triaged + P&L saved + regressions + clean-close - MTTR - false-positives')
is a maximization target with classic Goodhart failure modes: it rewards inflated incident
counts, post-hoc unfalsifiable P&L attribution, and ACTIVITY on quiet days (the Operator
would learn to FIND work to justify itself). v2 fixes it:

  HYGIENE (the ONLY thing optimized hard):
    + correct RESTRAINT       -- a session that correctly does NOTHING scores HIGH (the
                                 anti-activity forcing function)
    + appropriate action      -- action taken AND verified to clear the incident
    - unnecessary action      -- action taken when no-op was correct (the activity tax)
    - false positive          -- raised/diagnosed a non-real incident, or a harmful action
    - MTTR, ONLY when warranted -- speed matters only when action was actually needed

  VALUE (reported, CAPPED, NOT the optimization target):
    + P&L saved   -- counts ONLY with a logged counterfactual (data/ops/counterfactuals)
    + regression prevented -- counts ONLY when the mechanical T2 gate verified a real one

  CALIBRATION (reported, not scored): stated confidence vs retrospective outcome -- the
  meta-anti-selection check (is the Operator, like the trade-layer LLMs at -0.86, most
  confident when most wrong?).

"incidents triaged" is NO LONGER a positive. The benchmark is the Operator's own rolling
best, but because restraint scores high, quiet days are GOOD days -- removing the monotonic
activity pressure v1 had.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_LEDGER = _ROOT / "data" / "ops" / "operator_leaderboard.jsonl"

# weights — transparent linear form; HYGIENE is the optimization target, VALUE is capped.
W = {
    "correct_noop": 25.0,            # headline: correctly doing nothing
    "appropriate_action": 10.0,      # action taken AND verified to clear the incident
    "unnecessary_action": -20.0,     # action when no-op was correct (Goodhart activity tax)
    "false_positive": -25.0,         # non-real incident / harmful action
    "mttr_min_when_warranted": -0.3, # per minute, only when action was warranted
    "pnl_saved_per_100usd": 1.0,     # value side (counterfactual-gated)
    "regression_prevented": 25.0,    # value side (gate-verified)
    "value_cap": 60.0,               # value can NEVER dominate hygiene
}


@dataclass
class OperatorMetrics:
    session_date: str
    correct_noops: int = 0            # incidents/sessions where no-op was the correct call
    appropriate_actions: int = 0      # action taken AND verified to clear
    unnecessary_actions: int = 0      # action when no-op was correct
    false_positives: int = 0          # non-real incident or harmful action
    mttr_min_warranted: float = 0.0   # MTTR counted ONLY for warranted actions
    # value side — each REQUIRES its gate to count
    pnl_saved_est: float = 0.0
    counterfactual_logged: bool = False     # gate for pnl_saved
    regressions_prevented_verified: int = 0  # gate = the mechanical T2 gate verified it
    # calibration / meta (reported, not scored)
    mean_confidence: float | None = None
    confidence_outcome_corr: float | None = None
    notes: str = ""
    badges: list[str] = field(default_factory=list)


def compute_score(m: OperatorMetrics) -> dict:
    """Return {score, hygiene, value, breakdown}. Hygiene is optimized; value is capped+gated."""
    hygiene_b = {
        "correct_noop": round(W["correct_noop"] * m.correct_noops, 1),
        "appropriate_action": round(W["appropriate_action"] * m.appropriate_actions, 1),
        "unnecessary_action": round(W["unnecessary_action"] * m.unnecessary_actions, 1),
        "false_positive": round(W["false_positive"] * m.false_positives, 1),
        "mttr": round(W["mttr_min_when_warranted"] * m.mttr_min_warranted, 1),
    }
    hygiene = round(sum(hygiene_b.values()), 1)
    # value side — gated
    value_raw = 0.0
    if m.counterfactual_logged:
        value_raw += W["pnl_saved_per_100usd"] * (m.pnl_saved_est / 100.0)
    value_raw += W["regression_prevented"] * m.regressions_prevented_verified
    value = round(min(value_raw, W["value_cap"]), 1)
    return {"score": round(hygiene + value, 1), "hygiene": hygiene, "value": value,
            "breakdown": {**hygiene_b, "value_gated": value}}


def _load() -> list[dict]:
    if not _LEDGER.exists():
        return []
    rows = []
    for line in _LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def record_session(m: OperatorMetrics) -> dict:
    try:
        scored = compute_score(m)
        row = {
            "session_date": m.session_date, "score": scored["score"],
            "hygiene": scored["hygiene"], "value": scored["value"],
            "breakdown": scored["breakdown"],
            "correct_noops": m.correct_noops, "appropriate_actions": m.appropriate_actions,
            "unnecessary_actions": m.unnecessary_actions, "false_positives": m.false_positives,
            "mttr_min_warranted": round(m.mttr_min_warranted, 1),
            "pnl_saved_est": round(m.pnl_saved_est, 2),
            "counterfactual_logged": m.counterfactual_logged,
            "regressions_prevented_verified": m.regressions_prevented_verified,
            "mean_confidence": m.mean_confidence,
            "confidence_outcome_corr": m.confidence_outcome_corr,
            "badges": list(m.badges), "notes": m.notes,
        }
        rows = [r for r in _load() if r.get("session_date") != m.session_date]
        rows.append(row)
        rows.sort(key=lambda r: r.get("session_date", ""))
        _LEDGER.parent.mkdir(parents=True, exist_ok=True)
        _LEDGER.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return {**row, "standings": standings(m.session_date)}
    except Exception as e:  # noqa: BLE001
        logger.warning("operator_scorecard.record failed: %s", e)
        return {"score": 0.0, "error": str(e)[:160]}


def standings(today_date: str | None = None) -> dict:
    rows = _load()
    if not rows:
        return {"today": None, "best": None, "streak": 0}
    scores = {r["session_date"]: r["score"] for r in rows}
    best_date = max(scores, key=lambda d: scores[d])
    today = scores.get(today_date) if today_date else rows[-1]["score"]
    prior = {d: s for d, s in scores.items() if d != today_date}
    prior_best = max(prior.values()) if prior else None
    # restraint streak: consecutive trailing sessions with no unnecessary actions + no FPs
    streak = 0
    for r in reversed(rows):
        if r.get("unnecessary_actions", 0) == 0 and r.get("false_positives", 0) == 0:
            streak += 1
        else:
            break
    return {
        "today": today, "best": scores[best_date], "best_date": best_date,
        "prior_best": prior_best, "clean_streak": streak,
        "beat_best": (today is not None and prior_best is not None and today > prior_best),
    }


def game_line(today_date: str | None = None) -> str:
    s = standings(today_date)
    if s.get("today") is None:
        return "Operator: no score yet — first session pending."
    beat = " 🏆 NEW BEST!" if s.get("beat_best") else ""
    pb = f" · best {s['prior_best']:.0f}" if s.get("prior_best") is not None else ""
    return (f"Operator today: {s['today']:.0f}{pb} · clean (no unnecessary actions) streak "
            f"{s.get('clean_streak', 0)}{beat} — restraint is the high score.")
