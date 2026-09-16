"""Data models for the LLM Performance Arena labeled dataset."""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional
import json


class CatalystType(str, Enum):
    PHARMA_DEAL = "pharma_deal"
    EARNINGS = "earnings"
    CONTRACT = "contract"
    FDA = "fda"
    MERGER = "merger"
    SEC_FILING = "sec_filing"
    PROMOTIONAL = "promotional"
    SQUEEZE = "squeeze"
    TECHNICAL_BREAKOUT = "technical_breakout"
    UNKNOWN = "unknown"


class StockOutcome(str, Enum):
    RUNNER = "runner"    # Sustained +20% from open
    FADER = "fader"      # Reversed to stop-loss
    MIXED = "mixed"      # Partial run then fade
    FLAT = "flat"        # Minimal movement


class CorrectSignal(str, Enum):
    STRONG_BULL = "strong_bull"
    BULL = "bull"
    NEUTRAL = "neutral"
    BEAR = "bear"
    STRONG_BEAR = "strong_bear"


class LabelConfidence(str, Enum):
    VERIFIED = "verified"          # Human-verified catalyst and outcome
    AUTO_HIGH = "auto_high"        # Auto-labeled with high confidence (clear outcome)
    AUTO_MEDIUM = "auto_medium"    # Auto-labeled, moderate confidence
    AUTO_LOW = "auto_low"          # Auto-labeled, low confidence (needs review)
    UNLABELED = "unlabeled"        # Outcome known but catalyst not classified


@dataclass
class LabeledScenario:
    """A single stock-day scenario with ground truth labels."""

    # Identity
    ticker: str
    date: date
    scenario_id: str  # "{ticker}_{date}" unique key

    # Premarket data (agent inputs)
    gap_pct: Optional[float] = None
    rvol: Optional[float] = None
    dollar_volume: Optional[float] = None
    open_price: Optional[float] = None
    prev_close: Optional[float] = None
    premarket_headlines: list = field(default_factory=list)
    sec_filings: list = field(default_factory=list)  # Filing types found

    # Ground truth labels
    catalyst_type: CatalystType = CatalystType.UNKNOWN
    catalyst_text: str = ""  # The actual catalyst description
    catalyst_verified: bool = False
    outcome: StockOutcome = StockOutcome.FLAT

    # Price outcome data
    max_gain_pct: Optional[float] = None      # Max gain from open
    max_drawdown_pct: Optional[float] = None  # Max drawdown from open
    close_pct: Optional[float] = None         # Close vs open
    high_price: Optional[float] = None
    low_price: Optional[float] = None
    close_price: Optional[float] = None

    # What the correct agent response should have been
    correct_signal: CorrectSignal = CorrectSignal.NEUTRAL
    correct_confidence_min: float = 0.0
    correct_confidence_max: float = 1.0

    # What the system actually did
    was_traded: bool = False
    trade_direction: Optional[str] = None     # "long", "short", or None
    trade_pnl: Optional[float] = None
    actual_agent_signals: dict = field(default_factory=dict)  # Raw agent outputs
    actual_mfcs: Optional[float] = None
    actual_faller_score: Optional[float] = None

    # Metadata
    label_confidence: LabelConfidence = LabelConfidence.UNLABELED
    label_source: str = ""  # "auto_journal", "auto_trade_result", "manual", etc.
    notes: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        d = {
            "ticker": self.ticker,
            "date": self.date.isoformat() if isinstance(self.date, date) else self.date,
            "scenario_id": self.scenario_id,
            "gap_pct": self.gap_pct,
            "rvol": self.rvol,
            "dollar_volume": self.dollar_volume,
            "open_price": self.open_price,
            "prev_close": self.prev_close,
            "premarket_headlines": self.premarket_headlines,
            "sec_filings": self.sec_filings,
            "catalyst_type": self.catalyst_type.value if isinstance(self.catalyst_type, CatalystType) else self.catalyst_type,
            "catalyst_text": self.catalyst_text,
            "catalyst_verified": self.catalyst_verified,
            "outcome": self.outcome.value if isinstance(self.outcome, StockOutcome) else self.outcome,
            "max_gain_pct": self.max_gain_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "close_pct": self.close_pct,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "close_price": self.close_price,
            "correct_signal": self.correct_signal.value if isinstance(self.correct_signal, CorrectSignal) else self.correct_signal,
            "correct_confidence_min": self.correct_confidence_min,
            "correct_confidence_max": self.correct_confidence_max,
            "was_traded": self.was_traded,
            "trade_direction": self.trade_direction,
            "trade_pnl": self.trade_pnl,
            "actual_agent_signals": self.actual_agent_signals,
            "actual_mfcs": self.actual_mfcs,
            "actual_faller_score": self.actual_faller_score,
            "label_confidence": self.label_confidence.value if isinstance(self.label_confidence, LabelConfidence) else self.label_confidence,
            "label_source": self.label_source,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "LabeledScenario":
        """Deserialize from a dict (as produced by to_dict)."""
        raw_date = d["date"]
        if isinstance(raw_date, str):
            parsed_date = date.fromisoformat(raw_date)
        else:
            parsed_date = raw_date

        created_at = None
        if d.get("created_at"):
            created_at = datetime.fromisoformat(d["created_at"])

        updated_at = None
        if d.get("updated_at"):
            updated_at = datetime.fromisoformat(d["updated_at"])

        return cls(
            ticker=d["ticker"],
            date=parsed_date,
            scenario_id=d["scenario_id"],
            gap_pct=d.get("gap_pct"),
            rvol=d.get("rvol"),
            dollar_volume=d.get("dollar_volume"),
            open_price=d.get("open_price"),
            prev_close=d.get("prev_close"),
            premarket_headlines=d.get("premarket_headlines", []),
            sec_filings=d.get("sec_filings", []),
            catalyst_type=CatalystType(d.get("catalyst_type", "unknown")),
            catalyst_text=d.get("catalyst_text", ""),
            catalyst_verified=d.get("catalyst_verified", False),
            outcome=StockOutcome(d.get("outcome", "flat")),
            max_gain_pct=d.get("max_gain_pct"),
            max_drawdown_pct=d.get("max_drawdown_pct"),
            close_pct=d.get("close_pct"),
            high_price=d.get("high_price"),
            low_price=d.get("low_price"),
            close_price=d.get("close_price"),
            correct_signal=CorrectSignal(d.get("correct_signal", "neutral")),
            correct_confidence_min=d.get("correct_confidence_min", 0.0),
            correct_confidence_max=d.get("correct_confidence_max", 1.0),
            was_traded=d.get("was_traded", False),
            trade_direction=d.get("trade_direction"),
            trade_pnl=d.get("trade_pnl"),
            actual_agent_signals=d.get("actual_agent_signals", {}),
            actual_mfcs=d.get("actual_mfcs"),
            actual_faller_score=d.get("actual_faller_score"),
            label_confidence=LabelConfidence(d.get("label_confidence", "unlabeled")),
            label_source=d.get("label_source", ""),
            notes=d.get("notes", ""),
            created_at=created_at,
            updated_at=updated_at,
        )
