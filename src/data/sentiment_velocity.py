"""D193: Sentiment Velocity Detection.

Tracks the rate and acceleration of news headline accumulation during premarket.
Uses a Hawkes-process inspired model where headline events are self-exciting.

Velocity     = d(headline_count)/dt      — is news accelerating?
Acceleration = d²(headline_count)/dt²    — is the rate of acceleration increasing?

Stocks with high velocity + acceleration = developing story = likely real catalyst
Stocks with zero velocity                = isolated event or no news = likely promotional

DETERMINISTIC signal — pure math on headline timestamps, no LLM needed.

Design notes:
  - 15-minute buckets capture intraday rhythm (premarket ≈ 0–6 buckets by open)
  - Hawkes intensity λ(t) = μ + Σ α·exp(−β·(t − tᵢ)) decays old headlines
  - Source credibility weights bias toward institutional/official sources
  - Novelty detection (word-overlap) filters rehash headlines from counts

Ref: Trading Bot Architecture §Hawkes process model, D193
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Enums & Dataclasses
# ═══════════════════════════════════════════════════════════════════

class NarrativeMomentum(str, Enum):
    VIRAL    = "viral"     # Explosive, self-exciting cascade (intensity ≥ viral_threshold)
    BUILDING = "building"  # Developing story (intensity ≥ building_threshold, velocity > 0)
    STEADY   = "steady"    # Some coverage but not accelerating (2–4 headlines, flat)
    ISOLATED = "isolated"  # Single mention, no developing story (1 headline)
    SILENT   = "silent"    # No headlines at all


@dataclass
class HeadlineEvent:
    """A single headline observation."""
    timestamp: datetime
    source: str = ""
    headline_text: str = ""
    credibility_weight: float = 1.0  # Higher for Reuters/SEC/Bloomberg
    is_novel: bool = True            # False when flagged as rehash of earlier headline


@dataclass
class SentimentVelocityResult:
    """Complete velocity analysis for one ticker."""
    ticker: str

    # Raw counts
    total_headlines: int = 0
    unique_headlines: int = 0  # After novelty dedup

    # Time series
    events: list[HeadlineEvent] = field(default_factory=list)

    # Velocity metrics (the key signals)
    velocity: float = 0.0           # Headlines per hour (first derivative)
    acceleration: float = 0.0       # Change in velocity per hour (second derivative)
    intensity: float = 0.0          # Hawkes-process intensity estimate at latest observation
    weighted_velocity: float = 0.0  # Velocity weighted by source credibility

    # Classification
    momentum: NarrativeMomentum = NarrativeMomentum.SILENT

    # Derived flags
    narrative_is_building: bool = False  # velocity > 0 AND acceleration > 0
    narrative_is_viral: bool = False     # intensity >= viral_threshold
    is_rehash_only: bool = False         # All registered headlines are duplicates

    # Timing
    first_headline_time: Optional[datetime] = None
    latest_headline_time: Optional[datetime] = None
    observation_window_minutes: float = 0.0


# ═══════════════════════════════════════════════════════════════════
# Source credibility weights
# ═══════════════════════════════════════════════════════════════════

SOURCE_CREDIBILITY: dict[str, float] = {
    # Tier 1: Official / Institutional (3.0)
    "sec.gov": 3.0,
    "sec":     3.0,
    "edgar":   3.0,
    "fda.gov": 3.0,
    "reuters": 3.0,
    "bloomberg": 3.0,
    "associated press": 3.0,
    "ap news": 3.0,
    # Tier 2: Major Financial Media (2.0)
    "cnbc":            2.0,
    "wsj":             2.0,
    "wall street journal": 2.0,
    "barrons":         2.0,
    "financial times": 2.0,
    "marketwatch":     2.0,
    "yahoo finance":   2.0,
    # Tier 3: Analyst / Research (1.5)
    "seeking alpha":   1.5,
    "benzinga":        1.5,
    "tipranks":        1.5,
    "zacks":           1.5,
    "motley fool":     1.5,
    "investor place":  1.5,
    # Tier 4: General / Retail (0.5)
    "reddit":     0.5,
    "stocktwits": 0.5,
    "twitter":    0.5,
    "x.com":      0.5,
}


# ═══════════════════════════════════════════════════════════════════
# Tracker
# ═══════════════════════════════════════════════════════════════════

class SentimentVelocityTracker:
    """
    Tracks headline velocity and acceleration during premarket.

    Usage:
        tracker = SentimentVelocityTracker()

        # Populate from premarket scan (Phase 0/1)
        tracker.register_headlines_batch("AAPL", [
            {"headline": "Apple announces record earnings", "source": "Reuters",
             "timestamp": datetime(2026, 4, 3, 5, 0)},
            ...
        ])

        # Score at evaluation time
        result = tracker.get_result("AAPL")
        score_delta = tracker.get_signal_for_faller("AAPL")   # e.g. -0.15
    """

    def __init__(
        self,
        viral_threshold: float = 5.0,
        building_threshold: float = 2.0,
        hawkes_alpha: float = 0.8,   # Magnitude of excitation per event
        hawkes_beta: float = 0.5,    # Decay rate (per hour)
        novelty_similarity_threshold: float = 0.7,
        bucket_minutes: int = 15,
    ) -> None:
        self._viral_threshold    = viral_threshold
        self._building_threshold = building_threshold
        self._hawkes_alpha       = hawkes_alpha
        self._hawkes_beta        = hawkes_beta
        self._novelty_threshold  = novelty_similarity_threshold
        self._bucket_minutes     = bucket_minutes
        self._trackers: dict[str, SentimentVelocityResult] = {}

    # ─────────────────────────────────────────────────────────────
    # Public API — registration
    # ─────────────────────────────────────────────────────────────

    def register_headline(
        self,
        ticker: str,
        headline: str,
        source: str = "",
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Register a single headline observation for a ticker."""
        if timestamp is None:
            timestamp = datetime.utcnow()

        ticker = ticker.upper()
        if ticker not in self._trackers:
            self._trackers[ticker] = SentimentVelocityResult(ticker=ticker)

        result = self._trackers[ticker]
        result.total_headlines += 1

        credibility = self._get_credibility_weight(source)
        is_novel = self._compute_novelty(headline, result.events)

        event = HeadlineEvent(
            timestamp=timestamp,
            source=source,
            headline_text=headline,
            credibility_weight=credibility,
            is_novel=is_novel,
        )
        result.events.append(event)

        if is_novel:
            result.unique_headlines += 1

        # Update timing bounds
        if result.first_headline_time is None or timestamp < result.first_headline_time:
            result.first_headline_time = timestamp
        if result.latest_headline_time is None or timestamp > result.latest_headline_time:
            result.latest_headline_time = timestamp

        # Recompute derived metrics after each registration
        self._recompute(ticker)

        # D221 Phase F: forward-only persistence (no-op when env-toggle is off).
        # Captures the post-recompute state so v3 training has historical
        # sentiment-velocity series for tickers we observed.
        from src.data._feature_persistence import persist_feature_row
        r = self._trackers[ticker]
        persist_feature_row("sentiment_velocity", {
            "ticker": ticker,
            "timestamp": datetime.utcnow().isoformat(),
            "total_headlines": r.total_headlines,
            "unique_headlines": r.unique_headlines,
            "velocity": r.velocity,
            "acceleration": r.acceleration,
            "intensity": r.intensity,
            "weighted_velocity": r.weighted_velocity,
            "first_headline_time": r.first_headline_time.isoformat() if r.first_headline_time else None,
            "latest_headline_time": r.latest_headline_time.isoformat() if r.latest_headline_time else None,
            "trigger_event_source": event.source,
            "trigger_event_is_novel": event.is_novel,
        })

    def register_headlines_batch(self, ticker: str, headlines: list[dict]) -> None:
        """
        Register multiple headlines at once (e.g., from premarket scan results).

        Each dict should contain:
            headline  (str)      — required
            source    (str)      — optional, default ""
            timestamp (datetime) — optional, default utcnow()
        """
        for item in headlines:
            self.register_headline(
                ticker=ticker,
                headline=item.get("headline", ""),
                source=item.get("source", ""),
                timestamp=item.get("timestamp"),
            )

    # ─────────────────────────────────────────────────────────────
    # Public API — scoring
    # ─────────────────────────────────────────────────────────────

    def get_result(self, ticker: str) -> SentimentVelocityResult:
        """Get the complete velocity analysis for a ticker."""
        ticker = ticker.upper()
        if ticker not in self._trackers:
            return SentimentVelocityResult(ticker=ticker)
        return self._trackers[ticker]

    def get_signal_for_faller(self, ticker: str) -> float:
        """
        Return a faller-score adjustment based on narrative momentum.

        VIRAL/BUILDING → negative delta (developing story = real catalyst, score down)
        ISOLATED/SILENT → positive delta (no story = likely promotional, score up)

        Returns:
            float in [-0.25, +0.15] to be added to the faller risk score.
        """
        momentum = self.classify_momentum(ticker)
        if momentum == NarrativeMomentum.VIRAL:
            return -0.25
        elif momentum == NarrativeMomentum.BUILDING:
            return -0.15
        elif momentum == NarrativeMomentum.STEADY:
            return -0.05
        elif momentum == NarrativeMomentum.ISOLATED:
            return +0.10
        else:  # SILENT
            return +0.15

    def classify_momentum(self, ticker: str) -> NarrativeMomentum:
        """Classify the narrative momentum for a ticker."""
        ticker = ticker.upper()
        result = self._trackers.get(ticker)
        if not result or result.total_headlines == 0:
            return NarrativeMomentum.SILENT
        if result.total_headlines == 1:
            return NarrativeMomentum.ISOLATED
        if result.intensity >= self._viral_threshold:
            return NarrativeMomentum.VIRAL
        if result.intensity >= self._building_threshold and result.velocity > 0:
            return NarrativeMomentum.BUILDING
        return NarrativeMomentum.STEADY

    # ─────────────────────────────────────────────────────────────
    # Metric computation
    # ─────────────────────────────────────────────────────────────

    def compute_velocity(self, ticker: str) -> float:
        """
        Compute headline velocity (unique headlines per hour).

        Splits the observation window into 15-minute buckets and fits a
        linear trend to the cumulative count curve.  The slope of that
        trend line is the velocity in headlines/hour.

        Falls back to a simple first-difference when < 2 buckets are
        populated (e.g., very short observation windows).
        """
        ticker = ticker.upper()
        result = self._trackers.get(ticker)
        if not result or not result.events:
            return 0.0

        novel_events = [e for e in result.events if e.is_novel]
        if not novel_events:
            return 0.0

        # Determine window span
        t_min = min(e.timestamp for e in novel_events)
        t_max = max(e.timestamp for e in novel_events)
        window_hours = (t_max - t_min).total_seconds() / 3600.0

        if window_hours < 1e-6:
            # All headlines arrived simultaneously — treat as spike, not velocity
            return 0.0

        # Build 15-min bucket counts
        bucket_secs = self._bucket_minutes * 60
        n_buckets = max(1, int(math.ceil(
            (t_max - t_min).total_seconds() / bucket_secs
        )))

        counts = [0] * n_buckets
        for e in novel_events:
            idx = int((e.timestamp - t_min).total_seconds() / bucket_secs)
            idx = min(idx, n_buckets - 1)
            counts[idx] += 1

        # Cumulative sums
        cum = []
        running = 0
        for c in counts:
            running += c
            cum.append(running)

        if n_buckets == 1:
            # Only one bucket: slope = count / window
            return len(novel_events) / window_hours

        # Linear regression on (bucket_index, cumulative_count)
        n = n_buckets
        x_mean = (n - 1) / 2.0
        y_mean = sum(cum) / n
        numerator   = sum((i - x_mean) * (cum[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        if denominator < 1e-9:
            return 0.0

        # slope in units of (headlines / bucket)
        slope_per_bucket = numerator / denominator
        # Convert to headlines / hour
        buckets_per_hour = 60.0 / self._bucket_minutes
        return slope_per_bucket * buckets_per_hour

    def compute_acceleration(self, ticker: str) -> float:
        """
        Compute headline acceleration (change in velocity per hour).

        Splits the observation window in half and compares velocity in
        the first half versus the second half.

            acceleration = (v_second_half - v_first_half) / (window_hours / 2)

        Positive = story is gaining momentum.
        Negative = story is dying down.
        """
        ticker = ticker.upper()
        result = self._trackers.get(ticker)
        if not result or not result.events:
            return 0.0

        novel_events = sorted(
            [e for e in result.events if e.is_novel],
            key=lambda e: e.timestamp,
        )
        if len(novel_events) < 2:
            return 0.0

        t_min = novel_events[0].timestamp
        t_max = novel_events[-1].timestamp
        window_hours = (t_max - t_min).total_seconds() / 3600.0
        if window_hours < 1e-6:
            return 0.0

        midpoint = t_min + timedelta(hours=window_hours / 2.0)
        half_hours = window_hours / 2.0

        first_half  = [e for e in novel_events if e.timestamp <= midpoint]
        second_half = [e for e in novel_events if e.timestamp >  midpoint]

        v1 = len(first_half)  / half_hours if half_hours > 0 else 0.0
        v2 = len(second_half) / half_hours if half_hours > 0 else 0.0

        return (v2 - v1) / half_hours

    def compute_hawkes_intensity(
        self, ticker: str, now: Optional[datetime] = None
    ) -> float:
        """
        Compute Hawkes process intensity at time `now`.

        λ(t) = μ + Σᵢ α · credibility_weight_i · exp(−β · (t − tᵢ))

        Each past headline contributes excitation proportional to its
        credibility, decaying exponentially with age. Only novel headlines
        are counted (rehashes don't add new information energy).

        Args:
            ticker: The stock ticker (case-insensitive).
            now:    Reference time for intensity calculation. Defaults to utcnow().

        Returns:
            float ≥ 0. Baseline μ = 0.1 even with zero headlines.
        """
        ticker = ticker.upper()
        result = self._trackers.get(ticker)
        if not result or not result.events:
            return 0.1  # Baseline arrival rate

        now = now or datetime.utcnow()
        mu = 0.1
        intensity = mu

        for event in result.events:
            if not event.is_novel:
                continue
            dt_hours = (now - event.timestamp).total_seconds() / 3600.0
            if dt_hours < 0:
                continue  # Future-dated events (shouldn't happen, but be safe)
            intensity += (
                self._hawkes_alpha
                * event.credibility_weight
                * math.exp(-self._hawkes_beta * dt_hours)
            )

        return intensity

    def compute_weighted_velocity(self, ticker: str) -> float:
        """
        Compute velocity weighted by source credibility.

        Higher-credibility sources are worth more — Reuters ≠ Reddit.
        Returns weighted headline-equivalents per hour.
        """
        ticker = ticker.upper()
        result = self._trackers.get(ticker)
        if not result or not result.events:
            return 0.0

        novel_events = [e for e in result.events if e.is_novel]
        if not novel_events:
            return 0.0

        t_min = min(e.timestamp for e in novel_events)
        t_max = max(e.timestamp for e in novel_events)
        window_hours = (t_max - t_min).total_seconds() / 3600.0

        if window_hours < 1e-6:
            return 0.0

        total_weight = sum(e.credibility_weight for e in novel_events)
        return total_weight / window_hours

    # ─────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────

    def _recompute(self, ticker: str) -> None:
        """Recompute all derived metrics after a new event is added."""
        result = self._trackers[ticker]

        # Window span
        if result.first_headline_time and result.latest_headline_time:
            result.observation_window_minutes = (
                result.latest_headline_time - result.first_headline_time
            ).total_seconds() / 60.0

        # Now-reference: use latest headline time so intensity is stable
        # (doesn't decay as calendar time advances during tests)
        now = result.latest_headline_time or datetime.utcnow()

        result.velocity          = self.compute_velocity(ticker)
        result.acceleration      = self.compute_acceleration(ticker)
        result.intensity         = self.compute_hawkes_intensity(ticker, now=now)
        result.weighted_velocity = self.compute_weighted_velocity(ticker)
        result.momentum          = self.classify_momentum(ticker)

        result.narrative_is_building = result.velocity > 0 and result.acceleration > 0
        result.narrative_is_viral    = result.intensity >= self._viral_threshold

        novel_count = sum(1 for e in result.events if e.is_novel)
        result.is_rehash_only = result.total_headlines > 0 and novel_count == 0

        logger.debug(
            "D193 velocity recomputed for %s: headlines=%d unique=%d "
            "vel=%.2f accel=%.2f intensity=%.2f momentum=%s",
            ticker,
            result.total_headlines,
            result.unique_headlines,
            result.velocity,
            result.acceleration,
            result.intensity,
            result.momentum.value,
        )

    def _compute_novelty(self, headline: str, existing: list[HeadlineEvent]) -> bool:
        """
        Word-overlap novelty detection.

        Returns True if the headline is novel (overlap below threshold with
        all prior headlines). False if it's a rehash of an existing headline.

        Uses Jaccard-style overlap: |A ∩ B| / max(|A|, |B|)
        Stop words (a, the, is, ...) are stripped to reduce noise.
        """
        if not existing:
            return True

        _STOP = {
            "a", "an", "the", "is", "are", "was", "were", "in", "of", "to",
            "and", "or", "for", "on", "at", "by", "with", "from", "its",
            "it", "be", "as", "up", "has", "had", "he", "she", "they",
        }
        new_words = {w for w in headline.lower().split() if w not in _STOP}
        if not new_words:
            return True

        for event in existing:
            existing_words = {
                w for w in event.headline_text.lower().split() if w not in _STOP
            }
            if not existing_words:
                continue
            overlap = len(new_words & existing_words) / max(len(new_words), len(existing_words))
            if overlap >= self._novelty_threshold:
                return False

        return True

    def _get_credibility_weight(self, source: str) -> float:
        """Return credibility weight for a news source string."""
        source_lower = source.lower()
        for key, weight in SOURCE_CREDIBILITY.items():
            if key in source_lower:
                return weight
        return 1.0  # Default: unknown source
