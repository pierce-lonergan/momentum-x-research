#!/usr/bin/env python3
"""usage_audit.py -- doc 270 D1 (DORMANT-C): paid-service usage census from logs.

Log-derived, per-day, per-provider usage census for the MOMENTUM-X stack.
READ-ONLY over logs/ + data/journals/ + data/research/ + data/alerts/.
Writes ONLY data/ops/usage_audit_<date>.json (one per audited date).

Providers covered:
  TOGETHER AI : LLM calls by model/tier (LiteLLM lines), ensemble outcomes,
                D99 29s-cancels, circuit-breaker trips, EMPTY-data evals,
                journal-derived per-agent latency p50/p95.
  POLYGON     : main bot = none (uses Alpaca data; verified no api.polygon.io
                in src/). Flat-file ingest (data_ingest log), fader-short
                snapshot, shadow-grader open-close REST, rocket-watchlist
                (local warehouse only -- counted as re-processed days).
  ALPACA      : screener calls, scan iterations, order sub/fill (dashboard),
                recon equity polls, failed cancels (301 bug), 403s, CB trips.
  EDGAR       : search requests + failure count (efts.sec.gov 500 storm).
  FINNHUB     : company-news errors, preflight earnings check, calendar use.
  DISCORD     : measurable post markers + failed-post spool leftovers
                (exact success count UNMEASURABLE -- durable_post is silent).

Philosophy: NEVER raises. Every parser is wrapped; absent files/signals
degrade to counts of 0 with an explicit "unmeasurable" note, not a guess.

Usage:
  python scripts/usage_audit.py                      # today
  python scripts/usage_audit.py --date 2026-06-09    # one session
  python scripts/usage_audit.py --dates 2026-06-03 2026-06-04 ...  # census run

Re-runnable nightly (idempotent; overwrites its own JSON for the date).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date as date_cls
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOGS = REPO / "logs"
OUT_DIR = REPO / "data" / "ops"

# ---------------------------------------------------------------- helpers

def _say(msg: str = "") -> None:
    """cp1252-safe print (Windows console)."""
    try:
        print(msg)
    except Exception:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


def read_text(path: Path) -> str:
    """Read a log file tolerantly. Returns '' when missing/unreadable.

    PowerShell `*>>` appends UTF-16LE blobs into otherwise-UTF8 cron logs
    (every char NUL-padded). Stripping NULs restores the ASCII content.
    """
    try:
        raw = path.read_bytes()
    except Exception:
        return ""
    try:
        raw = raw.replace(b"\x00", b"")
        return raw.decode("utf-8", "replace")
    except Exception:
        return ""


def pctl(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def lat_stats(vals: list[float]) -> dict:
    vals = sorted(v for v in vals if isinstance(v, (int, float)) and v >= 0)
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "p50_ms": round(pctl(vals, 0.50) or 0),
        "p95_ms": round(pctl(vals, 0.95) or 0),
        "max_ms": round(vals[-1]),
    }


MODEL_TIER = [
    ("Qwen3.5-397B", "tier1_primary"),
    ("Qwen3-235B", "tier2_primary"),
    ("DeepSeek-V3.1", "fallback_deepseek(DEAD-400)"),
    ("Llama-3.3-70B", "fallback_llama"),
    ("Qwen2.5-7B", "fallback_qwen7b"),
]


def tier_of(model: str) -> str:
    for frag, tier in MODEL_TIER:
        if frag in model:
            return tier
    return "other"


# ---------------------------------------------------------------- momentum log

def parse_momentum(d: str) -> dict:
    """Main bot log: Together/EDGAR/Finnhub/Alpaca/Discord signals."""
    out: dict = {"log_found": False}
    txt = read_text(LOGS / f"momentum_{d}.log")
    if not txt:
        out["note"] = f"UNMEASURABLE: logs/momentum_{d}.log missing or empty"
        return out
    out["log_found"] = True
    lines = txt.splitlines()

    llm_by_model: Counter = Counter()
    ens_groups: Counter = Counter()          # agent -> group count
    ens_lat: dict[str, list[float]] = defaultdict(list)
    ens_member_fail = 0
    d99: Counter = Counter()                 # agent -> cancels
    d99_wait_s: list[float] = []
    cb_trips: Counter = Counter()            # breaker -> trips
    cb_causes: Counter = Counter()
    empty_evals = 0
    empty_agent_slots: Counter = Counter()
    dq = Counter()                           # complete/partial/empty agent-slots
    dq_evals = 0
    edgar_fail = 0
    edgar_status: Counter = Counter()
    finn_err = 0
    finn_err_status: Counter = Counter()
    screener_most_active = 0
    screener_movers = 0
    scan_iters = 0
    failed_cancel_301 = 0
    forbidden_403 = 0
    eod_recon_equity = 0
    discord_markers = 0
    manip_results = 0
    phase_a = 0
    orders_sub = orders_fill = None
    d92_fb_warn = d92_fb_success = d92_all_failed = 0

    re_llm = re.compile(r"LiteLLM completion\(\) model= (\S+);")
    re_ens = re.compile(r"D202 ENSEMBLE (\w+) \S+: (\d+)/(\d+) calls .*\[(\d+)ms\]")
    re_d99 = re.compile(r"D99: Cancelled slow agent (\w+) \(still pending after phase-1 (\d+(?:\.\d+)?)s\)")
    re_cb = re.compile(r"Circuit breaker '(\w+)' TRIPPED.*?last_failure=(\S+)?")
    re_empty = re.compile(r"EMPTY data for agents: ([^——]+)")
    re_dq = re.compile(r"DATA QUALITY: complete=(\d+) partial=(\d+) empty=(\d+)")
    re_edgar = re.compile(r"SEC EDGAR search failed for \S+: .*?'(\d{3})?")
    re_finn = re.compile(r"Finnhub news fetch failed: ?(.*)")
    re_orders = re.compile(r"Orders: ([\d.]+) sub / ([\d.]+) fill")

    for ln in lines:
        try:
            m = re_llm.search(ln)
            if m:
                llm_by_model[m.group(1)] += 1
                continue
            m = re_ens.search(ln)
            if m:
                agent, ok, total, ms = m.group(1), int(m.group(2)), int(m.group(3)), float(m.group(4))
                ens_groups[agent] += 1
                ens_lat[agent].append(ms)
                ens_member_fail += max(0, total - ok)
                continue
            m = re_d99.search(ln)
            if m:
                d99[m.group(1)] += 1
                d99_wait_s.append(float(m.group(2)))
                continue
            if "Circuit breaker" in ln and "TRIPPED" in ln:
                m = re_cb.search(ln)
                if m:
                    cb_trips[m.group(1)] += 1
                    cause = (m.group(2) or "unknown").split(":")[0]
                    cb_causes[f"{m.group(1)}:{cause}"] += 1
                continue
            if "EMPTY data for agents:" in ln:
                empty_evals += 1
                m = re_empty.search(ln)
                if m:
                    for a in m.group(1).split(","):
                        a = a.strip()
                        if a:
                            empty_agent_slots[a] += 1
                continue
            m = re_dq.search(ln)
            if m:
                dq_evals += 1
                dq["complete"] += int(m.group(1))
                dq["partial"] += int(m.group(2))
                dq["empty"] += int(m.group(3))
                continue
            if "SEC EDGAR search failed" in ln:
                edgar_fail += 1
                m = re_edgar.search(ln)
                code = m.group(1) if (m and m.group(1)) else "conn/other"
                edgar_status[code] += 1
                continue
            if "Finnhub news fetch failed" in ln:
                finn_err += 1
                m = re_finn.search(ln)
                detail = (m.group(1) or "").strip() if m else ""
                code = "empty/timeout"
                cm = re.search(r"'(\d{3})", detail)
                if cm:
                    code = cm.group(1)
                finn_err_status[code] += 1
                continue
            if "most-active tickers" in ln and "Screener returned" in ln:
                screener_most_active += 1
                continue
            if "Movers screener returned" in ln:
                screener_movers += 1
                continue
            if "Scan iteration:" in ln:
                scan_iters += 1
                continue
            if "Failed to cancel order :" in ln and "301" in ln:
                failed_cancel_301 += 1
                continue
            if "403 Forbidden" in ln:
                forbidden_403 += 1
            if "EOD RECON EQUITY" in ln:
                eod_recon_equity += 1
                continue
            if "Discord alert posted" in ln or "discord_notify" in ln:
                discord_markers += 1
                continue
            if "D106 MANIPULATION:" in ln:
                manip_results += 1
                continue
            if "D92: Phase A complete" in ln:
                phase_a += 1
                continue
            if "primary failed for" in ln and "D92" in ln:
                d92_fb_warn += 1
                continue
            if "SUCCESS for" in ln and ("FALLBACK" in ln or "EMERGENCY" in ln):
                d92_fb_success += 1
                continue
            if "models failed for" in ln and "ALL" in ln:
                d92_all_failed += 1
                continue
            m = re_orders.search(ln)
            if m:
                orders_sub, orders_fill = float(m.group(1)), float(m.group(2))
        except Exception:
            continue  # never-raises: skip any malformed line

    by_tier: Counter = Counter()
    for model, n in llm_by_model.items():
        by_tier[tier_of(model)] += n

    out.update({
        "together": {
            "llm_calls_total": sum(llm_by_model.values()),
            "by_model": dict(llm_by_model),
            "by_tier": dict(by_tier),
            "ensemble_groups_by_agent": dict(ens_groups),
            "ensemble_group_latency_ms": {a: lat_stats(v) for a, v in ens_lat.items()},
            "ensemble_member_failures": ens_member_fail,
            "d99_cancelled_by_agent": dict(d99),
            "d99_cancelled_total": sum(d99.values()),
            "d99_avg_burn_s": (round(sum(d99_wait_s) / len(d99_wait_s), 1) if d99_wait_s else None),
            "circuit_breaker_trips": dict(cb_trips),
            "circuit_breaker_causes": dict(cb_causes),
            "d92_fallback_warnings": d92_fb_warn,
            "d92_fallback_success": d92_fb_success,
            "d92_all_models_failed": d92_all_failed,
            "empty_data_evals": empty_evals,
            "empty_agent_slots": dict(empty_agent_slots),
            "data_quality_evals": dq_evals,
            "data_quality_slots": dict(dq),
            "manipulation_results": manip_results,
            "phase_a_completions": phase_a,
        },
        "edgar": {
            "search_failures": edgar_fail,
            "failure_status": dict(edgar_status),
            "search_successes": "UNMEASURABLE: sec_client logs errors only; "
                                "zero non-error sec_client lines observed = likely 100% failure",
        },
        "finnhub": {
            "company_news_errors": finn_err,
            "error_status": dict(finn_err_status),
            "successful_calls": "UNMEASURABLE: news_client logs failures only; "
                                "est ~1 company-news call per eval news-fetch",
        },
        "alpaca": {
            "screener_most_active_calls": screener_most_active,
            "screener_movers_calls": screener_movers,
            "scan_iterations": scan_iters,
            "orders_submitted_dashboard": orders_sub,
            "orders_filled_dashboard": orders_fill,
            "failed_cancel_301_bug": failed_cancel_301,
            "http_403_forbidden": forbidden_403,
            "eod_recon_equity_polls": eod_recon_equity,
            "cb_trips_alpaca_rest": cb_trips.get("alpaca_rest", 0),
            "position_polls": "UNMEASURABLE: per-call position GETs not logged "
                              "(only outcomes); monitor loop ~30s cadence",
        },
        "discord_markers_in_log": discord_markers,
    })
    return out


# ---------------------------------------------------------------- journals

def parse_journal(d: str) -> dict:
    out: dict = {"records": 0, "files": []}
    try:
        files = sorted((REPO / "data" / "journals").glob(f"journal_{d}_*.jsonl"))
    except Exception:
        files = []
    if not files:
        out["note"] = f"UNMEASURABLE: no journal_{d}_*.jsonl"
        return out
    agent_model: Counter = Counter()
    agent_lat: dict[str, list[float]] = defaultdict(list)
    primary_lat: dict[str, list[float]] = defaultdict(list)   # tier -> ms (primary only)
    ens_partial = 0
    agent_error = 0
    n = 0
    for f in files:
        out["files"].append(f.name)
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    n += 1
                    for s in rec.get("agent_signals", []) or []:
                        aid = s.get("agent_id") or "?"
                        mid = s.get("model_id") or ""
                        lm = s.get("latency_ms")
                        agent_model[f"{aid}|{mid or 'none'}"] += 1
                        if isinstance(lm, (int, float)) and lm > 0:
                            agent_lat[aid].append(float(lm))
                            if "397B" in mid:
                                primary_lat["tier1(manip,397B)"].append(float(lm))
                            elif "235B" in mid:
                                primary_lat[f"tier2({aid},235B,ens-wall)"].append(float(lm))
                        flags = s.get("flags") or []
                        if "AGENT_ERROR" in flags or "D92_ALL_FAILED" in flags:
                            agent_error += 1
                        rsn = s.get("reasoning") or ""
                        m = re.search(r"ENSEMBLE \((\d)/(\d) calls\)", rsn)
                        if m and int(m.group(1)) < int(m.group(2)):
                            ens_partial += 1
        except Exception:
            continue
    out.update({
        "records": n,
        "agent_model_counts": dict(agent_model),
        "agent_latency_ms": {a: lat_stats(v) for a, v in agent_lat.items()},
        "primary_model_latency_ms": {t: lat_stats(v) for t, v in primary_lat.items()},
        "ensemble_partial_signals": ens_partial,
        "agent_error_signals": agent_error,
    })
    return out


# ---------------------------------------------------------------- fleet logs

def parse_data_ingest(d: str) -> dict:
    txt = read_text(LOGS / f"data_ingest_{d}.log")
    if not txt:
        return {"note": f"UNMEASURABLE: logs/data_ingest_{d}.log missing"}
    out: dict = {}
    for step in ("day_aggs", "minute_aggs"):
        m = re.search(
            rf"\[polygon_flatfile_{step}\].*?ok=(\d+) skipped=(\d+) 404=(\d+) error=(\d+)",
            txt)
        if m:
            out[f"flatfile_{step}"] = {
                "downloaded": int(m.group(1)), "skipped": int(m.group(2)),
                "http404": int(m.group(3)), "errors": int(m.group(4)),
            }
    out["ran"] = bool(out)
    return out


def _cron_block(text: str, d: str, kind: str) -> str:
    """Slice the run-block for date d out of a cumulative cron log."""
    if not text:
        return ""
    try:
        pat = re.compile(rf"===== ({re.escape(d)} [\d:]+)\s+{kind} =====")
        m = pat.search(text)
        if not m:
            return ""
        start = m.end()
        # next RUN header (not a title underline like '=========')
        nxt = re.search(r"\n===== \d{4}-\d{2}-\d{2} ", text[start:])
        end = start + nxt.start() if nxt else len(text)
        return text[start:end]
    except Exception:
        return ""


def parse_rocket_watchlist(d: str) -> dict:
    txt = read_text(REPO / "data" / "research" / "rocket_watchlist_cron.log")
    if not txt:
        return {"ran": False, "note": "cron log missing"}
    # aggregate ALL run blocks for this date (6/4 ran twice)
    headers = [m for m in re.finditer(
        rf"===== {re.escape(d)} [\d:]+\s+rocket-watchlist catchup =====", txt)]
    if not headers:
        return {"ran": False, "note": "no run block for this date (weekend or not yet run)"}
    n_days = n_rows = 0
    scored_last = None
    since = None
    for m in headers:
        start = m.end()
        nxt = re.search(r"\n===== \d{4}-\d{2}-\d{2} ", txt[start:])
        blk = txt[start:start + nxt.start()] if nxt else txt[start:]
        days = re.findall(r"(\d{4}-\d{2}-\d{2}): (\d+) gappers logged", blk)
        n_days += len(days)
        n_rows += sum(int(n) for _, n in days)
        sc = re.search(r"scored (\d+) gapper-rows", blk)
        if sc:
            scored_last = int(sc.group(1))
        si = re.search(r"catchup since (\d{4}-\d{2}-\d{2})", blk)
        if si:
            since = si.group(1)
    return {
        "ran": True,
        "runs": len(headers),
        "catchup_since": since,
        "days_reprocessed": n_days,
        "gapper_rows_reprocessed": n_rows,
        "llm_redflag_rows_cumulative": scored_last,
        "polygon_rest": 0,
        "note": "reads LOCAL polygon warehouse (no REST); --llm scores via Together; "
                "1 Discord file post per run",
    }


def parse_shadow_grader(d: str) -> dict:
    txt = read_text(REPO / "data" / "research" / "shadow_grader_cron.log")
    blk = _cron_block(txt, d, "shadow-vs-prod grader")
    if not blk:
        return {"ran": False, "note": "no run block for this date"}
    filled = re.search(r"broker-FILLED: (\d+)", blk)
    unfilled = re.search(r"SUBMITTED-UNFILLED: (\d+)", blk)
    n_filled = int(filled.group(1)) if filled else 0
    n_unfilled = int(unfilled.group(1)) if unfilled else 0
    n = n_filled + n_unfilled
    if n == 0:
        # pre-doc-268 format: "prod opened N long(s): ..."
        opened = re.search(r"prod opened (\d+) long", blk)
        if opened:
            n = int(opened.group(1))
    return {
        "ran": True,
        "prod_buys_graded_filled": n_filled,
        "prod_buys_graded_unfilled": n_unfilled,
        "polygon_open_close_rest_est": n,
        "posted_to_discord": "[posted to Discord]" in blk,
    }


def parse_fader_short(d: str) -> dict:
    txt = read_text(LOGS / f"fader_short_{d}.log")
    if not txt:
        return {"ran": False, "note": f"UNMEASURABLE: logs/fader_short_{d}.log missing"}
    return {
        "ran": True,
        "polygon_snapshot_calls": len(re.findall(r"Snapshot: \d+ gainers", txt)),
        "alpaca_account_calls": len(re.findall(r"Account: \w+", txt)),
        "orders_attempted": len(re.findall(r"^.*ORDER: ", txt, re.M)),
        "orders_skipped_not_shortable": len(re.findall(r"NOT SHORTABLE", txt)),
    }


def parse_lottery(d: str) -> dict:
    txt = read_text(LOGS / f"lottery_{d}.log")
    if not txt:
        return {"ran": False, "note": f"UNMEASURABLE: logs/lottery_{d}.log missing"}
    return {
        "ran": True,
        "alpaca_account_calls": len(re.findall(r"Account: \w+", txt)),
        "buys": len(re.findall(r"BUY", txt)),
        "note": "Alpaca movers/bars only (no Polygon)",
    }


def parse_alert_spool(d: str) -> dict:
    base = REPO / "data" / "alerts" / d
    try:
        if not base.exists():
            return {"failed_or_pending_posts": 0, "note": "no spool dir (all posts delivered live or none made)"}
        files = [p for p in base.iterdir() if p.suffix == ".json"]
        stale = base / "stale"
        n_stale = len(list(stale.glob("*.json"))) if stale.exists() else 0
        sev: Counter = Counter()
        for p in files:
            m = re.search(r"_(\w+)\.json$", p.name)
            if m:
                sev[m.group(1)] += 1
        return {"failed_or_pending_posts": len(files), "by_severity": dict(sev),
                "stale_quarantined": n_stale}
    except Exception:
        return {"failed_or_pending_posts": 0, "note": "spool unreadable"}


# ---------------------------------------------------------------- assembly

def audit_one(d: str) -> dict:
    mom = parse_momentum(d)
    jrn = parse_journal(d)
    census = {
        "date": d,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "main_bot": mom,
        "journal": jrn,
        "polygon": {
            "main_bot_rest": 0,
            "main_bot_note": "verified: no api.polygon.io in src/ -- bot market data is Alpaca",
            "data_ingest_flatfiles": parse_data_ingest(d),
            "rocket_watchlist": parse_rocket_watchlist(d),
            "shadow_grader": parse_shadow_grader(d),
            "fader_short": parse_fader_short(d),
        },
        "lottery": parse_lottery(d),
        "discord": {
            "success_posts": "UNMEASURABLE: durable_post deletes spool on success and "
                             "logs nothing at INFO; markers below are a lower bound",
            "log_markers": mom.get("discord_markers_in_log", 0),
            "spool": parse_alert_spool(d),
        },
    }
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUT_DIR / f"usage_audit_{d}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(census, f, indent=1, default=str)
        census["_written"] = str(out_path)
    except Exception as e:
        census["_written"] = f"WRITE FAILED: {e}"
    return census


# ---------------------------------------------------------------- reporting

def g(c: dict, *keys, default=0):
    cur = c
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
    return cur if cur is not None else default


def print_census_table(rows: list[dict]) -> None:
    dates = [r["date"][5:] for r in rows]
    _say("")
    _say("=" * (34 + 11 * len(rows)))
    _say("PAID-SERVICE USAGE CENSUS (per session day)   [doc 270 D1]")
    _say("=" * (34 + 11 * len(rows)))
    header = f"{'metric':<34}" + "".join(f"{d:>11}" for d in dates)
    _say(header)
    _say("-" * len(header))

    def row(label, fn, fmt="{:>11}"):
        vals = []
        for r in rows:
            try:
                v = fn(r)
            except Exception:
                v = "?"
            vals.append("-" if v is None else v)
        _say(f"{label:<34}" + "".join(fmt.format(v) for v in vals))

    _say("TOGETHER AI")
    row("  LLM calls total", lambda r: g(r, "main_bot", "together", "llm_calls_total"))
    row("  tier1 primary (397B)", lambda r: g(r, "main_bot", "together", "by_tier", "tier1_primary"))
    row("  tier2 primary (235B)", lambda r: g(r, "main_bot", "together", "by_tier", "tier2_primary"))
    row("  fallback DeepSeek (DEAD 400)", lambda r: g(r, "main_bot", "together", "by_tier", "fallback_deepseek(DEAD-400)"))
    row("  fallback Llama-3.3", lambda r: g(r, "main_bot", "together", "by_tier", "fallback_llama"))
    row("  fallback Qwen2.5-7B", lambda r: g(r, "main_bot", "together", "by_tier", "fallback_qwen7b"))
    row("  ensemble groups (news+fund)", lambda r: sum(g(r, "main_bot", "together", "ensemble_groups_by_agent", default={}).values()))
    row("  ensemble member failures", lambda r: g(r, "main_bot", "together", "ensemble_member_failures"))
    row("  D99 29s-cancelled agents", lambda r: g(r, "main_bot", "together", "d99_cancelled_total"))
    row("  CB trips (llm_provider)", lambda r: g(r, "main_bot", "together", "circuit_breaker_trips", "llm_provider"))
    row("  evals (journal records)", lambda r: g(r, "journal", "records"))
    row("  EMPTY-data evals", lambda r: g(r, "main_bot", "together", "empty_data_evals"))
    row("  watchlist LLM rows (cum)", lambda r: g(r, "polygon", "rocket_watchlist", "llm_redflag_rows_cumulative", default="-"))
    _say("POLYGON")
    row("  main-bot REST", lambda r: g(r, "polygon", "main_bot_rest"))
    row("  flatfiles downloaded", lambda r: (g(r, "polygon", "data_ingest_flatfiles", "flatfile_day_aggs", "downloaded") + g(r, "polygon", "data_ingest_flatfiles", "flatfile_minute_aggs", "downloaded")) if g(r, "polygon", "data_ingest_flatfiles", "ran", default=False) else "-")
    row("  flatfile skip-checks", lambda r: (g(r, "polygon", "data_ingest_flatfiles", "flatfile_day_aggs", "skipped") + g(r, "polygon", "data_ingest_flatfiles", "flatfile_minute_aggs", "skipped")) if g(r, "polygon", "data_ingest_flatfiles", "ran", default=False) else "-")
    row("  shadow-grader open-close REST", lambda r: g(r, "polygon", "shadow_grader", "polygon_open_close_rest_est", default="-") if g(r, "polygon", "shadow_grader", "ran", default=False) else "-")
    row("  fader snapshot calls", lambda r: g(r, "polygon", "fader_short", "polygon_snapshot_calls", default="-") if g(r, "polygon", "fader_short", "ran", default=False) else "-")
    row("  rocket days re-processed", lambda r: g(r, "polygon", "rocket_watchlist", "days_reprocessed", default="-") if g(r, "polygon", "rocket_watchlist", "ran", default=False) else "-")
    row("  rocket rows re-processed", lambda r: g(r, "polygon", "rocket_watchlist", "gapper_rows_reprocessed", default="-") if g(r, "polygon", "rocket_watchlist", "ran", default=False) else "-")
    _say("ALPACA")
    row("  screener most-active calls", lambda r: g(r, "main_bot", "alpaca", "screener_most_active_calls"))
    row("  screener movers calls", lambda r: g(r, "main_bot", "alpaca", "screener_movers_calls"))
    row("  scan iterations", lambda r: g(r, "main_bot", "alpaca", "scan_iterations"))
    row("  orders submitted (dash)", lambda r: g(r, "main_bot", "alpaca", "orders_submitted_dashboard", default="-"))
    row("  orders filled (dash)", lambda r: g(r, "main_bot", "alpaca", "orders_filled_dashboard", default="-"))
    row("  failed cancel 301-bug", lambda r: g(r, "main_bot", "alpaca", "failed_cancel_301_bug"))
    row("  403 Forbidden responses", lambda r: g(r, "main_bot", "alpaca", "http_403_forbidden"))
    row("  EOD-recon equity polls", lambda r: g(r, "main_bot", "alpaca", "eod_recon_equity_polls"))
    row("  CB trips (alpaca_rest)", lambda r: g(r, "main_bot", "alpaca", "cb_trips_alpaca_rest"))
    _say("EDGAR")
    row("  search requests FAILED (500)", lambda r: g(r, "main_bot", "edgar", "search_failures"))
    _say("FINNHUB")
    row("  company-news errors", lambda r: g(r, "main_bot", "finnhub", "company_news_errors"))
    _say("DISCORD")
    row("  log markers (lower bound)", lambda r: g(r, "discord", "log_markers"))
    row("  failed/pending spool posts", lambda r: g(r, "discord", "spool", "failed_or_pending_posts"))
    _say("-" * len(header))


def print_waste_and_latency(rows: list[dict]) -> None:
    full = [r for r in rows if g(r, "journal", "records") and g(r, "journal", "records") > 0]
    nd = max(1, len(full))

    def tot(fn):
        s = 0
        for r in full:
            try:
                v = fn(r)
                s += v if isinstance(v, (int, float)) else 0
            except Exception:
                pass
        return s

    deepseek = tot(lambda r: g(r, "main_bot", "together", "by_tier", "fallback_deepseek(DEAD-400)"))
    llama = tot(lambda r: g(r, "main_bot", "together", "by_tier", "fallback_llama"))
    d99 = tot(lambda r: g(r, "main_bot", "together", "d99_cancelled_total"))
    edgar = tot(lambda r: g(r, "main_bot", "edgar", "search_failures"))
    finn = tot(lambda r: g(r, "main_bot", "finnhub", "company_news_errors"))
    c301 = tot(lambda r: g(r, "main_bot", "alpaca", "failed_cancel_301_bug"))
    rocket_rows = tot(lambda r: g(r, "polygon", "rocket_watchlist", "gapper_rows_reprocessed") if g(r, "polygon", "rocket_watchlist", "ran", default=False) else 0)
    rocket_days = tot(lambda r: g(r, "polygon", "rocket_watchlist", "days_reprocessed") if g(r, "polygon", "rocket_watchlist", "ran", default=False) else 0)
    ens_fail = tot(lambda r: g(r, "main_bot", "together", "ensemble_member_failures"))
    cb = tot(lambda r: g(r, "main_bot", "together", "circuit_breaker_trips", "llm_provider"))

    _say("")
    _say("WASTE SIGNALS (totals over %d full session(s), with per-day rate)" % len(full))
    _say("-" * 74)
    _say(f" 1. DEAD DeepSeek-V3.1 fallback calls (doc-267 broken middle link,")
    _say(f"    every call an instant 400): {deepseek} total = {deepseek/nd:.0f}/day;")
    _say(f"    each also cascades to Llama emergency (+{llama} = {llama/nd:.0f}/day)")
    _say(f" 2. EDGAR search storm, 100% failure (efts.sec.gov 500 on every call):")
    _say(f"    {edgar} failed requests = {edgar/nd:.0f}/day (~26 at every boot + per-eval)")
    _say(f" 3. CB-trip burst waste: {cb} llm_provider trips; {ens_fail} ensemble member")
    _say(f"    failures = {ens_fail/nd:.0f}/day burned calls inside 3x ensembles")
    _say(f" 4. D99 29s-cancels: {d99} agents cancelled after burning full 29s phase-1")
    _say(f"    window = {d99/nd:.1f}/day (each ~29s of paid latency, signal discarded)")
    _say(f" 5. Post-close fleet overlap: rocket-watchlist re-processes ~{rocket_days/nd:.0f} prior")
    _say(f"    days ({rocket_rows/nd:.0f} gapper-rows) EVERY night (catchup window re-walk)")
    _say(f" 6. Alpaca cancel-301 bug (DELETE /v2/orders/ with EMPTY id): {c301} total")
    _say(f"    = {c301/nd:.1f}/day, every one a guaranteed-fail API call")
    _say(f" 7. Finnhub company-news 5xx/timeouts: {finn} = {finn/nd:.0f}/day")

    # latency vs budget
    _say("")
    _say("LLM LATENCY vs BUDGET (journal latency_ms; primary models only)")
    _say("-" * 74)
    _say(f"{'tier/agent':<38}{'n':>6}{'p50':>8}{'p95':>8}  budget")
    agg: dict[str, list[float]] = defaultdict(list)
    for r in full:
        pl = g(r, "journal", "primary_model_latency_ms", default={})
        # re-aggregate from per-day stats is lossy; collect from raw if present
    # collect raw again from journals for precision
    for r in full:
        d = r["date"]
        try:
            files = sorted((REPO / "data" / "journals").glob(f"journal_{d}_*.jsonl"))
            for f in files:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        for s in rec.get("agent_signals", []) or []:
                            mid = s.get("model_id") or ""
                            lm = s.get("latency_ms")
                            if not isinstance(lm, (int, float)) or lm <= 0:
                                continue
                            aid = s.get("agent_id")
                            if "397B" in mid:
                                agg["tier1 manipulation_classifier"].append(lm)
                            elif "235B" in mid and aid == "news_agent":
                                agg["tier2 news_agent (3x ens wall)"].append(lm)
                            elif "235B" in mid and aid == "fundamental_agent":
                                agg["tier2 fundamental_agent (3x ens)"].append(lm)
        except Exception:
            continue
    budgets = {
        "tier1 manipulation_classifier": "25s timeout / 29s D99",
        "tier2 news_agent (3x ens wall)": "25s timeout / 29s D99",
        "tier2 fundamental_agent (3x ens)": "25s timeout / 29s D99",
    }
    for k in budgets:
        st = lat_stats(agg.get(k, []))
        if st.get("n"):
            _say(f"{k:<38}{st['n']:>6}{st['p50_ms']/1000:>7.1f}s{st['p95_ms']/1000:>7.1f}s  {budgets[k]}")
        else:
            _say(f"{k:<38}{'0':>6}{'-':>8}{'-':>8}  {budgets[k]} (UNMEASURABLE)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-day per-provider usage census (doc 270 D1)")
    ap.add_argument("--date", default=date_cls.today().isoformat(),
                    help="session date YYYY-MM-DD (default: today)")
    ap.add_argument("--dates", nargs="*", default=None,
                    help="multiple dates; overrides --date")
    args = ap.parse_args()
    dates = args.dates if args.dates else [args.date]

    rows = []
    for d in dates:
        try:
            rows.append(audit_one(d))
        except Exception as e:  # never-raises philosophy
            _say(f"[usage_audit] {d}: parser error swallowed: {e}")
            rows.append({"date": d, "error": str(e)})
    try:
        print_census_table(rows)
        print_waste_and_latency(rows)
    except Exception as e:
        _say(f"[usage_audit] report rendering error swallowed: {e}")
    for r in rows:
        _say(f"json: {r.get('_written', '-')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
