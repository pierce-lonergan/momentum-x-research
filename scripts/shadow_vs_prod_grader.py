"""doc 262 - SAME-DAY SHADOW-vs-PROD GRADER. Runs each evening: pulls what PROD traded + what every shadow
signalled today, fetches same-day outcomes via the Polygon REST open-close endpoint (available right after the
close, unlike the next-day S3 flat-file warehouse -> sidesteps the DataIngest lag), and grades whether the shadow
gates would have helped or hurt prod. Automates the doc-262 forensic (e.g. "GMHS: prod bought it and lost; the
Lottery meta-scorer SKIP-vetoed it" -> a shadow gate would have saved the loss). READ-ONLY. NO trading.

Usage: python scripts/shadow_vs_prod_grader.py [--date YYYY-MM-DD] [--discord]
"""
from __future__ import annotations
import os, re, sys, json, glob, argparse, datetime as dt, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def P(*a): return os.path.join(ROOT, *a)

def pkey():
    for f in [os.path.expanduser('~/momentum-x-secrets.env'), P('.env')]:
        if os.path.exists(f):
            for l in open(f, encoding='utf-8', errors='replace'):
                if l.startswith('POLYGON_API_KEY='): return l.split('=', 1)[1].strip().strip('"').strip("'")
    return os.environ.get('POLYGON_API_KEY')
KEY = pkey()

def webhook(var='OPS_ALERT_WEBHOOK_URL'):
    for f in [os.path.expanduser('~/momentum-x-secrets.env'), P('.env')]:
        if os.path.exists(f):
            for l in open(f, encoding='utf-8', errors='replace'):
                if l.startswith(var + '='): return l.split('=', 1)[1].strip().strip('"').strip("'")
    return None

def oc_ret(ticker, date):
    """Same-day open->close return from Polygon REST (available right after close)."""
    if not KEY: return None
    try:
        u = f"https://api.polygon.io/v1/open-close/{ticker}/{date}?adjusted=true&apiKey={KEY}"
        d = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'mx'}), timeout=15).read())
        if d.get('status') in ('OK', 'DELAYED') and d.get('open'):
            return d['close'] / d['open'] - 1
    except Exception:
        return None
    return None

def alpaca_keys():
    k = s = None
    for f in [os.path.expanduser('~/momentum-x-secrets.env'), P('.env')]:
        if os.path.exists(f):
            for l in open(f, encoding='utf-8', errors='replace'):
                if l.startswith('ALPACA_API_KEY='): k = l.split('=', 1)[1].strip().strip('"').strip("'")
                if l.startswith('ALPACA_SECRET_KEY='): s = l.split('=', 1)[1].strip().strip('"').strip("'")
        if k and s: break
    return k, s

def broker_filled_symbols(date):
    """doc 264: set of symbols with ANY broker fill on `date`. A journal BUY with no broker fill is a
    SUBMITTED-UNFILLED entry (the NPT class: dip-limits adversely select — rockets run away unfilled).
    Returns None if the broker is unreachable (grader then skips fill-awareness rather than mislabel)."""
    k, s = alpaca_keys()
    if not (k and s): return None
    syms = set(); tok = None
    try:
        for _ in range(20):
            q = dict(activity_types='FILL', page_size=100,
                     after=f'{date}T00:00:00Z', until=f'{date}T23:59:59Z')
            if tok: q['page_token'] = tok
            u = 'https://paper-api.alpaca.markets/v2/account/activities?' + urllib.parse.urlencode(q)
            req = urllib.request.Request(u, headers={'APCA-API-KEY-ID': k, 'APCA-API-SECRET-KEY': s, 'User-Agent': 'mx'})
            batch = json.loads(urllib.request.urlopen(req, timeout=25).read())
            if not batch: break
            syms |= {a['symbol'] for a in batch}
            tok = batch[-1].get('id')
            if len(batch) < 100: break
        return syms
    except Exception:
        return None

# ---------- PROD ----------
def prod_trades(date):
    js = sorted(glob.glob(P('data', 'journals', f'journal_{date}_*.jsonl')))
    if not js: return {}
    out = {}
    for l in open(js[-1], encoding='utf-8', errors='replace'):
        try: e = json.loads(l)
        except Exception: continue
        t = e.get('ticker')
        if not t or not e.get('action'): continue
        a = e.get('action')
        if a in ('BUY', 'SELL'):
            out[t] = dict(action=a, mfcs=e.get('mfcs'), exit_reason=e.get('exit_reason'),
                          pnl=e.get('realized_pnl') or e.get('realized_pnl_usd'))
    return out

def prod_eod(date):
    f = P('data', 'reports', f'eod_{date}.json')
    if not os.path.exists(f): return {}
    d = json.load(open(f, encoding='utf-8'))
    fs = d.get('sections', {}).get('eod_failsafes', {})
    return dict(broker_pnl=fs.get('broker_truth_recon', {}).get('broker_total_pnl'),
                recon_delta=fs.get('broker_truth_recon', {}).get('delta_usd'),
                ghosts=fs.get('force_close', {}).get('ghost_positions'),
                equity=d.get('metadata', {}).get('broker_equity_eod'))

