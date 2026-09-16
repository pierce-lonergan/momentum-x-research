"""
MOMENTUM-X Pre-Market Scanner

### ARCHITECTURAL CONTEXT
Implements the pre-market scanning phase (4:00 AM - 9:30 AM ET) from the
research framework Section II.A. This is a PURE PYTHON module — no LLM calls.
It processes raw market data to identify Explosive Momentum Candidates (EMC).

Ref: MOMENTUM_LOGIC.md §1 (EMC definition)
Ref: MOMENTUM_LOGIC.md §2 (RVOL)
Ref: MOMENTUM_LOGIC.md §3 (Gap %)
Ref: MOMENTUM_LOGIC.md §4 (ATR Ratio)
Ref: ADR-001 (Scanner position in pipeline)

### DESIGN DECISIONS
- Polars over Pandas for sub-millisecond vectorized filtering on tick data
- Async interface for non-blocking integration with WebSocket feeds
- Gap classification follows the tiered system from MOMENTUM_LOGIC.md §3
- Scanner emits CandidateStock objects — downstream agents add intelligence
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import polars as pl

from src.core.models import CandidateStock, GapClassification, GapQuality

if TYPE_CHECKING:
    from config.settings import ScannerThresholds, UniverseConfig

logger = logging.getLogger(__name__)


def classify_gap(gap_pct: float) -> GapClassification:
    """
    Classify gap magnitude per MOMENTUM_LOGIC.md §3.

    - [0.01, 0.04): MINOR — monitor only
    - [0.04, 0.10): SIGNIFICANT — active scan
    - [0.10, 0.20): MAJOR — high priority
    - >= 0.20: EXPLOSIVE — maximum priority, verify catalyst
    """
    if gap_pct >= 0.20:
        return "EXPLOSIVE"
    elif gap_pct >= 0.10:
        return "MAJOR"
    elif gap_pct >= 0.04:
        return "SIGNIFICANT"
    else:
        return "MINOR"


def classify_gap_quality(
    gap_pct: float,
    has_news: bool,
    rvol: float,
    rvol_exhaustion: bool = False,
    prior_day_gap_pct: float | None = None,
    multi_day_run: bool = False,
) -> GapQuality:
    """
    D101 §3.2: Classify gap quality (breakaway vs exhaustion).

    Exhaustion gaps have 72% accuracy for predicting reversals (LuxAlgo).

    Args:
        gap_pct: Today's gap percentage.
        has_news: Whether a catalyst was identified.
        rvol: Relative volume ratio.
        rvol_exhaustion: Whether RVOL > 5.0 (D87 flag).
        prior_day_gap_pct: Previous day's gap (None if unavailable).
        multi_day_run: Whether stock has been running for multiple days.

    Returns:
        GapQuality classification.
    """
    # EXHAUSTION: Stock already extended (multi-day run) or RVOL exhaustion
    if multi_day_run and rvol_exhaustion:
        return "EXHAUSTION"

    # EXHAUSTION: Back-to-back large gaps without catalyst = exhaustion
    if prior_day_gap_pct is not None and prior_day_gap_pct > 0.10 and gap_pct > 0.10:
        if not has_news:
            return "EXHAUSTION"

    # EXHAUSTION: Extreme RVOL without catalyst = retail FOMO blow-off
    if rvol_exhaustion and not has_news:
        return "EXHAUSTION"

    # BREAKAWAY: Catalyst confirmed + heavy volume = fresh move
    if has_news and rvol >= 3.0:
        return "BREAKAWAY"

    # BREAKAWAY: Large gap with catalyst (any RVOL)
    if has_news and gap_pct >= 0.10:
        return "BREAKAWAY"

    # CONTINUATION: Has volume but no catalyst — likely following prior move
    if not has_news and rvol >= 2.0 and gap_pct >= 0.05:
        return "CONTINUATION"

    return "UNKNOWN"


def compute_rvol(
    current_volume: int,
    historical_volumes_at_time: list[int],
) -> float:
    """
    Compute Relative Volume per MOMENTUM_LOGIC.md §2.

    RVOL = V(S, t) / V̄_n(S, t)

    Where V̄_n is the SMA of volume at the SAME TIME OF DAY
    over the past n sessions.

    Args:
        current_volume: Volume traded so far in current session
        historical_volumes_at_time: Volume at same time-of-day over past n sessions

    Returns:
        RVOL ratio (> 2.0 is "in play", > 5.0 is extreme)
    """
    if not historical_volumes_at_time:
        return 0.0
    avg = sum(historical_volumes_at_time) / len(historical_volumes_at_time)
    if avg <= 0:
        return 0.0
    return current_volume / avg


def compute_gap_pct(current_price: float, previous_close: float) -> float:
    """
    Compute gap percentage per MOMENTUM_LOGIC.md §3.

    GAP% = (P_current - P_close(t-1)) / P_close(t-1)
    """
    if previous_close <= 0:
        return 0.0
    return (current_price - previous_close) / previous_close


def scan_premarket_gappers(
    quotes_df: pl.DataFrame,
    thresholds: ScannerThresholds,
    universe_config: UniverseConfig | None = None,
) -> list[CandidateStock]:
    """
    Scan pre-market data to identify Explosive Momentum Candidates.

    D199: Supports two parallel filter paths:
      - MOMENTUM path: existing low-float gap-up criteria
      - CATALYST path: mid-cap catalyst plays ($1B+ mcap, 3%+ gap, $10M+ dolvol)

    Implements the EMC conjunction from MOMENTUM_LOGIC.md §1:
        EMC(S, t) = 𝟙[RVOL > τ_rvol] ∧ 𝟙[GAP% > τ_gap] ∧ 𝟙[ATR_RATIO > τ_atr]

    Args:
        quotes_df: Polars DataFrame with columns:
            - ticker (str)
            - current_price (f64)
            - previous_close (f64)
            - premarket_volume (i64)
            - avg_volume_at_time (f64) — historical average at same time-of-day
            - float_shares (i64, nullable)
            - market_cap (f64, nullable)
            - has_news (bool)
        thresholds: ScannerThresholds from config
        universe_config: D199 UniverseConfig controlling which tiers are active.
            None = MOMENTUM-only (backward-compatible default).

    Returns:
        List of CandidateStock sorted by gap_pct descending (strongest gappers first)

    Ref: Research framework Section II.A (Pre-Market Analysis)
    """
    now = datetime.now(timezone.utc)

    # ── Step 1: Compute derived columns ──
    enriched = quotes_df.with_columns([
        # Gap percentage (MOMENTUM_LOGIC.md §3)
        # D121 BUG-E4: Guard previous_close=0 to avoid Inf/NaN gap_pct
        (
            (pl.col("current_price") - pl.col("previous_close"))
            / pl.when(pl.col("previous_close") > 0).then(pl.col("previous_close")).otherwise(pl.lit(1.0))
        ).alias("gap_pct"),

        # RVOL (MOMENTUM_LOGIC.md §2)
        # D121 BUG-C4: When avg_volume_at_time=0 (no history), replacing with 1
        # inflated RVOL to raw premarket volume (e.g., 500,000x). New IPOs with
        # no history would pass every filter with fabricated extreme RVOL.
        # Fix: Replace 0 with a conservative floor (50,000) so unknown-history
        # stocks get a plausible RVOL that doesn't game the filters.
        (
            pl.col("premarket_volume").cast(pl.Float64)
            / pl.col("avg_volume_at_time").replace(0, 50_000)
        ).alias("rvol"),
    ])

    # ── D101: Compute dollar volume for liquidity filter ──
    # RVOL measures relative activity, not absolute liquidity. A stock with 50K avg
    # volume at 5x RVOL still only has 250K shares — too thin for our position sizes.
    # Use prev_volume (previous day's full-session volume) as ADV proxy for dollar
    # volume calculation, NOT avg_volume_at_time (which is a time-bucketed intraday avg).
    _has_prev_volume = "prev_volume" in enriched.columns
    if _has_prev_volume:
        enriched = enriched.with_columns(
            (
                pl.col("current_price")
                * pl.col("prev_volume").fill_null(0).cast(pl.Float64)
            ).alias("dollar_volume")
        )
    else:
        # Fallback: use premarket_volume × 10 as rough ADV estimate
        enriched = enriched.with_columns(
            (
                pl.col("current_price")
                * pl.col("premarket_volume").cast(pl.Float64) * 10
            ).alias("dollar_volume")
        )

    # ── Step 2: Apply EMC filters ──
    # D105: Price filter restructured for tiered price floor.
    # Standard path:     price >= $1.50 (D160: lowered from $3.00)
    # High-volume path:  price >= $0.50 AND dollar_vol > $2M AND rvol > 5x
    # D116: Extreme-RVOL path: price >= $0.50 AND rvol > 30x (bypass dollar vol)
    # D160: Mega-dolvol path: dollar_vol > $10M — bypasses ALL price floors.
    #        ITRM had $54.6M dolvol at $0.07 but was blocked because price_min_high_volume
    #        ($0.50) is still applied in the high-vol path. At $10M+ dolvol, the stock is
    #        demonstrably liquid regardless of share price — the dollar volume IS the proof.
    #        This path fires FIRST (OR short-circuits) before price floor checks apply.
    _price_standard = pl.col("current_price") >= thresholds.price_min
    _price_high_vol = (
        (pl.col("current_price") >= thresholds.price_min_high_volume)
        & (pl.col("dollar_volume") > thresholds.price_override_dollar_vol)
        & (pl.col("rvol") > thresholds.price_override_rvol)
    )
    # D116: Extreme RVOL override — 30x+ RVOL indicates genuine market attention
    # regardless of dollar volume. Only requires the absolute price floor ($0.50).
    _price_extreme_rvol = (
        (pl.col("current_price") >= thresholds.price_min_high_volume)
        & (pl.col("rvol") > thresholds.extreme_rvol_override)
    )
    # D160: Mega dollar volume override — $10M+ dolvol means the stock IS liquid,
    # period. No price floor needed. ITRM ($54.6M dolvol, $0.07 price) was blocked
    # by price checks despite having more dollar liquidity than most $5+ stocks.
    # Check attribute existence for forward-compatibility with older config objects.
    _mega_dolvol_threshold = getattr(thresholds, "price_override_mega_dollar_vol", 10_000_000)
    _price_mega_dolvol = pl.col("dollar_volume") > _mega_dolvol_threshold
    _price_filter = _price_mega_dolvol | _price_standard | _price_high_vol | _price_extreme_rvol

    # D105: RVOL filter restructured for absolute volume alternative.
    # Standard path: rvol >= 2.0 (unchanged)
    # Absolute volume path: premarket_volume > 500K (catches active names like MARA)
    _rvol_standard = pl.col("rvol") >= thresholds.rvol_premarket_min
    _rvol_absolute = pl.col("premarket_volume") > thresholds.absolute_volume_override
    _rvol_filter = _rvol_standard | _rvol_absolute

    # D116: Dollar volume filter — extreme RVOL overrides the floor
    # LNAI had $3.6M dolvol (below $5M min) but 63.6x RVOL → genuine interest
    _dolvol_standard = pl.col("dollar_volume") >= thresholds.min_dollar_volume
    _dolvol_extreme_rvol = pl.col("rvol") > thresholds.extreme_rvol_override
    _dolvol_filter = _dolvol_standard | _dolvol_extreme_rvol

    # D205: Maximum gap ceiling — extreme gaps (>50%) are promotional traps
    _gap_max = getattr(thresholds, "gap_pct_max", 10.0)  # Default high if not set

    _momentum_filter = (
        # GAP% > tau_gap
        (pl.col("gap_pct") >= thresholds.gap_pct_min)
        # D205: GAP% < gap_max (reject extreme promotional gaps)
        & (pl.col("gap_pct") <= _gap_max)
        # D105: Tiered RVOL (standard OR absolute volume)
        & _rvol_filter
        # D105: Tiered price floor (standard OR high-volume override OR extreme RVOL)
        & _price_filter
        # Price ceiling (MOMENTUM tier)
        & (pl.col("current_price") <= thresholds.price_max)
        # Minimum volume
        & (pl.col("premarket_volume") >= thresholds.premarket_volume_min_7am)
        # D116: Dollar volume minimum (extreme RVOL bypasses)
        & _dolvol_filter
    )

    # D199: CATALYST tier filter — mid-cap stocks gapping on real catalysts.
    # Parallel path to MOMENTUM: lower gap/RVOL thresholds, higher dollar volume,
    # higher price ceiling, confirmed market cap $1B-$50B.
    _catalyst_enabled = (
        universe_config is not None
        and getattr(universe_config, "catalyst_enabled", False)
    )
    if _catalyst_enabled:
        _cat_gap_min = getattr(universe_config, "catalyst_gap_min_pct", 0.03)
        _cat_rvol_min = getattr(universe_config, "catalyst_rvol_min", 1.5)
        _cat_mcap_min = getattr(universe_config, "catalyst_market_cap_min", 1_000_000_000)
        _cat_dolvol_min = getattr(universe_config, "catalyst_dollar_volume_min", 10_000_000)
        _cat_price_max = getattr(universe_config, "catalyst_price_max", 200.0)
        _cat_mcap_max = 50_000_000_000  # $50B ceiling — above this is large-cap

        # market_cap column may be all-null if Alpaca doesn't return it.
        # Use fill_null(0) so null mcap stocks score 0 and fail the $1B floor.
        _catalyst_filter = (
            (pl.col("gap_pct") >= _cat_gap_min)
            & (pl.col("rvol") >= _cat_rvol_min)
            & (pl.col("current_price") >= 5.0)
            & (pl.col("current_price") <= _cat_price_max)
            & (pl.col("premarket_volume") >= thresholds.premarket_volume_min_7am)
            & (pl.col("dollar_volume") >= _cat_dolvol_min)
            & (pl.col("market_cap").fill_null(0) >= _cat_mcap_min)
            & (pl.col("market_cap").fill_null(0) <= _cat_mcap_max)
        )
        combined_filter = _momentum_filter | _catalyst_filter
        logger.debug("D199: CATALYST tier active — scanning $1B-$50B mcap stocks (gap>=%.0f%%, rvol>=%.1fx, dolvol>=$%.0fM)",
                     _cat_gap_min * 100, _cat_rvol_min, _cat_dolvol_min / 1e6)
    else:
        combined_filter = _momentum_filter

    filtered = enriched.filter(combined_filter)

    # ── D96/D105: Log per-ticker rejection reasons for EOD audit ──
    n_rejected = len(enriched) - len(filtered)
    if n_rejected > 0:
        # D105: Identify top-10 most-active tickers by premarket volume
        # from the full enriched set (before filtering). These get INFO-level
        # rejection logs for post-session audit visibility.
        _top_active_tickers: set[str] = set()
        if len(enriched) > 0:
            _top_active = (
                enriched
                .sort("premarket_volume", descending=True)
                .head(10)
                .select("ticker")
                .to_series()
                .to_list()
            )
            _top_active_tickers = set(_top_active)

        # Identify rejected rows — reuse combined_filter (MOMENTUM + CATALYST if enabled)
        _rejected = enriched.filter(~combined_filter)
        for _rej_row in _rejected.iter_rows(named=True):
            _reasons = []
            if _rej_row["gap_pct"] < thresholds.gap_pct_min:
                _reasons.append(f"gap={_rej_row['gap_pct']:.1%}<{thresholds.gap_pct_min:.0%}")
            # D105: Check tiered RVOL (standard OR absolute volume)
            _rvol_pass = (
                _rej_row["rvol"] >= thresholds.rvol_premarket_min
                or _rej_row["premarket_volume"] > thresholds.absolute_volume_override
            )
            if not _rvol_pass:
                _reasons.append(
                    f"rvol={_rej_row['rvol']:.1f}x<{thresholds.rvol_premarket_min:.0f}x"
                    f" AND vol={_rej_row['premarket_volume']:,}<={thresholds.absolute_volume_override:,}"
                )
            # D105/D116/D160: Check tiered price (mega-dolvol OR standard OR high-volume OR extreme RVOL)
            _rej_dolvol = _rej_row.get("dollar_volume", 0) or 0
            _price_pass = (
                _rej_dolvol > _mega_dolvol_threshold  # D160: mega dolvol bypasses ALL price checks
                or _rej_row["current_price"] >= thresholds.price_min
                or (
                    _rej_row["current_price"] >= thresholds.price_min_high_volume
                    and _rej_dolvol > thresholds.price_override_dollar_vol
                    and _rej_row["rvol"] > thresholds.price_override_rvol
                )
                or (
                    _rej_row["current_price"] >= thresholds.price_min_high_volume
                    and _rej_row["rvol"] > thresholds.extreme_rvol_override
                )
            )
            if not _price_pass:
                _reasons.append(
                    f"price=${_rej_row['current_price']:.2f}<${thresholds.price_min:.2f}"
                    f" (no override: dolvol=${_rej_dolvol:,.0f}<${_mega_dolvol_threshold:,.0f},"
                    f" rvol={_rej_row['rvol']:.1f}x)"
                )
            if _rej_row["current_price"] > thresholds.price_max:
                _reasons.append(f"price=${_rej_row['current_price']:.2f}>${thresholds.price_max:.2f}")
            if _rej_row["premarket_volume"] < thresholds.premarket_volume_min_7am:
                _reasons.append(f"vol={_rej_row['premarket_volume']:,}<{thresholds.premarket_volume_min_7am:,}")
            if (
                _rej_row.get("dollar_volume", 0) < thresholds.min_dollar_volume
                and _rej_row["rvol"] <= thresholds.extreme_rvol_override
            ):
                _reasons.append(f"dolvol=${_rej_row.get('dollar_volume', 0):,.0f}<${thresholds.min_dollar_volume:,.0f}")
            # D105: Promote to INFO for top-10 most-active tickers
            _ticker = _rej_row["ticker"]
            _log_level = logging.INFO if _ticker in _top_active_tickers else logging.DEBUG
            logger.log(
                _log_level,
                "D96 EMC REJECT: %s — %s",
                _ticker, " | ".join(_reasons),
            )

    # ── Step 3: Sort by gap strength (strongest first) ──
    sorted_df = filtered.sort("gap_pct", descending=True)

    # ── Step 4: Convert to domain models ──
    candidates: list[CandidateStock] = []
    for row in sorted_df.iter_rows(named=True):
        gap_pct = row["gap_pct"]
        prev_vol = row.get("prev_volume")
        rvol_val = row["rvol"]
        # ── D121: Stale gap detection ──
        # Alpaca's prevDailyBar.c can return a 2-day-old close when the previous
        # day's bar hasn't settled yet (common on micro-caps). This creates
        # "phantom gaps" where the system sees +15% when the stock is actually DOWN.
        # Fix: If today's open (dailyBar.o) already incorporates the gap, the gap
        # is stale — the move happened on a prior day, not today.
        _day_open = row.get("day_open")
        if _day_open and _day_open > 0 and row["previous_close"] > 0:
            _open_gap_pct = (_day_open - row["previous_close"]) / row["previous_close"]
            _intraday_change_pct = (row["current_price"] - _day_open) / _day_open if _day_open > 0 else 0
            # If the open already shows >80% of the gap from prev_close,
            # the gap is from a prior session — the stock OPENED already gapped.
            # And if intraday change is flat or negative, this is NOT a fresh move.
            if (
                _open_gap_pct > 0.05  # Open is >5% above prev_close
                and gap_pct > 0.05  # Scanner sees a gap
                and _open_gap_pct >= gap_pct * 0.70  # Open accounts for >=70% of the gap
                and _intraday_change_pct < 0.03  # Stock hasn't moved up >3% since open
            ):
                logger.info(
                    "D121 STALE GAP: %s — gap=%.1f%% but open=%.1f%% above prev_close, "
                    "intraday=%.1f%%. Gap is from prior session, skipping.",
                    row["ticker"], gap_pct * 100, _open_gap_pct * 100,
                    _intraday_change_pct * 100,
                )
                continue

        # D87: Flag extreme RVOL as potential exhaustion
        # RVOL > 5.0 while overbought often precedes reversals, not continuation.
        # D121 BUG-H5: Sub-$3 stocks enter via price_override_rvol >= 5.0, so
        # using 5.0 as exhaustion threshold flags ALL of them. Use 15x for
        # sub-$3 stocks so the price override path isn't self-defeating.
        _exhaustion_rvol_threshold = 15.0 if row["current_price"] < thresholds.price_min else 5.0
        rvol_exhaustion = rvol_val > _exhaustion_rvol_threshold
        # D87: Flag potential corporate actions (gap > 100% without news = likely split)
        corporate_action = None
        if abs(gap_pct) > 1.0 and not row.get("has_news", False):
            corporate_action = "POSSIBLE_SPLIT" if gap_pct > 1.0 else "POSSIBLE_REVERSE_SPLIT"
            logger.warning(
                "D87: %s has %.0f%% gap with no news catalyst — possible corporate action",
                row["ticker"], gap_pct * 100,
            )
        # D101 §3.2: Classify gap quality (breakaway vs exhaustion)
        _has_news = row.get("has_news", False)
        # D121: Compute prior_day_gap_pct from day_open when available.
        # If today's open >> prev_close, yesterday also had a gap from this prev_close.
        # This wires the previously-dead exhaustion detection in classify_gap_quality().
        _prior_day_gap = row.get("prior_day_gap_pct")
        _multi_day = row.get("multi_day_run", False)
        if _prior_day_gap is None and _day_open and _day_open > 0 and row["previous_close"] > 0:
            _open_vs_prev = (_day_open - row["previous_close"]) / row["previous_close"]
            # If today's open is significantly above prev_close (>5%), and the
            # current price is also above prev_close, this is likely a multi-day gap.
            if _open_vs_prev > 0.05:
                _prior_day_gap = _open_vs_prev
                _multi_day = True
        _gap_quality = classify_gap_quality(
            gap_pct=gap_pct,
            has_news=_has_news,
            rvol=rvol_val,
            rvol_exhaustion=rvol_exhaustion,
            prior_day_gap_pct=_prior_day_gap,
            multi_day_run=_multi_day,
        )
        if _gap_quality == "EXHAUSTION":
            logger.info(
                "D101 §3.2: %s EXHAUSTION gap — filtered out (gap=%.1f%%, "
                "rvol=%.1fx, news=%s)",
                row["ticker"], gap_pct * 100, rvol_val, _has_news,
            )
            continue  # Skip exhaustion gaps entirely

        candidates.append(
            CandidateStock(
                ticker=row["ticker"],
                current_price=row["current_price"],
                previous_close=row["previous_close"],
                gap_pct=gap_pct,
                gap_classification=classify_gap(gap_pct),
                gap_quality=_gap_quality,
                rvol=rvol_val,
                premarket_volume=row["premarket_volume"],
                float_shares=row.get("float_shares"),
                market_cap=row.get("market_cap"),
                has_news_catalyst=_has_news,
                avg_daily_volume=int(prev_vol) if prev_vol else None,
                scan_timestamp=now,
                scan_phase="PRE_MARKET",
                rvol_exhaustion=rvol_exhaustion,
                corporate_action_flag=corporate_action,
            )
        )

    logger.info(
        "Pre-market scan complete: %d candidates from %d stocks",
        len(candidates),
        len(quotes_df),
    )
    return candidates
