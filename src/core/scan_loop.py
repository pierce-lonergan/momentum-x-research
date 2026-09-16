"""
MOMENTUM-X Live Scan Loop

### ARCHITECTURAL CONTEXT
Node ID: core.scan_loop
Graph Link: scanner.premarket → scanner.gex_filter → core.orchestrator

### RESEARCH BASIS
Orchestrates the scan → filter → enrich → evaluate pipeline in a
single iteration or polling loop.

Ref: MOMENTUM_LOGIC.md §1 (EMC definition)
Ref: MOMENTUM_LOGIC.md §19 (GEX integration)
Ref: ADR-012 (GEX Tiered Architecture)
Ref: ADR-014 (Pipeline Closure)

### CRITICAL INVARIANTS
1. GEX hard filter applied BEFORE agent evaluation (saves LLM tokens).
2. Missing GEX data → candidate passes (graceful degradation).
3. Single scan iteration must complete in < 5s (scanner only, no LLM).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import polars as pl

from config.settings import Settings
from src.core.models import CandidateStock
from src.scanners.premarket import scan_premarket_gappers
from src.scanners.gex_filter import should_reject_gex
from src.monitoring.metrics import get_metrics

logger = logging.getLogger(__name__)


class ScanLoop:
    """
    Orchestrates a single scan iteration or continuous polling loop.

    Node ID: core.scan_loop
    Ref: ADR-014 (Pipeline Closure)

    Pipeline per iteration:
      1. Convert raw quotes to Polars DataFrame
      2. Run EMC filter (scan_premarket_gappers)
      3. Enrich with GEX data (if available)
      4. Apply GEX hard filter
      5. Return filtered CandidateStock list
    """

    def __init__(
        self,
        settings: Settings,
        gex_calculator: Any | None = None,
        options_provider: Any | None = None,
    ) -> None:
        self._settings = settings
        self._gex_calc = gex_calculator
        self._options_provider = options_provider

    def run_single_scan(
        self,
        quotes: dict[str, dict[str, Any]],
        gex_overrides: dict[str, float] | None = None,
    ) -> list[CandidateStock]:
        """
        Run a single scan iteration.

        Args:
            quotes: Dict of ticker → {current_price, previous_close, premarket_volume,
                     avg_volume_at_time, float_shares, market_cap, has_news}.
            gex_overrides: Optional dict of ticker → gex_normalized for testing.

        Returns:
            List of CandidateStock passing all filters (EMC + GEX hard).

        Ref: MOMENTUM_LOGIC.md §1, §19
        """
        if not quotes:
            return []

        metrics = get_metrics()
        metrics.scan_iterations.inc()

        # Step 1: Build Polars DataFrame from raw quotes
        df = self._quotes_to_dataframe(quotes)
        if df.is_empty():
            return []

        # Step 2: Run EMC filter (D199: pass universe_config for CATALYST tier)
        candidates = scan_premarket_gappers(
            df,
            self._settings.thresholds,
            universe_config=getattr(self._settings, "universe", None),
        )

        # Step 3: GEX enrichment + hard filter
        gex_data = gex_overrides or {}
        filtered: list[CandidateStock] = []

        for candidate in candidates:
            # D219: Skip GEX for micro/small-cap stocks (<$500M market cap).
            # Research unanimously agrees GEX is meaningless for low-cap stocks:
            # options chains are too illiquid for dealer hedging to influence price.
            # GEX only becomes a meaningful signal above ~$2B market cap.
            _mcap = candidate.market_cap or 0
            if _mcap > 0 and _mcap < 500_000_000:
                # Skip GEX computation entirely — treat as neutral
                metrics.gex_filter_passes.inc()
                filtered.append(candidate)
                continue

            gex_norm = gex_data.get(candidate.ticker)

            # If no override, try live GEX computation
            if gex_norm is None and self._gex_calc and self._options_provider:
                gex_norm = self._compute_live_gex(candidate)

            # Hard filter: reject extreme positive GEX
            if should_reject_gex(gex_norm):
                metrics.gex_filter_rejections.inc()
                logger.info(
                    "GEX hard filter rejected %s (GEX_norm=%.3f, mcap=$%.0fM)",
                    candidate.ticker, gex_norm or 0.0, _mcap / 1e6,
                )
                continue

            metrics.gex_filter_passes.inc()

            # Enrich candidate with GEX data if available
            if gex_norm is not None:
                # Reconstruct with GEX fields (CandidateStock is frozen)
                candidate = CandidateStock(
                    ticker=candidate.ticker,
                    company_name=candidate.company_name,
                    current_price=candidate.current_price,
                    previous_close=candidate.previous_close,
                    gap_pct=candidate.gap_pct,
                    gap_classification=candidate.gap_classification,
                    rvol=candidate.rvol,
                    premarket_volume=candidate.premarket_volume,
                    float_shares=candidate.float_shares,
                    market_cap=candidate.market_cap,
                    has_news_catalyst=candidate.has_news_catalyst,
                    avg_daily_volume=candidate.avg_daily_volume,
                    scan_timestamp=candidate.scan_timestamp,
                    scan_phase=candidate.scan_phase,
                    gex_normalized=gex_norm,
                )

            filtered.append(candidate)

        logger.info(
            "Scan iteration: %d quotes → %d EMC candidates → %d after GEX filter",
            len(quotes), len(candidates), len(filtered),
        )
        metrics.scan_candidates_found.inc(len(filtered))

        return filtered

    def _compute_live_gex(self, candidate: CandidateStock) -> float | None:
        """Compute live GEX for a candidate if options provider available."""
        try:
            from datetime import date as date_type
            chain = self._options_provider.get_chain(
                candidate.ticker, date_type.today()
            )
            if not chain:
                return None

            # Use previous day's full-session volume as ADV.
            # Falls back to premarket_volume * 10 only if no ADV data available.
            adv = candidate.avg_daily_volume
            if not adv or adv <= 0:
                adv = candidate.premarket_volume * 10
                logger.warning(
                    "%s: No ADV data, falling back to premarket_volume × 10 = %d "
                    "(GEX normalization may be unreliable)",
                    candidate.ticker, adv,
                )

            result = self._gex_calc.compute(
                candidate.ticker,
                candidate.current_price,
                chain,
                adv=adv,
            )
            return result.gex_normalized
        except Exception as e:
            logger.debug("Live GEX computation failed for %s: %s", candidate.ticker, e)
            return None

    @staticmethod
    def _quotes_to_dataframe(quotes: dict[str, dict[str, Any]]) -> pl.DataFrame:
        """Convert raw quotes dict to Polars DataFrame for scanner."""
        _KEY_FIELDS = ("current_price", "previous_close", "premarket_volume")
        rows = []
        for ticker, data in quotes.items():
            # Log when key fields are missing or zero so we know data was
            # incomplete (Finding 11 – silent-drop audit).  Behaviour is
            # intentionally unchanged; we only surface the information.
            for _field in _KEY_FIELDS:
                if _field not in data or not data[_field]:
                    logger.debug(
                        "%s: key field '%s' missing or zero in quote data "
                        "(defaulting to 0) — may cause silent elimination",
                        ticker,
                        _field,
                    )
            rows.append({
                "ticker": ticker,
                "current_price": float(data.get("current_price", 0)),
                "previous_close": float(data.get("previous_close", 0)),
                "premarket_volume": int(data.get("premarket_volume", 0)),
                "avg_volume_at_time": float(data.get("avg_volume_at_time", 1)),
                "prev_volume": data.get("prev_volume"),
                "float_shares": data.get("float_shares"),
                "market_cap": data.get("market_cap"),
                "has_news": data.get("has_news", False),
            })

        if not rows:
            return pl.DataFrame()

        return pl.DataFrame(rows)
