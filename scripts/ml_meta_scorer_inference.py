"""Live-bot inference path for the unified meta-scorer (session 109).

WHAT: importable module that loads the v3-tuned ensemble + TCN model + Ising
regime once, then provides `score_candidate()` for per-pick decisions.

WHY: scripts/ml_meta_scorer.py operates on WF-prediction parquets (offline).
This module operates on LIVE candidate dicts at decision time, called from
scripts/lottery_runner.py and scripts/daily_paper_trade.ps1.

USAGE:
    from ml_meta_scorer_inference import MetaScorer

    scorer = MetaScorer.load_default()  # loads all artifacts once
    decision = scorer.score_candidate(
        candidate={
            "ticker": "AAPL", "open": 1.50, "intraday_pct": 0.30,
            "dvol_d0": 5_000_000, "ret_open_close_d0": 0.20,
            # ... full feature dict; see required_features list below
        },
        intraday_path=None,   # (30, 6) np array if available, else None for default TCN proba
        bankroll=10_000.0,
    )
    # decision = MetaDecision(
    #     tier="ELITE", meta_score=0.638, kelly_frac=0.05,
    #     notional_usd=500.0, conformal_width=0.42,
    #     v3t_proba=0.638, tcn_proba=None, mag_label="MID",
    #     reason="v3t>=0.60 AND mag IN (HI, MID)"
    # )

NO live trading is invoked here. Caller (lottery_runner) translates
MetaDecision into Alpaca order calls.

ENV:
  MX_META_SCORER_MODEL_DIR  override default data/models/ path
  MX_META_SCORER_LOG_LEVEL  default WARNING; set DEBUG to trace per-call
"""
from __future__ import annotations
import json
import logging
import os
import pickle
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
MODELS = Path(os.environ.get("MX_META_SCORER_MODEL_DIR",
                              str(REPO / "data" / "models")))
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"

# Tier kelly caps. Two profiles:
#   conservative (default): mirrors scripts/ml_meta_scorer.py session 109 caps.
#   aggressive: paper-trading-only "push to limit" caps (50/35/20/10) — chosen
#     to let the model express its conviction (full Kelly for ELITE is ~62%
#     given the WF win/loss distribution; HIGH is ~35%; capping at full-Kelly
#     levels avoids over-leverage if a freak high-p prediction lands).
#
# Selected by env: MX_META_KELLY_PROFILE=aggressive | conservative (default).
KELLY_CAPS_CONSERVATIVE = {"ELITE": 0.05, "HIGH": 0.03, "VETOED": 0.02, "BROAD": 0.01, "SKIP": 0.0}
# 2026-05-12 D291.5 (doc 162 §7): HIGH bumped 0.35 -> 0.50.
# Trigger: doc 150 sub-exp B found Bouchaud-optimal HIGH Kelly = 98% on
# n=25 picks. Doc 162 §1 E3 pre-commit required n_HIGH (last 60d) >= 50
# before shipping the raise; verified at n=56 with mean ret +4.13% over
# the last 60 days. Staying conservative at 0.50 vs the Bouchaud-optimal
# 0.98 -- production capacity caps + per-tier risk diversification still
# matter even when the per-pick Kelly says go bigger. Per-AUM schedule
# below at line 123 also updated for HIGH tier.
KELLY_CAPS_AGGRESSIVE   = {"ELITE": 0.50, "HIGH": 0.50, "VETOED": 0.20, "BROAD": 0.10, "SKIP": 0.0}

_PROFILE = os.environ.get("MX_META_KELLY_PROFILE", "conservative").strip().lower()
if _PROFILE == "aggressive":
    KELLY_CAPS = KELLY_CAPS_AGGRESSIVE
else:
    KELLY_CAPS = KELLY_CAPS_CONSERVATIVE

# D285 (2026-05-07, doc 142 Bouchaud capacity finding): cap per-pick
# notional at MAX_PARTICIPATION_PCT × dvol_d0. The Bouchaud square-root
# impact law (Toth-Eisler-Bouchaud PRX 2011) says position impact scales
# as Y · √(participation) · σ_daily. At 5% participation with Y=1.5 and
# σ_daily≈11% (median microcap), round-trip impact is ~7%. Above 5%
# participation the impact compounds super-linearly. Doc 142 capacity
# analysis showed v3's +20% raw alpha on ELITE picks goes net-NEGATIVE
# at $5M AUM under current Aggressive Kelly without this cap; with
# this cap, ELITE stays profitable through ~$10M AUM. At the current
# $140K paper account this guard rarely binds (most positions are
# already <0.5% of dvol_d0). Defense for future scaling.
#
# To disable (NOT RECOMMENDED): set LOTTERY_MAX_PARTICIPATION_PCT=1.0
LOTTERY_MAX_PARTICIPATION_PCT = float(
    os.environ.get("LOTTERY_MAX_PARTICIPATION_PCT", "0.05")
)

# D290 (2026-05-11, doc 149 adaptive Kelly): per-tier Kelly caps that
# shrink as AUM grows. Doc 142 + doc 147 §5 found that fixed Aggressive
# Kelly (50/35/20/10) produces NEGATIVE expected $-PNL at $1M+ AUM
# because per-pick impact dominates per-pick mean return for tiers
# with smaller mean (BROAD especially). The participation cap (D285)
# alone defends impact but DOES NOT optimize per-pick $-PNL.
#
# This adaptive schedule was derived by maximizing per-pick $-PNL
# = (AUM × kelly) × E[mean_ret - 2Y·sqrt(participation)·sigma_d]
# on the recent (Aug 2025+) per-tier OOS predictions. Schedule per
# tier × AUM bracket; linearly interpolated in log(AUM) between brackets.
#
# At $140K paper account: same as fixed Aggressive Kelly (no degradation).
# At $1M AUM: BROAD drops from 10% to 1%; ELITE from 50% to 27.5%; HIGH stays.
# At $5M AUM: ELITE 5.5%, HIGH 19.5%, VETOED 2%, BROAD 0.5%.
#
# Pre-commit verdict: ship if $-PNL improves >=10% at $1M+ AUM AND no
# degradation at $140K. Met: $0% lift at $140K, +60% lift at $500K,
# essentially infinite lift at $1M+ (current goes net-negative).
#
# To disable (NOT RECOMMENDED above $1M AUM): set LOTTERY_USE_ADAPTIVE_KELLY=0.
LOTTERY_USE_ADAPTIVE_KELLY = os.environ.get(
    "LOTTERY_USE_ADAPTIVE_KELLY", "1"
).strip() in ("1", "true", "yes", "on")