# ---------- SHADOWS ----------
def lottery_signals(date):
    f = P('logs', f'lottery_{date}.log')
    if not os.path.exists(f): return {}, 'no log'
    txt = open(f, encoding='utf-8', errors='replace').read()
    sig = {}
    for m in re.finditer(r'META-\w+ (\w+): tier=(\w+) score=([\d.]+)', txt):
        sig[m.group(1)] = dict(tier=m.group(2), score=float(m.group(3)))
    kept = re.search(r'META-SCORER kept (\d+) of (\d+)', txt)
    summ = f"kept {kept.group(1)} of {kept.group(2)}" if kept else ('no picks' if 'No picks' in txt else '?')
    return sig, summ

def fader_signals(date):
    f = P('logs', f'fader_short_{date}.log')
    if not os.path.exists(f): return {}, 'no log'
    txt = open(f, encoding='utf-8', errors='replace').read()
    sig = {t: 'attempt' for t in re.findall(r'ORDER: SHORT \d+ (\w+) @', txt)}
    for t in re.findall(r'NOT SHORTABLE.*?\] (\w+)', txt): sig[t] = 'not_shortable'
    opened = re.search(r'Opened (\d+) short position', txt)
    return sig, (f"{len(sig)} cand, {sum(1 for v in sig.values() if v=='not_shortable')} not-shortable, opened {opened.group(1) if opened else '?'}")

def redflag_signals(date):
    f = P('data', 'research', 'rocket_watchlist_log.jsonl')
    if not os.path.exists(f): return {}, False
    rows = [json.loads(l) for l in open(f, encoding='utf-8') if l.strip()]
    day = {r['ticker']: r.get('redflag') for r in rows if r['date'] == date and r.get('redflag') is not None}
    has_today = any(r['date'] == date for r in rows)
    return day, has_today

def rocket_signals(date):
    f = P('data', 'research', 'rocket_shadow_log.jsonl')
    if not os.path.exists(f): return {}, False
    rows = [json.loads(l) for l in open(f, encoding='utf-8') if l.strip()]
    day = {r['ticker']: r.get('proba') or r.get('score') for r in rows if r.get('session_date') == date or r.get('date') == date}
    return day, bool(day)

