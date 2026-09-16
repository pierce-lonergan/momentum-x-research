"""doc 206: Operator self-governance — the safety rails Pierce's critique demanded.

Encodes the hard rules so the Operator's in-the-moment judgment can't rationalize past them:
  - DECLARED T1 transition ladders (no "lower to a safer value" by feel — the legal,
    one-directional-toward-safety transitions are a table).
  - session AND daily T1 caps.
  - a SOFT FUSE circuit-breaker on the Operator itself (consecutive failed T1s /
    unresolved escalations -> auto-downgrade to T0, require Pierce re-arm).
  - a CONFIDENCE gate (below threshold, T1 is unavailable regardless of allow-list).
  - the explicit REVERSIBILITY TEST (must write the exact one-line rollback command first).
  - the MECHANICAL T2 gate (dominate CURRENT PRODUCTION on a pre-registered metric set;
    not "best ever", not narrative "ties safely").
  - a repo write LOCK + in-flight registry (sessions are independent Opus instances).

Pure logic + small JSON state files under data/ops/. Never raises into a guarded caller.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_OPS = _ROOT / "data" / "ops"
_FUSE = _OPS / "operator_fuse.json"
_LOCK = _OPS / "repo.lock"
_IN_FLIGHT = _OPS / "in_flight.json"

# ── T1 allow-list: DECLARED, one-directional-toward-safety transitions ──
# Ladders run least-safe -> safest; the Operator may only step toward the safe end.
T1_LADDERS: dict[str, list] = {
    "EXEC_RISK_PER_TRADE_PCT": [0.02, 0.01, 0.0075, 0.005],
    "EXEC_MAX_POSITIONS": [8, 6, 4, 2, 0],
    "EXEC_DAILY_DRAWDOWN_LIMIT_PCT": [0.05, 0.04, 0.03, 0.02],  # tighter (lower) = safer
}
# Boolean/string flags the Operator may set ONLY to the declared safe value (re-enable = Pierce).
T1_SAFE_SET: dict[str, str] = {
    "MOMENTUM_HALT_NEW_ENTRIES": "1",            # may HALT; re-arm (0) is Pierce-only
    "EXEC_ELITE_SIZING_PRESS_ENABLED": "false",  # may disable a risky press
    "EXEC_FADE_SHORT_ENABLED": "false",
}

T1_SESSION_CAP = int(os.environ.get("OPS_T1_SESSION_CAP", "6"))
T1_DAILY_CAP = int(os.environ.get("OPS_T1_DAILY_CAP", "12"))
T1_MIN_CONFIDENCE = float(os.environ.get("OPS_T1_MIN_CONFIDENCE", "0.70"))
FUSE_MAX_FAILED_T1 = 3          # consecutive failed T1s -> trip
FUSE_MAX_UNRESOLVED_ESC = 2     # consecutive unresolved escalations -> trip
# T2 gate tolerances (pre-registered; reference = CURRENT PRODUCTION)
T2_PRIMARY_TOL = 0.0            # candidate must be >= production on the primary metric (no slack down)
T2_MAXDD_TOL = 0.0             # candidate max-drawdown must be <= production (no worse tail)
T2_HITRATE_TOL = 0.02          # hit-rate may dip at most 2pp


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except Exception:
        return default


def _write_json(p: Path, obj) -> None:
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning("governance write %s failed: %s", p.name, e)


# ── T1 legality ──

def legal_t1_transition(flag: str, current, proposed) -> bool:
    """True iff (flag, current->proposed) is a DECLARED step toward safety. No feel, no up."""
    try:
        if flag in T1_LADDERS:
            ladder = T1_LADDERS[flag]
            cur = _coerce(current, ladder)
            prop = _coerce(proposed, ladder)
            if cur not in ladder or prop not in ladder:
                return False
            return ladder.index(prop) > ladder.index(cur)  # strictly safer
        if flag in T1_SAFE_SET:
            safe = T1_SAFE_SET[flag]
            return str(proposed).lower() == safe.lower() and str(current).lower() != safe.lower()
        return False
    except Exception:
        return False


def _coerce(v, ladder):
    sample = ladder[0]
    try:
        return type(sample)(v)
    except Exception:
        return v


# ── confidence + reversibility gates ──

def confidence_ok(confidence: float | None) -> bool:
    return confidence is not None and confidence >= T1_MIN_CONFIDENCE


def reversibility_ok(rollback_cmd: str | None) -> bool:
    """Pierce's forcing function: a T1 is only T1 if the Operator can state the EXACT rollback
    command in one line. Empty / multi-line / vague -> not T1."""
    if not rollback_cmd:
        return False
    rc = rollback_cmd.strip()
    return 0 < len(rc) <= 200 and "\n" not in rc


# ── the soft fuse (Operator circuit breaker) ──

def fuse_state() -> dict:
    return _read_json(_FUSE, {"failed_t1_streak": 0, "unresolved_esc_streak": 0,
                              "tripped": False, "tripped_reason": None})


def is_t1_armed() -> tuple[bool, str]:
    """(armed, reason). T1 is armed only if env-enabled AND the soft fuse is not tripped."""
    if os.environ.get("OPS_OPERATOR_T1_ENABLED", "true").strip().lower() in ("false", "0", "no", "off"):
        return False, "OPS_OPERATOR_T1_ENABLED=off"
    f = fuse_state()
    if f.get("tripped"):
        # Pierce re-arms by setting OPS_OPERATOR_T1_REARM=1 (which clears the fuse on next check)
        if os.environ.get("OPS_OPERATOR_T1_REARM", "").strip() in ("1", "true", "yes"):
            reset_fuse()
            return True, "re-armed by Pierce"
        return False, f"soft fuse tripped: {f.get('tripped_reason')}"
    return True, "armed"


def record_t1_outcome(success: bool) -> dict:
    """After a T1 action: success=cleared the incident. 3 fails in a row trips the fuse."""
    f = fuse_state()
    if success:
        f["failed_t1_streak"] = 0
    else:
        f["failed_t1_streak"] = f.get("failed_t1_streak", 0) + 1
        if f["failed_t1_streak"] >= FUSE_MAX_FAILED_T1:
            f["tripped"] = True
            f["tripped_reason"] = f"{f['failed_t1_streak']} failed T1s in a row"
    _write_json(_FUSE, f)
    return f


def record_escalation(resolved: bool) -> dict:
    f = fuse_state()
    if resolved:
        f["unresolved_esc_streak"] = 0
    else:
        f["unresolved_esc_streak"] = f.get("unresolved_esc_streak", 0) + 1
        if f["unresolved_esc_streak"] >= FUSE_MAX_UNRESOLVED_ESC:
            f["tripped"] = True
            f["tripped_reason"] = f"{f['unresolved_esc_streak']} unresolved escalations"
    _write_json(_FUSE, f)
    return f


def reset_fuse() -> None:
    _write_json(_FUSE, {"failed_t1_streak": 0, "unresolved_esc_streak": 0,
                        "tripped": False, "tripped_reason": None})


# ── T1 rate caps (session + daily) ──

def t1_cap_ok(session_count: int, daily_count: int) -> tuple[bool, str]:
    if session_count >= T1_SESSION_CAP:
        return False, f"session T1 cap {T1_SESSION_CAP} reached"
    if daily_count >= T1_DAILY_CAP:
        return False, f"daily T1 cap {T1_DAILY_CAP} reached"
    return True, "ok"


def t1_allowed(*, flag, current, proposed, confidence, rollback_cmd,
               session_count, daily_count) -> tuple[bool, str]:
    """The single gate every T1 must pass. ALL conditions required."""
    armed, why = is_t1_armed()
    if not armed:
        return False, why
    ok, why = t1_cap_ok(session_count, daily_count)
    if not ok:
        return False, why
    if not confidence_ok(confidence):
        return False, f"confidence {confidence} < {T1_MIN_CONFIDENCE}"
    if not reversibility_ok(rollback_cmd):
        return False, "no one-line rollback command -> not T1"
    if not legal_t1_transition(flag, current, proposed):
        return False, f"{flag}: {current}->{proposed} is not a declared safer transition"
    return True, "T1 authorized"


# ── the mechanical T2 gate (reference = CURRENT PRODUCTION) ──

def t2_gate(production: dict, candidate: dict, tests_pass: bool) -> tuple[bool, str]:
    """Ship a code change ONLY if tests pass AND it DOMINATES current production on the
    pre-registered metric set (no narrative 'ties safely'). Metrics expected (higher=better
    except max_drawdown): primary (e.g. net_pnl or sel_win), hit_rate, max_drawdown."""
    if not tests_pass:
        return False, "tests not green"
    p_primary = production.get("primary"); c_primary = candidate.get("primary")
    if p_primary is not None and c_primary is not None and c_primary < p_primary - T2_PRIMARY_TOL:
        return False, f"primary {c_primary} < production {p_primary}"
    p_dd = production.get("max_drawdown"); c_dd = candidate.get("max_drawdown")
    if p_dd is not None and c_dd is not None and c_dd > p_dd + T2_MAXDD_TOL:
        return False, f"max_drawdown {c_dd} worse than production {p_dd}"
    p_hr = production.get("hit_rate"); c_hr = candidate.get("hit_rate")
    if p_hr is not None and c_hr is not None and c_hr < p_hr - T2_HITRATE_TOL:
        return False, f"hit_rate {c_hr} below floor (prod {p_hr} - {T2_HITRATE_TOL})"
    return True, "dominates production -> ship at next restart"


# ── repo write lock + in-flight registry (session coordination) ──

def acquire_repo_lock(session_id: str, *, stale_sec: int = 1800) -> bool:
    """Atomic-ish exclusive lock so two concurrent Operator sessions don't both write the
    repo. Breaks a stale lock older than stale_sec."""
    try:
        _OPS.mkdir(parents=True, exist_ok=True)
        if _LOCK.exists():
            cur = _read_json(_LOCK, {})
            ts = cur.get("ts")
            age = None
            if ts:
                try:
                    age = (_now() - datetime.fromisoformat(ts)).total_seconds()
                except Exception:
                    age = None
            if age is not None and age < stale_sec and cur.get("session_id") != session_id:
                return False  # another live session owns it
        _write_json(_LOCK, {"session_id": session_id, "ts": _now().isoformat()})
        return _read_json(_LOCK, {}).get("session_id") == session_id
    except Exception:
        return False


def release_repo_lock(session_id: str) -> None:
    try:
        if _LOCK.exists() and _read_json(_LOCK, {}).get("session_id") == session_id:
            _LOCK.unlink()
    except Exception:
        pass


def read_in_flight() -> dict:
    return _read_json(_IN_FLIGHT, {"t2": []})


def set_in_flight(t2_items: list[dict]) -> None:
    _write_json(_IN_FLIGHT, {"t2": t2_items, "updated": _now().isoformat()})
