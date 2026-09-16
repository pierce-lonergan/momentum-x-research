"""
MOMENTUM-X Configuration

### ARCHITECTURAL CONTEXT
Type-safe configuration using pydantic-settings. All secrets load from
environment variables or .env file. Thresholds are derived from
docs/mathematics/MOMENTUM_LOGIC.md.

### DESIGN DECISIONS
- pydantic-settings over raw os.environ for validation at startup (ADR-005)
- Nested models for logical grouping (broker, models, thresholds)
- All threshold defaults match the LaTeX definitions in MOMENTUM_LOGIC.md
- .env file auto-loaded for developer convenience (ADR-005 §2)
- SIP feed enforced as default (ADR-004 §1, CONSTRAINT-001)
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Resolve .env relative to project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class AlpacaConfig(BaseSettings):
    """Alpaca broker configuration. Ref: DATA-001, DATA-001-EXT."""

    api_key: str = Field(default="", description="Alpaca API key ID")
    secret_key: str = Field(default="", description="Alpaca API secret key")
    base_url: str = Field(
        default="https://paper-api.alpaca.markets",
        description="Paper trading by default. NEVER default to live.",
    )
    data_url: str = Field(
        default="https://data.alpaca.markets",
        description="Market data endpoint (shared across paper/live)",
    )
    feed: str = Field(
        default="sip",
        description="Data feed: 'sip' (required for production per ADR-004) or 'iex' (testing only)",
    )
    # D130: Optional WebSocket URL overrides for mx-arena simulator
    ws_data_url: str = Field(
        default="",
        description="D130: Override market data WebSocket URL (e.g. ws://localhost:8082). "
        "Empty = use default Alpaca URL.",
    )
    ws_trade_url: str = Field(
        default="",
        description="D130: Override trade updates WebSocket URL (e.g. ws://localhost:8081). "
        "Empty = use default Alpaca URL.",
    )

    model_config = SettingsConfigDict(env_prefix="ALPACA_", env_file=str(_ENV_FILE), extra="ignore")


class ModelConfig(BaseSettings):
    """LLM model configuration. Ref: ADR-001 Model Tiering."""

    # D190: Frontier model experiment — 6-model head-to-head on 18 production scenarios.
    # All frontier models achieved identical 72.2% direction accuracy on the LLM Arena
    # dataset (13/18 correct, all predicting NEUTRAL, FP=0, FN=1).
    # The dataset is 72% NEUTRAL by ground truth — models correctly classify conservatively.
    #
    # Key findings (Experiment A, 2026-04-03):
    #   DeepSeek-R1:   72.2% acc, 17.9s, $0.01531/signal — 0% accuracy advantage vs R1 vs V3
    #   DeepSeek-V3.1: 72.2% acc,  4.8s, $0.00059/signal — WINNER on value
    #   Llama-3.3-70B: 72.2% acc,  2.7s, $0.00122/signal — solid dense transformer
    #   Qwen2.5-7B:    72.2% acc,  1.5s, $0.00041/signal — fastest + cheapest screener
    #   Qwen3-235B:    72.2% acc,  5.9s, $0.00131/signal — current production baseline
    #   Mixtral-8x7B:  60.0% acc,  3.4s, $0.00056/signal — only 10/18 parsed (DEMOTED)
    #
    # Conclusion: DeepSeek-R1 thinking mode provides zero accuracy gain over V3 but costs
    # 26x more and runs 3.7x slower. DeepSeek-V3 is the value winner — same accuracy
    # as every other frontier model at 26x lower cost than R1.
    #
    # Mixtral is DEMOTED: 55.6% parse rate on the production prompt suggests its
    # instruction-following has degraded or Together AI's serverless tier is unstable.
    # Replace with DeepSeek-V3 as primary.
    #
    # Two-pass pipeline (Experiment C): qwen2.5-7b binary screener → deepseek-r1 deep analysis.
    # On this dataset: all scenarios classify as no-catalyst → R1 never fires.
    # Validates that qwen2.5-7b alone provides fast, cheap, accurate screening.

    # Tier 1: Reasoning (Debate Judge, News, Manipulation agents)
    # D190: DeepSeek-V3.1 replaces Mixtral-8x7B as default.
    # Frontier experiment: same accuracy (72.2%), 100% parse rate vs Mixtral's 55.6%,
    # $0.40/M input vs Mixtral $0.60/M — 33% cheaper AND more reliable.
    tier1_model: str = Field(
        default="Qwen/Qwen3.5-397B-A17B",
        description="doc 267: default aligned to the env-pinned LIVE model. Prior default "
        "(deepseek-ai/DeepSeek-V3.1) is NO LONGER Together-serverless (400 model_not_available, "
        "verified 2026-06-09) — a dead default = D93-class incident if env fails to load.",
    )
    tier1_provider: str = Field(
        default="together_ai",
        description="LiteLLM provider prefix",
    )

    # Tier 2: Extraction (Fundamental, Institutional, Deep Search agents)
    # D150: Qwen3-Coder-480B: stable #4-5 across runs (30% sig, 1.6-1.9s).
    # D190: Not re-benchmarked for extraction — kept pending dedicated extraction experiment.
    tier2_model: str = Field(
        default="Qwen/Qwen3-235B-A22B-Instruct-2507-tput",
        description="doc 267: default aligned to the env-pinned LIVE model (0.7s p50, 3/3 JSON "
        "in the doc-267 bench). Prior default (Qwen3-Coder-480B) is no longer Together-serverless.",
    )
    tier2_provider: str = Field(
        default="together_ai",
        description="LiteLLM provider prefix",
    )

    # Tier 3: Validation (Final conviction, used sparingly)
    # D190: Same as Tier 1 (DeepSeek-V3.1).
    tier3_model: str = Field(
        default="Qwen/Qwen3.5-397B-A17B",
        description="doc 267: same as Tier 1 (V3.1 default was dead-serverless).",
    )
    tier3_provider: str = Field(
        default="together_ai",
        description="LiteLLM provider prefix",
    )

    # D190: Fallback chain updated.
    # Primary: DeepSeek-V3 -> Fallback: Qwen2.5-7B (fast screener) -> Emergency: Llama-3.3
    tier1_fallback_model: str = Field(
        default="Qwen/Qwen2.5-7B-Instruct-Turbo",
        description="D190: Fallback reasoning — Qwen2.5-7B Turbo. "
        "Frontier experiment: 72.2%% acc, 100%% parse, 1.5s, $0.30/$0.30 per 1M. "
        "Fastest + cheapest serverless model; different family from V3.",
    )
    tier1_fallback_provider: str = Field(
        default="together_ai",
        description="D190: Fallback provider",
    )
    tier2_fallback_model: str = Field(
        default="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        description="doc 267: fallback extraction — Llama 3.3 70B (alive serverless, 1.5s, 3/3 "
        "JSON in bench; dense architecture = diverse from MoE primary). Prior default (DeepSeek-"
        "V3.1) is dead-serverless — a broken MIDDLE link in the fallback chain (primary fail -> "
        "instant 400 -> emergency), verified 2026-06-09.",
    )
    tier2_fallback_provider: str = Field(
        default="together_ai",
        description="D190: Fallback provider",
    )
    # D190: Emergency — Llama-3.3-70B (dense transformer, different architecture from MoE DeepSeek/Qwen).
    emergency_model: str = Field(
        default="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        description="D190: Emergency fallback — Llama 3.3 70B Turbo. "
        "Frontier experiment: 72.2%% acc, 100%% parse, 2.7s. $0.88/$0.88. "
        "Dense transformer architecture — maximally diverse from MoE primary/fallback.",
    )
    emergency_provider: str = Field(
        default="together_ai",
        description="D190: Emergency provider",
    )

    # Inference
    # D204: Lowered from 0.3 to 0.1. D201-E8 found 47% verdict inconsistency at
    # temp=0.3. Ensemble (D202) handles some noise, but lower temperature makes
    # each individual call more deterministic. Combined with 3x ensemble: each call
    # is more stable AND we take majority vote. Risk: may miss edge-case catalysts
    # that require creative LLM interpretation. Acceptable trade-off given 0% WR.
    default_temperature: float = Field(default=0.1)
    max_tokens: int = Field(default=4096)
    timeout_seconds: int = Field(default=120)

    # D31: LiteLLM retry and timeout configuration
    litellm_num_retries: int = Field(
        default=0,
        description="Max LiteLLM retries per call. 0 = single attempt, no retries. "
        "D83: Cut from 1→0. Speed is everything for momentum trading.",
    )
    litellm_timeout_tier1: int = Field(
        default=25,
        description="D92: Timeout for Tier 1 instruct models. "
        "Qwen3.5 397B @ ~135 tok/s: 4096 tokens / 135 = 30s theoretical max. "
        "25s accommodates queue time + first-token latency on Together AI. "
        "Simulation testing showed 15s caused timeouts — raised to 25s. "
        "Still a massive improvement over 45s for Kimi K2 thinking model.",
    )
    litellm_timeout_tier2: int = Field(
        default=25,
        description="D92/doc178: Timeout for Tier 2 instruct models. "
        "Qwen3 235B @ ~165 tok/s. RAISED 15s→25s (doc 177/178): the news_agent "
        "runs on Tier 2 + ensemble×3, and at the 09:30-11:30 entry window Together "
        "AI queue latency pushed it past 15s → 34 timeouts on 5/28 → news_agent "
        "EMPTY in ~67%% of evals → D200/D204 gates blocked on DATA ABSENCE, not "
        "real signal (the 'blind funnel'). 25s matches Tier 1 and sits below the "
        "proven D87 35s. Revert via env LLM_LITELLM_TIMEOUT_TIER2=15 if latency "
        "dominates. Was 35s for Kimi K2.5 thinking model.",
    )

    # D202: Ensemble — multi-call signal averaging for noise reduction.
    # D201-E8: 47% verdict inconsistency. Same stock evaluated twice gets different
    # BUY/NO_TRADE nearly half the time. Ensemble calls each LLM agent N times in
    # parallel and aggregates via majority vote + confidence averaging.
    ensemble_enabled: bool = Field(
        default=True,
        description="D202: Enable ensemble multi-call for LLM agents. When True, each "
        "LLM agent is called N times in parallel and results are aggregated. "
        "Deterministic agents (technical, risk) are NOT ensembled.",
    )
    ensemble_n_calls: int = Field(
        default=3,
        ge=1,
        le=7,
        description="D202: Number of parallel LLM calls per agent per evaluation. "
        "3 calls gives majority vote with tie-breaking. Cost: 3x LLM tokens per "
        "agent. Latency: same as 1 call (all parallel).",
    )
    ensemble_min_quorum: int = Field(
        default=2,
        ge=1,
        description="D202: Minimum successful calls needed for ensemble aggregation. "
        "If fewer succeed, returns the single best result with a flag.",
    )

    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=str(_ENV_FILE), extra="ignore")


class ScannerThresholds(BaseSettings):
    """
    Scanning thresholds derived from MOMENTUM_LOGIC.md §1-§4.
    Every value here maps to a LaTeX definition.
    """

    # RVOL thresholds (MOMENTUM_LOGIC.md §2)
    rvol_premarket_min: float = Field(
        default=3.0,
        description="D219: Raised from 2.0 to 3.0. Professional minimum is 3-5x "
        "(Cameron, SMB Capital). 2x admits stocks 'slightly above average' — "
        "too many false positives. τ_rvol for pre-market. MOMENTUM_LOGIC.md §1",
    )
    rvol_intraday_min: float = Field(
        default=3.0,
        description="τ_rvol for intraday. MOMENTUM_LOGIC.md §1",
    )
    rvol_breakout_min: float = Field(
        default=2.0,
        description="Minimum RVOL for valid breakout. MOMENTUM_LOGIC.md §4",
    )

    # D105: Absolute volume gate as RVOL alternative
    absolute_volume_override: int = Field(
        default=500_000,
        description="D105: If premarket_volume exceeds this threshold, the stock passes "
        "the RVOL gate regardless of relative volume. Catches perpetually active "
        "names (MARA, MSTU) where RVOL stays low because the baseline is already "
        "high. 500K premarket shares = meaningful institutional interest.",
    )

    # Gap thresholds (MOMENTUM_LOGIC.md §3)
    gap_pct_min: float = Field(
        default=0.05,
        description="τ_gap minimum (5%). MOMENTUM_LOGIC.md §1",
    )
    gap_pct_explosive: float = Field(
        default=0.20,
        description="Explosive gap threshold (20%). MOMENTUM_LOGIC.md §3",
    )
    # D205: Maximum gap — extreme gaps (>50%) are promotional traps.
    # Scenario analysis: gap 50%+ had 33% WR and -7% avg return.
    # Gap 5-20% had 74% WR, +4.7%. Gap 20-50% had 64% WR, +12.2%.
    # Above 50% the stock is almost certainly a promotional pump-and-dump.
    gap_pct_max: float = Field(
        default=1.00,
        description="D219: Raised from 0.50 to 1.00. Research shows larger gaps on "
        "genuine catalysts (FDA, earnings) have LOWER fill rates — the 50%% cap was "
        "excluding the highest-expectancy setups. Gaps >100%% remain excluded "
        "(reverse-split artifacts). The D205 scenario analysis was based on "
        "worktree-era data with promotional stocks; catalyst-confirmed 80%% gappers "
        "are statistically safer than catalyst-ambiguous 15%% gappers. "
        "Previous: 0.50 (D205).",
    )

    # ATR ratio (MOMENTUM_LOGIC.md §4)
    atr_ratio_min: float = Field(
        default=1.5,
        description="τ_atr minimum. MOMENTUM_LOGIC.md §1",
    )
    atr_lookback_days: int = Field(
        default=14,
        description="ATR calculation period. MOMENTUM_LOGIC.md §4",
    )

    # Float constraints
    float_max_shares: int = Field(
        default=20_000_000,
        description="Max float for high-volatility focus (20M shares)",
    )
    float_ideal_shares: int = Field(
        default=5_000_000,
        description="D219: Tightened from 10M to 5M. Sub-5M float creates 50-200%%+ "
        "intraday range with catalyst. Cameron uses <10M primary, Sykes <10M. "
        "The scoring system should favor lower floats within the 20M cap. "
        "Previous: 10M.",
    )

    # Volume
    premarket_volume_min_7am: int = Field(
        default=50_000,
        description="Min pre-market volume by 7:00 AM ET",
    )
    premarket_volume_min_9am: int = Field(
        default=100_000,
        description="Min pre-market volume by 9:00 AM ET",
    )

    # Price range
    price_min: float = Field(
        default=2.00,
        description="D219: Raised from $1.50 to $2.00. Below $2 is deep penny stock "
        "territory with 3-5%% bid-ask spreads and severe slippage on exits. "
        "Cameron's operational minimum is $2; sweet spot is $2-$8. "
        "Hard floor remains $0.50 (price_min_high_volume) for extreme overrides. "
        "Previous: $1.50 (D160).",
    )
    price_max: float = Field(
        default=50.00,
        description="D105: Max price filter. Raised from $20 to $50 — BMNR at $20.55 was "
        "blocked. Small/mid-cap momentum stocks can gap into $20-$50 range. "
        "Above $50 = typically large-cap, wrong universe.",
    )

    # D105: Tiered price floor — conditional sub-$3 admission
    price_min_high_volume: float = Field(
        default=0.50,
        description="D105: Absolute price floor for high-volume override. Sub-$0.50 stocks "
        "are excluded unconditionally — too susceptible to manipulation and halts.",
    )
    price_override_dollar_vol: float = Field(
        default=2_000_000,
        description="D116: Lowered from $10M to $2M for sub-$3 price override. "
        "LNAI had $3.6M dolvol with 63.6x RVOL and ran +189% — was blocked "
        "at $10M. $2M still ensures meaningful liquidity while capturing "
        "explosive sub-$3 movers. BIAF had $380M, AIFF had $26M.",
    )
    price_override_rvol: float = Field(
        default=5.0,
        description="D105: Minimum RVOL to qualify for sub-$3 price override. "
        "5x relative volume confirms extreme interest, not just a thinly-traded "
        "penny stock. BIAF had 450x, AIFF had 23x.",
    )
    extreme_rvol_override: float = Field(
        default=30.0,
        description="D116: RVOL above this bypasses dollar volume check for sub-$3 "
        "price override. 30x+ RVOL indicates genuine market attention regardless "
        "of absolute dollar volume. LNAI had 63.6x RVOL and ran +189% but was "
        "blocked by the $10M dollar volume threshold.",
    )

    # D160: Mega dollar volume override — bypasses ALL price floor checks.
    # ITRM had $54.6M dolvol at $0.07 but was blocked because price_min_high_volume ($0.50)
    # is still applied in the high-vol path. Stocks with $10M+ in daily dolvol are
    # demonstrably liquid regardless of share price — the dollar volume IS the liquidity proof.
    # Fixes the ITRM / PMNT pattern: high-dolvol sub-penny blocked purely on price.
    price_override_mega_dollar_vol: float = Field(
        default=10_000_000,
        description="D160: Dollar volume above which ALL price floor checks are bypassed. "
        "At $10M+ dolvol the stock is liquid enough to trade regardless of share price. "
        "Fixes ITRM ($54.6M dolvol at $0.07) being blocked by the $0.50 price floor. "
        "This override fires BEFORE price checks in the filter logic.",
    )

    # D160: High-RVOL mandatory re-evaluation thresholds.
    # BFRG had 829.7x RVOL but only received 1 evaluation before NO_TRADE killed it.
    # A single LLM pass on a 829x RVOL stock is dangerous — LLMs can mis-classify
    # genuine extreme momentum as promotional noise on sparse data.
    # Stocks above this RVOL threshold must be evaluated at least min_evals times
    # before the NO_TRADE verdict sticks.
    high_rvol_reeval_threshold: float = Field(
        default=100.0,
        description="D160: RVOL above which stocks require mandatory re-evaluation "
        "before rejection. 100x+ RVOL is statistically extreme — a single agent "
        "pass that returns NO_TRADE should not be the final word. BFRG (829x) ran "
        "+86%% in 3 minutes after the system passed on it.",
    )
    high_rvol_min_evals: int = Field(
        default=3,
        description="D160: Minimum evaluation cycles before a high-RVOL stock can be "
        "rejected as NO_TRADE. Set to 3 — enough to catch LLM sampling variance "
        "without excessive latency. If PROMOTIONAL_LATE, still hard-blocked on first eval.",
    )

    # Dollar volume
    min_dollar_volume: float = Field(
        default=2_000_000,
        description="D160: Lowered from $5M to $2M — Selection Arena found dolvol_min "
        "was responsible for 100%% of false negatives on Mar 30. ITRM ($54.6M), "
        "PMNT, SLND, GLND all had $2-4M dolvol and ran 37-98%%. $2M still ensures "
        "meaningful liquidity while doubling capture rate from 33%% to 78%% "
        "with zero precision loss. Original D101 rationale was $5M for position "
        "exit without market impact — valid at $5M sizing, still fine at $2M "
        "with our bar-1 exit (out in 60s, no multi-million exit problem).",
    )

    # Sweep fix: Validators prevent misconfiguration that silently bypasses safety gates.
    @field_validator("extreme_rvol_override")
    @classmethod
    def _validate_extreme_rvol(cls, v: float) -> float:
        if v < 10.0:
            raise ValueError(f"extreme_rvol_override={v} too low (min 10.0) — would bypass dollar vol filter for low-RVOL stocks")
        return v

    @field_validator("price_override_rvol")
    @classmethod
    def _validate_price_override_rvol(cls, v: float) -> float:
        if v < 2.0:
            raise ValueError(f"price_override_rvol={v} too low (min 2.0) — would allow sub-$3 stocks with no volume confirmation")
        return v

    @field_validator("price_override_dollar_vol")
    @classmethod
    def _validate_price_override_dolvol(cls, v: float) -> float:
        if v < 500_000:
            raise ValueError(f"price_override_dollar_vol={v} too low (min $500K) — would allow illiquid penny stocks")
        return v

    model_config = SettingsConfigDict(env_prefix="SCAN_", env_file=str(_ENV_FILE), extra="ignore")


class ScoringWeights(BaseSettings):
    """
    Multi-Factor Composite Score weights. MOMENTUM_LOGIC.md §5.
    Must sum to 1.0.
    """

    # D203: Technical agent BULL has 0%% win rate on 115 BUY trades — it's an
    # anti-signal. All 3 winning trades had technical=NEUTRAL. Technical BULL
    # inflates MFCS on promotional gap-ups that look "technically bullish"
    # (RSI high, MACD positive by definition on momentum stocks).
    # News agent BULL has 16.7%% WR — the ONLY positive predictor.
    # Weight almost entirely on news + deterministic RVOL/float signals.
    catalyst_news: float = Field(default=0.55)
    technical: float = Field(default=0.05)
    volume_rvol: float = Field(default=0.25)
    float_structure: float = Field(default=0.15)
    institutional: float = Field(default=0.00)
    deep_search: float = Field(default=0.00)
    risk_aversion_lambda: float = Field(
        default=0.25,
        description="λ risk penalty. MOMENTUM_LOGIC.md §5. "
        "D168: Raised from 0.15 to 0.25. LLM Arena (509 scenarios) confirmed risk_agent "
        "is the best performing agent: F1=0.667, Precision=0.500, Recall=1.0, lowest "
        "calibration error (CE=0.541). Production target remains 0.3 once further validated.",
    )
    risk_veto_mode: str = Field(
        default="ADVISORY",
        description="HARD = risk VETO immediately kills trade (production). "
        "ADVISORY = risk VETO logged but not enforced; risk_score still penalizes "
        "MFCS via lambda. Use ADVISORY during Phase 3 backtesting where risk agent "
        "hallucinates veto conditions from sparse data.",
    )
    mfcs_buy_threshold: float = Field(
        default=0.25,
        description="D101: Minimum MFCS for a BUY verdict. Reduced from 0.30 — "
        "with bipolar scoring (BEAR=-0.5, STRONG_BEAR=-1.0), the effective "
        "scoring range shifted. 0.25 still requires genuine bullish consensus "
        "but accounts for the narrower positive range with bipolar scale.",
    )
    confidence_deflation_factor: float = Field(
        default=0.65,
        description="D106: Multiplier applied to raw LLM confidence scores only. "
        "KalshiBench found LLMs overconfident (ECE 0.12-0.40). "
        "D168: Lowered from 0.70 to 0.65. LLM Arena (509 scenarios) found news_agent "
        "73.9% overconfident and technical_agent 87.2% overconfident — both LLM agents "
        "are more miscalibrated than assumed. 0.65x still allows high-conviction signals "
        "through (0.90 confidence -> 0.585 deflated) while penalizing overconfidence more. "
        "NOTE: Only affects LLM agents (NewsAgent, FundamentalAgent, etc.) — "
        "deterministic agents (DeterministicRiskAgent, DeterministicTechnicalAgent) "
        "are duck-typed and bypass BaseAgent.analyze() entirely. "
        "Applied in BaseAgent.analyze() after parsing response.",
    )
    vix_block_threshold: float = Field(
        default=30.0,
        description="D219: Raised from 20.0 to 30.0. Research consensus: VIX>20 creates "
        "MORE momentum opportunity, not less. Cameron's 3rd-best year was 2022 (high VIX). "
        "Professional approach: reduce position size in high vol, don't stop trading. "
        "The D205 analysis (n=2 at VIX 20-25) was statistically unreliable. "
        "Academic evidence (Abreu & Brunnermeier): high VIX = STRONGER momentum in "
        "small/volatile stocks due to delayed arbitrage. Now: VIX 15-30 = reduced sizing, "
        "VIX 30+ = hard block. Previous: 20.0 (D205).",
    )
    vix_panic_threshold: float = Field(
        default=35.0,
        description="D211: VIX level above which ALL entries are blocked, including CATALYST tier. "
        "True market panic — institutional flow breaks down, even mid-cap catalysts fail. "
        "Tariff environment Apr 2026: VIX 25-35 is elevated but not panic; 35+ is full selloff mode.",
    )
    vix_reduce_threshold: float = Field(
        default=15.0,
        description="D205: VIX level above which position sizing is halved. "
        "Scenario analysis: VIX 12-15 had 70%% WR, +7.8%% avg. VIX 15-20 had "
        "75%% WR, +6.1%% avg — still positive EV but slightly worse. Half-size "
        "in this zone captures the trades while managing the elevated risk.",
    )
    vix_position_scale: float = Field(
        default=0.5,
        description="D101 §2.4: Position size multiplier when VIX is between "
        "reduce_threshold and block_threshold.",
    )
    vix_shock_threshold_pct: float = Field(
        default=15.0,
        description="D104: VIX delta threshold (%). If VIXY daily change exceeds "
        "this percentage, block all entries — indicates sudden regime shock. "
        "March 12 VIX spiked +35% in a day; absolute level was 27 (only 'caution') "
        "but the delta was extreme.",
    )
    spy_halt_threshold_pct: float = Field(
        default=-2.5,
        description="D128: SPY return below which momentum entries blocked. "
        "Widened from -1.0%% to -2.5%% — small-cap gappers have near-zero SPY "
        "correlation, and -1.0%% triggers on normal market days (~15%% of days). "
        "Mar 27: SPY was -1.40%% but watchlist stocks were +7-13%%. The -2.5%% "
        "level catches genuine broad selloffs (~2%% of days).",
    )
    # D203: Day-of-week regime gate based on D200-E3 regime experiment results.
    # Mon: 57% WR, +3.7% avg (positive EV)
    # Tue: 30% WR, -3.7% avg (NEGATIVE EV)
    # Wed: 38% WR, -1.2% avg (NEGATIVE EV)
    # Thu: 59% WR, +4.7% avg (positive EV)
    # Fri: 33% WR, -6.5% avg (NEGATIVE EV)
    trading_days_allowed: str = Field(
        default="0,1,2,3,4",
        description="D203/D211: All weekdays enabled for paper trading data collection. "
        "D200-E3 showed Mon/Thu were best in 196 historical scenarios, but with only "
        "49 live trades the sample is too small to justify blocking 3/5 of trading days. "
        "Collect data on all days to build a real signal. "
        "Set to '0,3' to restrict to Mon/Thu only once sample size > 200.",
    )
    min_directional_agents: int = Field(
        default=1,
        description="D125: Lowered from 2→1 after 3 consecutive zero-trade days. "
        "With min=2, the system required 2+ non-NEUTRAL agents — but on small-cap "
        "gappers, fundamental_agent (float unknown), news_agent (no coverage), and "
        "manipulation_classifier (no catalyst = suspicious) all return NEUTRAL. "
        "Only technical_agent is directional, so min=2 made the system unable to "
        "trade its target universe. Safety nets: MFCS threshold, D124 consensus "
        "alignment (bearish can't outnumber bullish), D106 manipulation classifier, "
        "spread filter, stop loss. The count gate was redundant on top of these.",
    )
    # D126: Gap-Up Momentum Mode thresholds
    gap_momentum_score_threshold: float = Field(
        default=1.0,
        description="D126: Composite threshold (gap_pct × rvol) for momentum mode. "
        "Captures 10%% gap × 10x RVOL, 20%% gap × 5x, 50%% gap × 2x, etc.",
    )
    gap_momentum_min_gap: float = Field(
        default=0.08,
        description="D126: Minimum gap %% floor for momentum mode (matches EMC threshold).",
    )
    gap_momentum_min_rvol: float = Field(
        default=2.5,
        description="D126: Minimum RVOL floor for momentum mode.",
    )

    # BUG-FIX: Validate that agent weights sum to ~1.0 to prevent silent
    # configuration corruption from env var overrides.
    @field_validator("deep_search")
    @classmethod
    def _validate_weight_sum(cls, v: float, info) -> float:
        """Check weight sum after all fields are loaded (deep_search is last weight)."""
        data = info.data
        weight_sum = (
            data.get("catalyst_news", 0.55) +
            data.get("technical", 0.05) +
            data.get("volume_rvol", 0.25) +
            data.get("float_structure", 0.15) +
            data.get("institutional", 0.0) +
            v  # deep_search (current field being validated)
        )
        if abs(weight_sum - 1.0) > 0.05:
            raise ValueError(
                f"Agent weights must sum to ~1.0 (got {weight_sum:.3f}). "
                f"Check SCORE_* env vars: news={data.get('catalyst_news')}, "
                f"tech={data.get('technical')}, rvol={data.get('volume_rvol')}, "
                f"float={data.get('float_structure')}, inst={data.get('institutional')}, "
                f"deep={v}"
            )
        return v

    @model_validator(mode="after")
    def _validate_vix_threshold_ordering(self) -> "ScoringWeights":
        """D211: Ensure VIX thresholds are in ascending order."""
        if self.vix_reduce_threshold >= self.vix_block_threshold:
            raise ValueError(
                f"vix_reduce_threshold ({self.vix_reduce_threshold}) must be < "
                f"vix_block_threshold ({self.vix_block_threshold})"
            )
        if self.vix_block_threshold >= self.vix_panic_threshold:
            raise ValueError(
                f"vix_block_threshold ({self.vix_block_threshold}) must be < "
                f"vix_panic_threshold ({self.vix_panic_threshold})"
            )
        return self

    model_config = SettingsConfigDict(env_prefix="SCORE_", env_file=str(_ENV_FILE), extra="ignore")


class ExecutionConfig(BaseSettings):
    """Position sizing and risk management. MOMENTUM_LOGIC.md §6."""

    halt_new_entries: bool = Field(
        default=False,
        description="D277 HALT_NEW_ENTRIES (2026-04-28): operator kill switch. "
        "When True, AlpacaClient.submit_oto_order refuses ALL new entries — "
        "FAST_PATH, Phase 2, Phase 3 RESCAN, VWAP. Existing positions, exits, "
        "and stop ratchets are unaffected. Override via env: "
        "MOMENTUM_HALT_NEW_ENTRIES=1 (any of: 1/true/yes/on). "
        "Rationale: per evaluation/2026-04-28-edge-assessment.md §1.4 — no "
        "demonstrated edge yet; AR + AS shipped today restored recon "
        "observability. Halt entries until validation completes (catalyst "
        "stratification + ≥3 sessions of clean recon data).",
    )
    exit_policy: str = Field(
        default="bar1_legacy",
        description="D278 EXIT_POLICY (2026-04-29): which intraday-exit rules fire. "
        "Options: "
        "  'bar1_legacy' — original BAR-1 EXIT at T+60s (D146). The default; what "
        "    prod has been running. Per doc 69 broker-truth analysis, this exit "
        "    policy LOSES money on the actual trade pool (-$5K across 117 trades) "
        "    even though the entries identify winners (median MFE +2.61%). "
        "  't1_next_open' — skip BAR-1 EXIT entirely; let positions carry overnight; "
        "    existing D86/D91 overnight-detection + close-at-open path handles T+1. "
        "    Per doc 69 counterfactual: +$23K incremental EV per 117-trade pool over "
        "    bar1_legacy. Stop-losses, D245 SMART_EXIT, D163 trailing stops, D164 "
        "    early profit take all REMAIN ACTIVE — only the T+60s BAR-1 default is "
        "    skipped. Override via env: MOMENTUM_EXIT_POLICY=t1_next_open. "
        "RATIONALE: doc 69 §4 Enhancement 1 — replacing BAR-1 EXIT with multi-day "
        "hold default produces +$23K-$35K incremental P&L per 117-trade window on "
        "the actual broker trade pool.",
    )
    max_positions: int = Field(
        default=3,
        description="D162: Max concurrent positions. Reduced from 8→3 after Mar 26 "
        "backtest where 4 simultaneous stops fired for -$3,411 in a single session. "
        "Concentration risk: fewer positions = smaller multi-stop drawdown on bad days.",
    )
    max_position_pct: float = Field(
        default=0.15,
        description="Max 15% of portfolio per position. Competition mode: "
        "larger sizing with 4x PDT margin. Original: 5%.",
    )

    # D150: Aggressive paper trading sizing — tiered by float
    # Bar-1 exit provides built-in safety (out in 60s). Max single-trade loss
    # on a 3% bar-1 decline at 50% equity = 1.5% of portfolio.
    paper_aggressive_mode: bool = Field(
        default=True,
        description="D150: Enable tiered aggressive sizing for paper trading. "
        "Tier 1 (float<5M, gap>20%%, RVOL>5x): 50%% of equity. "
        "Tier 2 (float<20M, gap>10%%, RVOL>3x): 30%%. "
        "Tier 3 (everything else): 15%%. Kill switch: set False.",
    )
    # Tier 1: Ultra-low float + big gap + high RVOL (29% outlier rate)
    tier1_position_pct: float = Field(default=0.50, gt=0, le=1.0, description="D150: Tier 1 sizing — 50%% of equity.")
    tier1_float_max: int = Field(default=5_000_000, gt=0, description="D150: Tier 1 max float (5M shares).")
    tier1_gap_min: float = Field(default=0.20, ge=0, description="D150: Tier 1 min gap (20%%).")
    tier1_rvol_min: float = Field(default=5.0, ge=0, description="D150: Tier 1 min RVOL (5x).")
    tier1_max_concurrent: int = Field(default=2, gt=0, description="D150: Max 2 Tier 1 positions.")
    # Tier 2: Low float + moderate gap (24% outlier rate)
    tier2_position_pct: float = Field(default=0.30, gt=0, le=1.0, description="D150: Tier 2 sizing — 30%% of equity.")
    tier2_float_max: int = Field(default=20_000_000, gt=0, description="D150: Tier 2 max float (20M).")
    tier2_gap_min: float = Field(default=0.10, ge=0, description="D150: Tier 2 min gap (10%%).")
    tier2_rvol_min: float = Field(default=3.0, ge=0, description="D150: Tier 2 min RVOL (3x).")
    tier2_max_concurrent: int = Field(default=3, gt=0, description="D150: Max 3 Tier 2 positions.")
    # Tier 3: Standard (19% outlier rate)
    tier3_position_pct: float = Field(default=0.15, gt=0, le=1.0, description="D150: Tier 3 sizing — 15%% of equity.")
    tier3_max_concurrent: int = Field(default=5, gt=0, description="D150: Max 5 Tier 3 positions.")
    # Safety
    daily_drawdown_limit_pct: float = Field(
        default=0.03,
        description="D150/doc178: If account drops 3%% in a day, disable aggressive mode "
        "(revert to conservative verdict.position_size_pct sizing). TIGHTENED 5%%→3%% as the "
        "safety counterweight to the doc-178 posture inversion (wider exits + downgrade-not-"
        "block selection + ELITE press all raise per-day variance). Revert via env "
        "EXEC_DAILY_DRAWDOWN_LIMIT_PCT=0.05.",
    )
    weekly_drawdown_limit_pct: float = Field(
        default=0.10,
        description="D150: If account drops 10%% in a week, disable aggressive mode.",
    )
    kelly_fraction: float = Field(
        default=0.5,
        description="Half-Kelly for estimation error. MOMENTUM_LOGIC.md §6",
    )
    risk_per_trade_pct: float = Field(
        default=0.01,
        description="D101: Fixed-risk sizing — risk 1% of equity per trade. "
        "qty = floor(equity × risk_per_trade_pct / stop_distance). Sizes down "
        "for volatile names (wide stops), up for calm names (tight stops). "
        "Integrates naturally with ATR-based stops from D100.",
    )
    # doc 178 Tier 4 (posture inversion): press the highest-conviction tail.
    elite_sizing_press_enabled: bool = Field(
        default=True,
        description="doc 178: when True, names with MFCS >= elite_sizing_mfcs_threshold get "
        "risk_per_trade_pct raised to elite_risk_per_trade_pct (press the fat right-tail the "
        "thesis targets, per doc 176). Independent of the qty_multiplier downgrade — a "
        "no-catalyst ELITE name nets ~base (2%%×0.5), a clean ELITE name presses to 2%%. "
        "Absolute size still bounded by max_position_pct (15%%) + D150 tier caps. "
        "Revert via env EXEC_ELITE_SIZING_PRESS_ENABLED=false.",
    )
    elite_sizing_mfcs_threshold: float = Field(
        default=0.50,
        description="doc 178: MFCS floor for the ELITE sizing press. 0.50 catches the "
        "highest-conviction squeezes (NCPL 0.525, AMSS 0.546). Env: EXEC_ELITE_SIZING_MFCS_THRESHOLD.",
    )
    elite_risk_per_trade_pct: float = Field(
        default=0.02,
        description="doc 178: per-trade risk for ELITE-conviction names (2%% vs 1%% base). "
        "Applied via max() — never reduces a higher Kelly-tier risk. Env: EXEC_ELITE_RISK_PER_TRADE_PCT.",
    )
    # doc 189 (FILL the continuers): marketable, momentum-adaptive entry limit.
    marketable_limit_enabled: bool = Field(
        default=True,
        description="doc 189: place the entry BUY limit MARKETABLE (cross the spread) instead "
        "of passive at the stale eval price. Friday 5/29 CMND filled 0/2 on a passive limit -> "
        "a perfect pick that can't fill earns $0. Anchored to the freshest ask, offset scales "
        "with conviction, capped. A LIMIT (not market) bounds the worst fill. "
        "Revert via env EXEC_MARKETABLE_LIMIT_ENABLED=false.",
    )
    marketable_base_offset_pct: float = Field(
        default=0.004,
        description="doc 189: base marketable offset above the anchor ask (0.4%%) — crosses a "
        "typical small-cap spread. Used at low conviction. Env: EXEC_MARKETABLE_BASE_OFFSET_PCT.",
    )
    marketable_max_offset_pct: float = Field(
        default=0.015,
        description="doc 189: max marketable offset (1.5%%) at full conviction — the cap on how "
        "far we chase a fast continuer. Never chase a runner past this. Env: EXEC_MARKETABLE_MAX_OFFSET_PCT.",
    )
    marketable_mfcs_full_at: float = Field(
        default=0.55,
        description="doc 189: MFCS at which the offset reaches marketable_max_offset_pct "
        "(momentum-adaptive: strong continuers chase more to ensure the fill). Env: EXEC_MARKETABLE_MFCS_FULL_AT.",
    )
    # doc 199 (f1): rvol-exhaustion size penalty (data-backed, flag-gated OFF).
    rvol_exhaustion_size_penalty_enabled: bool = Field(
        default=False,
        description="doc 199: down-weight position size for rvol_exhaustion=True candidates. "
        "Selection study (doc 198, n=302): exhausted names RAN 20%% vs 41%% un-flagged — extreme "
        "premarket RVOL is exhaustion, not fuel. RISK-REDUCING (only shrinks size). Default OFF; "
        "flip after the auto-scorecard confirms on more sessions. Env: EXEC_RVOL_EXHAUSTION_SIZE_PENALTY_ENABLED.",
    )
    rvol_exhaustion_size_mult: float = Field(
        default=0.5,
        description="doc 199: size multiplier applied to rvol_exhaustion=True candidates when the "
        "penalty is enabled (0.5 = half size). Env: EXEC_RVOL_EXHAUSTION_SIZE_MULT.",
    )
    # doc 202 (the fade-short second strategy, flag-gated OFF).
    fade_short_enabled: bool = Field(
        default=False,
        description="doc 202: SHORT the predictable faders instead of buying them. Selection "
        "study (198-201): high-MFCS picks fade; the borrowable ones net +9.47%% short. When a "
        "BUY candidate matches the ELITE-fade signature (MFCS>=threshold ∧ shortable/ETB ∧ "
        "liquid ∧ NOT running) it is routed to a SHORT (fractional size) instead of a long. "
        "DEFAULT OFF — flip after the auto-scorecard confirms on more sessions (n was 11). "
        "Env: EXEC_FADE_SHORT_ENABLED.",
    )
    fade_short_min_mfcs: float = Field(
        default=0.50,
        description="doc 202: minimum MFCS to treat a BUY as a fade-short (the ELITE bucket "
        "that RAN 4%% / faded -6.19%%). Env: EXEC_FADE_SHORT_MIN_MFCS.",
    )
    fade_short_min_dollar_volume: float = Field(
        default=5_000_000.0,
        description="doc 202: liquidity floor for a fade-short (need fills + a survivable "
        "cover). Env: EXEC_FADE_SHORT_MIN_DOLLAR_VOLUME.",
    )
    fade_short_size_mult: float = Field(
        default=0.25,
        description="doc 202: position-size multiplier for a fade-short — START SMALL (n=11). "
        "Env: EXEC_FADE_SHORT_SIZE_MULT.",
    )
    stop_loss_pct: float = Field(
        default=0.055,
        description="D94: 5.5% stop-loss fallback when no ATR data available. "
        "D100: ATR-based stops are now primary; this is the fallback only.",
    )
    initial_stop_atr_multiplier: float = Field(
        default=2.0,
        description="D100: Initial stop = entry - max(multiplier × ATR_14d, entry × floor_pct). "
        "2.0× ATR places stop outside the noise band for small-cap momentum stocks.",
    )
    initial_stop_floor_pct: float = Field(
        default=0.04,
        description="D100: Minimum stop distance as % of entry price. Prevents "
        "infinite stop distance on high-ATR names. 4% = ~$0.40 on a $10 stock.",
    )
    gap_day_stop_widening_enabled: bool = Field(
        default=True,
        description="D122: On large gap-ups (>gap_day_threshold), use half the "
        "gap %% as minimum stop distance. Entry-delay sweep showed 41-47%% "
        "of stops trigger after price had prior gain — too tight on gap days.",
    )
    gap_day_threshold: float = Field(
        default=0.08,
        description="D122: Minimum gap %% (absolute) to trigger gap-day stop widening.",
    )
    gap_day_stop_cap: float = Field(
        default=0.35,
        description="D124: Maximum stop distance %% when gap-day widening is active. "
        "Replaces the default 20%% cap on gap days. Without this, the 20%% cap "
        "defeats gap-day widening for any gap > 40%% (ANNA bug: 63%% gap → "
        "31.5%% floor → capped to 20%%). Set to 35%% as absolute safety limit.",
    )
    # D139: Time-phased stops — arena finding: 41% P&L improvement
    # Phase 1 (0 to phase_stop_t1_minutes): Ultra-tight stop catches non-momentum
    # Phase 2 (after phase_stop_t1_minutes): Widen to ATR-based stop
    phase_stop_enabled: bool = Field(
        default=True,
        description="D139: Enable time-phased stops. Phase 1 uses tight stop for "
        "first N minutes, then widens to ATR stop. Arena sweep: T1=7min with 1.5%% "
        "stop improved P&L 41%% across 7 dates.",
    )
    phase_stop_t1_minutes: float = Field(
        default=7.0,
        description="D139: Minutes before Phase 1 (tight) transitions to Phase 2 (ATR). "
        "Arena sweep: 7 min was optimal (vs 3, 5). Stocks that don't move in 7 min "
        "aren't momentum trades.",
    )
    phase1_stop_pct: float = Field(
        default=0.015,
        description="D139: Phase 1 stop distance as %% of entry. 1.5%% = ultra-tight. "
        "After phase_stop_t1_minutes, widens to ATR-based stop via stop_resubmitter. "
        "Arena sweep: 1.5%% was optimal (vs 2%%, disabled).",
    )

    # D145: Bar-1 exit — sell 100% at T+60s (the spike)
    # Arena finding: +$5.11 (+299% vs original). Walk-forward 2.0x overfit (at threshold).
    # MFE peaks at bar 1. Remainder captures -88%. 100% exit eliminates the loss center.
    # Tranche targets superseded by bar-1 exit but kept as fallback.
    bar1_exit_enabled: bool = Field(
        default=True,
        description="D145: Enable bar-1 exit. Sell bar1_exit_pct of position at T+60s. "
        "Arena: +$5.11 across 79 trades, +299%% vs original. Walk-forward: test PF=3.20. "
        "MFE peaks at bar 1. Remainder is -88%% capture. 100%% exit is optimal.",
    )
    bar1_exit_pct: float = Field(
        default=1.00, gt=0, le=1.0,
        description="D145: Fraction to sell at bar 1 (T+60s). 1.00 = sell 100%%. "
        "Walk-forward overfit: 50%%=1.5x (most robust), 80%%=1.8x, 100%%=2.0x (at threshold). "
        "Fallback to 0.50 if monitoring shows problems.",
    )
    bar1_exit_delay_seconds: float = Field(
        default=60.0,
        description="D145: Seconds after entry to trigger bar-1 exit. Default 60s = 1 bar.",
    )

    # D165: Tranche profit-taking targets
    # D142 had +1%/+2.5%/+5% — too tight, never fired in practice (ARTL hit +7%,
    # SST hit +6%, zero tranches). Raised to +3%/+6%/+10% matching arena findings.
    tranche_t1_pct: float = Field(
        default=0.03,
        description="D206: T1 target at +3%%. Bar data shows 100%% of passing stocks hit "
        "+3%% at some point. Lock 33%% of position early. Price-based fallback via D165.",
    )
    tranche_t2_pct: float = Field(
        default=0.08,
        description="D206: T2 target raised from +6%%→+8%%. Strategy optimizer (196 scenarios): "
        "Tranche_3_8_EOD had best risk-adjusted performance: 5.8x PF, 6.8%% max DD, "
        "80%% WR, +3.79%% avg P&L. Captures more of the main move vs 6%%.",
    )
    tranche_t3_pct: float = Field(
        default=0.10,
        description="D165: T3 target at +10%%. Price-based fallback via D165TrancheTaker.",
    )
    # D165: Enable price-based tranche fallback in Phase 3
    d165_tranche_enabled: bool = Field(
        default=True,
        description="D165: Enable price-based tranche profit-taking in Phase 3.",
    )
    d165_adjust_stop_after_partial: bool = Field(
        default=True,
        description="D165: Resize stop order qty after each partial tranche sell.",
    )

    # D142: Phase 0 ultra-tight stop for pipeline inversion safety
    phase0_stop_enabled: bool = Field(
        default=False,
        description="D142: Ultra-tight stop for first 30s. DISABLED per D145 finding: "
        "Phase 0 stops 25/79 trades that are net positive (+$0.72) under bar-1 exit. "
        "Bar-1 exit at T+60s IS the safety mechanism — 60s max exposure replaces "
        "the 30s Phase 0 stop. Re-enable only if bar-1 exit is disabled.",
    )
    phase0_stop_pct: float = Field(
        default=0.008,
        description="D142: Phase 0 stop distance (0.8%%). Ultra-tight for first 30s. "
        "Widens to Phase 1 (1.5%%) after LLM confirmation or 30s timeout.",
    )
    phase0_duration_seconds: float = Field(
        default=30.0,
        description="D142: Phase 0 duration in seconds. After this, widens to Phase 1.",
    )

    max_entry_spread_pct: float = Field(
        default=0.01,
        description="D101: Max bid-ask spread as % of price for entry. Reject if "
        "real-time spread > 1% of price. D125: On high-conviction entries "
        "(MFCS > debate threshold + bullish news), gap_day_spread_max applies instead.",
    )
    gap_day_spread_max: float = Field(
        default=0.03,
        description="D125: Wider spread tolerance for high-conviction gap-day entries. "
        "Applied when MFCS > mfcs_buy_threshold AND verdict has bullish catalyst. "
        "PTLE missed: 2.65%% spread blocked a +20%% winner. On confirmed catalysts, "
        "the expected move (20%%+) absorbs the spread cost (2.65%%). Default 3%%.",
    )
    daily_loss_limit_pct: float = Field(
        default=0.10,
        description="Circuit breaker: halt if daily P&L < -10%. "
        "Raised from 5% for competition mode.",
    )
    close_positions_by: str = Field(
        default="15:45",
        description="Close all intraday positions by 3:45 PM ET",
    )
    stale_entry_cutoff_hour: int = Field(
        default=11,
        description="D97/D211: Hour (ET) after which Phase 3 rescan entries are blocked. "
        "Extended from 10:30→11:30 AM for paper trading data collection. "
        "Captures mid-morning VWAP breakouts and second-wave momentum.",
    )
    stale_entry_cutoff_minute: int = Field(
        default=30,
        description="D97: Minute (ET) component of stale entry cutoff.",
    )
    deterministic_only: bool = Field(
        default=False,
        description="D107 WS6: Zero-LLM-calls mode. Only runs DeterministicTechnical "
        "+ DeterministicRisk agents. Answers 'does the scanner/strategy have edge?' "
        "independent of LLM quality. Enable via --deterministic-only CLI flag.",
    )

    model_config = SettingsConfigDict(env_prefix="EXEC_", env_file=str(_ENV_FILE), extra="ignore")


class ExitIntelligenceConfig(BaseSettings):
    """D78: Smart exit intelligence parameters."""

    enabled: bool = Field(
        default=True,
        description="Enable D78 smart exit intelligence in Phase 3.",
    )
    signal_history_dir: str = Field(
        default="data/signal_history",
        description="D107 WS1: Directory for per-cycle exit signal JSONL logs. "
        "Each trading day produces signal_log_{YYYY-MM-DD}.jsonl with all 13 "
        "signal values per position per cycle. Used for post-session signal "
        "efficacy analysis during configuration freeze.",
    )
    # D63: Trailing stop parameters
    trailing_stop_activation_pct: float = Field(
        default=0.04,
        description="Activate trailing stop when position up +4% from entry. "
        "Raised from 2% — replay analysis (Mar 19-20) showed 2% triggers on "
        "the first minor pullback, exiting ANNA at +2.3% of a +50% move. "
        "4% lets momentum trades develop toward tranche targets before trailing.",
    )
    trailing_stop_atr_multiplier: float = Field(
        default=2.0,
        description="Trail at 2x ATR below current price.",
    )
    trailing_stop_fallback_pct: float = Field(
        default=0.05,
        description="D100: Fallback trail below current price if no ATR. "
        "Raised from 3% — 3% trail on small-caps = whipsaw on any pullback > 1% from high.",
    )
    # Exit signal thresholds (D94: tuned via 4-day sweep Feb24+25+26, Mar3)
    exit_tighten_threshold: float = Field(
        default=0.15,
        description="D106: Composite exit urgency >= 0.15 -> TIGHTEN stop. Lowered from "
        "0.20 for more responsive stop-tightening when thesis deteriorates. "
        "Original D94 sweep found 0.20 optimal, but with 12 signals averaging "
        "~0.08 each, even 0.15 requires 2+ signals firing simultaneously.",
    )
    exit_threshold: float = Field(
        default=0.40,
        description="D106: Composite exit urgency >= 0.40 -> market sell EXIT. Lowered "
        "from 0.60 which required 7-8 of 12 signals at maximum simultaneously — "
        "effectively impossible. 0.40 requires 4-5 signals, achievable when "
        "distribution is genuinely occurring. In 16 trading days the 0.60 "
        "threshold was NEVER reached — zero exit intelligence exits triggered.",
    )
    # Time-of-day decay
    time_decay_start_hour: int = Field(
        default=10,
        description="Start time decay after 10:30 AM ET.",
    )
    time_decay_aggressive_hour: int = Field(
        default=14,
        description="Aggressive tightening after 2:00 PM ET.",
    )
    # Adaptive targets
    adaptive_targets_enabled: bool = Field(
        default=True,
        description="Use ATR/time-based adaptive targets instead of static +3/6/10%.",
    )
    # D89: Chandelier Exit
    chandelier_atr_multiplier: float = Field(
        default=4.0,
        description="D89: Chandelier Exit multiplier. 4.0× for small-cap (3.0× too tight).",
    )
    # D109 Phase 4: Parallel exit strategies
    parallel_strategies_enabled: bool = Field(
        default=True,
        description="D109: Compute parallel exit strategies (Velocity, Pullback, "
        "Volume Exhaustion, Gratitude) each cycle and log results to signal "
        "history. Does NOT affect trading decisions during config freeze.",
    )
    parallel_strategies_active: bool = Field(
        default=True,
        description="D109→D122: When True, parallel strategies can trigger actual "
        "EXIT/TIGHTEN actions (any-of architecture, UPGRADE-only). "
        "Activated D122 after freeze ended. Rollback: set to False.",
    )
    parallel_exit_min_confidence: float = Field(
        default=0.85,
        description="D122/doc178: Minimum confidence threshold for parallel strategy EXIT. "
        "RAISED 0.7→0.85 (posture inversion): a single high-conf strategy was flipping "
        "HOLD→market-EXIT and surrendering the tail (gratitude exited BB at +1.8%%, "
        "alpha_oracle exited UMAC at -2.2%% while it was set to run +26%%). The primary "
        "D78 smart-exit + D163 trail + hard stop still protect; this overlay should fire "
        "only on near-certain reversals. Revert via env EXIT_PARALLEL_EXIT_MIN_CONFIDENCE=0.7.",
    )
    parallel_tighten_min_confidence: float = Field(
        default=0.5,
        description="D122: Minimum confidence threshold for parallel strategy TIGHTEN.",
    )
    parallel_min_strategies_for_exit: int = Field(
        default=2,
        description="D122/doc178: Minimum parallel strategies agreeing on EXIT before "
        "upgrading. RAISED 1→2 (posture inversion). The prior 'set to 1' reasoning "
        "(correlated signals add no confirmation) was theoretically clean but in LIVE "
        "trading a single strategy repeatedly flattened winners at micro-gains. Requiring "
        "2 agreeing strategies + 0.85 conf reserves the override for genuine consensus "
        "reversals; the D163 trail + hard stop remain the primary protection. "
        "Revert via env EXIT_PARALLEL_MIN_STRATEGIES_FOR_EXIT=1.",
    )
    parallel_exit_excluded_strategies: list[str] = Field(
        default=["pullback", "catalyst_half_life", "gratitude", "alpha_oracle"],
        description="D122/doc178: Strategies excluded from triggering parallel EXIT/TIGHTEN. "
        "doc 177 ADDED gratitude + alpha_oracle: despite a low aggregate fire-rate they "
        "fired on WINNERS (gratitude flattened BB at +1.8%%, alpha_oracle exited UMAC at "
        "-2.2%% as it was set to run +26%%) — i.e. they exit the instant return dips below "
        "a null/gratitude baseline, which clips exactly the momentum tail we target. "
        "Revert via env EXIT_PARALLEL_EXIT_EXCLUDED_STRATEGIES (JSON list). Original note: "
        "Signal history analysis (355 signals, 3 days) showed pullback fires "
        "EXHAUSTED EXIT at 92% rate (fires on flat positions, not just exhausted "
        "advances) and catalyst_half_life fires EXIT at 85% rate (all positions "
        "have catalyst_type='unknown' → 20-min half-life). These strategies are "
        "broken, not selective. Remaining 4 strategies (velocity, volume_exhaustion, "
        "gratitude, alpha_oracle) fire at 2.8% rate — genuinely selective. "
        "Remove from list after fixing: (1) catalyst_type propagation (D111 bug), "
        "(2) PullbackClassifier EXHAUSTED-on-flat-positions bug.",
    )
    # D110: Catalyst Half-Life
    catalyst_half_life_table: dict = Field(
        default={
            "fda_approval": {"median": 180, "p25": 60, "p75": 390},
            "earnings_beat": {"median": 120, "p25": 45, "p75": 390},
            "contract_deal": {"median": 90, "p25": 30, "p75": 240},
            "technical_breakout": {"median": 25, "p25": 12, "p75": 60},
            "social_media_promo": {"median": 8, "p25": 4, "p75": 15},
            "sec_filing_dilutive": {"median": 12, "p25": 5, "p75": 25},
            "unknown": {"median": 20, "p25": 8, "p75": 45},
        },
        description="D110: Catalyst half-life table (minutes). Median = expected "
        "time for 50%% of catalyst's price impact to decay. Used by "
        "CatalystHalfLifeStrategy for information-side exit timing.",
    )
    # D110: Alpha Decay Oracle
    alpha_oracle_null_curve: list = Field(
        default=[(5, 3.0), (10, 2.0), (15, 1.5), (20, 1.0), (30, 0.5), (45, 0.2), (60, 0.0)],
        description="D110: Null return curve [(minutes_since_open, expected_return_pct), ...]. "
        "Average gap-up stock return at each minute. v1: domain-knowledge estimate. "
        "Refine from backtest --null-time output.",
    )
    # D110: Contagion Network
    contagion_decay_minutes: float = Field(
        default=10.0,
        description="D110: Minutes over which contagion signals decay to zero. "
        "Start at 10 (sector rotations take 10-20 min to propagate across names).",
    )
    contagion_threshold: float = Field(
        default=0.3,
        description="D110: Minimum contagion intensity to trigger TIGHTEN on target position.",
    )

    # D118: Entry Catalyst Profiler — LLM catalyst classification at entry time
    catalyst_profiler_enabled: bool = Field(
        default=False,
        description="D118: Enable LLM catalyst profiling at entry. When True, "
        "classifies catalyst durability and logs profile. Shadow-log mode: "
        "profile is logged but exit strategies use static tables until "
        "catalyst_profiler_active=True.",
    )
    catalyst_profiler_active: bool = Field(
        default=False,
        description="D118: When True AND enabled, catalyst profile overrides "
        "static half-life table and parameterizes exit strategies. "
        "Must remain False during initial shadow-logging phase.",
    )
    catalyst_profiler_timeout: int = Field(
        default=10,
        description="D118: Timeout in seconds for catalyst classification LLM call.",
    )
    catalyst_profiler_max_tokens: int = Field(
        default=2048,
        description="D118: Max tokens for catalyst classification response.",
    )

    # D214: Archetype exit system (off by default until validation promotes it)
    archetype_exit_enabled: bool = Field(
        default=False,
        description="D214: Enable archetype-specific null curves for AlphaDecayOracle. "
        "Requires trained model at archetype_model_path. Off until validation promotes.",
    )
    archetype_model_path: str = Field(
        default="data/archetypes/archetype_model.json",
        description="D214: Path to trained archetype model JSON.",
    )
    archetype_z_exit_threshold: float = Field(
        default=-0.5,
        description="D214: Z-score threshold for archetype exit. Swept in validation.",
    )
    archetype_z_consecutive_bars: int = Field(
        default=2,
        description="D214: Bars below z threshold to trigger exit. Swept in validation.",
    )
    archetype_target_haircut: float = Field(
        default=0.7,
        description="D214: Target = peak_mean × haircut. Swept in validation.",
    )
    archetype_confidence_threshold: float = Field(
        default=0.6,
        description="D214: Confidence below this routes to fallback (bar-1 exit).",
    )

    model_config = SettingsConfigDict(env_prefix="EXIT_", env_file=str(_ENV_FILE), extra="ignore")


class TrailingStopConfig(BaseSettings):
    """
    D163: Software-managed trailing stop system.

    Distinct from D63 (which ratchets the Alpaca stop order via Chandelier/ATR).
    D163 monitors price each Phase 3 cycle in pure software and fires a market EXIT
    when the trail is breached — no Alpaca order is touched.

    Backtest motivation (Mar 30): 14/18 trades hit the fixed 30-min stop.
    ARTL peaked +7% then reversed to -55%; D163 would have exited at +3.5%.
    SST peaked +6% then reversed to -35%; D163 would have exited at +3%.
    """

    enabled: bool = Field(
        default=True,
        description="D163: Enable software trailing stop. Set False to disable without "
        "changing other stop logic. D63 Chandelier/ATR ratcheting continues regardless.",
    )
    activation_threshold_pct: float = Field(
        default=0.06,
        description="D163/doc178: Activate trailing when position is +6%% in our favour. "
        "WIDENED 2%%→6%% (doc 176 'biggest single lever'): the +2%% activation exited LFS "
        "at +0.7%% (ran +28%%) and APPS at +0.3%%. Below +6%% the position is protected by "
        "the initial ATR/5.5%% stop only — let momentum names breathe before trailing. "
        "Revert via env TRAIL_ACTIVATION_THRESHOLD_PCT=0.02. For shorts: -6%% below entry.",
    )
    trail_pct_of_gain: float = Field(
        default=0.30,
        description="D163/doc178: Trail at 30%% of max gain (WIDENED from 50%%). At +20%% "
        "peak the trail sits at +14%% (gives back 30%% of the gain) vs +10%% under the old "
        "50%%. Surrendering half the gain on a fat-tail momentum name is the posture error "
        "doc 176 flagged. Revert via env TRAIL_TRAIL_PCT_OF_GAIN=0.50.",
    )
    min_trail_distance_pct: float = Field(
        default=0.05,
        description="D163/doc178: Trail never tighter than 5%% from current price "
        "(WIDENED from 2%%). The 2%% floor exited on normal opening-volume noise on thin "
        "gappers. 5%% dominates below ~+17%% gain, then trail_pct_of_gain takes over. "
        "Revert via env TRAIL_MIN_TRAIL_DISTANCE_PCT=0.02.",
    )
    max_trail_distance_pct: float = Field(
        default=0.35,
        description="D163: Trail is never wider than 35%% from current price. "
        "Matches the maximum initial stop distance on gap days (gap_day_stop_cap). "
        "Prevents trail from being so wide it never fires.",
    )

    model_config = SettingsConfigDict(env_prefix="TRAIL_", env_file=str(_ENV_FILE), extra="ignore")


class EarlyProfitTakeConfig(BaseSettings):
    """
    D164: Time-based partial profit take at T+2 minutes after entry.

    Arena data shows MFE peaks at bar 1. Most gap stocks give a brief profit
    window before reversing. D146 (bar-1 exit) captures this with a 100% sell.
    D164 is softer: sell 50% at T+2min if profitable, let D163 trail the rest.

    Interaction with D146: if bar-1 fires first (T+60s), position is closed
    before D164's T+2min trigger fires → check() returns NOT_TRACKED. No conflict.
    """

    enabled: bool = Field(
        default=True,
        description="D164: Enable time-based early profit take. Sell exit_pct at "
        "T+delay_seconds if position is profitable by at least min_profit_pct. "
        "Set False to disable without affecting D146 or D163.",
    )
    delay_seconds: float = Field(
        default=120.0,
        description="D164: Seconds after fill to check for early profit take. "
        "Default 120s (T+2 minutes). MFE data shows bar-1 spike fades quickly; "
        "T+2min captures the post-spike peak before mean reversion.",
    )
    exit_pct: float = Field(
        default=0.50,
        ge=0.01,
        le=1.0,
        description="D164: Fraction of ORIGINAL position to sell at trigger. "
        "0.50 = sell 50%%, hold 50%% for D163 trailing stop. Uses floor() rounding "
        "(e.g. 333 shares × 50%% = 166 sold, 167 held).",
    )
    min_profit_pct: float = Field(
        default=0.005,
        ge=0.0,
        description="D164: Minimum profit required to trigger. 0.005 = +0.5%%. "
        "Position must be at least this far in-the-money at T+delay_seconds. "
        "If not profitable enough, SKIP — let normal stop management handle it.",
    )
    max_delay_seconds: float = Field(
        default=300.0,
        description="D164: Maximum seconds after fill to trigger. 300s = T+5min. "
        "If Phase 3 monitoring loop is delayed (e.g. slow snapshot fetch), the "
        "check() call may arrive late. Past this window, always SKIP.",
    )
    apply_to_shorts: bool = Field(
        default=True,
        description="D164: Apply early profit take to short positions as well. "
        "For shorts, profitable means price is BELOW entry. Partial cover "
        "(buy-to-cover qty_to_sell) via submit_order(side='buy').",
    )

    model_config = SettingsConfigDict(
        env_prefix="EARLY_PROFIT_", env_file=str(_ENV_FILE), extra="ignore"
    )


class ObservationConfig(BaseSettings):
    """
    D170: Entry Delay with Observation Window.

    Addresses 0% win rate on 9:30–9:33 entries. Live trade data (Mar–Apr 2026)
    shows 10 open-bucket trades at 0% win rate, avg P&L -$1,263. Stocks like
    ARTL and BFRG were already below VWAP at 9:30 — the observation window
    would have rejected them before order submission.

    Instead of entering immediately at Phase 2 BUY verdict, candidates are
    placed in WATCHING state. Each monitoring cycle feeds price/VWAP/volume
    to EntryDelayManager.update_price(). When all criteria pass and the minimum
    window has elapsed, state transitions to APPROVED and the order is submitted.
    """

    enabled: bool = Field(
        default=True,
        description="D170: Enable entry delay observation window. When False, all "
        "Phase 2 BUY candidates are immediately approved (legacy behaviour).",
    )
    observation_minutes: float = Field(
        default=15.0,
        ge=1.0,
        le=60.0,
        description="D170: Normal observation window in minutes. Candidate must pass "
        "all criteria for this long before order submission. Default 15 min.",
    )
    min_observation_minutes: float = Field(
        default=3.0,
        ge=1.0,
        description="D170/D211: Minimum observation before any entry. Reduced from 5→3 min "
        "for paper trading data collection. Prevents entering on the very first tick "
        "after open while still capturing early momentum.",
    )
    max_observation_minutes: float = Field(
        default=30.0,
        ge=5.0,
        le=120.0,
        description="D170: Hard timeout — candidate is EXPIRED if not approved within "
        "this window. Prevents holding a candidate across irrelevant market conditions.",
    )
    require_above_vwap: bool = Field(
        default=True,
        description="D170: Reject if latest price is below VWAP. Stocks below VWAP "
        "are in institutional distribution, not accumulation. Key signal from ARTL/BFRG.",
    )
    require_higher_lows: bool = Field(
        default=False,
        description="D170/D211: Disabled for paper trading data collection. "
        "Fading stocks make progressively lower lows — this catches the pattern early. "
        "Re-enable (True) once sample size > 200 trades.",
    )
    require_no_new_low: bool = Field(
        default=True,
        description="D170: No new low in the last no_new_low_lookback readings. "
        "Complements require_higher_lows with a tighter recent-window check.",
    )
    no_new_low_lookback: int = Field(
        default=3,
        ge=2,
        description="D170: Number of recent readings for the no-new-low check.",
    )
    min_volume_sustain_pct: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="D170/D211: Disabled (0.0) for paper trading data collection. "
        "Was 0.50 — volume must be >= this fraction of opening bar. "
        "Re-enable (0.50) once sample size > 200 trades.",
    )
    max_drawdown_from_open_pct: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="D170: Reject if price drops more than this fraction from open_price. "
        "0.10 = reject if down more than 10%% from the Phase 2 reference price.",
    )
    early_entry_min_mfcs: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="D201: Lowered from 0.60→0.30. D200-E2 showed optimal entry at "
        "9:35 ET (+3.54%%, 65%% WR) vs 9:30 (+0.33%%, 48%% WR). With min_observation "
        "at 5 min, lowering the early-entry MFCS threshold makes 9:35 entry achievable "
        "for more candidates. Combined with catalyst gate, MFCS 0.30+ with confirmed "
        "catalyst is a reasonable quality bar.",
    )
    early_entry_min_agents_bullish: int = Field(
        default=2,
        ge=1,
        description="D201: Lowered from 4→2. With institutional + deep_search weights "
        "at 0.00 (D200 rebalance), requiring 4 bullish agents is impossible — only "
        "4 agents have nonzero weight. 2 bullish agents with catalyst confirmation "
        "(D200-E4 gate) is sufficient quality signal.",
    )

    model_config = SettingsConfigDict(
        env_prefix="OBSERVATION_", env_file=str(_ENV_FILE), extra="ignore"
    )


class DebateConfig(BaseSettings):
    """Debate engine parameters. ADR-001, MOMENTUM_LOGIC.md §10."""

    divergence_high_threshold: float = Field(
        default=0.6,
        description="DIV > 0.6 → full position. MOMENTUM_LOGIC.md §10",
    )
    divergence_low_threshold: float = Field(
        default=0.15,
        description="DIV < threshold → reduced position or no trade. MOMENTUM_LOGIC.md §10. "
        "Lowered from 0.3 for Phase 3 backtesting — partial data compresses divergence. "
        "Production target: 0.3 once debate prompts are calibrated and data is complete.",
    )
    mfcs_debate_threshold: float = Field(
        default=0.35,
        description="D97: Minimum MFCS to trigger debate engine. "
        "Raised from 0.15 — at 0.15 nearly all candidates qualified, causing "
        "35 debates with only 3 BUY on D11. At 0.35, only candidates with "
        "genuine multi-agent support enter debate.",
    )
    max_debate_attempts: int = Field(
        default=0,
        description="D100: Debate engine killed. 0% conversion rate over 7 debates, "
        "20-45s latency each. Speed of execution > quality of deliberation for momentum. "
        "Set to 2 to re-enable. Previous: D97 default=2.",
    )

    model_config = SettingsConfigDict(env_prefix="DEBATE_", env_file=str(_ENV_FILE), extra="ignore")


class FastPathConfig(BaseSettings):
    """D85: Fast-path pre-market scoring and immediate entry at market open."""

    enabled: bool = Field(
        default=True,
        description="Enable fast-path pre-market scoring and immediate entry.",
    )
    fast_path_threshold: float = Field(
        default=0.35,
        description="Minimum partial MFCS to queue a fast-path entry. "
        "Lower than full MFCS buy threshold since only 3 of 6 agents (50% signal weight).",
    )
    max_fast_path_entries: int = Field(
        default=3,
        description="Maximum concurrent fast-path orders at market open. "
        "Keep small — these are pre-market entries with THIRD sizing.",
    )
    position_size_pct: float = Field(
        default=0.08,
        description="Position sizing for fast-path entries (8% = THIRD). "
        "Larger than original 5% because 3-agent scoring (50% MFCS weight) "
        "provides higher confidence than 1-agent. Max risk = 0.32% equity per entry.",
    )
    scoring_time_minutes_before_open: int = Field(
        default=10,
        description="Minutes before market open to run fast-path scoring (default: score at 9:20 ET). "
        "10 min buffer for 3-agent parallel LLM calls (~20-30s) + order staging.",
    )
    cancel_on_disagree: bool = Field(
        default=True,
        description="Cancel/close fast-path position if full LLM evaluation disagrees.",
    )
    upgrade_on_confirm: bool = Field(
        default=True,
        description="Increase position size if full LLM evaluation confirms the trade.",
    )

    model_config = SettingsConfigDict(env_prefix="FAST_PATH_", env_file=str(_ENV_FILE), extra="ignore")


class ExperimentSettings(BaseSettings):
    """D102: Experimentation framework configuration."""

    enabled: bool = Field(
        default=True,
        description="D102: Enable experiment parameter replay. When True, every "
        "candidate evaluation also replays through experiment variants defined "
        "in the YAML config. Zero additional LLM calls — parameter replay only.",
    )
    yaml_path: str = Field(
        default="data/experiments/experiments.yaml",
        description="D102: Path to experiment definitions YAML file.",
    )
    max_variants_per_candidate: int = Field(
        default=30,
        description="D102: Safety cap on total variants per candidate evaluation. "
        "Prevents runaway if YAML has too many experiments defined.",
    )
    journal_dir: str = Field(
        default="data/experiments",
        description="D102: Directory for experiment journal JSONL files.",
    )

    model_config = SettingsConfigDict(env_prefix="EXPERIMENT_", env_file=str(_ENV_FILE), extra="ignore")


class RouterConfig(BaseSettings):
    """D112: Adaptive Compute Router — three-tier evaluation depth routing."""

    enabled: bool = Field(
        default=True,
        description="D112: Enable 3-tier adaptive routing. When False, all candidates "
        "go through the full LLM pipeline (existing behavior).",
    )
    # Tier 1: INSTANT_REJECT thresholds (more conservative than scanner —
    # catches anything that slipped through or was loosened by overrides)
    instant_reject_min_rvol: float = Field(
        default=1.0,
        description="D162: RVOL below this is instant reject. Relaxed from 1.5→1.0 "
        "after arena analysis showed removing/relaxing RVOL filter captures 4 big "
        "movers (including XWEL +102%%) at only 1%% precision cost. Stocks with "
        "moderate volume still have real catalysts. D112: Scanner default is 2.0 "
        "but D105 dollar-volume override can admit lower RVOL stocks.",
    )
    instant_reject_min_price: float = Field(
        default=0.50,
        description="D112: Price below this is instant reject (absolute floor).",
    )
    instant_reject_max_float: int = Field(
        default=2_000_000_000,
        description="D112: Float above 2B shares is wrong universe for gap-and-go. "
        "Apr 16 hotfix (D220): was 200M, killed 5 candidates with MFCS=0.821 "
        "(IMMP 1.17B, VSA 503M, QBTS 295M, HUBC, XHG). The router also has an "
        "MFCS escape hatch (deterministic_strong_pass) that bypasses this gate "
        "when the deterministic signal is strong. See "
        "docs/research-log/03_immediate_fixes.md.",
    )
    instant_reject_min_gap_pct: float = Field(
        default=0.03,
        description="D112: Gap below 3% is too small for momentum strategy.",
    )
    # Tier 1: Pump pattern detection
    pump_gap_threshold: float = Field(
        default=0.30,
        description="D112: Gap > 30% combined with price < pump_price_threshold "
        "triggers pump pattern detection.",
    )
    pump_price_threshold: float = Field(
        default=3.00,
        description="D112: Price below this combined with large gap triggers pump pattern.",
    )
    pump_catalyst_override: bool = Field(
        default=True,
        description="D112: Allow strong cached catalyst (FDA, earnings) to override "
        "pump INSTANT_REJECT → route to FULL_PIPELINE instead.",
    )
    # Tier 2: DETERMINISTIC_ONLY thresholds
    deterministic_strong_pass: float = Field(
        default=0.40,
        description="D112: Quick deterministic MFCS above this = clear winner, "
        "skip LLM agents. Deterministic signals already decisive.",
    )
    deterministic_clear_reject: float = Field(
        default=0.05,
        description="D112: Quick deterministic MFCS below this = clear reject, "
        "skip LLM agents. Would reach same NO_TRADE verdict slower.",
    )

    model_config = SettingsConfigDict(env_prefix="ROUTER_", env_file=str(_ENV_FILE), extra="ignore")


class KellyTierConfig(BaseSettings):
    """D115: Tiered Kelly Criterion position sizing — scales risk 1-5% by conviction."""

    enabled: bool = Field(
        default=True,
        description="D115: Enable tiered Kelly sizing. Scales risk 1-5% by conviction. "
        "Sweep fix: enabled by default — was in shadow mode, preventing any "
        "position scaling for high-conviction trades.",
    )
    # ── Tier 1: Standard (default for most trades) ──
    tier1_risk_pct: float = Field(
        default=0.02,
        description="D208: Raised from 1%%→2%%. With D199-D207 gates producing 80%% WR "
        "on longs, Kelly = 62%%. Even conservative sizing should be higher than 1%%.",
    )
    tier1_max_position_pct: float = Field(
        default=0.25,
        description="D208: Raised from 15%%→25%%. D208 arena: 25%% long sizing produces "
        "+57.6%% return with only 3.5%% maxDD (ratio 16.61). Conservative given "
        "full Kelly = 62%%.",
    )
    # ── Tier 2: High Conviction ──
    tier2_risk_pct: float = Field(
        default=0.04,
        description="D208: Raised from 2%%→4%%. Quarter-Kelly for 80%% WR setups.",
    )
    tier2_max_position_pct: float = Field(
        default=0.35,
        description="D208: Raised from 25%%→35%%. D208 arena: tiered sizing with "
        "higher long allocation dramatically improves returns.",
    )
    tier2_min_mfcs: float = Field(
        default=0.25,
        description="D115: Minimum MFCS for Tier 2. Sweep fix: recalibrated from 0.60 "
        "to 0.25 — actual MFCS distribution peaks around 0.15-0.40, so 0.60 "
        "was unreachable and Tier 2 never activated.",
    )
    tier2_min_directional_agents: int = Field(
        default=3,
        description="D115: Require 3+ directional agents for Tier 2.",
    )
    tier2_min_rvol: float = Field(
        default=5.0,
        description="D115: Extreme volume confirmation for Tier 2.",
    )
    tier2_min_reward_risk: float = Field(
        default=2.5,
        description="D115: Minimum gap_pct/stop_pct ratio for Tier 2.",
    )
    tier2_require_no_same_sector: bool = Field(
        default=True,
        description="D115: No existing position in same sector for Tier 2.",
    )
    tier2_first_n_minutes: int = Field(
        default=30,
        description="D115: Tier 2 only in first N minutes after open (highest alpha).",
    )
    tier2_min_win_rate: float = Field(
        default=0.35,
        description="D115: Minimum system win rate (last 30 trades) for Tier 2.",
    )
    tier2_proven_catalysts: list[str] = Field(
        default=["FDA_APPROVAL", "EARNINGS_BEAT", "M_AND_A", "CONTRACT_WIN", "SHORT_SQUEEZE"],
        description="D115: Catalyst types with historically proven positive expectancy.",
    )
    # ── Tier 3: Exceptional Conviction ──
    tier3_risk_pct: float = Field(
        default=0.06,
        description="D208: Raised from 4%%→6%%. D208 arena: with 80%% WR, "
        "aggressive sizing is justified. Kelly=62%%, this is ~10%% Kelly.",
    )
    tier3_max_position_pct: float = Field(
        default=0.50,
        description="D208: Raised from 35%%→50%%. D208 arena: 50%% long sizing "
        "produces +127.7%% return with 5.8%% maxDD (ratio 22.05 — BEST of all). "
        "This is the flagship aggressive tier for highest-conviction trades.",
    )
    tier3_min_mfcs: float = Field(
        default=0.35,
        description="D115: Minimum MFCS for Tier 3. Sweep fix: recalibrated from 0.80 "
        "to 0.35 — matches top ~5% of observed MFCS scores.",
    )
    tier3_catalysts: list[str] = Field(
        default=["FDA_APPROVAL", "EARNINGS_BEAT", "M_AND_A"],
        description="D115: Highest-conviction catalyst types only for Tier 3.",
    )
    tier3_min_gap_pct: float = Field(
        default=0.10,
        description="D115: Gap sweet spot floor — enough momentum.",
    )
    tier3_max_gap_pct: float = Field(
        default=0.50,
        description="D115: Gap sweet spot ceiling. Sweep fix: widened from 30% to 50% "
        "— ANNA at 37.2% gap was the best trade but would fail Tier 3.",
    )
    tier3_max_float: int = Field(
        default=20_000_000,
        description="D115: Float <= 20M for supply/demand squeeze conditions.",
    )
    tier3_max_vix: float = Field(
        default=20.0,
        description="D115: No macro headwind — VIX must be below this.",
    )
    tier3_require_spy_not_down: bool = Field(
        default=True,
        description="D115: SPY must not be gapping down for Tier 3.",
    )
    tier3_require_positive_daily_pnl: bool = Field(
        default=True,
        description="D115: Must have positive daily P&L (not chasing losses).",
    )
    tier3_max_open_exit_urgency: float = Field(
        default=0.15,
        description="D115: No open position under stress (exit urgency > this).",
    )
    # ── Tier 4: Statistical Outlier ──
    tier4_risk_pct: float = Field(
        default=0.08,
        description="D208: Raised from 5%%→8%%. Half-Kelly for proven 80%% WR. "
        "Only fires with confirmed catalyst + extreme MFCS + all guardrails.",
    )
    tier4_max_position_pct: float = Field(
        default=0.50,
        description="D208: Raised from 40%%→50%%. Same cap as Tier 3 — the risk "
        "per trade is higher (8%% vs 6%%) but position cap is the same. "
        "The 50%% was proven optimal in D208 arena (22.05 risk-adjusted ratio).",
    )
    tier4_min_mfcs: float = Field(
        default=0.45,
        description="D115: Near-perfect agent consensus for Tier 4. Sweep fix: "
        "recalibrated from 0.90 to 0.45 — matches top ~1% of observed scores.",
    )
    tier4_require_confirmed_catalyst: bool = Field(
        default=True,
        description="D115: catalyst_specificity must be CONFIRMED (not rumored).",
    )
    tier4_min_win_rate: float = Field(
        default=0.45,
        description="D115: Minimum system win rate for Tier 4.",
    )
    tier4_require_positive_unrealized: bool = Field(
        default=True,
        description="D115: Unrealized P&L must also be positive (house money only).",
    )
    tier4_max_concurrent_tier3_plus: int = Field(
        default=0,
        description="D115: No other Tier 3+ trade can be open for Tier 4.",
    )
    tier4_max_portfolio_heat_pct: float = Field(
        default=0.03,
        description="D115: Portfolio heat must be below 3% for Tier 4.",
    )
    tier4_min_reward_risk: float = Field(
        default=3.0,
        description="D115: Minimum reward/risk ratio for Tier 4.",
    )
    # ── Quality guardrails ──
    min_price_for_tier_upgrade: float = Field(
        default=1.00,
        description="D115: Minimum stock price for Tier 2+ sizing. Sub-$1 penny stocks "
        "should never get upgraded — replay analysis showed MOBX ($0.53) got "
        "Tier 2 sizing (26k shares) on 1.8% MFE, losing $1,219.",
    )
    min_gap_pct_for_tier_upgrade: float = Field(
        default=0.10,
        description="D115: Minimum gap % for Tier 2+ sizing. Stocks with small gaps "
        "lack the momentum catalyst to justify concentrated positions.",
    )
    # ── Safety guardrails ──
    max_tier3_plus_per_day: int = Field(
        default=2,
        description="D115: Hard cap on Tier 3+ trades per day.",
    )
    sequential_lockout_after_tier3_stop: bool = Field(
        default=True,
        description="D115: After a Tier 3+ stop-loss, no more Tier 3+ for the session.",
    )
    downgrade_when_budget_exceeded: bool = Field(
        default=True,
        description="D115: Auto-downgrade if tier risk > remaining daily budget.",
    )

    model_config = SettingsConfigDict(env_prefix="KELLY_", env_file=str(_ENV_FILE), extra="ignore")


class FallerDetectionConfig(BaseSettings):
    """
    D160: Faller Risk Score — pre-execution gate to avoid promotional faders.

    Runs AFTER MFCS scoring and BEFORE order submission. Scores each BUY candidate
    on a 0.0 (confident runner) to 1.0 (likely faller) scale by combining bearish
    and bullish microstructure signals.

    Position sizing tiers:
        score 0.00-0.30 → full position (confident runner)
        score 0.30-0.50 → 75% position
        score 0.50-0.60 → 50% position  (D162: lowered from 0.65)
        score  > 0.60   → REJECT (predicted fader)

    Design rationale (Mar 30 post-mortem):
        ARTL (-54.7%): manipulation 85%, no catalyst, unknown float → score ~0.75 REJECT
        SST (-35%):    bearish news, 5.6% spread, $138K dolvol, 20% below VWAP → score ~0.80 REJECT
        EEIQ (moderate): manipulation 85% but RSI 73, MACD+ → score ~0.35 reduce
        ELAB (runner):   real biotech catalyst, technical bullish → score ~0.10 full
        BFRG (runner):   real pharma, 829x RVOL, $4M+ dolvol → score ~0.05 full
    """

    enabled: bool = Field(
        default=True,
        description="D160: Enable faller risk scoring. Kill switch: set False.",
    )

    # ── Rejection thresholds ──
    reject_threshold: float = Field(
        default=0.60,
        description="D162: Faller score above this → hard REJECT for longs. "
        "Lowered from 0.65→0.60 after 0%% win rate on 18 live trades (Mar 2026 "
        "backtest). Stocks scoring 0.60-0.65 still route to short evaluation "
        "via D161 — if they don't qualify as shorts, they're REJECTED entirely, "
        "never longed. Aliases as max_faller_score_for_long.",
    )
    max_faller_score_for_long: float = Field(
        default=0.60,
        description="D162: Semantic alias for reject_threshold. Maximum faller "
        "score at which a LONG trade is still permitted. Above this threshold "
        "the stock routes to short evaluation (D161) or is rejected.",
    )
    reduce_half_threshold: float = Field(
        default=0.50,
        description="D160: Faller score above this → 50%% position size.",
    )
    reduce_partial_threshold: float = Field(
        default=0.30,
        description="D160: Faller score above this → 75%% position size.",
    )

    # ── doc 191 (gate-ACTION): continuation-confirmed faller EXEMPTION ──
    # Flip the faller block from "block all predicted faders" to "block UNLESS the
    # validated continuation signature (doc 188) is present AND the name is liquid
    # enough to fill+exit". DEFAULT OFF — wired live in OBSERVE mode until the doc-182
    # grader confirms confirmed-continuers actually run on OUR low-float universe
    # (doc 187's edge was validated on LIQUID names). When OFF, the wired call is a
    # no-op (the faller block behaves exactly as before).
    continuation_exemption_enabled: bool = Field(
        default=False,
        description="doc 191: EXEMPT a continuation-CONFIRMED + liquid name from the "
        "faller hard-reject. Default OFF (observe-first). Env: FALLER_CONTINUATION_EXEMPTION_ENABLED.",
    )
    continuation_exemption_min_dollar_volume: float = Field(
        default=5_000_000.0,
        description="doc 191: liquidity floor ($ daily dollar-volume) below which a "
        "confirmed continuer is NOT exempted — the edge needs fills + a survivable exit. "
        "Env: FALLER_CONTINUATION_EXEMPTION_MIN_DOLLAR_VOLUME.",
    )
    continuation_exemption_min_opening_rvol: float = Field(
        default=2.0,
        description="doc 191: minimum opening RVOL for a LIVE faller exemption (a higher "
        "bar than the detector's confirm threshold — extra conviction to override a hard "
        "gate). Env: FALLER_CONTINUATION_EXEMPTION_MIN_OPENING_RVOL.",
    )

    # ── Baseline ──
    baseline_faller_risk: float = Field(
        default=0.20,
        description="D160: Starting faller risk for any gap-up stock. Gap-ups revert "
        "more often than they continue — baseline of 20%% acknowledges this. "
        "Bullish signals push below baseline; bearish signals push above.",
    )

    # ── Bearish signal weights (increase faller score) ──
    weight_high_manipulation_no_catalyst: float = Field(
        default=0.35,
        description="D160: manipulation_probability > threshold AND no news catalyst. "
        "The ARTL/SST pattern: pumped with no real reason → likely distribution.",
    )
    weight_bearish_news: float = Field(
        default=0.20,
        description="D160: News agent returns BEAR or reasoning contains explicit "
        "bearish framing ('Why Is X Sliding?', 'analysts downgrade', etc.).",
    )
    weight_price_below_vwap: float = Field(
        default=0.25,
        description="D160: Current price is significantly below VWAP (>15%%). "
        "SST was 20%% below VWAP at evaluation — smart money already distributed.",
    )
    weight_high_spread: float = Field(
        default=0.15,
        description="D160: Bid-ask spread > 3%%. Added proportionally: "
        "3%% spread = +0.10, 5%% spread = +0.15 (SST had 5.6%%).",
    )
    weight_low_dollar_volume: float = Field(
        default=0.20,
        description="D160: Dollar volume < 500K — easy to manipulate, hard to exit. "
        "SST had $138K dolvol. At this level, even a 500-share exit moves price 1%%.",
    )
    weight_extreme_gap_no_catalyst: float = Field(
        default=0.15,
        description="D160: Gap > 100%% with no confirmed news catalyst. "
        "Pure promotional gaps that gap 100%%+ intraday almost always fade within 2h.",
    )
    weight_unknown_float: float = Field(
        default=0.10,
        description="D160: Float is None (unknown) AND no SEC data available. "
        "ARTL pattern: zero fundamental data makes exit sizing impossible.",
    )
    weight_multiple_caution_no_approve: float = Field(
        default=0.15,
        description="D160: Multiple agents return BEAR/CAUTION with zero BULL/APPROVE. "
        "All agents agree it's risky but MFCS still passes (e.g., news=BEAR + risk=VETO "
        "but technical=BULL pulls MFCS above threshold).",
    )

    # ── D192: Squeeze archetype weights ──
    weight_squeeze_faller_reduction: float = Field(
        default=0.30,
        description="D192: SQUEEZE classification reduces faller score by this amount. "
        "Stocks gapping up with >30% short float are running due to forced covering, "
        "not promotional pumping — the gap persistence is structural, not a fader signal. "
        "Applied as a negative (bullish) adjustment to the faller score.",
    )
    squeeze_gap_threshold: float = Field(
        default=0.05,
        description="D192: Minimum gap % (decimal) to trigger SQUEEZE classification. "
        "0.05 = 5%+ gap. Filters small technical gaps that can't trigger forced covering. "
        "Combined with short_float_pct >= 30%% for the full SQUEEZE signal.",
    )

    # ── D191: SEC filing signal weights ──
    weight_sec_dilution_active: float = Field(
        default=0.40,
        description="D191: 424B5 prospectus supplement filed today/yesterday. "
        "Company is actively selling shares into the market right now. "
        "The gap-up is being manufactured to enable distribution. Hard BEAR. "
        "Set to 0 to disable SEC integration without touching faller_detection.py.",
    )
    weight_sec_dilution_atm: float = Field(
        default=0.50,
        description="D191: 424B5 with at-the-market offering language (Rule 415(a)(4)). "
        "ATM programs allow continuous share sales at prevailing prices, "
        "suppressing every rally indefinitely. Hardest possible BEAR signal. "
        "Supersedes weight_sec_dilution_active when ATM language is detected.",
    )
    weight_sec_material_event: float = Field(
        default=0.25,
        description="D191: 8-K material event filed today — real news catalyst confirmed "
        "via SEC (not just LLM news agent). REDUCES faller score because genuine "
        "catalysts create sustained buying pressure that holds the gap. "
        "Applied as a negative (bullish) adjustment.",
    )

    # ── D193: Sentiment velocity signal weight ──
    weight_sentiment_velocity: float = Field(
        default=0.25,
        description="D193: Headline velocity signal base weight. Applied proportionally "
        "to NarrativeMomentum classification: VIRAL=-1.0×, BUILDING=-0.6×, STEADY=-0.2×, "
        "ISOLATED=+0.4×, SILENT=+0.6×. Deterministic (pure timestamp math, no LLM). "
        "Set to 0 to disable velocity signal without touching faller_detection.py.",
    )

    # ── D194: Order flow signal weight ──
    weight_order_flow: float = Field(
        default=1.0,
        description="D194: Order flow signal multiplier applied to the raw faller adjustment "
        "from OrderFlowAnalyzer. Raw adjustments: ACCUMULATION=-0.20, DISTRIBUTION=+0.20, "
        "RETAIL=+0.10. Set to 0 to disable without touching faller_detection.py.",
    )

    # ── Bullish signal weights (decrease faller score) ──
    weight_real_catalyst: float = Field(
        default=0.20,
        description="D160: Confirmed material news catalyst (pharma deal, FDA, earnings). "
        "The single strongest runner predictor — real catalysts hold gains.",
    )
    weight_price_at_vwap: float = Field(
        default=0.15,
        description="D160: Current price is AT or ABOVE VWAP. "
        "Confirms buying pressure — institutional demand absorbing supply.",
    )
    weight_low_spread: float = Field(
        default=0.10,
        description="D160: Bid-ask spread < 1%%. Tight spreads = institutional "
        "participation, real two-sided market, easy to exit cleanly.",
    )
    weight_high_dollar_volume: float = Field(
        default=0.15,
        description="D160: Dollar volume > $5M. Real institutional flow. "
        "At this level, price discovery is genuine, not manipulated.",
    )
    weight_rsi_sweet_spot: float = Field(
        default=0.10,
        description="D160: RSI between 60-80. Momentum without exhaustion — "
        "trending up but not yet in blow-off territory.",
    )
    weight_macd_positive: float = Field(
        default=0.10,
        description="D160: MACD line above signal line AND histogram positive. "
        "Momentum accelerating in the right direction.",
    )
    weight_agent_consensus_bullish: float = Field(
        default=0.15,
        description="D160: Multiple directional agents agree bullish (≥2 BULL/STRONG_BULL "
        "signals, not just technical). Cross-agent consensus is the strongest "
        "continuation signal we have.",
    )

    # ── Signal thresholds ──
    manipulation_high_prob_threshold: float = Field(
        default=0.70,
        description="D160: manipulation_probability above this triggers bearish flag.",
    )
    low_dollar_volume_threshold: float = Field(
        default=500_000,
        description="D160: Dollar volume below this → low_dollar_volume bearish flag.",
    )
    high_dollar_volume_threshold: float = Field(
        default=5_000_000,
        description="D160: Dollar volume above this → high_dollar_volume bullish flag.",
    )
    high_spread_threshold: float = Field(
        default=0.03,
        description="D160: Spread above this triggers bearish flag (proxy from risk_breakdown).",
    )
    low_spread_threshold: float = Field(
        default=0.01,
        description="D160: Spread below this triggers bullish flag.",
    )
    vwap_below_threshold_pct: float = Field(
        default=0.15,
        description="D160: Price more than this %% below VWAP triggers bearish flag. "
        "0.15 = 15%% below VWAP (SST was 20%% below).",
    )
    extreme_gap_threshold: float = Field(
        default=1.00,
        description="D160: Gap above this (100%%+) with no catalyst triggers bearish flag.",
    )
    rsi_sweet_spot_low: float = Field(
        default=60.0,
        description="D160: Lower bound of RSI sweet spot (momentum without exhaustion).",
    )
    rsi_sweet_spot_high: float = Field(
        default=80.0,
        description="D160: Upper bound of RSI sweet spot.",
    )
    bullish_consensus_min_agents: int = Field(
        default=2,
        description="D160: Minimum BULL/STRONG_BULL agent signals for bullish consensus flag.",
    )

    # ── D212: Free Data Enrichment Weights ──
    weight_float_rotation_exhausted: float = Field(
        default=0.15,
        description="D212: Bearish weight when float rotation > 3.0x (exhaustion).",
    )
    weight_serial_gapper: float = Field(
        default=0.15,
        description="D212: Bearish weight when stock has 3+ gap days in last 20 sessions.",
    )
    weight_day2_no_catalyst: float = Field(
        default=0.10,
        description="D212: Bearish weight when stock is a day-2 runner without confirmed catalyst.",
    )
    weight_vwap_extreme_deviation: float = Field(
        default=0.10,
        description="D212: Bearish weight when |VWAP deviation| > 10% (overextended).",
    )

    model_config = SettingsConfigDict(env_prefix="FALLER_", env_file=str(_ENV_FILE), extra="ignore")


class ShortSellingConfig(BaseSettings):
    """
    D161: Short Selling — turns high-faller-score rejects into short candidates.

    When the faller gate scores a stock above min_faller_score (default 0.65),
    instead of simply rejecting the trade, the system checks whether the stock
    can be shorted. High-confidence faders (score > full_size_faller_score=0.80)
    get a full short position; cautious faders (0.65-0.80) get a reduced size.

    Short entry requirements (ALL must pass):
      1. shortable=True AND easy_to_borrow=True from Alpaca asset endpoint
      2. faller_score >= min_faller_score (0.65)
      3. RVOL >= min_rvol_short (3x) — need volume for short to work
      4. Dollar volume >= min_dollar_volume_short ($500K) — need liquidity to exit
      5. Gap >= min_gap_pct_short (20%) — only short overextended gap-ups

    Stop/target structure (inverted from longs):
      - Stop-loss ABOVE entry: entry × (1 + stop_pct_above_entry)
      - Profit targets BELOW entry: entry × (1 + target_pct) for each negative target_pct

    Ref: D161 (Short Selling design — inspired by accidental EEIQ short, $19K profit)
    """

    enabled: bool = Field(
        default=False,
        description="D161: Master switch for short selling. "
        "DISABLED 2026-04-08 (D215): PF=0.11 over 25 trades, -244.6% cumulative. "
        "Short book destroyed 19% of long book gains. Do not re-enable without "
        "reading docs/d215_short_book_postmortem.md first.",
    )
    min_faller_score: float = Field(
        default=0.60,
        description="D162: Minimum faller score to consider shorting. Lowered from 0.65→0.60 "
        "to match the new reject_threshold — stocks scoring 0.60+ that fail the long path "
        "route to short evaluation. If they don't qualify as shorts (not shortable, low "
        "dolvol, etc.), they are REJECTED entirely. Never longed at 0.60+.",
    )
    full_size_faller_score: float = Field(
        default=0.80,
        description="D161: Faller score above this → full short position (high-confidence fader). "
        "ARTL pattern (manip 85%, no catalyst, unknown float) typically scores 0.75-0.85.",
    )
    reduced_size_multiplier: float = Field(
        default=0.50,
        description="D161: Position size multiplier for cautious short zone (0.65-0.80). "
        "50% of the normal position size — borderline faders get half exposure.",
    )
    min_rvol_short: float = Field(
        default=3.0,
        description="D161: Minimum relative volume to short. Need active tape for short to fill "
        "and for the fade to materialize within the session.",
    )
    min_dollar_volume_short: float = Field(
        default=500_000,
        description="D161: Minimum dollar volume to short. Need liquidity to cover. "
        "SST had $138K dolvol — would be rejected on this filter (correct, too illiquid to cover).",
    )
    min_gap_pct_short: float = Field(
        default=0.20,
        description="D161: Minimum gap as a decimal fraction to short (0.20 = 20%%). "
        "Only short overextended gap-ups that are likely to fade back. "
        "Consistent with CandidateStock.gap_pct (1.0 = 100%% gap).",
    )
    stop_pct_above_entry: float = Field(
        default=0.35,
        description="D161: Stop-loss distance ABOVE entry as a fraction (0.35 = 35%%). "
        "Short stop = entry × (1 + 0.35). Wide stop to avoid stop-hunts on promotional spikes.",
    )
    target_pcts: list[float] = Field(
        default_factory=lambda: [-0.03, -0.06, -0.10],
        description="D161: Profit target percentages BELOW entry (negative values). "
        "-0.03 = T1 at -3%%, -0.06 = T2 at -6%%, -0.10 = T3 at -10%%.",
    )

    model_config = SettingsConfigDict(env_prefix="SHORT_", env_file=str(_ENV_FILE), extra="ignore")


class AggressiveShortConfig(BaseSettings):
    """
    D207: Aggressive Gap Fader Short — proactive short path for extreme gap-ups.

    Unlike D161 (reactive: BUY eval -> faller gate -> short reroute), D207 identifies
    extreme gap-up stocks (>50%) as SHORT candidates DIRECTLY from scanner output.
    These are promotional pumps with no catalyst that statistically fade -27.7% avg.

    D200-D206 data: gap >50% losers n=8, avg intraday drop -27.7%, max -66.7%.
    Gap >30% losers: n=17, avg drop -25.6%.

    The proactive path runs BEFORE LLM evaluation, saving tokens and catching
    faders that the reactive D161 path misses (D161 requires BUY intent first).
    """

    enabled: bool = Field(
        default=False,
        description="D207: Proactive aggressive short path. "
        "DISABLED 2026-04-08 (D215): PF=0.11 over 25 trades, -244.6% cumulative. "
        "Do not re-enable without reading docs/d215_short_book_postmortem.md first.",
    )
    min_gap_pct: float = Field(
        default=0.30,
        description="D207: Minimum gap to qualify for aggressive short. 0.30 = 30%%. "
        "Arena proof: D207 on 30%%+ gaps = 78%% WR, +6.7%% avg, 1.9x PF on 23 trades. "
        "Lowered from 50%% to capture the larger opportunity set. "
        "Gap>50%% alone: 67%% WR, +2.5%% avg (8W/4L — 4 FFIE/GME squeeze stops).",
    )
    min_rvol: float = Field(
        default=3.0,
        description="D207: Minimum RVOL. Need active tape for short to fill and "
        "for the fade to materialize within the session.",
    )
    min_dollar_volume: float = Field(
        default=500_000,
        description="D207: Minimum dollar volume. Need liquidity to cover. "
        "SST had $138K dolvol — correctly rejected as too illiquid.",
    )
    require_no_catalyst: bool = Field(
        default=True,
        description="D207: Require NO confirmed catalyst. Only short promotional "
        "pumps. If news_agent found FDA/earnings/M&A, don't short — those gaps hold.",
    )
    stop_pct_above_entry: float = Field(
        default=0.35,
        description="D207: Stop-loss distance ABOVE entry (0.35 = 35%%). Same as D161. "
        "Wide stop avoids stop-hunts on promotional spikes before the fade.",
    )
    target_pcts: list[float] = Field(
        default_factory=lambda: [-0.05, -0.15, -0.25],
        description="D207: Aggressive profit targets BELOW entry. "
        "-0.05 = T1 at -5%%, -0.15 = T2 at -15%%, -0.25 = T3 at -25%%. "
        "D161 targets (-3%%/-6%%/-10%%) leave 17%% on table — avg fader drops -27.7%%.",
    )
    max_concurrent: int = Field(
        default=1,
        ge=1,
        description="D207: Maximum concurrent aggressive short positions. "
        "Conservative: 1 at a time. Increase after live validation.",
    )
    position_size_pct: float = Field(
        default=0.02,
        ge=0.005,
        le=0.10,
        description="D207: Position size as fraction of portfolio. 0.02 = 2%%. "
        "Conservative until live-validated.",
    )

    model_config = SettingsConfigDict(env_prefix="AGGRESSIVE_SHORT_", env_file=str(_ENV_FILE), extra="ignore")


class UniverseConfig(BaseSettings):
    """
    D199: Tiered Stock Universe — controls which tiers are active and their
    scanning criteria. Allows enabling/disabling CATALYST mid-cap plays
    independently from the existing MOMENTUM low-float strategy.

    The MOMENTUM tier is the original strategy. CATALYST is the D199 expansion.
    Each tier has its own scanning thresholds and execution parameters defined
    in src/data/universe_tiers.py.
    """

    momentum_enabled: bool = Field(
        default=True,
        description="D199: Enable MOMENTUM tier (low-float gap-ups). The original "
        "strategy. Disable to run CATALYST-only.",
    )
    catalyst_enabled: bool = Field(
        default=True,
        description="D199: Enable CATALYST tier (mid-cap earnings/FDA/contract plays). "
        "New in D199. Stocks must have $1B+ market cap, 3%+ gap, 1.5x+ RVOL, $10M+ dolvol.",
    )
    catalyst_gap_min_pct: float = Field(
        default=0.03,
        description="D199: Minimum gap percentage for CATALYST tier stocks (0.03 = 3%%). "
        "Lower than MOMENTUM (5%%) because mid-caps rarely gap 20-100%%.",
    )
    catalyst_rvol_min: float = Field(
        default=1.5,
        description="D199: Minimum RVOL for CATALYST tier. 1.5x is meaningful for "
        "large-cap baselines that are already active. MOMENTUM requires 2x.",
    )
    catalyst_market_cap_min: float = Field(
        default=1_000_000_000,
        description="D199: Minimum market cap for CATALYST tier ($1B). Ensures "
        "institutional ownership that supports price after gap-up.",
    )
    catalyst_dollar_volume_min: float = Field(
        default=10_000_000,
        description="D199: Minimum dollar volume for CATALYST tier ($10M). Ensures "
        "real institutional liquidity — easy to enter and exit at size.",
    )
    catalyst_price_max: float = Field(
        default=200.0,
        description="D199: Maximum price for CATALYST tier ($200). Mid-cap stocks can "
        "be priced well above the MOMENTUM ceiling of $50.",
    )

    # D200-E4: Catalyst Confirmation Gate
    require_catalyst: bool = Field(
        default=True,
        description="D201: ENABLED by default. Blocks BUY verdicts for stocks with no "
        "confirmed catalyst (catalyst_type='unknown' or 'NONE'). D200-E4 experiment: "
        "70%% of BUY verdicts had no catalyst with 0%% win rate ($243K losses). "
        "Stocks WITH catalyst: 7%% win rate. Set False to disable.",
    )
    # doc 178 (posture inversion): catalyst/news gate DOWNGRADE-not-BLOCK on high MFCS
    catalyst_gate_downgrade_high_mfcs: bool = Field(
        default=True,
        description="doc 178: when True, the D200 catalyst gate and D204 news-confidence "
        "gate DOWNGRADE high-MFCS candidates to reduced size instead of hard-blocking. The "
        "no-catalyst cohort historically lost (D200 rationale) — but it ALSO contains the "
        "no-news low-float squeezes the thesis exists to trade (NCPL ran +38.5%%, AMSS — "
        "both blocked 5/28 at MFCS 0.52-0.55, partly because the news agent had TIMED OUT, "
        "not because no catalyst existed; see H3 timeout fix). Gating on MFCS>=threshold "
        "admits only the highest-conviction names, at half size to bound pump risk. "
        "Revert via env UNIVERSE_CATALYST_GATE_DOWNGRADE_HIGH_MFCS=false (hard-block).",
    )
    catalyst_gate_downgrade_mfcs_threshold: float = Field(
        default=0.45,
        description="doc 178: minimum MFCS for the catalyst/news downgrade-not-block. 0.45 "
        "separates high-conviction squeezes (NCPL 0.525, AMSS 0.546) from marginal no-news "
        "names. Below this the gate still HARD-BLOCKS. Env: UNIVERSE_CATALYST_GATE_DOWNGRADE_MFCS_THRESHOLD.",
    )
    catalyst_gate_downgrade_qty_mult: float = Field(
        default=0.5,
        description="doc 178: position-size multiplier (via verdict.qty_multiplier) for a "
        "downgraded no-catalyst/weak-news entry. 0.5 = half size to bound the risk of "
        "trading without confirmed news. Env: UNIVERSE_CATALYST_GATE_DOWNGRADE_QTY_MULT.",
    )

    model_config = SettingsConfigDict(env_prefix="UNIVERSE_", env_file=str(_ENV_FILE), extra="ignore")


class D216FeatureFlags(BaseSettings):
    """
    D217: Kill switches for D216 features.

    If any D216 component causes instability (hangs, crashes), disable it here
    without reverting code. The system falls back to pre-D216 behavior (LLM-only
    news analysis, no earnings calendar enrichment).
    """

    finbert_enabled: bool = Field(
        default=True,
        description="D217: Enable FinBERT sentiment pre-stage in news_agent. "
        "If False, skips FinBERT entirely — LLM-only analysis. "
        "Disable if FinBERT inference causes event loop hangs.",
    )
    finnhub_earnings_enabled: bool = Field(
        default=True,
        description="D217: Enable Finnhub earnings calendar refresh in Phase 0. "
        "If False, skips earnings headline injection. "
        "Disable if Finnhub API causes startup delays or errors.",
    )

    model_config = SettingsConfigDict(env_prefix="D216_", env_file=str(_ENV_FILE), extra="ignore")


class ServerConfig(BaseSettings):
    """
    D218: HTTP server port configuration.

    Previously hardcoded in main.py. Extracted for env-override capability.
    """

    health_port: int = Field(
        default=9091,
        description="D217: Health/control server port. Watchdog checks this endpoint.",
    )
    metrics_port: int = Field(
        default=9090,
        description="ADR-019: Prometheus metrics server port.",
    )

    model_config = SettingsConfigDict(env_prefix="SERVER_", env_file=str(_ENV_FILE), extra="ignore")


class OperationalConfig(BaseSettings):
    """
    D218: Operational parameters previously hardcoded in main.py.

    Extracted for env-override capability. All defaults match the
    previously-hardcoded values exactly — this is a pure extraction,
    not a tuning pass.
    """

    dashboard_interval_seconds: int = Field(
        default=30,
        description="D67: Live dashboard console refresh interval (seconds).",
    )
    scan_interval_seconds: int = Field(
        default=30,
        description="Pre-market scan polling interval (seconds).",
    )
    max_sector_positions: int = Field(
        default=4,
        description="D55: Max positions per sector for portfolio heat management.",
    )
    max_portfolio_heat_pct: float = Field(
        default=40.0,
        description="D55: Max portfolio heat percentage (sum of stop distances).",
    )
    eval_batch_timeout_seconds: float = Field(
        default=120.0,
        description="D217: Overall timeout for parallel candidate evaluation batch.",
    )
    debate_gather_timeout_seconds: float = Field(
        default=60.0,
        description="D217: Timeout for debate bull/bear parallel gather.",
    )
    smart_exit_close_timeout_seconds: float = Field(
        default=15.0,
        description="D217: Timeout for D78 smart exit close_position() call.",
    )
    eod_close_timeout_seconds: float = Field(
        default=30.0,
        description="D217: Timeout for Phase 4 EOD close_position() call.",
    )
    log_queue_size: int = Field(
        default=1000,
        description="D217: Max size of non-blocking log queue (QueueHandler).",
    )
    alert_webhook_url: str = Field(
        default="",
        description="D218: Discord webhook for CRITICAL ALERTS only. "
        "Fires on: preflight failure, watchdog kill, heartbeat silence, "
        "EOD session summary. Low volume (~5 messages/day max).",
    )
    watchlist_webhook_url: str = Field(
        default="",
        description="D218: Discord webhook for WATCHLIST updates. "
        "Fires on: Phase 1 watchlist (once pre-market), Phase 1.5 fast-path "
        "scores, Phase 2 BUY verdicts, position opens/closes. "
        "Moderate volume (~10-20 messages/day).",
    )

    model_config = SettingsConfigDict(env_prefix="OPS_", env_file=str(_ENV_FILE), extra="ignore")


class Settings(BaseSettings):
    """Root configuration aggregating all sub-configs."""

    alpaca: AlpacaConfig = Field(default_factory=AlpacaConfig)
    models: ModelConfig = Field(default_factory=ModelConfig)
    thresholds: ScannerThresholds = Field(default_factory=ScannerThresholds)
    scoring: ScoringWeights = Field(default_factory=ScoringWeights)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    debate: DebateConfig = Field(default_factory=DebateConfig)
    exit_intelligence: ExitIntelligenceConfig = Field(default_factory=ExitIntelligenceConfig)
    fast_path: FastPathConfig = Field(default_factory=FastPathConfig)
    experiments: ExperimentSettings = Field(default_factory=ExperimentSettings)
    router: RouterConfig = Field(default_factory=RouterConfig)
    kelly_tier: KellyTierConfig = Field(default_factory=KellyTierConfig)
    faller: FallerDetectionConfig = Field(default_factory=FallerDetectionConfig)
    short_selling: ShortSellingConfig = Field(default_factory=ShortSellingConfig)
    aggressive_short: AggressiveShortConfig = Field(default_factory=AggressiveShortConfig)
    trailing_stop: TrailingStopConfig = Field(default_factory=TrailingStopConfig)
    early_profit: EarlyProfitTakeConfig = Field(default_factory=EarlyProfitTakeConfig)
    observation: ObservationConfig = Field(default_factory=ObservationConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    d216: D216FeatureFlags = Field(default_factory=D216FeatureFlags)
    server: ServerConfig = Field(default_factory=ServerConfig)
    ops: OperationalConfig = Field(default_factory=OperationalConfig)

    # D107 WS4: External heartbeat webhook
    heartbeat_webhook_url: str = Field(
        default="",
        description="D107 WS4: External heartbeat webhook URL (e.g. Healthchecks.io). "
        "If non-empty, pings this URL every ~5 minutes as a liveness signal. "
        "Empty string disables webhook.",
    )

    # D108 WS2: Post-session notification webhook
    session_notification_url: str = Field(
        default="",
        description="D108 WS2: Webhook URL for post-session notification. "
        "Receives JSON POST with P&L, trade count, wins/losses. "
        "Empty string disables notification.",
    )

    # Global
    mode: str = Field(
        default="paper",
        description="'paper' or 'live'. NEVER default to live.",
    )
    log_level: str = Field(default="INFO")
    max_candidates_per_scan: int = Field(default=20)

    # Backtest settings
    backtest_ticker: str | None = Field(
        default=None,
        description="Ticker for historical backtest. None = synthetic data.",
    )
    backtest_days: int = Field(
        default=252,
        description="Number of trading days for historical backtest.",
    )
    backtest_tickers: list[str] | None = Field(
        default=None,
        description="Multi-ticker list for portfolio-level backtest.",
    )


def load_settings() -> Settings:
    """Load settings from environment variables with validation."""
    return Settings()
