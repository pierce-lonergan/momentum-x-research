"""
LOTTERY RUNNER — paper-deployment of the freshness-tilted lottery (doc 90).

ARCHITECTURE: standalone process. Does NOT touch the main bot. Uses
                its own Alpaca paper credentials, own log, own state.

LIFECYCLE per session:
  09:25 ET  — startup + preflight (verify halt switch, account, secrets)
  09:25 ET  — fetch watchlist via Alpaca movers screener
  09:25 ET  — apply freshness filter (ticker not seen in last 30 days
              of bar_recordings/)
  09:25 ET  — apply price filter ($1.50 ≤ price ≤ $20)
  09:30 ET  — submit market BUY orders (qty sized to $LOTTERY_NOTIONAL_USD per
              ticker, default $250 — extremely conservative for paper)
  09:30 ET  — submit native trailing stop SELL orders (trail_percent=15)
              for each filled buy
  Loop      — every 60s, log positions/orders/PnL to lottery log
  15:55 ET  — submit market SELL for any remaining open positions
              (forced time-stop — backtest assumed this exit)
  16:00 ET  — write session report + exit

SAFETY CONTROLS:
  MOMENTUM_LOTTERY_HALT=1 → no orders placed, dry-run only
  LOTTERY_DRY_RUN=1       → log intended orders but do not submit
  LOTTERY_NOTIONAL_USD    → per-ticker notional cap (default $250)
  LOTTERY_MAX_TICKERS     → max simultaneous positions (default 10)
  LOTTERY_TRAIL_PCT       → trailing-stop width % (default 15.0)

INVARIANTS:
  1. Never opens a position without a corresponding trailing stop.
  2. Never carries a position overnight (15:55 ET force-close).
  3. Never trades if MOMENTUM_LOTTERY_HALT=1.
  4. Never deploys more than LOTTERY_MAX_TICKERS × LOTTERY_NOTIONAL_USD
     per session.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

REPO = Path(__file__).resolve().parents[1]
LOG_DIR = REPO / "logs"
STATE_DIR = REPO / "data" / "lottery"
STATE_DIR.mkdir(parents=True, exist_ok=True)
BAR_DIR = REPO / "data" / "bar_recordings"

ET = ZoneInfo("America/New_York")
TODAY_ET = datetime.now(ET).strftime("%Y-%m-%d")
LOG_FILE = LOG_DIR / f"lottery_{TODAY_ET}.log"

# ── Config from env ────────────────────────────────────────────────

# D262 ACCOUNT ISOLATION: prefer LOTTERY_ALPACA_* (the lottery's OWN paper sub-account) over the
# shared prod ALPACA_*. Falls back to the shared account if no isolated key is set -- but then a
# hard bankroll cap (below) applies so aggressive-Kelly can't draw down prod's cash. To fully
# isolate: create a separate Alpaca paper account and add LOTTERY_ALPACA_API_KEY / _SECRET_KEY to
# ~/momentum-x-secrets.env.
LOTTERY_ACCT_ISOLATED = bool(os.environ.get("LOTTERY_ALPACA_API_KEY", "").strip())
ALPACA_API_KEY = os.environ.get("LOTTERY_ALPACA_API_KEY") or os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("LOTTERY_ALPACA_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = (os.environ.get("LOTTERY_ALPACA_BASE_URL") or os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")).rstrip("/")
ALPACA_DATA_URL = os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets").rstrip("/")
# Bankroll hard-cap when sharing the prod account (so a fired ELITE pick can't size 50% of prod
# equity). Only applies when NOT isolated. Default $5k; override LOTTERY_SHARED_ACCOUNT_CAP_USD.
LOTTERY_SHARED_ACCOUNT_CAP_USD = float(os.environ.get("LOTTERY_SHARED_ACCOUNT_CAP_USD", "5000") or 5000)

LOTTERY_HALT = os.environ.get("MOMENTUM_LOTTERY_HALT", "").strip().lower() in ("1", "true", "yes", "on")
DRY_RUN = os.environ.get("LOTTERY_DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on")
TEST_MODE = os.environ.get("LOTTERY_TEST_MODE", "").strip().lower() in ("1", "true", "yes", "on")

NOTIONAL_USD = float(os.environ.get("LOTTERY_NOTIONAL_USD", "250"))
MAX_TICKERS = int(os.environ.get("LOTTERY_MAX_TICKERS", "10"))
TRAIL_PCT = float(os.environ.get("LOTTERY_TRAIL_PCT", "15.0"))
PRICE_MIN = float(os.environ.get("LOTTERY_PRICE_MIN", "1.50"))
PRICE_MAX = float(os.environ.get("LOTTERY_PRICE_MAX", "20.0"))
FRESHNESS_LOOKBACK_DAYS = int(os.environ.get("LOTTERY_FRESHNESS_DAYS", "30"))
MOVERS_LIMIT = int(os.environ.get("LOTTERY_MOVERS_LIMIT", "30"))

# Polygon integration (additive — if Polygon fails, Alpaca path still works)
USE_POLYGON_SCREENER = os.environ.get("LOTTERY_USE_POLYGON_SCREENER", "1").strip() in ("1", "true", "yes", "on")
USE_POLYGON_NEWS_GATE = os.environ.get("LOTTERY_USE_POLYGON_NEWS_GATE", "0").strip() in ("1", "true", "yes", "on")
NEWS_GATE_HOURS = int(os.environ.get("LOTTERY_NEWS_GATE_HOURS", "24"))

# Continuer prior gate (per-ticker historical T+5 continuer rate)
# Per docs/research-log/99 (aftermath analysis): chronic-fader tickers can be
# pre-filtered. Fri's 8 picks: 7 had history, all were chronic faders.
USE_CONTINUER_PRIOR = os.environ.get("LOTTERY_USE_CONTINUER_PRIOR", "0").strip() in ("1", "true", "yes", "on")
CONTINUER_PRIOR_MIN_RATE = float(os.environ.get("LOTTERY_CONTINUER_MIN_RATE", "0.03"))  # 3%
CONTINUER_PRIOR_MIN_APPEARANCES = int(os.environ.get("LOTTERY_CONTINUER_MIN_APPEARANCES", "5"))

# ML model gate (XGBoost + conformal; supersedes continuer-prior gate when on)
# Per doc 103: ML P>=0.30 produces +4.05%/trade Sharpe 2.91 walk-forward.
# Per doc 106: v2 ensemble + ticker_details = +6.82%/trade, +10.00% with HI-mag Ising gate.
USE_ML_MODEL = os.environ.get("LOTTERY_USE_ML_MODEL", "0").strip() in ("1", "true", "yes", "on")
ML_P_THRESHOLD = float(os.environ.get("LOTTERY_ML_P_THRESHOLD", "0.30"))

# Meta-scorer (session 109): unified v3-tuned + TCN-veto + Ising tier
# waterfall + conformal-modulated Kelly sizing. SUPERSEDES USE_ML_MODEL +
# USE_ISING_GATE when on. Per-pick notional is set by tier (5%/3%/2%/1% of
# bankroll) instead of the global LOTTERY_NOTIONAL_USD.
USE_META_SCORER = os.environ.get("LOTTERY_USE_META_SCORER", "0").strip() in ("1", "true", "yes", "on")
# Legacy fixed bankroll — used as a fallback if dynamic equity is unavailable
# AND as the floor when LOTTERY_BANKROLL_PCT==0.
META_BANKROLL_USD = float(os.environ.get("LOTTERY_META_BANKROLL_USD", "10000"))
# D280 (2026-05-05): dynamic bankroll = account_equity * LOTTERY_BANKROLL_PCT.
# Pre-D280: bankroll was hardcoded $10k → max ELITE pick = $5k = 3.5% of a
# $140k paper account. Production sized against 7% of available capital.
# With LOTTERY_BANKROLL_PCT=1.0 (default), Aggressive Kelly's 50/35/20/10
# tier caps map directly onto the full account: ELITE = 50% × $140k = $70k.
# Set LOTTERY_BANKROLL_PCT=0 to opt back into legacy $10k fixed sizing.
# Set to a fraction (e.g. 0.5) for ramp-up safety while gaining confidence.
LOTTERY_BANKROLL_PCT = float(os.environ.get("LOTTERY_BANKROLL_PCT", "1.0"))

# Aggressive Kelly mode (PAPER ONLY): bumps tier caps from conservative
# 5/3/2/1% to aggressive 50/35/20/10% — lets the meta-scorer express its
# full conviction. Sets MX_META_KELLY_PROFILE=aggressive for the inference
# module to pick up. NEVER enable on a live-money account; the +35-50%
# ELITE-tier sizing on a single microcap pick is paper-only experimental.
AGGRESSIVE_KELLY = os.environ.get("LOTTERY_AGGRESSIVE_KELLY", "0").strip() in ("1", "true", "yes", "on")
if AGGRESSIVE_KELLY:
    os.environ["MX_META_KELLY_PROFILE"] = "aggressive"

# Intraday VETOED-tier refresh (session 111): at 10:00 ET, re-evaluate
# the watchlist with first-30-min intraday paths to unlock the VETOED
# tier (v3t>=0.30 + MID-mag + TCN<0.30 = +12.74% n=73 WF). Requires
# bars from the broker (Alpaca) — cheap if already querying for
# heartbeat. Per-pick notional follows tier (Kelly-cap).
USE_INTRADAY_REFRESH = os.environ.get("LOTTERY_USE_INTRADAY_REFRESH", "0").strip() in ("1", "true", "yes", "on")
INTRADAY_REFRESH_HOUR = int(os.environ.get("LOTTERY_INTRADAY_REFRESH_HOUR", "10"))
INTRADAY_REFRESH_MIN = int(os.environ.get("LOTTERY_INTRADAY_REFRESH_MIN", "0"))

# Ising regime gate (combine with ML for +3pp lift per doc 106)
# When on, requires today's 5-day rolling magnetization to be in specified tercile.
USE_ISING_GATE = os.environ.get("LOTTERY_USE_ISING_GATE", "0").strip() in ("1", "true", "yes", "on")
ISING_REQUIRED_TERCILE = os.environ.get("LOTTERY_ISING_TERCILE", "HI").strip().upper()  # HI / MID / LO
ISING_MAG_HI_THRESHOLD = float(os.environ.get("LOTTERY_ISING_MAG_HI", "0.05"))
ISING_MAG_LO_THRESHOLD = float(os.environ.get("LOTTERY_ISING_MAG_LO", "-0.05"))

# Bug 1 fix: poll until filled instead of single check at +2.5s
FILL_POLL_TIMEOUT_S = float(os.environ.get("LOTTERY_FILL_POLL_TIMEOUT_S", "60"))
FILL_POLL_INTERVAL_S = float(os.environ.get("LOTTERY_FILL_POLL_INTERVAL_S", "2"))

# Time controls — relative to ET
ENTRY_HOUR, ENTRY_MIN = 9, 30      # market open
TIME_STOP_HOUR, TIME_STOP_MIN = 15, 55
EXIT_HOUR, EXIT_MIN = 16, 0


# ── Logger ────────────────────────────────────────────────────────

def setup_logger() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    handler_file = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler_file.setFormatter(logging.Formatter(fmt))
    handler_stream = logging.StreamHandler(sys.stdout)
    handler_stream.setFormatter(logging.Formatter(fmt))
    log = logging.getLogger("lottery")
    log.handlers.clear()
    log.addHandler(handler_file)
    log.addHandler(handler_stream)
    log.setLevel(logging.INFO)
    return log


log = setup_logger()


# ── Data classes ──────────────────────────────────────────────────

@dataclass
class Pick:
    ticker: str
    price: float
    pct_change: float
    is_first_appearance: bool
    # Optional meta-scorer fields (populated only when LOTTERY_USE_META_SCORER=1).
    # When set, override the global LOTTERY_NOTIONAL_USD per-pick.
    meta_tier: str | None = None
    meta_notional_usd: float | None = None
    meta_score: float | None = None

@dataclass
class Position:
    ticker: str
    qty: int
    entry_price: float
    entry_order_id: str
    trail_order_id: str | None = None
    trail_price_at_open: float | None = None
    closed_at: str | None = None
    exit_reason: str | None = None
    exit_price: float | None = None
    realized_pnl: float | None = None


# ── Alpaca HTTP helpers ──────────────────────────────────────────

class AlpacaClient:
    def __init__(self) -> None:
        if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
            raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set in env")
        self.headers = {
            "APCA-API-KEY-ID": ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
            "Content-Type": "application/json",
        }
        self.client = httpx.AsyncClient(headers=self.headers, timeout=30.0)  # noqa: async-leak  (closed via self.aclose())

    async def aclose(self) -> None:
        await self.client.aclose()

    async def get_account(self) -> dict:
        r = await self.client.get(f"{ALPACA_BASE_URL}/v2/account")
        r.raise_for_status()
        return r.json()

    async def get_clock(self) -> dict:
        r = await self.client.get(f"{ALPACA_BASE_URL}/v2/clock")
        r.raise_for_status()
        return r.json()

    async def get_movers(self, limit: int = 30) -> dict:
        """Returns {gainers: [{symbol, percent_change, price, change}], losers: [...]}"""
        r = await self.client.get(
            f"{ALPACA_DATA_URL}/v1beta1/screener/stocks/movers",
            params={"top": limit},
        )
        r.raise_for_status()
        return r.json()

    async def get_latest_quote(self, symbol: str) -> dict | None:
        try:
            r = await self.client.get(
                f"{ALPACA_DATA_URL}/v2/stocks/{symbol}/quotes/latest"
            )
            r.raise_for_status()
            return r.json().get("quote")
        except Exception as e:
            log.warning("get_latest_quote(%s) failed: %s", symbol, e)
            return None

    async def get_prev_day_bar(self, symbol: str) -> dict | None:
        """Fetch yesterday's COMPLETED day bar (for prev-day dvol).

        D279 (2026-05-05) bugfix: previously we passed only ``limit=2`` with
        no date range. At 09:25 ET (pre-market), the API returned today's
        in-progress bar with ``v=0`` for every microcap on the IEX feed
        (production: 0/10 tickers fetched). The caller's ``if bar_vol > 0``
        guard then silently rejected every result, falling back to the
        ``dvol_d0 = price * 1e6`` $1M stub.

        Fix: explicitly bound ``end`` to **yesterday 23:59 UTC**, forcing
        the API to return a completed bar regardless of when this is called.
        Default ``feed`` is now read from ``ALPACA_DATA_FEED`` (set to
        ``sip`` by ``lottery_paper_trade.ps1``); IEX-only deployments still
        work but with degraded microcap coverage.

        Returns ``{o, h, l, c, v, n, vw, t}`` or ``None``. Empty-bar
        responses now log a single WARNING (no more silent failure).
        """
        from datetime import datetime, timedelta, timezone
        try:
            end_dt = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
                hour=23, minute=59, second=59, microsecond=0
            )
            start_dt = end_dt - timedelta(days=10)  # 10d window catches weekends/holidays
            params = {
                "timeframe": "1Day",
                "limit": 5,
                "adjustment": "raw",
                "feed": os.environ.get("ALPACA_DATA_FEED", "iex"),
                "start": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            r = await self.client.get(
                f"{ALPACA_DATA_URL}/v2/stocks/{symbol}/bars",
                params=params,
            )
            r.raise_for_status()
            bars = r.json().get("bars", []) or []
            if not bars:
                log.warning(
                    "get_prev_day_bar(%s): API returned 0 bars (feed=%s start=%s end=%s) — "
                    "ticker may have no data on this feed; dvol will fall back to $1M stub",
                    symbol, params["feed"], params["start"], params["end"],
                )
                return None
            return bars[-1]
        except Exception as e:
            log.warning("get_prev_day_bar(%s) failed: %s", symbol, e)
            return None

    async def get_minute_bars(self, symbol: str, start_iso: str, end_iso: str,
                                limit: int = 60) -> list[dict]:
        """Fetch 1Min OHLCV bars between start/end ISO-8601 timestamps.

        Used by intraday VETOED-tier refresh (session 112). Returns a list of
        bar dicts: [{t, o, h, l, c, v, n, vw}, ...]. Empty list on failure.
        """
        try:
            params = {
                "timeframe": "1Min",
                "start": start_iso,
                "end": end_iso,
                "limit": limit,
                "adjustment": "raw",
                "feed": os.environ.get("ALPACA_DATA_FEED", "iex"),
            }
            r = await self.client.get(
                f"{ALPACA_DATA_URL}/v2/stocks/{symbol}/bars",
                params=params,
            )
            r.raise_for_status()
            return r.json().get("bars", []) or []
        except Exception as e:
            log.warning("get_minute_bars(%s) failed: %s", symbol, e)
            return []

    async def submit_market_buy(self, symbol: str, qty: int) -> dict:
        payload = {
            "symbol": symbol, "qty": str(qty), "side": "buy",
            "type": "market", "time_in_force": "day",
        }
        r = await self.client.post(f"{ALPACA_BASE_URL}/v2/orders", json=payload)
        r.raise_for_status()
        return r.json()

    async def submit_trailing_stop_sell(self, symbol: str, qty: int, trail_pct: float) -> dict:
        payload = {
            "symbol": symbol, "qty": str(qty), "side": "sell",
            "type": "trailing_stop", "time_in_force": "day",
            "trail_percent": str(trail_pct),
        }
        r = await self.client.post(f"{ALPACA_BASE_URL}/v2/orders", json=payload)
        r.raise_for_status()
        return r.json()

    async def submit_market_sell(self, symbol: str, qty: int) -> dict:
        payload = {
            "symbol": symbol, "qty": str(qty), "side": "sell",
            "type": "market", "time_in_force": "day",
        }
        r = await self.client.post(f"{ALPACA_BASE_URL}/v2/orders", json=payload)
        r.raise_for_status()
        return r.json()

    async def get_order(self, order_id: str) -> dict:
        r = await self.client.get(f"{ALPACA_BASE_URL}/v2/orders/{order_id}")
        r.raise_for_status()
        return r.json()

    async def cancel_order(self, order_id: str) -> dict | None:
        try:
            r = await self.client.delete(f"{ALPACA_BASE_URL}/v2/orders/{order_id}")
            return {"status": r.status_code}
        except Exception as e:
            log.warning("cancel_order(%s) failed: %s", order_id, e)
            return None

    async def list_positions(self) -> list[dict]:
        r = await self.client.get(f"{ALPACA_BASE_URL}/v2/positions")
        r.raise_for_status()
        return r.json()

    async def list_orders(self, status: str = "open", nested: bool = True) -> list[dict]:
        """List orders. status in {open, closed, all}."""
        r = await self.client.get(
            f"{ALPACA_BASE_URL}/v2/orders",
            params={"status": status, "nested": "true" if nested else "false"},
        )
        r.raise_for_status()
        return r.json()

    async def poll_until_filled(
        self,
        order_id: str,
        timeout_s: float,
        interval_s: float,
    ) -> dict:
        """Poll order_id until status reaches a terminal state or timeout.

        Returns the most recent order dict. Terminal: filled, canceled,
        expired, rejected, partially_filled (we treat partial as terminal
        success with whatever filled qty we got).

        Bug 1 fix: at market open the fill can take 5-30s. The previous
        single-check at +2.5s declared all orders 'unfilled' and skipped
        attaching trailing stops, leaving naked positions.
        """
        TERMINAL = {"filled", "canceled", "expired", "rejected"}
        deadline = asyncio.get_event_loop().time() + timeout_s
        last = await self.get_order(order_id)
        while True:
            status = (last.get("status") or "").lower()
            filled_qty = int(float(last.get("filled_qty") or 0))
            if status in TERMINAL:
                return last
            # partially_filled: usually transitions to filled within seconds.
            # Wait for the remaining unless we're already at deadline.
            if asyncio.get_event_loop().time() >= deadline:
                # If we got at least some fill, accept it; otherwise return as-is.
                return last
            await asyncio.sleep(interval_s)
            try:
                last = await self.get_order(order_id)
            except Exception as e:
                log.warning("poll_until_filled(%s): get_order error %s — retrying",
                            order_id, e)


# ── Freshness ────────────────────────────────────────────────────

def load_recent_tickers(lookback_days: int) -> set[str]:
    """All tickers seen in bar_recordings/ over the last N calendar days."""
    if not BAR_DIR.exists(): return set()
    cutoff = (datetime.now(ET) - timedelta(days=lookback_days)).date()
    recent: set[str] = set()
    for d in BAR_DIR.iterdir():
        if not d.is_dir(): continue
        try:
            dd = datetime.strptime(d.name, "%Y-%m-%d").date()
        except ValueError as e:
            log.debug("recent_bar_tickers: skip non-date dir %s: %s", d.name, e)
            continue
        if dd < cutoff: continue
        for f in d.glob("*.json"):
            recent.add(f.stem.upper())
    return recent


def load_lottery_history() -> set[str]:
    """All tickers we have ever traded in the lottery."""
    p = STATE_DIR / "lottery_traded_tickers.json"
    if not p.exists(): return set()
    try:
        return set(json.loads(p.read_text()))
    except Exception:
        return set()


def add_to_lottery_history(tickers: set[str]) -> None:
    p = STATE_DIR / "lottery_traded_tickers.json"
    existing = load_lottery_history()
    union = sorted(existing | tickers)
    p.write_text(json.dumps(union, indent=2))


# ── Polygon enrichment (additive; never blocks the lottery) ──────

async def fetch_polygon_gainers() -> list[dict]:
    """Pull /v2/snapshot/...gainers from Polygon. Returns Alpaca-shaped
    dicts so the rest of build_watchlist works unchanged.

    Returns [] silently on any failure — Polygon is opt-in enrichment, not
    a hard dependency.
    """
    if not USE_POLYGON_SCREENER:
        return []
    if not os.environ.get("POLYGON_API_KEY", "").strip():
        log.info("LOTTERY_USE_POLYGON_SCREENER=1 but POLYGON_API_KEY missing; skipping Polygon")
        return []
    try:
        # Lazy import + path setup to avoid hard dep on mx-arena being on PYTHONPATH
        import sys as _sys
        from pathlib import Path as _Path
        _here = _Path(__file__).resolve().parent.parent
        for p in (_here, _here / "mx-arena"):
            if str(p) not in _sys.path:
                _sys.path.insert(0, str(p))
        from data_providers.polygon import PolygonClient, PolygonEndpoints  # type: ignore
    except Exception as e:
        log.warning("Polygon SDK import failed: %s — skipping", e)
        return []
    try:
        client = PolygonClient.from_env()
    except Exception as e:
        log.warning("PolygonClient.from_env failed: %s — skipping", e)
        return []
    out: list[dict] = []
    try:
        async with client:
            ep = PolygonEndpoints(client)
            raw = await ep.gainers()
            for g in raw:
                # Polygon snapshot shape: {ticker, todaysChangePerc, lastTrade:{p}, day:{c,v}}
                sym = g.get("ticker")
                if not sym: continue
                pct = float(g.get("todaysChangePerc") or 0.0)
                price = 0.0
                lt = g.get("lastTrade") or {}
                if lt.get("p"): price = float(lt["p"])
                if not price:
                    day = g.get("day") or {}
                    if day.get("c"): price = float(day["c"])
                if not price: continue
                # Re-shape to match Alpaca screener row
                out.append({"symbol": sym, "price": price, "percent_change": pct,
                             "_source": "polygon"})
        log.info("Polygon gainers: %d candidates", len(out))
    except Exception as e:
        log.warning("Polygon gainers fetch failed: %s — skipping", e)
        return []
    return out


async def fetch_polygon_news_features(tickers: list[str], hours: int) -> dict[str, dict]:
    """For each ticker, return catalyst features from Polygon news + insights.
    Returns {} on failure (doesn't block the lottery)."""
    if not USE_POLYGON_NEWS_GATE or not tickers:
        return {}
    if not os.environ.get("POLYGON_API_KEY", "").strip():
        return {}
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _here = _Path(__file__).resolve().parent.parent
        for p in (_here, _here / "mx-arena"):
            if str(p) not in _sys.path:
                _sys.path.insert(0, str(p))
        from data_providers.polygon import PolygonClient, PolygonEndpoints  # type: ignore
        from polygon_news_catalyst import compute_features  # type: ignore
    except Exception as e:
        log.warning("Polygon news SDK import failed: %s", e)
        return {}
    out: dict[str, dict] = {}
    try:
        client = PolygonClient.from_env()
        async with client:
            ep = PolygonEndpoints(client)
            since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            for t in tickers:
                try:
                    arts = await ep.news(ticker=t, published_utc_gte=since,
                                          limit=20, max_pages=1)
                    out[t] = compute_features(arts, t)
                except Exception as e:
                    log.warning("  news(%s) failed: %s", t, e)
                    out[t] = {"ticker": t, "n_articles": 0, "error": str(e)}
    except Exception as e:
        log.warning("Polygon news bulk fetch failed: %s", e)
    return out


# ── Watchlist construction ───────────────────────────────────────

def compute_sizing_bankroll(account_equity: float) -> tuple[float, str]:
    """D280 (2026-05-05): resolve the bankroll used for Kelly position sizing.

    Returns ``(bankroll_usd, source_label)`` where source_label is a short
    string for the operator log line.

    Resolution order:
      1. If ``LOTTERY_BANKROLL_PCT > 0`` AND ``account_equity > 0`` → use
         ``account_equity * pct`` (DYNAMIC; default).
      2. Otherwise → fall back to ``META_BANKROLL_USD`` (LEGACY fixed
         $10k or whatever ``LOTTERY_META_BANKROLL_USD`` is set to).

    Pre-D280 production sized against the legacy $10k fixed bankroll on a
    $140k paper account → max ELITE pick was 3.5% of available capital.
    Default behavior post-D280: 100% of broker equity feeds the Aggressive
    Kelly tier caps (50/35/20/10%). Set ``LOTTERY_BANKROLL_PCT=0.5`` for
    half-account ramp-up, ``=0`` to opt back into legacy fixed sizing.
    """
    if LOTTERY_BANKROLL_PCT > 0 and account_equity > 0:
        bankroll = account_equity * LOTTERY_BANKROLL_PCT
        label = (f"dynamic ${bankroll:,.0f} = "
                 f"{LOTTERY_BANKROLL_PCT*100:.0f}% of ${account_equity:,.0f} equity")
    else:
        bankroll = META_BANKROLL_USD
        label = f"legacy fixed ${bankroll:,.0f} (LOTTERY_BANKROLL_PCT=0 or no equity)"
    # D262 ACCOUNT ISOLATION: on the SHARED prod account, hard-cap the bankroll so a fired ELITE
    # pick can't draw ~50% of prod's equity. Lift the cap by isolating (set LOTTERY_ALPACA_API_KEY).
    if not LOTTERY_ACCT_ISOLATED and bankroll > LOTTERY_SHARED_ACCOUNT_CAP_USD:
        bankroll = LOTTERY_SHARED_ACCOUNT_CAP_USD
        label = (f"CAPPED ${bankroll:,.0f} (SHARED prod account; set LOTTERY_ALPACA_API_KEY "
                 f"to isolate + lift the cap)")
    return bankroll, label


async def build_watchlist(client: AlpacaClient,
                            account_equity: float = 0.0) -> list[Pick]:
    """Get top movers from Alpaca + Polygon screeners; filter by price & freshness.

    Per doc 92 §6.1: Alpaca's screener was returning stale EOD-prior data at
    09:25 ET on 2026-05-01. Polygon snapshot is sub-second fresh.
    Per doc 96 §5: stack both sources, dedupe, take union.
    """
    log.info("Fetching movers (top %d) from Alpaca ...", MOVERS_LIMIT)
    movers = await client.get_movers(limit=MOVERS_LIMIT)
    gainers = movers.get("gainers", [])
    log.info("Alpaca gainers: %d", len(gainers))

    # Additive: Polygon snapshot
    poly_gainers = await fetch_polygon_gainers()

    # Merge: dedupe by symbol, prefer the source with higher pct_change
    by_sym: dict[str, dict] = {}
    for g in gainers:
        sym = (g.get("symbol") or "").upper()
        if not sym: continue
        g["_source"] = g.get("_source") or "alpaca"
        by_sym[sym] = g
    for g in poly_gainers:
        sym = (g.get("symbol") or "").upper()
        if not sym: continue
        existing = by_sym.get(sym)
        if existing is None:
            by_sym[sym] = g
        else:
            try:
                ex_pct = float(existing.get("percent_change") or 0)
                new_pct = float(g.get("percent_change") or 0)
                if new_pct > ex_pct:
                    by_sym[sym] = g
            except Exception as e:
                log.debug("merge_gainers: percent_change parse failed for %s: %s", sym, e)
    gainers = list(by_sym.values())
    log.info("After Alpaca+Polygon merge: %d unique candidates", len(gainers))

    if not gainers:
        log.warning("No gainers returned by screener — empty watchlist")
        return []

    # Apply price filter
    price_filtered = []
    for g in gainers:
        try:
            sym = g["symbol"]
            price = float(g["price"])
            pct = float(g.get("percent_change", 0))
        except Exception as e:
            log.debug("price_filter: malformed gainer row %r: %s", g, e)
            continue
        if not (PRICE_MIN <= price <= PRICE_MAX):
            log.debug("  %s @ $%.2f outside price band [%.2f, %.2f]",
                      sym, price, PRICE_MIN, PRICE_MAX)
            continue
        price_filtered.append(Pick(ticker=sym.upper(), price=price, pct_change=pct,
                                    is_first_appearance=False))
    log.info("After price filter [$%.2f, $%.2f]: %d candidates",
             PRICE_MIN, PRICE_MAX, len(price_filtered))

    # Apply freshness filter (against bar_recordings + lottery history)
    recent_in_bars = load_recent_tickers(FRESHNESS_LOOKBACK_DAYS)
    lottery_history = load_lottery_history()
    log.info("Freshness corpus: %d tickers in bar_recordings (last %dd) + %d tickers in lottery history",
             len(recent_in_bars), FRESHNESS_LOOKBACK_DAYS, len(lottery_history))

    fresh = []
    recurring = []
    for p in price_filtered:
        is_fresh = (p.ticker not in recent_in_bars) and (p.ticker not in lottery_history)
        p.is_first_appearance = is_fresh
        if is_fresh:
            fresh.append(p)
        else:
            recurring.append(p)
    log.info("Freshness: %d FRESH, %d recurring", len(fresh), len(recurring))

    # Strategy: prioritize fresh, fill remaining with recurring up to MAX_TICKERS
    selected = fresh[:MAX_TICKERS]
    remaining = MAX_TICKERS - len(selected)
    if remaining > 0:
        selected.extend(recurring[:remaining])

    # ── META-SCORER GATE (session 109; SUPERSEDES USE_ML_MODEL + USE_ISING_GATE) ──
    # When LOTTERY_USE_META_SCORER=1, the v3-tuned ensemble + TCN-veto + Ising
    # 5d-mag are stacked into a tier waterfall (ELITE/HIGH/VETOED/BROAD/SKIP).
    # Per-pick notional is set by tier-based Kelly cap on LOTTERY_META_BANKROLL_USD.
    # See scripts/ml_meta_scorer_inference.py + docs/research-log/109_v3_meta_scorer_breakthrough.md
    if USE_META_SCORER and selected:
        # D280: dynamic bankroll = account_equity * LOTTERY_BANKROLL_PCT.
        sizing_bankroll, _bankroll_label = compute_sizing_bankroll(account_equity)
        log.info("Applying META-SCORER gate (bankroll: %s)...", _bankroll_label)
        try:
            import sys as _sys
            from pathlib import Path as _Path
            _here = _Path(__file__).resolve().parent
            if str(_here) not in _sys.path:
                _sys.path.insert(0, str(_here))
            import math
            from ml_meta_scorer_inference import MetaScorer  # type: ignore

            scorer = MetaScorer.load_default()
            log.info("  loaded meta-scorer: features=%d, mag_5d=%.4f (%s)",
                     len(scorer.feature_columns), scorer.mag_5d, scorer.mag_label)

            now = datetime.now(ET)
            today_total = max(len(selected), 1)
            sorted_by_intra = sorted(selected, key=lambda x: -x.pct_change)
            avg_today_intra = sum(x.pct_change for x in selected) / max(today_total, 1) / 100.0

            # Optional priors lookup (best-effort)
            prior_path = REPO / "data" / "polygon_warehouse" / "derived" / "per_ticker_continuer_prior.parquet"
            prior_map: dict = {}
            if prior_path.exists():
                try:
                    import duckdb as _duckdb
                    rows = _duckdb.connect().sql(
                        f"SELECT ticker, n_appearances, n_continuer, n_fader, "
                        f"smoothed_continuer_rate, avg_ret_t5_pct "
                        f"FROM read_parquet('{prior_path.as_posix()}')"
                    ).fetchall()
                    prior_map = {r[0]: {"n": r[1], "n_cont": r[2], "n_fade": r[3],
                                          "rate": r[4], "avg_t5": r[5]} for r in rows}
                except Exception as _pe:
                    log.warning("  prior lookup failed: %s", _pe)

            # CRITICAL fix (s122): load ticker_details for sector dummies +
            # log_market_cap + log_float + log_employees + log_days_since_ipo.
            # Without these, v3-tuned-16fold sees 24 zeros and collapses to
            # v3t=~0.12 across all candidates (Monday s121 deploy = 0 picks).
            td_path = REPO / "data" / "polygon_warehouse" / "reference" / "ticker_details.parquet"
            td_map: dict = {}
            if td_path.exists():
                try:
                    import pandas as _pd
                    td_df = _pd.read_parquet(td_path)
                    cols = ["ticker", "market_cap", "sic_code", "sic_description",
                              "total_employees", "share_class_shares_outstanding",
                              "list_date"]
                    td_df = td_df[[c for c in cols if c in td_df.columns]]
                    td_df = td_df.drop_duplicates(subset=["ticker"], keep="last")
                    td_map = td_df.set_index("ticker").to_dict(orient="index")
                    log.info("  loaded ticker_details: %d tickers", len(td_map))
                except Exception as _te:
                    log.warning("  ticker_details lookup failed: %s", _te)

            # s123: pre-fetch real prev-day volumes to avoid the
            # `dvol_d0 = price * 1e6` stub that biases scores low/high
            # on real-volume outliers. Concurrent fetch, ~1s for 10 picks.
            # D279 (2026-05-05) hardening: log per-ticker rejection reasons
            # (no-bar, zero-volume, exception) so a future "0/N tickers"
            # outage is diagnosable from the production log without a probe.
            vol_map: dict = {}
            try:
                bar_results = await asyncio.gather(
                    *[client.get_prev_day_bar(p.ticker) for p in selected],
                    return_exceptions=True,
                )
                _no_bar = 0
                _zero_vol = 0
                _exc = 0
                for p, bar in zip(selected, bar_results):
                    if isinstance(bar, Exception):
                        _exc += 1
                        log.debug("  vol_map skip %s: exception=%s", p.ticker, bar)
                        continue
                    if not bar:
                        _no_bar += 1
                        continue  # already logged in get_prev_day_bar
                    bar_close = float(bar.get("c", p.price))
                    bar_vol = float(bar.get("v", 0))
                    if bar_vol <= 0:
                        _zero_vol += 1
                        log.debug("  vol_map skip %s: bar_vol=0 (bar=%s)", p.ticker, bar)
                        continue
                    vol_map[p.ticker] = {
                        "dvol": bar_close * bar_vol,
                        "vol": bar_vol,
                        "vwap": float(bar.get("vw", bar_close)),
                    }
                log.info(
                    "  pre-fetched prev-day bars: %d/%d tickers "
                    "(no_bar=%d, zero_vol=%d, exc=%d, feed=%s)",
                    len(vol_map), len(selected),
                    _no_bar, _zero_vol, _exc,
                    os.environ.get("ALPACA_DATA_FEED", "iex"),
                )
            except Exception as _ve:
                log.warning("  prev-day volume pre-fetch failed: %s", _ve)

            kept: list[Pick] = []
            for p in selected:
                pr = prior_map.get(p.ticker, {"n": 0, "n_cont": 0, "n_fade": 0,
                                                 "rate": 20.78, "avg_t5": 0})
                rank_intra = next((i + 1 for i, x in enumerate(sorted_by_intra)
                                    if x.ticker == p.ticker), today_total)
                # ── Ticker-details enrichment (s122 critical fix) ──
                td = td_map.get(p.ticker, {})
                _mcap = td.get("market_cap") if td else None
                _emp = td.get("total_employees") if td else None
                _list = td.get("list_date") if td else None
                _sso = td.get("share_class_shares_outstanding") if td else None
                _sic = td.get("sic_code") if td else None
                _sic_str = (td.get("sic_description") or "").upper() if td else ""
                # Derive log features
                log_market_cap = math.log(max(float(_mcap) if _mcap else 50e6, 1))
                mcap_known = 1 if _mcap else 0
                log_employees = math.log(max(float(_emp) if _emp else 100, 1))
                log_float = math.log(max(float(_sso) if _sso else 1e7, 1))
                # Days since IPO
                if _list:
                    try:
                        from datetime import date as _date
                        ld = _date.fromisoformat(str(_list)[:10])
                        days = max((now.date() - ld).days, 1)
                    except Exception:
                        days = 3650
                else:
                    days = 3650
                log_days_since_ipo = math.log1p(days)
                # SIC code as int (default 0)
                try:
                    sic_int = int(float(_sic)) if _sic else 0
                except Exception:
                    sic_int = 0
                sic_group = sic_int // 100
                # Sector dummies (matching engineer_features in v3 ensemble)
                def _has(needle): return 1 if needle in _sic_str else 0
                sec_pharma = _has("PHARMACEUTICAL")
                sec_bio = _has("BIOLOGICAL")
                sec_medical = 1 if ("SURGICAL" in _sic_str or "MEDICAL" in _sic_str) else 0
                sec_software = _has("SOFTWARE")
                sec_finance = _has("FINANCE")
                sec_semi = _has("SEMICONDUCTOR")
                sec_spac = _has("BLANK CHECK")
                sec_reit = _has("REAL ESTATE")

                # s123: real dvol from prev-day bar (not stub price*1e6).
                # When fetch failed, fall back to the stub.
                _vol = vol_map.get(p.ticker, {})
                _dvol = _vol.get("dvol") or (p.price * 1e6)
                _today_avg_dvol_proxy = (sum(vol_map.get(x.ticker, {}).get("dvol",
                                                                                p.price * 1e6)
                                                 for x in selected)
                                            / max(len(selected), 1))

                # Build the feature dict — all 54 v3-tuned-16fold features.
                features = {
                    "log_open": math.log(max(p.price, 0.01)),
                    "log_dvol_d0": math.log(max(_dvol, 1)),
                    "intraday_pct": p.pct_change / 100.0,
                    "intraday_pct_log": math.log1p(max(p.pct_change / 100.0, 0)),
                    "ret_open_close_d0": p.pct_change / 100.0,
                    "close_strength": 1.0,
                    "prior_n": pr["n"] or 0,
                    "prior_n_log": math.log1p(pr["n"] or 0),
                    "prior_cont_rate": pr["rate"] or 20.78,
                    "prior_fade_rate": (43.36 + (pr["n_fade"] or 0) * 100) / (100 + (pr["n"] or 0)),
                    "prior_avg_t5": (pr["avg_t5"] or 0) / 100.0,
                    "prior_7d_count": 0,
                    "dow": now.weekday(), "month": now.month, "year": now.year,
                    "day_of_year": now.timetuple().tm_yday,
                    "rank_intra": rank_intra, "rank_intra_log": math.log1p(rank_intra),
                    "rank_dvol": rank_intra, "n_today_total": today_total,
                    "intra_vs_today_avg": (p.pct_change / 100.0) / max(avg_today_intra, 0.01),
                    # s123: real ratio (was stub 1.0)
                    "dvol_vs_today_avg": _dvol / max(_today_avg_dvol_proxy, 1),
                    "prior_avg_intra": 0.5, "prior_avg_oc": 0.0,
                    "prior_30d_count": pr["n"] or 0, "intra_vs_prior": 1.0,
                    "day_of_month": now.day,
                    "week_of_month": (now.day - 1) // 7 + 1,
                    "quarter": (now.month - 1) // 3 + 1,
                    "year_frac": now.timetuple().tm_yday / 365.25,
                    # ── Ticker-details (s122 fix) ──
                    "log_market_cap": log_market_cap,
                    "mcap_known": mcap_known,
                    "log_employees": log_employees,
                    "log_days_since_ipo": log_days_since_ipo,
                    "sic_code": sic_int,
                    "sic_group": sic_group,
                    "sec_pharma": sec_pharma,
                    "sec_bio": sec_bio,
                    "sec_medical": sec_medical,
                    "sec_software": sec_software,
                    "sec_finance": sec_finance,
                    "sec_semi": sec_semi,
                    "sec_spac": sec_spac,
                    "sec_reit": sec_reit,
                    "log_float": log_float,
                    # LLM survivor (engineered from prior_cont/prior_n smoothing)
                    "log_rank_x_prior_continuer":
                        math.log1p(rank_intra) * (1 + (pr["n_cont"] or 0)) / (5 + (pr["n"] or 0)),
                    # Path features (no bars at 9:30 — set to 0 + has_path=0)
                    "first_5min_max_close": 0.0,
                    "first_5min_min_close": 0.0,
                    "last_5min_avg_close": 0.0,
                    "first_5min_avg_volz": 0.0,
                    "last_5min_avg_volz": 0.0,
                    "u_shape_intraday": 0.0,
                    "volume_acceleration": 0.0,
                    "has_path": 0,
                }
                # Live: no intraday_path at 09:30 ET (no bars yet); pass None.
                # TCN-veto won't fire pre-market; VETOED tier becomes available
                # only after the first 30 RTH min, when callers refresh decision.
                decision = scorer.score_candidate(
                    candidate=features | {"ticker": p.ticker},
                    intraday_path=None,
                    bankroll=sizing_bankroll,  # D280: dynamic, not legacy $10k
                )
                if decision.tier == "SKIP":
                    log.warning("  META-SKIP %s: tier=SKIP score=%.3f reason=%s",
                                p.ticker, decision.meta_score, decision.reason)
                    continue
                p.meta_tier = decision.tier
                p.meta_notional_usd = decision.notional_usd
                p.meta_score = decision.meta_score
                log.info("  META-PASS %s: tier=%s score=%.3f kelly=%.4f notional=$%.2f reason=%s",
                         p.ticker, decision.tier, decision.meta_score,
                         decision.kelly_frac, decision.notional_usd, decision.reason)
                kept.append(p)
            log.info("META-SCORER kept %d of %d", len(kept), len(selected))
            selected = kept
            # When meta-scorer is on, suppress the legacy ML+Ising blocks below
            log.info("META-SCORER active; legacy ML/Ising gates suppressed for this run")
        except Exception as e:
            log.error("META-SCORER error: %s - falling back to legacy gates", e, exc_info=True)
            # Fall through to legacy paths

    # Optional: ML model gate (XGBoost continuer model). When enabled AND
    # the meta-scorer is OFF, SUPERSEDES the simple continuer-prior gate.
    # Each candidate gets a P(continuer) prediction; only those above
    # LOTTERY_ML_P_THRESHOLD pass.
    if not USE_META_SCORER and USE_ML_MODEL and selected:
        log.info("Applying ML continuer model gate (P >= %.2f)...", ML_P_THRESHOLD)
        try:
            import sys as _sys
            from pathlib import Path as _Path
            _here = _Path(__file__).resolve().parent
            if str(_here) not in _sys.path:
                _sys.path.insert(0, str(_here))
            import duckdb as _duckdb
            from ml_position_sizer import load_model, predict_for_row  # type: ignore

            model = load_model()
            log.info("  loaded model: features=%d, training_rows=%d",
                     len(model["feature_columns"]), model["training_rows"])

            # Need walk-forward features per candidate. Quick lookup of prior
            # from per_ticker_continuer_prior parquet.
            prior_path = REPO / "data" / "polygon_warehouse" / "derived" / "per_ticker_continuer_prior.parquet"
            con = _duckdb.connect()
            prior_rows = con.sql(
                f"SELECT ticker, n_appearances, n_continuer, n_fader, "
                f"smoothed_continuer_rate, avg_ret_t5_pct "
                f"FROM read_parquet('{prior_path.as_posix()}')"
            ).fetchall()
            prior_map = {r[0]: {"n": r[1], "n_cont": r[2], "n_fade": r[3],
                                 "rate": r[4], "avg_t5": r[5]} for r in prior_rows}

            now = datetime.now(ET)
            gated_ml = []
            for p in selected:
                pr = prior_map.get(p.ticker, {"n": 0, "n_cont": 0, "n_fade": 0,
                                                "rate": 20.78, "avg_t5": 0})
                # Build features matching the trained model's columns.
                # Both v1 (16 features) and v2 (37 features) supported via
                # features.get(col, 0) in predict_for_row.
                import math
                # Total candidates today (for cross-sectional rank)
                today_total = max(len(selected), 1)
                # Find this pick's rank by intraday pct (descending)
                sorted_by_intra = sorted(selected, key=lambda x: -x.pct_change)
                rank_intra = next((i + 1 for i, x in enumerate(sorted_by_intra) if x.ticker == p.ticker), today_total)
                avg_today_intra = sum(x.pct_change for x in selected) / max(today_total, 1) / 100.0
                features = {
                    # v1 features
                    "log_open": math.log(max(p.price, 0.01)),
                    "log_dvol_d0": math.log(max(p.price * 1e6, 1)),  # rough estimate
                    "intraday_pct": p.pct_change / 100.0,
                    "intraday_pct_log": math.log1p(max(p.pct_change / 100.0, 0)),
                    "ret_open_close_d0": p.pct_change / 100.0,
                    "close_strength": 1.0,
                    "prior_n": pr["n"] or 0,
                    "prior_n_log": math.log1p(pr["n"] or 0),
                    "prior_cont_rate": pr["rate"] or 20.78,
                    "prior_fade_rate": (43.36 + (pr["n_fade"] or 0) * 100) / (100 + (pr["n"] or 0)),
                    "prior_avg_t5": (pr["avg_t5"] or 0) / 100.0,
                    "prior_7d_count": 0,
                    "dow": now.weekday(),
                    "month": now.month,
                    "year": now.year,
                    "day_of_year": now.timetuple().tm_yday,
                    # v2 features (cross-sectional + rolling)
                    "rank_intra": rank_intra,
                    "rank_intra_log": math.log1p(rank_intra),
                    "rank_dvol": rank_intra,  # proxy; we don't have real dvol ranking at scan time
                    "n_today_total": today_total,
                    "intra_vs_today_avg": (p.pct_change / 100.0) / max(avg_today_intra, 0.01),
                    "dvol_vs_today_avg": 1.0,
                    "prior_avg_intra": 0.5,  # neutral default
                    "prior_avg_oc": 0.0,
                    "prior_30d_count": pr["n"] or 0,  # use total prior_n as approximation
                    "intra_vs_prior": 1.0,
                    "day_of_month": now.day,
                    "week_of_month": (now.day - 1) // 7 + 1,
                    "quarter": (now.month - 1) // 3 + 1,
                    "year_frac": now.timetuple().tm_yday / 365.25,
                    # ticker_details features (zero defaults)
                    "log_market_cap": 0.0,
                    "mcap_known": 0,
                    "log_employees": 0.0,
                    "log_days_since_ipo": 0.0,
                    "sic_code": 0,
                    "sic_group": 0,
                    "log_float": 0.0,
                }
                p_cont, width = predict_for_row(features, model)
                p.is_first_appearance = (pr["n"] or 0) == 0  # repurpose for ML reasoning
                if p_cont < ML_P_THRESHOLD:
                    log.warning("  ML-GATE-OUT %s: P(cont)=%.3f < %.2f  (width=%.2f)",
                                p.ticker, p_cont, ML_P_THRESHOLD, width)
                    continue
                log.info("  ML-PASS %s: P(cont)=%.3f  width=%.2f  prior_n=%d  prior_rate=%.1f%%",
                         p.ticker, p_cont, width, pr["n"] or 0, pr["rate"] or 0)
                gated_ml.append(p)
            log.info("ML gate kept %d of %d", len(gated_ml), len(selected))
            selected = gated_ml
        except Exception as e:
            log.warning("ML gate error: %s - falling back to non-ML", e)

    # Optional: Ising regime gate (run BEFORE ML to short-circuit if regime is off).
    # Per doc 106: v2 + Mag=HI gate produced +10.00%/trade walk-forward.
    # Suppressed when LOTTERY_USE_META_SCORER=1 (meta-scorer applies its own
    # mag tercile via the tier waterfall).
    if not USE_META_SCORER and USE_ISING_GATE and selected:
        try:
            import duckdb as _duckdb
            ising_path = REPO / "data" / "polygon_warehouse" / "derived" / "ising_daily.parquet"
            if not ising_path.exists():
                log.warning("USE_ISING_GATE=1 but ising_daily.parquet missing - skipping gate")
            else:
                _con = _duckdb.connect()
                # Get the 5-day rolling magnetization through the most recent date
                rows = _con.sql(f"""
                    WITH last5 AS (
                        SELECT d, magnetization, n_huge_up
                        FROM read_parquet('{ising_path.as_posix()}')
                        ORDER BY d DESC LIMIT 5
                    )
                    SELECT AVG(magnetization) AS mag_5d, AVG(n_huge_up) AS breadth_5d
                    FROM last5
                """).fetchone()
                mag_5d = float(rows[0]) if rows[0] is not None else 0.0
                breadth_5d = float(rows[1]) if rows[1] is not None else 0.0

                # Determine current tercile
                if mag_5d > ISING_MAG_HI_THRESHOLD:
                    cur_tercile = "HI"
                elif mag_5d < ISING_MAG_LO_THRESHOLD:
                    cur_tercile = "LO"
                else:
                    cur_tercile = "MID"

                if cur_tercile != ISING_REQUIRED_TERCILE:
                    log.warning("ISING-GATE-BLOCKED: today's mag_5d=%.4f tercile=%s, "
                                "required=%s (gate active; no trades today)",
                                mag_5d, cur_tercile, ISING_REQUIRED_TERCILE)
                    log.info("Ising regime indicators: mag_5d=%.4f breadth_5d=%.0f", mag_5d, breadth_5d)
                    selected = []
                else:
                    log.info("ISING-GATE-PASS: mag_5d=%.4f tercile=%s matches required=%s "
                             "(breadth_5d=%.0f); proceeding",
                             mag_5d, cur_tercile, ISING_REQUIRED_TERCILE, breadth_5d)
        except Exception as e:
            log.warning("Ising gate error: %s - skipping", e)

    # Optional: continuer-prior gate (per-ticker historical T+5 continuer rate).
    # Tickers with sufficient catalog history AND continuer rate < threshold
    # are CHRONIC FADERS — gate them out. Tickers with no catalog history
    # (truly fresh) PASS by default (rewarded with default ~21% rate).
    if USE_CONTINUER_PRIOR and selected:
        prior_path = REPO / "data" / "polygon_warehouse" / "derived" / "per_ticker_continuer_prior.parquet"
        if not prior_path.exists():
            log.warning("USE_CONTINUER_PRIOR=1 but %s missing - skipping gate", prior_path.name)
        else:
            try:
                import duckdb as _duckdb
                con = _duckdb.connect()
                rows = con.sql(
                    f"SELECT ticker, n_appearances, smoothed_continuer_rate, avg_ret_t5_pct "
                    f"FROM read_parquet('{prior_path.as_posix()}')"
                ).fetchall()
                prior_map = {r[0]: {"n": r[1], "rate": r[2], "avg_t5": r[3]} for r in rows}
                gated = []
                for p in selected:
                    pr = prior_map.get(p.ticker)
                    if pr is None:
                        log.info("  PRIOR-PASS %s: no catalog history (truly fresh)", p.ticker)
                        gated.append(p)
                        continue
                    if pr["n"] < CONTINUER_PRIOR_MIN_APPEARANCES:
                        log.info("  PRIOR-PASS %s: only %d appearances (insufficient history)",
                                 p.ticker, pr["n"])
                        gated.append(p)
                        continue
                    if pr["rate"] / 100.0 < CONTINUER_PRIOR_MIN_RATE:
                        log.warning("  PRIOR-GATE-OUT %s: continuer_rate=%.1f%% < %.1f%% (chronic fader; n=%d, avg_t5=%.1f%%)",
                                    p.ticker, pr["rate"], CONTINUER_PRIOR_MIN_RATE * 100,
                                    pr["n"], pr["avg_t5"])
                        continue
                    log.info("  PRIOR-PASS %s: continuer_rate=%.1f%% (n=%d, avg_t5=%+.1f%%)",
                             p.ticker, pr["rate"], pr["n"], pr["avg_t5"])
                    gated.append(p)
                log.info("Continuer prior gate kept %d of %d", len(gated), len(selected))
                selected = gated
            except Exception as e:
                log.warning("continuer prior gate error: %s - skipping", e)

    # Optional: news-catalyst gate (E7 from doc 93). Skip candidates with
    # zero articles OR negative consensus in last NEWS_GATE_HOURS hours.
    if USE_POLYGON_NEWS_GATE and selected:
        log.info("Applying news catalyst gate (last %dh)...", NEWS_GATE_HOURS)
        feats = await fetch_polygon_news_features(
            [p.ticker for p in selected], NEWS_GATE_HOURS,
        )
        gated = []
        for p in selected:
            f = feats.get(p.ticker, {})
            n = f.get("n_articles", 0)
            wsent = f.get("weighted_sentiment", 0)
            tier = f.get("latest_publisher_tier")
            if n == 0:
                log.warning("  GATE-OUT %s: 0 news articles in last %dh",
                            p.ticker, NEWS_GATE_HOURS)
                continue
            if wsent < -0.5:
                log.warning("  GATE-OUT %s: negative consensus weighted_sentiment=%.2f",
                            p.ticker, wsent)
                continue
            if tier is not None and tier < 0.3:
                log.warning("  GATE-OUT %s: weak publisher tier=%.2f",
                            p.ticker, tier)
                continue
            log.info("  KEEP %s: n=%d wsent=%+.2f tier=%s",
                     p.ticker, n, wsent, tier)
            gated.append(p)
        log.info("News gate kept %d of %d", len(gated), len(selected))
        selected = gated

    for p in selected:
        log.info("  PICK: %-6s @ $%6.2f  +%5.1f%%  fresh=%s",
                 p.ticker, p.price, p.pct_change, p.is_first_appearance)
    return selected


# ── Trade execution ──────────────────────────────────────────────

async def open_lottery_positions(client: AlpacaClient, picks: list[Pick]) -> list[Position]:
    positions: list[Position] = []
    for p in picks:
        # Per-pick notional: meta-scorer override if present, else default
        per_pick_notional = (p.meta_notional_usd
                              if p.meta_notional_usd is not None and p.meta_notional_usd > 0
                              else NOTIONAL_USD)
        qty = max(1, int(per_pick_notional / max(p.price, 0.01)))
        tier_str = f" tier={p.meta_tier}" if p.meta_tier else ""
        log.info("ORDER: BUY %d %s (notional ~$%.2f, trail %.1f%%)%s",
                 qty, p.ticker, qty * p.price, TRAIL_PCT, tier_str)
        if DRY_RUN or LOTTERY_HALT:
            log.info("  [SKIPPED — %s]", "DRY_RUN" if DRY_RUN else "HALTED")
            positions.append(Position(ticker=p.ticker, qty=qty, entry_price=p.price,
                                       entry_order_id="DRY", trail_price_at_open=p.price * (1 - TRAIL_PCT/100)))
            continue
        try:
            buy_resp = await client.submit_market_buy(p.ticker, qty)
            buy_id = buy_resp.get("id", "?")
            log.info("  BUY submitted id=%s", buy_id)

            # Bug 1 fix: poll until filled with up to FILL_POLL_TIMEOUT_S
            # (default 60s). The previous 2.5s single-check missed every fill
            # at market open and left positions naked.
            order_status = await client.poll_until_filled(
                buy_id, FILL_POLL_TIMEOUT_S, FILL_POLL_INTERVAL_S,
            )
            filled_qty = int(float(order_status.get("filled_qty", 0)))
            filled_avg = float(order_status.get("filled_avg_price") or p.price)
            terminal = (order_status.get("status") or "").lower()

            if filled_qty == 0:
                log.warning("  BUY %s NOT FILLED after %.0fs (status=%s) — "
                            "registering as ATTEMPTED so EOD sweep can clean up",
                            p.ticker, FILL_POLL_TIMEOUT_S, terminal)
                # Bug 2/4 fix: even unfilled, we register the position so the
                # EOD sweep can cancel the buy and (if it filled later)
                # close the resulting position.
                positions.append(Position(
                    ticker=p.ticker, qty=qty, entry_price=p.price,
                    entry_order_id=buy_id, trail_order_id=None,
                    trail_price_at_open=None,
                ))
                continue

            log.info("  BUY filled qty=%d avg=$%.4f status=%s",
                     filled_qty, filled_avg, terminal)
            # Submit trailing stop
            try:
                trail_resp = await client.submit_trailing_stop_sell(
                    p.ticker, filled_qty, TRAIL_PCT,
                )
                trail_id = trail_resp.get("id", "?")
                log.info("  TRAIL submitted id=%s trail=%.1f%%", trail_id, TRAIL_PCT)
            except Exception as e:
                log.error("  TRAIL SUBMIT FAILED for %s (qty=%d): %s — "
                          "position registered without trail; EOD sweep will close",
                          p.ticker, filled_qty, e)
                trail_id = None
            positions.append(Position(
                ticker=p.ticker, qty=filled_qty, entry_price=filled_avg,
                entry_order_id=buy_id, trail_order_id=trail_id,
                trail_price_at_open=filled_avg * (1 - TRAIL_PCT/100) if trail_id else None,
            ))
        except Exception as e:
            log.error("  ORDER FAILED for %s: %s", p.ticker, e)
    return positions


async def force_close_remaining(
    client: AlpacaClient,
    positions: list[Position],
    lottery_tickers: set[str] | None = None,
) -> None:
    """Bug 2 fix: close positions based on what's ACTUALLY in the Alpaca
    account, not just what the runner tracked. The previous version missed
    any position whose buy fill happened after the (broken) 2.5s timeout.

    `lottery_tickers` is the set of tickers we attempted to trade today.
    We restrict the force-close to those tickers so we never accidentally
    close positions opened by the main bot or the operator.
    """
    log.info("=== FORCE CLOSE @ %02d:%02d ET ===", TIME_STOP_HOUR, TIME_STOP_MIN)
    if DRY_RUN or LOTTERY_HALT:
        log.info("  [SKIPPED — %s]", "DRY_RUN" if DRY_RUN else "HALTED")
        return

    # Default: union of attempted picks + what we tracked
    if lottery_tickers is None:
        lottery_tickers = {p.ticker for p in positions}

    # Snapshot live state
    try:
        live_positions = await client.list_positions()
        live_by_sym = {p["symbol"]: p for p in live_positions}
    except Exception as e:
        log.error("Failed to list positions: %s", e)
        return

    try:
        open_orders = await client.list_orders(status="open")
    except Exception as e:
        log.error("Failed to list open orders: %s", e)
        open_orders = []

    # Bug 2 fix: cancel ALL open lottery orders before closing positions
    # (previous version only cancelled the one we tracked, leaving zombie
    # trail-stops on the books).
    n_cancelled = 0
    for o in open_orders:
        sym = o.get("symbol")
        if sym not in lottery_tickers:
            continue
        try:
            await client.cancel_order(o["id"])
            log.info("  cancelled %s order %s on %s",
                     o.get("type"), o["id"], sym)
            n_cancelled += 1
        except Exception as e:
            log.warning("  cancel order %s on %s failed: %s", o["id"], sym, e)
    log.info("  cancelled %d open lottery orders", n_cancelled)

    # Brief pause so cancels settle before we try to sell
    await asyncio.sleep(2)

    # Refresh position list (cancels may have affected it via partial fills)
    try:
        live_positions = await client.list_positions()
        live_by_sym = {p["symbol"]: p for p in live_positions}
    except Exception as e:
        log.error("Failed to refresh positions after cancels: %s", e)
        return

    # Build a quick lookup of tracked positions by ticker so we can update them
    tracked_by_sym = {pos.ticker: pos for pos in positions}

    # Close every position whose ticker we attempted today
    n_closed = 0
    for sym, lp in live_by_sym.items():
        if sym not in lottery_tickers:
            continue
        try:
            qty = int(float(lp.get("qty", 0)))
            if qty <= 0:
                continue
            sell_resp = await client.submit_market_sell(sym, qty)
            log.info("  TIME_STOP SELL %d %s (id=%s)",
                     qty, sym, sell_resp.get("id"))
            n_closed += 1
            pos = tracked_by_sym.get(sym)
            if pos:
                pos.exit_reason = "time_stop"
                pos.closed_at = datetime.now(ET).isoformat()
            else:
                # Untracked position — append a stub so it shows in session report
                positions.append(Position(
                    ticker=sym, qty=qty,
                    entry_price=float(lp.get("avg_entry_price") or 0),
                    entry_order_id="UNTRACKED",
                    exit_reason="time_stop",
                    closed_at=datetime.now(ET).isoformat(),
                ))
        except Exception as e:
            log.error("  TIME_STOP FAILED for %s: %s", sym, e)
    log.info("  force-close issued %d market sells", n_closed)


async def session_report(
    client: AlpacaClient,
    positions: list[Position],
    lottery_tickers: set[str] | None = None,
) -> dict:
    """At EOD, reconcile runner state with live Alpaca state and emit P&L.

    Bug 5 fix: previous version only iterated `positions` (runner-tracked).
    If a position got opened but lost from the tracking list, it was
    invisible to the report. This version walks BOTH the runner list and
    today's filled orders, taking the union.
    """
    log.info("=== SESSION REPORT ===")
    if lottery_tickers is None:
        lottery_tickers = {p.ticker for p in positions}

    report = {
        "date": TODAY_ET, "n_positions": len(positions),
        "config": {"notional": NOTIONAL_USD, "max": MAX_TICKERS, "trail_pct": TRAIL_PCT,
                    "dry_run": DRY_RUN, "halted": LOTTERY_HALT},
        "lottery_tickers_attempted": sorted(lottery_tickers),
        "positions": [],
    }

    # Bug 5 fix: reconcile by querying today's closed orders. For each
    # ticker we attempted, sum up filled BUY notional vs filled SELL
    # notional. The delta is the realized P&L (within today).
    try:
        all_orders = await client.list_orders(status="all")
    except Exception as e:
        log.warning("session_report: list_orders(all) failed: %s", e)
        all_orders = []

    # Group orders by ticker
    by_ticker: dict[str, list[dict]] = {}
    for o in all_orders:
        sym = o.get("symbol")
        if sym not in lottery_tickers:
            continue
        # Only include orders submitted today (by created_at ET date)
        ca = o.get("created_at") or ""
        if not ca.startswith(TODAY_ET):
            continue
        by_ticker.setdefault(sym, []).append(o)

    total_pnl = 0.0
    pos_by_sym = {p.ticker: p for p in positions}
    for sym in sorted(lottery_tickers):
        orders = by_ticker.get(sym, [])
        # Compute realized P&L = sum(filled SELL notional) - sum(filled BUY notional)
        buy_notional = 0.0; buy_qty = 0
        sell_notional = 0.0; sell_qty = 0
        for o in orders:
            try:
                fq = int(float(o.get("filled_qty") or 0))
                fa = float(o.get("filled_avg_price") or 0)
            except Exception as e:
                log.debug("realized_pnl: bad order row for %s: %s", sym, e)
                continue
            if fq == 0 or fa == 0: continue
            side = (o.get("side") or "").lower()
            if side == "buy":
                buy_qty += fq; buy_notional += fq * fa
            elif side == "sell":
                sell_qty += fq; sell_notional += fq * fa

        avg_buy = buy_notional / buy_qty if buy_qty else None
        avg_sell = sell_notional / sell_qty if sell_qty else None
        realized = sell_notional - (sell_qty * avg_buy) if (avg_buy and sell_qty) else 0.0

        pos = pos_by_sym.get(sym)
        if pos:
            if avg_buy is not None: pos.entry_price = avg_buy
            if avg_sell is not None:
                pos.exit_price = avg_sell
                pos.realized_pnl = realized

        if buy_qty > 0 and sell_qty == buy_qty:
            log.info("  %-6s qty=%d entry=$%.4f exit=$%.4f pnl=$%+.2f CLOSED",
                     sym, buy_qty, avg_buy, avg_sell, realized)
            total_pnl += realized
        elif buy_qty > 0 and sell_qty == 0:
            log.warning("  %-6s qty=%d entry=$%.4f STILL OPEN (no sell fills)",
                         sym, buy_qty, avg_buy)
        elif buy_qty > 0 and sell_qty > 0 and sell_qty < buy_qty:
            log.warning("  %-6s buy_qty=%d sell_qty=%d PARTIAL (entry=$%.4f exit=$%.4f pnl=$%+.2f)",
                         sym, buy_qty, sell_qty, avg_buy, avg_sell, realized)
            total_pnl += realized
        else:
            log.info("  %-6s no fills today (buy_qty=%d sell_qty=%d)",
                     sym, buy_qty, sell_qty)

        report["positions"].append({
            "ticker": sym,
            "buy_qty": buy_qty, "avg_buy_px": avg_buy,
            "sell_qty": sell_qty, "avg_sell_px": avg_sell,
            "realized_pnl_usd": realized,
            "exit_reason": pos.exit_reason if pos else None,
        })

    report["total_realized_pnl_usd"] = total_pnl
    report["n_positions"] = len([r for r in report["positions"]
                                  if r.get("buy_qty", 0) > 0])
    log.info("TOTAL REALIZED PNL: $%+.2f over %d positions",
             total_pnl, report["n_positions"])
    out_path = STATE_DIR / f"session_report_{TODAY_ET}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    log.info("Wrote %s", out_path)
    return report


# ── Sleep helpers ────────────────────────────────────────────────

async def sleep_until_et(hour: int, minute: int) -> None:
    if TEST_MODE:
        log.info("TEST_MODE: skipping sleep_until_et(%02d:%02d)", hour, minute)
        return
    while True:
        now = datetime.now(ET)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now >= target:
            return
        delta = (target - now).total_seconds()
        log.info("Sleeping %.0fs until %02d:%02d ET ...", delta, hour, minute)
        await asyncio.sleep(min(delta, 60))


# ── Main orchestration ───────────────────────────────────────────

async def main_async() -> int:
    log.info("=" * 70)
    log.info("LOTTERY RUNNER START — %s", TODAY_ET)
    log.info("=" * 70)
    log.info("Config: notional=$%.2f  max=%d  trail=%.1f%%  price=[%.2f-%.2f]  fresh_lookback=%dd  movers_limit=%d",
             NOTIONAL_USD, MAX_TICKERS, TRAIL_PCT, PRICE_MIN, PRICE_MAX,
             FRESHNESS_LOOKBACK_DAYS, MOVERS_LIMIT)
    log.info("Safety: HALT=%s  DRY_RUN=%s", LOTTERY_HALT, DRY_RUN)

    if LOTTERY_HALT:
        log.warning("MOMENTUM_LOTTERY_HALT=1 — running in observation mode only")

    client = AlpacaClient()
    try:
        # Preflight
        try:
            account = await client.get_account()
            log.info("Account: %s  status=%s  equity=$%.2f  cash=$%.2f",
                     account.get("account_number", "?"), account.get("status"),
                     float(account.get("equity", 0)), float(account.get("cash", 0)))
            if LOTTERY_ACCT_ISOLATED:
                log.info("D262 account: ISOLATED (using LOTTERY_ALPACA_* sub-account)")
            else:
                log.warning(
                    "D262 account: SHARED with prod (no LOTTERY_ALPACA_API_KEY) -- bankroll "
                    "hard-capped to $%.0f. Create a separate Alpaca paper account and set "
                    "LOTTERY_ALPACA_API_KEY/_SECRET_KEY in secrets to isolate.",
                    LOTTERY_SHARED_ACCOUNT_CAP_USD,
                )
            assert account.get("status") in ("ACTIVE", "PAPER_TRADING"), \
                f"Account status {account.get('status')} not tradeable"
        except Exception as e:
            log.error("PREFLIGHT FAILED: %s", e)
            return 1

        # Wait until 09:25 ET to fetch movers (5 min before open)
        now = datetime.now(ET)
        if now.hour < 9 or (now.hour == 9 and now.minute < 25):
            await sleep_until_et(9, 25)

        # Build watchlist
        # D280: pass live broker equity so meta-scorer Kelly sizes against
        # full account, not the legacy $10k fixed bankroll.
        _equity_for_sizing = float(account.get("equity", 0) or 0)
        picks = await build_watchlist(client, account_equity=_equity_for_sizing)
        if not picks:
            log.warning("No picks — exiting cleanly")
            return 0

        # Wait until 09:30 to enter
        await sleep_until_et(ENTRY_HOUR, ENTRY_MIN)

        # Capture the canonical set of tickers we attempted today. This
        # drives the heartbeat filter and the EOD force-close, regardless
        # of whether a particular order actually filled or got tracked.
        lottery_tickers: set[str] = {p.ticker for p in picks}

        # Open positions
        positions = await open_lottery_positions(client, picks)
        log.info("Opened %d tracked position(s) over %d attempts",
                 len([p for p in positions if p.entry_order_id != "DRY"]),
                 len(picks))

        # Bug 3 fix: only persist history for REAL runs. DRY_RUN and
        # TEST_MODE invocations must not pollute the freshness corpus.
        if not (DRY_RUN or LOTTERY_HALT or TEST_MODE):
            add_to_lottery_history(lottery_tickers)
        else:
            log.info("Skipping lottery history write (DRY=%s HALT=%s TEST=%s)",
                     DRY_RUN, LOTTERY_HALT, TEST_MODE)

        # ── INTRADAY VETOED-TIER REFRESH (session 111) ──
        # At LOTTERY_INTRADAY_REFRESH_HOUR:MIN ET (default 10:00), re-evaluate
        # the original watchlist now that 30 RTH min of bars are available.
        # Candidates that scored BROAD/SKIP at 9:30 may upgrade to VETOED
        # (TCN<0.30 in MID-mag = +12.74%/trade WF n=73). Currently a NO-OP
        # placeholder unless USE_INTRADAY_REFRESH=1 AND USE_META_SCORER=1;
        # full implementation requires a live minute-bar fetch (Alpaca
        # /v2/stocks/{ticker}/bars or polygon WS). See ml_intraday_refresh.py
        # for the offline reference implementation.
        intraday_refresh_done = False
        if USE_INTRADAY_REFRESH and USE_META_SCORER:
            log.info("INTRADAY-REFRESH armed for %02d:%02d ET",
                     INTRADAY_REFRESH_HOUR, INTRADAY_REFRESH_MIN)

        # Periodic monitoring loop until time-stop. In TEST_MODE, run one
        # iteration and break.
        _test_iter = 0
        while True:
            now = datetime.now(ET)
            target = now.replace(hour=TIME_STOP_HOUR, minute=TIME_STOP_MIN, second=0, microsecond=0)
            if now >= target: break
            if TEST_MODE and _test_iter >= 1:
                log.info("TEST_MODE: exiting monitoring loop after 1 iteration")
                break
            _test_iter += 1

            # Trigger intraday refresh exactly once at the configured time
            refresh_target = now.replace(hour=INTRADAY_REFRESH_HOUR,
                                          minute=INTRADAY_REFRESH_MIN,
                                          second=0, microsecond=0)
            if (USE_INTRADAY_REFRESH and USE_META_SCORER
                    and not intraday_refresh_done
                    and now >= refresh_target):
                intraday_refresh_done = True
                log.info("INTRADAY-REFRESH @ %s ET — second-pass scoring with "
                         "30-min bars (n=%d original picks)",
                         now.strftime("%H:%M:%S"), len(picks))
                try:
                    import sys as _sys
                    from pathlib import Path as _Path
                    _here = _Path(__file__).resolve().parent
                    if str(_here) not in _sys.path:
                        _sys.path.insert(0, str(_here))
                    from ml_meta_scorer_inference import MetaScorer  # type: ignore
                    from ml_intraday_refresh import (  # type: ignore
                        build_path_from_alpaca_bars,
                    )
                    refresh_scorer = MetaScorer.load_default()

                    # Build start/end ISO for "today 9:30-10:00 ET"
                    today = now.replace(hour=9, minute=30, second=0, microsecond=0)
                    start_iso = today.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    end_dt = now.replace(hour=INTRADAY_REFRESH_HOUR,
                                          minute=INTRADAY_REFRESH_MIN,
                                          second=0, microsecond=0)
                    end_iso = end_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                    n_upgrades = 0
                    n_processed = 0
                    new_entries: list[Pick] = []
                    for p in picks:
                        # Skip already-positioned picks (only consider those
                        # that did NOT enter at 9:30, e.g. SKIPped)
                        if p.meta_tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
                            continue   # already entered with a tier
                        bars_resp = await client.get_minute_bars(
                            p.ticker, start_iso, end_iso, limit=60,
                        )
                        path = build_path_from_alpaca_bars(bars_resp)
                        if path is None:
                            continue   # not enough bars
                        n_processed += 1

                        # Re-score (we don't have full feature dict for
                        # SKIPped picks here — use price-based stub).
                        import math as _math
                        features = {
                            "ticker": p.ticker,
                            "log_open": _math.log(max(p.price, 0.01)),
                            "log_dvol_d0": _math.log(max(p.price * 1e6, 1)),
                            "intraday_pct": p.pct_change / 100.0,
                            "intraday_pct_log": _math.log1p(max(p.pct_change / 100.0, 0)),
                            "ret_open_close_d0": p.pct_change / 100.0,
                        }
                        decision = refresh_scorer.score_candidate(
                            features, intraday_path=path,
                            bankroll=META_BANKROLL_USD,
                        )
                        if decision.tier in ("VETOED", "HIGH", "ELITE"):
                            n_upgrades += 1
                            p.meta_tier = decision.tier
                            p.meta_notional_usd = decision.notional_usd
                            p.meta_score = decision.meta_score
                            log.info("  REFRESH-UPGRADE %s: %s -> %s "
                                     "score=%.3f notional=$%.2f tcn=%.3f",
                                     p.ticker, "SKIP", decision.tier,
                                     decision.meta_score,
                                     decision.notional_usd,
                                     decision.tcn_proba or -1)
                            new_entries.append(p)
                    log.info("INTRADAY-REFRESH summary: processed=%d, "
                             "upgrades=%d (will fire entries below)",
                             n_processed, n_upgrades)

                    # Fire BUYS for the new VETOED+ entries
                    if new_entries and not (DRY_RUN or LOTTERY_HALT):
                        new_positions = await open_lottery_positions(client, new_entries)
                        positions.extend(new_positions)
                        lottery_tickers.update({p.ticker for p in new_entries})
                        log.info("INTRADAY-REFRESH opened %d new tracked "
                                 "position(s)",
                                 len([p for p in new_positions
                                       if p.entry_order_id != "DRY"]))
                    elif new_entries:
                        log.info("INTRADAY-REFRESH would open %d entries "
                                 "(suppressed: DRY_RUN=%s HALT=%s)",
                                 len(new_entries), DRY_RUN, LOTTERY_HALT)
                except Exception as e:
                    log.warning("  INTRADAY-REFRESH failed: %s", e, exc_info=True)
            # Bug 4 fix: HEARTBEAT filters live positions by the canonical
            # lottery_tickers set (everything we attempted today), not the
            # runner-tracked positions list (which may be empty if a fill
            # arrived after the poll timeout).
            try:
                live = await client.list_positions()
                live_lot = [p for p in live if p["symbol"] in lottery_tickers]
                if live_lot:
                    summary = ", ".join(
                        f"{p['symbol']}={float(p.get('unrealized_plpc', 0))*100:+.1f}%"
                        for p in live_lot
                    )
                    total_unrealized = sum(float(p.get("unrealized_pl", 0)) for p in live_lot)
                    log.info("HEARTBEAT @ %s ET - %d open: %s | unrealized=$%+.2f",
                             now.strftime("%H:%M:%S"), len(live_lot), summary,
                             total_unrealized)
                else:
                    log.info("HEARTBEAT @ %s ET - no lottery positions open",
                             now.strftime("%H:%M:%S"))
            except Exception as e:
                log.warning("HEARTBEAT failed: %s", e)
            sleep_for = min(60, (target - now).total_seconds())
            await asyncio.sleep(max(5, sleep_for))

        # Force-close any remaining (uses live Alpaca state, not just tracked)
        await force_close_remaining(client, positions, lottery_tickers)

        # Wait for force-close fills then write session report
        await asyncio.sleep(15)
        await session_report(client, positions, lottery_tickers)

        log.info("LOTTERY RUNNER END")
        return 0
    finally:
        await client.aclose()


def main() -> int:
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        log.warning("KeyboardInterrupt — shutting down")
        return 130
    except Exception as e:
        log.exception("FATAL: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
