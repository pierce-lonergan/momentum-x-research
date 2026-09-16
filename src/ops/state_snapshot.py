"""doc 205: State snapshots + rollback — the Operator's safety net.

Pierce's requirement for full T0-T1 autonomy: "proper rollback mechanics through state
snapshots through git or some other mechanics." Before ANY T1 remediation or T2 code change,
the Operator takes a labeled snapshot:
  - records the git HEAD sha (code rollback = `git revert <sha>` — safe, inverse commit), and
  - copies the critical mutable state files (session_state.json, .env, recon_status.json)
    into data/ops/snapshots/<id>/ with a manifest.
`restore_state(id)` copies those files back. This makes every Operator action reversible.

Pure filesystem + git-sha capture; never raises into a caller that guards it.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_SNAP_DIR = _ROOT / "data" / "ops" / "snapshots"
# critical mutable state the Operator might touch (relative to repo root)
_STATE_FILES = ["data/session_state.json", ".env", "data/recon_status.json"]


def git_head() -> str:
    """Current HEAD sha (short). '' on any error."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(_ROOT),
            capture_output=True, text=True, timeout=15)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def git_dirty() -> bool:
    """True if the working tree has uncommitted changes (the live repo should be clean)."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(_ROOT),
            capture_output=True, text=True, timeout=15)
        return bool(out.stdout.strip())
    except Exception:
        return False


def snapshot(label: str, *, ts: datetime | None = None) -> dict:
    """Take a labeled restore point. Returns the manifest dict (with 'id'). Never raises."""
    try:
        t = ts or datetime.now(timezone.utc)
        sid = f"{t.strftime('%Y%m%dT%H%M%S')}_{_safe(label)}"
        d = _SNAP_DIR / sid
        d.mkdir(parents=True, exist_ok=True)
        copied = []
        for rel in _STATE_FILES:
            src = _ROOT / rel
            if src.exists():
                dst = d / rel.replace("/", "__")
                try:
                    shutil.copy2(src, dst)
                    copied.append(rel)
                except Exception:
                    pass
        manifest = {
            "id": sid, "label": label, "ts_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "git_head": git_head(), "git_dirty": git_dirty(),
            "files": copied, "dir": str(d),
        }
        (d / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        logger.info("OPS snapshot %s @ git=%s (%d files)", sid, manifest["git_head"], len(copied))
        return manifest
    except Exception as e:  # noqa: BLE001
        logger.warning("state_snapshot.snapshot failed for %s: %s", label, e)
        return {"id": "", "error": str(e)[:160]}


def restore_state(snapshot_id: str) -> bool:
    """Copy the snapshot's state files back into the repo. Returns True on success.

    NOTE: this restores STATE files only. CODE rollback is `git revert <git_head>` (an
    inverse commit) — documented in the operator runbook; we never `git reset --hard` a
    live repo automatically.
    """
    try:
        d = _SNAP_DIR / snapshot_id
        man_f = d / "manifest.json"
        if not man_f.exists():
            logger.warning("restore_state: no snapshot %s", snapshot_id)
            return False
        manifest = json.loads(man_f.read_text(encoding="utf-8"))
        for rel in manifest.get("files", []):
            src = d / rel.replace("/", "__")
            dst = _ROOT / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        logger.info("OPS restore_state %s -> %d files restored", snapshot_id,
                    len(manifest.get("files", [])))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("state_snapshot.restore failed for %s: %s", snapshot_id, e)
        return False


def list_snapshots(limit: int = 20) -> list[dict]:
    try:
        if not _SNAP_DIR.exists():
            return []
        mans = []
        for d in sorted(_SNAP_DIR.iterdir(), reverse=True)[:limit]:
            mf = d / "manifest.json"
            if mf.exists():
                try:
                    mans.append(json.loads(mf.read_text(encoding="utf-8")))
                except Exception:
                    pass
        return mans
    except Exception:
        return []


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(s))[:40]
