"""
MOMENTUM-X Alpaca Data Client

### ARCHITECTURAL CONTEXT
Node ID: data.alpaca_client
Graph Link: docs/memory/graph_state.json → "data.alpaca_client"

### RESEARCH BASIS
Implements snapshot-based market data retrieval per ADR-002.
Alpaca API latency: ~1.5ms live, ~731ms paper (DATA-001).
Rate limit: 200 req/min — batch operations critical.

### CRITICAL INVARIANTS
1. WebSocket jitter absorbed via 1-minute snapshot buckets (ADR-002 §1).
2. RVOL denominator via historical bars time-bucketing (ADR-002 §2, MOMENTUM_LOGIC.md §2).
3. All order submissions default to paper trading (INV-007).
4. Exponential backoff reconnection for WebSocket resilience (ADR-002 §4).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

from config.settings import AlpacaConfig
from src.utils.rate_limiter import (
    TokenBucketRateLimiter,
    trading_rate_limiter,
    market_data_rate_limiter,
)

logger = logging.getLogger(__name__)

# ── Constants (justified) ────────────────────────────────────────────
# Rate limit: 200 req/min per Alpaca docs (DATA-001, CONSTRAINT-004)
# Operational cap: 180 req/min (10% safety buffer per ADR-004 §2)
MAX_REQUESTS_PER_MINUTE = 200
OPERATIONAL_CAP_PER_MINUTE = 180
# Reconnect backoff: 1s base, 30s cap per ADR-002 §4
RECONNECT_BASE_SECONDS = 1
RECONNECT_MAX_SECONDS = 30
# Health ping interval per ADR-002 §4
HEALTH_PING_INTERVAL = 30
# Snapshot batch size: Alpaca supports up to 100 tickers per snapshot request
SNAPSHOT_BATCH_SIZE = 100

# D294 (2026-05-13): Alpaca enforces an absolute $0.01 floor between
# the stop_price and the entry/base_price on OTO and bracket orders.
# Sub-$1 stocks (e.g. QUCY at ~$0.55) trip this when our percent-based
# stop math (entry × (1 - 0.02) at $0.55 = $0.539, rounded to $0.5390)
# lands closer than $0.01 to base. The 422 response is silent except
# in low-level logs and triggers a BRIDGE_CANCEL ghost-fill retry storm.
# This helper enforces the floor BEFORE the order is sent.
ALPACA_STOP_BASE_TICK = 0.01


def _clamp_stop_below_base(
    stop_loss: float, base_price: float, side: str = "long",
) -> float:
    """Return ``stop_loss`` clamped to satisfy Alpaca's $0.01 absolute
    distance rule against ``base_price``.

    Long side (sell-stop must be at least $0.01 BELOW the limit/entry):
        result = min(stop_loss, base_price - 0.01)
    Short side (buy-stop must be at least $0.01 ABOVE the limit/entry):
        result = max(stop_loss, base_price + 0.01)

    The result is rounded to the appropriate SEC Rule 612 tick:
    2 decimals for ``base_price >= $1.00``, 4 decimals otherwise.

    NOTE: this widens the stop, which is the safe direction. Risk-sized
    quantity is unaffected; the worst case is a slightly larger loss
    on stop-out (one cent on a sub-$1 stock = ~1.8% relative loss).
    """
    decimals = 2 if base_price >= 1.0 else 4
    if side == "long":
        ceiling = round(base_price - ALPACA_STOP_BASE_TICK, decimals)
        clamped = min(stop_loss, ceiling)
    elif side == "short":
        floor = round(base_price + ALPACA_STOP_BASE_TICK, decimals)
        clamped = max(stop_loss, floor)
    else:
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")
    return round(clamped, decimals)


def _d277_halt_response(
    *,
    symbol: str,
    qty: int,
    limit_price: float,
    stop_loss: float,
    side: str,
) -> dict[str, Any] | None:
    """D277 HALT_NEW_ENTRIES (2026-04-28): operator kill switch helper.

    Returns a structured 'halted_by_operator' response when:
      - env var MOMENTUM_HALT_NEW_ENTRIES is truthy (1/true/yes/on), OR
      - settings.execution.halt_new_entries is True

    Returns None when entries are NOT halted — caller proceeds normally.

    Env wins over config (operator override is fastest). Settings load
    failure defaults to NOT halted (better to allow normal operation
    than silently freeze the system on a config bug).

    Used by both submit_oto_order (long entries) and
    submit_oto_short_order (short entries). Exits, stops, ratchets,
    and other order types are NOT gated by this helper.
    """
    import os as _os
    _halt_env = _os.environ.get("MOMENTUM_HALT_NEW_ENTRIES", "").strip().lower()
    _halt = _halt_env in {"1", "true", "yes", "on"}
    if not _halt:
        try:
            from config.settings import Settings as _S
            _halt = bool(getattr(_S().execution, "halt_new_entries", False))
        except Exception:  # noqa: BLE001 — settings load must never block exits
            _halt = False
    if not _halt:
        # doc 207: Operator file-based kill switch (data/HALT_NEW_ENTRIES), checked LIVE on
        # every submission so the out-of-process Operator can HALT new entries WITHOUT a
        # restart. Additive — it only ever ADDS a halt condition; positions, exits, stops,
        # and ratchets are never gated by it. Re-enable = delete the file (Operator/Pierce).
        try:
            import os as _os2
            _halt_file = _os2.path.join(
                _os2.path.dirname(_os2.path.dirname(_os2.path.dirname(__file__))),
                "data", "HALT_NEW_ENTRIES")
            if _os2.path.exists(_halt_file):
                _halt = True
        except Exception:
            pass
    if not _halt:
        return None
    logger.warning(
        "D277 HALT_NEW_ENTRIES: refusing OTO %s submission for %s "
        "qty=%d limit=$%.4f stop=$%.4f. To re-enable: unset "
        "MOMENTUM_HALT_NEW_ENTRIES env var AND set "
        "settings.execution.halt_new_entries=False.",
        side, symbol, qty, limit_price, stop_loss,
    )
    # 2026-05-12 fix: id was "" which fails the trade_context Pydantic
    # schema (min_length=1) downstream, generating PHASE0_SCHEMA_VALIDATION_FAILED
    # warnings (8 today, one per halted entry attempt). Synthesize a
    # halt-prefixed sentinel id so downstream instrumentation can record
    # the attempt cleanly while remaining unambiguously distinguishable
    # from a real Alpaca order_id (which are UUID-style without prefixes).
    from datetime import datetime as _dt, timezone as _tz
    _halt_id = f"halted-{symbol}-{_dt.now(_tz.utc).strftime('%Y%m%dT%H%M%S')}"
    # 2026-05-12: also fire Discord alert so operator sees blocks in real
    # time (doc 156 incident: 8 silent halts only discovered post-EOD).
    # Uses the OPS alert webhook from settings; sync-safe + rate-limited
    # per ticker (1 alert/min/symbol) inside alert_halt_blocked_entry_sync.
    try:
        from src.monitoring.alerts import alert_halt_blocked_entry_sync
        from config.settings import Settings
        _webhook = Settings().ops.alert_webhook_url
        alert_halt_blocked_entry_sync(
            ticker=symbol, side=side, qty=qty, limit_price=limit_price,
            stop_loss=stop_loss, halt_reason="MOMENTUM_HALT_NEW_ENTRIES",
            webhook_url=_webhook,
        )
    except Exception:
        # Discord alert is best-effort; never block a halt response on it.
        pass
    return {
        "id": _halt_id,
        "status": "halted_by_operator",
        "halt_reason": "MOMENTUM_HALT_NEW_ENTRIES",
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "legs": [],
    }


def _check_reentry_ban(symbol: str) -> dict[str, Any] | None:
    """doc 282 LOSER-RE-ENTRY BAN (2026-07-04): skip re-entering a ticker that took a
    realized loss recently. Doc-281 counterfactual on the clean book: every re-entry
    within ~7 sessions of a loss in the same ticker was a loss or a scratch (verified
    two ways: 8 trades -$9,948 / 5 trades -$6,027 under a stricter rule; ZERO forgone
    winners). The failure mode of this gate is trade-less, never trade-worse.

    Env-gated per call (no restart needed): MOMENTUM_REENTRY_BAN_DAYS = N calendar
    days (~10 ≈ 7 sessions). Unset/0/invalid = OFF (default). Long entries only —
    exits, stops, ratchets, shorts are NOT gated. Any read/parse failure = no ban
    (never block trading on an instrumentation bug)."""
    import os as _os
    raw = _os.environ.get("MOMENTUM_REENTRY_BAN_DAYS", "").strip()
    try:
        ban_days = int(float(raw)) if raw else 0
    except ValueError:
        ban_days = 0
    if ban_days <= 0:
        return None
    try:
        import json as _json
        from datetime import date as _date, datetime as _dt, timedelta as _td, timezone as _tz
        _tr = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))),
            "data", "trade_results.jsonl")
        if not _os.path.exists(_tr):
            return None
        cutoff = _date.today() - _td(days=ban_days)
        last_loss = None
        with open(_tr, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = _json.loads(line)
                except Exception:
                    continue
                if r.get("ticker") != symbol:
                    continue
                if (r.get("pnl") or 0) < -1.0:
                    d = str(r.get("session_date", ""))[:10]
                    if d and d >= cutoff.isoformat():
                        last_loss = d if (last_loss is None or d > last_loss) else last_loss
        if last_loss is None:
            return None
        logger.warning(
            "doc282 REENTRY_BAN: refusing long entry %s — realized loss on %s within "
            "%dd window. Failure mode is trade-less. Disable: unset MOMENTUM_REENTRY_BAN_DAYS.",
            symbol, last_loss, ban_days,
        )
        _ban_id = f"reentry-ban-{symbol}-{_dt.now(_tz.utc).strftime('%Y%m%dT%H%M%S')}"
        return {
            "id": _ban_id,
            "status": "halted_by_operator",
            "halt_reason": f"MOMENTUM_REENTRY_BAN_DAYS={ban_days} (loss {last_loss})",
            "symbol": symbol,
            "qty": "0",
            "side": "buy",
            "legs": [],
        }
    except Exception:  # noqa: BLE001 — the ban must never block trading on its own bug
        return None


class AlpacaDataClient:
    """
    Unified Alpaca client for market data (REST + WebSocket) and order execution.

    Node ID: data.alpaca_client
    Graph Link: docs/memory/graph_state.json → "data.alpaca_client"

    Provides:
    - Multi-ticker snapshot retrieval (current price, prev close, volume)
    - Historical bars for RVOL volume profile computation (MOMENTUM_LOGIC.md §2)
    - Order submission (market, limit, bracket) for paper/live trading
    - Gap percentage computation from snapshots

    Ref: DATA-001 (Alpaca Markets API)
    Ref: ADR-002 (Snapshot-based architecture)
    """

    def __init__(self, config: AlpacaConfig) -> None:
        self._config = config
        self._headers = {
            "APCA-API-KEY-ID": config.api_key,
            "APCA-API-SECRET-KEY": config.secret_key,
            "Content-Type": "application/json",
        }
        self._data_base = config.data_url
        self._trade_base = config.base_url

        # Rate limiters per ADR-004 §2 (CONSTRAINT-004)
        self._trading_limiter = trading_rate_limiter()
        self._data_limiter = market_data_rate_limiter()

        # D85: Persistent HTTP client with connection pooling.
        # Reuses TCP connections across requests (keep-alive) instead of
        # creating a new httpx.AsyncClient per request (saves ~2ms × 15 calls).
        self._http_client: httpx.AsyncClient | None = None

    # ── Market Data: Active Tickers ──────────────────────────────────

    async def get_most_active_tickers(self, limit: int = 20) -> list[str]:
        """
        Fetch most active stocks via Alpaca screener API.

        Uses GET /v1beta1/screener/stocks/most-actives to discover
        which tickers are seeing the highest volume/trade activity.
        Falls back to the curated MOMENTUM_UNIVERSE if the screener
        endpoint fails (v1beta1 — may be unstable or unavailable
        for some API plans).

        Args:
            limit: Maximum number of tickers to return.

        Returns:
            List of ticker symbols, e.g., ["AAPL", "TSLA", "NVDA"]

        Ref: DATA-001 (Alpaca screener endpoint)
        Ref: ADR-002 §1 (universe discovery)
        """
        try:
            raw = await self._data_get(
                f"{self._data_base}/v1beta1/screener/stocks/most-actives",
                params={"top": limit},
            )
            # Response format: {"most_actives": [{"symbol": "AAPL", ...}, ...]}
            actives = raw.get("most_actives", [])
            tickers = [
                item["symbol"]
                for item in actives
                if isinstance(item, dict) and "symbol" in item
            ]
            if tickers:
                logger.info(
                    "Screener returned %d most-active tickers (top: %s)",
                    len(tickers), ", ".join(tickers[:5]),
                )
                return tickers[:limit]
        except Exception as e:
            logger.warning(
                "Screener API failed: %s — falling back to MOMENTUM_UNIVERSE", e
            )

        # Fallback: curated momentum universe.
        # Bug U fix (2026-04-23): wrap import in try/except. The
        # constant lived in scenario_builder.py before that file was
        # deleted; restored as fail-closed empty list in
        # premarket_research.py. The except guard catches any future
        # rename / move so we never silently fall through to ImportError.
        try:
            from src.data.premarket_research import MOMENTUM_UNIVERSE
        except ImportError as _ie:
            logger.warning(
                "Bug U: MOMENTUM_UNIVERSE import failed (%s) — returning empty list, "
                "next scan iteration will retry primary screener", _ie,
            )
            return []
        logger.info(
            "Using MOMENTUM_UNIVERSE fallback (%d tickers)", len(MOMENTUM_UNIVERSE[:limit])
        )
        return MOMENTUM_UNIVERSE[:limit]

    # ── Market Data: Top Movers (Gappers) ──────────────────────────────

    async def get_top_movers(self, limit: int = 20) -> list[str]:
        """
        D117: Fetch top gaining stocks via Alpaca screener movers endpoint.

        Uses GET /v1beta1/screener/stocks/movers to discover stocks with
        the largest percentage moves. This catches small-cap gappers that
        don't appear in "most active" until later in the session.

        Bug: CHNR and MOBX (Mar 19) were only found in RESCAN at 10:14 ET
        because "most active" is volume-based — small-caps don't accumulate
        enough volume to rank at market open. The movers endpoint catches
        them by percentage change instead.

        Args:
            limit: Maximum number of tickers to return.

        Returns:
            List of ticker symbols sorted by percentage gain.
        """
        try:
            raw = await self._data_get(
                f"{self._data_base}/v1beta1/screener/stocks/movers",
                params={"top": limit},
            )
            # Response format: {"gainers": [...], "losers": [...]}
            # We only want gainers for momentum trading
            gainers = raw.get("gainers", [])
            tickers = [
                item["symbol"]
                for item in gainers
                if isinstance(item, dict) and "symbol" in item
            ]
            if tickers:
                logger.info(
                    "D117: Movers screener returned %d top gainers (top: %s)",
                    len(tickers), ", ".join(tickers[:5]),
                )
                return tickers[:limit]
        except Exception as e:
            logger.debug(
                "D117: Movers screener failed (non-fatal): %s — "
                "relying on most-active tickers only", e
            )

        return []

    # ── Market Data: Snapshots ───────────────────────────────────────

    async def get_snapshots(
        self, tickers: list[str]
    ) -> dict[str, dict[str, Any]]:
        """
        Fetch latest snapshots for multiple tickers.
        Returns normalized dict: {ticker: {last_price, prev_close, volume, bid, ask, ...}}

        Uses Alpaca GET /v2/stocks/snapshots?symbols=X,Y,Z
        Batches in groups of 100 per Alpaca limit.

        Ref: DATA-001 (Alpaca snapshot endpoint)
        Ref: ADR-002 §1 (snapshot-based architecture)
        """
        if not tickers:
            return {}

        result: dict[str, dict[str, Any]] = {}

        for i in range(0, len(tickers), SNAPSHOT_BATCH_SIZE):
            batch = tickers[i : i + SNAPSHOT_BATCH_SIZE]
            symbols_param = ",".join(batch)
            # Sweep fix: use _data_get (rate-limited) instead of _get (unlimited).
            # _get bypasses the data API rate limiter, risking 429 errors under load.
            raw = await self._data_get(
                f"{self._data_base}/v2/stocks/snapshots",
                params={"symbols": symbols_param, "feed": self._config.feed},
            )

            for ticker, snap in raw.items():
                result[ticker] = self._normalize_snapshot(ticker, snap)

            # D123: Log tickers that were requested but got no snapshot.
            # This is how SLND.WS silently disappeared — Alpaca returned
            # no data for it and nobody noticed.
            _missing = [t for t in batch if t not in raw]
            if _missing:
                logger.warning(
                    "D123: %d/%d tickers got no snapshot (dropped silently): %s",
                    len(_missing), len(batch), ", ".join(_missing[:10]),
                )

        return result

    def _normalize_snapshot(
        self, ticker: str, raw: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Normalize Alpaca snapshot into our domain format.

        Maps:
        - latestTrade.p → last_price
        - prevDailyBar.c → prev_close
        - dailyBar.v → volume
        - latestQuote.bp/ap → bid/ask
        """
        latest_trade = raw.get("latestTrade", {})
        latest_quote = raw.get("latestQuote", {})
        daily_bar = raw.get("dailyBar", {})
        prev_bar = raw.get("prevDailyBar", {})
        minute_bar = raw.get("minuteBar", {})

        return {
            "ticker": ticker,
            "last_price": latest_trade.get("p", 0.0),
            "prev_close": prev_bar.get("c", 0.0),
            "volume": daily_bar.get("v", 0),
            "prev_volume": prev_bar.get("v", 0),  # Previous day's full-session volume (ADV proxy)
            "bid": latest_quote.get("bp", 0.0),
            "ask": latest_quote.get("ap", 0.0),
            "bid_size": latest_quote.get("bs", 0),
            "ask_size": latest_quote.get("as", 0),
            "day_open": daily_bar.get("o", 0.0),
            "day_high": daily_bar.get("h", 0.0),
            "day_low": daily_bar.get("l", 0.0),
            "vwap": daily_bar.get("vw", 0.0),  # D78: Alpaca VWAP from dailyBar for exit intelligence
            "minute_volume": minute_bar.get("v", 0),
        }

    @staticmethod
    def compute_gap_pct(snapshot: dict[str, Any]) -> float:
        """
        Compute gap percentage from normalized snapshot.

        GAP% = (P_current - P_close(t-1)) / P_close(t-1)
        Ref: MOMENTUM_LOGIC.md §3
        """
        prev_close = snapshot.get("prev_close", 0.0)
        if prev_close <= 0:
            return 0.0
        # Sweep fix: use .get() to avoid KeyError on partial/empty snapshots
        last_price = snapshot.get("last_price", 0.0)
        if last_price == 0.0:
            return 0.0
        return (last_price - prev_close) / prev_close

    # ── Market Data: Bars ────────────────────────────────────────────

    async def get_bars(
        self,
        symbol: str,
        timeframe: str = "1Min",
        limit: int = 200,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch OHLCV bars for a single symbol.

        Args:
            symbol: Stock ticker.
            timeframe: Bar timeframe — "1Min", "5Min", "15Min", "1Hour", "1Day".
            limit: Max bars to return (Alpaca caps at 10000).
            start: ISO timestamp for range start (optional).
            end: ISO timestamp for range end (optional).

        Returns:
            List of bar dicts: {t, o, h, l, c, v, n, vw}.

        Ref: DATA-001 (Alpaca bars endpoint)
        """
        params: dict[str, Any] = {
            "timeframe": timeframe,
            "limit": limit,
            "feed": self._config.feed,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        raw = await self._data_get(
            f"{self._data_base}/v2/stocks/{symbol}/bars",
            params=params,
        )
        bars = raw.get("bars") or []
        if not bars:
            logger.debug(
                "get_bars returned empty for %s (timeframe=%s, limit=%d, start=%s, end=%s)",
                symbol, timeframe, limit, start, end,
            )
        return bars  # Alpaca returns null when no data available

    # ── Market Data: Historical Bars for RVOL ────────────────────────

    async def get_volume_profile(
        self,
        symbol: str,
        lookback_days: int = 20,
    ) -> list[int]:
        """
        Build a time-bucketed volume profile for RVOL denominator.

        For each minute since market open (09:30 ET), computes the average
        volume across the past `lookback_days` sessions.

        Returns: List where index i = average volume at minute i since open.
                 Length = 390 (minutes in regular session) or less if limited data.

        Ref: MOMENTUM_LOGIC.md §2 (RVOL = V(S,t) / V̄_n(S,t))
        Ref: ADR-002 §2 (Historical bars for RVOL)
        Resolution: H-001 (time-bucketed RVOL)
        """
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days * 2)  # Extra days for market closures

        # Sweep fix: use _data_get (rate-limited) — same fix as get_snapshots
        raw = await self._data_get(
            f"{self._data_base}/v2/stocks/{symbol}/bars",
            params={
                "timeframe": "1Min",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": 10000,
                "feed": self._config.feed,
            },
        )

        bars = raw.get("bars", [])
        if not bars:
            return []

        # ── Group bars by minute-since-open ──
        # Market open is 09:30 ET.  In UTC this is 14:30 (EST, Nov-Mar)
        # or 13:30 (EDT, Mar-Nov).  Rather than hardcoding one offset,
        # we convert each bar's UTC timestamp to ET and compute minutes
        # from 09:30 ET directly.  This handles DST transitions correctly.
        try:
            import zoneinfo
            _ET = zoneinfo.ZoneInfo("America/New_York")
        except Exception:
            # Fallback for environments without zoneinfo
            _ET = None

        minute_buckets: dict[int, list[int]] = {}
        for bar in bars:
            ts = bar["t"]
            # Parse timestamp — Alpaca returns ISO format
            if isinstance(ts, str):
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            else:
                dt = ts

            # Convert to ET for correct minute-since-open calculation
            if _ET is not None:
                dt_et = dt.astimezone(_ET)
                minutes_since_open = (dt_et.hour - 9) * 60 + (dt_et.minute - 30)
            else:
                # Fallback: approximate DST detection when zoneinfo unavailable.
                # D121 BUG-M10: Was hardcoded to EST (dt.hour - 14), off by 1 hour
                # during EDT (Mar-Nov). Market open is 14:30 UTC in EST, 13:30 UTC in EDT.
                _market_open_hour_utc = 13 if 3 <= dt.month <= 11 else 14
                minutes_since_open = (
                    (dt.hour - _market_open_hour_utc) * 60
                    + (dt.minute - 30)
                )

            # Only include regular session bars (0 to 389 minutes)
            if 0 <= minutes_since_open < 390:
                bucket = minutes_since_open
                if bucket not in minute_buckets:
                    minute_buckets[bucket] = []
                minute_buckets[bucket].append(bar["v"])

        # ── Compute average per bucket ──
        if not minute_buckets:
            return []

        max_bucket = max(minute_buckets.keys())
        profile = []
        for i in range(max_bucket + 1):
            volumes = minute_buckets.get(i, [])
            if volumes:
                profile.append(int(sum(volumes) / len(volumes)))
            else:
                profile.append(0)

        return profile

    async def compute_rvol_from_profile(
        self,
        current_cumulative_volume: int,
        profile: list[int],
        minutes_since_open: int,
    ) -> float:
        """
        Compute RVOL using a pre-built volume profile.

        RVOL = cumulative_volume_today / Σ(avg_volume[0:minute])
        Ref: MOMENTUM_LOGIC.md §2
        """
        if minutes_since_open <= 0 or not profile:
            return 0.0

        # Sum average volumes from open to current minute
        end_idx = min(minutes_since_open, len(profile))
        expected_cumulative = sum(profile[:end_idx])

        if expected_cumulative <= 0:
            return 0.0

        return current_cumulative_volume / expected_cumulative

    # ── Order Execution ──────────────────────────────────────────────

    async def submit_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_type: str = "market",
        limit_price: float | None = None,
        time_in_force: str = "day",
    ) -> dict[str, Any]:
        """
        Submit a single order to Alpaca.

        Ref: DATA-001 (Alpaca Trading API)
        Ref: INV-007 (defaults to paper trading)
        """
        payload: dict[str, Any] = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
        }
        if limit_price is not None:
            payload["limit_price"] = str(limit_price)

        return await self._post(f"{self._trade_base}/v2/orders", payload)

    async def submit_bracket_order(
        self,
        symbol: str,
        qty: int,
        limit_price: float,
        stop_loss: float,
        take_profit: float,
        time_in_force: str = "day",
    ) -> dict[str, Any]:
        """
        Submit a bracket (OCO) order: entry + stop-loss + take-profit.

        Ref: DATA-001 (Alpaca bracket orders)
        Ref: MOMENTUM_LOGIC.md §6 (position management)
        """
        # D81: Round all prices per SEC Rule 612
        if limit_price >= 1.0:
            limit_price = round(limit_price, 2)
        else:
            limit_price = round(limit_price, 4)
        # D294 (2026-05-13): clamp stop to satisfy Alpaca's base-floor.
        stop_loss = _clamp_stop_below_base(stop_loss, limit_price, side="long")
        if take_profit >= 1.0:
            take_profit = round(take_profit, 2)
        else:
            take_profit = round(take_profit, 4)

        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": "buy",
            "type": "limit",
            "limit_price": str(limit_price),
            "time_in_force": time_in_force,
            "order_class": "bracket",
            "stop_loss": {"stop_price": str(stop_loss)},
            "take_profit": {"limit_price": str(take_profit)},
        }
        return await self._post(f"{self._trade_base}/v2/orders", payload)

    async def get_market_clock(self) -> dict[str, Any]:
        """Fetch market clock (is_open, next_open, next_close).

        Returns dict with 'is_open' (bool), 'timestamp', 'next_open', 'next_close'.
        Used by D79 holiday detection to skip Phase 2/3 when market is closed.
        """
        return await self._trading_get(f"{self._trade_base}/v2/clock")

    async def get_account(self) -> dict[str, Any]:
        """Fetch account info (balance, buying power, PDT status, etc.)."""
        return await self._trading_get(f"{self._trade_base}/v2/account")

    async def get_positions(self) -> list[dict[str, Any]]:
        """Fetch all open positions."""
        return await self._trading_get(f"{self._trade_base}/v2/positions")

    async def get_orders(
        self,
        status: str = "open",
        limit: int = 100,
        symbols: str | None = None,
        after: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch orders from Alpaca Trading API.

        Args:
            status: 'open', 'closed', or 'all'.
            limit: Max orders to return (default 100).
            symbols: Comma-separated ticker filter (e.g. 'AAPL,TSLA').
            after: ISO 8601 timestamp; only orders submitted after this
                   instant. Crucial for D238 reconciliation: prevents
                   pulling historical orders from prior trading days when
                   the journal only contains today's activity.
            until: ISO 8601 timestamp; only orders submitted before this
                   instant.

        Returns:
            List of order dicts with id, symbol, qty, side, type, status,
            limit_price, stop_price, filled_qty, filled_avg_price, etc.

        Ref: DATA-001 (Alpaca GET /v2/orders)
        D64: Required for restart recovery — fetch active stops and tranche exits.
        D238 (2026-05-12 fix): added `after` parameter to filter out
        historical orders. Without this, EOD reconciliation pulled all 500
        most-recent orders (potentially weeks of history) and compared to
        today-only journal, generating false-positive deltas like the
        $41,256.11 disagreement on 2026-05-11.
        """
        params: dict[str, Any] = {
            "status": status,
            "limit": limit,
            "direction": "desc",
        }
        if symbols:
            params["symbols"] = symbols
        if after:
            params["after"] = after
        if until:
            params["until"] = until
        return await self._trading_get(
            f"{self._trade_base}/v2/orders", params=params
        )

    async def check_pdt_status(self) -> dict[str, Any]:
        """
        Check PDT status and buying power.
        Returns dict with pdt_flagged, buying_power, daytrading_buying_power.

        Ref: DATA-001-EXT CONSTRAINT-008
        Note: daytrading_buying_power may show $0 on new accounts (known bug).
        """
        account = await self.get_account()
        return {
            "pdt_flagged": account.get("pattern_day_trader", False),
            "buying_power": float(account.get("buying_power", 0)),
            "daytrading_buying_power": float(
                account.get("daytrading_buying_power", 0)
            ),
            "equity": float(account.get("equity", 0)),
            "cash": float(account.get("cash", 0)),
        }

    async def check_asset_tradable(self, symbol: str) -> dict[str, Any]:
        """
        Check if an asset is active, tradable, and fractionable.

        Ref: DATA-001-EXT §7.2 (Asset Universe Screening)
        Must verify before order submission:
        - status == "active"
        - tradable == true
        - fractionable (if fractional order)
        - easy_to_borrow (if short selling)
        """
        asset = await self._trading_get(
            f"{self._trade_base}/v2/assets/{symbol}"
        )
        return {
            "symbol": asset.get("symbol", symbol),
            "active": asset.get("status") == "active",
            "tradable": asset.get("tradable", False),
            "fractionable": asset.get("fractionable", False),
            "easy_to_borrow": asset.get("easy_to_borrow", False),
            "shortable": asset.get("shortable", False),
        }

    async def submit_limit_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        limit_price: float,
        time_in_force: str = "day",
    ) -> dict[str, Any]:
        """
        Submit a limit order (for tranche exits).

        Args:
            symbol: Stock symbol.
            qty: Number of shares.
            side: 'buy' or 'sell'.
            limit_price: Limit price.
            time_in_force: 'day', 'gtc', 'ioc', etc.

        Returns:
            Alpaca order response dict with 'id', 'status', etc.

        Ref: ADR-003 §2 (Scaled Exits via limit orders)
        """
        # D81: Round limit price to comply with SEC Rule 612 (sub-penny rule).
        # Stocks >= $1.00 must use $0.01 increments; < $1.00 can use $0.0001.
        if limit_price >= 1.0:
            limit_price = round(limit_price, 2)
        else:
            limit_price = round(limit_price, 4)

        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "limit",
            "limit_price": str(limit_price),
            "time_in_force": time_in_force,
        }
        return await self._trading_post(
            f"{self._trade_base}/v2/orders", payload
        )

    async def cancel_all_orders(self) -> list[dict[str, Any]]:
        """Cancel ALL open orders. Used by D80 before EOD/Phase 4 position close.

        Prevents runaway order loops from orphaned tranche/stop orders
        that can fill after positions are closed.

        Returns list of cancelled order statuses.
        """
        try:
            await self._trading_limiter.acquire()
            # D90: Use persistent HTTP client (was creating new per-request)
            client = self._get_http_client()
            resp = await client.delete(
                f"{self._trade_base}/v2/orders",
            )
            if resp.status_code in (200, 207):
                return resp.json()
            resp.raise_for_status()
            return []
        except Exception as e:
            logger.warning("Failed to cancel all orders: %s", e)
            return []

    async def cancel_order(self, order_id: str) -> dict[str, Any] | None:
        """
        Cancel an open order by ID.

        Returns:
            Empty dict on success (204), None on failure.

        Ref: ADR-003 §2 (Stop ratcheting requires cancel + resubmit)
        """
        try:
            await self._trading_limiter.acquire()
            # D90: Use persistent HTTP client (was creating new per-request)
            client = self._get_http_client()
            resp = await client.delete(
                f"{self._trade_base}/v2/orders/{order_id}",
            )
            if resp.status_code in (200, 204):
                return {}
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("Failed to cancel order %s: %s", order_id, e)
            return None

    async def submit_stop_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        stop_price: float,
        time_in_force: str = "gtc",
        position_intent: str | None = None,
    ) -> dict[str, Any]:
        """
        Submit a stop order (for ratcheted stop-loss after tranche fill,
        or for protective stop after OTO entry fills).

        Tue 2026-04-21 fix: default `time_in_force` is now `"gtc"`, not
        `"day"`. Every existing caller (main.py × 8 sites, stop_resubmitter
        × 2 sites, all of which were relying on the default) is a PROTECTIVE
        stop on an OPEN position. DAY-TIF stops expired at 16:00 ET and
        left positions naked overnight — caught Tue when ELSE's
        D100-converted stop expired at close while the session was dead
        and the position rode out the session unprotected. Microcap
        pump-day-2 gap-down risk is real and the strategy already sized
        for the stop to be active continuously.

        Callers that want DAY-TIF stop semantics (rare; arguably should
        not exist for protective stops) must pass `time_in_force="day"`
        explicitly. The bug-sweep test
        `tests/unit/test_stop_order_tif_gtc.py` enforces that no caller
        in the production tree does so.

        Args:
            symbol: Stock symbol.
            qty: Number of shares.
            side: 'buy' or 'sell'.
            stop_price: Trigger price for the stop order.
            time_in_force: 'gtc' (default — protective stops persist
                across sessions) or 'day' (rare; for explicit one-day
                semantics).
            position_intent: D124 — 'close' to explicitly mark as
                sell-to-close (not short-sell). Prevents 422 errors
                on stocks with short-sale restrictions.

        Returns:
            Alpaca order response dict with 'id', 'status', etc.

        Ref: ADR-003 §2 (Stop ratcheting via cancel + resubmit)
        Ref: docs/research-log/16 (silent-fallback audit) — DAY-TIF stops were
             the precondition for today's "ELSE naked overnight" outcome.
        """
        # D81: Round stop price per SEC Rule 612
        if stop_price >= 1.0:
            stop_price = round(stop_price, 2)
        else:
            stop_price = round(stop_price, 4)

        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "stop",
            "stop_price": str(stop_price),
            "time_in_force": time_in_force,
        }
        # D124: position_intent="close" was intended to prevent short-sell
        # rejection on restricted symbols. But Alpaca Paper API rejects it
        # with 422 "invalid position_intent specified" (D126 Mar 26: broke
        # ALL stop conversions on JBLU/SRPT/RMSG — positions ran unprotected).
        # DISABLED until Alpaca documents the correct field name.
        # if position_intent:
        #     payload["position_intent"] = position_intent
        return await self._trading_post(
            f"{self._trade_base}/v2/orders", payload
        )

    async def submit_oto_order(
        self,
        symbol: str,
        qty: int,
        limit_price: float,
        stop_loss: float,
        time_in_force: str = "day",
    ) -> dict[str, Any]:
        """
        Submit a One-Triggers-Other (OTO) order: limit buy + stop-loss sell.

        OTO ensures the stop-loss leg only activates AFTER the buy leg fills.
        This prevents Alpaca's "potential wash trade detected" 403 error that
        occurs when a sell stop is submitted while a buy limit is still pending.

        Unlike bracket orders, OTO does NOT create an OCO group on the sell side,
        so independent tranche sell (limit) orders can be submitted separately
        without 422/403 conflicts.

        Args:
            symbol: Stock symbol.
            qty: Number of shares.
            limit_price: Limit price for the buy entry.
            stop_loss: Stop price for the protective sell stop.
            time_in_force: 'day', 'gtc', etc.

        Returns:
            Alpaca order response dict with 'id', 'status', 'legs', etc.

        Ref: ADR-003 §1 (Stateless Order Submitter)
        Ref: Alpaca API — Order Class: OTO (One-Triggers-Other)
        Fix: D57 wash trade bug — stop must only activate after buy fills.

        D277 HALT_NEW_ENTRIES (2026-04-28): operator-controlled kill
        switch. When MOMENTUM_HALT_NEW_ENTRIES=1 (or
        ExecutionConfig.halt_new_entries=True), this function refuses
        ALL new OTO submissions. EVERY entry path (FAST_PATH, Phase 2,
        Phase 3 RESCAN, VWAP) goes through here, so this is the single
        chokepoint. Existing positions, exits, and stop ratchets are
        unaffected — only NEW entries are blocked. Rationale per
        evaluation/2026-04-28-edge-assessment.md §1.4 (honest edge
        statement) and evaluation/2026-04-28-catalyst-stratification.md.
        """
        _halt_resp = _d277_halt_response(
            symbol=symbol, qty=qty,
            limit_price=limit_price, stop_loss=stop_loss,
            side="buy",
        )
        if _halt_resp is not None:
            return _halt_resp
        # doc 282: loser-re-entry ban (env-gated, default OFF; long entries only —
        # the single chokepoint above guarantees every entry path passes through here).
        _ban_resp = _check_reentry_ban(symbol)
        if _ban_resp is not None:
            return _ban_resp
        # D81: Round all prices per SEC Rule 612
        if limit_price >= 1.0:
            limit_price = round(limit_price, 2)
        else:
            limit_price = round(limit_price, 4)
        # D294 (2026-05-13): clamp stop_loss to satisfy Alpaca's strict
        # `stop_price <= base_price - 0.01` rule. On 2026-05-13 every QUCY
        # OTO submission was rejected with HTTP 422 code=42210000 because
        # our calculated stop landed within $0.01 of base_price (sub-$1
        # stocks at ~$0.55-0.58, where percent-based stop math collapses
        # under Alpaca's absolute floor). The fix: enforce the floor
        # explicitly here, AFTER any caller-side rounding, so a too-tight
        # stop is silently widened by 1 tick instead of producing a
        # 4xx + retry storm + BRIDGE_CANCEL ghost-fill churn.
        stop_loss = _clamp_stop_below_base(stop_loss, limit_price, side="long")

        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": "buy",
            "type": "limit",
            "limit_price": str(limit_price),
            "time_in_force": time_in_force,
            "order_class": "oto",
            "stop_loss": {"stop_price": str(stop_loss)},
        }
        return await self._trading_post(
            f"{self._trade_base}/v2/orders", payload
        )

    async def submit_oto_short_order(
        self,
        symbol: str,
        qty: int,
        limit_price: float,
        stop_loss: float,
        time_in_force: str = "day",
    ) -> dict[str, Any]:
        """
        Submit a One-Triggers-Other (OTO) short order: sell limit + buy stop.

        OTO ensures the buy-stop (cover) leg only activates AFTER the short entry fills.
        This prevents Alpaca's "potential wash trade detected" error and ensures
        stop protection only activates once the short position is established.

        Short position structure:
          - Primary leg:   sell limit at entry_price (initiates short)
          - Secondary leg: buy stop at stop_price ABOVE entry (covers on adverse move)

        Args:
            symbol: Stock symbol.
            qty: Number of shares to short.
            limit_price: Limit price for the sell-short entry.
            stop_loss: Buy-stop price ABOVE entry for short protection.
            time_in_force: 'day', 'gtc', etc.

        Returns:
            Alpaca order response dict with 'id', 'status', 'legs', etc.

        Ref: D161 (Short Selling design)
        Ref: ADR-003 §1 — mirrors submit_oto_order but inverted side

        D277 HALT_NEW_ENTRIES (2026-04-28): same operator kill switch
        as submit_oto_order. Short entries also halt when the flag is on.
        See submit_oto_order docstring for the full rationale.
        """
        _halt_resp = _d277_halt_response(
            symbol=symbol, qty=qty,
            limit_price=limit_price, stop_loss=stop_loss,
            side="sell",
        )
        if _halt_resp is not None:
            return _halt_resp
        # D81: Round all prices per SEC Rule 612
        if limit_price >= 1.0:
            limit_price = round(limit_price, 2)
        else:
            limit_price = round(limit_price, 4)
        # D294 (2026-05-13): clamp short stop to satisfy Alpaca's
        # `stop_price >= base_price + 0.01` rule (mirror of long-side fix).
        stop_loss = _clamp_stop_below_base(stop_loss, limit_price, side="short")

        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": "sell",
            "type": "limit",
            "limit_price": str(limit_price),
            "time_in_force": time_in_force,
            "order_class": "oto",
            "stop_loss": {"stop_price": str(stop_loss)},
        }
        return await self._trading_post(
            f"{self._trade_base}/v2/orders", payload
        )

    async def submit_market_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        time_in_force: str = "day",
    ) -> dict[str, Any]:
        """
        Submit a market order (for EOD close — guaranteed fill at market price).

        D74: Phase 4 must submit actual sell orders to close positions.
        Market orders guarantee fill but accept slippage.

        Ref: ADR-003 §2 (Time-stop exit at market close)
        """
        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": time_in_force,
        }
        return await self._trading_post(
            f"{self._trade_base}/v2/orders", payload
        )

    async def close_position(
        self, symbol: str, qty: int | None = None,
    ) -> dict[str, Any]:
        """
        Close a position via Alpaca DELETE /v2/positions/{symbol}.

        D74: Most reliable way to close a position — Alpaca handles the
        market order internally. Works even if we don't know exact qty.

        D90: Uses persistent HTTP client for connection pooling (was creating
        a new httpx.AsyncClient per call, wasting ~2ms and connections).

        doc 269 (2026-06-09): optional ``qty`` liquidates only that many
        shares (Alpaca ``?qty=`` query param) so the D164/D165 partial-exit
        paths share the same broker-confirmed close pipeline as full closes.
        ``qty=None`` (default) closes the entire position — unchanged
        behavior for every pre-existing caller.

        Returns:
            Order response dict from Alpaca.

        Raises:
            httpx.HTTPStatusError on failure (404 if no position exists).
        """
        await self._trading_limiter.acquire()
        client = self._get_http_client()
        _params = {"qty": str(int(qty))} if qty else None
        resp = await client.delete(
            f"{self._trade_base}/v2/positions/{symbol}",
            params=_params,
        )
        if resp.status_code in (200, 204):
            try:
                return resp.json()
            except Exception:
                return {"status": "closed", "symbol": symbol}
        # D73: Log details before raising
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:500]
        logger.error(
            "D74 close_position %s → %d | body=%s",
            symbol, resp.status_code, body,
        )
        resp.raise_for_status()
        return {}

    async def validate_api_key(self) -> bool:
        """
        D73: Pre-flight API key validation at startup.
        Returns True if API key is valid and account is active.
        """
        try:
            account = await self.get_account()
            status = account.get("status", "unknown")
            # D150: Accept both ACTIVE (live) and PAPER_TRADING (paper) as valid
            if status not in ("ACTIVE", "PAPER_TRADING"):
                logger.error(
                    "D73: Account status is %s (expected ACTIVE or PAPER_TRADING). "
                    "Orders will fail.", status,
                )
                return False
            logger.info(
                "D73: API key validated — account %s, equity=$%.2f, status=%s",
                account.get("account_number", "?"),
                float(account.get("equity", 0)),
                status,
            )
            return True
        except Exception as e:
            logger.error("D73: API key validation FAILED: %s", e)
            return False

    async def submit_extended_hours_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        limit_price: float,
    ) -> dict[str, Any]:
        """
        Submit an extended hours order. Per CONSTRAINT-007:
        - type MUST be 'limit'
        - time_in_force MUST be 'day'
        - extended_hours MUST be true

        Ref: DATA-001-EXT CONSTRAINT-007
        """
        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "limit",
            "limit_price": str(limit_price),
            "time_in_force": "day",
            "extended_hours": True,
        }
        return await self._trading_post(
            f"{self._trade_base}/v2/orders", payload
        )

    # ── HTTP Helpers (Rate-Limited) ──────────────────────────────────

    def _get_http_client(self) -> httpx.AsyncClient:
        """D85: Lazy-init persistent HTTP client with connection pooling."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=30,
                headers=self._headers,
                limits=httpx.Limits(
                    max_keepalive_connections=10,
                    max_connections=20,
                ),
            )
        return self._http_client

    async def close(self) -> None:
        """Close persistent HTTP client. Call on shutdown."""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def _get(
        self, url: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Async GET with auth headers, connection pooling, and D87 circuit breaker."""
        from src.utils.circuit_breaker import alpaca_breaker

        async with alpaca_breaker:
            client = self._get_http_client()
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()

    async def _post(self, url: str, payload: dict[str, Any]) -> Any:
        """Async POST with auth headers + D73 retry/diagnostics + D87 circuit breaker."""
        import asyncio as _aio
        from src.utils.circuit_breaker import alpaca_breaker

        # D87: Check circuit breaker before attempting any retries
        alpaca_breaker.check()

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            client = self._get_http_client()
            resp = await client.post(url, json=payload)

            if resp.status_code < 400:
                alpaca_breaker.record_success()
                return resp.json()

            # D73: Capture full error details before raising
            try:
                body = resp.json()
            except Exception:
                body = resp.text[:500]

            logger.warning(
                "D73 POST %s → %d (attempt %d/%d) | body=%s | symbol=%s",
                url.split("/")[-1], resp.status_code, attempt, max_retries,
                body, payload.get("symbol", "?"),
            )

            # Retry on 429 (rate limit) or 5xx (server error)
            if resp.status_code in (429, 500, 502, 503) and attempt < max_retries:
                wait = 2 ** attempt  # 2s, 4s
                logger.info("D73: Retrying in %ds...", wait)
                await _aio.sleep(wait)
                continue

            # 403: Log diagnostic details and raise
            if resp.status_code == 403:
                logger.error(
                    "D73 FORBIDDEN: url=%s | payload=%s | response=%s | "
                    "Check: API key valid? Account active? Paper trading enabled?",
                    url, payload, body,
                )

            # 422: Log details (often means conflicting order state)
            if resp.status_code == 422:
                logger.error(
                    "D73 UNPROCESSABLE: url=%s | symbol=%s | response=%s",
                    url, payload.get("symbol", "?"), body,
                )

            # D279: forward HTTP context so trip log shows last_failure.
            # 2026-05-28 (doc 177): body is a dict on JSON error responses
            # (resp.json() at the top of this block), and dict[:120] raises
            # inside this error handler — masking the real 403 with a bogus
            # "slice(None, 120, None)" error that (a) leaves the actual
            # HTTPStatusError unraised, (b) defeats the Phase-A
            # cancel-blocking-stops detection downstream (it matches on
            # "40310000"/"insufficient qty", never on a slice repr), and
            # (c) aborted UMAC's residual-stop submission ("POSITION
            # UNPROTECTED") + APPS profit tranches on 2026-05-28. str() first.
            alpaca_breaker.record_failure(
                f"HTTP {resp.status_code} url={url} body={str(body)[:120]}"
            )
            resp.raise_for_status()

        # Should not reach here, but safety net
        alpaca_breaker.record_failure("unreachable code path in _trading_post")
        resp.raise_for_status()
        return resp.json()

    async def _trading_get(
        self, url: str, params: dict[str, Any] | None = None
    ) -> Any:
        """
        Rate-limited GET for Trading API.
        Acquires token from trading bucket before request.
        Ref: ADR-004 §2, CONSTRAINT-004
        """
        await self._trading_limiter.acquire()
        return await self._get(url, params)

    async def _trading_post(
        self, url: str, payload: dict[str, Any]
    ) -> Any:
        """
        Rate-limited POST for Trading API.
        Ref: ADR-004 §2, CONSTRAINT-004
        """
        await self._trading_limiter.acquire()
        return await self._post(url, payload)

    async def _data_get(
        self, url: str, params: dict[str, Any] | None = None
    ) -> Any:
        """
        Rate-limited GET for Market Data API.
        Ref: ADR-004 §2, CONSTRAINT-004
        """
        await self._data_limiter.acquire()
        return await self._get(url, params)
