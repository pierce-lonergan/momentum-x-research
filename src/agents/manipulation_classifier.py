"""
MOMENTUM-X Manipulation Lifecycle Classifier Agent (D106 §2C)

### ARCHITECTURAL CONTEXT
Node ID: agent.manipulation_classifier
Graph Link: docs/memory/graph_state.json → "agent.manipulation_classifier"

### PURPOSE
Classifies whether a gap-up candidate is driven by organic momentum
(genuine catalyst) or a promotional pump-and-dump (dilution play).
This is the hardest reasoning task in the pipeline: synthesizing filing
history, catalyst credibility, volume patterns, and prior gap outcomes
into a single phase classification.

### DESIGN DECISIONS
- Tier 1 (Qwen3.5-397B): Requires deep reasoning across multiple
  data sources — harder than news classification.
- Does NOT contribute to MFCS scoring: output is ManipulationSignal
  which gates entry parameters (sizing, stops, targets, hard exit time).
- Same-day 424B5 → always PROMOTIONAL_LATE (hard rule, no LLM needed).
- Extra ~10s latency irrelevant (runs in parallel with other 5 agents).
- Cost delta marginal (~$0.02/candidate).

### CRITICAL INVARIANTS
1. Same-day 424B5 → PROMOTIONAL_LATE (enforced pre-LLM).
2. ManipulationSignal does NOT affect MFCS computation.
3. Getting classification wrong is expensive: false ORGANIC on a pump = direct loss.

Ref: D106 WS2 (Manipulation Detection)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.agents.base import BaseAgent
from src.core.models import AgentSignal, ManipulationSignal
from src.utils.trade_logger import get_trade_logger

logger = get_trade_logger(__name__)


class ManipulationClassifier(BaseAgent):
    """
    Tier 1 LLM-powered manipulation lifecycle classifier.

    Node ID: agent.manipulation_classifier
    Tier: 1 (Qwen3.5-397B) — deep reasoning required
    Ref: D106 WS2 (Manipulation Detection)
    """

    @property
    def agent_id(self) -> str:
        return "manipulation_classifier"

    @property
    def system_prompt(self) -> str:
        return (
            "You are a financial forensics analyst specializing in detecting stock "
            "manipulation and promotional pump-and-dump schemes in small-cap and "
            "micro-cap stocks. You analyze SEC filings, news catalysts, volume "
            "patterns, and float dynamics to classify whether a gap-up move is "
            "driven by genuine organic momentum or a promotional scheme.\n\n"

            "FILING FRAMEWORK:\n"
            "- S-3 (Shelf Registration): Company registered to sell shares. Within "
            "14 days = HIGH dilution risk. Within 90 days = moderate risk.\n"
            "- 424B5 (Prospectus Supplement): Company is ACTIVELY selling shares NOW. "
            "Same-day 424B5 on a gap-up = textbook promotional dump. This is the "
            "strongest red flag.\n"
            "- Form 4 (Insider Transactions): Multiple sells in 30 days = insiders "
            "exiting ahead of dilution.\n"
            "- 8-K (Material Events): Check if the 'catalyst' is a genuine material "
            "event or just a PR fluff piece.\n\n"

            "CATALYST QUALITY MATRIX:\n"
            "- FDA APPROVAL / M&A / EARNINGS BEAT → High quality, likely ORGANIC\n"
            "- 'Strategic partnership' / 'collaboration' / 'LOI' → Low quality, "
            "often PROMOTIONAL cover story\n"
            "- No news at all + gap-up → Very suspicious, likely PROMOTIONAL\n"
            "- Clinical trial data / regulatory milestone → Medium-high quality\n\n"

            "PRIOR GAP ASSESSMENT:\n"
            "- Stocks that gap up repeatedly on vague news and then dump are "
            "serial promotionals (repeat offenders).\n"
            "- Check: is this the first time this stock has gapped, or is there "
            "a pattern of pump-and-dump?\n\n"

            "CLASSIFICATION RULES:\n"
            "1. ORGANIC_MOMENTUM: Confirmed material catalyst + no recent dilution "
            "filings (no S-3 within 14 days, no 424B5 within 30 days). Genuine "
            "institutional interest evidenced by volume patterns.\n"
            "2. PROMOTIONAL_EARLY: Active S-3 shelf within 14 days OR vague/no "
            "catalyst combined with high RVOL. Tradeable with tight parameters "
            "but distribution likely within 1-2 hours.\n"
            "3. PROMOTIONAL_LATE: Same-day 424B5, or clear evidence that "
            "distribution is already in progress. DO NOT trade.\n"
            "4. UNCERTAIN: Insufficient data to determine. Default when evidence "
            "is mixed or data sources are incomplete.\n\n"

            "HARD RULE: If has_424b5_same_day is true, you MUST classify as "
            "PROMOTIONAL_LATE regardless of all other evidence.\n\n"

            "ANTI-HALLUCINATION: Use ONLY the provided data. Never invent "
            "filing dates, insider names, or news details. If a field is empty "
            "or missing, note it as 'data unavailable' in your reasoning.\n\n"

            "ASYMMETRIC PENALTY: Classifying a promotional as ORGANIC is 5x more "
            "costly than classifying an organic as PROMOTIONAL. When uncertain, "
            "lean toward PROMOTIONAL_EARLY or UNCERTAIN.\n\n"

            "Your output MUST be valid JSON with NO additional text.\n\n"

            "Required output fields:\n"
            "- phase: one of ORGANIC_MOMENTUM, PROMOTIONAL_EARLY, PROMOTIONAL_LATE, UNCERTAIN\n"
            "- signal: BULL for ORGANIC, BEAR for PROMOTIONAL_LATE, NEUTRAL otherwise\n"
            "- confidence: 0.0 to 1.0\n"
            "- manipulation_probability: 0.0 to 1.0 (probability this is a promotional scheme)\n"
            "- estimated_remaining_upside_minutes: int (0 = unknown or imminent dump)\n"
            "- key_evidence: list of strings (evidence supporting classification)\n"
            "- red_flags: list of strings (warning signs observed)\n"
            "- reasoning: string (2-3 sentence explanation)"
        )

    def build_user_prompt(self, **kwargs: Any) -> str:
        """
        Build user prompt from ticker and enrichment data.

        Expected kwargs:
            ticker: str
            news_items: list[dict]  — news headlines/summaries
            rvol: float  — relative volume
            gap_pct: float  — gap percentage (decimal, e.g. 0.15 = 15%)
            premarket_volume: int
            float_shares: int | None
            filing_summary: dict  — from build_manipulation_filing_summary()
            sec_filings: list[dict]  — raw filing details
        """
        ticker = kwargs.get("ticker", "UNKNOWN")
        news_items = kwargs.get("news_items", [])
        rvol = kwargs.get("rvol", 0.0)
        gap_pct = kwargs.get("gap_pct", 0.0)
        premarket_volume = kwargs.get("premarket_volume", 0)
        float_shares = kwargs.get("float_shares")
        filing_summary = kwargs.get("filing_summary", {})
        sec_filings = kwargs.get("sec_filings", [])

        # Format news items
        news_block = "No news available"
        if news_items:
            news_lines = []
            for item in news_items[:10]:  # Cap at 10
                if isinstance(item, dict):
                    headline = item.get("headline", item.get("title", str(item)))
                    source = item.get("source", "unknown")
                    news_lines.append(f"  - [{source}] {headline}")
                else:
                    news_lines.append(f"  - {str(item)[:200]}")
            news_block = "\n".join(news_lines)

        # Format filing summary
        fs = filing_summary
        filing_block = (
            f"  S-3 age (days): {fs.get('s3_age_days', 'N/A')}\n"
            f"  Same-day 424B5: {fs.get('has_424b5_same_day', False)}\n"
            f"  Insider sells (30d): {fs.get('insider_sell_count_30d', 0)}\n"
            f"  Dilution filings: {fs.get('dilution_filing_count', 0)}\n"
            f"  Recent 8-K count: {fs.get('recent_8k_count', 0)}"
        )

        # Format raw filings
        filings_block = "No filings available"
        if sec_filings:
            filing_lines = []
            for f in sec_filings[:15]:  # Cap at 15
                if isinstance(f, dict):
                    form = f.get("form_type", "?")
                    filed = f.get("filed_date", "?")
                    desc = f.get("description", "")[:100]
                    filing_lines.append(f"  - {form} filed {filed}: {desc}")
            if filing_lines:
                filings_block = "\n".join(filing_lines)

        return (
            f"Analyze {ticker} for manipulation risk:\n\n"
            f"MARKET DATA:\n"
            f"  Gap: {gap_pct * 100:.1f}%\n"
            f"  RVOL: {rvol:.1f}x\n"
            f"  Premarket volume: {premarket_volume:,}\n"
            f"  Float: {f'{float_shares:,}' if float_shares else 'Unknown'}\n\n"
            f"NEWS:\n{news_block}\n\n"
            f"SEC FILING SUMMARY:\n{filing_block}\n\n"
            f"RAW FILINGS:\n{filings_block}\n\n"
            f"Classify the manipulation lifecycle phase and provide your analysis."
        )

    def parse_response(self, raw: dict, ticker: str) -> ManipulationSignal:
        """
        Parse LLM JSON response into ManipulationSignal.

        Handles missing/malformed fields gracefully with safe defaults.
        """
        # Extract fields with safe defaults
        phase = raw.get("phase", "UNCERTAIN")
        signal = raw.get("signal", "NEUTRAL")
        confidence = raw.get("confidence", 0.5)
        manipulation_probability = raw.get("manipulation_probability", 0.5)
        remaining_mins = raw.get("estimated_remaining_upside_minutes", 0)
        key_evidence = raw.get("key_evidence", [])
        red_flags = raw.get("red_flags", [])
        reasoning = raw.get("reasoning", "")

        # Ensure lists
        if not isinstance(key_evidence, list):
            key_evidence = [str(key_evidence)] if key_evidence else []
        if not isinstance(red_flags, list):
            red_flags = [str(red_flags)] if red_flags else []

        # Clamp numeric values
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 0.5
        try:
            manipulation_probability = max(0.0, min(1.0, float(manipulation_probability)))
        except (TypeError, ValueError):
            manipulation_probability = 0.5
        try:
            remaining_mins = max(0, int(remaining_mins))
        except (TypeError, ValueError):
            remaining_mins = 0

        return ManipulationSignal(
            agent_id=self.agent_id,
            ticker=ticker,
            timestamp=datetime.now(timezone.utc),
            signal=signal,
            confidence=confidence,
            reasoning=str(reasoning)[:500] if reasoning else "No reasoning provided",
            key_data={
                "phase": phase,
                "manipulation_probability": manipulation_probability,
            },
            phase=phase,
            manipulation_probability=manipulation_probability,
            estimated_remaining_upside_minutes=remaining_mins,
            key_evidence=[str(e)[:200] for e in key_evidence[:10]],
            red_flags=[str(f)[:200] for f in red_flags[:10]],
        )
