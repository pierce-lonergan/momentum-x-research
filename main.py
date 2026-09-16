"""
MOMENTUM-X CLI Entrypoint

### ARCHITECTURAL CONTEXT
Node ID: cli.main
Graph Link: docs/memory/graph_state.json -> "cli.main"

### PURPOSE
The operational command center for running Momentum-X.
Supports modes: scan (pre-market only), evaluate (scan + analyze), paper (full loop), backtest.

### USAGE
  python -m main scan          # Pre-market gap scanner only
  python -m main evaluate      # Scan + agent evaluation pipeline
  python -m main paper         # Full paper trading loop
  python -m main backtest      # CPCV backtest on historical data

### CRITICAL INVARIANTS
1. Default mode is always paper (INV-007).
2. Live mode requires explicit --live flag + confirmation prompt.
3. All runs log to structured JSON for post-analysis.
4. Ctrl+C graceful shutdown with position report.

Ref: SYSTEM_ARCHITECTURE.md (4 Market Phases)
Ref: ADR-004 (Rate Limiting)
Ref: DATA-001-EXT CONSTRAINT-010 (GCP us-east4 deployment)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from config.settings import Settings
from src.analysis.post_trade import PostTradeAnalyzer, TradeResult
from src.agents.prompt_arena import PromptArena
from src.agents.default_variants import seed_default_variants
# Doc 82 (2026-04-29) Phase 2 ship: unified post-fill bookkeeping. Closes
# Bug AT-bypass for RESCAN + VWAP_BREAKOUT entry paths. See doc 83.
from src.execution.post_fill_handler import (
    post_fill_bookkeeping,
    PostFillContext,
)
# doc 272 LEVER-1 (FLAG-B, default OFF — env MOMENTUM_EMPTY_NOT_BEAR):
# EMPTY != BEAR fail-open classifier for the D200-E4/D204 block sites.
# With the env unset, absence_fail_open() returns "" unconditionally
# (no-op). Fail closed to legacy gate behavior on any import error.
try:
    from src.execution.empty_not_bear import absence_fail_open as _d272_absence_fail_open
except Exception:  # pragma: no cover — fail closed to legacy gate behavior

    def _d272_absence_fail_open(*_a: object, **_k: object) -> str:
        return ""


logger = logging.getLogger("momentum_x")


def setup_logging(verbose: bool = False) -> None:
    """Configure structured logging.

    D217 FIX: The root cause of the April 9 and April 10 hangs was a
    LOGGING DEADLOCK. Python logged to stderr only, which was piped through
    PowerShell's ForEach-Object. When the pipe buffer filled (~64KB), stderr
    writes blocked, which blocked logging.emit(), which blocked the asyncio
    event loop. The heartbeat watchdog ALSO blocked trying to log.

    Fix: Write logs directly to a file via RotatingFileHandler. The stderr
    StreamHandler is set to non-blocking (QueueHandler) so pipe backpressure
    can never block the event loop.
    """
    import os
    from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener
    import queue

    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s | %(name)-20s | %(levelname)-5s | %(message)s"
    formatter = logging.Formatter(fmt, datefmt="%H:%M:%S")

    root = logging.getLogger()
    root.setLevel(level)
    # Remove any existing handlers (prevents duplicate handlers on re-init)
    root.handlers.clear()

    # 1. FILE HANDLER: Direct write to rotating log file (PRIMARY — never blocks)
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    from datetime import datetime
    log_file = os.path.join(log_dir, f"momentum_{datetime.now().strftime('%Y-%m-%d')}.log")
    file_handler = RotatingFileHandler(
        log_file, maxBytes=50 * 1024 * 1024,  # 50MB per file
        backupCount=5, encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    # 2. CONSOLE HANDLER: Non-blocking via QueueHandler → QueueListener
    # If the stderr pipe fills (PowerShell ForEach-Object backpressure),
    # messages are dropped from the queue instead of blocking the event loop.
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    log_queue = queue.Queue(maxsize=1000)  # Bounded queue — drops on overflow
    queue_handler = QueueHandler(log_queue)
    queue_handler.setLevel(level)
    root.addHandler(queue_handler)

    # QueueListener drains the queue in a background thread → console
    _queue_listener = QueueListener(log_queue, console_handler, respect_handler_level=True)
    _queue_listener.start()
    # Store reference so it doesn't get garbage collected
    setup_logging._queue_listener = _queue_listener

    # Suppress noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("litellm").setLevel(logging.WARNING)
    # 2026-05-12 fix: pandas_ta_classic emits a WARNING for each indicator
    # call that has insufficient series rows -- 1,632 events on 2026-05-12
    # alone, all "Series has 5 rows but indicator requires at least N" on
    # the first 5 minutes of intraday bars. The library returns None
    # gracefully; the warning carries no actionable signal. Promote to
    # ERROR so genuine pandas_ta failures still surface.
    logging.getLogger("pandas_ta_classic.utils._core").setLevel(logging.ERROR)


async def cmd_scan(settings: Settings, interval: int = 30, once: bool = False) -> None:
    """
    Run pre-market gap scanner with optional continuous polling.

    Modes:
      --once: single scan iteration, print candidates, exit
      default: continuous polling every --interval seconds

    Pipeline per iteration:
      1. Fetch market snapshots from Alpaca
      2. EMC conjunction filter (Gap%, RVOL, ATR)
      3. GEX enrichment (if options data available)
      4. GEX hard filter (reject extreme positive GEX)
      5. Output ranked CandidateStock list

    Ref: SYSTEM_ARCHITECTURE.md (Phase 1: Pre-Market)
    Ref: ADR-014 (Pipeline Closure)
    """
    from src.core.scan_loop import ScanLoop
    from src.data.alpaca_client import AlpacaDataClient

    logger.info("═══ MOMENTUM-X PRE-MARKET SCANNER ═══")
    logger.info("Mode: %s | Feed: %s | Interval: %ds | Once: %s",
                settings.mode, settings.alpaca.feed, interval, once)

    client = AlpacaDataClient(settings.alpaca)

    # Verify connectivity
    try:
        account = await client.get_account()
        equity = float(account.get("equity", 0))
        logger.info("Account connected: equity=$%.2f", equity)
    except Exception as e:
        logger.error("Failed to connect to Alpaca: %s", e)
        logger.error("Check ALPACA_API_KEY and ALPACA_SECRET_KEY env vars")
        return

    scan_loop = ScanLoop(settings=settings)

    # Graceful shutdown
    shutdown = asyncio.Event()

    def handle_signal(sig, frame):
        logger.info("Shutdown signal received.")
        shutdown.set()

    signal.signal(signal.SIGINT, handle_signal)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, handle_signal)

    iteration = 0
    while not shutdown.is_set():
        iteration += 1
        now = datetime.now(timezone.utc)
        logger.info("─── Scan iteration #%d at %s ───", iteration, now.strftime("%H:%M:%S UTC"))

        try:
            # Fetch snapshots from Alpaca (most-active tickers)
            quotes = await _fetch_scan_quotes(client, settings)

            if quotes:
                candidates = scan_loop.run_single_scan(quotes)
                if candidates:
                    logger.info("Found %d candidates:", len(candidates))
                    for i, c in enumerate(candidates, 1):
                        logger.info(
                            "  %d. %s | Gap: %.1f%% | RVOL: %.1fx | Price: $%.2f%s",
                            i, c.ticker, c.gap_pct * 100, c.rvol, c.current_price,
                            f" | GEX_norm: {c.gex_normalized:.2f}" if c.gex_normalized is not None else "",
                        )
                else:
                    logger.info("No candidates passed filters this iteration.")
            else:
                logger.info("No market data available. Market may be closed.")

        except Exception as e:
            logger.error("Scan iteration failed: %s", e)

        if once:
            break

        # Wait for next interval or shutdown
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=interval)
        except asyncio.TimeoutError:  # noqa: silent-handler — TimeoutError IS the expected tick signal
            pass

    logger.info("Scanner stopped after %d iterations.", iteration)


def _d278_active_policy(settings) -> str:
    """D278 EXIT_POLICY (2026-04-29): resolve active exit policy.

    Priority (matches D277 halt switch pattern):
      1. env var MOMENTUM_EXIT_POLICY ∈ {bar1_legacy, t1_next_open}
      2. settings.execution.exit_policy
      3. default 'bar1_legacy'

    The env var is the operator's fast override path. Setting
    MOMENTUM_EXIT_POLICY=t1_next_open in the launcher env disables
    BAR-1 EXIT for the entire process lifetime; positions carry
    overnight; D86/D91 close-at-next-open path takes over.
    """
    env_val = os.environ.get("MOMENTUM_EXIT_POLICY", "").strip().lower()
    if env_val in {"bar1_legacy", "t1_next_open"}:
        return env_val
    cfg_val = str(getattr(getattr(settings, "execution", None), "exit_policy", "bar1_legacy") or "bar1_legacy").strip().lower()
    if cfg_val in {"bar1_legacy", "t1_next_open"}:
        return cfg_val
    return "bar1_legacy"


def _d278_bar1_gated_off(settings) -> bool:
    """Returns True iff the active exit policy disables BAR-1 EXIT."""
    return _d278_active_policy(settings) != "bar1_legacy"


def _clean_derivative_symbols(symbols: list[str]) -> list[str]:
    """D123: Map warrant/preferred/unit symbols to common stock tickers.

    Alpaca movers endpoint returns derivative symbols like SLND.WS (warrant),
    ANNAW (warrant), SPKL.U (unit) that can't be quoted via the equities
    snapshot API. These silently disappear, causing the system to miss the
    underlying common stock entirely.

    Bug: SLND.WS was the #1 gainer on Mar 23 (SLND common +27%) but the
    system never evaluated SLND because it stored the warrant symbol.

    Rules:
        .WS / .WS.A / .WS.B  -> strip suffix (SLND.WS -> SLND)
        .U / .UN              -> strip suffix (SPKL.U -> SPKL)
        .RT / .RT.A           -> strip suffix (GAB.RT -> GAB)  [D124]

    D126: REMOVED trailing-W rule (ANNAW -> ANNA). This corrupted real
    tickers like MKDW -> MKD, causing MKDW (+59% winner Mar 25, +59%
    Mar 26) to be silently dropped. Warrants like ANNAW will fail the
    snapshot lookup and be dropped, while the common ANNA is usually
    also in the movers list independently.

    Returns deduplicated list preserving order.
    """
    cleaned = []
    for sym in symbols:
        common = sym
        if ".WS" in common:
            common = common.split(".WS")[0]
        elif ".RT" in common:
            common = common.split(".RT")[0]
        elif common.endswith(".U") or common.endswith(".UN"):
            common = common.split(".")[0]
        cleaned.append(common)
    # Deduplicate preserving order (ANNA and ANNAW both -> ANNA)
    return list(dict.fromkeys(cleaned))


async def _d98_fetch_broker_positions(client) -> list | None:
    """D98/doc-285 gap #2: fetch broker positions; None means UNKNOWN.

    The 2026-07-06 10:48 phantom close: a circuit-breaker-open
    get_positions() raise was substituted with an empty list, the D98
    stop-out poll diffed internal state against that fabricated empty
    book, and two LIVE positions were booked as fake stop-outs
    (-$1,272 phantom realized; regime flipped off a fake loss; $35.7K
    ran unmanaged). PHANTOM-P&L iron rule extended to ABSENCE: a
    failed read is NOT broker evidence. Callers must treat None as
    "broker state unknown — skip the stop-out scan this cycle", never
    as "flat".
    """
    try:
        return await client.get_positions()
    except Exception as _e98:
        logger.warning(
            "D98: get_positions() failed: %s — broker state UNKNOWN "
            "(stop-out scan will be skipped this cycle)", _e98,
        )
        return None


async def _d98_confirm_stop_out(
    client,
    ticker: str,
    stop_order_id: str,
    absent_counts: dict[str, int],
    required_absences: int = 2,
) -> tuple[bool, float | None]:
    """D98/doc-285 gap #2: absence-confirmation before booking a stop-out.

    A symbol missing from a single get_positions() snapshot is not
    broker evidence of a close. Booking requires EITHER:
      (a) the tracked stop order reporting status=filled via the orders
          API — preferred; returns its real filled_avg_price so the
          exit books at broker truth, not the intended stop price; OR
      (b) ``required_absences`` CONSECUTIVE successful get_positions()
          snapshots all showing the symbol absent. The caller resets
          ``absent_counts`` on any fetch failure or reappearance, so a
          transient absence never accumulates across gaps.

    Returns (confirmed, filled_avg_price or None). Never raises.
    """
    # (a) Preferred: the stop leg's terminal status is direct broker
    # evidence of the exit AND its true fill price.
    if stop_order_id:
        try:
            _orders = await client.get_orders(
                status="all", symbols=ticker, limit=50,
            )
            for _o in _orders or []:
                if _o.get("id") != stop_order_id:
                    continue
                if _o.get("status") == "filled":
                    absent_counts.pop(ticker, None)
                    try:
                        _fap = float(_o.get("filled_avg_price") or 0)
                    except (TypeError, ValueError):
                        _fap = 0.0
                    return True, (_fap if _fap > 0 else None)
                break  # found but not filled — fall through to (b)
        except Exception as _oe:
            logger.debug(
                "D98 confirm: get_orders failed for %s: %s", ticker, _oe,
            )
    # (b) Fallback: N consecutive successful snapshots, all absent.
    absent_counts[ticker] = absent_counts.get(ticker, 0) + 1
    if absent_counts[ticker] >= required_absences:
        return True, None
    logger.info(
        "D98: %s absent from broker snapshot (%d/%d) — awaiting absence "
        "confirmation before booking stop-out",
        ticker, absent_counts[ticker], required_absences,
    )
    return False, None


async def _fetch_scan_quotes(client, settings: Settings) -> dict:
    """
    Fetch market snapshot data from Alpaca for scanner input.

    Returns dict of ticker -> {current_price, previous_close, premarket_volume, ...}
    suitable for ScanLoop.run_single_scan().

    Uses AlpacaDataClient.get_snapshots() which returns normalized data:
    {ticker: {last_price, prev_close, volume, bid, ask, ...}}

    Ref: DATA-001 (Alpaca snapshot endpoint)
    Ref: ADR-002 (Snapshot-based architecture)
    """
    try:
        # Get most-active tickers for scanning universe
        tickers = await client.get_most_active_tickers(limit=50)

        # D117: Merge top movers (gainers) to catch small-cap gappers
        # that don't appear in "most active" until later in the session.
        # Bug: CHNR/MOBX (Mar 19) missed at MARKET_OPEN, only found in
        # RESCAN at 10:14 ET — 44 minutes of missed opportunity.
        try:
            movers = await client.get_top_movers(limit=20)
            if movers:
                # D123: Map derivative symbols to common stock tickers
                movers = _clean_derivative_symbols(movers)

                # Add movers that aren't already in the active list
                existing = set(tickers)
                new_movers = [t for t in movers if t not in existing]
                if new_movers:
                    logger.info(
                        "D117: Adding %d movers not in most-active: %s",
                        len(new_movers), ", ".join(new_movers[:5]),
                    )
                    tickers = tickers + new_movers
        except Exception as e:
            logger.debug("D117: Movers merge failed (non-fatal): %s", e)

        if not tickers:
            return {}

        # Batch snapshot fetch via normalized client
        snapshots = await client.get_snapshots(tickers)
        quotes = {}
        for ticker, snap in snapshots.items():
            try:
                # Use normalized field names from AlpacaDataClient._normalize_snapshot
                current = float(snap.get("last_price", 0))
                prev_close = float(snap.get("prev_close", 0))
                volume = int(snap.get("volume", 0))
                avg_vol = max(1, int(snap.get("prev_volume", 1)))
                bid = float(snap.get("bid", 0))
                ask = float(snap.get("ask", 0))

                # Fallback to raw Alpaca fields if normalized fields missing
                if current <= 0:
                    current = float(
                        snap.get("latestTrade", {}).get("p", 0) or
                        snap.get("minuteBar", {}).get("c", 0)
                    )
                if prev_close <= 0:
                    prev_close = float(snap.get("prevDailyBar", {}).get("c", 0))
                if volume <= 0:
                    volume = int(
                        snap.get("minuteBar", {}).get("v", 0) or
                        snap.get("dailyBar", {}).get("v", 0)
                    )

                if current > 0 and prev_close > 0:
                    prev_vol = int(snap.get("prev_volume", 0))
                    day_open = float(snap.get("day_open", 0))
                    # D148 FIX: Include Alpaca dailyBar VWAP (vw field) as REST fallback
                    # for when WebSocket VWAP is unavailable. Used as intermediate fallback
                    # in Phase 3 VWAP scanner: WebSocket > REST snapshot > prev_close.
                    snap_vwap = float(snap.get("vwap", 0))
                    quotes[ticker] = {
                        "current_price": current,
                        "previous_close": prev_close,
                        "premarket_volume": volume,
                        "avg_volume_at_time": avg_vol,
                        "prev_volume": prev_vol if prev_vol > 0 else None,
                        "float_shares": snap.get("float_shares"),
                        "market_cap": snap.get("market_cap"),
                        "bid": bid,
                        "ask": ask,
                        "has_news": True,  # Conservative: assume news may exist
                        # D121: Pass day_open for stale gap detection
                        "day_open": day_open if day_open > 0 else None,
                        # D148: REST VWAP from Alpaca dailyBar.vw for WebSocket fallback
                        "vwap": snap_vwap if snap_vwap > 0 else None,
                    }
                else:
                    # D123: Log tickers dropped by price guard — these had a
                    # snapshot but invalid price data. Without this log, the
                    # ticker silently vanishes from the pipeline.
                    logger.debug(
                        "D123: %s dropped — invalid prices (current=%.2f, prev_close=%.2f)",
                        ticker, current, prev_close,
                    )
            except (ValueError, TypeError, KeyError):
                # D123: Log parse failures — without this, tickers with
                # malformed snapshot data silently disappear.
                logger.warning("D123: %s dropped — snapshot parse error", ticker, exc_info=True)
                continue

        # D123: Summary of pipeline attrition — how many tickers survived
        # each stage. Without this, silent drops at any stage go unnoticed.
        _n_requested = len(tickers)
        _n_snapshots = len(snapshots)
        _n_valid = len(quotes)
        if _n_valid < _n_requested:
            logger.info(
                "D123: Quote pipeline attrition: %d requested -> %d got snapshots -> %d valid quotes (%d lost)",
                _n_requested, _n_snapshots, _n_valid, _n_requested - _n_valid,
            )

        return quotes

    except Exception as e:
        logger.warning("Failed to fetch scan quotes: %s", e)
        return {}


async def cmd_evaluate(settings: Settings) -> None:
    """
    Run scanner + full agent evaluation pipeline.
    Produces TradeVerdicts without executing orders.
    """
    from src.core.orchestrator import Orchestrator
    from src.data.alpaca_client import AlpacaDataClient
    from src.data.news_client import NewsClient

    logger.info("═══ MOMENTUM-X EVALUATE MODE ═══")
    logger.info("Mode: %s | Debate threshold: %.2f", settings.mode, settings.debate.mfcs_debate_threshold)

    orchestrator = Orchestrator(settings)

    # Example evaluation flow (requires live data subscription)
    logger.info("Orchestrator initialized with %d agents", 6)
    logger.info("Pipeline: Scanner -> 5 Agents (parallel) -> MFCS -> Debate -> Risk -> Verdict")
    logger.info("Evaluation mode ready. Feed candidates via scan or manual ticker entry.")


async def cmd_paper(settings: Settings) -> None:
    """
    Full paper trading loop with ExecutionBridge wiring.

    Pipeline per cycle:
      1. ScanLoop -> CandidateStock list
      2. Orchestrator.evaluate_candidates() -> TradeVerdicts
      3. ExecutionBridge.execute_verdict(verdict, scored) -> OrderResult
      4. PositionManager tracks lifecycle, circuit breaker, tranche exits
      5. On close: bridge.close_with_attribution() -> Shapley -> Elo

    Ref: SYSTEM_ARCHITECTURE.md (4 Market Phases)
    Ref: ADR-003 (Execution Layer)
    Ref: ADR-014 (Pipeline Closure)
    Ref: ADR-016 (Production Wiring)

    Phase 1: Pre-Market (4:00-9:30 ET) — scan, build ranked watchlist
    Phase 2: Market Open (9:30-10:00 ET) — evaluate top candidates, execute BUYs
    Phase 3: Intraday (10:00-15:45 ET) — monitor positions, tranche exits
    Phase 4: After-Hours (16:00+) — close all, Shapley, report
    """
    from src.core.orchestrator import Orchestrator
    from src.core.scan_loop import ScanLoop
    from src.data.alpaca_client import AlpacaDataClient
    from src.data.news_client import NewsClient
    from src.execution.alpaca_executor import AlpacaExecutor
    from src.execution.bridge import ExecutionBridge
    from src.execution.position_manager import PositionManager
    from src.execution.tranche_monitor import TrancheExitMonitor, TrancheFillEvent
    from src.execution.stop_resubmitter import StopResubmitter
    from src.execution.fill_stream_bridge import FillStreamBridge
    from src.execution.portfolio_risk import PortfolioRiskManager
    from src.execution.exit_intelligence import ExitSignalEngine, ExitIntelligenceManager, SignalHistoryLogger
    from src.execution.trailing_stop import TrailingStopManager, TrailingStopAction
    from src.execution.early_profit_take import EarlyProfitTaker, EarlyProfitAction
    from src.execution.entry_delay import EntryDelayManager, ObservationState
    from src.execution.d165_tranche_taker import D165TrancheTaker, D165Config
    from src.execution.entry_delay import EntryDelayManager, ObservationState
    from src.scheduling.heartbeat import HeartbeatWatchdog
    from src.monitoring.server import MetricsServer
    from src.monitoring.metrics import reset_metrics

    if settings.mode != "paper":
        logger.error("Paper command requires mode=paper. Current: %s", settings.mode)
        return

    # D150: Safety check — refuse to run against live Alpaca endpoint
    _base_url = getattr(settings.alpaca, "base_url", "") or ""
    if _base_url and "paper" not in _base_url.lower():
        import os
        if os.environ.get("ALLOW_LIVE_TRADING") != "1":
            logger.error(
                "SAFETY HALT: Alpaca base URL (%s) does not contain 'paper'. "
                "Set ALLOW_LIVE_TRADING=1 to override.", _base_url,
            )
            return

    # D103: Capture session start time for accurate duration reporting.
    # Previously SessionReportGenerator used datetime.now() at instantiation
    # (Phase 4), yielding duration=0.0min. Now we pass the real start time.
    _session_start_utc = datetime.now(timezone.utc)

    logger.info("═══ MOMENTUM-X PAPER TRADING ═══")
    logger.info("Mode: PAPER | Max positions: %d | Max per position: %.1f%%",
                settings.execution.max_positions,
                settings.execution.max_position_pct * 100)
    logger.info("Stop-loss: %.1f%% | Daily loss limit: %.1f%%",
                settings.execution.stop_loss_pct * 100,
                settings.execution.daily_loss_limit_pct * 100)

    # ── Initialize pipeline components ──
    client = AlpacaDataClient(settings.alpaca)

    try:
        account = await client.get_account()
        equity = float(account.get("equity", 0))
        logger.info("Account connected: equity=$%.2f", equity)
    except Exception as e:
        logger.error("Failed to connect to Alpaca: %s", e)
        return

    # D73: Pre-flight API key validation — catch auth issues before trading starts
    api_valid = await client.validate_api_key()
    if not api_valid:
        logger.error("D73: API key validation failed — aborting. Check Alpaca dashboard.")
        return

    # ── Initialize SEC EDGAR Client (H-004: dilution detection) ──
    from src.data.sec_client import SECEdgarClient

    sec_client = SECEdgarClient()
    logger.info("SEC EDGAR client initialized: dilution detection enabled")

    # ── D191: SEC Filing Prefetcher (premarket batch dilution scan) ──
    from src.data.sec_prefetcher import SECPrefetcher
    sec_prefetcher = SECPrefetcher()
    logger.info("D191: SEC prefetcher initialized")

    # ── Initialize GEX Pipeline (ADR-012: live options -> GEX filter) ──
    from src.scanners.gex import GEXCalculator
    from src.data.options_provider import AlpacaOptionsProvider

    gex_calculator = GEXCalculator()
    options_provider = AlpacaOptionsProvider(
        api_key=settings.alpaca.api_key,
        api_secret=settings.alpaca.secret_key,
        base_url=settings.alpaca.base_url,
    )
    logger.info("GEX pipeline initialized: live options -> GEX filter")

    # ── D192: Short Interest Provider (squeeze archetype detection) ──
    from src.data.short_interest import ShortInterestProvider
    short_interest_provider = ShortInterestProvider()
    logger.info("D192: Short interest provider initialized")

    # D115: Trade result tracker for Kelly tier win rate history
    from src.execution.trade_result_tracker import TradeResultTracker
    _tracker_path = Path("data/trade_results.jsonl")
    _tracker_path.parent.mkdir(parents=True, exist_ok=True)
    trade_tracker = TradeResultTracker(history_file=_tracker_path)

    # Phase 0 instrumentation writer — single instance per session, threaded
    # into both the executor (for submit-time TradeContextRow) and the bridge
    # (for terminal TradeContextRow + ChildFillRow + BarContextRow). Each
    # emit is internally guarded so a Phase 0 failure never crashes trading.
    try:
        from src.analysis.instrumentation import InstrumentationWriter
        _phase0_writer: Any = InstrumentationWriter()
        logger.info("Phase 0 instrumentation writer initialized (session_date=%s)",
                    _phase0_writer.session_date)
    except Exception as _e:
        logger.warning("Phase 0 instrumentation writer init failed (disabled): %s", _e)
        _phase0_writer = None

    # Kelly governor — D223 BOCPD_BREAK → halve Kelly for next 5 trades.
    # Per Tuesday call discussion item #5 recommendation (b). Module-instance
    # lives for the session. KellyGovernor.current_multiplier() is read by
    # execution code at entry; record_trade_completed() is called at close.
    try:
        from src.analysis.kelly_governor import KellyGovernor
        _kelly_governor: Any = KellyGovernor()
        logger.info(
            "Kelly governor armed: halve to %.2f for %d trades on D223 BOCPD_BREAK",
            _kelly_governor.halve_multiplier, _kelly_governor.halve_duration_trades,
        )
    except Exception as _kge:
        logger.warning("Kelly governor init failed (disabled): %s", _kge)
        _kelly_governor = None

    # BOCPD changepoint detector — load pre-trained prior + construct state.
    # Operational from trade #1 of this session. observe(realized_pnl) fires
    # at every position close in bridge.close_with_attribution. D223
    # BOCPD_BREAK is logged internally when posterior_cp > kill_switch_threshold.
    # When Kelly governor is wired, D223 → governor.on_break_detected() → D224.
    # Falls back to a default prior if the persisted file is missing/corrupt
    # (BOCPD continues to run, just with weaker calibration).
    try:
        from pathlib import Path as _Path
        from src.analysis.bocpd import BOCPDPrior, BOCPDState
        _bocpd_prior_path = _Path("data/priors/s1_bocpd_prior.parquet")
        if _bocpd_prior_path.exists():
            _bocpd_prior = BOCPDPrior.from_parquet(_bocpd_prior_path)
            logger.info(
                "BOCPD prior loaded: mu_edge=%.4f sigma_edge=%.4f hazard=%.5f n_trades=%d",
                _bocpd_prior.mu_edge, _bocpd_prior.sigma_edge,
                _bocpd_prior.hazard_rate, _bocpd_prior.n_trades,
            )
        else:
            logger.warning(
                "BOCPD prior file not found at %s — using safe default prior. "
                "Regenerate via `python scripts/pretrain_bocpd_prior.py`.",
                _bocpd_prior_path,
            )
            _bocpd_prior = BOCPDPrior.from_corpus([])  # safe defaults
        _bocpd_state: Any = BOCPDState(_bocpd_prior, kelly_governor=_kelly_governor)
    except Exception as _bocpd_init_e:
        logger.warning("BOCPD init failed (disabled): %s", _bocpd_init_e)
        _bocpd_state = None

    # D308 (2026-05-19): construct the StopDecisionLog ONCE per session and
    # pass it to the executor. Each OTO submission emits a structured event
    # to data/shadow_stops/stop_decisions_YYYY-MM-DD.jsonl so the offline
    # replayer (scripts/stop_widening_replay.py) can compute hypothetical
    # P&L under wider stops. Production order behavior is UNCHANGED.
    from src.analysis.stop_decision_log import StopDecisionLog
    _d308_stop_log = StopDecisionLog()
    executor = AlpacaExecutor(
        config=settings.execution, client=client,
        instrumentation=_phase0_writer,
        kelly_governor=_kelly_governor,
        stop_decision_log=_d308_stop_log,
    )
    position_manager = PositionManager(
        config=settings.execution,
        starting_equity=equity,
    )

    # BUG-005 fix: Create market data WebSocket client for real-time VWAP.
    # Previously only TradeUpdatesStream (order fills) was wired — the market
    # data stream (SIP trades -> VWAP accumulator) was never instantiated,
    # causing _get_vwap() to always return None and VWAP gate to skip.
    from src.data.websocket_client import AlpacaWebSocketClient, StreamConfig
    _market_ws_client = AlpacaWebSocketClient(
        config=StreamConfig(),
        api_key=settings.alpaca.api_key,
        secret_key=settings.alpaca.secret_key,
    )

    # D212: Trade buffer for order flow analysis — accumulates WebSocket trades
    from src.data.order_flow import TradeEvent, TradeDirection
    _trade_buffers: dict[str, list[TradeEvent]] = {}
    _TRADE_BUFFER_MAX = 1000

    def _on_ws_trade(update) -> None:
        """D212: Accumulate WebSocket trades for order flow analysis."""
        try:
            sym = update.symbol
            if sym not in _trade_buffers:
                _trade_buffers[sym] = []
            _trade_buffers[sym].append(TradeEvent(
                timestamp=datetime.fromisoformat(
                    update.timestamp.replace("Z", "+00:00")
                ).timestamp() if isinstance(update.timestamp, str) else 0.0,
                price=update.price,
                size=update.size,
                conditions=update.conditions or [],
                is_iso="F" in (update.conditions or []),
                direction=TradeDirection.UNKNOWN,
            ))
            # Rolling window cap
            if len(_trade_buffers[sym]) > _TRADE_BUFFER_MAX:
                _trade_buffers[sym] = _trade_buffers[sym][-_TRADE_BUFFER_MAX:]
        except Exception as _e:
            # Hot path — never crash, but always thread the exception through
            # the logger so a sudden flood is visible at debug level.
            logger.debug("WS trade callback failed: %s", _e)

    _market_ws_client.on_trade = _on_ws_trade
    logger.info("D212: WebSocket trade buffer initialized (max %d per symbol)", _TRADE_BUFFER_MAX)

    # D212: Float rotation tracker + premarket velocity tracker
    from src.data.float_rotation import FloatRotationTracker
    _float_rotation = FloatRotationTracker()
    from src.data.premarket_velocity import PremarketVelocityTracker
    _premarket_velocity = PremarketVelocityTracker()
    logger.info("D212: Float rotation + premarket velocity trackers initialized")

    # D40: Pass options_provider to orchestrator for institutional agent data
    # D100: Pass data_client for ATR-based stop calculation
    # D115: Pass trade_tracker + position_manager for Kelly tier classification
    # Bug AO (Tier 4 #15, 2026-04-27): Pass _phase0_writer so the
    # orchestrator can emit DecisionRow on every verdict finalization,
    # building the captured corpus for backward-looking decision replay.
    orchestrator = Orchestrator(
        settings, sec_client=sec_client, options_provider=options_provider,
        data_client=client, trade_tracker=trade_tracker,
        position_manager=position_manager,
        websocket_client=_market_ws_client,
        instrumentation_writer=_phase0_writer,
    )

    # D95: LLM provider preflight — verify connectivity before market opens
    try:
        import litellm
        _preflight_model = settings.models.tier2_model
        # litellm requires provider prefix for routing
        if not _preflight_model.startswith("together_ai/"):
            _preflight_model = f"together_ai/{_preflight_model}"
        _preflight_resp = await litellm.acompletion(
            model=_preflight_model,
            messages=[{"role": "user", "content": "Respond with exactly: OK"}],
            max_tokens=5,
            timeout=15,
        )
        _preflight_text = _preflight_resp.choices[0].message.content.strip()
        logger.info("D95: LLM provider preflight PASSED (%s -> '%s')", settings.models.tier2_model, _preflight_text)
    except Exception as e:
        logger.warning(
            "D95: LLM provider preflight FAILED: %s — agents may timeout at market open. "
            "Check TOGETHER_AI_API_KEY and model availability.",
            e,
        )

    scan_loop = ScanLoop(
        settings=settings,
        gex_calculator=gex_calculator,
        options_provider=options_provider,
    )
    bridge = ExecutionBridge(
        executor=executor,
        position_manager=position_manager,
        alpaca_client=client,
        settings=settings,
        trade_tracker=trade_tracker,
        instrumentation=_phase0_writer,
        bocpd_state=_bocpd_state,
        kelly_governor=_kelly_governor,
    )
    bridge._watchlist_webhook_url = settings.ops.watchlist_webhook_url  # D218: for trade close posts
    stop_resubmitter = StopResubmitter(client=client)
    tranche_monitor = TrancheExitMonitor(
        position_manager=position_manager,
        stop_resubmitter=stop_resubmitter,  # D121 BUG-L4: For orphan stop cleanup
    )
    # D301 (2026-05-19 hotfix): TradeJournal must be initialized BEFORE
    # FillStreamBridge so the D297 kwarg `trade_journal=trade_journal`
    # has a defined name. The original construction site (~line 1004)
    # was 275 lines below the bridge ctor -- a forward reference that
    # crashed the bot at startup on 2026-05-19 (UnboundLocalError, exit
    # code 90, 0 trades that day, no Discord alerts because the crash
    # happened before any alert path could fire). The init block is
    # self-contained (import + ctor + log line) so moving it is a pure
    # textual relocation with no functional change.
    from src.analysis.trade_journal import TradeJournal
    trade_journal = TradeJournal()
    logger.info("TradeJournal initialized: %s", trade_journal.path)
    fill_bridge = FillStreamBridge(
        tranche_monitor=tranche_monitor,
        stop_resubmitter=stop_resubmitter,
        # D297 (2026-05-19): inject position_manager + trade_journal so
        # OTO stop-leg fills (which arrive via websocket but aren't
        # tracked tranches) get their journal close recorded + tracker
        # removed. Without this, every stop-fill leaves a $X journal
        # gap that D238 EOD reconciliation reports as MISSING (see
        # CISS/GCTS on 2026-05-18 with $571 total miss).
        position_manager=position_manager,
        trade_journal=trade_journal,
    )
    # ── D214: Archetype exit system (loads model if enabled) ──
    _archetype_strategy = None
    exit_config = settings.exit_intelligence
    if exit_config.archetype_exit_enabled:
        try:
            from src.execution.archetype_exit import ArchetypeExitStrategy, load_model as _load_archetype
            _arch_clf, _arch_lib = _load_archetype(exit_config.archetype_model_path)
            _archetype_strategy = ArchetypeExitStrategy(
                classifier=_arch_clf,
                library=_arch_lib,
                z_threshold=exit_config.archetype_z_exit_threshold,
                z_consecutive_bars=exit_config.archetype_z_consecutive_bars,
                target_haircut=exit_config.archetype_target_haircut,
                confidence_threshold=exit_config.archetype_confidence_threshold,
            )
            logger.info("D214: Archetype exit loaded — %d archetypes", len(_arch_lib.archetype_ids()))
        except Exception as _ae:
            logger.warning("D214: Archetype exit disabled: %s", _ae)

    # ── D78: Smart exit intelligence ──
    exit_engine = ExitSignalEngine(
        tighten_threshold=exit_config.exit_tighten_threshold,
        exit_threshold=exit_config.exit_threshold,
    )
    exit_intelligence = ExitIntelligenceManager(
        engine=exit_engine,
        half_life_table=exit_config.catalyst_half_life_table,
        null_curve=exit_config.alpha_oracle_null_curve,
        contagion_decay_minutes=exit_config.contagion_decay_minutes,
        contagion_threshold=exit_config.contagion_threshold,
        parallel_strategies_active=exit_config.parallel_strategies_active,
        parallel_exit_min_confidence=exit_config.parallel_exit_min_confidence,
        parallel_tighten_min_confidence=exit_config.parallel_tighten_min_confidence,
        parallel_min_strategies_for_exit=exit_config.parallel_min_strategies_for_exit,
        parallel_exit_excluded_strategies=exit_config.parallel_exit_excluded_strategies,
        archetype_strategy=_archetype_strategy,
    )
    # D107 WS1: Per-cycle signal history logger
    signal_logger = SignalHistoryLogger(signal_history_dir=exit_config.signal_history_dir)
    logger.info(
        "D78: Exit intelligence initialized (enabled=%s, tighten=%.2f, exit=%.2f)",
        exit_config.enabled, exit_config.exit_tighten_threshold, exit_config.exit_threshold,
    )
    logger.info("D107: Signal history logger initialized -> %s", exit_config.signal_history_dir)
    # ── D163: Software trailing stop ──
    _trail_cfg = settings.trailing_stop
    trailing_manager = TrailingStopManager(config=_trail_cfg)
    # doc 188: opening-range continuation tracker — the validated continue-vs-pump
    # edge (doc 187 RANK 1+2: opening RVOL + opening-range break + VWAP). Populated
    # from minute bars at fetch time; queried at the gates to LOG the signal (observe
    # on our low-float universe before the gate-action is flipped on — doc 187 caveat).
    from src.analysis.opening_range import OpeningRangeTracker as _ORTracker
    _or_tracker = _ORTracker()
    logger.info(
        "D163: Software trailing stop initialized (enabled=%s, activation=+%.0f%%, "
        "trail=%.0f%% of gain, min_dist=%.0f%%, max_dist=%.0f%%)",
        _trail_cfg.enabled,
        _trail_cfg.activation_threshold_pct * 100,
        _trail_cfg.trail_pct_of_gain * 100,
        _trail_cfg.min_trail_distance_pct * 100,
        _trail_cfg.max_trail_distance_pct * 100,
    )
    # ── D164: Time-based early profit take ──
    _d164_cfg = settings.early_profit
    early_profit_taker = EarlyProfitTaker(config=_d164_cfg)
    # ── D165: Price-based tranche profit-taking fallback ──
    _d165_cfg = D165Config(
        enabled=getattr(settings.execution, "d165_tranche_enabled", True),
        target_pcts=[
            getattr(settings.execution, "tranche_t1_pct", 0.03),
            getattr(settings.execution, "tranche_t2_pct", 0.06),
            getattr(settings.execution, "tranche_t3_pct", 0.10),
        ],
        adjust_stop_after_partial=getattr(
            settings.execution, "d165_adjust_stop_after_partial", True
        ),
    )
    tranche_taker = D165TrancheTaker(config=_d165_cfg)
    # ── D170: Entry delay observation window ──
    entry_delay_mgr = EntryDelayManager(settings.observation)
    _d170_pending_verdicts: dict = {}  # ticker -> (verdict, scored)
    logger.info(
        "D170: Entry delay observation window initialized "
        "(enabled=%s, window=%.0f–%.0fmin)",
        settings.observation.enabled,
        settings.observation.min_observation_minutes,
        settings.observation.observation_minutes,
    )
    logger.info(
        "D164: Early profit take initialized (enabled=%s, delay=%.0fs, "
        "exit=%.0f%%, min_profit=%.1f%%, window=T+%.0f–%.0fs)",
        _d164_cfg.enabled,
        _d164_cfg.delay_seconds,
        _d164_cfg.exit_pct * 100,
        _d164_cfg.min_profit_pct * 100,
        _d164_cfg.delay_seconds,
        _d164_cfg.max_delay_seconds,
    )
    # ── D196: Bar Recorder (minute-bar price paths for arena replay) ──
    from src.data.bar_recorder import BarRecorder
    bar_recorder = BarRecorder(storage_dir="data/bar_recordings")
    _d196_session_tickers: set[str] = set()  # tickers evaluated this session
    logger.info("D196: Bar recorder initialized -> data/bar_recordings")
    # ── D210: Session Data Collector (comprehensive per-ticker data lake) ──
    from src.data.session_data_collector import SessionDataCollector
    session_collector = SessionDataCollector()
    logger.info("D210: SessionDataCollector initialized")
    # D211: Phantom Portfolio — records every BUY verdict for counterfactual analysis
    from src.data.phantom_journal import PhantomJournal
    _phantom = PhantomJournal("data/phantom")
    logger.info("D211: PhantomJournal initialized")
    # D216: Earnings calendar — identifies earnings-day gap-ups without headlines
    from src.data.earnings_calendar import EarningsCalendar
    _earnings_cal = EarningsCalendar()
    logger.info("D216: EarningsCalendar initialized")
    # D215: ExecutionRecorder initialized after trade_journal (line ~821)
    # D107 WS4: External heartbeat webhook
    _d107_watchdog = HeartbeatWatchdog(
        timeout_minutes=30,
        webhook_url=settings.heartbeat_webhook_url,
    )
    if settings.heartbeat_webhook_url:
        logger.info("D107: Heartbeat webhook -> %s (every ~5 pulses)", settings.heartbeat_webhook_url)
    _d107_watchdog.start()

    # ── D64: Enhanced restart recovery — session state + Alpaca API ──
    from src.execution.session_state import SessionStateManager
    state_mgr = SessionStateManager()
    session_state = None  # D91: Ensure variable is always defined for overnight detection
    try:
        session_state = state_mgr.load()
        alpaca_positions = await client.get_positions()

        if session_state is not None:
            # Enhanced recovery: merge state file with Alpaca data
            alpaca_orders = await client.get_orders(status="open")
            synced = position_manager.sync_from_state_and_orders(
                session_state=session_state,
                alpaca_positions=alpaca_positions,
                alpaca_orders=alpaca_orders,
            )

            # Build order lookup for matching
            order_by_id = {o.get("id", ""): o for o in alpaca_orders}
            tranche_count = 0
            stop_count = 0

            # Restore TrancheExitMonitor from state + active orders
            for ticker, pos_state in session_state.positions.items():
                if not position_manager.has_position(ticker):
                    continue  # Alpaca no longer has this position
                for idx, oid in enumerate(pos_state.tranche_order_ids):
                    if oid in order_by_id:
                        order = order_by_id[oid]
                        tranche_monitor.register_tranche_order(
                            order_id=oid,
                            ticker=ticker,
                            tranche_number=idx + 1,
                            target_price=float(order.get("limit_price", 0)),
                            qty=int(float(order.get("qty", 0))),
                        )
                        tranche_count += 1

                # Restore StopResubmitter from state + active orders
                if pos_state.stop_order_id and pos_state.stop_order_id in order_by_id:
                    order = order_by_id[pos_state.stop_order_id]
                    stop_resubmitter.register_stop(
                        ticker=ticker,
                        order_id=pos_state.stop_order_id,
                        stop_price=float(order.get("stop_price", pos_state.stop_loss)),
                        qty=int(float(order.get("qty", 0))),
                    )
                    stop_count += 1

            logger.info(
                "D64: Enhanced recovery — %d positions, %d tranches, %d stops",
                synced, tranche_count, stop_count,
            )
        else:
            # Fallback: D56 basic recovery (no state file).
            # Bug AM (2026-04-27): pass alpaca_orders so sync_from_broker
            # can auto-attach existing broker stops to the tracker
            # (avoids the LIDR scenario where the tracker shows COMPUTED
            # DEFAULT stop but the broker has the actual stop).
            try:
                _bug_am_alpaca_orders = await client.get_orders(status="open", limit=200)
            except Exception as _bug_am_e:
                logger.warning(
                    "D56 (Bug AM): get_orders failed (%s) — proceeding without "
                    "stop auto-attach", _bug_am_e,
                )
                _bug_am_alpaca_orders = []
            synced = position_manager.sync_from_broker(
                alpaca_positions, broker_orders=_bug_am_alpaca_orders,
            )
            if synced > 0:
                logger.info(
                    "D64: Basic recovery (no state file) — %d positions from broker",
                    synced,
                )
    except Exception as e:
        logger.warning("D64: Recovery failed — starting fresh: %s", e)

    # D121 BUG-L3: Restore tranche monitor state from recovered positions.
    # Without this, post-recovery tranche fills reset tranches_filled to 1.
    try:
        tranche_monitor.restore_tranche_state()
    except Exception as _rts_e:
        logger.warning("Tranche state restore failed: %s", _rts_e)

    # ── D108 WS2: Post-recovery connectivity probe ──
    # Verify broker link is alive after recovery before entering trading loops.
    try:
        _d108_acct = await client.get_account()
        logger.info("D108: Post-recovery connectivity OK (equity=$%.2f)",
                     float(_d108_acct.get("equity", 0)))
    except Exception as _d108_probe_e:
        logger.critical("D108: Post-recovery connectivity FAILED: %s — retrying in 5s...", _d108_probe_e)
        await asyncio.sleep(5)
        try:
            _d108_acct = await client.get_account()
            logger.info("D108: Connectivity restored on retry (equity=$%.2f)",
                         float(_d108_acct.get("equity", 0)))
        except Exception as _d108_retry_e:
            logger.critical("D108: Connectivity still failing: %s — continuing anyway", _d108_retry_e)

    # ── D107 WS2: Orphaned order reconciliation ──
    # Cancel orders for symbols not tracked by position manager.
    # Runs once at startup, non-fatal — better to trade with orphans than crash.
    try:
        _d107_all_orders = await client.get_orders(status="open")
        _d107_orphans = position_manager.identify_orphaned_orders(_d107_all_orders)
        for _d107_orphan in _d107_orphans:
            try:
                _d107_oid = _d107_orphan.get("id", "")
                if _d107_oid:
                    await client.cancel_order(_d107_oid)
                    logger.info("D107: Cancelled orphaned order %s (%s)",
                                _d107_oid, _d107_orphan.get("symbol", "?"))
            except Exception as _d107_ce:
                logger.warning("D107: Failed to cancel orphan %s: %s",
                               _d107_orphan.get("id", "?"), _d107_ce)
        if _d107_orphans:
            logger.info("D107: Cancelled %d orphaned orders", len(_d107_orphans))
    except Exception as _d107_e:
        logger.warning("D107: Orphan reconciliation failed (non-fatal): %s", _d107_e)

    # D55: Increase portfolio heat limit for competition mode (8 positions × 15% = 120% max)
    # Heat = sum of stop distances, not positions. With 4% stops, 8 positions = 32% heat.
    portfolio_risk = PortfolioRiskManager(
        max_sector_positions=settings.ops.max_sector_positions,
        max_portfolio_heat_pct=settings.ops.max_portfolio_heat_pct,
    )

    # ── Initialize News Client (Phase 2 data: 30% MFCS weight) ──
    import os
    news_client = NewsClient(
        alpaca_api_key=settings.alpaca.api_key,
        alpaca_secret_key=settings.alpaca.secret_key,
        finnhub_api_key=os.environ.get("FINNHUB_API_KEY", ""),
    )
    logger.info(
        "NewsClient initialized: Alpaca=%s, Finnhub=%s",
        "enabled" if settings.alpaca.api_key else "disabled",
        "enabled" if os.environ.get("FINNHUB_API_KEY") else "disabled",
    )

    # ── D193: Sentiment Velocity Tracker (narrative momentum pipeline) ──
    from src.data.sentiment_velocity import SentimentVelocityTracker
    sentiment_tracker = SentimentVelocityTracker()
    logger.info("D193: Sentiment velocity tracker initialized")

    # ── D194: Order Flow Analyzer (build order flow analysis) ──
    from src.data.order_flow import OrderFlowAnalyzer
    order_flow_analyzer = OrderFlowAnalyzer()
    logger.info("D194: Order flow analyzer initialized")

    # D301 (2026-05-19): trade_journal initialization moved to ~line 715
    # (before FillStreamBridge). The original location here caused a
    # forward-reference UnboundLocalError -- see D301 comment block above.

    # D215: Unified execution recorder — ALL 7 entry paths must record through this
    from src.analysis.execution_recorder import ExecutionRecorder
    _exec_recorder = ExecutionRecorder(
        trade_journal=trade_journal,
        session_collector=session_collector,
        phantom_journal=_phantom,
    )
    logger.info("D215: ExecutionRecorder initialized — all paths will record")

    # ── Initialize Pre-Market Research Engine (Phase 0) ──
    from src.data.premarket_research import PreMarketResearch, PreMarketCache
    premarket_research = PreMarketResearch(
        news_client=news_client,
        sec_client=sec_client,
        alpaca_client=client,
    )
    premarket_cache: PreMarketCache | None = None
    # D95: Restore phase flags from session state (survives crash/restart)
    phase0_completed: bool = False
    eod_close_completed: bool = False
    if session_state is not None:
        if session_state.phase0_completed:
            phase0_completed = True
            logger.info("D95: Restored phase0_completed=True from session state")
        if session_state.eod_close_completed:
            eod_close_completed = True
            logger.info("D95: Restored eod_close_completed=True from session state — preventing re-fire")
    # D90: If starting before Phase 1 (hour < 4 AM ET), mark Phase 4 as
    # already completed so we don't accidentally run EOD close at 3:30 AM.
    # Phase 1 resets phase4_completed to False when trading day begins (line 632).
    from zoneinfo import ZoneInfo as _ZI
    _startup_hour_et = datetime.now(timezone.utc).astimezone(_ZI("America/New_York")).hour
    phase4_completed: bool = _startup_hour_et < 4  # D71+D90: pre-market start guard
    _phase3_banner_shown: bool = False  # D103: Show phase 3 entry banner once
    market_is_open: bool | None = None  # D79: Cached market-open status (checked once per loop)
    logger.info("PreMarketResearch engine initialized: %d universe tickers",
                len(premarket_research._universe))

    # ── BUG-005: Market data WebSocket — launched lazily after Phase 1 ──
    # The AlpacaWebSocketClient needs symbols upfront to subscribe. We start
    # the connection after the first watchlist scan provides ticker symbols.
    _market_ws_task: asyncio.Task | None = None
    _market_ws_launched: bool = False

    # ── Launch WebSocket fill stream as background task (ADR-023, D2) ──
    from src.data.websocket_client import TradeUpdatesStream
    trade_stream = TradeUpdatesStream(
        api_key=settings.alpaca.api_key,
        secret_key=settings.alpaca.secret_key,
        paper=(settings.mode == "paper"),
    )
    trade_stream.on_trade_update = fill_bridge.on_trade_update
    fill_stream_task: asyncio.Task | None = None
    try:
        fill_stream_task = asyncio.create_task(trade_stream.connect())
        logger.info("WebSocket fill stream launched as background task")
    except Exception as e:
        logger.warning("WebSocket fill stream failed to launch: %s — falling back to polling", e)

    # ── D107 WS3: System reliability score tracking ──
    from src.scheduling.health_server import TradingControlState, HealthControlServer
    _d107_control_state = TradingControlState()
    logger.info("D107: Reliability tracking initialized")

    # ── D217: Wire Health/Control HTTP Server ──
    # Provides /health, /status, /pause, /resume, /shutdown endpoints.
    # External watchdog checks /health to detect hangs.
    _d217_health_server = None
    try:
        _d217_health_server = HealthControlServer(
            control_state=_d107_control_state,
            heartbeat=_d107_watchdog,
            port=settings.server.health_port,
            auth_token=os.environ.get("MOMENTUM_HEALTH_TOKEN"),
        )
        _d217_health_server.start()
        logger.info("D217: Health/control server started on http://0.0.0.0:%d", settings.server.health_port)
    except Exception as _d217_hs_e:
        logger.warning("D217: Health/control server failed to start: %s", _d217_hs_e)

    # Track candidates and their ScoredCandidates for execution
    watchlist: list = []  # CandidateStock from Phase 1
    session_trades: int = 0
    phase3_cycle_count: int = 0  # D42: Track Phase 3 cycles for periodic re-scanning

    # D160: High-RVOL mandatory re-evaluation tracking.
    # Stocks with RVOL > 100x that receive NO_TRADE must be re-evaluated at least
    # high_rvol_min_evals times before rejection sticks. A single LLM pass on a
    # 829x RVOL stock (BFRG) is too fragile — LLM sampling variance can kill a
    # genuine runner on the first pass.
    _rvol_eval_counts: dict[str, int] = {}          # ticker → evaluation cycle count
    _forced_reeval_candidates: list = []            # CandidateStock objects pending re-eval

    # D160: Faller Risk Detector — initialized once, used per BUY verdict.
    from src.execution.faller_detection import FallerRiskDetector
    _faller_detector = FallerRiskDetector(settings.faller)

    # D198: Live Session Regime Detector — cascade loss protection + momentum boost.
    from src.execution.session_regime import SessionRegimeDetector
    session_regime = SessionRegimeDetector()
    session_regime.initialize_session(starting_equity=equity, vix_at_open=0.0)
    logger.info("D198: Session regime detector initialized (equity=$%.2f)", equity)

    # D94b: Ticker cooldown after stop-out — prevent re-entry same day.
    # March 4 post-mortem: CANF stopped at $7.01, re-bought at $6.84, stopped again at $6.15.
    # D95: Now persisted in session_state.json for crash recovery.
    _stopped_out_tickers: set[str] = set()
    if session_state is not None:
        _stopped_out_tickers = state_mgr.get_stopped_out_tickers()
        if _stopped_out_tickers:
            logger.info(
                "D95: Restored %d stopped-out tickers from session state: %s",
                len(_stopped_out_tickers), _stopped_out_tickers,
            )

    # doc-285 gap #2: D98 absence-confirmation streaks. ticker -> count of
    # CONSECUTIVE successful get_positions() snapshots showing the ticker
    # absent. Reset on any fetch failure or reappearance; a stop-out books
    # only at >= 2 (or immediately on stop-order fill confirmation).
    _d98_absent_counts: dict[str, int] = {}

    # Doc 82 Phase 2 ship: build the PostFillContext singleton bundle once.
    # All entry call sites (PHASE2_BUY, RESCAN, VWAP_BREAKOUT) pass this
    # to post_fill_bookkeeping(). Mutable references (sets/dicts) are
    # shared by-reference so cleanup mutations from BAR-1 EXIT propagate
    # back to main()'s tracking state.
    _post_fill_ctx = PostFillContext(
        settings=settings,
        bridge=bridge,
        client=client,
        state_mgr=state_mgr,
        trade_journal=trade_journal,
        stop_resubmitter=stop_resubmitter,
        tranche_monitor=tranche_monitor,
        exec_recorder=_exec_recorder,
        orchestrator=orchestrator,
        trailing_manager=trailing_manager,
        early_profit_taker=early_profit_taker,
        tranche_taker=tranche_taker,
        entry_delay_mgr=entry_delay_mgr,
        d170_pending_verdicts=_d170_pending_verdicts,
        stopped_out_tickers=_stopped_out_tickers,
    )

    # ── D85: Fast-path entry state ──
    from src.execution.fast_path import (
        FastPathScorer, FastPathExecutor, FastPathReconciler,
        FastPathEntry, get_fast_path_tickers,
    )
    fast_path_scored: bool = False
    fast_path_fired: bool = False
    fast_path_queue: list[FastPathEntry] = []

    fp_scorer = FastPathScorer(
        settings=settings,
        news_agent=orchestrator._news_agent,
        fundamental_agent=orchestrator._fundamental_agent,
        deep_search_agent=orchestrator._deep_search_agent,
        technical_agent=orchestrator._technical_agent,   # D106: 5-agent scoring
        risk_agent=orchestrator._risk_agent,              # D106: 5-agent scoring
        threshold=settings.fast_path.fast_path_threshold,
        max_entries=settings.fast_path.max_fast_path_entries,
    )
    fp_executor = FastPathExecutor(
        settings=settings,
        alpaca_client=client,
        position_manager=position_manager,
        portfolio_risk=portfolio_risk,
    )
    fp_reconciler = FastPathReconciler(
        settings=settings,
        alpaca_client=client,
        position_manager=position_manager,
    )
    logger.info(
        "D85: Fast-path initialized (enabled=%s, threshold=%.2f, max=%d, sizing=%.1f%%)",
        settings.fast_path.enabled,
        settings.fast_path.fast_path_threshold,
        settings.fast_path.max_fast_path_entries,
        settings.fast_path.position_size_pct * 100,
    )

    # ── Start Metrics HTTP Server (ADR-019) ──
    reset_metrics()
    metrics_server = MetricsServer(port=settings.server.metrics_port)
    try:
        metrics_server.start()
        logger.info("Metrics server: http://0.0.0.0:%d/metrics", settings.server.metrics_port)
    except Exception as _metrics_err:
        logger.warning(
            "Metrics server failed to start on port %d: %s — continuing without metrics",
            settings.server.metrics_port, _metrics_err,
        )
        metrics_server = None

    # Graceful shutdown handler
    shutdown = asyncio.Event()

    def handle_signal(sig, frame):
        logger.info("Shutdown signal received. Closing positions...")
        if metrics_server:
            metrics_server.stop()
        shutdown.set()

    signal.signal(signal.SIGINT, handle_signal)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, handle_signal)

    # D97: Write Python PID to lock file for stale-process detection.
    # The PS1 wrapper stores its own PID (not Python's), so stale Python
    # processes aren't detected by the lock check. Writing the actual
    # Python PID enables the PS1 stale cleanup to find and kill us.
    _lock_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "logs", "momentum-x.lock",
    )
    os.makedirs(os.path.dirname(_lock_path), exist_ok=True)
    try:
        with open(_lock_path, "w") as _lf:
            _lf.write(str(os.getpid()))
        logger.info("D97: Wrote Python PID %d to %s", os.getpid(), _lock_path)
    except Exception as _e:
        logger.warning("D97: Failed to write PID lock file: %s", _e)

    # ── Start Live Dashboard (D67) ──
    from src.monitoring.live_dashboard import LiveDashboard
    dashboard = LiveDashboard(
        interval_seconds=settings.ops.dashboard_interval_seconds,
        enabled=True,
        position_manager=position_manager,
    )
    dashboard_task = asyncio.create_task(dashboard.run(shutdown))
    logger.info("Live dashboard started (%ds interval)", settings.ops.dashboard_interval_seconds)

    logger.info("Paper trading loop started. Press Ctrl+C to stop.")

    # D302 (2026-05-19 hotfix): compute git commit/branch HERE rather
    # than at line 1583, because alert_session_start below references
    # _d217_hb_commit as a kwarg and a forward-reference would silently
    # fail (the try/except: pass below would swallow UnboundLocalError,
    # making the Discord session-start ping a permanent no-op).
    # Detected by D301 forward-reference guard test.
    _d217_hb_repo = os.path.dirname(os.path.abspath(__file__))
    _d217_hb_path = os.path.join(_d217_hb_repo, "data", "heartbeat.json")
    import subprocess as _d217_hb_sp
    try:
        _d217_hb_commit = _d217_hb_sp.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_d217_hb_repo, stderr=_d217_hb_sp.DEVNULL,
        ).decode().strip()[:8]
        _d217_hb_branch = _d217_hb_sp.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=_d217_hb_repo, stderr=_d217_hb_sp.DEVNULL,
        ).decode().strip()
    except Exception:
        _d217_hb_commit = "unknown"
        _d217_hb_branch = "unknown"

    # D218: Session start alert
    try:
        from src.monitoring.alerts import alert_session_start
        asyncio.ensure_future(alert_session_start(
            equity=float(account.get("equity", 0)),
            commit=_d217_hb_commit,
            webhook_url=settings.ops.alert_webhook_url,
        ))
    except Exception:
        pass

    # ── D86 + Wed 2026-04-22 Bug A fix: filtered startup cleanup ──
    # Orders from Day N-1 EOD close may still be pending (e.g. market sell
    # submitted at 15:55 but not filled before close). These stale orders
    # can fill on Day N open and create ghost positions.
    #
    # The original `cancel_all_orders()` was a shotgun: it removed every
    # open order at the broker without considering whether each order was
    # a legitimate protective GTC stop on a position we currently hold.
    # On Wed 2026-04-22 04:30:29 ET that nuked the GTC stop I'd placed
    # the previous evening on ELSE ($6.50, order 6490714a), leaving the
    # position naked at the broker for ~2 hours pre-market while the
    # dashboard showed a phantom "$7.23 stop" derived from
    # entry_price × 0.945 settings default.
    #
    # The filter (src/execution/startup_order_cleanup.py): preserve a
    # GTC protective stop iff broker still holds a same-symbol position
    # with qty >= order.qty. Cancel everything else (DAY orders that
    # would expire anyway, orphaned GTC stops on closed-out symbols,
    # oversized stops that can't be coherent, non-stop limit orders).
    try:
        from src.execution.startup_order_cleanup import select_orders_to_cancel
        _open_orders = await client.get_orders(status="open", limit=500)
        _broker_positions_for_cleanup = await client.get_positions()
        _cleanup_plan = select_orders_to_cancel(
            _open_orders, _broker_positions_for_cleanup,
        )
        for _stale_oid in _cleanup_plan.cancel_ids:
            try:
                await client.cancel_order(_stale_oid)
            except Exception as _co_e:
                logger.warning(
                    "D86: cancel of order %s failed (non-fatal): %s",
                    _stale_oid, _co_e,
                )
        logger.info(
            "D86: Cancelled %d stale orders, PRESERVED %d protective "
            "GTC stops on held positions",
            _cleanup_plan.n_cancel, _cleanup_plan.n_preserved,
        )
        for _pid, _psym, _preason in _cleanup_plan.preserved:
            logger.info(
                "D86: PRESERVED order %s (%s) — %s",
                _pid, _psym, _preason,
            )
    except Exception as e:
        logger.warning("D86: Startup order cleanup failed: %s", e)

    # D86: Detect ghost positions (broker has positions our tracker doesn't know about).
    # These are typically shorts created by stale sell orders filling after EOD close.
    # D91: Also detect OVERNIGHT positions — tracked positions from a previous
    # session that should have been closed by D76 EOD close but weren't.
    _ghost_positions_to_close: list[str] = []
    _overnight_positions_to_close: list[str] = []
    _ghost_cleanup_done = False
    _overnight_cleanup_done = False
    try:
        _startup_broker_pos = await client.get_positions()
        _tracked_by_sym = {p.ticker: p for p in position_manager.open_positions}
        _tracked_tickers = set(_tracked_by_sym.keys())

        # Bug E fix (2026-04-22): D91 detection now uses opened_at vs
        # today's 04:00 ET pre-market boundary instead of `session_state
        # is None`. After Bug B's Phase-3 restart preserves session_state,
        # the old gate never fired and ELSE silently rode through 09:30.
        from zoneinfo import ZoneInfo as _D91_ZI
        from src.execution.bridge import _is_overnight_position as _d91_is_overnight
        _d91_now_et = datetime.now(timezone.utc).astimezone(_D91_ZI("America/New_York"))

        for bp in _startup_broker_pos:
            sym = bp.get("symbol", "")
            side = bp.get("side", "")
            qty = bp.get("qty", "0")
            if sym not in _tracked_tickers:
                logger.warning(
                    "D86: GHOST position detected at broker: %s %s qty=%s "
                    "(not in our tracker). Will close at market open.",
                    sym, side, qty,
                )
                _ghost_positions_to_close.append(sym)
                continue

            # Bug E: detect overnight via opened_at, NOT session_state
            _tracked_pos = _tracked_by_sym.get(sym)
            _opened_at = getattr(_tracked_pos, "opened_at", None)
            if _opened_at is None:
                # Defensive — shouldn't happen, but if it does, prefer
                # safety: treat as overnight so it gets closed at open.
                logger.warning(
                    "D91: %s tracked position has no opened_at — treating as "
                    "overnight (safety default)", sym,
                )
                _overnight_positions_to_close.append(sym)
                if _tracked_pos is not None:
                    _tracked_pos.close_pending = True
                continue

            if _d91_is_overnight(opened_at=_opened_at, now_et=_d91_now_et):
                _overnight_positions_to_close.append(sym)
                # Tag close_pending=True so the eval queue / scorer
                # excludes this position from re-evaluation between now
                # and the 09:30 close routine.
                _tracked_pos.close_pending = True
                logger.info(
                    "D91: %s tagged close_pending=True (opened_at=%s; today's "
                    "premarket-open 04:00 ET = %s)",
                    sym, _opened_at.isoformat(),
                    _d91_now_et.replace(hour=4, minute=0, second=0, microsecond=0).isoformat(),
                )

        if _startup_broker_pos:
            logger.info(
                "D86: Broker has %d positions at startup, tracker has %d, "
                "ghosts=%d, overnight=%d",
                len(_startup_broker_pos), len(_tracked_tickers),
                len(_ghost_positions_to_close), len(_overnight_positions_to_close),
            )
        if _overnight_positions_to_close:
            logger.warning(
                "D91: %d OVERNIGHT positions detected (opened before today 04:00 ET): %s. "
                "Will close at market open via _close_overnight_position.",
                len(_overnight_positions_to_close), _overnight_positions_to_close,
            )

            # Bug AJ (2026-04-27, Tier 1 #2): MOMENTUM_HOLD_OVERNIGHT env
            # override lets the operator specify positions to KEEP across
            # sessions instead of auto-closing at market open. Comma-
            # separated tickers, case-insensitive. Surfaced in the morning-
            # resolution Discord alert below so the operator knows the
            # escape hatch exists.
            _hold_set = {
                t.strip().upper() for t in os.environ.get("MOMENTUM_HOLD_OVERNIGHT", "").split(",")
                if t.strip()
            }
            if _hold_set:
                _filtered_close = [t for t in _overnight_positions_to_close if t.upper() not in _hold_set]
                _filtered_hold = [t for t in _overnight_positions_to_close if t.upper() in _hold_set]
                if _filtered_hold:
                    logger.warning(
                        "D91 (Bug AJ): MOMENTUM_HOLD_OVERNIGHT override — "
                        "KEEPING %d position(s) that would otherwise auto-close: %s",
                        len(_filtered_hold), _filtered_hold,
                    )
                _overnight_positions_to_close = _filtered_close

            # Bug AJ (2026-04-27, Tier 1 #2): post a CRITICAL-level Discord
            # alert at startup so the operator sees the morning-resolution
            # surface instead of having to grep logs. Includes per-position
            # P&L, days held, and the env-override escape hatch. Sync because
            # we're in the synchronous startup path before the asyncio loop
            # is running.
            try:
                from src.monitoring.alerts import alert_morning_resolution_sync
                from datetime import datetime as _dt_aj, timezone as _tz_aj
                _aj_now_utc = _dt_aj.now(_tz_aj.utc)
                _aj_alert_payload = []
                for _aj_sym in _overnight_positions_to_close + (_filtered_hold if _hold_set else []):
                    _aj_pos = next(
                        (p for p in position_manager.open_positions if p.ticker == _aj_sym),
                        None,
                    )
                    _aj_broker = next(
                        (b for b in (_startup_broker_pos or []) if (b.get("symbol") or "").upper() == _aj_sym.upper()),
                        None,
                    )
                    if _aj_pos is None and _aj_broker is None:
                        continue
                    _aj_qty = int(_aj_pos.qty) if _aj_pos else int(float(_aj_broker.get("qty", 0)))
                    _aj_entry = float(_aj_pos.entry_price) if _aj_pos else float(_aj_broker.get("avg_entry_price", 0) or 0)
                    _aj_cur = float(_aj_broker.get("current_price", 0) or 0) if _aj_broker else _aj_entry
                    _aj_pnl_d = float(_aj_broker.get("unrealized_pl", 0) or 0) if _aj_broker else 0.0
                    _aj_pnl_p = (
                        ((_aj_cur - _aj_entry) / _aj_entry * 100.0) if _aj_entry > 0 else 0.0
                    )
                    _aj_days = 1
                    try:
                        _aj_opened = getattr(_aj_pos, "opened_at", None) if _aj_pos else None
                        if _aj_opened is not None:
                            _aj_days = max(1, (_aj_now_utc - _aj_opened).days)
                    except Exception:  # noqa: silent-handler — defensive: opened_at can be naive datetime, missing tz, etc; days_held=1 is a safe fallback
                        pass
                    _aj_alert_payload.append({
                        "ticker": _aj_sym,
                        "qty": _aj_qty,
                        "entry": _aj_entry,
                        "current": _aj_cur,
                        "pnl_dollar": _aj_pnl_d,
                        "pnl_pct": _aj_pnl_p,
                        "days_held": _aj_days,
                    })
                if _aj_alert_payload:
                    alert_morning_resolution_sync(
                        overnight_positions=_aj_alert_payload,
                        webhook_url=os.environ.get("OPS_ALERT_WEBHOOK_URL"),
                    )
                    logger.info(
                        "D91 (Bug AJ): morning-resolution Discord alert posted "
                        "for %d position(s).", len(_aj_alert_payload),
                    )
            except Exception as _aj_e:
                logger.warning(
                    "D91 (Bug AJ): morning-resolution alert post failed (non-fatal): %s",
                    _aj_e,
                )
    except Exception as e:
        logger.warning("D86: Ghost position check failed: %s", e)

    # D240 — pre-open broker-state ghost detection (Track A item 26 / 2026-04-24).
    # Stronger version of D86: compare positions + working orders + active stops
    # against internal tracker. D86 only catches ghost POSITIONS; D240 catches
    # ghost ORDERS too (any working order at the broker that the bridge does
    # not have an internal record of).
    try:
        _d240_broker_orders = await client.get_orders(status="open", limit=100)
        _d240_internal_oids: set[str] = set()
        for _ipos in position_manager.open_positions:
            for _attr in ("order_id", "stop_order_id"):
                _v = getattr(_ipos, _attr, "") or ""
                if _v:
                    _d240_internal_oids.add(_v)
            for _toid in getattr(_ipos, "tranche_order_ids", []) or []:
                if _toid:
                    _d240_internal_oids.add(_toid)
        _d240_ghost_orders = [
            o for o in (_d240_broker_orders or [])
            if o.get("id") and o.get("id") not in _d240_internal_oids
        ]
        if _d240_ghost_orders:
            logger.warning(
                "D240 PREOPEN_GHOST_DETECTED: %d working order(s) at broker "
                "without internal tracking — manual investigation required "
                "before Phase 0:\n%s",
                len(_d240_ghost_orders),
                "\n".join(
                    f"    {o.get('symbol')} {o.get('side')} {o.get('qty')} "
                    f"status={o.get('status')} type={o.get('type')} "
                    f"id={(o.get('id') or '')[:8]}"
                    for o in _d240_ghost_orders
                ),
            )
        else:
            logger.info(
                "D240: pre-open broker-state clean (%d working orders, all "
                "internally tracked)",
                len(_d240_broker_orders or []),
            )
    except Exception as _d240_e:
        logger.warning("D240: pre-open broker-state check failed (non-fatal): %s", _d240_e)

    # ── Bug AG fix (2026-04-27): _d217_hb_repo was previously defined only
    # at line ~1382 inside the heartbeat-keeper block, but the Track B
    # daemon launch below references it for `status_file=`. On Monday
    # 2026-04-27 the live system logged:
    #   ERROR | Track B daemon failed to launch (non-fatal): cannot access
    #   local variable '_d217_hb_repo' where it is not associated with a value
    # …meaning the continuous 30s broker reconciliation daemon never armed
    # for the entire session — same Python `UnboundLocalError` class as
    # Bug AF (`_cand` in bridge.execute_verdict). Define the path here,
    # before any consumer references it. The heartbeat block below
    # references the same variable; the assignment there is idempotent
    # so we leave it as-is (clear self-documentation that the heartbeat
    # block owns its working state). See docs/research-log/44_bug_ag_*.md.
    _d217_hb_repo = os.path.dirname(os.path.abspath(__file__))

    # ── Track B reconciliation daemon — items 4-8 (2026-04-24) ──
    # Continuous 30s broker-vs-internal reconciliation in shadow mode.
    # Three escalation tiers (D230 warn / D231 hard-block / D232 lethal)
    # but shadow_mode=True means tier decisions log only — no enforcement
    # until at least N=5 sessions of clean shadow operation per item 8's
    # transition criteria.
    #
    # The status file (data/recon_status.json) is the independent
    # observability surface per playbook §3.5 — external dashboards /
    # Discord can read state without the trading loop being involved.
    try:
        from src.monitoring.recon_daemon import ReconDaemon, ReconState
        _recon_state = ReconState()
        _recon_daemon = ReconDaemon(
            client=client,
            position_manager=position_manager,
            shadow_mode=True,                         # Item 5: NON-NEGOTIABLE
            tick_interval_s=30.0,
            lethal_qty_drift_seconds=60.0,            # Item 19 conservative
            lethal_equity_drift_pct=0.05,             # 5%
            status_file=os.path.join(_d217_hb_repo, "data", "recon_status.json"),
        )
        _recon_task = asyncio.create_task(
            _recon_daemon.run_forever(),
            name="track_b_recon",
        )
        logger.info(
            "[SHADOW] ReconDaemon armed, shadow_mode=True (tick=30s, "
            "lethal_qty>60s, lethal_equity>5%%, status="
            "data/recon_status.json). Item 7 pre-open verification: "
            "this log line confirms the daemon launched."
        )
    except Exception as _td_e:
        logger.error(
            "Track B daemon failed to launch (non-fatal): %s. "
            "EOD recon (Track A item 3) is the fallback.",
            _td_e,
        )
        _recon_state = None  # noqa
        _recon_daemon = None  # noqa
        _recon_task = None  # noqa

    # D313 (2026-05-24, doc 171) — Layer 2 hedge integrity watcher.
    # Independent of T2 (always on). Polls broker positions + open
    # orders every 20s, enforces qty != 0 -> has_protective_order.
    # Auto-submits emergency stop after 60s tolerance. Would have
    # caught NXXT's 66h unhedge in 30-60 seconds.
    try:
        from src.monitoring.hedge_integrity_watcher import HedgeIntegrityWatcher
        _hedge_watcher = HedgeIntegrityWatcher(
            client=client,
            position_manager=position_manager,  # D313.v4 (2026-05-27): writeback emergency-stop oid
            poll_interval_sec=20.0,
            tolerance_sec=60.0,
            alert_webhook_url=settings.ops.alert_webhook_url,
            auto_submit_emergency=True,
            skip_below_price=1.0,  # sub-$1 names: alert only, no auto-stop
        )
        _hedge_watcher_task = asyncio.create_task(
            _hedge_watcher.run_forever(),
            name="d313_hedge_integrity_watcher",
        )
        logger.info(
            "D313 HEDGE_WATCHER launched: interval=20s tolerance=60s "
            "auto_submit=True skip_below=$1.00 -- catches D310/D312 "
            "and any future unhedge condition within 60s."
        )
    except Exception as _hw_e:
        logger.error(
            "D313 HEDGE_WATCHER failed to launch (non-fatal but "
            "REMOVES T2 SAFETY NET): %s",
            _hw_e,
        )
        _hedge_watcher = None  # noqa
        _hedge_watcher_task = None  # noqa

    # D313.v2 (2026-05-24, doc 173) -- watcher-of-watchers liveness check.
    # If HedgeIntegrityWatcher crashes silently mid-session OR never
    # produces a heartbeat (init succeeded but loop never ticked), the
    # bot was about to revert to ghost-TrailingStopManager land WITHOUT
    # any alert. This watchdog polls L2's heartbeat_age_sec every 60s
    # and pages the operator if >90s stale. The Pierce principle:
    # "watchers without watchers are unreliable by default."
    async def _l2_watchdog_loop():
        from src.monitoring.alerts import alert_critical
        _l2_warned = False
        _l2_recovered_once = False
        while not shutdown.is_set():
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=60.0)
                return  # shutdown event
            except asyncio.TimeoutError:
                pass
            if _hedge_watcher is None or _hedge_watcher_task is None:
                continue
            if _hedge_watcher_task.done():
                logger.error(
                    "D313.v2 L2_WATCHDOG: HedgeIntegrityWatcher task is "
                    "DONE -- task crashed or exited unexpectedly. T2 "
                    "SAFETY NET IS DOWN. Investigate immediately.",
                )
                try:
                    await alert_critical(
                        "@here D313.v2 L2_WATCHDOG: HedgeIntegrityWatcher "
                        "TASK CRASHED. T2 safety net is DOWN. Bot is "
                        "running but positions can silently unhedge "
                        "(NXXT-class exposure). Restart bot OR investigate.",
                        webhook_url=settings.ops.alert_webhook_url,
                    )
                except Exception:
                    pass
                # doc 272 A2: durable incident too (the direct Discord post
                # above is fire-and-forget; the bus is what the pager tails).
                try:
                    from src.ops.incident_bus import emit_incident as _emit
                    _emit("HEDGE_WATCHER_DOWN", "CRITICAL",
                          context={"mode": "task_crashed"},
                          suggested=["restart the bot to relaunch the D313 "
                                     "watcher — positions can silently unhedge"],
                          dedup_key="l2_watchdog_crashed")
                except Exception as _ie:
                    logger.debug("doc272 incident emit failed: %s", _ie)
                continue
            age = _hedge_watcher.heartbeat_age_sec()
            if age is None:
                # No tick has completed yet -- give it 3 cycles to start
                continue
            if age > 90.0:
                if not _l2_warned:
                    logger.error(
                        "D313.v2 L2_WATCHDOG: HedgeIntegrityWatcher "
                        "heartbeat is %.0fs stale (threshold 90s). "
                        "Loop hung OR broker calls timing out. T2 SAFETY "
                        "NET DEGRADED.",
                        age,
                    )
                    try:
                        await alert_critical(
                            f"@here D313.v2 L2_WATCHDOG: heartbeat "
                            f"{age:.0f}s stale (threshold 90s). L2 loop "
                            f"hung OR broker calls timing out. T2 safety "
                            f"net DEGRADED -- positions may silently unhedge.",
                            webhook_url=settings.ops.alert_webhook_url,
                        )
                    except Exception:
                        pass
                    # doc 272 A2: durable incident for the pager as well.
                    try:
                        from src.ops.incident_bus import emit_incident as _emit
                        _emit("HEDGE_WATCHER_DOWN", "CRITICAL",
                              context={"mode": "heartbeat_stale",
                                       "age_sec": round(age, 1)},
                              suggested=["D313 watcher hung or broker calls "
                                         "timing out — T2 safety net degraded"],
                              dedup_key="l2_watchdog_stale")
                    except Exception as _ie:
                        logger.debug("doc272 incident emit failed: %s", _ie)
                    _l2_warned = True
            elif _l2_warned and not _l2_recovered_once:
                # Recovered after a stale window; emit one recovery log
                logger.info(
                    "D313.v2 L2_WATCHDOG: heartbeat recovered (age=%.0fs)",
                    age,
                )
                _l2_warned = False
                _l2_recovered_once = True
    try:
        _l2_watchdog_task = asyncio.create_task(
            _l2_watchdog_loop(),
            name="d313_l2_watchdog",
        )
        logger.info(
            "D313.v2 L2_WATCHDOG launched: polls heartbeat every 60s, "
            "pages on >90s stale (watcher-of-watchers safety)."
        )
    except Exception as _wd_e:
        logger.error("D313.v2 L2_WATCHDOG failed to launch: %s", _wd_e)
        _l2_watchdog_task = None  # noqa

    # D314 (2026-05-24, doc 173) -- durable alert spool retry loop.
    # Every Discord-bound payload is written to data/alerts/ before
    # the live POST. This task tails the spool dir, retries failed
    # records, deletes on success, quarantines stale (>24h) to a
    # subdir for operator review. Means: Discord outage / rate-limit
    # / rotated webhook cannot lose CRITICAL alerts.
    try:
        from src.monitoring.durable_alert_spool import run_retry_loop
        _alert_retry_task = asyncio.create_task(
            run_retry_loop(interval_sec=60.0, shutdown_event=shutdown),
            name="d314_alert_retry",
        )
        logger.info(
            "D314 ALERT_RETRY launched: walks data/alerts/ every 60s, "
            "CRITICAL severity gets 50-cycle retry budget."
        )
    except Exception as _ar_e:
        logger.error("D314 ALERT_RETRY failed to launch: %s", _ar_e)
        _alert_retry_task = None  # noqa

    # D315 (2026-05-24, doc 174) -- boot-up self-test alert.
    # Post a single Discord message confirming every watcher launched
    # successfully. If the operator doesn't see this within 60s of
    # `schtasks /run`, something didn't launch. ALSO serves as the first
    # end-to-end test of the D314 durable_post path in production: if
    # this message lands, the entire alert pipeline is healthy.
    try:
        _t2_on = (os.environ.get("MOMENTUM_T2_ENABLED", "")
                  in ("1", "true", "yes", "on"))
        _halt_on = (os.environ.get("MOMENTUM_HALT_NEW_ENTRIES", "")
                    in ("1", "true", "yes", "on"))
        _l2_status = (
            "OK"
            if (_hedge_watcher is not None and _hedge_watcher_task is not None)
            else "MISSING"
        )
        _wdog_status = (
            "OK" if (_l2_watchdog_task is not None) else "MISSING"
        )
        _retry_status = (
            "OK" if (_alert_retry_task is not None) else "MISSING"
        )
        # doc 209 BUGFIX: was getattr(..., "_session_start_equity", 0) — a non-existent
        # attribute → always $0.00 in the boot self-test (while the sibling "Trading Session
        # Started" alert showed the correct equity). Prefer the live account equity (same
        # source as line ~1286), then the real PM attr (_starting_equity), then 0.
        _eq = 0.0
        try:
            _eq = float((account or {}).get("equity", 0) or 0)
        except Exception:
            _eq = 0.0
        if _eq <= 0:
            _eq = float(getattr(position_manager, "_starting_equity", 0) or 0)
        from zoneinfo import ZoneInfo as _ZIBoot
        _now_et_boot = datetime.now(timezone.utc).astimezone(
            _ZIBoot("America/New_York"))
        _startup_summary = (
            f"\U0001F916 Bot startup @ commit `{_d217_hb_commit[:8]}` "
            f"({_now_et_boot.strftime('%H:%M ET')})\n"
            f"  T2: {'ON' if _t2_on else 'OFF'}  "
            f"HALT: {'ON' if _halt_on else 'OFF'}\n"
            f"  L2: {_l2_status}  L2_WATCHDOG: {_wdog_status}  "
            f"D314_RETRY: {_retry_status}\n"
            f"  Account equity: ${_eq:,.2f}"
        )
        # INFO severity: the goal is operator-sees-confirmation, not
        # operator-paged. Use _post directly with INFO so no @here mention.
        from src.monitoring.alerts import _post as _post_boot
        _boot_payload = {
            "embeds": [{
                "title": "\U0001F7E2 Boot self-test (D315)",
                "description": _startup_summary,
                "color": (0x2ECC71 if _l2_status == "OK" and
                          _wdog_status == "OK" and _retry_status == "OK"
                          else 0xF39C12),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": "Momentum-X | D315 Boot Self-Test"},
            }]
        }
        asyncio.ensure_future(_post_boot(
            settings.ops.alert_webhook_url,
            _boot_payload, severity="INFO",
        ))
        logger.info(
            "D315 BOOT_SELF_TEST posted: T2=%s HALT=%s L2=%s WDOG=%s RETRY=%s",
            _t2_on, _halt_on, _l2_status, _wdog_status, _retry_status,
        )
    except Exception as _bse_e:
        logger.error("D315 BOOT_SELF_TEST failed: %s", _bse_e)

    # D121 BUG-L1: Initialize _phase2_start before loop. If system starts
    # during market hours (late start), Phase 3 checks _phase2_start before
    # Phase 2 ever sets it, causing NameError.
    _phase2_start = 0

    # Main loop — runs until shutdown
    # D211: Initialize data enrichment variables at loop scope
    # These may be populated in Phase 0 (premarket) and consumed in Phase 2 (market open).
    # Must exist even when Phase 0 is skipped (e.g., process restart during market hours).
    _sec_pfetch_results: dict = {}
    _si_results: dict = {}

    # D217: Heartbeat file updater — called at critical points to track last_function
    # Use absolute path based on __file__ (relative paths fail if cwd != project root)
    # D302 (2026-05-19): git rev-parse block moved to ~line 1248
    # (before alert_session_start). _d217_hb_repo/path/commit/branch
    # are all already defined by the time we reach this point.

    # D239 — heartbeat dirty-worktree detection (Track A item 25 / 2026-04-24).
    # Today's LIDR session proved process bytecode can lag disk by 12+ hours
    # after a patch commits. The launcher reports `commit=899e03f9` while the
    # actual bytecode is at 1db1031 + uncommitted Track A patches. The
    # `dirty=true` and `ahead=N` flags surface this gap to monitoring.
    _d239_dirty = False
    _d239_ahead = 0
    try:
        _diff_rc = _d217_hb_sp.run(
            ["git", "diff", "--quiet"], cwd=_d217_hb_repo,
            stderr=_d217_hb_sp.DEVNULL, stdout=_d217_hb_sp.DEVNULL,
        ).returncode
        _d239_dirty = (_diff_rc != 0)
        # Untracked files also count as "dirty" for our purposes
        _untracked = _d217_hb_sp.check_output(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=_d217_hb_repo, stderr=_d217_hb_sp.DEVNULL,
        ).decode().strip()
        if _untracked:
            _d239_dirty = True
        # Local commits ahead of upstream (un-pushed work)
        try:
            _ahead_str = _d217_hb_sp.check_output(
                ["git", "rev-list", "--count", "@{u}..HEAD"],
                cwd=_d217_hb_repo, stderr=_d217_hb_sp.DEVNULL,
            ).decode().strip()
            _d239_ahead = int(_ahead_str) if _ahead_str.isdigit() else 0
        except Exception:
            _d239_ahead = 0
    except Exception:
        _d239_dirty = False
    if _d239_dirty or _d239_ahead > 0:
        logger.warning(
            "D239 HEARTBEAT_DIRTY_WORKTREE: commit=%s but worktree dirty=%s ahead=%d. "
            "Running process bytecode may differ from current disk state. "
            "Restart loads disk; monitor `commit=` field accordingly.",
            _d217_hb_commit, _d239_dirty, _d239_ahead,
        )

    # Ensure heartbeat directory exists at definition time (not per-call)
    os.makedirs(os.path.dirname(_d217_hb_path), exist_ok=True)

    # D221 Phase F: capture env-audit ONCE at cmd_paper start (env vars
    # are already loaded by main() at this point). Embedded into every
    # heartbeat write so monitoring / watchdog can read activation state
    # without parsing logs. Static for the session.
    try:
        from src.utils.env_audit import format_for_heartbeat
        _env_audit_for_heartbeat = format_for_heartbeat()
    except Exception as _ea_e:
        logger.warning("env_audit capture failed (non-fatal): %s", _ea_e)
        _env_audit_for_heartbeat = {"error": str(_ea_e)}

    def _d217_update_heartbeat(last_function: str) -> None:
        """Update heartbeat file with current function. Non-fatal on error."""
        try:
            import json as _j
            _hb = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "phase": getattr(_d107_control_state, '_phase', 'UNKNOWN'),
                "last_function": last_function,
                "pulse_count": getattr(_d107_watchdog, '_pulse_count', 0),
                "positions": len(position_manager.open_positions),
                "trades_today": session_trades,
                "pid": os.getpid(),
                "repo": _d217_hb_repo,
                "branch": _d217_hb_branch,
                "commit": _d217_hb_commit,
                # D239 (2026-04-24): worktree state vs running bytecode
                "dirty": _d239_dirty,
                "ahead": _d239_ahead,
                # D221 Phase F (Mon 2026-04-20): captured-once env audit so
                # monitoring can see which toggles are active without parsing
                # logs. Compact dict (~few hundred bytes); same payload every
                # heartbeat write because env state is static for the session.
                "env_audit": _env_audit_for_heartbeat,
            }
            _tmp = _d217_hb_path + ".tmp"
            with open(_tmp, "w") as _f:
                _j.dump(_hb, _f)
            os.replace(_tmp, _d217_hb_path)
        except Exception as _hb_err:
            logger.warning("D217: Heartbeat write failed: %s", _hb_err)

    # ── Tue 2026-04-21 Fix 2: heartbeat keeper task ──
    # Today's 10:18 ET watchdog kill happened because a single main-loop
    # iteration with 11.8s/agent latency × N candidates exceeded the 120s
    # watchdog threshold. The main-loop heartbeat (line ~1320 below) only
    # pulses at the TOP of each iteration; if the iteration body takes
    # 2+ minutes, the heartbeat goes stale.
    #
    # The keeper is an async background task that pulses every 30s
    # INDEPENDENT of the main loop. As long as the asyncio event loop
    # itself is alive (= scheduling tasks), the keeper pulses. If the
    # event loop genuinely deadlocks, the keeper goes silent — which
    # is exactly when we WANT the watchdog to fire.
    #
    # The 30s interval is the contract: the watchdog's tightest
    # threshold (180s opening, see scripts/watchdog_monitor.ps1) gives
    # 6 keeper-pulse opportunities before stale. Even with 2 missed
    # pulses we have 4× margin.
    _heartbeat_keeper_interval_s = 30
    _heartbeat_keeper_task = None  # type: ignore[var-annotated]

    async def _heartbeat_keeper_loop() -> None:
        """Background task: pulses heartbeat every _heartbeat_keeper_interval_s
        seconds. Runs until cancelled at shutdown."""
        try:
            while not shutdown.is_set():
                _d217_update_heartbeat("keeper")
                await asyncio.sleep(_heartbeat_keeper_interval_s)
        except asyncio.CancelledError:
            # Clean shutdown — re-raise per asyncio convention
            raise
        except Exception as _kp_err:
            # Never let the keeper crash the session. Log loud and exit
            # the keeper; main-loop heartbeat will resume responsibility.
            logger.error("Heartbeat keeper crashed: %s", _kp_err, exc_info=True)

    _heartbeat_keeper_task = asyncio.create_task(
        _heartbeat_keeper_loop(), name="heartbeat-keeper",
    )
    logger.info(
        "D221 Fix 2: heartbeat keeper task started (interval=%ds, watchdog "
        "threshold 180s opening / 240s intraday)",
        _heartbeat_keeper_interval_s,
    )

    while not shutdown.is_set():
        now = datetime.now(timezone.utc)
        # D62: DST-safe ET conversion using zoneinfo (handles EST/EDT automatically)
        from zoneinfo import ZoneInfo
        now_et = now.astimezone(ZoneInfo("America/New_York"))
        hour_et = now_et.hour

        # D113: Keep-alive pulse every loop iteration — prevents heartbeat
        # timeout during phase transitions (e.g., Phase 2 -> Phase 3 idle gap)
        try:
            _d107_watchdog.pulse()
        except Exception:
            pass

        # D217: Check TradingControlState flags (set via /pause, /resume, /shutdown HTTP endpoints)
        if _d107_control_state.shutdown_requested:
            logger.info("D217: Shutdown requested via control server — exiting main loop")
            shutdown.set()
            break
        if _d107_control_state.is_paused:
            logger.debug("D217: System paused via control server — skipping this cycle")
            await asyncio.sleep(5)
            continue

        # D217: Update control state + write heartbeat file for external watchdog
        _d217_loop_start = time.monotonic()
        try:
            _d217_n_positions = len(position_manager.open_positions)
        except Exception:
            _d217_n_positions = 0
        _d217_phase = "UNKNOWN"
        if hour_et < 4:
            _d217_phase = "PRE_PHASE_0"
        elif hour_et < 9 or (hour_et == 9 and now_et.minute < 30):
            _d217_phase = "PHASE_0_1"
        elif hour_et < 10:
            _d217_phase = "PHASE_2"
        elif hour_et < 16:
            _d217_phase = "PHASE_3"
        else:
            _d217_phase = "PHASE_4"
        _d107_control_state.update_status(
            phase=_d217_phase,
            positions=_d217_n_positions,
            daily_pnl=0,  # Updated in Phase 3 when P&L tracking runs
            trades_today=session_trades,
        )
        # D217: Write heartbeat file — external watchdog checks staleness
        _d217_update_heartbeat("main_loop_top")

        # D218: Heartbeat silence detector — alert if >5 min gap during market hours
        # Fires exactly once per silence event, not on every iteration.
        _d218_now_mono = time.monotonic()
        if not hasattr(cmd_paper, '_d218_last_hb_time'):
            cmd_paper._d218_last_hb_time = _d218_now_mono
            cmd_paper._d218_silence_alerted = False
        _d218_hb_gap = _d218_now_mono - cmd_paper._d218_last_hb_time
        cmd_paper._d218_last_hb_time = _d218_now_mono
        if _d218_hb_gap > 300 and 9 <= hour_et < 16 and not cmd_paper._d218_silence_alerted:
            cmd_paper._d218_silence_alerted = True
            _d218_silence_msg = (
                f"Heartbeat gap detected: {_d218_hb_gap:.0f}s between loop iterations "
                f"during market hours (phase={_d217_phase}, positions={_d217_n_positions})"
            )
            logger.critical("D218: %s", _d218_silence_msg)
            try:
                from src.monitoring.alerts import alert_critical
                import asyncio as _d218_aio
                _d218_aio.ensure_future(alert_critical(
                    _d218_silence_msg,
                    webhook_url=settings.ops.alert_webhook_url,
                ))
            except Exception:
                pass
        elif _d218_hb_gap <= 300:
            cmd_paper._d218_silence_alerted = False  # Reset for next silence event

        try:
            # ── D79: Market Holiday Detection ─────────────────────────
            # Check Alpaca market clock once per loop to detect holidays
            # and weekends. Skip Phase 2/3 when market is closed.
            if 9 <= hour_et < 16:
                try:
                    clock_data = await client.get_market_clock()
                    market_is_open = clock_data.get("is_open", False)
                    if not market_is_open:
                        next_open = clock_data.get("next_open", "unknown")
                        logger.info(
                            "[D79] Market CLOSED (holiday/weekend). Next open: %s. Skipping trading phases.",
                            next_open,
                        )
                except Exception as e:
                    # Sweep fix: Changed from optimistic (True) to pessimistic (None).
                    # On holidays, the API may fail. Assuming open risks trading on
                    # a closed market. None = "unknown" — phases that check
                    # `market_is_open is not False` still run (safe), but we log it.
                    logger.warning("[D79] Market clock check failed: %s — status unknown (not assuming open)", e)
                    market_is_open = None
            else:
                # Outside trading hours, no need to check
                market_is_open = None

            # ── Phase 0: Pre-Market Research (runs once before Phase 1) ──
            # D71: Only run during pre-market hours (4-9 AM ET), not during after-hours
            if not phase0_completed and 4 <= hour_et < 9:
                logger.info("═══════════ [Phase 0] ENTERING PRE-MARKET RESEARCH ═══════════")
                logger.info("[Phase 0] Running pre-market research...")
                _phase0_start = time.monotonic()
                try:
                    # D216: Refresh earnings calendar at session start
                    # D217: Gated by kill switch — disable if Finnhub causes issues
                    if settings.d216.finnhub_earnings_enabled:
                        try:
                            _n_earnings = await _earnings_cal.refresh()
                            if _n_earnings > 0:
                                _today_earnings = _earnings_cal.get_earnings_tickers()
                                if _today_earnings:
                                    logger.info(
                                        "D216: %d earnings tickers today: %s",
                                        len(_today_earnings), sorted(_today_earnings)[:10],
                                    )
                        except Exception as _ec_e:
                            logger.debug("D216: Earnings calendar refresh failed: %s", _ec_e)
                    else:
                        logger.info("D217: Earnings calendar disabled via kill switch")

                    # D216: Pre-load FinBERT model during Phase 0 (before market open)
                    # First call downloads ~400MB and takes 5-10s synchronously.
                    # Loading here prevents blocking the async event loop during Phase 2.
                    # D217: Gated by kill switch
                    if settings.d216.finbert_enabled:
                        try:
                            from src.agents.finbert_scorer import _get_pipeline
                            _fb_pipe = _get_pipeline()
                            if _fb_pipe and _fb_pipe != "FAILED":
                                logger.info("D216: FinBERT model pre-loaded in Phase 0 (ready for Phase 2)")
                            else:
                                logger.warning("D216: FinBERT model failed to load — sentiment scoring disabled")
                        except Exception as _fb_load_e:
                            logger.debug("D216: FinBERT pre-load failed: %s", _fb_load_e)
                    else:
                        logger.info("D217: FinBERT disabled via kill switch — skipping pre-load")

                    premarket_cache = await premarket_research.run_full_prefetch()
                    phase0_completed = True
                    # D96: Record Phase 0 duration
                    _phase0_elapsed = time.monotonic() - _phase0_start
                    try:
                        from src.monitoring.metrics import get_metrics as _gm
                        _gm().phase0_duration.set(_phase0_elapsed)
                    except Exception:
                        pass
                    # D95: Persist so restart won't re-run Phase 0
                    try:
                        state_mgr.set_phase0_completed(True)
                        state_mgr.save()
                    except Exception as e:
                        logger.warning("D218: Phase 0 state persistence failed: %s", e)
                    # Tue 2026-04-21 fix: PreMarketCache schema is the slim
                    # `news`/`sec` dict-of-dicts (src/data/premarket_research.py:21).
                    # The previous rich `cache.tickers` / `cache.sec_checked` /
                    # `tc.technicals.atr_14` API never landed — referencing it
                    # threw AttributeError at every session start, which the
                    # outer try/except (line 1410) caught as "non-fatal" and
                    # silenced. Pre-market research has been dormant since
                    # Sun 2026-04-19 (commit f9592c6 added the slim version).
                    # Fix: derive counts from the actual schema; drop the ATR
                    # seed (the slim API doesn't fetch technicals — restoring
                    # that is Phase C rich-PreMarketCache rebuild work).
                    _pm_news_keys = set(premarket_cache.news.keys())
                    _pm_sec_keys = set(premarket_cache.sec.keys())
                    _pm_all_tickers = _pm_news_keys | _pm_sec_keys
                    logger.info(
                        "[Phase 0] Complete: %d tickers cached (%d with news, %d SEC checked)",
                        len(_pm_all_tickers),
                        sum(1 for v in premarket_cache.news.values() if v),
                        len(_pm_sec_keys),
                    )
                    # D104 ATR seeding deferred: requires rich PreMarketCache
                    # restoration (technicals prefetch). Tracked in Phase C.
                    # The orchestrator's evaluation loop falls back to its own
                    # ATR fetch when no seed is provided — verified working.
                    # D191: SEC prefetcher batch analysis on premarket universe
                    _sec_pfetch_results: dict = {}
                    try:
                        _sec_pfetch_tickers = list(_pm_all_tickers)[:30]
                        if _sec_pfetch_tickers:
                            async with sec_prefetcher:
                                _sec_pfetch_results = await sec_prefetcher.analyze_batch(_sec_pfetch_tickers)
                            _sec_hard_blocks = [t for t, r in _sec_pfetch_results.items() if r.is_hard_bear()]
                            logger.info(
                                "D191: SEC prefetch complete — %d tickers, %d dilution blocks: %s",
                                len(_sec_pfetch_results), len(_sec_hard_blocks),
                                _sec_hard_blocks or "none",
                            )
                    except Exception as _pf_e:
                        logger.warning("D191: SEC prefetch failed (non-fatal): %s", _pf_e)
                except Exception as e:
                    logger.warning("[Phase 0] Pre-market research failed (non-fatal): %s", e)
                    phase0_completed = True  # Don't retry on failure

            if 4 <= hour_et < 9 or (hour_et == 9 and market_is_open is False):
                # ── Phase 1: Pre-Market Scanning ──
                # D82: Extended to cover 9:00-9:30 when market not yet open.
                # Alpaca clock reports is_open=False until 9:30 AM, so without
                # this guard the 9:00-9:30 window falls through to D79 idle.
                phase4_completed = False  # D71: Reset so Phase 4 can run at EOD
                eod_close_completed = False  # D83: Reset for new day's EOD close
                # D121 BUG-C3: Only reset fast-path state on first Phase 1 entry.
                # Previously these reset every 60s iteration, destroying the
                # fast-path queue built at ~9:25 before Phase 2 could fire it.
                if not fast_path_scored:
                    fast_path_fired = False   # D85: Reset for new day
                    fast_path_queue = []      # D85: Reset for new day
                logger.info("═══════════ [Phase 1] ENTERING PRE-MARKET SCAN ═══════════")
                logger.info("[Phase 1] Pre-Market scanning...")
                quotes = await _fetch_scan_quotes(client, settings)
                if quotes:
                    watchlist = scan_loop.run_single_scan(quotes)
                    logger.info("Watchlist: %d candidates", len(watchlist))

                    # D219: Enrich candidates with float_shares and market_cap from Finnhub
                    # Alpaca snapshots don't return these fields. Without them:
                    # - GEX market cap gate (D219 Phase 3) can't fire
                    # - Feature logger writes NULL for float/market_cap (100% data loss)
                    # - Float scoring in MFCS has no data
                    # Cache results per session to avoid redundant API calls.
                    # FIX (Apr 16): CandidateStock is frozen Pydantic — use model_copy()
                    # to produce new instances and replace watchlist entries in-place.
                    if not hasattr(cmd_paper, '_d219_float_cache'):
                        cmd_paper._d219_float_cache = {}
                    _finnhub_key = os.environ.get("FINNHUB_API_KEY", "")
                    if _finnhub_key:
                        for _ec_idx, _ec in enumerate(watchlist):
                            _enrich = None
                            if _ec.ticker in cmd_paper._d219_float_cache:
                                _enrich = cmd_paper._d219_float_cache[_ec.ticker]
                            else:
                                try:
                                    import httpx as _d219_httpx
                                    async with _d219_httpx.AsyncClient(timeout=3) as _d219_client:
                                        _d219_resp = await asyncio.wait_for(
                                            _d219_client.get(
                                                "https://finnhub.io/api/v1/stock/profile2",
                                                params={"symbol": _ec.ticker, "token": _finnhub_key},
                                            ),
                                            timeout=3.0,
                                        )
                                    _enrich = {"float_shares": None, "market_cap": None}
                                    if _d219_resp.status_code == 200:
                                        _d219_profile = _d219_resp.json()
                                        _so = _d219_profile.get("shareOutstanding")  # millions
                                        _mc = _d219_profile.get("marketCapitalization")  # millions
                                        # D220 sanity check: shareOutstanding is in millions.
                                        # 50_000 millions = 50B shares. Microsoft has ~7B; nothing
                                        # legitimate exceeds 50B. Apr 16: XHG returned 98_972 (= 98B
                                        # shares) due to a Finnhub units bug; we shipped that to D112
                                        # and it crashed the float gate logic.
                                        if _so and 0 < _so < 50_000:
                                            _enrich["float_shares"] = int(_so * 1_000_000 * 0.80)  # 80% of outstanding
                                        elif _so and _so >= 50_000:
                                            logger.warning(
                                                "D220 enrichment: dropping implausible shareOutstanding for %s: %s (>=50,000M)",
                                                _ec.ticker, _so,
                                            )
                                        if _mc and _mc > 0:
                                            _enrich["market_cap"] = _mc * 1_000_000
                                    cmd_paper._d219_float_cache[_ec.ticker] = _enrich
                                except Exception:
                                    _enrich = {"float_shares": None, "market_cap": None}
                                    cmd_paper._d219_float_cache[_ec.ticker] = _enrich
                            # Replace candidate with enriched copy (frozen model — model_copy)
                            if _enrich and (_enrich.get("float_shares") is not None or _enrich.get("market_cap") is not None):
                                _update = {}
                                if _enrich.get("float_shares") is not None:
                                    _update["float_shares"] = _enrich["float_shares"]
                                if _enrich.get("market_cap") is not None:
                                    _update["market_cap"] = _enrich["market_cap"]
                                try:
                                    watchlist[_ec_idx] = _ec.model_copy(update=_update)
                                except Exception as _enrich_err:
                                    logger.debug("D219 enrich skip %s: %s", _ec.ticker, _enrich_err)

                    for c in watchlist[:5]:
                        logger.info(
                            "  %s | Gap: %.1f%% | RVOL: %.1fx | Float: %s | MCap: %s",
                            c.ticker, c.gap_pct * 100, c.rvol,
                            f"{c.float_shares/1e6:.1f}M" if c.float_shares else "?",
                            f"${c.market_cap/1e6:.0f}M" if c.market_cap else "?",
                        )

                    # D218: Post watchlist to Discord
                    # Before 9AM: every 30 min. 9-9:30AM: every 5 min.
                    try:
                        from src.monitoring.alerts import post_watchlist
                        asyncio.ensure_future(post_watchlist(
                            candidates=[
                                {"ticker": c.ticker, "gap_pct": c.gap_pct,
                                 "rvol": c.rvol, "price": c.current_price}
                                for c in watchlist
                            ],
                            phase="Pre-Market" if hour_et < 9 else "Phase 1",
                            scan_count=phase3_cycle_count if hour_et >= 10 else 0,
                            webhook_url=settings.ops.watchlist_webhook_url,
                            hour_et=hour_et,
                        ))
                    except Exception:
                        pass

                    # D212: Record premarket velocity snapshots for all watchlist tickers
                    for _pv_c in watchlist:
                        try:
                            _pv_snap = quotes.get(_pv_c.ticker, {})
                            _pv_bid = _pv_snap.get("bid", _pv_c.current_price) or _pv_c.current_price
                            _pv_ask = _pv_snap.get("ask", _pv_c.current_price) or _pv_c.current_price
                            _premarket_velocity.record_snapshot(
                                _pv_c.ticker, _pv_c.current_price,
                                _pv_c.premarket_volume, _pv_bid or _pv_c.current_price, _pv_ask or _pv_c.current_price,
                            )
                        except Exception:
                            pass

                    # BUG-005 fix: Launch market data WebSocket for real-time VWAP
                    # once we have symbols. Subscribe to watchlist + any open positions.
                    if not _market_ws_launched and watchlist:
                        _ws_symbols = list({c.ticker for c in watchlist} | {p.ticker for p in position_manager.open_positions})
                        if _ws_symbols:
                            try:
                                _market_ws_task = asyncio.create_task(
                                    _market_ws_client.connect(symbols=_ws_symbols)
                                )
                                _market_ws_launched = True
                                logger.info(
                                    "BUG-005: Market data WebSocket launched for %d symbols (VWAP gate now active)",
                                    len(_ws_symbols),
                                )
                            except Exception as e:
                                logger.warning("Market data WebSocket failed to launch: %s", e)

                # ── Phase 1.5: D85 Fast-Path Pre-Market Scoring ──────
                # Score at 9:25 ET using news + RVOL only. Queue entries
                # for immediate execution at 9:30:01. Runs ONCE per day.
                min_et = now_et.minute
                scoring_time = 30 - settings.fast_path.scoring_time_minutes_before_open
                if (
                    settings.fast_path.enabled
                    and not fast_path_scored
                    and hour_et == 9
                    and min_et >= scoring_time
                    and watchlist
                ):
                    logger.info(
                        "═══ [Phase 1.5] D85 FAST-PATH SCORING at 9:%02d ET ═══",
                        min_et,
                    )
                    try:
                        # Pre-fetch news for top watchlist candidates
                        fp_news: dict = {}
                        fp_top = watchlist[:10]

                        # D85: Parallel news fetch for fast-path scoring
                        async def _fp_news_fetch(ticker: str) -> tuple[str, list]:
                            try:
                                items = await news_client.get_news_for_ticker(
                                    ticker, lookback_hours=24, max_items=10,
                                )
                                return ticker, items
                            except Exception:
                                logger.warning("D123: Fast-path news fetch failed for %s", ticker, exc_info=True)
                                return ticker, []

                        fp_news_tasks = [_fp_news_fetch(c.ticker) for c in fp_top]
                        fp_news_results = await asyncio.gather(*fp_news_tasks)
                        fp_news = dict(fp_news_results)

                        # Enrich with premarket cache
                        if premarket_cache:
                            premarket_research.enrich_news_dict(
                                premarket_cache, fp_news,
                                [c.ticker for c in fp_top],
                            )

                        # D85: Build SEC filings dict from premarket cache for
                        # fundamental + deep_search agents (Phase 0 pre-fetched)
                        fp_sec_filings: dict[str, list] = {}
                        if premarket_cache:
                            for c in fp_top:
                                sec_data = premarket_research.get_cached_sec(
                                    premarket_cache, c.ticker,
                                )
                                if sec_data and sec_data.get("filings"):
                                    fp_sec_filings[c.ticker] = sec_data["filings"]

                        fast_path_queue = await fp_scorer.score_candidates(
                            fp_top,
                            news_by_ticker=fp_news,
                            sec_filings_by_ticker=fp_sec_filings,
                        )
                        fast_path_scored = True

                        # D218: Post fast-path scores to Discord
                        try:
                            from src.monitoring.alerts import post_fast_path_scores
                            asyncio.ensure_future(post_fast_path_scores(
                                entries=[
                                    {"ticker": fpe.ticker, "partial_mfcs": fpe.partial_mfcs,
                                     "entry_price": fpe.entry_price, "stop_loss": fpe.stop_loss}
                                    for fpe in fast_path_queue
                                ],
                                webhook_url=settings.ops.watchlist_webhook_url,
                            ))
                        except Exception:
                            pass

                        # D106: Cache MFCS for smart bypass at market open
                        for fpe in fast_path_queue:
                            orchestrator.cache_premarket_mfcs(
                                ticker=fpe.ticker,
                                mfcs=fpe.partial_mfcs,
                                signal_price=fpe.candidate.current_price,
                                cached_signals=fpe.agent_signals,
                            )

                        logger.info(
                            "[Phase 1.5] D85: %d fast-path entries queued for 9:30:01 "
                            "(D106: %d MFCS cached for bypass)",
                            len(fast_path_queue), len(fast_path_queue),
                        )
                    except Exception as e:
                        logger.warning("[Phase 1.5] D85 fast-path scoring failed: %s", e)
                        fast_path_scored = True  # Don't retry

                # D113: Heartbeat pulse for Phase 1 pre-market scanning
                try:
                    _d107_watchdog.pulse(phase="PHASE_1")
                except Exception:
                    pass

            elif 9 <= hour_et < 10 and market_is_open is not False and not (
                hour_et == 9 and now_et.minute < 30 and market_is_open is None
            ):
                # ── Phase 2: Market Open — Evaluate + Execute ──
                # D79: Skipped when market_is_open == False (holiday/weekend)
                # Sweep fix: also skip 9:00-9:29 when clock API failed (None),
                # because market is definitely closed before 9:30 ET.
                # D54: If watchlist is empty (e.g., restart during Phase 2), run a quick scan first
                if not watchlist:
                    logger.info("[Phase 2] Watchlist empty — running emergency scan...")
                    quotes = await _fetch_scan_quotes(client, settings)
                    if quotes:
                        watchlist = scan_loop.run_single_scan(quotes)
                        logger.info("[Phase 2] Emergency scan found %d candidates", len(watchlist))
                        for c in watchlist[:5]:
                            logger.info(
                                "  %s | Gap: %.1f%% | RVOL: %.1fx",
                                c.ticker, c.gap_pct * 100, c.rvol,
                            )

                # ── D86: Auto-cover ghost positions at market open ──────
                # Ghost positions (broker-side positions our tracker doesn't know about)
                # are detected at startup and queued for closing. We close them once
                # at the first Phase 2 iteration when market is confirmed open.
                if _ghost_positions_to_close and not _ghost_cleanup_done:
                    _ghost_cleanup_done = True
                    logger.info(
                        "D86: Auto-covering %d ghost positions at market open: %s",
                        len(_ghost_positions_to_close),
                        _ghost_positions_to_close,
                    )
                    for _ghost_sym in _ghost_positions_to_close:
                        try:
                            await client.close_position(_ghost_sym)
                            logger.info(
                                "D86: Ghost position %s — close_position submitted",
                                _ghost_sym,
                            )
                        except Exception as e:
                            logger.error(
                                "D86: Failed to close ghost position %s: %s",
                                _ghost_sym, e,
                            )
                    logger.info("D86: Ghost position cleanup complete")

                # ── D91: Close overnight positions at market open ──────
                # Bug E fix (2026-04-22): delegate to the explicit
                # `_close_overnight_position` helper. The helper emits
                # one log line per step (D91 STEP 1/2/3) so operators
                # can grep the close trace per-ticker. Each step is
                # independently fault-tolerant: a failed stop-cancel
                # does NOT prevent the market sell.
                if _overnight_positions_to_close and not _overnight_cleanup_done:
                    _overnight_cleanup_done = True
                    from src.execution.bridge import _close_overnight_position as _d91_close
                    logger.info(
                        "D91: Closing %d OVERNIGHT positions at market open: %s",
                        len(_overnight_positions_to_close),
                        _overnight_positions_to_close,
                    )
                    for _ov_sym in _overnight_positions_to_close:
                        try:
                            # D295 (2026-05-13): pass trade_journal so the
                            # bridge can record_close() for each carry-overnight
                            # close. Without this, EOD reconciliation flags
                            # every D278 t1_next_open exit as journal=MISSING
                            # (D222/D238). See bridge.py STEP 4 docstring.
                            _ok = await _d91_close(
                                client=client,
                                position_manager=position_manager,
                                ticker=_ov_sym,
                                trade_journal=trade_journal,
                            )
                            if _ok:
                                # Persist the removal to state-mgr so a
                                # subsequent restart doesn't re-open it.
                                try:
                                    state_mgr.remove_position(_ov_sym)
                                    state_mgr.save()
                                except Exception as _se:
                                    logger.warning(
                                        "D91 STEP 3 %s: state_mgr update failed (non-fatal): %s",
                                        _ov_sym, _se,
                                    )
                        except Exception as e:
                            logger.error(
                                "D91: Failed to close overnight position %s: %s",
                                _ov_sym, e,
                            )
                    logger.info("D91: Overnight position cleanup complete")

                # D108 WS1: System health gate — skip cycle if Alpaca breaker OPEN
                try:
                    from src.utils.circuit_breaker import check_system_health
                    _sys_healthy, _sys_problems = check_system_health()
                    if not _sys_healthy:
                        logger.warning(
                            "D108: System health check FAILED — skipping Phase 2 cycle: %s",
                            "; ".join(_sys_problems),
                        )
                        await asyncio.sleep(30)
                        continue
                except Exception as _she:
                    logger.debug("D108: Health check error (non-fatal): %s", _she)

                # D203: Day-of-week regime gate
                _d203_allowed_days = settings.scoring.trading_days_allowed
                _d203_today_dow = now_et.weekday()  # 0=Mon, 4=Fri
                _d203_allowed_set = set()
                try:
                    _d203_allowed_set = {int(d.strip()) for d in _d203_allowed_days.split(",") if d.strip().isdigit()}
                except Exception:
                    _d203_allowed_set = {0, 1, 2, 3, 4}  # Fallback: allow all
                if _d203_allowed_set and _d203_today_dow not in _d203_allowed_set:
                    _dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                    logger.warning(
                        "D203 DAY-OF-WEEK GATE: %s (dow=%d) is not in allowed days %s — "
                        "skipping Phase 2 evaluation (negative EV regime)",
                        _dow_names[_d203_today_dow], _d203_today_dow,
                        _d203_allowed_days,
                    )
                    await asyncio.sleep(60)
                    continue

                _phase2_start = time.monotonic()
                logger.info("═══════════ [Phase 2] ENTERING MARKET OPEN ═══════════")
                logger.info("[Phase 2] Market Open — evaluating %d candidates...", len(watchlist))

                # ── D85: Fire fast-path orders at 9:30:01 ──────────
                if (
                    settings.fast_path.enabled
                    and not fast_path_fired
                    and fast_path_queue
                    and market_is_open is not False
                ):
                    logger.info(
                        "═══ [Phase 2] D85 FAST-PATH FIRE: %d entries at market open ═══",
                        len(fast_path_queue),
                    )
                    try:
                        # Doc 84 (PROMPT_07, 2026-04-30): AT-1 fix migration.
                        # OLD: execute_queue submitted OTOs at limit price + main.py
                        # inline-registered positions with fpe.entry_price (the LIMIT)
                        # as the recorded fill, even when the order never filled
                        # (Bug AT-1, see doc 80 §1).
                        # NEW: prepare_queue does pre-checks WITHOUT submitting; we
                        # build a synthetic verdict + scored from each READY entry
                        # and route through bridge.execute_verdict (which submits +
                        # polls for broker confirmation via _poll_for_terminal_fill).
                        # post_fill_bookkeeping then handles all the standard recording
                        # (D215 with REALIZED fill price, journal, exit_ladder, stop
                        # register, state_mgr, BAR-1+D278). Same code path as the
                        # 3 paths migrated in PROMPT_06 (PHASE2_BUY/RESCAN/VWAP).
                        fast_path_queue = await fp_executor.prepare_queue(fast_path_queue)
                        fast_path_fired = True  # We've initiated the fire — don't retry

                        from src.execution.fast_path import (
                            fast_path_entry_to_verdict,
                            fast_path_entry_to_scored,
                        )
                        for fpe in fast_path_queue:
                            # D121 BUG-P7: skip already-stopped-out tickers
                            if fpe.ticker in _stopped_out_tickers:
                                logger.warning(
                                    "D121 BUG-P7: Skipping fast-path %s — already stopped out",
                                    fpe.ticker,
                                )
                                continue
                            # Only process entries that passed all pre-checks
                            if fpe.status != "READY" or fpe.qty <= 0:
                                continue
                            # D121 BUG-P10: skip if position already exists (recovered from state)
                            if position_manager.has_position(fpe.ticker):
                                logger.info(
                                    "D121 BUG-P10: Skipping fast-path %s — position already exists (recovered)",
                                    fpe.ticker,
                                )
                                continue
                            # Build synthetic verdict + scored, route through unified path
                            _fp_verdict = fast_path_entry_to_verdict(fpe)
                            _fp_scored = fast_path_entry_to_scored(fpe)
                            try:
                                order = await bridge.execute_verdict(_fp_verdict, scored=_fp_scored)
                            except Exception as _fp_be:
                                logger.error(
                                    "D85 bridge.execute_verdict failed for %s: %s",
                                    fpe.ticker, _fp_be,
                                )
                                fpe.status = "ERROR"
                                fpe.error = f"bridge.execute_verdict failed: {_fp_be}"
                                continue
                            if order is None:
                                logger.warning(
                                    "D85 FAST_PATH %s: bridge returned no confirmed order "
                                    "(rejected/expired/timeout) — no position recorded",
                                    fpe.ticker,
                                )
                                fpe.status = "EXPIRED"
                                continue
                            # Confirmed broker fill — call the unified post-fill helper.
                            fpe.order_id = order.order_id
                            fpe.status = "FILLED"
                            await post_fill_bookkeeping(
                                order=order, verdict=_fp_verdict, scored=_fp_scored,
                                candidate=fpe.candidate,
                                path="FAST_PATH",
                                ctx=_post_fill_ctx,
                                log_prefix="FastPath",
                            )
                            # D121 BUG-P3: update Prometheus open_positions gauge
                            try:
                                from src.monitoring.metrics import get_metrics as _gm_fp
                                _gm_fp().open_positions.set(len(position_manager.open_positions))
                            except Exception as _fp_me:
                                logger.debug("D121 BUG-P3: metrics update failed: %s", _fp_me)
                    except Exception as e:
                        logger.error("[Phase 2] D85 fast-path fire failed: %s", e)
                        fast_path_fired = True  # Don't retry

                if watchlist and bridge.position_manager.can_enter_new_position():
                    # ── D85: Parallel data fetch (was sequential) ──────
                    # News + bars fetched concurrently for all candidates.
                    # Previously: 10 sequential news + 10 sequential bars = 20 serial requests
                    # Now: all 20 in parallel ≈ single request latency

                    # D160: Inject high-RVOL mandatory re-eval candidates.
                    # Stocks with RVOL > high_rvol_reeval_threshold that got NO_TRADE
                    # must cycle through at least high_rvol_min_evals evaluations.
                    # Replace lowest-priority standard candidates to preserve the top-10 limit.
                    _base_candidates = watchlist[:10]
                    _watchlist_tickers = {c.ticker for c in _base_candidates}
                    _reeval_inject = [
                        c for c in _forced_reeval_candidates
                        if c.ticker not in _watchlist_tickers
                        and c.ticker not in _stopped_out_tickers
                        and not position_manager.has_position(c.ticker)
                    ]
                    if _reeval_inject:
                        # Prepend re-eval candidates, trim to keep total at 10
                        _combined = _reeval_inject[:3] + _base_candidates
                        top_candidates = _combined[:10]
                        logger.info(
                            "D160 HIGH-RVOL REEVAL: injecting %d candidates into eval batch: %s",
                            len(_reeval_inject[:3]),
                            [c.ticker for c in _reeval_inject[:3]],
                        )
                    else:
                        top_candidates = _base_candidates

                    from src.data.technical_indicators import compute_indicators, format_price_data

                    # Parallel news fetch
                    async def _fetch_news(ticker: str) -> tuple[str, list]:
                        try:
                            items = await news_client.get_news_for_ticker(
                                ticker, lookback_hours=24, max_items=10,
                            )
                            return ticker, items
                        except Exception as e:
                            logger.warning("  %s: news fetch failed: %s", ticker, e)
                            return ticker, []

                    # Parallel bars fetch
                    async def _fetch_bars(ticker: str) -> tuple[str, dict]:
                        try:
                            bars = await client.get_bars(
                                ticker, timeframe="1Min", limit=200,
                            )
                            if bars:
                                # doc 188: capture the 9:30-9:35 opening-range bar once
                                # (no-op before 9:35 / if already captured). Guarded.
                                try:
                                    _or_tracker.capture(ticker, bars)
                                except Exception:
                                    pass
                                indicators = compute_indicators(bars)
                                price_data = format_price_data(bars)
                                return ticker, {
                                    "price_data": price_data,
                                    "indicators": indicators,
                                }
                            logger.debug("  %s: bars response empty — technical agent has no price data", ticker)
                            return ticker, {}
                        except Exception as e:
                            logger.warning("  %s: bar fetch failed: %s", ticker, e)
                            return ticker, {}

                    # D85: Fire all fetches concurrently
                    import time as _time
                    _fetch_start = _time.monotonic()

                    all_fetch_tasks = (
                        [_fetch_news(c.ticker) for c in top_candidates]
                        + [_fetch_bars(c.ticker) for c in top_candidates]
                    )
                    all_fetch_results = await asyncio.gather(*all_fetch_tasks)

                    # Split results: first N are news, second N are bars
                    n = len(top_candidates)
                    news_by_ticker: dict = dict(all_fetch_results[:n])
                    _bar_results = list(all_fetch_results[n:])
                    _empty_bar_tickers = [t for t, d in _bar_results if not d]
                    if _empty_bar_tickers:
                        logger.debug(
                            "D85: %d/%d tickers had empty bar data (dropped): %s",
                            len(_empty_bar_tickers), len(_bar_results),
                            ", ".join(_empty_bar_tickers),
                        )
                    market_data_by_ticker: dict = {
                        t: d for t, d in _bar_results if d
                    }

                    _fetch_elapsed = (_time.monotonic() - _fetch_start) * 1000
                    logger.info(
                        "D85: Parallel data fetch complete in %.0fms — %d news, %d bars",
                        _fetch_elapsed,
                        sum(1 for v in news_by_ticker.values() if v),
                        len(market_data_by_ticker),
                    )

                    # ── D212: Enrich candidates with gap history + sector ──
                    # D216 FIX: Parallelize daily bar fetches (was sequential, 6s+ delay)
                    try:
                        _d212_bar_tasks = [
                            client.get_bars(c.ticker, timeframe="1Day", limit=20)
                            for c in top_candidates
                        ]
                        _d212_all_bars = await asyncio.gather(*_d212_bar_tasks, return_exceptions=True)

                        _d212_enriched = []
                        for _d212_idx, _d212_c in enumerate(top_candidates):
                            try:
                                _d212_bars = _d212_all_bars[_d212_idx]
                                if isinstance(_d212_bars, BaseException):
                                    _d212_enriched.append(_d212_c)
                                    continue
                                if _d212_bars and len(_d212_bars) >= 2:
                                    _d212_gaps = 0
                                    _d212_day2 = False
                                    for _d212_i in range(1, len(_d212_bars)):
                                        _d212_pc = _d212_bars[_d212_i - 1].get("c", 0)
                                        _d212_co = _d212_bars[_d212_i].get("o", 0)
                                        if _d212_pc > 0 and (_d212_co - _d212_pc) / _d212_pc > 0.05:
                                            _d212_gaps += 1
                                            if _d212_i == len(_d212_bars) - 1:
                                                _d212_day2 = True
                                    _d212_enriched.append(_d212_c.model_copy(update={
                                        "prior_gap_count": _d212_gaps,
                                        "is_day2_runner": _d212_day2,
                                    }))
                                    if _d212_gaps >= 3:
                                        logger.info("D212 SERIAL GAPPER: %s — %d gaps in 20 sessions", _d212_c.ticker, _d212_gaps)
                                    if _d212_day2:
                                        logger.info("D212 DAY-2 RUNNER: %s", _d212_c.ticker)
                                else:
                                    _d212_enriched.append(_d212_c)
                            except Exception:
                                _d212_enriched.append(_d212_c)
                        top_candidates = _d212_enriched
                    except Exception as _d212_e:
                        logger.debug("D212: Gap history enrichment failed: %s", _d212_e)

                    # ── D216: Flag earnings-day gap-ups ──
                    # D217: Gated by kill switch
                    if settings.d216.finnhub_earnings_enabled and _earnings_cal.loaded:
                        for _ec_c in top_candidates:
                            # D217: Wrap each ticker in try/except — malformed earnings
                            # data for ONE ticker must not crash the entire pipeline
                            try:
                                _ec_is_earnings, _ec_details = _earnings_cal.check_ticker(_ec_c.ticker)
                                if _ec_is_earnings:
                                    # Inject synthetic earnings headline — ALWAYS append,
                                    # even when Alpaca headlines exist (provides structured context)
                                    _ec_eps = _ec_details.get("epsEstimate", "?") if _ec_details else "?"
                                    _ec_headline = f"{_ec_c.ticker} scheduled earnings report (EPS estimate: {_ec_eps})"
                                    from src.data.news_client import NewsItem
                                    from datetime import datetime as _dt
                                    _ec_item = NewsItem(
                                        headline=_ec_headline,
                                        summary=f"Earnings scheduled. Gap of {_ec_c.gap_pct*100:.1f}% on earnings day suggests beat.",
                                        source="finnhub_earnings_calendar",
                                        url="",
                                        published_at=_dt.now(timezone.utc),
                                        tickers=[_ec_c.ticker],
                                        provider="finnhub_calendar",
                                    )
                                    _ec_existing = news_by_ticker.get(_ec_c.ticker, [])
                                    news_by_ticker[_ec_c.ticker] = list(_ec_existing) + [_ec_item]
                                    logger.info(
                                        "D216 EARNINGS DAY: %s — gap=%.1f%% on scheduled earnings (EPS est=%s, %d existing headlines)",
                                        _ec_c.ticker, _ec_c.gap_pct * 100, _ec_eps, len(_ec_existing),
                                    )
                            except Exception as _ec_err:
                                logger.warning("D217: Earnings injection failed for %s: %s", _ec_c.ticker, _ec_err)

                    # ── Enrich with pre-market cache (Phase 0 data) ──
                    if premarket_cache:
                        premarket_research.enrich_news_dict(
                            premarket_cache, news_by_ticker,
                            [c.ticker for c in top_candidates],
                        )

                    # ── D192: Short interest batch fetch for squeeze detection ──
                    _si_results: dict = {}
                    try:
                        _si_tickers = [c.ticker for c in top_candidates]
                        _si_results = await short_interest_provider.get_batch(_si_tickers)
                        _squeeze_candidates = [t for t, r in _si_results.items() if r.is_squeeze_candidate()]
                        if _squeeze_candidates:
                            logger.info(
                                "D192: Short interest — %d squeeze candidates: %s",
                                len(_squeeze_candidates), _squeeze_candidates,
                            )
                    except Exception as _si_e:
                        logger.debug("D192: Short interest fetch failed (non-fatal): %s", _si_e)

                    # ── D193: Register headlines for sentiment velocity ──
                    # D217 FIX: news_by_ticker contains NewsItem dataclass objects,
                    # not dicts. Use attribute access, not .get().
                    try:
                        for _sv_ticker, _sv_items in news_by_ticker.items():
                            if _sv_items:
                                _sv_batch = []
                                for h in _sv_items:
                                    if isinstance(h, dict):
                                        _sv_batch.append({
                                            "headline": h.get("headline", "") or h.get("title", ""),
                                            "source": h.get("source", ""),
                                            "timestamp": h.get("created_at", ""),
                                        })
                                    else:
                                        # NewsItem dataclass (or similar)
                                        _sv_batch.append({
                                            "headline": getattr(h, "headline", ""),
                                            "source": getattr(h, "source", ""),
                                            "timestamp": str(getattr(h, "published_at", "")),
                                        })
                                if _sv_batch:
                                    sentiment_tracker.register_headlines_batch(_sv_ticker, _sv_batch)
                    except Exception as _sv_e:
                        logger.debug("D193: Sentiment registration failed (non-fatal): %s", _sv_e)

                    # D196: Track evaluated tickers for EOD bar recording
                    _d196_session_tickers.update(c.ticker for c in top_candidates)
                    # D210: Register every candidate that passed the scanner filter
                    for _d210_c in top_candidates:
                        try:
                            session_collector.register_candidate(_d210_c)
                        except Exception:
                            pass

                    # ── D207: Proactive Aggressive Gap Fader Short ──────────
                    # BEFORE LLM evaluation: identify extreme gap-ups (>50%) as
                    # direct SHORT candidates. These are promotional pumps that
                    # statistically fade -27.7% on average. Shorts them immediately,
                    # saving LLM tokens and capturing the fading window.
                    _d207_cfg = settings.aggressive_short
                    _d207_shorted: set = set()
                    # BUG-FIX: Check D198 session regime before D207 shorts.
                    # Without this, D207 could fire shorts during HALTED session.
                    _d207_regime_ok = True
                    try:
                        _d207_allowed, _d207_regime_reason = session_regime.should_allow_new_entry()
                        if not _d207_allowed:
                            _d207_regime_ok = False
                            logger.warning("D207 BLOCKED by D198 session regime: %s", _d207_regime_reason)
                    except Exception:
                        pass  # Fail-open: if regime check errors, allow D207
                    if _d207_cfg.enabled and _d207_regime_ok:
                        _d207_short_count = sum(
                            1 for p in bridge.position_manager.open_positions
                            if getattr(p, "direction", "long") == "short"
                        )
                        for _d207_c in list(top_candidates):
                            if _d207_short_count >= _d207_cfg.max_concurrent:
                                break
                            _d207_gap = abs(_d207_c.gap_pct or 0.0)
                            _d207_rvol = _d207_c.rvol or 0.0
                            _d207_price = _d207_c.current_price or 0.0
                            _d207_adv = _d207_c.avg_daily_volume or 0
                            _d207_dolvol = _d207_adv * _d207_price if _d207_adv > 0 else _d207_c.premarket_volume * _d207_price * 10

                            # Gate 1: Gap threshold
                            if _d207_gap < _d207_cfg.min_gap_pct:
                                continue

                            logger.info(
                                "D207 AGGRESSIVE SHORT EVAL: %s gap=%.0f%% rvol=%.1fx dolvol=$%.0fK price=$%.2f",
                                _d207_c.ticker, _d207_gap * 100, _d207_rvol,
                                _d207_dolvol / 1000, _d207_price,
                            )

                            # Gate 2: CATALYST tier check (no shorts on institutional mid-caps)
                            try:
                                from src.data.universe_tiers import UniverseTier, universe_classifier as _d207_uc
                                _d207_tier = _d207_uc.classify(
                                    _d207_c.ticker, _d207_price, _d207_c.market_cap,
                                    _d207_gap, _d207_rvol, _d207_dolvol,
                                )
                                if _d207_tier == UniverseTier.CATALYST:
                                    logger.info("D207 REJECTED %s: CATALYST tier (institutional)", _d207_c.ticker)
                                    continue
                            except Exception:
                                pass

                            # Gate 3: No confirmed catalyst
                            if _d207_cfg.require_no_catalyst and _d207_c.has_news_catalyst:
                                logger.info("D207 REJECTED %s: has news catalyst", _d207_c.ticker)
                                continue

                            # Gate 4: RVOL
                            if _d207_rvol < _d207_cfg.min_rvol:
                                logger.info("D207 REJECTED %s: RVOL %.1fx < %.1fx", _d207_c.ticker, _d207_rvol, _d207_cfg.min_rvol)
                                continue

                            # Gate 5: Dollar volume
                            if _d207_dolvol < _d207_cfg.min_dollar_volume:
                                logger.info("D207 REJECTED %s: dolvol $%.0fK < $%.0fK", _d207_c.ticker, _d207_dolvol/1000, _d207_cfg.min_dollar_volume/1000)
                                continue

                            # Gate 6: Shortability via Alpaca
                            _d207_shortable = False
                            try:
                                _d207_asset = await client.check_asset_tradable(_d207_c.ticker)
                                _d207_shortable = (
                                    _d207_asset.get("shortable", False)
                                    and _d207_asset.get("easy_to_borrow", False)
                                )
                            except Exception as _d207_e:
                                logger.debug("D207: Shortability check failed for %s: %s", _d207_c.ticker, _d207_e)

                            if not _d207_shortable:
                                logger.info("D207 REJECTED %s: not shortable/ETB", _d207_c.ticker)
                                continue

                            # ── All gates passed — build SHORT verdict ──────
                            _d207_entry = _d207_price
                            _d207_stop = round(_d207_entry * (1 + _d207_cfg.stop_pct_above_entry), 2)
                            _d207_targets = [round(_d207_entry * (1 + t), 2) for t in _d207_cfg.target_pcts]

                            from src.core.models import TradeVerdict as _D207TV
                            _d207_verdict = _D207TV(
                                ticker=_d207_c.ticker,
                                action="BUY",
                                confidence=0.80,
                                mfcs=0.0,
                                entry_price=_d207_entry,
                                stop_loss=_d207_stop,
                                target_prices=_d207_targets,
                                position_size_pct=_d207_cfg.position_size_pct,
                                direction="short",
                                reasoning_summary=f"D207 aggressive short: gap={_d207_gap:.0%} rvol={_d207_rvol:.1f}x no_catalyst",
                            )

                            try:
                                _d207_order = await bridge.execute_verdict(_d207_verdict, scored=None)
                                if _d207_order:
                                    session_trades += 1
                                    _d207_short_count += 1
                                    _d207_shorted.add(_d207_c.ticker)
                                    logger.warning(
                                        "D207 AGGRESSIVE SHORT FILLED: %s qty=%d @ $%.2f "
                                        "gap=%.0f%% stop=$%.2f targets=%s",
                                        _d207_order.ticker, _d207_order.qty,
                                        _d207_order.submitted_price, _d207_gap * 100,
                                        _d207_stop, [f"${t:.2f}" for t in _d207_targets],
                                    )
                                    # D215: Unified execution recording
                                    _exec_recorder.record_execution(
                                        ticker=_d207_order.ticker, side="sell",
                                        fill_price=_d207_order.fill_price if _d207_order.fill_price and _d207_order.fill_price > 0 else _d207_order.submitted_price,
                                        signal_price=_d207_order.submitted_price,
                                        qty=_d207_order.qty, order_id=_d207_order.order_id,
                                        execution_path="D207_SHORT",
                                        gap_pct=abs(_d207_gap), rvol=_d207_rvol,
                                        direction="short",
                                        stop_loss=_d207_stop,
                                        target_prices=_d207_targets,
                                        candidate=_d207_c,
                                    )
                                    # Persist state for crash recovery
                                    try:
                                        state_mgr.update_position(
                                            ticker=_d207_c.ticker,
                                            order_id=_d207_order.order_id,
                                            stop_order_id=_d207_order.stop_order_id,
                                            direction="short",
                                        )
                                    except Exception:
                                        pass
                                    # BUG-FIX: Record D207 shorts in trade journal.
                                    # Without this, D207 shorts have ZERO audit trail
                                    # (removed from top_candidates before journal loop).
                                    try:
                                        from src.utils.trade_logger import generate_trade_id as _d207_gen_id
                                        _d207_j_id = _d207_gen_id(_d207_c.ticker)
                                        _d207_j_entry = trade_journal.create_entry(
                                            _d207_j_id, _d207_c, phase="D207_AGGRESSIVE_SHORT",
                                        )
                                        trade_journal.record_input_data(
                                            _d207_j_entry,
                                            news_items=news_by_ticker.get(_d207_c.ticker, []),
                                            market_data=market_data_by_ticker.get(_d207_c.ticker, {}),
                                        )
                                        _d207_j_entry.action = "BUY"
                                        _d207_j_entry.direction = "short"
                                        _d207_j_entry.confidence = 0.80
                                        _d207_j_entry.entry_price = _d207_entry
                                        _d207_j_entry.stop_loss = _d207_stop
                                        _d207_j_entry.target_prices = _d207_targets
                                        _d207_j_entry.reasoning_summary = _d207_verdict.reasoning_summary
                                        _d207_j_entry.order_id = _d207_order.order_id
                                        _d207_j_entry.stop_order_id = _d207_order.stop_order_id
                                        trade_journal.flush(_d207_j_entry)
                                        logger.debug("D207: Journal entry created for %s", _d207_c.ticker)
                                    except Exception as _d207_j_err:
                                        logger.debug("D207: Journal recording failed for %s: %s",
                                                    _d207_c.ticker, _d207_j_err)
                                else:
                                    logger.warning("D207: %s short verdict rejected by execution layer", _d207_c.ticker)
                            except Exception as _d207_exec_err:
                                logger.error("D207: Execution error for %s: %s", _d207_c.ticker, _d207_exec_err)

                        # Remove shorted tickers from candidate list (skip LLM eval)
                        if _d207_shorted:
                            top_candidates = [c for c in top_candidates if c.ticker not in _d207_shorted]
                            logger.info("D207: Removed %d shorted tickers from LLM eval: %s",
                                       len(_d207_shorted), ", ".join(_d207_shorted))

                    # ── M2 (doc 179): real-time data for the evaluated candidate set ──
                    # The WS previously subscribed ONLY the ~19-symbol premarket list +
                    # tickers we FILLED (add_symbols on order). Post-open candidates were
                    # evaluated on stale REST data → 59x "VWAP unavailable" on 5/28 and
                    # stale RVOL/price feeding the technical agent + D106 VWAP gate +
                    # D112 router. Subscribe the evaluated set NOW so live trades/bars
                    # are streaming by the next cycle. add_symbols is idempotent +
                    # chunked; top_candidates is ~10 names so the subscribed union stays
                    # bounded across the session.
                    if _market_ws_client is not None and top_candidates:
                        try:
                            await _market_ws_client.add_symbols([c.ticker for c in top_candidates])
                        except Exception as _m2e:
                            logger.debug("M2: candidate WS subscribe failed: %s", _m2e)

                    verdicts = await orchestrator.evaluate_candidates(
                        top_candidates,
                        news_by_ticker=news_by_ticker,
                        market_data_by_ticker=market_data_by_ticker,
                    )
                    # D150: Guard against None return from orchestrator
                    if not verdicts:
                        verdicts = []

                    # D211: Feed VIX to session regime for intraday spike detection
                    if orchestrator._vix_level is not None:
                        session_regime.update_vix(orchestrator._vix_level)
                        # Seed vix_at_open with first real VIX reading
                        if session_regime._perf.vix_at_open == 0.0:
                            session_regime._perf.vix_at_open = orchestrator._vix_level
                            session_regime._perf.vix_current = orchestrator._vix_level
                            logger.info(
                                "D211: Seeded vix_at_open=%.1f from orchestrator",
                                orchestrator._vix_level,
                            )

                    # ── D160: High-RVOL mandatory re-evaluation tracking ──
                    # After each evaluation batch, update eval counts and stash
                    # high-RVOL NO_TRADE candidates for mandatory re-evaluation.
                    # Hard blocks (PROMOTIONAL_LATE) are always final — no re-eval.
                    for _v in verdicts:
                        _rvol_eval_counts[_v.ticker] = _rvol_eval_counts.get(_v.ticker, 0) + 1
                        _cand_for_reeval = next(
                            (c for c in top_candidates if c.ticker == _v.ticker), None
                        )
                        if _cand_for_reeval is None:
                            continue
                        _is_high_rvol = (
                            _cand_for_reeval.rvol >= settings.thresholds.high_rvol_reeval_threshold
                        )
                        _is_promotional_late = (
                            "PROMOTIONAL_LATE" in (_v.reasoning_summary or "")
                        )
                        if (
                            _is_high_rvol
                            and _v.action == "NO_TRADE"
                            and not _is_promotional_late
                            and _rvol_eval_counts[_v.ticker] < settings.thresholds.high_rvol_min_evals
                        ):
                            # Stash for re-evaluation next cycle (deduplicate by ticker)
                            if not any(c.ticker == _v.ticker for c in _forced_reeval_candidates):
                                _forced_reeval_candidates.append(_cand_for_reeval)
                            logger.warning(
                                "D160 HIGH-RVOL NO_TRADE: %s (RVOL=%.0fx, eval %d/%d) — "
                                "scheduling mandatory re-evaluation next cycle",
                                _v.ticker,
                                _cand_for_reeval.rvol,
                                _rvol_eval_counts[_v.ticker],
                                settings.thresholds.high_rvol_min_evals,
                            )
                        elif _is_high_rvol and _v.action != "NO_TRADE":
                            # BUY verdict or HOLD — remove from re-eval pool
                            _forced_reeval_candidates[:] = [
                                c for c in _forced_reeval_candidates if c.ticker != _v.ticker
                            ]
                        elif (
                            _is_high_rvol
                            and _v.action == "NO_TRADE"
                            and _rvol_eval_counts[_v.ticker] >= settings.thresholds.high_rvol_min_evals
                        ):
                            # Exhausted re-eval budget — remove and accept final NO_TRADE
                            _forced_reeval_candidates[:] = [
                                c for c in _forced_reeval_candidates if c.ticker != _v.ticker
                            ]
                            logger.info(
                                "D160 HIGH-RVOL FINAL REJECT: %s — "
                                "NO_TRADE confirmed after %d evaluations",
                                _v.ticker, _rvol_eval_counts[_v.ticker],
                            )

                    # ── D102: Phase 2 Verdict Summary ──
                    _buy_count = sum(1 for v in verdicts if v.action in ("BUY", "STRONG_BUY"))
                    _hold_count = sum(1 for v in verdicts if v.action == "HOLD")
                    _no_trade_count = sum(1 for v in verdicts if v.action == "NO_TRADE")
                    _best_mfcs = max((v.mfcs for v in verdicts if v.mfcs is not None), default=0.0)
                    logger.info(
                        "═══ PHASE 2 VERDICT SUMMARY ═══ "
                        "%d evaluated -> %d BUY, %d HOLD, %d NO_TRADE | "
                        "Best MFCS: %.3f | Threshold: %.2f",
                        len(verdicts), _buy_count, _hold_count, _no_trade_count,
                        _best_mfcs, settings.scoring.mfcs_buy_threshold,
                    )
                    for _v in verdicts:
                        _marker = ">>>" if _v.action in ("BUY", "STRONG_BUY") else "   "
                        logger.info(
                            "  %s %s: %s (MFCS=%.3f, conf=%.2f) %s",
                            _marker, _v.ticker, _v.action,
                            _v.mfcs, _v.confidence,
                            _v.reasoning_summary[:80] if _v.reasoning_summary else "",
                        )

                    # D218: Post verdict summary to Discord
                    try:
                        from src.monitoring.alerts import post_verdict_summary
                        asyncio.ensure_future(post_verdict_summary(
                            verdicts=[
                                {"ticker": v.ticker, "action": v.action, "mfcs": v.mfcs}
                                for v in verdicts
                            ],
                            webhook_url=settings.ops.watchlist_webhook_url,
                        ))
                    except Exception:
                        pass

                    # D113: Heartbeat pulse after Phase 2 evaluation completes
                    try:
                        _d107_watchdog.pulse(phase="PHASE_2")
                    except Exception:
                        pass

                    # D210: Record evaluation results for every verdict
                    for _d210_v in verdicts:
                        try:
                            _d210_sc = orchestrator._scoring_by_ticker.get(_d210_v.ticker, {})
                            session_collector.record_evaluation(
                                ticker=_d210_v.ticker,
                                verdict=_d210_v.action,
                                mfcs=_d210_v.mfcs,
                                mfcs_components=_d210_sc.get("components"),
                                agent_signals=orchestrator._signals_by_ticker.get(_d210_v.ticker, []),
                                qualifies_debate=_d210_sc.get("qualifies_for_debate", False),
                                debate_outcome=getattr(_d210_v, "debate_outcome", None),
                            )
                            if _d210_v.action == "NO_TRADE":
                                _d210_reasons = []
                                if _d210_v.reasoning_summary:
                                    _d210_reasons.append(_d210_v.reasoning_summary[:200])
                                session_collector.record_rejection(
                                    ticker=_d210_v.ticker,
                                    reasons=_d210_reasons,
                                )
                        except Exception:
                            pass

                    # ── Journal: Record every evaluation ──
                    # D83: Match verdicts to candidates by TICKER (not index)
                    # to fix signal bleeding caused by confidence-based sorting.
                    cand_by_ticker = {c.ticker: c for c in top_candidates}
                    for verdict in verdicts:
                        try:
                            cand = cand_by_ticker.get(verdict.ticker)
                            if cand:
                                from src.utils.trade_logger import generate_trade_id
                                j_trade_id = generate_trade_id(verdict.ticker)
                                j_entry = trade_journal.create_entry(
                                    j_trade_id, cand, phase="MARKET_OPEN",
                                )
                                trade_journal.record_input_data(
                                    j_entry,
                                    news_items=news_by_ticker.get(verdict.ticker, []),
                                    market_data=market_data_by_ticker.get(verdict.ticker, {}),
                                )
                                # D83: Use per-candidate signals, NOT _last_agent_signals
                                ticker_signals = orchestrator._signals_by_ticker.get(
                                    verdict.ticker, orchestrator._last_agent_signals
                                )
                                ticker_variants = orchestrator._variant_map_by_ticker.get(
                                    verdict.ticker, orchestrator._last_variant_map
                                )
                                ticker_data_report = orchestrator._data_report_by_ticker.get(
                                    verdict.ticker, orchestrator._last_data_report
                                )
                                trade_journal.record_agent_signals(
                                    j_entry,
                                    ticker_signals,
                                    variant_map=ticker_variants,
                                    data_report=ticker_data_report,
                                )
                                # D83: Use per-candidate scoring, NOT _last_scored_*
                                ticker_scoring = orchestrator._scoring_by_ticker.get(
                                    verdict.ticker, {}
                                )
                                j_entry.mfcs = ticker_scoring.get("mfcs", orchestrator._last_scored_mfcs)
                                j_entry.component_scores = ticker_scoring.get("components", orchestrator._last_scored_components)
                                j_entry.risk_score = ticker_scoring.get("risk_score", orchestrator._last_scored_risk)
                                j_entry.qualifies_for_debate = ticker_scoring.get("qualifies_for_debate", orchestrator._last_scored_qualifies_debate)
                                j_entry.mfcs_buy_threshold = settings.scoring.mfcs_buy_threshold
                                j_entry.debate_threshold = settings.debate.mfcs_debate_threshold
                                j_entry.risk_aversion_lambda = settings.scoring.risk_aversion_lambda
                                trade_journal.record_verdict(j_entry, verdict)
                                trade_journal.flush(j_entry)
                        except Exception as e:
                            logger.debug("Journal recording error: %s", e)

                    # ── D85: Reconcile fast-path entries with full eval ──
                    fp_tickers: set = set()
                    if fast_path_queue:
                        try:
                            fast_path_queue = await fp_reconciler.reconcile(
                                fast_path_queue, verdicts,
                            )
                            fp_tickers = get_fast_path_tickers(fast_path_queue)
                            confirmed = sum(1 for e in fast_path_queue if e.status == "CONFIRMED")
                            cancelled = sum(1 for e in fast_path_queue if e.status == "CANCELLED")
                            logger.info(
                                "D85 RECONCILED: %d confirmed, %d cancelled",
                                confirmed, cancelled,
                            )
                        except Exception as e:
                            logger.warning("D85 reconciliation error: %s", e)

                    # D126: Sort BUY verdicts by momentum score (gap × rvol)
                    # so highest-momentum candidates get capital first.
                    # Mar 26: JBLU (9.9% gap, MFCS 0.256) executed before
                    # UGRO (360% gap, MFCS 0.184) because MFCS order ≠ momentum order.
                    cand_by_ticker = {c.ticker: c for c in top_candidates}
                    _buy_verdicts = [v for v in verdicts if v.action in ("BUY", "STRONG_BUY")]
                    _other_verdicts = [v for v in verdicts if v.action not in ("BUY", "STRONG_BUY")]
                    _buy_verdicts.sort(
                        key=lambda v: abs(getattr(cand_by_ticker.get(v.ticker), 'gap_pct', 0))
                        * getattr(cand_by_ticker.get(v.ticker), 'rvol', 0),
                        reverse=True,
                    )
                    if _buy_verdicts:
                        _momentum_order = ", ".join(
                            f"{v.ticker}({abs(getattr(cand_by_ticker.get(v.ticker), 'gap_pct', 0)) * getattr(cand_by_ticker.get(v.ticker), 'rvol', 0):.1f})"
                            for v in _buy_verdicts
                        )
                        logger.info("D126 MOMENTUM ORDER: %s", _momentum_order)

                    for verdict in _buy_verdicts + _other_verdicts:
                        if verdict.action in ("BUY", "STRONG_BUY"):
                            # D211: Record phantom verdict BEFORE any gate can block it
                            _cand_for_phantom = cand_by_ticker.get(verdict.ticker)
                            _phantom.record(
                                ticker=verdict.ticker,
                                entry_price=verdict.entry_price,
                                stop_loss=verdict.stop_loss,
                                target_prices=list(verdict.target_prices),
                                position_size_pct=verdict.position_size_pct,
                                mfcs=verdict.mfcs,
                                confidence=verdict.confidence,
                                direction=getattr(verdict, "direction", "long"),
                                blocked_by="PENDING",
                                reasoning_summary=verdict.reasoning_summary,
                                gap_pct=_cand_for_phantom.gap_pct if _cand_for_phantom else 0.0,
                                rvol=_cand_for_phantom.rvol if _cand_for_phantom else 0.0,
                                market_cap=_cand_for_phantom.market_cap if _cand_for_phantom else None,
                                kelly_tier=verdict.kelly_tier,
                                vix_level=orchestrator._vix_level,
                            )

                            # D150: Re-check before each entry. Circuit breaker
                            # halts ALL entries; allocation guard skips THIS entry
                            # but continues to try smaller tiers that might fit.
                            if bridge.position_manager.is_circuit_breaker_active:
                                logger.warning(
                                    "D150: Circuit breaker active — "
                                    "halting ALL remaining entries at %s",
                                    verdict.ticker,
                                )
                                # doc 272 VLL: daily-drawdown circuit terminal (halt-all)
                                try:
                                    from src.ops.verdict_ledger import vll_emit
                                    vll_emit("BLOCKED_CIRCUIT", verdict.ticker,
                                             reason="circuit breaker active (halts all entries)",
                                             path="MAIN")
                                except Exception:
                                    pass
                                break
                            if not bridge.position_manager.can_enter_new_position():
                                logger.info(
                                    "D150: %s blocked (max positions) — skipping",
                                    verdict.ticker,
                                )
                                _phantom.update_gate(verdict.ticker, "D150_MAX_POSITIONS")
                                # doc 272 VLL: max-positions terminal
                                try:
                                    from src.ops.verdict_ledger import vll_emit
                                    vll_emit("BLOCKED_MAX_POSITIONS", verdict.ticker,
                                             reason="position manager at max positions", path="MAIN")
                                except Exception:
                                    pass
                                continue

                            # D85: Skip full-eval execution for tickers already in fast-path
                            if verdict.ticker in fp_tickers:
                                logger.info(
                                    "D85: Skipping %s — already managed by fast-path",
                                    verdict.ticker,
                                )
                                _phantom.update_gate(verdict.ticker, "D85_FAST_PATH")
                                # doc 272 VLL: fast-path dedup terminal
                                try:
                                    from src.ops.verdict_ledger import vll_emit
                                    vll_emit("BLOCKED_FP_DEDUP", verdict.ticker,
                                             reason="already managed by fast-path", path="MAIN")
                                except Exception:
                                    pass
                                continue

                            # D56: Skip if we already hold this ticker (prevents duplicates)
                            if position_manager.has_position(verdict.ticker):
                                logger.info(
                                    "D56: Skipping %s — already holding position",
                                    verdict.ticker,
                                )
                                _phantom.update_gate(verdict.ticker, "D56_DUPLICATE")
                                # doc 272 VLL: already-held terminal
                                try:
                                    from src.ops.verdict_ledger import vll_emit
                                    vll_emit("BLOCKED_ALREADY_HELD", verdict.ticker,
                                             reason="already holding position", path="MAIN")
                                except Exception:
                                    pass
                                continue

                            # D94b: Skip if ticker was stopped out this session
                            # March 4 post-mortem: CANF stopped at $7.01, re-bought
                            # at $6.84, stopped again at $6.15. Don't chase losers.
                            # D216 FIX: Also check if position manager RECENTLY had this
                            # ticker (covers the race condition where stop fills between
                            # Phase 3 detection cycles).
                            if verdict.ticker in _stopped_out_tickers:
                                logger.warning(
                                    "D94b: Skipping %s — stopped out earlier this session",
                                    verdict.ticker,
                                )
                                _phantom.update_gate(verdict.ticker, "D94B_STOPPED_OUT")
                                # doc 272 VLL: stop-out cooldown terminal
                                try:
                                    from src.ops.verdict_ledger import vll_emit
                                    vll_emit("BLOCKED_STOP_COOLDOWN", verdict.ticker,
                                             reason="stopped out earlier this session", path="MAIN")
                                except Exception:
                                    pass
                                continue
                            # D216: Check if we JUST had this position (race condition fix)
                            # If position_manager has no position but we had a BUY verdict
                            # for this ticker in the last 5 minutes, it might have been
                            # stopped out between Phase 3 cycles
                            if hasattr(position_manager, '_recently_closed') and verdict.ticker in getattr(position_manager, '_recently_closed', set()):
                                logger.warning(
                                    "D216: Skipping %s — recently closed (race condition guard)",
                                    verdict.ticker,
                                )
                                _stopped_out_tickers.add(verdict.ticker)
                                # doc 272 VLL: recently-closed race-guard terminal
                                try:
                                    from src.ops.verdict_ledger import vll_emit
                                    vll_emit("BLOCKED_RECENTLY_CLOSED", verdict.ticker,
                                             reason="recently closed (race condition guard)", path="MAIN")
                                except Exception:
                                    pass
                                continue

                            # ── Portfolio risk check (ADR-024) ──
                            risk_check = portfolio_risk.check_entry(
                                ticker=verdict.ticker,
                                stop_loss_pct=2.0,  # Default stop distance
                                positions=bridge.position_manager.open_positions,
                            )
                            if not risk_check.allowed:
                                logger.warning(
                                    "PORTFOLIO RISK BLOCKED: %s — %s",
                                    verdict.ticker, risk_check.reason,
                                )
                                from src.monitoring.metrics import get_metrics as _get_metrics
                                _get_metrics().risk_vetoes.inc()
                                _phantom.update_gate(verdict.ticker, "PORTFOLIO_RISK")
                                # doc 272 VLL: portfolio-risk (ADR-024) terminal
                                try:
                                    from src.ops.verdict_ledger import vll_emit
                                    vll_emit("BLOCKED_PORTFOLIO_RISK", verdict.ticker,
                                             reason=str(risk_check.reason)[:120], path="MAIN")
                                except Exception:
                                    pass
                                continue

                            # Retrieve scored from orchestrator (cached during eval)
                            _scored = orchestrator._scored_by_ticker.get(verdict.ticker)

                            # ── D199: Universe Tier Classification ──────────────────────
                            # Classify each BUY candidate into its universe tier (MOMENTUM
                            # or CATALYST). CATALYST stocks override the faller reject
                            # threshold and disable short selling — institutional mid-caps
                            # are higher quality and shouldn't be treated like promotional faders.
                            _d199_cand_obj = cand_by_ticker.get(verdict.ticker)
                            _d199_tier = None
                            _d199_faller_threshold_override = None
                            _d199_short_enabled_override = None
                            _d199_obs_minutes_override = None
                            if _d199_cand_obj is not None:
                                try:
                                    from src.data.universe_tiers import (
                                        UniverseTier,
                                        universe_classifier as _uc,
                                    )
                                    _d199_price = _d199_cand_obj.current_price or 0.0
                                    _d199_mcap = _d199_cand_obj.market_cap
                                    _d199_gap = abs(_d199_cand_obj.gap_pct or 0.0)
                                    _d199_rvol = _d199_cand_obj.rvol or 0.0
                                    _d199_adv = _d199_cand_obj.avg_daily_volume or 0
                                    _d199_dolvol = _d199_adv * _d199_price
                                    _d199_tier = _uc.classify(
                                        verdict.ticker,
                                        _d199_price,
                                        _d199_mcap,
                                        _d199_gap,
                                        _d199_rvol,
                                        _d199_dolvol,
                                    )
                                    if _d199_tier == UniverseTier.CATALYST:
                                        _d199_overrides = _uc.should_override_params(_d199_tier)
                                        _d199_faller_threshold_override = _d199_overrides["faller_reject_threshold"]
                                        _d199_short_enabled_override = _d199_overrides["short_selling_enabled"]
                                        _d199_obs_minutes_override = _d199_overrides["observation_minutes"]
                                        logger.info(
                                            "D199 CATALYST TIER: %s mcap=$%.1fB gap=%.1f%% rvol=%.1fx "
                                            "→ faller_threshold=%.2f, short=%s, obs=%.0fmin",
                                            verdict.ticker,
                                            (_d199_mcap or 0) / 1e9,
                                            _d199_gap * 100,
                                            _d199_rvol,
                                            _d199_faller_threshold_override,
                                            _d199_short_enabled_override,
                                            _d199_obs_minutes_override,
                                        )
                                    else:
                                        logger.debug(
                                            "D199 MOMENTUM TIER: %s (price=%.2f, mcap=%s)",
                                            verdict.ticker,
                                            _d199_price,
                                            f"${(_d199_mcap or 0)/1e6:.0f}M" if _d199_mcap else "unknown",
                                        )
                                except Exception as _d199_err:
                                    logger.debug("D199 tier classification error for %s: %s", verdict.ticker, _d199_err)

                            # ── D200-E4: Catalyst Confirmation Gate ─────────────────────
                            # Blocks BUY verdicts for stocks with no confirmed catalyst.
                            # All 14 live losses had catalyst_type="unknown" — this gate
                            # prevents trading promotional pumps with no real news.
                            if getattr(settings.universe, "require_catalyst", False):
                                _e4_catalyst = None
                                _e4_signals = orchestrator._signals_by_ticker.get(verdict.ticker, [])
                                if not _e4_signals:
                                    # BUG-FIX: Log when signals are missing (was silent veto)
                                    logger.debug("D200-E4: No signals for %s — defaulting to block", verdict.ticker)
                                for _e4_sig in (_e4_signals if isinstance(_e4_signals, list) else []):
                                    if isinstance(_e4_sig, dict) and _e4_sig.get("agent_id") == "news_agent":
                                        _e4_catalyst = _e4_sig.get("catalyst_type")
                                    elif hasattr(_e4_sig, "agent_id") and _e4_sig.agent_id == "news_agent":
                                        _e4_catalyst = getattr(_e4_sig, "catalyst_type", None)
                                if _e4_catalyst in (None, "NONE", "None", "null", "unknown"):
                                    # doc 178 posture inversion: DOWNGRADE-not-BLOCK on high MFCS.
                                    # The no-catalyst cohort historically lost (D200 rationale),
                                    # but it ALSO contains the no-news low-float squeezes the thesis
                                    # targets (NCPL ran +38.5%, AMSS — both blocked 5/28 at MFCS
                                    # 0.52-0.55, partly because the news agent had TIMED OUT). Admit
                                    # only the highest-MFCS names, at half size to bound pump risk.
                                    _e4_mfcs = verdict.mfcs or 0.0
                                    _e4_thr = getattr(settings.universe, "catalyst_gate_downgrade_mfcs_threshold", 0.45)
                                    if (getattr(settings.universe, "catalyst_gate_downgrade_high_mfcs", False)
                                            and _e4_mfcs >= _e4_thr):
                                        _e4_mult = getattr(settings.universe, "catalyst_gate_downgrade_qty_mult", 0.5)
                                        verdict = verdict.model_copy(update={
                                            "qty_multiplier": min(verdict.qty_multiplier, _e4_mult),
                                        })
                                        logger.warning(
                                            "D200-E4 CATALYST GATE: %s DOWNGRADED (not blocked) — no "
                                            "confirmed catalyst but MFCS=%.3f>=%.2f → entering at "
                                            "qty_mult=%.2f (doc 178 posture inversion)",
                                            verdict.ticker, _e4_mfcs, _e4_thr, verdict.qty_multiplier,
                                        )
                                        # fall through — admit at reduced size
                                    elif (_e4_d272 := _d272_absence_fail_open(
                                            _e4_signals, catalyst_none_trigger=True)):
                                        # doc 272 LEVER-1 (env MOMENTUM_EMPTY_NOT_BEAR, default
                                        # OFF — branch unreachable when unset): catalyst ABSENCE
                                        # != bearish data. Fail-open at reduced size via the
                                        # doc-178 downgrade mechanism instead of blocking.
                                        _e4_d272_mult = getattr(settings.universe, "catalyst_gate_downgrade_qty_mult", 0.5)
                                        verdict = verdict.model_copy(update={
                                            "qty_multiplier": min(verdict.qty_multiplier, _e4_d272_mult),
                                        })
                                        logger.warning(
                                            "D272 EMPTY!=BEAR fail-open at %.2fx size: %s D200-E4 "
                                            "catalyst ABSENCE (%s, catalyst_type=%s, MFCS=%.3f) — "
                                            "admitted, not blocked",
                                            verdict.qty_multiplier, verdict.ticker, _e4_d272,
                                            _e4_catalyst, _e4_mfcs,
                                        )
                                        try:
                                            from src.ops.verdict_ledger import vll_emit
                                            vll_emit("DOWNGRADED_ABSENCE", verdict.ticker,
                                                     reason=_e4_d272, gate="D200_E4",
                                                     qty_mult=round(float(verdict.qty_multiplier), 2),
                                                     mfcs=round(float(_e4_mfcs), 3), path="MAIN")
                                        except Exception:
                                            pass
                                        # fall through — admit at reduced size
                                    else:
                                        logger.warning(
                                            "D200-E4 CATALYST GATE: %s blocked — no confirmed catalyst "
                                            "(catalyst_type=%s, MFCS=%.3f)",
                                            verdict.ticker, _e4_catalyst, _e4_mfcs,
                                        )
                                        try:
                                            from src.monitoring.metrics import get_metrics as _get_metrics
                                            _get_metrics().risk_vetoes.inc()
                                        except Exception:
                                            pass
                                        _phantom.update_gate(verdict.ticker, "D200_NO_CATALYST")
                                        # doc 268 VLL: the 6/8-6/9 silent-verdict killer #1 — make it loud.
                                        try:
                                            from src.ops.verdict_ledger import vll_emit
                                            vll_emit("BLOCKED_CATALYST_GATE", verdict.ticker,
                                                     reason=f"catalyst_type={_e4_catalyst}",
                                                     mfcs=round(float(_e4_mfcs), 3), path="MAIN")
                                        except Exception:
                                            pass
                                        continue

                            # ── D204: News Agent Confidence Gate ────────────────────────
                            # D203 arena: news_agent BULL has 16.7% WR (best predictor).
                            # But not all BULL signals are equal. The 3 winners had
                            # confidence >= 0.40. Require news_agent to be BULL with
                            # minimum confidence to proceed. This further filters noise.
                            _d204_news_ok = False
                            _d204_news_conf = 0.0
                            _d204_news_sig = "NEUTRAL"
                            _d204_signals = orchestrator._signals_by_ticker.get(verdict.ticker, [])
                            for _d204_s in (_d204_signals if isinstance(_d204_signals, list) else []):
                                _d204_aid = _d204_s.get("agent_id") if isinstance(_d204_s, dict) else getattr(_d204_s, "agent_id", "")
                                if _d204_aid == "news_agent":
                                    _d204_news_sig = (_d204_s.get("signal") if isinstance(_d204_s, dict) else getattr(_d204_s, "signal", "NEUTRAL"))
                                    _d204_news_conf = (_d204_s.get("confidence", 0) if isinstance(_d204_s, dict) else getattr(_d204_s, "confidence", 0)) or 0
                                    if _d204_news_sig in ("BULL", "STRONG_BULL") and _d204_news_conf >= 0.30:
                                        _d204_news_ok = True
                                    break
                            if not _d204_news_ok:
                                # doc 178 posture inversion: DOWNGRADE-not-BLOCK on high MFCS.
                                # Critical for the 'blind funnel': when news_agent is EMPTY /
                                # timed-out, news_sig=NEUTRAL/conf=0 — do NOT block a strong
                                # technical+volume squeeze just because the news call didn't
                                # return. Admit high-MFCS names at reduced size instead.
                                _d204_mfcs = verdict.mfcs or 0.0
                                _d204_thr = getattr(settings.universe, "catalyst_gate_downgrade_mfcs_threshold", 0.45)
                                if (getattr(settings.universe, "catalyst_gate_downgrade_high_mfcs", False)
                                        and _d204_mfcs >= _d204_thr):
                                    _d204_mult = getattr(settings.universe, "catalyst_gate_downgrade_qty_mult", 0.5)
                                    verdict = verdict.model_copy(update={
                                        "qty_multiplier": min(verdict.qty_multiplier, _d204_mult),
                                    })
                                    logger.warning(
                                        "D204 NEWS GATE: %s DOWNGRADED (not blocked) — news=%s "
                                        "conf=%.2f but MFCS=%.3f>=%.2f → qty_mult=%.2f (doc 178)",
                                        verdict.ticker, _d204_news_sig, _d204_news_conf,
                                        _d204_mfcs, _d204_thr, verdict.qty_multiplier,
                                    )
                                elif (_d204_d272 := _d272_absence_fail_open(_d204_signals)):
                                    # doc 272 LEVER-1 (env MOMENTUM_EMPTY_NOT_BEAR, default OFF —
                                    # branch unreachable when unset): news ABSENCE != bearish
                                    # news. Fail-open at reduced size via the doc-178 downgrade
                                    # mechanism instead of blocking.
                                    _d204_d272_mult = getattr(settings.universe, "catalyst_gate_downgrade_qty_mult", 0.5)
                                    verdict = verdict.model_copy(update={
                                        "qty_multiplier": min(verdict.qty_multiplier, _d204_d272_mult),
                                    })
                                    logger.warning(
                                        "D272 EMPTY!=BEAR fail-open at %.2fx size: %s D204 news "
                                        "ABSENCE (%s, MFCS=%.3f) — admitted, not blocked",
                                        verdict.qty_multiplier, verdict.ticker, _d204_d272, _d204_mfcs,
                                    )
                                    try:
                                        from src.ops.verdict_ledger import vll_emit
                                        vll_emit("DOWNGRADED_ABSENCE", verdict.ticker,
                                                 reason=_d204_d272, gate="D204",
                                                 qty_mult=round(float(verdict.qty_multiplier), 2),
                                                 mfcs=round(float(_d204_mfcs), 3), path="MAIN")
                                    except Exception:
                                        pass
                                    # fall through — admit at reduced size
                                else:
                                    logger.warning(
                                        "D204 NEWS CONFIDENCE GATE: %s blocked — news_agent=%s conf=%.2f "
                                        "(need BULL/STRONG_BULL with conf>=0.30, MFCS=%.3f)",
                                        verdict.ticker, _d204_news_sig, _d204_news_conf, _d204_mfcs,
                                    )
                                    _phantom.update_gate(verdict.ticker, "D204_NEWS_CONFIDENCE")
                                    # doc 272 VLL: MAIN-path news-gate terminal (doc 268 wired
                                    # only the VWAP/RESCAN copies of this gate — this was the gap).
                                    try:
                                        from src.ops.verdict_ledger import vll_emit
                                        vll_emit("BLOCKED_NEWS_GATE", verdict.ticker,
                                                 reason=f"news={_d204_news_sig} conf={_d204_news_conf:.2f} < BULL/0.30",
                                                 mfcs=round(float(_d204_mfcs), 3), path="MAIN")
                                    except Exception:
                                        pass
                                    continue

                            # ── doc 178 Tier 4: ELITE sizing press ──────────────────────
                            # Press the highest-conviction tail (doc 176: "press size when
                            # the signal is strong"). Independent of the qty_multiplier
                            # downgrade above — a no-catalyst ELITE name nets ~base
                            # (elite_risk × 0.5 qty_mult), a clean ELITE name presses up.
                            # max() never reduces a higher Kelly-tier risk already set.
                            if getattr(settings.execution, "elite_sizing_press_enabled", False):
                                _elite_thr = getattr(settings.execution, "elite_sizing_mfcs_threshold", 0.50)
                                if (verdict.mfcs or 0.0) >= _elite_thr:
                                    _elite_risk = getattr(settings.execution, "elite_risk_per_trade_pct", 0.02)
                                    _cur_risk = verdict.risk_per_trade_pct or 0.0
                                    if _elite_risk > _cur_risk:
                                        verdict = verdict.model_copy(update={"risk_per_trade_pct": _elite_risk})
                                        logger.info(
                                            "doc178 ELITE SIZING: %s MFCS=%.3f>=%.2f → risk_per_trade "
                                            "%.1f%%->%.1f%% (press the tail)",
                                            verdict.ticker, verdict.mfcs, _elite_thr,
                                            _cur_risk * 100, _elite_risk * 100,
                                        )

                            # ── D160 / D161: Faller Risk Gate + Short Selling Path ──────
                            # Runs AFTER MFCS (direction) and BEFORE order submission.
                            # Scores candidates on microstructure signals that predict
                            # "gap and fade" vs "gap and continue".
                            #
                            # D160: Rejects predicted faders and reduces size for borderline.
                            # D161: HIGH-score faders (>= min_faller_score) become SHORT
                            #   candidates instead of outright rejects. This turns the ARTL/SST
                            #   pattern (manipulation 85%, no catalyst, wide spread) into a
                            #   profitable short trade rather than a missed opportunity.
                            # D162: Lowered long-block threshold 0.65→0.60 after 0% win rate
                            #   on 18 live trades (Mar 2026 backtest). Fader stocks were still
                            #   being taken LONG at scores 0.60-0.65. Now: 0.60-0.65 routes to
                            #   short evaluation — if they don't qualify, REJECTED, never longed.
                            #
                            # Long tiers (D162):
                            #   0.00-0.30 → FULL long
                            #   0.30-0.50 → 75% long
                            #   0.50-0.60 → 50% long  (was 0.50-0.65 before D162)
                            #   0.60+     → NO LONG — routes to short evaluation
                            # Short tiers:
                            #   0.60-0.80 → 50% SHORT (cautious — score must also pass req checks)
                            #   > 0.80    → FULL SHORT  (high-confidence fader)
                            if settings.faller.enabled and _scored is not None:
                                _cand_obj = cand_by_ticker.get(verdict.ticker)
                                _indicators = market_data_by_ticker.get(
                                    verdict.ticker, {}
                                ).get("indicators", {})
                                if _cand_obj is not None:
                                    try:
                                      # D211: Wire D191-D194 enrichment data to faller scoring
                                      _d191_sec = _sec_pfetch_results.get(verdict.ticker) if _sec_pfetch_results else None
                                      _d192_si = _si_results.get(verdict.ticker) if _si_results else None
                                      _d193_sv = None
                                      try:
                                          _d193_sv = sentiment_tracker.get_result(verdict.ticker)
                                      except Exception:
                                          pass
                                      # D212: Compute float rotation + VWAP deviation + order flow
                                      _d212_fr = None
                                      try:
                                          _d212_vol = _market_ws_client.get_volume(verdict.ticker)
                                          _d212_float = _cand_obj.float_shares
                                          if _d212_vol > 0 and _d212_float:
                                              _d212_fr = _float_rotation.compute(verdict.ticker, _d212_vol, _d212_float)
                                      except Exception:
                                          pass
                                      _d212_vwap_dev = None
                                      try:
                                          _d212_vwap = _market_ws_client.get_vwap(verdict.ticker)
                                          if _d212_vwap > 0 and _cand_obj.current_price > 0:
                                              _d212_vwap_dev = (_cand_obj.current_price - _d212_vwap) / _d212_vwap
                                      except Exception:
                                          pass
                                      _d212_of = None
                                      try:
                                          _d212_trades = _trade_buffers.get(verdict.ticker, [])
                                          if _d212_trades:
                                              _d212_bid = _cand_obj.current_price * 0.998
                                              _d212_ask = _cand_obj.current_price * 1.002
                                              _d212_of = order_flow_analyzer.analyze_trades(
                                                  verdict.ticker, _d212_trades, _d212_bid, _d212_ask,
                                              )
                                      except Exception:
                                          pass
                                      _faller_assessment = _faller_detector.score(
                                          candidate=_cand_obj,
                                          scored=_scored,
                                          indicators=_indicators,
                                          sec_result=_d191_sec,
                                          short_interest_result=_d192_si,
                                          sentiment_velocity_result=_d193_sv,
                                          order_flow_result=_d212_of,
                                          float_rotation_result=_d212_fr,
                                          vwap_deviation_pct=_d212_vwap_dev,
                                      )
                                    except Exception as _fe:
                                      # D160: Faller gate fails OPEN — unexpected exception
                                      # should never block a valid trade.
                                      logger.error(
                                          "D160 FALLER GATE ERROR for %s: %s — "
                                          "proceeding with FULL position (fail-open)",
                                          verdict.ticker, _fe,
                                      )
                                      _faller_assessment = None
                                    # D160: Persist faller score to trade journal for arena analysis.
                                    if _faller_assessment is not None:
                                        try:
                                            for _j_tid, _j_ent in trade_journal._entries.items():
                                                if _j_ent.ticker == verdict.ticker and _j_ent.action == "BUY":
                                                    _j_ent.faller_score = round(_faller_assessment.score, 4)
                                                    _j_ent.faller_reject = _faller_assessment.reject
                                                    _j_ent.faller_position_multiplier = round(
                                                        _faller_assessment.position_multiplier, 3
                                                    )
                                                    break
                                        except Exception:
                                            pass
                                    # D199: CATALYST tier override — use higher faller
                                    # threshold (0.70 vs 0.60) and disable short path.
                                    # Institutional mid-caps have less fader risk than
                                    # promotional low-floats.
                                    _effective_reject = (
                                        _faller_assessment is not None
                                        and _faller_assessment.reject
                                    )
                                    if (
                                        _effective_reject
                                        and _d199_faller_threshold_override is not None
                                        and _faller_assessment is not None
                                        and _faller_assessment.score < _d199_faller_threshold_override
                                    ):
                                        # Score is between global threshold (0.60) and
                                        # CATALYST threshold (0.70) — allow the trade.
                                        logger.info(
                                            "D199 CATALYST OVERRIDE: %s faller_score=%.3f < CATALYST threshold=%.2f — "
                                            "overriding REJECT → ALLOW (global threshold=%.2f)",
                                            verdict.ticker,
                                            _faller_assessment.score,
                                            _d199_faller_threshold_override,
                                            settings.faller.reject_threshold,
                                        )
                                        _effective_reject = False

                                    if _effective_reject:
                                        _short_cfg = settings.short_selling
                                        # ── D161: SHORT PATH ────────────────────────────
                                        # High-faller stocks become short candidates instead
                                        # of outright rejects. All requirements must pass.
                                        # D199: CATALYST tier disables short selling entirely.
                                        _d199_short_allowed = (
                                            _d199_short_enabled_override
                                            if _d199_short_enabled_override is not None
                                            else True
                                        )
                                        if (
                                            _short_cfg.enabled
                                            and _d199_short_allowed
                                            and _faller_assessment.score >= _short_cfg.min_faller_score
                                        ):
                                            # Check RVOL requirement
                                            _s_rvol = _cand_obj.rvol or 0.0
                                            _s_gap = abs(_cand_obj.gap_pct or 0.0)
                                            _s_dolvol = _faller_assessment.dollar_volume
                                            _s_rvol_ok = _s_rvol >= _short_cfg.min_rvol_short
                                            _s_dolvol_ok = _s_dolvol >= _short_cfg.min_dollar_volume_short
                                            _s_gap_ok = _s_gap >= _short_cfg.min_gap_pct_short

                                            # Check shortability via Alpaca asset endpoint
                                            _s_shortable = False
                                            try:
                                                _s_asset = await client.check_asset_tradable(verdict.ticker)
                                                _s_shortable = (
                                                    _s_asset.get("shortable", False)
                                                    and _s_asset.get("easy_to_borrow", False)
                                                )
                                            except Exception as _sae:
                                                logger.debug(
                                                    "D161 SHORT: shortability check failed for %s: %s — "
                                                    "defaulting to not shortable",
                                                    verdict.ticker, _sae,
                                                )

                                            if not _s_shortable:
                                                logger.warning(
                                                    "D161 SHORT REJECTED %s: not shortable/ETB "
                                                    "(faller_score=%.3f) — falling back to REJECT",
                                                    verdict.ticker, _faller_assessment.score,
                                                )
                                            elif not _s_rvol_ok:
                                                logger.warning(
                                                    "D161 SHORT REJECTED %s: RVOL %.1fx < %.1fx required "
                                                    "(faller_score=%.3f) — falling back to REJECT",
                                                    verdict.ticker, _s_rvol,
                                                    _short_cfg.min_rvol_short, _faller_assessment.score,
                                                )
                                            elif not _s_dolvol_ok:
                                                logger.warning(
                                                    "D161 SHORT REJECTED %s: dolvol $%.0fK < $%.0fK required "
                                                    "(faller_score=%.3f) — falling back to REJECT "
                                                    "(SST pattern: too illiquid to cover)",
                                                    verdict.ticker, _s_dolvol / 1000,
                                                    _short_cfg.min_dollar_volume_short / 1000,
                                                    _faller_assessment.score,
                                                )
                                            elif not _s_gap_ok:
                                                logger.warning(
                                                    "D161 SHORT REJECTED %s: gap %.0f%% < %.0f%% required "
                                                    "(faller_score=%.3f) — falling back to REJECT",
                                                    verdict.ticker, _s_gap * 100,
                                                    _short_cfg.min_gap_pct_short * 100, _faller_assessment.score,
                                                )
                                            else:
                                                # ── All checks passed — build SHORT verdict ──
                                                _s_size_mult = (
                                                    1.0
                                                    if _faller_assessment.score >= _short_cfg.full_size_faller_score
                                                    else _short_cfg.reduced_size_multiplier
                                                )
                                                _s_stop = round(
                                                    verdict.entry_price * (1 + _short_cfg.stop_pct_above_entry), 2
                                                )
                                                _s_targets = [
                                                    round(verdict.entry_price * (1 + t), 2)
                                                    for t in _short_cfg.target_pcts
                                                ]
                                                _short_verdict = verdict.model_copy(update={
                                                    "direction": "short",
                                                    "stop_loss": _s_stop,
                                                    "target_prices": _s_targets,
                                                    "position_size_pct": max(
                                                        verdict.position_size_pct * _s_size_mult, 0.01
                                                    ),
                                                })
                                                _s_zone = (
                                                    "FULL"
                                                    if _faller_assessment.score >= _short_cfg.full_size_faller_score
                                                    else "50%"
                                                )
                                                logger.warning(
                                                    "D161 SHORT ENTRY %s: score=%.3f → %s SHORT | "
                                                    "entry=%.2f stop=%.2f (+%.0f%%) targets=%s | "
                                                    "rvol=%.1fx dolvol=$%.0fK gap=%.0f%% | "
                                                    "top_factors=%s",
                                                    verdict.ticker, _faller_assessment.score, _s_zone,
                                                    _short_verdict.entry_price, _s_stop,
                                                    _short_cfg.stop_pct_above_entry * 100,
                                                    [f"${t:.2f}" for t in _s_targets],
                                                    _s_rvol, _s_dolvol / 1000, _s_gap * 100,
                                                    " | ".join(_faller_assessment.top_factors[:3]),
                                                )
                                                # doc 272 VLL: long verdict re-routed to D161 SHORT —
                                                # informational terminal for the LONG attempt; the short
                                                # execution gets its own bridge terminals (SUBMITTED/etc).
                                                try:
                                                    from src.ops.verdict_ledger import vll_emit
                                                    vll_emit("ROUTED_SHORT", verdict.ticker,
                                                             reason=f"faller_score={_faller_assessment.score:.3f} -> short path",
                                                             path="MAIN")
                                                except Exception:
                                                    pass
                                                # D161: Update journal — mark as short candidate
                                                try:
                                                    for _j_tid, _j_ent in trade_journal._entries.items():
                                                        if _j_ent.ticker == verdict.ticker:
                                                            _j_ent.direction = "short"
                                                            _j_ent.faller_short_candidate = True
                                                            break
                                                except Exception:
                                                    pass
                                                order = await bridge.execute_verdict(
                                                    _short_verdict, scored=_scored
                                                )
                                                if order is None:
                                                    logger.warning(
                                                        "D161 SHORT BLOCKED: %s short verdict "
                                                        "rejected by execution layer",
                                                        verdict.ticker,
                                                    )
                                                else:
                                                    session_trades += 1
                                                    logger.warning(
                                                        "D161 SHORT FILLED: %s qty=%d @ $%.2f "
                                                        "(order_id=%s, stop_order_id=%s)",
                                                        order.ticker, order.qty,
                                                        order.submitted_price, order.order_id,
                                                        order.stop_order_id,
                                                    )
                                                    # D215: Unified execution recording
                                                    _exec_recorder.record_execution(
                                                        ticker=order.ticker, side="sell",
                                                        fill_price=order.fill_price if order.fill_price and order.fill_price > 0 else order.submitted_price,
                                                        signal_price=order.submitted_price,
                                                        qty=order.qty, order_id=order.order_id,
                                                        execution_path="D161_FALLER_SHORT",
                                                        gap_pct=abs(_cand_obj.gap_pct) if _cand_obj else 0.0,
                                                        rvol=_cand_obj.rvol if _cand_obj else 0.0,
                                                        mfcs=verdict.mfcs,
                                                        direction="short",
                                                        stop_loss=_short_verdict.stop_loss,
                                                        target_prices=list(_short_verdict.target_prices),
                                                        faller_score=_faller_assessment.score if _faller_assessment else None,
                                                        vix_level=orchestrator._vix_level,
                                                        verdict=_short_verdict, scored=_scored, candidate=_cand_obj,
                                                    )
                                                    # ── D161: Persist short position state ──
                                                    # Shorts skip the long-path stop conversion
                                                    # (they use the OTO buy-stop as-is), but still
                                                    # need state persistence for crash recovery and
                                                    # stop registration for Phase 3 monitoring.
                                                    try:
                                                        state_mgr.update_position(
                                                            ticker=order.ticker,
                                                            qty=order.qty,
                                                            entry_price=order.submitted_price,
                                                            signal_price=order.signal_price,
                                                            stop_loss=_short_verdict.stop_loss,
                                                            target_prices=list(_short_verdict.target_prices),
                                                            tranches_filled=0,
                                                            remaining_qty=order.qty,
                                                            realized_pnl=0.0,
                                                            entry_order_id=order.order_id,
                                                            stop_order_id=order.stop_order_id,
                                                            tranche_order_ids=[],
                                                            opened_at=order.timestamp.isoformat(),
                                                            position_tier=3,
                                                            kelly_tier=getattr(_short_verdict, "kelly_tier", 1),
                                                            gap_pct=getattr(_short_verdict, "gap_pct", 0.0),
                                                            direction="short",  # D202: persist for short crash-recovery
                                                        )
                                                        state_mgr.save()
                                                    except Exception as _s_se:
                                                        logger.debug("D161: State save error for short %s: %s", order.ticker, _s_se)
                                                    # Register stop for Phase 3 monitoring
                                                    try:
                                                        _s_stop_oid = order.stop_order_id or order.order_id
                                                        stop_resubmitter.register_stop(
                                                            ticker=order.ticker,
                                                            order_id=_s_stop_oid,
                                                            stop_price=_short_verdict.stop_loss,
                                                            qty=order.qty,
                                                        )
                                                        logger.info(
                                                            "D161 SHORT STOP registered: %s @ $%.2f (oid=%s)",
                                                            order.ticker, _short_verdict.stop_loss, _s_stop_oid,
                                                        )
                                                    except Exception as _s_re:
                                                        logger.warning("D161: Stop registration error for short %s: %s", order.ticker, _s_re)
                                                # Whether short succeeded or not, do NOT fall
                                                # through to the long path for this ticker.
                                                continue
                                        # ── SHORT path not taken — original reject ───────
                                        _d160_threshold = (
                                            _d199_faller_threshold_override
                                            if _d199_faller_threshold_override is not None
                                            else settings.faller.reject_threshold
                                        )
                                        _d160_tier_tag = (
                                            "D199-CATALYST"
                                            if _d199_short_enabled_override is False
                                            else "D160"
                                        )
                                        logger.warning(
                                            "%s FALLER GATE BLOCKED: %s "
                                            "(MFCS=%.3f, faller_score=%.3f > %.2f) — "
                                            "predicted fader, skipping execution",
                                            _d160_tier_tag,
                                            verdict.ticker, verdict.mfcs,
                                            _faller_assessment.score,
                                            _d160_threshold,
                                        )
                                        try:
                                            from src.monitoring.metrics import get_metrics as _get_metrics
                                            _get_metrics().risk_vetoes.inc()
                                        except Exception:
                                            pass
                                        _phantom.update_gate(verdict.ticker, "D160_FALLER_REJECT")
                                        # doc 191: ensure _or_sig is defined for the exemption
                                        # check after the shadow block (the inner try may not run).
                                        _or_sig = None
                                        # doc 182: grade this rejection post-close — was the faller
                                        # block right (did the name fade) or wrong (did it run)?
                                        # Write-only shadow; never raises into the trade loop.
                                        try:
                                            from src.shadow.rejection_outcome_shadow import (
                                                log_rejection_for_grading as _d182_grade,
                                            )
                                            _d182_cand = cand_by_ticker.get(verdict.ticker)
                                            # doc 187: VWAP interaction (signed % from VWAP) —
                                            # a top confirmed continuation feature.
                                            _d182_vw = getattr(_faller_assessment, "vwap", None) if _faller_assessment else None
                                            _d182_cp = getattr(_faller_assessment, "current_price", None) if _faller_assessment else None
                                            _d182_vwd = (
                                                ((_d182_cp - _d182_vw) / _d182_vw)
                                                if (_d182_vw and _d182_cp) else None
                                            )
                                            # doc 188: the validated continuation signal (opening
                                            # RVOL + range break + VWAP). OBSERVE only — a faller-
                                            # BLOCKED name that is continuation-CONFIRMED is exactly
                                            # the case the gate may be wrong about; the rejection-
                                            # grader will measure post-close whether it ran. (Gate-
                                            # action to EXEMPT confirmed continuers is the flagged
                                            # next step — doc 188 — pending validation on OUR universe.)
                                            _or_sig = None
                                            try:
                                                _or_sig = _or_tracker.signal(
                                                    verdict.ticker,
                                                    current_price=(_d182_cp or getattr(_d182_cand, "current_price", 0.0) or 0.0),
                                                    vwap=_d182_vw,
                                                    rvol_proxy=getattr(_d182_cand, "rvol", None),
                                                )
                                                if _or_sig and _or_sig.confirmed:
                                                    logger.warning(
                                                        "D188 CONTINUATION-CONFIRMED but FALLER-BLOCKED: %s (%s) "
                                                        "— grader will measure if the gate was wrong",
                                                        verdict.ticker, _or_sig.reason,
                                                    )
                                            except Exception:
                                                _or_sig = None
                                            _d182_grade(
                                                ticker=verdict.ticker, gate="D160_FALLER",
                                                decision_price=(getattr(_d182_cand, "current_price", None)
                                                                or verdict.entry_price),
                                                mfcs=verdict.mfcs,
                                                score=(_faller_assessment.score if _faller_assessment else None),
                                                reason="predicted_fader",
                                                gap_pct=getattr(_d182_cand, "gap_pct", None),
                                                rvol=getattr(_d182_cand, "rvol", None),
                                                float_shares=getattr(_d182_cand, "float_shares", None),
                                                market_cap=getattr(_d182_cand, "market_cap", None),
                                                vwap_distance=_d182_vwd,
                                                opening_rvol=(_or_sig.opening_rvol if _or_sig else None),
                                                opening_range_sign=(_or_sig.range_sign if _or_sig else None),
                                                broke_or_high=(_or_sig.broke_high if _or_sig else None),
                                            )
                                        except Exception:
                                            pass
                                        # doc 191 (gate-ACTION, default OFF): EXEMPT a continuation-
                                        # CONFIRMED + LIQUID name from the faller hard-reject. With the
                                        # flag OFF (default) _exempt is always False -> unconditional
                                        # continue (behavior identical to before). Flip ONLY after the
                                        # doc-182 grader proves confirmed-continuers run on OUR universe.
                                        _exempt = False
                                        try:
                                            from src.analysis.opening_range import (
                                                faller_exemption_ok as _d191_exempt_ok,
                                            )
                                            _exempt = _d191_exempt_ok(
                                                enabled=getattr(settings.faller, "continuation_exemption_enabled", False),
                                                confirmed=bool(_or_sig and _or_sig.confirmed),
                                                dollar_volume=(getattr(_faller_assessment, "dollar_volume", None)
                                                               if _faller_assessment else None),
                                                opening_rvol=(_or_sig.opening_rvol if _or_sig else None),
                                                min_dollar_volume=getattr(
                                                    settings.faller, "continuation_exemption_min_dollar_volume", 5_000_000.0),
                                                min_opening_rvol=getattr(
                                                    settings.faller, "continuation_exemption_min_opening_rvol", 2.0),
                                            )
                                        except Exception:
                                            _exempt = False
                                        if _exempt:
                                            logger.warning(
                                                "D191 FALLER EXEMPTION: %s continuation-CONFIRMED + liquid "
                                                "($%.0fK dolvol, orvol=%.1f) — OVERRIDING faller block, "
                                                "proceeding to D170 (at faller-reduced size)",
                                                verdict.ticker,
                                                (getattr(_faller_assessment, "dollar_volume", 0.0) or 0.0) / 1000,
                                                (_or_sig.opening_rvol if _or_sig else 0.0),
                                            )
                                            # fall through (NO continue) -> size-reduce + D170 + execution
                                        else:
                                            # doc 272 VLL: faller hard-reject terminal (emitted HERE,
                                            # after the D191 exemption check, so an exempted name that
                                            # falls through is NOT falsely marked blocked).
                                            try:
                                                from src.ops.verdict_ledger import vll_emit
                                                vll_emit("BLOCKED_FALLER", verdict.ticker,
                                                         reason=(f"faller_score="
                                                                 f"{_faller_assessment.score:.3f}"
                                                                 f">{_d160_threshold:.2f}"),
                                                         mfcs=round(float(verdict.mfcs or 0.0), 3),
                                                         path="MAIN")
                                            except Exception:
                                                pass
                                            continue
                                    if _faller_assessment is not None and _faller_assessment.position_multiplier < 1.0:
                                        _adjusted_size = (
                                            verdict.position_size_pct
                                            * _faller_assessment.position_multiplier
                                        )
                                        verdict = verdict.model_copy(update={
                                            "position_size_pct": max(_adjusted_size, 0.01),
                                        })
                                        logger.info(
                                            "D160 FALLER SIZE REDUCE: %s → "
                                            "%.0f%% of intended size "
                                            "(faller_score=%.3f, new_size_pct=%.1f%%)",
                                            verdict.ticker,
                                            _faller_assessment.position_multiplier * 100,
                                            _faller_assessment.score,
                                            verdict.position_size_pct * 100,
                                        )

                            # ── doc 202 (flag-gated OFF): ELITE-FADE SHORT ──────────────
                            # Selection study (198-201): the bot's high-MFCS BUY picks FADE
                            # (>=0.50 bucket RAN 4% / faded -6.19%); the BORROWABLE ones net
                            # +9.47% short. If this long BUY matches the ELITE-fade signature
                            # AND isn't running (squeeze guard = doc-188 ORB-confirmed or
                            # above-VWAP), SHORT it (fractional) instead of buying. DEFAULT OFF
                            # (n=11) -> no-op until EXEC_FADE_SHORT_ENABLED is flipped.
                            if (verdict.direction == "long"
                                    and getattr(settings.execution, "fade_short_enabled", False)
                                    and (verdict.mfcs or 0.0) >= getattr(settings.execution, "fade_short_min_mfcs", 0.50)):
                                _fs_cand = cand_by_ticker.get(verdict.ticker)
                                _fs_vwap = getattr(_faller_assessment, "vwap", None) if _faller_assessment else None
                                _fs_cp = (getattr(_fs_cand, "current_price", None) or verdict.entry_price)
                                _fs_above_vwap = bool(_fs_vwap and _fs_cp and _fs_cp >= _fs_vwap)
                                _fs_dolvol = getattr(_faller_assessment, "dollar_volume", None) if _faller_assessment else None
                                _fs_orb_confirmed = False
                                try:
                                    _fs_sig = _or_tracker.signal(
                                        verdict.ticker, current_price=_fs_cp, vwap=_fs_vwap,
                                        rvol_proxy=getattr(_fs_cand, "rvol", None))
                                    _fs_orb_confirmed = bool(_fs_sig and _fs_sig.confirmed)
                                except Exception:
                                    pass
                                _fs_short = _fs_etb = False
                                try:
                                    _fs_asset = await client.check_asset_tradable(verdict.ticker)
                                    _fs_short = bool(_fs_asset.get("shortable"))
                                    _fs_etb = bool(_fs_asset.get("easy_to_borrow"))
                                except Exception:
                                    pass
                                from src.analysis.opening_range import fade_short_signature_ok as _d202_ok
                                if _d202_ok(
                                    enabled=True, mfcs=verdict.mfcs, shortable=_fs_short,
                                    easy_to_borrow=_fs_etb, dollar_volume=_fs_dolvol,
                                    above_vwap=_fs_above_vwap, orb_confirmed=_fs_orb_confirmed,
                                    min_mfcs=getattr(settings.execution, "fade_short_min_mfcs", 0.50),
                                    min_dollar_volume=getattr(settings.execution, "fade_short_min_dollar_volume", 5_000_000.0),
                                ):
                                    _sc = settings.short_selling
                                    _fs_mult = getattr(settings.execution, "fade_short_size_mult", 0.25)
                                    _fs_stop = round(verdict.entry_price * (1 + _sc.stop_pct_above_entry), 4)
                                    _fs_targets = [round(verdict.entry_price * (1 + t), 4) for t in _sc.target_pcts]
                                    _fs_verdict = verdict.model_copy(update={
                                        "direction": "short", "stop_loss": _fs_stop,
                                        "target_prices": _fs_targets,
                                        "position_size_pct": max(verdict.position_size_pct * _fs_mult, 0.01),
                                    })
                                    logger.warning(
                                        "D202 ELITE-FADE SHORT: %s MFCS=%.2f ETB+below-VWAP+not-ORB-confirmed "
                                        "→ SHORT (size x%.2f, stop +%.0f%%, dolvol=$%.0fK) [doc 201: +9.47%% net]",
                                        verdict.ticker, verdict.mfcs or 0.0, _fs_mult,
                                        _sc.stop_pct_above_entry * 100, (_fs_dolvol or 0) / 1000,
                                    )
                                    _phantom.update_gate(verdict.ticker, "D202_ELITE_FADE_SHORT")
                                    # doc 272 VLL: long verdict re-routed to D202 fade-short —
                                    # informational terminal for the LONG attempt (flag-gated path).
                                    try:
                                        from src.ops.verdict_ledger import vll_emit
                                        vll_emit("ROUTED_FADE_SHORT", verdict.ticker,
                                                 reason=f"elite-fade signature MFCS={verdict.mfcs or 0.0:.2f}",
                                                 path="MAIN")
                                    except Exception:
                                        pass
                                    try:
                                        _fs_order = await bridge.execute_verdict(_fs_verdict, scored=_scored)
                                    except Exception as _fse:
                                        logger.warning("D202 fade-short exec error for %s: %s", verdict.ticker, _fse)
                                        _fs_order = None
                                    if _fs_order is not None:
                                        session_trades += 1
                                        logger.warning(
                                            "D202 FADE-SHORT FILLED: %s qty=%d @ $%.2f (oid=%s, stop_oid=%s)",
                                            _fs_order.ticker, _fs_order.qty, _fs_order.submitted_price,
                                            _fs_order.order_id, getattr(_fs_order, "stop_order_id", None))
                                        try:
                                            stop_resubmitter.register_stop(
                                                ticker=_fs_order.ticker,
                                                order_id=getattr(_fs_order, "stop_order_id", None) or _fs_order.order_id,
                                                stop_price=_fs_verdict.stop_loss, qty=_fs_order.qty)
                                        except Exception as _fsr:
                                            logger.warning("D202 stop registration error for %s: %s", verdict.ticker, _fsr)
                                        # doc 202(h): persist SHORT state with direction so it
                                        # survives a restart (recovered as a short, not a long).
                                        try:
                                            state_mgr.update_position(
                                                ticker=_fs_order.ticker, qty=_fs_order.qty,
                                                entry_price=_fs_order.submitted_price,
                                                signal_price=getattr(_fs_order, "signal_price", _fs_order.submitted_price),
                                                stop_loss=_fs_verdict.stop_loss,
                                                target_prices=list(_fs_verdict.target_prices),
                                                tranches_filled=0, remaining_qty=_fs_order.qty,
                                                realized_pnl=0.0, entry_order_id=_fs_order.order_id,
                                                stop_order_id=getattr(_fs_order, "stop_order_id", "") or "",
                                                tranche_order_ids=[],
                                                opened_at=_fs_order.timestamp.isoformat(),
                                                position_tier=3, gap_pct=getattr(_fs_verdict, "gap_pct", 0.0),
                                                direction="short",
                                            )
                                            state_mgr.save()
                                        except Exception as _fsp:
                                            logger.warning("D202 state-persist error for %s: %s", verdict.ticker, _fsp)
                                    continue  # shorted (or attempted) -> do NOT also buy

                            # D170: Entry delay — register long BUY for observation window.
                            # Short path (D161) already executed above; skips this block.
                            # D199: CATALYST stocks use a shorter observation window (5 min vs 15 min).
                            # EntryDelayManager uses the global config window; log the tier for audit.
                            if entry_delay_mgr.config.enabled and verdict.direction == "long":
                                if _d199_tier is not None and _d199_obs_minutes_override is not None:
                                    _global_obs = entry_delay_mgr.config.observation_minutes
                                    if _d199_obs_minutes_override < _global_obs:
                                        logger.info(
                                            "D199 CATALYST OBS: %s would prefer %.0fmin window "
                                            "(global=%.0fmin) — using global for now",
                                            verdict.ticker,
                                            _d199_obs_minutes_override,
                                            _global_obs,
                                        )
                                # D211: Convert signal list to dict for D170 observation window
                                _d170_raw_signals = orchestrator._signals_by_ticker.get(
                                    verdict.ticker, []
                                )
                                _d170_signals_dict = {
                                    getattr(s, "agent_id", f"agent_{i}"): getattr(s, "signal", "NEUTRAL")
                                    for i, s in enumerate(_d170_raw_signals)
                                } if isinstance(_d170_raw_signals, list) else _d170_raw_signals
                                entry_delay_mgr.register_candidate(
                                    ticker=verdict.ticker,
                                    open_price=verdict.entry_price,
                                    mfcs=verdict.mfcs,
                                    agent_signals=_d170_signals_dict,
                                    timestamp=datetime.now(timezone.utc),
                                )
                                _d170_obs = entry_delay_mgr.get_candidate(verdict.ticker)
                                if _d170_obs is None or _d170_obs.state != ObservationState.APPROVED:
                                    _d170_pending_verdicts[verdict.ticker] = (verdict, _scored)
                                    logger.info("D170 DEFERRED: %s observation started", verdict.ticker)
                                    _phantom.update_gate(verdict.ticker, "D170_OBSERVATION")
                                    # doc 272 VLL: NON-terminal marker — verdict parked in the
                                    # observation window. Terminal comes later: SUBMITTED (bridge,
                                    # on approved execution) or EXPIRED_OBSERVATION / ERROR_EXECUTOR.
                                    try:
                                        from src.ops.verdict_ledger import vll_emit
                                        vll_emit("DEFERRED_OBSERVATION", verdict.ticker,
                                                 reason="entry-delay observation window started", path="MAIN")
                                    except Exception:
                                        pass
                                    continue

                            # D198: Session regime gate — block entries when halted
                            _d198_allowed, _d198_reason = session_regime.should_allow_new_entry()
                            if not _d198_allowed:
                                logger.warning(
                                    "D198 ENTRY BLOCKED: %s regime=%s reason=%s",
                                    verdict.ticker, session_regime.get_regime().value, _d198_reason,
                                )
                                _phantom.update_gate(verdict.ticker, "D198_REGIME_HALT")
                                # doc 272 VLL: session-regime halt terminal
                                try:
                                    from src.ops.verdict_ledger import vll_emit
                                    vll_emit("BLOCKED_REGIME", verdict.ticker,
                                             reason=str(_d198_reason)[:120], path="MAIN")
                                except Exception:
                                    pass
                                continue
                            # D198: Apply regime size multiplier on top of D150 tier sizing
                            _d198_mult = session_regime.get_size_multiplier()
                            if _d198_mult != 1.0:
                                verdict = verdict.model_copy(update={
                                    "position_size_pct": max(
                                        verdict.position_size_pct * _d198_mult, 0.01
                                    ),
                                })
                                logger.info(
                                    "D198 SIZE ADJUST: %s regime=%s mult=%.2fx → size=%.1f%%",
                                    verdict.ticker, session_regime.get_regime().value,
                                    _d198_mult, verdict.position_size_pct * 100,
                                )

                            # doc 199 (f1, flag-gated OFF): down-weight rvol-EXHAUSTION names.
                            # Selection study (doc 198, n=302): rvol_exhaustion=True RAN 20% vs
                            # 41% un-flagged — extreme premarket RVOL is exhaustion, not fuel.
                            # RISK-REDUCING (only shrinks). Default OFF; flip via env after the
                            # auto-scorecard confirms on more sessions.
                            if getattr(settings.execution, "rvol_exhaustion_size_penalty_enabled", False):
                                _exh_cand = cand_by_ticker.get(verdict.ticker)
                                if _exh_cand is not None and getattr(_exh_cand, "rvol_exhaustion", False):
                                    _exh_mult = getattr(settings.execution, "rvol_exhaustion_size_mult", 0.5)
                                    verdict = verdict.model_copy(update={
                                        "position_size_pct": max(verdict.position_size_pct * _exh_mult, 0.01),
                                    })
                                    logger.info(
                                        "D199 EXHAUSTION DOWN-WEIGHT: %s rvol_exhaustion=True → size x%.2f "
                                        "= %.1f%% (doc 198: exhausted ran 20%% vs 41%%)",
                                        verdict.ticker, _exh_mult, verdict.position_size_pct * 100,
                                    )

                            _phantom.update_gate(verdict.ticker, "EXECUTED")
                            order = await bridge.execute_verdict(verdict, scored=_scored)
                            if order is None:
                                # D125: Log when BUY verdict is blocked by execution layer
                                # (spread filter, portfolio risk, duplicate position, etc.)
                                _phantom.update_gate(verdict.ticker, "EXECUTION_BRIDGE_BLOCK")
                                logger.warning(
                                    "D125 EXECUTION BLOCKED: %s BUY verdict (MFCS=%.3f) "
                                    "rejected by execution layer — check SPREAD/PORTFOLIO/DUPLICATE logs",
                                    verdict.ticker, verdict.mfcs,
                                )
                            if order is not None:
                                # D214: Assign archetype at entry (exactly once per position)
                                if _archetype_strategy is not None:
                                    _cand_for_arch = cand_by_ticker.get(verdict.ticker)
                                    if _cand_for_arch:
                                        _archetype_strategy.assign_at_entry(
                                            ticker=order.ticker,
                                            gap_pct=abs(_cand_for_arch.gap_pct),
                                            rvol=_cand_for_arch.rvol,
                                            prior_gap_count=getattr(_cand_for_arch, "prior_gap_count", 0) or 0,
                                            is_day2_runner=getattr(_cand_for_arch, "is_day2_runner", False),
                                        )
                                session_trades += 1
                                logger.info(
                                    "ORDER FILLED: %s qty=%d @ $%.2f (order_id=%s)",
                                    order.ticker, order.qty, order.submitted_price, order.order_id,
                                )
                                # D218: Post trade open to Discord
                                # D305 (2026-05-19): pass gap_pct + intended_entry
                                # so the doc 165 enriched fields actually render
                                # (gap badge on entry price; slippage badge when
                                # filled price differs from submitted limit).
                                # Pre-D305 these kwargs were dormant -- operator
                                # saw "Bought VELO at $21.14" with no gap context
                                # and no slippage visibility.
                                try:
                                    from src.monitoring.alerts import post_trade_open
                                    _d305_gap = None
                                    try:
                                        _cand_d305 = cand_by_ticker.get(verdict.ticker)
                                        if _cand_d305 is not None:
                                            _d305_gap = float(_cand_d305.gap_pct)
                                    except Exception:
                                        pass
                                    _d305_fill = (
                                        order.fill_price
                                        if order.fill_price and order.fill_price > 0
                                        else order.submitted_price
                                    )
                                    _d305_intended = float(order.submitted_price or 0) or None
                                    asyncio.ensure_future(post_trade_open(
                                        ticker=order.ticker, side="buy", qty=order.qty,
                                        entry_price=_d305_fill,
                                        stop_loss=verdict.stop_loss, mfcs=verdict.mfcs,
                                        path="PHASE2_BUY",
                                        webhook_url=settings.ops.watchlist_webhook_url,
                                        gap_pct=_d305_gap,            # D305
                                        intended_entry=_d305_intended,  # D305
                                    ))
                                except Exception:
                                    pass
                                # D306 (2026-05-19): also post the
                                # post_entry_filled alert added in doc 165.
                                # post_trade_open is the "BOT INTENDS TO
                                # BUY" message; post_entry_filled is the
                                # "BROKER CONFIRMED FILL" message with
                                # slippage + stop-distance + (in future
                                # iterations) BRIDGE_CANCEL attempt count
                                # and fill latency. Pre-D306 this alert
                                # existed in alerts.py but had ZERO
                                # callers -- the doc 165 wiring TODO was
                                # never closed.
                                try:
                                    from src.monitoring.alerts import post_entry_filled
                                    if _d305_fill and verdict.stop_loss:
                                        asyncio.ensure_future(post_entry_filled(
                                            ticker=order.ticker,
                                            qty=int(order.qty),
                                            fill_price=float(_d305_fill),
                                            intended_entry=float(_d305_intended or _d305_fill),
                                            stop_loss=float(verdict.stop_loss),
                                            gap_pct=_d305_gap,
                                            # n_submission_attempts left at default 1 -- a
                                            # follow-up commit will track per-ticker
                                            # OTO retry count for BRIDGE_CANCEL visibility.
                                            webhook_url=settings.ops.watchlist_webhook_url,
                                        ))
                                except Exception:
                                    pass
                                # D221 Phase F: enriched decision-learning channel.
                                # Adds per-agent attribution, statistical context,
                                # and cascade-anti-selection annotation. Parallel
                                # to the legacy post_trade_open above.
                                try:
                                    from src.monitoring.decision_learning import (
                                        post_trade_open_enriched, get_distribution_cache,
                                    )
                                    _agent_sigs = list(_scored.agent_signals) if _scored else []
                                    _catalyst = None
                                    for _s in _agent_sigs:
                                        if getattr(_s, "agent_id", "") == "news_agent":
                                            _catalyst = getattr(_s, "catalyst_type", None)
                                            break
                                    asyncio.ensure_future(post_trade_open_enriched(
                                        ticker=order.ticker, qty=order.qty,
                                        entry_price=order.fill_price if order.fill_price and order.fill_price > 0 else order.submitted_price,
                                        stop_loss=verdict.stop_loss, mfcs=verdict.mfcs,
                                        path="PHASE2_BUY",
                                        agent_signals=_agent_sigs,
                                        catalyst_type=_catalyst,
                                        kelly_tier=getattr(verdict, "kelly_tier", None),
                                        distribution_cache=get_distribution_cache(),
                                    ))
                                except Exception as _dl_e:
                                    logger.debug("decision_learning trade_open enrich failed: %s", _dl_e)
                                # Doc 82 Phase 2 ship (2026-04-29): unified post-fill
                                # bookkeeping handler. Replaces ~325 LOC of inline
                                # bookkeeping with a single call. The helper handles:
                                # D215, journal, exit_ladder, stop register, state_mgr,
                                # BAR-1 EXIT scheduling, D278 SKIPPED log. Preserved
                                # inline at this site: Discord webhooks (above) and
                                # D164 early profit take (below) which are PHASE2-only.
                                _p2_cand = cand_by_ticker.get(verdict.ticker)
                                await post_fill_bookkeeping(
                                    order=order, verdict=verdict, scored=_scored,
                                    candidate=_p2_cand,
                                    path="PHASE2_BUY",
                                    ctx=_post_fill_ctx,
                                    log_prefix="Phase2",
                                    faller_assessment=_faller_assessment,
                                )
                                # D164: Register early profit take + schedule async task.
                                # Phase 2 trades (9:30-10:00) need T+2min trigger BEFORE
                                # Phase 3 monitoring loop starts at 10 AM.
                                # Pattern mirrors D146: asyncio.create_task fires independently.
                                _d164_fill_time = datetime.now(timezone.utc)
                                _d164_pos = bridge.position_manager._positions.get(order.ticker)
                                _d164_direction = getattr(_d164_pos, "direction", "long")
                                early_profit_taker.register_fill(
                                    symbol=order.ticker,
                                    fill_time=_d164_fill_time,
                                    entry_price=order.submitted_price,
                                    qty=order.qty,
                                    direction=_d164_direction,
                                )
                                tranche_taker.register_fill(order.ticker)  # D165
                                if _d164_cfg.enabled and (
                                    _d164_direction == "long"
                                    or _d164_cfg.apply_to_shorts
                                ):
                                    _d164_ticker = order.ticker
                                    _d164_entry_px = order.submitted_price
                                    _d164_orig_qty = order.qty
                                    _d164_dir = _d164_direction

                                    async def _d164_early_profit_task(
                                        _ticker=_d164_ticker,
                                        _entry_px=_d164_entry_px,
                                        _orig_qty=_d164_orig_qty,
                                        _dir=_d164_dir,
                                        _delay=_d164_cfg.delay_seconds,
                                    ):
                                        await asyncio.sleep(_delay)
                                        _pos = bridge.position_manager._positions.get(_ticker)
                                        if _pos is None:
                                            return  # Position already closed
                                        # Get current price via snapshot
                                        try:
                                            _snap = await client.get_snapshots([_ticker])
                                            _price = float(
                                                _snap.get(_ticker, {}).get("last_price", 0) or 0
                                            )
                                        except Exception as _snap_e:
                                            logger.warning(
                                                "D164 P2 task: snapshot failed for %s: %s",
                                                _ticker, _snap_e,
                                            )
                                            return
                                        if _price <= 0:
                                            return
                                        _now = datetime.now(timezone.utc)
                                        _action = early_profit_taker.check(_ticker, _price, _now)
                                        if _action != EarlyProfitAction.TAKE_PROFIT:
                                            return
                                        _qty_to_sell = early_profit_taker.qty_to_sell(_ticker)
                                        # Clamp to current remaining_qty in case tranches reduced it
                                        _qty_to_sell = min(_qty_to_sell, _pos.remaining_qty)
                                        if _qty_to_sell <= 0:
                                            early_profit_taker.mark_skipped(_ticker)
                                            return
                                        # D160 RACE FIX: mark skipped BEFORE first await so
                                        # Phase 3 loop won't duplicate the sell while we wait.
                                        early_profit_taker.mark_skipped(_ticker)
                                        # doc 269 (A1): route the partial exit through the
                                        # broker-CONFIRMED close path (same Bug-Z hardening
                                        # as D78 SMART_EXIT ~6280 / D163 ~6080). The prior
                                        # submit_order + book-from-snapshot pattern booked
                                        # PHANTOM P&L whenever the sell 403'd against the
                                        # OTO protective stop (qty held_for_orders) or was
                                        # merely accepted-not-filled.
                                        from src.execution.bridge import attempt_close_with_status_check
                                        try:
                                            _close_res = await attempt_close_with_status_check(
                                                client=client,
                                                ticker=_ticker,
                                                qty=_qty_to_sell,
                                                max_retries=3,
                                                retry_backoff_s=1.0,
                                                cancel_blocking_stops_first=True,
                                                partial=True,
                                            )
                                        except Exception as _d269_e:
                                            logger.error(
                                                "D269 EARLY_PROFIT_ESCALATE %s (P2): close "
                                                "helper raised (%s) — no P&L booked, stop "
                                                "untouched, position stays tracked + protected.",
                                                _ticker, _d269_e,
                                            )
                                            return
                                        # Bug-Z gate (doc 269): broker close NOT confirmed →
                                        # do NOT book P&L, do NOT cancel/remove stops, position
                                        # stays tracked + fully protected (the helper re-arms
                                        # any stop IT cancelled via D249). skipped stays True —
                                        # no silent retry loop after 3 broker-level retries.
                                        # (canonical Bug-Z flag — D78 / test_d24 guard)
                                        _d245_close_succeeded = bool(
                                            _close_res["succeeded"] and _close_res["fill_price"]
                                        )
                                        if not _d245_close_succeeded:
                                            logger.error(
                                                "D269 EARLY_PROFIT_ESCALATE %s (P2): broker "
                                                "partial close FAILED/unconfirmed (last_error=%s) "
                                                "— no P&L booked, stop not cancelled by caller, "
                                                "position remains tracked.",
                                                _ticker, _close_res.get("last_error"),
                                            )
                                            try:
                                                _pos.close_attempt_failed = True
                                            except Exception as _caf_e:
                                                logger.debug(
                                                    "D269: close_attempt_failed flag set "
                                                    "failed: %s", _caf_e,
                                                )
                                            return
                                        try:
                                            # Book ONLY from the broker-confirmed fill — never
                                            # the snapshot price (doc 269).
                                            _fill_px = float(_close_res["fill_price"])
                                            _qty_done = min(
                                                int(_close_res.get("filled_qty") or _qty_to_sell),
                                                _qty_to_sell,
                                            )
                                            # Update position tracking (mirroring tranche fill pattern)
                                            _pos.remaining_qty = max(0, _pos.remaining_qty - _qty_done)
                                            if _dir == "long":
                                                _partial_pnl = (_fill_px - _entry_px) * _qty_done
                                            else:
                                                _partial_pnl = (_entry_px - _fill_px) * _qty_done
                                            bridge.position_manager.record_realized_pnl(_partial_pnl)
                                            _pos.realized_pnl += _partial_pnl
                                            try:
                                                from src.monitoring.metrics import get_metrics as _gm_d164
                                                _gm_d164().daily_pnl.inc(_partial_pnl)
                                            except Exception as e:
                                                logger.warning("D218: pnl_recording failed: %s", e)
                                            # Properly mark executed (unmarks the skipped flag)
                                            _ep_state = early_profit_taker.get_state(_ticker)
                                            if _ep_state is not None:
                                                _ep_state.skipped = False
                                            early_profit_taker.mark_executed(_ticker, _fill_px, _qty_done)
                                            _profit_pct = (
                                                ((_fill_px / _entry_px) - 1) * 100
                                                if _dir == "long"
                                                else ((_entry_px / _fill_px) - 1) * 100
                                            ) if _entry_px > 0 else 0
                                            logger.info(
                                                "D164 EARLY PROFIT TAKE: %s sold %d of %d at $%.4f "
                                                "(+%.2f%%) T+%.0fs — remaining=%d",
                                                _ticker, _qty_done, _orig_qty,
                                                _fill_px, _profit_pct, _delay,
                                                _pos.remaining_qty,
                                            )
                                            # doc 269: the helper may have cancelled the OTO
                                            # protective stop to free held qty. Either way the
                                            # live stop must now cover ONLY the remaining
                                            # shares — re-arm via the D186 resubmitter
                                            # (doc-186 D165 contract).
                                            if _close_res.get("cancelled_stops"):
                                                _pos.stop_order_id = ""
                                            if _pos.remaining_qty > 0:
                                                try:
                                                    _d269_stop_r = await stop_resubmitter.resubmit(
                                                        ticker=_ticker,
                                                        new_stop_price=_pos.stop_loss,
                                                        new_qty=_pos.remaining_qty,
                                                    )
                                                    if _d269_stop_r and _d269_stop_r.success:
                                                        logger.info(
                                                            "D269 STOP RESIZED: %s stop covers %d "
                                                            "shares after early take",
                                                            _ticker, _pos.remaining_qty,
                                                        )
                                                    else:
                                                        logger.warning(
                                                            "D269 stop resize failed %s: %s — "
                                                            "D313 hedge-watcher backstop",
                                                            _ticker,
                                                            getattr(_d269_stop_r, "error", "?"),
                                                        )
                                                except Exception as _d269_stop_e:
                                                    logger.warning(
                                                        "D269 stop resize failed %s: %s",
                                                        _ticker, _d269_stop_e,
                                                    )
                                            # Journal entry
                                            try:
                                                for _j_tid, _j_ent in trade_journal._entries.items():
                                                    if _j_ent.ticker == _ticker and _j_ent.action == "BUY":
                                                        trade_journal.record_close(
                                                            _j_tid,
                                                            exit_price=_fill_px,
                                                            realized_pnl=_partial_pnl,
                                                            exit_time=_now,
                                                            exit_reason="EARLY_PROFIT_TAKE",
                                                        )
                                                        break
                                            except Exception:
                                                pass
                                        except Exception as _d164_err:
                                            # Broker close CONFIRMED — never un_skip/retry here
                                            # (a second sell would double-sell). Book-keeping
                                            # errors are forensic, not re-tradeable.
                                            logger.error(
                                                "D269 D164 P2 %s: bookkeeping after CONFIRMED "
                                                "close raised: %s — P&L may be partially "
                                                "recorded; NOT retrying (would double-sell)",
                                                _ticker, _d164_err,
                                            )

                                    asyncio.create_task(_d164_early_profit_task())
                                    logger.info(
                                        "D164 P2: Scheduled early profit take for %s in %.0fs",
                                        order.ticker, _d164_cfg.delay_seconds,
                                    )

            elif 10 <= hour_et < 16 and market_is_open is not False:
                # D96: Record Phase 2 duration on first Phase 3 entry
                if _phase2_start > 0:
                    try:
                        _p2_elapsed = time.monotonic() - _phase2_start
                        from src.monitoring.metrics import get_metrics as _gm2
                        _gm2().phase2_duration.set(_p2_elapsed)
                        logger.info("D96 Phase 2 duration: %.1fs", _p2_elapsed)
                        _phase2_start = 0  # Only record once
                    except Exception:
                        pass

                # ── Phase 3: Intraday Monitoring + VWAP Breakout Scanner ──
                # D79: Skipped when market_is_open == False (holiday/weekend)

                # D86: Ghost cleanup fallback — if system restarts after 10 AM,
                # Phase 2 never runs so ghost positions would remain unclosed.
                if _ghost_positions_to_close and not _ghost_cleanup_done:
                    _ghost_cleanup_done = True
                    logger.info(
                        "D86: Auto-covering %d ghost positions (Phase 3 fallback): %s",
                        len(_ghost_positions_to_close),
                        _ghost_positions_to_close,
                    )
                    for _ghost_sym in _ghost_positions_to_close:
                        try:
                            await client.close_position(_ghost_sym)
                            logger.info(
                                "D86: Ghost position %s — close_position submitted",
                                _ghost_sym,
                            )
                        except Exception as e:
                            logger.error(
                                "D86: Failed to close ghost position %s: %s",
                                _ghost_sym, e,
                            )
                    logger.info("D86: Ghost position cleanup complete")

                from src.scanners.intraday_vwap import IntradayVWAPScanner

                positions = bridge.position_manager.open_positions

                # ── D76: FORCED EOD CLOSE at 15:55 ET ──────────────────
                # Day trading = NEVER hold overnight. Close ALL positions
                # 5 minutes before market close to guarantee fill.
                # D83: Guard prevents re-firing on subsequent 60s loop cycles.
                # D84: Also set eod_close_completed when no positions exist at
                # 15:55+ to prevent Phase 3 from opening NEW positions via
                # VWAP scan or re-scan in the final minutes before close.
                min_et = now_et.minute
                if hour_et == 15 and min_et >= 55 and not eod_close_completed and not positions:
                    logger.info(
                        "═══ D76 EOD CLOSE: No positions at 15:%02d ET — "
                        "marking EOD complete to prevent new entries ═══",
                        min_et,
                    )
                    eod_close_completed = True
                    # D95: Persist flag
                    try:
                        state_mgr.set_eod_close_completed(True)
                        state_mgr.save()
                    except Exception as e:
                        logger.warning("D218: state_persistence failed: %s", e)
                    continue
                if hour_et == 15 and min_et >= 55 and positions and not eod_close_completed:
                    logger.info(
                        "═══ D76 EOD CLOSE: %d positions closing at 15:%02d ET ═══",
                        len(positions), min_et,
                    )
                    # D80: Cancel ALL open orders first to prevent runaway loop
                    # Tranche/stop orders left active after close_position() can
                    # fill in after-hours, triggering fill handlers that resubmit
                    # orders — creating an infinite buy/sell cascade.
                    try:
                        await client.cancel_all_orders()
                        logger.info("D80: Cancelled all open orders before EOD close")
                    except Exception as e:
                        logger.warning("D80: Cancel all orders failed: %s", e)
                    tranche_monitor.reset()
                    tranche_taker.reset()  # D165
                    _or_tracker.reset()  # doc 188: clear opening-range bars for the next session
                    import asyncio as _aio
                    await _aio.sleep(1)  # Let cancellations propagate

                    # D86: Fetch actual broker positions to avoid ghost shorts.
                    # A stop order may have already closed a position that our
                    # internal tracker still thinks is open. Selling a closed
                    # position creates a short.
                    try:
                        broker_positions = await client.get_positions()
                        # D161: Include both long and short positions. Previously filtered
                        # to side=="long" only, which caused short positions to be treated
                        # as "already stopped out" at EOD, recording wrong P&L.
                        broker_tickers = {
                            p["symbol"] for p in broker_positions
                            if p.get("side") in ("long", "short")
                        }
                    except Exception as e:
                        logger.warning("D86: Failed to fetch broker positions: %s — closing all tracked", e)
                        broker_positions = []  # D150: Prevent NameError on later iteration
                        broker_tickers = {pos.ticker for pos in positions}

                    # Sweep fix: Pre-fetch snapshots for all positions so we have
                    # real market prices. Previously, close_position() returns a
                    # pending order (filled_avg_price=null) and the fallback was
                    # entry_price — recording $0 PnL for EVERY EOD close.
                    # ANNA went $5.20->$7.26 (+$1534) but recorded $0.
                    _eod_snapshots: dict[str, dict] = {}
                    try:
                        _eod_tickers = [pos.ticker for pos in positions if pos.ticker in broker_tickers]
                        if _eod_tickers:
                            _eod_snapshots = await client.get_snapshots(_eod_tickers)
                    except Exception as e:
                        logger.warning("D76: Failed to fetch EOD snapshots: %s — will use broker data", e)

                    # doc 220 (EOD-timing fix): track whether EVERY position actually closed
                    # this pass. The Adversary flagged that D76 set eod_close_completed=True
                    # even when closes FAILED (the 6/1 CMND/OPTU 403 -> they carried). With the
                    # doc-216 cancel-settle poll a single pass may not finish before the cancel
                    # settles; so if any close fails, we DON'T mark complete -> the next Phase-3
                    # cycle (~30s later, still before the 16:00 bell) RETRIES. Many passes across
                    # 15:55->15:59 instead of one 7-second burst.
                    _d76_all_closed = True
                    _d76_failed_tickers: list[str] = []
                    for pos in list(positions):
                        # D86: Skip if position no longer exists at broker (already stopped out)
                        if pos.ticker not in broker_tickers:
                            logger.warning(
                                "D86: Skipping EOD close for %s — not found at broker "
                                "(likely already stopped out)",
                                pos.ticker,
                            )
                            # D94b: Mark as stopped out to prevent re-entry
                            _stopped_out_tickers.add(pos.ticker)
                            # D95: Persist cooldown for crash recovery
                            try:
                                state_mgr.add_stopped_out_ticker(pos.ticker)
                                state_mgr.save()
                            except Exception as e:
                                logger.warning("D218: stopped_ticker_tracking failed: %s", e)
                            # Still close internal tracking — use last known price
                            # from broker positions instead of entry_price
                            trailing_manager.remove_position(pos.ticker)  # D163
                            early_profit_taker.remove(pos.ticker)  # D164
                            tranche_taker.remove(pos.ticker)  # D165
                            entry_delay_mgr.remove(pos.ticker)  # D170
                            # doc 272 VLL: terminal if a deferred verdict was still parked here
                            if _d170_pending_verdicts.pop(pos.ticker, None) is not None:  # D170
                                try:
                                    from src.ops.verdict_ledger import vll_emit
                                    vll_emit("EXPIRED_OBSERVATION", pos.ticker,
                                             reason="deferred verdict superseded by position close",
                                             path="MAIN")
                                except Exception:
                                    pass
                            _stopout_price = pos.entry_price
                            for bp in broker_positions:
                                if bp.get("symbol") == pos.ticker:
                                    _stopout_price = float(bp.get("current_price") or pos.entry_price)
                                    break
                            enriched = await bridge.close_with_attribution(
                                ticker=pos.ticker,
                                exit_price=_stopout_price,
                            )
                            if enriched:
                                session_trades += 1
                            continue

                        # Sweep fix: Build exit price from multiple sources,
                        # ordered by reliability:
                        # 1. filled_avg_price from close_position response (instant fill)
                        # 2. Snapshot last_price (pre-fetched above)
                        # 3. Broker position current_price
                        # 4. Entry price (last resort — records $0 PnL)
                        _eod_exit_price = pos.entry_price  # last resort
                        _snap = _eod_snapshots.get(pos.ticker, {})
                        if _snap.get("last_price"):
                            _eod_exit_price = float(_snap["last_price"])
                        # Broker position current_price as additional fallback
                        for bp in broker_positions:
                            if bp.get("symbol") == pos.ticker:
                                _bp_price = float(bp.get("current_price") or 0)
                                if _bp_price > 0:
                                    _eod_exit_price = _bp_price
                                break

                        # 2026-05-28 (doc 177): route the PRIMARY EOD close through
                        # the cancel-blocking-stops wrapper. Phase A (doc 175)
                        # hardened D91/D242/D85 but MISSED this D76 main-loop close —
                        # the exact path that booked the APPS +$1,425.90 PHANTOM on
                        # 5/28 (and LFS +$1,403 on 5/27). The raw client.close_position
                        # here 403'd (the D313 emergency stop reserved all 970 sh),
                        # the except merely logged, and execution FELL THROUGH to
                        # close_with_attribution — booking unrealized MTM as realized
                        # P&L into _daily_realized_pnl + trade_results.jsonl, which
                        # then poisoned the BOCPD/Kelly learning corpus.
                        # Fix: cancel blocking stops + retry; only book P&L when the
                        # broker close actually SUCCEEDS. On failure, leave the
                        # position tracked for the D242 failsafe / next-session D91
                        # (both Phase-A-hardened) — NEVER book a phantom close.
                        from src.execution.bridge import (
                            attempt_close_with_status_check as _d177_close,
                        )
                        _d76_close_ok = False
                        try:
                            _d76_res = await _d177_close(
                                client=client,
                                ticker=pos.ticker,
                                qty=pos.remaining_qty,
                                max_retries=3,
                                cancel_blocking_stops_first=True,
                            )
                            if _d76_res.get("succeeded"):
                                _d76_close_ok = True
                                # Prefer actual fill price if available (instant fill)
                                _fp = _d76_res.get("fill_price")
                                if _fp and float(_fp) > 0:
                                    _eod_exit_price = float(_fp)
                                # D150: Check for partial fill on EOD close
                                _eod_filled_qty = _d76_res.get("filled_qty")
                                if _eod_filled_qty:
                                    try:
                                        _eod_fq = int(float(_eod_filled_qty))
                                        if 0 < _eod_fq < pos.remaining_qty:
                                            logger.warning(
                                                "D76 PARTIAL FILL: %s only %d/%d shares filled "
                                                "— %d shares may remain at broker!",
                                                pos.ticker, _eod_fq, pos.remaining_qty,
                                                pos.remaining_qty - _eod_fq,
                                            )
                                    except (ValueError, TypeError):
                                        pass
                                _eod_pnl_per_share = (
                                    (pos.entry_price - _eod_exit_price)
                                    if getattr(pos, "direction", "long") == "short"
                                    else (_eod_exit_price - pos.entry_price)
                                )
                                logger.info(
                                    "D76 CLOSED: %s qty=%d @ $%.2f (entry=$%.2f, pnl=$%.2f) [%s]",
                                    pos.ticker, pos.remaining_qty,
                                    _eod_exit_price, pos.entry_price,
                                    _eod_pnl_per_share * pos.remaining_qty,
                                    getattr(pos, "direction", "long").upper(),
                                )
                            else:
                                logger.error(
                                    "D76 close FAILED %s after cancel-stops retries "
                                    "(last_error=%s) — NOT booking P&L; leaving position "
                                    "tracked for D242 failsafe / next-session D91 close",
                                    pos.ticker, _d76_res.get("last_error"),
                                )
                        except Exception as e:
                            # D86: Do NOT fallback to market sell — that creates ghost shorts
                            # if the position was already closed by a stop order.
                            logger.error(
                                "D76 close raised %s: %s — NOT booking P&L (phantom guard)",
                                pos.ticker, e,
                            )

                        # 2026-05-28 (doc 177): only proceed to internal-close +
                        # attribution (which BOOKS realized P&L) when the broker close
                        # actually succeeded. This is the phantom-P&L guard — prevents
                        # the APPS-5/28 / LFS-5/27 class of fake-positive ghost closes.
                        if not _d76_close_ok:
                            # doc 220: this position did NOT close — do not mark the EOD close
                            # complete; the next Phase-3 cycle will retry (still before the bell).
                            _d76_all_closed = False
                            _d76_failed_tickers.append(pos.ticker)
                            continue

                        # Internal tracking close
                        trailing_manager.remove_position(pos.ticker)  # D163
                        early_profit_taker.remove(pos.ticker)  # D164
                        tranche_taker.remove(pos.ticker)  # D165
                        entry_delay_mgr.remove(pos.ticker)  # D170
                        # doc 272 VLL: terminal if a deferred verdict was still parked here
                        if _d170_pending_verdicts.pop(pos.ticker, None) is not None:  # D170
                            try:
                                from src.ops.verdict_ledger import vll_emit
                                vll_emit("EXPIRED_OBSERVATION", pos.ticker,
                                         reason="deferred verdict superseded by position close",
                                         path="MAIN")
                            except Exception:
                                pass
                        enriched = await bridge.close_with_attribution(
                            ticker=pos.ticker,
                            exit_price=_eod_exit_price,
                        )
                        if enriched:
                            session_trades += 1
                            # Sweep fix: enriched.pnl is per-share. Multiply by qty.
                            _eod_total_pnl = enriched.pnl * pos.remaining_qty
                            logger.info(
                                "D76 PnL: %s $%.2f (%d shares, $%.2f/share)",
                                pos.ticker, _eod_total_pnl,
                                pos.remaining_qty, enriched.pnl,
                            )
                            # D198: Record EOD close for regime tracking
                            session_regime.record_trade_result(
                                pnl=_eod_total_pnl, is_win=(_eod_total_pnl > 0)
                            )
                            # D215: Per-path P&L tracking
                            _exec_recorder.record_close(
                                pos.ticker, enriched.exit_price,
                                pos.remaining_qty, _eod_total_pnl, "EOD_CLOSE",
                            )
                            try:
                                state_mgr.remove_position(pos.ticker)
                                state_mgr.update_daily_pnl(
                                    position_manager._daily_realized_pnl,
                                )
                                state_mgr.save()
                            except Exception as e:
                                logger.warning("D218: position_removal failed: %s", e)
                            # Journal close
                            try:
                                for j_tid, j_ent in trade_journal._entries.items():
                                    if j_ent.ticker == pos.ticker and j_ent.action == "BUY":
                                        trade_journal.record_close(
                                            j_tid,
                                            exit_price=enriched.exit_price,
                                            realized_pnl=_eod_total_pnl,
                                            exit_time=datetime.now(timezone.utc),
                                            exit_reason="EOD_CLOSE",
                                        )
                                        break
                            except Exception:
                                pass
                        else:
                            # D84: close_with_attribution returns None when no
                            # ScoredCandidate was cached (e.g., positions opened
                            # via RESCAN or before caching was deployed). Remove
                            # from position_manager so the position doesn't linger
                            # as a ghost that Phase 3/4 tries to re-close.
                            logger.warning(
                                "D76: No ScoredCandidate cache for %s — "
                                "removing from position manager without attribution",
                                pos.ticker,
                            )
                            bridge.position_manager.remove_position(pos.ticker)
                            try:
                                state_mgr.remove_position(pos.ticker)
                                state_mgr.save()
                            except Exception as e:
                                logger.warning("D218: position_removal failed: %s", e)
                    # D121 BUG-S9: Final order sweep — catch any orders submitted
                    # during the close loop (e.g., from concurrent fill handlers).
                    try:
                        await client.cancel_all_orders()
                        logger.info("D76: Final order sweep completed")
                    except Exception:
                        logger.debug("D76: Final order sweep failed (non-fatal)")
                    # doc 220 (EOD-timing fix): mark the EOD close COMPLETE only if EVERY
                    # position actually closed. If any failed (403/strand), leave the flag
                    # FALSE so the next Phase-3 cycle (~30-60s, still < 16:00) RETRIES — except
                    # on the "final pass", where we mark complete to avoid firing unfillable
                    # orders right at the bell (Phase 4 then carries + the doc-216/218 path
                    # keeps them protected/escalated). This turns the single 7-second burst
                    # into repeated passes across the 15:55->15:5x window.
                    #
                    # doc 225 BUGFIX (adversary review): the final pass was min_et>=59, but the
                    # loop cadence is 30-60s + D76 processing, so a 15:58 cycle can jump to
                    # 16:00:xx — at which point hour_et==16, the D76 block (gated hour_et==15)
                    # is NEVER re-entered, the flag stays False, and Phase 4 carries the
                    # retryable position overnight. Minute 59 could be SKIPPED entirely. Fix:
                    # fire the final pass at min_et>=58 (still ~2min/3-4 passes of headroom from
                    # 15:55) so the complete-flag is reliably set before the hour rolls over.
                    _d76_final_pass = (min_et >= 58)
                    if _d76_all_closed or _d76_final_pass:
                        logger.info(
                            "═══ D76 EOD CLOSE COMPLETE ═══ (%s)",
                            "all positions closed" if _d76_all_closed
                            else f"final pass at 15:{min_et:02d}; UNCLOSED carry: {_d76_failed_tickers}",
                        )
                        eod_close_completed = True  # D83: Prevent re-firing
                        try:
                            state_mgr.set_eod_close_completed(True)
                            state_mgr.save()
                        except Exception as e:
                            logger.warning("D218: state_persistence failed: %s", e)
                    else:
                        logger.warning(
                            "═══ D76 EOD CLOSE: %d/%d closed; %s did NOT close — NOT marking "
                            "complete, RETRYING next cycle (before 16:00 bell) ═══",
                            len(positions) - len(_d76_failed_tickers), len(positions),
                            _d76_failed_tickers,
                        )
                    # Skip normal Phase 3 monitoring this cycle
                    continue

                # ── D84: Skip Phase 3 monitoring after D76 EOD close ────
                # After D76 fires at 15:55, eod_close_completed=True. On
                # subsequent 60s cycles (15:56-15:59), hour_et is still 15
                # so 10<=hour_et<16 routes here instead of Phase 4. Without
                # this guard, Phase 3 continues monitoring (empty positions),
                # running VWAP scans, and re-scanning — which can open NEW
                # positions after D76 just closed everything.
                # Fix: idle until 16:00 when Phase 4 naturally takes over.
                if eod_close_completed:
                    logger.info(
                        "[Phase 3] D84: EOD close already fired — idling until Phase 4 (16:00 ET)"
                    )
                    try:
                        await asyncio.wait_for(shutdown.wait(), timeout=60)
                    except asyncio.TimeoutError:
                        pass
                    continue

                # D103: Show phase transition banner once
                if not _phase3_banner_shown:
                    logger.info("═══════════ [Phase 3] ENTERING INTRADAY MANAGEMENT ═══════════")
                    _phase3_banner_shown = True
                # D303 (2026-05-19): pre-init _d78_snapshots before any
                # branch that reads it. The BAR-1 exit branch at line
                # ~4451 (`_d78_snapshots.get(pos.ticker, {})`) is dormant
                # under D278 t1_next_open but would UnboundLocalError on
                # the first Phase 3 iteration if anyone reverted to
                # bar1_legacy. The actual batched snapshot fetch later
                # overwrites this; the only purpose here is to guarantee
                # the name is bound regardless of which exit policy is on.
                _d78_snapshots: dict = {}
                logger.info("[Phase 3] Intraday — monitoring %d positions", len(positions))
                for pos in positions:
                    logger.info(
                        "  %s: qty=%d, entry=$%.2f, stop=$%.2f",
                        pos.ticker, pos.remaining_qty, pos.entry_price, pos.stop_loss,
                    )

                # D93: Tranche fill cooldown — skip trailing stop and EXIT for
                # tickers that had a tranche fill THIS cycle.  Prevents same-bar
                # ejection (confirmed on EDSA Feb 24, XWEL Feb 25 in simulator).
                _tranche_cooldown_tickers: set[str] = set()

                # D98: Single broker position poll — shared by tranche syncing
                # and stop-out detection. Avoids duplicate API calls.
                # doc-285 gap #2: a failed fetch yields None (UNKNOWN), never
                # a fabricated empty list — the stop-out scan skips on None.
                _d98_broker_positions = await _d98_fetch_broker_positions(client)

                # ── Process tranche fills via WebSocket bridge (ADR-023, D2) ──
                if tranche_monitor.registered_orders > 0:
                    try:
                        # Primary path: drain WebSocket fill events (sub-second)
                        fill_events = await fill_bridge.drain_and_resubmit()
                        for fev in fill_events:
                            logger.info(
                                "TRANCHE T%d FILL (stream): %s @ $%.2f | Stop: $%.2f -> $%.2f%s",
                                fev.tranche_number or 0, fev.ticker,
                                fev.filled_price, fev.old_stop or 0, fev.new_stop or 0,
                                " [RATCHETED]" if fev.stop_resubmitted else "",
                            )
                            # D64: Persist state after tranche fill
                            try:
                                for _pos in bridge.position_manager.open_positions:
                                    if _pos.ticker == fev.ticker:
                                        state_mgr.update_position(
                                            ticker=fev.ticker,
                                            tranches_filled=_pos.tranches_filled,
                                            remaining_qty=_pos.remaining_qty,
                                            realized_pnl=_pos.realized_pnl,
                                            stop_loss=_pos.stop_loss,
                                        )
                                        _tracked = stop_resubmitter.get_tracked_stop(fev.ticker)
                                        if _tracked:
                                            state_mgr.update_position(
                                                ticker=fev.ticker,
                                                stop_order_id=_tracked.order_id,
                                            )
                                        state_mgr.update_daily_pnl(
                                            position_manager._daily_realized_pnl,
                                        )
                                        state_mgr.save()
                                        break
                                if fev.position_closed:
                                    # D94b: Mark as stopped out to prevent re-entry
                                    _stopped_out_tickers.add(fev.ticker)
                                    logger.info(
                                        "D96 POSITION CLOSED (stream): %s fill=$%.2f "
                                        "tranche=%d — added to stop-out cooldown",
                                        fev.ticker, fev.filled_price,
                                        fev.tranche_number or 0,
                                    )
                                    # D95: Persist cooldown + remove position
                                    state_mgr.add_stopped_out_ticker(fev.ticker)
                                    state_mgr.remove_position(fev.ticker)
                                    state_mgr.save()
                            except Exception as e:
                                logger.warning("D218: position_removal failed: %s", e)
                            # D93: Mark ticker for cooldown this cycle
                            _tranche_cooldown_tickers.add(fev.ticker)

                        # Tranche qty syncing via poll (needs tranche_monitor)
                        # doc-285: None (UNKNOWN) degrades to no-op, same as
                        # the pre-fix empty-list behavior for this sync path.
                        for pos in list(bridge.position_manager.open_positions):
                            for ap in (_d98_broker_positions or []):
                                if ap.get("symbol") == pos.ticker:
                                    live_qty = int(float(ap.get("qty", 0)))
                                    if live_qty < pos.remaining_qty:
                                        filled_qty = pos.remaining_qty - live_qty
                                        live_price = float(ap.get("current_price", pos.entry_price))
                                        for oid, to in list(tranche_monitor._order_map.items()):
                                            if to.ticker == pos.ticker and to.qty <= filled_qty:
                                                result = tranche_monitor.on_fill(TrancheFillEvent(
                                                    order_id=oid,
                                                    ticker=pos.ticker,
                                                    filled_price=live_price,
                                                    filled_qty=to.qty,
                                                ))
                                                if result:
                                                    logger.info(
                                                        "TRANCHE T%d FILL (poll): %s @ $%.2f | Stop: $%.2f -> $%.2f",
                                                        result.tranche_number, pos.ticker,
                                                        live_price, result.old_stop, result.new_stop,
                                                    )
                                                    # D121 BUG-L4: Cancel orphaned stop on full close
                                                    if result.position_fully_closed and result.orphaned_stop_oid:
                                                        try:
                                                            await client.cancel_order(result.orphaned_stop_oid)
                                                            logger.info(
                                                                "ORPHAN STOP CANCELED: %s (oid=%s)",
                                                                pos.ticker, result.orphaned_stop_oid,
                                                            )
                                                        except Exception as _ose:
                                                            logger.warning("Orphan stop cancel failed %s: %s", pos.ticker, _ose)
                                                    if result.new_stop > result.old_stop and not result.position_fully_closed:
                                                        try:
                                                            resubmit_result = await stop_resubmitter.resubmit(
                                                                ticker=pos.ticker,
                                                                new_stop_price=result.new_stop,
                                                                # D121 BUG-8: tranche_monitor.on_fill() already
                                                                # decremented remaining_qty — don't subtract again
                                                                new_qty=pos.remaining_qty,
                                                            )
                                                            if resubmit_result.success:
                                                                logger.info(
                                                                    "STOP RATCHETED (poll): %s $%.2f -> $%.2f",
                                                                    pos.ticker, result.old_stop, result.new_stop,
                                                                )
                                                        except Exception as e:
                                                            logger.warning("Stop resubmit error: %s", e)
                                                    # D64: Persist state after poll-based fill
                                                    try:
                                                        state_mgr.update_position(
                                                            ticker=pos.ticker,
                                                            tranches_filled=pos.tranches_filled,
                                                            remaining_qty=pos.remaining_qty,
                                                            stop_loss=pos.stop_loss,
                                                        )
                                                        _ts = stop_resubmitter.get_tracked_stop(pos.ticker)
                                                        if _ts:
                                                            state_mgr.update_position(
                                                                ticker=pos.ticker,
                                                                stop_order_id=_ts.order_id,
                                                            )
                                                        state_mgr.update_daily_pnl(
                                                            position_manager._daily_realized_pnl,
                                                        )
                                                        state_mgr.save()
                                                    except Exception as e:
                                                        logger.warning("D218: state_persistence failed: %s", e)
                                                # D93: Mark ticker for cooldown
                                                _tranche_cooldown_tickers.add(pos.ticker)
                                                break
                    except Exception as e:
                        logger.warning("Tranche fill check error: %s", e)

                # ── D98: Unconditional broker position polling for stop-out detection ──
                # CRITICAL FIX: This was previously INSIDE the
                # `if tranche_monitor.registered_orders > 0:` block, meaning it
                # NEVER ran when no tranche orders were registered. On D11 and D12,
                # ALL stop-outs went undetected because tranche orders were never
                # registered for OTO stops. Now runs every cycle unconditionally.
                try:
                    if _d98_broker_positions is None:
                        # doc-285 gap #2 (PHANTOM-P&L iron rule extended to
                        # ABSENCE): the fetch failed — e.g. circuit breaker
                        # open — so broker state is UNKNOWN. Never diff
                        # internal positions against a fabricated empty book:
                        # on 2026-07-06 10:48 that diff booked two phantom
                        # stop-outs on LIVE positions (-$1,272 fake realized,
                        # regime flipped off a fake loss, $35.7K unmanaged).
                        _broker_syms = None
                        _d98_absent_counts.clear()  # failure resets streaks
                        logger.warning(
                            "D98: broker state unknown — skipping stop-out "
                            "scan this cycle"
                        )
                    else:
                        _broker_syms = {ap.get("symbol") for ap in _d98_broker_positions}
                        # doc-285: a successful snapshot showing the ticker
                        # again resets its absence streak — transient
                        # absences never accumulate across gaps. A streak is
                        # also dropped once its ticker is no longer tracked
                        # (position closed via another path, e.g. tranche T3
                        # full close): a leftover count must never carry into
                        # a same-ticker re-entry, where a single transient
                        # absence would instantly "confirm" a phantom stop-out
                        # on the NEW position.
                        _open_syms98 = {
                            p.ticker for p in bridge.position_manager.open_positions
                        }
                        for _sym98 in list(_d98_absent_counts):
                            if _sym98 in _broker_syms or _sym98 not in _open_syms98:
                                del _d98_absent_counts[_sym98]
                    _d99_now = datetime.now(timezone.utc)
                    for pos in list(bridge.position_manager.open_positions):
                        # D97: Skip tickers already handled by WebSocket events above
                        if pos.ticker in _tranche_cooldown_tickers:
                            continue
                        # D99: Grace period for newly-opened positions.
                        # OTO buy limits may not have filled yet, so get_positions()
                        # won't return them. Without this, D98 falsely detects
                        # "stop-out" for positions that are still pending fill.
                        # (D13: RLMD falsely removed 61s after OTO submission)
                        # D121 BUG-H2: opened_at can be str (from state persistence
                        # isoformat) or datetime (from fresh ManagedPosition).
                        # D121 BUG-S3: Guard against malformed opened_at
                        # from corrupted state file — ValueError on bad ISO.
                        try:
                            _opened = (
                                datetime.fromisoformat(pos.opened_at)
                                if isinstance(pos.opened_at, str)
                                else pos.opened_at
                            )
                        except (ValueError, TypeError):
                            _opened = _d99_now  # Treat as just-opened -> grace period
                        _pos_age_s = (_d99_now - _opened).total_seconds()

                        # ── D146: Bar-1 exit — sell 100% at T+60s ──
                        # The arena's strongest finding: MFE peaks at bar 1.
                        # 100% exit at T+60s = +$5.11 (+299% vs original).
                        # Walk-forward: 2.0x overfit (borderline). Test PF=3.20.
                        # Fires BEFORE the 3-minute grace period because
                        # the bar-1 exit IS the exit strategy (not a stop-out).
                        #
                        # D278 EXIT_POLICY (2026-04-29): gate BAR-1 EXIT on
                        # exit_policy. When 't1_next_open', skip BAR-1 entirely;
                        # positions carry overnight; D86/D91 closes them at next-day
                        # open. Per doc 69: BAR-1 loses -$5K on actual prod trades;
                        # T+1 hold yields +$23K incremental on same trade pool.
                        #
                        # D278 visibility patch (2026-04-29 PM): the existing if
                        # below silently short-circuits when the gate is on, so
                        # there's no log evidence that D278 actually fired.
                        # Emit a single INFO line per position when the gate is
                        # what's preventing BAR-1 EXIT (i.e. all other conditions
                        # are met). Doc 70 §2 step 6 promised this log line for
                        # runtime confirmation; today's session never emitted one
                        # because Phase 2's emission path was bypassed.
                        if (getattr(settings.execution, "bar1_exit_enabled", False)
                                and not getattr(pos, "_bar1_exit_fired", False)
                                and not getattr(pos, "_d278_skip_logged", False)
                                and _d278_bar1_gated_off(settings)
                                and _pos_age_s >= settings.execution.bar1_exit_delay_seconds):
                            logger.info(
                                "D278 BAR-1 SKIPPED Phase3 %s: exit_policy=%s "
                                "age=%.0fs (carrying overnight via D86/D91 next-open path)",
                                pos.ticker,
                                _d278_active_policy(settings),
                                _pos_age_s,
                            )
                            pos._d278_skip_logged = True

                        if (getattr(settings.execution, "bar1_exit_enabled", False)
                                and not getattr(pos, "_bar1_exit_fired", False)
                                and not _d278_bar1_gated_off(settings)
                                and _pos_age_s >= settings.execution.bar1_exit_delay_seconds):
                            _bar1_pct = settings.execution.bar1_exit_pct
                            _bar1_qty = int(pos.remaining_qty * _bar1_pct)
                            if _bar1_qty > 0:
                                # Bug F fix (2026-04-22): use the centralized
                                # bar1_exit_logging helper. Pre-exit log carries
                                # Arena_expected (backtest mean), post-exit log
                                # below carries Arena_actual (realized P&L).
                                from src.execution.bar1_exit_logging import (
                                    format_arena_expected_line,
                                    format_arena_actual_line,
                                )
                                logger.info(
                                    "%s",
                                    format_arena_expected_line(
                                        ticker=pos.ticker,
                                        qty=_bar1_qty,
                                        total=pos.remaining_qty,
                                        pct=_bar1_pct,
                                        age_s=_pos_age_s,
                                    ),
                                )
                                # Capture entry context for the post-exit
                                # Arena_actual line below.
                                _bar1_entry_px_for_actual = float(pos.entry_price or 0)
                                _bar1_qty_for_actual = _bar1_qty
                                # D160 RACE FIX: Set flag BEFORE first await so the Phase 2
                                # async task (which may wake during our awaits) cannot
                                # simultaneously fire a duplicate sell order.
                                pos._bar1_exit_fired = True
                                try:
                                    # Cancel any pending stop AND tranche orders first
                                    # D150: Tranches must be canceled before bar-1 market sell
                                    # to prevent accidental short if tranche fills after exit.
                                    if hasattr(pos, "stop_order_id") and pos.stop_order_id:
                                        try:
                                            await client.cancel_order(pos.stop_order_id)
                                        except Exception as _cancel_err:
                                            logger.warning(
                                                "D146: Failed to cancel stop %s for %s: %s "
                                                "— proceeding with sell (risk: stop may still execute)",
                                                pos.stop_order_id, pos.ticker, _cancel_err,
                                            )
                                    # Cancel pending tranche limit sells
                                    for _toid in getattr(pos, "tranche_order_ids", []) or []:
                                        try:
                                            await client.cancel_order(_toid)
                                        except Exception:
                                            pass  # Best-effort cancel
                                    # D147 FIX: close_position (DELETE /v2/positions)
                                    # never attempts a short — Alpaca closes the existing
                                    # long internally. Same fix as Phase 2 bar-1 exit —
                                    # prevents 422 "cannot be sold short" on SST, ARTL etc.
                                    await client.close_position(pos.ticker)
                                    # If 100% exit, record trade result and remove from tracker
                                    if _bar1_pct >= 1.0:
                                        _stopped_out_tickers.add(pos.ticker)
                                        # D150 FIX: Use close_with_attribution so the trade result
                                        # is written to trade_results.jsonl (bug: EEIQ bar-1 exit
                                        # called remove_position directly, bypassing trade result
                                        # persistence in bridge.close_with_attribution).
                                        _bar1_snap = _d78_snapshots.get(pos.ticker, {})
                                        # Bug Q fix (2026-04-23): use the
                                        # explicit fallback chain helper
                                        # instead of the silent `or` cascade.
                                        # Today's XNDU showed exact-zero
                                        # P&L because snapshot was empty
                                        # and the bare `or pos.entry_price`
                                        # masked the real outcome.
                                        from src.execution.bar1_exit_logging import (
                                            resolve_bar1_exit_price,
                                        )
                                        _bar1_exit_px, _bar1_exit_src = await resolve_bar1_exit_price(
                                            snap=_bar1_snap,
                                            client=client,
                                            ticker=pos.ticker,
                                            fallback_price=float(pos.entry_price),
                                        )
                                        trailing_manager.remove_position(pos.ticker)  # D163
                                        early_profit_taker.remove(pos.ticker)  # D164
                                        tranche_taker.remove(pos.ticker)  # D165
                                        entry_delay_mgr.remove(pos.ticker)  # D170
                                        # doc 272 VLL: terminal if a deferred verdict was parked here
                                        if _d170_pending_verdicts.pop(pos.ticker, None) is not None:  # D170
                                            try:
                                                from src.ops.verdict_ledger import vll_emit
                                                vll_emit("EXPIRED_OBSERVATION", pos.ticker,
                                                         reason="deferred verdict superseded by position close",
                                                         path="MAIN")
                                            except Exception:
                                                pass
                                        try:
                                            _b1_enriched = await bridge.close_with_attribution(
                                                ticker=pos.ticker,
                                                exit_price=_bar1_exit_px,
                                            )
                                            if _b1_enriched:
                                                session_trades += 1
                                            # Bug F: emit Arena_actual line
                                            # carrying realized per-trade P&L
                                            # so it is structurally distinct
                                            # from the Arena_expected backtest
                                            # mean above. Three trades on the
                                            # session must produce three
                                            # different Arena_actual values.
                                            try:
                                                _b1_realized = (
                                                    float(_b1_enriched.pnl) * _bar1_qty_for_actual
                                                    if _b1_enriched is not None
                                                    else (_bar1_exit_px - _bar1_entry_px_for_actual) * _bar1_qty_for_actual
                                                )
                                                logger.info(
                                                    "%s",
                                                    format_arena_actual_line(
                                                        ticker=pos.ticker,
                                                        realized_pnl=_b1_realized,
                                                        entry_price=_bar1_entry_px_for_actual,
                                                        exit_price=_bar1_exit_px,
                                                        qty=_bar1_qty_for_actual,
                                                    ),
                                                )
                                            except Exception as _bf_e:
                                                logger.debug(
                                                    "Bug F: Arena_actual log skipped for %s: %s",
                                                    pos.ticker, _bf_e,
                                                )
                                        except Exception as _b1_ae:
                                            logger.error(
                                                "D146: close_with_attribution failed for %s: %s "
                                                "— falling back to direct remove",
                                                pos.ticker, _b1_ae,
                                            )
                                            bridge.position_manager.remove_position(pos.ticker)
                                        # D150: Persist removal to session state for crash recovery
                                        try:
                                            state_mgr.remove_position(pos.ticker)
                                            state_mgr.save()
                                        except Exception as e:
                                            logger.warning("D218: position_removal failed: %s", e)
                                        logger.info(
                                            "D146: %s fully exited via bar-1. "
                                            "Removed from tracker + session state.",
                                            pos.ticker,
                                        )
                                        continue  # D150: Skip phase transitions for removed position
                                except Exception as _e:
                                    logger.error(
                                        "D146: Bar-1 exit failed for %s: %s "
                                        "— will retry next cycle",
                                        pos.ticker, _e,
                                    )
                                    # D160 RACE FIX: Reset flag so retry fires next cycle.
                                    # We set it optimistically before the try block; on failure
                                    # we must clear it or the exit will never retry.
                                    pos._bar1_exit_fired = False

                        if _pos_age_s < 180:  # 3-minute grace period
                            continue

                        # ── D142: Phase 0 -> Phase 1 transition (30s) ──
                        # Pipeline inversion safety: ultra-tight Phase 0 widens
                        # to Phase 1 after LLM confirmation or 30s timeout.
                        if (settings.execution.phase_stop_enabled
                                and getattr(settings.execution, "phase0_stop_enabled", False)
                                and not getattr(pos, "_phase1_applied", False)):
                            _phase0_s = getattr(settings.execution, "phase0_duration_seconds", 30)
                            if _pos_age_s >= _phase0_s and pos.entry_price > 0:
                                _phase1_stop = pos.entry_price * (
                                    1 - settings.execution.phase1_stop_pct
                                )
                                if _phase1_stop < pos.stop_loss:
                                    logger.info(
                                        "D142 PHASE 0->1: %s after %.0fs. "
                                        "Stop: $%.2f -> $%.2f (Phase 1)",
                                        pos.ticker, _pos_age_s,
                                        pos.stop_loss, _phase1_stop,
                                    )
                                    try:
                                        # D139 fix (2026-04-29): use the local
                                        # stop_resubmitter singleton (line 701),
                                        # not bridge.stop_resubmitter (which the
                                        # ExecutionBridge does not own). Same fix
                                        # applied to D139 Phase 1->2 below.
                                        await stop_resubmitter.resubmit(
                                            pos.ticker, _phase1_stop,
                                        )
                                        pos.stop_loss = _phase1_stop
                                    except Exception as _e:
                                        logger.warning("D142: Phase 0->1 resubmit failed: %s", _e)
                                # Only mark applied after time threshold met
                                pos._phase1_applied = True

                        # ── D139: Phase 1 -> Phase 2 transition (7 min) ──
                        # Widen from tight Phase 1 to ATR-based Phase 2.
                        if (settings.execution.phase_stop_enabled
                                and not getattr(pos, "_phase2_applied", False)):
                            _phase1_s = settings.execution.phase_stop_t1_minutes * 60
                            if _pos_age_s >= _phase1_s and pos.entry_price > 0:
                                # Compute ATR-based stop (Phase 2)
                                _atr = getattr(pos, "atr", 0)
                                if _atr > 0:
                                    _phase2_stop = pos.entry_price - (
                                        _atr * settings.execution.initial_stop_atr_multiplier
                                    )
                                else:
                                    _phase2_stop = pos.entry_price * (
                                        1 - settings.execution.stop_loss_pct
                                    )
                                # Only widen (Phase 2 stop is farther from entry)
                                if _phase2_stop < pos.stop_loss:
                                    logger.info(
                                        "D139 PHASE TRANSITION: %s Phase 1 -> Phase 2 "
                                        "after %.0fs. Stop: $%.2f -> $%.2f (ATR-based)",
                                        pos.ticker, _pos_age_s,
                                        pos.stop_loss, _phase2_stop,
                                    )
                                    try:
                                        # D139 fix (2026-04-29): use local singleton.
                                        # See D142 fix note above.
                                        await stop_resubmitter.resubmit(
                                            pos.ticker, _phase2_stop,
                                        )
                                        pos.stop_loss = _phase2_stop
                                    except Exception as _e:
                                        logger.warning(
                                            "D139: Phase 1->2 resubmit failed for %s: %s",
                                            pos.ticker, _e,
                                        )
                                # Only mark applied after time threshold met
                                pos._phase2_applied = True

                        if _broker_syms is not None and pos.ticker not in _broker_syms:
                            # doc-285 gap #2: ABSENCE CONFIRMATION. One absent
                            # snapshot is not broker evidence of a close —
                            # require the tracked stop order to report filled
                            # (booked at its real filled_avg_price), or 2
                            # consecutive successful fetches both absent.
                            _d98_stop_oid = getattr(pos, "stop_order_id", "") or ""
                            if not _d98_stop_oid:
                                _d98_tracked = stop_resubmitter.get_tracked_stop(pos.ticker)
                                if _d98_tracked:
                                    _d98_stop_oid = _d98_tracked.order_id
                            _d98_confirmed, _d98_fill_px = await _d98_confirm_stop_out(
                                client, pos.ticker, _d98_stop_oid, _d98_absent_counts,
                            )
                            if not _d98_confirmed:
                                continue
                            _d98_absent_counts.pop(pos.ticker, None)
                            # D121 BUG-H1: Use actual stop_loss, not hardcoded 5.5%.
                            # After trailing ratchets stop to breakeven/above, the old
                            # hardcoded estimate recorded phantom losses (e.g., -5.5%
                            # when actual stop was at breakeven). The stop_loss field
                            # is always up-to-date via ratcheting.
                            # doc-285: prefer the stop leg's real filled_avg_price
                            # when the orders API confirmed the fill.
                            _est_stop_price = (
                                _d98_fill_px if _d98_fill_px is not None
                                else pos.stop_loss
                            )
                            logger.warning(
                                "D98 STOP-OUT DETECTED (poll): %s gone from broker — "
                                "entry=$%.2f stop=$%.2f est_exit=$%.2f qty=%d dir=%s "
                                "est_pnl=$%.2f — closing internal tracker",
                                pos.ticker, pos.entry_price, pos.stop_loss,
                                _est_stop_price, pos.remaining_qty,
                                getattr(pos, "direction", "long"),
                                (pos.entry_price - _est_stop_price if getattr(pos, "direction", "long") == "short"
                                 else _est_stop_price - pos.entry_price) * pos.remaining_qty,
                            )
                            _stopped_out_tickers.add(pos.ticker)
                            # D96: Track stop-out in metrics
                            try:
                                from src.monitoring.metrics import get_metrics as _gm_so
                                _gm_so().stop_outs.inc()
                            except Exception:
                                pass
                            # D95: Persist cooldown for crash recovery
                            try:
                                state_mgr.add_stopped_out_ticker(pos.ticker)
                            except Exception as e:
                                logger.warning("D218: stopped_ticker_tracking failed: %s", e)
                            # D121 BUG-P6 / CQ-3: Cancel orphaned stop and tranche
                            # orders. Without this, tranche fills on a rebound
                            # create accidental short positions.
                            await stop_resubmitter.cancel_and_remove(pos.ticker)
                            await tranche_monitor.cancel_all_for_ticker(pos.ticker, client)
                            trailing_manager.remove_position(pos.ticker)  # D163: clean up trail
                            early_profit_taker.remove(pos.ticker)  # D164
                            tranche_taker.remove(pos.ticker)  # D165
                            entry_delay_mgr.remove(pos.ticker)  # D170
                            # doc 272 VLL: terminal if a deferred verdict was still parked here
                            if _d170_pending_verdicts.pop(pos.ticker, None) is not None:  # D170
                                try:
                                    from src.ops.verdict_ledger import vll_emit
                                    vll_emit("EXPIRED_OBSERVATION", pos.ticker,
                                             reason="deferred verdict superseded by position close",
                                             path="MAIN")
                                except Exception:
                                    pass
                            enriched = await bridge.close_with_attribution(
                                ticker=pos.ticker,
                                exit_price=_est_stop_price,
                            )
                            if enriched:
                                session_trades += 1
                                logger.info(
                                    "D98 STOP-OUT CONFIRMED: %s exit=$%.2f pnl=$%.2f "
                                    "hold_time=%.0fmin entry=$%.2f stop=$%.2f",
                                    pos.ticker, enriched.exit_price, enriched.pnl,
                                    (datetime.now(timezone.utc) - (datetime.fromisoformat(pos.opened_at) if isinstance(pos.opened_at, str) else pos.opened_at)).total_seconds() / 60
                                    if pos.opened_at else 0,
                                    pos.entry_price, pos.stop_loss,
                                )
                                # D198: Record stop-out for regime tracking
                                session_regime.record_trade_result(
                                    pnl=enriched.pnl, is_win=(enriched.pnl > 0)
                                )
                                _exec_recorder.record_close(
                                    pos.ticker, enriched.exit_price,
                                    pos.remaining_qty, enriched.pnl, "STOP_OUT",
                                )
                                logger.debug("D198 %s", session_regime.get_status_report())
                            else:
                                # D99: close_position_with_attribution already removed
                                # the position and recorded PnL (lines 798-804 in
                                # position_manager.py). It returns None only when there's
                                # no cached ScoredCandidate for Shapley — NOT because PnL
                                # wasn't recorded. We just need to update the metrics gauge.
                                # D121 BUG-4: Previously this double-recorded PnL.
                                # BUG-FIX: Account for short direction in P&L calculation.
                                # Without this, short stop-outs record INVERTED P&L
                                # (positive when should be negative), corrupting D198
                                # session regime and daily metrics.
                                if getattr(pos, "direction", "long") == "short":
                                    _d99_pnl = (pos.entry_price - _est_stop_price) * pos.remaining_qty
                                else:
                                    _d99_pnl = (_est_stop_price - pos.entry_price) * pos.remaining_qty
                                from src.monitoring.metrics import get_metrics as _gm_d103
                                _gm_d103().daily_pnl.inc(_d99_pnl)
                                _gm_d103().open_positions.set(len(bridge.position_manager.open_positions))
                                session_trades += 1
                                logger.warning(
                                    "D99 STOP-OUT (no Shapley): %s exit=$%.2f pnl=$%.2f "
                                    "hold_time=%.0fmin entry=$%.2f stop=$%.2f qty=%d",
                                    pos.ticker, _est_stop_price, _d99_pnl,
                                    (datetime.now(timezone.utc) - (datetime.fromisoformat(pos.opened_at) if isinstance(pos.opened_at, str) else pos.opened_at)).total_seconds() / 60
                                    if pos.opened_at else 0,
                                    pos.entry_price, pos.stop_loss, pos.remaining_qty,
                                )
                                # D198: Record D99 stop-out for regime tracking
                                session_regime.record_trade_result(
                                    pnl=_d99_pnl, is_win=(_d99_pnl > 0)
                                )
                                _exec_recorder.record_close(
                                    pos.ticker, enriched.exit_price,
                                    pos.remaining_qty, _d99_pnl, "D99_STOP",
                                )
                                logger.debug("D198 %s", session_regime.get_status_report())
                            try:
                                state_mgr.remove_position(pos.ticker)
                                state_mgr.save()
                            except Exception as e:
                                logger.warning("D218: position_removal failed: %s", e)
                except Exception as e:
                    logger.warning("D98 broker polling error: %s", e)

                # D98: Refresh positions list after stop-out detection may have
                # removed positions from the internal tracker.
                positions = bridge.position_manager.open_positions

                # ── D78: Batch snapshot fetch for all position tickers ──
                _d78_snapshots: dict = {}
                _d78_position_tickers = [p.ticker for p in positions]
                if _d78_position_tickers:
                    try:
                        _d78_snapshots = await client.get_snapshots(_d78_position_tickers)
                    except Exception as _e78:
                        logger.warning("D78 snapshot fetch error: %s", _e78)

                # ── D170: Poll observation windows + execute approved candidates ──
                _d170_watching = entry_delay_mgr.watching_tickers
                if _d170_watching:
                    # Fetch snapshots for watching tickers not already in _d78_snapshots
                    _d170_missing = [t for t in _d170_watching if t not in _d78_snapshots]
                    _d170_extra_snaps: dict = {}
                    if _d170_missing:
                        try:
                            _d170_extra_snaps = await client.get_snapshots(_d170_missing)
                        except Exception as _e170s:
                            logger.warning("D170: snapshot fetch failed: %s", _e170s)
                    _d170_all_snaps = {**_d78_snapshots, **_d170_extra_snaps}

                    _d170_now = datetime.now(timezone.utc)
                    for _obs_ticker in list(_d170_watching):
                        _obs_snap = _d170_all_snaps.get(_obs_ticker, {})
                        _obs_price = float(
                            _obs_snap.get("latestTrade", {}).get("p", 0)
                            or _obs_snap.get("last_price", 0) or 0
                        )
                        _obs_vwap = orchestrator._get_vwap(_obs_ticker, 0.0)
                        _obs_vol = float(_obs_snap.get("dailyBar", {}).get("v", 0) or 0)
                        if _obs_price <= 0:
                            continue
                        _obs_new_state = entry_delay_mgr.update_price(
                            ticker=_obs_ticker,
                            price=_obs_price,
                            vwap=_obs_vwap,
                            volume=_obs_vol,
                            timestamp=_d170_now,
                        )
                        if _obs_new_state in (ObservationState.REJECTED, ObservationState.EXPIRED):
                            # doc 272 VLL: deferred verdict died unexecuted (terminal)
                            if _d170_pending_verdicts.pop(_obs_ticker, None) is not None:
                                try:
                                    from src.ops.verdict_ledger import vll_emit
                                    vll_emit("EXPIRED_OBSERVATION", _obs_ticker,
                                             reason=f"observation {_obs_new_state.value}", path="MAIN")
                                except Exception:
                                    pass
                            entry_delay_mgr.remove(_obs_ticker)

                for _obs_cand in entry_delay_mgr.get_approved_candidates():
                    _obs_ticker = _obs_cand.ticker
                    _d170_pending = _d170_pending_verdicts.pop(_obs_ticker, None)
                    if _d170_pending is None:
                        entry_delay_mgr.remove(_obs_ticker)
                        continue
                    _obs_verdict, _obs_scored = _d170_pending
                    # Use current observed price rather than stale Phase 2 entry price
                    if _obs_cand.latest_price > 0:
                        _obs_verdict = _obs_verdict.model_copy(
                            update={"entry_price": _obs_cand.latest_price}
                        )
                    logger.info(
                        "D170 EXECUTING: %s approved after %.1f min | "
                        "entry=$%.2f (was $%.2f) vwap=$%.2f",
                        _obs_ticker, _obs_cand.elapsed_minutes,
                        _obs_cand.latest_price, _obs_cand.open_price,
                        _obs_cand.latest_vwap,
                    )
                    try:
                        _obs_order = await bridge.execute_verdict(_obs_verdict, scored=_obs_scored)
                    except Exception as _oe170:
                        logger.error("D170: execute_verdict failed for %s: %s", _obs_ticker, _oe170)
                        # doc 272 VLL: deferred verdict was popped above, then exec raised —
                        # without this emit the verdict would die with no terminal.
                        try:
                            from src.ops.verdict_ledger import vll_emit
                            vll_emit("ERROR_EXECUTOR", _obs_ticker,
                                     reason=f"D170 deferred exec raised: {str(_oe170)[:80]}",
                                     path="MAIN")
                        except Exception:
                            pass
                        entry_delay_mgr.remove(_obs_ticker)
                        continue
                    entry_delay_mgr.remove(_obs_ticker)
                    if _obs_order is not None:
                        session_trades += 1
                        logger.info(
                            "D170 FILLED: %s qty=%d @ $%.2f (order_id=%s)",
                            _obs_order.ticker, _obs_order.qty,
                            _obs_order.submitted_price, _obs_order.order_id,
                        )
                        # D215: Unified execution recording
                        # Extract candidate from scored (verdict doesn't carry all fields)
                        _obs_candidate = getattr(_obs_scored, "candidate", None) if _obs_scored else None
                        _exec_recorder.record_execution(
                            ticker=_obs_order.ticker, side="buy",
                            fill_price=_obs_order.fill_price if _obs_order.fill_price and _obs_order.fill_price > 0 else _obs_order.submitted_price,
                            signal_price=_obs_order.submitted_price,
                            qty=_obs_order.qty, order_id=_obs_order.order_id,
                            execution_path="D170_OBSERVATION",
                            gap_pct=abs(_obs_candidate.gap_pct) if _obs_candidate else abs(getattr(_obs_verdict, "gap_pct", 0)),
                            rvol=_obs_candidate.rvol if _obs_candidate else getattr(_obs_verdict, "rvol", 0),
                            mfcs=_obs_verdict.mfcs,
                            float_shares=_obs_candidate.float_shares if _obs_candidate else getattr(_obs_verdict, "float_shares", None),
                            market_cap=_obs_candidate.market_cap if _obs_candidate else None,
                            prior_gap_count=getattr(_obs_candidate, "prior_gap_count", None) if _obs_candidate else None,
                            is_day2_runner=getattr(_obs_candidate, "is_day2_runner", False) if _obs_candidate else False,
                            stop_loss=_obs_verdict.stop_loss,
                            target_prices=list(_obs_verdict.target_prices),
                            kelly_tier=_obs_verdict.kelly_tier,
                            vix_level=orchestrator._vix_level,
                            verdict=_obs_verdict, scored=_obs_scored, candidate=_obs_candidate,
                        )

                # ── D96: Per-cycle position snapshot for EOD audit ────
                # Logs current price, unrealized P&L, and % change for every
                # position. Enables post-session replay of price action.
                if _d78_snapshots and positions:
                    _snap_lines = []
                    _total_unrealized = 0.0
                    for _sp in positions:
                        _sp_snap = _d78_snapshots.get(_sp.ticker, {})
                        _sp_price = float(
                            _sp_snap.get("latestTrade", {}).get("p", 0)
                            or _sp_snap.get("last_price", 0) or 0
                        )
                        if _sp_price > 0:
                            # BUG-FIX: Account for short direction in unrealized P&L
                            if getattr(_sp, "direction", "long") == "short":
                                _sp_pnl = (_sp.entry_price - _sp_price) * _sp.remaining_qty
                                _sp_pct = ((1 - _sp_price / _sp.entry_price)) * 100
                            else:
                                _sp_pnl = (_sp_price - _sp.entry_price) * _sp.remaining_qty
                                _sp_pct = ((_sp_price / _sp.entry_price) - 1) * 100
                            _total_unrealized += _sp_pnl
                            _sp_spread = _sp_snap.get("latestQuote", {})
                            _sp_bid = float(_sp_spread.get("bp", 0) or 0)
                            _sp_ask = float(_sp_spread.get("ap", 0) or 0)
                            _sp_spread_pct = (
                                ((_sp_ask - _sp_bid) / _sp_price * 100) if _sp_bid > 0 and _sp_ask > 0 else 0
                            )
                            _snap_lines.append(
                                f"    {_sp.ticker}: ${_sp_price:.2f} ({_sp_pct:+.1f}%) "
                                f"uPnL=${_sp_pnl:+.2f} qty={_sp.remaining_qty} "
                                f"stop=${_sp.stop_loss:.2f} spread={_sp_spread_pct:.2f}% "
                                f"T{_sp.tranches_filled}/3"
                            )
                        else:
                            _snap_lines.append(f"    {_sp.ticker}: NO PRICE DATA")
                    logger.info(
                        "D96 POSITION SNAPSHOT (total_unrealized=$%.2f):\n%s",
                        _total_unrealized, "\n".join(_snap_lines),
                    )

                    # D196: Track position tickers for EOD bar recording
                    _d196_session_tickers.update(_sp.ticker for _sp in positions)

                    # D194: Order flow signal logging (uses latestQuote spread as proxy)
                    try:
                        for _of_pos in positions:
                            _of_snap = _d78_snapshots.get(_of_pos.ticker, {})
                            _of_price = float(
                                _of_snap.get("latestTrade", {}).get("p", 0)
                                or _of_snap.get("last_price", 0) or 0
                            )
                            _of_vol = float(_of_snap.get("dailyBar", {}).get("v", 0) or 0)
                            if _of_price > 0 and _of_vol > 0:
                                # D212: Feed real WebSocket trades to order flow
                                _of_bid = float(
                                    _of_snap.get("latestQuote", {}).get("bp", _of_price) or _of_price
                                )
                                _of_ask = float(
                                    _of_snap.get("latestQuote", {}).get("ap", _of_price) or _of_price
                                )
                                _of_result = order_flow_analyzer.analyze_trades(
                                    ticker=_of_pos.ticker,
                                    trades=_trade_buffers.get(_of_pos.ticker, []),
                                    bid=_of_bid,
                                    ask=_of_ask,
                                )
                                if _of_result.signal.value not in ("NEUTRAL", "INSUFFICIENT_DATA"):
                                    logger.debug(
                                        "D194 ORDER FLOW: %s signal=%s inst_footprint=%s",
                                        _of_pos.ticker, _of_result.signal.value,
                                        _of_result.has_institutional_footprint(),
                                    )
                    except Exception as _of_e:
                        logger.debug("D194: Order flow analysis failed (non-fatal): %s", _of_e)

                # ── D63: Trailing Stop Activation ─────────────────────
                # Wire the existing compute_trailing_stop() and
                # should_activate_trailing_stop() methods (D43) into Phase 3.
                if _d78_snapshots and positions:
                    for _ts_pos in list(positions):
                        try:
                            # D93: Skip trailing stop eval for tickers that just had a tranche fill
                            if _ts_pos.ticker in _tranche_cooldown_tickers:
                                logger.debug(
                                    "D93 COOLDOWN: Skipping trailing stop for %s (tranche filled this cycle)",
                                    _ts_pos.ticker,
                                )
                                continue

                            _ts_snap = _d78_snapshots.get(_ts_pos.ticker, {})
                            _ts_price = float(
                                _ts_snap.get("latestTrade", {}).get("p", 0)
                                or _ts_snap.get("last_price", 0) or 0
                            )
                            if _ts_price <= 0:
                                continue

                            # Update peak price
                            if _ts_price > _ts_pos.peak_price:
                                _ts_pos.peak_price = _ts_price

                            # Check if trailing stop should activate (+2%)
                            if position_manager.should_activate_trailing_stop(_ts_pos, _ts_price):
                                _ts_pos.trailing_stop_active = True
                                # D89b: Compute ATR from exit intelligence bar history
                                _ts_atr = exit_intelligence.get_atr(_ts_pos.ticker)
                                _ts_new_stop = position_manager.compute_trailing_stop(
                                    current_price=_ts_price,
                                    entry_price=_ts_pos.entry_price,
                                    current_stop=_ts_pos.stop_loss,
                                    atr=_ts_atr,
                                    peak_price=_ts_pos.peak_price,  # D89: Chandelier Exit
                                )
                                if _ts_new_stop > _ts_pos.stop_loss:
                                    _ts_old_stop = _ts_pos.stop_loss
                                    _ts_pos.stop_loss = _ts_new_stop
                                    try:
                                        _ts_result = await stop_resubmitter.resubmit(
                                            ticker=_ts_pos.ticker,
                                            new_stop_price=_ts_new_stop,
                                            new_qty=_ts_pos.remaining_qty,
                                        )
                                        if _ts_result and _ts_result.success:
                                            logger.info(
                                                "D63 TRAILING STOP: %s $%.2f -> $%.2f (price=$%.2f, +%.1f%%)",
                                                _ts_pos.ticker, _ts_old_stop, _ts_new_stop,
                                                _ts_price,
                                                (_ts_new_stop / _ts_pos.entry_price - 1) * 100,
                                            )
                                    except Exception as _ts_e:
                                        logger.warning("D63 trailing stop resubmit error %s: %s", _ts_pos.ticker, _ts_e)
                                    # Persist state
                                    try:
                                        state_mgr.update_position(
                                            ticker=_ts_pos.ticker,
                                            stop_loss=_ts_new_stop,
                                        )
                                        state_mgr.save()
                                    except Exception as e:
                                        logger.warning("D218: state_persistence failed: %s", e)
                        except Exception as _ts_e2:
                            logger.debug("D63 trailing check error %s: %s", _ts_pos.ticker, _ts_e2)

                # ── D164: Time-based Early Profit Take ────────────────
                # Sell exit_pct (50%) of position at T+2min if profitable.
                # Phase 2 tasks handle 9:30-10 AM entries. This block handles
                # Phase 3 (10 AM+) entries and any Phase 2 entries still within
                # the max_delay_seconds window.
                if _d164_cfg.enabled and _d78_snapshots and positions:
                    _d164_now = datetime.now(timezone.utc)
                    for _d164_pos in list(positions):
                        try:
                            _d164_snap = _d78_snapshots.get(_d164_pos.ticker, {})
                            _d164_price = float(
                                _d164_snap.get("latestTrade", {}).get("p", 0)
                                or _d164_snap.get("last_price", 0) or 0
                            )
                            if _d164_price <= 0:
                                continue

                            _d164_action = early_profit_taker.check(
                                _d164_pos.ticker, _d164_price, _d164_now
                            )

                            if _d164_action != EarlyProfitAction.TAKE_PROFIT:
                                continue

                            # ── TAKE_PROFIT: submit partial sell ──
                            _d164_qty_to_sell = early_profit_taker.qty_to_sell(_d164_pos.ticker)
                            # Clamp to actual remaining qty (tranches may have reduced it)
                            _d164_qty_to_sell = min(_d164_qty_to_sell, _d164_pos.remaining_qty)
                            if _d164_qty_to_sell <= 0:
                                early_profit_taker.mark_skipped(_d164_pos.ticker)
                                continue

                            # D160 RACE FIX: mark as skipped BEFORE first await to
                            # prevent re-entry on the next cycle if this loop runs
                            # concurrently with the Phase 2 asyncio task.
                            early_profit_taker.mark_skipped(_d164_pos.ticker)

                            # doc 269 (A1): route the partial exit through the broker-
                            # CONFIRMED close path (same Bug-Z hardening as D78 SMART_EXIT
                            # ~6280 / D163 ~6080). The prior submit_order + book-from-
                            # snapshot pattern booked PHANTOM P&L whenever the sell 403'd
                            # against the OTO protective stop (qty held_for_orders) or was
                            # merely accepted-not-filled.
                            from src.execution.bridge import attempt_close_with_status_check
                            _d164_sell_price = 0.0
                            try:
                                _d164_close_res = await attempt_close_with_status_check(
                                    client=client,
                                    ticker=_d164_pos.ticker,
                                    qty=_d164_qty_to_sell,
                                    max_retries=3,
                                    retry_backoff_s=1.0,
                                    cancel_blocking_stops_first=True,
                                    partial=True,
                                )
                            except Exception as _d164_submit_err:
                                logger.error(
                                    "D269 EARLY_PROFIT_ESCALATE %s (P3): close helper "
                                    "raised (%s) — no P&L booked, stop untouched, "
                                    "position stays tracked + protected.",
                                    _d164_pos.ticker, _d164_submit_err,
                                )
                                # Leave mark_skipped=True — don't retry on error
                                continue
                            # Bug-Z gate (doc 269): broker close NOT confirmed → do NOT
                            # book P&L, do NOT cancel/remove stops, position stays tracked
                            # + fully protected (the helper re-arms any stop IT cancelled
                            # via D249). Leave mark_skipped=True — don't retry.
                            if not _d164_close_res["succeeded"] or not _d164_close_res["fill_price"]:
                                logger.error(
                                    "D269 EARLY_PROFIT_ESCALATE %s (P3): broker partial "
                                    "close FAILED/unconfirmed (last_error=%s) — no P&L "
                                    "booked, stop not cancelled by caller, position "
                                    "remains tracked.",
                                    _d164_pos.ticker,
                                    _d164_close_res.get("last_error"),
                                )
                                try:
                                    _d164_pos.close_attempt_failed = True
                                except Exception as _caf_e:
                                    logger.debug(
                                        "D269: close_attempt_failed flag set failed: %s",
                                        _caf_e,
                                    )
                                continue
                            # Book ONLY from the broker-confirmed fill — never the
                            # snapshot price (doc 269).
                            _d164_sell_price = float(_d164_close_res["fill_price"])
                            _d164_qty_done = min(
                                int(_d164_close_res.get("filled_qty") or _d164_qty_to_sell),
                                _d164_qty_to_sell,
                            )

                            # Update position tracking (mirrors tranche fill pattern)
                            _d164_pos.remaining_qty = max(0, _d164_pos.remaining_qty - _d164_qty_done)
                            if _d164_pos.direction == "long":
                                _d164_partial_pnl = (
                                    (_d164_sell_price - _d164_pos.entry_price) * _d164_qty_done
                                )
                            else:
                                _d164_partial_pnl = (
                                    (_d164_pos.entry_price - _d164_sell_price) * _d164_qty_done
                                )
                            bridge.position_manager.record_realized_pnl(_d164_partial_pnl)
                            _d164_pos.realized_pnl += _d164_partial_pnl
                            try:
                                from src.monitoring.metrics import get_metrics as _gm_d164p3
                                _gm_d164p3().daily_pnl.inc(_d164_partial_pnl)
                            except Exception as e:
                                logger.warning("D218: pnl_recording failed: %s", e)

                            # Properly mark executed
                            _d164_ep_state = early_profit_taker.get_state(_d164_pos.ticker)
                            if _d164_ep_state is not None:
                                _d164_ep_state.skipped = False
                            early_profit_taker.mark_executed(
                                _d164_pos.ticker, _d164_sell_price, _d164_qty_done
                            )

                            _d164_elapsed = 0.0
                            _d164_ep_state2 = early_profit_taker.get_state(_d164_pos.ticker)
                            if _d164_ep_state2:
                                _d164_elapsed = (
                                    _d164_now - _d164_ep_state2.fill_time
                                ).total_seconds()
                            _d164_profit_pct = (
                                ((_d164_sell_price / _d164_pos.entry_price) - 1) * 100
                                if _d164_pos.direction == "long" and _d164_pos.entry_price > 0
                                else ((_d164_pos.entry_price / _d164_sell_price) - 1) * 100
                                if _d164_pos.entry_price > 0 else 0
                            )
                            logger.info(
                                "D164 EARLY PROFIT TAKE (P3): %s sold %d shares at $%.4f "
                                "(+%.2f%%) T+%.0fs — remaining=%d",
                                _d164_pos.ticker, _d164_qty_done,
                                _d164_sell_price, _d164_profit_pct, _d164_elapsed,
                                _d164_pos.remaining_qty,
                            )
                            # doc 269: the helper may have cancelled the OTO protective
                            # stop to free held qty. Either way the live stop must now
                            # cover ONLY the remaining shares — re-arm via the D186
                            # resubmitter (doc-186 D165 contract).
                            if _d164_close_res.get("cancelled_stops"):
                                _d164_pos.stop_order_id = ""
                            if _d164_pos.remaining_qty > 0:
                                try:
                                    _d269_p3_stop_r = await stop_resubmitter.resubmit(
                                        ticker=_d164_pos.ticker,
                                        new_stop_price=_d164_pos.stop_loss,
                                        new_qty=_d164_pos.remaining_qty,
                                    )
                                    if _d269_p3_stop_r and _d269_p3_stop_r.success:
                                        logger.info(
                                            "D269 STOP RESIZED: %s stop covers %d shares "
                                            "after early take",
                                            _d164_pos.ticker, _d164_pos.remaining_qty,
                                        )
                                    else:
                                        logger.warning(
                                            "D269 stop resize failed %s: %s — D313 "
                                            "hedge-watcher backstop",
                                            _d164_pos.ticker,
                                            getattr(_d269_p3_stop_r, "error", "?"),
                                        )
                                except Exception as _d269_p3_stop_e:
                                    logger.warning(
                                        "D269 stop resize failed %s: %s",
                                        _d164_pos.ticker, _d269_p3_stop_e,
                                    )
                            # Journal entry
                            try:
                                for _j_tid, _j_ent in trade_journal._entries.items():
                                    if _j_ent.ticker == _d164_pos.ticker and _j_ent.action == "BUY":
                                        trade_journal.record_close(
                                            _j_tid,
                                            exit_price=_d164_sell_price,
                                            realized_pnl=_d164_partial_pnl,
                                            exit_time=_d164_now,
                                            exit_reason="EARLY_PROFIT_TAKE",
                                        )
                                        break
                            except Exception:
                                pass
                        except Exception as _d164_loop_e:
                            logger.debug(
                                "D164 early profit check error %s: %s",
                                _d164_pos.ticker, _d164_loop_e,
                            )

                # ── D165: Price-based Tranche Profit-Taking ───────────────
                # Fallback for when limit-order tranches weren't submitted (D100
                # OTO failure) or never filled. Checks each cycle whether current
                # price has crossed a tranche target and fires a market sell.
                # Fixes: ARTL entered $7.68, hit $8.22 (+7%) → T1($7.91) + T2($8.14)
                # both crossed with 0 tranches fired. Same for SST.
                # Priority: D164 (partial T+2min) → D165 (price targets) → D163 (trail)
                if _d165_cfg.enabled and _d78_snapshots and positions:
                    for _d165_pos in list(positions):
                        try:
                            if _d165_pos.ticker in _tranche_cooldown_tickers:
                                continue
                            if not _d165_pos.target_prices:
                                continue
                            _d165_snap = _d78_snapshots.get(_d165_pos.ticker, {})
                            _d165_price = float(
                                _d165_snap.get("latestTrade", {}).get("p", 0)
                                or _d165_snap.get("last_price", 0) or 0
                            )
                            if _d165_price <= 0:
                                continue

                            _d165_hits = tranche_taker.check(_d165_pos, _d165_price)
                            for _d165_hit in _d165_hits:
                                _d165_t_num = _d165_hit.tranche_number
                                _d165_t_qty = _d165_hit.qty
                                logger.info(
                                    "D165 TRANCHE T%d HIT: %s target=$%.2f current=$%.2f "
                                    "selling %d shares (+%.1f%%)",
                                    _d165_t_num, _d165_pos.ticker,
                                    _d165_hit.target_price, _d165_price,
                                    _d165_t_qty,
                                    ((_d165_price / _d165_pos.entry_price) - 1) * 100
                                    if _d165_pos.entry_price > 0 else 0,
                                )
                                # Mark fired BEFORE await to prevent re-entry next cycle
                                tranche_taker.mark_fired(_d165_pos.ticker, _d165_t_num)
                                _d165_side = (
                                    "sell" if _d165_pos.direction == "long" else "buy"
                                )
                                _d165_fill_px = _d165_price
                                # doc 186 (B2 fix): the standalone protective stop reserves
                                # the FULL position qty (held_for_orders), so a plain tranche
                                # sell 403s "insufficient qty" — the bot fights its own stop.
                                # On 5/29 CMND/STG tranches 403'd every cycle and the position
                                # ghosted. Cancel the blocking stop FIRST so the qty is free;
                                # we ALWAYS re-arm below (success: remaining qty; failure: full).
                                _d165_stop_oid = getattr(_d165_pos, "stop_order_id", "") or ""
                                if _d165_stop_oid:
                                    try:
                                        await client.cancel_order(_d165_stop_oid)
                                        _d165_pos.stop_order_id = ""
                                        import asyncio as _d165_aio
                                        await _d165_aio.sleep(0.4)  # let broker release held_for_orders
                                    except Exception as _d165_cx:
                                        logger.warning(
                                            "D165 T%d %s: pre-sell stop-cancel failed (%s) — sell may 403",
                                            _d165_t_num, _d165_pos.ticker, _d165_cx,
                                        )
                                # doc 269 (A1): route the partial sell through the broker-
                                # CONFIRMED close path (same Bug-Z hardening as D78
                                # SMART_EXIT ~6280 / D163 ~6080). The old submit_order +
                                # filled_avg_price-or-SNAPSHOT booking booked PHANTOM P&L
                                # for orders that were merely ACCEPTED (or later rejected)
                                # — same family as the D163 fix (doc 263).
                                from src.execution.bridge import attempt_close_with_status_check
                                _d165_close_failed = False
                                _d165_fail_err: Any = None
                                try:
                                    _d165_close_res = await attempt_close_with_status_check(
                                        client=client,
                                        ticker=_d165_pos.ticker,
                                        qty=_d165_t_qty,
                                        max_retries=3,
                                        retry_backoff_s=1.0,
                                        cancel_blocking_stops_first=True,
                                        partial=True,
                                    )
                                except Exception as _d165_sell_e:  # defensive — helper is non-raising
                                    _d165_close_failed = True
                                    _d165_fail_err = _d165_sell_e
                                else:
                                    if (
                                        _d165_close_res["succeeded"]
                                        and _d165_close_res["fill_price"]
                                    ):
                                        # Book ONLY from the broker-confirmed fill —
                                        # never the snapshot price (doc 269).
                                        _d165_fill_px = float(_d165_close_res["fill_price"])
                                        _d165_t_qty = min(
                                            int(
                                                _d165_close_res.get("filled_qty")
                                                or _d165_t_qty
                                            ),
                                            _d165_t_qty,
                                        )
                                        if _d165_close_res.get("cancelled_stops"):
                                            _d165_pos.stop_order_id = ""
                                    else:
                                        _d165_close_failed = True
                                        _d165_fail_err = _d165_close_res.get("last_error")
                                if _d165_close_failed:
                                    # Bug-Z gate (doc 269): broker close NOT confirmed →
                                    # do NOT book P&L. Tranche is forfeited (no retry —
                                    # could double-sell); protection re-armed below.
                                    logger.error(
                                        "D269 D165 TRANCHE T%d sell FAILED/unconfirmed %s: %s "
                                        "— no P&L booked, re-arming protective stop, tranche "
                                        "forfeited (no retry — could double-sell)",
                                        _d165_t_num, _d165_pos.ticker, _d165_fail_err,
                                    )
                                    try:
                                        _d165_pos.close_attempt_failed = True
                                    except Exception as _caf_e:
                                        logger.debug(
                                            "D269: close_attempt_failed flag set "
                                            "failed: %s", _caf_e,
                                        )
                                    tranche_taker.mark_fired(
                                        _d165_pos.ticker, _d165_t_num
                                    )  # Don't retry failed sells (could double-sell)
                                    # doc 186: we cancelled the protective stop pre-sell; the
                                    # sell failed so NOTHING sold — re-arm protection for the
                                    # full remaining qty (never leave the position naked).
                                    if _d165_stop_oid:
                                        try:
                                            await stop_resubmitter.resubmit(
                                                ticker=_d165_pos.ticker,
                                                new_stop_price=_d165_pos.stop_loss,
                                                new_qty=_d165_pos.remaining_qty,
                                            )
                                        except Exception as _d165_rr_e:
                                            logger.warning(
                                                "D165 T%d %s: stop re-arm after failed sell "
                                                "FAILED: %s — D313 hedge-watcher backstop",
                                                _d165_t_num, _d165_pos.ticker, _d165_rr_e,
                                            )
                                    break

                                # Update position state
                                _d165_pos.remaining_qty = max(
                                    0, _d165_pos.remaining_qty - _d165_t_qty
                                )
                                _d165_pos.tranches_filled = max(
                                    _d165_pos.tranches_filled, _d165_t_num
                                )
                                if _d165_pos.direction == "long":
                                    _d165_pnl = (
                                        (_d165_fill_px - _d165_pos.entry_price)
                                        * _d165_t_qty
                                    )
                                else:
                                    _d165_pnl = (
                                        (_d165_pos.entry_price - _d165_fill_px)
                                        * _d165_t_qty
                                    )
                                bridge.position_manager.record_realized_pnl(_d165_pnl)
                                _d165_pos.realized_pnl += _d165_pnl
                                try:
                                    from src.monitoring.metrics import (
                                        get_metrics as _gm_d165,
                                    )
                                    _gm_d165().daily_pnl.inc(_d165_pnl)
                                except Exception:
                                    pass

                                logger.info(
                                    "D165 TRANCHE T%d FILLED: %s sold %d at $%.4f "
                                    "pnl=$%.2f remaining=%d",
                                    _d165_t_num, _d165_pos.ticker,
                                    _d165_t_qty, _d165_fill_px,
                                    _d165_pnl, _d165_pos.remaining_qty,
                                )

                                # doc 186: re-arm the protective stop for the remaining qty.
                                # We cancelled the standalone stop pre-sell (B2 403 fix), so
                                # re-arming is now MANDATORY (no longer gated on
                                # adjust_stop_after_partial) — else the remaining position is
                                # left naked after a successful tranche sell.
                                if _d165_pos.remaining_qty > 0:
                                    try:
                                        _d165_stop_r = await stop_resubmitter.resubmit(
                                            ticker=_d165_pos.ticker,
                                            new_stop_price=_d165_pos.stop_loss,
                                            new_qty=_d165_pos.remaining_qty,
                                        )
                                        if _d165_stop_r and _d165_stop_r.success:
                                            logger.info(
                                                "D165 STOP RESIZED: %s stop covers "
                                                "%d shares (remaining after T%d)",
                                                _d165_pos.ticker,
                                                _d165_pos.remaining_qty, _d165_t_num,
                                            )
                                    except Exception as _d165_stop_e:
                                        logger.warning(
                                            "D165 stop resize failed %s: %s",
                                            _d165_pos.ticker, _d165_stop_e,
                                        )

                                # Persist state
                                try:
                                    state_mgr.update_position(
                                        ticker=_d165_pos.ticker,
                                        tranches_filled=_d165_pos.tranches_filled,
                                        remaining_qty=_d165_pos.remaining_qty,
                                        realized_pnl=_d165_pos.realized_pnl,
                                    )
                                    state_mgr.save()
                                except Exception as e:
                                    logger.warning("D218: state_persistence failed: %s", e)

                                # Add to cooldown so D163/D78 don't double-fire
                                _tranche_cooldown_tickers.add(_d165_pos.ticker)

                                # If fully closed, clean up all tracking
                                if _d165_pos.remaining_qty <= 0:
                                    await stop_resubmitter.cancel_and_remove(
                                        _d165_pos.ticker
                                    )
                                    await tranche_monitor.cancel_all_for_ticker(
                                        _d165_pos.ticker, client
                                    )
                                    trailing_manager.remove_position(_d165_pos.ticker)
                                    early_profit_taker.remove(_d165_pos.ticker)
                                    tranche_taker.remove(_d165_pos.ticker)
                                    entry_delay_mgr.remove(_d165_pos.ticker)  # D170
                                    # doc 272 VLL: terminal if a deferred verdict was parked here
                                    if _d170_pending_verdicts.pop(_d165_pos.ticker, None) is not None:  # D170
                                        try:
                                            from src.ops.verdict_ledger import vll_emit
                                            vll_emit("EXPIRED_OBSERVATION", _d165_pos.ticker,
                                                     reason="deferred verdict superseded by position close",
                                                     path="MAIN")
                                        except Exception:
                                            pass
                                    enriched = await bridge.close_with_attribution(
                                        ticker=_d165_pos.ticker,
                                        exit_price=_d165_fill_px,
                                    )
                                    if enriched:
                                        session_trades += 1
                                    try:
                                        state_mgr.remove_position(_d165_pos.ticker)
                                        state_mgr.save()
                                    except Exception as e:
                                        logger.warning("D218: position_removal failed: %s", e)
                                    logger.info(
                                        "D165 POSITION FULLY CLOSED: %s via T3 tranche",
                                        _d165_pos.ticker,
                                    )
                                    break  # Position gone — stop processing hits

                        except Exception as _d165_outer_e:
                            logger.debug(
                                "D165 tranche check error %s: %s",
                                _d165_pos.ticker, _d165_outer_e,
                            )

                # ── D170: Entry Delay Observation Window ──────────────────
                # Check all watching candidates each cycle. Fetch fresh snapshots
                # since these tickers are NOT positions (not in _d78_snapshots).
                _d170_watching = entry_delay_mgr.watching_tickers
                if entry_delay_mgr.config.enabled and _d170_watching:
                    try:
                        _d170_snaps = await client.get_snapshots(_d170_watching)
                    except Exception as _d170_snap_e:
                        logger.warning("D170 snapshot fetch error: %s", _d170_snap_e)
                        _d170_snaps = {}
                    _d170_now = datetime.now(timezone.utc)
                    for _d170_ticker in list(_d170_watching):
                        try:
                            _d170_snap = _d170_snaps.get(_d170_ticker, {})
                            _d170_price = float(
                                _d170_snap.get("latestTrade", {}).get("p", 0)
                                or _d170_snap.get("last_price", 0) or 0
                            )
                            if _d170_price <= 0:
                                continue
                            _d170_vwap = float(orchestrator._get_vwap(_d170_ticker, 0.0) or 0.0)
                            if _d170_vwap <= 0:
                                _d170_vwap = float(_d170_snap.get("vwap", 0) or 0)
                            _d170_state = entry_delay_mgr.update_price(
                                _d170_ticker, _d170_price, _d170_vwap, 0.0, _d170_now
                            )
                            if _d170_state == ObservationState.APPROVED:
                                _d170_pending = _d170_pending_verdicts.pop(_d170_ticker, None)
                                entry_delay_mgr.remove(_d170_ticker)
                                if _d170_pending:
                                    _d170_verdict, _d170_scored = _d170_pending
                                    order = await bridge.execute_verdict(
                                        _d170_verdict, scored=_d170_scored
                                    )
                                    if order is not None:
                                        session_trades += 1
                                        logger.info(
                                            "D170 APPROVED ENTRY: %s filled @ $%.2f after observation",
                                            _d170_ticker, order.submitted_price,
                                        )
                            elif _d170_state in (ObservationState.REJECTED, ObservationState.EXPIRED):
                                entry_delay_mgr.remove(_d170_ticker)
                                # doc 272 VLL: deferred verdict died unexecuted (terminal)
                                if _d170_pending_verdicts.pop(_d170_ticker, None) is not None:
                                    try:
                                        from src.ops.verdict_ledger import vll_emit
                                        vll_emit("EXPIRED_OBSERVATION", _d170_ticker,
                                                 reason=f"observation {_d170_state.value}", path="MAIN")
                                    except Exception:
                                        pass
                        except Exception as _d170_e:
                            logger.debug(
                                "D170 observation check error %s: %s", _d170_ticker, _d170_e
                            )

                # ── D163: Software Trailing Stop ──────────────────────
                # Pure software trail — no Alpaca order is modified.
                # Runs alongside D63 (Chandelier ratchet) and D78 (SMART_EXIT).
                # Whichever fires first wins.  Activation: +2% gain, trail at 50%
                # of max gain from peak.  Breached trail → immediate market EXIT.
                if _trail_cfg.enabled and _d78_snapshots and positions:
                    for _tr_pos in list(positions):
                        try:
                            # D93: Skip if tranche just filled this cycle
                            if _tr_pos.ticker in _tranche_cooldown_tickers:
                                continue

                            _tr_snap = _d78_snapshots.get(_tr_pos.ticker, {})
                            _tr_price = float(
                                _tr_snap.get("latestTrade", {}).get("p", 0)
                                or _tr_snap.get("last_price", 0) or 0
                            )
                            if _tr_price <= 0:
                                continue

                            # Lazy-register: idempotent, skips if already registered
                            trailing_manager.register_position(
                                symbol=_tr_pos.ticker,
                                entry_price=_tr_pos.entry_price,
                                direction=_tr_pos.direction,
                                initial_stop=_tr_pos.stop_loss,
                            )

                            _tr_action = trailing_manager.update_price(_tr_pos.ticker, _tr_price)

                            if _tr_action == TrailingStopAction.EXIT:
                                # D93: Suppress EXIT for tickers that just had a tranche fill
                                if _tr_pos.ticker in _tranche_cooldown_tickers:
                                    logger.info(
                                        "D93 COOLDOWN: Suppressing D163 EXIT for %s "
                                        "(tranche filled this cycle)",
                                        _tr_pos.ticker,
                                    )
                                    continue

                                _tr_state = trailing_manager.get_state(_tr_pos.ticker)
                                _max_gain_pct = 0.0
                                if _tr_state:
                                    if _tr_pos.direction == "long":
                                        _max_gain_pct = (
                                            (_tr_state.max_favorable_price / _tr_pos.entry_price - 1) * 100
                                            if _tr_pos.entry_price > 0 else 0.0
                                        )
                                    else:
                                        _max_gain_pct = (
                                            (_tr_pos.entry_price / _tr_state.max_favorable_price - 1) * 100
                                            if _tr_state.max_favorable_price > 0 else 0.0
                                        )

                                logger.info(
                                    "═══ D163 TRAILING STOP EXIT: %s price=$%.2f "
                                    "trail=$%.2f max_gain=+%.1f%% ═══",
                                    _tr_pos.ticker, _tr_price,
                                    _tr_state.current_trail_level if _tr_state else 0.0,
                                    _max_gain_pct,
                                )

                                # D262 PHANTOM-FIX: route the trailing-stop close through the
                                # broker-CONFIRMED close path (same hardening as D78 SMART_EXIT
                                # at lines ~6280). The prior path (close_position + bare-except
                                # fallback + MTM snapshot price) booked PHANTOM P&L whenever the
                                # close 403'd against the OTO child stop (qty held_for_orders):
                                # TNGX/ABAT on 2026-06-08 booked +747/+730 of MTM the broker never
                                # realized, then cancelled the stop and left the position naked
                                # until the EOD force-close. Mirror the D78 Bug-Z gate.
                                from src.execution.bridge import attempt_close_with_status_check
                                _tr_exit_price = 0.0
                                _tr_close_result = await attempt_close_with_status_check(
                                    client=client,
                                    ticker=_tr_pos.ticker,
                                    qty=_tr_pos.remaining_qty,
                                    max_retries=settings.ops.smart_exit_max_retries
                                    if hasattr(settings.ops, "smart_exit_max_retries") else 3,
                                    retry_backoff_s=1.0,
                                    cancel_blocking_stops_first=True,
                                )
                                # Bug-Z gate: if the broker close FAILED, do NOT cancel stops and
                                # do NOT book P&L. Leave the position tracked + protected; the EOD
                                # failsafe / next session handles it. Prevents phantom-MTM booking.
                                if not _tr_close_result["succeeded"]:
                                    logger.error(
                                        "D262 D163 TRAILING_ESCALATE %s: broker close FAILED -- "
                                        "position remains tracked + protected, no P&L booked "
                                        "(prevents phantom-MTM booking).",
                                        _tr_pos.ticker,
                                    )
                                    try:
                                        _tr_pos.close_attempt_failed = True
                                    except Exception:
                                        pass
                                    continue
                                if _tr_close_result["fill_price"]:
                                    _tr_exit_price = float(_tr_close_result["fill_price"])

                                if _tr_exit_price <= 0:
                                    _tr_sx = _d78_snapshots.get(_tr_pos.ticker, {})
                                    _tr_exit_price = float(
                                        _tr_sx.get("latestTrade", {}).get("p", 0)
                                        or _tr_sx.get("last_price", 0) or 0
                                    )

                                _tr_exit_qty = _tr_pos.remaining_qty

                                # Cancel orphaned stop and tranche orders (D121 BUG-P5)
                                await stop_resubmitter.cancel_and_remove(_tr_pos.ticker)
                                await tranche_monitor.cancel_all_for_ticker(_tr_pos.ticker, client)

                                # Remove from software trail tracker
                                trailing_manager.remove_position(_tr_pos.ticker)
                                early_profit_taker.remove(_tr_pos.ticker)  # D164
                                tranche_taker.remove(_tr_pos.ticker)  # D165
                                entry_delay_mgr.remove(_tr_pos.ticker)  # D170
                                # doc 272 VLL: terminal if a deferred verdict was parked here
                                if _d170_pending_verdicts.pop(_tr_pos.ticker, None) is not None:  # D170
                                    try:
                                        from src.ops.verdict_ledger import vll_emit
                                        vll_emit("EXPIRED_OBSERVATION", _tr_pos.ticker,
                                                 reason="deferred verdict superseded by position close",
                                                 path="MAIN")
                                    except Exception:
                                        pass

                                # Internal close + attribution
                                _tr_enriched = await bridge.close_with_attribution(
                                    ticker=_tr_pos.ticker,
                                    exit_price=_tr_exit_price or 0,
                                )

                                if _tr_enriched:
                                    session_trades += 1
                                    _tr_total_pnl = _tr_enriched.pnl * _tr_exit_qty
                                    # Prevent re-entry (D94b)
                                    _stopped_out_tickers.add(_tr_pos.ticker)
                                    try:
                                        state_mgr.add_stopped_out_ticker(_tr_pos.ticker)
                                        state_mgr.save()
                                    except Exception as e:
                                        logger.warning("D218: stopped_ticker_tracking failed: %s", e)
                                    logger.info(
                                        "D163 TRAILING EXIT: %s exit=$%.2f pnl=$%.2f (%.1f%%) "
                                        "trail=$%.2f max_gain=+%.1f%% entry=$%.2f qty=%d",
                                        _tr_pos.ticker, _tr_enriched.exit_price, _tr_total_pnl,
                                        ((_tr_enriched.exit_price / _tr_pos.entry_price - 1) * 100)
                                        if _tr_pos.entry_price > 0 else 0,
                                        _tr_state.current_trail_level if _tr_state else 0.0,
                                        _max_gain_pct,
                                        _tr_pos.entry_price, _tr_exit_qty,
                                    )
                                    # D198: Record trailing exit for regime tracking
                                    session_regime.record_trade_result(
                                        pnl=_tr_total_pnl, is_win=(_tr_total_pnl > 0)
                                    )
                                    _exec_recorder.record_close(
                                        _tr_pos.ticker, _tr_enriched.exit_price,
                                        _tr_exit_qty, _tr_total_pnl, "TRAILING_STOP",
                                    )
                                    logger.debug("D198 %s", session_regime.get_status_report())
                                    # Journal with exit reason
                                    try:
                                        for j_tid, j_ent in trade_journal._entries.items():
                                            if j_ent.ticker == _tr_pos.ticker and j_ent.action == "BUY":
                                                trade_journal.record_close(
                                                    j_tid,
                                                    exit_price=_tr_enriched.exit_price,
                                                    realized_pnl=_tr_total_pnl,
                                                    exit_time=datetime.now(timezone.utc),
                                                    exit_reason="TRAILING_STOP",
                                                )
                                                break
                                    except Exception:
                                        pass
                                    try:
                                        state_mgr.remove_position(_tr_pos.ticker)
                                        state_mgr.update_daily_pnl(position_manager._daily_realized_pnl)
                                        state_mgr.save()
                                    except Exception as e:
                                        logger.warning("D218: position_removal failed: %s", e)
                        except Exception as _tr_e:
                            logger.debug(
                                "D163 trailing stop check error %s: %s", _tr_pos.ticker, _tr_e
                            )

                # ── D78: Smart Exit Intelligence ──────────────────────
                if exit_config.enabled and _d78_snapshots and positions:
                    try:
                        # Build VWAP lookup from orchestrator
                        _d78_vwap_lookup: dict[str, float] = {}
                        for _vt in _d78_position_tickers:
                            try:
                                _vv = orchestrator._get_vwap(_vt, 0.0)
                                if _vv > 0:
                                    _d78_vwap_lookup[_vt] = _vv
                            except Exception:
                                pass

                        _d78_actions = exit_intelligence.evaluate_positions(
                            positions=positions,
                            snapshots=_d78_snapshots,
                            hour_et=hour_et,
                            minute_et=now_et.minute,
                            vwap_lookup=_d78_vwap_lookup,
                        )
                        # D107 WS1: Log all signal values per position per cycle
                        for _sl_ea in _d78_actions:
                            try:
                                _sl_snap = _d78_snapshots.get(_sl_ea.ticker, {})
                                _sl_price = float(
                                    _sl_snap.get("latestTrade", {}).get("p", 0)
                                    or _sl_snap.get("last_price", 0)
                                    or 0
                                )
                                _sl_entry = 0.0
                                for _sl_pos in positions:
                                    if _sl_pos.ticker == _sl_ea.ticker:
                                        _sl_entry = _sl_pos.entry_price
                                        break
                                _sl_pnl = ((_sl_price - _sl_entry) / _sl_entry) if _sl_entry > 0 else 0.0
                                signal_logger.log_cycle(
                                    ticker=_sl_ea.ticker,
                                    signal=_sl_ea.signal,
                                    current_price=_sl_price,
                                    entry_price=_sl_entry,
                                    pnl_pct=_sl_pnl,
                                    # D109 Phase 4: parallel strategy metrics
                                    parallel_log=getattr(_sl_ea.signal, "_parallel_log", None),
                                    intraday_atr=getattr(_sl_ea.signal, "_intraday_atr", None),
                                    mfe_pct=getattr(_sl_ea.signal, "_mfe_pct", None),
                                    time_held_min=getattr(_sl_ea.signal, "_time_held_min", None),
                                )
                            except Exception:
                                pass  # D107: fire-and-forget, never affect trading
                        for _ea in _d78_actions:
                            if _ea.action == "TIGHTEN" and _ea.new_stop:
                                logger.info(
                                    "D78 TIGHTEN: %s stop -> $%.2f (urgency=%.2f, %s)",
                                    _ea.ticker, _ea.new_stop,
                                    _ea.signal.composite_exit_urgency,
                                    _ea.signal.reasoning,
                                )
                                # Find position and update stop
                                for _tp in positions:
                                    if _tp.ticker == _ea.ticker and _ea.new_stop > _tp.stop_loss:
                                        _tp.stop_loss = _ea.new_stop
                                        try:
                                            await stop_resubmitter.resubmit(
                                                ticker=_ea.ticker,
                                                new_stop_price=_ea.new_stop,
                                                new_qty=_tp.remaining_qty,
                                            )
                                        except Exception as _te:
                                            logger.warning("D78 tighten resubmit error: %s", _te)
                                        try:
                                            state_mgr.update_position(ticker=_ea.ticker, stop_loss=_ea.new_stop)
                                            state_mgr.save()
                                        except Exception as e:
                                            logger.warning("D218: state_persistence failed: %s", e)
                                        break

                            elif _ea.action == "EXIT":
                                # D93: Suppress EXIT for tickers that just had a tranche fill
                                if _ea.ticker in _tranche_cooldown_tickers:
                                    logger.info(
                                        "D93 COOLDOWN: Suppressing EXIT for %s (tranche filled this cycle, urgency=%.2f)",
                                        _ea.ticker, _ea.signal.composite_exit_urgency,
                                    )
                                    continue
                                # D217: Verify position still exists before closing.
                                # Another exit path (stop-out, D164, trailing stop) may have
                                # already closed this position during this Phase 3 cycle.
                                if not position_manager.has_position(_ea.ticker):
                                    logger.info(
                                        "D217: Skipping D78 EXIT for %s — position already closed",
                                        _ea.ticker,
                                    )
                                    continue
                                logger.info(
                                    "═══ D78 SMART EXIT: %s (urgency=%.2f, %s) ═══",
                                    _ea.ticker, _ea.signal.composite_exit_urgency,
                                    _ea.signal.reasoning,
                                )
                                # D96: Track smart exit in metrics
                                try:
                                    from src.monitoring.metrics import get_metrics as _gm_se
                                    _gm_se().smart_exits.inc()
                                except Exception:
                                    pass
                                # Market sell via close_position + fallback.
                                # Bug Z fix (2026-04-24): use the
                                # status-checked helper that gates the
                                # cleanup chain. Today's LIDR catastrophe
                                # was: close_position returned 403,
                                # the prior except-pass swallowed the
                                # error, and the cleanup chain ran
                                # unconditionally — cancelled stops,
                                # cancelled tranches, marked tracker
                                # closed at fake-positive P&L. Position
                                # was naked at broker overnight.
                                from src.execution.bridge import attempt_close_with_status_check
                                _d78_exit_price = 0.0
                                _d245_close_result = await attempt_close_with_status_check(
                                    client=client,
                                    ticker=_ea.ticker,
                                    qty=next(
                                        (_p.remaining_qty for _p in positions if _p.ticker == _ea.ticker),
                                        0,
                                    ),
                                    max_retries=settings.ops.smart_exit_max_retries
                                        if hasattr(settings.ops, "smart_exit_max_retries") else 3,
                                    retry_backoff_s=1.0,
                                    # Bug AI (2026-04-27): SMART_EXIT is a force-close path
                                    # (BAR-1 / EOD MOO / smart-exit signal). Opt in to the
                                    # cancel-blocking-stops-then-close coordination so we
                                    # don't deadlock against operator-submitted protective
                                    # stops like today's LIDR scenario. Stops are auto-
                                    # re-armed via D249 if the close ultimately fails.
                                    cancel_blocking_stops_first=True,
                                )
                                _d245_close_succeeded = _d245_close_result["succeeded"]
                                if _d245_close_result["fill_price"]:
                                    _d78_exit_price = float(_d245_close_result["fill_price"])

                                # Bug Z gate: if broker close FAILED, do
                                # NOT proceed to cleanup chain. Position
                                # remains in tracker, stops remain active,
                                # operator must intervene manually.
                                if not _d245_close_succeeded:
                                    logger.error(
                                        "D247 SMART_EXIT_ESCALATE %s: skipping "
                                        "cleanup chain (cancel-stops, cancel-"
                                        "tranches, close_with_attribution). "
                                        "Position remains tracked + protected. "
                                        "Manual intervention required before "
                                        "next session.",
                                        _ea.ticker,
                                    )
                                    # Mark for operator visibility; recon
                                    # daemon expects this divergence and
                                    # won't double-flag
                                    for _ep in positions:
                                        if _ep.ticker == _ea.ticker:
                                            try:
                                                _ep.close_attempt_failed = True
                                            except Exception:
                                                pass
                                            break
                                    continue  # next exit_action; do not run cleanup

                                # Get exit price from snapshot if broker didn't return one
                                if _d78_exit_price <= 0:
                                    _sx = _d78_snapshots.get(_ea.ticker, {})
                                    _d78_exit_price = float(
                                        _sx.get("latestTrade", {}).get("p", 0)
                                        or _sx.get("last_price", 0) or 0
                                    )

                                # D121 BUG-P1: Capture pos BEFORE close removes it.
                                # Previously captured AFTER, so _exit_pos was always None
                                # and _exit_qty defaulted to 1 -> journal PnL was per-share.
                                _exit_pos = None
                                for _ep in positions:
                                    if _ep.ticker == _ea.ticker:
                                        _exit_pos = _ep
                                        break
                                _exit_qty = _exit_pos.remaining_qty if _exit_pos else 1

                                # D121 BUG-P5 / CQ-3: Cancel orphaned stop and tranche
                                # orders before closing. Without this, orphaned sells
                                # can create accidental short positions after close.
                                await stop_resubmitter.cancel_and_remove(_ea.ticker)
                                await tranche_monitor.cancel_all_for_ticker(_ea.ticker, client)
                                trailing_manager.remove_position(_ea.ticker)  # D163: clean up trail
                                early_profit_taker.remove(_ea.ticker)  # D164
                                tranche_taker.remove(_ea.ticker)  # D165
                                entry_delay_mgr.remove(_ea.ticker)  # D170
                                # doc 272 VLL: terminal if a deferred verdict was parked here
                                if _d170_pending_verdicts.pop(_ea.ticker, None) is not None:  # D170
                                    try:
                                        from src.ops.verdict_ledger import vll_emit
                                        vll_emit("EXPIRED_OBSERVATION", _ea.ticker,
                                                 reason="deferred verdict superseded by position close",
                                                 path="MAIN")
                                    except Exception:
                                        pass

                                # Internal close + attribution
                                enriched = await bridge.close_with_attribution(
                                    ticker=_ea.ticker,
                                    exit_price=_d78_exit_price or 0,
                                )

                                if enriched:
                                    session_trades += 1
                                    _smart_total_pnl = enriched.pnl * _exit_qty
                                    # D198: Record smart exit for regime tracking
                                    session_regime.record_trade_result(
                                        pnl=_smart_total_pnl, is_win=(_smart_total_pnl > 0)
                                    )
                                    _exec_recorder.record_close(
                                        pos.ticker, enriched.exit_price,
                                        _exit_qty, _smart_total_pnl, "SMART_EXIT",
                                    )
                                    logger.debug("D198 %s", session_regime.get_status_report())
                                    # D94b: Prevent re-entry after smart exit
                                    _stopped_out_tickers.add(_ea.ticker)
                                    # D95: Persist cooldown for crash recovery
                                    try:
                                        state_mgr.add_stopped_out_ticker(_ea.ticker)
                                        state_mgr.save()
                                    except Exception as e:
                                        logger.warning("D218: stopped_ticker_tracking failed: %s", e)
                                    # D96: Comprehensive exit audit log
                                    logger.info(
                                        "D96 SMART EXIT: %s exit=$%.2f pnl=$%.2f (%.1f%%) "
                                        "urgency=%.2f entry=$%.2f qty=%d T%d/3 reason=%s",
                                        _ea.ticker, enriched.exit_price, _smart_total_pnl,
                                        ((enriched.exit_price / _exit_pos.entry_price - 1) * 100)
                                        if _exit_pos and _exit_pos.entry_price > 0 else 0,
                                        _ea.signal.composite_exit_urgency,
                                        _exit_pos.entry_price if _exit_pos else 0,
                                        _exit_qty,
                                        _exit_pos.tranches_filled if _exit_pos else 0,
                                        _ea.signal.reasoning,
                                    )
                                    # Journal with exit reason + signal scores
                                    try:
                                        for j_tid, j_ent in trade_journal._entries.items():
                                            if j_ent.ticker == _ea.ticker and j_ent.action == "BUY":
                                                trade_journal.record_close(
                                                    j_tid,
                                                    exit_price=enriched.exit_price,
                                                    realized_pnl=_smart_total_pnl,
                                                    exit_time=datetime.now(timezone.utc),
                                                    exit_reason="SMART_EXIT",
                                                    exit_signal_scores=_ea.signal.to_scores_dict(),
                                                )
                                                break
                                    except Exception:
                                        pass
                                    try:
                                        state_mgr.remove_position(_ea.ticker)
                                        state_mgr.update_daily_pnl(position_manager._daily_realized_pnl)
                                        state_mgr.save()
                                    except Exception as e:
                                        logger.warning("D218: position_removal failed: %s", e)
                    except Exception as _d78_e:
                        logger.warning("D78 exit intelligence error: %s", _d78_e)

                # ── D87: Midday Dead Zone (12:00-14:00 ET) ─────────────
                # Research shows 12-2 PM has the highest false breakout rate
                # of any session. Institutions accumulate/distribute quietly,
                # creating endless break-and-fail patterns. Skip scanning
                # and evaluation during this window to save LLM cost and
                # prevent bad entries. Position monitoring (exits, stops)
                # continues as normal.
                _midday_dead_zone = (hour_et == 12) or (hour_et == 13)
                if _midday_dead_zone and phase3_cycle_count % 30 == 0:
                    logger.info(
                        "[D87] Midday dead zone (%02d:%02d ET) - scanning paused, "
                        "position monitoring continues",
                        hour_et, now_et.minute,
                    )

                # ── VWAP Breakout Scanner (ADR-018, D2) ──
                if bridge.position_manager.can_enter_new_position() and not _midday_dead_zone:
                    try:
                        quotes = await _fetch_scan_quotes(client, settings)
                        if quotes:
                            # Build VWAP snapshots from available data.
                            # Try real VWAP from WebSocket accumulator first; fall back
                            # to prev_close (known inaccurate — will overstate breakouts
                            # on gap-up stocks since prev_close < intraday VWAP).
                            vwap_snapshots: dict = {}
                            vwap_fallback_count = 0
                            for ticker, q in quotes.items():
                                price = q.get("current_price", 0)
                                vol = q.get("premarket_volume", 0)
                                if price <= 0:
                                    continue

                                # Prefer real VWAP from orchestrator's WebSocket client
                                real_vwap = orchestrator._get_vwap(ticker, 0.0)
                                if real_vwap is not None and real_vwap > 0:
                                    vwap_val = real_vwap
                                else:
                                    # D148 FIX: Fallback 1 — REST snapshot VWAP from Alpaca
                                    # dailyBar.vw. More accurate than prev_close on gap-up stocks
                                    # since it reflects actual intraday volume-weighted price.
                                    rest_vwap = q.get("vwap")
                                    if rest_vwap and float(rest_vwap) > 0:
                                        vwap_val = float(rest_vwap)
                                    else:
                                        # Fallback 2: prev_close is NOT VWAP — it will be lower
                                        # than true intraday VWAP on gap-up stocks, creating
                                        # false breakout signals. Log this degradation.
                                        prev = q.get("previous_close", 0)
                                        if prev <= 0:
                                            continue
                                        vwap_val = prev
                                        vwap_fallback_count += 1

                                vwap_snapshots[ticker] = {
                                    "price": price,
                                    "vwap": vwap_val,
                                    "volume": vol,
                                    "avg_volume": max(1, q.get("avg_volume_at_time", vol)),
                                }

                            if vwap_fallback_count > 0:
                                logger.warning(
                                    "[Phase 3] %d/%d tickers using prev_close as VWAP proxy "
                                    "(WebSocket VWAP unavailable — breakout signals may be unreliable)",
                                    vwap_fallback_count, len(vwap_snapshots),
                                )

                            if not hasattr(cmd_paper, "_vwap_scanner"):
                                cmd_paper._vwap_scanner = IntradayVWAPScanner()  # type: ignore[attr-defined]

                            vwap_signals = cmd_paper._vwap_scanner.scan(vwap_snapshots)  # type: ignore[attr-defined]

                            if vwap_signals:
                                logger.info(
                                    "[Phase 3] %d VWAP breakout signals detected",
                                    len(vwap_signals),
                                )
                                # Convert VWAP signals to CandidateStocks for evaluation
                                from src.core.models import CandidateStock

                                vwap_candidates = []
                                # Bug T fix (2026-04-23): scan_timestamp is now
                                # a required CandidateStock field (Pydantic
                                # validation). Without it every Phase-3 VWAP
                                # rescan silently failed (35× today). Set to
                                # current UTC at construction time — the
                                # rescan IS the scan event.
                                _vwap_scan_ts = datetime.now(timezone.utc)
                                for sig in vwap_signals[:3]:  # Cap at 3
                                    vwap_candidates.append(CandidateStock(
                                        ticker=sig.ticker,
                                        current_price=sig.current_price,
                                        previous_close=sig.vwap,
                                        gap_pct=sig.breakout_pct,
                                        gap_classification="SIGNIFICANT",
                                        rvol=sig.rvol_at_breakout,
                                        premarket_volume=sig.total_volume,
                                        scan_phase="INTRADAY",
                                        scan_timestamp=_vwap_scan_ts,
                                    ))

                                # D39: Fetch news + technical data for VWAP candidates
                                # (previously evaluated with NO data — agents confabulated)
                                vwap_news: dict = {}
                                for cand in vwap_candidates:
                                    try:
                                        items = await news_client.get_news_for_ticker(
                                            cand.ticker, lookback_hours=4, max_items=5,
                                        )
                                        vwap_news[cand.ticker] = items
                                    except Exception:
                                        vwap_news[cand.ticker] = []

                                from src.data.technical_indicators import compute_indicators as _ci, format_price_data as _fpd
                                vwap_market_data: dict = {}
                                for cand in vwap_candidates:
                                    try:
                                        bars = await client.get_bars(
                                            cand.ticker, timeframe="1Min", limit=200,
                                        )
                                        if bars:
                                            vwap_market_data[cand.ticker] = {
                                                "price_data": _fpd(bars),
                                                "indicators": _ci(bars),
                                            }
                                    except Exception:
                                        pass

                                verdicts = await orchestrator.evaluate_candidates(
                                    vwap_candidates,
                                    news_by_ticker=vwap_news,
                                    market_data_by_ticker=vwap_market_data,
                                )
                                # D150: Guard against None return
                                if not verdicts:
                                    verdicts = []

                                # D211: Feed VIX to session regime
                                if orchestrator._vix_level is not None:
                                    session_regime.update_vix(orchestrator._vix_level)

                                # ── Journal: Record VWAP evaluations ──
                                for vi, verdict in enumerate(verdicts):
                                    try:
                                        # D83: Match by ticker, not index
                                        vwap_cand_map = {c.ticker: c for c in vwap_candidates}
                                        cand = vwap_cand_map.get(verdict.ticker)
                                        if cand:
                                            from src.utils.trade_logger import generate_trade_id as _gti
                                            j_entry = trade_journal.create_entry(
                                                _gti(verdict.ticker), cand, phase="VWAP_BREAKOUT",
                                            )
                                            trade_journal.record_input_data(
                                                j_entry,
                                                news_items=vwap_news.get(verdict.ticker, []),
                                                market_data=vwap_market_data.get(verdict.ticker, {}),
                                            )
                                            # D83: Per-candidate signals
                                            trade_journal.record_agent_signals(
                                                j_entry,
                                                orchestrator._signals_by_ticker.get(verdict.ticker, orchestrator._last_agent_signals),
                                                variant_map=orchestrator._variant_map_by_ticker.get(verdict.ticker, orchestrator._last_variant_map),
                                                data_report=orchestrator._data_report_by_ticker.get(verdict.ticker, orchestrator._last_data_report),
                                            )
                                            # D83: Per-candidate scoring
                                            _sc = orchestrator._scoring_by_ticker.get(verdict.ticker, {})
                                            j_entry.mfcs = _sc.get("mfcs", orchestrator._last_scored_mfcs)
                                            j_entry.component_scores = _sc.get("components", orchestrator._last_scored_components)
                                            j_entry.risk_score = _sc.get("risk_score", orchestrator._last_scored_risk)
                                            j_entry.qualifies_for_debate = _sc.get("qualifies_for_debate", orchestrator._last_scored_qualifies_debate)
                                            trade_journal.record_verdict(j_entry, verdict)
                                            trade_journal.flush(j_entry)
                                    except Exception:
                                        pass

                                # D211: Build candidate map for safety gates
                                _vwap_cand_map = {c.ticker: c for c in vwap_candidates}

                                for verdict in verdicts:
                                    if verdict.action in ("BUY", "STRONG_BUY"):
                                        # Sweep fix: VWAP path was missing all safety guards
                                        # that Phase 2 (line 1295-1325) and Rescan have.
                                        if position_manager.has_position(verdict.ticker):
                                            logger.info(
                                                "VWAP: Skipping %s — already holding position",
                                                verdict.ticker,
                                            )
                                            # doc 272 VLL: already-held terminal (VWAP path)
                                            try:
                                                from src.ops.verdict_ledger import vll_emit
                                                vll_emit("BLOCKED_ALREADY_HELD", verdict.ticker,
                                                         reason="already holding position", path="VWAP")
                                            except Exception:
                                                pass
                                            continue
                                        if verdict.ticker in _stopped_out_tickers:
                                            logger.warning(
                                                "VWAP: Skipping %s — stopped out earlier this session",
                                                verdict.ticker,
                                            )
                                            # doc 272 VLL: stop-out cooldown terminal (VWAP path)
                                            try:
                                                from src.ops.verdict_ledger import vll_emit
                                                vll_emit("BLOCKED_STOP_COOLDOWN", verdict.ticker,
                                                         reason="stopped out earlier this session", path="VWAP")
                                            except Exception:
                                                pass
                                            continue
                                        risk_check = portfolio_risk.check_entry(
                                            ticker=verdict.ticker,
                                            stop_loss_pct=2.0,
                                            positions=bridge.position_manager.open_positions,
                                        )
                                        if not risk_check.allowed:
                                            logger.warning(
                                                "VWAP RISK BLOCKED: %s — %s",
                                                verdict.ticker, risk_check.reason,
                                            )
                                            # doc 272 VLL: portfolio-risk terminal (VWAP path)
                                            try:
                                                from src.ops.verdict_ledger import vll_emit
                                                vll_emit("BLOCKED_PORTFOLIO_RISK", verdict.ticker,
                                                         reason=str(risk_check.reason)[:120], path="VWAP")
                                            except Exception:
                                                pass
                                            continue

                                        # ── D211: Safety gates (replicated from Phase 2) ──

                                        # Gate A: D198 Session Regime
                                        _vw_regime_ok, _vw_regime_reason = session_regime.should_allow_new_entry()
                                        if not _vw_regime_ok:
                                            logger.warning(
                                                "VWAP D198 REGIME BLOCKED: %s — %s",
                                                verdict.ticker, _vw_regime_reason,
                                            )
                                            # doc 272 VLL: session-regime halt terminal (VWAP path)
                                            try:
                                                from src.ops.verdict_ledger import vll_emit
                                                vll_emit("BLOCKED_REGIME", verdict.ticker,
                                                         reason=str(_vw_regime_reason)[:120], path="VWAP")
                                            except Exception:
                                                pass
                                            continue
                                        _vw_regime_mult = session_regime.get_size_multiplier()
                                        if _vw_regime_mult < 1.0 and verdict.position_size_pct:
                                            verdict = verdict.model_copy(
                                                update={"position_size_pct": verdict.position_size_pct * _vw_regime_mult}
                                            )

                                        # Gate B: D204 News Confidence
                                        _vw_news_ok = False
                                        try:
                                            _vw_signals = orchestrator._signals_by_ticker.get(verdict.ticker, [])
                                            for _vw_sig in _vw_signals:
                                                _vw_aid = getattr(_vw_sig, "agent_id", "") or ""
                                                if "news" in _vw_aid.lower():
                                                    _vw_sig_val = getattr(_vw_sig, "signal", "")
                                                    _vw_conf = getattr(_vw_sig, "confidence", 0.0) or 0.0
                                                    if _vw_sig_val in ("BULL", "STRONG_BULL") and _vw_conf >= 0.30:
                                                        _vw_news_ok = True
                                                    break
                                        except Exception:
                                            _vw_news_ok = True  # fail-open
                                        if not _vw_news_ok and (_vw_d272 := _d272_absence_fail_open(
                                                orchestrator._signals_by_ticker.get(verdict.ticker, []))):
                                            # doc 272 LEVER-1 (env MOMENTUM_EMPTY_NOT_BEAR, default
                                            # OFF — branch unreachable when unset): news ABSENCE !=
                                            # bearish news. Fail-open at reduced size (doc-178
                                            # downgrade mechanism) instead of blocking.
                                            _vw_d272_mult = getattr(settings.universe, "catalyst_gate_downgrade_qty_mult", 0.5)
                                            verdict = verdict.model_copy(update={
                                                "qty_multiplier": min(verdict.qty_multiplier, _vw_d272_mult),
                                            })
                                            logger.warning(
                                                "D272 EMPTY!=BEAR fail-open at %.2fx size: %s VWAP D204 "
                                                "news ABSENCE (%s) — admitted, not blocked",
                                                verdict.qty_multiplier, verdict.ticker, _vw_d272,
                                            )
                                            try:
                                                from src.ops.verdict_ledger import vll_emit
                                                vll_emit("DOWNGRADED_ABSENCE", verdict.ticker,
                                                         reason=_vw_d272, gate="D204",
                                                         qty_mult=round(float(verdict.qty_multiplier), 2),
                                                         path="VWAP")
                                            except Exception:
                                                pass
                                            # fall through — admit at reduced size
                                        elif not _vw_news_ok:
                                            logger.info(
                                                "VWAP D204 NEWS GATE: %s — no BULL news signal with confidence >= 0.30",
                                                verdict.ticker,
                                            )
                                            # doc 268 VLL: silent-verdict killer #2 (VWAP path)
                                            try:
                                                from src.ops.verdict_ledger import vll_emit
                                                vll_emit("BLOCKED_NEWS_GATE", verdict.ticker,
                                                         reason="no BULL news >= 0.30", path="VWAP")
                                            except Exception:
                                                pass
                                            continue

                                        # Gate C: D160 Faller Detection
                                        try:
                                            if settings.faller.enabled:
                                                _vw_cand = _vwap_cand_map.get(verdict.ticker)
                                                _vw_scored = orchestrator._scored_by_ticker.get(verdict.ticker)
                                                _vw_indicators = vwap_market_data.get(
                                                    verdict.ticker, {}
                                                ).get("indicators", {})
                                                if _vw_cand and _vw_scored:
                                                    _vw_faller = _faller_detector.score(
                                                        _vw_cand, _vw_scored, _vw_indicators,
                                                    )
                                                    if _vw_faller.reject:
                                                        logger.warning(
                                                            "VWAP D160 FALLER BLOCKED: %s score=%.2f > threshold — "
                                                            "gap-and-fade risk too high",
                                                            verdict.ticker, _vw_faller.faller_score,
                                                        )
                                                        # doc 272 VLL: faller-reject terminal (VWAP path)
                                                        try:
                                                            from src.ops.verdict_ledger import vll_emit
                                                            vll_emit("BLOCKED_FALLER", verdict.ticker,
                                                                     reason=f"faller_score={_vw_faller.faller_score:.3f}",
                                                                     path="VWAP")
                                                        except Exception:
                                                            pass
                                                        continue
                                        except Exception as _vw_fe:
                                            logger.debug("VWAP D160 faller error (fail-open): %s", _vw_fe)

                                        _scored = orchestrator._scored_by_ticker.get(verdict.ticker)
                                        order = await bridge.execute_verdict(verdict, scored=_scored)
                                        if order is not None:
                                            session_trades += 1
                                            # D121 BUG-P9: Subscribe new VWAP ticker to
                                            # market data WebSocket for real-time VWAP.
                                            try:
                                                await _market_ws_client.add_symbols([order.ticker])
                                            except Exception:
                                                pass
                                            logger.info(
                                                "VWAP BREAKOUT ORDER: %s qty=%d @ $%.2f",
                                                order.ticker, order.qty, order.submitted_price,
                                            )
                                            # Doc 82 Phase 2 ship (2026-04-29): unified post-fill
                                            # bookkeeping handler. Closes Bug AT-bypass for VWAP_BREAKOUT.
                                            _vw_cand_rec = _vwap_cand_map.get(verdict.ticker)
                                            await post_fill_bookkeeping(
                                                order=order, verdict=verdict, scored=_scored,
                                                candidate=_vw_cand_rec,
                                                path="VWAP_BREAKOUT",
                                                ctx=_post_fill_ctx,
                                                log_prefix="VWAP",
                                            )
                    except Exception as e:
                        logger.warning("[Phase 3] VWAP scan error: %s", e)

                # D42: Periodic re-scanning for new gap candidates every 15 cycles (~15 min)
                # D87: Skip re-scanning during midday dead zone (12-2 PM)
                phase3_cycle_count += 1
                # D96: Track Phase 3 cycle count in metrics
                try:
                    from src.monitoring.metrics import get_metrics as _gm3
                    _gm3().phase3_cycles.inc()
                except Exception:
                    pass

                # ── D107/D102: Heartbeat pulse + 5-minute log ──
                try:
                    _d107_watchdog.pulse(phase="PHASE_3")
                except Exception:
                    pass
                if phase3_cycle_count % 5 == 0:
                    _hb_positions = bridge.position_manager.open_positions
                    _hb_pnl = position_manager._daily_realized_pnl if hasattr(position_manager, '_daily_realized_pnl') else 0.0
                    logger.info(
                        "♥ [Phase 3] Heartbeat cycle=%d | %d positions open | "
                        "Realized P&L: $%+.2f | %02d:%02d ET",
                        phase3_cycle_count,
                        len(_hb_positions),
                        _hb_pnl,
                        hour_et, now_et.minute,
                    )
                    # D215: Per-path P&L dashboard every 50 cycles (~50 min)
                    if phase3_cycle_count % 50 == 0:
                        try:
                            _exec_recorder.print_dashboard()
                        except Exception:
                            pass
                    for _hb_pos in _hb_positions:
                        try:
                            # doc-285: None (UNKNOWN) falls back to entry price
                            _hb_current = float(next(
                                (ap.get("current_price", _hb_pos.entry_price)
                                 for ap in (_d98_broker_positions or [])
                                 if ap.get("symbol") == _hb_pos.ticker),
                                _hb_pos.entry_price,
                            ))
                            _hb_pnl_pct = ((_hb_current - _hb_pos.entry_price) / _hb_pos.entry_price * 100) if _hb_pos.entry_price > 0 else 0
                            logger.info(
                                "  %s: entry=$%.2f current=$%.2f (%+.1f%%) "
                                "stop=$%.2f qty=%d",
                                _hb_pos.ticker, _hb_pos.entry_price,
                                _hb_current, _hb_pnl_pct,
                                _hb_pos.stop_loss, _hb_pos.remaining_qty,
                            )
                        except Exception:
                            logger.info(
                                "  %s: entry=$%.2f stop=$%.2f qty=%d",
                                _hb_pos.ticker, _hb_pos.entry_price,
                                _hb_pos.stop_loss, _hb_pos.remaining_qty,
                            )
                # D108 WS2: Metric snapshots to disk every 5th cycle (~5 min)
                if phase3_cycle_count % 5 == 0:
                    try:
                        from src.monitoring.metrics import get_metrics as _gm_snap
                        from pathlib import Path as _SnapPath
                        _gm_snap().save_snapshot(_SnapPath("data/metrics"))
                    except Exception:
                        pass

                # D97: Stale entry cutoff — block new entries after configured time
                _cutoff_h = settings.execution.stale_entry_cutoff_hour
                _cutoff_m = settings.execution.stale_entry_cutoff_minute
                _past_cutoff = hour_et > _cutoff_h or (hour_et == _cutoff_h and now_et.minute >= _cutoff_m)
                if _past_cutoff and phase3_cycle_count % 15 == 0 and phase3_cycle_count % 60 == 0:
                    # Log once every ~60 cycles (~60 min) to avoid spam
                    logger.info(
                        "D97: Stale entry guard — skipping Phase 3 rescan "
                        "(current=%02d:%02d ET, cutoff=%02d:%02d ET)",
                        hour_et, now_et.minute, _cutoff_h, _cutoff_m,
                    )

                if phase3_cycle_count % 15 == 0 and bridge.position_manager.can_enter_new_position() and not _midday_dead_zone and not _past_cutoff:
                    try:
                        rescan_quotes = await _fetch_scan_quotes(client, settings)
                        if rescan_quotes:
                            rescan_watchlist = scan_loop.run_single_scan(rescan_quotes)
                            # Filter out tickers we already have positions in
                            # D150: Filter both open positions AND stopped-out tickers (cooldown)
                            existing_tickers = {p.ticker for p in bridge.position_manager.open_positions}
                            new_candidates = [
                                c for c in rescan_watchlist
                                if c.ticker not in existing_tickers
                                and c.ticker not in _stopped_out_tickers
                            ]
                            if new_candidates:
                                logger.info(
                                    "[Phase 3] Re-scan found %d new candidates (filtering %d existing)",
                                    len(new_candidates), len(existing_tickers),
                                )
                                # D75: Evaluate top 5 new candidates with FULL data enrichment
                                # (same quality as Phase 2 — 24h news, cache enrichment)
                                rescan_top = new_candidates[:5]
                                rescan_news: dict = {}
                                for cand in rescan_top:
                                    try:
                                        # D75: Use 24h lookback (same as Phase 2), not 4h
                                        items = await news_client.get_news_for_ticker(
                                            cand.ticker, lookback_hours=24, max_items=10,
                                        )
                                        rescan_news[cand.ticker] = items
                                    except Exception:
                                        rescan_news[cand.ticker] = []

                                # D75: Enrich with premarket cache (if ticker is in universe)
                                if premarket_cache:
                                    try:
                                        premarket_research.enrich_news_dict(
                                            premarket_cache, rescan_news,
                                            [c.ticker for c in rescan_top],
                                        )
                                    except Exception:
                                        pass

                                from src.data.technical_indicators import compute_indicators as _ci2, format_price_data as _fpd2
                                rescan_mkt: dict = {}
                                for cand in rescan_top:
                                    try:
                                        bars = await client.get_bars(
                                            cand.ticker, timeframe="1Min", limit=200,
                                        )
                                        if bars:
                                            rescan_mkt[cand.ticker] = {
                                                "price_data": _fpd2(bars),
                                                "indicators": _ci2(bars),
                                            }
                                    except Exception:
                                        pass

                                rescan_verdicts = await orchestrator.evaluate_candidates(
                                    rescan_top,
                                    news_by_ticker=rescan_news,
                                    market_data_by_ticker=rescan_mkt,
                                )
                                # D150: Guard against None return
                                if not rescan_verdicts:
                                    rescan_verdicts = []

                                # D211: Feed VIX to session regime
                                if orchestrator._vix_level is not None:
                                    session_regime.update_vix(orchestrator._vix_level)

                                # ── Journal: Record rescan evaluations ──
                                for ri, rv in enumerate(rescan_verdicts):
                                    try:
                                        # D83: Match by ticker, not index
                                        rescan_cand_map = {c.ticker: c for c in rescan_top}
                                        rc = rescan_cand_map.get(rv.ticker)
                                        if rc:
                                            from src.utils.trade_logger import generate_trade_id as _gti3
                                            j_entry = trade_journal.create_entry(
                                                _gti3(rv.ticker), rc, phase="RESCAN",
                                            )
                                            trade_journal.record_input_data(
                                                j_entry,
                                                news_items=rescan_news.get(rv.ticker, []),
                                                market_data=rescan_mkt.get(rv.ticker, {}),
                                            )
                                            # D83: Per-candidate signals
                                            trade_journal.record_agent_signals(
                                                j_entry,
                                                orchestrator._signals_by_ticker.get(rv.ticker, orchestrator._last_agent_signals),
                                                variant_map=orchestrator._variant_map_by_ticker.get(rv.ticker, orchestrator._last_variant_map),
                                                data_report=orchestrator._data_report_by_ticker.get(rv.ticker, orchestrator._last_data_report),
                                            )
                                            # D83: Per-candidate scoring
                                            _rsc = orchestrator._scoring_by_ticker.get(rv.ticker, {})
                                            j_entry.mfcs = _rsc.get("mfcs", orchestrator._last_scored_mfcs)
                                            j_entry.component_scores = _rsc.get("components", orchestrator._last_scored_components)
                                            j_entry.risk_score = _rsc.get("risk_score", orchestrator._last_scored_risk)
                                            j_entry.qualifies_for_debate = _rsc.get("qualifies_for_debate", orchestrator._last_scored_qualifies_debate)
                                            trade_journal.record_verdict(j_entry, rv)
                                            trade_journal.flush(j_entry)
                                    except Exception:
                                        pass

                                # D211: Build candidate map for safety gates
                                _rescan_cand_map = {c.ticker: c for c in rescan_top}

                                for verdict in rescan_verdicts:
                                    if verdict.action in ("BUY", "STRONG_BUY"):
                                        # D94b: Skip if we already hold this ticker (prevents duplicates)
                                        # March 4 post-mortem: ASNS bought 4x, CANF bought 3x because
                                        # Phase 3 rescan had no has_position guard (Phase 2 had one at line 1019).
                                        if position_manager.has_position(verdict.ticker):
                                            logger.info(
                                                "D94b: Rescan skipping %s — already holding position",
                                                verdict.ticker,
                                            )
                                            # doc 272 VLL: already-held terminal (RESCAN path)
                                            try:
                                                from src.ops.verdict_ledger import vll_emit
                                                vll_emit("BLOCKED_ALREADY_HELD", verdict.ticker,
                                                         reason="already holding position", path="RESCAN")
                                            except Exception:
                                                pass
                                            continue
                                        # D94b: Skip if stopped out this session
                                        if verdict.ticker in _stopped_out_tickers:
                                            logger.warning(
                                                "D94b: Rescan skipping %s — stopped out earlier this session",
                                                verdict.ticker,
                                            )
                                            # doc 272 VLL: stop-out cooldown terminal (RESCAN path)
                                            try:
                                                from src.ops.verdict_ledger import vll_emit
                                                vll_emit("BLOCKED_STOP_COOLDOWN", verdict.ticker,
                                                         reason="stopped out earlier this session", path="RESCAN")
                                            except Exception:
                                                pass
                                            continue
                                        # D121 BUG-5: Portfolio risk check (was missing)
                                        risk_check = portfolio_risk.check_entry(
                                            ticker=verdict.ticker,
                                            stop_loss_pct=2.0,
                                            positions=bridge.position_manager.open_positions,
                                        )
                                        if not risk_check.allowed:
                                            logger.warning(
                                                "RESCAN RISK BLOCKED: %s — %s",
                                                verdict.ticker, risk_check.reason,
                                            )
                                            # doc 272 VLL: portfolio-risk terminal (RESCAN path)
                                            try:
                                                from src.ops.verdict_ledger import vll_emit
                                                vll_emit("BLOCKED_PORTFOLIO_RISK", verdict.ticker,
                                                         reason=str(risk_check.reason)[:120], path="RESCAN")
                                            except Exception:
                                                pass
                                            continue

                                        # ── D211: Safety gates (replicated from Phase 2) ──

                                        # Gate A: D198 Session Regime
                                        _rs_regime_ok, _rs_regime_reason = session_regime.should_allow_new_entry()
                                        if not _rs_regime_ok:
                                            logger.warning(
                                                "RESCAN D198 REGIME BLOCKED: %s — %s",
                                                verdict.ticker, _rs_regime_reason,
                                            )
                                            # doc 272 VLL: session-regime halt terminal (RESCAN path)
                                            try:
                                                from src.ops.verdict_ledger import vll_emit
                                                vll_emit("BLOCKED_REGIME", verdict.ticker,
                                                         reason=str(_rs_regime_reason)[:120], path="RESCAN")
                                            except Exception:
                                                pass
                                            continue
                                        _rs_regime_mult = session_regime.get_size_multiplier()
                                        if _rs_regime_mult < 1.0 and verdict.position_size_pct:
                                            verdict = verdict.model_copy(
                                                update={"position_size_pct": verdict.position_size_pct * _rs_regime_mult}
                                            )

                                        # Gate B: D204 News Confidence
                                        _rs_news_ok = False
                                        try:
                                            _rs_signals = orchestrator._signals_by_ticker.get(verdict.ticker, [])
                                            for _rs_sig in _rs_signals:
                                                _rs_aid = getattr(_rs_sig, "agent_id", "") or ""
                                                if "news" in _rs_aid.lower():
                                                    _rs_sig_val = getattr(_rs_sig, "signal", "")
                                                    _rs_conf = getattr(_rs_sig, "confidence", 0.0) or 0.0
                                                    if _rs_sig_val in ("BULL", "STRONG_BULL") and _rs_conf >= 0.30:
                                                        _rs_news_ok = True
                                                    break
                                        except Exception:
                                            _rs_news_ok = True  # fail-open
                                        if not _rs_news_ok and (_rs_d272 := _d272_absence_fail_open(
                                                orchestrator._signals_by_ticker.get(verdict.ticker, []))):
                                            # doc 272 LEVER-1 (env MOMENTUM_EMPTY_NOT_BEAR, default
                                            # OFF — branch unreachable when unset): news ABSENCE !=
                                            # bearish news. Fail-open at reduced size (doc-178
                                            # downgrade mechanism) instead of blocking.
                                            _rs_d272_mult = getattr(settings.universe, "catalyst_gate_downgrade_qty_mult", 0.5)
                                            verdict = verdict.model_copy(update={
                                                "qty_multiplier": min(verdict.qty_multiplier, _rs_d272_mult),
                                            })
                                            logger.warning(
                                                "D272 EMPTY!=BEAR fail-open at %.2fx size: %s RESCAN D204 "
                                                "news ABSENCE (%s) — admitted, not blocked",
                                                verdict.qty_multiplier, verdict.ticker, _rs_d272,
                                            )
                                            try:
                                                from src.ops.verdict_ledger import vll_emit
                                                vll_emit("DOWNGRADED_ABSENCE", verdict.ticker,
                                                         reason=_rs_d272, gate="D204",
                                                         qty_mult=round(float(verdict.qty_multiplier), 2),
                                                         path="RESCAN")
                                            except Exception:
                                                pass
                                            # fall through — admit at reduced size
                                        elif not _rs_news_ok:
                                            logger.info(
                                                "RESCAN D204 NEWS GATE: %s — no BULL news signal with confidence >= 0.30",
                                                verdict.ticker,
                                            )
                                            # doc 268 VLL: silent-verdict killer #2 (RESCAN path)
                                            try:
                                                from src.ops.verdict_ledger import vll_emit
                                                vll_emit("BLOCKED_NEWS_GATE", verdict.ticker,
                                                         reason="no BULL news >= 0.30", path="RESCAN")
                                            except Exception:
                                                pass
                                            continue

                                        # Gate C: D160 Faller Detection
                                        try:
                                            if settings.faller.enabled:
                                                _rs_cand = _rescan_cand_map.get(verdict.ticker)
                                                _rs_scored = orchestrator._scored_by_ticker.get(verdict.ticker)
                                                _rs_indicators = rescan_mkt.get(
                                                    verdict.ticker, {}
                                                ).get("indicators", {})
                                                if _rs_cand and _rs_scored:
                                                    _rs_faller = _faller_detector.score(
                                                        _rs_cand, _rs_scored, _rs_indicators,
                                                    )
                                                    if _rs_faller.reject:
                                                        logger.warning(
                                                            "RESCAN D160 FALLER BLOCKED: %s score=%.2f > threshold — "
                                                            "gap-and-fade risk too high",
                                                            verdict.ticker, _rs_faller.faller_score,
                                                        )
                                                        # doc 272 VLL: faller-reject terminal (RESCAN path)
                                                        try:
                                                            from src.ops.verdict_ledger import vll_emit
                                                            vll_emit("BLOCKED_FALLER", verdict.ticker,
                                                                     reason=f"faller_score={_rs_faller.faller_score:.3f}",
                                                                     path="RESCAN")
                                                        except Exception:
                                                            pass
                                                        continue
                                        except Exception as _rs_fe:
                                            logger.debug("RESCAN D160 faller error (fail-open): %s", _rs_fe)

                                        _scored = orchestrator._scored_by_ticker.get(verdict.ticker)
                                        order = await bridge.execute_verdict(verdict, scored=_scored)
                                        if order is not None:
                                            session_trades += 1
                                            # D121 BUG-P9: Subscribe new rescan ticker to
                                            # market data WebSocket for real-time VWAP.
                                            try:
                                                await _market_ws_client.add_symbols([order.ticker])
                                            except Exception:
                                                pass
                                            logger.info(
                                                "RESCAN ORDER: %s qty=%d @ $%.2f",
                                                order.ticker, order.qty, order.submitted_price,
                                            )
                                            # Doc 82 Phase 2 ship (2026-04-29): unified post-fill
                                            # bookkeeping handler. Closes Bug AT-bypass for RESCAN.
                                            # Path-specific items (subscribe to ws, RESCAN ORDER log)
                                            # stay inline above; D215+journal+exit_ladder+stop+state_mgr+
                                            # BAR-1+D278 are in the helper.
                                            _rs_cand_rec = _rescan_cand_map.get(verdict.ticker)
                                            await post_fill_bookkeeping(
                                                order=order, verdict=verdict, scored=_scored,
                                                candidate=_rs_cand_rec,
                                                path="RESCAN",
                                                ctx=_post_fill_ctx,
                                                log_prefix="RESCAN",
                                            )
                    except Exception as e:
                        logger.warning("[Phase 3] Re-scan error: %s", e)

            elif market_is_open is False:
                # ── D79: Market Holiday/Weekend Idle ──
                # Market is closed (verified via Alpaca clock API).
                # Don't run Phase 2/3/4. Just sleep and check again.
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=60)
                except asyncio.TimeoutError:
                    pass
                continue

            elif market_is_open is None and 9 <= hour_et < 16:
                # ── D79-CLOCKFAIL: clock UNKNOWN during regular trading hours ──
                # market_is_open is None ONLY because the Alpaca clock check raised
                # (transient REST outage / the client-side 'alpaca_rest' breaker
                # tripping ~9:00 ET). A failed check is NOT "after-hours": before this
                # guard, None fell through to the `else` Phase-4 branch and the bot ran
                # SESSION CLOSE + shut down mid-morning on a ~25s blip (prod outages
                # 2026-06-23/26/29; Phase 0:26s, Phase 2:0s, trades:0). Treat unknown
                # as "wait and retry" — idle like the verified-closed path, NEVER close.
                # (After 16:00 ET line ~2082 forces market_is_open=None, hour_et>=16, so
                # the legitimate after-hours close still reaches the `else` below.)
                logger.warning(
                    "[D79-CLOCKFAIL] Market clock UNKNOWN during trading hours "
                    "(hour_et=%s, market_is_open=None) — idling + retrying, NOT entering "
                    "Phase 4. Transient-API guard (was the self-EOD shutdown bug).",
                    hour_et,
                )
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=60)
                except asyncio.TimeoutError:
                    pass
                continue

            else:
                # ── Phase 4: After-Hours ──
                # D71: Only run Phase 4 close/report/reset once per session.
                # Without this guard, Phase 4 would re-run every 60s cycle,
                # generating duplicate session reports and resetting P&L each time.
                if phase4_completed:
                    try:
                        await asyncio.wait_for(shutdown.wait(), timeout=60)
                    except asyncio.TimeoutError:
                        pass
                    continue

                logger.info("═══════════ [Phase 4] ENTERING SESSION CLOSE ═══════════")

                # D90: Check market clock — if market is closed (late restart),
                # don't submit unfillable market orders. Orders with
                # extended_hours=False submitted after 4PM ET will never fill
                # and create "STILL OPEN" positions that become ghost shorts.
                _p4_market_open = True
                try:
                    _p4_clock = await client.get_market_clock()
                    _p4_market_open = _p4_clock.get("is_open", False)
                except Exception as e:
                    logger.warning("[Phase 4] D90: Market clock check failed: %s — skipping close", e)
                    _p4_market_open = False

                # D90: If market is closed on a late restart, skip position close.
                # Market orders with extended_hours=False submitted after 4PM
                # will never fill and leave positions "STILL OPEN", which become
                # ghost shorts when the system tries to re-close the next day.
                _skip_close = False
                if not _p4_market_open and hour_et >= 16:
                    logger.warning(
                        "[Phase 4] D90: Market is CLOSED at %s ET. "
                        "Skipping position close to avoid unfillable orders. "
                        "Positions will be closed at next market open.",
                        now_et.strftime("%H:%M"),
                    )
                    _skip_close = True
                    positions = bridge.position_manager.open_positions
                    if positions:
                        logger.info(
                            "[Phase 4] D90: %d positions will carry overnight: %s",
                            len(positions),
                            [p.ticker for p in positions],
                        )

                if not _skip_close:
                    positions = bridge.position_manager.open_positions

                    # D80: Cancel ALL open orders before closing positions.
                    # Prevents runaway buy/sell loop from orphaned tranche/stop orders.
                    try:
                        await client.cancel_all_orders()
                        logger.info("[Phase 4] D80: Cancelled all open orders before close")
                    except Exception as e:
                        logger.warning("[Phase 4] D80: Cancel all orders failed: %s", e)
                    tranche_monitor.reset()
                    tranche_taker.reset()  # D165
                    _or_tracker.reset()  # doc 188: clear opening-range bars for the next session
                    await asyncio.sleep(1)  # Let cancellations propagate

                if not _skip_close and positions:
                    logger.info("[Phase 4] D74: Closing %d positions via BROKER SELL...", len(positions))

                    # D86: Fetch actual broker positions to avoid ghost shorts
                    try:
                        _broker_pos = await client.get_positions()
                        # D161: Include both long and short positions. Previously filtered
                        # to side=="long" only, causing short positions to appear as
                        # "not found at broker" and skip proper close/P&L recording.
                        _broker_long = {
                            p["symbol"] for p in _broker_pos
                            if p.get("side") in ("long", "short")
                        }
                    except Exception as e:
                        logger.warning("D86: Failed to fetch broker positions: %s — closing all tracked", e)
                        _broker_long = {pos.ticker for pos in positions}

                    for pos in list(positions):  # copy — list may mutate
                        # D86: Skip if position no longer exists at broker
                        if pos.ticker not in _broker_long:
                            logger.warning(
                                "D86: Skipping Phase 4 close for %s — not found at broker",
                                pos.ticker,
                            )
                            enriched = await bridge.close_with_attribution(
                                ticker=pos.ticker,
                                exit_price=pos.entry_price,
                            )
                            if enriched:
                                session_trades += 1
                            continue

                        # D74: Submit REAL sell order to Alpaca to close position
                        exit_price = pos.entry_price  # fallback; updated if close succeeds
                        broker_closed = False
                        try:
                            # D217: timeout on broker close to prevent Phase 4 hanging (D218: config)
                            close_resp = await asyncio.wait_for(
                                client.close_position(pos.ticker),
                                timeout=settings.ops.eod_close_timeout_seconds,
                            )
                            logger.info(
                                "D74 BROKER CLOSE: %s -> %s",
                                pos.ticker, close_resp,
                            )
                            # Try to get actual fill price from response
                            filled_price = close_resp.get("filled_avg_price")
                            if filled_price:
                                exit_price = float(filled_price)
                            broker_closed = True
                        except asyncio.TimeoutError:
                            logger.error(
                                "D217: Phase 4 close timed out for %s after 30s — position may remain open",
                                pos.ticker,
                            )
                        except Exception as e:
                            # D86: Do NOT fallback to market sell — creates ghost shorts
                            logger.error(
                                "D74 BROKER CLOSE FAILED: %s — %s. "
                                "D86: Skipping market sell fallback to prevent ghost shorts.",
                                pos.ticker, e,
                            )

                        # doc 229 PHANTOM GUARD: only book P&L + untrack when the broker close
                        # actually SUCCEEDED. Mirrors the D76 guard (~main.py:4682). On a 403
                        # (a protective stop re-reserved the qty) or a timeout, broker_closed=False
                        # and the OLD code ("Record internal close regardless") booked fake ~$0 P&L
                        # at entry_price AND removed tracking -> untracked overnight carry +
                        # BOCPD/Kelly corpus poisoning (the exact APPS-5/28 / LFS-5/27 phantom class
                        # the 216-220 arc exists to kill, surviving on the Phase-4 EOD fallback).
                        if not broker_closed:
                            logger.error(
                                "Phase 4 close FAILED for %s — NOT booking P&L; leaving position "
                                "tracked for D242 failsafe / next-session D91 (phantom guard)",
                                pos.ticker,
                            )
                            continue
                        # Record internal close (broker close CONFIRMED above)
                        enriched = await bridge.close_with_attribution(
                            ticker=pos.ticker,
                            exit_price=exit_price,
                        )
                        if enriched:
                            # Sweep fix: total PnL, not per-share
                            _stopout_total_pnl = enriched.pnl * pos.remaining_qty
                            status_str = "CLOSED"  # broker close confirmed (phantom guard above)
                            logger.info(
                                "%s %s: PnL=$%.2f (%d shares, exit=$%.2f)",
                                status_str, pos.ticker, _stopout_total_pnl,
                                pos.remaining_qty, exit_price,
                            )
                            # D64: Remove closed position from state
                            try:
                                state_mgr.remove_position(pos.ticker)
                                state_mgr.update_daily_pnl(
                                    position_manager._daily_realized_pnl,
                                )
                                state_mgr.save()
                            except Exception as e:
                                logger.warning("D218: position_removal failed: %s", e)
                            # ── Journal: Record position close ──
                            try:
                                for j_tid, j_ent in trade_journal._entries.items():
                                    if j_ent.ticker == pos.ticker and j_ent.action == "BUY":
                                        trade_journal.record_close(
                                            j_tid,
                                            exit_price=enriched.exit_price,
                                            realized_pnl=_stopout_total_pnl,
                                            exit_time=datetime.now(timezone.utc),
                                        )
                                        break
                            except Exception:
                                pass
                            # ── Shapley -> Elo feedback (ADR-016 D1) ──
                            try:
                                from src.analysis.post_trade import PostTradeAnalyzer
                                from src.analysis.shapley import ShapleyAttributor
                                from src.agents.prompt_arena import PromptArena

                                arena_path = Path("data/arena_ratings.json")
                                if arena_path.exists():
                                    arena = PromptArena.load(str(arena_path))
                                else:
                                    from src.agents.prompt_arena import seed_default_variants
                                    arena = seed_default_variants()

                                analyzer = PostTradeAnalyzer(arena=arena)
                                attributor = ShapleyAttributor()
                                matchups = analyzer.analyze_with_shapley(enriched, attributor)

                                if matchups:
                                    logger.info(
                                        "%s: %d Shapley->Elo matchups processed",
                                        pos.ticker, len(matchups),
                                    )
                                    arena.save(str(arena_path))
                                    # D65: Wire Elo ratings into metrics for session report
                                    try:
                                        from src.monitoring.metrics import get_metrics as _gm_elo
                                        _m_elo = _gm_elo()
                                        for _aid in ["news_agent", "technical_agent", "fundamental_agent",
                                                      "institutional_agent", "deep_search_agent", "risk_agent"]:
                                            _best = arena.get_best_variant(_aid)
                                            if _best:
                                                _m_elo.set_agent_elo(_aid, _best.elo_rating)
                                    except Exception:
                                        pass
                            except Exception as e:
                                logger.warning("Shapley->Elo feedback failed for %s: %s", pos.ticker, e)

                    # D74: Verify all positions are closed at broker
                    try:
                        remaining = await client.get_positions()
                        if remaining:
                            remaining_syms = [p.get("symbol") for p in remaining]
                            logger.error(
                                "D74 WARNING: %d positions STILL OPEN at broker after Phase 4: %s",
                                len(remaining), remaining_syms,
                            )
                        else:
                            logger.info("D74: Broker confirms ALL positions closed ✓")
                    except Exception as e:
                        logger.warning("D74: Could not verify broker positions: %s", e)
                elif not _skip_close:
                    logger.info("[Phase 4] No positions. Session trades: %d", session_trades)

                # ── D103: Safety sync — ensure metrics gauge matches PM ground truth ──
                # The D99 fallback path now updates the gauge (D103 fix above),
                # but as a belt-and-suspenders measure, force-sync right before
                # generating the report. This catches any future code paths that
                # might modify PM P&L without updating the gauge.
                try:
                    from src.monitoring.metrics import get_metrics as _gm_d103_sync
                    _pm_pnl = bridge.position_manager._daily_realized_pnl
                    _gm_d103_sync().daily_pnl.set(_pm_pnl)
                except Exception:
                    pass  # Non-fatal — don't block session report

                # ── Generate End-of-Day Session Report (ADR-022 + D60 fallback) ──
                try:
                    from src.analysis.session_report import SessionReportGenerator
                    report_gen = SessionReportGenerator(mode=settings.mode, session_start=_session_start_utc)
                    # D103: Pass PM ground-truth P&L for cross-check in session report
                    _pm_pnl_for_report = bridge.position_manager._daily_realized_pnl
                    report = report_gen.generate(realized_pnl_pm=_pm_pnl_for_report)

                    # D60: If metrics look empty but journal has entries, use journal fallback
                    if report.evaluations_total == 0 and trade_journal.entry_count > 0:
                        logger.warning(
                            "D60: Metrics empty but journal has %d entries — using journal fallback",
                            trade_journal.entry_count,
                        )
                        report = report_gen.generate_from_journal()

                    report_gen.save(report)
                    logger.info("\n%s", report.summary_text())

                    # D108 WS2: Post-session notification webhook
                    if settings.session_notification_url:
                        try:
                            report_gen.send_notification(report, settings.session_notification_url)
                            logger.info("D108: Session notification sent -> %s", settings.session_notification_url)
                        except Exception as _ne:
                            logger.warning("D108: Session notification failed (non-fatal): %s", _ne)
                except Exception as e:
                    logger.warning("Session report generation failed: %s", e)

                # ── Trade Journal End-of-Day Summary ──
                # Bug #13 fix (2026-04-22): Closed/P&L counts now derive
                # from the broker fill ledger (single source of truth),
                # not from per-exit-path record_close() calls. Today's
                # AGPU BAR-1 exit silently dropped because that path
                # never invoked record_close — broker truth would have
                # caught it.
                try:
                    from src.analysis.trade_journal import (
                        summarize_from_broker_fills,
                        run_pnl_reconciliation,
                    )

                    # Journal-side numbers (the OLD computation, kept for
                    # backward compat + the recon comparison).
                    j_summary = trade_journal.session_summary()
                    j_per_ticker: dict[str, float] = {}
                    for _e in trade_journal._entries.values():
                        if _e.action == "BUY" and _e.exit_price is not None and _e.realized_pnl is not None:
                            j_per_ticker[_e.ticker] = (
                                j_per_ticker.get(_e.ticker, 0.0) + float(_e.realized_pnl)
                            )

                    # Broker-truth numbers (today's filled order activities).
                    _bug13_closed = j_summary["closed_count"]
                    _bug13_total_pnl = j_summary["total_pnl"]
                    _bug13_source = "journal"
                    try:
                        # D222 fix (2026-04-29): AlpacaDataClient does not have
                        # get_account_activities; mirror the hasattr-guard pattern
                        # used in trade_journal.run_pnl_reconciliation (Bug N fix
                        # from 2026-04-23) so this AttributeError stops being
                        # silently swallowed by the broad except below.
                        if hasattr(client, "get_account_activities"):
                            _broker_fills = await client.get_account_activities()
                        else:
                            # get_orders schema is also handled by
                            # summarize_from_broker_fills (Bug N flexibility).
                            _broker_fills = await client.get_orders(
                                status="all", limit=500
                            )
                        _broker_summary = summarize_from_broker_fills(_broker_fills or [])
                        _bug13_closed = _broker_summary["closed_count"]
                        _bug13_total_pnl = _broker_summary["total_pnl"]
                        _bug13_source = "broker"
                        # Run D222 reconciliation against journal numbers
                        emit_div = await run_pnl_reconciliation(
                            client=client,
                            journal_pnl=float(j_summary["total_pnl"] or 0.0),
                            per_ticker_journal=j_per_ticker,
                        )
                        if emit_div:
                            logger.warning(
                                "D222: journal vs broker P&L diverged — see DIVERGENCE log above"
                            )
                    except Exception as _be:
                        logger.warning(
                            "D222: broker fill fetch failed (%s) — falling back to journal-only counts. "
                            "Closed/P&L numbers below may be undercounting BAR-1/D146 exits.",
                            _be,
                        )

                    logger.info(
                        "═══ TRADE JOURNAL SUMMARY ═══\n"
                        "  Evaluations: %d | BUYs: %d | Closed: %d (source=%s)\n"
                        "  Total P&L: $%.2f (source=%s) | Avg MFCS: %.4f\n"
                        "  Journal file: %s",
                        j_summary["total_evaluations"],
                        j_summary["buy_count"],
                        _bug13_closed,
                        _bug13_source,
                        _bug13_total_pnl,
                        _bug13_source,
                        j_summary["avg_mfcs"],
                        j_summary["journal_file"],
                    )
                except Exception as e:
                    logger.warning("Trade journal summary failed: %s", e)

                # ── EOD section return-value capture (consolidated report) ──
                # Each EOD helper returns a structured dict; capture them
                # all so the post-section consolidator can persist a
                # single data/reports/eod_<date>.json for Monday review.
                _eod_recon: dict | None = None
                _phase0_health: dict | None = None
                _bocpd_refit: dict | None = None
                _cohort_bf: dict | None = None
                _bayes_fit: dict | None = None
                _failsafes: dict | None = None

                # ── D230/D231 EOD Reconciliation Invariants ──
                # Track A item 3 (2026-04-23): cheap stopgap for the
                # full Tier-1 reconciliation daemon (Track B Week 2).
                # Three invariants run once at session close:
                #   QTY    (D231 if mismatch)  — Bug D-class regression
                #   STOP   (D231 if mismatch with confirmed OID)
                #                              — Bug R-class regression
                #   EQUITY (D230 if drift > $1) — Bug N-class soft signal
                # Non-fatal: any broker outage degrades to logged warning.
                try:
                    from src.monitoring.eod_recon import run_eod_invariants
                    _eod_recon = await run_eod_invariants(
                        client=client,
                        position_manager=position_manager,
                    )
                    if _eod_recon["qty_drift_count"] > 0 or _eod_recon["stop_drift_count"] > 0:
                        logger.error(
                            "EOD RECON: %d Tier-1 violations require investigation "
                            "before next session. Broker reachable: %s.",
                            _eod_recon["qty_drift_count"] + _eod_recon["stop_drift_count"],
                            _eod_recon["broker_reachable"],
                        )
                except Exception as _re:
                    logger.warning(
                        "EOD RECON: invariants check failed (non-fatal): %s", _re,
                    )

                # ── Phase 0 EOD flush + health surface (D261) ──
                # Flush all in-memory instrumentation buffers to Parquet
                # atomically, then surface the per-schema health snapshot
                # (incl. D261 validation-failure counters) in the EOD log.
                # Non-fatal — Phase 0 failure never blocks session close.
                try:
                    if _phase0_writer is not None:
                        _flush_counts = await _phase0_writer.flush_all(reason="eod")
                        logger.info(
                            "Phase 0 EOD flush: %s",
                            ", ".join(f"{k}={v}" for k, v in _flush_counts.items()),
                        )
                        from src.monitoring.eod_recon import run_eod_phase0_health
                        _phase0_health = run_eod_phase0_health(_phase0_writer)
                        # _phase0_health logs the WARNING + count internally
                except Exception as _pe:
                    logger.warning("Phase 0 EOD flush failed (non-fatal): %s", _pe)

                # ── BOCPD EOD refit check (D262) ──
                # Compare the persisted BOCPD prior against a fresh re-fit
                # on the current corpus. Logs WARNING with literal D262
                # marker if drift OR sample-size growth recommends a refit;
                # operator runs `python scripts/pretrain_bocpd_prior.py`.
                # Non-fatal — never blocks session close.
                try:
                    from src.monitoring.eod_recon import run_eod_bocpd_refit_check
                    _bocpd_refit = run_eod_bocpd_refit_check()
                    # _bocpd_refit logs the recommendation internally
                except Exception as _bre:
                    logger.warning("BOCPD EOD refit check failed (non-fatal): %s", _bre)

                # ── Cohort EOD backfill ──
                # Populate the cohort registry's deferred fields
                # (cohort_eod_px, cohort_60min_px, cohort_signed_return_60min)
                # by fetching prices from the broker for each peer.
                # Non-fatal; degrades per-row.
                try:
                    from src.analysis.cohort_eod_backfill import run_eod_cohort_backfill
                    _cohort_bf = await run_eod_cohort_backfill(client=client)
                    if _cohort_bf.get("error"):
                        logger.info(
                            "Cohort EOD backfill: %s", _cohort_bf.get("error"),
                        )
                except Exception as _cbe:
                    logger.warning("Cohort EOD backfill failed (non-fatal): %s", _cbe)

                # ── Bayesian (eta_perm, gamma) EOD fit + 4-trigger gates ──
                # Reads the day's child_fill_ticks + bar_context Parquets,
                # builds (q/v, slippage) tuples, runs fit_eta_gamma + the
                # four gates D256-D259. Persists daily report at
                # data/reports/bayesian_eta_gamma_<date>.json.
                # Skips gracefully when below min_observations (=30).
                try:
                    from src.analysis.bayesian_eod_runner import run_eod_bayesian_fit
                    _bayes_fit = run_eod_bayesian_fit()
                    if _bayes_fit.get("error"):
                        logger.info(
                            "Bayesian EOD fit: %s (n=%d)",
                            _bayes_fit.get("error"),
                            _bayes_fit.get("n_observations", 0),
                        )
                    else:
                        logger.info(
                            "Bayesian EOD fit: posterior eta=%.4f gamma=%.4f n=%d → %s",
                            _bayes_fit["posterior"]["eta_mean"],
                            _bayes_fit["posterior"]["gamma_mean"],
                            _bayes_fit["n_observations"],
                            _bayes_fit.get("report_path"),
                        )
                except Exception as _bfe:
                    logger.warning("Bayesian EOD fit failed (non-fatal): %s", _bfe)

                # ── EOD failsafes (D241 + D242 + D238) ──
                # Items 15+28 from 2026-04-24 next-actions list +
                # D242 force-close motivated by today's LIDR Bug Z catastrophe.
                # Three failsafes that fire after the EOD recon as the
                # last-resort layer:
                #   D242 force-close ghost broker positions
                #   D241 cancel orphan working orders
                #   D238 broker-truth-first per-ticker P&L recon
                # Each is independent + non-fatal.
                try:
                    from src.monitoring.eod_failsafes import run_all_eod_failsafes
                    # Build the expected_order_ids allowlist from internal tracker
                    _expected_oids: set[str] = set()
                    for _ipos in position_manager.open_positions:
                        for _attr in ("order_id", "stop_order_id"):
                            _v = getattr(_ipos, _attr, "") or ""
                            if _v:
                                _expected_oids.add(_v)
                        for _toid in getattr(_ipos, "tranche_order_ids", []) or []:
                            if _toid:
                                _expected_oids.add(_toid)
                    _failsafes = await run_all_eod_failsafes(
                        client=client,
                        position_manager=position_manager,
                        journal_pnl=float(j_summary.get("total_pnl", 0.0) or 0.0),
                        per_ticker_journal=j_per_ticker,
                        expected_order_ids=_expected_oids,
                    )
                except Exception as _fse:
                    logger.warning(
                        "EOD failsafes outer error (non-fatal): %s", _fse,
                    )

                # ── EOD report consolidation (single JSON for Monday review) ──
                # Aggregate every EOD section's return value into one
                # data/reports/eod_<session_date>.json file. Surfaces a
                # top-level "fires" list of D-codes for fast triage.
                # Non-fatal — write failure never blocks session close.
                try:
                    from src.monitoring.eod_report import (
                        build_eod_report, write_eod_report,
                    )
                    _eod_full_report = build_eod_report(
                        eod_recon=_eod_recon,
                        phase0_health=_phase0_health,
                        bocpd_refit=_bocpd_refit,
                        cohort_backfill=_cohort_bf,
                        bayesian_fit=_bayes_fit,
                        eod_failsafes=_failsafes,
                        extra_metadata={
                            "broker_equity_eod": float(
                                _eod_recon.get("broker_equity", 0.0)
                                if _eod_recon else 0.0
                            ),
                            "open_positions_eod": len(position_manager.open_positions),
                        },
                    )
                    _eod_report_path = write_eod_report(_eod_full_report)
                    if _eod_report_path:
                        logger.info("EOD CONSOLIDATED REPORT: %s", _eod_report_path)
                except Exception as _ree:
                    logger.warning("EOD report consolidation failed (non-fatal): %s", _ree)

                # ── D196: Save minute-bar recordings for arena replay ──
                if _d196_session_tickers:
                    try:
                        _d196_session_date_utc = datetime.now(timezone.utc)
                        _d196_start = _d196_session_date_utc.replace(
                            hour=13, minute=30, second=0, microsecond=0  # 9:30 ET in UTC
                        )
                        _d196_end = _d196_session_date_utc.replace(
                            hour=20, minute=0, second=0, microsecond=0  # 4:00 PM ET in UTC
                        )
                        _d196_saved = 0
                        for _d196_ticker in _d196_session_tickers:
                            try:
                                _d196_series = await bar_recorder.record_bars(
                                    _d196_ticker,
                                    start=_d196_start,
                                    end=_d196_end,
                                    alpaca_client=client,
                                )
                                if _d196_series:
                                    _d196_saved += 1
                            except Exception:
                                pass
                        logger.info(
                            "D196: Saved bar recordings for %d/%d tickers -> data/bar_recordings",
                            _d196_saved, len(_d196_session_tickers),
                        )
                    except Exception as e:
                        logger.error("D196: Bar recording save failed: %s", e)

                # ── D210: Fetch full minute bars + save comprehensive session data ──
                try:
                    _d210_tickers = list(_d196_session_tickers) or session_collector.tickers
                    if _d210_tickers:
                        await session_collector.fetch_and_store_bars(client, tickers=_d210_tickers)
                    session_collector.save_session()
                except Exception as _d210_e:
                    logger.error("D210: Session save failed: %s", _d210_e)

                # doc 209 BUGFIX: capture the EOD P&L/equity BEFORE reset_daily() zeroes
                # _daily_realized_pnl. The prior code read these AFTER reset (line ~7534) so
                # the Discord EOD headline always showed $0.00 / "Profitable day" even on
                # losing days (5/28 -$269, 5/29 -$1,500), while the recon sub-section had the
                # truth. Capture now; prefer broker-truth-recon below.
                _d209_pre_reset_realized = float(getattr(position_manager, "_daily_realized_pnl", 0) or 0)
                _d209_pre_reset_starting_eq = float(getattr(position_manager, "_starting_equity", 0) or 0)
                # Reset daily state
                bridge.position_manager.reset_daily()
                watchlist.clear()
                phase0_completed = False  # Allow Phase 0 to re-run next day
                phase4_completed = True  # D71: Prevent Phase 4 from re-running until next day
                # D64: Reset session state for new day
                state_mgr.reset()
                state_mgr.save()
                logger.info("[Phase 4] Complete — initiating clean shutdown")
                # 2026-05-12 doc 161: replaced minimal alert_session_end with
                # rich alert_session_end_rich. Backwards-compat: the old call
                # is still here as a fallback in case the rich call fails;
                # both go to the same OPS webhook so duplication is acceptable
                # rather than risk losing the EOD message entirely.
                # doc 209 BUGFIX: source the EOD headline from BROKER TRUTH first (the recon
                # that already shipped correctly in the sub-section), then the pre-reset
                # captured value — NOT the post-reset _daily_realized_pnl (always 0.0) or the
                # non-existent _unrealized_pnl / _session_start_equity attrs (always 0.0).
                _d209_btr = {}
                try:
                    if "_eod_full_report" in dir():
                        _d209_btr = (_eod_full_report.get("sections", {})
                                     .get("eod_failsafes", {})
                                     .get("broker_truth_recon", {}) or {})
                except Exception:
                    _d209_btr = {}
                _d209_broker_pnl = _d209_btr.get("broker_total_pnl")
                # broker P&L is the source of truth; else the value captured before reset
                _d218_eod_pnl = float(_d209_broker_pnl) if _d209_broker_pnl is not None \
                    else _d209_pre_reset_realized
                _d218_unrealized = 0.0  # realized-basis EOD; broker P&L already realized
                _d218_starting_equity = _d209_pre_reset_starting_eq
                _d218_ending_equity = _d218_starting_equity + _d218_eod_pnl
                _d218_halt_blocks = getattr(position_manager, '_halt_blocked_count', 0)
                # ── D304 (2026-05-19): build the rich EOD payload ──────
                # Pre-D304 the call below passed only 9 of 15 supported
                # kwargs, dropping d_codes_fired, top_winners/losers,
                # eod_recon, bocpd_refit, veto_summary. The Discord
                # message was the headline numbers only — the rich
                # context I added in doc 161 never actually shipped to
                # the operator. The user explicitly called this out:
                # "Discord is also providing incomplete messages".
                _d304_d_codes = list(_eod_full_report.get("fires", [])) \
                    if "_eod_full_report" in dir() else []
                # Build top winners/losers from the trade journal's
                # closed BUY entries (each has ticker, entry_price,
                # exit_price, realized_pnl populated by record_close).
                _d304_winners: list[dict] = []
                _d304_losers: list[dict] = []
                try:
                    _d304_closed = [
                        e for e in trade_journal._entries.values()
                        if e.action == "BUY" and e.realized_pnl is not None
                    ]
                    _d304_closed.sort(key=lambda e: float(e.realized_pnl or 0.0),
                                        reverse=True)
                    for e in _d304_closed[:3]:
                        if (e.realized_pnl or 0) <= 0:
                            break
                        _ep = float(getattr(e, "entry_price", 0) or 0)
                        _xp = float(getattr(e, "exit_price", 0) or 0)
                        _pct = ((_xp / _ep - 1) * 100) if _ep > 0 and _xp > 0 else 0.0
                        _d304_winners.append({
                            "ticker": e.ticker,
                            "pnl": float(e.realized_pnl),
                            "pnl_pct": _pct,
                            "entry_price": _ep,
                            "exit_price": _xp,
                        })
                    for e in reversed(_d304_closed[-3:]):
                        if (e.realized_pnl or 0) >= 0:
                            break
                        _ep = float(getattr(e, "entry_price", 0) or 0)
                        _xp = float(getattr(e, "exit_price", 0) or 0)
                        _pct = ((_xp / _ep - 1) * 100) if _ep > 0 and _xp > 0 else 0.0
                        _d304_losers.append({
                            "ticker": e.ticker,
                            "pnl": float(e.realized_pnl),
                            "pnl_pct": _pct,
                            "entry_price": _ep,
                            "exit_price": _xp,
                        })
                except Exception as _d304_we:
                    logger.warning("D304: winners/losers build failed: %s", _d304_we)
                # Pull eod_recon from the local dict if available
                _d304_eod_recon = None
                _d304_bocpd_payload = None
                try:
                    _bt = (_eod_full_report.get("sections", {})
                           .get("eod_failsafes", {})
                           .get("broker_truth_recon"))
                    if _bt:
                        _d304_eod_recon = _bt
                except Exception:
                    pass
                try:
                    if _bocpd_refit:
                        _diff = _bocpd_refit.get("diff", {}) or {}
                        _d304_bocpd_payload = {
                            "refit_recommended": bool(_bocpd_refit.get("fires_d262")),
                            "old_mu": _diff.get("old_mu_edge", 0),
                            "new_mu": _diff.get("new_mu_edge", 0),
                            "n_trades": _diff.get("new_n_trades", 0),
                        }
                except Exception:
                    pass
                # D312 (2026-05-24, doc 172): pull gate-rejection summary
                # from the PhantomJournal. Closes the Friday silent-BUY
                # observability gap AND the veto_summary TODO from doc 168.
                _d312_veto_summary: dict | None = None
                try:
                    _d312_veto_summary = _phantom.gate_summary() or None
                except Exception as _vs_e:
                    logger.warning("D312: gate_summary failed (non-fatal): %s", _vs_e)

                # D313.v2 (2026-05-24, doc 173): pull L2 hedge-integrity
                # stats and inject as D-code into the EOD report. Tells
                # operator: how many checks ran, how many violations
                # fired, how many emergency stops were submitted, how
                # many "stop exists but wrong params" were caught. If
                # L2 emergency_stops > 0 OR incorrect_stops > 0,
                # surface as critical D-code so the EOD report flags it.
                try:
                    if _hedge_watcher is not None:
                        _hw_stats = _hedge_watcher.stats()
                        if _hw_stats.get("violations", 0) > 0:
                            _d304_d_codes.append(
                                f"D313-{_hw_stats['violations']}violations"
                            )
                        if _hw_stats.get("emergency_stops", 0) > 0:
                            _d304_d_codes.append(
                                f"D313-{_hw_stats['emergency_stops']}emerg_stops"
                            )
                        if _hw_stats.get("incorrect_stops", 0) > 0:
                            _d304_d_codes.append(
                                f"D313v2-{_hw_stats['incorrect_stops']}wrong_params"
                            )
                        logger.info(
                            "D313 EOD STATS: checks=%d violations=%d "
                            "emergency_stops=%d incorrect_stops=%d "
                            "last_heartbeat=%s",
                            _hw_stats.get("checks", 0),
                            _hw_stats.get("violations", 0),
                            _hw_stats.get("emergency_stops", 0),
                            _hw_stats.get("incorrect_stops", 0),
                            _hw_stats.get("last_heartbeat_utc"),
                        )
                except Exception as _hwse:
                    logger.warning("D313 EOD stats injection failed: %s", _hwse)
                try:
                    from src.monitoring.alerts import alert_session_end_rich
                    from datetime import date as _d161_date
                    asyncio.ensure_future(alert_session_end_rich(
                        session_date=_d161_date.today().isoformat(),
                        trades=session_trades,
                        realized_pnl=float(_d218_eod_pnl),
                        unrealized_pnl=float(_d218_unrealized),
                        positions_at_close=len(position_manager.open_positions),
                        starting_equity=float(_d218_starting_equity),
                        ending_equity=float(_d218_ending_equity),
                        d_codes_fired=_d304_d_codes,        # D304
                        top_winners=_d304_winners,          # D304
                        top_losers=_d304_losers,            # D304
                        eod_recon=_d304_eod_recon,          # D304
                        bocpd_refit=_d304_bocpd_payload,    # D304
                        veto_summary=_d312_veto_summary,    # D312
                        halt_blocked_count=int(_d218_halt_blocks),
                        webhook_url=settings.ops.alert_webhook_url,
                    ))
                except Exception:
                    # Fallback to minimal version if rich one errors
                    try:
                        from src.monitoring.alerts import alert_session_end
                        asyncio.ensure_future(alert_session_end(
                            trades=session_trades,
                            pnl=_d218_eod_pnl,
                            positions_at_close=len(position_manager.open_positions),
                            webhook_url=settings.ops.alert_webhook_url,
                        ))
                    except Exception:
                        pass
                # D97: Clean process exit after Phase 4.
                # Without this, the while loop continues forever (60s sleeps)
                # and stale Python processes accumulate across Task Scheduler
                # invocations. shutdown.set() breaks the main loop, allowing
                # cleanup code (WebSocket close, dashboard cancel) to execute.
                shutdown.set()

        except Exception as e:
            # D217: Log full traceback, not just message. Without exc_info=True,
            # errors like "NoneType has no attribute 'ticker'" give zero context
            # about which line/function failed. This was a root cause of the
            # April 9 forensics gap — errors were invisible.
            logger.error("Phase error: %s", e, exc_info=True)

        # Wait 60 seconds between phase checks
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass

    # ── Shutdown: close all remaining positions ──
    # D121 BUG-M9: Use last known price, not entry_price (which records $0 PnL).
    # Try broker positions for current market value, fall back to entry as last resort.
    _broker_positions = {}
    try:
        _bp_list = await client.get_positions()
        _broker_positions = {p.get("symbol", ""): float(p.get("current_price", 0)) for p in _bp_list}
    except Exception as _bp_err:
        logger.warning("Shutdown: Could not fetch broker positions: %s", _bp_err)
    # doc 229 PHANTOM GUARD: the OLD code (D121) called close_with_attribution at MTM with NO
    # broker close — booking UNREALIZED MTM as REALIZED on the normal overnight-carry path
    # (Phase 4 _skip_close leaves positions tracked, then this shutdown loop booked them) ->
    # poisoned the BOCPD/Kelly corpus AND orphaned the still-open broker position. We now book +
    # untrack ONLY on a CONFIRMED broker close, and only ATTEMPT the close when the market is
    # OPEN. Cancelling protective stops (cancel_blocking_stops_first) while the market is CLOSED
    # would leave the carry NAKED overnight — strictly worse than the bug — so when closed we
    # leave the position fully intact (tracked + persisted, stops untouched) for next-session D91.
    _shutdown_market_open = False
    try:
        _shutdown_market_open = bool((await client.get_market_clock()).get("is_open", False))
    except Exception as _clk_err:
        logger.warning("Shutdown: market clock check failed: %s — treating as CLOSED", _clk_err)
    from src.execution.bridge import attempt_close_with_status_check as _shutdown_close
    for pos in list(bridge.position_manager.open_positions):
        if not _shutdown_market_open:
            logger.warning(
                "Shutdown: market CLOSED — leaving %s tracked + persisted intact (stops "
                "untouched) for next-session D91; NOT booking phantom MTM P&L",
                pos.ticker,
            )
            continue
        _sc_ok = False
        _sc_px = None
        try:
            _sc_res = await _shutdown_close(
                client=client,
                ticker=pos.ticker,
                qty=pos.remaining_qty,
                max_retries=2,
                cancel_blocking_stops_first=True,
            )
            _sc_ok = bool(_sc_res.get("succeeded"))
            _sc_px = _sc_res.get("fill_price")
        except Exception as _sc_err:
            logger.error(
                "Shutdown close raised for %s: %s — leaving tracked (phantom guard)",
                pos.ticker, _sc_err,
            )
        if not _sc_ok:
            logger.warning(
                "Shutdown: %s did NOT close at broker — leaving tracked + persisted for "
                "next-session D91 (no phantom book)", pos.ticker,
            )
            continue
        _exit_px = float(_sc_px) if _sc_px and float(_sc_px) > 0 else (
            _broker_positions.get(pos.ticker) or pos.entry_price)
        await bridge.close_with_attribution(
            ticker=pos.ticker,
            exit_price=_exit_px,
        )
        # D121 BUG-S8b: Persist state on shutdown so recovery doesn't
        # see ghost positions that were already closed at the broker.
        try:
            state_mgr.remove_position(pos.ticker)
            state_mgr.save()
        except Exception:
            logger.debug("Shutdown: state save failed for %s", pos.ticker)

    # ── Shutdown Live Dashboard (D67) ──
    # D217: All shutdown awaits have 5s timeout to prevent hanging on
    # stale WebSocket/task that won't respond to cancellation
    dashboard_task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(dashboard_task), timeout=5.0)
    except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
        pass

    # ── Shutdown WebSocket fill stream ──
    trade_stream.stop()
    if fill_stream_task is not None:
        fill_stream_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(fill_stream_task), timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
    logger.info("WebSocket fill stream stopped")

    # ── Shutdown market data WebSocket (BUG-005 cleanup) ──
    _market_ws_client.stop()
    if _market_ws_task is not None and not _market_ws_task.done():
        _market_ws_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(_market_ws_task), timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
    logger.info("Market data WebSocket stopped")

    # D107 WS4: Stop heartbeat watchdog
    try:
        _d107_watchdog.stop()
    except Exception:
        pass

    # D217: Stop health/control server
    if _d217_health_server is not None:
        try:
            _d217_health_server.stop()
            logger.info("D217: Health/control server stopped")
        except Exception:
            pass

    # D107 WS3: Record session reliability
    try:
        _d107_control_state.record_session(had_trades=session_trades > 0)
        logger.info(
            "D107: Session recorded (trades=%d, reliability=%.2f, attempted=%d, with_trades=%d)",
            session_trades,
            _d107_control_state.reliability_score,
            _d107_control_state._sessions_attempted,
            _d107_control_state._sessions_with_trades,
        )
    except Exception:
        pass

    # Tue 2026-04-21 Fix 2: cancel the heartbeat keeper task on shutdown.
    # Skipping the cancel would leave the task scheduled but with no
    # event loop owner; pytest cleanups occasionally surface "Task was
    # destroyed but it is pending" warnings if not explicitly cancelled.
    if _heartbeat_keeper_task is not None and not _heartbeat_keeper_task.done():
        _heartbeat_keeper_task.cancel()
        try:
            await _heartbeat_keeper_task
        except asyncio.CancelledError:
            pass
        except Exception as _kp_close_err:
            logger.warning("Heartbeat keeper close raised: %s", _kp_close_err)

    logger.info("Paper trading stopped. Session trades: %d", session_trades)


async def cmd_backtest(settings: Settings) -> None:
    """
    Run CPCV backtest with PBO+DSR combined acceptance gate.

    Uses HistoricalBacktestSimulator for full pipeline:
      1. Load or generate (signals, returns) data
      2. LLM-Aware CPCV with contamination detection
      3. Deflated Sharpe Ratio computation
      4. Combined gate: PBO < 0.10 AND DSR > 0.95

    Supports --synthetic flag for testing without historical data.
    Supports --n-obs, --accuracy, --seed for synthetic data params.

    Ref: INV-001 (CPCV mandatory), REF-007 (Lopez de Prado)
    Ref: ADR-011 (LLM-Aware Backtesting)
    Ref: ADR-015 (Production Readiness, D3)
    Ref: ADR-016 (Production Wiring, D3)
    """
    from src.core.backtest_simulator import HistoricalBacktestSimulator
    from src.data.historical_loader import HistoricalDataLoader
    import json as _json
    import numpy as np

    logger.info("═══ MOMENTUM-X BACKTESTER ═══")
    logger.info("Validation: LLM-Aware CPCV (Purged + Embargoed)")
    logger.info("Acceptance gate: PBO < 0.10 AND DSR > 0.95")

    # ── Data Loading: Scenario DB, Multi-ticker, Single-ticker, or Synthetic ──
    use_scenarios = Path("data/scenarios/backtest_data.json").exists()
    use_multi = hasattr(settings, 'backtest_tickers') and settings.backtest_tickers
    use_historical = hasattr(settings, 'backtest_ticker') and settings.backtest_ticker
    strategy_name = "momentum_x_synthetic"
    position_sizes = None  # D35: will be populated from scenario data if available

    # Backtest date range — will be overridden by scenario dates if available
    from datetime import date as date_cls
    bt_start = date_cls(2025, 1, 1)
    bt_end = date_cls(2025, 12, 31)

    if use_scenarios and not use_multi and not use_historical:
        # Scenario-based backtest (Phase 3: real agent signals on historical gaps)
        logger.info("Loading scenario-based backtest data...")
        try:
            scenario_data = _json.loads(Path("data/scenarios/backtest_data.json").read_text())
            signals = np.array(scenario_data["signals"])
            returns = np.array(scenario_data["returns"])
            strategy_name = "momentum_x_gap_scenarios"

            # D35: Load position sizes if available (backward compatible)
            position_sizes_raw = scenario_data.get("position_sizes")
            position_sizes = np.array(position_sizes_raw) if position_sizes_raw else None

            # Use actual scenario dates for contamination detection
            scenario_dates = scenario_data.get("dates", [])
            if scenario_dates:
                parsed_dates = [date_cls.fromisoformat(d) for d in scenario_dates]
                bt_start = min(parsed_dates)
                bt_end = max(parsed_dates)
                logger.info(
                    "Scenario data loaded: %d observations (%s to %s) from %s",
                    len(signals), bt_start, bt_end,
                    scenario_data.get("source", "unknown"),
                )
            else:
                logger.info(
                    "Scenario data loaded: %d observations from %s (no dates, using defaults)",
                    len(signals), scenario_data.get("source", "unknown"),
                )
        except Exception as e:
            logger.warning("Scenario data load failed (%s), falling back to synthetic", e)
            signals = returns = None  # Will be generated below

    elif use_multi:
        # Multi-ticker portfolio backtest
        tickers = settings.backtest_tickers
        logger.info("Loading multi-ticker historical data: %s...", tickers)
        try:
            from src.data.alpaca_client import AlpacaDataClient
            from src.data.multi_ticker_backtest import MultiTickerBacktest
            client = AlpacaDataClient(settings.alpaca)
            loader = HistoricalDataLoader(client=client)
            multi = MultiTickerBacktest(loader=loader)
            result = await multi.load_and_merge(
                tickers=tickers,
                days=settings.backtest_days,
            )
            signals, returns = result.merged_dataset.signals, result.merged_dataset.returns
            strategy_name = f"momentum_x_portfolio_{'_'.join(tickers[:3])}"
            logger.info(
                "Multi-ticker loaded: %d tickers, %d observations, %d BUY signals, %d failed",
                len(result.per_ticker), result.total_observations,
                result.total_buy_signals, len(result.failed_tickers),
            )
            if result.failed_tickers:
                logger.warning("Failed tickers: %s", result.failed_tickers)
        except Exception as e:
            logger.warning("Multi-ticker load failed (%s), falling back to synthetic", e)
            signals = returns = None

    elif use_historical:
        # Historical mode: fetch real OHLCV from Alpaca
        ticker = settings.backtest_ticker
        logger.info("Loading historical data for %s...", ticker)
        try:
            from src.data.alpaca_client import AlpacaDataClient
            client = AlpacaDataClient(settings.alpaca)
            loader = HistoricalDataLoader(client=client)
            dataset = await loader.load(ticker=ticker, days=settings.backtest_days)
            signals, returns = dataset.signals, dataset.returns
            strategy_name = f"momentum_x_{ticker}"
            logger.info(
                "Historical data loaded: %d observations, %d BUY signals, %s -> %s",
                dataset.n_observations, dataset.n_buy_signals,
                dataset.start_date, dataset.end_date,
            )
        except Exception as e:
            logger.warning("Historical data load failed (%s), falling back to synthetic", e)
            signals = returns = None
    else:
        signals = returns = None

    # Create simulator with backtest date range (may have been set from scenario dates)
    sim = HistoricalBacktestSimulator(
        model_id=settings.models.tier1_model,
        n_groups=6,
        n_test_groups=2,
        pbo_threshold=0.10,
        dsr_threshold=0.95,
        backtest_start=bt_start,
        backtest_end=bt_end,
    )

    # Fall back to synthetic data if no real data was loaded
    if signals is None or returns is None:
        logger.info("Generating synthetic backtest data...")
        signals, returns = sim.generate_synthetic_data(
            n=500,
            signal_accuracy=0.55,
            seed=42,
        )
        strategy_name = "momentum_x_synthetic"

    logger.info("Running backtest: %d observations, model=%s", len(signals), settings.models.tier1_model)
    report = sim.run(
        signals=signals,
        returns=returns,
        strategy_name=strategy_name,
        position_sizes=position_sizes,
    )

    # ── Report ──
    logger.info("═══ BACKTEST RESULTS ═══")
    logger.info("Strategy: %s", report.strategy_name)
    logger.info("Period: %s -> %s", report.backtest_start, report.backtest_end)
    logger.info("Observations: %d | Folds: %d | Contaminated: %d",
                report.n_observations, report.n_folds, report.n_contaminated_folds)
    logger.info("PBO: %.4f (%s)", report.pbo, "✓ PASS" if report.pbo_pass else "✗ FAIL")
    logger.info("DSR: %.4f (%s)", report.dsr, "✓ PASS" if report.dsr_pass else "✗ FAIL")
    logger.info("Clean OOS Sharpe: %.4f", report.clean_oos_sharpe)

    # D36: Portfolio simulation metrics
    if report.portfolio_final_equity > 0:
        logger.info("═══ PORTFOLIO SIMULATION ═══")
        logger.info("  Final equity:    $%s (%.1f%%)",
                    f"{report.portfolio_final_equity:,.2f}",
                    report.portfolio_total_return_pct)
        logger.info("  Max drawdown:    %.1f%%", report.portfolio_max_drawdown_pct)
        logger.info("  Profit factor:   %.2f", report.portfolio_profit_factor)
        logger.info("  Win rate:        %.1f%%", report.portfolio_win_rate_pct)

    if report.accepted:
        logger.info("═══ VERDICT: ACCEPTED ═══")
    else:
        logger.info("═══ VERDICT: REJECTED ═══")
    logger.info("Summary: %s", report.summary)

    # Save report to disk
    report_path = Path("data/backtest_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_json.dumps(report.to_dict(), indent=2, default=str))
    logger.info("Report saved to %s", report_path)


async def cmd_build_scenarios(settings: Settings, lookback_days: int = 540, min_gap_pct: float = 0.10, max_gap_pct: float = 5.0) -> None:
    """
    Build historical scenario database from Alpaca daily bars.

    Scans MOMENTUM_UNIVERSE for gap days exceeding threshold,
    records intraday outcomes, saves as ScenarioDatabase.

    Phase 3.1 of the validation plan.

    Ref: ADR-025 (Phase 3: Backtesting Validation)
    """
    from src.data.alpaca_client import AlpacaDataClient
    from src.data.scenario_builder import ScenarioBuilder, MOMENTUM_UNIVERSE

    logger.info("═══ MOMENTUM-X SCENARIO BUILDER ═══")
    logger.info("Lookback: %d days | Min gap: %.0f%% | Max gap: %.0f%% | Universe: %d tickers",
                lookback_days, min_gap_pct * 100, max_gap_pct * 100, len(set(MOMENTUM_UNIVERSE)))

    client = AlpacaDataClient(settings.alpaca)

    # Verify connectivity
    try:
        account = await client.get_account()
        logger.info("Account connected: equity=$%.2f", float(account.get("equity", 0)))
    except Exception as e:
        logger.error("Failed to connect to Alpaca: %s", e)
        return

    builder = ScenarioBuilder(client=client)
    db = await builder.build(
        tickers=list(set(MOMENTUM_UNIVERSE)),
        lookback_days=lookback_days,
        min_gap_pct=min_gap_pct,
        max_gap_pct=max_gap_pct,
        min_volume=500_000,
    )

    # Save to disk
    output_path = Path("data/scenarios/gap_scenarios.json")
    db.save(output_path)

    # Print summary
    summary = db.summary()
    logger.info("═══ SCENARIO DATABASE BUILT ═══")
    for key, val in summary.items():
        logger.info("  %s: %s", key, val)


async def cmd_record_scenarios(settings: Settings, max_scenarios: int | None = None) -> None:
    """
    Run historical scenarios through the agent pipeline in RECORD mode.

    Loads the scenario database, evaluates each scenario through the
    full orchestrator pipeline, and stores agent signals + verdicts
    for CPCV backtesting.

    Phase 3.2 of the validation plan.

    Ref: ADR-025 (Phase 3: Backtesting Validation)
    Ref: ADR-011 (LLM-Aware Backtesting)
    """
    from src.core.orchestrator import Orchestrator
    from src.data.alpaca_client import AlpacaDataClient
    from src.data.news_client import NewsClient
    from src.data.scenario_builder import ScenarioDatabase
    from src.data.scenario_recorder import ScenarioAgentRecorder
    import os

    logger.info("═══ MOMENTUM-X SCENARIO RECORDER ═══")

    # Load scenario database
    scenario_path = Path("data/scenarios/gap_scenarios.json")
    if not scenario_path.exists():
        logger.error("No scenario database found at %s. Run 'build-scenarios' first.", scenario_path)
        return

    db = ScenarioDatabase.load(scenario_path)
    logger.info("Loaded %d scenarios from %s", db.n_scenarios, scenario_path)

    if db.n_scenarios == 0:
        logger.error("Empty scenario database. Nothing to record.")
        return

    # Log model cutoff boundary for scenario selection diagnostics
    from datetime import timedelta as _td
    from src.core.llm_leakage import KnowledgeCutoffRegistry
    _registry = KnowledgeCutoffRegistry()
    _cutoff = _registry.get_cutoff(settings.models.tier1_model)
    if _cutoff:
        _buffer_end = _cutoff + _td(days=30)
        _pre = sum(1 for s in db.scenarios if s.date <= _buffer_end.isoformat())
        _post = db.n_scenarios - _pre
        logger.info(
            "Model: %s | Cutoff+buffer: %s | Pre-cutoff: %d | Post-cutoff: %d",
            settings.models.tier1_model, _buffer_end, _pre, _post,
        )
    else:
        logger.warning(
            "Model '%s' has no registered cutoff — all folds will be marked contaminated",
            settings.models.tier1_model,
        )

    # Initialize pipeline
    client = AlpacaDataClient(settings.alpaca)

    try:
        account = await client.get_account()
        logger.info("Account connected: equity=$%.2f", float(account.get("equity", 0)))
    except Exception as e:
        logger.error("Failed to connect to Alpaca: %s", e)
        return

    # Initialize SEC client for dilution detection
    from src.data.sec_client import SECEdgarClient
    sec_client = SECEdgarClient()

    orchestrator = Orchestrator(settings, sec_client=sec_client, data_client=client)

    news_client = NewsClient(
        alpaca_api_key=settings.alpaca.api_key,
        alpaca_secret_key=settings.alpaca.secret_key,
        finnhub_api_key=os.environ.get("FINNHUB_API_KEY", ""),
    )

    recorder = ScenarioAgentRecorder(
        orchestrator=orchestrator,
        client=client,
        news_client=news_client,
        fetch_historical_data=True,
    )

    report = await recorder.record(
        db, max_scenarios=max_scenarios, model_id=settings.models.tier1_model,
    )

    # Save report
    report_path = Path("data/scenarios/recording_report.json")
    report.save(report_path)

    # Print summary
    summary = report.summary()
    logger.info("═══ RECORDING RESULTS ═══")
    for key, val in summary.items():
        logger.info("  %s: %s", key, val)

    # Save signals + returns for CPCV
    from src.data.scenario_recorder import results_to_backtest_arrays
    import numpy as np
    import json as _json

    signals, returns, dates, position_sizes = results_to_backtest_arrays(report.results)
    if signals:
        backtest_data_path = Path("data/scenarios/backtest_data.json")
        backtest_data_path.parent.mkdir(parents=True, exist_ok=True)
        backtest_data_path.write_text(_json.dumps({
            "signals": signals,
            "returns": returns,
            "dates": dates,
            "position_sizes": position_sizes,
            "n_observations": len(signals),
            "source": "scenario_recorder",
        }, indent=2))
        logger.info("Saved %d backtest observations to %s", len(signals), backtest_data_path)

    # D35/D36: Portfolio simulation
    portfolio = report.portfolio_summary()
    logger.info("═══ PORTFOLIO SIMULATION ═══")
    logger.info("  Starting equity: $%s", f"{portfolio['starting_equity']:,.2f}")
    logger.info("  Final equity:    $%s", f"{portfolio['final_equity']:,.2f}")
    logger.info(
        "  Total P&L:       $%s (%s%%)",
        f"{portfolio['total_pnl']:,.2f}", portfolio['total_return_pct'],
    )
    logger.info(
        "  Win rate:        %s%% (%d/%d)",
        portfolio['win_rate_pct'], portfolio['n_wins'], portfolio['n_trades'],
    )
    logger.info(
        "  Avg win:         $%s | Avg loss: $%s",
        f"{portfolio['avg_win']:,.2f}", f"{portfolio['avg_loss']:,.2f}",
    )
    logger.info("  Profit factor:   %s", portfolio['profit_factor'])
    logger.info(
        "  Max drawdown:    %s%% ($%s)",
        portfolio['max_drawdown_pct'], f"{portfolio['max_drawdown_dollars']:,.2f}",
    )


async def cmd_analyze(settings: Settings) -> None:
    """
    Post-session analysis: load closed trades, compute Elo feedback, update arena.

    ### ARCHITECTURAL CONTEXT
    Node ID: cli.analyze
    Graph Link: docs/memory/graph_state.json -> "cli.main"

    ### RESEARCH BASIS
    Closes the Elo optimization loop from ADR-009.
    Ref: docs/research/POST_TRADE_ANALYSIS.md
    Ref: MOMENTUM_LOGIC.md §15 (Elo Feedback Dynamics)

    ### CRITICAL INVARIANTS
    1. Arena state is loaded from disk (or seeded fresh if missing).
    2. Batch analysis processes ALL closed trades from the session.
    3. Updated Elo ratings are saved back to disk.
    4. Summary is printed to stdout for operator review.
    """
    logger.info("📊 Starting post-session analysis...")

    # Load or seed arena
    arena_path = Path("data/arena_ratings.json")
    if arena_path.exists():
        arena = PromptArena.load(str(arena_path))
        logger.info("Loaded arena state from %s", arena_path)
    else:
        arena = seed_default_variants()
        logger.info("No arena state found — seeded defaults")

    analyzer = PostTradeAnalyzer(arena=arena)

    # Load closed trades from position manager data
    trades_path = Path("data/closed_trades.json")
    trade_results: list[TradeResult] = []

    if trades_path.exists():
        import json
        raw_trades = json.loads(trades_path.read_text())
        for t in raw_trades:
            try:
                trade_results.append(TradeResult(
                    ticker=t["ticker"],
                    entry_price=t["entry_price"],
                    exit_price=t["exit_price"],
                    entry_time=datetime.fromisoformat(t["entry_time"]),
                    exit_time=datetime.fromisoformat(t["exit_time"]),
                    agent_variants=t.get("agent_variants", {}),
                    agent_signals=t.get("agent_signals", {}),
                ))
            except (KeyError, ValueError) as e:
                logger.warning("Skipping malformed trade record: %s", e)
    else:
        logger.info("No closed trades found at %s", trades_path)

    # Run batch analysis
    if trade_results:
        total_matchups = analyzer.batch_analyze(trade_results)
        logger.info(
            "Processed %d trades -> %d Elo matchups",
            len(trade_results), total_matchups,
        )

        # Save updated arena state
        arena.save(str(arena_path))
        logger.info("Arena state saved to %s", arena_path)
    else:
        logger.info("No trades to analyze")

    # Print Elo summary
    summary = analyzer.get_elo_summary()
    print("\n🏟️  PROMPT ARENA — ELO RATINGS AFTER ANALYSIS")
    print("=" * 60)
    for vid, elo in sorted(summary.items(), key=lambda x: x[1], reverse=True):
        print(f"  {vid:35s} Elo = {elo:.0f}")
    print("=" * 60)
    print(f"  Trades analyzed: {len(trade_results)}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="momentum-x",
        description="Momentum-X: Explosive Alpha Trading System",
    )
    parser.add_argument(
        "command",
        choices=["scan", "evaluate", "paper", "backtest", "analyze",
                 "build-scenarios", "record-scenarios"],
        help="Operating mode",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="USE LIVE TRADING (requires confirmation). Default: paper.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Scan polling interval in seconds (default: 30). Used with 'scan' command.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run scan once and exit (default: continuous polling).",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        default=True,
        help="Use synthetic data for backtesting (default: True). Future: load historical data.",
    )
    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help="Ticker symbol for historical backtest (e.g., AAPL). Overrides --synthetic.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=252,
        help="Number of trading days for historical backtest (default: 252).",
    )
    parser.add_argument(
        "--tickers",
        type=str,
        default=None,
        help="Comma-separated tickers for multi-ticker backtest (e.g., AAPL,MSFT,TSLA).",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=540,
        help="Lookback days for scenario builder (default: 540 = ~18 months).",
    )
    parser.add_argument(
        "--min-gap",
        type=float,
        default=0.10,
        help="Minimum gap percentage for scenario builder (default: 0.10 = 10%%).",
    )
    parser.add_argument(
        "--max-gap",
        type=float,
        default=5.0,
        help="Maximum gap percentage filter for scenario builder (default: 5.0 = 500%%). "
             "Removes stock-split artifacts and extreme penny stock anomalies.",
    )
    parser.add_argument(
        "--max-scenarios",
        type=int,
        default=None,
        help="Max scenarios to process in record-scenarios (for testing).",
    )
    parser.add_argument(
        "--deterministic-only",
        action="store_true",
        default=False,
        help="D107 WS6: Zero-LLM-calls mode. Only DeterministicTechnical + "
             "DeterministicRisk agents run. Tests whether the scanner/strategy "
             "has fundamental edge independent of LLM quality.",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    # D217: Version telemetry — log exactly which code is running.
    # After the worktree incident (April 9), this is the first line of defense
    # against "running stale code without knowing it."
    import subprocess as _d217_sp
    _d217_repo_path = os.path.dirname(os.path.abspath(__file__))
    try:
        _d217_git_commit = _d217_sp.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_d217_repo_path,
            stderr=_d217_sp.DEVNULL,
        ).decode().strip()
        _d217_git_branch = _d217_sp.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=_d217_repo_path,
            stderr=_d217_sp.DEVNULL,
        ).decode().strip()
    except Exception:
        _d217_git_commit = "unknown"
        _d217_git_branch = "unknown"
    logger.critical(
        "D217 STARTUP: repo=%s branch=%s commit=%s pid=%d python=%s cwd=%s",
        _d217_repo_path, _d217_git_branch, _d217_git_commit[:8],
        os.getpid(), sys.version.split()[0], os.getcwd(),
    )
    # Abort if running from a worktree (stale code risk)
    if ".claude-worktrees" in _d217_repo_path or "worktrees" in _d217_repo_path.lower():
        logger.critical(
            "D217 FATAL: Running from a worktree (%s). "
            "Worktrees contain stale code. Update Task Scheduler to point to the main repo. "
            "Refusing to trade on potentially stale code.",
            _d217_repo_path,
        )
        sys.exit(1)

    # Load .env into os.environ so litellm can find API keys
    # (pydantic-settings reads .env for its own fields but doesn't export
    # non-matching keys like TOGETHER_AI_API_KEY to the environment)
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)
        logger.debug("Loaded environment from %s", _env_path)

    # D221 Phase F (Mon 2026-04-20): startup env-toggle audit.
    # Three silent-activation failures in the prior 48 hours (Path B 36h
    # dormancy; Tier 3 dormant Sun-night; ALPACA exposure during own audit)
    # made activation state opaque to operators. This call enumerates
    # every tracked env var with its loaded status (UNSET/EMPTY/WHITESPACE/SET)
    # plus a length hint (no value echo). Heartbeat embedding happens
    # inside cmd_paper (different scope) via a fresh collect_env_audit
    # call there.
    from src.utils.env_audit import log_env_audit
    log_env_audit(logger)

    # Load settings
    settings = Settings()

    # D95: Detect stale .env overrides (D93 lesson: Kimi K2 models caused 0 trades on Mar 2)
    _stale_env_checks = {
        "LLM_TIER1_MODEL": ["kimi", "k2"],
        "LLM_TIER2_MODEL": ["kimi", "k2"],
    }
    for _env_key, _stale_hints in _stale_env_checks.items():
        _env_val = os.environ.get(_env_key, "")
        if _env_val and any(h in _env_val.lower() for h in _stale_hints):
            logger.warning(
                "D95 STALE ENV OVERRIDE: %s=%s — this overrides settings.py defaults! "
                "Remove from .env if unintended (D93 lesson: caused 0 trades on Mar 2).",
                _env_key, _env_val,
            )

    # D95: Log active model configuration for quick verification
    logger.info(
        "D95 MODEL CONFIG: Tier1=%s | Tier2=%s | Stop=%.1f%% | Debate=%.2f",
        settings.models.tier1_model,
        settings.models.tier2_model,
        settings.execution.stop_loss_pct * 100,
        settings.debate.mfcs_debate_threshold,
    )

    # D107 WS6: Deterministic-only mode flag
    if args.deterministic_only:
        settings.execution.deterministic_only = True
        logger.info("D107: DETERMINISTIC-ONLY mode — zero LLM calls, 2 deterministic agents + RVOL synthetic")

    # Safety: Live trading requires explicit confirmation (INV-007)
    if args.live:
        logger.warning("⚠️  LIVE TRADING MODE REQUESTED ⚠️")
        confirm = input("Type 'I ACCEPT THE RISK' to proceed with REAL MONEY: ")
        if confirm != "I ACCEPT THE RISK":
            logger.info("Live trading cancelled.")
            sys.exit(0)
        settings.mode = "live"
        settings.alpaca.base_url = "https://api.alpaca.markets"
    else:
        settings.mode = "paper"

    # Backtest configuration from CLI flags
    if hasattr(args, 'ticker') and args.ticker:
        settings.backtest_ticker = args.ticker
    if hasattr(args, 'days') and args.days:
        settings.backtest_days = args.days
    if hasattr(args, 'tickers') and args.tickers:
        settings.backtest_tickers = [t.strip() for t in args.tickers.split(",")]

    # Dispatch to command handler
    if args.command == "scan":
        asyncio.run(cmd_scan(settings, interval=args.interval, once=args.once))
    elif args.command == "build-scenarios":
        asyncio.run(cmd_build_scenarios(
            settings,
            lookback_days=args.lookback,
            min_gap_pct=args.min_gap,
            max_gap_pct=args.max_gap,
        ))
    elif args.command == "record-scenarios":
        asyncio.run(cmd_record_scenarios(
            settings,
            max_scenarios=args.max_scenarios,
        ))
    else:
        commands = {
            "evaluate": cmd_evaluate,
            "paper": cmd_paper,
            "backtest": cmd_backtest,
            "analyze": cmd_analyze,
        }
        # D95: Top-level exception handler — log actual crash cause
        # Without this, Python exits with code 1 and Task Scheduler
        # restarts with no clue what happened (March 4: 4 session reports).
        #
        # D221 Bug #3 (Mon 2026-04-20): minimum-viable crash-report-on-
        # exception. When the orchestrator loop dies, also persist a
        # crash_report_<ts>.json next to the heartbeat with the full
        # traceback + last heartbeat snapshot, and exit with a
        # distinguishable code (90 unhandled / 91 cancelled) so the
        # watchdog can tell "I died" from "I was killed". Caught after
        # a 09:40 EDT session crash where the post-mortem had to
        # reconstruct state from logs that nearly rotated out.
        try:
            asyncio.run(commands[args.command](settings))
        except KeyboardInterrupt:
            logger.info("Shutdown via Ctrl+C")
        except SystemExit as e:
            logger.info("SystemExit: code=%s", e.code)
            raise
        except BaseException as e:  # noqa: BLE001 — must catch everything except KI/SE
            logger.critical(
                "D95 FATAL CRASH: %s: %s", type(e).__name__, e, exc_info=True,
            )
            try:
                from src.utils.crash_report import (
                    write_crash_report,
                    EXIT_CODE_UNHANDLED,
                    EXIT_CODE_CANCELLED,
                    _is_cancelled_error,
                )
                report_path = write_crash_report(
                    e,
                    context={
                        "command": args.command,
                        "mode": getattr(settings, "mode", "unknown"),
                    },
                )
                exit_code = (
                    EXIT_CODE_CANCELLED
                    if _is_cancelled_error(e)
                    else EXIT_CODE_UNHANDLED
                )
                logger.critical(
                    "D221 Bug #3: crash report at %s, exiting with code %d",
                    report_path, exit_code,
                )
                sys.exit(exit_code)
            except SystemExit:
                raise
            except Exception as report_err:  # noqa: BLE001
                # Never let crash-report writing mask the original crash.
                logger.error(
                    "D221 Bug #3: crash-report write itself failed: %s — "
                    "re-raising original exception so process still dies",
                    report_err,
                )
                raise e from None


if __name__ == "__main__":
    main()