# Adaptive Kelly schedule from doc 149 § Step 3.
# Format: tier -> [(aum_threshold, kelly_cap), ...] sorted by aum_threshold.
# adaptive_kelly_cap(tier, aum) interpolates linearly in log(AUM) between
# adjacent brackets. Below the lowest bracket: use that bracket's cap.
# Above the highest: use that bracket's cap.
ADAPTIVE_KELLY_SCHEDULE = {
    "ELITE":  [(100_000, 0.500), (250_000, 0.500), (500_000, 0.500),
               (1_000_000, 0.275), (2_000_000, 0.140),
               (5_000_000, 0.055), (10_000_000, 0.030)],
    # 2026-05-12 D291.5 (doc 162 §7): HIGH bumped 0.350 -> 0.500 at AUM
    # brackets <= $2M (where capacity caps haven't bitten yet). High-AUM
    # brackets ($5M, $10M) keep the smaller cap because Bouchaud impact
    # dominates there. n_HIGH (last 60d) = 56 picks, mean ret = +4.13%
    # justified the raise.
    "HIGH":   [(100_000, 0.500), (250_000, 0.500), (500_000, 0.500),
               (1_000_000, 0.500), (2_000_000, 0.500),
               (5_000_000, 0.195), (10_000_000, 0.100)],
    "VETOED": [(100_000, 0.200), (250_000, 0.200), (500_000, 0.200),
               (1_000_000, 0.110), (2_000_000, 0.055),
               (5_000_000, 0.020), (10_000_000, 0.010)],
    "BROAD":  [(100_000, 0.100), (250_000, 0.040), (500_000, 0.020),
               (1_000_000, 0.010), (2_000_000, 0.005),
               (5_000_000, 0.005), (10_000_000, 0.005)],
    "SKIP":   [(100_000, 0.0)],
}


def adaptive_kelly_cap(tier: str, aum: float) -> float:
    """Lookup adaptive Kelly cap for (tier, aum), interpolating in log(AUM).

    If LOTTERY_USE_ADAPTIVE_KELLY=0, returns the fixed AGGRESSIVE cap
    (preserving D285+prior-version behavior for opt-out).
    """
    import math
    if not LOTTERY_USE_ADAPTIVE_KELLY:
        return KELLY_CAPS_AGGRESSIVE.get(tier, 0.0)
    schedule = ADAPTIVE_KELLY_SCHEDULE.get(tier)
    if not schedule:
        return 0.0
    if aum <= schedule[0][0]:
        return schedule[0][1]
    if aum >= schedule[-1][0]:
        return schedule[-1][1]
    # Linear interpolation in log10(AUM)
    log_aum = math.log10(max(aum, 1.0))
    for i in range(len(schedule) - 1):
        a_lo, k_lo = schedule[i]
        a_hi, k_hi = schedule[i + 1]
        if a_lo <= aum <= a_hi:
            log_lo = math.log10(a_lo)
            log_hi = math.log10(a_hi)
            if log_hi == log_lo:
                return k_lo
            t = (log_aum - log_lo) / (log_hi - log_lo)
            return k_lo + t * (k_hi - k_lo)
    return schedule[-1][1]

# Per-tier expected win/loss frozen from session 109 WF (used for Kelly inputs).
# In production, refit per-fold; here we use the validated WF-aggregate values.
TIER_EW_EL_PCT = {
    "ELITE":  (0.7207, -0.0833),   # +72.07% / -8.33%
    "HIGH":   (0.4075, -0.1745),
    "VETOED": (0.3490, -0.1421),
    "BROAD":  (0.3095, -0.1763),
}

# Mag tercile thresholds (session 106)
MAG_HI_THR = 0.05
MAG_LO_THR = -0.05

# VETOED tier rule selector (env-gated). Default = "E" (production).
#   E: v3t>=0.30 AND mag=MID AND tcn<0.30  (s109; +9.07%/trade WF; TCN debunked s114)
#   D: v3t>=0.30 AND mag=MID AND intraday_pct < INTRA_P25_THRESHOLD
#      (s115 ablation: +13.37%/trade WF n=67; Lou/Polk/Skouras 2019 fade prior)
# Set MX_VETOED_RULE=D to swap. Recommended for post-stable-Monday production.
VETOED_RULE = os.environ.get("MX_VETOED_RULE", "E").strip().upper()
# p25 of intraday_pct within the v3+MID-mag cohort (frozen from s115 WF).
# Recompute per fold in production deployment via training-set quantile.
INTRA_P25_THRESHOLD = float(os.environ.get("MX_VETOED_INTRA_P25", "0.3608"))

# v3 temperature scaling (s123). Applies sigmoid(logit / T) to v3 outputs.
# T from data/models/v3_temperature.json: 1.0275 (model is slightly
# overconfident; softening brings ECE 0.0097 -> 0.0053 = -45%).
# Per-tier lift: +9% picks in BROAD, marginal +2.9% total weighted lift.
# Set MX_V3_TEMPERATURE=1.0 to disable.
V3_TEMPERATURE = float(os.environ.get("MX_V3_TEMPERATURE", "1.0275"))

