"""
D221 Phase F: Decision-learning Discord channel — Tier 3 enriched messages.

Companion to src/monitoring/alerts.py. The legacy alerts.py functions
(post_trade_open, post_trade_close, etc.) keep working unchanged for
the WATCHLIST channel; this module adds NEW enriched functions for the
Decision Learning channel that include:

  - Per-agent verdict + reasoning attribution (Tier 1)
  - Shapley contribution breakdown at trade close (Tier 1)
  - DISAGREE_SHADOW_BUYS surface for cascade-anti-selection observation (Tier 2)
  - Statistical context: percentile rank, historical winrate, catalyst
    base rate, per-agent accuracy, consensus annotation (Tier 3)
  - Paper-1 annotated JSON footer with the structured numerics behind
    every human-readable claim, so each message is a Paper 1 data row

### Channel routing
This module posts to DECISION_LEARNING_WEBHOOK_URL. Reads the URL once
at module load via os.environ. If unset, every function is a no-op
(prevents accidental message floods if the env isn't propagated).

### The cascade-anti-selection annotation
Per the v2 thesis (docs/research-log/composite_retrain_log.md): cascade
consensus has a -0.86 coefficient in v0 composite. Every message where
agent consensus is high gets an explicit annotation flagging this as
contra-predictive. The annotation IS the publishable claim, surfaced in
real time.

### Trust-the-gate allowlist
Some rejection codes (HARD_VETO_DILUTION, HARD_VETO_BANKRUPTCY,
D198_REGIME_HALT, etc.) are correct-by-design. DISAGREE messages
suppress these so the channel stays signal-dense.

### Failure mode
NEVER raises into the caller. Same contract as alerts.py.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# ── Module-level config (read once) ──────────────────────────────────

_WEBHOOK_URL = os.environ.get("DECISION_LEARNING_WEBHOOK_URL", "").strip()
try:
    _DISAGREE_THRESHOLD = float(os.environ.get("DISCORD_DISAGREE_THRESHOLD", "0.55"))
except (TypeError, ValueError):
    _DISAGREE_THRESHOLD = 0.55

# Suppress DISAGREE messages where the rejecting gate caught a
# correct-by-design risk. These are NOT "the system disagreed with itself
# in an interesting way" -- they're "the system did its job."
_TRUST_THE_GATE_REJECTIONS: frozenset[str] = frozenset({
    "HARD_VETO_DILUTION",
    "HARD_VETO_BANKRUPTCY",
    "HARD_VETO_SPREAD",
    "D198_REGIME_HALT",      # Regime-level halt is system-correct
    "D150_MAX_POSITIONS",    # Capacity hit, not signal disagreement
    "D150_CIRCUIT_BREAKER",  # Risk control engaged
    "D56_DUPLICATE",         # Already in position
    "D85_FAST_PATH",         # Already executed via fast_path
    "EXECUTED",              # Trade actually went through
    "EXECUTED_VIA_FAST_PATH",
    "EXECUTED_VIA_PHASE2",
})

_ET = ZoneInfo("America/New_York")
_COLOR_BLUE = 0x3498DB
_COLOR_GREEN = 0x2ECC71
_COLOR_RED = 0xE74C3C
_COLOR_PURPLE = 0x9B59B6
_COLOR_GOLD = 0xF1C40F


def is_enabled() -> bool:
    """True if the Decision Learning channel is configured."""
    return bool(_WEBHOOK_URL)


# ── Distribution cache singleton (lazy, fail-safe) ───────────────────


_dist_cache_singleton: Any = None


def get_distribution_cache() -> Any:
    """Return the process-wide DistributionCache, constructing on first call.

    Returns None if construction fails (cache will not crash callers).
    Safe to call from any code path; cache is loaded lazily on first
    query, not on construction.
    """
    global _dist_cache_singleton
    if _dist_cache_singleton is None:
        try:
            from src.monitoring.distribution_cache import DistributionCache
            _dist_cache_singleton = DistributionCache()
        except Exception as e:
            logger.warning("DistributionCache singleton init failed: %s", e)
            _dist_cache_singleton = None
    return _dist_cache_singleton


def disagree_threshold() -> float:
    """Current DISAGREE_SHADOW_BUYS threshold for caller filtering."""
    return _DISAGREE_THRESHOLD


def is_trusted_gate(rejection_code: str | None) -> bool:
    """True if this gate's rejections should suppress DISAGREE messages."""
    if not isinstance(rejection_code, str):
        return False
    # Match exact code OR a substring (some rejection_reason strings
    # include extra context like 'D204_NEWS_CONFIDENCE: confidence 0.32 < ...').
    code_upper = rejection_code.upper().strip()
    for trusted in _TRUST_THE_GATE_REJECTIONS:
        if trusted in code_upper:
            return True
    return False


