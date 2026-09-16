"""
MOMENTUM-X Fundamental Agent

### ARCHITECTURAL CONTEXT
Node ID: agent.fundamental
Graph Link: docs/memory/graph_state.json → "agent.fundamental"

### RESEARCH BASIS
Float structure is MFCS weight w_float_structure = 0.15 (MOMENTUM_LOGIC.md §5).
Low-float stocks (<20M shares) show 3-5x higher probability of +20% single-day moves.
Asset tradability and fractionability must be verified (CONSTRAINT-009, DATA-001-EXT §7.2).

### CRITICAL INVARIANTS
1. Float > 50M shares caps at NEUTRAL (low probability of explosive move).
2. Recent dilution filing (S-3/424B5) triggers BEAR with red flag.
3. Short interest > 20% of float supports BULL (squeeze potential).
4. Must verify asset is tradable/active via Alpaca asset check.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.agents.base import BaseAgent
from src.core.models import AgentSignal
from src.utils.trade_logger import get_trade_logger

logger = get_trade_logger(__name__)


class FundamentalAgent(BaseAgent):
    """
    Float structure, short interest, and dilution analysis agent.

    Node ID: agent.fundamental
    Tier: 2 (Qwen-2.5-14B) — structured extraction, not deep reasoning
    Ref: MOMENTUM_LOGIC.md §5 (w_float_structure = 0.15)
    Ref: DATA-001-EXT CONSTRAINT-009 (asset checks)
    """

    @property
    def agent_id(self) -> str:
        return "fundamental_agent"

    async def analyze(self, *, ticker: str, **kwargs: Any) -> AgentSignal:
        """D125: Skip LLM call when fundamental data is unavailable.

        On small-cap gappers, float_shares, short_interest, and SEC filings
        are almost always unavailable. The LLM returns NEUTRAL every time
        with "Float size is unknown, preventing any meaningful assessment."
        This wastes a Tier 2 call and artificially inflates the consensus
        gate denominator — making it harder for directional agents to pass.

        When we have no meaningful data, return NEUTRAL with D94_NO_DATA_SKIP
        flag to exclude from the consensus count.
        """
        float_shares = kwargs.get("float_shares")
        short_interest = kwargs.get("short_interest")
        recent_filings = kwargs.get("recent_filings", [])
        shares_outstanding = kwargs.get("shares_outstanding")

        has_data = bool(
            float_shares
            or short_interest
            or recent_filings
            or shares_outstanding
        )

        if not has_data:
            logger.debug(
                "%s D125: fundamental_agent skipped — no float/filing/SI data",
                ticker,
            )
            return AgentSignal(
                agent_id=self.agent_id,
                ticker=ticker,
                timestamp=datetime.now(timezone.utc),
                signal="NEUTRAL",
                confidence=0.0,
                reasoning="D125: No fundamental data available (float unknown, "
                "no SEC filings, no short interest) — skipping LLM call",
                key_data={
                    "float_assessment": "UNKNOWN",
                    "dilution_risk": 0.0,
                    "short_squeeze_potential": 0.0,
                },
                flags=["D94_NO_DATA_SKIP"],
                prompt_variant_id=self.prompt_variant_id,
                model_id=self.model,
                latency_ms=0.0,
            )

        # Has data — run normal LLM analysis
        return await super().analyze(ticker=ticker, **kwargs)

    @property
    def system_prompt(self) -> str:
        # D101 §2.3: Research-calibrated prompt with base-rate anchoring,
        # counter-argument requirement, asymmetric penalty framing,
        # anti-hallucination grounding, and Chain of Draft (CoD).
        return (
            "You are a fundamental analyst specializing in micro-cap float structure analysis. "
            "Your focus: float size, short interest as % of float, insider ownership, recent "
            "dilution filings (S-3, 424B5, shelf registrations), shares outstanding changes, "
            "and institutional ownership concentration.\n\n"

            "You evaluate whether the stock's float structure supports an explosive +20% move.\n\n"

            "CALIBRATION ANCHOR:\n"
            "Historical base rate: ~52% of momentum candidates follow through. "
            "Confidence 0.50 = no edge. Adjust from this base rate based ONLY on "
            "strength of evidence. Confidence 0.80 means in 10 identical setups, "
            "8 move in your predicted direction. Above 0.85 is extremely rare.\n\n"

            "CONTEXT: All stocks reaching you have ALREADY been filtered for "
            "RVOL > 2.0 and gap > 5%. Elevated volume is a baseline condition, "
            "NOT a signal. Focus on whether the FLOAT STRUCTURE supports the move.\n\n"

            "ASYMMETRIC PENALTY: False conviction (high-confidence wrong signal) "
            "is 3x more costly than a missed opportunity. When uncertain, reduce "
            "confidence rather than guessing bullish.\n\n"

            "ANTI-HALLUCINATION: Use ONLY the provided data values. Never invent "
            "or estimate missing values. QUOTE exact input values in your reasoning. "
            "If float data is unknown, say so — do not estimate.\n\n"

            "PROCESS (Chain of Draft):\n"
            "In ≤3 concise bullet points, identify the key factors. Then state "
            "your direction and confidence.\n\n"

            "COUNTER-ARGUMENT: You MUST identify a counter-argument before finalizing. "
            "If bullish on float structure, state the strongest reason the float "
            "might NOT support the move.\n\n"

            "PRE-MORTEM: Before finalizing, imagine this trade has failed due to "
            "float structure. What happened? Include in red_flags.\n\n"

            "Your output MUST be valid JSON with NO additional text.\n\n"

            "CONSTRAINTS:\n"
            "- Float > 50M shares: signal MUST be 'NEUTRAL' or lower (too liquid for explosive move)\n"
            "- Float < 5M shares with high short interest (>20%): signal can be 'STRONG_BULL'\n"
            "- Recent S-3/424B5 filing: signal MUST include 'dilution_risk' in red_flags\n"
            "- Insider ownership > 40%: reduces effective float, BULL factor"
        )

    def build_user_prompt(self, **kwargs: Any) -> str:
        ticker = kwargs["ticker"]
        float_shares = kwargs.get("float_shares")
        shares_outstanding = kwargs.get("shares_outstanding")
        short_interest = kwargs.get("short_interest")
        insider_pct = kwargs.get("insider_ownership_pct")
        institutional_pct = kwargs.get("institutional_ownership_pct")
        recent_filings = kwargs.get("recent_filings", [])

        filings_text = ""
        for f in recent_filings[:5]:
            filings_text += f"\n  - {f.get('form', 'Unknown')}: {f.get('description', 'N/A')} ({f.get('date', 'N/A')})"
        if not filings_text:
            filings_text = "\n  (No recent SEC filings available)"

        # Sweep fix: Build prompt with explicit parts list.
        # Previous code used ternary + implicit f-string concatenation which
        # mis-grouped due to Python operator precedence (ternary binds tighter
        # than string concatenation), producing garbled prompts.
        parts = [f"Analyze the float structure for {ticker}:\n"]
        parts.append(f"Float Shares: {float_shares:,}" if float_shares else "Float Shares: Unknown")
        if shares_outstanding:
            parts.append(f"Shares Outstanding: {shares_outstanding:,}")
        if short_interest:
            parts.append(f"Short Interest: {short_interest:.1%} of float")
        if insider_pct:
            parts.append(f"Insider Ownership: {insider_pct:.1%}")
        if institutional_pct:
            parts.append(f"Institutional Ownership: {institutional_pct:.1%}")
        parts.append(f"\n--- RECENT SEC FILINGS ---{filings_text}\n")
        parts.append(
            "Provide your analysis as JSON:\n"
            '{\n'
            '  "signal": "STRONG_BULL" | "BULL" | "NEUTRAL" | "BEAR" | "STRONG_BEAR",\n'
            '  "confidence": 0.0 to 1.0,\n'
            '  "float_assessment": "NANO" | "MICRO" | "SMALL" | "MEDIUM" | "LARGE",\n'
            '  "short_squeeze_potential": 0.0 to 1.0,\n'
            '  "dilution_risk": 0.0 to 1.0,\n'
            '  "effective_float": int,\n'
            '  "key_reasoning": "...",\n'
            '  "red_flags": ["..."]\n'
            '}'
        )
        return "\n".join(parts)

    def parse_response(self, raw: dict, ticker: str) -> AgentSignal:
        signal = raw.get("signal", "NEUTRAL")
        confidence = float(raw.get("confidence") or 0.0)
        float_assessment = raw.get("float_assessment", "MEDIUM")
        dilution_risk = float(raw.get("dilution_risk") or 0.0)

        # ── Enforce invariants ──
        # Large float caps at NEUTRAL
        if float_assessment in ("LARGE", "MEDIUM"):
            if signal in ("STRONG_BULL", "BULL"):
                signal = "NEUTRAL"
                confidence = min(confidence, 0.4)

        # Dilution risk > 0.7 forces BEAR flag
        if dilution_risk > 0.7 and signal in ("STRONG_BULL", "BULL"):
            signal = "NEUTRAL"
            flags = raw.get("red_flags", [])
            if "dilution_risk" not in [f.lower() for f in flags]:
                flags.append("High dilution risk from recent SEC filings")
            raw["red_flags"] = flags

        confidence = max(0.0, min(1.0, confidence))

        return AgentSignal(
            agent_id=self.agent_id,
            ticker=ticker,
            timestamp=datetime.now(timezone.utc),
            signal=signal,
            confidence=confidence,
            reasoning=raw.get("key_reasoning", ""),
            key_data={
                "float_assessment": float_assessment,
                "short_squeeze_potential": raw.get("short_squeeze_potential", 0),
                "dilution_risk": dilution_risk,
                "effective_float": raw.get("effective_float"),
            },
            flags=raw.get("red_flags", []),
        )
