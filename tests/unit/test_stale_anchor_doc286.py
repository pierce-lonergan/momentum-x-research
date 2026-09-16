"""doc 286 (doc-285 gap #9): the STALE-ANCHOR class, fixed conservatively.

WHY (2026-07-06 live session): sizing caps and tranche targets were computed
on the FROZEN eval price while the D190 marketable limit chased the live ask.
RIVN: eval $18.50, limit chased to ~$19.44 (+4.4%) —
  (1) the "15% cap" qty (1,552sh against $18.50) FILLED at 15.65% of equity;
  (2) T1 (eval*1.02) sat only 0.7% above the actual fill -> instant clip.

THE TWO FIXES (both strictly conservative — qty only shrinks, targets keep
their designed percentages off real cost):
  (1) CAP AT THE PRICE YOU MIGHT PAY: the qty cap denominator is
      max(eval price, marketable limit) at submission time.
  (2) TRANCHE TARGETS OFF THE FILL: when the broker CONFIRMS a fill price in
      the submit response (instant fill — the norm for marketable limits),
      target_prices are re-anchored in place to target_pct x fill.

DOCUMENTED RESIDUAL (fix 2): an order that goes terminal AFTER submit gets its
fill price from bridge.py's _poll_for_terminal_fill (bridge.py:1565), which is
outside the executor — those late fills keep eval-anchored targets. The
broker-evidence-only rule below (test_no_fill_evidence_no_reanchor) pins that
we NEVER re-anchor without a confirmed price.
"""

from __future__ import annotations

import math

import pytest
from unittest.mock import AsyncMock

from config.settings import ExecutionConfig
from src.core.models import TradeVerdict
from src.execution.alpaca_executor import AlpacaExecutor

# ── RIVN 7/6 reconstruction constants ─────────────────────────────
EQUITY = 191_532.0
EVAL = 18.50
ASK = 19.1499            # chosen so round(ask*1.015, 4) == 19.4371 (the doc-285 limit)
LIMIT = 19.4371          # D190 marketable limit at full conviction (mfcs 0.78 >= 0.55)
TIER_PCT = 0.05          # the corrected doc-285 tier cap
TARGETS = [18.87, 19.61, 20.35]  # eval * (1.02, 1.06, 1.10)


@pytest.fixture(autouse=True)
def _mute_vll(monkeypatch):
    """Never let a unit test append to the LIVE data/ops verdict trace."""
    monkeypatch.setattr(
        "src.ops.verdict_ledger.vll_emit", lambda *a, **k: None, raising=True
    )


def _cfg(**overrides) -> ExecutionConfig:
    """Env-immune config: every knob this path reads is passed explicitly
    (ExecutionConfig is BaseSettings — kwargs beat EXEC_* env overrides)."""
    kw = dict(
        paper_aggressive_mode=True,
        tier3_position_pct=TIER_PCT,
        max_positions=8,
        phase_stop_enabled=False,
        marketable_limit_enabled=True,
        marketable_base_offset_pct=0.004,
        marketable_max_offset_pct=0.015,
        marketable_mfcs_full_at=0.55,
    )
    kw.update(overrides)
    return ExecutionConfig(**kw)


def _verdict(**overrides) -> TradeVerdict:
    kw = dict(
        ticker="RIVN",
        action="BUY",
        confidence=0.85,
        mfcs=0.78,                      # >= full_at -> max 1.5% chase offset
        entry_price=EVAL,
        stop_loss=17.50,                # distance 1.0 -> qty_risk 1915 (cap binds)
        target_prices=list(TARGETS),
        position_size_pct=0.05,
        kelly_tier=1,
        risk_per_trade_pct=0.01,
        reasoning_summary="doc286 test",
    )
    kw.update(overrides)
    return TradeVerdict(**kw)