# ── Cascade-anti-selection annotation ────────────────────────────────


def _consensus_strength_from_signals(signals: list[Any]) -> float | None:
    """Compute consensus strength as 1 - normalized_std of agent signal
    numerics. Returns None if fewer than 2 signals.

    Higher = more agreement. Per the -0.86 coefficient finding, higher
    consensus is contra-predictive in this universe."""
    sig_to_num = {
        "STRONG_BEAR": -2.0, "BEAR": -1.0, "NEUTRAL": 0.0,
        "BULL": 1.0, "STRONG_BULL": 2.0,
    }
    nums = []
    for s in signals or []:
        # Tolerate dict, AgentSignal-like, or string
        if isinstance(s, dict):
            sig = s.get("signal")
        else:
            sig = getattr(s, "signal", None)
        if isinstance(sig, str) and sig in sig_to_num:
            nums.append(sig_to_num[sig])
    if len(nums) < 2:
        return None
    mean = sum(nums) / len(nums)
    var = sum((x - mean) ** 2 for x in nums) / len(nums)
    std = math.sqrt(var)
    # Normalize: max possible std on [-2, 2] scale is ~2.0. Map to [0, 1]
    # consensus by 1 - std/2 (clamped).
    return max(0.0, min(1.0, 1.0 - std / 2.0))


def _format_cascade_warning(consensus: float | None) -> str | None:
    """Returns the warning text if consensus is high enough to be
    contra-predictive, else None."""
    if consensus is None or consensus < 0.7:
        return None
    return (
        f"⚠ **CASCADE CONSENSUS = {consensus:.2f}** — per D221 retrain finding "
        f"(arena_buy_verdict coefficient = -0.86 in v0 composite), high "
        f"agent agreement is *contra-predictive* in this universe. The "
        f"more agents agree, the more this resembles the slice the cascade "
        f"loses on."
    )


# ── HTTP helpers (never raise) ───────────────────────────────────────


async def _post_async(payload: dict) -> None:
    """Async post. Never raises."""
    if not _WEBHOOK_URL:
        return
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(_WEBHOOK_URL, json=payload)
    except Exception as e:
        logger.debug("decision_learning post failed: %s", e)


def _post_sync(payload: dict) -> None:
    """Sync post. Never raises."""
    if not _WEBHOOK_URL:
        return
    try:
        import httpx
        httpx.post(_WEBHOOK_URL, json=payload, timeout=5)
    except Exception:  # noqa: silent-handler
        # By-design: Discord webhook failures must NEVER raise into the
        # trading hot path. Logged debug-only by the async sibling; sync
        # path swallows entirely. See docs/research-log/12_discord_learning_utility.md.
        pass


# ── Paper 1 JSON footer ──────────────────────────────────────────────


def _paper1_footer(metrics: dict[str, Any]) -> str:
    """Render structured numerics as a fenced JSON code block.

    Every Tier 3 message includes one of these so the Discord feed is
    a Paper 1 dataset, not just notifications. Filter sentinels (None /
    NaN) to keep the block compact.
    """
    cleaned = {}
    for k, v in metrics.items():
        if v is None:
            continue
        if isinstance(v, float):
            if v != v or v == float("inf") or v == float("-inf"):
                continue
            cleaned[k] = round(v, 6)
        else:
            cleaned[k] = v
    if not cleaned:
        return ""
    body = json.dumps(cleaned, sort_keys=True, separators=(",", ": "))
    return f"\n```json\n{body}\n```"


# ── Time formatting ──────────────────────────────────────────────────


def _now_et_str() -> str:
    n = datetime.now(timezone.utc).astimezone(_ET)
    h12 = n.hour % 12 or 12
    ampm = "AM" if n.hour < 12 else "PM"
    return f"{h12}:{n.minute:02d} {ampm} ET"


