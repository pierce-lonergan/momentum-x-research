"""
D160: Faller Risk Score — Pre-Execution Gate

### ARCHITECTURAL CONTEXT
Runs AFTER MFCS scoring (which decides direction/conviction) and BEFORE order
submission. Its job is orthogonal to MFCS: where MFCS asks "is there a signal?",
the Faller Risk Score asks "will this gap UP or fade?".

MFCS can pass a stock with high technical momentum even when manipulation signals,
spread, and VWAP position scream "distribution in progress". This module is the
last line of defense before capital is committed.

### DESIGN RATIONALE (Mar 30 post-mortem)
Today the system entered ARTL (-54.7%) and SST (-35%). Both had MFCS above
threshold. The differentiating signals were:
  - ARTL: 85% manipulation probability, no catalyst, unknown float, zero SEC data
  - SST:  Bearish news, 5.6% spread, $138K dolvol, 20% below VWAP at evaluation time

Stocks that GAP and KEEP RUNNING share a distinct microstructure fingerprint:
  - Real news catalyst (BFRG: pharma deal, ELAB: licensing deal)
  - Price at or above VWAP (institutional demand absorbing supply)
  - Tight spread (real two-sided market)
  - High dollar volume (institutional flow, not just retail noise)
  - RSI in sweet spot 60-80 (momentum without blow-off exhaustion)
  - Multiple agents independently bullish

Stocks that FADE share the opposite:
  - No real catalyst (gap is promotional or FOMO-driven)
  - Price already below VWAP (smart money distributed into the open spike)
  - Wide spread (market-maker risk premium for illiquid pump)
  - Sub-$500K dollar volume (thin, easy to print green then disappear)
  - Manipulation classifier > 70% probability

### SCORING FORMULA
score = baseline + Σ bearish_weights - Σ bullish_weights, clamped to [0, 1]

baseline = 0.20 (any gap-up stock has inherent reversal risk)

The score is NOT a calibrated probability. It's a relative ranking for position
sizing and rejection decisions. Recalibrate weights from actual trade outcomes
using the arena analysis field (faller_score logged to journal).

### POSITION SIZING INTEGRATION
  score 0.00-0.30 → full position  (confident runner)
  score 0.30-0.50 → 75% position
  score 0.50-0.60 → 50% position  (D162: was 0.50-0.65)
  score  > 0.60   → REJECT (predicted fader) — routes to short eval via D161

Ref: D160 (Faller Detection design), D162 (threshold lowered 0.65→0.60),
     D191 (SEC EDGAR pre-fetcher integration)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from config.settings import FallerDetectionConfig
    from src.core.models import CandidateStock, ScoredCandidate
    from src.data.sec_prefetcher import SECFilingResult
    from src.data.sentiment_velocity import SentimentVelocityResult
    from src.data.short_interest import ShortInterestResult, SqueezeClassification
    from src.data.order_flow import OrderFlowResult

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Output Model
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FallerAssessment:
    """Result of FallerRiskDetector.score() for one candidate."""

    ticker: str
    score: float                         # 0.0 (runner) to 1.0 (faller)
    reject: bool                         # True if score > reject_threshold
    position_multiplier: float           # 0.0 (reject) to 1.0 (full size)

    # Contributing factors (for logging + arena analysis)
    bearish_factors: list[tuple[str, float]] = field(default_factory=list)
    bullish_factors: list[tuple[str, float]] = field(default_factory=list)

    # D192: Squeeze archetype flag
    squeeze_classification: str = "unknown"  # SqueezeClassification.value
    shorting_blocked_by_squeeze: bool = False  # True when SQUEEZE prevents short routing

    # D193: Sentiment velocity flag
    sentiment_momentum: str = "silent"  # NarrativeMomentum.value

    # D194: Order flow signal
    order_flow_signal: str = "insufficient_data"  # FlowSignal.value
    order_flow_adjustment: float = 0.0             # Signed contribution to faller score

    # Signal values used in scoring (for arena replay)
    manipulation_prob: float = 0.0
    has_catalyst: bool = False
    vwap: float | None = None
    current_price: float = 0.0
    spread_proxy: float = 0.0           # Inferred from risk_breakdown["liquidity"]
    dollar_volume: float = 0.0
    rsi: float | None = None
    macd_positive: bool = False
    bullish_agent_count: int = 0
    bearish_agent_count: int = 0

    @property
    def top_factors(self) -> list[str]:
        """Top 3 factors by absolute weight, for logging."""
        all_factors = (
            [(name, -w) for name, w in self.bullish_factors]  # negative = bullish
            + [(name, w) for name, w in self.bearish_factors]
        )
        all_factors.sort(key=lambda x: abs(x[1]), reverse=True)
        return [f"{name}({w:+.2f})" for name, w in all_factors[:3]]

    @property
    def position_action(self) -> str:
        """Human-readable position sizing decision."""
        if self.reject:
            return "REJECT"
        if self.position_multiplier >= 1.0:
            return "FULL"
        if self.position_multiplier >= 0.75:
            return "75%"
        return "50%"

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "faller_score": round(self.score, 4),
            "reject": self.reject,
            "position_multiplier": round(self.position_multiplier, 3),
            "position_action": self.position_action,
            "bearish_factors": [(n, round(w, 3)) for n, w in self.bearish_factors],
            "bullish_factors": [(n, round(w, 3)) for n, w in self.bullish_factors],
            "signals": {
                "manipulation_prob": round(self.manipulation_prob, 3),
                "has_catalyst": self.has_catalyst,
                "vwap": self.vwap,
                "current_price": self.current_price,
                "spread_proxy": round(self.spread_proxy, 3),
                "dollar_volume": self.dollar_volume,
                "rsi": self.rsi,
                "macd_positive": self.macd_positive,
                "bullish_agent_count": self.bullish_agent_count,
                "bearish_agent_count": self.bearish_agent_count,
            },
            "squeeze_classification": self.squeeze_classification,
            "shorting_blocked_by_squeeze": self.shorting_blocked_by_squeeze,
            "sentiment_momentum": self.sentiment_momentum,
            "order_flow_signal": self.order_flow_signal,
            "order_flow_adjustment": round(self.order_flow_adjustment, 4),
        }


# ═══════════════════════════════════════════════════════════════════
# Detector
# ═══════════════════════════════════════════════════════════════════

class FallerRiskDetector:
    """
    Computes a Faller Risk Score for each BUY candidate before order submission.

    Usage (in main.py, after verdict sorting and before execute_verdict):

        detector = FallerRiskDetector(settings.faller)
        ...
        assessment = detector.score(candidate, scored, indicators)
        if assessment.reject:
            continue
        if assessment.position_multiplier < 1.0:
            verdict = verdict.model_copy(update={
                "position_size_pct": verdict.position_size_pct * assessment.position_multiplier
            })
    """

    def __init__(self, config: FallerDetectionConfig) -> None:
        self.cfg = config

    def score(
        self,
        candidate: CandidateStock,
        scored: ScoredCandidate,
        indicators: dict,
        sec_result: Optional[SECFilingResult] = None,
        short_interest_result: Optional["ShortInterestResult"] = None,
        sentiment_velocity_result: Optional["SentimentVelocityResult"] = None,
        order_flow_result: Optional["OrderFlowResult"] = None,
        **kwargs,
    ) -> FallerAssessment:
        """
        Compute faller risk score for a candidate.

        Args:
            candidate:                CandidateStock from scanner (price, gap, rvol, float, etc.)
            scored:                   ScoredCandidate from orchestrator (agent_signals, component_scores)
            indicators:               Technical indicator dict from market_data_by_ticker[ticker]
                                      Keys: vwap, rsi_14, rsi_9, macd_line, macd_signal, etc.
            sec_result:               Optional SECFilingResult from D191 SEC prefetcher.
                                      When present, adds deterministic SEC filing signals before
                                      agent-derived signals. None = no SEC data (neutral treatment).
            short_interest_result:    Optional ShortInterestResult from D192 short interest pipeline.
                                      When classification=SQUEEZE, reduces faller score by
                                      cfg.weight_squeeze_faller_reduction (0.30 default) and sets
                                      shorting_blocked_by_squeeze=True on the assessment.
                                      None = no short interest data (neutral treatment).
            sentiment_velocity_result: Optional SentimentVelocityResult from D193 velocity tracker.
                                      VIRAL/BUILDING → reduces faller score (developing story).
                                      ISOLATED/SILENT → increases faller score (no story = promo).
                                      None = no velocity data (neutral treatment).
            order_flow_result:        Optional OrderFlowResult from D194 order flow analyzer.
                                      INSTITUTIONAL_ACCUMULATION → reduces faller score (-0.20).
                                      INSTITUTIONAL_DISTRIBUTION → increases faller score (+0.20).
                                      RETAIL_DOMINATED → slight increase (+0.10, promo risk).
                                      None = no T&S data (neutral treatment).

        Returns:
            FallerAssessment with score, reject flag, position_multiplier,
            squeeze_classification, shorting_blocked_by_squeeze, sentiment_momentum,
            and order_flow_signal.
        """
        cfg = self.cfg

        # ── Extract signals ──────────────────────────────────────────────
        manipulation_prob, has_manipulation_signal = self._extract_manipulation(scored)
        news_is_bearish = self._extract_news_bearish(scored)
        spread_proxy = self._extract_spread_proxy(scored)
        bullish_count, bearish_count = self._count_directional_agents(scored)

        vwap = indicators.get("vwap")
        current_price = candidate.current_price
        rsi = indicators.get("rsi_9") or indicators.get("rsi_14")  # fast RSI preferred
        macd_line = indicators.get("macd_fast_line") or indicators.get("macd_line")
        macd_signal = indicators.get("macd_fast_signal") or indicators.get("macd_signal")
        macd_positive = (
            macd_line is not None
            and macd_signal is not None
            and macd_line > macd_signal
            and macd_line > 0
        )

        # Dollar volume: use avg_daily_volume × current_price as best proxy
        if candidate.avg_daily_volume and candidate.avg_daily_volume > 0:
            dollar_volume = candidate.avg_daily_volume * current_price
        else:
            dollar_volume = 0.0

        # D166 FIX: Use news agent's quality verdict, not raw has_news_catalyst flag.
        # candidate.has_news_catalyst=True whenever ANY headlines exist (even generic
        # listicles with catalyst_type=NONE), causing false bullish credits that tank
        # the faller score below the rejection threshold. Only count as real catalyst
        # if the news agent explicitly rated the signal BULL or STRONG_BULL.
        has_catalyst = self._extract_catalyst_quality(scored, candidate)
        float_unknown = candidate.float_shares is None
        gap_pct = candidate.gap_pct or 0.0

        # ── Start from baseline ──────────────────────────────────────────
        score = cfg.baseline_faller_risk
        bearish_factors: list[tuple[str, float]] = []
        bullish_factors: list[tuple[str, float]] = []

        # ─────────────────────────────────────────────────────────────────
        # BEARISH SIGNALS (increase faller score)
        # ─────────────────────────────────────────────────────────────────

        # 1. High manipulation probability with NO news catalyst
        #    The ARTL/SST pattern: 85%+ manipulation classified + no real catalyst
        #    is the single strongest faller predictor in today's data.
        if manipulation_prob > cfg.manipulation_high_prob_threshold and not has_catalyst:
            w = cfg.weight_high_manipulation_no_catalyst
            score += w
            bearish_factors.append(("high_manip_no_catalyst", w))
        elif manipulation_prob > cfg.manipulation_high_prob_threshold and has_catalyst:
            # Manipulation flagged but HAS catalyst — partial concern (EEIQ pattern)
            partial_w = cfg.weight_high_manipulation_no_catalyst * 0.4
            score += partial_w
            bearish_factors.append(("high_manip_with_catalyst", partial_w))

        # 2. Bearish news — explicit negative framing
        if news_is_bearish:
            w = cfg.weight_bearish_news
            score += w
            bearish_factors.append(("bearish_news", w))

        # 3. Price significantly below VWAP — smart money already distributed
        if vwap and vwap > 0 and current_price > 0:
            pct_below_vwap = (vwap - current_price) / vwap
            if pct_below_vwap > cfg.vwap_below_threshold_pct:
                # Proportional: 15% below = full weight, scales up to 30%+ below
                ratio = min(pct_below_vwap / cfg.vwap_below_threshold_pct, 2.0)
                w = cfg.weight_price_below_vwap * min(ratio, 1.5) / 1.5
                score += w
                bearish_factors.append((f"below_vwap_{pct_below_vwap:.0%}", w))

        # 4. Wide bid-ask spread — market-maker risk premium for illiquid pump
        #    spread_proxy from risk_breakdown["liquidity"]: 0.1=tight, 0.4=1-2%, 0.7=2-3%, 1.0=>3%
        if spread_proxy > 0.5:  # ~2%+ spread territory
            # Proportional: 0.5 proxy = half weight, 1.0 proxy = full weight
            ratio = (spread_proxy - 0.5) / 0.5
            w = cfg.weight_high_spread * ratio
            score += w
            bearish_factors.append((f"wide_spread(proxy={spread_proxy:.1f})", w))

        # 5. Sub-$500K dollar volume — thin, easy to print green then vanish
        #    SST had $138K, ARTL similarly thin. At this level the entire float
        #    can be "distributed" in minutes with no visible tape.
        if dollar_volume < cfg.low_dollar_volume_threshold and dollar_volume > 0:
            # Proportional: $0 → full weight, $500K → zero
            ratio = 1.0 - (dollar_volume / cfg.low_dollar_volume_threshold)
            w = cfg.weight_low_dollar_volume * ratio
            score += w
            bearish_factors.append((f"low_dolvol_${dollar_volume/1e3:.0f}K", w))
        elif dollar_volume == 0:
            # No volume data at all — treat as worst case (illiquid)
            score += cfg.weight_low_dollar_volume
            bearish_factors.append(("no_dolvol_data", cfg.weight_low_dollar_volume))

        # 6. Extreme gap (>100%) with no fundamental catalyst
        #    Pure promotional momentum — gap this extreme is almost always a distribution event.
        if gap_pct > cfg.extreme_gap_threshold and not has_catalyst:
            w = cfg.weight_extreme_gap_no_catalyst
            score += w
            bearish_factors.append((f"extreme_gap_{gap_pct:.0%}_no_cat", w))

        # 7. Unknown float + no SEC data — ARTL pattern: zero fundamental data
        #    Makes position sizing impossible and suggests shell/promo stock.
        if float_unknown:
            # Check if SEC filing data is also absent (scored.agent_signals has filing_summary)
            has_sec_data = self._has_sec_data(scored)
            if not has_sec_data:
                w = cfg.weight_unknown_float
                score += w
                bearish_factors.append(("unknown_float_no_sec", w))
            else:
                # Float unknown but has SEC data — lower concern
                score += cfg.weight_unknown_float * 0.5
                bearish_factors.append(("unknown_float_has_sec", cfg.weight_unknown_float * 0.5))

        # ── D191: SEC filing signals (deterministic, highest-conviction inputs) ──
        # These are applied BEFORE agent signals because they are fact-based:
        # a 424B5 filing is an objective fact, not an LLM interpretation.
        # Weights are configured in FallerDetectionConfig to allow tuning.
        if sec_result is not None:
            from src.data.sec_prefetcher import FilingSignal

            # ATM dilution: hardest possible BEAR — continuous selling into every rally
            if sec_result.atm_detected:
                w = cfg.weight_sec_dilution_atm
                score += w
                bearish_factors.append(("sec_atm_dilution", w))

            # Active dilution: 424B5 today/yesterday — shares being sold into this gap
            elif sec_result.dilution_detected:
                w = cfg.weight_sec_dilution_active
                score += w
                bearish_factors.append(("sec_424b5_dilution", w))

            # Material event: 8-K today — real catalyst confirmed via SEC (not LLM)
            # Reduces faller score because genuine catalysts hold their gaps
            if sec_result.material_event_detected and not sec_result.dilution_detected:
                w = cfg.weight_sec_material_event
                score -= w
                bullish_factors.append(("sec_8k_material_event", w))

        # ── D192: Squeeze archetype signal ─────────────────────────────────────
        # Applied AFTER SEC signals and BEFORE agent signals.
        # A SQUEEZE stock is gapping up due to forced short covering — the gap
        # persistence is structural (mechanics), not promotional. Faller score
        # is reduced because the gap is more likely to continue than to fade.
        # SQUEEZE also blocks the short path (forced covering is directional).
        squeeze_classification_str = "unknown"
        shorting_blocked = False

        if short_interest_result is not None:
            from src.data.short_interest import SqueezeClassification

            si_classification = short_interest_result.classification
            squeeze_classification_str = si_classification.value

            if si_classification == SqueezeClassification.SQUEEZE:
                w = cfg.weight_squeeze_faller_reduction
                score -= w
                bullish_factors.append(("squeeze_forced_covering", w))
                shorting_blocked = True
                logger.info(
                    "D192 SQUEEZE detected for %s: short_float=%.1f%% → "
                    "faller -%.2f, shorting BLOCKED",
                    candidate.ticker,
                    short_interest_result.short_float_pct or 0.0,
                    w,
                )
            elif si_classification == SqueezeClassification.HIGH_SHORT:
                # High short but no gap trigger → caution only, partial credit
                w = cfg.weight_squeeze_faller_reduction * 0.5
                score -= w
                bullish_factors.append(("high_short_float_caution", w))
                # High short with no squeeze trigger still blocks shorting
                # (don't short into potential squeeze setup)
                shorting_blocked = True
                logger.info(
                    "D192 HIGH_SHORT for %s: short_float=%.1f%% → "
                    "faller -%.2f, shorting BLOCKED (squeeze risk)",
                    candidate.ticker,
                    short_interest_result.short_float_pct or 0.0,
                    w,
                )

        # ── D193: Sentiment velocity signal ────────────────────────────────────
        # Applied AFTER SEC and squeeze signals and BEFORE agent signals.
        # Headline velocity is deterministic (pure math) — faster than any LLM
        # agent and immune to hallucination.
        # VIRAL/BUILDING = real developing story → faller score down.
        # ISOLATED/SILENT = no story, likely promotional → faller score up.
        sentiment_momentum_str = "silent"

        if sentiment_velocity_result is not None:
            from src.data.sentiment_velocity import NarrativeMomentum

            momentum = sentiment_velocity_result.momentum
            sentiment_momentum_str = momentum.value

            if momentum == NarrativeMomentum.VIRAL:
                w = cfg.weight_sentiment_velocity
                score -= w
                bullish_factors.append(("sentiment_viral_cascade", w))
                logger.info(
                    "D193 VIRAL for %s: %d headlines, intensity=%.2f → faller -%.2f",
                    candidate.ticker,
                    sentiment_velocity_result.total_headlines,
                    sentiment_velocity_result.intensity,
                    w,
                )
            elif momentum == NarrativeMomentum.BUILDING:
                w = cfg.weight_sentiment_velocity * 0.6
                score -= w
                bullish_factors.append(("sentiment_building_story", w))
                logger.info(
                    "D193 BUILDING for %s: vel=%.2f accel=%.2f → faller -%.2f",
                    candidate.ticker,
                    sentiment_velocity_result.velocity,
                    sentiment_velocity_result.acceleration,
                    w,
                )
            elif momentum == NarrativeMomentum.STEADY:
                w = cfg.weight_sentiment_velocity * 0.2
                score -= w
                bullish_factors.append(("sentiment_steady_coverage", w))
            elif momentum == NarrativeMomentum.ISOLATED:
                w = cfg.weight_sentiment_velocity * 0.4
                score += w
                bearish_factors.append(("sentiment_isolated_mention", w))
                logger.debug("D193 ISOLATED for %s → faller +%.2f", candidate.ticker, w)
            elif momentum == NarrativeMomentum.SILENT:
                w = cfg.weight_sentiment_velocity * 0.6
                score += w
                bearish_factors.append(("sentiment_no_news", w))
                logger.debug("D193 SILENT for %s → faller +%.2f", candidate.ticker, w)

        # ── D194: Order flow signal ─────────────────────────────────────────
        order_flow_signal_str = "insufficient_data"
        order_flow_adj = 0.0
        if order_flow_result is not None:
            from src.data.order_flow import FlowSignal
            order_flow_signal_str = order_flow_result.signal.value
            adj = order_flow_result.faller_adjustment
            order_flow_adj = adj
            w = cfg.weight_order_flow if hasattr(cfg, "weight_order_flow") else 1.0
            adjusted = adj * w
            if adjusted < 0:
                score += adjusted  # negative = bullish reduction
                bullish_factors.append((f"D194_{order_flow_signal_str}", abs(adjusted)))
                logger.debug(
                    "D194 ACCUMULATION for %s: net_flow=%.2f block=%.2f → faller %.2f",
                    candidate.ticker,
                    order_flow_result.net_flow_ratio,
                    order_flow_result.block_ratio,
                    adjusted,
                )
            elif adjusted > 0:
                score += adjusted  # positive = bearish increase
                bearish_factors.append((f"D194_{order_flow_signal_str}", adjusted))
                logger.debug(
                    "D194 %s for %s: net_flow=%.2f → faller +%.2f",
                    order_flow_signal_str,
                    candidate.ticker,
                    order_flow_result.net_flow_ratio,
                    adjusted,
                )

        # 8. Multiple CAUTION/BEAR agents with zero BULL/APPROVE signals
        #    All agents agree it's risky but MFCS still passed because technical=BULL.
        #    This cross-agent alarm pattern predicts fades better than individual signals.
        if bearish_count >= 2 and bullish_count == 0:
            w = cfg.weight_multiple_caution_no_approve
            score += w
            bearish_factors.append((f"all_bearish_{bearish_count}agents_0bull", w))
        elif bearish_count > bullish_count and bearish_count >= 2:
            # Bearish majority but some bullish signals — partial concern
            partial_w = cfg.weight_multiple_caution_no_approve * 0.5
            score += partial_w
            bearish_factors.append((f"bearish_majority_{bearish_count}v{bullish_count}", partial_w))

        # ─────────────────────────────────────────────────────────────────
        # BULLISH SIGNALS (decrease faller score)
        # ─────────────────────────────────────────────────────────────────

        # 1. Real news catalyst — single strongest runner predictor
        #    FDA approval, M&A, earnings beat, legitimate licensing deal all
        #    create sustained buying that absorbs supply and keeps price elevated.
        if has_catalyst:
            w = cfg.weight_real_catalyst
            score -= w
            bullish_factors.append(("real_catalyst", w))

        # 2. Price at or above VWAP — institutional demand confirmed
        #    Buying pressure is sufficient to push through VWAP, meaning smart
        #    money is NOT distributing into the open spike.
        if vwap and vwap > 0 and current_price >= vwap:
            pct_above = (current_price - vwap) / vwap
            # Proportional credit: at VWAP = half weight, +5% above = full weight
            ratio = min(1.0, pct_above / 0.05 + 0.5)
            w = cfg.weight_price_at_vwap * ratio
            score -= w
            bullish_factors.append((f"at_above_vwap_{pct_above:.0%}", w))

        # 3. Tight bid-ask spread — institutional participation, real market
        if spread_proxy < 0.25:  # ~sub-1% spread territory
            w = cfg.weight_low_spread
            score -= w
            bullish_factors.append(("tight_spread", w))

        # 4. High dollar volume — real institutional flow
        if dollar_volume >= cfg.high_dollar_volume_threshold:
            # Extra credit for very high volume ($20M+)
            ratio = min(dollar_volume / cfg.high_dollar_volume_threshold, 3.0) / 3.0
            w = cfg.weight_high_dollar_volume * (0.5 + 0.5 * ratio)
            score -= w
            bullish_factors.append((f"high_dolvol_${dollar_volume/1e6:.1f}M", w))
        elif dollar_volume >= cfg.high_dollar_volume_threshold * 0.4:
            # Partial credit for $2M-$5M range
            ratio = (dollar_volume - cfg.high_dollar_volume_threshold * 0.4) / (cfg.high_dollar_volume_threshold * 0.6)
            w = cfg.weight_high_dollar_volume * 0.5 * ratio
            score -= w
            bullish_factors.append((f"moderate_dolvol_${dollar_volume/1e6:.1f}M", w))

        # 5. RSI in sweet spot (60-80) — momentum without exhaustion
        if rsi is not None and cfg.rsi_sweet_spot_low <= rsi <= cfg.rsi_sweet_spot_high:
            # Center of sweet spot (70) gets full credit; edges get partial
            center = (cfg.rsi_sweet_spot_low + cfg.rsi_sweet_spot_high) / 2
            half_range = (cfg.rsi_sweet_spot_high - cfg.rsi_sweet_spot_low) / 2
            ratio = 1.0 - abs(rsi - center) / half_range
            w = cfg.weight_rsi_sweet_spot * ratio
            score -= w
            bullish_factors.append((f"rsi_sweet_{rsi:.0f}", w))
        elif rsi is not None and rsi > cfg.rsi_sweet_spot_high:
            # RSI overbought (>80) — slight penalty, not major
            overbought_penalty = min((rsi - cfg.rsi_sweet_spot_high) / 20.0, 0.5) * 0.05
            score += overbought_penalty
            bearish_factors.append((f"rsi_overbought_{rsi:.0f}", overbought_penalty))

        # 6. MACD positive and above signal line — momentum accelerating
        if macd_positive:
            w = cfg.weight_macd_positive
            score -= w
            bullish_factors.append(("macd_positive", w))

        # 7. Multiple independent agents bullish — cross-agent consensus
        if bullish_count >= cfg.bullish_consensus_min_agents:
            ratio = min(bullish_count / (cfg.bullish_consensus_min_agents + 1), 1.0)
            w = cfg.weight_agent_consensus_bullish * ratio
            score -= w
            bullish_factors.append((f"agent_consensus_{bullish_count}bull", w))

        # ── D212: Free Data Enrichment Signals ──

        # D212-A: Float rotation exhaustion (from float_rotation_result kwarg)
        _fr_result = kwargs.get("float_rotation_result")
        if _fr_result is not None:
            from src.data.float_rotation import RotationLevel
            if _fr_result.level == RotationLevel.EXHAUSTED:
                w = cfg.weight_float_rotation_exhausted
                score += w
                bearish_factors.append((f"float_exhausted_{_fr_result.rotation_ratio:.1f}x", w))
            elif _fr_result.level == RotationLevel.HYPER_ROTATED:
                w = cfg.weight_float_rotation_exhausted * 0.33
                score += w
                bearish_factors.append((f"float_hyper_{_fr_result.rotation_ratio:.1f}x", w))

        # D212-B: Serial gapper detection (from candidate fields)
        _prior_gaps = getattr(candidate, "prior_gap_count", None)
        if _prior_gaps is not None and _prior_gaps >= 3:
            w = cfg.weight_serial_gapper
            score += w
            bearish_factors.append((f"serial_gapper_{_prior_gaps}_gaps", w))
        elif _prior_gaps is not None and _prior_gaps == 0 and has_catalyst:
            # Fresh gapper with catalyst = bullish signal
            w = cfg.weight_serial_gapper * 0.33
            score -= w
            bullish_factors.append(("fresh_gapper_with_catalyst", w))

        # D212-C: Day-2 runner without catalyst
        _is_day2 = getattr(candidate, "is_day2_runner", False)
        if _is_day2 and not has_catalyst:
            w = cfg.weight_day2_no_catalyst
            score += w
            bearish_factors.append(("day2_runner_no_catalyst", w))
        elif _is_day2 and has_catalyst:
            # Day-2 with catalyst = continuation play, slightly bullish
            w = cfg.weight_day2_no_catalyst * 0.3
            score -= w
            bullish_factors.append(("day2_continuation_catalyst", w))

        # D212-D: VWAP extreme deviation (from vwap_deviation_pct kwarg)
        _vwap_dev = kwargs.get("vwap_deviation_pct")
        if _vwap_dev is not None and abs(_vwap_dev) > 0.10:
            w = cfg.weight_vwap_extreme_deviation
            score += w
            direction = "above" if _vwap_dev > 0 else "below"
            bearish_factors.append((f"vwap_extreme_{direction}_{abs(_vwap_dev)*100:.0f}%", w))

        # ── Clamp to [0, 1] ──
        score = max(0.0, min(1.0, score))

        # ── Determine position action ──
        if score > cfg.reject_threshold:
            reject = True
            position_multiplier = 0.0
        else:
            reject = False
            if score > cfg.reduce_half_threshold:
                position_multiplier = 0.50
            elif score > cfg.reduce_partial_threshold:
                position_multiplier = 0.75
            else:
                position_multiplier = 1.0

        assessment = FallerAssessment(
            ticker=candidate.ticker,
            score=score,
            reject=reject,
            position_multiplier=position_multiplier,
            bearish_factors=bearish_factors,
            bullish_factors=bullish_factors,
            manipulation_prob=manipulation_prob,
            has_catalyst=has_catalyst,
            vwap=vwap,
            current_price=current_price,
            spread_proxy=spread_proxy,
            dollar_volume=dollar_volume,
            rsi=rsi,
            macd_positive=macd_positive,
            bullish_agent_count=bullish_count,
            bearish_agent_count=bearish_count,
            squeeze_classification=squeeze_classification_str,
            shorting_blocked_by_squeeze=shorting_blocked,
            sentiment_momentum=sentiment_momentum_str,
            order_flow_signal=order_flow_signal_str,
            order_flow_adjustment=order_flow_adj,
        )

        self._log_assessment(assessment)
        return assessment

    # ─────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────

    def _extract_manipulation(self, scored: ScoredCandidate) -> tuple[float, bool]:
        """Extract manipulation_probability from agent signals."""
        from src.core.models import ManipulationSignal
        for sig in scored.agent_signals:
            if isinstance(sig, ManipulationSignal):
                return sig.manipulation_probability, True
        return 0.5, False  # Default: uncertain (0.5) when no classifier ran

    def _extract_catalyst_quality(
        self, scored: ScoredCandidate, candidate: "CandidateStock"
    ) -> bool:
        """
        True only when a news agent confirms a REAL (material) catalyst.

        candidate.has_news_catalyst is set by the scanner whenever ANY headlines
        are present in the enrichment payload — including generic listicles, watchlist
        articles, and sector round-ups that carry zero fundamental weight.

        The news agent's signal is the authoritative quality verdict. We require
        BULL or STRONG_BULL (i.e., the agent explicitly judged the catalyst as
        material and directionally positive). NEUTRAL / BEAR / missing = no catalyst.

        Root cause of 2026-04-02 CYCN/AGPU losses: both had has_news_catalyst=True
        from generic headlines, but news agent returned catalyst_type=NONE / signal=NEUTRAL.
        The false -0.20 real_catalyst credit + missed +0.15 extreme_gap_no_catalyst
        penalty collapsed faller scores from ~0.85 to 0.20, blocking rejection.
        """
        for sig in scored.agent_signals:
            agent_id_lower = (sig.agent_id or "").lower()
            if "news" not in agent_id_lower:
                continue
            # D221 Phase F: read from news_features.signal_direction_numeric
            # instead of .signal. Numeric mapping (STRONG_BEAR=-2 ... STRONG_BULL=2)
            # makes `>= 1.0` exactly equivalent to the prior
            # `.signal in ("BULL", "STRONG_BULL")` check. Pattern (a) distillation.
            features = getattr(sig, "news_features", None) or {}
            sig_num = features.get("signal_direction_numeric")
            if sig_num is not None:
                return sig_num >= 1.0
            # Legacy fallback: NewsSignal without news_features populated
            # (shouldn't happen post-Phase F but guards against older serialized
            # signals being replayed). Preserves exact prior behavior.
            if sig.signal in ("BULL", "STRONG_BULL"):
                return True
            # Any other signal (NEUTRAL, CAUTION, BEAR, STRONG_BEAR) = no real catalyst
            return False
        # No news agent ran — fall back to scanner flag only if clearly set AND
        # we have no contradicting evidence (conservative: treat as no catalyst)
        return False

    def _extract_news_bearish(self, scored: ScoredCandidate) -> bool:
        """
        True if any news-related agent returned a BEAR signal.

        Checks both the signal direction AND reasoning text for explicit bearish
        language ('sliding', 'downgrade', 'why is X falling', etc.).
        """
        for sig in scored.agent_signals:
            agent_id_lower = (sig.agent_id or "").lower()
            if "news" not in agent_id_lower:
                continue
            # D221 Phase F: read signal_direction_numeric <= -1.0 instead of
            # `.signal in ("BEAR", "STRONG_BEAR")`. Exact semantic equivalence.
            features = getattr(sig, "news_features", None) or {}
            sig_num = features.get("signal_direction_numeric")
            if sig_num is not None and sig_num <= -1.0:
                return True
            # Legacy fallback for older signals without features populated.
            if sig_num is None and sig.signal in ("BEAR", "STRONG_BEAR"):
                return True
            # Check reasoning text for explicit bearish headlines
            reasoning_lower = (sig.reasoning or "").lower()
            bearish_phrases = [
                "why is", "sliding", "falling", "declines", "downgrade",
                "sell", "bearish", "negative", "drops", "plunges",
            ]
            if any(phrase in reasoning_lower for phrase in bearish_phrases):
                return True
        return False

    def _extract_spread_proxy(self, scored: ScoredCandidate) -> float:
        """
        Infer spread magnitude from RiskSignal.risk_breakdown["liquidity"].

        liquidity breakdown values:
            0.1 → spread < 1%   (tight)
            0.4 → spread 1-2%   (moderate)
            0.7 → spread 2-3%   (wide)
            1.0 → spread > 3%   (very wide / veto territory)
        """
        from src.core.models import RiskSignal
        for sig in scored.agent_signals:
            if isinstance(sig, RiskSignal):
                return sig.risk_breakdown.get("liquidity", 0.4)
        return 0.4  # Default: moderate spread when no risk agent ran

    def _has_sec_data(self, scored: ScoredCandidate) -> bool:
        """True if the manipulation classifier has filing_summary data."""
        from src.core.models import ManipulationSignal
        for sig in scored.agent_signals:
            if isinstance(sig, ManipulationSignal):
                return bool(sig.filing_summary)
        return False

    def _count_directional_agents(self, scored: ScoredCandidate) -> tuple[int, int]:
        """
        Count directional agent signals.

        Returns:
            (bullish_count, bearish_count) — counts of BULL/STRONG_BULL and
            BEAR/STRONG_BEAR signals across all agents, excluding risk/manipulation
            (which have their own dedicated checks).
        """
        from src.core.models import ManipulationSignal, RiskSignal
        bullish = 0
        bearish = 0
        for sig in scored.agent_signals:
            if isinstance(sig, (ManipulationSignal, RiskSignal)):
                continue  # Handled separately
            if sig.signal in ("BULL", "STRONG_BULL"):
                bullish += 1
            elif sig.signal in ("BEAR", "STRONG_BEAR"):
                bearish += 1
        return bullish, bearish

    def _log_assessment(self, a: FallerAssessment) -> None:
        """Log assessment at appropriate level."""
        if a.reject:
            logger.warning(
                "D160 FALLER REJECT %s: score=%.3f > threshold=%.2f | "
                "manip=%.0f%% cat=%s vwap_pos=%s spread=%.2f dolvol=$%.0fK | "
                "top_factors=%s",
                a.ticker, a.score, self.cfg.reject_threshold,
                a.manipulation_prob * 100,
                "Y" if a.has_catalyst else "N",
                "Y" if (a.vwap and a.current_price >= a.vwap) else "N",
                a.spread_proxy,
                a.dollar_volume / 1000,
                " | ".join(a.top_factors[:3]),
            )
        elif a.position_multiplier < 1.0:
            logger.info(
                "D160 FALLER REDUCE %s: score=%.3f → %s position | "
                "top_factors=%s",
                a.ticker, a.score, a.position_action,
                " | ".join(a.top_factors[:3]),
            )
        else:
            logger.info(
                "D160 FALLER PASS %s: score=%.3f → FULL position | "
                "top_factors=%s",
                a.ticker, a.score,
                " | ".join(a.top_factors[:3]),
            )
