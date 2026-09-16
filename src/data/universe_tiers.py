"""D199: Tiered Stock Universe — expands beyond low-float to mid-cap catalysts.

### ARCHITECTURAL CONTEXT
Node ID: data.universe_tiers
Graph Link: scanners.premarket → data.universe_tiers → core.orchestrator

### STRATEGY RATIONALE
Every stock traded so far has been a low-float gap-up: $1-$5 price, tiny dollar
volume, extreme RVOL, promotional schemes. 0% win rate. These are the HARDEST
stocks to trade.

Mid-cap catalyst plays ($1B-$10B market cap) gapping on earnings/FDA/contracts
are fundamentally different: real institutional ownership, actual liquidity, tighter
spreads, and their gap-ups tend to HOLD because institutional money supports the
price.

### TIERS

Tier 1 (MOMENTUM): Existing low-float gap-ups
  - Price: $1.50-$50
  - Market cap: < $1B (or unknown)
  - Gap: > 5% (scanner minimum; explosive = 20%+)
  - RVOL: > 2x
  - Dollar volume: > $2M
  - Risk: HIGH — promotional schemes, illiquid, violent reversals
  - Parameters: faller_reject_threshold=0.60, wider stops (35%), longer observation (15 min)
  - Short selling: enabled (these are the fader candidates)

Tier 2 (CATALYST): Mid-cap catalyst plays — NEW in D199
  - Price: $5-$200
  - Market cap: $1B-$50B
  - Gap: > 3% (lower threshold — mid-caps don't gap 100%)
  - RVOL: > 1.5x
  - Dollar volume: > $10M (ensures institutional liquidity)
  - Risk: MEDIUM — institutional support, real catalysts, holds better
  - Parameters: faller_reject_threshold=0.70 (less aggressive), tighter stops (8%),
                shorter observation (5 min — these move on real catalysts fast)
  - Short selling: disabled (institutional support = dangerous to short)

Tier 3 (LARGE_CAP): Large-cap movers — future expansion placeholder
  - Price: $20+
  - Market cap: > $50B
  - Gap: > 2%
  - Parameters: TBD — not yet active
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UniverseTier(str, Enum):
    """Stock universe tier classification."""
    MOMENTUM = "momentum"     # Low-float gap-ups (existing strategy)
    CATALYST = "catalyst"     # Mid-cap catalyst plays (D199 — new)
    LARGE_CAP = "large_cap"   # Future expansion (not yet active)
    UNKNOWN = "unknown"       # Doesn't fit any active tier


@dataclass(frozen=True)
class TierConfig:
    """Per-tier trading parameters that override global defaults."""

    tier: UniverseTier

    # Scanning criteria
    price_min: float        # Minimum stock price ($)
    price_max: float        # Maximum stock price ($)
    market_cap_min: float   # Minimum market cap ($ — 0 means no floor)
    market_cap_max: float   # Maximum market cap ($ — inf means no ceiling)
    gap_min_pct: float      # Minimum gap-up percentage (0.03 = 3%)
    rvol_min: float         # Minimum relative volume ratio
    dollar_volume_min: float  # Minimum daily dollar volume ($)

    # Execution parameters (override global settings per tier)
    max_positions: int              # Concurrent positions in this tier
    faller_reject_threshold: float  # Faller score → REJECT (lower = stricter)
    stop_distance_pct: float        # Stop loss distance from entry (0.08 = 8%)
    trailing_activation_pct: float  # Gain before trailing stop activates (0.02 = 2%)
    short_selling_enabled: bool     # Whether short selling is allowed for this tier
    observation_minutes: float      # D170 entry delay window duration (minutes)


# ── Pre-configured tier definitions ──────────────────────────────────────────

TIER_CONFIGS: dict[UniverseTier, TierConfig] = {
    UniverseTier.MOMENTUM: TierConfig(
        tier=UniverseTier.MOMENTUM,
        # Scanning: matches existing ScannerThresholds defaults
        price_min=1.50,
        price_max=50.0,
        market_cap_min=0,
        market_cap_max=1_000_000_000,         # < $1B
        gap_min_pct=0.05,                     # 5% — standard EMC threshold
        rvol_min=2.0,
        dollar_volume_min=2_000_000,          # $2M — D160 minimum
        # Execution: aggressive parameters for volatile low-floats
        max_positions=2,
        faller_reject_threshold=0.60,         # D162: tighter for promotional faders
        stop_distance_pct=0.35,               # Wide stop — these whip violently
        trailing_activation_pct=0.02,         # 2% gain before trailing activates
        short_selling_enabled=True,           # Faders become short candidates (D161)
        observation_minutes=15.0,             # 15 min D170 window — these can run/fade fast
    ),
    UniverseTier.CATALYST: TierConfig(
        tier=UniverseTier.CATALYST,
        # Scanning: mid-cap catalyst criteria
        price_min=5.0,
        price_max=200.0,                      # Mid-caps can be priced well above $50
        market_cap_min=1_000_000_000,         # $1B — institutional ownership threshold
        market_cap_max=50_000_000_000,        # $50B ceiling — above this is large-cap
        gap_min_pct=0.03,                     # 3% — mid-caps don't gap 100%
        rvol_min=1.5,                         # 1.5x — meaningful for mid-cap baseline
        dollar_volume_min=10_000_000,         # $10M — ensures real institutional liquidity
        # Execution: less aggressive for better-behaved mid-cap movers
        max_positions=2,
        faller_reject_threshold=0.70,         # Less strict — institutional support reduces fader risk
        stop_distance_pct=0.08,               # Tight stop OK — less volatile than low-floats
        trailing_activation_pct=0.015,        # 1.5% gain before trailing activates
        short_selling_enabled=False,          # Institutional support = dangerous to short
        observation_minutes=5.0,              # 5 min window — catalyst moves are real and fast
    ),
    UniverseTier.LARGE_CAP: TierConfig(
        tier=UniverseTier.LARGE_CAP,
        # Scanning: large-cap movers (placeholder — not yet active)
        price_min=20.0,
        price_max=10_000.0,
        market_cap_min=50_000_000_000,        # $50B+
        market_cap_max=float("inf"),
        gap_min_pct=0.02,                     # 2% — large-caps rarely gap big
        rvol_min=1.2,
        dollar_volume_min=50_000_000,         # $50M — large-cap liquidity standard
        # Execution: TBD — use conservative defaults
        max_positions=1,
        faller_reject_threshold=0.75,
        stop_distance_pct=0.05,
        trailing_activation_pct=0.01,
        short_selling_enabled=False,
        observation_minutes=3.0,
    ),
}


class UniverseClassifier:
    """
    Classifies a stock into its universe tier based on price, market cap,
    gap, RVOL, and dollar volume.

    CATALYST is checked before MOMENTUM so higher-quality stocks are
    correctly identified even if they overlap on lower bounds.

    Usage:
        classifier = UniverseClassifier()
        tier = classifier.classify("NVDA", 500.0, 2e12, 0.05, 2.0, 50e6)
        overrides = classifier.should_override_params(tier)
    """

    def classify(
        self,
        ticker: str,
        price: float,
        market_cap: float | None,
        gap_pct: float,
        rvol: float,
        dollar_volume: float,
    ) -> UniverseTier:
        """
        Classify a stock into its universe tier.

        Checks CATALYST tier first (higher quality) before falling through
        to MOMENTUM. LARGE_CAP is a placeholder — not yet active.

        Args:
            ticker: Stock symbol (used only for debug logging).
            price: Current stock price ($).
            market_cap: Market capitalization ($). None treated as unknown.
            gap_pct: Gap-up percentage (0.05 = 5%).
            rvol: Relative volume ratio (2.0 = 2x normal).
            dollar_volume: Previous-day dollar volume ($).

        Returns:
            UniverseTier enum value.
        """
        # Unknown or invalid market cap → can only be MOMENTUM (low-float path)
        # Mid-cap classification requires confirmed market cap data.
        # BUG-FIX: market_cap=0 was treated as valid, classifying bankrupt/
        # delisted stocks as MOMENTUM. Treat 0 same as None.
        if market_cap is None or market_cap <= 0:
            return self._check_momentum(price, gap_pct, rvol, dollar_volume)

        # Check CATALYST tier first — higher quality, more conservative
        cat = TIER_CONFIGS[UniverseTier.CATALYST]
        if (
            cat.price_min <= price <= cat.price_max
            and cat.market_cap_min <= market_cap <= cat.market_cap_max
            and gap_pct >= cat.gap_min_pct
            and rvol >= cat.rvol_min
            and dollar_volume >= cat.dollar_volume_min
        ):
            return UniverseTier.CATALYST

        # Check MOMENTUM tier
        # D211: Enforce market_cap_max for MOMENTUM. A $5B stock that fails CATALYST
        # criteria must NOT fall into MOMENTUM (gets 35% stops, shorts enabled).
        mom_cfg = TIER_CONFIGS[UniverseTier.MOMENTUM]
        if market_cap is not None and market_cap > mom_cfg.market_cap_max:
            return UniverseTier.UNKNOWN
        return self._check_momentum(price, gap_pct, rvol, dollar_volume)

    def _check_momentum(
        self,
        price: float,
        gap_pct: float,
        rvol: float,
        dollar_volume: float,
    ) -> UniverseTier:
        """Check MOMENTUM tier criteria (market_cap is optional here)."""
        mom = TIER_CONFIGS[UniverseTier.MOMENTUM]
        if (
            mom.price_min <= price <= mom.price_max
            and gap_pct >= mom.gap_min_pct
            and rvol >= mom.rvol_min
            and dollar_volume >= mom.dollar_volume_min
        ):
            return UniverseTier.MOMENTUM
        return UniverseTier.UNKNOWN

    def get_config(self, tier: UniverseTier) -> TierConfig:
        """Get the configuration for a tier. Falls back to MOMENTUM for UNKNOWN."""
        return TIER_CONFIGS.get(tier, TIER_CONFIGS[UniverseTier.MOMENTUM])

    def should_override_params(self, tier: UniverseTier) -> dict:
        """
        Return parameter overrides for a specific tier.

        These override global defaults (settings.faller, settings.observation, etc.)
        during candidate evaluation. Only CATALYST has meaningful differences from
        global defaults — MOMENTUM values are the same as global defaults.

        Returns:
            dict with keys matching settings field names.
        """
        config = self.get_config(tier)
        return {
            "faller_reject_threshold": config.faller_reject_threshold,
            "stop_distance_pct": config.stop_distance_pct,
            "trailing_activation_pct": config.trailing_activation_pct,
            "short_selling_enabled": config.short_selling_enabled,
            "observation_minutes": config.observation_minutes,
            "max_positions": config.max_positions,
        }


# Module-level singleton — safe for import-time use.
universe_classifier = UniverseClassifier()