# ── Tier 1 + 3: trade-open enriched ──────────────────────────────────


async def post_trade_open_enriched(
    *,
    ticker: str,
    qty: int,
    entry_price: float,
    stop_loss: float,
    mfcs: float | None,
    path: str,
    agent_signals: list[Any] | None = None,
    catalyst_type: str | None = None,
    kelly_tier: int | None = None,
    distribution_cache: Any = None,
) -> None:
    """Trade-open message with decision attribution + statistical context.

    Args:
        ticker: stock symbol
        qty: shares
        entry_price: fill price
        stop_loss: stop level
        mfcs: MFCS at entry
        path: 'FAST_PATH' / 'PHASE2_BUY' / 'DEGRADED_MODE'
        agent_signals: list of AgentSignal objects (or dicts) from cascade
        catalyst_type: news_agent's catalyst_type (e.g. 'FDA_APPROVAL')
        kelly_tier: 1-4 conviction tier
        distribution_cache: optional DistributionCache for percentile lookups
    """
    if not is_enabled():
        return

    # Per-agent verdict table
    agent_lines: list[str] = []
    if agent_signals:
        for sig in agent_signals[:6]:  # cap at 6 to keep readable
            try:
                agent_id = (
                    sig.get("agent_id") if isinstance(sig, dict)
                    else getattr(sig, "agent_id", "unknown")
                )
                signal = (
                    sig.get("signal") if isinstance(sig, dict)
                    else getattr(sig, "signal", "?")
                )
                conf = (
                    sig.get("confidence") if isinstance(sig, dict)
                    else getattr(sig, "confidence", 0.0)
                )
                conf_f = float(conf) if conf is not None else 0.0
                agent_lines.append(
                    f"  • `{agent_id:22s}` {signal:11s} conf={conf_f:.2f}"
                )
            except Exception:  # noqa: silent-handler
                # Same pattern as DISAGREE message: one malformed entry
                # skipped, others continue.
                continue

    # Cascade-anti-selection annotation
    consensus = _consensus_strength_from_signals(agent_signals or [])
    cascade_warning = _format_cascade_warning(consensus)

    # Statistical context
    mfcs_pct_str = ""
    mfcs_winrate_str = ""
    catalyst_winrate_str = ""
    paper1_metrics: dict[str, Any] = {
        "ticker": ticker, "qty": qty, "entry_price": entry_price,
        "stop_loss": stop_loss, "mfcs": mfcs, "path": path,
        "catalyst_type": catalyst_type, "kelly_tier": kelly_tier,
        "consensus_strength": consensus,
    }
    if distribution_cache is not None:
        try:
            pct = distribution_cache.mfcs_percentile(mfcs)
            if pct.ok:
                mfcs_pct_str = f" (P{int(pct.value * 100)} of historical setups, n={pct.n})"
                paper1_metrics["mfcs_percentile"] = pct.value
            wr = distribution_cache.mfcs_historical_winrate(mfcs)
            if wr.ok:
                mfcs_winrate_str = (
                    f"\n  📊 Historical conversion at MFCS={mfcs:.2f}±0.05: "
                    f"**{wr.value:.0%}** win rate (n={wr.n})"
                )
                paper1_metrics["mfcs_winrate_at_level"] = wr.value
                paper1_metrics["mfcs_winrate_n"] = wr.n
            if catalyst_type:
                cwr = distribution_cache.catalyst_winrate(catalyst_type)
                if cwr.ok:
                    catalyst_winrate_str = (
                        f"\n  📰 Catalyst `{catalyst_type}` historical win rate: "
                        f"**{cwr.value:.0%}** (n={cwr.n})"
                    )
                    paper1_metrics["catalyst_winrate"] = cwr.value
                    paper1_metrics["catalyst_winrate_n"] = cwr.n
        except Exception as e:
            logger.debug("distribution_cache query failed: %s", e)

    # Build embed
    descr_lines = [f"⏰ {_now_et_str()}"]
    descr_lines.append(f"**ENTRY:** {qty} shares @ ${entry_price:.2f}, stop ${stop_loss:.2f}")
    if mfcs is not None:
        descr_lines.append(f"**MFCS:** {mfcs:.3f}{mfcs_pct_str}")
    descr_lines.append(f"**Path:** {path}" + (f" | Kelly tier {kelly_tier}" if kelly_tier else ""))
    if catalyst_type:
        descr_lines.append(f"**Catalyst:** {catalyst_type}")
    if agent_lines:
        descr_lines.append("\n**Agent verdicts:**")
        descr_lines.extend(agent_lines)
    if mfcs_winrate_str or catalyst_winrate_str:
        descr_lines.append("\n**Statistical context:**" + mfcs_winrate_str + catalyst_winrate_str)
    if cascade_warning:
        descr_lines.append(f"\n{cascade_warning}")
    descr_lines.append(_paper1_footer(paper1_metrics))

    payload = {"embeds": [{
        "title": f"📈 BUY {ticker}",
        "description": "\n".join(descr_lines),
        "color": _COLOR_GREEN,
        "footer": {"text": "Momentum-X | Decision Learning | Trade Open"},
    }]}
    await _post_async(payload)


