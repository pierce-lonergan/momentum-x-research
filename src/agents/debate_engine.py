"""
MOMENTUM-X Debate Engine

### ARCHITECTURAL CONTEXT
Node ID: agent.debate_engine
Graph Link: docs/memory/graph_state.json → "agent.debate_engine"

### RESEARCH BASIS
TradingAgents (REF-001): Bull/bear debate achieves Sharpe 8.21 vs single-agent ~2-3.
Structured debate prevents groupthink (Alpha Arena REF-011: GPT-5 lost 53% without adversarial challenge).
Debate divergence metric: MOMENTUM_LOGIC.md §10.

### CRITICAL INVARIANTS
1. DIV > 0.6 → full position sizing (MOMENTUM_LOGIC.md §10).
2. DIV ∈ [0.3, 0.6] → half position.
3. DIV < 0.3 → NO TRADE (insufficient edge).
4. Judge sees both arguments + raw data — not just summaries.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import litellm

from src.core.models import (
    AgentSignal,
    DebateResult,
    ScoredCandidate,
)

logger = logging.getLogger(__name__)

litellm.suppress_debug_info = True
litellm.drop_params = True
# D83: Zero retries — single attempt only. Speed > completeness for momentum.
litellm.num_retries = 0

# D83-fix: Disable the OpenAI *client-level* retries (httpx transport layer).
# litellm.num_retries only controls litellm's own retry loop. The underlying
# openai.AsyncOpenAI client has a separate max_retries (default=2) that causes
# httpx to retry on connection errors / 5xx, adding ~60s of hidden latency
# (observed: 91s instead of 30s timeout). We also pass max_retries=0 explicitly
# in each acompletion() call as defense in depth.
import os
import re
os.environ.setdefault("DEFAULT_MAX_RETRIES", "0")


def _safe_judge_float(val: Any) -> float | None:
    """Safely convert LLM judge output to float. Handles strings like '$4.50'."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        # Strip currency symbols and whitespace
        cleaned = re.sub(r'[^0-9.\-]', '', val)
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None
    return None