# ---------- grade ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=dt.date.today().isoformat())
    ap.add_argument('--discord', action='store_true')
    a = ap.parse_args()
    date = a.date
    L = []
    def pr(s=''): L.append(s)

    pt = prod_trades(date); eod = prod_eod(date)
    lot, lot_sum = lottery_signals(date); fad, fad_sum = fader_signals(date)
    rf, rf_today = redflag_signals(date); rk, rk_today = rocket_signals(date)

    pr(f"SHADOW vs PROD GRADER — {date}")
    pr("=" * 56)
    bp = eod.get('broker_pnl')
    pr(f"PROD: broker P&L {'$%+.2f' % bp if bp is not None else '?'} | equity ${eod.get('equity') or 0:,.0f} | "
       f"ghosts force-closed {eod.get('ghosts')} | recon delta ${eod.get('recon_delta') or 0:+.2f}")
    buys = {t: v for t, v in pt.items() if v['action'] == 'BUY'}
    filled_syms = broker_filled_symbols(date)
    if filled_syms is not None:
        filled = {t: v for t, v in buys.items() if t in filled_syms}
        unfilled = {t: v for t, v in buys.items() if t not in filled_syms}
        pr(f"      prod journal BUYs: {len(buys)} | broker-FILLED: {len(filled)} ({', '.join(filled) or '-'}) | "
           f"SUBMITTED-UNFILLED: {len(unfilled)} ({', '.join(unfilled) or '-'})")
    else:
        filled, unfilled = buys, {}
        pr(f"      prod opened {len(buys)} long(s): {', '.join(buys) or '(none)'} (broker unreachable; fill-awareness off)")

    # doc 264: price the NPT class — submitted-unfilled entries that RAN (dip-limit adverse selection cost)
    if unfilled:
        missed = []
        for t in unfilled:
            oc = oc_ret(t, date)
            if oc is not None and oc > 0.10: missed.append((t, oc))
        if missed:
            pr("      UNFILLED-THAT-RAN (adverse-selection misses): " +
               ", ".join(f"{t} {oc*100:+.0f}%" for t, oc in sorted(missed, key=lambda x: -x[1])))

    pr("\n=== PROD TRADES vs SHADOW SIGNALS (same-day outcome via REST; FILLED entries only) ===")
    pr(f"  {'tk':<7}{'mfcs':>6}{'oc_ret':>9}  {'lottery':>14}  {'redflag':>8}  verdict")
    saved = 0.0; false_veto = 0.0; graded = 0
    for t, v in filled.items():
        oc = oc_ret(t, date)
        lo = lot.get(t); lo_s = f"{lo['tier']}@{lo['score']:.2f}" if lo else '-'
        r = rf.get(t)
        vetoed = bool(lo and lo['tier'] == 'SKIP') or (r is not None and r >= 0.6)
        oc_s = f"{oc*100:+.1f}%" if oc is not None else '?'
        verdict = ''
        if oc is not None and vetoed:
            graded += 1
            if oc < 0: verdict = 'SHADOW-VETO would have SAVED a loser'; saved += -oc
            else: verdict = 'shadow-veto would have MISSED a winner'; false_veto += oc
        mf = f"{v['mfcs']:.2f}" if isinstance(v.get('mfcs'), (int, float)) else '-'
        pr(f"  {t:<7}{mf:>6}{oc_s:>9}  {lo_s:>14}  {('%.2f'%r) if r is not None else '-':>8}  {verdict}")

    pr("\n=== SHADOWS ===")
    pr(f"  Lottery     : {lot_sum}" + (f" | SKIP'd prod-bought: {', '.join(t for t in buys if lot.get(t,{}).get('tier')=='SKIP') or '-'}" if lot else ''))
    pr(f"  FaderShort  : {fad_sum}")
    pr(f"  Red-flag    : {len(rf)} scored for {date}" + ("" if rf_today else " (LAGGED — warehouse has no " + date + " gappers yet)"))
    pr(f"  RocketShadow: {len(rk)} scored for {date}" + ("" if rk_today else " (LAGGED)"))

    # doc 269 A2: Verdict Lifecycle Ledger invariant + drop-reason economics (DORMANT-C).
    vll_path = P('data', 'ops', f'verdict_trace_{date}.jsonl')
    if os.path.exists(vll_path):
        events = []
        for l in open(vll_path, encoding='utf-8', errors='replace'):
            try:
                ev = json.loads(l)
                if not str(ev.get('stage', '')).startswith('SMOKE'):
                    events.append(ev)
            except Exception:
                continue
        terminals = {}
        reasons = {}
        for ev in events:
            terminals.setdefault(ev['ticker'], ev['stage'])
            if ev['stage'].startswith('BLOCKED'):
                reasons.setdefault(ev['stage'], set()).add(ev['ticker'])
        orphans = sorted(t for t in buys if t not in terminals and (filled_syms is None or t not in filled_syms))
        pr("\n=== VLL (doc 268/269): verdict lifecycle invariant ===")
        pr(f"  trace events: {len(events)} | verdict-tickers with terminals: {len(terminals)} | journal BUYs: {len(buys)}")
        if orphans:
            pr(f"  ORPHANS (journal BUY, no VLL terminal, no broker fill) — CRITICAL: {', '.join(orphans)}")
        else:
            pr("  orphan invariant: CLEAN (every silent death is now named)")
        for stage, tks in sorted(reasons.items()):
            outs = []
            for t in sorted(tks):
                oc = oc_ret(t, date)
                outs.append(f"{t} {oc*100:+.0f}%" if oc is not None else t)
            pr(f"  {stage}: {len(tks)} — " + ", ".join(outs))
            ran = [t for t in tks if (oc_ret(t, date) or 0) > 0.10]
            if ran:
                pr(f"    BLOCKED-THAT-RAN (the gate's cost today): {', '.join(ran)}")
    else:
        pr("\n=== VLL ===\n  no verdict trace for this date (pre-VLL session or bot did not run)")

    # doc 270 C2: B1 ledger-shadow three-way recon (LEDGER vs JOURNAL vs BROKER), never-raises.
    try:
        import subprocess as _sp
        _lr = _sp.run([sys.executable, P('scripts', 'ledger_shadow_recon.py'), '--date', date],
                      capture_output=True, text=True, timeout=120)
        if _lr.stdout.strip():
            pr("\n=== LEDGER SHADOW (doc 270 C2) ===")
            pr("  " + _lr.stdout.strip())
    except Exception as _le:
        pr(f"\n=== LEDGER SHADOW ===\n  recon failed: {str(_le)[:80]}")

    pr("\n=== HEADLINE ===")
    if graded:
        net = saved - false_veto
        pr(f"  Of prod's {graded} graded longs that a shadow gate flagged: veto would have AVOIDED "
           f"{saved*100:.1f}% of adverse moves vs MISSED {false_veto*100:.1f}% of favorable -> net {'+' if net>=0 else ''}{net*100:.1f}% selection improvement.")
    else:
        pr("  No prod longs today were both shadow-flagged AND outcome-gradeable (data lag or no overlap).")
    if not rf_today:
        pr("  NOTE: red-flag/rocket shadows lag a day (flat-file ingest); this grader fetches prod-trade outcomes "
           "live via REST, so PROD grading is same-day even when the warehouse isn't.")

    report = "\n".join(L)
    print(report)
    out = P('data', 'reports', f'shadow_vs_prod_{date}.txt')
    open(out, 'w', encoding='utf-8').write(report)
    if a.discord:
        wh = webhook()
        if wh:
            try:
                body = json.dumps({'content': f"```\n{report[:1900]}\n```"}).encode()
                req = urllib.request.Request(wh, data=body, method='POST',
                    headers={'Content-Type': 'application/json', 'User-Agent': 'MomentumX-Grader/1.0'})
                urllib.request.urlopen(req, timeout=20)
                print("[posted to Discord]")
            except Exception as e:
                print(f"[discord failed: {str(e)[:80]}]")

if __name__ == '__main__':
    main()