# ── Tier 1 + 3: trade-close enriched (with Shapley) ──────────────────


async def post_trade_close_enriched(
    *,
    ticker: str,
    qty: int,
    entry_price: float,
    exit_price: float,
    pnl: float,
    exit_reason: str,
    hold_minutes: float | None,
    mfcs_at_entry: float | None,
    agent_component_scores: dict[str, float] | None = None,
    shapley_attribution: dict[str, float] | None = None,
    debate_triggered: bool = False,
    risk_score: float | None = None,
    catalyst_type: str | None = None,
    distribution_cache: Any = None,
) -> None:
    """Trade-close message with Shapley breakdown + post-trade learning.

    Shapley attribution shows which agent category carried the trade and
    which (if any) was a drag. Statistical context shows whether this
    outcome was expected at this MFCS level.
    """
    if not is_enabled():
        return

    is_win = pnl > 0
    color = _COLOR_GREEN if is_win else _COLOR_RED
    title_emoji = "✅" if is_win else "❌"
    pnl_pct = (pnl / (entry_price * qty)) * 100 if entry_price * qty > 0 else 0.0

    # Mechanical outcome
    descr_lines = [f"⏰ {_now_et_str()}"]
    descr_lines.append(
        f"**OUTCOME:** {qty} shares @ ${entry_price:.2f} → ${exit_price:.2f}  "
        f"= **${pnl:+,.2f}** ({pnl_pct:+.1f}%)"
    )
    if hold_minutes is not None:
        descr_lines.append(f"**Hold:** {hold_minutes:.1f} min  |  **Exit:** {exit_reason}")
    else:
        descr_lines.append(f"**Exit:** {exit_reason}")

    # Shapley attribution table (Tier 1 core deliverable)
    paper1_metrics: dict[str, Any] = {
        "ticker": ticker, "qty": qty, "pnl": pnl, "pnl_pct": pnl_pct / 100,
        "entry_price": entry_price, "exit_price": exit_price,
        "exit_reason": exit_reason, "hold_minutes": hold_minutes,
        "mfcs_at_entry": mfcs_at_entry, "is_win": is_win,
        "debate_triggered": debate_triggered, "risk_score": risk_score,
        "catalyst_type": catalyst_type,
    }
    if shapley_attribution:
        # Sort by absolute contribution
        sorted_attr = sorted(
            shapley_attribution.items(), key=lambda kv: -abs(kv[1] or 0.0)
        )
        attr_lines = ["\n**Shapley attribution (per category):**"]
        for cat, val in sorted_attr[:6]:
            if val is None:
                continue
            sign = "+" if val >= 0 else ""
            attr_lines.append(f"  • `{cat:18s}` {sign}{val:+.4f}")
        descr_lines.extend(attr_lines)
        paper1_metrics["shapley_attribution"] = {
            k: round(v, 6) for k, v in shapley_attribution.items() if v is not None
        }
    elif agent_component_scores:
        # Fallback: raw component scores (pre-Shapley)
        sorted_scores = sorted(
            agent_component_scores.items(), key=lambda kv: -abs(kv[1] or 0.0)
        )
        score_lines = ["\n**Agent component scores (pre-Shapley):**"]
        for cat, val in sorted_scores[:6]:
            if val is None:
                continue
            score_lines.append(f"  • `{cat:18s}` {val:+.4f}")
        descr_lines.extend(score_lines)
        paper1_metrics["agent_component_scores"] = {
            k: round(v, 6) for k, v in agent_component_scores.items() if v is not None
        }

    # Statistical context (Tier 3)
    if distribution_cache is not None and mfcs_at_entry is not None:
        try:
            wr = distribution_cache.mfcs_historical_winrate(mfcs_at_entry)
            if wr.ok:
                expected = "expected" if (wr.value >= 0.5) == is_win else "**unexpected**"
                descr_lines.append(
                    f"\n**Calibration:** historical win rate at this MFCS was "
                    f"{wr.value:.0%}; this outcome was {expected} (n={wr.n})"
                )
                paper1_metrics["mfcs_winrate_at_level"] = wr.value
                paper1_metrics["calibration_expected"] = (wr.value >= 0.5) == is_win
        except Exception as e:
            logger.debug("distribution_cache calibration query failed: %s", e)

    # Trade context flags
    flags = []
    if debate_triggered:
        flags.append("debate_triggered")
    if risk_score is not None and risk_score > 0.6:
        flags.append(f"high_risk_at_entry={risk_score:.2f}")
    if flags:
        descr_lines.append(f"\n**Flags:** {', '.join(flags)}")

    descr_lines.append(_paper1_footer(paper1_metrics))

    payload = {"embeds": [{
        "title": f"{title_emoji} CLOSED {ticker} — {'WIN' if is_win else 'LOSS'}",
        "description": "\n".join(descr_lines),
        "color": color,
        "footer": {"text": "Momentum-X | Decision Learning | Trade Close"},
    }]}
    await _post_async(payload)


