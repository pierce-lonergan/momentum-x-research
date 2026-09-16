"""
MOMENTUM-X Portfolio Risk Manager

### ARCHITECTURAL CONTEXT
Node ID: execution.portfolio_risk
Graph Link: docs/memory/graph_state.json → "execution.portfolio_risk"

### RESEARCH BASIS
Concentrated sector exposure amplifies drawdowns during sector-wide events
(e.g., semiconductor sell-off, biotech FDA rejections). This module enforces:

1. Sector concentration limits — max % of capital in any single sector
2. Cross-position correlation — block new entries highly correlated to existing positions
3. Portfolio heat — total unrealized risk across all positions

Known sector mapping uses GICS-like taxonomy for common momentum stocks.

Ref: ADR-003 §1 (Risk Management: max_positions, daily_loss_limit)
Ref: MOMENTUM_LOGIC.md §4 (Position Sizing)

### CRITICAL INVARIANTS
1. Sector limit check MUST pass before any new entry.
2. Unknown sectors default to "Other" — no free passes.
3. Portfolio heat never exceeds max_portfolio_heat_pct.
4. All checks are O(n) where n = open positions (always < 10).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Sector Taxonomy (GICS-inspired for momentum stocks) ──
# Explicit ticker map for known names; see _infer_sector() for small-cap heuristics.

TICKER_SECTOR_MAP: dict[str, str] = {
    # Technology / Semiconductors
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "AMD": "Technology", "INTC": "Technology", "AVGO": "Technology",
    "QCOM": "Technology", "TSM": "Technology", "ASML": "Technology",
    "MU": "Technology", "MRVL": "Technology", "ARM": "Technology",
    "SMCI": "Technology", "DELL": "Technology",
    # Software / Internet
    "META": "Software", "GOOG": "Software", "GOOGL": "Software",
    "AMZN": "Software", "CRM": "Software", "NOW": "Software",
    "SNOW": "Software", "PLTR": "Software", "NET": "Software",
    "DDOG": "Software", "ZS": "Software", "PANW": "Software",
    # Biotech/Pharma (large-cap + common small-cap momentum)
    "MRNA": "Biotech", "BNTX": "Biotech", "REGN": "Biotech",
    "VRTX": "Biotech", "GILD": "Biotech", "AMGN": "Biotech",
    "BIIB": "Biotech", "SGEN": "Biotech",
    # Consumer
    "TSLA": "Consumer", "NKE": "Consumer", "SBUX": "Consumer",
    "MCD": "Consumer", "COST": "Consumer", "WMT": "Consumer",
    # Finance
    "JPM": "Finance", "GS": "Finance", "MS": "Finance",
    "BAC": "Finance", "C": "Finance", "WFC": "Finance",
    "COIN": "Finance", "SQ": "Finance", "HOOD": "Finance",
    # Energy
    "XOM": "Energy", "CVX": "Energy", "SLB": "Energy",
    "OXY": "Energy", "COP": "Energy",
    # Crypto-adjacent
    "MSTR": "Crypto", "MARA": "Crypto", "RIOT": "Crypto",
    "CLSK": "Crypto", "BITF": "Crypto", "HUT": "Crypto",
    "BTBT": "Crypto", "CIFR": "Crypto",
    # China/ADR
    "BABA": "China-ADR", "PDD": "China-ADR", "JD": "China-ADR",
    "BIDU": "China-ADR", "NIO": "China-ADR", "LI": "China-ADR",
    "XPEV": "China-ADR", "FUTU": "China-ADR", "TME": "China-ADR",
    "BILI": "China-ADR", "IQ": "China-ADR", "TIGR": "China-ADR",
    "YMM": "China-ADR", "TAL": "China-ADR",
    # EV/Clean Energy
    "RIVN": "EV", "LCID": "EV", "FSR": "EV",
    "ENPH": "EV", "FSLR": "EV", "PLUG": "EV", "MULN": "EV",
    "GOEV": "EV", "FFIE": "EV", "WKHS": "EV",
    # AI/Meme
    "AI": "AI", "SOUN": "AI", "BBAI": "AI",
    "GME": "Meme", "AMC": "Meme",
    # Cannabis
    "TLRY": "Cannabis", "CGC": "Cannabis", "ACB": "Cannabis",
    "SNDL": "Cannabis", "HEXO": "Cannabis",
}

# Runtime cache for dynamically classified tickers
_sector_cache: dict[str, str] = {}

DEFAULT_SECTOR = "Other"


def get_sector(ticker: str, company_name: str = "") -> str:
    """
    Look up sector for a ticker.

    Uses three-tier resolution:
    1. Explicit TICKER_SECTOR_MAP (known names)
    2. Runtime cache (previously inferred)
    3. Heuristic inference from company name keywords

    The heuristic catches the small-cap momentum universe (biotech micro-caps,
    cannabis, SPACs, China ADRs) that the static map cannot cover.
    Defaults to 'Other' only when no heuristic matches.
    """
    upper = ticker.upper()

    # Tier 1: Explicit map
    if upper in TICKER_SECTOR_MAP:
        return TICKER_SECTOR_MAP[upper]

    # Tier 2: Runtime cache
    if upper in _sector_cache:
        return _sector_cache[upper]

    # Tier 3: Heuristic inference
    inferred = _infer_sector(upper, company_name)
    _sector_cache[upper] = inferred
    return inferred


def _infer_sector(ticker: str, company_name: str) -> str:
    """
    Heuristic sector inference for unknown tickers.

    Uses company_name keywords to classify into broad sectors.
    This handles the long tail of small-cap momentum stocks that rotate
    through the scanner daily and cannot be pre-mapped.
    """
    name_lower = company_name.lower() if company_name else ""

    # Biotech/Pharma keywords (most common small-cap momentum sector)
    biotech_keywords = (
        "therapeut", "pharma", "bioscien", "oncolog", "genomic",
        "biotech", "biopharma", "medical", "health", "diagnostic",
        "immuno", "vaccine", "clinical",
    )
    if any(kw in name_lower for kw in biotech_keywords):
        return "Biotech"

    # Cannabis
    if any(kw in name_lower for kw in ("cannabis", "marijuana", "hemp", "weed")):
        return "Cannabis"

    # China/ADR (common patterns)
    china_keywords = ("china", "chinese", "beijing", "shanghai", "hong kong")
    if any(kw in name_lower for kw in china_keywords):
        return "China-ADR"

    # EV/Clean Energy
    if any(kw in name_lower for kw in ("electric vehicle", " ev ", "solar", "clean energy", "hydrogen")):
        return "EV"

    # Crypto/Mining
    if any(kw in name_lower for kw in ("bitcoin", "crypto", "blockchain", "mining", "digital asset")):
        return "Crypto"

    # AI/Technology
    if any(kw in name_lower for kw in ("artificial intellig", " ai ", "machine learning", "robot")):
        return "AI"

    # SPAC (common in momentum universe)
    if any(kw in name_lower for kw in ("acquisition", "spac", "blank check")):
        return "SPAC"

    return DEFAULT_SECTOR


@dataclass
class PortfolioRiskCheck:
    """
    Result of a portfolio risk check for a proposed new entry.

    Node ID: execution.portfolio_risk.PortfolioRiskCheck
    """
    allowed: bool
    reason: str = ""
    sector: str = ""
    current_sector_count: int = 0
    max_sector_count: int = 0
    portfolio_heat_pct: float = 0.0


class PortfolioRiskManager:
    """
    Manages portfolio-level risk: sector concentration, portfolio heat.

    Node ID: execution.portfolio_risk
    Ref: ADR-003 §1 (Risk Management)

    Usage:
        prm = PortfolioRiskManager(
            max_sector_positions=2,
            max_portfolio_heat_pct=5.0,
        )
        check = prm.check_entry("NVDA", stop_loss_pct=2.0, positions=open_positions)
        if not check.allowed:
            logger.warning("Blocked: %s", check.reason)
    """

    def __init__(
        self,
        max_sector_positions: int = 2,
        max_portfolio_heat_pct: float = 5.0,
        max_same_catalyst_type: int = 2,
    ) -> None:
        """
        Args:
            max_sector_positions: Max open positions in any single sector.
            max_portfolio_heat_pct: Max total portfolio heat (sum of stop distances).
            max_same_catalyst_type: D110: Max positions with the same catalyst type.
                Uses news agent catalyst_type if available, otherwise falls back to
                ManipulationClassifier phase as proxy.  Known limitation: the 4
                ManipulationClassifier categories are coarse — two ORGANIC positions
                could be genuinely diversified (FDA vs technical breakout) or
                correlated (both biotech FDA).
        """
        self._max_sector = max_sector_positions
        self._max_heat = max_portfolio_heat_pct
        self._max_catalyst = max_same_catalyst_type

    def check_entry(
        self,
        ticker: str,
        stop_loss_pct: float,
        positions: list[Any],
        catalyst_type: str = "",
    ) -> PortfolioRiskCheck:
        """
        Check if a new entry passes portfolio risk constraints.

        Args:
            ticker: Proposed new position ticker.
            stop_loss_pct: Distance to stop as % (e.g., 2.0 = 2%).
            positions: List of open Position objects with .ticker attribute.
            catalyst_type: D110: Optional catalyst type for concentration check.
                Uses news agent catalyst_type if available, otherwise caller
                can pass ManipulationClassifier phase as proxy.

        Returns:
            PortfolioRiskCheck with allowed flag and reason.
        """
        sector = get_sector(ticker)

        # Check 1: Sector concentration
        sector_count = sum(1 for p in positions if get_sector(p.ticker) == sector)
        if sector_count >= self._max_sector:
            return PortfolioRiskCheck(
                allowed=False,
                reason=f"Sector concentration: {sector} has {sector_count}/{self._max_sector} positions",
                sector=sector,
                current_sector_count=sector_count,
                max_sector_count=self._max_sector,
            )

        # Check 2: D110 — Catalyst type concentration
        # Uses catalyst_type attribute if available on positions.
        # Known limitation: ManipulationClassifier's 4 categories are coarse.
        candidate_catalyst = catalyst_type if catalyst_type else None
        if candidate_catalyst and candidate_catalyst != "unknown":
            catalyst_count = sum(
                1 for p in positions
                if getattr(p, "catalyst_type", "unknown") == candidate_catalyst
            )
            if catalyst_count >= self._max_catalyst:
                return PortfolioRiskCheck(
                    allowed=False,
                    reason=(
                        f"Catalyst concentration: {candidate_catalyst} has "
                        f"{catalyst_count}/{self._max_catalyst} positions"
                    ),
                    sector=sector,
                    current_sector_count=sector_count,
                    max_sector_count=self._max_sector,
                )

        # Check 3: Portfolio heat
        current_heat = sum(
            self._estimate_position_heat(p) for p in positions
        )
        proposed_heat = current_heat + stop_loss_pct
        if proposed_heat > self._max_heat:
            return PortfolioRiskCheck(
                allowed=False,
                reason=f"Portfolio heat: {proposed_heat:.1f}% would exceed {self._max_heat}% limit",
                sector=sector,
                portfolio_heat_pct=proposed_heat,
            )

        return PortfolioRiskCheck(
            allowed=True,
            sector=sector,
            current_sector_count=sector_count,
            max_sector_count=self._max_sector,
            portfolio_heat_pct=proposed_heat,
        )

    def get_sector_exposure(self, positions: list[Any]) -> dict[str, int]:
        """
        Get current sector exposure across all positions.

        Returns:
            Dict of sector → count of open positions.
        """
        exposure: dict[str, int] = {}
        for p in positions:
            sector = get_sector(p.ticker)
            exposure[sector] = exposure.get(sector, 0) + 1
        return exposure

    def _estimate_position_heat(self, position: Any) -> float:
        """
        Estimate heat contribution of a single position.

        Heat = distance from entry to stop as percentage.

        Args:
            position: Position with entry_price and stop_loss attributes.

        Returns:
            Heat as percentage (e.g., 2.0 = 2%).
        """
        try:
            entry = getattr(position, "entry_price", 0)
            stop = getattr(position, "stop_loss", 0)
            if entry > 0 and stop > 0:
                return abs(entry - stop) / entry * 100
        except (TypeError, ZeroDivisionError):
            pass
        return 2.0  # Default conservative estimate
