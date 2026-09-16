"""
D119: Strategy Simulation Arena — Core Engine

Two-layer replay engine (entry re-evaluation + exit replay) with Elo-based
ranking of strategy profiles against historical paper trading sessions.

Fidelity limitation: The entry layer re-weights stored agent VERDICTS
(BULL/BEAR/NEUTRAL + confidence), not raw features. It answers "among trades
we evaluated, which param combination extracts the most P&L?" — NOT "are
there profitable trades we're missing?" (that requires scripts/backtest.py).
"""

from __future__ import annotations

import json
import logging
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── Project root for imports ──
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.agents.prompt_arena import compute_expected_score, update_elo
from src.core.models import AgentSignal, CandidateStock

logger = logging.getLogger(__name__)

# Import replay_optimizer components — these are the exit-layer engine
# We import lazily in functions to avoid top-level side effects (dotenv, etc.)
_replay_mod = None


def _get_replay_mod():
    """Lazy import of scripts.replay_optimizer to avoid dotenv side effects."""
    global _replay_mod
    if _replay_mod is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "replay_optimizer",
            _PROJECT_ROOT / "scripts" / "replay_optimizer.py",
        )
        mod = importlib.util.module_from_spec(spec)
        # Register in sys.modules BEFORE exec so dataclass can resolve __module__
        sys.modules["replay_optimizer"] = mod
        spec.loader.exec_module(mod)
        _replay_mod = mod
    return _replay_mod


# ═══════════════════════════════════════════════════════════════════
# Strategy Profile
# ═══════════════════════════════════════════════════════════════════

@dataclass
class StrategyProfile:
    """Complete strategy configuration for arena simulation.

    Uses None for unset fields — production defaults are inherited.
    Entry-layer params (agent_weights, thresholds) control WHICH trades
    are taken. Exit-layer params (stops, targets, trailing) control
    HOW those trades are managed.
    """
    name: str
    description: str = ""

    # ── Entry/Scoring (Layer 1) ──
    agent_weights: dict[str, float] | None = None
    mfcs_buy_threshold: float | None = None
    risk_aversion_lambda: float | None = None
    confidence_deflation: float | None = None
    min_directional_agents: int | None = None

    # ── Exit/Execution (Layer 2) — maps to SimConfig ──
    equity: float | None = None
    risk_per_trade_pct: float | None = None
    max_position_pct: float | None = None
    kelly_enabled: bool | None = None
    tier2_min_mfcs: float | None = None
    tier2_risk_pct: float | None = None
    tier2_max_position_pct: float | None = None
    tier3_min_mfcs: float | None = None
    tier3_risk_pct: float | None = None
    tier3_max_position_pct: float | None = None
    stop_loss_pct: float | None = None
    atr_multiplier: float | None = None
    atr_floor_pct: float | None = None
    atr_cap_pct: float | None = None
    tranche_targets: tuple[float, ...] | None = None
    exit_threshold: float | None = None
    tighten_threshold: float | None = None
    trailing_activation_pct: float | None = None
    trailing_trail_distance_pct: float | None = None  # D120: separate trail width
    chandelier_multiplier: float | None = None
    tranche_ratchet_ratio: float | None = None  # D120: stop ratchet after tranche fill
    exit_signal_weight_overrides: dict[str, float] | None = None  # D120: partial override
    mfcs_scaling_denom: float | None = None  # D120: position sizing responsiveness
    runner_pct: float | None = None
    runner_trail_pct: float | None = None
    eod_close_hour_et: int | None = None
    eod_close_minute_et: int | None = None

    # ── Exit Strategy Params (D110) ──
    gratitude_initial_r: float | None = None
    gratitude_decay_per_min: float | None = None
    gratitude_floor_r: float | None = None

    @property
    def has_entry_overrides(self) -> bool:
        return any(v is not None for v in [
            self.agent_weights, self.mfcs_buy_threshold,
            self.risk_aversion_lambda, self.min_directional_agents,
        ])

    def to_sim_config(self):
        """Build a SimConfig with this profile's overrides applied to defaults."""
        mod = _get_replay_mod()
        # Start from the "current" profile defaults
        defaults = mod.PROFILES["current"]
        kwargs: dict[str, Any] = {"name": self.name}

        # Map profile fields → SimConfig fields
        _FIELD_MAP = {
            "equity": "equity",
            "risk_per_trade_pct": "risk_per_trade_pct",
            "max_position_pct": "max_position_pct",
            "kelly_enabled": "kelly_enabled",
            "tier2_min_mfcs": "tier2_min_mfcs",
            "tier2_risk_pct": "tier2_risk_pct",
            "tier2_max_position_pct": "tier2_max_position_pct",
            "tier3_min_mfcs": "tier3_min_mfcs",
            "tier3_risk_pct": "tier3_risk_pct",
            "tier3_max_position_pct": "tier3_max_position_pct",
            "stop_loss_pct": "stop_loss_pct",
            "atr_multiplier": "atr_multiplier",
            "atr_floor_pct": "atr_floor_pct",
            "atr_cap_pct": "atr_cap_pct",
            "tranche_targets": "tranche_targets",
            "exit_threshold": "exit_threshold",
            "tighten_threshold": "tighten_threshold",
            "trailing_activation_pct": "trailing_activation_pct",
            "trailing_trail_distance_pct": "trailing_trail_distance_pct",
            "chandelier_multiplier": "chandelier_multiplier",
            "tranche_ratchet_ratio": "tranche_ratchet_ratio",
            "exit_signal_weight_overrides": "exit_signal_weight_overrides",
            "mfcs_scaling_denom": "mfcs_scaling_denom",
            "runner_pct": "runner_pct",
            "runner_trail_pct": "runner_trail_pct",
            "eod_close_hour_et": "eod_close_hour_et",
            "eod_close_minute_et": "eod_close_minute_et",
        }

        for profile_field, sim_field in _FIELD_MAP.items():
            val = getattr(self, profile_field)
            if val is not None:
                kwargs[sim_field] = val
            else:
                kwargs[sim_field] = getattr(defaults, sim_field)

        return mod.SimConfig(**kwargs)

    def to_dict(self) -> dict:
        d = {"name": self.name, "description": self.description}
        for f in self.__dataclass_fields__:
            if f in ("name", "description"):
                continue
            v = getattr(self, f)
            if v is not None:
                d[f] = v
        return d


