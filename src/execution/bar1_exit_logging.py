"""
D146 BAR-1 EXIT log-line formatting + exit-price resolver (Bug F + Bug Q).

Background. Today's BAR-1 EXIT log lines for ELSE / AGPU / MAAS were
identical:

    D146 BAR-1 EXIT: ELSE — selling 2478/2478 shares (100%) at T+60s. Arena: +$5.11 (+299%).
    D146 BAR-1 EXIT: AGPU — selling 846/846 shares (100%) at T+60s. Arena: +$5.11 (+299%).
    D146 BAR-1 EXIT: MAAS — selling 200/200 shares (100%) at T+60s. Arena: +$5.11 (+299%).

The "+$5.11 (+299%)" was the Arena research backtest mean (motivating
the BAR-1 pattern, see D146 docstring), NOT a per-trade P&L. Logged
without qualifier, it read as live realized P&L — three structurally
different trades emitting an identical literal.

Fix per directive: relabel as `Arena_expected` (the backtest mean), and
emit a separate `Arena_actual` line after `close_with_attribution`
completes carrying the realized per-trade P&L. Centralize the backtest
constants here so they cannot drift silently from the underlying
research again.

Bug Q (2026-04-23) addendum: BAR-1 exit-price resolution must NOT
silently fall back to entry_price when the snapshot is missing — that
masks every realized outcome behind an artificial $0.00. The
`resolve_bar1_exit_price` helper enforces snapshot → broker NBBO mid
→ explicit D228 warning → fallback chain. Never silent.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Arena research constants ───────────────────────────────────────
# Source: BAR-1 sell-100% backtest from D146 development (Mar 2026).
# The arena's strongest finding: MFE peaks at bar 1.
# 100% exit at T+60s = +$5.11 mean realized / trade vs the original
# strategy mean → +299% relative uplift. Walk-forward ratio 2.0x
# (borderline overfit). Test-window profit factor 3.20.
#
# These are STATIC backtest summary statistics — they describe the
# pattern's expected behavior in research, NOT what the live system
# realized on any given day. Any update to the underlying backtest
# must change this constant in one place; nothing else encodes the
# mean.
ARENA_EXPECTED_MEAN_USD: float = 5.11
ARENA_EXPECTED_UPLIFT_PCT: float = 299.0


def format_arena_expected_line(
    *,
    ticker: str,
    qty: int,
    total: int,
    pct: float,
    age_s: float,
) -> str:
    """
    Pre-exit log line emitted when D146 BAR-1 EXIT fires. Carries the
    Arena_expected backtest annotation so operators see the research
    benchmark — but explicitly qualified, never bare 'Arena: $X.XX'.
    """
    return (
        f"D146 BAR-1 EXIT: {ticker} — selling {qty}/{total} shares "
        f"({pct * 100:.0f}%) at T+{age_s:.0f}s. "
        f"Arena_expected: +${ARENA_EXPECTED_MEAN_USD:.2f} "
        f"(+{ARENA_EXPECTED_UPLIFT_PCT:.0f}%) [backtest mean]."
    )


def format_arena_actual_line(
    *,
    ticker: str,
    realized_pnl: float,
    entry_price: float,
    exit_price: float,
    qty: int,
) -> str:
    """
    Post-exit log line emitted after close_with_attribution completes.
    Carries the realized per-trade P&L tagged Arena_actual=$X.XX so it
    is greppable and obviously distinct from the expected-value
    annotation.
    """
    sign = "+" if realized_pnl >= 0 else "-"
    return (
        f"D146 BAR-1 ACTUAL: {ticker} — Arena_actual={sign}${abs(realized_pnl):.2f} "
        f"(entry=${entry_price:.4f} exit=${exit_price:.4f} qty={qty}). "
        f"Compare vs Arena_expected=+${ARENA_EXPECTED_MEAN_USD:.2f} backtest mean."
    )


# ── Bug Q fix (2026-04-23) — explicit exit-price resolution ────────


async def resolve_bar1_exit_price(
    *,
    snap: dict[str, Any],
    client: Any,
    ticker: str,
    fallback_price: float,
) -> tuple[float, str]:
    """
    Resolve the BAR-1 exit price with an explicit fallback chain.
    Never silent — when the chain falls through to fallback_price
    (typically `pos.entry_price`), emits a D228 BAR1_EXIT_PX_DEGRADED
    warning so operators can see that the realised P&L computation
    used a degraded price source.

    Resolution chain:
      1. snap['last_price'] if present and > 0       → source="snapshot"
      2. NBBO mid from client.get_latest_quote(...)  → source="broker_nbbo_mid"
      3. fallback_price (with D228 warning)          → source="fallback_entry_price"

    Args:
        snap: D78 snapshot dict for the ticker (may be empty).
        client: Alpaca client with `get_latest_quote(symbol)` method.
        ticker: Symbol (used in warning logs only).
        fallback_price: Last-resort price (typically pos.entry_price).

    Returns:
        (price, source) — the resolved price plus a source tag for
        the Arena_actual log line. The source tag IS the operational
        signal for whether to trust the exit P&L for v2.2 calibration.
    """
    # 1. Snapshot path
    snap_price = float(snap.get("last_price", 0) or 0)
    if snap_price > 0:
        return snap_price, "snapshot"

    # 2. Broker NBBO mid path
    try:
        quote = await client.get_latest_quote(ticker)
        if quote:
            bid = float(quote.get("bp", 0) or 0)
            ask = float(quote.get("ap", 0) or 0)
            if bid > 0 and ask > 0 and ask >= bid:
                return (bid + ask) / 2.0, "broker_nbbo_mid"
    except Exception as e:
        logger.debug(
            "Bug Q: %s broker NBBO lookup raised: %s", ticker, e,
        )

    # 3. Fallback with explicit warning — DO NOT silently equal entry
    logger.warning(
        "D228 BAR1_EXIT_PX_DEGRADED %s: snapshot empty AND broker NBBO "
        "lookup failed — falling back to entry_price ($%.4f). The "
        "Arena_actual P&L for this trade reflects a degraded price "
        "source, NOT a real market quote. Calibration must exclude "
        "this trade from η_perm estimation per v2.2 §13.3.",
        ticker, fallback_price,
    )
    return fallback_price, "fallback_entry_price"
