#!/usr/bin/env python
"""doc 192/195/196: Fill-realism backtest — quantify the marketable-limit change (189/190).

WHY: every other sim assumes a perfect fill at the eval price, so 189/190 is invisible.
This forward-models PASSIVE (limit at eval price) vs MARKETABLE (189/190) fills on real
historical BUY candidates and measures the difference — and isolates the SELECTION signal
(where the BUY set goes), which doc 192 found dominates fills.

FIDELITY LADDER:
  - doc 192: a LOW<=limit fill proxy, return = gross capture to a fixed +exit-min close.
  - doc 195 (default): the arena AlpacaFillModel (Alpaca's real rule: a limit buy fills
    only when limit>=ask, AT the ask) + ~45s submission staleness (the marketable limit
    anchored to the ask AT SUBMISSION). The real reason CMND filled 0/2.
  - doc 196 (this): EXIT-AWARE NET P&L. The gross "capture to +60min" ignored stops,
    D122 exits, and the hold-to-EOD. Now each fill is replayed through the SAME D122 exit
    machinery the live bot uses (reused from phase3_replay_simulator: stop-loss, the 6
    parallel exit strategies w/ upgrade-only semantics, EOD close). NET P&L caps the
    downside at the stop and times the upside on D122 signals -> the realistic number.

train==serve: PROD compute_marketable_limit + the phase3 D122ExitEvaluator (mirrors
exit_intelligence.py). Spread: arena Polygon-calibrated SpreadModel. Bars: alpaca get_bars.

CAVEATS (stated): current_price as the eval anchor; modeled NBBO (spread model, not real
quotes); the protective stop is a config-% approximation (--stop-pct), and tranche
partial-sells are NOT modeled (single-exit: stop / D122 / EOD). The SELECTION line is
robust to all of these.

Usage:
    python scripts/fill_model_backtest.py 2026-05-29                 # realistic + net P&L
    python scripts/fill_model_backtest.py 2026-05-29 --no-net-pnl    # gross sweep only
    python scripts/fill_model_backtest.py 2026-05-29 --fidelity proxy --submission-delay-sec 0
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from random import Random
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parent.parent
_HERE = Path(__file__).resolve().parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
_ARENA = _ROOT / "mx-arena"
if _ARENA.exists() and str(_ARENA) not in sys.path:
    sys.path.insert(0, str(_ARENA))

from config.settings import Settings  # noqa: E402
from src.data.alpaca_client import AlpacaDataClient  # noqa: E402
from src.execution.alpaca_executor import compute_marketable_limit  # noqa: E402

try:
    from arena.spread_model import SpreadModel  # noqa: E402
    from arena.fill_model import AlpacaFillModel, Bar  # noqa: E402
    _HAVE_ARENA = True
except Exception:  # pragma: no cover
    _HAVE_ARENA = False

try:  # doc 196: reuse the live-parity D122 exit machinery
    from phase3_replay_simulator import (  # noqa: E402
        SimPosition, SimBar, D122ExitEvaluator, replay_position,
    )
    _HAVE_REPLAY = True
except Exception:  # pragma: no cover
    _HAVE_REPLAY = False

_WINDOWS = [1, 2, 5, 15, 60]
_FETCH_MIN = 400  # fetch ~to EOD so the net-P&L replay can run to the close


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return 0.0
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def _load_buy_candidates(date: str) -> list[dict]:
    f = _ROOT / "data" / "features" / f"features_{date}.jsonl"
    if not f.exists():
        return []
    out = []
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
        price = r.get("current_price") or r.get("entry_price")
        ts = r.get("timestamp")
        if not price or not ts or price <= 0:
            continue
        out.append({"ticker": r.get("ticker"), "ts": ts, "price": float(price),
                    "mfcs": r.get("mfcs"), "rvol": r.get("rvol") or 1.0,
                    "gap": r.get("gap_pct") or 0.0})
    return out


async def _eval_one(client, c: dict, exit_min: int) -> dict | None:
    """Fetch the forward path to ~EOD; store (off,lo,op,hi,cl,vol) bars + the +exit_min
    close (gross horizon). The net-P&L replay uses the full bar list."""
    ticker, price = c["ticker"], c["price"]
    try:
        ts0 = _parse_ts(c["ts"])
    except Exception:
        return None
    try:
        bars = await client.get_bars(
            ticker, timeframe="1Min",
            start=ts0.strftime("%Y-%m-%dT%H:%M:%SZ"),
            end=(ts0 + timedelta(minutes=_FETCH_MIN)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            limit=500,
        )
    except Exception:
        bars = None
    if not bars:
        return None
    trimmed, p_end60 = [], None
    for b in bars:
        t = b.get("t") or b.get("timestamp")
        if t is None:
            continue
        bt = _parse_ts(t) if isinstance(t, str) else t
        off = (bt - ts0).total_seconds() / 60.0
        if off < 0:
            continue
        lo = b.get("l", b.get("low")); op = b.get("o", b.get("open"))
        hi = b.get("h", b.get("high")); cl = b.get("c", b.get("close"))
        vol = b.get("v", b.get("volume")) or 0
        if lo is None:
            continue
        op = float(op) if op is not None else float(lo)
        hi = float(hi) if hi is not None else max(float(lo), op)
        cl = float(cl) if cl is not None else op
        trimmed.append((off, float(lo), op, hi, cl, float(vol)))
        if off <= exit_min:
            p_end60 = cl
    if not trimmed:
        return None
    if p_end60 is None:
        p_end60 = trimmed[0][4]
    return {"ticker": ticker, "price": price, "mfcs": c.get("mfcs"),
            "rvol": float(c.get("rvol") or 1.0), "gap": float(c.get("gap") or 0.0),
            "ts0": ts0, "p_end": p_end60, "bars": trimmed}


def _quote(spread, mid, vol, ts0, rvol):
    if spread is not None:
        try:
            return spread.get_bid_ask(mid, vol, ts0, rvol)
        except Exception:
            pass
    return round(mid * 0.997, 4), round(mid * 1.003, 4)


def _submission(bars, sub_off, spread, ts0, rvol):
    for i, (off, lo, op, hi, cl, vol) in enumerate(bars):
        if off >= sub_off:
            _bid, ask = _quote(spread, cl or op or lo, vol, ts0, rvol)
            return i, ask
    return None, None


def _fill(r, limit, w, sub_off, fidelity, spread, fill_model, rng):
    """Return (filled, fill_price, fill_off) for a BUY limit live from sub_off, within
    sub_off+w."""
    bars, ts0, rvol = r["bars"], r["ts0"], r["rvol"]
    for (off, lo, op, hi, cl, vol) in bars:
        if off < sub_off:
            continue
        if off > sub_off + w:
            break
        if fidelity == "arena" and fill_model is not None:
            bid, ask = _quote(spread, cl or op or lo, vol, ts0, rvol)
            order = SimpleNamespace(qty=100, filled_qty=0, type="limit", side="buy",
                                    limit_price=limit, stop_price=None,
                                    stop_triggered=False, hwm=None, trail_percent=None)
            bar = Bar(timestamp="", open=op, high=hi, low=lo, close=cl, volume=int(vol))
            f = fill_model.try_fill(order, bar, bid, ask, rng)
            if f:
                return True, f.price, off
        else:
            if lo <= limit:
                return True, max(min(limit, op), lo), off
    return False, 0.0, None


def _limits(r, sub_off, spread, exec_cfg):
    _i, ask_sub = _submission(r["bars"], sub_off, spread, r["ts0"], r["rvol"])
    passive = r["price"]
    marketable = compute_marketable_limit(
        side="buy", entry_price=r["price"], quote_price=ask_sub, mfcs=r["mfcs"],
        base_offset=getattr(exec_cfg, "marketable_base_offset_pct", 0.004),
        max_offset=getattr(exec_cfg, "marketable_max_offset_pct", 0.015),
        mfcs_full_at=getattr(exec_cfg, "marketable_mfcs_full_at", 0.55),
    )
    return passive, marketable


def _atr_proxy(fwd, fill_px):
    rngs = [hi - lo for (off, lo, op, hi, cl, vol) in fwd[:14] if hi >= lo]
    a = (sum(rngs) / len(rngs)) if rngs else fill_px * 0.03
    return max(a, fill_px * 0.005)


def _replay_tranche(fwd, fill_px, stop_pct, t):
    """doc 196d: scale-out exit — sell 1/3 at T1, 1/3 at T2, the FINAL 1/3 RIDES the D163
    trail (runner). Hard stop on un-sold qty (ratchets to breakeven after T1); EOD closes
    the remainder. Captures the runner upside the single-exit D122 model misses (MOBX +95%
    vs single-exit +6%). Returns (net_pnl_frac, exit_reason). Convention: tranche targets
    (resting limits) fill on the bar HIGH first, then the protective stop on the LOW."""
    entry = fill_px
    remaining = 1.0
    realized = 0.0
    hard_stop = entry * (1 - stop_pct)
    t1, t2 = entry * (1 + t.t1), entry * (1 + t.t2)
    sold1 = sold2 = False
    peak = entry
    last_close = fwd[-1][4] if fwd else entry
    reason = "EOD"
    for (off, lo, op, hi, cl, vol) in fwd:
        if hi > peak:
            peak = hi
        peak_gain = (peak - entry) / entry
        # 1. tranche scale-out (limit-style: fills when the high reaches the target)
        if not sold1 and remaining > 0 and hi >= t1:
            sell = min(1 / 3, remaining); realized += sell * (t1 - entry) / entry
            remaining -= sell; sold1 = True
            hard_stop = max(hard_stop, entry)  # floor to breakeven after T1
        if not sold2 and remaining > 0 and hi >= t2:
            sell = min(1 / 3, remaining); realized += sell * (t2 - entry) / entry
            remaining -= sell; sold2 = True
        # 2. protective exit on the remainder (D163 trail once active, else hard stop)
        if remaining > 0:
            protect, rsn = hard_stop, "STOP"
            if peak_gain >= t.activation:
                give = min(max(t.trail_pct_of_gain * peak_gain, t.min_trail), t.max_trail)
                ts = peak * (1 - give)
                if ts > protect:
                    protect, rsn = ts, "TRAIL"
            if lo <= protect:
                realized += remaining * (protect - entry) / entry
                remaining = 0.0
                reason = rsn + ("_RUNNER" if (sold1 or sold2) else "")
                break
    if remaining > 0:
        realized += remaining * (last_close - entry) / entry
        reason = "EOD_RUNNER" if (sold1 or sold2) else "EOD"
    return realized, reason


def _net_for_fill(r, limit, w, sub_off, fidelity, spread, fill_model, rng, evaluator, stop_pct, tcfg):
    """Exit-aware NET P&L for one fill under BOTH exit models. Returns a dict (or None if
    unfilled): {fill_px, d122_pnl, d122_reason, hold, tr_pnl, tr_reason}.
    d122 = single-exit intelligence (phase3 replay); tranche = scale-out + runner trail."""
    filled, fill_px, fill_off = _fill(r, limit, w, sub_off, fidelity, spread, fill_model, rng)
    if not filled or fill_px <= 0:
        return None
    fwd = [b for b in r["bars"] if b[0] >= fill_off]
    if len(fwd) < 2:
        return {"fill_px": fill_px, "d122_pnl": 0.0, "d122_reason": "NO_BARS",
                "hold": 0, "tr_pnl": 0.0, "tr_reason": "NO_BARS"}
    stop = round(fill_px * (1 - stop_pct), 4)
    # d122 single-exit (reuse the live-parity machinery)
    d122_pnl, d122_reason, hold = 0.0, "REPLAY_ERR", 0
    try:
        pos = SimPosition(
            ticker=r["ticker"], entry_price=fill_px,
            entry_time=r["ts0"] + timedelta(minutes=fill_off), qty=100,
            stop_loss=stop, atr=_atr_proxy(fwd, fill_px), gap_pct=r.get("gap", 0.0),
        )
        simbars = [SimBar(timestamp=r["ts0"] + timedelta(minutes=off), open=op, high=hi,
                          low=lo, close=cl, volume=vol)
                   for (off, lo, op, hi, cl, vol) in fwd]
        _legacy, d122 = replay_position(pos, simbars, evaluator, stop, stop)
        d122_pnl, d122_reason, hold = d122.pnl_pct / 100.0, d122.exit_reason, d122.hold_minutes
    except Exception:
        pass
    # tranche + runner
    tr_pnl, tr_reason = _replay_tranche(fwd, fill_px, stop_pct, tcfg)
    return {"fill_px": fill_px, "d122_pnl": d122_pnl, "d122_reason": d122_reason,
            "hold": hold, "tr_pnl": tr_pnl, "tr_reason": tr_reason}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=None)
    ap.add_argument("--exit-min", type=int, default=60, help="gross-capture horizon")
    ap.add_argument("--submission-delay-sec", type=int, default=45,
                    help="staleness: order live this long after eval (doc 195). 0 = none.")
    ap.add_argument("--fidelity", choices=["arena", "proxy"], default="arena")
    ap.add_argument("--net-pnl", dest="net_pnl", action="store_true", default=True,
                    help="exit-aware NET P&L via the D122 replay (doc 196, default ON)")
    ap.add_argument("--no-net-pnl", dest="net_pnl", action="store_false")
    ap.add_argument("--net-window", type=int, default=2,
                    help="fill window (min) used for the net-P&L replay (the bot's real ~120s)")
    ap.add_argument("--stop-pct", type=float, default=0.055,
                    help="protective stop %% below fill for the replay (config approx)")
    ap.add_argument("--max", type=int, default=150)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    date = args.date
    if not date:
        files = sorted(glob.glob(str(_ROOT / "data" / "features" / "features_2026-*.jsonl")))
        if not files:
            print("No features files found."); return 1
        date = Path(files[-1]).stem.replace("features_", "")

    cands = _load_buy_candidates(date)
    if not cands:
        print(f"No BUY candidates in features_{date}.jsonl"); return 0
    # doc 214 BUGFIX (same as doc 213 selection_study): the old `cands[:max]` truncated by
    # FILE/TIME order -> a cap kept only the EARLIEST evals of the day (time-of-day bias).
    # Fix: evenly-spaced (time-representative) sample; args.max<=0 means NO CAP.
    if args.max and args.max > 0 and len(cands) > args.max:
        print(f"NOTE: stratified-sampling {len(cands)} BUY candidates to {args.max} (API budget).")
        stride = len(cands) / args.max
        cands = [cands[int(i * stride)] for i in range(args.max)]

    settings = Settings()
    client = AlpacaDataClient(settings.alpaca)
    fidelity = args.fidelity
    spread = fill_model = None
    if _HAVE_ARENA:
        spread = SpreadModel()
        fill_model = AlpacaFillModel()
    elif fidelity == "arena":
        print("WARN: arena models unavailable -> --fidelity proxy.")
        fidelity = "proxy"
    rng = Random(42)
    sub_off = args.submission_delay_sec / 60.0

    results = []
    try:
        for i in range(0, len(cands), 6):
            got = await asyncio.gather(*[_eval_one(client, c, args.exit_min) for c in cands[i:i+6]])
            results.extend([g for g in got if g])
    finally:
        close = getattr(client, "aclose", None) or getattr(client, "close", None)
        if close:
            try:
                res = close()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass

    n = len(results)
    if not n:
        print(f"No candidates had forward bars for {date}."); return 0

    for r in results:
        r["_passive"], r["_mkt"] = _limits(r, sub_off, spread, settings.execution)

    end_rets = [(r["p_end"] - r["price"]) / r["price"] for r in results if r["price"] > 0]
    sel_up = sum(1 for x in end_rets if x > 0)
    sel_mean = _mean(end_rets)
    sel_median = _median(end_rets)
    sel_win = (sel_up / len(end_rets)) if end_rets else 0.0
    print(f"\n=== Fill-realism backtest {date} (fidelity={fidelity}, submit+{args.submission_delay_sec}s, n={n}) ===")
    print(f"  SELECTION (gross, eval->+{args.exit_min}min): win% {100*sel_win:.0f} "
          f"({sel_up}/{len(end_rets)} up) | median {sel_median:+.2%} | mean {sel_mean:+.2%} "
          f"<-- trust WIN%/MEDIAN; the mean is outlier-skewed by rare small-cap rockets.")

    print(f"\n  GROSS FILL-WINDOW SWEEP (capture -> +{args.exit_min}min close, miss=0):")
    print(f"  {'win':>4} | {'passiveFill':>11} | {'mktFill':>8} | {'caught':>6} | "
          f"{'passiveNet':>10} | {'mktNet':>8} | {'EDGE':>7} | caught_fwd")
    win_rows = []
    for w in _WINDOWS:
        p_n = m_n = caught = 0
        caught_rets, p_caps, m_caps = [], [], []
        for r in results:
            pf, ppx, _ = _fill(r, r["_passive"], w, sub_off, fidelity, spread, fill_model, rng)
            mf, mpx, _ = _fill(r, r["_mkt"], w, sub_off, fidelity, spread, fill_model, rng)
            p_caps.append((r["p_end"] - ppx) / ppx if (pf and ppx > 0) else 0.0)
            m_caps.append((r["p_end"] - mpx) / mpx if (mf and mpx > 0) else 0.0)
            if pf: p_n += 1
            if mf: m_n += 1
            if mf and not pf:
                caught += 1
                caught_rets.append((r["p_end"] - mpx) / mpx if mpx > 0 else None)
        p_net, m_net = _mean(p_caps), _mean(m_caps)
        cf = _mean(caught_rets) if caught_rets else None
        win_rows.append({"w": w, "passive_fill": p_n, "mkt_fill": m_n, "caught": caught,
                         "passive_net": round(p_net, 4), "mkt_net": round(m_net, 4),
                         "edge": round(m_net - p_net, 4),
                         "caught_fwd": round(cf, 4) if cf is not None else None})
        cstr = f"{cf:+.1%}" if cf is not None else "n/a"
        print(f"  {w:>4} | {p_n:>4}/{n} {100*p_n/n:>3.0f}% | {m_n:>3}/{n} {100*m_n/n:>3.0f}% | "
              f"{caught:>6} | {p_net:>+9.2%} | {m_net:>+7.2%} | {m_net-p_net:>+6.2%} | {cstr}")

    # ── doc 196 + 196d: exit-aware NET P&L under TWO exit models ──
    net_summary = None
    if args.net_pnl and _HAVE_REPLAY:
        _ei = getattr(settings, "exit_intelligence", None) or getattr(settings, "exit", None)
        min_conf = getattr(_ei, "parallel_exit_min_confidence", 0.85) if _ei else 0.85
        min_strat = getattr(_ei, "parallel_min_strategies_for_exit", 2) if _ei else 2
        evaluator = D122ExitEvaluator(min_confidence=min_conf, min_strategies_for_exit=min_strat)
        _ec = settings.execution
        _tr = getattr(settings, "trailing_stop", None) or getattr(settings, "trailing", None)
        tcfg = SimpleNamespace(
            t1=getattr(_ec, "tranche_t1_pct", 0.03), t2=getattr(_ec, "tranche_t2_pct", 0.08),
            t3=getattr(_ec, "tranche_t3_pct", 0.10),
            activation=getattr(_tr, "activation_threshold_pct", 0.06) if _tr else 0.06,
            trail_pct_of_gain=getattr(_tr, "trail_pct_of_gain", 0.30) if _tr else 0.30,
            min_trail=getattr(_tr, "min_trail_distance_pct", 0.05) if _tr else 0.05,
            max_trail=getattr(_tr, "max_trail_distance_pct", 0.35) if _tr else 0.35,
        )
        nw = args.net_window
        net_c = {("p", "d122"): [], ("m", "d122"): [], ("p", "tranche"): [], ("m", "tranche"): []}
        real = {("p", "d122"): [], ("m", "d122"): [], ("p", "tranche"): [], ("m", "tranche"): []}
        reasons = {"d122": Counter(), "tranche": Counter()}
        holds = []; p_fn = m_fn = 0
        for r in results:
            pf = _net_for_fill(r, r["_passive"], nw, sub_off, fidelity, spread, fill_model, rng, evaluator, args.stop_pct, tcfg)
            mf = _net_for_fill(r, r["_mkt"], nw, sub_off, fidelity, spread, fill_model, rng, evaluator, args.stop_pct, tcfg)
            for side, fr in (("p", pf), ("m", mf)):
                for model, key in (("d122", "d122_pnl"), ("tranche", "tr_pnl")):
                    net_c[(side, model)].append(fr[key] if fr else 0.0)  # miss = 0
                    if fr:
                        real[(side, model)].append(fr[key])
            if pf: p_fn += 1
            if mf:
                m_fn += 1
                reasons["d122"][mf["d122_reason"] or "?"] += 1
                reasons["tranche"][mf["tr_reason"] or "?"] += 1
                if mf["hold"] is not None: holds.append(mf["hold"])
        print(f"\n  NET P&L (exit-aware; fill window {nw}min, stop {args.stop_pct:.1%}):")
        print(f"    fills: marketable {m_fn}/{n}, passive {p_fn}/{n} | avg hold {_mean(holds):.0f}min")
        print(f"    {'model':>8} | {'mktRealized':>11} | {'mktMed':>7} | {'mktWin%':>7} | "
              f"{'passiveReal':>11} | {'NETedge':>8}")
        net_summary = {"net_window_min": nw, "stop_pct": args.stop_pct,
                       "marketable_filled": m_fn, "passive_filled": p_fn,
                       "avg_hold_min": round(_mean(holds), 1), "models": {}}
        for model in ("d122", "tranche"):
            mr = real[("m", model)]
            mreal, mmed = _mean(mr), _median(mr)
            mwin = (sum(1 for x in mr if x > 0) / len(mr)) if mr else 0.0
            preal = _mean(real[("p", model)])
            m_net, p_net = _mean(net_c[("m", model)]), _mean(net_c[("p", model)])
            print(f"    {model:>8} | {mreal:>+10.2%} | {mmed:>+6.2%} | {100*mwin:>6.0f}% | "
                  f"{preal:>+10.2%} | {m_net-p_net:>+7.2%}")
            net_summary["models"][model] = {
                "marketable_realized_mean": round(mreal, 4), "marketable_realized_median": round(mmed, 4),
                "marketable_win_rate": round(mwin, 4), "passive_realized_mean": round(preal, 4),
                "net_edge_per_candidate": round(m_net - p_net, 4),
                "marketable_net_per_candidate": round(m_net, 4),
                "passive_net_per_candidate": round(p_net, 4),
            }
        for model in ("d122", "tranche"):
            rtot = sum(reasons[model].values()) or 1
            rstr = ", ".join(f"{k} {100*v/rtot:.0f}%" for k, v in reasons[model].most_common(4))
            print(f"    {model} exit mix (mkt): {rstr}")
        print("    ^ d122 = single-exit intelligence; tranche = scale-out (T1/T2) + a RUNNER third on")
        print("      the D163 trail (recovers the runner upside d122 misses). Truth is bracketed by the two.")
    elif args.net_pnl and not _HAVE_REPLAY:
        print("\n  (net P&L skipped: phase3_replay_simulator not importable)")

    print("\n  READ: NET P&L is the realistic number (stops cap fader losses; D122/EOD time the")
    print("  exit). SELECTION (gross) is the lever; the marketable NET EDGE is its fill multiplier.")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "date": date, "n": n, "fidelity": fidelity,
            "submission_delay_sec": args.submission_delay_sec, "exit_min": args.exit_min,
            "selection_mean_ret": round(sel_mean, 4),
            "selection_median_ret": round(sel_median, 4),
            "selection_win_rate": round(sel_win, 4), "selection_up": sel_up,
            "windows": win_rows, "net_pnl": net_summary,
        }, indent=2), encoding="utf-8")
        print(f"\n  JSON summary -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
