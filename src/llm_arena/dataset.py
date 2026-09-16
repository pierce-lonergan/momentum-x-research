"""DatasetManager — stores, loads, and queries labeled scenarios."""

import json
import os
import random
from collections import defaultdict
from datetime import date
from typing import Optional

from .models import CatalystType, LabelConfidence, LabeledScenario, StockOutcome


class DatasetManager:
    """Manages the labeled scenario dataset.

    Storage layout (under data_dir):
        scenarios_YYYY-MM-DD.json   — one file per date, list of scenario dicts
        index.json                  — {scenario_id: {date, ticker, outcome, catalyst_type, label_confidence}}
    """

    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self._scenarios: dict[str, LabeledScenario] = {}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> int:
        """Load all scenarios from disk. Returns count loaded."""
        self._scenarios = {}
        index_path = os.path.join(self._data_dir, "index.json")
        if not os.path.exists(index_path):
            return 0

        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)

        dates_to_load: set[str] = set()
        for entry in index.values():
            dates_to_load.add(entry["date"])

        for date_str in dates_to_load:
            file_path = os.path.join(self._data_dir, f"scenarios_{date_str}.json")
            if not os.path.exists(file_path):
                continue
            with open(file_path, "r", encoding="utf-8") as f:
                rows = json.load(f)
            for row in rows:
                scenario = LabeledScenario.from_dict(row)
                self._scenarios[scenario.scenario_id] = scenario

        return len(self._scenarios)

    def save(self):
        """Persist all scenarios to disk (one file per date + index)."""
        by_date: dict[str, list] = defaultdict(list)
        for scenario in self._scenarios.values():
            date_str = scenario.date.isoformat() if isinstance(scenario.date, date) else scenario.date
            by_date[date_str].append(scenario.to_dict())

        for date_str, rows in by_date.items():
            file_path = os.path.join(self._data_dir, f"scenarios_{date_str}.json")
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(rows, f, indent=2)

        index: dict[str, dict] = {}
        for sid, scenario in self._scenarios.items():
            date_str = scenario.date.isoformat() if isinstance(scenario.date, date) else str(scenario.date)
            index[sid] = {
                "date": date_str,
                "ticker": scenario.ticker,
                "outcome": scenario.outcome.value if isinstance(scenario.outcome, StockOutcome) else scenario.outcome,
                "catalyst_type": scenario.catalyst_type.value if isinstance(scenario.catalyst_type, CatalystType) else scenario.catalyst_type,
                "label_confidence": scenario.label_confidence.value if isinstance(scenario.label_confidence, LabelConfidence) else scenario.label_confidence,
            }

        index_path = os.path.join(self._data_dir, "index.json")
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_scenario(self, scenario: LabeledScenario) -> bool:
        """Add or update a scenario. Returns True if new (not an update)."""
        is_new = scenario.scenario_id not in self._scenarios
        self._scenarios[scenario.scenario_id] = scenario
        return is_new

    def get(self, scenario_id: str) -> Optional[LabeledScenario]:
        """Retrieve a scenario by ID."""
        return self._scenarios.get(scenario_id)

    def all(self) -> list[LabeledScenario]:
        """Return all scenarios."""
        return list(self._scenarios.values())

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def filter(
        self,
        catalyst_type: Optional[CatalystType] = None,
        outcome: Optional[StockOutcome] = None,
        label_confidence: Optional[LabelConfidence] = None,
        min_gap_pct: Optional[float] = None,
        date_range: Optional[tuple] = None,
        was_traded: Optional[bool] = None,
    ) -> list[LabeledScenario]:
        """Filter scenarios by one or more criteria."""
        results = list(self._scenarios.values())

        if catalyst_type is not None:
            results = [s for s in results if s.catalyst_type == catalyst_type]

        if outcome is not None:
            results = [s for s in results if s.outcome == outcome]

        if label_confidence is not None:
            results = [s for s in results if s.label_confidence == label_confidence]

        if min_gap_pct is not None:
            results = [s for s in results if s.gap_pct is not None and s.gap_pct >= min_gap_pct]

        if date_range is not None:
            start, end = date_range
            results = [
                s for s in results
                if start <= (s.date if isinstance(s.date, date) else date.fromisoformat(str(s.date))) <= end
            ]

        if was_traded is not None:
            results = [s for s in results if s.was_traded == was_traded]

        return results

    # ------------------------------------------------------------------
    # Split
    # ------------------------------------------------------------------

    def split(
        self, test_pct: float = 0.20, seed: int = 42
    ) -> tuple[list[LabeledScenario], list[LabeledScenario]]:
        """Stratified train/test split preserving outcome distribution.

        Returns (train_list, test_list).
        """
        rng = random.Random(seed)
        by_outcome: dict[str, list[LabeledScenario]] = defaultdict(list)
        for s in self._scenarios.values():
            key = s.outcome.value if isinstance(s.outcome, StockOutcome) else str(s.outcome)
            by_outcome[key].append(s)

        train: list[LabeledScenario] = []
        test: list[LabeledScenario] = []

        for bucket in by_outcome.values():
            shuffled = list(bucket)
            rng.shuffle(shuffled)
            n_test = max(1, int(len(shuffled) * test_pct)) if len(shuffled) > 1 else 0
            test.extend(shuffled[:n_test])
            train.extend(shuffled[n_test:])

        return train, test

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """Return dataset statistics."""
        total = len(self._scenarios)

        by_catalyst: dict[str, int] = defaultdict(int)
        by_outcome: dict[str, int] = defaultdict(int)
        by_confidence: dict[str, int] = defaultdict(int)

        traded = 0
        wins = 0

        for s in self._scenarios.values():
            cat = s.catalyst_type.value if isinstance(s.catalyst_type, CatalystType) else str(s.catalyst_type)
            out = s.outcome.value if isinstance(s.outcome, StockOutcome) else str(s.outcome)
            conf = s.label_confidence.value if isinstance(s.label_confidence, LabelConfidence) else str(s.label_confidence)

            by_catalyst[cat] += 1
            by_outcome[out] += 1
            by_confidence[conf] += 1

            if s.was_traded:
                traded += 1
                if s.trade_pnl is not None and s.trade_pnl > 0:
                    wins += 1

        return {
            "total": total,
            "by_catalyst_type": dict(by_catalyst),
            "by_outcome": dict(by_outcome),
            "by_label_confidence": dict(by_confidence),
            "traded": traded,
            "trade_wins": wins,
            "trade_win_rate": round(wins / traded, 3) if traded > 0 else 0.0,
        }

    # ------------------------------------------------------------------
    # Agent export
    # ------------------------------------------------------------------

    def export_for_agent(self, scenario_id: str) -> dict:
        """Export a scenario in the format an agent receives in production.

        Strips ground-truth fields so the agent can be evaluated blind.
        """
        scenario = self._scenarios.get(scenario_id)
        if scenario is None:
            raise KeyError(f"Scenario not found: {scenario_id}")

        return {
            "ticker": scenario.ticker,
            "date": scenario.date.isoformat() if isinstance(scenario.date, date) else str(scenario.date),
            "gap_pct": scenario.gap_pct,
            "rvol": scenario.rvol,
            "dollar_volume": scenario.dollar_volume,
            "open_price": scenario.open_price,
            "prev_close": scenario.prev_close,
            "premarket_headlines": scenario.premarket_headlines,
            "sec_filings": scenario.sec_filings,
            # Ground truth intentionally excluded:
            #   catalyst_type, outcome, correct_signal, trade_pnl, etc.
        }
