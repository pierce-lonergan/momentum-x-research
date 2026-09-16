"""
MOMENTUM-X News/Catalyst Agent

### ARCHITECTURAL CONTEXT
Node ID: agent.news
Graph Link: docs/memory/graph_state.json → "agent.news"

### RESEARCH BASIS
News catalysts are the #1 driver of explosive single-day moves.
MFCS weight: w_catalyst_news = 0.25 (MOMENTUM_LOGIC.md §5, D168 rebalance).
Kirtac & Germano (REF-003): OPT achieves 74.4% accuracy, 3.05 Sharpe on news sentiment.
Prompt signature defined in docs/agents/PROMPT_SIGNATURES.md → NEWS_AGENT.

### CRITICAL INVARIANTS
1. CONFIRMED catalyst required for STRONG_BULL (PROMPT_SIGNATURES constraint).
2. Unverifiable sources cap confidence at 0.3.
3. Analyst upgrades alone cap at BULL/0.6.
4. No identifiable catalyst (no news) → signal MUST be NEUTRAL.
5. D114: Minor catalysts (CORPORATE_UPDATE, SECTOR_CATALYST) cap at BULL/0.65.
6. D169: Headline pre-filter eliminates listicles/roundups before LLM call.
   All-promotional headline set → NEUTRAL immediately (no LLM call).

### D169 ROOT CAUSE FIX
LLM Arena (509 scenarios): news_agent F1=0.000, 73.9% overconfident, 55.7% direction.
Every winning stock had a REAL catalyst. Every losing BULL call was on promotional content.
Root cause: prompt said "ANY identifiable news is a valid catalyst" — too permissive.
Fix: deterministic headline pre-filter removes roundup/listicle noise before the LLM
sees it; rewritten prompt with explicit NOT-A-CATALYST rules and few-shot examples.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from src.agents.base import BaseAgent
from src.core.models import AgentSignal, NewsSignal
from src.data.news_client import NewsItem


# D221 Phase F: 12-dim feature vector for agent-as-feature-distiller pattern.
# Computed from LLM output in parse_response and populated into
# NewsSignal.news_features so consumers can read dense features instead of
# the coarse BULL/NEUTRAL/BEAR verdict.
#
# Consumers migrated from reading .signal to reading .news_features[KEY]:
#   src/core/orchestrator.py            (D91 degraded-mode gate)
#   src/execution/faller_detection.py   (catalyst quality scoring)
#   src/core/scoring.py                 (D26 filter)
#   src/agents/debate_engine.py         (data quality logging)
#
# See docs/research-log/08_overfit_vs_regime_experiment.md for the v2 thesis
# underlying this pattern.

# Catalyst-quality mapping: HIGH-quality catalysts are the ones where the
# cascade's prior LLM-Arena validation showed positive realized returns.
# NONE = 0.0; promotional-category = 0.3; minor = 0.6; major = 1.0.
_CATALYST_QUALITY_SCORES: dict[str, float] = {
    # Major catalysts (LLM-Arena validated as predictive)
    "FDA_APPROVAL": 1.0,
    "M_AND_A": 1.0,
    "EARNINGS_BEAT": 1.0,
    "CONTRACT_WIN": 1.0,
    "LEGAL_WIN": 1.0,
    # Minor catalysts (some evidence, capped in prompt signatures)
    "ANALYST_UPGRADE": 0.6,
    "PRODUCT_LAUNCH": 0.6,
    "REGULATORY": 0.6,
    "SHORT_SQUEEZE": 0.6,
    "MANAGEMENT_CHANGE": 0.6,
    # D114 weak catalysts (capped at BULL/0.65)
    "CORPORATE_UPDATE": 0.3,
    "SECTOR_CATALYST": 0.3,
    # No catalyst identified
    "NONE": 0.0,
}

_HIGH_QUALITY_CATALYSTS = frozenset({
    "FDA_APPROVAL", "M_AND_A", "EARNINGS_BEAT", "CONTRACT_WIN", "LEGAL_WIN",
})

_SIGNAL_TO_NUMERIC: dict[str, float] = {
    "STRONG_BEAR": -2.0,
    "BEAR": -1.0,
    "NEUTRAL": 0.0,
    "BULL": 1.0,
    "STRONG_BULL": 2.0,
}

_SPECIFICITY_SCORES: dict[str, float] = {
    "CONFIRMED": 1.0,
    "RUMORED": 0.5,
    "SPECULATIVE": 0.0,
}


def _safe_float(val, default: float = 0.0) -> float:
    """Safely convert to float, returning default on failure.

    Mirrors deterministic_technical._safe_float / deterministic_risk._safe_float
    -- same class-of-bug defense. `float("not a number" or 0)` crashes because
    `or` short-circuits on truthy strings. Caught by Sunday adversarial sweep
    (test_news_distillation_adversarial.py).
    """
    try:
        if val is None:
            return default
        result = float(val)
        if result != result:  # NaN check
            return default
        return result
    except (ValueError, TypeError):
        return default


def compute_news_features(
    *,
    signal: str,
    confidence: float,
    catalyst_type: str,
    specificity: str,
    sentiment_score: float,
    reasoning: str,
    red_flags: list,
    source_citations: list,
) -> dict[str, float]:
    """Compute the 12-dim dense news feature vector from news_agent outputs.

    Pure function -- no side effects, no I/O. Called from parse_response to
    populate NewsSignal.news_features, and from the parity validation
    harness to verify feature extraction matches on historical records.

    All keys are documented in docs/research-log/08_overfit_vs_regime_experiment.md.
    Value ranges are enforced by clamping or by the source LLM contract.
    All numeric coercions go through _safe_float to honor the news_agent
    "never fails" invariant even under malformed upstream inputs.
    """
    sig_num = _SIGNAL_TO_NUMERIC.get(signal, 0.0)
    cat_quality = _CATALYST_QUALITY_SCORES.get(catalyst_type, 0.0)
    spec_score = _SPECIFICITY_SCORES.get(specificity, 0.0)
    # Clamp in case upstream produced out-of-range values. _safe_float
    # absorbs string-typed bad inputs ("not a number" -> 0.0 instead of crash).
    sent = _safe_float(sentiment_score, 0.0)
    sent = max(-1.0, min(1.0, sent))
    conf = _safe_float(confidence, 0.0)
    conf = max(0.0, min(1.0, conf))

    return {
        "catalyst_quality_score": cat_quality,
        "catalyst_specificity_score": spec_score,
        "sentiment_score": sent,
        "sentiment_intensity": abs(sent),
        "signal_direction_numeric": sig_num,
        "confidence": conf,
        "signal_x_confidence": sig_num * conf,
        "n_source_citations": float(len(source_citations or [])),
        "n_red_flags": float(len(red_flags or [])),
        "reasoning_length_chars": float(len(reasoning or "")),
        "has_specific_catalyst": 1.0 if catalyst_type != "NONE" else 0.0,
        "is_high_quality_catalyst": 1.0 if catalyst_type in _HIGH_QUALITY_CATALYSTS else 0.0,
    }
from src.utils.trade_logger import get_trade_logger

logger = get_trade_logger(__name__)


# ── D169: Headline pre-filter patterns ────────────────────────────────
# Matches generic market roundups and listicle headlines that are NOT
# specific catalysts for any single stock. These are the #1 source of
# false BULL signals: "12 Healthcare Stocks Moving Pre-Market" etc.

_LISTICLE_RE = re.compile(
    r"""
    # Numbered stock lists
    \d+\s+(?:stocks?|shares?|companies|names?)\s+
        (?:to\s+watch|moving|gaining|losing|trending|in\s+focus|worth\s+watching|on\s+the\s+move)
    |
    # Sector roundup lists
    \d+\s+(?:health\s*care|biotech|tech(?:nology)?|energy|financial|cannabis|ev|crypto|
             pharma|mining|oil|gold|silver|semiconductor)\s+stocks?
    |
    # "Top N stocks" patterns
    (?:top|best|biggest|hottest|notable|must[-\s]watch)\s+\d+\s+stocks?
    |
    # Pre-market / after-hours mover roundups
    (?:pre[-\s]?market|after[-\s]?hours?)\s+(?:movers?|gainers?|losers?|buzz|highlights?|action)
    |
    # Generic mover roundups with time qualifier
    (?:today|this\s+week|this\s+month|monday|tuesday|wednesday|thursday|friday|
       morning|midday|afternoon|weekly|daily)['s\s]*
        (?:top|biggest|notable)\s+(?:movers?|gainers?|losers?)
    |
    # "Stocks moving/to watch" generic patterns
    stocks?\s+(?:to\s+watch|on\s+the\s+move|making\s+moves?|in\s+the\s+news\s+today)
    |
    # Market brief / roundup / recap titles
    (?:market\s+)?(?:morning|midday|afternoon|weekly|daily|pre[-\s]?market)\s+
        (?:brief|roundup|recap|update|wrap|summary|movers?)
    |
    # "Hot/trending stocks [qualifier]" — e.g. "Hot Stocks Trending Now", "Trending Stocks Today"
    (?:hot|trending|buzzing)\s+stocks?\s*(?:trending|now|today|this\s+week|to\s+watch)?$
    |
    stocks?\s+(?:trending|on\s+fire|on\s+a\s+tear)\s+(?:today|now|this\s+week)
    |
    # Penny / small-cap alert roundups
    (?:penny\s+stocks?|small[-\s]cap\s+stocks?)\s+(?:to\s+watch|alert|on\s+the\s+move)
    |
    # "Stocks making news" / "stocks in focus"
    stocks?\s+(?:making\s+(?:moves?|headlines?|news)|in\s+(?:focus|the\s+spotlight))
    |
    # "X companies moving on earnings" type roundups
    (?:companies|stocks?)\s+moving\s+(?:on|after|following)\s+(?:earnings?|results?)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Capital letter "words" that are NOT ticker symbols
_NON_TICKER_CAPS = frozenset({
    "FDA", "CEO", "CFO", "CTO", "COO", "SEC", "ETF", "IPO", "SPY", "QQQ",
    "M&A", "AI", "EV", "US", "UK", "EU", "NYSE", "NASDAQ", "OTC", "AM",
    "PM", "EST", "EDT", "ET", "GDP", "CPI", "PPI", "FOMC", "FED", "Q1",
    "Q2", "Q3", "Q4", "YOY", "QOQ", "EPS", "PE", "ATH", "LOD", "HOD",
    "PR", "LLC", "INC", "CORP", "LTD", "NDA", "BLA", "IND", "BTC", "ETH",
})


def _is_promotional_headline(headline: str, ticker: str) -> bool:
    """
    Return True if this headline is a generic market roundup or listicle,
    NOT a specific catalyst for `ticker`.

    D169: The single biggest source of false BULL signals. Deterministic
    check — no LLM involved. Fast and cheap.
    """
    # Pattern match against known roundup/listicle structures
    if _LISTICLE_RE.search(headline):
        return True

    # Multi-ticker list heuristic: if headline contains 3+ distinct tokens
    # that look like ticker symbols (2-5 chars starting with a capital letter,
    # not in stop-words), it's likely a roundup article listing several stocks.
    # Pattern allows alphanumeric tickers (e.g. BRK, TICK1) but starts with A-Z.
    candidate_tickers = re.findall(r'\b([A-Z][A-Z0-9]{1,4})\b', headline)
    other_tickers = [
        t for t in candidate_tickers
        if t not in _NON_TICKER_CAPS and t != ticker.upper()
    ]
    if len(other_tickers) >= 3:
        return True

    return False


def _filter_promotional_headlines(
    news_items: list[NewsItem], ticker: str
) -> list[NewsItem]:
    """
    Remove generic market roundup and listicle headlines.
    Returns only headlines likely to contain specific catalyst information.

    D169: Applied before LLM call. If the result is empty, the caller
    returns NEUTRAL immediately — no LLM call needed.
    """
    kept = []
    for item in news_items:
        if _is_promotional_headline(item.headline, ticker):
            logger.debug(
                "news_agent %s: pre-filter removed promotional headline: %r",
                ticker, item.headline[:120],
            )
        else:
            kept.append(item)
    return kept


class NewsAgent(BaseAgent):
    """
    Catalyst classification and sentiment analysis agent.

    D216: FinBERT and CatalystClassifier cached as class-level singletons
    to avoid re-instantiation on every analyze() call.

    Node ID: agent.news
    Tier: 2 (Qwen3-Coder-480B) — extraction task, completion in 3-11s
    Ref: PROMPT_SIGNATURES.md → NEWS_AGENT
    Ref: REF-003 (Sentiment Trading with LLMs)

    D169: Two-stage pipeline:
    Stage 1 (deterministic, ~0ms): Pre-filter promotional headlines.
              All-promotional → NEUTRAL immediately (no LLM call).
    Stage 2 (LLM, ~5-15s): Full catalyst analysis on specific headlines only.
    """

    @property
    def agent_id(self) -> str:
        return "news_agent"

    @property
    def system_prompt(self) -> str:
        # D169: Rewritten from scratch after LLM Arena forensics.
        # Root cause of F1=0.000 was the old "ANY identifiable news is a valid
        # catalyst" instruction — it trained the model to treat roundup articles
        # and sector noise as catalysts. New prompt is explicit and restrictive.
        return (
            "You are a financial news analyst. Your ONLY job is to determine whether "
            "a specific, material, company-level catalyst exists that explains a stock's "
            "gap-up move. You are NOT looking for general market context.\n\n"

            "WHAT COUNTS AS A REAL CATALYST:\n"
            "- Specific FDA approval, rejection, or clinical trial result naming the drug\n"
            "- Specific earnings report with revenue/EPS numbers that beat or miss estimates\n"
            "- Named M&A announcement with specific acquirer/target and deal terms\n"
            "- Named contract win with counterparty and contract value\n"
            "- Specific regulatory approval or ruling that directly affects this company\n"
            "- Named analyst upgrade/downgrade with firm, prior rating, new rating, price target\n"
            "- Specific product launch, approval, or partnership with concrete details\n\n"

            "WHAT IS NOT A CATALYST — return NEUTRAL immediately:\n"
            "- ANY headline listing multiple stocks ('12 Biotech Stocks Moving', "
            "'Top Movers Today', 'Pre-Market Buzz')\n"
            "- Generic sector strength without a specific event at this company\n"
            "- A stock being mentioned alongside other movers in a roundup article\n"
            "- Price and volume action alone — you already know the stock is gapping\n"
            "- Social media buzz, Reddit mentions, or unverified rumors without a source\n"
            "- Vague 'corporate update' without specific terms, numbers, or named parties\n\n"

            "FEW-SHOT EXAMPLES:\n"
            "REAL: 'XYZ Pharma Receives FDA Approval for Drug ABC in NASH Indication' "
            "→ STRONG_BULL, FDA_APPROVAL, CONFIRMED, conf 0.90\n"
            "REAL: 'ABC Corp Q4 Revenue $120M Beats Estimates by 45%, Raises FY26 Guide' "
            "→ STRONG_BULL, EARNINGS_BEAT, CONFIRMED, conf 0.88\n"
            "REAL: 'DEF Inc. Wins $50M DoD Contract for Satellite Sensor Systems' "
            "→ BULL, CONTRACT_WIN, CONFIRMED, conf 0.75\n"
            "REAL: 'Goldman Sachs Upgrades GHI to Buy from Neutral, PT $28 from $18' "
            "→ BULL, ANALYST_UPGRADE, CONFIRMED, conf 0.55\n"
            "NOT REAL: '12 Healthcare Stocks Moving In Pre-Market Session' "
            "→ NEUTRAL, NONE — listicle, no company-specific event\n"
            "NOT REAL: 'Small Caps Making Big Moves: TICK1, TICK2, XYZ, TICK3' "
            "→ NEUTRAL, NONE — multi-stock roundup\n"
            "NOT REAL: 'Pre-Market Movers: What's Driving Markets Today' "
            "→ NEUTRAL, NONE — generic market roundup\n"
            "NOT REAL: 'Biotech Sector on Fire After FDA Activity This Week' "
            "→ NEUTRAL, NONE — sector commentary, no specific company event\n\n"

            "CATALYST CONFIRMATION MATRIX:\n"
            "- CONFIRMED + MAJOR (FDA, M&A, Earnings beat >30%) → STRONG_BULL eligible\n"
            "- CONFIRMED + SIGNIFICANT (contract, product launch, analyst upgrade) → BULL\n"
            "- CONFIRMED + MINOR (crypto holdings, vague partnership) → BULL, conf ≤ 0.65\n"
            "- RUMORED + MAJOR → BULL max, conf ≤ 0.70\n"
            "- SPECULATIVE + anything → conf ≤ 0.3; minor catalyst → NEUTRAL\n"
            "- No specific catalyst → NEUTRAL, NONE\n\n"

            "CALIBRATION:\n"
            "Base rate: ~52% of gap-up candidates follow through. Confidence 0.50 = no edge. "
            "Confidence above 0.85 is extremely rare. False conviction is 3x costlier than "
            "a missed opportunity — when in doubt, reduce confidence or return NEUTRAL.\n\n"

            "ANTI-HALLUCINATION: Use ONLY the provided headlines and summaries. "
            "Never invent catalysts. If a headline is about multiple stocks, it is NOT "
            "a specific catalyst for this ticker — treat it as NEUTRAL.\n\n"

            "PROCESS: In ≤3 bullet points, identify: (1) Is there a specific press release "
            "or SEC filing naming this company and a material event? (2) What type of "
            "catalyst is it? (3) Is the source credible and the claim confirmed?\n\n"

            "COUNTER-ARGUMENT: State the strongest reason the stock could fade despite "
            "the catalyst. If bearish, state why it might reverse. Include in red_flags.\n\n"

            "Your output MUST be valid JSON with NO additional text.\n\n"

            "CONSTRAINTS:\n"
            "- No specific catalyst → signal MUST be 'NEUTRAL'\n"
            "- 'STRONG_BULL' requires CONFIRMED catalyst of type FDA_APPROVAL, M_AND_A, or EARNINGS_BEAT\n"
            "- Analyst upgrades alone cap at 'BULL' with confidence <= 0.6\n"
            "- Unverifiable / speculative sources cap confidence at 0.3\n"
            "- Cite the specific source and timestamp for every claim"
        )

    def build_user_prompt(self, **kwargs: Any) -> str:
        """
        Build user prompt from ticker and news items.

        Expected kwargs:
            ticker: str
            company_name: str
            news_items: list[NewsItem]  (already pre-filtered by analyze())
            market_cap: float | None
            sector: str
        """
        ticker = kwargs["ticker"]
        company = kwargs.get("company_name", ticker)
        news_items: list[NewsItem] = kwargs.get("news_items", [])
        market_cap = kwargs.get("market_cap")
        sector = kwargs.get("sector", "Unknown")

        # Format news items for the prompt
        news_text = ""
        for i, item in enumerate(news_items[:10]):  # Limit to 10 most recent
            news_text += (
                f"\n[{i+1}] Headline: {item.headline}\n"
                f"    Source: {item.source}\n"
                f"    Published: {item.published_at.isoformat()}\n"
                f"    Summary: {item.summary[:500]}\n"
            )

        if not news_text:
            news_text = "\n[No news found for this ticker in the last 24 hours]\n"

        n_items = len(news_items)
        filter_note = (
            f"NOTE: These {n_items} headline(s) have passed a specificity pre-filter that "
            f"removed generic roundups and multi-stock listicles. "
            f"If the remaining headlines still lack a specific company catalyst, return NEUTRAL.\n\n"
            if n_items > 0
            else ""
        )

        return (
            f"Analyze the following news for {ticker} ({company}).\n"
            f"Sector: {sector}\n"
            f"Market Cap: {'$' + f'{market_cap:,.0f}' if market_cap else 'Unknown'}\n"
            f"\n{filter_note}"
            f"--- NEWS ITEMS ---{news_text}\n"
            f"--- END NEWS ---\n\n"
            f"Provide your analysis as a JSON object with these exact fields:\n"
            f'{{\n'
            f'  "signal": "STRONG_BULL" | "BULL" | "NEUTRAL" | "BEAR" | "STRONG_BEAR",\n'
            f'  "confidence": 0.0 to 1.0,\n'
            f'  "catalyst_type": "FDA_APPROVAL" | "EARNINGS_BEAT" | "M_AND_A" | "CONTRACT_WIN" | '
            f'"LEGAL_WIN" | "MANAGEMENT_CHANGE" | "ANALYST_UPGRADE" | "PRODUCT_LAUNCH" | '
            f'"REGULATORY" | "SHORT_SQUEEZE" | "CORPORATE_UPDATE" | "SECTOR_CATALYST" | "NONE",\n'
            f'  "catalyst_specificity": "CONFIRMED" | "RUMORED" | "SPECULATIVE",\n'
            f'  "sentiment_score": -1.0 to 1.0,\n'
            f'  "key_reasoning": "...",\n'
            f'  "red_flags": ["..."],\n'
            f'  "source_citations": [{{"headline": "...", "source": "...", "timestamp": "..."}}]\n'
            f'}}'
        )

    def parse_response(self, raw: dict, ticker: str) -> NewsSignal:
        """
        Parse LLM JSON response into a typed NewsSignal.

        Enforces PROMPT_SIGNATURES constraints:
        - No catalyst → force NEUTRAL
        - Unverifiable → cap confidence at 0.3
        - Analyst upgrade → cap at BULL/0.6
        """
        signal = raw.get("signal", "NEUTRAL")
        confidence = float(raw.get("confidence") or 0.0)
        catalyst_type = raw.get("catalyst_type", "NONE")
        specificity = raw.get("catalyst_specificity", "SPECULATIVE")

        # ── Enforce invariants from PROMPT_SIGNATURES ──

        # No catalyst → NEUTRAL
        if catalyst_type == "NONE" and signal in ("STRONG_BULL", "BULL"):
            signal = "NEUTRAL"
            confidence = min(confidence, 0.3)

        # STRONG_BULL requires CONFIRMED major catalyst
        if signal == "STRONG_BULL":
            if catalyst_type not in ("FDA_APPROVAL", "M_AND_A", "EARNINGS_BEAT"):
                signal = "BULL"
            if specificity != "CONFIRMED":
                signal = "BULL"
                confidence = min(confidence, 0.7)

        # Analyst upgrade cap
        if catalyst_type == "ANALYST_UPGRADE":
            if signal == "STRONG_BULL":
                signal = "BULL"
            confidence = min(confidence, 0.6)

        # D114: Weaker catalyst types — enough to confirm the gap, capped conviction
        if catalyst_type in ("CORPORATE_UPDATE", "SECTOR_CATALYST"):
            if signal == "STRONG_BULL":
                signal = "BULL"
            confidence = min(confidence, 0.65)

        # Unknown catalyst types: cap like minor catalysts (defensive)
        _KNOWN_TYPES = frozenset({
            "FDA_APPROVAL", "EARNINGS_BEAT", "M_AND_A", "CONTRACT_WIN",
            "LEGAL_WIN", "MANAGEMENT_CHANGE", "ANALYST_UPGRADE",
            "PRODUCT_LAUNCH", "REGULATORY", "SHORT_SQUEEZE",
            "CORPORATE_UPDATE", "SECTOR_CATALYST", "NONE",
        })
        if catalyst_type not in _KNOWN_TYPES:
            if signal == "STRONG_BULL":
                signal = "BULL"
            confidence = min(confidence, 0.65)

        # Speculative sources cap — must come AFTER D114 block so
        # SPECULATIVE + MINOR → NEUTRAL (per prompt matrix line 87)
        if specificity == "SPECULATIVE":
            confidence = min(confidence, 0.3)
            if catalyst_type in ("CORPORATE_UPDATE", "SECTOR_CATALYST"):
                signal = "NEUTRAL"

        # Clamp confidence
        confidence = max(0.0, min(1.0, confidence))

        # D221 Phase F: compute dense feature vector for agent-as-feature
        # pattern. Populated into news_features so downstream consumers can
        # read features instead of the verdict.
        _reasoning_text = raw.get("key_reasoning", "")
        _red_flags = raw.get("red_flags", []) or []
        _source_citations = raw.get("source_citations", []) or []
        _sent_score = float(raw.get("sentiment_score") or 0.0)
        news_features = compute_news_features(
            signal=signal,
            confidence=confidence,
            catalyst_type=catalyst_type,
            specificity=specificity,
            sentiment_score=_sent_score,
            reasoning=_reasoning_text,
            red_flags=_red_flags,
            source_citations=_source_citations,
        )

        return NewsSignal(
            agent_id=self.agent_id,
            ticker=ticker,
            timestamp=datetime.now(timezone.utc),
            signal=signal,
            confidence=confidence,
            reasoning=_reasoning_text,
            key_data={
                "catalyst_type": catalyst_type,
                "catalyst_specificity": specificity,
                "sentiment_score": _sent_score,
            },
            flags=_red_flags,
            sources_used=[
                c.get("source", "") for c in _source_citations
                if isinstance(c, dict)
            ],
            catalyst_type=catalyst_type,
            catalyst_specificity=specificity,
            sentiment_score=_sent_score,
            source_citations=_source_citations,
            news_features=news_features,
        )

    async def analyze(self, ticker: str, **kwargs: Any) -> AgentSignal:
        """
        D169: Two-stage pipeline.

        Stage 1 — Deterministic headline pre-filter (~0ms):
          Remove generic market roundups and listicle headlines.
          If all headlines are promotional → return NEUTRAL immediately.
          No LLM call. Eliminates the #1 false-BULL source.

        Stage 2 — LLM analysis (~5-15s):
          Only runs when Stage 1 finds at least one specific headline.
          Full catalyst classification via parent BaseAgent.analyze().
        """
        news_items: list[NewsItem] = kwargs.get("news_items", [])
        n_original = len(news_items)

        # Stage 1: pre-filter
        filtered = _filter_promotional_headlines(news_items, ticker)
        n_filtered = len(filtered)

        if n_original > 0 and n_filtered == 0:
            # Every headline was promotional noise — no LLM call needed
            logger.info(
                "news_agent %s: all %d headlines promotional → NEUTRAL (no LLM call)",
                ticker, n_original,
            )
            return NewsSignal(
                agent_id=self.agent_id,
                ticker=ticker,
                timestamp=datetime.now(timezone.utc),
                signal="NEUTRAL",
                confidence=0.0,
                reasoning=(
                    f"All {n_original} headline(s) were generic market roundups or "
                    f"multi-stock listicles with no specific catalyst for {ticker}."
                ),
                flags=["D169_PRE_FILTER", "ALL_HEADLINES_PROMOTIONAL"],
                prompt_variant_id=self.prompt_variant_id,
                model_id="pre_filter",
                latency_ms=0.0,
                catalyst_type="NONE",
                catalyst_specificity="SPECULATIVE",
                sentiment_score=0.0,
                source_citations=[],
            )

        if n_filtered < n_original:
            logger.info(
                "news_agent %s: pre-filter removed %d/%d promotional headlines (kept %d specific)",
                ticker, n_original - n_filtered, n_original, n_filtered,
            )

        # Stage 1.5: FinBERT sentiment + keyword catalyst classification (~20-50ms)
        # Runs BEFORE the LLM call. If FinBERT identifies a clear signal and the
        # keyword classifier identifies a catalyst type, we have a fast backstop
        # that works even when the LLM times out.
        # D217: Gated by settings.d216.finbert_enabled kill switch.
        _d216_finbert_signal = None
        _d216_catalyst_type = "NONE"
        _d216_finbert_sentiment = 0.0
        _d217_finbert_enabled = True
        try:
            from config.settings import D216FeatureFlags as _D216FF
            _d217_finbert_enabled = _D216FF().finbert_enabled
        except Exception:
            pass  # Default to enabled if settings unavailable
        if not _d217_finbert_enabled:
            logger.debug("D217: FinBERT disabled via kill switch for %s", ticker)
        elif filtered:
            try:
                from src.agents.finbert_scorer import FinBERTScorer
                from src.agents.catalyst_classifier import CatalystClassifier

                # D216 FIX: Use class-level singletons instead of per-call instantiation
                if not hasattr(NewsAgent, '_finbert_instance'):
                    NewsAgent._finbert_instance = FinBERTScorer()
                    NewsAgent._catalyst_instance = CatalystClassifier()
                _finbert = NewsAgent._finbert_instance
                _catalyst_clf = NewsAgent._catalyst_instance

                # Score headlines with FinBERT
                _fb_headlines = [
                    {"headline": getattr(ni, "headline", str(ni)),
                     "timestamp": getattr(ni, "published_at", None)}
                    for ni in filtered
                ]
                # D217 FIX: FinBERT inference is synchronous torch — MUST run in
                # thread pool to avoid blocking the asyncio event loop. This was the
                # prime suspect for the April 9 ten-hour hang.
                import asyncio as _aio_d217
                _fb_result = await _aio_d217.to_thread(_finbert.score_multiple, _fb_headlines)
                _d216_finbert_sentiment = _fb_result["sentiment"]

                # Classify catalyst type via keywords
                _headline_texts = [getattr(ni, "headline", str(ni)) for ni in filtered]
                # D217: Also wrap for consistency (regex-only, <1ms, but keeps pattern uniform)
                _d216_catalyst_type, _d216_catalyst_conf = await _aio_d217.to_thread(
                    _catalyst_clf.classify_multiple, _headline_texts, ticker,
                )

                # Determine FinBERT signal
                if _fb_result["sentiment"] > 0.30 and _fb_result["n_positive"] >= 1:
                    _d216_finbert_signal = "BULL"
                elif _fb_result["sentiment"] > 0.50 and _fb_result["n_positive"] >= 2:
                    _d216_finbert_signal = "STRONG_BULL"
                elif _fb_result["sentiment"] < -0.30 and _fb_result["n_negative"] >= 1:
                    _d216_finbert_signal = "BEAR"

                logger.info(
                    "D216 FINBERT %s: sentiment=%.2f signal=%s catalyst=%s (n=%d, pos=%d, neg=%d)",
                    ticker, _fb_result["sentiment"],
                    _d216_finbert_signal or "NEUTRAL",
                    _d216_catalyst_type,
                    _fb_result["n_scored"], _fb_result["n_positive"], _fb_result["n_negative"],
                )
            except Exception as _fb_err:
                logger.debug("D216: FinBERT pre-stage failed for %s: %s", ticker, _fb_err)

        # Stage 2: full LLM analysis on filtered headlines
        kwargs["news_items"] = filtered
        # D216: Pass FinBERT results as context for the LLM
        kwargs["_d216_finbert_signal"] = _d216_finbert_signal
        kwargs["_d216_catalyst_type"] = _d216_catalyst_type
        kwargs["_d216_finbert_sentiment"] = _d216_finbert_sentiment

        llm_result = await super().analyze(ticker=ticker, **kwargs)

        # D219: Catalyst quality classification — SHADOW MODE
        # Logs what the adjusted score WOULD be without modifying the live score.
        # After 2-3 sessions of shadow data, verify classification accuracy
        # on 20-30 headlines before flipping to live mode.
        _d219_cat_type = getattr(llm_result, "catalyst_type", None) or _d216_catalyst_type or "UNKNOWN"
        _d219_quality_map = {
            "FDA_APPROVAL": "HIGH", "CLINICAL_TRIAL": "HIGH", "PDUFA": "HIGH",
            "M_AND_A": "HIGH", "ACQUISITION": "HIGH", "MERGER": "HIGH",
            "EARNINGS_BEAT": "MEDIUM", "EARNINGS": "MEDIUM", "GUIDANCE_RAISE": "MEDIUM",
            "CONTRACT_WIN": "MEDIUM", "PARTNERSHIP": "MEDIUM", "PRODUCT_LAUNCH": "MEDIUM",
            "ANALYST_UPGRADE": "MEDIUM",
            "SEC_FILING": "LOW", "SHELF_REGISTRATION": "LOW", "SYMPATHY": "LOW",
            "PROMOTIONAL": "NONE", "UNKNOWN": "NONE", "NONE": "NONE",
        }
        _d219_quality = _d219_quality_map.get(_d219_cat_type.upper(), "NONE") if _d219_cat_type else "NONE"
        _d219_multiplier = {"HIGH": 1.5, "MEDIUM": 1.0, "LOW": 0.7, "NONE": 0.5}.get(_d219_quality, 1.0)
        _d219_orig_conf = getattr(llm_result, "confidence", 0)
        _d219_adj_conf = min(1.0, _d219_orig_conf * _d219_multiplier)
        logger.info(
            "D219 CATALYST_SHADOW: %s | catalyst_type=%s quality=%s | "
            "original_confidence=%.3f multiplier=%.1fx adjusted_would_be=%.3f | "
            "signal=%s finbert=%s",
            ticker, _d219_cat_type, _d219_quality,
            _d219_orig_conf, _d219_multiplier, _d219_adj_conf,
            getattr(llm_result, "signal", "?"), _d216_finbert_signal or "N/A",
        )

        # D216: If LLM returned NEUTRAL but FinBERT found a clear signal,
        # use the FinBERT signal as a backstop (conservative AND logic)
        if (
            llm_result.signal == "NEUTRAL"
            and _d216_finbert_signal in ("BULL", "STRONG_BULL")
            and _d216_catalyst_type not in ("UNKNOWN", "SYMPATHY", "NONE")
        ):
            logger.info(
                "D216 FINBERT BACKSTOP %s: LLM=NEUTRAL but FinBERT=%s catalyst=%s → upgrading to BULL",
                ticker, _d216_finbert_signal, _d216_catalyst_type,
            )
            # Create a new signal with FinBERT data
            return NewsSignal(
                agent_id=self.agent_id,
                ticker=ticker,
                timestamp=datetime.now(timezone.utc),
                signal="BULL",
                confidence=min(0.50, abs(_d216_finbert_sentiment)),
                reasoning=(
                    f"D216 FinBERT backstop: LLM returned NEUTRAL (possibly timeout/fallback) "
                    f"but FinBERT sentiment={_d216_finbert_sentiment:.2f} with "
                    f"catalyst_type={_d216_catalyst_type}. Upgrading to BULL."
                ),
                flags=["D216_FINBERT_BACKSTOP"],
                prompt_variant_id=self.prompt_variant_id,
                model_id="finbert_backstop",
                latency_ms=getattr(llm_result, "latency_ms", 0),
                catalyst_type=_d216_catalyst_type,
                catalyst_specificity="CONFIRMED" if _d216_catalyst_type in (
                    "FDA_APPROVAL", "EARNINGS_BEAT", "M_AND_A", "CONTRACT_WIN",
                ) else "RUMORED",
                sentiment_score=_d216_finbert_sentiment,
                source_citations=[],
            )

        # D216: If LLM succeeded but returned NONE catalyst, inject keyword catalyst
        if (
            hasattr(llm_result, "catalyst_type")
            and llm_result.catalyst_type in ("NONE", None, "")
            and _d216_catalyst_type not in ("UNKNOWN", "SYMPATHY", "NONE")
        ):
            logger.info(
                "D216 CATALYST INJECT %s: LLM catalyst=NONE, keyword=%s → injecting",
                ticker, _d216_catalyst_type,
            )
            return llm_result.model_copy(update={
                "catalyst_type": _d216_catalyst_type,
            })

        return llm_result
