"""
D218: Discord webhook integration — dual-channel alerts + watchlist.

Two channels, two purposes:
- ALERTS channel: Critical events only. ~5 messages/day max.
- WATCHLIST channel: Market intelligence for humans. Rich formatting,
  emoji visual cues, clear timestamps, grandpa-friendly readability.

Must NEVER crash the caller — all exceptions are caught and logged.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Rate limiting
_last_sent: dict[str, float] = {}

_ET = ZoneInfo("America/New_York")


def _should_send(msg_type: str, min_interval: float = 30.0) -> bool:
    """Rate limit: return True if enough time has passed since last send."""
    now = time.monotonic()
    last = _last_sent.get(msg_type, 0)
    if now - last < min_interval:
        return False
    _last_sent[msg_type] = now
    return True


def _now_et() -> datetime:
    return datetime.now(timezone.utc).astimezone(_ET)


def _date_header() -> str:
    """Human-friendly date + day of week, e.g. 'Monday, April 14, 2026'"""
    return _now_et().strftime("%A, %B %d, %Y")


def _time_str() -> str:
    """e.g. '9:31 AM ET'"""
    now = _now_et()
    hour = now.hour % 12 or 12  # Convert 0->12, 13->1, etc.
    ampm = "AM" if now.hour < 12 else "PM"
    return f"{hour}:{now.minute:02d} {ampm} ET"


def _momentum_bar(value: float, max_val: float = 1.0, length: int = 10) -> str:
    """Visual bar using block chars. e.g. value=0.7 -> '███████░░░'"""
    filled = int(round((value / max_val) * length)) if max_val > 0 else 0
    filled = max(0, min(length, filled))
    return "\u2588" * filled + "\u2591" * (length - filled)


def _gap_emoji(gap_pct: float) -> str:
    """Emoji indicator for gap size."""
    g = abs(gap_pct) * 100
    if g >= 40:
        return "\U0001f525\U0001f525\U0001f525"  # 🔥🔥🔥
    elif g >= 20:
        return "\U0001f525\U0001f525"  # 🔥🔥
    elif g >= 10:
        return "\U0001f525"  # 🔥
    else:
        return "\u2b06\ufe0f"  # ⬆️


def _rvol_emoji(rvol: float) -> str:
    """Emoji indicator for relative volume."""
    if rvol >= 100:
        return "\U0001f4a5"  # 💥 explosive
    elif rvol >= 10:
        return "\U0001f30a"  # 🌊 wave
    elif rvol >= 3:
        return "\U0001f4c8"  # 📈 chart up
    else:
        return "\U0001f4ca"  # 📊 chart


def _pnl_emoji(pnl: float) -> str:
    if pnl > 100:
        return "\U0001f4b0\U0001f389"  # 💰🎉
    elif pnl > 0:
        return "\u2705"  # ✅
    elif pnl == 0:
        return "\u2796"  # ➖
    elif pnl > -100:
        return "\u274c"  # ❌
    else:
        return "\u274c\U0001f4c9"  # ❌📉


# -- 2026-05-13 (doc 165) shared % change formatting helpers --------
# Every alert that displays a stock should include its % change so the
# operator sees price action without looking it up. These helpers
# standardize the presentation across every embed.

def _pct_arrow(pct: float) -> str:
    """Direction arrow with magnitude-graded coloring.

    Thresholds (signed pct, e.g. 0.08 = +8%):
      >= +20% rocket   | >= +5% green up | > 0    green
      = 0     pause    | > -5%  red      | <= -5% red down
      <= -20% explosion
    """
    if pct >= 0.20:
        return "\U0001f680"            # rocket
    if pct >= 0.05:
        return "\U0001f7e2\u2b06\ufe0f"  # green circle + up arrow
    if pct > 0:
        return "\U0001f7e2"            # green circle
    if pct == 0:
        return "\u23f8\ufe0f"            # pause
    if pct > -0.05:
        return "\U0001f534"            # red circle
    if pct > -0.20:
        return "\U0001f534\u2b07\ufe0f"  # red circle + down arrow
    return "\U0001f4a5"                # explosion


def _pct_badge(pct: float, *, signed: bool = True) -> str:
    """Inline % string with sign: '+8.8%' / '-1.2%' / '0.0%'.

    Pass ``signed=False`` for headline magnitudes (e.g. "gap of 38.2%").
    Three-digit moves (>=1000%) are rounded to whole percent for layout.
    """
    sign = "+" if (signed and pct > 0) else ""
    if abs(pct) < 10:
        return f"{sign}{pct * 100:.1f}%"
    return f"{sign}{pct * 100:.0f}%"


def _price_with_change(price: float, pct: float | None = None,
                       *, decimals: int | None = None) -> str:
    """Standard price display with optional change badge.

    Returns "$21.14" if pct is None, else "$21.14 (<arrow> +8.8%)".
    Sub-$1 prices auto-default to 4 decimals (penny-stock precision).
    """
    if decimals is None:
        decimals = 2 if price >= 1.0 else 4
    if pct is None:
        return f"${price:.{decimals}f}"
    return f"${price:.{decimals}f} ({_pct_arrow(pct)} {_pct_badge(pct)})"


def _separator(width: int = 30) -> str:
    """Em-dash divider used in embed descriptions. DRY-up of the
    copy-pasted box-drawing string repeated in 6+ places."""
    return "\u2500" * width


def _color_for_pct(pct: float) -> int:
    """Discord embed color graded by signed pct change."""
    if pct >= 0.05:
        return 0x2ECC71
    if pct > 0:
        return 0x58D68D
    if pct == 0:
        return 0x95A5A6
    if pct > -0.05:
        return 0xEC7063
    return 0xE74C3C


# D313.v3 (2026-05-24, doc 174): @here debounce. If HEDGE_VIOLATION
# fires every 60s during a 3-minute Alpaca degradation, we'd spam @here
# 3x. Track last CRITICAL mention time; inject @here only if no
# CRITICAL was mentioned in last MENTION_COOLDOWN_SEC. Alert STILL
# fires (operator sees it) -- just without the audible ping.
_last_critical_mention_utc: dict[str, float] = {}  # keyed by webhook
MENTION_COOLDOWN_SEC = 300.0  # 5 minutes


def _should_mention_at_here(webhook_url: str) -> bool:
    """Returns True if @here mention should be injected for this webhook
    (i.e., no CRITICAL has been mentioned for it in the last 5 min)."""
    import time as _t
    now = _t.monotonic()
    last = _last_critical_mention_utc.get(webhook_url)
    if last is None or (now - last) >= MENTION_COOLDOWN_SEC:
        _last_critical_mention_utc[webhook_url] = now
        return True
    return False


async def _post(webhook_url: str, payload: dict,
                  severity: str = "INFO") -> None:
    """Send to Discord. Never raises.

    D314 (2026-05-24, doc 173): every payload also written to durable
    spool BEFORE the live POST so Discord outage cannot lose alerts --
    retry loop redelivers. CRITICAL severity adds @here mention.
    D313.v3 (2026-05-24, doc 174): @here debounce -- only ping when no
    CRITICAL was mentioned in last 5 min (prevents degradation spam).
    """
    if not webhook_url:
        return
    if severity.upper() == "CRITICAL" and _should_mention_at_here(webhook_url):
        try:
            payload = dict(payload)
            existing = payload.get("content", "")
            payload["content"] = (f"@here {existing}".strip()
                                   if existing else "@here")
        except Exception:
            pass
    try:
        from src.monitoring.durable_alert_spool import durable_post
        await durable_post(webhook_url, payload, severity=severity)
    except Exception as e:
        logger.debug("D218: durable_post raised: %s", e)
        try:  # Last-resort raw POST
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(webhook_url, json=payload)
        except Exception:
            pass


def _post_sync(webhook_url: str, payload: dict,
                severity: str = "INFO") -> None:
    """Sync version. Never raises.

    D314: writes to spool synchronously first (so process crash doesn't
    lose the alert), then attempts live POST. CRITICAL adds @here
    (subject to D313.v3 debounce -- same logic as async path).
    """
    if not webhook_url:
        return
    if severity.upper() == "CRITICAL" and _should_mention_at_here(webhook_url):
        try:
            payload = dict(payload)
            existing = payload.get("content", "")
            payload["content"] = (f"@here {existing}".strip()
                                   if existing else "@here")
        except Exception:
            pass
    spool_path = None
    try:
        from src.monitoring.durable_alert_spool import (
            _today_dir, _spool_filename, _serialize, DEFAULT_SPOOL_DIR,
        )
        import json as _j
        spool_path = _today_dir(DEFAULT_SPOOL_DIR) / _spool_filename(severity)
        record = _serialize(webhook_url, payload, severity)
        with open(spool_path, "w", encoding="utf-8") as f:
            _j.dump(record, f, separators=(",", ":"))
    except Exception:
        spool_path = None
    try:
        import httpx
        resp = httpx.post(webhook_url, json=payload, timeout=5)
        if 200 <= resp.status_code < 300 and spool_path is not None:
            try:
                spool_path.unlink()
            except Exception:
                pass
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
#  ALERT CHANNEL — critical events only
# ═══════════════════════════════════════════════════════════════


async def alert_critical(message: str, webhook_url: str | None = None) -> None:
    if not webhook_url:
        return
    embed = {
        "title": "\u26a0\ufe0f Alert",
        "description": message,
        "color": 0xE74C3C,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"Momentum-X | {_date_header()}"},
    }
    # D314 (2026-05-24, doc 173): alert_critical = CRITICAL severity.
    # Adds @here mention + 50-cycle (~50 min) durable-spool retry budget.
    await _post(webhook_url, {"embeds": [embed]}, severity="CRITICAL")


def alert_critical_sync(message: str, webhook_url: str | None = None) -> None:
    if not webhook_url:
        return
    embed = {
        "title": "\u26a0\ufe0f Alert",
        "description": message,
        "color": 0xE74C3C,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"Momentum-X | {_date_header()}"},
    }
    _post_sync(webhook_url, {"embeds": [embed]}, severity="CRITICAL")


def alert_morning_resolution_sync(
    *,
    overnight_positions: list[dict],
    webhook_url: str | None = None,
) -> None:
    """Bug AJ (Tier 1 #2): startup notification for overnight positions
    that the system will auto-close at market open. Lets the operator
    intervene BEFORE the close fires (e.g., set MOMENTUM_HOLD_OVERNIGHT
    in env, or manually close at a different price).

    `overnight_positions` is a list of dicts with at minimum:
      {ticker, qty, entry, current, pnl_dollar, pnl_pct, days_held}

    Posts a single multi-position embed to the OPS alert channel. Sync
    because it's called from the synchronous startup path before the
    asyncio loop is running.
    """
    if not webhook_url or not overnight_positions:
        return

    # Total P&L summary
    total_pnl = sum(p.get("pnl_dollar", 0.0) for p in overnight_positions)
    total_pnl_emoji = _pnl_emoji(total_pnl)

    # Per-position lines
    lines: list[str] = []
    for p in overnight_positions:
        ticker = p.get("ticker", "?")
        qty = int(p.get("qty", 0))
        entry = float(p.get("entry", 0))
        cur = float(p.get("current", 0))
        pnl_d = float(p.get("pnl_dollar", 0))
        pnl_p = float(p.get("pnl_pct", 0))
        days = int(p.get("days_held", 1))
        emoji = _pnl_emoji(pnl_d)
        lines.append(
            f"  {emoji} **{ticker}** qty={qty} entry=${entry:.2f} "
            f"cur=${cur:.2f} P&L=${pnl_d:+,.0f} ({pnl_p:+.1f}%) "
            f"held={days}d"
        )

    description = (
        f"**MORNING RESOLUTION REQUIRED** — {len(overnight_positions)} "
        f"overnight position(s) carried into today.\n\n"
        + "\n".join(lines)
        + f"\n\n**Total unrealized P&L:** {total_pnl_emoji} ${total_pnl:+,.2f}\n\n"
        "**Default action:** auto-close at market open via "
        "`_close_overnight_position`. To override, set "
        "`MOMENTUM_HOLD_OVERNIGHT=TICKER1,TICKER2` in env before next "
        "session start (Bug AJ env override).\n\n"
        "**Investigate:** check `logs/momentum_<date>.log` for D91 lines + "
        "review recent journal entries before market open."
    )

    embed = {
        "title": "\u26a0\ufe0f Morning Resolution Required (D91 Overnight Positions)",
        "description": description,
        "color": 0xF39C12,  # warning amber, less urgent than critical red
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"Momentum-X | {_date_header()} | Bug AJ alert"},
    }
    # Defensive: never crash the startup path. _post_sync already
    # swallows exceptions internally, but in case some other code in
    # this function path raises (e.g., Discord library import failure
    # at startup, mock override in tests), this outer try/except is the
    # final safety net. Worst case: silent telemetry loss; not a startup
    # crash.
    try:
        _post_sync(webhook_url, {"embeds": [embed]})
    except Exception as e:  # noqa: silent-handler — startup path safety
        logger.debug(
            "alert_morning_resolution_sync: post failed (%s) — telemetry lost, "
            "startup continues", e,
        )


async def alert_session_start(
    equity: float, commit: str, webhook_url: str | None = None,
) -> None:
    if not webhook_url:
        return
    embed = {
        "title": "\U0001f7e2 Trading Session Started",
        "description": (
            f"\U0001f4c5 **{_date_header()}**\n"
            f"\u23f0 Started at **{_time_str()}**\n\n"
            f"The system is scanning for gap-up momentum stocks.\n"
            f"Trades will execute automatically at market open (9:30 AM)."
        ),
        "color": 0x2ECC71,
        "fields": [
            {"name": "\U0001f4b5 Account Equity", "value": f"**${equity:,.2f}**", "inline": True},
            {"name": "\U0001f527 Software Version", "value": f"`{commit[:8]}`", "inline": True},
            {"name": "\U0001f4cb Mode", "value": "Paper Trading", "inline": True},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"Momentum-X Automated Trading"},
    }
    await _post(webhook_url, {"embeds": [embed]})


async def alert_session_end(
    trades: int, pnl: float, positions_at_close: int,
    webhook_url: str | None = None,
) -> None:
    if not webhook_url:
        return
    pnl_emoji = _pnl_emoji(pnl)
    if trades == 0:
        summary = "No trades today. The system scanned but no stocks met the quality bar."
    elif pnl >= 0:
        summary = f"Profitable day! {trades} trade{'s' if trades != 1 else ''} executed."
    else:
        summary = f"Loss day. {trades} trade{'s' if trades != 1 else ''} executed."

    embed = {
        "title": f"{pnl_emoji} Market Day Complete",
        "description": (
            f"\U0001f4c5 **{_date_header()}**\n"
            f"\u23f0 Market closed at **4:00 PM ET**\n\n"
            f"{summary}"
        ),
        "color": 0x2ECC71 if pnl >= 0 else 0xE74C3C,
        "fields": [
            {"name": "\U0001f4b0 Day's Profit/Loss", "value": f"**${pnl:+,.2f}**", "inline": True},
            {"name": "\U0001f4ca Trades Today", "value": f"**{trades}**", "inline": True},
            {"name": "\U0001f4e6 Positions at Close", "value": f"**{positions_at_close}**", "inline": True},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"Momentum-X | End of Day Report"},
    }
    await _post(webhook_url, {"embeds": [embed]})


# ═══════════════════════════════════════════════════════════════
#  WATCHLIST CHANNEL — market intelligence, grandpa-friendly
# ═══════════════════════════════════════════════════════════════


async def post_watchlist(
    candidates: list[dict],
    phase: str,
    scan_count: int,
    webhook_url: str | None = None,
    hour_et: int = 0,
) -> None:
    """Post watchlist with rich visual formatting.

    Before 9 AM: every 30 min. After 9 AM: every 5 min.
    """
    if not webhook_url or not candidates:
        return
    interval = 300.0 if hour_et >= 9 else 1800.0
    if not _should_send(f"watchlist_{phase}", min_interval=interval):
        return

    now = _now_et()
    if hour_et < 9:
        market_status = "\U0001f319 Pre-Market (market opens at 9:30 AM)"
    elif hour_et == 9 and now.minute < 30:
        mins_to_open = 30 - now.minute
        market_status = f"\u23f3 **{mins_to_open} minutes to market open!**"
    else:
        market_status = "\U0001f7e2 Market is OPEN"

    # Build candidate list with visual indicators
    lines = []
    for i, c in enumerate(candidates[:10], 1):
        ticker = c.get("ticker", "?")
        gap = c.get("gap_pct", 0)
        rvol = c.get("rvol", 0)
        price = c.get("price", 0)
        gap_pct = gap * 100

        g_emoji = _gap_emoji(gap)
        r_emoji = _rvol_emoji(rvol)

        # Momentum strength bar (combines gap and rvol)
        strength = min(1.0, (abs(gap) * 2 + min(rvol / 100, 1)) / 3)
        bar = _momentum_bar(strength)

        # Doc 165: include intraday day-change badge alongside gap
        # so the operator sees both the overnight move (gap) and the
        # cash-session move (intraday). day_change_pct is optional;
        # callers that don't supply it just get the gap badge.
        day_pct = c.get("day_change_pct")
        price_str = _price_with_change(price, day_pct)
        gap_arrow = _pct_arrow(gap)
        lines.append(
            f"{g_emoji} **{ticker}** — {price_str}\n"
            f"\u2003Gap: **{gap_arrow} {_pct_badge(gap)}** | "
            f"Volume: **{rvol:.1f}x** normal {r_emoji}\n"
            f"\u2003Momentum: `{bar}` {strength * 100:.0f}%"
        )

    desc = "\n\n".join(lines)
    if len(candidates) > 10:
        desc += f"\n\n*+ {len(candidates) - 10} more stocks being watched*"

    embed = {
        "title": f"\U0001f50d Today's Watchlist — {len(candidates)} Stocks",
        "description": (
            f"\U0001f4c5 **{_date_header()}** | \u23f0 {_time_str()}\n"
            f"{market_status}\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
            f"{desc}"
        ),
        "color": 0x3498DB,
        "fields": [
            {
                "name": "\U0001f4d6 What does this mean?",
                "value": (
                    "These stocks **gapped up** in pre-market trading "
                    "(price jumped overnight). Higher gap % and volume = "
                    "stronger momentum. The system will evaluate each one "
                    "at market open and decide whether to buy."
                ),
                "inline": False,
            },
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"Momentum-X | Scan #{scan_count} | Auto-updates every {'30 min' if hour_et < 9 else '5 min'}"},
    }
    await _post(webhook_url, {"embeds": [embed]})


async def post_fast_path_scores(
    entries: list[dict],
    webhook_url: str | None = None,
) -> None:
    """Fast-path scores at 9:25 AM — pre-queued for instant execution."""
    if not webhook_url or not entries:
        return

    lines = []
    for e in entries[:5]:
        ticker = e.get("ticker", "?")
        mfcs = e.get("partial_mfcs", 0)
        entry = e.get("entry_price", 0)
        stop = e.get("stop_loss", 0)
        # Doc 165: include the gap % so the operator sees WHY each
        # name was fast-tracked (gap is the headline number for
        # premarket). gap_pct is signed (e.g. 0.382 for +38.2%).
        gap_pct = e.get("gap_pct", 0.0)
        risk_pct = abs(entry - stop) / entry if entry > 0 else 0
        stop_pct_signed = ((stop - entry) / entry) if entry > 0 else 0.0
        confidence_bar = _momentum_bar(mfcs)

        lines.append(
            f"\U0001f3af **{ticker}** — Target entry: **{_price_with_change(entry, gap_pct)}**\n"
            f"\u2003Confidence: `{confidence_bar}` {mfcs:.0%}\n"
            f"\u2003Safety stop: {_price_with_change(stop)} "
            f"({_pct_badge(stop_pct_signed)} from entry, risk {_pct_badge(risk_pct, signed=False)})"
        )

    desc = "\n\n".join(lines) if lines else "No stocks qualified for fast entry."

    embed = {
        "title": "\u26a1 Fast-Track Entries Queued for 9:30 AM",
        "description": (
            f"\U0001f4c5 **{_date_header()}** | \u23f0 {_time_str()}\n"
            f"\u23f3 **Market opens in ~5 minutes!**\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
            f"{desc}"
        ),
        "color": 0xF39C12,
        "fields": [
            {
                "name": "\U0001f4d6 What is this?",
                "value": (
                    "These stocks scored highest in pre-market analysis. "
                    "The system will try to buy them **instantly** when the "
                    "market opens at 9:30 AM — speed matters for gap-up stocks!"
                ),
                "inline": False,
            },
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"Momentum-X | {len(entries)} stocks queued for market open"},
    }
    await _post(webhook_url, {"embeds": [embed]})


async def post_trade_open(
    ticker: str, side: str, qty: int, entry_price: float,
    stop_loss: float, mfcs: float, path: str,
    webhook_url: str | None = None,
    *,
    gap_pct: float | None = None,
    intended_entry: float | None = None,
) -> None:
    """Post a "BOUGHT X" alert.

    Doc 165 additions (all optional, kwarg-only):
      gap_pct: The premarket gap that triggered the trade. Shown
               as a badge next to the entry price.
      intended_entry: Original limit price the bot tried to fill
               at. If different from actual fill, slippage % is
               shown so the operator sees how much price ran
               between submit and fill (today VELO ran +8.8%
               across 4 BRIDGE_CANCEL retries before filling).
    """
    if not webhook_url:
        return

    risk_pct = abs(entry_price - stop_loss) / entry_price if entry_price > 0 else 0
    stop_pct_signed = ((stop_loss - entry_price) / entry_price) if entry_price > 0 else 0.0
    total_cost = qty * entry_price
    max_loss = qty * abs(entry_price - stop_loss)
    confidence_bar = _momentum_bar(mfcs)
    # Doc 165: slippage from intended entry (positive = paid up).
    slippage_str = ""
    if intended_entry is not None and intended_entry > 0 and intended_entry != entry_price:
        _slip = (entry_price - intended_entry) / intended_entry
        slippage_str = (
            f"\n\U0001f4cf Slippage: **{_pct_badge(_slip)}** "
            f"(intended ${intended_entry:.4f}, filled ${entry_price:.4f})"
        )

    if path == "FAST_PATH":
        path_desc = "\u26a1 Fast-Track (instant at market open)"
    elif path == "PHASE2_BUY":
        path_desc = "\U0001f9e0 Full Analysis (AI-evaluated)"
    else:
        path_desc = f"\U0001f504 {path}"

    embed = {
        "title": f"\U0001f7e2 BOUGHT {ticker}",
        "description": (
            f"\U0001f4c5 **{_date_header()}** | \u23f0 {_time_str()}\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
            f"\U0001f4b5 **{qty} shares** of **{ticker}** at **{_price_with_change(entry_price, gap_pct)}** each"
            f"{slippage_str}\n"
            f"\U0001f4b0 Total position: **${total_cost:,.2f}**\n\n"
            f"\U0001f6e1\ufe0f Safety stop at **{_price_with_change(stop_loss)}** "
            f"({_pct_badge(stop_pct_signed)} from entry, max loss: **${max_loss:,.2f}** / {_pct_badge(risk_pct, signed=False)})\n\n"
            f"AI Confidence: `{confidence_bar}` {mfcs:.0%}\n"
            f"Entry method: {path_desc}"
        ),
        "color": 0x2ECC71,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"Momentum-X | Position Opened"},
    }
    await _post(webhook_url, {"embeds": [embed]})


async def post_trade_close(
    ticker: str, qty: int, entry_price: float, exit_price: float,
    pnl: float, exit_reason: str, hold_minutes: float | None = None,
    webhook_url: str | None = None,
) -> None:
    if not webhook_url:
        return

    pnl_pct = ((exit_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
    emoji = _pnl_emoji(pnl)
    is_win = pnl >= 0

    if hold_minutes and hold_minutes >= 60:
        hold_str = f"{hold_minutes / 60:.1f} hours"
    elif hold_minutes:
        hold_str = f"{hold_minutes:.0f} minutes"
    else:
        hold_str = "Unknown"

    # Friendly exit reason
    reason_map = {
        "D163 Trailing Stop": "\U0001f6e1\ufe0f Trailing Stop — locked in profits as price pulled back",
        "D164 Early Profit": "\U0001f4b0 Early Profit Take — grabbed quick gains",
        "D78 Smart Exit": "\U0001f9e0 Smart Exit — AI detected momentum fading",
        "STOP_LOSS": "\U0001f6d1 Stop Loss — cut the loss to protect capital",
        "EOD_CLOSE": "\U0001f307 End of Day — closed before market close",
        "TRANCHE_FILL": "\U0001f3af Target Hit — reached profit target",
    }
    friendly_reason = reason_map.get(exit_reason, f"\U0001f504 {exit_reason}")

    embed = {
        "title": f"{emoji} SOLD {ticker} — {'PROFIT' if is_win else 'LOSS'}",
        "description": (
            f"\U0001f4c5 **{_date_header()}** | \u23f0 {_time_str()}\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
            f"{'__**WON**__' if is_win else '__**LOST**__'} "
            f"**${abs(pnl):,.2f}** ({pnl_pct:+.1f}%)\n\n"
            f"\U0001f4c8 Bought at **${entry_price:.2f}** \u2192 Sold at **${exit_price:.2f}**\n"
            f"\U0001f4e6 **{qty} shares** held for **{hold_str}**\n\n"
            f"**Why it was sold:**\n{friendly_reason}"
        ),
        "color": 0x2ECC71 if is_win else 0xE74C3C,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"Momentum-X | Position Closed"},
    }
    await _post(webhook_url, {"embeds": [embed]})


async def post_verdict_summary(
    verdicts: list[dict],
    webhook_url: str | None = None,
) -> None:
    """Phase 2 evaluation results — what the AI decided."""
    if not webhook_url or not verdicts:
        return
    if not _should_send("verdict_summary", min_interval=60.0):
        return

    buys = [v for v in verdicts if v.get("action") in ("BUY", "STRONG_BUY")]
    holds = [v for v in verdicts if v.get("action") == "HOLD"]
    no_trades = [v for v in verdicts if v.get("action") == "NO_TRADE"]

    # Doc 165: BUY/HOLD lines now include current price + gap badge
    # so the operator sees both the score AND the price action that
    # drove it. Required keys per verdict dict: ticker, mfcs, action.
    # Optional: price (float), gap_pct (signed float, e.g. 0.382 for +38%).
    def _vline(v: dict, *, prefix: str, action: str | None = None) -> str:
        ticker = v.get("ticker", "?")
        mfcs = v.get("mfcs", 0)
        bar = _momentum_bar(mfcs)
        price = v.get("price")
        gap_pct = v.get("gap_pct")
        price_str = _price_with_change(price, gap_pct) if price else ""
        head = f"{prefix} **{action or ''} {ticker}**".rstrip()
        if price_str:
            head += f" @ {price_str}"
        return f"{head} — Confidence: `{bar}` {mfcs:.0%}"

    lines = []
    for v in buys:
        lines.append(_vline(v, prefix="\U0001f7e2", action="BUY"))
    for v in holds[:3]:
        lines.append(_vline(v, prefix="\U0001f7e1", action="HOLD"))
    if no_trades:
        lines.append(f"\U0001f534 {len(no_trades)} stocks rejected (didn't meet quality bar)")

    desc = "\n".join(lines) if lines else "No stocks evaluated yet."

    if buys:
        title = f"\U0001f9e0 AI Says BUY {len(buys)} Stock{'s' if len(buys) != 1 else ''}!"
        color = 0x2ECC71
    elif holds:
        title = f"\U0001f9e0 AI Evaluated — Watching {len(holds)}, No Buys Yet"
        color = 0xF39C12
    else:
        title = "\U0001f9e0 AI Evaluated — Nothing Worth Buying Right Now"
        color = 0x95A5A6

    embed = {
        "title": title,
        "description": (
            f"\U0001f4c5 **{_date_header()}** | \u23f0 {_time_str()}\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
            f"{desc}"
        ),
        "color": color,
        "fields": [
            {
                "name": "\U0001f4d6 How this works",
                "value": (
                    "The AI reads news, checks volume, analyzes price patterns, "
                    "and scores each stock. Only stocks that pass **all** quality "
                    "checks get a BUY signal."
                ),
                "inline": False,
            },
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"Momentum-X | {len(verdicts)} stocks analyzed"},
    }
    await _post(webhook_url, {"embeds": [embed]})


# ═══════════════════════════════════════════════════════════════
#  2026-05-12 enhancements: rich event-driven Discord coverage
# ═══════════════════════════════════════════════════════════════


def alert_halt_blocked_entry_sync(
    *,
    ticker: str,
    side: str,
    qty: int,
    limit_price: float,
    stop_loss: float,
    halt_reason: str,
    webhook_url: str | None = None,
) -> None:
    """2026-05-12: D277 HALT switch silently blocked an OTO entry.

    Doc 156 incident: 8 valid trades blocked over 2 days (WOK, PLUG,
    TDIC×4, VSTS×2) without any operator visibility -- discovered only
    via end-of-day log audit. This alert fires EVERY time D277 refuses
    a submission, so the operator can decide whether to lift the halt
    mid-session.

    Sync because called from the alpaca_client _d277_halt_response
    function (synchronous code path).
    """
    if not webhook_url:
        return
    if not _should_send(f"halt_block_{ticker}", min_interval=60.0):
        # rate-limit per-ticker so storms don't spam (one alert per minute per symbol)
        return
    notional = qty * limit_price
    risk = qty * abs(limit_price - stop_loss)
    # Doc 165: show stop distance as a signed pct of the intended
    # entry, so the operator immediately knows how aggressive the
    # stop was -- a too-tight stop on a sub-$1 stock is the D294
    # failure mode (today QUCY rejected with stop only -1.3% from base).
    stop_pct = ((stop_loss - limit_price) / limit_price) if limit_price > 0 else 0.0
    embed = {
        "title": "⛔ HALT BLOCKED Entry",
        "description": (
            f"\U0001f4c5 **{_date_header()}** | ⏰ {_time_str()}\n\n"
            f"**The bot tried to {side.upper()} {ticker} but was blocked by the kill switch.**\n\n"
            f"\U0001f4b5 Wanted: **{qty} shares** of **{ticker}** at **{_price_with_change(limit_price)}**\n"
            f"\U0001f4b0 Notional: **${notional:,.2f}** (risk if filled: ${risk:,.2f})\n"
            f"\U0001f6d1 Stop: **{_price_with_change(stop_loss)}** ({_pct_badge(stop_pct)} from entry)\n\n"
            f"**Reason:** `{halt_reason}` is enabled.\n\n"
            "**To lift the halt and resume trading:**\n"
            "```powershell\n"
            "[Environment]::SetEnvironmentVariable("
            "'MOMENTUM_HALT_NEW_ENTRIES', $null, 'User')\n"
            "schtasks /end /TN MomentumX-PaperTrading\n"
            "schtasks /run /TN MomentumX-PaperTrading\n"
            "```"
        ),
        "color": 0xE67E22,  # orange — operator action required
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"Momentum-X | D277 HALT_NEW_ENTRIES"},
    }
    _post_sync(webhook_url, {"embeds": [embed]})


async def alert_data_ingest_result(
    *,
    success: bool,
    aftermath_strat_max_d0: str | None = None,
    rows: int | None = None,
    duration_sec: float | None = None,
    failed_step: str | None = None,
    webhook_url: str | None = None,
) -> None:
    """2026-05-12: notify Discord when nightly MomentumX-DataIngest fires.

    SUCCESS posts a green embed with the new max_d0 + row count + runtime.
    FAILURE posts a red embed with the failed step name so operator knows
    the lake is stale and the next morning's bot will use yesterday's data.
    """
    if not webhook_url:
        return
    if success:
        embed = {
            "title": "✅ Daily Data Ingest OK",
            "description": (
                f"\U0001f4c5 **{_date_header()}** | ⏰ {_time_str()}\n\n"
                f"Lake refreshed cleanly. Tomorrow morning's bot will use this data."
            ),
            "color": 0x2ECC71,
            "fields": [
                {"name": "max d0", "value": f"`{aftermath_strat_max_d0 or '?'}`", "inline": True},
                {"name": "rows", "value": f"**{rows:,}**" if rows else "?", "inline": True},
                {"name": "runtime", "value": f"{duration_sec:.0f}s" if duration_sec else "?", "inline": True},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Momentum-X | MomentumX-DataIngest"},
        }
    else:
        embed = {
            "title": "❌ Daily Data Ingest FAILED",
            "description": (
                f"\U0001f4c5 **{_date_header()}** | ⏰ {_time_str()}\n\n"
                f"The nightly data refresh failed at step: **{failed_step or 'unknown'}**.\n\n"
                f"**Impact:** tomorrow's bot will use stale data. Shadow runner won't\n"
                f"have today's d0 to score. **Investigate:** see "
                f"`logs/data_ingest_<date>.log` for the failure."
            ),
            "color": 0xE74C3C,
            "fields": [
                {"name": "Failed step", "value": f"`{failed_step or 'unknown'}`", "inline": True},
                {"name": "Last good max_d0", "value": f"`{aftermath_strat_max_d0 or '?'}`", "inline": True},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Momentum-X | MomentumX-DataIngest"},
        }
    await _post(webhook_url, {"embeds": [embed]})


async def alert_session_end_rich(
    *,
    session_date: str,
    trades: int,
    realized_pnl: float,
    unrealized_pnl: float,
    positions_at_close: int,
    starting_equity: float,
    ending_equity: float,
    d_codes_fired: list[str] | None = None,
    top_winners: list[dict] | None = None,
    top_losers: list[dict] | None = None,
    eod_recon: dict | None = None,
    bocpd_refit: dict | None = None,
    halt_blocked_count: int = 0,
    veto_summary: dict | None = None,
    webhook_url: str | None = None,
) -> None:
    """2026-05-12: comprehensive EOD report — replaces minimal alert_session_end.

    Posts a rich Discord embed with everything the operator needs to assess
    the session's health WITHOUT having to read logs:
      - Equity / P&L / fills / positions / halt blocks
      - D-codes fired (e.g., D262 BOCPD refit, D238 EOD recon delta)
      - Top winners + top losers (per-trade breakdown)
      - EOD reconciliation status
      - Veto breakdown by reason
      - BOCPD refit recommendation if any

    Uses the OPS alert webhook (operator channel, ~5 msgs/day max).
    """
    if not webhook_url:
        return

    pnl_emoji = _pnl_emoji(realized_pnl)
    equity_change = ending_equity - starting_equity
    equity_pct = (equity_change / starting_equity * 100) if starting_equity > 0 else 0

    # Header description
    if trades == 0 and halt_blocked_count > 0:
        header = (
            f"**{halt_blocked_count} entries BLOCKED by HALT switch** -- "
            f"bot wanted to trade but couldn't. Lift switch + restart "
            f"to resume."
        )
    elif trades == 0:
        header = "No trades today. The system scanned but no candidates met the quality bar."
    elif realized_pnl > 0:
        header = f"Profitable day. **{trades}** trade(s) executed."
    elif realized_pnl < 0:
        header = f"Loss day. **{trades}** trade(s) executed."
    else:
        # doc 209: exactly 0.0 with trades>0 almost always means the P&L source was empty
        # (the old bug), not a true scratch. Say so honestly instead of "Profitable day".
        header = f"**{trades}** trade(s) executed. (P&L $0.00 — verify reconciliation below.)"

    fields = [
        {"name": "\U0001f4b0 Realized P&L", "value": f"**${realized_pnl:+,.2f}**", "inline": True},
        {"name": "\U0001f4ca Trades", "value": f"**{trades}**", "inline": True},
        {"name": "\U0001f4e6 Open at Close", "value": f"**{positions_at_close}**", "inline": True},
        {"name": "\U0001f3e6 Starting Equity", "value": f"${starting_equity:,.2f}", "inline": True},
        {"name": "\U0001f3e6 Ending Equity", "value": f"${ending_equity:,.2f}", "inline": True},
        {"name": "\U0001f4c8 Day Change", "value": f"${equity_change:+,.2f} ({equity_pct:+.2f}%)", "inline": True},
    ]

    # D-codes fired
    if d_codes_fired:
        codes_str = ", ".join(f"`{c}`" for c in d_codes_fired)
        fields.append({
            "name": f"\U0001f6a8 D-Codes Fired ({len(d_codes_fired)})",
            "value": codes_str[:1024],
            "inline": False,
        })

    # Halt blocks
    if halt_blocked_count > 0:
        fields.append({
            "name": f"⛔ HALT-Blocked Entries ({halt_blocked_count})",
            "value": (
                f"D277 refused {halt_blocked_count} OTO submission(s) today. "
                "Lift `MOMENTUM_HALT_NEW_ENTRIES` env var to resume."
            ),
            "inline": False,
        })

    # Top winners (Doc 165: arrow + entry/exit price visibility)
    if top_winners:
        wlines = []
        for w in top_winners[:3]:
            t = w.get("ticker", "?")
            p = w.get("pnl", 0.0)
            r = w.get("pnl_pct", 0.0) / 100.0  # caller passes %, normalize
            entry_px = w.get("entry_price")
            exit_px = w.get("exit_price")
            extra = ""
            if entry_px and exit_px:
                extra = f"  ({_price_with_change(entry_px)} → {_price_with_change(exit_px)})"
            wlines.append(
                f"{_pct_arrow(r)} **{t}** ${p:+,.2f} ({_pct_badge(r)}){extra}"
            )
        fields.append({
            "name": "\U0001f3c6 Top Winners",
            "value": "\n".join(wlines)[:1024],
            "inline": True,
        })

    # Top losers (Doc 165: arrow + entry/exit price visibility)
    if top_losers:
        llines = []
        for ll in top_losers[:3]:
            t = ll.get("ticker", "?")
            p = ll.get("pnl", 0.0)
            r = ll.get("pnl_pct", 0.0) / 100.0
            entry_px = ll.get("entry_price")
            exit_px = ll.get("exit_price")
            extra = ""
            if entry_px and exit_px:
                extra = f"  ({_price_with_change(entry_px)} → {_price_with_change(exit_px)})"
            llines.append(
                f"{_pct_arrow(r)} **{t}** ${p:+,.2f} ({_pct_badge(r)}){extra}"
            )
        fields.append({
            "name": "\U0001f4c9 Top Losers",
            "value": "\n".join(llines)[:1024],
            "inline": True,
        })

    # EOD reconciliation
    if eod_recon:
        delta = eod_recon.get("delta_usd", 0.0)
        n_disagree = len(eod_recon.get("ticker_disagreements", []))
        recon_emoji = "✅" if abs(delta) < 1.0 and n_disagree == 0 else "⚠️"
        fields.append({
            "name": f"{recon_emoji} EOD Reconciliation",
            "value": (
                f"broker_pnl=${eod_recon.get('broker_total_pnl', 0):+,.2f}  "
                f"journal_pnl=${eod_recon.get('journal_total_pnl', 0):+,.2f}  "
                f"delta=${delta:+,.2f}  disagreements={n_disagree}"
            )[:1024],
            "inline": False,
        })

    # BOCPD refit recommendation
    if bocpd_refit and bocpd_refit.get("refit_recommended"):
        old_mu = bocpd_refit.get("old_mu", 0)
        new_mu = bocpd_refit.get("new_mu", 0)
        n = bocpd_refit.get("n_trades", 0)
        fields.append({
            "name": "\U0001f4ca D262 BOCPD Refit Recommended",
            "value": (
                f"Prior mu drifted from ${old_mu:.2f} -> ${new_mu:.2f} (n={n}).\n"
                f"Run: `python scripts/pretrain_bocpd_prior.py`"
            )[:1024],
            "inline": False,
        })

    # Veto breakdown
    if veto_summary:
        v_lines = []
        for reason, count in sorted(veto_summary.items(), key=lambda kv: -kv[1])[:5]:
            v_lines.append(f"  {reason}: **{count}**")
        if v_lines:
            fields.append({
                "name": f"\U0001f6ab Vetoes by Reason ({sum(veto_summary.values())} total)",
                "value": "\n".join(v_lines)[:1024],
                "inline": False,
            })

    embed = {
        "title": f"{pnl_emoji} EOD Report -- {session_date}",
        "description": (
            f"\U0001f4c5 **{_date_header()}** | ⏰ Market closed\n\n"
            f"{header}"
        ),
        "color": 0x2ECC71 if realized_pnl >= 0 else 0xE74C3C,
        "fields": fields[:25],  # Discord limit
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Momentum-X | EOD Comprehensive Report"},
    }
    await _post(webhook_url, {"embeds": [embed]})


# ── 2026-05-13 (doc 165) NEW ALERT: post_entry_filled ─────────────────
# Today's deep dive surfaced a new visibility gap: the bot submits an
# OTO (post_trade_open today fires AT submit time), but VELO took 4
# attempts before actually filling. The first 3 BRIDGE_CANCELs left
# the operator with no way to know whether the position was actually
# open until the next heartbeat. This alert fires AT FILL, with full
# context: actual fill price, slippage from limit, gap, stop distance,
# and the time-from-submit-to-fill so churn is visible.

async def post_entry_filled(
    *,
    ticker: str,
    qty: int,
    fill_price: float,
    intended_entry: float,
    stop_loss: float,
    gap_pct: float | None = None,
    fill_latency_ms: float | None = None,
    n_submission_attempts: int = 1,
    webhook_url: str | None = None,
) -> None:
    """Posted at OTO fill confirmation. Includes:
      - actual fill price w/ gap badge
      - slippage from intended_entry (signed pct)
      - stop distance as signed pct of fill
      - n_submission_attempts (so D237 BRIDGE_CANCEL churn is visible)
      - fill latency from first submit to confirm
    """
    if not webhook_url:
        return
    slip = ((fill_price - intended_entry) / intended_entry) if intended_entry > 0 else 0.0
    stop_pct = ((stop_loss - fill_price) / fill_price) if fill_price > 0 else 0.0
    notional = qty * fill_price
    risk = qty * abs(fill_price - stop_loss)

    extras = []
    if n_submission_attempts > 1:
        extras.append(
            f"\u26a0\ufe0f **{n_submission_attempts} OTO attempts** before fill "
            f"(D237 BRIDGE_CANCEL churn -- check stop math)"
        )
    if fill_latency_ms is not None:
        extras.append(f"\u23f1\ufe0f Fill latency: **{fill_latency_ms / 1000:.1f}s** from first submit")
    extras_block = ("\n" + "\n".join(extras)) if extras else ""

    embed = {
        "title": f"\u2705 FILLED {ticker}",
        "description": (
            f"\U0001f4c5 **{_date_header()}** | \u23f0 {_time_str()}\n"
            f"{_separator()}\n\n"
            f"\U0001f4b5 **{qty} shares** of **{ticker}** filled at "
            f"**{_price_with_change(fill_price, gap_pct)}**\n"
            f"\U0001f4cf Slippage from limit: **{_pct_badge(slip)}** "
            f"(intended ${intended_entry:.4f})\n"
            f"\U0001f4b0 Notional: **${notional:,.2f}**  | Risk to stop: **${risk:,.2f}**\n"
            f"\U0001f6e1\ufe0f Stop: **{_price_with_change(stop_loss)}** ({_pct_badge(stop_pct)} from fill)"
            f"{extras_block}"
        ),
        "color": _color_for_pct(gap_pct or 0.0),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Momentum-X | Entry filled at broker"},
    }
    await _post(webhook_url, {"embeds": [embed]})