# Hybrid ELITE router (s125). Per s124 finding: v3-default model
# generates +$1,388 (+14.4%) better ELITE-tier P&L vs v3-tuned-16fold.
# When MX_HYBRID_ELITE=1, MetaScorer loads v3-default in addition to
# the production v3-tuned-16fold. For each candidate, BOTH predictions
# are computed; if v3-default >= 0.60, use v3-default's prediction
# (allows ELITE upgrade). Otherwise use v3-tuned-16fold.
USE_HYBRID_ELITE = os.environ.get("MX_HYBRID_ELITE", "0").strip() in ("1", "true", "yes", "on")

# D281 (2026-05-05) cohort-specialized cascade. When MX_TIERED_LEARNING=1,
# MetaScorer additionally loads 4 tier-specialist models trained on
# different return-magnitude thresholds (BROAD: ret_t5>=0.10,
# VETOED: 0.15, HIGH: 0.25, ELITE: 0.40). At inference, BOTH the
# baseline (v3-tuned) and each specialist are evaluated; tier waterfall
# fires under UNION semantics: a tier matches if EITHER the baseline rule
# matches OR the specialist exceeds its tier-specific threshold. WF
# verdict (data/models/v3_tier_threshold_search.json): UNION strategy at
# (ELITE 0.40, HIGH 0.40, VETOED 0.40, BROAD 0.50) delivers +$1,104 over
# the production baseline ($12,699 vs $11,594) on the s109 16-fold WF
# eval. Per-tier: +$1,468 ELITE, +$246 HIGH, -$241 VETOED, -$367 BROAD
# (worst tier regression -24% within the SHIP gate's -30% allowance).
USE_TIERED_LEARNING = os.environ.get("MX_TIERED_LEARNING", "0").strip() in ("1", "true", "yes", "on")
TIER_ELITE_THR = float(os.environ.get("MX_TIER_ELITE_THR", "0.40"))
TIER_HIGH_THR = float(os.environ.get("MX_TIER_HIGH_THR", "0.40"))
TIER_VETOED_THR = float(os.environ.get("MX_TIER_VETOED_THR", "0.40"))
TIER_BROAD_THR = float(os.environ.get("MX_TIER_BROAD_THR", "0.50"))

# D282 (2026-05-06) MoMTrans v4 — multi-task tabular transformer with SSL
# pre-training and 4 cohort specialists. Phase A-C validation:
#   Phase A (OOS threshold validation): lift retention 103.7% (verify lift
#           +$38,803 vs tune lift +$37,417). NOT threshold-overfit.
#   Phase B (cap=10/day capacity-realistic): lift +$71,049 (+612.6%) vs
#           production v3-tuned-16f baseline.
#   Phase C (slippage-adjusted at 100 bps live-retail): lift +$46,573
#           (+421%); break-even at ~290 bps with 3-4x headroom.
# Per-pick edge at 100 bps: $33.76 (MoMTrans) vs $22.97 (v3) = +47%.
# When MX_USE_MOMTRANS=1, MetaScorer loads 4 PyTorch tier-specialist
# .pt files and routes inference through predict_v4t() which applies
# the Phase-A-frozen UNION cascade with thresholds (E 0.60, H 0.70,
# V 0.90, B 0.60). When OFF, the existing v3+D281 path is unchanged.
USE_MOMTRANS = os.environ.get("MX_USE_MOMTRANS", "0").strip() in ("1", "true", "yes", "on")
MOMTRANS_ELITE_THR = float(os.environ.get("MX_MOMTRANS_ELITE_THR", "0.60"))
MOMTRANS_HIGH_THR = float(os.environ.get("MX_MOMTRANS_HIGH_THR", "0.70"))
MOMTRANS_VETOED_THR = float(os.environ.get("MX_MOMTRANS_VETOED_THR", "0.90"))
MOMTRANS_BROAD_THR = float(os.environ.get("MX_MOMTRANS_BROAD_THR", "0.60"))

log = logging.getLogger("meta_scorer_inference")
log.setLevel(os.environ.get("MX_META_SCORER_LOG_LEVEL", "WARNING").upper())


@dataclass
class MetaDecision:
    ticker: str
    tier: str
    meta_score: float
    kelly_frac: float
    notional_usd: float
    conformal_width: float
    v3t_proba: float
    tcn_proba: Optional[float]
    mag_label: str
    reason: str


def classify_mag(mag_5d: float) -> str:
    if mag_5d > MAG_HI_THR: return "HI"
    if mag_5d < MAG_LO_THR: return "LO"
    return "MID"