def _client(*, equity: float = EQUITY, ask: float = ASK, bid: float = 19.10,
            oto_response: dict | None = None) -> AsyncMock:
    c = AsyncMock()
    c.get_positions.return_value = []
    c.get_account.return_value = {"equity": str(equity)}
    c.get_snapshots.return_value = {"RIVN": {"ask": ask, "bid": bid}}
    c.submit_oto_order.return_value = oto_response or {
        "id": "oto-286",
        "status": "accepted",
        "symbol": "RIVN",
        "order_class": "oto",
        "legs": [
            {"id": "stop-286", "side": "sell", "type": "stop", "status": "held"},
        ],
    }
    c.submit_oto_short_order.return_value = {
        "id": "oto-286s",
        "status": "accepted",
        "symbol": "RIVN",
        "order_class": "oto",
        "legs": [
            {"id": "stop-286s", "side": "buy", "type": "stop", "status": "held"},
        ],
    }
    return c


# ═══ Fix 1: CAP AT THE PRICE YOU MIGHT PAY ═══════════════════════════


class TestWorstFillCap:
    @pytest.mark.asyncio
    async def test_cap_uses_max_of_eval_and_marketable_limit(self):
        """The RIVN numbers, to the share. OLD behavior: cap denominator was
        the frozen eval price -> floor(191532*0.05/18.50) = 517 shares, which
        the chased limit filled at 15.65% of a 15% cap. NEW: denominator is
        max(eval, limit) -> floor(191532*0.05/19.4371) = 492. FAILS pre-fix
        (the old code submits 517)."""
        client = _client()
        executor = AlpacaExecutor(config=_cfg(), client=client)
        result = await executor.execute(_verdict())

        assert result is not None
        kwargs = client.submit_oto_order.call_args.kwargs
        # The marketable limit itself is unchanged by the fix
        assert kwargs["limit_price"] == pytest.approx(LIMIT)
        # Cap re-anchored to the worst legal fill
        assert kwargs["qty"] == 492
        assert result.qty == 492
        # And explicitly NOT the eval-anchored cap
        assert kwargs["qty"] != math.floor(EQUITY * TIER_PCT / EVAL)  # != 517
        # Worst-case deployed capital now respects the cap
        assert kwargs["qty"] * LIMIT <= EQUITY * TIER_PCT

    @pytest.mark.asyncio
    async def test_risk_bound_sizing_untouched_by_recap(self):
        """When fixed-risk qty is already below the re-anchored cap, the
        recap must be a no-op (min semantics — never sizes UP)."""
        client = _client()
        executor = AlpacaExecutor(config=_cfg(), client=client)
        # Wide stop -> qty_risk = floor(191532*0.01/5.0) = 383 < 492
        result = await executor.execute(_verdict(stop_loss=13.50))

        assert result is not None
        assert client.submit_oto_order.call_args.kwargs["qty"] == 383

    @pytest.mark.asyncio
    async def test_short_with_limit_below_eval_unchanged(self):
        """Shorts cross DOWN: limit < eval -> max(eval, limit) keeps the eval
        denominator, so short sizing is bit-identical to pre-fix (the fix can
        only ever SHRINK qty, never grow it)."""
        client = _client(bid=18.40)
        executor = AlpacaExecutor(config=_cfg(), client=client)
        result = await executor.execute(_verdict(
            direction="short",
            stop_loss=19.50,                       # above entry for a short
            target_prices=[18.13, 17.76, 17.39],   # below entry
        ))

        assert result is not None
        kwargs = client.submit_oto_short_order.call_args.kwargs
        # sell limit crossed down below eval — recap no-op
        assert kwargs["limit_price"] < EVAL
        assert kwargs["qty"] == math.floor(EQUITY * TIER_PCT / EVAL)  # 517

    @pytest.mark.asyncio
    async def test_recap_to_zero_skips_entry_instead_of_breaching_cap(self):
        """If even ONE share breaches the cap at the worst fill, skip the
        entry (conservative: trade less, never more). OLD behavior submitted
        1 share (floor(19/18.50)=1). FAILS pre-fix."""
        client = _client(equity=380.0)  # 5% budget $19: 1sh at eval, 0sh at limit
        executor = AlpacaExecutor(config=_cfg(), client=client)
        result = await executor.execute(_verdict())

        assert result is None
        client.submit_oto_order.assert_not_called()


