"""doc 272 C2 — reference experiment: per-ticker ledger-vs-journal realized drift.

Rebuilds the ledger-side realized P&L for ONE session date by folding the
day's websocket fill events (kind="fill") through the doc-222 avg-entry pure
fold — the same shape as scripts/ledger_shadow_recon.py, reimplemented
minimally here (no import of the script) — and compares it per ticker against
the journal's realized_pnl rows (kind="journal", deduped last-write-wins per
trade_id, mirroring recon's journal_realized).

Single-day caveat (documented, intentional): the fold starts flat, so sells of
positions carried in from a prior session book 0 via the `min(qty, pos)`
orphan-sell clamp — exactly the clamp recon uses, minus its cross-day
fold-through. Drift here is therefore an intraday-round-trip signal, not the
full three-way recon.

metrics: {"drift_total", "worst_ticker", "n_fills", "per_ticker_drift"}.
Gates: promote_gate is 'informational' (this experiment never promotes);
kill_gate None (never auto-retires).
"""

from __future__ import annotations

from src.research.event_bus import Event
from src.research.experiment import Experiment, register


@register
class LedgerJournalDrift(Experiment):
    name = "ledger_journal_drift"
    consumes = {"fill", "journal"}

    def __init__(self) -> None:
        self._pos: dict[str, float] = {}
        self._avg: dict[str, float] = {}
        self._realized: dict[str, float] = {}
        self._seen_execution_ids: set[str] = set()
        self._n_fills = 0
        self._journal: dict[str, tuple[str, float]] = {}  # trade_id -> (ticker, pnl)

    # ── accumulate ──────────────────────────────────────────────────────────
    def on_event(self, e: Event) -> None:
        if e.kind == "fill":
            self._on_fill(e)
        elif e.kind == "journal":
            self._on_journal(e)

    def _on_fill(self, e: Event) -> None:
        xid = e.execution_id
        if not xid or xid in self._seen_execution_ids:
            return  # ledger semantics: dedupe on execution_id; no-xid rows don't book
        side = e.payload.get("side")
        qty = e.payload.get("qty")
        price = e.payload.get("price")
        if not e.ticker or side not in ("buy", "sell") or not qty or not price:
            return
        qty, price = float(qty), float(price)
        if qty <= 0 or price <= 0:
            return
        self._seen_execution_ids.add(xid)
        self._n_fills += 1
        sym = e.ticker
        pos0, avg0 = self._pos.get(sym, 0.0), self._avg.get(sym, 0.0)
        if side == "buy":
            self._pos[sym] = pos0 + qty
            self._avg[sym] = (avg0 * pos0 + price * qty) / (pos0 + qty) if (pos0 + qty) > 0 else price
        else:
            booked = min(qty, pos0)  # orphan sells (pre-session position) book 0
            self._realized[sym] = self._realized.get(sym, 0.0) + (price - avg0) * booked
            self._pos[sym] = pos0 - booked

    def _on_journal(self, e: Event) -> None:
        trade_id = e.payload.get("trade_id")
        pnl = e.payload.get("realized_pnl")
        if not trade_id or pnl is None or not e.ticker:
            return
        try:
            self._journal[str(trade_id)] = (e.ticker, float(pnl))
        except (TypeError, ValueError):
            return

    # ── emit ────────────────────────────────────────────────────────────────
    def metrics(self) -> dict:
        journal_per: dict[str, float] = {}
        for ticker, pnl in self._journal.values():
            journal_per[ticker] = journal_per.get(ticker, 0.0) + pnl
        deltas = {
            sym: round(self._realized.get(sym, 0.0) - journal_per.get(sym, 0.0), 2)
            for sym in set(self._realized) | set(journal_per)
        }
        worst = max(deltas, key=lambda s: abs(deltas[s])) if deltas else None
        return {
            "drift_total": round(sum(deltas.values()), 2),
            "worst_ticker": worst,
            "n_fills": self._n_fills,
            "per_ticker_drift": deltas,
        }

    def promote_gate(self) -> str | None:
        return "informational"

    def kill_gate(self) -> str | None:
        return None
