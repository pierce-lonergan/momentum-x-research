# D193: Sentiment Velocity Detection

## Summary

A deterministic signal that measures the **rate and acceleration of news headline accumulation** during premarket. Uses a Hawkes-process inspired model to quantify whether a story is developing (real catalyst) or stalled (promotional).

No LLM required — pure math on headline timestamps.

---

## The Signal Hypothesis

| Pattern | Interpretation | Faller adjustment |
|---------|---------------|-------------------|
| Many headlines, accelerating | Developing story, real catalyst | −0.25 (VIRAL) |
| Moderate headlines, positive trend | Story building | −0.15 (BUILDING) |
| A few headlines, flat | Coverage exists, no momentum | −0.05 (STEADY) |
| Single mention, no follow-on | Likely promotional, no real news | +0.10 (ISOLATED) |
| Zero headlines | No story at all | +0.15 (SILENT) |

The archetypal contrast:
- **BFRG/ELAB**: FDA approval → 10+ follow-on headlines from Reuters, Bloomberg, analysts within 2 hours → VIRAL → real catalyst
- **ITRM**: Single StockTwits/PromoNews mention → no follow-on → ISOLATED/SILENT → promotional pump

---

## Architecture

### Hawkes Process Model

The system tracks headline intensity using:

```
λ(t) = μ + Σᵢ α · wᵢ · exp(−β · (t − tᵢ))
```

Where:
- `μ = 0.1` — baseline arrival rate
- `α = 0.8` — excitation magnitude per event
- `β = 0.5` — decay rate (per hour; half-life ≈ 1.4h)
- `wᵢ` — credibility weight of source i

Each headline excites future headline arrival probability, with exponential decay. A genuine catalyst creates self-reinforcing coverage cascades. A pump lacks the institutional follow-on and the process decays quickly.

### Velocity and Acceleration

**Velocity** (headlines/hour): Slope of a linear trend fit to 15-minute cumulative headline buckets. The bucket approach smooths single spikes while preserving genuine acceleration patterns.

**Acceleration** (change in velocity/hour): Splits the observation window at its midpoint and computes `(v₂ − v₁) / half_window`. Positive = story is gaining momentum; negative = story is fading.

### Source Credibility Tiers

| Tier | Sources | Weight |
|------|---------|--------|
| 1 — Official/Institutional | SEC/EDGAR, FDA, Reuters, Bloomberg, AP | 3.0 |
| 2 — Major Financial Media | CNBC, WSJ, Barron's, FT, MarketWatch, Yahoo Finance | 2.0 |
| 3 — Analyst/Research | Seeking Alpha, Benzinga, TipRanks, Zacks, Motley Fool | 1.5 |
| 4 — General/Retail | Reddit, StockTwits, Twitter/X | 0.5 |
| Default | Unknown | 1.0 |

A single Reuters headline is worth 6× a Reddit post in `weighted_velocity`.

### Novelty Detection

Word-overlap deduplication filters rehash headlines so they don't inflate counts. Uses Jaccard-style overlap after stop-word removal:

```
overlap = |A ∩ B| / max(|A|, |B|)
```

If `overlap ≥ 0.7`, the headline is flagged `is_novel=False` and excluded from velocity, acceleration, and intensity calculations. `total_headlines` still counts all registrations; `unique_headlines` counts only novel ones.

---

## NarrativeMomentum Classification

```
SILENT   → total_headlines == 0
ISOLATED → total_headlines == 1
VIRAL    → intensity >= viral_threshold (5.0)
BUILDING → intensity >= building_threshold (2.0) AND velocity > 0
STEADY   → everything else (2+ headlines, no strong trend)
```

Both thresholds are configurable in `FallerDetectionConfig`.

---

## Integration with Faller Detection

Located at `src/execution/faller_detection.py`, the D193 signal is applied after D191 (SEC) and D192 (short interest) and before agent-consensus signals.

Score deltas applied by `weight_sentiment_velocity` (default 0.25):

| Momentum | Multiplier | Delta |
|----------|-----------|-------|
| VIRAL | −1.0× | −0.25 |
| BUILDING | −0.6× | −0.15 |
| STEADY | −0.2× | −0.05 |
| ISOLATED | +0.4× | +0.10 |
| SILENT | +0.6× | +0.15 |

Set `weight_sentiment_velocity = 0` in config to disable without code changes.

The result is surfaced in `FallerAssessment.sentiment_momentum` for arena replay analysis.

---

## Usage

### Premarket scanning (Phase 0/1)

```python
from src.data.sentiment_velocity import SentimentVelocityTracker

tracker = SentimentVelocityTracker()

# Populate from news API results during pre-fetch
for ticker, news_items in prefetch_cache.items():
    tracker.register_headlines_batch(ticker, [
        {"headline": item.headline, "source": item.source, "timestamp": item.published_at}
        for item in news_items
    ])

# At evaluation time
result = tracker.get_result("BFRG")
print(f"Momentum: {result.momentum.value}")  # "viral"
print(f"Velocity: {result.velocity:.1f} h⁻¹")
print(f"Intensity: {result.intensity:.2f}")
```

### Faller detection integration

```python
assessment = detector.score(
    candidate=candidate,
    scored=scored,
    indicators=indicators,
    sec_result=sec_result,
    short_interest_result=si_result,
    sentiment_velocity_result=tracker.get_result(candidate.ticker),
)
```

---

## Files

| File | Purpose |
|------|---------|
| `src/data/sentiment_velocity.py` | Core tracker, Hawkes model, classification |
| `src/execution/faller_detection.py` | D193 signal block, `sentiment_momentum` field |
| `config/settings.py` | `weight_sentiment_velocity` config field |
| `tests/unit/test_sentiment_velocity.py` | 18 unit tests (100% pass) |

---

## Design Decisions

**Why Hawkes process?** Self-exciting processes model information cascades accurately. Real news creates follow-on coverage; pumps don't. The exponential decay ensures that old coverage doesn't mask a lack of current momentum.

**Why 15-minute buckets?** Matches the natural rhythm of premarket news cycles. Finer granularity is noisy; coarser loses the acceleration signal.

**Why not use LLM?** Speed and determinism. By 4 AM, the tracker has 5+ hours of data and produces an instantaneous signal with no API latency or hallucination risk. LLM catalyst quality assessment (D166) remains as a complementary signal.

**Why dedup by word overlap?** Wires, redistributors, and aggregators republish the same story verbatim. Counting those as independent headlines would inflate velocity falsely. The 70% threshold is permissive enough to pass genuinely different angles on the same story.
