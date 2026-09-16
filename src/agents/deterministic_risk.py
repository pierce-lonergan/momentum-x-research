"""
MOMENTUM-X Deterministic Risk Agent

### ARCHITECTURAL CONTEXT
Node ID: agent.risk (replaces LLM-based RiskAgent)
Graph Link: docs/memory/graph_state.json → "agent.risk"

### RESEARCH BASIS
D101 Plan §2.1: The LLM-based RiskAgent had a 40% failure rate — catastrophic
for the one function that must NEVER fail. ATR, spread, liquidity, float, and
filing checks are all numeric operations computable in <1ms with 0% failure rate.

This deterministic replacement implements the same RiskSignal interface but uses
pure Python computations instead of LLM calls. Zero API calls, zero latency,
zero failure rate.

### CRITICAL INVARIANTS
1. VETO if bid-ask spread > 3% (PROMPT_SIGNATURES).
2. VETO if active bankruptcy proceedings detected in SEC filings.
3. VETO if S-3/424B5 filed within 5 trading days (dilution trap).
4. CAUTION if float > 50M shares (reduces breakout probability).
5. CAUTION if RVOL < 2.0 at proposed entry time.
6. CAUTION if multiple halts detected (halt_count > 2).
7. CAUTION if gap > 100% without news (possible corporate action).
8. Never fails — guaranteed RiskSignal output on every call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.core.models import AgentSignal, RiskSignal

logger = logging.getLogger(__name__)

# Filing form types that indicate dilution risk
_DILUTION_FORMS = {"S-3", "S-3/A", "424B5", "424B2", "424B4"}

# Filing form types that may indicate bankruptcy
_BANKRUPTCY_KEYWORDS = {
    "chapter 11", "chapter 7", "bankruptcy", "reorganization",
    "debtor in possession", "dip financing",
}


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Safely convert to float, returning default on failure.

    Mirrors deterministic_technical._safe_float. Required because the
    `or default` pattern alone handles None but NOT string-typed bad
    inputs (e.g. 'NaN', 'unknown') -- those bypass `or` and crash float().
    Caught by D221 Phase F test_never_fails_on_malformed_market_data.
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


def _safe_int(val: Any, default: int = 0) -> int:
    """Safely convert to int, returning default on failure. Same rationale
    as _safe_float."""
    try:
        if val is None:
            return default
        return int(val)
    except (ValueError, TypeError):
        return default


class DeterministicRiskAgent:
    """
    Pure-Python risk assessment agent with VETO power. Zero LLM calls.

    Node ID: agent.risk
    Replaces: RiskAgent (LLM-based, 40% failure rate)

    Interface contract:
        - agent_id property → "risk_agent"
        - async analyze(ticker, **kwargs) → RiskSignal
        - Compatible with CachedAgentWrapper (same duck-typed interface)

    Inputs (via kwargs):
        - candidate_signals: list[AgentSignal] — Phase A agent results
        - market_data: dict — current price, spread, volume data
        - sec_filings: dict — recent SEC filings ({"filings": [...]})

    Ref: ADR-001 (Risk Agent veto architecture)
    Ref: D101 Plan §2.1 (Deterministic Risk Agent)
    """

    @property
    def agent_id(self) -> str:
        return "risk_agent"

    async def analyze(self, ticker: str, **kwargs: Any) -> RiskSignal:
        """
        Deterministic risk assessment — pure Python, zero LLM calls.

        Evaluates risk across 6 dimensions:
            1. Liquidity (spread, dollar volume)
            2. Dilution (S-3/424B5 filings)
            3. Bankruptcy (Chapter 11/7 indicators)
            4. Float structure (>50M = reduced breakout probability)
            5. Volume confirmation (RVOL < 2.0 = weak)
            6. Catalyst validity (gap without news)

        Returns:
            RiskSignal with APPROVE/CAUTION/VETO verdict.
        """
        # D221 Phase F bug sweep: the default `[]` in kwargs.get only applies
        # when the key is MISSING. Explicit None from upstream bypasses the
        # default. Coerce explicitly so downstream iteration is safe.
        _cs_raw = kwargs.get("candidate_signals")
        candidate_signals: list[AgentSignal] = _cs_raw if isinstance(_cs_raw, list) else []
        _md_raw = kwargs.get("market_data")
        market_data: dict = _md_raw if isinstance(_md_raw, dict) else {}
        _sf_raw = kwargs.get("sec_filings")
        sec_filings: dict = _sf_raw if isinstance(_sf_raw, dict) else {}

        # ── Extract market data fields ──
        # Use _safe_float / _safe_int (D221 Phase F) instead of `float(... or 0)`
        # because the latter crashes on string-typed bad inputs ("not a number"
        # passes through `or` then fails in float()). The helpers honor INV 8
        # ("Never fails -- guaranteed RiskSignal output").
        # Defensively coerce market_data itself in case upstream passed a non-dict.
        if not isinstance(market_data, dict):
            market_data = {}
        current_price = _safe_float(market_data.get("current_price"))
        bid = _safe_float(market_data.get("bid"))
        ask = _safe_float(market_data.get("ask"))
        rvol = _safe_float(market_data.get("rvol"))
        # D221 Phase F bug sweep: float_shares was passed through raw
        # (no coercion), so string values ('3.7M', '500K') crashed the
        # downstream int comparisons. Coerce via _safe_float; the None case
        # is preserved because _safe_float with default=None would coerce
        # invalid inputs to None too. Use sentinel.
        _fs_raw = market_data.get("float_shares")
        if _fs_raw is None:
            float_shares = None
        else:
            _fs_coerced = _safe_float(_fs_raw, default=float("nan"))
            float_shares = None if _fs_coerced != _fs_coerced else _fs_coerced  # NaN-check
        gap_pct = _safe_float(market_data.get("gap_pct"))
        has_news = market_data.get("has_news", False)
        halt_count = _safe_int(market_data.get("halt_count"))
        avg_daily_volume = _safe_int(market_data.get("avg_daily_volume"))

        # ── Initialize risk breakdown ──
        risk_breakdown: dict[str, float] = {
            "liquidity": 0.0,
            "dilution": 0.0,
            "false_breakout": 0.0,
            "catalyst_validity": 0.0,
            "halt_risk": 0.0,
            "bankruptcy": 0.0,
        }
        veto_reasons: list[str] = []
        caution_reasons: list[str] = []
        flags: list[str] = []

        # ── 1. Liquidity: Bid-ask spread check ──
        spread_pct = 0.0
        if ask > bid > 0:
            spread_pct = (ask - bid) / ask
            if spread_pct > 0.03:
                veto_reasons.append(
                    f"Bid-ask spread {spread_pct:.1%} exceeds 3% threshold "
                    f"(bid=${bid:.2f}, ask=${ask:.2f})"
                )
                risk_breakdown["liquidity"] = 1.0
                flags.append("SPREAD_VETO")
            elif spread_pct > 0.02:
                caution_reasons.append(
                    f"Wide spread {spread_pct:.1%} (bid=${bid:.2f}, ask=${ask:.2f})"
                )
                risk_breakdown["liquidity"] = 0.7
                flags.append("WIDE_SPREAD")
            elif spread_pct > 0.01:
                risk_breakdown["liquidity"] = 0.4
            else:
                risk_breakdown["liquidity"] = 0.1

        # Dollar volume liquidity check
        if avg_daily_volume > 0 and current_price > 0:
            dollar_volume = avg_daily_volume * current_price
            if dollar_volume < 1_000_000:
                caution_reasons.append(
                    f"Low dollar volume ${dollar_volume:,.0f} (< $1M)"
                )
                risk_breakdown["liquidity"] = max(risk_breakdown["liquidity"], 0.7)
                flags.append("LOW_DOLLAR_VOLUME")
            elif dollar_volume < 5_000_000:
                risk_breakdown["liquidity"] = max(risk_breakdown["liquidity"], 0.4)

        # ── 2. Dilution: S-3/424B5 filing detection ──
        # D221 Phase F: tighten type guard. Both the outer dict shape AND the
        # inner filings list must be validated -- a malformed upstream that
        # passes {"filings": "not a list"} would crash the iteration otherwise.
        filings_list_raw = sec_filings.get("filings") if isinstance(sec_filings, dict) else None
        filings_list = filings_list_raw if isinstance(filings_list_raw, list) else []
        recent_dilution = False
        for filing in filings_list:
            if not isinstance(filing, dict):
                continue
            # D221 Phase F bug sweep: form / description are not guaranteed
            # to be strings. LLM upstream or malformed JSON can put dicts,
            # numbers, or None in these fields. Coerce to str defensively.
            _form_raw = filing.get("form", "")
            form = str(_form_raw).upper().strip() if _form_raw is not None else ""
            if form in _DILUTION_FORMS:
                recent_dilution = True
                veto_reasons.append(
                    f"Dilution filing detected: {form} "
                    f"(date: {filing.get('date', 'unknown')})"
                )
                risk_breakdown["dilution"] = 1.0
                flags.append("DILUTION_FILING")
                break

        if not recent_dilution:
            risk_breakdown["dilution"] = 0.0

        # ── 3. Bankruptcy: Chapter 11/7 detection ──
        bankruptcy_detected = False
        for filing in filings_list:
            if not isinstance(filing, dict):
                continue
            # D221 Phase F bug sweep: same defensive str() as dilution loop.
            _desc_raw = filing.get("description", "")
            desc = str(_desc_raw).lower() if _desc_raw is not None else ""
            _form_raw = filing.get("form", "")
            form = str(_form_raw).upper() if _form_raw is not None else ""
            # Check for bankruptcy keywords in filing descriptions
            if any(kw in desc for kw in _BANKRUPTCY_KEYWORDS):
                bankruptcy_detected = True
                # desc is already str()'d above so [:100] is safe
                veto_reasons.append(
                    f"Bankruptcy indicator in filing: {form} — {desc[:100]}"
                )
                risk_breakdown["bankruptcy"] = 1.0
                flags.append("BANKRUPTCY_INDICATOR")
                break

        if not bankruptcy_detected:
            risk_breakdown["bankruptcy"] = 0.0

        # ── 4. Float structure ──
        if float_shares is not None:
            if float_shares > 100_000_000:
                caution_reasons.append(
                    f"Very high float {float_shares / 1e6:.0f}M shares — "
                    f"breakout probability significantly reduced"
                )
                risk_breakdown["false_breakout"] = 0.7
                flags.append("VERY_HIGH_FLOAT")
            elif float_shares > 50_000_000:
                caution_reasons.append(
                    f"High float {float_shares / 1e6:.0f}M shares — "
                    f"breakout probability reduced"
                )
                risk_breakdown["false_breakout"] = 0.5
                flags.append("HIGH_FLOAT")
            elif float_shares < 1_000_000:
                # Ultra-low float: high volatility, manipulation risk
                caution_reasons.append(
                    f"Ultra-low float {float_shares / 1e6:.2f}M shares — "
                    f"manipulation risk elevated"
                )
                risk_breakdown["false_breakout"] = 0.4
                flags.append("ULTRA_LOW_FLOAT")
            else:
                risk_breakdown["false_breakout"] = 0.1
        else:
            # Unknown float — moderate uncertainty
            risk_breakdown["false_breakout"] = 0.3

        # ── 5. Volume confirmation ──
        if rvol > 0:
            if rvol < 1.5:
                caution_reasons.append(
                    f"Very low RVOL {rvol:.1f}x — insufficient volume confirmation"
                )
                risk_breakdown["catalyst_validity"] = max(
                    risk_breakdown["catalyst_validity"], 0.7
                )
                flags.append("VERY_LOW_RVOL")
            elif rvol < 2.0:
                caution_reasons.append(
                    f"Low RVOL {rvol:.1f}x — weak volume confirmation"
                )
                risk_breakdown["catalyst_validity"] = max(
                    risk_breakdown["catalyst_validity"], 0.5
                )
                flags.append("LOW_RVOL")
            elif rvol > 10.0:
                caution_reasons.append(
                    f"Extreme RVOL {rvol:.1f}x — potential exhaustion or short squeeze"
                )
                risk_breakdown["catalyst_validity"] = max(
                    risk_breakdown["catalyst_validity"], 0.4
                )
                flags.append("EXTREME_RVOL")

        # ── 6. Catalyst validity: gap without news ──
        if abs(gap_pct) > 1.0 and not has_news:
            # >100% gap with no news = very likely corporate action
            caution_reasons.append(
                f"Gap {gap_pct:.0%} with no news catalyst — "
                f"possible corporate action (split/reverse split)"
            )
            risk_breakdown["catalyst_validity"] = max(
                risk_breakdown["catalyst_validity"], 0.8
            )
            flags.append("GAP_NO_CATALYST")
        elif abs(gap_pct) > 0.20 and not has_news:
            caution_reasons.append(
                f"Large gap {gap_pct:.0%} without confirmed news catalyst"
            )
            risk_breakdown["catalyst_validity"] = max(
                risk_breakdown["catalyst_validity"], 0.5
            )
            flags.append("UNCONFIRMED_GAP")

        # ── 7. Halt risk ──
        if halt_count > 2:
            caution_reasons.append(
                f"Multiple halts ({halt_count}) in recent sessions — elevated halt risk"
            )
            risk_breakdown["halt_risk"] = min(halt_count * 0.2, 1.0)
            flags.append("MULTIPLE_HALTS")
        elif halt_count > 0:
            risk_breakdown["halt_risk"] = 0.2
        else:
            risk_breakdown["halt_risk"] = 0.0

        # ── 8. Agent consensus check ──
        # If multiple analytical agents flagged bearish, elevate risk.
        # D221 Phase F bug sweep: candidate_signals from kwargs may be None
        # (explicit, bypasses the default=[]) or contain dicts instead of
        # AgentSignal objects. Tolerate both via defensive iteration.
        _cs_iter = candidate_signals if isinstance(candidate_signals, list) else []
        bearish_count = 0
        for s in _cs_iter:
            _sig = getattr(s, "signal", None) if not isinstance(s, dict) else s.get("signal")
            _aid = getattr(s, "agent_id", None) if not isinstance(s, dict) else s.get("agent_id")
            if _sig in ("BEAR", "STRONG_BEAR") and _aid != "risk_agent":
                bearish_count += 1
        if bearish_count >= 3:
            caution_reasons.append(
                f"Strong bearish consensus: {bearish_count} agents bearish"
            )
            risk_breakdown["false_breakout"] = max(
                risk_breakdown["false_breakout"], 0.6
            )
            flags.append("BEARISH_CONSENSUS")

        # ── Compute aggregate risk score ──
        risk_score = sum(risk_breakdown.values()) / len(risk_breakdown)
        risk_score = min(1.0, max(0.0, risk_score))

        # ── Determine verdict ──
        if veto_reasons:
            verdict = "VETO"
            signal_direction = "STRONG_BEAR"
            confidence = 1.0
            pos_recommendation = "NONE"
            reasoning = f"DETERMINISTIC VETO: {'; '.join(veto_reasons)}"
            risk_score = max(risk_score, 0.9)
        elif len(caution_reasons) >= 3 or risk_score > 0.6:
            verdict = "CAUTION"
            signal_direction = "BEAR"
            confidence = 1.0 - risk_score
            pos_recommendation = "QUARTER"
            reasoning = (
                f"DETERMINISTIC CAUTION ({len(caution_reasons)} factors): "
                f"{'; '.join(caution_reasons[:3])}"
            )
        elif caution_reasons:
            verdict = "CAUTION"
            signal_direction = "NEUTRAL"
            confidence = 1.0 - risk_score
            pos_recommendation = "HALF"
            reasoning = (
                f"DETERMINISTIC CAUTION: {'; '.join(caution_reasons)}"
            )
        else:
            verdict = "APPROVE"
            signal_direction = "BULL"
            confidence = 1.0 - risk_score
            pos_recommendation = "FULL"
            reasoning = "DETERMINISTIC APPROVE: No risk factors detected"

        logger.info(
            "D101 RISK %s: verdict=%s risk=%.2f | %s | breakdown=%s",
            ticker, verdict, risk_score, reasoning[:100],
            {k: f"{v:.2f}" for k, v in risk_breakdown.items() if v > 0},
        )

        return RiskSignal(
            agent_id=self.agent_id,
            ticker=ticker,
            timestamp=datetime.now(timezone.utc),
            signal=signal_direction,
            confidence=confidence,
            reasoning=reasoning,
            key_data=risk_breakdown,
            flags=flags,
            risk_verdict=verdict,
            risk_score=risk_score,
            risk_breakdown=risk_breakdown,
            veto_reason="; ".join(veto_reasons) if veto_reasons else None,
            position_size_recommendation=pos_recommendation,
        )
