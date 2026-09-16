"""
MOMENTUM-X Orchestrator

### ARCHITECTURAL CONTEXT
Node ID: core.orchestrator
Graph Link: docs/memory/graph_state.json → "core.orchestrator"

### RESEARCH BASIS
Parallel fan-out with debate synthesis per ADR-001.
Pipeline: Scanner → 6 Agents (parallel) → MFCS Scoring → Debate (if qualified) → Risk Veto → Execution.
Full pipeline must complete in <90s per candidate (ADR-001 latency budget).

### CRITICAL INVARIANTS
1. Risk Agent VETO blocks execution unconditionally (INV-008).
2. Debate only triggers if MFCS > threshold (MOMENTUM_LOGIC.md §5, default 0.6).
3. Max 3 concurrent positions (INV-009, config).
4. Paper trading is the default mode (INV-007).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.settings import Settings
from src.core.adaptive_router import AdaptiveComputeRouter, EvalTier
from src.core.kelly_tier import KellyTierClassifier, KellyTierResult
from src.monitoring.metrics import get_metrics
from src.core.models import (
    AgentSignal,
    CandidateStock,
    DebateResult,
    ManipulationSignal,
    RiskSignal,
    ScoredCandidate,
    TradeVerdict,
)
from src.utils.trade_logger import (
    TradeContext,
    generate_trade_id,
    get_trade_logger,
    set_trade_context,
    clear_trade_context,
)
from src.core.scoring import compute_mfcs, signal_to_score
from src.execution.exit_intelligence import compute_atr
from src.experiments.registry import ExperimentRegistry
from src.experiments.journal import ExperimentJournal
from src.experiments.models import ExperimentJournalEntry, VariantResult
from src.agents.base import BaseAgent
from src.agents.news_agent import NewsAgent
from src.agents.fundamental_agent import FundamentalAgent
from src.agents.institutional_agent import InstitutionalAgent
from src.agents.deep_search_agent import DeepSearchAgent
# D221 Phase F: LLM-based TechnicalAgent and RiskAgent removed.
# Production has used DeterministicTechnicalAgent and DeterministicRiskAgent
# since D101 (commit 2b11f53). The LLM versions were retained as dead code
# until 2026-04-19, when they were deleted to prevent future "is this
# still live?" confusion. See git history for the prior implementations.
from src.agents.deterministic_risk import DeterministicRiskAgent
from src.agents.deterministic_technical import DeterministicTechnicalAgent
from src.agents.manipulation_classifier import ManipulationClassifier
from src.agents.debate_engine import DebateEngine
from src.data.universe_tiers import UniverseTier, universe_classifier as _universe_classifier

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Main pipeline coordinator for the Momentum-X system.

    Node ID: core.orchestrator
    Graph Link: docs/memory/graph_state.json → "core.orchestrator"

    Manages the full signal pipeline:
    1. Receives CandidateStock from scanners
    2. Dispatches to analytical agents (D92: staggered asyncio.wait for partial results)
    3. Computes MFCS via scoring engine
    4. Triggers debate engine for qualified candidates
    5. Runs risk agent veto check
    6. Produces TradeVerdict for execution layer

    Ref: ADR-001 (Multi-Agent Debate Architecture)
    Ref: SYSTEM_ARCHITECTURE.md (Data Flow)
    """

    def __init__(
        self,
        settings: Settings,
        websocket_client: Any | None = None,
        sec_client: Any | None = None,
        prompt_arena: Any | None = None,
        options_provider: Any | None = None,
        data_client: Any | None = None,
        premarket_atr: dict[str, float] | None = None,
        trade_tracker: Any | None = None,  # D115: TradeResultTracker for Kelly win rates
        position_manager: Any | None = None,  # D115: PositionManager for Kelly open positions
        instrumentation_writer: Any | None = None,  # Bug AO (Tier 4 #15): DecisionRow capture
    ) -> None:
        self._settings = settings
        self._ws_client = websocket_client  # H-006: Real VWAP from streaming
        self._sec_client = sec_client  # H-004: SEC EDGAR dilution detection
        self._prompt_arena = prompt_arena  # H-003: Elo-rated prompt selection
        self._options_provider = options_provider  # D40: Live options chain for institutional agent
        self._data_client = data_client  # D100: Alpaca data client for ATR-based stops
        self._position_manager = position_manager  # D115: For Kelly open positions + portfolio heat
        # Bug AO (Tier 4 #15, 2026-04-27): InstrumentationWriter for
        # DecisionRow capture on every orchestrator verdict. Enables
        # rigorous counterfactual replay of any future calibration
        # change. None-tolerant: trading proceeds without capture if
        # the writer isn't wired. See docs/research-log/54_bug_ao_*.md.
        self._instrumentation = instrumentation_writer
        # Per-session evaluation cycle counter (used by DecisionRow.cycle_number)
        self._evaluation_cycle_count: int = 0
        # D104: Pre-market ATR bridge — Phase 0 already computes ATR_14 for
        # each ticker in premarket_research.py. Reuse instead of redundant API call.
        self._premarket_atr: dict[str, float] = premarket_atr or {}
        # D104: Macro regime state (VIX delta, SPY return)
        # D128: Refresh every 5 min instead of caching forever. Mar 27: stale
        # SPY=-1.40% from daily bars blocked all trading for entire session.
        self._vix_delta_pct: float | None = None
        # D211: VIX extra reduction is now a LOCAL variable in _evaluate_candidate_inner
        # to prevent race condition in concurrent evaluation (third-pass audit fix)
        self._spy_return_pct: float | None = None
        self._spy_last_fetch: datetime | None = None
        self._vix_last_fetch: datetime | None = None

        # D112: Adaptive Compute Router — three-tier evaluation depth routing
        if settings.router.enabled:
            self._router: AdaptiveComputeRouter | None = AdaptiveComputeRouter(
                config=settings.router,
                max_entry_spread_pct=settings.execution.max_entry_spread_pct,
            )
        else:
            self._router = None

        # D115: Tiered Kelly Criterion position sizing
        # Always instantiate for shadow-mode logging; .enabled controls actual sizing
        self._kelly_classifier = KellyTierClassifier(
            config=settings.kelly_tier,
            trade_tracker=trade_tracker,
            daily_loss_limit_pct=settings.execution.daily_loss_limit_pct,
        )

        # D87: Feature logger for ML training data
        try:
            from src.analysis.feature_logger import FeatureLogger
            self._feature_logger = FeatureLogger()
        except Exception:
            self._feature_logger = None
        self._last_variant_map: dict[str, str] = {}  # Track arena selections per trade
        self._last_data_report: dict[str, dict[str, Any]] = {}  # Data completeness per agent
        self._last_agent_signals: list[AgentSignal] = []  # Last evaluation's agent signals (for recording)
        # D83: Per-candidate signal storage to prevent signal bleeding across tickers.
        # Previously _last_agent_signals held only the LAST candidate's signals,
        # causing all candidates to share MCW's M&A signal in journal output.
        self._signals_by_ticker: dict[str, list[AgentSignal]] = {}
        self._scoring_by_ticker: dict[str, dict[str, Any]] = {}
        self._scored_by_ticker: dict[str, ScoredCandidate] = {}  # D121 BUG: Cache for bridge.execute_verdict
        self._data_report_by_ticker: dict[str, dict[str, Any]] = {}
        self._variant_map_by_ticker: dict[str, dict[str, str]] = {}
        # D61: Expose MFCS scoring for journal recording
        self._last_scored_mfcs: float = 0.0
        self._last_scored_components: dict[str, float] = {}
        self._last_scored_risk: float = 0.0
        self._last_scored_qualifies_debate: bool = False
        # D97: Per-ticker debate attempt counter to enforce budget
        self._debate_attempts: dict[str, int] = {}
        # D100: Per-ticker ATR cache (fetched once per session, 14-day daily ATR)
        self._atr_cache: dict[str, float | None] = {}
        # D101 §2.4: VIX cache (fetched once per session — VIX doesn't change dramatically intraday)
        self._vix_level: float | None = None
        # D106: Pre-market MFCS cache — stores fast-path 5-agent scores for
        # smart cache bypass at market open. Key = ticker, value = (mfcs, signal_price,
        # score_time, cached_llm_signals). At 9:30, skip LLM re-dispatch when price
        # stable (<3% move) and cache fresh (<15 min). Re-run only deterministic
        # agents with real market data.
        self._premarket_mfcs_cache: dict[
            str, tuple[float, float, datetime, list[AgentSignal]]
        ] = {}

        # ── Initialize agents per ADR-001 Model Tiering ──
        # D92: Tier-specific timeouts — much lower for instruct models
        # Qwen3.5 @ ~135 tok/s: 15s. Qwen3 Instruct @ ~165 tok/s: 10s.
        tier1_timeout = settings.models.litellm_timeout_tier1
        tier2_timeout = settings.models.litellm_timeout_tier2

        # D92: Three-tier fallback chain per model tier
        t1_fb_model = settings.models.tier1_fallback_model
        t1_fb_provider = settings.models.tier1_fallback_provider
        t2_fb_model = settings.models.tier2_fallback_model
        t2_fb_provider = settings.models.tier2_fallback_provider
        em_model = settings.models.emergency_model
        em_provider = settings.models.emergency_provider

        # D92: Common kwargs for Tier 1 agents (reasoning)
        _t1_kwargs = dict(
            model=settings.models.tier1_model,
            provider=settings.models.tier1_provider,
            temperature=settings.models.default_temperature,
            timeout=tier1_timeout,
            fallback_model=t1_fb_model,
            fallback_provider=t1_fb_provider,
            emergency_model=em_model,
            emergency_provider=em_provider,
        )
        # D92: Common kwargs for Tier 2 agents (extraction)
        _t2_kwargs = dict(
            model=settings.models.tier2_model,
            provider=settings.models.tier2_provider,
            temperature=settings.models.default_temperature,
            timeout=tier2_timeout,
            fallback_model=t2_fb_model,
            fallback_provider=t2_fb_provider,
            emergency_model=em_model,
            emergency_provider=em_provider,
        )

        # D101: Risk agent is now DETERMINISTIC — zero LLM calls, zero failure rate.
        # The LLM-based RiskAgent had 40% failure rate which is catastrophic for the
        # one function that must NEVER fail. All risk checks (spread, dilution,
        # bankruptcy, float, RVOL) are numeric computations computable in <1ms.
        self._risk_agent = DeterministicRiskAgent()

        # D101: Technical agent is now DETERMINISTIC — pandas_ta indicators + rules.
        # FINSABER (arXiv:2505.07078) showed LLM trading agents generated no
        # statistically significant alpha. RSI, MACD, Bollinger, EMA are computed
        # in microseconds with 0% failure rate.
        self._technical_agent = DeterministicTechnicalAgent()

        # Tier 2 (D92: Qwen3 235B A22B Instruct): Structured extraction agents
        # D92b: News agent moved to Tier 2 — headline classification and catalyst
        # extraction is structured extraction, not deep reasoning. Simulation testing
        # showed Tier 1 (Qwen3.5 397B) consistently times out for news_agent while
        # Tier 2 (Qwen3 235B) completes in 3-11s. News quality is maintained because
        # the task is pattern matching (FDA? Earnings? Offering?), not reasoning.
        self._news_agent = NewsAgent(**_t2_kwargs)
        self._fundamental_agent = FundamentalAgent(**_t2_kwargs)
        self._institutional_agent = InstitutionalAgent(**_t2_kwargs)
        self._deep_search_agent = DeepSearchAgent(**_t2_kwargs)

        # D202: Ensemble wrapper — calls each LLM agent N times in parallel.
        # D201-E8: 47% verdict inconsistency without ensemble. Majority vote +
        # confidence averaging dramatically reduces noise.
        # Only wraps LLM-based agents. Deterministic agents (technical, risk)
        # are already stable and don't benefit from ensembling.
        try:
            _ensemble_enabled = bool(settings.models.ensemble_enabled)
            _en = int(settings.models.ensemble_n_calls)
            _eq = int(settings.models.ensemble_min_quorum)
        except (TypeError, ValueError, AttributeError):
            _ensemble_enabled = False
            _en = 1
            _eq = 1
        if _ensemble_enabled and _en > 1:
            from src.agents.ensemble import EnsembleWrapper
            # D218: Only wrap agents with non-zero weight. Zero-weight agents
            # are skipped at dispatch (line 2118) but wrapping them in ensemble
            # creates unnecessary objects and obscures the skip in logs.
            _ensemble_weight_map = {
                "news": settings.scoring.catalyst_news,
                "fundamental": settings.scoring.float_structure,
                "institutional": settings.scoring.institutional,
                "deep_search": settings.scoring.deep_search,
            }
            _wrapped = []
            _skipped_ensemble = []
            if _ensemble_weight_map["news"] > 0:
                self._news_agent = EnsembleWrapper(self._news_agent, n_calls=_en, min_quorum=_eq)
                _wrapped.append("news")
            else:
                _skipped_ensemble.append("news")
            if _ensemble_weight_map["fundamental"] > 0:
                self._fundamental_agent = EnsembleWrapper(self._fundamental_agent, n_calls=_en, min_quorum=_eq)
                _wrapped.append("fundamental")
            else:
                _skipped_ensemble.append("fundamental")
            if _ensemble_weight_map["institutional"] > 0:
                self._institutional_agent = EnsembleWrapper(self._institutional_agent, n_calls=_en, min_quorum=_eq)
                _wrapped.append("institutional")
            else:
                _skipped_ensemble.append("institutional")
            if _ensemble_weight_map["deep_search"] > 0:
                self._deep_search_agent = EnsembleWrapper(self._deep_search_agent, n_calls=_en, min_quorum=_eq)
                _wrapped.append("deep_search")
            else:
                _skipped_ensemble.append("deep_search")
            logger.info(
                "D202: Ensemble enabled — %dx calls (quorum=%d). "
                "Wrapped: [%s]. Skipped (weight=0): [%s]",
                _en, _eq, ", ".join(_wrapped) or "none",
                ", ".join(_skipped_ensemble) or "none",
            )

        # D106 §2E: Manipulation classifier — Tier 1 (Qwen3.5-397B).
        # This is the hardest reasoning task in the pipeline: synthesizes filing
        # history, catalyst credibility, volume patterns into phase classification.
        # Harder than news agent. Extra ~10s latency irrelevant (runs in parallel).
        # Does NOT contribute to MFCS — only gates entry parameters.
        self._manipulation_classifier = ManipulationClassifier(**_t1_kwargs)

        # D218: Log agent dispatch decisions at startup — which agents are active,
        # which are skipped (zero-weight), and their weights. This makes the
        # LLM cost profile visible in every session log.
        _d218_agent_status = {
            "news": (settings.scoring.catalyst_news, "active" if settings.scoring.catalyst_news > 0 else "SKIPPED"),
            "technical": (settings.scoring.technical, "active" if settings.scoring.technical > 0 else "SKIPPED"),
            "fundamental": (settings.scoring.float_structure, "active" if settings.scoring.float_structure > 0 else "SKIPPED"),
            "institutional": (settings.scoring.institutional, "active" if settings.scoring.institutional > 0 else "SKIPPED"),
            "deep_search": (settings.scoring.deep_search, "active" if settings.scoring.deep_search > 0 else "SKIPPED"),
            "risk": (settings.scoring.risk_aversion_lambda, "active (lambda)"),
            "manipulation": (1.0, "active (always)"),
        }
        _d218_active = [f"{k}({v[0]:.2f})" for k, v in _d218_agent_status.items() if "active" in v[1]]
        _d218_skipped = [k for k, v in _d218_agent_status.items() if v[1] == "SKIPPED"]
        logger.info(
            "D218 AGENT DISPATCH: active=[%s] skipped=[%s] — "
            "skipped agents make ZERO LLM calls, weight redistributed to active agents",
            ", ".join(_d218_active),
            ", ".join(_d218_skipped) or "none",
        )

        # Debate engine: D29 (divergence config), D32 (advocate model)
        # D92b: Debate prompts are 2-3x longer than extraction prompts (full context
        # + arguments). Simulation showed Tier 2 15s timeout consistently fails for
        # debate advocates. Use Tier 1 timeout for BOTH judge AND advocates in debate
        # since the prompt length, not the model quality, drives the latency.
        self._debate_engine = DebateEngine(
            model=f"{settings.models.tier1_provider}/{settings.models.tier1_model}",
            advocate_model=f"{settings.models.tier2_provider}/{settings.models.tier2_model}",
            temperature=settings.models.default_temperature,
            timeout=tier1_timeout,
            advocate_timeout=tier1_timeout,  # D92b: same as judge — debate prompts are long
            divergence_no_trade=settings.debate.divergence_low_threshold,
            divergence_full=settings.debate.divergence_high_threshold,
            fallback_model=f"{t1_fb_provider}/{t1_fb_model}" if t1_fb_model else "",
            fallback_advocate_model=f"{t2_fb_provider}/{t2_fb_model}" if t2_fb_model else "",
            gather_timeout=settings.ops.debate_gather_timeout_seconds,
        )

        # ── D102: Experiment framework — parameter replay ──
        self._experiment_registry = ExperimentRegistry()
        if settings.experiments.enabled:
            try:
                yaml_path = Path(settings.experiments.yaml_path)
                if yaml_path.exists():
                    self._experiment_registry.load(yaml_path)
                else:
                    logger.info(
                        "D102: Experiment YAML not found at %s — experiments disabled",
                        yaml_path,
                    )
            except Exception as e:
                logger.warning("D102: Experiment config load failed (non-fatal): %s", e)
        self._experiment_journal = ExperimentJournal(
            journal_dir=Path(settings.experiments.journal_dir),
        )

    def cache_premarket_mfcs(
        self,
        ticker: str,
        mfcs: float,
        signal_price: float,
        cached_signals: list[AgentSignal] | None = None,
    ) -> None:
        """
        D106: Store fast-path 5-agent MFCS for smart cache bypass at market open.

        Called by main.py after FastPathScorer scores candidates at 9:20.
        At 9:30 in _evaluate_candidate_inner(), if the cache is valid
        (price < 3% move, < 15 min stale), LLM re-dispatch is skipped.
        Only deterministic agents (Technical + Risk) are re-run with real data.

        Args:
            ticker: Stock ticker symbol
            mfcs: The 5-agent MFCS score from fast-path
            signal_price: Price at scoring time (for staleness guard)
            cached_signals: The LLM agent signals from fast-path (for reuse)
        """
        self._premarket_mfcs_cache[ticker] = (
            mfcs,
            signal_price,
            datetime.now(timezone.utc),
            cached_signals or [],
        )
        logger.info(
            "D106: Cached premarket MFCS for %s: %.3f @ $%.2f (%d signals)",
            ticker, mfcs, signal_price, len(cached_signals or []),
        )

    def wrap_agents_for_replay(self, agent_caches: dict[str, dict]) -> None:
        """
        Wrap all analytical agents with CachedAgentWrapper in REPLAY mode.

        For deterministic backtesting: wraps each agent so that
        analyze() returns cached responses instead of calling the LLM API.

        Args:
            agent_caches: Dict of agent_id → cache dict.
                Each cache dict maps cache_key → serialized AgentSignal.

        Usage:
            caches = json.loads(Path("data/agent_cache.json").read_text())
            orchestrator.wrap_agents_for_replay(caches)
            # Now evaluate_candidate() uses cached responses — zero API calls

        Ref: ADR-016 (D2: CachedAgentWrapper)
        """
        from src.agents.cached_wrapper import CachedAgentWrapper

        agent_map = {
            "news_agent": "_news_agent",
            "technical_agent": "_technical_agent",
            "fundamental_agent": "_fundamental_agent",
            "institutional_agent": "_institutional_agent",
            "deep_search_agent": "_deep_search_agent",
            "risk_agent": "_risk_agent",
            "manipulation_classifier": "_manipulation_classifier",
        }

        for agent_id, attr_name in agent_map.items():
            cache = agent_caches.get(agent_id, {})
            current_agent = getattr(self, attr_name)
            # D202 FIX: If agent is already wrapped by EnsembleWrapper,
            # unwrap to the inner agent before wrapping with CachedAgentWrapper.
            # Prevents double-wrapping chain: EnsembleWrapper → CachedAgentWrapper
            # which would break signal extraction in replay mode.
            try:
                from src.agents.ensemble import EnsembleWrapper
                if isinstance(current_agent, EnsembleWrapper):
                    current_agent = current_agent._agent
                    logger.info(
                        "D202: Unwrapped %s from EnsembleWrapper for replay mode",
                        agent_id,
                    )
            except ImportError:
                pass
            wrapper = CachedAgentWrapper(
                agent=current_agent,
                mode="replay",
                cache=cache,
            )
            setattr(self, attr_name, wrapper)
            logger.info(
                "Wrapped %s with CachedAgentWrapper (REPLAY, %d entries)",
                agent_id, len(cache),
            )

    def wrap_agents_for_recording(self) -> dict[str, Any]:
        """
        Wrap all analytical agents with CachedAgentWrapper in RECORD mode.

        For capturing live responses to build a replay cache.
        Returns the wrapper dict so caches can be saved after the session.

        Returns:
            Dict of agent_id → CachedAgentWrapper (in RECORD mode).

        Ref: ADR-016 (D2: CachedAgentWrapper)
        """
        from src.agents.cached_wrapper import CachedAgentWrapper

        agent_map = {
            "news_agent": "_news_agent",
            "technical_agent": "_technical_agent",
            "fundamental_agent": "_fundamental_agent",
            "institutional_agent": "_institutional_agent",
            "deep_search_agent": "_deep_search_agent",
            "risk_agent": "_risk_agent",
            "manipulation_classifier": "_manipulation_classifier",
        }

        wrappers: dict[str, Any] = {}
        for agent_id, attr_name in agent_map.items():
            current_agent = getattr(self, attr_name)
            # D202 FIX: Unwrap EnsembleWrapper for recording mode
            try:
                from src.agents.ensemble import EnsembleWrapper
                if isinstance(current_agent, EnsembleWrapper):
                    current_agent = current_agent._agent
                    logger.info("D202: Unwrapped %s from EnsembleWrapper for record mode", agent_id)
            except ImportError:
                pass
            wrapper = CachedAgentWrapper(
                agent=current_agent,
                mode="record",
            )
            setattr(self, attr_name, wrapper)
            wrappers[agent_id] = wrapper
            logger.info("Wrapped %s with CachedAgentWrapper (RECORD)", agent_id)

        return wrappers

    def seed_premarket_atr(self, atr_data: dict[str, float]) -> None:
        """
        D104: Inject ATR values from Phase 0 pre-market research cache.

        Phase 0 already computes ATR_14 for each ticker in the watchlist.
        By seeding the orchestrator's cache, we avoid a redundant API call
        that may fail (as happened with NVTS on Day 1 — 0 bars returned).

        Args:
            atr_data: Mapping of ticker → ATR_14 value (e.g. {"NVTS": 0.82}).
        """
        for ticker, atr_val in atr_data.items():
            if atr_val and atr_val > 0:
                self._premarket_atr[ticker] = atr_val
        if self._premarket_atr:
            logger.info(
                "D104: Seeded %d pre-market ATR values: %s",
                len(self._premarket_atr),
                {k: round(v, 4) for k, v in self._premarket_atr.items()},
            )

    async def evaluate_candidate(
        self,
        candidate: CandidateStock,
        news_items: list | None = None,
        market_data: dict[str, Any] | None = None,
        sec_filings: dict[str, Any] | None = None,
    ) -> TradeVerdict:
        """
        Run the full evaluation pipeline for a single candidate.

        Pipeline:
            Agents (parallel) → MFCS → Debate (conditional) → Risk → Verdict

        Args:
            candidate: CandidateStock from scanner
            news_items: Pre-fetched news for this ticker
            market_data: Current market data (price, spread, volume)
            sec_filings: Recent SEC filings if available

        Returns:
            TradeVerdict with action, sizing, levels, and full reasoning chain

        Ref: SYSTEM_ARCHITECTURE.md (Data Flow)
        """
        pipeline_start = time.monotonic()
        ticker = candidate.ticker

        # ── Trade Context (ADR-008): Correlation ID for full pipeline tracing ──
        trade_id = generate_trade_id(ticker)
        ctx = TradeContext(trade_id=trade_id, ticker=ticker, phase="EVALUATION")
        set_trade_context(ctx)

        try:
            return await self._evaluate_candidate_inner(
                candidate=candidate,
                news_items=news_items,
                market_data=market_data,
                sec_filings=sec_filings,
                pipeline_start=pipeline_start,
                trade_id=trade_id,
            )
        finally:
            clear_trade_context()

    async def _evaluate_candidate_inner(
        self,
        candidate: CandidateStock,
        news_items: list | None,
        market_data: dict[str, Any] | None,
        sec_filings: dict[str, Any] | None,
        pipeline_start: float,
        trade_id: str,
    ) -> TradeVerdict:
        """
        Inner evaluation logic — separated for clean TradeContext lifecycle.

        TradeContext is set by evaluate_candidate() and cleared in its finally block.
        All logging within this method automatically includes trade_id.
        """
        ticker = candidate.ticker

        logger.info(
            "Evaluating %s | Gap: %.1f%% | RVOL: %.1fx",
            ticker, candidate.gap_pct * 100, candidate.rvol,
        )

        # D87: Corporate action filter — reject candidates with suspected splits
        if candidate.corporate_action_flag:
            logger.warning(
                "%s D87: CORPORATE ACTION detected (%s) — skipping evaluation. "
                "Gap=%.0f%% with no news = likely split/reverse-split.",
                ticker, candidate.corporate_action_flag,
                candidate.gap_pct * 100,
            )
            return self._build_no_trade_verdict(
                candidate,
                scored=None,
                debate=None,
                reason=f"D87: Corporate action filter ({candidate.corporate_action_flag})",
            )

        # D122: Filter stale news before agent dispatch (second defense after D117)
        if news_items:
            news_items = self._filter_stale_news(news_items)

        # D87: RVOL exhaustion warning (advisory, not blocking)
        if candidate.rvol_exhaustion:
            logger.warning(
                "%s D87: RVOL exhaustion flag (RVOL=%.1fx > 5.0) — "
                "potential reversal risk, agents will factor this in",
                ticker, candidate.rvol,
            )

        metrics = get_metrics()
        metrics.evaluations_total.inc()

        # D211: Per-candidate VIX reduction — LOCAL variable, not self, to prevent
        # race condition when evaluate_candidates runs asyncio.gather on multiple
        # candidates concurrently (third-pass audit finding)
        _d211_local_vix_reduction = 1.0

        # ── D101 §2.4 / D128: VIX Regime Filter ──
        # Daniel & Moskowitz (JFE) showed dynamic regime strategy doubled Sharpe.
        # D211: Tier-aware VIX gating. Panic (>=35): block ALL. Block (>20): MOMENTUM/UNKNOWN only.
        # Reduce (>15): half sizing. CATALYST tier allowed through block zone at reduced size.
        # D128: Refresh every 5 min (was cached once forever, same stale-data
        # bug as SPY gate that blocked all trading on Mar 27).
        _now_vix = datetime.now(timezone.utc)
        _vix_stale = (
            self._vix_level is None
            or self._vix_last_fetch is None
            or (_now_vix - self._vix_last_fetch).total_seconds() > 300
        )
        if _vix_stale and self._data_client is not None:
            self._vix_level = await self._fetch_vix()
            self._vix_last_fetch = _now_vix

        if self._vix_level is not None:
            if self._vix_level >= self._settings.scoring.vix_panic_threshold:
                # D211: True panic — block everything, including CATALYST tier
                logger.warning(
                    "%s D211 VIX PANIC BLOCK: VIX=%.1f >= %.0f — all entries halted",
                    ticker, self._vix_level,
                    self._settings.scoring.vix_panic_threshold,
                )
                return self._build_no_trade_verdict(
                    candidate, scored=None, debate=None,
                    reason=f"D211 VIX panic: VIX={self._vix_level:.1f} >= "
                    f"{self._settings.scoring.vix_panic_threshold:.0f}",
                )
            elif self._vix_level > self._settings.scoring.vix_block_threshold:
                # D211: High VIX (20-35) — tier-aware decision
                _dollar_vol = (
                    candidate.current_price * candidate.avg_daily_volume
                    if candidate.avg_daily_volume is not None
                    else 0.0
                )
                _d211_tier = _universe_classifier.classify(
                    ticker,
                    candidate.current_price,
                    candidate.market_cap,
                    candidate.gap_pct,
                    candidate.rvol,
                    _dollar_vol,
                )
                if _d211_tier == UniverseTier.CATALYST:
                    # Allow CATALYST tier with reduced size (sizing code picks up VIX > reduce_threshold)
                    _d211_local_vix_reduction = 1.0  # No extra reduction beyond VIX reduce
                    logger.info(
                        "%s D211 VIX CATALYST MODE: VIX=%.1f, tier=CATALYST — "
                        "allowing at 50%% size (VIX reduce threshold active)",
                        ticker, self._vix_level,
                    )
                else:
                    # D211: Allow MOMENTUM/UNKNOWN at heavily reduced sizing for data collection
                    # Paper trading: 25% of normal = 50% VIX reduce × 50% momentum penalty
                    _d211_local_vix_reduction = 0.50
                    logger.info(
                        "%s D211 VIX MOMENTUM REDUCED: VIX=%.1f > %.0f, tier=%s — "
                        "allowing at 25%% size (paper trading data collection)",
                        ticker, self._vix_level,
                        self._settings.scoring.vix_block_threshold, _d211_tier.value,
                    )
            elif self._vix_level > self._settings.scoring.vix_reduce_threshold:
                logger.info(
                    "%s D101 VIX CAUTION: VIX=%.1f > %.0f — position sizing will be halved",
                    ticker, self._vix_level,
                    self._settings.scoring.vix_reduce_threshold,
                )

        # ── D104: VIX Delta Shock Detection ──
        # Even when absolute VIX is moderate (e.g. 27), a large daily delta
        # (e.g. +35%) indicates sudden regime shock. Block all entries.
        if (self._vix_delta_pct is not None
                and abs(self._vix_delta_pct)
                > self._settings.scoring.vix_shock_threshold_pct):
            logger.warning(
                "%s D104 VIX SHOCK: VIXY delta=%+.1f%% > ±%.0f%% threshold "
                "— all entries blocked",
                ticker, self._vix_delta_pct,
                self._settings.scoring.vix_shock_threshold_pct,
            )
            return self._build_no_trade_verdict(
                candidate, scored=None, debate=None,
                reason=f"D104 VIX shock: VIXY delta={self._vix_delta_pct:+.1f}% "
                f"> ±{self._settings.scoring.vix_shock_threshold_pct:.0f}%",
            )

        # ── D104/D128: SPY Return Gate ──
        # On broad selloff days, momentum longs face severe macro headwinds.
        # D128: Refresh every 5 min. Mar 27: stale -1.40% from daily bars
        # blocked all trading while SPY recovered to -0.4% intraday.
        _now = datetime.now(timezone.utc)
        _spy_stale = (
            self._spy_return_pct is None
            or self._spy_last_fetch is None
            or (_now - self._spy_last_fetch).total_seconds() > 300
        )
        if _spy_stale and self._data_client is not None:
            self._spy_return_pct = await self._fetch_spy_return()
            self._spy_last_fetch = _now
        if (self._spy_return_pct is not None
                and self._spy_return_pct
                < self._settings.scoring.spy_halt_threshold_pct):
            logger.warning(
                "%s D104 SPY HALT: SPY return=%.2f%% < %.1f%% "
                "— momentum entries blocked",
                ticker, self._spy_return_pct,
                self._settings.scoring.spy_halt_threshold_pct,
            )
            return self._build_no_trade_verdict(
                candidate, scored=None, debate=None,
                reason=f"D104 SPY halt: SPY={self._spy_return_pct:.2f}% "
                f"< {self._settings.scoring.spy_halt_threshold_pct:.1f}%",
            )

        # ── D112: Adaptive Compute Router ──
        # Three-tier evaluation depth routing. Classifies candidates BEFORE
        # agent dispatch to skip LLM evaluation for obvious rejects and
        # high-confidence deterministic cases. Inserted after macro regime
        # filters (VIX/SPY) but before agent dispatch.
        if self._router is not None:
            _router_decision = self._router.classify(
                candidate=candidate,
                sec_filings=sec_filings,
                news_items=news_items,
                deterministic_mfcs=None,  # Quick score computed below for Tier 2
            )

            if _router_decision.tier == EvalTier.INSTANT_REJECT:
                logger.info(
                    "%s D112 INSTANT_REJECT: %s",
                    ticker, _router_decision.reason,
                )
                return self._build_no_trade_verdict(
                    candidate, scored=None, debate=None,
                    reason=f"D112 Router: {_router_decision.reason}",
                )

            # Tier 2 and Tier 3 continue to the normal flow below.
            # Tier 2 (DETERMINISTIC_ONLY) is handled by the D107 deterministic
            # path — we temporarily override deterministic_only to route through it.
            if _router_decision.tier == EvalTier.DETERMINISTIC_ONLY:
                logger.info(
                    "%s D112 DETERMINISTIC_ONLY: %s — using D107 path",
                    ticker, _router_decision.reason,
                )
                # Fall through to the D107 deterministic block below
                # by setting _router_use_deterministic flag
                _router_force_deterministic = True
            else:
                _router_force_deterministic = False
        else:
            _router_force_deterministic = False

        # ── D107 WS6: Deterministic-Only Mode ──
        # Zero LLM calls — only DeterministicTechnical + DeterministicRisk.
        # Answers "does the scanner/strategy have fundamental edge?" independent
        # of LLM quality. Enable via --deterministic-only flag.
        # D112: Also used when router routes to DETERMINISTIC_ONLY tier.
        if self._settings.execution.deterministic_only or _router_force_deterministic:
            _det_signals: list[AgentSignal] = []
            try:
                _det_tech = await self._technical_agent.analyze(
                    ticker=ticker,
                    current_price=candidate.current_price,
                    rvol=candidate.rvol,
                    gap_pct=candidate.gap_pct,  # D126: gap momentum mode
                    vwap=self._get_vwap(ticker, candidate.current_price) or 0.0,
                    price_data=market_data.get("price_data", {}) if market_data else {},
                )
                _det_signals.append(_det_tech)
            except Exception as e:
                logger.warning("%s D107 DETERMINISTIC: Technical failed: %s", ticker, e)

            try:
                _det_risk = await self._risk_agent.analyze(
                    ticker=ticker,
                    candidate_signals=_det_signals,
                    market_data=market_data or {},
                    sec_filings=sec_filings or {},
                )
                _det_signals.append(_det_risk)
            except Exception as e:
                logger.warning("%s D107 DETERMINISTIC: Risk failed: %s", ticker, e)

            # Synthetic RVOL signal — provides a third "agent" signal from scanner data
            _det_rvol_conf = min(1.0, candidate.rvol / 10.0) if candidate.rvol > 0 else 0.0
            _det_rvol_signal = "BULL" if candidate.rvol >= 3.0 else "NEUTRAL"
            _det_signals.append(AgentSignal(
                agent_id="rvol_synthetic",
                ticker=ticker,
                timestamp=datetime.now(timezone.utc),
                signal=_det_rvol_signal,
                confidence=_det_rvol_conf,
                reasoning=f"D107 synthetic RVOL signal: rvol={candidate.rvol:.1f}x",
                sources_used=["scanner"],
            ))

            logger.info(
                "%s D107 DETERMINISTIC: %d signals, zero LLM calls",
                ticker, len(_det_signals),
            )
            agent_signals = _det_signals
        else:
            # Normal path: D106 cache bypass or full LLM dispatch

            # ── D106: Smart Cache Bypass — skip LLM re-dispatch if premarket cache valid ──
            # At 9:20, FastPathScorer runs 5 agents (3 LLM + 2 deterministic).
            # At 9:30, if cache is fresh (<15 min) and price stable (<3% move):
            #   - Reuse cached LLM signals (no re-dispatch = ~45s saved)
            #   - Re-run only Technical + Risk agents with real market data (<1ms)
            #   - Combine for full MFCS score
            _cache_hit = False
            _cached_entry = self._premarket_mfcs_cache.get(ticker)
            if _cached_entry is not None:
                _cached_mfcs, _cached_price, _cache_time, _cached_signals = _cached_entry
                _cache_age_s = (datetime.now(timezone.utc) - _cache_time).total_seconds()
                _price_change_pct = (
                    abs(candidate.current_price - _cached_price) / _cached_price * 100
                    if _cached_price > 0 else 999.0
                )

                # Guard 1: Price moved >3% → invalidate cache, full eval
                if _price_change_pct > 3.0:
                    logger.info(
                        "%s D106 CACHE INVALIDATED: price moved %.1f%% ($%.2f → $%.2f) "
                        "> 3%% threshold — full LLM eval",
                        ticker, _price_change_pct, _cached_price, candidate.current_price,
                    )
                # Guard 2: Cache too stale (>15 min) → invalidate
                elif _cache_age_s > 900:
                    logger.info(
                        "%s D106 CACHE STALE: %.0fs old > 900s — full LLM eval",
                        ticker, _cache_age_s,
                    )
                # Cache valid — reuse LLM signals, re-run deterministic with real data
                elif _cached_signals:
                    _cache_hit = True
                    logger.info(
                        "%s D106 CACHE HIT: premarket MFCS=%.3f, age=%.0fs, "
                        "price_delta=%.1f%% — skipping LLM re-dispatch",
                        ticker, _cached_mfcs, _cache_age_s, _price_change_pct,
                    )

            if _cache_hit:
                # Re-run deterministic agents with real market data (both <1ms)
                _fresh_deterministic: list[AgentSignal] = []
                try:
                    _tech_sig = await self._technical_agent.analyze(
                        ticker=ticker,
                        current_price=candidate.current_price,
                        rvol=candidate.rvol,
                        gap_pct=candidate.gap_pct,  # D126: gap momentum mode
                        vwap=self._get_vwap(ticker, candidate.current_price) or 0.0,
                        price_data=market_data.get("price_data", {}) if market_data else {},
                    )
                    _fresh_deterministic.append(_tech_sig)
                    logger.debug(
                        "%s D106 cache: fresh Technical signal=%s conf=%.2f",
                        ticker, _tech_sig.signal, _tech_sig.confidence,
                    )
                except Exception as e:
                    logger.warning("%s D106 cache: Technical re-run failed: %s", ticker, e)

                try:
                    _risk_sig = await self._risk_agent.analyze(
                        ticker=ticker,
                        candidate_signals=_cached_signals,
                        market_data=market_data or {},
                        sec_filings=sec_filings or {},
                    )
                    _fresh_deterministic.append(_risk_sig)
                    logger.debug(
                        "%s D106 cache: fresh Risk verdict=%s score=%.2f",
                        ticker, getattr(_risk_sig, "risk_verdict", "?"),
                        getattr(_risk_sig, "risk_score", 0),
                    )
                except Exception as e:
                    logger.warning("%s D106 cache: Risk re-run failed: %s", ticker, e)

                # Combine cached LLM signals + fresh deterministic signals
                # Filter out old deterministic signals from cache (replace with fresh)
                _deterministic_ids = {"technical_agent", "risk_agent"}
                agent_signals = [
                    s for s in _cached_signals if s.agent_id not in _deterministic_ids
                ] + _fresh_deterministic

                logger.info(
                    "%s D106 CACHE BYPASS: %d total signals (%d cached LLM + %d fresh deterministic)",
                    ticker,
                    len(agent_signals),
                    len(agent_signals) - len(_fresh_deterministic),
                    len(_fresh_deterministic),
                )
            else:
                # ── Phase 1: Parallel Agent Dispatch (full LLM eval) ──
                # Auto-query SEC if client available and no filings provided
                effective_sec = sec_filings or {}
                if not effective_sec and self._sec_client is not None:
                    effective_sec = await self._fetch_sec_filings(ticker)

                agent_signals = await self._dispatch_agents(
                    candidate=candidate,
                    news_items=news_items or [],
                    market_data=market_data or {},
                    sec_filings=effective_sec,
                )

        # ── D100/D104: Fetch 14-day daily ATR for stop calculation ──
        # Cached per-ticker per-session (ATR doesn't change intraday).
        if ticker not in self._atr_cache:
            # D104: Check Phase 0 pre-market cache first (avoids redundant API call)
            if ticker in self._premarket_atr and self._premarket_atr[ticker] > 0:
                self._atr_cache[ticker] = self._premarket_atr[ticker]
                logger.info(
                    "%s D104: ATR_14d=$%.4f (from Phase 0 pre-market cache)",
                    ticker, self._atr_cache[ticker],
                )
            elif self._data_client is not None:
                try:
                    # D104: Use explicit start date — calling get_bars with only
                    # limit=15 and no start date returned 0 bars for NVTS on Day 1.
                    # Alpaca's API needs a date range for reliable daily bar retrieval.
                    _atr_start = (
                        datetime.now(timezone.utc) - timedelta(days=30)
                    ).strftime("%Y-%m-%d")
                    daily_bars = await self._data_client.get_bars(
                        ticker, timeframe="1Day", limit=20, start=_atr_start,
                    )
                    self._atr_cache[ticker] = compute_atr(daily_bars, period=14)
                    if self._atr_cache[ticker]:
                        logger.info(
                            "%s D100: ATR_14d=$%.4f (from %d daily bars)",
                            ticker, self._atr_cache[ticker], len(daily_bars),
                        )
                    else:
                        logger.warning(
                            "%s D100: ATR_14d=None (insufficient bars: %d)",
                            ticker, len(daily_bars) if daily_bars else 0,
                        )
                except Exception as e:
                    logger.warning("%s D100: ATR fetch failed: %s — using fallback stop", ticker, e)
                    self._atr_cache[ticker] = None

        # Store for ScenarioAgentRecorder and post-trade analysis
        self._last_agent_signals = agent_signals
        # D96: Record per-agent signal distribution for session report
        for _sig in agent_signals:
            metrics.record_agent_signal(_sig.agent_id, _sig.signal)
        # D83: Store per-candidate to prevent signal bleeding in journal
        self._signals_by_ticker[ticker] = agent_signals
        self._data_report_by_ticker[ticker] = dict(self._last_data_report)
        self._variant_map_by_ticker[ticker] = dict(self._last_variant_map)

        # ── Phase 2: MFCS Scoring (pure math, no LLM) ──
        # D44: Catalyst-quality-aware debate threshold
        base_debate_threshold = self._settings.debate.mfcs_debate_threshold
        news_signal = next(
            (s for s in agent_signals if hasattr(s, "catalyst_specificity")),
            None,
        )
        if news_signal and hasattr(news_signal, "catalyst_specificity"):
            if news_signal.catalyst_specificity == "CONFIRMED":
                effective_debate_threshold = base_debate_threshold * 0.8
            elif news_signal.catalyst_specificity == "RUMORED":
                effective_debate_threshold = base_debate_threshold * 1.2
            else:
                effective_debate_threshold = base_debate_threshold
        else:
            effective_debate_threshold = base_debate_threshold

        scored = compute_mfcs(
            candidate=candidate,
            signals=agent_signals,
            weights={
                "catalyst_news": self._settings.scoring.catalyst_news,
                "technical": self._settings.scoring.technical,
                "volume_rvol": self._settings.scoring.volume_rvol,
                "float_structure": self._settings.scoring.float_structure,
                "institutional": self._settings.scoring.institutional,
                "deep_search": self._settings.scoring.deep_search,
            },
            risk_aversion_lambda=self._settings.scoring.risk_aversion_lambda,
            debate_threshold=effective_debate_threshold,
        )

        # D61: Capture scored data for journal recording
        self._last_scored_mfcs = scored.mfcs
        self._last_scored_components = dict(scored.component_scores) if scored.component_scores else {}
        self._last_scored_risk = scored.risk_score
        self._last_scored_qualifies_debate = scored.qualifies_for_debate
        # D121 BUG: Cache full ScoredCandidate for bridge.execute_verdict Shapley
        self._scored_by_ticker[ticker] = scored
        # D83: Per-candidate scoring to fix journal misattribution
        self._scoring_by_ticker[ticker] = {
            "mfcs": scored.mfcs,
            "components": dict(scored.component_scores) if scored.component_scores else {},
            "risk_score": scored.risk_score,
            "qualifies_for_debate": scored.qualifies_for_debate,
        }

        logger.info(
            "%s MFCS=%.3f (qualifies_for_debate=%s)",
            ticker, scored.mfcs, scored.qualifies_for_debate,
        )

        # ── D101 §2.5: Minimum Consensus Gate ──
        # D219 FIX: The original gate required at least 1 directional (non-NEUTRAL)
        # agent signal. This killed stocks like CMCT (MFCS=0.497) where volume
        # scored extremely high but all LLM agents returned NEUTRAL (no news found).
        # The MFCS itself IS the signal — if it's strong from volume/float alone,
        # the consensus gate shouldn't block it.
        #
        # New logic: waive the directional agent requirement if MFCS > 0.30.
        # Below 0.30, still require at least 1 directional agent (prevents
        # trading on volume alone with a weak score).
        _non_risk_for_consensus = [
            sig for sig in agent_signals
            if not isinstance(sig, RiskSignal)
            and sig.signal != "NEUTRAL"
            and "AGENT_ERROR" not in (sig.flags or [])
            and "D94_NO_DATA_SKIP" not in (sig.flags or [])
        ]
        _min_directional = self._settings.scoring.min_directional_agents
        _mfcs_waives_consensus = scored.mfcs >= 0.30 if scored else False
        if len(_non_risk_for_consensus) < _min_directional and not _mfcs_waives_consensus:
            logger.info(
                "%s D101 CONSENSUS GATE: Only %d/%d agents directional (need %d, MFCS=%.3f < 0.30 waiver) — HOLD",
                ticker, len(_non_risk_for_consensus),
                len([s for s in agent_signals if not isinstance(s, RiskSignal)]),
                _min_directional, scored.mfcs if scored else 0,
            )
            return self._build_no_trade_verdict(
                candidate, scored, debate=None,
                reason=f"D101 consensus gate: {len(_non_risk_for_consensus)} directional "
                f"agents < {_min_directional} minimum (MFCS={scored.mfcs:.3f} < 0.30 waiver)",
            )
        if _mfcs_waives_consensus and len(_non_risk_for_consensus) < _min_directional:
            logger.info(
                "%s D219 CONSENSUS WAIVER: MFCS=%.3f >= 0.30 overrides %d directional "
                "agents < %d minimum — allowing evaluation to proceed",
                ticker, scored.mfcs, len(_non_risk_for_consensus), _min_directional,
            )

        # ── D124 §2: Consensus Alignment Gate ──
        # D219 FIX (2026-04-13): The original gate required strict bullish
        # majority (bearish >= bullish = REJECT). D219 softened to require
        # bearish to OUTNUMBER bullish by 2+ OR have 1.5× the aggregate
        # confidence.
        #
        # Bug AK FIX (2026-04-27, Tier 2 #5): The D219 rule had a latent
        # arithmetic defect — when bullish_conf=0 (no bullish agents at
        # all), `bearish_conf > bullish_conf * 1.5` reduces to
        # `bearish_conf > 0`, so ANY bearish confidence > 0 trips the
        # second branch. Today's session log showed 47% of D124 rejections
        # fired on "0 bullish vs 1 bearish (bear_conf=0.45-0.80)" — the
        # system had no bullish opinion, one cautious bearish whisper,
        # and the trade was killed before MFCS could evaluate. Other
        # bearish agents likely included risk_agent (which is wary of
        # high-vol stocks by design) and manipulation_agent (which
        # frequently flags low-conf concerns on penny stocks).
        #
        # New three-tier rejection logic:
        #   Tier A: numeric dominance — bearish outnumber bullish by 2+
        #   Tier B: ≥2 bearish AND aggregate conf > max(bullish*1.5, 1.0)
        #          (the absolute floor of 1.0 prevents the bullish_conf=0
        #           degenerate case from auto-rejecting)
        #   Tier C: single very-high-conf bearish (>=0.85) — preserves the
        #          fraud/manipulation single-veto path
        # Everything else passes through to MFCS scoring (where the
        # bearish signals already deduct from the composite score).
        _bullish = [s for s in _non_risk_for_consensus
                    if s.signal in ("BULL", "STRONG_BULL")]
        _bearish = [s for s in _non_risk_for_consensus
                    if s.signal in ("BEAR", "STRONG_BEAR")]
        _bullish_conf = sum(s.confidence for s in _bullish) if _bullish else 0
        _bearish_conf = sum(s.confidence for s in _bearish) if _bearish else 0
        _max_bearish_conf = max((s.confidence for s in _bearish), default=0.0)

        # Bug AK three-tier rejection
        _bearish_dominant_a = len(_bearish) >= len(_bullish) + 2
        _bearish_dominant_b = (
            len(_bearish) >= 2
            and _bearish_conf > max(_bullish_conf * 1.5, 1.0)
        )
        _bearish_dominant_c = (
            len(_bearish) >= 1
            and _max_bearish_conf >= 0.85
        )
        _bearish_dominant = (
            _bearish_dominant_a or _bearish_dominant_b or _bearish_dominant_c
        )
        if _bearish_dominant:
            _tier = (
                "A:numeric" if _bearish_dominant_a
                else "B:aggregate" if _bearish_dominant_b
                else "C:single-veto"
            )
            logger.info(
                "%s D124 CONSENSUS ALIGNMENT (tier=%s): %d bullish (conf=%.2f) vs "
                "%d bearish (conf=%.2f, max=%.2f) — REJECT",
                ticker, _tier, len(_bullish), _bullish_conf,
                len(_bearish), _bearish_conf, _max_bearish_conf,
            )
            return self._build_no_trade_verdict(
                candidate, scored, debate=None,
                reason=f"D124 consensus alignment (tier={_tier}): {len(_bullish)} bullish "
                f"(conf={_bullish_conf:.2f}) vs {len(_bearish)} bearish "
                f"(conf={_bearish_conf:.2f}, max={_max_bearish_conf:.2f}) — bearish dominant",
            )
        elif _bearish:
            # Bug AK: log the pass-through so we can audit how often the
            # softened rule lets trades reach MFCS (would have been a hard
            # reject under the pre-D219 or pre-Bug-AK rule).
            _was_old_d219_reject = (
                len(_bearish) > len(_bullish)
                and _bearish_conf > _bullish_conf * 1.5
            )
            if _was_old_d219_reject:
                logger.info(
                    "%s D219.v2 ALIGNMENT_PASSED (Bug AK): %d bullish (conf=%.2f) vs "
                    "%d bearish (conf=%.2f, max=%.2f) — would have hard-rejected under "
                    "pre-Bug-AK rule. MFCS=%.3f will determine verdict.",
                    ticker, len(_bullish), _bullish_conf,
                    len(_bearish), _bearish_conf, _max_bearish_conf,
                    scored.mfcs if scored else 0,
                )
            elif len(_bearish) >= len(_bullish):
                logger.info(
                    "%s D219 ALIGNMENT SOFTENED: %d bullish vs %d bearish — allowing "
                    "(was original D124 hard reject). MFCS=%.3f will determine verdict.",
                    ticker, len(_bullish), len(_bearish), scored.mfcs if scored else 0,
                )

        # ── Phase 3: Debate (conditional, with D87 adaptive skip + D91 degraded skip) ──
        # D91: Count how many agents actually returned useful data.
        # When >50% of agents failed (timeout/circuit breaker/error), debate
        # with empty bull/bear arguments defaults to NO_TRADE — which is wrong.
        # The absence of LLM data is not evidence against the trade.
        # Skip debate when data is degraded; let MFCS decide with available signals.
        _non_risk_signals = [
            sig for sig in agent_signals
            if not isinstance(sig, RiskSignal)
        ]
        _failed_agents = sum(
            1 for sig in _non_risk_signals
            if "AGENT_ERROR" in (sig.flags or [])
            or "D91_BOTH_FAILED" in (sig.flags or [])
            or "D92_ALL_FAILED" in (sig.flags or [])
            or (sig.confidence == 0.0 and not sig.reasoning.strip())
        )
        _total_non_risk = len(_non_risk_signals)
        _degraded_data = _total_non_risk > 0 and _failed_agents > _total_non_risk / 2

        if _degraded_data:
            logger.warning(
                "%s D91: DEGRADED DATA — %d/%d agents failed. "
                "Skipping debate (would default to NO_TRADE on empty data). "
                "Letting MFCS decide with available signals.",
                ticker, _failed_agents, _total_non_risk,
            )

        # D87: Compute coefficient of variation (CV) across agent scores.
        # When agents strongly agree (CV < 0.30), debate adds latency
        # without improving accuracy — skip it. When agents meaningfully
        # disagree (CV >= 0.30), debate synthesizes conflicting views.
        # Research: "Debate or Vote" (arXiv:2508.17536) shows majority
        # voting captures ~90% of debate accuracy gains.
        debate_result: DebateResult | None = None
        _skip_debate = _degraded_data  # D91: Skip debate when data is degraded
        _skip_reason = "degraded" if _degraded_data else ""  # D103: Track skip reason
        if scored.qualifies_for_debate and not _degraded_data:
            # D97: Check debate budget before triggering
            _prior_attempts = self._debate_attempts.get(ticker, 0)
            if _prior_attempts >= self._settings.debate.max_debate_attempts:
                _skip_debate = True
                _skip_reason = "budget"  # D103
                logger.info(
                    "%s D97: Debate budget exhausted (%d/%d attempts) — MFCS-only verdict",
                    ticker, _prior_attempts, self._settings.debate.max_debate_attempts,
                )

            # Compute per-agent scores (excluding risk agent and NEUTRAL signals)
            _agent_scores = [
                signal_to_score(sig) for sig in agent_signals
                if not isinstance(sig, RiskSignal)
                and sig.reasoning.strip()  # D26: skip empty fallback signals
                and sig.signal != "NEUTRAL"  # D97: Exclude NEUTRAL from CV calc
            ]

            # Coefficient of variation = std / mean (0 = perfect agreement)
            _debate_cv_threshold = 0.30
            if len(_agent_scores) >= 2:
                _mean = sum(_agent_scores) / len(_agent_scores)
                if _mean > 0:
                    _variance = sum((s - _mean) ** 2 for s in _agent_scores) / len(_agent_scores)
                    _cv = math.sqrt(_variance) / _mean
                    logger.info(
                        "%s D87: Agent score CV=%.3f (scores=%s, threshold=%.2f)",
                        ticker, _cv,
                        [round(s, 3) for s in _agent_scores],
                        _debate_cv_threshold,
                    )
                    if _cv < _debate_cv_threshold:
                        _skip_debate = True
                        if not _skip_reason:  # D103: Don't overwrite budget reason
                            _skip_reason = "cv"
                        logger.info(
                            "%s D87: Agents agree (CV=%.3f < %.2f) — skipping debate, "
                            "saving ~50s latency",
                            ticker, _cv, _debate_cv_threshold,
                        )

            if _skip_debate:
                # Agents agree or budget exhausted — proceed directly to risk veto + verdict
                metrics.debates_triggered.inc()  # Still count for tracking
                # D103: Differentiate budget vs CV skip for accurate reporting
                if _skip_reason == "budget":
                    metrics.debates_skipped_budget.inc()
                else:
                    metrics.debates_skipped.inc()  # D96: CV consensus skip
                logger.info(
                    "%s D103: Debate SKIPPED (%s). MFCS=%.3f proceeding to verdict.",
                    ticker, _skip_reason or "unknown", scored.mfcs,
                )
            else:
                metrics.debates_triggered.inc()
                self._debate_attempts[ticker] = self._debate_attempts.get(ticker, 0) + 1
                logger.info(
                    "%s → Entering debate engine (CV above threshold, attempt %d/%d)",
                    ticker, self._debate_attempts[ticker],
                    self._settings.debate.max_debate_attempts,
                )
                debate_result = await self._debate_engine.run_debate(scored)

                if debate_result is not None:
                    logger.info(
                        "%s Debate verdict=%s, confidence=%.2f, divergence=%.2f",
                        ticker, debate_result.verdict, debate_result.confidence,
                        debate_result.debate_divergence,
                    )

                    if debate_result.verdict == "BUY":
                        metrics.debates_buy.inc()

                    # No trade if debate says so
                    if debate_result.verdict == "NO_TRADE":
                        metrics.debates_no_trade.inc()  # D96: Track rejection rate
                        return self._build_no_trade_verdict(
                            candidate, scored, debate_result,
                            reason="Debate verdict: NO_TRADE",
                        )
                else:
                    logger.warning(
                        "%s Debate engine returned None — proceeding without debate",
                        ticker,
                    )

        # ── Phase 4: Risk Veto Check ──
        risk_signal = self._extract_risk_signal(agent_signals)
        if risk_signal and risk_signal.risk_verdict == "VETO":
            veto_mode = getattr(self._settings.scoring, "risk_veto_mode", "HARD")
            if veto_mode == "HARD":
                logger.warning(
                    "%s VETOED by Risk Agent: %s", ticker, risk_signal.veto_reason
                )
                metrics.risk_vetoes.inc()
                return self._build_no_trade_verdict(
                    candidate, scored, debate_result,
                    reason=f"Risk VETO: {risk_signal.veto_reason}",
                )
            else:
                # ADVISORY mode: log the veto but let MFCS score decide
                logger.info(
                    "%s Risk VETO advisory (not enforced, mode=%s): %s",
                    ticker, veto_mode, risk_signal.veto_reason,
                )
                metrics.risk_vetoes.inc()

        # ── D91: Degraded-Mode Trading ──
        # When >50% of agents failed (timeout/circuit breaker), MFCS is
        # artificially low because failed agents return 0.0 scores. The
        # trade isn't BAD — we just don't have LLM data. Use deterministic
        # signals from the scanner (gap%, RVOL, news catalyst) to make a
        # reduced-confidence decision. This prevents total blindness.
        _degraded_verdict = None
        if _degraded_data:
            # Extract any surviving news signal
            _news_sig = next(
                (s for s in agent_signals if s.agent_id == "news_agent"
                 and s.confidence > 0 and "AGENT_ERROR" not in (s.flags or [])),
                None,
            )
            _has_catalyst = (
                _news_sig is not None
                and hasattr(_news_sig, "catalyst_type")
                and _news_sig.catalyst_type not in (None, "", "NONE", "UNKNOWN")
            )
            # D221 Phase F: read from news_features instead of .signal.
            # Includes a legacy fallback -- mirrors the pattern in
            # src/execution/faller_detection.py:704-720. If news_features is
            # empty (degraded path, e.g. parse_response raised and NewsSignal
            # was returned with default {}), fall back to the old verdict
            # check to preserve prior D91 degraded-mode semantics exactly.
            # Sunday bug sweep (docs/research-log/11_sunday_bug_sweep.md) caught
            # that without this fallback, the degraded-mode gate becomes
            # silently more conservative on the degraded path.
            if _news_sig is None:
                _news_bull = False
            else:
                _features = getattr(_news_sig, "news_features", None) or {}
                _sig_num = _features.get("signal_direction_numeric")
                if _sig_num is not None:
                    _news_bull = _sig_num >= 1.0
                else:
                    # Legacy fallback: preserves exact prior behavior for
                    # NewsSignal objects without news_features populated.
                    _news_bull = _news_sig.signal in (
                        "BULL", "STRONG_BULL", "BULLISH", "STRONG_BUY", "BUY",
                    )

            # Deterministic BUY criteria (no LLM needed):
            # 1. RVOL > 3x (strong institutional interest)
            # 2. Gap > 5% (meaningful catalyst response)
            # 3. News agent returned BULL signal (at least one agent worked)
            _det_buy = (
                candidate.rvol >= 3.0
                and candidate.gap_pct >= 0.05
                and (_has_catalyst or _news_bull)
            )

            if _det_buy:
                logger.info(
                    "%s D91: DEGRADED-MODE BUY — RVOL=%.1fx, Gap=%.1f%%, "
                    "catalyst=%s, news=%s. Using 50%% position size.",
                    ticker, candidate.rvol, candidate.gap_pct * 100,
                    getattr(_news_sig, "catalyst_type", "N/A") if _news_sig else "N/A",
                    _news_sig.signal if _news_sig else "N/A",
                )
                # Build a degraded-mode verdict with half position size
                _degraded_verdict = TradeVerdict(
                    ticker=ticker,
                    action="BUY",
                    confidence=0.5,  # Medium confidence — deterministic only
                    mfcs=scored.mfcs,
                    debate_result=None,
                    risk_signal=risk_signal,
                    entry_price=candidate.current_price,
                    stop_loss=round(candidate.current_price * (1 - self._settings.execution.stop_loss_pct), 4),
                    target_prices=[
                        round(candidate.current_price * 1.03, 4),
                        round(candidate.current_price * 1.06, 4),
                        round(candidate.current_price * 1.10, 4),
                    ],
                    position_size_pct=self._settings.execution.max_position_pct * 0.5,  # Half size
                    # D150: Pass enrichment data for tier-based aggressive sizing
                    float_shares=candidate.float_shares,
                    gap_pct=candidate.gap_pct,
                    rvol=candidate.rvol,
                    reasoning_summary=(
                        f"D91 DEGRADED-MODE: {_failed_agents}/{_total_non_risk} agents failed. "
                        f"Deterministic signals: RVOL={candidate.rvol:.1f}x, "
                        f"Gap={candidate.gap_pct*100:.1f}%, "
                        f"Catalyst={getattr(_news_sig, 'catalyst_type', 'N/A') if _news_sig else 'N/A'}"
                    ),
                )
            else:
                logger.info(
                    "%s D91: DEGRADED-MODE NO_TRADE — criteria not met "
                    "(RVOL=%.1fx, Gap=%.1f%%, catalyst=%s, news=%s). "
                    "Need RVOL>=3x + Gap>=5%% + news catalyst for degraded buy.",
                    ticker, candidate.rvol, candidate.gap_pct * 100,
                    getattr(_news_sig, "catalyst_type", "N/A") if _news_sig else "N/A",
                    _news_sig.signal if _news_sig else "N/A",
                )

        # ── D101 §3.3: VWAP Directional Bias ──
        # Zarattini & Aziz (2023): VWAP creates natural support/resistance.
        # For gap plays: only enter longs when price is above VWAP.
        #
        # D106 FIX: Two bypass conditions:
        # 1. Skip first 10 min of market open — VWAP is undefined/unreliable
        # 2. Skip when VWAP is None — WebSocket unavailable, no opinion
        # D220: Extended from 5min to 10min and threshold raised from 0.5% to 2%.
        # Apr 16 journal showed 14 rejections at 0.6%-3.5% below VWAP — most are
        # noise (bid/ask spread on small caps) or pre-breakout consolidation.
        # The Zarattini ORB research (Sharpe 2.81) explicitly enters above the
        # opening range, which often means below VWAP for the first 5-10 min.
        _skip_vwap_gate = False
        try:
            from zoneinfo import ZoneInfo
            _now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
            _market_open_minute = _now_et.hour * 60 + _now_et.minute
            # 9:30 = 570, 9:40 = 580 (D220: extended from 9:35 to 9:40)
            if 570 <= _market_open_minute < 580:
                _skip_vwap_gate = True
                logger.info(
                    "%s D106 VWAP gate SKIPPED — first 10 min of market open "
                    "(%s ET), VWAP unreliable",
                    ticker, _now_et.strftime("%H:%M"),
                )
        except Exception as _vwap_tz_err:
            logger.warning(
                "%s D220 VWAP timezone lookup failed (%s) — proceeding with gate",
                ticker, _vwap_tz_err,
            )

        _vwap = self._get_vwap(ticker, candidate.current_price)

        if _vwap is None:
            _skip_vwap_gate = True
            logger.info(
                "%s D106 VWAP gate SKIPPED — VWAP unavailable (WebSocket not connected)",
                ticker,
            )

        if not _skip_vwap_gate and _vwap is not None and candidate.current_price < _vwap and _vwap > 0:
            _vwap_deficit_pct = (_vwap - candidate.current_price) / _vwap * 100
            # D220: Only block if MEANINGFULLY below VWAP (> 2%) — was 0.5%
            if _vwap_deficit_pct > 2.0:
                logger.info(
                    "%s D101 VWAP BIAS: price=$%.2f < VWAP=$%.2f (%.1f%% below) — "
                    "rejecting long entry",
                    ticker, candidate.current_price, _vwap, _vwap_deficit_pct,
                )
                return self._build_no_trade_verdict(
                    candidate, scored, debate=None,
                    reason=f"D101 VWAP bias: price ${candidate.current_price:.2f} is "
                    f"{_vwap_deficit_pct:.1f}% below VWAP ${_vwap:.2f}",
                )

        # ── D106 §2D: Manipulation Phase Parameter Modification ──
        # Extract ManipulationSignal from agent_signals (if ManipulationClassifier ran)
        _manipulation_signal: ManipulationSignal | None = None
        for _sig in agent_signals:
            if isinstance(_sig, ManipulationSignal):
                _manipulation_signal = _sig
                break

        _manipulation_phase = (
            _manipulation_signal.phase if _manipulation_signal else "UNCERTAIN"
        )

        if _manipulation_signal:
            logger.info(
                "%s D106 MANIPULATION: phase=%s prob=%.2f upside_min=%d | "
                "evidence=%s | red_flags=%s",
                ticker, _manipulation_phase,
                _manipulation_signal.manipulation_probability,
                _manipulation_signal.estimated_remaining_upside_minutes,
                _manipulation_signal.key_evidence[:3],
                _manipulation_signal.red_flags[:3],
            )

        # PROMOTIONAL_LATE → NO_TRADE immediately (dump imminent or in progress)
        if _manipulation_phase == "PROMOTIONAL_LATE":
            logger.warning(
                "%s D106 PROMOTIONAL_LATE: blocking trade — %s",
                ticker,
                (_manipulation_signal.reasoning[:200]
                 if _manipulation_signal else "same-day 424B5 or dump in progress"),
            )
            return self._build_no_trade_verdict(
                candidate, scored, debate_result,
                reason=f"D106 Manipulation: PROMOTIONAL_LATE — "
                f"{_manipulation_signal.reasoning[:100] if _manipulation_signal else 'dump imminent'}",
            )

        # ── Phase 5: Build Trade Verdict ──
        pipeline_ms = (time.monotonic() - pipeline_start) * 1000
        metrics.pipeline_latency.observe(pipeline_ms / 1000.0)  # Convert to seconds
        logger.info(
            "%s Pipeline complete in %.0fms", ticker, pipeline_ms
        )

        # D115: Kelly tier classification (before sizing)
        _kelly_result: KellyTierResult | None = None
        if _degraded_verdict is None:  # Skip Kelly for degraded verdicts (conservative by design)
            try:
                from zoneinfo import ZoneInfo
                _now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
                _market_open_et = _now_et.replace(hour=9, minute=30, second=0, microsecond=0)
                _mins_since_open = max(0.0, (_now_et - _market_open_et).total_seconds() / 60)

                # Extract catalyst info from news agent signal
                _catalyst_type = "NONE"
                _catalyst_spec = "SPECULATIVE"
                for _sig in agent_signals:
                    if _sig.agent_id == "news_agent" and _sig.key_data:
                        _catalyst_type = _sig.key_data.get("catalyst_type", "NONE")
                        _catalyst_spec = _sig.key_data.get("catalyst_specificity", "SPECULATIVE")
                        break

                # Use ATR-based stop estimate when available, else config default
                _stop_pct = self._settings.execution.stop_loss_pct
                _atr = self._atr_cache.get(ticker)
                if _atr and _atr > 0 and candidate.current_price > 0:
                    _atr_stop_dist = max(
                        self._settings.execution.initial_stop_atr_multiplier * _atr,
                        candidate.current_price * self._settings.execution.initial_stop_floor_pct,
                    )
                    _stop_pct = _atr_stop_dist / candidate.current_price

                # Gather Kelly context from PositionManager if available
                _open_positions = []
                _daily_realized_pnl = 0.0
                _portfolio_heat_pct = 0.0
                _pm = self._position_manager
                if _pm is not None:
                    _open_positions = getattr(_pm, "open_positions", [])
                    _daily_realized_pnl = getattr(_pm, "_daily_realized_pnl", 0.0)
                    # Portfolio heat: sum of position risk / equity
                    _equity = getattr(_pm, "_starting_equity", 0.0)
                    if _equity > 0:
                        for _pos in _open_positions:
                            _entry = getattr(_pos, "entry_price", 0.0)
                            _stop = getattr(_pos, "stop_loss", 0.0)
                            _qty = getattr(_pos, "remaining_qty", 0)
                            if _entry > 0:
                                _risk = _qty * (_entry - _stop)
                                _portfolio_heat_pct += max(0.0, _risk) / _equity

                _kelly_result = self._kelly_classifier.classify(
                    mfcs=scored.mfcs,
                    rvol=candidate.rvol,
                    gap_pct=candidate.gap_pct,
                    stop_loss_pct=_stop_pct,
                    catalyst_type=_catalyst_type,
                    catalyst_specificity=_catalyst_spec,
                    float_shares=candidate.float_shares,
                    sector=getattr(candidate, "sector", ""),
                    directional_agent_count=len(_non_risk_for_consensus),
                    vix_level=self._vix_level,
                    spy_return_pct=self._spy_return_pct,
                    daily_realized_pnl=_daily_realized_pnl,
                    open_positions=_open_positions,
                    portfolio_heat_pct=_portfolio_heat_pct,
                    # daily_unrealized_pnl and max_open_exit_urgency require live
                    # price data / exit intelligence state — not available at entry time
                    minutes_since_open=_mins_since_open,
                    current_price=candidate.current_price,
                )
                logger.info(
                    "%s D115 KELLY: %s",
                    ticker, _kelly_result.reason,
                )
            except Exception:
                logger.exception("%s D115 Kelly classification failed — using Tier 1", ticker)

        # ── D118: Entry Catalyst Profiler ──
        _catalyst_profile = None
        _exit_cfg = self._settings.exit_intelligence
        if (
            getattr(_exit_cfg, "catalyst_profiler_enabled", False)
            and self._debate_engine is not None
            and _degraded_verdict is None
        ):
            try:
                # Extract news headlines from news agent signal
                _headlines: list[str] = []
                for _sig in agent_signals:
                    if _sig.agent_id == "news_agent" and _sig.key_data:
                        _headlines = _sig.key_data.get("headlines", [])
                        if not _headlines and _sig.reasoning:
                            # Fallback: use reasoning text as context
                            _headlines = [_sig.reasoning[:500]]
                        break

                _catalyst_profile = await self._debate_engine.classify_catalyst(
                    ticker=ticker,
                    current_price=candidate.current_price,
                    gap_pct=candidate.gap_pct,
                    rvol=candidate.rvol,
                    news_headlines=_headlines,
                    manipulation_phase=_manipulation_phase,
                    sector=getattr(candidate, "sector", ""),
                    float_shares=candidate.float_shares,
                    timeout=getattr(_exit_cfg, "catalyst_profiler_timeout", 10),
                    max_tokens=getattr(_exit_cfg, "catalyst_profiler_max_tokens", 2048),
                )
                # Counterfactual: log what the static table would have produced
                _static_hl = 20  # "unknown" default
                from src.execution.exit_strategies import DEFAULT_CATALYST_HALF_LIFE_TABLE
                _static_entry = DEFAULT_CATALYST_HALF_LIFE_TABLE.get(
                    _catalyst_type.lower(),
                    DEFAULT_CATALYST_HALF_LIFE_TABLE.get("unknown", {"median": 20}),
                )
                _static_hl = _static_entry.get("median", 20)

                logger.info(
                    "%s D118 CATALYST PROFILE: type=%s durability=%s half_life=%dmin "
                    "(static_table=%dmin) atr_mult=%.1f risk_scale=%.1f conf=%.2f | %s",
                    ticker, _catalyst_profile.catalyst_type,
                    _catalyst_profile.durability,
                    _catalyst_profile.recommended_half_life_minutes,
                    _static_hl,
                    _catalyst_profile.recommended_atr_multiplier,
                    _catalyst_profile.recommended_risk_scale,
                    _catalyst_profile.confidence,
                    _catalyst_profile.reasoning[:200],
                )
            except Exception:
                logger.debug(
                    "%s D118 catalyst profiler failed — using defaults", ticker
                )

        # D91: Use degraded verdict if data was insufficient for normal pipeline
        if _degraded_verdict is not None:
            verdict = _degraded_verdict
        else:
            verdict = self._build_trade_verdict(
                candidate, scored, debate_result, risk_signal,
                manipulation_phase=_manipulation_phase,
                kelly_result=_kelly_result,
                catalyst_profile=_catalyst_profile,
                # Mon 2026-04-20 Bug #2 fix: thread the local VIX reduction
                # explicitly. Previously the verdict builder read a bare
                # name from this caller's scope, which Python doesn't
                # support across sibling methods (NameError on every call).
                d211_local_vix_reduction=_d211_local_vix_reduction,
            )

        # ── D87: Log feature vector for ML training ──
        if self._feature_logger:
            try:
                from zoneinfo import ZoneInfo
                _now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
                self._feature_logger.log_evaluation(
                    trade_id=getattr(self, "_current_trade_id", ticker),
                    ticker=ticker,
                    gap_pct=candidate.gap_pct,
                    rvol=candidate.rvol,
                    current_price=candidate.current_price,
                    previous_close=candidate.previous_close,
                    premarket_volume=candidate.premarket_volume,
                    float_shares=candidate.float_shares,
                    market_cap=candidate.market_cap,
                    has_news_catalyst=candidate.has_news_catalyst,
                    rvol_exhaustion=candidate.rvol_exhaustion,
                    agent_signals=[
                        {"agent": s.agent_id, "signal": s.signal,
                         "confidence": s.confidence}
                        for s in agent_signals
                    ],
                    mfcs=scored.mfcs,
                    component_scores=scored.component_scores,
                    risk_score=scored.risk_score,
                    qualifies_for_debate=scored.qualifies_for_debate,
                    indicators=market_data or {},
                    debate_verdict=debate_result.verdict if debate_result else None,
                    debate_confidence=debate_result.confidence if debate_result else None,
                    debate_divergence=debate_result.debate_divergence if debate_result else None,
                    debate_skipped=_skip_debate if scored.qualifies_for_debate else False,
                    hour_et=_now_et.hour,
                    minute_et=_now_et.minute,
                    day_of_week=_now_et.weekday(),
                    final_action=verdict.action,
                    position_size_pct=verdict.position_size_pct,
                )
            except Exception as _fl_e:
                logger.debug("D87: Feature logging error (non-fatal): %s", _fl_e)

        # ── D102: Experiment parameter replay ──────────────────────────────
        # Replay the same agent signals through all active parameter variants.
        # Zero LLM calls — just re-runs compute_mfcs() with different configs.
        # Total overhead: <1ms per variant (21 variants × ~50μs = ~1ms total).
        try:
            self._replay_experiment_variants(
                candidate=candidate,
                agent_signals=agent_signals,
                scored=scored,
                verdict=verdict,
                trade_id=trade_id,
            )
        except Exception as _exp_e:
            logger.debug("D102: Experiment replay error (non-fatal): %s", _exp_e)

        # D220 Phase 5: composite shadow telemetry on BUY path. Write-only.
        # The shadow log writes to data/shadow/shadow_<date>.jsonl; the verdict
        # itself contains NO shadow fields, so production decision code cannot
        # read shadow data. Static guard: tests/static_analysis/test_shadow_isolation.py
        try:
            from src.shadow.composite_shadow import maybe_score_composite
            from zoneinfo import ZoneInfo as _ZI
            _now_et_for_shadow = datetime.now(timezone.utc).astimezone(_ZI("America/New_York"))
            maybe_score_composite(
                candidate_features={
                    "gap_pct": candidate.gap_pct,
                    "premarket_volume": candidate.premarket_volume,
                    "dollar_volume": getattr(candidate, "dollar_volume", 0)
                                     or (candidate.premarket_volume * candidate.current_price),
                    "price": candidate.current_price,
                    "pre_market_high": getattr(candidate, "pre_market_high", 0),
                    "orb_range_pct": 0.0,
                    "day_volume": 0,
                    # D221 Phase F: pass agent_signals for the DISAGREE_SHADOW_BUYS
                    # surface (only fires on NO_TRADE path; harmless here but
                    # consistent shape across both call sites).
                    "_agent_signals_for_dl": list(scored.agent_signals) if scored else [],
                },
                ticker=candidate.ticker,
                session_date=_now_et_for_shadow.strftime("%Y-%m-%d"),
                production_decision="BUY",
                production_gate_rejected=None,
                production_mfcs=scored.mfcs if scored else None,
                # D221 Phase F: explicit None until docs/engineering_hygiene/
                # sec_degradation_fix.md ships the producer-side wiring that
                # makes risk_agent emit a structured sec_data_status field.
                # Until then, the schema field is reserved but unpopulated.
                sec_data_available=None,
            )
        except Exception as _shadow_e:
            logger.warning(
                "composite shadow failed for %s BUY (non-fatal): %s",
                candidate.ticker, _shadow_e,
            )

        # D310.T2 (2026-05-24, doc 171): apply A/B arm assignment to the
        # final verdict. No-op when MOMENTUM_T2_ENABLED is unset (default
        # OFF) -- production behavior unchanged. When enabled, ~50% of
        # long-side verdicts get execution_arm="wide_stop" with
        # qty_multiplier=0.5; the executor's wide-stop branch then uses
        # the ATR stop directly + halved sizing + standalone STOP
        # submission (bypassing TrailingStopManager / D310).
        #
        # D310.T2.v2 (2026-05-27): when T2 is ENABLED and assignment
        # fails, HARD-ABORT the entry by converting verdict to HOLD.
        # The original silent-non-fatal try/except let trades enter
        # WITHOUT arm assignment -- silently breaking A/B experiment
        # integrity. Filed as `T2_arm_assignment_fail_should_halt_entry`
        # 2026-05-26 deep-dive; implementing now during pre-open window.
        try:
            from src.execution.t2_arm_assignment import (
                assigned_arm_for_verdict,
                t2_enabled,
            )
            verdict = assigned_arm_for_verdict(verdict)
        except Exception as _t2_e:
            from src.execution.t2_arm_assignment import t2_enabled as _t2_chk
            if _t2_chk():
                logger.error(
                    "D310.T2.v2 ARM_ASSIGN_HARD_ABORT %s: assignment "
                    "failed during T2 window (MOMENTUM_T2_ENABLED=1) -- "
                    "HOLDING rather than entering un-assigned. err=%s",
                    candidate.ticker, _t2_e,
                )
                # doc 272 VLL: terminal — verdict converted to HOLD here, so it
                # never reaches the BUY gate chain (HOLDs are filtered before
                # the bridge). Without this emit the verdict dies silently.
                try:
                    from src.ops.verdict_ledger import vll_emit
                    vll_emit("BLOCKED_ARM_ASSIGN", candidate.ticker,
                             reason=f"T2 arm assignment failed: {str(_t2_e)[:80]}",
                             path="ORCH")
                except Exception:
                    pass
                try:
                    verdict = verdict.model_copy(update={
                        "action": "HOLD",
                        "reasoning": (
                            f"D310.T2.v2 HARD_ABORT: arm assignment "
                            f"failed ({_t2_e}); refusing to enter "
                            f"un-assigned during T2 A/B window."
                        ),
                    })
                except Exception:
                    # Worst-case fallback: zero out the qty multiplier
                    # so executor naturally rejects.
                    pass
            else:
                logger.warning(
                    "D310.T2 arm assignment failed for %s (T2 disabled, "
                    "non-fatal): %s",
                    candidate.ticker, _t2_e,
                )

        return verdict

    async def evaluate_candidates(
        self,
        candidates: list[CandidateStock],
        news_by_ticker: dict[str, list] | None = None,
        market_data_by_ticker: dict[str, dict] | None = None,
    ) -> list[TradeVerdict]:
        """
        D85/D92: Evaluate multiple candidates in PARALLEL.

        D92: Uses asyncio.gather for candidate-level parallelism (each candidate
        is independent). Within each candidate, _dispatch_agents uses asyncio.wait
        for agent-level partial results. Rate limits are fine:
        10 candidates × 6 agents = 60 Together AI calls + 20 Alpaca data calls
        (within 200/min cap).

        Returns: List of TradeVerdicts sorted by confidence descending.
        """
        news_by_ticker = news_by_ticker or {}
        market_data_by_ticker = market_data_by_ticker or {}

        # D83: Clear per-candidate caches for this batch
        self._signals_by_ticker.clear()
        self._scoring_by_ticker.clear()
        self._scored_by_ticker.clear()
        self._data_report_by_ticker.clear()
        self._variant_map_by_ticker.clear()
        # D121 BUG-R4: Clear stale _last_* caches to prevent signal bleed
        # from previous evaluation cycle into journal entries on exception.
        self._last_agent_signals = []
        self._last_variant_map = {}
        self._last_data_report = {}
        self._last_scored_mfcs = 0.0
        self._last_scored_components = {}
        self._last_scored_risk = 0.0
        self._last_scored_qualifies_debate = False

        capped = candidates[: self._settings.max_candidates_per_scan]

        # D85/D92: Parallel evaluation — all candidates concurrently
        eval_tasks = [
            self.evaluate_candidate(
                candidate=candidate,
                news_items=news_by_ticker.get(candidate.ticker, []),
                market_data=market_data_by_ticker.get(candidate.ticker, {}),
            )
            for candidate in capped
        ]

        # D217: Overall timeout on parallel evaluation — prevents a single
        # hung candidate from blocking the entire scan cycle indefinitely.
        # 120s is generous (most evals take 10-30s) but prevents infinite hangs.
        try:
            _eval_timeout = self._settings.ops.eval_batch_timeout_seconds
            results = await asyncio.wait_for(
                asyncio.gather(*eval_tasks, return_exceptions=True),
                timeout=_eval_timeout,
            )
        except asyncio.TimeoutError:
            _eval_timeout = self._settings.ops.eval_batch_timeout_seconds
            logger.error(
                "D217: evaluate_candidates timed out after %.0fs for %d candidates — "
                "cancelling remaining tasks", _eval_timeout,
                len(eval_tasks),
            )
            # Cancel any still-running tasks
            for t in eval_tasks:
                if hasattr(t, 'cancel'):
                    t.cancel()
            results = []

        verdicts = []
        for i, result in enumerate(results):
            if isinstance(result, TradeVerdict):
                verdicts.append(result)
            elif isinstance(result, Exception):
                ticker = capped[i].ticker if i < len(capped) else "?"
                logger.error(
                    "D92: Parallel eval failed for %s: %s", ticker, result,
                )

        # Sort by confidence (actionable trades first)
        verdicts.sort(key=lambda v: v.confidence, reverse=True)
        return verdicts

    # ── Private Methods ──────────────────────────────────────────────

    def _build_filing_summary(self, sec_filings: dict[str, Any]) -> dict:
        """
        D106 §2E: Build structured filing summary for ManipulationClassifier.

        Converts the dict-format filings (from _fetch_sec_filings) into a
        ManipulationFilingSummary. Returns a plain dict for the agent prompt.
        """
        from src.data.sec_client import (
            Filing,
            ManipulationFilingSummary,
            build_manipulation_filing_summary,
            parse_form_type,
        )
        from datetime import date as date_type

        filings_list = sec_filings.get("filings", [])
        if not filings_list:
            return ManipulationFilingSummary().__dict__

        # Convert dict filings back to Filing objects
        filing_objs: list[Filing] = []
        for f_dict in filings_list:
            try:
                form_raw = f_dict.get("form", f_dict.get("form_type", ""))
                filed_str = f_dict.get("date", f_dict.get("filed_date", ""))
                filed_date = (
                    date_type.fromisoformat(filed_str)
                    if filed_str else date_type.today()
                )
                filing_objs.append(Filing(
                    form_type=form_raw,
                    filing_type=parse_form_type(form_raw),
                    filed_date=filed_date,
                    company_name=f_dict.get("company_name", ""),
                    cik=f_dict.get("cik", ""),
                    accession_number=f_dict.get("accession_number", ""),
                    description=f_dict.get("description", ""),
                ))
            except Exception as e:
                logger.debug("D106: Filing parse error (non-fatal): %s", e)

        summary = build_manipulation_filing_summary(filing_objs)
        return {
            "s3_age_days": summary.s3_age_days,
            "has_424b5_same_day": summary.has_424b5_same_day,
            "insider_sell_count_30d": summary.insider_sell_count_30d,
            "dilution_filing_count": summary.dilution_filing_count,
            "recent_8k_count": summary.recent_8k_count,
        }

    async def _fetch_sec_filings(self, ticker: str) -> dict[str, Any]:
        """
        Auto-query SEC EDGAR for recent filings when SEC client is available.

        Converts Filing objects to the dict format expected by fundamental agent:
        {"filings": [{"form": "S-3", "description": "...", "date": "2026-01-15"}]}

        H-004 RESOLUTION: Live SEC data feeds into fundamental agent pipeline.
        """
        try:
            filings = await self._sec_client.search_filings(
                ticker=ticker,
                form_types=["S-3", "S-3/A", "424B5", "8-K", "4", "10-K", "10-Q"],
                days_back=90,
            )
            return {
                "filings": [
                    {
                        "form": f.form_type,
                        "description": f.description,
                        "date": str(f.filed_date),
                    }
                    for f in filings[:10]  # Cap at 10 most recent
                ]
            }
        except Exception as e:
            logger.warning("SEC filing fetch failed for %s: %s", ticker, e)
            return {}

    def _get_vwap(self, ticker: str, fallback_price: float) -> float | None:
        """
        Get real VWAP from WebSocket client.

        H-006 RESOLUTION: Uses VWAPAccumulator from streaming trades when available.

        D106 FIX: Returns None when VWAP genuinely unavailable instead of
        price × 0.98 approximation. The old fallback auto-rejected entries
        ALL DAY when WebSocket disconnected (price always < price*0.98 is false,
        but the 0.98 bias created false VWAP-below signals). None means
        "no opinion" — the VWAP gate skips rather than rejecting.
        """
        if self._ws_client is not None:
            vwap = self._ws_client.get_vwap(ticker)
            if vwap is not None and vwap > 0:
                return vwap
        # D106: Return None (no opinion) instead of price*0.98 which
        # auto-rejected entries when WebSocket was unavailable
        return None

    async def _fetch_vix(self) -> float | None:
        """
        D101 §2.4: Fetch current VIX level for regime filtering.
        D104: Also computes VIXY daily delta for shock detection.

        Attempts to get VIX from Alpaca data API using VIXY (VIX short-term ETF)
        as a proxy, since ^VIX index is not directly available via Alpaca.
        Falls back to None if unavailable (filter is skipped).

        Cached per session — VIX doesn't change dramatically intraday.
        """
        if self._data_client is None:
            return None
        try:
            # D104: Fetch 2 bars to compute delta (previous close vs current)
            _vix_start = (
                datetime.now(timezone.utc) - timedelta(days=10)
            ).strftime("%Y-%m-%d")
            bars = await self._data_client.get_bars(
                "VIXY", timeframe="1Day", limit=2, start=_vix_start,
            )
            if bars and len(bars) > 0:
                # VIXY tracks VIX futures, roughly scale to VIX equivalent
                # VIXY ~$20 ≈ VIX 15, VIXY ~$30 ≈ VIX 25, VIXY ~$45 ≈ VIX 35
                vixy_close = float(bars[-1].get("c", bars[-1].get("close", 0)))
                if vixy_close > 0:
                    # Rough linear approximation: VIX ≈ VIXY * 0.8
                    vix_estimate = vixy_close * 0.8
                    logger.info(
                        "D101 VIX: VIXY=$%.2f → VIX estimate=%.1f",
                        vixy_close, vix_estimate,
                    )
                    # D104: Compute VIXY delta if 2 bars available
                    if len(bars) >= 2:
                        vixy_prev = float(
                            bars[-2].get("c", bars[-2].get("close", 0))
                        )
                        if vixy_prev > 0:
                            self._vix_delta_pct = (
                                (vixy_close - vixy_prev) / vixy_prev
                            ) * 100
                            logger.info(
                                "D104 VIX DELTA: VIXY prev=$%.2f → now=$%.2f "
                                "(delta=%+.1f%%)",
                                vixy_prev, vixy_close, self._vix_delta_pct,
                            )
                    return vix_estimate
        except Exception as e:
            logger.debug("D101: VIX fetch failed: %s — regime filter disabled", e)
        return None

    async def _fetch_spy_return(self) -> float | None:
        """D128: Fetch SPY return using snapshot for intraday accuracy.

        D104 original used daily bars (timeframe="1Day") which returned
        prev_close-to-today's-close. At 09:30, today's "close" = today's
        open, giving a stale gap-down value that never updates intraday.
        Mar 27: -1.40% at open blocked all trading while SPY recovered to -0.4%.

        D128 fix: Use snapshot API for live price, fall back to daily bars.
        Refreshed every 5 min (see caller).
        """
        if self._data_client is None:
            return None

        # Try snapshot first (live intraday price)
        try:
            snapshots = await self._data_client.get_snapshots(["SPY"])
            spy_data = snapshots.get("SPY", {})
            current = float(spy_data.get("last_price", 0)
                            or spy_data.get("current_price", 0))
            prev_close = float(spy_data.get("prev_close", 0)
                               or spy_data.get("previous_close", 0))
            if prev_close > 0 and current > 0:
                spy_return = ((current - prev_close) / prev_close) * 100
                logger.info(
                    "D128 SPY: prev=$%.2f → now=$%.2f (return=%+.2f%%)",
                    prev_close, current, spy_return,
                )
                return spy_return
        except Exception as e:
            logger.debug("D128: SPY snapshot failed, trying daily bars: %s", e)

        # Fallback: daily bars (original D104 method)
        try:
            _spy_start = (
                datetime.now(timezone.utc) - timedelta(days=10)
            ).strftime("%Y-%m-%d")
            bars = await self._data_client.get_bars(
                "SPY", timeframe="1Day", limit=2, start=_spy_start,
            )
            if bars and len(bars) >= 2:
                prev_close = float(
                    bars[-2].get("c", bars[-2].get("close", 0))
                )
                current = float(
                    bars[-1].get("c", bars[-1].get("close", 0))
                )
                if prev_close > 0:
                    spy_return = ((current - prev_close) / prev_close) * 100
                    logger.info(
                        "D104 SPY (daily fallback): prev=$%.2f → now=$%.2f "
                        "(return=%+.2f%%)",
                        prev_close, current, spy_return,
                    )
                    return spy_return
        except Exception as e:
            logger.debug("D104: SPY daily bars also failed: %s — gate disabled", e)
        return None

    async def _fetch_options_summary(self, ticker: str) -> dict:
        """
        D40: Fetch options chain summary for institutional agent.
        Returns empty dict if provider unavailable or fetch fails.
        """
        if self._options_provider is None:
            return {}
        try:
            chain = await self._options_provider.get_chain(ticker)
            if chain is None:
                return {}
            # Summarize options chain for the institutional agent
            return {
                "has_options": True,
                "chain_summary": getattr(chain, "to_summary_dict", lambda: {})(),
                "unusual_activity": getattr(chain, "detect_unusual", lambda: [])(),
            }
        except Exception as e:
            logger.debug("Options fetch for %s failed: %s", ticker, e)
            return {}

    def _get_best_prompt(self, agent_id: str) -> dict[str, str] | None:
        """
        Get the best prompt variant from the arena for an agent.

        H-003 RESOLUTION: PromptArena Elo-rated selection.
        Cold start (<10 matches) → random exploration.
        Warm (≥10 matches) → highest Elo exploitation.

        Args:
            agent_id: The agent's identifier (e.g., "news_agent").

        Returns:
            Dict with "system_prompt" and "user_prompt_template" keys,
            or None if no arena or no variants available.

        Ref: docs/research/ARENA_LIVE_SELECTION.md
        """
        if self._prompt_arena is None:
            return None
        try:
            variant = self._prompt_arena.get_best_variant(agent_id)
            if variant is not None:
                return {
                    "system_prompt": variant.system_prompt,
                    "user_prompt_template": variant.user_prompt_template,
                    "variant_id": variant.variant_id,
                }
        except Exception as e:
            logger.debug("Arena selection failed for %s: %s", agent_id, e)
        return None

    @staticmethod
    def _assess_data_completeness(
        news_items: list,
        market_data: dict,
        sec_filings: dict,
        candidate: CandidateStock,
    ) -> dict[str, dict[str, Any]]:
        """
        Assess what data each agent will actually receive before dispatch.

        Returns a per-agent report: {agent_id: {"status": COMPLETE|PARTIAL|EMPTY,
        "fields_present": [...], "fields_missing": [...]}}.

        This prevents confabulation: agents receiving insufficient data are logged
        as DEGRADED so signals can be interpreted accordingly.
        """
        filings_list = sec_filings.get("filings", []) if isinstance(sec_filings, dict) else []

        report: dict[str, dict[str, Any]] = {
            "news_agent": {
                "status": "COMPLETE" if news_items else "EMPTY",
                "items_count": len(news_items),
                "fields_present": ["news_items"] if news_items else [],
                "fields_missing": [] if news_items else ["news_items"],
            },
            "technical_agent": {
                "status": "COMPLETE" if market_data.get("price_data") and market_data.get("indicators") else (
                    "PARTIAL" if market_data.get("price_data") or market_data.get("indicators") else "EMPTY"
                ),
                "fields_present": [k for k in ("price_data", "indicators") if market_data.get(k)],
                "fields_missing": [k for k in ("price_data", "indicators") if not market_data.get(k)],
            },
            "fundamental_agent": {
                "status": "COMPLETE" if (candidate.float_shares and filings_list) else (
                    "PARTIAL" if (candidate.float_shares or filings_list) else "EMPTY"
                ),
                "fields_present": [
                    f for f, v in [
                        ("float_shares", candidate.float_shares),
                        ("sec_filings", filings_list),
                    ] if v
                ],
                "fields_missing": [
                    f for f, v in [
                        ("float_shares", candidate.float_shares),
                        ("sec_filings", filings_list),
                    ] if not v
                ],
            },
            "institutional_agent": {
                "status": "COMPLETE" if candidate.gex_net is not None else "EMPTY",
                "fields_present": ["gex_data"] if candidate.gex_net is not None else [],
                "fields_missing": (
                    ["options_data", "dark_pool_data", "insider_trades"]
                    + ([] if candidate.gex_net is not None else ["gex_data"])
                ),
            },
            "deep_search_agent": {
                "status": "PARTIAL" if filings_list else "EMPTY",
                "fields_present": ["sec_filings"] if filings_list else [],
                "fields_missing": [
                    f for f, v in [
                        ("sec_filings", filings_list),
                        ("social_data", None),
                        ("historical_moves", None),
                        ("sector_peers", None),
                    ] if not v
                ],
            },
        }
        return report

    async def _dispatch_agents(
        self,
        candidate: CandidateStock,
        news_items: list,
        market_data: dict,
        sec_filings: dict,
    ) -> list[AgentSignal]:
        """
        Two-phase agent dispatch (H-007 fix), now with ALL 6 agents + arena selection.
        Phase A: 5 analytical agents in parallel (news, technical, fundamental, institutional, deep_search)
        Phase B: Risk agent receives Phase A results for informed adversarial assessment

        Arena Integration (H-003): If PromptArena is available, selects best Elo-rated
        variant for each agent. Variant IDs are tracked in _last_variant_map for
        post-trade Elo feedback.

        Data Completeness Guard: Logs per-agent data quality before dispatch.
        Agents receiving EMPTY data are flagged — their signals should be
        weighted lower or treated as low-confidence.

        Ref: ADR-001 (Parallel fan-out, <90s budget)
        Resolution: H-007 (risk agent was receiving empty candidate_signals)
        """
        # ── Data Completeness Assessment ──
        data_report = self._assess_data_completeness(
            news_items=news_items,
            market_data=market_data,
            sec_filings=sec_filings,
            candidate=candidate,
        )
        empty_agents = [aid for aid, r in data_report.items() if r["status"] == "EMPTY"]
        partial_agents = [aid for aid, r in data_report.items() if r["status"] == "PARTIAL"]
        complete_agents = [aid for aid, r in data_report.items() if r["status"] == "COMPLETE"]

        logger.info(
            "%s DATA QUALITY: complete=%d partial=%d empty=%d | %s",
            candidate.ticker,
            len(complete_agents),
            len(partial_agents),
            len(empty_agents),
            " | ".join(
                f"{aid}={r['status']}" for aid, r in data_report.items()
            ),
        )
        if empty_agents:
            logger.warning(
                "%s EMPTY data for agents: %s — signals may be unreliable (confabulation risk)",
                candidate.ticker,
                ", ".join(empty_agents),
            )

        # Store report for post-trade analysis and push to metrics
        self._last_data_report = data_report
        metrics = get_metrics()
        metrics.record_data_completeness(data_report)

        # ── Arena variant selection (H-003) ──
        agent_ids = [
            "news_agent", "technical_agent", "fundamental_agent",
            "institutional_agent", "deep_search_agent",
        ]
        variant_map: dict[str, str] = {}
        for aid in agent_ids:
            prompt = self._get_best_prompt(aid)
            if prompt:
                variant_map[aid] = prompt["variant_id"]
                logger.debug(
                    "Arena selected %s for %s", prompt["variant_id"], aid,
                )

        # Track for post-trade analysis
        self._last_variant_map = variant_map

        # ── Phase A: 5 Analytical agents with staggered launch + partial results ──
        # D92: asyncio.wait replaces asyncio.gather for partial result handling.
        # If 4/5 agents complete in 8s and 1 hangs for 15s, we proceed with 4
        # instead of waiting for all 5. Minimum 4 results required.
        # D92: Stagger launches by 150ms to avoid Together AI rate-limit bursts.
        _latency_metrics = get_metrics()
        _min_analytical_results = 4  # D92: Accept 4/5 results (80% coverage)

        async def _timed_agent(coro: Any, agent_id: str) -> Any:
            """Wrap an agent coroutine with latency observation."""
            t0 = time.monotonic()
            try:
                return await coro
            finally:
                _latency_metrics.agent_latency.observe(time.monotonic() - t0)

        # D92: Pre-fetch options before creating tasks (needed for institutional)
        _options_data = await self._fetch_options_summary(candidate.ticker)

        # D92: Create agent coroutines with staggered start (150ms between launches)
        _stagger_delay = 0.15  # 150ms between agent launches

        async def _staggered_agent(coro: Any, agent_id: str, delay: float) -> Any:
            """Launch agent with staggered delay to avoid rate-limit bursts."""
            if delay > 0:
                await asyncio.sleep(delay)
            return await _timed_agent(coro, agent_id)

        _agent_defs = [
            ("news_agent", self._news_agent.analyze(
                ticker=candidate.ticker,
                company_name=getattr(candidate, "company_name", candidate.ticker),
                news_items=news_items,
                market_cap=candidate.market_cap,
                sector="Unknown",
            )),
            ("technical_agent", self._technical_agent.analyze(
                ticker=candidate.ticker,
                current_price=candidate.current_price,
                rvol=candidate.rvol,
                gap_pct=candidate.gap_pct,  # D126: gap momentum mode
                vwap=self._get_vwap(candidate.ticker, candidate.current_price),
                price_data=market_data.get("price_data", {}),
                indicators=market_data.get("indicators", {}),
            )),
            ("fundamental_agent", self._fundamental_agent.analyze(
                ticker=candidate.ticker,
                float_shares=candidate.float_shares,
                shares_outstanding=None,
                short_interest=None,
                insider_ownership_pct=None,
                institutional_ownership_pct=None,
                recent_filings=sec_filings.get("filings", []) if isinstance(sec_filings, dict) else [],
            )),
            ("institutional_agent", self._institutional_agent.analyze(
                ticker=candidate.ticker,
                rvol=candidate.rvol,
                options_data=_options_data,
                dark_pool_data={},
                insider_trades=[],
                gex_data=(
                    {
                        "gex_net": candidate.gex_net,
                        "gex_normalized": candidate.gex_normalized,
                        "gamma_flip_price": candidate.gamma_flip_price,
                        "gex_regime": candidate.gex_regime,
                    }
                    if candidate.gex_net is not None
                    else None
                ),
            )),
            ("deep_search_agent", self._deep_search_agent.analyze(
                ticker=candidate.ticker,
                sec_filings=sec_filings.get("filings", []) if isinstance(sec_filings, dict) else [],
                social_data={},
                historical_moves=[],
                sector_peers=[],
            )),
            # D106 §2E: ManipulationClassifier — runs in parallel with other 5 agents.
            # Does NOT contribute to MFCS — only gates entry parameters.
            ("manipulation_classifier", self._manipulation_classifier.analyze(
                ticker=candidate.ticker,
                news_items=news_items,
                rvol=candidate.rvol,
                gap_pct=candidate.gap_pct,
                premarket_volume=candidate.premarket_volume,
                float_shares=candidate.float_shares,
                filing_summary=self._build_filing_summary(sec_filings),
                sec_filings=sec_filings.get("filings", []) if isinstance(sec_filings, dict) else [],
            )),
        ]

        # D100: Skip agents with zero weight — saves LLM tokens and latency.
        # Map agent_id → scoring weight category for filtering.
        # D106: manipulation_classifier has weight 1.0 (always run, not gated by scoring).
        _agent_weight_map = {
            "news_agent": self._settings.scoring.catalyst_news,
            "technical_agent": self._settings.scoring.technical,
            "fundamental_agent": self._settings.scoring.float_structure,
            "institutional_agent": self._settings.scoring.institutional,
            "deep_search_agent": self._settings.scoring.deep_search,
            "manipulation_classifier": 1.0,  # Always run
        }
        _active_defs = []
        for agent_id, coro in _agent_defs:
            w = _agent_weight_map.get(agent_id, 1.0)
            if w <= 0.0:
                logger.info(
                    "%s D100: Skipping %s (weight=%.2f) — saves LLM tokens",
                    candidate.ticker, agent_id, w,
                )
                coro.close()  # Prevent "coroutine never awaited" warning
                continue
            _active_defs.append((agent_id, coro))

        # Create staggered tasks
        analytical_tasks_map: dict[asyncio.Task, str] = {}
        for i, (agent_id, coro) in enumerate(_active_defs):
            task = asyncio.create_task(
                _staggered_agent(coro, agent_id, i * _stagger_delay),
                name=f"agent_{agent_id}",
            )
            analytical_tasks_map[task] = agent_id

        # D92b: Two-phase wait strategy for partial results.
        # Phase 1: Wait up to Tier 2 timeout + stagger + grace for the FAST agents.
        #   The 4 Tier 2 agents complete in 3-15s. We need at least 4 results.
        # Phase 2: If a Tier 1 agent (news) is still in its fallback chain,
        #   give it extra time (up to full fallback timeout) to complete.
        # This avoids cancelling news_agent mid-fallback while still being fast.
        # 2026-05-12 fix: was tier2 (15s+stagger+3=~19s), but Phase A includes
        # manipulation_classifier which is Tier 1 (25s native). 187 of 264
        # evals today (71%) cancelled manipulation_classifier mid-flight,
        # leaving ManipulationSignal absent for entry param gating.
        # Switch to tier1 timeout so the slowest legitimate agent has room.
        # Fast path unchanged: if 4+ agents done early, asyncio.wait returns
        # immediately. Only the SLOW-straggler path uses the longer wait.
        _phase1_wait = self._settings.models.litellm_timeout_tier1 + (
            len(_active_defs) * _stagger_delay  # D100: Use active agent count
        ) + 3.0  # Tier 1 timeout + stagger + 3s grace

        done, pending = await asyncio.wait(
            analytical_tasks_map.keys(),
            timeout=_phase1_wait,
            return_when=asyncio.ALL_COMPLETED,
        )

        # Phase 2: If we have enough fast results but slow agents are still
        # in their fallback chain, give them extra time to complete.
        if pending and len(done) >= _min_analytical_results:
            # Already have enough results — give stragglers one more fallback
            # cycle (tier1 timeout) to complete via fallback/emergency model.
            _phase2_wait = self._settings.models.litellm_timeout_tier1 + 3.0
            logger.info(
                "%s D92: %d/%d agents done (enough), giving %d stragglers %.0fs more for fallback...",
                candidate.ticker, len(done), len(_agent_defs),
                len(pending), _phase2_wait,
            )
            more_done, pending = await asyncio.wait(
                pending, timeout=_phase2_wait, return_when=asyncio.ALL_COMPLETED,
            )
            done = done | more_done
        elif len(done) < _min_analytical_results and pending:
            # Not enough results — wait a bit for any straggler
            logger.warning(
                "%s D92: Only %d/%d agents done, waiting 5s more for stragglers...",
                candidate.ticker, len(done), len(_agent_defs),
            )
            more_done, pending = await asyncio.wait(
                pending, timeout=5.0, return_when=asyncio.FIRST_COMPLETED,
            )
            done = done | more_done

        # Cancel any still-pending tasks
        for task in pending:
            _pending_agent = analytical_tasks_map.get(task, "unknown")
            task.cancel()
            logger.warning(
                "%s D99: Cancelled slow agent %s (still pending after phase-1 %.0fs)",
                candidate.ticker, _pending_agent, _phase1_wait,
            )

        # Collect results from completed tasks
        signals: list[AgentSignal] = []
        dispatch_metrics = get_metrics()
        for task in done:
            agent_id = analytical_tasks_map.get(task, "unknown")
            try:
                result = task.result()
                if isinstance(result, AgentSignal):
                    signals.append(result)
                else:
                    logger.warning(
                        "D92: Agent %s returned non-AgentSignal result (type=%s), discarding",
                        agent_id, type(result).__name__,
                    )
            except Exception as e:
                dispatch_metrics.agent_errors.inc()
                # D92 (2026-05-12, doc 153/154/155 hygiene): include traceback
                # so the exact bug site is captured in logs. Yesterday's
                # 'NoneType' object is not subscriptable bug fired 20x in
                # manipulation_classifier without traceback context, blocking
                # surgical fix. exc_info=True is non-fatal when e is None or
                # truthy; safe to add here.
                logger.error("D92: Agent %s error: %s", agent_id, e, exc_info=True)

        logger.info(
            "%s D92: Phase A complete — %d/%d agents returned signals (min=%d)",
            candidate.ticker, len(signals), len(_agent_defs), _min_analytical_results,
        )

        # ── D85: Pre-score MFCS to decide if risk agent is worth waiting for ──
        # D92: Risk agent now runs at 15s timeout (was 30-45s). Still sequential
        # because it needs Phase A signals. But with instruct models, it's fast.
        pre_scored = compute_mfcs(
            candidate=candidate,
            signals=signals,
            weights={
                "catalyst_news": self._settings.scoring.catalyst_news,
                "technical": self._settings.scoring.technical,
                "volume_rvol": self._settings.scoring.volume_rvol,
                "float_structure": self._settings.scoring.float_structure,
                "institutional": self._settings.scoring.institutional,
                "deep_search": self._settings.scoring.deep_search,
            },
            risk_aversion_lambda=self._settings.scoring.risk_aversion_lambda,
            debate_threshold=1.0,  # Don't trigger debate here
        )

        buy_threshold = self._settings.scoring.mfcs_buy_threshold
        if pre_scored.mfcs < buy_threshold:
            logger.info(
                "D85 FAST-SKIP: %s pre-MFCS=%.3f < %.3f — skipping risk agent",
                candidate.ticker, pre_scored.mfcs, buy_threshold,
            )
            return signals

        # ── Phase B: Risk agent (CRITICAL — must complete for trade decisions) ──
        # D101: Risk agent is now deterministic — zero failure rate.
        # D92: Still marked CRITICAL in case of unexpected errors.
        risk_prompt = self._get_best_prompt("risk_agent")
        if risk_prompt:
            variant_map["risk_agent"] = risk_prompt["variant_id"]
        self._last_variant_map = variant_map  # Update with risk agent variant

        # D101: Enrich market_data with candidate fields for deterministic risk agent.
        # The deterministic agent needs structured fields (rvol, float_shares, gap_pct)
        # that were previously interpreted from free-text by the LLM.
        _risk_market_data = dict(market_data)
        _risk_market_data.setdefault("current_price", candidate.current_price)
        _risk_market_data.setdefault("rvol", candidate.rvol)
        _risk_market_data.setdefault("gap_pct", candidate.gap_pct)
        _risk_market_data.setdefault("has_news", candidate.has_news_catalyst)
        if candidate.float_shares is not None:
            _risk_market_data.setdefault("float_shares", candidate.float_shares)
        if candidate.avg_daily_volume is not None:
            _risk_market_data.setdefault("avg_daily_volume", candidate.avg_daily_volume)

        try:
            risk_result = await _timed_agent(self._risk_agent.analyze(
                ticker=candidate.ticker,
                candidate_signals=signals,  # H-007 FIX: now populated with analytical signals
                market_data=_risk_market_data,
                sec_filings=sec_filings,
            ), "risk_agent")
            if isinstance(risk_result, AgentSignal):
                signals.append(risk_result)
            else:
                logger.warning(
                    "%s D92: Risk agent returned non-signal result: %s",
                    candidate.ticker, type(risk_result).__name__,
                )
        except Exception as e:
            logger.error(
                "%s D92: CRITICAL Risk agent failed: %s — proceeding without risk assessment",
                candidate.ticker, e,
            )

        return signals

    @staticmethod
    def _extract_risk_signal(
        signals: list[AgentSignal],
    ) -> Any | None:
        """Extract the RiskSignal from the agent signals list."""
        from src.core.models import RiskSignal
        for sig in signals:
            if isinstance(sig, RiskSignal):
                return sig
        return None

    def _replay_experiment_variants(
        self,
        candidate: CandidateStock,
        agent_signals: list[AgentSignal],
        scored: ScoredCandidate,
        verdict: TradeVerdict,
        trade_id: str,
    ) -> None:
        """
        D102: Replay evaluation through all active experiment variants.

        Re-runs compute_mfcs() with different weight/threshold/lambda configs
        and computes variant stop/sizing for ATR experiments. Zero LLM calls.

        Results are written to the experiment journal (JSONL).
        This method is synchronous and sub-millisecond per variant.

        Ref: D102 (Experimentation Framework Phase 1)
        """
        if not self._experiment_registry.loaded:
            return

        experiments = self._experiment_registry.get_active_experiments()
        if not experiments:
            return

        # Safety cap on total variants
        max_variants = self._settings.experiments.max_variants_per_candidate
        ticker = candidate.ticker
        candidate_atr = self._atr_cache.get(ticker)

        variant_results: list[VariantResult] = []
        variants_processed = 0

        for experiment in experiments:
            for variant in experiment.variants:
                if variants_processed >= max_variants:
                    logger.debug(
                        "D102: Hit variant cap (%d) for %s — stopping replay",
                        max_variants, ticker,
                    )
                    break

                # Apply overrides to get variant settings (deep copy)
                variant_settings = ExperimentRegistry.apply_overrides(
                    self._settings, variant.overrides
                )

                # D122: Re-apply deflation if variant overrides it.
                # Bug fix: deflation was applied upstream (base.py) and baked into
                # signal.confidence. The replay received already-deflated signals,
                # so changing confidence_deflation_factor had zero effect (~3000
                # wasted experiment records). Fix: use raw_confidence when available
                # and re-apply the variant's deflation factor.
                variant_deflation = variant_settings.scoring.confidence_deflation_factor
                primary_deflation = self._settings.scoring.confidence_deflation_factor
                if abs(variant_deflation - primary_deflation) > 1e-6:
                    # Variant has a different deflation — rebuild signals
                    replay_signals = []
                    for sig in agent_signals:
                        if sig.raw_confidence is not None:
                            new_conf = min(1.0, max(0.0, sig.raw_confidence * variant_deflation))
                            replay_signals.append(sig.model_copy(update={"confidence": new_conf}))
                        else:
                            # Deterministic agents (no raw_confidence) — pass through
                            replay_signals.append(sig)
                    _replay_sigs = replay_signals
                else:
                    _replay_sigs = agent_signals

                # Re-score with variant weights/threshold/lambda
                variant_weights = {
                    "catalyst_news": variant_settings.scoring.catalyst_news,
                    "technical": variant_settings.scoring.technical,
                    "volume_rvol": variant_settings.scoring.volume_rvol,
                    "float_structure": variant_settings.scoring.float_structure,
                    "institutional": variant_settings.scoring.institutional,
                    "deep_search": variant_settings.scoring.deep_search,
                }
                variant_scored = compute_mfcs(
                    candidate=candidate,
                    signals=_replay_sigs,
                    weights=variant_weights,
                    risk_aversion_lambda=variant_settings.scoring.risk_aversion_lambda,
                    debate_threshold=variant_settings.debate.mfcs_debate_threshold,
                )

                # Determine if variant would enter
                would_enter = (
                    variant_scored.mfcs >= variant_settings.scoring.mfcs_buy_threshold
                )

                # Compute variant stop/sizing (for ATR experiments)
                variant_stop = None
                variant_qty = None
                variant_size_pct = None
                if would_enter and candidate_atr and candidate_atr > 0:
                    atr_mult = variant_settings.execution.initial_stop_atr_multiplier
                    stop_floor = variant_settings.execution.initial_stop_floor_pct
                    stop_distance = max(
                        atr_mult * candidate_atr,
                        candidate.current_price * stop_floor,
                    )
                    variant_stop = round(
                        candidate.current_price - stop_distance, 4
                    )
                    if stop_distance > 0:
                        # D211: Use actual equity when available, fall back to $100k
                        _var_equity = 100_000.0
                        if self._position_manager is not None:
                            _var_equity = getattr(
                                self._position_manager, "_starting_equity", 100_000.0
                            ) or 100_000.0
                        risk_per_trade = (
                            _var_equity * variant_settings.execution.risk_per_trade_pct
                        )
                        variant_qty = int(risk_per_trade / stop_distance)
                        variant_size_pct = round(
                            (variant_qty * candidate.current_price) / _var_equity, 4
                        )

                variant_results.append(
                    VariantResult(
                        experiment_id=experiment.experiment_id,
                        variant_id=variant.variant_id,
                        overrides=variant.overrides,
                        mfcs=round(variant_scored.mfcs, 4),
                        would_enter=would_enter,
                        stop_loss=variant_stop,
                        position_size_qty=variant_qty,
                        position_size_pct=variant_size_pct,
                        reasoning=(
                            f"MFCS={variant_scored.mfcs:.3f} vs "
                            f"threshold={variant_settings.scoring.mfcs_buy_threshold}"
                        ),
                    )
                )
                variants_processed += 1

        if variant_results:
            # Write to experiment journal
            entry = ExperimentJournalEntry(
                trade_id=trade_id,
                ticker=ticker,
                timestamp=datetime.now(timezone.utc).isoformat(),
                primary_mfcs=round(scored.mfcs, 4),
                primary_action=verdict.action,
                primary_stop_loss=(
                    verdict.stop_loss
                    if verdict.action in ("BUY", "STRONG_BUY")
                    else None
                ),
                variant_results=variant_results,
            )
            self._experiment_journal.record(entry)

            would_enter_count = sum(
                1 for v in variant_results if v.would_enter
            )
            logger.info(
                "D102: Experiment replay for %s: %d variants, %d would-enter "
                "(primary=%s MFCS=%.3f)",
                ticker,
                len(variant_results),
                would_enter_count,
                verdict.action,
                scored.mfcs,
            )

            # ── Per-experiment breakdown for console visibility ──
            # Group by experiment for a readable summary per evaluation
            _exp_groups: dict[str, list[VariantResult]] = {}
            for _vr in variant_results:
                _exp_groups.setdefault(_vr.experiment_id, []).append(_vr)

            for _exp_id, _variants in _exp_groups.items():
                _enter = sum(1 for _v in _variants if _v.would_enter)
                _mfcs_vals = [_v.mfcs for _v in _variants]
                _best = max(_mfcs_vals) if _mfcs_vals else 0.0
                _worst = min(_mfcs_vals) if _mfcs_vals else 0.0
                _detail_parts = []
                for _v in _variants:
                    _flag = "BUY" if _v.would_enter else "---"
                    _detail_parts.append(
                        f"{_v.variant_id}={_v.mfcs:.3f}[{_flag}]"
                    )
                logger.info(
                    "  🧪 %s: %d/%d enter | MFCS range [%.3f–%.3f] | %s",
                    _exp_id, _enter, len(_variants),
                    _worst, _best,
                    " ".join(_detail_parts),
                )

    def _compute_atr_stop(
        self, ticker: str, entry: float, gap_pct: float = 0.0,
    ) -> float:
        """
        D100: Compute ATR-based initial stop for a position.

        Formula: stop = entry - max(multiplier × ATR_14d, entry × floor_pct)

        D104: Gap-aware fallback when ATR unavailable.
        Instead of fixed 5.5%, use max(stop_loss_pct, |gap_pct| × 0.5),
        capped at 15%. For NVTS (20% gap): stop width = 10.08% instead of 5.5%.
        """
        candidate_atr = self._atr_cache.get(ticker)
        if candidate_atr and candidate_atr > 0:
            atr_stop_distance = max(
                self._settings.execution.initial_stop_atr_multiplier * candidate_atr,
                entry * self._settings.execution.initial_stop_floor_pct,
            )
            # D122: Gap-day stop widening. Entry-delay sweep showed 41-47%
            # of stops trigger after price had prior gain — too tight on gap days.
            # On large gap-ups, intraday vol is 2-3x daily ATR. Use half the
            # gap % as a minimum stop distance to prevent false stop-outs.
            if (
                self._settings.execution.gap_day_stop_widening_enabled
                and abs(gap_pct) > self._settings.execution.gap_day_threshold
            ):
                gap_floor = entry * abs(gap_pct) * 0.5
                if gap_floor > atr_stop_distance:
                    logger.info(
                        "%s D122: Gap-day widening — gap=%.1f%%, "
                        "gap_floor=$%.4f > ATR_stop=$%.4f, using gap_floor",
                        ticker, abs(gap_pct) * 100, gap_floor, atr_stop_distance,
                    )
                    atr_stop_distance = gap_floor
            # Sweep fix: Cap ATR stop at a % of entry price. Without this cap,
            # sub-dollar stocks with high ATR get absurd stop distances.
            # MOBX at $0.53 with ATR=$0.24 → 2.0×$0.24 = $0.48 = 90.3% stop.
            # D124: When gap-day widening is active, use a dynamic cap based on
            # half the gap (up to gap_day_stop_cap). Otherwise the 20% cap
            # defeats widening for any gap > 40% (ANNA: 63% gap → 31.5% floor
            # → capped to 20%, making widening useless).
            _gap_day_active = (
                self._settings.execution.gap_day_stop_widening_enabled
                and abs(gap_pct) > self._settings.execution.gap_day_threshold
            )
            if _gap_day_active:
                _dynamic_cap = min(
                    abs(gap_pct) * 0.5,
                    self._settings.execution.gap_day_stop_cap,
                )
                max_stop_distance = entry * _dynamic_cap
            else:
                max_stop_distance = entry * 0.20
            if atr_stop_distance > max_stop_distance:
                _cap_pct = (_dynamic_cap * 100) if _gap_day_active else 20.0
                logger.warning(
                    "%s D100: ATR stop distance $%.4f (%.1f%%) exceeds %.0f%% cap — "
                    "capping at $%.4f",
                    ticker, atr_stop_distance,
                    (atr_stop_distance / entry * 100) if entry > 0 else 0,
                    _cap_pct, max_stop_distance,
                )
                atr_stop_distance = max_stop_distance
            stop = entry - atr_stop_distance
            stop_pct = atr_stop_distance / entry * 100 if entry > 0 else 0
            logger.info(
                "%s D100: ATR stop=$%.2f (entry=$%.2f - max(%.1f×$%.4f, %.1f%%)=$%.4f = %.1f%%)",
                ticker, stop, entry,
                self._settings.execution.initial_stop_atr_multiplier,
                candidate_atr,
                self._settings.execution.initial_stop_floor_pct * 100,
                atr_stop_distance,
                stop_pct,
            )
            return stop
        else:
            # D104: Gap-aware fallback — wider stop for high-gap stocks
            base_pct = self._settings.execution.stop_loss_pct  # Default 5.5%
            gap_stop_pct = max(base_pct, abs(gap_pct) * 0.5)  # Half the gap
            gap_stop_pct = min(gap_stop_pct, 0.15)  # Cap at 15%
            stop = entry * (1 - gap_stop_pct)
            logger.info(
                "%s D104: ATR unavailable — gap-aware fallback stop=$%.2f "
                "(gap=%.1f%%, stop_width=%.1f%%, base=%.1f%%, cap=15%%)",
                ticker, stop,
                abs(gap_pct) * 100,
                gap_stop_pct * 100,
                base_pct * 100,
            )
            return stop

    def _build_trade_verdict(
        self,
        candidate: CandidateStock,
        scored: ScoredCandidate,
        debate: DebateResult | None,
        risk_signal: Any | None,
        manipulation_phase: str = "UNCERTAIN",
        kelly_result: KellyTierResult | None = None,
        catalyst_profile: Any | None = None,
        d211_local_vix_reduction: float = 1.0,
    ) -> TradeVerdict:
        """Build a positive trade verdict with sizing and levels.

        D106 §2D: manipulation_phase gates entry parameters:
        - PROMOTIONAL_EARLY: half position, tighter stop (1.5x ATR), aggressive
          targets (+3/+6/+10%), hard 10:30 AM exit deadline, $500 min position floor.
        - ORGANIC_MOMENTUM / UNCERTAIN: standard parameters.
        - PROMOTIONAL_LATE: should never reach here (blocked in evaluate_candidate_inner).

        Mon 2026-04-20 Bug #2 fix: ``d211_local_vix_reduction`` now passes
        in as a parameter (default 1.0 = no reduction). Previously this
        function read a bare name ``_d211_local_vix_reduction`` that lived
        in the *caller's* local scope (`_evaluate_candidate_inner`).
        Python does not propagate caller locals into a sibling method's
        scope, so every call to ``_build_trade_verdict`` raised NameError
        at the ``if _d211_local_vix_reduction < 1.0`` line. Caught
        post-mortem of the Mon 09:40 EDT session crash.
        """
        if debate:
            action = debate.verdict
            confidence = debate.confidence
            entry = debate.entry_price or candidate.current_price
            # D100: ATR-based stop replaces fixed %. Debate stop is clamped to ATR stop.
            atr_stop = self._compute_atr_stop(
                candidate.ticker, entry, gap_pct=candidate.gap_pct,
            )
            debate_stop = debate.stop_loss
            if debate_stop and debate_stop > 0 and debate_stop < entry:
                stop = max(debate_stop, atr_stop)  # max() = tighter (closer to entry)
            else:
                stop = atr_stop
            # D94: Widened from 3/6/10% to 5/10/20% — small caps move 20-100%+
            targets = debate.target_prices or [entry * 1.05, entry * 1.10, entry * 1.20]
            pos_size = debate.position_size
        else:
            # D100: Debate engine killed. MFCS > buy_threshold is the sole gate.
            # Previous logic required qualifies_for_debate=True (MFCS >= debate_threshold)
            # which created a paradox when debate was disabled — nothing could trade.
            # Now: any candidate clearing mfcs_buy_threshold (0.30) can BUY directly.
            buy_threshold = self._settings.scoring.mfcs_buy_threshold

            # D216: Dynamic threshold adjustment based on agent data quality
            # When most agents received EMPTY data, the max achievable MFCS is
            # structurally lower. Reduce threshold proportionally.
            # D216 FIX: Use per-ticker report only — never fall back to _last_data_report
            # which may belong to a DIFFERENT candidate evaluated concurrently (D217 race fix)
            _d216_report = self._data_report_by_ticker.get(candidate.ticker, {})
            _d216_empty = sum(
                1 for v in _d216_report.values()
                if isinstance(v, dict) and v.get("status") == "EMPTY"
            )
            _d216_total = max(len(_d216_report), 1)
            _d216_empty_frac = _d216_empty / _d216_total
            if _d216_empty_frac > 0.3:
                _d216_reduction = min(_d216_empty_frac * 0.15, 0.10)
                buy_threshold = max(0.10, buy_threshold - _d216_reduction)
                logger.info(
                    "%s D216: Dynamic threshold: %d/%d agents EMPTY -> %.3f -> %.3f",
                    candidate.ticker, _d216_empty, _d216_total,
                    self._settings.scoring.mfcs_buy_threshold, buy_threshold,
                )

            action = "BUY" if scored.mfcs > buy_threshold else "HOLD"
            confidence = scored.mfcs
            entry = candidate.current_price
            stop = self._compute_atr_stop(  # D100: ATR-based
                candidate.ticker, entry, gap_pct=candidate.gap_pct,
            )
            # D94: Widened from 3/6/10% to 5/10/20%
            targets = [entry * 1.05, entry * 1.10, entry * 1.20]
            pos_size = "QUARTER"

        # ════════════════════════════════════════════════════════════════
        # ⚠️  ADVISORY-ONLY UNDER paper_aggressive_mode (the default).
        #     Everything below — the D37 base map, D94 MFCS/debate scaling,
        #     D46 float_mult, D101 VIX, D211 MOMENTUM-VIX, D106 PROMOTIONAL_EARLY
        #     0.5x, D118 catalyst risk_scale, D115 Kelly cap — produces
        #     `verdict.position_size_pct`, logged as the "D96 SIZING ... final="
        #     number. BUT the executor THROWS THIS NUMBER AWAY whenever
        #     config.paper_aggressive_mode is True (config/settings.py:650,
        #     the default): see alpaca_executor.py:239,254, where effective_pct
        #     is the flat D150 tier_pct (15% for Tier 3), and qty is sized by
        #     fixed-risk (risk_per_trade_pct ÷ stop_distance) — not by this %.
        #
        #     Proof (logs/momentum_2026-05-26.log, LFS):
        #       D96 SIZING   ... final=4.8%        ← computed here
        #       D96 EXECUTION ... actual_alloc=10.2% (method=fixed_risk)  ← deployed
        #     The 4.8% had ZERO effect on capital deployed.
        #
        #     So the D46/D101/D106/MFCS multiplier stack below only shrinks/grows
        #     a number that is cosmetic under the default mode. DO NOT spend time
        #     tuning these multipliers expecting deployed capital to change — it
        #     won't, until the executor is re-wired. position_size_pct still
        #     matters: (a) when paper_aggressive_mode is False, and (b) as the
        #     >0 gate at alpaca_executor.py:130. It is also recorded for audit.
        #
        #     This divergence is KNOWN and is the deliberate D150 tier override,
        #     NOT a bug — but the D96 chain that survives it is effectively dead
        #     code. Re-wiring is a tracked, gated decision (Phase B item 3,
        #     "reconnect the brain OR raise fixed-risk on ELITE", conviction-
        #     gated + env-flagged + A/B'd) in
        #     docs/research-log/176_mariana_trench_audit_why_not_five_percent.md §3.
        #     Change executor behavior there, with that gating — not by quietly
        #     editing the math below.
        # ════════════════════════════════════════════════════════════════
        # Convert position size to percentage
        # D37: Competition mode sizing — FULL=$15k, HALF=$10k, QUARTER=$5k on $100k
        size_map = {"FULL": 0.15, "HALF": 0.10, "QUARTER": 0.05, "NONE": 0.0}
        position_pct = size_map.get(pos_size, 0.0)

        # D94: MFCS-scaled sizing for all paths (debate and no-debate)
        # D115: Kelly tier overrides max_pct ceiling when active
        min_pct = 0.05
        if kelly_result is not None and self._settings.kelly_tier.enabled:
            max_pct = kelly_result.max_position_pct
        else:
            max_pct = self._settings.execution.max_position_pct
        if debate and debate.confidence > 0:
            # Use max of divergence and a floor (0.3) so high-confidence/low-divergence
            # trades (agents agreed → CV skip) still get sized up
            div_factor = max(min(debate.debate_divergence, 1.0), 0.3)
            scale = div_factor * min(debate.confidence, 1.0)
            position_pct = min_pct + (max_pct - min_pct) * scale
        elif scored.mfcs > 0:
            # D121 BUG-M4: Fall through to MFCS sizing when debate has zero
            # confidence OR when no debate occurred. Previously the `not debate`
            # guard meant debate-with-zero-confidence got position_pct=0.
            mfcs_scale = min(1.0, scored.mfcs / 0.5)
            position_pct = min_pct + (max_pct - min_pct) * mfcs_scale

        # D46: Float-based position size multiplier
        # D121 BUG-E6: `or` treats 0 as falsy → 0-float becomes 50M (large-cap).
        # Use explicit None check so 0 maps to micro-float (conservative).
        float_shares = candidate.float_shares if candidate.float_shares is not None else 50_000_000
        if float_shares < 5_000_000:
            float_mult = 1.5   # +50% sizing for micro-float
        elif float_shares < 15_000_000:
            float_mult = 1.2   # +20% for low-float
        elif float_shares < 30_000_000:
            float_mult = 1.0   # Standard for mid-float
        else:
            float_mult = 0.7   # -30% for high-float (lower volatility)
        _pre_float_pct = position_pct
        position_pct = position_pct * float_mult

        # D101 §2.4: VIX regime sizing reduction
        _vix_mult = 1.0
        if (self._vix_level is not None
                and self._vix_level > self._settings.scoring.vix_reduce_threshold):
            _vix_mult = self._settings.scoring.vix_position_scale
            position_pct = position_pct * _vix_mult
            logger.info(
                "%s D101 VIX SIZING: VIX=%.1f > %.0f — position ×%.1f",
                candidate.ticker, self._vix_level,
                self._settings.scoring.vix_reduce_threshold, _vix_mult,
            )

        # D211: Extra reduction for MOMENTUM tier in high VIX (paper trading data collection)
        # Mon 2026-04-20 Bug #2 fix: read from method parameter instead of caller's
        # local scope (Python does not propagate locals into sibling methods).
        if d211_local_vix_reduction < 1.0:
            position_pct = position_pct * d211_local_vix_reduction
            logger.info(
                "%s D211 MOMENTUM VIX SIZING: extra ×%.2f — position now %.4f",
                candidate.ticker, d211_local_vix_reduction, position_pct,
            )

        # ── D106 §2D: PROMOTIONAL_EARLY parameter modifications ──
        # Half position, tighter stop (1.5x ATR instead of 2.0x), aggressive
        # targets (+3/+6/+10%), hard 10:30 AM exit deadline.
        # Minimum position floor: if notional < $500, skip trade.
        _manipulation_mod = ""
        if manipulation_phase == "PROMOTIONAL_EARLY":
            _pre_manip_pct = position_pct
            position_pct *= 0.5  # Half position (0.5% risk instead of 1%)

            # Tighter stop: use 1.5x ATR instead of standard 2.0x
            _tight_atr = self._atr_cache.get(candidate.ticker)
            if _tight_atr and _tight_atr > 0:
                _tight_stop_dist = max(
                    1.5 * _tight_atr,
                    entry * self._settings.execution.initial_stop_floor_pct,
                )
                stop = entry - _tight_stop_dist

            # Aggressive targets: +3/+6/+10% instead of +5/+10/+20%
            targets = [
                round(entry * 1.03, 4),
                round(entry * 1.06, 4),
                round(entry * 1.10, 4),
            ]

            _manipulation_mod = (
                f" | D106_PROMO_EARLY: pos {_pre_manip_pct*100:.1f}%→{position_pct*100:.1f}% "
                f"stop=${stop:.2f} targets=[+3/+6/+10%]"
            )
            logger.info(
                "%s D106 PROMOTIONAL_EARLY mods: half position (%.1f%%→%.1f%%), "
                "tight stop=$%.2f, aggressive targets=[+3/+6/+10%%], 10:30 AM hard exit",
                candidate.ticker, _pre_manip_pct * 100, position_pct * 100, stop,
            )

        # ── D118: Catalyst profile parameter modifications ──
        # When profiler is active AND profile is available, apply:
        # 1) ATR multiplier → widen/tighten stop
        # 2) Risk scale → adjust position sizing
        if (catalyst_profile is not None
                and self._settings.exit_intelligence.catalyst_profiler_active):
            # 1) ATR multiplier: re-scale stop distance from default 2.0x
            _default_atr_mult = 2.0
            _profile_atr_mult = catalyst_profile.recommended_atr_multiplier
            if _profile_atr_mult != _default_atr_mult and entry > 0 and stop > 0:
                _stop_dist = entry - stop
                _rescaled_dist = _stop_dist * (_profile_atr_mult / _default_atr_mult)
                # Apply floor: never tighter than initial_stop_floor_pct
                _rescaled_dist = max(
                    _rescaled_dist,
                    entry * self._settings.execution.initial_stop_floor_pct,
                )
                stop = entry - _rescaled_dist
                logger.info(
                    "%s D118 STOP ADJUST: ATR mult %.1fx→%.1fx, stop $%.2f",
                    candidate.ticker, _default_atr_mult, _profile_atr_mult, stop,
                )

            # 2) Risk scale: multiply position_pct
            _risk_scale = catalyst_profile.recommended_risk_scale
            if _risk_scale != 1.0:
                _pre_scale_pct = position_pct
                position_pct *= _risk_scale
                logger.info(
                    "%s D118 RISK SCALE: %.1fx → pos %.1f%%→%.1f%%",
                    candidate.ticker, _risk_scale,
                    _pre_scale_pct * 100, position_pct * 100,
                )

        # D96: Log complete sizing rationale for EOD audit
        # D115: Use Kelly tier's max_position_pct for capping when active
        _effective_max_pct = max_pct  # Already set from kelly_result or config above
        _final_pct = min(position_pct, _effective_max_pct)
        _was_capped = position_pct > _effective_max_pct

        # D106 §2D: Minimum position floor for PROMOTIONAL_EARLY — if notional
        # is below $500, skip trade (not worth managing 3-tranche exit on tiny position)
        if manipulation_phase == "PROMOTIONAL_EARLY" and action == "BUY":
            # D211: Use actual equity when available, fall back to $100k
            _floor_equity = 100_000.0
            if self._position_manager is not None:
                _floor_equity = getattr(
                    self._position_manager, "_starting_equity", 100_000.0
                ) or 100_000.0
            _est_notional = _final_pct * _floor_equity
            if _est_notional < 500:
                logger.info(
                    "%s D106 MIN POSITION FLOOR: PROMOTIONAL_EARLY notional $%.0f < $500 "
                    "— skipping trade (not worth managing small promotional position)",
                    candidate.ticker, _est_notional,
                )
                return self._build_no_trade_verdict(
                    candidate, scored, debate,
                    reason=f"D106 min position floor: PROMOTIONAL_EARLY notional "
                    f"${_est_notional:.0f} < $500",
                )

        if debate and debate.confidence > 0:
            _sizing_path = "DEBATE"
            _sizing_detail = (
                f"div={debate.debate_divergence:.2f} conf={debate.confidence:.2f} "
                f"scale={max(min(debate.debate_divergence, 1.0), 0.3) * min(debate.confidence, 1.0):.3f}"
            )
        elif not debate and scored.mfcs > 0:
            _sizing_path = "NO_DEBATE"
            _sizing_detail = f"mfcs={scored.mfcs:.3f} scale={min(1.0, scored.mfcs / 0.5):.3f}"
        else:
            _sizing_path = "BASE"
            _sizing_detail = f"pos_size={pos_size}"
        logger.info(
            "D96 SIZING %s: path=%s %s | base=%.1f%% ×float=%.1fx → %.1f%%%s → final=%.1f%%"
            " | float=%s stop=$%.2f%s",
            candidate.ticker, _sizing_path, _sizing_detail,
            _pre_float_pct * 100, float_mult, position_pct * 100,
            " [CAPPED]" if _was_capped else "",
            _final_pct * 100,
            f"{float_shares / 1e6:.1f}M" if float_shares else "unknown",
            stop,
            _manipulation_mod,
        )

        # D115: Determine kelly tier and risk for this verdict
        _kelly_tier_val = kelly_result.tier if kelly_result else 1
        _risk_pct = (
            kelly_result.risk_per_trade_pct
            if kelly_result and self._settings.kelly_tier.enabled
            else self._settings.execution.risk_per_trade_pct
        )
        _kelly_suffix = (
            f" | D115:Tier{int(_kelly_tier_val)} risk={_risk_pct*100:.1f}%"
            if kelly_result and int(_kelly_tier_val) > 1
            else ""
        )

        # Bug AO (Tier 4 #15, 2026-04-27): emit DecisionRow for the
        # decision-replay corpus on the BUY/HOLD finalization path.
        # Together with the _build_no_trade_verdict emit, every
        # orchestrator verdict ends up in the captured corpus.
        _bug_ao_reasoning = (
            f"MFCS={scored.mfcs:.3f} | "
            f"Debate={'YES' if debate else 'NO'} | "
            f"Risk={'PASS' if not risk_signal or risk_signal.risk_verdict != 'VETO' else 'VETO'}"
            f"{_kelly_suffix}"
        )
        self._emit_decision_row(
            candidate=candidate,
            scored=scored,
            verdict_action=action,
            verdict_reason=_bug_ao_reasoning,
            verdict_confidence=float(confidence or 0.0),
        )

        return TradeVerdict(
            ticker=candidate.ticker,
            action=action,
            confidence=confidence,
            mfcs=scored.mfcs,
            debate_result=debate,
            risk_signal=risk_signal,
            entry_price=entry,
            stop_loss=stop,
            target_prices=targets,
            position_size_pct=min(position_pct, _effective_max_pct),
            kelly_tier=int(_kelly_tier_val),
            risk_per_trade_pct=_risk_pct,
            catalyst_profile=catalyst_profile,
            # D150: Pass enrichment data for tier-based aggressive sizing
            float_shares=candidate.float_shares,
            gap_pct=candidate.gap_pct,
            rvol=candidate.rvol,
            time_horizon="INTRADAY",
            reasoning_summary=(
                f"MFCS={scored.mfcs:.3f} | "
                f"Debate={'YES' if debate else 'NO'} | "
                f"Risk={'PASS' if not risk_signal or risk_signal.risk_verdict != 'VETO' else 'VETO'}"
                f"{' | D106:PROMO_EARLY' if manipulation_phase == 'PROMOTIONAL_EARLY' else ''}"
                f"{_kelly_suffix}"
            ),
        )

    @staticmethod
    def _filter_stale_news(news_items: list) -> list:
        """D122: Two-layer staleness filter before agent dispatch.

        Layer 1: Publication time < previous 4PM ET (mirrors D117 scanner filter).
        Layer 2: Recap headline — past-tense price verbs suggest article is
                 about a prior session's move, not a fresh catalyst.

        This is the second defense after news_client._filter_stale_session_news().
        The MOBX Day 4 loss ($-9.4%) was caused by stale "Anti-Drone" news from
        the prior session being treated as a fresh catalyst.
        """
        import re
        from zoneinfo import ZoneInfo

        if not news_items:
            return news_items

        et = ZoneInfo("America/New_York")
        now_et = datetime.now(et)
        # Session boundary: previous trading day 4PM ET
        if now_et.hour >= 16:
            boundary = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        else:
            boundary = (now_et - timedelta(days=1)).replace(
                hour=16, minute=0, second=0, microsecond=0,
            )
            # Skip weekends
            while boundary.weekday() >= 5:
                boundary -= timedelta(days=1)
        boundary_utc = boundary.astimezone(timezone.utc)

        # Recap headline patterns: past-tense price verbs, "Why X soared"
        recap_re = re.compile(
            r"\b(soared|surged|jumped|plunged|crashed|tumbled|spiked|rallied"
            r"|why\s+\w+\s+(rose|fell|dropped|gained|lost)"
            r"|here'?s why|what happened)\b",
            re.IGNORECASE,
        )

        filtered = []
        removed = 0
        for item in news_items:
            # Layer 1: publication time
            pub = getattr(item, "published_at", None)
            if pub is not None:
                pub_utc = pub if pub.tzinfo else pub.replace(tzinfo=timezone.utc)
                if pub_utc < boundary_utc:
                    logger.warning(
                        "D122 stale news (pub time): %.80s [%s]",
                        getattr(item, "headline", "?"), pub,
                    )
                    removed += 1
                    continue

            # Layer 2: recap headline content
            headline = getattr(item, "headline", "")
            if recap_re.search(headline):
                logger.warning(
                    "D122 stale news (recap headline): %.80s", headline,
                )
                removed += 1
                continue

            filtered.append(item)

        if removed:
            logger.info("D122: filtered %d/%d stale news items", removed, len(news_items))
        return filtered

    def _emit_decision_row(
        self,
        *,
        candidate: CandidateStock,
        scored: ScoredCandidate | None,
        verdict_action: str,
        verdict_reason: str,
        verdict_confidence: float,
    ) -> None:
        """Bug AO (Tier 4 #15, 2026-04-27): emit a DecisionRow on every
        orchestrator verdict finalization.

        Enables BACKWARD-LOOKING decision replay against captured corpus
        instead of needing to parse session logs. The forward capture is
        ~5× richer than what log parsing can extract (full agent_signals
        breakdown with confidences, MFCS components, ticker metadata).

        Wrapped in try/except — never crashes the trading hot path. If
        the writer isn't wired (None), this is a no-op.

        D124 state is computed on-demand from `scored.agent_signals` so
        no upstream wiring change is needed (mirror of orchestrator's
        own D124 logic at lines ~1057-1135). The d124_was_rejected flag
        is parsed from `verdict_reason` for NO_TRADE verdicts.
        """
        if not self._instrumentation:
            return
        try:
            import uuid
            from src.analysis.instrumentation.schemas import DecisionRow

            now = datetime.now(timezone.utc)
            agent_signals = list(scored.agent_signals) if scored else []

            # Mirror the D124 consensus logic (orchestrator.py:1029-1086) so
            # the captured DecisionRow's d124 fields match what production
            # actually computed. Only NON-RISK, NON-NEUTRAL, error-free
            # signals participate in the count.
            non_risk = [
                s for s in agent_signals
                if not isinstance(s, RiskSignal)
                and s.signal != "NEUTRAL"
                and "AGENT_ERROR" not in (s.flags or [])
                and "D94_NO_DATA_SKIP" not in (s.flags or [])
            ]
            bullish = [s for s in non_risk if s.signal in ("BULL", "STRONG_BULL")]
            bearish = [s for s in non_risk if s.signal in ("BEAR", "STRONG_BEAR")]
            bullish_conf = float(sum(s.confidence for s in bullish)) if bullish else 0.0
            bearish_conf = float(sum(s.confidence for s in bearish)) if bearish else 0.0
            max_bearish_conf = float(max((s.confidence for s in bearish), default=0.0))

            # Detect D124 rejection from the reason string
            d124_was_rejected = bool(
                verdict_action == "NO_TRADE"
                and "D124" in verdict_reason
                and "consensus alignment" in verdict_reason.lower()
            )
            d124_rejection_tier = None
            if d124_was_rejected and "tier=" in verdict_reason:
                try:
                    d124_rejection_tier = (
                        verdict_reason.split("tier=", 1)[1].split(")", 1)[0].strip()
                    )
                except (IndexError, ValueError):
                    d124_rejection_tier = "unknown"

            mfcs_static = float(self._settings.scoring.mfcs_buy_threshold)
            mfcs_score = float(scored.mfcs) if scored else 0.0
            row = DecisionRow(
                decision_id=str(uuid.uuid4()),
                timestamp=now,
                session_date=now.strftime("%Y-%m-%d"),
                cycle_number=self._evaluation_cycle_count,
                ticker=candidate.ticker,
                candidate_gap_pct=float(candidate.gap_pct or 0.0),
                candidate_rvol=float(candidate.rvol or 0.0),
                candidate_current_price=float(candidate.current_price or 0.0),
                candidate_float_shares=candidate.float_shares,
                candidate_market_cap=candidate.market_cap,
                candidate_gap_classification=str(candidate.gap_classification or ""),
                candidate_has_news_catalyst=bool(candidate.has_news_catalyst),
                agent_signals=[
                    {
                        "agent_id": s.agent_id,
                        "signal": s.signal,
                        "confidence": float(s.confidence),
                        "weight": float(getattr(s, "weight", 0.0) or 0.0),
                        "flags": list(s.flags or []),
                    }
                    for s in agent_signals
                ],
                n_agents_total=len(agent_signals),
                n_agents_returned=sum(
                    1 for s in agent_signals
                    if s.confidence > 0 or (s.reasoning and s.reasoning.strip())
                ),
                n_agents_failed=sum(
                    1 for s in agent_signals
                    if "AGENT_ERROR" in (s.flags or [])
                    or "D91_BOTH_FAILED" in (s.flags or [])
                    or "D92_ALL_FAILED" in (s.flags or [])
                ),
                mfcs_score=mfcs_score,
                mfcs_components=dict(scored.component_scores or {}) if scored else {},
                d124_bullish_count=len(bullish),
                d124_bearish_count=len(bearish),
                d124_bullish_conf=bullish_conf,
                d124_bearish_conf=bearish_conf,
                d124_max_bearish_conf=max_bearish_conf,
                d124_was_rejected=d124_was_rejected,
                d124_rejection_tier=d124_rejection_tier,
                mfcs_threshold_static=mfcs_static,
                mfcs_threshold_effective=mfcs_static,  # D216 dynamic reduction TBD in follow-up
                mfcs_threshold_passed=mfcs_score >= mfcs_static,
                verdict_action=verdict_action if verdict_action in (
                    "BUY", "STRONG_BUY", "HOLD", "NO_TRADE",
                ) else "NO_TRADE",
                verdict_reason=verdict_reason[:500] if verdict_reason else "",
                verdict_confidence=float(verdict_confidence or 0.0),
            )
            self._instrumentation.emit_decision_row(row)
        except Exception as _bug_ao_e:  # noqa: silent-handler — never crash hot path
            logger.debug(
                "Bug AO emit_decision_row failed (non-fatal) for %s: %s",
                candidate.ticker, _bug_ao_e,
            )

    def _build_no_trade_verdict(
        self,
        candidate: CandidateStock,
        scored: ScoredCandidate | None,
        debate: DebateResult | None,
        reason: str,
    ) -> TradeVerdict:
        """Build a NO_TRADE verdict with reason.

        D217 FIX: Also logs feature vector here so rejected candidates
        get recorded. Previously, feature logging only happened after ALL
        gates passed (line 1465), meaning ~90% of evaluations were never
        logged. Now every evaluation produces a feature row.
        """
        # Log feature vector for rejected candidate
        if self._feature_logger:
            try:
                from zoneinfo import ZoneInfo
                _now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
                self._feature_logger.log_evaluation(
                    trade_id=getattr(self, "_current_trade_id", candidate.ticker),
                    ticker=candidate.ticker,
                    gap_pct=candidate.gap_pct,
                    rvol=candidate.rvol,
                    current_price=candidate.current_price,
                    previous_close=candidate.previous_close,
                    premarket_volume=candidate.premarket_volume,
                    float_shares=candidate.float_shares,
                    market_cap=candidate.market_cap,
                    has_news_catalyst=candidate.has_news_catalyst,
                    rvol_exhaustion=getattr(candidate, "rvol_exhaustion", False),
                    agent_signals=[
                        {"agent": s.agent_id, "signal": s.signal, "confidence": s.confidence}
                        for s in (scored.agent_signals if scored else [])
                    ],
                    mfcs=scored.mfcs if scored else 0.0,
                    component_scores=scored.component_scores if scored else {},
                    risk_score=scored.risk_score if scored else 0.0,
                    qualifies_for_debate=scored.qualifies_for_debate if scored else False,
                    indicators={},
                    debate_verdict=debate.verdict if debate else None,
                    debate_confidence=debate.confidence if debate else None,
                    debate_divergence=debate.debate_divergence if debate else None,
                    debate_skipped=False,
                    hour_et=_now_et.hour,
                    minute_et=_now_et.minute,
                    day_of_week=_now_et.weekday(),
                    final_action="NO_TRADE",
                    position_size_pct=0.0,
                )
            except Exception as _feature_log_e:
                logger.warning(
                    "feature logger failed for %s NO_TRADE (non-fatal): %s",
                    candidate.ticker, _feature_log_e,
                )

        # D220 Phase 5: composite shadow telemetry. Write-only side channel.
        # NEVER raises into production — composite_shadow handles its own errors.
        try:
            from src.shadow.composite_shadow import maybe_score_composite
            from zoneinfo import ZoneInfo as _ZI
            _now_et_for_shadow = datetime.now(timezone.utc).astimezone(_ZI("America/New_York"))
            maybe_score_composite(
                candidate_features={
                    "gap_pct": candidate.gap_pct,
                    "premarket_volume": candidate.premarket_volume,
                    "dollar_volume": getattr(candidate, "dollar_volume", 0)
                                     or (candidate.premarket_volume * candidate.current_price),
                    "price": candidate.current_price,
                    "pre_market_high": getattr(candidate, "pre_market_high", 0),
                    "orb_range_pct": 0.0,
                    "day_volume": 0,
                    # D221 Phase F: pass agent_signals so the DISAGREE_SHADOW_BUYS
                    # message in decision_learning.py can show per-agent verdicts.
                    # Underscore-prefixed key to signal "internal use, not a feature."
                    "_agent_signals_for_dl": list(scored.agent_signals) if scored else [],
                },
                ticker=candidate.ticker,
                session_date=_now_et_for_shadow.strftime("%Y-%m-%d"),
                production_decision="NO_TRADE",
                production_gate_rejected=reason,
                production_mfcs=scored.mfcs if scored else None,
                # D221 Phase F: explicit None until docs/engineering_hygiene/
                # sec_degradation_fix.md ships the producer-side wiring.
                sec_data_available=None,
            )
        except Exception as _shadow_e:
            logger.warning(
                "composite shadow failed for %s NO_TRADE (non-fatal): %s",
                candidate.ticker, _shadow_e,
            )

        # Bug AO (Tier 4 #15, 2026-04-27): emit DecisionRow for the
        # decision-replay corpus before returning the NO_TRADE verdict.
        # Captures the full input + verdict reason so future calibration
        # changes can be replayed quantitatively.
        self._emit_decision_row(
            candidate=candidate,
            scored=scored,
            verdict_action="NO_TRADE",
            verdict_reason=reason,
            verdict_confidence=0.0,
        )

        return TradeVerdict(
            ticker=candidate.ticker,
            action="NO_TRADE",
            confidence=0.0,
            mfcs=scored.mfcs if scored else 0.0,
            debate_result=debate,
            entry_price=candidate.current_price,
            # D121 BUG: Was entry_price == stop_loss, creating division-by-zero
            # trap if NO_TRADE verdict ever leaks to position sizing code.
            stop_loss=candidate.current_price * 0.95,
            target_prices=[],
            position_size_pct=0.0,
            # D150: Enrichment data for consistency (even on NO_TRADE)
            float_shares=candidate.float_shares,
            gap_pct=candidate.gap_pct,
            rvol=candidate.rvol,
            reasoning_summary=reason,
        )

    def build_enriched_trade_result(
        self,
        scored: ScoredCandidate,
        agent_signals_map: dict[str, str],
        variant_map: dict[str, str],
        exit_price: float,
        exit_time: datetime,
    ) -> Any:
        """
        Construct an EnrichedTradeResult from pipeline data for Shapley attribution.

        Called post-trade when position is closed. Captures all the data
        needed by ShapleyAttributor.compute_attributions():
          - agent_component_scores from MFCS computation
          - mfcs_at_entry (the composite score at time of entry)
          - agent_variants and agent_signals for Elo feedback
          - debate_triggered flag

        Args:
            scored: ScoredCandidate from MFCS computation at entry time.
            agent_signals_map: Map of agent_id → signal direction string.
            variant_map: Map of agent_id → variant_id from arena selection.
            exit_price: Final exit fill price.
            exit_time: When position was closed.

        Returns:
            EnrichedTradeResult ready for ShapleyAttributor.

        Ref: ADR-013 (D1: Shapley → PostTradeAnalyzer)
        Ref: MOMENTUM_LOGIC.md §17 (Shapley Attribution)
        """
        from src.analysis.shapley import EnrichedTradeResult

        return EnrichedTradeResult(
            ticker=scored.candidate.ticker,
            entry_price=scored.candidate.current_price,
            exit_price=exit_price,
            entry_time=scored.candidate.scan_timestamp,
            exit_time=exit_time,
            agent_variants=variant_map,
            agent_signals=agent_signals_map,
            agent_component_scores=dict(scored.component_scores),
            mfcs_at_entry=scored.mfcs,
            risk_score=scored.risk_score,
            debate_triggered=scored.qualifies_for_debate,
        )
