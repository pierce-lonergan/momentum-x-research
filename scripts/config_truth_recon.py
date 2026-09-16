#!/usr/bin/env python
"""doc 285 gaps #6/#12: CONFIG-TRUTH RECON — the broker_truth_recon for CONFIG.

Doc-282 verified env->Settings binding OFFLINE; nothing verified Settings->
HOT-PATH behavior — so the doc-282 sizing knobs sat as dead letters while
D150 tier caps sized RIVN to 15.65% of equity on a 5% intent (2026-07-06).
This sentinel closes the loop nightly, READ-ONLY vs the live account:

  1. SIZING TRUTH   — join the session's FILLED entry orders (Alpaca orders
                      API, GET only) with equity at fill; per-fill
                      notional/equity vs the declared INTENT
                      (min EXEC_TIER*_POSITION_PCT; default-doc 0.05) and
                      risk-at-stop vs min KELLY_TIER*_RISK_PCT (default-doc
                      0.0067, the doc-285 corrected package).
  2. T2 TRUTH       — wide-arm fraction observed in the session log
                      ('ARM ASSIGNED', deduped per ticker) vs MOMENTUM_T2_WIDE_PCT.
  3. EXIT TRUTH     — exit_policy APPLIED (grep 'exit_policy=' in the log) vs
                      INTENDED. Intent comes from the repo .env FILE ONLY
                      (default bar1_legacy): a process/user-scope override is
                      exactly the invisible surface this sentinel exists to
                      expose (doc-285 gap #4), so it cannot bless itself.
  4. INVISIBLE SURFACE — enumerate Windows USER-scope env vars (HKCU\\Environment
                      via 'reg query' — os.environ won't show user scope from a
                      service) matching MOMENTUM_|EXEC_|KELLY_|EXIT_|TRAIL_ and
                      flag any not mirrored verbatim in the repo .env file.
  5. INSTRUMENT FRESHNESS (gap #12, the collector-dark-4-weeks class) —
                      rocket_gate_ledger.jsonl has the session's row;
                      posture_delta_trend.jsonl grew; kalshi shadow log +
                      rocket watchlist touched within 36h.

Output: ONE JSON line appended to data/reports/config_truth_recon.jsonl
(a NEW file the live bot never writes) + a loud '[CONFIG-DRIFT]' print per
breach. Never prints API keys. Wired into post_close_scorecard.py as a
best-effort block (the doc-282/284 pattern).

Usage:
    python scripts/config_truth_recon.py               # today (ET)
    python scripts/config_truth_recon.py 2026-07-06    # one date
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_ROOT = Path(__file__).resolve().parent.parent
_REPORTS = _ROOT / "data" / "reports"
_OUT = _REPORTS / "config_truth_recon.jsonl"
_ENV_FILE = _ROOT / ".env"

# Relative tolerance on the intent caps: fills legitimately chase a little
# above the eval-price anchor (doc-285 gap #9: a 5% cap can fill ~5.4%), so
# alarm only above intent*1.25 — 15.65% vs a 5% intent still fires LOUDLY,
# a 5.4% chase-fill on a 5% intent does not.
_REL_TOL = 1.25
# doc-285 declared intents when the env carries nothing (the corrected package).
_DEFAULT_POSITION_PCT = 0.05
_DEFAULT_RISK_PCT = 0.0067
# T2 wide-fraction: binomial noise is huge at session n; only alarm on gross
# divergence (e.g. wide arm silently disabled -> observed 0.0 vs intent 0.75).
_T2_MIN_N = 4
_T2_BAND = 0.35
_FRESH_HOURS = 36.0
_ENV_PREFIX_RE = re.compile(r"^(MOMENTUM_|EXEC_|KELLY_|EXIT_|TRAIL_)")
_SECRETY_RE = re.compile(r"KEY|SECRET|TOKEN|PASS", re.IGNORECASE)


# ── env-file / intent parsing (pure; unit-tested) ────────────────────────────

def parse_env_file(text: str) -> dict[str, str]:
    """Parse KEY=value lines the way python-dotenv does for our .env style:
    skip comments/blank lines, strip unquoted inline ' # ...' comments."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if val[:1] in ("'", '"') and val.count(val[0]) >= 2:
            val = val[1:val.index(val[0], 1)]
        else:
            val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
        out[key] = val
    return out


