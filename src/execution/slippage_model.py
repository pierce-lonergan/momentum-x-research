"""D196: Dynamic slippage model proportional to position size / dollar volume.

Instead of flat 0.5% slippage, models actual market impact tiered by liquidity:
  - High-liquidity   ($50M+ dolvol):  0.05–0.1%  slippage
  - Medium-liquidity ($5M–$50M):      0.2–0.5%   slippage
  - Low-liquidity    (<$5M):          1–3%        slippage
  - Micro-liquidity  (<$500K):        3–5%+       slippage

Formula:
    slippage = spread/2 + participation_impact
    participation_impact = (position_dollars / daily_dollar_volume) * impact_coefficient

Stop fills on illiquid / volatile stocks use a wider model to reflect
gap-through risk that the existing flat STOP_FILL_SLIPPAGE_PCT misses.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Liquidity tier boundaries (daily dollar volume, USD) ──────────────────────
_MICRO_DOLVOL = 500_000
_LOW_DOLVOL = 5_000_000
_MEDIUM_DOLVOL = 50_000_000

# ── Impact coefficients by tier ───────────────────────────────────────────────
# participation_impact = (position_dollars / dolvol) * coefficient
_IMPACT_COEFF_MICRO = 15.0
_IMPACT_COEFF_LOW = 6.0
_IMPACT_COEFF_MEDIUM = 2.5
_IMPACT_COEFF_HIGH = 0.8

# ── Absolute slippage caps by tier ────────────────────────────────────────────
_CAP_MICRO = 0.10       # 10%
_CAP_LOW = 0.05         # 5%
_CAP_MEDIUM = 0.02      # 2%
_CAP_HIGH = 0.005       # 0.5%

# ── Stop fill extra penalty ───────────────────────────────────────────────────
# Stops on illiquid / volatile stocks gap through; model extra slippage.
_STOP_VOLATILITY_SCALE = 2.0


@dataclass(frozen=True)
class SlippageEstimate:
    """Estimated slippage for a single leg."""

    slippage_pct: float         # Fraction of trade value (0.01 = 1%)
    spread_component: float     # Half-spread crossing cost
    impact_component: float     # Participation / market-impact cost
    tier: str                   # Liquidity tier label


def _liquidity_tier(daily_dollar_volume: float) -> tuple[str, float, float]:
    """Return (tier_name, impact_coeff, cap) for a given daily dollar volume."""
    if daily_dollar_volume < _MICRO_DOLVOL:
        return "micro", _IMPACT_COEFF_MICRO, _CAP_MICRO
    if daily_dollar_volume < _LOW_DOLVOL:
        return "low", _IMPACT_COEFF_LOW, _CAP_LOW
    if daily_dollar_volume < _MEDIUM_DOLVOL:
        return "medium", _IMPACT_COEFF_MEDIUM, _CAP_MEDIUM
    return "high", _IMPACT_COEFF_HIGH, _CAP_HIGH


class SlippageModel:
    """D196 dynamic slippage model.

    Replaces the flat ENTRY_SLIPPAGE_PCT / STOP_FILL_SLIPPAGE_PCT constants
    in the meta simulation with liquidity-tiered estimates.
    """

    def estimate_entry_slippage(
        self,
        position_dollars: float,
        daily_dollar_volume: float,
        spread_pct: float = 0.01,
    ) -> SlippageEstimate:
        """Estimate entry slippage as a percentage of trade value.

        Args:
            position_dollars: Size of the order in USD.
            daily_dollar_volume: Stock's typical daily dollar volume.
            spread_pct: Current bid-ask spread as fraction (0.01 = 1%).

        Returns:
            SlippageEstimate with component breakdown.
        """
        tier, coeff, cap = _liquidity_tier(daily_dollar_volume)

        # Half-spread cost (crossing the book)
        spread_component = spread_pct / 2.0

        # Participation / impact cost
        if daily_dollar_volume > 0:
            participation = position_dollars / daily_dollar_volume
            impact_component = participation * coeff
        else:
            impact_component = cap

        total = min(spread_component + impact_component, cap)
        return SlippageEstimate(
            slippage_pct=total,
            spread_component=spread_component,
            impact_component=impact_component,
            tier=tier,
        )

    def estimate_stop_slippage(
        self,
        position_dollars: float,
        daily_dollar_volume: float,
        volatility_pct: float = 0.05,
    ) -> SlippageEstimate:
        """Estimate stop fill slippage.

        Stops on volatile, illiquid stocks fill worse than limit orders:
          - Spread is effectively crossed at worst (full spread, not half).
          - Gap-through risk scales with volatility.

        Args:
            position_dollars: Size of the position being stopped out.
            daily_dollar_volume: Stock's daily dollar volume.
            volatility_pct: Single-bar volatility estimate (0.05 = 5%).

        Returns:
            SlippageEstimate for the stop fill.
        """
        tier, coeff, cap = _liquidity_tier(daily_dollar_volume)

        # Full spread crossing (stops are market orders on gap-through)
        spread_component = volatility_pct * 0.20   # proxy: 20% of bar range

        # Impact scaled up for stops (urgency premium + gap-through)
        if daily_dollar_volume > 0:
            participation = position_dollars / daily_dollar_volume
            impact_component = participation * coeff * _STOP_VOLATILITY_SCALE
        else:
            impact_component = cap

        total = min(spread_component + impact_component, cap)
        return SlippageEstimate(
            slippage_pct=total,
            spread_component=spread_component,
            impact_component=impact_component,
            tier=tier,
        )

    def estimate_total_round_trip(
        self,
        position_dollars: float,
        daily_dollar_volume: float,
        spread_pct: float = 0.01,
    ) -> float:
        """Total slippage cost of entry + exit (round trip), as a fraction.

        Args:
            position_dollars: Trade size in USD.
            daily_dollar_volume: Stock's daily dollar volume.
            spread_pct: Bid-ask spread as fraction.

        Returns:
            Combined entry + exit slippage fraction (0.02 = 2% round trip).
        """
        entry = self.estimate_entry_slippage(position_dollars, daily_dollar_volume, spread_pct)
        # Assume exit is also an entry-equivalent (limit or market on trail/target)
        exit_ = self.estimate_entry_slippage(position_dollars, daily_dollar_volume, spread_pct)
        return entry.slippage_pct + exit_.slippage_pct
