"""
MOMENTUM-X Alpaca Executor

### ARCHITECTURAL CONTEXT
Node ID: execution.alpaca_executor
Graph Link: docs/memory/graph_state.json → "execution.alpaca_executor"

### RESEARCH BASIS
Stateless order adapter per ADR-003 §1.
Converts TradeVerdict → Alpaca OTO order (buy limit + stop sell).
Half-Kelly sizing with 5% hard cap (MOMENTUM_LOGIC.md §6, INV-009).

### CRITICAL INVARIANTS
1. Paper trading is the only default mode (INV-007).
2. Max 3 concurrent positions (ExecutionConfig.max_positions).
3. Max 5% portfolio per position (ExecutionConfig.max_position_pct).
4. Records signal_price for slippage analysis (ADR-003 §3, H-005).
5. NO_TRADE and zero-size verdicts are silently skipped.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from config.settings import ExecutionConfig
from src.core.models import TradeVerdict

logger = logging.getLogger(__name__)


def compute_marketable_offset(
    mfcs: float | None,
    *,
    base_offset: float,
    max_offset: float,
    mfcs_full_at: float,
) -> float:
    """doc 189: momentum-adaptive marketable offset (fraction above the anchor).

    Scales linearly with conviction from ``base_offset`` (low/None MFCS) to
    ``max_offset`` at ``mfcs_full_at`` and CLAMPS there — so a strong continuer
    chases a bit more to ensure the fill while a marginal name stays passive, but
    we NEVER chase a runner past the cap. Pure + deterministic (train==serve
    testable without mocking the executor or the broker).
    """
    conv = (
        min(max((mfcs or 0.0) / mfcs_full_at, 0.0), 1.0)
        if mfcs_full_at > 0
        else 0.0
    )
    return base_offset + (max_offset - base_offset) * conv


def compute_marketable_limit(
    *,
    side: str,
    entry_price: float,
    quote_price: float | None,
    mfcs: float | None,
    base_offset: float,
    max_offset: float,
    mfcs_full_at: float,
) -> float:
    """doc 189/190: marketable entry-limit price for either direction.

    - ``side='buy'`` (long entry): anchor = max(entry, ask), cross UP   -> anchor*(1+offset)
    - ``side='sell'`` (short entry): anchor = min(entry, bid), cross DOWN -> anchor*(1-offset)

    ``quote_price`` is the live ask (buy) or bid (sell); pass None/<=0 to fall back to
    ``entry_price`` (so a missing quote degrades to entry-anchored, still marketable by
    the offset). The offset scales with conviction (``compute_marketable_offset``) and is
    CAPPED, and the result is a LIMIT so the worst fill is bounded. Pure + deterministic
    (train==serve testable without mocking the executor/broker).
    """
    offset = compute_marketable_offset(
        mfcs, base_offset=base_offset, max_offset=max_offset, mfcs_full_at=mfcs_full_at
    )
    anchor = entry_price
    if quote_price and quote_price > 0:
        anchor = max(anchor, quote_price) if side == "buy" else min(anchor, quote_price)
    factor = (1.0 + offset) if side == "buy" else (1.0 - offset)
    return round(anchor * factor, 4)


class OrderResult(BaseModel):
    """
    Result of an order submission to Alpaca.
    Tracks both signal-time price and submitted price for slippage analysis.

    Node ID: execution.alpaca_executor.OrderResult
    Ref: ADR-003 §3 (Slippage Tracking)
    """

    order_id: str
    status: str
    ticker: str
    qty: int
    side: str = "buy"
    order_type: str = "bracket"
    signal_price: float = Field(
        description="Price when TradeVerdict was generated. For H-005 slippage analysis."
    )
    submitted_price: float = Field(
        description="Limit price sent to Alpaca."
    )
    stop_loss: float = 0.0
    take_profit: float = 0.0
    stop_order_id: str = ""  # D64: Actual stop order ID from D57 separate stop submission
    fill_price: float = 0.0  # D121 BUG-2: Actual fill price from broker (set on fill confirmation)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Bug R fix (2026-04-23): the stop price ACTUALLY submitted to the
    # broker, post Phase-0 / Phase-1 tightening. May differ from
    # `stop_loss` (verdict's original) when D142 phase-stop logic kicks
    # in. The bridge must use this value when constructing
    # ManagedPosition.stop_loss so the internal tracker matches broker
    # truth, not the un-tightened verdict value.
    actual_stop_price: float | None = None


class AlpacaExecutor:
    """
    Stateless order submitter: TradeVerdict → Alpaca API call.

    Node ID: execution.alpaca_executor
    Graph Link: docs/memory/graph_state.json → "execution.alpaca_executor"

    Does NOT track state. That is PositionManager's job.
    Validates: position count limit, size caps, actionable verdicts.
    Submits: bracket orders (entry + stop + take_profit).

    Ref: ADR-003 §1 (Stateless Order Submitter)
    Ref: MOMENTUM_LOGIC.md §6 (Half-Kelly, hard cap 5%)
    Ref: DATA-001 (Alpaca API)
    """

    def __init__(
        self,
        config: ExecutionConfig,
        client: Any,
        instrumentation: Any | None = None,
        kelly_governor: Any | None = None,
        stop_decision_log: Any | None = None,
    ) -> None:
        self._config = config
        self._client = client
        # Phase 0 instrumentation writer — optional. Each emit is wrapped
        # so an instrumentation failure NEVER crashes the trading hot
        # path. Pass None to disable.
        self._instrumentation = instrumentation
        # Kelly governor — read at every entry sizing call to apply the
        # active multiplier (1.0 normal, 0.5 during D223 BOCPD_BREAK halve
        # window). When None, full Kelly applies (governance disabled).
        self._kelly_governor = kelly_governor
        # D308 (2026-05-19): optional StopDecisionLog. When provided,
        # every OTO submission emits a structured event capturing both
        # the orchestrator's wide stop AND the D142 Phase 1 tightened
        # stop, so the offline replayer (scripts/stop_widening_replay)
        # can compute hypothetical P&L under wider stops. Production
        # behavior is UNCHANGED -- we still submit the tight stop.
        # See doc 169 for the T1/T2/T3 validation plan.
        self._stop_decision_log = stop_decision_log

    async def execute(self, verdict: TradeVerdict) -> OrderResult | None:
        """
        Convert a TradeVerdict into an Alpaca order.

        Returns None if:
        - Verdict is NO_TRADE or HOLD
        - position_size_pct is 0
        - Max concurrent positions reached

        Ref: ADR-003
        """
        # ── Guard: skip non-actionable verdicts ──
        if verdict.action in ("NO_TRADE", "HOLD"):
            logger.info("%s: Skipping (action=%s)", verdict.ticker, verdict.action)
            return None

        if verdict.position_size_pct <= 0:
            logger.info("%s: Skipping (position_size_pct=0)", verdict.ticker)
            return None

        if verdict.entry_price <= 0:
            logger.error("%s: Invalid entry_price=%.4f", verdict.ticker, verdict.entry_price)
            # doc 272 VLL: invalid-entry terminal
            try:
                from src.ops.verdict_ledger import vll_emit
                vll_emit("BLOCKED_INVALID_ENTRY", verdict.ticker,
                         reason=f"entry_price={verdict.entry_price:.4f}", path="EXEC")
            except Exception:
                pass
            return None

        # ── Guard: max concurrent positions ──
        positions = await self._client.get_positions()
        if len(positions) >= self._config.max_positions:
            logger.warning(
                "%s: Skipping — at max %d positions",
                verdict.ticker, self._config.max_positions,
            )
            # doc 272 VLL: broker-level max-positions terminal
            try:
                from src.ops.verdict_ledger import vll_emit
                vll_emit("BLOCKED_MAX_POSITIONS_EXEC", verdict.ticker,
                         reason=f"broker positions {len(positions)}>={self._config.max_positions}",
                         path="EXEC")
            except Exception:
                pass
            return None

        # ── Compute share quantity (D101: fixed-risk sizing) ──
        account = await self._client.get_account()
        equity = float(account.get("equity", 0))
        if equity <= 0:
            logger.error("Account equity is zero or negative")
            # doc 272 VLL: zero-equity terminal
            try:
                from src.ops.verdict_ledger import vll_emit
                vll_emit("BLOCKED_ZERO_EQUITY", verdict.ticker,
                         reason=f"equity={equity:.0f}", path="EXEC")
            except Exception:
                pass
            return None

        # D150: Aggressive paper trading — tier-based sizing by float
        _tier = 3  # Default tier
        _tier_pct = self._config.max_position_pct  # Default sizing
        if getattr(self._config, "paper_aggressive_mode", False):
            # Get float from enrichment (if available)
            _float = getattr(verdict, "float_shares", None)
            _gap = abs(getattr(verdict, "gap_pct", 0) if hasattr(verdict, "gap_pct") else 0)
            _rvol = getattr(verdict, "rvol", 0) if hasattr(verdict, "rvol") else 0

            if (_float is not None and _float < self._config.tier1_float_max
                    and _gap >= self._config.tier1_gap_min
                    and _rvol >= self._config.tier1_rvol_min):
                _tier = 1
                _tier_pct = self._config.tier1_position_pct
            elif (_float is not None and _float < self._config.tier2_float_max
                    and _gap >= self._config.tier2_gap_min
                    and _rvol >= self._config.tier2_rvol_min):
                _tier = 2
                _tier_pct = self._config.tier2_position_pct
            else:
                _tier = 3
                _tier_pct = getattr(self._config, "tier3_position_pct", 0.15)

            logger.info(
                "D150 TIER %d: %s position_size=%.0f%% float=%s gap=%.0f%% rvol=%.1fx",
                _tier, verdict.ticker,
                _tier_pct * 100,
                f"{_float/1e6:.1f}M" if _float else "?",
                _gap * 100, _rvol,
            )

        # D161: Determine trade direction (long vs short)
        _direction = getattr(verdict, "direction", "long")
        _is_short = _direction == "short"

        # D101: Fixed-risk position sizing.
        # D115: Use per-trade risk from Kelly tier when available.
        # qty = floor(equity × risk_per_trade_pct / stop_distance)
        # This sizes down for volatile names (wide ATR stops) and up for calm names.
        # Capped by max_position_pct to prevent any single position from being too large.
        _risk_pct = getattr(verdict, "risk_per_trade_pct", None) or self._config.risk_per_trade_pct
        # Kelly governor wire-in: when D223 BOCPD_BREAK has fired, the
        # governor's current_multiplier() drops to 0.5 for the next 5
        # closed trades. Apply to BOTH the risk budget AND the position
        # cap so qty halves correctly. No-op when governor is None or
        # is_active=False (current_multiplier() returns 1.0).
        _kelly_mult = 1.0
        if self._kelly_governor is not None:
            try:
                _kelly_mult = float(self._kelly_governor.current_multiplier())
            except Exception as _ke:
                logger.debug("Kelly governor read failed: %s", _ke)
                _kelly_mult = 1.0
        if _kelly_mult < 1.0:
            logger.warning(
                "D224 KELLY_HALVED ACTIVE — sizing %s at multiplier=%.2f "
                "(remaining trades in halve window: %d)",
                verdict.ticker, _kelly_mult,
                getattr(self._kelly_governor, "remaining_trades", 0),
            )
        _risk_pct = _risk_pct * _kelly_mult
        if _is_short:
            # D161: Short position — stop is ABOVE entry (stop_loss > entry_price)
            stop_distance = verdict.stop_loss - verdict.entry_price
            if stop_distance <= 0:
                logger.error(
                    "%s: Invalid short stop (%.2f) <= entry (%.2f) — using fallback pct sizing",
                    verdict.ticker, verdict.stop_loss, verdict.entry_price,
                )
                stop_distance = verdict.entry_price * self._config.stop_loss_pct
        else:
            # D150: Validate stop is below entry (long position). abs() was masking
            # inverted stops where stop_loss > entry_price, causing wrong sizing.
            stop_distance = verdict.entry_price - verdict.stop_loss
            if stop_distance <= 0:
                logger.error(
                    "%s: Invalid stop (%.2f) >= entry (%.2f) — using fallback pct sizing",
                    verdict.ticker, verdict.stop_loss, verdict.entry_price,
                )
                stop_distance = verdict.entry_price * self._config.stop_loss_pct
        if stop_distance > 0 and _risk_pct > 0:
            risk_budget = equity * _risk_pct
            qty_risk = math.floor(risk_budget / stop_distance)
            # Cap by max_position_pct (or tier pct in aggressive mode).
            # ── D150 OVERRIDE: verdict.position_size_pct is ADVISORY-ONLY here ──
            # In aggressive paper mode (config.paper_aggressive_mode, the default
            # — config/settings.py:650), effective_pct is the flat D150 tier_pct
            # (15% for Tier 3), NOT the orchestrator's elaborate D96 SIZING number.
            # The orchestrator's D46-float / D101-VIX / D106-PROMOTIONAL_EARLY /
            # MFCS / Kelly multiplier stack (orchestrator.py ~2944-3120) shrinks
            # verdict.position_size_pct, but that value is discarded here — so
            # those multipliers do NOT influence deployed capital under the
            # default mode. They also do NOT feed the fixed-risk qty below
            # (qty_risk = equity·risk_per_trade_pct ÷ stop_distance): the only
            # knobs that actually move deployed capital in aggressive mode are
            # risk_per_trade_pct, stop_distance, _tier_pct, _kelly_mult, and the
            # D310.T2 qty_multiplier. effective_pct is only a CEILING (qty_cap),
            # and qty = min(qty_risk, qty_cap) — fixed-risk usually wins.
            # See orchestrator.py advisory block above the D96 chain, and
            # docs/research-log/176_mariana_trench_audit_why_not_five_percent.md §3.
            # Re-wiring (honor position_size_pct, or fold float/D106 into the
            # fixed-risk path) is a gated Phase B decision in that doc — do not
            # quietly flip this without the conviction-tier gating + A/B it calls
            # for, since it directly changes real capital deployed.
            effective_pct = (_tier_pct if getattr(self._config, "paper_aggressive_mode", False) else verdict.position_size_pct) * _kelly_mult
            qty_cap = math.floor(equity * effective_pct / verdict.entry_price)
            qty = min(qty_risk, qty_cap)
            sizing_method = "fixed_risk"
            _kelly_tier = getattr(verdict, "kelly_tier", 1)
            logger.info(
                "D101 SIZING %s: risk=$%.0f (%.1f%% of $%.0f) / stop_dist=$%.2f → "
                "qty_risk=%d, qty_cap=%d (%.1f%% cap) → qty=%d%s",
                verdict.ticker, risk_budget, _risk_pct * 100,
                equity, stop_distance, qty_risk, qty_cap,
                effective_pct * 100, qty,
                f" [D115:Tier{_kelly_tier}]" if _kelly_tier > 1 else "",
            )
        else:
            # Fallback: percentage-based sizing when no valid stop distance.
            # Same D150 override as the fixed-risk path above: in aggressive mode
            # this uses the flat tier_pct and ignores verdict.position_size_pct
            # (and the orchestrator's D46/D101/D106/MFCS stack). See the advisory
            # block at the fixed-risk branch above and doc 176 §3 before changing.
            effective_pct = (_tier_pct if getattr(self._config, "paper_aggressive_mode", False) else verdict.position_size_pct) * _kelly_mult
            dollar_amount = equity * effective_pct
            qty = math.floor(dollar_amount / verdict.entry_price)
            sizing_method = "pct_fallback"
            logger.warning(
                "D101 SIZING %s: No valid stop_distance (entry=$%.2f, stop=$%.2f) — "
                "falling back to pct sizing: $%.0f × %.1f%% → qty=%d",
                verdict.ticker, verdict.entry_price, verdict.stop_loss,
                equity, effective_pct * 100, qty,
            )

        # D96: Log complete position sizing conversion for EOD audit
        logger.info(
            "D96 EXECUTION %s: equity=$%.0f → qty=%d @ $%.2f "
            "(actual_alloc=%.1f%% of equity, method=%s)",
            verdict.ticker, equity, qty, verdict.entry_price,
            (qty * verdict.entry_price / equity * 100) if equity > 0 else 0,
            sizing_method,
        )

        # D310.T2 (2026-05-24, doc 171) + doc 178: apply qty_multiplier BEFORE the
        # qty<=0 guard so a halved-to-zero edge case fails fast. qty_multiplier is a
        # GENERAL dollar-risk reducer (1.0 = no change):
        #   - T2 wide_stop arm sets 0.5 (compensates the 3-25x wider stop), and
        #   - doc 178 D200/D204 downgrade-not-block sets 0.5 (no-catalyst high-MFCS
        #     entry — bounds pump risk while still admitting the squeeze).
        # doc 178 FIX: previously this was gated to `arm == "wide_stop"`, which
        # SILENTLY NO-OP'd the doc-178 downgrade on the default tight_stop arm (the
        # half-size never applied). Now applied for ANY arm when mult < 1.0. The
        # tight_stop default qty_multiplier is 1.0, so T2 behavior is unchanged.
        _t2_arm = getattr(verdict, "execution_arm", "tight_stop")
        _t2_mult = float(getattr(verdict, "qty_multiplier", 1.0) or 1.0)
        if _t2_mult < 1.0:
            _qty_before = qty
            qty = max(1, int(math.floor(qty * _t2_mult)))
            logger.info(
                "SIZING %s: qty_multiplier=%.2f (arm=%s) -> qty %d -> %d "
                "(dollar-risk bound: T2 wide-stop and/or doc178 downgrade)",
                verdict.ticker, _t2_mult, _t2_arm, _qty_before, qty,
            )

        if qty <= 0:
            logger.warning(
                "%s: Computed qty=0 (equity=%.0f, price=%.2f, stop=$%.2f)",
                verdict.ticker, equity, verdict.entry_price, verdict.stop_loss,
            )
            # doc 272 VLL: sizing-computed-zero terminal
            try:
                from src.ops.verdict_ledger import vll_emit
                vll_emit("BLOCKED_QTY_ZERO", verdict.ticker,
                         reason=(f"computed qty=0 (equity={equity:.0f} "
                                 f"price={verdict.entry_price:.2f})"),
                         path="EXEC")
            except Exception:
                pass
            return None

        # ── D57-fix / D161: Submit OTO (One-Triggers-Other) order ──
        # For LONGS: buy limit + sell stop (stop below entry)
        # For SHORTS: sell limit + buy stop (stop above entry, covers the short)
        #
        # OTO ensures the stop leg only activates AFTER the entry leg fills, preventing
        # Alpaca 403 "potential wash trade detected" on pending-order conflicts.
        take_profit_price = verdict.target_prices[0] if verdict.target_prices else (
            verdict.entry_price * (0.90 if _is_short else 1.10)
        )

        # D142: Time-phased stops apply to LONGS only.
        # For shorts, use verdict.stop_loss as-is (stop is already set wide above entry).
        # ── D308 (2026-05-19): track which stop wins for shadow log ──
        # The orchestrator's verdict.stop_loss is the WIDE stop (ATR-based,
        # potentially widened by D122 gap-floor logic). D142 may override
        # with a TIGHTER stop here. We need to remember both for the shadow
        # log emission AFTER the decision settles.
        _d308_atr_stop = verdict.stop_loss  # the "wide" candidate
        _d308_phase1_stop: float | None = None
        _d308_phase1_pct: float | None = None
        _d308_strategy = "atr"
        _stop_price = verdict.stop_loss
        # D310.T2 (2026-05-24, doc 171): wide_stop arm SKIPS the D142 Phase 1
        # override entirely. The whole point of T2 is to use the wide ATR
        # stop directly so the bot doesn't shake out of momentum runs at
        # 1.5% noise. _d308_strategy stays "atr" for downstream shadow log.
        _t2_wide_arm = (not _is_short) and (_t2_arm == "wide_stop")
        if not _is_short and not _t2_wide_arm and self._config.phase_stop_enabled:
            if (getattr(self._config, "phase0_stop_enabled", False)
                    and getattr(self._config, "phase0_stop_pct", 0) > 0):
                # Phase 0: ultra-tight for first 30s (pipeline inversion safety)
                _phase0_stop = verdict.entry_price * (1 - self._config.phase0_stop_pct)
                if _phase0_stop > _stop_price:
                    _stop_price = round(_phase0_stop, 4)
                    _d308_phase1_stop = _stop_price
                    _d308_phase1_pct = self._config.phase0_stop_pct
                    _d308_strategy = "phase0"
                    logger.info(
                        "%s D142: Phase 0 stop $%.2f (%.1f%%) — ultra-tight for %.0fs "
                        "pipeline inversion safety",
                        verdict.ticker, _phase0_stop, self._config.phase0_stop_pct * 100,
                        getattr(self._config, "phase0_duration_seconds", 30),
                    )
            elif self._config.phase1_stop_pct > 0:
                # Phase 1: tight for momentum confirmation
                _phase1_stop = verdict.entry_price * (1 - self._config.phase1_stop_pct)
                if _phase1_stop > _stop_price:
                    _stop_price = round(_phase1_stop, 4)
                    _d308_phase1_stop = _stop_price
                    _d308_phase1_pct = self._config.phase1_stop_pct
                    _d308_strategy = "phase1"

        # doc 189/190 (FILL the continuers): marketable entry limits for EVERY path.
        # A passive limit at the STALE eval price doesn't fill on thin movers (CMND
        # 0/2, Friday 5/29) -- a perfect PICK we can't FILL earns $0. Fetch ONE
        # snapshot (ask for buys, bid for shorts) and cross the spread by a
        # conviction-scaled, CAPPED offset; a LIMIT (not market) bounds the worst fill.
        _mk_enabled = getattr(self._config, "marketable_limit_enabled", True)
        _mk_ask: float | None = None
        _mk_bid: float | None = None
        if _mk_enabled:
            try:
                _mk_snaps = await self._client.get_snapshots([verdict.ticker])
                _mk_s = (_mk_snaps or {}).get(verdict.ticker, {}) or {}
                _mk_ask = float(_mk_s.get("ask") or 0) or float(_mk_s.get("last_price") or 0) or None
                _mk_bid = float(_mk_s.get("bid") or 0) or float(_mk_s.get("last_price") or 0) or None
            except Exception:
                pass

        def _mk_limit(_side: str, _quote: float | None) -> float:
            if not _mk_enabled:
                return verdict.entry_price
            _px = compute_marketable_limit(
                side=_side,
                entry_price=verdict.entry_price,
                quote_price=_quote,
                mfcs=getattr(verdict, "mfcs", None),
                base_offset=getattr(self._config, "marketable_base_offset_pct", 0.004),
                max_offset=getattr(self._config, "marketable_max_offset_pct", 0.015),
                mfcs_full_at=getattr(self._config, "marketable_mfcs_full_at", 0.55),
            )
            logger.info(
                "D190 MARKETABLE %s LIMIT %s: entry=$%.4f quote=%s -> limit=$%.4f "
                "(mfcs=%.2f, qty=%d)",
                _side.upper(), verdict.ticker, verdict.entry_price,
                (f"${_quote:.4f}" if _quote else "n/a"), _px,
                getattr(verdict, "mfcs", None) or 0.0, qty,
            )
            return _px

        # ── doc 286 (doc-285 gap #9, stale-anchor): CAP AT THE PRICE YOU MIGHT PAY ──
        # qty_cap above divides by verdict.entry_price — the FROZEN eval price —
        # while the D190 marketable limit chases the live ask. RIVN 7/6: sized
        # 1,552sh against $18.50, the limit chased to $19.44, and the "15% cap"
        # filled at 15.65% of equity. Compute the entry limit ONCE here, then
        # re-check the cap against the WORST price this order may legally fill
        # at — max(eval, limit) — and shrink qty when the cap no longer holds.
        # qty only ever gets SMALLER here (strictly conservative); risk-bound
        # sizings already below the re-anchored cap are untouched by the min.
        _entry_limit_px = _mk_limit("sell", _mk_bid) if _is_short else _mk_limit("buy", _mk_ask)
        _worst_fill_px = max(verdict.entry_price, _entry_limit_px)
        if _worst_fill_px > verdict.entry_price:
            _qty_at_worst = math.floor(equity * effective_pct / _worst_fill_px)
            if _qty_at_worst < qty:
                logger.info(
                    "D286 STALE-ANCHOR CAP %s: limit $%.4f > eval $%.4f — cap "
                    "re-anchored to worst fill: qty %d -> %d (%.1f%% cap)",
                    verdict.ticker, _entry_limit_px, verdict.entry_price,
                    qty, _qty_at_worst, effective_pct * 100,
                )
                qty = _qty_at_worst
            if qty <= 0:
                logger.warning(
                    "%s: qty=0 after D286 worst-fill cap re-anchor (equity=%.0f, "
                    "limit=$%.4f) — one share would breach the cap; skipping entry",
                    verdict.ticker, equity, _entry_limit_px,
                )
                # doc 272 VLL: sizing-computed-zero terminal (doc-286 recap)
                try:
                    from src.ops.verdict_ledger import vll_emit
                    vll_emit("BLOCKED_QTY_ZERO", verdict.ticker,
                             reason=(f"doc286 worst-fill cap: qty=0 "
                                     f"(equity={equity:.0f} "
                                     f"limit={_entry_limit_px:.4f})"),
                             path="EXEC")
                except Exception:
                    pass
                return None

        if _is_short:
            logger.info(
                "D161 SHORT %s: Submitting OTO order (sell limit + buy stop) — "
                "qty=%d, entry=%.2f, stop=%.2f (+%.1f%% above entry)",
                verdict.ticker, qty, verdict.entry_price, _stop_price,
                (_stop_price / verdict.entry_price - 1) * 100,
            )
            response = await self._client.submit_oto_short_order(
                symbol=verdict.ticker,
                qty=qty,
                limit_price=_entry_limit_px,
                stop_loss=_stop_price,
                time_in_force="day",
            )
        else:
            # D310.T2 (2026-05-24, doc 171) L1 STANDALONE-STOP WIDE-ARM:
            # When verdict.execution_arm == "wide_stop", we submit a plain
            # BUY limit (NOT an OTO with a stop child). Then a background
            # task polls for fill and submits a SEPARATE protective STOP
            # order. This bypasses TrailingStopManager entirely -- there's
            # no OTO leg ID for it to cancel-then-fail-to-resubmit (D310).
            # Layer 2 hedge_integrity_watcher backstops this with a 20s-
            # poll invariant check (qty != 0 -> has_protective_order).
            if _t2_wide_arm:
                logger.info(
                    "D310.T2 WIDE_STOP %s: submitting plain BUY limit (no OTO) "
                    "qty=%d entry=$%.4f atr_stop=$%.4f (%.1f%% from entry); "
                    "standalone STOP will be submitted after fill confirmation",
                    verdict.ticker, qty, verdict.entry_price, _stop_price,
                    (_stop_price / verdict.entry_price - 1) * 100,
                )
                # D308 shadow log: still emit so the replayer can compare
                # actual wide-arm trades to what they would have been
                # under tight stops.
                if self._stop_decision_log is not None:
                    try:
                        self._stop_decision_log.log_stop_decision(
                            symbol=verdict.ticker, side="buy",
                            entry_price=verdict.entry_price,
                            atr_stop=_d308_atr_stop,
                            phase1_stop=_d308_phase1_stop,
                            phase1_pct=_d308_phase1_pct,
                            submitted_stop=_stop_price,
                            submitted_strategy="wide_arm_standalone",
                            qty=qty,
                            gap_pct=getattr(verdict, "gap_pct", None),
                            atr_14d=getattr(verdict, "atr_ratio", None),
                            kelly_tier=getattr(verdict, "kelly_tier", None),
                            mfcs=getattr(verdict, "mfcs", None),
                            target_prices=list(verdict.target_prices)
                                if verdict.target_prices else None,
                        )
                    except Exception:
                        pass
                # doc 189/190 (FILL): marketable, ask-anchored, conviction-scaled BUY
                # limit (shared _mk_limit helper) — the standalone STOP below is
                # unchanged (ATR-from-entry); the <=cap buffer only modestly widens
                # effective risk.
                response = await self._client.submit_limit_order(
                    symbol=verdict.ticker, qty=qty, side="buy",
                    limit_price=_entry_limit_px, time_in_force="day",
                )
                # Fire-and-forget: poll for fill, then submit standalone
                # protective STOP. Never raises out of this caller -- if
                # something goes wrong, Layer 2 hedge_integrity_watcher
                # detects qty != 0 with no stop and intervenes within 60s.
                import asyncio as _aio
                _aio.ensure_future(self._t2_arm_standalone_stop(
                    entry_order_id=response.get("id", ""),
                    symbol=verdict.ticker,
                    intended_qty=qty,
                    stop_price=_stop_price,
                ))
            else:
                logger.info(
                    "%s: Submitting OTO order (buy limit + stop sell) — qty=%d, entry=%.2f, stop=%.2f",
                    verdict.ticker, qty, verdict.entry_price, _stop_price,
                )
                # D308 (2026-05-19): emit shadow log BEFORE submission so
                # every long-side OTO attempt is captured, including ones
                # that may subsequently get BRIDGE_CANCEL'd or rejected.
                # Submit-but-no-fill events are filterable downstream via
                # cross-reference with the trade_journal at replay time.
                if self._stop_decision_log is not None:
                    try:
                        self._stop_decision_log.log_stop_decision(
                            symbol=verdict.ticker,
                            side="buy",
                            entry_price=verdict.entry_price,
                            atr_stop=_d308_atr_stop,
                            phase1_stop=_d308_phase1_stop,
                            phase1_pct=_d308_phase1_pct,
                            submitted_stop=_stop_price,
                            submitted_strategy=_d308_strategy,
                            qty=qty,
                            gap_pct=getattr(verdict, "gap_pct", None),
                            atr_14d=getattr(verdict, "atr_ratio", None),
                            kelly_tier=getattr(verdict, "kelly_tier", None),
                            mfcs=getattr(verdict, "mfcs", None),
                            target_prices=list(verdict.target_prices)
                                if verdict.target_prices else None,
                            verdict_id=getattr(verdict, "verdict_id", None),
                        )
                    except Exception:
                        pass  # shadow log MUST NOT block trading
                response = await self._client.submit_oto_order(
                    symbol=verdict.ticker,
                    qty=qty,
                    limit_price=_entry_limit_px,
                    stop_loss=_stop_price,
                    time_in_force="day",
                )
        entry_order_id = response.get("id", "unknown")

        # D65: Track order rejections and partial fills
        order_status = response.get("status", "unknown")
        if order_status in ("rejected", "canceled", "expired"):
            try:
                from src.monitoring.metrics import get_metrics
                get_metrics().orders_rejected.inc()
            except Exception:
                pass
            logger.warning(
                "%s: OTO order rejected by broker: status=%s, id=%s",
                verdict.ticker, order_status, entry_order_id,
            )
            # doc 272 VLL: broker rejected the OTO at submit — terminal
            try:
                from src.ops.verdict_ledger import vll_emit
                vll_emit("REJECTED_BROKER_OTO", verdict.ticker,
                         reason=f"order {str(entry_order_id)[:12]} {order_status}",
                         path="EXEC")
            except Exception:
                pass
            # Sweep fix: Return None so bridge.py doesn't create a phantom position.
            # Previously, rejected orders still returned OrderResult, causing
            # bridge.execute_verdict() to add_position() for non-existent orders.
            return None
        elif order_status == "partially_filled":
            try:
                from src.monitoring.metrics import get_metrics
                get_metrics().orders_partial_fills.inc()
            except Exception:
                pass
            # Bug D fix (2026-04-22): previously this branch overwrote
            # `qty` with the submit-time filled_qty, freezing a partial
            # fill as the official position size. AGPU showed up here at
            # submit+~0ms with filled_qty=505 of 846 — bridge.py recorded
            # 505, broker eventually filled 846, exit sold 846, P&L
            # journal recorded 505 (silent qty drift).
            #
            # New behavior: do NOT overwrite qty. Pass the ORDERED qty
            # back with status='partially_filled'; bridge.py's
            # `_poll_for_terminal_fill` will poll until status reaches
            # {filled, done_for_day, canceled, ...} and reconcile via
            # the terminal `filled_qty` value. The post-entry D218
            # checks at T+5/T+30/T+60s catch any late-terminal drift.
            try:
                _submit_filled_qty = int(float(response.get("filled_qty", 0)))
            except (ValueError, TypeError):
                _submit_filled_qty = 0
            logger.warning(
                "%s: OTO order partially filled at submit: status=%s, id=%s, "
                "submit_filled=%d/%d — preserving ORDERED qty; bridge poll will "
                "drive to terminal (Bug D fix)",
                verdict.ticker, order_status, entry_order_id,
                _submit_filled_qty, qty,
            )
            # Intentionally NO `qty = ...` reassignment. Keep ordered qty.

        # Extract stop leg order ID from OTO response legs
        # Alpaca returns legs as a list of child orders in the OTO response
        # D161: For short OTO orders, the stop leg is a BUY stop (side="buy"),
        # not a sell stop. Selecting the wrong side left stop_order_id always
        # empty for shorts, breaking stop registration and monitoring.
        stop_order_id = ""
        legs = response.get("legs") or []
        _expected_stop_side = "buy" if _is_short else "sell"
        for leg in legs:
            if leg.get("side") == _expected_stop_side and leg.get("type") == "stop":
                stop_order_id = leg.get("id", "")
                break

        # Phase 0 instrumentation — emit submit-time TradeContextRow.
        # Wrapped to never crash the trading hot path on instrumentation
        # failure; D261 graceful path via emit_*_dict.
        if self._instrumentation is not None:
            try:
                from datetime import datetime, timezone
                self._instrumentation.emit_trade_context_dict({
                    "order_id": entry_order_id,
                    "ticker": verdict.ticker,
                    "side": "sell" if _is_short else "buy",
                    "requested_qty": qty,
                    "requested_px": float(verdict.entry_price),
                    "submit_ts": datetime.now(timezone.utc),
                    "terminal_status": "pending",
                    "terminal_filled_qty": 0,
                })
            except Exception as _e:
                logger.debug("Phase 0 emit_trade_context (submit) failed: %s", _e)

        if stop_order_id:
            # Bug R fix (2026-04-23): use _stop_price (the actually-
            # submitted, Phase-0/1-tightened value) NOT verdict.stop_loss
            # (the un-tightened original). Today's XNDU trade proved the
            # log line was lying — submitted at $32.44, log said $25.72.
            logger.info(
                "%s: OTO stop leg — oid=%s @ $%.2f (activates on buy fill)",
                verdict.ticker, stop_order_id, _stop_price,
            )
        else:
            logger.warning(
                "%s: OTO response has no stop leg ID — stop may not be extractable from legs=%s",
                verdict.ticker, legs,
            )

        # D121 BUG-2: Extract fill price from Alpaca response if available.
        # For instant fills, filled_avg_price is set immediately. For pending
        # orders, it arrives later via WebSocket — fill_price stays 0.0 and
        # bridge.py falls back to verdict.entry_price.
        _fill_price = float(response.get("filled_avg_price") or 0.0)

        # ── doc 286 (doc-285 gap #9, stale-anchor): TRANCHE TARGETS OFF THE FILL ──
        # RIVN 7/6: target_prices stayed anchored to the frozen eval price
        # ($18.50) while the D190 marketable limit chased the fill +4.4% — T1
        # sat only 0.7% above cost and clipped in 4 minutes. When the broker
        # CONFIRMS a fill price in the submit response (instant fill — the norm
        # for marketable limits), re-anchor the targets to the price actually
        # PAID (target_pct × fill, not × eval) — the same ratio pattern as the
        # doc-229 re-buy merge (position_manager.py:794).
        #   - TradeVerdict is frozen=True, so the list is re-anchored IN PLACE:
        #     the bridge (bridge.py:1718) and post_fill_handler read
        #     verdict.target_prices AFTER execute() returns, so the
        #     ManagedPosition / exit ladder / D165 taker all inherit
        #     fill-anchored targets.
        #   - Broker evidence ONLY: no filled_avg_price in the response → NO
        #     re-anchor. RESIDUAL: an order that goes terminal AFTER submit
        #     (bridge's _poll_for_terminal_fill learns the price at
        #     bridge.py:1565) keeps eval-anchored targets — that re-anchor
        #     belongs in bridge.py and is out of scope here.
        #   - Stops are NOT re-anchored: raising a stop's anchor after a
        #     chased fill would widen dollar risk (anti-conservative).
        if _fill_price > 0 and verdict.entry_price > 0:
            _anchor_ratio = _fill_price / verdict.entry_price
            if abs(_anchor_ratio - 1.0) > 1e-9:
                if verdict.target_prices:
                    _eval_targets = list(verdict.target_prices)
                    verdict.target_prices[:] = [
                        round(t * _anchor_ratio, 6) for t in verdict.target_prices
                    ]
                    logger.info(
                        "D286 TARGET RE-ANCHOR %s: fill $%.4f vs eval $%.4f "
                        "(%+.2f%%) — targets %s -> %s",
                        verdict.ticker, _fill_price, verdict.entry_price,
                        (_anchor_ratio - 1.0) * 100.0,
                        [f"{t:.4f}" for t in _eval_targets],
                        [f"{t:.4f}" for t in verdict.target_prices],
                    )
                take_profit_price = round(take_profit_price * _anchor_ratio, 6)

        return OrderResult(
            order_id=entry_order_id,
            status=response.get("status", "unknown"),
            ticker=verdict.ticker,
            qty=qty,
            side="sell" if _is_short else "buy",
            order_type="oto",  # OTO: entry triggers stop
            signal_price=verdict.entry_price,
            # Bug R fix (2026-04-23): expose the actual broker stop so
            # the bridge can sync ManagedPosition.stop_loss to broker
            # truth instead of carrying the un-tightened verdict value.
            actual_stop_price=_stop_price,
            submitted_price=verdict.entry_price,
            stop_loss=verdict.stop_loss,
            take_profit=take_profit_price,
            stop_order_id=stop_order_id,  # D64: expose for StopResubmitter
            fill_price=_fill_price,
        )

    # ── D310.T2 (2026-05-24, doc 171) — L1 standalone-stop helper ────
    async def _t2_arm_standalone_stop(
        self,
        *,
        entry_order_id: str,
        symbol: str,
        intended_qty: int,
        stop_price: float,
        poll_interval_sec: float = 2.0,
        poll_timeout_sec: float = 120.0,
    ) -> None:
        """Background task: poll for entry-leg fill, then submit a SEPARATE
        protective STOP order. Used by the T2 wide_stop arm to bypass
        ``TrailingStopManager`` (D310 cancel-without-resubmit bug).

        Defensive guarantees:
          - Never raises. Logs WARN on every failure path.
          - Bounded: 120s poll timeout (typical fill is <30s for liquid
            names). If not filled by then, no stop is submitted (the
            order was likely canceled / rejected anyway).
          - Layer 2 hedge_integrity_watcher backstops this with a 20s
            poll invariant check, so even if this helper completely
            fails the position becomes hedged within ~60s.
        """
        import asyncio as _aio
        if not entry_order_id:
            logger.warning(
                "D310.T2 %s: standalone-stop task has no entry_order_id; "
                "Layer 2 hedge_integrity_watcher will need to intervene",
                symbol,
            )
            return
        deadline = _aio.get_event_loop().time() + poll_timeout_sec
        filled_qty = 0
        # Poll loop
        while _aio.get_event_loop().time() < deadline:
            await _aio.sleep(poll_interval_sec)
            try:
                orders = await self._client.get_orders(
                    status="all", limit=10, symbols=symbol,
                )
            except Exception as _ge:
                logger.warning(
                    "D310.T2 %s: get_orders raised (%s); will retry next tick",
                    symbol, _ge,
                )
                continue
            order = None
            for o in orders or []:
                if o.get("id") == entry_order_id:
                    order = o
                    break
            if order is None:
                continue
            status = (order.get("status") or "").lower()
            filled_qty = int(float(order.get("filled_qty") or 0))
            if status in ("filled",) and filled_qty > 0:
                break
            if status in ("canceled", "expired", "rejected"):
                logger.info(
                    "D310.T2 %s: entry order %s terminated with status=%s "
                    "filled=%d -- no protective stop needed (no position)",
                    symbol, entry_order_id, status, filled_qty,
                )
                return
        if filled_qty <= 0:
            logger.warning(
                "D310.T2 %s: entry order %s did not fill within %.0fs "
                "(intended_qty=%d); Layer 2 watcher will backstop if "
                "broker still shows partial position",
                symbol, entry_order_id, poll_timeout_sec, intended_qty,
            )
            return

        # Submit the standalone STOP. GTC so it doesn't expire intraday.
        try:
            stop_resp = await self._client.submit_stop_order(
                symbol=symbol, qty=filled_qty, side="sell",
                stop_price=stop_price, time_in_force="gtc",
            )
            stop_oid = stop_resp.get("id", "")
            logger.info(
                "D310.T2 %s: STANDALONE STOP submitted oid=%s qty=%d "
                "stop_price=$%.4f time_in_force=gtc (bypasses "
                "TrailingStopManager)",
                symbol, stop_oid, filled_qty, stop_price,
            )
        except Exception as _se:
            logger.error(
                "D310.T2 %s: standalone STOP submission FAILED (%s) "
                "after entry fill -- position is UNHEDGED; Layer 2 "
                "hedge_integrity_watcher MUST intervene within 60s",
                symbol, _se,
            )
