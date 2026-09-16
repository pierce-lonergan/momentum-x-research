"""doc 207: Operator T1 action toolkit — live, reversible, governance-gated remediations.

Every action follows the same discipline (Pierce's rules):
  1. write the EXACT one-line rollback command FIRST (the reversibility forcing function),
  2. pass `governance.t1_allowed(...)` (armed + caps + confidence + reversibility + declared
     transition),
  3. `state_snapshot.snapshot()`,
  4. act,
  5. caller verifies it cleared the incident, then `governance.record_t1_outcome(success)`.

The flagship action HALTs new entries via the D277 LIVE file (`data/HALT_NEW_ENTRIES`,
checked each submission — instant, no restart). Flag-ladder reductions are restart-STAGED
(config loads at launch) and say so. Re-enabling anything is Pierce-only (not a T1).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.ops import operator_governance as gov
from src.ops import state_snapshot

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_HALT_FILE = _ROOT / "data" / "HALT_NEW_ENTRIES"


@dataclass
class ActionResult:
    ok: bool
    action: str
    detail: str = ""
    rollback_cmd: str = ""
    snapshot_id: str = ""
    applies: str = ""          # "LIVE (this session)" | "NEXT RESTART"
    gate_reason: str = ""
    blocked: bool = False      # True if governance refused (not an error, a refusal)
    extras: dict = field(default_factory=dict)


def halt_new_entries(reason: str, *, confidence: float, session_count: int,
                     daily_count: int) -> ActionResult:
    """T1 (flagship): refuse NEW entries immediately by dropping the D277 live halt file.
    Positions + their stops are untouched; only new-entry submission is refused. INSTANT
    (the bot checks the file each submission). Rollback: delete the file."""
    rollback = "del data\\HALT_NEW_ENTRIES   (or: rm data/HALT_NEW_ENTRIES)"
    allowed, why = gov.t1_allowed(
        flag="MOMENTUM_HALT_NEW_ENTRIES", current="0", proposed="1",
        confidence=confidence, rollback_cmd="rm data/HALT_NEW_ENTRIES",
        session_count=session_count, daily_count=daily_count)
    if not allowed:
        return ActionResult(False, "halt_new_entries", detail=why, blocked=True,
                            gate_reason=why, rollback_cmd=rollback)
    snap = state_snapshot.snapshot("T1-halt_new_entries")
    try:
        _HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _HALT_FILE.write_text(
            f"HALT by Operator: {reason}\n{datetime.now(timezone.utc).isoformat()}\n",
            encoding="utf-8")
        logger.warning("OPS T1 HALT_NEW_ENTRIES dropped: %s", reason)
        return ActionResult(True, "halt_new_entries", detail=reason,
                            rollback_cmd=rollback, snapshot_id=snap.get("id", ""),
                            applies="LIVE (this session — D277 file checked each submission)")
    except Exception as e:  # noqa: BLE001
        return ActionResult(False, "halt_new_entries", detail=f"error: {e}",
                            rollback_cmd=rollback, snapshot_id=snap.get("id", ""))


def clear_halt() -> ActionResult:
    """ROLLBACK / Pierce re-arm only (re-enabling entries is NOT a T1). Deletes the halt file."""
    try:
        existed = _HALT_FILE.exists()
        if existed:
            _HALT_FILE.unlink()
        return ActionResult(True, "clear_halt", detail=("removed" if existed else "not present"),
                            applies="LIVE (this session)")
    except Exception as e:  # noqa: BLE001
        return ActionResult(False, "clear_halt", detail=f"error: {e}")


def is_halted() -> bool:
    return _HALT_FILE.exists()


def stage_flag_safer(flag: str, current, proposed, *, reason: str, confidence: float,
                     session_count: int, daily_count: int,
                     secrets_path: Path | None = None) -> ActionResult:
    """T1 (restart-STAGED): step a flag DOWN its declared safety ladder by writing the new
    value to the secrets file (the launcher's source of truth). Takes effect at the NEXT
    restart (config loads at launch) — NOT live this session. Governance enforces that the
    transition is a declared step toward safety. Rollback: restore the prior value."""
    rollback = f"set {flag}={current} in the secrets/.env file"
    allowed, why = gov.t1_allowed(
        flag=flag, current=current, proposed=proposed, confidence=confidence,
        rollback_cmd=rollback, session_count=session_count, daily_count=daily_count)
    if not allowed:
        return ActionResult(False, "stage_flag_safer", detail=why, blocked=True,
                            gate_reason=why, rollback_cmd=rollback,
                            extras={"flag": flag, "proposed": proposed})
    snap = state_snapshot.snapshot(f"T1-stage_{flag}")
    sp = secrets_path or (Path.home() / "momentum-x-secrets.env")
    try:
        _upsert_env(sp, flag, str(proposed))
        logger.warning("OPS T1 stage_flag_safer %s=%s (restart-staged): %s", flag, proposed, reason)
        return ActionResult(True, "stage_flag_safer", detail=f"{flag}={proposed} staged ({reason})",
                            rollback_cmd=rollback, snapshot_id=snap.get("id", ""),
                            applies="NEXT RESTART (config loads at launch)",
                            extras={"flag": flag, "from": current, "to": proposed})
    except Exception as e:  # noqa: BLE001
        return ActionResult(False, "stage_flag_safer", detail=f"error: {e}",
                            rollback_cmd=rollback, snapshot_id=snap.get("id", ""))


def _upsert_env(path: Path, key: str, value: str) -> None:
    """Set KEY=value in a dotenv-style file (replace in place or append). Preserves comments."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, found = [], False
    for ln in lines:
        stripped = ln.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped \
                and stripped.split("=", 1)[0].strip() == key:
            out.append(f"{key}={value}   # set by Operator T1 {datetime.now(timezone.utc).date()}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{key}={value}   # added by Operator T1 {datetime.now(timezone.utc).date()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    # atomic write (tmp + replace) so a live reader of .env never sees a half-written file
    import os as _os
    tmp = path.with_suffix(path.suffix + ".optmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    _os.replace(tmp, path)
