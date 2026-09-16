"""DOC 294 — SEVP pipeline (armed, blind until coverage certifies). Prereg frozen in _doc294_PREREG.md
(sha256 ae62ea59..., commit 5c2b0e7): gates may be read ONLY at N>=300 two-leg-covered events; until then
this pipeline computes plumbing metrics only (event pulls, join integrity, coverage counts). Death 2026-09-01.

  --events   : pull + cache earnings events (yfinance primary, timestamped; Polygon filing cross-check cached)
  --coverage : join events x option-price store; report two-leg coverage (NO P&L — pre-unblinding)
  --run      : REFUSES below N>=300; at coverage, computes the frozen G1/G2 gates
"""
from __future__ import annotations
import argparse, json
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
OUT = _ROOT / "data" / "research" / "doc294"
OUT.mkdir(parents=True, exist_ok=True)
EVENTS = OUT / "earnings_events.jsonl"
PRICES = _ROOT / "data" / "iv_store" / "option_eod.jsonl"
N_UNBLIND = 300
COST_LINES = (0.05, 0.10)
SEED = 294


def pull_events(names):
    """yfinance earnings dates (timestamped) for names; cached append-only by (ticker, ts)."""
    import yfinance as yf
    have = set()
    rows = []
    if EVENTS.exists():
        rows = [json.loads(l) for l in EVENTS.read_text(encoding="utf-8").splitlines() if l.strip()]
        have = {(r["ticker"], r["ts"]) for r in rows}
    added = 0
    for n in names:
        try:
            ed = yf.Ticker(n).get_earnings_dates(limit=16)
        except Exception:
            continue
        if ed is None:
            continue
        for ts in ed.index:
            iso = str(ts)
            d = iso[:10]
            if d < "2024-09-15" or d > "2026-07-12":
                continue
            k = (n, iso)
            if k in have:
                continue
            rows.append({"ticker": n, "ts": iso, "date": d, "source": "yfinance"})
            have.add(k); added += 1
    EVENTS.write_text("\n".join(json.dumps(r) for r in sorted(rows, key=lambda r: (r["ticker"], r["ts"])))
                      + ("\n" if rows else ""), encoding="utf-8")
    return {"names_pulled": len(names), "events_total": len(rows), "events_added": added}


def _load_prices():
    by_tc = {}
    if PRICES.exists():
        for l in PRICES.read_text(encoding="utf-8").splitlines():
            if l.strip():
                r = json.loads(l)
                by_tc.setdefault(r["ticker"], {}).setdefault(r["contract"], {})[r["date"]] = r
    return by_tc


def _event_legs(ev, prices):
    """find the straddle pair covering the event with both T-1 and T+1 closes; prereg contract rule."""
    tk = ev["ticker"]
    ann = datetime.fromisoformat(ev["ts"].replace("+00:00", "")[:19])
    cands = []
    for ct, days in prices.get(tk, {}).items():
        ds = sorted(days)
        pre = [d for d in ds if datetime.fromisoformat(d + "T16:00") < ann]
        post = [d for d in ds if datetime.fromisoformat(d + "T16:00") > ann]
        if not pre or not post:
            continue
        t0, t1 = pre[-1], post[0]
        r0 = days[t0]
        dte = (datetime.fromisoformat(r0["exp"]) - datetime.fromisoformat(t0)).days
        if not (5 <= dte <= 40):
            continue
        cands.append((abs(dte - 21), ct, t0, t1, r0["cp"], r0["K"]))
    if not cands:
        return None
    cands.sort()
    # need BOTH cp legs of the chosen strike/exp
    _score, ct, t0, t1, _cp, K = cands[0]
    base = ct[:-9]  # strip cp+strike
    legs = {}
    for cp in ("C", "P"):
        match = [c for c in prices.get(tk, {}) if c.startswith(base) and c[-9] == cp
                 and abs(_parse_k(c) - K) < 1e-9]
        if not match:
            return None
        days = prices[tk][match[0]]
        if t0 not in days or t1 not in days:
            return None
        legs[cp] = (days[t0]["close"], days[t1]["close"])
    return {"t0": t0, "t1": t1, "K": K, "legs": legs}


