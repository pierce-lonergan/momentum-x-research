"""D194: Order Flow Analysis — Institutional vs Retail Trade Classification.

Analyzes time-and-sales (T&S) data to detect institutional accumulation or
distribution. Uses tick rule direction classification, block-trade detection,
ISO flag analysis, and autocorrelation to distinguish smart-money flow from
retail noise.

Key signals:
  - Block trades (>= 1000 shares OR >= $10K) dominating volume → institutional
  - ISO (Intermarket Sweep Order) flags → aggressive institutional sweep
  - Net flow ratio > 0.3 with block dominance → INSTITUTIONAL_ACCUMULATION
  - Negative net flow with large blocks → INSTITUTIONAL_DISTRIBUTION
  - Small trades, no blocks, mixed direction → RETAIL_DOMINATED
  - Positive autocorrelation → persistent directional flow (institutional)
  - Negative autocorrelation → alternating (retail scalping / market-maker)

DETERMINISTIC signal — no LLM, pure trade microstructure math.

Ref: D194 (Order Flow design), D160 (Faller Detection), D162-D165 (stop/entry rules)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Enums & Dataclasses
# ═══════════════════════════════════════════════════════════════════

class FlowSignal(str, Enum):
    INSTITUTIONAL_ACCUMULATION = "institutional_accumulation"
    """Large blocks + positive net flow → smart money buying."""

    INSTITUTIONAL_DISTRIBUTION = "institutional_distribution"
    """Large blocks + negative net flow → smart money selling into strength."""

    RETAIL_DOMINATED = "retail_dominated"
    """Small trades, no block presence, mixed direction → no institutional footprint."""

    MIXED = "mixed"
    """Signals conflict — some institutional block buying but also distribution, or
    inconclusive metrics (e.g. near-zero net flow with moderate block ratio)."""

    INSUFFICIENT_DATA = "insufficient_data"
    """Too few trades to classify reliably (< min_trades threshold)."""


class TradeDirection(str, Enum):
    BUY  = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


@dataclass
class TradeEvent:
    """A single time-and-sales record."""
    timestamp: float          # Unix epoch seconds (float for sub-second)
    price: float
    size: int                 # Shares
    conditions: list[str] = field(default_factory=list)  # Exchange condition codes
    is_iso: bool = False      # True when condition code "F" (ISO) is present
    direction: TradeDirection = TradeDirection.UNKNOWN


@dataclass
class OrderFlowResult:
    """Complete order flow analysis for one ticker snapshot."""
    ticker: str

    # ── Raw counts ──────────────────────────────────────────────────
    total_trades: int = 0
    total_volume: int = 0           # shares
    total_dollar_volume: float = 0.0

    # ── Block trade stats ────────────────────────────────────────────
    block_trades: int = 0
    block_volume: int = 0
    block_buy_volume: int = 0
    block_sell_volume: int = 0
    block_ratio: float = 0.0        # block_volume / total_volume

    # ── Retail (small) trade stats ──────────────────────────────────
    retail_trades: int = 0
    retail_volume: int = 0
    retail_ratio: float = 0.0       # retail_volume / total_volume

    # ── Directional flow ────────────────────────────────────────────
    buy_volume: int = 0
    sell_volume: int = 0
    net_flow: int = 0               # buy_volume - sell_volume
    net_flow_ratio: float = 0.0     # net_flow / total_volume  ∈ [-1, 1]

    # ── ISO (sweep) stats ────────────────────────────────────────────
    iso_trades: int = 0
    iso_volume: int = 0
    iso_ratio: float = 0.0          # iso_volume / total_volume
    iso_buy_volume: int = 0
    iso_sell_volume: int = 0

    # ── Autocorrelation ─────────────────────────────────────────────
    direction_autocorrelation: float = 0.0   # lag-1 autocorr of trade directions
    # Positive → persistent directional flow (institutional momentum)
    # Negative → alternating directions (retail scalping / market-maker ping-pong)

    # ── Average trade size ──────────────────────────────────────────
    avg_trade_size: float = 0.0
    avg_block_size: float = 0.0

    # ── Classification ───────────────────────────────────────────────
    signal: FlowSignal = FlowSignal.INSUFFICIENT_DATA

    # ── Faller score adjustment ──────────────────────────────────────
    faller_adjustment: float = 0.0  # Signed: negative=bullish, positive=bearish

    @property
    def has_institutional_footprint(self) -> bool:
        return self.signal in (
            FlowSignal.INSTITUTIONAL_ACCUMULATION,
            FlowSignal.INSTITUTIONAL_DISTRIBUTION,
        )

    @property
    def is_accumulation(self) -> bool:
        return self.signal == FlowSignal.INSTITUTIONAL_ACCUMULATION

    @property
    def is_distribution(self) -> bool:
        return self.signal == FlowSignal.INSTITUTIONAL_DISTRIBUTION

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "signal": self.signal.value,
            "faller_adjustment": round(self.faller_adjustment, 4),
            "total_trades": self.total_trades,
            "total_volume": self.total_volume,
            "block_ratio": round(self.block_ratio, 4),
            "iso_ratio": round(self.iso_ratio, 4),
            "net_flow_ratio": round(self.net_flow_ratio, 4),
            "direction_autocorrelation": round(self.direction_autocorrelation, 4),
            "avg_trade_size": round(self.avg_trade_size, 1),
            "retail_ratio": round(self.retail_ratio, 4),
        }


# ═══════════════════════════════════════════════════════════════════
# Thresholds (module-level constants — can be overridden in config)
# ═══════════════════════════════════════════════════════════════════

BLOCK_MIN_SHARES: int = 1_000        # Shares threshold for "block trade"
BLOCK_MIN_DOLLARS: float = 10_000.0  # Dollar threshold for "block trade" (OR condition)
RETAIL_MAX_SHARES: int = 100         # Trades <= this size = retail
ISO_CONDITION_CODE: str = "F"        # Alpaca/SIP condition code for ISO trades

MIN_TRADES_FOR_CLASSIFICATION: int = 5   # Below this → INSUFFICIENT_DATA

# Signal thresholds
ACCUMULATION_NET_FLOW_MIN: float = 0.30   # net_flow_ratio must exceed this
ACCUMULATION_BLOCK_MIN: float = 0.40      # block_ratio must exceed this
ACCUMULATION_ISO_MIN: float = 0.15        # OR iso_ratio must exceed this
DISTRIBUTION_NET_FLOW_MAX: float = -0.30  # net_flow_ratio must be below this
DISTRIBUTION_BLOCK_MIN: float = 0.35      # block_ratio must still be present
RETAIL_BLOCK_MAX: float = 0.15            # block_ratio < this = retail dominated
RETAIL_NET_FLOW_ABS_MAX: float = 0.20     # |net_flow_ratio| < this = mixed direction

# Faller score adjustments (raw, before weight scaling)
FALLER_ADJ_ACCUMULATION: float = -0.20   # Institutional buying → bullish (reduce faller)
FALLER_ADJ_DISTRIBUTION: float = +0.20   # Institutional selling → bearish (raise faller)
FALLER_ADJ_RETAIL: float = +0.10         # Retail only → slight bearish (promotional risk)
FALLER_ADJ_MIXED: float = 0.0
FALLER_ADJ_INSUFFICIENT: float = 0.0


# ═══════════════════════════════════════════════════════════════════
# Analyzer
# ═══════════════════════════════════════════════════════════════════

class OrderFlowAnalyzer:
    """
    Classifies order flow from raw time-and-sales trades.

    Usage::

        analyzer = OrderFlowAnalyzer()
        result = analyzer.analyze_trades(
            ticker="AAPL",
            trades=[TradeEvent(timestamp=..., price=..., size=..., conditions=["F"]),
                    ...],
            bid=149.95,
            ask=150.05,
        )
        if result.signal == FlowSignal.INSTITUTIONAL_ACCUMULATION:
            faller_adj = result.faller_adjustment  # -0.20

    The analyzer is stateless — each call is independent. Create one instance
    and reuse across tickers.
    """

    def __init__(
        self,
        block_min_shares: int = BLOCK_MIN_SHARES,
        block_min_dollars: float = BLOCK_MIN_DOLLARS,
        retail_max_shares: int = RETAIL_MAX_SHARES,
        min_trades: int = MIN_TRADES_FOR_CLASSIFICATION,
    ) -> None:
        self.block_min_shares = block_min_shares
        self.block_min_dollars = block_min_dollars
        self.retail_max_shares = retail_max_shares
        self.min_trades = min_trades

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    def analyze_trades(
        self,
        ticker: str,
        trades: list[TradeEvent],
        bid: float,
        ask: float,
    ) -> OrderFlowResult:
        """
        Full order flow analysis for one ticker snapshot.

        Args:
            ticker: Stock ticker symbol.
            trades: List of TradeEvent objects (raw T&S records).
            bid:    Current best bid price.
            ask:    Current best ask price.

        Returns:
            OrderFlowResult with signal, adjustment, and all component metrics.
        """
        result = OrderFlowResult(ticker=ticker)

        if not trades:
            logger.debug("D194 %s: no trades → INSUFFICIENT_DATA", ticker)
            return result

        result.total_trades = len(trades)

        # Classify direction for each trade
        classified: list[TradeEvent] = []
        for t in trades:
            t.is_iso = ISO_CONDITION_CODE in t.conditions
            t.direction = self._classify_trade_direction(t, bid, ask)
            classified.append(t)

        # Aggregate metrics
        self._aggregate_metrics(result, classified)

        # Autocorrelation on direction sequence
        directions = [t.direction for t in classified]
        result.direction_autocorrelation = self._compute_autocorrelation(directions)

        # Classify signal
        result.signal = self._classify_signal(result)
        result.faller_adjustment = self.get_faller_adjustment(result)

        logger.debug(
            "D194 %s: signal=%s net_flow=%.2f block=%.2f iso=%.2f autocorr=%.2f adj=%.2f",
            ticker,
            result.signal.value,
            result.net_flow_ratio,
            result.block_ratio,
            result.iso_ratio,
            result.direction_autocorrelation,
            result.faller_adjustment,
        )

        # D221 Phase F: forward-only persistence (no-op when env-toggle is off).
        # Captures live order-flow analyses so v3 has historical flow features.
        from datetime import datetime, timezone
        from src.data._feature_persistence import persist_feature_row
        persist_feature_row("order_flow", {
            "ticker": ticker,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signal": result.signal.value,
            "total_trades": result.total_trades,
            "total_volume": result.total_volume,
            "total_dollar_volume": result.total_dollar_volume,
            "block_ratio": result.block_ratio,
            "block_buy_volume": result.block_buy_volume,
            "block_sell_volume": result.block_sell_volume,
            "iso_ratio": result.iso_ratio,
            "net_flow_ratio": result.net_flow_ratio,
            "direction_autocorrelation": result.direction_autocorrelation,
            "faller_adjustment": result.faller_adjustment,
            "bid_at_eval": bid,
            "ask_at_eval": ask,
        })

        return result

    def get_faller_adjustment(self, result: OrderFlowResult) -> float:
        """
        Return the signed faller score adjustment for this order flow signal.

        Negative = bullish (reduces faller score = more runner confidence).
        Positive = bearish (increases faller score = more fader concern).
        """
        mapping = {
            FlowSignal.INSTITUTIONAL_ACCUMULATION: FALLER_ADJ_ACCUMULATION,
            FlowSignal.INSTITUTIONAL_DISTRIBUTION: FALLER_ADJ_DISTRIBUTION,
            FlowSignal.RETAIL_DOMINATED: FALLER_ADJ_RETAIL,
            FlowSignal.MIXED: FALLER_ADJ_MIXED,
            FlowSignal.INSUFFICIENT_DATA: FALLER_ADJ_INSUFFICIENT,
        }
        return mapping.get(result.signal, 0.0)

    # ─────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────

    def _classify_trade_direction(
        self, trade: TradeEvent, bid: float, ask: float
    ) -> TradeDirection:
        """
        Tick rule + quote rule to classify trade as BUY or SELL.

        Quote rule (preferred when spread > 0):
          - Trade at or above ask  → BUY  (hitting the offer)
          - Trade at or below bid  → SELL (hitting the bid)
          - Trade inside spread    → UNKNOWN

        Midpoint fallback: trades exactly at midpoint are UNKNOWN.
        """
        if ask <= 0 or bid <= 0:
            return TradeDirection.UNKNOWN

        spread = ask - bid
        if spread <= 0:
            return TradeDirection.UNKNOWN

        midpoint = (bid + ask) / 2.0

        if trade.price >= ask:
            return TradeDirection.BUY
        elif trade.price <= bid:
            return TradeDirection.SELL
        elif trade.price > midpoint:
            # Above midpoint but inside spread → lean BUY
            return TradeDirection.BUY
        elif trade.price < midpoint:
            # Below midpoint but inside spread → lean SELL
            return TradeDirection.SELL
        else:
            return TradeDirection.UNKNOWN

    def _aggregate_metrics(
        self, result: OrderFlowResult, trades: list[TradeEvent]
    ) -> None:
        """Fill result with aggregated volume/block/retail/ISO metrics."""
        for t in trades:
            dollar_val = t.price * t.size
            result.total_volume += t.size
            result.total_dollar_volume += dollar_val

            is_block = (
                t.size >= self.block_min_shares
                or dollar_val >= self.block_min_dollars
            )
            is_retail = t.size <= self.retail_max_shares

            # Directional aggregation
            if t.direction == TradeDirection.BUY:
                result.buy_volume += t.size
            elif t.direction == TradeDirection.SELL:
                result.sell_volume += t.size

            # Block aggregation
            if is_block:
                result.block_trades += 1
                result.block_volume += t.size
                if t.direction == TradeDirection.BUY:
                    result.block_buy_volume += t.size
                elif t.direction == TradeDirection.SELL:
                    result.block_sell_volume += t.size

            # Retail aggregation
            if is_retail:
                result.retail_trades += 1
                result.retail_volume += t.size

            # ISO aggregation
            if t.is_iso:
                result.iso_trades += 1
                result.iso_volume += t.size
                if t.direction == TradeDirection.BUY:
                    result.iso_buy_volume += t.size
                elif t.direction == TradeDirection.SELL:
                    result.iso_sell_volume += t.size

        # Derived ratios (guard against zero division)
        vol = result.total_volume
        if vol > 0:
            result.block_ratio = result.block_volume / vol
            result.retail_ratio = result.retail_volume / vol
            result.iso_ratio = result.iso_volume / vol
            result.net_flow = result.buy_volume - result.sell_volume
            result.net_flow_ratio = result.net_flow / vol

        n = result.total_trades
        if n > 0:
            result.avg_trade_size = result.total_volume / n
        if result.block_trades > 0:
            result.avg_block_size = result.block_volume / result.block_trades

    def _compute_autocorrelation(
        self, directions: list[TradeDirection]
    ) -> float:
        """
        Lag-1 autocorrelation of trade direction sequence.

        Encodes: BUY=+1, SELL=-1, UNKNOWN=0.
        Returns Pearson correlation between consecutive direction pairs.

        Interpretation:
          > 0.2  → persistent flow (institutional momentum sweep)
          < -0.2 → alternating (retail scalping or market-maker quote stuffing)
          near 0 → random walk (no directional pattern)
        """
        if len(directions) < 4:
            return 0.0

        # Encode directions
        encoded: list[float] = []
        for d in directions:
            if d == TradeDirection.BUY:
                encoded.append(1.0)
            elif d == TradeDirection.SELL:
                encoded.append(-1.0)
            else:
                encoded.append(0.0)

        # Build lag-1 pairs (skip UNKNOWN pairs for cleaner signal)
        x: list[float] = []
        y: list[float] = []
        for i in range(len(encoded) - 1):
            if encoded[i] != 0.0 and encoded[i + 1] != 0.0:
                x.append(encoded[i])
                y.append(encoded[i + 1])

        if len(x) < 4:
            return 0.0

        n = len(x)
        mean_x = sum(x) / n
        mean_y = sum(y) / n

        cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / n
        std_x = math.sqrt(sum((v - mean_x) ** 2 for v in x) / n)
        std_y = math.sqrt(sum((v - mean_y) ** 2 for v in y) / n)

        if std_x < 1e-9 or std_y < 1e-9:
            return 0.0

        return cov / (std_x * std_y)

    def _classify_signal(self, result: OrderFlowResult) -> FlowSignal:
        """
        Combine metrics into a FlowSignal classification.

        Priority order (most specific first):
          1. INSUFFICIENT_DATA  — too few trades
          2. INSTITUTIONAL_ACCUMULATION — net flow + block/ISO presence
          3. INSTITUTIONAL_DISTRIBUTION — net flow + block presence (selling side)
          4. RETAIL_DOMINATED — low blocks, mixed direction
          5. MIXED — everything else
        """
        if result.total_trades < self.min_trades:
            return FlowSignal.INSUFFICIENT_DATA

        nfr = result.net_flow_ratio
        br = result.block_ratio
        ir = result.iso_ratio

        # INSTITUTIONAL_ACCUMULATION
        # Strong positive net flow + institutional presence (blocks OR sweeps)
        if (
            nfr >= ACCUMULATION_NET_FLOW_MIN
            and (br >= ACCUMULATION_BLOCK_MIN or ir >= ACCUMULATION_ISO_MIN)
        ):
            return FlowSignal.INSTITUTIONAL_ACCUMULATION

        # INSTITUTIONAL_DISTRIBUTION
        # Strong negative net flow + block presence (smart money unloading)
        if (
            nfr <= DISTRIBUTION_NET_FLOW_MAX
            and br >= DISTRIBUTION_BLOCK_MIN
        ):
            return FlowSignal.INSTITUTIONAL_DISTRIBUTION

        # RETAIL_DOMINATED
        # Few or no blocks, mixed direction → retail chop with no smart money
        if (
            br <= RETAIL_BLOCK_MAX
            and abs(nfr) <= RETAIL_NET_FLOW_ABS_MAX
        ):
            return FlowSignal.RETAIL_DOMINATED

        return FlowSignal.MIXED


# ═══════════════════════════════════════════════════════════════════
# Convenience: build TradeEvent list from raw Alpaca T&S dicts
# ═══════════════════════════════════════════════════════════════════

def trades_from_alpaca(raw_trades: list[dict]) -> list[TradeEvent]:
    """
    Convert raw Alpaca time-and-sales dicts to TradeEvent objects.

    Expected keys per dict (Alpaca Trade format):
      t  → timestamp ISO string or epoch float
      p  → price (float)
      s  → size (int)
      c  → conditions list[str] (may be absent)
    """
    from datetime import datetime, timezone

    events: list[TradeEvent] = []
    for raw in raw_trades:
        t_val = raw.get("t", 0)
        if isinstance(t_val, str):
            try:
                ts = datetime.fromisoformat(t_val.replace("Z", "+00:00")).timestamp()
            except ValueError:
                ts = 0.0
        elif isinstance(t_val, (int, float)):
            ts = float(t_val)
        else:
            ts = 0.0

        conditions = raw.get("c") or []
        events.append(TradeEvent(
            timestamp=ts,
            price=float(raw.get("p", 0.0)),
            size=int(raw.get("s", 0)),
            conditions=list(conditions),
        ))
    return events