# ═══ Fix 2: TRANCHE TARGETS OFF THE FILL ═════════════════════════════


def _instant_fill_response(fill_price: float, qty: int = 492) -> dict:
    return {
        "id": "oto-286f",
        "status": "filled",
        "symbol": "RIVN",
        "order_class": "oto",
        "filled_qty": str(qty),
        "filled_avg_price": f"{fill_price}",
        "legs": [
            {"id": "stop-286f", "side": "sell", "type": "stop", "status": "held"},
        ],
    }


class TestTargetReanchorToFill:
    @pytest.mark.asyncio
    async def test_targets_reanchor_to_confirmed_fill_price(self):
        """RIVN geometry: eval $18.50, targets [+2%,+6%,+10%], fill chased to
        $18.74. OLD behavior left T1 at the eval-anchored $18.87 — only 0.7%
        above real cost -> the 4-minute clip. NEW: targets scale by fill/eval
        so T1 is again +2% off what we actually paid. FAILS pre-fix (targets
        stay eval-anchored)."""
        fill = 18.74
        client = _client(oto_response=_instant_fill_response(fill))
        executor = AlpacaExecutor(config=_cfg(), client=client)
        verdict = _verdict()
        targets_ref = verdict.target_prices   # the SAME list the bridge reads

        result = await executor.execute(verdict)

        assert result is not None
        assert result.fill_price == pytest.approx(fill)
        ratio = fill / EVAL
        expected = [round(t * ratio, 6) for t in TARGETS]
        # Re-anchored IN PLACE (bridge.py:1718 reads verdict.target_prices
        # after execute() returns — same object)
        assert verdict.target_prices == expected
        assert targets_ref == expected
        # T1 restored to its designed +2% distance from actual cost
        assert verdict.target_prices[0] / fill == pytest.approx(
            TARGETS[0] / EVAL
        )
        # OrderResult.take_profit follows the same anchor
        assert result.take_profit == pytest.approx(round(TARGETS[0] * ratio, 6))
        # Stop is NOT re-anchored (that would widen dollar risk)
        assert result.stop_loss == 17.50

    @pytest.mark.asyncio
    async def test_no_fill_evidence_no_reanchor(self):
        """Broker-evidence-only rule (the doc-285 phantom-close lesson): a
        pending order has NO filled_avg_price -> targets must stay EXACTLY
        eval-anchored. This also documents the residual: late fills are
        priced by bridge._poll_for_terminal_fill (bridge.py:1565), outside
        the executor, and are NOT re-anchored by this fix."""
        client = _client()  # default response: accepted, no filled_avg_price
        executor = AlpacaExecutor(config=_cfg(), client=client)
        verdict = _verdict()

        result = await executor.execute(verdict)

        assert result is not None
        assert result.fill_price == 0.0
        assert verdict.target_prices == TARGETS
        assert result.take_profit == pytest.approx(TARGETS[0])

    @pytest.mark.asyncio
    async def test_fill_below_eval_scales_targets_down(self):
        """A better-than-eval fill re-anchors DOWN: profit is taken at the
        designed percentage off real (lower) cost — sooner, not later.
        Conservative in both directions."""
        fill = 18.13   # limit orders may fill better than the eval price
        client = _client(oto_response=_instant_fill_response(fill))
        executor = AlpacaExecutor(config=_cfg(), client=client)
        verdict = _verdict()

        result = await executor.execute(verdict)

        assert result is not None
        ratio = fill / EVAL
        assert verdict.target_prices == [round(t * ratio, 6) for t in TARGETS]
        assert all(new < old for new, old in zip(verdict.target_prices, TARGETS))
