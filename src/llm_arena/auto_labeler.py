"""AutoLabeler — builds labeled scenarios from existing data sources."""

import glob
import json
import os
import re
from datetime import date, datetime
from typing import Optional

from .models import (
    CatalystType,
    CorrectSignal,
    LabelConfidence,
    LabeledScenario,
    StockOutcome,
)


def _make_id(ticker: str, d: date) -> str:
    date_str = d.isoformat() if isinstance(d, date) else str(d)
    return f"{ticker}_{date_str}"


def _parse_date(s: str) -> date:
    return date.fromisoformat(s[:10])


class AutoLabeler:
    """Automatically labels scenarios from existing Momentum-X data sources.

    Data directory structure expected:
        data/journals/          — journal_*.jsonl files
        data/trade_results.jsonl
        data/session_reports/   — session_*.json
        data/selection_arena/   — universe_*.json
        data/scenarios/         — gap_scenarios.json
        data/premarket/         — premarket_*.json
    """

    def __init__(self, data_dir: str):
        self._data_dir = data_dir

    # ------------------------------------------------------------------
    # Source parsers
    # ------------------------------------------------------------------

    def label_from_journals(self) -> list[LabeledScenario]:
        """Extract scenarios from all journal JSONL files."""
        journal_dir = os.path.join(self._data_dir, "journals")
        if not os.path.isdir(journal_dir):
            return []

        scenarios: dict[str, LabeledScenario] = {}

        for path in sorted(glob.glob(os.path.join(journal_dir, "journal_*.jsonl"))):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    ticker = entry.get("ticker", "")
                    session_date_str = entry.get("session_date", "")
                    if not ticker or not session_date_str:
                        continue

                    try:
                        session_date = _parse_date(session_date_str)
                    except ValueError:
                        continue

                    sid = _make_id(ticker, session_date)

                    # Build or update the scenario for this ticker+date
                    if sid not in scenarios:
                        scenario = LabeledScenario(
                            ticker=ticker,
                            date=session_date,
                            scenario_id=sid,
                            label_source="auto_journal",
                            created_at=datetime.utcnow(),
                            updated_at=datetime.utcnow(),
                        )
                    else:
                        scenario = scenarios[sid]

                    # Premarket inputs
                    scenario.gap_pct = entry.get("gap_pct", scenario.gap_pct)
                    scenario.rvol = entry.get("rvol", scenario.rvol)
                    scenario.open_price = entry.get("current_price", scenario.open_price)
                    scenario.prev_close = entry.get("previous_close", scenario.prev_close)

                    input_data = entry.get("input_data", {})

                    # Headlines
                    headlines = input_data.get("news_headlines", [])
                    if headlines:
                        scenario.premarket_headlines = headlines

                    # SEC filings
                    filing_types = input_data.get("sec_filing_types", [])
                    if filing_types:
                        scenario.sec_filings = filing_types

                    # Dollar volume approximation: open * premarket_volume
                    premarket_vol = entry.get("premarket_volume")
                    if premarket_vol and scenario.open_price:
                        scenario.dollar_volume = premarket_vol * scenario.open_price

                    # Agent signals
                    agent_signals = entry.get("agent_signals", [])
                    for sig in agent_signals:
                        agent_id = sig.get("agent_id", "")
                        scenario.actual_agent_signals[agent_id] = {
                            "signal": sig.get("signal"),
                            "confidence": sig.get("confidence"),
                            "reasoning": sig.get("reasoning", ""),
                            "catalyst_type": sig.get("catalyst_type"),
                            "sentiment_score": sig.get("sentiment_score"),
                        }

                    scenario.actual_mfcs = entry.get("mfcs", scenario.actual_mfcs)

                    # Faller score (may be in key_data of faller or technical agent)
                    for sig in agent_signals:
                        kd = sig.get("key_data", {})
                        if "faller_score" in kd:
                            scenario.actual_faller_score = kd["faller_score"]
                            break

                    # Trade decision
                    action = entry.get("action", "")
                    direction = entry.get("direction", "")
                    if action == "BUY" or action == "SHORT":
                        scenario.was_traded = True
                        scenario.trade_direction = direction if direction else (
                            "long" if action == "BUY" else "short"
                        )
                    elif action in ("REJECT", "REJECT_SHORT"):
                        # Only mark as not-traded if not already set to traded
                        if not scenario.was_traded:
                            scenario.was_traded = False

                    scenario.updated_at = datetime.utcnow()
                    scenarios[sid] = scenario

        return list(scenarios.values())

    def label_from_trade_results(
        self, scenarios: list[LabeledScenario]
    ) -> list[LabeledScenario]:
        """Enrich scenarios with P&L from trade_results.jsonl."""
        path = os.path.join(self._data_dir, "trade_results.jsonl")
        if not os.path.exists(path):
            return scenarios

        by_id: dict[str, LabeledScenario] = {s.scenario_id: s for s in scenarios}

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ticker = entry.get("ticker", "")
                session_date_str = entry.get("session_date", "")
                if not ticker or not session_date_str:
                    continue

                try:
                    session_date = _parse_date(session_date_str)
                except ValueError:
                    continue

                sid = _make_id(ticker, session_date)

                if sid not in by_id:
                    # Trade exists but no journal entry — create stub
                    scenario = LabeledScenario(
                        ticker=ticker,
                        date=session_date,
                        scenario_id=sid,
                        was_traded=True,
                        label_source="auto_trade_result",
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                    by_id[sid] = scenario
                else:
                    scenario = by_id[sid]

                scenario.was_traded = True
                scenario.trade_pnl = entry.get("pnl")
                # Infer direction from catalyst_type field (usually "unknown")
                # Direction already set from journal if available

        return list(by_id.values())

    def label_from_session_reports(
        self, scenarios: list[LabeledScenario]
    ) -> list[LabeledScenario]:
        """Enrich with session-level context (currently adds no per-scenario data)."""
        # Session reports contain aggregate stats, not per-ticker data.
        # Kept for future enrichment (e.g., session-level market regime flags).
        return scenarios

    def label_from_universe_files(
        self, scenarios: list[LabeledScenario]
    ) -> list[LabeledScenario]:
        """Enrich with Selection Arena universe data (confirmed price outcomes)."""
        arena_dir = os.path.join(self._data_dir, "selection_arena")
        if not os.path.isdir(arena_dir):
            return scenarios

        by_id: dict[str, LabeledScenario] = {s.scenario_id: s for s in scenarios}

        for path in sorted(glob.glob(os.path.join(arena_dir, "universe_*.json"))):
            with open(path, "r", encoding="utf-8") as f:
                universe = json.load(f)

            date_str = universe.get("date", "")
            if not date_str:
                continue
            try:
                universe_date = _parse_date(date_str)
            except ValueError:
                continue

            for stock in universe.get("stocks", []):
                ticker = stock.get("ticker", "")
                if not ticker:
                    continue

                sid = _make_id(ticker, universe_date)

                if sid not in by_id:
                    scenario = LabeledScenario(
                        ticker=ticker,
                        date=universe_date,
                        scenario_id=sid,
                        label_source="auto_universe",
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                    by_id[sid] = scenario
                else:
                    scenario = by_id[sid]

                scenario.open_price = stock.get("open_price", scenario.open_price)
                scenario.prev_close = stock.get("previous_close", scenario.prev_close)
                scenario.gap_pct = stock.get("gap_pct", scenario.gap_pct)
                scenario.rvol = stock.get("rvol_at_open", scenario.rvol)
                scenario.dollar_volume = stock.get("dollar_volume", scenario.dollar_volume)
                scenario.high_price = stock.get("high", scenario.high_price)
                scenario.low_price = stock.get("low", scenario.low_price)
                scenario.close_price = stock.get("close", scenario.close_price)

                # max_gain from open
                max_gain = stock.get("max_gain_from_open")
                if max_gain is not None:
                    scenario.max_gain_pct = max_gain

                # Compute price-based fields if we have high/low/close/open
                if scenario.open_price:
                    if scenario.high_price:
                        gain = (scenario.high_price - scenario.open_price) / scenario.open_price
                        if scenario.max_gain_pct is None:
                            scenario.max_gain_pct = gain
                    if scenario.low_price:
                        dd = (scenario.low_price - scenario.open_price) / scenario.open_price
                        scenario.max_drawdown_pct = dd
                    if scenario.close_price:
                        scenario.close_pct = (scenario.close_price - scenario.open_price) / scenario.open_price

                # Journal action from universe file
                journal_action = stock.get("journal_action", "")
                if journal_action in ("BUY", "SHORT") and not scenario.was_traded:
                    scenario.was_traded = True
                    scenario.trade_direction = "long" if journal_action == "BUY" else "short"

                scenario.updated_at = datetime.utcnow()

        # Also enrich from gap_scenarios.json (historical backtest data)
        gap_path = os.path.join(self._data_dir, "scenarios", "gap_scenarios.json")
        if os.path.exists(gap_path):
            with open(gap_path, "r", encoding="utf-8") as f:
                gap_data = json.load(f)

            for stock in gap_data.get("scenarios", []):
                ticker = stock.get("ticker", "")
                date_str = stock.get("date", "")
                if not ticker or not date_str:
                    continue
                try:
                    stock_date = _parse_date(date_str)
                except ValueError:
                    continue

                sid = _make_id(ticker, stock_date)

                if sid not in by_id:
                    scenario = LabeledScenario(
                        ticker=ticker,
                        date=stock_date,
                        scenario_id=sid,
                        label_source="auto_gap_scenarios",
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                    by_id[sid] = scenario
                else:
                    scenario = by_id[sid]

                scenario.open_price = stock.get("open_price", scenario.open_price)
                scenario.prev_close = stock.get("prev_close", scenario.prev_close)
                scenario.gap_pct = stock.get("gap_pct", scenario.gap_pct)
                scenario.rvol = stock.get("rvol", scenario.rvol)
                scenario.high_price = stock.get("high", scenario.high_price)
                scenario.low_price = stock.get("low", scenario.low_price)
                scenario.close_price = stock.get("close", scenario.close_price)

                high_from_open = stock.get("high_from_open_pct")
                low_from_open = stock.get("low_from_open_pct")
                intraday_return = stock.get("intraday_return")

                if high_from_open is not None and scenario.max_gain_pct is None:
                    scenario.max_gain_pct = high_from_open
                if low_from_open is not None:
                    scenario.max_drawdown_pct = low_from_open
                if intraday_return is not None:
                    scenario.close_pct = intraday_return

                scenario.updated_at = datetime.utcnow()

        return list(by_id.values())

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    _FDA_KEYWORDS = re.compile(
        r"\bfda\b|approval|approv|phase\s*[123]|clinical\s*trial|nda\b|bla\b|pdufa",
        re.IGNORECASE,
    )
    _EARNINGS_KEYWORDS = re.compile(
        r"\bearnings\b|revenue|eps\b|quarterly\s*results|q[1-4]\s+20\d\d|profit|loss\s+per\s+share",
        re.IGNORECASE,
    )
    _CONTRACT_KEYWORDS = re.compile(
        r"\bcontract\b|awarded|agreement|partnership|deal\b",
        re.IGNORECASE,
    )
    _MERGER_KEYWORDS = re.compile(
        r"\bmerger\b|acquisition|acqui|buyout|takeover|tender\s+offer",
        re.IGNORECASE,
    )
    _PHARMA_KEYWORDS = re.compile(
        r"\bpharma\b|biotech|bioscience|therapeutics|drug|compound|treatment",
        re.IGNORECASE,
    )
    _DILUTION_FILINGS = {"424B5", "S-1", "S-3", "424B3", "424B4"}
    _SQUEEZE_KEYWORDS = re.compile(
        r"\bsqueeze\b|short\s+squeeze|high\s+short\s+interest",
        re.IGNORECASE,
    )

    def auto_classify_catalyst(self, scenario: LabeledScenario) -> LabeledScenario:
        """Auto-classify catalyst type from headlines and SEC filings."""
        all_text = " ".join(scenario.premarket_headlines) + " " + scenario.catalyst_text

        # SEC filing takes precedence for dilution plays
        if any(f in self._DILUTION_FILINGS for f in scenario.sec_filings):
            scenario.catalyst_type = CatalystType.SEC_FILING
            return scenario

        if self._FDA_KEYWORDS.search(all_text):
            scenario.catalyst_type = CatalystType.FDA
            return scenario

        if self._EARNINGS_KEYWORDS.search(all_text):
            scenario.catalyst_type = CatalystType.EARNINGS
            return scenario

        if self._MERGER_KEYWORDS.search(all_text):
            scenario.catalyst_type = CatalystType.MERGER
            return scenario

        if self._CONTRACT_KEYWORDS.search(all_text):
            scenario.catalyst_type = CatalystType.CONTRACT
            return scenario

        if self._SQUEEZE_KEYWORDS.search(all_text):
            scenario.catalyst_type = CatalystType.SQUEEZE
            return scenario

        if self._PHARMA_KEYWORDS.search(all_text):
            scenario.catalyst_type = CatalystType.PHARMA_DEAL
            return scenario

        # Heuristic: no headlines + extreme RVOL likely promotional
        if (
            not scenario.premarket_headlines
            and scenario.rvol is not None
            and scenario.rvol > 10.0
        ):
            scenario.catalyst_type = CatalystType.PROMOTIONAL
            return scenario

        # Check news_agent signal for M&A / promotional
        news_sig = scenario.actual_agent_signals.get("news_agent", {})
        cat = news_sig.get("catalyst_type", "")
        if cat == "M_AND_A":
            scenario.catalyst_type = CatalystType.MERGER
            return scenario

        scenario.catalyst_type = CatalystType.UNKNOWN
        return scenario

    def auto_classify_outcome(self, scenario: LabeledScenario) -> LabeledScenario:
        """Auto-classify outcome from price data."""
        gain = scenario.max_gain_pct
        dd = scenario.max_drawdown_pct   # negative number or None
        close_pct = scenario.close_pct

        if gain is None and dd is None:
            # Fallback: use trade P&L sign if available
            if scenario.trade_pnl is not None:
                if scenario.trade_pnl > 100:
                    scenario.outcome = StockOutcome.RUNNER
                elif scenario.trade_pnl < -500:
                    scenario.outcome = StockOutcome.FADER
                else:
                    scenario.outcome = StockOutcome.FLAT
            return scenario

        gain = gain or 0.0
        dd_abs = abs(dd) if dd is not None else 0.0
        close_vs_open = close_pct if close_pct is not None else 0.0

        # RUNNER: strong sustained gain
        if gain >= 0.20 and close_vs_open > 0:
            scenario.outcome = StockOutcome.RUNNER
        # FADER: big drawdown, closed below open
        elif dd_abs >= 0.20 and close_vs_open < 0:
            scenario.outcome = StockOutcome.FADER
        # MIXED: ran then faded
        elif gain >= 0.10 and dd_abs >= 0.15:
            scenario.outcome = StockOutcome.MIXED
        # Additional FADER heuristic from gap_scenarios WIN/LOSS
        elif close_vs_open <= -0.05:
            scenario.outcome = StockOutcome.FADER
        elif close_vs_open >= 0.05:
            scenario.outcome = StockOutcome.RUNNER
        else:
            scenario.outcome = StockOutcome.FLAT

        return scenario

    def auto_set_correct_signal(self, scenario: LabeledScenario) -> LabeledScenario:
        """Set the correct agent signal and confidence range based on outcome+catalyst."""
        outcome = scenario.outcome
        catalyst = scenario.catalyst_type

        if outcome == StockOutcome.RUNNER:
            if catalyst in (
                CatalystType.FDA,
                CatalystType.MERGER,
                CatalystType.CONTRACT,
                CatalystType.EARNINGS,
                CatalystType.PHARMA_DEAL,
            ):
                scenario.correct_signal = CorrectSignal.STRONG_BULL
                scenario.correct_confidence_min = 0.70
                scenario.correct_confidence_max = 0.90
            else:
                scenario.correct_signal = CorrectSignal.BULL
                scenario.correct_confidence_min = 0.50
                scenario.correct_confidence_max = 0.75

        elif outcome == StockOutcome.FADER:
            if catalyst in (CatalystType.PROMOTIONAL, CatalystType.SEC_FILING):
                scenario.correct_signal = CorrectSignal.STRONG_BEAR
                scenario.correct_confidence_min = 0.70
                scenario.correct_confidence_max = 0.90
            else:
                scenario.correct_signal = CorrectSignal.BEAR
                scenario.correct_confidence_min = 0.45
                scenario.correct_confidence_max = 0.70

        elif outcome == StockOutcome.MIXED:
            scenario.correct_signal = CorrectSignal.NEUTRAL
            scenario.correct_confidence_min = 0.30
            scenario.correct_confidence_max = 0.50

        else:  # FLAT
            scenario.correct_signal = CorrectSignal.NEUTRAL
            scenario.correct_confidence_min = 0.20
            scenario.correct_confidence_max = 0.50

        return scenario

    def _assign_confidence(self, scenario: LabeledScenario) -> LabeledScenario:
        """Assign LabelConfidence based on data quality."""
        has_price_data = scenario.open_price is not None and scenario.close_price is not None
        has_headlines = bool(scenario.premarket_headlines)
        has_trade = scenario.was_traded and scenario.trade_pnl is not None
        has_rvol = scenario.rvol is not None

        score = sum([has_price_data, has_headlines, has_trade, has_rvol])

        if score >= 3:
            scenario.label_confidence = LabelConfidence.AUTO_HIGH
        elif score == 2:
            scenario.label_confidence = LabelConfidence.AUTO_MEDIUM
        elif score == 1:
            scenario.label_confidence = LabelConfidence.AUTO_LOW
        else:
            scenario.label_confidence = LabelConfidence.UNLABELED

        return scenario

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    def run_full_pipeline(self) -> list[LabeledScenario]:
        """Run the complete auto-labeling pipeline across all data sources.

        Order:
          1. Load base scenarios from journals (agent signals, decisions)
          2. Enrich with trade P&L from trade_results.jsonl
          3. Enrich with price outcomes from universe / gap_scenarios files
          4. Session reports (no-op currently)
          5. Auto-classify catalyst, outcome, correct signal
          6. Assign confidence
        """
        print("[AutoLabeler] Step 1: Loading journal entries...")
        scenarios = self.label_from_journals()
        print(f"[AutoLabeler]   -> {len(scenarios)} scenarios from journals")

        print("[AutoLabeler] Step 2: Enriching with trade results...")
        scenarios = self.label_from_trade_results(scenarios)
        print(f"[AutoLabeler]   -> {len(scenarios)} scenarios after trade results")

        print("[AutoLabeler] Step 3: Enriching with universe/gap scenario files...")
        scenarios = self.label_from_universe_files(scenarios)
        print(f"[AutoLabeler]   -> {len(scenarios)} scenarios after universe files")

        print("[AutoLabeler] Step 4: Session reports (aggregate only)...")
        scenarios = self.label_from_session_reports(scenarios)

        print("[AutoLabeler] Step 5: Auto-classifying catalyst, outcome, correct signal...")
        for i, s in enumerate(scenarios):
            scenarios[i] = self.auto_classify_catalyst(s)
            scenarios[i] = self.auto_classify_outcome(scenarios[i])
            scenarios[i] = self.auto_set_correct_signal(scenarios[i])
            scenarios[i] = self._assign_confidence(scenarios[i])

        print(f"[AutoLabeler] Done. Total labeled scenarios: {len(scenarios)}")
        return scenarios