def _parse_k(contract):
    return int(contract.split(":", 1)[1][-8:]) / 1000.0


def coverage_report():
    prices = _load_prices()
    rows = [json.loads(l) for l in EVENTS.read_text(encoding="utf-8").splitlines() if l.strip()] if EVENTS.exists() else []
    covered, uncovered = [], 0
    for ev in rows:
        legs = _event_legs(ev, prices)
        if legs:
            covered.append({**ev, **{"t0": legs["t0"], "t1": legs["t1"]}})
        else:
            uncovered += 1
    rep = {"events_known": len(rows), "events_two_leg_covered": len(covered), "events_uncovered": uncovered,
           "unblind_at": N_UNBLIND, "status": ("READY-TO-UNBLIND" if len(covered) >= N_UNBLIND
                                               else "PENDING-COLLECTION"),
           "covered_by_name": {}}
    for c in covered:
        rep["covered_by_name"][c["ticker"]] = rep["covered_by_name"].get(c["ticker"], 0) + 1
    return rep


def run_gates():
    rep = coverage_report()
    if rep["events_two_leg_covered"] < N_UNBLIND:
        return {"REFUSED": f"coverage {rep['events_two_leg_covered']}/{N_UNBLIND} — gates stay blind "
                           f"(prereg ae62ea59...)", "coverage": rep}
    # ---- gates (only reachable at coverage; frozen math) ----
    import numpy as np
    prices = _load_prices()
    rows = [json.loads(l) for l in EVENTS.read_text(encoding="utf-8").splitlines() if l.strip()]
    nets = {c: [] for c in COST_LINES}
    events_used = []
    for ev in rows:
        legs = _event_legs(ev, prices)
        if not legs:
            continue
        p0 = legs["legs"]["C"][0] + legs["legs"]["P"][0]
        p1 = legs["legs"]["C"][1] + legs["legs"]["P"][1]
        if p0 <= 0:
            continue
        gross = (p0 - p1) / p0
        for c in COST_LINES:
            nets[c].append(gross - c)
        events_used.append({"ticker": ev["ticker"], "date": ev["date"], "gross": round(gross, 4)})
    # ── doc 298 CONFORMITY FIX, applied while still BLIND. run_gates() refuses below N=300 and
    # coverage stands at 205, so no gate statistic has ever been computed for this test. The runner
    # disagreed with its own frozen prereg in FOUR places — the doc-296 failure class, caught before
    # the unblind instead of after. Conforming a runner to a frozen spec is NOT the doc-284
    # "re-tune = kill" case: there is no result to tune toward.
    #
    #   1. prereg L27 "event-blocked bootstrap (B=5000)"  <- was plain IID rng.choice over events
    #   2. prereg L29 "both CALENDAR halves"              <- was file order, and pull_events() writes
    #                                                        the file sorted by (ticker, ts), so the
    #                                                        halves were literally A-M vs N-Z
    #   3. prereg L30-32 "G2 CONDITIONER SUB-GATE"        <- absent entirely
    #   4. prereg L34 "fat-tail -> FRAGILE-PASS"          <- absent entirely
    res = {"n_events": len(events_used),
           "conformity": "doc-298: event-blocked bootstrap, calendar halves, G2, fat-tail rule"}
    order = list(np.argsort([e["date"] for e in events_used]))     # (2) calendar order
    dates = np.array([events_used[i]["date"] for i in order])
    uniq = sorted(set(dates.tolist()))
    mid_date = uniq[len(uniq) // 2]

    def blocked_boot(vals, ds, seed):
        """(1) Resample event DATES with replacement, keeping every event on a date together.
        Earnings on one session share a market factor; IID resampling understates the CI."""
        r = np.random.default_rng(seed)
        by_date = {d: vals[ds == d] for d in set(ds.tolist())}
        keys = list(by_date)
        means = []
        for _ in range(5000):
            pick = r.integers(0, len(keys), len(keys))
            means.append(np.concatenate([by_date[keys[i]] for i in pick]).mean())
        lo, hi = np.percentile(means, [2.5, 97.5])
        return float(lo), float(hi)

    for c in COST_LINES:
        v = np.array(nets[c])[order]
        lo, hi = blocked_boot(v, dates, SEED)
        h1, h2 = v[dates < mid_date], v[dates >= mid_date]
        cum = float(np.abs(v).sum())
        worst = float(np.abs(v).max() / cum) if cum > 0 else 1.0
        res[f"cost_{int(c*100)}pct"] = {
            "mean_net": round(float(v.mean()), 4), "median_net": round(float(np.median(v)), 4),
            "ci95_event_blocked": [round(lo, 4), round(hi, 4)],
            "half1_mean_calendar": round(float(h1.mean()), 4) if len(h1) else None,
            "half2_mean_calendar": round(float(h2.mean()), 4) if len(h2) else None,
            "n_half1": int(len(h1)), "n_half2": int(len(h2)),
            "max_loss": round(float(v.min()), 4),
            # (4) prereg L34
            "largest_single_event_share_of_cum_net": round(worst, 4),
            "fat_tail_flag": bool(worst > 0.50),
        }
    # (3) prereg L30-32
    res["G2"] = _g2_conditioner(events_used, nets, dates, order)
    (OUT / "sevp_gates.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    return res


def _g2_conditioner(events_used, nets, dates, order):
    """prereg L30-32 — G2 CONDITIONER SUB-GATE.

    Events the doc-291 RV forecast would TAKE (forecast RV < implied move, i.e. the straddle looks
    rich relative to our forecast) must beat the complement. G2 failure kills the "our signal adds"
    claim, not G1. If the forecast cannot be computed the gate REFUSES — a missing conditioner must
    never be recorded as a pass.
    """
    import numpy as np
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "H291", str(_ROOT / "scripts" / "_doc291_stage2_harrv.py"))
        H = importlib.util.module_from_spec(spec); spec.loader.exec_module(H)
        panel = H.build_panel()
    except Exception as e:
        return {"REFUSED": f"doc-291 RV forecaster unavailable ({type(e).__name__}); G2 not evaluated "
                           "and MUST NOT be reported as a pass"}
    fc = {(t, d): float(np.exp(x)) for t, d, x in zip(panel.ticker, panel.d, panel.log_rv_d)}

    missing, take, skip = 0, [], []
    for j, i in enumerate(order):
        ev = events_used[i]
        rv = fc.get((ev["ticker"], ev["date"]))
        if rv is None:
            missing += 1
            continue
        implied = abs(ev.get("gross", 0.0))     # straddle premium as a fraction of its own price
        (take if rv < implied else skip).append(j)
    out = {"n_take": len(take), "n_skip": len(skip), "n_missing_forecast": missing}
    if missing > 0.25 * max(len(events_used), 1):
        out["REFUSED"] = f"RV forecast missing for {missing}/{len(events_used)} events (>25%)"
        return out
    if len(take) < 30 or len(skip) < 30:
        out["REFUSED"] = "an arm is below n=30; G2 underpowered and not evaluated"
        return out
    for c in COST_LINES:
        v = np.array(nets[c])[order]
        r = np.random.default_rng(SEED)
        boots = [float(r.choice(v[take], len(take)).mean() - r.choice(v[skip], len(skip)).mean())
                 for _ in range(5000)]
        lo, hi = np.percentile(boots, [2.5, 97.5])
        out[f"cost_{int(c*100)}pct"] = {
            "take_minus_skip": round(float(v[take].mean() - v[skip].mean()), 4),
            "ci95": [round(float(lo), 4), round(float(hi), 4)], "passes": bool(lo > 0)}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--events", default=None, help="comma-list of names to pull events for")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()
    if args.events:
        print(json.dumps(pull_events([n.strip().upper() for n in args.events.split(",")]), indent=2))
    if args.coverage:
        print(json.dumps(coverage_report(), indent=2))
    if args.run:
        print(json.dumps(run_gates(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
