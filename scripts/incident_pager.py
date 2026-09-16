"""doc 272 A2 — INCIDENT PAGER (DORMANT-C, never raises out, posts nothing unless ARMED).

WHY: on 2026-06-09 a position sat naked at the broker for 5.8 HOURS while the recon
daemon logged "[SHADOW] D232 RECON_LETHAL ... WOULD flat all + halt" ~700 times and
src/ops/incident_bus.py had NO live consumer (Operator disabled since 6/2). This script
is the missing consumer: it tails data/ops/incidents_<date>.jsonl and pages NEW CRITICAL
(and WARN with --warn) incidents to the OPS_ALERT Discord channel within ~60s.

SAFETY MODEL
  - Master flag: env INCIDENT_PAGER_ARMED. Unset/0/false => DRY mode: logs exactly what
    it WOULD post, posts NOTHING. Arming = Pierce adds INCIDENT_PAGER_ARMED=1 to
    ~/momentum-x-secrets.env (or the env of the scheduled task).
  - Synthetic exclusion: rows with ticker in src/ops/synthetics.SYNTHETIC_TICKERS
    ({X, TEST, AAPL, TSLA}) or a synthetic:true field never page (the nightly Adversary
    battle emits CRITICALs on ticker X by design).
  - Dedup: one page per (severity, kind, ticker) per --dedup-window-sec (default 3600s),
    persisted across restarts in the state file. The 2,824-alerts class is the enemy.
  - Rate cap: max 10 posts per pass; overflow is summarized on the 10th post and
    dedup-marked (a 50-incident storm = 10 pages, not 50).
  - Cursor: byte offset per incidents file in data/ops/incident_pager_state.json so
    restarts never re-page history. Offset advances only after a pass whose armed posts
    all succeeded (permanent 4xx counts as delivered; 5xx/429/network holds the cursor
    so the page retries next pass — already-paged keys are dedup-suppressed).
  - Date rollover: each pass scans yesterday's AND today's files (NY dates), so
    incidents written seconds before midnight are not orphaned.

MODES
  python scripts/incident_pager.py                 # loop forever, pass every 20s
  python scripts/incident_pager.py --once          # single pass (rides run_shadow_grader.ps1 @19:30)
  python scripts/incident_pager.py --once --warn   # also page WARN severity
  python scripts/incident_pager.py --self-test     # inject synthetic CRITICAL, prove
                                                   # post-path + cursor + dedup, exit 0/1

PIERCE'S CHECKLIST — true real-time paging (documented, NOT registered):
  The 19:30 ride-along is once a day. For ~60s-latency paging during sessions, register
  a 5-minute loop task (the --once pass is idempotent and cheap):
    schtasks /Create /TN "MomentumX-IncidentPager" /SC MINUTE /MO 5 ^
      /TR "C:\\Python313\\python.exe \"<local-path> operator\\Documents\\GitHub\\momentum-x\\scripts\\incident_pager.py\" --once" ^
      /ST 04:00 /F
  (or run `python scripts/incident_pager.py` under the watchdog for a persistent 20s loop).
  Then add INCIDENT_PAGER_ARMED=1 to ~/momentum-x-secrets.env to arm.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from src.ops.synthetics import SYNTHETIC_TICKERS, is_synthetic_incident
except Exception:  # noqa: BLE001 — DORMANT-C: a broken import must not kill paging
    SYNTHETIC_TICKERS = frozenset({"X", "TEST", "AAPL", "TSLA"})

    def is_synthetic_incident(row: dict) -> bool:  # type: ignore[misc]
        try:
            t = str(row.get("ticker") or "").upper()
            ctx = row.get("context")
            return (t in SYNTHETIC_TICKERS or row.get("synthetic") is True
                    or (isinstance(ctx, dict) and ctx.get("synthetic") is True))
        except Exception:
            return False

_NY = ZoneInfo("America/New_York")
SELF_TEST_KIND = "PAGER_SELF_TEST"
MAX_POSTS_PER_PASS = 10
DEFAULT_DEDUP_WINDOW_SEC = 3600.0
DEFAULT_LOOP_INTERVAL_SEC = 20.0
_STATE_KEEP_FILES = 5  # prune per-file offsets older than the newest N files


def _log(msg: str) -> None:
    """Console log that NEVER raises — Windows cp1252 consoles choke on emoji
    (PYTHONIOENCODING=utf-8 is set by the ps1 launcher but not guaranteed)."""
    line = f"[incident_pager {datetime.now(timezone.utc).strftime('%H:%M:%SZ')}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        try:
            print(line.encode(enc, errors="replace").decode(enc, errors="replace"),
                  flush=True)
        except Exception:  # noqa: silent-handler — the logger itself; nowhere left to log
            pass
    except Exception:  # noqa: silent-handler — the logger itself; nowhere left to log
        pass


def _armed() -> bool:
    return os.environ.get("INCIDENT_PAGER_ARMED", "").strip().lower() in (
        "1", "true", "yes", "on")


def _resolve_webhook() -> str | None:
    """OPS_ALERT_WEBHOOK_URL from env, else ~/momentum-x-secrets.env, else <root>/.env."""
    wh = os.environ.get("OPS_ALERT_WEBHOOK_URL", "").strip()
    if wh:
        return wh
    for f in (Path.home() / "momentum-x-secrets.env", _ROOT / ".env"):
        try:
            if not f.exists():
                continue
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("OPS_ALERT_WEBHOOK_URL="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
        except Exception:  # noqa: silent-handler — unreadable env file: try next source
            continue
    return None


# ── state ──────────────────────────────────────────────────────────


def _load_state(state_file: Path) -> dict:
    try:
        if state_file.exists():
            d = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                d.setdefault("files", {})
                d.setdefault("dedup", {})
                return d
    except Exception as e:  # noqa: BLE001
        _log(f"state load failed ({e}) — starting from EMPTY state: offsets reset to 0, "
             f"so old rows re-read; the dedup window is the only re-page guard. "
             f"Inspect {state_file}.")
    return {"files": {}, "dedup": {}}


def _save_state(state_file: Path, state: dict, dedup_window_sec: float) -> None:
    """Atomic write; prunes stale dedup keys + old file offsets. Never raises."""
    try:
        now = time.time()
        state["dedup"] = {k: v for k, v in state.get("dedup", {}).items()
                          if isinstance(v, (int, float)) and now - v < dedup_window_sec * 2}
        files = state.get("files", {})
        if len(files) > _STATE_KEEP_FILES:
            for k in sorted(files)[:-_STATE_KEEP_FILES]:
                files.pop(k, None)
        state["last_run_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["last_run_mode"] = "ARMED" if _armed() else "DRY"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        os.replace(tmp, state_file)
    except Exception as e:  # noqa: BLE001
        _log(f"state save failed (non-fatal, may re-page next run): {e}")


# ── tail + parse ───────────────────────────────────────────────────


def _read_new_rows(path: Path, offset: int) -> tuple[list[dict], int]:
    """Read complete JSONL lines from byte `offset`. Returns (rows, new_offset).
    A trailing partial line (no \\n yet — writer mid-append) is left unconsumed."""
    try:
        size = path.stat().st_size
    except OSError:
        return [], offset
    if offset > size:  # truncated/replaced — start over
        _log(f"{path.name}: stored offset {offset} > size {size} — file replaced; reset to 0")
        offset = 0
    if size == offset:
        return [], offset
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            blob = f.read(size - offset)
    except OSError as e:
        _log(f"{path.name}: read failed ({e})")
        return [], offset
    last_nl = blob.rfind(b"\n")
    if last_nl < 0:
        return [], offset  # only a partial line so far
    consumed = blob[: last_nl + 1]
    new_offset = offset + len(consumed)
    rows: list[dict] = []
    for raw in consumed.decode("utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
            if isinstance(row, dict):
                rows.append(row)
        except Exception:  # noqa: silent-handler — poison JSONL line: skip by contract
            continue
    return rows, new_offset


def _incident_files(ops_dir: Path, date_override: str | None) -> list[Path]:
    if date_override:
        return [ops_dir / f"incidents_{date_override}.jsonl"]
    today = datetime.now(timezone.utc).astimezone(_NY).date()
    return [ops_dir / f"incidents_{(today - timedelta(days=1)).isoformat()}.jsonl",
            ops_dir / f"incidents_{today.isoformat()}.jsonl"]


# ── selection + formatting ─────────────────────────────────────────


def _wanted(row: dict, include_warn: bool, self_test: bool) -> tuple[bool, str]:
    """(should_page, reason_if_not)."""
    sev = str(row.get("severity", "")).upper()
    if sev not in (("CRITICAL", "WARN") if include_warn else ("CRITICAL",)):
        return False, f"severity={sev or '?'}"
    if is_synthetic_incident(row):
        # self-test rows bypass ONLY in --self-test mode (clearly marked downstream)
        if self_test and row.get("kind") == SELF_TEST_KIND:
            return True, ""
        return False, "synthetic"
    return True, ""


def _dedup_key(row: dict) -> str:
    return (f"{str(row.get('severity', '')).upper()}"
            f"|{row.get('kind', '?')}|{str(row.get('ticker') or '-').upper()}")


def _format_message(row: dict, self_test: bool) -> str:
    sev = str(row.get("severity", "")).upper()
    kind = row.get("kind", "?")
    ticker = row.get("ticker") or "-"
    ts = row.get("ts_utc", "?")
    emoji = "\U0001f6a8" if sev == "CRITICAL" else "⚠️"
    ctx = row.get("context") or {}
    try:
        ctx_s = json.dumps(ctx, default=str)[:300]
    except Exception:
        ctx_s = str(ctx)[:300]
    suggested = row.get("suggested") or []
    sug = f"\nsuggested: {suggested[0]}" if suggested else ""
    prefix = "[SELF-TEST] " if (self_test and kind == SELF_TEST_KIND) else ""
    return (f"{prefix}{emoji} **[{sev}] {kind}** `{ticker}` at {ts}\n"
            f"context: `{ctx_s}`{sug}\n"
            f"(incident `{row.get('id', '?')}` — incident_pager doc272)")


def _post_discord(webhook: str, content: str, *, mention: bool) -> str:
    """POST one message. Returns 'ok' | 'permanent' (4xx, don't retry) | 'retry'."""
    body = {"content": (("@here " if mention else "") + content)[:1900]}
    try:
        req = urllib.request.Request(
            webhook, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json",
                     "User-Agent": "MomentumX-IncidentPager/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if 200 <= resp.status < 300:
                return "ok"
            return "retry"
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        if e.code == 429 or e.code >= 500:
            _log(f"discord HTTP {e.code} — will retry next pass")
            return "retry"
        _log(f"discord HTTP {e.code} — permanent, treating as delivered (won't wedge cursor)")
        return "permanent"
    except Exception as e:  # noqa: BLE001 — network down etc.
        _log(f"discord post failed ({e}) — will retry next pass")
        return "retry"


# ── one pass ───────────────────────────────────────────────────────


def run_pass(*, ops_dir: Path, state_file: Path, include_warn: bool,
             dedup_window_sec: float, self_test: bool = False,
             date_override: str | None = None) -> dict:
    """One tail-and-page pass. Returns a summary dict. Never raises."""
    summary = {"scanned": 0, "candidates": 0, "posted": 0, "would_post": 0,
               "deduped": 0, "excluded_synthetic": 0, "capped": 0, "post_failures": 0,
               "cursor_advanced": False, "armed": _armed()}
    try:
        state = _load_state(state_file)
        dedup: dict[str, float] = state.get("dedup", {})
        webhook = _resolve_webhook()
        armed = summary["armed"]
        if armed and not webhook:
            _log("ARMED but OPS_ALERT_WEBHOOK_URL unresolved — falling back to DRY this pass")
            armed = False
            summary["armed"] = False

        to_page: list[dict] = []
        new_offsets: dict[str, int] = {}
        now = time.time()
        for f in _incident_files(ops_dir, date_override):
            prev = int(state.get("files", {}).get(f.name, {}).get("offset", 0) or 0)
            rows, new_off = _read_new_rows(f, prev)
            new_offsets[f.name] = new_off
            summary["scanned"] += len(rows)
            for row in rows:
                ok, why = _wanted(row, include_warn, self_test)
                if not ok:
                    if why == "synthetic":
                        summary["excluded_synthetic"] += 1
                    continue
                key = _dedup_key(row)
                last = dedup.get(key)
                if isinstance(last, (int, float)) and now - last < dedup_window_sec:
                    summary["deduped"] += 1
                    continue
                # collapse duplicates WITHIN this pass too
                if any(_dedup_key(r) == key for r in to_page):
                    summary["deduped"] += 1
                    continue
                to_page.append(row)
        summary["candidates"] = len(to_page)

        capped = to_page[MAX_POSTS_PER_PASS:]
        to_page = to_page[:MAX_POSTS_PER_PASS]
        summary["capped"] = len(capped)

        failures = 0
        for i, row in enumerate(to_page):
            msg = _format_message(row, self_test)
            if capped and i == len(to_page) - 1:
                msg += f"\n(+{len(capped)} more incidents suppressed by the 10/run rate cap)"
            if armed:
                status = _post_discord(
                    webhook, msg,
                    mention=(i == 0 and str(row.get("severity", "")).upper() == "CRITICAL"))
                if status == "retry":
                    failures += 1
                    continue  # not dedup-marked; cursor will hold; retried next pass
                summary["posted"] += 1
            else:
                _log(f"[DRY] WOULD post -> OPS_ALERT: {msg.splitlines()[0]}")
                summary["would_post"] += 1
            dedup[_dedup_key(row)] = now
        for row in capped:  # summarized on the last post — never paged individually
            dedup[_dedup_key(row)] = now
        summary["post_failures"] = failures

        # cursor: advance only when nothing needs a retry (DRY never needs retries)
        if failures == 0:
            for name, off in new_offsets.items():
                state.setdefault("files", {}).setdefault(name, {})["offset"] = off
            summary["cursor_advanced"] = True
        else:
            _log(f"{failures} post(s) need retry — holding cursor (dedup map prevents "
                 f"re-paging the {summary['posted']} delivered)")
        state["dedup"] = dedup
        _save_state(state_file, state, dedup_window_sec)
    except Exception as e:  # noqa: BLE001 — DORMANT-C: never raises
        _log(f"PASS FAILED (swallowed, never-raises): {e!r}")
    return summary


# ── self-test ──────────────────────────────────────────────────────


def _inject_self_test_incident(ops_dir: Path, marker: str) -> Path:
    """Direct append matching the incident_bus schema (avoids the WAKE-sentinel side
    effect of a CRITICAL emit_incident and the bus's in-process dedup) + the top-level
    synthetic:true field. Schema mirrors src/ops/incident_bus.py:emit_incident."""
    ts = datetime.now(timezone.utc)
    session_date = ts.astimezone(_NY).strftime("%Y-%m-%d")
    row = {
        "id": f"{ts.strftime('%Y%m%dT%H%M%S')}_{marker[:8]}_{SELF_TEST_KIND}",
        "kind": SELF_TEST_KIND, "severity": "CRITICAL", "ticker": "TEST",
        "ts_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "session_date": session_date,
        "context": {"synthetic": True, "marker": marker,
                    "note": "doc272 pager self-test — ignore"},
        "suggested": ["no action — synthetic self-test row"],
        "resolved": False, "resolution": None, "synthetic": True,
    }
    ops_dir.mkdir(parents=True, exist_ok=True)
    f = ops_dir / f"incidents_{session_date}.jsonl"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return f


def run_self_test(*, ops_dir: Path, state_file: Path, dedup_window_sec: float) -> int:
    """Prove the pager from birth: inject -> page -> cursor -> dedup -> synthetic-guard.
    Returns 0 on PASS, 1 on FAIL. Posts to OPS_ALERT (prefixed [SELF-TEST]) ONLY if armed."""
    results: list[tuple[str, bool, str]] = []
    mode = "ARMED" if _armed() else "DRY"
    _log(f"SELF-TEST starting (mode={mode}, ops_dir={ops_dir})")

    # consume any backlog first so the assertions below see ONLY our injections
    pre = run_pass(ops_dir=ops_dir, state_file=state_file, include_warn=False,
                   dedup_window_sec=dedup_window_sec, self_test=False)
    _log(f"baseline pass (backlog consumed): {pre}")

    # forget any PREVIOUS self-test's dedup mark so this run proves dedup
    # within ITSELF (otherwise a re-run inside the window fails check 1)
    st0 = _load_state(state_file)
    if st0.get("dedup", {}).pop(f"CRITICAL|{SELF_TEST_KIND}|TEST", None) is not None:
        _save_state(state_file, st0, dedup_window_sec)
        _log("cleared stale self-test dedup key from a previous run")

    marker = f"st{int(time.time())}"
    inc_file = _inject_self_test_incident(ops_dir, marker)
    _log(f"injected synthetic CRITICAL #1 (ticker=TEST, synthetic:true) -> {inc_file.name}")

    s1 = run_pass(ops_dir=ops_dir, state_file=state_file, include_warn=False,
                  dedup_window_sec=dedup_window_sec, self_test=True)
    delivered = (s1["posted"] if _armed() else s1["would_post"])
    results.append(("post-path fires for injected CRITICAL",
                    delivered >= 1 and s1["post_failures"] == 0,
                    f"posted={s1['posted']} would_post={s1['would_post']} fail={s1['post_failures']}"))
    st = _load_state(state_file)
    off = int(st.get("files", {}).get(inc_file.name, {}).get("offset", 0) or 0)
    results.append(("cursor advanced to EOF after pass 1",
                    s1["cursor_advanced"] and off == inc_file.stat().st_size,
                    f"offset={off} size={inc_file.stat().st_size}"))

    _inject_self_test_incident(ops_dir, marker + "b")
    s2 = run_pass(ops_dir=ops_dir, state_file=state_file, include_warn=False,
                  dedup_window_sec=dedup_window_sec, self_test=True)
    results.append(("dedup suppresses identical (severity,kind,ticker) within window",
                    (s2["posted"] + s2["would_post"]) == 0 and s2["deduped"] >= 1,
                    f"posted={s2['posted']} would_post={s2['would_post']} deduped={s2['deduped']}"))
    st2 = _load_state(state_file)
    off2 = int(st2.get("files", {}).get(inc_file.name, {}).get("offset", 0) or 0)
    results.append(("cursor advanced again after pass 2 (no re-read of paged rows)",
                    off2 == inc_file.stat().st_size and off2 > 0, f"offset={off2}"))

    # the same row must be EXCLUDED outside self-test mode (synthetic guard)
    probe = {"severity": "CRITICAL", "kind": SELF_TEST_KIND, "ticker": "TEST",
             "synthetic": True, "context": {"synthetic": True}}
    ok, why = _wanted(probe, include_warn=False, self_test=False)
    results.append(("normal mode EXCLUDES the synthetic row (no human paged by tests)",
                    (not ok) and why == "synthetic", f"wanted={ok} reason={why}"))
    real_probe = {"severity": "CRITICAL", "kind": "RECON_LETHAL_SHADOW", "ticker": "APPS"}
    ok2, _ = _wanted(real_probe, include_warn=False, self_test=False)
    results.append(("normal mode PAGES a real CRITICAL (e.g. RECON_LETHAL_SHADOW APPS)",
                    ok2, "wanted=True expected"))

    n_pass = sum(1 for _, p, _ in results if p)
    for name, p, detail in results:
        _log(f"  {'PASS' if p else 'FAIL'} — {name} ({detail})")
    verdict = "PASS" if n_pass == len(results) else "FAIL"
    _log(f"SELF-TEST {verdict}: {n_pass}/{len(results)} checks (mode={mode}; "
         f"{'live [SELF-TEST] post(s) sent to OPS_ALERT' if _armed() else 'nothing posted — DRY'})")
    return 0 if verdict == "PASS" else 1


# ── entrypoint ─────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="doc272 incident pager (DORMANT-C)")
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    ap.add_argument("--warn", action="store_true", help="also page WARN severity")
    ap.add_argument("--self-test", action="store_true", dest="self_test")
    ap.add_argument("--date", default=None, help="override incidents date YYYY-MM-DD")
    ap.add_argument("--ops-dir", default=str(_ROOT / "data" / "ops"),
                    help="incident-bus directory (default <repo>/data/ops)")
    ap.add_argument("--state-file", default=None,
                    help="cursor/dedup state (default <ops-dir>/incident_pager_state.json)")
    ap.add_argument("--interval", type=float, default=DEFAULT_LOOP_INTERVAL_SEC)
    ap.add_argument("--dedup-window-sec", type=float, default=DEFAULT_DEDUP_WINDOW_SEC)
    a = ap.parse_args()

    ops_dir = Path(a.ops_dir)
    state_file = Path(a.state_file) if a.state_file else ops_dir / "incident_pager_state.json"
    mode = "ARMED" if _armed() else "DRY (set INCIDENT_PAGER_ARMED=1 to arm)"
    wh = _resolve_webhook()
    _log(f"mode={mode} | webhook={'resolved' if wh else 'NOT RESOLVED'} | ops_dir={ops_dir} "
         f"| dedup_window={a.dedup_window_sec:.0f}s | warn={'on' if a.warn else 'off'}")

    if a.self_test:
        return run_self_test(ops_dir=ops_dir, state_file=state_file,
                             dedup_window_sec=a.dedup_window_sec)

    if a.once:
        s = run_pass(ops_dir=ops_dir, state_file=state_file, include_warn=a.warn,
                     dedup_window_sec=a.dedup_window_sec, date_override=a.date)
        _log(f"pass summary: {s}")
        return 0

    _log(f"loop mode: pass every {a.interval:.0f}s (Ctrl+C to stop)")
    while True:
        s = run_pass(ops_dir=ops_dir, state_file=state_file, include_warn=a.warn,
                     dedup_window_sec=a.dedup_window_sec, date_override=a.date)
        if s["posted"] or s["would_post"] or s["post_failures"]:
            _log(f"pass summary: {s}")
        try:
            time.sleep(max(5.0, a.interval))
        except KeyboardInterrupt:
            _log("stopped by user")
            return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as _e:  # noqa: BLE001 — DORMANT-C: never crash the ride-along chain
        _log(f"FATAL swallowed (never-raises): {_e!r}")
        sys.exit(0)
