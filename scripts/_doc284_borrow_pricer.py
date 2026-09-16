#!/usr/bin/env python
"""doc 284 / WORKSTREAM A — PRICE THE BORROW (doc-283 Step 1, THE DECIDER for the short door).

Prices the shadow-short ledger (selection_study weeks, 2026-06-01..2026-07-02) against REAL
historical IBKR borrow data archived by iborrowdesk.com, and evaluates the PRE-REGISTERED
GATE from doc 283 Section 5 Step 1.

PRE-REGISTERED DESIGN (fixed before looking at borrow results):
  * TICKET = one BUY evaluation row from data/features/features_*.jsonl with current_price>0
    and a timestamp — the EXACT ticket definition of scripts/selection_study.py, whose
    fade_short.whole_set_pnl is the gross we are netting (+0.62/+1.32/+1.09/+2.48/+1.11 %/wk,
    ticket-weighted pool = +1.33%).
  * Each calendar date maps to the EARLIEST weekly selection_study report containing it
    (weeklies overlap on boundary dates 06-12 / 06-18 / 06-24 / 06-25; disclosed).
  * BORROW per (ticker, date): iborrowdesk daily record for that exact date.
      - record with available >  0  -> FILLABLE, day-hold borrow cost = fee_apr / 252 (%).
      - record with available == 0  -> UNFILLABLE (zero inventory).
      - ticker known, no record that date -> UNFILLABLE (absent from IBKR shortable list that
        day; consistent with the live 0-for-94 HTB/NSS prior). Sensitivity arm: nearest
        record within +/-3 calendar days (LENIENT, reported separately, not gate-bearing).
      - ticker 404 / no history at all -> UNFILLABLE (never in IBKR SLB universe).
  * NET/ticket (FILLABLE tickets only) = gross_week(date) - fee_apr/252 - 0.75 (execution
    friction, mid of doc-283's 0.5-1.0) - 0.90 (stop-overshoot, mid of 0.5-1.3).  All in %.
  * FILLABLE FRACTION reported at BOTH grains (tickets and unique ticker-days), denominator
    disclosed. Gate uses the TICKET grain (tickets are the units of the gross).
  * CI: day-blocked bootstrap — resample DATES with replacement, B=5000, seed=284, mean of
    fillable-ticket net over the resampled dates; percentile 2.5/97.5.
  * GATE (verbatim doc 283 Section 5 Step 1):
      fillable-net <= 0 OR fillable fraction < 30%          -> 'GATE FAILED - TOMBSTONE'
      net > 0 with CI excluding 0 AND fillable >= 30%       -> 'GATE PASSED - proceed to
                                                                TradeZero quote shadow'
      else                                                  -> 'AMBIGUOUS - Step 2 needed'

HONESTY NOTES (stated up front, repeated in the artifact):
  * iborrowdesk 'fee' is the IBKR borrow APR. Day-of LOCATE fees at specialist brokers
    (CenterPoint / TradeZero / Guardian) are typically HIGHER than IBKR APR/252 for these
    names — so the IBKR-fee-based net is an OPTIMISTIC (UPPER) bound for the short door.
  * NO NUMBER BENT: a failed gate is a success — it kills the door cheaply.

Usage:
  python scripts/_doc284_borrow_pricer.py             # full run (fetch missing + price)
  python scripts/_doc284_borrow_pricer.py --no-fetch  # cache-only (offline rerun)
  python scripts/_doc284_borrow_pricer.py --delay 1.0 # politer fetch pacing

Outputs:
  data/research/doc284/borrow_cache/{SYMBOL}.json   (raw API responses, rerun-free)
  data/research/doc284/borrow_pricing.json          (the artifact)
  stdout: the table + the verdict, verbatim.
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date as _date
from datetime import datetime, timezone
from pathlib import Path

import httpx

_ROOT = Path(__file__).resolve().parents[1]
_FEATURES = _ROOT / "data" / "features"
_REPORTS = _ROOT / "data" / "reports"
_LOGS = _ROOT / "logs"
_OUTDIR = _ROOT / "data" / "research" / "doc284"
_CACHE = _OUTDIR / "borrow_cache"

WINDOW_START = "2026-06-01"
WINDOW_END = "2026-07-02"
SEED = 284
B_BOOT = 5000
FRICTION_PCT = 0.75      # execution friction, mid of doc-283's 0.5-1.0%
OVERSHOOT_PCT = 0.90     # stop-overshoot, mid of doc-283's 0.5-1.3%
# doc 288 AUDIT DISCLOSURE (frozen param NOT mutated, per doc-275/277 no-post-hoc-tuning discipline):
#   OVERSHOOT_PCT is subtracted FLAT from every fillable ticket at line ~360, but a stop-GAP-THROUGH cost is
#   only incurred when a ticket is actually stopped out (measured stop-rate n-weighted ~0.34). If 0.90% is a
#   per-STOP magnitude (as the label reads), the correct expected charge is ~0.90*0.34 ~= 0.31%, so the pooled
#   net is OVERSTATED ~2x: reported -1.205%/ticket CI[-1.571,-0.822] -> stop-weighted ~-0.62%/ticket
#   CI~[-0.99,-0.24]. VERDICT UNCHANGED: still <0 with CI fully below zero -> SHORT DOOR TOMBSTONE STANDS, and
#   the +0.13%/day frontier is unaffected (the pricer separately discloses its IBKR-APR borrow is an optimistic
#   lower bound vs 1-3% specialist locates). Ambiguity (MEDIUM confidence): if 0.90% was intended as an
#   already-whole-set-averaged drag, this is a labeling defect not a math bug. A clean stop-conditional re-run
#   (charge OVERSHOOT only on mfe>=cover_stop tickets) is the documented follow-up; the magnitude, not the
#   decision, is what moves.
TRADING_DAYS = 252
LENIENT_WINDOW_DAYS = 3  # sensitivity arm only
API = "https://iborrowdesk.com/api/ticker/{sym}"
UA = "Mozilla/5.0 (research; momentum-x doc284 borrow-cost study; contact: repo operator)"

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_FADER_RE = re.compile(r"\[(\d+)\]\s+([A-Z][A-Z0-9.\-]{0,9})\s+intra=")


# --------------------------------------------------------------------------- ledger
def extract_ledger():
    """Every BUY ticket (selection_study definition) in the shadow window."""
    tickets = []  # dicts: ticker, date, ts, price
    files = sorted(glob.glob(str(_FEATURES / "features_2026-*.jsonl")))
    for f in files:
        d = Path(f).stem.replace("features_", "")
        if not (WINDOW_START <= d <= WINDOW_END):
            continue
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if str(r.get("final_action", "")).upper() != "BUY":
                continue
            if not (r.get("current_price") or 0) or not r.get("timestamp"):
                continue  # mirrors selection_study._load ticket filter exactly
            t = str(r["ticker"]).upper()
            if not _TICKER_RE.match(t):
                continue
            tickets.append({"ticker": t, "date": d, "ts": r["timestamp"],
                            "price": float(r["current_price"])})
    return tickets


def extract_fader_candidates():
    """Secondary ledger: 15:50 fader-short S3-gate candidate lines from the runner logs."""
    out = []
    for f in sorted(glob.glob(str(_LOGS / "fader_short_2026-*.log"))):
        d = Path(f).stem.replace("fader_short_", "")
        if not (WINDOW_START <= d <= WINDOW_END):
            continue
        try:
            text = Path(f).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in _FADER_RE.finditer(text):
            out.append({"ticker": m.group(2), "date": d})
    # dedupe (ticker, date)
    seen, ded = set(), []
    for r in out:
        k = (r["ticker"], r["date"])
        if k not in seen:
            seen.add(k)
            ded.append(r)
    return ded


def load_weekly_gross():
    """date -> weekly whole_set_pnl (%) from the EARLIEST weekly study containing the date."""
    date2gross, weeks = {}, []
    for f in sorted(glob.glob(str(_REPORTS / "selection_study_2026-*.json"))):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        fs = d.get("fade_short") or {}
        if "whole_set_pnl" not in fs:
            continue
        g = 100.0 * float(fs["whole_set_pnl"])
        weeks.append({"file": Path(f).name, "dates": d["dates"], "n": d["n"],
                      "gross_pct": round(g, 4)})
        for dt in d["dates"]:
            date2gross.setdefault(dt, g)   # earliest study wins (files sorted by week)
    return date2gross, weeks


# --------------------------------------------------------------------------- borrow fetch
def fetch_borrow(tickers, delay=0.7, do_fetch=True, client=None):
    """Fetch iborrowdesk history per ticker, cache-first. Returns ticker -> envelope."""
    _CACHE.mkdir(parents=True, exist_ok=True)
    out, fetched, cached, failed = {}, 0, 0, []
    own_client = False
    if client is None and do_fetch:
        client = httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": UA})
        own_client = True
    try:
        for i, sym in enumerate(sorted(tickers)):
            cf = _CACHE / f"{sym}.json"
            if cf.exists():
                try:
                    env0 = json.loads(cf.read_text(encoding="utf-8"))
                    # validator hardening: only 200/404 are real data. A cached ERROR/
                    # NOT_FETCHED envelope must NOT count as coverage (it would silently
                    # bypass the PENDING guard and deflate fillability) -> refetch it.
                    if env0.get("status") in (200, 404):
                        out[sym] = env0
                        cached += 1
                        continue
                except Exception:
                    pass
            if not do_fetch:
                out[sym] = {"status": "NOT_FETCHED", "daily": []}
                failed.append(sym)
                continue
            env = None
            for attempt in (1, 2):
                try:
                    r = client.get(API.format(sym=sym))
                    if r.status_code == 200:
                        d = r.json()
                        env = {"status": 200, "fetched_at":
                               datetime.now(timezone.utc).isoformat(),
                               "daily": d.get("daily") or [],
                               "name": d.get("name"), "updated": d.get("updated")}
                    elif r.status_code == 404:
                        env = {"status": 404, "fetched_at":
                               datetime.now(timezone.utc).isoformat(), "daily": []}
                    else:
                        raise RuntimeError(f"http {r.status_code}")
                    break
                except Exception as e:
                    if attempt == 2:
                        env = {"status": f"ERROR:{type(e).__name__}", "daily": []}
                        failed.append(sym)
                    else:
                        time.sleep(5.0)
            if env.get("status") in (200, 404):   # never cache ERROR envelopes
                cf.write_text(json.dumps(env), encoding="utf-8")
            out[sym] = env
            fetched += 1
            if (i + 1) % 25 == 0:
                print(f"    ... fetch progress {i+1}/{len(tickers)}", flush=True)
            time.sleep(delay)
    finally:
        if own_client and client is not None:
            client.close()
    return out, {"fetched_now": fetched, "from_cache": cached, "failed": failed}


def _daily_index(env):
    """date-str -> {fee, available} for one ticker envelope."""
    idx = {}
    for row in env.get("daily") or []:
        dt = row.get("date")
        if dt:
            idx[dt] = row
    return idx


def classify(ticker, dt, env, idx):
    """Return (status, fee_apr, available, lenient_fee_apr, lenient_avail).

    status in {FILLABLE, UNFILL_ZERO_AVAIL, UNFILL_NO_DAY, UNFILL_NO_TICKER}."""
    lenient_fee, lenient_avail = None, None
    if env.get("status") != 200 or not idx:
        return "UNFILL_NO_TICKER", None, None, None, None
    row = idx.get(dt)
    # lenient arm: nearest record within +/-3 calendar days
    base = _date.fromisoformat(dt)
    best = None
    for ddt, drow in idx.items():
        try:
            off = abs((_date.fromisoformat(ddt) - base).days)
        except Exception:
            continue
        if off <= LENIENT_WINDOW_DAYS and (best is None or off < best[0]):
            best = (off, drow)
    if best is not None:
        lenient_fee = best[1].get("fee")
        lenient_avail = best[1].get("available")
    if row is None:
        return "UNFILL_NO_DAY", None, None, lenient_fee, lenient_avail
    fee = row.get("fee")
    avail = row.get("available")
    if not avail or avail <= 0:
        return "UNFILL_ZERO_AVAIL", fee, avail or 0, lenient_fee, lenient_avail
    if fee is None:
        return "UNFILL_NO_DAY", None, avail, lenient_fee, lenient_avail
    return "FILLABLE", float(fee), int(avail), lenient_fee, lenient_avail


# --------------------------------------------------------------------------- stats
def _pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def day_blocked_bootstrap(net_by_date, dates, b=B_BOOT, seed=SEED):
    """Resample dates with replacement; mean over pooled fillable-ticket nets."""
    rng = random.Random(seed)
    means, empty = [], 0
    for _ in range(b):
        pool = []
        for _k in range(len(dates)):
            pool.extend(net_by_date.get(rng.choice(dates), []))
        if pool:
            means.append(sum(pool) / len(pool))
        else:
            empty += 1
    return {"B": b, "seed": seed, "empty_resamples": empty,
            "ci_lo": round(_pct(means, 0.025), 4) if means else None,
            "ci_hi": round(_pct(means, 0.975), 4) if means else None,
            "boot_mean": round(sum(means) / len(means), 4) if means else None}


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="cache-only, no network")
    ap.add_argument("--delay", type=float, default=0.7, help="seconds between API calls")
    args = ap.parse_args()

    print("=" * 74)
    print("doc 284 WORKSTREAM A - PRICE THE BORROW  (doc-283 Step 1: THE DECIDER)")
    print("=" * 74)

    # 1) ledger --------------------------------------------------------------
    tickets = extract_ledger()
    dates = sorted({t["date"] for t in tickets})
    tds = sorted({(t["ticker"], t["date"]) for t in tickets})
    tickers = sorted({t["ticker"] for t in tickets})
    fader = extract_fader_candidates()
    fader_tickers = sorted({r["ticker"] for r in fader})
    print(f"\n[1] LEDGER (features BUY tickets, {WINDOW_START}..{WINDOW_END})")
    print(f"    tickets(BUY eval rows) = {len(tickets)}   unique ticker-days = {len(tds)}")
    print(f"    unique tickers = {len(tickers)}   dates = {len(dates)}")
    print(f"    secondary (fader 15:50 candidates): {len(fader)} ticker-days, "
          f"{len(fader_tickers)} tickers")

    date2gross, weeks = load_weekly_gross()
    missing_gross = [d for d in dates if d not in date2gross]
    if missing_gross:
        print(f"    WARNING: no weekly gross for dates {missing_gross} - EXCLUDED")
    pooled_check = (sum(w["n"] * w["gross_pct"] for w in weeks)
                    / max(1, sum(w["n"] for w in weeks)))
    print(f"    weekly gross map: " +
          ", ".join(f"{w['file'][-15:-5]}:{w['gross_pct']:+.2f}%" for w in weeks))
    print(f"    pooled gross self-check (ticket-weighted) = {pooled_check:+.3f}% "
          f"(doc-283 headline +1.33%)")

    # 2) borrow fetch ----------------------------------------------------------
    all_syms = sorted(set(tickers) | set(fader_tickers))
    print(f"\n[2] BORROW FETCH (iborrowdesk.com, IBKR archive) - {len(all_syms)} symbols, "
          f"sequential, {args.delay}s delay, cache={_CACHE}")
    borrow, fstats = fetch_borrow(all_syms, delay=args.delay, do_fetch=not args.no_fetch)
    print(f"    fetched now={fstats['fetched_now']}  cache hits={fstats['from_cache']}  "
          f"failed={len(fstats['failed'])} {fstats['failed'][:10]}")
    idx_by_sym = {s: _daily_index(env) for s, env in borrow.items()}

    # 3) classify every ticker-day; price every ticket -------------------------
    td_class = {}     # (ticker,date) -> dict
    for (tk, dt) in tds:
        st, fee, avail, lfee, lavail = classify(tk, dt, borrow.get(tk, {}),
                                                idx_by_sym.get(tk, {}))
        td_class[(tk, dt)] = {"status": st, "fee_apr": fee, "available": avail,
                              "lenient_fee_apr": lfee, "lenient_available": lavail}

    counts_td = Counter(v["status"] for v in td_class.values())
    counts_tickets = Counter(td_class[(t["ticker"], t["date"])]["status"] for t in tickets)

    # ticket-level nets over FILLABLE tickets only
    net_by_date = defaultdict(list)
    fill_fees, fill_nets = [], []
    week_net = defaultdict(list)
    for t in tickets:
        dt = t["date"]
        if dt not in date2gross:
            continue
        c = td_class[(t["ticker"], dt)]
        if c["status"] != "FILLABLE":
            continue
        day_borrow = c["fee_apr"] / TRADING_DAYS
        net = date2gross[dt] - day_borrow - FRICTION_PCT - OVERSHOOT_PCT
        net_by_date[dt].append(net)
        fill_fees.append(c["fee_apr"])
        fill_nets.append(net)
        week_net[date2gross[dt]].append(net)

    n_tickets = len(tickets)
    n_fill_tickets = counts_tickets.get("FILLABLE", 0)
    n_fill_td = counts_td.get("FILLABLE", 0)
    fill_frac_tickets = n_fill_tickets / n_tickets if n_tickets else 0.0
    fill_frac_td = n_fill_td / len(tds) if tds else 0.0

    # lenient sensitivity arm (NOT gate-bearing)
    len_fill_td = sum(1 for v in td_class.values()
                      if (v["lenient_available"] or 0) > 0
                      and v["lenient_fee_apr"] is not None)
    len_nets = []
    for t in tickets:
        dt = t["date"]
        if dt not in date2gross:
            continue
        c = td_class[(t["ticker"], dt)]
        if (c["lenient_available"] or 0) > 0 and c["lenient_fee_apr"] is not None:
            len_nets.append(date2gross[dt] - c["lenient_fee_apr"] / TRADING_DAYS
                            - FRICTION_PCT - OVERSHOOT_PCT)

    # fader secondary ledger, same classification
    fader_counts = Counter()
    for r in fader:
        st, *_rest = classify(r["ticker"], r["date"], borrow.get(r["ticker"], {}),
                              idx_by_sym.get(r["ticker"], {}))
        fader_counts[st] += 1

    print(f"\n[3] AVAILABILITY / CLASSIFICATION")
    print(f"    {'status':<20}{'ticker-days':>12}{'tickets':>10}")
    for st in ["FILLABLE", "UNFILL_ZERO_AVAIL", "UNFILL_NO_DAY", "UNFILL_NO_TICKER"]:
        print(f"    {st:<20}{counts_td.get(st, 0):>12}{counts_tickets.get(st, 0):>10}")
    print(f"    {'TOTAL':<20}{len(tds):>12}{n_tickets:>10}")
    print(f"    FILLABLE fraction: tickets {100*fill_frac_tickets:.1f}%  |  "
          f"ticker-days {100*fill_frac_td:.1f}%   (gate grain = tickets)")
    print(f"    lenient arm (+/-{LENIENT_WINDOW_DAYS}d nearest, sensitivity only): "
          f"fillable ticker-days {len_fill_td}/{len(tds)} "
          f"({100*len_fill_td/max(1,len(tds)):.1f}%)")
    print(f"    fader 15:50 secondary ledger: {dict(fader_counts)} "
          f"(fillable {100*fader_counts.get('FILLABLE',0)/max(1,len(fader)):.1f}%)")

    # 4) net -------------------------------------------------------------------
    mean_net = sum(fill_nets) / len(fill_nets) if fill_nets else None
    med_fee = _pct(fill_fees, 0.5)
    print(f"\n[4] NET/ticket over FILLABLE tickets only "
          f"(gross_week - fee_apr/252 - {FRICTION_PCT} - {OVERSHOOT_PCT}, all %)")
    if fill_nets:
        print(f"    fillable tickets n={len(fill_nets)} across {len(net_by_date)} dates")
        print(f"    borrow fee APR (fillable): median {med_fee:.1f}%  "
              f"p25 {_pct(fill_fees,0.25):.1f}%  p75 {_pct(fill_fees,0.75):.1f}%  "
              f"p95 {_pct(fill_fees,0.95):.1f}%  max {max(fill_fees):.1f}%")
        print(f"    day-hold borrow cost (fee/252): median {med_fee/252:.3f}%  "
              f"p95 {_pct(fill_fees,0.95)/252:.3f}%")
        print(f"    mean NET/ticket (fillable) = {mean_net:+.3f}%")
        print(f"    per-week NET means: " + ", ".join(
            f"g{g:+.2f}->n{sum(v)/len(v):+.2f}% (n={len(v)})"
            for g, v in sorted(week_net.items())))
    else:
        print("    NO fillable tickets - net undefined over empty set")

    # 5) day-blocked bootstrap ---------------------------------------------------
    boot = day_blocked_bootstrap(net_by_date, dates) if fill_nets else \
        {"B": B_BOOT, "seed": SEED, "ci_lo": None, "ci_hi": None,
         "boot_mean": None, "empty_resamples": B_BOOT}
    print(f"\n[5] DAY-BLOCKED BOOTSTRAP (resample {len(dates)} dates, B={boot['B']}, "
          f"seed={SEED})")
    if boot["ci_lo"] is not None:
        print(f"    fillable-net mean CI95 = [{boot['ci_lo']:+.3f}%, {boot['ci_hi']:+.3f}%]"
              f"  (boot mean {boot['boot_mean']:+.3f}%, empty resamples "
              f"{boot['empty_resamples']})")
    else:
        print("    bootstrap undefined (no fillable tickets)")

    # 6) THE GATE -----------------------------------------------------------------
    print(f"\n[6] THE PRE-REGISTERED GATE (doc 283 Section 5, Step 1)")
    coverage = 1.0 - len(fstats["failed"]) / max(1, len(all_syms))
    if coverage < 0.90:
        verdict = ("PENDING - borrow data coverage insufficient "
                   f"({100*coverage:.0f}% of symbols fetched; source down/blocked?)")
    elif mean_net is None or mean_net <= 0 or fill_frac_tickets < 0.30:
        verdict = "GATE FAILED - TOMBSTONE"
    elif (mean_net > 0 and boot["ci_lo"] is not None and boot["ci_lo"] > 0
          and fill_frac_tickets >= 0.30):
        verdict = "GATE PASSED - proceed to TradeZero quote shadow"
    else:
        verdict = "AMBIGUOUS - Step 2 needed"
    print(f"    fillable-net = "
          f"{('%+.3f%%' % mean_net) if mean_net is not None else 'UNDEFINED (0 fillable)'}"
          f"  |  fillable fraction (tickets) = {100*fill_frac_tickets:.1f}%  "
          f"|  CI95 lo = {boot['ci_lo']}")
    print(f"\n    VERDICT: {verdict}\n")
    print("    NB: iborrowdesk fee = IBKR borrow APR; day-of LOCATE fees at specialist")
    print("    brokers are typically HIGHER -> this net is an OPTIMISTIC (UPPER) bound.")

    # artifact ----------------------------------------------------------------------
    _OUTDIR.mkdir(parents=True, exist_ok=True)
    artifact = {
        "doc": 284, "workstream": "A-borrow-pricer",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prereg": {
            "window": [WINDOW_START, WINDOW_END],
            "ticket_definition": "selection_study BUY eval row (final_action==BUY, "
                                 "current_price>0, timestamp present)",
            "gross_source": "fade_short.whole_set_pnl per weekly selection_study report; "
                            "date -> earliest weekly report containing it (weeklies "
                            "overlap on 06-12/06-18/06-24/06-25)",
            "friction_pct": FRICTION_PCT, "overshoot_pct": OVERSHOOT_PCT,
            "day_borrow": "fee_apr/252", "bootstrap": {"B": B_BOOT, "seed": SEED,
                                                       "block": "date"},
            "gate": "doc283 s5 step1: fillable-net<=0 OR fillable<30% -> FAILED; "
                    "net>0 AND ci_lo>0 AND fillable>=30% -> PASSED; else AMBIGUOUS",
            "optimistic_bound_note": "IBKR APR/252 underprices day-of specialist locate "
                                     "fees; net here is an UPPER bound for the door",
        },
        "ledger": {
            "tickets": n_tickets, "unique_ticker_days": len(tds),
            "unique_tickers": len(tickers), "dates": dates,
            "note": "prompt expected ~2000-2500 'ticker-days' == BUY ROWS (tickets); "
                    "unique (ticker,date) pairs = " + str(len(tds)),
            "fader_secondary": {"ticker_days": len(fader),
                                "tickers": len(fader_tickers),
                                "classification": dict(fader_counts)},
        },
        "weekly_gross": weeks,
        "pooled_gross_selfcheck_pct": round(pooled_check, 4),
        "fetch": {"symbols": len(all_syms), **{k: v for k, v in fstats.items()}},
        "classification": {
            "ticker_days": dict(counts_td), "tickets": dict(counts_tickets),
            "fillable_fraction_tickets": round(fill_frac_tickets, 4),
            "fillable_fraction_ticker_days": round(fill_frac_td, 4),
            "lenient_arm": {
                "window_days": LENIENT_WINDOW_DAYS,
                "fillable_ticker_days": len_fill_td,
                "fillable_fraction_ticker_days":
                    round(len_fill_td / max(1, len(tds)), 4),
                "mean_net_pct": round(sum(len_nets) / len(len_nets), 4)
                    if len_nets else None,
                "n_tickets": len(len_nets),
            },
        },
        "fees_fillable_apr_pct": {
            "n": len(fill_fees),
            "median": round(med_fee, 2) if fill_fees else None,
            "p25": round(_pct(fill_fees, 0.25), 2) if fill_fees else None,
            "p75": round(_pct(fill_fees, 0.75), 2) if fill_fees else None,
            "p95": round(_pct(fill_fees, 0.95), 2) if fill_fees else None,
            "max": round(max(fill_fees), 2) if fill_fees else None,
        },
        "net": {
            "mean_fillable_net_pct": round(mean_net, 4) if mean_net is not None else None,
            "n_fillable_tickets": len(fill_nets),
            "n_dates_with_fillable": len(net_by_date),
            "per_week": [{"gross_pct": round(g, 2),
                          "mean_net_pct": round(sum(v) / len(v), 4), "n": len(v)}
                         for g, v in sorted(week_net.items())],
        },
        "bootstrap": boot,
        "gate": {"verdict": verdict, "fetch_coverage": round(coverage, 4),
                 "fillable_net_pct": round(mean_net, 4) if mean_net is not None else None,
                 "fillable_fraction_tickets": round(fill_frac_tickets, 4),
                 "ci_lo": boot["ci_lo"], "ci_hi": boot["ci_hi"]},
        "ticker_day_detail": [
            {"ticker": tk, "date": dt, **td_class[(tk, dt)]} for (tk, dt) in tds],
    }
    out = _OUTDIR / "borrow_pricing.json"
    out.write_text(json.dumps(artifact, indent=1), encoding="utf-8")
    print(f"\n    artifact -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
