#!/usr/bin/env python
"""
kalshi_shadow_doc284.py -- WORKSTREAM C: the Kalshi zero-capital shadow (doc-283 Step 4).

Collects Kalshi market snapshots + independent LLM probabilities on text-rich markets,
and scores both at resolution. NO position is ever taken -- shadow only, zero capital.

================================ FROZEN GATE (doc-283 Step 4) ================================
After >= 200 RESOLVED forecasts:
    PASS  = fee-adjusted divergence-rule P&L, day-blocked bootstrap 95% CI > 0
            AND  mean Brier(LLM) < mean Brier(market mid at forecast time)
            -> propose the $5-20K live side-pocket.
    FAIL  = anything else -> the door is CLOSED. No number bent; a failed gate is a success.
Divergence rule (frozen): if |edge| > 0.08 where edge = p_llm - p_market_mid, buy the LLM
side at the prevailing ask (YES at yes_ask, NO at 100 - yes_bid), 1 contract, with Kalshi
taker fee round_up_to_next_cent(0.07 * p * (1 - p)) dollars per 1-contract order
(p = execution price in dollars; rounding per the published fee-schedule 1-contract table,
kalshi.com/docs/kalshi-fee-schedule.pdf effective 2026-06-29 -- verified 2026-07-05).
==============================================================================================

Subcommands
    snapshot  -- fetch open Kalshi markets (public API, no auth), filter to text-rich
                 categories with close_time > 24h away and a live two-sided quote;
                 append rows to data/research/doc284/kalshi_snapshots.jsonl
    forecast  -- for up to N (default 25, budget guard) snapshotted-but-unforecast markets,
                 ask the LLM (Together AI, openai/gpt-oss-120b, temperature 0.2) for an
                 INDEPENDENT p_yes (market price is never shown to the model);
                 append rows to data/research/doc284/kalshi_forecasts.jsonl
    score     -- check resolution status of forecasted markets (batch GET /markets?tickers=),
                 compute Brier(LLM) vs Brier(market mid) and simulated fee-adjusted P&L of
                 the divergence rule; append to data/research/doc284/kalshi_scores.jsonl and
                 print the running comparison + gate progress. Handles zero-resolved cleanly.
    plan      -- print the 60-day collection plan: the exact schtasks one-liner + cost/day.

60-DAY COLLECTION (operator creates the task; this script never does):
    schtasks /Create /TN "MomentumX\\KalshiShadowDoc284" /SC DAILY /ST 18:00 /TR "cmd /c cd /d \\"<local-path> operator\\Documents\\GitHub\\momentum-x\\" && set PYTHONIOENCODING=utf-8&& python scripts\\kalshi_shadow_doc284.py snapshot && python scripts\\kalshi_shadow_doc284.py forecast && python scripts\\kalshi_shadow_doc284.py score >> logs\\kalshi_shadow_doc284.log 2>&1"

API notes (verified live 2026-07-05):
    base https://api.elections.kalshi.com/trade-api/v2 ; GET /events?status=open&
    with_nested_markets=true paginates via `cursor`; events carry `category`; nested
    markets carry dollar-string quote fields (yes_bid_dollars etc. -- the old integer-cent
    fields are gone) plus rules_primary. GET /markets?tickers=A,B batch works; settled
    markets show status in {finalized, settled} and result in {yes, no}.

Usage:
    python scripts/kalshi_shadow_doc284.py snapshot [--max-days 90] [--per-event-cap 5]
    python scripts/kalshi_shadow_doc284.py forecast [--n 25]
    python scripts/kalshi_shadow_doc284.py score
    python scripts/kalshi_shadow_doc284.py plan
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

# ----------------------------------------------------------------------------- constants
REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "research" / "doc284"
SNAP_PATH = DATA_DIR / "kalshi_snapshots.jsonl"
FCAST_PATH = DATA_DIR / "kalshi_forecasts.jsonl"
SCORE_PATH = DATA_DIR / "kalshi_scores.jsonl"

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# Text-rich categories where an LLM plausibly has information to add (doc-283 door 2).
# EXCLUDED by design: Financials / Crypto / Commodities (pure market-data derivatives --
# S&P daily ranges, BTC hourly etc., where the close is market data, not text), Sports,
# Entertainment, Mentions, Social, Transportation.
TEXT_RICH_CATEGORIES = {
    "Politics",
    "Elections",
    "World",
    "Economics",
    "Climate and Weather",
    "Companies",
    "Science and Technology",
    "Health",
}

MIN_HOURS_TO_CLOSE = 24          # frozen: never touch markets closing within 24h
DEFAULT_MAX_DAYS_TO_CLOSE = 90   # snapshot horizon guard (gate needs resolutions in ~60d)
FORECAST_PRIORITY_DAYS = 45      # forecast budget goes to markets that can resolve in-window
DEFAULT_PER_EVENT_CAP = 5        # top-K markets per event by volume (multi-strike events)
MAX_EVENT_PAGES = 80             # pagination guard
DEFAULT_FORECAST_N = 25          # budget guard

TOGETHER_URL = "https://api.together.xyz/v1/chat/completions"
TOGETHER_MODEL = "openai/gpt-oss-120b"
LLM_TEMPERATURE = 0.2
# Together list price for openai/gpt-oss-120b (2026-07): $0.15/M input, $0.60/M output.
PRICE_IN_PER_M = 0.15
PRICE_OUT_PER_M = 0.60

EDGE_THRESHOLD = 0.08            # frozen divergence rule
# Kalshi general taker fee (fee schedule effective 2026-06-29, verified 2026-07-05):
#   fees = round up(0.07 x C x P x (1-P)) dollars; the published 1-contract table rounds
#   UP TO THE NEXT CENT per order ($0.01 price -> $0.01 fee, $0.50 -> $0.02).
#   INX/NASDAQ100 series are 0.035 but live in the excluded Financials category.
FEE_RATE = 0.07
GATE_MIN_RESOLVED = 200          # frozen gate sample size
SNAPSHOT_MAX_AGE_HOURS = 12      # forecast must use quotes captured this run-cycle (lookahead guard)

SCHTASKS_ONELINER = (
    'schtasks /Create /TN "MomentumX\\KalshiShadowDoc284" /SC DAILY /ST 18:00 '
    '/TR "cmd /c cd /d \\"<local-path> operator\\Documents\\GitHub\\momentum-x\\" '
    '&& set PYTHONIOENCODING=utf-8&& python scripts\\kalshi_shadow_doc284.py snapshot '
    '&& python scripts\\kalshi_shadow_doc284.py forecast '
    '&& python scripts\\kalshi_shadow_doc284.py score '
    '>> logs\\kalshi_shadow_doc284.log 2>&1"'
)


# ----------------------------------------------------------------------------- utilities
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def to_cents(market: dict, field: str) -> float | None:
    """Defensive price parse: prefer legacy integer-cent field, else *_dollars string."""
    v = market.get(field)
    if isinstance(v, (int, float)):
        return float(v)
    dv = market.get(field + "_dollars")
    if dv is None:
        return None
    try:
        return round(float(dv) * 100.0, 4)
    except (TypeError, ValueError):
        return None


def to_float(market: dict, field: str) -> float | None:
    """Defensive numeric parse: integer field, else *_fp fixed-point string."""
    v = market.get(field)
    if isinstance(v, (int, float)):
        return float(v)
    fv = market.get(field + "_fp")
    if fv is None:
        return None
    try:
        return float(fv)
    except (TypeError, ValueError):
        return None


def append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # never let one bad line kill the pipeline
    return rows


def kalshi_get(client: httpx.Client, path: str, params: dict | None = None,
               retries: int = 3) -> dict:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            r = client.get(f"{KALSHI_BASE}{path}", params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(2.0 * (attempt + 1))
                continue
            last_err = RuntimeError(f"HTTP {r.status_code} on {path}: {r.text[:200]}")
        except httpx.HTTPError as e:
            last_err = e
        time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Kalshi GET {path} failed after {retries} tries: {last_err}")


# ----------------------------------------------------------------------------- snapshot
def cmd_snapshot(args: argparse.Namespace) -> int:
    now = utc_now()
    min_close = now + timedelta(hours=MIN_HOURS_TO_CLOSE)
    max_close = now + timedelta(days=args.max_days)
    kept_rows: list[dict] = []
    n_events_seen = 0
    n_events_textrich = 0
    n_markets_seen = 0
    reject = collections.Counter()
    cat_mix = collections.Counter()

    with httpx.Client(headers={"User-Agent": "momentum-x doc284 shadow (read-only)"}) as client:
        cursor = None
        pages = 0
        while pages < MAX_EVENT_PAGES:
            params = {"limit": 200, "status": "open", "with_nested_markets": "true"}
            if cursor:
                params["cursor"] = cursor
            d = kalshi_get(client, "/events", params)
            events = d.get("events", [])
            n_events_seen += len(events)
            for ev in events:
                cat = ev.get("category") or "?"
                if cat not in TEXT_RICH_CATEGORIES:
                    continue
                n_events_textrich += 1
                ev_title = ev.get("title") or ""
                candidates = []
                for m in ev.get("markets") or []:
                    n_markets_seen += 1
                    if m.get("status") != "active":
                        reject["not_active"] += 1
                        continue
                    if m.get("market_type") != "binary":
                        reject["not_binary"] += 1
                        continue
                    if m.get("mve_selected_legs") or m.get("mve_collection_ticker"):
                        reject["multivariate_combo"] += 1
                        continue
                    ct = parse_ts(m.get("close_time"))
                    if ct is None:
                        reject["bad_close_time"] += 1
                        continue
                    if ct <= min_close:
                        reject["closes_within_24h"] += 1
                        continue
                    if ct > max_close:
                        reject["closes_beyond_horizon"] += 1
                        continue
                    yes_bid = to_cents(m, "yes_bid")
                    yes_ask = to_cents(m, "yes_ask")
                    if yes_bid is None or yes_ask is None:
                        reject["missing_quote"] += 1
                        continue
                    # "both present" in the dollars-string API = a live two-sided book
                    if not (0 < yes_bid <= yes_ask < 100):
                        reject["one_sided_or_degenerate"] += 1
                        continue
                    strike = m.get("yes_sub_title") or ""
                    m_title = m.get("title") or ev_title
                    display = ev_title if not strike or strike == m_title else f"{ev_title} :: {strike}"
                    if not display:
                        display = m_title
                    candidates.append({
                        "ts": iso(now),
                        "market_ticker": m.get("ticker"),
                        "event_ticker": ev.get("event_ticker"),
                        "series_ticker": ev.get("series_ticker"),
                        "title": display,
                        "category": cat,
                        "close_time": m.get("close_time"),
                        "yes_bid": yes_bid,
                        "yes_ask": yes_ask,
                        "mid": round((yes_bid + yes_ask) / 2.0, 4),
                        "volume": to_float(m, "volume"),
                        "open_interest": to_float(m, "open_interest"),
                        "rules_primary": (m.get("rules_primary") or "")[:800],
                    })
                candidates.sort(key=lambda r: -(r["volume"] or 0.0))
                if len(candidates) > args.per_event_cap:
                    reject["per_event_cap"] += len(candidates) - args.per_event_cap
                    candidates = candidates[: args.per_event_cap]
                for row in candidates:
                    cat_mix[cat] += 1
                kept_rows.extend(candidates)
            cursor = d.get("cursor")
            pages += 1
            if not cursor or not events:
                break
            time.sleep(0.15)

    append_jsonl(SNAP_PATH, kept_rows)
    print(f"[snapshot] {iso(now)}  pages={pages} events_seen={n_events_seen} "
          f"text_rich_events={n_events_textrich} markets_considered={n_markets_seen}")
    print(f"[snapshot] KEPT {len(kept_rows)} markets -> {SNAP_PATH}")
    print(f"[snapshot] category mix: {dict(cat_mix.most_common())}")
    print(f"[snapshot] rejections: {dict(reject.most_common())}")
    if pages >= MAX_EVENT_PAGES and cursor:
        print("[snapshot] WARNING: page guard hit before cursor exhausted; coverage partial")
    return 0


# ----------------------------------------------------------------------------- forecast
FORECAST_SYSTEM = (
    "You are a careful, calibrated probabilistic forecaster. "
    "You respond with ONLY a single JSON object. No prose, no markdown, no code fences."
)

FORECAST_TEMPLATE = """Today's date: {today} (UTC). Your knowledge may have a cutoff; reason from what you know and be honest in your confidence.