# ── Tier 2: DISAGREE_SHADOW_BUYS surface ─────────────────────────────


async def post_disagree_shadow_buy(
    *,
    ticker: str,
    composite_score: float,
    production_decision: str,
    rejection_code: str | None,
    rejection_reason: str | None = None,
    mfcs: float | None = None,
    agent_signals: list[Any] | None = None,
    distribution_cache: Any = None,
) -> bool:
    """Surface a case where composite_shadow says BUY but production blocked.

    Returns True if a message was sent, False if filtered (below threshold,
    trusted gate, or webhook unset). Caller can use the boolean for metrics.

    The exact signal we want to study per the v2 thesis:
    cascade-anti-selection means these are exactly the candidates where
    composite is right and cascade is wrong. Every message becomes a
    real-time observation for Paper 1's effect-size table.
    """
    if not is_enabled():
        return False
    if composite_score < _DISAGREE_THRESHOLD:
        return False
    if is_trusted_gate(rejection_code) or is_trusted_gate(rejection_reason):
        return False

    # Per-agent compact verdict table
    agent_lines = []
    if agent_signals:
        for sig in agent_signals[:6]:
            try:
                agent_id = (
                    sig.get("agent_id") if isinstance(sig, dict)
                    else getattr(sig, "agent_id", "?")
                )
                signal = (
                    sig.get("signal") if isinstance(sig, dict)
                    else getattr(sig, "signal", "?")
                )
                agent_lines.append(f"  • `{agent_id:22s}` {signal}")
            except Exception:  # noqa: silent-handler
                # One malformed agent_signal entry must not break the
                # whole DISAGREE message; skip and continue.
                continue

    consensus = _consensus_strength_from_signals(agent_signals or [])
    cascade_warning = _format_cascade_warning(consensus)

    paper1_metrics: dict[str, Any] = {
        "ticker": ticker,
        "composite_score": composite_score,
        "production_decision": production_decision,
        "rejection_code": rejection_code,
        "mfcs": mfcs,
        "consensus_strength": consensus,
        "kind": "DISAGREE_SHADOW_BUYS",
    }

    # Statistical context: catalyst winrate if known
    catalyst_str = ""
    if agent_signals and distribution_cache is not None:
        try:
            for sig in agent_signals:
                aid = sig.get("agent_id") if isinstance(sig, dict) else getattr(sig, "agent_id", "")
                if aid == "news_agent":
                    kd = sig.get("key_data") if isinstance(sig, dict) else getattr(sig, "key_data", {})
                    if isinstance(kd, dict):
                        ct = kd.get("catalyst_type")
                        if isinstance(ct, str):
                            cwr = distribution_cache.catalyst_winrate(ct)
                            if cwr.ok:
                                catalyst_str = (
                                    f"\n  📰 Catalyst `{ct}` historical win rate: "
                                    f"**{cwr.value:.0%}** (n={cwr.n})"
                                )
                                paper1_metrics["catalyst_type"] = ct
                                paper1_metrics["catalyst_winrate"] = cwr.value
                    break
        except Exception:  # noqa: silent-handler
            # Optional statistical context lookup; absence is fine.
            pass

    descr_lines = [
        f"⏰ {_now_et_str()}",
        f"**Composite shadow score:** {composite_score:.3f} (≥ {_DISAGREE_THRESHOLD} threshold)",
        f"**Production verdict:** {production_decision}",
        f"**Rejected by:** `{rejection_code or rejection_reason or 'unknown gate'}`",
    ]
    if mfcs is not None:
        descr_lines.append(f"**MFCS:** {mfcs:.3f}")
    if agent_lines:
        descr_lines.append("\n**Agent verdicts:**")
        descr_lines.extend(agent_lines)
    if catalyst_str:
        descr_lines.append(catalyst_str)
    if cascade_warning:
        descr_lines.append(f"\n{cascade_warning}")
    descr_lines.append(
        f"\n_Watch this ticker post-9:31. Composite says winner; production says no. "
        f"This is the exact slice the v2 cascade-anti-selection thesis predicts._"
    )
    descr_lines.append(_paper1_footer(paper1_metrics))

    payload = {"embeds": [{
        "title": f"⚖ DISAGREE: {ticker}  |  composite says BUY, production blocked",
        "description": "\n".join(descr_lines),
        "color": _COLOR_PURPLE,
        "footer": {"text": "Momentum-X | Decision Learning | Cascade Disagreement"},
    }]}
    await _post_async(payload)
    return True


