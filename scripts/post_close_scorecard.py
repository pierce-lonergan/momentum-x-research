#!/usr/bin/env python
"""doc 193: post-close measurement scorecard — make quantification a HABIT.

doc 192 proved SELECTION dominates fills ~30x and that our measurement was a one-off
(the doc-182 grader had NEVER been run). This runner closes that gap: after each session
it runs, per recent date, the fill-realism backtest (SELECTION win% + forward return +
the marketable fill edge) and the rejection grader (gate-correct%), and appends a
one-line SELECTION SCORECARD to data/reports/measurement_trend.jsonl.

THE HEADLINE METRIC is selection win% / forward return — that is the number to move
(everything else is 2nd order). Run after close, or wire into the launcher's Phase-4.

Usage:
    python scripts/post_close_scorecard.py                 # last 5 sessions w/ feature logs
    python scripts/post_close_scorecard.py 2026-05-29      # one date
    python scripts/post_close_scorecard.py --days 10
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"

# doc 287: the EOD launcher runs these sub-scripts with a cp1252 stdout (PYTHONIOENCODING unset), so a
# child that print()s a non-ASCII glyph dies with UnicodeEncodeError BEFORE it writes its output file.
# That silently killed posture_delta_trend for weeks (a leaderboard header prints U+0394 before the append).
# Force utf-8 on every EOD child so a print can never pre-empt a write.
_SUB_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}
_REPORTS = _ROOT / "data" / "reports"
_SHADOW = _ROOT / "data" / "shadow"
_FEATURES = _ROOT / "data" / "features"
_TREND = _REPORTS / "measurement_trend.jsonl"


def _recent_dates(n: int) -> list[str]:
    files = sorted(_FEATURES.glob("features_2026-*.jsonl"))
    return [f.stem.replace("features_", "") for f in files[-n:]]


def _run(cmd: list[str]):
    try:
        return subprocess.run(cmd, cwd=str(_ROOT), capture_output=True, text=True, timeout=900)
    except Exception as e:  # noqa: BLE001 — a measurement helper must never crash the runner
        print(f"  (subprocess failed: {e})")
        return None


def _fill_backtest(date: str, exit_min: int, max_n: int) -> dict | None:
    out = _REPORTS / f"fill_backtest_{date}.json"
    _REPORTS.mkdir(parents=True, exist_ok=True)
    _run([sys.executable, str(_SCRIPTS / "fill_model_backtest.py"), date,
          "--exit-min", str(exit_min), "--max", str(max_n), "--json", str(out)])
    if out.exists():
        try:
            return json.loads(out.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _grade(date: str) -> tuple[int, float | None]:
    """Best-effort rejection grading -> (n_graded, correct_pct) via the doc-182 grader."""
    _run([sys.executable, str(_SCRIPTS / "finalize_rejection_outcomes.py"), date])
    gf = _SHADOW / f"rejection_outcomes_graded_{date}.jsonl"
    if not gf.exists():
        return 0, None
    graded = []
    for line in gf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            if r.get("outcome", {}).get("filled"):
                graded.append(r)
        except Exception:
            pass
    if not graded:
        return 0, None
    correct = sum(1 for r in graded if r["outcome"].get("verdict") == "block_CORRECT_faded")
    return len(graded), round(100 * correct / len(graded), 1)


def _edge_at(summary: dict, w: int) -> float | None:
    for row in summary.get("windows", []):
        if row.get("w") == w:
            return row.get("edge")
    return None


def _upsert_trend(row: dict) -> None:
    """Idempotent: replace the date's row if present, keep the file date-sorted."""
    rows = []
    if _TREND.exists():
        for line in _TREND.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("date") != row["date"]:
                    rows.append(r)
            except Exception:
                pass
    rows.append(row)
    rows.sort(key=lambda r: r.get("date", ""))
    _TREND.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=None)
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--exit-min", type=int, default=60)
    ap.add_argument("--max", type=int, default=150)
    args = ap.parse_args()

    dates = [args.date] if args.date else _recent_dates(args.days)
    if not dates:
        print("No feature logs found."); return 1

    print(f"\n{'date':>11} | {'n':>4} | {'selWin%':>7} | {'selMed':>7} | "
          f"{'netReal':>8} | {'netEdge':>7} | {'gate':>12}")
    print("  " + "-" * 70)
    for date in dates:
        s = _fill_backtest(date, args.exit_min, args.max)
        if not s or s.get("n", 0) == 0:
            print(f"{date:>11} |  --   (no feature/bar data)")
            continue
        gate_n, gate_pct = _grade(date)
        edge2 = _edge_at(s, 2)
        net = s.get("net_pnl") or {}
        models = net.get("models") or {}
        # prefer the runner-aware tranche model (doc 196d); fall back to d122 single-exit
        m = models.get("tranche") or models.get("d122") or {}
        net_model = "tranche" if models.get("tranche") else ("d122" if models.get("d122") else None)
        sel_med = s.get("selection_median_ret")
        net_real = m.get("marketable_realized_mean")  # realized P&L of the names we'd hold
        net_edge = m.get("net_edge_per_candidate")    # marketable vs passive, net
        _upsert_trend({
            "date": date, "n": s["n"],
            "selection_win_rate": s["selection_win_rate"],
            "selection_median_ret": sel_med,
            "selection_mean_ret": s["selection_mean_ret"],
            "fill_edge_2min_gross": edge2,
            "net_model": net_model,
            "net_marketable_realized": net_real,
            "net_edge_per_candidate": net_edge,
            "gate_graded_n": gate_n, "gate_correct_pct": gate_pct,
        })
        gate_str = f"{gate_pct:.0f}% (n={gate_n})" if gate_pct is not None else f"n={gate_n}"
        med_str = f"{sel_med:+.2%}" if sel_med is not None else "n/a"
        real_str = f"{net_real:+.2%}" if net_real is not None else "n/a"
        edge_str = f"{net_edge:+.2%}" if net_edge is not None else "n/a"
        print(f"{date:>11} | {s['n']:>4} | {100*s['selection_win_rate']:>6.0f}% | {med_str:>7} | "
              f"{real_str:>8} | {edge_str:>7} | {gate_str:>12}")

    # doc 199 (g): weekly selection study on Fridays (auto, best-effort). The 188/191/184
    # selection signals get re-validated as data accrues — the AUC table hardens weekly.
    def _is_friday(d):
        try:
            return datetime.strptime(d, "%Y-%m-%d").weekday() == 4
        except Exception:
            return False
    fridays = [d for d in dates if _is_friday(d)]
    if fridays and os.environ.get("MX_WEEKLY_SELECTION_STUDY", "1") != "0":
        wd = fridays[-1]
        out = _REPORTS / f"selection_study_{wd}.json"
        print(f"\n  [weekly] Friday {wd} -> selection study (--days 5) -> {out.name}")
        _run([sys.executable, str(_SCRIPTS / "selection_study.py"), "--days", "5", "--json", str(out)])

    # doc 219(a): battle the REAL execution code against the Adversary every session. Any
    # WRAPPER bug (a regression in the close path) -> a CRITICAL incident the Operator triages.
    # Architectural flags (EOD-timing) are expected and don't alarm. Cheap, deterministic,
    # can't be overfit (doc 217); continuous robustness of the layer that bleeds real money.
    if os.environ.get("MX_ADVERSARY_BATTLE", "1") != "0":
        adv_out = _REPORTS / "adversary_report.json"
        r = _run([sys.executable, str(_SCRIPTS / "adversary_run.py"), "--json", str(adv_out)])
        try:
            rep = json.loads(adv_out.read_text(encoding="utf-8")) if adv_out.exists() else {}
            wbugs = rep.get("wrapper_bugs", [])
            survived, total = rep.get("survived", "?"), rep.get("total", "?")
            print(f"\n  [adversary] execution battle: survived {survived}/{total} | "
                  f"wrapper bugs: {wbugs or '0 (clean)'} | arch flags: {rep.get('architectural_flags', [])}")
            if wbugs:
                # a wrapper bug = the close code regressed and can lose money -> CRITICAL
                from src.ops.incident_bus import emit_incident as _emit
                _emit("ADVERSARY_WRAPPER_BUG", "CRITICAL",
                      context={"wrapper_bugs": wbugs, "survived": f"{survived}/{total}"},
                      suggested=["the execution close path regressed -- a scenario now goes "
                                 "naked/phantom/strands; read docs/research-log/216/218/219 + the report",
                                 "do NOT arm Operator T1 until this is green"],
                      dedup_key="adversary_wrapper_bug")
        except Exception as _ae:
            print(f"  [adversary] battle could not be scored ({_ae})")

    print(f"\n  Trend file: {_TREND}")
    print("  HEADLINE = selWin% / selMed (the honest selection signal; the MEAN is outlier-skewed).")
    print("  netReal = exit-aware realized P&L of the marketable-filled set (stop/D122/EOD, doc 196)")
    print("            -- the realistic per-name P&L. netEdge = marketable vs passive, net.")
    print("  gate = doc-182 faller/D170 block correctness (populates from Mon when 177-191 deploy).")

    # ── doc 282: the posture-delta scoreboard (doc 280's instrument) — best-effort, never fatal.
    # This is the RESTORE GATE for the doc-281 size cut (EXEC_RISK_PER_TRADE_PCT/EXEC_MAX_POSITION_PCT):
    # sizing is restored only when the cumulative hold-vs-exit delta turns durably positive (n>=30).
    try:
        r = subprocess.run([sys.executable, str(_SCRIPTS / "_doc280_posture_scoreboard.py"), "--append"],
                           capture_output=True, text=True, timeout=1800, cwd=str(_ROOT), env=_SUB_ENV)
        tail = (r.stdout or "").strip().splitlines()
        if tail:
            print("  [posture-delta] " + tail[-1][:160])
    except Exception as _pe:  # pragma: no cover — instrumentation must never fail the scorecard
        print(f"  [posture-delta] skipped ({_pe})")

    # ── doc 284: the rocket-gate Stage-2 FORWARD ledger (prereg-frozen) — best-effort, never fatal.
    # Gate = entry-time rvol>100 AND entry hour 9 ET; posture = hold-to-close (15% disaster stop, never
    # overnight); appends the session's gated dollar delta (B_hold_close - A_bar1 @ $8K/ticket) to
    # data/reports/rocket_gate_ledger.jsonl. Acceptance is evaluated ONLY on forward rows (>= 2026-07-06)
    # per docs/research-log/284_rocket_gate_prereg.md — retro seeding is context, not evidence.
    try:
        r = subprocess.run([sys.executable, str(_SCRIPTS / "_doc284_rocket_gate_ledger.py"), "--append-today"],
                           capture_output=True, text=True, timeout=1800, cwd=str(_ROOT), env=_SUB_ENV)
        tail = (r.stdout or "").strip().splitlines()
        if tail:
            print("  [rocket-gate] " + tail[-1][:160])
        # doc 288: today's row is written at 16:01 ET BEFORE the local minute-bar warehouse lands, so it is
        # always a no_bar_data hole. Immediately repair every PRIOR forward hole now that its bars have landed
        # T+1 (prereg-§3 warehouse repair) — otherwise the acceptance sample never grows past n=0.
        rb = subprocess.run([sys.executable, str(_SCRIPTS / "_doc284_rocket_gate_ledger.py"), "--backfill-prior"],
                            capture_output=True, text=True, timeout=1800, cwd=str(_ROOT), env=_SUB_ENV)
        rbt = [ln for ln in (rb.stdout or "").splitlines() if "repaired" in ln]
        if rbt:
            print("  [rocket-gate] " + rbt[-1][:160])
    except Exception as _rge:  # pragma: no cover — instrumentation must never fail the scorecard
        print(f"  [rocket-gate] skipped ({_rge})")

    # ── doc 292: the RV forward shadow-ledger (doc-291 confirmation instrument) — best-effort, never fatal.
    # Scores challenger-vs-GBM[HAR-only] on each new session across the 151-name liquid panel; certification
    # bar frozen in _doc292_PREREG.md (n>=60 fwd sessions, CI excl 0, >=2%). Self-repairing (T+1 bars).
    try:
        r = subprocess.run([sys.executable, str(_SCRIPTS / "_doc292_rv_forward_ledger.py"), "--append"],
                           capture_output=True, text=True, timeout=1800, cwd=str(_ROOT), env=_SUB_ENV)
        tail = (r.stdout or "").strip().splitlines()
        if tail:
            print("  [rv-forward] " + tail[-1][:160])
    except Exception as _rvf:  # pragma: no cover — instrumentation must never fail the scorecard
        print(f"  [rv-forward] skipped ({_rvf})")

    # ── doc 285: the CONFIG-TRUTH recon (gaps #6/#12) — best-effort, never fatal.
    # The "broker_truth_recon for CONFIG": per-fill notional/equity + risk-at-stop vs the
    # env-declared intent (EXEC_TIER*_POSITION_PCT / KELLY_TIER*_RISK_PCT), T2 wide-arm
    # fraction + exit_policy applied-vs-intended from the session log, the invisible
    # user-scope env surface (HKCU), and instrument dark-run checks. Appends one row to
    # data/reports/config_truth_recon.jsonl; every breach prints a [CONFIG-DRIFT] line.
    try:
        r = subprocess.run([sys.executable, str(_SCRIPTS / "config_truth_recon.py")],
                           capture_output=True, text=True, timeout=1800, cwd=str(_ROOT), env=_SUB_ENV)
        tail = (r.stdout or "").strip().splitlines()
        for _ln in tail:
            if "[CONFIG-DRIFT]" in _ln:
                print("  [config-truth] " + _ln[:160])
        if tail:
            print("  [config-truth] " + tail[-1][:160])
    except Exception as _cte:  # pragma: no cover — instrumentation must never fail the scorecard
        print(f"  [config-truth] skipped ({_cte})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