Forecast this prediction-market question (binary, resolves YES or NO):

QUESTION: {title}
CATEGORY: {category}
MARKET CLOSES: {close_time}
RESOLUTION RULES (summary): {rules}

Estimate the probability that this market resolves YES.
Respond with ONLY this JSON object:
{{"p_yes": <number between 0 and 1>, "confidence": "low"|"med"|"high", "reasoning": "<at most 30 words>"}}"""


def parse_llm_json(text: str) -> dict | None:
    """Defensive parse: strip fences, find the first {...} block, validate fields."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or "p_yes" not in obj:
        return None
    try:
        p = float(obj["p_yes"])
    except (TypeError, ValueError):
        return None
    if math.isnan(p):
        return None
    p = min(max(p, 0.0), 1.0)
    conf = str(obj.get("confidence", "")).lower()
    if conf not in ("low", "med", "high"):
        conf = "unparsed"
    reasoning = str(obj.get("reasoning", ""))[:300]
    return {"p_yes": p, "confidence": conf, "reasoning": reasoning}


def call_llm(client: httpx.Client, api_key: str, prompt: str,
             retries: int = 3) -> tuple[dict | None, dict]:
    """Returns (parsed, usage). Never raises; returns (None, {}) on hard failure."""
    for attempt in range(retries):
        try:
            r = client.post(
                TOGETHER_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": TOGETHER_MODEL,
                    "messages": [
                        {"role": "system", "content": FORECAST_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": LLM_TEMPERATURE,
                    "max_tokens": 800,
                },
                timeout=90,
            )
            if r.status_code == 429:
                time.sleep(3.0 * (attempt + 1))
                continue
            if r.status_code != 200:
                time.sleep(1.5 * (attempt + 1))
                continue
            d = r.json()
            content = d["choices"][0]["message"]["content"]
            parsed = parse_llm_json(content)
            usage = d.get("usage") or {}
            if parsed is not None:
                return parsed, usage
            # parse failure: retry once with the same prompt (temperature adds variety)
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError):
            time.sleep(1.5 * (attempt + 1))
    return None, {}


