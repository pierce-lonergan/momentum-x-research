"""DOC 284 — ROCKET-GATE STAGE-2 FORWARD LEDGER (doc-283 Step 3b; prereg: docs/research-log/284_rocket_gate_prereg.md).

The doc-281 investigation graded the rvol>100 x hour-9 hold-to-close cell D+ (threshold-mined, frozen-OOS fails,
book-negative deployable estimand) and the doc-280 blanket posture flip is NO-SHIP. Verdict: FREEZE a prereg and
evaluate FORWARD ONLY. This script is that instrument. For one session date it computes:

  1. the GATED candidates: final_action==BUY rows of data/features/features_{date}.jsonl with entry-time
     rvol > 100 AND entry hour == 9 ET (timestamps are UTC; 13:xx UTC = 9:xx ET under EDT — converted via
     America/New_York so EST winter sessions stay correct);
  2. their A_bar1 vs B_hold_close returns through the doc-280 arm definitions (validated arena fill model +
     LOCAL minute bars, hostile 0.5% one-way exit haircut) — reusing _doc280_posture_backtest's
     _load_local_bars/_arm_returns verbatim;
  3. the per-session gated dollar delta at $8K/ticket, appended as ONE JSON line to
     data/reports/rocket_gate_ledger.jsonl: {date, n_gated, delta_hold_vs_bar1_usd, retro, ...}.

FROZEN PARAMETERS (see the module constants): any change = AUTOMATIC KILL of the gate (re-tuning is not a thing
this prereg permits; tests/unit/test_rocket_gate_ledger.py trips if the constants move). All history before
2026-07-06 is RETROSPECTIVE SEEDING (retro=true) and is EXCLUDED from the acceptance test.

Usage:
    python scripts/_doc284_rocket_gate_ledger.py --seed           # backfill all history (retro rows; idempotent)
    python scripts/_doc284_rocket_gate_ledger.py --date 2026-07-06
    python scripts/_doc284_rocket_gate_ledger.py --append-today   # nightly hook (post_close_scorecard.py)
    python scripts/_doc284_rocket_gate_ledger.py --acceptance     # evaluate the frozen acceptance test
    python scripts/_doc284_rocket_gate_ledger.py                  # print the ledger summary
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from random import Random

_ROOT = Path(__file__).resolve().parent.parent
_FEATURES = _ROOT / "data" / "features"
_LEDGER = _ROOT / "data" / "reports" / "rocket_gate_ledger.jsonl"

# ── FROZEN PARAMETERS — doc 284 prereg, frozen 2026-07-05. ANY edit = automatic kill of the gate. ──
FORWARD_START = "2026-07-06"   # first forward session; every earlier date is retro seeding
RVOL_MIN = 100.0               # gate: entry-time rvol STRICTLY greater than this
ENTRY_HOUR_ET = 9              # gate: entry hour in America/New_York == 9
STOP_PCT = 0.15                # disaster stop of the shippable posture (doc-280 D-arm, recorded as diagnostic)
EXIT_COST = 0.005              # hostile one-way exit haircut (doc-280)
NOTIONAL = 8000.0              # $/ticket dollarization (doc-280)
BOOT_B = 10_000                # day-blocked bootstrap resamples
BOOT_SEED = 284                # bootstrap RNG seed
N_MIN = 30                     # acceptance evaluates at >= this many gated FORWARD sessions
N_KILL = 60                    # not PASSED by this many gated forward sessions -> gate DEAD (no extension)
MAX_FIRST_BAR_OFF_MIN = 390.0  # same-session guard: first local bar must be within this many minutes of entry,
                               # else the ticket is UNMEASURED (a warehouse hole hands back NEXT-session bars —
                               # observed live on 2026-05-29/2026-06-30 — and must NOT masquerade as a $0 session)
MAX_UNMEASURED_FRAC = 0.20     # forward coverage-integrity: above this unmeasured share, acceptance cannot PASS
SUBMIT_OFF_MIN = 45 / 60.0     # doc-280: order submitted ~45 s after the features row (entry-fill machinery)
FILL_WINDOW_MIN = 2.0          # doc-280: marketable-limit fill window, minutes from submission
# ───────────────────────────────────────────────────────────────────────────────────────────────────


def parse_ts_utc(s):
    """ISO timestamp -> aware UTC datetime, or None if missing/unparseable."""
    if s is None:
        return None
    try:
        ts = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def entry_hour_et(ts_str):
    """UTC timestamp string -> hour-of-day in America/New_York (handles EDT and EST), or None."""
    ts = parse_ts_utc(ts_str)
    if ts is None:
        return None
    from zoneinfo import ZoneInfo
    return ts.astimezone(ZoneInfo("America/New_York")).hour


def gate_predicate(cand: dict) -> bool:
    """THE FROZEN GATE: entry-time rvol > RVOL_MIN AND entry hour ET == ENTRY_HOUR_ET.
    Accepts a _load_buy_candidates dict ('ts') or a raw features row ('timestamp').
    Missing or unparseable fields -> EXCLUDED (never guessed)."""
    rvol = cand.get("rvol")
    ts = cand.get("ts", cand.get("timestamp"))
    if rvol is None or ts is None:
        return False
    try:
        rv = float(rvol)
    except (TypeError, ValueError):
        return False
    h = entry_hour_et(ts)
    return h is not None and rv > RVOL_MIN and h == ENTRY_HOUR_ET


def is_retro(date: str) -> bool:
    """All history before FORWARD_START is retrospective seeding — excluded from acceptance."""
    return str(date) < FORWARD_START


def compute_session(date: str) -> dict:
    """One session's ledger row: gate the BUY candidates, price A_bar1 vs B_hold_close through the
    doc-280 machinery, sum the dollar delta at NOTIONAL/ticket. Heavy imports are lazy so the gate
    predicate stays unit-testable without the arena/duckdb stack."""
    os.chdir(_ROOT)  # the doc-280 loader + fill model resolve repo-relative paths
    scripts = str(_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import duckdb
    import fill_model_backtest as F
    import _doc280_posture_backtest as B

    cands = [c for c in F._load_buy_candidates(date) if gate_predicate(c)]
    row = {
        "date": date, "n_gated": len(cands), "n_filled": 0, "n_unmeasured": 0,
        "delta_hold_vs_bar1_usd": 0.0, "d_stop15_vs_bar1_usd": 0.0,
        "tickers": [], "no_bar_data": False, "fidelity": None, "notional": NOTIONAL,
        "retro": is_retro(date),
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if not cands:
        return row

    con = duckdb.connect()
    con.execute("SET threads=4")
    if F._HAVE_ARENA:
        spread, fill_model, fidelity = F.SpreadModel(), F.AlpacaFillModel(), "arena"
    else:  # pragma: no cover — arena is present in this repo
        spread, fill_model, fidelity = None, None, "proxy"
    row["fidelity"] = fidelity
    settings = F.Settings()
    rng = Random(f"doc284:{date}")  # frozen seed rule: deterministic PER SESSION (recompute-stable)
    sub_off = SUBMIT_OFF_MIN        # frozen: submit ~45s after the features row (doc-280)
    n_with_bars = 0
    try:
        for c in cands:
            try:
                ts0 = F._parse_ts(c["ts"])
            except Exception:
                continue
            lb = B._load_local_bars(con, c["ticker"], ts0)
            if (not lb or not lb["bars"] or lb["close_px"] is None
                    or lb["bars"][0][0] > MAX_FIRST_BAR_OFF_MIN):
                # ticker absent from the warehouse OR entry-day hole (loader falls through to the NEXT
                # session's bars): the ticket is UNMEASURED — never booked as a $0 outcome
                row["n_unmeasured"] += 1
                continue
            n_with_bars += 1
            r = {"ticker": c["ticker"], "price": c["price"], "ts0": ts0.replace(tzinfo=None),
                 "rvol": float(c.get("rvol") or 1.0), "gap": float(c.get("gap") or 0.0),
                 "mfcs": c.get("mfcs"), "bars": lb["bars"]}
            _passive, mkt = F._limits(r, sub_off, spread, settings.execution)
            filled, fill_px, fill_off = F._fill(r, mkt, FILL_WINDOW_MIN, sub_off, fidelity, spread, fill_model, rng)
            if not filled or fill_px <= 0:
                continue  # unfillable gated ticket -> contributes $0 (the deployable estimand)
            arms = B._arm_returns(fill_px, fill_off, lb["bars"], lb["close_px"], lb["nopen_px"],
                                  STOP_PCT, EXIT_COST)
            row["n_filled"] += 1
            row["tickers"].append(c["ticker"])
            row["delta_hold_vs_bar1_usd"] += (arms["B_hold_close"] - arms["A_bar1"]) * NOTIONAL
            row["d_stop15_vs_bar1_usd"] += (arms["D_hold_stop15"] - arms["A_bar1"]) * NOTIONAL
    finally:
        con.close()
    row["no_bar_data"] = n_with_bars == 0  # gated candidates but zero local bars (non-session / warehouse hole)
    row["delta_hold_vs_bar1_usd"] = round(row["delta_hold_vs_bar1_usd"], 2)
    row["d_stop15_vs_bar1_usd"] = round(row["d_stop15_vs_bar1_usd"], 2)
    return row


# ── ledger IO: append-only; a date already present is SKIPPED (--force replaces, loudly) ──

def read_ledger() -> list[dict]:
    rows = []
    if _LEDGER.exists():
        for line in _LEDGER.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def write_row(row: dict, force: bool = False) -> str:
    """Append the session row. Returns 'appended' | 'skipped' | 'replaced'."""
    _LEDGER.parent.mkdir(parents=True, exist_ok=True)
    rows = read_ledger()
    have = {r.get("date") for r in rows}
    if row["date"] in have:
        if not force:
            return "skipped"
        rows = [r for r in rows if r.get("date") != row["date"]] + [row]
        rows.sort(key=lambda r: r.get("date", ""))
        _LEDGER.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return "replaced"
    with open(_LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return "appended"


# ── the frozen acceptance test (doc 284 prereg §4) ──

def acceptance(rows: list[dict]) -> dict:
    """Forward-only, day-blocked. Sample = forward sessions with n_gated>=1 and same-session bar data
    (zero-fill sessions stay in at $0); holes (no_bar_data) are excluded but counted. PASS = bootstrap CI
    lower bound > 0 AND both chronological halves positive, at n>=N_MIN — and coverage integrity holds
    (unmeasured gated tickets <= MAX_UNMEASURED_FRAC). Not passed by N_KILL -> DEAD."""
    fwd_all = sorted((r for r in rows if not r.get("retro") and r.get("n_gated", 0) >= 1),
                     key=lambda r: r.get("date", ""))
    holes = [r["date"] for r in fwd_all if r.get("no_bar_data")]
    # Coverage integrity (§4) counts EVERY gated forward ticket, INCLUDING whole-session holes:
    # a no_bar_data session is 100% unmeasured (n_unmeasured == n_gated) and must weigh on the
    # fraction, not vanish from it — the observed hole mode (2026-05-29, 2026-06-30) is exactly
    # whole-session, so excluding holes here would make BLOCKED-COVERAGE unreachable.
    n_gated_t = sum(r.get("n_gated", 0) for r in fwd_all)
    n_unm_t = sum(r.get("n_unmeasured", 0) for r in fwd_all)
    fwd = [r for r in fwd_all if not r.get("no_bar_data")]
    n = len(fwd)
    unm_frac = (n_unm_t / n_gated_t) if n_gated_t else 0.0
    out = {"n_gated_forward_sessions": n, "coverage_holes": holes, "n_min": N_MIN, "n_kill": N_KILL,
           "unmeasured_ticket_frac": round(unm_frac, 4)}
    if n == 0:
        out.update(status="PENDING-COLLECTION", detail="no gated forward sessions yet")
        return out
    deltas = [float(r["delta_hold_vs_bar1_usd"]) for r in fwd]
    import numpy as np
    v = np.array(deltas, float)
    rng = np.random.RandomState(BOOT_SEED)
    means = [rng.choice(v, len(v), replace=True).mean() for _ in range(BOOT_B)]
    ci_lo, ci_hi = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    half = (n + 1) // 2
    h1, h2 = float(v[:half].sum()), float(v[half:].sum())
    out.update(mean_delta_usd=float(v.mean()), total_delta_usd=float(v.sum()),
               ci95_mean=[ci_lo, ci_hi], half1_total_usd=h1, half2_total_usd=h2)
    passed = n >= N_MIN and ci_lo > 0 and h1 > 0 and h2 > 0
    if passed and unm_frac > MAX_UNMEASURED_FRAC:
        # coverage-integrity clause (doc 284 §4): a PASS on a holey window is not a PASS —
        # repair the warehouse + recompute (documented --force) before the verdict can stand
        out["status"] = "BLOCKED-COVERAGE"
    elif passed:
        out["status"] = "PASSED"
    elif n >= N_KILL:
        out["status"] = "DEAD"   # the frozen kill horizon: no extension, no re-tune
    else:
        out["status"] = "PENDING-COLLECTION" if n < N_MIN else "FAILING-SO-FAR"
    return out


def _summary(rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: r.get("date", ""))
    gated = [r for r in rows if r.get("n_gated", 0) >= 1 and not r.get("no_bar_data")]
    retro = [r for r in gated if r.get("retro")]
    fwd = [r for r in gated if not r.get("retro")]
    print(f"=== DOC 284 ROCKET-GATE LEDGER — {len(rows)} session rows "
          f"({len(retro)} retro-gated, {len(fwd)} forward-gated) ===")
    print(f"{'date':>11} {'gated':>5} {'fill':>4} {'unm':>3} {'Δhold_close$':>12} {'Δstop15$':>10} {'retro':>5}")
    for r in rows:
        flag = "HOLE" if r.get("no_bar_data") else ""
        print(f"{r['date']:>11} {r.get('n_gated', 0):>5} {r.get('n_filled', 0):>4} {r.get('n_unmeasured', 0):>3} "
              f"{r.get('delta_hold_vs_bar1_usd', 0.0):>+12,.2f} {r.get('d_stop15_vs_bar1_usd', 0.0):>+10,.2f} "
              f"{str(bool(r.get('retro'))):>5} {flag}")
    for name, grp in (("RETRO (seeding — NOT acceptance evidence)", retro), ("FORWARD", fwd)):
        if not grp:
            print(f"\n{name}: none")
            continue
        d = [r["delta_hold_vs_bar1_usd"] for r in grp]
        pos = sum(1 for x in d if x > 0)
        print(f"\n{name}: {len(grp)} gated sessions | total Δ ${sum(d):+,.2f} | mean/session ${sum(d)/len(d):+,.2f} "
              f"| positive sessions {pos}/{len(grp)}")
    acc = acceptance(rows)
    print(f"\nACCEPTANCE (forward-only, frozen): {acc['status']} "
          f"(n={acc['n_gated_forward_sessions']}/{N_MIN}; kill at {N_KILL})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", action="store_true", help="backfill every features_*.jsonl session (idempotent)")
    ap.add_argument("--date", default=None, help="compute + append one session date")
    ap.add_argument("--append-today", action="store_true", help="nightly hook: append today's ET session")
    ap.add_argument("--backfill-prior", action="store_true",
                    help="doc 288: recompute prior FORWARD rows still flagged no_bar_data now that the "
                         "minute-bar warehouse has landed T+1 (documented prereg-§3 warehouse repair)")
    ap.add_argument("--acceptance", action="store_true", help="evaluate the frozen acceptance test")
    ap.add_argument("--force", action="store_true", help="replace an existing date row (prints loudly)")
    args = ap.parse_args()

    if args.acceptance:
        print(json.dumps(acceptance(read_ledger()), indent=2))
        return 0

    if args.backfill_prior:
        # doc 288: compute_session runs at 16:01 ET SAME-DAY, before the local minute-bar warehouse is
        # populated for that session, so every gated ticket is a whole-session hole (no_bar_data=true) and
        # acceptance can never grow. The bars land T+1 (proven: the retro seeds, computed T+1, DID measure).
        # This recomputes every prior FORWARD hole (strictly before today, so we never touch a still-warming
        # session) with force=True — the prereg §3 warehouse-repair path. Only ever REPAIRS dark rows; a
        # measured row is left untouched.
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        dark = [r["date"] for r in read_ledger()
                if not r.get("retro") and r.get("no_bar_data") and str(r["date"]) < today]
        if not dark:
            print("rocket-gate backfill-prior: no dark forward rows to repair")
            return 0
        repaired = 0
        for date in sorted(dark):
            if not (_FEATURES / f"features_{date}.jsonl").exists():
                print(f"rocket-gate backfill-prior {date}: no features file -> skipped")
                continue
            row = compute_session(date)
            action = write_row(row, force=True)
            still_hole = " STILL-NO_BAR_DATA (warehouse not landed yet)" if row["no_bar_data"] else ""
            if not row["no_bar_data"]:
                repaired += 1
            print(f"rocket-gate backfill-prior {date}: n_gated={row['n_gated']} filled={row['n_filled']} "
                  f"unmeasured={row['n_unmeasured']} delta_hold_vs_bar1=${row['delta_hold_vs_bar1_usd']:+,.2f} "
                  f"[{action}]{still_hole}")
        print(f"rocket-gate backfill-prior: repaired {repaired}/{len(dark)} dark forward row(s).")
        print(json.dumps(acceptance(read_ledger()), indent=2))
        return 0

    dates: list[str] = []
    if args.seed:
        dates = [f.stem.replace("features_", "") for f in sorted(_FEATURES.glob("features_2026-*.jsonl"))]
    elif args.date:
        dates = [args.date]
    elif args.append_today:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        if not (_FEATURES / f"features_{today}.jsonl").exists():
            print(f"rocket-gate {today}: no features file (no session) -> nothing to append")
            return 0
        dates = [today]
    else:
        _summary(read_ledger())
        return 0

    for date in dates:
        if not (_FEATURES / f"features_{date}.jsonl").exists():
            print(f"rocket-gate {date}: no features file -> skipped")
            continue
        existing = {r.get("date") for r in read_ledger()}
        if date in existing and not args.force:
            print(f"rocket-gate {date}: already in ledger -> skipped (append-only; --force replaces)")
            continue
        row = compute_session(date)
        action = write_row(row, force=args.force)
        if action == "replaced" and not row["retro"]:
            print(f"rocket-gate {date}: WARNING — FORWARD row force-replaced. Prereg §3 permits this "
                  f"ONLY as a documented warehouse data-repair; anything else is a protocol violation.")
        hole = " NO_BAR_DATA" if row["no_bar_data"] else ""
        print(f"rocket-gate {date}: n_gated={row['n_gated']} filled={row['n_filled']} "
              f"unmeasured={row['n_unmeasured']} delta_hold_vs_bar1=${row['delta_hold_vs_bar1_usd']:+,.2f} "
              f"retro={row['retro']}{hole} [{action}] -> {_LEDGER.relative_to(_ROOT)}")
    if args.seed:
        _summary(read_ledger())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
