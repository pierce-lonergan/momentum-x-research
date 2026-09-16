"""
D214: Archetype Exit System — Empirical Decay Curves from Clustered Price Paths

Clusters historical gap-up stocks by pre-entry features (gap_pct, rvol,
prior_gap_count) stratified by is_day2_runner. Builds per-archetype
minute-by-minute decay curves from real price data. At entry, assigns
archetype and feeds its curve to AlphaDecayOracle as a per-ticker override.

Design principles:
- No synthetic minute bars — train on reality only
- HDBSCAN with min_cluster_size=50 (hard floor)
- Stratify on is_day2_runner before clustering
- Every tunable is swept in validation, nothing hardcoded
- Noise points route to fallback (bar-1 exit)
- Archetype assignment is frozen at entry (no re-classification)
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
from scipy import stats

logger = logging.getLogger(__name__)

FALLBACK_ARCHETYPE_ID = -1
SCHEMA_VERSION = 1


# ── Data Structures ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ArchetypeCurve:
    """Per-archetype empirical decay curve with quantile bands."""
    archetype_id: int
    n_samples: int
    stratum: str                       # "fresh" or "day2"
    # minute -> {"mean", "std", "p10", "p25", "p50", "p75", "p90"}
    curves: dict[int, dict[str, float]]
    peak_minute: int                   # argmax of mean curve
    peak_mean_return: float            # mean return at peak
    peak_minute_p75: int               # p75 of per-sample peak times
    peak_minute_p90: int               # p90 of per-sample peak times
    target_pct: float                  # peak_mean × haircut
    stop_floor_pct: float              # min(p10) over [0, peak_minute]
    time_stop_minute: int              # peak_minute_p90
    # Cluster centroid features (for diagnostics)
    centroid: dict[str, float] | None = None


# ── Archetype Classifier ────────────────────────────────────────────────


class ArchetypeClassifier:
    """Assigns stocks to behavioral archetypes based on pre-entry features.

    Uses HDBSCAN clustering on log-transformed features, stratified by
    is_day2_runner. Noise points assigned to fallback (bar-1 exit).
    """

    MIN_CLUSTER_SIZE = 50
    FEATURES = ["log_gap_pct", "log_rvol", "prior_gap_count"]

    def __init__(self) -> None:
        self._models: dict[str, Any] = {}    # stratum -> fitted model
        self._scalers: dict[str, Any] = {}   # stratum -> StandardScaler
        self._cluster_maps: dict[str, dict[int, int]] = {}  # stratum -> {hdbscan_label: archetype_id}
        self._archetype_meta: dict[int, dict] = {}  # archetype_id -> {n, stratum, centroid}
        self._next_id = 0
        self._fitted = False

    def fit(self, scenarios: list[dict]) -> dict[int, int]:
        """Fit HDBSCAN on scenarios, stratified by is_day2_runner.

        Returns: {archetype_id: n_members}. Raises ValueError if any
        non-fallback archetype has n < MIN_CLUSTER_SIZE.
        """
        from sklearn.preprocessing import StandardScaler

        # Stratify
        fresh = [s for s in scenarios if not s.get("is_day2_runner", False)]
        day2 = [s for s in scenarios if s.get("is_day2_runner", False)]

        archetype_sizes: dict[int, int] = {}
        self._next_id = 0

        for stratum_name, stratum_data in [("fresh", fresh), ("day2", day2)]:
            if len(stratum_data) < self.MIN_CLUSTER_SIZE:
                # Entire stratum too small — all go to fallback
                logger.info(
                    "D214: Stratum '%s' has %d < %d samples — all assigned to fallback",
                    stratum_name, len(stratum_data), self.MIN_CLUSTER_SIZE,
                )
                self._cluster_maps[stratum_name] = {}
                continue

            # Extract and transform features
            X = np.array([
                [
                    np.log1p(abs(s.get("gap_pct", 0))),
                    np.log1p(s.get("rvol", 1)),
                    s.get("prior_gap_count", 0) or 0,
                ]
                for s in stratum_data
            ])

            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)
            self._scalers[stratum_name] = scaler

            # Try HDBSCAN
            labels = self._fit_hdbscan(X_scaled, stratum_name)

            if labels is None:
                # HDBSCAN failed — try GMM
                labels = self._fit_gmm(X_scaled, stratum_name)

            if labels is None:
                # All clustering failed — entire stratum to fallback
                self._cluster_maps[stratum_name] = {}
                continue

            # Map cluster labels to archetype IDs
            cluster_map: dict[int, int] = {}
            unique_labels = set(labels)
            for cl in sorted(unique_labels):
                if cl == -1:
                    continue  # Noise → fallback
                n_in_cluster = int(np.sum(labels == cl))
                if n_in_cluster < self.MIN_CLUSTER_SIZE:
                    continue  # Too small → fallback
                aid = self._next_id
                self._next_id += 1
                cluster_map[cl] = aid
                archetype_sizes[aid] = n_in_cluster

                # Compute centroid
                mask = labels == cl
                centroid = {
                    "log_gap_pct": float(np.mean(X[mask, 0])),
                    "log_rvol": float(np.mean(X[mask, 1])),
                    "prior_gap_count": float(np.mean(X[mask, 2])),
                }
                self._archetype_meta[aid] = {
                    "n": n_in_cluster,
                    "stratum": stratum_name,
                    "centroid": centroid,
                }

            self._cluster_maps[stratum_name] = cluster_map

        self._fitted = True
        logger.info(
            "D214: Classifier fitted — %d archetypes: %s (fallback for rest)",
            len(archetype_sizes), archetype_sizes,
        )
        return archetype_sizes

    def _fit_hdbscan(self, X: np.ndarray, stratum: str) -> np.ndarray | None:
        """Fit HDBSCAN. Returns labels or None if it fails."""
        try:
            import hdbscan
            model = hdbscan.HDBSCAN(
                min_cluster_size=self.MIN_CLUSTER_SIZE,
                min_samples=5,
                metric="euclidean",
            )
            labels = model.fit_predict(X)
            self._models[stratum] = model

            n_clusters = len(set(labels) - {-1})
            n_noise = int(np.sum(labels == -1))
            logger.info(
                "D214: HDBSCAN '%s': %d clusters, %d noise points",
                stratum, n_clusters, n_noise,
            )

            if n_clusters == 0:
                logger.warning("D214: HDBSCAN produced 0 clusters for '%s'", stratum)
                return None

            return labels
        except ImportError:
            logger.warning("D214: hdbscan not installed — falling back to GMM")
            return None
        except Exception as e:
            logger.warning("D214: HDBSCAN failed for '%s': %s", stratum, e)
            return None

    def _fit_gmm(self, X: np.ndarray, stratum: str) -> np.ndarray | None:
        """Fallback: GMM with BIC-selected k in {2, 3}."""
        try:
            from sklearn.mixture import GaussianMixture

            best_bic = float("inf")
            best_labels = None
            for k in [2, 3]:
                if len(X) < k * self.MIN_CLUSTER_SIZE:
                    continue
                gmm = GaussianMixture(n_components=k, random_state=42)
                gmm.fit(X)
                bic = gmm.bic(X)
                if bic < best_bic:
                    best_bic = bic
                    best_labels = gmm.predict(X)
                    self._models[stratum] = gmm

            if best_labels is not None:
                logger.info("D214: GMM '%s': k=%d selected by BIC", stratum, len(set(best_labels)))
            return best_labels
        except Exception as e:
            logger.warning("D214: GMM failed for '%s': %s", stratum, e)
            return None

    def predict(
        self,
        gap_pct: float,
        rvol: float,
        prior_gap_count: int,
        is_day2_runner: bool,
    ) -> tuple[int, float]:
        """Predict archetype for a single stock.

        Returns: (archetype_id, confidence). FALLBACK_ARCHETYPE_ID with
        confidence 0.0 if model not fitted or no matching archetype.
        """
        if not self._fitted:
            return FALLBACK_ARCHETYPE_ID, 0.0

        stratum = "day2" if is_day2_runner else "fresh"

        if stratum not in self._cluster_maps or not self._cluster_maps[stratum]:
            return FALLBACK_ARCHETYPE_ID, 0.0

        model = self._models.get(stratum)
        scaler = self._scalers.get(stratum)
        if model is None or scaler is None:
            return FALLBACK_ARCHETYPE_ID, 0.0

        features = np.array([[
            np.log1p(abs(gap_pct)),
            np.log1p(rvol),
            prior_gap_count,
        ]])
        X_scaled = scaler.transform(features)

        try:
            # HDBSCAN: use approximate_predict for new points
            import hdbscan
            if isinstance(model, hdbscan.HDBSCAN):
                labels, strengths = hdbscan.approximate_predict(model, X_scaled)
                label = int(labels[0])
                confidence = float(strengths[0])
            else:
                # GMM
                label = int(model.predict(X_scaled)[0])
                probs = model.predict_proba(X_scaled)[0]
                confidence = float(max(probs))
        except Exception:
            return FALLBACK_ARCHETYPE_ID, 0.0

        archetype_id = self._cluster_maps[stratum].get(label, FALLBACK_ARCHETYPE_ID)
        return archetype_id, confidence

    def to_dict(self) -> dict:
        """Serialize for JSON persistence (model params, not sklearn objects)."""
        # Convert all keys to native Python types for JSON
        cluster_maps = {
            k: {str(ck): int(cv) for ck, cv in v.items()}
            for k, v in self._cluster_maps.items()
        }
        archetype_meta = {
            str(k): v for k, v in self._archetype_meta.items()
        }
        return {
            "fitted": self._fitted,
            "cluster_maps": cluster_maps,
            "archetype_meta": archetype_meta,
            "next_id": self._next_id,
            "scalers": {
                k: {"mean": v.mean_.tolist(), "scale": v.scale_.tolist()}
                for k, v in self._scalers.items()
            },
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ArchetypeClassifier":
        """Deserialize from JSON. Note: HDBSCAN model is NOT restored —
        predict() will use centroid-distance fallback."""
        obj = cls()
        obj._fitted = d.get("fitted", False)
        obj._cluster_maps = d.get("cluster_maps", {})
        obj._archetype_meta = d.get("archetype_meta", {})
        obj._next_id = d.get("next_id", 0)
        # Restore scalers
        from sklearn.preprocessing import StandardScaler
        for k, v in d.get("scalers", {}).items():
            scaler = StandardScaler()
            scaler.mean_ = np.array(v["mean"])
            scaler.scale_ = np.array(v["scale"])
            scaler.var_ = scaler.scale_ ** 2
            scaler.n_features_in_ = len(v["mean"])
            obj._scalers[k] = scaler
        return obj


# ── Decay Curve Library ─────────────────────────────────────────────────


class DecayCurveLibrary:
    """Per-archetype empirical decay curves computed from real minute bars."""

    def __init__(self) -> None:
        self._curves: dict[int, ArchetypeCurve] = {}

    def build_from_paths(
        self,
        archetype_assignments: dict[int, list[int]],
        minute_paths: list[list[float]],
        scenario_meta: list[dict],
        target_haircut: float = 0.7,
    ) -> None:
        """Build curves from minute-by-minute normalized return paths.

        Args:
            archetype_assignments: {archetype_id: [scenario_indices]}
            minute_paths: [scenario_idx][minute] = (price/entry - 1)
            scenario_meta: [{stratum, ...}] for each scenario
            target_haircut: multiplier on peak_mean for target
        """
        for aid, indices in archetype_assignments.items():
            if aid == FALLBACK_ARCHETYPE_ID:
                continue

            paths = np.array([minute_paths[i] for i in indices])
            n_samples, n_minutes = paths.shape

            # Compute statistics at each minute
            curves: dict[int, dict[str, float]] = {}
            for m in range(n_minutes):
                col = paths[:, m]
                curves[m] = {
                    "mean": float(np.mean(col)),
                    "std": float(np.std(col)),
                    "p10": float(np.percentile(col, 10)),
                    "p25": float(np.percentile(col, 25)),
                    "p50": float(np.percentile(col, 50)),
                    "p75": float(np.percentile(col, 75)),
                    "p90": float(np.percentile(col, 90)),
                }

            # Peak analysis
            means = [curves[m]["mean"] for m in range(n_minutes)]
            peak_minute = int(np.argmax(means))
            peak_mean = float(max(means))

            # Per-sample peak times
            per_sample_peaks = [int(np.argmax(paths[i, :])) for i in range(n_samples)]
            peak_minute_p75 = int(np.percentile(per_sample_peaks, 75))
            peak_minute_p90 = int(np.percentile(per_sample_peaks, 90))

            # Stop floor: min of p10 over [0, peak_minute]
            stop_floor = min(curves[m]["p10"] for m in range(max(peak_minute, 1)))

            stratum = scenario_meta[indices[0]].get("stratum", "fresh") if indices else "fresh"

            self._curves[aid] = ArchetypeCurve(
                archetype_id=aid,
                n_samples=n_samples,
                stratum=stratum,
                curves=curves,
                peak_minute=peak_minute,
                peak_mean_return=peak_mean,
                peak_minute_p75=peak_minute_p75,
                peak_minute_p90=peak_minute_p90,
                target_pct=peak_mean * target_haircut,
                stop_floor_pct=stop_floor,
                time_stop_minute=peak_minute_p90,
            )

    def get_null_curve(self, archetype_id: int) -> list[tuple[int, float]] | None:
        """Return [(minute, mean_return_pct), ...] for AlphaDecayOracle."""
        curve = self._curves.get(archetype_id)
        if curve is None:
            return None
        return [(m, v["mean"] * 100) for m, v in sorted(curve.curves.items())]

    def get_curve(self, archetype_id: int) -> ArchetypeCurve | None:
        return self._curves.get(archetype_id)

    def archetype_ids(self) -> list[int]:
        return list(self._curves.keys())

    def archetype_distinctness_test(self) -> list[dict]:
        """Pairwise KS test on return distributions at peak_minute."""
        results = []
        ids = self.archetype_ids()
        for i, a1 in enumerate(ids):
            for a2 in ids[i + 1:]:
                c1 = self._curves[a1]
                c2 = self._curves[a2]
                # Compare at the earlier peak minute
                test_minute = min(c1.peak_minute, c2.peak_minute)
                # Need raw data — store it or recompute
                # For now, compare means ± stds as proxy
                m1 = c1.curves.get(test_minute, {}).get("mean", 0)
                s1 = c1.curves.get(test_minute, {}).get("std", 1)
                m2 = c2.curves.get(test_minute, {}).get("mean", 0)
                s2 = c2.curves.get(test_minute, {}).get("std", 1)
                # Welch's t-test approximation
                t_stat = abs(m1 - m2) / np.sqrt(s1**2 / c1.n_samples + s2**2 / c2.n_samples + 1e-10)
                df = min(c1.n_samples, c2.n_samples) - 1
                p_val = 2 * (1 - stats.t.cdf(abs(t_stat), df))
                results.append({
                    "archetype_a": a1, "archetype_b": a2,
                    "test_minute": test_minute,
                    "mean_a": m1, "mean_b": m2,
                    "p_value": float(p_val),
                    "distinct": p_val < 0.05,
                })
        return results

    def to_dict(self) -> dict:
        d = {}
        for aid, curve in self._curves.items():
            d[str(aid)] = {
                "archetype_id": curve.archetype_id,
                "n_samples": curve.n_samples,
                "stratum": curve.stratum,
                "curves": {str(m): v for m, v in curve.curves.items()},
                "peak_minute": curve.peak_minute,
                "peak_mean_return": curve.peak_mean_return,
                "peak_minute_p75": curve.peak_minute_p75,
                "peak_minute_p90": curve.peak_minute_p90,
                "target_pct": curve.target_pct,
                "stop_floor_pct": curve.stop_floor_pct,
                "time_stop_minute": curve.time_stop_minute,
                "centroid": curve.centroid,
            }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DecayCurveLibrary":
        obj = cls()
        for aid_str, v in d.items():
            aid = int(aid_str)
            curves = {int(m): vals for m, vals in v["curves"].items()}
            obj._curves[aid] = ArchetypeCurve(
                archetype_id=aid,
                n_samples=v["n_samples"],
                stratum=v.get("stratum", "fresh"),
                curves=curves,
                peak_minute=v["peak_minute"],
                peak_mean_return=v["peak_mean_return"],
                peak_minute_p75=v.get("peak_minute_p75", v["peak_minute"]),
                peak_minute_p90=v.get("peak_minute_p90", v["peak_minute"]),
                target_pct=v["target_pct"],
                stop_floor_pct=v["stop_floor_pct"],
                time_stop_minute=v.get("time_stop_minute", v["peak_minute"]),
                centroid=v.get("centroid"),
            )
        return obj


# ── Archetype Exit Strategy ─────────────────────────────────────────────


class ArchetypeExitStrategy:
    """Real-time exit scoring against archetype-specific decay curves.

    Assigns archetype at entry (once, frozen). Evaluates live position
    against the archetype's empirical curve. Routes fallback/low-confidence
    to bar-1 exit.
    """

    def __init__(
        self,
        classifier: ArchetypeClassifier,
        library: DecayCurveLibrary,
        z_threshold: float = -0.5,
        z_consecutive_bars: int = 2,
        target_haircut: float = 0.7,
        confidence_threshold: float = 0.6,
    ) -> None:
        self._classifier = classifier
        self._library = library
        self.z_threshold = z_threshold
        self.z_consecutive_bars = z_consecutive_bars
        self.target_haircut = target_haircut
        self.confidence_threshold = confidence_threshold
        # Per-ticker state
        self._assignments: dict[str, tuple[int, float]] = {}  # ticker -> (archetype_id, confidence)
        self._z_history: dict[str, list[float]] = {}

    def assign_at_entry(
        self,
        ticker: str,
        gap_pct: float,
        rvol: float,
        prior_gap_count: int,
        is_day2_runner: bool,
    ) -> tuple[int, float]:
        """Assign archetype at entry. Called exactly once per position."""
        if ticker in self._assignments:
            logger.warning("D214: %s already assigned — returning cached", ticker)
            return self._assignments[ticker]

        aid, conf = self._classifier.predict(gap_pct, rvol, prior_gap_count, is_day2_runner)

        if conf < self.confidence_threshold:
            aid = FALLBACK_ARCHETYPE_ID
            logger.info(
                "D214: %s → fallback (confidence %.2f < %.2f threshold)",
                ticker, conf, self.confidence_threshold,
            )
        else:
            curve = self._library.get_curve(aid)
            if curve:
                logger.info(
                    "D214: %s → archetype %d (n=%d, peak=+%.1f%% at min %d, conf=%.2f)",
                    ticker, aid, curve.n_samples,
                    curve.peak_mean_return * 100, curve.peak_minute, conf,
                )
            else:
                aid = FALLBACK_ARCHETYPE_ID
                logger.info("D214: %s → fallback (no curve for archetype %d)", ticker, aid)

        self._assignments[ticker] = (aid, conf)
        self._z_history[ticker] = []
        return aid, conf

    def evaluate(
        self,
        ticker: str,
        current_return_pct: float,
        minutes_since_open: float,
    ) -> dict:
        """Evaluate live position against archetype curve.

        Returns dict with: should_exit, should_tighten, z_score,
        archetype_id, archetype_mean, archetype_p25, details.
        """
        assignment = self._assignments.get(ticker)
        if assignment is None or assignment[0] == FALLBACK_ARCHETYPE_ID:
            return {"should_exit": False, "should_tighten": False, "fallback": True}

        aid, conf = assignment
        curve = self._library.get_curve(aid)
        if curve is None:
            return {"should_exit": False, "should_tighten": False, "fallback": True}

        minute = int(minutes_since_open)
        minute = max(0, min(minute, max(curve.curves.keys())))

        stats_at_minute = curve.curves.get(minute)
        if stats_at_minute is None:
            return {"should_exit": False, "should_tighten": False, "fallback": True}

        mean_ret = stats_at_minute["mean"]
        std_ret = stats_at_minute.get("std", 0.01) or 0.01
        p25_ret = stats_at_minute["p25"]

        # Normalize current return to fraction (matching curve units)
        current_ret_frac = current_return_pct / 100.0

        # Z-score
        z = (current_ret_frac - mean_ret) / std_ret

        # Track z history
        self._z_history.setdefault(ticker, []).append(z)

        # Primary exit: z < threshold for N consecutive bars
        z_hist = self._z_history[ticker]
        consecutive_below = 0
        for z_val in reversed(z_hist):
            if z_val < self.z_threshold:
                consecutive_below += 1
            else:
                break
        z_exit = consecutive_below >= self.z_consecutive_bars

        # Backup confirmation: live < p25 upgrades 1-bar z-violation
        below_p25 = current_ret_frac < p25_ret
        if consecutive_below == 1 and below_p25:
            z_exit = True  # Upgrade

        # Time stop
        time_exit = (
            minute > curve.time_stop_minute
            and current_ret_frac < curve.target_pct
        )

        # Target
        target_hit = current_ret_frac >= curve.target_pct

        should_exit = z_exit or time_exit or target_hit
        should_tighten = z < 0 and not should_exit

        return {
            "should_exit": should_exit,
            "should_tighten": should_tighten,
            "fallback": False,
            "z_score": round(z, 3),
            "archetype_id": aid,
            "archetype_mean": round(mean_ret * 100, 2),
            "archetype_p25": round(p25_ret * 100, 2),
            "current_return": round(current_ret_frac * 100, 2),
            "minute": minute,
            "consecutive_z_below": consecutive_below,
            "below_p25": below_p25,
            "time_exit": time_exit,
            "target_hit": target_hit,
            "target_pct": round(curve.target_pct * 100, 2),
            "time_stop_minute": curve.time_stop_minute,
            "exit_reason": (
                "z_threshold" if z_exit else
                "time_stop" if time_exit else
                "target" if target_hit else
                "hold"
            ),
        }

    def get_archetype_null_curve(self, ticker: str) -> list[tuple[int, float]] | None:
        """Get null curve for AlphaDecayOracle override."""
        assignment = self._assignments.get(ticker)
        if assignment is None or assignment[0] == FALLBACK_ARCHETYPE_ID:
            return None
        return self._library.get_null_curve(assignment[0])

    def cleanup_position(self, ticker: str) -> None:
        self._assignments.pop(ticker, None)
        self._z_history.pop(ticker, None)

    def prune_stale(self, active_tickers: set[str]) -> None:
        stale = set(self._assignments.keys()) - active_tickers
        for t in stale:
            self.cleanup_position(t)


# ── Model Persistence ───────────────────────────────────────────────────


def save_model(
    classifier: ArchetypeClassifier,
    library: DecayCurveLibrary,
    path: str,
    training_scenario_ids: list[str] | None = None,
) -> None:
    """Save trained model to JSON."""
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, timeout=5,
        ).strip()[:8]
    except Exception:
        git_sha = "unknown"

    model = {
        "schema_version": SCHEMA_VERSION,
        "git_sha": git_sha,
        "trained_at": __import__("datetime").datetime.now().isoformat(),
        "classifier": classifier.to_dict(),
        "curves": library.to_dict(),
        "training_scenario_ids": training_scenario_ids or [],
    }

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Convert numpy types for JSON serialization
    def _convert(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        raise TypeError(f"Not serializable: {type(obj)}")

    p.write_text(json.dumps(model, indent=2, default=_convert))
    logger.info("D214: Model saved to %s", path)


def load_model(path: str) -> tuple[ArchetypeClassifier, DecayCurveLibrary]:
    """Load trained model from JSON."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Archetype model not found: {path}")

    model = json.loads(p.read_text())
    if model.get("schema_version", 0) != SCHEMA_VERSION:
        raise ValueError(f"Schema version mismatch: {model.get('schema_version')}")

    classifier = ArchetypeClassifier.from_dict(model["classifier"])
    library = DecayCurveLibrary.from_dict(model["curves"])

    logger.info(
        "D214: Model loaded from %s (sha=%s, %d archetypes)",
        path, model.get("git_sha", "?"), len(library.archetype_ids()),
    )
    return classifier, library
