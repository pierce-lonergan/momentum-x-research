"""
D216 Tier 2: FinBERT Sentiment Scorer

ProsusAI/finbert on HuggingFace (Apache 2.0, 110M params).
CPU inference in 20-50ms per headline. Provides financial sentiment
scoring independent of the LLM pipeline.

Usage:
    scorer = FinBERTScorer()
    result = scorer.score("FDA approves new drug for rare disease")
    # {"label": "positive", "score": 0.85, "sentiment": 0.85}

    agg = scorer.score_multiple(headlines, half_life_hours=4.0)
    # {"sentiment": 0.72, "label": "positive", "n_scored": 5, "n_positive": 3}
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

import threading

_pipeline = None
_pipeline_lock = threading.Lock()


def _get_pipeline():
    """Lazy-load FinBERT pipeline (first call ~5-10s, subsequent ~0ms).

    2026-05-12 fix: added threading.Lock to prevent the concurrent-import
    race that put _pipeline = "FAILED" on the bot's startup. Today's log
    showed 16 failures with 'cannot import name pipeline from transformers'
    — all clustered at 09:21:00 ET (sub-second window) when multiple
    callers raced into _get_pipeline simultaneously, triggering Python's
    import-machinery confusion. Once one fails, _pipeline is set to
    "FAILED" and FinBERT is permanently disabled for the process.
    The lock serializes the first-load attempt; subsequent calls hit
    the populated _pipeline and return immediately.
    """
    global _pipeline
    if _pipeline is None:
        with _pipeline_lock:
            # Double-check after acquiring lock (another thread may have
            # finished loading while we waited)
            if _pipeline is None:
                try:
                    import os
                    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
                    os.environ["TOKENIZERS_PARALLELISM"] = "false"
                    from transformers import pipeline
                    logger.info("D216: Loading FinBERT model (first load ~10s)...")
                    _pipeline = pipeline(
                        "sentiment-analysis",
                        model="ProsusAI/finbert",
                        device=-1,  # CPU
                    )
                    logger.info("D216: FinBERT loaded successfully")
                except Exception as e:
                    logger.warning("D216: FinBERT failed to load: %s", e)
                    _pipeline = "FAILED"
    return _pipeline if _pipeline != "FAILED" else None


class FinBERTScorer:
    """Financial sentiment scoring via ProsusAI/finbert."""

    def score(self, text: str) -> dict:
        """Score a single text.

        Returns: {"label": "positive"|"negative"|"neutral",
                  "score": float (model confidence),
                  "sentiment": float (-1 to +1)}
        """
        pipe = _get_pipeline()
        if pipe is None:
            return {"label": "neutral", "score": 0.0, "sentiment": 0.0}

        try:
            # Let the pipeline handle tokenization + truncation internally
            # FinBERT max is 512 tokens; pipeline truncates automatically
            result = pipe(text, truncation=True, max_length=512)[0]
            label = result["label"]
            score = result["score"]

            # Convert to -1 to +1 sentiment
            if label == "positive":
                sentiment = score
            elif label == "negative":
                sentiment = -score
            else:
                sentiment = 0.0

            return {"label": label, "score": score, "sentiment": sentiment}
        except Exception as e:
            logger.debug("D216: FinBERT scoring failed: %s", e)
            return {"label": "neutral", "score": 0.0, "sentiment": 0.0}

    def score_multiple(
        self,
        headlines: list[dict],
        half_life_hours: float = 4.0,
        now: datetime | None = None,
    ) -> dict:
        """Score multiple headlines with recency weighting.

        Args:
            headlines: [{headline: str, timestamp: str|datetime, source: str}, ...]
            half_life_hours: Exponential decay half-life for recency weighting
            now: Current time (default: UTC now)

        Returns: {
            "sentiment": float (-1 to +1, recency-weighted aggregate),
            "label": "positive"|"negative"|"neutral" (based on aggregate),
            "n_scored": int,
            "n_positive": int,
            "n_negative": int,
            "n_neutral": int,
            "max_sentiment": float (highest individual score),
            "scores": [{headline, label, score, sentiment, weight}, ...]
        }
        """
        if not headlines:
            return {
                "sentiment": 0.0, "label": "neutral",
                "n_scored": 0, "n_positive": 0, "n_negative": 0, "n_neutral": 0,
                "max_sentiment": 0.0, "scores": [],
            }

        if now is None:
            now = datetime.now(timezone.utc)

        decay_lambda = math.log(2) / (half_life_hours * 3600)
        scored = []
        total_weight = 0.0
        weighted_sentiment = 0.0

        for item in headlines:
            headline_text = item.get("headline", "") if isinstance(item, dict) else str(item)
            if not headline_text or len(headline_text) < 10:
                continue

            result = self.score(headline_text)

            # Compute recency weight
            ts = item.get("timestamp") or item.get("published_at") if isinstance(item, dict) else None
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    ts = now
            elif not isinstance(ts, datetime):
                ts = now

            age_seconds = max(0, (now - ts).total_seconds())
            weight = math.exp(-decay_lambda * age_seconds)

            weighted_sentiment += result["sentiment"] * weight
            total_weight += weight

            scored.append({
                "headline": headline_text[:100],
                "label": result["label"],
                "score": round(result["score"], 3),
                "sentiment": round(result["sentiment"], 3),
                "weight": round(weight, 3),
            })

        # Aggregate
        agg_sentiment = weighted_sentiment / total_weight if total_weight > 0 else 0.0
        n_pos = sum(1 for s in scored if s["label"] == "positive")
        n_neg = sum(1 for s in scored if s["label"] == "negative")
        n_neu = sum(1 for s in scored if s["label"] == "neutral")
        max_sent = max((s["sentiment"] for s in scored), default=0.0)

        if agg_sentiment > 0.15:
            agg_label = "positive"
        elif agg_sentiment < -0.15:
            agg_label = "negative"
        else:
            agg_label = "neutral"

        return {
            "sentiment": round(agg_sentiment, 3),
            "label": agg_label,
            "n_scored": len(scored),
            "n_positive": n_pos,
            "n_negative": n_neg,
            "n_neutral": n_neu,
            "max_sentiment": round(max_sent, 3),
            "scores": scored,
        }