def _float_or_none(raw: str | None) -> float | None:
    try:
        return float(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def intent_position_pct(env: dict[str, str]) -> float:
    """min of the declared EXEC_TIER*_POSITION_PCT; default-doc 0.05."""
    vals = [_float_or_none(env.get(f"EXEC_TIER{i}_POSITION_PCT")) for i in (1, 2, 3)]
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else _DEFAULT_POSITION_PCT


def intent_risk_pct(env: dict[str, str]) -> float:
    """min of the declared KELLY_TIER*_RISK_PCT; default-doc 0.0067."""
    vals = [_float_or_none(env.get(f"KELLY_TIER{i}_RISK_PCT")) for i in (1, 2, 3, 4)]
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else _DEFAULT_RISK_PCT


def intent_t2_wide(env: dict[str, str]) -> float:
    """MOMENTUM_T2_WIDE_PCT normalized like t2_arm_assignment (fraction or %)."""
    v = _float_or_none(env.get("MOMENTUM_T2_WIDE_PCT"))
    if v is None:
        return 0.50
    if v > 1.0:
        v /= 100.0
    return max(0.0, min(1.0, v))


def intent_exit_policy(env: dict[str, str]) -> str:
    v = (env.get("MOMENTUM_EXIT_POLICY") or "").strip().lower()
    return v if v in ("bar1_legacy", "t1_next_open") else "bar1_legacy"


# ── breach math (pure; unit-tested) ──────────────────────────────────────────

def evaluate_fill(fill: dict, intent_pos: float, intent_risk: float,
                  rel_tol: float = _REL_TOL) -> dict:
    """One filled entry -> sizing-truth verdict.

    fill: {symbol, qty, price, equity, stop_price(None ok)}. Returns the fill
    annotated with notional_pct / risk_at_stop_pct + a breaches list.
    """
    sym = fill["symbol"]
    qty = float(fill["qty"])
    price = float(fill["price"])
    equity = float(fill["equity"])
    stop = fill.get("stop_price")
    breaches: list[str] = []

    notional_pct = qty * price / equity if equity > 0 else float("inf")
    if notional_pct > intent_pos * rel_tol:
        breaches.append(
            f"POSITION_PCT {sym}: filled {notional_pct:.2%} of equity vs intent "
            f"{intent_pos:.2%} (tol x{rel_tol})")

    risk_pct = None
    if stop is None:
        breaches.append(f"NO_STOP_FOUND {sym}: no protective stop visible at the "
                        f"broker for this entry (risk-at-stop unverifiable)")
    else:
        stop = float(stop)
        risk_pct = qty * max(price - stop, 0.0) / equity if equity > 0 else float("inf")
        if risk_pct > intent_risk * rel_tol:
            breaches.append(
                f"RISK_PCT {sym}: risk-at-stop {risk_pct:.2%} of equity vs intent "
                f"{intent_risk:.2%} (stop {stop:g} vs fill {price:g}, tol x{rel_tol})")

    return {"symbol": sym, "qty": qty, "price": price, "equity": round(equity, 2),
            "stop_price": stop, "notional_pct": round(notional_pct, 5),
            "risk_at_stop_pct": round(risk_pct, 5) if risk_pct is not None else None,
            "breaches": breaches}


_ARM_RE = re.compile(r"ARM ASSIGNED (\S+): arm=(\w+)")


def parse_arm_assignments(log_text: str) -> dict[str, str]:
    """'ARM ASSIGNED <TICKER>: arm=<arm>' lines -> {ticker: arm}. Assignment is
    sticky per (date, ticker) so repeats across cycles dedupe to one row."""
    return {m.group(1): m.group(2) for m in _ARM_RE.finditer(log_text)}


def check_wide_fraction(assignments: dict[str, str], intent: float,
                        min_n: int = _T2_MIN_N, band: float = _T2_BAND) -> dict:
    n = len(assignments)
    wide = sum(1 for a in assignments.values() if a == "wide_stop")
    obs = wide / n if n else None
    breach = (n >= min_n and obs is not None and abs(obs - intent) > band)
    return {"n": n, "wide": wide, "observed": round(obs, 3) if obs is not None else None,
            "intent": intent,
            "breaches": ([f"T2_WIDE_FRACTION: observed {obs:.0%} wide (n={n}) vs "
                          f"intent {intent:.0%} (band {band})"] if breach else [])}


_POLICY_RE = re.compile(r"exit_policy=(\w+)")


def parse_exit_policies(log_text: str) -> list[str]:
    return sorted({m.group(1) for m in _POLICY_RE.finditer(log_text)})


def check_exit_policy(observed: list[str], intended: str) -> dict:
    """Drift when a policy OBSERVED in the log differs from the file-declared
    intent. No observations = no bar-1-skip lines = consistent with bar1_legacy
    (weak evidence only; recorded, never alarmed)."""
    drift = [p for p in observed if p != intended]
    return {"observed": observed, "intended": intended,
            "breaches": ([f"EXIT_POLICY: log shows exit_policy={'/'.join(drift)} "
                          f"but declared intent is {intended} (doc-280: overnight "
                          f"carry is the one robustly-negative arm)"] if drift else [])}


def parse_reg_query(reg_output: str) -> dict[str, str]:
    """'reg query HKCU\\Environment' stdout -> {name: value} for the momentum
    prefixes. Lines look like: '    NAME    REG_SZ    value'."""
    out: dict[str, str] = {}
    for line in reg_output.splitlines():
        m = re.match(r"^\s{2,}(\S+)\s+(REG_(?:EXPAND_)?SZ|REG_DWORD)\s+(.*)$", line)
        if m and _ENV_PREFIX_RE.match(m.group(1)):
            out[m.group(1)] = m.group(3).strip()
    return out


def check_user_scope(hkcu: dict[str, str], env_file: dict[str, str]) -> dict:
    """Any user-scope var not mirrored VERBATIM in the repo .env file is an
    invisible surface (doc-285 gap #4: MOMENTUM_EXIT_POLICY lived only in
    HKCU, contradicting doc-280, invisible to file greps)."""
    breaches = []
    rows = {}
    for name, val in sorted(hkcu.items()):
        shown = "<redacted>" if _SECRETY_RE.search(name) else val
        mirrored = env_file.get(name) == val
        rows[name] = {"value": shown, "mirrored_in_env_file": mirrored}
        if not mirrored:
            breaches.append(
                f"USER_SCOPE_ENV {name}={shown}: live from HKCU\\Environment but "
                f"not mirrored in .env -- invisible to file greps (mirror it or "
                f"clear it: [Environment]::SetEnvironmentVariable('{name}',$null,'User'))")
    return {"vars": rows, "breaches": breaches}


def check_instruments(root: Path, date: str, now_utc: float,
                      fresh_hours: float = _FRESH_HOURS) -> dict:
    """gap #12: the doc-283/284 instruments must not dark-run (the
    collector-dark-4-weeks class)."""
    def _mtime_fresh(p: Path) -> tuple[bool, str]:
        if not p.exists():
            return False, "missing"
        age_h = (now_utc - p.stat().st_mtime) / 3600.0
        return age_h <= fresh_hours, f"age {age_h:.1f}h"

    def _date_row(p: Path) -> dict | None:
        """Return the row object for `date`, or None. (doc 287: callers must inspect the row's
        MEASUREMENT fields, not just its existence -- a present-but-dark row is not 'fresh'.)"""
        if not p.exists():
            return None
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("date") == date:
                        return obj
                except Exception:
                    continue
        except Exception:
            return None
        return None

    def _has_date_row(p: Path) -> bool:
        return _date_row(p) is not None

    checks = {}
    # doc 287: the OLD check reported rocket_gate 'fresh' whenever a row for the date existed -- but
    # every ran-day row (7/6, 7/7, 7/9) was measurement-dark (`n_filled=0`, `no_bar_data=true`,
    # 100% unmeasured), so the sentinel was blind to the exact collector-dark-4-weeks failure it was
    # built to catch (gap #12). A row is only fresh if it actually MEASURED something.
    p = root / "data" / "reports" / "rocket_gate_ledger.jsonl"
    _rg = _date_row(p)
    if _rg is None:
        _rg_fresh, _rg_detail = False, f"no row for {date}"
    else:
        _filled = int(_rg.get("n_filled") or 0)
        _gated = int(_rg.get("n_gated") or 0)
        _no_bars = bool(_rg.get("no_bar_data"))
        _measured = _filled > 0 and not _no_bars
        _rg_fresh = _measured
        _rg_detail = (f"row for {date}: n_gated={_gated} n_filled={_filled} no_bar_data={_no_bars} "
                      f"-> {'MEASURED' if _measured else 'DARK (present but 0 measured)'}")
    checks["rocket_gate_ledger"] = {"fresh": _rg_fresh, "detail": _rg_detail}
    p = root / "data" / "reports" / "posture_delta_trend.jsonl"
    grew = _has_date_row(p) or _mtime_fresh(p)[0]
    checks["posture_delta_trend"] = {"fresh": grew, "detail": _mtime_fresh(p)[1]}
    p = root / "logs" / "kalshi_shadow_doc284.log"
    ok, det = _mtime_fresh(p)
    checks["kalshi_shadow"] = {"fresh": ok, "detail": det}
    p = root / "data" / "research" / "rocket_watchlist_log.jsonl"
    ok, det = _mtime_fresh(p)
    checks["rocket_watchlist"] = {"fresh": ok, "detail": det}
    # doc 292: the RV forward shadow-ledger — measurement-aware (a row must have actually SCORED names,
    # the doc-287 deep-freshness lesson). Sessions land T+1 (self-repairing), so freshness = a measured
    # row for the date OR a recent mtime (the append may lag one session legitimately).
    p = root / "data" / "reports" / "rv_forward_ledger.jsonl"
    _rvrow = _date_row(p)
    _rv_measured = bool(_rvrow and int(_rvrow.get("n_names") or 0) >= 30)
    _rv_fresh = _rv_measured or _mtime_fresh(p)[0]
    checks["rv_forward_ledger"] = {"fresh": _rv_fresh,
                                   "detail": (f"row for {date}: n_names={_rvrow.get('n_names')}" if _rvrow
                                              else f"no row for {date}; {_mtime_fresh(p)[1]}")}

    breaches = [f"INSTRUMENT_DARK {name}: {c['detail']} (fail-loud per doc-285 #12)"
                for name, c in checks.items() if not c["fresh"]]
    return {"checks": checks, "breaches": breaches}


# ── live IO (Alpaca GET-only; keys never printed) ────────────────────────────

def _alpaca_keys() -> tuple[str | None, str | None, str]:
    k = s = None
    base = "https://paper-api.alpaca.markets"
    for f in [os.path.expanduser("~/momentum-x-secrets.env"), str(_ENV_FILE)]:
        if os.path.exists(f):
            env = parse_env_file(Path(f).read_text(encoding="utf-8", errors="replace"))
            k = k or env.get("ALPACA_API_KEY")
            s = s or env.get("ALPACA_SECRET_KEY")
            base = env.get("ALPACA_BASE_URL") or base
        if k and s:
            break
    return k, s, base.rstrip("/")


def _get(path: str, params: dict | None = None):
    k, s, base = _alpaca_keys()
    u = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(u, headers={
        "APCA-API-KEY-ID": k or "", "APCA-API-SECRET-KEY": s or "",
        "User-Agent": "mx-config-truth-recon"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _parse_ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def fetch_session_fills(date: str) -> list[dict]:
    """FILLED entry (side=buy) orders for the ET session date, each with the
    entry-time protective stop: the OTO stop leg when present (the intent at
    entry, regardless of its later cancel), else the LOWEST same-day sell stop
    for the symbol (worst-case risk — the conservative read), else None."""
    day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=_ET)
    orders = _get("/v2/orders", {
        "status": "all", "limit": 500, "nested": "true", "direction": "asc",
        "after": day.isoformat(), "until": (day + timedelta(days=1)).isoformat()})
    if len(orders) >= 500:
        print("  (warn: order page full at 500 -- session view may be truncated)")

    stops_by_sym: dict[str, list[float]] = {}
    for o in orders:
        if o.get("side") == "sell" and o.get("type") in ("stop", "stop_limit", "trailing_stop"):
            sp = _float_or_none(o.get("stop_price"))
            if sp is not None:
                stops_by_sym.setdefault(o["symbol"], []).append(sp)

    fills = []
    for o in orders:
        if o.get("side") != "buy" or float(o.get("filled_qty") or 0) <= 0:
            continue
        if not o.get("filled_at") or not o.get("filled_avg_price"):
            continue
        stop = None
        for leg in (o.get("legs") or []):
            if leg.get("type") in ("stop", "stop_limit") and leg.get("stop_price"):
                stop = float(leg["stop_price"])
                break
        if stop is None and stops_by_sym.get(o["symbol"]):
            stop = min(stops_by_sym[o["symbol"]])
        fills.append({"symbol": o["symbol"], "qty": float(o["filled_qty"]),
                      "price": float(o["filled_avg_price"]),
                      "filled_at": o["filled_at"], "filled_epoch": _parse_ts(o["filled_at"]),
                      "stop_price": stop, "order_id": o.get("id")})
    return fills


def fetch_equity_curve(date: str) -> tuple[list[int], list[float]]:
    days_back = (datetime.now(_ET).date()
                 - datetime.strptime(date, "%Y-%m-%d").date()).days
    period = "1W" if days_back <= 5 else ("1M" if days_back <= 28 else "3M")
    ph = _get("/v2/account/portfolio/history", {"period": period, "timeframe": "5Min"})
    ts = ph.get("timestamp") or []
    eq = ph.get("equity") or []
    return ts, eq


def equity_at(fill_epoch: float, ts: list[int], eq: list[float]) -> float | None:
    best = None
    for t, e in zip(ts, eq):
        if e and t <= fill_epoch:
            best = float(e)
        elif t > fill_epoch:
            break
    if best is None:
        try:
            best = float(_get("/v2/account").get("last_equity"))
        except Exception:
            best = None
    return best


# ── runner ───────────────────────────────────────────────────────────────────

def run(date: str) -> dict:
    env_file = parse_env_file(_ENV_FILE.read_text(encoding="utf-8", errors="replace")) \
        if _ENV_FILE.exists() else {}
    # INTENT is the file-declared truth (greppable, reviewed); process env is
    # consulted only where the file is silent — a session-env override must
    # never be able to bless its own drift.
    merged = {**{k: v for k, v in os.environ.items() if _ENV_PREFIX_RE.match(k)},
              **env_file}
    i_pos, i_risk = intent_position_pct(merged), intent_risk_pct(merged)
    i_wide, i_policy = intent_t2_wide(merged), intent_exit_policy(env_file)

    breaches: list[str] = []
    row: dict = {"date": date, "generated_utc": datetime.now(timezone.utc)
                 .strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "intent": {"position_pct": i_pos, "risk_pct": i_risk,
                            "t2_wide_pct": i_wide, "exit_policy": i_policy}}

    # 1) sizing truth vs the broker
    try:
        fills = fetch_session_fills(date)
        ts, eq = fetch_equity_curve(date)
        evaluated = []
        for f in fills:
            equity = equity_at(f["filled_epoch"], ts, eq)
            if equity is None:
                breaches.append(f"NO_EQUITY_REF {f['symbol']}: could not resolve "
                                f"equity at fill -- sizing unverifiable")
                continue
            ev = evaluate_fill({**f, "equity": equity}, i_pos, i_risk)
            ev["filled_at"] = f["filled_at"]
            evaluated.append(ev)
            breaches.extend(ev["breaches"])
        row["fills"] = evaluated
    except Exception as e:  # noqa: BLE001 — recon must always produce its row
        row["fills_error"] = str(e)[:200]
        breaches.append(f"RECON_BLIND fills: broker read failed ({str(e)[:120]}) -- "
                        f"sizing truth UNVERIFIED for {date}")

    # 2+3) session-log truth
    log_file = _ROOT / "logs" / f"momentum_{date}.log"
    if log_file.exists():
        text = log_file.read_text(encoding="utf-8", errors="replace")
        t2 = check_wide_fraction(parse_arm_assignments(text), i_wide)
        pol = check_exit_policy(parse_exit_policies(text), i_policy)
        row["t2"], row["exit_policy"] = t2, pol
        breaches.extend(t2["breaches"])
        breaches.extend(pol["breaches"])
    else:
        row["log_missing"] = str(log_file)
        breaches.append(f"RECON_BLIND log: {log_file.name} missing -- T2/exit-policy "
                        f"truth UNVERIFIED for {date}")

    # 4) the invisible surface (user-scope registry env)
    try:
        r = subprocess.run(["reg", "query", r"HKCU\Environment"],
                           capture_output=True, text=True, timeout=30)
        us = check_user_scope(parse_reg_query(r.stdout or ""), env_file)
        row["user_scope_env"] = us["vars"]
        breaches.extend(us["breaches"])
    except Exception as e:  # noqa: BLE001 — non-Windows / locked-down hosts
        row["user_scope_env_error"] = str(e)[:120]

    # 5) instrument freshness
    inst = check_instruments(_ROOT, date, time.time())
    row["instruments"] = inst["checks"]
    breaches.extend(inst["breaches"])

    row["breaches"] = breaches
    row["n_breaches"] = len(breaches)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=None,
                    help="session date YYYY-MM-DD (default: today ET)")
    ap.add_argument("--no-append", action="store_true",
                    help="print only; skip the jsonl append")
    args = ap.parse_args()
    date = args.date or datetime.now(_ET).strftime("%Y-%m-%d")

    row = run(date)

    for b in row["breaches"]:
        print(f"[CONFIG-DRIFT] {b}")
    if not args.no_append:
        _REPORTS.mkdir(parents=True, exist_ok=True)
        with _OUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    print(f"CONFIG-TRUTH {date}: {row['n_breaches']} breach(es), "
          f"{len(row.get('fills', []))} entry fill(s) checked -> {_OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
