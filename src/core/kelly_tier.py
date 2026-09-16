"""
D115: Tiered Kelly Criterion Position Sizing

Classifies each trade candidate into one of four conviction tiers (1-4),
each with its own risk_per_trade_pct and max_position_pct. Higher tiers
require increasingly strict confluence of signals.

This module changes SIZING MATH, not TRADING SIGNALS — freeze-safe.

Ref: docs/PAPER_TRADING_LOG.md Day 1 learnings
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum

from config.settings import KellyTierConfig

logger = logging.getLogger(__name__)


class KellyTier(IntEnum):
    STANDARD = 1
    HIGH_CONVICTION = 2
    EXCEPTIONAL = 3
    STATISTICAL_OUTLIER = 4



@dataclass(frozen=True)
class KellyTierResult:
    """Classification result from the Kelly tier system."""

    tier: KellyTier
    risk_per_trade_pct: float
    max_position_pct: float
    reason: str
    checks_passed: tuple[str, ...] = ()
    checks_failed: tuple[str, ...] = ()

    def to_log_dict(self) -> dict:
        return {
            "kelly_tier": int(self.tier),
            "kelly_tier_name": self.tier.name,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_position_pct": self.max_position_pct,
            "reason": self.reason,
            "checks_passed": list(self.checks_passed),
            "checks_failed": list(self.checks_failed),
        }


class KellyTierClassifier:
    """
    Classifies trade candidates into conviction tiers 1-4.

    Evaluation is top-down: check Tier 4 first, fall through to 3, 2,
    then default to 1. Each tier's checks are a conjunction (ALL must pass).
    """

    def __init__(
        self,
        config: KellyTierConfig,
        trade_tracker: object | None = None,  # TradeResultTracker
        daily_loss_limit_pct: float = 0.10,
    ) -> None:
        self._config = config
        self._tracker = trade_tracker
        self._daily_loss_limit_pct = daily_loss_limit_pct

    def classify(
        self,
        *,
        mfcs: float,
        rvol: float,
        gap_pct: float,
        stop_loss_pct: float,
        catalyst_type: str,
        catalyst_specificity: str,
        float_shares: int | None,
        sector: str,
        directional_agent_count: int,
        vix_level: float | None,
        spy_return_pct: float | None,
        daily_realized_pnl: float,
        daily_unrealized_pnl: float = 0.0,
        open_positions: list | None = None,
        portfolio_heat_pct: float = 0.0,
        max_open_exit_urgency: float = 0.0,
        minutes_since_open: float = 0.0,
        current_price: float = 0.0,
    ) -> KellyTierResult:
        """
        Classify a candidate into a Kelly tier.

        Returns KellyTierResult with the tier, risk params, and audit trail.
        """
        t0 = time.perf_counter()
        open_positions = open_positions or []
        cfg = self._config

        # Compute reward/risk ratio
        if stop_loss_pct <= 0:
            logger.warning(
                "D115: stop_loss_pct=%.4f for classification — "
                "reward/risk defaults to 0.0 (tier will be capped at 1)",
                stop_loss_pct,
            )
        reward_risk = abs(gap_pct) / stop_loss_pct if stop_loss_pct > 0 else 0.0

        # Count same-sector open positions
        same_sector_open = any(
            getattr(p, "sector", "") == sector
            for p in open_positions
            if sector
        )

        # Get win rates from tracker
        win_rate = None
        tier3_plus_today = 0
        had_tier3_stop = False
        if self._tracker is not None:
            win_rate = self._tracker.win_rate(n=30)
            tier3_plus_today = self._tracker.tier3_plus_count_today()
            had_tier3_stop = self._tracker.had_tier3_stop_today()

        # Count Tier 3+ currently-open positions (for Tier 4 concurrent check)
        tier3_plus_open = sum(
            1 for p in open_positions
            if getattr(p, "kelly_tier", 1) >= 3
        )

        # ── Safety guardrails (apply to Tier 3+) ──
        tier3_plus_blocked = False
        tier3_block_reason = ""
        if tier3_plus_today >= cfg.max_tier3_plus_per_day:
            tier3_plus_blocked = True
            tier3_block_reason = f"max_tier3_plus_per_day={cfg.max_tier3_plus_per_day} reached"
        elif cfg.sequential_lockout_after_tier3_stop and had_tier3_stop:
            tier3_plus_blocked = True
            tier3_block_reason = "sequential_lockout: Tier 3+ stop-loss today"

        # Note: Budget downgrade (downgrade_when_budget_exceeded) is handled at the
        # executor layer where actual equity is available. The classifier only checks
        # signal-based criteria; the executor caps based on real account equity.

        # ── Quality guardrails — block tier upgrades for low-quality stocks ──
        # Replay analysis (Mar 19-20): MOBX ($0.53) got Tier 2 sizing on MFCS=0.256,
        # scaling to 26k shares on a stock with 1.8% MFE → lost $1,219.
        tier_upgrade_blocked = False
        tier_upgrade_block_reason = ""
        if current_price > 0 and current_price < cfg.min_price_for_tier_upgrade:
            tier_upgrade_blocked = True
            tier_upgrade_block_reason = (
                f"price=${current_price:.2f} < min ${cfg.min_price_for_tier_upgrade:.2f}"
            )
        elif abs(gap_pct) < cfg.min_gap_pct_for_tier_upgrade:
            tier_upgrade_blocked = True
            tier_upgrade_block_reason = (
                f"gap={gap_pct:.1%} < min {cfg.min_gap_pct_for_tier_upgrade:.1%}"
            )
        if tier_upgrade_blocked:
            logger.info(
                "D115: Tier upgrade blocked — %s. Staying at Tier 1.",
                tier_upgrade_block_reason,
            )
            return self._result(
                KellyTier.STANDARD,
                (),
                (tier_upgrade_block_reason,),
                t0,
            )

        # ── Try Tier 4 ──
        if not tier3_plus_blocked:
            passed_4, failed_4 = self._check_tier4(
                mfcs=mfcs,
                rvol=rvol,
                gap_pct=gap_pct,
                reward_risk=reward_risk,
                catalyst_type=catalyst_type,
                catalyst_specificity=catalyst_specificity,
                float_shares=float_shares,
                directional_agent_count=directional_agent_count,
                vix_level=vix_level,
                spy_return_pct=spy_return_pct,
                daily_realized_pnl=daily_realized_pnl,
                daily_unrealized_pnl=daily_unrealized_pnl,
                same_sector_open=same_sector_open,
                win_rate=win_rate,
                minutes_since_open=minutes_since_open,
                portfolio_heat_pct=portfolio_heat_pct,
                max_open_exit_urgency=max_open_exit_urgency,
                tier3_plus_open=tier3_plus_open,
            )
            if not failed_4:
                return self._result(
                    KellyTier.STATISTICAL_OUTLIER, passed_4, (), t0,
                )

        # ── Try Tier 3 ──
        if not tier3_plus_blocked:
            passed_3, failed_3 = self._check_tier3(
                mfcs=mfcs,
                rvol=rvol,
                gap_pct=gap_pct,
                reward_risk=reward_risk,
                catalyst_type=catalyst_type,
                float_shares=float_shares,
                directional_agent_count=directional_agent_count,
                vix_level=vix_level,
                spy_return_pct=spy_return_pct,
                daily_realized_pnl=daily_realized_pnl,
                same_sector_open=same_sector_open,
                win_rate=win_rate,
                minutes_since_open=minutes_since_open,
                max_open_exit_urgency=max_open_exit_urgency,
            )
            if not failed_3:
                return self._result(
                    KellyTier.EXCEPTIONAL, passed_3, (), t0,
                )

        # ── Try Tier 2 ──
        passed_2, failed_2 = self._check_tier2(
            mfcs=mfcs,
            rvol=rvol,
            reward_risk=reward_risk,
            catalyst_type=catalyst_type,
            directional_agent_count=directional_agent_count,
            same_sector_open=same_sector_open,
            win_rate=win_rate,
            minutes_since_open=minutes_since_open,
        )
        if not failed_2:
            return self._result(
                KellyTier.HIGH_CONVICTION, passed_2, (), t0,
            )

        # ── Default: Tier 1 ──
        all_failed = list(failed_2)
        if tier3_plus_blocked:
            all_failed.append(f"tier3+_blocked: {tier3_block_reason}")
        return self._result(
            KellyTier.STANDARD, (), tuple(all_failed), t0,
        )

    def _check_tier2(
        self, *, mfcs, rvol, reward_risk, catalyst_type,
        directional_agent_count, same_sector_open, win_rate,
        minutes_since_open,
    ) -> tuple[list[str], list[str]]:
        """Check Tier 2 requirements. Returns (passed, failed) lists."""
        cfg = self._config
        passed, failed = [], []

        # MFCS check
        if mfcs >= cfg.tier2_min_mfcs:
            passed.append(f"mfcs={mfcs:.3f}>={cfg.tier2_min_mfcs}")
        else:
            failed.append(f"mfcs={mfcs:.3f}<{cfg.tier2_min_mfcs}")

        # Directional agents
        if directional_agent_count >= cfg.tier2_min_directional_agents:
            passed.append(f"directional={directional_agent_count}>={cfg.tier2_min_directional_agents}")
        else:
            failed.append(f"directional={directional_agent_count}<{cfg.tier2_min_directional_agents}")

        # Catalyst type
        cat_upper = catalyst_type.upper() if catalyst_type else ""
        if cat_upper in cfg.tier2_proven_catalysts:
            passed.append(f"catalyst={cat_upper}")
        else:
            failed.append(f"catalyst={cat_upper} not in proven list")

        # RVOL
        if rvol >= cfg.tier2_min_rvol:
            passed.append(f"rvol={rvol:.1f}x>={cfg.tier2_min_rvol}x")
        else:
            failed.append(f"rvol={rvol:.1f}x<{cfg.tier2_min_rvol}x")

        # Reward/risk
        if reward_risk >= cfg.tier2_min_reward_risk:
            passed.append(f"reward_risk={reward_risk:.2f}>={cfg.tier2_min_reward_risk}")
        else:
            failed.append(f"reward_risk={reward_risk:.2f}<{cfg.tier2_min_reward_risk}")

        # Same sector
        if cfg.tier2_require_no_same_sector and same_sector_open:
            failed.append("same_sector_open")
        else:
            passed.append("no_same_sector")

        # Time window
        if minutes_since_open <= cfg.tier2_first_n_minutes:
            passed.append(f"minutes={minutes_since_open:.0f}<={cfg.tier2_first_n_minutes}")
        else:
            failed.append(f"minutes={minutes_since_open:.0f}>{cfg.tier2_first_n_minutes}")

        # Win rate
        if win_rate is not None and win_rate >= cfg.tier2_min_win_rate:
            passed.append(f"win_rate={win_rate:.1%}>={cfg.tier2_min_win_rate:.0%}")
        else:
            wr_str = f"{win_rate:.1%}" if win_rate is not None else "None(<30 trades)"
            failed.append(f"win_rate={wr_str}")

        return passed, failed

    def _check_tier3(
        self, *, mfcs, rvol, gap_pct, reward_risk, catalyst_type,
        float_shares, directional_agent_count, vix_level, spy_return_pct,
        daily_realized_pnl, same_sector_open, win_rate, minutes_since_open,
        max_open_exit_urgency,
    ) -> tuple[list[str], list[str]]:
        """Check Tier 3 requirements (includes all Tier 2 checks)."""
        cfg = self._config

        # Start with Tier 2 checks
        passed, failed = self._check_tier2(
            mfcs=mfcs, rvol=rvol, reward_risk=reward_risk,
            catalyst_type=catalyst_type,
            directional_agent_count=directional_agent_count,
            same_sector_open=same_sector_open, win_rate=win_rate,
            minutes_since_open=minutes_since_open,
        )

        # Override MFCS check with Tier 3 threshold
        passed = [p for p in passed if not p.startswith("mfcs=")]
        failed = [f for f in failed if not f.startswith("mfcs=")]
        if mfcs >= cfg.tier3_min_mfcs:
            passed.append(f"mfcs={mfcs:.3f}>={cfg.tier3_min_mfcs}")
        else:
            failed.append(f"mfcs={mfcs:.3f}<{cfg.tier3_min_mfcs}")

        # Override catalyst check with Tier 3 restricted list
        passed = [p for p in passed if not p.startswith("catalyst=")]
        failed = [f for f in failed if not f.startswith("catalyst=")]
        cat_upper = catalyst_type.upper() if catalyst_type else ""
        if cat_upper in cfg.tier3_catalysts:
            passed.append(f"catalyst={cat_upper}")
        else:
            failed.append(f"catalyst={cat_upper} not in tier3 list")

        # Gap sweet spot
        if cfg.tier3_min_gap_pct <= abs(gap_pct) <= cfg.tier3_max_gap_pct:
            passed.append(f"gap={abs(gap_pct):.1%} in [{cfg.tier3_min_gap_pct:.0%},{cfg.tier3_max_gap_pct:.0%}]")
        else:
            failed.append(f"gap={abs(gap_pct):.1%} outside [{cfg.tier3_min_gap_pct:.0%},{cfg.tier3_max_gap_pct:.0%}]")

        # Float
        if float_shares is not None and float_shares <= cfg.tier3_max_float:
            passed.append(f"float={float_shares:,}<={cfg.tier3_max_float:,}")
        else:
            fs = f"{float_shares:,}" if float_shares is not None else "None"
            failed.append(f"float={fs}>{cfg.tier3_max_float:,}")

        # VIX — missing data fails Tier 3+ (safety-critical guardrail)
        if vix_level is not None and vix_level < cfg.tier3_max_vix:
            passed.append(f"vix={vix_level:.1f}<{cfg.tier3_max_vix}")
        elif vix_level is None:
            failed.append("vix=None(missing_data)")
        else:
            failed.append(f"vix={vix_level:.1f}>={cfg.tier3_max_vix}")

        # SPY not down — missing data fails Tier 3+ (safety-critical guardrail)
        if cfg.tier3_require_spy_not_down:
            if spy_return_pct is None:
                failed.append("spy=None(missing_data)")
            elif spy_return_pct < 0:
                failed.append(f"spy_down={spy_return_pct:.2%}")
            else:
                passed.append("spy_not_down")

        # Positive daily P&L
        if cfg.tier3_require_positive_daily_pnl:
            if daily_realized_pnl >= 0:
                passed.append(f"daily_pnl=${daily_realized_pnl:.0f}>=0")
            else:
                failed.append(f"daily_pnl=${daily_realized_pnl:.0f}<0")

        # Exit urgency
        if max_open_exit_urgency <= cfg.tier3_max_open_exit_urgency:
            passed.append(f"exit_urgency={max_open_exit_urgency:.2f}<={cfg.tier3_max_open_exit_urgency}")
        else:
            failed.append(f"exit_urgency={max_open_exit_urgency:.2f}>{cfg.tier3_max_open_exit_urgency}")

        return passed, failed

    def _check_tier4(
        self, *, mfcs, rvol, gap_pct, reward_risk, catalyst_type,
        catalyst_specificity, float_shares, directional_agent_count,
        vix_level, spy_return_pct, daily_realized_pnl,
        daily_unrealized_pnl, same_sector_open, win_rate,
        minutes_since_open, portfolio_heat_pct, max_open_exit_urgency,
        tier3_plus_open: int = 0,
    ) -> tuple[list[str], list[str]]:
        """Check Tier 4 requirements (includes all Tier 3 checks)."""
        cfg = self._config

        # Start with Tier 3 checks
        passed, failed = self._check_tier3(
            mfcs=mfcs, rvol=rvol, gap_pct=gap_pct, reward_risk=reward_risk,
            catalyst_type=catalyst_type, float_shares=float_shares,
            directional_agent_count=directional_agent_count,
            vix_level=vix_level, spy_return_pct=spy_return_pct,
            daily_realized_pnl=daily_realized_pnl,
            same_sector_open=same_sector_open, win_rate=win_rate,
            minutes_since_open=minutes_since_open,
            max_open_exit_urgency=max_open_exit_urgency,
        )

        # Override MFCS with Tier 4 threshold
        passed = [p for p in passed if not p.startswith("mfcs=")]
        failed = [f for f in failed if not f.startswith("mfcs=")]
        if mfcs >= cfg.tier4_min_mfcs:
            passed.append(f"mfcs={mfcs:.3f}>={cfg.tier4_min_mfcs}")
        else:
            failed.append(f"mfcs={mfcs:.3f}<{cfg.tier4_min_mfcs}")

        # Override reward/risk with Tier 4 threshold
        passed = [p for p in passed if not p.startswith("reward_risk=")]
        failed = [f for f in failed if not f.startswith("reward_risk=")]
        if reward_risk >= cfg.tier4_min_reward_risk:
            passed.append(f"reward_risk={reward_risk:.2f}>={cfg.tier4_min_reward_risk}")
        else:
            failed.append(f"reward_risk={reward_risk:.2f}<{cfg.tier4_min_reward_risk}")

        # Override win rate with Tier 4 threshold
        passed = [p for p in passed if not p.startswith("win_rate=")]
        failed = [f for f in failed if not f.startswith("win_rate=")]
        if win_rate is not None and win_rate >= cfg.tier4_min_win_rate:
            passed.append(f"win_rate={win_rate:.1%}>={cfg.tier4_min_win_rate:.0%}")
        else:
            wr_str = f"{win_rate:.1%}" if win_rate is not None else "None"
            failed.append(f"win_rate={wr_str}<{cfg.tier4_min_win_rate:.0%}")

        # Confirmed catalyst
        if cfg.tier4_require_confirmed_catalyst:
            spec_upper = (catalyst_specificity or "").upper()
            if spec_upper == "CONFIRMED":
                passed.append("catalyst_confirmed")
            else:
                failed.append(f"catalyst_specificity={spec_upper}!=CONFIRMED")

        # Positive unrealized P&L
        if cfg.tier4_require_positive_unrealized:
            if daily_unrealized_pnl >= 0:
                passed.append(f"unrealized_pnl=${daily_unrealized_pnl:.0f}>=0")
            else:
                failed.append(f"unrealized_pnl=${daily_unrealized_pnl:.0f}<0")

        # Portfolio heat
        if portfolio_heat_pct <= cfg.tier4_max_portfolio_heat_pct:
            passed.append(f"heat={portfolio_heat_pct:.1%}<={cfg.tier4_max_portfolio_heat_pct:.0%}")
        else:
            failed.append(f"heat={portfolio_heat_pct:.1%}>{cfg.tier4_max_portfolio_heat_pct:.0%}")

        # No other Tier 3+ trade currently open
        if tier3_plus_open <= cfg.tier4_max_concurrent_tier3_plus:
            passed.append(f"tier3+_open={tier3_plus_open}<={cfg.tier4_max_concurrent_tier3_plus}")
        else:
            failed.append(f"tier3+_open={tier3_plus_open}>{cfg.tier4_max_concurrent_tier3_plus}")

        return passed, failed

    def _result(
        self,
        tier: KellyTier,
        passed: tuple | list,
        failed: tuple | list,
        t0: float,
    ) -> KellyTierResult:
        """Build result with risk params from config."""
        cfg = self._config
        tier_params = {
            KellyTier.STANDARD: (cfg.tier1_risk_pct, cfg.tier1_max_position_pct),
            KellyTier.HIGH_CONVICTION: (cfg.tier2_risk_pct, cfg.tier2_max_position_pct),
            KellyTier.EXCEPTIONAL: (cfg.tier3_risk_pct, cfg.tier3_max_position_pct),
            KellyTier.STATISTICAL_OUTLIER: (cfg.tier4_risk_pct, cfg.tier4_max_position_pct),
        }
        risk_pct, max_pos = tier_params[tier]

        latency_ms = (time.perf_counter() - t0) * 1000
        reason = f"Tier {int(tier)} ({tier.name})"
        if failed:
            reason += f" — failed: {', '.join(str(f) for f in failed[:3])}"

        logger.info(
            "D115 KELLY %s: tier=%d (%s) risk=%.1f%% max_pos=%.0f%% [%.1fms]",
            tier.name, int(tier), reason, risk_pct * 100, max_pos * 100, latency_ms,
        )

        return KellyTierResult(
            tier=tier,
            risk_per_trade_pct=risk_pct,
            max_position_pct=max_pos,
            reason=reason,
            checks_passed=tuple(passed),
            checks_failed=tuple(failed),
        )
