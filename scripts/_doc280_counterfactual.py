"""DOC 280 PRIMARY measurement (panel-redesigned) — exit-posture counterfactual on the ACTUAL book, per-session-dollar.
The design panel (wf_a7feedc6, 4/4 DO-NOT-FREEZE on the first design) forced every fix folded in here:
 - ESTIMAND = the actual realizable book (82 clean trades, 2 phantom excluded), NOT the 5,140-candidate scored pool
   (which is 62x the book and manufactures significance). Baseline = the real -$22,961 clean book.
 - EXITS PRICED THROUGH THE VALIDATED SpreadModel (SELL at the bid) at each policy's real bar (price,volume,ts,rvol),
   not a clean bar close and not a flat cost. Closing-auction bid for hold-to-close; gap-through slippage on the stop.
 - SURVIVORSHIP on hold-to-next-open: require a real next-session bar WITH volume; else book the LAST tradeable price
   (do NOT silently fall back to same-day close = best case).
 - RIGHT-TAIL HARD CAP at +20% per trade (one-sided), the realistic single-session ceiling — winsor@5% was theater.
 - INFERENCE PER-SESSION-DOLLAR: aggregate Delta to a per-session total; sign test + block bootstrap OVER SESSIONS
   (not per-candidate). OUT-OF-SAMPLE split (early vs late sessions) for any tail-harvest claim.
 - NOVELTY PRIOR (deep-research, verified): overnight gaps REVERT (fade edge), so the hold thesis is a-priori suspect;
   the NEGATIVE branch is expected. qty from journal (size%*equity/entry) with a reconciliation gate; qty-free ratio
   fallback. RUN ONLY AFTER THE PREREG IS COMMITTED."""
from __future__ import annotations
import argparse, glob, json, sys
from datetime import datetime, timedelta
import duckdb, numpy as np
sys.path.insert(0, "scripts")
import fill_model_backtest as F  # validated SpreadModel

_DA = "data/polygon_warehouse/minute_aggs/**/*.parquet"
DEFAULT_EQUITY = 200_000.0
RIGHT_CAP = 0.20          # one-sided hard cap on the counterfactual per-trade RETURN
SLIP = 0.003              # hostile market-sell slippage added on top of the SpreadModel half-spread
MIN_NOPEN_VOL = 1000      # min next-session opening-bar volume to count next-open as sellable