class MetaScorer:
    """Self-contained inference. Load once at runner startup, call per pick."""

    def __init__(self, v3t_artifacts: dict, tcn_state: Optional[dict],
                 mag_5d: float,
                 v3_default_artifacts: Optional[dict] = None,
                 tier_specialist_artifacts: Optional[dict[str, dict]] = None,
                 momtrans_artifacts: Optional[dict[str, dict]] = None):
        self.v3t = v3t_artifacts
        self.feature_columns: list[str] = list(v3t_artifacts["feature_columns"])
        self.conformal_threshold: float = float(v3t_artifacts["conformal_threshold"])
        self.tcn_state = tcn_state
        self.tcn_model = None
        if tcn_state is not None:
            self.tcn_model = self._build_tcn_from_state(tcn_state)
        # s125 hybrid ELITE: optional v3-default for elite-tier routing
        self.v3_default = v3_default_artifacts
        # D281 tiered cascade: optional dict {ELITE: {...}, HIGH: {...}, ...}
        self.tier_specialists = tier_specialist_artifacts or {}
        # Per-call cache for specialist probabilities (set by predict_v3t,
        # consumed by assign_tier). Avoids changing predict_v3t's return shape.
        self._last_specialist_probas: dict[str, float] = {}
        # D282 MoMTrans: artifacts dict + lazily-instantiated nn.Modules
        self.momtrans_artifacts = momtrans_artifacts or {}
        self._momtrans_models: dict[str, "torch.nn.Module"] = {}
        self._momtrans_feature_columns: list[str] = []
        self._momtrans_means: list[float] = []
        self._momtrans_stds: list[float] = []
        if self.momtrans_artifacts:
            self._build_momtrans_models()
        self.mag_5d = float(mag_5d)
        self.mag_label = classify_mag(self.mag_5d)
        log.info(
            "MetaScorer loaded: v3t feats=%d, tcn=%s, hybrid=%s, "
            "tiered=%s (%d specialists), momtrans=%s (%d/4), mag=%.4f (%s)",
            len(self.feature_columns), tcn_state is not None,
            v3_default_artifacts is not None,
            bool(self.tier_specialists), len(self.tier_specialists),
            bool(self._momtrans_models), len(self._momtrans_models),
            self.mag_5d, self.mag_label,
        )

    def _build_momtrans_models(self) -> None:
        """Reconstruct the 4 MoMTrans tier-specialist nn.Modules from saved
        state dicts. Done once at MetaScorer construction; the resulting
        models live on CPU (inference latency is sub-ms per candidate).
        """
        try:
            import torch  # noqa: F401 — required for model rebuild
        except ImportError:
            log.warning("torch not importable — MoMTrans inference disabled")
            self._momtrans_models = {}
            return

        # All 4 specialists share the same feature_columns / means / stds.
        # Take them from the first available specialist.
        first = next(iter(self.momtrans_artifacts.values()))
        self._momtrans_feature_columns = list(first["feature_columns"])
        self._momtrans_means = list(first["feature_means"])
        self._momtrans_stds = list(first["feature_stds"])

        # Local import to avoid a hard dependency on the trainer at runtime
        # if MoMTrans is OFF. The build_model factory is identical to what
        # produced the .pt artifacts.
        import sys as _sys
        from pathlib import Path as _Path
        _here = _Path(__file__).resolve().parent
        if str(_here) not in _sys.path:
            _sys.path.insert(0, str(_here))
        try:
            from ml_v4_momtrans_train import build_model as _build_v4_model
        except Exception as e:
            log.warning("Failed to import MoMTrans build_model: %s — disabled", e)
            self._momtrans_models = {}
            return

        for tier_name, blob in self.momtrans_artifacts.items():
            cfg = blob["config"]
            model = _build_v4_model(
                n_tab_features=cfg["n_features"],
                d_model=cfg["d_model"],
                n_layers=cfg["n_layers"],
                dropout=cfg["dropout"],
                disable_sequence=cfg.get("disable_sequence", True),
            )
            model.load_state_dict(blob["model_state"])
            model.eval()
            self._momtrans_models[tier_name] = model
        log.info("MoMTrans: rebuilt %d nn.Module specialists from .pt state dicts",
                 len(self._momtrans_models))

    @classmethod
    def load_default(cls, v3t_pkl: Optional[Path] = None,
                      tcn_pt: Optional[Path] = None,
                      mag_5d: Optional[float] = None) -> "MetaScorer":
        """Load production models. Skips TCN load when MX_VETOED_RULE=D
        (s122: rule D is TCN-free, no need to load 50 MB checkpoint).
        """
        v3t_pkl = v3t_pkl or (MODELS / "continuer_v2_v3_tuned.pkl")
        tcn_pt = tcn_pt or (MODELS / "tcn_intraday.pt")
        if not v3t_pkl.exists():
            raise FileNotFoundError(
                f"v3-tuned model missing at {v3t_pkl}. Run: "
                "python scripts/ml_continuer_v2_ensemble.py --include-paths "
                "--optuna-params data/models/v2_optuna_best_params.json --out-suffix _v3_tuned"
            )
        with open(v3t_pkl, "rb") as f:
            v3t_artifacts = pickle.load(f)
        tcn_state = None
        # Skip TCN load entirely when rule D is active — saves 50 MB + load time.
        if VETOED_RULE == "D":
            log.info("VETOED_RULE=D — skipping TCN load (rule D is TCN-free)")
        elif tcn_pt.exists():
            try:
                import torch
                tcn_state = torch.load(tcn_pt, map_location="cpu", weights_only=False)
            except Exception as e:
                log.warning("Failed to load TCN at %s: %s — VETOED tier disabled", tcn_pt, e)
        else:
            log.warning("TCN .pt missing at %s — VETOED tier disabled (no TCN-veto signal)", tcn_pt)
        if mag_5d is None:
            mag_5d = cls._latest_mag_5d()

        # s125 hybrid ELITE: optionally load v3-default for elite routing
        v3_default_artifacts = None
        if USE_HYBRID_ELITE:
            v3_default_pkl = MODELS / "continuer_v2_v3.pkl"
            if v3_default_pkl.exists():
                try:
                    with open(v3_default_pkl, "rb") as f:
                        v3_default_artifacts = pickle.load(f)
                    log.info("MX_HYBRID_ELITE=1 — loaded v3-default for elite routing")
                except Exception as e:
                    log.warning("Failed to load v3-default at %s: %s — hybrid disabled",
                                  v3_default_pkl, e)
            else:
                log.warning("MX_HYBRID_ELITE=1 but %s missing — hybrid disabled",
                              v3_default_pkl)

        # D281 tiered learning: optionally load 4 cohort specialists
        tier_specialists: dict[str, dict] = {}
        if USE_TIERED_LEARNING:
            for tier_name in ("ELITE", "HIGH", "VETOED", "BROAD"):
                spec_pkl = MODELS / f"continuer_v2_v3_tier_{tier_name}.pkl"
                if not spec_pkl.exists():
                    log.warning("MX_TIERED_LEARNING=1 but %s missing — "
                                  "%s specialist disabled", spec_pkl.name, tier_name)
                    continue
                try:
                    with open(spec_pkl, "rb") as f:
                        tier_specialists[tier_name] = pickle.load(f)
                except Exception as e:
                    log.warning("Failed to load %s specialist at %s: %s",
                                  tier_name, spec_pkl, e)
            log.info("MX_TIERED_LEARNING=1 — loaded %d/4 cohort specialists "
                     "(thresholds: ELITE>=%.2f, HIGH>=%.2f, VETOED>=%.2f, BROAD>=%.2f)",
                     len(tier_specialists), TIER_ELITE_THR, TIER_HIGH_THR,
                     TIER_VETOED_THR, TIER_BROAD_THR)

        # D282 MoMTrans: optionally load 4 PyTorch tier specialists.
        # When MX_USE_MOMTRANS=1 AND all 4 .pt files present, the
        # MetaScorer's predict path uses the MoMTrans cascade INSTEAD OF
        # the v3 + D281 path. When OFF or files missing, MoMTrans is
        # disabled and the existing v3 + D281 path runs unchanged.
        momtrans_specialists: dict[str, dict] = {}
        if USE_MOMTRANS:
            for tier_name in ("ELITE", "HIGH", "VETOED", "BROAD"):
                spec_pt = MODELS / f"momtrans_v4_tier_{tier_name}.pt"
                if not spec_pt.exists():
                    log.warning("MX_USE_MOMTRANS=1 but %s missing — "
                                  "%s MoMTrans specialist disabled", spec_pt.name, tier_name)
                    continue
                try:
                    import torch
                    blob = torch.load(spec_pt, map_location="cpu", weights_only=False)
                    momtrans_specialists[tier_name] = blob
                except Exception as e:
                    log.warning("Failed to load MoMTrans %s at %s: %s",
                                  tier_name, spec_pt, e)
            log.info("MX_USE_MOMTRANS=1 — loaded %d/4 MoMTrans specialists "
                     "(thresholds: E>=%.2f, H>=%.2f, V>=%.2f, B>=%.2f)",
                     len(momtrans_specialists), MOMTRANS_ELITE_THR, MOMTRANS_HIGH_THR,
                     MOMTRANS_VETOED_THR, MOMTRANS_BROAD_THR)

        return cls(v3t_artifacts, tcn_state, mag_5d, v3_default_artifacts,
                     tier_specialist_artifacts=tier_specialists,
                     momtrans_artifacts=momtrans_specialists)

    @staticmethod
    def _latest_mag_5d() -> float:
        """Read the latest 5d-rolling magnetization from ising_daily.parquet.
        Returns 0.0 if missing (defaults to MID-mag)."""
        ising = DERIVED / "ising_daily.parquet"
        if not ising.exists():
            log.warning("ising_daily.parquet missing — defaulting mag_5d=0 (MID)")
            return 0.0
        try:
            import duckdb
            r = duckdb.connect().sql(f"""
                SELECT AVG(magnetization) FROM (
                    SELECT magnetization FROM read_parquet('{ising.as_posix()}')
                    ORDER BY d DESC LIMIT 5
                )
            """).fetchone()
            return float(r[0]) if r and r[0] is not None else 0.0
        except Exception as e:
            log.warning("Failed to read ising_daily.parquet: %s", e)
            return 0.0

    def _build_tcn_from_state(self, state: dict):
        import torch
        import torch.nn as nn

        # Re-instantiate the DilatedTCN architecture from saved metadata
        in_channels = state.get("n_features", 6)
        channels = tuple(state.get("channels", (16, 32, 32, 16)))
        k = int(state.get("kernel_size", 3))
        dropout = float(state.get("dropout", 0.2))

        layers = []
        prev_c = in_channels
        for i, c in enumerate(channels):
            d = 2 ** i
            pad = (k - 1) * d
            layers += [
                nn.Conv1d(prev_c, c, kernel_size=k, dilation=d, padding=pad // 2),
                nn.BatchNorm1d(c),
                nn.ReLU(),
                nn.Dropout(dropout),
            ]
            prev_c = c

        class _TCN(nn.Module):
            def __init__(self):
                super().__init__()
                self.body = nn.Sequential(*layers)
                self.head = nn.Linear(prev_c, 1)

            def forward(self, x):
                h = self.body(x).mean(dim=2)
                return self.head(h).squeeze(-1)

        model = _TCN()
        model.load_state_dict(state["state_dict"])
        model.eval()
        return model

    def predict_v3t(self, features: dict) -> tuple[float, float]:
        """Run candidate features through v3-tuned stacked ensemble.

        Returns (proba, conformal_width).
        """
        # Build a 1-row DataFrame in feature-column order; default missing to 0.
        row = {c: float(features.get(c, 0.0)) for c in self.feature_columns}
        Xrow = pd.DataFrame([row])[self.feature_columns]
        # Replace inf/nan as in training
        Xrow = Xrow.replace([np.inf, -np.inf], 0).fillna(0)

        base = self.v3t["base_learners"]
        meta = self.v3t["meta_learner"]
        # P_test stack
        P = np.column_stack([m.predict_proba(Xrow)[:, 1] for m in base.values()])
        proba = float(meta.predict_proba(P)[0, 1])
        # s123: temperature scaling. T=1.0 disables; T=1.0275 = production
        # post-training calibration (ECE 0.0097 -> 0.0053).
        if abs(V3_TEMPERATURE - 1.0) > 1e-3:
            eps = 1e-7
            p_clip = max(min(proba, 1 - eps), eps)
            logit = np.log(p_clip / (1 - p_clip))
            proba = float(1.0 / (1.0 + np.exp(-logit / V3_TEMPERATURE)))
        # s125 hybrid ELITE: if v3-default loaded AND it would predict ELITE
        # (>=0.60), prefer that prediction. Per s124: v3-default ELITE tier
        # +$1,388 vs v3-tuned-16f. Otherwise stick with v3-tuned (+s123 T-scale).
        if self.v3_default is not None:
            v3d_base = self.v3_default["base_learners"]
            v3d_meta = self.v3_default["meta_learner"]
            v3d_cols = list(self.v3_default["feature_columns"])
            # Build row in v3-default's feature order (different col set possible)
            row_d = {c: float(features.get(c, 0.0)) for c in v3d_cols}
            Xrow_d = pd.DataFrame([row_d])[v3d_cols]
            Xrow_d = Xrow_d.replace([np.inf, -np.inf], 0).fillna(0)
            P_d = np.column_stack([m.predict_proba(Xrow_d)[:, 1] for m in v3d_base.values()])
            proba_d = float(v3d_meta.predict_proba(P_d)[0, 1])
            # If v3-default predicts ELITE, use its score
            if proba_d >= 0.60:
                log.debug("hybrid ELITE upgrade: v3t=%.3f, v3-default=%.3f",
                            proba, proba_d)
                proba = proba_d
        # D281 tiered cascade: also compute specialist probabilities (cached
        # on self for assign_tier to consume). UNION semantics: tier fires if
        # baseline rule matches OR specialist exceeds tier threshold.
        if self.tier_specialists:
            self._last_specialist_probas = {}
            for tier_name, spec in self.tier_specialists.items():
                spec_base = spec["base_learners"]
                spec_meta = spec["meta_learner"]
                spec_cols = list(spec["feature_columns"])
                row_s = {c: float(features.get(c, 0.0)) for c in spec_cols}
                Xrow_s = pd.DataFrame([row_s])[spec_cols]
                Xrow_s = Xrow_s.replace([np.inf, -np.inf], 0).fillna(0)
                P_s = np.column_stack([
                    m.predict_proba(Xrow_s)[:, 1] for m in spec_base.values()
                ])
                self._last_specialist_probas[tier_name] = float(
                    spec_meta.predict_proba(P_s)[0, 1]
                )
        # Conformal width = (1 - |p - 0.5| * 2) * (1 + threshold), clipped [0,1]
        width = (1 - abs(proba - 0.5) * 2) * (1 + self.conformal_threshold)
        width = float(np.clip(width, 0.0, 1.0))
        return proba, width

    def predict_tcn(self, intraday_path: Optional[np.ndarray]) -> Optional[float]:
        """Run intraday path through TCN.

        intraday_path: shape (6, 30) for one (ticker, d0). Returns proba in [0,1]
        or None if no TCN model loaded or path is None.
        """
        if self.tcn_model is None or intraday_path is None:
            return None
        import torch
        if intraday_path.shape != (6, 30):
            log.warning("intraday_path shape %s != (6, 30); skipping TCN",
                         intraday_path.shape)
            return None
        x = torch.from_numpy(intraday_path.astype(np.float32)).unsqueeze(0)  # (1, 6, 30)
        with torch.no_grad():
            logit = self.tcn_model(x).item()
        return float(1.0 / (1.0 + np.exp(-logit)))

    def assign_tier(self, v3t_proba: float, tcn_proba: Optional[float],
                      intraday_pct: Optional[float] = None) -> tuple[str, str]:
        """Tier waterfall. Returns (tier, reason).

        VETOED rule depends on MX_VETOED_RULE env:
          E (default, production): v3t>=0.30 AND mag=MID AND tcn<0.30
          D (s115, post-Monday):    v3t>=0.30 AND mag=MID AND intraday_pct < p25

        D281 tiered cascade: when MX_TIERED_LEARNING=1 and specialists are
        loaded, each tier's gate is the UNION of (baseline rule) OR
        (specialist proba >= tier-specific threshold).
        """
        mag = self.mag_label
        sp = self._last_specialist_probas  # may be empty when tiered learning off

        # ELITE tier (UNION: baseline OR specialist)
        elite_baseline = v3t_proba >= 0.60 and mag in ("HI", "MID")
        elite_specialist = (
            sp.get("ELITE", -1.0) >= TIER_ELITE_THR and mag in ("HI", "MID")
        )
        if elite_baseline or elite_specialist:
            r = (f"v3t>=0.60 AND mag IN (HI, MID)" if elite_baseline
                 else f"specialist_ELITE({sp['ELITE']:.3f})>={TIER_ELITE_THR:.2f} "
                      f"AND mag IN (HI, MID) [D281 cascade]")
            return "ELITE", r

        # HIGH tier
        high_baseline = v3t_proba >= 0.50 and mag in ("HI", "MID")
        high_specialist = (
            sp.get("HIGH", -1.0) >= TIER_HIGH_THR and mag in ("HI", "MID")
        )
        if high_baseline or high_specialist:
            r = (f"v3t>=0.50 AND mag IN (HI, MID)" if high_baseline
                 else f"specialist_HIGH({sp['HIGH']:.3f})>={TIER_HIGH_THR:.2f} "
                      f"AND mag IN (HI, MID) [D281 cascade]")
            return "HIGH", r

        # VETOED tier — rule selector
        vetoed_baseline = False
        if VETOED_RULE == "D":
            vetoed_baseline = (v3t_proba >= 0.30 and mag == "MID"
                               and intraday_pct is not None
                               and intraday_pct < INTRA_P25_THRESHOLD)
        else:  # rule E (default)
            vetoed_baseline = (v3t_proba >= 0.30 and mag == "MID"
                               and tcn_proba is not None and tcn_proba < 0.30)
        vetoed_specialist = (
            sp.get("VETOED", -1.0) >= TIER_VETOED_THR and mag == "MID"
        )
        if vetoed_baseline or vetoed_specialist:
            if vetoed_baseline:
                r = (f"v3t>=0.30 AND mag=MID AND "
                     f"intraday_pct({intraday_pct:.3f})<p25({INTRA_P25_THRESHOLD:.3f}) [rule D]"
                     if VETOED_RULE == "D"
                     else "v3t>=0.30 AND mag=MID AND tcn<0.30 [rule E]")
            else:
                r = (f"specialist_VETOED({sp['VETOED']:.3f})>={TIER_VETOED_THR:.2f} "
                     f"AND mag=MID [D281 cascade]")
            return "VETOED", r

        # BROAD tier
        broad_baseline = v3t_proba >= 0.30 and mag in ("HI", "MID")
        broad_specialist = (
            sp.get("BROAD", -1.0) >= TIER_BROAD_THR and mag in ("HI", "MID")
        )
        if broad_baseline or broad_specialist:
            r = (f"v3t>=0.30 AND mag IN (HI, MID)" if broad_baseline
                 else f"specialist_BROAD({sp['BROAD']:.3f})>={TIER_BROAD_THR:.2f} "
                      f"AND mag IN (HI, MID) [D281 cascade]")
            return "BROAD", r

        return "SKIP", (f"v3t={v3t_proba:.3f} mag={mag} tcn={tcn_proba} "
                          + (f"specialists={sp}" if sp else ""))

    def predict_v4t(self, features: dict) -> dict[str, float]:
        """D282 — run candidate features through all 4 MoMTrans tier specialists.

        Returns ``{ELITE: p, HIGH: p, VETOED: p, BROAD: p}`` — the raw
        per-specialist probabilities. The cascade router (assign_tier_momtrans)
        applies the Phase-A-frozen UNION thresholds on top.

        Each specialist uses the SAME feature_columns / means / stds (set at
        SSL pre-training time), so we standardize once and reuse for all 4.
        """
        if not self._momtrans_models:
            return {}
        import torch
        # Build standardized feature row
        row = np.array([float(features.get(c, 0.0))
                          for c in self._momtrans_feature_columns], dtype=np.float32)
        means = np.asarray(self._momtrans_means, dtype=np.float32)
        stds = np.asarray(self._momtrans_stds, dtype=np.float32) + 1e-6
        row = (row - means) / stds
        tab = torch.from_numpy(row).unsqueeze(0)  # (1, F)
        # Sequence + regime are placeholder zeros — disable_sequence=True in
        # the trained model means seq_branch contribution is zero anyway, and
        # regime token is one of {0, 1, 2}; default MID (1) when unknown.
        seq_len = 30  # matches training SEQ_LEN
        seq_features = 6
        seq = torch.zeros(1, seq_len, seq_features, dtype=torch.float32)
        regime_idx = {"HI": 0, "MID": 1, "LO": 2}.get(self.mag_label, 1)
        regime = torch.tensor([regime_idx], dtype=torch.long)

        out = {}
        with torch.no_grad():
            for tier_name, model in self._momtrans_models.items():
                pred = model(tab, seq, regime)
                p = float(torch.sigmoid(pred["binary"]).item())
                out[tier_name] = p
        return out

    def assign_tier_momtrans(self, momtrans_probas: dict,
                                intraday_pct: Optional[float] = None) -> tuple[str, str]:
        """D282 — UNION cascade tier router using MoMTrans specialist probas.

        Phase-A-frozen thresholds: {ELITE 0.60, HIGH 0.70, VETOED 0.90,
        BROAD 0.60}. Cascade order: ELITE > HIGH > VETOED > BROAD.

        VETOED additionally requires mag=MID (matches the production v3
        path's VETOED constraint). All others require mag IN (HI, MID).
        """
        if not momtrans_probas:
            return "SKIP", "MoMTrans probas empty"
        mag = self.mag_label
        is_hi_mid = mag in ("HI", "MID")
        is_mid = mag == "MID"

        if momtrans_probas.get("ELITE", 0.0) >= MOMTRANS_ELITE_THR and is_hi_mid:
            return "ELITE", (f"momtrans_ELITE({momtrans_probas['ELITE']:.3f})"
                              f">={MOMTRANS_ELITE_THR:.2f} AND mag IN (HI,MID) [D282]")
        if momtrans_probas.get("HIGH", 0.0) >= MOMTRANS_HIGH_THR and is_hi_mid:
            return "HIGH", (f"momtrans_HIGH({momtrans_probas['HIGH']:.3f})"
                              f">={MOMTRANS_HIGH_THR:.2f} AND mag IN (HI,MID) [D282]")
        if momtrans_probas.get("VETOED", 0.0) >= MOMTRANS_VETOED_THR and is_mid:
            return "VETOED", (f"momtrans_VETOED({momtrans_probas['VETOED']:.3f})"
                                f">={MOMTRANS_VETOED_THR:.2f} AND mag=MID [D282]")
        if momtrans_probas.get("BROAD", 0.0) >= MOMTRANS_BROAD_THR and is_hi_mid:
            return "BROAD", (f"momtrans_BROAD({momtrans_probas['BROAD']:.3f})"
                               f">={MOMTRANS_BROAD_THR:.2f} AND mag IN (HI,MID) [D282]")
        return "SKIP", (f"momtrans probas {momtrans_probas} all below thresholds, mag={mag}")

    def compute_kelly(self, tier: str, v3t_proba: float, conformal_width: float,
                       bankroll: float = 0.0) -> float:
        """Conformal-modulated Kelly with per-tier cap.

        D290 (2026-05-11): when LOTTERY_USE_ADAPTIVE_KELLY=1 (default) and
        a non-zero bankroll is provided, use the adaptive cap from
        ADAPTIVE_KELLY_SCHEDULE. Otherwise falls back to the fixed
        KELLY_CAPS lookup (preserves opt-out and pre-D290 behavior).
        """
        if tier == "SKIP":
            return 0.0
        ew, el = TIER_EW_EL_PCT[tier]
        if abs(el) < 1e-6 or ew <= 0:
            return 0.0
        b = abs(ew / el)
        q = 1.0 - v3t_proba
        f_star = (b * v3t_proba - q) / b
        f_star = max(0.0, f_star)
        width_mod = float(np.exp(-2 * conformal_width))
        kelly = f_star * width_mod
        # D290: prefer adaptive cap when bankroll known; else fixed cap
        if bankroll > 0:
            cap = adaptive_kelly_cap(tier, bankroll)
        else:
            cap = KELLY_CAPS.get(tier, 0.0)
        return float(min(kelly, cap))

    def score_candidate(self, candidate: dict,
                          intraday_path: Optional[np.ndarray] = None,
                          bankroll: float = 10_000.0) -> MetaDecision:
        """End-to-end scoring. Returns a MetaDecision.

        intraday_pct is read from the candidate dict (key 'intraday_pct')
        when VETOED rule D is active; ignored under default rule E.

        D282 routing: when MoMTrans specialists are loaded (4/4 .pt files),
        the cascade router REPLACES the v3+D281 path. The MetaDecision's
        meta_score field carries the MAX MoMTrans specialist probability
        (across the 4 tiers) for visibility, with v3t_proba still computed
        and exposed in the reason string for debugging A/B comparisons.
        """
        ticker = str(candidate.get("ticker", "?"))
        v3t_proba, conf_w = self.predict_v3t(candidate)
        tcn_proba = self.predict_tcn(intraday_path)
        intraday_pct = candidate.get("intraday_pct")

        # D282: route via MoMTrans cascade when all 4 specialists loaded
        if len(self._momtrans_models) == 4:
            momtrans_probas = self.predict_v4t(candidate)
            tier, reason = self.assign_tier_momtrans(
                momtrans_probas, intraday_pct=intraday_pct,
            )
            # Use the WINNING-tier's specialist proba for Kelly sizing
            tier_proba = momtrans_probas.get(tier, v3t_proba) if tier != "SKIP" else 0.0
            # Floor proba at 0.31 so compute_kelly's "p < 0.30" check passes
            tier_proba_eff = max(tier_proba, 0.31) if tier != "SKIP" else tier_proba
            # D290: pass bankroll so adaptive_kelly_cap kicks in
            kelly = self.compute_kelly(tier, tier_proba_eff, conf_w, bankroll=bankroll)
            meta_score = max(momtrans_probas.values()) if momtrans_probas else 0.0
            reason = f"{reason} | v3t={v3t_proba:.3f} (shadow)"
        else:
            # Legacy path — v3 + D281 cohort cascade (unchanged behavior when
            # MoMTrans is OFF or partial)
            tier, reason = self.assign_tier(v3t_proba, tcn_proba,
                                              intraday_pct=intraday_pct)
            # D290: pass bankroll so adaptive_kelly_cap kicks in
            kelly = self.compute_kelly(tier, v3t_proba, conf_w, bankroll=bankroll)
            meta_score = v3t_proba

        notional = bankroll * kelly
        # D285: cap notional at MAX_PARTICIPATION_PCT × dvol_d0 (doc 142).
        # If dvol_d0 missing or <=0, cap doesn't apply (we can't compute
        # participation). The cap rarely binds at current paper-account
        # AUM but bounds impact at any future scaling.
        dvol_d0 = candidate.get("dvol_d0")
        bouchaud_cap_applied = False
        if (notional > 0 and dvol_d0 is not None
                and isinstance(dvol_d0, (int, float)) and dvol_d0 > 0
                and LOTTERY_MAX_PARTICIPATION_PCT < 1.0):
            cap = float(dvol_d0) * LOTTERY_MAX_PARTICIPATION_PCT
            if notional > cap:
                log.debug("D285 participation cap: %s tier=%s notional=$%.0f "
                          "exceeds %.1f%% of dvol_d0=$%.0f; capped to $%.0f",
                          ticker, tier, notional,
                          LOTTERY_MAX_PARTICIPATION_PCT * 100, dvol_d0, cap)
                notional = cap
                bouchaud_cap_applied = True
        # Update kelly_frac to reflect the post-cap notional
        kelly_effective = (notional / bankroll) if bankroll > 0 else kelly
        if bouchaud_cap_applied and "D285 cap" not in reason:
            reason = f"{reason} | D285 part-cap"
        return MetaDecision(
            ticker=ticker, tier=tier,
            meta_score=meta_score, kelly_frac=kelly_effective,
            notional_usd=round(notional, 2),
            conformal_width=conf_w,
            v3t_proba=v3t_proba, tcn_proba=tcn_proba,
            mag_label=self.mag_label, reason=reason,
        )


def cli():
    """Smoke-test entry point: print MetaDecision for a sample candidate."""
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", default="DEMO")
    p.add_argument("--bankroll", type=float, default=10_000.0)
    p.add_argument("--mag-5d", type=float, default=None)
    args = p.parse_args()

    scorer = MetaScorer.load_default(mag_5d=args.mag_5d)
    # Stub feature dict — fills 0 for missing fields
    cand = {
        "ticker": args.ticker, "intraday_pct": 0.40,
        "log_open": 0.5, "log_dvol_d0": 15.5,
        "rank_intra": 1, "prior_cont": 5, "prior_n": 10,
    }
    decision = scorer.score_candidate(cand, intraday_path=None, bankroll=args.bankroll)
    print(json.dumps(asdict(decision), indent=2))


if __name__ == "__main__":
    cli()