# ═══════════════════════════════════════════════════════════════════
# Data Loading
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ArenaSession:
    """All data needed to replay one historical trading session."""
    date: str
    journal_path: Path
    raw_entries: list[dict]
    trade_setups: list  # list[TradeSetup] from replay_optimizer
    bars: dict[str, list]  # ticker -> list[Bar]
    tickers_with_bars: set[str] = field(default_factory=set)


class ArenaDataLoader:
    """Loads historical sessions for arena simulation (cache-only, no API)."""

    def __init__(self, project_root: Path | None = None):
        self._root = project_root or _PROJECT_ROOT
        self._journals_dir = self._root / "data" / "journals"
        self._bars_dir = self._root / "data" / "bars"

    def discover_sessions(self) -> list[str]:
        """Find dates that have both journal AND at least one bars file."""
        if not self._journals_dir.exists():
            return []

        # Get all journal dates
        journal_dates: set[str] = set()
        for f in self._journals_dir.glob("journal_*.jsonl"):
            # journal_2026-03-20_143000.jsonl → "2026-03-20"
            parts = f.stem.split("_", 2)
            if len(parts) >= 2:
                journal_dates.add(parts[1])

        # Get all bar dates
        bar_dates: set[str] = set()
        if self._bars_dir.exists():
            for f in self._bars_dir.glob("bars_*_*.json"):
                # bars_ANNA_2026-03-20.json → "2026-03-20"
                parts = f.stem.rsplit("_", 1)
                if len(parts) == 2:
                    bar_dates.add(parts[1])

        # Intersection: dates with both
        valid = sorted(journal_dates & bar_dates)
        return valid

    def load_session(self, date: str) -> ArenaSession | None:
        """Load journal entries + cached bars for a single session."""
        mod = _get_replay_mod()

        # Find journal
        journal_path = None
        for f in sorted(self._journals_dir.glob(f"journal_{date}*.jsonl")):
            journal_path = f
            break
        if not journal_path:
            return None

        # Load raw entries
        raw_entries: list[dict] = []
        with open(journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        raw_entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        # Load trade setups (BUY verdicts)
        trade_setups = mod._load_trade_setups(journal_path)

        # Load cached bars (no API calls)
        bars: dict[str, list] = {}
        tickers_with_bars: set[str] = set()

        # Get unique tickers from setups
        tickers_needed = {s.ticker for s in trade_setups}
        for ticker in tickers_needed:
            cache_file = self._bars_dir / f"bars_{ticker}_{date}.json"
            if cache_file.exists():
                try:
                    with open(cache_file, "r") as bf:
                        raw_bars = json.load(bf)
                    parsed = mod._parse_bars(raw_bars)
                    if parsed:
                        bars[ticker] = parsed
                        tickers_with_bars.add(ticker)
                except Exception:
                    logger.warning("Failed to load bars for %s on %s", ticker, date)
            else:
                logger.debug("No cached bars for %s on %s — skipping", ticker, date)

        if not trade_setups:
            return None

        return ArenaSession(
            date=date,
            journal_path=journal_path,
            raw_entries=raw_entries,
            trade_setups=trade_setups,
            bars=bars,
            tickers_with_bars=tickers_with_bars,
        )


# ═══════════════════════════════════════════════════════════════════
# Entry Re-evaluation (Layer 1)
# ═══════════════════════════════════════════════════════════════════

def recompute_entry(
    raw_entry: dict,
    profile: StrategyProfile,
) -> tuple[bool, float, int]:
    """Re-evaluate entry decision with profile's scoring params.

    Reconstructs AgentSignal objects from stored journal data,
    computes a simplified MFCS using the profile's custom weights
    and risk_aversion_lambda.

    Returns: (would_enter, recomputed_mfcs, directional_count)
    """
    from src.core.scoring import signal_to_score

    agent_signals_raw = raw_entry.get("agent_signals", [])
    if not agent_signals_raw:
        return False, 0.0, 0

    # Use profile weights or production defaults
    weights = profile.agent_weights or {
        "catalyst_news": 0.35,
        "technical": 0.30,
        "volume_rvol": 0.20,
        "float_structure": 0.15,
        "institutional": 0.00,
        "deep_search": 0.00,
    }
    risk_lambda = profile.risk_aversion_lambda if profile.risk_aversion_lambda is not None else 0.15
    buy_threshold = profile.mfcs_buy_threshold if profile.mfcs_buy_threshold is not None else 0.25
    min_directional = profile.min_directional_agents if profile.min_directional_agents is not None else 2

    # Reconstruct signals and compute MFCS
    weighted_sum = 0.0
    risk_score = 0.0
    directional_count = 0

    # Map agent_id to weight key
    _AGENT_TO_WEIGHT = {
        "news_agent": "catalyst_news",
        "catalyst_news": "catalyst_news",
        "technical_agent": "technical",
        "technical": "technical",
        "fundamental_agent": "float_structure",
        "float_structure": "float_structure",
        "volume_agent": "volume_rvol",
        "volume_rvol": "volume_rvol",
        "institutional_agent": "institutional",
        "institutional": "institutional",
        "deep_search_agent": "deep_search",
        "deep_search": "deep_search",
    }

    _SIGNAL_NUMERIC = {
        "STRONG_BULL": 1.0,
        "BULL": 0.5,
        "NEUTRAL": 0.0,
        "BEAR": -0.5,
        "STRONG_BEAR": -1.0,
    }

    for sig_data in agent_signals_raw:
        agent_id = sig_data.get("agent_id", "")
        signal_str = sig_data.get("signal", "NEUTRAL")
        confidence = float(sig_data.get("confidence", 0.5))

        # Risk agent → penalty
        if "risk" in agent_id.lower():
            risk_score = float(sig_data.get("risk_score", 0) or 0)
            continue

        # Map to weight key
        weight_key = _AGENT_TO_WEIGHT.get(agent_id)
        if not weight_key:
            continue

        w = weights.get(weight_key, 0.0)
        if w == 0:
            continue

        # Compute numeric score: signal_value * confidence (with D116 floor)
        base_score = _SIGNAL_NUMERIC.get(signal_str, 0.0)
        if signal_str != "NEUTRAL" and confidence < 0.20:
            confidence = 0.20  # D116 confidence floor
        score = base_score * confidence
        weighted_sum += w * score

        # Count bullish directional agents (D121 BUG: was counting BEAR too,
        # letting stocks with 2 BEAR + 0 BULL pass min_directional_agents gate)
        if signal_str in ("BULL", "STRONG_BULL"):
            directional_count += 1

    mfcs = weighted_sum - risk_lambda * risk_score

    # Entry decision
    would_enter = mfcs > buy_threshold and directional_count >= min_directional
    return would_enter, round(mfcs, 4), directional_count


# ═══════════════════════════════════════════════════════════════════
# Profile Session Simulation
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ProfileSessionResult:
    """Result of simulating one profile against one historical session."""
    profile_name: str
    session_date: str
    trade_results: list  # list[SimResult]
    entries_accepted: int = 0
    entries_rejected: int = 0
    total_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0

    @property
    def win_rate(self) -> float:
        total = self.win_count + self.loss_count
        return self.win_count / total if total > 0 else 0.0


def simulate_profile_session(
    session: ArenaSession,
    profile: StrategyProfile,
) -> ProfileSessionResult:
    """Run one profile against one historical session.

    Layer 1 (entry): If profile has entry overrides, re-evaluate entry
    decisions with custom weights/thresholds. Otherwise accept all BUY entries.

    Layer 2 (exit): Replay each accepted trade through 1-min bars using
    simulate_trade() from replay_optimizer with the profile's SimConfig.
    """
    mod = _get_replay_mod()
    sim_config = profile.to_sim_config()

    result = ProfileSessionResult(
        profile_name=profile.name,
        session_date=session.date,
        trade_results=[],
    )

    # Build entry filter from raw journal entries (Layer 1)
    entry_decisions: dict[str, tuple[bool, float]] = {}  # ticker -> (would_enter, mfcs)
    if profile.has_entry_overrides:
        for raw_entry in session.raw_entries:
            if raw_entry.get("action") != "BUY":
                continue
            ticker = raw_entry.get("ticker", "")
            would_enter, mfcs, _dir_count = recompute_entry(raw_entry, profile)
            entry_decisions[ticker] = (would_enter, mfcs)

    # Layer 2: Simulate each trade setup
    for setup in session.trade_setups:
        # Check entry filter
        if profile.has_entry_overrides:
            decision = entry_decisions.get(setup.ticker)
            if decision is None or not decision[0]:
                result.entries_rejected += 1
                continue

        # Check bars availability
        if setup.ticker not in session.tickers_with_bars:
            continue

        bars = session.bars[setup.ticker]
        # Filter to market hours (same as replay_optimizer.simulate_day)
        # D121 BUG-H7: Was `hour >= 13` which admits 13:00-13:29 (pre-market).
        market_bars = [
            b for b in bars
            if b.dt.hour > 13 or (b.dt.hour == 13 and b.dt.minute >= 30)
        ]

        sim_result = mod.simulate_trade(setup, market_bars, sim_config)
        if sim_result:
            result.trade_results.append(sim_result)
            result.entries_accepted += 1
            if sim_result.pnl > 0:
                result.win_count += 1
            else:
                result.loss_count += 1
        else:
            result.entries_rejected += 1

    result.total_pnl = sum(r.pnl for r in result.trade_results)
    return result


# ═══════════════════════════════════════════════════════════════════
# Elo Tournament
# ═══════════════════════════════════════════════════════════════════

DEFAULT_ELO = 1200.0
DEFAULT_K = 32.0
COLD_START_THRESHOLD = 10
DRAW_THRESHOLD_PCT = 0.001  # 0.1% of equity — within this = draw


@dataclass
class StrategyVariant:
    """A strategy profile competing in the arena with Elo tracking."""
    profile_name: str
    elo_rating: float = DEFAULT_ELO
    pnl_stddev: float = 0.0  # D121 BUG-M8: renamed from elo_stddev (it's PnL variance, not Elo)
    match_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    draw_count: int = 0
    total_pnl: float = 0.0
    per_session_pnl: dict[str, float] = field(default_factory=dict)

    @property
    def win_rate(self) -> float:
        return self.win_count / max(1, self.match_count)

    def compute_stddev(self) -> None:
        """Compute ±1σ from per-session PnL variance."""
        pnls = list(self.per_session_pnl.values())
        if len(pnls) < 2:
            self.pnl_stddev = 0.0
            return
        mean = sum(pnls) / len(pnls)
        variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
        self.pnl_stddev = round(math.sqrt(variance), 1)

    def to_dict(self) -> dict:
        return {
            "profile_name": self.profile_name,
            "elo_rating": round(self.elo_rating, 1),
            "pnl_stddev": self.pnl_stddev,
            "match_count": self.match_count,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "draw_count": self.draw_count,
            "total_pnl": round(self.total_pnl, 2),
            "per_session_pnl": {k: round(v, 2) for k, v in self.per_session_pnl.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyVariant":
        return cls(
            profile_name=d["profile_name"],
            elo_rating=d.get("elo_rating", DEFAULT_ELO),
            pnl_stddev=d.get("pnl_stddev", d.get("elo_stddev", 0.0)),
            match_count=d.get("match_count", 0),
            win_count=d.get("win_count", 0),
            loss_count=d.get("loss_count", 0),
            draw_count=d.get("draw_count", 0),
            total_pnl=d.get("total_pnl", 0.0),
            per_session_pnl=d.get("per_session_pnl", {}),
        )


@dataclass
class TournamentResult:
    """Complete tournament output."""
    rankings: list[StrategyVariant]
    n_sessions: int
    n_profiles: int
    n_matchups: int
    per_profile_results: dict[str, list[ProfileSessionResult]]


class StrategyArena:
    """Round-robin tournament engine with Elo ranking.

    Reuses prompt_arena.compute_expected_score() and update_elo()
    for the core Elo math.
    """

    def __init__(self, equity: float = 138_000.0):
        self._variants: dict[str, StrategyVariant] = {}
        self._equity = equity  # D121 BUG-L4: configurable equity for draw threshold

    def register_profile(self, profile: StrategyProfile) -> None:
        if profile.name not in self._variants:
            self._variants[profile.name] = StrategyVariant(profile_name=profile.name)

    def run_tournament(
        self,
        sessions: list[ArenaSession],
        profiles: list[StrategyProfile],
    ) -> TournamentResult:
        """Full round-robin tournament across all sessions and profiles.

        For each session, simulates ALL profiles, then does pairwise
        Elo updates based on per-session P&L comparison.
        """
        # D121 BUG-L3: Reset variants on each tournament run to prevent
        # double-counting totals when run_tournament is called multiple times.
        self._variants = {}
        for p in profiles:
            self.register_profile(p)

        per_profile_results: dict[str, list[ProfileSessionResult]] = {
            p.name: [] for p in profiles
        }
        n_matchups = 0

        for session in sessions:
            # Simulate all profiles against this session
            session_results: dict[str, ProfileSessionResult] = {}
            for profile in profiles:
                psr = simulate_profile_session(session, profile)
                session_results[profile.name] = psr
                per_profile_results[profile.name].append(psr)
                self._variants[profile.name].per_session_pnl[session.date] = psr.total_pnl
                self._variants[profile.name].total_pnl += psr.total_pnl

            # Pairwise Elo updates
            profile_names = [p.name for p in profiles]
            for i in range(len(profile_names)):
                for j in range(i + 1, len(profile_names)):
                    name_a = profile_names[i]
                    name_b = profile_names[j]
                    pnl_a = session_results[name_a].total_pnl
                    pnl_b = session_results[name_b].total_pnl

                    self._record_matchup(name_a, name_b, pnl_a, pnl_b)
                    n_matchups += 1

        # Compute stddev for all variants
        for v in self._variants.values():
            v.compute_stddev()

        rankings = sorted(
            self._variants.values(),
            key=lambda v: v.elo_rating,
            reverse=True,
        )

        return TournamentResult(
            rankings=rankings,
            n_sessions=len(sessions),
            n_profiles=len(profiles),
            n_matchups=n_matchups,
            per_profile_results=per_profile_results,
        )

    def _record_matchup(
        self,
        name_a: str,
        name_b: str,
        pnl_a: float,
        pnl_b: float,
    ) -> None:
        """Record one head-to-head comparison and update Elo ratings."""
        va = self._variants[name_a]
        vb = self._variants[name_b]

        # Determine outcome
        diff = pnl_a - pnl_b
        # D121 BUG-L4: Use arena equity instead of hardcoded $138k
        threshold = self._equity * DRAW_THRESHOLD_PCT

        if abs(diff) < threshold:
            actual_a, actual_b = 0.5, 0.5
            va.draw_count += 1
            vb.draw_count += 1
        elif diff > 0:
            actual_a, actual_b = 1.0, 0.0
            va.win_count += 1
            vb.loss_count += 1
        else:
            actual_a, actual_b = 0.0, 1.0
            va.loss_count += 1
            vb.win_count += 1

        # Update Elo using prompt_arena math
        expected_a = compute_expected_score(va.elo_rating, vb.elo_rating)
        expected_b = 1.0 - expected_a

        va.elo_rating = update_elo(va.elo_rating, expected_a, actual_a, k=DEFAULT_K)
        vb.elo_rating = update_elo(vb.elo_rating, expected_b, actual_b, k=DEFAULT_K)

        va.match_count += 1
        vb.match_count += 1

    def get_rankings(self) -> list[StrategyVariant]:
        rankings = sorted(
            self._variants.values(),
            key=lambda v: v.elo_rating,
            reverse=True,
        )
        return rankings

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "variants": {k: v.to_dict() for k, v in self._variants.items()},
            "equity": self._equity,  # D121 BUG: persist for correct draw threshold on load
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, path: Path) -> "StrategyArena":
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # D121 BUG: Restore equity for correct draw threshold
            arena = cls(equity=data.get("equity", 138_000.0))
            for name, vd in data.get("variants", {}).items():
                arena._variants[name] = StrategyVariant.from_dict(vd)
        else:
            arena = cls()
        return arena


# ═══════════════════════════════════════════════════════════════════
# Counterfactual Analysis
# ═══════════════════════════════════════════════════════════════════

@dataclass
class CounterfactualOutcome:
    would_enter: bool
    recomputed_mfcs: float | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    hold_minutes: float | None = None
    tranches_filled: int | None = None


@dataclass
class TradeCounterfactual:
    ticker: str
    session_date: str
    entry_price: float
    actual_pnl: float | None
    profile_outcomes: dict[str, CounterfactualOutcome] = field(default_factory=dict)


def build_counterfactuals(
    session: ArenaSession,
    profiles: list[StrategyProfile],
    results: dict[str, ProfileSessionResult],
) -> list[TradeCounterfactual]:
    """Build per-trade counterfactual comparison across all profiles."""
    # Get all traded tickers from any profile's results
    all_tickers: dict[str, float] = {}  # ticker -> entry_price
    for setup in session.trade_setups:
        if setup.ticker in session.tickers_with_bars:
            all_tickers[setup.ticker] = setup.entry_price

    # Find actual PnL from journal
    actual_pnl_map: dict[str, float] = {}
    for entry in session.raw_entries:
        ticker = entry.get("ticker", "")
        pnl = entry.get("realized_pnl")
        if pnl is not None and ticker:
            actual_pnl_map[ticker] = float(pnl)

    counterfactuals: list[TradeCounterfactual] = []
    for ticker, entry_price in all_tickers.items():
        cf = TradeCounterfactual(
            ticker=ticker,
            session_date=session.date,
            entry_price=entry_price,
            actual_pnl=actual_pnl_map.get(ticker),
        )

        for profile in profiles:
            psr = results.get(profile.name)
            if not psr:
                continue

            # Find this ticker's result in the profile's trades
            trade_match = None
            for tr in psr.trade_results:
                if tr.ticker == ticker:
                    trade_match = tr
                    break

            if trade_match:
                cf.profile_outcomes[profile.name] = CounterfactualOutcome(
                    would_enter=True,
                    exit_price=trade_match.exit_price,
                    exit_reason=trade_match.exit_reason,
                    pnl=trade_match.pnl,
                    pnl_pct=trade_match.pnl_pct,
                    hold_minutes=trade_match.hold_minutes,
                    tranches_filled=trade_match.tranches_filled,
                )
            else:
                # Check if entry was rejected
                would_enter = True
                mfcs = None
                if profile.has_entry_overrides:
                    for raw_entry in session.raw_entries:
                        if raw_entry.get("ticker") == ticker and raw_entry.get("action") == "BUY":
                            we, m, _ = recompute_entry(raw_entry, profile)
                            would_enter = we
                            mfcs = m
                            break
                cf.profile_outcomes[profile.name] = CounterfactualOutcome(
                    would_enter=would_enter,
                    recomputed_mfcs=mfcs,
                )

        counterfactuals.append(cf)

    return counterfactuals
