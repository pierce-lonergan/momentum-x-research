"""
D216 Tier 2: Keyword-Based Catalyst Type Classifier

Deterministic regex classifier that tags headlines by catalyst type.
Runs in <1ms per headline. No external API. Provides the catalyst_type
that FinBERT sentiment alone cannot determine.

Usage:
    classifier = CatalystClassifier()
    catalyst_type, confidence = classifier.classify("FDA approves new drug for XYZ Corp")
    # ("FDA_APPROVAL", 0.90)
"""

from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)


# Catalyst patterns: (regex, catalyst_type, confidence)
_CATALYST_PATTERNS: list[tuple[re.Pattern, str, float]] = [
    # FDA / Biotech
    (re.compile(r"\b(FDA|EMA|PDUFA)\b.*\b(approv|clear|accept|grant|designat)", re.I), "FDA_APPROVAL", 0.90),
    (re.compile(r"\b(breakthrough|orphan|fast.?track|priority.?review)\b.*\b(designat|status|grant)", re.I), "FDA_APPROVAL", 0.80),
    (re.compile(r"\b(phase [23]|pivotal|clinical.?trial)\b.*\b(positive|success|met|exceed|primary.?endpoint)", re.I), "FDA_APPROVAL", 0.75),
    (re.compile(r"\b(FDA|EMA)\b.*\b(reject|refuse|CRL|complete.?response|fail)", re.I), "FDA_REJECTION", 0.85),

    # Earnings
    (re.compile(r"\b(earnings|EPS|revenue)\b.*\b(beat|exceed|surpass|top|above)", re.I), "EARNINGS_BEAT", 0.85),
    (re.compile(r"\b(earnings|EPS|revenue)\b.*\b(miss|below|disappoint|short|fall)", re.I), "EARNINGS_MISS", 0.85),
    (re.compile(r"\b(quarter|Q[1-4]|annual)\b.*\b(result|report|earning)", re.I), "EARNINGS_REPORT", 0.60),

    # M&A
    (re.compile(r"\b(acquir|merger|buyout|takeover|bid)\b", re.I), "M_AND_A", 0.80),
    (re.compile(r"\b(acquisition|acquire[ds]?)\b.*\b(agree|complet|announc|definitive)", re.I), "M_AND_A", 0.85),

    # Offerings / Dilution
    (re.compile(r"\b(offering|secondary|dilut|shelf.?registr|S-?3|424B|prospectus)\b", re.I), "OFFERING", 0.80),
    (re.compile(r"\b(raise|capital.?raise|public.?offer|stock.?sale|at.the.market|ATM)\b", re.I), "OFFERING", 0.70),
    (re.compile(r"\b(convert|warrant|exercise)\b.*\b(note|bond|share)", re.I), "OFFERING", 0.65),

    # Contracts / Deals
    (re.compile(r"\b(contract|deal|agreement|partnership|collaborat|license|licens)\b.*\b(sign|award|win|secur|enter|announc)", re.I), "CONTRACT_WIN", 0.75),
    (re.compile(r"\b(government|military|defense|DOD|NASA)\b.*\b(contract|award)", re.I), "CONTRACT_WIN", 0.80),

    # Analyst
    (re.compile(r"\b(upgrade|initiat|price.?target|rais)\b.*\b(buy|overweight|outperform|strong)", re.I), "ANALYST_UPGRADE", 0.70),
    (re.compile(r"\b(downgrade|cut|lower)\b.*\b(sell|underweight|underperform|hold)", re.I), "ANALYST_DOWNGRADE", 0.70),

    # Insider
    (re.compile(r"\b(insider|CEO|CFO|director)\b.*\b(buy|purchas|acquir)", re.I), "INSIDER_BUY", 0.70),
    (re.compile(r"\b(insider|CEO|CFO|director)\b.*\b(sell|dispos|liquidat)", re.I), "INSIDER_SELL", 0.65),

    # Short squeeze / Reddit
    (re.compile(r"\b(short.?squeeze|gamma.?squeeze|WSB|WallStreetBets|reddit|meme)", re.I), "SHORT_SQUEEZE", 0.60),

    # Legal
    (re.compile(r"\b(lawsuit|litigation|SEC.?investig|fraud|settlement|indict)", re.I), "LITIGATION", 0.70),

    # Product
    (re.compile(r"\b(launch|release|unveil|new.?product|debut)\b", re.I), "PRODUCT_LAUNCH", 0.55),
]

# Promotional / sympathy patterns (no real catalyst)
_PROMOTIONAL_PATTERNS: list[re.Pattern] = [
    re.compile(r"\d+\s*(stock|share|pick|comp)", re.I),
    re.compile(r"(top|best|hot)\s*(stock|pick|mover|gainer)", re.I),
    re.compile(r"(pre.?market|after.?hour|mid.?day)\s*(mover|gainer|buzz|watch)", re.I),
    re.compile(r"(most.?active|biggest.?gainer|trending)", re.I),
    re.compile(r"penny.?stock", re.I),
]


class CatalystClassifier:
    """Deterministic keyword-based catalyst type classifier."""

    def classify(self, headline: str, ticker: str = "") -> tuple[str, float]:
        """Classify a single headline.

        Returns: (catalyst_type, confidence)
          catalyst_type: one of the standard types or "SYMPATHY" or "UNKNOWN"
          confidence: 0.0-1.0
        """
        if not headline or not isinstance(headline, str) or len(headline) < 10:
            return "UNKNOWN", 0.0

        # Check promotional first
        for pattern in _PROMOTIONAL_PATTERNS:
            if pattern.search(headline):
                return "SYMPATHY", 0.30

        # Check catalyst patterns (first match wins, patterns ordered by specificity)
        for pattern, catalyst_type, confidence in _CATALYST_PATTERNS:
            if pattern.search(headline):
                # Boost confidence if ticker is mentioned in headline
                if ticker and ticker.upper() in headline.upper():
                    confidence = min(1.0, confidence + 0.10)
                return catalyst_type, confidence

        return "UNKNOWN", 0.20

    def classify_multiple(
        self, headlines: list[str], ticker: str = ""
    ) -> tuple[str, float]:
        """Classify multiple headlines, return best catalyst.

        Takes the highest-confidence non-UNKNOWN classification.
        If all are UNKNOWN/SYMPATHY, returns the most common.
        """
        if not headlines:
            return "UNKNOWN", 0.0

        results = [self.classify(h, ticker) for h in headlines]

        # Filter out UNKNOWN and SYMPATHY
        real_catalysts = [(ct, conf) for ct, conf in results if ct not in ("UNKNOWN", "SYMPATHY")]

        if real_catalysts:
            # Return highest confidence real catalyst
            best = max(real_catalysts, key=lambda x: x[1])
            return best

        # Check if any sympathy
        sympathies = [r for r in results if r[0] == "SYMPATHY"]
        if sympathies:
            return "SYMPATHY", 0.30

        return "UNKNOWN", 0.10