class DebateEngine:
    """
    Structured Bull/Bear/Judge debate for high-conviction trade decisions.

    Node ID: agent.debate_engine
    Protocol: ADR-001 (Debate Engine Protocol)
    All three roles use Tier 1 (DeepSeek R1-32B) for maximum reasoning depth.

    Flow:
    1. Bull Agent constructs strongest case FOR the trade
    2. Bear Agent constructs strongest case AGAINST the trade
    3. Both see each other's arguments (single round — latency constraint)
    4. Judge Agent synthesizes both cases into a final verdict

    Ref: REF-001 (TradingAgents, arXiv:2412.20138)
    Ref: MOMENTUM_LOGIC.md §10 (Debate Divergence)
    """

    def __init__(
        self,
        model: str = "together_ai/Qwen/Qwen3.5-397B-A17B",
        advocate_model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: int = 15,
        advocate_timeout: int | None = None,
        divergence_no_trade: float = 0.3,
        divergence_full: float = 0.6,
        fallback_model: str = "",
        fallback_advocate_model: str = "",
        gather_timeout: float = 60.0,  # D218: extracted from hardcoded value
    ) -> None:
        self.model = model  # Judge model (Tier 1: D92 Qwen3.5 397B)
        self.advocate_model = advocate_model or model  # D32: Bull/Bear model (Tier 2 or same)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.advocate_timeout = advocate_timeout or timeout  # D32: shorter timeout for fast model
        self.divergence_no_trade = divergence_no_trade  # D29: configurable threshold
        self.divergence_full = divergence_full  # D29: configurable threshold
        # D91/D92: Fallback models for debate engine resilience
        self.fallback_model = fallback_model  # Judge fallback
        self.fallback_advocate_model = fallback_advocate_model  # Bull/Bear fallback
        self.gather_timeout = gather_timeout  # D218: bull/bear gather timeout

    async def run_debate(
        self, scored: ScoredCandidate
    ) -> DebateResult:
        """
        Execute full Bull/Bear/Judge debate for a scored candidate.

        Args:
            scored: ScoredCandidate with MFCS and all agent signals

        Returns:
            DebateResult with verdict, divergence, and position sizing

        Ref: ADR-001 (Debate Engine Protocol)
        """
        ticker = scored.candidate.ticker
        context = self._build_context(scored)

        # ── Step 1 & 2: Bull and Bear argue in parallel ──
        bull_task = self._run_agent(
            role="bull",
            ticker=ticker,
            context=context,
        )
        bear_task = self._run_agent(
            role="bear",
            ticker=ticker,
            context=context,
        )

        # D217: 60s timeout on bull/bear parallel gather — prevents debate from
        # hanging indefinitely if both agents hit full fallback chains (25s+15s+15s each)
        try:
            bull_arg, bear_arg = await asyncio.wait_for(
                asyncio.gather(bull_task, bear_task),
                timeout=self.gather_timeout,
            )
        except asyncio.TimeoutError:
            logger.error("D217: Debate bull/bear timed out after %.0fs for %s", self.gather_timeout, ticker)
            bull_arg = f"The bull advocate timed out for {ticker}. No bull case available."
            bear_arg = f"The bear advocate timed out for {ticker}. No bear case available."

        # D121 BUG-H6: If an agent errored, its return is an error string like
        # "[BULL AGENT ERROR: ...]". Passing this to the judge causes it to
        # evaluate the error text as an argument. Replace with a neutral message.
        if bull_arg.startswith("[BULL AGENT ERROR:") or bull_arg.startswith("[BEAR AGENT ERROR:"):
            bull_arg = f"The bull advocate was unable to present an argument for {ticker}. No bull case available."
            logger.warning("D121: Bull agent errored — providing neutral placeholder to judge")
        if bear_arg.startswith("[BEAR AGENT ERROR:") or bear_arg.startswith("[BULL AGENT ERROR:"):
            bear_arg = f"The bear advocate was unable to present an argument against {ticker}. No bear case available."
            logger.warning("D121: Bear agent errored — providing neutral placeholder to judge")

        # ── Step 3: Judge synthesizes ──
        verdict = await self._run_judge(
            ticker=ticker,
            context=context,
            bull_argument=bull_arg,
            bear_argument=bear_arg,
        )

        return verdict

    async def _run_agent(
        self, role: str, ticker: str, context: str
    ) -> str:
        """Run Bull or Bear agent and return their argument text."""
        if role == "bull":
            system = (
                f"You are the BULL advocate in a structured trading debate for {ticker}. "
                f"Construct the STRONGEST POSSIBLE case for why {ticker} will achieve "
                f"a +20% price increase today. Focus on the available technical signals, "
                f"volume indicators, and catalyst evidence. Be persuasive but honest — "
                f"do not fabricate data. Acknowledge but minimize bearish concerns. "
                f"Base your argument on the DATA PROVIDED, not on what data is missing."
            )
        else:
            system = (
                f"You are the BEAR advocate in a structured trading debate for {ticker}. "
                f"Construct the STRONGEST POSSIBLE case for why {ticker} will NOT achieve "
                f"+20% and may in fact decline. Attack the bull thesis at its weakest points "
                f"using CONCRETE EVIDENCE from the provided data. "
                f"Identify specific risks supported by actual data points. "
                f"IMPORTANT: Do NOT penalize the bull case for data that is unavailable "
                f"or missing. Arguments must be grounded in evidence that IS present, "
                f"not in the absence of data. Speculation about unknown risks is weak — "
                f"focus on risks you can demonstrate from the available information."
            )

        try:
            response = await litellm.acompletion(
                model=self.advocate_model,  # D32: use faster model for bull/bear
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": context},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=self.advocate_timeout,  # D32: tier-specific timeout
                max_retries=0,  # D83-fix: disable OpenAI client-level httpx retries
            )
            # D121 BUG: Guard against None content from LLM
            text = response.choices[0].message.content or ""

            # D211: Empty response (Qwen3.5 at low temp) must trigger fallback
            if not text.strip():
                raise ValueError(f"Empty advocate response from {self.advocate_model}")

            # D92: Primary models are now instruct (no <think> blocks), but
            # fallback models (DeepSeek V3.1) may still emit them. Keep as defensive.
            if "<think>" in text:
                think_end = text.rfind("</think>")
                if think_end != -1:
                    text = text[think_end + len("</think>"):].strip()

            return text
        except Exception as primary_err:
            # D91: Try fallback advocate model
            if self.fallback_advocate_model:
                logger.warning(
                    "D91: Debate %s primary failed for %s: %s — trying fallback",
                    role, ticker, str(primary_err)[:80],
                )
                try:
                    response = await litellm.acompletion(
                        model=self.fallback_advocate_model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": context},
                        ],
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                        timeout=self.advocate_timeout,
                        max_retries=0,
                    )
                    # D121 BUG: Guard against None content from LLM
                    text = response.choices[0].message.content or ""
                    if "<think>" in text:
                        think_end = text.rfind("</think>")
                        if think_end != -1:
                            text = text[think_end + len("</think>"):].strip()
                    logger.info("D91: Debate %s FALLBACK SUCCESS for %s", role, ticker)
                    return text
                except Exception as fb_err:
                    logger.error(
                        "D91: Debate %s BOTH models failed for %s: %s; fallback: %s",
                        role, ticker, str(primary_err)[:60], str(fb_err)[:60],
                    )
            else:
                logger.error("Debate %s agent failed for %s: %s", role, ticker, primary_err)
            return f"[{role.upper()} AGENT ERROR: {str(primary_err)}]"

    async def _run_judge(
        self,
        ticker: str,
        context: str,
        bull_argument: str,
        bear_argument: str,
    ) -> DebateResult:
        """
        Judge synthesizes bull/bear arguments into a final verdict.
        Computes debate divergence per MOMENTUM_LOGIC.md §10.
        """
        system = (
            f"You are the impartial Judge in a trading debate about {ticker}. "
            f"You have access to both the Bull and Bear arguments AND the raw underlying data. "
            f"Your job:\n"
            f"1. Identify which side presented stronger EVIDENCE (not rhetoric)\n"
            f"2. Weight arguments based on evidence QUALITY, not quantity. "
            f"An argument grounded in actual data is stronger than one based on missing data.\n"
            f"3. If the Bear's case relies primarily on data gaps or unknowns rather than "
            f"concrete negative indicators, that weakens the Bear's position.\n"
            f"4. Assess which risks are adequately addressed vs hand-waved\n"
            f"5. Produce a FINAL VERDICT with calibrated confidence\n\n"
            f"Your output MUST be valid JSON with NO additional text."
        )

        user_prompt = (
            f"--- RAW DATA ---\n{context}\n\n"
            f"--- BULL ARGUMENT ---\n{bull_argument}\n\n"
            f"--- BEAR ARGUMENT ---\n{bear_argument}\n\n"
            f"Provide your verdict as JSON:\n"
            f'{{\n'
            f'  "verdict": "STRONG_BUY" | "BUY" | "HOLD" | "NO_TRADE",\n'
            f'  "confidence": 0.0-1.0,\n'
            f'  "bull_strength": 0.0-1.0,\n'
            f'  "bear_strength": 0.0-1.0,\n'
            f'  "key_reasoning": "...",\n'
            f'  "entry_price": float,\n'
            f'  "stop_loss": float,\n'
            f'  "target_prices": [float, float, float],\n'
            f'  "time_horizon": "INTRADAY" | "OVERNIGHT" | "MULTI_DAY"\n'
            f'}}'
        )

        try:
            # D59: Retry once on empty parse — LLM may truncate JSON on first attempt
            raw = {}
            for attempt in range(2):
                response = await litellm.acompletion(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.2,  # Lower temp for judge — consistency matters
                    max_tokens=self.max_tokens,
                    timeout=self.timeout,
                    max_retries=0,  # D83-fix: disable OpenAI client-level httpx retries
                    response_format={"type": "json_object"},
                )

                raw_text = response.choices[0].message.content or ""

                # D211: Empty response from judge model — try fallback
                if not raw_text.strip():
                    logger.warning(
                        "%s Judge empty response (attempt %d) from %s — will retry/fallback",
                        ticker, attempt + 1, self.model,
                    )
                    continue

                raw = self._extract_json(raw_text)

                # D94: Log raw judge response for post-hoc debugging
                logger.debug(
                    "%s Judge raw (attempt %d): %s",
                    ticker, attempt + 1, raw_text[:500] if raw_text else "(empty)",
                )
                logger.debug("%s Judge parsed fields: %s", ticker, list(raw.keys()))

                if raw and raw.get("verdict"):
                    break  # Got usable response
                if attempt == 0:
                    logger.warning(
                        "%s Judge parse empty on attempt 1 — retrying...", ticker,
                    )

            # D211: If primary judge model produced no verdict after retries, try fallback
            if not raw.get("verdict") and self.fallback_model:
                logger.warning(
                    "%s Judge primary model (%s) failed both attempts — trying fallback %s",
                    ticker, self.model, self.fallback_model,
                )
                try:
                    fb_response = await litellm.acompletion(
                        model=self.fallback_model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.2,
                        max_tokens=self.max_tokens,
                        timeout=self.timeout,
                        max_retries=0,
                        response_format={"type": "json_object"},
                    )
                    fb_text = fb_response.choices[0].message.content or ""
                    if fb_text.strip():
                        raw = self._extract_json(fb_text)
                        logger.info(
                            "%s Judge fallback SUCCESS via %s — verdict=%s",
                            ticker, self.fallback_model, raw.get("verdict", "?"),
                        )
                except Exception as fb_err:
                    logger.warning(
                        "%s Judge fallback also failed: %s", ticker, str(fb_err)[:100],
                    )

            # D34: Verdict-aware defaults for bull/bear_strength
            # When judge returns partial JSON with verdict but no strengths,
            # estimate from verdict instead of defaulting both to 0.5 (which
            # produces divergence=0.0 and hides the parse problem).
            verdict = raw.get("verdict", "NO_TRADE")
            if "verdict" in raw and "bull_strength" not in raw:
                if verdict in ("STRONG_BUY", "BUY"):
                    bull_str = 0.7
                    bear_str = 0.3
                elif verdict == "NO_TRADE":
                    bull_str = 0.3
                    bear_str = 0.7
                else:  # HOLD
                    bull_str = 0.5
                    bear_str = 0.5
            else:
                # D121 BUG-S1: Use _safe_judge_float() — LLM may return
                # non-numeric strings like "high" or "0.75/1.0".
                bull_str = _safe_judge_float(raw.get("bull_strength")) or 0.5
                bear_str = _safe_judge_float(raw.get("bear_strength")) or 0.5
            divergence = abs(bull_str - bear_str)
            # D94: Verdict-aware confidence default (parallel to D34 bull/bear fix).
            # When judge omits confidence field, estimate from verdict instead of
            # defaulting to 0.0 which triggers the guard below and kills valid trades.
            if "confidence" not in raw:
                if verdict in ("STRONG_BUY", "BUY"):
                    confidence = 0.6
                elif verdict == "NO_TRADE":
                    confidence = 0.3
                else:  # HOLD
                    confidence = 0.4
                logger.debug(
                    "%s Judge omitted confidence field — inferred %.1f from verdict=%s",
                    ticker, confidence, verdict,
                )
            else:
                confidence = _safe_judge_float(raw.get("confidence")) or 0.0

            # D34: Confidence guard — only force NO_TRADE on true parse failure
            # (empty response with no verdict, no reasoning, no confidence).
            # D94: Relaxed — don't trigger when judge returned a valid verdict
            # but omitted numeric fields. The D34/D94 defaults handle that.
            if not raw.get("verdict") and not raw.get("key_reasoning"):
                logger.warning(
                    "%s Judge returned no usable data (empty parse), forcing NO_TRADE",
                    ticker,
                )
                verdict = "NO_TRADE"
                confidence = 0.0
                pos_size = "NONE"
            else:
                # ── Position sizing from divergence (MOMENTUM_LOGIC.md §10) ──
                # D29: Use configurable thresholds instead of hardcoded values
                if divergence > self.divergence_full:
                    pos_size = "FULL"
                elif divergence > self.divergence_no_trade:
                    pos_size = "HALF"
                else:
                    pos_size = "QUARTER"  # D29: partial position, not zero

                # D94: Low-divergence handling — respect judge's verdict.
                # Previously converted HOLD→NO_TRADE when divergence < threshold.
                # This killed valid trades when bull/bear strengths were missing
                # (divergence=0.0 from 0.5/0.5 defaults or HOLD 0.5/0.5).
                # Now: only reduce sizing, never override the judge's verdict.
                if divergence < self.divergence_no_trade:
                    if verdict in ("BUY", "STRONG_BUY"):
                        pos_size = "QUARTER"
                    # D94: HOLD stays HOLD (proceeds to orchestrator with no trade)
                    # NO_TRADE stays NO_TRADE (judge explicitly rejected)

            return DebateResult(
                ticker=ticker,
                verdict=verdict,
                confidence=confidence,
                bull_strength=bull_str,
                bear_strength=bear_str,
                debate_divergence=divergence,
                bull_argument=bull_argument[:2000],  # Truncate for storage
                bear_argument=bear_argument[:2000],
                judge_reasoning=raw.get("key_reasoning", ""),
                position_size=pos_size,
                # D121 BUG: LLM can return non-numeric strings (e.g. "$4.50").
                # Safe-convert to float to prevent Pydantic coercion crash.
                entry_price=_safe_judge_float(raw.get("entry_price")),
                stop_loss=_safe_judge_float(raw.get("stop_loss")),
                target_prices=[t for t in [_safe_judge_float(x) for x in raw.get("target_prices", [])] if t is not None and t > 0],
                time_horizon=raw.get("time_horizon", "INTRADAY"),
            )

        except Exception as primary_err:
            # D91: Try fallback judge model before giving up
            if self.fallback_model:
                logger.warning(
                    "D91: Judge primary failed for %s: %s — trying fallback %s",
                    ticker, str(primary_err)[:80], self.fallback_model,
                )
                try:
                    response = await litellm.acompletion(
                        model=self.fallback_model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.2,
                        max_tokens=self.max_tokens,
                        timeout=self.timeout,
                        max_retries=0,
                        response_format={"type": "json_object"},
                    )
                    raw_text = response.choices[0].message.content
                    raw = self._extract_json(raw_text)
                    logger.info("D91: Judge FALLBACK SUCCESS for %s", ticker)

                    # Same verdict processing as primary path
                    verdict = raw.get("verdict", "NO_TRADE")
                    if "verdict" in raw and "bull_strength" not in raw:
                        if verdict in ("STRONG_BUY", "BUY"):
                            bull_str, bear_str = 0.7, 0.3
                        elif verdict == "NO_TRADE":
                            bull_str, bear_str = 0.3, 0.7
                        else:
                            bull_str, bear_str = 0.5, 0.5
                    else:
                        bull_str = _safe_judge_float(raw.get("bull_strength")) or 0.5
                        bear_str = _safe_judge_float(raw.get("bear_strength")) or 0.5
                    divergence = abs(bull_str - bear_str)
                    # D94: Same confidence fix as primary path
                    if "confidence" not in raw:
                        if verdict in ("STRONG_BUY", "BUY"):
                            confidence = 0.6
                        elif verdict == "NO_TRADE":
                            confidence = 0.3
                        else:
                            confidence = 0.4
                    else:
                        confidence = _safe_judge_float(raw.get("confidence")) or 0.0

                    if not raw.get("verdict") and not raw.get("key_reasoning"):
                        verdict = "NO_TRADE"
                        confidence = 0.0
                        pos_size = "NONE"
                    else:
                        if divergence > self.divergence_full:
                            pos_size = "FULL"
                        elif divergence > self.divergence_no_trade:
                            pos_size = "HALF"
                        else:
                            pos_size = "QUARTER"
                        if divergence < self.divergence_no_trade:
                            if verdict in ("BUY", "STRONG_BUY"):
                                pos_size = "QUARTER"

                    return DebateResult(
                        ticker=ticker,
                        verdict=verdict,
                        confidence=confidence,
                        bull_strength=bull_str,
                        bear_strength=bear_str,
                        debate_divergence=divergence,
                        bull_argument=bull_argument[:2000],
                        bear_argument=bear_argument[:2000],
                        judge_reasoning=raw.get("key_reasoning", "(D91 fallback)"),
                        position_size=pos_size,
                        # D121 BUG-O1: Fallback path was missing _safe_judge_float,
                        # so LLM strings like "$4.50" would crash downstream math.
                        entry_price=_safe_judge_float(raw.get("entry_price")),
                        stop_loss=_safe_judge_float(raw.get("stop_loss")),
                        target_prices=[t for t in [_safe_judge_float(x) for x in raw.get("target_prices", [])] if t is not None and t > 0],
                        time_horizon=raw.get("time_horizon", "INTRADAY"),
                    )
                except Exception as fb_err:
                    logger.error(
                        "D91: Judge BOTH models failed for %s: primary=%s, fallback=%s",
                        ticker, str(primary_err)[:60], str(fb_err)[:60],
                    )

            logger.error("Judge agent failed for %s: %s", ticker, primary_err)
            return DebateResult(
                ticker=ticker,
                verdict="NO_TRADE",
                confidence=0.0,
                bull_strength=0.0,
                bear_strength=0.0,
                debate_divergence=0.0,
                judge_reasoning=f"Judge error: {str(primary_err)}",
                position_size="NONE",
            )

    def _build_context(self, scored: ScoredCandidate) -> str:
        """Build shared context from scored candidate for debate agents."""
        c = scored.candidate
        lines = [
            f"Ticker: {c.ticker}",
            f"Current Price: ${c.current_price:.2f}",
            f"Previous Close: ${c.previous_close:.2f}",
            f"Gap: {c.gap_pct:.1%} ({c.gap_classification})",
            f"RVOL: {c.rvol:.1f}x",
            f"Pre-Market Volume: {c.premarket_volume:,}",
            f"Float: {c.float_shares:,}" if c.float_shares else "Float: Unknown",
            f"MFCS Score: {scored.mfcs:.3f}",
            f"\n--- AGENT SIGNALS ---",
        ]
        for sig in scored.agent_signals:
            lines.append(
                f"  [{sig.agent_id}] {sig.signal} (conf={sig.confidence:.2f}): "
                f"{sig.reasoning[:300]}"
            )
            if sig.flags:
                lines.append(f"    Flags: {sig.flags}")

        # ── D30: Data quality context for fair debate ──
        agents_with_data = []
        agents_without_data = []
        for sig in scored.agent_signals:
            if sig.signal == "NEUTRAL" and not sig.reasoning.strip():
                agents_without_data.append(sig.agent_id)
            else:
                agents_with_data.append(sig.agent_id)

        if agents_without_data:
            lines.append(f"\n--- DATA QUALITY NOTE ---")
            lines.append(
                f"Agents with real data: {', '.join(agents_with_data)}"
            )
            lines.append(
                f"Agents with NO data (empty/default signals): "
                f"{', '.join(agents_without_data)}"
            )
            lines.append(
                f"NOTE: Arguments about risks from data-absent agents "
                f"carry less weight."
            )

        return "\n".join(lines)

    # ── D118: Entry Catalyst Profiler ─────────────────────────────────

    async def classify_catalyst(
        self,
        ticker: str,
        current_price: float,
        gap_pct: float,
        rvol: float,
        news_headlines: list[str],
        manipulation_phase: str,
        sector: str,
        float_shares: int | None = None,
        sec_filings_summary: str = "",
        timeout: int = 10,
        max_tokens: int = 2048,
    ):
        """
        D118: Classify the catalyst driving a stock's gap-up and recommend
        downstream exit strategy parameters.

        Single LLM call (not bull/bear/judge — catalyst classification is a
        structured extraction task). Runs in parallel with existing agent
        dispatch at entry time. Zero net latency impact.

        Returns CatalystProfile with recommended half-life, ATR multiplier,
        risk scale, and gratitude decay. On error, returns safe default
        (SHORT_LIVED, 20-min half-life) matching current "unknown" behavior.
        """
        from src.core.models import CatalystProfile

        headlines_text = "\n".join(
            f"  - {h}" for h in news_headlines[:10]
        ) if news_headlines else "  [No news headlines available]"

        system = (
            "You are a catalyst analyst for momentum day trading. Given data about "
            "a stock that just gapped up, classify the catalyst and estimate its "
            "price impact durability. Your classification directly determines how "
            "the trading system manages exit timing.\n\n"
            "Your output MUST be valid JSON with NO additional text."
        )

        user_prompt = (
            f"Stock: {ticker} | Price: ${current_price:.2f} | "
            f"Gap: {gap_pct:+.1%} | RVOL: {rvol:.1f}x\n"
            f"Sector: {sector or 'Unknown'} | "
            f"Float: {float_shares / 1e6:.1f}M shares\n"
            if float_shares else
            f"Stock: {ticker} | Price: ${current_price:.2f} | "
            f"Gap: {gap_pct:+.1%} | RVOL: {rvol:.1f}x\n"
            f"Sector: {sector or 'Unknown'} | Float: Unknown\n"
        )
        user_prompt += (
            f"Manipulation Phase: {manipulation_phase}\n"
            f"\nNews Headlines:\n{headlines_text}\n"
        )
        if sec_filings_summary:
            user_prompt += f"\nSEC Filings (recent):\n{sec_filings_summary}\n"

        user_prompt += (
            "\nClassify this catalyst into ONE of these durability categories:\n\n"
            "1. PERMANENT_REVALUATION — FDA approval, major acquisition, transformative "
            "contract. Price impact lasts hours to days.\n"
            "   → half_life_minutes: 120-240, atr_multiplier: 2.5-3.5, "
            "risk_scale: 1.2-1.5, gratitude_decay: 0.01-0.03\n\n"
            "2. MULTI_HOUR — Strong earnings beat, significant partnership, sector "
            "rotation catalyst. Price impact lasts 1-4 hours.\n"
            "   → half_life_minutes: 60-180, atr_multiplier: 2.0-2.5, "
            "risk_scale: 1.0-1.3, gratitude_decay: 0.03-0.05\n\n"
            "3. SHORT_LIVED — Technical breakout, moderate news, gap continuation. "
            "Price impact lasts 15-60 minutes.\n"
            "   → half_life_minutes: 15-60, atr_multiplier: 1.5-2.0, "
            "risk_scale: 0.8-1.0, gratitude_decay: 0.05-0.08\n\n"
            "4. EPHEMERAL — Social media pump, promotional activity, no identifiable "
            "real catalyst. Price impact lasts 5-15 minutes.\n"
            "   → half_life_minutes: 5-15, atr_multiplier: 1.0-1.5, "
            "risk_scale: 0.3-0.6, gratitude_decay: 0.08-0.15\n\n"
            "Return JSON:\n"
            '{\n'
            '  "catalyst_type": "fda_approval"|"earnings_beat"|"contract_deal"|'
            '"technical_breakout"|"social_media_promo"|"sector_rotation"|"unknown",\n'
            '  "durability": "PERMANENT_REVALUATION"|"MULTI_HOUR"|"SHORT_LIVED"|"EPHEMERAL",\n'
            '  "confidence": 0.0-1.0,\n'
            '  "reasoning": "one sentence explaining classification",\n'
            '  "half_life_minutes": integer,\n'
            '  "atr_multiplier": float,\n'
            '  "risk_scale": float,\n'
            '  "gratitude_decay": float\n'
            '}'
        )

        try:
            response = await litellm.acompletion(
                model=self.advocate_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=max_tokens,
                timeout=timeout,
                max_retries=0,
                response_format={"type": "json_object"},
            )

            raw_text = response.choices[0].message.content
            raw = self._extract_json(raw_text)

            logger.debug(
                "D118 %s catalyst raw: %s",
                ticker, raw_text[:300] if raw_text else "(empty)",
            )

            if not raw or not raw.get("durability"):
                logger.warning(
                    "D118 %s: empty parse from catalyst profiler — using defaults",
                    ticker,
                )
                return self._default_catalyst_profile(ticker)

            return CatalystProfile(
                ticker=ticker,
                catalyst_type=raw.get("catalyst_type", "unknown"),
                durability=raw.get("durability", "SHORT_LIVED"),
                confidence=float(raw.get("confidence", 0.5)),
                reasoning=str(raw.get("reasoning", ""))[:500],
                recommended_half_life_minutes=int(
                    raw.get("half_life_minutes", 20)
                ),
                recommended_atr_multiplier=float(
                    raw.get("atr_multiplier", 2.0)
                ),
                recommended_risk_scale=max(0.25, min(2.0, float(
                    raw.get("risk_scale", 1.0)
                ))),
                recommended_gratitude_decay=float(
                    raw.get("gratitude_decay", 0.05)
                ),
            )

        except Exception as e:
            logger.warning(
                "D118 %s: catalyst profiler failed — using defaults: %s",
                ticker, str(e)[:100],
            )
            return self._default_catalyst_profile(ticker)

    @staticmethod
    def _default_catalyst_profile(ticker: str):
        """D118: Safe default profile matching current 'unknown' behavior."""
        from src.core.models import CatalystProfile
        return CatalystProfile(
            ticker=ticker,
            catalyst_type="unknown",
            durability="SHORT_LIVED",
            confidence=0.0,
            reasoning="D118: default (profiler failed or disabled)",
            recommended_half_life_minutes=20,
            recommended_atr_multiplier=2.0,
            recommended_risk_scale=1.0,
            recommended_gratitude_decay=0.05,
        )

    @staticmethod
    def _extract_json(raw: str) -> dict:
        """Extract JSON from LLM response. D92: <think> handling kept as defensive
        for fallback models (DeepSeek V3.1). Primary Qwen models don't emit them."""
        import src.utils.fast_json as json  # D87: orjson drop-in
        import re

        text = raw.strip()

        # Strip R1 thinking blocks (handle both complete and incomplete tags)
        if "<think>" in text:
            think_end = text.rfind("</think>")
            if think_end != -1:
                text = text[think_end + len("</think>"):].strip()
            else:
                # Incomplete think block — try to find JSON after <think>
                think_start = text.find("<think>")
                after_think = text[think_start + len("<think>"):]
                json_start = min(
                    (after_think.find("{") if after_think.find("{") >= 0 else len(after_think)),
                    (after_think.find("[") if after_think.find("[") >= 0 else len(after_think)),
                )
                if json_start < len(after_think):
                    text = after_think[json_start:].strip()

        # Strip markdown fences
        if "```" in text:
            fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
            if fence_match:
                text = fence_match.group(1).strip()
            elif text.startswith("```"):
                lines = text.split("\n")
                lines = [line for line in lines if not line.strip().startswith("```")]
                text = "\n".join(lines).strip()

        # Try direct parse
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Fallback: regex extraction of JSON object
            json_match = re.search(r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})", text, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    logger.warning(
                        "Debate _extract_json: regex match also invalid (len=%d): %s...",
                        len(text), text[:200],
                    )
                    return {}
            else:
                logger.warning(
                    "Debate _extract_json: no JSON found in response (len=%d): %s...",
                    len(text), text[:200],
                )
                return {}

        # Unwrap JSON arrays (DeepSeek R1 sometimes wraps in [...])
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed and isinstance(parsed[0], dict) else {}
        if not isinstance(parsed, dict):
            parsed = {}
        return parsed