def cmd_forecast(args: argparse.Namespace) -> int:
    now = utc_now()
    snaps = read_jsonl(SNAP_PATH)
    if not snaps:
        print("[forecast] no snapshots yet -- run `snapshot` first")
        return 0
    already = {r.get("market_ticker") for r in read_jsonl(FCAST_PATH)}

    # latest snapshot row per ticker
    latest: dict[str, dict] = {}
    for r in snaps:
        t = r.get("market_ticker")
        if t:
            latest[t] = r  # file is append-ordered; last write wins

    min_close = now + timedelta(hours=MIN_HOURS_TO_CLOSE)
    priority_close = now + timedelta(days=FORECAST_PRIORITY_DAYS)
    oldest_ok = now - timedelta(hours=SNAPSHOT_MAX_AGE_HOURS)
    eligible, n_stale = [], 0
    for t, r in latest.items():
        if t in already:
            continue
        snap_ts = parse_ts(r.get("ts"))
        if snap_ts is None or snap_ts < oldest_ok:
            # LOOKAHEAD GUARD: p_market_mid and the fill quotes MUST be the prevailing
            # quotes at forecast time. A stale snapshot would pair a fresh forecast with
            # dead prices -- skip; the next snapshot run re-captures the market.
            n_stale += 1
            continue
        ct = parse_ts(r.get("close_time"))
        if ct is None or ct <= min_close:
            continue
        eligible.append((ct <= priority_close, r.get("volume") or 0.0, t, r))
    if n_stale:
        print(f"[forecast] skipped {n_stale} markets with snapshots older than "
              f"{SNAPSHOT_MAX_AGE_HOURS}h (run `snapshot` first)")
    # markets that can resolve inside the 60-day window first, most-traded first
    eligible.sort(key=lambda x: (not x[0], -x[1]))
    batch = [r for _, _, _, r in eligible[: args.n]]
    if not batch:
        print("[forecast] nothing eligible to forecast (all snapshotted markets done or closed)")
        return 0

    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
    api_key = os.getenv("TOGETHER_AI_API_KEY")
    if not api_key:
        print("[forecast] ERROR: TOGETHER_AI_API_KEY not found in .env")
        return 1

    rows, failures = [], 0
    tot_in = tot_out = 0
    with httpx.Client() as client:
        for r in batch:
            prompt = FORECAST_TEMPLATE.format(
                today=now.strftime("%Y-%m-%d"),
                title=r["title"],
                category=r["category"],
                close_time=r["close_time"],
                rules=(r.get("rules_primary") or "(none provided)")[:700],
            )
            parsed, usage = call_llm(client, api_key, prompt)
            tot_in += int(usage.get("prompt_tokens") or 0)
            tot_out += int(usage.get("completion_tokens") or 0)
            if parsed is None:
                failures += 1
                print(f"[forecast]   PARSE-FAIL {r['market_ticker']}")
                continue
            mid_prob = round((r["mid"] or 0.0) / 100.0, 4)
            edge = round(parsed["p_yes"] - mid_prob, 4)
            rows.append({
                "ts": iso(utc_now()),
                "market_ticker": r["market_ticker"],
                "title": r["title"],
                "category": r["category"],
                "close_time": r["close_time"],
                "p_llm": round(parsed["p_yes"], 4),
                "p_market_mid": mid_prob,
                "edge": edge,
                "yes_bid": r["yes_bid"],
                "yes_ask": r["yes_ask"],
                "confidence": parsed["confidence"],
                "reasoning": parsed["reasoning"],
                "model": TOGETHER_MODEL,
            })
            print(f"[forecast]   {r['market_ticker']}: p_llm={parsed['p_yes']:.2f} "
                  f"mid={mid_prob:.2f} edge={edge:+.2f} ({parsed['confidence']}) "
                  f"| {r['title'][:70]}")
            time.sleep(0.3)

    append_jsonl(FCAST_PATH, rows)
    cost = tot_in / 1e6 * PRICE_IN_PER_M + tot_out / 1e6 * PRICE_OUT_PER_M
    print(f"[forecast] wrote {len(rows)} forecasts ({failures} parse failures) -> {FCAST_PATH}")
    print(f"[forecast] tokens in={tot_in} out={tot_out}  est cost=${cost:.4f} "
          f"({TOGETHER_MODEL} @ ${PRICE_IN_PER_M}/M in, ${PRICE_OUT_PER_M}/M out)")
    return 0


