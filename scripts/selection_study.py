#!/usr/bin/env python
"""doc 198: Selection study — what separates the continuers from the faders.

doc 192/196 proved SELECTION dominates P&L. This mines data/features/*.jsonl: for every
BUY candidate across recent sessions it fetches the forward path, labels RAN (max favorable
excursion >= --run-threshold) vs FADED, and ranks every decision-time feature by how well
it SEPARATES the two (rank-based AUC — P(a random winner outranks a random fader)). The
ranked features are the data-driven head-start on the selection lever (188/191/184):
which signals actually predicted continuation on OUR low-float tape — and which (per the
doc-187 debunked list) do NOT.

AUC reading: 0.50 = no separation; >0.50 = higher feature -> more likely to RUN; <0.50 =
higher feature -> more likely to FADE. |AUC-0.50| is the separation strength.

Usage:
    python scripts/selection_study.py                       # last 3 sessions, MFE>=10% = ran
    python scripts/selection_study.py --days 5 --run-threshold 0.10 --max-per-day 120
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import Settings  # noqa: E402
from src.data.alpaca_client import AlpacaDataClient  # noqa: E402

# decision-time features to test (numeric). log-scaled ones are size/volume.
_NUMERIC = ["gap_pct", "rvol", "mfcs", "current_price", "risk_score",
            "premarket_volume", "float_shares", "market_cap", "hour_et", "minute_et"]
_LOG_FEATS = {"premarket_volume", "float_shares", "market_cap"}
_BOOL = ["has_news_catalyst", "rvol_exhaustion", "qualifies_for_debate"]


def _parse_ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _recent_dates(n):
    files = sorted(glob.glob(str(_ROOT / "data" / "features" / "features_2026-*.jsonl")))
    return [Path(f).stem.replace("features_", "") for f in files[-n:]]


def _load(date, max_per_day):
    f = _ROOT / "data" / "features" / f"features_{date}.jsonl"
    if not f.exists():
        return []
    rows = []
    for line in f.read_text(encoding="utf-8").splitlines():
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
            continue
        rows.append(r)
    # doc 213 BUGFIX: the old `rows[:max_per_day]` truncated by FILE ORDER (= time order),
    # so a cap kept only the EARLIEST N evals of the day and dropped all later ones. That
    # made different --max values sample different TIMES OF DAY (the red-team found this
    # drove the 3-day-vs-wide "contradiction", not sample size). Fix: if capping, take an
    # EVENLY-SPACED (stride) sample across the whole day so the slice is time-representative.
    # max_per_day<=0 means NO CAP (the definitive run). Deterministic (no RNG) for repeatability.
    if max_per_day and max_per_day > 0 and len(rows) > max_per_day:
        stride = len(rows) / max_per_day
        rows = [rows[int(i * stride)] for i in range(max_per_day)]
    return rows


async def _label(client, r, run_threshold, horizon_min):
    """Fetch the forward path; return (ran: bool, mfe, ret_end) or None."""
    price = float(r.get("current_price") or 0)
    try:
        ts0 = _parse_ts(r["timestamp"])
    except Exception:
        return None
    try:
        bars = await client.get_bars(
            r["ticker"], timeframe="1Min",
            start=ts0.strftime("%Y-%m-%dT%H:%M:%SZ"),
            end=(ts0 + timedelta(minutes=horizon_min)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            limit=500,
        )
    except Exception:
        bars = None
    if not bars or price <= 0:
        return None
    highs, last = [], None
    for b in bars:
        t = b.get("t") or b.get("timestamp")
        if t is None:
            continue
        bt = _parse_ts(t) if isinstance(t, str) else t
        if bt < ts0:
            continue
        h = b.get("h", b.get("high")); c = b.get("c", b.get("close"))
        if h is not None:
            highs.append(float(h))
        if c is not None:
            last = float(c)
    if not highs or last is None:
        return None
    mfe = (max(highs) - price) / price
    ret_end = (last - price) / price
    return {"ran": mfe >= run_threshold, "mfe": mfe, "ret_end": ret_end}


def _feat_value(r, key):
    if key.startswith("cs."):
        cs = r.get("component_scores") or {}
        return cs.get(key[3:])
    v = r.get(key)
    if v is None:
        return None
    try:
        v = float(v)
    except Exception:
        return None
    if key in _LOG_FEATS:
        return math.log1p(max(v, 0.0))
    return v


def _auc(winners, faders):
    """Rank-based AUC: P(random winner's feature > random fader's). Ties -> 0.5."""
    w = [x for x in winners if x is not None]
    f = [x for x in faders if x is not None]
    if not w or not f:
        return None, len(w), len(f)
    allv = sorted(w + f)
    rank = {}
    i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1] == allv[i]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank for ties
        rank[allv[i]] = avg
        i = j + 1
    rw = sum(rank[x] for x in w)
    n1, n2 = len(w), len(f)
    u = rw - n1 * (n1 + 1) / 2.0
    return u / (n1 * n2), n1, n2


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return 0.0
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--run-threshold", type=float, default=0.10, help="MFE >= this = RAN")
    ap.add_argument("--horizon-min", type=int, default=60)
    ap.add_argument("--max-per-day", type=int, default=120)
    ap.add_argument("--short-stop", type=float, default=0.08,
                    help="doc 200: fade-short cover stop above entry (squeeze cap)")
    ap.add_argument("--realistic-short", action="store_true", default=False,
                    help="doc 201: NET fade-short — Alpaca ETB/shortable filter + squeeze "
                    "gap-through + frictions (the executable edge, not the gross one)")
    ap.add_argument("--squeeze-gap-k", type=float, default=1.5,
                    help="doc 201: a stopped short covers at min(MFE, stop*k) — gap-through")
    ap.add_argument("--short-friction", type=float, default=0.005,
                    help="doc 201: round-trip bid-fill + slippage haircut on the short")
    ap.add_argument("--borrow-haircut", type=float, default=0.0,
                    help="doc 201: borrow-fee haircut (≈0 intraday; >0 for overnight holds)")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    dates = _recent_dates(args.days)
    if not dates:
        print("No feature logs."); return 1
    print(f"Selection study over {dates} (RAN = MFE >= {args.run_threshold:.0%} within {args.horizon_min}min)")

    settings = Settings()
    client = AlpacaDataClient(settings.alpaca)
    labeled = []  # (feature_row, label_dict)
    short_ok = {}  # ticker -> shortable&ETB (doc 201, only if --realistic-short)
    try:
        for date in dates:
            rows = _load(date, args.max_per_day)
            for i in range(0, len(rows), 6):
                chunk = rows[i:i + 6]
                labs = await asyncio.gather(*[_label(client, r, args.run_threshold, args.horizon_min) for r in chunk])
                for r, lab in zip(chunk, labs):
                    if lab:
                        labeled.append((r, lab))
        # doc 201: shortability per UNIQUE ticker (current ETB — proxy for the trade day).
        if args.realistic_short and labeled:
            uniq = sorted({r["ticker"] for r, _l in labeled})
            for i in range(0, len(uniq), 8):
                ch = uniq[i:i + 8]
                res = await asyncio.gather(
                    *[client.check_asset_tradable(t) for t in ch], return_exceptions=True)
                for t, a in zip(ch, res):
                    short_ok[t] = bool(isinstance(a, dict) and a.get("shortable")
                                       and a.get("easy_to_borrow"))
    finally:
        close = getattr(client, "aclose", None) or getattr(client, "close", None)
        if close:
            try:
                res = close()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass

    n = len(labeled)
    if not n:
        print("No labeled candidates (no forward bars)."); return 0
    winners = [r for r, lab in labeled if lab["ran"]]
    faders = [r for r, lab in labeled if not lab["ran"]]
    nw, nf = len(winners), len(faders)
    base_rate = nw / n
    print(f"\n  n={n} BUY candidates | RAN {nw} ({100*base_rate:.0f}%) | FADED {nf} "
          f"| mean MFE ran {_mean([l['mfe'] for r,l in labeled if l['ran']]):+.1%} "
          f"vs faded {_mean([l['mfe'] for r,l in labeled if not l['ran']]):+.1%}")

    # discover component-score keys present
    cs_keys = set()
    for r, _l in labeled:
        for k in (r.get("component_scores") or {}):
            cs_keys.add(f"cs.{k}")
    feats = _NUMERIC + sorted(cs_keys)

    rankings = []
    for key in feats:
        wv = [_feat_value(r, key) for r in winners]
        fv = [_feat_value(r, key) for r in faders]
        auc, c1, c2 = _auc(wv, fv)
        if auc is None or c1 < 5 or c2 < 5:
            continue
        rankings.append({"feature": key, "auc": round(auc, 3), "sep": round(abs(auc - 0.5), 3),
                         "ran_mean": round(_mean(wv), 4), "fade_mean": round(_mean(fv), 4),
                         "dir": "higher->RAN" if auc > 0.5 else "higher->FADE"})
    rankings.sort(key=lambda x: x["sep"], reverse=True)

    print(f"\n  FEATURE SEPARATION (ranked by |AUC-0.5|; AUC>0.5 = higher predicts RAN):")
    print(f"  {'feature':>20} | {'AUC':>5} | {'sep':>5} | {'ran_mean':>9} | {'fade_mean':>9} | direction")
    for x in rankings[:18]:
        rm = x["ran_mean"]; fm = x["fade_mean"]
        print(f"  {x['feature']:>20} | {x['auc']:>5.2f} | {x['sep']:>5.2f} | "
              f"{rm:>9.3f} | {fm:>9.3f} | {x['dir']}")

    # boolean features: ran-rate split
    print(f"\n  BOOLEAN features (RAN-rate True vs False; base {100*base_rate:.0f}%):")
    bool_rows = []
    for key in _BOOL:
        wt = sum(1 for r in winners if r.get(key)); ft = sum(1 for r in faders if r.get(key))
        wt_f = sum(1 for r in winners if not r.get(key)); ft_f = sum(1 for r in faders if not r.get(key))
        rate_true = wt / (wt + ft) if (wt + ft) else None
        rate_false = wt_f / (wt_f + ft_f) if (wt_f + ft_f) else None
        if rate_true is None and rate_false is None:
            continue
        bool_rows.append({"feature": key, "ran_rate_true": round(rate_true, 3) if rate_true is not None else None,
                          "n_true": wt + ft, "ran_rate_false": round(rate_false, 3) if rate_false is not None else None,
                          "n_false": wt_f + ft_f})
        rt = f"{100*rate_true:.0f}% (n={wt+ft})" if rate_true is not None else "n/a"
        rf = f"{100*rate_false:.0f}% (n={wt_f+ft_f})" if rate_false is not None else "n/a"
        print(f"    {key:>22}: True {rt}  vs  False {rf}")

    # doc 199 (f2): MFCS-bucketed breakdown — does the ELITE bucket (>=0.50) run LESS /
    # return LESS? (tests the doc-178 ELITE press that sizes UP at MFCS>=0.50.)
    # doc 213: Wilson 95% CI on a proportion — exposes underpowered buckets (the red-team's
    # core point: a 42% RAN at n=66 has a ~23pt CI, indistinguishable from neighbors).
    def _wilson(k, nn):
        if nn == 0:
            return (0.0, 0.0)
        import math
        z = 1.96; p = k / nn
        d = 1 + z * z / nn
        c = p + z * z / (2 * nn)
        h = z * math.sqrt(p * (1 - p) / nn + z * z / (4 * nn * nn))
        return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))

    print("\n  MFCS BUCKETS (tests doc-178 ELITE press = size UP at MFCS>=0.50):")
    print(f"    {'bucket':>10} | {'n':>4} | {'RAN%':>5} | {'95% CI':>13} | {'medFwd60':>8} | {'meanFwd60':>9}")
    buckets = [(0.0, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 1.01)]
    mfcs_rows = []
    for lo, hi in buckets:
        grp = [(r, l) for (r, l) in labeled
               if r.get("mfcs") is not None and lo <= float(r["mfcs"]) < hi]
        if not grp:
            continue
        nb = len(grp); ranb = sum(1 for _r, l in grp if l["ran"])
        mfe = _mean([l["mfe"] for _r, l in grp])
        fwd = _mean([l["ret_end"] for _r, l in grp])
        fwd_med = _median([l["ret_end"] for _r, l in grp])  # doc 213: median (mean is rocket-skewed)
        lo_ci, hi_ci = _wilson(ranb, nb)
        name = f">={lo:.2f}" if hi > 1.0 else f"{lo:.2f}-{hi:.2f}"
        print(f"    {name:>10} | {nb:>4} | {100*ranb/nb:>4.0f}% | "
              f"[{100*lo_ci:>4.0f},{100*hi_ci:>4.0f}]% | {fwd_med:>+7.2%} | {fwd:>+8.2%}")
        mfcs_rows.append({"bucket": name, "n": nb, "ran_rate": round(ranb / nb, 3),
                          "ran_ci95": [round(lo_ci, 3), round(hi_ci, 3)],
                          "mean_mfe": round(mfe, 4), "mean_fwd60": round(fwd, 4),
                          "median_fwd60": round(fwd_med, 4)})
    print("    ^ if the >=0.50 (ELITE) bucket has LOW RAN% / negative meanFwd60, the press is")
    print("      sizing INTO faders -- gate it on a continuation signal, not raw MFCS.")

    # doc 200: can we SHORT the predictable faders? Short at entry; cover at +short_stop%
    # (squeeze cap) else at +horizon close. P&L_short = -short_stop if MFE>=short_stop else
    # -ret_end. GROSS (no borrow/HTB fee, no stop slippage, no squeeze-gap-through).
    ss = args.short_stop

    def _spnl(l):
        return -ss if l["mfe"] >= ss else -l["ret_end"]

    def _ssum(rows):
        if not rows:
            return 0.0, 0.0, 0.0, 0
        sp = [_spnl(l) for _r, l in rows]
        win = sum(1 for x in sp if x > 0) / len(sp)
        stopped = sum(1 for _r, l in rows if l["mfe"] >= ss) / len(rows)
        return _mean(sp), win, stopped, len(sp)

    am, aw, ast_, an = _ssum(labeled)
    worst_mfe = max((l["mfe"] for _r, l in labeled), default=0.0)
    print(f"\n  FADE-SHORT (short at entry, cover stop +{ss:.0%}; GROSS — no borrow/HTB/slippage):")
    print(f"    whole BUY set: mean short P&L {am:+.2%} | win% {100*aw:.0f} | "
          f"stopped(squeezed) {100*ast_:.0f}% | n={an}")
    print(f"    {'bucket':>10} | {'shortPnL':>8} | {'win%':>5} | {'stop%':>5} | n")
    short_buckets = []
    for lo, hi in buckets:
        grp = [(r, l) for (r, l) in labeled
               if r.get("mfcs") is not None and lo <= float(r["mfcs"]) < hi]
        if not grp:
            continue
        m, w, st, nn = _ssum(grp)
        name = f">={lo:.2f}" if hi > 1.0 else f"{lo:.2f}-{hi:.2f}"
        print(f"    {name:>10} | {m:>+7.2%} | {100*w:>4.0f}% | {100*st:>4.0f}% | {nn}")
        short_buckets.append({"bucket": name, "short_pnl": round(m, 4), "win": round(w, 3),
                              "stop_rate": round(st, 3), "n": nn})
    print(f"    worst single-name MFE (squeeze tail if the +{ss:.0%} stop slips/gaps): {worst_mfe:+.0%}")
    print("    ^ positive shortPnL + low stop% on the >=0.50/exhausted buckets = a tradeable")
    print("      fade-short. REAL-WORLD KILLERS: hard-to-borrow/no shares, squeeze gap-through")
    print("      the stop, borrow fees, bid-side fills. Quantify net before wiring (doc 200).")

    # doc 201: REALISTIC NET fade-short — ETB/shortable filter + squeeze gap-through +
    # frictions. The executable edge on names we can ACTUALLY borrow.
    short_realistic = None
    if args.realistic_short:
        gk, fr, bh = args.squeeze_gap_k, args.short_friction, args.borrow_haircut

        def _net_short(l):
            cover = min(l["mfe"], ss * gk) if l["mfe"] >= ss else None
            pnl = -cover if cover is not None else -l["ret_end"]
            return pnl - fr - bh

        n_etb = sum(1 for r, _l in labeled if short_ok.get(r["ticker"]))
        print(f"\n  REALISTIC NET FADE-SHORT (ETB-only; squeeze gap k={gk}, friction {fr:.1%}, "
              f"borrow {bh:.1%}):")
        print(f"    shortable/ETB: {n_etb}/{len(labeled)} ({100*n_etb/len(labeled):.0f}%) "
              f"— the rest can't be shorted at all")
        print(f"    {'bucket':>10} | {'%ETB':>5} | {'netShort':>8} | {'win%':>5} | n_ETB/n")
        short_realistic = {"shortable_pct": round(n_etb / len(labeled), 3), "squeeze_gap_k": gk,
                           "friction": fr, "borrow_haircut": bh, "by_bucket": []}
        for lo, hi in buckets:
            tot = [(r, l) for (r, l) in labeled
                   if r.get("mfcs") is not None and lo <= float(r["mfcs"]) < hi]
            grp = [(r, l) for (r, l) in tot if short_ok.get(r["ticker"])]
            name = f">={lo:.2f}" if hi > 1.0 else f"{lo:.2f}-{hi:.2f}"
            if not tot:
                continue
            if not grp:
                print(f"    {name:>10} | {'0%':>5} | {'n/a':>8} | {'n/a':>5} | 0/{len(tot)}")
                continue
            sp = [_net_short(l) for _r, l in grp]
            m = _mean(sp); w = sum(1 for x in sp if x > 0) / len(sp)
            print(f"    {name:>10} | {100*len(grp)/len(tot):>4.0f}% | {m:>+7.2%} | {100*w:>4.0f}% | "
                  f"{len(grp)}/{len(tot)}")
            short_realistic["by_bucket"].append({
                "bucket": name, "pct_etb": round(len(grp) / len(tot), 3),
                "net_short_pnl": round(m, 4), "win": round(w, 3), "n_etb": len(grp), "n": len(tot)})
        print("    ^ VERDICT: if the >=0.50 bucket stays POSITIVE after ETB+squeeze+friction AND")
        print("      enough of it is borrowable, the fade-short is live-wireable (D161, flag-gated).")

    print("\n  READ: features with sep>=~0.10 and a sane direction are selection-signal candidates")
    print("  (feed the 184 continuer / 188 detector). Cross-check vs doc-187: opening-RVOL/VWAP")
    print("  should separate; short-interest/float-rotation should NOT. Small n -> directional only.")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "dates": dates, "n": n, "ran": nw, "faded": nf, "base_rate": round(base_rate, 4),
            "run_threshold": args.run_threshold, "numeric_rankings": rankings,
            "boolean": bool_rows, "mfcs_buckets": mfcs_rows,
            "fade_short": {"cover_stop": ss, "whole_set_pnl": round(am, 4),
                           "whole_set_win": round(aw, 3), "stop_rate": round(ast_, 3),
                           "worst_mfe": round(worst_mfe, 4), "by_bucket": short_buckets},
            "fade_short_realistic": short_realistic,
        }, indent=2), encoding="utf-8")
        print(f"\n  JSON -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