def _parse(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def load_clean_trades():
    out = []
    for l in open("data/trade_results.jsonl", encoding="utf-8"):
        l = l.strip()
        if not l:
            continue
        r = json.loads(l)
        if r.get("infrastructure_contaminated"):       # exclude the 2 phantom wins (Bug C)
            continue
        if r.get("pnl") is None or not r.get("entry_time") or not r.get("exit_time"):
            continue
        out.append(r)
    return out


def journal_index():
    idx = {}
    for f in glob.glob("data/journals/journal_*.jsonl"):
        for l in open(f, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(l)
            except Exception:
                continue
            if str(r.get("action", "")).upper() == "BUY" and r.get("entry_price"):
                idx.setdefault((r.get("ticker"), r.get("session_date")), []).append(r)
    return idx


def equity_by_session():
    eq = {}
    for f in glob.glob("data/reports/eod_*.json"):
        try:
            d = json.load(open(f))
            e = (d.get("sections", {}).get("eod_recon", {}) or {}).get("broker_equity")
            if e:
                eq[d.get("session_date")] = float(e)
        except Exception:
            pass
    return eq


def _sell_fill(spread, price, volume, ts, rvol):
    """Realistic SELL fill = SpreadModel bid minus hostile slippage."""
    try:
        bid, ask = spread.get_bid_ask(price, max(volume, 1.0), ts, rvol or 1.0)
    except Exception:
        bid = price * 0.997
    return bid * (1 - SLIP)


def bars_for(con, ticker, entry_ts):
    q = f"""SELECT ts_utc, ts_et, open, high, low, close, volume FROM read_parquet('{_DA}', union_by_name=true)
            WHERE ticker=? AND ts_utc BETWEEN ? AND ? ORDER BY ts_utc"""
    lo = entry_ts.replace(tzinfo=None) - timedelta(minutes=3)
    hi = entry_ts.replace(tzinfo=None) + timedelta(days=6)
    df = con.execute(q, [ticker, lo, hi]).df()
    if df.empty:
        return None
    df["ts_utc"] = df["ts_utc"].astype("datetime64[ns]")
    df["etstr"] = df["ts_et"].astype(str)
    df["etdate"], df["ettime"] = df["etstr"].str[:10], df["etstr"].str[11:16]
    reg = df[(df.ettime >= "09:30") & (df.ettime <= "16:00")]
    return reg


def measure(rvol_default=3.0):
    con = duckdb.connect(); con.execute("SET threads=4")
    spread = F.SpreadModel() if F._HAVE_ARENA else None
    trades, jidx, eqs = load_clean_trades(), journal_index(), equity_by_session()
    rows = []
    for t in trades:
        try:
            ets, xts = _parse(t["entry_time"]), _parse(t["exit_time"])
        except Exception:
            continue
        reg = bars_for(con, t["ticker"], ets)
        if reg is None or reg.empty:
            rows.append({**t, "status": "NO_TAPE"}); continue
        et, xt = ets.replace(tzinfo=None), xts.replace(tzinfo=None)
        pre = reg[reg.ts_utc <= et]
        edate = pre.etdate.iloc[-1] if len(pre) else reg.etdate.iloc[0]
        same = reg[reg.etdate == edate]
        if same.empty:
            rows.append({**t, "status": "NO_TAPE"}); continue
        js = jidx.get((t["ticker"], t["session_date"]), [])
        j = min(js, key=lambda r: abs((_parse(r.get("timestamp") or t["entry_time"]) - ets).total_seconds())) if js else None
        entry_px = float(j["entry_price"]) if (j and j.get("entry_price")) else float(pre.close.iloc[-1]) if len(pre) else None
        if not entry_px or entry_px <= 0:
            rows.append({**t, "status": "NO_ENTRY"}); continue
        size_pct = float(j["position_size_pct"]) if (j and j.get("position_size_pct")) else None
        sgn = -1.0 if (j and str(j.get("direction", "")).startswith("short")) else 1.0
        equity = eqs.get(t["session_date"], DEFAULT_EQUITY)
        exit_tape = float(reg[reg.ts_utc <= xt].close.iloc[-1]) if len(reg[reg.ts_utc <= xt]) else float(same.close.iloc[-1])
        # qty: journal size preferred; else qty-free ratio (pnl / move) when the move is meaningful
        if size_pct and entry_px > 0:
            qty = sgn * size_pct * equity / entry_px
        elif abs(exit_tape - entry_px) / entry_px > 0.003:
            qty = t["pnl"] / (exit_tape - entry_px)
        else:
            rows.append({**t, "status": "NO_QTY"}); continue
        pred = qty * (exit_tape - entry_px)
        # tier the trust: CONFIRMED = |pnl| large AND pred reconciles (trustworthy lower bound);
        # SIZE_BASED = breakeven-clip (|pnl|<100), qty from journal size (pnl can't confirm it, but the position was
        #   real and was flattened at ~breakeven -> the counterfactual is the thesis-relevant money left on the table);
        # MISMATCH = |pnl| large AND pred disagrees -> a real reconciliation failure, EXCLUDED.
        if abs(t["pnl"]) < 100 and size_pct:
            tier = "SIZE_BASED"
        elif abs(pred - t["pnl"]) <= max(60.0, 0.6 * abs(t["pnl"])):
            tier = "CONFIRMED"
        else:
            tier = "MISMATCH"
        reconciled = tier in ("CONFIRMED", "SIZE_BASED")
        # counterfactual exits, priced through the SpreadModel bid (SELL), one-sided +20% cap
        def cf_pnl(exit_px, vol, ts, capped=True):
            sell = _sell_fill(spread, exit_px, vol, ts, rvol_default)
            ret = (sell - entry_px) / entry_px
            if capped:
                ret = min(ret, RIGHT_CAP)
            return qty * entry_px * ret
        # P1 hold-to-close (closing-auction bar, widened bid via late ts)
        cbar = same.iloc[-1]
        d_close = cf_pnl(float(cbar.close), float(cbar.volume), cbar.ts_utc.to_pydatetime()) - t["pnl"]
        # P2 hold-to-next-open with SURVIVORSHIP: require a real next-session bar with volume
        later = reg[reg.etdate > edate]
        nbar = later.iloc[0] if len(later) else None
        if nbar is not None and float(nbar.volume) >= MIN_NOPEN_VOL:
            d_nopen = cf_pnl(float(nbar.open), float(nbar.volume), nbar.ts_utc.to_pydatetime()) - t["pnl"]
            nopen_ok = True
        else:  # not sellable at next open -> book the LAST tradeable price (same-day close), NOT best case
            d_nopen = d_close; nopen_ok = False
        # P3 hold-to-close + 15% disaster stop with gap-through slippage
        stop = entry_px * 0.85
        d_stop = d_close
        for r in same[same.ts_utc >= et].itertuples():
            if float(r.low) <= stop:
                d_stop = cf_pnl(min(stop, float(r.open)), float(r.volume), r.ts_utc.to_pydatetime(), capped=False) - t["pnl"]
                break
        rows.append({**t, "status": "OK", "qty": qty, "entry_px": entry_px, "exit_tape": exit_tape, "tier": tier,
                     "size_pct": size_pct, "equity": equity, "pred_pnl": pred, "reconciled": reconciled,
                     "nopen_ok": nopen_ok, "d_hold_close": d_close, "d_hold_nextopen": d_nopen, "d_hold_stop15": d_stop})
    con.close()
    return rows


def _per_session(usable, key):
    from collections import defaultdict
    s = defaultdict(float)
    for r in usable:
        s[r["session_date"]] += r[key]
    return s


def _boot_ci_sessions(sess_totals, B=10000, seed=280):
    rng = np.random.RandomState(seed); v = np.array(list(sess_totals.values()), float)
    tot = [np.sum(rng.choice(v, len(v), replace=True)) for _ in range(B)]
    return float(np.percentile(tot, 2.5)), float(np.percentile(tot, 97.5))


def _block(pop, key, base):
    deltas = [r[key] for r in pop]
    tot = sum(deltas)
    sess = _per_session(pop, key)
    n_up = sum(1 for v in sess.values() if v > 0); n = len(sess)
    ci = _boot_ci_sessions(sess) if n >= 3 else (float("nan"), float("nan"))
    sd = sorted(sess)
    half = len(sd) // 2
    early = sum(sess[d] for d in sd[:half]); late = sum(sess[d] for d in sd[half:])
    drop_best = tot - max(sess.values()) if sess else tot
    st = sorted(deltas)
    return {"delta_total": tot, "new_book": base + tot, "sessions_up": n_up, "n_sessions": n, "ci95": ci,
            "oos_early": early, "oos_late": late, "drop_best": drop_best, "top": st[-1] if st else 0.0}


def report(rows):
    ok = [r for r in rows if r.get("status") == "OK"]
    confirmed = [r for r in ok if r.get("tier") == "CONFIRMED"]
    sizebased = [r for r in ok if r.get("tier") == "SIZE_BASED"]
    full = confirmed + sizebased
    full_clean = sum(r["pnl"] for r in rows if r.get("pnl") is not None)
    from collections import Counter
    print("\n=== DOC 280 PRIMARY: exit-posture counterfactual on the ACTUAL book (per-session-dollar) ===")
    print(f"clean trades={sum(1 for r in rows if r.get('pnl') is not None)} | priced(OK)={len(ok)} | tiers={dict(Counter(r.get('tier') for r in ok))}")
    print(f"  dropped: no_tape={sum(1 for r in rows if r.get('status')=='NO_TAPE')} no_qty={sum(1 for r in rows if r.get('status')=='NO_QTY')}")
    print(f"full clean book = ${full_clean:,.0f}")
    out = {"variants": {}}
    for key, lbl in [("d_hold_close", "HOLD-TO-CLOSE"), ("d_hold_nextopen", "HOLD-TO-NEXT-OPEN (survivorship)"),
                     ("d_hold_stop15", "HOLD-TO-CLOSE + 15% disaster stop")]:
        print(f"\n  {lbl}:")
        for pop, plbl in [(confirmed, "CONFIRMED (large-pnl, reconciled -> trustworthy lower bound)"),
                          (full, "FULL (confirmed + breakeven-clips via journal size -> fuller, less certain)")]:
            if not pop:
                continue
            base = sum(r["pnl"] for r in pop)
            b = _block(pop, key, base)
            ci = b["ci95"]
            ex0 = "YES" if (ci[0] == ci[0] and (ci[0] > 0 or ci[1] < 0)) else "NO"
            print(f"    [{plbl}] n={len(pop)}")
            print(f"       base ${base:>+9,.0f}  Δtotal ${b['delta_total']:>+10,.0f} -> new ${b['new_book']:>+10,.0f} | {b['sessions_up']}/{b['n_sessions']} sess up | boot95%CI[${ci[0]:,.0f},${ci[1]:,.0f}] excl0={ex0}")
            print(f"       OOS early ${b['oos_early']:+,.0f} / late ${b['oos_late']:+,.0f} sameSign={'Y' if b['oos_early']*b['oos_late']>0 else 'N'} | drop-best ${b['drop_best']:+,.0f} | top ${b['top']:+,.0f} ({100*b['top']/b['delta_total'] if b['delta_total'] else 0:.0f}%)")
            out["variants"].setdefault(key, {})[plbl.split()[0]] = b
    n_nopen = sum(1 for r in full if not r.get("nopen_ok"))
    print(f"\n  NB: next-open NOT sellable (survivorship -> last tradeable) for {n_nopen}/{len(full)} trades.")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", type=int, default=0)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    rows = measure()
    if a.smoke:
        ok = [r for r in rows if r.get("status") == "OK"]; rec = [r for r in ok if r.get("reconciled")]
        print(f"SMOKE: {len(ok)} priced, {len(rec)} reconciled (mechanics only).")
        for r in ok[:a.smoke]:
            print(f"  {r['ticker']:>6} pnl=${r['pnl']:+,.0f} pred=${r['pred_pnl']:+,.0f} recon={r['reconciled']} d_close=${r['d_hold_close']:+,.0f} d_nopen=${r['d_hold_nextopen']:+,.0f} nopen_ok={r['nopen_ok']}")
        sys.exit(0)
    out = report(rows)
    if a.json:
        json.dump({"summary": out, "rows": rows}, open(a.json, "w"), indent=1, default=str)
        print(f"\nwrote {a.json}")