# ── Tier 3: session-summary enriched ─────────────────────────────────


async def post_session_summary_enriched(
    *,
    trades_count: int,
    pnl: float,
    wins: int,
    losses: int,
    distribution_cache: Any = None,
    cascade_stats: dict[str, Any] | None = None,
) -> None:
    """End-of-day summary with calibration check and distribution snapshot."""
    if not is_enabled():
        return

    win_rate = (wins / trades_count) if trades_count > 0 else 0.0
    color = _COLOR_GREEN if pnl > 0 else (_COLOR_RED if pnl < 0 else _COLOR_GOLD)

    descr_lines = [f"⏰ {_now_et_str()}"]
    descr_lines.append(
        f"**Trades:** {trades_count}  |  **W/L:** {wins}/{losses}  |  "
        f"**Win rate:** {win_rate:.0%}"
    )
    descr_lines.append(f"**Net P&L:** ${pnl:+,.2f}")

    paper1_metrics: dict[str, Any] = {
        "trades_count": trades_count, "pnl": pnl, "wins": wins, "losses": losses,
        "win_rate": win_rate,
        "kind": "SESSION_SUMMARY",
    }

    # Distribution snapshot
    if distribution_cache is not None:
        try:
            stats = distribution_cache.stats()
            descr_lines.append(
                f"\n**Distribution cache:** {stats['n_mfcs_observations']} historical "
                f"MFCS observations, {stats['n_mfcs_with_outcome']} with outcomes"
            )
            paper1_metrics["distribution_cache_size"] = stats["n_mfcs_observations"]
        except Exception:  # noqa: silent-handler
            # Distribution cache snapshot is purely informational; if it
            # fails, session summary still ships without the stats line.
            pass

    # Cascade statistics for the day
    if cascade_stats:
        descr_lines.append(
            f"\n**Cascade today:** "
            f"agree_buy={cascade_stats.get('agree_buy', 0)}, "
            f"disagree_shadow_buys={cascade_stats.get('disagree_shadow_buys', 0)}, "
            f"agree_no_trade={cascade_stats.get('agree_no_trade', 0)}"
        )
        paper1_metrics["cascade_stats"] = cascade_stats

    descr_lines.append(_paper1_footer(paper1_metrics))

    payload = {"embeds": [{
        "title": f"📊 Session summary: {trades_count} trades, ${pnl:+,.2f}",
        "description": "\n".join(descr_lines),
        "color": color,
        "footer": {"text": "Momentum-X | Decision Learning | Session End"},
    }]}
    await _post_async(payload)