# ----------------------------------------------------------------------------- score
def taker_fee_cents(price_cents: float, contracts: int = 1) -> float:
    """Kalshi taker fee per ORDER, in cents: round up(0.07 * C * p * (1-p)) dollars,
    p = price in dollars, rounded UP to the next cent per the published fee-schedule
    table (effective 2026-06-29; 1 contract at $0.01 pays $0.01, at $0.50 pays $0.02).
    The old fractional-cent version underpriced longshot fills ~70x -- gate-corrupting."""
    p = price_cents / 100.0
    raw_cents = FEE_RATE * contracts * p * (1.0 - p) * 100.0
    return float(math.ceil(round(raw_cents, 6)))


def day_blocked_ci(pnl_by_day: dict[str, list[float]], n_boot: int = 10000,
                   alpha: float = 0.05, seed: int = 284) -> tuple[float, float] | None:
    """Bootstrap 95% CI of mean-daily P&L, resampling whole resolution-days (blocks)."""
    days = sorted(pnl_by_day)
    if len(days) < 5:
        return None
    day_sums = [sum(v) for v in (pnl_by_day[d] for d in days)]
    n = len(day_sums)
    rng = random.Random(seed)
    means = sorted(
        sum(day_sums[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot)
    )
    lo = means[int(alpha / 2 * n_boot)]
    hi = means[min(int((1 - alpha / 2) * n_boot), n_boot - 1)]
    return lo, hi


def cmd_score(args: argparse.Namespace) -> int:
    forecasts = read_jsonl(FCAST_PATH)
    if not forecasts:
        print("[score] no forecasts yet -- nothing to score (0 resolved). Gate: 0/"
              f"{GATE_MIN_RESOLVED} resolved -> PENDING-COLLECTION")
        return 0
    fmap = {r["market_ticker"]: r for r in forecasts if r.get("market_ticker")}
    scored_already = {r.get("market_ticker") for r in read_jsonl(SCORE_PATH)}
    pending = [t for t in fmap if t not in scored_already]
    print(f"[score] forecasts={len(fmap)} already_scored={len(scored_already & set(fmap))} "
          f"pending_check={len(pending)}")

    new_rows: list[dict] = []
    if pending:
        with httpx.Client(headers={"User-Agent": "momentum-x doc284 shadow (read-only)"}) as client:
            for i in range(0, len(pending), 20):
                chunk = pending[i:i + 20]
                try:
                    d = kalshi_get(client, "/markets", {"tickers": ",".join(chunk)})
                except RuntimeError as e:
                    print(f"[score] WARNING: batch fetch failed ({e}); will retry next run")
                    continue
                for m in d.get("markets", []):
                    if m.get("status") not in ("finalized", "settled"):
                        continue  # still open/closed-not-settled: check again next run
                    result = (m.get("result") or "").lower()
                    f = fmap.get(m.get("ticker"))
                    if f is None:
                        continue
                    if result == "":
                        # settled-status but result not yet populated (transient):
                        # do NOT tombstone -- leave pending and re-check next run
                        continue
                    if result not in ("yes", "no"):
                        # voided / scalar / weird settlement: record as unscoreable
                        new_rows.append({
                            "ts": iso(utc_now()), "market_ticker": m.get("ticker"),
                            "result": result, "scoreable": False, "traded": False,
                            "forecast_ts": f.get("ts"), "close_time": f.get("close_time"),
                        })
                        continue
                    y = 1.0 if result == "yes" else 0.0
                    p_llm = float(f["p_llm"])
                    p_mid = float(f["p_market_mid"])
                    edge = p_llm - p_mid
                    traded, side, fill_c, fee_c, pnl_c = False, None, None, None, None
                    if edge > EDGE_THRESHOLD:
                        traded, side = True, "yes"
                        fill_c = float(f["yes_ask"])
                    elif edge < -EDGE_THRESHOLD:
                        traded, side = True, "no"
                        fill_c = 100.0 - float(f["yes_bid"])
                    if traded and fill_c is not None and 0 < fill_c < 100:
                        fee_c = taker_fee_cents(fill_c)
                        won = (side == result)
                        pnl_c = round((100.0 - fill_c - fee_c) if won else (-fill_c - fee_c), 4)
                    elif traded:
                        traded, side = False, None  # degenerate fill price: no trade
                    new_rows.append({
                        "ts": iso(utc_now()), "market_ticker": m.get("ticker"),
                        "result": result, "scoreable": True,
                        "p_llm": p_llm, "p_market_mid": p_mid, "edge": round(edge, 4),
                        "brier_llm": round((p_llm - y) ** 2, 6),
                        "brier_market": round((p_mid - y) ** 2, 6),
                        "traded": traded, "side": side,
                        "fill_price_cents": fill_c, "fee_cents": fee_c, "pnl_cents": pnl_c,
                        "forecast_ts": f.get("ts"), "close_time": f.get("close_time"),
                        "title": f.get("title"), "category": f.get("category"),
                    })
                time.sleep(0.15)
        append_jsonl(SCORE_PATH, new_rows)
    print(f"[score] newly scored this run: {len(new_rows)}")

    # ---- running aggregates over ALL score rows (this run + prior runs)
    all_scores = [r for r in read_jsonl(SCORE_PATH) if r.get("scoreable")]
    n = len(all_scores)
    if n == 0:
        print(f"[score] running: 0 resolved forecasts so far. "
              f"Gate: 0/{GATE_MIN_RESOLVED} -> PENDING-COLLECTION")
        return 0
    mean_bl = sum(r["brier_llm"] for r in all_scores) / n
    mean_bm = sum(r["brier_market"] for r in all_scores) / n
    trades = [r for r in all_scores if r.get("traded")]
    pnl_total_c = sum(r["pnl_cents"] for r in trades) if trades else 0.0
    wins = sum(1 for r in trades if r["pnl_cents"] > 0)
    print(f"[score] running Brier: LLM={mean_bl:.4f} vs market={mean_bm:.4f} on n={n} resolved "
          f"({'LLM better' if mean_bl < mean_bm else 'market better or tied'})")
    print(f"[score] divergence rule (|edge|>{EDGE_THRESHOLD}): trades={len(trades)} "
          f"wins={wins} fee-adj P&L=${pnl_total_c / 100.0:+.2f} (1 contract each)")
    pnl_by_day: dict[str, list[float]] = collections.defaultdict(list)
    for r in trades:
        day = (r.get("close_time") or r.get("ts") or "")[:10]
        pnl_by_day[day].append(r["pnl_cents"])
    ci = day_blocked_ci(pnl_by_day)
    if ci:
        print(f"[score] day-blocked bootstrap 95% CI of mean-daily P&L: "
              f"[{ci[0] / 100.0:+.2f}, {ci[1] / 100.0:+.2f}] $/day over {len(pnl_by_day)} days")
    else:
        print(f"[score] day-blocked CI: needs >=5 resolution days (have {len(pnl_by_day)})")

    # ---- frozen gate
    if n < GATE_MIN_RESOLVED:
        print(f"[score] GATE: {n}/{GATE_MIN_RESOLVED} resolved -> PENDING-COLLECTION")
    else:
        brier_ok = mean_bl < mean_bm
        ci_ok = ci is not None and ci[0] > 0
        verdict = "PASSED -> propose $5-20K side-pocket" if (brier_ok and ci_ok) else "FAILED -> CLOSED"
        print(f"[score] GATE ({n}>={GATE_MIN_RESOLVED}): Brier(LLM)<Brier(mkt)={brier_ok}, "
              f"P&L CI>0={ci_ok} -> {verdict}")
    return 0


# ----------------------------------------------------------------------------- plan
def cmd_plan(args: argparse.Namespace) -> int:
    print("60-DAY COLLECTION PLAN (doc-283 Step 4 / doc-284 shadow)")
    print("=" * 72)
    print("Daily at 18:00 local (after US market close, before most event resolutions):")
    print("  1. snapshot  -- refresh open text-rich markets (>24h to close, two-sided)")
    print("  2. forecast  -- LLM p_yes on up to 25 new markets (budget guard)")
    print("  3. score     -- settle anything resolved; print running Brier + P&L + gate")
    print()
    print("Create the task with EXACTLY this one-liner (run in cmd.exe as the operator;")
    print("this script never creates it):")
    print()
    print(SCHTASKS_ONELINER)
    print()
    est_in, est_out = 300, 300  # measured 2026-07-05 first run: 285 in / 284 out avg per call
    daily = DEFAULT_FORECAST_N * (est_in / 1e6 * PRICE_IN_PER_M + est_out / 1e6 * PRICE_OUT_PER_M)
    print(f"LLM cost at N={DEFAULT_FORECAST_N}/day ({TOGETHER_MODEL}):")
    print(f"  ~{est_in} prompt + ~{est_out} completion tokens/call "
          f"-> ${daily:.4f}/day, ~${daily * 60:.2f} for the full 60-day collection.")
    print()
    print(f"FROZEN GATE: after >={GATE_MIN_RESOLVED} resolved forecasts, "
          "fee-adjusted divergence-rule P&L day-blocked 95% CI > 0 AND "
          "Brier(LLM) < Brier(market) -> propose $5-20K side-pocket; else CLOSED.")
    return 0


# ----------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1].strip())
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("snapshot", help="collect open text-rich markets")
    sp.add_argument("--max-days", type=int, default=DEFAULT_MAX_DAYS_TO_CLOSE,
                    help="skip markets closing beyond this horizon (default %(default)s)")
    sp.add_argument("--per-event-cap", type=int, default=DEFAULT_PER_EVENT_CAP,
                    help="max markets kept per event, by volume (default %(default)s)")
    sp.set_defaults(fn=cmd_snapshot)

    fp = sub.add_parser("forecast", help="LLM p_yes on unforecast snapshotted markets")
    fp.add_argument("--n", type=int, default=DEFAULT_FORECAST_N,
                    help="budget guard: max forecasts this run (default %(default)s)")
    fp.set_defaults(fn=cmd_forecast)

    sc = sub.add_parser("score", help="score resolved forecasts; print running gate")
    sc.set_defaults(fn=cmd_score)

    pl = sub.add_parser("plan", help="print the 60-day collection plan + schtasks line")
    pl.set_defaults(fn=cmd_plan)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
